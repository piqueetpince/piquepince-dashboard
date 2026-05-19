import streamlit as st
import pandas as pd
from supabase_api import select
from sync_database import (get_wizi_token, sync_categories, sync_marques,
                           sync_skus, sync_commandes, log_sync)
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
        "🏭 Stock & Fournisseurs",
        "🌍 Comptabilité TVA",
        "🔄 Synchronisation"
    ])

if page == "🔄 Synchronisation":
    st.subheader("Synchronisation des données")
    st.info("Lance chaque synchronisation séparément pour éviter les timeouts.")

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
                st.success("Connecté à Wizishop !")
            else:
                st.error("Impossible de se connecter à Wizishop.")

    if token_cached:
        col1, col2 = st.columns(2)

        with col1:
            if st.button("1️⃣ Sync Catégories & Marques", use_container_width=True):
                with st.spinner("Synchronisation catégories et marques..."):
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

        with col2:
            if st.button("3️⃣ Sync Commandes", use_container_width=True):
                with st.spinner("Synchronisation commandes... (10-15 min)"):
                    debut = time.time()
                    try:
                        nb = sync_commandes(token_cached, shop_id_cached)
                        duree = time.time() - debut
                        log_sync("commandes", "wizishop", nb, "success", f"{nb} enregistrements", duree)
                        st.success(f"✓ {nb} commandes en {duree:.1f}s")
                    except Exception as e:
                        st.error(f"Erreur : {e}")

            st.info("💡 Lance 1️⃣ puis 2️⃣ puis 3️⃣. Les prochaines synchros seront plus rapides.")

    logs = select("sync_log", "select=table_name,nb_enregistrements,statut,created_at&order=created_at.desc&limit=10")
    if logs:
        st.divider()
        st.subheader("Historique des synchronisations")
        df_logs = pd.DataFrame(logs)
        df_logs.columns = ["Table", "Nb", "Statut", "Date"]
        df_logs["Date"] = pd.to_datetime(df_logs["Date"]).dt.strftime("%d/%m/%Y %H:%M")
        st.dataframe(df_logs, use_container_width=True, hide_index=True)

elif page == "📊 Vue d'ensemble":
    with st.sidebar:
        st.divider()
        nb_mois = st.slider("Période (mois)", min_value=1, max_value=24, value=12)

    commandes = select("commandes",
        "select=date_commande,montant_ttc,montant_ht,statut_code&statut_code=not.in.(0,50)&source=eq.wizishop")

    st.write(f"Nb commandes récupérées : {len(commandes) if commandes else 0}")

    if commandes:
        df = pd.DataFrame(commandes)
        st.write(f"Exemple date_commande : {df['date_commande'].iloc[0]}")

        df["date_commande"] = pd.to_datetime(df["date_commande"]).dt.tz_convert(None)
        df["montant_ttc"] = pd.to_numeric(df["montant_ttc"], errors="coerce").fillna(0)
        df["mois"] = df["date_commande"].dt.strftime("%Y-%m")

        date_limite = pd.Timestamp.now() - pd.DateOffset(months=nb_mois)
        st.write(f"Date limite : {date_limite}")
        st.write(f"Min date : {df['date_commande'].min()}")
        st.write(f"Max date : {df['date_commande'].max()}")

        df_periode = df[df["date_commande"] >= date_limite]
        st.write(f"Nb commandes dans la période : {len(df_periode)}")

        if not df_periode.empty:
            mois_max = df_periode["mois"].max()
            df_mois = df_periode[df_periode["mois"] == mois_max]

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Commandes ce mois", len(df_mois))
            with col2:
                st.metric("CA ce mois (TTC)", f"{df_mois['montant_ttc'].sum():.0f} €")
            with col3:
                st.metric(f"CA sur {nb_mois} mois (TTC)", f"{df_periode['montant_ttc'].sum():.0f} €")
            with col4:
                st.metric(f"Commandes sur {nb_mois} mois", len(df_periode))

            st.divider()
            par_mois = df_periode.groupby("mois").agg(
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
            st.warning("Aucune commande dans la période sélectionnée.")
    else:
        st.info("Aucune donnée récupérée de Supabase.")

elif page == "📦 Commandes":
    with st.sidebar:
        st.divider()
        nb_mois = st.slider("Période (mois)", min_value=1, max_value=24, value=3)

    date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")
    commandes = select("commandes",
        f"select=date_commande,numero_commande,nom_facturation,prenom_facturation,montant_ttc,statut_texte,pays_facturation_iso,zone_tva,numero_suivi&date_commande=gte.{date_limite}&statut_code=not.in.(0,50)&order=date_commande.desc&limit=500")

    if commandes:
        df = pd.DataFrame(commandes)
        df["date_commande"] = pd.to_datetime(df["date_commande"]).dt.strftime("%d/%m/%Y")
        df.columns = ["Date", "N° commande", "Nom", "Prénom", "Montant (€)", "Statut", "Pays", "Zone TVA", "Suivi"]
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

    date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")
    lignes = select("lignes_commande",
        "select=sku,nom_produit,quantite,prix_unitaire_ttc,id_commande&source=eq.wizishop")
    commandes_valides = select("commandes",
        f"select=id_wizi&statut_code=not.in.(0,50)&date_commande=gte.{date_limite}&source=eq.wizishop")

    if lignes and commandes_valides:
        ids_valides = {c["id_wizi"] for c in commandes_valides}
        df_lignes = pd.DataFrame(lignes)
        df_lignes = df_lignes[df_lignes["id_commande"].isin(ids_valides)]
        df_lignes["quantite"] = pd.to_numeric(df_lignes["quantite"], errors="coerce").fillna(0)
        df_lignes["prix_unitaire_ttc"] = pd.to_numeric(df_lignes["prix_unitaire_ttc"], errors="coerce").fillna(0)
        df_lignes["ca"] = df_lignes["quantite"] * df_lignes["prix_unitaire_ttc"]

        bestsellers = df_lignes.groupby(["sku", "nom_produit"]).agg(
            total_vendu=("quantite", "sum"),
            ca_total=("ca", "sum"),
            nb_commandes=("id_commande", "nunique")
        ).reset_index().sort_values("total_vendu", ascending=False).head(50)

        bestsellers.columns = ["SKU", "Produit", "Unités vendues", "CA (€)", "Nb commandes"]
        bestsellers["CA (€)"] = bestsellers["CA (€)"].apply(lambda x: f"{x:.2f}")
        st.subheader(f"Top 50 best-sellers — {nb_mois} derniers mois")
        st.dataframe(bestsellers, use_container_width=True, hide_index=True)
        csv = bestsellers.to_csv(index=False).encode("utf-8")
        st.download_button("Télécharger en CSV", csv, "bestsellers.csv", "text/csv")
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

    commandes = select("commandes",
        f"select=zone_tva,pays_facturation,pays_facturation_iso,montant_ttc,montant_ht&statut_code=not.in.(0,50)&date_commande=gte.{annee}-01-01&date_commande=lt.{annee+1}-01-01&source=eq.wizishop")

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
