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
st.set_page_config(page_title="Sprzedaż: WoW TOP (Allegro.pl / eBay.de) — Rozszerzone", layout="wide")
st.title("🛒 Sprzedaż — Trendy i TOP N (Allegro.pl / eBay.de)")

# Globalny config dla Plotly (używamy WYŁĄCZNIE parametru `config=...` w st.plotly_chart)
PLOTLY_CONFIG = {
    "displaylogo": False,
    "modeBarButtonsToRemove": [
        "lasso2d", "select2d", "zoom2d", "zoomIn2d", "zoomOut2d",
        "autoScale2d", "resetScale2d", "toImage"
    ],
    "scrollZoom": False,
    "responsive": True,
}

# ─────────────────────────────────────────────────────────────
# 2) Ustawienia Metabase
# ─────────────────────────────────────────────────────────────
METABASE_URL = "https://metabase.emamas.ideaerp.pl"
METABASE_DATABASE_ID = int(st.secrets.get("metabase_database_id", 2))
METABASE_USER = st.secrets["metabase_user"]
METABASE_PASSWORD = st.secrets["metabase_password"]
TZ = ZoneInfo("Europe/Warsaw")

# ─────────────────────────────────────────────────────────────
# 3) SQL (snapshot + trend jednorazowy)
# ─────────────────────────────────────────────────────────────
SQL_ALLEGRO_PLN = """
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

SQL_EBAY_EUR = """
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
    AND cur.name = 'EUR'
    AND s.name ILIKE '%eBay%'
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

SQL_ALLEGRO_TREND = """
WITH params AS (
  SELECT
    {{week_from}}::date AS week_from,
    {{week_to}}::date   AS week_to
),
lines AS (
  SELECT
    COALESCE(pp.default_code, l.product_id::text) AS sku,
    COALESCE(pt.name, l.name) AS product_name,
    COALESCE(l.product_uom_qty, 0) AS qty,
    COALESCE(l.price_total, l.price_subtotal,
             l.price_unit * COALESCE(l.product_uom_qty,0), 0) AS line_total,
    (COALESCE(s.confirm_date, s.date_order, s.create_date) AT TIME ZONE 'Europe/Warsaw') AS order_ts
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
  SELECT generate_series(
           date_trunc('week', (SELECT week_from FROM params)),
           date_trunc('week', (SELECT week_to   FROM params) - INTERVAL '1 day'),
           INTERVAL '7 day'
         ) AS week_start
)
SELECT
  l.sku,
  MAX(l.product_name) AS product_name,
  w.week_start::date  AS week_start,
  SUM(l.line_total)   AS curr_rev,
  SUM(l.qty)          AS curr_qty
FROM w
LEFT JOIN lines l
  ON l.order_ts >= w.week_start
 AND l.order_ts <  w.week_start + INTERVAL '7 day'
GROUP BY l.sku, w.week_start
HAVING l.sku IS NOT NULL
ORDER BY w.week_start ASC, curr_rev DESC;
"""

SQL_EBAY_TREND = """
WITH params AS (
  SELECT
    {{week_from}}::date AS week_from,
    {{week_to}}::date   AS week_to
),
lines AS (
  SELECT
    COALESCE(pp.default_code, l.product_id::text) AS sku,
    COALESCE(pt.name, l.name) AS product_name,
    COALESCE(l.product_uom_qty, 0) AS qty,
    COALESCE(l.price_total, l.price_subtotal,
             l.price_unit * COALESCE(l.product_uom_qty,0), 0) AS line_total,
    (COALESCE(s.confirm_date, s.date_order, s.create_date) AT TIME ZONE 'Europe/Warsaw') AS order_ts
  FROM sale_order_line l
  JOIN sale_order s           ON s.id = l.order_id
  JOIN res_currency cur       ON cur.id = l.currency_id
  LEFT JOIN product_product  pp ON pp.id = l.product_id
  LEFT JOIN product_template pt ON pt.id = pp.product_tmpl_id
  WHERE s.state IN ('sale','done')
    AND cur.name = 'EUR'
    AND s.name ILIKE '%eBay%'
),
w AS (
  SELECT generate_series(
           date_trunc('week', (SELECT week_from FROM params)),
           date_trunc('week', (SELECT week_to   FROM params) - INTERVAL '1 day'),
           INTERVAL '7 day'
         ) AS week_start
)
SELECT
  l.sku,
  MAX(l.product_name) AS product_name,
  w.week_start::date  AS week_start,
  SUM(l.line_total)   AS curr_rev,
  SUM(l.qty)          AS curr_qty
FROM w
LEFT JOIN lines l
  ON l.order_ts >= w.week_start
 AND l.order_ts <  w.week_start + INTERVAL '7 day'
GROUP BY l.sku, w.week_start
HAVING l.sku IS NOT NULL
ORDER BY w.week_start ASC, curr_rev DESC;
"""

