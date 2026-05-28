import streamlit as st
import pandas as pd
from supabase_api import select, upsert, insert, update, delete
from sync_database import (get_wizi_token, sync_categories, sync_marques,
                           sync_skus, sync_commandes, sync_produits, log_sync)
from sync_etsy import sync_etsy_commandes, log_sync_etsy
from sync_etsy_produits import sync_produits_etsy
from etsy_api import get_shop_id
from sync_faire import sync_faire_commandes, sync_faire_produits, log_sync_faire
import time
from datetime import datetime, timezone
from faire_api import api_get as faire_api_get, test_write_permission, api_patch as faire_api_patch

st.set_page_config(
    page_title="Pique&Pince — Dashboard",
    page_icon="📊",
    layout="wide"
)


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
        f"select=id_wizi&source=eq.wizishop&statut_code=not.in.(0,45,50)&date_commande=gte.{date_limite}")
    cmds_etsy = select("commandes",
        f"select=id_wizi&source=eq.etsy&statut_code=not.in.(0,45,50)&date_commande=gte.{date_limite}")
    cmds_faire = select("commandes",
        f"select=id_faire&source=eq.faire&statut_code=not.in.(0,45,50)&date_commande=gte.{date_limite}")

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

with st.sidebar:
    st.header("Navigation")
    page = st.radio("", [
        "📊 Vue d'ensemble",
        "📦 Commandes",
        "⭐ Best-sellers",
        "🚨 Réapprovisionnement",
        "🏭 Stock & Fournisseurs",
        "🏷️ Catalogue Etsy",
        "📊 Gestion stock Etsy",
        "🔎 Produits manquants sur Etsy",
        "🔎 Produits manquants sur Faire",
        "✏️ Correction SKUs Faire",
        "🔗 Mapping SKUs Faire",
        "🔧 Correction lignes commandes Faire",
        "💰 Vérification prix Faire",
        "🌍 Comptabilité TVA",
        "🔍 Vérification Wizishop",
        "🔍 Vérification Etsy",
        "🔍 Vérification Faire",
        "📒 Réconciliation Faire",
        "📊 Gestion stock Faire",
        "🔗 Connexion Faire",
        "🔄 Synchronisation"
    ])

if page == "🔄 Synchronisation":
    st.subheader("Synchronisation des données")
    col1, col2, col3 = st.columns(3)

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
        f"select=date_commande,montant_ttc,statut_code,source&statut_code=not.in.(0,45,50)&date_commande=gte.{date_limite_str}&order=date_commande.desc")

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
    query = f"select=date_commande,numero_commande,nom_facturation,prenom_facturation,montant_ttc,statut_texte,pays_facturation_iso,zone_tva,numero_suivi,source&date_commande=gte.{date_limite}&statut_code=not.in.(0,45,50)&order=date_commande.desc&limit=500"
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

