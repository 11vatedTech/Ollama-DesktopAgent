# Which Ollama models can be agents

This page answers two questions with measurements from this repository, not from
model cards: **what makes a local model act as an agent rather than describe one**,
and **which Ollama models to choose** for the 11vated desktop agent.

Every number below comes from `11vated qualify` (verified tool tasks, host-checked,
seeded per trial) or `11vated accept` (end-to-end scenarios) on the reference
workstation: RTX 5070 Ti Laptop (12 GB VRAM), Windows 11, Ollama 0.40.16. Evidence
records are in `engineering/campaign/VERIFICATION_EVIDENCE.md`.

## 1. An agent is a model inside a loop

A model never "becomes" an agent by itself. The host runs a loop: it shows the model
a task and a tool catalogue, the model emits an **action**, the host executes it under
policy, returns the **observation**, and repeats until the model gives a final answer
that the host then **verifies**. The model only has to do two things well:

1. **Act, do not narrate.** Emit a machine-readable action (a native tool call, or a
   schema-valid JSON action) instead of prose such as "I would call read_file".
2. **Hold the goal across many steps.** Keep doing the next useful action until every
   part of the task is done, then stop.

Everything else (permissions, sandboxing, recovery, verification, memory) is the
host's job, and this platform does it the same way for every model.

## 2. Why some models only describe the tools

Measured causes, in order of how often they decide the outcome:

| Cause | How it shows | Measured example | What the platform does |
|---|---|---|---|
| No tool template in the Ollama build | `ollama show` lists no `tools` capability; native tool calls are impossible | `deepseek-coder-v2:16b`: native harness *unsupported*, 0/16 and 1/16 on the fallbacks | Falls back to schema-constrained JSON actions (`structured_actions`) and the guided text protocol; if no harness reaches 50 %, the gate refuses the model |
| Reasoning-only distillation | Spends the output on `thinking`, returns no action or an empty answer | `deepseek-r1:7b`: unqualified on all harnesses (1/12, 0/12, 2/12) | Retries a step without reasoning when only thinking came back; still cannot create action skill the model lacks |
| Chat tuning without agent training | Narrates a plan, half-writes JSON, abandons multi-step tasks | `qwen2.5-coder:14b`: native 2-6/16, best harness provisional 12/16; scenario H 0/9 | Protocol repair (one corrected response), fenced-JSON tolerance, undecodable actions never accepted as answers |
| **The host silently truncating the conversation** | The model "forgets" the task half-way and stops without the last step | Ollama's default 4096-token window dropped whole messages with no error (probe: a 6.7k-token message evaluated as 42 tokens) | **Fixed in session 8:** every request names its window (16 384 by default, sized to fit the GPU) and compaction keeps the system prompt and task |

The last row matters most. It looked like a model weakness for three sessions: every
model failed the long desktop scenario H by stopping before its final file write. With
the window fixed, the same model passed H five times in five runs without the
completion gate ever having to intervene, and an ablation that restored the old
window failed both runs (section 5). The stock `qwen3.5:9b` passed H 3/3 on its
first attempts.

**Bottom line.** On a 12 GB GPU the platform's true potential is reached today with
the Qwen3.5 9B family: qualified on every harness and catalogue, passing the long
desktop, cross-environment workforce and injection scenarios. What decides whether a
model can be an agent is, in order: a tool template in its Ollama build, training for
tool use (2025+ instruction models), not being a reasoning-only distill, and fitting
the GPU with a 16k window. Size alone does not: the 14-22B coder models are weaker
agents than the 9B Qwen3.5.

## 3. Agent tiers

Tiers are assigned from evidence, never from a model's name or size.

| Tier | Name | Criterion (all must hold) | What the platform allows |
|---|---|---|---|
| 0 | Conversational | No harness reaches 50 % on the qualification suite | Chat only; the execution gate refuses agent runs |
| 1 | Assisted | Best harness 50-79 %, or a safety category below threshold (`provisional`) | Supervised, short workspace tasks; explicit `allow_provisional` |
| 2 | Operator | Best harness at least 80 % with every safety category passing (`qualified`) | Unattended workspace, coding, web and workforce tasks on its qualified catalogue |
| 3 | Autonomous | Tier 2 on the browser/computer catalogue **and** the long-horizon scenarios (H desktop workflow, I cross-environment workforce, J injection retest) pass on repeated runs | Multi-application workflows, workforce operator and verifier roles |

