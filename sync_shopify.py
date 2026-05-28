import time
import streamlit as st
from shopify_api import graphql_query
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

_ORDERS_QUERY = """
query GetOrders($cursor: String, $queryFilter: String) {
  orders(first: 250, after: $cursor, query: $queryFilter) {
    nodes {
      id
      legacyResourceId
      name
      number
      processedAt
      createdAt
      updatedAt
      cancelledAt
      displayFinancialStatus
      displayFulfillmentStatus
      totalPriceSet      { shopMoney { amount currencyCode } }
      subtotalPriceSet   { shopMoney { amount } }
      totalTaxSet        { shopMoney { amount } }
      totalShippingPriceSet { shopMoney { amount } }
      totalDiscountsSet  { shopMoney { amount } }
      currencyCode
      taxesIncluded
      test
      note
      tags
      sourceName
      email
      customer {
        id
        email
        firstName
        lastName
      }
      billingAddress {
        name
        address1
        city
        zip
        countryCodeV2
      }
      shippingAddress {
        name
        address1
        city
        zip
        countryCodeV2
      }
      lineItems(first: 100) {
        nodes {
          id
          title
          quantity
          sku
          taxable
          originalUnitPrice { amount }
          discountedUnitPrice { amount }
          totalDiscount { amount }
          variant { id }
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


# ── Sync commandes ────────────────────────────────────────────────────────────

def sync_shopify_commandes(boutique, shop, token, since_date="2025-01-01"):
    """
    Synchronise toutes les commandes depuis since_date pour la boutique donnée.
    Retourne nb_commandes.
    """
    nb_commandes = 0
    cursor = None
    query_filter = f"created_at:>={since_date}"

    while True:
        variables = {"cursor": cursor, "queryFilter": query_filter}
        r = _gql(shop, token, _ORDERS_QUERY, variables)
        if r.status_code != 200:
            st.warning(f"[sync_shopify] Erreur GraphQL commandes {r.status_code}: {r.text[:200]}")
            break

        body = r.json()
        if body.get("errors"):
            st.warning(f"[sync_shopify] Erreurs GraphQL commandes: {body['errors']}")
            break

        data = body.get("data", {}).get("orders", {})
        nodes = data.get("nodes", [])

        for order in nodes:
            order_id = order["id"]
            billing  = order.get("billingAddress") or {}
            shipping = order.get("shippingAddress") or {}
            customer = order.get("customer") or {}

            upsert("commandes_shopify", [{
                "id_shopify":           order_id,
                "boutique":             boutique,
                "legacy_id":            _legacy_int(order.get("legacyResourceId")),
                "numero":               order.get("name"),
                "numero_seq":           order.get("number"),
                "cree_le":              order.get("createdAt"),
                "traite_le":            order.get("processedAt"),
                "mis_a_jour_le":        order.get("updatedAt"),
                "annule_le":            order.get("cancelledAt"),
                "statut_financier":     order.get("displayFinancialStatus"),
                "statut_livraison":     order.get("displayFulfillmentStatus"),
                "montant_ttc":          _money(order.get("totalPriceSet")),
                "montant_ht_approx":    _money(order.get("subtotalPriceSet")),
                "montant_taxes":        _money(order.get("totalTaxSet")),
                "frais_port":           _money(order.get("totalShippingPriceSet")),
                "montant_remises":      _money(order.get("totalDiscountsSet")),
                "devise":               order.get("currencyCode"),
                "taxes_incluses":       order.get("taxesIncluded"),
                "commande_test":        order.get("test", False),
                "source":               order.get("sourceName"),
                "note":                 order.get("note"),
                "tags":                 order.get("tags") or [],
                "email_client":         order.get("email") or customer.get("email"),
                "id_client_shopify":    customer.get("id"),
                "prenom_client":        customer.get("firstName"),
                "nom_client":           customer.get("lastName"),
                "nom_facturation":      billing.get("name"),
                "adresse_facturation":  billing.get("address1"),
                "ville_facturation":    billing.get("city"),
                "cp_facturation":       billing.get("zip"),
                "pays_facturation_iso": billing.get("countryCodeV2"),
                "nom_livraison":        shipping.get("name"),
                "adresse_livraison":    shipping.get("address1"),
                "ville_livraison":      shipping.get("city"),
                "cp_livraison":         shipping.get("zip"),
                "pays_livraison_iso":   shipping.get("countryCodeV2"),
            }], "id_shopify,boutique")

            lignes = []
            for item in (order.get("lineItems") or {}).get("nodes", []):
                lignes.append({
                    "id_shopify":             item["id"],
                    "boutique":               boutique,
                    "id_commande_shopify":    order_id,
                    "id_variant_shopify":     (item.get("variant") or {}).get("id"),
                    "sku":                    item.get("sku"),
                    "titre":                  item.get("title"),
                    "quantite":               item.get("quantity"),
                    "prix_unitaire_original": _money(item.get("originalUnitPrice")),
                    "prix_unitaire_remise":   _money(item.get("discountedUnitPrice")),
                    "total_remise":           _money(item.get("totalDiscount")),
                    "taxable":                item.get("taxable"),
                })

            if lignes:
                upsert("lignes_commande_shopify", lignes, "id_shopify,boutique")

            nb_commandes += 1

        page_info = data.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        time.sleep(0.3)

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
