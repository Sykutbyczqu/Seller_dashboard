# streamlit_app.py
import io
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

# ─────────────────────────────────────────────────────────────
# 1) Konfiguracja aplikacji
# ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="Sprzedaż: WoW TOP (PLN) — Rozszerzone", layout="wide")
st.title("🛒 Sprzedaż — Trendy i TOP N (PLN)")

# ─────────────────────────────────────────────────────────────
# 2) Ustawienia Metabase (przykładowe - w runtime użyj st.secrets)
# ─────────────────────────────────────────────────────────────
METABASE_URL = "https://metabase.emamas.ideaerp.pl"
METABASE_DATABASE_ID = int(st.secrets.get("metabase_database_id", 2))
METABASE_USER = st.secrets["metabase_user"]
METABASE_PASSWORD = st.secrets["metabase_password"]
TZ = ZoneInfo("Europe/Warsaw")

# ─────────────────────────────────────────────────────────────
# 3) SQL (ten sam co wcześniej)
# ─────────────────────────────────────────────────────────────
SQL_WOW_TOP10 = """
WITH params AS (
  SELECT
    {{week_start}}::date AS week_start,
    ({{week_start}}::date + INTERVAL '7 day') AS week_end,
    ({{week_start}}::date - INTERVAL '7 day') AS prev_start,
    {{week_start}}::date AS prev_end
),
lines AS (
  SELECT
    l.product_id,
    COALESCE(pp.default_code, l.product_id::text) AS sku,
    COALESCE(pt.name, l.name) AS product_name,
    COALESCE(l.product_uom_qty, 0) AS qty,
    COALESCE(l.price_total, l.price_subtotal,
             l.price_unit * COALESCE(l.product_uom_qty,0), 0) AS line_total_pln,
    COALESCE(s.confirm_date, s.date_order, s.create_date) AS order_ts
  FROM sale_order_line l
  JOIN sale_order s           ON s.id = l.order_id
  JOIN res_currency cur       ON cur.id = l.currency_id
  LEFT JOIN product_product  pp ON pp.id = l.product_id
  LEFT JOIN product_template pt ON pt.id = pp.product_tmpl_id
  WHERE s.state IN ('sale','done')
    AND cur.name = 'PLN'
    AND s.name ILIKE '%Allegro%'
),
w AS (
  SELECT p.week_start, p.week_end, p.prev_start, p.prev_end FROM params p
),
curr AS (
  SELECT
    l.sku,
    MAX(l.product_name) AS product_name,
    SUM(l.line_total_pln) AS curr_rev,
    SUM(l.qty)            AS curr_qty
  FROM lines l CROSS JOIN w
  WHERE (l.order_ts AT TIME ZONE 'Europe/Warsaw') >= w.week_start
    AND (l.order_ts AT TIME ZONE 'Europe/Warsaw') <  w.week_end
  GROUP BY l.sku
),
prev AS (
  SELECT
    l.sku,
    SUM(l.line_total_pln) AS prev_rev,
    SUM(l.qty)            AS prev_qty
  FROM lines l CROSS JOIN w
  WHERE (l.order_ts AT TIME ZONE 'Europe/Warsaw') >= w.prev_start
    AND (l.order_ts AT TIME ZONE 'Europe/Warsaw') <  w.prev_end
  GROUP BY l.sku
)
SELECT
  c.sku,
  c.product_name,
  COALESCE(c.curr_rev,0) AS curr_rev,
  COALESCE(c.curr_qty,0) AS curr_qty,
  COALESCE(p.prev_rev,0) AS prev_rev,
  COALESCE(p.prev_qty,0) AS prev_qty,
  CASE WHEN COALESCE(p.prev_rev,0)=0 AND COALESCE(c.curr_rev,0)>0 THEN NULL
       WHEN COALESCE(p.prev_rev,0)=0 THEN 0
       ELSE (c.curr_rev - p.prev_rev) / NULLIF(p.prev_rev,0)::numeric * 100.0 END AS rev_change_pct,
  CASE WHEN COALESCE(p.prev_qty,0)=0 AND COALESCE(c.curr_qty,0)>0 THEN NULL
       WHEN COALESCE(p.prev_qty,0)=0 THEN 0
       ELSE (c.curr_qty - p.prev_qty) / NULLIF(p.prev_qty,0)::numeric * 100.0 END AS qty_change_pct
FROM curr c
LEFT JOIN prev p ON p.sku = c.sku
ORDER BY c.curr_rev DESC
"""

