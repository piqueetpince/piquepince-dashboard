import streamlit as st
import time
from supabase_api import upsert, select
from etsy_api import api_get

ETSY_API_URL = "https://openapi.etsy.com/v3"

def get_all_listings(shop_id, state="active"):
    all_listings = []
    offset = 0
    limit = 100
    while True:
        r = api_get(
            f"{ETSY_API_URL}/application/shops/{shop_id}/listings",
            params={"state": state, "limit": limit, "offset": offset}
        )
        if r.status_code != 200:
            break
        data = r.json()
        results = data.get("results", [])
        if not results:
            break
        all_listings.extend(results)
        if offset + limit >= data.get("count", 0):
            break
        offset += limit
        time.sleep(0.1)
    return all_listings

def get_listing_inventory(listing_id):
    r = api_get(f"{ETSY_API_URL}/application/listings/{listing_id}/inventory")
    if r.status_code == 200:
        return r.json().get("products", [])
    return []

def sync_produits_etsy(shop_id):
    listings_actifs = get_all_listings(shop_id, state="active")
    listings_inactifs = get_all_listings(shop_id, state="inactive")
    listings = listings_actifs + listings_inactifs
    total_listings = 0
    total_variations = 0

    skus_data = select("skus", "select=sku,stock&statut=eq.visible")
    sku_stock_map = {s["sku"]: int(s["stock"] or 0) for s in skus_data} if skus_data else {}

    for listing in listings:
        listing_id = listing.get("listing_id")
        listing_active = listing.get("state") == "active"
        prix = listing.get("price", {})
        divisor = prix.get("divisor", 100) or 100

        upsert("produits_etsy", [{
            "listing_id": listing_id,
            "titre": listing.get("title"),
            "statut": listing.get("state"),
            "stock_total": listing.get("quantity", 0),
            "prix": prix.get("amount", 0) / divisor,
            "has_variations": listing.get("has_variations", False),
            "url": listing.get("url"),
            "tags": listing.get("tags", []),
            "nb_favoris": listing.get("num_favorers", 0),
            "shop_section_id": listing.get("shop_section_id"),
            "date_creation": None,
            "date_maj": None,
            "source": "etsy"
        }], "listing_id")
        total_listings += 1

        if listing.get("has_variations"):
            products = get_listing_inventory(listing_id)

            # Grouper par SKU : somme des stocks, is_enabled si au moins un offering actif
            sku_data = {}
            for product in products:
                sku = product.get("sku") or ""
                if not sku:
                    continue

                variation_valeur = ""
                prop_values = product.get("property_values", [])
                if prop_values:
                    variation_valeur = " / ".join([
                        v for pv in prop_values
                        for v in pv.get("values", [])
                    ])

                for offering in product.get("offerings", []):
                    qty = offering.get("quantity", 0)
                    enabled = listing_active and offering.get("is_enabled", False)
                    prix_var = offering.get("price", {})
                    divisor_var = prix_var.get("divisor", 100) or 100
                    prix_amount = prix_var.get("amount", 0) / divisor_var

                    if sku not in sku_data:
                        sku_data[sku] = {
                            "stock": 0,
                            "is_enabled": False,
                            "variation_valeur": variation_valeur,
                            "prix": prix_amount,
                        }
                    sku_data[sku]["stock"] += qty
                    if enabled:
                        sku_data[sku]["is_enabled"] = True

            for sku, data in sku_data.items():
                stock_wizishop = sku_stock_map.get(sku, 0)
                alerte = data["is_enabled"] and stock_wizishop == 0

                upsert("produits_etsy_variations", [{
                    "listing_id": listing_id,
                    "sku": sku,
                    "prix": data["prix"],
                    "stock_etsy": data["stock"],
                    "is_enabled": data["is_enabled"],
                    "variation_valeur": data["variation_valeur"],
                    "stock_wizishop": stock_wizishop,
                    "alerte_stock": alerte,
                }], "listing_id,sku")
                total_variations += 1

            time.sleep(0.1)
        else:
            skus_listing = listing.get("skus", [])
            for sku in skus_listing:
                stock_wizishop = sku_stock_map.get(sku, 0)
                is_enabled = listing_active
                alerte = is_enabled and stock_wizishop == 0

                upsert("produits_etsy_variations", [{
                    "listing_id": listing_id,
                    "sku": sku,
                    "prix": prix.get("amount", 0) / divisor,
                    "stock_etsy": listing.get("quantity", 0),
                    "is_enabled": is_enabled,
                    "variation_valeur": "—",
                    "stock_wizishop": stock_wizishop,
                    "alerte_stock": alerte,
                }], "listing_id,sku")
                total_variations += 1

    return total_listings, total_variations
