import requests
import streamlit as st
from supabase_api import upsert, select

ETSY_API_URL = "https://openapi.etsy.com/v3"


# ── Config Supabase ───────────────────────────────────────────────────────────

def _get_config(key):
    """Lit une valeur depuis la table config. Retourne None si absente."""
    try:
        rows = select("config", f"select=value&key=eq.{key}&limit=1")
        if rows:
            return rows[0].get("value")
    except Exception:
        pass
    return None


def _set_config(key, value):
    """Persiste une valeur dans la table config."""
    try:
        upsert("config", [{"key": key, "value": value}], "key")
    except Exception:
        pass


# ── Tokens ────────────────────────────────────────────────────────────────────

def _get_refresh_token():
    """Retourne le refresh token le plus récent : session > Supabase > secrets."""
    if st.session_state.get("etsy_refresh_token"):
        return st.session_state["etsy_refresh_token"]
    val = _get_config("etsy_refresh_token")
    if val:
        return val
    return st.secrets.get("ETSY_REFRESH_TOKEN")


def get_access_token():
    """Retourne l'access token le plus récent : session > Supabase > secrets."""
    if st.session_state.get("etsy_access_token"):
        return st.session_state["etsy_access_token"]

    val = _get_config("etsy_access_token")
    if val:
        return val

    # Fallback vers secrets : on persiste immédiatement pour les runs suivants
    token   = st.secrets.get("ETSY_ACCESS_TOKEN")
    refresh = st.secrets.get("ETSY_REFRESH_TOKEN")
    if token:
        _set_config("etsy_access_token", token)
    if refresh:
        _set_config("etsy_refresh_token", refresh)
    return token


def refresh_access_token():
    """Rafraîchit le token et persiste les deux tokens dans Supabase."""
    r = requests.post(
        "https://api.etsy.com/v3/public/oauth/token",
        data={
            "grant_type":    "refresh_token",
            "client_id":     st.secrets["ETSY_API_KEY"],
            "refresh_token": _get_refresh_token(),
        }
    )
    if r.status_code == 200:
        data = r.json()
        new_access  = data.get("access_token")
        new_refresh = data.get("refresh_token")

        st.session_state["etsy_access_token"]  = new_access
        st.session_state["etsy_refresh_token"] = new_refresh

        _set_config("etsy_access_token",  new_access)
        _set_config("etsy_refresh_token", new_refresh)

        return new_access
    return None


# ── Requêtes ──────────────────────────────────────────────────────────────────

def get_headers():
    token = get_access_token()
    if not token:
        token = refresh_access_token()
    return {
        "x-api-key":     f"{st.secrets['ETSY_API_KEY']}:{st.secrets['ETSY_SHARED_SECRET']}",
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }


def api_get(url, params=None):
    r = requests.get(url, headers=get_headers(), params=params)
    if r.status_code in (401, 403):
        new_token = refresh_access_token()
        if new_token:
            r = requests.get(url, headers=get_headers(), params=params)
    return r


def get_shop_id():
    if "ETSY_SHOP_ID" in st.secrets:
        return st.secrets["ETSY_SHOP_ID"]
    r = api_get(f"{ETSY_API_URL}/application/users/me")
    if r.status_code == 200:
        return r.json().get("shop_id")
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
    limit  = 100
    while True:
        data    = get_receipts(shop_id, limit=limit, offset=offset)
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
