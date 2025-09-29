# Trend: tygodniowy vs dzienny
st.subheader("📈 Trendy — wybierz granularność i SKU")
granularity = st.radio(
    f"Agregacja trendu - {platform_name}",
    ["tydzień", "dzień"],
    index=0,
    horizontal=True,
    key=f"gran_{platform_key}"
)

# Zakresy dla zapytań trendu
date_from = week_start - timedelta(weeks=weeks_back - 1)
date_to = week_end  # półotwarty [from, to)

if granularity == "tydzień" and sql_query_trend_week:
    df_trend = query_platform_trend_week(sql_query_trend_week, week_start, weeks=weeks_back, platform_key=platform_key)
    time_col = "week_start"
    # Pełna oś: każdy poniedziałek w zakresie
    full_index = pd.date_range(
        start=week_start - timedelta(weeks=weeks_back - 1),
        end=week_start,
        freq="W-MON"
    )
elif granularity == "dzień" and sql_query_trend_day:
    df_trend = query_platform_trend_day(sql_query_trend_day, date_from=date_from, date_to=date_to,
                                        platform_key=platform_key)
    time_col = "day"
    # Pełna oś: każdy dzień w zakresie
    full_index = pd.date_range(start=date_from, end=date_to - timedelta(days=1), freq="D")
else:
    df_trend = pd.DataFrame()
    time_col = None
    full_index = None

if df_trend.empty or not time_col:
    st.info("Brak danych trendu dla wybranych ustawień.")
else:
    # Lista SKU + filtr tekstowy
    all_skus = sorted(df_trend["sku"].dropna().unique().tolist())
    search_term = st.text_input(f"Szukaj SKU lub produktu - {platform_name}", "", key=f"search_{platform_key}")
    filtered_skus = [sku for sku in all_skus if search_term.lower() in str(sku).lower()] if search_term else all_skus

    pick_skus = st.multiselect(
        f"Wybierz SKU do analizy trendu - {platform_name}",
        options=filtered_skus,
        default=filtered_skus[:5] if filtered_skus else [],
        key=f"multiselect_{platform_key}"
    )

    chart_type = st.radio(
        f"Typ wykresu - {platform_name}",
        ["area", "line"],
        index=0,
        horizontal=True,
        key=f"chart_{platform_key}"
    )

    if pick_skus:
        # Sumowanie (gdyby w pojedynczym dniu/tygodniu było kilka wierszy)
        df_plot = (
            df_trend[df_trend["sku"].isin(pick_skus)]
            .groupby([time_col, "sku"], as_index=False)[["curr_rev", "curr_qty"]].sum()
        )

        # Pivot REV i QTY
        rev_pv = df_plot.pivot(index=time_col, columns="sku", values="curr_rev").sort_index()
        qty_pv = df_plot.pivot(index=time_col, columns="sku", values="curr_qty").sort_index()

        # UZUPEŁNIENIE brakujących tygodni/dni zerami (pełna oś)
        rev_pv = rev_pv.reindex(full_index, fill_value=0)
        qty_pv = qty_pv.reindex(full_index, fill_value=0)

        # Rysowanie
        fig_tr = go.Figure()
        currency_symbol = "zł" if currency == "PLN" else "€"

        for sku in rev_pv.columns:
            yvals = rev_pv[sku].values
            qvals = qty_pv[sku].values  # ilość sztuk do tooltipa

            common_kwargs = dict(
                x=rev_pv.index,
                y=yvals,
                name=sku,
                customdata=np.stack([qvals], axis=-1),
                hovertemplate=(
                    "<b>%{fullData.name}</b><br>"
                    "%{x|%Y-%m-%d}<br>"
                    f"Sprzedaż: %{{y:,.0f}} {currency_symbol}<br>"
                    "Ilość: %{customdata[0]:,.0f}"
                    "<extra></extra>"
                )
            )

            if chart_type == "area":
                fig_tr.add_trace(go.Scatter(mode="lines", stackgroup="one", **common_kwargs))
            else:
                fig_tr.add_trace(go.Scatter(mode="lines+markers", **common_kwargs))

        fig_tr.update_layout(
            xaxis=dict(tickformat="%Y-%m-%d"),
            yaxis_title=f"Sprzedaż ({currency})",
            height=520,
            autosize=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
        )
        st.plotly_chart(fig_tr, config=PLOTLY_CONFIG)
