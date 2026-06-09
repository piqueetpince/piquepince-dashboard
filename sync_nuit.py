"""
Synchronisation nocturne de toutes les sources de données.

Usage:
    python sync_nuit.py                              # tout synchroniser
    python sync_nuit.py --only wizishop-commandes    # une seule sync
    python sync_nuit.py --only etsy faire            # groupes entiers
    python sync_nuit.py --only wizishop-produits wizishop-commandes

Slugs disponibles :
    wizishop  wizishop-categories  wizishop-marques  wizishop-skus
    wizishop-produits  wizishop-commandes
    etsy  etsy-commandes  etsy-produits  etsy-stock
    faire  faire-commandes  faire-produits  faire-stock
    shopify  shopify-produits  shopify-commandes
    ankorstore  ankorstore-produits  ankorstore-commandes  ankorstore-stock

Credentials lus depuis .env :
    SUPABASE_URL, SUPABASE_KEY
    WIZISHOP_EMAIL, WIZISHOP_PASSWORD
    ETSY_API_KEY, ETSY_SHARED_SECRET, ETSY_REFRESH_TOKEN, ETSY_ACCESS_TOKEN, ETSY_SHOP_ID
    FAIRE_TOKEN
    SHOPIFY_FOULARD_FRENCHY_SHOP, SHOPIFY_FOULARD_FRENCHY_TOKEN
    GMAIL_USER, GMAIL_PASSWORD
"""

import argparse
import os
import smtplib
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

# ── Arguments CLI ─────────────────────────────────────────────────────────────

_parser = argparse.ArgumentParser(description="Sync nocturne Pique&Pince")
_parser.add_argument(
    "--only", nargs="+", metavar="SLUG",
    help="Lancer uniquement ces syncs (ex: wizishop-commandes etsy faire)"
)
_args = _parser.parse_args()
only: list[str] = [s.lower() for s in (_args.only or [])]

# ── Mock Streamlit (avant tout import des modules sync) ───────────────────────
# Tous les modules sync appellent st.secrets, st.session_state, st.warning, etc.
# On injecte un objet qui lit depuis os.environ à la place.

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


class _SessionState(dict):
    pass


@contextmanager
def _noop_spinner(text=""):
    yield


class _MockSt:
    secrets       = _Secrets()
    session_state = _SessionState()

    def warning(self, msg):      print(f"    ⚠️  {msg}")
    def error(self, msg):        print(f"    ❌ {msg}")
    def success(self, msg):      print(f"    ✅ {msg}")
    def info(self, msg):         print(f"    ℹ️  {msg}")
    def write(self, *a, **kw):   pass
    def subheader(self, *a):     pass
    def spinner(self, text=""):  return _noop_spinner(text)

    def cache_data(self, **kwargs):
        def decorator(fn): return fn
        return decorator


sys.modules["streamlit"] = _MockSt()  # type: ignore

# ── Imports sync (après injection du mock) ────────────────────────────────────

from sync_database import (get_wizi_token, sync_categories, sync_marques,
                            sync_skus, sync_produits, sync_commandes, log_sync)
from sync_etsy import sync_etsy_commandes, sync_etsy_stock, log_sync_etsy
from sync_etsy_produits import sync_produits_etsy
from sync_faire import sync_faire_commandes, sync_faire_produits, sync_faire_stock, log_sync_faire
from sync_shopify import sync_shopify_produits, sync_shopify_commandes, log_sync_shopify
from sync_ankorstore import sync_ankorstore_produits, sync_ankorstore_commandes, sync_ankorstore_stock
import etsy_api

# ── Config email ──────────────────────────────────────────────────────────────

GMAIL_USER     = os.environ.get("GMAIL_USER", "")
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD", "")
DESTINATAIRE   = "contact@ns-ebiz.fr"

# ── Runner ────────────────────────────────────────────────────────────────────

resultats = []   # liste de dicts {nom, statut, duree, detail}


def run(nom, fn):
    """Exécute fn(), mesure la durée, capture les exceptions."""
    print(f"\n{'─'*55}")
    print(f"▶  {nom}")
    t0 = time.time()
    try:
        detail = fn()
        duree = time.time() - t0
        resultats.append({"nom": nom, "statut": "✅", "duree": duree,
                          "detail": str(detail) if detail is not None else "OK"})
        print(f"   ✅ Terminé en {duree:.1f}s — {detail}")
    except Exception as e:
        duree = time.time() - t0
        resultats.append({"nom": nom, "statut": "❌", "duree": duree, "detail": str(e)})
        print(f"   ❌ Erreur en {duree:.1f}s : {e}")


def _active(slug: str) -> bool:
    """Retourne True si ce slug doit être exécuté.

    Sans --only : tout est actif.
    Avec --only : actif si slug correspond exactement ou si un groupe préfixe le slug
    (ex: 'wizishop' active 'wizishop-commandes', 'wizishop-produits', etc.)
    """
    if not only:
        return True
    return any(slug == o or slug.startswith(o + "-") for o in only)


