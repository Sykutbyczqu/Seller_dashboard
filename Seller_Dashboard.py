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
# 2. Pobieranie danych (przykładowe)
# -----------------------
# W prawdziwej wersji tutaj robisz zapytanie do Metabase API
# np. response = requests.post(METABASE_URL + "/api/card/55/query", headers=headers, json={"parameters": []})
# data = response.json()

data = pd.DataFrame({
    "date": pd.date_range(start="2025-01-01", periods=10, freq="D"),
    "sales": [100, 120, 90, 150, 200, 180, 220, 210, 250, 230],
    "country": ["DE", "DE", "PL", "PL", "DE", "ES", "ES", "PL", "DE", "ES"],
    "product": ["A", "B", "A", "C", "D", "B", "C", "A", "E", "D"]
})

# -----------------------
# 3. Filtry boczne
# -----------------------
country_filter = st.sidebar.multiselect("Wybierz kraj", options=data["country"].unique(), default=data["country"].unique())
product_filter = st.sidebar.multiselect("Wybierz produkt", options=data["product"].unique(), default=data["product"].unique())

filtered_data = data[(data["country"].isin(country_filter)) & (data["product"].isin(product_filter))]

# -----------------------
# 4. KPI
# -----------------------
total_sales = filtered_data["sales"].sum()
avg_sales = filtered_data["sales"].mean()
top_product = filtered_data.groupby("product")["sales"].sum().idxmax()

col1, col2, col3 = st.columns(3)
col1.metric("💰 Łączna sprzedaż", f"{total_sales:,.0f} €")
col2.metric("📈 Średnia sprzedaż dziennie", f"{avg_sales:,.0f} €")
col3.metric("🏆 Top produkt", top_product)

# -----------------------
# 5. Wykresy
# -----------------------
st.subheader("📅 Sprzedaż w czasie")
fig_time = px.line(filtered_data, x="date", y="sales", color="country", markers=True, title="Sprzedaż dzienna")
st.plotly_chart(fig_time, use_container_width=True)

st.subheader("🏆 Sprzedaż wg produktów")
fig_products = px.bar(filtered_data.groupby("product")["sales"].sum().reset_index(), x="product", y="sales", title="Łączna sprzedaż wg produktów")
st.plotly_chart(fig_products, use_container_width=True)

st.subheader("🌍 Sprzedaż wg krajów")
fig_countries = px.pie(filtered_data, names="country", values="sales", title="Udział sprzedaży wg krajów")
st.plotly_chart(fig_countries, use_container_width=True)

# -----------------------
# 6. Alerty (prosta logika)
# -----------------------
st.subheader("🔔 Alerty sprzedażowe")
alerts = []

# Przykład alertu: spadek sprzedaży
if avg_sales < 120:
    alerts.append("⚠️ Średnia sprzedaż spadła poniżej 120 €!")

# Top produkt alert
if top_product == "A":
    alerts.append("🔥 Produkt A nadal jest bestsellerem!")

if alerts:
    for alert in alerts:
        st.warning(alert)
else:
    st.success("Brak alertów - wszystko stabilnie 🚀")
