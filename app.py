import streamlit as st
from supabase import create_client, Client
import uuid
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# --- PAGE CONFIG ---
st.set_page_config(page_title="Support Portal Pro", layout="wide", page_icon="🎫")

# --- CONNECTION ---
@st.cache_resource
def get_supabase_client():
    try:
        base_url = st.secrets["connections"]["supabase"]["url"]
        api_key = st.secrets["connections"]["supabase"]["key"]
        clean_url = base_url.rstrip("/") + "/"
        return create_client(clean_url, api_key)
    except Exception as e:
        st.error(f"Connection Error: {e}")
        st.stop()

supabase: Client = get_supabase_client()

# --- EMAIL LOGIC ---
def send_email(to_email, subject, body):
    try:
        s = st.secrets["smtp"]
        msg = MIMEMultipart()
        msg['From'], msg['To'], msg['Subject'] = s["sender"], to_email, subject
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP(s["server"], s["port"])
        server.starttls()
        server.login(s["user"], s["password"])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"SMTP Error: {e}")
        return False

# --- CANNED RESPONSES ---
CANNED_RESPONSES = {
    "Select a template...": "",
    "Ticket Received": "Hi,\n\nWe have received your ticket and are looking into it.\n\nBest,\nSupport Team",
    "More Info Needed": "Hi,\n\nCould you please provide a screenshot or more details about the error?\n\nBest,\nSupport Team",
    "Password Reset": "Hi,\n\nTo reset your password, please go to the login page and click 'Forgot Password'.\n\nBest,\nSupport Team"
}

# --- SESSION STATE ---
if "user" not in st.session_state: st.session_state.user = None

# --- AUTH LOGIC ---
def handle_auth():
    st.sidebar.header("🔐 Staff Access")
    if not st.session_state.user:
        with st.sidebar.expander("Login"):
            e = st.text_input("Email")
            p = st.text_input("Password", type="password")
            if st.button("Login"):
                try:
                    res = supabase.auth.sign_in_with_password({"email": e, "password": p})
                    st.session_state.user = res.user
                    st.rerun()
                except: st.error("Invalid credentials")
    else:
        st.sidebar.success(f"User: {st.session_state.user.email}")
        if st.sidebar.button("Logout"):
            supabase.auth.sign_out()
            st.session_state.user = None
            st.rerun()

handle_auth()

# --- ROLE HELPERS ---
role = st.session_state.user.user_metadata.get("role") if st.session_state.user else None
is_admin = role == "admin"
is_agent = role == "agent"
is_staff = is_admin or is_agent

# --- HELPER: LOG SYSTEM ACTIVITY ---
def log_activity(ticket_id, message):
    supabase.table("ticket_notes").insert({
        "ticket_id": ticket_id,
        "note_text": f"⚙️ SYSTEM: {message}",
        "author_email": st.session_state.user.email
    }).execute()

