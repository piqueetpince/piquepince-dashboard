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
        status, message, _, _ = get_token(email, password)
    st.sidebar.write(f"Status: {status}")
    st.sidebar.write(f"Réponse: {message}")

else:
    st.info("Entre tes identifiants Wizishop dans le menu à gauche pour commencer.")
