"""
Diagnostic Wizishop <-> Supabase.

Compare les SKUs réellement présents chez Wizishop (source de vérité) avec
ceux stockés en base Supabase, pour repérer :
  - les résidus en base à nettoyer (SKU visible en base mais qui n'existe
    plus du tout chez Wizishop)
  - les SKUs manquants à synchroniser (SKU chez Wizishop mais absent des
    SKUs visibles en base)
  - les SKUs présents des deux côtés avec un stock ou un statut différent

Usage:
    python diagnose_wizishop.py
    python diagnose_wizishop.py --include-fournisseur   # + diff fournisseur
                                                          # (lent : un appel
                                                          # API Wizishop par
                                                          # produit, comme
                                                          # sync_produits)
    python diagnose_wizishop.py --dry-run                # prévisualise le nettoyage des résidus
                                                           # (toutes boutiques confondues)
    python diagnose_wizishop.py --clean                   # nettoie les résidus (confirmation requise)
    python diagnose_wizishop.py --clean-v2                # nettoyage ciblé boutique piqueetpince (3899)
                                                            # uniquement : dry-run + confirmation,
                                                            # n'exécute que ça (ignore les autres options)

Credentials lus depuis .env :
    SUPABASE_URL, SUPABASE_KEY, WIZISHOP_EMAIL, WIZISHOP_PASSWORD
"""

import argparse
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Mock Streamlit (sync_database.py et supabase_api.py appellent st.secrets) ──
# Même pattern que sync_nuit.py : on injecte un faux module streamlit qui lit
# les secrets depuis os.environ, pour pouvoir importer ces modules hors appli.


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


class _MockSt:
    secrets = _Secrets()
    session_state = {}

    def warning(self, msg):
        print(f"    ⚠️  {msg}")

    def error(self, msg):
        print(f"    ❌ {msg}")

    def cache_data(self, **kwargs):
        def decorator(fn):
            return fn
        return decorator


sys.modules["streamlit"] = _MockSt()  # type: ignore

from sync_database import get_wizi_token, get_wizi_shops, WIZISHOP_API_URL  # noqa: E402
from supabase_api import select, update  # noqa: E402


def fetch_wizishop_skus(token, shop_id):
    """Récupère TOUS les SKUs Wizishop (toutes pages, tous statuts confondus)."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    all_skus = {}
    page = 1
    while True:
        r = requests.get(
            f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/skus",
            headers=headers, params={"page": page, "limit": 500}, timeout=30,
        )
        if r.status_code != 200:
            print(f"  ❌ Erreur API Wizishop /skus page {page} : {r.status_code} — {r.text[:200]}")
            break
        data = r.json()
        results = data.get("results", [])
        if not results:
            break
        for s in results:
            sku = str(s.get("sku")) if s.get("sku") else None
            if not sku:
                continue
            all_skus[sku] = {
                "stock": int(float(s.get("stock") or 0)),
                "statut": s.get("status") or "",
            }
        total_pages = data.get("pages", 1)
        print(f"  page {page}/{total_pages} — {len(results)} skus")
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.05)
    return all_skus


def check_skus_directement(token, shop_id, skus):
    """Vérifie l'existence de SKUs précis dans une boutique via un appel API
    direct et ciblé par SKU (paramètre sku= sur /v3/shops/{shop_id}/skus),
    plutôt que de se fier au dump paginé complet — sert de double contrôle
    indépendant."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resultats = {}
    for sku in skus:
        r = requests.get(
            f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/skus",
            headers=headers, params={"sku": sku}, timeout=30,
        )
        if r.status_code != 200:
            print(f"  ❌ Erreur API pour {sku} : {r.status_code} — {r.text[:200]}")
            resultats[sku] = None
            continue
        data = r.json()
        found = data.get("results", [])
        resultats[sku] = found[0] if found else None
    return resultats


