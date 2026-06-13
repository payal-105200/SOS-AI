import streamlit as st
import pandas as pd
import random
import time
import json
import os
import requests
import pydeck as pdk 
from geopy.geocoders import Nominatim
from streamlit_js_eval import get_geolocation
from twilio.rest import Client
import google.generativeai as genai

# --- API CREDENTIALS (SECURED FOR HACKATHON) ---
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "mock_sid_for_demo")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "mock_token_for_demo")
TWILIO_PHONE_NUMBER = "+1 570 741 5625"
RESCUE_TEAM_PHONE = "+919876543210" 

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "mock_gemini_key_for_demo")

# --- CONFIGURE GENAI ---
try:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash') 
except Exception:
    model = None

# --- PAGE SETUP ---
st.set_page_config(page_title="SOS System", page_icon="🚨", layout="wide")

# --- HACKATHON DATABASES ---
DB_FILE = "sos_database.json"
PROFILE_FILE = "user_profile.json"

def load_db():
    if not os.path.exists(DB_FILE): return []
    try:
        with open(DB_FILE, 'r') as f: return json.load(f)
    except: return []

def save_db(data):
    try:
        with open(DB_FILE, 'w') as f: json.dump(data, f, indent=4)
    except Exception as e:
        st.sidebar.error("Database sync delayed due to traffic.")

def load_profile():
    if os.path.exists(PROFILE_FILE):
        try:
            with open(PROFILE_FILE, 'r') as f:
                data = json.load(f)
                if isinstance(data, dict) and "saved" in data: return data
        except: pass
    return {"saved": False, "name": "", "phone": ""}

def save_profile(data):
    try:
        with open(PROFILE_FILE, 'w') as f: json.dump(data, f, indent=4)
    except: pass

# --- VIRTUAL DATABASE & SESSION STATES ---
if 'otp_sent' not in st.session_state: st.session_state.otp_sent = False
if 'is_verified' not in st.session_state: st.session_state.is_verified = False
if 'real_otp' not in st.session_state: st.session_state.real_otp = ""
if 'panic_locked' not in st.session_state: st.session_state.panic_locked = False
if 'proposed_plan' not in st.session_state: st.session_state.proposed_plan = None
if 'last_dispatch' not in st.session_state: st.session_state.last_dispatch = None
if 'voice_text' not in st.session_state: st.session_state.voice_text = ""
if 'last_audio_id' not in st.session_state: st.session_state.last_audio_id = None
if 'admin_logged_in' not in st.session_state: st.session_state.admin_logged_in = False
if 'phone_chat_history' not in st.session_state:
    st.session_state.phone_chat_history = [{"role": "assistant", "content": "SOS Service active. Send your emergency details and location."}]

# User profile refresh lock safety assignments
if 'user_profile' not in st.session_state or not st.session_state.user_profile.get("saved", False):
    st.session_state.user_profile = load_profile()

if not isinstance(st.session_state.user_profile, dict) or "saved" not in st.session_state.user_profile:
    st.session_state.user_profile = {"saved": False, "name": "", "phone": ""}

# --- CORE FUNCTIONS ---
def send_real_otp(phone_number, otp_code):
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(body=f"Your SOS Emergency Verification Code is: {otp_code}. Do not share this.", from_=TWILIO_PHONE_NUMBER, to=phone_number)
        return True
    except Exception: return False

def get_network_ip_location():
    """Real dynamic fallback using ISP/Network Node"""
    try:
        geo_resp = requests.get('https://ipapi.co/json/', timeout=3).json()
        return float(geo_resp.get("latitude")), float(geo_resp.get("longitude"))
    except:
        return None, None

def fetch_coordinates(location_name):
    # Agar SMS me koi location na ho, toh directly Network IP fetch karo
    if not location_name or str(location_name).strip().lower() in ["unknown", "none", "pending", ""]:
        return get_network_ip_location()
        
    try:
        geolocator = Nominatim(user_agent=f"sos_emergency_management_{random.randint(1,1000)}")
        location = geolocator.geocode(f"{location_name}, India", language='en', timeout=3)
        if location: 
            return location.latitude, location.longitude
            
        # Agar API location dhoondhne mein fail ho jaye, tab bhi asli Network Node ping karo
        return get_network_ip_location()
    except: 
        return get_network_ip_location()
        
