"""Host-side deliverable contract: refuse a premature "done" while a named output is missing.

Live acceptance (scenario H, sessions 5-7) showed the dominant failure of capable local
models is not a wrong action but an early stop: the model does the interactive work and
then declares itself finished without writing the file the task asked for. Strong hosted
agents avoid this with a completion check before they stop; this module gives every model
the same check, deterministically, from the host.

Rules:

* Obligations come only from the operator's task text, never from workspace content, tool
  output or retrieved memory, so injected text cannot create (or cancel) an obligation.
* A path is an obligation only when the nearest preceding verb in its clause is an output
  verb (write, save, create, ...) and the clause is not negated ("do not create x.txt").
* An obligation is satisfied when the file exists and, if it already existed when the run
  started, its size or modification time changed. A run resumed from a checkpoint has no
  start snapshot, so there existence alone satisfies it.
* The gate only nudges. After its budget is spent the final answer is accepted and the
  missing outputs are recorded; the independent verifier still decides pass or fail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

OUTPUT_VERBS = frozenset({
    "write", "writes", "writing", "save", "saving", "create", "creating", "produce",
    "producing", "generate", "generating", "export", "exporting", "output", "store",
    "storing", "record", "put", "append", "update", "updating", "overwrite", "emit",
    "draft", "compose",
})
INPUT_VERBS = frozenset({
    "read", "reading", "open", "opening", "inspect", "inspecting", "verify", "verifying",
    "check", "checking", "compare", "comparing", "review", "reviewing", "load", "loading",
    "parse", "parsing", "analyse", "analyze", "examine", "use", "using", "against",
    "summarise", "summarize", "list", "search", "find", "count", "audit",
})
# The word right before a path says which way data flows.
DESTINATION_WORDS = frozenset({"into", "to", "as", "in"})
SOURCE_WORDS = frozenset({"of", "from"})
_NEGATION = re.compile(r"\b(?:do not|don't|never|must not|should not|without|avoid|no)\b")

# A relative path with a file extension: report/total.txt, notes.md, src/app.py.
_PATH = re.compile(r"(?<![\w/\\:.-])((?:[\w.-]+[/\\])*[\w-][\w.-]*\.[A-Za-z][A-Za-z0-9]{0,7})(?![\w/\\])")
_WORD = re.compile(r"[A-Za-z']+")
_EXAMPLE = re.compile(r"(?:\bfor example|\bfor instance|\beg|\bsuch as|\blike|\bsay)[\s:,]*$")
_CLAUSE_BREAK = re.compile(r"(?<=[.;!?])\s+|\n+|\bthen\b", re.IGNORECASE)
MAX_OBLIGATIONS = 12


def _clauses(text: str) -> list[str]:
    return [part for part in _CLAUSE_BREAK.split(text) if part and part.strip()]


def extract_declared_outputs(task: str) -> list[str]:
    """Return workspace-relative output paths the task explicitly asks the agent to produce."""
    found: list[str] = []
    # "e.g." and "i.e." would otherwise end a clause and read as file names.
    task = re.sub(r"\b([ei])\.([gde])\.?", lambda m: "eg" if m.group(1) == "e" else "ie",
                  task, flags=re.IGNORECASE)
    for clause in _clauses(task):
        lowered = clause.lower()
        for match in _PATH.finditer(clause):
            path = match.group(1).replace("\\", "/").rstrip(".")
            start = match.start(1)
            if "://" in clause[max(0, start - 12):start + 1] or path.startswith(("www.", "/")):
                continue
            if ".." in Path(path).parts or re.match(r"^\d+(\.\d+)+$", path):
                continue
            prefix = lowered[:start]
            # An illustration is not an output: "(for example alpha.txt)", "e.g. a.md",
            # "such as notes.txt". Treating one as owed made the gate demand a file the
            # task forbids (live qualification, search_and_locate, 2026-11-16).
            if prefix.count("(") > prefix.count(")") or _EXAMPLE.search(prefix[-40:]):
                continue
            words = _WORD.findall(prefix)
            near = words[-3:]
            if any(word in SOURCE_WORDS for word in near[-2:]):
                continue  # "from notes.md", "a summary of notes.md"
            if any(word in DESTINATION_WORDS for word in near):
                # "... into report.md": a destination, if the clause asks for output at all.
                if not any(word in OUTPUT_VERBS for word in words):
                    continue
            else:
                verb = next((word for word in reversed(words)
                             if word in OUTPUT_VERBS or word in INPUT_VERBS), None)
                if verb not in OUTPUT_VERBS:
                    continue
            if _NEGATION.search(prefix):
                continue
            if path not in found:
                found.append(path)
            if len(found) >= MAX_OBLIGATIONS:
                return found
    return found


@dataclass
class DeliverableLedger:
    """Start-of-run snapshot of declared outputs, checked before completion is accepted."""

    workspace: Path
    paths: list[str]
    baseline: dict[str, tuple[int, int] | None] = field(default_factory=dict)
    has_baseline: bool = False

    @classmethod
    def for_task(cls, task: str, workspace: str | Path, *, snapshot: bool = True) -> DeliverableLedger:
        ledger = cls(Path(workspace).expanduser().resolve(), extract_declared_outputs(task))
        if snapshot:
            ledger.baseline = {path: ledger._stat(path) for path in ledger.paths}
            ledger.has_baseline = True
        return ledger

    def _resolve(self, path: str) -> Path | None:
        candidate = (self.workspace / path).resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError:
            return None
        return candidate

    def _stat(self, path: str) -> tuple[int, int] | None:
        target = self._resolve(path)
        if target is None or not target.is_file():
            return None
        info = target.stat()
        return (info.st_size, info.st_mtime_ns)

    def missing(self) -> list[str]:
        outstanding = []
        for path in self.paths:
            current = self._stat(path)
            if current is None:
                outstanding.append(path)
            elif self.has_baseline and self.baseline.get(path) == current:
                outstanding.append(path)
        return outstanding


def completion_notice(missing: list[str]) -> str:
    names = ", ".join(missing)
    return (
        "HOST NOTICE: you declared the task finished, but the task asks you to produce "
        f"{names} and the host does not see it written yet. Do that now with the file tool, "
        "using the results you already have. If it truly cannot be produced, reply with a "
        "final answer that says why."
    )
