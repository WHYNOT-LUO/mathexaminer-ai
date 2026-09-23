"""Login / sign-up screen."""

from __future__ import annotations

import streamlit as st

from .. import db
from .components import alert

MIN_PASSWORD_LEN = 8


def render(client) -> None:
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown(
            """<div class="auth-header"><span class="logo-icon">📐</span>
            <h1>MathExaminer<br>AI</h1>
            <p class="tagline">CIE A-Level · Instant · Structured feedback</p></div>""",
            unsafe_allow_html=True,
        )
        if notice := st.session_state.pop("auth_notice", None):
            alert("success", notice)

        login_tab, signup_tab = st.tabs(["Log in", "Sign up"])

        with login_tab, st.form("login_form"):
            email = st.text_input("Email address", placeholder="you@example.com")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Log in →", use_container_width=True):
                if not email.strip() or not password:
                    alert("error", "Please enter your email and password.")
                else:
                    try:
                        st.session_state.profile = db.sign_in(client, email.strip(), password)
                        st.rerun()
                    except db.DBError as exc:
                        alert("error", str(exc))

        with signup_tab, st.form("signup_form"):
            name = st.text_input("Full name", placeholder="Ada Lovelace")
            email = st.text_input("Email address", placeholder="you@example.com", key="su_email")
            password = st.text_input(
                f"Password (min {MIN_PASSWORD_LEN} characters)", type="password", key="su_password"
            )
            code = st.text_input(
                "Teacher access code",
                type="password",
                help="Leave blank if you are a student. Teachers get this code from their school admin.",
            )
            if st.form_submit_button("Create account →", use_container_width=True):
                if not (name.strip() and email.strip() and password):
                    alert("error", "Please fill in your name, email and password.")
                elif len(password) < MIN_PASSWORD_LEN:
                    alert("error", f"Password must be at least {MIN_PASSWORD_LEN} characters.")
                else:
                    try:
                        db.sign_up(client, email.strip(), password, name.strip(), code)
                        st.session_state.auth_notice = (
                            "Account created. If email confirmation is enabled, check your inbox first, "
                            "then log in."
                        )
                        st.rerun()
                    except db.DBError as exc:
                        alert("error", str(exc))
