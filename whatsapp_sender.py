import streamlit as st

# --- Page Configuration ---
st.set_page_config(
    page_title="NCSF WhatsApp Sender",
    layout="wide",
    initial_sidebar_state="expanded",
)

import pandas as pd
import requests
import time
import io
import re
import os

# --- Helper: normalize phone numbers ---
def normalize_number(raw):
    s = re.sub(r"\D", "", raw)
    if s.startswith("65") and len(s) == 10:
        return s
    if len(s) == 8:
        return "65" + s
    if len(s) == 9 and s.startswith("0"):
        return "65" + s[1:]
    return None

# --- Credentials storage (local fallback) ---
CRED_FILE = "config.txt"

def load_credentials():
    creds = st.secrets.get("whatsapp", None)
    if creds and all(k in creds for k in ("access_token","phone_number_id","business_account_id")):
        return creds["access_token"], creds["phone_number_id"], creds["business_account_id"]
    if os.path.exists(CRED_FILE):
        lines = [l.strip() for l in open(CRED_FILE) if l.strip()]
        if len(lines) >= 3:
            return lines[0], lines[1], lines[2]
    return "", "", ""

def save_credentials(token, phone_id, business_id):
    with open(CRED_FILE, "w") as f:
        f.write(f"{token}\n{phone_id}\n{business_id}\n")

ACCESS_TOKEN, PHONE_NUMBER_ID, BUSINESS_ACCOUNT_ID = load_credentials()

# --- Fetch WhatsApp Templates ---
@st.cache_data(ttl=3600)
def get_whatsapp_templates(token, business_id):
    url = f"https://graph.facebook.com/v18.0/{business_id}/message_templates"
    params = {"access_token": token, "fields": "name,components,status,language"}
    resp = requests.get(url, params=params)
    data = resp.json().get("data", [])
    return [tpl for tpl in data if tpl.get("status") == "APPROVED"]

# --- Initialize session state ---
st.session_state.setdefault("numbers", [])
st.session_state.setdefault("success", 0)
st.session_state.setdefault("failure", 0)

# --- Sidebar: Credentials & Controls ---
st.sidebar.header("🔑 WhatsApp API Credentials")
token_input       = st.sidebar.text_area("Access Token", value=ACCESS_TOKEN, height=100)
phone_id_input    = st.sidebar.text_input("Phone Number ID", value=PHONE_NUMBER_ID)
business_id_input = st.sidebar.text_input("Business Account ID", value=BUSINESS_ACCOUNT_ID)
if st.sidebar.button("Save Credentials"):
    if token_input and phone_id_input and business_id_input:
        save_credentials(token_input, phone_id_input, business_id_input)
        st.sidebar.success("Credentials saved.")
    else:
        st.sidebar.error("All fields are required.")

ACCESS_TOKEN        = token_input or ACCESS_TOKEN
PHONE_NUMBER_ID    = phone_id_input or PHONE_NUMBER_ID
BUSINESS_ACCOUNT_ID = business_id_input or BUSINESS_ACCOUNT_ID

st.sidebar.markdown("---")

# --- Main Layout ---
col_config, col_main = st.columns([4, 6], gap="large")

with col_config:
    st.header("Config")

    # File uploader
    file = st.file_uploader(
        "Upload leads CSV (one column, no header; e.g. 6598578141, +65 98578141, 98578141)",
        type="csv"
    )

    # Buttons row: Send & Refresh
    btn_send, btn_refresh = st.columns([4, 1])
    send_btn = btn_send.button("Send Messages")
    if btn_refresh.button("🔄", help="Refresh template list"):
        get_whatsapp_templates.clear()
        st.success("Template list refreshed.")

    # Template dropdown
    try:
        templates = get_whatsapp_templates(ACCESS_TOKEN, BUSINESS_ACCOUNT_ID)
        names = [tpl["name"] for tpl in templates]
        template_name = st.selectbox("WhatsApp Template", names)
    except Exception as e:
        st.error(f"Failed to load templates: {e}")
        template_name = st.text_input("Template Name", "hello_world")

    # Template preview
    if 'templates' in locals() and template_name:
        selected = next((tpl for tpl in templates if tpl["name"] == template_name), None)
        if selected:
            st.subheader("Template Preview")
            for comp in selected.get("components", []):
                if comp.get("type") == "HEADER" and comp.get("format") == "TEXT":
                    st.markdown(f"**Header:** {comp.get('text','')}  ")
                elif comp.get("type") == "BODY":
                    st.code(comp.get("text",""), language="text")

