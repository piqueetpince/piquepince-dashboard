import streamlit as st
import pandas as pd
import plotly.express as px
from wizishop_api import get_token, get_orders, get_products

st.set_page_config(
    page_title="Pique&Pince — Dashboard",
    page_icon="📊",
    layout="wide"
)

st.title("Pique&Pince — Dashboard ventes")

with st.sidebar:
    st.header("Connexion Wizishop")
    email = st.text_input("Email", type="default")
    password = st.text_input("Mot de passe", type="password")
    connect_btn = st.button("Se connecter", use_container_width=True)

if connect_btn:
    with st.spinner("Connexion en cours..."):
        token, shop_id = get_token(email, password)

    if token:
        st.session_state["token"] = token
        st.session_state["shop_id"] = shop_id
        st.sidebar.success("Connecté !")
    else:
        st.sidebar.error("Identifiants incorrects")

if "token" in st.session_state:
    token = st.session_state["token"]
    shop_id = st.session_state["shop_id"]

    with st.spinner("Chargement des données..."):
        orders_data = get_orders(token, shop_id)
        products_data = get_products(token, shop_id)

    if orders_data:
        orders = pd.DataFrame(orders_data.get("orders", orders_data))

        if "created_at" in orders.columns:
            orders["date"] = pd.to_datetime(orders["created_at"])
            orders["mois"] = orders["date"].dt.to_period("M").astype(str)

        st.subheader("Vue d'ensemble")
        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric("Commandes ce mois", 
                      len(orders[orders["mois"] == orders["mois"].max()]))
        with col2:
            if "total_price" in orders.columns:
                ca_mois = orders[orders["mois"] == orders["mois"].max()]["total_price"].sum()
                st.metric("CA ce mois (Wizishop)", f"{ca_mois:.0f} €")
        with col3:
            st.metric("Commandes total", len(orders))

        st.subheader("Commandes par mois")
        if "mois" in orders.columns:
            commandes_par_mois = orders.groupby("mois").size().reset_index(name="commandes")
            fig = px.bar(commandes_par_mois, x="mois", y="commandes",
                        color_discrete_sequence=["#1D9E75"])
            st.plotly_chart(fig, use_container_width=True)

        st.subheader("Dernières commandes")
        st.dataframe(orders.tail(20), use_container_width=True)

    else:
        st.warning("Aucune commande récupérée — vérifie ta connexion.")

    if products_data:
        st.subheader("Produits & stock")
        products = pd.DataFrame(products_data.get("products", products_data))
        st.dataframe(products, use_container_width=True)

else:
    st.info("Entre tes identifiants Wizishop dans le menu à gauche pour commencer.")
