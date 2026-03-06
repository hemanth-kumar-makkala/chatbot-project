"""
pages/dashboard.py — Bot list and create-new-bot entry point.
"""
import streamlit as st
from database import get_bots_for_user, create_bot, get_all_provider_keys
from model_service import PROVIDER_LABELS, FALLBACK_MODELS


def show() -> None:
    user_id: int = st.session_state["user_id"]
    username: str = st.session_state["username"]

    st.title(f"🤖 Your Bots")
    st.caption(f"Logged in as **{username}**")
    st.divider()

    # Get available providers for bot creation
    stored_keys = get_all_provider_keys(user_id)
    available_providers = [k["provider"] for k in stored_keys]

    # ── Create new bot ───────────────────────────────────────────────────────
    with st.expander("➕ Create a New Bot", expanded=False):
        if not available_providers:
            st.warning(
                "⚠️ Add at least one API key in **Settings** before creating a bot."
            )

        with st.form("create_bot_form"):
            bot_name = st.text_input("Bot Name", placeholder="e.g. HR Assistant")
            system_prompt = st.text_area(
                "System Prompt",
                placeholder="You are a helpful HR assistant…",
                height=120,
            )

            # Provider selection
            if available_providers:
                provider_labels = [PROVIDER_LABELS.get(p, p) for p in available_providers]
                selected_label = st.selectbox("🔌 API Provider", provider_labels)
                selected_provider = available_providers[provider_labels.index(selected_label)]

                # Model selection (using fallback list in form context)
                models = FALLBACK_MODELS.get(selected_provider, [])
                selected_model = st.selectbox("🧠 Model", models) if models else ""
            else:
                selected_provider = "google"
                selected_model = "gemini-2.5-flash"
                st.info("Default: Google Gemini (add keys in Settings to choose a provider)")

            create_submitted = st.form_submit_button("Create Bot", use_container_width=True)

        if create_submitted:
            if not bot_name.strip():
                st.error("Bot name cannot be empty.")
            elif not available_providers:
                st.error("Please add an API key in Settings first.")
            else:
                new_bot = create_bot(
                    user_id, bot_name, system_prompt,
                    provider=selected_provider,
                    model_name=selected_model,
                )
                st.success(f"Bot **{new_bot['bot_name']}** created!")
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
                st.markdown(f"### 🤖 {bot['bot_name']}")
                provider_label = PROVIDER_LABELS.get(bot.get("provider", ""), "")
                model_name = bot.get("model_name", "")
                preview = bot["system_prompt"][:120] + "…" if len(bot["system_prompt"]) > 120 else bot["system_prompt"]
                st.caption(
                    f"🔌 {provider_label} · `{model_name}`\n\n"
                    f"{preview or '*No system prompt set*'}"
                )
            with col_btns:
                if st.button("💬 Chat", key=f"chat_{bot['bot_id']}", use_container_width=True):
                    st.session_state["active_bot_id"] = bot["bot_id"]
                    st.session_state["page"] = "chat"
                    st.session_state.pop(f"chat_history_{bot['bot_id']}", None)
                    st.rerun()
                if st.button("⚙️ Manage", key=f"manage_{bot['bot_id']}", use_container_width=True):
                    st.session_state["active_bot_id"] = bot["bot_id"]
                    st.session_state["page"] = "bot_management"
                    st.rerun()
