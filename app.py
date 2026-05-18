import streamlit as st
import pandas as pd
from wizishop_api import get_token, get_all_recent_orders, get_all_skus

st.set_page_config(
    page_title="Pique&Pince — Dashboard",
    page_icon="📊",
    layout="wide"
)

st.title("Pique&Pince — Dashboard ventes")

with st.sidebar:
    st.header("Connexion Wizishop")
    email = st.text_input("Email")
    password = st.text_input("Mot de passe", type="password")
    connect_btn = st.button("Se connecter", use_container_width=True)
    st.divider()
    st.subheader("Filtres")
    nb_mois = st.slider("Période (mois)", min_value=1, max_value=24, value=12)

if connect_btn:
    with st.spinner("Connexion en cours..."):
        token, account_id, shop_id = get_token(email, password)
    if token:
        st.session_state["token"] = token
        st.session_state["account_id"] = account_id
        st.session_state["shop_id"] = shop_id
        st.sidebar.success("Connecté !")
    else:
        st.sidebar.error("Identifiants incorrects")

STATUTS_VALIDES = [
    "Livrée",
    "Livraison en cours",
    "En attente de préparation",
    "Problème livraison / retour",
    "Remboursée",
    "En cours de retour"
]

if "token" in st.session_state:
    token = st.session_state["token"]
    shop_id = st.session_state["shop_id"]

    with st.spinner("Chargement des données..."):
        orders_list = get_all_recent_orders(token, shop_id, nb_mois=nb_mois)
        skus_list = get_all_skus(token, shop_id)

    if orders_list:
        orders_brutes = pd.DataFrame(orders_list)
        orders_brutes["date"] = pd.to_datetime(orders_brutes["date"], utc=True)
        orders_brutes = orders_brutes.sort_values("date", ascending=False)
        orders_brutes["mois"] = orders_brutes["date"].dt.to_period("M").astype(str)
        orders_brutes["total_amount"] = pd.to_numeric(orders_brutes["total_amount"], errors="coerce")

        orders = orders_brutes[orders_brutes["status_text"].isin(STATUTS_VALIDES)].copy()
        mois_max = orders["mois"].max() if not orders.empty else ""
        orders_ce_mois = orders[orders["mois"] == mois_max]

        st.subheader("Vue d'ensemble — commandes validées")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Commandes ce mois", len(orders_ce_mois))
        with col2:
            ca_mois = orders_ce_mois["total_amount"].sum()
            st.metric("CA ce mois", f"{ca_mois:.0f} €")
        with col3:
            ca_periode = orders["total_amount"].sum()
            st.metric(f"CA sur {nb_mois} mois", f"{ca_periode:.0f} €")
        with col4:
            st.metric("Commandes validées (période)", len(orders))

        st.divider()
        col_graph, col_top = st.columns([3, 2])

        with col_graph:
            st.subheader("Commandes validées par mois")
            par_mois = orders.groupby("mois").agg(
                commandes=("id", "count"),
                ca=("total_amount", "sum")
            ).reset_index().sort_values("mois")
            st.bar_chart(par_mois.set_index("mois")[["commandes"]])

        with col_top:
            st.subheader("Répartition par statut")
            par_statut = orders_brutes.groupby("status_text").size().reset_index(name="nb")
            par_statut = par_statut.sort_values("nb", ascending=False)
            st.dataframe(par_statut, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Dernières commandes validées")
        cols = ["date", "public_id", "client_full_name", "nb_products", "total_amount", "status_text", "currency"]
        df_affichage = orders[cols].copy()
        df_affichage["date"] = df_affichage["date"].dt.strftime("%d/%m/%Y")
        df_affichage.columns = ["Date", "N° commande", "Client", "Produits", "Montant (€)", "Statut", "Devise"]
        st.dataframe(df_affichage.head(20), use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Export")
        csv = orders[cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Télécharger les commandes validées en CSV",
            data=csv,
            file_name=f"commandes_wizishop_{nb_mois}mois.csv",
            mime="text/csv"
        )

    else:
        st.warning("Aucune commande trouvée sur cette période.")

    st.divider()

    if skus_list:
        st.subheader("Produits & stock")
        skus = pd.DataFrame(skus_list)
        skus["stock"] = pd.to_numeric(skus["stock"], errors="coerce").fillna(0).astype(int)

        skus_visibles = skus[skus["status"] == "visible"].copy()

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total SKUs actives", len(skus_visibles))
        with col2:
            st.metric("SKUs en rupture", len(skus_visibles[skus_visibles["stock"] == 0]))
        with col3:
            st.metric("SKUs en stock", len(skus_visibles[skus_visibles["stock"] > 0]))

        st.subheader("SKUs affichées en boutique")
        cols_skus = ["sku", "label", "stock", "type"]
        df_skus = skus_visibles[cols_skus].copy()
        df_skus.columns = ["SKU", "Produit", "Stock", "Type"]
        df_skus = df_skus.sort_values("Stock", ascending=True)
        st.dataframe(df_skus, use_container_width=True, hide_index=True)

    else:
        st.warning("Aucune SKU récupérée.")

else:
    st.info("Entre tes identifiants Wizishop dans le menu à gauche pour commencer.")
