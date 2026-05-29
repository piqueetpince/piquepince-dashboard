import time
import streamlit as st
from shopify_api import graphql_query, api_get, _next_page_info
from supabase_api import upsert

# ── GraphQL queries ───────────────────────────────────────────────────────────

_PRODUCTS_QUERY = """
query GetProducts($cursor: String) {
  products(first: 250, after: $cursor) {
    nodes {
      id
      legacyResourceId
      handle
      title
      status
      productType
      vendor
      tags
      totalInventory
      publishedAt
      createdAt
      updatedAt
      variants(first: 100) {
        nodes {
          id
          legacyResourceId
          sku
          title
          displayName
          price
          compareAtPrice
          inventoryQuantity
          availableForSale
          barcode
          position
          taxable
          createdAt
          updatedAt
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""



# ── Helper bas niveau ─────────────────────────────────────────────────────────

def _gql(shop, token, query, variables=None):
    """graphql_query avec support des variables."""
    import requests
    from shopify_api import SHOPIFY_API_VERSION, _get_headers
    url = f"https://{shop}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    return requests.post(url, headers=_get_headers(token), json=payload)


def _money(obj):
    """Extrait le montant float depuis un objet MoneyBag.shopMoney ou MoneyV2."""
    if obj is None:
        return None
    shop = obj.get("shopMoney") or obj
    try:
        return float(shop.get("amount", 0) or 0)
    except (TypeError, ValueError):
        return None


def _legacy_int(gid_str):
    """Extrait l'entier depuis un legacyResourceId (déjà numérique) ou un GID."""
    if gid_str is None:
        return None
    try:
        return int(gid_str)
    except (ValueError, TypeError):
        # Fallback si c'est un GID complet "gid://shopify/X/123"
        try:
            return int(str(gid_str).rsplit("/", 1)[-1])
        except (ValueError, TypeError):
            return None


# ── Sync produits ─────────────────────────────────────────────────────────────

def sync_shopify_produits(boutique, shop, token):
    """
    Synchronise tous les produits (ACTIVE + ARCHIVED + DRAFT) et leurs variantes
    pour la boutique donnée.
    Retourne (nb_produits, nb_variants).
    """
    nb_produits = 0
    nb_variants = 0
    cursor = None

    while True:
        variables = {"cursor": cursor}
        r = _gql(shop, token, _PRODUCTS_QUERY, variables)
        if r.status_code != 200:
            st.warning(f"[sync_shopify] Erreur GraphQL produits {r.status_code}: {r.text[:200]}")
            break

        body = r.json()
        if body.get("errors"):
            st.warning(f"[sync_shopify] Erreurs GraphQL produits: {body['errors']}")
            break

        data = body.get("data", {}).get("products", {})
        nodes = data.get("nodes", [])

        for product in nodes:
            prod_id = product["id"]

            upsert("produits_shopify", [{
                "id_shopify":    prod_id,
                "boutique":      boutique,
                "legacy_id":     _legacy_int(product.get("legacyResourceId")),
                "handle":        product.get("handle"),
                "titre":         product.get("title"),
                "statut":        product.get("status"),
                "type_produit":  product.get("productType"),
                "fournisseur":   product.get("vendor"),
                "tags":          product.get("tags") or [],
                "total_stock":   product.get("totalInventory"),
                "publie_le":     product.get("publishedAt"),
                "cree_le":       product.get("createdAt"),
                "mis_a_jour_le": product.get("updatedAt"),
            }], "id_shopify,boutique")
            nb_produits += 1

            for variant in (product.get("variants") or {}).get("nodes", []):
                upsert("produits_shopify_variants", [{
                    "id_shopify":         variant["id"],
                    "boutique":           boutique,
                    "legacy_id":          _legacy_int(variant.get("legacyResourceId")),
                    "id_produit_shopify": prod_id,
                    "sku":                variant.get("sku"),
                    "titre":              variant.get("title"),
                    "nom_complet":        variant.get("displayName"),
                    "prix":               float(variant["price"]) if variant.get("price") else None,
                    "prix_compare":       float(variant["compareAtPrice"]) if variant.get("compareAtPrice") else None,
                    "stock":              variant.get("inventoryQuantity"),
                    "disponible":         variant.get("availableForSale"),
                    "code_barre":         variant.get("barcode"),
                    "position":           variant.get("position"),
                    "taxable":            variant.get("taxable"),
                    "cree_le":            variant.get("createdAt"),
                    "mis_a_jour_le":      variant.get("updatedAt"),
                }], "id_shopify,boutique")
                nb_variants += 1

        page_info = data.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        time.sleep(0.3)

    return nb_produits, nb_variants


# ── Sync commandes (REST — pas de limite 60 jours) ───────────────────────────

