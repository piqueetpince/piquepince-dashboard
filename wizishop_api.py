import requests

WIZISHOP_API_URL = "https://api.wizishop.com"

def get_token(email, password):
    try:
        response = requests.post(
            f"{WIZISHOP_API_URL}/v3/auth/login",
            headers={"Content-Type": "application/json"},
            json={"username": email, "password": password}
        )
        return response.status_code, response.text, None, None
    except Exception as e:
        return None, str(e), None, None

def get_shops(token, account_id):
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        response = requests.get(
            f"{WIZISHOP_API_URL}/v3/accounts/{account_id}/shops",
            headers=headers
        )
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        return []

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
