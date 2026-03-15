# PROJECT: Nyx — Linux AI Assistant (Hyprland)
> LLM-CONTEXT FILE — Written to be used as context in future AI sessions.
> Keep this file updated as the project evolves. It is the single source of truth.
> All decisions are finalized unless marked [OPEN].

---

## 1. PROJECT OVERVIEW

**Name:** Nyx
**Binary:** `nyx`
**Config:** `~/.config/nyx/`
**Systemd service:** `nyx.service`

**What this is:** A local-first, context-aware AI assistant daemon for Linux (Hyprland/Wayland). Accepts input via a GTK4 hotkey overlay, CLI, and voice (STT). Autonomously gathers context from the screen, active window, RAG knowledge base, and persistent memory — then routes intent to the appropriate module and executes actions on the system.

**Core philosophy:**
- Context-first — Nyx gathers context itself rather than waiting to be told.
- Local by default, cloud when needed. Degrades gracefully offline.
- Full system autonomy — AI generates and runs commands, not a fixed menu.
- Destructive operations always require confirmation (unless YOLO mode active).
- Project-aware — all knowledge, tasks, macros, and memory organized by project.
- Modular and extensible — core modules + user-defined skills.
- Provider-agnostic — any model source plugs in via a unified provider interface.
- Platform-abstracted — OS-specific code isolated behind SystemBridge for future Windows port.
- Config-driven — all behavior controlled via `~/.config/nyx/config.toml`.

---

## 2. TARGET HARDWARE

| Device | CPU | GPU | VRAM | RAM | Default Model |
|---|---|---|---|---|---|
| Laptop | Intel i7-12700H | RTX 4050 Mobile | 6GB | 16GB | `qwen2.5:7b` (ollama) |
| Desktop PC | Intel i7-12700K | RTX 3080 Ti | 12GB | 64GB | `qwen2.5:14b` (ollama) |

Default provider set in `config.toml`, overridable with `--model <provider-name>` CLI flag.

---

## 3. FINALIZED DECISIONS

| Decision | Choice | Rationale |
|---|---|---|
| Project name | Nyx | Short, CLI-friendly, fits Hyprland ecosystem |
| Primary language | Python 3.11+ (asyncio) | Fast iteration, rich ecosystem |
| Local LLM runtime | Ollama | Easy model management, local HTTP API |
| Default model (laptop) | `qwen2.5:7b` via ollama | Fits 6GB VRAM, strong tool use |
| Default model (PC) | `qwen2.5:14b` via ollama | Fits 12GB VRAM, better reasoning |
| Model system | Named provider abstraction | Swap any model source without code changes |
| CLI subprocess support | Codex CLI, Claude Code, Gemini CLI | Spawn as subprocess, communicate via stdin/stdout JSON |
| Cloud fallback | Anthropic Claude API | Best complex reasoning |
| Fallback chain | Configurable ordered list | Tries providers in sequence on failure |
| STT | `whisper.cpp` (local) | No API cost, fast, private |
| TTS | None | Voice input only |
| Overlay UI (Linux) | GTK4 + `gtk4-layer-shell` | User already uses GTK4 |
| Overlay UI (Windows) | Tauri (Rust + WebView) | Native `.exe`, cross-platform, polished |
| System abstraction | `SystemBridge` interface | OS-specific code isolated, enables Windows port |
| Confirmation UX | GTK4 modal dialog (Linux) / Tauri modal (Windows) | Shows exact command before execution |
| Notes storage | Plain markdown in `~/notes/` | Portable, RAG-compatible |
| Notes organization | Project-aware RAG | AI auto-organizes by project |
| Web search primary | SearXNG (self-hosted) | Private, free, no rate limits |
| Web search fallback | Brave Search API | Fallback on timeout/empty results |
| Config format | TOML | Human-readable, Python-native |
| Cross-device sync | Git (notes/memory) + Syncthing (RAG index) | Version history + fast index sync |
| GitHub auth | SSH + GitHub CLI (`gh`) | Push/pull + PR/issue management |
| Macro format | Python scripts | Full power, AI-generatable, git-tracked |
| Task storage | `tasks.md` per project | Self-contained, RAG-indexed |
| Calendar | Google Calendar API (free, OAuth) | Live read/write |
| Skill discovery | Auto-discovered + disable list in config | Drop-in with off switch |
| New project creation | AI suggests → user confirms | No silent project creation |
| Daemon startup | `exec-once = nyx` → starts `nyx.service` | systemd is always process owner |
| Crash recovery | `Restart=on-failure` in systemd unit | Auto-restart with 3s cooldown |
| Context compaction | README.md frontmatter summaries | Cheap pre-filter before full RAG |
| Multi-monitor | Focused monitor default, configurable | `overlay_monitor` in config |
| Offline/degraded mode | Status bar warning + local fallback | No silent failures |
| Security | `blacklist.txt` + hardcoded paths + YOLO mode | Floor always enforced |

