import requests

WIZISHOP_API_URL = "https://api.wizishop.com/v3"

def get_token(email, password):
    response = requests.post(
        f"{WIZISHOP_API_URL}/users/login",
        json={"username": email, "password": password}
    )
    if response.status_code == 200:
        data = response.json()
        return data.get("token"), data.get("id_shop")
    else:
        return None, None

def get_orders(token, shop_id, page=1, per_page=100):
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(
        f"{WIZISHOP_API_URL}/shops/{shop_id}/orders",
        headers=headers,
        params={"page": page, "per_page": per_page}
    )
    if response.status_code == 200:
        return response.json()
    else:
        return []

def get_products(token, shop_id):
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(
        f"{WIZISHOP_API_URL}/shops/{shop_id}/products",
        headers=headers,
        params={"per_page": 100}
    )
    if response.status_code == 200:
        return response.json()
    else:
        return []
