"""Message rendering with deliberate, dependency-free Markdown and code support."""

from __future__ import annotations

import html
import json
import re
from urllib.parse import quote

from ollama_workstation.ui.theme import COLORS

_FENCE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_HEADING = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)

_KEYWORDS = {
    "python": (
        "and as assert async await break class continue def del elif else except finally for from "
        "global if import in is lambda nonlocal not or pass raise return try while with yield "
        "match case True False None"
    ).split(),
    "javascript": (
        "async await break case catch class const continue default delete else export extends "
        "finally for from function if import in instanceof let new of return static super switch "
        "this throw try typeof var while yield true false null undefined"
    ).split(),
    "json": ["true", "false", "null"],
    "bash": ["if", "then", "else", "fi", "for", "do", "done", "while", "function", "export"],
    "sql": ["select", "from", "where", "join", "on", "group", "by", "order", "insert", "update",
            "delete", "create", "table", "and", "or", "limit"],
}


def _code_language(info: str) -> str:
    value = info.strip().split()[0].casefold() if info.strip() else ""
    aliases = {
        "py": "python", "js": "javascript", "ts": "javascript", "typescript": "javascript",
        "sh": "bash", "shell": "bash", "console": "bash", "ps1": "bash", "powershell": "bash",
    }
    return aliases.get(value, value)


def highlight_code(source: str, language: str = "") -> str:
    """Small, safe syntax highlighter for local code previews and chat blocks."""
    lang = _code_language(language)
    token = re.compile(
        r"(`[^`]*`|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|"
        r"#.*$|//.*$|/\*.*?\*/|\b\d+(?:\.\d+)?\b|"
        r"\b[" + re.escape("A-Za-z_") + r"][A-Za-z0-9_]*\b)",
        re.MULTILINE | re.DOTALL,
    )
    keywords = {item.casefold() for item in _KEYWORDS.get(lang, [])}
    output: list[str] = []
    position = 0
    for match in token.finditer(source):
        output.append(html.escape(source[position:match.start()]))
        value = match.group(0)
        escaped = html.escape(value)
        if value.startswith(("#", "//", "/*")):
            output.append(f'<span style="color:{COLORS["faint"]}">{escaped}</span>')
        elif value.startswith(("'", '"', "`")):
            output.append(f'<span style="color:{COLORS["green"]}">{escaped}</span>')
        elif value[0].isdigit():
            output.append(f'<span style="color:{COLORS["amber"]}">{escaped}</span>')
        elif value.casefold() in keywords:
            output.append(f'<span style="color:{COLORS["violet"]}">{escaped}</span>')
        else:
            output.append(escaped)
        position = match.end()
    output.append(html.escape(source[position:]))
    return "".join(output)


def _inline_markup(value: str) -> str:
    escaped = html.escape(value)
    escaped = _INLINE.sub(
        lambda match: (
            f'<span style="font-family:Consolas;color:{COLORS["cyan"]};'
            f'background:{COLORS["surface_alt"]};">&nbsp;{match.group(1)}&nbsp;</span>'
        ),
        escaped,
    )
    escaped = _BOLD.sub(r"<b>\1</b>", escaped)
    escaped = _ITALIC.sub(r"<i>\1</i>", escaped)
    escaped = _LINK.sub(
        lambda match: (
            f'<a style="color:{COLORS["cyan"]};" href="{html.escape(match.group(2))}">'
            f"{match.group(1)}</a>"
        ),
        escaped,
    )
    return escaped


def _text_block(value: str) -> str:
    value = value.strip("\n")
    if not value:
        return ""
    lines = value.splitlines()
    output: list[str] = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        heading = _HEADING.match(line)
        if heading:
            if in_list:
                output.append("</ul>")
                in_list = False
            level = len(heading.group(1))
            size = {1: 21, 2: 17, 3: 15, 4: 13}[level]
            output.append(
                f'<h{level} style="color:{COLORS["ice"]};font-size:{size}px;'
                f'margin:8px 0 3px 0;">{_inline_markup(heading.group(2))}</h{level}>'
            )
            continue
        if stripped.startswith(("- ", "* ")):
            if not in_list:
                output.append("<ul style='margin:4px 0 6px 18px;'>")
                in_list = True
            output.append(f"<li>{_inline_markup(stripped[2:])}</li>")
            continue
        if in_list:
            output.append("</ul>")
            in_list = False
        if stripped:
            output.append(
                f'<p style="margin:0 0 5px 0;line-height:145%;">{_inline_markup(line)}</p>'
            )
        else:
            output.append("<br>")
    if in_list:
        output.append("</ul>")
    return "".join(output)


def render_markdown(text: str) -> str:
    """Render a bounded, readable subset of Markdown to Qt-rich HTML."""
    text = text or ""
    parts: list[str] = []
    cursor = 0
    for match in _FENCE.finditer(text):
        parts.append(_text_block(text[cursor:match.start()]))
        language = match.group(1).strip()
        code = match.group(2).rstrip("\n")
        parts.append(
            '<table width="100%" cellspacing="0" cellpadding="0" '
            f'style="margin:7px 0;background:{COLORS["void"]};border:1px solid {COLORS["border"]};">'
            "<tr><td style='padding:8px 10px;'>"
            f'<div style="color:{COLORS["faint"]};font-size:9px;margin-bottom:3px;">'
            f"{(html.escape(language) or 'TEXT').upper()}</div>"
            f'<pre style="margin:0;color:{COLORS["text_secondary"]};font-family:Consolas;'
            f'font-size:11px;line-height:140%;white-space:pre-wrap;">'
            f"{highlight_code(code, language)}</pre></td></tr></table>"
        )
        cursor = match.end()
    parts.append(_text_block(text[cursor:]))
    body = "".join(part for part in parts if part)
    return (
        f'<div style="color:{COLORS["text"]};font-family:Segoe UI;font-size:13px;'
        f'line-height:145%;">{body}</div>'
    )


def render_compact(text: str, limit: int = 320) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) > limit:
        normalized = normalized[: limit - 1].rstrip() + "…"
    return html.escape(normalized)


def pretty_json(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def conversation_to_markdown(
    title: str,
    messages: list[dict[str, object]],
    *,
    model: str = "",
) -> str:
    """Create a portable, local-only conversation export."""
    lines = [f"# {title or 'Intelligence session'}", ""]
    if model:
        lines.extend([f"Model: `{model}`", ""])
    lines.append(f"Messages: {len(messages)}")
    lines.append("")
    for message in messages:
        role = str(message.get("role") or "assistant")
        heading = "User" if role == "user" else "11vated Intelligence"
        meta = " · ".join(
            str(message[key]) for key in ("model", "mode", "status")
            if message.get(key)
        )
        lines.append(f"## {heading}" + (f" · {meta}" if meta else ""))
        lines.append("")
        content = str(message.get("content") or "").strip()
        if message.get("error"):
            lines.append("> This message records a failed or stopped operation.")
            lines.append("")
        lines.append(content or "*No content recorded.*")
        artifacts = message.get("artifacts")
        if isinstance(artifacts, list) and artifacts:
            lines.extend(["", "Artifacts:"])
            lines.extend(f"- `{item}`" for item in artifacts)
        lines.extend(["", "---", ""])
    return "\n".join(lines).rstrip() + "\n"


def file_url(path: str) -> str:
    normalized = path.replace("\\", "/")
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return "file://" + quote(normalized, safe="/:")
