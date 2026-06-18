import time

import requests
import streamlit as st
from supabase_api import upsert, select

ETSY_API_URL = "https://openapi.etsy.com/v3"

# Etsy n'accepte pas de timestamp avant cette date pour min_created/max_created
ETSY_MIN_CREATED_TIMESTAMP = 946684800  # 2000-01-01


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


# ── Données financières ──────────────────────────────────────────────────────

# Etsy refuse un écart min_created/max_created supérieur à 31 jours (HTTP 400)
ETSY_LEDGER_MAX_WINDOW = 2678400  # 31 jours en secondes


def _get_ledger_entries_window(shop_id, min_created, max_created):
    """Pagine par offset les entrées du grand-livre sur une fenêtre <= 31 jours."""
    entries = []
    limit  = 100
    offset = 0
    while True:
        r = api_get(
            f"{ETSY_API_URL}/application/shops/{shop_id}/payment-account/ledger-entries",
            params={
                "min_created": min_created,
                "max_created": max_created,
                "limit":       limit,
                "offset":      offset,
            }
        )
        if r.status_code != 200:
            st.warning(f"[get_ledger_entries] HTTP {r.status_code} — {r.text[:200]}")
            break
        data    = r.json()
        results = data.get("results", [])
        if not results:
            break
        entries.extend(results)
        if offset + limit >= data.get("count", 0):
            break
        offset += limit
    return entries


def get_ledger_entries(shop_id, min_timestamp=None, max_timestamp=None):
    """Récupère toutes les entrées du grand-livre du compte de paiement Etsy
    (relevé avec solde courant), paginées par offset.

    Découpe la plage demandée en fenêtres de 31 jours maximum (limite imposée
    par Etsy sur min_created/max_created), sinon l'API renvoie un 400.

    Champs renvoyés par Etsy : entry_id, ledger_id, amount, balance, currency,
    create_date, created_timestamp, ledger_type, reference_type, reference_id,
    parent_entry_id, payment_adjustments[].
    """
    min_created = min_timestamp or ETSY_MIN_CREATED_TIMESTAMP
    max_created = max_timestamp or int(time.time())

    entries = []
    window_start = min_created
    while window_start <= max_created:
        window_end = min(window_start + ETSY_LEDGER_MAX_WINDOW, max_created)
        entries.extend(_get_ledger_entries_window(shop_id, window_start, window_end))
        window_start = window_end + 1
        time.sleep(0.1)
    return entries


def get_payments(shop_id, min_timestamp=None, max_timestamp=None, entries=None):
    """Récupère les paiements postés sur une plage de dates.

    L'endpoint Etsy GET /shops/{shop_id}/payments n'accepte pas de filtre par
    date : il exige une liste de payment_ids. On passe donc par le grand-livre
    (qui supporte min_created/max_created) pour obtenir les entry_id, puis on
    résout les Payments correspondants via /payment-account/ledger-entries/payments.

    Si `entries` est fourni (déjà récupéré via get_ledger_entries), on l'utilise
    directement au lieu de refaire l'appel — utile pour un backfill sur une
    longue période où le fetch du grand-livre est déjà coûteux.

    Champs renvoyés par Etsy : payment_id, receipt_id, amount_gross, amount_fees,
    amount_net, posted_gross, posted_fees, posted_net, adjusted_gross,
    adjusted_fees, adjusted_net, currency, create_timestamp, status,
    payment_adjustments[].
    """
    if entries is None:
        entries = get_ledger_entries(shop_id, min_timestamp, max_timestamp)
    entry_ids = [e["entry_id"] for e in entries if e.get("entry_id")]
    if not entry_ids:
        return []

    payments = {}
    batch_size = 25  # taille de lot non documentée par Etsy, valeur conservatrice
    for i in range(0, len(entry_ids), batch_size):
        batch = entry_ids[i:i + batch_size]
        r = api_get(
            f"{ETSY_API_URL}/application/shops/{shop_id}/payment-account/ledger-entries/payments",
            params={"ledger_entry_ids": ",".join(str(e) for e in batch)}
        )
        if r.status_code != 200:
            continue
        for payment in r.json().get("results", []):
            payments[payment.get("payment_id")] = payment
    return list(payments.values())


def get_receipt_payments(shop_id, receipt_id):
    """Récupère les paiements liés à un reçu spécifique."""
    r = api_get(f"{ETSY_API_URL}/application/shops/{shop_id}/receipts/{receipt_id}/payments")
    if r.status_code == 200:
        return r.json().get("results", [])
    return []
