import streamlit as st
import pandas as pd
import requests
import json
import os
from supabase_api import select, upsert, insert, update, delete
from sync_database import (get_wizi_token, sync_categories, sync_marques,
                           sync_skus, sync_commandes, sync_produits, log_sync,
                           WIZISHOP_API_URL)
from sync_etsy import sync_etsy_commandes, log_sync_etsy
from sync_etsy_produits import sync_produits_etsy
from etsy_api import get_shop_id
from sync_faire import sync_faire_commandes, sync_faire_produits, log_sync_faire
from sync_shopify import sync_shopify_produits, sync_shopify_commandes, log_sync_shopify
import time
from datetime import datetime, timezone, timedelta
from faire_api import (api_get as faire_api_get, test_write_permission,
                       api_patch as faire_api_patch, create_product as faire_create_product)
from shopify_api import (
    test_connection as shopify_test_connection,
    get_shopify_token,
    get_shopify_token_montessori,
    get_auth_url as shopify_get_auth_url,
    verify_hmac as shopify_verify_hmac,
    exchange_code_for_token as shopify_exchange_code,
    generate_nonce as shopify_generate_nonce,
)

st.set_page_config(
    page_title="Pique&Pince — Dashboard",
    page_icon="📊",
    layout="wide"
)

# ── Interception callback OAuth Shopify ──────────────────────────────────────
# Doit s'exécuter avant la navigation sidebar, car Shopify redirige vers la
# racine de l'app avec ?code=...&hmac=...&state=...&shop=...&timestamp=...
_qp = dict(st.query_params)
if "code" in _qp and "hmac" in _qp:
    _state = _qp.get("state", "")
    if _state.startswith("shopify_ff_"):
        try:
            _client_secret = st.secrets["SHOPIFY_FOULARD_FRENCHY_CLIENT_SECRET"]
            _client_id = st.secrets["SHOPIFY_FOULARD_FRENCHY_CLIENT_ID"]
            _shop = st.secrets["SHOPIFY_FOULARD_FRENCHY_SHOP"]
            _hmac_ok = shopify_verify_hmac(_qp, _client_secret)
            if not _hmac_ok:
                st.session_state["shopify_ff_error"] = "HMAC invalide — callback rejeté."
            else:
                _status, _result = shopify_exchange_code(_shop, _client_id, _client_secret, _qp["code"])
                if _status == 200:
                    st.session_state["shopify_ff_token_obtained"] = _result.get("access_token", "")
                    st.session_state["shopify_ff_scope_obtained"] = _result.get("scope", "")
                else:
                    st.session_state["shopify_ff_error"] = f"Échange token échoué {_status} : {_result}"
        except Exception as _e:
            st.session_state["shopify_ff_error"] = str(_e)
        st.query_params.clear()

    elif _state.startswith("shopify_montessori_"):
        try:
            _client_secret = st.secrets["SHOPIFY_BOUTIQUE2_CLIENT_SECRET"]
            _client_id = st.secrets["SHOPIFY_BOUTIQUE2_CLIENT_ID"]
            _shop = st.secrets["SHOPIFY_BOUTIQUE2_SHOP"]
            _hmac_ok = shopify_verify_hmac(_qp, _client_secret)
            if not _hmac_ok:
                st.session_state["shopify_montessori_error"] = "HMAC invalide — callback rejeté."
            else:
                _status, _result = shopify_exchange_code(_shop, _client_id, _client_secret, _qp["code"])
                if _status == 200:
                    st.session_state["shopify_montessori_token_obtained"] = _result.get("access_token", "")
                    st.session_state["shopify_montessori_scope_obtained"] = _result.get("scope", "")
                else:
                    st.session_state["shopify_montessori_error"] = f"Échange token échoué {_status} : {_result}"
        except Exception as _e:
            st.session_state["shopify_montessori_error"] = str(_e)
        st.query_params.clear()
# ── Fin interception OAuth ───────────────────────────────────────────────────


def get_prod_parent(sku, prod_map):
    if not sku:
        return {}
    sku = str(sku)
    # Correspondance exacte : couvre les variations stockées par sync_produits
    if sku in prod_map:
        return prod_map[sku]
    # Fallback préfixe : pour les SKUs absents de produits (sync non faite)
    for longueur in range(len(sku)-1, 3, -1):
        prefixe = sku[:longueur]
        if prefixe in prod_map:
            return prod_map[prefixe]
    return {}


@st.cache_data(ttl=600)
def _get_wizishop_description(id_wizi):
    """Description HTML brute du produit Wizishop (non synchronisée en base —
    récupérée à la demande pour servir de contexte à la génération de fiche Faire)."""
    if not id_wizi:
        return ""
    token, _, shop_id = get_wizi_token()
    if not token:
        return ""
    r = requests.get(f"{WIZISHOP_API_URL}/v3/shops/{shop_id}/products/{id_wizi}",
                     headers={"Authorization": f"Bearer {token}"}, timeout=15)
    if r.status_code != 200:
        return ""
    return r.json().get("description") or ""


_PROMPT_SYSTEME_FICHE_FAIRE = """Tu es un expert en rédaction de fiches produits pour Faire, plateforme B2B wholesale. Tu rédiges pour Pique&Pince, marque française d'accessoires cheveux faits main en acétate de cellulose.
Règles strictes :
- Titre : 35-60 caractères
- Description : 1000-2000 caractères, premiers 160 chars = snippet crucial, pas de saut de ligne manuel
- Toujours mentionner : fait main en France, acétate de cellulose bio-sourcé, pochette velours Pique&Pince incluse
- Inclure : conseils merchandising BtoB pour retailers, instructions entretien
- Interdits : le mot "artisanal", "bien supérieur au plastique ordinaire"
- Ton : premium, B2B, pour boutiques indépendantes et concept stores
- Langue : français
- Retourner JSON : {"titre": "...", "description": "...", "short_description": "..."}"""


def _generer_fiche_faire(nom, categorie, fournisseur, description_wizi):
    """Appelle l'API Anthropic (Messages) pour générer la fiche produit Faire.
    Lève une exception si la clé est absente, l'appel échoue ou le JSON est invalide."""
    contenu_utilisateur = (
        f"Produit Wizishop :\n"
        f"Nom : {nom}\n"
        f"Catégorie : {categorie}\n"
        f"Fournisseur : {fournisseur}\n"
        f"Description actuelle (site B2C Pique&Pince, HTML) :\n{description_wizi or '(aucune)'}\n\n"
        f"Rédige la fiche produit Faire (B2B) selon les règles strictes du system prompt. "
        f"Réponds uniquement avec le JSON demandé, sans texte ni balises autour."
    )

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":         st.secrets["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":      "claude-sonnet-4-6",
            "max_tokens": 2000,
            "system":     _PROMPT_SYSTEME_FICHE_FAIRE,
            "messages":   [{"role": "user", "content": contenu_utilisateur}],
        },
        timeout=60,
    )
    r.raise_for_status()
    texte = r.json()["content"][0]["text"].strip()
    if texte.startswith("```"):
        texte = texte.strip("`")
        if texte.lower().startswith("json"):
            texte = texte[4:]
    return json.loads(texte.strip())


@st.cache_data(ttl=300)
def _get_produits_reap():
    produits_data = select("produits",
        "select=sku,nom,nom_categorie,fournisseur,reference_fournisseur,prix_achat_ht")
    prod_map = {p["sku"]: p for p in produits_data} if produits_data else {}
    mapping_data = select("sku_mapping_faire", "select=sku_faire,sku_wizishop")
    sku_mapping = {m["sku_faire"]: m["sku_wizishop"] for m in mapping_data} if mapping_data else {}
    return prod_map, sku_mapping


@st.cache_data(ttl=300)
def _get_ventes_reap(date_limite):
    cmds_wizi = select("commandes",
        f"select=id_wizi&source=eq.wizishop&statut_code=not.in.(0,45,46,50)&date_commande=gte.{date_limite}")
    cmds_etsy = select("commandes",
        f"select=id_wizi&source=eq.etsy&statut_code=not.in.(0,45,46,50)&date_commande=gte.{date_limite}")
    cmds_faire = select("commandes",
        f"select=id_faire&source=eq.faire&statut_code=not.in.(0,45,46,50)&date_commande=gte.{date_limite}")

    nom_par_sku = {}
    ventes_wizi = {}
    ventes_etsy = {}
    ventes_faire = {}

    if cmds_wizi:
        ids_str = ",".join(str(c["id_wizi"]) for c in cmds_wizi if c.get("id_wizi"))
        lignes = select("lignes_commande",
            f"select=sku,sku_variation,quantite,nom_produit&id_commande=in.({ids_str})",
            limit=50000)
        if lignes:
            for l in lignes:
                sku = l.get("sku")
                if sku and sku not in nom_par_sku:
                    nom_par_sku[sku] = l.get("nom_produit", "")
                sku_key = l.get("sku_variation") or sku
                if sku_key:
                    ventes_wizi[sku_key] = ventes_wizi.get(sku_key, 0) + (l.get("quantite") or 0)

    if cmds_etsy:
        ids_str = ",".join(str(c["id_wizi"]) for c in cmds_etsy if c.get("id_wizi"))
        lignes = select("lignes_commande",
            f"select=sku,sku_variation,quantite,nom_produit&id_commande=in.({ids_str})",
            limit=50000)
        if lignes:
            for l in lignes:
                sku = l.get("sku")
                if sku and sku not in nom_par_sku:
                    nom_par_sku[sku] = l.get("nom_produit", "")
                sku_key = l.get("sku_variation") or sku
                if sku_key:
                    ventes_etsy[sku_key] = ventes_etsy.get(sku_key, 0) + (l.get("quantite") or 0)

    if cmds_faire:
        ids_str = ",".join(str(c["id_faire"]) for c in cmds_faire if c.get("id_faire"))
        lignes = select("lignes_commande",
            f"select=sku,quantite,nom_produit&id_commande=in.({ids_str})",
            limit=50000)
        if lignes:
            mapping_data = select("sku_mapping_faire", "select=sku_faire,sku_wizishop")
            sku_mapping_local = {m["sku_faire"]: m["sku_wizishop"] for m in mapping_data} if mapping_data else {}
            for l in lignes:
                sku = l.get("sku") or ""
                sku_resolu = sku_mapping_local.get(sku, sku) if sku else ""
                if sku_resolu and sku_resolu not in nom_par_sku:
                    nom_par_sku[sku_resolu] = l.get("nom_produit", "")
                if sku_resolu:
                    ventes_faire[sku_resolu] = ventes_faire.get(sku_resolu, 0) + (l.get("quantite") or 0)

    return ventes_wizi, ventes_etsy, ventes_faire, nom_par_sku


st.title("Pique&Pince — Dashboard ventes")

# ── Navigation groupée ────────────────────────────────────────────────────────

_NAV_GROUPES = {
    "📊 Général":       ["📊 Vue d'ensemble"],
    "📊 Analytique":    ["🎨 Meilleures variations", "📊 CA par catégories", "🐌 Produits peu vendus"],
    "🛍️ Wizishop":     ["📦 Commandes", "👥 Clients", "⭐ Best-sellers", "🚨 Réapprovisionnement",
                         "🏭 Stock & Fournisseurs", "🔍 Vérification Wizishop",
                         "💎 Valorisation du stock", "🗂️ Catalogue par catégories",
                         "📈 Évolution CA annuelle"],
    "🏷️ Etsy":         ["⭐ Best-sellers Etsy", "📊 Gestion stock Etsy",
                         "🔎 Produits manquants sur Etsy", "🔍 Vérification Etsy",
                         "📒 Export comptable Etsy"],
    "🛒 Faire":         ["⭐ Best-sellers Faire", "🔍 Vérification Faire", "📒 Réconciliation Faire",
                         "📊 Gestion stock Faire", "🔎 Produits manquants sur Faire",
                         "🚀 Créer produits sur Faire"],
    "🧣 Foulard Frenchy": ["⭐ Best-sellers Foulard Frenchy", "🚨 Réapprovisionnement Foulard Frenchy"],
    "🛍️ Ankorstore":    ["⭐ Best-sellers Ankorstore", "📊 Gestion stock Ankorstore",
                         "🔎 Produits manquants sur Ankorstore", "🔍 Vérification Ankorstore"],
    "⚙️ Outils":       ["🌍 Comptabilité TVA", "🔗 Connexion Faire/Shopify", "🔄 Synchronisation"],
}

_NAV_KEYS = {g: f"_nav_{i}" for i, g in enumerate(_NAV_GROUPES)}


def _nav_change(key):
    val = st.session_state.get(key)
    if val:
        st.session_state["page"] = val
    for k in _NAV_KEYS.values():
        if k != key:
            st.session_state[k] = None


# Initialisation au premier chargement
if "page" not in st.session_state:
    st.session_state["page"] = "📊 Vue d'ensemble"
    st.session_state[_NAV_KEYS["📊 Général"]] = "📊 Vue d'ensemble"

with st.sidebar:
    st.markdown("### Navigation")
    for groupe, pages in _NAV_GROUPES.items():
        key = _NAV_KEYS[groupe]
        st.radio(
            groupe,
            pages,
            index=None,
            key=key,
            on_change=_nav_change,
            args=(key,),
        )

page = st.session_state.get("page", "📊 Vue d'ensemble")

if page == "🔄 Synchronisation":
    st.subheader("Synchronisation des données")
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.subheader("🛍️ Wizishop")
        token_cached = st.session_state.get("wizi_token")
        shop_id_cached = st.session_state.get("wizi_shop_id")

        if not token_cached:
            with st.spinner("Connexion à Wizishop..."):
                token, _, shop_id = get_wizi_token()
                if token:
                    st.session_state["wizi_token"] = token
                    st.session_state["wizi_shop_id"] = shop_id
                    token_cached = token
                    shop_id_cached = shop_id
                else:
                    st.error("Impossible de se connecter à Wizishop.")

        if token_cached:
            if st.button("1️⃣ Sync Catégories & Marques", use_container_width=True):
                with st.spinner("Synchronisation..."):
                    debut = time.time()
                    try:
                        nb_cat = sync_categories(token_cached, shop_id_cached)
                        nb_mar = sync_marques(token_cached, shop_id_cached)
                        duree = time.time() - debut
                        log_sync("categories", "wizishop", nb_cat, "success", f"{nb_cat} enregistrements", duree)
                        log_sync("marques", "wizishop", nb_mar, "success", f"{nb_mar} enregistrements", duree)
                        st.success(f"✓ {nb_cat} catégories et {nb_mar} marques en {duree:.1f}s")
                    except Exception as e:
                        st.error(f"Erreur : {e}")

            if st.button("2️⃣ Sync SKUs & Stock", use_container_width=True):
                with st.spinner("Synchronisation SKUs... (2-3 min)"):
                    debut = time.time()
                    try:
                        nb = sync_skus(token_cached, shop_id_cached)
                        duree = time.time() - debut
                        log_sync("skus", "wizishop", nb, "success", f"{nb} enregistrements", duree)
                        st.success(f"✓ {nb} SKUs en {duree:.1f}s")
                    except Exception as e:
                        st.error(f"Erreur : {e}")

            if st.button("3️⃣ Sync Commandes Wizishop", use_container_width=True):
                with st.spinner("Synchronisation commandes... (10-15 min)"):
                    debut = time.time()
                    try:
                        nb = sync_commandes(token_cached, shop_id_cached)
                        duree = time.time() - debut
                        log_sync("commandes", "wizishop", nb, "success", f"{nb} enregistrements", duree)
                        st.success(f"✓ {nb} commandes en {duree:.1f}s")
                    except Exception as e:
                        st.error(f"Erreur : {e}")

            if st.button("4️⃣ Sync Produits Wizishop (lent ~2h)", use_container_width=True):
                with st.spinner("Synchronisation produits... (très long, ne pas fermer la page)"):
                    debut = time.time()
                    try:
                        nb = sync_produits(token_cached, shop_id_cached)
                        duree = time.time() - debut
                        log_sync("produits", "wizishop", nb, "success", f"{nb} enregistrements", duree)
                        st.success(f"✓ {nb} produits en {duree:.1f}s")
                    except Exception as e:
                        st.error(f"Erreur : {e}")

    with col2:
        st.subheader("🏷️ Etsy")

        if st.button("5️⃣ Sync Commandes Etsy", use_container_width=True):
            with st.spinner("Connexion à Etsy et synchronisation..."):
                debut = time.time()
                try:
                    shop_id_etsy = get_shop_id()
                    if shop_id_etsy:
                        nb = sync_etsy_commandes(shop_id_etsy)
                        duree = time.time() - debut
                        log_sync_etsy("commandes_etsy", nb, "success", f"{nb} commandes", duree)
                        st.success(f"✓ {nb} commandes Etsy en {duree:.1f}s")
                    else:
                        st.error("Impossible de récupérer le shop_id Etsy.")
                except Exception as e:
                    st.error(f"Erreur : {e}")

        if st.button("6️⃣ Sync Produits Etsy", use_container_width=True):
            with st.spinner("Synchronisation produits Etsy..."):
                debut = time.time()
                try:
                    shop_id_etsy = get_shop_id()
                    if shop_id_etsy:
                        nb_listings, nb_variations = sync_produits_etsy(shop_id_etsy)
                        duree = time.time() - debut
                        log_sync_etsy("produits_etsy", nb_listings, "success",
                                     f"{nb_listings} listings, {nb_variations} variations", duree)
                        st.success(f"✓ {nb_listings} listings et {nb_variations} variations en {duree:.1f}s")
                    else:
                        st.error("Impossible de récupérer le shop_id Etsy.")
                except Exception as e:
                    st.error(f"Erreur : {e}")

        st.info("💡 Le token Etsy se rafraîchit automatiquement.")

    with col3:
        st.subheader("🛒 Faire")

        if st.button("7️⃣ Sync Commandes Faire", use_container_width=True):
            with st.spinner("Synchronisation commandes Faire..."):
                debut = time.time()
                try:
                    nb = sync_faire_commandes()
                    duree = time.time() - debut
                    log_sync_faire("commandes_faire", nb, "success", f"{nb} commandes", duree)
                    st.success(f"✓ {nb} commandes Faire en {duree:.1f}s")
                except Exception as e:
                    st.error(f"Erreur : {e}")

        if st.button("8️⃣ Sync Produits Faire", use_container_width=True):
            with st.spinner("Synchronisation produits Faire..."):
                debut = time.time()
                try:
                    nb_prod, nb_var = sync_faire_produits()
                    duree = time.time() - debut
                    log_sync_faire("produits_faire", nb_prod, "success",
                                   f"{nb_prod} produits, {nb_var} variants", duree)
                    st.success(f"✓ {nb_prod} produits et {nb_var} variants en {duree:.1f}s")
                except Exception as e:
                    st.error(f"Erreur : {e}")

    with col4:
        st.subheader("🧣 Foulard Frenchy")

        if st.button("9️⃣ Sync Produits Foulard Frenchy", use_container_width=True):
            with st.spinner("Synchronisation produits Shopify Foulard Frenchy..."):
                debut = time.time()
                try:
                    shop_ff, token_ff = get_shopify_token()
                    nb_prod, nb_var = sync_shopify_produits("foulard_frenchy", shop_ff, token_ff)
                    duree = time.time() - debut
                    log_sync_shopify("foulard_frenchy", "produits_shopify", nb_prod, "success",
                                     f"{nb_prod} produits, {nb_var} variants", duree)
                    st.success(f"✓ {nb_prod} produits et {nb_var} variants en {duree:.1f}s")
                except Exception as e:
                    st.error(f"Erreur : {e}")

        if st.button("🔟 Sync Commandes Foulard Frenchy", use_container_width=True):
            with st.spinner("Synchronisation commandes Shopify Foulard Frenchy..."):
                debut = time.time()
                try:
                    shop_ff, token_ff = get_shopify_token()
                    nb = sync_shopify_commandes("foulard_frenchy", shop_ff, token_ff)
                    duree = time.time() - debut
                    log_sync_shopify("foulard_frenchy", "commandes_shopify", nb, "success",
                                     f"{nb} commandes", duree)
                    st.success(f"✓ {nb} commandes en {duree:.1f}s")
                except Exception as e:
                    st.error(f"Erreur : {e}")

    with col5:
        st.subheader("🧸 Montessori")

        if st.button("1️⃣1️⃣ Sync Produits Montessori", use_container_width=True):
            with st.spinner("Synchronisation produits Shopify Montessori..."):
                debut = time.time()
                try:
                    shop_m, token_m = get_shopify_token_montessori()
                    nb_prod, nb_var = sync_shopify_produits("montessori", shop_m, token_m)
                    duree = time.time() - debut
                    log_sync_shopify("montessori", "produits_shopify", nb_prod, "success",
                                     f"{nb_prod} produits, {nb_var} variants", duree)
                    st.success(f"✓ {nb_prod} produits et {nb_var} variants en {duree:.1f}s")
                except Exception as e:
                    st.error(f"Erreur : {e}")

        if st.button("1️⃣2️⃣ Sync Commandes Montessori", use_container_width=True):
            with st.spinner("Synchronisation commandes Shopify Montessori..."):
                debut = time.time()
                try:
                    shop_m, token_m = get_shopify_token_montessori()
                    nb = sync_shopify_commandes("montessori", shop_m, token_m)
                    duree = time.time() - debut
                    log_sync_shopify("montessori", "commandes_shopify", nb, "success",
                                     f"{nb} commandes", duree)
                    st.success(f"✓ {nb} commandes en {duree:.1f}s")
                except Exception as e:
                    st.error(f"Erreur : {e}")

    logs = select("sync_log",
        "select=table_name,source,nb_enregistrements,statut,created_at&order=created_at.desc&limit=15")
    if logs:
        st.divider()
        st.subheader("Historique des synchronisations")
        df_logs = pd.DataFrame(logs)
        df_logs.columns = ["Table", "Source", "Nb", "Statut", "Date"]
        df_logs["Date"] = pd.to_datetime(df_logs["Date"]).dt.strftime("%d/%m/%Y %H:%M")
        st.dataframe(df_logs, use_container_width=True, hide_index=True)

elif page == "📊 Vue d'ensemble":
    with st.sidebar:
        st.divider()
        nb_mois = st.slider("Période (mois)", min_value=1, max_value=24, value=12)

    date_limite_str = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")
    commandes = select("commandes",
        f"select=date_commande,montant_ttc,statut_code,source&statut_code=not.in.(0,45,46,50)&date_commande=gte.{date_limite_str}&order=date_commande.desc")

    if commandes:
        df = pd.DataFrame(commandes)
        df["date_commande"] = pd.to_datetime(df["date_commande"]).dt.tz_convert(None)
        df["montant_ttc"] = pd.to_numeric(df["montant_ttc"], errors="coerce").fillna(0)
        df["mois"] = df["date_commande"].dt.strftime("%Y-%m")

        mois_max = df["mois"].max()
        df_mois = df[df["mois"] == mois_max]
        df_wizi = df[df["source"] == "wizishop"]
        df_etsy = df[df["source"] == "etsy"]
        df_faire = df[df["source"] == "faire"]
        df_mois_wizi = df_mois[df_mois["source"] == "wizishop"]
        df_mois_etsy = df_mois[df_mois["source"] == "etsy"]
        df_mois_faire = df_mois[df_mois["source"] == "faire"]

        st.subheader("📊 Vue consolidée")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Commandes ce mois", len(df_mois))
        with col2:
            st.metric("CA ce mois (TTC)", f"{df_mois['montant_ttc'].sum():.0f} €")
        with col3:
            st.metric(f"CA sur {nb_mois} mois (TTC)", f"{df['montant_ttc'].sum():.0f} €")
        with col4:
            st.metric(f"Commandes sur {nb_mois} mois", len(df))

        st.divider()
        col_w, col_e, col_f = st.columns(3)
        with col_w:
            st.subheader("🛍️ Wizishop")
            w1, w2, w3 = st.columns(3)
            with w1:
                st.metric("Commandes ce mois", len(df_mois_wizi))
            with w2:
                st.metric("CA ce mois", f"{df_mois_wizi['montant_ttc'].sum():.0f} €")
            with w3:
                st.metric(f"CA {nb_mois} mois", f"{df_wizi['montant_ttc'].sum():.0f} €")

        with col_e:
            st.subheader("🏷️ Etsy")
            e1, e2, e3 = st.columns(3)
            with e1:
                st.metric("Commandes ce mois", len(df_mois_etsy))
            with e2:
                st.metric("CA ce mois", f"{df_mois_etsy['montant_ttc'].sum():.0f} €")
            with e3:
                st.metric(f"CA {nb_mois} mois", f"{df_etsy['montant_ttc'].sum():.0f} €")

        with col_f:
            st.subheader("🛒 Faire")
            f1, f2, f3 = st.columns(3)
            with f1:
                st.metric("Commandes ce mois", len(df_mois_faire))
            with f2:
                st.metric("CA ce mois", f"{df_mois_faire['montant_ttc'].sum():.0f} €")
            with f3:
                st.metric(f"CA {nb_mois} mois", f"{df_faire['montant_ttc'].sum():.0f} €")

        st.divider()
        par_mois = df.groupby(["mois", "source"]).agg(
            Commandes=("montant_ttc", "count"),
            CA=("montant_ttc", "sum")
        ).reset_index().sort_values("mois")

        par_mois_pivot_ca = par_mois.pivot(index="mois", columns="source", values="CA").fillna(0)
        par_mois_pivot_cmd = par_mois.pivot(index="mois", columns="source", values="Commandes").fillna(0)

        col_g1, col_g2 = st.columns(2)
        with col_g1:
            st.subheader("Commandes par mois")
            st.bar_chart(par_mois_pivot_cmd)
        with col_g2:
            st.subheader("CA par mois (€)")
            st.bar_chart(par_mois_pivot_ca)
    else:
        st.info("Aucune donnée. Lance d'abord une synchronisation depuis le menu 🔄.")

elif page == "📦 Commandes":
    with st.sidebar:
        st.divider()
        nb_mois = st.slider("Période (mois)", min_value=1, max_value=24, value=3)
        source_filtre = st.selectbox("Source", ["Toutes", "Wizishop", "Etsy"])

    date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")
    query = f"select=date_commande,numero_commande,nom_facturation,prenom_facturation,montant_ttc,statut_texte,pays_facturation_iso,zone_tva,numero_suivi,source&date_commande=gte.{date_limite}&statut_code=not.in.(0,45,46,50)&order=date_commande.desc&limit=500"
    if source_filtre == "Wizishop":
        query += "&source=eq.wizishop"
    elif source_filtre == "Etsy":
        query += "&source=eq.etsy"

    commandes = select("commandes", query)

    if commandes:
        df = pd.DataFrame(commandes)
        df["date_commande"] = pd.to_datetime(df["date_commande"]).dt.strftime("%d/%m/%Y")
        df.columns = ["Date", "N° commande", "Nom", "Prénom", "Montant (€)",
                     "Statut", "Pays", "Zone TVA", "Suivi", "Source"]
        st.subheader(f"Commandes — {nb_mois} derniers mois")
        st.dataframe(df, use_container_width=True, hide_index=True)
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("Télécharger en CSV", csv, f"commandes_{nb_mois}mois.csv", "text/csv")
    else:
        st.info("Aucune commande. Lance d'abord une synchronisation.")

elif page == "👥 Clients":
    st.subheader("👥 Clients")

    commandes_clients = select("commandes",
        "select=email_client,date_commande,montant_ht,nom_facturation,prenom_facturation"
        "&source=eq.wizishop"
        "&statut_code=not.in.(0,45,46,50)")

    if not commandes_clients:
        st.info("Données indisponibles. Lance d'abord une synchronisation depuis le menu 🔄.")
    else:
        df = pd.DataFrame(commandes_clients)
        df = df[df["email_client"].notna() & (df["email_client"] != "")]
        df["date_commande"] = pd.to_datetime(df["date_commande"], errors="coerce", utc=True).dt.tz_localize(None)
        df["montant_ht"] = pd.to_numeric(df["montant_ht"], errors="coerce").fillna(0)

        date_2024 = pd.Timestamp("2024-01-01")
        maintenant = pd.Timestamp.now()

        rows_clients = []
        for email, g in df.groupby("email_client"):
            date_premiere = g["date_commande"].min()
            date_derniere = g["date_commande"].max()
            nb_total = len(g)
            nb_2024 = int((g["date_commande"] >= date_2024).sum())
            if nb_2024 == 0:
                continue

            ca_total = g["montant_ht"].sum()
            panier_moyen = ca_total / nb_total if nb_total else 0.0
            mois_inactif = (maintenant - date_derniere).days / 30 if pd.notna(date_derniere) else None

            g_sorted = g.sort_values("date_commande")
            noms = g_sorted["nom_facturation"].dropna()
            prenoms = g_sorted["prenom_facturation"].dropna()
            nom = noms.iloc[-1] if not noms.empty else ""
            prenom = prenoms.iloc[-1] if not prenoms.empty else ""

            rows_clients.append({
                "Email": email,
                "Nom": nom,
                "Prénom": prenom,
                "nb_commandes_total": nb_total,
                "nb_commandes_2024": nb_2024,
                "date_premiere_commande": date_premiere,
                "date_derniere_commande": date_derniere,
                "mois_inactif": mois_inactif,
                "ca_total": ca_total,
                "panier_moyen": panier_moyen,
            })

        df_clients = pd.DataFrame(rows_clients)

        with st.sidebar:
            st.divider()
            nb_cmd_min = st.number_input("Nb commandes total (min)", min_value=1, value=1, step=1, key="clients_nb_cmd_min")
            mois_inactif_min = st.number_input("Inactif depuis plus de X mois", min_value=0.0, value=0.0, step=1.0, key="clients_mois_inactif")
            ca_total_min = st.number_input("CA total minimum (€)", min_value=0.0, value=0.0, step=10.0, key="clients_ca_min")
            panier_moyen_min = st.number_input("Panier moyen minimum (€)", min_value=0.0, value=0.0, step=5.0, key="clients_panier_min")

        if not df_clients.empty:
            df_clients = df_clients[df_clients["nb_commandes_total"] >= nb_cmd_min]
            if mois_inactif_min > 0:
                df_clients = df_clients[df_clients["mois_inactif"] > mois_inactif_min]
            df_clients = df_clients[df_clients["ca_total"] >= ca_total_min]
            df_clients = df_clients[df_clients["panier_moyen"] >= panier_moyen_min]

        nb_clients = len(df_clients)
        ca_moyen_client = df_clients["ca_total"].mean() if nb_clients else 0.0
        pct_une_commande = (df_clients["nb_commandes_total"] == 1).mean() * 100 if nb_clients else 0.0

        col1, col2, col3 = st.columns(3)
        col1.metric("Nb clients filtrés", nb_clients)
        col2.metric("CA moyen par client", f"{ca_moyen_client:,.2f} €".replace(",", " "))
        col3.metric("% clients avec 1 seule commande", f"{pct_une_commande:.1f} %")

        if df_clients.empty:
            st.info("Aucun client pour ces filtres.")
        else:
            df_clients_sorted = df_clients.sort_values("date_derniere_commande", ascending=False).reset_index(drop=True)

            display_df = df_clients_sorted.rename(columns={
                "nb_commandes_total": "Nb commandes",
                "date_derniere_commande": "Dernière commande",
                "mois_inactif": "Mois inactif",
                "ca_total": "CA total (€)",
                "panier_moyen": "Panier moyen (€)",
            })[["Email", "Nom", "Prénom", "Nb commandes", "Dernière commande",
                "Mois inactif", "CA total (€)", "Panier moyen (€)"]]
            display_df["Dernière commande"] = display_df["Dernière commande"].dt.strftime("%d/%m/%Y")

            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Mois inactif": st.column_config.NumberColumn(format="%.1f"),
                    "CA total (€)": st.column_config.NumberColumn(format="%.2f €"),
                    "Panier moyen (€)": st.column_config.NumberColumn(format="%.2f €"),
                },
            )

            csv_emails = display_df[["Email", "Nom", "Prénom"]].to_csv(index=False).encode("utf-8")
            st.download_button("Télécharger les emails (CSV)", csv_emails, "clients_emails.csv", "text/csv")

