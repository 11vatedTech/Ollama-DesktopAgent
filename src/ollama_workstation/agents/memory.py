"""Local, scoped agent memory with explicit writes and hybrid retrieval.

Retrieval fuses a deterministic keyword ranking with an optional local
embedding ranking (reciprocal-rank fusion). Embeddings come from a local Ollama
embedding model; when none is configured or it fails, retrieval is keyword-only
and says so - it never silently pretends to be semantic.

Writes are defended:

* near-duplicates reinforce the existing note instead of piling up copies;
* a note that is semantically close to an existing note in the same scope and
  category but disagrees on negation or numbers is recorded as a *conflict*;
  both notes are flagged on retrieval so the agent sees the disagreement;
* agent-written notes are capped at confidence 0.6 (only a user can assert a
  high-confidence fact), and instruction-shaped content ("ignore previous
  instructions", "system override", ...) is flagged as suspected poisoning;
* :meth:`MemoryStore.consolidate` archives stale, never-used, low-confidence notes.
"""

from __future__ import annotations

import array
import builtins
import hashlib
import math
import re
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.storage.repository import Repository

MEMORY_SCOPES = {"user", "project", "agent"}
MEMORY_CATEGORIES = {"fact", "decision", "preference", "constraint", "procedure", "lesson"}
AGENT_CONFIDENCE_CAP = 0.6
DUPLICATE_SIMILARITY = 0.96
CONFLICT_SIMILARITY = 0.80
_RRF_K = 60.0
SEMANTIC_FLOOR = 0.45
_INJECTION_PATTERNS = re.compile(
    r"ignore (all |any )?(previous|prior|above) (instructions|rules)|system override|"
    r"you are now|disregard (your|the) (instructions|task)|new instructions:|"
    r"exfiltrat|send .{0,40}(password|token|credential)", re.IGNORECASE)
_NEGATION = re.compile(r"\b(not|never|no|don't|doesn't|isn't|aren't|won't|cannot|can't|"
                       r"without|disabled?|false)\b", re.IGNORECASE)
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")

