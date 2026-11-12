from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from app_models import BagelState, PizzaState, Session


class SessionRepository:
    def __init__(self, database_path: str | Path | None = None) -> None:
        configured_path = (
            database_path if database_path is not None else os.getenv("DATABASE_PATH", "data/orders.sqlite3")
        )
        self.database_path = Path(configured_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL DEFAULT 'legacy',
                    flow TEXT NOT NULL CHECK (flow IN ('pizza', 'bagel')),
                    state_json TEXT NOT NULL,
                    trace_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "owner_id" not in columns:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'legacy'"
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO app_metadata (key, value) VALUES ('demo_cookie_secret', ?)",
                (secrets.token_urlsafe(48),),
            )

    @property
    def cookie_secret(self) -> bytes:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_metadata WHERE key = 'demo_cookie_secret'"
            ).fetchone()
        if row is None:
            raise RuntimeError("Demo session signing secret is missing from the database")
        return row["value"].encode("utf-8")

    def save(self, session_id: str, session: Session, owner_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO sessions (session_id, owner_id, flow, state_json, trace_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    flow = excluded.flow,
                    state_json = excluded.state_json,
                    trace_json = excluded.trace_json,
                    updated_at = CURRENT_TIMESTAMP
                WHERE sessions.owner_id = excluded.owner_id
                """,
                (
                    session_id,
                    owner_id,
                    session.flow,
                    json.dumps(asdict(session.state)),
                    json.dumps(session.trace),
                ),
            )
            return cursor.rowcount == 1

    def get(self, session_id: str, owner_id: str) -> Session | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT flow, state_json, trace_json
                FROM sessions WHERE session_id = ? AND owner_id = ?
                """,
                (session_id, owner_id),
            ).fetchone()

        if row is None:
            return None

        session = Session(flow=row["flow"])
        state_type = PizzaState if session.flow == "pizza" else BagelState
        session.state = state_type(**json.loads(row["state_json"]))
        session.trace = json.loads(row["trace_json"])
        return session

    def delete(self, session_id: str, owner_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM sessions WHERE session_id = ? AND owner_id = ?",
                (session_id, owner_id),
            )
        return cursor.rowcount == 1

    def list_sessions(
        self,
        owner_id: str,
        flow: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, str]]:
        query = """
            SELECT session_id, flow, state_json, updated_at
            FROM sessions WHERE owner_id = ?
        """
        parameters: tuple[Any, ...] = (owner_id,)
        if flow is not None:
            query += " AND flow = ?"
            parameters += (flow,)
        query += " ORDER BY updated_at DESC, session_id LIMIT ?"
        parameters += (limit,)

        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()

        sessions = []
        for row in rows:
            state = json.loads(row["state_json"])
            if row["flow"] == "pizza":
                summary = " ".join(part for part in (state.get("size"), state.get("crust")) if part)
                summary = f"Pizza: {summary or 'new order'}"
            else:
                summary = " ".join(part for part in (state.get("bread_type"), state.get("filling")) if part)
                summary = f"Bagel: {summary or 'new order'}"
            sessions.append(
                {
                    "session_id": row["session_id"],
                    "flow": row["flow"],
                    "updated_at": row["updated_at"],
                    "summary": summary,
                }
            )
        return sessions