def merge_skus_par_boutique(resultats_par_shop):
    """Fusionne les dicts sku->{stock,statut} de plusieurs boutiques en un
    seul. Un SKU est considéré présent chez Wizishop dès qu'il apparaît dans
    au moins une boutique. En cas de doublon inter-boutiques (même SKU
    catalogué sur plusieurs boutiques), on garde le statut 'visible' en
    priorité s'il apparaît dans au moins une boutique, et le stock le plus
    élevé rencontré."""
    fusion = {}
    for shop_id, skus in resultats_par_shop.items():
        for sku, info in skus.items():
            if sku not in fusion:
                fusion[sku] = {"stock": info["stock"], "statut": info["statut"], "boutiques": [shop_id]}
            else:
                fusion[sku]["boutiques"].append(shop_id)
                if info["statut"] == "visible":
                    fusion[sku]["statut"] = "visible"
                if info["stock"] > fusion[sku]["stock"]:
                    fusion[sku]["stock"] = info["stock"]
    return fusion


def fetch_wizishop_fournisseurs(token, shop_id):
    """Fournisseur par SKU (parent + variations), via l'API détail produit.
    Lent : un appel HTTP par produit, comme sync_produits() dans sync_database.py."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    fournisseur_par_sku = {}
    page = 1
    while True:
        r = requests.get(
            f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/products",
            headers=headers, params={"page": page, "limit": 100}, timeout=30,
        )
        if r.status_code != 200:
            print(f"  ❌ Erreur API Wizishop /products page {page} : {r.status_code} — {r.text[:200]}")
            break
        data = r.json()
        results = data.get("results", [])
        if not results:
            break
        for p in results:
            if str(p.get("sku") or "").startswith("AE_"):
                continue
            detail_r = requests.get(
                f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/products/{p['id']}",
                headers=headers, timeout=30,
            )
            if detail_r.status_code != 200:
                continue
            prod = detail_r.json()
            fournisseur = prod.get("supplier") or ""
            if prod.get("sku"):
                fournisseur_par_sku[prod["sku"]] = fournisseur
            for attr in prod.get("attributes", []):
                for option in attr.get("options", []):
                    if option.get("sku"):
                        fournisseur_par_sku[option["sku"]] = fournisseur
            time.sleep(0.05)
        total_pages = data.get("pages", 1)
        print(f"  produits page {page}/{total_pages}")
        if page >= total_pages:
            break
        page += 1
    return fournisseur_par_sku


def fetch_base_skus_visibles():
    skus_data = select("skus", "select=sku,stock,statut&statut=eq.visible")
    return {
        s["sku"]: {"stock": int(s.get("stock") or 0), "statut": s.get("statut") or ""}
        for s in (skus_data or []) if s.get("sku")
    }


def fetch_base_fournisseurs(skus):
    """Fournisseur en base pour un ensemble de SKUs donné (table produits,
    correspondance exacte uniquement — pas de fallback préfixe ici, on
    compare SKU à SKU avec la source Wizishop)."""
    produits_data = select("produits", "select=sku,fournisseur")
    return {p["sku"]: (p.get("fournisseur") or "") for p in (produits_data or []) if p.get("sku")}


def fetch_ventes_totales(skus):
    """Ventes totales historiques (tous statuts de commande, toutes sources
    confondues) pour un ensemble de SKUs Wizishop donné — garde-fou avant
    d'archiver des résidus : on ne veut pas archiver un SKU qui a vendu."""
    if not skus:
        return {}
    ventes = {sku: 0 for sku in skus}
    skus_str = ",".join(skus)

    # Wizishop + Etsy : lignes_commande utilise directement les mêmes SKUs
    # que Wizishop (pas de mapping nécessaire, contrairement à Faire).
    lignes_directes = select(
        "lignes_commande",
        f"select=sku,sku_variation,quantite,source"
        f"&source=in.(wizishop,etsy)"
        f"&or=(sku.in.({skus_str}),sku_variation.in.({skus_str}))",
        limit=50000,
    )
    for l in (lignes_directes or []):
        sku_key = l.get("sku_variation") or l.get("sku")
        if sku_key in ventes:
            ventes[sku_key] += l.get("quantite") or 0

    # Faire : lignes_commande.sku est un SKU Faire, à résoudre via sku_mapping_faire
    mapping_data = select("sku_mapping_faire", "select=sku_faire,sku_wizishop")
    sku_faire_vers_wizi = {}
    for m in (mapping_data or []):
        if m.get("sku_wizishop") in ventes and m.get("sku_faire"):
            sku_faire_vers_wizi[m["sku_faire"]] = m["sku_wizishop"]

    if sku_faire_vers_wizi:
        faire_str = ",".join(sku_faire_vers_wizi.keys())
        lignes_faire = select(
            "lignes_commande",
            f"select=sku,quantite&source=eq.faire&sku=in.({faire_str})",
            limit=50000,
        )
        for l in (lignes_faire or []):
            wizi_sku = sku_faire_vers_wizi.get(l.get("sku"))
            if wizi_sku in ventes:
                ventes[wizi_sku] += l.get("quantite") or 0

    return ventes


