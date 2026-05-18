import streamlit as st
import pandas as pd
import pg8000.native as psycopg2
from sync_database import run_full_sync

st.set_page_config(
    page_title="Pique&Pince — Dashboard",
    page_icon="📊",
    layout="wide"
)

st.title("Pique&Pince — Dashboard ventes")

def get_db_connection():
    return pg8000.native.Connection(
    host="db.donsocudmtnopajnhomj.supabase.co",
    database="postgres",
    user="postgres",
    password=st.secrets["SUPABASE_DB_PASSWORD"],
    port=5432
)

def get_zone_tva(country_iso):
    ue = {"AT","BE","BG","CY","CZ","DE","DK","EE","ES","FI","FR","GR",
          "HR","HU","IE","IT","LT","LU","LV","MT","NL","PL","PT","RO",
          "SE","SI","SK"}
    if not country_iso:
        return "inconnu"
    if country_iso == "FR":
        return "france"
    if country_iso in ue:
        return "ue"
    return "hors_ue"

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

# Page synchronisation
if page == "🔄 Synchronisation":
    st.subheader("Synchronisation des données")
    st.info("La synchronisation récupère toutes les données Wizishop et les stocke dans la base de données.")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Lancer la synchronisation", use_container_width=True):
            with st.spinner("Synchronisation en cours... (peut prendre plusieurs minutes)"):
                succes, resultats = run_full_sync()
            if succes:
                st.success("Synchronisation terminée !")
                for table, info in resultats.items():
                    st.write(f"{info['statut']} **{table}** — {info['nb']} enregistrements en {info['duree']:.1f}s")
            else:
                st.error(f"Erreur : {resultats}")
    with col2:
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT table_name, nb_enregistrements, statut, created_at FROM sync_log ORDER BY created_at DESC LIMIT 10")
            logs = cur.fetchall()
            cur.close()
            conn.close()
            if logs:
                df_logs = pd.DataFrame(logs, columns=["Table", "Nb", "Statut", "Date"])
                df_logs["Date"] = pd.to_datetime(df_logs["Date"]).dt.strftime("%d/%m/%Y %H:%M")
                st.subheader("Dernières synchronisations")
                st.dataframe(df_logs, use_container_width=True, hide_index=True)
        except:
            st.warning("Aucun historique disponible.")

