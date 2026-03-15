"""GTK4 sidebar/panel mode for Nyx."""

from __future__ import annotations

import asyncio
import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell, Pango

from nyx.config import (
    NyxConfig,
    UI_MIN_CHAT_WIDTH,
    UI_MIN_COMPOSER_HEIGHT,
    UI_MIN_CONVERSATION_HEIGHT,
    UI_MIN_HISTORY_WIDTH,
    UI_MIN_PANEL_HEIGHT,
    UI_PANEL_INNER_SPACING,
    UI_PANEL_OUTER_MARGIN,
    UI_PANEL_RAIL_WIDTH,
    compute_panel_total_width,
)
from nyx.ui.monitors import MonitorSelectionState, resolve_overlay_monitor
from nyx.ui.rendering import render_markdown_to_buffer
from nyx.ui.session import OverlaySessionController, OverlayViewState, SessionRecord
from nyx.ui.settings import NyxSettingsEditor
from nyx.ui.theme import ResolvedTheme


class NyxPanelWindow(Gtk.ApplicationWindow):
    """Left-anchored GTK4 history/settings panel."""

    _RAIL_WIDTH = UI_PANEL_RAIL_WIDTH
    _OUTER_MARGIN = UI_PANEL_OUTER_MARGIN
    _INNER_SPACING = UI_PANEL_INNER_SPACING
    _MIN_PANEL_HEIGHT = UI_MIN_PANEL_HEIGHT
    _MIN_HISTORY_WIDTH = UI_MIN_HISTORY_WIDTH
    _MIN_CHAT_WIDTH = UI_MIN_CHAT_WIDTH
    _STATUS_STRIP_HEIGHT = 44
    _THREAD_VERTICAL_CHROME = 92

    def __init__(
        self,
        application: Gtk.Application,
        config: NyxConfig,
        controller: OverlaySessionController,
        logger: logging.Logger,
        monitor_state: MonitorSelectionState,
        theme: ResolvedTheme,
    ) -> None:
        """Initialize the panel window and its sidebar/main content widgets."""

        super().__init__(application=application)
        self.config = config
        self.controller = controller
        self.logger = logger
        self.monitor_state = monitor_state
        self.theme = theme
        self._last_response_text = ""
        self._submission_task: asyncio.Task[None] | None = None
        self._renaming_session_id: str | None = None

        self.set_title("Nyx")
        self.set_resizable(False)
        self.set_decorated(False)
        self.add_css_class("nyx-window")

        self._configure_layer_shell()
        self._build_layout()
        self._apply_panel_geometry()
        self.refresh_from_controller()

    def refresh_from_controller(self, page_name: str = "history") -> None:
        """Refresh the session list and main state from the controller."""

        self._show_sidebar_page(page_name)
        self._rebuild_session_list()
        state = self.controller.idle_state()
        if self.controller.selected_session_id is not None:
            selected_state = self.controller.state_for_session(self.controller.selected_session_id)
            if selected_state is not None:
                state = selected_state
        self._apply_state(state)
        self._select_session_row(state.selected_session_id)

    def focus_prompt(self) -> None:
        """Focus the main prompt input widget."""

        self.prompt_view.grab_focus()

    def refresh_visuals(self, config: NyxConfig, theme: ResolvedTheme) -> None:
        """Refresh config-bound visual state after settings changes."""

        self.config = config
        self.theme = theme
        self._apply_panel_geometry()
        self.queue_draw()
        self.settings_editor.config = config
        self.settings_editor._populate_controls_from_config()
        self.settings_editor._load_editor_text()

    def set_recording_state(self, recording: bool) -> None:
        """Reflect whether live microphone capture is active."""

        if recording:
            self.voice_button.add_css_class("recording")
        else:
            self.voice_button.remove_css_class("recording")

    def append_prompt_text(self, text: str) -> None:
        """Append text to the current prompt composer."""

        buffer = self.prompt_view.get_buffer()
        end = buffer.get_end_iter()
        if buffer.get_char_count() > 0:
            buffer.insert(end, "\n")
            end = buffer.get_end_iter()
        buffer.insert(end, text)
        self.focus_prompt()

    def _configure_layer_shell(self) -> None:
        """Apply left-sidebar panel layer-shell configuration."""

        Gtk4LayerShell.init_for_window(self)
        Gtk4LayerShell.set_namespace(self, "nyx-panel")
        Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.TOP)
        Gtk4LayerShell.set_keyboard_mode(self, Gtk4LayerShell.KeyboardMode.EXCLUSIVE)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.TOP, True)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.BOTTOM, True)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.LEFT, True)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.RIGHT, False)
        Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.TOP, 12)
        Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.BOTTOM, 12)
        Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.LEFT, 12)

        monitor = self._resolve_monitor()
        if monitor is not None:
            Gtk4LayerShell.set_monitor(self, monitor)

    def _resolve_monitor(self):
        """Resolve the configured monitor selection for the panel window."""

        display = self.get_display()
        return resolve_overlay_monitor(display, self.config.ui.overlay_monitor, self.monitor_state)

    def _build_layout(self) -> None:
        """Create the sidebar and main panel widget tree."""

        stage = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        stage.set_halign(Gtk.Align.START)
        stage.set_valign(Gtk.Align.FILL)
        stage.set_margin_top(10)
        stage.set_margin_bottom(10)
        stage.set_margin_start(10)
        stage.set_margin_end(10)
        stage.add_css_class("nyx-stage")
        stage.add_css_class("nyx-stage-panel")
        self.set_child(stage)
        self.stage = stage

        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        rail.add_css_class("nyx-rail")
        stage.append(rail)
        rail.set_size_request(self._RAIL_WIDTH, -1)
        self.rail = rail

        self.history_button = self._rail_button(
            "view-list-symbolic",
            self._on_history_clicked,
            "Conversations",
        )
        rail.append(self.history_button)
        self.settings_button = self._rail_button(
            "preferences-system-symbolic",
            self._on_settings_clicked,
            "Settings",
        )
        rail.append(self.settings_button)
        self.new_button = self._rail_button(
            "document-new-symbolic",
            self._on_new_conversation_clicked,
            "New conversation",
        )
        rail.append(self.new_button)
        self.collapse_button = self._rail_button(
            "view-restore-symbolic",
            self._on_compact_clicked,
            "Compact mode",
        )
        rail.append(self.collapse_button)
        rail.append(Gtk.Box(vexpand=True))

        left_stack_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        stage.append(left_stack_box)
        self.left_stack_box = left_stack_box

        self.sidebar_stack = Gtk.Stack()
        self.sidebar_stack.set_vexpand(True)
        left_stack_box.append(self.sidebar_stack)

        history_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        history_page.add_css_class("nyx-history-pane")
        self.sidebar_stack.add_titled(history_page, "history", "History")

        history_title = Gtk.Label(label="Conversations", xalign=0.0)
        history_title.add_css_class("nyx-section-title")
        history_title.add_css_class("nyx-sidebar-title")
        history_page.append(history_title)

        history_help = Gtk.Label(
            label="Search, switch, archive, or delete saved Nyx conversations.",
            xalign=0.0,
        )
        history_help.set_wrap(True)
        history_help.add_css_class("nyx-sidebar-copy")
        history_page.append(history_help)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search conversations")
        self.search_entry.connect("search-changed", self._on_search_changed)
        history_page.append(self.search_entry)

        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_vexpand(True)
        history_page.append(sidebar_scroll)

        self.session_list = Gtk.ListBox()
        self.session_list.add_css_class("nyx-session-list")
        self.session_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.session_list.connect("row-selected", self._on_row_selected)
        self.session_list.set_placeholder(Gtk.Label(label="No conversations yet."))
        sidebar_scroll.set_child(self.session_list)

        settings_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        settings_page.add_css_class("nyx-settings-pane")
        settings_scroll = Gtk.ScrolledWindow()
        settings_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        settings_scroll.set_vexpand(True)
        self.settings_editor = NyxSettingsEditor(
            config=self.config,
            logger=self.logger,
            on_config_saved=self._on_config_saved,
        )
        settings_scroll.set_child(self.settings_editor)
        settings_page.append(settings_scroll)
        self.sidebar_stack.add_titled(settings_page, "settings", "Settings")

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.set_hexpand(True)
        stage.append(main_box)
        self.main_box = main_box

        thread_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        thread_pane.add_css_class("nyx-thread-pane")
        thread_pane.set_vexpand(True)
        main_box.append(thread_pane)

        self.status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.status_row.add_css_class("nyx-status-strip")
        thread_pane.append(self.status_row)

        self.provider_label = self._chip_label()
        self.status_row.append(self.provider_label)

        self.status_meta_label = Gtk.Label(xalign=0.0)
        self.status_meta_label.set_hexpand(True)
        self.status_meta_label.set_wrap(True)
        self.status_meta_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.status_meta_label.set_lines(2)
        self.status_meta_label.set_max_width_chars(36)
        self.status_meta_label.add_css_class("nyx-status-meta")
        self.status_row.append(self.status_meta_label)

        self.tokens_label = self._chip_label()
        self.status_row.append(self.tokens_label)

        self.degraded_label = self._chip_label("degraded")
        self.degraded_label.set_visible(False)
        self.status_row.append(self.degraded_label)

        self.window_label = Gtk.Label(xalign=1.0)
        self.window_label.set_wrap(True)
        self.window_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.window_label.set_lines(2)
        self.window_label.set_max_width_chars(18)
        self.window_label.add_css_class("nyx-metadata")
        self.status_row.append(self.window_label)

        copy_button = Gtk.Button()
        copy_button.add_css_class("nyx-icon-button")
        copy_button.add_css_class("nyx-status-copy")
        copy_button.set_child(Gtk.Image.new_from_icon_name("edit-copy-symbolic"))
        copy_button.set_tooltip_text("Copy latest response")
        _enable_instant_tooltip(copy_button)
        copy_button.connect("clicked", lambda button: self._copy_last_response())
        self.status_row.append(copy_button)

        response_scroll = Gtk.ScrolledWindow()
        response_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        response_scroll.set_vexpand(False)
        thread_pane.append(response_scroll)
        self.response_scroll = response_scroll

        self.response_view = Gtk.TextView()
        self.response_view.set_editable(False)
        self.response_view.set_cursor_visible(False)
        self.response_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.response_view.set_monospace(False)
        self.response_view.set_top_margin(8)
        self.response_view.set_bottom_margin(8)
        self.response_view.set_left_margin(8)
        self.response_view.set_right_margin(8)
        self.response_view.add_css_class("nyx-thread-view")
        response_scroll.set_child(self.response_view)

        composer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        composer.add_css_class("nyx-composer")
        composer.add_css_class("nyx-composer-dock")
        thread_pane.append(composer)
        self.composer = composer

        self.prompt_view = Gtk.TextView()
        self.prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.prompt_view.set_monospace(True)
        self.prompt_view.set_top_margin(8)
        self.prompt_view.set_bottom_margin(8)
        self.prompt_view.set_left_margin(10)
        self.prompt_view.set_right_margin(10)
        self.prompt_view.set_size_request(-1, 88)
        self.prompt_view.add_css_class("nyx-popup-input")
        composer.append(self.prompt_view)

        prompt_controller = Gtk.EventControllerKey()
        prompt_controller.connect("key-pressed", self._on_prompt_key_pressed)
        self.prompt_view.add_controller(prompt_controller)

        composer_footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        composer_footer.add_css_class("nyx-composer-footer")
        composer.append(composer_footer)

        hint = Gtk.Label(
            label="Enter to send • Shift+Enter for newline • Esc to close",
            xalign=0.0,
        )
        hint.set_hexpand(True)
        hint.add_css_class("nyx-hint")
        composer_footer.append(hint)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_halign(Gtk.Align.END)
        composer_footer.append(actions)

        self.voice_button = self._icon_button(
            "audio-input-microphone-symbolic",
            self._on_voice_clicked,
            "Voice input",
        )
        actions.append(self.voice_button)

        send_button = self._icon_button("mail-send-symbolic", self._on_send_clicked, "Send")
        actions.append(send_button)

        compact_button = self._icon_button(
            "view-restore-symbolic",
            self._on_compact_clicked,
            "Compact mode",
        )
        actions.append(compact_button)

        window_controller = Gtk.EventControllerKey()
        window_controller.connect("key-pressed", self._on_window_key_pressed)
        self.add_controller(window_controller)

    def _apply_panel_geometry(self) -> None:
        """Apply the configured sidebar geometry using explicit inner-pane widths."""

        requested_utility_width = max(self._MIN_HISTORY_WIDTH, self.config.ui.panel_history_width)
        requested_main_width = max(self._MIN_CHAT_WIDTH, self.config.ui.panel_chat_width)
        total_width = compute_panel_total_width(requested_utility_width, requested_main_width)
        total_height = max(self._MIN_PANEL_HEIGHT, self.config.ui.panel_height)
        utility_width = requested_utility_width
        main_width = requested_main_width

        usable_thread_height = max(
            UI_MIN_CONVERSATION_HEIGHT + UI_MIN_COMPOSER_HEIGHT,
            total_height - self._THREAD_VERTICAL_CHROME,
        )
        conversation_height = int(usable_thread_height * self.config.ui.panel_conversation_ratio)
        conversation_height = min(
            usable_thread_height - UI_MIN_COMPOSER_HEIGHT,
            max(UI_MIN_CONVERSATION_HEIGHT, conversation_height),
        )
        composer_height = max(
            UI_MIN_COMPOSER_HEIGHT,
            usable_thread_height - conversation_height,
        )

        self.set_default_size(total_width, total_height)
        if hasattr(self, "stage"):
            self.stage.set_size_request(total_width - self._OUTER_MARGIN, total_height - self._OUTER_MARGIN)
        if hasattr(self, "left_stack_box"):
            self.left_stack_box.set_size_request(utility_width, total_height - self._OUTER_MARGIN)
        if hasattr(self, "main_box"):
            self.main_box.set_size_request(main_width, total_height - self._OUTER_MARGIN)
        if hasattr(self, "response_scroll"):
            self.response_scroll.set_size_request(main_width - 28, conversation_height)
        if hasattr(self, "composer"):
            self.composer.set_size_request(main_width - 28, composer_height)

    def _rail_button(self, icon_name: str, callback, tooltip: str) -> Gtk.Button:
        """Create one sidebar rail button with a symbolic icon."""

        button = Gtk.Button()
        button.set_child(Gtk.Image.new_from_icon_name(icon_name))
        button.set_tooltip_text(tooltip)
        _enable_instant_tooltip(button)
        button.connect("clicked", callback)
        return button

    def _icon_button(self, icon_name: str, callback, tooltip: str) -> Gtk.Button:
        """Create one icon-only action button."""

        button = Gtk.Button()
        button.add_css_class("nyx-icon-button")
        button.set_child(Gtk.Image.new_from_icon_name(icon_name))
        button.set_tooltip_text(tooltip)
        _enable_instant_tooltip(button)
        button.connect("clicked", callback)
        return button

    def _chip_label(self, text: str = "") -> Gtk.Label:
        """Create one rounded status-chip label."""

        label = Gtk.Label(label=text)
        label.add_css_class("nyx-chip")
        return label

    def _set_active_class(self, widget: Gtk.Widget, active: bool) -> None:
        """Toggle the ``active`` CSS class on one widget."""

        if active:
            widget.add_css_class("active")
        else:
            widget.remove_css_class("active")

    def _show_sidebar_page(self, page_name: str) -> None:
        """Show one sidebar stack page by name and update rail highlights."""

        self.sidebar_stack.set_visible_child_name(page_name)
        self._set_active_class(self.history_button, page_name == "history")
        self._set_active_class(self.settings_button, page_name == "settings")

    def _on_search_changed(self, search_entry: Gtk.SearchEntry) -> None:
        """Rebuild the session list when the search query changes."""

        del search_entry
        self._rebuild_session_list()

    def _rebuild_session_list(self) -> None:
        """Rebuild the panel session list from controller state and search query."""

        while (row := self.session_list.get_first_child()) is not None:
            self.session_list.remove(row)

        matches = self.controller.filter_sessions(self.search_entry.get_text())
        if not matches:
            self.session_list.set_placeholder(Gtk.Label(label="No matching conversations."))
            return

        self.session_list.set_placeholder(Gtk.Label(label="No conversations yet."))
        for session in matches:
            row = Gtk.ListBoxRow()
            row.set_activatable(True)
            row.set_selectable(True)
            row._session_id = session.session_id
            row.set_child(self._build_session_row(session))
            self.session_list.append(row)
            if session.session_id == self._renaming_session_id:
                rename_entry = getattr(row, "_rename_entry", None)
                if rename_entry is not None:
                    GLib.idle_add(self._focus_rename_entry, rename_entry)

    def _build_session_row(self, session: SessionRecord) -> Gtk.Widget:
        """Create the list-box row widget for one conversation."""

        shell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        shell.add_css_class("nyx-history-row")
        shell.add_css_class("nyx-history-row-shell")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.set_hexpand(True)
        content.add_css_class("nyx-history-row-main")
        shell.append(content)

        if session.session_id == self._renaming_session_id:
            rename_entry = Gtk.Entry()
            rename_entry.set_text(session.title)
            rename_entry.set_hexpand(True)
            rename_entry.set_activates_default(False)
            rename_entry.add_css_class("nyx-history-rename-entry")
            rename_entry.connect("activate", self._on_rename_submit_activate, session.session_id)
            content.append(rename_entry)
            shell._rename_entry = rename_entry

            helper_label = Gtk.Label(
                label="Rename this conversation and press Enter or the check button to save.",
                xalign=0.0,
            )
            helper_label.set_wrap(True)
            helper_label.add_css_class("nyx-history-subtitle")
            content.append(helper_label)
        else:
            title_label = Gtk.Label(label=session.title, xalign=0.0)
            title_label.set_wrap(True)
            title_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            title_label.set_lines(2)
            title_label.set_max_width_chars(24)
            title_label.add_css_class("nyx-history-title")
            content.append(title_label)

            subtitle_label = Gtk.Label(label=session.subtitle, xalign=0.0)
            subtitle_label.set_wrap(True)
            subtitle_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            subtitle_label.set_lines(2)
            subtitle_label.set_max_width_chars(24)
            subtitle_label.add_css_class("nyx-history-subtitle")
            content.append(subtitle_label)

            preview_label = Gtk.Label(label=session.preview, xalign=0.0)
            preview_label.add_css_class("nyx-history-preview")
            preview_label.set_wrap(True)
            preview_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            preview_label.set_lines(3)
            preview_label.set_max_width_chars(28)
            content.append(preview_label)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.set_valign(Gtk.Align.START)
        shell.append(actions)

        if session.session_id == self._renaming_session_id:
            save_button = Gtk.Button()
            save_button.add_css_class("nyx-history-delete")
            save_button.set_child(Gtk.Image.new_from_icon_name("object-select-symbolic"))
            save_button.set_tooltip_text("Save name")
            _enable_instant_tooltip(save_button)
            save_button.connect("clicked", self._on_rename_save_clicked, session.session_id)
            actions.append(save_button)

            cancel_button = Gtk.Button()
            cancel_button.add_css_class("nyx-history-delete")
            cancel_button.set_child(Gtk.Image.new_from_icon_name("window-close-symbolic"))
            cancel_button.set_tooltip_text("Cancel rename")
            _enable_instant_tooltip(cancel_button)
            cancel_button.connect("clicked", self._on_rename_cancel_clicked, session.session_id)
            actions.append(cancel_button)
        else:
            rename_button = Gtk.Button()
            rename_button.add_css_class("nyx-history-delete")
            rename_button.set_child(Gtk.Image.new_from_icon_name("document-edit-symbolic"))
            rename_button.set_tooltip_text("Rename conversation")
            _enable_instant_tooltip(rename_button)
            rename_button.connect("clicked", self._on_rename_session_clicked, session.session_id)
            actions.append(rename_button)

        delete_button = Gtk.Button()
        delete_button.add_css_class("nyx-history-delete")
        delete_button.set_child(Gtk.Image.new_from_icon_name("edit-delete-symbolic"))
        delete_button.set_tooltip_text("Delete conversation")
        _enable_instant_tooltip(delete_button)
        delete_button.connect("clicked", self._on_delete_session_clicked, session.session_id)
        actions.append(delete_button)

        return shell

    def _on_row_selected(self, list_box: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        """Load the selected conversation into the panel main view."""

        del list_box
        if row is None:
            return
        session_id = getattr(row, "_session_id", None)
        if session_id is None:
            return

        state = self.controller.state_for_session(session_id)
        if state is None:
            return
        self._apply_state(state)
        self._set_prompt_text("")
        self._move_cursor_to_end()

    def _apply_state(self, state: OverlayViewState) -> None:
        """Render a controller-provided state into the GTK widgets."""

        provider_text = f"● {state.provider_name}"
        if state.model_name:
            provider_text += f"  {state.model_name}"
        self.provider_label.set_label(provider_text)
        self.tokens_label.set_visible(state.token_count is not None)
        if state.token_count is not None:
            self.tokens_label.set_label(f"{state.token_count} tok")
        self.degraded_label.set_visible(state.degraded)

        self.status_meta_label.set_label("Conversation")

        window_text = ""
        if state.active_window is not None and state.active_window.app_name:
            window_text = state.active_window.app_name
            self.window_label.set_tooltip_text(state.active_window.window_title or state.active_window.app_name)
            self.status_meta_label.set_label(
                state.active_window.window_title or f"Working in {state.active_window.app_name}"
            )
        self.window_label.set_label(window_text)
        self.window_label.set_visible(bool(window_text))
        render_markdown_to_buffer(self.response_view.get_buffer(), state.conversation_text)
        self._last_response_text = state.response_text

    def _on_window_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Handle panel-level shortcuts such as close, copy, and mode toggle."""

        del controller, keycode
        if keyval == Gdk.KEY_Escape:
            self.close()
            self.get_application().quit()
            return True
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in {Gdk.KEY_h, Gdk.KEY_H}:
            self._on_compact_clicked(None)
            return True
        if state & Gdk.ModifierType.CONTROL_MASK and keyval == Gdk.KEY_comma:
            self._show_sidebar_page("settings")
            return True
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in {Gdk.KEY_c, Gdk.KEY_C}:
            self._copy_last_response()
            return True
        return False

    def _on_prompt_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Handle panel prompt submission and history navigation."""

        del controller, keycode
        if keyval == Gdk.KEY_Return and not (state & Gdk.ModifierType.SHIFT_MASK):
            self._submit_current_prompt()
            return True
        if keyval == Gdk.KEY_Up and not (state & Gdk.ModifierType.SHIFT_MASK):
            previous = self.controller.previous_history()
            if previous is not None:
                self._set_prompt_text(previous)
                self._move_cursor_to_end()
                return True
        if keyval == Gdk.KEY_Down and not (state & Gdk.ModifierType.SHIFT_MASK):
            self._set_prompt_text(self.controller.next_history())
            self._move_cursor_to_end()
            return True
        return False

    def _submit_current_prompt(self) -> None:
        """Submit the current composer contents when possible."""

        prompt = self._get_prompt_text().strip()
        if prompt and self._submission_task is None:
            self._submission_task = asyncio.create_task(self._submit_prompt(prompt))

    async def _submit_prompt(self, prompt: str) -> None:
        """Submit the current prompt asynchronously and update the panel."""

        self._apply_state(self.controller.busy_state())
        self.prompt_view.set_editable(False)
        try:
            state = await self.controller.submit_prompt(prompt)
        except Exception as exc:
            self.logger.exception("Panel prompt submission failed.")
            state = OverlayViewState(
                response_text=f"Nyx panel failed to submit prompt: {exc}",
                conversation_text=f"## Assistant\n\nNyx panel failed to submit prompt: {exc}",
                provider_name=self.config.models.default,
                model_name=None,
                token_count=None,
                degraded=True,
                yolo=self.config.system.yolo,
                busy=False,
                active_window=None,
                selected_session_id=self.controller.selected_session_id,
            )
        finally:
            self.prompt_view.set_editable(True)
            self._submission_task = None

        self._rebuild_session_list()
        self._apply_state(state)
        self._select_session_row(state.selected_session_id)
        self._set_prompt_text("")
        self.focus_prompt()

    def _select_session_row(self, session_id: str | None) -> None:
        """Select the visible row matching the supplied conversation id."""

        if session_id is None:
            return
        child = self.session_list.get_first_child()
        while child is not None:
            if getattr(child, "_session_id", None) == session_id:
                self.session_list.select_row(child)
                return
            child = child.get_next_sibling()

    def _focus_rename_entry(self, entry: Gtk.Entry) -> bool:
        """Focus and select the inline rename entry once the row is visible."""

        entry.grab_focus()
        entry.select_region(0, -1)
        return False

    def _get_prompt_text(self) -> str:
        """Return the full prompt text from the input buffer."""

        buffer = self.prompt_view.get_buffer()
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        return buffer.get_text(start, end, True)

    def _set_prompt_text(self, text: str) -> None:
        """Replace the prompt buffer contents with the supplied text."""

        self.prompt_view.get_buffer().set_text(text)

    def _copy_last_response(self) -> None:
        """Copy the current response text to the system clipboard."""

        display = self.get_display()
        if display is None or not self._last_response_text:
            return
        clipboard = display.get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(self._last_response_text))

    def _move_cursor_to_end(self) -> None:
        """Move the insertion cursor to the end of the composer buffer."""

        buffer = self.prompt_view.get_buffer()
        end = buffer.get_end_iter()
        buffer.place_cursor(end)

    def _on_new_conversation_clicked(self, button: Gtk.Button | None) -> None:
        """Clear the current selection and start a new thread."""

        del button
        state = self.controller.start_new_conversation()
        self._apply_state(state)
        self.session_list.unselect_all()
        self._set_prompt_text("")
        self._show_sidebar_page("history")
        self.focus_prompt()

    def _on_archive_clicked(self, button: Gtk.Button) -> None:
        """Archive the currently selected conversation."""

        del button
        if self.controller.selected_session_id is None:
            return
        state = self.controller.archive_session(self.controller.selected_session_id)
        self._rebuild_session_list()
        self._apply_state(state)

    def _on_delete_session_clicked(self, button: Gtk.Button, session_id: str) -> None:
        """Delete one conversation directly from its row action."""

        del button
        self._renaming_session_id = None
        state = self.controller.delete_session(session_id)
        self._rebuild_session_list()
        self._apply_state(state)

    def _on_rename_session_clicked(self, button: Gtk.Button, session_id: str) -> None:
        """Switch one conversation row into inline rename mode."""

        del button
        self._renaming_session_id = session_id
        self._rebuild_session_list()
        self._select_session_row(session_id)

    def _on_rename_submit_activate(self, entry: Gtk.Entry, session_id: str) -> None:
        """Save the inline rename when Enter is pressed."""

        self._commit_rename_session(session_id, entry.get_text())

    def _on_rename_save_clicked(self, button: Gtk.Button, session_id: str) -> None:
        """Save the inline rename using the current row entry value."""

        del button
        row = self._find_session_row(session_id)
        if row is None:
            return
        entry = getattr(row, "_rename_entry", None)
        if entry is None:
            return
        self._commit_rename_session(session_id, entry.get_text())

    def _on_rename_cancel_clicked(self, button: Gtk.Button, session_id: str) -> None:
        """Exit inline rename mode without persisting changes."""

        del button, session_id
        self._renaming_session_id = None
        self._rebuild_session_list()

    def _commit_rename_session(self, session_id: str, title: str) -> None:
        """Persist an inline rename and refresh the visible history list."""

        state = self.controller.rename_session(session_id, title)
        self._renaming_session_id = None
        if state is None:
            self._rebuild_session_list()
            return
        self._rebuild_session_list()
        self._apply_state(state)
        self._select_session_row(session_id)

    def _find_session_row(self, session_id: str) -> Gtk.ListBoxRow | None:
        """Return the visible row for one conversation id, if present."""

        child = self.session_list.get_first_child()
        while child is not None:
            if getattr(child, "_session_id", None) == session_id:
                return child
            child = child.get_next_sibling()
        return None

    def _on_config_saved(self, new_config: NyxConfig) -> None:
        """Refresh local config references after the settings editor saves."""

        self.config = new_config
        self.controller.config = new_config
        application = self.get_application()
        if application is not None and hasattr(application, "apply_saved_config"):
            application.apply_saved_config(new_config)

    def _on_voice_clicked(self, button: Gtk.Button) -> None:
        """Toggle live voice capture from the panel composer."""

        del button
        application = self.get_application()
        if application is not None and hasattr(application, "toggle_voice_capture"):
            application.toggle_voice_capture(self)

    def _on_send_clicked(self, button: Gtk.Button) -> None:
        """Submit the current composer text."""

        del button
        self._submit_current_prompt()

    def _on_compact_clicked(self, button: Gtk.Button | None) -> None:
        """Return to the compact popup view."""

        del button
        application = self.get_application()
        if application is not None and hasattr(application, "show_launcher"):
            application.show_launcher()

    def _on_history_clicked(self, button: Gtk.Button) -> None:
        """Show the history page in the left stack."""

        del button
        self._show_sidebar_page("history")

    def _on_settings_clicked(self, button: Gtk.Button) -> None:
        """Show the settings page in the left stack."""

        del button
        self._show_sidebar_page("settings")


def _enable_instant_tooltip(widget: Gtk.Widget) -> None:
    """Show the tooltip query immediately when the pointer enters."""

    widget.set_has_tooltip(True)
    motion = Gtk.EventControllerMotion()
    motion.connect("enter", lambda controller, x, y: widget.trigger_tooltip_query())
    widget.add_controller(motion)
