import streamlit as st
import pandas as pd
import plotly.express as px
import requests

# -----------------------
# 1. Konfiguracja aplikacji
# -----------------------
st.set_page_config(page_title="E-commerce Dashboard", layout="wide")
st.title("📊 Dashboard sprzedaży - E-commerce")

# -----------------------
# 2. Dane logowania do Metabase
# -----------------------
METABASE_URL = "https://metabase.emamas.ideaerp.pl"
METABASE_USER = st.secrets["metabase_user"]  # login zapisany w Streamlit secrets
METABASE_PASSWORD = st.secrets["metabase_password"]  # hasło zapisane w Streamlit secrets

# -----------------------
# 3. Logowanie do Metabase
# -----------------------
def get_metabase_session():
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
def get_sales_data():
    try:
        card_id = 55  # <-- ID Twojej karty w Metabase (zmień na poprawny)
        url = f"{METABASE_URL}/api/card/{card_id}/query"
        response = requests.post(url, headers=headers, json={"parameters": []})
        response.raise_for_status()
        data = response.json()
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"❌ Błąd pobierania danych z Metabase: {e}")
        return pd.DataFrame()

df = get_sales_data()

# -----------------------
# 5. KPI
# -----------------------
if not df.empty:
    try:
        total_sales = df["sales"].sum()
        avg_sales = df["sales"].mean()
        top_product = df.groupby("product")["sales"].sum().idxmax()

        col1, col2, col3 = st.columns(3)
        col1.metric("💰 Łączna sprzedaż", f"{total_sales:,.0f} €")
        col2.metric("📈 Średnia sprzedaż", f"{avg_sales:,.0f} €")
        col3.metric("🏆 Top produkt", top_product)

        # -----------------------
        # 6. Wykresy
        # -----------------------
        st.subheader("📅 Sprzedaż w czasie")
        if "date" in df.columns:
            fig_time = px.line(df, x="date", y="sales", color="country", markers=True, title="Sprzedaż dzienna")
            st.plotly_chart(fig_time, use_container_width=True)

        st.subheader("🏆 Sprzedaż wg produktów")
        if "product" in df.columns:
            fig_products = px.bar(df.groupby("product")["sales"].sum().reset_index(), x="product", y="sales")
            st.plotly_chart(fig_products, use_container_width=True)

    except Exception as e:
        st.error(f"❌ Błąd przy generowaniu KPI: {e}")
else:
    st.warning("Brak danych do wyświetlenia 🚧")
