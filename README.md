# AI Manager

Production-style foundational architecture for a scalable personal Telegram AI assistant.

## Stack

- Python 3.12
- aiogram 3.x
- FastAPI
- SQLAlchemy (async)
- SQLite (easy path to PostgreSQL)
- Anthropic Claude API
- dotenv-based settings
- Tauri 2 (desktop wrapper)

## Run backend (web API + PWA)

1. Create virtual environment and install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Copy env template:
   ```bash
   cp .env.example .env
   ```
3. Fill required secrets in `.env`.
4. Start service:
   ```bash
   python -m src.main
   ```

## Run desktop app (Tauri wrapper)

> Backend must already be running at `http://127.0.0.1:8000/`.

1. Install Node dependencies:
   ```bash
   npm install
   ```
2. Start desktop window in development mode:
   ```bash
   npm run desktop:dev
   ```

Tauri opens a native desktop window named **AI Manager** and points it to:

- `http://127.0.0.1:8000/`

Build command (no installer setup yet):

```bash
npm run desktop:build
```

## Architecture goals

- Clean modular boundaries
- Async-first execution
- Separation of API, bot, domain, services, infrastructure
- Ready for long-term memory, scheduling, tools, and integrations