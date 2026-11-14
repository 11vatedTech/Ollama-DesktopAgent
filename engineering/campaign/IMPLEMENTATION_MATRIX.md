# Implementation matrix

Status vocabulary: **OPEN** (not done), **PARTIAL** (implemented in part, or a
required element is missing), **VERIFIED** (implemented with live evidence that
satisfies the gate on the reference workstation). Evidence references are to
VERIFICATION_EVIDENCE.md.

| Gate | Outcome required | Status | Implemented | Evidence | Missing before the gate can close |
|---|---|---|---|---|---|
| G1 | Repository integrity, consolidation | PARTIAL | PR #4 merged; #2/#3 contained in main; strict mypy clean and enforced in CI; hosted CI green | "Session 4" | Merge `campaign/complete-agent-v2`; close #2/#3 (owner) |
| G2 | Model intelligence, empirical qualification | VERIFIED | Profiler, benchmarks, routing; verified agent-task suite run live on three models, with stored final messages for every failed attempt | "Harness qualification on real models" | (Improvement, not a gate blocker: more tasks/trials, long-context and vision suites — R7) |
| G3 | Universal agentification, harness selection | VERIFIED | Measured harness selection; versioned digest-bound agents; one execution gate for every path; Agent Foundry UI | Live qualification; gate tests; scenarios A-E ran on gated agents | - |
| G4 | Functional autonomous execution | VERIFIED | Bounded loop, budgets, protocol repair (fenced-JSON fix), tool-error guidance | Live scenarios A, B, C | - |
| G5 | Persistent, secure memory | VERIFIED | Hybrid keyword + local-embedding recall, duplicate reinforcement, contradiction flags, poisoning defences, retention, UI | Live semantic recall through nomic-embed-text; 10 memory tests | (Improvement: memory not yet written automatically by agents) |
| G6 | Reusable skills, capability engineering | VERIFIED | Versioned skills, paired evaluation with sign test, authoring/evaluation UI, built-in `content-is-data` | "Session 4: skills" | - |
| G7 | Workforce orchestration, resource scheduling | VERIFIED | Resource scheduler (reservations, auto-unload, queue); parallel read-only waves; grounding rule | "Session 4: parallel workforce" | - |
| G8 | Security and isolation | VERIFIED | AppContainer + Job Object for agent commands; egress-policed web tools; loopback-only URL override | "Session 4: sandbox" | (Residual: MCP servers and POSIX not sandboxed) |
| G9 | Tool and integration ecosystem | PARTIAL | Workspace, git, MCP, web research, isolated browser (Edge/Playwright, egress-fetched), computer use (UI Automation, launched apps only), one policy broker with audit | "Session 5" | Real-model acceptance H (0/9 with qwen2.5-coder:14b) and I (1/3) need a more reliable model |
| G10 | Independent verification | VERIFIED | Host verification engine; workforce verifier contract with fabricated-citation rejection and supporting-evidence rule | Live scenario D (PASS) and the recorded failed verifier runs | - |
| G11 | Complete desktop experience | PARTIAL | Ten pages; Foundry with Skills tab and web permission; rendered visual review with fixes | "Session 4: visual review" | Owner interactive QA |
| G12 | End-to-end recovery | VERIFIED | Checkpoints, leases, dead-owner reclamation, frozen-safe recovery child | Live scenario E | (Further drills: Ollama restart mid-run, model removal, DB error) |
| G13 | Real-world acceptance scenarios | VERIFIED | A-F implemented and run on real models | Acceptance run 20261113-110646 and D re-runs | - |
| G14 | Clean Windows packaging and install | PARTIAL | Signing support, release manifest, clean-machine CI install (no Python on PATH) | "Session 4: release" | Owner code-signing certificate |
| G15 | Final release certification | OPEN | - | - | G1 merge, G9 model reliability, G11 owner QA, G14 certificate |

Summary: 10 of 15 gates VERIFIED (G2-G8, G10, G12, G13), 4 PARTIAL (G1, G9, G11, G14), 1 OPEN (G15). Every remaining item needs an owner action or decision; none is blocked on engineering work that can proceed without one.