elif page == "⭐ Best-sellers":
    with st.sidebar:
        st.divider()
        nb_mois = st.slider("Période (mois)", min_value=1, max_value=24, value=12)
        source_filtre = st.selectbox("Source", ["Toutes", "Wizishop", "Etsy"])

    date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")

    query_cmd = f"select=id_wizi,source&statut_code=not.in.(0,45,50)&date_commande=gte.{date_limite}"
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

            cols_editor = ["SKU", "Produit", "Fournisseur", "Stock",
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

elif page == "🏷️ Catalogue Etsy":
    st.subheader("🏷️ Catalogue Etsy")

    with st.sidebar:
        st.divider()
        vue = st.radio("Vue", [
            "📋 Catalogue complet",
            "🔴 Alertes stock",
            "⚠️ SKUs manquantes dans Wizishop"
        ])

    variations = select("produits_etsy_variations",
        "select=sku,variation_valeur,stock_etsy,stock_wizishop,is_enabled,alerte_stock,prix,listing_id")
    listings = select("produits_etsy", "select=listing_id,titre,url,has_variations,nb_favoris")
    listing_map = {l["listing_id"]: l for l in listings} if listings else {}
    skus_wizi = select("skus", "select=sku&statut=eq.visible")
    skus_wizi_set = {s["sku"] for s in skus_wizi} if skus_wizi else set()

    if variations:
        df = pd.DataFrame(variations)
        df["stock_etsy"] = pd.to_numeric(df["stock_etsy"], errors="coerce").fillna(0).astype(int)
        df["stock_wizishop"] = pd.to_numeric(df["stock_wizishop"], errors="coerce").fillna(0).astype(int)
        df["titre"] = df["listing_id"].map(
            lambda x: listing_map.get(x, {}).get("titre", "")[:60] if x else "")

        if vue == "📋 Catalogue complet":
            nb_alertes = len(df[df["alerte_stock"] == True])
            nb_actifs = len(df[df["is_enabled"] == True])
            nb_inactifs = len(df[df["is_enabled"] == False])

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("🔴 Alertes stock", nb_alertes)
            with col2:
                st.metric("✅ Variations actives", nb_actifs)
            with col3:
                st.metric("⏸️ Variations inactives", nb_inactifs)

            df["Alerte"] = df["alerte_stock"].map({True: "🔴 Stock 0", False: "🟢 OK"})
            df["Actif"] = df["is_enabled"].map({True: "✅", False: "⏸️"})
            df_show = df[["sku", "titre", "variation_valeur", "Actif",
                         "stock_wizishop", "stock_etsy", "prix", "Alerte"]].copy()
            df_show.columns = ["SKU", "Produit", "Variation", "Actif",
                               "Stock Wizishop", "Stock Etsy", "Prix (€)", "Alerte"]
            df_show = df_show.sort_values("Alerte", ascending=False)
            st.dataframe(df_show, use_container_width=True, hide_index=True)
            csv = df_show.to_csv(index=False).encode("utf-8")
            st.download_button("📥 Télécharger catalogue", csv, "catalogue_etsy.csv", "text/csv")

        elif vue == "🔴 Alertes stock":
            df_alertes = df[df["alerte_stock"] == True].copy()
            if not df_alertes.empty:
                st.warning(f"⚠️ {len(df_alertes)} variations actives avec stock Wizishop = 0 !")
                df_alertes["Actif"] = df_alertes["is_enabled"].map({True: "✅", False: "⏸️"})
                df_show = df_alertes[["sku", "titre", "variation_valeur", "Actif",
                                     "stock_wizishop", "stock_etsy", "prix"]].copy()
                df_show.columns = ["SKU", "Produit", "Variation", "Actif",
                                   "Stock Wizishop", "Stock Etsy", "Prix (€)"]
                st.dataframe(df_show, use_container_width=True, hide_index=True)
                csv = df_show.to_csv(index=False).encode("utf-8")
                st.download_button("📥 Télécharger alertes", csv, "alertes_stock_etsy.csv", "text/csv")
            else:
                st.success("✅ Aucune alerte stock !")

        elif vue == "⚠️ SKUs manquantes dans Wizishop":
            st.info("Liste des SKUs Etsy qui n'existent pas dans Wizishop — à corriger sur Etsy.")

            df["sku_existe_wizi"] = df["sku"].apply(
                lambda x: x in skus_wizi_set if x else False)
            df["sku_parent_existe"] = df["sku"].apply(
                lambda x: any(s.startswith(x) for s in skus_wizi_set) if x else False)
            df["statut_sku"] = df.apply(
                lambda r: "✅ SKU exact" if r["sku_existe_wizi"]
                else ("⚠️ SKU parent sans variation" if r["sku_parent_existe"]
                else "❌ Introuvable dans Wizishop"), axis=1)

            df_manquants = df[df["statut_sku"] != "✅ SKU exact"].copy()

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("✅ SKUs corrects", len(df[df["statut_sku"] == "✅ SKU exact"]))
            with col2:
                st.metric("⚠️ SKU parent sans variation",
                         len(df[df["statut_sku"] == "⚠️ SKU parent sans variation"]))
            with col3:
                st.metric("❌ Introuvable",
                         len(df[df["statut_sku"] == "❌ Introuvable dans Wizishop"]))

            df_show = df_manquants[["sku", "titre", "variation_valeur",
                                    "is_enabled", "statut_sku"]].copy()
            df_show["is_enabled"] = df_show["is_enabled"].map(
                {True: "✅ Actif", False: "⏸️ Inactif"})
            df_show.columns = ["SKU Etsy", "Produit", "Variation", "Statut listing", "Statut SKU"]
            df_show = df_show.sort_values("Statut SKU")
            st.dataframe(df_show, use_container_width=True, hide_index=True)
            csv = df_show.to_csv(index=False).encode("utf-8")
            st.download_button("📥 Télécharger liste à corriger",
                              csv, "skus_a_corriger_etsy.csv", "text/csv")
    else:
        st.info("Aucune donnée. Lance d'abord la sync 6️⃣ Produits Etsy.")

elif page == "📊 Gestion stock Etsy":
    st.subheader("📊 Gestion stock Etsy")

    with st.sidebar:
        st.divider()
        nb_mois = st.slider("Période ventes (mois)", min_value=1, max_value=12, value=3)
        seuil_jours = st.slider("Seuil alerte jours de stock", min_value=7, max_value=90, value=30)
        seuil_ecart_jours = st.slider("Seuil écart significatif (jours)", min_value=5, max_value=60, value=15)
        alerte_filtre = st.selectbox("Filtre alerte", [
            "Toutes", "🔴 Urgent uniquement", "🔴 + 🟡 Attention", "⚪ Info uniquement"
        ])
        statut_filtre = st.selectbox("Statut listing", [
            "Tous", "Actifs uniquement", "Inactifs uniquement"
        ])

    variations = select("produits_etsy_variations",
        "select=sku,stock_etsy,stock_wizishop,is_enabled,variation_valeur,listing_id")
    listings = select("produits_etsy", "select=listing_id,titre")
    skus_data = select("skus", "select=sku,stock")

    date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")
    commandes_valides = select("commandes",
        f"select=id_wizi,source&statut_code=not.in.(0,45,50)&date_commande=gte.{date_limite}")

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

            if is_enabled and ((stock_wizi == 0 and stock_etsy > 0 and v_total > 0) or
                               (jours_stock < seuil_jours and v_total > 0)):
                alerte = "🔴 URGENT"
                priorite = 1
            elif is_enabled and ((stock_etsy > stock_wizi and v_total > 0) or
                                 (ventes_par_jour > 0 and ecart > 0 and
                                  (ecart / ventes_par_jour) > seuil_ecart_jours)):
                alerte = "🟡 ATTENTION"
                priorite = 2
            elif not is_enabled and jours_stock > seuil_jours and v_total > 0:
                alerte = "🟡 ATTENTION"
                priorite = 2
            elif stock_etsy > stock_wizi and v_total == 0:
                alerte = "⚪ INFO"
                priorite = 3
            else:
                alerte = "🟢 OK"
                priorite = 4

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
                "Jours de stock": jours_stock,
                "Actif": "✅" if is_enabled else "⏸️",
                "Alerte": alerte,
                "_priorite": priorite
            })

        df = pd.DataFrame(rows)
        df = df[~((df["Stock Wizishop"] == 0) & (df["Stock Etsy"] == 0))]

        nb_urgent = len(df[df["Alerte"] == "🔴 URGENT"])
        nb_attention = len(df[df["Alerte"] == "🟡 ATTENTION"])
        nb_info = len(df[df["Alerte"] == "⚪ INFO"])
        nb_ok = len(df[df["Alerte"] == "🟢 OK"])

        col1, col2, col3, col4 = st.columns(4)
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
        elif alerte_filtre == "⚪ Info uniquement":
            df = df[df["Alerte"] == "⚪ INFO"]

        df = df.sort_values("_priorite").drop(columns=["_priorite"]).reset_index(drop=True)

        st.dataframe(df, use_container_width=True, hide_index=True)
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Télécharger en CSV", csv, "gestion_stock_etsy.csv", "text/csv")
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

    query = f"select=zone_tva,pays_facturation,pays_facturation_iso,montant_ttc,montant_ht,source&statut_code=not.in.(0,45,50)&date_commande=gte.{annee}-01-01&date_commande=lt.{annee+1}-01-01"
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

    with st.sidebar:
        st.divider()
        nb_mois = st.slider("Période (mois)", min_value=1, max_value=24, value=12)

    date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")
    commandes_wizi = select("commandes",
        f"select=id_wizi&statut_code=not.in.(0,45,50)&source=eq.wizishop&date_commande=gte.{date_limite}")

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

