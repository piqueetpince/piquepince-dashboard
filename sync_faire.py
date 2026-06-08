import time
from faire_api import get_orders, get_products, api_patch
from supabase_api import upsert, upsert_ignore, select
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
            "frais_port": (order.get("faire_covered_shipping_cost") or {}).get("amount_minor", 0) / 100,
            "tva_client": (payout.get("net_tax") or {}).get("amount_minor", 0) / 100,
            "montant_net_recu": (payout.get("total_payout") or {}).get("amount_minor", 0) / 100,
            "frais_expedition_faire": (payout.get("shipping_subsidy") or {}).get("amount_minor", 0) / 100,
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
                "id_faire": item.get("id"),
                "id_commande": order_id,
                "sku": item.get("sku"),
                "nom_produit": item.get("product_name"),
                "libelle_variation": item.get("variant_name"),
                "quantite": item.get("quantity"),
                "prix_unitaire_ttc": prix_ttc,
                "source": "faire"
            })

        if lignes:
            upsert_ignore("lignes_commande", lignes, "id_faire")

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
            prix_vente_conseille = 0
            if prices:
                eu_price = next(
                    (p for p in prices
                     if (p.get("geo_constraint") or {}).get("country_group") == "EUROPEAN_UNION"),
                    prices[0]
                )
                prix_grossiste = (
                    (eu_price.get("wholesale_price") or {}).get("amount_minor") or 0) / 100
                prix_vente_conseille = (
                    (eu_price.get("retail_price") or {}).get("amount_minor") or 0) / 100

            upsert("produits_faire_variants", [{
                "id_faire": variant.get("id"),
                "id_produit_faire": prod_id,
                "sku": variant.get("sku"),
                "nom": variant.get("name"),
                "available_quantity": variant.get("available_quantity"),
                "sale_state": variant.get("sale_state"),
                "lifecycle_state": variant.get("lifecycle_state"),
                "prix_grossiste": prix_grossiste,
                "prix_vente_conseille": prix_vente_conseille
            }], "id_faire")
            total_variants += 1

        time.sleep(0.05)

    return total_produits, total_variants


def sync_faire_stock():
    """
    Pousse le stock Wizishop vers Faire pour chaque variant dont le SKU
    existe dans la table skus. Ne met à jour que si le stock diffère.
    Retourne (nb_mis_a_jour, nb_erreurs, nb_sku_inconnus).
    """
    faire_variants = select(
        "produits_faire_variants",
        "select=id_faire,id_produit_faire,sku,available_quantity&sku=not.is.null",
    )
    if not faire_variants:
        print("  → Aucun variant Faire en base.")
        return 0, 0, 0

    wizi_skus = select("skus", "select=sku,stock&statut=eq.visible")
    stock_wizi_map = {
        r["sku"]: int(r.get("stock") or 0)
        for r in (wizi_skus or [])
    }

    nb_maj      = 0
    nb_erreurs  = 0
    nb_inconnus = 0

    for variant in faire_variants:
        variant_id  = variant.get("id_faire")
        produit_id  = variant.get("id_produit_faire")
        sku         = (variant.get("sku") or "").strip()
        stock_faire = int(variant.get("available_quantity") or 0)

        if sku not in stock_wizi_map:
            nb_inconnus += 1
            continue

        stock_wizi = stock_wizi_map[sku]

        if stock_wizi == stock_faire:
            continue  # pas de changement — évite l'appel inutile

        r = api_patch(
            f"/products/{produit_id}/variants/{variant_id}",
            {"inventory_levels": [{"quantity": stock_wizi}]},
        )

        if r.status_code in (200, 204):
            nb_maj += 1
        else:
            nb_erreurs += 1
            print(f"  ⚠️  Erreur stock Faire {sku} ({variant_id}): "
                  f"HTTP {r.status_code} — {r.text[:150]}")

        time.sleep(1.0)

    print(f"  → {nb_maj} variants mis à jour, "
          f"{nb_erreurs} erreur(s), "
          f"{nb_inconnus} SKU(s) non trouvés dans Wizishop")
    return nb_maj, nb_erreurs, nb_inconnus


def log_sync_faire(table, nb, statut, message, duree):
    upsert("sync_log", [{
        "table_name": table,
        "source": "faire",
        "nb_enregistrements": nb,
        "statut": statut,
        "message": message,
        "duree_secondes": round(duree, 2)
    }], "id")