def _print_section(titre, items, formatter, limite=50):
    print("\n" + "=" * 78)
    print(f"{titre} : {len(items)}")
    print("=" * 78)
    for item in items[:limite]:
        print(f"  {formatter(item)}")
    if len(items) > limite:
        print(f"  ... et {len(items) - limite} de plus")


def clean_residus(residus, dry_run):
    """Marque les SKUs résidus (visibles en base, absents de Wizishop) comme
    invisible (table skus) / archived (table produits). En mode dry_run,
    affiche seulement ce qui serait modifié, sans toucher à la base."""
    if not residus:
        print("\nℹ️  Aucun résidu à nettoyer.")
        return

    entete = "🔎 [DRY-RUN] SKUs qui seraient modifiés" if dry_run else "🧹 SKUs à modifier"
    print(f"\n{entete} ({len(residus)}) :")
    for sku in residus:
        print(f"  {sku}")
    print("\n  -> table skus     : statut = 'invisible'")
    print("  -> table produits : statut = 'archived'")

    if dry_run:
        print("\nℹ️  Mode --dry-run : aucune modification effectuée.")
        return

    try:
        reponse = input(f"\n⚠️  Confirmer la mise à jour de {len(residus)} SKU(s) en base ? [o/N] ").strip().lower()
    except EOFError:
        reponse = ""
    if reponse not in ("o", "oui", "y", "yes"):
        print("❌ Annulé, aucune modification effectuée.")
        return

    batch_size = 200
    nb_skus_ok = 0
    nb_produits_ok = 0
    for i in range(0, len(residus), batch_size):
        batch = residus[i:i + batch_size]
        ids_str = ",".join(batch)
        if update("skus", f"sku=in.({ids_str})", {"statut": "invisible"}):
            nb_skus_ok += len(batch)
        if update("produits", f"sku=in.({ids_str})", {"statut": "archived"}):
            nb_produits_ok += len(batch)

    print(f"\n✓ skus.statut='invisible' appliqué sur {nb_skus_ok}/{len(residus)} SKU(s)")
    print(f"✓ produits.statut='archived' appliqué sur {nb_produits_ok}/{len(residus)} SKU(s)")


SHOP_ID_PIQUEETPINCE = 3899
SHOP_ID_BTOB = 424578
SHOP_ID_GREENBRUSH = 381934


def _afficher_groupe(titre, skus_tries, base_skus):
    print("\n" + "=" * 78)
    print(f"{titre} : {len(skus_tries)}")
    print("=" * 78)
    for sku in skus_tries:
        print(f"  {sku}  (stock base={base_skus[sku]['stock']}, statut base={base_skus[sku]['statut']})")


