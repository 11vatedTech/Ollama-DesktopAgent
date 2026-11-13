# Verification evidence

Only commands that were actually executed are listed, with their real results.

## Baseline (commit 974517c, before any change)

Environment: Linux x86_64, Python 3.13.18, pytest 9.1.1, ruff 0.16.9. PyPI was
unreachable, so the preinstalled `httpx2` (API-compatible) was aliased as
`httpx` outside the repository, and PySide6 could not be installed.

| Command | Result |
|---|---|
| `pytest -q` | 191 passed, 4 skipped (1 module: PySide6 missing; 3: `OLLAMA_LIVE` not set) |
| `ruff check .` | clean |
| `mypy src` | 177 errors in 31 files (not part of CI; mostly missing PySide6 and jsonschema stubs) |

Local checkout on the workstation: same commit 974517c, clean working tree.

## Increment 1 (commit 423c296451a5de0062f305a4daac43a363d64bf1)

| Command | Result |
|---|---|
| `pytest -q` (full suite) | 281 passed, 4 skipped, exit 0. All 191 pre-existing tests pass unmodified |
| `ruff check .` | clean |
| `python -m compileall -q src` | clean |
| `mypy src/ollama_workstation/agentification` | 1 error: missing third-party stubs for jsonschema (same class as baseline) |

New tests by file: verifier 22, agent service 24, skills 15, runtime policy 12,
harness qualification 9, acceptance scenarios 6, CLI lifecycle 2 (90 total).

Mutation spot-checks (change made, tests run, change reverted):

| Mutation | Detected by |
|---|---|
| `qualification_issues` ignores a changed tool manifest | test_record_is_bound_and_invalidated_by_any_dependency_change |
| Empty check list no longer yields needs_human_review | test_agent_claim_alone_is_never_certified, test_required_verification_without_checks_needs_human_review |

## Increment 2 (commit 0c2c935), session 2

Recovery check first: `validate_ledger.py` ok; full suite on the recovered tree
green before any change.

| Command | Result |
|---|---|
| `pytest -o addopts="" -q` (full suite) | 296 passed, 4 skipped, exit 0 |
| `ruff check .` | clean |
| `python -m compileall -q src` | clean |
| `mypy src/ollama_workstation/agentification/resources.py` | no issues |

Mutation spot-check: replacing `admission.require` with `admission.evaluate` in
`AgentService.run` is detected by
`test_agent_run_is_refused_before_any_model_request_when_resources_are_short`.

Not established: behaviour on the real RTX 5070 Ti / 32 GB host. The decision
logic was exercised with synthetic RAM/VRAM figures only.

### What the scripted-model tests establish

Run over real HTTP against `tests/support/scripted_ollama.py`:

- Five scripted models with different protocol abilities receive different
  harnesses (native, structured, guided, structured, none).
- A model that declares tool support but emits wrong arguments does not qualify
  for the native harness.
- A model that obeys an embedded instruction is capped at provisional.
- An unqualified, provisional-without-override, digest-changed,
  runtime-changed, uninstalled, disabled or retired agent cannot run.
- A run whose agent reports success but whose file is wrong is reported failed.
- A child process is killed with `kill()` after its first tool call; the same
  run id resumes, writes exactly once, keeps its earlier journal entries and
  passes hash-chain verification.
- A seeded defect is fixed and the host's own pytest run confirms it.

### What they do not establish

Anything about a real model: whether any installed model qualifies, which
harness it gets, whether it completes scenario A, C or E, or how long that takes.

## Live evidence

**None yet for this campaign.** To be filled from
`.artifacts\campaign\acceptance-*.json` after running
`scripts\live_acceptance.ps1` on the workstation. Record failures as failures.

Pre-campaign live evidence recorded by the repository owner (not re-verified
here): CHANGELOG "Unreleased" section; `.artifacts/` on the workstation
(Ollama 0.40.16, 15 models, qwen2.5-coder:7b profile and workforce runs).
