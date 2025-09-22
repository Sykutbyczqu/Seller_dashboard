import streamlit as st
import pandas as pd
import plotly.express as px
import requests

# -----------------------
# 1. Konfiguracja aplikacji
# -----------------------
st.set_page_config(page_title="E-commerce Dashboard", layout="wide")
st.title("📊 Dashboard wydajności pakowania - E-commerce")

# -----------------------
# 2. Dane logowania do Metabase
# -----------------------
# Dane logowania są bezpiecznie przechowywane w Streamlit Secrets
METABASE_URL = "https://metabase.emamas.ideaerp.pl"
METABASE_USER = st.secrets["metabase_user"]
METABASE_PASSWORD = st.secrets["metabase_password"]


# -----------------------
# 3. Logowanie do Metabase
# -----------------------
def get_metabase_session():
    """Funkcja loguje się do Metabase i zwraca ID sesji."""
    try:
        login_payload = {"username": METABASE_USER, "password": METABASE_PASSWORD}
        response = requests.post(f"{METABASE_URL}/api/session", json=login_payload)
        response.raise_for_status()
        return response.json()["id"]
    except Exception as e:
        st.error(f"❌ Błąd logowania do Metabase: {e}")
        return None


session_id = get_metabase_session()
headers = {"X-Metabase-Session": session_id} if session_id else {}


# -----------------------
# 4. Pobieranie danych z karty Metabase
# -----------------------
@st.cache_data(ttl=600)  # cache na 10 minut
def get_packing_data():
    """Funkcja pobiera dane o pakowaniu z karty Metabase."""
    try:
        # PAMIĘTAJ: Zmień CARD_ID na poprawny identyfikator karty z Metabase
        # zawierającej dane widoczne na obrazku
        card_id = 55
        url = f"{METABASE_URL}/api/card/{card_id}/query"
        response = requests.post(url, headers=headers, json={"parameters": []})
        response.raise_for_status()
        data = response.json()

        # Tworzenie DataFrame z kolumnami na podstawie obrazka
        df = pd.DataFrame(data, columns=["packing_user_login", "paczki_pracownika"])
        return df
    except Exception as e:
        st.error(f"❌ Błąd pobierania danych z Metabase: {e}")
        return pd.DataFrame()


df = get_packing_data()

# -----------------------
# 5. KPI - Wskaźniki wydajności pakowania
# -----------------------
if not df.empty:
    try:
        # Obliczenia KPI
        total_packages = df["paczki_pracownika"].sum()
        avg_packages_per_user = df["paczki_pracownika"].mean()
        # Najlepszy pakowacz to pierwszy wiersz, jeśli karta sortuje malejąco
        top_packer = df.iloc[0]["packing_user_login"]

        col1, col2, col3 = st.columns(3)
        col1.metric("📦 Łączna liczba paczek", f"{total_packages:,.0f}")
        col2.metric("🧑‍💼 Średnia paczek na pracownika", f"{avg_packages_per_user:,.0f}")
        col3.metric("🏆 Najlepszy pakowacz", top_packer)

        # -----------------------
        # 6. Wykresy
        # -----------------------
        st.subheader("📦 Ranking wydajności pakowania")
        fig_packing = px.bar(
            df,
            x="packing_user_login",
            y="paczki_pracownika",
            title="Liczba paczek spakowanych przez pracownika",
            labels={"packing_user_login": "Login pracownika", "paczki_pracownika": "Liczba paczek"}
        )
        st.plotly_chart(fig_packing, use_container_width=True)

    except KeyError as e:
        st.error(
            f"❌ Błąd: Upewnij się, że kolumny 'packing_user_login' i 'paczki_pracownika' istnieją w danych z Metabase. Błąd kolumny: {e}")
    except IndexError:
        st.warning("Brak danych w DataFrame, nie można ustalić najlepszego pakowacza.")
    except Exception as e:
        st.error(f"❌ Wystąpił błąd przy generowaniu wskaźników lub wykresów: {e}")
else:
    st.warning("Brak danych do wyświetlenia 🚧. Sprawdź, czy karta Metabase jest poprawnie skonfigurowana.")