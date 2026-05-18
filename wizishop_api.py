import requests

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
            token = data.get("token")
            account_id = data.get("account_id")
            shop_id = data.get("default_shop_id")
            return token, account_id, shop_id
        return None, None, None
    except Exception as e:
        return None, None, None

def get_orders(token, shop_id, page=1, limit=100, sort="date_desc"):
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        response = requests.get(
            f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/orders",
            headers=headers,
            params={"page": page, "limit": limit, "sort": sort}
        )
        if response.status_code == 200:
            return response.json()
        return {}
    except Exception as e:
        return {}

def get_all_recent_orders(token, shop_id, nb_mois=12):
    import pandas as pd
    from datetime import timezone
    all_orders = []
    page = 1
    date_limite = pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=nb_mois)

    while True:
        data = get_orders(token, shop_id, page=page, limit=100, sort="date_desc")
        results = data.get("results", [])
        if not results:
            break

        for order in results:
            order_date = pd.to_datetime(order.get("date"), utc=True)
            if order_date >= date_limite:
                all_orders.append(order)
            else:
                return all_orders

        total_pages = data.get("pages", 1)
        if page >= total_pages:
            break
        page += 1

    return all_orders

def get_products(token, shop_id, page=1, limit=100):
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        response = requests.get(
            f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/products",
            headers=headers,
            params={"page": page, "limit": limit}
        )
        if response.status_code == 200:
            return response.json()
        return {}
    except Exception as e:
        return {}