elif page == "🔍 Vérification Etsy":
    st.subheader("🔍 Vérification des ventes Etsy")

    with st.sidebar:
        st.divider()
        nb_mois = st.slider("Période (mois)", min_value=1, max_value=24, value=12)

    date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")
    commandes_etsy = select("commandes",
        f"select=id_wizi&statut_code=not.in.(0,45,50)&source=eq.etsy&date_commande=gte.{date_limite}")

    if commandes_etsy:
        ids = [str(c["id_wizi"]) for c in commandes_etsy]
        ids_str = ",".join(ids)

        lignes = select("lignes_commande",
            f"select=sku,sku_variation,nom_produit,quantite,prix_unitaire_ttc,id_commande&id_commande=in.({ids_str})",
            limit=50000)

        produits_wizi = select("produits", "select=sku,nom,nom_categorie")
        prod_wizi_map = {p["sku"]: p for p in produits_wizi} if produits_wizi else {}

        if lignes:
            df = pd.DataFrame(lignes)
            df["quantite"] = pd.to_numeric(df["quantite"], errors="coerce").fillna(0)
            df["prix_unitaire_ttc"] = pd.to_numeric(df["prix_unitaire_ttc"], errors="coerce").fillna(0)
            df["ca"] = df["quantite"] * df["prix_unitaire_ttc"]
            df["sku_effectif"] = df.apply(
                lambda r: r["sku_variation"] if r["sku_variation"] else r["sku"], axis=1)
            df["nom_affiche"] = df["sku_effectif"].map(
                lambda x: get_prod_parent(x, prod_wizi_map).get("nom", "") or "")
            df["nom_affiche"] = df.apply(
                lambda r: r["nom_affiche"] if r["nom_affiche"] else r["nom_produit"], axis=1)
            df["categorie"] = df["sku_effectif"].map(
                lambda x: get_prod_parent(x, prod_wizi_map).get("nom_categorie", "") or "")

            result = df.groupby(["sku_effectif", "nom_affiche", "categorie"]).agg(
                unites_vendues=("quantite", "sum"),
                ca_total=("ca", "sum"),
                nb_commandes=("id_commande", "nunique")
            ).reset_index().sort_values("unites_vendues", ascending=False)

            result.columns = ["SKU", "Produit", "Catégorie",
                             "Unités vendues", "CA (€)", "Nb commandes"]
            result["CA (€)"] = result["CA (€)"].apply(lambda x: f"{x:.2f}")

            st.info(f"{len(result)} produits vendus sur Etsy sur les {nb_mois} derniers mois")
            st.dataframe(result, use_container_width=True, hide_index=True)
            csv = result.to_csv(index=False).encode("utf-8")
            st.download_button("Télécharger en CSV", csv, "verification_etsy.csv", "text/csv")
        else:
            st.info("Aucune ligne de commande trouvée.")
    else:
        st.info("Aucune commande Etsy trouvée.")

