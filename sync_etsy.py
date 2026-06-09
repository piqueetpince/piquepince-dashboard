import requests
import streamlit as st
import time
from datetime import datetime, timezone, timedelta
from supabase_api import upsert, select, insert, update, delete
from etsy_api import api_get, get_headers

ETSY_API_URL = "https://openapi.etsy.com/v3"


def get_zone_tva(country_iso):
    ue = {"AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR",
          "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO",
          "SE", "SI", "SK"}
    if not country_iso:
        return "inconnu"
    if country_iso == "FR":
        return "france"
    if country_iso in ue:
        return "ue"
    return "hors_ue"


def get_max_etsy_date():
    results = select("commandes", "select=date_commande&order=date_commande.desc&limit=1&source=eq.etsy")
    if results and results[0].get("date_commande"):
        return datetime.fromisoformat(results[0]["date_commande"].replace("Z", "+00:00"))
    return None


def sync_etsy_commandes(shop_id):
    from etsy_api import get_all_receipts
    depuis_date = get_max_etsy_date()
    seuil = (depuis_date - timedelta(days=2)) if depuis_date else None
    receipts = get_all_receipts(shop_id)
    total = 0


    for receipt in receipts:
        id_etsy = receipt.get("receipt_id")
        if not id_etsy:
            continue

        create_ts = receipt.get("create_timestamp")
        date_commande_dt = datetime.fromtimestamp(create_ts, tz=timezone.utc) if create_ts else None
        date_commande = date_commande_dt.isoformat() if date_commande_dt else None

        if seuil and date_commande_dt and date_commande_dt < seuil:
            continue

        country_iso = receipt.get("country_iso")
        zone = get_zone_tva(country_iso)

        grandtotal = receipt.get("grandtotal", {})
        subtotal = receipt.get("subtotal", {})
        shipping = receipt.get("total_shipping_cost", {})
        discount = receipt.get("discount_amt", {})
        divisor = grandtotal.get("divisor", 100) or 100

        montant_ttc = grandtotal.get("amount", 0) / divisor
        montant_ht = subtotal.get("amount", 0) / divisor
        frais_port = shipping.get("amount", 0) / (shipping.get("divisor", 100) or 100)
        remise = discount.get("amount", 0) / (discount.get("divisor", 100) or 100)

        status = receipt.get("status", "")
        if status == "Completed":
            statut_code = 35
        elif status == "Paid":
            statut_code = 30
        elif status in ["Canceled", "Cancelled"]:
            statut_code = 50
        elif status in ["Fully refunded", "Partially refunded"]:
            statut_code = 45
        elif status == "Open":
            statut_code = 20
        else:
            statut_code = 30

        upsert("commandes", [{
            "id_wizi": id_etsy,
            "numero_commande": str(id_etsy),
            "date_commande": date_commande,
            "statut_code": statut_code,
            "statut_texte": status,
            "devise": grandtotal.get("currency_code", "EUR"),
            "montant_ttc": montant_ttc,
            "montant_ht": montant_ht,
            "frais_port": frais_port,
            "remise": remise,
            "id_client": receipt.get("buyer_user_id"),
            "nom_facturation": receipt.get("name"),
            "adresse_facturation": receipt.get("first_line"),
            "cp_facturation": receipt.get("zip"),
            "ville_facturation": receipt.get("city"),
            "pays_facturation_iso": country_iso,
            "pays_facturation": country_iso,
            "nom_livraison": receipt.get("name"),
            "adresse_livraison": receipt.get("first_line"),
            "cp_livraison": receipt.get("zip"),
            "ville_livraison": receipt.get("city"),
            "pays_livraison_iso": country_iso,
            "pays_livraison": country_iso,
            "zone_tva": zone,
            "source": "etsy"
        }], "id_wizi")

        lignes = []
        for transaction in receipt.get("transactions", []):
            prix = transaction.get("price", {})
            prix_div = prix.get("divisor", 100) or 100
            prix_unitaire = prix.get("amount", 0) / prix_div

            variations = transaction.get("variations", [])
            sku_variation = None
            libelle_variation = None
            if variations:
                libelle_variation = " / ".join(
                    [v.get("formatted_value", "") for v in variations])
                sku_variation = transaction.get("sku")

            lignes.append({
                "id_commande": id_etsy,
                "sku": transaction.get("sku"),
                "nom_produit": transaction.get("title"),
                "quantite": transaction.get("quantity"),
                "prix_unitaire_ttc": prix_unitaire,
                "sku_variation": sku_variation,
                "libelle_variation": libelle_variation,
                "source": "etsy"
            })

        if lignes:
            insert("lignes_commande", lignes)

        total += 1
        time.sleep(0.05)

    return total


