import copy
import streamlit as st
import pandas as pd
import plotly.express as px
import requests
from datetime import date, timedelta, datetime
from typing import List, Tuple, Dict, Optional, Any

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
# 4. Sekcja wyboru w interfejsie użytkownika
# -----------------------
st.sidebar.header("Opcje raportu")
card_id = st.sidebar.number_input("ID karty Metabase", value=55, min_value=1, step=1)
selected_date = st.sidebar.date_input("Domyślna data", value=date.today() - timedelta(days=1))
selected_date_str = selected_date.strftime('%Y-%m-%d')
ignore_cache = st.sidebar.toggle("Ignoruj cache Metabase", value=True)
show_debug = st.sidebar.toggle("Pokaż diagnostykę", value=False)

# -----------------------
# 5. Funkcje pomocnicze (HTTP)
# -----------------------
def _http_get_json(url: str) -> Optional[Any]:
    try:
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _http_post_json(url: str, payload: dict) -> Tuple[Optional[List[dict]], Optional[str]]:
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data, None
        else:
            return None, "Odpowiedź nie jest listą rekordów JSON"
    except Exception as e:
        return None, str(e)


def _http_post_obj(url: str, payload: dict) -> Tuple[Optional[dict], Optional[str]]:
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            return data, None
        else:
            return None, "Odpowiedź nie jest obiektem JSON"
    except Exception as e:
        return None, str(e)


# -----------------------
# 6. Odczyt definicji karty i budowanie parametrów
# -----------------------
@st.cache_data(ttl=600, show_spinner=False)
def get_card_details(card_id: int) -> Tuple[Optional[dict], Dict[str, Any]]:
    info: Dict[str, Any] = {}
    url = f"{METABASE_URL}/api/card/{card_id}"
    card = _http_get_json(url)
    if card is None:
        info["error_get_card"] = f"Nie udało się pobrać definicji karty {card_id}"
        return None, info

    dq = card.get("dataset_query", {})
    info["dataset_query_keys"] = list(dq.keys())

    native = dq.get("native", {})
    tags = native.get("template-tags", {}) if isinstance(native, dict) else {}
    info["template_tags"] = list(tags.keys())

    params = dq.get("parameters", []) if isinstance(dq, dict) else []
    info["parameters"] = [
        {
            "type": p.get("type"),
            "target": p.get("target"),
            "name": p.get("name"),
        } for p in params if isinstance(p, dict)
    ]

    info["card_name"] = card.get("name")
    info["archived"] = card.get("archived")

    return card, info


def _val_for_date_param(param_type: str, date_str_or_tuple: Any) -> Any:
    t = (param_type or '').lower()
    if 'range' in t:
        if isinstance(date_str_or_tuple, (list, tuple)) and len(date_str_or_tuple) == 2:
            a = date_str_or_tuple[0]
            b = date_str_or_tuple[1]
            def _fmt(x):
                return x.strftime('%Y-%m-%d') if isinstance(x, (date, datetime)) else str(x)
            return [_fmt(a), _fmt(b)]
        return [str(date_str_or_tuple), str(date_str_or_tuple)]
    if isinstance(date_str_or_tuple, (date, datetime)):
        return date_str_or_tuple.strftime('%Y-%m-%d')
    return str(date_str_or_tuple)


def _parse_date_str(s: Optional[str], fallback: date) -> date:
    if not s:
        return fallback
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    return fallback


def derive_param_specs(card: dict) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    dq = card.get("dataset_query", {}) if isinstance(card, dict) else {}

    # Field filters (dataset_query.parameters)
    params = dq.get("parameters", []) if isinstance(dq, dict) else []
    for idx, p in enumerate(params or []):
        if not isinstance(p, dict):
            continue
        p_type = p.get('type') or ''
        target = p.get('target')
        name = p.get('name') or f"param_{idx}"
        label = f"Field param: {name}"
        specs.append({
            'mode': 'field',
            'name': name,
            'label': label,
            'type': p_type,
            'target': target,
            'tag_name': None,
            'default': None,
        })

    # Template-tags (native.template-tags)
    native = dq.get("native", {}) if isinstance(dq, dict) else {}
    tags = native.get("template-tags", {}) if isinstance(native, dict) else {}
    if isinstance(tags, dict):
        for tag_name, tag_def in tags.items():
            if not isinstance(tag_def, dict):
                continue
            t = tag_def.get('type') or 'text'
            label = f"Tag: {tag_name} ({t})"
            specs.append({
                'mode': 'tag',
                'name': tag_name,
                'label': label,
                'type': t,
                'target': ["variable", ["template-tag", tag_name]],
                'tag_name': tag_name,
                'default': tag_def.get('default')
            })

    return specs


