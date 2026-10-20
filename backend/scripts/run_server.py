import asyncio
import os

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


def main():
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
    app = create_app(
        ApiSettings(profile="desktop-gpu", storage_dir=storage_path, cookie_secure=False)
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="info"))
    asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)


if __name__ == "__main__":
    main()