elif page == "🔎 Produits manquants sur Faire":
    st.subheader("🔎 Produits manquants sur Faire")

    with st.sidebar:
        st.divider()
        fournisseurs_fixes = ["Tous", "VEINIERE", "NPC", "NPGL", "NAVARRO", "BAVOUX", "DELORME"]
        fournisseur_filtre = st.selectbox("Fournisseur", fournisseurs_fixes)

    skus_data = select("skus", "select=sku,stock&statut=eq.visible")
    faire_variants = select("produits_faire_variants", "select=sku")
    produits_data = select("produits",
        "select=sku,nom,fournisseur,nom_categorie,prix_vente_ht,reference_fournisseur")

    if skus_data:
        skus_wizi = {s["sku"] for s in skus_data}
        skus_faire = {v["sku"] for v in faire_variants if v.get("sku")} if faire_variants else set()
        prod_map = {p["sku"]: p for p in produits_data} if produits_data else {}
        sku_stock = {s["sku"]: int(s["stock"] or 0) for s in skus_data}

        rows = []
        for sku in skus_wizi - skus_faire:
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
            st.metric("Total SKUs manquants sur Faire", total_manquants)
        with col2:
            label = f"Manquants — {fournisseur_filtre}" if fournisseur_filtre != "Tous" else "Manquants (tous fournisseurs)"
            st.metric(label, nb_filtre)

        if not df_filtre.empty:
            df_show = df_filtre.sort_values("SKU").reset_index(drop=True)
            st.dataframe(df_show, use_container_width=True, hide_index=True)
            csv = df_show.to_csv(index=False).encode("utf-8")
            st.download_button("📥 Télécharger en CSV", csv, "produits_manquants_faire.csv", "text/csv")
        else:
            st.info("Aucun SKU manquant pour ce filtre." if fournisseur_filtre != "Tous"
                    else "Tous les SKUs Wizishop sont présents sur Faire.")
    else:
        st.info("Aucune donnée. Lance d'abord une synchronisation.")

