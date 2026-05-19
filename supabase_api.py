import requests
import streamlit as st

def get_headers():
    return {
        "apikey": st.secrets["SUPABASE_KEY"],
        "Authorization": f"Bearer {st.secrets["SUPABASE_KEY"]}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }

def get_url(table):
    return f"{st.secrets['SUPABASE_URL']}/rest/v1/{table}"

def select(table, query=""):
    r = requests.get(f"{get_url(table)}?{query}", headers=get_headers())
    if r.status_code == 200:
        return r.json()
    return []

def upsert(table, data, on_conflict):
    headers = get_headers()
    headers["Prefer"] = f"resolution=merge-duplicates,return=minimal"
    r = requests.post(
        f"{get_url(table)}?on_conflict={on_conflict}",
        headers=headers,
        json=data if isinstance(data, list) else [data]
    )
    return r.status_code in [200, 201, 204]

def count(table, query=""):
    headers = get_headers()
    headers["Prefer"] = "count=exact"
    r = requests.get(
        f"{get_url(table)}?{query}&select=id",
        headers=headers
    )
    count_header = r.headers.get("content-range", "0")
    try:
        return int(count_header.split("/")[-1])
    except:
        return 0
