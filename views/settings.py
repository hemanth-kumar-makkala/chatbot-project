"""
pages/settings.py — Multi-Provider BYOK API Key Vault UI.

Users can securely store up to three API keys (Google, OpenAI, Anthropic).
Each key is immediately encrypted with Fernet (MASTER_KEY from .env) before storage.
Raw keys are never logged, displayed, or stored anywhere.
"""
import streamlit as st
from encryption import encrypt_key, decrypt_key
from database import save_provider_key, get_provider_key, get_all_provider_keys, delete_provider_key
from model_service import fetch_models, PROVIDER_LABELS

PROVIDERS = ["google", "openai", "anthropic"]

PROVIDER_HINTS = {
    "google":    "Get your key at https://aistudio.google.com/app/apikey (starts with AIza...)",
    "openai":    "Get your key at https://platform.openai.com/api-keys (starts with sk-...)",
    "anthropic": "Get your key at https://console.anthropic.com (starts with sk-ant-...)",
}

PROVIDER_ICONS = {
    "google":    "🔵",
    "openai":    "🟢",
    "anthropic": "🟠",
}


def show() -> None:
    st.title("🔑 API Key Vault")
    st.caption(
        "Store API keys for multiple providers. "
        "Each key is encrypted with AES-256 before storage."
    )
    st.divider()

    user_id: int = st.session_state["user_id"]

    # Get currently stored providers
    stored = {k["provider"] for k in get_all_provider_keys(user_id)}

    # ── Per-provider key sections ──────────────────────────────────────────
    for provider in PROVIDERS:
        icon = PROVIDER_ICONS[provider]
        label = PROVIDER_LABELS[provider]
        has_key = provider in stored

        with st.container(border=True):
            col_header, col_status = st.columns([4, 1])
            with col_header:
                st.markdown(f"### {icon} {label}")
            with col_status:
                if has_key:
                    st.success("✅ Active")
                else:
                    st.caption("⚠️ Not set")

            st.caption(PROVIDER_HINTS.get(provider, ""))

            with st.form(f"key_form_{provider}"):
                raw_key = st.text_input(
                    f"{label} API Key",
                    type="password",
                    placeholder="Paste your API key here…",
                    help="Key is encrypted immediately on save and never shown again.",
                    key=f"key_input_{provider}",
                )
                col_save, col_delete = st.columns([3, 1])
                with col_save:
                    save_btn = st.form_submit_button(
                        f"💾 Save {label} Key",
                        use_container_width=True,
                    )
                with col_delete:
                    delete_btn = st.form_submit_button(
                        "🗑️ Remove",
                        use_container_width=True,
                        disabled=not has_key,
                    )

            if save_btn:
                raw_key = raw_key.strip()
                if not raw_key:
                    st.error("Please enter an API key before saving.")
                elif len(raw_key) < 20:
                    st.error("That doesn't look like a valid API key — it's too short.")
                else:
                    try:
                        encrypted_blob = encrypt_key(raw_key)
                        save_provider_key(user_id, provider, encrypted_blob)

                        # Validate by attempting to fetch models
                        with st.spinner(f"Validating {label} key…"):
                            try:
                                models = fetch_models(provider, raw_key)
                                st.success(
                                    f"✅ {label} key saved and validated — "
                                    f"{len(models)} models available."
                                )
                            except Exception:
                                st.warning(
                                    f"⚠️ Key saved but validation failed. "
                                    f"The key may have restricted permissions."
                                )

                        # Wipe raw key from local scope
                        del raw_key
                        st.rerun()

                    except EnvironmentError as exc:
                        st.error(f"Server configuration error: {exc}")

            if delete_btn and has_key:
                delete_provider_key(user_id, provider)
                st.success(f"{label} key removed.")
                st.rerun()

    st.divider()

    # ── Help section ────────────────────────────────────────────────────────
    with st.expander("🛡️ How are my keys protected?"):
        st.markdown("""
        1. Each key is encrypted with **AES-256 (Fernet)** before it leaves your browser session.
        2. The encryption master key lives only on the server's `.env` file — never in the database.
        3. Your raw keys are **never logged, cached, or displayed** after submission.
        4. Decrypted keys are only held in memory for the milliseconds it takes to make an API call.
        
        > If you revoke a key on the provider's dashboard, simply paste the new key here.
        """)
