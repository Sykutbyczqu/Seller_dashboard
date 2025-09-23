import streamlit as st
import pandas as pd
import plotly.express as px
import requests
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────────────────────
# Konfiguracja aplikacji
# ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="E-commerce Dashboard", layout="wide")
st.title("📊 Dashboard magazyn & sprzedaż")

METABASE_URL = "https://metabase.emamas.ideaerp.pl"
METABASE_DATABASE_ID = int(st.secrets.get("metabase_database_id", 2))
METABASE_USER = st.secrets["metabase_user"]
METABASE_PASSWORD = st.secrets["metabase_password"]
TZ = ZoneInfo("Europe/Warsaw")

# ─────────────────────────────────────────────────────────────
# Logowanie do Metabase (cache ~50 min)
# ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=50 * 60)
def get_metabase_session() -> str | None:
    try:
        payload = {"username": METABASE_USER, "password": METABASE_PASSWORD}
        r = requests.post(f"{METABASE_URL}/api/session", json=payload, timeout=15)
        r.raise_for_status()
        return r.json()["id"]
    except Exception as e:
        st.error(f"❌ Błąd logowania do Metabase: {e}")
        return None

session_id = get_metabase_session()
headers = {"X-Metabase-Session": session_id} if session_id else {}

# ─────────────────────────────────────────────────────────────
# Sidebar: wybór modułu
# ─────────────────────────────────────────────────────────────
st.sidebar.header("🧭 Widok")
view = st.sidebar.radio("Wybierz raport:", ["Pakowanie — dzień", "Sprzedaż — WoW TOP 10 SKU"])

# ─────────────────────────────────────────────────────────────
# Pakowanie — DZIEŃ (00:00–24:00)
# ─────────────────────────────────────────────────────────────
SQL_DAY_0000_24 = """
SELECT 
    p.name AS packing_user_login,
    COUNT(s.name) AS paczki_pracownika
FROM sale_order s
JOIN res_users   u ON s.packing_user = u.id
JOIN res_partner p ON u.partner_id   = p.id
WHERE s.packing_user IS NOT NULL
  AND s.packing_date >= ({{day}}::date)
  AND s.packing_date <  ({{day}}::date + INTERVAL '1 day')
GROUP BY p.name
ORDER BY paczki_pracownika DESC;
"""

@st.cache_data(ttl=600)
def query_packing_data_for_day(day_iso: str) -> pd.DataFrame:
    if not session_id:
        return pd.DataFrame()
    payload = {
        "database": METABASE_DATABASE_ID,
        "type": "native",
        "native": {
            "query": SQL_DAY_0000_24,
            "template-tags": {"day": {"name": "day", "display-name": "day", "type": "date"}},
        },
        "parameters": [{"type": "date", "target": ["variable", ["template-tag", "day"]], "value": day_iso}],
    }
    try:
        r = requests.post(f"{METABASE_URL}/api/dataset", headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        j = r.json()
        if isinstance(j, dict) and "data" in j and "rows" in j["data"]:
            cols = [c.get("name") for c in j["data"].get("cols", [])]
            rows = j["data"]["rows"]
            df = pd.DataFrame([[row[i] for i in range(len(cols))] for row in rows], columns=cols)
        else:
            df = pd.DataFrame(j)
        if not df.empty and "paczki_pracownika" in df.columns:
            df["paczki_pracownika"] = pd.to_numeric(df["paczki_pracownika"], errors="coerce").fillna(0).astype(int)
        return df
    except requests.HTTPError as err:
        body = getattr(err, "response", None)
        body_txt = (body.text[:300] if body is not None and hasattr(body, "text") else "")
        st.error(f"❌ Błąd HTTP Metabase: {err} | {body_txt}")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"❌ Wystąpił błąd podczas pobierania danych: {e}")
        return pd.DataFrame()

def render_packing_day():
    st.sidebar.header("🔎 Filtry (pakowanie)")
    selected_day = st.sidebar.date_input("Dzień (00:00–24:00)", value=date.today() - timedelta(days=1))
    df = query_packing_data_for_day(selected_day.isoformat())

    st.header("📦 Raport pakowania (00:00–24:00)")
    start_ts = datetime.combine(selected_day, time(0, 0), tzinfo=TZ)
    end_ts   = datetime.combine(selected_day + timedelta(days=1), time(0, 0), tzinfo=TZ)
    st.caption(f"Zakres: **{start_ts:%Y-%m-%d %H:%M}** → **{end_ts:%Y-%m-%d %H:%M}** (Europe/Warsaw)")

    if df.empty:
        st.warning("Brak danych w wybranym dniu. Zmień datę.")
        return

    total_packages = int(df["paczki_pracownika"].sum())
    avg_packages_per_user = float(df["paczki_pracownika"].mean()) if len(df) else 0.0
    idx = df["paczki_pracownika"].idxmax()
    top_packer = str(df.loc[idx, "packing_user_login"])
    top_value = int(df.loc[idx, "paczki_pracownika"])

    c1, c2, c3 = st.columns(3)
    c1.metric("📦 Łączna liczba paczek", f"{total_packages:,}".replace(",", " "))
    c2.metric("🧑‍💼 Średnia na pracownika", f"{avg_packages_per_user:,.0f}".replace(",", " "))
    c3.metric("🏆 Najlepszy pakowacz", f"{top_packer} ({top_value})")

    st.subheader("📦 Ranking wydajności pakowania")
    df_sorted = df.sort_values(by="paczki_pracownika", ascending=True).copy()
    df_sorted["próg 300"] = df_sorted["paczki_pracownika"].apply(lambda x: "≥ 300" if x >= 300 else "< 300")

    fig = px.bar(
        df_sorted,
        x="paczki_pracownika", y="packing_user_login",
        color="próg 300",
        color_discrete_map={"≥ 300": "firebrick", "< 300": "cornflowerblue"},
        title="Liczba paczek spakowanych przez pracownika",
        labels={"packing_user_login": "Pracownik", "paczki_pracownika": "Liczba paczek"},
        orientation="h", height=600
    )
    fig.add_vline(x=300, line_width=2, line_dash="dash", line_color="darkgray")
    fig.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("Podgląd danych"):
        st.dataframe(
            df_sorted.rename(columns={"packing_user_login": "Pracownik", "paczki_pracownika": "Spakowane paczki"}),
            use_container_width=True
        )

