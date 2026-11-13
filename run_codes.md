cd "$env:USERPROFILE\Downloads\Projects\chatbot-project"
docker compose up -d postgres

Window 1: API (port 8010)
$P = "$env:USERPROFILE\Downloads\Projects"; $D = "$P\cka-data"
$env:HF_HOME="$D\hf"; $env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"; $env:TEMP="$D\tmp"; $env:TMP="$D\tmp"
$env:OLLAMA_BASE_URL="http://127.0.0.1:11434"; $env:APP_PROFILE="desktop-gpu"; $env:COOKIE_SECURE="false"; $env:W_STORAGE="$D\storage\files"
cd "$P\chatbot-project\backend"
uv run python -c "import asyncio, uvicorn, os; from app.api.app import create_app; from app.api.config import ApiSettings; app = create_app(ApiSettings(profile='desktop-gpu', storage_dir=os.environ['W_STORAGE'], cookie_secure=False)); server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8010)); asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)"

Window 2: Worker
$P = "$env:USERPROFILE\Downloads\Projects"; $D = "$P\cka-data"
$env:HF_HOME="$D\hf"; $env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"; $env:TEMP="$D\tmp"; $env:TMP="$D\tmp"
$env:OLLAMA_BASE_URL="http://127.0.0.1:11434"; $env:APP_PROFILE="desktop-gpu"
cd "$P\chatbot-project\backend"
uv run python -m procrastinate --app=app.worker.app worker

Window 3: Frontend
cd "$env:USERPROFILE\Downloads\Projects\chatbot-project\web"
$env:API_ORIGIN="http://127.0.0.1:8010"; $env:NEXT_PUBLIC_DATA_SOURCE="live"
npm run dev -- -H 127.0.0.1