def build_params_from_ui(specs: List[Dict[str, Any]], default_date: date) -> List[dict]:
    """Buduje listę parametrów do wysłania na podstawie definicji karty i wejść z panelu.
    Tylko parametry z włączonym checkboxem są wysyłane, aby uniknąć pustych filtrów."""
    params_to_send: List[dict] = []
    if not specs:
        return params_to_send

    st.sidebar.subheader("Parametry karty")
    for i, spec in enumerate(specs):
        label = spec['label']
        ptype = (spec['type'] or '').lower()
        base_key = f"param_input_{spec['mode']}_{spec['name']}_{i}"

        # Checkbox włączenia parametru (domyślnie True tylko dla typów daty)
        use_default = 'date' in ptype
        use_param = st.sidebar.checkbox(f"Włącz: {label}", value=use_default, key=base_key+"_use")
        if not use_param:
            continue

        # Ustal domyślną wartość
        default_val = spec.get('default')

        if 'date' in ptype:
            if 'range' in ptype:
                if isinstance(default_val, (list, tuple)) and len(default_val) == 2:
                    d1 = _parse_date_str(str(default_val[0]), default_date)
                    d2 = _parse_date_str(str(default_val[1]), default_date)
                    ui_val = st.sidebar.date_input(label, value=(d1, d2), key=base_key)
                else:
                    ui_val = st.sidebar.date_input(label, value=(default_date, default_date), key=base_key)
            else:
                d = _parse_date_str(str(default_val), default_date) if default_val else default_date
                ui_val = st.sidebar.date_input(label, value=d, key=base_key)
        elif 'number' in ptype or 'int' in ptype:
            num_default = 0
            try:
                if default_val is not None:
                    num_default = int(default_val)
            except Exception:
                pass
            ui_val = st.sidebar.number_input(label, value=num_default, key=base_key)
        else:
            txt_default = '' if default_val is None else str(default_val)
            ui_val = st.sidebar.text_input(label, value=txt_default, key=base_key)

        # Jeżeli to tekst i pozostawiono pusty, nie wysyłaj parametru
        if isinstance(ui_val, str) and ui_val.strip() == '':
            continue

        # Budowa parametru do wysłania
        val = _val_for_date_param(spec['type'], ui_val)
        params_to_send.append({
            'type': spec['type'],
            'target': spec['target'],
            'value': val
        })

    return params_to_send


