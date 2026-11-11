"""Host-owned artifact manifests and the strict independent verifier contract.

A verifier model may describe what it observed, but it cannot establish the
truth of the workspace by writing a file into that same workspace. The host
snapshots the deliverables first, gives the verifier only read access, validates
its evidence references against the snapshot, and then writes the audit record.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from ollama_workstation.models.schemas import utc_now

VERIFIER_PROTOCOL = "11vated-verifier/1"
VERIFIER_SCHEMA_VERSION = 1
DEFAULT_VERDICT_PATH = "workforce/verification.json"
Verdict = Literal["PASS", "FAIL"]


def verdict_output_schema() -> dict[str, Any]:
    """Provider-enforced schema for a verifier verdict.

    Prompt instructions alone are not a contract. Ollama receives this schema
    through ``format`` for the final formatting pass, so malformed prose cannot
    silently become the host's verification record.
    """
    return {
        "type": "object",
        "required": ["schema_version", "verdict", "summary", "evidence"],
        "properties": {
            "schema_version": {"type": "integer", "enum": [VERIFIER_SCHEMA_VERSION]},
            "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
            "summary": {"type": "string", "minLength": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["artifact", "observation"],
                    "properties": {
                        "artifact": {"type": "string"},
                        "observation": {"type": "string", "minLength": 1},
                        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    },
                },
            },
        },
    }


class VerificationProtocolError(ValueError):
    """A verifier response or artifact manifest violated the host contract."""


def canonical_artifact_path(value: str) -> str:
    """Normalize a workspace-relative artifact path and reject escapes."""
    raw = str(value).strip().replace("\\", "/")
    parts = tuple(part for part in raw.split("/") if part not in {"", "."})
    if (
        not parts
        or raw.startswith("/")
        or raw.startswith("~")
        or (len(raw) > 1 and raw[1] == ":")
        or ".." in parts
        or "\x00" in raw
    ):
        raise VerificationProtocolError(
            f"Artifact path must stay inside the workspace: {value!r}"
        )
    return "/".join(parts)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    path: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    """Immutable snapshot of the deliverables a verifier is allowed to cite."""

    created_at: str
    files: tuple[ArtifactRecord, ...]

    @classmethod
    def build(
        cls,
        workspace: Path,
        targets: list[str] | tuple[str, ...],
        *,
        require_files: bool = True,
    ) -> ArtifactManifest:
        root = workspace.expanduser().resolve()
        normalized = sorted({canonical_artifact_path(item) for item in targets})
        if not normalized:
            raise VerificationProtocolError(
                "A verifier run requires at least one host artifact target."
            )
        records: list[ArtifactRecord] = []
        for relative in normalized:
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise VerificationProtocolError(
                    f"Artifact path escapes the workspace: {relative!r}"
                ) from exc
            if not path.is_file():
                if require_files:
                    raise VerificationProtocolError(
                        f"Required artifact is missing before verification: {relative}"
                    )
                continue
            records.append(
                ArtifactRecord(relative, path.stat().st_size, _sha256_file(path))
            )
        if require_files and not records:
            raise VerificationProtocolError("No verifiable artifacts exist.")
        return cls(created_at=utc_now(), files=tuple(records))

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "protocol": VERIFIER_PROTOCOL,
            "files": [record.to_dict() for record in self.files],
        }

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(record.path for record in self.files)

    def record_for(self, artifact: str) -> ArtifactRecord | None:
        normalized = canonical_artifact_path(artifact)
        return next((record for record in self.files if record.path == normalized), None)

    def verify_unchanged(self, workspace: Path) -> tuple[str, ...]:
        """Return host-visible changes since the pre-verification snapshot."""
        root = workspace.expanduser().resolve()
        errors: list[str] = []
        for record in self.files:
            path = (root / record.path).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                errors.append(f"{record.path}: path escapes the workspace")
                continue
            if not path.is_file():
                errors.append(f"{record.path}: artifact disappeared during verification")
                continue
            if path.stat().st_size != record.size_bytes:
                errors.append(f"{record.path}: artifact size changed during verification")
                continue
            if _sha256_file(path) != record.sha256:
                errors.append(f"{record.path}: artifact digest changed during verification")
        return tuple(errors)


@dataclass(frozen=True, slots=True)
class VerdictEvidence:
    artifact: str
    observation: str
    sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {"artifact": self.artifact, "observation": self.observation}
        if self.sha256:
            payload["sha256"] = self.sha256
        return payload


@dataclass(frozen=True, slots=True)
class StructuredVerdict:
    verdict: Verdict
    summary: str
    evidence: tuple[VerdictEvidence, ...]
    confidence: float | None = None

    @classmethod
    def parse(cls, text: str) -> StructuredVerdict:
        payload = _extract_json_object(text)
        schema_version = payload.get("schema_version")
        if schema_version != VERIFIER_SCHEMA_VERSION:
            raise VerificationProtocolError(
                f"Verifier schema_version must be {VERIFIER_SCHEMA_VERSION}."
            )
        raw_verdict = str(payload.get("verdict") or "").strip().upper()
        if raw_verdict not in {"PASS", "FAIL"}:
            raise VerificationProtocolError(
                "Verifier verdict must be exactly PASS or FAIL."
            )
        summary = str(payload.get("summary") or "").strip()
        if not summary:
            raise VerificationProtocolError("Verifier summary cannot be empty.")
        raw_evidence = payload.get("evidence")
        if not isinstance(raw_evidence, list):
            raise VerificationProtocolError("Verifier evidence must be a JSON array.")
        evidence: list[VerdictEvidence] = []
        for index, item in enumerate(raw_evidence):
            if not isinstance(item, dict):
                raise VerificationProtocolError(
                    f"Verifier evidence[{index}] must be an object."
                )
            artifact = canonical_artifact_path(str(item.get("artifact") or ""))
            observation = str(item.get("observation") or "").strip()
            if not observation:
                raise VerificationProtocolError(
                    f"Verifier evidence[{index}] requires an observation."
                )
            digest = str(item.get("sha256") or "").strip().lower()
            if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise VerificationProtocolError(
                    f"Verifier evidence[{index}] sha256 must be 64 hex characters."
                )
            evidence.append(VerdictEvidence(artifact, observation, digest))
        if raw_verdict == "PASS" and not evidence:
            raise VerificationProtocolError(
                "A PASS verdict requires at least one artifact evidence item."
            )
        confidence = payload.get("confidence")
        if confidence is not None:
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise VerificationProtocolError("Verifier confidence must be numeric.")
            confidence = max(0.0, min(1.0, float(confidence)))
        return cls(raw_verdict, summary, tuple(evidence), confidence)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": VERIFIER_SCHEMA_VERSION,
            "verdict": self.verdict,
            "summary": self.summary,
            "evidence": [item.to_dict() for item in self.evidence],
        }
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        return payload


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.I | re.S
    )
    if fenced:
        stripped = fenced.group(1).strip()
    candidates: list[Any] = []
    try:
        candidates.append(json.loads(stripped))
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(stripped):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        candidates.append(value)
    for value in candidates:
        if isinstance(value, dict) and "verdict" in value:
            return value
    raise VerificationProtocolError(
        "Verifier must return a single JSON object with the structured verdict."
    )


def validate_verdict_evidence(
    verdict: StructuredVerdict,
    manifest: ArtifactManifest,
) -> tuple[str, ...]:
    """Validate every model claim against the host snapshot."""
    errors: list[str] = []
    manifest_paths = set(manifest.paths)
    cited: set[str] = set()
    for item in verdict.evidence:
        record = manifest.record_for(item.artifact)
        if record is None:
            errors.append(f"{item.artifact}: not present in the host artifact manifest")
            continue
        cited.add(record.path)
        if item.sha256 and item.sha256 != record.sha256:
            errors.append(f"{record.path}: verifier sha256 does not match the host snapshot")
    if verdict.verdict == "PASS":
        missing = sorted(manifest_paths - cited)
        if missing:
            errors.append(
                "PASS did not cite host artifact(s): " + ", ".join(missing)
            )
    return tuple(errors)
