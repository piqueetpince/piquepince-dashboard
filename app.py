import streamlit as st
import pandas as pd
from supabase_api import select
from sync_database import (get_wizi_token, sync_categories, sync_marques,
                           sync_skus, sync_commandes, log_sync)
from sync_etsy import sync_etsy_commandes, log_sync_etsy
from etsy_api import get_shop_id
import time

st.set_page_config(
    page_title="Pique&Pince — Dashboard",
    page_icon="📊",
    layout="wide"
)

st.title("Pique&Pince — Dashboard ventes")

with st.sidebar:
    st.header("Navigation")
    page = st.radio("", [
        "📊 Vue d'ensemble",
        "📦 Commandes",
        "⭐ Best-sellers",
        "🚨 Réapprovisionnement",
        "🏭 Stock & Fournisseurs",
        "🌍 Comptabilité TVA",
        "🔄 Synchronisation"
    ])

if page == "🔄 Synchronisation":
    st.subheader("Synchronisation des données")

    col1, col2 = st.columns(2)

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

    with col2:
        st.subheader("🏷️ Etsy")

        if st.button("4️⃣ Sync Commandes Etsy", use_container_width=True):
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

        st.info("💡 L'access token Etsy expire toutes les heures. Si erreur, relance la sync.")

    logs = select("sync_log", "select=table_name,source,nb_enregistrements,statut,created_at&order=created_at.desc&limit=15")
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
        source_filtre = st.selectbox("Source", ["Toutes", "Wizishop", "Etsy"])

    date_limite_str = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")
    query = f"select=date_commande,montant_ttc,montant_ht,statut_code,source&statut_code=not.in.(0,50)&date_commande=gte.{date_limite_str}&order=date_commande.desc"
    if source_filtre == "Wizishop":
        query += "&source=eq.wizishop"
    elif source_filtre == "Etsy":
        query += "&source=eq.etsy"

    commandes = select("commandes", query)

    if commandes:
        df = pd.DataFrame(commandes)
        df["date_commande"] = pd.to_datetime(df["date_commande"]).dt.tz_convert(None)
        df["montant_ttc"] = pd.to_numeric(df["montant_ttc"], errors="coerce").fillna(0)
        df["montant_ht"] = pd.to_numeric(df["montant_ht"], errors="coerce").fillna(0)
        df["mois"] = df["date_commande"].dt.strftime("%Y-%m")

        mois_max = df["mois"].max()
        df_mois = df[df["mois"] == mois_max]

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
        par_mois = df.groupby("mois").agg(
            Commandes=("montant_ttc", "count"),
            CA=("montant_ttc", "sum")
        ).reset_index().sort_values("mois")

        col_g1, col_g2 = st.columns(2)
        with col_g1:
            st.subheader("Commandes par mois")
            st.bar_chart(par_mois.set_index("mois")["Commandes"])
        with col_g2:
            st.subheader("CA par mois (€)")
            st.bar_chart(par_mois.set_index("mois")["CA"])
    else:
        st.info("Aucune donnée. Lance d'abord une synchronisation depuis le menu 🔄.")