---

## 4. SYSTEM ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────┐
│                        INPUT LAYER                          │
│   GTK4 Overlay (Super+A)  │  CLI  │  Voice (whisper.cpp)   │
└───────────────────────────┬─────────────────────────────────┘
                            │ raw text string
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                   CONTEXT AGGREGATOR                        │
│  ├── Active window (SystemBridge.get_active_window())      │
│  ├── Project summaries (README.md frontmatter — all)       │
│  ├── RAG query (relevant projects only)                     │
│  ├── Screen capture (SystemBridge.screenshot() → vision)   │
│  └── Persistent memory (memory.md + project context.md)    │
└───────────────────────────┬─────────────────────────────────┘
                            │ enriched context bundle
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                      CORE DAEMON                            │
│                (Python asyncio — nyx.service)               │
│   Intent Router                                             │
│   ├── Classifies intent → module                           │
│   ├── Selects provider tier (local / cloud / cli)          │
│   └── Checks blacklist before any execution                │
└──┬──────┬──────┬──────┬──────┬──────┬──────┬──────────────┘
   ▼      ▼      ▼      ▼      ▼      ▼      ▼
┌─────┐┌─────┐┌─────┐┌─────┐┌─────┐┌─────┐┌──────┐
│ Sys ││Notes││ Web ││ Git ││Task ││Macro││Skills│
│Ctrl ││& RAG││Srch ││& GH ││& Cal││  s  ││(ext) │
└──┬──┘└─────┘└─────┘└─────┘└─────┘└─────┘└──────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│                    SYSTEM BRIDGE LAYER                      │
│         (all OS-specific calls isolated here)               │
│                                                             │
│  Linux impl:              Windows impl (future):            │
│  HyprlandBridge           WindowsBridge                    │
│  - hyprctl dispatch       - win32api / PowerShell           │
│  - grim (screenshot)      - PIL.ImageGrab                   │
│  - hyprctl activewindow   - win32gui.GetForegroundWindow    │
│  - bash subprocess        - PowerShell subprocess           │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                   MODEL PROVIDER LAYER                      │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐ │
│  │  HTTP API   │  │ OpenAI-compat│  │  Subprocess CLI    │ │
│  │ ollama      │  │ lm-studio    │  │  codex-cli         │ │
│  │ anthropic   │  │ localai      │  │  claude-code       │ │
│  │ openai      │  │ any server   │  │  gemini-cli        │ │
│  └─────────────┘  └──────────────┘  └────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. SYSTEM BRIDGE ABSTRACTION

**Critical architectural decision:** all OS-specific code lives behind a `SystemBridge` interface. No module ever calls `hyprctl`, `grim`, or `subprocess` with Linux-specific commands directly. This is what makes the Windows port possible without rewriting core logic.

### Interface Definition

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class WindowInfo:
    app_name: str
    window_title: str
    workspace: str | None