# -----------------------
# 7. Wybór kolumn i mapowanie
# -----------------------
def _pick_columns(df: pd.DataFrame) -> Tuple[Optional[str], Optional[str], Dict[str, str]]:
    info: Dict[str, str] = {}
    cols = list(df.columns)
    lower_map = {c.lower(): c for c in cols}

    user_candidates = [
        "packing_user_login", "user_login", "login", "user", "pracownik",
        "pracownik_login", "login_pracownika", "packer", "packing_user", "employee"
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

    if user_col is None:
        object_cols = [c for c in cols if df[c].dtype == 'object' or str(df[c].dtype).startswith('category')]
        if object_cols:
            user_col = object_cols[0]

    if count_col is None:
        num_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
        if num_cols:
            sums = {c: pd.to_numeric(df[c], errors='coerce').sum(skipna=True) for c in num_cols}
            count_col = max(sums, key=sums.get)

    info['mapped_user_col'] = user_col or ''
    info['mapped_count_col'] = count_col or ''
    info['all_columns'] = ", ".join(cols)
    return user_col, count_col, info


def _rows_cols_to_records(obj: dict) -> List[dict]:
    data = obj.get('data') or {}
    rows = data.get('rows') or []
    cols = data.get('cols') or []
    # Nazwy kolumn: preferuj display_name, potem name
    names = []
    for c in cols:
        if isinstance(c, dict):
            names.append(c.get('display_name') or c.get('name') or 'col')
        else:
            names.append('col')
    records: List[dict] = []
    for r in rows:
        if isinstance(r, list):
            rec = {}
            for i, v in enumerate(r):
                key = names[i] if i < len(names) else f'col_{i}'
                rec[key] = v
            records.append(rec)
    return records


# -----------------------
# 8. Pobieranie danych z karty Metabase w oparciu o definicję karty i wiele endpointów
# -----------------------
@st.cache_data(ttl=600, show_spinner=False)
def get_packing_data(date_param: str, card_id: int, ignore_cache: bool, preferred_params: Optional[List[dict]]) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    info: Dict[str, Any] = {"card_id": card_id}

    card, card_info = get_card_details(card_id)
    info.update(card_info)
    if card is None:
        return pd.DataFrame(), {**info, "error": f"Nie można pobrać karty {card_id}"}

    attempts = []
    if preferred_params:
        attempts.append(preferred_params)
        info['preferred_params'] = preferred_params

    attempts += build_parameter_attempts(card, date_param)
    info['attempts_count'] = len(attempts)

    url_json = f"{METABASE_URL}/api/card/{card_id}/query/json"
    url_obj = f"{METABASE_URL}/api/card/{card_id}/query"
    url_dataset = f"{METABASE_URL}/api/dataset"

    last_error: Optional[str] = None

    for idx, params in enumerate(attempts):
        # 1) /api/card/:id/query/json
        payload = {"parameters": params} if params else {}
        if ignore_cache:
            payload["ignore_cache"] = True
        data, err = _http_post_json(url_json, payload)
        if err is None and data and isinstance(data, list) and len(data) > 0:
            info['used_attempt_index'] = idx
            info['used_params'] = params
            info['endpoint'] = '/api/card/query/json'
            df = pd.DataFrame(data)
            user_col, count_col, map_info = _pick_columns(df)
            info.update(map_info)
            if not user_col or not count_col:
                return pd.DataFrame(), {**info, "error": "Nie można odnaleźć kolumn użytkownika lub liczby paczek w odpowiedzi Metabase."}
            df[count_col] = pd.to_numeric(df[count_col], errors='coerce')
            result = df[[user_col, count_col]].rename(columns={user_col: 'packing_user_login', count_col: 'paczki_pracownika'})
            return result, info
        if err:
            last_error = err

        # 2) /api/card/:id/query (obiekt data->rows/cols)
        payload2 = {"parameters": params} if params else {}
        if ignore_cache:
            payload2["ignore_cache"] = True
        obj, err2 = _http_post_obj(url_obj, payload2)
        if err2 is None and obj and isinstance(obj, dict):
            records = _rows_cols_to_records(obj)
            if records:
                info['used_attempt_index'] = idx
                info['used_params'] = params
                info['endpoint'] = '/api/card/query'
                df = pd.DataFrame(records)
                user_col, count_col, map_info = _pick_columns(df)
                info.update(map_info)
                if not user_col or not count_col:
                    return pd.DataFrame(), {**info, "error": "Nie można odnaleźć kolumn użytkownika lub liczby paczek w odpowiedzi Metabase."}
                df[count_col] = pd.to_numeric(df[count_col], errors='coerce')
                result = df[[user_col, count_col]].rename(columns={user_col: 'packing_user_login', count_col: 'paczki_pracownika'})
                return result, info
        if err2:
            last_error = err2

        # 3) /api/dataset (bezpośrednio z dataset_query karty)
        dq = copy.deepcopy(card.get('dataset_query', {}))
        if isinstance(dq, dict) and dq:
            dq_payload = dq
            dq_payload['parameters'] = params
            if ignore_cache:
                dq_payload['ignore_cache'] = True
            obj2, err3 = _http_post_obj(url_dataset, dq_payload)
            if err3 is None and obj2 and isinstance(obj2, dict):
                records = _rows_cols_to_records(obj2)
                if records:
                    info['used_attempt_index'] = idx
                    info['used_params'] = params
                    info['endpoint'] = '/api/dataset'
                    df = pd.DataFrame(records)
                    user_col, count_col, map_info = _pick_columns(df)
                    info.update(map_info)
                    if not user_col or not count_col:
                        return pd.DataFrame(), {**info, "error": "Nie można odnaleźć kolumn użytkownika lub liczby paczek w odpowiedzi Metabase."}
                    df[count_col] = pd.to_numeric(df[count_col], errors='coerce')
                    result = df[[user_col, count_col]].rename(columns={user_col: 'packing_user_login', count_col: 'paczki_pracownika'})
                    return result, info
            if err3:
                last_error = err3

    # Jeżeli żadna próba nie zwróciła danych
    err_info = {
        **info,
        "error": "Brak danych z Metabase (pusta odpowiedź)",
    }
    if last_error:
        err_info['last_error'] = last_error
    return pd.DataFrame(), err_info


# -----------------------
# 9. Render: Card params UI -> pobranie danych -> KPI
# -----------------------
card, card_info = get_card_details(int(card_id))
user_params: List[dict] = []
if card:
    specs = derive_param_specs(card)
    user_params = build_params_from_ui(specs, selected_date)


df, info = get_packing_data(selected_date_str, int(card_id), bool(ignore_cache), user_params if user_params else None)

st.header(f"Raport z dnia: {selected_date.strftime('%d-%m-%Y')}")

if show_debug:
    with st.expander("Diagnostyka danych"):
        st.write("– Informacje o karcie –")
        for k, v in card_info.items():
            st.write(f"{k}: {v}")
        st.write("– Informacje o zapytaniu –")
        for k, v in info.items():
            st.write(f"{k}: {v}")
        if not df.empty:
            st.dataframe(df.head())

if not df.empty:
    try:
        total_packages = pd.to_numeric(df["paczki_pracownika"], errors='coerce').sum()
        avg_packages_per_user = pd.to_numeric(df["paczki_pracownika"], errors='coerce').mean()
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
    if 'error' in info:
        st.error(f"❌ {info.get('error')}\nSzczegóły: {info}")
    else:
        st.warning(
            f"Brak danych do wyświetlenia dla dnia {selected_date_str} 🚧. Upewnij się, że karta Metabase (ID {card_id}) jest poprawnie skonfigurowana.")
