"""
pages/bot_management.py — Configure a bot's name/system_prompt and ingest PDFs.
The st.spinner wrapping ingest_document is per the UX requirement.
"""
import streamlit as st
from database import (
    get_bot_by_id,
    update_bot,
    add_document,
    get_documents_for_bot,
)
from rag_engine import ingest_document


# Status badge helper
_STATUS_BADGE = {
    "pending":   "🟡 Pending",
    "processed": "🟢 Processed",
    "error":     "🔴 Error",
}


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
    if st.button("← Back to Dashboard"):
        st.session_state["page"] = "dashboard"
        st.rerun()

    st.title(f"⚙️ Manage: {bot['name']}")
    st.divider()

    # ── Bot settings ─────────────────────────────────────────────────────────
    st.subheader("Bot Settings")
    with st.form("update_bot_form"):
        new_name = st.text_input("Bot Name", value=bot["name"])
        new_prompt = st.text_area(
            "System Prompt",
            value=bot["system_prompt"],
            height=160,
            help="This text is prepended to every LLM call. Define the bot's persona here.",
        )
        save_btn = st.form_submit_button("💾 Save Changes", use_container_width=True)

    if save_btn:
        if not new_name.strip():
            st.error("Bot name cannot be empty.")
        else:
            update_bot(bot_id, new_name, new_prompt)
            st.success("Bot settings saved!")
            st.rerun()

    st.divider()

    # ── Document upload ───────────────────────────────────────────────────────
    st.subheader("📄 Upload Knowledge Documents")
    st.caption(
        "Upload PDF files for this bot to learn from. "
        "Each file is parsed by LlamaParse and chunked using markdown-aware splitting."
    )

    uploaded_file = st.file_uploader(
        "Choose a PDF file",
        type=["pdf"],
        key=f"uploader_{bot_id}",
        label_visibility="collapsed",
    )

    if uploaded_file is not None:
        # Guard: don't re-ingest if the user just refreshed
        already_uploaded = [
            d["filename"] for d in get_documents_for_bot(bot_id)
        ]
        if uploaded_file.name in already_uploaded:
            st.warning(
                f"**{uploaded_file.name}** has already been uploaded to this bot. "
                "Upload a different file or delete the old record first."
            )
        else:
            # Create a 'pending' document record first so the user sees progress
            doc_record = add_document(bot_id, uploaded_file.name)
            pdf_bytes = uploaded_file.read()

            with st.spinner(
                f"⏳ Parsing **{uploaded_file.name}** with LlamaParse… "
                "This may take up to 60 seconds."
            ):
                try:
                    ingest_document(
                        pdf_bytes=pdf_bytes,
                        filename=uploaded_file.name,
                        bot_id=bot_id,
                        doc_id=doc_record["id"],
                    )
                    st.success(
                        f"✅ **{uploaded_file.name}** has been parsed, embedded, "
                        "and stored in the knowledge base!"
                    )
                except RuntimeError as exc:
                    st.error(f"Ingestion failed: {exc}")

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
            col_name, col_status = st.columns([4, 1])
            with col_name:
                st.markdown(f"📄 **{doc['filename']}**")
            with col_status:
                st.markdown(badge)
