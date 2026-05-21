import streamlit as st
import pandas as pd
from supabase_api import select, upsert, insert
from sync_database import (get_wizi_token, sync_categories, sync_marques,
                           sync_skus, sync_commandes, sync_produits, log_sync)
from sync_etsy import sync_etsy_commandes, log_sync_etsy
from sync_etsy_produits import sync_produits_etsy
from etsy_api import get_shop_id
import time
from datetime import datetime, timezone

st.set_page_config(
    page_title="Pique&Pince — Dashboard",
    page_icon="📊",
    layout="wide"
)


def get_prod_parent(sku, prod_map):
    if not sku:
        return {}
    sku = str(sku)
    if sku in prod_map:
        return prod_map[sku]
    for longueur in range(len(sku)-1, 3, -1):
        prefixe = sku[:longueur]
        if prefixe in prod_map:
            return prod_map[prefixe]
    return {}


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
        "🌍 Comptabilité TVA",
        "🔍 Vérification Wizishop",
        "🔍 Vérification Etsy",
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
        df_mois_wizi = df_mois[df_mois["source"] == "wizishop"]
        df_mois_etsy = df_mois[df_mois["source"] == "etsy"]

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
        col_w, col_e = st.columns(2)
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
        "select=id,sku,nom_produit,fournisseur,date_commande&statut=eq.en_commande")
    skus_en_commande = {c["sku"] for c in en_commande} if en_commande else set()

    date_limite = (pd.Timestamp.now() - pd.DateOffset(months=nb_mois)).strftime("%Y-%m-%dT%H:%M:%S")
    commandes_valides = select("commandes",
        f"select=id_wizi&statut_code=not.in.(0,45,50)&date_commande=gte.{date_limite}")

    skus_data = select("skus", "select=sku,stock,statut&statut=eq.visible")
    produits_data = select("produits",
        "select=sku,nom,nom_categorie,fournisseur,reference_fournisseur,prix_achat_ht")
    prod_map = {p["sku"]: p for p in produits_data} if produits_data else {}

    if commandes_valides and skus_data:
        ids_valides = [str(c["id_wizi"]) for c in commandes_valides]
        ids_str = ",".join(ids_valides)

        lignes = select("lignes_commande",
            f"select=sku,sku_variation,quantite,nom_produit,id_commande&id_commande=in.({ids_str})",
            limit=50000)

        nom_par_sku = {}
        if lignes:
            for ligne in lignes:
                sku = ligne.get("sku")
                if sku and sku not in nom_par_sku:
                    nom_par_sku[sku] = ligne.get("nom_produit", "")

        ventes_par_sku = {}
        if lignes:
            for ligne in lignes:
                sku_key = ligne.get("sku_variation") or ligne.get("sku")
                if sku_key:
                    ventes_par_sku[sku_key] = ventes_par_sku.get(sku_key, 0) + (ligne.get("quantite") or 0)

        rows = []
        for sku_item in skus_data:
            sku = sku_item.get("sku")
            if sku in skus_en_commande:
                continue
            stock = int(sku_item.get("stock") or 0)
            prod = get_prod_parent(sku, prod_map)
            nom = prod.get("nom") or nom_par_sku.get(sku, "") or sku
            fournisseur = prod.get("fournisseur") or ""
            ref_fourn = prod.get("reference_fournisseur") or ""
            prix_achat = prod.get("prix_achat_ht") or 0
            categorie = prod.get("nom_categorie") or ""

            prod_exact = prod_map.get(sku, {})
            if not prod_exact and prod:
                parent_sku = prod.get("sku", "")
                variation = sku[len(parent_sku):] if parent_sku and sku.startswith(parent_sku) else ""
            else:
                variation = ""

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
                "sku": sku,
                "Produit": nom,
                "Variation": variation,
                "Catégorie": categorie,
                "Fournisseur": fournisseur,
                "Réf. fournisseur": ref_fourn,
                "Prix achat HT": f"{float(prix_achat):.2f} €" if prix_achat else "",
                "Stock": stock,
                "Ventes/mois": moy_mois,
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
            cols_affich = ["sku", "Produit", "Variation", "Catégorie", "Fournisseur",
                          "Réf. fournisseur", "Prix achat HT", "Stock",
                          "Ventes/mois", "Mois de stock", "Alerte"]
            df_show = df_reap[cols_affich].copy()
            df_show = df_show.rename(columns={"sku": "SKU"})
            st.dataframe(df_show, use_container_width=True, hide_index=True)

            st.divider()
            col_cmd1, col_cmd2 = st.columns([3, 1])
            with col_cmd1:
                sku_selectionne = st.selectbox(
                    "Sélectionner un SKU à marquer en commande",
                    options=[""] + df_reap["sku"].tolist(),
                    format_func=lambda x: f"{x} — {df_reap[df_reap['sku']==x]['Produit'].iloc[0]}"
                    if x else "Choisir un SKU..."
                )
            with col_cmd2:
                st.write("")
                st.write("")
                if sku_selectionne and st.button("📦 Marquer en commande", type="primary"):
                    row = df_reap[df_reap["sku"] == sku_selectionne].iloc[0]
                    insert("commandes_fournisseur", [{
                        "sku": sku_selectionne,
                        "nom_produit": row["Produit"],
                        "fournisseur": row["Fournisseur"],
                        "date_commande": datetime.now(timezone.utc).isoformat(),
                        "statut": "en_commande"
                    }])
                    st.success(f"✓ {sku_selectionne} marqué en commande !")
                    st.rerun()

            st.divider()
            csv = df_show.to_csv(index=False).encode("utf-8")
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

    en_commande_affich = select("commandes_fournisseur",
        "select=id,sku,nom_produit,fournisseur,date_commande&statut=eq.en_commande&order=date_commande.desc")

    if en_commande_affich:
        df_cmd = pd.DataFrame(en_commande_affich)
        df_cmd["date_commande"] = pd.to_datetime(
            df_cmd["date_commande"]).dt.strftime("%d/%m/%Y")
        df_show_cmd = df_cmd[["sku", "nom_produit", "fournisseur", "date_commande"]].copy()
        df_show_cmd.columns = ["SKU", "Produit", "Fournisseur", "Date commande"]
        st.dataframe(df_show_cmd, use_container_width=True, hide_index=True)

        st.divider()
        col_r1, col_r2 = st.columns([3, 1])
        with col_r1:
            sku_recu = st.selectbox(
                "Sélectionner un SKU reçu",
                options=[""] + df_cmd["sku"].tolist(),
                format_func=lambda x: f"{x} — {df_cmd[df_cmd['sku']==x]['nom_produit'].iloc[0]}"
                if x else "Choisir un SKU..."
            )
        with col_r2:
            st.write("")
            st.write("")
            if sku_recu and st.button("✅ Marquer comme reçu", type="primary"):
                row_cmd = df_cmd[df_cmd["sku"] == sku_recu].iloc[0]
                upsert("commandes_fournisseur", [{
                    "id": int(row_cmd["id"]),
                    "statut": "recu"
                }], "id")
                st.success(f"✓ {sku_recu} marqué comme reçu !")
                st.rerun()
    else:
        st.info("Aucun produit en commande actuellement.")

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
