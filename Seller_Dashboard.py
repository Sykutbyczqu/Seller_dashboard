import streamlit as st
import pandas as pd
import plotly.express as px
import requests
from datetime import date, timedelta
from typing import List, Tuple, Dict, Optional

# -----------------------
# 1. Konfiguracja aplikacji
# -----------------------
st.set_page_config(page_title="E-commerce Dashboard", layout="wide")
st.title("📊 Dashboard wydajności pakowania")

# -----------------------
# 2. Dane logowania do Metabase
# -----------------------
METABASE_URL = "https://metabase.emamas.ideaerp.pl"
METABASE_USER = st.secrets.get("metabase_user")
METABASE_PASSWORD = st.secrets.get("metabase_password")

if not METABASE_USER or not METABASE_PASSWORD:
    st.error("❌ Brak danych logowania w streamlit.secrets. Uzupełnij metabase_user i metabase_password.")
    st.stop()

# -----------------------
# 3. Logowanie do Metabase
# -----------------------
def get_metabase_session() -> Optional[str]:
    """Loguje do Metabase i zwraca ID sesji."""
    try:
        login_payload = {"username": METABASE_USER, "password": METABASE_PASSWORD}
        response = requests.post(f"{METABASE_URL}/api/session", json=login_payload, timeout=30)
        response.raise_for_status()
        return response.json().get("id")
    except Exception as e:
        st.error(f"❌ Błąd logowania do Metabase: {e}")
        return None

session_id = get_metabase_session()
if not session_id:
    st.stop()
headers = {"X-Metabase-Session": session_id}

# -----------------------
# 4. Sekcja wyboru daty w interfejsie użytkownika
# -----------------------
st.sidebar.header("Opcje raportu")
selected_date = st.sidebar.date_input("Wybierz datę", value=date.today() - timedelta(days=1))
selected_date_str = selected_date.strftime('%Y-%m-%d')
show_debug = st.sidebar.toggle("Pokaż diagnostykę", value=False)

# -----------------------
# 5. Funkcje pomocnicze
# -----------------------
def _try_query_card(card_id: int, params: List[dict]) -> List[dict]:
    """Wysyła zapytanie do karty z podanymi parametrami i zwraca listę rekordów JSON (lub pustą listę)."""
    url = f"{METABASE_URL}/api/card/{card_id}/query/json"
    try:
        response = requests.post(url, headers=headers, json={"parameters": params}, timeout=60)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        # Zwracamy pustą listę; obsługa błędu w miejscu wywołania.
        if show_debug:
            st.warning(f"Zapytanie nie powiodło się dla parametrów {params}: {e}")
        return []


def _pick_columns(df: pd.DataFrame) -> Tuple[Optional[str], Optional[str], Dict[str, str]]:
    """Wybiera kolumny: użytkownika (login) i liczby paczek. Zwraca (user_col, count_col, info)."""
    info: Dict[str, str] = {}
    cols = list(df.columns)
    lower_map = {c.lower(): c for c in cols}

    user_candidates = [
        "packing_user_login", "user_login", "login", "user", "pracownik",
        "pracownik_login", "login_pracownika", "packer", "packing_user"
    ]
    count_candidates = [
        "paczki_pracownika", "liczba_paczek", "paczki", "ilosc", "quantity",
        "count", "cnt", "total", "sum"
    ]

    user_col = None
    for key in user_candidates:
        if key in lower_map:
            user_col = lower_map[key]
            break

    count_col = None
    for key in count_candidates:
        if key in lower_map:
            count_col = lower_map[key]
            break

    # Fallbacki, jeśli nie znaleziono po nazwie
    if user_col is None:
        # Pierwsza kolumna typu object/category
        object_cols = [c for c in cols if df[c].dtype == 'object' or str(df[c].dtype).startswith('category')]
        if object_cols:
            user_col = object_cols[0]

    if count_col is None:
        # Najbardziej sensowna kolumna numeryczna (największa suma)
        num_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
        if num_cols:
            sums = {c: pd.to_numeric(df[c], errors='coerce').sum(skipna=True) for c in num_cols}
            count_col = max(sums, key=sums.get)

    info['mapped_user_col'] = user_col or ''
    info['mapped_count_col'] = count_col or ''
    info['all_columns'] = ", ".join(cols)

    return user_col, count_col, info


