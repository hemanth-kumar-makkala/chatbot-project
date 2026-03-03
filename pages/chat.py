"""
pages/chat.py — Conversational chat interface with a specific bot.

Architecture:
  - Uses ContextChatEngine (rag_engine.get_chat_engine) for multi-turn memory.
  - Chat history is stored per-bot in st.session_state["chat_history_{bot_id}"]
    so switching between bots preserves each bot's history independently.
  - The engine is rebuilt on each page load (stateless Streamlit model) by
    replaying the stored history into the ChatMemoryBuffer.
"""
import streamlit as st
from database import get_bot_by_id
from rag_engine import get_chat_engine


def show() -> None:
    bot_id: int = st.session_state.get("active_bot_id")
    if not bot_id:
        st.error("No bot selected.")
        return

    bot = get_bot_by_id(bot_id)
    if not bot:
        st.error("Bot not found.")
        return

    # ── Navigation ───────────────────────────────────────────────────────────
    col_back, col_title = st.columns([1, 5])
    with col_back:
        if st.button("← Bots"):
            st.session_state["page"] = "dashboard"
            st.rerun()
    with col_title:
        st.title(f"💬 {bot['name']}")
    st.caption(f"*System prompt:* {bot['system_prompt'][:120]}…" if len(bot['system_prompt']) > 120 else f"*System prompt:* {bot['system_prompt']}")
    st.divider()

    # Key for this bot's history in session_state
    history_key = f"chat_history_{bot_id}"
    if history_key not in st.session_state:
        st.session_state[history_key] = []

    chat_history: list[dict] = st.session_state[history_key]

    # ── Clear history button (always rendered to keep widget tree stable) ─────
    if st.button("🗑️ Clear conversation", key="clear_chat", disabled=not chat_history):
        st.session_state[history_key] = []
        st.rerun()

    # ── Render existing messages ──────────────────────────────────────────────
    for msg in chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # ── Chat input ───────────────────────────────────────────────────────────
    user_input = st.chat_input(f"Ask {bot['name']} something…")

    if user_input:
        # Immediately show the user's message
        with st.chat_message("user"):
            st.markdown(user_input)
        chat_history.append({"role": "user", "content": user_input})

        # Build the engine (replays history into memory buffer each turn)
        # This is necessary because Streamlit re-runs the script from scratch.
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    engine = get_chat_engine(
                        bot_id=bot_id,
                        system_prompt=bot["system_prompt"],
                        chat_history=chat_history[:-1],  # exclude current user turn
                    )
                    response = engine.chat(user_input)
                    answer = str(response).strip()
                    if not answer or answer.lower() == "empty response":
                        answer = "I don't have any documents loaded yet to reference. Please upload a PDF in the **Manage Settings** page, or ask me a general question!"
                except Exception as exc:  # noqa: BLE001
                    import traceback
                    answer = f"⚠️ An error occurred: {exc}\n\n```\n{traceback.format_exc()}\n```"

            st.markdown(answer)

        chat_history.append({"role": "assistant", "content": answer})
        # Persist updated history back to session_state
        st.session_state[history_key] = chat_history
