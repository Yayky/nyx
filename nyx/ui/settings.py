"""GTK settings surface for editing Nyx configuration from the overlay UI."""

from __future__ import annotations

import copy
import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from nyx.config import NyxConfig, load_config, load_config_text, render_config_toml, save_config_text


class NyxSettingsEditor(Gtk.Box):
    """Editable settings surface with quick controls plus raw TOML editing."""

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
        self.add_css_class("nyx-settings-pane")
        self._build_layout()
        self._populate_controls_from_config()
        self._load_editor_text()

    def _build_layout(self) -> None:
        """Create the settings header, quick controls, and raw TOML editor."""

        heading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.append(heading_box)

        title = Gtk.Label(label="Settings", xalign=0.0)
        title.add_css_class("nyx-section-title")
        heading_box.append(title)

        subtitle = Gtk.Label(
            label=(
                "Most runtime settings apply immediately. Typography, monitor placement, and some "
                "visual theme changes may need the overlay to be reopened for a full refresh."
            ),
            xalign=0.0,
        )
        subtitle.set_wrap(True)
        subtitle.add_css_class("nyx-settings-help")
        heading_box.append(subtitle)

        self.quick_settings = Gtk.Grid(column_spacing=12, row_spacing=10)
        self.quick_settings.add_css_class("nyx-settings-grid")
        self.append(self.quick_settings)

        self.default_model_entry = self._labeled_entry("Default Provider", 0, 0)
        self.overlay_monitor_entry = self._labeled_entry("Overlay Monitor", 1, 0)
        self.hotkey_entry = self._labeled_entry("Summon Hotkey", 2, 0)
        self.wallpaper_entry = self._labeled_entry("Wallpaper Path", 3, 0)
        self.font_entry = self._labeled_entry("Font", 4, 0)

        self.theme_mode_entry = self._labeled_entry("Theme Mode", 0, 2)
        self.searxng_entry = self._labeled_entry("SearXNG URL", 1, 2)
        self.history_backend_entry = self._labeled_entry("History Backend", 2, 2)
        self.blur_radius_entry = self._labeled_entry("Backdrop Blur", 3, 2)
        self.dim_opacity_entry = self._labeled_entry("Backdrop Dim", 4, 2)

        self.voice_switch = self._labeled_switch("Voice Enabled", 5, 0)
        self.backdrop_switch = self._labeled_switch("Backdrop Enabled", 5, 2)
        self.yolo_switch = self._labeled_switch("YOLO", 6, 0)
        self.confirm_switch = self._labeled_switch("Confirm Destructive", 6, 2)
        self.auto_sort_switch = self._labeled_switch("Auto Sort Notes", 7, 0)

        theme_title = Gtk.Label(label="Theme Overrides", xalign=0.0)
        theme_title.add_css_class("nyx-section-title")
        self.append(theme_title)

        self.theme_grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        self.theme_grid.add_css_class("nyx-settings-grid")
        self.append(self.theme_grid)

        self.theme_entries = {
            "accent_cool": self._theme_entry("Accent Cool", 0),
            "accent_warm": self._theme_entry("Accent Warm", 1),
            "border_primary": self._theme_entry("Border Primary", 2),
            "text_primary": self._theme_entry("Text Primary", 3),
            "text_muted": self._theme_entry("Text Muted", 4),
            "bg_panel": self._theme_entry("Panel Background", 5),
            "bg_card": self._theme_entry("Card Background", 6),
            "shadow_color": self._theme_entry("Shadow Color", 7),
        }

        hotkey_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        hotkey_box.add_css_class("nyx-inner-card-alt")
        self.append(hotkey_box)

        hotkey_title = Gtk.Label(label="Hyprland Setup", xalign=0.0)
        hotkey_title.add_css_class("nyx-section-title")
        hotkey_box.append(hotkey_title)

        self.hotkey_snippet = Gtk.Label(xalign=0.0)
        self.hotkey_snippet.set_wrap(True)
        self.hotkey_snippet.set_selectable(True)
        self.hotkey_snippet.add_css_class("nyx-metadata")
        hotkey_box.append(self.hotkey_snippet)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.append(button_row)

        apply_button = Gtk.Button(label="Apply Quick Settings")
        apply_button.add_css_class("nyx-button-strong")
        apply_button.connect("clicked", self._on_apply_quick_settings_clicked)
        button_row.append(apply_button)

        save_button = Gtk.Button(label="Save Config")
        save_button.add_css_class("nyx-button-soft")
        save_button.connect("clicked", self._on_save_clicked)
        button_row.append(save_button)

        reload_button = Gtk.Button(label="Reload From Disk")
        reload_button.add_css_class("nyx-button-soft")
        reload_button.connect("clicked", self._on_reload_clicked)
        button_row.append(reload_button)

        self.status_label = Gtk.Label(xalign=0.0)
        self.status_label.add_css_class("nyx-settings-status")
        self.append(self.status_label)

        editor_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        editor_frame.add_css_class("nyx-inner-card")
        self.append(editor_frame)

        editor_label = Gtk.Label(label="Advanced TOML Editor", xalign=0.0)
        editor_label.add_css_class("nyx-section-title")
        editor_frame.append(editor_label)

        helper = Gtk.Label(
            label=(
                "Use the advanced editor for full provider arrays, theme overrides, fallback chains, and any "
                "new settings not represented by the quick controls."
            ),
            xalign=0.0,
        )
        helper.set_wrap(True)
        helper.add_css_class("nyx-settings-help")
        editor_frame.append(helper)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        editor_frame.append(scroll)

        self.editor_view = Gtk.TextView()
        self.editor_view.set_monospace(True)
        self.editor_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.editor_view.add_css_class("nyx-settings-text")
        scroll.set_child(self.editor_view)

    def _labeled_entry(self, label_text: str, row: int, column: int) -> Gtk.Entry:
        """Create one labeled text entry in the quick-settings grid."""

        label = Gtk.Label(label=label_text, xalign=0.0)
        label.add_css_class("nyx-settings-label")
        self.quick_settings.attach(label, column, row, 1, 1)
        entry = Gtk.Entry()
        self.quick_settings.attach(entry, column + 1, row, 1, 1)
        return entry

    def _labeled_switch(self, label_text: str, row: int, column: int) -> Gtk.Switch:
        """Create one labeled switch in the quick-settings grid."""

        label = Gtk.Label(label=label_text, xalign=0.0)
        label.add_css_class("nyx-settings-label")
        self.quick_settings.attach(label, column, row, 1, 1)
        toggle = Gtk.Switch()
        toggle.set_halign(Gtk.Align.START)
        self.quick_settings.attach(toggle, column + 1, row, 1, 1)
        return toggle

    def _theme_entry(self, label_text: str, row: int) -> Gtk.Entry:
        """Create one labeled theme override entry."""

        label = Gtk.Label(label=label_text, xalign=0.0)
        label.add_css_class("nyx-settings-label")
        self.theme_grid.attach(label, 0 if row < 4 else 2, row % 4, 1, 1)
        entry = Gtk.Entry()
        self.theme_grid.attach(entry, 1 if row < 4 else 3, row % 4, 1, 1)
        return entry

    def _populate_controls_from_config(self) -> None:
        """Populate quick-setting widgets from the current config object."""

        self.default_model_entry.set_text(self.config.models.default)
        self.overlay_monitor_entry.set_text(self.config.ui.overlay_monitor)
        self.hotkey_entry.set_text(self.config.ui.summon_hotkey)
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

    def _on_apply_quick_settings_clicked(self, button: Gtk.Button) -> None:
        """Merge quick-setting widgets into a draft config and render TOML."""

        del button
        draft = copy.deepcopy(self.config)
        draft.models.default = self.default_model_entry.get_text().strip() or draft.models.default
        draft.ui.overlay_monitor = self.overlay_monitor_entry.get_text().strip() or draft.ui.overlay_monitor
        draft.ui.summon_hotkey = self.hotkey_entry.get_text().strip() or draft.ui.summon_hotkey
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
        draft.ui.backdrop_blur_radius = _safe_int(
            self.blur_radius_entry.get_text(),
            draft.ui.backdrop_blur_radius,
        )
        draft.ui.backdrop_dim_opacity = _safe_float(
            self.dim_opacity_entry.get_text(),
            draft.ui.backdrop_dim_opacity,
        )
        for key, entry in self.theme_entries.items():
            setattr(draft.ui.theme, key, entry.get_text().strip())
        self.editor_view.get_buffer().set_text(render_config_toml(draft))
        self.status_label.set_label("Quick settings copied into the TOML editor. Save to persist them.")

    def _on_save_clicked(self, button: Gtk.Button) -> None:
        """Validate and save the current TOML editor contents."""

        del button
        buffer = self.editor_view.get_buffer()
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        config_text = buffer.get_text(start, end, True)
        try:
            new_config = save_config_text(config_text, self.config.config_path)
        except Exception as exc:
            self.logger.exception("Failed to save config from settings editor.")
            self.status_label.set_label(f"Config save failed: {exc}")
            return

        self.config = new_config
        self._populate_controls_from_config()
        self._load_editor_text()
        self.status_label.set_label(
            "Settings saved. Runtime changes apply immediately where possible; some visual changes may need reopening the overlay."
        )
        self.on_config_saved(new_config)

    def _on_reload_clicked(self, button: Gtk.Button) -> None:
        """Reload the config from disk, discarding editor changes."""

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