elif page == "✏️ Correction SKUs Faire":
    st.subheader("✏️ Correction SKUs Faire")

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
        nb_total = len(incorrects)
        nb_vides = len([v for v in incorrects if not v.get("sku")])

        col1, col2 = st.columns(2)
        with col1:
            st.metric("SKUs incorrects sur Faire", nb_total)
        with col2:
            st.metric("SKUs vides (NULL ou vide)", nb_vides)

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
                hide_index=True,
                use_container_width=True,
                key="editor_sku_faire"
            )

            lignes_a_corriger = df_result[
                df_result["Nouveau SKU"].notna() & (df_result["Nouveau SKU"].str.strip() != "")
            ]
            st.caption(f"{len(lignes_a_corriger)} correction(s) à appliquer "
                       f"sur {lignes_a_corriger['ID Produit Faire'].nunique()} produit(s).")

            if st.button("Mettre à jour sur Faire", type="primary",
                         disabled=len(lignes_a_corriger) == 0):
                progress = st.progress(0)
                # Grouper par produit pour éviter les 429
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
                        r = faire_api_patch(
                            f"/products/{product_id}",
                            {"variants": variants_payload}
                        )
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
                if erreurs > 0:
                    st.warning(f"⚠️ {erreurs} variant(s) en erreur.")
                if succes > 0:
                    st.info("💡 Relance la **8️⃣ Sync Produits Faire** depuis la page "
                            "🔄 Synchronisation pour mettre à jour la base.")

elif page == "🔗 Mapping SKUs Faire":
    st.subheader("🔗 Mapping SKUs Faire")
    st.caption("Associe les anciens SKUs Faire aux SKUs Wizishop corrects pour la réconciliation.")

    # Commandes Faire depuis le 1er janvier 2026
    commandes_faire = select("commandes",
        "select=id_faire&source=eq.faire&statut_code=not.in.(0,45,50)"
        "&date_commande=gte.2026-01-01")
    skus_data = select("skus", "select=sku&statut=eq.visible")
    mapping_existant = select("sku_mapping_faire", "select=id,sku_faire,sku_wizishop&order=sku_faire.asc")
    faire_variants_data = select("produits_faire_variants", "select=sku,nom,id_produit_faire")
    produits_faire_data = select("produits_faire", "select=id_faire,nom")

    variant_map = {v["sku"]: v for v in faire_variants_data if v.get("sku")} if faire_variants_data else {}
    produits_faire_map = {p["id_faire"]: p["nom"] for p in produits_faire_data} if produits_faire_data else {}

    skus_valides = {s["sku"] for s in skus_data} if skus_data else set()
    mapping_connu = {m["sku_faire"] for m in mapping_existant} if mapping_existant else set()

    skus_inconnus = {}  # sku_faire → nb occurrences
    if commandes_faire:
        ids_faire = [str(c["id_faire"]) for c in commandes_faire if c.get("id_faire")]
        ids_str = ",".join(ids_faire)
        lignes = select("lignes_commande",
            f"select=sku&id_commande=in.({ids_str})", limit=50000)
        if lignes:
            for ligne in lignes:
                sku = ligne.get("sku") or ""
                if sku and sku not in skus_valides:
                    skus_inconnus[sku] = skus_inconnus.get(sku, 0) + 1

    # Exclure les SKUs déjà mappés
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

        df_edit = pd.DataFrame([{
            "SKU Faire": sku,
            "Nb commandes": nb,
            "Nom produit": produits_faire_map.get(
                (variant_map.get(sku) or {}).get("id_produit_faire", ""), ""),
            "Nom variant": (variant_map.get(sku) or {}).get("nom", ""),
            "Nouveau SKU Wizishop": "",
        } for sku, nb in sorted(skus_a_mapper.items(), key=lambda x: -x[1])])

        df_result = st.data_editor(
            df_edit,
            column_config={
                "SKU Faire": st.column_config.TextColumn("SKU Faire", disabled=True),
                "Nb commandes": st.column_config.NumberColumn("Nb commandes", disabled=True),
                "Nom produit": st.column_config.TextColumn("Nom produit", disabled=True),
                "Nom variant": st.column_config.TextColumn("Nom variant", disabled=True),
                "Nouveau SKU Wizishop": st.column_config.TextColumn("Nouveau SKU Wizishop"),
            },
            hide_index=True,
            use_container_width=True,
            key="editor_mapping_faire"
        )

        lignes_remplies = df_result[
            df_result["Nouveau SKU Wizishop"].notna() &
            (df_result["Nouveau SKU Wizishop"].str.strip() != "")
        ]
        st.caption(f"{len(lignes_remplies)} mapping(s) à enregistrer.")

        if st.button("Enregistrer le mapping", type="primary",
                     disabled=len(lignes_remplies) == 0):
            payload = [
                {"sku_faire": row["SKU Faire"], "sku_wizishop": row["Nouveau SKU Wizishop"].strip()}
                for _, row in lignes_remplies.iterrows()
            ]
            ok = upsert("sku_mapping_faire", payload, "sku_faire")
            if ok:
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
                "sku_faire": "SKU Faire",
                "sku_wizishop": "SKU Wizishop"
            }),
            column_config={
                "SKU Faire": st.column_config.TextColumn("SKU Faire", disabled=True),
                "SKU Wizishop": st.column_config.TextColumn("SKU Wizishop"),
            },
            hide_index=True,
            use_container_width=True,
            key="editor_mapping_existant"
        )

        if st.button("Mettre à jour les mappings", type="secondary"):
            payload = [
                {"sku_faire": row["SKU Faire"], "sku_wizishop": row["SKU Wizishop"].strip()}
                for _, row in df_mapping_edit.iterrows()
                if row["SKU Wizishop"].strip()
            ]
            ok = upsert("sku_mapping_faire", payload, "sku_faire")
            if ok:
                st.success(f"✅ {len(payload)} mapping(s) mis à jour.")
                st.rerun()
            else:
                st.error("Erreur lors de la mise à jour.")

