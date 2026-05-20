import streamlit as st
import time
from supabase_api import upsert, select, insert

def get_zone_tva(country_iso):
    ue = {"AT","BE","BG","CY","CZ","DE","DK","EE","ES","FI","FR","GR",
          "HR","HU","IE","IT","LT","LU","LV","MT","NL","PL","PT","RO",
          "SE","SI","SK"}
    if not country_iso:
        return "inconnu"
    if country_iso == "FR":
        return "france"
    if country_iso in ue:
        return "ue"
    return "hors_ue"

def get_max_etsy_commande_id():
    results = select("commandes", "select=id_wizi&order=id_wizi.desc&limit=1&source=eq.etsy")
    if results:
        return results[0].get("id_wizi", 0)
    return 0

def sync_etsy_commandes(shop_id):
    from etsy_api import get_all_receipts
    depuis_id = get_max_etsy_commande_id()
    receipts = get_all_receipts(shop_id)
    total = 0

    for receipt in receipts:
        id_etsy = receipt.get("receipt_id")
        if not id_etsy:
            continue
        if depuis_id and id_etsy <= depuis_id:
            continue

        country_iso = receipt.get("country_iso")
        zone = get_zone_tva(country_iso)

        from datetime import datetime, timezone
        create_ts = receipt.get("create_timestamp")
        date_commande = datetime.fromtimestamp(create_ts, tz=timezone.utc).isoformat() if create_ts else None

        # Montants
        grandtotal = receipt.get("grandtotal", {})
        subtotal = receipt.get("subtotal", {})
        shipping = receipt.get("total_shipping_cost", {})
        discount = receipt.get("discount_amt", {})
        divisor = grandtotal.get("divisor", 100) or 100

        montant_ttc = grandtotal.get("amount", 0) / divisor
        montant_ht = subtotal.get("amount", 0) / divisor
        frais_port = shipping.get("amount", 0) / (shipping.get("divisor", 100) or 100)
        remise = discount.get("amount", 0) / (discount.get("divisor", 100) or 100)

        # Statut
        status = receipt.get("status", "")
        if status in ["Paid", "Completed"]:
            statut_code = 35
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
                libelle_variation = " / ".join([v.get("formatted_value", "") for v in variations])
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

def log_sync_etsy(table, nb, statut, message, duree):
    from supabase_api import upsert
    upsert("sync_log", [{
        "table_name": table,
        "source": "etsy",
        "nb_enregistrements": nb,
        "statut": statut,
        "message": message,
        "duree_secondes": round(duree, 2)
    }], "id")
