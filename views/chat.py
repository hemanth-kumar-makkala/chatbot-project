"""
pages/chat.py — Chat interface with Intent Router + 2-step Escalation State Machine.

Escalation levels (per-bot in session_state):
  0 → Normal Vision RAG (default, resets on every new bot selection)
  1 → External intent detected; awaiting user confirmation
  2 → User confirmed escalation; call LLM Gateway without document context

Routes queries to the correct provider and model based on bot-level configuration.
"""
import streamlit as st
from database import get_bot_by_id, get_provider_key
from encryption import decrypt_key
from rag_engine import query_bot
from intent_router import classify
from llm_gateway import LLMGateway, PROVIDER_GOOGLE
from model_service import PROVIDER_LABELS

gateway = LLMGateway()


def _get_api_key_and_provider(bot: dict) -> tuple[str | None, str, str]:
    """
    Fetch and decrypt the API key for the bot's configured provider.
    Returns (decrypted_key, provider, model_name) or (None, "", "") if unavailable.
    """
    user_id = st.session_state["user_id"]
    provider = bot.get("provider", "google")
    model_name = bot.get("model_name", "")

    blob = get_provider_key(user_id, provider)
    if not blob:
        return None, provider, model_name
    try:
        key = decrypt_key(blob)
    except ValueError:
        return None, provider, model_name
    return key, provider, model_name


def show() -> None:
    bot_id: int = st.session_state.get("active_bot_id")
    if not bot_id:
        st.error("No bot selected.")
        return

    bot = get_bot_by_id(bot_id)
    if not bot:
        st.error("Bot not found.")
        return

    # ── Session state keys for this bot ───────────────────────────────────────
    history_key    = f"chat_history_{bot_id}"
    escalation_key = f"escalation_{bot_id}"

    if history_key not in st.session_state:
        st.session_state[history_key] = []
    if escalation_key not in st.session_state:
        st.session_state[escalation_key] = 0  # default: normal RAG

    chat_history: list[dict] = st.session_state[history_key]
    escalation_level: int    = st.session_state[escalation_key]

    # ── Navigation ───────────────────────────────────────────────────────────
    col_back, col_title = st.columns([1, 5])
    with col_back:
        if st.button("← Bots"):
            st.session_state["page"] = "dashboard"
            st.rerun()
    with col_title:
        st.title(f"💬 {bot['bot_name']}")

    # Show provider/model info
    provider_label = PROVIDER_LABELS.get(bot.get("provider", "google"), bot.get("provider", ""))
    model_name = bot.get("model_name", "")
    st.caption(
        f"*Provider:* {provider_label} · *Model:* `{model_name}` · "
        f"*System prompt:* {bot['system_prompt'][:80] + '…' if len(bot['system_prompt']) > 80 else bot['system_prompt'] or 'Not set'}"
    )
    st.divider()

    # ── API key check ─────────────────────────────────────────────────────────
    api_key, provider, model = _get_api_key_and_provider(bot)
    if not api_key:
        st.warning(
            f"⚠️ No API key found for **{provider_label}**. "
            "Please go to **Settings** and add your API key."
        )
        if st.button("⚙️ Go to Settings"):
            st.session_state["page"] = "settings"
            st.rerun()
        return

    # ── Clear history button ──────────────────────────────────────────────────
    if st.button("🗑️ Clear conversation", key="clear_chat", disabled=not chat_history):
        st.session_state[history_key] = []
        st.session_state[escalation_key] = 0
        st.rerun()

    # ── Render existing messages ──────────────────────────────────────────────
    for msg in chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # ── Escalation state: awaiting user confirmation ──────────────────────────
    if escalation_level == 1:
        st.info(
            "💡 This question seems to go beyond the uploaded documents. "
            "Do you want me to answer from general knowledge?"
        )
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("✅ Yes, use general knowledge", use_container_width=True):
                st.session_state[escalation_key] = 2
                # Re-answer the last user question with external context
                last_user_msg = next(
                    (m["content"] for m in reversed(chat_history) if m["role"] == "user"),
                    None,
                )
                if last_user_msg:
                    _generate_external_answer(
                        last_user_msg, bot, chat_history,
                        api_key, provider, model, history_key, escalation_key,
                    )
                st.rerun()
        with col_no:
            if st.button("❌ No, search documents only", use_container_width=True):
                st.session_state[escalation_key] = 0
                chat_history.append({
                    "role": "assistant",
                    "content": "Understood — I'll only reference your uploaded documents.",
                })
                st.session_state[history_key] = chat_history
                st.rerun()
        return  # Block the chat input while confirming

    # ── Chat input ───────────────────────────────────────────────────────────
    user_input = st.chat_input(f"Ask {bot['bot_name']} something…")

    if user_input:
        with st.chat_message("user"):
            st.markdown(user_input)
        chat_history.append({"role": "user", "content": user_input})

        intent = classify(user_input)

        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                answer = _route_and_generate(
                    intent=intent,
                    user_input=user_input,
                    bot=bot,
                    chat_history=chat_history,
                    api_key=api_key,
                    provider=provider,
                    model=model,
                    bot_id=bot_id,
                    escalation_key=escalation_key,
                )

            if answer is not None:
                st.markdown(answer)
                chat_history.append({"role": "assistant", "content": answer})

        st.session_state[history_key] = chat_history
        st.rerun()


