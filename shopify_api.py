import hashlib
import hmac
import re
import secrets
import requests
import streamlit as st

SHOPIFY_API_VERSION = "2026-04"
SHOPIFY_SCOPES = "read_orders,read_products,read_inventory,read_customers"
SHOPIFY_REDIRECT_URI = "https://piquepince-dashboard-e5yp9kroebwpi6edfgl9zo.streamlit.app"


# ── Helpers bas niveau ───────────────────────────────────────────────────────

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


# ── OAuth Authorization Code Grant ──────────────────────────────────────────

def generate_nonce():
    return secrets.token_hex(16)


def get_auth_url(shop, client_id, state):
    """Construit l'URL d'autorisation Shopify (step 1 du flux)."""
    return (
        f"https://{shop}/admin/oauth/authorize"
        f"?client_id={client_id}"
        f"&scope={SHOPIFY_SCOPES}"
        f"&redirect_uri={SHOPIFY_REDIRECT_URI}"
        f"&state={state}"
    )


def verify_hmac(params: dict, client_secret: str) -> bool:
    """Vérifie la signature HMAC-SHA256 du callback Shopify.

    Algorithme doc officielle :
    1. Retirer le paramètre hmac
    2. Trier les paramètres restants alphabétiquement
    3. Joindre sous la forme key=value&key=value
    4. Calculer HMAC-SHA256 avec client_secret
    5. Comparer en constant-time avec le hmac reçu
    """
    received_hmac = params.get("hmac", "")
    filtered = {k: v for k, v in params.items() if k != "hmac"}
    message = "&".join(f"{k}={v}" for k, v in sorted(filtered.items()))
    digest = hmac.new(
        client_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(digest, received_hmac)


def exchange_code_for_token(shop, client_id, client_secret, code):
    """Échange le code d'autorisation contre un access token offline permanent."""
    r = requests.post(
        f"https://{shop}/admin/oauth/access_token",
        data={"client_id": client_id, "client_secret": client_secret, "code": code},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
    )
    return r.status_code, r.json() if r.status_code == 200 else r.text[:400]


# ── API REST paginée ─────────────────────────────────────────────────────────

def api_get(shop, token, endpoint, params=None):
    url = f"https://{shop}/admin/api/{SHOPIFY_API_VERSION}/{endpoint}"
    return requests.get(url, headers=_get_headers(token), params=params or {})


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
    params = {"limit": 250, "fields": "id,title,variants"}

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


# ── GraphQL ──────────────────────────────────────────────────────────────────

def graphql_query(shop, token, query):
    url = f"https://{shop}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
    return requests.post(url, headers=_get_headers(token), json={"query": query})


def test_connection(shop, token):
    query = "{ shop { name email myshopifyDomain plan { displayName } } }"
    r = graphql_query(shop, token, query)
    if r.status_code == 200:
        data = r.json()
        errors = data.get("errors")
        if errors:
            return 200, {"errors": errors}
        return 200, {"shop": {
            "name": data["data"]["shop"]["name"],
            "email": data["data"]["shop"]["email"],
            "domain": data["data"]["shop"]["myshopifyDomain"],
            "plan_name": data["data"]["shop"]["plan"]["displayName"],
        }}
    return r.status_code, r.text[:300]


def get_shopify_token():
    """Retourne le token depuis st.secrets (après installation OAuth)."""
    return (
        st.secrets["SHOPIFY_FOULARD_FRENCHY_SHOP"],
        st.secrets["SHOPIFY_FOULARD_FRENCHY_TOKEN"],
    )
