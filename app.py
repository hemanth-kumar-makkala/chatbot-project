"""
app.py — Main Streamlit entry point.

Page routing is controlled by st.session_state["page"]:
  "auth"           → Login / Register
  "dashboard"      → Bot list
  "bot_management" → Configure bot + upload PDFs
  "chat"           → Chat with a specific bot
"""
import os
from dotenv import load_dotenv

# Load .env BEFORE any LlamaIndex / Gemini imports so env vars are available
load_dotenv()

import streamlit as st
from database import init_db

# Initialise DB on every cold start (no-op if tables already exist)
init_db()

# ── Page router ───────────────────────────────────────────────────────────────

def _sidebar() -> None:
    """Persistent sidebar shown on all authenticated pages."""
    with st.sidebar:
        st.markdown("## 🤖 Chatbot Platform")
        st.divider()
        username = st.session_state.get("username", "")
        st.markdown(f"👤 **{username}**")
        if st.button("🚪 Logout", use_container_width=True):
            # Clear all auth + navigation state
            for key in ["user_id", "username", "page", "active_bot_id"]:
                st.session_state.pop(key, None)
            st.rerun()


def main() -> None:
    # Default page
    if "page" not in st.session_state:
        st.session_state["page"] = "auth"

    page = st.session_state["page"]

    # Unauthenticated pages
    if page == "auth":
        from pages.auth import show as auth_show
        auth_show()
        return

    # All pages below require authentication
    if "user_id" not in st.session_state:
        st.session_state["page"] = "auth"
        st.rerun()
        return

    _sidebar()

    if page == "dashboard":
        from pages.dashboard import show as dashboard_show
        dashboard_show()

    elif page == "bot_management":
        from pages.bot_management import show as bot_mgmt_show
        bot_mgmt_show()

    elif page == "chat":
        from pages.chat import show as chat_show
        chat_show()

    else:
        # Catch-all: redirect to dashboard
        st.session_state["page"] = "dashboard"
        st.rerun()


if __name__ == "__main__" or True:
    # Streamlit always runs the file top-to-bottom, so we call main() here.
    main()