def _route_and_generate(
    intent: str,
    user_input: str,
    bot: dict,
    chat_history: list[dict],
    api_key: str,
    provider: str,
    model: str,
    bot_id: int,
    escalation_key: str,
) -> str | None:
    """
    Route the query based on intent and return the answer text.
    Returns None if escalation is being triggered (UI will handle state).
    """
    try:
        if intent == "external" and st.session_state.get(escalation_key, 0) < 2:
            # Trigger escalation flow — don't answer yet
            st.session_state[escalation_key] = 1
            return None

        if intent == "external" or st.session_state.get(escalation_key, 0) == 2:
            # Confirmed external: call gateway without document context
            return gateway.generate(
                provider=provider,
                api_key=api_key,
                prompt=(
                    f"{bot['system_prompt']}\n\n"
                    f"User Question: {user_input}"
                ),
                model=model,
            )

        else:
            # "verbatim" or "quick_qa": use Vision RAG pipeline
            return query_bot(
                bot_id=bot_id,
                bot_custom_prompt=bot["system_prompt"],
                user_query=user_input,
                chat_history=chat_history[:-1],  # exclude current user turn
                api_key=api_key,
                provider=provider,
                model=model,
            )

    except KeyError as exc:
        st.session_state[escalation_key] = 0
        return (
            f"⚠️ **API Key Error:** {exc}\n\n"
            "Please go to **Settings** and update your API key."
        )
    except RuntimeError as exc:
        return f"⚠️ **Rate Limit:** {exc}"
    except Exception as exc:
        import traceback
        return f"⚠️ An unexpected error occurred:\n```\n{traceback.format_exc()}\n```"


def _generate_external_answer(
    user_query: str,
    bot: dict,
    chat_history: list[dict],
    api_key: str,
    provider: str,
    model: str,
    history_key: str,
    escalation_key: str,
) -> None:
    """Called when user confirms escalation — generate and append the answer."""
    try:
        answer = gateway.generate(
            provider=provider,
            api_key=api_key,
            prompt=(
                f"{bot['system_prompt']}\n\n"
                f"User Question: {user_query}"
            ),
            model=model,
        )
    except KeyError as exc:
        answer = f"⚠️ **API Key Error:** {exc}\n\nPlease update your key in Settings."
    except RuntimeError as exc:
        answer = f"⚠️ **Rate Limit:** {exc}"
    except Exception:
        import traceback
        answer = f"⚠️ An unexpected error occurred:\n```\n{traceback.format_exc()}\n```"
    finally:
        st.session_state[escalation_key] = 0

    chat_history.append({"role": "assistant", "content": answer})
    st.session_state[history_key] = chat_history