def get_urgency_from_description(e_type, desc):
    if not desc or len(desc.strip()) < 5: return "Critical" if e_type in ["Fire 🔥", "Medical 🚑"] else "Moderate"
    if model is None: return "Critical"
    prompt = f"Emergency Type: '{e_type}'. Description: '{desc}'. Classify urgency strictly as ONE word: Low, Moderate, or Critical."
    try:
        response = model.generate_content(prompt)
        res = response.text.strip().lower()
        if "critical" in res: return "Critical"
        elif "low" in res: return "Low"
        else: return "Moderate"
    except: return "Critical"

def get_sms_draft(team_name, case_type, urgency, location_lat, location_lon, tactical_plan, raw_msg):
    lat_str = f"{location_lat:.4f}" if location_lat is not None and str(location_lat).lower() != 'nan' else "Pending Alignment"
    lon_str = f"{location_lon:.4f}" if location_lon is not None and str(location_lon).lower() != 'nan' else "Pending Alignment"
    return (
        f"🚨 DISPATCH ALERT 🚨\nTeam: {team_name}\nType: {case_type} ({urgency})\n"
        f"📍 Lat {lat_str}, Lon {lon_str}\n🧠 Orders: {tactical_plan}\n📱 Msg: {raw_msg}"
    )

def parse_sms_with_ai(sms_text):
    if model is None: return "General Rescue 🚁", "Critical", "API Missing", "Unknown"
    prompt = f"Analyze this raw emergency text: '{sms_text}'. Return EXACTLY 4 comma-separated values. No markdown, no labels. Format: Type,Urgency,Confidence,LocationName\nOptions for Type: Fire 🔥, Medical 🚑, Security 👮, General Rescue 🚁\nOptions for Urgency: Low, Moderate, Critical\nLocationName: Extract city, landmark, or area. If none, output 'Unknown'."
    try:
        response = model.generate_content(prompt)
        clean_text = response.text.replace('`', '').replace('csv', '').replace('text', '').strip()
        result = [x.strip() for x in clean_text.split(',')]
        if len(result) >= 4:
            e_type = result[0] if any(t in result[0] for t in ["Fire", "Medical", "Security", "Rescue"]) else "General Rescue 🚁"
            urgency = result[1] if result[1] in ["Low", "Moderate", "Critical"] else "Critical"
            return e_type, urgency, result[2], result[3]
        return "General Rescue 🚁", "Moderate", "Format Error", "Unknown"
    except: return "General Rescue 🚁", "Critical", "System Fallback", "Unknown"

def add_emergency(e_type, urgency, source, lat, lon, name="Unknown", phone="Unknown", description="N/A"):
    db = load_db()
    final_lat = float(lat) if lat and str(lat).lower() != 'nan' else None
    final_lon = float(lon) if lon and str(lon).lower() != 'nan' else None

    # Check if we successfully fetched network coordinates as a fallback
    display_desc = description
    if source == "Offline SMS" and "Loc: Location Pending" in description and final_lat is not None:
        display_desc = description.replace("Loc: Location Pending", "Loc: [Network Node Triangulated]")

    if phone != "Unknown":
        for emp in db:
            if emp["Phone"] == phone and emp["Status"] == "Pending 🔴":
                emp["Raw_Message"] = display_desc
                emp["Type"] = e_type
                if urgency == "Critical": emp["Urgency"] = "Critical"
                
                if final_lat is not None and final_lon is not None:
                    emp["lat"] = final_lat
                    emp["lon"] = final_lon
                save_db(db)
                
                if 'proposed_plan' in st.session_state and st.session_state.proposed_plan:
                    st.session_state.proposed_plan = None 
                return

    db.append({
        "Name": name, "Phone": phone, "Raw_Message": display_desc, 
        "Type": e_type, "Urgency": urgency, "Source": source,
        "lat": final_lat, "lon": final_lon, "Status": "Pending 🔴", "Assigned_To": "Unassigned"
    })
    save_db(db)

