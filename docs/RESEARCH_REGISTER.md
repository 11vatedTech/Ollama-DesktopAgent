# External Research Register

**Review date:** 2026-11-10  
**Scope:** Local Ollama agent workstations, tool interoperability, agent composition and empirical model evaluation.  
**Policy:** Primary documentation and benchmark owners take precedence. A benchmark leaderboard is a measurement source, not a certification authority. Research references do not automatically become runtime dependencies.

## 1. Product and competitor evidence

### Ollama — inference provider contract

**Primary source:** [Ollama API documentation](https://github.com/ollama/ollama/blob/main/docs/api.md)

The documented chat contract supports tool definitions and tool calls, and structured outputs can use JSON Schema. Completed responses report prompt/evaluation token counts and durations, model load duration and total duration; generation throughput can be calculated from evaluation tokens divided by evaluation duration.

**Architectural consequences**
- Keep Ollama behind a provider adapter. Store the server version, model tag and exact digest with every profile and evaluation run.
- Treat tool calling and constrained JSON as separate model capabilities. Test the exact installed model rather than assuming that a family or model tag supports a protocol.
- Preserve raw numeric timing/counter measurements and the source endpoint; derive rates from the raw counts and nanoseconds.
- Keep streaming and non-streaming behavior explicit. A final streamed chunk carries usage values that may not exist in intermediate chunks.
- Local inference does not mean all features are offline: publisher research, MCP servers, browser use and update checks need separate network policy.

### Goose — direct local-agent competitor

**Primary sources:** [Provider configuration](https://goose-docs.ai/docs/getting-started/providers/), [extensions](https://goose-docs.ai/docs/getting-started/using-extensions/), [subagents](https://goose-docs.ai/docs/guides/subagents/)

Goose documents Ollama as a local provider alongside other local model runtimes. Its extension model uses MCP and includes built-in developer, computer-control, memory, skills and delegation capabilities. Its subagent guidance allows limits and tool-extension scopes to be configured. Goose also warns that models without tool calling are limited to chat completion with extensions disabled.

**Architectural consequences**
- Local inference, model selection, MCP and basic subagents are table stakes, not the product moat.
- 11vated should differentiate through measured model-to-harness matching, per-digest identity dossiers, repeatable local qualification, hardware-aware scheduling and end-to-end evidence.
- Tool/extension access for a child agent must be explicitly reduced; do not inherit a parent's full permissions by default.
- A model that fails native tool tests should be assessed against structured-action and guided harnesses rather than being silently labelled unusable.

### Model Context Protocol — interoperability contract

**Primary sources:** [MCP specification](https://modelcontextprotocol.io/specification/2026-07-28), [authorization security considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)

The current MCP specification describes version negotiation and JSON-RPC messaging, server tools/resources/prompts, client elicitation/sampling/roots, and HTTP authorization. Its security considerations require implementers to follow OAuth 2.1 security practices for HTTP-based authorization, validate token audience, and prohibit token passthrough.

**Architectural consequences**
- An MCP client/server must be an adapter behind the policy gateway, not a bypass around the host permission system.
- Bind each configured server to its transport, endpoint, identity, allowed capabilities and approval policy.
- Keep server credentials outside model-visible prompts and tool results; tokens must be audience-bound and scoped.
- MCP authorization does not sandbox a local process. Local stdio servers need OS/process isolation and an explicit executable trust decision.
- Negotiate protocol version/capabilities and validate every JSON-RPC message against the negotiated contract.

## 2. Evaluation evidence and benchmark policy

### Function calling — BFCL V4

**Primary sources:** [Berkeley Function Calling Leaderboard](https://gorilla.cs.berkeley.edu/leaderboard), [Epoch AI benchmark review](https://epoch.ai/benchmarks/berkeley-function-calling-leaderboard/review)

BFCL V4 covers function selection and arguments across single-call, multi-turn and agentic tasks. Epoch AI's September 10, 2026 review sampled 50 tasks and reported defects affecting 24 (48%) in that sample, grading the benchmark flawed.

**Policy**
- Use BFCL as one diagnostic reference, not as a single source of truth or a product certification.
- Pin the evaluator commit and dataset version, record exact category weights and preserve raw call traces.
- Audit task validity and scoring before adopting a public score. Cross-check against an independently maintained 11vated tool-call suite with deterministic host-side oracles.
- Separate native function-call support from text/prompt workarounds in all reports.

### Repository engineering — SWE-bench

**Primary sources:** [SWE-bench Verified evaluation concerns](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/), [SWE-bench Pro V2 protocol](https://labs.scale.com/leaderboard/swe_bench_pro_public_v2), [Epoch AI review of SWE-bench Pro](https://epoch.ai/benchmarks/swe-bench-pro/review)

OpenAI's 2026 analysis argues SWE-bench Verified is contaminated by public training exposure and should not be used as a reliable proxy for frontier coding ability. The original SWE-bench Pro benchmark also received quality criticism: Epoch AI's September 1, 2026 review flags substantial task defects. Scale's September 22, 2026 SWE-bench Pro V2 release describes a refreshed 642-task public split, corrections to contradictory requirements in 69 tasks, disabled web tools during the agent phase, pristine-image regrading, and a two-sided task validity gate.

**Policy**
- Do not use SWE-bench Verified as the sole or headline measure of coding ability.
- Do not mix SWE-bench Pro v1 and v2 results. For a Pro run, require the exact V2 protocol, split, image, grader and tool-access restrictions.
- Preserve both issue-resolution and regression results, including invalid/infra-failed exclusions.
- Compare multiple task sources, including private/internal repository tasks and held-out tasks where legally available.
- Publish harness, model digest, quantization, context budget, tools, retry limits, timeouts, cost/compute and grader versions with every result.

### Computer use — OSWorld

**Primary source:** [OSWorld project and benchmark](https://os-world.github.io/)

OSWorld is an execution-based benchmark for multimodal agents operating real computer environments across operating systems and applications. The project site now points users to OSWorld 2.0 as the latest version; use the current versioned materials rather than silently reusing an older OSWorld/OSWorld-Verified protocol.

**Policy**
- Do not report computer-use competence from screenshots or visual QA alone. Score state changes and task completion inside a controlled desktop environment.
- Record OS image, applications, initial state, UI scale, accessibility settings, interaction API and task protocol.
- Keep desktop-control permissions distinct from file, shell, network and credential permissions.
- Pin the exact OSWorld 2.0 release/checkpoint before publishing results.

## 3. 11vated evaluation standard

Public benchmarks are useful external reference points but do not cover the complete product contract. The workstation therefore needs a versioned **11Vated Agent Arena** with deterministic tasks and independent oracles.

### Evaluation tiers

| Tier | Test family | Required output |
|---|---|---|
| T0 | Provider and model identity | Exact installed model tag/digest, Ollama version, endpoint and hardware snapshot |
| T1 | Core model capabilities | Task-level result, prompt, output, scorer, repeated trials and confidence |
| T2 | Harness protocol | Native tool, structured action and guided action results independently |
| T3 | Tool correctness | Tool selection, argument validity, policy denial, idempotency and observation integrity |
| T4 | Workspace engineering | Patch applied in the assigned workspace; tests run; diff and artifact hashes |
| T5 | Recovery | Controlled timeout, malformed tool response, transient provider error, cancellation and restart |
| T6 | Memory | Scope isolation, relevance, stale-note handling, explicit write/delete and injection resistance |
| T7 | Safety | Path escape, symlink escape, unexpected property, prompt injection and denied capability attempts |
| T8 | Operations | Cold/warm latency, tokens/sec, prompt throughput, RAM/VRAM peak, resource limits and repeated-run variance |
| T9 | Multi-agent | Least-privilege delegation, child budgets, evidence handoff, schedule/retry and independent result verification |

### Primary metric

`Verified Autonomy Rate = independently verified completed tasks / tasks attempted`

This metric must always be reported with task count, task difficulty/source, confidence interval, human interventions, denied/unauthorized attempts, timeout rate, regression rate and compute/resource use. A task that returns fluent prose without a verified state change is not a completed task.

### Experiment reproducibility manifest

Every benchmark run must record:
- Model name, exact Ollama digest, quantization, parameter size and context configuration.
- Ollama version, OS build, CPU, GPU, driver, RAM/VRAM and power/performance mode where measurable.
- Harness identifier and version, system prompt hash, tool manifest hash, permission-policy version and memory policy.
- Dataset/task IDs and hashes, split, scorer/validator version and exclusions.
- Trial count, random seeds, timeouts, step/retry/token budgets and concurrency.
- Full sanitized event trace, tool I/O, patch/diff, validation output and result artifact hashes.

No result is production certification unless the run is reproducible, independently scored and passed on the declared supported hardware/software matrix.