# ── 1. Wizishop ───────────────────────────────────────────────────────────────

_wizi_slugs = ["wizishop-categories", "wizishop-marques",
               "wizishop-skus", "wizishop-produits", "wizishop-commandes"]

if any(_active(s) for s in _wizi_slugs):
    print("Authentification Wizishop …")
    token_wizi, account_id_wizi, shop_id_wizi = get_wizi_token()
    if not token_wizi:
        print("❌ Impossible d'obtenir le token Wizishop — syncs Wizishop ignorées.")
        for nom in ["Wizishop — catégories", "Wizishop — marques",
                    "Wizishop — SKUs/stocks", "Wizishop — produits", "Wizishop — commandes"]:
            resultats.append({"nom": nom, "statut": "⏭️", "duree": 0,
                              "detail": "token Wizishop absent"})
    else:
        if _active("wizishop-categories"):
            run("Wizishop — catégories",
                lambda: f"{sync_categories(token_wizi, shop_id_wizi)} catégories")
        if _active("wizishop-marques"):
            run("Wizishop — marques",
                lambda: f"{sync_marques(token_wizi, shop_id_wizi)} marques")
        if _active("wizishop-skus"):
            run("Wizishop — SKUs/stocks",
                lambda: f"{sync_skus(token_wizi, shop_id_wizi)} SKUs")
        if _active("wizishop-produits"):
            run("Wizishop — produits",
                lambda: f"{sync_produits(token_wizi, shop_id_wizi)} produits")
        if _active("wizishop-commandes"):
            run("Wizishop — commandes",
                lambda: f"{sync_commandes(token_wizi, shop_id_wizi)} commandes")

# ── 2. Etsy commandes ─────────────────────────────────────────────────────────

def _etsy_commandes():
    shop_id = etsy_api.get_shop_id()
    if not shop_id:
        raise RuntimeError("ETSY_SHOP_ID introuvable")
    nb = sync_etsy_commandes(shop_id)
    return f"{nb} commandes"

if _active("etsy-commandes"):
    run("Etsy — commandes", _etsy_commandes)

# ── 3. Etsy produits ──────────────────────────────────────────────────────────

def _etsy_produits():
    shop_id = etsy_api.get_shop_id()
    if not shop_id:
        raise RuntimeError("ETSY_SHOP_ID introuvable")
    nb_l, nb_v = sync_produits_etsy(shop_id)
    return f"{nb_l} listings, {nb_v} variantes"

if _active("etsy-produits"):
    run("Etsy — produits", _etsy_produits)

if _active("etsy-stock"):
    def _etsy_stock():
        nb_maj, nb_err, nb_inc = sync_etsy_stock()
        return f"{nb_maj} listings mis à jour, {nb_err} erreur(s), {nb_inc} SKU(s) inconnus"
    run("Etsy — stock", _etsy_stock)

# ── 4. Faire commandes ────────────────────────────────────────────────────────

if _active("faire-commandes"):
    run("Faire — commandes",
        lambda: f"{sync_faire_commandes()} commandes")

# ── 5. Faire produits ─────────────────────────────────────────────────────────

def _faire_produits():
    nb_p, nb_v = sync_faire_produits()
    return f"{nb_p} produits, {nb_v} variantes"

if _active("faire-produits"):
    run("Faire — produits", _faire_produits)

if _active("faire-stock"):
    def _faire_stock():
        nb_maj, nb_err, nb_inc = sync_faire_stock()
        return f"{nb_maj} mis à jour, {nb_err} erreur(s), {nb_inc} SKU(s) inconnus"
    run("Faire — stock", _faire_stock)

# ── 6. Shopify Foulard Frenchy ────────────────────────────────────────────────

_shopify_slugs = ["shopify-produits", "shopify-commandes"]

if any(_active(s) for s in _shopify_slugs):
    shop_ff  = os.environ.get("SHOPIFY_FOULARD_FRENCHY_SHOP", "")
    token_ff = os.environ.get("SHOPIFY_FOULARD_FRENCHY_TOKEN", "")

    if not shop_ff or not token_ff:
        for nom in ["Shopify FF — produits", "Shopify FF — commandes"]:
            resultats.append({"nom": nom, "statut": "⏭️", "duree": 0,
                              "detail": "SHOPIFY_FOULARD_FRENCHY_SHOP / TOKEN absent"})
        print("\n⏭️  Shopify Foulard Frenchy ignoré (credentials manquants)")
    else:
        def _shopify_produits():
            nb_p, nb_v = sync_shopify_produits("foulard_frenchy", shop_ff, token_ff)
            return f"{nb_p} produits, {nb_v} variantes"

        def _shopify_commandes():
            nb = sync_shopify_commandes("foulard_frenchy", shop_ff, token_ff)
            return f"{nb} commandes"

        if _active("shopify-produits"):
            run("Shopify FF — produits",  _shopify_produits)
        if _active("shopify-commandes"):
            run("Shopify FF — commandes", _shopify_commandes)

