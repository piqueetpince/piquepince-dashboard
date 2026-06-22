"""
Crée les listings Etsy en DRAFT à partir du fichier Revised_65th_PiqueetPince.docx.

Pour chaque listing du docx (SKU, titre optimisé, tags, description) :
  1. Si le SKU est un SKU "parent" (plusieurs variations en base) → récupère les
     variations (sku, prix_vente_ht, image_url) depuis Supabase (produits + skus).
  2. Si le SKU est déjà une variation/produit simple → l'utilise directement.
  3. Crée le listing Etsy en DRAFT (titre, description, tags, prix = prix_vente_ht × 1.2).
  4. Met à jour l'inventaire Etsy avec les variations (sku, stock, prix).
  5. Uploade les images depuis les image_url Wizishop.

Affiche un résumé complet (avec avertissements/erreurs par listing) et demande
une confirmation avant de créer quoi que ce soit sur Etsy.

Usage:
    python create_etsy_listings.py              # résumé puis confirmation interactive
    python create_etsy_listings.py --dry-run    # résumé uniquement, aucun appel Etsy
    python create_etsy_listings.py --yes        # saute la confirmation interactive
    python create_etsy_listings.py --listing 1       # uniquement le listing #1
    python create_etsy_listings.py --listing BAR0020 # uniquement ce SKU
    python create_etsy_listings.py --listing 1-5     # les listings #1 à #5

Credentials lus depuis .env : SUPABASE_URL, SUPABASE_KEY,
ETSY_API_KEY, ETSY_SHARED_SECRET, ETSY_REFRESH_TOKEN, ETSY_ACCESS_TOKEN, ETSY_SHOP_ID
"""

import argparse
import os
import re
import sys
import time
from contextlib import contextmanager

import requests
from docx import Document
from dotenv import load_dotenv

load_dotenv()

DOCX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "Revised_65th_PiqueetPince.docx")

# ── Mock Streamlit (identique à sync_nuit.py) ─────────────────────────────────
# Les modules etsy_api / supabase_api appellent st.secrets, st.warning, etc.
# On injecte un objet qui lit depuis os.environ et imprime en console à la place.

class _Secrets:
    def __getitem__(self, key):
        val = os.environ.get(key)
        if val is None:
            raise KeyError(f"Secret manquant : {key!r} — vérifie ton .env")
        return val
    def get(self, key, default=None):
        return os.environ.get(key, default)
    def __contains__(self, key):
        return key in os.environ


class _SessionState(dict):
    pass


@contextmanager
def _noop_spinner(text=""):
    yield


class _MockSt:
    secrets       = _Secrets()
    session_state = _SessionState()

    def warning(self, msg):      print(f"    ⚠️  {msg}")
    def error(self, msg):        print(f"    ❌ {msg}")
    def success(self, msg):      print(f"    ✅ {msg}")
    def info(self, msg):         print(f"    ℹ️  {msg}")
    def write(self, *a, **kw):   pass
    def subheader(self, *a):     pass
    def spinner(self, text=""):  return _noop_spinner(text)

    def cache_data(self, **kwargs):
        def decorator(fn): return fn
        return decorator


sys.modules["streamlit"] = _MockSt()  # type: ignore

# ── Imports après injection du mock ───────────────────────────────────────────

import etsy_api
from supabase_api import select

ETSY_API_URL = "https://openapi.etsy.com/v3"

# ── Défauts de listing, dérivés de listings existants sur la boutique ────────
# (taxonomy_id / shop_section_id par catégorie Wizishop, reste commun à la boutique)

CATEGORY_DEFAULTS = {
    "Barrettes Classiques FAIT MAIN":        {"taxonomy_id": 220, "shop_section_id": 53379850},
    "Pinces à cheveux":                      {"taxonomy_id": 220, "shop_section_id": 56681282},
    "Peignes cheveux":                       {"taxonomy_id": 222, "shop_section_id": 53461606},
    "Pics à cheveux en acétate de cellulose": {"taxonomy_id": 224, "shop_section_id": 53518333},
}
DEFAULT_TAXONOMY_ID = 220  # repli si la catégorie Wizishop n'est pas reconnue

