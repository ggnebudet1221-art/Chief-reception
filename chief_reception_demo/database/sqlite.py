from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_services (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    price_rub INTEGER NOT NULL,
    duration_minutes INTEGER NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS demo_booking_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_telegram_id INTEGER NOT NULL,
    client_username TEXT,
    client_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    service_id TEXT NOT NULL,
    service_title TEXT NOT NULL,
    requested_date TEXT NOT NULL,
    requested_time TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(service_id) REFERENCES demo_services(id)
);

CREATE TABLE IF NOT EXISTS demo_bookings (
    booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL,
    telegram_username TEXT,
    telegram_first_name TEXT,
    client_name TEXT NOT NULL,
    phone TEXT,
    selected_service TEXT NOT NULL,
    selected_date TEXT NOT NULL,
    selected_time TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS demo_client_questions (
    question_id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL,
    telegram_username TEXT,
    telegram_first_name TEXT,
    client_name TEXT,
    question_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    owner_answer TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    answered_at TEXT
);
"""


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    ensure_columns(connection)
    return connection


def ensure_columns(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(demo_bookings)").fetchall()
    }
    if "selected_time" not in columns:
        connection.execute("ALTER TABLE demo_bookings ADD COLUMN selected_time TEXT")
        connection.commit()
