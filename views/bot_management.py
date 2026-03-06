"""
pages/bot_management.py — Configure a bot's settings and manage documents.
Includes document upload (Vision RAG ingestion), per-document delete,
and provider/model selection.
"""
import traceback
import streamlit as st
from database import (
    get_bot_by_id,
    update_bot,
    add_document,
    get_documents_for_bot,
    delete_document as db_delete_document,
    get_all_provider_keys,
    get_provider_key,
    get_all_doc_ids_for_bot,
    delete_bot as db_delete_bot,
)
from encryption import decrypt_key
from rag_engine import ingest_document, delete_document as rag_delete_document, delete_all_bot_data
from model_service import fetch_models, PROVIDER_LABELS, FALLBACK_MODELS

_STATUS_BADGE = {
    "pending":   "🟡 Pending",
    "processed": "🟢 Processed",
    "failed":    "🔴 Failed",
}


def _get_available_providers(user_id: int) -> list[str]:
    """Return list of providers where the user has stored a key."""
    stored = get_all_provider_keys(user_id)
    return [k["provider"] for k in stored]


def _get_model_list(provider: str, user_id: int) -> list[str]:
    """Fetch models for a provider using the user's stored key. Cache in session_state."""
    cache_key = f"_models_cache_{provider}"

    # Return cached if available
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    # Try to fetch dynamically
    blob = get_provider_key(user_id, provider)
    if blob:
        try:
            api_key = decrypt_key(blob)
            models = fetch_models(provider, api_key)
            st.session_state[cache_key] = models
            return models
        except Exception:
            pass

    # Fallback
    fallback = FALLBACK_MODELS.get(provider, [])
    st.session_state[cache_key] = fallback
    return fallback


