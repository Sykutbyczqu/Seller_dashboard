# streamlit_app.py
import io
import os
import time
import folium
from streamlit_folium import st_folium
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import json
import plotly.express as px
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
st.set_page_config(page_title="Sprzedaż: WoW TOP — Rozszerzone", layout="wide")
st.title("🛒 Sprzedaż — Trendy i TOP N")

TZ = ZoneInfo("Europe/Warsaw")

# ─────────────────────────────────────────────────────────────
# 2) Ustawienia Metabase
# ─────────────────────────────────────────────────────────────
METABASE_URL = "https://metabase.emamas.ideaerp.pl"
METABASE_DATABASE_ID = int(st.secrets.get("metabase_database_id", 2))
METABASE_USER = st.secrets["metabase_user"]
METABASE_PASSWORD = st.secrets["metabase_password"]

# ─────────────────────────────────────────────────────────────
# 3) SQL — snapshoty WoW (po jednym na platformę)
# ─────────────────────────────────────────────────────────────
ZIP_TO_REGION = {
    # Dolnoslaskie
    "50": "Dolnoslaskie", "51": "Dolnoslaskie", "52": "Dolnoslaskie", "53": "Dolnoslaskie",
    "54": "Dolnoslaskie", "55": "Dolnoslaskie", "56": "Dolnoslaskie", "57": "Dolnoslaskie",
    "58": "Dolnoslaskie", "59": "Dolnoslaskie",

    # Kujawsko-Pomorskie
    "85": "Kujawsko-Pomorskie", "86": "Kujawsko-Pomorskie", "87": "Kujawsko-Pomorskie", "88": "Kujawsko-Pomorskie",

    # Lubelskie
    "20": "Lubelskie", "21": "Lubelskie", "22": "Lubelskie", "23": "Lubelskie", "24": "Lubelskie",

    # Lubuskie
    "65": "Lubuskie", "66": "Lubuskie", "67": "Lubuskie", "68": "Lubuskie", "69": "Lubuskie",

    # Lodzkie
    "90": "Lodzkie", "91": "Lodzkie", "92": "Lodzkie", "93": "Lodzkie", "94": "Lodzkie",
    "95": "Lodzkie", "96": "Lodzkie", "97": "Lodzkie", "98": "Lodzkie", "99": "Lodzkie",

    # Malopolskie
    "30": "Malopolskie", "31": "Malopolskie", "32": "Malopolskie", "33": "Malopolskie", "34": "Malopolskie",

    # Mazowieckie
    "00": "Mazowieckie", "01": "Mazowieckie", "02": "Mazowieckie", "03": "Mazowieckie", "04": "Mazowieckie",
    "05": "Mazowieckie", "06": "Mazowieckie", "07": "Mazowieckie", "08": "Mazowieckie", "09": "Mazowieckie",

    # Opolskie
    "45": "Opolskie", "46": "Opolskie", "47": "Opolskie", "48": "Opolskie", "49": "Opolskie",

    # Podkarpackie
    "35": "Podkarpackie", "36": "Podkarpackie", "37": "Podkarpackie", "38": "Podkarpackie", "39": "Podkarpackie",

    # Podlaskie
    "15": "Podlaskie", "16": "Podlaskie", "17": "Podlaskie", "18": "Podlaskie", "19": "Podlaskie",

    # Pomorskie
    "80": "Pomorskie", "81": "Pomorskie", "82": "Pomorskie", "83": "Pomorskie", "84": "Pomorskie",

    # Slaskie
    "40": "Slaskie", "41": "Slaskie", "42": "Slaskie", "43": "Slaskie", "44": "Slaskie",

    # Swietokrzyskie
    "25": "Swietokrzyskie", "26": "Swietokrzyskie", "27": "Swietokrzyskie", "28": "Swietokrzyskie", "29": "Swietokrzyskie",

    # Warminsko-Mazurskie
    "10": "Warminsko-Mazurskie", "11": "Warminsko-Mazurskie", "12": "Warminsko-Mazurskie",
    "13": "Warminsko-Mazurskie", "14": "Warminsko-Mazurskie",

    # Wielkopolskie
    "60": "Wielkopolskie", "61": "Wielkopolskie", "62": "Wielkopolskie", "63": "Wielkopolskie", "64": "Wielkopolskie",

    # Zachodniopomorskie
    "70": "Zachodniopomorskie", "71": "Zachodniopomorskie", "72": "Zachodniopomorskie",
    "73": "Zachodniopomorskie", "74": "Zachodniopomorskie", "75": "Zachodniopomorskie",
    "76": "Zachodniopomorskie", "77": "Zachodniopomorskie", "78": "Zachodniopomorskie",
}

