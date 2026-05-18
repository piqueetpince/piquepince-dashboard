import streamlit as st
import pandas as pd
from wizishop_api import get_token, get_orders, get_products

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

    if "token" in st.session_state:
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

if "token" in st.session_state:
    token = st.session_state["token"]
    shop_id = st.session_state["shop_id"]

    with st.spinner("Chargement des données..."):
        orders_data = get_orders(token, shop_id, limit=100)
        products_data = get_products(token, shop_id, limit=100)

    if orders_data and orders_data.get("results"):
        orders = pd.DataFrame(orders_data["results"])

        orders["date"] = pd.to_datetime(orders["date"], utc=True)
        orders = orders.sort_values("date", ascending=False)
        orders["mois"] = orders["date"].dt.to_period("M").astype(str)
        orders["total_amount"] = pd.to_numeric(orders["total_amount"], errors="coerce")

        date_limite = pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=nb_mois)
        orders_filtrees = orders[orders["date"] >= date_limite].copy()

        mois_max = orders_filtrees["mois"].max() if not orders_filtrees.empty else ""
        orders_ce_mois = orders_filtrees[orders_filtrees["mois"] == mois_max]

        st.subheader("Vue d'ensemble")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Commandes ce mois", len(orders_ce_mois))
        with col2:
            ca_mois = orders_ce_mois["total_amount"].sum()
            st.metric("CA ce mois", f"{ca_mois:.0f} €")
        with col3:
            ca_periode = orders_filtrees["total_amount"].sum()
            st.metric(f"CA sur {nb_mois} mois", f"{ca_periode:.0f} €")
        with col4:
            st.metric("Commandes sur la période", len(orders_filtrees))

        st.divider()

        col_graph, col_top = st.columns([3, 2])

        with col_graph:
            st.subheader("Commandes par mois")
            if not orders_filtrees.empty:
                par_mois = orders_filtrees.groupby("mois").agg(
                    commandes=("id", "count"),
                    ca=("total_amount", "sum")
                ).reset_index().sort_values("mois")
                st.bar_chart(par_mois.set_index("mois")[["commandes"]])

        with col_top:
            st.subheader("Statut des commandes")
            if not orders_filtrees.empty:
                par_statut = orders_filtrees.groupby("status_text").size().reset_index(name="nb")
                par_statut = par_statut.sort_values("nb", ascending=False)
                st.dataframe(par_statut, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Dernières commandes")
        cols = ["date", "public_id", "client_full_name", "nb_products", "total_amount", "status_text", "currency"]
        df_affichage = orders_filtrees[cols].copy()
        df_affichage["date"] = df_affichage["date"].dt.strftime("%d/%m/%Y")
        df_affichage.columns = ["Date", "N° commande", "Client", "Produits", "Montant (€)", "Statut", "Devise"]
        st.dataframe(df_affichage.head(20), use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Export")
        csv = orders_filtrees[cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Télécharger les commandes en CSV",
            data=csv,
            file_name=f"commandes_wizishop_{nb_mois}mois.csv",
            mime="text/csv"
        )

    else:
        st.warning("Aucune commande récupérée.")

    st.divider()

    if products_data and products_data.get("results"):
        st.subheader("Produits & stock")
        products = pd.DataFrame(products_data["results"])
        products["stock"] = pd.to_numeric(products["stock"], errors="coerce").fillna(0).astype(int)

        col_stock1, col_stock2 = st.columns(2)
        with col_stock1:
            st.metric("Produits actifs", len(products[products["status"] == "enabled"]) if "status" in products.columns else len(products))
        with col_stock2:
            st.metric("Produits en rupture", len(products[products["stock"] == 0]))

        cols_produits = ["sku", "label", "stock", "status"]
        df_produits = products[cols_produits].copy()
        df_produits.columns = ["SKU", "Produit", "Stock", "Statut"]
        df_produits = df_produits.sort_values("Stock", ascending=True)
        st.dataframe(df_produits, use_container_width=True, hide_index=True)

    else:
        st.warning("Aucun produit récupéré.")

else:
    st.info("Entre tes identifiants Wizishop dans le menu à gauche pour commencer.")
