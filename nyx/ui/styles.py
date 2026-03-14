"""Shared GTK CSS helpers for Nyx overlay windows."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gtk

UI_CSS = """
window {
  color: #ece8dd;
  font: __NYX_FONT__;
}

.nyx-overlay-window {
  background:
    linear-gradient(180deg, rgba(18, 22, 29, 0.975), rgba(10, 13, 18, 0.975)),
    radial-gradient(circle at top right, rgba(192, 124, 63, 0.09), transparent 42%);
  border: 1px solid rgba(242, 165, 95, 0.24);
  border-radius: 22px;
  box-shadow: 0 22px 60px rgba(0, 0, 0, 0.38);
}

.nyx-launcher-shell {
  min-width: 760px;
}

.nyx-header-row {
  padding-bottom: 2px;
}

.nyx-brand-title {
  font-size: 16pt;
  font-weight: 800;
  letter-spacing: 0.24em;
  color: #f5e8d0;
}

.nyx-brand-subtitle {
  opacity: 0.74;
  font-size: 9.5pt;
}

.nyx-status-row {
  padding: 4px 0 2px 0;
}

.nyx-status-label {
  font-weight: 700;
}

.nyx-chip {
  background: rgba(242, 165, 95, 0.12);
  border: 1px solid rgba(242, 165, 95, 0.18);
  border-radius: 999px;
  padding: 5px 11px;
}

.nyx-toolbar button,
.nyx-sidebar-toolbar button {
  border-radius: 999px;
  border: 1px solid rgba(242, 165, 95, 0.16);
  background: rgba(255, 255, 255, 0.03);
  padding: 6px 12px;
}

.nyx-prompt,
.nyx-response,
.nyx-sidebar,
.nyx-settings-editor {
  border-radius: 16px;
  background:
    linear-gradient(180deg, rgba(13, 17, 23, 0.92), rgba(10, 12, 18, 0.92));
  border: 1px solid rgba(255, 255, 255, 0.05);
  padding: 12px;
}

.nyx-prompt text,
.nyx-response text,
.nyx-settings-text {
  background: transparent;
  color: #ece8dd;
}

.nyx-section-title {
  font-size: 12pt;
  font-weight: 800;
  letter-spacing: 0.08em;
  color: #f3d7b1;
}

.nyx-sidebar-toolbar {
  padding-bottom: 4px;
}

.nyx-session-row {
  border-radius: 14px;
  padding: 10px 11px;
  background: rgba(255, 255, 255, 0.015);
}

.nyx-session-row:selected,
list row:selected .nyx-session-row {
  background:
    linear-gradient(180deg, rgba(242, 165, 95, 0.16), rgba(242, 165, 95, 0.07));
  border: 1px solid rgba(242, 165, 95, 0.24);
}

.nyx-session-title {
  font-weight: 700;
}

.nyx-session-subtitle {
  opacity: 0.68;
  font-size: 9.5pt;
}

.nyx-session-preview {
  opacity: 0.9;
  font-size: 10pt;
}

.nyx-settings-root {
  padding: 4px 2px 8px 2px;
}

.nyx-settings-grid {
  background: rgba(255, 255, 255, 0.025);
  border-radius: 14px;
  padding: 12px;
}

.nyx-settings-label {
  font-weight: 650;
}

.nyx-settings-help {
  opacity: 0.76;
}

.nyx-settings-status {
  color: #f3d7b1;
}
"""


def install_ui_css(font: str = "monospace 11") -> None:
    """Install the shared Nyx CSS provider on the default display."""

    provider = Gtk.CssProvider()
    safe_font = _normalize_font_value(font).replace("\\", "\\\\").replace('"', '\\"')
    provider.load_from_string(UI_CSS.replace("__NYX_FONT__", safe_font))
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


def _normalize_font_value(font: str) -> str:
    """Convert the user font setting into a CSS-compatible font value."""

    parts = font.split()
    if len(parts) >= 2 and parts[-1].isdigit():
        family = " ".join(parts[:-1])
        return f"{parts[-1]}pt \"{family}\""
    return font