elif page == "⭐ Best-sellers":
    with st.sidebar:
        st.divider()
        nb_mois = st.slider("Période (mois)", min_value=1, max_value=24, value=12)
        source_filtre = st.selectbox("Source", ["Toutes", "Wizishop", "Etsy"])

    date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")

    query_cmd = f"select=id_wizi,source&statut_code=not.in.(0,45,46,50)&date_commande=gte.{date_limite}"
    if source_filtre == "Wizishop":
        query_cmd += "&source=eq.wizishop"
    elif source_filtre == "Etsy":
        query_cmd += "&source=eq.etsy"

    commandes_valides = select("commandes", query_cmd)

    if commandes_valides:
        ids_valides = [str(c["id_wizi"]) for c in commandes_valides]
        ids_str = ",".join(ids_valides)
        source_map = {str(c["id_wizi"]): c["source"] for c in commandes_valides}

        lignes = select("lignes_commande",
            f"select=sku,sku_variation,libelle_variation,nom_produit,quantite,prix_unitaire_ttc,id_commande&id_commande=in.({ids_str})",
            limit=50000)

        produits = select("produits", "select=sku,nom,nom_categorie,fournisseur")
        prod_map = {p["sku"]: p for p in produits} if produits else {}

        if lignes:
            df_lignes = pd.DataFrame(lignes)
            df_lignes["quantite"] = pd.to_numeric(df_lignes["quantite"], errors="coerce").fillna(0)
            df_lignes["prix_unitaire_ttc"] = pd.to_numeric(df_lignes["prix_unitaire_ttc"], errors="coerce").fillna(0)
            df_lignes["ca"] = df_lignes["quantite"] * df_lignes["prix_unitaire_ttc"]
            df_lignes["source"] = df_lignes["id_commande"].astype(str).map(source_map).fillna("wizishop")
            df_lignes["sku_variation"] = df_lignes["sku_variation"].fillna("")
            df_lignes["libelle_variation"] = df_lignes["libelle_variation"].fillna("—")

            st.subheader(f"📊 Tableau 1 — Best-sellers par produit ({nb_mois} derniers mois)")

            grp_wizi = df_lignes[df_lignes["source"] == "wizishop"].groupby("sku").agg(
                vendu_wizi=("quantite", "sum")).reset_index()
            grp_etsy = df_lignes[df_lignes["source"] == "etsy"].groupby("sku").agg(
                vendu_etsy=("quantite", "sum")).reset_index()
            grp_ca = df_lignes.groupby("sku").agg(
                ca_total=("ca", "sum"),
                nb_commandes=("id_commande", "nunique")
            ).reset_index()

            bs_produit = grp_ca.merge(grp_wizi, on="sku", how="left")
            bs_produit = bs_produit.merge(grp_etsy, on="sku", how="left")
            bs_produit["vendu_wizi"] = bs_produit["vendu_wizi"].fillna(0).astype(int)
            bs_produit["vendu_etsy"] = bs_produit["vendu_etsy"].fillna(0).astype(int)
            bs_produit["total_vendu"] = bs_produit["vendu_wizi"] + bs_produit["vendu_etsy"]
            bs_produit["moy_mois"] = (bs_produit["total_vendu"] / nb_mois).round(1)
            bs_produit["nom"] = bs_produit["sku"].map(
                lambda x: get_prod_parent(x, prod_map).get("nom", "") or str(x))
            bs_produit["categorie"] = bs_produit["sku"].map(
                lambda x: get_prod_parent(x, prod_map).get("nom_categorie", "") or "")

            bs_produit = bs_produit.sort_values("total_vendu", ascending=False).head(100)
            bs_produit = bs_produit[["sku", "nom", "categorie", "vendu_wizi",
                                     "vendu_etsy", "total_vendu", "moy_mois",
                                     "ca_total", "nb_commandes"]]
            bs_produit.columns = ["SKU", "Produit", "Catégorie", "Wizishop",
                                  "Etsy", "Total vendu", "Moy/mois", "CA (€)", "Nb commandes"]
            bs_produit["CA (€)"] = bs_produit["CA (€)"].apply(lambda x: f"{x:.2f}")

            st.dataframe(bs_produit, use_container_width=True, hide_index=True)
            csv1 = bs_produit.to_csv(index=False).encode("utf-8")
            st.download_button("Télécharger tableau 1", csv1, "bestsellers_produits.csv", "text/csv")

            st.divider()
            st.subheader(f"🎨 Tableau 2 — Best-sellers par variation ({nb_mois} derniers mois)")

            skus_data = select("skus", "select=sku,stock&statut=eq.visible")
            sku_stock = {s["sku"]: int(s["stock"] or 0) for s in skus_data} if skus_data else {}

            df_lignes["sku_effectif"] = df_lignes.apply(
                lambda r: r["sku_variation"] if r["sku_variation"] else r["sku"], axis=1)

            rows_var = []
            for sku_eff, grp in df_lignes.groupby("sku_effectif"):
                if not sku_eff:
                    continue
                sku_parent = grp["sku"].iloc[0]
                prod_info = get_prod_parent(sku_eff, prod_map) or get_prod_parent(sku_parent, prod_map)
                grp_wizi_rows = grp[grp["source"] == "wizishop"]
                nom = prod_info.get("nom", "") or \
                      (grp_wizi_rows["nom_produit"].iloc[0] if len(grp_wizi_rows) > 0
                       else grp["nom_produit"].iloc[0]) or str(sku_eff)
                cat = prod_info.get("nom_categorie", "") or ""
                variation = grp["libelle_variation"].iloc[0] if grp["libelle_variation"].iloc[0] != "—" else "—"

                total_vendu = int(grp["quantite"].sum())
                ca_total = grp["ca"].sum()
                nb_cmd = grp["id_commande"].nunique()
                vendu_wizi = int(grp[grp["source"] == "wizishop"]["quantite"].sum())
                vendu_etsy = int(grp[grp["source"] == "etsy"]["quantite"].sum())
                stock = sku_stock.get(str(sku_eff), 0)
                moy_mois = round(total_vendu / nb_mois, 1)
                mois_stock = round(stock / moy_mois, 1) if moy_mois > 0 else 99

                rows_var.append({
                    "SKU": sku_eff, "Produit": nom, "Variation": variation,
                    "Catégorie": cat, "Wizishop": vendu_wizi, "Etsy": vendu_etsy,
                    "Total vendu": total_vendu, "Stock": stock,
                    "Moy/mois": moy_mois, "Mois de stock": mois_stock,
                    "CA (€)": f"{ca_total:.2f}", "Nb commandes": nb_cmd
                })

            if rows_var:
                df_var_final = pd.DataFrame(rows_var).sort_values("Total vendu", ascending=False)

                def alerte(mois):
                    if mois <= 3:
                        return "🔴 Commander"
                    elif mois <= 5:
                        return "🟡 Surveiller"
                    else:
                        return "🟢 OK"

                df_var_final["Alerte"] = df_var_final["Mois de stock"].apply(alerte)
                st.dataframe(df_var_final, use_container_width=True, hide_index=True)
                csv2 = df_var_final.to_csv(index=False).encode("utf-8")
                st.download_button("Télécharger tableau 2", csv2, "bestsellers_variations.csv", "text/csv")
            else:
                st.info("Aucune donnée de variation disponible.")
        else:
            st.info("Aucune ligne de commande trouvée.")
    else:
        st.info("Aucune commande valide trouvée.")

elif page == "🚨 Réapprovisionnement":
    with st.sidebar:
        st.divider()
        nb_mois = st.slider("Période calcul ventes (mois)", min_value=1, max_value=12, value=6)
        alerte_filtre = st.selectbox("Filtre alerte", [
            "Tous les produits",
            "🔴 À commander uniquement",
            "🔴 + 🟡 Surveiller"
        ])

    st.subheader("🚨 Réapprovisionnement")
    st.info(f"Calcul basé sur les ventes des {nb_mois} derniers mois. Délai fournisseur : 2 mois.")

    en_commande = select("commandes_fournisseur",
        "select=id,sku,nom_produit,fournisseur,date_commande,quantite_commandee,quantite_attendue,quantite_recue"
        "&statut=in.(en_commande,recu_partiel)&order=date_commande.desc")

    skus_exclu = set()
    skus_partiels = {}
    commandes_par_sku = {}
    if en_commande:
        for c in en_commande:
            sku_c = c["sku"]
            qty_cmd = int(c.get("quantite_commandee") or 0)
            qty_att = int(c.get("quantite_attendue") or 0)
            commandes_par_sku[sku_c] = c
            if qty_att == 0 or qty_cmd >= qty_att:
                skus_exclu.add(sku_c)
            else:
                skus_partiels[sku_c] = c

    ignores_data = select("skus_ignores", "select=sku,nom_produit,fournisseur,raison,created_at&order=created_at.desc")
    skus_ignores_set = {r["sku"] for r in ignores_data} if ignores_data else set()

    if "reap_sku_selectionne" not in st.session_state:
        st.session_state["reap_sku_selectionne"] = ""
    if "reap_quantite" not in st.session_state:
        st.session_state["reap_quantite"] = 0

    date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")

    skus_data = select("skus", "select=sku,stock,statut&statut=eq.visible")
    prod_map, sku_mapping = _get_produits_reap()
    ventes_wizi, ventes_etsy, ventes_faire, nom_par_sku = _get_ventes_reap(date_limite)

    qty_attendue_map = {}
    rows = []
    if skus_data:
        for sku_item in skus_data:
            sku = sku_item.get("sku")
            if sku in skus_exclu or sku in skus_ignores_set:
                continue
            stock = int(sku_item.get("stock") or 0)
            prod = get_prod_parent(sku, prod_map)
            nom = prod.get("nom") or nom_par_sku.get(sku, "") or sku
            fournisseur = prod.get("fournisseur") or ""
            ref_fourn = prod.get("reference_fournisseur") or ""
            prix_achat = prod.get("prix_achat_ht") or 0
            categorie = prod.get("nom_categorie") or ""

            v_wizi = round(ventes_wizi.get(sku, 0) / nb_mois, 1)
            v_etsy = round(ventes_etsy.get(sku, 0) / nb_mois, 1)
            v_faire = round(ventes_faire.get(sku, 0) / nb_mois, 1)
            v_total = round((ventes_wizi.get(sku, 0) + ventes_etsy.get(sku, 0) + ventes_faire.get(sku, 0)) / nb_mois, 1)
            mois_stock = round(stock / v_total, 1) if v_total > 0 else 99

            qty_attendue_map[sku] = max(0, round(v_total * 4) - stock) if v_total > 0 else 0

            if sku in skus_partiels:
                alerte = "⚠️ Commande partielle"
                cmd_partielle = skus_partiels[sku]
                qty_commandee_partiel = int(cmd_partielle.get("quantite_commandee") or 0)
                qty_attendue_partiel = int(cmd_partielle.get("quantite_attendue") or 0)
                qty_a_commander = max(0, qty_attendue_partiel - qty_commandee_partiel)
                en_commande_label = f"📦 {qty_commandee_partiel} unités commandées"
            else:
                qty_a_commander = qty_attendue_map[sku]
                en_commande_label = ""
                if mois_stock <= 3:
                    alerte = "🔴 Commander"
                elif mois_stock <= 5:
                    alerte = "🟡 Surveiller"
                else:
                    alerte = "🟢 OK"

            rows.append({
                "sku": sku,
                "Produit": nom,
                "Catégorie": categorie,
                "Fournisseur": fournisseur,
                "Réf. fournisseur": ref_fourn,
                "Prix achat HT": f"{float(prix_achat):.2f} €" if prix_achat else "",
                "Stock": stock,
                "En commande": en_commande_label,
                "Qté à commander": qty_a_commander,
                "Ventes/mois Wizi": v_wizi,
                "Ventes/mois Etsy": v_etsy,
                "Ventes/mois Faire": v_faire,
                "Ventes/mois Total": v_total,
                "Mois de stock": mois_stock,
                "Alerte": alerte
            })

        df_reap = pd.DataFrame(rows)

        fournisseurs_liste = ["Tous"] + sorted(
            [f for f in df_reap["Fournisseur"].dropna().unique().tolist() if f])
        with st.sidebar:
            fournisseur_filtre = st.selectbox("Fournisseur", fournisseurs_liste)

        if fournisseur_filtre != "Tous":
            df_reap = df_reap[df_reap["Fournisseur"] == fournisseur_filtre]
        if alerte_filtre == "🔴 À commander uniquement":
            df_reap = df_reap[df_reap["Alerte"] == "🔴 Commander"]
        elif alerte_filtre == "🔴 + 🟡 Surveiller":
            df_reap = df_reap[df_reap["Alerte"].isin(["🔴 Commander", "🟡 Surveiller"])]

        df_reap = df_reap.sort_values("Mois de stock", ascending=True)

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("🔴 À commander", len(df_reap[df_reap["Alerte"] == "🔴 Commander"]))
        with col2:
            st.metric("🟡 À surveiller", len(df_reap[df_reap["Alerte"] == "🟡 Surveiller"]))
        with col3:
            st.metric("🟢 OK", len(df_reap[df_reap["Alerte"] == "🟢 OK"]))

        st.subheader("📋 Produits à réapprovisionner")

        if not df_reap.empty:
            cols_affich = ["sku", "Produit", "Catégorie", "Fournisseur",
                          "Réf. fournisseur", "Prix achat HT", "Stock", "En commande",
                          "Qté à commander", "Ventes/mois Wizi", "Ventes/mois Etsy",
                          "Ventes/mois Faire", "Ventes/mois Total", "Mois de stock", "Alerte"]
            df_show_csv = df_reap[cols_affich].copy()
            df_show_csv = df_show_csv.rename(columns={"sku": "SKU"})

            cols_editor = ["SKU", "Produit", "Fournisseur", "Réf. fournisseur", "Stock",
                           "Qté à commander", "Ventes/mois Total", "Mois de stock", "Alerte", "En commande"]
            df_editor = df_show_csv[cols_editor].copy()
            df_editor["Qté commandée"] = 0
            df_editor["Ignorer ?"] = False

            with st.form("form_reap"):
                edited = st.data_editor(
                    df_editor,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "SKU": st.column_config.TextColumn(disabled=True),
                        "Produit": st.column_config.TextColumn(disabled=True),
                        "Fournisseur": st.column_config.TextColumn(disabled=True),
                        "Réf. fournisseur": st.column_config.TextColumn(disabled=True),
                        "Stock": st.column_config.NumberColumn(disabled=True),
                        "Qté à commander": st.column_config.NumberColumn(disabled=True),
                        "Ventes/mois Total": st.column_config.NumberColumn(disabled=True),
                        "Mois de stock": st.column_config.NumberColumn(disabled=True),
                        "Alerte": st.column_config.TextColumn(disabled=True),
                        "En commande": st.column_config.TextColumn(disabled=True),
                        "Qté commandée": st.column_config.NumberColumn(min_value=0, step=1),
                        "Ignorer ?": st.column_config.CheckboxColumn(),
                    }
                )
                col_sub1, col_sub2 = st.columns(2)
                with col_sub1:
                    submitted_cmd = st.form_submit_button("📦 Marquer en commande", type="primary")
                with col_sub2:
                    submitted_ign = st.form_submit_button("🚫 Ignorer les produits sélectionnés", type="secondary")

            if submitted_cmd:
                a_commander = edited[edited["Qté commandée"] > 0]
                if a_commander.empty:
                    st.warning("Aucune quantité saisie.")
                else:
                    nb_ok = 0
                    for _, r in a_commander.iterrows():
                        sku_r = r["SKU"]
                        qty_att_r = int(qty_attendue_map.get(sku_r, 0))
                        upsert("commandes_fournisseur", [{
                            "sku": sku_r,
                            "nom_produit": r["Produit"],
                            "fournisseur": r["Fournisseur"],
                            "date_commande": datetime.now(timezone.utc).isoformat(),
                            "statut": "en_commande",
                            "quantite_commandee": int(r["Qté commandée"]),
                            "quantite_attendue": qty_att_r,
                        }], "sku")
                        nb_ok += 1
                    st.success(f"✓ {nb_ok} produit(s) marqué(s) en commande !")
                    st.rerun()

            if submitted_ign:
                a_ignorer = edited[edited["Ignorer ?"] == True]
                if a_ignorer.empty:
                    st.warning("Aucun produit sélectionné.")
                else:
                    nb_ign = 0
                    for _, r in a_ignorer.iterrows():
                        upsert("skus_ignores", [{
                            "sku": r["SKU"],
                            "nom_produit": r["Produit"],
                            "fournisseur": r["Fournisseur"],
                            "raison": None,
                        }], "sku")
                        nb_ign += 1
                    st.success(f"✓ {nb_ign} produit(s) ignoré(s) !")
                    st.rerun()

            st.divider()
            csv = df_show_csv.to_csv(index=False).encode("utf-8")
            st.download_button(
                "📥 Télécharger liste",
                csv,
                f"reapprovisionnement_{fournisseur_filtre}.csv",
                "text/csv"
            )
        else:
            st.info("Aucun produit à afficher avec ces filtres.")

    st.divider()
    st.subheader("📦 Produits en commande chez le fournisseur")

    if en_commande:
        df_cmd = pd.DataFrame(en_commande)
        for col_qty in ["quantite_commandee", "quantite_attendue", "quantite_recue"]:
            df_cmd[col_qty] = pd.to_numeric(df_cmd[col_qty], errors="coerce").fillna(0).astype(int)
        df_cmd["date_commande"] = pd.to_datetime(df_cmd["date_commande"]).dt.strftime("%d/%m/%Y")
        df_cmd["Statut"] = df_cmd.apply(
            lambda r: "⚠️ Partielle" if r["quantite_commandee"] < r["quantite_attendue"] else "✅ Complète", axis=1
        )
        df_show_cmd = df_cmd[["sku", "nom_produit", "fournisseur", "date_commande",
                               "quantite_commandee", "quantite_attendue", "Statut"]].copy()
        df_show_cmd.columns = ["SKU", "Produit", "Fournisseur", "Date commande",
                                "Qté commandée", "Qté attendue", "Statut"]
        df_recu_editor = df_show_cmd.copy()
        df_recu_editor["Reçu ?"] = False
        df_recu_editor["Qté reçue"] = df_recu_editor["Qté commandée"]

        with st.form("form_recu"):
            edited_recu = st.data_editor(
                df_recu_editor,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "SKU": st.column_config.TextColumn(disabled=True),
                    "Produit": st.column_config.TextColumn(disabled=True),
                    "Fournisseur": st.column_config.TextColumn(disabled=True),
                    "Date commande": st.column_config.TextColumn(disabled=True),
                    "Qté commandée": st.column_config.NumberColumn(disabled=True),
                    "Qté attendue": st.column_config.NumberColumn(disabled=True),
                    "Statut": st.column_config.TextColumn(disabled=True),
                    "Reçu ?": st.column_config.CheckboxColumn(),
                    "Qté reçue": st.column_config.NumberColumn(min_value=0, step=1),
                }
            )
            submitted_recu = st.form_submit_button("✅ Marquer comme reçu", type="primary")

        if submitted_recu:
            a_recevoir = edited_recu[edited_recu["Reçu ?"] == True]
            if a_recevoir.empty:
                st.warning("Aucune ligne sélectionnée.")
            else:
                nb_recu = 0
                for _, r in a_recevoir.iterrows():
                    sku_r = r["SKU"]
                    row_orig = df_cmd[df_cmd["sku"] == sku_r].iloc[0]
                    qty_cmd_val = int(row_orig["quantite_commandee"])
                    qty_recue_val = int(r["Qté reçue"])
                    new_statut = "recu" if qty_recue_val >= qty_cmd_val else "recu_partiel"
                    upsert("commandes_fournisseur", [{
                        "id": int(row_orig["id"]),
                        "sku": sku_r,
                        "statut": new_statut,
                        "quantite_recue": qty_recue_val,
                    }], "id")
                    nb_recu += 1
                st.success(f"✓ {nb_recu} produit(s) mis à jour !")
                st.rerun()
    else:
        st.info("Aucun produit en commande actuellement.")

    st.divider()
    st.subheader("🚫 Produits ignorés")
    if ignores_data:
        df_ign = pd.DataFrame(ignores_data)
        df_ign["created_at"] = pd.to_datetime(df_ign["created_at"]).dt.strftime("%d/%m/%Y")
        df_ign_show = df_ign[["sku", "nom_produit", "fournisseur", "raison", "created_at"]].copy()
        df_ign_show.columns = ["SKU", "Produit", "Fournisseur", "Raison", "Date"]
        st.dataframe(df_ign_show, use_container_width=True, hide_index=True)

        st.divider()
        col_ret1, col_ret2 = st.columns([3, 1])
        with col_ret1:
            sku_retirer = st.selectbox(
                "Sélectionner un SKU à remettre en suivi",
                options=[""] + df_ign["sku"].tolist(),
                format_func=lambda x: f"{x} — {df_ign[df_ign['sku']==x]['nom_produit'].iloc[0]}"
                if x else "Choisir un SKU...",
                key="selectbox_retirer"
            )
        with col_ret2:
            st.write("")
            st.write("")
            if sku_retirer and st.button("↩️ Remettre en suivi", type="secondary"):
                delete("skus_ignores", f"sku=eq.{sku_retirer}")
                st.success(f"✓ {sku_retirer} remis en suivi !")
                st.rerun()
    else:
        st.info("Aucun produit ignoré.")

elif page == "🏭 Stock & Fournisseurs":
    with st.sidebar:
        st.divider()
        fournisseur_filtre = st.text_input("Filtrer par fournisseur")
        stock_filtre = st.selectbox("Stock", ["Tous", "En rupture (stock = 0)", "En stock (stock > 0)"])

    skus = select("skus",
        "select=sku,nom,fournisseur,stock,statut,date_maj_stock&statut=eq.visible&order=stock.asc")
    produits = select("produits", "select=id_wizi,sku,nom,fournisseur&statut=eq.visible")

    if skus:
        df_skus = pd.DataFrame(skus)
        df_skus["stock"] = pd.to_numeric(df_skus["stock"], errors="coerce").fillna(0).astype(int)

        if produits:
            df_prod = pd.DataFrame(produits)[["sku", "nom", "fournisseur"]].rename(
                columns={"nom": "nom_produit", "fournisseur": "fournisseur_prod"})
            df_skus = df_skus.merge(df_prod, on="sku", how="left")
            df_skus["nom"] = df_skus["nom"].fillna(df_skus.get("nom_produit", ""))
            df_skus["fournisseur"] = df_skus["fournisseur"].fillna(df_skus.get("fournisseur_prod", ""))

        if fournisseur_filtre:
            df_skus = df_skus[df_skus["fournisseur"].str.contains(
                fournisseur_filtre, case=False, na=False)]
        if stock_filtre == "En rupture (stock = 0)":
            df_skus = df_skus[df_skus["stock"] == 0]
        elif stock_filtre == "En stock (stock > 0)":
            df_skus = df_skus[df_skus["stock"] > 0]

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total SKUs actives", len(df_skus))
        with col2:
            st.metric("En rupture", len(df_skus[df_skus["stock"] == 0]))
        with col3:
            st.metric("En stock", len(df_skus[df_skus["stock"] > 0]))

        cols = [c for c in ["sku", "nom", "fournisseur", "stock", "date_maj_stock"]
                if c in df_skus.columns]
        df_affichage = df_skus[cols].copy()
        if "date_maj_stock" in df_affichage.columns:
            df_affichage["date_maj_stock"] = pd.to_datetime(
                df_affichage["date_maj_stock"], errors="coerce").dt.strftime("%d/%m/%Y")
        df_affichage.columns = ["SKU", "Produit", "Fournisseur", "Stock", "Dernière MAJ"][:len(cols)]
        st.dataframe(df_affichage, use_container_width=True, hide_index=True)
        csv = df_affichage.to_csv(index=False).encode("utf-8")
        st.download_button("Télécharger en CSV", csv, "stock.csv", "text/csv")
    else:
        st.info("Aucune donnée. Lance d'abord une synchronisation.")

elif page == "⭐ Best-sellers Etsy":
    with st.sidebar:
        st.divider()
        periode_etsy = st.selectbox("Période", ["3 mois", "6 mois", "12 mois", "Tout"],
                                    key="bs_etsy_periode")

    st.subheader("⭐ Best-sellers Etsy")

    if periode_etsy == "Tout":
        query_cmds = "select=id_wizi&source=eq.etsy&statut_code=not.in.(0,45,46,50)"
        nb_mois_etsy = None
    else:
        nb_mois_etsy = int(periode_etsy.split()[0])
        date_limite_etsy = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois_etsy)).strftime("%Y-%m-%dT%H:%M:%S")
        query_cmds = f"select=id_wizi&source=eq.etsy&statut_code=not.in.(0,45,46,50)&date_commande=gte.{date_limite_etsy}"

    # Catalogue complet Etsy (base du left join)
    catalogue_etsy = select("produits_etsy_variations",
        "select=sku,variation_valeur,listing_id&sku=not.is.null")
    # Dédoublonner par SKU en gardant le premier nom trouvé
    catalogue_skus = {}
    if catalogue_etsy:
        for r in catalogue_etsy:
            sku = r.get("sku") or ""
            if sku and sku not in catalogue_skus:
                catalogue_skus[sku] = r.get("variation_valeur") or ""

    cmds_etsy = select("commandes", query_cmds)

    # Agréger les ventes par SKU
    ventes_sku = {}
    if cmds_etsy:
        ids_str = ",".join(str(c["id_wizi"]) for c in cmds_etsy if c.get("id_wizi"))
        lignes_etsy = select("lignes_commande",
            f"select=sku,sku_variation,quantite,prix_unitaire_ttc,nom_produit"
            f"&id_commande=in.({ids_str})",
            limit=50000)
        if lignes_etsy:
            for l in lignes_etsy:
                sku_key = l.get("sku_variation") or l.get("sku") or ""
                if not sku_key:
                    continue
                qty = l.get("quantite") or 0
                prix_ttc = float(l.get("prix_unitaire_ttc") or 0)
                ca_ht = prix_ttc / 1.2 * qty
                if sku_key not in ventes_sku:
                    ventes_sku[sku_key] = {"quantite": 0, "ca_ht": 0.0, "nom": l.get("nom_produit") or ""}
                ventes_sku[sku_key]["quantite"] += qty
                ventes_sku[sku_key]["ca_ht"] += ca_ht

    if not catalogue_skus:
        st.info("Aucune donnée catalogue. Lance d'abord la sync Produits Etsy.")
    else:
        prod_map_etsy, _ = _get_produits_reap()

        rows_etsy = []
        # Tous les SKUs du catalogue (left join)
        for sku, nom_etsy in catalogue_skus.items():
            prod = get_prod_parent(sku, prod_map_etsy)
            nom = prod.get("nom") or nom_etsy or sku
            categorie = prod.get("nom_categorie") or ""
            data = ventes_sku.get(sku, {"quantite": 0, "ca_ht": 0.0})
            v_mois = round(data["quantite"] / nb_mois_etsy, 1) if nb_mois_etsy else (
                "—" if data["quantite"] == 0 else data["quantite"])
            rows_etsy.append({
                "SKU": sku,
                "Produit": nom,
                "Catégorie": categorie,
                "Unités vendues": data["quantite"],
                "CA HT (€)": round(data["ca_ht"], 2),
                "Ventes/mois": v_mois,
            })

        df_etsy = pd.DataFrame(rows_etsy).sort_values(
            ["Unités vendues", "SKU"], ascending=[False, True]
        )

        total_unites = df_etsy["Unités vendues"].sum()
        total_ca = df_etsy["CA HT (€)"].sum()
        nb_skus_vendus = (df_etsy["Unités vendues"] > 0).sum()

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("📦 Unités vendues", f"{total_unites:,}")
        with col2:
            st.metric("💶 CA HT", f"{total_ca:,.2f} €")
        with col3:
            st.metric("🔢 SKUs vendus", f"{nb_skus_vendus} / {len(df_etsy)}")

        st.dataframe(df_etsy, use_container_width=True, hide_index=True)
        csv_etsy = df_etsy.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Exporter CSV", csv_etsy, "bestsellers_etsy.csv", "text/csv",
                           key="bs_etsy_csv")

