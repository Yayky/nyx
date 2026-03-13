## Phase 1 — Core Daemon Scaffold
**Date:** 2026-03-12
**Status:** Complete

### What was built
- Created the initial `nyx` Python package with `pyproject.toml`, module entrypoint, and console script metadata.
- Implemented strict TOML-backed configuration loading with documented defaults, path expansion, partial override merging, and unknown-key validation.
- Added startup logging, the Phase 1 asyncio daemon lifecycle, the CLI one-shot prompt flow, and the intent router stub.
- Established the `SystemBridge` interface, `WindowInfo`, `BridgeNotImplementedError`, a Phase 1 `StubBridge`, and platform bridge factory wiring.
- Added a checked-in `systemd/nyx.service` template and a Phase 1 test suite covering config, CLI, routing, bridge selection, and daemon behavior.

### Key decisions made
- Deferred the no-argument `nyx` service bootstrap helper so Phase 1 does not introduce Linux-specific `systemctl` behavior outside the bridge boundary.
- Stored provider-specific config fields in `ProviderConfig.options` to preserve the documented provider schema without prematurely locking in provider implementation details.

### Known issues / next steps
- `StubBridge` is intentionally temporary; Phase 3 must replace Linux stub behavior with `HyprlandBridge`.
- The router is deterministic and degraded by design until the model provider layer is added in Phase 2.

## Phase 2 — Model Provider Layer
**Date:** 2026-03-12
**Status:** Complete

### What was built
- Added the `nyx.providers` package with the `ModelProvider` abstraction, provider errors, query result types, and the `ProviderRegistry`.
- Implemented HTTP providers for Ollama, Anthropic, OpenAI, and OpenAI-compatible backends using `httpx.AsyncClient`.
- Implemented the subprocess CLI provider for `codex`, `claude`, `gemini`, and other config-defined CLIs using `asyncio.create_subprocess_exec`.
- Wired the provider registry into the CLI and intent router so `python -m nyx "prompt"` now routes through the configured provider chain instead of the Phase 1 placeholder.
- Added Phase 2 tests covering HTTP payload handling, subprocess output parsing, registry fallback selection, router degradation behavior, and CLI/provider integration.

### Key decisions made
- Added `httpx` as the first runtime dependency because the provider layer needs true async HTTP and the project forbids synchronous I/O in async paths.
- Used OpenAI-compatible `chat/completions` for both `openai` and `openai-compat` providers so the same core request path works with OpenAI-hosted and LM Studio-style backends.
- Made the subprocess CLI provider support both stdin-fed prompts and positional prompt arguments because the documented JSON/stdin flow matches `codex`, while the installed `claude` CLI currently expects `-p` plus a positional prompt for non-interactive JSON output.
- Chose `max_tokens = 1024` as the Anthropic default until token controls are added to config.

### Known issues / next steps
- Live provider behavior still needs manual verification against a real Ollama server and any API-backed providers you intend to use.
- The router still passes an empty context bundle; context aggregation and provider-aware routing decisions belong to later phases.
- Degraded mode exists only as router response behavior in CLI for now; overlay/status bar surfacing comes in later UI phases.

### Follow-up fixes
- Updated the subprocess CLI parser after real-environment verification showed that `codex exec --json` emits JSONL events with assistant text nested under `item.text`.
- Added coverage for Codex-style JSONL output so future parser changes continue to support the installed CLI event format.

## Phase 3 — HyprlandBridge Implementation
**Date:** 2026-03-12
**Status:** Complete

### What was built
- Added the real Linux `HyprlandBridge` with implementations for active window lookup, window listing, workspace moves, screenshots, command execution, process inspection, process termination, brightness control, volume control, system stats, and desktop notifications.
- Switched the Linux bridge factory path from the temporary stub to `HyprlandBridge` while keeping Windows on the stub bridge.
- Added bridge-specific errors for command failure, confirmation-required flows, and security violations.
- Added Phase 3 tests covering Hyprland output parsing, workspace dispatch resolution, command safety checks, and audio command wiring.
- Verified the real bridge against live read-only calls for active window detection, window listing, and system stats on the current machine.