# --- POP-UP DIALOG FUNCTION ---
@st.dialog("📝 Ticket Manager", width="large")
def ticket_popup(ticket):
    tab_details, tab_reply = st.tabs(["Details & Activity Log", "📧 Reply to Customer"])

    # === TAB 1: DETAILS & LOG ===
    with tab_details:
        c1, c2 = st.columns(2)
        c1.write(f"**Ticket ID:** `{ticket['id']}`")
        c2.write(f"**Customer:** {ticket['customer_name']}")
        
        st.divider()
        
        # --- EDITABLE FIELDS ---
        col_status, col_pri, col_assign = st.columns(3)
        
        # Status Dropdown
        status_opts = ["New", "Open", "Deferred", "Closed"]
        curr_status_idx = status_opts.index(ticket['status']) if ticket['status'] in status_opts else 0
        new_status = col_status.selectbox("Status", status_opts, index=curr_status_idx)

        # Priority Dropdown
        new_priority = col_pri.selectbox("Priority", ["Low", "Medium", "High"], index=["Low", "Medium", "High"].index(ticket['priority']))
        
        # Assignee Dropdown
        if is_admin:
            staff_res = supabase.table("tickets").select("assigned_to").execute()
            staff_list = sorted(list(set([r['assigned_to'] for r in staff_res.data if r['assigned_to']] + ["Unassigned", st.session_state.user.email])))
            current_assign = ticket['assigned_to'] if ticket['assigned_to'] in staff_list else "Unassigned"
            new_assign = col_assign.selectbox("Assigned To", staff_list, index=staff_list.index(current_assign))
        else:
            current_assign = ticket['assigned_to'] or "Unassigned"
            col_assign.text_input("Assigned To", value=current_assign, disabled=True)
            new_assign = ticket['assigned_to']

        # --- CLOSING LOGIC ---
        if new_status == "Closed" and ticket['status'] != "Closed":
            st.warning("⚠️ **Closing Ticket:** Please enter resolution.")
            resolution_text = st.text_area("✅ Resolution Summary", placeholder="What was the fix?")
            
            if st.button("✅ Close & Resolve", type="primary", use_container_width=True):
                if resolution_text:
                    supabase.table("tickets").update({
                        "status": "Closed", "priority": new_priority, "assigned_to": new_assign,
                        "resolved_at": datetime.now().isoformat(), "resolution_summary": resolution_text
                    }).eq("id", ticket['id']).execute()
                    
                    # Log Close
                    log_activity(ticket['id'], f"Ticket Closed. Resolution: {resolution_text}")
                    
                    # Email Customer
                    subject = f"Ticket #{ticket['id']} Resolved"
                    body = f"Hi {ticket['customer_name']},\n\nYour ticket is resolved:\n{resolution_text}\n\nBest,\nSupport Team"
                    send_email(ticket['email'], subject, body)
                    
                    st.success("Resolved!"); st.rerun()
                else: st.error("Resolution required.")
        
        # --- STANDARD SAVE LOGIC ---
        else:
            # Issue Description
            with st.expander("View Issue Description", expanded=False):
                st.info(ticket['description'])
                if ticket['attachment_url']: st.image(ticket['attachment_url'], width=300)

            st.divider()
            
            # --- FULL ACTIVITY LOG ---
            st.write("📜 **Ticket Activity Log**")
            notes_res = supabase.table("ticket_notes").select("*").eq("ticket_id", ticket['id']).order("created_at", desc=True).execute()
            
            with st.container(height=300): # Taller container for full history
                if notes_res.data:
                    for note in notes_res.data:
                        author = note.get('author_email') or "Unknown"
                        
                        # Timestamp Formatting
                        try:
                            dt = datetime.fromisoformat(note['created_at'].replace('Z', '+00:00'))
                            fmt_time = dt.strftime("%d %b %H:%M")
                        except: fmt_time = ""

                        # Styling based on type
                        if "SYSTEM:" in note['note_text']:
                            # System Log Style
                            st.caption(f"⚙️ {fmt_time} - {note['note_text'].replace('⚙️ SYSTEM:', '')}")
                        elif "EMAIL SENT" in note['note_text']:
                            # Email Log Style
                            with st.chat_message("assistant"):
                                st.write(f"**Email Out ({fmt_time}):**")
                                st.caption(note['note_text'].split('\n', 1)[1] if '\n' in note['note_text'] else note['note_text'])
                        else:
                            # User Note Style
                            with st.chat_message("user"):
                                st.write(f"**{author}** ({fmt_time}):")
                                st.write(note['note_text'])
                        st.divider()
                else: st.caption("No history yet.")

            new_note = st.text_input("Add manual note...")

            if st.button("💾 Save Changes", use_container_width=True):
                # 1. Detect Changes for the Log
                changes = []
                if new_status != ticket['status']: changes.append(f"Status: {ticket['status']} → {new_status}")
                if new_priority != ticket['priority']: changes.append(f"Priority: {ticket['priority']} → {new_priority}")
                if new_assign != ticket['assigned_to']: changes.append(f"Assignee: {ticket['assigned_to']} → {new_assign}")
                
                # 2. Update DB
                supabase.table("tickets").update({
                    "status": new_status, "priority": new_priority, "assigned_to": new_assign
                }).eq("id", ticket['id']).execute()
                
                # 3. Write Logs
                if changes:
                    log_activity(ticket['id'], "Updated: " + ", ".join(changes))
                
                if new_note:
                    supabase.table("ticket_notes").insert({
                        "ticket_id": ticket['id'], "note_text": new_note, "author_email": st.session_state.user.email
                    }).execute()
                
                st.success("Updated!"); st.rerun()

    # === TAB 2: REPLY ===
    with tab_reply:
        st.write(f"**To:** {ticket['email']}")
        tmpl = st.selectbox("Templates", list(CANNED_RESPONSES.keys()))
        body_val = CANNED_RESPONSES[tmpl] if tmpl != "Select a template..." else ""
        
        email_body = st.text_area("Message", value=body_val, height=250)
        
        if st.button("✈️ Send Email"):
            if email_body:
                if send_email(ticket['email'], f"Re: Ticket #{ticket['id']}", email_body):
                    st.success("Sent!")
                    log_activity(ticket['id'], f"EMAIL SENT TO CUSTOMER:\n{email_body}")
                    st.rerun()
                else: st.error("Failed.")

