"""
Envoi hebdomadaire des emails de réapprovisionnement.

Usage:
    python send_reapprovisionnement.py

Credentials lus depuis .env : SUPABASE_URL, SUPABASE_KEY, GMAIL_USER, GMAIL_PASSWORD
"""

import math
import os
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────────

load_dotenv()

SUPABASE_URL   = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY   = os.environ.get("SUPABASE_KEY", "")
GMAIL_USER     = os.environ.get("GMAIL_USER", "")
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD", "")
DESTINATAIRE   = "contact@ns-ebiz.fr"

NB_MOIS      = 6
SEUIL_ROUGE  = 3   # mois_stock <= seuil → 🔴
SEUIL_JAUNE  = 5   # mois_stock <= seuil → 🟡
MOIS_CIBLE   = 4   # objectif de stock = ventes_par_mois × MOIS_CIBLE

if not SUPABASE_URL or not SUPABASE_KEY:
    raise SystemExit("SUPABASE_URL et SUPABASE_KEY requis dans .env")
if not GMAIL_USER or not GMAIL_PASSWORD:
    raise SystemExit("GMAIL_USER et GMAIL_PASSWORD requis dans .env")

# ── Supabase ──────────────────────────────────────────────────────────────────

def _headers():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


def select(table, query="", limit=None):
    all_rows = []
    page_size = 1000
    offset = 0
    while True:
        sep = "&" if query else ""
        url = f"{SUPABASE_URL}/rest/v1/{table}?{query}{sep}limit={page_size}&offset={offset}"
        r = requests.get(url, headers=_headers(), timeout=30)
        if r.status_code not in (200, 206):
            print(f"  [ERREUR] select {table}: HTTP {r.status_code} — {r.text[:200]}")
            break
        data = r.json()
        if not data:
            break
        all_rows.extend(data)
        if limit and len(all_rows) >= limit:
            all_rows = all_rows[:limit]
            break
        if len(data) < page_size:
            break
        offset += page_size
    return all_rows


# ── Chargement données ────────────────────────────────────────────────────────