### Key decisions made
- `hyprctl activewindow -j` occasionally returned an empty JSON object in the live environment, so the bridge now falls back to plain-text `hyprctl activewindow` and then to focused client history.
- `run_command()` now enforces blacklist and protected-path checks inside the bridge and blocks destructive commands unless YOLO mode is active, because confirmation UI does not exist yet in Phase 3.
- Used `notify-send`, `brightnessctl`, and `wpctl` as the Linux implementations for notifications, brightness, and volume because they are installed on the target machine and fit the bridge-only OS-call rule.

### Known issues / next steps
- Destructive command confirmation currently fails closed with a bridge exception until the GTK confirmation UX is implemented in later phases.
- Hyprland mutation paths such as moving windows, changing volume, and changing brightness are covered by unit tests but were not exercised live to avoid mutating the current desktop state during development.
- System control, context gathering, and UI layers still sit above the bridge and remain for later phases.

## Phase 4 — GTK4 Launcher UI
**Date:** 2026-03-12
**Status:** Complete

### What was built
- Added the Phase 4 GTK4 launcher overlay with `gtk4-layer-shell`, including a status row, prompt input, response area, provider/status indicators, and session prompt history.
- Wired the launcher to the existing daemon/router/provider path so submitted prompts use the current model provider layer instead of a separate UI-only code path.
- Added a `--launcher` CLI mode to open the GTK launcher for local testing until hotkey integration is added in later phases.
- Added launcher tests covering CLI wiring, prompt history, and launcher session state mapping.

### Key decisions made
- Left `overlay_monitor = "focused"` as an unset layer-shell monitor selection in Phase 4 so the compositor can place the launcher naturally on the focused output; `primary` maps to the first GTK monitor and numeric strings map to one-based monitor indices.
- Kept token display as `—` when token usage is unavailable because provider responses do not yet expose usage consistently across all backends.
- Ran launcher prompt submission in-process through the existing daemon/router stack because an IPC layer between overlay and daemon is not defined in the current build phases.
- Added `--launcher` as the practical entrypoint for exercising the UI before Hyprland hotkey binding and launcher toggling are implemented.

### Known issues / next steps
- The launcher requires the `gtk4-layer-shell` shared library to be preloaded before GTK is imported on this system; Nyx now re-execs the launcher path with `LD_PRELOAD` automatically, but this is an implementation detail to keep in mind for future packaging work.
- Keyboard shortcuts currently cover the Phase 4 launcher flow (`Enter`, `Shift+Enter`, `Escape`, `Ctrl+C`, history up/down); panel-mode shortcuts remain for Phase 5.
- Panel mode, markdown rendering, syntax highlighting, sessions/history sidebar, and search remain out of scope until the next phase.

### Follow-up fixes
- Added a launcher entrypoint that re-execs with `LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so` before GTK import, resolving the real-session layer-shell initialization failure caused by library load order.
- Added a launcher auto-close hook used for smoke-testing the real `nyx --launcher` path without leaving the overlay open during automated verification.

## Phase 5 — GTK4 Panel Mode
**Date:** 2026-03-12
**Status:** Complete

### What was built
- Added a Phase 5 left-sidebar panel window with `gtk4-layer-shell`, session history, search, shared status chips, prompt input, and markdown-rendered response output.
- Split shared overlay concerns into reusable UI modules for session/history state, shared CSS styling, and lightweight markdown rendering.
- Wired `Ctrl+H` launcher/panel toggling through one GTK application so both views share the same in-memory prompt history and session records.
- Added Phase 5 tests covering shared overlay session filtering/restoration and markdown buffer rendering.
- Smoke-tested both launcher and panel startup paths with automated auto-close hooks in addition to the full pytest suite.

### Key decisions made
- Kept launcher and panel as separate layer-shell windows managed by one GTK application instead of mutating a single window in place, because that keeps each layout simpler while preserving shared state.
- Implemented a lightweight in-process markdown renderer for the panel response view using `Gtk.TextBuffer` tags rather than introducing a heavier rendering dependency before the documentation calls for one.
- Scoped session history to in-memory overlay state for now because persistent chat/session storage is not defined in the current architecture phases.