## 4. Classification of the installed models

Declared capabilities are from `ollama show`; harness scores are
passed/attempted across native / structured / guided; "computer" means the
browser+computer catalogue.

| Model | Declares tools | Fits 16k window on 12 GB | Best measured result | Tier |
|---|---|---|---|---|
| `abliterant/qwen3.5-rana:latest` (9B, Qwen3.5) | yes | yes | qualified: computer catalogue 14-15/16, browser+computer 18/18 on every harness; H 5/5 and J 6/6 hostile runs (session 8); I 2/3 since the session 7 fixes (the failure: the writer wrote the literal word 'payee', the model verifier missed it, the host checks caught it); edit catalogue 14/14 on every harness | **3 Autonomous** |
| `qwen3.5:9b` (stock) | yes | yes | qualified: edit catalogue 14/14 on every harness, computer catalogue 16/16 native, 16/16 structured, 15/16 guided; native tool calling selected; H 3/3, J 6/6 hostile runs with 0 prohibited actions, I 1/1 | **3 Autonomous, provisional** (I has run once) |
| `qwen2.5-coder:14b` | yes | CPU spill at 16k | provisional (best 12/16 structured); H 0/9 (measured before the window fix) | 1 Assisted |
| `qwen2.5-coder:7b` | yes | yes | provisional (best 10/12 structured) | 1 Assisted |
| `qwen3-vl:8b` | yes | yes | provisional (11/16 structured); slow and unsuitable for computer use | 1 Assisted |
| `deepseek-r1:7b` | yes | yes | unqualified (best 2/12) | 0 Conversational |
| `deepseek-coder-v2:16b` | **no** | CPU spill | unqualified; native unsupported | 0 Conversational |
| `codestral:22b` | **no** | CPU spill | not qualified (no tool template, CPU-bound here) | 0 expected |
| `gemma2:9b` | **no** | yes | not qualified (no tool template) | 0 expected |
| `qwen2.5:32b` | yes | CPU spill | not qualified (21 GiB; CPU-bound here) | unmeasured |
| `gemma4:26b` | yes | **denied** (about 22 GiB at 8k) | cannot load on this host | unmeasured here |
| `gspl-*` (custom 7-15B) | yes | yes/spill | not qualified | unmeasured |
| `moondream`, `qwen3-vl:4b`, `nomic-embed-text` | no / vision / embedding | - | not agent models | n/a |

Results measured before session 8 ran under the silent 4096-token truncation; the
long-horizon numbers for `qwen2.5-coder:14b` in particular may understate it.

## 5. What closed the gap (session 8)

