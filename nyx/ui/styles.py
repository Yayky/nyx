"""Shared GTK CSS helpers for Nyx overlay windows."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gtk

UI_CSS = """
.nyx-overlay-window {
  background-color: alpha(@window_bg_color, 0.96);
  border: 1px solid alpha(@borders, 0.75);
  border-radius: 18px;
  padding: 16px;
  box-shadow: 0 18px 48px alpha(black, 0.18);
}

.nyx-status-label {
  font-weight: 600;
}

.nyx-chip {
  background-color: alpha(@accent_bg_color, 0.12);
  border-radius: 999px;
  padding: 4px 10px;
}

.nyx-response,
.nyx-prompt,
.nyx-sidebar {
  border-radius: 12px;
  background-color: alpha(@view_bg_color, 0.55);
  padding: 10px;
}

.nyx-section-title {
  font-weight: 700;
  letter-spacing: 0.04em;
}

.nyx-session-row {
  border-radius: 12px;
  padding: 8px;
}

.nyx-session-title {
  font-weight: 600;
}

.nyx-session-subtitle {
  opacity: 0.75;
}

.nyx-session-preview {
  opacity: 0.85;
}
"""


def install_ui_css() -> None:
    """Install the shared Nyx CSS provider on the default display."""

    provider = Gtk.CssProvider()
    provider.load_from_string(UI_CSS)
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