# --- NAVIGATION ---
menu = ["New Ticket", "Track Ticket"]
if is_staff: menu.append("Staff Dashboard")
choice = st.sidebar.selectbox("Menu", menu)

# --- PAGES ---
if choice == "New Ticket":
    st.title("🎫 Open Request")
    with st.form("main"):
        c1, c2 = st.columns(2)
        name, email = c1.text_input("Name"), c2.text_input("Email")
        cat = st.selectbox("Category", ["Beacon CRM", "Dashboards", "Data", "General Request", "New Feature", "Other"])
        pri = st.select_slider("Urgency", options=["Low", "Medium", "High"], value="Medium")
        msg = st.text_area("Issue")
        if st.form_submit_button("Submit"):
            t_id = str(uuid.uuid4())[:8].upper()
            supabase.table("tickets").insert({
                "id": t_id, "customer_name": name, "email": email, "description": msg,
                "priority": pri, "category": cat, "status": "New"
            }).execute()
            send_email(email, f"Ticket #{t_id}", "Received.")
            st.success(f"Created #{t_id}")

elif choice == "Track Ticket":
    st.title("🔍 Status")
    with st.form("track_ticket_form"):
        tid = st.text_input("ID").upper()
        tem = st.text_input("Email")
        st.caption("Enter the ticket ID and the same email used to create the ticket.")
        submitted = st.form_submit_button("Check")

    if submitted:
        if not tid or not tem:
            st.error("Please enter both Ticket ID and Email.")
        elif not tid.isdigit():
            st.error("Ticket ID must be a number.")
        else:
            res = supabase.rpc("public_track_ticket", {"ticket_id": int(tid), "email_in": tem}).execute()
            if res.data:
                t = res.data[0]
                st.write(f"**Status:** {t['status']}")
                if t.get('resolution_summary'): st.success(f"Resolution: {t['resolution_summary']}")
            else:
                st.warning("No ticket found for that ID and email.")

elif choice == "Staff Dashboard":
    st.title("📈 Command Center")
    ticket_query = supabase.table("tickets").select("*")
    if is_agent and st.session_state.user:
        user_email = st.session_state.user.email
        ticket_query = ticket_query.or_(f"assigned_to.is.null,assigned_to.eq.{user_email}")
    res = ticket_query.order("created_at", desc=True).execute()
    if res.data:
        df = pd.DataFrame(res.data)
        
        # Dashboard Metrics
        m1, m2, m3 = st.columns(3)
        m1.metric("New", len(df[df['status'] == 'New']))
        m2.metric("Urgent", len(df[df['priority'] == 'High']))
        m3.metric("Total", len(df))
        
        st.divider()
        st.caption("Select a ticket to manage.")
        
        event = st.dataframe(
            df[["id", "category", "priority", "status", "assigned_to", "customer_name", "created_at"]],
            on_select="rerun", selection_mode="single-row",
            use_container_width=True, hide_index=True
        )
        
        if event.selection.rows:
            idx = event.selection.rows[0]
            clean_ticket = df.iloc[idx].to_dict()
            ticket_popup(clean_ticket)
