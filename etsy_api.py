import requests
import streamlit as st

ETSY_API_URL = "https://openapi.etsy.com/v3"

def refresh_access_token():
    r = requests.post(
        "https://api.etsy.com/v3/public/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": st.secrets["ETSY_API_KEY"],
            "refresh_token": st.secrets["ETSY_REFRESH_TOKEN"]
        }
    )
    if r.status_code == 200:
        data = r.json()
        st.session_state["etsy_access_token"] = data.get("access_token")
        st.session_state["etsy_refresh_token"] = data.get("refresh_token")
        return data.get("access_token")
    return None

def get_access_token():
    if "etsy_access_token" in st.session_state:
        return st.session_state["etsy_access_token"]
    return st.secrets.get("ETSY_ACCESS_TOKEN")

def get_headers():
    return {
        "x-api-key": st.secrets["ETSY_API_KEY"],
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": "application/json"
    }

def api_get(url, params=None):
    r = requests.get(url, headers=get_headers(), params=params)
    if r.status_code == 401:
        new_token = refresh_access_token()
        if new_token:
            r = requests.get(url, headers=get_headers(), params=params)
    return r

def get_shop_id():
    r = api_get(f"{ETSY_API_URL}/application/openapi-ping")
    st.write(f"Ping status: {r.status_code} — {r.json()}")
    
    r2 = api_get(f"{ETSY_API_URL}/application/shops/PiqueetPince")
    st.write(f"Shop direct status: {r2.status_code} — {r2.text[:200]}")
    
    if r2.status_code == 200:
        return r2.json().get("shop_id")
    return None

def get_receipts(shop_id, limit=100, offset=0):
    r = api_get(
        f"{ETSY_API_URL}/application/shops/{shop_id}/receipts",
        params={"limit": limit, "offset": offset, "was_paid": "true"}
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
