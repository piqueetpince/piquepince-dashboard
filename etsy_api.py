import requests
import streamlit as st

ETSY_API_URL = "https://openapi.etsy.com/v3"

def get_headers():
    return {
        "x-api-key": st.secrets["ETSY_API_KEY"],
        "Authorization": f"Bearer {st.secrets['ETSY_ACCESS_TOKEN']}",
        "Content-Type": "application/json"
    }

def refresh_token():
    r = requests.post(
        "https://api.etsy.com/v3/public/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": st.secrets["ETSY_API_KEY"],
            "refresh_token": st.secrets["ETSY_REFRESH_TOKEN"]
        }
    )
    if r.status_code == 200:
        return r.json().get("access_token")
    return None

def get_shop_id():
    r = requests.get(
        f"{ETSY_API_URL}/application/shops",
        headers=get_headers(),
        params={"shop_name": "PiqueetPince"}
    )
    if r.status_code == 200:
        results = r.json().get("results", [])
        if results:
            return results[0].get("shop_id")

    r2 = requests.get(
        f"{ETSY_API_URL}/application/users/me",
        headers=get_headers()
    )
    if r2.status_code == 200:
        user_id = r2.json().get("user_id")
        if user_id:
            r3 = requests.get(
                f"{ETSY_API_URL}/application/users/{user_id}/shops",
                headers=get_headers()
            )
            if r3.status_code == 200:
                results = r3.json().get("results", [])
                if results:
                    return results[0].get("shop_id")
    return None

def get_receipts(shop_id, limit=100, offset=0):
    r = requests.get(
        f"{ETSY_API_URL}/application/shops/{shop_id}/receipts",
        headers=get_headers(),
        params={
            "limit": limit,
            "offset": offset,
            "was_paid": "true"
        }
    )
    if r.status_code == 200:
        return r.json()
    return {}

def get_all_receipts(shop_id, depuis_date=None):
    all_receipts = []
    offset = 0
    limit = 100
    while True:
        data = get_receipts(shop_id, limit=limit, offset=offset)
        results = data.get("results", [])
        if not results:
            break
        if depuis_date:
            results_filtres = [r for r in results
                             if r.get("create_timestamp", 0) >= depuis_date]
            if len(results_filtres) < len(results):
                all_receipts.extend(results_filtres)
                break
            all_receipts.extend(results_filtres)
        else:
            all_receipts.extend(results)
        if offset + limit >= data.get("count", 0):
            break
        offset += limit
    return all_receipts
