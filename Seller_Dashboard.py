import streamlit as st
import pandas as pd
import plotly.express as px
import requests
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────────────────────
# 1) Konfiguracja aplikacji
# ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="E-commerce Dashboard", layout="wide")
st.title("📊 Dashboard wydajności pakowania")

# ─────────────────────────────────────────────────────────────
# 2) Ustawienia Metabase
# ─────────────────────────────────────────────────────────────
METABASE_URL = "https://metabase.emamas.ideaerp.pl"
METABASE_DATABASE_ID = int(st.secrets.get("metabase_database_id", 2))
METABASE_USER = st.secrets["metabase_user"]
METABASE_PASSWORD = st.secrets["metabase_password"]

TZ = ZoneInfo("Europe/Warsaw")

# ─────────────────────────────────────────────────────────────
# 3) Logowanie do Metabase (cache ~50 min)
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
# 4) UI — wybór jednego dnia (00:00–24:00)
# ─────────────────────────────────────────────────────────────
st.sidebar.header("🔎 Filtry")
selected_day = st.sidebar.date_input("Dzień (00:00–24:00)", value=date.today() - timedelta(days=1))

# ─────────────────────────────────────────────────────────────
# 5) SQL: jeden dzień 00:00–24:00 (parametr {{day}})
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

# ─────────────────────────────────────────────────────────────
# 6) Pobranie danych dla jednego dnia (cache ~10 min)
# ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=600)
def query_packing_data_for_day(day_iso: str) -> pd.DataFrame:
    if not session_id:
        return pd.DataFrame()

    payload = {
        "database": METABASE_DATABASE_ID,
        "type": "native",
        "native": {
            "query": SQL_DAY_0000_24,
            "template-tags": {
                "day": {"name": "day", "display-name": "day", "type": "date"}
            },
        },
        "parameters": [
            {"type": "date", "target": ["variable", ["template-tag", "day"]], "value": day_iso},
        ],
    }

    try:
        r = requests.post(f"{METABASE_URL}/api/dataset", headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        j = r.json()

        # standardowy format Metabase data/rows/cols
        if isinstance(j, dict) and "data" in j and "rows" in j["data"]:
            cols = [c.get("name") for c in j["data"].get("cols", [])]
            rows = j["data"]["rows"]
            df = pd.DataFrame([[row[i] for i in range(len(cols))] for row in rows], columns=cols)
        else:
            # fallback: czasem zwraca listę słowników
            df = pd.DataFrame(j)

        if not df.empty:
            if "paczki_pracownika" in df.columns:
                df["paczki_pracownika"] = pd.to_numeric(df["paczki_pracownika"], errors="coerce").fillna(0).astype(int)
            if "packing_user_login" not in df.columns:
                st.error("❌ Brak kolumny 'packing_user_login' w zwróconych danych.")
                return pd.DataFrame()
        return df

    except requests.HTTPError as err:
        body = getattr(err, "response", None)
        body_txt = (body.text[:300] if body is not None and hasattr(body, "text") else "")
        st.error(f"❌ Błąd HTTP Metabase: {err} | {body_txt}")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"❌ Wystąpił błąd podczas pobierania danych: {e}")
        return pd.DataFrame()

df = query_packing_data_for_day(selected_day.isoformat())

# ─────────────────────────────────────────────────────────────
# 7) Prezentacja: opis zakresu, KPI i wykres
# ─────────────────────────────────────────────────────────────
st.header("Raport pakowania (00:00–24:00)")
start_ts = datetime.combine(selected_day, time(0, 0), tzinfo=TZ)
end_ts   = datetime.combine(selected_day + timedelta(days=1), time(0, 0), tzinfo=TZ)
st.caption(f"Zakres: **{start_ts.strftime('%Y-%m-%d %H:%M')}** → **{end_ts.strftime('%Y-%m-%d %H:%M')}** (Europe/Warsaw)")

if df.empty:
    st.warning("Brak danych w wybranym dniu. Zmień datę.")
else:
    try:
        total_packages = int(df["paczki_pracownika"].sum())
        avg_packages_per_user = float(df["paczki_pracownika"].mean()) if len(df) else 0.0

        # Najlepszy pakowacz
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
            st.dataframe(
                df_sorted.rename(columns={"packing_user_login": "Pracownik", "paczki_pracownika": "Spakowane paczki"}),
                use_container_width=True
            )

    except KeyError as e:
        st.error(f"❌ Brak oczekiwanej kolumny w danych: {e}")
    except Exception as e:
        st.error(f"❌ Błąd podczas generowania KPI/wykresu: {e}")
