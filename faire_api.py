import base64
import requests
import streamlit as st

FAIRE_API_URL = "https://www.faire.com/external-api/v2"
FAIRE_APP_ID = "apa_82qgm4c87e"


def _get_headers_oauth():
    """Mode OAuth : token Brand Portal utilisé comme OAuth token + app credentials en Base64."""
    credentials = base64.b64encode(
        f"{FAIRE_APP_ID}:{st.secrets['FAIRE_SECRET']}".encode()
    ).decode()
    return {
        "X-FAIRE-APP-CREDENTIALS": credentials,
        "X-FAIRE-OAUTH-ACCESS-TOKEN": st.secrets["FAIRE_TOKEN"],
        "Content-Type": "application/json"
    }


def _get_headers_token():
    """Mode token direct V1/V2 : token Brand Portal seul via X-FAIRE-ACCESS-TOKEN."""
    return {
        "X-FAIRE-ACCESS-TOKEN": st.secrets["FAIRE_TOKEN"],
        "Content-Type": "application/json"
    }


def api_get(endpoint, params=None):
    return requests.get(
        f"{FAIRE_API_URL}{endpoint}",
        headers=_get_headers_token(),
        params=params or {}
    )


def get_orders(since=None):
    all_orders = []
    params = {"limit": 100}
    if since:
        params["updated_at_min"] = since
    while True:
        r = api_get("/orders", params=params)
        if r.status_code != 200:
            break
        data = r.json()
        orders = data.get("orders", [])
        all_orders.extend(orders)
        cursor = data.get("cursor")
        if not cursor or not orders:
            break
        params = {"limit": 100, "cursor": cursor}
        if since:
            params["updated_at_min"] = since
    return all_orders


def get_products():
    all_products = []
    params = {"limit": 100}
    while True:
        r = api_get("/products", params=params)
        if r.status_code != 200:
            break
        data = r.json()
        products = data.get("products", [])
        all_products.extend(products)
        cursor = data.get("cursor")
        if not cursor or not products:
            break
        params = {"limit": 100, "cursor": cursor}
    return all_products
