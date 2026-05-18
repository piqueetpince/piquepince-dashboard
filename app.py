import streamlit as st
import pandas as pd
from wizishop_api import get_token, get_shops, get_orders, get_products

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

if connect_btn:
    with st.spinner("Connexion en cours..."):
        token, account_id = get_token(email, password)
    if token:
        shops = get_shops(token, account_id)
        if shops:
            shop_id = shops[0].get("id")
            st.session_state["token"] = token
            st.session_state["account_id"] = account_id
            st.session_state["shop_id"] = shop_id
            st.sidebar.success(f"Connecté ! Boutique #{shop_id}")
        else:
            st.sidebar.error("Connecté mais aucune boutique trouvée")
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

        if "created_at" in orders.columns:
            orders["date"] = pd.to_datetime(orders["created_at"])
            orders["mois"] = orders["date"].dt.to_period("M").astype(str)

        st.subheader("Vue d'ensemble")
        col1, col2, col3 = st.columns(3)
        with col1:
            mois_max = orders["mois"].max()
            st.metric("Commandes ce mois", len(orders[orders["mois"] == mois_max]))
        with col2:
            if "total_price" in orders.columns:
                ca = orders[orders["mois"] == mois_max]["total_price"].sum()
                st.metric("CA ce mois (Wizishop)", f"{ca:.0f} €")
        with col3:
            st.metric("Commandes total", orders_data.get("total", len(orders)))

        st.subheader("Commandes par mois")
        par_mois = orders.groupby("mois").size().reset_index(name="commandes")
        st.bar_chart(par_mois.set_index("mois"))

        st.subheader("Dernières commandes")
        st.dataframe(orders.tail(20), use_container_width=True)

    else:
        st.warning("Aucune commande récupérée.")

    if products_data and products_data.get("results"):
        st.subheader("Produits & stock")
        products = pd.DataFrame(products_data["results"])
        st.dataframe(products, use_container_width=True)

else:
    st.info("Entre tes identifiants Wizishop dans le menu à gauche pour commencer.")
