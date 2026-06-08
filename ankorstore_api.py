import os
import sys
import time
from contextlib import contextmanager

from dotenv import load_dotenv

load_dotenv()

# ── Mock Streamlit (hors Streamlit Cloud) ─────────────────────────────────────
if "streamlit" not in sys.modules:
    class _Secrets:
        def __getitem__(self, key):
            val = os.environ.get(key)
            if val is None:
                raise KeyError(f"Secret manquant : {key!r} — vérifie ton .env")
            return val
        def get(self, key, default=None):
            return os.environ.get(key, default)
        def __contains__(self, key):
            return key in os.environ

    @contextmanager
    def _noop_spinner(text=""):
        yield

    class _MockSt:
        secrets       = _Secrets()
        session_state = {}
        def warning(self, msg):     print(f"  ⚠️  {msg}")
        def error(self, msg):       print(f"  ❌ {msg}")
        def spinner(self, text=""): return _noop_spinner(text)
        def cache_data(self, **kw):
            def dec(fn): return fn
            return dec

    sys.modules["streamlit"] = _MockSt()  # type: ignore
# ─────────────────────────────────────────────────────────────────────────────

import requests
import streamlit as st

ANKORSTORE_BASE_URL = "https://www.ankorstore.com"
ANKORSTORE_TOKEN_URL = f"{ANKORSTORE_BASE_URL}/oauth/token"

# Marge de renouvellement : renouvelle le token 60s avant expiration
_TOKEN_REFRESH_MARGIN = 60


# ── Authentification ──────────────────────────────────────────────────────────

def _fetch_token() -> str:
    """Demande un nouveau token via client_credentials et le met en cache."""
    r = requests.post(
        ANKORSTORE_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "client_credentials",
            "client_id":     st.secrets["ANKORSTORE_CLIENT_ID"],
            "client_secret": st.secrets["ANKORSTORE_CLIENT_SECRET"],
            "scope":         "*",
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    token      = data["access_token"]
    expires_in = int(data.get("expires_in", 3600))
    st.session_state["ankorstore_token"]      = token
    st.session_state["ankorstore_token_exp"]  = time.time() + expires_in
    return token


def _get_token() -> str:
    """Retourne le token en cache, le renouvelle si proche de l'expiration."""
    exp = st.session_state.get("ankorstore_token_exp", 0)
    if time.time() < exp - _TOKEN_REFRESH_MARGIN:
        return st.session_state["ankorstore_token"]
    return _fetch_token()


# ── Headers & requêtes ────────────────────────────────────────────────────────

def get_headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Accept":        "application/vnd.api+json",
    }


def api_get(endpoint: str, params: dict = None) -> dict:
    """GET sur un endpoint Ankorstore. Retourne le JSON parsé."""
    url = endpoint if endpoint.startswith("http") else f"{ANKORSTORE_BASE_URL}{endpoint}"
    r = requests.get(url, headers=get_headers(), params=params or {}, timeout=30)
    if r.status_code == 401:
        # Token expiré côté serveur : force un renouvellement et réessaie
        st.session_state.pop("ankorstore_token_exp", None)
        r = requests.get(url, headers=get_headers(), params=params or {}, timeout=30)
    if r.status_code != 200:
        st.warning(f"[Ankorstore] Erreur {r.status_code} sur {endpoint} : {r.text[:200]}")
        return {}
    return r.json()


# ── Pagination cursor-based (JSON:API links.next) ─────────────────────────────

def get_all_pages(endpoint: str, params: dict = None) -> list:
    """
    Parcourt toutes les pages d'un endpoint JSON:API.
    Suit links.next jusqu'à épuisement. Retourne la liste complète des data[].
    """
    all_data = []
    next_url = endpoint if endpoint.startswith("http") else f"{ANKORSTORE_BASE_URL}{endpoint}"
    current_params = params or {}

    while next_url:
        r = requests.get(
            next_url, headers=get_headers(), params=current_params, timeout=30
        )
        if r.status_code == 401:
            st.session_state.pop("ankorstore_token_exp", None)
            r = requests.get(
                next_url, headers=get_headers(), params=current_params, timeout=30
            )
        if r.status_code != 200:
            st.warning(f"[Ankorstore] Erreur {r.status_code} sur {next_url} : {r.text[:200]}")
            break

        body = r.json()
        page_data = body.get("data", [])
        if isinstance(page_data, list):
            all_data.extend(page_data)
        elif isinstance(page_data, dict):
            all_data.append(page_data)

        next_url = (body.get("links") or {}).get("next") or None
        current_params = {}  # le curseur est déjà encodé dans next_url

    return all_data


# ── Test de connexion ─────────────────────────────────────────────────────────

def test_connection() -> bool:
    """Vérifie que l'authentification et l'accès à l'API fonctionnent."""
    try:
        data = api_get("/api/v1/me/config")
        return bool(data)
    except Exception:
        return False