Embedder = Callable[[list[str]], list[list[float]]]


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    scope: str
    scope_id: str
    category: str
    content: str
    tags: list[str] = field(default_factory=list)
    source: str = "user"
    confidence: float = 1.0
    created_at: str = ""
    updated_at: str = ""
    last_accessed_at: str | None = None
    access_count: int = 0
    # Retrieval-time annotations (not persisted): "conflict", "suspected_injection".
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of this memory record."""
        return asdict(self)


class MemoryStore:
    """Memory is opt-in on write, scoped on read, and treated as untrusted context."""

    MAX_CONTENT_CHARS = 8_000

    def __init__(self, repository: Repository, *, embedder: Embedder | None = None,
                 embedding_model: str = "") -> None:
        """Create a memory service backed by the workstation repository.

        ``embedder`` maps texts to vectors (normally a local Ollama embedding
        model). Without it retrieval is keyword-only.
        """
        self.repository = repository
        self.embedder = embedder
        self.embedding_model = embedding_model or ("custom" if embedder else "")
        self.last_retrieval_mode = "keyword"
        self.last_embedding_error = ""
        with self.repository._connection() as db:  # noqa: SLF001 - same database, own tables
            db.executescript("""
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    memory_id TEXT NOT NULL, model TEXT NOT NULL, dimensions INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL, vector BLOB NOT NULL,
                    PRIMARY KEY(memory_id, model)
                );
                CREATE TABLE IF NOT EXISTS memory_conflicts (
                    memory_id TEXT NOT NULL, other_id TEXT NOT NULL, similarity REAL NOT NULL,
                    reason TEXT NOT NULL, detected_at TEXT NOT NULL,
                    PRIMARY KEY(memory_id, other_id)
                );
            """)

    # -- embeddings ----------------------------------------------------------------
    def _embed(self, texts: list[str]) -> list[list[float]] | None:
        if self.embedder is None or not texts:
            return None
        try:
            vectors = self.embedder(texts)
        except Exception as exc:  # noqa: BLE001 - degrade to keyword retrieval, recorded
            self.last_embedding_error = f"{type(exc).__name__}: {exc}"[:300]
            return None
        if len(vectors) != len(texts):
            self.last_embedding_error = "embedder returned the wrong number of vectors"
            return None
        self.last_embedding_error = ""
        return [_normalize(vector) for vector in vectors]

    def _store_vector(self, memory_id: str, content: str, vector: list[float]) -> None:
        blob = array.array("f", vector).tobytes()
        with self.repository._connection() as db:  # noqa: SLF001
            db.execute(
                "INSERT OR REPLACE INTO memory_embeddings(memory_id,model,dimensions,"
                "content_sha256,vector) VALUES(?,?,?,?,?)",
                (memory_id, self.embedding_model, len(vector), _sha(content), blob))

    def _vectors(self, records: list[MemoryRecord]) -> dict[str, list[float]]:
        """Stored vectors for these records, embedding any that are missing or stale."""
        if self.embedder is None or not records:
            return {}
        ids = [record.memory_id for record in records]
        found: dict[str, list[float]] = {}
        placeholders = ",".join("?" for _ in ids)
        with self.repository._connection() as db:  # noqa: SLF001
            rows = db.execute(
                f"SELECT memory_id, content_sha256, vector FROM memory_embeddings "
                f"WHERE model=? AND memory_id IN ({placeholders})",
                [self.embedding_model, *ids]).fetchall()
        digests = {record.memory_id: _sha(record.content) for record in records}
        for memory_id, digest, blob in rows:
            if digests.get(memory_id) == digest:
                values = array.array("f")
                values.frombytes(blob)
                found[memory_id] = list(values)
        missing = [record for record in records if record.memory_id not in found]
        if missing:
            vectors = self._embed([record.content for record in missing])
            if vectors is not None:
                for record, vector in zip(missing, vectors, strict=True):
                    self._store_vector(record.memory_id, record.content, vector)
                    found[record.memory_id] = vector
        return found

    # -- writes ----------------------------------------------------------------------
    def remember(self, content: str, *, scope: str, scope_id: str,
                 category: str = "fact", tags: list[str] | None = None,
                 source: str = "user", confidence: float = 1.0) -> MemoryRecord:
        """Persist an explicit, validated note in one named memory scope.

        A near-duplicate reinforces and returns the existing note. A likely
        contradiction is stored and linked to the note it disagrees with.
        """
        normalized, _ = WorkspaceTools.redact_sensitive_text(content.strip())
        if not normalized or len(normalized) > self.MAX_CONTENT_CHARS:
            raise ValueError(f"Memory content must contain 1-{self.MAX_CONTENT_CHARS} characters")
        if scope not in MEMORY_SCOPES:
            raise ValueError(f"Unsupported memory scope: {scope}")
        if not scope_id.strip():
            raise ValueError("Memory scope_id cannot be empty")
        if category not in MEMORY_CATEGORIES:
            raise ValueError(f"Unsupported memory category: {category}")
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("Memory confidence must be between 0 and 1")
        if source != "user":
            # Only a person can assert a high-confidence fact.
            confidence = min(confidence, AGENT_CONFIDENCE_CAP)
        now = datetime.now(UTC).isoformat()
        clean_tags = list(dict.fromkeys(
            tag.strip().lower() for tag in (tags or []) if tag.strip()))[:30]
        if _INJECTION_PATTERNS.search(normalized) and "suspected-injection" not in clean_tags:
            clean_tags = [*clean_tags[:29], "suspected-injection"]
            confidence = min(confidence, 0.2)

        existing = [MemoryRecord(**raw) for raw in
                    self.repository.list_memories([(scope, scope_id)], limit=500)]
        same_category = [item for item in existing if item.category == category]
        embedded = self._embed([normalized])
        vector: list[float] | None = embedded[0] if embedded else None
        conflicts: list[tuple[MemoryRecord, float, str]] = []
        if same_category:
            for other, similarity in self._similarities(normalized, vector, same_category):
                reason = _disagree(normalized, other.content)
                if similarity >= DUPLICATE_SIMILARITY and not reason:
                    reinforced = replace(
                        other, updated_at=now,
                        confidence=max(other.confidence, confidence),
                        tags=list(dict.fromkeys([*other.tags, *clean_tags]))[:30])
                    self.repository.save_memory(reinforced.to_dict())
                    return reinforced
                if similarity >= CONFLICT_SIMILARITY and reason:
                    conflicts.append((other, similarity, reason))
        record = MemoryRecord(
            memory_id=str(uuid.uuid4()), scope=scope, scope_id=scope_id,
            category=category, content=normalized, tags=clean_tags,
            source=source[:120], confidence=confidence, created_at=now, updated_at=now,
        )
        self.repository.save_memory(record.to_dict())
        if vector is not None:
            self._store_vector(record.memory_id, normalized, vector)
        if conflicts:
            with self.repository._connection() as db:  # noqa: SLF001
                for other, similarity, reason in conflicts:
                    for left, right in ((record.memory_id, other.memory_id),
                                        (other.memory_id, record.memory_id)):
                        db.execute(
                            "INSERT OR REPLACE INTO memory_conflicts VALUES(?,?,?,?,?)",
                            (left, right, round(similarity, 4), reason, now))
        return record

    def _similarities(self, text: str, vector: list[float] | None,
                      candidates: list[MemoryRecord]) -> list[tuple[MemoryRecord, float]]:
        """Cosine similarity when vectors exist, otherwise token Jaccard similarity."""
        if vector is not None:
            stored = self._vectors(candidates)
            if stored:
                return sorted(((item, _dot(vector, stored[item.memory_id]))
                               for item in candidates if item.memory_id in stored),
                              key=lambda pair: pair[1], reverse=True)
        tokens = _tokens(text)
        return sorted(((item, _jaccard(tokens, _tokens(item.content))) for item in candidates),
                      key=lambda pair: pair[1], reverse=True)

    def conflicts(self, memory_id: str) -> list[dict[str, Any]]:
        """Notes this one was detected to disagree with."""
        with self.repository._connection() as db:  # noqa: SLF001
            rows = db.execute(
                "SELECT other_id, similarity, reason, detected_at FROM memory_conflicts "
                "WHERE memory_id=?", (memory_id,)).fetchall()
        return [{"other_id": row[0], "similarity": row[1], "reason": row[2],
                 "detected_at": row[3]} for row in rows]

    # -- reads -----------------------------------------------------------------------
    def recall(self, query: str, scopes: list[tuple[str, str]], limit: int = 6) -> list[MemoryRecord]:
        """Rank relevant notes only within the caller-provided scopes."""
        if not scopes:
            return []
        limit = max(1, min(limit, 20))
        terms = set(re.findall(r"[a-z0-9_'-]{2,}", query.casefold()))
        keyword_ranked = self._keyword_rank(query, terms, scopes)
        semantic_ranked: list[tuple[float, MemoryRecord]] = []
        query_vector = self._embed([query.strip()]) if query.strip() else None
        if query_vector is not None:
            pool = [MemoryRecord(**raw) for raw in self.repository.list_memories(scopes, limit=500)]
            stored = self._vectors(pool)
            semantic_ranked = sorted(
                ((_dot(query_vector[0], stored[item.memory_id]), item)
                 for item in pool if item.memory_id in stored),
                key=lambda pair: pair[0], reverse=True)
            # Weak semantic matches are noise, not recall.
            semantic_ranked = [pair for pair in semantic_ranked if pair[0] >= SEMANTIC_FLOOR]
        self.last_retrieval_mode = "hybrid" if query_vector is not None else "keyword"
        fused: dict[str, float] = {}
        by_id: dict[str, MemoryRecord] = {}
        for ranking in (keyword_ranked, semantic_ranked):
            for position, (_score, record) in enumerate(ranking):
                by_id[record.memory_id] = record
                fused[record.memory_id] = fused.get(record.memory_id, 0.0) + (
                    (0.5 + 0.5 * record.confidence) / (_RRF_K + position + 1))
        ordered = sorted(fused, key=lambda key: (fused[key], by_id[key].updated_at), reverse=True)
        selected = [self._annotate(by_id[key]) for key in ordered[:limit]]
        if selected:
            self.repository.touch_memories(
                [record.memory_id for record in selected], datetime.now(UTC).isoformat()
            )
        return selected

    def _keyword_rank(self, query: str, terms: set[str],
                      scopes: list[tuple[str, str]]) -> list[tuple[float, MemoryRecord]]:
        candidates = (
            self.repository.search_memories(scopes, sorted(terms), limit=500)
            if terms else self.repository.list_memories(scopes, limit=500)
        )
        ranked: list[tuple[float, MemoryRecord]] = []
        for raw in candidates:
            record = MemoryRecord(**raw)
            content = record.content.casefold()
            tag_text = " ".join(record.tags).casefold()
            if not terms:
                score = 1.0
            else:
                content_terms = set(re.findall(r"[a-z0-9_'-]{2,}", content))
                tag_terms = set(re.findall(r"[a-z0-9_'-]{2,}", tag_text))
                score = (len(terms & content_terms) + 2.0 * len(terms & tag_terms)) / len(terms)
                if query.strip().casefold() in content:
                    score += 2.0
            if score <= 0:
                continue
            ranked.append((score * (0.5 + 0.5 * record.confidence), record))
        ranked.sort(key=lambda pair: (pair[0], pair[1].updated_at), reverse=True)
        return ranked

    def _annotate(self, record: MemoryRecord) -> MemoryRecord:
        flags: list[str] = []
        if self.conflicts(record.memory_id):
            flags.append("conflict")
        if "suspected-injection" in record.tags:
            flags.append("suspected_injection")
        return replace(record, flags=flags) if flags else record

    def list(self, scopes: list[tuple[str, str]], limit: int = 100) -> list[MemoryRecord]:
        """List active notes in explicitly requested scopes."""
        return [MemoryRecord(**raw) for raw in self.repository.list_memories(scopes, limit=limit)]

    def forget(self, memory_id: str) -> bool:
        """Archive one note so it is excluded from future retrieval."""
        return self.repository.archive_memory(memory_id)

    def consolidate(self, scopes: builtins.list[tuple[str, str]], *, max_idle_days: int = 90,
                    keep_confidence: float = 0.8,
                    now: datetime | None = None) -> builtins.list[str]:
        """Retention: archive notes idle for ``max_idle_days`` that were never
        retrieved and are below ``keep_confidence``. Returns archived ids."""
        moment = now or datetime.now(UTC)
        cutoff = moment - timedelta(days=max(1, max_idle_days))
        archived: list[str] = []
        for record in self.list(scopes, limit=5_000):
            try:
                last = datetime.fromisoformat(record.last_accessed_at or record.updated_at)
            except ValueError:
                continue
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            if (last < cutoff and record.access_count == 0
                    and record.confidence < keep_confidence and self.forget(record.memory_id)):
                archived.append(record.memory_id)
        return archived


def ollama_embedder(client: Any, model: str, *, keep_alive: str = "5m") -> Embedder:
    """Embedder backed by a local Ollama embedding model."""
    def embed(texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = client.embed(model, texts, keep_alive=keep_alive)
        return vectors
    return embed


def memory_store_for(repository: Repository, client: Any, settings: Any = None) -> MemoryStore:
    """Build the workstation memory store, semantic when an embedding model is configured."""
    model = str(getattr(settings, "memory_embedding_model", "auto") or "").strip()
    if model == "auto" and client is not None:
        model = _installed_embedding_model(client)
    if not model or model == "auto" or client is None:
        return MemoryStore(repository)
    return MemoryStore(repository, embedder=ollama_embedder(client, model),
                       embedding_model=model)


def _installed_embedding_model(client: Any) -> str:
    """Name of an installed embedding model, preferring nomic-embed-text; else ``""``."""
    try:
        names = [str(item.get("name") or item.get("model") or "") for item in client.list_models()]
    except Exception:  # noqa: BLE001 - Ollama offline: keyword retrieval
        return ""
    candidates = [name for name in names if "embed" in name.casefold()]
    preferred = [name for name in candidates if name.startswith("nomic-embed-text")]
    return (preferred or sorted(candidates) or [""])[0]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _dot(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_'-]{2,}", text.casefold()))


def _jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left and right else 0.0


def _disagree(new: str, old: str) -> str:
    """Name a surface disagreement between two similar notes, or ``""``."""
    if bool(_NEGATION.search(new)) != bool(_NEGATION.search(old)):
        return "negation differs"
    new_numbers, old_numbers = set(_NUMBER.findall(new)), set(_NUMBER.findall(old))
    if new_numbers and old_numbers and new_numbers != old_numbers:
        return "numbers differ"
    return ""
