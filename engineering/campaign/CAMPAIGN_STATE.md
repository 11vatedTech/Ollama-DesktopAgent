# Campaign state

**Product:** 11VATED INTELLIGENCE - Ollama Desktop Agent
**Branch:** `campaign/complete-agent-v1` (created from `feat/execution-integrity-v1-1`)
**Baseline commit:** `974517c0cf4f293192a289cb419f21654671c39b`
**Last updated:** 2026-11-13
**Overall status:** IN PROGRESS. 0 of 15 gates are certified. See IMPLEMENTATION_MATRIX.md.

## Where the campaign stands

Increment 1 (commit `423c296`) delivered the central missing subsystem: the path
from an installed model to a qualified, versioned, verified agent. It is
implemented and covered by 90 new automated tests that run over real HTTP and
real process boundaries against scripted models.

**It has not yet been run against a real Ollama model.** The engineering
environment for increment 1 had no Ollama, no GPU, no Windows and no PySide6.
Every claim about real-model behaviour is therefore unverified until the live
run below is executed on the workstation.

## Session 2 (2026-11-13)

Recovered from this ledger after an interruption (full suite re-run: green).
Increment 2 (commit `0c2c935`) added RAM/VRAM admission control in front of
every agent run, the `11vated resources` command, and a time budget that
survives resume (closes R6). 296 passed, 4 skipped; ruff clean.

Increments 1 and 2 were written into the workstation folder as working-tree
files. The local checkout there is still on `feat/execution-integrity-v1-1`;
the files are uncommitted on that machine. To adopt the campaign history
instead, fetch `campaign-complete-agent-v1.bundle` (see RECOVERY_PROTOCOL.md).

The live run could not be started remotely in this session (no shell on the
workstation; terminals cannot be typed into through desktop control).

## Next exact action

On the Windows workstation, from the repository root, with Ollama running:

    powershell -ExecutionPolicy Bypass -File scripts\live_acceptance.ps1 -Models qwen2.5-coder:7b,deepseek-r1:7b -AllowCodeExecution

Then record the resulting `.artifacts\campaign\acceptance-*.json` in
VERIFICATION_EVIDENCE.md under "Live evidence" and update gates G3, G4, G12 and
G13 in IMPLEMENTATION_MATRIX.md from the measured results, including failures.

If the suite step fails on Windows, fix that first: the new tests have only been
executed on Linux / Python 3.13 (see KNOWN_RISKS.md R2).

## After that, in dependency order

1. Agent Management and Skill Studio pages in the desktop UI, wired to
   `AgentService` (G11). Nothing in the UI reaches the new subsystem yet.
2. Route `11vated run`, chat agent mode and workforce child runs through
   `AgentService.run` so no execution path bypasses the readiness gate (G3, G7).
3. Windows Job Object containment for `run_command` and command checks (G8).
4. Embedding-backed hybrid memory with provenance and contradiction handling (G5).
5. Admission queue and unload-before-load using the admission decision; call
   admission from workforce child runs and chat (G7, G15).
6. Scenarios B (research) and D (workforce) in the acceptance harness (G13).
7. Installer, SBOM, licence audit, clean-VM install test (G14).
