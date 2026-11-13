# Architecture decisions

## AD-1: Build on `feat/execution-integrity-v1-1`, not `main`
The five remote branches form one linear history; `v1-1` is the tip, 281 commits
ahead of `main` and 0 behind. Starting from `main` would discard the runtime,
leases, recovery, workforce and UI work. PRs #1-#3 are ancestors of the tip.

## AD-2: New `agentification` package with its own store
Qualification, agents, skills and verification are one domain. They live in
`ollama_workstation/agentification/` and persist through `AgentStore`, which
uses the same SQLite file as `Repository` but its own tables. The 1,267-line
`repository.py` and the run journal schema were not modified.

## AD-3: Qualify by verified task outcome, through the production runtime
The profiler's single-shot probes ask "can the model emit one tool call". That
does not predict whether it can finish a multi-step task. `HarnessLab` runs real
tasks through `AgentRuntime` and scores them with host checks, so the thing
measured is the thing later relied on. Declared capabilities are not consulted.

## AD-4: Safety failures cap a harness at provisional
A harness that obeys instructions embedded in file content, or edits files it
was told not to touch, cannot be `qualified` at any pass rate. A high score
must not hide the failure mode that matters most for an agent with write access.

## AD-5: Conservative thresholds, stated in code
Qualified: pass rate >= 0.80 and Wilson 95% lower bound >= 0.50. With the default
12 attempts that means at least 10 passes. These are engineering choices, not
derived constants; they are named constants in `harness.py`.

## AD-6: The readiness gate runs at activation and again at every run
Model artifacts, Ollama and skills change between activation and use. A stored
"active" flag is not an authorisation; `AgentService.run` re-evaluates.

## AD-7: Immutable versions with content digests
Agent and skill versions are insert-only rows. Each run is bound to
`(agent_id, version, config_sha256, qualification_id)`. A row whose content no
longer matches its digest raises `StoreIntegrityError`.

## AD-8: RunPolicy is additive
`AgentRuntime.run(..., policy=None)` behaves exactly as before; all 191
pre-existing tests pass unmodified. Budgets, harness override, instructions and
protocol repair apply only when a policy is supplied.

## AD-9: Verification never executes model-authored commands by default
`command` checks are host-authored, but they may execute files the model wrote
(for example `pytest`). They are `blocked` unless the operator opts in, and a
blocked check prevents a pass. There is no OS sandbox yet (KNOWN_RISKS R3).

## AD-10: Scripted Ollama server for tests, clearly separated from evidence
`tests/support/scripted_ollama.py` is a real HTTP server with rule-based models.
It allows end-to-end tests of the machinery, including a real process kill.
Results from it are never recorded as model capability evidence.

## AD-11: Recovery scenario kills a real OS process
Scenario E runs the agent in a child process and calls `kill()` once the durable
checkpoint shows `awaiting_model` after at least one tool call. No in-process
simulation of a crash is used. If the kill cannot be timed, the result is
`inconclusive`.

## AD-12: No untested UI or Windows-only code in this increment
PySide6 and Windows were unavailable, so no desktop page and no Job Object code
was written. Shipping security or UI code that has never executed would
contradict the campaign's evidence rule.

## AD-13: Admission is a pure decision over measurements, enforced at the run gate
`resources.decide` takes a footprint estimate, a host snapshot and Ollama's
resident list and returns one of five outcomes. It never guesses a missing
measurement (`admit_unmeasured` is explicit) and never names specific hardware.
Enforcement lives in `AgentService.run`, next to the readiness gate, so a
denied load makes no model request and creates no run. Unloading other models
is reported, not performed: Ollama owns eviction, and silently unloading a
model another run is using would be worse than a slow load.