elif page == "🔧 Correction lignes commandes Faire":
    st.subheader("🔧 Correction lignes commandes Faire")

    commandes_faire = select("commandes",
        "select=id_faire,date_commande,nom_facturation,montant_ttc"
        "&source=eq.faire&statut_code=not.in.(0,45,50)"
        "&date_commande=gte.2026-01-01&order=date_commande.desc")
    skus_data = select("skus", "select=sku&statut=eq.visible")
    skus_valides = {s["sku"] for s in skus_data} if skus_data else set()

    if not commandes_faire:
        st.info("Aucune commande Faire depuis le 1er janvier 2026.")
    else:
        ids_faire = [str(c["id_faire"]) for c in commandes_faire if c.get("id_faire")]
        ids_str = ",".join(ids_faire)

        all_lignes = select("lignes_commande",
            f"select=id_commande,sku&id_commande=in.({ids_str})", limit=50000)

        cmds_avec_probleme = set()
        nb_lignes_a_corriger = 0
        if all_lignes:
            for ligne in all_lignes:
                sku = ligne.get("sku") or ""
                id_cmd = str(ligne.get("id_commande", ""))
                if not sku or sku not in skus_valides:
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
            cmd = next((c for c in commandes_faire if c["id_faire"] == id_faire), {})
            try:
                date = pd.to_datetime(cmd.get("date_commande", "")).strftime("%d/%m/%Y")
            except Exception:
                date = "?"
            client = cmd.get("nom_facturation", "")
            montant = float(cmd.get("montant_ttc") or 0)
            alerte = "⚠️ " if id_faire in cmds_avec_probleme else ""
            return f"{alerte}{date} — {client} — {montant:.2f}€"

        commande_choisie = st.selectbox(
            "Commande",
            options=[""] + ids_faire,
            format_func=format_cmd
        )

        if commande_choisie:
            lignes_cmd = select("lignes_commande",
                f"select=id,nom_produit,libelle_variation,quantite,prix_unitaire_ttc,sku"
                f"&id_commande=eq.{commande_choisie}")

            if lignes_cmd:
                ids_lignes = [row["id"] for row in lignes_cmd]
                orig_skus = [row.get("sku") or "" for row in lignes_cmd]

                df_edit = pd.DataFrame([{
                    "Statut SKU": ("✅" if (row.get("sku") and row["sku"] in skus_valides)
                                   else ("❌" if not row.get("sku") else "⚠️")),
                    "Produit": row.get("nom_produit", ""),
                    "Variation": row.get("libelle_variation", "") or "",
                    "Qté": row.get("quantite", 0),
                    "Prix TTC": float(row.get("prix_unitaire_ttc") or 0),
                    "SKU": row.get("sku") or "",
                } for row in lignes_cmd])

                df_result = st.data_editor(
                    df_edit,
                    column_config={
                        "Statut SKU": st.column_config.TextColumn("Statut", disabled=True),
                        "Produit": st.column_config.TextColumn("Produit", disabled=True),
                        "Variation": st.column_config.TextColumn("Variation", disabled=True),
                        "Qté": st.column_config.NumberColumn("Qté", disabled=True),
                        "Prix TTC": st.column_config.NumberColumn("Prix TTC", disabled=True),
                        "SKU": st.column_config.TextColumn("SKU"),
                    },
                    hide_index=True,
                    use_container_width=True,
                    key=f"editor_lignes_{commande_choisie}"
                )

                edit_skus = df_result["SKU"].tolist()
                lignes_modifiees = [
                    {"id": ids_lignes[i], "sku": edit_skus[i]}
                    for i in range(len(ids_lignes))
                    if orig_skus[i] != edit_skus[i]
                ]
                st.caption(f"{len(lignes_modifiees)} ligne(s) modifiée(s).")

                if st.button("Enregistrer les corrections", type="primary",
                             disabled=len(lignes_modifiees) == 0):
                    succes = 0
                    for ligne in lignes_modifiees:
                        ok = update("lignes_commande",
                                    f"id=eq.{ligne['id']}",
                                    {"sku": ligne["sku"] or None})
                        if ok:
                            succes += 1
                    st.success(f"✅ {succes} ligne(s) corrigée(s).")
                    st.rerun()
            else:
                st.info("Aucune ligne pour cette commande.")