def clean_v2(token, archiver_btob=False):
    """Nettoyage ciblé : piqueetpince (3899) est la source de vérité pour le
    catalogue B2C. Par défaut, un SKU absent de piqueetpince mais présent
    sur piqueetpince-btob (424578) est préservé (groupe 2), car encore
    légitimement vendu là-bas. Avec archiver_btob=True, le groupe 2 est
    archivé aussi — à utiliser seulement si c'est une décision délibérée,
    puisque ces SKUs sont confirmés actifs sur la boutique BtoB. On isole
    aussi les SKUs liés à greenbrush (381934, boutique tierce sans rapport
    avec Pique&Pince) ou préfixés AE_ (dropshipping AliExpress).

    Affiche un dry-run détaillé en 3 groupes, puis demande une confirmation
    explicite avant de modifier la base."""
    print(f"\n📥 Récupération des SKUs de piqueetpince (id={SHOP_ID_PIQUEETPINCE})...")
    skus_pep = fetch_wizishop_skus(token, SHOP_ID_PIQUEETPINCE)
    print(f"✓ {len(skus_pep)} SKUs")

    print(f"\n📥 Récupération des SKUs de piqueetpince-btob (id={SHOP_ID_BTOB})...")
    skus_btob = fetch_wizishop_skus(token, SHOP_ID_BTOB)
    print(f"✓ {len(skus_btob)} SKUs")

    print(f"\n📥 Récupération des SKUs de greenbrush (id={SHOP_ID_GREENBRUSH})...")
    skus_greenbrush = fetch_wizishop_skus(token, SHOP_ID_GREENBRUSH)
    print(f"✓ {len(skus_greenbrush)} SKUs")

    print("\n📥 Récupération des SKUs visibles en base Supabase (table skus)...")
    base_skus = fetch_base_skus_visibles()
    print(f"✓ {len(base_skus)} SKUs visibles en base")

    candidats = sorted(set(base_skus) - set(skus_pep))

    groupe_btob = sorted(sku for sku in candidats if sku in skus_btob)
    reste = [sku for sku in candidats if sku not in skus_btob]
    groupe_greenbrush = sorted(
        sku for sku in reste if sku.startswith("AE_") or sku in skus_greenbrush
    )
    groupe_residus = sorted(set(reste) - set(groupe_greenbrush))

    _afficher_groupe(
        "1️⃣  Absents de piqueetpince ET de piqueetpince-btob — vrais résidus à archiver",
        groupe_residus, base_skus,
    )
    _afficher_groupe(
        "2️⃣  Absents de piqueetpince mais PRÉSENTS dans piqueetpince-btob"
        + ("  — À ARCHIVER (archiver_btob=True)" if archiver_btob else "  — à CONSERVER"),
        groupe_btob, base_skus,
    )
    _afficher_groupe(
        "3️⃣  SKUs Greenbrush (AE_ ou présents uniquement dans greenbrush) — à archiver",
        groupe_greenbrush, base_skus,
    )

    groupes_archives = "groupes 1+2+3" if archiver_btob else "groupes 1+3"
    a_archiver = sorted(groupe_residus + groupe_greenbrush + (groupe_btob if archiver_btob else []))
    print(f"\nRécapitulatif : {len(candidats)} candidat(s) au total  |  "
          f"{len(groupe_residus)} vrais résidus  |  {len(groupe_btob)} BtoB  |  "
          f"{len(groupe_greenbrush)} greenbrush  |  "
          f"{len(a_archiver)} à archiver au total ({groupes_archives})")

    print(f"\n  -> table skus     : statut = 'invisible'  ({groupes_archives})")
    print(f"  -> table produits : statut = 'archived'    ({groupes_archives})")
    if not archiver_btob:
        print(f"  -> {len(groupe_btob)} SKU(s) du groupe 2 préservés (aucune modification)")
    else:
        print(f"  -> ⚠️  {len(groupe_btob)} SKU(s) du groupe 2 (confirmés actifs sur piqueetpince-btob) "
              f"seront AUSSI archivés")

    if not a_archiver:
        print("\nℹ️  Aucun SKU à archiver.")
        return

    try:
        reponse = input(
            f"\n⚠️  Confirmer la mise à jour de {len(a_archiver)} SKU(s) en base (--clean-v2) ? [o/N] "
        ).strip().lower()
    except EOFError:
        reponse = ""
    if reponse not in ("o", "oui", "y", "yes"):
        print("❌ Annulé, aucune modification effectuée.")
        return

    batch_size = 200
    nb_skus_ok = 0
    nb_produits_ok = 0
    for i in range(0, len(a_archiver), batch_size):
        batch = a_archiver[i:i + batch_size]
        ids_str = ",".join(batch)
        if update("skus", f"sku=in.({ids_str})", {"statut": "invisible"}):
            nb_skus_ok += len(batch)
        if update("produits", f"sku=in.({ids_str})", {"statut": "archived"}):
            nb_produits_ok += len(batch)

    print(f"\n✓ skus.statut='invisible' appliqué sur {nb_skus_ok}/{len(a_archiver)} SKU(s)")
    print(f"✓ produits.statut='archived' appliqué sur {nb_produits_ok}/{len(a_archiver)} SKU(s)")


