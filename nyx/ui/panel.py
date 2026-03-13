"""GTK4 panel mode for Nyx.

The panel expands the Phase 4 launcher into a two-pane history/search surface
with an in-memory session sidebar and markdown response rendering.
"""

from __future__ import annotations

import asyncio
import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gtk, Gtk4LayerShell

from nyx.config import NyxConfig
from nyx.ui.rendering import render_markdown_to_buffer
from nyx.ui.session import OverlaySessionController, OverlayViewState, SessionRecord
from nyx.ui.styles import install_ui_css


class NyxPanelWindow(Gtk.ApplicationWindow):
    """Left-anchored GTK4 history/search panel."""

    def __init__(
        self,
        application: Gtk.Application,
        config: NyxConfig,
        controller: OverlaySessionController,
        logger: logging.Logger,
    ) -> None:
        """Initialize the panel window and its sidebar/main content widgets."""

        super().__init__(application=application)
        self.config = config
        self.controller = controller
        self.logger = logger
        self._last_response_text = ""
        self._submission_task: asyncio.Task[None] | None = None

        self.set_title("Nyx Panel")
        self.set_default_size(
            self.config.ui.panel_width + self.config.ui.launcher_width,
            max(720, self.config.ui.launcher_height + 360),
        )
        self.set_resizable(False)

        install_ui_css()
        self._configure_layer_shell()
        self._build_layout()
        self.refresh_from_controller()

    def refresh_from_controller(self) -> None:
        """Refresh the session list and the main panel state from the controller."""

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
        Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.TOP, 16)
        Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.BOTTOM, 16)
        Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.LEFT, 16)

        monitor = self._resolve_monitor()
        if monitor is not None:
            Gtk4LayerShell.set_monitor(self, monitor)

    def _resolve_monitor(self):
        """Resolve the configured monitor selection for the panel window."""

        display = self.get_display()
        if display is None:
            return None

        selection = self.config.ui.overlay_monitor
        if selection == "focused":
            return None

        monitors = display.get_monitors()
        monitor_count = monitors.get_n_items()
        if monitor_count == 0:
            return None

        if selection == "primary":
            return monitors.get_item(0)
        if selection.isdigit():
            index = max(0, int(selection) - 1)
            if index < monitor_count:
                return monitors.get_item(index)
        return None

    def _build_layout(self) -> None:
        """Create the sidebar and main panel widget tree."""

        root = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.set_child(root)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sidebar.set_margin_top(14)
        sidebar.set_margin_bottom(14)
        sidebar.set_margin_start(14)
        sidebar.set_margin_end(10)
        sidebar.add_css_class("nyx-overlay-window")
        sidebar.add_css_class("nyx-sidebar")
        root.set_start_child(sidebar)
        root.set_resize_start_child(False)
        root.set_shrink_start_child(False)
        root.set_position(self.config.ui.panel_width)

        sidebar_title = Gtk.Label(label="HISTORY", xalign=0.0)
        sidebar_title.add_css_class("nyx-section-title")
        sidebar.append(sidebar_title)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search")
        self.search_entry.connect("search-changed", self._on_search_changed)
        sidebar.append(self.search_entry)

        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_vexpand(True)
        sidebar.append(sidebar_scroll)

        self.session_list = Gtk.ListBox()
        self.session_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.session_list.connect("row-selected", self._on_row_selected)
        self.session_list.set_placeholder(Gtk.Label(label="No sessions yet."))
        sidebar_scroll.set_child(self.session_list)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(14)
        main_box.set_margin_bottom(14)
        main_box.set_margin_start(10)
        main_box.set_margin_end(14)
        main_box.add_css_class("nyx-overlay-window")
        root.set_end_child(main_box)
        root.set_resize_end_child(True)
        root.set_shrink_end_child(False)

        self.status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        main_box.append(self.status_row)

        self.spinner = Gtk.Spinner()
        self.status_row.append(self.spinner)

        self.provider_label = Gtk.Label(xalign=0.0)
        self.provider_label.add_css_class("nyx-status-label")
        self.status_row.append(self.provider_label)

        self.tokens_label = self._chip_label()
        self.status_row.append(self.tokens_label)

        self.window_label = self._chip_label()
        self.status_row.append(self.window_label)

        self.degraded_label = self._chip_label()
        self.degraded_label.set_label("⚠ degraded")
        self.status_row.append(self.degraded_label)

        self.yolo_label = self._chip_label()
        self.yolo_label.set_label("⚡ YOLO")
        self.status_row.append(self.yolo_label)

        prompt_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        prompt_frame.add_css_class("nyx-prompt")
        main_box.append(prompt_frame)

        self.prompt_view = Gtk.TextView()
        self.prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.prompt_view.set_monospace(True)
        self.prompt_view.set_top_margin(4)
        self.prompt_view.set_bottom_margin(4)
        self.prompt_view.set_left_margin(4)
        self.prompt_view.set_right_margin(4)
        self.prompt_view.set_size_request(-1, 84)
        prompt_frame.append(self.prompt_view)

        prompt_controller = Gtk.EventControllerKey()
        prompt_controller.connect("key-pressed", self._on_prompt_key_pressed)
        self.prompt_view.add_controller(prompt_controller)

        response_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        response_frame.add_css_class("nyx-response")
        main_box.append(response_frame)

        response_scroll = Gtk.ScrolledWindow()
        response_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        response_scroll.set_vexpand(True)
        response_frame.append(response_scroll)

        self.response_view = Gtk.TextView()
        self.response_view.set_editable(False)
        self.response_view.set_cursor_visible(False)
        self.response_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.response_view.set_monospace(False)
        self.response_view.set_top_margin(4)
        self.response_view.set_bottom_margin(4)
        self.response_view.set_left_margin(4)
        self.response_view.set_right_margin(4)
        response_scroll.set_child(self.response_view)

        window_controller = Gtk.EventControllerKey()
        window_controller.connect("key-pressed", self._on_window_key_pressed)
        self.add_controller(window_controller)

    def _chip_label(self) -> Gtk.Label:
        """Create a rounded status-chip label."""

        label = Gtk.Label()
        label.add_css_class("nyx-chip")
        return label

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
            self.session_list.set_placeholder(Gtk.Label(label="No matching sessions."))
            return

        self.session_list.set_placeholder(Gtk.Label(label="No sessions yet."))
        for session in matches:
            row = Gtk.ListBoxRow()
            row.set_activatable(True)
            row.set_selectable(True)
            row._session_id = session.session_id
            row.set_child(self._build_session_row(session))
            self.session_list.append(row)

    def _build_session_row(self, session: SessionRecord) -> Gtk.Widget:
        """Create the list-box row widget for a session record."""

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("nyx-session-row")

        title_label = Gtk.Label(label=session.title, xalign=0.0)
        title_label.add_css_class("nyx-session-title")
        box.append(title_label)

        subtitle_label = Gtk.Label(label=session.subtitle, xalign=0.0)
        subtitle_label.add_css_class("nyx-session-subtitle")
        box.append(subtitle_label)

        preview_label = Gtk.Label(label=session.preview or "No prompt preview", xalign=0.0)
        preview_label.add_css_class("nyx-session-preview")
        preview_label.set_wrap(True)
        preview_label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        box.append(preview_label)

        return box

    def _on_row_selected(
        self,
        list_box: Gtk.ListBox,
        row: Gtk.ListBoxRow | None,
    ) -> None:
        """Load the selected session into the panel main view."""

        del list_box
        if row is None:
            return
        session_id = getattr(row, "_session_id", None)
        if session_id is None:
            return

        state = self.controller.state_for_session(session_id)
        session = self.controller.get_session(session_id)
        if state is None or session is None:
            return

        self._apply_state(state)
        self._set_prompt_text(session.prompt)
        self._move_cursor_to_end(self.prompt_view)

    def _apply_state(self, state: OverlayViewState) -> None:
        """Render a controller-provided state into the GTK widgets."""

        provider_text = f"● {state.provider_name}"
        if state.model_name:
            provider_text += f"  {state.model_name}"
        self.provider_label.set_label(provider_text)

        token_text = "—" if state.token_count is None else str(state.token_count)
        self.tokens_label.set_label(f"[tokens: {token_text}]")

        window_text = "[⊞]"
        tooltip = None
        if state.active_window is not None and state.active_window.app_name:
            window_text = f"[⊞ {state.active_window.app_name}]"
            tooltip = state.active_window.window_title or state.active_window.app_name
        self.window_label.set_label(window_text)
        self.window_label.set_tooltip_text(tooltip)

        self.degraded_label.set_visible(state.degraded)
        self.yolo_label.set_visible(state.yolo)
        if state.busy:
            self.spinner.start()
        else:
            self.spinner.stop()

        render_markdown_to_buffer(self.response_view.get_buffer(), state.response_text)
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
            application = self.get_application()
            if application is not None and hasattr(application, "show_launcher"):
                application.show_launcher()
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
            prompt = self._get_prompt_text().strip()
            if prompt and self._submission_task is None:
                self._submission_task = asyncio.create_task(self._submit_prompt(prompt))
            return True

        if keyval == Gdk.KEY_Up and not (state & Gdk.ModifierType.SHIFT_MASK):
            previous = self.controller.previous_history()
            if previous is not None:
                self._set_prompt_text(previous)
                self._move_cursor_to_end(self.prompt_view)
                return True

        if keyval == Gdk.KEY_Down and not (state & Gdk.ModifierType.SHIFT_MASK):
            self._set_prompt_text(self.controller.next_history())
            self._move_cursor_to_end(self.prompt_view)
            return True

        return False

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
        self.prompt_view.grab_focus()

    def _select_session_row(self, session_id: int | None) -> None:
        """Select the session row matching the given identifier, if visible."""

        if session_id is None:
            return

        child = self.session_list.get_first_child()
        while child is not None:
            if getattr(child, "_session_id", None) == session_id:
                self.session_list.select_row(child)
                return
            child = child.get_next_sibling()

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

    def _move_cursor_to_end(self, text_view: Gtk.TextView) -> None:
        """Move the insertion cursor to the end of a ``Gtk.TextView`` buffer."""

        buffer = text_view.get_buffer()
        end = buffer.get_end_iter()
        buffer.place_cursor(end)
