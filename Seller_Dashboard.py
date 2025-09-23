import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import io

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ─────────────────────────────────────────────
# 1) Konfiguracja aplikacji
# ─────────────────────────────────────────────
st.set_page_config(page_title="Sprzedaż: WoW TOP 10", layout="wide")
st.title("📊 Sprzedaż — Tydzień do tygodnia (TOP N SKU)")

METABASE_URL = "https://metabase.emamas.ideaerp.pl"
METABASE_DATABASE_ID = int(st.secrets.get("metabase_database_id", 2))
METABASE_USER = st.secrets["metabase_user"]
METABASE_PASSWORD = st.secrets["metabase_password"]
TZ = ZoneInfo("Europe/Warsaw")

# ─────────────────────────────────────────────
# 2) Wspólna baza SQL (parametry platformy i waluty będą podstawiane)
# ─────────────────────────────────────────────
SQL_TEMPLATE = """
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
             l.price_unit * COALESCE(l.product_uom_qty,0), 0) AS line_total,
    COALESCE(s.confirm_date, s.date_order, s.create_date) AS order_ts
  FROM sale_order_line l
  JOIN sale_order s           ON s.id = l.order_id
  JOIN res_currency cur       ON cur.id = l.currency_id
  LEFT JOIN product_product  pp ON pp.id = l.product_id
  LEFT JOIN product_template pt ON pt.id = pp.product_tmpl_id
  WHERE s.state IN ('sale','done')
    AND cur.name = '{currency}'
    AND s.name ILIKE '%{platform}%'
),
w AS (
  SELECT p.week_start, p.week_end, p.prev_start, p.prev_end FROM params p
),
curr AS (
  SELECT
    l.sku,
    MAX(l.product_name) AS product_name,
    SUM(l.line_total) AS curr_rev,
    SUM(l.qty)        AS curr_qty
  FROM lines l CROSS JOIN w
  WHERE (l.order_ts AT TIME ZONE 'Europe/Warsaw') >= w.week_start
    AND (l.order_ts AT TIME ZONE 'Europe/Warsaw') <  w.week_end
  GROUP BY l.sku
),
prev AS (
  SELECT
    l.sku,
    SUM(l.line_total) AS prev_rev,
    SUM(l.qty)        AS prev_qty
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

# ─────────────────────────────────────────────
# 3) Funkcje pomocnicze (Metabase API)
# ─────────────────────────────────────────────
@st.cache_data(ttl=50 * 60)
def get_metabase_session() -> str | None:
    try:
        r = requests.post(
            f"{METABASE_URL}/api/session",
            json={"username": METABASE_USER, "password": METABASE_PASSWORD},
            timeout=20
        )
        r.raise_for_status()
        return r.json()["id"]
    except Exception as e:
        st.error(f"❌ Błąd logowania do Metabase: {e}")
        return None


def _dataset_call(sql_text: str, params: dict, session: str) -> dict:
    payload = {
        "database": METABASE_DATABASE_ID,
        "type": "native",
        "native": {
            "query": sql_text,
            "template-tags": {k: {"name": k, "type": "date"} for k in params.keys()},
        },
        "parameters": [
            {"type": "date", "target": ["variable", ["template-tag", k]], "value": v}
            for k, v in params.items()
        ],
    }
    headers = {"X-Metabase-Session": session}
    r = requests.post(f"{METABASE_URL}/api/dataset", headers=headers, json=payload, timeout=120)
    if r.status_code == 200:
        return {"status": 200, "json": r.json(), "text": r.text}
    return {"status": r.status_code, "json": None, "text": r.text}


def _metabase_json_to_df(j: dict) -> pd.DataFrame:
    if not j or "data" not in j: return pd.DataFrame()
    rows = j["data"].get("rows", [])
    cols = j["data"].get("cols", [])
    if not rows: return pd.DataFrame()
    if cols:
        names = [c.get("name") for c in cols]
        return pd.DataFrame(rows, columns=names)
    return pd.DataFrame(rows)


@st.cache_data(ttl=600)
def query_data(sql_text: str, week_start_iso: str) -> pd.DataFrame:
    session = get_metabase_session()
    if not session: return pd.DataFrame()
    res = _dataset_call(sql_text, {"week_start": week_start_iso}, session)
    if res["status"] != 200 or not res["json"]: return pd.DataFrame()
    df = _metabase_json_to_df(res["json"])
    df.columns = [c.lower() for c in df.columns]
    for col in ["curr_rev","prev_rev","rev_change_pct","curr_qty","prev_qty","qty_change_pct"]:
        if col in df: df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

# ─────────────────────────────────────────────
# 4) Logika dashboardu (wspólna dla Allegro/eBay)
# ─────────────────────────────────────────────
def last_completed_week_start(today: date | None = None) -> date:
    d = today or datetime.now(TZ).date()
    offset = d.weekday() + 7
    return d - timedelta(days=offset)


def classify_change_icon(pct: float, threshold: float) -> str:
    if pd.isna(pct): return "➖"
    if pct >= threshold: return "🟢⬆️"
    if pct <= -threshold: return "🔴⬇️"
    return "⚪"


def render_dashboard(platform: str, currency: str):
    st.sidebar.header(f"⚙️ Filtry ({platform})")

    default_week = last_completed_week_start()
    pick_day = st.sidebar.date_input("Wybierz tydzień", value=default_week, key=f"date_{platform}")
    week_start = pick_day - timedelta(days=pick_day.weekday())
    week_end = week_start + timedelta(days=7)

    thr_rev = st.sidebar.slider("Próg alertu sprzedaży %", 5, 80, 20, step=5, key=f"thr_rev_{platform}")
    thr_qty = st.sidebar.slider("Próg alertu ilości %", 5, 80, 20, step=5, key=f"thr_qty_{platform}")
    top_n = st.sidebar.slider("Ile pozycji w TOP?", 5, 20, 10, step=5, key=f"topn_{platform}")
    weeks_back = st.sidebar.slider("Ile tygodni wstecz (trend)", 4, 12, 8, key=f"weeks_{platform}")

    st.caption(f"Tydzień: **{week_start} → {week_end - timedelta(days=1)}**  •  {platform} ({currency})")

    sql_text = SQL_TEMPLATE.format(platform=platform, currency=currency)
    df = query_data(sql_text, week_start.isoformat())
    if df.empty:
        st.warning("Brak danych dla wybranego tygodnia.")
        return

    # Statusy
    df["status_rev"] = df["rev_change_pct"].apply(lambda x: classify_change_icon(x, thr_rev))
    df["status_qty"] = df["qty_change_pct"].apply(lambda x: classify_change_icon(x, thr_qty))

    df_top = df.sort_values("curr_rev", ascending=False).head(top_n)

    # KPI
    sum_curr, sum_prev = df["curr_rev"].sum(), df["prev_rev"].sum()
    delta_abs, delta_pct = sum_curr - sum_prev, (sum_curr - sum_prev)/sum_prev*100 if sum_prev else 0

    c1,c2,c3 = st.columns(3)
    c1.metric("Suma sprzedaży", f"{sum_curr:,.0f} {currency}".replace(",", " "))
    c2.metric("Zmiana", f"{delta_abs:,.0f} {currency}".replace(",", " "))
    c3.metric("Zmiana %", f"{delta_pct:+.0f}%")

    # Bar chart TOPN
    st.subheader(f"TOP {top_n} — Sprzedaż tygodnia ({currency})")
    fig = px.bar(df_top, x="curr_rev", y="sku", color="status_rev",
                 orientation="h", height=500,
                 labels={"curr_rev":"Sprzedaż tygodnia","sku":"SKU","status_rev":"Status"})
    fig.update_layout(yaxis={"categoryorder":"total ascending"})
    st.plotly_chart(fig, use_container_width=True)

    # Waterfall
    st.subheader("Wkład TOP produktów w zmianę sprzedaży (waterfall)")
    df_delta = df_top.assign(delta=df_top["curr_rev"] - df_top["prev_rev"]).sort_values("delta", ascending=False)
    fig_wf = go.Figure(go.Waterfall(x=df_delta["sku"], y=df_delta["delta"], text=df_delta["product_name"], textposition="auto"))
    fig_wf.update_traces(increasing={"marker":{"color":"green"}},
                         decreasing={"marker":{"color":"red"}},
                         totals={"marker":{"color":"blue"}})
    st.plotly_chart(fig_wf, use_container_width=True)

    # Trend
    st.subheader(f"Trend sprzedaży — ostatnie {weeks_back} tygodni")
    dfs = []
    for i in range(weeks_back):
        ws = (week_start - timedelta(weeks=i)).isoformat()
        df_i = query_data(sql_text, ws)
        if not df_i.empty:
            df_i["week_start"] = pd.to_datetime(ws)
            dfs.append(df_i)
    if dfs:
        df_trend = pd.concat(dfs)
        all_skus = sorted(df_trend["sku"].dropna().unique())
        search_term = st.text_input("Szukaj SKU/produktu", key=f"search_{platform}")
        filtered = [sku for sku in all_skus if search_term.lower() in str(sku).lower()] if search_term else all_skus
        pick_skus = st.multiselect("Wybierz SKU", options=filtered, default=filtered[:5], key=f"ms_{platform}")
        chart_type = st.radio("Typ wykresu", ["area","line"], horizontal=True, key=f"chart_{platform}")
        if pick_skus:
            df_plot = df_trend[df_trend["sku"].isin(pick_skus)].groupby(["week_start","sku"])["curr_rev"].sum().reset_index()
            fig_tr = go.Figure()
            for sku in df_plot["sku"].unique():
                sub = df_plot[df_plot["sku"]==sku]
                if chart_type=="area":
                    fig_tr.add_trace(go.Scatter(x=sub["week_start"], y=sub["curr_rev"], mode="lines", stackgroup="one", name=sku))
                else:
                    fig_tr.add_trace(go.Scatter(x=sub["week_start"], y=sub["curr_rev"], mode="lines+markers", name=sku))
            st.plotly_chart(fig_tr, use_container_width=True)

    # Export
    st.download_button("📥 Pobierz CSV", df.to_csv(index=False).encode("utf-8"),
                       file_name=f"{platform.lower()}_{week_start}.csv", mime="text/csv")
    excel_buf = io.BytesIO()
    with pd.ExcelWriter(excel_buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    st.download_button("📥 Pobierz Excel", excel_buf.getvalue(),
                       file_name=f"{platform.lower()}_{week_start}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ─────────────────────────────────────────────
# 5) Zakładki: Allegro + eBay.de
# ─────────────────────────────────────────────
tab1, tab2 = st.tabs(["🇵🇱 Allegro (PLN)", "🇩🇪 eBay.de (EUR)"])

with tab1:
    render_dashboard("Allegro", "PLN")

with tab2:
    render_dashboard("eBay", "EUR")
