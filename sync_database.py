import requests
import streamlit as st
import time
from supabase_api import upsert, select

WIZISHOP_API_URL = "https://api.wizishop.com"

def get_wizi_token():
    response = requests.post(
        f"{WIZISHOP_API_URL}/v3/auth/login",
        headers={"Content-Type": "application/json"},
        json={
            "username": st.secrets["WIZISHOP_EMAIL"],
            "password": st.secrets["WIZISHOP_PASSWORD"]
        }
    )
    if response.status_code in [200, 201]:
        data = response.json()
        return data.get("token"), data.get("account_id"), data.get("default_shop_id")
    return None, None, None

def sync_categories(token, shop_id):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    page, total = 1, 0
    while True:
        r = requests.get(f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/categories",
                        headers=headers, params={"page": page, "limit": 100})
        if r.status_code != 200:
            break
        data = r.json()
        results = data if isinstance(data, list) else data.get("results", [])
        if not results:
            break
        batch = []
        for cat in results:
            batch.append({
                "id_wizi": cat.get("id"),
                "id_parent": cat.get("id_parent"),
                "nom": cat.get("name"),
                "url": cat.get("url"),
                "menu_title": cat.get("menu_title"),
                "visible": cat.get("visible"),
                "source": "wizishop"
            })
        if batch:
            upsert("categories", batch, "id_wizi")
            total += len(batch)
        if isinstance(data, list) or page >= data.get("pages", 1):
            break
        page += 1
    return total

def sync_marques(token, shop_id):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    page, total = 1, 0
    while True:
        r = requests.get(f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/brands",
                        headers=headers, params={"page": page, "limit": 100})
        if r.status_code != 200:
            break
        data = r.json()
        results = data.get("results", [])
        if not results:
            break
        batch = []
        for m in results:
            batch.append({
                "id_wizi": m.get("id"),
                "nom": m.get("name"),
                "url": m.get("url"),
                "image_url": m.get("image_url"),
                "source": "wizishop"
            })
        if batch:
            upsert("marques", batch, "id_wizi")
            total += len(batch)
        if page >= data.get("pages", 1):
            break
        page += 1
    return total

def sync_skus(token, shop_id):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    page, total = 1, 0
    while True:
        r = requests.get(f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/skus",
                        headers=headers, params={"page": page, "limit": 500})
        if r.status_code != 200:
            break
        data = r.json()
        results = data.get("results", [])
        if not results:
            break
        batch = []
        for s in results:
            batch.append({
                "sku": s.get("sku"),
                "id_produit_parent": s.get("prod_id"),
                "type": s.get("type"),
                "ean13": s.get("ean13"),
                "stock": int(s.get("stock") or 0),
                "statut": s.get("status"),
                "date_creation": s.get("created_at"),
                "date_maj_stock": s.get("updated_at"),
                "source": "wizishop"
            })
        if batch:
            upsert("skus", batch, "sku")
            total += len(batch)
        if page >= data.get("pages", 1):
            break
        page += 1
    return total

def sync_produits(token, shop_id):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    page, total = 1, 0
    while True:
        r = requests.get(f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/products",
                        headers=headers, params={"page": page, "limit": 100})
        if r.status_code != 200:
            break
        data = r.json()
        results = data.get("results", [])
        if not results:
            break
        for p in results:
            detail_r = requests.get(
                f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/products/{p['id']}",
                headers=headers
            )
            prod = detail_r.json() if detail_r.status_code == 200 else p
            upsert("produits", [{
                "id_wizi": int(prod.get("id")),
                "sku": prod.get("sku"),
                "nom": prod.get("name") or prod.get("label"),
                "fournisseur": prod.get("supplier"),
                "reference_fournisseur": prod.get("supplier_reference"),
                "marque": prod.get("brand"),
                "ean13": prod.get("ean13"),
                "prix_vente_ht": prod.get("price_tax_excluded"),
                "prix_achat_ht": prod.get("wholesale_price_tax_excluded"),
                "tva_pct": prod.get("tax"),
                "poids": prod.get("weight"),
                "reduction": prod.get("reduction"),
                "statut": prod.get("status") or ("visible" if prod.get("visible") else "hidden"),
                "image_url": prod.get("image_url"),
                "url": prod.get("url"),
                "source": "wizishop"
            }], "id_wizi")
            total += 1
            time.sleep(0.05)
        if page >= data.get("pages", 1):
            break
        page += 1
    return total

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

def get_max_commande_id():
    results = select("commandes", "select=id_wizi&order=id_wizi.desc&limit=1&source=eq.wizishop")
    if results:
        return results[0].get("id_wizi", 0)
    return 0

