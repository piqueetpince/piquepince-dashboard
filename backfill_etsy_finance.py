"""
Backfill complet de l'historique financier Etsy (grand-livre + paiements)
depuis le début de l'activité de la boutique sur Etsy.

Contrairement à sync_etsy_ledger()/sync_etsy_payments() appelées sans argument
(reprise depuis MAX(create_timestamp) en base), ce script force min_timestamp
à DATE_DEBUT et ignore ce qui est déjà synchronisé.

Usage:
    python backfill_etsy_finance.py

Credentials lus depuis .env :
    SUPABASE_URL, SUPABASE_KEY
    ETSY_API_KEY, ETSY_SHARED_SECRET, ETSY_REFRESH_TOKEN, ETSY_ACCESS_TOKEN, ETSY_SHOP_ID
"""

import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

DATE_DEBUT = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())

# ── Mock Streamlit (avant tout import des modules sync) ───────────────────────
# etsy_api / sync_etsy_finance appellent st.secrets et st.warning.

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


class _MockSt:
    secrets       = _Secrets()
    session_state = _SessionState()

    def warning(self, msg): print(f"    ⚠️  {msg}")
    def error(self, msg):   print(f"    ❌ {msg}")


sys.modules["streamlit"] = _MockSt()  # type: ignore

import etsy_api
from sync_etsy_finance import sync_etsy_ledger, sync_etsy_payments


def main():
    shop_id = etsy_api.get_shop_id()
    if not shop_id:
        print("❌ ETSY_SHOP_ID introuvable — vérifie ton .env.")
        sys.exit(1)

    debut_str = datetime.fromtimestamp(DATE_DEBUT, tz=timezone.utc).date()
    print(f"▶  Backfill Etsy finance — shop {shop_id} — depuis {debut_str}")
    print("   (peut prendre plusieurs minutes : ~31 jours par requête côté Etsy)")

    t0 = time.time()
    # Le grand-livre est fetché une seule fois et réutilisé pour les paiements
    # (get_payments en a besoin pour résoudre les payment_id) — évite de refaire
    # tout le fetch paginé deux fois sur plusieurs années d'historique.
    print("   Récupération du grand-livre …")
    entries = etsy_api.get_ledger_entries(shop_id, min_timestamp=DATE_DEBUT)
    duree_fetch = time.time() - t0
    print(f"   → {len(entries)} entrées récupérées en {duree_fetch:.0f}s")

    t1 = time.time()
    nb_ledger = sync_etsy_ledger(shop_id, entries=entries)
    print(f"   → {nb_ledger} entrées de grand-livre synchronisées en {time.time() - t1:.0f}s")

    t2 = time.time()
    nb_payments = sync_etsy_payments(shop_id, entries=entries)
    print(f"   → {nb_payments} paiements synchronisés en {time.time() - t2:.0f}s")

    print(f"✅ Backfill terminé en {time.time() - t0:.0f}s au total")


if __name__ == "__main__":
    main()
