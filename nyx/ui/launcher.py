"""GTK4 launcher and overlay application for Nyx.

This module keeps the compact top-center launcher window from Phase 4 and now
owns the shared GTK application that can toggle into Phase 5 panel mode.
"""

from __future__ import annotations

import asyncio
import logging
import os
import warnings

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell

from nyx.bridges.base import SystemBridge
from nyx.config import NyxConfig
from nyx.daemon import NyxDaemon
from nyx.ui.panel import NyxPanelWindow
from nyx.ui.session import OverlaySessionController, OverlayViewState
from nyx.ui.styles import install_ui_css


class NyxLauncherWindow(Gtk.ApplicationWindow):
    """Top-center GTK4 launcher overlay backed by ``gtk4-layer-shell``."""

    def __init__(
        self,
        application: Gtk.Application,
        config: NyxConfig,
        controller: OverlaySessionController,
        logger: logging.Logger,
        initial_prompt: str = "",
    ) -> None:
        """Create and configure the launcher window and widgets."""

        super().__init__(application=application)
        self.config = config
        self.controller = controller
        self.logger = logger
        self._last_response_text = ""
        self._initial_prompt = initial_prompt
        self._submission_task: asyncio.Task[None] | None = None

        self.set_title("Nyx")
        self.set_default_size(config.ui.launcher_width, config.ui.launcher_height)
        self.set_resizable(False)
        self.set_hide_on_close(False)

        self._install_css()
        self._configure_layer_shell()
        self._build_layout()
        self._apply_state(self.controller.idle_state())
        if initial_prompt:
            self._set_prompt_text(initial_prompt)

    def _install_css(self) -> None:
        """Install the launcher CSS provider on the current display."""

        install_ui_css()

    def _configure_layer_shell(self) -> None:
        """Apply the documented top-center layer-shell behavior."""

        Gtk4LayerShell.init_for_window(self)
        Gtk4LayerShell.set_namespace(self, "nyx-launcher")
        Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.TOP)
        Gtk4LayerShell.set_keyboard_mode(self, Gtk4LayerShell.KeyboardMode.EXCLUSIVE)

        anchor = self.config.ui.overlay_anchor
        if anchor != "top-center":
            raise ValueError(f"Unsupported overlay anchor '{anchor}' in Phase 4.")

        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.TOP, True)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.LEFT, False)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.RIGHT, False)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.BOTTOM, False)
        Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.TOP, 20)

        monitor = self._resolve_monitor()
        if monitor is not None:
            Gtk4LayerShell.set_monitor(self, monitor)

    def _resolve_monitor(self):
        """Resolve the configured monitor selection for the launcher.

        Phase 4 leaves ``focused`` unset so the compositor can place the
        surface naturally on the focused output. ``primary`` maps to the first
        monitor exposed by GTK4, and explicit numeric values are treated as
        one-based monitor indices.
        """

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
        """Create the Phase 4 launcher widget tree."""

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(14)
        root.set_margin_bottom(14)
        root.set_margin_start(16)
        root.set_margin_end(16)
        root.add_css_class("nyx-overlay-window")
        self.set_child(root)

        self.status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.status_row.set_halign(Gtk.Align.FILL)
        root.append(self.status_row)

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
        root.append(prompt_frame)

        self.prompt_view = Gtk.TextView()
        self.prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.prompt_view.set_monospace(True)
        self.prompt_view.set_top_margin(4)
        self.prompt_view.set_bottom_margin(4)
        self.prompt_view.set_left_margin(4)
        self.prompt_view.set_right_margin(4)
        self.prompt_view.set_vexpand(False)
        self.prompt_view.set_size_request(-1, 72)
        prompt_frame.append(self.prompt_view)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_prompt_key_pressed)
        self.prompt_view.add_controller(key_controller)

        window_controller = Gtk.EventControllerKey()
        window_controller.connect("key-pressed", self._on_window_key_pressed)
        self.add_controller(window_controller)

        response_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        response_frame.add_css_class("nyx-response")
        root.append(response_frame)

        response_scroll = Gtk.ScrolledWindow()
        response_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        response_scroll.set_vexpand(True)
        response_frame.append(response_scroll)

        self.response_view = Gtk.TextView()
        self.response_view.set_editable(False)
        self.response_view.set_cursor_visible(False)
        self.response_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.response_view.set_monospace(True)
        self.response_view.set_top_margin(4)
        self.response_view.set_bottom_margin(4)
        self.response_view.set_left_margin(4)
        self.response_view.set_right_margin(4)
        response_scroll.set_child(self.response_view)

    def _chip_label(self) -> Gtk.Label:
        """Create a rounded status-chip label."""

        label = Gtk.Label()
        label.add_css_class("nyx-chip")
        return label

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

        self._set_response_text(state.response_text)
        self._last_response_text = state.response_text

    def _set_prompt_text(self, text: str) -> None:
        """Replace the prompt buffer contents with the supplied text."""

        buffer = self.prompt_view.get_buffer()
        buffer.set_text(text)

    def _get_prompt_text(self) -> str:
        """Return the full prompt text from the input buffer."""

        buffer = self.prompt_view.get_buffer()
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        return buffer.get_text(start, end, True)

    def _set_response_text(self, text: str) -> None:
        """Replace the response buffer contents with plain text."""

        buffer = self.response_view.get_buffer()
        buffer.set_text(text)

    def _on_window_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Handle launcher-level shortcuts documented for overlay control."""

        del controller, keycode

        if keyval == Gdk.KEY_Escape:
            self.close()
            self.get_application().quit()
            return True

        if state & Gdk.ModifierType.CONTROL_MASK and keyval in {Gdk.KEY_h, Gdk.KEY_H}:
            application = self.get_application()
            if application is not None and hasattr(application, "show_panel"):
                application.show_panel()
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
        """Handle launcher prompt submission and history navigation."""

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
        """Submit the current prompt asynchronously and update the view."""

        self._apply_state(self.controller.busy_state())
        self.prompt_view.set_editable(False)
        try:
            state = await self.controller.submit_prompt(prompt)
        except Exception as exc:
            self.logger.exception("Launcher prompt submission failed.")
            state = OverlayViewState(
                response_text=f"Nyx launcher failed to submit prompt: {exc}",
                provider_name=self.config.models.default,
                model_name=None,
                token_count=None,
                degraded=True,
                yolo=self.config.system.yolo,
                busy=False,
                active_window=None,
            )
        finally:
            self.prompt_view.set_editable(True)
            self._submission_task = None

        self._apply_state(state)
        self._set_prompt_text("")
        self.prompt_view.grab_focus()

    def _copy_last_response(self) -> None:
        """Copy the last response text to the system clipboard."""

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

    def focus_prompt(self) -> None:
        """Focus the prompt input and move the cursor to the end."""

        self.prompt_view.grab_focus()
        self._move_cursor_to_end(self.prompt_view)


class NyxLauncherApplication(Gtk.Application):
    """GTK application wrapper for launcher and panel overlay windows."""

    def __init__(
        self,
        config: NyxConfig,
        daemon: NyxDaemon,
        bridge: SystemBridge,
        logger: logging.Logger,
        initial_prompt: str = "",
    ) -> None:
        """Initialize the application with injected Nyx runtime dependencies."""

        super().__init__(application_id="dev.nyx.launcher")
        self.config = config
        self.daemon = daemon
        self.bridge = bridge
        self.logger = logger
        self.initial_prompt = initial_prompt
        self.launcher_window: NyxLauncherWindow | None = None
        self.panel_window: NyxPanelWindow | None = None
        self._auto_close_scheduled = False
        self.controller = OverlaySessionController(
            daemon=daemon,
            bridge=bridge,
            config=config,
            logger=logger,
        )
        self.connect("activate", self._on_activate)

    def _on_activate(self, app: Gtk.Application) -> None:
        """Create or re-present the correct overlay window on activation."""

        del app
        if not self._auto_close_scheduled:
            self._schedule_auto_close()

        if os.environ.get("NYX_UI_START_MODE") == "panel":
            self.show_panel()
            return
        self.show_launcher()

    def show_launcher(self) -> None:
        """Show the compact launcher window and hide panel mode."""

        if self.panel_window is not None:
            self.panel_window.hide()
        if self.launcher_window is None:
            self.launcher_window = NyxLauncherWindow(
                application=self,
                config=self.config,
                controller=self.controller,
                logger=self.logger,
                initial_prompt=self.initial_prompt,
            )
        self.launcher_window.present()
        self.launcher_window.focus_prompt()

    def show_panel(self) -> None:
        """Show the history/search panel and hide launcher mode."""

        if self.launcher_window is not None:
            self.launcher_window.hide()
        if self.panel_window is None:
            self.panel_window = NyxPanelWindow(
                application=self,
                config=self.config,
                controller=self.controller,
                logger=self.logger,
            )
        self.panel_window.refresh_from_controller()
        self.panel_window.present()
        self.panel_window.focus_prompt()

    def _auto_close(self) -> bool:
        """Quit the launcher application during automated smoke checks."""

        self.quit()
        return False

    def _schedule_auto_close(self) -> None:
        """Install the optional auto-close timer used by smoke checks."""

        auto_close_ms = os.environ.get("NYX_LAUNCHER_AUTOCLOSE_MS")
        if auto_close_ms and auto_close_ms.isdigit():
            GLib.timeout_add(int(auto_close_ms), self._auto_close)
        self._auto_close_scheduled = True


def run_launcher(
    config: NyxConfig,
    daemon: NyxDaemon,
    bridge: SystemBridge,
    logger: logging.Logger,
    initial_prompt: str = "",
) -> int:
    """Run the GTK launcher application.

    Args:
        config: Loaded Nyx configuration.
        daemon: Nyx daemon instance used for prompt handling.
        bridge: Active system bridge used for active-window status updates.
        logger: Application logger.
        initial_prompt: Optional prompt text pre-filled into the input field.

    Returns:
        The GTK application exit code.
    """

    import gi.events

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="'asyncio.set_event_loop_policy' is deprecated",
            category=DeprecationWarning,
        )
        asyncio.set_event_loop_policy(gi.events.GLibEventLoopPolicy())

    if not Gtk4LayerShell.is_supported():
        raise RuntimeError("gtk4-layer-shell is not supported in this session.")

    app = NyxLauncherApplication(
        config=config,
        daemon=daemon,
        bridge=bridge,
        logger=logger,
        initial_prompt=initial_prompt,
    )
    return app.run(None)