with col_main:
    st.title("NCSF WhatsApp Lead Messenger")
    st.markdown("---")

    # ★ Process the CSV immediately on upload
    if file:
        df_raw = pd.read_csv(file, header=None, names=["Raw Input"]).dropna()
        df_raw["Phone Number"] = df_raw["Raw Input"].astype(str).apply(normalize_number)
        valid_df = df_raw.dropna(subset=["Phone Number"])
        st.session_state["numbers"] = valid_df["Phone Number"].tolist()

    # Handle sending
    if send_btn:
        numbers = st.session_state["numbers"]
        if not numbers:
            st.error("No valid leads to send. Upload a valid CSV.")
        else:
            total = len(numbers)
            success = failure = 0
            log = []
            progress = st.progress(0)
            status_area = st.empty()

            # Determine language code
            tpl = next((t for t in templates if t["name"] == template_name), None)
            lang_entry = tpl.get("language") if tpl else {}
            lang_code = (
                lang_entry.get("code") if isinstance(lang_entry, dict)
                else (lang_entry or "en_US")
            )

            for i, num in enumerate(numbers, start=1):
                url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
                headers = {
                    "Authorization": f"Bearer {ACCESS_TOKEN}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "messaging_product": "whatsapp",
                    "to": num,
                    "type": "template",
                    "template": {"name": template_name, "language": {"code": lang_code}}
                }
                resp = requests.post(url, headers=headers, json=payload)
                try:
                    err = resp.json().get("error", {}).get("message", "")
                except Exception:
                    err = resp.text

                if resp.status_code == 200:
                    success += 1
                    status = "Sent"
                else:
                    failure += 1
                    status = f"Failed ({resp.status_code}): {err}"

                log.append([num, resp.status_code, err])
                status_area.write(f"{i}/{total} → {num}: {status}")
                progress.progress(i/total)
                time.sleep(0.05)

            st.session_state["success"] = success
            st.session_state["failure"] = failure
            if failure > 0:
                st.success(f"Completed: {success} sent, {failure} failed out of {total} leads.")
            else:
                st.success(f"Completed: {success} sent out of {total} leads.")
            # Download log
            df_log = pd.DataFrame(log, columns=["Phone Number","Status Code","Error Message"])
            buf = io.StringIO()
            df_log.to_csv(buf, index=False)
            st.download_button("Download Log", buf.getvalue(), "ncsf_log.csv", "text/csv")

    # Metrics
    leads_loaded = len(st.session_state["numbers"])
    succ = st.session_state["success"]
    fail = st.session_state["failure"]
    m1, m2, m3 = st.columns(3)
    m1.metric("Leads Loaded", leads_loaded)
    m2.metric("Successful", succ if succ > 0 else "-")
    m3.metric("Failed", fail if fail > 0 else "-")

    # Preview Uploaded Leads (NOW below the stats)
    if st.session_state["numbers"]:
        st.subheader("Preview Uploaded Leads")
        df_preview = valid_df.reset_index(drop=True).rename(
            columns={"Raw Input": "Input", "Phone Number": "Normalized"}
        )
        df_preview.index = df_preview.index + 1
        df_preview.index.name = "No."
        st.dataframe(df_preview, use_container_width=True)

    st.markdown("---")
    st.caption("Built for NCSF Singapore · 2025")