elif page == "💰 Vérification prix Faire":
    st.subheader("💰 Vérification prix Faire")

    with st.sidebar:
        st.divider()
        filtre = st.selectbox("Filtre", [
            "Tous",
            "Écart prix vente",
            "Coefficient anormal",
            "Sans prix conseillé"
        ])

    faire_variants = select("produits_faire_variants",
        "select=id_faire,sku,nom,prix_grossiste,prix_vente_conseille,sale_state,lifecycle_state")
    produits_data = select("produits", "select=sku,nom,prix_vente_ht")
    prod_map = {p["sku"]: p for p in produits_data} if produits_data else {}

    variants_avec_sku = [v for v in faire_variants if v.get("sku")] if faire_variants else []

    if not variants_avec_sku:
        st.info("Aucun variant avec SKU. Lance d'abord la sync 8️⃣ Produits Faire.")
    else:
        COEFF_CIBLE = 2.50
        rows = []
        for v in variants_avec_sku:
            sku = v["sku"]
            prod_wizi = get_prod_parent(sku, prod_map)
            nom = prod_wizi.get("nom", "") or v.get("nom", "") or sku
            prix_wizi_ttc = round(float(prod_wizi.get("prix_vente_ht") or 0) * 1.20, 2)
            prix_conseille = float(v.get("prix_vente_conseille") or 0)
            prix_grossiste = float(v.get("prix_grossiste") or 0)
            ecart_prix = round(prix_conseille - prix_wizi_ttc, 2)
            coeff = round(prix_conseille / prix_grossiste, 2) if prix_grossiste else 0
            ecart_coeff = round(coeff - COEFF_CIBLE, 2) if coeff else 0

            rows.append({
                "SKU": sku,
                "Produit": nom,
                "Prix vente Wizi TTC": prix_wizi_ttc,
                "Prix conseillé Faire": prix_conseille,
                "Écart prix vente": ecart_prix,
                "Prix revendeur Faire": prix_grossiste,
                "Coefficient": coeff,
                "Coefficient cible": COEFF_CIBLE,
                "Écart coefficient": ecart_coeff,
            })

        df = pd.DataFrame(rows).sort_values("SKU").reset_index(drop=True)

        nb_ecart_prix = len(df[df["Écart prix vente"].abs() > 0.05])
        nb_coeff_anormal = len(df[(df["Coefficient"] > 0) & ((df["Coefficient"] < 2.40) | (df["Coefficient"] > 2.60))])
        nb_sans_prix = len(df[df["Prix conseillé Faire"] == 0])

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Écart prix vente > 0.05€", nb_ecart_prix)
        with col2:
            st.metric("Coefficient anormal (< 2.40 ou > 2.60)", nb_coeff_anormal)
        with col3:
            st.metric("Sans prix conseillé", nb_sans_prix)

        if filtre == "Écart prix vente":
            df = df[df["Écart prix vente"].abs() > 0.05]
        elif filtre == "Coefficient anormal":
            df = df[(df["Coefficient"] > 0) & ((df["Coefficient"] < 2.40) | (df["Coefficient"] > 2.60))]
        elif filtre == "Sans prix conseillé":
            df = df[df["Prix conseillé Faire"] == 0]

        def color_ecart_prix(val):
            return "color: red" if abs(val) > 0.05 else ""

        def color_ecart_coeff(val):
            return "color: red" if val != 0 and (val < -0.10 or val > 0.10) else ""

        st.divider()
        styled = df.style.map(color_ecart_prix, subset=["Écart prix vente"]) \
                         .map(color_ecart_coeff, subset=["Écart coefficient"])
        st.dataframe(styled, use_container_width=True, hide_index=True)

        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Télécharger en CSV", csv, "verification_prix_faire.csv", "text/csv")

