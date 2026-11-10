"""SQLite persistence for profiles, benchmark runs and agent conversations."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

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
                CREATE TABLE IF NOT EXISTS agent_memories (
                    memory_id TEXT PRIMARY KEY, scope TEXT NOT NULL, scope_id TEXT NOT NULL,
                    category TEXT NOT NULL, content TEXT NOT NULL, tags_json TEXT NOT NULL,
                    source TEXT NOT NULL, confidence REAL NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, last_accessed_at TEXT,
                    access_count INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_agent_memories_scope
                    ON agent_memories(scope, scope_id, archived, updated_at DESC);
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


    def save_memory(self, memory: dict[str, Any]) -> None:
        with self._connection() as db:
            db.execute(
                """INSERT OR REPLACE INTO agent_memories(
                    memory_id,scope,scope_id,category,content,tags_json,source,confidence,
                    created_at,updated_at,last_accessed_at,access_count,archived
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                (memory["memory_id"], memory["scope"], memory["scope_id"],
                 memory["category"], memory["content"],
                 json.dumps(memory.get("tags", []), ensure_ascii=False),
                 memory.get("source", "user"), float(memory.get("confidence", 1.0)),
                 memory["created_at"], memory["updated_at"],
                 memory.get("last_accessed_at"), int(memory.get("access_count", 0))),
            )

    def list_memories(self, scopes: list[tuple[str, str]], limit: int = 100) -> list[dict[str, Any]]:
        if not scopes:
            return []
        bounded = max(1, min(int(limit), 500))
        where = " OR ".join("(scope=? AND scope_id=?)" for _ in scopes)
        parameters: list[Any] = [value for pair in scopes for value in pair]
        parameters.append(bounded)
        with self._connection() as db:
            rows = db.execute(
                "SELECT memory_id,scope,scope_id,category,content,tags_json,source,confidence,"
                "created_at,updated_at,last_accessed_at,access_count "
                f"FROM agent_memories WHERE archived=0 AND ({where}) "
                "ORDER BY updated_at DESC LIMIT ?", parameters
            ).fetchall()
        return [{
            "memory_id": row["memory_id"], "scope": row["scope"], "scope_id": row["scope_id"],
            "category": row["category"], "content": row["content"],
            "tags": json.loads(row["tags_json"]), "source": row["source"],
            "confidence": row["confidence"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "last_accessed_at": row["last_accessed_at"],
            "access_count": row["access_count"],
        } for row in rows]

    def search_memories(
        self, scopes: list[tuple[str, str]], terms: list[str], limit: int = 500
    ) -> list[dict[str, Any]]:
        """Search terms inside requested scopes before applying the candidate limit."""
        if not scopes or not terms:
            return self.list_memories(scopes, limit)
        bounded = max(1, min(int(limit), 2_000))
        scope_where = " OR ".join("(scope=? AND scope_id=?)" for _ in scopes)
        term_clauses: list[str] = []
        term_parameters: list[str] = []
        for term in dict.fromkeys(item.casefold().strip() for item in terms if item.strip()):
            escaped = term.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            pattern = f"%{escaped}%"
            term_clauses.append(
                "(LOWER(content) LIKE ? ESCAPE '!' OR LOWER(tags_json) LIKE ? ESCAPE '!')"
            )
            term_parameters.extend((pattern, pattern))
        if not term_clauses:
            return self.list_memories(scopes, limit)
        query = (
            "SELECT memory_id,scope,scope_id,category,content,tags_json,source,confidence,"
            "created_at,updated_at,last_accessed_at,access_count "
            f"FROM agent_memories WHERE archived=0 AND ({scope_where}) "
            f"AND ({' OR '.join(term_clauses)}) "
            "ORDER BY updated_at DESC LIMIT ?"
        )
        parameters: list[Any] = [value for pair in scopes for value in pair]
        parameters.extend(term_parameters)
        parameters.append(bounded)
        with self._connection() as db:
            rows = db.execute(query, parameters).fetchall()
        return [{
            "memory_id": row["memory_id"], "scope": row["scope"], "scope_id": row["scope_id"],
            "category": row["category"], "content": row["content"],
            "tags": json.loads(row["tags_json"]), "source": row["source"],
            "confidence": row["confidence"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "last_accessed_at": row["last_accessed_at"],
            "access_count": row["access_count"],
        } for row in rows]

    def touch_memories(self, memory_ids: list[str], accessed_at: str) -> None:
        if not memory_ids:
            return
        placeholders = ",".join("?" for _ in memory_ids)
        with self._connection() as db:
            db.execute(
                f"UPDATE agent_memories SET last_accessed_at=?, access_count=access_count+1 "
                f"WHERE archived=0 AND memory_id IN ({placeholders})",
                [accessed_at, *memory_ids],
            )

    def archive_memory(self, memory_id: str) -> bool:
        with self._connection() as db:
            cursor = db.execute(
                "UPDATE agent_memories SET archived=1, updated_at=datetime('now') "
                "WHERE memory_id=? AND archived=0", (memory_id,)
            )
            return cursor.rowcount > 0
