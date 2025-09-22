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
# ---- ZMIANY DLA WYBORU DATY ----
# -----------------------
st.sidebar.header("Opcje raportu")
selected_date = st.sidebar.date_input("Wybierz datę", value=date.today() - timedelta(days=1))
selected_date_str = selected_date.strftime('%Y-%m-%d')


# -----------------------
# 5. Pobieranie danych z karty Metabase (ID 55)
# ---- ZMIANY DLA WYBORU DATY ----
# -----------------------
@st.cache_data(ttl=600)
def get_packing_data(date_param):
    """
    Funkcja pobiera dane o pakowaniu, wysyłając parametr daty do karty Metabase.
    """
    try:
        card_id = 55
        url = f"{METABASE_URL}/api/card/{card_id}/query/json"

        # Ładunek JSON z parametrem daty
        payload = {
            "parameters": [
                {"type": "date/single", "value": date_param, "name": "selected_date"}
            ]
        }

        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)

        if 'paczki_pracownika' not in df.columns or 'packing_user_login' not in df.columns:
            st.error("❌ Błąd: Dane z Metabase nie zawierają oczekiwanych kolumn.")
            return pd.DataFrame()

        df['paczki_pracownika'] = pd.to_numeric(df['paczki_pracownika'])

        return df
    except requests.exceptions.HTTPError as err:
        st.error(f"❌ Błąd HTTP: {err}. Sprawdź, czy URL Metabase i dane logowania są poprawne.")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"❌ Wystąpił nieoczekiwany błąd: {e}")
        return pd.DataFrame()


df = get_packing_data(selected_date_str)

# -----------------------
# 6. Prezentacja danych (KPI i Wykresy)
# ---- ZMIANY DLA WYBORU DATY ----
# -----------------------
st.header(f"Raport z dnia: {selected_date.strftime('%d-%m-%Y')}")

if not df.empty:
    try:
        total_packages = df["paczki_pracownika"].sum()
        avg_packages_per_user = df["paczki_pracownika"].mean()
        top_packer = df.iloc[0]["packing_user_login"]

        col1, col2, col3 = st.columns(3)
        col1.metric("📦 Łączna liczba paczek:", f"{total_packages:,.0f}")
        col2.metric("🧑‍💼 Średnia paczek na pracownika", f"{avg_packages_per_user:,.0f}")
        col3.metric("🏆 Najlepszy pakowacz", top_packer)

        st.subheader("📦 Ranking wydajności pakowania")
        df_sorted = df.sort_values(by="paczki_pracownika", ascending=True)

        fig_packing = px.bar(
            df_sorted,
            x="paczki_pracownika",
            y="packing_user_login",
            title="Liczba paczek spakowanych przez pracownika",
            labels={"packing_user_login": "Login pracownika", "paczki_pracownika": "Liczba paczek"},
            orientation='h'
        )
        fig_packing.update_layout(yaxis={'categoryorder': 'total ascending'})
        st.plotly_chart(fig_packing, use_container_width=True)

    except KeyError as e:
        st.error(
            f"❌ Błąd: Upewnij się, że kolumny 'packing_user_login' i 'paczki_pracownika' istnieją w danych. Błąd kolumny: {e}")
    except IndexError:
        st.warning("Brak danych w DataFrame.")
    except Exception as e:
        st.error(f"❌ Wystąpił błąd przy generowaniu wskaźników lub wykresów: {e}")
else:
    st.warning(
        f"Brak danych do wyświetlenia dla dnia {selected_date_str} 🚧. Upewnij się, że karta Metabase jest poprawnie skonfigurowana.")