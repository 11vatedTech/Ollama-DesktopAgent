# Known risks and limitations

| ID | Risk | Impact | Mitigation in place | Resolution |
|---|---|---|---|---|
| R1 | (closed in session 3) Increment 1 had never run against a real model | - | Live qualification of three models and live scenarios A-F on the workstation; failures recorded and fixed | Closed |
| R2 | (closed in session 3) New code had only run on Linux | - | Full suite passes on Windows 11 / Python 3.12; live Ollama tests pass | Closed |
| R3 | No filesystem or network sandbox for executed code | Model-written code runs with the user's token: it can read/write the user's files and open connections | Windows Job Object per command (suspended start, kill-on-close, 32 processes, 2 GiB, UI restrictions; fail-closed); shell off by default; allow-list; approval; `--allow-code-execution` consent | Restricted token + AppContainer (or a disposable VM) |
| R4 | (closed in session 3) Legacy paths bypassed the readiness gate | - | `run`, `recover`, chat agent mode, history resume and workforce children go through `gate.clear_for_execution` | Closed |
| R5 | (closed in session 3) No desktop UI for agents | - | Agent Foundry page | Closed |
| R6 | (closed in 0c2c935) Time budget was per process | - | - | Closed |
| R7 | Qualification uses 6 tasks x 2 trials per harness | Wide intervals; run-to-run variance observed live (qwen2.5-coder:14b guided 7/12 then 10/12) | Wilson lower bound; conservative statuses; all attempts and final messages stored | More tasks and trials under a new suite version; report variance |
| R8 | Skill evaluation uses few trials by default | `improved` can be noise | Identical seeds in both arms | Raise `--trials`; add a significance rule |
| R9 | Scenario E timing depends on model latency | A very fast model could finish before the kill | Reports inconclusive rather than pass | Acceptable |
| R10 | `OLLAMA_WORKSTATION_OLLAMA_URL` redirects all model traffic | A hostile environment variable could point the client at a remote host | Documented; local-first default | Restrict to loopback unless a setting allows remote |
| R11 | (closed in session 3) Recovery child used `python -m` | - | Frozen CLI runs the child via `11vated.exe --recovery-child` | Closed |
| R12 | Hosted CI not run on the campaign branch | Ubuntu regressions undetected (Windows is verified locally) | Local Windows suite | Push the branch and check the matrix |
| R13 | Admission footprint is an estimate | A load could be misjudged | Calibrated live: 10-12% conservative for 7B/14B at num_ctx 4096 on the RTX 5070 Ti | Calibrate long contexts and multi-model residency |
| R14 | Campaign work lives on a local branch only | Not on GitHub until pushed | Committed locally on `campaign/complete-agent-v1` | Owner pushes the branch |
| R15 | Real models fail the safety categories | No installed model is fully qualified; agents run on provisional harnesses that obey embedded instructions some of the time | Gate surfaces "measured unsuitable for: injection_resistance" on every run; verifier and host checks catch unauthorized changes | Prefer stronger models; keep write/shell grants narrow; human review of consequential output |
| R16 | Model verifiers hallucinate | Live: 7B and 14B verifiers reported a present artifact as missing | Malformed or evidence-free FAIL verdicts are never overridden; host checks run independently; file-not-found errors now suggest same-named files | Stronger verifier models; host-side re-check of factual verifier claims |
| R17 | Release is unsigned and has no clean-VM test | SmartScreen warnings; undeclared runtime dependencies would go unnoticed on this machine | Clean build venv, SBOM, licence audit, frozen smoke tests, install/upgrade/tamper/uninstall cycle tested on the workstation | Code-signing certificate; test on a clean Windows VM (Windows Sandbox needs Pro/Enterprise) |
| R18 | No web research, browser or computer-use tools | Scenario B is offline corpus research only | Deliberate: agents have no network egress | Add only with an egress policy and per-domain approval |
