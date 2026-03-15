"""GTK4 launcher and overlay application for Nyx."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import tempfile
import warnings

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell

from nyx.bridges.base import AudioRecordingSession, SystemBridge
from nyx.config import NyxConfig
from nyx.control import NyxControlError, send_control_command
from nyx.daemon import NyxDaemon
from nyx.ui.monitors import MonitorSelectionState, resolve_overlay_monitor
from nyx.ui.panel import NyxPanelWindow
from nyx.ui.rendering import render_markdown_to_buffer
from nyx.ui.session import OverlaySessionController, OverlayViewState
from nyx.ui.styles import install_ui_css
from nyx.ui.theme import ResolvedTheme, resolve_theme
from nyx.voice import VoiceInputError, VoiceTranscriber


class NyxLauncherWindow(Gtk.ApplicationWindow):
    """Compact glass popup overlay backed by ``gtk4-layer-shell``."""

    def __init__(
        self,
        application: Gtk.Application,
        config: NyxConfig,
        controller: OverlaySessionController,
        logger: logging.Logger,
        monitor_state: MonitorSelectionState,
        theme: ResolvedTheme,
        initial_prompt: str = "",
    ) -> None:
        """Create and configure the launcher window and widgets."""

        super().__init__(application=application)
        self.config = config
        self.controller = controller
        self.logger = logger
        self.monitor_state = monitor_state
        self.theme = theme
        self._last_response_text = ""
        self._initial_prompt = initial_prompt
        self._submission_task: asyncio.Task[None] | None = None

        self.set_title("Nyx")
        self.set_default_size(config.ui.launcher_width, config.ui.launcher_height)
        self.set_resizable(False)
        self.set_hide_on_close(False)
        self.set_decorated(False)
        self.add_css_class("nyx-window")

        self._configure_layer_shell()
        self._build_layout()
        self._apply_state(self.controller.idle_state())
        if initial_prompt:
            self._set_prompt_text(initial_prompt)

    def focus_prompt(self) -> None:
        """Focus the prompt input and move the cursor to the end."""

        self.prompt_view.grab_focus()
        self._move_cursor_to_end()

    def refresh_visuals(self, config: NyxConfig, theme: ResolvedTheme) -> None:
        """Refresh theme-bound visuals after config changes."""

        self.config = config
        self.theme = theme
        self.set_default_size(config.ui.launcher_width, config.ui.launcher_height)
        self.queue_draw()

    def set_recording_state(self, recording: bool) -> None:
        """Reflect whether live microphone capture is active."""

        if recording:
            self.voice_button.add_css_class("recording")
        else:
            self.voice_button.remove_css_class("recording")

    def append_prompt_text(self, text: str) -> None:
        """Append transcribed text into the current prompt input."""

        buffer = self.prompt_view.get_buffer()
        end = buffer.get_end_iter()
        if buffer.get_char_count() > 0:
            buffer.insert(end, "\n")
            end = buffer.get_end_iter()
        buffer.insert(end, text)
        self.focus_prompt()

    def _configure_layer_shell(self) -> None:
        """Apply the documented top-center layer-shell behavior."""

        Gtk4LayerShell.init_for_window(self)
        Gtk4LayerShell.set_namespace(self, "nyx-launcher")
        Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.TOP)
        Gtk4LayerShell.set_keyboard_mode(self, Gtk4LayerShell.KeyboardMode.EXCLUSIVE)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.TOP, True)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.LEFT, False)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.RIGHT, False)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.BOTTOM, False)
        Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.TOP, 18)
        monitor = self._resolve_monitor()
        if monitor is not None:
            Gtk4LayerShell.set_monitor(self, monitor)

    def _resolve_monitor(self):
        """Resolve the configured monitor selection for the launcher."""

        display = self.get_display()
        return resolve_overlay_monitor(display, self.config.ui.overlay_monitor, self.monitor_state)

    def _build_layout(self) -> None:
        """Create the compact popup widget tree."""

        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        stage.set_halign(Gtk.Align.CENTER)
        stage.set_valign(Gtk.Align.START)
        stage.set_size_request(self.config.ui.launcher_width, self.config.ui.launcher_height)
        stage.set_margin_top(10)
        stage.set_margin_bottom(10)
        stage.set_margin_start(10)
        stage.set_margin_end(10)
        stage.add_css_class("nyx-stage")
        stage.add_css_class("nyx-stage-compact")
        self.set_child(stage)

        self.status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.status_row.add_css_class("nyx-status-strip")
        self.status_row.set_halign(Gtk.Align.END)
        stage.append(self.status_row)

        self.provider_label = self._chip_label()
        self.status_row.append(self.provider_label)

        self.degraded_label = self._chip_label("degraded")
        self.degraded_label.set_visible(False)
        self.status_row.append(self.degraded_label)

        self.recording_label = self._chip_label("recording")
        self.recording_label.set_visible(False)
        self.status_row.append(self.recording_label)

        response_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        response_card.add_css_class("nyx-popup-card")
        response_card.add_css_class("nyx-popup-response-wrap")
        stage.append(response_card)

        response_scroll = Gtk.ScrolledWindow()
        response_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        response_scroll.set_vexpand(True)
        response_card.append(response_scroll)

        self.response_view = Gtk.TextView()
        self.response_view.set_editable(False)
        self.response_view.set_cursor_visible(False)
        self.response_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.response_view.set_monospace(True)
        self.response_view.set_top_margin(12)
        self.response_view.set_bottom_margin(12)
        self.response_view.set_left_margin(12)
        self.response_view.set_right_margin(12)
        self.response_view.add_css_class("nyx-popup-response")
        response_scroll.set_child(self.response_view)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        action_row.set_halign(Gtk.Align.CENTER)
        stage.append(action_row)

        self.voice_button = self._icon_button("audio-input-microphone-symbolic", self._on_voice_clicked)
        action_row.append(self.voice_button)

        popout_button = self._icon_button("view-right-pane-symbolic", self._on_popout_clicked)
        action_row.append(popout_button)

        input_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        input_card.add_css_class("nyx-popup-composer")
        stage.append(input_card)

        self.prompt_view = Gtk.TextView()
        self.prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.prompt_view.set_monospace(True)
        self.prompt_view.set_top_margin(6)
        self.prompt_view.set_bottom_margin(6)
        self.prompt_view.set_left_margin(8)
        self.prompt_view.set_right_margin(8)
        self.prompt_view.set_size_request(-1, 64)
        self.prompt_view.add_css_class("nyx-popup-input")
        input_card.append(self.prompt_view)

        prompt_controller = Gtk.EventControllerKey()
        prompt_controller.connect("key-pressed", self._on_prompt_key_pressed)
        self.prompt_view.add_controller(prompt_controller)

        footer = Gtk.Label(
            label="Enter to send • Shift+Enter for newline • Esc to close",
            xalign=0.5,
        )
        footer.add_css_class("nyx-hint")
        stage.append(footer)

        window_controller = Gtk.EventControllerKey()
        window_controller.connect("key-pressed", self._on_window_key_pressed)
        self.add_controller(window_controller)

    def _icon_button(self, icon_name: str, callback) -> Gtk.Button:
        """Create one icon-only action button."""

        button = Gtk.Button()
        button.add_css_class("nyx-icon-button")
        button.set_child(Gtk.Image.new_from_icon_name(icon_name))
        button.connect("clicked", callback)
        return button

    def _chip_label(self, text: str = "") -> Gtk.Label:
        """Create one rounded status-chip label."""

        label = Gtk.Label(label=text)
        label.add_css_class("nyx-chip")
        return label

    def _apply_state(self, state: OverlayViewState) -> None:
        """Render a controller-provided state into the GTK widgets."""

        provider_text = f"● {state.provider_name}"
        if state.model_name:
            provider_text += f"  {state.model_name}"
        self.provider_label.set_label(provider_text)
        self.degraded_label.set_visible(state.degraded)
        render_markdown_to_buffer(self.response_view.get_buffer(), state.response_text or "Nyx is ready.")
        self._last_response_text = state.response_text

    def _set_prompt_text(self, text: str) -> None:
        """Replace the prompt buffer contents with the supplied text."""

        self.prompt_view.get_buffer().set_text(text)

    def _get_prompt_text(self) -> str:
        """Return the full prompt text from the input buffer."""

        buffer = self.prompt_view.get_buffer()
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        return buffer.get_text(start, end, True)

    def _move_cursor_to_end(self) -> None:
        """Move the insertion cursor to the end of the prompt buffer."""

        buffer = self.prompt_view.get_buffer()
        end = buffer.get_end_iter()
        buffer.place_cursor(end)

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
            self._on_popout_clicked(None)
            return True
        if state & Gdk.ModifierType.CONTROL_MASK and keyval == Gdk.KEY_comma:
            application = self.get_application()
            if application is not None and hasattr(application, "show_panel"):
                application.show_panel("settings")
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
                self._move_cursor_to_end()
                return True
        if keyval == Gdk.KEY_Down and not (state & Gdk.ModifierType.SHIFT_MASK):
            self._set_prompt_text(self.controller.next_history())
            self._move_cursor_to_end()
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
                conversation_text=f"## Assistant\n\nNyx launcher failed to submit prompt: {exc}",
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
        self.focus_prompt()
        application = self.get_application()
        if application is not None and hasattr(application, "refresh_from_controller"):
            application.refresh_from_controller()

    def _copy_last_response(self) -> None:
        """Copy the last response text to the system clipboard."""

        display = self.get_display()
        if display is None or not self._last_response_text:
            return
        clipboard = display.get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(self._last_response_text))

    def _on_voice_clicked(self, button: Gtk.Button) -> None:
        """Toggle voice capture for the compact popup."""

        del button
        application = self.get_application()
        if application is not None and hasattr(application, "toggle_voice_capture"):
            application.toggle_voice_capture(self)

    def _on_popout_clicked(self, button: Gtk.Button | None) -> None:
        """Switch to the larger sidebar/panel mode."""

        del button
        application = self.get_application()
        if application is not None and hasattr(application, "show_panel"):
            application.show_panel("history")


class NyxLauncherApplication(Gtk.Application):
    """GTK application wrapper for launcher and panel overlay windows."""

    def __init__(
        self,
        config: NyxConfig,
        daemon: NyxDaemon,
        bridge: SystemBridge,
        logger: logging.Logger,
        monitor_state: MonitorSelectionState,
        initial_prompt: str = "",
    ) -> None:
        """Initialize the application with injected Nyx runtime dependencies."""

        super().__init__(application_id="dev.nyx.launcher")
        self.config = config
        self.daemon = daemon
        self.bridge = bridge
        self.logger = logger
        self.monitor_state = monitor_state
        self.initial_prompt = initial_prompt
        self.launcher_window: NyxLauncherWindow | None = None
        self.panel_window: NyxPanelWindow | None = None
        self._auto_close_scheduled = False
        self.theme = resolve_theme(config, logger)
        install_ui_css(self.theme, self.config.ui.font)
        self.controller = OverlaySessionController(
            daemon=daemon,
            bridge=bridge,
            config=config,
            logger=logger,
        )
        self._voice_recording_session: AudioRecordingSession | None = None
        self._voice_temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._voice_target: NyxLauncherWindow | NyxPanelWindow | None = None
        self._voice_recording_path: Path | None = None
        self._voice_toggle_task: asyncio.Task[None] | None = None
        self.connect("activate", self._on_activate)

    def refresh_from_controller(self) -> None:
        """Refresh whichever window is visible from the current controller state."""

        if self.panel_window is not None and self.panel_window.is_visible():
            self.panel_window.refresh_from_controller()
        if self.launcher_window is not None and self.launcher_window.is_visible():
            self.launcher_window._apply_state(self.controller.idle_state())

    def apply_saved_config(self, new_config: NyxConfig) -> None:
        """Apply a saved config locally and propagate it to the background daemon."""

        asyncio.create_task(self._apply_saved_config_async(new_config))

    async def _apply_saved_config_async(self, new_config: NyxConfig) -> None:
        """Reload local runtime state after the settings editor saves."""

        await self.daemon.reload_config(new_config)
        self.config = new_config
        self.bridge = self.daemon.bridge
        self.controller.config = new_config
        self.controller.daemon = self.daemon
        self.controller.bridge = self.bridge
        self.theme = resolve_theme(new_config, self.logger)
        install_ui_css(self.theme, self.config.ui.font)
        if self.launcher_window is not None:
            self.launcher_window.refresh_visuals(new_config, self.theme)
        if self.panel_window is not None:
            self.panel_window.refresh_visuals(new_config, self.theme)
        try:
            await send_control_command("reload_config")
        except NyxControlError:
            self.logger.debug("No running background Nyx daemon to reload.")

    def toggle_voice_capture(self, target_window: NyxLauncherWindow | NyxPanelWindow) -> None:
        """Start or stop live microphone capture for one window."""

        if self._voice_toggle_task is None:
            self._voice_toggle_task = asyncio.create_task(self._toggle_voice_capture_async(target_window))

    async def _toggle_voice_capture_async(
        self,
        target_window: NyxLauncherWindow | NyxPanelWindow,
    ) -> None:
        """Start or stop live microphone capture and insert the transcript."""

        try:
            if not self.config.voice.enabled:
                raise VoiceInputError(
                    "Voice input is disabled in config. Set [voice].enabled = true to use microphone input."
                )
            if self._voice_recording_session is None:
                self._voice_temp_dir = tempfile.TemporaryDirectory(prefix="nyx-voice-ui-")
                self._voice_recording_path = Path(self._voice_temp_dir.name) / "microphone.wav"
                self._voice_recording_session = await self.bridge.start_audio_recording(
                    str(self._voice_recording_path)
                )
                self._voice_target = target_window
                self._set_recording_state(True)
                return

            session = self._voice_recording_session
            output_path = self._voice_recording_path
            if output_path is None:
                raise VoiceInputError("Nyx lost the temporary microphone recording path.")
            recorded = await session.stop()
            self._voice_recording_session = None
            self._set_recording_state(False)
            if not recorded:
                raise VoiceInputError(
                    "Microphone recording did not produce usable audio. Check your microphone and PipeWire input."
                )
            transcript = await VoiceTranscriber(config=self.config, logger=self.logger).transcribe_file(output_path)
            if self._voice_target is not None:
                self._voice_target.append_prompt_text(transcript)
        except Exception as exc:
            self.logger.exception("Nyx overlay voice capture failed.")
            message = f"## Assistant\n\nVoice input failed: {exc}"
            state = OverlayViewState(
                response_text=f"Voice input failed: {exc}",
                conversation_text=message,
                provider_name=self.config.models.default,
                degraded=True,
                yolo=self.config.system.yolo,
            )
            if isinstance(target_window, NyxLauncherWindow):
                target_window._apply_state(state)
            elif self.panel_window is not None:
                self.panel_window._apply_state(state)
        finally:
            if self._voice_recording_session is None:
                self._clear_voice_state()
            self._voice_toggle_task = None

    def _set_recording_state(self, recording: bool) -> None:
        """Update visible windows to reflect recording state."""

        if self.launcher_window is not None:
            self.launcher_window.set_recording_state(recording)
            self.launcher_window.recording_label.set_visible(recording)
        if self.panel_window is not None:
            self.panel_window.set_recording_state(recording)

    def _clear_voice_state(self) -> None:
        """Reset temporary voice capture state and clean up temp files."""

        self._voice_recording_session = None
        self._voice_recording_path = None
        self._voice_target = None
        if self._voice_temp_dir is not None:
            self._voice_temp_dir.cleanup()
            self._voice_temp_dir = None

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
                monitor_state=self.monitor_state,
                theme=self.theme,
                initial_prompt=self.initial_prompt,
            )
        self.launcher_window.present()
        self.launcher_window.focus_prompt()
        self.launcher_window._apply_state(self.controller.idle_state())

    def show_panel(self, page_name: str = "history") -> None:
        """Show the history/settings panel and hide launcher mode."""

        if self.launcher_window is not None:
            self.launcher_window.hide()
        if self.panel_window is None:
            self.panel_window = NyxPanelWindow(
                application=self,
                config=self.config,
                controller=self.controller,
                logger=self.logger,
                monitor_state=self.monitor_state,
                theme=self.theme,
            )
        self.panel_window.refresh_from_controller(page_name)
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
    monitor_state: MonitorSelectionState,
    initial_prompt: str = "",
) -> int:
    """Run the GTK launcher application."""

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
        monitor_state=monitor_state,
        initial_prompt=initial_prompt,
    )
    return app.run(None)
