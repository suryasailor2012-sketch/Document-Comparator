from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "app.db"


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
        existing_admin = connection.execute(
            "SELECT id FROM users WHERE is_admin = 1 LIMIT 1"
        ).fetchone()
        if existing_admin is None:
            now = datetime.utcnow().isoformat(timespec="seconds")
            connection.execute(
                """
                INSERT INTO users (username, password_hash, is_admin, is_active, created_at, updated_at)
                VALUES (?, ?, 1, 1, ?, ?)
                """,
                (admin_username, generate_password_hash(admin_password), now, now),
            )


def get_user_by_username(username: str) -> sqlite3.Row | None:
    with get_connection() as connection:
        return connection.execute(
            "SELECT * FROM users WHERE lower(username) = lower(?)",
            (username.strip(),),
        ).fetchone()


def get_user_by_id(user_id: int) -> sqlite3.Row | None:
    with get_connection() as connection:
        return connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def authenticate_user(username: str, password: str) -> sqlite3.Row | None:
    user = get_user_by_username(username)
    if user is None or not user["is_active"]:
        return None
    if not check_password_hash(user["password_hash"], password):
        return None
    return user


def list_users() -> list[sqlite3.Row]:
    with get_connection() as connection:
        return connection.execute(
            "SELECT id, username, is_admin, is_active, created_at, updated_at FROM users ORDER BY username"
        ).fetchall()


def create_user(username: str, password: str, is_admin: bool, is_active: bool = True) -> None:
    username = username.strip()
    if not username:
        raise ValueError("Username is required.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")

    now = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO users (username, password_hash, is_admin, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (username, generate_password_hash(password), int(is_admin), int(is_active), now, now),
        )


def update_user(user_id: int, username: str, is_admin: bool, is_active: bool, new_password: str = "") -> None:
    username = username.strip()
    if not username:
        raise ValueError("Username is required.")
    if new_password and len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters.")

    now = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        if new_password:
            connection.execute(
                """
                UPDATE users
                SET username = ?, is_admin = ?, is_active = ?, password_hash = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    username,
                    int(is_admin),
                    int(is_active),
                    generate_password_hash(new_password),
                    now,
                    user_id,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE users
                SET username = ?, is_admin = ?, is_active = ?, updated_at = ?
                WHERE id = ?
                """,
                (username, int(is_admin), int(is_active), now, user_id),
            )


def change_password(user_id: int, current_password: str, new_password: str) -> None:
    if len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters.")

    user = get_user_by_id(user_id)
    if user is None or not check_password_hash(user["password_hash"], current_password):
        raise ValueError("Current password is incorrect.")

    now = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (generate_password_hash(new_password), now, user_id),
        )
