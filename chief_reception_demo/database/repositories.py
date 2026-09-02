from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Service:
    id: str
    title: str
    price_rub: int
    duration_minutes: int
    description: str


@dataclass(frozen=True)
class BookingRequest:
    booking_id: int
    telegram_user_id: int
    telegram_username: str | None
    telegram_first_name: str | None
    client_name: str
    phone: str | None
    selected_service: str
    selected_date: str
    selected_time: str | None
    created_at: str


@dataclass(frozen=True)
class ClientQuestion:
    question_id: int
    telegram_user_id: int
    telegram_username: str | None
    telegram_first_name: str | None
    client_name: str | None
    question_text: str
    status: str
    owner_answer: str | None
    created_at: str
    answered_at: str | None


class ServiceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def seed_defaults(self, services: list[Service]) -> None:
        self.connection.executemany(
            """
            INSERT OR IGNORE INTO demo_services
                (id, title, price_rub, duration_minutes, description)
            VALUES
                (:id, :title, :price_rub, :duration_minutes, :description)
            """,
            [service.__dict__ for service in services],
        )
        self.connection.commit()

    def list_all(self) -> list[Service]:
        rows = self.connection.execute(
            "SELECT id, title, price_rub, duration_minutes, description FROM demo_services ORDER BY price_rub"
        ).fetchall()
        return [Service(**dict(row)) for row in rows]

    def find_by_text(self, text: str) -> Service | None:
        normalized = text.casefold()
        for service in self.list_all():
            if service.id.casefold() in normalized or service.title.casefold() in normalized:
                return service
        return None

    def get(self, service_id: str) -> Service | None:
        row = self.connection.execute(
            """
            SELECT id, title, price_rub, duration_minutes, description
            FROM demo_services
            WHERE id = ?
            """,
            (service_id,),
        ).fetchone()
        return Service(**dict(row)) if row else None


class BookingRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create(
        self,
        *,
        telegram_user_id: int,
        telegram_username: str | None,
        telegram_first_name: str | None,
        client_name: str,
        phone: str | None,
        selected_service: str,
        selected_date: str,
        selected_time: str,
    ) -> BookingRequest:
        cursor = self.connection.execute(
            """
            INSERT INTO demo_bookings
                (
                    telegram_user_id,
                    telegram_username,
                    telegram_first_name,
                    client_name,
                    phone,
                    selected_service,
                    selected_date,
                    selected_time
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_user_id,
                telegram_username,
                telegram_first_name,
                client_name,
                phone,
                selected_service,
                selected_date,
                selected_time,
            ),
        )
        self.connection.commit()
        row = self.connection.execute(
            """
            SELECT booking_id, telegram_user_id, telegram_username, telegram_first_name,
                   client_name, phone, selected_service, selected_date, selected_time, created_at
            FROM demo_bookings
            WHERE booking_id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        return BookingRequest(
            **dict(row),
        )

    def get_latest_for_user(self, telegram_user_id: int) -> BookingRequest | None:
        row = self.connection.execute(
            """
            SELECT booking_id, telegram_user_id, telegram_username, telegram_first_name,
                   client_name, phone, selected_service, selected_date, selected_time, created_at
            FROM demo_bookings
            WHERE telegram_user_id = ?
            ORDER BY booking_id DESC
            LIMIT 1
            """,
            (telegram_user_id,),
        ).fetchone()
        return BookingRequest(**dict(row)) if row else None

    def get_by_id(self, booking_id: int) -> BookingRequest | None:
        row = self.connection.execute(
            """
            SELECT booking_id, telegram_user_id, telegram_username, telegram_first_name,
                   client_name, phone, selected_service, selected_date, selected_time, created_at
            FROM demo_bookings
            WHERE booking_id = ?
            """,
            (booking_id,),
        ).fetchone()
        return BookingRequest(**dict(row)) if row else None

    def delete(self, booking_id: int) -> None:
        self.connection.execute("DELETE FROM demo_bookings WHERE booking_id = ?", (booking_id,))
        self.connection.commit()

    def update_schedule(
        self,
        *,
        booking_id: int,
        selected_date: str,
        selected_time: str,
    ) -> BookingRequest:
        self.connection.execute(
            """
            UPDATE demo_bookings
            SET selected_date = ?, selected_time = ?
            WHERE booking_id = ?
            """,
            (selected_date, selected_time, booking_id),
        )
        self.connection.commit()
        row = self.connection.execute(
            """
            SELECT booking_id, telegram_user_id, telegram_username, telegram_first_name,
                   client_name, phone, selected_service, selected_date, selected_time, created_at
            FROM demo_bookings
            WHERE booking_id = ?
            """,
            (booking_id,),
        ).fetchone()
        return BookingRequest(**dict(row))

    def booked_times_for_date(self, selected_date: str) -> set[str]:
        rows = self.connection.execute(
            """
            SELECT selected_time
            FROM demo_bookings
            WHERE selected_date = ? AND selected_time IS NOT NULL
            """,
            (selected_date,),
        ).fetchall()
        return {str(row["selected_time"]) for row in rows}

    def is_slot_booked(self, *, selected_date: str, selected_time: str) -> bool:
        row = self.connection.execute(
            """
            SELECT 1
            FROM demo_bookings
            WHERE selected_date = ? AND selected_time = ?
            LIMIT 1
            """,
            (selected_date, selected_time),
        ).fetchone()
        return row is not None


class ClientQuestionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create_pending(
        self,
        *,
        telegram_user_id: int,
        telegram_username: str | None,
        telegram_first_name: str | None,
        client_name: str | None,
        question_text: str,
    ) -> ClientQuestion:
        cursor = self.connection.execute(
            """
            INSERT INTO demo_client_questions
                (telegram_user_id, telegram_username, telegram_first_name, client_name, question_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (telegram_user_id, telegram_username, telegram_first_name, client_name, question_text),
        )
        self.connection.commit()
        row = self.connection.execute(
            """
            SELECT question_id, telegram_user_id, telegram_username, telegram_first_name,
                   client_name, question_text, status, owner_answer, created_at, answered_at
            FROM demo_client_questions
            WHERE question_id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        return ClientQuestion(**dict(row))
