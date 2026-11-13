# Recovery protocol

Use this after any interruption. Trust the repository over this ledger.

1. `git status -sb && git log --oneline -5` — confirm the branch is
   `campaign/complete-agent-v1` and note the head commit and any uncommitted work.
2. Compare the head with `current_commit` in EXECUTION_LEDGER.json. If they
   differ, read `git log <ledger commit>..HEAD` and reconcile the ledger first.
3. `python engineering/campaign/validate_ledger.py` — structural check of the ledger.
4. `python -m pytest -q` and `ruff check .` — re-establish the real test state.
   If results differ from VERIFICATION_EVIDENCE.md, the code is the truth:
   investigate, fix, then update the evidence.
5. Uncommitted changes: run the tests. Keep and commit if green and coherent;
   otherwise `git stash` them with a descriptive message and record it here.
6. Read `next_action` in EXECUTION_LEDGER.json and execute it. It must be a
   concrete command or file-level change; if it is vague, replace it before working.
7. After each coherent unit: run the suite, commit, then update
   EXECUTION_LEDGER.json (`python engineering/campaign/validate_ledger.py` again),
   the matrix and the evidence file in a following commit.

Interrupted agent runs (product, not campaign): `11vated recover --list`.
A checkpoint in `tool_in_progress` is never replayed automatically.

## Engineering-container notes (cloud sessions)

- The working copy lives at `/home/claude/repo`; run tests with
  `PYTHONPATH=/home/claude/shim:src python3 -m pytest -o addopts="" -q`.
- Never copy staged workstation files over the working copy: the workstation
  checkout is behind the campaign branch and the copy silently reverts tracked
  files. (This happened at the start of session 2 and was repaired with
  `git checkout --` from commit 423c296; no work was lost.)
- Deliver to the workstation by writing changed files into its folder and by
  refreshing `campaign-complete-agent-v1.bundle`.