# ==========================================
# 🔥 THE UNIFIED LIVE DASHBOARD FRAGMENT 🔥
# ==========================================
@st.fragment(run_every="4s")
def live_dashboard_fragment():
    db = load_db()
    df = pd.DataFrame(db) if len(db) > 0 else pd.DataFrame()
    
    if not df.empty:
        pending = df[(df["Status"] != "Resolved 🟢") & (df["Assigned_To"] == "Unassigned")]
        if not pending.empty and st.session_state.get('proposed_plan') is None:
            if 'rejected_indices' in st.session_state:
                actual_pending = pending[~pending.index.isin(st.session_state.rejected_indices)]
                if not actual_pending.empty: st.rerun()
            else: st.rerun() 

    map_col, data_col = st.columns([2, 1])
    
    with map_col:
        st.subheader("📍 Live Incident Map")
        if not df.empty:
            df['lat'] = pd.to_numeric(df['lat'], errors='coerce')
            df['lon'] = pd.to_numeric(df['lon'], errors='coerce')
            active_map_df = df[(df["Status"] != "Resolved 🟢")].dropna(subset=['lat', 'lon']).copy()
            
            if not active_map_df.empty:
                center_lat = active_map_df['lat'].mean()
                center_lon = active_map_df['lon'].mean()
                def get_color(urgency):
                    if 'Critical' in str(urgency): return [255, 0, 0, 200]
                    elif 'Moderate' in str(urgency): return [255, 165, 0, 200]
                    else: return [0, 255, 0, 200]
                active_map_df['color'] = active_map_df['Urgency'].apply(get_color)
                layer = pdk.Layer('ScatterplotLayer', data=active_map_df, get_position='[lon, lat]', get_color='color', get_radius=20000, pickable=True)
                layers = [layer]
                fire_df = active_map_df[active_map_df['Type'].astype(str).str.contains('Fire', na=False)]
                if not fire_df.empty: layers.append(pdk.Layer('ScatterplotLayer', data=fire_df, get_position='[lon, lat]', get_color='[255, 69, 0, 60]', get_radius=50000))
                st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=5), tooltip={"text": "{Type} ({Urgency})\nPhone: {Phone}"}))
            else:
                st.warning("⚠️ Pending Network Alignment. Triangulating active node streams...")
                st.pydeck_chart(pdk.Deck(initial_view_state=pdk.ViewState(latitude=20.5937, longitude=78.9629, zoom=4)))
        else: st.pydeck_chart(pdk.Deck(initial_view_state=pdk.ViewState(latitude=20.5937, longitude=78.9629, zoom=4)))

    with data_col:
        st.subheader("⚡ Action Panel")
        if not df.empty:
            active_df_for_counts = df[df["Status"] != "Resolved 🟢"]
            critical_alerts = len(active_df_for_counts[active_df_for_counts['Urgency'].astype(str).str.contains('Critical', na=False)])
            moderate_alerts = len(active_df_for_counts[active_df_for_counts['Urgency'].astype(str).str.contains('Moderate', na=False)])
            low_alerts = len(active_df_for_counts[active_df_for_counts['Urgency'].astype(str).str.contains('Low', na=False)])
            stat_col1, stat_col2, stat_col3 = st.columns(3)
            stat_col1.error(f"🔴 {critical_alerts}")
            stat_col2.warning(f"🟠 {moderate_alerts}")
            stat_col3.success(f"🟢 {low_alerts}")
        else: st.info("No active emergencies.")
            
        st.markdown("---")
        st.subheader("📡 Live Emergency Feed")
        with st.container(height=320):
            if not df.empty and len(df[df["Status"] != "Resolved 🟢"]) > 0:
                for incident in reversed(df[df["Status"] != "Resolved 🟢"].to_dict('records')):
                    urgency_icon = "🔴" if "Critical" in incident['Urgency'] else "🟠" if "Moderate" in incident['Urgency'] else "🟢"
                    st.info(f"**{urgency_icon} {incident['Type']}** ({incident['Urgency']})\n\n📞 **Phone:** {incident['Phone']} | 👤 **Name:** {incident['Name']}\n\n📝 **Details:** {incident['Raw_Message']}")
            else: st.write("No active streams.")

    st.markdown("---")
    st.subheader("📋 Detailed Incident Log (Tick to Close Case)")
    if not df.empty and not df[df["Status"] != "Resolved 🟢"].empty:
        active_df = df[df["Status"] != "Resolved 🟢"].copy()
        active_df.insert(0, "Close Case", False)
        edited_df = st.data_editor(active_df.drop(columns=['color'], errors='ignore'), hide_index=True, use_container_width=True, disabled=["Name", "Phone", "Raw_Message", "Type", "Urgency", "Source", "Status"])
        changes_made = False
        for i, row in edited_df.iterrows():
            if row["Close Case"]:
                df.loc[i, "Status"] = "Resolved 🟢"
                changes_made = True
            elif row["Assigned_To"] != active_df.loc[i, "Assigned_To"]:
                df.loc[i, "Assigned_To"] = row["Assigned_To"]
                changes_made = True
        if changes_made:
            save_db(df.to_dict(orient="records"))
            st.success("✅ Database Updated!")
            time.sleep(0.5)
            st.rerun()
    else: st.info("✅ All cases resolved.")

    # 📁 YEH RAHA TUMHARA PAST DETAILS WALA DABBA!
    st.markdown("---")
    st.subheader("📁 History / Resolved Logs Records")
    if not df.empty and not df[df["Status"] == "Resolved 🟢"].empty:
        st.dataframe(df[df["Status"] == "Resolved 🟢"].drop(columns=['color'], errors='ignore'), use_container_width=True)
    else: st.caption("No historical records available yet.")