def charger_donnees():
    date_limite = (datetime.now(timezone.utc) - timedelta(days=NB_MOIS * 30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    print(f"Période ventes : {NB_MOIS} derniers mois (depuis {date_limite[:10]})")

    # Produits
    print("Chargement produits …")
    produits_raw = select(
        "produits",
        "select=sku,nom,nom_categorie,fournisseur,reference_fournisseur,prix_achat_ht",
    )
    prod_map = {p["sku"]: p for p in (produits_raw or [])}

    # Stocks
    print("Chargement stocks …")
    skus_raw = select("skus", "select=sku,stock&statut=eq.visible")
    stock_map = {r["sku"]: int(r.get("stock") or 0) for r in (skus_raw or [])}

    # SKUs à exclure (commande fournisseur complète en cours)
    print("Chargement commandes fournisseur …")
    cmds_fourn = select(
        "commandes_fournisseur",
        "select=sku,quantite_commandee,quantite_attendue"
        "&statut=in.(en_commande,recu_partiel)",
    )
    skus_exclu = set()
    skus_partiels = {}   # sku → quantite_deja_commandee
    for c in (cmds_fourn or []):
        sku_c   = c["sku"]
        qty_cmd = int(c.get("quantite_commandee") or 0)
        qty_att = int(c.get("quantite_attendue") or 0)
        if qty_att == 0 or qty_cmd >= qty_att:
            skus_exclu.add(sku_c)
        else:
            skus_partiels[sku_c] = qty_cmd

    # SKUs ignorés
    print("Chargement SKUs ignorés …")
    ignores_raw = select("skus_ignores", "select=sku")
    skus_ignores = {r["sku"] for r in (ignores_raw or [])}

    # Ventes Wizishop + Etsy
    print("Chargement ventes Wizishop/Etsy …")
    ventes = {}

    def _ajouter_lignes_wizi(cmds):
        if not cmds:
            return
        ids_str = ",".join(str(c["id_wizi"]) for c in cmds if c.get("id_wizi"))
        if not ids_str:
            return
        lignes = select(
            "lignes_commande",
            f"select=sku,sku_variation,quantite&id_commande=in.({ids_str})",
            limit=50000,
        )
        for l in (lignes or []):
            sku_key = l.get("sku_variation") or l.get("sku")
            if sku_key:
                ventes[sku_key] = ventes.get(sku_key, 0) + (l.get("quantite") or 0)

    cmds_wizi = select(
        "commandes",
        f"select=id_wizi&source=eq.wizishop"
        f"&statut_code=not.in.(0,45,46,50)&date_commande=gte.{date_limite}",
    )
    cmds_etsy = select(
        "commandes",
        f"select=id_wizi&source=eq.etsy"
        f"&statut_code=not.in.(0,45,46,50)&date_commande=gte.{date_limite}",
    )
    _ajouter_lignes_wizi(cmds_wizi)
    _ajouter_lignes_wizi(cmds_etsy)

    # Ventes Faire (id_faire + mapping SKU)
    print("Chargement ventes Faire …")
    cmds_faire = select(
        "commandes",
        f"select=id_faire&source=eq.faire"
        f"&statut_code=not.in.(0,45,46,50)&date_commande=gte.{date_limite}",
    )
    if cmds_faire:
        ids_str = ",".join(str(c["id_faire"]) for c in cmds_faire if c.get("id_faire"))
        if ids_str:
            lignes_faire = select(
                "lignes_commande",
                f"select=sku,quantite&id_commande=in.({ids_str})",
                limit=50000,
            )
            mapping_raw = select("sku_mapping_faire", "select=sku_faire,sku_wizishop")
            sku_mapping = {m["sku_faire"]: m["sku_wizishop"] for m in (mapping_raw or [])}
            for l in (lignes_faire or []):
                sku_faire = l.get("sku") or ""
                sku_key   = sku_mapping.get(sku_faire, sku_faire)
                if sku_key:
                    ventes[sku_key] = ventes.get(sku_key, 0) + (l.get("quantite") or 0)

    print(f"  → {len(prod_map)} produits, {len(stock_map)} SKUs visibles, "
          f"{len(ventes)} SKUs avec ventes, "
          f"{len(skus_exclu)} exclus (commande complète), "
          f"{len(skus_ignores)} ignorés")

    return prod_map, stock_map, ventes, skus_exclu, skus_partiels, skus_ignores


# ── Calcul alertes ────────────────────────────────────────────────────────────

def calculer_alertes(prod_map, stock_map, ventes, skus_exclu, skus_partiels, skus_ignores):
    resultats = []
    for sku, stock in stock_map.items():
        if sku in skus_exclu or sku in skus_ignores:
            continue

        prod = prod_map.get(sku, {})
        fournisseur = (prod.get("fournisseur") or "").strip()
        if not fournisseur:
            continue

        v_total      = ventes.get(sku, 0)
        ventes_mois  = round(v_total / NB_MOIS, 1)
        mois_stock   = round(stock / ventes_mois, 1) if ventes_mois > 0 else 99

        if mois_stock <= SEUIL_ROUGE:
            alerte = "🔴"
        elif mois_stock <= SEUIL_JAUNE:
            alerte = "🟡"
        else:
            continue  # 🟢 OK

        # Qté à commander = ceil(max(0, objectif - stock - déjà commandé si partiel))
        objectif = ventes_mois * MOIS_CIBLE
        deja_cmd = skus_partiels.get(sku, 0)
        qty_a_commander = max(0, math.ceil(objectif - stock - deja_cmd))

        resultats.append({
            "sku":             sku,
            "nom":             (prod.get("nom") or sku),
            "ref_fourn":       (prod.get("reference_fournisseur") or ""),
            "fournisseur":     fournisseur,
            "stock":           stock,
            "ventes_mois":     ventes_mois,
            "mois_stock":      mois_stock,
            "qty_a_commander": qty_a_commander,
            "alerte":          alerte,
            "partielle":       sku in skus_partiels,
            "deja_cmd":        deja_cmd,
        })

    return resultats


# ── Rendu HTML ────────────────────────────────────────────────────────────────

_STYLE_ROUGE   = 'background-color:#ffe0e0;'
_STYLE_JAUNE   = 'background-color:#fff9c4;'
_STYLE_ENTETE  = 'background-color:#f0f0f0; font-weight:bold; padding:8px; border:1px solid #ccc;'
_STYLE_CELLULE = 'padding:7px 10px; border:1px solid #ddd;'


def _td(val, style=""):
    return f'<td style="{_STYLE_CELLULE}{style}">{val}</td>'


def construire_html(lignes, titre):
    entetes = ["SKU", "Nom produit", "Réf. fournisseur",
               "Stock", "Ventes/mois", "Mois de stock", "Qté à commander", "Alerte"]
    thead = "".join(f'<th style="{_STYLE_ENTETE}">{h}</th>' for h in entetes)

    tbody_rows = []
    for l in lignes:
        row_style = _STYLE_ROUGE if l["alerte"] == "🔴" else _STYLE_JAUNE
        note_partielle = " ⚠️ cmd partielle" if l["partielle"] else ""
        tbody_rows.append(
            f'<tr style="{row_style}">'
            + _td(l["sku"])
            + _td(l["nom"])
            + _td(l["ref_fourn"])
            + _td(l["stock"])
            + _td(l["ventes_mois"])
            + _td(l["mois_stock"])
            + _td(f"{l['qty_a_commander']}{note_partielle}")
            + _td(l["alerte"])
            + "</tr>"
        )

    return f"""
    <html><body>
    <h2>{titre}</h2>
    <p>Généré le {datetime.now().strftime('%d/%m/%Y à %Hh%M')} — période ventes : {NB_MOIS} mois
    — objectif stock : {MOIS_CIBLE} mois</p>
    <table style="border-collapse:collapse; font-family:Arial, sans-serif; font-size:13px;">
      <thead><tr>{thead}</tr></thead>
      <tbody>{"".join(tbody_rows)}</tbody>
    </table>
    </body></html>
    """


# ── Envoi email ───────────────────────────────────────────────────────────────

def envoyer_email(sujet, html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = sujet
    msg["From"]    = GMAIL_USER
    msg["To"]      = DESTINATAIRE
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as srv:
        srv.ehlo()
        srv.starttls()
        srv.login(GMAIL_USER, GMAIL_PASSWORD)
        srv.sendmail(GMAIL_USER, DESTINATAIRE, msg.as_string())


# ── Main ──────────────────────────────────────────────────────────────────────

prod_map, stock_map, ventes, skus_exclu, skus_partiels, skus_ignores = charger_donnees()
alertes = calculer_alertes(prod_map, stock_map, ventes, skus_exclu, skus_partiels, skus_ignores)

date_str = datetime.now().strftime("%d/%m/%Y")

configs = [
    {
        "fournisseur": "ALIEXPRESS",
        "niveaux":     {"🔴"},
        "sujet":       f"🔴 Réapprovisionnement ALIEXPRESS - {date_str}",
        "titre_email": "Réapprovisionnement urgent — ALIEXPRESS",
    },
    {
        "fournisseur": "VEINIERE",
        "niveaux":     {"🔴", "🟡"},
        "sujet":       f"⚠️ Réapprovisionnement VEINIERE - {date_str}",
        "titre_email": "Réapprovisionnement — VEINIERE",
    },
    {
        "fournisseur": "NPC",
        "niveaux":     {"🔴", "🟡"},
        "sujet":       f"⚠️ Réapprovisionnement NPC - {date_str}",
        "titre_email": "Réapprovisionnement — NPC",
    },
]

print()
nb_emails_envoyes = 0

for cfg in configs:
    fourn = cfg["fournisseur"]
    lignes = [
        a for a in alertes
        if a["fournisseur"].upper() == fourn and a["alerte"] in cfg["niveaux"]
    ]
    lignes.sort(key=lambda x: (x["alerte"] != "🔴", x["mois_stock"]))

    if not lignes:
        print(f"[{fourn}] Aucun produit à signaler — email non envoyé.")
        continue

    html = construire_html(lignes, cfg["titre_email"])
    try:
        envoyer_email(cfg["sujet"], html)
        nb_emails_envoyes += 1
        rouge    = sum(1 for l in lignes if l["alerte"] == "🔴")
        jaune    = sum(1 for l in lignes if l["alerte"] == "🟡")
        partiels = sum(1 for l in lignes if l["partielle"])
        print(f"[{fourn}] ✓ Email envoyé — {len(lignes)} produits "
              f"(🔴 {rouge}  🟡 {jaune}  ⚠️ cmd partielle {partiels})")
    except Exception as e:
        print(f"[{fourn}] ✗ Erreur envoi : {e}")

print(f"\n{'─'*45}")
print(f"  Emails envoyés : {nb_emails_envoyes} / {len(configs)}")
print(f"{'─'*45}")