SHIPPING_PROFILE_ID = 267762107187
RETURN_POLICY_ID    = 1359607938491
WHO_MADE             = "i_did"
WHEN_MADE            = "2020_2026"
PROCESSING_MIN       = 1
PROCESSING_MAX       = 2
# Profil de traitement "1-2 days" de la boutique (Etsy l'exige pour les
# listings physiques — cf GET /shops/{shop_id}/readiness-state-definitions).
READINESS_STATE_ID   = 1405123732603

MAX_TAG_LEN   = 20
MAX_TAGS      = 13
MAX_TITLE_LEN = 140
MAX_IMAGES    = 10


# ── Parsing du docx ────────────────────────────────────────────────────────────

def _next_nonempty(paras, start, end):
    for i in range(start, end):
        if paras[i].strip():
            return paras[i].strip()
    return ""


def parse_docx(path):
    """Découpe le docx en blocs 'LISTING # N' et extrait sku/title/tags/description."""
    doc   = Document(path)
    paras = [p.text for p in doc.paragraphs]

    listing_idxs = [i for i, t in enumerate(paras) if t.strip().startswith("LISTING #")]
    listing_idxs.append(len(paras))

    listings = []
    for n in range(len(listing_idxs) - 1):
        start, end = listing_idxs[n], listing_idxs[n + 1]

        sku, title, tags, description = None, "", [], ""

        for i in range(start, end):
            s = paras[i].strip()
            if sku is None and s.startswith("SKU"):
                m = re.search(r"SKU\s*:\s*(\S+)", s)
                if m:
                    sku = m.group(1)
            elif s.startswith("Optimized Title"):
                title = _next_nonempty(paras, i + 1, end)
            elif "Keyword Researched Tags" in s:
                tags_line = _next_nonempty(paras, i + 1, end)
                tags = [t.strip() for t in tags_line.split(",") if t.strip()]
            elif s.startswith("Description"):
                desc_paras  = [paras[j].strip() for j in range(i + 1, end) if paras[j].strip()]
                description = "\n\n".join(desc_paras)

        listings.append({
            "numero": n + 1, "sku": sku, "title": title,
            "tags": tags, "description": description,
        })
    return listings


def fix_repeated_ampersands(title):
    """Etsy n'autorise qu'une seule occurrence de '&' dans un titre. Garde le
    premier '&' et remplace les suivants par 'and'."""
    parts = title.split("&")
    if len(parts) <= 2:
        return title, False
    fixed = parts[0] + "&" + "and".join(parts[1:])
    return fixed, True


