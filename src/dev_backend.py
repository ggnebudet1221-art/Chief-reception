from __future__ import annotations

from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import unquote


ROOT_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT_DIR / "web"
STATIC_DIR = WEB_DIR / "static"
DB_PATH = ROOT_DIR / "ai_manager.db"


def _load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


ENV = _load_env()
WEB_ACCESS_TOKEN = ENV.get("WEB_ACCESS_TOKEN") or os.environ.get("WEB_ACCESS_TOKEN") or "change_me"
WEB_OWNER_ID = int(ENV.get("WEB_OWNER_ID") or os.environ.get("WEB_OWNER_ID") or "1")
APP_HOST = ENV.get("APP_HOST") or os.environ.get("APP_HOST") or "0.0.0.0"
APP_PORT = int(ENV.get("APP_PORT") or os.environ.get("APP_PORT") or "8000")
ANTHROPIC_MODEL = ENV.get("ANTHROPIC_MODEL") or os.environ.get("ANTHROPIC_MODEL") or "dev-backend"
MAX_HISTORY_MESSAGES = int(ENV.get("MAX_HISTORY_MESSAGES") or os.environ.get("MAX_HISTORY_MESSAGES") or "6")
CLAUDE_MAX_TOKENS = int(ENV.get("CLAUDE_MAX_TOKENS") or os.environ.get("CLAUDE_MAX_TOKENS") or "512")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role VARCHAR(32) NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id INTEGER PRIMARY KEY,
                profile_text TEXT DEFAULT '',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title VARCHAR(300) NOT NULL,
                status VARCHAR(16) DEFAULT 'active',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                completed_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS day_plan_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title VARCHAR(300) NOT NULL,
                status VARCHAR(16) DEFAULT 'active',
                plan_date DATE NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                completed_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text VARCHAR(300) NOT NULL,
                chat_id INTEGER,
                remind_at DATETIME NOT NULL,
                status VARCHAR(16) DEFAULT 'active',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                completed_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                content VARCHAR(300) NOT NULL,
                category VARCHAR(64) DEFAULT 'general',
                importance INTEGER DEFAULT 3,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME
            );
            """
        )


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


class DevBackendHandler(SimpleHTTPRequestHandler):
    server_version = "AIManagerDevBackend/0.1"

    def translate_path(self, path: str) -> str:
        clean_path = unquote(path.split("?", 1)[0])
        if clean_path.startswith("/static/"):
            return str(STATIC_DIR / clean_path.removeprefix("/static/"))
        if clean_path == "/manifest.json":
            return str(WEB_DIR / "manifest.json")
        return str(WEB_DIR / clean_path.lstrip("/"))

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            return
        if self.path == "/health":
            self._json({"status": "ok", "backend": "dev-stdlib"})
            return
        if self.path.startswith("/api/"):
            if not self._authorized():
                return
            self._handle_api_get()
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path.startswith("/api/"):
            if not self._authorized():
                return
            self._handle_api_post()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        if self.path.startswith("/api/"):
            if not self._authorized():
                return
            self._handle_api_delete()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization,Content-Type")

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        if header != f"Bearer {WEB_ACCESS_TOKEN}":
            self._json({"detail": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
            return False
        return True

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _handle_api_get(self) -> None:
        path = self.path.split("?", 1)[0]
        with _connect() as conn:
            if path == "/api/tasks":
                rows = conn.execute(
                    "SELECT id, title, status FROM tasks WHERE user_id=? AND status='active' ORDER BY id ASC",
                    (WEB_OWNER_ID,),
                ).fetchall()
                self._json([_row_dict(row) for row in rows])
                return
            if path == "/api/plan/today":
                rows = conn.execute(
                    "SELECT id, title, status FROM day_plan_items WHERE user_id=? AND plan_date=? AND status='active' ORDER BY id ASC",
                    (WEB_OWNER_ID, date.today().isoformat()),
                ).fetchall()
                self._json([_row_dict(row) for row in rows])
                return
            if path == "/api/reminders":
                rows = conn.execute(
                    "SELECT id, text, remind_at FROM reminders WHERE user_id=? AND status='active' ORDER BY remind_at ASC",
                    (WEB_OWNER_ID,),
                ).fetchall()
                self._json([_row_dict(row) for row in rows])
                return
            if path == "/api/profile":
                row = conn.execute(
                    "SELECT profile_text FROM user_profiles WHERE user_id=?",
                    (WEB_OWNER_ID,),
                ).fetchone()
                self._json({"text": row["profile_text"] if row else ""})
                return
            if path == "/api/memories":
                rows = conn.execute(
                    "SELECT id, content, category, importance, created_at FROM memories WHERE user_id=? ORDER BY importance DESC, created_at DESC LIMIT 100",
                    (WEB_OWNER_ID,),
                ).fetchall()
                self._json([_row_dict(row) for row in rows])
                return
            if path == "/api/chat/history":
                rows = conn.execute(
                    "SELECT id, role, content, created_at FROM chat_messages WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
                    (WEB_OWNER_ID,),
                ).fetchall()
                self._json([_row_dict(row) for row in reversed(rows)])
                return
            if path == "/api/status":
                count = conn.execute(
                    "SELECT COUNT(*) AS count FROM memories WHERE user_id=?",
                    (WEB_OWNER_ID,),
                ).fetchone()["count"]
                self._json(
                    {
                        "model": ANTHROPIC_MODEL,
                        "max_history_messages": MAX_HISTORY_MESSAGES,
                        "max_tokens": CLAUDE_MAX_TOKENS,
                        "api": "ok-dev-stdlib",
                        "memories_count": count,
                        "version": "v0.3.0",
                    }
                )
                return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _handle_api_post(self) -> None:
        path = self.path.split("?", 1)[0]
        payload = self._body()
        now = datetime.now(timezone.utc).isoformat()
        with _connect() as conn:
            if path == "/api/tasks":
                title = str(payload.get("title", "")).strip()[:300]
                cur = conn.execute(
                    "INSERT INTO tasks (user_id, title, status) VALUES (?, ?, 'active')",
                    (WEB_OWNER_ID, title),
                )
                self._json({"id": cur.lastrowid, "title": title, "status": "active"})
                return
            if path.startswith("/api/tasks/") and path.endswith("/done"):
                item_id = int(path.split("/")[3])
                conn.execute(
                    "UPDATE tasks SET status='done', completed_at=? WHERE id=? AND user_id=?",
                    (now, item_id, WEB_OWNER_ID),
                )
                self._json({"ok": True})
                return
            if path == "/api/plan":
                title = str(payload.get("title", "")).strip()[:300]
                cur = conn.execute(
                    "INSERT INTO day_plan_items (user_id, title, status, plan_date) VALUES (?, ?, 'active', ?)",
                    (WEB_OWNER_ID, title, date.today().isoformat()),
                )
                self._json({"id": cur.lastrowid, "title": title, "status": "active"})
                return
            if path.startswith("/api/plan/") and path.endswith("/done"):
                item_id = int(path.split("/")[3])
                conn.execute(
                    "UPDATE day_plan_items SET status='done', completed_at=? WHERE id=? AND user_id=?",
                    (now, item_id, WEB_OWNER_ID),
                )
                self._json({"ok": True})
                return
            if path == "/api/reminders":
                text = str(payload.get("text", "")).strip()[:300]
                remind_at = str(payload.get("remind_at", "")).replace("Z", "+00:00")
                cur = conn.execute(
                    "INSERT INTO reminders (user_id, chat_id, text, remind_at, status) VALUES (?, ?, ?, ?, 'active')",
                    (WEB_OWNER_ID, WEB_OWNER_ID, text, remind_at),
                )
                self._json({"id": cur.lastrowid, "text": text})
                return
            if path.startswith("/api/reminders/") and path.endswith("/cancel"):
                item_id = int(path.split("/")[3])
                conn.execute(
                    "UPDATE reminders SET status='cancelled', completed_at=? WHERE id=? AND user_id=?",
                    (now, item_id, WEB_OWNER_ID),
                )
                self._json({"ok": True})
                return
            if path == "/api/profile":
                text = str(payload.get("text", ""))[:500]
                conn.execute(
                    """
                    INSERT INTO user_profiles (user_id, profile_text, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET profile_text=excluded.profile_text, updated_at=excluded.updated_at
                    """,
                    (WEB_OWNER_ID, text, now),
                )
                self._json({"ok": True})
                return
            if path == "/api/memories":
                content = str(payload.get("content", "")).strip()[:300]
                category = str(payload.get("category", "general")).strip()[:64] or "general"
                importance = max(1, min(5, int(payload.get("importance", 3) or 3)))
                cur = conn.execute(
                    "INSERT INTO memories (user_id, content, category, importance) VALUES (?, ?, ?, ?)",
                    (WEB_OWNER_ID, content, category, importance),
                )
                self._json({"id": cur.lastrowid})
                return
            if path == "/api/chat":
                message = str(payload.get("message", "")).strip()
                conn.execute(
                    "INSERT INTO chat_messages (user_id, role, content) VALUES (?, 'user', ?)",
                    (WEB_OWNER_ID, message),
                )
                reply = "Dev backend is running. Claude service is unavailable until FastAPI dependencies are repaired."
                conn.execute(
                    "INSERT INTO chat_messages (user_id, role, content) VALUES (?, 'assistant', ?)",
                    (WEB_OWNER_ID, reply),
                )
                self._json({"reply": reply})
                return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _handle_api_delete(self) -> None:
        path = self.path.split("?", 1)[0]
        with _connect() as conn:
            if path == "/api/profile":
                conn.execute("DELETE FROM user_profiles WHERE user_id=?", (WEB_OWNER_ID,))
                self._json({"ok": True})
                return
            if path.startswith("/api/memories/"):
                item_id = int(path.split("/")[3])
                conn.execute("DELETE FROM memories WHERE id=? AND user_id=?", (item_id, WEB_OWNER_ID))
                self._json({"ok": True})
                return
            if path == "/api/chat/history":
                conn.execute("DELETE FROM chat_messages WHERE user_id=?", (WEB_OWNER_ID,))
                self._json({"ok": True})
                return
        self.send_error(HTTPStatus.NOT_FOUND)


def run_dev_backend(import_error: BaseException | None = None) -> None:
    _init_db()
    if import_error is not None:
        print(f"FastAPI backend unavailable, using stdlib dev backend: {import_error}", flush=True)
    server = ThreadingHTTPServer((APP_HOST, APP_PORT), DevBackendHandler)
    print(f"AI Manager dev backend running on http://127.0.0.1:{APP_PORT}/", flush=True)
    server.serve_forever()
