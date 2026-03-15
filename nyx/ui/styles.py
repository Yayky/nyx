"""Shared GTK CSS helpers for Nyx overlay windows."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gtk

from nyx.ui.theme import ResolvedTheme

UI_CSS = """
window {
  color: __TEXT_PRIMARY__;
  font: __NYX_FONT__;
}

.nyx-stage {
  background: alpha(__BG_OUTER__, 0.72);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.48);
  border-radius: 24px;
  box-shadow: 0 30px 90px alpha(__SHADOW_COLOR__, 0.72);
}

.nyx-stage-compact {
  padding: 14px;
}

.nyx-stage-panel {
  padding: 12px;
}

.nyx-backdrop {
  background-size: cover;
  background-position: center;
  border-radius: 24px;
}

.nyx-shell-card,
.nyx-history-pane,
.nyx-thread-pane,
.nyx-settings-pane,
.nyx-composer,
.nyx-status-strip {
  background: alpha(__BG_PANEL__, 0.78);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.28);
  border-radius: 18px;
}

.nyx-shell-card {
  padding: 16px;
}

.nyx-thread-pane,
.nyx-history-pane,
.nyx-settings-pane {
  padding: 14px;
}

.nyx-composer {
  padding: 10px 12px;
}

.nyx-status-strip {
  padding: 6px 10px;
}

.nyx-inner-card {
  background: alpha(__BG_CARD__, 0.80);
  border: 1px solid alpha(__BORDER_SOFT__, 0.34);
  border-radius: 14px;
  padding: 12px;
}

.nyx-inner-card-alt {
  background: alpha(__BG_CARD_ALT__, 0.84);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.24);
  border-radius: 14px;
  padding: 12px;
}

.nyx-popup-response text,
.nyx-popup-input text,
.nyx-thread-view text,
.nyx-settings-text {
  background: transparent;
  color: __TEXT_PRIMARY__;
}

.nyx-thread-view text {
}

.nyx-popup-response,
.nyx-popup-input,
.nyx-thread-view {
  min-height: 72px;
}

.nyx-icon-button {
  min-width: 36px;
  min-height: 36px;
  border-radius: 999px;
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.34);
  background: alpha(__BG_CARD_ALT__, 0.74);
}

.nyx-icon-button.recording {
  border-color: alpha(__BORDER_SOFT__, 0.9);
  background: alpha(__ACCENT_WARM__, 0.16);
}

.nyx-chip {
  background: alpha(__BG_CARD_ALT__, 0.78);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.26);
  border-radius: 999px;
  padding: 4px 8px;
}

.nyx-hint {
  color: alpha(__TEXT_MUTED__, 0.92);
  font-size: 9.5pt;
}

.nyx-rail {
  background: alpha(__BG_PANEL__, 0.72);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.26);
  border-radius: 18px;
  padding: 8px 6px;
}

.nyx-rail button {
  min-width: 36px;
  min-height: 36px;
  border-radius: 12px;
  border: 1px solid transparent;
  background: transparent;
}

.nyx-rail button.active,
.nyx-rail button:hover,
.nyx-history-row:selected,
list row:selected .nyx-history-row {
  background: alpha(__BG_CARD_ALT__, 0.78);
  border: 1px solid alpha(__BORDER_SOFT__, 0.34);
}

.nyx-history-row {
  border-radius: 14px;
  padding: 10px;
  background: alpha(__BG_CARD__, 0.32);
}

.nyx-history-title {
  font-weight: 800;
  color: __TEXT_PRIMARY__;
}

.nyx-history-subtitle {
  color: __TEXT_MUTED__;
  font-size: 9.5pt;
}

.nyx-history-preview {
  color: alpha(__TEXT_PRIMARY__, 0.9);
}

.nyx-section-title {
  font-weight: 800;
  letter-spacing: 0.08em;
  color: __ACCENT_COOL__;
}

.nyx-settings-grid {
  background: alpha(__BG_CARD__, 0.48);
  border-radius: 14px;
  padding: 12px;
}

.nyx-settings-label {
  font-weight: 700;
}

.nyx-settings-help,
.nyx-metadata {
  color: __TEXT_MUTED__;
}

.nyx-settings-status {
  color: __ACCENT_WARM__;
}

.nyx-button-soft {
  border-radius: 12px;
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.28);
  background: alpha(__BG_CARD_ALT__, 0.68);
  padding: 6px 10px;
}

.nyx-button-strong {
  border-radius: 12px;
  border: 1px solid alpha(__BORDER_SOFT__, 0.42);
  background: alpha(__ACCENT_WARM__, 0.18);
  padding: 6px 10px;
}
"""


def install_ui_css(theme: ResolvedTheme, font: str = "monospace 12") -> None:
    """Install the shared Nyx CSS provider on the default display."""

    provider = Gtk.CssProvider()
    safe_font = _normalize_font_value(font).replace("\\", "\\\\").replace('"', '\\"')
    css = (
        UI_CSS.replace("__NYX_FONT__", safe_font)
        .replace("__TEXT_PRIMARY__", theme.colors["text_primary"])
        .replace("__TEXT_MUTED__", theme.colors["text_muted"])
        .replace("__ACCENT_COOL__", theme.colors["accent_cool"])
        .replace("__ACCENT_WARM__", theme.colors["accent_warm"])
        .replace("__BORDER_PRIMARY__", theme.colors["border_primary"])
        .replace("__BORDER_SOFT__", theme.colors["border_soft"])
        .replace("__BG_OUTER__", theme.colors["bg_outer"])
        .replace("__BG_PANEL__", theme.colors["bg_panel"])
        .replace("__BG_CARD__", theme.colors["bg_card"])
        .replace("__BG_CARD_ALT__", theme.colors["bg_card_alt"])
        .replace("__SHADOW_COLOR__", theme.colors["shadow_color"])
    )
    provider.load_from_string(css)
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
