"""Local, scoped agent memory with explicit writes and deterministic retrieval."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from ollama_workstation.storage.repository import Repository

MEMORY_SCOPES = {"user", "project", "agent"}
MEMORY_CATEGORIES = {"fact", "decision", "preference", "constraint", "procedure", "lesson"}


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryStore:
    """Memory is opt-in on write, scoped on read, and treated as untrusted context."""

    MAX_CONTENT_CHARS = 8_000

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def remember(self, content: str, *, scope: str, scope_id: str,
                 category: str = "fact", tags: list[str] | None = None,
                 source: str = "user", confidence: float = 1.0) -> MemoryRecord:
        normalized = content.strip()
        if not normalized or len(normalized) > self.MAX_CONTENT_CHARS:
            raise ValueError(f"Memory content must contain 1-{self.MAX_CONTENT_CHARS} characters")
        if scope not in MEMORY_SCOPES:
            raise ValueError(f"Unsupported memory scope: {scope}")
        if not scope_id.strip():
            raise ValueError("Memory scope_id cannot be empty")
        if category not in MEMORY_CATEGORIES:
            raise ValueError(f"Unsupported memory category: {category}")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("Memory confidence must be between 0 and 1")
        now = datetime.now(UTC).isoformat()
        record = MemoryRecord(
            memory_id=str(uuid.uuid4()), scope=scope, scope_id=scope_id,
            category=category, content=normalized,
            tags=list(dict.fromkeys(tag.strip().lower() for tag in (tags or []) if tag.strip()))[:30],
            source=source[:120], confidence=confidence, created_at=now, updated_at=now,
        )
        self.repository.save_memory(record.to_dict())
        return record

    def recall(self, query: str, scopes: list[tuple[str, str]], limit: int = 6) -> list[MemoryRecord]:
        if not scopes:
            return []
        candidates = self.repository.list_memories(scopes, limit=500)
        terms = set(re.findall(r"[a-z0-9_'-]{2,}", query.casefold()))
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
                content_overlap = len(terms & content_terms)
                tag_overlap = len(terms & tag_terms)
                score = (content_overlap + 2.0 * tag_overlap) / len(terms)
                if query.strip().casefold() in content:
                    score += 2.0
            if score <= 0:
                continue
            score *= 0.5 + 0.5 * record.confidence
            ranked.append((score, record))
        ranked.sort(key=lambda pair: (pair[0], pair[1].updated_at), reverse=True)
        selected = [record for _, record in ranked[:max(1, min(limit, 20))]]
        if selected:
            self.repository.touch_memories(
                [record.memory_id for record in selected], datetime.now(UTC).isoformat()
            )
        return selected

    def list(self, scopes: list[tuple[str, str]], limit: int = 100) -> list[MemoryRecord]:
        return [MemoryRecord(**raw) for raw in self.repository.list_memories(scopes, limit=limit)]

    def forget(self, memory_id: str) -> bool:
        return self.repository.archive_memory(memory_id)
