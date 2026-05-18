import requests
import pg8000.native as psycopg2
import streamlit as st
import time

WIZISHOP_API_URL = "https://api.wizishop.com"

def get_db_connection():
    return pg8000.native.Connection(
    host="db.donsocudmtnopajnhomj.supabase.co",
    database="postgres",
    user="postgres",
    password=st.secrets["SUPABASE_DB_PASSWORD"],
    port=5432
)

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

def sync_categories(token, shop_id, conn):
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
        cur = conn.cursor()
        for cat in results:
            cur.execute("""
                INSERT INTO categories (id_wizi, id_parent, nom, url, menu_title, visible)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id_wizi) DO UPDATE SET
                    nom = EXCLUDED.nom, visible = EXCLUDED.visible, updated_at = NOW()
            """, (cat.get("id"), cat.get("id_parent"), cat.get("name"),
                  cat.get("url"), cat.get("menu_title"), cat.get("visible")))
            total += 1
        conn.commit()
        cur.close()
        if isinstance(data, list) or page >= data.get("pages", 1):
            break
        page += 1
    return total

def sync_marques(token, shop_id, conn):
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
        cur = conn.cursor()
        for m in results:
            cur.execute("""
                INSERT INTO marques (id_wizi, nom, url, image_url)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id_wizi) DO UPDATE SET
                    nom = EXCLUDED.nom, updated_at = NOW()
            """, (m.get("id"), m.get("name"), m.get("url"), m.get("image_url")))
            total += 1
        conn.commit()
        cur.close()
        if page >= data.get("pages", 1):
            break
        page += 1
    return total

