"""
Import du CSV Shopify (commandes + lignes) vers Supabase.

Usage:
    python import_shopify_csv.py [chemin_du_csv]

Par défaut le CSV attendu : commandes_lignes_articles_2025-01-01_2026-03-28.csv
"""

import csv
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────────

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

if not SUPABASE_URL:
    secrets_path = Path(__file__).parent / ".streamlit" / "secrets.toml"
    if secrets_path.exists():
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # pip install tomli sur Python < 3.11
        with secrets_path.open("rb") as fh:
            _secrets = tomllib.load(fh)
        SUPABASE_URL = _secrets.get("SUPABASE_URL", "").rstrip("/")
        SUPABASE_KEY = _secrets.get("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    sys.exit("Erreur : SUPABASE_URL et SUPABASE_KEY doivent être définis dans .env")

BOUTIQUE   = "foulard_frenchy"
BATCH_SIZE = 500

CSV_PATH = Path(
    sys.argv[1] if len(sys.argv) > 1
    else "commandes_lignes_articles_2025-01-01_2026-03-28.csv"
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _headers():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates,return=minimal",
    }


def _float(val):
    """Convertit une valeur en float, None si vide ou non numérique."""
    if val is None:
        return None
    s = str(val).strip().replace(",", ".")
    if s in ("", "-", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _str(val):
    """Retourne None si la chaîne est vide."""
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def upsert_batch(table, rows, on_conflict):
    """Envoie rows vers Supabase par lots de BATCH_SIZE. Retourne nb_erreurs."""
    nb_erreurs = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
        r = requests.post(url, headers=_headers(), json=batch, timeout=30)
        if r.status_code not in (200, 201, 204):
            print(f"  [ERREUR] {table} lot {i//BATCH_SIZE + 1} : "
                  f"HTTP {r.status_code} — {r.text[:200]}")
            nb_erreurs += len(batch)
    return nb_erreurs


# ── Lecture CSV ───────────────────────────────────────────────────────────────

if not CSV_PATH.exists():
    sys.exit(f"Erreur : fichier introuvable → {CSV_PATH}")

print(f"Lecture de {CSV_PATH} …")

commandes: dict[str, dict] = {}   # Order ID → dict commande
lignes:    list[dict]       = []

with CSV_PATH.open(newline="", encoding="utf-8-sig") as fh:
    reader = csv.DictReader(fh)
    for row in reader:
        order_id = _str(row.get("Order ID"))
        if not order_id:
            continue

        # ── Commande (première occurrence par Order ID) ──────────────────────
        if order_id not in commandes:
            commandes[order_id] = {
                "id_shopify":        order_id,
                "boutique":          BOUTIQUE,
                "numero":            _str(row.get("Order name")),
                "statut_financier":  _str(row.get("Order payment status")),
                "statut_livraison":  _str(row.get("Order fulfillment status")),
                "devise":            _str(row.get("Order checkout currency")),
                "montant_ttc":       _float(row.get("Total sales")),
                "montant_ht_approx": _float(row.get("Net sales")),
                "montant_taxes":     _float(row.get("Taxes")),
                "frais_port":        _float(row.get("Shipping charges")),
                "montant_remises":   _float(row.get("Discounts")),
                "email_client":      _str(row.get("Customer email")),
                "id_client_shopify": _str(row.get("Customer ID")),
                "nom_client":        _str(row.get("Customer name")),
                "ville_facturation": _str(row.get("Billing city")),
                "cp_facturation":    _str(row.get("Billing postal code")),
                "pays_facturation_iso": _str(row.get("Billing country")),
                "cree_le":           (f"{row['Day']}T00:00:00+00:00" if _str(row.get("Day")) else None),
            }

        # ── Ligne article ─────────────────────────────────────────────────────
        product_id = _str(row.get("Product ID"))
        if product_id:
            lignes.append({
                "id_shopify":             f"{product_id}_{order_id}",
                "boutique":               BOUTIQUE,
                "id_commande_shopify":    order_id,
                "id_variant_shopify":     product_id,
                "sku":                    _str(row.get("Product variant SKU")),
                "titre":                  _str(row.get("Product title at time of sale")),
                "quantite":               int(float(row["Quantity ordered"])) if _str(row.get("Quantity ordered")) else None,
                "prix_unitaire_original": _float(row.get("Product variant price")),
                "total_remise":           _float(row.get("Line item discounts")),
            })

print(f"  → {len(commandes)} commandes distinctes, {len(lignes)} lignes articles")

# ── DEBUG : aperçu des 3 premières lignes construites ─────────────────────────
print("\n[DEBUG] 3 premières lignes construites avant envoi Supabase :")
for l in lignes[:3]:
    print(f"  id_shopify={l['id_shopify']!r}  id_commande_shopify={l['id_commande_shopify']!r}"
          f"  sku={l['sku']!r}  quantite={l['quantite']}")
# ─────────────────────────────────────────────────────────────────────────────

# ── Upsert ────────────────────────────────────────────────────────────────────

print("\nImport commandes_shopify …")
err_cmd = upsert_batch("commandes_shopify", list(commandes.values()), "id_shopify,boutique")

lignes_dedup = list({l["id_shopify"]: l for l in lignes}.values())
print(f"Import lignes_commande_shopify … ({len(lignes)} lignes → {len(lignes_dedup)} après dédoublonnage)")
err_lig = upsert_batch("lignes_commande_shopify", lignes_dedup, "id_shopify,boutique")

# ── Résumé ────────────────────────────────────────────────────────────────────

total_erreurs = err_cmd + err_lig
print("\n" + "─" * 45)
print(f"  Commandes importées  : {len(commandes) - err_cmd} / {len(commandes)}")
print(f"  Lignes importées     : {len(lignes_dedup) - err_lig} / {len(lignes_dedup)}")
print(f"  Erreurs              : {total_erreurs}")
print("─" * 45)

sys.exit(1 if total_erreurs else 0)
