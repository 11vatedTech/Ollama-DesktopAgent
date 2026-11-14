"""Host-side authorization and audit for browser and computer actions.

The model proposes an action; the broker decides. A decision depends only on
facts the host observed: what kind of action it is, the role and accessible
label of the element as read from the page or the UI Automation tree, the
target's process integrity, and what the operator granted. Nothing the model
writes (and nothing a web page or application says) can change a decision,
which is what makes prompt injection unable to *authorize* a prohibited action.

Default policy (from the release decision):

==============================================  ===============================
Read public websites                            allowed under the egress policy
Execute page JavaScript                         allowed inside browser isolation
Read local files (file:// and similar)          denied
Localhost / private networks                    denied unless explicitly granted
Download files                                  quarantined, never opened
Upload files                                    workspace files only, confirmed
User browser cookies / profile                  never used (fresh context)
Inspect / type into application windows         only apps the agent launched
Password and credential fields                  denied
Delete, send, submit, publish                   human confirmation
Purchase, payment, transfer, account security   human confirmation
Administrator / UAC / elevated windows          denied
==============================================  ===============================

Human confirmation goes to the operator's approval callback. Unattended runs
have no approver, so such actions are refused.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

ALLOW = "allow"
CONFIRM = "confirm"
DENY = "deny"

DESTRUCTIVE = re.compile(
    r"\b(delete|remove|erase|wipe|destroy|discard|drop|clear all|empty trash|uninstall|"
    r"format|reset|send|submit|publish|post|share|reply|forward|sign out|log ?out|"
    r"unsubscribe|cancel (order|subscription|account))\b", re.IGNORECASE)
FINANCIAL = re.compile(
    r"\b(buy|purchase|pay|payment|checkout|check out|order now|place order|transfer|"
    r"wire|donate|subscribe|billing|credit card|withdraw|invest|trade|bid)\b", re.IGNORECASE)
ACCOUNT_SECURITY = re.compile(
    r"\b(password|passcode|2fa|two-factor|mfa|security (settings|question|key)|"
    r"recovery (code|email|phone)|api key|access token|change email|delete account)\b",
    re.IGNORECASE)

ELEVATION_CLASSES = frozenset({"Credential Dialog Xaml Host", "$$$Secure UAP Dummy Window Class "
                                                              "For Interim Dialog"})
ELEVATION_PROCESSES = frozenset({"consent.exe", "credentialuibroker.exe"})


class InteractionDenied(PermissionError):
    """The broker refused an action. The message says why."""


@dataclass(frozen=True, slots=True)
class Decision:
    outcome: str  # allow | confirm | deny
    reason: str
    category: str = "ordinary"


def classify_label(label: str) -> str:
    """Risk category of an action from the target's accessible label/role text."""
    text = " ".join(label.split())
    if ACCOUNT_SECURITY.search(text):
        return "account_security"
    if FINANCIAL.search(text):
        return "financial"
    if DESTRUCTIVE.search(text):
        return "destructive"
    return "ordinary"


@dataclass
class InteractionGrants:
    """What the operator granted this agent. Never derived from model output."""
    browser: bool = False
    computer: bool = False
    computer_apps: tuple[str, ...] = ()       # app keys the agent may launch and control
    private_hosts: tuple[str, ...] = ()       # hosts allowed despite private addresses
    allow_uploads: bool = False
    allow_raw_input: bool = False             # physical mouse/keyboard fallback


class AuditTrail:
    """Append-only, hash-chained record of every proposed action and its outcome."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._previous = self._last_hash()
        self.records: list[dict[str, Any]] = []

    def _last_hash(self) -> str:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return "0" * 64
        return str(json.loads(lines[-1])["hash"]) if lines else "0" * 64

    def append(self, **entry: Any) -> dict[str, Any]:
        with self._lock:
            record = {"seq": len(self.records) + 1, "time": time.time(), **entry,
                      "prev": self._previous}
            encoded = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
            record["hash"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self._previous = record["hash"]
            self.records.append(record)
            return record

    @staticmethod
    def verify(path: Path) -> bool:
        previous = "0" * 64
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            claimed = record.pop("hash")
            if record.get("prev") != previous:
                return False
            encoded = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
            if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != claimed:
                return False
            previous = claimed
        return True


def default_audit_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "11vated-interaction" / "audit"
    return base


class InteractionBroker:
    """Decides, records and (for confirmation) asks the operator."""

    def __init__(self, grants: InteractionGrants, *,
                 approval: Callable[[str], bool] | None = None,
                 audit: AuditTrail | None = None, session: str = "") -> None:
        self.grants = grants
        self.approval = approval
        self.session = session or f"session-{int(time.time() * 1000)}"
        self.audit = audit or AuditTrail(default_audit_dir() / f"{self.session}.jsonl")

    # -- decisions --------------------------------------------------------------------
    def decide(self, service: str, action: str, *, label: str = "", role: str = "",
               password_field: bool = False, elevated: bool = False,
               elevation_window: bool = False) -> Decision:
        if service == "browser" and not self.grants.browser:
            return Decision(DENY, "this agent is not granted the browser")
        if service == "computer" and not self.grants.computer:
            return Decision(DENY, "this agent is not granted computer use")
        if elevated or elevation_window:
            return Decision(DENY, "administrator, elevated and UAC windows are never controllable",
                            "elevation")
        if password_field and action in {"type", "read_value", "select"}:
            return Decision(DENY, "password and credential fields are never read or filled by agents",
                            "account_security")
        if action == "upload":
            if not self.grants.allow_uploads:
                return Decision(DENY, "file upload is not granted")
            return Decision(CONFIRM, "uploading a workspace file needs confirmation", "upload")
        if action in {"click", "submit", "type", "select", "key"}:
            category = classify_label(f"{role} {label}")
            if category != "ordinary":
                return Decision(CONFIRM, f"'{label[:80]}' is a {category.replace('_', ' ')} action "
                                         "and needs human confirmation", category)
        return Decision(ALLOW, "permitted by policy")

    def authorize(self, service: str, action: str, target: str = "", **facts: Any) -> Decision:
        """Decide, ask for confirmation when needed, record, and raise when refused."""
        decision = self.decide(service, action, **facts)
        outcome = decision.outcome
        if outcome == CONFIRM:
            prompt = (f"Agent {service} action needs confirmation: {action} on "
                      f"{facts.get('role', '')} '{facts.get('label', target)}'. {decision.reason}.")
            approved = bool(self.approval(prompt)) if self.approval is not None else False
            outcome = ALLOW if approved else DENY
            decision = Decision(outcome, decision.reason + ("; approved by operator" if approved
                                                             else "; not confirmed (refused)"),
                                decision.category)
        self.audit.append(session=self.session, service=service, action=action, target=target[:300],
                          label=str(facts.get("label", ""))[:200], role=str(facts.get("role", "")),
                          decision=decision.outcome, category=decision.category,
                          reason=decision.reason)
        if decision.outcome != ALLOW:
            raise InteractionDenied(f"Refused by policy: {decision.reason}")
        return decision

    def record_result(self, service: str, action: str, target: str, result: str) -> None:
        self.audit.append(session=self.session, service=service, action=action,
                          target=target[:300], decision="result", result=result[:500])

    def denials(self) -> list[dict[str, Any]]:
        return [item for item in self.audit.records if item.get("decision") == DENY]