elif page == "📊 Gestion stock Etsy":
    st.subheader("📊 Gestion stock Etsy")

    with st.sidebar:
        st.divider()
        nb_mois = st.slider("Période ventes (mois)", min_value=1, max_value=12, value=3)
        seuil_jours = st.slider("Seuil jours de stock", min_value=7, max_value=90, value=15)
        alerte_filtre = st.selectbox("Filtre alerte", [
            "Toutes", "🔴 Urgent uniquement", "🟡 Attention uniquement", "🟢 OK uniquement"
        ])

    variations = select("produits_etsy_variations",
        "select=sku,stock_etsy,stock_wizishop,is_enabled,variation_valeur,listing_id")
    listings = select("produits_etsy", "select=listing_id,titre")
    skus_data = select("skus", "select=sku,stock")

    date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")
    commandes_valides = select("commandes",
        f"select=id_wizi,source&statut_code=not.in.(0,45,46,50)&date_commande=gte.{date_limite}")

    if variations:
        listing_map = {l["listing_id"]: l["titre"] for l in listings} if listings else {}
        sku_stock_wizi = {s["sku"]: int(s["stock"] or 0) for s in skus_data} if skus_data else {}

        ventes_wizi = {}
        ventes_etsy = {}
        if commandes_valides:
            source_map = {str(c["id_wizi"]): c["source"] for c in commandes_valides}
            ids_str = ",".join(str(c["id_wizi"]) for c in commandes_valides)
            lignes = select("lignes_commande",
                f"select=sku,sku_variation,quantite,id_commande&id_commande=in.({ids_str})",
                limit=50000)
            if lignes:
                for ligne in lignes:
                    sku_key = ligne.get("sku_variation") or ligne.get("sku")
                    if not sku_key:
                        continue
                    source = source_map.get(str(ligne.get("id_commande")), "wizishop")
                    qty = ligne.get("quantite") or 0
                    if source == "wizishop":
                        ventes_wizi[sku_key] = ventes_wizi.get(sku_key, 0) + qty
                    else:
                        ventes_etsy[sku_key] = ventes_etsy.get(sku_key, 0) + qty

        rows = []
        rows_inactifs = []
        for v in variations:
            sku = v.get("sku") or ""
            listing_id = v.get("listing_id")
            titre = (listing_map.get(listing_id, "") or "")[:60]
            variation_valeur = v.get("variation_valeur") or "—"
            is_enabled = v.get("is_enabled", False)
            stock_etsy = int(v.get("stock_etsy") or 0)
            stock_wizi = sku_stock_wizi.get(sku, 0)
            ecart = stock_wizi - stock_etsy

            total_ventes = ventes_wizi.get(sku, 0) + ventes_etsy.get(sku, 0)
            v_wizi = round(ventes_wizi.get(sku, 0) / nb_mois, 1)
            v_etsy = round(ventes_etsy.get(sku, 0) / nb_mois, 1)
            v_total = round(total_ventes / nb_mois, 1)
            ventes_par_jour = v_total / 30
            jours_stock = round(stock_wizi / ventes_par_jour) if ventes_par_jour > 0 else 999
            jours_stock_etsy = round(stock_etsy / ventes_par_jour) if ventes_par_jour > 0 else 999

            if not is_enabled:
                # Tableau séparé : inactifs avec stock Etsy supérieur au stock Wizishop
                if stock_etsy > stock_wizi:
                    rows_inactifs.append({
                        "SKU": sku,
                        "Produit": titre,
                        "Variation": variation_valeur,
                        "Stock Wizishop": stock_wizi,
                        "Stock Etsy": stock_etsy,
                        "Écart": ecart,
                        "Ventes/mois Total": v_total,
                    })
                continue

            # Tableau principal — listings actifs uniquement
            if stock_wizi == 0 and stock_etsy == 0:
                continue

            if stock_etsy > stock_wizi and jours_stock < seuil_jours:
                alerte = "🔴 URGENT"
                priorite = 1
            elif stock_etsy == 0 and jours_stock > seuil_jours:
                alerte = "🟡 ATTENTION"
                priorite = 2
            elif jours_stock < seuil_jours:
                alerte = "🟡 ATTENTION"
                priorite = 2
            else:
                alerte = "🟢 OK"
                priorite = 3

            rows.append({
                "SKU": sku,
                "Produit": titre,
                "Variation": variation_valeur,
                "Stock Wizishop": stock_wizi,
                "Stock Etsy": stock_etsy,
                "Écart": ecart,
                "Ventes/mois Wizi": v_wizi,
                "Ventes/mois Etsy": v_etsy,
                "Ventes/mois Total": v_total,
                "Jours stock Wizi": jours_stock if jours_stock < 999 else "—",
                "Alerte": alerte,
                "_priorite": priorite
            })

        df = pd.DataFrame(rows)

        nb_urgent = len(df[df["Alerte"] == "🔴 URGENT"])
        nb_attention = len(df[df["Alerte"] == "🟡 ATTENTION"])
        nb_ok = len(df[df["Alerte"] == "🟢 OK"])

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("🔴 Urgent", nb_urgent)
        with col2:
            st.metric("🟡 Attention", nb_attention)
        with col3:
            st.metric("🟢 OK", nb_ok)

        if alerte_filtre == "🔴 Urgent uniquement":
            df = df[df["Alerte"] == "🔴 URGENT"]
        elif alerte_filtre == "🟡 Attention uniquement":
            df = df[df["Alerte"] == "🟡 ATTENTION"]
        elif alerte_filtre == "🟢 OK uniquement":
            df = df[df["Alerte"] == "🟢 OK"]

        df = df.sort_values("_priorite").drop(columns=["_priorite"]).reset_index(drop=True)

        st.dataframe(df, use_container_width=True, hide_index=True)
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Télécharger en CSV", csv, "gestion_stock_etsy.csv", "text/csv")

        # ── Anomalies listings inactifs ───────────────────────────────────────
        if rows_inactifs:
            st.divider()
            st.subheader("⚠️ Anomalies stock (listings inactifs)")
            st.caption(f"{len(rows_inactifs)} listing(s) inactif(s) avec stock Etsy > stock Wizishop")
            df_inactifs = pd.DataFrame(rows_inactifs)
            df_inactifs = df_inactifs.sort_values("Écart")
            st.dataframe(df_inactifs, use_container_width=True, hide_index=True)
    else:
        st.info("Aucune donnée. Lance d'abord la sync 6️⃣ Produits Etsy.")

elif page == "🔎 Produits manquants sur Etsy":
    st.subheader("🔎 Produits manquants sur Etsy")

    with st.sidebar:
        st.divider()
        fournisseurs_fixes = ["Tous", "VEINIERE", "NPC", "NPGL", "NAVARRO", "BAVOUX", "DELORME"]
        fournisseur_filtre = st.selectbox("Fournisseur", fournisseurs_fixes)

    skus_data = select("skus", "select=sku,stock&statut=eq.visible")
    etsy_variations = select("produits_etsy_variations", "select=sku")
    produits_data = select("produits",
        "select=sku,nom,fournisseur,nom_categorie,prix_vente_ht,reference_fournisseur")

    if skus_data:
        skus_wizi = {s["sku"] for s in skus_data}
        skus_etsy = {v["sku"] for v in etsy_variations} if etsy_variations else set()
        prod_map = {p["sku"]: p for p in produits_data} if produits_data else {}
        sku_stock = {s["sku"]: int(s["stock"] or 0) for s in skus_data}

        rows = []
        for sku in skus_wizi - skus_etsy:
            prod = get_prod_parent(sku, prod_map)
            rows.append({
                "SKU": sku,
                "Produit": prod.get("nom") or "",
                "Fournisseur": prod.get("fournisseur") or "",
                "Catégorie": prod.get("nom_categorie") or "",
                "Prix vente HT": prod.get("prix_vente_ht") or 0,
                "Stock": sku_stock.get(sku, 0),
                "Réf. fournisseur": prod.get("reference_fournisseur") or ""
            })

        df_manquants = pd.DataFrame(rows) if rows else pd.DataFrame()

        total_manquants = len(df_manquants)

        if fournisseur_filtre != "Tous" and not df_manquants.empty:
            df_filtre = df_manquants[df_manquants["Fournisseur"] == fournisseur_filtre]
        else:
            df_filtre = df_manquants

        nb_filtre = len(df_filtre)

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total SKUs manquants sur Etsy", total_manquants)
        with col2:
            label = f"Manquants — {fournisseur_filtre}" if fournisseur_filtre != "Tous" else "Manquants (tous fournisseurs)"
            st.metric(label, nb_filtre)

        if not df_filtre.empty:
            df_show = df_filtre.sort_values("SKU").reset_index(drop=True)
            st.dataframe(df_show, use_container_width=True, hide_index=True)
            csv = df_show.to_csv(index=False).encode("utf-8")
            st.download_button("📥 Télécharger en CSV", csv, "produits_manquants_etsy.csv", "text/csv")
        else:
            st.info("Aucun SKU manquant pour ce filtre." if fournisseur_filtre != "Tous"
                    else "Tous les SKUs Wizishop sont présents sur Etsy.")
    else:
        st.info("Aucune donnée. Lance d'abord une synchronisation.")

elif page == "🌍 Comptabilité TVA":
    with st.sidebar:
        st.divider()
        annee = st.selectbox("Année", [2026, 2025, 2024, 2023], index=0)
        source_filtre = st.selectbox("Source", ["Toutes", "Wizishop", "Etsy"])

    query = f"select=zone_tva,pays_facturation,pays_facturation_iso,montant_ttc,montant_ht,source&statut_code=not.in.(0,45,46,50)&date_commande=gte.{annee}-01-01&date_commande=lt.{annee+1}-01-01"
    if source_filtre == "Wizishop":
        query += "&source=eq.wizishop"
    elif source_filtre == "Etsy":
        query += "&source=eq.etsy"

    commandes = select("commandes", query)

    if commandes:
        df = pd.DataFrame(commandes)
        df["montant_ttc"] = pd.to_numeric(df["montant_ttc"], errors="coerce").fillna(0)
        df["montant_ht"] = pd.to_numeric(df["montant_ht"], errors="coerce").fillna(0)

        zones = df.groupby("zone_tva").agg(
            nb_commandes=("montant_ttc", "count"),
            ca_ttc=("montant_ttc", "sum"),
            ca_ht=("montant_ht", "sum")
        ).reset_index()
        zones["zone_tva"] = zones["zone_tva"].map({
            "france": "🇫🇷 France",
            "ue": "🇪🇺 Union Européenne",
            "hors_ue": "🌍 Hors UE",
            "inconnu": "❓ Inconnu"
        }).fillna(zones["zone_tva"])
        zones.columns = ["Zone", "Nb commandes", "CA TTC (€)", "CA HT (€)"]
        st.subheader(f"Répartition CA par zone TVA — {annee}")
        st.dataframe(zones, use_container_width=True, hide_index=True)

        pays = df.groupby(["pays_facturation", "pays_facturation_iso", "zone_tva"]).agg(
            nb_commandes=("montant_ttc", "count"),
            ca_ttc=("montant_ttc", "sum")
        ).reset_index().sort_values("ca_ttc", ascending=False)
        pays.columns = ["Pays", "ISO", "Zone", "Nb commandes", "CA TTC (€)"]
        st.divider()
        st.subheader("Détail par pays")
        st.dataframe(pays, use_container_width=True, hide_index=True)
        csv = pays.to_csv(index=False).encode("utf-8")
        st.download_button("Télécharger en CSV", csv, f"tva_{annee}.csv", "text/csv")
    else:
        st.info("Aucune donnée. Lance d'abord une synchronisation.")

elif page == "🔍 Vérification Wizishop":
    st.subheader("🔍 Vérification des ventes Wizishop")

    tab1, tab2 = st.tabs(["📊 Ventes", "💰 Prix d'achat manquants"])

    with tab1:
        with st.sidebar:
            st.divider()
            nb_mois = st.slider("Période (mois)", min_value=1, max_value=24, value=12)

        date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")
        commandes_wizi = select("commandes",
            f"select=id_wizi&statut_code=not.in.(0,45,46,50)&source=eq.wizishop&date_commande=gte.{date_limite}")

        if commandes_wizi:
            ids = [str(c["id_wizi"]) for c in commandes_wizi]
            ids_str = ",".join(ids)

            lignes = select("lignes_commande",
                f"select=sku,sku_variation,nom_produit,quantite,prix_unitaire_ttc,id_commande&id_commande=in.({ids_str})",
                limit=50000)

            produits = select("produits", "select=sku,nom,nom_categorie")
            prod_map = {p["sku"]: p for p in produits} if produits else {}

            if lignes:
                df = pd.DataFrame(lignes)
                df["quantite"] = pd.to_numeric(df["quantite"], errors="coerce").fillna(0)
                df["prix_unitaire_ttc"] = pd.to_numeric(df["prix_unitaire_ttc"], errors="coerce").fillna(0)
                df["ca"] = df["quantite"] * df["prix_unitaire_ttc"]
                df["sku_effectif"] = df.apply(
                    lambda r: r["sku_variation"] if r["sku_variation"] else r["sku"], axis=1)
                df["nom_affiche"] = df["sku_effectif"].map(
                    lambda x: get_prod_parent(x, prod_map).get("nom", "") or "")
                df["nom_affiche"] = df.apply(
                    lambda r: r["nom_affiche"] if r["nom_affiche"] else r["nom_produit"], axis=1)
                df["categorie"] = df["sku_effectif"].map(
                    lambda x: get_prod_parent(x, prod_map).get("nom_categorie", "") or "")

                result = df.groupby(["sku_effectif", "nom_affiche", "categorie"]).agg(
                    unites_vendues=("quantite", "sum"),
                    ca_total=("ca", "sum"),
                    nb_commandes=("id_commande", "nunique")
                ).reset_index().sort_values("unites_vendues", ascending=False)

                result.columns = ["SKU", "Produit", "Catégorie",
                                 "Unités vendues", "CA (€)", "Nb commandes"]
                result["CA (€)"] = result["CA (€)"].apply(lambda x: f"{x:.2f}")

                st.info(f"{len(result)} produits vendus sur Wizishop sur les {nb_mois} derniers mois")
                st.dataframe(result, use_container_width=True, hide_index=True)
                csv = result.to_csv(index=False).encode("utf-8")
                st.download_button("Télécharger en CSV", csv, "verification_wizishop.csv", "text/csv")
            else:
                st.info("Aucune ligne de commande trouvée.")
        else:
            st.info("Aucune commande Wizishop trouvée.")

    with tab2:
        produits_sans_prix = select("produits",
            "select=sku,nom,nom_categorie,fournisseur,prix_vente_ht"
            "&or=(prix_achat_ht.is.null,prix_achat_ht.eq.0)"
            "&order=fournisseur.asc,nom.asc")

        if produits_sans_prix:
            skus_stock = select("skus", "select=sku,stock")
            stock_map = {s["sku"]: s.get("stock") or 0 for s in skus_stock} if skus_stock else {}

            # SKU parent = SKU le plus court partagé par un même produit Wizishop (id_wizi)
            tous_produits = select("produits", "select=sku,id_wizi,statut")
            prod_map = {p["sku"]: p for p in tous_produits} if tous_produits else {}
            parent_par_id_wizi = {}
            if tous_produits:
                for p in tous_produits:
                    iw, sku = p.get("id_wizi"), p.get("sku")
                    if iw is None or not sku:
                        continue
                    courant = parent_par_id_wizi.get(iw)
                    if courant is None or len(sku) < len(courant["sku"]):
                        parent_par_id_wizi[iw] = p
            prod_map_parents = {p["sku"]: p for p in parent_par_id_wizi.values()}

            def _statut_parent(sku):
                if sku in prod_map_parents:
                    return prod_map_parents[sku].get("statut") or ""
                # Fallback : préfixe le plus court trouvé parmi les SKUs parents
                for longueur in range(4, len(sku)):
                    prefixe = sku[:longueur]
                    if prefixe in prod_map_parents:
                        return prod_map_parents[prefixe].get("statut") or ""
                # Aucun parent trouvé : statut du SKU lui-même
                return (prod_map.get(sku) or {}).get("statut") or ""

            df_pa = pd.DataFrame(produits_sans_prix)
            df_pa["stock"] = df_pa["sku"].map(stock_map).fillna(0)
            df_pa = df_pa[df_pa["stock"] > 0]

            df_pa["statut"] = df_pa["sku"].apply(_statut_parent)

            df_pa["prix_vente_ht"] = pd.to_numeric(df_pa["prix_vente_ht"], errors="coerce").fillna(0)
            for col in ["nom", "nom_categorie", "fournisseur", "statut"]:
                df_pa[col] = df_pa[col].fillna("")
            df_pa = df_pa.rename(columns={
                "sku": "SKU",
                "nom": "Nom produit",
                "nom_categorie": "Catégorie",
                "fournisseur": "Fournisseur",
                "statut": "Statut",
                "prix_vente_ht": "Prix vente HT",
            })[["SKU", "Nom produit", "Catégorie", "Fournisseur", "Statut", "Prix vente HT"]]

            st.metric("SKUs sans prix d'achat (stock > 0)", len(df_pa))
            if not df_pa.empty:
                st.dataframe(df_pa, use_container_width=True, hide_index=True)
                csv = df_pa.to_csv(index=False).encode("utf-8")
                st.download_button("Télécharger en CSV", csv, "prix_achat_manquants.csv", "text/csv")
            else:
                st.success("✅ Tous les produits visibles avec du stock ont un prix d'achat renseigné.")
        else:
            st.success("✅ Tous les produits visibles ont un prix d'achat renseigné.")

elif page == "💎 Valorisation du stock":
    st.subheader("💎 Valorisation du stock")

    import plotly.graph_objects as go

    skus_data = select("skus", "select=sku,stock,statut")
    produits_data = select("produits", "select=sku,nom,prix_achat_ht,statut,nom_categorie,fournisseur")

    if not skus_data or not produits_data:
        st.info("Données indisponibles. Lance d'abord une synchronisation depuis le menu 🔄.")
    else:
        prod_map = {p["sku"]: p for p in produits_data}

        rows = []
        nb_sans_prix = 0
        for s in skus_data:
            stock = int(s.get("stock") or 0)
            if stock <= 0:
                continue
            prod = prod_map.get(s.get("sku"), {})
            prix_achat = prod.get("prix_achat_ht") or 0
            if not prix_achat:
                nb_sans_prix += 1
                continue
            prix_achat = float(prix_achat)
            rows.append({
                "SKU": s.get("sku"),
                "Nom produit": prod.get("nom") or "",
                "Catégorie": prod.get("nom_categorie") or "",
                "Fournisseur": prod.get("fournisseur") or "",
                "Statut": prod.get("statut") or "",
                "Stock": stock,
                "Prix achat HT": prix_achat,
                "Valorisation (€)": stock * prix_achat,
            })

        df = pd.DataFrame(rows)
        total_valo = df["Valorisation (€)"].sum() if not df.empty else 0.0
        valo_visible = df.loc[df["Statut"] == "visible", "Valorisation (€)"].sum() if not df.empty else 0.0
        valo_unavailable = df.loc[df["Statut"] == "unavailable", "Valorisation (€)"].sum() if not df.empty else 0.0

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Valorisation totale", f"{total_valo:,.0f} €".replace(",", " "))
        col2.metric("Valorisation \"visible\"", f"{valo_visible:,.0f} €".replace(",", " "))
        col3.metric("Valorisation \"unavailable\"", f"{valo_unavailable:,.0f} €".replace(",", " "))
        with col4:
            st.metric("SKUs sans prix d'achat (stock > 0)", nb_sans_prix)
            if st.button("➡️ Voir le détail", use_container_width=True):
                st.session_state["page"] = "🔍 Vérification Wizishop"
                st.session_state[_NAV_KEYS["🛍️ Wizishop"]] = "🔍 Vérification Wizishop"
                for k in _NAV_KEYS.values():
                    if k != _NAV_KEYS["🛍️ Wizishop"]:
                        st.session_state[k] = None
                st.rerun()

        if df.empty:
            st.info("Aucun SKU valorisable (stock > 0 et prix d'achat renseigné).")
        else:
            df_sorted = df.sort_values("Valorisation (€)", ascending=False).reset_index(drop=True)

            st.divider()
            st.subheader("📋 Détail par statut")

            ordre_statuts = ["visible", "unavailable", "hidden"]
            tous_statuts = ordre_statuts + sorted(set(df_sorted["Statut"]) - set(ordre_statuts))
            for statut_val in tous_statuts:
                df_grp = df_sorted[df_sorted["Statut"] == statut_val]
                if df_grp.empty:
                    continue
                label = statut_val if statut_val else "(non renseigné)"
                total_grp = df_grp["Valorisation (€)"].sum()
                st.markdown(f"**{label}** — {len(df_grp)} SKU(s) — "
                             f"Total : {total_grp:,.0f} €".replace(",", " "))
                st.dataframe(
                    df_grp,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Prix achat HT": st.column_config.NumberColumn(format="%.2f €"),
                        "Valorisation (€)": st.column_config.NumberColumn(format="%.2f €"),
                    },
                )

            csv = df_sorted.to_csv(index=False).encode("utf-8")
            st.download_button("Télécharger en CSV", csv, "valorisation_stock.csv", "text/csv")

            st.divider()
            st.subheader("📊 Top 10 catégories par valorisation")
            cat_valo = (df.groupby("Catégorie")["Valorisation (€)"].sum()
                          .sort_values(ascending=False).head(10))
            cat_valo = cat_valo.iloc[::-1]
            fig = go.Figure(go.Bar(
                x=cat_valo.values,
                y=cat_valo.index,
                orientation="h",
                marker_color="#4C78A8",
                text=[f"{v:,.0f} €".replace(",", " ") for v in cat_valo.values],
                textposition="outside",
            ))
            fig.update_layout(
                xaxis_title="Valorisation (€)",
                yaxis_title="",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(t=20, b=40, l=150, r=80),
                height=max(320, len(cat_valo) * 40 + 80),
            )
            st.plotly_chart(fig, use_container_width=True)

elif page == "🗂️ Catalogue par catégories":
    st.subheader("🗂️ Catalogue par catégories")

    produits_data = select("produits", "select=sku,nom,nom_categorie,statut,prix_vente_ht,prix_achat_ht")
    skus_data = select("skus", "select=sku,stock")

    if not produits_data:
        st.info("Données indisponibles. Lance d'abord une synchronisation depuis le menu 🔄.")
    else:
        stock_map = {s["sku"]: s.get("stock") or 0 for s in skus_data} if skus_data else {}

        categories = sorted({p["nom_categorie"] for p in produits_data if p.get("nom_categorie")})

        if not categories:
            st.info("Aucune catégorie trouvée dans les produits.")
        else:
            col_cat, col_statut = st.columns(2)
            with col_cat:
                categorie_choisie = st.selectbox("Catégorie", categories)
            with col_statut:
                statuts_options = {
                    "Tous": None,
                    "Affiché (visible)": "visible",
                    "Indisponible (unavailable)": "unavailable",
                    "Non affiché (hidden)": "hidden",
                }
                statut_label = st.selectbox("Statut", list(statuts_options.keys()))
                statut_filtre = statuts_options[statut_label]

            produits_filtres = [p for p in produits_data if p.get("nom_categorie") == categorie_choisie]
            if statut_filtre:
                produits_filtres = [p for p in produits_filtres if p.get("statut") == statut_filtre]

            rows = []
            for p in produits_filtres:
                stock = int(stock_map.get(p.get("sku")) or 0)
                prix_achat = float(p.get("prix_achat_ht") or 0)
                rows.append({
                    "SKU": p.get("sku"),
                    "Nom produit": p.get("nom") or "",
                    "Statut": p.get("statut") or "",
                    "Stock": stock,
                    "Prix vente HT": float(p.get("prix_vente_ht") or 0),
                    "Prix achat HT": prix_achat,
                    "Valorisation (€)": stock * prix_achat,
                })

            df = pd.DataFrame(rows)
            stock_total = int(df["Stock"].sum()) if not df.empty else 0
            valo_totale = df["Valorisation (€)"].sum() if not df.empty else 0.0

            col1, col2, col3 = st.columns(3)
            col1.metric("Nb produits", len(df))
            col2.metric("Stock total", stock_total)
            col3.metric("Valorisation totale", f"{valo_totale:,.2f} €".replace(",", " "))

            if df.empty:
                st.info("Aucun produit pour cette catégorie / ce statut.")
            else:
                display_df = df.sort_values("Nom produit").reset_index(drop=True)[
                    ["SKU", "Nom produit", "Statut", "Stock", "Prix vente HT", "Prix achat HT"]]

                st.dataframe(
                    display_df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Prix vente HT": st.column_config.NumberColumn(format="%.2f €"),
                        "Prix achat HT": st.column_config.NumberColumn(format="%.2f €"),
                    },
                )

                csv = display_df.to_csv(index=False).encode("utf-8")
                st.download_button("Télécharger en CSV", csv, "catalogue_par_categorie.csv", "text/csv")

elif page == "📈 Évolution CA annuelle":
    st.subheader("📈 Évolution CA HT Wizishop + Etsy — 2025 vs 2026")

    import plotly.graph_objects as go
    from datetime import date

    MOIS_LABELS = ["Jan", "Fév", "Mar", "Avr", "Mai", "Jun",
                   "Jul", "Aoû", "Sep", "Oct", "Nov", "Déc"]

    @st.cache_data(ttl=600)
    def _ca_annuel_multi():
        rows = select("commandes",
            "select=date_commande,montant_ht,source"
            "&source=in.(wizishop,etsy)"
            "&statut_code=not.in.(0,45,46,50)"
            "&date_commande=gte.2025-01-01"
            "&montant_ht=not.is.null")
        return rows or []

    rows = _ca_annuel_multi()

    wizi_2025 = [0.0] * 12
    wizi_2026 = [0.0] * 12
    etsy_2025 = [0.0] * 12
    etsy_2026 = [0.0] * 12

    for r in rows:
        d = r.get("date_commande", "")
        if not d:
            continue
        try:
            dt = pd.Timestamp(d)
        except Exception:
            continue
        mois_idx = dt.month - 1
        montant = float(r.get("montant_ht") or 0)
        source = r.get("source", "")
        if dt.year == 2025:
            if source == "wizishop":
                wizi_2025[mois_idx] += montant
            elif source == "etsy":
                etsy_2025[mois_idx] += montant
        elif dt.year == 2026:
            if source == "wizishop":
                wizi_2026[mois_idx] += montant
            elif source == "etsy":
                etsy_2026[mois_idx] += montant

    total_2025 = [w + e for w, e in zip(wizi_2025, etsy_2025)]
    total_2026 = [w + e for w, e in zip(wizi_2026, etsy_2026)]

    mois_courant = date.today().month
    total_2025_comp = sum(total_2025[:mois_courant])
    total_2026_comp = sum(total_2026[:mois_courant])

    if total_2025_comp > 0:
        evol_pct = (total_2026_comp - total_2025_comp) / total_2025_comp * 100
        evol_str = f"{evol_pct:+.1f}%"
        evol_color = "normal" if evol_pct >= 0 else "inverse"
    else:
        evol_str = "—"
        evol_color = "off"

    col1, col2, col3 = st.columns(3)
    col1.metric("CA HT total 2025", f"{sum(total_2025):,.0f} €".replace(",", " "))
    col2.metric("CA HT total 2026", f"{sum(total_2026):,.0f} €".replace(",", " "))
    col3.metric(
        f"Évolution (jan–{MOIS_LABELS[mois_courant - 1]})",
        evol_str,
        delta=evol_str if evol_str != "—" else None,
        delta_color=evol_color,
    )

    _hover_2025 = [[wizi_2025[i], etsy_2025[i], total_2025[i]] for i in range(12)]
    _hover_2026 = [[wizi_2026[i], etsy_2026[i], total_2026[i]] for i in range(12)]
    _ht = "<br>Wizishop : %{customdata[0]:,.0f} €<br>Etsy : %{customdata[1]:,.0f} €<br><b>Total : %{customdata[2]:,.0f} €</b><extra></extra>"

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Wizishop 2025",
        x=MOIS_LABELS,
        y=wizi_2025,
        marker_color="#4C78A8",
        offsetgroup="2025",
        customdata=_hover_2025,
        hovertemplate="<b>%{x} 2025</b>" + _ht,
    ))
    fig.add_trace(go.Bar(
        name="Etsy 2025",
        x=MOIS_LABELS,
        y=etsy_2025,
        marker_color="#72B7B2",
        offsetgroup="2025",
        base=wizi_2025,
        customdata=_hover_2025,
        hovertemplate="<b>%{x} 2025</b>" + _ht,
    ))
    fig.add_trace(go.Bar(
        name="Wizishop 2026",
        x=MOIS_LABELS,
        y=wizi_2026,
        marker_color="#F58518",
        offsetgroup="2026",
        customdata=_hover_2026,
        hovertemplate="<b>%{x} 2026</b>" + _ht,
    ))
    fig.add_trace(go.Bar(
        name="Etsy 2026",
        x=MOIS_LABELS,
        y=etsy_2026,
        marker_color="#FFBF79",
        offsetgroup="2026",
        base=wizi_2026,
        customdata=_hover_2026,
        hovertemplate="<b>%{x} 2026</b>" + _ht,
    ))
    fig.update_layout(
        barmode="stack",
        xaxis_title="Mois",
        yaxis_title="CA HT (€)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis=dict(tickformat=",.0f", ticksuffix=" €"),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=40, b=40),
        height=480,
    )
    st.plotly_chart(fig, use_container_width=True)

elif page == "📊 CA par catégories":
    st.subheader("📊 CA HT par catégorie — Wizishop + Etsy")

    import plotly.graph_objects as go

    with st.sidebar:
        st.divider()
        periodes_cat = {"3 mois": 3, "6 mois": 6, "12 mois": 12, "24 mois": 24}
        periode_label_cat = st.selectbox(
            "Période", list(periodes_cat.keys()), index=1, key="sel_periode_ca_categories")
        nb_mois_cat = periodes_cat[periode_label_cat]

    date_limite_cat = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois_cat)).strftime("%Y-%m-%dT%H:%M:%S")

    commandes_cat = select("commandes",
        f"select=id_wizi"
        f"&source=in.(wizishop,etsy)"
        f"&statut_code=not.in.(0,45,46,50)"
        f"&date_commande=gte.{date_limite_cat}")

    if not commandes_cat:
        st.info("Aucune commande trouvée sur la période.")
    else:
        ids = [str(c["id_wizi"]) for c in commandes_cat]
        all_lignes = []
        for i in range(0, len(ids), 500):
            batch = ids[i:i + 500]
            rows = select("lignes_commande",
                f"select=sku,sku_variation,nom_produit,quantite,prix_unitaire_ttc"
                f"&id_commande=in.({','.join(batch)})"
                f"&quantite=gt.0")
            if rows:
                all_lignes.extend(rows)

        if not all_lignes:
            st.info("Aucune ligne de commande trouvée sur la période.")
        else:
            produits_cat = select("produits", "select=sku,nom,nom_categorie,statut,id_wizi")
            prod_map_cat = {p["sku"]: p for p in produits_cat} if produits_cat else {}

            # SKU parent = SKU le plus court partagé par un même produit Wizishop (id_wizi)
            parent_par_id_wizi_cat = {}
            if produits_cat:
                for p in produits_cat:
                    iw, sku = p.get("id_wizi"), p.get("sku")
                    if iw is None or not sku:
                        continue
                    courant = parent_par_id_wizi_cat.get(iw)
                    if courant is None or len(sku) < len(courant["sku"]):
                        parent_par_id_wizi_cat[iw] = p
            prod_map_parents_cat = {p["sku"]: p for p in parent_par_id_wizi_cat.values()}

            def _get_categorie_cat(sku):
                cat = (prod_map_cat.get(sku) or {}).get("nom_categorie") or ""
                if not cat:
                    cat = get_prod_parent(sku, prod_map_parents_cat).get("nom_categorie") or ""
                return cat

            df = pd.DataFrame(all_lignes)
            df["quantite"] = pd.to_numeric(df["quantite"], errors="coerce").fillna(0)
            df["prix_unitaire_ttc"] = pd.to_numeric(df["prix_unitaire_ttc"], errors="coerce").fillna(0)
            df["ca_ht"] = df["prix_unitaire_ttc"] / 1.2 * df["quantite"]
            df["sku_variation"] = df["sku_variation"].fillna("")
            df["sku_effectif"] = df.apply(
                lambda r: r["sku_variation"] if r["sku_variation"] else r["sku"], axis=1)
            df["categorie"] = df["sku_effectif"].apply(_get_categorie_cat)
            df["nom_categorie_produits"] = df["sku_effectif"].map(
                lambda x: (prod_map_cat.get(x) or {}).get("nom_categorie"))
            df["categorie"] = df["categorie"].replace("", "Sans catégorie")

            result = df.groupby("categorie").agg(
                ca_ht=("ca_ht", "sum"),
                unites=("quantite", "sum"),
            ).reset_index().sort_values("ca_ht", ascending=False).reset_index(drop=True)

            total_ca = result["ca_ht"].sum()
            total_unites = result["unites"].sum()
            result["pct"] = (result["ca_ht"] / total_ca * 100) if total_ca else 0.0

            col1, col2, col3 = st.columns(3)
            col1.metric(f"CA HT total ({periode_label_cat})", f"{total_ca:,.0f} €".replace(",", " "))
            col2.metric("Nb catégories", len(result))
            top1 = result.iloc[0]
            col3.metric("Catégorie n°1", top1["categorie"],
                        delta=f"{top1['ca_ht']:,.0f} €".replace(",", " "))

            display_df = result.rename(columns={
                "categorie": "Catégorie",
                "ca_ht": "CA HT (€)",
                "pct": "% du total",
                "unites": "Unités vendues",
            })[["Catégorie", "CA HT (€)", "% du total", "Unités vendues"]]

            total_row = pd.DataFrame([{
                "Catégorie": "Total",
                "CA HT (€)": total_ca,
                "% du total": 100.0,
                "Unités vendues": total_unites,
            }])
            display_df_total = pd.concat([display_df, total_row], ignore_index=True)

            st.dataframe(
                display_df_total,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "CA HT (€)": st.column_config.NumberColumn(format="%.2f €"),
                    "% du total": st.column_config.NumberColumn(format="%.1f %%"),
                    "Unités vendues": st.column_config.NumberColumn(format="%.0f"),
                },
            )

            csv = display_df_total.to_csv(index=False).encode("utf-8")
            st.download_button("Télécharger en CSV", csv, "ca_par_categories.csv", "text/csv")

            st.divider()
            st.subheader("📋 Détail par produit")

            col_cat_detail, col_statut_detail = st.columns(2)
            with col_cat_detail:
                categories_liste = result["categorie"].tolist()
                categorie_detail = st.selectbox(
                    "Catégorie à détailler", categories_liste, key="sel_categorie_detail_ca")
            with col_statut_detail:
                statuts_options_detail = {
                    "Tous": None,
                    "Affiché (visible)": "visible",
                    "Indisponible (unavailable)": "unavailable",
                    "Non affiché (hidden)": "hidden",
                }
                statut_label_detail = st.selectbox(
                    "Statut", list(statuts_options_detail.keys()), key="sel_statut_detail_ca")
                statut_filtre_detail = statuts_options_detail[statut_label_detail]

            skus_data_cat = select("skus", "select=sku,stock")
            stock_map_cat = {s["sku"]: s.get("stock") or 0 for s in skus_data_cat} if skus_data_cat else {}

            if categorie_detail == "Sans catégorie":
                produits_categorie = [p for p in (produits_cat or []) if not p.get("nom_categorie")]
            else:
                produits_categorie = [p for p in (produits_cat or []) if p.get("nom_categorie") == categorie_detail]

            if statut_filtre_detail:
                produits_categorie = [p for p in produits_categorie if p.get("statut") == statut_filtre_detail]

            # Exclure les SKUs parents : seuls les SKUs présents dans la table skus
            # (variations vendables, ou SKU unique si pas de variation) sont conservés
            skus_set_cat = set(stock_map_cat.keys())
            produits_categorie = [p for p in produits_categorie if p.get("sku") in skus_set_cat]

            ventes_par_sku = df.groupby("sku_effectif").agg(
                unites_vendues=("quantite", "sum"),
                ca_ht_sku=("ca_ht", "sum"),
            )
            ventes_map_sku = ventes_par_sku.to_dict("index")

            rows_detail = []
            for p in produits_categorie:
                sku = p.get("sku")
                ventes = ventes_map_sku.get(sku, {})
                rows_detail.append({
                    "SKU": sku,
                    "Nom produit": p.get("nom") or "",
                    "Statut": p.get("statut") or "",
                    "Stock": int(stock_map_cat.get(sku) or 0),
                    "Unités vendues": ventes.get("unites_vendues", 0.0),
                    "CA HT (€)": ventes.get("ca_ht_sku", 0.0),
                })

            df_detail = pd.DataFrame(rows_detail)

            nb_skus_total = len(df_detail)
            nb_skus_vendus = int((df_detail["Unités vendues"] > 0).sum()) if not df_detail.empty else 0
            nb_skus_sans_vente = nb_skus_total - nb_skus_vendus

            col1d, col2d, col3d = st.columns(3)
            col1d.metric("Nb SKUs dans la catégorie", nb_skus_total)
            col2d.metric("Nb SKUs vendus", nb_skus_vendus)
            col3d.metric("Nb SKUs sans vente", nb_skus_sans_vente)

            if df_detail.empty:
                st.info("Aucun SKU visible/indisponible trouvé pour cette catégorie.")
            else:
                df_detail = df_detail.sort_values("Unités vendues", ascending=False).reset_index(drop=True)
                st.dataframe(
                    df_detail,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Unités vendues": st.column_config.NumberColumn(format="%.0f"),
                        "CA HT (€)": st.column_config.NumberColumn(format="%.2f €"),
                    },
                )

            with st.expander("🔍 Debug - Produits sans catégorie"):
                df_sans_cat = df[df["categorie"] == "Sans catégorie"][
                    ["sku", "sku_variation", "sku_effectif", "nom_produit", "nom_categorie_produits"]
                ].drop_duplicates().head(20)
                df_sans_cat.columns = ["sku (lignes_commande)", "sku_variation (lignes_commande)",
                                       "SKU effectif", "Nom produit (lignes_commande)", "nom_categorie (produits)"]
                if df_sans_cat.empty:
                    st.success("✅ Tous les produits vendus ont une catégorie.")
                else:
                    st.dataframe(df_sans_cat, use_container_width=True, hide_index=True)

            st.divider()
            fig = go.Figure(go.Pie(
                labels=result["categorie"],
                values=result["ca_ht"],
                hole=0.3,
            ))
            fig.update_layout(margin=dict(t=20, b=20, l=20, r=20), height=480)
            st.plotly_chart(fig, use_container_width=True)

elif page == "🐌 Produits peu vendus":
    st.subheader("🐌 Produits peu vendus")

    skus_data_pv = select("skus", "select=sku,stock,statut&statut=eq.visible")
    produits_data_pv = select("produits", "select=sku,nom,nom_categorie,statut,prix_achat_ht")

    if not skus_data_pv or not produits_data_pv:
        st.info("Données indisponibles. Lance d'abord une synchronisation depuis le menu 🔄.")
    else:
        prod_map_pv = {p["sku"]: p for p in produits_data_pv}

        with st.sidebar:
            st.divider()
            seuil_ventes_mois = st.number_input(
                "Seuil ventes/mois", min_value=0.0, value=0.5, step=0.1, key="seuil_pv")
            periodes_pv = {"3 mois": 3, "6 mois": 6, "12 mois": 12}
            periode_label_pv = st.selectbox(
                "Période de calcul", list(periodes_pv.keys()), index=1, key="periode_pv")
            nb_mois_pv = periodes_pv[periode_label_pv]
            categories_pv = ["Toutes"] + sorted(
                {p["nom_categorie"] for p in produits_data_pv if p.get("nom_categorie")})
            categorie_pv = st.selectbox("Catégorie", categories_pv, key="categorie_pv")
            statuts_options_pv = {
                "Tous": None,
                "Affiché (visible)": "visible",
                "Indisponible (unavailable)": "unavailable",
                "Non affiché (hidden)": "hidden",
            }
            statut_label_pv = st.selectbox(
                "Statut", list(statuts_options_pv.keys()), index=1, key="statut_pv")
            statut_filtre_pv = statuts_options_pv[statut_label_pv]

        date_limite_pv = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois_pv)).strftime("%Y-%m-%dT%H:%M:%S")

        commandes_pv = select("commandes",
            f"select=id_wizi"
            f"&source=in.(wizishop,etsy)"
            f"&statut_code=not.in.(0,45,46,50)"
            f"&date_commande=gte.{date_limite_pv}")

        ventes_map_pv = {}
        if commandes_pv:
            ids_pv = [str(c["id_wizi"]) for c in commandes_pv]
            all_lignes_pv = []
            for i in range(0, len(ids_pv), 500):
                batch = ids_pv[i:i + 500]
                rows = select("lignes_commande",
                    f"select=sku,sku_variation,quantite,prix_unitaire_ttc"
                    f"&id_commande=in.({','.join(batch)})"
                    f"&quantite=gt.0")
                if rows:
                    all_lignes_pv.extend(rows)

            if all_lignes_pv:
                df_ventes_pv = pd.DataFrame(all_lignes_pv)
                df_ventes_pv["quantite"] = pd.to_numeric(df_ventes_pv["quantite"], errors="coerce").fillna(0)
                df_ventes_pv["prix_unitaire_ttc"] = pd.to_numeric(df_ventes_pv["prix_unitaire_ttc"], errors="coerce").fillna(0)
                df_ventes_pv["ca_ht"] = df_ventes_pv["prix_unitaire_ttc"] / 1.2 * df_ventes_pv["quantite"]
                df_ventes_pv["sku_variation"] = df_ventes_pv["sku_variation"].fillna("")
                df_ventes_pv["sku_effectif"] = df_ventes_pv.apply(
                    lambda r: r["sku_variation"] if r["sku_variation"] else r["sku"], axis=1)

                ventes_map_pv = df_ventes_pv.groupby("sku_effectif").agg(
                    total_vendu=("quantite", "sum"),
                    ca_ht=("ca_ht", "sum"),
                ).to_dict("index")

        rows_pv = []
        for s in skus_data_pv:
            sku = s.get("sku")
            prod = prod_map_pv.get(sku, {})
            statut_prod = prod.get("statut") or ""

            if categorie_pv != "Toutes" and (prod.get("nom_categorie") or "") != categorie_pv:
                continue
            if statut_filtre_pv and statut_prod != statut_filtre_pv:
                continue

            ventes = ventes_map_pv.get(sku, {})
            total_vendu = float(ventes.get("total_vendu", 0.0))
            ca_ht = float(ventes.get("ca_ht", 0.0))
            ventes_par_mois = total_vendu / nb_mois_pv

            if ventes_par_mois >= seuil_ventes_mois:
                continue

            stock = int(s.get("stock") or 0)
            prix_achat = float(prod.get("prix_achat_ht") or 0)

            rows_pv.append({
                "SKU": sku,
                "Nom produit": prod.get("nom") or "",
                "Catégorie": prod.get("nom_categorie") or "",
                "Statut": statut_prod,
                "Stock": stock,
                "Ventes/mois": ventes_par_mois,
                "Total vendu (période)": total_vendu,
                "CA HT (€)": ca_ht,
                "_valorisation": stock * prix_achat,
            })

        df_pv = pd.DataFrame(rows_pv)

        nb_produits_pv = len(df_pv)
        stock_immobilise_pv = int(df_pv["Stock"].sum()) if not df_pv.empty else 0
        valo_immobilisee_pv = df_pv["_valorisation"].sum() if not df_pv.empty else 0.0

        col1, col2, col3 = st.columns(3)
        col1.metric("Nb produits concernés", nb_produits_pv)
        col2.metric("Stock immobilisé total", stock_immobilise_pv)
        col3.metric("Valorisation immobilisée", f"{valo_immobilisee_pv:,.2f} €".replace(",", " "))

        if df_pv.empty:
            st.info("Aucun produit sous le seuil pour ces filtres.")
        else:
            df_pv_sorted = df_pv.sort_values("Ventes/mois", ascending=True).reset_index(drop=True)
            display_df_pv = df_pv_sorted[["SKU", "Nom produit", "Catégorie", "Statut", "Stock",
                                           "Ventes/mois", "Total vendu (période)", "CA HT (€)"]]
            st.dataframe(
                display_df_pv,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Ventes/mois": st.column_config.NumberColumn(format="%.2f"),
                    "Total vendu (période)": st.column_config.NumberColumn(format="%.0f"),
                    "CA HT (€)": st.column_config.NumberColumn(format="%.2f €"),
                },
            )

elif page == "🎨 Meilleures variations":
    st.subheader("🎨 Meilleures variations — Wizishop + Etsy")

    import plotly.graph_objects as go
    from datetime import date as _date
    import re as _re

    with st.sidebar:
        st.divider()
        periodes_var = {"3 mois": 3, "6 mois": 6, "12 mois": 12, "Année complète": 0}
        periode_label_var = st.selectbox(
            "Période", list(periodes_var.keys()), index=2, key="sel_periode_variations")
        nb_mois_var = periodes_var[periode_label_var]
        tri_var = st.selectbox("Trier par", ["Ratio", "Unités vendues"], key="sel_tri_variations")

    if nb_mois_var == 0:
        date_limite_var = f"{_date.today().year}-01-01T00:00:00"
    else:
        date_limite_var = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois_var)).strftime("%Y-%m-%dT%H:%M:%S")

    @st.cache_data(ttl=300)
    def _load_cmds_var(dl):
        return select("commandes",
            f"select=id_wizi"
            f"&source=in.(wizishop,etsy)"
            f"&statut_code=not.in.(0,45,46,50)"
            f"&date_commande=gte.{dl}") or []

    @st.cache_data(ttl=300)
    def _load_lignes_var(ids_t):
        if not ids_t:
            return []
        all_lignes = []
        ids_list = list(ids_t)
        for i in range(0, len(ids_list), 500):
            batch = ids_list[i:i + 500]
            rows = select("lignes_commande",
                f"select=sku,sku_variation,libelle_variation,nom_produit,quantite,prix_unitaire_ttc,source"
                f"&id_commande=in.({','.join(batch)})"
                f"&quantite=gt.0")
            if rows:
                all_lignes.extend(rows)
        return all_lignes

    _FOURNISSEURS_VAR = "BAVOUX,DELORME,Navarro,NPC,VEINIERE"

    @st.cache_data(ttl=600)
    def _load_skus_fournisseurs():
        """Charge les SKUs parents + variants pour les fournisseurs autorisés."""
        produits_f = select("produits",
            f"select=sku,id_wizi&statut=eq.visible&fournisseur=in.({_FOURNISSEURS_VAR})") or []
        parent_skus = {p["sku"] for p in produits_f if p.get("sku")}
        variant_skus = set()
        if produits_f:
            prod_ids = ",".join(str(p["id_wizi"]) for p in produits_f if p.get("id_wizi"))
            if prod_ids:
                variants = select("skus",
                    f"select=sku&statut=eq.visible&id_produit_parent=in.({prod_ids})") or []
                variant_skus = {v["sku"] for v in variants if v.get("sku")}
        return parent_skus, variant_skus

    @st.cache_data(ttl=600)
    def _load_catalogue_var():
        return select("produits_etsy_variations",
            "select=sku,variation_valeur&sku=not.is.null&is_enabled=eq.true") or []

    import unicodedata as _ud

    def _strip_accents(s):
        return _ud.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")

    def _extract_variation(libelle, sku_var):
        """libelle_variation en priorité, fallback regex sur sku_variation."""
        if libelle:
            part = _strip_accents(str(libelle).split("/")[0].strip()).upper()
            if part and part != "—":
                return part
        if sku_var:
            m = _re.search(r'\d+([A-Z]{2,})$', _strip_accents(str(sku_var).strip()).upper())
            if m:
                return m.group(1)
        return None

    _valid_parent_skus, _valid_variant_skus = _load_skus_fournisseurs()
    _etsy_cat = _load_catalogue_var()
    # Catalogue : variation -> set de SKUs dispo, filtré par fournisseurs autorisés
    catalogue_dispo = {}
    for r in _etsy_cat:
        if _valid_variant_skus and r.get("sku") not in _valid_variant_skus:
            continue
        vn = _extract_variation(r.get("variation_valeur"), r.get("sku"))
        if vn:
            catalogue_dispo.setdefault(vn, set()).add(r["sku"])

    # --- DEBUG MER DU SUD ---
    with st.expander("🐛 Debug — MER DU SUD", expanded=True):
        st.write(f"**1. variant_skus (fournisseurs filtrés) :** {len(_valid_variant_skus)} SKUs")
        _mer_all = [
            r for r in _etsy_cat
            if "MER" in _strip_accents(str(r.get("variation_valeur") or "")).upper()
            or "SUD" in _strip_accents(str(r.get("variation_valeur") or "")).upper()
        ]
        st.write(f"**2. Lignes produits_etsy_variations avec 'MER' ou 'SUD' :** {len(_mer_all)}")
        for _r in _mer_all[:10]:
            st.write(f"  → SKU: `{_r.get('sku')}` | variation_valeur: `{_r.get('variation_valeur')}`")
        _mer_ok = [_r for _r in _mer_all if not _valid_variant_skus or _r.get("sku") in _valid_variant_skus]
        st.write(f"**3. Après filtre sku in variant_skus :** {len(_mer_ok)}")
        for _r in _mer_ok[:10]:
            _vn = _extract_variation(_r.get("variation_valeur"), _r.get("sku"))
            st.write(f"  → SKU: `{_r.get('sku')}` | variation_valeur: `{_r.get('variation_valeur')}` | extrait: `{_vn}`")
    # --- FIN DEBUG ---

    cmds = _load_cmds_var(date_limite_var)
    if not cmds:
        st.info("Aucune commande trouvée pour cette période.")
    else:
        ids_t = tuple(str(c["id_wizi"]) for c in cmds)
        lignes = _load_lignes_var(ids_t)

        if not lignes:
            st.info("Aucune ligne de commande trouvée.")
        else:
            agg = {}
            for l in lignes:
                # Filtre fournisseur
                if _valid_parent_skus or _valid_variant_skus:
                    src_l = l.get("source", "")
                    if src_l == "wizishop" and (l.get("sku") or "") not in _valid_parent_skus:
                        continue
                    if src_l == "etsy" and (l.get("sku") or "") not in _valid_variant_skus:
                        continue
                var = _extract_variation(l.get("libelle_variation"), l.get("sku_variation"))
                if not var:
                    continue
                qty = int(l.get("quantite") or 0)
                sku = l.get("sku_variation") or l.get("sku") or ""
                ca = float(l.get("prix_unitaire_ttc") or 0) * qty
                if var not in agg:
                    agg[var] = {"unites": 0, "skus": set(), "ca": 0.0}
                agg[var]["unites"] += qty
                if sku:
                    agg[var]["skus"].add(sku)
                agg[var]["ca"] += ca

            if not agg:
                st.info("Aucune variation identifiable dans les lignes de commande.")
            else:
                rows_var = []
                for v, d in agg.items():
                    nb_dispo = len(catalogue_dispo.get(v, set()))
                    ratio = round(d["unites"] / nb_dispo, 1) if nb_dispo > 0 else None
                    rows_var.append({
                        "Variation": v,
                        "Unités vendues": d["unites"],
                        "Nb produits vendus": len(d["skus"]),
                        "Nb produits dispo": nb_dispo if nb_dispo > 0 else "—",
                        "Ratio": ratio,
                        "CA TTC (€)": round(d["ca"], 2),
                    })

                sort_col = "Ratio" if tri_var == "Ratio" else "Unités vendues"
                df_var = pd.DataFrame(rows_var)
                df_var_sorted = df_var.copy()
                df_var_sorted["_sort"] = pd.to_numeric(df_var_sorted[sort_col], errors="coerce").fillna(-1)
                df_var = df_var_sorted.sort_values("_sort", ascending=False).drop(columns="_sort").reset_index(drop=True)

                top1 = df_var.iloc[0]
                col1, col2, col3 = st.columns(3)
                col1.metric("Variations distinctes", len(df_var))
                col2.metric("Variation n°1", top1["Variation"],
                            delta=f"{int(top1['Unités vendues'])} unités")
                top1_ratio = top1["Ratio"]
                col3.metric("Meilleur ratio", f"{top1_ratio}" if top1_ratio is not None else "—")

                chart_col = sort_col
                top20_chart = df_var.head(20).copy()
                top20_chart["_x"] = pd.to_numeric(top20_chart[chart_col], errors="coerce").fillna(0)
                top20 = top20_chart.sort_values("_x", ascending=True)
                fig_var = go.Figure(go.Bar(
                    x=top20["_x"],
                    y=top20["Variation"],
                    orientation="h",
                    marker_color="#4C78A8",
                    text=top20["_x"].round(1),
                    textposition="outside",
                    customdata=list(zip(top20["Unités vendues"], top20["Nb produits dispo"], top20["Ratio"])),
                    hovertemplate=(
                        "<b>%{y}</b><br>"
                        "Unités vendues : %{customdata[0]}<br>"
                        "Nb dispo catalogue : %{customdata[1]}<br>"
                        "Ratio : %{customdata[2]}<extra></extra>"
                    ),
                ))
                fig_var.update_layout(
                    xaxis_title=chart_col,
                    yaxis_title="",
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    margin=dict(t=20, b=40, l=150, r=80),
                    height=max(320, len(top20) * 30 + 80),
                )
                st.subheader("🔝 Top 20 variations")
                st.plotly_chart(fig_var, use_container_width=True)

                st.subheader("📋 Tableau complet")
                st.dataframe(
                    df_var[["Variation", "Unités vendues", "Nb produits vendus",
                             "Nb produits dispo", "Ratio", "CA TTC (€)"]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "CA TTC (€)": st.column_config.NumberColumn(format="%.2f €"),
                        "Unités vendues": st.column_config.NumberColumn(format="%d"),
                        "Nb produits vendus": st.column_config.NumberColumn(format="%d"),
                        "Ratio": st.column_config.NumberColumn(format="%.1f"),
                    },
                )
                st.download_button(
                    "📥 Exporter CSV",
                    df_var.to_csv(index=False).encode("utf-8"),
                    "meilleures_variations.csv",
                    "text/csv",
                    key="dl_variations",
                )

            # ── Tableau 2 : produits sans variation ───────────────────────────
            st.divider()
            st.subheader("📋 Produits sans variation à classifier (impact sur le tableau ci-dessus)")

            lignes_sans_var = [
                l for l in lignes
                if not l.get("libelle_variation") and not l.get("sku_variation")
                and (not _valid_parent_skus or (l.get("sku") or "") in _valid_parent_skus)
            ]

            if not lignes_sans_var:
                st.success("✅ Tous les produits ont une variation attribuée.")
            else:
                agg_sans = {}
                for l in lignes_sans_var:
                    sku = l.get("sku") or ""
                    nom = l.get("nom_produit") or sku
                    qty = int(l.get("quantite") or 0)
                    ca = float(l.get("prix_unitaire_ttc") or 0) * qty
                    if sku not in agg_sans:
                        agg_sans[sku] = {"nom": nom, "unites": 0, "ca": 0.0}
                    agg_sans[sku]["unites"] += qty
                    agg_sans[sku]["ca"] += ca

                rows_sans = sorted(agg_sans.items(), key=lambda x: -x[1]["unites"])

                h1, h2, h3, h4, h5, h6 = st.columns([2, 4, 1, 2, 2, 1])
                h1.markdown("**SKU**")
                h2.markdown("**Nom produit**")
                h3.markdown("**Unités**")
                h4.markdown("**CA TTC**")
                h5.markdown("**Variation à attribuer**")

                for sku, d in rows_sans:
                    with st.form(key=f"form_var_{sku}", border=False):
                        c1, c2, c3, c4, c5, c6 = st.columns([2, 4, 1, 2, 2, 1])
                        c1.write(sku)
                        c2.write(d["nom"])
                        c3.write(str(d["unites"]))
                        c4.write(f"{d['ca']:.2f} €")
                        variation_input = c5.text_input(
                            "Variation",
                            label_visibility="collapsed",
                            placeholder="ex: VISON, NOIR...",
                        )
                        submitted = c6.form_submit_button("💾")
                        if submitted:
                            variation = variation_input.strip().upper()
                            if variation:
                                ok = update(
                                    "lignes_commande",
                                    f"sku=eq.{sku}&libelle_variation=is.null&source=in.(wizishop,etsy)",
                                    {"libelle_variation": variation},
                                )
                                if ok:
                                    st.success(f"✓ {sku} → {variation}")
                                    st.cache_data.clear()
                                else:
                                    st.error(f"Erreur pour {sku}.")
                            else:
                                st.warning("Saisir une variation avant d'enregistrer.")

elif page == "🔍 Vérification Etsy":
    st.subheader("🔍 Vérification Etsy")

    tab1, tab2, tab3 = st.tabs([
        "⚠️ SKUs absents de Wizishop",
        "💰 Comparaison des prix",
        "⚡ Contrôle statuts",
    ])

    # ── Onglet 1 : SKUs Etsy absents de Wizishop ─────────────────────────────
    with tab1:
        etsy_vars = select("produits_etsy_variations",
            "select=sku,listing_id,variation_valeur&sku=not.is.null&is_enabled=eq.true")
        wizi_skus_raw = select("produits", "select=sku")

        if etsy_vars and wizi_skus_raw:
            wizi_skus_set = {r["sku"] for r in wizi_skus_raw if r.get("sku")}

            # Dédoublonner : une ligne par (sku, listing_id, variation_valeur)
            vus = set()
            rows_absents = []
            for r in etsy_vars:
                sku = r.get("sku") or ""
                if not sku or sku in wizi_skus_set:
                    continue
                key = (sku, r.get("listing_id"), r.get("variation_valeur"))
                if key not in vus:
                    vus.add(key)
                    rows_absents.append({
                        "SKU": sku,
                        "listing_id": r.get("listing_id"),
                        "variation_valeur": r.get("variation_valeur") or "—",
                    })

            if rows_absents:
                st.warning(f"{len(rows_absents)} SKU(s) présents sur Etsy mais introuvables dans Wizishop")
                df_absents = pd.DataFrame(rows_absents).sort_values("SKU")
                st.dataframe(df_absents, use_container_width=True, hide_index=True)
                csv_absents = df_absents.to_csv(index=False).encode("utf-8")
                st.download_button("📥 Exporter CSV", csv_absents, "skus_etsy_absents_wizishop.csv",
                                   "text/csv", key="verif_etsy_absents_csv")
            else:
                st.success("✅ Tous les SKUs Etsy sont présents dans Wizishop.")
        else:
            st.info("Données insuffisantes pour la vérification.")

    # ── Onglet 2 : Comparaison des prix Etsy vs Wizishop ─────────────────────
    with tab2:
        etsy_prix = select("produits_etsy_variations",
            "select=sku,prix&sku=not.is.null&prix=not.is.null&is_enabled=eq.true")
        wizi_prix = select("produits", "select=sku,nom,prix_vente_ht&prix_vente_ht=not.is.null")

        if etsy_prix and wizi_prix:
            # Garder le prix max par SKU Etsy (en cas de plusieurs variations)
            etsy_prix_map = {}
            for r in etsy_prix:
                sku = r.get("sku") or ""
                prix = float(r.get("prix") or 0)
                if sku and prix > 0:
                    etsy_prix_map[sku] = max(etsy_prix_map.get(sku, 0), prix)

            wizi_prix_map = {}   # sku → {"nom": ..., "ht": ..., "ttc": ...}
            for r in wizi_prix:
                sku = r.get("sku") or ""
                prix_ht = float(r.get("prix_vente_ht") or 0)
                if sku and prix_ht > 0:
                    wizi_prix_map[sku] = {
                        "nom": r.get("nom") or "",
                        "ht": prix_ht,
                        "ttc": round(prix_ht * 1.2, 2),
                    }

            rows_ecart = []
            for sku, prix_etsy in etsy_prix_map.items():
                wizi = wizi_prix_map.get(sku)
                if not wizi:
                    continue
                prix_wizi_ttc = wizi["ttc"]
                ecart_pct = (prix_etsy - prix_wizi_ttc) / prix_wizi_ttc * 100
                if abs(ecart_pct) > 2:
                    rows_ecart.append({
                        "SKU": sku,
                        "Produit": wizi["nom"],
                        "Prix Etsy (€)": round(prix_etsy, 2),
                        "Prix Wizishop TTC (€)": prix_wizi_ttc,
                        "Écart (%)": round(ecart_pct, 1),
                    })

            if rows_ecart:
                df_ecart = pd.DataFrame(rows_ecart).sort_values("Écart (%)", key=abs, ascending=False)
                st.warning(f"{len(df_ecart)} SKU(s) avec écart de prix > 2% entre Etsy et Wizishop")
                st.dataframe(df_ecart, use_container_width=True, hide_index=True)
                csv_ecart = df_ecart.to_csv(index=False).encode("utf-8")
                st.download_button("📥 Exporter CSV", csv_ecart, "ecarts_prix_etsy_wizishop.csv",
                                   "text/csv", key="verif_etsy_prix_csv")
            else:
                st.success("✅ Aucun écart de prix significatif (> 2%) entre Etsy et Wizishop.")
        else:
            st.info("Données insuffisantes pour la comparaison des prix.")

    # ── Onglet 3 : Listings Inactive avec stock Wizishop disponible ──────────
    with tab3:
        st.markdown("**Produits Inactive sur Etsy alors qu'ils ont du stock dans Wizishop**")

        listings_inactifs = select("produits_etsy",
            "select=listing_id,titre,statut&statut=eq.inactive")

        if not listings_inactifs:
            st.success("✅ Aucun listing Inactive sur Etsy.")
        else:
            listing_ids_str = ",".join(str(l["listing_id"]) for l in listings_inactifs)
            titre_par_listing = {l["listing_id"]: l.get("titre") or "" for l in listings_inactifs}

            etsy_vars_inactifs = select("produits_etsy_variations",
                f"select=listing_id,sku,stock_etsy&listing_id=in.({listing_ids_str})")
            skus_visibles = select("skus", "select=sku,stock&statut=eq.visible")
            produits_noms = select("produits", "select=sku,nom")

            stock_wizi_map = {s["sku"]: int(s.get("stock") or 0) for s in (skus_visibles or [])}
            nom_par_sku = {p["sku"]: p.get("nom") or "" for p in (produits_noms or []) if p.get("sku")}

            rows_a_reactiver = []
            for v in (etsy_vars_inactifs or []):
                sku = v.get("sku") or ""
                if not sku:
                    continue
                stock_wizi = stock_wizi_map.get(sku, 0)
                if stock_wizi <= 0:
                    continue
                listing_id = v.get("listing_id")
                rows_a_reactiver.append({
                    "SKU": sku,
                    "Nom produit": nom_par_sku.get(sku) or titre_par_listing.get(listing_id, "") or sku,
                    "Listing ID": listing_id,
                    "Stock Wizishop": stock_wizi,
                    "Stock Etsy": int(v.get("stock_etsy") or 0),
                    "Statut listing": "inactive",
                })

            nb_listings_concernes = len({r["Listing ID"] for r in rows_a_reactiver})
            st.metric("📦 Listings inactifs avec stock disponible", nb_listings_concernes)

            if rows_a_reactiver:
                st.warning(f"{len(rows_a_reactiver)} SKU(s) sur {nb_listings_concernes} listing(s) "
                           f"Inactive ont du stock Wizishop disponible — à réactiver sur Etsy")
                df_reactiver = pd.DataFrame(rows_a_reactiver).sort_values(
                    ["Listing ID", "SKU"]).reset_index(drop=True)
                st.dataframe(df_reactiver, use_container_width=True, hide_index=True)
                csv_reactiver = df_reactiver.to_csv(index=False).encode("utf-8")
                st.download_button("📥 Exporter CSV", csv_reactiver, "etsy_listings_a_reactiver.csv",
                                   "text/csv", key="verif_etsy_statuts_csv")
            else:
                st.success("✅ Aucun listing Inactive n'a de stock Wizishop disponible.")

elif page == "📒 Export comptable Etsy":
    st.subheader("📒 Export comptable Etsy")

    # ── Classification des lignes du grand-livre ──────────────────────────────
    # Mapping basé sur les ledger_type réels observés en base (reference_type
    # seul ne suffit pas : 'refund'/'shipping_label' n'existent jamais chez Etsy).
    # FRAIS_ADS est exclu de _TYPES_FRAIS_TOTAL pour rester visible séparément
    # dans les métriques (coût publicitaire isolé du reste des frais Etsy).
    _TYPES_FRAIS_TOTAL = ("FRAIS_LISTING", "FRAIS_TRANSACTION", "FRAIS_ETSY_DIVERS")
    _TYPES_FRAIS_COLONNE = _TYPES_FRAIS_TOTAL + ("FRAIS_ADS",)

    def _classify_ledger_entry(ledger_type):
        lt = ledger_type or ""
        if (lt.endswith("_refund")
                or lt in ("REFUND_GROSS", "REFUND_PROCESSING_FEE", "regulatory_operating_fee_refund")):
            return "REMBOURSEMENT"
        if lt == "sales_tax":
            return "TVA"
        if lt == "PAYMENT_GROSS":
            return "VENTE"
        if lt == "DISBURSE2":
            return "VIREMENT"  # versement bancaire Etsy → vendeur, exclu des métriques frais/CA
        if lt in ("transaction", "transaction_quantity", "shipping_transaction",
                  "regulatory_operating_fee", "PAYMENT_PROCESSING_FEE", "buyer_fee"):
            return "FRAIS_TRANSACTION"
        if lt in ("listing", "renew_sold", "renew_sold_auto", "renew_expired", "auto_renew_expired"):
            return "FRAIS_LISTING"
        if lt in ("prolist", "offsite_ads_fee"):
            return "FRAIS_ADS"
        if lt in ("billing_payment", "RECOUP") or lt.startswith("seller_onboarding_fee"):
            return "FRAIS_ETSY_DIVERS"
        return "FRAIS_ETSY_DIVERS"  # fallback pour tout ledger_type non répertorié

    def _resolve_receipt_id(reference_type, reference_id, payments_by_id):
        # Vérifié sur données réelles : reference_type='receipt' → reference_id
        # est déjà le receipt_id ; 'shop_payment'/'processing_fee' → reference_id
        # est un payment_id qu'il faut résoudre via etsy_payments.receipt_id.
        if reference_type == "receipt":
            return reference_id
        if reference_type in ("shop_payment", "processing_fee"):
            p = payments_by_id.get(reference_id)
            return p.get("receipt_id") if p else None
        return None

    with st.sidebar:
        st.divider()
        _aujourdhui = datetime.now().date()
        _premier_jour_mois = _aujourdhui.replace(day=1)
        _dernier_jour_mois_prec = _premier_jour_mois - timedelta(days=1)
        _premier_jour_mois_prec = _dernier_jour_mois_prec.replace(day=1)

        date_debut, date_fin = st.date_input(
            "Période",
            value=(_premier_jour_mois_prec, _dernier_jour_mois_prec),
            format="DD/MM/YYYY",
        )

    if date_debut > date_fin:
        st.error("La date de début doit précéder la date de fin.")
    else:
        ts_debut = date_debut.strftime("%Y-%m-%dT00:00:00Z")
        ts_fin   = (date_fin + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")

        ledger_entries = select("etsy_ledger_entries",
            f"select=ledger_entry_id,amount,currency,create_timestamp,ledger_type,"
            f"reference_type,reference_id,description"
            f"&create_timestamp=gte.{ts_debut}&create_timestamp=lt.{ts_fin}"
            f"&order=create_timestamp.asc")

        if not ledger_entries:
            st.info("Aucune entrée de grand-livre Etsy sur cette période. "
                    "Lance d'abord la sync 🔄 etsy-ledger.")
        else:
            payments = select("etsy_payments", "select=payment_id,receipt_id")
            payments_by_id = {p["payment_id"]: p for p in (payments or [])}

            commandes_etsy = select("commandes",
                "select=id_wizi,numero_commande,pays_facturation_iso&source=eq.etsy")
            commandes_by_receipt = {c["id_wizi"]: c for c in (commandes_etsy or [])}

            rows = []
            for entry in ledger_entries:
                ledger_type    = entry.get("ledger_type")
                reference_type = entry.get("reference_type")
                reference_id   = entry.get("reference_id")
                type_ = _classify_ledger_entry(ledger_type)
                amount_eur = (entry.get("amount") or 0) / 100

                montant_brut = amount_eur if type_ in ("VENTE", "REMBOURSEMENT") else 0.0
                frais_etsy   = amount_eur if type_ in _TYPES_FRAIS_COLONNE else 0.0
                tva          = amount_eur if type_ == "TVA" else 0.0
                montant_net  = amount_eur  # toujours le montant brut de la ligne : la somme de
                                            # cette colonne sur toutes les lignes = variation du
                                            # solde Etsy sur la période (vérifiable / auditable)

                receipt_id = _resolve_receipt_id(reference_type, reference_id, payments_by_id)
                cmd = commandes_by_receipt.get(receipt_id, {})

                try:
                    date_str = datetime.fromisoformat(
                        entry["create_timestamp"].replace("Z", "+00:00")).strftime("%d/%m/%Y")
                except Exception:
                    date_str = ""

                rows.append({
                    "Date":              date_str,
                    "Type":              type_,
                    "Référence":         reference_id,
                    "Description":       entry.get("description"),
                    "Montant brut (€)":  round(montant_brut, 2),
                    "Frais Etsy (€)":    round(frais_etsy, 2),
                    "TVA collectée (€)": round(tva, 2),
                    "Montant net (€)":   round(montant_net, 2),
                    "Devise":            entry.get("currency"),
                    "N° commande":       cmd.get("numero_commande", ""),
                    "Pays client":       cmd.get("pays_facturation_iso", ""),
                })

            df = pd.DataFrame(rows)

            ca_brut       = df.loc[df["Type"] == "VENTE", "Montant brut (€)"].sum()
            total_remb    = df.loc[df["Type"] == "REMBOURSEMENT", "Montant brut (€)"].sum()
            total_frais   = df.loc[df["Type"].isin(_TYPES_FRAIS_TOTAL), "Frais Etsy (€)"].sum()
            total_ads     = df.loc[df["Type"] == "FRAIS_ADS", "Frais Etsy (€)"].sum()
            total_tva     = df.loc[df["Type"] == "TVA", "TVA collectée (€)"].sum()
            net_reverse   = ca_brut + total_remb + total_frais + total_ads + total_tva

            col1, col2, col3, col4, col5 = st.columns(5)
            with col1:
                st.metric("CA brut période", f"{ca_brut:,.2f} €")
            with col2:
                st.metric("Total frais Etsy", f"{total_frais:,.2f} €")
            with col3:
                st.metric("Total frais publicité (Ads)", f"{total_ads:,.2f} €")
            with col4:
                st.metric("Total TVA collectée", f"{total_tva:,.2f} €")
            with col5:
                st.metric("Net reversé", f"{net_reverse:,.2f} €")

            nb_virement = len(df[df["Type"] == "VIREMENT"])
            if nb_virement:
                st.caption(f"ℹ️ {nb_virement} versement(s) bancaire(s) Etsy → vendeur sur la "
                           f"période (type VIREMENT) — affichés dans le tableau mais exclus des "
                           f"métriques ci-dessus (ils reflètent un calendrier de paiement, pas "
                           f"l'activité comptable de la période).")

            st.divider()
            st.dataframe(df, use_container_width=True, hide_index=True)

            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "📥 Exporter CSV", csv,
                f"export_comptable_etsy_{date_debut.strftime('%Y%m%d')}_{date_fin.strftime('%Y%m%d')}.csv",
                "text/csv",
            )

elif page == "🚀 Créer produits sur Faire":
    st.subheader("🚀 Créer produits sur Faire")

    if "ANTHROPIC_API_KEY" not in st.secrets:
        st.warning("⚠️ `ANTHROPIC_API_KEY` absente des secrets — l'étape 2 "
                   "(génération de la fiche) ne fonctionnera pas tant qu'elle "
                   "n'est pas configurée.")

    _FOURNISSEURS_CIBLES = ["BAVOUX", "DELORME", "Navarro", "NPC", "VEINIERE"]

    # ── Étape 1 : sélection du produit ────────────────────────────────────────
    skus_data    = select("skus", "select=sku,stock&statut=eq.visible")
    produits_data = select("produits",
        "select=sku,nom,fournisseur,nom_categorie,prix_vente_ht,image_url,id_wizi")

    produits_faire_publis = select("produits_faire",
        "select=id_faire&lifecycle_state=eq.PUBLISHED")
    ids_publis = {p["id_faire"] for p in (produits_faire_publis or [])}
    faire_variants_all = select("produits_faire_variants", "select=sku,id_produit_faire")
    if faire_variants_all and ids_publis:
        skus_faire = {v["sku"] for v in faire_variants_all
                      if v.get("sku") and v.get("id_produit_faire") in ids_publis}
    else:
        skus_faire = {v["sku"] for v in (faire_variants_all or []) if v.get("sku")}

    faire_ignores_data = select("faire_ignores", "select=sku")
    faire_ignores_set = {r["sku"] for r in (faire_ignores_data or [])}

    if not skus_data or not produits_data:
        st.info("Aucune donnée. Lance d'abord une synchronisation.")
    else:
        skus_wizi = {s["sku"] for s in skus_data}
        prod_map  = {p["sku"]: p for p in produits_data}
        sku_stock = {s["sku"]: int(s["stock"] or 0) for s in skus_data}

        candidats = []
        for sku in skus_wizi - skus_faire - faire_ignores_set:
            prod = get_prod_parent(sku, prod_map)
            fournisseur = prod.get("fournisseur") or ""
            if fournisseur not in _FOURNISSEURS_CIBLES:
                continue
            candidats.append({"sku": sku, "nom": prod.get("nom") or "", "fournisseur": fournisseur})

        if not candidats:
            st.info(f"Aucun SKU manquant sur Faire pour les fournisseurs "
                    f"{', '.join(_FOURNISSEURS_CIBLES)}.")
        else:
            candidats.sort(key=lambda r: r["sku"])
            noms_par_sku = {c["sku"]: c["nom"] for c in candidats}
            sku_sel = st.selectbox(
                f"SKU à créer sur Faire ({len(candidats)} disponible(s))",
                [c["sku"] for c in candidats],
                format_func=lambda s: f"{s} — {noms_par_sku.get(s, '')}",
                key="faire_create_sku_select",
            )

            # Changement de SKU → on repart de zéro sur les étapes suivantes
            if sku_sel != st.session_state.get("faire_create_sku_actif"):
                st.session_state["faire_create_sku_actif"] = sku_sel
                st.session_state.pop("faire_create_fiche", None)
                for k in ("faire_titre_edit", "faire_description_edit", "faire_short_desc_edit"):
                    st.session_state.pop(k, None)

            prod_sel    = get_prod_parent(sku_sel, prod_map)
            id_wizi_sel = prod_sel.get("id_wizi")
            nom_sel     = prod_sel.get("nom") or ""
            categorie_sel   = prod_sel.get("nom_categorie") or ""
            fournisseur_sel = prod_sel.get("fournisseur") or ""
            image_url_sel   = prod_sel.get("image_url") or ""

            col_info, col_img = st.columns([2, 1])
            with col_info:
                st.markdown(f"**{nom_sel}**")
                st.caption(f"Catégorie : {categorie_sel} — Fournisseur : {fournisseur_sel} "
                           f"— SKU : {sku_sel}")
                description_wizi = _get_wizishop_description(id_wizi_sel)
                with st.expander("Description Wizishop (source)"):
                    st.markdown(description_wizi or "_Aucune description_", unsafe_allow_html=True)
            with col_img:
                if image_url_sel:
                    st.image(image_url_sel, width=200)

            st.divider()

            # ── Étape 2 : génération de la fiche ──────────────────────────────
            if st.button("✨ Générer la fiche Faire", type="primary"):
                with st.spinner("Génération via Claude…"):
                    try:
                        fiche = _generer_fiche_faire(
                            nom_sel, categorie_sel, fournisseur_sel, description_wizi)
                        st.session_state["faire_create_fiche"] = fiche
                        for k in ("faire_titre_edit", "faire_description_edit", "faire_short_desc_edit"):
                            st.session_state.pop(k, None)
                    except Exception as e:
                        st.error(f"❌ Échec de la génération : {e}")

            fiche = st.session_state.get("faire_create_fiche")
            if fiche:
                st.divider()
                st.markdown("### Étape 3 — Validation et édition")

                titre_edit = st.text_input(
                    "Titre", value=fiche.get("titre", ""), key="faire_titre_edit")
                st.caption(f"{len(titre_edit)} caractère(s)")

                description_edit = st.text_area(
                    "Description", value=fiche.get("description", ""),
                    height=300, key="faire_description_edit")
                st.caption(f"{len(description_edit)} caractère(s)")

                short_desc_edit = st.text_input(
                    "Short description", value=fiche.get("short_description", ""),
                    key="faire_short_desc_edit")

                # Variations du produit : tous les SKUs partageant le même id_wizi,
                # à l'exclusion de la ligne parent (prix=0, simple regroupement)
                # quand de vraies variations existent.
                produits_groupe = select("produits",
                    f"select=sku,nom,prix_vente_ht,image_url&id_wizi=eq.{id_wizi_sel}") or []
                vendables = [p for p in produits_groupe if (p.get("prix_vente_ht") or 0) > 0]
                if not vendables:
                    vendables = produits_groupe

                # Le coloris = partie du nom qui diverge entre variations (préfixe
                # commun du groupe retiré). Sur un produit à variation unique, le
                # préfixe commun couvrirait tout le nom : on garde alors le nom entier.
                noms_groupe = [p.get("nom") or "" for p in vendables]
                prefixe_commun = os.path.commonprefix(noms_groupe) if len(noms_groupe) > 1 else ""

                variations_rows = []
                for p in vendables:
                    nom_variation = p.get("nom") or ""
                    valeur = nom_variation
                    if prefixe_commun:
                        reste = nom_variation[len(prefixe_commun):].lstrip(" -")
                        if reste:
                            valeur = reste
                    prix_vente_ht = float(p.get("prix_vente_ht") or 0)
                    variations_rows.append({
                        "sku":              p["sku"],
                        "coloris":          valeur,
                        "stock":            sku_stock.get(p["sku"], 0),
                        "prix_vente_ht":    prix_vente_ht,
                        "prix_grossiste":   round(prix_vente_ht / 2.5, 2),
                        "prix_retail":      prix_vente_ht,
                        "image_url":        p.get("image_url") or image_url_sel,
                    })

                st.markdown("**Variations**")
                st.dataframe(
                    pd.DataFrame(variations_rows)[
                        ["sku", "coloris", "stock", "prix_grossiste", "prix_retail"]],
                    use_container_width=True, hide_index=True)

                st.divider()
                st.markdown("### Étape 4 — Création sur Faire")

                if st.button("🚀 Créer sur Faire en DRAFT", type="primary",
                             disabled=not variations_rows):
                    coloris_liste = sorted({v["coloris"] for v in variations_rows})
                    body = {
                        "name":                    titre_edit,
                        "description":             description_edit,
                        "short_description":       short_desc_edit,
                        "lifecycle_state":         "DRAFT",
                        "made_in_country":         "FR",
                        "unit_multiplier":         1,
                        "minimum_order_quantity":  1,
                        "images":                  [{"url": image_url_sel, "sequence": 0}] if image_url_sel else [],
                        "variant_option_sets":     [{"name": "Couleur", "values": coloris_liste}],
                        "variants": [{
                            "name":              v["coloris"],
                            "sku":               v["sku"],
                            "available_quantity": v["stock"],
                            "prices": [{
                                "currency":              "EUR",
                                "wholesale_price_cents":  round(v["prix_grossiste"] * 100),
                                "retail_price_cents":     round(v["prix_retail"] * 100),
                            }],
                            "options": [{"name": "Couleur", "value": v["coloris"]}],
                            "images": [{"url": v["image_url"], "sequence": 0}] if v["image_url"] else [],
                        } for v in variations_rows],
                    }

                    with st.spinner("Création du produit sur Faire…"):
                        try:
                            r = faire_create_product(body)
                        except Exception as e:
                            st.error(f"❌ Erreur réseau : {e}")
                            r = None

                    if r is not None:
                        if r.status_code in (200, 201):
                            data = r.json()
                            product_id = data.get("id") or data.get("product_id") or ""
                            st.success(f"✅ Produit créé en DRAFT sur Faire"
                                       f"{f' (id : {product_id})' if product_id else ''} !")
                            st.json(data)
                        else:
                            st.error(f"❌ Échec création (HTTP {r.status_code}) : {r.text[:500]}")

elif page == "🔎 Produits manquants sur Faire":
    st.subheader("🔎 Produits manquants sur Faire")

    with st.sidebar:
        st.divider()
        fournisseurs_fixes = ["Tous", "VEINIERE", "NPC", "NPGL", "NAVARRO", "BAVOUX", "DELORME"]
        fournisseur_filtre = st.selectbox("Fournisseur", fournisseurs_fixes)

    skus_data    = select("skus", "select=sku,stock&statut=eq.visible")
    produits_data = select("produits",
        "select=sku,nom,fournisseur,nom_categorie,prix_vente_ht,reference_fournisseur")

    # Variants Faire — uniquement ceux dont le produit parent est PUBLIÉ
    produits_faire_publis = select("produits_faire",
        "select=id_faire&lifecycle_state=eq.PUBLISHED")
    ids_publis = {p["id_faire"] for p in (produits_faire_publis or [])}

    faire_variants = select("produits_faire_variants",
        "select=sku,id_produit_faire")
    if faire_variants and ids_publis:
        skus_faire = {
            v["sku"] for v in faire_variants
            if v.get("sku") and v.get("id_produit_faire") in ids_publis
        }
    else:
        skus_faire = {v["sku"] for v in (faire_variants or []) if v.get("sku")}

    # SKUs à ignorer
    faire_ignores_data = select("faire_ignores",
        "select=sku,raison,created_at&order=created_at.desc")
    faire_ignores_set = {r["sku"] for r in faire_ignores_data} if faire_ignores_data else set()

    if skus_data:
        skus_wizi = {s["sku"] for s in skus_data}
        prod_map  = {p["sku"]: p for p in produits_data} if produits_data else {}
        sku_stock = {s["sku"]: int(s["stock"] or 0) for s in skus_data}

        rows = []
        for sku in skus_wizi - skus_faire - faire_ignores_set:
            prod = get_prod_parent(sku, prod_map)
            rows.append({
                "SKU":              sku,
                "Produit":          prod.get("nom") or "",
                "Fournisseur":      prod.get("fournisseur") or "",
                "Catégorie":        prod.get("nom_categorie") or "",
                "Prix vente HT":    prod.get("prix_vente_ht") or 0,
                "Stock":            sku_stock.get(sku, 0),
                "Réf. fournisseur": prod.get("reference_fournisseur") or "",
            })

        df_manquants = pd.DataFrame(rows) if rows else pd.DataFrame()

        if fournisseur_filtre != "Tous" and not df_manquants.empty:
            df_filtre = df_manquants[df_manquants["Fournisseur"] == fournisseur_filtre]
        else:
            df_filtre = df_manquants.copy()

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total SKUs manquants", len(df_manquants))
        with col2:
            label = f"Manquants — {fournisseur_filtre}" if fournisseur_filtre != "Tous" else "Affichés"
            st.metric(label, len(df_filtre))
        with col3:
            st.metric("🚫 Ignorés", len(faire_ignores_set))

        if not df_filtre.empty:
            df_editor = df_filtre.sort_values("SKU").reset_index(drop=True).copy()
            df_editor["Ignorer ?"] = False

            csv = df_filtre.to_csv(index=False).encode("utf-8")
            st.download_button("📥 Exporter CSV", csv,
                "produits_manquants_faire.csv", "text/csv",
                key="dl_faire_manquants")

            with st.form("form_faire_ignores"):
                edited = st.data_editor(
                    df_editor,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "SKU":              st.column_config.TextColumn(disabled=True),
                        "Produit":          st.column_config.TextColumn(disabled=True),
                        "Fournisseur":      st.column_config.TextColumn(disabled=True),
                        "Catégorie":        st.column_config.TextColumn(disabled=True),
                        "Prix vente HT":    st.column_config.NumberColumn(disabled=True),
                        "Stock":            st.column_config.NumberColumn(disabled=True),
                        "Réf. fournisseur": st.column_config.TextColumn(disabled=True),
                        "Ignorer ?":        st.column_config.CheckboxColumn(),
                    }
                )
                submitted_ign = st.form_submit_button(
                    "🚫 Ignorer les produits sélectionnés", type="secondary")

            if submitted_ign:
                a_ignorer = edited[edited["Ignorer ?"] == True]
                if a_ignorer.empty:
                    st.warning("Aucun produit sélectionné.")
                else:
                    nb_ign = 0
                    for _, r in a_ignorer.iterrows():
                        upsert("faire_ignores", [{
                            "sku": r["SKU"],
                            "raison": None,
                        }], "sku")
                        nb_ign += 1
                    st.success(f"✓ {nb_ign} produit(s) ignoré(s) !")
                    st.rerun()
        else:
            st.info("Aucun SKU manquant pour ce filtre." if fournisseur_filtre != "Tous"
                    else "Tous les SKUs Wizishop sont présents sur Faire (publiés).")

        # ── Produits exclus ───────────────────────────────────────────────────
        st.divider()
        st.subheader("🚫 Produits exclus de Faire")

        if faire_ignores_data:
            df_ign = pd.DataFrame(faire_ignores_data)
            df_ign["created_at"] = pd.to_datetime(
                df_ign["created_at"]).dt.strftime("%d/%m/%Y")
            df_ign_show = df_ign[["sku", "raison", "created_at"]].copy()
            df_ign_show.columns = ["SKU", "Raison", "Date"]
            st.dataframe(df_ign_show, use_container_width=True, hide_index=True)

            col_ret1, col_ret2 = st.columns([3, 1])
            with col_ret1:
                sku_retirer = st.selectbox(
                    "Sélectionner un SKU à réintégrer",
                    options=[""] + df_ign["sku"].tolist(),
                    format_func=lambda x: x if x else "Choisir un SKU…",
                    key="faire_ignores_retirer"
                )
            with col_ret2:
                st.write("")
                st.write("")
                if sku_retirer and st.button("↩️ Réintégrer", type="secondary"):
                    delete("faire_ignores", f"sku=eq.{sku_retirer}")
                    st.success(f"✓ {sku_retirer} réintégré !")
                    st.rerun()
        else:
            st.info("Aucun produit exclu.")
    else:
        st.info("Aucune donnée. Lance d'abord une synchronisation.")

elif page == "🔍 Vérification Faire":
    st.subheader("🔍 Vérification Faire")

    tab1, tab2, tab3, tab4 = st.tabs([
        "✏️ Correction SKUs",
        "🔗 Mapping SKUs",
        "🔧 Correction lignes commandes",
        "💰 Vérification prix",
    ])

    # ── Onglet 1 : Correction SKUs ────────────────────────────────────────────
    with tab1:
        faire_variants = select("produits_faire_variants",
            "select=id_faire,id_produit_faire,sku,nom")
        skus_data = select("skus", "select=sku&statut=eq.visible")
        produits_faire_data = select("produits_faire", "select=id_faire,nom")
        produits_faire_map = {p["id_faire"]: p["nom"] for p in produits_faire_data} if produits_faire_data else {}
        skus_valides = {s["sku"] for s in skus_data} if skus_data else set()

        if not faire_variants:
            st.info("Aucun variant Faire trouvé. Lance d'abord la sync 8️⃣ Produits Faire.")
        else:
            incorrects = [
                v for v in faire_variants
                if not v.get("sku") or v.get("sku") not in skus_valides
            ]
            col1, col2 = st.columns(2)
            with col1:
                st.metric("SKUs incorrects sur Faire", len(incorrects))
            with col2:
                st.metric("SKUs vides (NULL ou vide)", len([v for v in incorrects if not v.get("sku")]))

            if not incorrects:
                st.success("✅ Tous les SKUs Faire correspondent à des SKUs Wizishop valides !")
            else:
                df_edit = pd.DataFrame([{
                    "ID Faire": v.get("id_faire", ""),
                    "ID Produit Faire": v.get("id_produit_faire", ""),
                    "Produit parent": produits_faire_map.get(v.get("id_produit_faire", ""), v.get("nom", "")),
                    "Nom variant": v.get("nom", ""),
                    "SKU actuel": v.get("sku", ""),
                    "Nouveau SKU": "",
                } for v in incorrects])

                df_result = st.data_editor(
                    df_edit,
                    column_config={
                        "ID Faire": st.column_config.TextColumn("ID Faire", disabled=True),
                        "ID Produit Faire": st.column_config.TextColumn("ID Produit Faire", disabled=True),
                        "Produit parent": st.column_config.TextColumn("Produit parent", disabled=True),
                        "Nom variant": st.column_config.TextColumn("Nom variant", disabled=True),
                        "SKU actuel": st.column_config.TextColumn("SKU actuel", disabled=True),
                        "Nouveau SKU": st.column_config.TextColumn("Nouveau SKU"),
                    },
                    hide_index=True, use_container_width=True, key="editor_sku_faire"
                )

                lignes_a_corriger = df_result[
                    df_result["Nouveau SKU"].notna() & (df_result["Nouveau SKU"].str.strip() != "")
                ]
                st.caption(f"{len(lignes_a_corriger)} correction(s) à appliquer "
                           f"sur {lignes_a_corriger['ID Produit Faire'].nunique()} produit(s).")

                if st.button("Mettre à jour sur Faire", type="primary",
                             disabled=len(lignes_a_corriger) == 0, key="btn_corriger_skus"):
                    progress = st.progress(0)
                    groupes = lignes_a_corriger.groupby("ID Produit Faire")
                    nb_produits = len(groupes)
                    succes = 0
                    erreurs = 0
                    for i, (product_id, groupe) in enumerate(groupes):
                        variants_payload = [
                            {"id": row["ID Faire"], "sku": row["Nouveau SKU"].strip()}
                            for _, row in groupe.iterrows()
                        ]
                        try:
                            r = faire_api_patch(f"/products/{product_id}", {"variants": variants_payload})
                            if r.status_code == 200:
                                succes += len(variants_payload)
                            else:
                                erreurs += len(variants_payload)
                                skus_concernes = ", ".join(
                                    row["SKU actuel"] or "(vide)" for _, row in groupe.iterrows())
                                st.error(f"❌ Produit {product_id} ({skus_concernes}) : "
                                         f"{r.status_code} — {r.text[:200]}")
                        except Exception as e:
                            erreurs += len(variants_payload)
                            st.error(f"❌ Produit {product_id} : {e}")
                        progress.progress((i + 1) / nb_produits)
                    if succes > 0:
                        st.success(f"✅ {succes} variant(s) corrigé(s).")
                        st.info("💡 Relance la **8️⃣ Sync Produits Faire** pour mettre à jour la base.")
                    if erreurs > 0:
                        st.warning(f"⚠️ {erreurs} variant(s) en erreur.")

    # ── Onglet 2 : Mapping SKUs ───────────────────────────────────────────────
    with tab2:
        st.caption("Associe les anciens SKUs Faire aux SKUs Wizishop corrects pour la réconciliation.")

        commandes_faire_map = select("commandes",
            "select=id_faire&source=eq.faire&statut_code=not.in.(0,45,46,50)"
            "&date_commande=gte.2026-01-01")
        skus_data_map = select("skus", "select=sku&statut=eq.visible")
        mapping_existant = select("sku_mapping_faire", "select=id,sku_faire,sku_wizishop&order=sku_faire.asc")
        faire_variants_data = select("produits_faire_variants", "select=sku,nom,id_produit_faire")
        produits_faire_data2 = select("produits_faire", "select=id_faire,nom")

        variant_map = {v["sku"]: v for v in faire_variants_data if v.get("sku")} if faire_variants_data else {}
        produits_faire_map2 = {p["id_faire"]: p["nom"] for p in produits_faire_data2} if produits_faire_data2 else {}
        skus_valides_map = {s["sku"] for s in skus_data_map} if skus_data_map else set()
        mapping_connu = {m["sku_faire"] for m in mapping_existant} if mapping_existant else set()

        skus_inconnus = {}
        if commandes_faire_map:
            ids_str_map = ",".join(str(c["id_faire"]) for c in commandes_faire_map if c.get("id_faire"))
            lignes_map = select("lignes_commande", f"select=sku&id_commande=in.({ids_str_map})", limit=50000)
            if lignes_map:
                for ligne in lignes_map:
                    sku = ligne.get("sku") or ""
                    if sku and sku not in skus_valides_map:
                        skus_inconnus[sku] = skus_inconnus.get(sku, 0) + 1

        skus_a_mapper = {sku: nb for sku, nb in skus_inconnus.items() if sku not in mapping_connu}

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("SKUs inconnus depuis 2026", len(skus_inconnus))
        with col2:
            st.metric("Déjà mappés", len(mapping_connu))
        with col3:
            st.metric("Restant à mapper", len(skus_a_mapper))

        if skus_a_mapper:
            st.divider()
            st.subheader("SKUs à mapper")
            df_edit_map = pd.DataFrame([{
                "SKU Faire": sku,
                "Nb commandes": nb,
                "Nom produit": produits_faire_map2.get(
                    (variant_map.get(sku) or {}).get("id_produit_faire", ""), ""),
                "Nom variant": (variant_map.get(sku) or {}).get("nom", ""),
                "Nouveau SKU Wizishop": "",
            } for sku, nb in sorted(skus_a_mapper.items(), key=lambda x: -x[1])])

            df_result_map = st.data_editor(
                df_edit_map,
                column_config={
                    "SKU Faire": st.column_config.TextColumn("SKU Faire", disabled=True),
                    "Nb commandes": st.column_config.NumberColumn("Nb commandes", disabled=True),
                    "Nom produit": st.column_config.TextColumn("Nom produit", disabled=True),
                    "Nom variant": st.column_config.TextColumn("Nom variant", disabled=True),
                    "Nouveau SKU Wizishop": st.column_config.TextColumn("Nouveau SKU Wizishop"),
                },
                hide_index=True, use_container_width=True, key="editor_mapping_faire"
            )

            lignes_remplies = df_result_map[
                df_result_map["Nouveau SKU Wizishop"].notna() &
                (df_result_map["Nouveau SKU Wizishop"].str.strip() != "")
            ]
            st.caption(f"{len(lignes_remplies)} mapping(s) à enregistrer.")

            if st.button("Enregistrer le mapping", type="primary",
                         disabled=len(lignes_remplies) == 0, key="btn_enreg_mapping"):
                payload = [
                    {"sku_faire": row["SKU Faire"], "sku_wizishop": row["Nouveau SKU Wizishop"].strip()}
                    for _, row in lignes_remplies.iterrows()
                ]
                if upsert("sku_mapping_faire", payload, "sku_faire"):
                    st.success(f"✅ {len(payload)} mapping(s) enregistré(s).")
                    st.rerun()
                else:
                    st.error("Erreur lors de l'enregistrement.")
        else:
            st.success("✅ Tous les SKUs inconnus depuis 2026 sont déjà mappés.")

        if mapping_existant:
            st.divider()
            st.subheader("Mappings enregistrés")
            df_mapping = pd.DataFrame(mapping_existant)
            df_mapping_edit = st.data_editor(
                df_mapping[["sku_faire", "sku_wizishop"]].rename(columns={
                    "sku_faire": "SKU Faire", "sku_wizishop": "SKU Wizishop"}),
                column_config={
                    "SKU Faire": st.column_config.TextColumn("SKU Faire", disabled=True),
                    "SKU Wizishop": st.column_config.TextColumn("SKU Wizishop"),
                },
                hide_index=True, use_container_width=True, key="editor_mapping_existant"
            )
            if st.button("Mettre à jour les mappings", type="secondary", key="btn_maj_mapping"):
                payload = [
                    {"sku_faire": row["SKU Faire"], "sku_wizishop": row["SKU Wizishop"].strip()}
                    for _, row in df_mapping_edit.iterrows()
                    if row["SKU Wizishop"].strip()
                ]
                if upsert("sku_mapping_faire", payload, "sku_faire"):
                    st.success(f"✅ {len(payload)} mapping(s) mis à jour.")
                    st.rerun()
                else:
                    st.error("Erreur lors de la mise à jour.")

    # ── Onglet 3 : Correction lignes commandes ────────────────────────────────
    with tab3:
        commandes_faire_corr = select("commandes",
            "select=id_faire,date_commande,nom_facturation,montant_ttc"
            "&source=eq.faire&statut_code=not.in.(0,45,46,50)"
            "&date_commande=gte.2026-01-01&order=date_commande.desc")
        skus_data_corr = select("skus", "select=sku&statut=eq.visible")
        skus_valides_corr = {s["sku"] for s in skus_data_corr} if skus_data_corr else set()

        if not commandes_faire_corr:
            st.info("Aucune commande Faire depuis le 1er janvier 2026.")
        else:
            ids_corr = [str(c["id_faire"]) for c in commandes_faire_corr if c.get("id_faire")]
            ids_str_corr = ",".join(ids_corr)
            all_lignes_corr = select("lignes_commande",
                f"select=id_commande,sku&id_commande=in.({ids_str_corr})", limit=50000)

            cmds_avec_probleme = set()
            nb_lignes_a_corriger = 0
            if all_lignes_corr:
                for ligne in all_lignes_corr:
                    sku = ligne.get("sku") or ""
                    id_cmd = str(ligne.get("id_commande", ""))
                    if not sku or sku not in skus_valides_corr:
                        cmds_avec_probleme.add(id_cmd)
                        nb_lignes_a_corriger += 1

            col1, col2 = st.columns(2)
            with col1:
                st.metric("Commandes avec lignes à corriger", len(cmds_avec_probleme))
            with col2:
                st.metric("Total lignes à corriger", nb_lignes_a_corriger)

            st.divider()

            def format_cmd(id_faire):
                if not id_faire:
                    return "Choisir une commande..."
                cmd = next((c for c in commandes_faire_corr if c["id_faire"] == id_faire), {})
                try:
                    date = pd.to_datetime(cmd.get("date_commande", "")).strftime("%d/%m/%Y")
                except Exception:
                    date = "?"
                client = cmd.get("nom_facturation", "")
                montant = float(cmd.get("montant_ttc") or 0)
                alerte = "⚠️ " if id_faire in cmds_avec_probleme else ""
                return f"{alerte}{date} — {client} — {montant:.2f}€"

            commande_choisie = st.selectbox(
                "Commande", options=[""] + ids_corr,
                format_func=format_cmd, key="sel_cmd_corr"
            )

            if commande_choisie:
                lignes_cmd = select("lignes_commande",
                    f"select=id,nom_produit,libelle_variation,quantite,prix_unitaire_ttc,sku"
                    f"&id_commande=eq.{commande_choisie}")

                if lignes_cmd:
                    ids_lignes = [row["id"] for row in lignes_cmd]
                    orig_skus = [row.get("sku") or "" for row in lignes_cmd]

                    df_edit_corr = pd.DataFrame([{
                        "Statut SKU": ("✅" if (row.get("sku") and row["sku"] in skus_valides_corr)
                                       else ("❌" if not row.get("sku") else "⚠️")),
                        "Produit": row.get("nom_produit", ""),
                        "Variation": row.get("libelle_variation", "") or "",
                        "Qté": row.get("quantite", 0),
                        "Prix TTC": float(row.get("prix_unitaire_ttc") or 0),
                        "SKU": row.get("sku") or "",
                    } for row in lignes_cmd])

                    df_result_corr = st.data_editor(
                        df_edit_corr,
                        column_config={
                            "Statut SKU": st.column_config.TextColumn("Statut", disabled=True),
                            "Produit": st.column_config.TextColumn("Produit", disabled=True),
                            "Variation": st.column_config.TextColumn("Variation", disabled=True),
                            "Qté": st.column_config.NumberColumn("Qté", disabled=True),
                            "Prix TTC": st.column_config.NumberColumn("Prix TTC", disabled=True),
                            "SKU": st.column_config.TextColumn("SKU"),
                        },
                        hide_index=True, use_container_width=True,
                        key=f"editor_lignes_{commande_choisie}"
                    )

                    edit_skus = df_result_corr["SKU"].tolist()
                    lignes_modifiees = [
                        {"id": ids_lignes[i], "sku": edit_skus[i]}
                        for i in range(len(ids_lignes))
                        if orig_skus[i] != edit_skus[i]
                    ]
                    st.caption(f"{len(lignes_modifiees)} ligne(s) modifiée(s).")

                    if st.button("Enregistrer les corrections", type="primary",
                                 disabled=len(lignes_modifiees) == 0, key="btn_enreg_corr"):
                        succes = 0
                        for ligne in lignes_modifiees:
                            ok = update("lignes_commande", f"id=eq.{ligne['id']}",
                                        {"sku": ligne["sku"] or None})
                            if ok:
                                succes += 1
                        st.success(f"✅ {succes} ligne(s) corrigée(s).")
                        st.rerun()
                else:
                    st.info("Aucune ligne pour cette commande.")

    # ── Onglet 4 : Vérification prix ──────────────────────────────────────────
    with tab4:
        filtre_prix = st.selectbox("Filtre", [
            "Tous", "Écart prix vente", "Coefficient anormal", "Sans prix conseillé"
        ], key="sel_filtre_prix_faire")

        faire_variants_prix = select("produits_faire_variants",
            "select=id_faire,sku,nom,prix_grossiste,prix_vente_conseille,sale_state,lifecycle_state")
        produits_data_prix = select("produits", "select=sku,nom,prix_vente_ht")
        prod_map_prix = {p["sku"]: p for p in produits_data_prix} if produits_data_prix else {}

        variants_avec_sku = [v for v in faire_variants_prix if v.get("sku")] if faire_variants_prix else []

        if not variants_avec_sku:
            st.info("Aucun variant avec SKU. Lance d'abord la sync 8️⃣ Produits Faire.")
        else:
            COEFF_CIBLE = 2.50
            rows_prix = []
            for v in variants_avec_sku:
                sku = v["sku"]
                prod_wizi = get_prod_parent(sku, prod_map_prix)
                nom = prod_wizi.get("nom", "") or v.get("nom", "") or sku
                prix_wizi_ttc = round(float(prod_wizi.get("prix_vente_ht") or 0) * 1.20, 2)
                prix_conseille = float(v.get("prix_vente_conseille") or 0)
                prix_grossiste = float(v.get("prix_grossiste") or 0)
                ecart_prix = round(prix_conseille - prix_wizi_ttc, 2)
                coeff = round(prix_conseille / prix_grossiste, 2) if prix_grossiste else 0
                ecart_coeff = round(coeff - COEFF_CIBLE, 2) if coeff else 0
                rows_prix.append({
                    "SKU": sku, "Produit": nom,
                    "Prix vente Wizi TTC": prix_wizi_ttc,
                    "Prix conseillé Faire": prix_conseille,
                    "Écart prix vente": ecart_prix,
                    "Prix revendeur Faire": prix_grossiste,
                    "Coefficient": coeff,
                    "Coefficient cible": COEFF_CIBLE,
                    "Écart coefficient": ecart_coeff,
                })

            df_prix = pd.DataFrame(rows_prix).sort_values("SKU").reset_index(drop=True)

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Écart prix vente > 0.05€", len(df_prix[df_prix["Écart prix vente"].abs() > 0.05]))
            with col2:
                st.metric("Coefficient anormal",
                          len(df_prix[(df_prix["Coefficient"] > 0) &
                                      ((df_prix["Coefficient"] < 2.40) | (df_prix["Coefficient"] > 2.60))]))
            with col3:
                st.metric("Sans prix conseillé", len(df_prix[df_prix["Prix conseillé Faire"] == 0]))

            if filtre_prix == "Écart prix vente":
                df_prix = df_prix[df_prix["Écart prix vente"].abs() > 0.05]
            elif filtre_prix == "Coefficient anormal":
                df_prix = df_prix[(df_prix["Coefficient"] > 0) &
                                  ((df_prix["Coefficient"] < 2.40) | (df_prix["Coefficient"] > 2.60))]
            elif filtre_prix == "Sans prix conseillé":
                df_prix = df_prix[df_prix["Prix conseillé Faire"] == 0]

            styled = df_prix.style \
                .map(lambda v: "color: red" if abs(v) > 0.05 else "", subset=["Écart prix vente"]) \
                .map(lambda v: "color: red" if v != 0 and (v < -0.10 or v > 0.10) else "",
                     subset=["Écart coefficient"])
            st.dataframe(styled, use_container_width=True, hide_index=True)
            csv_prix = df_prix.to_csv(index=False).encode("utf-8")
            st.download_button("📥 Télécharger en CSV", csv_prix, "verification_prix_faire.csv",
                               "text/csv", key="dl_prix_faire")

elif page == "📒 Réconciliation Faire":
    st.subheader("📒 Réconciliation Faire")

    with st.sidebar:
        st.divider()
        annee = st.selectbox("Année", [2026, 2025, 2024], index=0)

    commandes_faire = select("commandes",
        f"select=id_faire,date_commande,nom_facturation,prenom_facturation,"
        f"montant_ttc,frais_port,tva_client,montant_net_recu,frais_expedition_faire,commission_faire"
        f"&source=eq.faire&statut_code=not.in.(0,45,46,50)"
        f"&date_commande=gte.{annee}-01-01&date_commande=lt.{annee+1}-01-01"
        f"&order=date_commande.desc")

    if not commandes_faire:
        st.info("Aucune commande Faire pour cette année.")
    else:
        df_cmd = pd.DataFrame(commandes_faire)
        for col in ["montant_ttc", "frais_port", "tva_client",
                    "montant_net_recu", "frais_expedition_faire", "commission_faire"]:
            df_cmd[col] = pd.to_numeric(df_cmd[col], errors="coerce").fillna(0)

        ids_faire = [str(c["id_faire"]) for c in commandes_faire if c.get("id_faire")]
        ids_str = ",".join(ids_faire)

        lignes = select("lignes_commande",
            f"select=id_commande,sku,quantite&id_commande=in.({ids_str})",
            limit=50000)

        produits_data = select("produits", "select=sku,prix_achat_ht")
        prod_map_achat = {p["sku"]: p for p in produits_data} if produits_data else {}
        mapping_data = select("sku_mapping_faire", "select=sku_faire,sku_wizishop")
        sku_mapping = {m["sku_faire"]: m["sku_wizishop"] for m in mapping_data} if mapping_data else {}

        cout_achat_par_cmd = {}
        prix_achat_ok_par_cmd = {}

        if lignes:
            for ligne in lignes:
                id_cmd = str(ligne.get("id_commande", ""))
                sku = ligne.get("sku") or ""
                qty = float(ligne.get("quantite") or 0)
                sku_resolu = sku_mapping.get(sku, sku)
                prod_info = get_prod_parent(sku_resolu, prod_map_achat)
                prix = float(prod_info.get("prix_achat_ht") or 0)

                if id_cmd not in cout_achat_par_cmd:
                    cout_achat_par_cmd[id_cmd] = 0.0
                    prix_achat_ok_par_cmd[id_cmd] = True

                cout_achat_par_cmd[id_cmd] += prix * qty
                if prix <= 0:
                    prix_achat_ok_par_cmd[id_cmd] = False

        rows = []
        for _, row in df_cmd.iterrows():
            id_faire = str(row["id_faire"])
            try:
                date = pd.to_datetime(row["date_commande"]).strftime("%d/%m/%Y")
            except Exception:
                date = ""
            client = f"{row.get('nom_facturation', '')} {row.get('prenom_facturation', '')}".strip()
            ca_ht = float(row["montant_ttc"])
            frais_port = float(row["frais_port"])
            tva = float(row["tva_client"])
            total_ttc = round(ca_ht + frais_port + tva, 2)
            commission = float(row["commission_faire"])
            frais_exp = float(row["frais_expedition_faire"])
            net_recu = float(row["montant_net_recu"])
            cout_achat = round(cout_achat_par_cmd.get(id_faire, 0), 2)
            marge_nette = round(ca_ht - commission - cout_achat, 2)
            marge_pct = round(marge_nette / ca_ht * 100, 1) if ca_ht else 0

            ecart = round(total_ttc - commission - frais_exp - net_recu, 2)
            equilibre = "✅" if abs(ecart) <= 0.02 else f"⚠️ {ecart:+.2f}€"

            if id_faire in prix_achat_ok_par_cmd:
                prix_achat_statut = "✅" if prix_achat_ok_par_cmd[id_faire] else "⚠️ prix achat manquant"
            else:
                prix_achat_statut = "⚠️ prix achat manquant"

            rows.append({
                "Date": date,
                "Client": client,
                "CA HT articles": ca_ht,
                "Frais port": frais_port,
                "TVA": tva,
                "Total TTC calculé": total_ttc,
                "Commission Faire": commission,
                "Frais expéd. Faire": frais_exp,
                "Net reçu": net_recu,
                "Coût achat HT": cout_achat,
                "Marge nette": marge_nette,
                "Marge %": marge_pct,
                "Équilibre": equilibre,
                "Prix achat": prix_achat_statut,
            })

        df_table = pd.DataFrame(rows)

        ca_total = df_table["CA HT articles"].sum()
        commission_total = df_table["Commission Faire"].sum()
        cout_total = df_table["Coût achat HT"].sum()
        marge_totale = df_table["Marge nette"].sum()
        marge_pct_moy = round(marge_totale / ca_total * 100, 1) if ca_total else 0
        nb_desequilibre = len(df_table[df_table["Équilibre"] != "✅"])
        nb_prix_manquant = len(df_table[df_table["Prix achat"] != "✅"])

        col1, col2, col3, col4, col5, col6, col7 = st.columns(7)
        with col1:
            st.metric("CA HT total", f"{ca_total:.0f} €")
        with col2:
            st.metric("Commission Faire", f"{commission_total:.0f} €")
        with col3:
            st.metric("Coût achat total", f"{cout_total:.0f} €")
        with col4:
            st.metric("Marge nette", f"{marge_totale:.0f} €")
        with col5:
            st.metric("Marge %", f"{marge_pct_moy:.1f} %")
        with col6:
            st.metric("⚠️ Déséquilibrées", nb_desequilibre)
        with col7:
            st.metric("⚠️ Prix achat manquant", nb_prix_manquant)

        st.divider()
        st.dataframe(df_table, use_container_width=True, hide_index=True)
        csv = df_table.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Télécharger en CSV", csv,
                           f"reconciliation_faire_{annee}.csv", "text/csv")

elif page == "📊 Gestion stock Faire":
    st.subheader("📊 Gestion stock Faire")

    with st.sidebar:
        st.divider()
        nb_mois = st.slider("Période ventes (mois)", min_value=1, max_value=24, value=12)
        seuil_jours = st.slider("Seuil alerte jours de stock", min_value=7, max_value=90, value=30)
        seuil_ecart_jours = st.slider("Seuil écart significatif (jours)", min_value=5, max_value=60, value=15)
        alerte_filtre = st.selectbox("Filtre alerte", [
            "Toutes", "🔴 Urgent uniquement", "🔴 + 🟡 Attention"
        ])
        statut_filtre = st.selectbox("Statut listing", [
            "Tous", "Actifs uniquement", "Inactifs uniquement"
        ])

    faire_variants = select("produits_faire_variants",
        "select=sku,nom,available_quantity,sale_state,lifecycle_state")
    skus_data = select("skus", "select=sku,stock")
    produits_data = select("produits", "select=sku,nom")
    prod_map = {p["sku"]: p for p in produits_data} if produits_data else {}

    date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")
    commandes_faire = select("commandes",
        f"select=id_faire&source=eq.faire&statut_code=not.in.(0,45,46,50)&date_commande=gte.{date_limite}")

    if faire_variants:
        sku_stock_wizi = {s["sku"]: int(s["stock"] or 0) for s in skus_data} if skus_data else {}

        ventes_faire = {}
        if commandes_faire:
            ids_faire = [str(c["id_faire"]) for c in commandes_faire if c.get("id_faire")]
            ids_str = ",".join(ids_faire)
            lignes = select("lignes_commande",
                f"select=sku,quantite&id_commande=in.({ids_str})",
                limit=50000)
            if lignes:
                for ligne in lignes:
                    sku_key = ligne.get("sku") or ""
                    if sku_key:
                        ventes_faire[sku_key] = ventes_faire.get(sku_key, 0) + (ligne.get("quantite") or 0)

        rows = []
        for v in faire_variants:
            sku = v.get("sku") or ""
            if not sku:
                continue
            nom_produit = get_prod_parent(sku, prod_map).get("nom", "") or v.get("nom", "") or sku
            is_paused = v.get("sale_state") == "SALES_PAUSED"
            is_active = v.get("sale_state") == "FOR_SALE" and v.get("lifecycle_state") == "PUBLISHED"
            stock_faire = int(v.get("available_quantity") or 0)
            stock_wizi = sku_stock_wizi.get(sku, 0)
            ecart = stock_wizi - stock_faire

            v_faire = round(ventes_faire.get(sku, 0) / nb_mois, 1)
            ventes_par_jour = v_faire / 30
            jours_stock = round(stock_wizi / ventes_par_jour) if ventes_par_jour > 0 else 999

            if is_paused:
                # En pause sur Faire : aucune vente possible quel que soit le
                # stock — signalé en priorité, avant même les alertes de stock.
                alerte = "🚫 EN PAUSE"
                priorite = 0
            elif is_active and ((stock_wizi == 0 and stock_faire > 0 and v_faire > 0) or
                               (jours_stock < seuil_jours and v_faire > 0)):
                alerte = "🔴 URGENT"
                priorite = 1
            elif is_active and ((stock_faire > stock_wizi and v_faire > 0) or
                                 (ventes_par_jour > 0 and ecart > 0 and
                                  (ecart / ventes_par_jour) > seuil_ecart_jours)):
                alerte = "🟡 ATTENTION"
                priorite = 2
            elif not is_active and jours_stock > seuil_jours and v_faire > 0:
                alerte = "🟡 ATTENTION"
                priorite = 2
            elif stock_faire > stock_wizi and v_faire == 0:
                alerte = "⚪ INFO"
                priorite = 3
            else:
                alerte = "🟢 OK"
                priorite = 4

            rows.append({
                "SKU": sku,
                "Produit": nom_produit,
                "Stock Wizishop": stock_wizi,
                "Stock Faire": stock_faire,
                "Écart": ecart,
                "Ventes/mois Faire": v_faire,
                "Jours de stock": jours_stock,
                "Actif": "✅" if is_active else "⏸️",
                "Alerte": alerte,
                "_priorite": priorite
            })

        df = pd.DataFrame(rows)
        # Les SKUs sans aucun stock (Wizishop et Faire) sont exclus du bruit,
        # sauf s'ils sont en pause sur Faire : on veut les voir malgré tout.
        df = df[~((df["Stock Wizishop"] == 0) & (df["Stock Faire"] == 0) &
                  (df["Alerte"] != "🚫 EN PAUSE"))]

        nb_pause = len(df[df["Alerte"] == "🚫 EN PAUSE"])
        nb_urgent = len(df[df["Alerte"] == "🔴 URGENT"])
        nb_attention = len(df[df["Alerte"] == "🟡 ATTENTION"])
        nb_info = len(df[df["Alerte"] == "⚪ INFO"])
        nb_ok = len(df[df["Alerte"] == "🟢 OK"])

        col0, col1, col2, col3, col4 = st.columns(5)
        with col0:
            st.metric("🚫 En pause", nb_pause)
        with col1:
            st.metric("🔴 Urgent", nb_urgent)
        with col2:
            st.metric("🟡 Attention", nb_attention)
        with col3:
            st.metric("⚪ Info", nb_info)
        with col4:
            st.metric("🟢 OK", nb_ok)

        if statut_filtre == "Actifs uniquement":
            df = df[df["Actif"] == "✅"]
        elif statut_filtre == "Inactifs uniquement":
            df = df[df["Actif"] == "⏸️"]

        if alerte_filtre == "🔴 Urgent uniquement":
            df = df[df["Alerte"] == "🔴 URGENT"]
        elif alerte_filtre == "🔴 + 🟡 Attention":
            df = df[df["Alerte"].isin(["🔴 URGENT", "🟡 ATTENTION"])]

        df = df.sort_values("_priorite").drop(columns=["_priorite"]).reset_index(drop=True)
        st.dataframe(df, use_container_width=True, hide_index=True)
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Télécharger en CSV", csv, "gestion_stock_faire.csv", "text/csv")
    else:
        st.info("Aucune donnée. Lance d'abord la sync 8️⃣ Produits Faire.")

elif page == "⭐ Best-sellers Faire":
    with st.sidebar:
        st.divider()
        periode_faire = st.selectbox("Période", ["3 mois", "6 mois", "12 mois", "Tout"],
                                     key="bs_faire_periode")

    st.subheader("⭐ Best-sellers Faire")

    if periode_faire == "Tout":
        query_cmds_faire = "select=id_faire&source=eq.faire&statut_code=not.eq.50"
        nb_mois_faire = None
    else:
        nb_mois_faire = int(periode_faire.split()[0])
        date_limite_faire = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois_faire)).strftime("%Y-%m-%dT%H:%M:%S")
        query_cmds_faire = (
            f"select=id_faire&source=eq.faire&statut_code=not.eq.50"
            f"&date_commande=gte.{date_limite_faire}"
        )

    # Catalogue Faire (base du left join)
    faire_variants = select("produits_faire_variants", "select=sku,nom,id_produit_faire&sku=not.is.null")
    faire_produits = select("produits_faire", "select=id_faire,nom")

    catalogue_faire = {}
    if faire_variants:
        produit_map_faire = {r["id_faire"]: r.get("nom") or "" for r in faire_produits} if faire_produits else {}
        for r in faire_variants:
            sku = r.get("sku") or ""
            if not sku or sku in catalogue_faire:
                continue
            nom_variant = r.get("nom") or ""
            nom_parent = produit_map_faire.get(r.get("id_produit_faire"), "")
            catalogue_faire[sku] = nom_parent or nom_variant

    cmds_faire = select("commandes", query_cmds_faire)

    # Agréger les ventes par SKU
    ventes_faire = {}
    if cmds_faire:
        ids_str_faire = ",".join(str(c["id_faire"]) for c in cmds_faire if c.get("id_faire"))
        lignes_faire = select("lignes_commande",
            f"select=sku,quantite,prix_unitaire_ttc"
            f"&source=eq.faire&id_commande=in.({ids_str_faire})",
            limit=50000)
        if lignes_faire:
            for l in lignes_faire:
                sku = l.get("sku") or ""
                if not sku:
                    continue
                qty = l.get("quantite") or 0
                ca_ht = float(l.get("prix_unitaire_ttc") or 0) / 1.2 * qty
                if sku not in ventes_faire:
                    ventes_faire[sku] = {"quantite": 0, "ca_ht": 0.0}
                ventes_faire[sku]["quantite"] += qty
                ventes_faire[sku]["ca_ht"] += ca_ht

    if not catalogue_faire:
        st.info("Aucune donnée catalogue. Lance d'abord la sync Produits Faire.")
    else:
        rows_faire = []
        for sku, nom in catalogue_faire.items():
            data = ventes_faire.get(sku, {"quantite": 0, "ca_ht": 0.0})
            v_mois = round(data["quantite"] / nb_mois_faire, 1) if nb_mois_faire else (
                "—" if data["quantite"] == 0 else data["quantite"])
            rows_faire.append({
                "SKU": sku,
                "Produit": nom or sku,
                "Unités vendues": data["quantite"],
                "CA HT (€)": round(data["ca_ht"], 2),
                "Ventes/mois": v_mois,
            })

        df_faire = pd.DataFrame(rows_faire).sort_values(
            ["Unités vendues", "SKU"], ascending=[False, True]
        )

        total_unites_faire = df_faire["Unités vendues"].sum()
        total_ca_faire = df_faire["CA HT (€)"].sum()
        nb_skus_vendus_faire = (df_faire["Unités vendues"] > 0).sum()

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("📦 Unités vendues", f"{total_unites_faire:,}")
        with col2:
            st.metric("💶 CA HT", f"{total_ca_faire:,.2f} €")
        with col3:
            st.metric("🔢 SKUs vendus", f"{nb_skus_vendus_faire} / {len(df_faire)}")

        st.dataframe(df_faire, use_container_width=True, hide_index=True)
        csv_faire = df_faire.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Exporter CSV", csv_faire, "bestsellers_faire.csv", "text/csv",
                           key="bs_faire_csv")

elif page == "⭐ Best-sellers Foulard Frenchy":
    with st.sidebar:
        st.divider()
        periode_ff = st.selectbox("Période", ["3 mois", "6 mois", "12 mois", "Tout"],
                                  key="bs_ff_periode")

    st.subheader("⭐ Best-sellers Foulard Frenchy")

    if periode_ff == "Tout":
        nb_mois_ff = None
        filtre_date_ff = ""
    else:
        nb_mois_ff = int(periode_ff.split()[0])
        date_limite_ff = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois_ff)).strftime("%Y-%m-%dT%H:%M:%S")
        filtre_date_ff = f"&cree_le=gte.{date_limite_ff}"

    # Commandes valides (hors remboursées / annulées)
    query_cmds_ff = (
        "select=id_shopify"
        "&boutique=eq.foulard_frenchy"
        "&or=(statut_financier.is.null,statut_financier.not.in.(refunded,partially_refunded))"
        "&annule_le=is.null"
        + filtre_date_ff
    )
    cmds_ff = select("commandes_shopify", query_cmds_ff)

    # Catalogue Foulard Frenchy — variants de produits ACTIVE uniquement
    produits_actifs_ff = select(
        "produits_shopify",
        "select=id_shopify&boutique=eq.foulard_frenchy&statut=eq.ACTIVE",
    )
    ids_produits_actifs_ff = {r["id_shopify"] for r in (produits_actifs_ff or [])}

    variants_ff = select(
        "produits_shopify_variants",
        "select=sku,nom_complet,id_produit_shopify&boutique=eq.foulard_frenchy&sku=not.is.null",
    )
    catalogue_ff = {}
    if variants_ff:
        for v in variants_ff:
            if ids_produits_actifs_ff and v.get("id_produit_shopify") not in ids_produits_actifs_ff:
                continue
            sku = (v.get("sku") or "").strip()
            if not sku or sku in catalogue_ff:
                continue
            catalogue_ff[sku] = v.get("nom_complet") or sku

    # Jeu d'IDs numériques valides (extrait depuis id_shopify, qui peut être
    # un GID "gid://shopify/Order/XXXXX" ou directement un entier/string numérique)
    ids_valides_ff = set()
    if cmds_ff:
        for c in cmds_ff:
            raw = str(c.get("id_shopify") or "")
            ids_valides_ff.add(raw.rsplit("/", 1)[-1])

    # Toutes les lignes de la boutique (pas de filtre id_commande — évite le
    # problème de format GID vs numérique dans la clause in.())
    lignes_ff = select(
        "lignes_commande_shopify",
        "select=id_commande_shopify,sku,quantite,prix_unitaire_original"
        "&boutique=eq.foulard_frenchy",
        limit=50000,
    )

    # Agréger les ventes par SKU en joignant en Python
    ventes_ff = {}
    if lignes_ff:
        for l in lignes_ff:
            raw_id = str(l.get("id_commande_shopify") or "").rsplit("/", 1)[-1]
            if raw_id not in ids_valides_ff:
                continue
            sku = (l.get("sku") or "").strip()
            if not sku:
                continue
            qty = l.get("quantite") or 0
            ca = float(l.get("prix_unitaire_original") or 0) * qty
            if sku not in ventes_ff:
                ventes_ff[sku] = {"quantite": 0, "ca": 0.0}
            ventes_ff[sku]["quantite"] += qty
            ventes_ff[sku]["ca"] += ca

    if not catalogue_ff:
        st.info("Aucune donnée catalogue. Lance d'abord la sync Produits Foulard Frenchy.")
    else:
        rows_ff = []
        for sku, nom in catalogue_ff.items():
            data = ventes_ff.get(sku, {"quantite": 0, "ca": 0.0})
            rows_ff.append({
                "SKU":            sku,
                "Produit":        nom,
                "Unités vendues": data["quantite"],
                "CA (€)":         round(data["ca"], 2),
            })

        df_ff = pd.DataFrame(rows_ff).sort_values(
            ["Unités vendues", "SKU"], ascending=[False, True]
        )

        total_unites_ff = int(df_ff["Unités vendues"].sum())
        total_ca_ff     = df_ff["CA (€)"].sum()
        nb_skus_vendus_ff = int((df_ff["Unités vendues"] > 0).sum())

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("📦 Unités vendues", f"{total_unites_ff:,}")
        with col2:
            st.metric("💶 CA", f"{total_ca_ff:,.2f} €")
        with col3:
            st.metric("🔢 SKUs vendus", f"{nb_skus_vendus_ff} / {len(df_ff)}")

        def _categorie_ff(sku):
            s = (sku or "").upper()
            if s.startswith("CHIM"):  return "Foulard Chimio"
            if s.startswith("CH"):    return "Chouchou Foulard"
            if s.startswith("CF"):    return "Ceinture Foulard"
            if s.startswith("FCAR") or s.startswith("FC"): return "Foulard Carré"
            if s.startswith("FF"):    return "Foulard Femme"
            return "Autre"

        cats_ff = {}
        for row in rows_ff:
            cat = _categorie_ff(row["SKU"])
            cats_ff[cat] = cats_ff.get(cat, 0) + row["Unités vendues"]

        cats_sorted = sorted(cats_ff.items(), key=lambda x: x[1], reverse=True)
        if cats_sorted:
            st.divider()
            cols_cat = st.columns(len(cats_sorted))
            for col, (cat, qty) in zip(cols_cat, cats_sorted):
                pct = f"{qty / total_unites_ff * 100:.0f}%" if total_unites_ff else "—"
                col.metric(cat, f"{qty:,}", pct)

        st.dataframe(df_ff, use_container_width=True, hide_index=True)
        csv_ff = df_ff.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Exporter CSV", csv_ff, "bestsellers_foulard_frenchy.csv", "text/csv",
                           key="bs_ff_csv")

elif page == "🔗 Connexion Faire/Shopify":
    st.subheader("🔗 Connexion Faire/Shopify")

    if st.button("Tester la connexion Faire"):
        with st.spinner("Test de connexion..."):
            try:
                r = faire_api_get("/orders", params={"limit": 10})
                if r.status_code == 200:
                    st.success("✅ Connexion Faire fonctionnelle")
                else:
                    st.error(f"Erreur {r.status_code} : {r.text[:300]}")
            except Exception as e:
                st.error(f"Erreur : {e}")

    if st.button("Tester les permissions d'écriture Faire"):
        with st.spinner("Test en cours..."):
            try:
                status, body = test_write_permission()
                if status is None:
                    st.warning(f"Impossible de tester : {body}")
                else:
                    st.write(f"**Status code :** {status}")
                    st.write(f"**Réponse :** {body}")
            except Exception as e:
                st.error(f"Erreur : {e}")

    st.divider()
    st.subheader("🛍️ Connexion Shopify Foulard Frenchy")

    # Résultat de l'échange OAuth (si callback vient d'être traité)
    if "shopify_ff_error" in st.session_state:
        st.error(f"Erreur OAuth : {st.session_state.pop('shopify_ff_error')}")

    if "shopify_ff_token_obtained" in st.session_state:
        st.success("✅ Token Shopify obtenu !")
        st.write(f"**Scopes accordés :** `{st.session_state['shopify_ff_scope_obtained']}`")
        st.info("Ajoute cette valeur dans tes secrets Streamlit sous la clé `SHOPIFY_FOULARD_FRENCHY_TOKEN` puis redémarre l'app :")
        st.code(st.session_state["shopify_ff_token_obtained"])

    # Token déjà configuré → boutons de test
    _token_pret = "SHOPIFY_FOULARD_FRENCHY_TOKEN" in st.secrets
    if _token_pret:
        st.success("✅ Token configuré dans les secrets")
        _col_ff1, _col_ff2 = st.columns(2)
        with _col_ff1:
            if st.button("Tester la connexion Shopify"):
                with st.spinner("Test en cours..."):
                    try:
                        shop, token = get_shopify_token()
                        status, result = shopify_test_connection(shop, token)
                        if status == 200:
                            errors = result.get("errors")
                            if errors:
                                st.error(f"Erreur GraphQL : {errors}")
                            else:
                                shop_info = result["shop"]
                                st.success("✅ Connexion Shopify fonctionnelle")
                                st.write(f"**Boutique :** {shop_info.get('name')} (`{shop_info.get('domain')}`)")
                                st.write(f"**Email :** {shop_info.get('email')}")
                                st.write(f"**Plan :** {shop_info.get('plan_name')}")
                        else:
                            st.error(f"Erreur {status} : {result}")
                    except Exception as e:
                        st.error(f"Erreur : {e}")
        with _col_ff2:
            if st.button("Vérifier les scopes du token"):
                with st.spinner("Récupération des scopes..."):
                    try:
                        shop, token = get_shopify_token()
                        r = requests.get(
                            f"https://{shop}/admin/oauth/access_scopes.json",
                            headers={"X-Shopify-Access-Token": token}
                        )
                        if r.status_code == 200:
                            scopes = [s["handle"] for s in r.json().get("access_scopes", [])]
                            st.success(f"✅ {len(scopes)} scope(s) accordé(s) :")
                            st.code("\n".join(scopes))
                            if "read_all_orders" in scopes:
                                st.success("✅ read_all_orders présent — accès illimité aux commandes")
                            else:
                                st.warning("⚠️ read_all_orders absent — commandes limitées à 60 jours. Réautorise l'app.")
                        else:
                            st.error(f"Erreur {r.status_code} : {r.text[:200]}")
                    except Exception as e:
                        st.error(f"Erreur : {e}")

    # Flux OAuth — bouton d'installation
    st.divider()
    st.markdown("**Installer / réinstaller l'app pour obtenir un nouveau token :**")
    try:
        _shop_ff = st.secrets["SHOPIFY_FOULARD_FRENCHY_SHOP"]
        _cid_ff = st.secrets["SHOPIFY_FOULARD_FRENCHY_CLIENT_ID"]
        _nonce = f"shopify_ff_{shopify_generate_nonce()}"
        st.session_state["shopify_ff_nonce"] = _nonce
        _auth_url = shopify_get_auth_url(_shop_ff, _cid_ff, _nonce)
        st.code(_auth_url, language=None)
        st.link_button("🔐 Autoriser l'app sur Shopify Foulard Frenchy", _auth_url)
        st.caption(f"redirect_uri enregistrée : `https://piquepince-dashboard-e5yp9kroebwpi6edfgl9zo.streamlit.app`")
    except KeyError as e:
        st.warning(f"Secret manquant : {e}")

    st.divider()
    st.subheader("🧸 Connexion Shopify Montessori")

    # Résultat de l'échange OAuth (si callback vient d'être traité)
    if "shopify_montessori_error" in st.session_state:
        st.error(f"Erreur OAuth : {st.session_state.pop('shopify_montessori_error')}")

    if "shopify_montessori_token_obtained" in st.session_state:
        st.success("✅ Token Shopify obtenu !")
        st.write(f"**Scopes accordés :** `{st.session_state['shopify_montessori_scope_obtained']}`")
        st.info("Ajoute cette valeur dans tes secrets Streamlit sous la clé `SHOPIFY_BOUTIQUE2_TOKEN` puis redémarre l'app :")
        st.code(st.session_state["shopify_montessori_token_obtained"])

    # Token déjà configuré → bouton de test
    _token_montessori_pret = "SHOPIFY_BOUTIQUE2_TOKEN" in st.secrets
    if _token_montessori_pret:
        st.success("✅ Token configuré dans les secrets")
        if st.button("Tester la connexion Shopify Montessori"):
            with st.spinner("Test en cours..."):
                try:
                    shop, token = get_shopify_token_montessori()
                    status, result = shopify_test_connection(shop, token)
                    if status == 200:
                        errors = result.get("errors")
                        if errors:
                            st.error(f"Erreur GraphQL : {errors}")
                        else:
                            shop_info = result["shop"]
                            st.success("✅ Connexion Shopify Montessori fonctionnelle")
                            st.write(f"**Boutique :** {shop_info.get('name')} (`{shop_info.get('domain')}`)")
                            st.write(f"**Email :** {shop_info.get('email')}")
                            st.write(f"**Plan :** {shop_info.get('plan_name')}")
                    else:
                        st.error(f"Erreur {status} : {result}")
                except Exception as e:
                    st.error(f"Erreur : {e}")

    # Flux OAuth — bouton d'installation
    st.divider()
    st.markdown("**Installer / réinstaller l'app pour obtenir un nouveau token :**")
    try:
        _shop_m = st.secrets["SHOPIFY_BOUTIQUE2_SHOP"]
        _cid_m = st.secrets["SHOPIFY_BOUTIQUE2_CLIENT_ID"]
        _nonce_m = f"shopify_montessori_{shopify_generate_nonce()}"
        st.session_state["shopify_montessori_nonce"] = _nonce_m
        _auth_url_m = shopify_get_auth_url(_shop_m, _cid_m, _nonce_m)
        st.link_button("🔐 Autoriser l'app sur Shopify Montessori", _auth_url_m)
        st.caption(f"redirect_uri enregistrée : `https://piquepince-dashboard-e5yp9kroebwpi6edfgl9zo.streamlit.app`")
    except KeyError as e:
        st.warning(f"Secret manquant : {e}")

elif page == "🚨 Réapprovisionnement Foulard Frenchy":
    from datetime import timedelta

    with st.sidebar:
        st.divider()
        nb_mois_ff = st.slider("Période calcul ventes (mois)", min_value=1, max_value=2, value=2,
                               key="reap_ff_nb_mois")
        alerte_filtre_ff = st.selectbox("Filtre alerte", [
            "Tous les produits",
            "🔴 À commander uniquement",
            "🔴 + 🟡 Surveiller",
        ], key="reap_ff_alerte")

    st.subheader("🚨 Réapprovisionnement Foulard Frenchy")
    st.info(f"Calcul basé sur les ventes des {nb_mois_ff} derniers mois. Objectif : 4 mois de stock.")

    # ── Chargement données ────────────────────────────────────────────────────

    @st.cache_data(ttl=300)
    def _ff_load_produits():
        rows = select("produits_shopify",
            "select=id_shopify,fournisseur"
            "&boutique=eq.foulard_frenchy&statut=eq.ACTIVE")
        return {r["id_shopify"]: r.get("fournisseur") or "" for r in rows} if rows else {}

    @st.cache_data(ttl=300)
    def _ff_load_variants(ids_actifs_tuple):
        if not ids_actifs_tuple:
            return []
        ids_str = ",".join(ids_actifs_tuple)
        rows = select("produits_shopify_variants",
            f"select=sku,nom_complet,stock,id_produit_shopify"
            f"&boutique=eq.foulard_frenchy&sku=not.is.null"
            f"&id_produit_shopify=in.({ids_str})")
        return rows or []

    @st.cache_data(ttl=300)
    def _ff_load_ventes(depuis):
        lignes = select("lignes_commande_shopify",
            "select=sku,quantite,id_commande_shopify"
            "&boutique=eq.foulard_frenchy&sku=not.is.null",
            limit=50000)
        if not lignes:
            return {}

        cmds = select("commandes_shopify",
            f"select=id_shopify"
            f"&boutique=eq.foulard_frenchy"
            f"&statut_financier=not.eq.voided"
            f"&cree_le=gte.{depuis}"
            f"&commande_test=eq.false")
        if not cmds:
            return {}
        ids_valides = {c["id_shopify"] for c in cmds}

        ventes = {}
        for l in lignes:
            if l.get("id_commande_shopify") not in ids_valides:
                continue
            sku = l.get("sku") or ""
            if not sku:
                continue
            ventes[sku] = ventes.get(sku, 0) + (l.get("quantite") or 0)
        return ventes

    @st.cache_data(ttl=60)
    def _ff_load_en_commande():
        rows = select("commandes_fournisseur",
            "select=id,sku,nom_produit,fournisseur,date_commande,quantite_commandee,quantite_attendue,quantite_recue"
            "&source=eq.foulard_frenchy&statut=in.(en_commande,recu_partiel)&order=date_commande.desc")
        return rows or []

    depuis_str = (datetime.now(timezone.utc) - timedelta(days=30 * nb_mois_ff)).strftime("%Y-%m-%dT%H:%M:%SZ")

    with st.spinner("Chargement des données Shopify..."):
        prod_fourn_ff  = _ff_load_produits()
        ids_actifs     = tuple(sorted(prod_fourn_ff.keys()))
        variants_ff    = _ff_load_variants(ids_actifs)
        ventes_ff      = _ff_load_ventes(depuis_str)
        en_commande_ff = _ff_load_en_commande()

    skus_exclu_ff = set()
    skus_partiels_ff = {}
    for c in en_commande_ff:
        sku_c = c["sku"]
        qty_cmd = int(c.get("quantite_commandee") or 0)
        qty_att = int(c.get("quantite_attendue") or 0)
        if qty_att == 0 or qty_cmd >= qty_att:
            skus_exclu_ff.add(sku_c)
        else:
            skus_partiels_ff[sku_c] = c

    # ── Construction tableau ──────────────────────────────────────────────────

    fournisseurs_set_ff = set()
    rows_ff = []

    for v in variants_ff:
        sku = v.get("sku") or ""
        if not sku or sku in skus_exclu_ff:
            continue
        stock = int(v.get("stock") or 0)
        fournisseur = prod_fourn_ff.get(v.get("id_produit_shopify") or "", "") or "—"
        fournisseurs_set_ff.add(fournisseur)

        ventes_total = ventes_ff.get(sku, 0)
        v_mois = round(ventes_total / nb_mois_ff, 1)

        if sku in skus_partiels_ff:
            mois_stock = round(stock / v_mois, 1) if v_mois > 0 else 999
            alerte = "⚠️ Commande partielle"
            cmd_p = skus_partiels_ff[sku]
            qty_cmd_p = int(cmd_p.get("quantite_commandee") or 0)
            qty_att_p = int(cmd_p.get("quantite_attendue") or 0)
            qty_a_commander = max(0, qty_att_p - qty_cmd_p)
        elif v_mois > 0:
            mois_stock = round(stock / v_mois, 1)
            qty_a_commander = max(0, round(v_mois * 4) - stock)
            if mois_stock <= 3:
                alerte = "🔴 Commander"
            elif mois_stock <= 5:
                alerte = "🟡 Surveiller"
            else:
                alerte = "🟢 OK"
        elif stock == 0:
            # Rupture totale, aucune vente récente
            mois_stock = 0
            alerte = "🔴 Commander"
            qty_a_commander = 0
        else:
            # Stock présent, aucune vente récente
            mois_stock = 999
            alerte = "🟢 OK"
            qty_a_commander = 0

        rows_ff.append({
            "sku":             sku,
            "Produit":         v.get("nom_complet") or sku,
            "Fournisseur":     fournisseur,
            "Stock":           stock,
            "Ventes/mois":     v_mois,
            "Mois de stock":   mois_stock if mois_stock < 99 else "—",
            "Qté à commander": qty_a_commander,
            "Alerte":          alerte,
        })

    df_ff = pd.DataFrame(rows_ff)

    # ── Filtres ───────────────────────────────────────────────────────────────

    fourn_options_ff = ["Tous"] + sorted(fournisseurs_set_ff)
    with st.sidebar:
        fournisseur_filtre_ff = st.selectbox("Fournisseur", fourn_options_ff, key="reap_ff_fourn")

    if df_ff.empty:
        st.info("Aucun produit à afficher.")
    else:
        if fournisseur_filtre_ff != "Tous":
            df_ff = df_ff[df_ff["Fournisseur"] == fournisseur_filtre_ff]

        if alerte_filtre_ff == "🔴 À commander uniquement":
            df_ff = df_ff[df_ff["Alerte"].isin(["🔴 Commander", "⚠️ Commande partielle"])]
        elif alerte_filtre_ff == "🔴 + 🟡 Surveiller":
            df_ff = df_ff[df_ff["Alerte"].isin(["🔴 Commander", "🟡 Surveiller", "⚠️ Commande partielle"])]

        df_ff = df_ff.sort_values(["Alerte", "Ventes/mois"], ascending=[True, False])
        st.caption(f"{len(df_ff)} produits affichés")

        csv_ff = df_ff[["Alerte", "sku", "Produit", "Fournisseur", "Stock",
                         "Ventes/mois", "Mois de stock", "Qté à commander"]].to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Exporter CSV", csv_ff,
                           file_name="reapprovisionnement_foulard_frenchy.csv",
                           mime="text/csv", key="reap_ff_csv")

        # ── Éditeur de commande ───────────────────────────────────────────────

        df_edit_ff = df_ff.copy()
        df_edit_ff["Qté commandée"] = df_edit_ff["Qté à commander"]

        edited_ff = st.data_editor(
            df_edit_ff[["Alerte", "sku", "Produit", "Fournisseur", "Stock",
                         "Ventes/mois", "Mois de stock", "Qté à commander", "Qté commandée"]],
            column_config={
                "Alerte":          st.column_config.TextColumn("Alerte", width="small"),
                "sku":             st.column_config.TextColumn("SKU"),
                "Produit":         st.column_config.TextColumn("Produit", width="large"),
                "Fournisseur":     st.column_config.TextColumn("Fournisseur"),
                "Stock":           st.column_config.NumberColumn("Stock"),
                "Ventes/mois":     st.column_config.NumberColumn("Ventes/mois", format="%.1f"),
                "Mois de stock":   st.column_config.TextColumn("Mois stock"),
                "Qté à commander": st.column_config.NumberColumn("Qté suggérée"),
                "Qté commandée":   st.column_config.NumberColumn("Qté commandée", min_value=0),
            },
            disabled=["Alerte", "sku", "Produit", "Fournisseur", "Stock",
                      "Ventes/mois", "Mois de stock", "Qté à commander"],
            use_container_width=True,
            hide_index=True,
            key="reap_ff_editor",
        )

        if st.button("📦 Marquer en commande", type="primary"):
            lignes_cmd_ff = edited_ff[edited_ff["Qté commandée"] > 0]
            if lignes_cmd_ff.empty:
                st.warning("Aucune quantité saisie.")
            else:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                records_ff = []
                for _, row in lignes_cmd_ff.iterrows():
                    records_ff.append({
                        "source":             "foulard_frenchy",
                        "sku":                row["sku"],
                        "nom_produit":        row["Produit"],
                        "fournisseur":        None if row["Fournisseur"] == "—" else row["Fournisseur"],
                        "date_commande":      today,
                        "quantite_attendue":  int(row["Qté à commander"]),
                        "quantite_commandee": int(row["Qté commandée"]),
                        "statut":             "en_commande",
                    })
                try:
                    upsert("commandes_fournisseur", records_ff, "id")
                    _ff_load_en_commande.clear()
                    st.success(f"✅ {len(records_ff)} produit(s) marqués en commande.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur : {e}")

    # ── Produits en commande ──────────────────────────────────────────────────

    st.divider()
    st.subheader("📦 Produits en commande")

    if en_commande_ff:
        df_cmd_ff = pd.DataFrame(en_commande_ff)
        df_cmd_ff = df_cmd_ff.rename(columns={
            "sku":                "SKU",
            "nom_produit":        "Produit",
            "fournisseur":        "Fournisseur",
            "date_commande":      "Date commande",
            "quantite_commandee": "Qté commandée",
            "quantite_attendue":  "Qté attendue",
            "quantite_recue":     "Qté reçue",
        })
        st.dataframe(df_cmd_ff[["SKU", "Produit", "Fournisseur", "Date commande",
                                 "Qté attendue", "Qté commandée", "Qté reçue"]],
                     use_container_width=True, hide_index=True)
    else:
        st.info("Aucune commande fournisseur en cours.")

# ══════════════════════════════════════════════════════════════════════════════
# 🛍️ ANKORSTORE
# ══════════════════════════════════════════════════════════════════════════════

elif page == "⭐ Best-sellers Ankorstore":
    with st.sidebar:
        st.divider()
        periode_ank = st.selectbox("Période", ["3 mois", "6 mois", "12 mois", "Tout"],
                                   key="bs_ank_periode")

    st.subheader("⭐ Best-sellers Ankorstore")

    if periode_ank == "Tout":
        query_cmds_ank = (
            "select=id_ankorstore"
            "&source=eq.ankorstore"
            "&statut_texte=not.eq.cancelled"
        )
        nb_mois_ank = None
    else:
        nb_mois_ank = int(periode_ank.split()[0])
        date_limite_ank = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois_ank)).strftime("%Y-%m-%dT%H:%M:%S")
        query_cmds_ank = (
            "select=id_ankorstore"
            "&source=eq.ankorstore"
            "&statut_texte=not.eq.cancelled"
            f"&date_commande=gte.{date_limite_ank}"
        )

    # Catalogue Ankorstore — variants + nom produit parent
    variants_ank_bs = select("produits_ankorstore_variants",
        "select=sku,nom,prix_grossiste,id_produit_ankorstore&sku=not.is.null")
    produits_ank_bs = select("produits_ankorstore", "select=id_ankorstore,nom")
    produit_map_ank = {p["id_ankorstore"]: p.get("nom") for p in (produits_ank_bs or [])}

    catalogue_ank = {}
    for v in (variants_ank_bs or []):
        sku = (v.get("sku") or "").strip()
        if not sku or sku in catalogue_ank:
            continue
        nom = produit_map_ank.get(v.get("id_produit_ankorstore")) or v.get("nom") or sku
        catalogue_ank[sku] = nom

    cmds_ank_bs = select("commandes", query_cmds_ank)

    ventes_ank = {}
    if cmds_ank_bs:
        ids_ank_bs = ",".join(c["id_ankorstore"] for c in cmds_ank_bs if c.get("id_ankorstore"))
        if ids_ank_bs:
            lignes_ank_bs = select(
                "lignes_commande",
                f"select=sku,quantite,prix_unitaire_ttc"
                f"&source=eq.ankorstore&id_commande=in.({ids_ank_bs})",
                limit=50000,
            )
            for l in (lignes_ank_bs or []):
                sku = (l.get("sku") or "").strip()
                if not sku:
                    continue
                qty   = l.get("quantite") or 0
                ca_ht = float(l.get("prix_unitaire_ttc") or 0) / 1.2 * qty
                if sku not in ventes_ank:
                    ventes_ank[sku] = {"quantite": 0, "ca_ht": 0.0}
                ventes_ank[sku]["quantite"] += qty
                ventes_ank[sku]["ca_ht"]    += ca_ht

    if not catalogue_ank:
        st.info("Aucune donnée catalogue. Lance d'abord la sync Produits Ankorstore.")
    else:
        rows_ank_bs = []
        for sku, nom in catalogue_ank.items():
            d = ventes_ank.get(sku, {"quantite": 0, "ca_ht": 0.0})
            rows_ank_bs.append({
                "SKU":            sku,
                "Produit":        nom,
                "Unités vendues": d["quantite"],
                "CA HT (€)":      round(d["ca_ht"], 2),
            })

        df_ank_bs = pd.DataFrame(rows_ank_bs).sort_values(
            ["Unités vendues", "SKU"], ascending=[False, True])

        total_u_ank  = int(df_ank_bs["Unités vendues"].sum())
        total_ca_ank = df_ank_bs["CA HT (€)"].sum()
        nb_skus_ank  = int((df_ank_bs["Unités vendues"] > 0).sum())

        col1, col2, col3 = st.columns(3)
        with col1: st.metric("📦 Unités vendues", f"{total_u_ank:,}")
        with col2: st.metric("💶 CA HT", f"{total_ca_ank:,.2f} €")
        with col3: st.metric("🔢 SKUs vendus", f"{nb_skus_ank} / {len(df_ank_bs)}")

        st.dataframe(df_ank_bs, use_container_width=True, hide_index=True)
        st.download_button("📥 Exporter CSV",
            df_ank_bs.to_csv(index=False).encode("utf-8"),
            "bestsellers_ankorstore.csv", "text/csv", key="bs_ank_csv")


elif page == "📊 Gestion stock Ankorstore":
    st.subheader("📊 Gestion stock Ankorstore")

    with st.sidebar:
        st.divider()
        nb_mois_ga   = st.slider("Période ventes (mois)", 1, 24, 12, key="gs_ank_mois")
        seuil_j_ga   = st.slider("Seuil alerte jours de stock", 7, 90, 30, key="gs_ank_seuil")
        alerte_ga    = st.selectbox("Filtre alerte",
            ["Toutes", "🔴 Urgent uniquement", "🔴 + 🟡 Attention"], key="gs_ank_alerte")

    produits_ank_actifs_ga = select("produits_ankorstore",
        "select=id_ankorstore&active=eq.true&archived=eq.false")
    ids_actifs_ga = {p["id_ankorstore"] for p in (produits_ank_actifs_ga or [])}

    ank_vars_ga   = select("produits_ankorstore_variants",
        "select=sku,nom,stock,id_produit_ankorstore")
    skus_ga       = select("skus", "select=sku,stock")
    prod_wizi_ga  = select("produits", "select=sku,nom")
    prod_map_ga   = {p["sku"]: p for p in (prod_wizi_ga or [])}
    sku_stock_wizi_ga = {s["sku"]: int(s["stock"] or 0) for s in (skus_ga or [])}

    date_lim_ga = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois_ga)).strftime("%Y-%m-%dT%H:%M:%S")
    cmds_ga = select("commandes",
        f"select=id_ankorstore&source=eq.ankorstore&statut_texte=not.eq.cancelled"
        f"&date_commande=gte.{date_lim_ga}")

    if ank_vars_ga:
        ventes_ga = {}
        if cmds_ga:
            ids_ga = ",".join(c["id_ankorstore"] for c in cmds_ga if c.get("id_ankorstore"))
            if ids_ga:
                lignes_ga = select("lignes_commande",
                    f"select=sku,quantite&source=eq.ankorstore&id_commande=in.({ids_ga})",
                    limit=50000)
                for l in (lignes_ga or []):
                    k = l.get("sku") or ""
                    if k:
                        ventes_ga[k] = ventes_ga.get(k, 0) + (l.get("quantite") or 0)

        rows_ga = []
        for v in ank_vars_ga:
            sku = (v.get("sku") or "").strip()
            if not sku:
                continue
            is_active  = v.get("id_produit_ankorstore") in ids_actifs_ga
            stock_ank  = int(v.get("stock") or 0)
            stock_wizi = sku_stock_wizi_ga.get(sku, 0)
            nom        = get_prod_parent(sku, prod_map_ga).get("nom", "") or v.get("nom", "") or sku
            v_ank      = round(ventes_ga.get(sku, 0) / nb_mois_ga, 1)
            ventes_j   = v_ank / 30
            jours_stk  = round(stock_wizi / ventes_j) if ventes_j > 0 else 999
            ecart      = stock_wizi - stock_ank

            if is_active and jours_stk < seuil_j_ga and v_ank > 0:
                alerte, prio = "🔴 URGENT", 1
            elif is_active and ecart > 0 and v_ank > 0:
                alerte, prio = "🟡 ATTENTION", 2
            elif not is_active and v_ank > 0:
                alerte, prio = "🟡 ATTENTION", 2
            else:
                alerte, prio = "🟢 OK", 4

            rows_ga.append({
                "SKU": sku, "Produit": nom,
                "Stock Wizishop": stock_wizi, "Stock Ankorstore": stock_ank,
                "Écart": ecart, "Ventes/mois": v_ank,
                "Jours de stock": jours_stk if jours_stk < 999 else "—",
                "Actif": "✅" if is_active else "⏸️",
                "Alerte": alerte, "_priorite": prio,
            })

        df_ga = pd.DataFrame(rows_ga)
        df_ga = df_ga[~((df_ga["Stock Wizishop"] == 0) & (df_ga["Stock Ankorstore"] == 0))]

        col1, col2, col3 = st.columns(3)
        with col1: st.metric("🔴 Urgent",    len(df_ga[df_ga["Alerte"] == "🔴 URGENT"]))
        with col2: st.metric("🟡 Attention", len(df_ga[df_ga["Alerte"] == "🟡 ATTENTION"]))
        with col3: st.metric("🟢 OK",        len(df_ga[df_ga["Alerte"] == "🟢 OK"]))

        if alerte_ga == "🔴 Urgent uniquement":
            df_ga = df_ga[df_ga["Alerte"] == "🔴 URGENT"]
        elif alerte_ga == "🔴 + 🟡 Attention":
            df_ga = df_ga[df_ga["Alerte"].isin(["🔴 URGENT", "🟡 ATTENTION"])]

        df_ga = df_ga.sort_values("_priorite").drop(columns=["_priorite"]).reset_index(drop=True)
        st.dataframe(df_ga, use_container_width=True, hide_index=True)
        st.download_button("📥 Télécharger en CSV",
            df_ga.to_csv(index=False).encode("utf-8"),
            "gestion_stock_ankorstore.csv", "text/csv", key="dl_gs_ank")
    else:
        st.info("Aucune donnée. Lance d'abord la sync Produits Ankorstore.")


elif page == "🔎 Produits manquants sur Ankorstore":
    st.subheader("🔎 Produits manquants sur Ankorstore")

    with st.sidebar:
        st.divider()
        fournisseurs_ank_ma = ["Tous", "VEINIERE", "NPC", "NPGL", "NAVARRO", "BAVOUX", "DELORME"]
        fourn_ank_ma = st.selectbox("Fournisseur", fournisseurs_ank_ma, key="manquants_ank_fourn")

    skus_data_ma   = select("skus", "select=sku,stock&statut=eq.visible")
    prod_data_ma   = select("produits",
        "select=sku,nom,fournisseur,nom_categorie,prix_vente_ht,reference_fournisseur")

    prod_ank_actifs_ma = select("produits_ankorstore",
        "select=id_ankorstore&active=eq.true&archived=eq.false")
    ids_actifs_ma = {p["id_ankorstore"] for p in (prod_ank_actifs_ma or [])}

    ank_vars_ma = select("produits_ankorstore_variants",
        "select=sku,id_produit_ankorstore&sku=not.is.null")
    if ank_vars_ma and ids_actifs_ma:
        skus_ank_ma = {
            v["sku"] for v in ank_vars_ma
            if v.get("sku") and v.get("id_produit_ankorstore") in ids_actifs_ma
        }
    else:
        skus_ank_ma = {v["sku"] for v in (ank_vars_ma or []) if v.get("sku")}

    ank_ignores_data = select("ankorstore_ignores",
        "select=sku,raison,created_at&order=created_at.desc")
    ank_ignores_set = {r["sku"] for r in ank_ignores_data} if ank_ignores_data else set()

    if skus_data_ma:
        skus_wizi_ma  = {s["sku"] for s in skus_data_ma}
        prod_map_ma   = {p["sku"]: p for p in prod_data_ma} if prod_data_ma else {}
        sku_stock_ma  = {s["sku"]: int(s["stock"] or 0) for s in skus_data_ma}

        rows_ma = []
        for sku in skus_wizi_ma - skus_ank_ma - ank_ignores_set:
            prod = get_prod_parent(sku, prod_map_ma)
            rows_ma.append({
                "SKU":              sku,
                "Produit":          prod.get("nom") or "",
                "Fournisseur":      prod.get("fournisseur") or "",
                "Catégorie":        prod.get("nom_categorie") or "",
                "Prix vente HT":    prod.get("prix_vente_ht") or 0,
                "Stock":            sku_stock_ma.get(sku, 0),
                "Réf. fournisseur": prod.get("reference_fournisseur") or "",
            })

        df_ma = pd.DataFrame(rows_ma) if rows_ma else pd.DataFrame()
        df_ma_filtre = (df_ma[df_ma["Fournisseur"] == fourn_ank_ma]
                        if fourn_ank_ma != "Tous" and not df_ma.empty else df_ma.copy())

        col1, col2, col3 = st.columns(3)
        with col1: st.metric("Total SKUs manquants", len(df_ma))
        with col2:
            lbl = f"Manquants — {fourn_ank_ma}" if fourn_ank_ma != "Tous" else "Affichés"
            st.metric(lbl, len(df_ma_filtre))
        with col3: st.metric("🚫 Ignorés", len(ank_ignores_set))

        if not df_ma_filtre.empty:
            df_ed_ma = df_ma_filtre.sort_values("SKU").reset_index(drop=True).copy()
            df_ed_ma["Ignorer ?"] = False

            st.download_button("📥 Exporter CSV",
                df_ma_filtre.to_csv(index=False).encode("utf-8"),
                "produits_manquants_ankorstore.csv", "text/csv", key="dl_manquants_ank")

            with st.form("form_ank_ignores"):
                ed_ma = st.data_editor(df_ed_ma, use_container_width=True, hide_index=True,
                    column_config={
                        "SKU":              st.column_config.TextColumn(disabled=True),
                        "Produit":          st.column_config.TextColumn(disabled=True),
                        "Fournisseur":      st.column_config.TextColumn(disabled=True),
                        "Catégorie":        st.column_config.TextColumn(disabled=True),
                        "Prix vente HT":    st.column_config.NumberColumn(disabled=True),
                        "Stock":            st.column_config.NumberColumn(disabled=True),
                        "Réf. fournisseur": st.column_config.TextColumn(disabled=True),
                        "Ignorer ?":        st.column_config.CheckboxColumn(),
                    })
                submitted_ma = st.form_submit_button(
                    "🚫 Ignorer les produits sélectionnés", type="secondary")

            if submitted_ma:
                a_ign = ed_ma[ed_ma["Ignorer ?"] == True]
                if a_ign.empty:
                    st.warning("Aucun produit sélectionné.")
                else:
                    for _, r in a_ign.iterrows():
                        upsert("ankorstore_ignores", [{"sku": r["SKU"], "raison": None}], "sku")
                    st.success(f"✓ {len(a_ign)} produit(s) ignoré(s) !")
                    st.rerun()
        else:
            st.info("Aucun SKU manquant pour ce filtre." if fourn_ank_ma != "Tous"
                    else "Tous les SKUs Wizishop sont présents sur Ankorstore (actifs).")

        st.divider()
        st.subheader("🚫 Produits exclus d'Ankorstore")

        if ank_ignores_data:
            df_ign_ma = pd.DataFrame(ank_ignores_data)
            df_ign_ma["created_at"] = pd.to_datetime(
                df_ign_ma["created_at"]).dt.strftime("%d/%m/%Y")
            df_ign_ma = df_ign_ma[["sku", "raison", "created_at"]].rename(
                columns={"sku": "SKU", "raison": "Raison", "created_at": "Date"})
            st.dataframe(df_ign_ma, use_container_width=True, hide_index=True)

            col_r1, col_r2 = st.columns([3, 1])
            with col_r1:
                sku_ret_ma = st.selectbox("Sélectionner un SKU à réintégrer",
                    options=[""] + df_ign_ma["SKU"].tolist(),
                    format_func=lambda x: x if x else "Choisir un SKU…",
                    key="ank_ignores_retirer")
            with col_r2:
                st.write(""); st.write("")
                if sku_ret_ma and st.button("↩️ Réintégrer", type="secondary",
                                             key="btn_ank_reintegrer"):
                    delete("ankorstore_ignores", f"sku=eq.{sku_ret_ma}")
                    st.success(f"✓ {sku_ret_ma} réintégré !")
                    st.rerun()
        else:
            st.info("Aucun produit exclu.")
    else:
        st.info("Aucune donnée. Lance d'abord une synchronisation.")


elif page == "🔍 Vérification Ankorstore":
    st.subheader("🔍 Vérification Ankorstore")

    tab1_ank, tab2_ank, tab3_ank, tab4_ank = st.tabs([
        "✏️ Correction SKUs",
        "🔗 Mapping SKUs",
        "🔧 Correction lignes commandes",
        "💰 Vérification prix",
    ])

    # ── Onglet 1 : Correction SKUs ────────────────────────────────────────────
    with tab1_ank:
        ank_vars_t1    = select("produits_ankorstore_variants",
            "select=id_ankorstore,id_produit_ankorstore,sku,nom")
        skus_valides_t1 = {s["sku"] for s in (select("skus", "select=sku&statut=eq.visible") or [])}
        prod_ank_t1    = select("produits_ankorstore", "select=id_ankorstore,nom")
        prod_ank_map_t1 = {p["id_ankorstore"]: p["nom"] for p in (prod_ank_t1 or [])}

        if not ank_vars_t1:
            st.info("Aucun variant Ankorstore trouvé. Lance d'abord la sync Produits Ankorstore.")
        else:
            incorrects_t1 = [
                v for v in ank_vars_t1
                if not v.get("sku") or v.get("sku") not in skus_valides_t1
            ]
            col1, col2 = st.columns(2)
            with col1: st.metric("SKUs incorrects sur Ankorstore", len(incorrects_t1))
            with col2: st.metric("SKUs vides (NULL ou vide)",
                                 len([v for v in incorrects_t1 if not v.get("sku")]))

            if not incorrects_t1:
                st.success("✅ Tous les SKUs Ankorstore correspondent à des SKUs Wizishop valides !")
            else:
                df_edit_t1 = pd.DataFrame([{
                    "ID Ankorstore":         v.get("id_ankorstore", ""),
                    "ID Produit Ankorstore": v.get("id_produit_ankorstore", ""),
                    "Produit parent":        prod_ank_map_t1.get(
                                                v.get("id_produit_ankorstore", ""), v.get("nom", "")),
                    "Nom variant":           v.get("nom", ""),
                    "SKU actuel":            v.get("sku", ""),
                    "Nouveau SKU":           "",
                } for v in incorrects_t1])

                df_result_t1 = st.data_editor(
                    df_edit_t1,
                    column_config={
                        "ID Ankorstore":         st.column_config.TextColumn(disabled=True),
                        "ID Produit Ankorstore": st.column_config.TextColumn(disabled=True),
                        "Produit parent":        st.column_config.TextColumn(disabled=True),
                        "Nom variant":           st.column_config.TextColumn(disabled=True),
                        "SKU actuel":            st.column_config.TextColumn(disabled=True),
                        "Nouveau SKU":           st.column_config.TextColumn("Nouveau SKU"),
                    },
                    hide_index=True, use_container_width=True, key="editor_sku_ank"
                )

                a_corriger_t1 = df_result_t1[
                    df_result_t1["Nouveau SKU"].notna() &
                    (df_result_t1["Nouveau SKU"].str.strip() != "")
                ]
                st.caption(f"{len(a_corriger_t1)} correction(s) à appliquer.")

                if st.button("Mettre à jour dans Supabase", type="primary",
                             disabled=len(a_corriger_t1) == 0, key="btn_corriger_skus_ank"):
                    succes_t1, erreurs_t1 = 0, 0
                    for _, row in a_corriger_t1.iterrows():
                        ok = update("produits_ankorstore_variants",
                                    f"id_ankorstore=eq.{row['ID Ankorstore']}",
                                    {"sku": row["Nouveau SKU"].strip()})
                        if ok:
                            succes_t1 += 1
                        else:
                            erreurs_t1 += 1
                    if succes_t1:
                        st.success(f"✅ {succes_t1} variant(s) corrigé(s).")
                        st.info("💡 Relance la sync Produits Ankorstore pour mettre à jour le catalogue.")
                    if erreurs_t1:
                        st.warning(f"⚠️ {erreurs_t1} erreur(s).")

    # ── Onglet 2 : Mapping SKUs ───────────────────────────────────────────────
    with tab2_ank:
        st.caption("Associe les anciens SKUs Ankorstore aux SKUs Wizishop corrects.")

        cmds_ank_t2   = select("commandes",
            "select=id_ankorstore&source=eq.ankorstore&statut_texte=not.eq.cancelled")
        skus_val_t2   = {s["sku"] for s in (select("skus", "select=sku&statut=eq.visible") or [])}
        mapping_ank   = select("sku_mapping_ankorstore",
            "select=sku_ankorstore,sku_wizishop&order=sku_ankorstore.asc")
        ank_vars_t2   = select("produits_ankorstore_variants",
            "select=sku,nom,id_produit_ankorstore")
        prod_ank_t2   = select("produits_ankorstore", "select=id_ankorstore,nom")

        variant_map_t2   = {v["sku"]: v for v in (ank_vars_t2 or []) if v.get("sku")}
        prod_ank_map_t2  = {p["id_ankorstore"]: p["nom"] for p in (prod_ank_t2 or [])}
        mapping_connu_t2 = {m["sku_ankorstore"] for m in (mapping_ank or [])}

        skus_inconnus_t2 = {}
        if cmds_ank_t2:
            ids_t2   = ",".join(c["id_ankorstore"] for c in cmds_ank_t2 if c.get("id_ankorstore"))
            lignes_t2 = select("lignes_commande",
                f"select=sku&source=eq.ankorstore&id_commande=in.({ids_t2})", limit=50000)
            for l in (lignes_t2 or []):
                sku = l.get("sku") or ""
                if sku and sku not in skus_val_t2:
                    skus_inconnus_t2[sku] = skus_inconnus_t2.get(sku, 0) + 1

        skus_a_mapper_t2 = {s: n for s, n in skus_inconnus_t2.items()
                            if s not in mapping_connu_t2}

        col1, col2, col3 = st.columns(3)
        with col1: st.metric("SKUs inconnus", len(skus_inconnus_t2))
        with col2: st.metric("Déjà mappés", len(mapping_connu_t2))
        with col3: st.metric("Restant à mapper", len(skus_a_mapper_t2))

        if skus_a_mapper_t2:
            st.divider()
            st.subheader("SKUs à mapper")
            df_edit_t2 = pd.DataFrame([{
                "SKU Ankorstore":      sku,
                "Nb commandes":        nb,
                "Nom produit":         prod_ank_map_t2.get(
                    (variant_map_t2.get(sku) or {}).get("id_produit_ankorstore", ""), ""),
                "Nom variant":         (variant_map_t2.get(sku) or {}).get("nom", ""),
                "Nouveau SKU Wizishop": "",
            } for sku, nb in sorted(skus_a_mapper_t2.items(), key=lambda x: -x[1])])

            df_result_t2 = st.data_editor(df_edit_t2,
                column_config={
                    "SKU Ankorstore":       st.column_config.TextColumn(disabled=True),
                    "Nb commandes":         st.column_config.NumberColumn(disabled=True),
                    "Nom produit":          st.column_config.TextColumn(disabled=True),
                    "Nom variant":          st.column_config.TextColumn(disabled=True),
                    "Nouveau SKU Wizishop": st.column_config.TextColumn("Nouveau SKU Wizishop"),
                },
                hide_index=True, use_container_width=True, key="editor_mapping_ank")

            lignes_t2_remplies = df_result_t2[
                df_result_t2["Nouveau SKU Wizishop"].notna() &
                (df_result_t2["Nouveau SKU Wizishop"].str.strip() != "")
            ]
            st.caption(f"{len(lignes_t2_remplies)} mapping(s) à enregistrer.")

            if st.button("Enregistrer le mapping", type="primary",
                         disabled=len(lignes_t2_remplies) == 0, key="btn_enreg_mapping_ank"):
                payload_t2 = [
                    {"sku_ankorstore": row["SKU Ankorstore"],
                     "sku_wizishop":   row["Nouveau SKU Wizishop"].strip()}
                    for _, row in lignes_t2_remplies.iterrows()
                ]
                if upsert("sku_mapping_ankorstore", payload_t2, "sku_ankorstore"):
                    st.success(f"✅ {len(payload_t2)} mapping(s) enregistré(s).")
                    st.rerun()
                else:
                    st.error("Erreur lors de l'enregistrement.")
        else:
            st.success("✅ Tous les SKUs inconnus sont déjà mappés.")

        if mapping_ank:
            st.divider()
            st.subheader("Mappings enregistrés")
            df_mapping_ank = pd.DataFrame(mapping_ank)[["sku_ankorstore", "sku_wizishop"]].rename(
                columns={"sku_ankorstore": "SKU Ankorstore", "sku_wizishop": "SKU Wizishop"})
            df_mapping_ank_edit = st.data_editor(df_mapping_ank,
                column_config={
                    "SKU Ankorstore": st.column_config.TextColumn(disabled=True),
                    "SKU Wizishop":   st.column_config.TextColumn("SKU Wizishop"),
                },
                hide_index=True, use_container_width=True, key="editor_mapping_ank_existant")
            if st.button("Mettre à jour les mappings", type="secondary",
                         key="btn_maj_mapping_ank"):
                payload_maj = [
                    {"sku_ankorstore": row["SKU Ankorstore"],
                     "sku_wizishop":   row["SKU Wizishop"].strip()}
                    for _, row in df_mapping_ank_edit.iterrows()
                    if row["SKU Wizishop"].strip()
                ]
                if upsert("sku_mapping_ankorstore", payload_maj, "sku_ankorstore"):
                    st.success(f"✅ {len(payload_maj)} mapping(s) mis à jour.")
                    st.rerun()
                else:
                    st.error("Erreur lors de la mise à jour.")

    # ── Onglet 3 : Correction lignes commandes ────────────────────────────────
    with tab3_ank:
        cmds_ank_t3   = select("commandes",
            "select=id_ankorstore,date_commande,montant_ttc"
            "&source=eq.ankorstore&statut_texte=not.eq.cancelled"
            "&order=date_commande.desc")
        skus_val_t3   = {s["sku"] for s in (select("skus", "select=sku&statut=eq.visible") or [])}

        if not cmds_ank_t3:
            st.info("Aucune commande Ankorstore.")
        else:
            ids_t3     = [c["id_ankorstore"] for c in cmds_ank_t3 if c.get("id_ankorstore")]
            ids_str_t3 = ",".join(ids_t3)
            all_lignes_t3 = select("lignes_commande",
                f"select=id_commande,sku&source=eq.ankorstore&id_commande=in.({ids_str_t3})",
                limit=50000)

            cmds_pb_t3, nb_lig_pb_t3 = set(), 0
            for l in (all_lignes_t3 or []):
                sku = l.get("sku") or ""
                if not sku or sku not in skus_val_t3:
                    cmds_pb_t3.add(str(l.get("id_commande", "")))
                    nb_lig_pb_t3 += 1

            col1, col2 = st.columns(2)
            with col1: st.metric("Commandes avec lignes à corriger", len(cmds_pb_t3))
            with col2: st.metric("Total lignes à corriger", nb_lig_pb_t3)

            st.divider()

            def _fmt_cmd_ank(id_ank):
                if not id_ank:
                    return "Choisir une commande..."
                cmd = next((c for c in cmds_ank_t3 if c["id_ankorstore"] == id_ank), {})
                try:
                    date = pd.to_datetime(cmd.get("date_commande", "")).strftime("%d/%m/%Y")
                except Exception:
                    date = "?"
                montant = float(cmd.get("montant_ttc") or 0)
                alerte  = "⚠️ " if id_ank in cmds_pb_t3 else ""
                return f"{alerte}{date} — {montant:.2f}€ — {id_ank[:8]}…"

            cmd_choisie_t3 = st.selectbox("Commande", options=[""] + ids_t3,
                format_func=_fmt_cmd_ank, key="sel_cmd_corr_ank")

            if cmd_choisie_t3:
                lignes_cmd_t3 = select("lignes_commande",
                    f"select=id,id_ankorstore,nom_produit,quantite,prix_unitaire_ttc,sku"
                    f"&source=eq.ankorstore&id_commande=eq.{cmd_choisie_t3}")

                if lignes_cmd_t3:
                    ids_lig_t3  = [row["id"] for row in lignes_cmd_t3]
                    orig_skus_t3 = [row.get("sku") or "" for row in lignes_cmd_t3]

                    df_edit_t3 = pd.DataFrame([{
                        "Statut SKU": ("✅" if (row.get("sku") and row["sku"] in skus_val_t3)
                                       else ("❌" if not row.get("sku") else "⚠️")),
                        "Produit":    row.get("nom_produit", ""),
                        "Qté":        row.get("quantite", 0),
                        "Prix TTC":   float(row.get("prix_unitaire_ttc") or 0),
                        "SKU":        row.get("sku") or "",
                    } for row in lignes_cmd_t3])

                    df_result_t3 = st.data_editor(df_edit_t3,
                        column_config={
                            "Statut SKU": st.column_config.TextColumn("Statut", disabled=True),
                            "Produit":    st.column_config.TextColumn(disabled=True),
                            "Qté":        st.column_config.NumberColumn(disabled=True),
                            "Prix TTC":   st.column_config.NumberColumn(disabled=True),
                            "SKU":        st.column_config.TextColumn("SKU"),
                        },
                        hide_index=True, use_container_width=True,
                        key=f"editor_lignes_ank_{cmd_choisie_t3}")

                    edit_skus_t3 = df_result_t3["SKU"].tolist()
                    lignes_mod_t3 = [
                        {"id": ids_lig_t3[i], "sku": edit_skus_t3[i]}
                        for i in range(len(ids_lig_t3))
                        if orig_skus_t3[i] != edit_skus_t3[i]
                    ]
                    st.caption(f"{len(lignes_mod_t3)} ligne(s) modifiée(s).")

                    if st.button("Enregistrer les corrections", type="primary",
                                 disabled=len(lignes_mod_t3) == 0, key="btn_enreg_corr_ank"):
                        succes_t3 = 0
                        for l in lignes_mod_t3:
                            if update("lignes_commande", f"id=eq.{l['id']}",
                                      {"sku": l["sku"] or None}):
                                succes_t3 += 1
                        st.success(f"✅ {succes_t3} ligne(s) corrigée(s).")
                        st.rerun()
                else:
                    st.info("Aucune ligne pour cette commande.")

    # ── Onglet 4 : Vérification prix ──────────────────────────────────────────
    with tab4_ank:
        COEFF_GROSSISTE_ANK = 2.50
        SEUIL_ECART_ANK     = 2.0

        ank_vars_vp  = select("produits_ankorstore_variants",
            "select=sku,nom,prix_grossiste,id_produit_ankorstore&sku=not.is.null")
        prod_wizi_vp = select("produits", "select=sku,nom,prix_vente_ht")
        prod_map_vp  = {p["sku"]: p for p in (prod_wizi_vp or [])}
        vars_vp      = [v for v in (ank_vars_vp or []) if v.get("sku")]

        if not vars_vp:
            st.info("Aucun variant avec SKU. Lance d'abord la sync Produits Ankorstore.")
        else:
            rows_vp = []
            for v in vars_vp:
                sku            = v["sku"]
                prod_wizi      = get_prod_parent(sku, prod_map_vp)
                nom            = prod_wizi.get("nom", "") or v.get("nom", "") or sku
                prix_ank       = float(v.get("prix_grossiste") or 0)
                prix_wizi_ht   = float(prod_wizi.get("prix_vente_ht") or 0)
                prix_wizi_gros = round(prix_wizi_ht / COEFF_GROSSISTE_ANK, 2) if COEFF_GROSSISTE_ANK else 0
                ecart_pct      = (round((prix_ank - prix_wizi_gros) / prix_wizi_gros * 100, 1)
                                  if prix_ank > 0 and prix_wizi_gros > 0 else None)
                rows_vp.append({
                    "SKU": sku, "Nom": nom,
                    "Prix Ankorstore (€)":        prix_ank,
                    "Prix Wizishop grossiste (€)": prix_wizi_gros,
                    "Écart (%)": ecart_pct,
                })

            df_vp       = pd.DataFrame(rows_vp)
            df_vp_ecart = df_vp[df_vp["Écart (%)"].notna() &
                                 (df_vp["Écart (%)"].abs() > SEUIL_ECART_ANK)]

            col1, col2 = st.columns(2)
            with col1: st.metric("Total variants", len(df_vp))
            with col2: st.metric(f"Écart > {SEUIL_ECART_ANK}%", len(df_vp_ecart))

            afficher_tous_vp = st.checkbox("Afficher tous les variants", key="vp_ank_tous")
            df_disp = (df_vp.sort_values("SKU") if afficher_tous_vp
                       else df_vp_ecart.sort_values("Écart (%)",
                            ascending=False, key=lambda s: s.abs()))

            st.dataframe(df_disp.reset_index(drop=True), use_container_width=True, hide_index=True)
            st.download_button("📥 Exporter CSV",
                df_disp.to_csv(index=False).encode("utf-8"),
                "verification_prix_ankorstore.csv", "text/csv", key="dl_vp_ank")