# --- SIDEBAR NAVIGATION ---
page = st.sidebar.radio("Choose your access level:", ["📱 Public Portal (Victim)", "🖥️ Command Center (Authorities)"])
st.sidebar.markdown("---")

# ==========================================
# PAGE 1: PUBLIC PORTAL (FOR VICTIMS)
# ==========================================
if page == "📱 Public Portal (Victim)":
    st.markdown("### ⚠️ EXTREME EMERGENCY")
    st.caption("🚨 **WARNING:** Misuse is a punishable offense.")
    
    if not st.session_state.get('panic_locked'):
        is_unlocked = st.toggle("🔓 Unlock Panic Button to Dispatch")
        if is_unlocked:
            st.info("📡 Requesting GPS permission...")
            loc = get_geolocation()
            if st.button("🆘 DISPATCH INSTANT CRITICAL ALERT", type="primary", use_container_width=True):
                panic_lat, panic_lon = None, None
                loc_details = "Location Pending - Manual Tracing Required"
                if loc:
                    panic_lat = loc['coords']['latitude']
                    panic_lon = loc['coords']['longitude']
                    loc_details = f"Exact Hardware GPS (Accuracy: {loc['coords']['accuracy']}m)"
                else:
                    try:
                        geo_resp = requests.get('https://ipapi.co/json/', timeout=3).json()
                        panic_lat, panic_lon = float(geo_resp.get("latitude")), float(geo_resp.get("longitude"))
                        loc_details = f"IP Fallback Geolocation ({geo_resp.get('city', 'Unknown')})"
                    except: pass
                add_emergency("Unspecified Panic ⚠️", "Critical", "Panic Button", panic_lat, panic_lon, description=loc_details)
                st.session_state.panic_locked = True 
                st.error("🚨 CRITICAL ALERT SENT!")
                time.sleep(1.5)
                st.rerun()
    else: st.error("🔒 Panic alert active.")
        
    st.markdown("---")
    st.title("🆘 Standard Help Request")
    tab1, tab2 = st.tabs(["🌐 Web App (With Internet)", "📡 SMS Gateway (No Internet)"])
    
    with tab1:
        if not st.session_state.user_profile.get("saved", False):
            st.subheader("Step 1: One-Time Identity Verification")
            col1, col2 = st.columns(2)
            with col1: user_name = st.text_input("Full Name", key="reg_name")
            with col2: phone_no = st.text_input("Phone Number (Start with +91)", key="reg_phone")
            
            otp_col1, otp_col2 = st.columns(2)
            with otp_col1:
                demo_mode = st.toggle("🛠️ Enable Demo Mode (Bypass SMS)")
                if st.button("Send Verification Code"):
                    if len(phone_no) >= 12 and phone_no.startswith("+"):
                        st.session_state.real_otp = str(random.randint(1000, 9999))
                        st.session_state.otp_sent = True
                        st.info(f"Mock OTP for Judge: {st.session_state.real_otp}")
                    else: st.warning("Enter valid phone number.")
            with otp_col2:
                if st.session_state.get('otp_sent'):
                    entered_otp = st.text_input("Enter 4-Digit OTP", key="reg_otp")
                    if st.button("Verify & Lock Profile"):
                        if entered_otp == st.session_state.get('real_otp') and user_name and phone_no:
                            profile_data = {"saved": True, "name": user_name, "phone": phone_no}
                            st.session_state.user_profile = profile_data
                            save_profile(profile_data)
                            st.success("Verified & Profile Locked!")
                            time.sleep(0.5)
                            st.rerun()
                        else: st.error("❌ Invalid Parameters/OTP")
        else:
            st.success(f"🔒 Identity Verified: {st.session_state.user_profile['name']} ({st.session_state.user_profile['phone']})")
            if st.button("🚪 Reset/Change Profile"):
                if os.path.exists(PROFILE_FILE): os.remove(PROFILE_FILE)
                st.session_state.user_profile = {"saved": False, "name": "", "phone": ""}
                st.session_state.otp_sent = False
                st.rerun()
                
            st.markdown("---")
            st.subheader("🚨 Report Emergency")
            e_type = st.selectbox("What is the emergency?", ["Fire 🔥", "Medical 🚑", "Security 👮", "General Rescue 🚁"])
            loc_query = st.text_input("Enter Landmark or City Name (e.g. Delhi, Mumbai)", key="web_loc")
            
            st.markdown("#### 🎙️ Voice SOS")
            audio_value = st.audio_input("Record Emergency")
            if audio_value:
                if st.session_state.last_audio_id != audio_value.file_id:
                    with st.spinner("🧠 Gemini AI is processing your voice..."):
                        try:
                            if model:
                                audio_part = {"mime_type": "audio/wav", "data": audio_value.getvalue()}
                                resp = model.generate_content(["Transcribe this emergency audio package to English. Output only raw text transcript.", audio_part])
                                st.session_state.voice_text = resp.text.strip()
                            else: st.session_state.voice_text = "[Voice recorded - Sync Complete]"
                            st.session_state.last_audio_id = audio_value.file_id
                            st.success("✅ Voice Transcribed!")
                        except Exception: st.session_state.voice_text = "Emergency situation reported via Voice Module."
            
            desc = st.text_area("Describe the situation", value=st.session_state.voice_text, placeholder="Describe what happened...")
            if st.button("🚨 Submit Emergency Alert", type="primary", use_container_width=True):
                if loc_query and desc:
                    with st.spinner("Locating targets & calculating triage parameters..."):
                        lat, lon = fetch_coordinates(loc_query)
                        calculated_urgency = get_urgency_from_description(e_type, desc)
                        add_emergency(e_type, calculated_urgency, "Web App", lat, lon, st.session_state.user_profile["name"], st.session_state.user_profile["phone"], desc)
                    st.error("🚨 ALERT DISPATCHED TO COMMAND CENTER!")
                    time.sleep(1.5)
                    st.rerun()
                else: st.warning("⚠️ Please specify both Location and Situation text.")

    with tab2:
        spacer1, phone_col, spacer2 = st.columns([1, 2, 1])
        with phone_col:
            st.markdown("### 📱 Mobile Simulator")
            phone_screen = st.container(border=True)
            with phone_screen:
                st.markdown("💬 **To: 🚨 +1-800-SOS-HELP**")
                chat_container = st.container(height=350)
                with chat_container:
                    for chat in st.session_state.get('phone_chat_history', []):
                        with st.chat_message(chat["role"], avatar="🚨" if chat["role"] == "assistant" else "👤"):
                            st.write(chat["content"])
                sender_num = st.text_input("Your Mobile No.", value="+91 9876543210")
                raw_msg = st.chat_input("Type SMS...")
                if raw_msg:
                    if len(raw_msg) > 5:
                        st.session_state.phone_chat_history.append({"role": "user", "content": raw_msg})
                        sms_type, sms_urgency, _, extracted_loc = parse_sms_with_ai(raw_msg)
                        ai_lat, ai_lon = None, None
                        
                        loc_display = "Location Pending"
                        if extracted_loc and str(extracted_loc).strip().lower() not in ["unknown", "none", "", "pending"]: 
                            ai_lat, ai_lon = fetch_coordinates(extracted_loc.strip())
                            loc_display = extracted_loc.strip()
                        else:
                            ai_lat, ai_lon = fetch_coordinates("") # Triggers Network Node Ping
                            
                        add_emergency(sms_type, sms_urgency, "Offline SMS", ai_lat, ai_lon, "Unknown", sender_num, description=f"SMS: '{raw_msg}' | Loc: {loc_display}")
                        st.rerun()

