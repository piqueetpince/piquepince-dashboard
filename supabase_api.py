import requests
import streamlit as st

def get_headers():
    return {
        "apikey": st.secrets["SUPABASE_KEY"],
        "Authorization": f"Bearer {st.secrets['SUPABASE_KEY']}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }

def get_url(table):
    return f"{st.secrets['SUPABASE_URL']}/rest/v1/{table}"

def select(table, query="", limit=None):
    headers = get_headers()
    all_results = []
    page_size = 1000
    offset = 0

    while True:
        range_end = offset + page_size - 1
        req_headers = {**headers, "Range-Unit": "items", "Range": f"{offset}-{range_end}"}
        if query:
            url = f"{get_url(table)}?{query}&limit={page_size}&offset={offset}"
        else:
            url = f"{get_url(table)}?limit={page_size}&offset={offset}"

        r = requests.get(url, headers=req_headers)

        if r.status_code not in [200, 206]:
            break

        data = r.json()
        if not data:
            break

        all_results.extend(data)

        if limit and len(all_results) >= limit:
            all_results = all_results[:limit]
            break

        if len(data) < page_size:
            break

        offset += page_size

    return all_results

def upsert(table, data, on_conflict):
    headers = get_headers()
    url = f"{get_url(table)}?on_conflict={on_conflict}"
    payload = data if isinstance(data, list) else [data]
    r = requests.post(url, headers=headers, json=payload)
    if r.status_code not in [200, 201, 204]:
        st.warning(f"Erreur upsert {table}: {r.status_code} — {r.text[:200]}")
        return False
    return True

def insert(table, data):
    headers = get_headers()
    headers["Prefer"] = "return=minimal"
    url = get_url(table)
    payload = data if isinstance(data, list) else [data]
    r = requests.post(url, headers=headers, json=payload)
    if r.status_code not in [200, 201, 204]:
        st.warning(f"Erreur insert {table}: {r.status_code} — {r.text[:200]}")
        return False
    return True

def delete(table, query):
    headers = get_headers()
    headers["Prefer"] = "return=minimal"
    r = requests.delete(f"{get_url(table)}?{query}", headers=headers)
    if r.status_code not in [200, 204]:
        st.warning(f"Erreur delete {table}: {r.status_code} — {r.text[:200]}")
        return False
    return True

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
