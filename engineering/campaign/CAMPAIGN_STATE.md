# Campaign state

**Product:** 11VATED INTELLIGENCE - Ollama Desktop Agent
**Branch:** `campaign/complete-agent-v2` (from `main` after PR #4)
**Baseline commit:** `974517c0cf4f293192a289cb419f21654671c39b`
**Last updated:** 2026-11-13 (session 4)
**Overall status:** 10 of 15 gates VERIFIED, 4 PARTIAL, 1 OPEN. Every open item
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

## Next exact action

Owner: open and merge a PR for `campaign/complete-agent-v2`, close PRs #2/#3
(fully contained in `main`), and try the Agent Foundry page.

## Owner decisions and inputs still needed

1. G9: whether to build a JavaScript browser and computer-use tools now. The
   measured injection behaviour (R15) argues for waiting for stronger models.
2. G14: a code-signing certificate (thumbprint or PFX) for signed releases.
3. G11: interactive QA of the desktop.
4. G15: release certification once the above are done.
