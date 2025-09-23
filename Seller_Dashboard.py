# streamlit_app.py
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st

# ─────────────────────────────────────────────────────────────
# 1) Konfiguracja aplikacji
# ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="Sprzedaż: WoW TOP 10 (PLN)", layout="wide")
st.title("🛒 Sprzedaż — Tydzień do tygodnia (TOP 10 SKU, PLN)")

# ─────────────────────────────────────────────────────────────
# 2) Ustawienia Metabase
# ─────────────────────────────────────────────────────────────
METABASE_URL = "https://metabase.emamas.ideaerp.pl"
METABASE_DATABASE_ID = int(st.secrets.get("metabase_database_id", 2))
METABASE_USER = st.secrets["metabase_user"]
METABASE_PASSWORD = st.secrets["metabase_password"]
TZ = ZoneInfo("Europe/Warsaw")

# ─────────────────────────────────────────────────────────────
# 3) SQL — tylko PLN (filtr po l.currency_id → res_currency.name = 'PLN')
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
    COALESCE(pp.default_code, l.product_id::text) AS sku,   -- SKU
    COALESCE(pt.name, l.name) AS product_name,               -- nazwa
    COALESCE(l.product_uom_qty, 0) AS qty,                   -- ilość
    COALESCE(l.price_total, l.price_subtotal,
             l.price_unit * COALESCE(l.product_uom_qty,0), 0) AS line_total_pln,
    COALESCE(s.confirm_date, s.date_order, s.create_date) AS order_ts
  FROM sale_order_line l
  JOIN sale_order s           ON s.id = l.order_id
  JOIN res_currency cur       ON cur.id = l.currency_id
  LEFT JOIN product_product  pp ON pp.id = l.product_id
  LEFT JOIN product_template pt ON pt.id = pp.product_tmpl_id
  WHERE s.state IN ('sale','done')
    AND cur.name = 'PLN'                 -- tylko PLN
    AND s.name ILIKE '%Allegro%'         -- tylko zamówienia z numerem zawierającym "Allegro"
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
# 4) Sesja Metabase (cache ~50 min)
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
# 5) Wywołanie /api/dataset z obsługą 202 (polling) i 401
# ─────────────────────────────────────────────────────────────
def _dataset_call(sql_text: str, params: dict, session: str, poll_max_s: float = 12.0) -> dict:
    """POST /api/dataset; obsługa 200/202 (+ polling), zwraca dict {status,json,text}."""
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
        # (a) już są dane:
        if isinstance(j, dict) and isinstance(j.get("data", {}).get("rows"), list):
            return {"status": 200, "json": j, "text": r.text}
        # (b) polling po tokenie:
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
        # 202 bez tokena:
        return {"status": 202, "json": None, "text": r.text}

    return {"status": r.status_code, "json": (r.json() if r.content else None), "text": r.text}

# ─────────────────────────────────────────────────────────────
# 6) Parser JSON Metabase → DataFrame (odporny na brak data.cols)
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
# 7) Publiczna funkcja pobrania danych (auto-refresh sesji, cache)
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
# 8) UI: wybór tygodnia, próg alertu, panel debug
# ─────────────────────────────────────────────────────────────
def last_completed_week_start(today: date | None = None) -> date:
    """Poniedziałek ostatniego zakończonego tygodnia (Mon)."""
    d = today or datetime.now(TZ).date()
    offset = d.weekday() + 7  # od poniedziałku wstecz o pełny tydzień
    return d - timedelta(days=offset)

st.sidebar.header("🔎 Filtry")
default_week = last_completed_week_start()
pick_day = st.sidebar.date_input("Wybierz tydzień (podaj dowolny dzień z tego tygodnia)", value=default_week)
week_start = pick_day - timedelta(days=pick_day.weekday())
week_end = week_start + timedelta(days=7)
threshold = st.sidebar.slider("Próg alertu (±%)", min_value=5, max_value=80, value=20, step=5)
debug_api = st.sidebar.toggle("Debug API", value=False)

st.caption(f"Tydzień: **{week_start} → {week_end - timedelta(days=1)}**  •  Strefa: Europe/Warsaw")

# ─────────────────────────────────────────────────────────────
# 9) Pobranie danych i prezentacja (z polskimi nazwami kolumn)
# ─────────────────────────────────────────────────────────────
df = query_wow_top10(SQL_WOW_TOP10, week_start.isoformat())

if debug_api:
    st.write(f"Metabase HTTP: {st.session_state.get('mb_last_status')}")
    st.subheader("Raw JSON (Metabase)")
    st.json(st.session_state.get("mb_last_json"))