def sync_shopify_commandes(boutique, shop, token, since_date="2025-01-01"):
    """
    Synchronise toutes les commandes depuis since_date via l'API REST.
    L'API REST avec status=any n'a pas la limite 60 jours de l'API GraphQL.
    Retourne nb_commandes.
    """
    nb_commandes = 0
    nb_pages = 0
    debug_first_done = False

    if "T" not in since_date:
        since_date = f"{since_date}T00:00:00Z"

    # DEBUG — paramètres utilisés
    st.write(f"[DEBUG] Endpoint REST orders.json | created_at_min={since_date} | status=any")

    # DEBUG — total via /orders/count.json avant de paginer
    r_count = api_get(shop, token, "orders/count.json", {
        "status": "any", "created_at_min": since_date
    })
    if r_count.status_code == 200:
        total_api = r_count.json().get("count", "?")
        st.write(f"[DEBUG] orders/count.json → {total_api} commandes attendues")
    else:
        st.write(f"[DEBUG] orders/count.json → erreur {r_count.status_code}: {r_count.text[:100]}")

    params = {
        "status":              "any",
        "fulfillment_status":  "any",
        "created_at_min":      since_date,
        "limit":               250,
        "fields": (
            "id,name,order_number,created_at,processed_at,updated_at,cancelled_at,"
            "financial_status,fulfillment_status,total_price,subtotal_price,total_tax,"
            "total_shipping_price_set,total_discounts,currency,taxes_included,test,"
            "note,tags,source_name,email,customer,billing_address,shipping_address,line_items"
        ),
    }

    while True:
        r = api_get(shop, token, "orders.json", params)
        if r.status_code != 200:
            st.warning(f"[sync_shopify] Erreur REST commandes {r.status_code}: {r.text[:200]}")
            break

        orders = r.json().get("orders", [])
        nb_pages += 1
        link_header = r.headers.get("Link", "")
        rate_limit  = r.headers.get("X-Shopify-Shop-Api-Call-Limit", "?")

        # DEBUG — infos de la page courante
        parsed_page_info = _next_page_info(link_header)
        st.write(f"[DEBUG] Page {nb_pages} — {len(orders)} commandes | API call limit: {rate_limit}")
        st.code(f"Link header brut : {repr(link_header)}\npage_info parsé  : {repr(parsed_page_info)}")

        for order in orders:
            order_id   = str(order["id"])
            legacy_id  = int(order["id"])
            billing    = order.get("billing_address") or {}
            shipping   = order.get("shipping_address") or {}
            customer   = order.get("customer") or {}
            tags_raw   = order.get("tags", "") or ""
            tags_list  = [t.strip() for t in tags_raw.split(",") if t.strip()]
            ship_set   = (order.get("total_shipping_price_set") or {}).get("shop_money") or {}

            # DEBUG — première commande uniquement
            if not debug_first_done:
                st.write(f"[DEBUG] 1ère commande → id_shopify={order_id!r} (str) | "
                         f"legacy_id={legacy_id!r} (int) | "
                         f"on_conflict='id_shopify,boutique'")
                debug_first_done = True

            upsert("commandes_shopify", [{
                "id_shopify":           order_id,
                "boutique":             boutique,
                "legacy_id":            legacy_id,
                "numero":               order.get("name"),
                "numero_seq":           order.get("order_number"),
                "cree_le":              order.get("created_at"),
                "traite_le":            order.get("processed_at"),
                "mis_a_jour_le":        order.get("updated_at"),
                "annule_le":            order.get("cancelled_at"),
                "statut_financier":     order.get("financial_status"),
                "statut_livraison":     order.get("fulfillment_status"),
                "montant_ttc":          float(order["total_price"])    if order.get("total_price")    else None,
                "montant_ht_approx":    float(order["subtotal_price"]) if order.get("subtotal_price") else None,
                "montant_taxes":        float(order["total_tax"])      if order.get("total_tax")      else None,
                "frais_port":           float(ship_set["amount"])      if ship_set.get("amount")      else None,
                "montant_remises":      float(order["total_discounts"]) if order.get("total_discounts") else None,
                "devise":               order.get("currency"),
                "taxes_incluses":       order.get("taxes_included"),
                "commande_test":        order.get("test", False),
                "source":               order.get("source_name"),
                "note":                 order.get("note"),
                "tags":                 tags_list,
                "email_client":         order.get("email") or customer.get("email"),
                "id_client_shopify":    str(customer["id"]) if customer.get("id") else None,
                "prenom_client":        customer.get("first_name"),
                "nom_client":           customer.get("last_name"),
                "nom_facturation":      billing.get("name"),
                "adresse_facturation":  billing.get("address1"),
                "ville_facturation":    billing.get("city"),
                "cp_facturation":       billing.get("zip"),
                "pays_facturation_iso": billing.get("country_code"),
                "nom_livraison":        shipping.get("name"),
                "adresse_livraison":    shipping.get("address1"),
                "ville_livraison":      shipping.get("city"),
                "cp_livraison":         shipping.get("zip"),
                "pays_livraison_iso":   shipping.get("country_code"),
            }], "id_shopify,boutique")

            lignes = []
            for item in order.get("line_items", []):
                lignes.append({
                    "id_shopify":             str(item["id"]),
                    "boutique":               boutique,
                    "id_commande_shopify":    order_id,
                    "id_variant_shopify":     str(item["variant_id"]) if item.get("variant_id") else None,
                    "sku":                    item.get("sku"),
                    "titre":                  item.get("title"),
                    "quantite":               item.get("quantity"),
                    "prix_unitaire_original": float(item["price"])          if item.get("price")          else None,
                    "prix_unitaire_remise":   None,
                    "total_remise":           float(item["total_discount"]) if item.get("total_discount") else None,
                    "taxable":                item.get("taxable"),
                })

            if lignes:
                upsert("lignes_commande_shopify", lignes, "id_shopify,boutique")

            nb_commandes += 1

        if not parsed_page_info or not orders:
            break
        params = {"limit": 250, "page_info": parsed_page_info}
        time.sleep(0.3)

    # DEBUG — bilan
    st.write(f"[DEBUG] Total : {nb_commandes} commandes en {nb_pages} page(s)")

    return nb_commandes


# ── Log ───────────────────────────────────────────────────────────────────────

def log_sync_shopify(boutique, table, nb, statut, message, duree):
    upsert("sync_log", [{
        "table_name":          table,
        "source":              f"shopify_{boutique}",
        "nb_enregistrements":  nb,
        "statut":              statut,
        "message":             message,
        "duree_secondes":      round(duree, 2),
    }], "id")
