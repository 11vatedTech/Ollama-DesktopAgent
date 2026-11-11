"""SQLite persistence for profiles, benchmark runs and agent conversations."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ollama_workstation.models.schemas import ModelProfile


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


class Repository:
    CHECKPOINT_KEY_ENV = "OLLAMA_WORKSTATION_CHECKPOINT_KEY_FILE"

    def __init__(self, database: Path, *, checkpoint_key_path: Path | None = None) -> None:
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        configured_key = os.environ.get(self.CHECKPOINT_KEY_ENV, "").strip()
        self.checkpoint_key_path = (
            checkpoint_key_path
            or (Path(configured_key).expanduser() if configured_key
                else database.parent / ".checkpoint.key")
        )
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

    @staticmethod
    def _event_sha256(
        *, run_id: str, sequence: int, kind: str, message: str,
        data_json: str, created_at: str, previous_sha256: str,
    ) -> str:
        """Hash one append-only journal entry into the preceding entry's chain."""
        envelope = json.dumps({
            "run_id": run_id, "sequence": sequence, "kind": kind,
            "message": message, "data_json": data_json,
            "created_at": created_at, "previous_sha256": previous_sha256,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(envelope.encode("utf-8")).hexdigest()

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
                    event_sha256 TEXT NOT NULL DEFAULT '',
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
                    checkpoint_sha256 TEXT NOT NULL DEFAULT '',
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
                CREATE TABLE IF NOT EXISTS agent_run_leases (
                    run_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_run_leases_expiry
                    ON agent_run_leases(expires_at);
                CREATE TABLE IF NOT EXISTS workforce_runs (
                    run_id TEXT PRIMARY KEY, objective TEXT NOT NULL,
                    plan_json TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, completed_at TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}', owner_id TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS workforce_tasks (
                    run_id TEXT NOT NULL, task_id TEXT NOT NULL, role TEXT NOT NULL,
                    objective TEXT NOT NULL, depends_on_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL, model_name TEXT NOT NULL DEFAULT '',
                    child_run_id TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                    started_at TEXT, completed_at TEXT,
                    PRIMARY KEY(run_id, task_id),
                    FOREIGN KEY(run_id) REFERENCES workforce_runs(run_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS workforce_events (
                    run_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL, message TEXT NOT NULL,
                    data_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    event_sha256 TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(run_id, sequence),
                    FOREIGN KEY(run_id) REFERENCES workforce_runs(run_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_workforce_events_time
                    ON workforce_events(run_id, created_at);
            """)
            self._migrate_checkpoint_integrity(db)

    @staticmethod
    def _migrate_checkpoint_integrity(db: sqlite3.Connection) -> None:
        """Add the binding-wide checkpoint hash to databases created before it existed."""
        columns = {row["name"] for row in db.execute("PRAGMA table_info(agent_run_checkpoints)")}
        if "checkpoint_sha256" not in columns:
            db.execute(
                "ALTER TABLE agent_run_checkpoints "
                "ADD COLUMN checkpoint_sha256 TEXT NOT NULL DEFAULT ''"
            )
        event_columns = {row["name"] for row in db.execute("PRAGMA table_info(agent_run_events)")}
        if "event_sha256" not in event_columns:
            db.execute(
                "ALTER TABLE agent_run_events "
                "ADD COLUMN event_sha256 TEXT NOT NULL DEFAULT ''"
            )

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
        """Durably append one hash-chained event and create a running record."""
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
            previous = db.execute(
                "SELECT event_sha256 FROM agent_run_events WHERE run_id=? "
                "ORDER BY sequence DESC LIMIT 1", (run_id,)
            ).fetchone()
            previous_sha256 = str(previous[0]) if previous and previous[0] else ""
            event_sha256 = self._event_sha256(
                run_id=run_id, sequence=sequence, kind=kind, message=message,
                data_json=encoded_data, created_at=created_at,
                previous_sha256=previous_sha256,
            )
            db.execute(
                "INSERT INTO agent_run_events("
                "run_id,sequence,kind,message,data_json,created_at,event_sha256) "
                "VALUES(?,?,?,?,?,?,?)",
                (run_id, sequence, kind, message, encoded_data, created_at, event_sha256),
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
                "SELECT sequence,kind,message,data_json,created_at,event_sha256 "
                "FROM agent_run_events WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return [{
            "sequence": row["sequence"],
            "kind": row["kind"],
            "message": row["message"],
            "data": json.loads(row["data_json"]),
            "timestamp": row["created_at"],
            "event_sha256": row["event_sha256"],
        } for row in rows]

    def verify_run_event_chain(self, run_id: str, *, strict: bool = False) -> bool:
        """Verify the journal hash chain; legacy blank hashes are visible by design."""
        previous = ""
        for row in self.list_run_events(run_id):
            stored = str(row.get("event_sha256") or "")
            if not stored:
                if strict:
                    return False
                previous = ""
                continue
            expected = self._event_sha256(
                run_id=run_id, sequence=int(row["sequence"]), kind=str(row["kind"]),
                message=str(row["message"]),
                data_json=json.dumps(row.get("data") or {}, ensure_ascii=False, default=str),
                created_at=str(row["timestamp"]), previous_sha256=previous,
            )
            if expected != stored:
                return False
            previous = stored
        return True

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

    def _checkpoint_key(self) -> bytes:
        """Load or create a machine-local key separate from the SQLite database."""
        path = self.checkpoint_key_path
        if path.exists():
            key = path.read_bytes()
            if len(key) != 32:
                raise ValueError("Checkpoint key must contain exactly 32 bytes")
            try:
                os.chmod(path, 0o600)
            except OSError:
                # Windows ACLs do not map cleanly to POSIX mode bits; the
                # key remains in the per-user application data directory.
                pass
            return key
        path.parent.mkdir(parents=True, exist_ok=True)
        key = os.urandom(32)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            existing = path.read_bytes()
            if len(existing) != 32:
                raise ValueError("Checkpoint key must contain exactly 32 bytes") from exc
            return existing
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(key)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return key

    def _encode_checkpoint_payload(self, payload_json: str, run_id: str) -> str:
        """Encrypt checkpoint conversation data at rest with AES-256-GCM."""
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._checkpoint_key()).encrypt(
            nonce, payload_json.encode("utf-8"), run_id.encode("utf-8")
        )
        return "enc:v1:" + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def _decode_checkpoint_payload(self, stored: str, run_id: str) -> str:
        """Decrypt a checkpoint, preserving compatibility with legacy plaintext rows."""
        if not stored.startswith("enc:v1:"):
            return stored
        try:
            encoded = base64.urlsafe_b64decode(stored[len("enc:v1:"):].encode("ascii"))
            if len(encoded) < 13:
                raise ValueError("encrypted checkpoint is truncated")
            nonce, ciphertext = encoded[:12], encoded[12:]
            plaintext = AESGCM(self._checkpoint_key()).decrypt(
                nonce, ciphertext, run_id.encode("utf-8")
            )
            return plaintext.decode("utf-8")
        except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
            raise ValueError(
                f"Run checkpoint payload integrity check failed for {run_id}"
            ) from exc

    @staticmethod
    def _checkpoint_sha256(
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
        payload_json: str,
        created_at: str,
        updated_at: str,
    ) -> str:
        """Hash the complete checkpoint envelope, not just its conversation payload.

        This binds the recovery identity to the model digest, workspace, harness,
        tool manifest, state and counters. Altering any column invalidates the
        checkpoint instead of silently resuming a different execution.
        """
        envelope = json.dumps(
            {
                "run_id": run_id,
                "model_name": model_name,
                "model_digest": model_digest,
                "agent_title": agent_title,
                "workspace": workspace,
                "harness_mode": harness_mode,
                "tool_manifest_sha256": tool_manifest_sha256,
                "state": state,
                "step": step,
                "tool_calls": tool_calls,
                "payload_json": payload_json,
                "created_at": created_at,
                "updated_at": updated_at,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(envelope.encode("utf-8")).hexdigest()

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
        stored_payload_json = self._encode_checkpoint_payload(payload_json, run_id)
        payload_sha256 = hashlib.sha256(stored_payload_json.encode("utf-8")).hexdigest()
        checkpoint_sha256 = self._checkpoint_sha256(
            run_id=run_id, model_name=model_name, model_digest=model_digest,
            agent_title=agent_title, workspace=workspace, harness_mode=harness_mode,
            tool_manifest_sha256=tool_manifest_sha256, state=state, step=step,
            tool_calls=tool_calls, payload_json=stored_payload_json, created_at=created_at,
            updated_at=updated_at,
        )
        with self._connection() as db:
            # A checkpoint is self-contained: create the crash-visible run row if
            # the caller persisted the snapshot before its first journal event.
            db.execute(
                "INSERT OR IGNORE INTO agent_runs("
                "run_id,model_name,agent_title,task,status,created_at,completed_at,trace_json"
                ") VALUES(?,?,?,?,?,?,NULL,'[]')",
                (run_id, model_name, agent_title, str(payload.get("task") or ""),
                 "running", created_at),
            )
            db.execute(
                """INSERT INTO agent_run_checkpoints(
                    run_id,model_name,model_digest,agent_title,workspace,harness_mode,
                    tool_manifest_sha256,state,step,tool_calls,payload_json,payload_sha256,
                    checkpoint_sha256,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                    model_name=excluded.model_name, model_digest=excluded.model_digest,
                    agent_title=excluded.agent_title, workspace=excluded.workspace,
                    harness_mode=excluded.harness_mode,
                    tool_manifest_sha256=excluded.tool_manifest_sha256,
                    state=excluded.state, step=excluded.step, tool_calls=excluded.tool_calls,
                    payload_json=excluded.payload_json, payload_sha256=excluded.payload_sha256,
                    checkpoint_sha256=excluded.checkpoint_sha256,
                    updated_at=excluded.updated_at""",
                (run_id, model_name, model_digest, agent_title, workspace, harness_mode,
                 tool_manifest_sha256, state, step, tool_calls, stored_payload_json,
                 payload_sha256, checkpoint_sha256, created_at, updated_at),
            )

    def get_run_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        """Load and fully verify one run checkpoint before it can be used."""
        with self._connection() as db:
            row = db.execute(
                """SELECT c.run_id,c.model_name,c.model_digest,c.agent_title,c.workspace,
                          c.harness_mode,c.tool_manifest_sha256,c.state,c.step,c.tool_calls,
                          c.payload_json,c.payload_sha256,c.checkpoint_sha256,c.created_at,
                          c.updated_at,r.status AS run_status
                   FROM agent_run_checkpoints c
                   LEFT JOIN agent_runs r ON r.run_id=c.run_id
                   WHERE c.run_id=?""",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        stored = str(row["payload_json"])
        digest = hashlib.sha256(stored.encode("utf-8")).hexdigest()
        if digest != row["payload_sha256"]:
            raise ValueError(f"Run checkpoint payload integrity check failed for {run_id}")
        expected = self._checkpoint_sha256(
            run_id=str(row["run_id"]),
            model_name=str(row["model_name"]),
            model_digest=str(row["model_digest"]),
            agent_title=str(row["agent_title"]),
            workspace=str(row["workspace"]),
            harness_mode=str(row["harness_mode"]),
            tool_manifest_sha256=str(row["tool_manifest_sha256"]),
            state=str(row["state"]),
            step=int(row["step"]),
            tool_calls=int(row["tool_calls"]),
            payload_json=stored,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
        if not row["checkpoint_sha256"] or expected != row["checkpoint_sha256"]:
            raise ValueError(
                f"Run checkpoint binding integrity check failed for {run_id}; "
                "the model, workspace, harness or tool binding may have been altered"
            )
        try:
            payload = json.loads(self._decode_checkpoint_payload(stored, run_id))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Run checkpoint payload is not valid JSON for {run_id}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Run checkpoint payload is not an object for {run_id}")
        return {
            "run_id": row["run_id"], "model_name": row["model_name"],
            "model_digest": row["model_digest"], "agent_title": row["agent_title"],
            "workspace": row["workspace"], "harness_mode": row["harness_mode"],
            "tool_manifest_sha256": row["tool_manifest_sha256"], "state": row["state"],
            "step": row["step"], "tool_calls": row["tool_calls"],
            "payload": payload, "payload_sha256": row["payload_sha256"],
            "checkpoint_sha256": row["checkpoint_sha256"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "run_status": row["run_status"],
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

    def mark_run_checkpoint_needs_review(self, run_id: str) -> bool:
        """Durably move an interrupted tool checkpoint into the human-review state."""
        with self._connection() as db:
            row = db.execute(
                """SELECT run_id,model_name,model_digest,agent_title,workspace,harness_mode,
                          tool_manifest_sha256,state,step,tool_calls,payload_json,payload_sha256,
                          created_at,updated_at
                   FROM agent_run_checkpoints WHERE run_id=?""",
                (run_id,),
            ).fetchone()
            if row is None or row["state"] != "tool_in_progress":
                return False
            payload_json = str(row["payload_json"])
            if hashlib.sha256(payload_json.encode("utf-8")).hexdigest() != row["payload_sha256"]:
                raise ValueError(f"Run checkpoint payload integrity check failed for {run_id}")
            digest = self._checkpoint_sha256(
                run_id=str(row["run_id"]),
                model_name=str(row["model_name"]),
                model_digest=str(row["model_digest"]),
                agent_title=str(row["agent_title"]),
                workspace=str(row["workspace"]),
                harness_mode=str(row["harness_mode"]),
                tool_manifest_sha256=str(row["tool_manifest_sha256"]),
                state="needs_review",
                step=int(row["step"]),
                tool_calls=int(row["tool_calls"]),
                payload_json=payload_json,
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            cursor = db.execute(
                "UPDATE agent_run_checkpoints SET state='needs_review', checkpoint_sha256=? "
                "WHERE run_id=? AND state='tool_in_progress'",
                (digest, run_id),
            )
            return cursor.rowcount > 0

    def delete_run_checkpoint(self, run_id: str) -> bool:
        with self._connection() as db:
            cursor = db.execute(
                "DELETE FROM agent_run_checkpoints WHERE run_id=?", (run_id,)
            )
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Multi-process run leases
    # ------------------------------------------------------------------

    def acquire_run_lease(self, run_id: str, owner_id: str, ttl_seconds: int = 120) -> bool:
        """Atomically acquire a resume/execution lease for one run.

        An expired lease is reclaimable. A live lease owned by another process
        is never stolen, which prevents two processes from resuming the same
        checkpoint concurrently.
        """
        if not run_id.strip() or not owner_id.strip():
            raise ValueError("Lease run_id and owner_id must be non-empty")
        ttl = max(5, min(int(ttl_seconds), 3_600))
        now = _utc_now()
        expires = now + timedelta(seconds=ttl)
        stamp = now.isoformat()
        expires_stamp = expires.isoformat()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT owner_id,expires_at FROM agent_run_leases WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is not None and str(row["owner_id"]) != owner_id:
                try:
                    held_until = _parse_utc(str(row["expires_at"]))
                except ValueError:
                    held_until = now
                if held_until > now:
                    return False
            db.execute(
                """INSERT INTO agent_run_leases(
                    run_id,owner_id,acquired_at,heartbeat_at,expires_at
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                    owner_id=excluded.owner_id, acquired_at=excluded.acquired_at,
                    heartbeat_at=excluded.heartbeat_at, expires_at=excluded.expires_at""",
                (run_id, owner_id, stamp, stamp, expires_stamp),
            )
        return True

    def renew_run_lease(self, run_id: str, owner_id: str, ttl_seconds: int = 120) -> bool:
        """Renew a held lease without allowing an expired or foreign owner to renew."""
        ttl = max(5, min(int(ttl_seconds), 3_600))
        now = _utc_now()
        with self._connection() as db:
            row = db.execute(
                "SELECT owner_id,expires_at FROM agent_run_leases WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is None or str(row["owner_id"]) != owner_id:
                return False
            try:
                if _parse_utc(str(row["expires_at"])) <= now:
                    return False
            except ValueError:
                return False
            cursor = db.execute(
                "UPDATE agent_run_leases SET heartbeat_at=?, expires_at=? "
                "WHERE run_id=? AND owner_id=?",
                (now.isoformat(), (now + timedelta(seconds=ttl)).isoformat(),
                 run_id, owner_id),
            )
            return cursor.rowcount > 0

    def release_run_lease(self, run_id: str, owner_id: str) -> bool:
        """Release only a lease owned by the caller."""
        with self._connection() as db:
            cursor = db.execute(
                "DELETE FROM agent_run_leases WHERE run_id=? AND owner_id=?",
                (run_id, owner_id),
            )
            return cursor.rowcount > 0

    def get_run_lease(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT run_id,owner_id,acquired_at,heartbeat_at,expires_at "
                "FROM agent_run_leases WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            expires_at = _parse_utc(str(row["expires_at"]))
            active = expires_at > _utc_now()
        except ValueError:
            active = False
        return {
            "run_id": row["run_id"], "owner_id": row["owner_id"],
            "acquired_at": row["acquired_at"], "heartbeat_at": row["heartbeat_at"],
            "expires_at": row["expires_at"], "active": active,
        }

    # ------------------------------------------------------------------
    # Workforce orchestration state
    # ------------------------------------------------------------------

    @staticmethod
    def _bounded_json(payload: Any, *, limit: int = 1_000_000) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        if len(encoded.encode("utf-8")) > limit:
            raise ValueError("Persisted orchestration payload exceeds the 1 MB limit")
        return encoded

    def create_workforce_run(
        self, run_id: str, objective: str, plan: dict[str, Any], *,
        status: str = "planned", owner_id: str = "",
    ) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT INTO workforce_runs("
                "run_id,objective,plan_json,status,created_at,completed_at,result_json,owner_id"
                ") VALUES(?,?,?,?,?,NULL,'{}',?)",
                (run_id, objective, self._bounded_json(plan), status,
                 _utc_now().isoformat(), owner_id),
            )

    def save_workforce_tasks(self, run_id: str, tasks: list[dict[str, Any]]) -> None:
        with self._connection() as db:
            for task in tasks:
                db.execute(
                    """INSERT INTO workforce_tasks(
                        run_id,task_id,role,objective,depends_on_json,status,model_name,
                        child_run_id,attempts,result_json,error,started_at,completed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) """
                    "ON CONFLICT(run_id,task_id) DO UPDATE SET "
                    "role=excluded.role, objective=excluded.objective, "
                    "depends_on_json=excluded.depends_on_json",
                    (run_id, str(task["task_id"]), str(task.get("role") or ""),
                     str(task.get("objective") or ""),
                     self._bounded_json(list(task.get("depends_on") or []), limit=100_000),
                     str(task.get("status") or "pending"),
                     str(task.get("model_name") or ""), str(task.get("child_run_id") or ""),
                     int(task.get("attempts") or 0),
                     self._bounded_json(task.get("result") or {}),
                     str(task.get("error") or ""),
                     task.get("started_at"), task.get("completed_at")),
                )

    def update_workforce_task(
        self, run_id: str, task_id: str, *,
        status: str | None = None, model_name: str | None = None,
        child_run_id: str | None = None, attempts: int | None = None,
        result: dict[str, Any] | None = None, error: str | None = None,
        started_at: str | None = None, completed_at: str | None = None,
    ) -> None:
        fields: list[str] = []
        parameters: list[Any] = []
        for column, value in (
            ("status", status), ("model_name", model_name),
            ("child_run_id", child_run_id), ("attempts", attempts),
            ("result_json", self._bounded_json(result) if result is not None else None),
            ("error", error), ("started_at", started_at),
            ("completed_at", completed_at),
        ):
            if value is not None:
                fields.append(f"{column}=?")
                parameters.append(value)
        if not fields:
            return
        parameters.extend((run_id, task_id))
        with self._connection() as db:
            db.execute(
                f"UPDATE workforce_tasks SET {', '.join(fields)} "
                "WHERE run_id=? AND task_id=?", parameters
            )

    def list_workforce_tasks(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT run_id,task_id,role,objective,depends_on_json,status,model_name,"
                "child_run_id,attempts,result_json,error,started_at,completed_at "
                "FROM workforce_tasks WHERE run_id=? ORDER BY rowid", (run_id,)
            ).fetchall()
        return [{
            "run_id": row["run_id"], "task_id": row["task_id"],
            "role": row["role"], "objective": row["objective"],
            "depends_on": json.loads(row["depends_on_json"]),
            "status": row["status"], "model_name": row["model_name"],
            "child_run_id": row["child_run_id"], "attempts": row["attempts"],
            "result": json.loads(row["result_json"]), "error": row["error"],
            "started_at": row["started_at"], "completed_at": row["completed_at"],
        } for row in rows]

    def save_workforce_result(
        self, run_id: str, status: str, result: dict[str, Any],
        *, completed_at: str | None = None,
    ) -> None:
        with self._connection() as db:
            cursor = db.execute(
                "UPDATE workforce_runs SET status=?, result_json=?, completed_at=? "
                "WHERE run_id=?",
                (status, self._bounded_json(result), completed_at or _utc_now().isoformat(), run_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Unknown workforce run: {run_id}")

    def append_workforce_event(self, run_id: str, event: dict[str, Any]) -> int:
        created_at = str(event.get("timestamp") or _utc_now().isoformat())
        kind = str(event.get("kind") or "event")
        message = str(event.get("message") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        encoded_data = self._bounded_json(data, limit=200_000)
        with self._connection() as db:
            row = db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM workforce_events WHERE run_id=?",
                (run_id,),
            ).fetchone()
            sequence = int(row[0])
            previous = db.execute(
                "SELECT event_sha256 FROM workforce_events WHERE run_id=? "
                "ORDER BY sequence DESC LIMIT 1", (run_id,)
            ).fetchone()
            previous_sha256 = str(previous[0]) if previous and previous[0] else ""
            event_sha256 = self._event_sha256(
                run_id=run_id, sequence=sequence, kind=kind, message=message,
                data_json=encoded_data, created_at=created_at,
                previous_sha256=previous_sha256,
            )
            db.execute(
                "INSERT INTO workforce_events("
                "run_id,sequence,kind,message,data_json,created_at,event_sha256) "
                "VALUES(?,?,?,?,?,?,?)",
                (run_id, sequence, kind, message, encoded_data, created_at, event_sha256),
            )
        return sequence

    def list_workforce_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT sequence,kind,message,data_json,created_at,event_sha256 "
                "FROM workforce_events WHERE run_id=? ORDER BY sequence", (run_id,)
            ).fetchall()
        return [{
            "sequence": row["sequence"], "kind": row["kind"],
            "message": row["message"], "data": json.loads(row["data_json"]),
            "timestamp": row["created_at"], "event_sha256": row["event_sha256"],
        } for row in rows]

    def verify_workforce_event_chain(self, run_id: str, *, strict: bool = False) -> bool:
        previous = ""
        for row in self.list_workforce_events(run_id):
            stored = str(row.get("event_sha256") or "")
            if not stored:
                if strict:
                    return False
                previous = ""
                continue
            expected = self._event_sha256(
                run_id=run_id, sequence=int(row["sequence"]), kind=str(row["kind"]),
                message=str(row["message"]),
                data_json=json.dumps(row.get("data") or {}, ensure_ascii=False, default=str),
                created_at=str(row["timestamp"]), previous_sha256=previous,
            )
            if expected != stored:
                return False
            previous = stored
        return True

    def get_workforce_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT run_id,objective,plan_json,status,created_at,completed_at,"
                "result_json,owner_id FROM workforce_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"], "objective": row["objective"],
            "plan": json.loads(row["plan_json"]), "status": row["status"],
            "created_at": row["created_at"], "completed_at": row["completed_at"],
            "result": json.loads(row["result_json"]), "owner_id": row["owner_id"],
            "tasks": self.list_workforce_tasks(run_id),
        }

    def list_workforce_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 500))
        with self._connection() as db:
            rows = db.execute(
                "SELECT run_id,objective,status,created_at,completed_at,owner_id "
                "FROM workforce_runs ORDER BY created_at DESC LIMIT ?", (bounded,)
            ).fetchall()
        return [{
            "run_id": row["run_id"], "objective": row["objective"],
            "status": row["status"], "created_at": row["created_at"],
            "completed_at": row["completed_at"], "owner_id": row["owner_id"],
        } for row in rows]