if df.empty:
    st.warning("Brak danych dla wybranego tygodnia (PLN). Zmień tydzień lub sprawdź źródło.")
    st.stop()

# Walidacja kluczowych kolumn
need = {"sku","product_name","curr_rev","prev_rev","curr_qty","prev_qty","rev_change_pct","qty_change_pct"}
missing = [c for c in need if c not in df.columns]
if missing:
    st.error(f"Brak kolumn w danych: {missing}")
    st.dataframe(df.head(), use_container_width=True)
    st.stop()

# Ranking TOP 10
df_top = df.sort_values("curr_rev", ascending=False).head(10).copy()

def classify_change(pct: float | np.floating | None) -> str:
    if pd.isna(pct): return "n/d"
    if pct >= threshold: return f"↑ ≥{threshold}%"
    if pct <= -threshold: return f"↓ ≤-{threshold}%"
    return "≈"

df_top["status"] = df_top["rev_change_pct"].apply(classify_change)

# KPI całościowe
sum_curr = float(df["curr_rev"].sum() or 0)
sum_prev = float(df["prev_rev"].sum() or 0)
delta_abs = sum_curr - sum_prev
delta_pct = (delta_abs / sum_prev * 100) if sum_prev else 0.0

c1, c2, c3 = st.columns(3)
c1.metric("Suma sprzedaży (PLN, tydzień)", f"{sum_curr:,.0f} zł".replace(",", " "))
c2.metric("Zmiana vs poprzedni (PLN)", f"{delta_abs:,.0f} zł".replace(",", " "))
c3.metric("Zmiana % całości", f"{delta_pct:+.0f}%")

# Wykres TOP10 — etykiety PL
st.subheader("TOP 10 — Sprzedaż tygodnia (PLN)")
fig = px.bar(
    df_top, x="curr_rev", y="sku", color="status",
    labels={"curr_rev": "Sprzedaż tygodnia (PLN)", "sku": "SKU", "status": "Status zmiany"},
    orientation="h", height=600
)
fig.update_layout(yaxis={"categoryorder": "total ascending"})
st.plotly_chart(fig, use_container_width=True)

# Słownik faktycznych nazw do tabel
COLS_DISPLAY = {
    "sku": "SKU",
    "product_name": "Produkt",
    "curr_rev": "Sprzedaż tygodnia (PLN)",
    "prev_rev": "Sprzedaż poprzedniego tygodnia (PLN)",
    "rev_change_pct": "Zmiana sprzedaży %",
    "curr_qty": "Ilość tygodnia (szt.)",
    "prev_qty": "Ilość poprzedniego tygodnia (szt.)",
    "qty_change_pct": "Zmiana ilości %",
    "status": "Status zmiany",
}

ORDER_DISPLAY = [
    "SKU",
    "Produkt",
    "Sprzedaż tygodnia (PLN)",
    "Sprzedaż poprzedniego tygodnia (PLN)",
    "Zmiana sprzedaży %",
    "Ilość tygodnia (szt.)",
    "Ilość poprzedniego tygodnia (szt.)",
    "Zmiana ilości %",
    "Status zmiany",
]

def to_display(df_in: pd.DataFrame) -> pd.DataFrame:
    out = df_in.rename(columns=COLS_DISPLAY)
    # zachowaj tylko kolumny zdefiniowane do wyświetlenia, jeśli istnieją
    keep = [c for c in ORDER_DISPLAY if c in out.columns]
    return out[keep]

# Wzrosty / Spadki z faktycznymi nazwami
ups = df_top[df_top["rev_change_pct"] >= threshold].copy()
downs = df_top[df_top["rev_change_pct"] <= -threshold].copy()

colA, colB = st.columns(2)
with colA:
    st.markdown("### 🚀 Wzrosty (≥ próg)")
    if ups.empty:
        st.info("Brak pozycji przekraczających próg wzrostu.")
    else:
        st.dataframe(
            to_display(ups),
            use_container_width=True
        )
with colB:
    st.markdown("### 📉 Spadki (≤ -próg)")
    if downs.empty:
        st.info("Brak pozycji przekraczających próg spadku.")
    else:
        st.dataframe(
            to_display(downs),
            use_container_width=True
        )

# Podgląd TOP10 (z faktycznymi nazwami)
with st.expander("🔎 Podgląd TOP 10 (tabela)"):
    st.dataframe(to_display(df_top), use_container_width=True)
