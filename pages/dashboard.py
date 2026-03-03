"""
pages/dashboard.py — Bot list and create-new-bot entry point.
"""
import streamlit as st
from database import get_bots_for_user, create_bot


def show() -> None:
    user_id: int = st.session_state["user_id"]
    username: str = st.session_state["username"]

    st.title(f"🤖 Your Bots")
    st.caption(f"Logged in as **{username}**")
    st.divider()

    # ── Create new bot ───────────────────────────────────────────────────────
    with st.expander("➕ Create a New Bot", expanded=False):
        with st.form("create_bot_form"):
            bot_name = st.text_input("Bot Name", placeholder="e.g. HR Assistant")
            system_prompt = st.text_area(
                "System Prompt",
                placeholder="You are a helpful HR assistant…",
                height=120,
            )
            create_submitted = st.form_submit_button("Create Bot", use_container_width=True)

        if create_submitted:
            if not bot_name.strip():
                st.error("Bot name cannot be empty.")
            else:
                new_bot = create_bot(user_id, bot_name, system_prompt)
                st.success(f"Bot **{new_bot['name']}** created!")
                st.rerun()

    st.divider()

    # ── Bot list ─────────────────────────────────────────────────────────────
    bots = get_bots_for_user(user_id)

    if not bots:
        st.info("You haven't created any bots yet. Use the panel above to get started!")
        return

    for bot in bots:
        with st.container(border=True):
            col_info, col_btns = st.columns([3, 1])
            with col_info:
                st.markdown(f"### 🤖 {bot['name']}")
                preview = bot["system_prompt"][:120] + "…" if len(bot["system_prompt"]) > 120 else bot["system_prompt"]
                st.caption(preview or "*No system prompt set*")
            with col_btns:
                if st.button("💬 Chat", key=f"chat_{bot['id']}", use_container_width=True):
                    st.session_state["active_bot_id"] = bot["id"]
                    st.session_state["page"] = "chat"
                    # Clear previous chat history when switching bots
                    st.session_state.pop(f"chat_history_{bot['id']}", None)
                    st.rerun()
                if st.button("⚙️ Manage", key=f"manage_{bot['id']}", use_container_width=True):
                    st.session_state["active_bot_id"] = bot["id"]
                    st.session_state["page"] = "bot_management"
                    st.rerun()