### Known issues / next steps
- Phase 5 still depends on the existing launcher preload path for `gtk4-layer-shell`, so packaging/install work must preserve that startup behavior until a cleaner deployment approach is defined.
- Session history is intentionally ephemeral and resets when the GTK process exits.
- Panel-mode summon/dismiss through Hyprland keybindings and any richer markdown features remain for later phases.

## Phase 6 — System Control Module
**Date:** 2026-03-13
**Status:** Complete

### What was built
- Added a dedicated Phase 6 `SystemControlModule` that plans bridge-backed actions through the model provider layer and executes them only through `SystemBridge`.
- Wired the intent router to dispatch obvious system-control requests into the new module while preserving the existing general-provider response path for normal prompts.
- Added validation and execution support for the documented bridge operations, including shell commands, window queries, workspace moves, screenshots, process listing/termination, brightness, volume, system stats, and notifications.
- Added Phase 6 tests covering planner JSON parsing, fenced JSON extraction, bridge confirmation errors, conservative request matching, and router dispatch behavior.
- Live-smoked the real Phase 6 path with `codex-cli` using read-only requests for active-window lookup and system stats.

### Key decisions made
- Used provider-generated JSON action plans instead of a hardcoded command menu so the Phase 6 module matches the documentation's “AI generates commands via SystemBridge” requirement without leaking OS-specific behavior outside the bridge.
- Kept routing conservative with obvious system-control heuristics so normal prompts still flow through the regular provider response path until a richer classifier/context layer exists.
- Scoped provider planning to one bridge action per request in Phase 6 to keep execution auditable and compatible with the existing confirmation/security model.

### Known issues / next steps
- System-control routing is heuristic for now; later context and intent-classification phases should replace or augment it with richer context-aware classification.
- Destructive actions still fail closed with a confirmation-required response until the dedicated confirmation UX phase is implemented.
- Conversation/session carry-forward is still limited to the current overlay history model; persistent contextual memory remains a later phase.

## Phase 7 — Notes Module + Inbox Auto-Sort
**Date:** 2026-03-13
**Status:** Complete

### What was built
- Added a dedicated `NotesModule` that captures note requests into `inbox.md` under the configured notes directory.
- Implemented provider-backed note planning so Nyx can keep notes in the inbox or route them into existing project `notes.md` files.
- Added explicit inbox sorting support for pending inbox entries, using the provider layer to classify each entry against known project directories.
- Wired the intent router to dispatch obvious note/inbox requests into the new notes module while preserving the general provider path for non-notes prompts.
- Added Phase 7 tests covering inbox capture, project routing, manual inbox sorting, and router dispatch, plus a live isolated smoke run against the real `codex-cli` provider with a temporary notes tree.

### Key decisions made
- Stored inbox entries in a structured markdown format so later auto-sort passes can mark entries as routed without needing a separate database.
- Scoped project routing to existing project directories only; Nyx does not create new projects yet because the documentation requires user-confirmed project proposals before folders are created.
- Kept auto-sort synchronous within the current request flow because the architecture mentions background classification, but no background job system exists yet in the implemented phases.

### Known issues / next steps
- Inbox auto-sort currently routes only to existing project directories and does not yet propose or create new projects.
- Inbox/project note persistence uses asynchronous thread offloading for filesystem access because Python stdlib has no native async file API and synchronous I/O is not allowed in async request paths.
- Richer note search and semantic project routing remain for the later RAG phases.

## Phase 8 — RAG System
**Date:** 2026-03-13
**Status:** Complete

