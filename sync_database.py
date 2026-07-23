import requests
import streamlit as st
import time
from datetime import datetime, timezone, timedelta
from supabase_api import upsert, select, insert

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
        # ID fixe de la boutique BtoC plutôt que default_shop_id : le compte
        # gère plusieurs boutiques (BtoC + BtoB) et default_shop_id renvoyé par
        # le login n'est pas garanti de pointer vers la BtoC (voir get_wizi_shops).
        return data.get("token"), data.get("account_id"), 3899
    return None, None, None

def get_wizi_shops(token, account_id):
    """Liste les boutiques du compte Wizishop. Utile quand le compte gère
    plusieurs boutiques (ex: BtoC + BtoB) pour identifier le shop_id BtoC à
    utiliser dans les syncs — default_shop_id renvoyé par le login n'est pas
    forcément la bonne boutique."""
    if not token or not account_id:
        return []
    response = requests.get(
        f"{WIZISHOP_API_URL}/v3/accounts/{account_id}/shops",
        headers={"Authorization": f"Bearer {token}"}
    )
    if response.status_code != 200:
        return []
    data = response.json()
    results = data.get("results", data) if isinstance(data, dict) else data
    if not isinstance(results, list):
        return []
    return [{
        "id": shop.get("id"),
        "nom": shop.get("name") or shop.get("shop_name") or "",
        "type": shop.get("type") or shop.get("shop_type") or "",
        "url": shop.get("url") or "",
    } for shop in results]