else:
    try:
        conn = get_db_connection()

        if page == "📊 Vue d'ensemble":
            st.subheader("Vue d'ensemble — Wizishop")
            with st.sidebar:
                st.divider()
                nb_mois = st.slider("Période (mois)", min_value=1, max_value=24, value=12)

            cur = conn.cursor()
            cur.execute("""
                SELECT
                    COUNT(*) as nb_commandes,
                    SUM(montant_ttc) as ca_ttc,
                    SUM(montant_ht) as ca_ht
                FROM commandes
                WHERE statut_code NOT IN (0, 50)
                AND date_commande >= NOW() - INTERVAL '%s months'
            """, (nb_mois,))
            stats = cur.fetchone()

            cur.execute("""
                SELECT
                    COUNT(*) as nb_commandes,
                    SUM(montant_ttc) as ca_ttc
                FROM commandes
                WHERE statut_code NOT IN (0, 50)
                AND date_commande >= DATE_TRUNC('month', NOW())
            """)
            stats_mois = cur.fetchone()
            cur.close()

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Commandes ce mois", int(stats_mois[0] or 0))
            with col2:
                st.metric("CA ce mois (TTC)", f"{stats_mois[1] or 0:.0f} €")
            with col3:
                st.metric(f"CA sur {nb_mois} mois (TTC)", f"{stats[1] or 0:.0f} €")
            with col4:
                st.metric(f"Commandes sur {nb_mois} mois", int(stats[0] or 0))

            st.divider()
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    TO_CHAR(date_commande, 'YYYY-MM') as mois,
                    COUNT(*) as nb_commandes,
                    SUM(montant_ttc) as ca
                FROM commandes
                WHERE statut_code NOT IN (0, 50)
                AND date_commande >= NOW() - INTERVAL '%s months'
                GROUP BY mois ORDER BY mois
            """, (nb_mois,))
            rows = cur.fetchall()
            cur.close()

            if rows:
                df = pd.DataFrame(rows, columns=["Mois", "Commandes", "CA (€)"])
                col_g1, col_g2 = st.columns(2)
                with col_g1:
                    st.subheader("Commandes par mois")
                    st.bar_chart(df.set_index("Mois")["Commandes"])
                with col_g2:
                    st.subheader("CA par mois (€)")
                    st.bar_chart(df.set_index("Mois")["CA (€)"])

        elif page == "📦 Commandes":
            st.subheader("Commandes")
            with st.sidebar:
                st.divider()
                nb_mois = st.slider("Période (mois)", min_value=1, max_value=24, value=3)
                statuts = st.multiselect("Statuts", [
                    "Livrée", "Livraison en cours", "En attente de préparation",
                    "Remboursée", "Annulée", "Abandonnée"
                ], default=["Livrée", "Livraison en cours", "En attente de préparation"])

            cur = conn.cursor()
            query = """
                SELECT date_commande, numero_commande, nom_facturation,
                       prenom_facturation, montant_ttc, statut_texte,
                       pays_facturation_iso, zone_tva, numero_suivi
                FROM commandes
                WHERE date_commande >= NOW() - INTERVAL '%s months'
            """
            params = [nb_mois]
            if statuts:
                query += " AND statut_texte = ANY(%s)"
                params.append(statuts)
            query += " ORDER BY date_commande DESC LIMIT 500"
            cur.execute(query, params)
            rows = cur.fetchall()
            cur.close()

            if rows:
                df = pd.DataFrame(rows, columns=[
                    "Date", "N° commande", "Nom", "Prénom",
                    "Montant (€)", "Statut", "Pays", "Zone TVA", "Suivi"
                ])
                df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%d/%m/%Y")
                st.dataframe(df, use_container_width=True, hide_index=True)
                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button("Télécharger en CSV", csv,
                                  f"commandes_{nb_mois}mois.csv", "text/csv")
            else:
                st.info("Aucune commande trouvée.")

        elif page == "⭐ Best-sellers":
            st.subheader("Best-sellers")
            with st.sidebar:
                st.divider()
                nb_mois = st.slider("Période (mois)", min_value=1, max_value=24, value=12)

            cur = conn.cursor()
            cur.execute("""
                SELECT
                    l.sku,
                    p.nom as produit,
                    p.fournisseur,
                    SUM(l.quantite) as total_vendu,
                    SUM(l.quantite * l.prix_unitaire_ttc) as ca_total,
                    COUNT(DISTINCT l.id_commande) as nb_commandes
                FROM lignes_commande l
                LEFT JOIN skus s ON l.sku = s.sku
                LEFT JOIN produits p ON s.id_produit_parent = p.id_wizi
                JOIN commandes c ON l.id_commande = c.id_wizi
                WHERE c.statut_code NOT IN (0, 50)
                AND c.date_commande >= NOW() - INTERVAL '%s months'
                GROUP BY l.sku, p.nom, p.fournisseur
                ORDER BY total_vendu DESC
                LIMIT 50
            """, (nb_mois,))
            rows = cur.fetchall()
            cur.close()

            if rows:
                df = pd.DataFrame(rows, columns=[
                    "SKU", "Produit", "Fournisseur",
                    "Unités vendues", "CA (€)", "Nb commandes"
                ])
                df["CA (€)"] = df["CA (€)"].apply(lambda x: f"{x:.2f}" if x else "0.00")
                st.dataframe(df, use_container_width=True, hide_index=True)
                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button("Télécharger en CSV", csv, "bestsellers.csv", "text/csv")
            else:
                st.info("Aucune donnée disponible. Lance d'abord une synchronisation.")

        elif page == "🏭 Stock & Fournisseurs":
            st.subheader("Stock & Fournisseurs")
            with st.sidebar:
                st.divider()
                fournisseur_filtre = st.text_input("Filtrer par fournisseur")
                stock_filtre = st.selectbox("Stock", ["Tous", "En rupture (stock = 0)", "En stock (stock > 0)"])

            cur = conn.cursor()
            query = """
                SELECT s.sku, p.nom as produit, p.fournisseur,
                       s.stock, s.statut, s.date_maj_stock
                FROM skus s
                LEFT JOIN produits p ON s.id_produit_parent = p.id_wizi
                WHERE s.statut = 'visible'
            """
            params = []
            if fournisseur_filtre:
                query += " AND LOWER(p.fournisseur) LIKE LOWER(%s)"
                params.append(f"%{fournisseur_filtre}%")
            if stock_filtre == "En rupture (stock = 0)":
                query += " AND s.stock = 0"
            elif stock_filtre == "En stock (stock > 0)":
                query += " AND s.stock > 0"
            query += " ORDER BY s.stock ASC, p.fournisseur"
            cur.execute(query, params)
            rows = cur.fetchall()
            cur.close()

            if rows:
                df = pd.DataFrame(rows, columns=[
                    "SKU", "Produit", "Fournisseur", "Stock", "Statut", "Dernière MAJ"
                ])
                df["Dernière MAJ"] = pd.to_datetime(df["Dernière MAJ"]).dt.strftime("%d/%m/%Y")

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Total SKUs actives", len(df))
                with col2:
                    st.metric("En rupture", len(df[df["Stock"] == 0]))
                with col3:
                    st.metric("En stock", len(df[df["Stock"] > 0]))

                st.dataframe(df, use_container_width=True, hide_index=True)
                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button("Télécharger en CSV", csv, "stock.csv", "text/csv")
            else:
                st.info("Aucune donnée disponible. Lance d'abord une synchronisation.")

        elif page == "🌍 Comptabilité TVA":
            st.subheader("Répartition CA par zone TVA")
            with st.sidebar:
                st.divider()
                annee = st.selectbox("Année", [2026, 2025, 2024, 2023], index=0)

            cur = conn.cursor()
            cur.execute("""
                SELECT
                    zone_tva,
                    COUNT(*) as nb_commandes,
                    SUM(montant_ttc) as ca_ttc,
                    SUM(montant_ht) as ca_ht
                FROM commandes
                WHERE statut_code NOT IN (0, 50)
                AND EXTRACT(YEAR FROM date_commande) = %s
                GROUP BY zone_tva
                ORDER BY ca_ttc DESC
            """, (annee,))
            rows = cur.fetchall()

            cur.execute("""
                SELECT
                    pays_facturation, pays_facturation_iso, zone_tva,
                    COUNT(*) as nb_commandes,
                    SUM(montant_ttc) as ca_ttc
                FROM commandes
                WHERE statut_code NOT IN (0, 50)
                AND EXTRACT(YEAR FROM date_commande) = %s
                GROUP BY pays_facturation, pays_facturation_iso, zone_tva
                ORDER BY ca_ttc DESC
            """, (annee,))
            rows_pays = cur.fetchall()
            cur.close()

            if rows:
                df_zones = pd.DataFrame(rows, columns=["Zone", "Nb commandes", "CA TTC (€)", "CA HT (€)"])
                df_zones["CA TTC (€)"] = df_zones["CA TTC (€)"].apply(lambda x: f"{x:.2f}" if x else "0.00")
                df_zones["CA HT (€)"] = df_zones["CA HT (€)"].apply(lambda x: f"{x:.2f}" if x else "0.00")
                df_zones["Zone"] = df_zones["Zone"].map({
                    "france": "🇫🇷 France",
                    "ue": "🇪🇺 Union Européenne",
                    "hors_ue": "🌍 Hors UE",
                    "inconnu": "❓ Inconnu"
                }).fillna(df_zones["Zone"])

                st.subheader(f"Répartition par zone — {annee}")
                st.dataframe(df_zones, use_container_width=True, hide_index=True)

                if rows_pays:
                    df_pays = pd.DataFrame(rows_pays, columns=[
                        "Pays", "ISO", "Zone", "Nb commandes", "CA TTC (€)"
                    ])
                    df_pays["CA TTC (€)"] = df_pays["CA TTC (€)"].apply(lambda x: f"{x:.2f}" if x else "0.00")
                    st.divider()
                    st.subheader("Détail par pays")
                    st.dataframe(df_pays, use_container_width=True, hide_index=True)

                    csv = df_pays.to_csv(index=False).encode("utf-8")
                    st.download_button("Télécharger en CSV", csv,
                                      f"tva_{annee}.csv", "text/csv")
            else:
                st.info("Aucune donnée disponible. Lance d'abord une synchronisation.")

        conn.close()

    except Exception as e:
        st.error(f"Erreur de connexion à la base de données : {e}")
        st.info("Lance d'abord une synchronisation depuis le menu 'Synchronisation'.")