# ─────────────────────────────────────────────────────────────
# Sprzedaż — WoW TOP 10 SKU (+ alerty %)
# ─────────────────────────────────────────────────────────────
SQL_WOW_TOP10 = r"""
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
    COALESCE(pp.default_code, l.product_id::text) AS sku,           -- SKU
    COALESCE(pt.name, l.name) AS product_name,                       -- Nazwa
    COALESCE(l.product_uom_qty, 0) AS qty,                           -- Ilość
    COALESCE(l.price_total, l.price_subtotal,
             l.price_unit * COALESCE(l.product_uom_qty,0), 0) AS line_total,  -- Wartość linii
    COALESCE(s.confirm_date, s.date_order, s.create_date) AS order_ts         -- Czas zamówienia
  FROM sale_order_line l
  JOIN sale_order s        ON s.id = l.order_id
  LEFT JOIN product_product  pp ON pp.id = l.product_id
  LEFT JOIN product_template pt ON pt.id = pp.product_tmpl_id
  WHERE s.state IN ('sale','done')
),
w AS (
  SELECT p.week_start, p.week_end, p.prev_start, p.prev_end FROM params p
),
curr AS (
  SELECT
    l.sku,
    MAX(l.product_name) AS product_name,
    SUM(COALESCE(l.line_total,0)) AS curr_rev,
    SUM(COALESCE(l.qty,0))        AS curr_qty
  FROM lines l CROSS JOIN w
  WHERE (l.order_ts AT TIME ZONE 'Europe/Warsaw') >= w.week_start
    AND (l.order_ts AT TIME ZONE 'Europe/Warsaw') <  w.week_end
  GROUP BY l.sku
),
prev AS (
  SELECT
    l.sku,
    SUM(COALESCE(l.line_total,0)) AS prev_rev,
    SUM(COALESCE(l.qty,0))        AS prev_qty
  FROM lines l CROSS JOIN w
  WHERE (l.order_ts AT TIME ZONE 'Europe/Warsaw') >= w.prev_start
    AND (l.order_ts AT TIME ZONE 'Europe/Warsaw') <  w.prev_end
  GROUP BY l.sku
)
SELECT
  c.sku,
  c.product_name,
  c.curr_rev,
  c.curr_qty,
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
LIMIT 100;
"""

@st.cache_data(ttl=600)
def query_wow_top10(week_start_iso: str) -> pd.DataFrame:
    if not session_id:
        return pd.DataFrame()
    payload = {
        "database": METABASE_DATABASE_ID,
        "type": "native",
        "native": {
            "query": SQL_WOW_TOP10,
            "template-tags": {"week_start": {"name": "week_start", "display-name": "week_start", "type": "date"}},
        },
        "parameters": [{"type": "date", "target": ["variable", ["template-tag", "week_start"]], "value": week_start_iso}],
    }
    try:
        r = requests.post(f"{METABASE_URL}/api/dataset", headers=headers, json=payload, timeout=120)
        r.raise_for_status()
        j = r.json()
        if isinstance(j, dict) and "data" in j and "rows" in j["data"]:
            cols = [c.get("name") for c in j["data"].get("cols", [])]
            rows = j["data"]["rows"]
            df = pd.DataFrame([[row[i] for i in range(len(cols))] for row in rows], columns=cols)
        else:
            df = pd.DataFrame(j)
        # typy liczbowe
        for col in ["curr_rev", "prev_rev", "rev_change_pct", "curr_qty", "prev_qty", "qty_change_pct"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except requests.HTTPError as err:
        body = getattr(err, "response", None)
        body_txt = (body.text[:300] if body is not None and hasattr(body, "text") else "")
        st.error(f"❌ Błąd HTTP Metabase: {err} | {body_txt}")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"❌ Błąd pobierania WoW: {e}")
        return pd.DataFrame()

