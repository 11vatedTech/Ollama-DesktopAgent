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
                CREATE TABLE IF NOT EXISTS agent_run_events (
                    run_id TEXT NOT NULL, sequence INTEGER NOT NULL, kind TEXT NOT NULL,
                    message TEXT NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, sequence),
                    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_run_events_time
                    ON agent_run_events(run_id, created_at);
                CREATE TABLE IF NOT EXISTS agent_run_checkpoints (
                    run_id TEXT PRIMARY KEY,
                    model_name TEXT NOT NULL,
                    model_digest TEXT NOT NULL,
                    agent_title TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    harness_mode TEXT NOT NULL,
                    tool_manifest_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    step INTEGER NOT NULL,
                    tool_calls INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_run_checkpoints_state
                    ON agent_run_checkpoints(state, updated_at DESC);
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

    def append_run_event(
        self, run_id: str, model_name: str, agent_title: str, task: str,
        event: dict[str, Any],
    ) -> int:
        """Durably append one ordered event and create a crash-visible running record."""
        created_at = str(event.get("timestamp") or "")
        kind = str(event.get("kind") or "event")
        message = str(event.get("message") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        encoded_data = json.dumps(data, ensure_ascii=False, default=str)
        with self._connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO agent_runs("
                "run_id,model_name,agent_title,task,status,created_at,completed_at,trace_json"
                ") VALUES(?,?,?,?,?,?,NULL,'[]')",
                (run_id, model_name, agent_title, task, "running", created_at),
            )
            row = db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM agent_run_events WHERE run_id=?",
                (run_id,),
            ).fetchone()
            sequence = int(row[0])
            db.execute(
                "INSERT INTO agent_run_events(run_id,sequence,kind,message,data_json,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (run_id, sequence, kind, message, encoded_data, created_at),
            )
            db.execute(
                "UPDATE agent_runs SET status='running' WHERE run_id=?",
                (run_id,),
            )
        return sequence

    def list_run_events(self, run_id: str) -> list[dict[str, Any]]:
        """Read the append-only event journal in its durable sequence order."""
        with self._connection() as db:
            rows = db.execute(
                "SELECT sequence,kind,message,data_json,created_at "
                "FROM agent_run_events WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return [{
            "sequence": row["sequence"],
            "kind": row["kind"],
            "message": row["message"],
            "data": json.loads(row["data_json"]),
            "timestamp": row["created_at"],
        } for row in rows]

    def save_run(self, run_id: str, model_name: str, agent_title: str, task: str,
                 status: str, created_at: str, completed_at: str | None,
                 trace: list[dict[str, Any]]) -> None:
        """Finalize a run without replacing its append-only event history."""
        with self._connection() as db:
            db.execute(
                "INSERT INTO agent_runs(run_id,model_name,agent_title,task,status,created_at,completed_at,trace_json) "
                "VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET "
                "model_name=excluded.model_name, agent_title=excluded.agent_title, "
                "task=excluded.task, status=excluded.status, created_at=excluded.created_at, "
                "completed_at=excluded.completed_at, trace_json=excluded.trace_json",
                (run_id, model_name, agent_title, task, status, created_at, completed_at,
                 json.dumps(trace, ensure_ascii=False, default=str)),
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


    def save_run_checkpoint(
        self,
        *,
        run_id: str,
        model_name: str,
        model_digest: str,
        agent_title: str,
        workspace: str,
        harness_mode: str,
        tool_manifest_sha256: str,
        state: str,
        step: int,
        tool_calls: int,
        payload: dict[str, Any],
        created_at: str,
        updated_at: str,
    ) -> None:
        """Atomically persist a bounded, integrity-checked safe-boundary snapshot."""
        if state not in {"ready", "awaiting_model", "tool_in_progress", "needs_review"}:
            raise ValueError(f"Unsupported run checkpoint state: {state}")
        if step < 0 or tool_calls < 0:
            raise ValueError("Checkpoint counters cannot be negative")
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        if len(payload_json.encode("utf-8")) > 1_000_000:
            raise ValueError("Agent run checkpoint exceeds the 1 MB persistence limit")
        payload_sha256 = __import__("hashlib").sha256(payload_json.encode("utf-8")).hexdigest()
        with self._connection() as db:
            db.execute(
                """INSERT INTO agent_run_checkpoints(
                    run_id,model_name,model_digest,agent_title,workspace,harness_mode,
                    tool_manifest_sha256,state,step,tool_calls,payload_json,payload_sha256,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                    model_name=excluded.model_name, model_digest=excluded.model_digest,
                    agent_title=excluded.agent_title, workspace=excluded.workspace,
                    harness_mode=excluded.harness_mode,
                    tool_manifest_sha256=excluded.tool_manifest_sha256,
                    state=excluded.state, step=excluded.step, tool_calls=excluded.tool_calls,
                    payload_json=excluded.payload_json, payload_sha256=excluded.payload_sha256,
                    updated_at=excluded.updated_at""",
                (run_id, model_name, model_digest, agent_title, workspace, harness_mode,
                 tool_manifest_sha256, state, step, tool_calls, payload_json,
                 payload_sha256, created_at, updated_at),
            )

    def get_run_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        """Load and integrity-check one run checkpoint."""
        with self._connection() as db:
            row = db.execute(
                """SELECT run_id,model_name,model_digest,agent_title,workspace,harness_mode,
                          tool_manifest_sha256,state,step,tool_calls,payload_json,payload_sha256,
                          created_at,updated_at
                   FROM agent_run_checkpoints WHERE run_id=?""",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        import hashlib

        encoded = str(row["payload_json"])
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if digest != row["payload_sha256"]:
            raise ValueError(f"Run checkpoint integrity check failed for {run_id}")
        return {
            "run_id": row["run_id"], "model_name": row["model_name"],
            "model_digest": row["model_digest"], "agent_title": row["agent_title"],
            "workspace": row["workspace"], "harness_mode": row["harness_mode"],
            "tool_manifest_sha256": row["tool_manifest_sha256"], "state": row["state"],
            "step": row["step"], "tool_calls": row["tool_calls"],
            "payload": json.loads(encoded), "payload_sha256": row["payload_sha256"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def list_run_checkpoints(self, limit: int = 100) -> list[dict[str, Any]]:
        """List checkpoint metadata without loading conversation payloads."""
        bounded = max(1, min(int(limit), 500))
        with self._connection() as db:
            rows = db.execute(
                """SELECT c.run_id,c.model_name,c.model_digest,c.agent_title,c.workspace,
                          c.harness_mode,c.state,c.step,c.tool_calls,c.created_at,c.updated_at,
                          r.task,r.status
                   FROM agent_run_checkpoints c
                   LEFT JOIN agent_runs r ON r.run_id=c.run_id
                   ORDER BY c.updated_at DESC LIMIT ?""",
                (bounded,),
            ).fetchall()
        return [{
            "run_id": row["run_id"], "model_name": row["model_name"],
            "model_digest": row["model_digest"], "agent_title": row["agent_title"],
            "workspace": row["workspace"], "harness_mode": row["harness_mode"],
            "state": row["state"], "step": row["step"], "tool_calls": row["tool_calls"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "task": row["task"], "run_status": row["status"],
        } for row in rows]

    def delete_run_checkpoint(self, run_id: str) -> bool:
        with self._connection() as db:
            cursor = db.execute(
                "DELETE FROM agent_run_checkpoints WHERE run_id=?", (run_id,)
            )
            return cursor.rowcount > 0