| Change | Effect, measured |
|---|---|
| Explicit context window on every request (16 384 default, 8 192 when 16k would spill to CPU), compaction sized to it | Scenario H on rana: 0/4 before, 5/5 after; 0 completion refusals and 0 compactions in the passing runs. Ablation, same model and code with the window forced back to 4096 and the old 240k-character budget: 0/2 (one run ended on an undecodable action, the other entered wrong amounts; the gate fired once and could not repair lost context) |
| Host deliverable gate (refuses a final answer while an output the task names is missing; at most 2 nudges; obligations only from the operator's task, never from content) | Safety net; not needed in the 5 passing H runs. A first version misread "(for example alpha.txt)" as an obligation and cost the search task; fixed, and every built-in objective is now checked against its own allowed changes in the tests |
| `edit_file` (exact, unique string replacement; same approval as an overwrite) and the `code_edit` qualification task | qwen3.5:9b and rana: `code_edit` 6/6 each across the three harnesses; both 14/14 on the edit catalogue |
| Refused changes are tracked by the host: the model is told plainly nothing changed; a later artifact that records the refused new content is flagged; the completion record names what was refused; an agent run with such a claim fails verification | Found live: an agent whose edit was refused (CLI run without `--yes`) wrote a summary stating the value it had failed to set. Regression tests reproduce that sequence; not yet re-run against a live model |

## 5a. Sample sizes and limits

These are small samples on one workstation. They show the capability exists and the
failure was removed; they are not rates, and they are not a security certification.

| Measure | Sample | What it does not establish |
|---|---|---|
| Scenario H with the 16k window | 8 runs: rana 5/5, qwen3.5:9b 3/3 | A success rate; behaviour on other hosts, Ollama versions or quantizations |
| Ablation back to 4096 | 2 runs, both failed | The exact failure probability at 4096 |
| Scenario I since the session 7 fixes | 4 runs: rana 2/3, qwen3.5:9b 1/1 | Reliability of the workforce; one failure was a model verifier passing a wrong report |
| Scenario J (injection retest) | 6 hostile runs per model (3 browser, 3 desktop), 0 prohibited actions executed | Resistance to other or adaptive injections. The guarantee that no prohibited action *executes* comes from the host policy broker, not from the model; the model attempted 0 here |
| Qualification | 2 trials per task, 14-18 tasks per catalogue | Held-out or adversarial task performance |

Two further limits stay open. A model verifier's PASS is not accepted on its own: in the
failed I run the verifier passed a report missing the payee names, and the host's
content checks failed the scenario. The refused-change detector matches the refused
content literally (ignoring spacing, quotes and case); a second check flags an artifact
that names a refused identifier with its refused value as a *possible* claim (warning
only). Paraphrases that do neither are not caught. Live, after the session 9 fixes, a
denied edit produced a truthful summary; before them, one run paraphrased the refused
value (see VERIFICATION_EVIDENCE "Session 9").

## 6. What to look for in an Ollama model

Use this checklist before pulling a model for agent work. Each item is a measured
predictor in section 4, not a preference.

1. **`ollama show <model>` lists `tools`.** Without a tool template the model can
   only use the fallback protocols; no model without one has qualified here.
2. **A 2025-or-later instruction model trained for tool use** (the Qwen3/Qwen3.5
   generation is the best measured family here). Older coder models (Qwen2.5-Coder,
   DeepSeek-Coder-V2, Codestral) write good code but are weak agents.
3. **Not a pure reasoning distillation** (for example the `deepseek-r1` distills).
   They think instead of acting. Models that declare a `thinking` *toggle* (levels
   false/true) are fine: the runtime can switch reasoning off when it stalls.
4. **Fits the GPU with a 16k window at 100 % GPU.** On 12 GB that means roughly a 9B
   model at Q4_K_M, or a mixture-of-experts model whose weights fit. CPU spill makes
   each step several times slower, which long tasks cannot absorb.
5. **At least about 8B parameters** for multi-step work; 4B-class models are useful
   for single tool calls only.
6. **Vision is optional.** The computer-use broker reads the accessibility tree, not
   pixels, so a vision model is not required, and the measured vision model
   (`qwen3-vl:8b`) was slower and less reliable than a text model.

Then verify, because the checklist predicts but does not certify:

```powershell
ollama pull <model>
11vated profile <model>
11vated qualify <model> --edit          # coding catalogue (add --shell to include run_command)
11vated qualify <model> --computer      # browser + desktop catalogue
11vated accept --models <model> --scenarios H,I,J
```

### Candidates to measure next, by GPU memory

Not measured on this workstation; third-party reports only, listed so the next
measurement has a shortlist. Treat every entry as unverified until qualified here.

| GPU memory | Candidates |
|---|---|
| 8-12 GB | `qwen3:8b`, `granite4` small variants (`qwen3.5:9b` is measured above) |
| 16 GB | `gpt-oss:20b`, `devstral` (24B, Q4) |
| 24 GB | `qwen3-coder:30b` (MoE, about 3B active), `qwen3:30b-a3b`, `gemma4:26b` (MoE) |
| 48 GB+ | `qwen3.5:27b` and larger Qwen3.5, `gpt-oss:120b` |
