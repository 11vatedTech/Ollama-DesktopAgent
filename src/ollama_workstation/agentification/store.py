"""SQLite persistence for qualifications, agent definitions, skills and verification.

The store shares the workstation database file with ``Repository`` but owns its
own tables, so the agentification domain can evolve without touching run
journals or leases. Agent and skill versions are immutable rows: a change is a
new version, and the previous version with its evidence is always preserved.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ollama_workstation.models.schemas import utc_now

SCHEMA_VERSION = 1


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class StoreIntegrityError(RuntimeError):
    """A stored record no longer matches the digest written with it."""


class AgentStore:
    def __init__(self, database: Path) -> None:
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS agentification_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS harness_qualifications (
                    qualification_id TEXT PRIMARY KEY, model_name TEXT NOT NULL,
                    model_digest TEXT NOT NULL, status TEXT NOT NULL,
                    selected_harness TEXT, tool_manifest_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL, record_json TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_qualification_model
                    ON harness_qualifications(model_name, model_digest, created_at);
                CREATE TABLE IF NOT EXISTS agent_definitions (
                    agent_id TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL,
                    created_at TEXT NOT NULL, config_json TEXT NOT NULL,
                    config_sha256 TEXT NOT NULL, PRIMARY KEY(agent_id, version)
                );
                CREATE TABLE IF NOT EXISTS agent_state (
                    agent_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                    active_version INTEGER, updated_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS skills (
                    skill_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, spec_json TEXT NOT NULL,
                    spec_sha256 TEXT NOT NULL, PRIMARY KEY(skill_id, version)
                );
                CREATE TABLE IF NOT EXISTS skill_evaluations (
                    evaluation_id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
                    version INTEGER NOT NULL, model_digest TEXT NOT NULL, harness TEXT NOT NULL,
                    outcome TEXT NOT NULL, created_at TEXT NOT NULL, record_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS verification_reports (
                    report_sha256 TEXT PRIMARY KEY, run_id TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, report_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_verification_run
                    ON verification_reports(run_id, created_at);
                CREATE TABLE IF NOT EXISTS agent_run_bindings (
                    run_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, version INTEGER NOT NULL,
                    config_sha256 TEXT NOT NULL, qualification_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            db.execute(
                "INSERT OR IGNORE INTO agentification_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _load(text: str, digest: str, what: str) -> dict[str, Any]:
        payload = json.loads(text)
        if canonical_sha256(payload) != digest:
            raise StoreIntegrityError(f"Stored {what} failed its integrity check")
        return dict(payload)

    # -- harness qualifications -------------------------------------------------
    def save_qualification(self, record: dict[str, Any]) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT INTO harness_qualifications VALUES(?,?,?,?,?,?,?,?,?)",
                (record["qualification_id"], record["model_name"], record["model_digest"],
                 record["status"], record.get("selected_harness"),
                 record["tool_manifest_sha256"], record["created_at"],
                 json.dumps(record, ensure_ascii=False), canonical_sha256(record)),
            )

    def get_qualification(self, qualification_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT record_json, record_sha256 FROM harness_qualifications "
                "WHERE qualification_id=?", (qualification_id,),
            ).fetchone()
        return None if row is None else self._load(row[0], row[1], "qualification")

    def latest_qualification(
        self, model_name: str, model_digest: str, tool_manifest_sha256: str | None = None,
    ) -> dict[str, Any] | None:
        query = ("SELECT record_json, record_sha256 FROM harness_qualifications "
                 "WHERE model_name=? AND model_digest=?")
        values: list[Any] = [model_name, model_digest]
        if tool_manifest_sha256 is not None:
            query += " AND tool_manifest_sha256=?"
            values.append(tool_manifest_sha256)
        with self._connection() as db:
            row = db.execute(query + " ORDER BY created_at DESC LIMIT 1", values).fetchone()
        return None if row is None else self._load(row[0], row[1], "qualification")

    def qualifications_for(self, model_name: str, model_digest: str,
                           limit: int = 20) -> list[dict[str, Any]]:
        """Full, integrity-checked records for one model artifact, newest first."""
        with self._connection() as db:
            rows = db.execute(
                "SELECT record_json, record_sha256 FROM harness_qualifications "
                "WHERE model_name=? AND model_digest=? ORDER BY created_at DESC LIMIT ?",
                (model_name, model_digest, max(1, min(limit, 200))),
            ).fetchall()
        return [self._load(row[0], row[1], "qualification") for row in rows]

    def list_qualifications(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT qualification_id, model_name, model_digest, status, selected_harness, "
                "tool_manifest_sha256, created_at FROM harness_qualifications "
                "ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 1_000)),),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- agent definitions ------------------------------------------------------
    def save_agent_version(self, config: dict[str, Any]) -> None:
        """Insert an immutable version. Fails if that version already exists."""
        with self._connection() as db:
            db.execute(
                "INSERT INTO agent_definitions VALUES(?,?,?,?,?,?)",
                (config["agent_id"], int(config["version"]), config["name"],
                 config["created_at"], json.dumps(config, ensure_ascii=False),
                 canonical_sha256(config)),
            )
            db.execute(
                "INSERT OR IGNORE INTO agent_state(agent_id, status, active_version, updated_at) "
                "VALUES(?, 'draft', NULL, ?)", (config["agent_id"], utc_now()),
            )

    def get_agent_version(self, agent_id: str, version: int | None = None) -> dict[str, Any] | None:
        with self._connection() as db:
            if version is None:
                row = db.execute(
                    "SELECT config_json, config_sha256 FROM agent_definitions WHERE agent_id=? "
                    "ORDER BY version DESC LIMIT 1", (agent_id,),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT config_json, config_sha256 FROM agent_definitions "
                    "WHERE agent_id=? AND version=?", (agent_id, int(version)),
                ).fetchone()
        return None if row is None else self._load(row[0], row[1], "agent definition")

    def agent_versions(self, agent_id: str) -> list[int]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT version FROM agent_definitions WHERE agent_id=? ORDER BY version",
                (agent_id,),
            ).fetchall()
        return [int(row[0]) for row in rows]

    def get_agent_state(self, agent_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute("SELECT * FROM agent_state WHERE agent_id=?", (agent_id,)).fetchone()
        return None if row is None else dict(row)

    def set_agent_state(self, agent_id: str, status: str, active_version: int | None,
                        note: str = "") -> None:
        with self._connection() as db:
            updated = db.execute(
                "UPDATE agent_state SET status=?, active_version=?, updated_at=?, note=? "
                "WHERE agent_id=?", (status, active_version, utc_now(), note[:500], agent_id),
            ).rowcount
            if not updated:
                raise KeyError(agent_id)

    def list_agents(self) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT s.agent_id, s.status, s.active_version, s.updated_at, s.note, "
                "d.name, d.version AS latest_version FROM agent_state s JOIN agent_definitions d "
                "ON d.agent_id=s.agent_id AND d.version=(SELECT MAX(version) FROM "
                "agent_definitions WHERE agent_id=s.agent_id) ORDER BY d.name COLLATE NOCASE"
            ).fetchall()
        return [dict(row) for row in rows]

    def find_agent_id(self, reference: str) -> str | None:
        """Resolve an exact id, a unique id prefix or an exact (case-insensitive) name."""
        agents = self.list_agents()
        exact = [item["agent_id"] for item in agents if item["agent_id"] == reference]
        if exact:
            return str(exact[0])
        named = [item["agent_id"] for item in agents
                 if str(item["name"]).casefold() == reference.casefold()]
        if len(named) == 1:
            return str(named[0])
        prefixed = [item["agent_id"] for item in agents
                    if len(reference) >= 6 and str(item["agent_id"]).startswith(reference)]
        return str(prefixed[0]) if len(prefixed) == 1 else None

    def bind_run(self, run_id: str, agent_id: str, version: int, config_sha256: str,
                 qualification_id: str) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO agent_run_bindings VALUES(?,?,?,?,?,?)",
                (run_id, agent_id, int(version), config_sha256, qualification_id, utc_now()),
            )

    def get_run_binding(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM agent_run_bindings WHERE run_id=?", (run_id,)
            ).fetchone()
        return None if row is None else dict(row)

    # -- skills -------------------------------------------------------------------
    def save_skill_version(self, spec: dict[str, Any], status: str = "active") -> None:
        with self._connection() as db:
            db.execute(
                "INSERT INTO skills VALUES(?,?,?,?,?,?)",
                (spec["skill_id"], int(spec["version"]), status, utc_now(),
                 json.dumps(spec, ensure_ascii=False), canonical_sha256(spec)),
            )

    def get_skill(self, skill_id: str, version: int | None = None) -> dict[str, Any] | None:
        with self._connection() as db:
            if version is None:
                row = db.execute(
                    "SELECT spec_json, spec_sha256, status FROM skills WHERE skill_id=? "
                    "ORDER BY version DESC LIMIT 1", (skill_id,),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT spec_json, spec_sha256, status FROM skills "
                    "WHERE skill_id=? AND version=?", (skill_id, int(version)),
                ).fetchone()
        if row is None:
            return None
        return {**self._load(row[0], row[1], "skill"), "_status": row[2]}

    def list_skills(self) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT skill_id, MAX(version) AS version FROM skills GROUP BY skill_id "
                "ORDER BY skill_id"
            ).fetchall()
        found = [self.get_skill(str(row[0]), int(row[1])) for row in rows]
        return [item for item in found if item is not None]

    def set_skill_status(self, skill_id: str, version: int, status: str) -> bool:
        with self._connection() as db:
            return bool(db.execute(
                "UPDATE skills SET status=? WHERE skill_id=? AND version=?",
                (status, skill_id, int(version)),
            ).rowcount)

    def save_skill_evaluation(self, record: dict[str, Any]) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT INTO skill_evaluations VALUES(?,?,?,?,?,?,?,?)",
                (record["evaluation_id"], record["skill_id"], int(record["version"]),
                 record["model_digest"], record["harness"], record["outcome"],
                 record["created_at"], json.dumps(record, ensure_ascii=False)),
            )

    def list_skill_evaluations(self, skill_id: str) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT record_json FROM skill_evaluations WHERE skill_id=? ORDER BY created_at",
                (skill_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    # -- verification -------------------------------------------------------------
    def save_verification(self, report: dict[str, Any]) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO verification_reports VALUES(?,?,?,?,?)",
                (report["report_sha256"], report["run_id"], report["status"],
                 report["verified_at"], json.dumps(report, ensure_ascii=False)),
            )

    def list_verifications(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT report_json FROM verification_reports WHERE run_id=? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]