def main():
    parser = argparse.ArgumentParser(description="Diagnostic SKUs Wizishop vs Supabase")
    parser.add_argument(
        "--include-fournisseur", action="store_true",
        help="Compare aussi le fournisseur (lent : un appel API Wizishop par produit)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Affiche les SKUs résidus qui seraient nettoyés (skus.statut='invisible', "
             "produits.statut='archived') sans rien modifier",
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="Nettoie les SKUs résidus en base (skus.statut='invisible', "
             "produits.statut='archived') après confirmation interactive",
    )
    parser.add_argument(
        "--clean-v2", action="store_true",
        help="Nettoyage ciblé sur la boutique piqueetpince (3899) UNIQUEMENT (pas les "
             "autres boutiques du compte) : dry-run + confirmation avant d'archiver. "
             "Ignore les autres options et n'exécute que ce nettoyage.",
    )
    parser.add_argument(
        "--include-btob", action="store_true",
        help="Avec --clean-v2 : archive aussi le groupe 2 (SKUs présents sur "
             "piqueetpince-btob), normalement préservé. À utiliser seulement en "
             "connaissance de cause — ces SKUs sont confirmés actifs sur la boutique BtoB.",
    )
    parser.add_argument(
        "--check-skus", nargs="+", metavar="SKU",
        help="Vérifie l'existence de SKU(s) précis dans piqueetpince (3899) via un appel "
             "API direct par SKU (double contrôle indépendant du dump paginé complet). "
             "Ignore les autres options et n'exécute que cette vérification.",
    )
    args = parser.parse_args()

    print("🔐 Connexion à Wizishop...")
    token, account_id, shop_id = get_wizi_token()
    if not token:
        print("❌ Impossible de se connecter à Wizishop (vérifie WIZISHOP_EMAIL/WIZISHOP_PASSWORD dans .env).")
        sys.exit(1)
    print(f"✓ Connecté — account_id={account_id}, shop_id par défaut={shop_id}")

    if args.check_skus:
        print(f"\n🔎 Vérification directe de {len(args.check_skus)} SKU(s) dans piqueetpince "
              f"(id={SHOP_ID_PIQUEETPINCE}) via appel API ciblé (paramètre sku=)...")
        resultats = check_skus_directement(token, SHOP_ID_PIQUEETPINCE, args.check_skus)
        print("\n" + "=" * 78)
        for sku, info in resultats.items():
            if info is None:
                print(f"  ❌ {sku:<20} ABSENT de piqueetpince (3899)")
            else:
                print(f"  ✓ {sku:<20} PRÉSENT — stock={info.get('stock')}, statut={info.get('status')}")
        print("=" * 78)
        return

    if args.clean_v2:
        clean_v2(token, archiver_btob=args.include_btob)
        return

    print("\n🏪 Récupération de la liste des boutiques du compte (/v3/accounts/{account_id}/shops)...")
    shops = get_wizi_shops(token, account_id)
    if not shops:
        print(f"  ⚠️  Impossible de récupérer la liste des boutiques, on continue avec la "
              f"seule boutique shop_id={shop_id}.")
        shops = [{"id": shop_id, "nom": "(par défaut)"}]
    else:
        print(f"✓ {len(shops)} boutique(s) trouvée(s) : "
              + ", ".join(f"{s.get('nom') or '(sans nom)'} (id={s.get('id')})" for s in shops))

    print("\n📥 Récupération des SKUs de chaque boutique (toutes pages, tous statuts confondus)...")
    resultats_par_shop = {}
    for s in shops:
        sid = s.get("id")
        if sid is None:
            continue
        print(f"  boutique {s.get('nom') or sid} (id={sid}) :")
        resultats_par_shop[sid] = fetch_wizishop_skus(token, sid)
        print(f"    -> {len(resultats_par_shop[sid])} SKUs")

    wizi_skus = merge_skus_par_boutique(resultats_par_shop)
    print(f"\n✓ {len(wizi_skus)} SKUs distincts au total, toutes boutiques confondues")

    print("\n📥 Récupération des SKUs visibles en base Supabase (table skus)...")
    base_skus = fetch_base_skus_visibles()
    print(f"✓ {len(base_skus)} SKUs visibles en base")

    wizi_set = set(wizi_skus)
    base_set = set(base_skus)

    residus = sorted(base_set - wizi_set)
    manquants = sorted(wizi_set - base_set)
    communs = sorted(wizi_set & base_set)

    fournisseur_wizi = {}
    fournisseur_base = {}
    if args.include_fournisseur:
        print(f"\n📥 Récupération des fournisseurs Wizishop pour la boutique shop_id={shop_id} uniquement "
              f"(appel détail par produit, peut prendre du temps — pas encore étendu aux autres boutiques)...")
        fournisseur_wizi = fetch_wizishop_fournisseurs(token, shop_id)
        print(f"✓ {len(fournisseur_wizi)} SKUs avec fournisseur récupérés depuis Wizishop")
        fournisseur_base = fetch_base_fournisseurs(communs)

    diffs = []
    for sku in communs:
        b, w = base_skus[sku], wizi_skus[sku]
        ecarts = []
        if b["stock"] != w["stock"]:
            ecarts.append(f"stock base={b['stock']} / wizi={w['stock']}")
        if b["statut"] != w["statut"]:
            ecarts.append(f"statut base={b['statut']} / wizi={w['statut']}")
        if args.include_fournisseur:
            fb = (fournisseur_base.get(sku) or "").strip()
            fw = (fournisseur_wizi.get(sku) or "").strip()
            if fb != fw:
                ecarts.append(f"fournisseur base={fb or '(vide)'} / wizi={fw or '(vide)'}")
        if ecarts:
            diffs.append((sku, ecarts))

    print("\n📥 Calcul des ventes totales historiques pour les résidus (garde-fou avant archivage)...")
    ventes_residus = fetch_ventes_totales(residus)

    residus_tries = sorted(residus, key=lambda s: (-ventes_residus.get(s, 0), -base_skus[s]["stock"]))
    print("\n" + "=" * 78)
    print(f"🗑️  SKUs en base (visibles) mais ABSENTS de Wizishop — résidus à nettoyer : {len(residus)}")
    print("=" * 78)
    nb_a_risque = 0
    for sku in residus_tries:
        stock = base_skus[sku]["stock"]
        ventes = ventes_residus.get(sku, 0)
        a_risque = stock > 0 or ventes > 0
        if a_risque:
            nb_a_risque += 1
        marqueur = " ⚠️" if a_risque else ""
        print(f"  {sku:<45} stock={stock:<6} ventes_totales={ventes:<6}{marqueur}")
    print(f"\n  ⚠️  {nb_a_risque}/{len(residus)} résidu(s) ont du stock et/ou des ventes historiques "
          f"— à vérifier avant d'archiver.")

    _print_section(
        "➕ SKUs chez Wizishop mais ABSENTS des SKUs visibles en base — manquants à synchroniser",
        manquants,
        lambda sku: f"{sku}  (stock wizi={wizi_skus[sku]['stock']}, statut wizi={wizi_skus[sku]['statut']})",
    )

    _print_section(
        "⚠️  SKUs présents des deux côtés avec des différences",
        diffs,
        lambda item: f"{item[0]}  —  {' ; '.join(item[1])}",
    )

    if not args.include_fournisseur:
        print(
            "\nℹ️  Diff fournisseur non exécuté (nécessite un appel API détaillé par "
            "produit, potentiellement long). Relance avec --include-fournisseur pour l'inclure."
        )

    print("\n✅ Diagnostic terminé.")
    print(f"   Total Wizishop: {len(wizi_skus)}  |  Total base (visibles): {len(base_skus)}  |  "
          f"Communs: {len(communs)}  |  Résidus: {len(residus)}  |  Manquants: {len(manquants)}  |  "
          f"Avec écarts: {len(diffs)}")

    if args.dry_run or args.clean:
        clean_residus(residus, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
