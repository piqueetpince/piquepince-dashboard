import requests
import pandas as pd

WIZISHOP_API_URL = "https://api.wizishop.com"

def get_token(email, password):
    try:
        response = requests.post(
            f"{WIZISHOP_API_URL}/v3/auth/login",
            headers={"Content-Type": "application/json"},
            json={"username": email, "password": password}
        )
        if response.status_code in [200, 201]:
            data = response.json()
            # ID fixe de la boutique BtoC plutôt que default_shop_id : le compte
            # gère plusieurs boutiques (BtoC + BtoB) et default_shop_id renvoyé par
            # le login n'est pas garanti de pointer vers la BtoC (voir sync_database.get_wizi_shops).
            return data.get("token"), data.get("account_id"), 3899
        return None, None, None
    except Exception as e:
        return None, None, None

def get_orders_page(token, shop_id, page=1, limit=100):
    try:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        response = requests.get(
            f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/orders",
            headers=headers,
            params={"page": page, "limit": limit}
        )
        if response.status_code == 200:
            return response.json()
        return {}
    except Exception as e:
        return {}

def get_all_recent_orders(token, shop_id, nb_mois=12):
    date_limite = pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=nb_mois)
    all_orders = []
    first_page = get_orders_page(token, shop_id, page=1, limit=100)
    if not first_page:
        return []
    total_pages = first_page.get("pages", 1)
    current_page = total_pages
    while current_page >= 1:
        data = get_orders_page(token, shop_id, page=current_page, limit=100)
        results = data.get("results", [])
        if not results:
            break
        recent = []
        stop = False
        for order in reversed(results):
            order_date = pd.to_datetime(order.get("date"), utc=True)
            if order_date >= date_limite:
                recent.append(order)
            else:
                stop = True
        all_orders.extend(recent)
        if stop:
            break
        current_page -= 1
    return all_orders

def get_all_skus(token, shop_id):
    try:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        all_skus = []
        page = 1
        while True:
            response = requests.get(
                f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/skus",
                headers=headers,
                params={"page": page, "limit": 500}
            )
            if response.status_code != 200:
                break
            data = response.json()
            results = data.get("results", [])
            if not results:
                break
            all_skus.extend(results)
            total_pages = data.get("pages", 1)
            if page >= total_pages:
                break
            page += 1
        return all_skus
    except Exception as e:
        return []

def get_all_products(token, shop_id):
    try:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        all_products = []
        page = 1
        while True:
            response = requests.get(
                f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/products",
                headers=headers,
                params={"page": page, "limit": 100}
            )
            if response.status_code != 200:
                break
            data = response.json()
            results = data.get("results", [])
            if not results:
                break
            all_products.extend(results)
            total_pages = data.get("pages", 1)
            if page >= total_pages:
                break
            page += 1
        return all_products
    except Exception as e:
        return []

def get_product_detail(token, shop_id, product_id):
    try:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        response = requests.get(
            f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/products/{product_id}",
            headers=headers
        )
        if response.status_code == 200:
            return response.json()
        return {}
    except Exception as e:
        return {}

def build_sku_mapping(products_list, token=None, shop_id=None, load_suppliers=False):
    mapping = {}
    for product in products_list:
        nom = product.get("label", "")
        sku_parent = product.get("sku", "")
        product_id = product.get("id", "")
        fournisseur = ""

        if load_suppliers and token and shop_id and product_id:
            detail = get_product_detail(token, shop_id, product_id)
            fournisseur = detail.get("supplier") or ""

        if sku_parent:
            mapping[sku_parent] = {"nom": nom, "fournisseur": fournisseur}

        for attribute in product.get("attributes", []):
            for option in attribute.get("options", []):
                sku_variation = option.get("sku", "")
                if sku_variation:
                    mapping[sku_variation] = {"nom": nom, "fournisseur": fournisseur}

    return mapping
