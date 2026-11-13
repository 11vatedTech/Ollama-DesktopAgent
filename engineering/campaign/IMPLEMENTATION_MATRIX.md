# Implementation matrix

Status vocabulary: **OPEN** (not done), **PARTIAL** (implemented in part or not
yet verified live), **VERIFIED** (implemented with evidence that satisfies the
gate). No gate is VERIFIED yet.

| Gate | Outcome required | Status | Implemented | Evidence | Missing before the gate can close |
|---|---|---|---|---|---|
| G1 | Repository integrity, consolidation | PARTIAL | Baseline mapped; campaign branch on the integration tip; ruff clean | VERIFICATION_EVIDENCE "Baseline" | PRs #1-#3 not merged to `main`; 177 pre-existing mypy errors (mostly missing PySide6/jsonschema stubs); hosted CI not run on this branch |
| G2 | Model intelligence, empirical qualification | PARTIAL | Pre-existing profiler, benchmarks, routing, model-card research; new verified agent-task suite | tests/test_harness_qualification.py | No long-context, research, vision or memory suites; no live run of the new suite |
| G3 | Universal agentification, harness selection | PARTIAL | `harness.py`, `agents.py`, `store.py`; CLI `qualify`, `agent` | 33 tests (harness + agent service) on scripted models | Live qualification of real models; legacy `run`/chat/workforce paths still bypass the gate; no UI |
| G4 | Functional autonomous execution | PARTIAL | Pre-existing bounded loop, checkpoints, leases; new `RunPolicy` budgets (time budget persists across resume), usage accounting, protocol repair | tests/test_runtime_policy.py; scenario C on scripted models | Live scenario C; explicit plan/task-state structure |
| G5 | Persistent, secure memory | OPEN | Pre-existing keyword memory with scopes and redaction (unchanged) | pre-existing tests | Embeddings, hybrid retrieval, contradiction detection, consolidation, retention, poisoning defences |
| G6 | Reusable skills, capability engineering | PARTIAL | `skills.py`: contracts, versions, dependencies, 4 built-ins, with/without evaluation; CLI `skill` | tests/test_skill_system.py (15) | Live evaluation on real models; skill authoring UI; regression scheduling |
| G7 | Workforce orchestration, resource scheduling | PARTIAL | Pre-existing orchestrator and selector (unchanged); new `resources.py` admission control (footprint estimate from model geometry; admit / after-unload / degraded / unmeasured / deny) enforced in `AgentService.run`; CLI `resources` | tests/test_resource_admission.py (14) on synthetic measurements | Never run on the real GPU; estimate not calibrated against Ollama's actual allocation; no queue, no automatic unload; workforce children and chat do not call admission; no parallel execution |
| G8 | Security and isolation | OPEN | Pre-existing path containment, allow-list, redaction; new host permission layer (`GrantedTools`), consent gates for code execution | permission and consent tests | **No OS-enforced isolation.** No Job Objects, restricted tokens or AppContainer; no egress control. Path checks and allow-lists are not a sandbox |
| G9 | Tool and integration ecosystem | OPEN | Pre-existing workspace tools and MCP gateway (unchanged) | pre-existing tests | Git, code-analysis, browser and computer-use tools; MCP tools are not part of agent definitions |
| G10 | Independent verification | PARTIAL | `verifier.py`: 13 check kinds, 5 outcomes, typed failure reasons, artifact digests; pre-existing workforce verifier | tests/test_verifier_engine.py (22) | Reviewer-agent evidence is not yet attached to engine reports; build verification is only via `command` checks |
| G11 | Complete desktop experience | OPEN | Pre-existing nine-workspace PySide6 shell (unchanged) | UI tests could not run here (PySide6 unavailable) | Agent Management, Skill Studio, Memory, Security pages; qualification in Model Intelligence; VRAM telemetry review |
| G12 | End-to-end recovery | PARTIAL | Pre-existing checkpoints, leases, dead-owner reclamation; scenario E kills and resumes a real process | tests/test_acceptance_scenarios.py::test_scenario_e_* | Live scenario E; Ollama-disconnect, model-removal, database-error and verifier-crash drills |
| G13 | Real-world acceptance scenarios | PARTIAL | A, C, E, F implemented in `acceptance.py`; CLI `accept` | 6 scenario tests on scripted models | **All four must be run on real models.** B (research) and D (workforce) are not implemented in the harness |
| G14 | Clean Windows packaging and install | OPEN | Pre-existing PyInstaller workflow (unchanged) | none from this campaign | Installer, upgrade/uninstall, SBOM, licence audit, clean-VM test |
| G15 | Final release certification | OPEN | - | - | Everything above |

## Directive requirements deliberately not attempted in increments 1-2

Desktop pages, OS sandboxing, memory intelligence, scheduling queue, browser
and computer-use tools, scenarios B and D, installer and release artefacts.
None of these is claimed anywhere as done.
