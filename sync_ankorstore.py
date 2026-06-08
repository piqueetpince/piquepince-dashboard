import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# ankorstore_api injecte le mock Streamlit si nécessaire — doit être importé en premier
from ankorstore_api import api_get, get_headers  # noqa: E402 (mock injection side-effect)

import streamlit as st
from supabase_api import upsert, upsert_ignore, select, delete

import requests

# ── Helpers JSON:API ──────────────────────────────────────────────────────────

def _included_map(included: list) -> dict:
    """Construit un index {(type, id): item} depuis le tableau included."""
    result = {}
    for item in (included or []):
        key = (item.get("type"), item.get("id"))
        result[key] = item
    return result


def _cents(val) -> float:
    """Convertit des centimes (int) en euros (float)."""
    try:
        return float(val or 0) / 100
    except (TypeError, ValueError):
        return 0.0


def _get_all_pages_with_included(endpoint: str, params: dict = None):
    """
    Parcourt toutes les pages en préservant le tableau included.
    Retourne (all_data, all_included).
    """
    all_data     = []
    all_included = []
    next_url     = f"https://www.ankorstore.com{endpoint}" if not endpoint.startswith("http") else endpoint
    current_params = params or {}

    while next_url:
        r = requests.get(next_url, headers=get_headers(),
                         params=current_params, timeout=30)
        if r.status_code != 200:
            st.warning(f"[Ankorstore] Erreur {r.status_code} sur {next_url}: {r.text[:200]}")
            break
        body = r.json()
        page_data = body.get("data", [])
        if isinstance(page_data, list):
            all_data.extend(page_data)
        elif isinstance(page_data, dict):
            all_data.append(page_data)
        all_included.extend(body.get("included") or [])
        next_url = (body.get("links") or {}).get("next") or None
        current_params = {}  # curseur déjà encodé dans next_url

    return all_data, all_included


# ── Reprise depuis la dernière date ───────────────────────────────────────────

def _get_max_ankorstore_date() -> Optional[datetime]:
    rows = select(
        "commandes",
        "select=date_commande&source=eq.ankorstore"
        "&date_commande=not.is.null&order=date_commande.desc&limit=1",
    )
    if rows and rows[0].get("date_commande"):
        return datetime.fromisoformat(
            rows[0]["date_commande"].replace("Z", "+00:00"))
    return None


# ── Sync produits ─────────────────────────────────────────────────────────────

def sync_ankorstore_produits():
    """
    Synchronise tous les produits et leurs variants depuis Ankorstore.
    Deux appels distincts : /api/v1/products puis /api/v1/product-variants.
    Retourne (nb_produits, nb_variants).
    """
    # ── Passe 1 : produits ────────────────────────────────────────────────────
    products, _ = _get_all_pages_with_included("/api/v1/products")

    # ── Passe 2 : variants ────────────────────────────────────────────────────
    variants, _ = _get_all_pages_with_included(
        "/api/v1/product-variants", {"include": "product"})

    nb_produits = 0

    for product in products:
        prod_id   = product.get("id")
        prod_attr = product.get("attributes") or {}

        if prod_attr.get("archived") == True:
            continue

        upsert("produits_ankorstore", [{
            "id_ankorstore":         prod_id,
            "nom":                   prod_attr.get("name"),
            "active":                prod_attr.get("active"),
            "archived":              prod_attr.get("archived"),
            "created_at_ankorstore": prod_attr.get("createdAt"),
        }], "id_ankorstore")
        nb_produits += 1

    nb_variants = 0

    for variant in variants:
        v_attr   = variant.get("attributes") or {}

        if v_attr.get("archivedAt") is not None:
            continue

        prod_ref = (
            (variant.get("relationships") or {})
            .get("product", {})
            .get("data") or {}
        )

        upsert("produits_ankorstore_variants", [{
            "id_ankorstore":         variant.get("id"),
            "id_produit_ankorstore": prod_ref.get("id"),
            "sku":                   v_attr.get("sku"),
            "nom":                   v_attr.get("name"),
            "stock":                 v_attr.get("availableQuantity"),
            "prix_grossiste":        _cents(v_attr.get("wholesalePrice")),
            "prix_vente_conseille":  _cents(v_attr.get("retailPrice")),
            "archived":              v_attr.get("archivedAt") is not None,
        }], "id_ankorstore")
        nb_variants += 1

    # ── Nettoyage : produits absents de l'API (archivés ou supprimés) ────────
    # prod_ids ne contient que les produits non-archivés — not.in. couvre donc
    # à la fois les produits archivés et les produits définitivement supprimés.
    prod_ids_str  = ",".join(p.get("id") for p in products
                             if p.get("id") and not (p.get("attributes") or {}).get("archived"))
    nb_prod_suppr = 0
    nb_var_suppr  = 0

    if prod_ids_str:
        a_supprimer = select("produits_ankorstore",
            f"select=id_ankorstore&id_ankorstore=not.in.({prod_ids_str})")
        if a_supprimer:
            nb_prod_suppr = len(a_supprimer)
            delete("produits_ankorstore", f"id_ankorstore=not.in.({prod_ids_str})")

        orphelins = select("produits_ankorstore_variants",
            f"select=id_ankorstore&id_produit_ankorstore=not.in.({prod_ids_str})")
        if orphelins:
            nb_var_suppr = len(orphelins)
            delete("produits_ankorstore_variants",
                   f"id_produit_ankorstore=not.in.({prod_ids_str})")

    print(f"  → {nb_produits} produits, {nb_variants} variants synchronisés"
          f" | supprimés : {nb_prod_suppr} produits, {nb_var_suppr} variants")
    return nb_produits, nb_variants