def clean_date(date_str):
    if not date_str:
        return None
    try:
        from datetime import datetime
        date_str_clean = str(date_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(date_str_clean)
        if dt.year < 1900:
            return None
        return date_str
    except:
        return None

def _flatten_categories(cats, id_parent=None):
    """Aplatit l'arborescence de catégories Wizishop (champ "children" imbriqué)
    en une liste plate, en déduisant id_parent depuis la position dans l'arbre."""
    batch = []
    for cat in cats:
        batch.append({
            "id_wizi": cat.get("id"),
            "id_parent": id_parent,
            "nom": cat.get("name"),
            "url": cat.get("url"),
            "menu_title": cat.get("menu_title"),
            "visible": cat.get("visible"),
            "source": "wizishop"
        })
        enfants = cat.get("children") or []
        if enfants:
            batch.extend(_flatten_categories(enfants, id_parent=cat.get("id")))
    return batch

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
        batch = _flatten_categories(results)
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
            sku = str(s.get("sku")) if s.get("sku") else None
            if sku and sku.startswith("AE_"):
                continue  # produits dropshipping AliExpress importés dans Wizishop — exclus
            batch.append({
                "sku": sku,
                "id_produit_parent": str(s.get("prod_id")) if s.get("prod_id") else None,
                "type": s.get("type"),
                "ean13": s.get("ean13"),
                "stock": int(float(s.get("stock") or 0)),
                "statut": s.get("status"),
                "date_creation": clean_date(s.get("created_at")),
                "date_maj_stock": clean_date(s.get("updated_at")),
                "source": "wizishop"
            })
        if batch:
            upsert("skus", batch, "sku")
            total += len(batch)
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

def sync_produits(token, shop_id):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    categories_list = select("categories", "select=id_wizi,nom,id_parent&source=eq.wizishop")
    cat_map = {c["id_wizi"]: c["nom"] for c in categories_list} if categories_list else {}
    cat_parent_map = {c["id_wizi"]: c["id_parent"] for c in categories_list} if categories_list else {}

    def _resolve_nom_categorie(id_cat):
        if not id_cat:
            return ""
        nom = cat_map.get(id_cat, "")
        if nom:
            return nom
        id_parent = cat_parent_map.get(id_cat)
        return cat_map.get(id_parent, "") if id_parent else ""

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
            if str(p.get("sku") or "").startswith("AE_"):
                continue  # produits dropshipping AliExpress importés dans Wizishop — exclus

            detail_r = requests.get(
                f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/products/{p['id']}",
                headers=headers
            )
            prod = detail_r.json() if detail_r.status_code == 200 else p
            id_cat = prod.get("category_id")
            nom_cat = _resolve_nom_categorie(id_cat)

            nom = prod.get("name") or prod.get("label") or ""
            fournisseur = prod.get("supplier") or ""
            ref_fourn = prod.get("supplier_reference") or ""
            prix_achat = prod.get("wholesale_price_tax_excluded") or 0
            prix_vente = prod.get("price_tax_excluded") or 0
            tva = prod.get("tax") or 0
            poids = prod.get("weight") or 0
            statut = prod.get("status") or ("visible" if prod.get("visible") else "hidden")
            image_url = prod.get("image_url") or (
                prod.get("images", [None])[0] if prod.get("images") else None)

            # Upsert produit parent avec sku comme clé
            payload_parent = {
                "id_wizi": int(prod.get("id")),
                "sku": prod.get("sku"),
                "nom": nom,
                "reference_fournisseur": ref_fourn,
                "marque": prod.get("brand"),
                "ean13": prod.get("ean13"),
                "id_categorie": id_cat,
                "nom_categorie": nom_cat,
                "prix_vente_ht": prix_vente,
                "prix_achat_ht": prix_achat,
                "tva_pct": tva,
                "poids": poids,
                "reduction": prod.get("reduction"),
                "statut": statut,
                "image_url": image_url,
                "url": prod.get("url"),
                "source": "wizishop"
            }
            if fournisseur:
                # Ne jamais écraser un fournisseur déjà en base avec une valeur
                # vide — l'API Wizishop peut renvoyer "supplier" vide ponctuellement.
                payload_parent["fournisseur"] = fournisseur
            upsert("produits", [payload_parent], "sku")

            # Upsert chaque variation avec sku comme clé
            attributes = prod.get("attributes", [])
            for attr in attributes:
                for option in attr.get("options", []):
                    sku_variation = option.get("sku")
                    if not sku_variation:
                        continue
                    variation_valeur = option.get("value", "")
                    nom_variation = f"{nom} - {variation_valeur}" if variation_valeur else nom

                    # La variation hérite du statut du parent dès que celui-ci
                    # n'est pas "visible" (unavailable/hidden/draft) : un produit
                    # indisponible côté Wizishop ne doit pas avoir de variation
                    # "visible" en base, même si option.get("active") est True.
                    if statut == "visible":
                        statut_variation = "visible" if option.get("active") else "hidden"
                    else:
                        statut_variation = statut

                    payload_variation = {
                        "id_wizi": int(prod.get("id")),
                        "sku": sku_variation,
                        "nom": nom_variation,
                        "reference_fournisseur": ref_fourn,
                        "marque": prod.get("brand"),
                        "ean13": option.get("ean13") or "",
                        "id_categorie": id_cat,
                        "nom_categorie": nom_cat,
                        "prix_vente_ht": option.get("price_tax_excluded") or prix_vente,
                        "prix_achat_ht": prix_achat,
                        "tva_pct": tva,
                        "poids": option.get("weight") or poids,
                        "statut": statut_variation,
                        "image_url": option.get("image") or image_url,
                        "url": prod.get("url"),
                        "source": "wizishop"
                    }
                    if fournisseur:
                        # Même logique que pour le produit parent : ne pas écraser
                        # un fournisseur existant par une valeur vide.
                        payload_variation["fournisseur"] = fournisseur
                    upsert("produits", [payload_variation], "sku")

            total += 1
            time.sleep(0.05)

        if page >= data.get("pages", 1):
            break
        page += 1
    return total

def get_max_commande_id():
    results = select("commandes", "select=id_wizi&order=id_wizi.desc&limit=1&source=eq.wizishop")
    if results:
        return results[0].get("id_wizi", 0)
    return 0

def _sync_commandes_paginated(headers, shop_id, extra_params, insert_lignes):
    page, total = 1, 0
    while True:
        params = {"page": page, "limit": 100, "sort": "id", **extra_params}
        r = requests.get(f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/orders",
                         headers=headers, params=params)
        if r.status_code != 200:
            break
        data = r.json()
        results = data.get("results", [])
        if not results:
            break

        for cmd in results:
            # Passe 2 (insert_lignes=False) : si status_code est présent dans le listing,
            # on évite l'appel detail — upsert minimal sur le statut uniquement
            if not insert_lignes and "status_code" in cmd:
                upsert("commandes", [{
                    "id_wizi":      cmd["id"],
                    "statut_code":  cmd.get("status_code"),
                    "statut_texte": cmd.get("status_text"),
                }], "id_wizi")
                total += 1
                continue

            # Passe 1 (ou fallback si status_code absent du listing) : appel détail complet
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

            discounts = o.get("discounts", [])
            code_promo = discounts[0].get("name") if discounts else None

            upsert("commandes", [{
                "id_wizi": o.get("id"),
                "numero_commande": o.get("public_id"),
                "date_commande": clean_date(o.get("date")),
                "statut_code": o.get("status_code"),
                "statut_texte": o.get("status_text"),
                "devise": o.get("currency"),
                "montant_ttc": o.get("total_amount"),
                "montant_ht": o.get("total_amount_excl_tax"),
                "montant_produits_ttc": o.get("total_products_amount"),
                "frais_port": o.get("total_shipping_amount"),
                "remise": o.get("total_reduc_amount"),
                "code_promo": code_promo,
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
                "pickup_number": shipping.get("pickup_number"),
                "shipping_tax": shipping.get("tax"),
                "emballage_cadeau": services.get("gift_wrap", False),
                "message_cadeau": services.get("message"),
                "third_party_id": o.get("third_party_id"),
                "third_party_from": o.get("third_party_from"),
                "zone_tva": zone,
                "source": "wizishop"
            }], "id_wizi")

            lignes = []
            for sku_item in shipping.get("skus", []):
                customisations = sku_item.get("customisations", [])
                custom_titre = customisations[0].get("title") if customisations else None
                custom_contenu = customisations[0].get("content") if customisations else None
                custom_prix = customisations[0].get("price") if customisations else None

                variations = sku_item.get("variations", [])
                if variations:
                    for variation in variations:
                        lignes.append({
                            "id_commande": o.get("id"),
                            "sku": sku_item.get("sku"),
                            "nom_produit": sku_item.get("title"),
                            "quantite": sku_item.get("quantity"),
                            "prix_unitaire_ttc": sku_item.get("price"),
                            "tva": sku_item.get("tax"),
                            "remise_produit": sku_item.get("total_discount"),
                            "poids": sku_item.get("weight"),
                            "image_url": sku_item.get("image_url"),
                            "sku_variation": variation.get("sku"),
                            "libelle_variation": variation.get("title"),
                            "quantite_variation": variation.get("quantity"),
                            "poids_variation": variation.get("weight"),
                            "customisation_titre": custom_titre,
                            "customisation_contenu": custom_contenu,
                            "customisation_prix": custom_prix,
                            "source": "wizishop"
                        })
                else:
                    lignes.append({
                        "id_commande": o.get("id"),
                        "sku": sku_item.get("sku"),
                        "nom_produit": sku_item.get("title"),
                        "quantite": sku_item.get("quantity"),
                        "prix_unitaire_ttc": sku_item.get("price"),
                        "tva": sku_item.get("tax"),
                        "remise_produit": sku_item.get("total_discount"),
                        "poids": sku_item.get("weight"),
                        "image_url": sku_item.get("image_url"),
                        "sku_variation": None,
                        "libelle_variation": None,
                        "quantite_variation": None,
                        "poids_variation": None,
                        "customisation_titre": custom_titre,
                        "customisation_contenu": custom_contenu,
                        "customisation_prix": custom_prix,
                        "source": "wizishop"
                    })

            if lignes and insert_lignes:
                insert("lignes_commande", lignes)

            total += 1
            time.sleep(0.05)

        if page >= data.get("pages", 1):
            break
        page += 1
    return total


def sync_commandes(token, shop_id):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    total = 0

    depuis_id = get_max_commande_id()
    params_new = {"start_date": "2024-01-01T00:00:00+00:00"}
    if depuis_id:
        params_new["id_greater_than"] = depuis_id
    total += _sync_commandes_paginated(headers, shop_id, params_new, insert_lignes=True)

    date_14j = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00+00:00")
    total += _sync_commandes_paginated(headers, shop_id, {"date_from": date_14j}, insert_lignes=False)

    return total

def log_sync(table, source, nb, statut, message, duree):
    upsert("sync_log", [{
        "table_name": table,
        "source": source,
        "nb_enregistrements": nb,
        "statut": statut,
        "message": message,
        "duree_secondes": round(duree, 2)
    }], "id")
