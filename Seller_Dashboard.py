import streamlit as st
import pandas as pd
import plotly.express as px
import requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

# -----------------------
# 1) Konfiguracja aplikacji
# -----------------------
st.set_page_config(page_title="E-commerce Dashboard", layout="wide")
st.title("📊 Dashboard wydajności pakowania")

# -----------------------
# 2) Konfiguracja Metabase
# -----------------------
METABASE_URL = "https://metabase.emamas.ideaerp.pl"
METABASE_DATABASE_ID = int(st.secrets.get("metabase_database_id", 2))  # <- dostosuj jeśli trzeba
METABASE_USER = st.secrets["metabase_user"]
METABASE_PASSWORD = st.secrets["metabase_password"]

TZ = ZoneInfo("Europe/Warsaw")

# -----------------------
# 3) Logowanie do Metabase
# -----------------------
@st.cache_data(ttl=50*60)  # 50 minut
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

# -----------------------
# 4) UI: wybór zakresu i trybu doby
# -----------------------
st.sidebar.header("🔎 Filtry")
default_end = date.today() - timedelta(days=1)       # domyślnie: wczoraj
default_start = default_end - timedelta(days=7)      # ostatnie 7 dni do wczoraj

start_d = st.sidebar.date_input("Okres od (data)", value=default_start)
end_d = st.sidebar.date_input("Okres do (data)", value=default_end, min_value=start_d)

shifted_window = st.sidebar.checkbox("Doba magazynowa 18–18", value=True,
                                     help="Jeśli włączone: od 18:00 dnia 'od' do 18:00 dnia po 'do'. "
                                          "Jeśli wyłączone: od 00:00 dnia 'od' do 00:00 dnia po 'do'.")

# Wylicz ramy czasowe w PL
if shifted_window:
    start_ts_pl = datetime.combine(start_d, datetime.min.time()).replace(tzinfo=TZ) + timedelta(hours=18)
    end_ts_pl   = (datetime.combine(end_d, datetime.min.time()).replace(tzinfo=TZ) + timedelta(days=1, hours=18))
else:
    start_ts_pl = datetime.combine(start_d, datetime.min.time()).replace(tzinfo=TZ)
    end_ts_pl   = datetime.combine(end_d + timedelta(days=1), datetime.min.time()).replace(tzinfo=TZ)

# Metabase przyjmuje ISO8601; zostawiamy offset strefy (np. +02:00)
start_iso = start_ts_pl.isoformat()
end_iso = end_ts_pl.isoformat()

# -----------------------
# 5) Zapytanie: /api/dataset (native SQL + template-tags)
# -----------------------
# Wariant doby 18–18 (magazynowa)
SQL_1818 = """
SELECT 
    p.name AS packing_user_login,
    COUNT(s.name) AS paczki_pracownika
FROM sale_order s
JOIN res_users   u ON s.packing_user = u.id
JOIN res_partner p ON u.partner_id   = p.id
WHERE s.packing_user IS NOT NULL
  AND s.packing_date >= ({{start_d}}::date + INTERVAL '18 hours')
  AND s.packing_date <  ({{end_d}}::date   + INTERVAL '1 day' + INTERVAL '18 hours')
GROUP BY p.name
ORDER BY paczki_pracownika DESC;
"""

# Wariant doby 00–00 (kalendarzowa)
SQL_0000 = """
SELECT 
    p.name AS packing_user_login,
    COUNT(s.name) AS paczki_pracownika
FROM sale_order s
JOIN res_users   u ON s.packing_user = u.id
JOIN res_partner p ON u.partner_id   = p.id
WHERE s.packing_user IS NOT NULL
  AND s.packing_date >= ({{start_d}}::date)
  AND s.packing_date <  ({{end_d}}::date + INTERVAL '1 day')
GROUP BY p.name
ORDER BY paczki_pracownika DESC;
"""
@st.cache_data(ttl=600)
def query_packing_data_by_dates(start_date_str: str, end_date_str: str, use_1818: bool) -> pd.DataFrame:
    """
    Odpytuje Metabase /api/dataset z parametrami dat: start_d / end_d.
    Gdy use_1818=True – liczy 18→18, w przeciwnym wypadku 00→00.
    """
    if not session_id:
        return pd.DataFrame()

    sql = SQL_1818 if use_1818 else SQL_0000

    payload = {
        "database": METABASE_DATABASE_ID,
        "type": "native",
        "native": {
            "query": sql,
            "template-tags": {
                "start_d": {"name": "start_d", "display-name": "start_d", "type": "date"},
                "end_d":   {"name": "end_d",   "display-name": "end_d",   "type": "date"},
            },
        },
        "parameters": [
            {"type": "date", "target": ["variable", ["template-tag", "start_d"]], "value": start_date_str},
            {"type": "date", "target": ["variable", ["template-tag", "end_d"]],   "value": end_date_str},
        ],
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
        st.error(f"❌ Błąd HTTP Metabase: {err} | {(getattr(err, 'response', None) and err.response.text[:200])}")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"❌ Wystąpił błąd podczas pobierania danych: {e}")
        return pd.DataFrame()

df = query_packing_data_by_dates(start_d.isoformat(), end_d.isoformat(), use_1818=shifted_window)

# -----------------------
# 6) KPI + wykres
# -----------------------
st.header("Raport pakowania")
st.caption(f"Zakres: **{start_ts_pl.strftime('%Y-%m-%d %H:%M')}** → **{end_ts_pl.strftime('%Y-%m-%d %H:%M')}**  (Europe/Warsaw)")

if df.empty:
    st.warning("Brak danych w wybranym zakresie. Zmień daty lub tryb doby.")
else:
    try:
        total_packages = int(df["paczki_pracownika"].sum())
        avg_packages_per_user = float(df["paczki_pracownika"].mean()) if len(df) else 0.0

        # Najlepszy pakowacz
        idx = df["paczki_pracownika"].idxmax()
        top_packer = df.loc[idx, "packing_user_login"]
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
            x="paczki_pracownika",
            y="packing_user_login",
            color="próg 300",
            color_discrete_map={"≥ 300": "firebrick", "< 300": "cornflowerblue"},
            title="Liczba paczek spakowanych przez pracownika",
            labels={"packing_user_login": "Pracownik", "paczki_pracownika": "Liczba paczek"},
            orientation="h",
            height=600
        )
        fig.add_vline(x=300, line_width=2, line_dash="dash", line_color="darkgray")
        fig.update_layout(yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig, use_container_width=True)

        with st.expander("Podgląd danych"):
            st.dataframe(df_sorted.rename(columns={
                "packing_user_login": "Pracownik",
                "paczki_pracownika": "Spakowane paczki"
            }), use_container_width=True)

    except KeyError as e:
        st.error(f"❌ Brak oczekiwanej kolumny w danych: {e}")
    except Exception as e:
        st.error(f"❌ Błąd podczas generowania KPI/wykresu: {e}")
