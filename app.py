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

        st.subheader("Colonnes disponibles (debug)")
        st.write(list(orders.columns))

        st.subheader("Aperçu des 5 premières commandes")
        st.dataframe(orders.head(5), use_container_width=True)

    else:
        st.warning("Aucune commande récupérée.")

    if products_data and products_data.get("results"):
        st.subheader("Colonnes produits (debug)")
        products = pd.DataFrame(products_data["results"])
        st.write(list(products.columns))

        st.subheader("Aperçu des 5 premiers produits")
        st.dataframe(products.head(5), use_container_width=True)
    else:
        st.warning("Aucun produit récupéré.")

else:
    st.info("Entre tes identifiants Wizishop dans le menu à gauche pour commencer.")
