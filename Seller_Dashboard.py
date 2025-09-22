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
# -----------------------
st.sidebar.header("Opcje raportu")
selected_date = st.sidebar.date_input("Wybierz datę", value=date.today() - timedelta(days=1))


# -----------------------
# 5. Pobieranie danych bezpośrednio z zapytania SQL
# -----------------------
@st.cache_data(ttl=600)
def get_packing_data(selected_date_str):
    """
    Funkcja pobiera dane o pakowaniu, wysyłając zapytanie SQL bezpośrednio do API Metabase.
    """
    try:
        url = f"{METABASE_URL}/api/dataset"

        # Konwersja wybranej daty na string w formacie SQL
        end_date = date.fromisoformat(selected_date_str) + timedelta(days=1)
        end_date_str = end_date.strftime('%Y-%m-%d')

        # Dynamiczne tworzenie zapytania SQL z wstawionymi datami
        sql_query = f"""
        SELECT
            u.login AS packing_user_login,
            COUNT(s.name) AS paczki_pracownika
        FROM
            sale_order s
            JOIN res_users u ON s.packing_user = u.id
        WHERE
            s.packing_user IS NOT NULL
            AND s.packing_date >= '{selected_date_str}'
            AND s.packing_date < '{end_date_str}'
        GROUP BY
            u.login
        ORDER BY
            paczki_pracownika DESC
        """

        payload = {
            # ZMIEŃ 1 NA ID TWOJEJ BAZY DANYCH W METABASE!
            # Możesz je znaleźć w URL Metabase, przechodząc do "Admin > Databases > (Twoja Baza)".
            # URL będzie wyglądał tak: https://metabase.emamas.ideaerp.pl/admin/databases/1
            "database": 1,
            "type": "native",
            "native": {
                "query": sql_query
            }
        }

        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        # Poprawne odczytanie danych z odpowiedzi API
        if 'data' not in data or 'results_metadata' not in data['data'] or 'rows' not in data['data']:
            return pd.DataFrame()

        columns = [col['name'] for col in data['data']['results_metadata']['columns']]
        rows = data['data']['rows']

        df = pd.DataFrame(rows, columns=columns)
        df['paczki_pracownika'] = pd.to_numeric(df['paczki_pracownika'])

        return df
    except requests.exceptions.HTTPError as err:
        st.error(f"❌ Błąd HTTP: {err}. Sprawdź, czy URL, ID bazy danych i dane logowania są poprawne.")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"❌ Błąd pobierania danych: {e}")
        return pd.DataFrame()


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
        top_packer = df.iloc[0]["packing_user_login"]

        col1, col2, col3 = st.columns(3)
        col1.metric("📦 Łączna liczba paczek", f"{total_packages:,.0f}")
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
        st.warning("Brak danych w DataFrame dla wybranej daty.")
    except Exception as e:
        st.error(f"❌ Wystąpił błąd przy generowaniu wskaźników lub wykresów: {e}")
else:
    st.warning("Brak danych do wyświetlenia 🚧. Upewnij się, że dane są dostępne dla wybranej daty.")