elif page == "📦 Commandes":
    with st.sidebar:
        st.divider()
        nb_mois = st.slider("Période (mois)", min_value=1, max_value=24, value=3)
        source_filtre = st.selectbox("Source", ["Toutes", "Wizishop", "Etsy"])

    date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")
    query = f"select=date_commande,numero_commande,nom_facturation,prenom_facturation,montant_ttc,statut_texte,pays_facturation_iso,zone_tva,numero_suivi,source&date_commande=gte.{date_limite}&statut_code=not.in.(0,50)&order=date_commande.desc&limit=500"
    if source_filtre == "Wizishop":
        query += "&source=eq.wizishop"
    elif source_filtre == "Etsy":
        query += "&source=eq.etsy"

    commandes = select("commandes", query)

    if commandes:
        df = pd.DataFrame(commandes)
        df["date_commande"] = pd.to_datetime(df["date_commande"]).dt.strftime("%d/%m/%Y")
        df.columns = ["Date", "N° commande", "Nom", "Prénom", "Montant (€)", "Statut", "Pays", "Zone TVA", "Suivi", "Source"]
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

    query_cmd = f"select=id_wizi&statut_code=not.in.(0,50)&date_commande=gte.{date_limite}"
    if source_filtre == "Wizishop":
        query_cmd += "&source=eq.wizishop"
    elif source_filtre == "Etsy":
        query_cmd += "&source=eq.etsy"
    else:
        query_cmd += "&source=not.is.null"

    commandes_valides = select("commandes", query_cmd)

    if commandes_valides:
        ids_valides = [str(c["id_wizi"]) for c in commandes_valides]
        ids_str = ",".join(ids_valides)

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

            df_lignes["nom_produit_enrichi"] = df_lignes["sku"].map(
                lambda x: prod_map.get(x, {}).get("nom", "") or "")
            df_lignes["categorie"] = df_lignes["sku"].map(
                lambda x: prod_map.get(x, {}).get("nom_categorie", "") or "")
            df_lignes["nom_affiche"] = df_lignes.apply(
                lambda r: r["nom_produit_enrichi"] if r["nom_produit_enrichi"] else r["nom_produit"], axis=1)

            st.subheader(f"📊 Tableau 1 — Best-sellers par produit ({nb_mois} derniers mois)")
            bs_produit = df_lignes.groupby(["sku", "nom_affiche", "categorie"]).agg(
                total_vendu=("quantite", "sum"),
                ca_total=("ca", "sum"),
                nb_commandes=("id_commande", "nunique")
            ).reset_index().sort_values("total_vendu", ascending=False).head(100)

            bs_produit["moy_mois"] = (bs_produit["total_vendu"] / nb_mois).round(1)
            bs_produit.columns = ["SKU", "Produit", "Catégorie", "Unités vendues", "CA (€)", "Nb commandes", "Moy/mois"]
            bs_produit["CA (€)"] = bs_produit["CA (€)"].apply(lambda x: f"{x:.2f}")
            st.dataframe(bs_produit, use_container_width=True, hide_index=True)
            csv1 = bs_produit.to_csv(index=False).encode("utf-8")
            st.download_button("Télécharger tableau 1", csv1, "bestsellers_produits.csv", "text/csv")

            st.divider()
            st.subheader(f"🎨 Tableau 2 — Best-sellers par variation ({nb_mois} derniers mois)")

            df_var = df_lignes[df_lignes["sku_variation"].notna() & (df_lignes["sku_variation"] != "")].copy()
            df_novar = df_lignes[df_lignes["sku_variation"].isna() | (df_lignes["sku_variation"] == "")].copy()

            bs_variation = pd.concat([
                df_var.groupby(["sku_variation", "nom_affiche", "libelle_variation", "categorie"]).agg(
                    total_vendu=("quantite_variation" if "quantite_variation" in df_var.columns else "quantite", "sum"),
                    ca_total=("ca", "sum"),
                    nb_commandes=("id_commande", "nunique")
                ).reset_index().rename(columns={"sku_variation": "sku_var", "libelle_variation": "variation"}),
                df_novar.groupby(["sku", "nom_affiche", "categorie"]).agg(
                    total_vendu=("quantite", "sum"),
                    ca_total=("ca", "sum"),
                    nb_commandes=("id_commande", "nunique")
                ).reset_index().rename(columns={"sku": "sku_var"}).assign(variation="—")
            ]).sort_values("total_vendu", ascending=False).head(100)

            skus_data = select("skus", "select=sku,stock&statut=eq.visible")
            sku_stock = {s["sku"]: s["stock"] for s in skus_data} if skus_data else {}

            bs_variation["stock"] = bs_variation["sku_var"].map(lambda x: sku_stock.get(x, 0))
            bs_variation["moy_mois"] = (bs_variation["total_vendu"] / nb_mois).round(1)
            bs_variation["mois_stock"] = bs_variation.apply(
                lambda r: round(r["stock"] / r["moy_mois"], 1) if r["moy_mois"] > 0 else 99, axis=1)

            def alerte(mois):
                if mois <= 3:
                    return "🔴 Commander"
                elif mois <= 5:
                    return "🟡 Surveiller"
                else:
                    return "🟢 OK"

            bs_variation["alerte"] = bs_variation["mois_stock"].apply(alerte)

            cols_show = ["sku_var", "nom_affiche", "variation", "categorie", "stock",
                        "total_vendu", "moy_mois", "mois_stock", "alerte"]
            cols_show = [c for c in cols_show if c in bs_variation.columns]
            df_show = bs_variation[cols_show].copy()
            df_show.columns = ["SKU", "Produit", "Variation", "Catégorie", "Stock",
                               "Unités vendues", "Moy/mois", "Mois de stock", "Alerte"][:len(cols_show)]
            st.dataframe(df_show, use_container_width=True, hide_index=True)
            csv2 = df_show.to_csv(index=False).encode("utf-8")
            st.download_button("Télécharger tableau 2", csv2, "bestsellers_variations.csv", "text/csv")
        else:
            st.info("Aucune ligne de commande trouvée.")
    else:
        st.info("Aucune commande valide trouvée.")