# ─────────────────────────────────────────────────────────────
# 4) Metabase session (cache)
# ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=50 * 60)
def get_metabase_session() -> str | None:
    try:
        payload = {"username": METABASE_USER, "password": METABASE_PASSWORD}
        r = requests.post(f"{METABASE_URL}/api/session", json=payload, timeout=20)
        r.raise_for_status()
        return r.json()["id"]
    except Exception as e:
        st.error(f"❌ Błąd logowania do Metabase: {e}")
        return None

# ─────────────────────────────────────────────────────────────
# 5) /api/dataset caller (200/202/401 handling)
# ─────────────────────────────────────────────────────────────
def _dataset_call(sql_text: str, params: dict, session: str, poll_max_s: float = 12.0) -> dict:
    payload = {
        "database": METABASE_DATABASE_ID,
        "type": "native",
        "native": {
            "query": sql_text,
            "template-tags": {k: {"name": k, "display-name": k, "type": "date"} for k in params.keys()},
        },
        "parameters": [
            {"type": "date", "target": ["variable", ["template-tag", k]], "value": v}
            for k, v in params.items()
        ],
    }
    headers = {"X-Metabase-Session": session}
    r = requests.post(f"{METABASE_URL}/api/dataset", headers=headers, json=payload, timeout=120)

    if r.status_code == 401:
        return {"status": 401, "json": None, "text": r.text}

    if r.status_code == 200:
        return {"status": 200, "json": (r.json() if r.content else None), "text": r.text}

    if r.status_code == 202:
        j = r.json() if r.content else {}
        if isinstance(j, dict) and isinstance(j.get("data", {}).get("rows"), list):
            return {"status": 200, "json": j, "text": r.text}
        token = j.get("id") or j.get("data", {}).get("id")
        if token:
            deadline = time.time() + poll_max_s
            last = None
            while time.time() < deadline:
                rr = requests.get(f"{METABASE_URL}/api/dataset/{token}/json", headers=headers, timeout=60)
                if rr.status_code == 200 and rr.content:
                    return {"status": 200, "json": rr.json(), "text": rr.text}
                rr = requests.get(f"{METABASE_URL}/api/dataset/{token}", headers=headers, timeout=60)
                if rr.status_code == 200 and rr.content:
                    return {"status": 200, "json": rr.json(), "text": rr.text}
                last = rr
                time.sleep(0.5)
            return {"status": getattr(last, "status_code", 202), "json": None, "text": getattr(last, "text", "")}
        return {"status": 202, "json": None, "text": r.text}

    return {"status": r.status_code, "json": (r.json() if r.content else None), "text": r.text}

# ─────────────────────────────────────────────────────────────
# 6) Metabase JSON → DataFrame (robust)
# ─────────────────────────────────────────────────────────────
def _metabase_json_to_df(j: dict) -> pd.DataFrame:
    if not isinstance(j, (dict, list)):
        return pd.DataFrame()

    if isinstance(j, dict) and "data" in j and isinstance(j["data"], dict):
        data = j["data"]
        rows = data.get("rows", [])
        cols_meta = data.get("cols", [])
        if cols_meta:
            col_names = [(c.get("name") or c.get("display_name") or f"col_{i}") for i, c in enumerate(cols_meta)]
            if rows and isinstance(rows[0], dict):
                df = pd.DataFrame(rows)
            else:
                df = pd.DataFrame(rows, columns=col_names)
        else:
            if not rows:
                return pd.DataFrame()
            if isinstance(rows[0], dict):
                df = pd.DataFrame(rows)
            else:
                n = len(rows[0])
                expected = ["sku","product_name","curr_rev","curr_qty","prev_rev","prev_qty","rev_change_pct","qty_change_pct"]
                col_names = expected[:n] if n == 8 else [f"c{i}" for i in range(n)]
                df = pd.DataFrame(rows, columns=col_names)
        return df

    if isinstance(j, list):
        return pd.DataFrame(j)

    return pd.DataFrame()

