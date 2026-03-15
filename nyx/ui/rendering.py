"""Lightweight markdown rendering helpers for Nyx GTK text views."""

from __future__ import annotations

import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gtk, Pango

_CODE_KEYWORDS = {
    "and",
    "break",
    "case",
    "class",
    "const",
    "continue",
    "def",
    "else",
    "false",
    "fi",
    "for",
    "function",
    "if",
    "import",
    "in",
    "let",
    "match",
    "null",
    "or",
    "pass",
    "return",
    "then",
    "true",
    "while",
}

_MARKDOWN_THEME = {
    "heading": "#87BBB5",
    "inline_code": "#B47A56",
    "code_keyword": "#74A8A2",
}


def configure_markdown_theme(*, heading: str, inline_code: str, code_keyword: str) -> None:
    """Update markdown tag colors to match the active Nyx theme."""

    _MARKDOWN_THEME["heading"] = heading
    _MARKDOWN_THEME["inline_code"] = inline_code
    _MARKDOWN_THEME["code_keyword"] = code_keyword


def render_markdown_to_buffer(buffer: Gtk.TextBuffer, text: str) -> None:
    """Render lightweight markdown with basic code highlighting into a buffer."""

    _ensure_tags(buffer)
    buffer.set_text("")

    in_code_block = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            _insert_code_line(buffer, line)
            _insert_text(buffer, "\n", ["code"])
            continue

        heading_level = _heading_level(line)
        if heading_level:
            _insert_inline_segments(buffer, line[heading_level:].strip(), [f"heading-{heading_level}"])
            _insert_text(buffer, "\n", [])
            continue

        bullet_match = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", line)
        if bullet_match:
            prefix = f"{bullet_match.group(1)}• "
            _insert_text(buffer, prefix, ["bullet"])
            _insert_inline_segments(buffer, bullet_match.group(3), [])
            _insert_text(buffer, "\n", [])
            continue

        _insert_inline_segments(buffer, line, [])
        _insert_text(buffer, "\n", [])


def render_plain_text_to_buffer(buffer: Gtk.TextBuffer, text: str) -> None:
    """Render plain text into a GTK text buffer."""

    buffer.set_text(text)


def _ensure_tags(buffer: Gtk.TextBuffer) -> None:
    """Create the text tags needed by the lightweight markdown renderer."""

    tag_table = buffer.get_tag_table()
    definitions = {
        "heading-1": {
            "weight": Pango.Weight.BOLD,
            "scale": 1.25,
            "pixels_above_lines": 8,
            "foreground": _MARKDOWN_THEME["heading"],
        },
        "heading-2": {
            "weight": Pango.Weight.BOLD,
            "scale": 1.15,
            "pixels_above_lines": 6,
            "foreground": _MARKDOWN_THEME["heading"],
        },
        "heading-3": {
            "weight": Pango.Weight.BOLD,
            "scale": 1.08,
            "pixels_above_lines": 4,
            "foreground": _MARKDOWN_THEME["heading"],
        },
        "bullet": {"weight": Pango.Weight.BOLD},
        "inline-code": {"family": "monospace", "foreground": _MARKDOWN_THEME["inline_code"]},
        "code": {"family": "monospace", "left_margin": 12, "right_margin": 12},
        "code-keyword": {
            "family": "monospace",
            "weight": Pango.Weight.BOLD,
            "foreground": _MARKDOWN_THEME["code_keyword"],
        },
    }

    for name, properties in definitions.items():
        existing = tag_table.lookup(name)
        if existing is None:
            buffer.create_tag(name, **properties)
            continue
        for key, value in properties.items():
            existing.set_property(key, value)


def _heading_level(line: str) -> int:
    """Return markdown heading depth for a line, or zero if not a heading."""

    match = re.match(r"^(#{1,3})\s+.+$", line)
    if match is None:
        return 0
    return len(match.group(1))


def _insert_inline_segments(buffer: Gtk.TextBuffer, text: str, base_tags: list[str]) -> None:
    """Insert plain text plus inline code spans into the buffer."""

    matches = list(re.finditer(r"`([^`]+)`", text))
    if not matches:
        _insert_text(buffer, text, base_tags)
        return

    cursor = 0
    for match in matches:
        if match.start() > cursor:
            _insert_text(buffer, text[cursor:match.start()], base_tags)
        _insert_text(buffer, match.group(1), [*base_tags, "inline-code"])
        cursor = match.end()
    if cursor < len(text):
        _insert_text(buffer, text[cursor:], base_tags)


def _insert_code_line(buffer: Gtk.TextBuffer, line: str) -> None:
    """Insert a code block line with simple keyword highlighting."""

    matches = list(re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]*\b", line))
    cursor = 0
    for match in matches:
        if match.start() > cursor:
            _insert_text(buffer, line[cursor:match.start()], ["code"])
        word = match.group(0)
        tags = ["code-keyword"] if word in _CODE_KEYWORDS else ["code"]
        _insert_text(buffer, word, tags)
        cursor = match.end()
    if cursor < len(line):
        _insert_text(buffer, line[cursor:], ["code"])


def _insert_text(buffer: Gtk.TextBuffer, text: str, tag_names: list[str]) -> None:
    """Insert text at the end of a buffer using the provided tag names."""

    end_iter = buffer.get_end_iter()
    if tag_names:
        buffer.insert_with_tags_by_name(end_iter, text, *tag_names)
        return
    buffer.insert(end_iter, text)
