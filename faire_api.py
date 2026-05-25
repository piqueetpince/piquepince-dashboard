import base64
import requests
import streamlit as st
from supabase_api import select

FAIRE_API_URL = "https://www.faire.com/external-api/v2"
FAIRE_APP_ID = "apa_82qgm4c87e"


def _get_headers_token():
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


def api_patch(endpoint, body=None):
    return requests.patch(
        f"{FAIRE_API_URL}{endpoint}",
        headers=_get_headers_token(),
        json=body or {}
    )


def test_write_permission():
    variants = select("produits_faire_variants",
        "select=id_faire,id_produit_faire&limit=1")
    if not variants:
        return None, "Aucun variant trouvé dans produits_faire_variants"

    variant = variants[0]
    product_id = variant.get("id_produit_faire")
    variant_id = variant.get("id_faire")

    if not product_id or not variant_id:
        return None, "IDs manquants dans le variant"

    r = api_patch(f"/products/{product_id}/variants/{variant_id}", {})
    return r.status_code, r.text[:300]


def get_orders(since=None):
    all_orders = []
    params = {"limit": 50}
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
    params = {"limit": 50}
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