class SystemBridge(ABC):

    # Window management
    @abstractmethod
    async def get_active_window(self) -> WindowInfo: ...

    @abstractmethod
    async def move_window_to_workspace(self, window: str, workspace: str) -> bool: ...

    @abstractmethod
    async def list_windows(self) -> list[WindowInfo]: ...

    # Screen
    @abstractmethod
    async def screenshot(self, path: str) -> bool: ...

    # Process / shell
    @abstractmethod
    async def run_command(self, command: str, confirm_if_destructive: bool = True) -> str: ...

    @abstractmethod
    async def list_processes(self) -> list[dict]: ...

    @abstractmethod
    async def kill_process(self, identifier: str) -> bool: ...

    # System
    @abstractmethod
    async def set_brightness(self, percent: int) -> bool: ...

    @abstractmethod
    async def set_volume(self, percent: int) -> bool: ...

    @abstractmethod
    async def get_system_stats(self) -> dict: ...  # CPU, RAM, disk, etc.

    # Notifications
    @abstractmethod
    async def notify(self, title: str, body: str) -> None: ...
```

### Linux Implementation (Phase 1)

```python
class HyprlandBridge(SystemBridge):

    async def get_active_window(self) -> WindowInfo:
        result = await asyncio.create_subprocess_exec(
            "hyprctl", "activewindow", "-j",
            stdout=PIPE, stderr=PIPE
        )
        data = json.loads(await result.stdout.read())
        return WindowInfo(
            app_name=data.get("class", ""),
            window_title=data.get("title", ""),
            workspace=str(data.get("workspace", {}).get("id"))
        )

    async def screenshot(self, path: str) -> bool:
        proc = await asyncio.create_subprocess_exec("grim", path)
        await proc.wait()
        return proc.returncode == 0

    async def run_command(self, command: str, confirm_if_destructive: bool = True) -> str:
        # blacklist check, destructive check, then execute
        ...
```

### Windows Implementation (future, Phase W)

```python
class WindowsBridge(SystemBridge):

    async def get_active_window(self) -> WindowInfo:
        import win32gui, win32process
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        # extract app name from process
        ...

    async def screenshot(self, path: str) -> bool:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        img.save(path)
        return True

    async def run_command(self, command: str, confirm_if_destructive: bool = True) -> str:
        # PowerShell subprocess
        ...
```

### Bridge Initialization

```python
# daemon entrypoint — detects platform, loads correct bridge
import platform

def get_system_bridge() -> SystemBridge:
    if platform.system() == "Linux":
        return HyprlandBridge()
    elif platform.system() == "Windows":
        return WindowsBridge()
    else:
        raise NotImplementedError(f"Platform {platform.system()} not supported yet")
```

All modules receive the bridge as a dependency — they never import platform-specific code directly.

---

## 6. MODEL PROVIDER SYSTEM

### Provider Abstraction

```python
class ModelProvider:
    name: str
    type: str  # "ollama" | "anthropic" | "openai" | "openai-compat" | "subprocess-cli"

    async def query(self, prompt: str, context: dict) -> str: ...
    async def is_available(self) -> bool: ...
```

### Provider Types

#### HTTP API Providers
| Type | Examples | Auth |
|---|---|---|
| `ollama` | qwen2.5, mistral, llama3 | None (local) |
| `anthropic` | claude-sonnet, claude-opus | `ANTHROPIC_API_KEY` env var |
| `openai` | gpt-4o, o3, codex-mini | `OPENAI_API_KEY` env var |
| `openai-compat` | LM Studio, LocalAI, any compatible server | Configurable |

#### Subprocess CLI Providers
Nyx spawns binary as child process, communicates via stdin/stdout JSON. User's existing CLI auth used — Nyx never touches credentials.

| Provider | Binary | Auth | Subscription |
|---|---|---|---|
| `codex-cli` | `codex` | `codex login` | ChatGPT Plus / API |
| `claude-code` | `claude` | `claude login` | Claude Max / API |
| `gemini-cli` | `gemini` | `gemini auth` | Google AI |

**Subprocess flow:**
```
asyncio.create_subprocess_exec(binary, *args, stdin=PIPE, stdout=PIPE)
    → write prompt to stdin
    → read JSONL from stdout
    → parse response
    → kill process