def filter_listings(listings, selector):
    """Filtre les listings parsés selon --listing : numéro ("1"), plage
    ("1-5") ou SKU ("BAR0020", insensible à la casse)."""
    if not selector:
        return listings

    s = selector.strip()
    m = re.match(r"^(\d+)-(\d+)$", s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return [l for l in listings if lo <= l["numero"] <= hi]

    if s.isdigit():
        n = int(s)
        return [l for l in listings if l["numero"] == n]

    return [l for l in listings if (l["sku"] or "").upper() == s.upper()]


# ── Classification SKU (parent / simple) via Supabase ─────────────────────────

def classify_sku(sku):
    """Retourne le 'kind' d'un SKU et les données nécessaires à la création.

    - 'missing' : le SKU n'existe pas dans la table produits.
    - 'parent'  : le SKU regroupe plusieurs variations (même id_wizi, même
                  préfixe de nom, prix > 0).
    - 'simple'  : le SKU est un produit/variation autonome (pas de famille).

    NB : id_wizi n'est pas un identifiant de famille fiable à 100 % — des
    produits totalement différents (ex. imports AliExpress) peuvent se
    retrouver avec le même id_wizi suite à une réutilisation d'ID côté
    Wizishop. On ne retient donc comme variation que les frères/sœurs dont le
    nom commence exactement par le nom du produit parent (convention
    "{Nom du produit} - {Couleur}" observée sur tout le catalogue).
    """
    rows = select("produits", f"select=id_wizi,sku,nom,prix_vente_ht,image_url,"
                               f"nom_categorie,statut&sku=eq.{sku}")
    if not rows:
        return {"kind": "missing"}

    row = rows[0]
    idw           = row["id_wizi"]
    parent_nom    = row.get("nom") or ""
    nom_categorie = row.get("nom_categorie")

    variations = []
    if idw is not None and parent_nom:
        siblings = select("produits", f"select=sku,nom,prix_vente_ht,image_url,statut"
                                       f"&id_wizi=eq.{idw}")
        variations = [
            s for s in siblings
            if s["sku"] != sku
            and (s.get("nom") or "").startswith(parent_nom)
            and (s.get("prix_vente_ht") or 0) > 0
        ]

    if variations:
        return {
            "kind": "parent",
            "parent_nom": parent_nom,
            "nom_categorie": nom_categorie,
            "variations": variations,
        }

    return {
        "kind": "simple",
        "nom_categorie": nom_categorie,
        "prix_vente_ht": row.get("prix_vente_ht") or 0,
        "image_url": row.get("image_url"),
    }


def variation_label(parent_nom, variant_nom):
    """Dérive le libellé de couleur/variante en retirant le préfixe du nom parent."""
    variant_nom = variant_nom or ""
    if parent_nom and variant_nom.startswith(parent_nom):
        label = variant_nom[len(parent_nom):].strip(" -–—")
        if label:
            return label
    return variant_nom or "Variante"


def get_stock_map(skus):
    """Récupère le stock Wizishop (table skus) pour un ensemble de SKUs."""
    skus = sorted({s for s in skus if s})
    stock_map = {}
    batch_size = 80
    for i in range(0, len(skus), batch_size):
        batch = skus[i:i + batch_size]
        rows = select("skus", f"select=sku,stock&sku=in.({','.join(batch)})")
        for r in rows or []:
            stock_map[r["sku"]] = int(r.get("stock") or 0)
    return stock_map


# ── Construction du plan de création par listing ──────────────────────────────

def build_plan(listing, info, stock_map):
    sku = listing["sku"]
    warnings, errors = [], []

    if not sku:
        errors.append("SKU introuvable dans le docx")
    if info["kind"] == "missing":
        errors.append(f"SKU '{sku}' introuvable dans la table produits (Supabase)")
    if not listing["title"]:
        errors.append("Titre vide")
    if not listing["description"]:
        warnings.append("Description vide")

    nom_categorie    = info.get("nom_categorie")
    variations_plan  = []

    if info["kind"] == "parent":
        for v in info["variations"]:
            vsku    = v["sku"]
            prix_ht = v.get("prix_vente_ht") or 0
            stock   = stock_map.get(vsku, 0)
            if vsku not in stock_map:
                warnings.append(f"Stock inconnu pour la variation {vsku} (défaut 0)")
            if not v.get("image_url"):
                warnings.append(f"Image manquante pour la variation {vsku}")
            variations_plan.append({
                "sku": vsku,
                "label": variation_label(info["parent_nom"], v.get("nom")),
                "prix_ht": prix_ht,
                "prix_etsy": round(prix_ht * 1.2, 2),
                "stock": stock,
                "image_url": v.get("image_url"),
            })
        if not variations_plan:
            errors.append("Aucune variation avec prix trouvée pour ce SKU parent")

    elif info["kind"] == "simple":
        prix_ht = info.get("prix_vente_ht") or 0
        stock   = stock_map.get(sku, 0)
        if sku not in stock_map:
            warnings.append(f"Stock inconnu pour {sku} (défaut 0)")
        if not info.get("image_url"):
            warnings.append("Image manquante")
        if prix_ht <= 0:
            warnings.append("Prix de vente HT à 0 ou manquant")
        variations_plan.append({
            "sku": sku,
            "label": None,
            "prix_ht": prix_ht,
            "prix_etsy": round(prix_ht * 1.2, 2),
            "stock": stock,
            "image_url": info.get("image_url"),
        })

    cat_defaults = CATEGORY_DEFAULTS.get(nom_categorie)
    if not cat_defaults:
        warnings.append(f"Catégorie Wizishop inconnue ({nom_categorie!r}) — "
                         f"taxonomy_id par défaut ({DEFAULT_TAXONOMY_ID}) utilisé")
        cat_defaults = {"taxonomy_id": DEFAULT_TAXONOMY_ID, "shop_section_id": None}

    title = listing["title"]
    title, ampersands_fixed = fix_repeated_ampersands(title)
    if ampersands_fixed:
        warnings.append("Plusieurs '&' dans le titre — seul le premier est conservé, "
                         "les autres remplacés par 'and'")
    if len(title) > MAX_TITLE_LEN:
        warnings.append(f"Titre tronqué à {MAX_TITLE_LEN} caractères (était {len(title)})")
        title = title[:MAX_TITLE_LEN].rstrip()

    tags = []
    for t in listing["tags"]:
        if len(t) > MAX_TAG_LEN:
            warnings.append(f"Tag ignoré (> {MAX_TAG_LEN} car.) : {t!r}")
            continue
        tags.append(t)
    if len(tags) > MAX_TAGS:
        warnings.append(f"{len(tags) - MAX_TAGS} tag(s) en trop ignoré(s) (max {MAX_TAGS})")
        tags = tags[:MAX_TAGS]

    images, seen = [], set()
    for v in variations_plan:
        u = v.get("image_url")
        if u and u not in seen:
            seen.add(u)
            images.append(u)
    if len(images) > MAX_IMAGES:
        warnings.append(f"{len(images) - MAX_IMAGES} image(s) en trop ignorée(s) (max {MAX_IMAGES})")
        images = images[:MAX_IMAGES]
    if not images:
        warnings.append("Aucune image disponible pour ce listing")

    total_stock = sum(v["stock"] for v in variations_plan)
    if total_stock == 0:
        warnings.append("Stock total = 0 — le brouillon sera créé avec 1 offre forcée activée")

    prix_valides = [v["prix_etsy"] for v in variations_plan if v["prix_etsy"] > 0]
    prix_listing = min(prix_valides) if prix_valides else 0.01
    if not prix_valides:
        warnings.append("Aucun prix valide — prix par défaut 0.01€ utilisé")

    return {
        "numero": listing["numero"], "sku": sku, "kind": info["kind"],
        "title": title, "tags": tags, "description": listing["description"],
        "variations": variations_plan, "images": images,
        "total_stock": total_stock, "prix_listing": prix_listing,
        "taxonomy_id": cat_defaults["taxonomy_id"],
        "shop_section_id": cat_defaults.get("shop_section_id"),
        "warnings": warnings, "errors": errors,
    }


# ── Résumé console ─────────────────────────────────────────────────────────────

def print_summary(plans):
    nb_err = sum(1 for p in plans if p["errors"])
    nb_ok  = len(plans) - nb_err

    print("\n" + "=" * 100)
    print(f"  RÉSUMÉ — {len(plans)} listing(s) parsé(s) depuis le docx")
    print("=" * 100)

    for p in plans:
        flag = "❌" if p["errors"] else ("⚠️ " if p["warnings"] else "✅")
        print(f"\n{flag} #{p['numero']:>2} [{p['sku']}] ({p['kind']}) — {p['title'][:70]}")
        print(f"     {len(p['variations'])} variation(s) — stock total={p['total_stock']} — "
              f"prix={p['prix_listing']:.2f}€ — {len(p['images'])} image(s) — {len(p['tags'])} tag(s)")
        for e in p["errors"]:
            print(f"     ❌ {e}")
        for w in p["warnings"]:
            print(f"     ⚠️  {w}")

    print("\n" + "-" * 100)
    print(f"  Total : {nb_ok} prêt(s) à créer — {nb_err} en erreur (seront ignorés)")
    print("-" * 100)


# ── Création sur Etsy ──────────────────────────────────────────────────────────

def download_image(url):
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            return r.content
    except requests.RequestException:
        pass
    return None


def _image_extension(url):
    m = re.search(r"\.(jpg|jpeg|png|gif|webp)(?:\?|$)", url, re.IGNORECASE)
    return m.group(1).lower() if m else "jpg"


def create_listing(shop_id, plan):
    """Crée le listing draft + inventaire + images. Retourne (listing_id, erreurs[])."""
    has_variations = plan["kind"] == "parent"

    body = {
        "quantity":            max(plan["total_stock"], 1),
        "title":               plan["title"],
        "description":         plan["description"] or plan["title"],
        "price":               plan["prix_listing"],
        "who_made":            WHO_MADE,
        "when_made":           WHEN_MADE,
        "taxonomy_id":         plan["taxonomy_id"],
        "shipping_profile_id": SHIPPING_PROFILE_ID,
        "return_policy_id":    RETURN_POLICY_ID,
        "processing_min":      PROCESSING_MIN,
        "processing_max":      PROCESSING_MAX,
        "readiness_state_id":  READINESS_STATE_ID,
        "tags":                plan["tags"],
        "is_taxable":          True,
        "is_supply":           False,
        "type":                "physical",
    }
    if plan["shop_section_id"]:
        body["shop_section_id"] = plan["shop_section_id"]

    r = etsy_api.api_post(f"{ETSY_API_URL}/application/shops/{shop_id}/listings", json_body=body)
    if r.status_code not in (200, 201):
        return None, [f"Création listing : HTTP {r.status_code} — {r.text[:300]}"]

    listing_id = r.json().get("listing_id")
    errors = []

    # ── Inventaire (sku, stock, prix, variations) ─────────────────────────────
    products = []
    for v in plan["variations"]:
        offering = {
            "price":              v["prix_etsy"] if v["prix_etsy"] > 0 else 0.01,
            "quantity":           v["stock"],
            "is_enabled":         v["stock"] > 0,
            "readiness_state_id": READINESS_STATE_ID,
        }
        property_values = []
        if has_variations:
            property_values = [{
                "property_id":   200,
                "property_name": "Primary color",
                "scale_id":      None,
                "value_ids":     [],
                "values":        [v["label"] or v["sku"]],
            }]
        products.append({"sku": v["sku"], "property_values": property_values,
                          "offerings": [offering]})

    # Etsy refuse un inventaire où aucune offre n'est activée
    if products and not any(p["offerings"][0]["is_enabled"] for p in products):
        products[0]["offerings"][0]["is_enabled"] = True
        if products[0]["offerings"][0]["quantity"] <= 0:
            products[0]["offerings"][0]["quantity"] = 1

    inv_body = {
        "products":             products,
        "price_on_property":    [],
        "quantity_on_property": [200] if has_variations else [],
        "sku_on_property":      [200] if has_variations else [],
    }
    r_inv = etsy_api.api_put(
        f"{ETSY_API_URL}/application/listings/{listing_id}/inventory", json_body=inv_body)
    if r_inv.status_code not in (200, 201):
        errors.append(f"Inventaire : HTTP {r_inv.status_code} — {r_inv.text[:300]}")

    # ── Images ──────────────────────────────────────────────────────────────
    for rank, image_url in enumerate(plan["images"], start=1):
        content = download_image(image_url)
        if not content:
            errors.append(f"Téléchargement image échoué : {image_url}")
            continue
        files = {"image": (f"image_{rank}.{_image_extension(image_url)}", content)}
        r_img = etsy_api.api_post(
            f"{ETSY_API_URL}/application/shops/{shop_id}/listings/{listing_id}/images",
            files=files)
        if r_img.status_code not in (200, 201):
            errors.append(f"Upload image {rank} échoué : HTTP {r_img.status_code} — {r_img.text[:200]}")
        time.sleep(0.3)

    return listing_id, errors


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Créer les listings Etsy en DRAFT depuis le docx")
    parser.add_argument("--dry-run", action="store_true",
                         help="Parse et affiche le résumé sans rien créer sur Etsy")
    parser.add_argument("--yes", action="store_true",
                         help="Ne pas demander de confirmation interactive")
    parser.add_argument("--listing", metavar="SELECTOR",
                         help="Ne traiter qu'un sous-ensemble : numéro (1), SKU (BAR0020) "
                              "ou plage de numéros (1-5)")
    args = parser.parse_args()

    if not os.path.exists(DOCX_PATH):
        print(f"❌ Fichier introuvable : {DOCX_PATH}")
        sys.exit(1)

    print(f"📄 Lecture de {os.path.basename(DOCX_PATH)} …")
    listings = parse_docx(DOCX_PATH)
    print(f"   {len(listings)} listing(s) trouvé(s) dans le docx")

    if args.listing:
        listings = filter_listings(listings, args.listing)
        print(f"   → filtré sur --listing {args.listing!r} : {len(listings)} listing(s) sélectionné(s)")
        if not listings:
            print(f"❌ Aucun listing ne correspond à --listing {args.listing!r}")
            sys.exit(1)

    print("🔎 Classification des SKUs (Supabase : produits + skus) …")
    classified = [classify_sku(l["sku"]) if l["sku"] else {"kind": "missing"} for l in listings]

    needed_skus = set()
    for l, info in zip(listings, classified):
        if info["kind"] == "parent":
            needed_skus.update(v["sku"] for v in info["variations"])
        elif info["kind"] == "simple":
            needed_skus.add(l["sku"])
    stock_map = get_stock_map(needed_skus)

    plans = [build_plan(l, info, stock_map) for l, info in zip(listings, classified)]
    print_summary(plans)

    ready   = [p for p in plans if not p["errors"]]
    skipped = [p for p in plans if p["errors"]]

    if args.dry_run:
        print("\n(dry-run) Aucun appel à l'API Etsy effectué.")
        return

    if not ready:
        print("\nAucun listing prêt à être créé.")
        return

    if not args.yes:
        reponse = input(
            f"\nCréer les {len(ready)} listing(s) ci-dessus en DRAFT sur Etsy ? (oui/non) : "
        ).strip().lower()
        if reponse not in ("oui", "o", "yes", "y"):
            print("Annulé.")
            return

    shop_id = etsy_api.get_shop_id()
    if not shop_id:
        print("❌ Impossible de récupérer le shop_id Etsy (vérifie .env / token)")
        sys.exit(1)

    print(f"\n🚀 Création de {len(ready)} listing(s) sur Etsy (shop {shop_id}) …\n")
    resultats = []
    for p in ready:
        print(f"→ #{p['numero']} [{p['sku']}] {p['title'][:60]} …", end=" ")
        try:
            listing_id, errors = create_listing(shop_id, p)
        except Exception as e:
            listing_id, errors = None, [str(e)]

        if listing_id:
            suffix = f" ({len(errors)} erreur(s) partielle(s))" if errors else ""
            print(f"✅ listing_id={listing_id}{suffix}")
        else:
            print("❌ échec")
        for e in errors:
            print(f"     ⚠️  {e}")

        resultats.append({"numero": p["numero"], "sku": p["sku"],
                           "listing_id": listing_id, "errors": errors})
        time.sleep(0.5)

    nb_ok = sum(1 for r in resultats if r["listing_id"])
    nb_ko = len(resultats) - nb_ok

    print(f"\n{'=' * 100}")
    print(f"  TERMINÉ — {nb_ok} listing(s) créé(s) en DRAFT, {nb_ko} échec(s), "
          f"{len(skipped)} ignoré(s) (erreurs de données)")
    print(f"{'=' * 100}")
    for r in resultats:
        if r["listing_id"]:
            print(f"  ✅ #{r['numero']} [{r['sku']}] -> "
                  f"https://www.etsy.com/your/shops/me/tools/listings/{r['listing_id']}")
    for r in resultats:
        if not r["listing_id"]:
            print(f"  ❌ #{r['numero']} [{r['sku']}] : {'; '.join(r['errors'])}")


if __name__ == "__main__":
    main()