def last_completed_week_start(today_pl: date | None = None) -> date:
    if today_pl is None:
        today_pl = datetime.now(TZ).date()
    monday_this = today_pl - timedelta(days=today_pl.weekday())
    return monday_this - timedelta(days=7)

def render_wow_top10():
    st.sidebar.header("🔎 Filtry (sprzedaż)")
    default_week = last_completed_week_start()
    pick_day = st.sidebar.date_input("Wybierz tydzień (dowolny dzień tego tygodnia)", value=default_week)
    # wyznacz poniedziałek tygodnia wybranego dnia
    week_start = pick_day - timedelta(days=pick_day.weekday())
    threshold = st.sidebar.slider("Próg alertu (±%)", min_value=5, max_value=80, value=20, step=5)

    week_end = week_start + timedelta(days=7)
    st.header("🛒 Sprzedaż — WoW TOP 10 SKU")
    st.caption(f"Tydzień: **{week_start} → {week_end - timedelta(days=1)}**  | Alert: ±{threshold}%  (Europe/Warsaw)")

    df = query_wow_top10(week_start.isoformat())
    if df.empty:
        st.warning("Brak danych dla wybranego tygodnia. Zmień tydzień lub sprawdź schemat SQL (tabela linii zamówień).")
        return

    # TOP 10 po bieżącym tygodniu (revenue)
    df_top = df.sort_values("curr_rev", ascending=False).head(10).copy()
    # kategorie do kolorowania (wzrost/spadek/neutral)
    def cat(p):
        if pd.isna(p): return "n/d"
        if p >= threshold: return f"↑ ≥{threshold}%"
        if p <= -threshold: return f"↓ ≤-{threshold}%"
        return "≈"

    df_top["status"] = df_top["rev_change_pct"].apply(cat)

    c1, c2, c3 = st.columns(3)
    c1.metric("Suma rev (tydzień)", f"{df['curr_rev'].sum():,.0f} zł".replace(",", " "))
    c2.metric("Zmiana vs poprzedni", f"{(df['curr_rev'].sum() - df['prev_rev'].sum()):,.0f} zł".replace(",", " "))
    try:
        pct_total = (df['curr_rev'].sum() - df['prev_rev'].sum()) / df['prev_rev'].sum() * 100 if df['prev_rev'].sum() else 0
    except ZeroDivisionError:
        pct_total = 0
    c3.metric("Δ% całości", f"{pct_total:+.0f}%")

    st.subheader("TOP 10 (wartość tygodnia)")
    fig = px.bar(
        df_top,
        x="curr_rev", y="sku",
        color="status",
        color_discrete_map={"↑ ≥20%": "#2ca02c", "↑ ≥25%": "#2ca02c", "↑ ≥30%": "#2ca02c",
                            "↓ ≤-20%": "#d62728", "↓ ≤-25%": "#d62728", "↓ ≤-30%": "#d62728",
                            "≈": "#1f77b4", "n/d": "#7f7f7f"},
        hover_data={"product_name": True, "prev_rev": ":,.0f", "rev_change_pct": ":.0f"},
        labels={"curr_rev": "Rev (tydzień)", "sku": "SKU"},
        orientation="h", height=600
    )
    fig.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, use_container_width=True)

    # Alerty
    ups = df_top[df_top["rev_change_pct"] >= threshold].copy()
    downs = df_top[df_top["rev_change_pct"] <= -threshold].copy()

    colA, colB = st.columns(2)
    with colA:
        st.markdown("### 🚀 Wzrosty")
        if ups.empty:
            st.info("Brak pozycji ≥ próg.")
        else:
            ups_view = ups[["sku", "product_name", "curr_rev", "prev_rev", "rev_change_pct", "curr_qty", "prev_qty"]]
            ups_view = ups_view.rename(columns={
                "sku": "SKU", "product_name": "Produkt",
                "curr_rev": "Rev (tydz.)", "prev_rev": "Rev (poprz.)",
                "rev_change_pct": "Δ Rev %", "curr_qty": "Qty (tydz.)", "prev_qty": "Qty (poprz.)"
            })
            st.dataframe(ups_view, use_container_width=True)

    with colB:
        st.markdown("### 📉 Spadki")
        if downs.empty:
            st.info("Brak pozycji ≤ -próg.")
        else:
            downs_view = downs[["sku", "product_name", "curr_rev", "prev_rev", "rev_change_pct", "curr_qty", "prev_qty"]]
            downs_view = downs_view.rename(columns={
                "sku": "SKU", "product_name": "Produkt",
                "curr_rev": "Rev (tydz.)", "prev_rev": "Rev (poprz.)",
                "rev_change_pct": "Δ Rev %", "curr_qty": "Qty (tydz.)", "prev_qty": "Qty (poprz.)"
            })
            st.dataframe(downs_view, use_container_width=True)

    with st.expander("Podgląd danych (TOP 10)"):
        st.dataframe(df_top, use_container_width=True)

# ─────────────────────────────────────────────────────────────
# Router widoków
# ─────────────────────────────────────────────────────────────
if view == "Pakowanie — dzień":
    render_packing_day()
else:
    render_wow_top10()