### What was built
- Added an async Ollama embedding client for `nomic-embed-text` and a local Chroma-backed RAG store wrapper with persistent collections.
- Added a `RagService` that rebuilds local RAG collections from `inbox.md` and per-project markdown files, with one collection for the inbox and one collection per project.
- Added a dedicated `RagModule` for explicit semantic-search requests over notes/projects and wired the intent router to dispatch those prompts into the RAG path.
- Added Phase 8 tests covering collection rebuilds, chunk indexing, formatted retrieval responses, and router dispatch for explicit RAG queries.
- Declared `chromadb` as a Phase 8 runtime dependency and verified the real provider-backed RAG route far enough to confirm the remaining runtime prerequisite is a reachable Ollama embedding backend.

### Key decisions made
- Used explicit embeddings from an async Ollama client and passed those embeddings directly into Chroma instead of relying on synchronous Chroma embedding functions, keeping network I/O out of the asyncio event loop.
- Rebuilds the managed RAG collections from the notes tree on demand rather than introducing a background file-watcher/indexer before the later context phases define one.
- Scoped explicit RAG routing to obvious search/lookup prompts only so general requests still follow the normal provider path until later context-aggregation phases.

### Known issues / next steps
- Live RAG queries require both `chromadb` to be installed in the runtime environment and Ollama to be reachable for the configured embedding model.
- The Phase 8 RAG path returns formatted retrieval hits rather than a synthesized answer; richer context-aware answer generation is left for later context/RAG integration phases.
- README frontmatter ranking, project pre-filtering, and autonomous context enrichment remain for later phases.

### Follow-up fixes
- Tightened routing so explicit `search inbox ...` requests go to the Phase 8 RAG module instead of being misclassified as Phase 7 inbox-note operations.
- Removed the over-broad bare `inbox` notes matcher and now prioritize explicit RAG lookups ahead of notes capture routing.

## Phase 9 — Context Compaction
**Date:** 2026-03-13
**Status:** Complete

### What was built
- Added a `ContextCompactor` service that parses project `README.md` frontmatter summaries, `last_updated`, and `tags`.
- Added cheap project ranking so global RAG searches pre-filter to the most relevant 1–3 projects before querying the heavier Chroma index.
- Integrated context compaction into the existing `RagService` so inbox plus top-ranked project collections are queried for global notes searches.
- Added Phase 9 tests covering frontmatter parsing, relevance ranking, and the constrained RAG collection-selection path.

### Key decisions made
- Implemented a small purpose-built frontmatter parser for the documented `summary`, `last_updated`, and `tags` fields instead of adding a full YAML dependency at this phase.
- Used deterministic lexical overlap scoring across project name, summary, and tags as the cheap relevance filter because the documentation only requires a lightweight pre-filter before full RAG.
- Limited context compaction to global search paths; explicit project searches still bypass ranking and go directly to the named project collection.

### Known issues / next steps
- Frontmatter parsing intentionally supports only the currently documented fields and simple inline tag lists.
- The compaction ranking is lexical, not semantic; richer ranking can be revisited later if future phases require it.
- Context compaction is currently consumed by the explicit RAG path; broader autonomous context aggregation still belongs to later phases.

## Phase 10 — Persistent Memory
**Date:** 2026-03-13
**Status:** Complete

### What was built
- Added a dedicated `MemoryModule` for global memory and per-project memory requests.
- Implemented file-backed global memory at `~/.config/nyx/memory.md` and per-project memory at `~/notes/projects/<project>/context.md`.
- Added a persistent proposal flow so memory updates are proposed first, then explicitly applied or skipped by the user.
- Added direct memory commands for listing pending proposals, applying/skipping proposals, and showing global or project memory.
- Wired the intent router to dispatch explicit memory requests into the new module and added Phase 10 tests plus an isolated live smoke run through the real `codex-cli` provider path.

### Key decisions made
- Added a persisted proposal store at `~/.config/nyx/memory_proposals.json` so the documented “accepts/edits/skips” workflow survives process restarts even though the docs do not prescribe a file format for pending proposals.
- Scoped automatic memory updates to explicit user-driven memory requests in Phase 10; the broader “after significant sessions” proposal trigger still needs a later session/context hook.
- Applied accepted memory updates as markdown bullet lines to keep memory files simple, append-only, and easy to edit manually.