def show() -> None:
    bot_id: int = st.session_state.get("active_bot_id")
    if not bot_id:
        st.error("No bot selected.")
        return

    bot = get_bot_by_id(bot_id)
    if not bot:
        st.error("Bot not found.")
        return

    user_id: int = st.session_state["user_id"]

    # ── Navigation ───────────────────────────────────────────────────────────
    if st.button("← Back to Dashboard"):
        st.session_state["page"] = "dashboard"
        st.rerun()

    st.title(f"⚙️ Manage: {bot['bot_name']}")
    st.divider()

    # ── Get available providers ────────────────────────────────────────────
    available_providers = _get_available_providers(user_id)

    # ── Bot settings ─────────────────────────────────────────────────────────
    st.subheader("Bot Settings")

    # --- Provider & Model selection (outside the form for dynamic interaction) ---
    if available_providers:
        # Persist selected provider in session_state
        provider_state_key = f"_bot_provider_{bot_id}"
        if provider_state_key not in st.session_state:
            st.session_state[provider_state_key] = bot.get("provider", "google")

        current_provider = st.session_state[provider_state_key]
        if current_provider not in available_providers:
            current_provider = available_providers[0]
            st.session_state[provider_state_key] = current_provider

        provider_labels = [PROVIDER_LABELS.get(p, p) for p in available_providers]
        current_idx = available_providers.index(current_provider) if current_provider in available_providers else 0

        selected_label = st.selectbox(
            "🔌 API Provider",
            provider_labels,
            index=current_idx,
            key=f"provider_select_{bot_id}",
            help="Choose which AI provider this bot uses for chat.",
        )
        selected_provider = available_providers[provider_labels.index(selected_label)]
        st.session_state[provider_state_key] = selected_provider

        # --- Model dropdown (populated based on provider) ---
        model_state_key = f"_bot_model_{bot_id}"
        models = _get_model_list(selected_provider, user_id)

        if model_state_key not in st.session_state:
            st.session_state[model_state_key] = bot.get("model_name", "")

        current_model = st.session_state[model_state_key]
        if current_model not in models and models:
            current_model = models[0]
            st.session_state[model_state_key] = current_model

        model_idx = models.index(current_model) if current_model in models else 0

        selected_model = st.selectbox(
            "🧠 Model",
            models,
            index=model_idx,
            key=f"model_select_{bot_id}",
            help="Choose the specific model for this bot.",
        )
        st.session_state[model_state_key] = selected_model

        # Clear model cache if provider changed
        if selected_provider != bot.get("provider", "google"):
            old_cache = f"_models_cache_{bot.get('provider', 'google')}"
            st.session_state.pop(old_cache, None)
    else:
        st.warning(
            "⚠️ No API keys configured. Go to **Settings** to add at least one provider key."
        )
        selected_provider = bot.get("provider", "google")
        selected_model = bot.get("model_name", "gemini-2.5-flash")

    with st.form("update_bot_form"):
        new_name = st.text_input("Bot Name", value=bot["bot_name"])
        new_prompt = st.text_area(
            "System Prompt",
            value=bot["system_prompt"],
            height=160,
            help="Defines this bot's persona. Prepended to every LLM call.",
        )
        save_btn = st.form_submit_button("💾 Save Changes", use_container_width=True)

    if save_btn:
        if not new_name.strip():
            st.error("Bot name cannot be empty.")
        else:
            update_bot(
                bot_id, new_name, new_prompt,
                provider=selected_provider,
                model_name=selected_model,
            )
            st.success("Bot settings saved!")
            st.rerun()

    st.divider()



    # ── Document upload ───────────────────────────────────────────────────────
    st.subheader("📄 Upload Knowledge Documents")
    st.caption(
        "Upload PDF or DOCX files for this bot to learn from. "
        "Each page is rendered as an image and described by Gemini Vision — "
        "enabling precise table and layout extraction."
    )

    uploaded_file = st.file_uploader(
        "Choose a PDF or DOCX file",
        type=["pdf", "docx"],
        key=f"uploader_{bot_id}",
        label_visibility="collapsed",
    )

    if uploaded_file is not None:
        already_uploaded = [d["filename"] for d in get_documents_for_bot(bot_id)]
        if uploaded_file.name in already_uploaded:
            st.warning(
                f"**{uploaded_file.name}** has already been uploaded to this bot. "
                "Delete the existing record first, then re-upload."
            )
        else:
            doc_record = add_document(bot_id, uploaded_file.name)
            file_bytes = uploaded_file.read()
            uploaded_file.seek(0)  # reset stream position for safety

            with st.spinner(
                f"⏳ Processing **{uploaded_file.name}** with Vision RAG… "
                "Each page is being rendered and captioned — this may take a minute."
            ):
                try:
                    ingest_document(
                        file_bytes=file_bytes,
                        filename=uploaded_file.name,
                        bot_id=bot_id,
                        doc_id=doc_record["doc_id"],
                    )
                    st.success(
                        f"✅ **{uploaded_file.name}** has been processed and added "
                        "to the knowledge base!"
                    )
                except Exception as exc:
                    st.error(f"Ingestion failed: {exc}")
                    st.code(traceback.format_exc(), language="python")
                    print(traceback.format_exc())

            st.rerun()

    st.divider()

    # ── Document list ─────────────────────────────────────────────────────────
    st.subheader("Knowledge Base")
    docs = get_documents_for_bot(bot_id)

    if not docs:
        st.info("No documents uploaded yet. Add a PDF above to give this bot knowledge.")
    else:
        for doc in docs:
            badge = _STATUS_BADGE.get(doc["status"], doc["status"])
            page_info = f"({doc['page_count']} pages)" if doc.get("page_count") else ""

            with st.container(border=True):
                col_name, col_status, col_delete = st.columns([4, 1, 1])

                with col_name:
                    st.markdown(f"📄 **{doc['filename']}** {page_info}")

                with col_status:
                    st.markdown(badge)

                with col_delete:
                    if st.button("🗑️", key=f"del_{doc['doc_id']}", help="Delete this document"):
                        with st.spinner(f"Deleting **{doc['filename']}**…"):
                            try:
                                rag_delete_document(bot_id, doc["doc_id"])
                                db_delete_document(doc["doc_id"])
                                st.session_state[f"doc_deleted_{doc['doc_id']}"] = True
                            except Exception as exc:
                                st.error(f"Delete failed: {exc}")
                        st.rerun()

        # Show success message for 1 render cycle if a document was just deleted
        for doc in docs:
            if st.session_state.pop(f"doc_deleted_{doc['doc_id']}", False):
                st.success(f"**{doc['filename']}** was successfully deleted.")

    st.divider()


    # ── Danger zone: delete bot ───────────────────────────────────────────────
    st.subheader("⚠️ Danger Zone")
    with st.expander("🗑️ Delete This Bot", expanded=False):
        st.error(
            f"**Permanently delete '{bot['bot_name']}'?** "
            "This will remove the bot, all its documents, all ChromaDB embeddings, "
            "and all page images. This action cannot be undone."
        )
        confirm_name = st.text_input(
            "Type the bot name to confirm:",
            key="confirm_delete_bot",
            placeholder=bot["bot_name"],
        )
        if st.button("🗑️ Permanently Delete Bot", type="primary", use_container_width=True):
            if confirm_name.strip() != bot["bot_name"]:
                st.error("Bot name doesn't match. Deletion cancelled.")
            else:
                with st.spinner("Deleting bot and all associated data…"):
                    try:
                        doc_ids = get_all_doc_ids_for_bot(bot_id)
                        delete_all_bot_data(bot_id, doc_ids)
                        db_delete_bot(bot_id)
                        st.session_state.pop("active_bot_id", None)
                        st.session_state["page"] = "dashboard"
                    except Exception as exc:
                        st.error(f"Delete failed: {exc}")
                st.rerun()
