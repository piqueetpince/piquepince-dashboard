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

    with st.spinner("Chargement des commandes..."):
        orders_data = get_orders(token, shop_id, limit=100)
        products_data = get_products(token, shop_id, limit=100)

    if orders_data and orders_data.get("results"):
        orders = pd.DataFrame(orders_data["results"])

        if "created_at" in orders.columns:
            orders["date"] = pd.to_datetime(orders["created_at"], utc=True)
            orders = orders.sort_values("date", ascending=False)
            orders["mois"] = orders["date"].dt.to_period("M").astype(str)

            date_limite = pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=nb_mois)
            orders_filtrees = orders[orders["date"] >= date_limite]
            mois_max = orders_filtrees["mois"].max() if not orders_filtrees.empty else orders["mois"].max()
            orders_ce_mois = orders_filtrees[orders_filtrees["mois"] == mois_max]
        else:
            orders_filtrees = orders
            orders_ce_mois = orders
            mois_max = "N/A"

        st.subheader("Vue d'ensemble")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Commandes ce mois", len(orders_ce_mois))
        with col2:
            if "total_price" in orders.columns:
                ca = orders_ce_mois["total_price"].astype(float).sum()
                st.metric("CA ce mois (Wizishop)", f"{ca:.0f} €")
            else:
                st.metric("CA ce mois", "N/A")
        with col3:
            st.metric("Commandes total (période)", len(orders_filtrees))

        if "mois" in orders_filtrees.columns and not orders_filtrees.empty:
            st.subheader(f"Commandes par mois — {nb_mois} derniers mois")
            par_mois = orders_filtrees.groupby("mois").size().reset_index(name="commandes")
            par_mois = par_mois.sort_values("mois")
            st.bar_chart(par_mois.set_index("mois"))

        st.subheader("Dernières commandes")
        cols_affichage = [c for c in ["created_at", "public_id", "total_price", "status_code", "firstname", "lastname"] if c in orders_filtrees.columns]
        st.dataframe(
            orders_filtrees[cols_affichage].head(20) if cols_affichage else orders_filtrees.head(20),
            use_container_width=True
        )

    else:
        st.warning("Aucune commande récupérée.")

    if products_data and products_data.get("results"):
        st.subheader("Produits & stock")
        products = pd.DataFrame(products_data["results"])
        cols_produits = [c for c in ["id", "name", "reference", "price", "quantity"] if c in products.columns]
        st.dataframe(
            products[cols_produits] if cols_produits else products,
            use_container_width=True
        )
    else:
        st.warning("Aucun produit récupéré.")

else:
    st.info("Entre tes identifiants Wizishop dans le menu à gauche pour commencer.")
