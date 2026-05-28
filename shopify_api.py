import re
import requests
import streamlit as st

SHOPIFY_API_VERSION = "2026-04"


def _get_headers(token):
    return {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }


def _next_page_info(link_header):
    if not link_header:
        return None
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            match = re.search(r'page_info=([^&>]+)', part)
            if match:
                return match.group(1)
    return None


def api_get(shop, token, endpoint, params=None):
    url = f"https://{shop}/admin/api/{SHOPIFY_API_VERSION}/{endpoint}"
    headers = _get_headers(token)
    r = requests.get(url, headers=headers, params=params or {})
    return r


def get_orders(shop, token, since_date=None):
    all_orders = []
    params = {
        "limit": 250,
        "status": "any",
        "fields": "id,name,created_at,financial_status,fulfillment_status,"
                  "total_price,subtotal_price,total_tax,currency,"
                  "customer,billing_address,line_items",
    }
    if since_date:
        params["created_at_min"] = since_date

    while True:
        r = api_get(shop, token, "orders.json", params)
        if r.status_code != 200:
            st.warning(f"Erreur Shopify orders: {r.status_code} — {r.text[:200]}")
            break
        orders = r.json().get("orders", [])
        all_orders.extend(orders)

        page_info = _next_page_info(r.headers.get("Link", ""))
        if not page_info or not orders:
            break
        params = {"limit": 250, "page_info": page_info}

    return all_orders


def get_products(shop, token):
    all_products = []
    params = {
        "limit": 250,
        "fields": "id,title,variants",
    }

    while True:
        r = api_get(shop, token, "products.json", params)
        if r.status_code != 200:
            st.warning(f"Erreur Shopify products: {r.status_code} — {r.text[:200]}")
            break
        products = r.json().get("products", [])
        all_products.extend(products)

        page_info = _next_page_info(r.headers.get("Link", ""))
        if not page_info or not products:
            break
        params = {"limit": 250, "page_info": page_info}

    return all_products


def test_connection(shop, token):
    r = api_get(shop, token, "shop.json")
    return r.status_code, r.json() if r.status_code == 200 else r.text[:300]


def get_shopify_credentials():
    return st.secrets["SHOPIFY_FOULARD_FRENCHY_SHOP"], st.secrets["SHOPIFY_FOULARD_FRENCHY_TOKEN"]