elif page == "🔍 Vérification Faire":
    st.subheader("🔍 Vérification des ventes Faire")

    with st.sidebar:
        st.divider()
        nb_mois = st.slider("Période (mois)", min_value=1, max_value=24, value=12)

    date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")
    commandes_faire = select("commandes",
        f"select=id_faire&statut_code=not.in.(0,45,50)&source=eq.faire&date_commande=gte.{date_limite}")

    if commandes_faire:
        ids = [str(c["id_faire"]) for c in commandes_faire if c.get("id_faire")]
        ids_str = ",".join(ids)

        lignes = select("lignes_commande",
            f"select=sku,nom_produit,quantite,prix_unitaire_ttc,id_commande&id_commande=in.({ids_str})",
            limit=50000)

        produits_data = select("produits", "select=sku,nom,nom_categorie")
        prod_map = {p["sku"]: p for p in produits_data} if produits_data else {}
        mapping_data = select("sku_mapping_faire", "select=sku_faire,sku_wizishop")
        sku_mapping = {m["sku_faire"]: m["sku_wizishop"] for m in mapping_data} if mapping_data else {}

        if lignes:
            df = pd.DataFrame(lignes)
            df["quantite"] = pd.to_numeric(df["quantite"], errors="coerce").fillna(0)
            df["prix_unitaire_ttc"] = pd.to_numeric(df["prix_unitaire_ttc"], errors="coerce").fillna(0)
            df["ca"] = df["quantite"] * df["prix_unitaire_ttc"]
            df["sku_resolu"] = df["sku"].map(lambda x: sku_mapping.get(x, x) if x else x)
            df["nom_affiche"] = df["sku_resolu"].map(
                lambda x: get_prod_parent(x, prod_map).get("nom", "") or "")
            df["nom_affiche"] = df.apply(
                lambda r: r["nom_affiche"] if r["nom_affiche"] else r["nom_produit"], axis=1)
            df["categorie"] = df["sku_resolu"].map(
                lambda x: get_prod_parent(x, prod_map).get("nom_categorie", "") or "")

            result = df.groupby(["sku_resolu", "nom_affiche", "categorie"]).agg(
                unites_vendues=("quantite", "sum"),
                ca_total=("ca", "sum"),
                nb_commandes=("id_commande", "nunique")
            ).reset_index().sort_values("unites_vendues", ascending=False)

            result.columns = ["SKU", "Produit", "Catégorie",
                              "Unités vendues", "CA (€)", "Nb commandes"]
            result["CA (€)"] = result["CA (€)"].apply(lambda x: f"{x:.2f}")

            st.info(f"{len(result)} produits vendus sur Faire sur les {nb_mois} derniers mois")
            st.dataframe(result, use_container_width=True, hide_index=True)
            csv = result.to_csv(index=False).encode("utf-8")
            st.download_button("Télécharger en CSV", csv, "verification_faire.csv", "text/csv")
        else:
            st.info("Aucune ligne de commande trouvée.")
    else:
        st.info("Aucune commande Faire trouvée.")

elif page == "📒 Réconciliation Faire":
    st.subheader("📒 Réconciliation Faire")

    with st.sidebar:
        st.divider()
        annee = st.selectbox("Année", [2026, 2025, 2024], index=0)

    commandes_faire = select("commandes",
        f"select=id_faire,date_commande,nom_facturation,prenom_facturation,"
        f"montant_ttc,frais_port,tva_client,montant_net_recu,frais_expedition_faire,commission_faire"
        f"&source=eq.faire&statut_code=not.in.(0,45,50)"
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
        f"select=id_faire&source=eq.faire&statut_code=not.in.(0,45,50)&date_commande=gte.{date_limite}")

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
            is_active = v.get("sale_state") == "FOR_SALE" and v.get("lifecycle_state") == "PUBLISHED"
            stock_faire = int(v.get("available_quantity") or 0)
            stock_wizi = sku_stock_wizi.get(sku, 0)
            ecart = stock_wizi - stock_faire

            v_faire = round(ventes_faire.get(sku, 0) / nb_mois, 1)
            ventes_par_jour = v_faire / 30
            jours_stock = round(stock_wizi / ventes_par_jour) if ventes_par_jour > 0 else 999

            if is_active and ((stock_wizi == 0 and stock_faire > 0 and v_faire > 0) or
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
        df = df[~((df["Stock Wizishop"] == 0) & (df["Stock Faire"] == 0))]

        nb_urgent = len(df[df["Alerte"] == "🔴 URGENT"])
        nb_attention = len(df[df["Alerte"] == "🟡 ATTENTION"])
        nb_info = len(df[df["Alerte"] == "⚪ INFO"])
        nb_ok = len(df[df["Alerte"] == "🟢 OK"])

        col1, col2, col3, col4 = st.columns(4)
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

elif page == "🔗 Connexion Faire":
    st.subheader("🔗 Connexion Faire")

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
