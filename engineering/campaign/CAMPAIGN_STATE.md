# Campaign state

**Product:** 11VATED INTELLIGENCE - Ollama Desktop Agent
**Branch:** `main` (v2 fast-forwarded in session 6)
**Baseline commit:** `974517c0cf4f293192a289cb419f21654671c39b`
**Last updated:** 2026-11-16 (session 8)
**Overall status:** 12 of 15 gates VERIFIED, 2 PARTIAL, 1 OPEN. Every open item
needs an owner action or decision. See IMPLEMENTATION_MATRIX.md.

## Where the campaign stands

A model is profiled, qualified on verified tasks (including web tasks on an
offline test web), bound into a versioned agent, admitted against measured
RAM/VRAM through a scheduler that queues and unloads, run through its measured
harness with commands in an AppContainer and web access behind an egress
policy, independently verified, and recovered after a hard kill. Workforces
run read-only tasks in parallel. Skills are authored and accepted only on a
significant paired improvement.

## Session 4 (2026-11-13)

1. AppContainer sandbox for agent commands; live scenario A inside it.
2. Resource scheduler (reservations, automatic unload, queue) and parallel
   read-only workforce waves; live parallel runs.
3. Grounding rule after a live run produced invented figures.
4. Web research tools with an egress policy; web qualification tasks that keep
   existing qualification fingerprints valid.
5. Sign-test skill evaluation; live A/B on two skills; new built-in
   `content-is-data` (injection obedience 8/10 -> 3/10).
6. Agent Foundry Skills tab and web permission; rendered visual review and fixes.
7. Strict mypy clean and enforced in CI; release signing support; clean-machine
   CI install check; loopback-only URL override.

## Session 5 (2026-11-14)

Owner decision: build the browser and computer use now. Built the interaction
engine (policy broker with audit, isolated browser, UI Automation broker,
practice app), qualification tasks for both, acceptance scenarios G-J. B1-B5 and
C1-C5 pass automated tests on real Edge and UI Automation; live G and J passed,
I passed 1/3, H failed 0/9 because of model reliability. See VERIFICATION_EVIDENCE.

## Session 8 (2026-11-16)

Found the cause of the scenario H failures: Ollama's default 4096-token window
truncated long runs silently, dropping the task. Every request now names its
window (16 384, or 8 192 when 16k would spill), and compaction is sized to it.
H then passed 5/5 on rana and 3/3 on the stock qwen3.5:9b; an ablation back to
4096 failed 0/2. Added a host deliverable gate, an opt-in `edit_file` tool with
its own qualification task, and `docs/MODEL_CLASSIFICATION.md` (what makes an
Ollama model an agent, tiers, measured classification, selection checklist).
G9 is VERIFIED. See VERIFICATION_EVIDENCE "Session 8".

## Session 9 (2026-11-16)

Rendered every page and drove the desktop end to end with a live model. Found and
fixed nine defects, two of them serious: chat agent mode refused every overwrite
silently (it had no approval path), and desktop chat still sent no context window.
Added desktop approvals, chat window sizing, possible-claim detection for refused
changes, and `G11_OWNER_QA.md`. See VERIFICATION_EVIDENCE "Session 9".

## Next exact action

Owner: work through `engineering/campaign/G11_OWNER_QA.md` (about 20 minutes) and
choose a signing route from `docs/CODE_SIGNING_OPTIONS.md`. PR #5 (sessions 8-9) is
merged (`bc9f7ec`).

## Owner decisions and inputs still needed

1. G14: choose a signing route (`docs/CODE_SIGNING_OPTIONS.md`) and provide the certificate or account.
2. G11: interactive QA of the desktop, using `G11_OWNER_QA.md`.
3. G15: release certification once the above are done.