```

Missing binaries → provider marked unavailable on startup, skipped silently.

### Fallback Chain
```toml
[models]
default = "ollama-local"
fallback = ["anthropic", "codex-cli"]
```
Tried in order. All fail → `⚠ all providers unavailable` in overlay.

### Model Selection
- Per query: `nyx --model codex-cli "refactor this"`
- Permanent: edit `default` in config
- From overlay: `@model codex-cli` prefix (planned)

### Status Bar Display
```
● ollama  qwen2.5:7b       ← local
● codex   chatgpt-4o       ← CLI subprocess
● claude  claude-sonnet    ← API
⚠ degraded — local only   ← cloud down
```

---

## 7. DAEMON LIFECYCLE

### Startup Flow
```
Hyprland starts
    → exec-once = nyx         (hyprland.conf)
    → nyx checks nyx.service
    → systemctl --user start nyx.service
    → systemd owns process, handles crash recovery
```

### systemd Unit (`~/.config/systemd/user/nyx.service`)
```ini
[Unit]
Description=Nyx AI Assistant Daemon
After=graphical-session.target

[Service]
ExecStart=/usr/local/bin/nyx --daemon
Restart=on-failure
RestartSec=3
Environment=WAYLAND_DISPLAY=wayland-1

[Install]
WantedBy=graphical-session.target
```

Logs: `journalctl --user -u nyx.service`

---

## 8. CONTEXT SYSTEM

### Context Compaction
Each project's `README.md` has YAML frontmatter:
```yaml
---
summary: "Godot 4 action RPG with procedural dungeons. Current focus: combat system."
last_updated: 2025-03-11
tags: [godot, gamedev, gdscript]
---
```
Flow: load all summaries (cheap) → rank by relevance → full RAG on top 1-3 projects only.

### Screen Context
- `SystemBridge.screenshot()` → `/tmp/nyx-screen.png`
- `SystemBridge.get_active_window()` always read
- Autonomous on low-confidence intent → vision model → retry
- If still unclear → ask user

### RAG System
- **Vector DB:** `chromadb` (local)
- **Embedding:** `nomic-embed-text` via Ollama
- **Index:** `~/.local/share/nyx/rag/` (Syncthing-synced)
- **Collections:** one per project + global inbox

### Persistent Memory
- **Global:** `~/.config/nyx/memory.md`
- **Per-project:** `~/notes/projects/<n>/context.md`
- AI proposes updates after significant sessions → user accepts/edits/skips

---

## 9. PROJECT STRUCTURE

```
~/notes/
├── inbox.md
├── memory.md
└── projects/
    └── <project-name>/
        ├── README.md        ← AI-maintained, YAML frontmatter
        ├── notes.md
        ├── tasks.md         ← - [ ] / - [x]
        ├── context.md       ← AI project memory
        ├── progress.md      ← AI-maintained changelog
        └── macros/
            └── *.py
