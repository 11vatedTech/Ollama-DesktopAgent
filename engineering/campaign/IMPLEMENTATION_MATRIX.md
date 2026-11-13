# Implementation matrix

Status vocabulary: **OPEN** (not done), **PARTIAL** (implemented in part, or a
required element is missing), **VERIFIED** (implemented with live evidence that
satisfies the gate on the reference workstation). Evidence references are to
VERIFICATION_EVIDENCE.md.

| Gate | Outcome required | Status | Implemented | Evidence | Missing before the gate can close |
|---|---|---|---|---|---|
| G1 | Repository integrity, consolidation | PARTIAL | Campaign committed on `campaign/complete-agent-v1`; ruff and compileall clean; Windows suite green | "Final suite" | Push; hosted CI (Ubuntu + Windows); merge PRs #1-#3 to `main`; mypy debt |
| G2 | Model intelligence, empirical qualification | VERIFIED | Profiler, benchmarks, routing; verified agent-task suite run live on three models, with stored final messages for every failed attempt | "Harness qualification on real models" | (Improvement, not a gate blocker: more tasks/trials, long-context and vision suites — R7) |
| G3 | Universal agentification, harness selection | VERIFIED | Measured harness selection; versioned digest-bound agents; one execution gate for every path; Agent Foundry UI | Live qualification; gate tests; scenarios A-E ran on gated agents | - |
| G4 | Functional autonomous execution | VERIFIED | Bounded loop, budgets, protocol repair (fenced-JSON fix), tool-error guidance | Live scenarios A, B, C | - |
| G5 | Persistent, secure memory | VERIFIED | Hybrid keyword + local-embedding recall, duplicate reinforcement, contradiction flags, poisoning defences, retention, UI | Live semantic recall through nomic-embed-text; 10 memory tests | (Improvement: memory not yet written automatically by agents) |
| G6 | Reusable skills, capability engineering | PARTIAL | Versioned skills with contracts, dependencies, with/without evaluation; pinning in the Foundry UI | 15 skill tests; scenario A used `defect-fix-loop` live | Live skill A/B evaluation on a real model; skill authoring UI |
| G7 | Workforce orchestration, resource scheduling | PARTIAL | Orchestrator with gated children; admission calibrated live (10-12% conservative) | Live scenario D pass; calibration table | Admission queue / automatic unload; parallel children |
| G8 | Security and isolation | PARTIAL | Job Object containment (kill-on-close, process/memory caps, UI limits, fail-closed); gate; git hardening; memory poisoning defences | Containment tests with negative controls; git hostile-config control | Filesystem/network sandbox (restricted token / AppContainer / VM) — R3 |
| G9 | Tool and integration ecosystem | PARTIAL | Workspace tools, MCP gateway, read-only git tools | git tool tests | Web research, browser and computer-use tools (deliberately withheld without an egress policy — R18) |
| G10 | Independent verification | VERIFIED | Host verification engine; workforce verifier contract with fabricated-citation rejection and supporting-evidence rule | Live scenario D (PASS) and the recorded failed verifier runs | - |
| G11 | Complete desktop experience | PARTIAL | Ten-page shell including Agent Foundry (qualification, agents, runs, memory); settings for override and embeddings | UI tests; frozen GUI smoke test renders all pages | Interactive QA pass by the owner; visual review of the new page |
| G12 | End-to-end recovery | VERIFIED | Checkpoints, leases, dead-owner reclamation, frozen-safe recovery child | Live scenario E | (Further drills: Ollama restart mid-run, model removal, DB error) |
| G13 | Real-world acceptance scenarios | VERIFIED | A-F implemented and run on real models | Acceptance run 20261113-110646 and D re-runs | - |
| G14 | Clean Windows packaging and install | PARTIAL | Clean-venv build, GUI + CLI, SBOM, licence audit, smoke tests, checksums, install/upgrade/uninstall scripts | Release build and install cycle on the workstation | Code signing; clean-VM install test (R17) |
| G15 | Final release certification | OPEN | - | - | G1, G6-G9, G11, G14 |

Summary: 7 of 15 gates VERIFIED (G2, G3, G4, G5, G10, G12, G13), 7 PARTIAL,
1 OPEN. The product is usable end to end on the reference workstation; it is
not release-certified.