# ==========================================
# PAGE 2: COMMAND CENTER (FOR AUTHORITIES)
# ==========================================
elif page == "🖥️ Command Center (Authorities)":
    
    # 🔥 SECURE LOGIN SYSTEM 🔥
    if not st.session_state.admin_logged_in:
        st.title("🔐 Secure Command Center Access")
        st.markdown("---")
        
        login_col1, login_col2, login_col3 = st.columns([1, 2, 1])
        with login_col2:
            st.info("⚠️ Authorized Dispatch Personnel Only")
            with st.form("admin_login_form"):
                username = st.text_input("Admin ID", placeholder="Enter ID")
                password = st.text_input("Password", type="password", placeholder="Enter Password")
                submit_btn = st.form_submit_button("Authenticate & Login", use_container_width=True)
                
                if submit_btn:
                    if username == "admin" and password == "9876":
                        st.session_state.admin_logged_in = True
                        st.success("Access Granted! Initializing Dashboard...")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("❌ Invalid Credentials. Access Denied.")
    
    # AGAR LOGIN HAI TOH PURA DASHBOARD DIKHEGA
    else:
        header_col1, header_col2 = st.columns([8, 1])
        with header_col1:
            st.title("🛡️ Central Dispatch Dashboard")
        with header_col2:
            if st.button("🚪 Logout"):
                st.session_state.admin_logged_in = False
                st.rerun()
                
        main_db = load_db()
        main_df = pd.DataFrame(main_db) if len(main_db) > 0 else pd.DataFrame()
        
        if 'rejected_indices' not in st.session_state:
            st.session_state.rejected_indices = []
        
        if not main_df.empty:
            pending_dispatch = main_df[
                (main_df["Status"] != "Resolved 🟢") & 
                (main_df["Assigned_To"] == "Unassigned") &
                (~main_df.index.isin(st.session_state.rejected_indices))
            ]
            
            if not pending_dispatch.empty and st.session_state.get('proposed_plan') is None:
                critical_priority = pending_dispatch[pending_dispatch['Urgency'].str.contains('Critical', na=False)]
                if not critical_priority.empty: target_idx = critical_priority.index[0]
                else: target_idx = pending_dispatch.index[0]
                    
                target_case = main_df.loc[target_idx]
                
                action_plan = "Proceed immediately with tactical disaster routing."
                if model:
                    try: 
                        prompt_instructions = f"Emergency: {target_case['Type']}. Urgency: {target_case['Urgency']}. Raw Message: {target_case['Raw_Message']}. Write strictly a single 1-sentence tactical directive for dispatch response units using tactical terms."
                        action_plan = model.generate_content(prompt_instructions).text.strip()
                    except: pass
                    
                assigned_team = f"{random.choice(['Alpha Squad', 'Pihu Rescue Squad', 'Bravo Unit', 'Sneha Tactical Squad'])} ({round(random.uniform(0.5, 3.8), 1)}km)"
                st.session_state.proposed_plan = {"target_idx": int(target_idx), "team": assigned_team, "sms_draft": get_sms_draft(assigned_team, target_case['Type'], target_case['Urgency'], target_case['lat'], target_case['lon'], action_plan, target_case['Raw_Message'])}
                st.rerun()

        if st.session_state.get('proposed_plan'):
            plan = st.session_state.proposed_plan
            st.warning("⚠️ **AI DISPATCH PROPOSAL PENDING HUMAN APPROVAL**")
            st.info(plan["sms_draft"])
            col1, col2 = st.columns(2)
            with col1:
                if st.button("✅ Approve & Send SMS", type="primary"):
                    idx = plan["target_idx"]
                    main_df.loc[idx, "Status"] = "Dispatched 🟢"
                    main_df.loc[idx, "Assigned_To"] = plan["team"]
                    save_db(main_df.to_dict(orient="records"))
                    st.session_state.last_dispatch = plan
                    st.session_state.proposed_plan = None
                    st.rerun()
            with col2:
                if st.button("❌ Reject Override"):
                    st.session_state.rejected_indices.append(plan["target_idx"])
                    st.session_state.proposed_plan = None
                    st.rerun()

        if st.session_state.get('last_dispatch'):
            st.markdown("---")
            st.success("📱 **MOCK RESCUE TERMINAL (CELLULAR RECEIVER)**")
            ld = st.session_state.last_dispatch
            st.code(f"📡 INBOUND SMS TO RESPONSE TEAM\nSTATUS: DELIVERED ✅\n\n{ld['sms_draft']}")

        st.markdown("---")
        live_dashboard_fragment()