def _clean_etsy_inventory(products_raw, stock_wizi_map):
    """
    Prépare le body d'inventaire pour le PUT Etsy :
    - SKU connu + stock > 0 → quantity=stock, is_enabled=True
    - SKU connu + stock = 0 → quantity=0,     is_enabled=False
    - SKU inconnu           → quantity et is_enabled inchangés
    - Convertit price objet → décimal
    - Supprime les champs read-only (product_id, offering_id, is_deleted,
      scale_name, value_pairs)
    Retourne (products_clean, nb_skus_maj, nb_skus_inconnus, nb_offerings_actifs).
    """
    cleaned          = []
    nb_maj           = 0
    nb_inconnus      = 0
    nb_enabled_total = 0

    for product in products_raw:
        sku        = (product.get("sku") or "").strip()
        sku_connu  = sku and sku in stock_wizi_map

        offerings_clean = []
        for offering in (product.get("offerings") or []):
            price_raw = offering.get("price") or {}
            if isinstance(price_raw, dict):
                amount  = price_raw.get("amount", 0)
                divisor = price_raw.get("divisor", 100) or 100
                price   = round(amount / divisor, 2)
            else:
                price = float(price_raw or 0)

            if sku_connu:
                stock_wizi = stock_wizi_map[sku]
                quantity   = stock_wizi
                is_enabled = stock_wizi > 0
            else:
                quantity   = offering.get("quantity", 0)
                is_enabled = offering.get("is_enabled", True)

            if is_enabled:
                nb_enabled_total += 1

            offering_clean = {
                "quantity":   quantity,
                "is_enabled": is_enabled,
                "price":      price,
            }
            if offering.get("readiness_state_id"):
                offering_clean["readiness_state_id"] = offering["readiness_state_id"]

            offerings_clean.append(offering_clean)

        if sku_connu:
            nb_maj += 1
        elif sku:
            nb_inconnus += 1

        props_clean = []
        for pv in (product.get("property_values") or []):
            props_clean.append({k: v for k, v in pv.items()
                                if k not in ("scale_name", "value_pairs")})

        cleaned.append({
            "sku":             sku,
            "offerings":       offerings_clean,
            "property_values": props_clean,
        })

    return cleaned, nb_maj, nb_inconnus, nb_enabled_total


