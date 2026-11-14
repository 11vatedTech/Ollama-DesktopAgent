# Known risks and limitations

| ID | Risk | Impact | Mitigation in place | Resolution |
|---|---|---|---|---|
| R1 | (closed in session 3) Increment 1 had never run against a real model | - | Live qualification of three models and live scenarios A-F on the workstation; failures recorded and fixed | Closed |
| R2 | (closed in session 3) New code had only run on Linux | - | Full suite passes on Windows 11 / Python 3.12; live Ollama tests pass | Closed |
| R3 | (closed for Windows commands in session 4) No filesystem or network sandbox for executed code | Code can still do anything inside its own workspace, including reading secrets placed there | AppContainer + Job Object: workspace-only writes, runtime-folder reads, no network or loopback; fail-closed; grant ledger | POSIX hosts and MCP servers remain unsandboxed (R20) |
| R4 | (closed in session 3) Legacy paths bypassed the readiness gate | - | `run`, `recover`, chat agent mode, history resume and workforce children go through `gate.clear_for_execution` | Closed |
| R5 | (closed in session 3) No desktop UI for agents | - | Agent Foundry page | Closed |
| R6 | (closed in 0c2c935) Time budget was per process | - | - | Closed |
| R7 | Qualification uses 6 tasks x 2 trials per harness | Wide intervals; run-to-run variance observed live (qwen2.5-coder:14b guided 7/12 then 10/12) | Wilson lower bound; conservative statuses; all attempts and final messages stored | More tasks and trials under a new suite version; report variance |
| R8 | (closed in session 4) Skill evaluation used few trials | - | Paired sign test, p <= 0.1 required; trends reported as `inconclusive` | Closed |
| R9 | Scenario E timing depends on model latency | A very fast model could finish before the kill | Reports inconclusive rather than pass | Acceptable |
| R10 | (closed in session 4) `OLLAMA_WORKSTATION_OLLAMA_URL` could redirect model traffic | - | Honoured only for loopback hosts unless `allow_remote_ollama` | Closed |
| R11 | (closed in session 3) Recovery child used `python -m` | - | Frozen CLI runs the child via `11vated.exe --recovery-child` | Closed |
| R12 | (closed in session 4) Hosted CI not run on the campaign branch | - | CI green on Ubuntu and Windows; runs on `campaign/**` pushes | Closed |
| R13 | Admission footprint is an estimate | A load could be misjudged | Calibrated live: 10-12% conservative for 7B/14B at num_ctx 4096 on the RTX 5070 Ti | Calibrate long contexts and multi-model residency |
| R14 | (closed in session 4) Campaign work lived on a local branch | - | Pushed and merged as PR #4 | Closed |
| R15 | Real models follow instructions embedded in content | Measured on qwen2.5-coder:14b: wrote an injected file in 8/10 attempts without mitigation, 3/10 with the `content-is-data` skill | Gate reports the weakness; web agents auto-pin the skill; sandbox limits what injected commands can reach; verifier and host checks; web injection task in qualification | Stronger models; keep grants narrow; human review of consequential output |
| R16 | Model verifiers hallucinate | Live: 7B and 14B verifiers reported a present artifact as missing | Malformed or evidence-free FAIL verdicts are never overridden; host checks run independently; file-not-found errors now suggest same-named files | Stronger verifier models; host-side re-check of factual verifier claims |
| R17 | Release is unsigned and has no clean-VM test | SmartScreen warnings; undeclared runtime dependencies would go unnoticed on this machine | Clean build venv, SBOM, licence audit, frozen smoke tests, install/upgrade/tamper/uninstall cycle tested on the workstation | Code-signing certificate; test on a clean Windows VM (Windows Sandbox needs Pro/Enterprise) |
| R18 | (closed in session 5) No JavaScript browser or computer-use tools | - | Isolated browser and computer-use broker | Closed |
| R19 | DNS rebinding between the egress check and the connection | One request could reach a private address | Every redirect re-checked; private/loopback/metadata refused at check time | Pin the resolved address for the connection |
| R20 | MCP servers and POSIX commands are not sandboxed | A malicious MCP package or POSIX command runs with the user's rights | Explicit per-tool MCP grants; Job Object supervisor; MCP off by default | Run MCP servers in the AppContainer too |
| R21 | Research agents can be confidently wrong | Live: a reviewer that read nothing was trusted and its dependant invented figures | Grounding rule (a read-only task must observe the workspace); host checks on figures where possible | Encourage value-level checks in plans |
| R22 | Label-based risk classification | A destructive control with an innocuous label is not recognised | Confirmation for known risky labels; launched-apps-only scope; audit trail | Per-application action allow-lists |
| R23 | Local models are unreliable at desktop workflows | Live H 0/9 with qwen2.5-coder:14b; injected text makes it abandon tasks | Host-verified application state; policy refusals; read-only retries | Stronger local models; smaller single-environment tasks |