def sync_commandes(token, shop_id):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    depuis_id = get_max_commande_id()
    page, total = 1, 0
    while True:
        params = {"page": page, "limit": 100, "sort": "id"}
        if depuis_id:
            params["id_greater_than"] = depuis_id
        r = requests.get(f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/orders",
                        headers=headers, params=params)
        if r.status_code != 200:
            break
        data = r.json()
        results = data.get("results", [])
        if not results:
            break
        for cmd in results:
            detail_r = requests.get(
                f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/orders/{cmd['id']}",
                headers=headers
            )
            if detail_r.status_code != 200:
                continue
            o = detail_r.json()
            bil = o.get("billing_address", {})
            shp = o.get("shipping_address", {})
            shipping = o.get("shippings", [{}])[0] if o.get("shippings") else {}
            services = o.get("services", {})
            zone = get_zone_tva(bil.get("country_iso"))

            upsert("commandes", [{
                "id_wizi": o.get("id"),
                "numero_commande": o.get("public_id"),
                "date_commande": o.get("date"),
                "statut_code": o.get("status_code"),
                "statut_texte": o.get("status_text"),
                "devise": o.get("currency"),
                "montant_ttc": o.get("total_amount"),
                "montant_ht": o.get("total_amount_excl_tax"),
                "montant_produits_ttc": o.get("total_products_amount"),
                "frais_port": o.get("total_shipping_amount"),
                "remise": o.get("total_reduc_amount"),
                "code_promo": o.get("discount_code"),
                "frais_supplementaires": o.get("total_fees"),
                "mode_paiement": str(o.get("payment_mode")) if o.get("payment_mode") else None,
                "type_paiement": str(o.get("payment_type")) if o.get("payment_type") else None,
                "libelle_paiement": o.get("payment_label"),
                "numero_transaction": o.get("transaction_number"),
                "numero_facture": str(o.get("invoice_id")) if o.get("invoice_id") else None,
                "url_facture": o.get("invoice_url"),
                "poids_total": o.get("weight"),
                "origine": o.get("origin"),
                "tag": o.get("tag"),
                "commentaire": o.get("comment"),
                "id_client": o.get("customer_id"),
                "civilite_facturation": bil.get("civility"),
                "prenom_facturation": bil.get("firstname"),
                "nom_facturation": bil.get("lastname"),
                "email_client": bil.get("email"),
                "telephone_facturation": bil.get("phone"),
                "societe_facturation": bil.get("company"),
                "adresse_facturation": bil.get("street"),
                "cp_facturation": bil.get("postal_code"),
                "ville_facturation": bil.get("town"),
                "pays_facturation": bil.get("country"),
                "pays_facturation_iso": bil.get("country_iso"),
                "prenom_livraison": shp.get("firstname"),
                "nom_livraison": shp.get("lastname"),
                "telephone_livraison": shp.get("phone"),
                "adresse_livraison": shp.get("street"),
                "cp_livraison": shp.get("postal_code"),
                "ville_livraison": shp.get("town"),
                "pays_livraison": shp.get("country"),
                "pays_livraison_iso": shp.get("country_iso"),
                "mode_transport": str(shipping.get("mode")) if shipping.get("mode") is not None else None,
                "nom_transporteur": shipping.get("name"),
                "numero_suivi": shipping.get("tracking_number"),
                "emballage_cadeau": services.get("gift_wrap", False),
                "message_cadeau": services.get("message"),
                "zone_tva": zone,
                "source": "wizishop"
            }], "id_wizi")

            lignes = []
            for sku_item in shipping.get("skus", []):
                lignes.append({
                    "id_commande": o.get("id"),
                    "sku": sku_item.get("sku"),
                    "nom_produit": sku_item.get("title"),
                    "quantite": sku_item.get("quantity"),
                    "prix_unitaire_ttc": sku_item.get("price"),
                    "tva": sku_item.get("tax"),
                    "remise_produit": sku_item.get("total_discount"),
                    "poids": sku_item.get("weight"),
                    "source": "wizishop"
                })
            if lignes:
                upsert("lignes_commande", lignes, "id_commande,sku")

            total += 1
            time.sleep(0.05)
        if page >= data.get("pages", 1):
            break
        page += 1
    return total

def log_sync(table, source, nb, statut, message, duree):
    from supabase_api import upsert
    upsert("sync_log", [{
        "table_name": table,
        "source": source,
        "nb_enregistrements": nb,
        "statut": statut,
        "message": message,
        "duree_secondes": round(duree, 2)
    }], "id")

def run_full_sync():
    token, account_id, shop_id = get_wizi_token()
    if not token:
        return False, "Erreur de connexion Wizishop"
    resultats = {}
    etapes = [
        ("categories", sync_categories),
        ("marques", sync_marques),
        ("skus", sync_skus),
        ("commandes", sync_commandes),
    ]
    for nom, fn in etapes:
        debut = time.time()
        try:
            nb = fn(token, shop_id)
            duree = time.time() - debut
            log_sync(nom, "wizishop", nb, "success", f"{nb} enregistrements", duree)
            resultats[nom] = {"nb": nb, "duree": duree, "statut": "✓"}
        except Exception as e:
            duree = time.time() - debut
            log_sync(nom, "wizishop", 0, "error", str(e), duree)
            resultats[nom] = {"nb": 0, "duree": duree, "statut": "✗", "erreur": str(e)}
    return True, resultats
