import requests

WIZISHOP_API_URL = "https://api.wizishop.com"

def get_token(email, password):
    try:
        response = requests.post(
            f"{WIZISHOP_API_URL}/v3/auth/login",
            headers={"Content-type": "application/json"},
            json={"mail": email, "password": password}
        )
        if response.status_code == 200:
            data = response.json()
            token = data.get("token")
            account_id = data.get("account_id")
            return token, account_id
        else:
            return None, None
    except Exception as e:
        return None, None

def get_shops(token, account_id):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-type": "application/json"
    }
    response = requests.get(
        f"{WIZISHOP_API_URL}/v3/accounts/{account_id}/shops",
        headers=headers
    )
    if response.status_code == 200:
        return response.json()
    return []

def get_orders(token, shop_id, page=1, limit=20):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-type": "application/json"
    }
    response = requests.get(
        f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/orders",
        headers=headers,
        params={"page": page, "limit": limit}
    )
    if response.status_code == 200:
        return response.json()
    return {}

def get_products(token, shop_id, page=1, limit=20):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-type": "application/json"
    }
    response = requests.get(
        f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/products",
        headers=headers,
        params={"page": page, "limit": limit}
    )
    if response.status_code == 200:
        return response.json()
    return {}