# ─────────────────────────────────────────────────────────────
# 7) Public query function (caching, session refresh)
# ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=600)
def query_wow_top10(sql_text: str, week_start_iso: str) -> pd.DataFrame:
    session = get_metabase_session()
    if not session:
        return pd.DataFrame()

    res = _dataset_call(sql_text, {"week_start": week_start_iso}, session)
    if res["status"] == 401:
        get_metabase_session.clear()
        session = get_metabase_session()
        if not session:
            st.error("❌ Nie udało się odświeżyć sesji Metabase.")
            return pd.DataFrame()
        res = _dataset_call(sql_text, {"week_start": week_start_iso}, session)

    st.session_state["mb_last_status"] = res["status"]
    st.session_state["mb_last_json"] = res["json"]

    if res["status"] not in (200, 202) or not res["json"]:
        st.error(f"❌ Metabase HTTP {res['status']}: {str(res.get('text',''))[:300]}")
        return pd.DataFrame()

    df = _metabase_json_to_df(res["json"])
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    for col in ["curr_rev", "prev_rev", "rev_change_pct", "curr_qty", "prev_qty", "qty_change_pct"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

# ─────────────────────────────────────────────────────────────
# 8) UI: filtry, progi, trend, TOP N
# ─────────────────────────────────────────────────────────────
def last_completed_week_start(today: date | None = None) -> date:
    d = today or datetime.now(TZ).date()
    offset = d.weekday() + 7
    return d - timedelta(days=offset)

st.sidebar.header("🔎 Filtry")
default_week = last_completed_week_start()
pick_day = st.sidebar.date_input("Wybierz tydzień (podaj dowolny dzień z tego tygodnia)", value=default_week)
week_start = pick_day - timedelta(days=pick_day.weekday())
week_end = week_start + timedelta(days=7)

threshold_rev = st.sidebar.slider("Próg alertu — wartość sprzedaży (%)", min_value=5, max_value=200, value=20, step=5)
threshold_qty = st.sidebar.slider("Próg alertu — ilość (%)", min_value=5, max_value=200, value=20, step=5)

weeks_back = st.sidebar.slider("Ile tygodni wstecz (trend)", 4, 16, 8, step=1)
top_n = st.sidebar.slider("Ile pozycji w TOP?", 5, 20, 10, step=5)

debug_api = st.sidebar.checkbox("Debug API", value=False)

st.caption(f"Tydzień: **{week_start} → {week_end - timedelta(days=1)}**  •  Strefa: Europe/Warsaw")

# ─────────────────────────────────────────────────────────────
# 9) Pobranie snapshotu (główny tydzień)
# ─────────────────────────────────────────────────────────────
df = query_wow_top10(SQL_WOW_TOP10, week_start.isoformat())

if debug_api:
    st.write(f"Metabase HTTP: {st.session_state.get('mb_last_status')}")
    st.subheader("Raw JSON (Metabase)")
    st.json(st.session_state.get("mb_last_json"))

if df.empty:
    st.warning("Brak danych dla wybranego tygodnia (PLN). Zmień tydzień lub sprawdź źródło.")
    st.stop()

need = {"sku","product_name","curr_rev","prev_rev","curr_qty","prev_qty","rev_change_pct","qty_change_pct"}
missing = [c for c in need if c not in df.columns]
if missing:
    st.error(f"Brak kolumn w danych: {missing}")
    st.dataframe(df.head(), use_container_width=True)
    st.stop()

# Ranking TOP N
df_top = df.sort_values("curr_rev", ascending=False).head(top_n).copy()

# ─────────────────────────────────────────────────────────────
# 10) Pomocnicze: klasyfikacja i kolory
# ─────────────────────────────────────────────────────────────
def classify_change_symbol(pct: float | np.floating | None, threshold: float):
    if pd.isna(pct): return ("—","#9e9e9e")
    if pct >= threshold:
        if pct >= threshold * 4: return ("🟢⬆️⬆️","#2e7d32")
        if pct >= threshold * 2: return ("🟢⬆️","#388e3c")
        return ("🟢↑","#66bb6a")
    if pct <= -threshold:
        if pct <= -threshold * 4: return ("🔴⬇️⬇️","#b71c1c")
        if pct <= -threshold * 2: return ("🔴⬇️","#d32f2f")
        return ("🔴↓","#ef5350")
    return ("⚪≈","#9e9e9e")

df_top["status_rev"], df_top["color_rev"] = zip(*df_top["rev_change_pct"].apply(lambda x: classify_change_symbol(x, threshold_rev)))
df_top["status_qty"], df_top["color_qty"] = zip(*df_top["qty_change_pct"].apply(lambda x: classify_change_symbol(x, threshold_qty)))

# ─────────────────────────────────────────────────────────────
# 11) KPI sticky (CSS)
# ─────────────────────────────────────────────────────────────
sum_curr = float(df["curr_rev"].sum() or 0)
sum_prev = float(df["prev_rev"].sum() or 0)
delta_abs = sum_curr - sum_prev
delta_pct = (delta_abs / sum_prev * 100) if sum_prev else 0.0

st.markdown("""
    <style>
    .sticky-kpi {
      position: sticky;
      top: 70px;
      background-color: white;
      padding: 8px;
      z-index: 999;
      border-bottom: 1px solid rgba(0,0,0,0.06);
    }
    </style>
""", unsafe_allow_html=True)

st.markdown('<div class="sticky-kpi">', unsafe_allow_html=True)
c1, c2, c3 = st.columns(3)
c1.metric("Suma sprzedaży (PLN, tydzień)", f"{sum_curr:,.0f} zł".replace(",", " "))
c2.metric("Zmiana vs poprzedni (PLN)", f"{delta_abs:,.0f} zł".replace(",", " "))
c3.metric("Zmiana % całości", f"{delta_pct:+.0f}%")
st.markdown('</div>', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
# 12) Wykres TOPN — go.Bar (unikamy potencjalnych deprecacji PX)
# ─────────────────────────────────────────────────────────────
st.subheader(f"TOP {top_n} — Sprzedaż tygodnia (PLN)")

colors = df_top["color_rev"].tolist()
hover = df_top.apply(lambda r: f"{r.sku} — {r.product_name}<br>Sprzedaż: {r.curr_rev:,.0f} zł<br>Zmiana: {('n/d' if pd.isna(r.rev_change_pct) else f'{r.rev_change_pct:+.0f}%')}", axis=1)
fig = go.Figure(go.Bar(
    x=df_top["curr_rev"],
    y=df_top["sku"],
    orientation="h",
    marker=dict(color=colors),
    hoverinfo="text",
    hovertext=hover
))
fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=520, margin=dict(l=150))
st.plotly_chart(fig, use_container_width=True)

# ─────────────────────────────────────────────────────────────
# 13) Waterfall — bez deprecated kwargs w konstruktorze
# ─────────────────────────────────────────────────────────────
st.subheader("📊 Wkład TOP produktów w zmianę sprzedaży (waterfall)")
df_delta = df_top.copy()
df_delta["delta"] = df_delta["curr_rev"] - df_delta["prev_rev"]
df_delta = df_delta.sort_values("delta", ascending=False).reset_index(drop=True)

measures = ["relative"] * len(df_delta) + ["total"]
x = df_delta["sku"].tolist() + ["SUMA"]
y = df_delta["delta"].tolist() + [df_delta["delta"].sum()]

fig_wf = go.Figure(go.Waterfall(
    x=x,
    y=y,
    measure=measures,
    text=[f"{v:,.0f}" for v in y],
    textposition="outside"
))
# ustaw kolory przez update_traces (zalecane)
fig_wf.update_traces(
    increasing=dict(marker=dict(color="#66bb6a")),
    decreasing=dict(marker=dict(color="#ef5350")),
    totals=dict(marker=dict(color="#42a5f5"))
)
fig_wf.update_layout(title="Wkład produktów w zmianę sprzedaży (PLN)", showlegend=False)
st.plotly_chart(fig_wf, use_container_width=True, height=420)

# ─────────────────────────────────────────────────────────────
# 14) Trend tygodniowy — pobieramy wiele snapshotów i rysujemy go.Scatter
# ─────────────────────────────────────────────────────────────
st.subheader("📈 Trendy tygodniowe — wybierz SKU do analizy trendu")

@st.cache_data(ttl=600)
def query_trend_many_weeks(sql_text: str, week_start_date: date, weeks: int = 8) -> pd.DataFrame:
    frames = []
    for i in range(weeks):
        ws_date = week_start_date - timedelta(weeks=i)
        iso = ws_date.isoformat()
        df_i = query_wow_top10(sql_text, iso)
        if df_i is None or df_i.empty:
            continue
        df_i = df_i.copy()
        df_i["week_start"] = pd.to_datetime(ws_date)
        frames.append(df_i)
    if frames:
        all_df = pd.concat(frames, ignore_index=True)
        return all_df
    return pd.DataFrame()

df_trend = query_trend_many_weeks(SQL_WOW_TOP10, week_start, weeks=weeks_back)

if df_trend.empty:
    st.info("Brak danych trendu (dla wybranej liczby tygodni).")
else:
    all_skus = sorted(df_trend["sku"].dropna().unique().tolist())

    # 🔍 pole do filtrowania SKU/nazwy
    search_term = st.text_input("Szukaj SKU lub produktu", "")
    if search_term:
        filtered_skus = [sku for sku in all_skus if search_term.lower() in str(sku).lower()]
    else:
        filtered_skus = all_skus

    pick_skus = st.multiselect(
        "Wybierz SKU do analizy trendu",
        options=filtered_skus,
        default=filtered_skus[:5] if filtered_skus else []
    )

    chart_type = st.radio("Typ wykresu", ["area", "line"], index=0, horizontal=True)

    if pick_skus:
        df_plot = df_trend[df_trend["sku"].isin(pick_skus)].copy()
        df_plot = df_plot.groupby(["week_start","sku"])["curr_rev"].sum().reset_index()
        pv = df_plot.pivot(index="week_start", columns="sku", values="curr_rev").fillna(0).sort_index()

        fig_tr = go.Figure()
        for sku in pv.columns:
            y = pv[sku].values
            if chart_type == "area":
                fig_tr.add_trace(go.Scatter(x=pv.index, y=y, mode="lines", name=sku, stackgroup="one"))
            else:
                fig_tr.add_trace(go.Scatter(x=pv.index, y=y, mode="lines+markers", name=sku))
        fig_tr.update_layout(xaxis=dict(tickformat="%Y-%m-%d"), yaxis_title="Sprzedaż (PLN)", height=520)
        st.plotly_chart(fig_tr, use_container_width=True)

# ─────────────────────────────────────────────────────────────
# 15) Tabele wzrostów/spadków i podgląd TOP
# ─────────────────────────────────────────────────────────────
COLS_DISPLAY = {
    "sku": "SKU",
    "product_name": "Produkt",
    "curr_rev": "Sprzedaż tygodnia (PLN)",
    "prev_rev": "Sprzedaż poprzedniego tygodnia (PLN)",
    "rev_change_pct": "Zmiana sprzedaży %",
    "curr_qty": "Ilość tygodnia (szt.)",
    "prev_qty": "Ilość poprzedniego tygodnia (szt.)",
    "qty_change_pct": "Zmiana ilości %",
    "status_rev": "Status (wartość)",
    "status_qty": "Status (ilość)"
}

def to_display(df_in: pd.DataFrame) -> pd.DataFrame:
    out = df_in.rename(columns=COLS_DISPLAY)
    keep = [c for c in ["SKU","Produkt","Sprzedaż tygodnia (PLN)","Sprzedaż poprzedniego tygodnia (PLN)","Zmiana sprzedaży %","Ilość tygodnia (szt.)","Ilość poprzedniego tygodnia (szt.)","Zmiana ilości %","Status (wartość)","Status (ilość)"] if c in out.columns]
    return out[keep]

ups = df_top[df_top["rev_change_pct"] >= threshold_rev].copy()
downs = df_top[df_top["rev_change_pct"] <= -threshold_rev].copy()

colA, colB = st.columns(2)
with colA:
    st.markdown("### 🚀 Wzrosty (≥ próg)")
    if ups.empty:
        st.info("Brak pozycji przekraczających próg wzrostu.")
    else:
        st.dataframe(to_display(ups), use_container_width=True)
with colB:
    st.markdown("### 📉 Spadki (≤ -próg)")
    if downs.empty:
        st.info("Brak pozycji przekraczających próg spadku.")
    else:
        st.dataframe(to_display(downs), use_container_width=True)

with st.expander("🔎 Podgląd TOP (tabela)"):
    st.dataframe(to_display(df_top), use_container_width=True)

# ─────────────────────────────────────────────────────────────
# 16) Eksport: CSV / Excel / PDF
# ─────────────────────────────────────────────────────────────
st.subheader("📥 Eksport danych")
download_col1, download_col2, download_col3 = st.columns(3)

csv_bytes = df.to_csv(index=False).encode("utf-8")
download_col1.download_button("📥 Pobierz (CSV)", csv_bytes, "sprzedaz.csv", "text/csv")

def to_excel_bytes(dframe: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        dframe.to_excel(writer, index=False, sheet_name="sprzedaz")
    return output.getvalue()

excel_bytes = to_excel_bytes(df)
download_col2.download_button("📥 Pobierz (Excel)", excel_bytes, "sprzedaz.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

def df_to_pdf_bytes(dframe: pd.DataFrame, title: str = "Raport") -> bytes:
    buf = io.BytesIO()
    d = dframe.copy().head(200)
    with PdfPages(buf) as pdf:
        fig, ax = plt.subplots(figsize=(11.69, 8.27))
        ax.axis('off')
        ax.set_title(title, fontsize=14, loc='left')
        table = ax.table(cellText=d.values, colLabels=d.columns, loc='center', cellLoc='left')
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.scale(1, 1.2)
        pdf.savefig(fig, bbox_inches='tight')
        plt.close(fig)
    buf.seek(0)
    return buf.read()

pdf_bytes = df_to_pdf_bytes(to_display(df_top), title=f"TOP{top_n} - raport tygodniowy")
download_col3.download_button("📥 Pobierz (PDF) — TOP", pdf_bytes, "sprzedaz_top.pdf", "application/pdf")

# ─────────────────────────────────────────────────────────────
# 17) Panel QA / Debug
# ─────────────────────────────────────────────────────────────
with st.expander("🔧 Panel QA / Debug"):
    st.write("Metabase HTTP:", st.session_state.get("mb_last_status"))
    st.write("Liczba wierszy (snapshot):", len(df))
    st.write("Liczba SKU w snapshot:", df["sku"].nunique())
    st.write("SKU bez nazwy:", df[df["product_name"].isna()]["sku"].unique().tolist())
    if debug_api:
        st.subheader("Raw JSON (Metabase)")
        st.json(st.session_state.get("mb_last_json"))