# ── Sync commandes ────────────────────────────────────────────────────────────

def sync_ankorstore_commandes():
    """
    Synchronise toutes les commandes et leurs lignes depuis Ankorstore.
    Pas de filtre date disponible — pagination complète + upsert (idempotent).
    Retourne (nb_commandes, nb_lignes).
    """
    orders, included = _get_all_pages_with_included(
        "/api/v1/orders",
        params={"include": "orderItems.productVariant"},
    )
    inc_map      = _included_map(included)
    nb_commandes = 0
    nb_lignes    = 0

    for order in orders:
        order_id   = order.get("id")
        order_attr = order.get("attributes") or {}

        upsert("commandes", [{
            "id_ankorstore": order_id,
            "source":        "ankorstore",
            "date_commande": order_attr.get("submittedAt"),
            "statut_texte":  order_attr.get("status"),
            "montant_ttc":   _cents(order_attr.get("brandTotalAmountWithVat")),
            "montant_ht":    _cents(order_attr.get("brandTotalAmount")),
        }], "id_ankorstore")
        nb_commandes += 1

        # Lignes : orderItems dans included[], liés à la commande via relationships
        item_refs = (
            (order.get("relationships") or {})
            .get("orderItems", {})
            .get("data", [])
        )
        lignes = []
        for ref in item_refs:
            item = inc_map.get((ref.get("type"), ref.get("id")))
            if not item:
                continue
            item_attr = item.get("attributes") or {}

            variant_ref = (
                (item.get("relationships") or {})
                .get("productVariant", {})
                .get("data") or {}
            )
            variant = inc_map.get((variant_ref.get("type"), variant_ref.get("id"))) or {}
            v_attr  = variant.get("attributes") or {}

            lignes.append({
                "id_ankorstore":     item.get("id"),
                "id_commande":       order_id,
                "source":            "ankorstore",
                "sku":               v_attr.get("sku"),
                "nom_produit":       v_attr.get("name"),
                "quantite":          item_attr.get("quantity"),
                "prix_unitaire_ttc": _cents(item_attr.get("brandUnitPrice")),
            })

        if lignes:
            upsert_ignore("lignes_commande", lignes, "id_ankorstore")
            nb_lignes += len(lignes)

        time.sleep(0.05)

    print(f"  → {nb_commandes} commandes, {nb_lignes} lignes synchronisées")
    return nb_commandes, nb_lignes


# ── Sync stock ───────────────────────────────────────────────────────────────

def sync_ankorstore_stock():
    """
    Pousse le stock Wizishop vers Ankorstore pour chaque variant dont le SKU
    existe dans la table skus. Ne met à jour que si le stock diffère.
    Retourne (nb_mis_a_jour, nb_erreurs, nb_sku_inconnus).
    """
    # Variants Ankorstore avec stock actuel
    ank_variants = select(
        "produits_ankorstore_variants",
        "select=id_ankorstore,sku,stock&sku=not.is.null",
    )
    if not ank_variants:
        print("  → Aucun variant Ankorstore en base.")
        return 0, 0, 0

    # Stock Wizishop (tous les SKUs visibles)
    wizi_skus = select("skus", "select=sku,stock&statut=eq.visible")
    stock_wizi_map = {
        r["sku"]: int(r.get("stock") or 0)
        for r in (wizi_skus or [])
    }

    nb_maj      = 0
    nb_erreurs  = 0
    nb_inconnus = 0

    patch_headers = {
        **get_headers(),
        "Content-Type": "application/vnd.api+json",
    }

    for variant in ank_variants:
        variant_id  = variant.get("id_ankorstore")
        sku         = (variant.get("sku") or "").strip()
        stock_ank   = int(variant.get("stock") or 0)

        if sku not in stock_wizi_map:
            nb_inconnus += 1
            continue

        stock_wizi = stock_wizi_map[sku]

        if stock_wizi == stock_ank:
            continue  # pas de changement — évite l'appel inutile

        url = f"https://www.ankorstore.com/api/v1/product-variants/{variant_id}/stock"
        body = {
            "data": {
                "type":       "product-variants",
                "id":         variant_id,
                "attributes": {"stockQuantity": stock_wizi},
            }
        }
        r = requests.patch(url, headers=patch_headers, json=body, timeout=15)

        if r.status_code in (200, 204):
            nb_maj += 1
            # Met à jour le stock local pour éviter les recalculs si appelé en boucle
            upsert("produits_ankorstore_variants", [{
                "id_ankorstore": variant_id,
                "stock":         stock_wizi,
            }], "id_ankorstore")
        else:
            nb_erreurs += 1
            print(f"  ⚠️  Erreur stock {sku} ({variant_id}): "
                  f"HTTP {r.status_code} — {r.text[:150]}")

        time.sleep(0.1)  # respecte le rate-limit Ankorstore

    print(f"  → {nb_maj} variants mis à jour, "
          f"{nb_erreurs} erreur(s), "
          f"{nb_inconnus} SKU(s) non trouvés dans Wizishop")
    return nb_maj, nb_erreurs, nb_inconnus


# ── Log ───────────────────────────────────────────────────────────────────────

def log_sync_ankorstore(table: str, nb: int, statut: str, message: str, duree: float):
    upsert("sync_log", [{
        "table_name":         table,
        "source":             "ankorstore",
        "nb_enregistrements": nb,
        "statut":             statut,
        "message":            message,
        "duree_secondes":     round(duree, 2),
    }], "id")
