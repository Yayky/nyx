"""Configuration models and loader for Nyx.

This module defines the Phase 1 config schema matching ``documentation.md`` and
provides a strict TOML loader. The loader returns fully populated dataclass
instances, expands user paths, and rejects unknown keys so later phases build on
stable configuration contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import copy
import tomllib

DEFAULT_CONFIG_PATH = Path("~/.config/nyx/config.toml").expanduser()

_SECTION_KEYS: dict[str, set[str]] = {
    "models": {"default", "fallback", "providers"},
    "voice": {"enabled", "whisper_model", "whisper_binary"},
    "notes": {"notes_dir", "inbox_file", "projects_dir", "auto_sort"},
    "rag": {"db_path", "embed_model"},
    "sync": {
        "notes_repo_path",
        "memory_mirror_path",
        "syncthing_config_path",
        "syncthing_snippet_path",
        "syncthing_folder_id",
    },
    "web": {"searxng_url", "brave_api_key", "fallback_timeout_seconds"},
    "git": {"use_ssh", "gh_cli"},
    "calendar": {
        "provider",
        "credentials_path",
        "auth_mode",
        "default_calendar_id",
        "calendar_ids",
        "include_all_calendars",
    },
    "skills": {"disabled"},
    "monitors": {"poll_interval_seconds"},
    "ui": {
        "overlay_anchor",
        "overlay_monitor",
        "launcher_width",
        "launcher_height",
        "panel_width",
        "font",
        "summon_hotkey",
    },
    "system": {"confirm_destructive", "yolo", "screenshot_tmp"},
}


@dataclass(slots=True)
class ProviderConfig:
    """One model provider definition from ``[models.providers]``.

    Attributes:
        name: Provider instance name used for selection.
        type: Provider implementation type such as ``ollama`` or
            ``subprocess-cli``.
        options: Provider-specific fields kept as a validated mapping for later
            phases.
    """

    name: str
    type: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelsConfig:
    """Model selection settings and provider definitions."""

    default: str
    fallback: list[str]
    providers: list[ProviderConfig]


@dataclass(slots=True)
class VoiceConfig:
    """Voice and speech-to-text configuration."""

    enabled: bool
    whisper_model: str
    whisper_binary: str


@dataclass(slots=True)
class NotesConfig:
    """Notes and project storage configuration."""

    notes_dir: Path
    inbox_file: str
    projects_dir: Path
    auto_sort: bool


@dataclass(slots=True)
class RagConfig:
    """RAG storage and embedding configuration."""

    db_path: Path
    embed_model: str


@dataclass(slots=True)
class WebConfig:
    """Web lookup configuration."""

    searxng_url: str
    brave_api_key: str
    fallback_timeout_seconds: int


@dataclass(slots=True)
class SyncConfig:
    """Cross-device sync configuration."""

    notes_repo_path: Path
    memory_mirror_path: Path
    syncthing_config_path: Path
    syncthing_snippet_path: Path
    syncthing_folder_id: str


@dataclass(slots=True)
class GitConfig:
    """Git and GitHub integration configuration."""

    use_ssh: bool
    gh_cli: bool


@dataclass(slots=True)
class CalendarConfig:
    """Calendar provider configuration."""

    provider: str
    credentials_path: Path
    auth_mode: str
    default_calendar_id: str
    calendar_ids: list[str]
    include_all_calendars: bool


@dataclass(slots=True)
class SkillsConfig:
    """Skill discovery configuration."""

    disabled: list[str]


@dataclass(slots=True)
class MonitorsConfig:
    """System monitor polling configuration."""

    poll_interval_seconds: int


@dataclass(slots=True)
class UiConfig:
    """Overlay UI sizing and behavior configuration."""

    overlay_anchor: str
    overlay_monitor: str
    launcher_width: int
    launcher_height: int
    panel_width: int
    font: str
    summon_hotkey: str


@dataclass(slots=True)
class SystemConfig:
    """System behavior configuration."""

    confirm_destructive: bool
    yolo: bool
    screenshot_tmp: Path


@dataclass(slots=True)
class NyxConfig:
    """Fully materialized Nyx configuration tree."""

    models: ModelsConfig
    voice: VoiceConfig
    notes: NotesConfig
    rag: RagConfig
    sync: SyncConfig
    web: WebConfig
    git: GitConfig
    calendar: CalendarConfig
    skills: SkillsConfig
    monitors: MonitorsConfig
    ui: UiConfig
    system: SystemConfig
    config_path: Path


def load_config(config_path: Path | None = None) -> NyxConfig:
    """Load Nyx configuration from TOML, merging over documented defaults.

    Args:
        config_path: Optional path to a TOML file. When omitted, Nyx uses the
            standard user config path.

    Returns:
        A fully populated ``NyxConfig`` dataclass tree.

    Raises:
        ValueError: The config contains unknown keys or unsupported shapes.
        tomllib.TOMLDecodeError: The file exists but is not valid TOML.
        OSError: The file cannot be read.
    """

    resolved_path = (config_path or DEFAULT_CONFIG_PATH).expanduser()
    merged = copy.deepcopy(_default_config_dict())

    if resolved_path.exists():
        try:
            with resolved_path.open("rb") as config_file:
                loaded = tomllib.load(config_file)
        except tomllib.TOMLDecodeError as exc:
            raise tomllib.TOMLDecodeError(
                f"Failed to parse TOML in {resolved_path}: {exc}",
                exc.doc,
                exc.pos,
            ) from exc

        if not isinstance(loaded, dict):
            raise ValueError(f"Configuration in {resolved_path} must be a TOML table.")

        _merge_top_level(merged, loaded, resolved_path)

    return _build_config(merged, resolved_path)


def _default_config_dict() -> dict[str, Any]:
    """Return the Phase 1 default configuration as nested dictionaries."""

    return {
        "models": {
            "default": "ollama-local",
            "fallback": ["anthropic", "codex-cli"],
            "providers": [
                {
                    "name": "ollama-local",
                    "type": "ollama",
                    "model": "qwen2.5:7b",
                    "host": "http://localhost:11434",
                },
                {
                    "name": "ollama-big",
                    "type": "ollama",
                    "model": "qwen2.5:14b",
                    "host": "http://localhost:11434",
                },
                {
                    "name": "anthropic",
                    "type": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "api_key_env": "ANTHROPIC_API_KEY",
                },
                {
                    "name": "openai",
                    "type": "openai",
                    "model": "gpt-4o",
                    "api_key_env": "OPENAI_API_KEY",
                },
                {
                    "name": "codex-cli",
                    "type": "subprocess-cli",
                    "binary": "codex",
                    "args": ["exec", "--json", "-"],
                    "image_args": ["--image", "{image_path}"],
                    "timeout_seconds": 60,
                },
                {
                    "name": "claude-code",
                    "type": "subprocess-cli",
                    "binary": "claude",
                    "args": ["--output-format", "json", "-p"],
                    "timeout_seconds": 60,
                },
                {
                    "name": "gemini-cli",
                    "type": "subprocess-cli",
                    "binary": "gemini",
                    "args": ["--json"],
                    "timeout_seconds": 60,
                },
                {
                    "name": "lm-studio",
                    "type": "openai-compat",
                    "model": "local-model",
                    "base_url": "http://localhost:1234/v1",
                    "api_key_env": "LM_STUDIO_KEY",
                },
            ],
        },
        "voice": {
            "enabled": True,
            "whisper_model": "base",
            "whisper_binary": "whisper",
        },
        "notes": {
            "notes_dir": "~/notes",
            "inbox_file": "inbox.md",
            "projects_dir": "~/notes/projects",
            "auto_sort": True,
        },
        "rag": {
            "db_path": "~/.local/share/nyx/rag",
            "embed_model": "nomic-embed-text",
        },
        "sync": {
            "notes_repo_path": "~/notes",
            "memory_mirror_path": "~/notes/memory.md",
            "syncthing_config_path": "~/.local/state/syncthing/config.xml",
            "syncthing_snippet_path": "~/.config/nyx/syncthing-nyx-rag.xml",
            "syncthing_folder_id": "nyx-rag",
        },
        "web": {
            "searxng_url": "http://localhost:8080",
            "brave_api_key": "",
            "fallback_timeout_seconds": 3,
        },
        "git": {
            "use_ssh": True,
            "gh_cli": True,
        },
        "calendar": {
            "provider": "google",
            "credentials_path": "~/.config/nyx/google_credentials.json",
            "auth_mode": "auto",
            "default_calendar_id": "primary",
            "calendar_ids": [],
            "include_all_calendars": False,
        },
        "skills": {
            "disabled": [],
        },
        "monitors": {
            "poll_interval_seconds": 30,
        },
        "ui": {
            "overlay_anchor": "top-center",
            "overlay_monitor": "focused",
            "launcher_width": 700,
            "launcher_height": 300,
            "panel_width": 400,
            "font": "monospace 11",
            "summon_hotkey": "Super+A",
        },
        "system": {
            "confirm_destructive": True,
            "yolo": False,
            "screenshot_tmp": "/tmp/nyx-screen.png",
        },
    }


def _merge_top_level(
    destination: dict[str, Any],
    overrides: dict[str, Any],
    config_path: Path,
) -> None:
    """Merge TOML data over defaults while rejecting unknown keys."""

    for key, value in overrides.items():
        if key not in destination:
            raise ValueError(f"Unknown top-level config section '{key}' in {config_path}.")

        if key == "models":
            _merge_models_section(destination[key], value, config_path)
            continue

        if not isinstance(value, dict):
            raise ValueError(f"Config section '{key}' in {config_path} must be a TOML table.")

        unknown_keys = set(value) - _SECTION_KEYS[key]
        if unknown_keys:
            unknown = ", ".join(sorted(unknown_keys))
            raise ValueError(f"Unknown keys in section '{key}' in {config_path}: {unknown}")

        destination[key].update(value)


def _merge_models_section(
    destination: dict[str, Any],
    overrides: Any,
    config_path: Path,
) -> None:
    """Merge the models section and validate provider table shapes."""

    if not isinstance(overrides, dict):
        raise ValueError(f"Config section 'models' in {config_path} must be a TOML table.")

    unknown_keys = set(overrides) - _SECTION_KEYS["models"]
    if unknown_keys:
        unknown = ", ".join(sorted(unknown_keys))
        raise ValueError(f"Unknown keys in section 'models' in {config_path}: {unknown}")

    for key, value in overrides.items():
        if key == "providers":
            if not isinstance(value, list):
                raise ValueError(
                    f"Key 'models.providers' in {config_path} must be an array of tables."
                )
            for index, provider in enumerate(value):
                if not isinstance(provider, dict):
                    raise ValueError(
                        f"Provider at models.providers[{index}] in {config_path} must be a table."
                    )
                missing_required_keys = {"name", "type"} - set(provider)
                if missing_required_keys:
                    missing = ", ".join(sorted(missing_required_keys))
                    raise ValueError(
                        "Missing required provider keys in "
                        f"models.providers[{index}] in {config_path}: {missing}"
                    )
            destination["providers"] = value
        else:
            destination[key] = value


def _build_config(data: dict[str, Any], config_path: Path) -> NyxConfig:
    """Convert the merged dictionary tree into strict dataclass instances."""

    providers = [
        ProviderConfig(
            name=provider["name"],
            type=provider["type"],
            options={
                key: _expand_path_value(key, value)
                for key, value in provider.items()
                if key not in {"name", "type"}
            },
        )
        for provider in data["models"]["providers"]
    ]

    return NyxConfig(
        models=ModelsConfig(
            default=data["models"]["default"],
            fallback=list(data["models"]["fallback"]),
            providers=providers,
        ),
        voice=VoiceConfig(**data["voice"]),
        notes=NotesConfig(
            notes_dir=_expand_path(data["notes"]["notes_dir"]),
            inbox_file=data["notes"]["inbox_file"],
            projects_dir=_expand_path(data["notes"]["projects_dir"]),
            auto_sort=data["notes"]["auto_sort"],
        ),
        rag=RagConfig(
            db_path=_expand_path(data["rag"]["db_path"]),
            embed_model=data["rag"]["embed_model"],
        ),
        sync=SyncConfig(
            notes_repo_path=_expand_path(data["sync"]["notes_repo_path"]),
            memory_mirror_path=_expand_path(data["sync"]["memory_mirror_path"]),
            syncthing_config_path=_expand_path(data["sync"]["syncthing_config_path"]),
            syncthing_snippet_path=_expand_path(data["sync"]["syncthing_snippet_path"]),
            syncthing_folder_id=data["sync"]["syncthing_folder_id"],
        ),
        web=WebConfig(**data["web"]),
        git=GitConfig(**data["git"]),
        calendar=CalendarConfig(
            provider=data["calendar"]["provider"],
            credentials_path=_expand_path(data["calendar"]["credentials_path"]),
            auth_mode=data["calendar"]["auth_mode"],
            default_calendar_id=data["calendar"]["default_calendar_id"],
            calendar_ids=list(data["calendar"]["calendar_ids"]),
            include_all_calendars=data["calendar"]["include_all_calendars"],
        ),
        skills=SkillsConfig(disabled=list(data["skills"]["disabled"])),
        monitors=MonitorsConfig(**data["monitors"]),
        ui=UiConfig(**data["ui"]),
        system=SystemConfig(
            confirm_destructive=data["system"]["confirm_destructive"],
            yolo=data["system"]["yolo"],
            screenshot_tmp=_expand_path(data["system"]["screenshot_tmp"]),
        ),
        config_path=config_path,
    )


def _expand_path(raw_path: str) -> Path:
    """Expand a user path string into a ``Path`` object."""

    return Path(raw_path).expanduser()


def _expand_path_value(key: str, value: Any) -> Any:
    """Expand provider path-like values while leaving other values unchanged."""

    if key.endswith("_path") and isinstance(value, str):
        return _expand_path(value)
    return value
