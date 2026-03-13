"""Tests for lightweight GTK markdown rendering helpers."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from nyx.ui.rendering import render_markdown_to_buffer


def test_render_markdown_to_buffer_creates_expected_text_and_tags() -> None:
    """Markdown rendering should normalize text and install formatting tags."""

    buffer = Gtk.TextBuffer()

    render_markdown_to_buffer(
        buffer,
        "# Heading\n- item one\n\n```python\nreturn value\n```",
    )

    start = buffer.get_start_iter()
    end = buffer.get_end_iter()
    text = buffer.get_text(start, end, True)
    tag_table = buffer.get_tag_table()

    assert text == "Heading\n• item one\n\nreturn value\n"
    assert tag_table.lookup("heading-1") is not None
    assert tag_table.lookup("bullet") is not None
    assert tag_table.lookup("code") is not None
    assert tag_table.lookup("code-keyword") is not None
