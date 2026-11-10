# Model, harness and agent benchmarking

## Benchmark layers

### 1. Model smoke profile

The initial profiler tests instruction following, JSON-schema output, one harmless native function call, structured agent actions, code syntax/AST structure, deterministic code tracing, a syllogism and arithmetic. One-shot results have low confidence and cannot certify broad skill.

### 2. Protocol qualification

- Native tools: valid schema, multi-tool, malformed args, parallel calls and continuation.
- Structured actions: schema validity, enum compliance, required args, action/final discrimination.
- ReAct/shell: command planning, observation, bounded retry and termination.
- Vision: image grounding, object/diagram interpretation and OCR-sensitive evaluation where appropriate.
- Context: recall at multiple context lengths, distractor resistance and compaction recovery.
- Research: source retrieval, attribution, contradiction handling and unsupported-claim rate.

### 3. Agent end-to-end benchmarks

| Domain | Primary benchmark | Secondary/internal |
|---|---|---|
| Repository engineering | SWE-bench Verified | Issue-fix regression tasks |
| Terminal workflows | Terminal-Bench | TUA-Bench and Windows scenarios |
| Tool selection | BFCL | Invalid args, multi-round and permission denial |
| Desktop use | OSWorld | Repeatable Windows VM tasks |
| Browser use | Browser Use benchmark | Seeded local websites |
| Broad LLM tasks | Inspect AI / LM Evaluation Harness | Role-specific tasks |

### 4. Reliability and operations

Measure task completion, verified completion, human interventions, unauthorized-action attempts, tool-call success, recovery, run variance, first-token latency, tokens/sec, peak VRAM/RAM, load latency and energy when instrumentation exists.

## 11Vated Agent Arena

Primary metric:

`Verified Autonomy Rate = independently verified completed tasks / tasks attempted`

Report confidence intervals and include timeouts and invalid runs in the denominator. Infrastructure exclusions must follow predeclared rules and remain visible.

Secondary metrics: tool argument validity, handoff success, rejected unauthorized actions, recovery success, human interventions, median/p95 completion time, repeat-run variance, context tokens per verified task, peak/integrated memory, evidence coverage.

## Controlled comparison

Keep exact model digest, quantization, context, task, environment, tools, timeout and scoring fixed across:

1. Raw chat.
2. Native Ollama tool harness.
3. JSON-schema action harness.
4. Shell/ReAct harness in a controlled sandbox.
5. Reference harness (Goose/OpenHands only after compatibility verification).
6. 11vated agentification harness.

Use at least three exploratory repeats and five or more for release gates; increase samples for high variance. Freeze task and scoring definitions before comparing. A model must not be its own sole judge.

## Report contract

Record UTC timestamp, source commit, Ollama version, model/digest/quantization, hardware/OS/driver, harness and prompt hashes, task IDs/version, raw outcomes, failure categories, scoring version, artifacts, medians/p95/variance/confidence intervals and deviations.

Never publish unverified marketing percentages. Review benchmark licenses and run generated code in disposable isolated environments without secrets or uncontrolled egress.
