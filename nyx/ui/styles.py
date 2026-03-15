"""Shared GTK CSS helpers for Nyx overlay windows."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gtk

from nyx.ui.theme import ResolvedTheme

UI_CSS = """
window.nyx-window {
  background: transparent;
}

window {
  color: __TEXT_PRIMARY__;
  font: __NYX_FONT__;
}

.nyx-stage {
  background: alpha(__BG_OUTER__, 0.56);
  background-image: __STAGE_BACKGROUND_IMAGE__;
  background-size: cover;
  background-position: center;
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.54);
  border-radius: 24px;
  box-shadow: 0 28px 96px alpha(__SHADOW_COLOR__, 0.74);
}

.nyx-stage-compact {
  padding: 10px 12px 12px 12px;
}

.nyx-stage-panel {
  padding: 10px;
}

.nyx-history-pane,
.nyx-settings-pane,
.nyx-thread-pane {
  background: alpha(__BG_PANEL__, 0.70);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.30);
  border-radius: 18px;
}

.nyx-history-pane,
.nyx-settings-pane {
  padding: 14px 14px 12px 14px;
}

.nyx-thread-pane {
  padding: 12px 14px 10px 14px;
}

.nyx-status-strip {
  background: transparent;
  border: none;
  padding: 2px 2px 6px 2px;
}

.nyx-popup-card,
.nyx-popup-composer {
  background: alpha(__BG_PANEL__, 0.52);
  border-radius: 16px;
  padding: 8px 10px;
}

.nyx-popup-card {
  border: 1px solid alpha(__BORDER_SOFT__, 0.58);
  box-shadow: inset 0 0 0 1px alpha(__ACCENT_COOL__, 0.08);
}

.nyx-popup-composer {
  border: 1px solid alpha(__BORDER_SOFT__, 0.72);
  box-shadow: inset 0 0 0 1px alpha(__ACCENT_COOL__, 0.06);
  padding: 6px 10px;
}

.nyx-popup-response-wrap {
  min-height: 128px;
}

.nyx-popup-response,
.nyx-popup-input,
.nyx-thread-view text,
.nyx-settings-text {
  background: transparent;
  color: __TEXT_PRIMARY__;
}

.nyx-popup-response text,
.nyx-popup-input text {
  background: transparent;
  color: __TEXT_PRIMARY__;
}

.nyx-popup-response,
.nyx-thread-view {
  min-height: 0;
}

.nyx-popup-input {
  min-height: 44px;
}

.nyx-thread-view text {
  line-height: 1.3;
}

.nyx-icon-button,
.nyx-rail button {
  min-width: 42px;
  min-height: 42px;
  border-radius: 999px;
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.40);
  background: alpha(__BG_CARD_ALT__, 0.54);
  box-shadow: inset 0 0 0 1px alpha(__ACCENT_COOL__, 0.05);
}

.nyx-icon-button.recording {
  border-color: alpha(__BORDER_SOFT__, 0.9);
  background: alpha(__ACCENT_WARM__, 0.18);
}

.nyx-chip {
  background: alpha(__BG_CARD_ALT__, 0.42);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.22);
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 9.5pt;
}

.nyx-hint {
  color: alpha(__TEXT_MUTED__, 0.88);
  font-size: 8.75pt;
}

.nyx-rail {
  background: alpha(__BG_PANEL__, 0.46);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.18);
  border-radius: 18px;
  padding: 8px 8px;
}

.nyx-rail button.active,
.nyx-rail button:hover,
.nyx-history-row:selected,
list row:selected .nyx-history-row {
  background: alpha(__BG_CARD_ALT__, 0.64);
  border: 1px solid alpha(__BORDER_SOFT__, 0.44);
}

.nyx-history-row {
  border-radius: 14px;
  padding: 10px 12px;
  background: alpha(__BG_CARD__, 0.18);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.10);
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

.nyx-sidebar-title {
  font-size: 15pt;
  font-weight: 800;
}

.nyx-sidebar-copy {
  color: alpha(__TEXT_MUTED__, 0.90);
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

.nyx-thread-pane .nyx-composer {
  background: transparent;
  border: none;
  border-top: 1px solid alpha(__BORDER_PRIMARY__, 0.18);
  border-radius: 0;
  padding: 12px 0 0 0;
}

.nyx-thread-pane .nyx-popup-input {
  background: alpha(__BG_CARD_ALT__, 0.22);
  border-radius: 14px;
}
"""


def install_ui_css(theme: ResolvedTheme, font: str = "monospace 12") -> None:
    """Install the shared Nyx CSS provider on the default display."""

    provider = Gtk.CssProvider()
    safe_font = _normalize_font_value(font).replace("\\", "\\\\").replace('"', '\\"')
    backdrop_image = "none"
    if theme.backdrop_path is not None and theme.backdrop_path.exists():
        backdrop_image = f'linear-gradient(alpha({theme.colors["bg_outer"]}, 0.34), alpha({theme.colors["bg_outer"]}, 0.34)), url("{theme.backdrop_path.as_uri()}")'
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
        .replace("__STAGE_BACKGROUND_IMAGE__", backdrop_image)
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