```

New project: AI proposes → user confirms → folder + files created, RAG collection initialized.

**Sync:**
| Data | Sync |
|---|---|
| `~/notes/` + `~/.config/nyx/` | Git (private GitHub) |
| `~/.local/share/nyx/rag/` | Syncthing (laptop ↔ PC) |

---

## 10. SECURITY MODEL

- **`~/.config/nyx/blacklist.txt`** — patterns never executed, never bypassed
- **Hardcoded protected paths:** `~/.ssh/`, `~/.gnupg/`, `/etc/`, `/boot/`, `/sys/`, `/proc/`
- **YOLO mode** (`--yolo` flag) — skips GTK4 modal, never bypasses blacklist, shows `⚡ YOLO` in status bar

---

## 11. FEATURE MODULES

### 11.1 System Control
- AI generates commands via SystemBridge (never direct OS calls)
- Destructive → modal confirmation (unless YOLO)
- Blacklist checked before every execution

### 11.2 Notes & RAG
- Captures → inbox → background classification → project routing
- Semantic search, project collections, AI proposes new projects

### 11.3 Web / Info Lookup
- SearXNG primary, Brave API fallback (3s timeout)
- URL summarization via cloud provider

### 11.4 Voice Input
- `whisper.cpp` subprocess, no TTS

### 11.5 Git & GitHub
- SSH push/pull, `gh` CLI for PR/issue management
- Commit, push (modal confirm), pull, PR creation, issue listing, diff summary

### 11.6 Tasks & Calendar
- `tasks.md` per project, RAG-indexed
- Google Calendar API, OAuth, offline `.ical` fallback

### 11.7 Macros
- Python scripts, project-linked or global
- AI-generated, user-editable, docstring defines name/triggers/scope

### 11.8 Screen Context
- `SystemBridge.screenshot()` + `get_active_window()`
- Autonomous on low confidence, explicit on user request
- Vision via cloud provider

### 11.9 System Monitor
- Natural language conditions → `monitors.toml`
- `psutil` polling via asyncio scheduled tasks

### 11.10 Skills
- `~/.config/nyx/skills/*.py` auto-discovered
- Disable list in config
- 4 trigger modes: keyword, explicit, AI intent, scheduled

---

## 12. OFFLINE / DEGRADED MODE

| Scenario | Behavior |
|---|---|
| Cloud providers down | `⚠ degraded — local only` in status bar |
| One provider fails | Silent fallback to next in chain |
| All providers fail | Clear message in overlay |
| Google Calendar down | Inform user, fall back to cached `.ical` |
| Full offline | All local features work normally |

---

## 13. GTK4 OVERLAY UI (Linux)

Follows system GTK theme automatically. GTK4 + `gtk4-layer-shell`.

### Launcher (top center)
```
┌──────────────────────────────────────────────────────────────┐
│  ⠋ ollama  qwen2.5:7b  [tokens: 142]  [⊞]  [⚠ degraded]   │
├──────────────────────────────────────────────────────────────┤
│  > _                                                         │
├──────────────────────────────────────────────────────────────┤
│  Response (plain text, monospace, selectable)                │
└──────────────────────────────────────────────────────────────┘
```

### Panel (left sidebar)
```
┌────────────────┬─────────────────────────────────────────────┐
│  HISTORY       │  ● ollama  qwen2.5:7b  [tokens: —]  [⊞]   │
│  ┌──────────┐  │─────────────────────────────────────────────│
│  │🔍 Search │  │  > _                                        │
│  └──────────┘  │─────────────────────────────────────────────│
│  Session 3     │  Response (markdown + syntax highlight)     │
│  Today 14:22   │                                             │
└────────────────┴─────────────────────────────────────────────┘
```

### Keyboard Shortcuts
| Shortcut | Action |
|---|---|
| `Super+A` | Summon / dismiss launcher |
| `Escape` | Close overlay |
| `Ctrl+H` | Open panel / history |
| `Ctrl+C` | Copy last response to clipboard |
| `Enter` | Send input |
| `Shift+Enter` | Newline in input |
| `↑ / ↓` | Cycle input history |

**Multi-monitor:** `overlay_monitor = "focused" | "primary" | "1" | "2"` — default: `"focused"`

---

## 14. WINDOWS PORT PLAN

### Overview
Nyx Linux is open source and free. Nyx Windows is a paid desktop app built with Tauri, targeting developers and power users who want a local-first AI assistant with no chat-app overhead.

### What's shared (~80% of codebase)
- Python asyncio daemon and intent router
- All model providers (Ollama, Claude API, Codex CLI, etc.)
- RAG system (chromadb, nomic-embed-text)
- Git/GitHub module
- Notes, tasks, memory system
- Web lookup
- Skills and macros
- System monitor (psutil is cross-platform)
- Calendar (Google API)

### What changes for Windows

| Component | Linux | Windows |
|---|---|---|
| Overlay UI | GTK4 + gtk4-layer-shell | Tauri (Rust + WebView, ships as `.exe`) |
| System control | HyprlandBridge | WindowsBridge (PowerShell + win32api) |
| Screenshot | `grim` | `PIL.ImageGrab` |
| Active window | `hyprctl activewindow` | `win32gui.GetForegroundWindow()` |
| Daemon | systemd user service | Windows Service or startup task |
| Hotkey | hyprland.conf bind | Global hotkey via `pynput` |
| Installer | — | NSIS or MSI installer |

### Why Tauri for Windows UI
- Ships as a native `.exe` with a proper installer — what Windows users expect
- WebView2 (already installed on all modern Windows) renders HTML/CSS/JS
- Significantly smaller binary than Electron (~5MB vs ~150MB)
- Cross-platform: same UI code works on Windows, macOS (future)
- Looks polished and native — not a Linux port that feels wrong
- Actively maintained, used by modern desktop AI tools

### Effort estimate
- Linux Nyx complete: ~3 months
- Windows port on top: ~6–8 weeks
- Mostly spent on: Tauri UI rewrite + WindowsBridge implementation

**Key requirement:** SystemBridge abstraction must be in place before Windows work starts. If modules call OS-specific code directly, the port becomes a rewrite.

---

## 15. BUSINESS STRATEGY

### Competitive Landscape

No direct equivalent exists. Partial overlaps:

| Product | Gap vs Nyx |
|---|---|
| AnythingLLM | Chat app UI, no system control, no project-aware RAG, not an OS layer |
| PyGPT | Chat app, no system control, no screen context |
| Braina (Windows) | No RAG, no project structure, no git, Windows-only |
| screenpipe | Screen context only, not a full assistant |
| ValeDesk | Basic chat wrapper, no system control or project structure |
| Hyprland MCP Server | Single-purpose window control, no memory or RAG |

**Nyx's differentiation:** OS-layer daemon (not a chat app) + project-aware RAG + autonomous screen context + full system control + git + cross-device sync — assembled into one coherent system built for how power users actually work.

### Release Strategy

**Phase 1: Build reputation (Linux, open source)**
- Release on GitHub under MIT license
- Well-written README with demo GIF is the most important asset
- Open source builds community, credibility, and the funnel for Windows sales

**Phase 2: Validate with community**
- Reddit: r/hyprland, r/unixporn, r/LocalLLaMA, r/selfhosted
- Hacker News: "Show HN: Nyx, a context-aware AI assistant daemon for Hyprland"
- One polished 3–5 minute demo video (not a channel — just one video)
- Target mid-size YouTubers (10k–100k subscribers) covering Hyprland, Linux setups, local AI
  — they actively look for interesting projects and respond to emails
  — short genuine email: "built X, thought your audience might find it interesting, here's a demo"
  — avoid large creators, they get flooded

**Phase 3: Ship Windows paid app**
- Tauri-based native app, `.exe` installer
- One-time purchase pricing (see below)
- Linux remains free and open source indefinitely

### Pricing

**Model: One-time purchase, not subscription.**

Rationale:
- Core value prop is privacy and local-first — subscriptions contradict that message
- Competing tools (LM Studio, Jan, GPT4All) are free — price gap must be justified by quality
- Subscriptions require billing infrastructure, churn management — too much overhead solo
- One-time feels honest to the audience

**Target price: $29–$39**
- Under $25 feels disposable
- Over $50 significantly increases purchase friction for a new product with no brand
- $29 and $39 are proven psychological price points for developer tools
- Optional tiering: free core features, paid for advanced (cloud sync, priority support)

### Marketing (zero existing platform)

In priority order:

1. **GitHub README + demo GIF** — most important asset, lives forever, gets discovered organically
2. **Reddit posts** (r/hyprland, r/unixporn, r/LocalLLaMA, r/selfhosted) — "I built this" posts with demo video regularly get 500–2000 upvotes in these communities
3. **Hacker News Show HN** — if it lands on the front page, expect 10,000+ visitors in a day
4. **One YouTube demo video** — 3–5 min, shows the real thing working end to end, more impactful than any creator outreach
5. **Targeted creator emails** — mid-size creators (10k–100k) in Hyprland/Linux/local-AI space, short genuine email with demo link

---

## 16. CONFIGURATION FILE

Location: `~/.config/nyx/config.toml`

```toml
[models]
default = "ollama-local"
fallback = ["anthropic", "codex-cli"]

[[models.providers]]
name = "ollama-local"
type = "ollama"
model = "qwen2.5:7b"
host = "http://localhost:11434"

[[models.providers]]
name = "ollama-big"
type = "ollama"
model = "qwen2.5:14b"
host = "http://localhost:11434"

[[models.providers]]
name = "anthropic"
type = "anthropic"
model = "claude-sonnet-4-5"
api_key_env = "ANTHROPIC_API_KEY"

[[models.providers]]
name = "openai"
type = "openai"
model = "gpt-4o"
api_key_env = "OPENAI_API_KEY"

[[models.providers]]
name = "codex-cli"
type = "subprocess-cli"
binary = "codex"
args = ["exec", "--json", "-"]
timeout_seconds = 60

[[models.providers]]
name = "claude-code"
type = "subprocess-cli"
binary = "claude"
args = ["--output-format", "json", "-p"]
timeout_seconds = 60

[[models.providers]]
name = "gemini-cli"
type = "subprocess-cli"
binary = "gemini"
args = ["--json"]
timeout_seconds = 60

[[models.providers]]
name = "lm-studio"
type = "openai-compat"
model = "local-model"
base_url = "http://localhost:1234/v1"
api_key_env = "LM_STUDIO_KEY"

[voice]
whisper_model = "base"
whisper_binary = "whisper"

[notes]
notes_dir = "~/notes"
inbox_file = "inbox.md"
projects_dir = "~/notes/projects"
auto_sort = true

[rag]
db_path = "~/.local/share/nyx/rag"
embed_model = "nomic-embed-text"

[web]
searxng_url = "http://localhost:8080"
brave_api_key = ""
fallback_timeout_seconds = 3

[git]
use_ssh = true
gh_cli = true

[calendar]
provider = "google"
credentials_path = "~/.config/nyx/google_credentials.json"

[skills]
disabled = []

[monitors]
poll_interval_seconds = 30

[ui]
overlay_anchor = "top-center"
overlay_monitor = "focused"
launcher_width = 700
launcher_height = 300
panel_width = 400
font = "monospace 11"
summon_hotkey = "Super+A"

[system]
confirm_destructive = true
yolo = false
screenshot_tmp = "/tmp/nyx-screen.png"
```

---

## 17. PROJECT PHASES

### Linux Phases

| Phase | Description | Status |
|---|---|---|
| Phase 0 | Planning & architecture | ✅ Complete |
| Phase 1 | Core daemon + systemd service + CLI + Intent Router stub + SystemBridge interface | ⬜ Not started |
| Phase 2 | Model provider layer (HTTP + subprocess-cli drivers) | ⬜ Not started |
| Phase 3 | HyprlandBridge implementation (all OS calls) | ⬜ Not started |
| Phase 4 | GTK4 launcher UI (input + response + status bar + indicators) | ⬜ Not started |
| Phase 5 | GTK4 panel mode (sidebar + sessions + search) | ⬜ Not started |
| Phase 6 | System control module (via SystemBridge) | ⬜ Not started |
| Phase 7 | Notes module + inbox auto-sort | ⬜ Not started |
| Phase 8 | RAG system (chromadb + nomic-embed-text + project collections) | ⬜ Not started |
| Phase 9 | Context compaction (README frontmatter + smart pre-filter) | ⬜ Not started |
| Phase 10 | Persistent memory (global + per-project) | ⬜ Not started |
| Phase 11 | Screen context module (SystemBridge.screenshot + vision) | ⬜ Not started |
| Phase 12 | Git + GitHub module (SSH + gh CLI) | ⬜ Not started |
| Phase 13 | Tasks module (tasks.md per project) | ⬜ Not started |
| Phase 14 | Calendar module (Google Calendar API + offline fallback) | ⬜ Not started |
| Phase 15 | Macros system (Python scripts + project-linked + AI generation) | ⬜ Not started |
| Phase 16 | Skills system (auto-discovery + 4 trigger modes) | ⬜ Not started |
| Phase 17 | System monitor (proactive alerts + psutil) | ⬜ Not started |
| Phase 18 | Voice input / STT (whisper.cpp) | ⬜ Not started |
| Phase 19 | Web lookup module (SearXNG + Brave fallback) | ⬜ Not started |
| Phase 20 | Cloud fallback routing + degraded mode | ⬜ Not started |
| Phase 21 | Cross-device sync (Git automation + Syncthing config) | ⬜ Not started |
| Phase 22 | Multi-monitor support | ⬜ Not started |
| Phase 23 | Public release (GitHub, Reddit, HN, demo video) | ⬜ Not started |

### Windows Phases (after Linux stable)

| Phase | Description | Status |
|---|---|---|
| Phase W1 | WindowsBridge implementation | ⬜ Not started |
| Phase W2 | Tauri UI (launcher + panel, matches Linux feature parity) | ⬜ Not started |
| Phase W3 | Windows daemon (Service or startup task) | ⬜ Not started |
| Phase W4 | Windows global hotkey | ⬜ Not started |
| Phase W5 | NSIS/MSI installer | ⬜ Not started |
| Phase W6 | Paid release ($29–$39 one-time) | ⬜ Not started |

---

## 18. OPEN QUESTIONS

None — all planning questions fully resolved as of Session 7.

---

## 19. SESSION LOG

### Session 1 — Initial Planning
- Core use cases: system control, notes/tasks, web lookup, voice input
- Hybrid AI, dual input, STT only, plain markdown, initial architecture.

### Session 2 — Core Decisions Resolved
- Models, notes organization, GTK4 UI, SearXNG + Brave, GTK4 modal.

### Session 3 — Full UI/UX Resolved
- Adaptive overlay, sessions + search, GTK theme, markdown/plain split,
  full response at once, Super+A, keyboard shortcuts.

### Session 4 — Major Feature Expansion
- Screen context, RAG, persistent memory, cross-device sync, Git/GitHub,
  macros, tasks, calendar, system monitor, skills.
- Project structure defined.

### Session 5 — Foundation Decisions + Naming
- Named Nyx. Daemon lifecycle, context compaction, multi-monitor,
  degraded mode, security model (blacklist + YOLO).

### Session 6 — Model Provider System
- Unified ModelProvider abstraction, subprocess CLI providers
  (codex-cli, claude-code, gemini-cli), named provider config,
  fallback chain, --model flag, @model prefix planned.

### Session 7 — Commercial Strategy + Windows Port + SystemBridge
- Competitive analysis: no direct equivalent exists.
- Viability: Linux open source → build reputation → Windows paid app.
- Pricing: $29–$39 one-time purchase, not subscription.
- Marketing: GitHub README, Reddit (r/hyprland, r/unixporn, r/LocalLLaMA),
  Hacker News Show HN, one demo video, targeted mid-size creator emails.
- Windows port: ~6–8 weeks on top of Linux, Tauri for UI, shares ~80% of codebase.
- SystemBridge abstraction added: all OS-specific calls isolated behind interface.
  HyprlandBridge (Linux) and WindowsBridge (future) implement same interface.
  Critical: modules must never call hyprctl or OS-specific tools directly.
- Phase 1 updated to include SystemBridge interface implementation.

---

*Last updated: Session 7 — Planning complete. Zero open questions. Ready to build.*
