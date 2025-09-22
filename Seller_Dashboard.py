import streamlit as st
import pandas as pd
import plotly.express as px
import requests
from datetime import date, timedelta

# -----------------------
# 1. Konfiguracja aplikacji
# -----------------------
st.set_page_config(page_title="E-commerce Dashboard", layout="wide")
st.title("📊 Dashboard wydajności pakowania")

# -----------------------
# 2. Dane logowania do Metabase
# -----------------------
METABASE_URL = "https://metabase.emamas.ideaerp.pl"
# Pamiętaj, że dane logowania muszą być w pliku .streamlit/secrets.toml
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
# 4. Sekcja wyboru daty w interfejsie użytkownika
# -----------------------
st.sidebar.header("Opcje raportu")
selected_date = st.sidebar.date_input("Wybierz datę", value=date.today() - timedelta(days=1))


# -----------------------
# 5. Pobieranie danych z karty Metabase
# -----------------------
@st.cache_data(ttl=600)
def get_packing_data(selected_date_str):
    """
    Funkcja pobiera dane o pakowaniu z karty Metabase,
    przekazując datę jako parametr zapytania.
    """
    try:
        card_id = 55
        url = f"{METABASE_URL}/api/card/{card_id}/query"

        # Konwersja daty na string w formacie YYYY-MM-DD
        parameters = [{"type": "date/single", "value": selected_date_str, "name": "selected_date"}]

        response = requests.post(url, headers=headers, json={"parameters": parameters})
        response.raise_for_status()
        data = response.json()

        # Tworzenie DataFrame z danych JSON, jak na obrazku
        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data, columns=["packing_user_login", "paczki_pracownika"])
        # Konwersja liczby paczek na typ numeryczny
        df['paczki_pracownika'] = pd.to_numeric(df['paczki_pracownika'])
        return df
    except Exception as e:
        st.error(f"❌ Błąd pobierania danych z Metabase: {e}")
        return pd.DataFrame()


# Konwersja obiektu date na string w wymaganym formacie
selected_date_str = selected_date.strftime('%Y-%m-%d')
df = get_packing_data(selected_date_str)

# -----------------------
# 6. Prezentacja danych (KPI i Wykresy)
# -----------------------
st.header(f"Raport z dnia: {selected_date.strftime('%d-%m-%Y')}")

if not df.empty:
    try:
        total_packages = df["paczki_pracownika"].sum()
        avg_packages_per_user = df["paczki_pracownika"].mean()
        # Najlepszy pakowacz to pierwszy wiersz, jeśli karta sortuje malejąco
        top_packer = df.iloc[0]["packing_user_login"]

        col1, col2, col3 = st.columns(3)
        col1.metric("📦 Łączna liczba paczek", f"{total_packages:,.0f}")
        col2.metric("🧑‍💼 Średnia paczek na pracownika", f"{avg_packages_per_user:,.0f}")
        col3.metric("🏆 Najlepszy pakowacz", top_packer)

        st.subheader("📦 Ranking wydajności pakowania")
        # Sortowanie dla wykresu, aby upewnić się, że jest poprawne
        df_sorted = df.sort_values(by="paczki_pracownika", ascending=True)

        fig_packing = px.bar(
            df_sorted,
            x="paczki_pracownika",
            y="packing_user_login",
            title="Liczba paczek spakowanych przez pracownika",
            labels={"packing_user_login": "Login pracownika", "paczki_pracownika": "Liczba paczek"},
            orientation='h'
        )
        # Zwiększenie czytelności osi Y
        fig_packing.update_layout(yaxis={'categoryorder': 'total ascending'})
        st.plotly_chart(fig_packing, use_container_width=True)

    except KeyError as e:
        st.error(
            f"❌ Błąd: Upewnij się, że kolumny 'packing_user_login' i 'paczki_pracownika' istnieją w danych z Metabase. Błąd kolumny: {e}")
    except IndexError:
        st.warning("Brak danych w DataFrame dla wybranej daty.")
    except Exception as e:
        st.error(f"❌ Wystąpił błąd przy generowaniu wskaźników lub wykresów: {e}")
else:
    st.warning(
        "Brak danych do wyświetlenia 🚧. Upewnij się, że karta Metabase jest poprawnie skonfigurowana i dostępne są dane dla wybranej daty.")