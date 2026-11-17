# G11 owner QA checklist

G11 closes only when the owner has used the desktop and signs off below. Automated
checks cannot stand in for it: they confirm what was scripted, not whether the
product feels right to the person it is for. About 20 minutes, on the installed
release or `python -m ollama_workstation` from the repository.

What has already been checked automatically (session 9), so you can focus on judgement:

| Check | Evidence |
|---|---|
| All 10 pages render at 1480x940, 1120x720 and three smaller sizes | `scripts/capture_ui_gallery.py` (Agent Foundry was missing from it; added) |
| Chat agent mode, live `qwen3.5:9b`: the approval dialog appears; "Allow once" lets the agent make an exact edit; summary accurate | live GUI drive, see VERIFICATION_EVIDENCE "Session 9" |
| Same task with "Deny": nothing changes and the refusal is recorded | live GUI drive, see VERIFICATION_EVIDENCE "Session 9" |
| Frozen release starts; GUI renders 10 pages | `scripts/build_release.ps1` smoke test |

## Steps

Mark each step pass or fail and note anything that felt wrong, slow or unclear.

| # | Do this | Expect | Pass/fail, notes |
|---|---|---|---|
| 1 | Start the app | Command Center within a few seconds; runtime online; your GPU named | |
| 2 | Open Advanced Chat | Model defaults to a model that loads (here `qwen3.5:9b`), not the newest download | |
| 3 | Model chat: ask a question, then paste a long document (several pages) and ask about its start | The answer uses the start of the document (the window grows; nothing is cut silently) | |
| 4 | Switch to Agent workspace; in Settings point the workspace at a scratch folder with one small source file | Context panel shows the workspace and "~N tokens / 16,384 window" | |
| 5 | Ask the agent to change one value in that file and write a short note | An approval dialog names the file; "Allow once" makes the change; the note is accurate | |
| 6 | Repeat step 5 and choose Deny | The file is unchanged; the agent says it was refused; any note does not claim the change | |
| 7 | Press Stop during a long agent task | It stops within a few seconds; history shows the run as cancelled | |
| 8 | Agent Foundry: list loads quickly; select `qwen3.5:9b` | Qualification table shows it qualified; detail shows harness results | |
| 9 | Agent Foundry: create an agent with "exact edits", activate it, run it on the scratch folder | Runs to completion; an unattended edit is refused and named in the result | |
| 10 | Workforce Studio: review a plan for a small objective; approve | Tasks run; the verifier result and the host checks are both shown | |
| 11 | Run History: open the runs from steps 5-10 | Each has its trace; refused operations visible | |
| 12 | Resize to a small window; use the keyboard only for one chat turn | Nothing overlaps or is cut off; focus order is usable | |
| 13 | Close the app during an agent run | Asked to confirm; reopened later, the run is offered for recovery or shown as interrupted | |

## Sign-off

- Result: pass / pass with issues / fail
- Issues to fix before release:
- Owner:                       Date:
