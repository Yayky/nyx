# Nyx

Nyx is a local-first, context-aware AI assistant daemon for Linux desktops.

It is designed around a persistent local runtime instead of a browser chat tab:
Nyx can reason over your project notes, route requests across local and cloud
models, inspect desktop context through a strict bridge layer, and expose both a
CLI and a GTK overlay UI.

## What Nyx Does

Nyx currently includes:

- provider routing across Ollama, OpenAI-compatible HTTP backends, and CLI tools like Codex CLI
- Hyprland/Wayland bridge integration for window state, screenshots, commands, notifications, and microphone recording
- GTK launcher and panel UI with history, search, and multi-monitor-aware placement
- persistent local conversation threads in the overlay history sidebar
- project-aware notes, tasks, memory, macros, and skills
- local RAG with ChromaDB and Ollama embeddings
- screen-context analysis with vision-capable providers
- Git/GitHub, calendar, system monitor, and live web lookup modules
- cross-device sync helpers for Git-managed notes/memory and Syncthing-managed RAG indexes
- local speech-to-text through `whisper.cpp`

## Status

Nyx has been built through Phase 22 of the architecture plan.

This repository is usable now, but it is still pre-release software. The
Windows port and the public-release phase are not complete yet.

## Requirements

Core runtime requirements:

- Python 3.11+
- Linux desktop environment with Wayland
- Hyprland for the current bridge implementation

GTK launcher requirements on Arch Linux:

```bash
sudo pacman -S python-gobject gtk4 gtk4-layer-shell
```

Voice input requirements:

- `whisper.cpp` CLI
- a local ggml Whisper model file
- `pw-record` for live microphone capture
- `ffmpeg` if you want Nyx to accept non-WAV audio files

Optional service dependencies:

- Ollama for local text, embeddings, and optional local vision models
- SearXNG and/or Brave Search API for live web lookups
- Google Calendar OAuth or ADC / `gcloud` for calendar access
- `gh` for GitHub issue/PR flows
- Git and optionally Syncthing for cross-device sync

## Installation

### 1. Create the environment

```bash
git clone <your-repo-url> nyx
cd nyx
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. If you want the GTK launcher on Arch

Because `gi` comes from the system `python-gobject` package, a normal virtual
environment will not see it. Recreate the environment with system site
packages:

```bash
rm -rf .venv
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e .
```

### 3. Create the config directory

```bash
mkdir -p ~/.config/nyx
```

Nyx reads its config from:

```text
~/.config/nyx/config.toml
```

A public example config is included here:

[config.example.toml](/home/yayky/projects/AIAssistnat/examples/config.example.toml)

## Quick Start

Run one-shot CLI mode:

```bash
python3 -m nyx "hello"
```

Run the GTK launcher:

```bash
python3 -m nyx --launcher
```

Run the daemon and toggle the managed overlay on demand:

```bash
python3 -m nyx --daemon
python3 -m nyx --toggle-ui
```

Run live microphone input:

```bash
python3 -m nyx --voice
```

Transcribe one existing audio file:

```bash
python3 -m nyx --voice-file /path/to/input.wav
```

## UI Controls

Launcher and panel controls currently implemented:

- `Enter` submits
- `Shift+Enter` inserts a newline
- `Ctrl+H` toggles panel/history mode
- `Ctrl+,` opens the settings sidebar
- `Ctrl+C` copies the last response
- `Escape` closes the launcher process

The panel now stores conversation threads locally at:

```text
~/.local/state/nyx/conversations.json
```

That history survives launcher restarts and is searchable from the sidebar.

### Summon hotkey on Hyprland

Nyx's configurable summon command is:

```bash
python3 -m nyx --toggle-ui
```

If the daemon is running in the background, bind it in `hyprland.conf`, for example:

```text
bind = SUPER, A, exec, /path/to/your/venv/bin/python -m nyx --toggle-ui
```

Typical startup on login is:

```text
exec-once = /path/to/your/venv/bin/python -m nyx --daemon
```

The desired key combination lives in `[ui].summon_hotkey`, and the settings sidebar exposes it directly, but Hyprland still owns the actual compositor bind.

## Configuration Overview

Important config areas:

- `[models]` controls your default provider, fallback chain, and all configured providers
- `[voice]` controls `whisper.cpp` and lets you fully disable voice input
- `[notes]` controls the notes tree and project layout
- `[rag]` controls the ChromaDB path and embedding model
- `[web]` configures SearXNG and Brave fallback
- `[calendar]` configures Google Calendar access
- `[sync]` configures Git-managed notes sync and Syncthing-managed RAG sync
- `[ui]` controls overlay sizing and monitor placement
- `[system]` controls destructive-command confirmation and YOLO mode

### Multi-monitor placement

`[ui].overlay_monitor` currently supports:

- `focused`
- `primary`
- one-based numeric monitor indices like `1` or `2`
- named outputs such as `eDP-2`

### Cross-device sync

Nyx splits sync by data type:

- Git syncs `~/notes/` and a mirrored copy of global memory at `~/notes/memory.md`
- Syncthing syncs the local RAG index at `~/.local/share/nyx/rag`

Nyx does not sync the whole `~/.config/nyx/` tree across devices. That
directory contains machine-local and sensitive files such as OAuth tokens and
credentials.

## Optional Integrations

### Ollama

Nyx can use Ollama for:

- local text models
- local embeddings for RAG
- local vision models

### Google Calendar

Nyx supports:

- desktop OAuth client flow
- ADC / `gcloud auth application-default login`

### Web lookup

Nyx uses:

- SearXNG as the primary backend
- Brave Search API as fallback

## Project Layout

Nyx stores user data in plain files where practical:

```text
~/.config/nyx/
~/notes/
~/notes/projects/<project>/
~/.local/share/nyx/rag/
```

That means notes, tasks, macros, and much of the assistant context stay easy to
inspect, edit, and sync.

## Development

Run tests:

```bash
python3 -m pytest
```

Bytecode / import sanity check:

```bash
python3 -m compileall nyx tests
```

## Current Scope And Limits

- Linux-first
- Hyprland/Wayland bridge only for now
- no public release packaging yet
- no Windows bridge/UI yet
- some integrations still depend on local services or external credentials

## Notes

- Local user configuration lives under `~/.config/nyx/`
- Project notes live under `~/notes/projects/`
- Cross-device sync expects your notes directory to already be a Git repository if you want Nyx to automate commit/pull/push
- The GTK launcher requires system PyGObject bindings (`gi`), `gtk4`, and `gtk4-layer-shell`
- Voice input requires a local `whisper.cpp` CLI plus a ggml model file configured under `[voice]`
- Live microphone input on Linux uses PipeWire `pw-record`; set `[voice].enabled = false` to disable all Nyx voice input
