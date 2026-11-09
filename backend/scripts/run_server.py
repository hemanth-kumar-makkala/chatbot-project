import asyncio
import os
import sys

import uvicorn
from dotenv import load_dotenv

# Load workspace .env into os.environ without viewing or printing it
load_dotenv()
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

from app.api.app import create_app  # noqa: E402
from app.api.config import ApiSettings  # noqa: E402
from app.ports.errors import ConfigurationError  # noqa: E402


def main() -> None:
    storage = os.environ.get("LOCAL_STORAGE_PATH")
    if not storage or not storage.strip():
        raise ConfigurationError(
            "LOCAL_STORAGE_PATH environment variable is required",
            error_code="CONFIGURATION_ERROR",
        )
    storage_path = storage.strip()
    if not os.path.isdir(storage_path):
        raise ConfigurationError(
            f"LOCAL_STORAGE_PATH directory does not exist: {storage_path}",
            error_code="CONFIGURATION_ERROR",
        )
    profile = os.environ.get("APP_PROFILE", "desktop-gpu").strip() or "desktop-gpu"
    cookie_secure_raw = os.environ.get("COOKIE_SECURE", "false").strip().lower()
    cookie_secure = cookie_secure_raw in ("true", "1", "yes", "on")
    retention_hours = int(os.environ.get("SESSION_UPLOAD_RETENTION_HOURS", "24"))
    host = os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get("PORT", "8000"))

    app = create_app(
        ApiSettings(
            profile=profile,  # type: ignore[arg-type]
            storage_dir=storage_path,
            cookie_secure=cookie_secure,
            session_upload_retention_hours=retention_hours,
        )
    )
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))
    if sys.platform == "win32":
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
