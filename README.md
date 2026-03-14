# Nyx

Nyx is a local-first, context-aware AI assistant daemon for Linux desktops.

## Current status

Nyx is being built in phases. The current implementation includes:

- provider routing across local and remote models
- Hyprland bridge integration
- GTK launcher and panel UI
- live web lookup through SearXNG with Brave fallback
- cross-device sync helpers for Git-managed notes and Syncthing-backed RAG sharing
- offline file-based voice input through `whisper.cpp`
- notes, memory, RAG, screen context, git/GitHub, tasks, calendar, macros, skills, and system monitor modules

## Development setup

```bash
sudo pacman -S python-gobject gtk4 gtk4-layer-shell
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python3 -m pytest
```

If you want to run the GTK launcher from the venv on Arch, recreate the venv with system site packages so `gi` is visible:

```bash
rm -rf .venv
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e .
```

## Running Nyx

One-shot CLI:

```bash
python3 -m nyx "hello"
```

One-shot microphone voice input:

```bash
python3 -m nyx --voice
```

One-shot voice transcription from a file:

```bash
python3 -m nyx --voice-file /path/to/input.wav
```

Launcher UI:

```bash
python3 -m nyx --launcher
```

## Notes

- Local user configuration lives under `~/.config/nyx/`
- Project notes live under `~/notes/projects/`
- Some features require local services or credentials, such as Ollama or Google Calendar
- Cross-device sync expects your notes directory to already be a Git repository if you want Nyx to automate commit/pull/push
- The GTK launcher requires system PyGObject bindings (`gi`), `gtk4`, and `gtk4-layer-shell`
- Voice input requires a local `whisper.cpp` CLI plus a ggml model file configured under `[voice]`
- Live microphone input on Linux uses PipeWire `pw-record`; set `[voice].enabled = false` to disable all Nyx voice input