# -----------------------
# 6. Pobieranie danych z karty Metabase (ID 55) z odpornością na zmiany nazw parametrów/kolumn
# -----------------------
@st.cache_data(ttl=600, show_spinner=False)
def get_packing_data(date_param: str) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """
    Pobiera dane pakowania z karty Metabase, próbując różne nazwy i typy parametru daty.
    Zwraca (DataFrame, info_diagnostics).
    """
    card_id = 55

    # Kolejność prób wysłania parametru daty
    parameter_trials = [
        [{"type": "date", "value": date_param, "name": "selected_date"}],
        [{"type": "date/single", "value": date_param, "name": "selected_date"}],
        [{"type": "date", "value": date_param, "name": "date"}],
        [{"type": "date/single", "value": date_param, "name": "date"}],
        # Ostateczny fallback: bez parametrów (gdy karta nie wymaga parametru)
        []
    ]

    data: List[dict] = []
    used_params: List[dict] = []
    for params in parameter_trials:
        data = _try_query_card(card_id, params)
        if data:
            used_params = params
            break

    if not data:
        return pd.DataFrame(), {
            "error": "Brak danych z Metabase (pusta odpowiedź)",
            "used_params": str(used_params)
        }

    df = pd.DataFrame(data)

    # Wybór kolumn i ewentualne mapowanie
    user_col, count_col, info = _pick_columns(df)
    info['used_params'] = str(used_params)

    if not user_col or not count_col:
        return pd.DataFrame(), {
            "error": "Nie można odnaleźć kolumn użytkownika lub liczby paczek w odpowiedzi Metabase.",
            **info
        }

    # Konwersja liczby paczek
    df[count_col] = pd.to_numeric(df[count_col], errors='coerce')

    # Zwracamy tylko potrzebne kolumny pod ustalonymi nazwami
    result = df[[user_col, count_col]].rename(columns={user_col: 'packing_user_login', count_col: 'paczki_pracownika'})

    return result, info


df, info = get_packing_data(selected_date_str)

# -----------------------
# 7. Prezentacja danych (KPI i Wykresy)
# -----------------------
st.header(f"Raport z dnia: {selected_date.strftime('%d-%m-%Y')}")

if show_debug:
    with st.expander("Diagnostyka danych"):
        for k, v in info.items():
            st.write(f"{k}: {v}")
        if not df.empty:
            st.dataframe(df.head())

if not df.empty:
    try:
        total_packages = pd.to_numeric(df["paczki_pracownika"], errors='coerce').sum()
        avg_packages_per_user = pd.to_numeric(df["paczki_pracownika"], errors='coerce').mean()
        # Najlepszy pakowacz po liczbie paczek
        top_row = df.sort_values(by="paczki_pracownika", ascending=False).iloc[0]
        top_packer = str(top_row["packing_user_login"]) if pd.notna(top_row["packing_user_login"]) else "-"

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
        st.warning("Brak danych w DataFrame.")
    except Exception as e:
        st.error(f"❌ Wystąpił błąd przy generowaniu wskaźników lub wykresów: {e}")
else:
    # Precyzyjniejszy komunikat diagnostyczny
    if 'error' in info:
        st.error(f"❌ {info.get('error')}\nSzczegóły: {info}")
    else:
        st.warning(
            f"Brak danych do wyświetlenia dla dnia {selected_date_str} 🚧. Upewnij się, że karta Metabase (ID 55) jest poprawnie skonfigurowana.")
