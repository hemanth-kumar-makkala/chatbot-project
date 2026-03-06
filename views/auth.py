"""
pages/auth.py — Login and Register UI.
Sets session_state keys: user_id, username, page.
"""
import streamlit as st
from database import register_user, get_user


def show() -> None:
    st.set_page_config(page_title="Chatbot Platform — Sign In", page_icon="🤖")

    # Centered card layout
    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        st.markdown("## 🤖 Chatbot Platform")
        st.markdown("Build and chat with your own AI-powered bots.")
        st.divider()

        tab_login, tab_register = st.tabs(["🔑 Login", "📝 Register"])

        # ── Login ────────────────────────────────────────────────────────────
        with tab_login:
            with st.form("login_form"):
                username = st.text_input("Username", key="login_username")
                password = st.text_input("Password", type="password", key="login_password")
                submitted = st.form_submit_button("Login", use_container_width=True)

            if submitted:
                if not username or not password:
                    st.error("Please fill in both fields.")
                else:
                    user = get_user(username, password)
                    if user:
                        _set_logged_in(user)
                        st.rerun()
                    else:
                        st.error("Invalid username or password.")

        # ── Register ─────────────────────────────────────────────────────────
        with tab_register:
            with st.form("register_form"):
                new_username = st.text_input("Choose a username", key="reg_username")
                new_password = st.text_input("Choose a password", type="password", key="reg_password")
                new_password2 = st.text_input("Confirm password", type="password", key="reg_password2")
                submitted_reg = st.form_submit_button("Create Account", use_container_width=True)

            if submitted_reg:
                if not new_username or not new_password:
                    st.error("Please fill in all fields.")
                elif new_password != new_password2:
                    st.error("Passwords do not match.")
                elif len(new_password) < 4:
                    st.error("Password must be at least 4 characters.")
                else:
                    user = register_user(new_username, new_password)
                    if user:
                        _set_logged_in(user)
                        st.success("Account created! Redirecting…")
                        st.rerun()
                    else:
                        st.error(f"Username '{new_username}' is already taken.")


def _set_logged_in(user: dict) -> None:
    st.session_state["user_id"] = user["user_id"]
    st.session_state["username"] = user["username"]
    st.session_state["page"] = "dashboard"
