# Nyx

Nyx is a local-first AI assistant daemon for Linux desktops.

It is built around a persistent local runtime instead of a browser tab: Nyx can
route prompts across local and cloud models, inspect desktop context through a
strict bridge layer, manage notes/tasks/macros/skills, and expose both a
one-shot CLI and a GTK overlay UI.

![Nyx demo](PreviewVid/demo.gif)

## Table Of Contents

- [What Nyx Is](#what-nyx-is)
- [Current Status](#current-status)
- [What Nyx Can Do](#what-nyx-can-do)
- [Requirements](#requirements)
- [Install](#install)
- [Quick Start](#quick-start)
- [Presets](#presets)
- [Configuration](#configuration)
- [UI And Hotkeys](#ui-and-hotkeys)
- [Feature Setup Guides](#feature-setup-guides)
- [Where Nyx Stores Things](#where-nyx-stores-things)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [License](#license)
- [Limitations](#limitations)

## What Nyx Is

Nyx is designed for a desktop workflow where the assistant is part of the
machine, not a web session.

The project is currently:

- Linux-first
- Wayland-first
- Hyprland-only for the active system bridge
- local-first for state, notes, history, and desktop integrations

Nyx is a good fit if you want:

- a background assistant you can summon with a compositor shortcut
- local notes, memory, tasks, macros, and skills
- model routing across Ollama, HTTP APIs, and CLI tools like Codex CLI
- a GTK overlay that keeps conversation history locally
- a setup you can inspect and edit with plain files

## Current Status

Nyx is usable now, but it is still alpha software.

Implemented so far:

- CLI mode
- daemon mode
- managed overlay UI
- standalone Workspace window shell
- local overlay conversation history
- notes, tasks, memory, macros, skills
- screen context and system bridge integrations
- web lookup, calendar, sync, and monitor modules
- local speech-to-text with `whisper.cpp`

Not done yet:

- non-Hyprland bridge support
- Windows support
- polished public release flow

License note:

- Nyx is source-available under `PolyForm-Noncommercial-1.0.0`
- noncommercial use, modification, and redistribution are allowed under that license
- commercial use requires a separate license from the author

## What Nyx Can Do

Current capabilities include:

- provider routing across:
  - Ollama
  - Anthropic
  - OpenAI
  - OpenAI-compatible backends
  - subprocess CLI providers such as Codex CLI
- Hyprland bridge integration for:
  - active window information
  - screenshots
  - notifications
  - command execution
  - microphone recording
- GTK launcher and sidebar UI with:
  - persistent local conversation threads
  - sidebar history
  - settings editor
  - multi-monitor placement
- standalone Workspace shell with:
  - project pane
  - thread pane
  - large work surface
  - dedicated Database navigation section
- project-aware features:
  - notes
  - tasks
  - memory
  - macros
  - skills
- local RAG using ChromaDB + Ollama embeddings
- screen-context analysis through vision-capable providers
- Git/GitHub, calendar, system monitor, and live web lookup modules
- cross-device sync helpers for Git and Syncthing workflows
- voice input through local `whisper.cpp`

## Requirements

### Core runtime

- Python 3.11+
- Linux desktop running Wayland
- Hyprland for the current desktop bridge

### Python package dependencies

Nyx installs these through `pip install -e .`:

- `chromadb`
- `google-api-python-client`
- `google-auth-httplib2`
- `google-auth-oauthlib`
- `httpx`
- `Pillow`
- `psutil`

### GTK launcher requirements

On Arch Linux:

```bash
sudo pacman -S python-gobject gtk4 gtk4-layer-shell
```

Important:

- `gi` comes from the system `python-gobject` package
- if you want the GTK launcher inside a virtualenv, use `--system-site-packages`

### Voice input requirements

- `whisper.cpp` CLI binary
- a local Whisper ggml model file
- `pw-record` for live microphone capture
- `ffmpeg` if you want non-WAV audio input

### Optional integrations

- Ollama for local text / embeddings / local vision
- SearXNG and optionally Brave Search API for web lookup
- Google Calendar credentials or ADC for calendar support
- `gh` for GitHub workflows
- Git and optionally Syncthing for cross-device sync

## Install

### 1. Clone the repo

```bash
git clone https://github.com/Yayky/nyx.git
cd nyx
```

### 2. Create the environment

If you only want CLI mode:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

If you want the GTK launcher on Arch Linux or another distro where PyGObject is
installed system-wide:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e .
```

### 3. Create the config file

Nyx reads config from:

```text
~/.config/nyx/config.toml
```

Start from the example file:

```bash
mkdir -p ~/.config/nyx
cp examples/config.example.toml ~/.config/nyx/config.toml
```

### 4. Choose a preset

Preset files live in:

```text
examples/presets/
```

Available starter presets:

- [codex-cli.toml](examples/presets/codex-cli.toml)
- [local-ollama.toml](examples/presets/local-ollama.toml)
- [desktop-full.toml](examples/presets/desktop-full.toml)

You can either:

1. copy one preset directly to `~/.config/nyx/config.toml`, or
2. use `examples/config.example.toml` and copy the sections you want

### 5. Install optional external tools

Typical desktop setup:

```bash
sudo pacman -S ffmpeg git github-cli
```

Then add the tools you actually want to use:

- Ollama
- `whisper.cpp`
- SearXNG
- `gcloud`
- Syncthing

## Quick Start

### One-shot CLI

```bash
source .venv/bin/activate
python3 -m nyx "hello"
```

### Override the provider for one prompt

```bash
python3 -m nyx --model codex-cli "summarize this project"
```

### Start the overlay directly

```bash
python3 -m nyx --launcher
```

### Open the Nyx Workspace window

```bash
python3 -m nyx --workspace
```

This launches the new long-session desktop workspace shell with:

- a project-first workspace section
- provider, mode, and access selectors
- a dedicated `Database` section in the left navigation

If you want to open the same window focused on the Database area first:

```bash
python3 -m nyx --admin
```

### Run Nyx as a background daemon

```bash
python3 -m nyx --daemon
```

Then toggle the managed overlay:

```bash
python3 -m nyx --toggle-ui
```

Show it explicitly without toggling:

```bash
python3 -m nyx --show-ui
```

Hide it explicitly:

```bash
python3 -m nyx --hide-ui
```

### Voice input from microphone

```bash
python3 -m nyx --voice
```

### Voice input from a file

```bash
python3 -m nyx --voice-file /path/to/input.wav
```

### CLI help

```bash
python3 -m nyx --help
```

## Presets

These presets are meant as practical starting points, not final configs.

### 1. Codex CLI only

Use:

- [codex-cli.toml](examples/presets/codex-cli.toml)

Best if you want:

- the fastest setup
- a CLI-first workflow
- no Ollama requirement

### 2. Local Ollama first

Use:

- [local-ollama.toml](examples/presets/local-ollama.toml)

Best if you want:

- local text generation by default
- Codex CLI as a fallback
- local embeddings for RAG

### 3. Full desktop setup

Use:

- [desktop-full.toml](examples/presets/desktop-full.toml)

Best if you want:

- overlay UI
- voice input
- web lookup
- calendar access
- sync helpers
- desktop-oriented defaults

## Configuration

### Main config file

Nyx reads and writes:

```text
~/.config/nyx/config.toml
```

You can change settings in two ways:

- edit `~/.config/nyx/config.toml` directly
- use the sidebar settings UI with `Ctrl+,`

### Example config

The public example lives here:

- [config.example.toml](examples/config.example.toml)

### Workspace shell settings

The standalone workspace window uses these `[ui]` settings:

- `workspace_width`
- `workspace_height`
- `workspace_sidebar_width`
- `workspace_thread_list_width`
- `workspace_detail_width`
- `workspace_default_mode`
- `workspace_default_access`

The shell-only UI state is stored separately at:

```text
~/.local/state/nyx/workspace_state.json
```

Tracked workspace projects are stored at:

```text
~/.local/state/nyx/workspace_projects.json
```

### Config sections

#### `[models]`

Controls:

- default provider
- fallback chain
- provider definitions

Supported provider types:

- `ollama`
- `anthropic`
- `openai`
- `openai-compat`
- `subprocess-cli`

Typical places to change:

- switch the default model/provider
- add API-backed providers
- add or remove CLI providers
- change fallback order

#### `[voice]`

Controls:

- whether voice is enabled
- `whisper.cpp` binary path
- Whisper model path

Typical places to change:

- fully disable voice input
- point Nyx at your `whisper-cli`
- move to a larger Whisper model for better transcription quality

#### `[notes]`

Controls:

- notes root
- inbox filename
- projects directory
- automatic note sorting

#### `[rag]`

Controls:

- local ChromaDB path
- embedding model

#### `[web]`

Controls:

- SearXNG URL
- Brave API key
- fallback timeout

#### `[calendar]`

Controls:

- provider
- auth mode
- credentials path
- default calendar
- multiple calendar ids
- include-all-calendars mode

Supported auth modes:

- `auto`
- `adc`
- `desktop-oauth`

#### `[sync]`

Controls:

- notes repo path
- memory mirror path
- Syncthing config/snippet paths
- Syncthing folder id

#### `[ui]`

Controls:

- overlay monitor selection
- popup width / height
- sidebar height
- sidebar inner widths
- conversation/composer height split
- summon hotkey label
- font
- wallpaper path
- backdrop settings
- theme overrides

Important:

- sidebar width is derived from `panel_history_width` and `panel_chat_width`
- `panel_width` is preserved for compatibility and rendered from those values

#### `[ui.theme]`

Optional manual overrides for:

- `text_primary`
- `text_muted`
- `accent_cool`
- `accent_warm`
- `border_primary`
- `border_soft`
- `bg_outer`
- `bg_panel`
- `bg_card`
- `bg_card_alt`
- `shadow_color`

If left blank, Nyx will try to derive them from your wallpaper.

#### `[system]`

Controls:

- destructive action confirmation
- `yolo`
- screenshot temp path

## UI And Hotkeys

### Keyboard controls

- `Enter` submits
- `Shift+Enter` inserts a newline
- `Ctrl+H` toggles panel/history mode
- `Ctrl+,` opens settings
- `Ctrl+C` copies the last response
- `Escape` closes the overlay process

### Hyprland summon setup

Run Nyx in the background:

```text
exec-once = /absolute/path/to/your/.venv/bin/nyx --daemon
exec-once = /bin/sh -lc 'sleep 2; /absolute/path/to/your/.venv/bin/nyx --show-ui'
```

Bind a summon key:

```text
bind = SUPER, A, exec, /absolute/path/to/your/.venv/bin/nyx --toggle-ui
```

Then reload Hyprland:

```bash
hyprctl reload
```

### Sidebar sizing

The current sidebar sizing controls are:

- `panel_height`
- `panel_history_width`
- `panel_chat_width`
- `panel_conversation_ratio`

These are available in:

- `~/.config/nyx/config.toml`
- the sidebar settings UI

## Feature Setup Guides

### Voice / `whisper.cpp`

1. Install or build `whisper.cpp`
2. Download a ggml Whisper model
3. Set:

```toml
[voice]
enabled = true
whisper_binary = "/full/path/to/whisper-cli"
whisper_model = "/full/path/to/ggml-small.bin"
```

Then test:

```bash
python3 -m nyx --voice
```

### Google Calendar

Nyx supports two routes:

- desktop OAuth client JSON
- ADC through `gcloud`

Desktop OAuth files:

- credentials: `~/.config/nyx/google_credentials.json`
- token: `~/.config/nyx/google_token.json`

ADC example:

```bash
gcloud auth application-default login \
  --client-id-file ~/.config/nyx/google_credentials.json \
  --scopes https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/calendar
```

Then use:

```toml
[calendar]
provider = "google"
auth_mode = "auto"
include_all_calendars = true
```

### Web lookup

Set SearXNG:

```toml
[web]
searxng_url = "http://localhost:8080"
```

Optional Brave fallback:

```toml
[web]
brave_api_key = "YOUR_BRAVE_API_KEY"
fallback_timeout_seconds = 3
```

### Cross-device sync

Nyx intentionally splits sync:

- Git for `~/notes/` and mirrored memory
- Syncthing for the local RAG index

Git side:

- initialize `~/notes` as a repo
- push it to your remote
- use Nyx sync commands afterward

Syncthing side:

- share `~/.local/share/nyx/rag`
- use folder id `nyx-rag`

## Where Nyx Stores Things

### Config and runtime data

- config: `~/.config/nyx/config.toml`
- calendar credentials: `~/.config/nyx/google_credentials.json`
- calendar token: `~/.config/nyx/google_token.json`
- overlay history: `~/.local/state/nyx/conversations.db`
- RAG data: `~/.local/share/nyx/rag`

### Notes and project data

- notes root: `~/notes/`
- inbox: `~/notes/inbox.md`
- projects: `~/notes/projects/<project>/`
- mirrored memory: `~/notes/memory.md`

### User-extensible code

- global macros: `~/.config/nyx/macros/`
- project macros: `~/notes/projects/<project>/macros/`
- skills: `~/.config/nyx/skills/`

## Troubleshooting

### `ModuleNotFoundError: No module named 'gi'`

Your virtualenv probably cannot see the system PyGObject packages.

Recreate it like this:

```bash
rm -rf .venv
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e .
```

### `whisper.cpp binary not found`

Set both values in `~/.config/nyx/config.toml`:

```toml
[voice]
enabled = true
whisper_binary = "/full/path/to/whisper-cli"
whisper_model = "/full/path/to/ggml-small.bin"
```

### Sidebar size is not what you expect

The sidebar width is controlled by:

- `panel_history_width`
- `panel_chat_width`

The total width is derived from those two values. The vertical split on the
right side is controlled by:

- `panel_conversation_ratio`

## Development

Run tests:

```bash
python3 -m pytest
```

Bytecode/import check:

```bash
python3 -m compileall nyx tests
```

Current package metadata is in:

- [pyproject.toml](pyproject.toml)

## License

Nyx is released under [PolyForm-Noncommercial-1.0.0](LICENSE).

That means:

- you can use, study, modify, and share Nyx for noncommercial purposes
- you cannot use Nyx commercially under this repository license
- commercial licensing stays with the author

If you need commercial use, contact the project owner for separate terms.

## Limitations

- Hyprland is the only implemented desktop bridge right now
- the launcher depends on system GTK/PyGObject packages
- Nyx is still alpha and the UI is still being actively refined
- some integrations require external services that Nyx does not install for you
- the repository license is source-available, not OSI open-source
