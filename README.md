# Nyx

Nyx is a local-first, context-aware AI assistant daemon for Linux desktops.

## Current status

Nyx is being built in phases. The current implementation includes:

- provider routing across local and remote models
- Hyprland bridge integration
- GTK launcher and panel UI
- offline file-based voice input through `whisper.cpp`
- notes, memory, RAG, screen context, git/GitHub, tasks, calendar, macros, skills, and system monitor modules

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python3 -m pytest
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
- Voice input requires a local `whisper.cpp` CLI plus a ggml model file configured under `[voice]`
- Live microphone input on Linux uses PipeWire `pw-record`; set `[voice].enabled = false` to disable all Nyx voice input
