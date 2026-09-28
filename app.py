"""MathExaminer AI - Streamlit entrypoint. Run with: streamlit run app.py"""

import streamlit as st

from mathexaminer import db
from mathexaminer.config import ConfigError, load_settings
from mathexaminer.ui import auth_page, student_page, teacher_page
from mathexaminer.ui.components import alert, esc, footer, inject_css, welcome_banner

st.set_page_config(page_title="MathExaminer AI", page_icon="📐", layout="centered", initial_sidebar_state="collapsed")
inject_css()

try:
    settings = load_settings()
except ConfigError as exc:
    st.markdown(
        f'<div class="setup-box"><strong>MathExaminer AI is not configured yet.</strong><br>{esc(exc)}<br><br>'
        "See the <em>Setup</em> section of the README.</div>",
        unsafe_allow_html=True,
    )
    st.stop()

# One Supabase client per browser session: it carries the signed-in user's JWT. The service client
# (server-side only, bypasses row-level security) is used for the few trusted writes in db.py.
if "sb_client" not in st.session_state:
    st.session_state.sb_client = db.new_client(settings)
    st.session_state.sb_service = db.new_service_client(settings)
client, service = st.session_state.sb_client, st.session_state.sb_service
profile = st.session_state.get("profile")

if profile is None:
    auth_page.render(client)
else:
    left, right = st.columns([5, 1])
    with left:
        welcome_banner(profile.name, profile.email, profile.role)
    with right:
        st.markdown("<br><br>", unsafe_allow_html=True)
        if st.button("Sign out", width="stretch"):
            db.sign_out(client)
            st.session_state.clear()
            st.rerun()

    if profile.role == "teacher":
        teacher_page.render(client, service, profile)
    elif profile.role == "student":
        student_page.render(client, service, profile, settings)
    else:
        alert("error", "Your account has an unknown role. Please contact an administrator.")

footer(settings.deepseek_model)
