"""Web research tools and the egress policy (offline: mock transport + resolver)."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from ollama_workstation.agents.tools import ToolError, WorkspaceTools
from ollama_workstation.agents.web import (
    EgressDenied,
    EgressPolicy,
    WebAccess,
    domain_matches,
    html_to_text,
)

PUBLIC = {"docs.example.com": ["93.184.216.34"], "example.com": ["93.184.216.34"],
          "evil.example.net": ["93.184.216.35"], "rebind.example.com": ["127.0.0.1"],
          "metadata.example.com": ["169.254.169.254"], "lan.example.com": ["192.168.1.10"],
          "mixed.example.com": ["93.184.216.36", "10.0.0.5"],
          "html.duckduckgo.com": ["52.142.124.215"]}


def resolver(host: str) -> list[str]:
    if host not in PUBLIC:
        raise OSError("no such host")
    return PUBLIC[host]


PAGE = ("<html><head><title>Release notes</title><style>.x{}</style>"
        "<script>steal()</script></head><body><h1>v2.4</h1><p>Rollout window: "
        "Tuesdays 10:00-12:00 UTC.</p><a href='/next'>Next</a></body></html>")


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/redirect-private":
        return httpx.Response(302, headers={"location": "https://rebind.example.com/x"})
    if path == "/redirect-ok":
        return httpx.Response(301, headers={"location": "/page"})
    if path == "/binary":
        return httpx.Response(200, headers={"content-type": "application/octet-stream"}, content=b"\0" * 10)
    if path == "/huge":
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"a" * 3_000_000)
    if path == "/secret":
        return httpx.Response(200, headers={"content-type": "text/plain"},
                              content=b"token ghp_abcdefghijklmnopqrstuvwxyz0123456789 here")
    if "duckduckgo" in request.url.host:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=(
            b'<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg='
            b'https%3A%2F%2Fdocs.example.com%2Fguide&rut=x">Guide <b>v2</b></a>'
            b'<a class="result__snippet" href="x">The rollout guide.</a>'
            b'<a rel="nofollow" class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a>'))
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=PAGE.encode())


def web(*, allowed: tuple[str, ...] = ("example.com",), approve: bool = False,
        enabled: bool = True) -> WebAccess:
    asked: list[str] = []

    def approval(description: str) -> bool:
        asked.append(description)
        return approve

    access = WebAccess(EgressPolicy(enabled=enabled, allowed_domains=allowed, approval=approval,
                                    resolver=resolver),
                       transport=httpx.MockTransport(handler))
    access.asked = asked  # type: ignore[attr-defined]
    return access


def test_fetch_returns_readable_text_marked_untrusted() -> None:
    result = web().fetch("https://docs.example.com/page")
    assert result["title"] == "Release notes"
    assert "Rollout window: Tuesdays" in result["text"]
    assert "steal()" not in result["text"] and ".x{}" not in result["text"]
    assert result["untrusted"] is True


@pytest.mark.parametrize("url, reason", [
    ("http://docs.example.com/page", "Scheme"),
    ("https://rebind.example.com/", "non-public"),
    ("https://metadata.example.com/latest", "non-public"),
    ("https://lan.example.com/", "non-public"),
    ("https://mixed.example.com/", "non-public"),
    ("https://127.0.0.1:11434/api/tags", "not a public address"),
    ("https://[::1]/", "not a public address"),
    ("https://user:pw@docs.example.com/", "credentials"),
    ("file:///C:/Windows/win.ini", "Scheme"),
])
def test_policy_refuses_unsafe_destinations(url: str, reason: str) -> None:
    allowed = ("example.com", "127.0.0.1", "::1")
    with pytest.raises(EgressDenied, match=reason):
        web(allowed=allowed).fetch(url)


def test_redirect_hops_are_rechecked() -> None:
    with pytest.raises(EgressDenied, match="non-public"):
        web().fetch("https://docs.example.com/redirect-private")
    assert web().fetch("https://docs.example.com/redirect-ok")["url"].endswith("/page")


def test_unlisted_domain_needs_approval_once() -> None:
    denied = web(allowed=())
    with pytest.raises(EgressDenied, match="not approved"):
        denied.fetch("https://evil.example.net/")
    approved = web(allowed=(), approve=True)
    approved.fetch("https://evil.example.net/a")
    approved.fetch("https://evil.example.net/b")
    assert approved.asked == ["Allow the agent to access the web domain: evil.example.net"]  # type: ignore[attr-defined]


def test_disabled_policy_refuses_everything() -> None:
    with pytest.raises(EgressDenied, match="disabled"):
        web(enabled=False).fetch("https://docs.example.com/page")


def test_content_type_and_size_limits() -> None:
    with pytest.raises(EgressDenied, match="not text"):
        web().fetch("https://docs.example.com/binary")
    result = web().fetch("https://docs.example.com/huge")
    assert result["truncated"] is True and len(result["text"]) == 20_000


def test_search_parses_results_and_drops_ads() -> None:
    result = web(allowed=("example.com", "duckduckgo.com")).search("rollout guide")
    assert result["results"] == [{"title": "Guide v2", "url": "https://docs.example.com/guide",
                                  "snippet": "The rollout guide."}]


def test_domain_matching() -> None:
    assert domain_matches("docs.example.com", "example.com")
    assert domain_matches("example.com", "*.example.com")
    assert not domain_matches("badexample.com", "example.com")
    assert not domain_matches("example.com.evil.net", "example.com")


def test_workspace_tools_expose_web_only_when_granted(tmp_path: Path) -> None:
    plain = WorkspaceTools(tmp_path)
    assert not {"web_search", "web_fetch"} & {d["function"]["name"] for d in plain.definitions()}
    with pytest.raises(ToolError):
        plain.invoke("web_fetch", {"url": "https://docs.example.com/page"})
    granted = WorkspaceTools(tmp_path, web=web())
    names = {d["function"]["name"] for d in granted.definitions()}
    assert {"web_search", "web_fetch"} <= names
    with pytest.raises(ToolError, match="non-public"):
        granted.invoke("web_fetch", {"url": "https://rebind.example.com/"})
    secret = granted.invoke("web_fetch", {"url": "https://docs.example.com/secret"})
    assert "ghp_" not in secret["text"] and secret["secrets_redacted"] >= 1


def test_html_to_text_keeps_links() -> None:
    title, text, links = html_to_text(PAGE)
    assert title == "Release notes" and "v2.4" in text and ("/next", "Next") in links


@pytest.mark.skipif(not os.environ.get("WEB_LIVE"), reason="set WEB_LIVE=1 for real network")
def test_live_fetch_and_search() -> None:
    access = WebAccess(EgressPolicy(enabled=True, allowed_domains=(
        "python.org", "duckduckgo.com")))
    page = access.fetch("https://docs.python.org/3/library/ipaddress.html")
    assert "ipaddress" in page["text"]
    found = access.search("python ipaddress module documentation")
    assert found["results"], found