SQL_WOW_POLAND_ZIP = """
WITH params AS (
  SELECT
    {{week_start}}::date AS week_start,
    ({{week_start}}::date + INTERVAL '7 day') AS week_end
),
lines AS (
  SELECT
    l.product_id,
    COALESCE(pp.default_code, l.product_id::text) AS sku,
    COALESCE(pt.name, l.name) AS product_name,
    COALESCE(l.price_total, l.price_subtotal,
             l.price_unit * COALESCE(l.product_uom_qty,0), 0) AS line_total,
    COALESCE(s.confirm_date, s.date_order, s.create_date) AS order_ts,
    sh.receiver_zip,
    l.id AS line_id,
    s.id AS order_id
  FROM sale_order_line l
  JOIN sale_order s ON s.id = l.order_id
  JOIN res_currency cur ON cur.id = l.currency_id
  LEFT JOIN shipping_order sh ON sh.sale_order_id = s.id
  LEFT JOIN product_product pp ON pp.id = l.product_id
  LEFT JOIN product_template pt ON pt.id = pp.product_tmpl_id
  WHERE s.state IN ('sale','done')
    AND cur.name = 'PLN'
    AND s.name ILIKE '%Allegro%'
    AND s.name LIKE '%-1'
    AND (s.confirm_date AT TIME ZONE 'Europe/Warsaw') >= (SELECT week_start FROM params)
    AND (s.confirm_date AT TIME ZONE 'Europe/Warsaw') < (SELECT week_end FROM params)
),
-- Deduplikacja: jedna linia zamówienia = jeden adres
lines_dedup AS (
  SELECT DISTINCT ON (line_id)
    line_id,
    sku,
    product_name,
    line_total,
    receiver_zip
  FROM lines
  WHERE receiver_zip IS NOT NULL
  ORDER BY line_id, order_id
)
SELECT
  receiver_zip,
  sku,
  product_name,
  SUM(line_total) AS revenue
FROM lines_dedup
GROUP BY receiver_zip, sku, product_name
ORDER BY receiver_zip, revenue DESC;
"""
SQL_WOW_ALLEGRO_PLN = """
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
    AND s.name LIKE '%-1'
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

SQL_WOW_EBAY_EUR = """
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

SQL_WOW_KAUFLAND_EUR = """
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
    AND s.name ILIKE '%Kaufland%'
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

# ─────────────────────────────────────────────────────────────
# 3a) SQL — liczniki zamówień do AOV (po jednym na platformę)
# ─────────────────────────────────────────────────────────────
SQL_ORDERS_ALLEGRO_PLN = """
WITH params AS (
  SELECT
    {{week_start}}::date AS week_start,
    ({{week_start}}::date + INTERVAL '7 day') AS week_end,
    ({{week_start}}::date - INTERVAL '7 day') AS prev_start,
    {{week_start}}::date AS prev_end
),
orders_raw AS (
  SELECT DISTINCT
    s.id AS order_id,
    (COALESCE(s.confirm_date, s.date_order, s.create_date)) AS order_ts
  FROM sale_order_line l
  JOIN sale_order s      ON s.id = l.order_id
  JOIN res_currency cur  ON cur.id = l.currency_id
  WHERE s.state IN ('sale','done')
    AND cur.name = 'PLN'
    AND s.name ILIKE '%Allegro%'
    AND s.name LIKE '%-1'
)
SELECT
  COUNT(*) FILTER (WHERE (order_ts AT TIME ZONE 'Europe/Warsaw') >= p.week_start
                   AND   (order_ts AT TIME ZONE 'Europe/Warsaw') <  p.week_end)  AS orders_curr,
  COUNT(*) FILTER (WHERE (order_ts AT TIME ZONE 'Europe/Warsaw') >= p.prev_start
                   AND   (order_ts AT TIME ZONE 'Europe/Warsaw') <  p.prev_end)  AS orders_prev
FROM orders_raw, params p;
"""

SQL_ORDERS_EBAY_EUR = """
WITH params AS (
  SELECT
    {{week_start}}::date AS week_start,
    ({{week_start}}::date + INTERVAL '7 day') AS week_end,
    ({{week_start}}::date - INTERVAL '7 day') AS prev_start,
    {{week_start}}::date AS prev_end
),
orders_raw AS (
  SELECT DISTINCT
    s.id AS order_id,
    (COALESCE(s.confirm_date, s.date_order, s.create_date)) AS order_ts
  FROM sale_order_line l
  JOIN sale_order s      ON s.id = l.order_id
  JOIN res_currency cur  ON cur.id = l.currency_id
  WHERE s.state IN ('sale','done')
    AND cur.name = 'EUR'
    AND s.name ILIKE '%eBay%'
)
SELECT
  COUNT(*) FILTER (WHERE (order_ts AT TIME ZONE 'Europe/Warsaw') >= p.week_start
                   AND   (order_ts AT TIME ZONE 'Europe/Warsaw') <  p.week_end)  AS orders_curr,
  COUNT(*) FILTER (WHERE (order_ts AT TIME ZONE 'Europe/Warsaw') >= p.prev_start
                   AND   (order_ts AT TIME ZONE 'Europe/Warsaw') <  p.prev_end)  AS orders_prev
FROM orders_raw, params p;
"""

SQL_ORDERS_KAUFLAND_EUR = """
WITH params AS (
  SELECT
    {{week_start}}::date AS week_start,
    ({{week_start}}::date + INTERVAL '7 day') AS week_end,
    ({{week_start}}::date - INTERVAL '7 day') AS prev_start,
    {{week_start}}::date AS prev_end
),
orders_raw AS (
  SELECT DISTINCT
    s.id AS order_id,
    (COALESCE(s.confirm_date, s.date_order, s.create_date)) AS order_ts
  FROM sale_order_line l
  JOIN sale_order s      ON s.id = l.order_id
  JOIN res_currency cur  ON cur.id = l.currency_id
  WHERE s.state IN ('sale','done')
    AND cur.name = 'EUR'
    AND s.name ILIKE '%Kaufland%'
)
SELECT
  COUNT(*) FILTER (WHERE (order_ts AT TIME ZONE 'Europe/Warsaw') >= p.week_start
                   AND   (order_ts AT TIME ZONE 'Europe/Warsaw') <  p.week_end)  AS orders_curr,
  COUNT(*) FILTER (WHERE (order_ts AT TIME ZONE 'Europe/Warsaw') >= p.prev_start
                   AND   (order_ts AT TIME ZONE 'Europe/Warsaw') <  p.prev_end)  AS orders_prev
FROM orders_raw, params p;
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
# 7) Zapytania pomocnicze
# ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=600)
def query_snapshot(sql_text: str, week_start_iso: str) -> pd.DataFrame:
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

