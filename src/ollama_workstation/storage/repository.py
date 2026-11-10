"""SQLite persistence for profiles, benchmark runs and agent conversations."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ollama_workstation.models.schemas import ModelProfile


class Repository:
    def __init__(self, database: Path) -> None:
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS model_profiles (
                    model_name TEXT NOT NULL, digest TEXT NOT NULL, generated_at TEXT NOT NULL,
                    profile_json TEXT NOT NULL, PRIMARY KEY(model_name, digest)
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY, model_name TEXT NOT NULL, agent_title TEXT NOT NULL,
                    task TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    completed_at TEXT, trace_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY, model_name TEXT NOT NULL, title TEXT NOT NULL,
                    updated_at TEXT NOT NULL, messages_json TEXT NOT NULL
                );
            """)

    def save_profile(self, profile: ModelProfile) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO model_profiles(model_name,digest,generated_at,profile_json) VALUES(?,?,?,?)",
                (profile.model_name, profile.digest, profile.generated_at,
                 json.dumps(profile.to_dict(), ensure_ascii=False)),
            )

    def get_profile(self, model_name: str, digest: str) -> ModelProfile | None:
        with self._connection() as db:
            row = db.execute("SELECT profile_json FROM model_profiles WHERE model_name=? AND digest=?",
                             (model_name, digest)).fetchone()
        return ModelProfile.from_dict(json.loads(row[0])) if row else None

    def list_profiles(self) -> list[ModelProfile]:
        with self._connection() as db:
            rows = db.execute("SELECT profile_json FROM model_profiles ORDER BY generated_at DESC").fetchall()
        return [ModelProfile.from_dict(json.loads(row[0])) for row in rows]

    def save_run(self, run_id: str, model_name: str, agent_title: str, task: str,
                 status: str, created_at: str, completed_at: str | None,
                 trace: list[dict[str, Any]]) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO agent_runs(run_id,model_name,agent_title,task,status,created_at,completed_at,trace_json) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (run_id, model_name, agent_title, task, status, created_at, completed_at,
                 json.dumps(trace, ensure_ascii=False)),
            )

    def save_conversation(self, conversation_id: str, model_name: str, title: str,
                          updated_at: str, messages: list[dict[str, Any]]) -> None:
        with self._connection() as db:
            db.execute("INSERT OR REPLACE INTO conversations VALUES(?,?,?,?,?)",
                       (conversation_id, model_name, title, updated_at,
                        json.dumps(messages, ensure_ascii=False)))

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 500))
        with self._connection() as db:
            rows = db.execute(
                "SELECT run_id,model_name,agent_title,task,status,created_at,completed_at,trace_json "
                "FROM agent_runs ORDER BY created_at DESC LIMIT ?", (bounded,)
            ).fetchall()
        return [{
            "run_id": row[0], "model_name": row[1], "agent_title": row[2],
            "task": row[3], "status": row[4], "created_at": row[5],
            "completed_at": row[6], "trace": json.loads(row[7]),
        } for row in rows]
