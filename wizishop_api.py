import requests

WIZISHOP_API_URL = "https://api.wizishop.com"

def get_token(email, password):
    try:
        response = requests.post(
            f"{WIZISHOP_API_URL}/v3/login",
            json={"username": email, "password": password}
        )
        return response.status_code, response.text, None, None
    except Exception as e:
        return None, str(e), None, None

def get_orders(token, shop_id, page=1, per_page=100):
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(
        f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/orders",
        headers=headers,
        params={"page": page, "per_page": per_page}
    )
    if response.status_code == 200:
        return response.json()
    return []

def get_products(token, shop_id):
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(
        f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/products",
        headers=headers,
        params={"per_page": 100}
    )
    if response.status_code == 200:
        return response.json()
    return []