# ─────────────────────────────────────────────────────────────
# 4) Metabase session + HTTP pooling
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

@st.cache_resource
def get_http_session() -> requests.Session:
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=2)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s

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
    http = get_http_session()

    r = http.post(f"{METABASE_URL}/api/dataset", headers=headers, json=payload, timeout=120)

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
                rr = http.get(f"{METABASE_URL}/api/dataset/{token}/json", headers=headers, timeout=60)
                if rr.status_code == 200 and rr.content:
                    return {"status": 200, "json": rr.json(), "text": rr.text}
                rr = http.get(f"{METABASE_URL}/api/dataset/{token}", headers=headers, timeout=60)
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
                expected = ["sku", "product_name", "curr_rev", "curr_qty", "prev_rev", "prev_qty", "rev_change_pct",
                            "qty_change_pct"]
                col_names = expected[:n] if n == 8 else [f"c{i}" for i in range(n)]
                df = pd.DataFrame(rows, columns=col_names)
        return df

    if isinstance(j, list):
        return pd.DataFrame(j)

    return pd.DataFrame()

# ─────────────────────────────────────────────────────────────
# 7) Public query (snapshot) + trend (1 call)
# ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=600)
def query_platform_data(sql_text: str, week_start_iso: str, platform_key: str) -> pd.DataFrame:
    """Query data for specific platform - cache key includes platform for separate caching"""
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

    st.session_state[f"mb_last_status_{platform_key}"] = res["status"]
    st.session_state[f"mb_last_json_{platform_key}"] = res["json"]

    if res["status"] not in (200, 202) or not res["json"]:
        st.error(f"❌ Metabase HTTP {res['status']}: {str(res.get('text', ''))[:300]}")
        return pd.DataFrame()

    df = _metabase_json_to_df(res["json"])
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    for col in ["curr_rev", "prev_rev", "rev_change_pct", "curr_qty", "prev_qty", "qty_change_pct"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

@st.cache_data(ttl=600)
def query_platform_trend(sql_trend_text: str, week_start_date: date, weeks: int, platform_key: str) -> pd.DataFrame:
    """Pobiera trendy w jednym zapytaniu: [week_from, week_to)."""
    session = get_metabase_session()
    if not session:
        return pd.DataFrame()

    week_from = (week_start_date - timedelta(weeks=weeks - 1)).isoformat()
    week_to   = (week_start_date + timedelta(days=7)).isoformat()

    res = _dataset_call(sql_trend_text, {"week_from": week_from, "week_to": week_to}, session)
    if res["status"] == 401:
        get_metabase_session.clear()
        session = get_metabase_session()
        if not session:
            st.error("❌ Nie udało się odświeżyć sesji Metabase (trend).")
            return pd.DataFrame()
        res = _dataset_call(sql_trend_text, {"week_from": week_from, "week_to": week_to}, session)

    st.session_state[f"mb_last_status_{platform_key}_trend"] = res["status"]
    st.session_state[f"mb_last_json_{platform_key}_trend"] = res["json"]

    if res["status"] not in (200, 202) or not res["json"]:
        st.error(f"❌ Metabase (trend) HTTP {res['status']}: {str(res.get('text', ''))[:300]}")
        return pd.DataFrame()

    df = _metabase_json_to_df(res["json"])
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    for col in ["curr_rev", "curr_qty"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "week_start" in df.columns:
        df["week_start"] = pd.to_datetime(df["week_start"])
    return df

# ─────────────────────────────────────────────────────────────
# 8) Pomocnicze funkcje
# ─────────────────────────────────────────────────────────────
def last_completed_week_start(today: date | None = None) -> date:
    d = today or datetime.now(TZ).date()
    offset = d.weekday() + 7
    return d - timedelta(days=offset)

def classify_change_symbol(pct: float | np.floating | None, threshold: float):
    if pd.isna(pct): return ("—", "#9e9e9e")
    if pct >= threshold:
        if pct >= threshold * 4: return ("🟢⬆️⬆️", "#2e7d32")
        if pct >= threshold * 2: return ("🟢⬆️", "#388e3c")
        return ("🟢↑", "#66bb6a")
    if pct <= -threshold:
        if pct <= -threshold * 4: return ("🔴⬇️⬇️", "#b71c1c")
        if pct <= -threshold * 2: return ("🔴⬇️", "#d32f2f")
        return ("🔴↓", "#ef5350")
    return ("⚪≈", "#9e9e9e")

# Fallback (nieużywany domyślnie)
@st.cache_data(ttl=600)
def query_trend_many_weeks(sql_text: str, week_start_date: date, weeks: int, platform_key: str) -> pd.DataFrame:
    frames = []
    for i in range(weeks):
        ws_date = week_start_date - timedelta(weeks=i)
        iso = ws_date.isoformat()
        df_i = query_platform_data(sql_text, iso, f"{platform_key}_trend_{i}")
        if df_i is None or df_i.empty:
            continue
        df_i = df_i.copy()
        df_i["week_start"] = pd.to_datetime(ws_date)
        frames.append(df_i)
    if frames:
        return pd.concat(frames, ignore_index=True)
    return pd.DataFrame()

@st.cache_data(ttl=3600)
def to_excel_bytes(dframe: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        dframe.to_excel(writer, index=False, sheet_name="sprzedaz")
    return output.getvalue()

@st.cache_data(ttl=3600)
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

# ─────────────────────────────────────────────────────────────
# 9) Render platformy
# ─────────────────────────────────────────────────────────────
def render_platform_analysis(platform_name: str, sql_query: str, currency: str, platform_key: str, sql_query_trend: str | None = None):
    st.sidebar.header(f"🔎 Filtry ({platform_name})")
    default_week = last_completed_week_start()
    pick_day = st.sidebar.date_input(f"Wybierz tydzień ({platform_name})", value=default_week,
                                     key=f"date_{platform_key}")
    week_start = pick_day - timedelta(days=pick_day.weekday())
    week_end = week_start + timedelta(days=7)

    threshold_rev = st.sidebar.slider(f"Próg alertu — wartość sprzedaży (%) - {platform_name}", min_value=5,
                                      max_value=200, value=20, step=5, key=f"rev_{platform_key}")
    threshold_qty = st.sidebar.slider(f"Próg alertu — ilość (%) - {platform_name}", min_value=5, max_value=200,
                                      value=20, step=5, key=f"qty_{platform_key}")

    weeks_back = st.sidebar.slider(f"Ile tygodni wstecz (trend) - {platform_name}", 4, 16, 8, step=1,
                                   key=f"weeks_{platform_key}")
    top_n = st.sidebar.slider(f"Ile pozycji w TOP? - {platform_name}", 5, 20, 10, step=5, key=f"top_{platform_key}")

    debug_api = st.sidebar.checkbox(f"Debug API - {platform_name}", value=False, key=f"debug_{platform_key}")

    st.caption(f"Tydzień: **{week_start} → {week_end - timedelta(days=1)}**  •  Strefa: Europe/Warsaw  •  Waluta: {currency}")

    # Snapshot
    df = query_platform_data(sql_query, week_start.isoformat(), platform_key)

    if debug_api:
        st.write(f"Metabase HTTP: {st.session_state.get(f'mb_last_status_{platform_key}')}")
        st.subheader("Raw JSON (Metabase)")
        st.json(st.session_state.get(f"mb_last_json_{platform_key}"))

    if df.empty:
        st.warning(f"Brak danych dla wybranego tygodnia ({currency}) na {platform_name}. Zmień tydzień lub sprawdź źródło.")
        return

    need = {"sku", "product_name", "curr_rev", "prev_rev", "curr_qty", "prev_qty", "rev_change_pct", "qty_change_pct"}
    missing = [c for c in need if c not in df.columns]
    if missing:
        st.error(f"Brak kolumn w danych: {missing}")
        st.dataframe(df.head(), width='stretch', hide_index=True)
        return

    # TOP N
    df_top = df.sort_values("curr_rev", ascending=False).head(top_n).copy()

    # Klasyfikacja zmian
    df_top["status_rev"], df_top["color_rev"] = zip(*df_top["rev_change_pct"].apply(lambda x: classify_change_symbol(x, threshold_rev)))
    df_top["status_qty"], df_top["color_qty"] = zip(*df_top["qty_change_pct"].apply(lambda x: classify_change_symbol(x, threshold_qty)))

    # KPI
    sum_curr = float(df["curr_rev"].sum() or 0)
    sum_prev = float(df["prev_rev"].sum() or 0)
    delta_abs = sum_curr - sum_prev
    delta_pct = (delta_abs / sum_prev * 100) if sum_prev else 0.0

    currency_symbol = "zł" if currency == "PLN" else "€"

    c1, c2, c3 = st.columns(3)
    c1.metric(f"Suma sprzedaży ({currency}, tydzień)", f"{sum_curr:,.0f} {currency_symbol}".replace(",", " "))
    c2.metric(f"Zmiana vs poprzedni ({currency})", f"{delta_abs:,.0f} {currency_symbol}".replace(",", " "))
    c3.metric("Zmiana % całości", f"{delta_pct:+.0f}%")

    # Wykres TOP N
    st.subheader(f"TOP {top_n} — Sprzedaż tygodnia ({currency})")

    colors = df_top["color_rev"].tolist()
    hover = df_top.apply(
        lambda r: f"{r.sku} — {str(r.product_name)[:80]}<br>Sprzedaż: {r.curr_rev:,.0f} {currency_symbol}<br>Zmiana: {('n/d' if pd.isna(r.rev_change_pct) else f'{r.rev_change_pct:+.0f}%')}",
        axis=1
    )
    fig = go.Figure(go.Bar(
        x=df_top["curr_rev"],
        y=df_top["sku"],
        orientation="h",
        marker=dict(color=colors),
        hoverinfo="text",
        hovertext=hover
    ))
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=520, margin=dict(l=150), autosize=True)
    st.plotly_chart(fig, config=PLOTLY_CONFIG)  # ← tylko config

    # Waterfall
    st.subheader(f"📊 Wkład TOP produktów w zmianę sprzedaży (waterfall) - {currency}")
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
    fig_wf.update_traces(
        increasing=dict(marker=dict(color="#66bb6a")),
        decreasing=dict(marker=dict(color="#ef5350")),
        totals=dict(marker=dict(color="#42a5f5"))
    )
    fig_wf.update_layout(title=f"Wkład produktów w zmianę sprzedaży ({currency})", showlegend=False, height=520, autosize=True)
    st.plotly_chart(fig_wf, config=PLOTLY_CONFIG)  # ← tylko config

    # Trend tygodniowy
    st.subheader("📈 Trendy tygodniowe — wybierz SKU do analizy trendu")

    df_trend = (
        query_platform_trend(sql_query_trend, week_start, weeks=weeks_back, platform_key=platform_key)
        if sql_query_trend else
        query_trend_many_weeks(sql_query, week_start, weeks=weeks_back, platform_key=platform_key)
    )

    if df_trend.empty:
        st.info("Brak danych trendu (dla wybranej liczby tygodni).")
    else:
        all_skus = sorted(df_trend["sku"].dropna().unique().tolist())

        search_term = st.text_input(f"Szukaj SKU lub produktu - {platform_name}", "", key=f"search_{platform_key}")
        filtered_skus = [sku for sku in all_skus if search_term.lower() in str(sku).lower()] if search_term else all_skus

        pick_skus = st.multiselect(
            f"Wybierz SKU do analizy trendu - {platform_name}",
            options=filtered_skus,
            default=filtered_skus[:5] if filtered_skus else [],
            key=f"multiselect_{platform_key}"
        )

        chart_type = st.radio(f"Typ wykresu - {platform_name}", ["area", "line"], index=0, horizontal=True,
                              key=f"chart_{platform_key}")

        if pick_skus:
            df_plot = df_trend[df_trend["sku"].isin(pick_skus)].copy()
            df_plot = df_plot.groupby(["week_start", "sku"])["curr_rev"].sum().reset_index()
            pv = df_plot.pivot(index="week_start", columns="sku", values="curr_rev").fillna(0).sort_index()

            fig_tr = go.Figure()
            for sku in pv.columns:
                yvals = pv[sku].values
                if chart_type == "area":
                    fig_tr.add_trace(go.Scatter(x=pv.index, y=yvals, mode="lines", name=sku, stackgroup="one"))
                else:
                    fig_tr.add_trace(go.Scatter(x=pv.index, y=yvals, mode="lines+markers", name=sku))
            fig_tr.update_layout(xaxis=dict(tickformat="%Y-%m-%d"), yaxis_title=f"Sprzedaż ({currency})", height=520, autosize=True)
            st.plotly_chart(fig_tr, config=PLOTLY_CONFIG)  # ← tylko config

    # Tabele wzrostów/spadków
    COLS_DISPLAY = {
        "sku": "SKU",
        "product_name": "Produkt",
        "curr_rev": f"Sprzedaż tygodnia ({currency})",
        "prev_rev": f"Sprzedaż poprzedniego tygodnia ({currency})",
        "rev_change_pct": "Zmiana sprzedaży %",
        "curr_qty": "Ilość tygodnia (szt.)",
        "prev_qty": "Ilość poprzedniego tygodnia (szt.)",
        "qty_change_pct": "Zmiana ilości %",
        "status_rev": "Status (wartość)",
        "status_qty": "Status (ilość)"
    }

    def to_display(df_in: pd.DataFrame) -> pd.DataFrame:
        out = df_in.rename(columns=COLS_DISPLAY)
        keep = [c for c in
                ["SKU", "Produkt", f"Sprzedaż tygodnia ({currency})", f"Sprzedaż poprzedniego tygodnia ({currency})",
                 "Zmiana sprzedaży %", "Ilość tygodnia (szt.)", "Ilość poprzedniego tygodnia (szt.)", "Zmiana ilości %",
                 "Status (wartość)", "Status (ilość)"] if c in out.columns]
        return out[keep]

    ups = df_top[df_top["rev_change_pct"] >= threshold_rev].copy()
    downs = df_top[df_top["rev_change_pct"] <= -threshold_rev].copy()

    colA, colB = st.columns(2)
    with colA:
        st.markdown("### 🚀 Wzrosty (≥ próg)")
        if ups.empty:
            st.info("Brak pozycji przekraczających próg wzrostu.")
        else:
            st.dataframe(to_display(ups), width='stretch', hide_index=True)
    with colB:
        st.markdown("### 📉 Spadki (≤ -próg)")
        if downs.empty:
            st.info("Brak pozycji przekraczających próg spadku.")
        else:
            st.dataframe(to_display(downs), width='stretch', hide_index=True)

    with st.expander("🔎 Podgląd TOP (tabela)"):
        st.dataframe(to_display(df_top), width='stretch', hide_index=True)

# ─────────────────────────────────────────────────────────────
# 10) Główna aplikacja z zakładkami
# ─────────────────────────────────────────────────────────────
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
    .stTabs [data-baseweb="tab-list"] { gap: 24px; }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: rgba(255, 255, 255, 0.2);
        border-radius: 4px 4px 0px 0px;
        gap: 1px;
        padding-top: 10px;
        padding-bottom: 10px;
    }
    .stTabs [aria-selected="true"] { background-color: #ffffff; }
    </style>
""", unsafe_allow_html=True)

tab1, tab2 = st.tabs(["🇵🇱 Allegro.pl (PLN)", "🇩🇪 eBay.de (EUR)"])

with tab1:
    st.header("🇵🇱 Allegro.pl - Analiza sprzedaży (PLN)")
    render_platform_analysis(
        platform_name="Allegro.pl",
        sql_query=SQL_ALLEGRO_PLN,
        currency="PLN",
        platform_key="allegro",
        sql_query_trend=SQL_ALLEGRO_TREND
    )

with tab2:
    st.header("🇩🇪 eBay.de - Analiza sprzedaży (EUR)")
    render_platform_analysis(
        platform_name="eBay.de",
        sql_query=SQL_EBAY_EUR,
        currency="EUR",
        platform_key="ebay",
        sql_query_trend=SQL_EBAY_TREND
    )
