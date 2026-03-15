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
  background: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
  padding: 0;
}

.nyx-stage-panel {
  background: alpha(__BG_OUTER__, 0.16);
  border-color: alpha(__BORDER_PRIMARY__, 0.18);
  box-shadow: 0 20px 72px alpha(__SHADOW_COLOR__, 0.34);
  padding: 6px;
}

.nyx-history-pane,
.nyx-settings-pane,
.nyx-thread-pane {
  background: alpha(__BG_PANEL__, 0.24);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.18);
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
  padding: 0 0 8px 0;
}

.nyx-popup-card,
.nyx-popup-composer {
  background: alpha(__BG_PANEL__, 0.44);
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
.nyx-thread-view,
.nyx-thread-view text,
.nyx-settings-text,
.nyx-settings-text text {
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

.nyx-popup-input,
.nyx-thread-view,
.nyx-settings-text,
entry,
searchentry > text,
searchentry > image {
  color: __TEXT_PRIMARY__;
}

.nyx-popup-input,
.nyx-thread-view,
.nyx-settings-text,
entry,
searchentry {
  background: alpha(__BG_CARD_ALT__, 0.16);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.18);
  border-radius: 14px;
}

.nyx-thread-view text {
  line-height: 1.3;
}

.nyx-icon-button,
.nyx-panel-row-button {
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
  background: alpha(__BG_CARD_ALT__, 0.28);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.18);
  border-radius: 999px;
  padding: 3px 9px;
  font-size: 9pt;
}

.nyx-hint {
  color: alpha(__TEXT_MUTED__, 0.88);
  font-size: 8.75pt;
}

.nyx-rail {
  background: alpha(__BG_PANEL__, 0.46);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.18);
  border-radius: 18px;
  padding: 6px 6px;
}

.nyx-rail button {
  min-width: 34px;
  min-height: 34px;
  border-radius: 999px;
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.34);
  background: alpha(__BG_CARD_ALT__, 0.36);
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
  padding: 8px 10px;
  background: alpha(__BG_CARD__, 0.12);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.10);
}

.nyx-history-delete {
  min-width: 28px;
  min-height: 28px;
  border-radius: 999px;
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.26);
  background: alpha(__BG_CARD_ALT__, 0.34);
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

.nyx-status-meta {
  color: alpha(__TEXT_MUTED__, 0.92);
  font-size: 9.25pt;
}

.nyx-status-copy {
  min-width: 30px;
  min-height: 30px;
}

.nyx-session-list,
.nyx-session-list row,
.nyx-session-list row:selected,
scrolledwindow,
viewport {
  background: transparent;
}

textview.nyx-popup-response,
textview.nyx-popup-response text,
textview.nyx-popup-response border,
textview.nyx-popup-input,
textview.nyx-popup-input text,
textview.nyx-popup-input border,
textview.nyx-thread-view,
textview.nyx-thread-view text,
textview.nyx-thread-view border,
textview.nyx-settings-text,
textview.nyx-settings-text text,
textview.nyx-settings-text border {
  background: transparent;
}

.nyx-history-pane label,
.nyx-thread-pane label,
.nyx-settings-pane label,
.nyx-settings-pane text {
  color: __TEXT_PRIMARY__;
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

.nyx-settings-section {
  background: alpha(__BG_CARD__, 0.16);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.12);
  border-radius: 16px;
  padding: 14px;
}

.nyx-settings-codeblock {
  background: alpha(__BG_CARD_ALT__, 0.16);
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.14);
  border-radius: 14px;
  padding: 10px 12px;
}

.nyx-settings-actions {
  margin-top: 4px;
}

.nyx-settings-switcher button {
  border-radius: 999px;
  border: 1px solid alpha(__BORDER_PRIMARY__, 0.18);
  background: alpha(__BG_CARD_ALT__, 0.18);
  padding: 5px 14px;
}

.nyx-settings-switcher button:checked,
.nyx-settings-switcher button:hover {
  background: alpha(__BG_CARD_ALT__, 0.40);
  border-color: alpha(__BORDER_SOFT__, 0.32);
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

.nyx-composer-dock {
  background: alpha(__BG_CARD_ALT__, 0.10);
  border-radius: 16px;
}

.nyx-composer-footer {
  background: transparent;
}

.nyx-thread-pane .nyx-popup-input {
  background: alpha(__BG_CARD_ALT__, 0.14);
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
