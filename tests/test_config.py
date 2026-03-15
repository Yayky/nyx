"""Tests for Nyx configuration loading."""

from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from nyx.config import load_config, render_config_toml, save_config_text


def test_missing_config_uses_documented_defaults(tmp_path: Path) -> None:
    """A missing config file should yield the in-memory documented defaults."""

    config = load_config(tmp_path / "missing.toml")

    assert config.models.default == "ollama-local"
    assert config.voice.enabled is True
    assert config.web.fallback_timeout_seconds == 3
    assert config.system.screenshot_tmp == Path("/tmp/nyx-screen.png")


def test_path_values_are_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """User paths in config should be expanded to concrete filesystem paths."""

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    config = load_config(tmp_path / "missing.toml")

    assert config.notes.notes_dir == fake_home / "notes"
    assert config.rag.db_path == fake_home / ".local/share/nyx/rag"
    assert config.sync.notes_repo_path == fake_home / "notes"
    assert config.sync.syncthing_config_path == fake_home / ".local/state/syncthing/config.xml"


def test_partial_config_merges_over_defaults(tmp_path: Path) -> None:
    """Partial TOML should override defaults while preserving the rest."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[models]
default = "openai"

[system]
yolo = true
""".strip()
    )

    config = load_config(config_path)

    assert config.models.default == "openai"
    assert config.models.fallback == ["anthropic", "codex-cli"]
    assert config.system.yolo is True


def test_provider_specific_options_are_preserved(tmp_path: Path) -> None:
    """Provider tables should allow backend-specific options beyond the Phase 1 set."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[models]
default = "ollama-vision"

[[models.providers]]
name = "ollama-vision"
type = "ollama"
model = "gemma3:4b"
host = "http://localhost:11434"
vision_timeout_seconds = 300
""".strip()
    )

    config = load_config(config_path)

    provider = config.models.providers[0]
    assert provider.name == "ollama-vision"
    assert provider.options["vision_timeout_seconds"] == 300


def test_calendar_config_supports_adc_and_multi_calendar_settings(tmp_path: Path) -> None:
    """Calendar config should preserve auth-mode and multi-calendar options."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[calendar]
provider = "google"
auth_mode = "adc"
default_calendar_id = "work@example.com"
calendar_ids = ["primary", "team@example.com"]
include_all_calendars = true
""".strip()
    )

    config = load_config(config_path)

    assert config.calendar.provider == "google"
    assert config.calendar.auth_mode == "adc"
    assert config.calendar.default_calendar_id == "work@example.com"
    assert config.calendar.calendar_ids == ["primary", "team@example.com"]
    assert config.calendar.include_all_calendars is True


def test_sync_config_supports_custom_paths_and_folder_id(tmp_path: Path) -> None:
    """Sync config should preserve custom Git and Syncthing settings."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[sync]
notes_repo_path = "~/vault/notes"
memory_mirror_path = "~/vault/notes/memory.md"
syncthing_config_path = "~/.config/syncthing/config.xml"
syncthing_snippet_path = "~/.config/nyx/custom-snippet.xml"
syncthing_folder_id = "nyx-work"
""".strip()
    )

    config = load_config(config_path)

    assert config.sync.notes_repo_path == Path("~/vault/notes").expanduser()
    assert config.sync.syncthing_folder_id == "nyx-work"
    assert config.sync.syncthing_snippet_path == Path("~/.config/nyx/custom-snippet.xml").expanduser()


def test_voice_config_can_disable_voice_input(tmp_path: Path) -> None:
    """Voice config should preserve the explicit enabled flag."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[voice]
enabled = false
whisper_model = "ggml-base.bin"
whisper_binary = "/usr/bin/whisper-cli"
""".strip()
    )

    config = load_config(config_path)

    assert config.voice.enabled is False
    assert config.voice.whisper_model == "ggml-base.bin"


def test_unknown_keys_raise_descriptive_error(tmp_path: Path) -> None:
    """Unknown config keys should fail fast with useful context."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[system]
unknown = 1
""".strip()
    )

    with pytest.raises(ValueError, match="Unknown keys in section 'system'"):
        load_config(config_path)


def test_provider_missing_required_keys_raise_descriptive_error(tmp_path: Path) -> None:
    """Provider tables should still require stable identity keys."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[models]

[[models.providers]]
name = "broken-provider"
model = "gemma3:4b"
""".strip()
    )

    with pytest.raises(ValueError, match="Missing required provider keys"):
        load_config(config_path)


def test_invalid_toml_raises_descriptive_error(tmp_path: Path) -> None:
    """Malformed TOML should preserve file path context in the raised error."""

    config_path = tmp_path / "config.toml"
    config_path.write_text("[system\n")

    with pytest.raises(tomllib.TOMLDecodeError, match=str(config_path)):
        load_config(config_path)


def test_config_can_round_trip_through_toml_renderer(tmp_path: Path) -> None:
    """Rendering and saving config TOML should preserve key runtime settings."""

    source_path = tmp_path / "source.toml"
    source_path.write_text(
        """
[models]
default = "codex-cli"
fallback = ["ollama-local"]

[voice]
enabled = false

[ui]
overlay_monitor = "2"
summon_hotkey = "Super+Space"
""".strip()
    )

    config = load_config(source_path)
    rendered = render_config_toml(config)
    saved = save_config_text(rendered, tmp_path / "saved.toml")

    assert saved.models.default == "codex-cli"
    assert saved.models.fallback == ["ollama-local"]
    assert saved.voice.enabled is False
    assert saved.ui.overlay_monitor == "2"
    assert saved.ui.summon_hotkey == "Super+Space"


def test_ui_panel_geometry_settings_round_trip(tmp_path: Path) -> None:
    """UI panel sizing settings should survive load-render-save cycles."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[ui]
panel_width = 1180
panel_height = 700
panel_history_width = 290
panel_chat_width = 810
panel_conversation_ratio = 0.72
""".strip()
    )

    config = load_config(config_path)
    assert config.ui.panel_width == 1180
    assert config.ui.panel_height == 700
    assert config.ui.panel_history_width == 290
    assert config.ui.panel_chat_width == 810
    assert config.ui.panel_conversation_ratio == 0.72

    rendered = render_config_toml(config)
    saved = save_config_text(rendered, tmp_path / "saved.toml")
    assert saved.ui.panel_width == 1186
    assert saved.ui.panel_height == 700
    assert saved.ui.panel_history_width == 290
    assert saved.ui.panel_chat_width == 810
    assert saved.ui.panel_conversation_ratio == 0.72