def sync_etsy_stock():
    """
    Pousse le stock Wizishop vers Etsy pour tous les listings (actifs et inactifs).
    Gère automatiquement l'activation/désactivation selon le stock résultant.
    Retourne (nb_listings_maj, nb_erreurs, nb_skus_inconnus).
    """
    # Tous statuts : on veut aussi réactiver les inactifs si stock disponible
    listings = select("produits_etsy", "select=listing_id,statut")
    if not listings:
        print("  → Aucun listing Etsy en base.")
        return 0, 0, 0

    wizi_skus = select("skus", "select=sku,stock&statut=eq.visible")
    stock_wizi_map = {r["sku"]: int(r.get("stock") or 0) for r in (wizi_skus or [])}

    nb_maj            = 0
    nb_erreurs        = 0
    nb_inconnus_total = 0

    for listing in listings:
        listing_id     = listing["listing_id"]
        statut_actuel  = listing.get("statut") or "active"

        r_get = api_get(
            f"{ETSY_API_URL}/application/listings/{listing_id}/inventory")
        if r_get.status_code == 404:
            # Listing supprimé sur Etsy → nettoyage en base
            delete("produits_etsy_variations", f"listing_id=eq.{listing_id}")
            delete("produits_etsy", f"listing_id=eq.{listing_id}")
            print(f"  [sync_etsy_stock] Listing {listing_id} introuvable (404) → supprimé de la base")
            time.sleep(0.5)
            continue
        if r_get.status_code != 200:
            nb_erreurs += 1
            st.warning(f"[sync_etsy_stock] GET {listing_id}: "
                       f"HTTP {r_get.status_code} — {r_get.text[:150]}")
            time.sleep(0.5)
            continue

        inv          = r_get.json()
        products_raw = inv.get("products", [])
        if not products_raw:
            time.sleep(0.5)
            continue

        products_clean, nb_updated, nb_unkn, nb_enabled = _clean_etsy_inventory(
            products_raw, stock_wizi_map)
        nb_inconnus_total += nb_unkn

        if nb_updated == 0:
            time.sleep(0.5)
            continue  # aucun SKU Wizishop trouvé → pas de modification

        if nb_enabled == 0:
            # Tous les stocks sont à 0 — Etsy interdit un PUT avec tous les
            # offerings désactivés ("One offering must be enabled").
            # Solution : désactiver le listing directement, sans PUT inventory.
            if statut_actuel not in ("inactive", "edit"):
                r_deact = requests.patch(
                    f"https://api.etsy.com/v3/application/listings/{listing_id}",
                    headers=get_headers(),
                    json={"state": "inactive"},
                    timeout=15,
                )
                if r_deact.status_code in (200, 204):
                    nb_maj += 1
                elif r_deact.status_code == 404:
                    delete("produits_etsy_variations", f"listing_id=eq.{listing_id}")
                    delete("produits_etsy", f"listing_id=eq.{listing_id}")
                    print(f"  [sync_etsy_stock] Listing {listing_id} introuvable (404) → supprimé de la base")
                else:
                    st.warning(f"[sync_etsy_stock] PATCH inactive {listing_id}: "
                               f"HTTP {r_deact.status_code} — {r_deact.text[:200]}")
                time.sleep(0.3)
            time.sleep(0.5)
            continue

        # Au moins un SKU a du stock → PUT inventory normal
        body = {
            "products":             products_clean,
            "price_on_property":    inv.get("price_on_property", []),
            "quantity_on_property": inv.get("quantity_on_property", []),
            "sku_on_property":      inv.get("sku_on_property", []),
        }

        r_put = requests.put(
            f"{ETSY_API_URL}/application/listings/{listing_id}/inventory",
            headers=get_headers(),
            json=body,
            timeout=30,
        )

        if r_put.status_code in (200, 204):
            nb_maj += 1
            # Mise à jour stock_etsy en base pour chaque SKU connu
            for product in products_clean:
                sku = (product.get("sku") or "").strip()
                if sku and sku in stock_wizi_map:
                    update("produits_etsy_variations",
                           f"sku=eq.{sku}&listing_id=eq.{listing_id}",
                           {"stock_etsy": stock_wizi_map[sku]})
            # Réactiver si le listing était inactif et qu'il y a du stock
            if statut_actuel == "inactive":
                requests.patch(
                    f"https://api.etsy.com/v3/application/listings/{listing_id}",
                    headers=get_headers(),
                    json={"state": "active"},
                    timeout=15,
                )
                time.sleep(0.3)
        else:
            nb_erreurs += 1
            st.warning(f"[sync_etsy_stock] PUT {listing_id}: "
                       f"HTTP {r_put.status_code} — {r_put.text[:200]}")

        time.sleep(0.5)

    print(f"  → {nb_maj} listings mis à jour, {nb_erreurs} erreur(s), "
          f"{nb_inconnus_total} SKU(s) non trouvés dans Wizishop")
    return nb_maj, nb_erreurs, nb_inconnus_total


def log_sync_etsy(table, nb, statut, message, duree):
    upsert("sync_log", [{
        "table_name": table,
        "source": "etsy",
        "nb_enregistrements": nb,
        "statut": statut,
        "message": message,
        "duree_secondes": round(duree, 2)
    }], "id")
