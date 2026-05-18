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

def get_orders(token, shop_id, page=1, limit=100):
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
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
