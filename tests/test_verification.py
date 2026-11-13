from __future__ import annotations

import json
from pathlib import Path

import pytest

from ollama_workstation.agents.verification import (
    ArtifactManifest,
    StructuredVerdict,
    VerificationProtocolError,
    canonical_artifact_path,
    validate_verdict_evidence,
)


def _verdict(evidence: list[dict[str, str]], verdict: str = "PASS") -> str:
    return json.dumps({
        "schema_version": 2,
        "verdict": verdict,
        "summary": "Observed evidence.",
        "evidence": evidence,
        "failure_reasons": [],
    })


def test_manifest_hashes_and_detects_mutation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "workforce" / "result.md"
    target.parent.mkdir()
    target.write_text("first", encoding="utf-8")
    manifest = ArtifactManifest.build(workspace, ["workforce/result.md"])
    assert manifest.files[0].sha256
    assert manifest.verify_unchanged(workspace) == ()
    target.write_text("other", encoding="utf-8")
    errors = manifest.verify_unchanged(workspace)
    assert errors and "digest changed" in errors[0]


def test_manifest_rejects_escapes_and_missing_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for path in ("../outside.md", "/etc/passwd", "C:/Windows/win.ini", "~/.ssh/id_rsa"):
        with pytest.raises(VerificationProtocolError):
            canonical_artifact_path(path)
    with pytest.raises(VerificationProtocolError, match="missing"):
        ArtifactManifest.build(workspace, ["workforce/missing.md"])


def test_structured_verdict_requires_schema_and_pass_evidence() -> None:
    with pytest.raises(VerificationProtocolError, match="schema_version"):
        StructuredVerdict.parse('{"verdict":"PASS","summary":"x","evidence":[]}')
    with pytest.raises(VerificationProtocolError, match="evidence"):
        StructuredVerdict.parse(_verdict([]))
    with pytest.raises(VerificationProtocolError, match="JSON"):
        StructuredVerdict.parse("VERDICT: PASS")
    with pytest.raises(VerificationProtocolError, match="enumerate"):
        StructuredVerdict.parse(_verdict([
            {"artifact": "workforce/result.md", "observation": "Observed."},
        ], verdict="FAIL"))
    failure = {
        "schema_version": 2,
        "verdict": "FAIL",
        "summary": "The artifact is empty.",
        "evidence": [{"artifact": "workforce/result.md", "observation": "Empty."}],
        "failure_reasons": [{
            "kind": "artifact_empty",
            "description": "The artifact is empty.",
            "artifact": "workforce/result.md",
        }],
    }
    parsed_failure = StructuredVerdict.parse(json.dumps(failure))
    assert parsed_failure.failure_reasons[0].kind == "artifact_empty"
    assert parsed_failure.to_dict()["failure_reasons"][0]["artifact"] == "workforce/result.md"
    parsed = StructuredVerdict.parse(
        "```json\n" + _verdict([
            {"artifact": "workforce/result.md", "observation": "non-empty"},
        ]) + "\n```"
    )
    assert parsed.verdict == "PASS"
    assert parsed.evidence[0].artifact == "workforce/result.md"


def test_evidence_must_cite_every_manifest_artifact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "workforce").mkdir()
    (workspace / "workforce" / "analysis.md").write_text("a", encoding="utf-8")
    (workspace / "workforce" / "result.md").write_text("r", encoding="utf-8")
    manifest = ArtifactManifest.build(
        workspace, ["workforce/analysis.md", "workforce/result.md"]
    )
    only_one = StructuredVerdict.parse(_verdict([
        {"artifact": "workforce/analysis.md", "observation": "a"},
    ]))
    errors = validate_verdict_evidence(only_one, manifest)
    assert any("did not cite" in error for error in errors)
    complete = StructuredVerdict.parse(_verdict([
        {"artifact": "workforce/analysis.md", "observation": "a"},
        {"artifact": "workforce/result.md", "observation": "r"},
    ]))
    assert validate_verdict_evidence(complete, manifest) == ()


def test_evidence_sha_must_match_manifest(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "result.txt"
    target.write_text("content", encoding="utf-8")
    manifest = ArtifactManifest.build(workspace, ["result.txt"])
    parsed = StructuredVerdict.parse(_verdict([{
        "artifact": "result.txt",
        "observation": "content",
        "sha256": "0" * 64,
    }]))
    errors = validate_verdict_evidence(parsed, manifest)
    assert any("sha256" in error for error in errors)


def test_supporting_citations_must_be_real_workspace_files(tmp_path: Path) -> None:
    """Live finding: an honest verifier cites the inputs it recomputed from."""
    workspace = tmp_path / "workspace"
    (workspace / "report").mkdir(parents=True)
    (workspace / "orders").mkdir()
    (workspace / "report" / "summary.txt").write_text("grand total: 3", encoding="utf-8")
    (workspace / "orders" / "a.txt").write_text("total: 3", encoding="utf-8")
    (workspace / ".env").write_text("TOKEN=x", encoding="utf-8")
    manifest = ArtifactManifest.build(workspace, ["report/summary.txt"])
    honest = StructuredVerdict.parse(_verdict([
        {"artifact": "report/summary.txt", "observation": "grand total: 3"},
        {"artifact": "orders/a.txt", "observation": "total: 3"},
    ]))
    assert validate_verdict_evidence(honest, manifest, workspace) == ()
    # Without the workspace the strict manifest-only rule still applies.
    assert validate_verdict_evidence(honest, manifest)
    with pytest.raises(VerificationProtocolError):  # escapes are refused at parse time
        StructuredVerdict.parse(_verdict([{"artifact": "../outside.txt", "observation": "x"}]))
    for bad in ("orders/missing.txt", ".env"):
        fabricated = StructuredVerdict.parse(_verdict([
            {"artifact": "report/summary.txt", "observation": "grand total: 3"},
            {"artifact": bad, "observation": "x"},
        ]))
        assert validate_verdict_evidence(fabricated, manifest, workspace), bad
    # Supporting citations never satisfy manifest coverage.
    inputs_only = StructuredVerdict.parse(_verdict([
        {"artifact": "orders/a.txt", "observation": "total: 3"},
    ]))
    errors = validate_verdict_evidence(inputs_only, manifest, workspace)
    assert any("did not cite" in error for error in errors)
