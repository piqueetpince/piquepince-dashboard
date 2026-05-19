import streamlit as st
import time
from supabase_api import upsert, select, insert
from etsy_api import get_all_receipts

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
    depuis_id = get_max_etsy_commande_id()
    depuis_date = None

    if depuis_id:
        commande = select("commandes", f"select=date_commande&id_wizi=eq.{depuis_id}&source=eq.etsy")
        if commande:
            from datetime import datetime
            dt = datetime.fromisoformat(commande[0]["date_commande"].replace("Z", "+00:00"))
            depuis_date = int(dt.timestamp())

    receipts = get_all_receipts(shop_id, depuis_date=depuis_date)
    total = 0

    for receipt in receipts:
        id_etsy = receipt.get("receipt_id")
        if not id_etsy:
            continue

        buyer = receipt.get("buyer_user_id")
        ship = receipt.get("shipping_address") or {}
        zone = get_zone_tva(ship.get("country_iso"))

        from datetime import datetime, timezone
        create_ts = receipt.get("create_timestamp")
        date_commande = datetime.fromtimestamp(create_ts, tz=timezone.utc).isoformat() if create_ts else None

        upsert("commandes", [{
            "id_wizi": id_etsy,
            "numero_commande": str(receipt.get("receipt_id")),
            "date_commande": date_commande,
            "statut_code": 35 if receipt.get("status") == "completed" else 30,
            "statut_texte": receipt.get("status"),
            "devise": receipt.get("grand_total", {}).get("currency_code", "EUR"),
            "montant_ttc": receipt.get("grand_total", {}).get("amount", 0) / 100,
            "montant_ht": receipt.get("subtotal", {}).get("amount", 0) / 100,
            "frais_port": receipt.get("total_shipping_cost", {}).get("amount", 0) / 100,
            "remise": receipt.get("discount_amt", {}).get("amount", 0) / 100,
            "id_client": buyer,
            "prenom_livraison": ship.get("first_line"),
            "nom_livraison": ship.get("name"),
            "adresse_livraison": ship.get("first_line"),
            "cp_livraison": ship.get("zip"),
            "ville_livraison": ship.get("city"),
            "pays_livraison": ship.get("country_iso"),
            "pays_livraison_iso": ship.get("country_iso"),
            "pays_facturation_iso": ship.get("country_iso"),
            "zone_tva": zone,
            "source": "etsy"
        }], "id_wizi")

        lignes = []
        for transaction in receipt.get("transactions", []):
            lignes.append({
                "id_commande": id_etsy,
                "sku": transaction.get("sku") or str(transaction.get("listing_id")),
                "nom_produit": transaction.get("title"),
                "quantite": transaction.get("quantity"),
                "prix_unitaire_ttc": transaction.get("price", {}).get("amount", 0) / 100,
                "tva": transaction.get("taxable"),
                "source": "etsy"
            })

        if lignes:
            insert("lignes_commande", lignes)

        total += 1
        time.sleep(0.1)

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