### Known issues / next steps
- Phase 10 proposal creation currently depends on explicit user memory requests; automatic proposal generation after significant sessions is still pending later integration work.
- Project memory proposals only target existing projects and do not create new project folders or context files until a valid project target exists.
- Memory content is append-only for now; future phases may need merge/edit tooling once longer-lived memory entries accumulate.

## Phase 12 — Git + GitHub Module
**Date:** 2026-03-13
**Status:** Complete

### What was built
- Added a dedicated `GitHubModule` for explicit repository actions in the current working tree: commit, push proposal/apply flow, pull, PR creation, issue listing, and diff summary.
- Implemented async `git` and `gh` subprocess execution with current-repo detection via `git rev-parse --show-toplevel`.
- Wired the Phase 12 module into the intent router so obvious git/GitHub prompts route into the dedicated module instead of the general provider path.
- Added persisted push proposals at `~/.config/nyx/git_push_proposals.json` to satisfy the documented “push with confirmation” behavior before modal UI exists.
- Added Phase 12 tests covering commit planning, push proposal creation/application, GitHub issue formatting, diff summarization, and router dispatch.

### Key decisions made
- Scoped Phase 12 to the current repository only because the documentation requires Git/GitHub actions but does not define multi-repo selection UX yet.
- Used an explicit proposal/apply flow for `git push` because the docs require confirmation and Nyx still has no modal confirmation surface outside the GTK overlay roadmap.
- Kept diff summary provider-backed while leaving commit/pull/push/PR/issue execution deterministic so model output chooses the operation but does not directly execute arbitrary shell commands.

### Known issues / next steps
- Your current config emits `Unknown provider 'codex-cli'` warnings in fallback order because `codex-cli` is no longer defined in `~/.config/nyx/config.toml`; Phase 12 still works, but cleaning that fallback list would remove noisy warnings.
- PR creation and issue listing require a working `gh` auth state in the current repository context.
- Repo targeting is current-working-directory only for now; richer repo selection can be added later if the documentation calls for it.

## Phase 11 — Screen Context Module
**Date:** 2026-03-13
**Status:** Complete

### What was built
- Added a dedicated `ScreenContextModule` that captures the current screen through `SystemBridge.screenshot()` and bundles active-window metadata from `SystemBridge.get_active_window()`.
- Extended the provider layer with vision-query support for Ollama, Anthropic, OpenAI, and OpenAI-compatible HTTP backends using the current official multimodal request formats.
- Added provider-registry support for image-capable fallback selection and wired explicit screen-analysis prompts into the intent router.
- Added Phase 11 tests covering vision payload shapes, screenshot capture flow, and router dispatch behavior.

### Key decisions made
- Scoped Phase 11 to explicit user screen-analysis requests because the documentation's “autonomous on low confidence” trigger depends on later confidence/context plumbing that does not exist yet.
- Reused the existing provider abstraction by adding an optional image-query path instead of creating a separate one-off vision client stack.
- Allowed Ollama multimodal models as valid vision backends in addition to cloud providers, while still preserving the documented screenshot-plus-vision architecture.

### Known issues / next steps
- Live screen analysis still depends on at least one configured vision-capable provider/model being available in the local environment.
- The router does not yet invoke screen analysis automatically on low-confidence prompts; explicit requests only are implemented in this phase.
- Richer screenshot lifecycle management and cleanup can be revisited later if temporary image retention becomes an issue.

### Follow-up fixes
- Confirmed that subprocess CLI providers can participate in the Phase 11 vision path through configurable `image_args`, with `codex-cli` enabled by default using the installed `codex exec --image` interface.
- Added explicit regression coverage for subprocess image-query invocation and live-smoked `codex-cli` against a tiny PNG to verify the end-to-end CLI vision path.
- Increased the default Ollama vision timeout to 180 seconds and mapped HTTP timeout/network failures into descriptive Nyx provider errors after real-environment testing showed local vision model startup could exceed the original 60-second limit.
- Relaxed provider config validation so backend-specific options such as `vision_timeout_seconds` are accepted while still requiring stable `name` and `type` keys in each provider table.