# ── 7. Ankorstore ─────────────────────────────────────────────────────────────

_ankorstore_slugs = ["ankorstore-produits", "ankorstore-commandes", "ankorstore-stock"]

if any(_active(s) for s in _ankorstore_slugs):
    def _ankorstore_produits():
        nb_p, nb_v = sync_ankorstore_produits()
        return f"{nb_p} produits, {nb_v} variants"

    def _ankorstore_commandes():
        nb_c, nb_l = sync_ankorstore_commandes()
        return f"{nb_c} commandes, {nb_l} lignes"

    def _ankorstore_stock():
        nb_maj, nb_err, nb_inc = sync_ankorstore_stock()
        return f"{nb_maj} mis à jour, {nb_err} erreur(s), {nb_inc} SKU(s) inconnus"

    if _active("ankorstore-produits"):
        run("Ankorstore — produits",  _ankorstore_produits)
    if _active("ankorstore-commandes"):
        run("Ankorstore — commandes", _ankorstore_commandes)
    if _active("ankorstore-stock"):
        run("Ankorstore — stock",     _ankorstore_stock)

# ── Résumé console ────────────────────────────────────────────────────────────

duree_totale = sum(r["duree"] for r in resultats)
nb_ok  = sum(1 for r in resultats if r["statut"] == "✅")
nb_ko  = sum(1 for r in resultats if r["statut"] == "❌")

print(f"\n{'═'*55}")
print(f"  RÉSUMÉ — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
print(f"{'═'*55}")
for r in resultats:
    print(f"  {r['statut']}  {r['nom']:<35} {r['duree']:5.1f}s")
print(f"{'─'*55}")
print(f"  Total : {nb_ok} OK  {nb_ko} erreur(s)  en {duree_totale:.0f}s")
print(f"{'═'*55}")

# ── Email récapitulatif ───────────────────────────────────────────────────────

_ENTETE = 'background-color:#f0f0f0; font-weight:bold; padding:8px; border:1px solid #ccc;'
_CELL   = 'padding:7px 10px; border:1px solid #ddd;'
_OK     = 'background-color:#e8f5e9;'
_KO     = 'background-color:#ffebee;'
_SKIP   = 'background-color:#f5f5f5; color:#888;'


def _construire_html_recap():
    lignes_html = []
    for r in resultats:
        if r["statut"] == "✅":
            style = _OK
        elif r["statut"] == "❌":
            style = _KO
        else:
            style = _SKIP
        lignes_html.append(
            f'<tr style="{style}">'
            f'<td style="{_CELL}">{r["statut"]}</td>'
            f'<td style="{_CELL}">{r["nom"]}</td>'
            f'<td style="{_CELL}">{r["duree"]:.1f}s</td>'
            f'<td style="{_CELL}">{r["detail"]}</td>'
            f'</tr>'
        )
    thead = (
        f'<tr>'
        f'<th style="{_ENTETE}">Statut</th>'
        f'<th style="{_ENTETE}">Sync</th>'
        f'<th style="{_ENTETE}">Durée</th>'
        f'<th style="{_ENTETE}">Détail</th>'
        f'</tr>'
    )
    couleur_titre = "#2e7d32" if nb_ko == 0 else "#c62828"
    return f"""
    <html><body style="font-family:Arial,sans-serif;">
    <h2 style="color:{couleur_titre};">
      {'✅' if nb_ko == 0 else '❌'} Sync nocturne — {datetime.now().strftime('%d/%m/%Y %H:%M')}
    </h2>
    <p><strong>{nb_ok} syncs réussies</strong> — {nb_ko} erreur(s) — durée totale : {duree_totale:.0f}s</p>
    <table style="border-collapse:collapse; font-size:13px;">
      <thead>{thead}</thead>
      <tbody>{"".join(lignes_html)}</tbody>
    </table>
    </body></html>
    """


if GMAIL_USER and GMAIL_PASSWORD:
    statut_sujet = "✅" if nb_ko == 0 else f"❌ {nb_ko} erreur(s)"
    sujet = f"{statut_sujet} Sync nocturne — {datetime.now().strftime('%d/%m/%Y')}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = sujet
    msg["From"]    = GMAIL_USER
    msg["To"]      = DESTINATAIRE
    msg.attach(MIMEText(_construire_html_recap(), "html", "utf-8"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(GMAIL_USER, GMAIL_PASSWORD)
            srv.sendmail(GMAIL_USER, DESTINATAIRE, msg.as_string())
        print(f"\n📧 Email récapitulatif envoyé à {DESTINATAIRE}")
    except Exception as e:
        print(f"\n⚠️  Envoi email échoué : {e}")
else:
    print("\n⚠️  GMAIL_USER/GMAIL_PASSWORD absents — email non envoyé.")

sys.exit(1 if nb_ko > 0 else 0)
