import time
from faire_api import get_orders, get_products
from supabase_api import upsert, insert
from sync_database import get_zone_tva

STATUT_MAP = {
    "NEW": 10,
    "PENDING_RETAILER_CONFIRMATION": 10,
    "BACKORDERED": 15,
    "PROCESSING": 20,
    "PRE_TRANSIT": 25,
    "IN_TRANSIT": 30,
    "DELIVERED": 35,
    "CANCELED": 50,
}

# Faire renvoie des codes ISO alpha-3 ; get_zone_tva attend de l'alpha-2.
# Mapping des pays les plus fréquents pour les marchands français.
ISO3_TO_ISO2 = {
    "FRA": "FR", "DEU": "DE", "BEL": "BE", "NLD": "NL", "ESP": "ES",
    "ITA": "IT", "CHE": "CH", "GBR": "GB", "USA": "US", "CAN": "CA",
    "AUT": "AT", "PRT": "PT", "LUX": "LU", "DNK": "DK", "SWE": "SE",
    "NOR": "NO", "FIN": "FI", "POL": "PL", "CZE": "CZ", "HUN": "HU",
    "ROU": "RO", "HRV": "HR", "SVK": "SK", "SVN": "SI", "BGR": "BG",
    "LTU": "LT", "LVA": "LV", "EST": "EE", "CYP": "CY", "MLT": "MT",
    "GRC": "GR", "IRL": "IE", "AUS": "AU", "JPN": "JP",
}


def _country_iso2(country_code):
    if not country_code:
        return ""
    if len(country_code) == 2:
        return country_code.upper()
    return ISO3_TO_ISO2.get(country_code.upper(), country_code.upper())


def sync_faire_commandes():
    orders = get_orders()
    total = 0

    for order in orders:
        order_id = order.get("id")
        statut_brut = order.get("state", "")
        statut_code = STATUT_MAP.get(statut_brut, 10)

        address = order.get("address") or {}
        customer = order.get("customer") or {}
        payout = order.get("payout_costs") or {}

        montant_ttc = (payout.get("subtotal_after_brand_discounts") or {}).get("amount_minor", 0) / 100
        commission_faire = (payout.get("commission") or {}).get("amount_minor", 0) / 100

        country_iso2 = _country_iso2(address.get("country_code", ""))
        zone = get_zone_tva(country_iso2)
        tva_rate = 0.20 if zone in ("france", "ue") else 0.0
        montant_ht = round(montant_ttc / (1 + tva_rate), 2) if montant_ttc else 0

        date_commande = order.get("created_at")

        upsert("commandes", [{
            "id_faire": order_id,
            # id_wizi laissé NULL pour les commandes Faire
            "numero_commande": order_id,
            "date_commande": date_commande,
            "statut_code": statut_code,
            "statut_texte": statut_brut,
            "montant_ttc": montant_ttc,
            "montant_ht": montant_ht,
            "commission_faire": commission_faire,
            "pays_facturation_iso": country_iso2,
            "pays_facturation": address.get("country"),
            "ville_facturation": address.get("city"),
            "nom_facturation": customer.get("last_name", ""),
            "prenom_facturation": customer.get("first_name", ""),
            "zone_tva": zone,
            "source": "faire"
        }], "id_faire")

        # Prérequis schema : lignes_commande.id_commande doit être TEXT (ou VARCHAR)
        # pour accepter les IDs Faire (bo_xxx). Si la colonne est BIGINT avec FK
        # vers commandes.id_wizi, il faudra modifier le type dans Supabase.
        lignes = []
        for item in order.get("items", []):
            price_obj = item.get("price") or {}
            prix_ttc = (price_obj.get("amount_minor") or 0) / 100
            lignes.append({
                "id_commande": order_id,
                "sku": item.get("sku"),
                "nom_produit": item.get("product_name"),
                "quantite": item.get("quantity"),
                "prix_unitaire_ttc": prix_ttc,
                "source": "faire"
            })

        if lignes:
            insert("lignes_commande", lignes)

        total += 1
        time.sleep(0.05)

    return total


def sync_faire_produits():
    products = get_products()
    total_produits = 0
    total_variants = 0

    for product in products:
        prod_id = product.get("id")

        upsert("produits_faire", [{
            "id_faire": prod_id,
            "nom": product.get("name"),
            "sale_state": product.get("sale_state"),
            "lifecycle_state": product.get("lifecycle_state"),
            "unit_multiplier": product.get("unit_multiplier"),
            "minimum_order_quantity": product.get("minimum_order_quantity")
        }], "id_faire")
        total_produits += 1

        for variant in product.get("variants", []):
            prices = variant.get("prices") or []
            prix_grossiste = 0
            if prices:
                wp = prices[0].get("wholesale_price") or {}
                prix_grossiste = (wp.get("amount_minor") or 0) / 100

            upsert("produits_faire_variants", [{
                "id_faire": variant.get("id"),
                "id_produit_faire": prod_id,
                "sku": variant.get("sku"),
                "nom": variant.get("name"),
                "available_quantity": variant.get("available_quantity"),
                "sale_state": variant.get("sale_state"),
                "lifecycle_state": variant.get("lifecycle_state"),
                "prix_grossiste": prix_grossiste
            }], "id_faire")
            total_variants += 1

        time.sleep(0.05)

    return total_produits, total_variants


def log_sync_faire(table, nb, statut, message, duree):
    upsert("sync_log", [{
        "table_name": table,
        "source": "faire",
        "nb_enregistrements": nb,
        "statut": statut,
        "message": message,
        "duree_secondes": round(duree, 2)
    }], "id")
