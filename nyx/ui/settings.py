"""GTK settings surface for editing Nyx configuration from the overlay UI."""

from __future__ import annotations

import copy
import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from nyx.config import (
    NyxConfig,
    compute_panel_total_width,
    load_config,
    load_config_text,
    render_config_toml,
    save_config_text,
)


class NyxSettingsEditor(Gtk.Box):
    """Editable settings surface with Basic and Advanced tabs."""

    def __init__(
        self,
        config: NyxConfig,
        logger: logging.Logger,
        on_config_saved,
    ) -> None:
        """Build the settings editor for the supplied Nyx config."""

        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.config = config
        self.logger = logger
        self.on_config_saved = on_config_saved
        self._build_layout()
        self._populate_controls_from_config()
        self._load_editor_text()

    def _build_layout(self) -> None:
        """Create the settings header, switcher, tabs, and save status."""

        heading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.append(heading_box)

        title = Gtk.Label(label="Settings", xalign=0.0)
        title.add_css_class("nyx-section-title")
        title.add_css_class("nyx-sidebar-title")
        heading_box.append(title)

        subtitle = Gtk.Label(
            label=(
                "Basic settings apply the common options directly. Advanced mode exposes the raw TOML "
                "for full provider arrays, fallback chains, and any lower-level overrides."
            ),
            xalign=0.0,
        )
        subtitle.set_wrap(True)
        subtitle.add_css_class("nyx-sidebar-copy")
        heading_box.append(subtitle)

        switcher_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.append(switcher_row)

        self.tab_stack = Gtk.Stack()
        self.tab_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.tab_stack.set_hexpand(True)
        self.tab_stack.set_vexpand(True)

        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.tab_stack)
        switcher.add_css_class("nyx-settings-switcher")
        switcher_row.append(switcher)

        self.append(self.tab_stack)

        self.status_label = Gtk.Label(xalign=0.0)
        self.status_label.add_css_class("nyx-settings-status")
        self.append(self.status_label)

        self._build_basic_page()
        self._build_advanced_page()

    def _build_basic_page(self) -> None:
        """Create the structured Basic settings page."""

        basic_scroll = Gtk.ScrolledWindow()
        basic_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        basic_scroll.set_vexpand(True)
        self.tab_stack.add_titled(basic_scroll, "basic", "Basic")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_margin_bottom(6)
        basic_scroll.set_child(content)

        appearance = self._section(
            "Appearance",
            "Tune the wallpaper-driven look, glass depth, and text contrast used by the sidebar.",
        )
        content.append(appearance)

        self.wallpaper_entry = self._section_entry(appearance, "Wallpaper Path")
        self.theme_mode_entry = self._section_entry(appearance, "Theme Mode")
        self.font_entry = self._section_entry(appearance, "Font")
        self.blur_radius_entry = self._section_entry(appearance, "Backdrop Blur")
        self.dim_opacity_entry = self._section_entry(appearance, "Backdrop Dim")
        self.backdrop_switch = self._section_switch(appearance, "Backdrop Enabled")

        theme_section = self._section(
            "Theme Overrides",
            "These colors override the wallpaper-derived palette when set.",
        )
        content.append(theme_section)
        self.theme_entries = {
            "text_primary": self._section_entry(theme_section, "Text Primary"),
            "text_muted": self._section_entry(theme_section, "Text Muted"),
            "accent_cool": self._section_entry(theme_section, "Accent Cool"),
            "accent_warm": self._section_entry(theme_section, "Accent Warm"),
            "border_primary": self._section_entry(theme_section, "Border Primary"),
            "border_soft": self._section_entry(theme_section, "Border Soft"),
            "bg_panel": self._section_entry(theme_section, "Panel Background"),
            "bg_card": self._section_entry(theme_section, "Card Background"),
            "shadow_color": self._section_entry(theme_section, "Shadow Color"),
        }

        overlay = self._section(
            "Overlay",
            "Placement and startup integration for the launcher and panel.",
        )
        content.append(overlay)

        self.overlay_monitor_entry = self._section_entry(overlay, "Overlay Monitor")
        self.hotkey_entry = self._section_entry(overlay, "Summon Hotkey")
        self.launcher_width_entry = self._section_entry(overlay, "Popup Width (px)")
        self.launcher_height_entry = self._section_entry(overlay, "Popup Height (px)")
        self.sidebar_height_entry = self._section_entry(overlay, "Sidebar Height (px)")
        self.history_width_entry = self._section_entry(overlay, "History/Settings Width (px)")
        self.chat_width_entry = self._section_entry(overlay, "Chat Width (px)")
        self.conversation_ratio_entry = self._section_entry(overlay, "Conversation Height Ratio")
        self.computed_sidebar_width_label = self._section_value(overlay, "Computed Sidebar Width")

        overlay_note = Gtk.Label(
            label="Sidebar width is derived from the history/settings width and chat width. Long content wraps instead of widening the panel.",
            xalign=0.0,
        )
        overlay_note.set_wrap(True)
        overlay_note.add_css_class("nyx-settings-help")
        overlay.append(overlay_note)

        hyprland_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        hyprland_box.add_css_class("nyx-settings-codeblock")
        overlay.append(hyprland_box)

        snippet_title = Gtk.Label(label="Hyprland Setup", xalign=0.0)
        snippet_title.add_css_class("nyx-settings-label")
        hyprland_box.append(snippet_title)

        self.hotkey_snippet = Gtk.Label(xalign=0.0)
        self.hotkey_snippet.set_wrap(True)
        self.hotkey_snippet.set_selectable(True)
        self.hotkey_snippet.add_css_class("nyx-metadata")
        hyprland_box.append(self.hotkey_snippet)

        behavior = self._section(
            "Behavior",
            "Common runtime options used most often from the sidebar.",
        )
        content.append(behavior)

        self.default_model_entry = self._section_entry(behavior, "Default Provider")
        self.searxng_entry = self._section_entry(behavior, "SearXNG URL")
        self.history_backend_entry = self._section_entry(behavior, "History Backend")
        self.voice_switch = self._section_switch(behavior, "Voice Enabled")
        self.yolo_switch = self._section_switch(behavior, "YOLO")
        self.confirm_switch = self._section_switch(behavior, "Confirm Destructive")
        self.auto_sort_switch = self._section_switch(behavior, "Auto Sort Notes")

        basic_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        basic_actions.add_css_class("nyx-settings-actions")
        content.append(basic_actions)

        save_button = Gtk.Button(label="Save Settings")
        save_button.add_css_class("nyx-button-strong")
        save_button.connect("clicked", self._on_save_basic_clicked)
        basic_actions.append(save_button)

        reload_button = Gtk.Button(label="Reload From Disk")
        reload_button.add_css_class("nyx-button-soft")
        reload_button.connect("clicked", self._on_reload_clicked)
        basic_actions.append(reload_button)

    def _build_advanced_page(self) -> None:
        """Create the Advanced TOML editor page."""

        advanced_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        advanced_box.set_vexpand(True)
        self.tab_stack.add_titled(advanced_box, "advanced", "Advanced")

        helper = Gtk.Label(
            label=(
                "Use Advanced mode for full provider arrays, fallback chains, and any settings not surfaced "
                "in the Basic tab."
            ),
            xalign=0.0,
        )
        helper.set_wrap(True)
        helper.add_css_class("nyx-settings-help")
        advanced_box.append(helper)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        action_row.add_css_class("nyx-settings-actions")
        advanced_box.append(action_row)

        save_button = Gtk.Button(label="Save Advanced")
        save_button.add_css_class("nyx-button-strong")
        save_button.connect("clicked", self._on_save_advanced_clicked)
        action_row.append(save_button)

        reload_button = Gtk.Button(label="Reload From Disk")
        reload_button.add_css_class("nyx-button-soft")
        reload_button.connect("clicked", self._on_reload_clicked)
        action_row.append(reload_button)

        editor_scroll = Gtk.ScrolledWindow()
        editor_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        editor_scroll.set_vexpand(True)
        advanced_box.append(editor_scroll)

        self.editor_view = Gtk.TextView()
        self.editor_view.set_monospace(True)
        self.editor_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.editor_view.add_css_class("nyx-settings-text")
        editor_scroll.set_child(self.editor_view)

    def _section(self, title: str, description: str) -> Gtk.Box:
        """Create one styled settings section."""

        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        section.add_css_class("nyx-settings-section")

        heading = Gtk.Label(label=title, xalign=0.0)
        heading.add_css_class("nyx-section-title")
        section.append(heading)

        body = Gtk.Label(label=description, xalign=0.0)
        body.set_wrap(True)
        body.add_css_class("nyx-settings-help")
        section.append(body)
        return section

    def _section_entry(self, section: Gtk.Box, label_text: str) -> Gtk.Entry:
        """Append one labeled full-width entry row to a settings section."""

        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        section.append(row)

        label = Gtk.Label(label=label_text, xalign=0.0)
        label.add_css_class("nyx-settings-label")
        row.append(label)

        entry = Gtk.Entry()
        entry.set_hexpand(True)
        row.append(entry)
        return entry

    def _section_switch(self, section: Gtk.Box, label_text: str) -> Gtk.Switch:
        """Append one labeled toggle row to a settings section."""

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        section.append(row)

        label = Gtk.Label(label=label_text, xalign=0.0)
        label.set_hexpand(True)
        label.add_css_class("nyx-settings-label")
        row.append(label)

        toggle = Gtk.Switch()
        toggle.set_halign(Gtk.Align.END)
        row.append(toggle)
        return toggle

    def _section_value(self, section: Gtk.Box, label_text: str) -> Gtk.Label:
        """Append one labeled read-only value row to a settings section."""

        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        section.append(row)

        label = Gtk.Label(label=label_text, xalign=0.0)
        label.add_css_class("nyx-settings-label")
        row.append(label)

        value = Gtk.Label(xalign=0.0)
        value.set_wrap(True)
        value.add_css_class("nyx-metadata")
        row.append(value)
        return value

    def _populate_controls_from_config(self) -> None:
        """Populate structured controls from the active config object."""

        self.default_model_entry.set_text(self.config.models.default)
        self.overlay_monitor_entry.set_text(self.config.ui.overlay_monitor)
        self.hotkey_entry.set_text(self.config.ui.summon_hotkey)
        self.launcher_width_entry.set_text(str(self.config.ui.launcher_width))
        self.launcher_height_entry.set_text(str(self.config.ui.launcher_height))
        self.sidebar_height_entry.set_text(str(self.config.ui.panel_height))
        self.history_width_entry.set_text(str(self.config.ui.panel_history_width))
        self.chat_width_entry.set_text(str(self.config.ui.panel_chat_width))
        self.conversation_ratio_entry.set_text(str(self.config.ui.panel_conversation_ratio))
        self.computed_sidebar_width_label.set_label(
            f"{compute_panel_total_width(self.config.ui.panel_history_width, self.config.ui.panel_chat_width)} px"
        )
        self.wallpaper_entry.set_text(self.config.ui.wallpaper_path)
        self.font_entry.set_text(self.config.ui.font)
        self.theme_mode_entry.set_text(self.config.ui.theme_mode)
        self.searxng_entry.set_text(self.config.web.searxng_url)
        self.history_backend_entry.set_text(self.config.ui.history.backend)
        self.blur_radius_entry.set_text(str(self.config.ui.backdrop_blur_radius))
        self.dim_opacity_entry.set_text(str(self.config.ui.backdrop_dim_opacity))
        self.voice_switch.set_active(self.config.voice.enabled)
        self.backdrop_switch.set_active(self.config.ui.backdrop_enabled)
        self.yolo_switch.set_active(self.config.system.yolo)
        self.confirm_switch.set_active(self.config.system.confirm_destructive)
        self.auto_sort_switch.set_active(self.config.notes.auto_sort)

        for key, entry in self.theme_entries.items():
            entry.set_text(getattr(self.config.ui.theme, key))

        self.hotkey_snippet.set_label(
            "\n".join(
                [
                    f"exec-once = {self._nyx_command()} --daemon",
                    f"bind = SUPER, A, exec, {self._nyx_command()} --toggle-ui",
                    f"# Current Nyx summon_hotkey setting: {self.config.ui.summon_hotkey}",
                    "# Copy these lines into your Hyprland config, then run `hyprctl reload`.",
                ]
            )
        )

    def _load_editor_text(self) -> None:
        """Load raw TOML text into the advanced editor."""

        self.editor_view.get_buffer().set_text(load_config_text(self.config.config_path))

    def _build_basic_draft(self) -> NyxConfig:
        """Materialize the Basic-tab widget values into a config draft."""

        draft = copy.deepcopy(self.config)
        draft.models.default = self.default_model_entry.get_text().strip() or draft.models.default
        draft.ui.overlay_monitor = self.overlay_monitor_entry.get_text().strip() or draft.ui.overlay_monitor
        draft.ui.summon_hotkey = self.hotkey_entry.get_text().strip() or draft.ui.summon_hotkey
        draft.ui.launcher_width = _safe_int(self.launcher_width_entry.get_text(), draft.ui.launcher_width)
        draft.ui.launcher_height = _safe_int(self.launcher_height_entry.get_text(), draft.ui.launcher_height)
        draft.ui.panel_height = _safe_int(self.sidebar_height_entry.get_text(), draft.ui.panel_height)
        draft.ui.panel_history_width = _safe_int(
            self.history_width_entry.get_text(),
            draft.ui.panel_history_width,
        )
        draft.ui.panel_chat_width = _safe_int(
            self.chat_width_entry.get_text(),
            draft.ui.panel_chat_width,
        )
        draft.ui.panel_conversation_ratio = min(
            0.92,
            max(
                0.35,
                _safe_float(
                    self.conversation_ratio_entry.get_text(),
                    draft.ui.panel_conversation_ratio,
                ),
            ),
        )
        draft.ui.panel_width = compute_panel_total_width(
            draft.ui.panel_history_width,
            draft.ui.panel_chat_width,
        )
        draft.ui.wallpaper_path = self.wallpaper_entry.get_text().strip()
        draft.ui.font = self.font_entry.get_text().strip() or draft.ui.font
        draft.ui.theme_mode = self.theme_mode_entry.get_text().strip() or draft.ui.theme_mode
        draft.ui.history.backend = self.history_backend_entry.get_text().strip() or draft.ui.history.backend
        draft.web.searxng_url = self.searxng_entry.get_text().strip() or draft.web.searxng_url
        draft.voice.enabled = self.voice_switch.get_active()
        draft.ui.backdrop_enabled = self.backdrop_switch.get_active()
        draft.system.yolo = self.yolo_switch.get_active()
        draft.system.confirm_destructive = self.confirm_switch.get_active()
        draft.notes.auto_sort = self.auto_sort_switch.get_active()
        draft.ui.backdrop_blur_radius = _safe_int(self.blur_radius_entry.get_text(), draft.ui.backdrop_blur_radius)
        draft.ui.backdrop_dim_opacity = _safe_float(
            self.dim_opacity_entry.get_text(),
            draft.ui.backdrop_dim_opacity,
        )
        for key, entry in self.theme_entries.items():
            setattr(draft.ui.theme, key, entry.get_text().strip())
        return draft

    def _on_save_basic_clicked(self, button: Gtk.Button) -> None:
        """Validate and persist the Basic settings controls."""

        del button
        try:
            rendered = render_config_toml(self._build_basic_draft())
            new_config = save_config_text(rendered, self.config.config_path)
        except Exception as exc:
            self.logger.exception("Failed to save basic settings.")
            self.status_label.set_label(f"Settings save failed: {exc}")
            return

        self.config = new_config
        self._populate_controls_from_config()
        self._load_editor_text()
        self.status_label.set_label(
            "Basic settings saved. Runtime changes apply immediately where possible; some visual changes may need reopening the overlay."
        )
        self.on_config_saved(new_config)

    def _on_save_advanced_clicked(self, button: Gtk.Button) -> None:
        """Validate and save the raw TOML editor contents."""

        del button
        buffer = self.editor_view.get_buffer()
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        config_text = buffer.get_text(start, end, True)
        try:
            new_config = save_config_text(config_text, self.config.config_path)
        except Exception as exc:
            self.logger.exception("Failed to save config from advanced editor.")
            self.status_label.set_label(f"Advanced save failed: {exc}")
            return

        self.config = new_config
        self._populate_controls_from_config()
        self._load_editor_text()
        self.status_label.set_label(
            "Advanced settings saved. Runtime changes apply immediately where possible; some visual changes may need reopening the overlay."
        )
        self.on_config_saved(new_config)

    def _on_reload_clicked(self, button: Gtk.Button) -> None:
        """Reload the config from disk and repopulate both tabs."""

        del button
        try:
            self.config = load_config(self.config.config_path)
        except Exception as exc:
            self.logger.exception("Failed to reload config into settings editor.")
            self.status_label.set_label(f"Config reload failed: {exc}")
            return

        self._populate_controls_from_config()
        self._load_editor_text()
        self.status_label.set_label("Reloaded settings from disk.")
        self.on_config_saved(self.config)

    def _nyx_command(self) -> str:
        """Return the best-effort absolute nyx command for compositor snippets."""

        scripts_dir = self.config.config_path.parent.parent / "bin"
        venv_script = Path.cwd() / ".venv" / "bin" / "nyx"
        if venv_script.exists():
            return str(venv_script)
        candidate = scripts_dir / "nyx"
        return str(candidate)


def _safe_int(value: str, fallback: int) -> int:
    """Parse an integer from one entry string with fallback."""

    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return fallback


def _safe_float(value: str, fallback: float) -> float:
    """Parse a float from one entry string with fallback."""

    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return fallback
