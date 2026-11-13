# Campaign state

**Product:** 11VATED INTELLIGENCE - Ollama Desktop Agent
**Branch:** `campaign/complete-agent-v1` (created from `feat/execution-integrity-v1-1`)
**Baseline commit:** `974517c0cf4f293192a289cb419f21654671c39b`
**Last updated:** 2026-11-13 (session 3)
**Overall status:** IN PROGRESS. 7 of 15 gates VERIFIED, 7 PARTIAL, 1 OPEN. See IMPLEMENTATION_MATRIX.md.

## Where the campaign stands

The product works end to end on the reference workstation against real local
models: a model is profiled, qualified on verified tasks, bound into a versioned
agent, admitted against measured RAM/VRAM, run through the measured harness,
independently verified, and recovered after a hard kill. All six acceptance
scenarios have passed live (D after two fixes; the failed runs are recorded).

## Session 3 (2026-11-13) - first session on the workstation

1. Ran the suite on Windows (green) and live Ollama tests (green).
2. Qualified qwen2.5-coder:7b, qwen2.5-coder:14b and deepseek-r1:7b live.
3. Built and wired the shared execution gate into every legacy run path.
4. Added Windows Job Object containment for commands, with OS-level tests.
5. Added hybrid embedding memory with conflict and poisoning defences.
6. Added the Agent Foundry desktop page and the related settings.
7. Implemented acceptance scenarios B (offline research) and D (workforce).
8. Added read-only, hostile-config-safe git tools.
9. Fixed defects found only by live runs: fenced-JSON native tool calls,
   cp1252 console crashes, missing diagnostics in failed attempts, UI worker
   lifetime, verifier path guidance and supporting-evidence citations.
10. Built a release with SBOM and licence audit, and exercised install,
    upgrade, tamper refusal and uninstall on the workstation.

## Next exact action

Owner: push the branch and open a pull request so hosted CI runs on Ubuntu and
Windows:

    git push -u origin campaign/complete-agent-v1

Then review the Agent Foundry page interactively (G11).

## After that, in dependency order

1. Hosted CI green on the campaign branch; merge PRs #1-#3 and this branch (G1).
2. Restricted token / AppContainer for executed code, or a VM runner (G8, R3).
3. Admission queue with automatic unload; parallel workforce children (G7).
4. Live skill A/B evaluation and a skill authoring view (G6).
5. Code-signing certificate and a clean-VM install test (G14, R17).
6. Egress policy, then web research / browser tools (G9, R18).
7. Release certification (G15).