elif page == "🚨 Réapprovisionnement":
    with st.sidebar:
        st.divider()
        nb_mois = st.slider("Période calcul ventes (mois)", min_value=1, max_value=12, value=6)
        fournisseur_filtre = st.text_input("Filtrer par fournisseur")
        alerte_filtre = st.selectbox("Filtre alerte", [
            "Tous les produits",
            "🔴 À commander uniquement",
            "🔴 + 🟡 Surveiller"
        ])

    st.subheader("🚨 Réapprovisionnement — Tous les produits")
    st.info(f"Calcul basé sur les ventes des {nb_mois} derniers mois. Délai fournisseur : 2 mois.")

    date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")
    commandes_valides = select("commandes",
        f"select=id_wizi&statut_code=not.in.(0,50)&date_commande=gte.{date_limite}")

    skus_data = select("skus", "select=sku,nom,fournisseur,stock,statut&statut=eq.visible")
    produits_data = select("produits", "select=sku,nom,nom_categorie,fournisseur,reference_fournisseur")
    prod_map = {p["sku"]: p for p in produits_data} if produits_data else {}

    if commandes_valides and skus_data:
        ids_valides = [str(c["id_wizi"]) for c in commandes_valides]
        ids_str = ",".join(ids_valides)

        lignes = select("lignes_commande",
            f"select=sku,sku_variation,quantite,id_commande&id_commande=in.({ids_str})",
            limit=50000)

        ventes_par_sku = {}
        if lignes:
            for ligne in lignes:
                sku_key = ligne.get("sku_variation") or ligne.get("sku")
                if sku_key:
                    ventes_par_sku[sku_key] = ventes_par_sku.get(sku_key, 0) + (ligne.get("quantite") or 0)

        rows = []
        for sku_item in skus_data:
            sku = sku_item.get("sku")
            stock = int(sku_item.get("stock") or 0)
            prod = prod_map.get(sku, {})
            nom = prod.get("nom") or sku_item.get("nom") or sku
            fournisseur = prod.get("fournisseur") or sku_item.get("fournisseur") or ""
            ref_fourn = prod.get("reference_fournisseur") or ""
            categorie = prod.get("nom_categorie") or ""
            ventes = ventes_par_sku.get(sku, 0)
            moy_mois = round(ventes / nb_mois, 1)
            mois_stock = round(stock / moy_mois, 1) if moy_mois > 0 else 99

            if mois_stock <= 3:
                alerte = "🔴 Commander"
            elif mois_stock <= 5:
                alerte = "🟡 Surveiller"
            else:
                alerte = "🟢 OK"

            rows.append({
                "SKU": sku,
                "Produit": nom,
                "Catégorie": categorie,
                "Fournisseur": fournisseur,
                "Réf. fournisseur": ref_fourn,
                "Stock": stock,
                "Ventes/mois": moy_mois,
                "Mois de stock": mois_stock,
                "Alerte": alerte
            })

        df_reap = pd.DataFrame(rows)

        if fournisseur_filtre:
            df_reap = df_reap[df_reap["Fournisseur"].str.contains(fournisseur_filtre, case=False, na=False)]

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

        st.dataframe(df_reap, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Export par fournisseur")
        fournisseurs = df_reap["Fournisseur"].dropna().unique()
        fournisseur_export = st.selectbox("Choisir un fournisseur", ["Tous"] + list(fournisseurs))

        if fournisseur_export == "Tous":
            df_export = df_reap[df_reap["Alerte"] == "🔴 Commander"]
        else:
            df_export = df_reap[
                (df_reap["Fournisseur"] == fournisseur_export) &
                (df_reap["Alerte"] == "🔴 Commander")
            ]

        if not df_export.empty:
            csv = df_export.to_csv(index=False).encode("utf-8")
            st.download_button(
                f"📥 Télécharger commande {fournisseur_export}",
                csv,
                f"commande_{fournisseur_export.replace(' ', '_')}.csv",
                "text/csv"
            )
        else:
            st.info("Aucun produit à commander pour ce fournisseur.")
    else:
        st.info("Aucune donnée. Lance d'abord une synchronisation.")

elif page == "🏭 Stock & Fournisseurs":
    with st.sidebar:
        st.divider()
        fournisseur_filtre = st.text_input("Filtrer par fournisseur")
        stock_filtre = st.selectbox("Stock", ["Tous", "En rupture (stock = 0)", "En stock (stock > 0)"])

    skus = select("skus",
        "select=sku,nom,fournisseur,stock,statut,date_maj_stock&statut=eq.visible&order=stock.asc")
    produits = select("produits",
        "select=id_wizi,sku,nom,fournisseur&statut=eq.visible")

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
            df_skus = df_skus[df_skus["fournisseur"].str.contains(fournisseur_filtre, case=False, na=False)]
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

        cols = [c for c in ["sku", "nom", "fournisseur", "stock", "date_maj_stock"] if c in df_skus.columns]
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

elif page == "🌍 Comptabilité TVA":
    with st.sidebar:
        st.divider()
        annee = st.selectbox("Année", [2026, 2025, 2024, 2023], index=0)
        source_filtre = st.selectbox("Source", ["Toutes", "Wizishop", "Etsy"])

    query = f"select=zone_tva,pays_facturation,pays_facturation_iso,montant_ttc,montant_ht,source&statut_code=not.in.(0,50)&date_commande=gte.{annee}-01-01&date_commande=lt.{annee+1}-01-01"
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
