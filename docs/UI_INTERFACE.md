# 11vatedTech Intelligence Interface

**Status:** Implemented, locally verified on Windows and hosted-CI verified. Run
[43139186015](https://github.com/11vatedTech/Ollama-DesktopAgent/actions/runs/43139186015)
passed all four Python 3.11/3.12 Windows/Ubuntu jobs. The interface is a
PySide6/Qt client of the existing local application services. It is not
production-certified; the limits of the current evidence are listed below.

The desktop shell presents the workstation as nine coordinated workspaces. It
uses a local-only visual language built from shared tokens and widgets, with a
`QPainter` intelligence core, real event-driven activity states and charts fed by
actual host telemetry. It does not invent reasoning progress, task success or
model activity.

## Design contract

- **Local first.** No web runtime, hosted UI service or remote font dependency is
  required. The shell runs against the configured local Ollama endpoint and the
  local SQLite repository.
- **Service-backed controls.** Buttons and pages call `AppContext` services and
  workers; provider calls, policy decisions and recovery eligibility remain
  outside the widgets.
- **Truthful state.** Chat streaming, cancellation, workforce task transitions,
  run recovery candidates and telemetry are displayed from real backend events.
  A verifier `FAIL` is shown as a failure, never relabelled as success.
- **One visual system.** `theme.py`, `components.py`, `intelligence_core.py` and
  the page modules share spacing, color, type, border, focus and disabled-state
  tokens. The shell uses the same components for desktop and compact layouts.
- **Local persistence.** Conversations save to SQLite; reopening a session
  restores its messages. Settings and the last page persist under the
  application data directory.

### Motion and accessibility

The intelligence core targets a 60 FPS cadence with a 16 ms timer when motion is
enabled. Reduced motion switches to a 750 ms cadence and preserves every state
label and status message. Page changes use a short opacity transition that is
suppressed under reduced motion.

The shell supplies accessible names and descriptions for the core and
navigation, keyboard-focus styling, disabled-state styling, readable contrast
tokens, tooltips on compact controls and an explicit responsive layout. This is
not a completed screen-reader, switch-input, high-contrast or WCAG audit.

### Responsive behavior

| Surface | Desktop | Compact (1120 × 720 window) |
|---|---|---|
| Shell | 236 px sidebar, full top rail | sidebar remains; the long status text is hidden and the core is smaller |
| Command Center | side-by-side core and copy; five telemetry tiles; three lower cards | stacked core and copy; three telemetry tiles; two lower cards; page scroll |
| Advanced Chat | conversation rail, chat, context panel | context panel collapses; **Show context** restores it as a pinned panel |
| Workforce Studio | left plan/graph/assignments and right state/roster/runs | both columns scroll independently; no page-level horizontal clipping |

The breakpoints are 1050 px for Command Center, 1030 px for Chat and 880 px for
the Model Intelligence split. The gallery also exercises 1280 × 800, 1024 × 768
and 900 × 700 logical windows; at 900 px the Model inventory stacks above a
scrollable dossier so no horizontal clipping is introduced.

## Workspaces

| Workspace | What is wired |
|---|---|
| Command Center | Local inventory, profile count, GPU/VRAM/RAM/CPU/disk telemetry, active operations, recent execution, recovery attention, quick actions |
| Advanced Chat | Model/agent/routed/workforce modes, streamed responses, stop, attachments with vision checks, persisted sessions and context inspection |
| Model Intelligence | Installed inventory, filters, saved profiles, evidence bars, model dossier, comparison and benchmark controls |
| Workforce Studio | Objective, analysis/delivery plan templates, validated DAG graph, role-fit assignments, approval/stop, live task status and durable runs |
| Agent Operations | Active-operation register, durable run journal, execution events and technical-inspection toggle |
| Integration Hub | Ollama endpoint/version/model counts, runtime health check, MCP grant state and trust boundaries |
| System Laboratory | Live telemetry, diagnostics, benchmark history, recovery candidates and local logs |
| Run History & Recovery | Searchable run archive, event journal, fail-closed recovery candidates and explicit resume/discard actions |
| Settings | Runtime endpoint/budgets/keep-alive, workspace, policy, motion, MCP JSON and local paths |

## Validation evidence

### Automated and local

| Layer | Evidence | Result |
|---|---|---|
| Focused UI tests | `QT_QPA_PLATFORM=offscreen pytest tests/test_ui_workstation.py -q` | 11 passed |
| Full suite, Python 3.12.13 | `pytest -q` | 202 passed, 4 skipped |
| Static checks | Ruff and `compileall` over `src`, `tests` and the gallery script | passed |
| Hosted CI | GitHub Actions run [43139186015](https://github.com/11vatedTech/Ollama-DesktopAgent/actions/runs/43139186015) | all four jobs passed; Ubuntu 3.11/3.12: 192 passed, 4 skipped; Windows 3.11/3.12: 191 passed, 5 skipped; both Windows desktop imports and 11-test UI suites passed |
| Live local profile | `qwen2.5-coder:7b`, Ollama 0.40.16, two repeats per probe | 16 probe calls; 44.22 tok/s mean; reasoning/structured output/actions passed; vision and tool calling unsupported; code generation failed; long-context and other dimensions unassessed |
| Live local benchmark | `qwen2.5-coder:7b`, `11vated-core@1.0.0`, two repeats | 8/12 passed, score 66.67, manifest `9e68732a…f79`; full profile now carries this benchmark summary |
| Bounded workforce (CLI) | Analysis plan, `qwen2.5-coder:7b`, run `23dc040f-1aa3-40d1-882a-b3a2c7f6b6e0` | completed with `verification_passed=true`; prose verifier was schema-normalized; host audit recorded |
| Bounded workforce (desktop) | Real `WorkforcePage`, run `beee5cd1-342a-449b-994b-e47d3a53d9be` | completed with `verification_passed=true`; audit recorded model verdict `FAIL`, host decision `PASS`, contradiction override `true` |
| Workforce failure path | Desktop run `fb65bbb1-064e-4be6-9937-a40c2d3acd30` | analysis returned a completion claim without writing the artifact; host blocked verification and reported `verification_failed` |

### Visually inspected

Twenty-four native Windows captures were generated and inspected:

- desktop: Command Center, Chat, Model Intelligence, Workforce, Agent
  Operations, Integration Hub, System Laboratory, Run History, Settings;
- compact: Command Center, Chat, Workforce;
- responsive matrix: Command, Chat, Model Intelligence and Workforce at
  1280 × 800, 1024 × 768 and 900 × 700.

Confirmed in the final captures: all nine workspaces render with the shared
shell; the Command Center reflows its stat and action rows without horizontal
clipping; Chat's context panel collapses; Model Intelligence switches to a
vertical inventory/dossier split at compact widths and the dossier status pills
wrap; Workforce columns and the execution journal scroll; selected run rows
render two lines; Settings shows the effective `120` timeout, `12` step budget
and workspace path. Live inventory, 15 metadata profiles, one empirical profile
and host telemetry appear in the appropriate pages.

Not visually or semantically verified: 4K/high-DPI scaling beyond the host's
Windows scaling, light theme, screen readers, high-contrast mode, touch input,
clean Windows VM/client hardware and exhaustive keyboard traversal. A native
screenshot is visual evidence, not a certification of those surfaces.

### Performance

A paint-only sample of the 410 × 410 intelligence core (240 frames, offscreen Qt,
Python 3.12, warm process) measured a mean of **5.77 ms**, median **5.43 ms**,
p95 **8.01 ms** and max **9.84 ms** per frame. This is below the 16 ms target for
the core paint itself, but it is not an end-to-end FPS measurement: window
composition, other widgets and the user's GPU/driver are excluded.

## Screenshot gallery

Desktop captures are 2220 × 1410 pixels; compact captures are 1680 × 1080
pixels because Windows scales the 1480 × 940 and 1120 × 720 logical windows.
The responsive matrix adds 1920 × 1200, 1536 × 1152 and 1350 × 1050 captures.

| Workspace | Desktop | Compact |
|---|---|---|
| Command Center | [view](../tests/screenshots/windows-command.png) | [view](../tests/screenshots/windows-narrow-command.png) |
| Advanced Chat | [view](../tests/screenshots/windows-chat.png) | [view](../tests/screenshots/windows-narrow-chat.png) |
| Model Intelligence | [view](../tests/screenshots/windows-models.png) | — |
| Workforce Studio | [view](../tests/screenshots/windows-workforce.png) | [view](../tests/screenshots/windows-narrow-workforce.png) |
| Agent Operations | [view](../tests/screenshots/windows-operations.png) | — |
| Integration Hub | [view](../tests/screenshots/windows-integrations.png) | — |
| System Laboratory | [view](../tests/screenshots/windows-laboratory.png) | — |
| Run History | [view](../tests/screenshots/windows-history.png) | — |
| Settings | [view](../tests/screenshots/windows-settings.png) | — |

Responsive matrix: [Command 1280](../tests/screenshots/windows-1280x800-command.png),
[Command 1024](../tests/screenshots/windows-1024x768-command.png),
[Command 900](../tests/screenshots/windows-900x700-command.png),
[Models 1280](../tests/screenshots/windows-1280x800-models.png),
[Models 1024](../tests/screenshots/windows-1024x768-models.png),
[Models 900](../tests/screenshots/windows-900x700-models.png),
[Chat 900](../tests/screenshots/windows-900x700-chat.png),
[Workforce 900](../tests/screenshots/windows-900x700-workforce.png).

## Reproduce

Run the focused UI suite:

```powershell
$env:QT_QPA_PLATFORM="offscreen"
python -m pytest tests/test_ui_workstation.py -q
Remove-Item Env:QT_QPA_PLATFORM
```

Regenerate the native gallery with Ollama running and a real model available:

```powershell
python scripts/capture_ui_gallery.py --output tests/screenshots
```

The gallery uses the native Windows platform rather than `offscreen` because the
offscreen platform reports no font families on this host. It waits for live
inventory/telemetry, captures the desktop pages, then resizes to the compact
window and captures the three responsive views.