def sync_produits(token, shop_id, conn):
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
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO produits (id_wizi, sku, nom, fournisseur, reference_fournisseur,
                    marque, ean13, prix_vente_ht, prix_achat_ht, tva_pct, poids,
                    reduction, statut, image_url, url)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id_wizi) DO UPDATE SET
                    sku = EXCLUDED.sku, nom = EXCLUDED.nom,
                    fournisseur = EXCLUDED.fournisseur,
                    prix_vente_ht = EXCLUDED.prix_vente_ht,
                    prix_achat_ht = EXCLUDED.prix_achat_ht,
                    statut = EXCLUDED.statut, updated_at = NOW()
            """, (
                prod.get("id"), prod.get("sku"), prod.get("name") or prod.get("label"),
                prod.get("supplier"), prod.get("supplier_reference"),
                prod.get("brand"), prod.get("ean13"),
                prod.get("price_tax_excluded"), prod.get("wholesale_price_tax_excluded"),
                prod.get("tax"), prod.get("weight"), prod.get("reduction"),
                prod.get("status") or ("visible" if prod.get("visible") else "hidden"),
                prod.get("image_url"), prod.get("url")
            ))
            conn.commit()
            cur.close()
            total += 1
            time.sleep(0.05)
        if page >= data.get("pages", 1):
            break
        page += 1
    return total

def sync_skus(token, shop_id, conn):
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
        cur = conn.cursor()
        for s in results:
            cur.execute("""
                INSERT INTO skus (sku, id_produit_parent, type, ean13, stock, statut,
                    date_creation, date_maj_stock)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sku) DO UPDATE SET
                    stock = EXCLUDED.stock, statut = EXCLUDED.statut,
                    date_maj_stock = EXCLUDED.date_maj_stock, updated_at = NOW()
            """, (
                s.get("sku"), s.get("prod_id"), s.get("type"), s.get("ean13"),
                s.get("stock") or 0, s.get("status"),
                s.get("created_at"), s.get("updated_at")
            ))
            total += 1
        conn.commit()
        cur.close()
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

def sync_commandes(token, shop_id, conn):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    cur = conn.cursor()
    cur.execute("SELECT MAX(id_wizi) FROM commandes WHERE source = 'wizishop'")
    result = cur.fetchone()
    depuis_id = result[0] if result and result[0] else 0
    cur.close()

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
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO commandes (
                    id_wizi, numero_commande, date_commande, statut_code, statut_texte,
                    devise, montant_ttc, montant_ht, montant_produits_ttc, frais_port,
                    remise, code_promo, frais_supplementaires, mode_paiement,
                    type_paiement, libelle_paiement, numero_transaction, numero_facture,
                    url_facture, poids_total, origine, tag, commentaire, id_client,
                    civilite_facturation, prenom_facturation, nom_facturation,
                    email_client, telephone_facturation, societe_facturation,
                    adresse_facturation, cp_facturation, ville_facturation,
                    pays_facturation, pays_facturation_iso,
                    prenom_livraison, nom_livraison, telephone_livraison,
                    adresse_livraison, cp_livraison, ville_livraison,
                    pays_livraison, pays_livraison_iso,
                    mode_transport, nom_transporteur, numero_suivi,
                    emballage_cadeau, message_cadeau, zone_tva
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                ON CONFLICT (id_wizi) DO UPDATE SET
                    statut_code = EXCLUDED.statut_code,
                    statut_texte = EXCLUDED.statut_texte,
                    numero_suivi = EXCLUDED.numero_suivi,
                    updated_at = NOW()
            """, (
                o.get("id"), o.get("public_id"), o.get("date"),
                o.get("status_code"), o.get("status_text"),
                o.get("currency"), o.get("total_amount"), o.get("total_amount_excl_tax"),
                o.get("total_products_amount"), o.get("total_shipping_amount"),
                o.get("total_reduc_amount"), o.get("discount_code"),
                o.get("total_fees"), o.get("payment_mode"),
                o.get("payment_type"), o.get("payment_label"),
                o.get("transaction_number"), o.get("invoice_id"),
                o.get("invoice_url"), o.get("weight"),
                o.get("origin"), o.get("tag"), o.get("comment"),
                o.get("customer_id"),
                bil.get("civility"), bil.get("firstname"), bil.get("lastname"),
                bil.get("email"), bil.get("phone"), bil.get("company"),
                bil.get("street"), bil.get("postal_code"), bil.get("town"),
                bil.get("country"), bil.get("country_iso"),
                shp.get("firstname"), shp.get("lastname"), shp.get("phone"),
                shp.get("street"), shp.get("postal_code"), shp.get("town"),
                shp.get("country"), shp.get("country_iso"),
                shipping.get("mode"), shipping.get("name"), shipping.get("tracking_number"),
                services.get("gift_wrap", False), services.get("message"),
                zone
            ))
            for sku_item in shipping.get("skus", []):
                cur.execute("""
                    INSERT INTO lignes_commande (
                        id_commande, sku, nom_produit, quantite,
                        prix_unitaire_ttc, tva, remise_produit, poids
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    o.get("id"), sku_item.get("sku"), sku_item.get("title"),
                    sku_item.get("quantity"), sku_item.get("price"),
                    sku_item.get("tax"), sku_item.get("total_discount"),
                    sku_item.get("weight")
                ))
            conn.commit()
            cur.close()
            total += 1
            time.sleep(0.05)
        if page >= data.get("pages", 1):
            break
        page += 1
    return total

def log_sync(conn, table, source, nb, statut, message, duree):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sync_log (table_name, source, nb_enregistrements, statut, message, duree_secondes)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (table, source, nb, statut, message, duree))
    conn.commit()
    cur.close()

def run_full_sync():
    token, account_id, shop_id = get_wizi_token()
    if not token:
        return False, "Erreur de connexion Wizishop"
    conn = get_db_connection()
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
            nb = fn(token, shop_id, conn)
            duree = time.time() - debut
            log_sync(conn, nom, "wizishop", nb, "success", f"{nb} enregistrements", duree)
            resultats[nom] = {"nb": nb, "duree": duree, "statut": "✓"}
        except Exception as e:
            duree = time.time() - debut
            log_sync(conn, nom, "wizishop", 0, "error", str(e), duree)
            resultats[nom] = {"nb": 0, "duree": duree, "statut": "✗", "erreur": str(e)}
    conn.close()
    return True, resultats
