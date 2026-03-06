"""
app.py — Main Streamlit entry point and page router.

Pages:
  "auth"           → Login / Register
  "dashboard"      → Bot list & create
  "bot_management" → Configure bot + upload/delete PDFs
  "chat"           → Chat with a bot (Vision RAG + escalation)
  "settings"       → BYOK API Key Vault
"""
from dotenv import load_dotenv
load_dotenv()

import streamlit as st
from database import init_db

init_db()


def _sidebar() -> None:
    """Persistent sidebar for all authenticated pages."""
    with st.sidebar:
        st.markdown("## 🤖 Chatbot Platform")
        st.divider()
        st.markdown(f"👤 **{st.session_state.get('username', '')}**")
        st.divider()

        if st.button("🏠 Dashboard", use_container_width=True):
            st.session_state["page"] = "dashboard"
            st.rerun()

        if st.button("🔑 Settings", use_container_width=True):
            st.session_state["page"] = "settings"
            st.rerun()

        st.divider()

        if st.button("🚪 Logout", use_container_width=True):
            for key in ["user_id", "username", "page", "active_bot_id", "api_provider"]:
                st.session_state.pop(key, None)
            st.rerun()





def main() -> None:
    st.set_page_config(
        page_title="AI Chatbot Platform",
        page_icon="🤖",
        layout="centered",
        initial_sidebar_state="expanded",
    )


    # ── DEV LOGIN BYPASS ──
    if "user_id" not in st.session_state:
        from database import register_user, get_user
        # Create or fetch default dev user
        dev_user = get_user("dev_admin", "admin123")
        if not dev_user:
            dev_user = register_user("dev_admin", "admin123")
        
        st.session_state["user_id"] = dev_user["user_id"]
        st.session_state["username"] = dev_user["username"]
        st.session_state["page"] = "dashboard"
        st.rerun()
        return

    page = st.session_state.get("page", "dashboard")

    # Unauthenticated - bypassed above but kept for structure
    if page == "auth":
        st.session_state["page"] = "dashboard"
        st.rerun()
        return



    _sidebar()

    if page == "dashboard":
        from views.dashboard import show as dashboard_show
        dashboard_show()

    elif page == "bot_management":
        from views.bot_management import show as bot_mgmt_show
        bot_mgmt_show()

    elif page == "chat":
        from views.chat import show as chat_show
        chat_show()

    elif page == "settings":
        from views.settings import show as settings_show
        settings_show()

    else:
        st.session_state["page"] = "dashboard"
        st.rerun()


if __name__ == "__main__" or True:
    main()
 