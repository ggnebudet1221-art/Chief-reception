# AI Manager

Production-like development skeleton for AI Manager: FastAPI backend, SQLite persistence,
Telegram communication layer, Claude chat, and a Tauri desktop operations workspace.

## Stack

- Python 3.13
- FastAPI
- SQLAlchemy (async)
- SQLite (easy path to PostgreSQL)
- Anthropic Claude API
- Telegram bot via aiogram/aiohttp
- dotenv-based settings
- Tauri 2 (desktop wrapper)

## Environment

Use Python 3.13. On Windows with a Cyrillic user profile path, the project keeps
dependencies in a shared ASCII-path environment:

```powershell
C:\Users\Public\AIManagerVenv\Lib\site-packages
```

The launcher runs the real Python 3.13 executable and sets `PYTHONPATH` to that
site-packages folder. This avoids Windows venv launcher issues with non-ASCII
user paths while keeping project dependencies separate from the repo.

Required `.env` values:

```dotenv
WEB_ACCESS_TOKEN="change_me"
WEB_OWNER_ID="1"
TELEGRAM_BOT_TOKEN=""
CHIEF_BOT_TOKEN=""
BUSINESS_BOT_TOKEN=""
SMM_BOT_TOKEN=""
CHIEF_AGENT_NAME="Chief"
BUSINESS_AGENT_NAME="Business"
SMM_AGENT_NAME="SMM"
CHIEF_PROMPT_PATH="prompts/chief.txt"
BUSINESS_PROMPT_PATH="prompts/business.txt"
SMM_PROMPT_PATH="prompts/smm.txt"
OWNER_TELEGRAM_ID="0"
ENABLE_TELEGRAM_BOT="true"
TELEGRAM_REQUEST_TIMEOUT="45"
TELEGRAM_POLLING_TIMEOUT="30"
AGENT_TASK_TIMEOUT_SECONDS="60"
TELEGRAM_PROXY_URL=""
TELEGRAM_CHIEF_CHAT_ID="0"
TELEGRAM_BUSINESS_CHAT_ID="0"
TELEGRAM_SMM_CHAT_ID="0"
TELEGRAM_COORDINATION_CHAT_ID="0"
TELEGRAM_GENERAL_TOPIC_ID="0"
TELEGRAM_TASKS_TOPIC_ID="0"
TELEGRAM_INFRA_TOPIC_ID="0"
DATABASE_URL="sqlite+aiosqlite:///./ai_manager.db"
ANTHROPIC_API_KEY="your_anthropic_api_key"
ANTHROPIC_BASE_URL="https://api.vibecode-claude.online"
ANTHROPIC_MODEL="claude-sonnet-4.5"
CLAUDE_MAX_TOKENS="300"
SCOUT_ANTHROPIC_API_KEY=""
SCOUT_ANTHROPIC_BASE_URL=""
SCOUT_ANTHROPIC_MODEL=""
```

If the main Anthropic-compatible route is coding-focused, configure the `SCOUT_ANTHROPIC_*`
variables with a normal analysis-capable Claude-compatible provider. Scout will not silently
fall back to template analysis when the provider returns coding-only refusals.

SQLite database path:

```text
./ai_manager.db
```

Telegram is the primary communication layer. Desktop is the operations center:
task queue, agents, room activity, long-running work, and monitoring.

- `TELEGRAM_BOT_TOKEN` is legacy and does not start polling by itself.
- Polling starts when any of `CHIEF_BOT_TOKEN`, `BUSINESS_BOT_TOKEN`,
  or `SMM_BOT_TOKEN` is set.
- Prefer per-agent tokens for the multi-agent runtime.
- Set `OWNER_TELEGRAM_ID` to your Telegram user id for private access.
- Set `TELEGRAM_PROXY_URL` only if Windows cannot reach `api.telegram.org`
  directly through your current network/VPN.
- Optional per-agent chat ids let you route separate Telegram chats to
  `Chief`, `Business`, `SMM`, or a shared coordination chat. Leave them as `0`
  to route by message intent.
- `TELEGRAM_COORDINATION_CHAT_ID` can point to a Telegram group/forum workspace.
  Optional topic ids split live messages into general coordination, task queue,
  and infrastructure alerts.
- All Telegram-created workspace data is stored under `WEB_OWNER_ID`, so the
  desktop task queue and room state update from the same SQLite source of truth.

## Run Backend Directly

```powershell
$env:PYTHONPATH="C:\Users\Public\AIManagerVenv\Lib\site-packages"
& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" -m src.main
```

Health check:

```text
http://127.0.0.1:8000/health
```

Authenticated API example:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/tasks -Headers @{ Authorization = "Bearer <WEB_ACCESS_TOKEN>" }
```

## Run Desktop Dev

1. Install Node dependencies:
   ```bash
   npm install
   ```
2. Start backend + desktop window:
   ```bash
   npm run desktop:dev
   ```

Alternative helper:

```powershell
.\start-dev.ps1
```

The desktop launcher starts the real FastAPI backend automatically when
`http://127.0.0.1:8000/health` is not already healthy, then opens the native
AI Manager window at:

- `http://127.0.0.1:8000/`

The emergency static/mock fallback is kept only for diagnostics. Normal dev mode
should use the real FastAPI backend and SQLite database.

If Telegram env values are present, the same backend process starts the aiogram
dispatcher/router polling loop and reminder scheduler. There is no urllib
fallback polling mode; network failures reconnect through aiogram/aiohttp with
backoff and health logging.

## Telegram Agent Architecture

- Agent identity comes only from prompt files in `prompts/`.
- If a prompt file is missing or empty, backend logs an explicit error and does
  not silently fall back to a generic assistant personality.
- `Chief` is the orchestrator and default route.
- `Business` receives strategy, market, monetization, and research requests.
- `SMM` receives content, social media, post, Reels, and channel requests.
- Agent work is stored as `agent_task` rows in SQLite.
- Agent-to-agent coordination is stored in `agent_messages`.
- User tasks remain `user_task` rows and are never mixed with completed
  agent work when answering "what are my tasks today?"

Task lifecycle:

```text
active -> delegated -> in_progress -> completed -> archived
```

All interfaces use the same SQLite `tasks` table through `TaskService`.
Telegram, desktop, local console, orchestration, and internal discussion logs
must not infer active tasks from chat history or long-term memory.

## Build Desktop

```bash
npm run desktop:build
```

## Architecture goals

- Clean modular boundaries
- Async-first execution
- Separation of API, bot, domain, services, infrastructure
- Ready for long-term memory, scheduling, tools, and integrations
