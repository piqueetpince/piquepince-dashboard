"""
Synchronisation des données financières Etsy (grand-livre et paiements) vers Supabase.

Tables Supabase requises (SQL à exécuter une fois) :

    CREATE TABLE etsy_ledger_entries (
        ledger_entry_id   BIGINT PRIMARY KEY,
        shop_id           BIGINT,
        amount            INTEGER,                 -- en centimes
        balance           INTEGER,                 -- solde après opération, en centimes
        currency          TEXT,
        create_timestamp  TIMESTAMPTZ,
        ledger_type       TEXT,
        reference_type    TEXT,
        reference_id      BIGINT,                  -- NULL si Etsy renvoie un id non numérique
        description       TEXT,
        synced_at         TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_etsy_ledger_entries_shop_date
        ON etsy_ledger_entries (shop_id, create_timestamp);

    CREATE TABLE etsy_payments (
        payment_id        BIGINT PRIMARY KEY,
        shop_id           BIGINT,
        receipt_id        BIGINT,
        amount_gross      INTEGER,                 -- en centimes
        amount_fees       INTEGER,
        amount_net        INTEGER,
        posted_gross      INTEGER,
        posted_fees       INTEGER,
        posted_net        INTEGER,
        adjusted_gross    INTEGER,
        adjusted_fees     INTEGER,
        adjusted_net      INTEGER,
        currency          TEXT,
        create_timestamp  TIMESTAMPTZ,
        status            TEXT,
        synced_at         TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_etsy_payments_shop_date
        ON etsy_payments (shop_id, create_timestamp);
"""

from datetime import datetime, timezone

from supabase_api import upsert, select
from etsy_api import get_ledger_entries, get_payments


def _money_amount(value):
    """Extrait le montant en centimes d'un objet Money Etsy ({amount, divisor, currency_code})."""
    if isinstance(value, dict):
        return value.get("amount", 0)
    return value or 0


def _epoch_to_iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def _iso_to_epoch(iso_str):
    return int(datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp())


def _safe_bigint(value):
    """Etsy documente reference_id comme une chaîne ; on ne garde que les valeurs numériques."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_max_ledger_timestamp(shop_id):
    """Dernier create_timestamp synchronisé pour ce shop, en epoch seconds (ou None)."""
    rows = select(
        "etsy_ledger_entries",
        f"select=create_timestamp&shop_id=eq.{shop_id}&order=create_timestamp.desc&limit=1"
    )
    if rows and rows[0].get("create_timestamp"):
        return _iso_to_epoch(rows[0]["create_timestamp"])
    return None


def get_max_payment_timestamp(shop_id):
    """Dernier create_timestamp synchronisé pour ce shop, en epoch seconds (ou None)."""
    rows = select(
        "etsy_payments",
        f"select=create_timestamp&shop_id=eq.{shop_id}&order=create_timestamp.desc&limit=1"
    )
    if rows and rows[0].get("create_timestamp"):
        return _iso_to_epoch(rows[0]["create_timestamp"])
    return None


def sync_etsy_ledger(shop_id, min_timestamp=None, entries=None):
    """Synchronise le grand-livre du compte de paiement Etsy vers etsy_ledger_entries.

    Reprend depuis le dernier create_timestamp connu si min_timestamp n'est pas fourni.
    Si `entries` est fourni (déjà récupéré via get_ledger_entries), on l'utilise
    directement au lieu de refaire l'appel API.
    """
    if entries is None:
        depuis = min_timestamp if min_timestamp is not None else get_max_ledger_timestamp(shop_id)
        entries = get_ledger_entries(shop_id, min_timestamp=depuis)

    rows = []
    for entry in entries:
        entry_id = entry.get("entry_id")
        if not entry_id:
            continue
        ts = entry.get("created_timestamp") or entry.get("create_date")
        rows.append({
            "ledger_entry_id":  entry_id,
            "shop_id":          shop_id,
            "amount":           entry.get("amount", 0),
            "balance":          entry.get("balance", 0),
            "currency":         entry.get("currency"),
            "create_timestamp": _epoch_to_iso(ts),
            "ledger_type":      entry.get("ledger_type"),
            "reference_type":   entry.get("reference_type"),
            "reference_id":     _safe_bigint(entry.get("reference_id")),
            "description":      entry.get("description"),
        })

    if rows:
        upsert("etsy_ledger_entries", rows, "ledger_entry_id")
    return len(rows)


def sync_etsy_payments(shop_id, min_timestamp=None, entries=None):
    """Synchronise les paiements Etsy vers etsy_payments.

    Reprend depuis le dernier create_timestamp connu si min_timestamp n'est pas fourni.
    Si `entries` est fourni (déjà récupéré via get_ledger_entries), on l'utilise
    pour résoudre les paiements au lieu de refaire le fetch du grand-livre.
    """
    if entries is not None:
        payments = get_payments(shop_id, entries=entries)
    else:
        depuis = min_timestamp if min_timestamp is not None else get_max_payment_timestamp(shop_id)
        payments = get_payments(shop_id, min_timestamp=depuis)

    rows = []
    for payment in payments:
        payment_id = payment.get("payment_id")
        if not payment_id:
            continue
        rows.append({
            "payment_id":       payment_id,
            "shop_id":          shop_id,
            "receipt_id":       payment.get("receipt_id"),
            "amount_gross":     _money_amount(payment.get("amount_gross")),
            "amount_fees":      _money_amount(payment.get("amount_fees")),
            "amount_net":       _money_amount(payment.get("amount_net")),
            "posted_gross":     _money_amount(payment.get("posted_gross")),
            "posted_fees":      _money_amount(payment.get("posted_fees")),
            "posted_net":       _money_amount(payment.get("posted_net")),
            "adjusted_gross":   _money_amount(payment.get("adjusted_gross")),
            "adjusted_fees":    _money_amount(payment.get("adjusted_fees")),
            "adjusted_net":     _money_amount(payment.get("adjusted_net")),
            "currency":         payment.get("currency"),
            "create_timestamp": _epoch_to_iso(payment.get("create_timestamp")),
            "status":           payment.get("status"),
        })

    if rows:
        upsert("etsy_payments", rows, "payment_id")
    return len(rows)