@st.cache_data(ttl=600)
def query_order_counts(sql_text: str, week_start_iso: str) -> pd.DataFrame:
    """Zwraca 1-wierszowy DF z kolumnami: orders_curr, orders_prev."""
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
    if res["status"] not in (200, 202) or not res["json"]:
        return pd.DataFrame()
    df = _metabase_json_to_df(res["json"])
    df.columns = [str(c).strip().lower() for c in df.columns]
    for col in ["orders_curr", "orders_prev"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    return df

@st.cache_data(ttl=600)
def query_trend_many_weeks(sql_text: str, week_start_date: date, weeks: int = 8) -> pd.DataFrame:
    frames = []
    for i in range(weeks):
        ws_date = week_start_date - timedelta(weeks=i)
        df_i = query_snapshot(sql_text, ws_date.isoformat())
        if df_i is None or df_i.empty:
            continue
        df_i = df_i.copy()
        df_i["week_start"] = pd.to_datetime(ws_date)
        frames.append(df_i)
    if frames:
        return pd.concat(frames, ignore_index=True)
    return pd.DataFrame()
@st.cache_data(ttl=600)
def query_poland_zip(week_start_iso: str) -> pd.DataFrame:
    session = get_metabase_session()
    if not session:
        return pd.DataFrame()
    res = _dataset_call(SQL_WOW_POLAND_ZIP, {"week_start": week_start_iso}, session)
    if res["status"] != 200 or not res["json"]:
        return pd.DataFrame()
    df = _metabase_json_to_df(res["json"])
    df.columns = [c.lower() for c in df.columns]
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").fillna(0.0)
    return df

# ─────────────────────────────────────────────────────────────
# 8) UI — wspólne filtry
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
# 9) Wspólne pomocnicze
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

COLS_DISPLAY_BASE = {
    "sku": "SKU",
    "product_name": "Produkt",
    "curr_rev": "Sprzedaż tygodnia ({CUR})",
    "prev_rev": "Sprzedaż poprzedniego tygodnia ({CUR})",
    "rev_change_pct": "Zmiana sprzedaży %",
    "curr_qty": "Ilość tygodnia (szt.)",
    "prev_qty": "Ilość poprzedniego tygodnia (szt.)",
    "qty_change_pct": "Zmiana ilości %",
    "status_rev": "Status (wartość)",
    "status_qty": "Status (ilość)"
}

def to_display(df_in: pd.DataFrame, cur: str) -> pd.DataFrame:
    cols = {k: (v.replace("{CUR}", cur)) for k, v in COLS_DISPLAY_BASE.items()}
    out = df_in.rename(columns=cols)
    keep = [c for c in [
        "SKU", "Produkt",
        f"Sprzedaż tygodnia ({cur})",
        f"Sprzedaż poprzedniego tygodnia ({cur})",
        "Zmiana sprzedaży %", "Ilość tygodnia (szt.)",
        "Ilość poprzedniego tygodnia (szt.)", "Zmiana ilości %",
        "Status (wartość)", "Status (ilość)"
    ] if c in out.columns]
    return out[keep]

def to_excel_bytes(dframe: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        dframe.to_excel(writer, index=False, sheet_name="sprzedaz")
    return output.getvalue()

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
# 10) Renderer platformy (z AOV i bogatym hoverem)
# ─────────────────────────────────────────────────────────────
def render_platform(platform_key: str,
                    platform_title: str,
                    sql_query: str,
                    sql_orders: str,
                    currency_label: str,
                    currency_symbol: str):

    st.header(platform_title)

    # Snapshot SKU
    df = query_snapshot(sql_query, week_start.isoformat())
    if df.empty:
        st.warning(f"Brak danych dla wybranego tygodnia ({currency_label}).")
        return

    need = {"sku","product_name","curr_rev","prev_rev","curr_qty","prev_qty","rev_change_pct","qty_change_pct"}
    missing = [c for c in need if c not in df.columns]
    if missing:
        st.error(f"Brak kolumn w danych: {missing}")
        st.dataframe(df.head(), width="stretch")
        return

    # TOP N
    df_top = df.sort_values("curr_rev", ascending=False).head(top_n).copy()
    df_top["status_rev"], df_top["color_rev"] = zip(*df_top["rev_change_pct"].apply(lambda x: classify_change_symbol(x, threshold_rev)))
    df_top["status_qty"], df_top["color_qty"] = zip(*df_top["qty_change_pct"].apply(lambda x: classify_change_symbol(x, threshold_qty)))

    # KPI sumy
    sum_curr = float(df["curr_rev"].sum() or 0)
    sum_prev = float(df["prev_rev"].sum() or 0)
    delta_abs = sum_curr - sum_prev
    delta_pct = (delta_abs / sum_prev * 100) if sum_prev else 0.0

    # AOV (średnia wartość koszyka)
    df_ord = query_order_counts(sql_orders, week_start.isoformat())
    orders_curr = int(df_ord["orders_curr"].iloc[0]) if not df_ord.empty and "orders_curr" in df_ord.columns else 0
    orders_prev = int(df_ord["orders_prev"].iloc[0]) if not df_ord.empty and "orders_prev" in df_ord.columns else 0

    aov_curr = (sum_curr / orders_curr) if orders_curr else np.nan
    aov_prev = (sum_prev / orders_prev) if orders_prev else np.nan
    aov_delta = (aov_curr - aov_prev) if (pd.notna(aov_curr) and pd.notna(aov_prev)) else np.nan

    # Sticky KPI
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
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"Suma sprzedaży ({currency_label}, tydzień)", f"{sum_curr:,.0f} {currency_symbol}".replace(",", " "))
    c2.metric(f"Zmiana vs poprzedni ({currency_label})", f"{delta_abs:,.0f} {currency_symbol}".replace(",", " "))
    c3.metric("Zmiana % całości", f"{delta_pct:+.0f}%")
    if pd.notna(aov_curr):
        delta_str = (f"{aov_delta:+,.0f} {currency_symbol}".replace(",", " ")) if pd.notna(aov_delta) else "—"
        c4.metric("Średnia wartość koszyka", f"{aov_curr:,.0f} {currency_symbol}".replace(",", " "), delta=delta_str)
    else:
        c4.metric("Średnia wartość koszyka", "—", delta="—")
    st.markdown('</div>', unsafe_allow_html=True)

    # TOP N — wykres
    st.subheader(f"TOP {top_n} — Sprzedaż tygodnia ({currency_label})")
    colors = df_top["color_rev"].tolist()
    hover = df_top.apply(
        lambda r: f"{r.sku} — {r.product_name}<br>Sprzedaż: {r.curr_rev:,.0f} {currency_symbol}<br>Zmiana: {('n/d' if pd.isna(r.rev_change_pct) else f'{r.rev_change_pct:+.0f}%')}",
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
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=520, margin=dict(l=150))
    st.plotly_chart(fig, width="stretch")

    # Waterfall
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
    fig_wf.update_traces(
        increasing=dict(marker=dict(color="#66bb6a")),
        decreasing=dict(marker=dict(color="#ef5350")),
        totals=dict(marker=dict(color="#42a5f5"))
    )
    fig_wf.update_layout(title=f"Wkład produktów w zmianę sprzedaży ({currency_label})", showlegend=False, height=420)
    st.plotly_chart(fig_wf, width="stretch")

    # Trend tygodniowy — bogaty hover
    st.subheader("📈 Trendy tygodniowe — wybierz SKU do analizy trendu")

    df_trend = query_trend_many_weeks(sql_query, week_start, weeks=weeks_back)
    if df_trend.empty:
        st.info("Brak danych trendu (dla wybranej liczby tygodni).")
    else:
        all_skus = sorted(df_trend["sku"].dropna().unique().tolist())

        search_term = st.text_input(f"Szukaj SKU lub produktu — {platform_key}", "")
        filtered_skus = [sku for sku in all_skus if search_term.lower() in str(sku).lower()] if search_term else all_skus

        pick_skus = st.multiselect(
            f"Wybierz SKU do analizy trendu — {platform_key}",
            options=filtered_skus,
            default=filtered_skus[:5] if filtered_skus else []
        )

        chart_type = st.radio(f"Typ wykresu — {platform_key}", ["area", "line"], index=1, horizontal=True)

        if pick_skus:
            df_plot = df_trend[df_trend["sku"].isin(pick_skus)].copy()
            df_plot = df_plot.groupby(["week_start", "sku"], as_index=False)[["curr_rev", "curr_qty"]].sum()

            full_weeks = pd.date_range(
                start=df_plot["week_start"].min().normalize(),
                end=df_plot["week_start"].max().normalize(),
                freq="W-MON"
            )

            pv_rev = df_plot.pivot(index="week_start", columns="sku", values="curr_rev").reindex(full_weeks).fillna(0.0)
            pv_qty = df_plot.pivot(index="week_start", columns="sku", values="curr_qty").reindex(full_weeks).fillna(0.0)

            week_end_labels = (pv_rev.index + pd.Timedelta(days=6)).strftime("%Y-%m-%d").values

            fig_tr = go.Figure()
            for sku in pv_rev.columns:
                y = pv_rev[sku].values.astype(float)
                q = pv_qty[sku].values.astype(float)
                prev = np.concatenate(([np.nan], y[:-1]))
                wow_abs = y - prev
                wow_pct = np.where((prev > 0) & np.isfinite(prev), (y - prev) / prev * 100.0, np.nan)

                custom = np.column_stack([q, wow_abs, wow_pct, week_end_labels])
                hovertemplate = (
                    "<b>%{fullData.name}</b><br>"
                    "Tydzień: %{x|%Y-%m-%d} → %{customdata[3]}<br>"
                    "Sprzedaż: %{y:,.0f} " + currency_symbol + "<br>"
                    "Ilość: %{customdata[0]:,.0f} szt.<br>"
                    "WoW: %{customdata[1]:+,.0f} " + currency_symbol + " (%{customdata[2]:+.0f}%)"
                    "<extra></extra>"
                )

                if chart_type == "area":
                    fig_tr.add_trace(
                        go.Scatter(
                            x=pv_rev.index, y=y, name=sku, mode="lines",
                            stackgroup="one", customdata=custom, hovertemplate=hovertemplate
                        )
                    )
                else:
                    fig_tr.add_trace(
                        go.Scatter(
                            x=pv_rev.index, y=y, name=sku, mode="lines+markers",
                            customdata=custom, hovertemplate=hovertemplate
                        )
                    )

            fig_tr.update_layout(
                xaxis=dict(tickformat="%Y-%m-%d"),
                yaxis_title=f"Sprzedaż ({currency_label})",
                height=520,
                hovermode="x unified"
            )
            st.plotly_chart(fig_tr, width="stretch")

    # Tabele
    ups = df_top[df_top["rev_change_pct"] >= threshold_rev].copy()
    downs = df_top[df_top["rev_change_pct"] <= -threshold_rev].copy()

    colA, colB = st.columns(2)
    with colA:
        st.markdown("### 🚀 Wzrosty (≥ próg)")
        if ups.empty:
            st.info("Brak pozycji przekraczających próg wzrostu.")
        else:
            st.dataframe(to_display(ups, currency_label), width="stretch")
    with colB:
        st.markdown("### 📉 Spadki (≤ -próg)")
        if downs.empty:
            st.info("Brak pozycji przekraczających próg spadku.")
        else:
            st.dataframe(to_display(downs, currency_label), width="stretch")

    with st.expander("🔎 Podgląd TOP (tabela)"):
        st.dataframe(to_display(df_top, currency_label), width="stretch")

    # Eksport
    st.subheader("📥 Eksport danych")
    d1, d2, d3 = st.columns(3)
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    d1.download_button(f"📥 Pobierz (CSV) — {platform_key}", csv_bytes, f"sprzedaz_{platform_key}.csv", "text/csv")
    excel_bytes = to_excel_bytes(df)
    d2.download_button(f"📥 Pobierz (Excel) — {platform_key}", excel_bytes, f"sprzedaz_{platform_key}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    pdf_bytes = df_to_pdf_bytes(to_display(df_top, currency_label), title=f"TOP{top_n} - raport tygodniowy - {platform_key}")
    d3.download_button(f"📥 Pobierz (PDF) — TOP — {platform_key}", pdf_bytes,
                       f"sprzedaz_top_{platform_key}.pdf", "application/pdf")

    # QA / Debug
    with st.expander(f"🔧 Panel QA / Debug — {platform_key}"):
        st.write("Metabase HTTP:", st.session_state.get("mb_last_status"))
        st.write("Liczba wierszy (snapshot):", len(df))
        st.write("Liczba SKU w snapshot:", df["sku"].nunique())
        st.write("Zamówienia (tydzień / poprzedni):", orders_curr, orders_prev)
        if debug_api:
            st.subheader("Raw JSON (Metabase)")
            st.json(st.session_state.get("mb_last_json"))

# Mapa polski
def render_poland_map(week_start: date):
    st.header("🗺️ Sprzedaż wg województw (na podstawie ZIP)")

    df = query_poland_zip(week_start.isoformat())
    if df.empty:
        st.warning("Brak danych adresów ZIP dla tego tygodnia.")
        return

    df["zip_prefix"] = df["receiver_zip"].astype(str).str[:2]
    df["region"] = df["zip_prefix"].map(ZIP_TO_REGION)
    df = df.dropna(subset=["region"])

    if df.empty:
        st.warning("Po mapowaniu ZIP → województwo brak danych.")
        return

    # Debug: pokaż unikalne regiony
    st.info(f"Znalezione województwa w danych: {sorted(df['region'].unique())}")

    grouped = df.groupby(["region", "sku", "product_name"], as_index=False).agg({"revenue": "sum"})
    region_totals = grouped.groupby("region", as_index=False)["revenue"].sum().rename(
        columns={"revenue": "region_total"})
    grouped = grouped.merge(region_totals, on="region", how="left")
    grouped["share_pct"] = grouped["revenue"] / grouped["region_total"] * 100

    # KPI
    st.metric("Łączna sprzedaż (wszystkie regiony)", f"{region_totals['region_total'].sum():,.0f} zł".replace(",", " "))

    # Przygotuj tooltips
    hover_text = {}
    for region, sub in grouped.groupby("region"):
        sub_sorted = sub.sort_values("revenue", ascending=False).reset_index(drop=True)
        top5 = sub_sorted.head(5)
        total = sub_sorted["revenue"].sum()
        lines = [f"<b>{region}</b><br>Łącznie: {total:,.0f} zł<br><br>TOP 5:"]
        lines += [f"{i + 1}. {row['sku']}: {row['revenue']:,.0f} zł ({row['share_pct']:.1f}%)"
                  for i, (_, row) in enumerate(top5.iterrows())]
        hover_text[region] = "<br>".join(lines)

    df_map = region_totals.copy()

    import os
    geojson_path = os.path.join(os.path.dirname(__file__), "polska-wojewodztwa.geojson")

    if not os.path.exists(geojson_path):
        st.error(f"Nie znaleziono pliku GeoJSON: {geojson_path}")
        return

    try:
        with open(geojson_path, "r", encoding="utf-8") as f:
            geojson = json.load(f)
    except Exception as e:
        st.error(f"Błąd wczytywania GeoJSON: {e}")
        return

    region_revenue_dict = df_map.set_index("region")["region_total"].to_dict()

    max_revenue = df_map["region_total"].max()
    min_revenue = df_map["region_total"].min()

    def get_color(revenue):
        if revenue is None or pd.isna(revenue):
            return "#e0e0e0"
        if max_revenue == min_revenue:
            return "#42a5f5"
        norm = (revenue - min_revenue) / (max_revenue - min_revenue)
        r = int(255 * (1 - norm))
        g = int(200 * (1 - norm))
        b = 255
        return f"#{r:02x}{g:02x}{b:02x}"

    m = folium.Map(location=[52.0, 19.0], zoom_start=6, tiles="CartoDB positron")

    # Dodaj GeoJSON z popupami
    for feature in geojson.get("features", []):
        region_name = feature.get("properties", {}).get("name")
        revenue = region_revenue_dict.get(region_name, 0)

        popup_content = hover_text.get(region_name, f"<b>{region_name}</b><br>Brak danych")

        folium.GeoJson(
            feature,
            style_function=lambda x, rev=revenue: {
                "fillColor": get_color(rev),
                "color": "black",
                "weight": 1.5,
                "fillOpacity": 0.7 if rev > 0 else 0.3,
            },
            popup=folium.Popup(popup_content, max_width=300),
            tooltip=f"{region_name}: {revenue:,.0f} zł" if revenue > 0 else f"{region_name}: brak danych"
        ).add_to(m)

    st_folium(m, width=1200, height=600)

    # SEKCJA INTERAKTYWNA - wybór województwa
    st.subheader("📊 Szczegóły sprzedaży według województw")

    # Wykres słupkowy wszystkich regionów
    region_totals_sorted = region_totals.sort_values("region_total", ascending=False)

    fig_bar = go.Figure(go.Bar(
        x=region_totals_sorted["region_total"],
        y=region_totals_sorted["region"],
        orientation="h",
        marker=dict(color=region_totals_sorted["region_total"], colorscale="Blues"),
        text=region_totals_sorted["region_total"].apply(lambda x: f"{x:,.0f} zł"),
        textposition="outside"
    ))
    fig_bar.update_layout(
        xaxis_title="Przychód (zł)",
        yaxis_title="Województwo",
        height=400,
        yaxis={"categoryorder": "total ascending"}
    )
    st.plotly_chart(fig_bar, use_container_width=True)

    # Wybór województwa do szczegółów
    st.markdown("---")
    selected_region = st.selectbox(
        "🔍 Wybierz województwo, aby zobaczyć TOP produkty",
        options=sorted(region_totals["region"].tolist()),
        index=0
    )

    if selected_region:
        region_data = grouped[grouped["region"] == selected_region].copy()
        region_data = region_data.sort_values("revenue", ascending=False)

        col1, col2 = st.columns(2)
        with col1:
            st.metric(
                f"Sprzedaż w {selected_region}",
                f"{region_data['revenue'].sum():,.0f} zł".replace(",", " ")
            )
        with col2:
            st.metric(
                "Liczba różnych produktów",
                f"{region_data['sku'].nunique()}"
            )

        # TOP 10 produktów
        st.markdown(f"#### TOP 10 produktów w {selected_region}")
        top_products = region_data.head(10).copy()
        top_products["revenue_formatted"] = top_products["revenue"].apply(lambda x: f"{x:,.0f} zł")

        display_df = top_products[["sku", "product_name", "revenue_formatted", "share_pct"]].rename(columns={
            "sku": "SKU",
            "product_name": "Nazwa produktu",
            "revenue_formatted": "Przychód",
            "share_pct": "Udział %"
        })
        display_df["Udział %"] = display_df["Udział %"].round(2)

        st.dataframe(display_df, use_container_width=True, hide_index=True)

    # Tabela wszystkich regionów
    with st.expander("📋 Pełna tabela - wszystkie województwa"):
        summary = region_totals.sort_values("region_total", ascending=False).copy()
        summary["revenue_formatted"] = summary["region_total"].apply(lambda x: f"{x:,.0f} zł")
        st.dataframe(
            summary[["region", "revenue_formatted"]].rename(
                columns={"region": "Województwo", "revenue_formatted": "Przychód"}),
            use_container_width=True,
            hide_index=True
        )
# ─────────────────────────────────────────────────────────────
# 11) Zakładki
# ─────────────────────────────────────────────────────────────
tabs = st.tabs(["🇵🇱 Allegro.pl (PLN)", "🇩🇪 eBay.de (EUR)", "🇩🇪 Kaufland.de (EUR)","🇵🇱 Polska — mapa wg województw"])

with tabs[0]:
    render_platform(
        platform_key="allegro",
        platform_title="🇵🇱 Allegro.pl — Analiza sprzedaży (PLN)",
        sql_query=SQL_WOW_ALLEGRO_PLN,
        sql_orders=SQL_ORDERS_ALLEGRO_PLN,
        currency_label="PLN",
        currency_symbol="zł",
    )

with tabs[1]:
    render_platform(
        platform_key="ebay",
        platform_title="🇩🇪 eBay.de — Analiza sprzedaży (EUR)",
        sql_query=SQL_WOW_EBAY_EUR,
        sql_orders=SQL_ORDERS_EBAY_EUR,
        currency_label="EUR",
        currency_symbol="€",
    )

with tabs[2]:
    render_platform(
        platform_key="kaufland",
        platform_title="🇩🇪 Kaufland.de — Analiza sprzedaży (EUR)",
        sql_query=SQL_WOW_KAUFLAND_EUR,
        sql_orders=SQL_ORDERS_KAUFLAND_EUR,
        currency_label="EUR",
        currency_symbol="€",
    )
with tabs[3]:
    render_poland_map(week_start)