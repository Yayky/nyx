"""Standalone GTK workspace window for longer Nyx sessions."""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from nyx.config import NyxConfig
from nyx.ui.styles import install_ui_css
from nyx.ui.theme import resolve_theme
from nyx.workspace import WorkspaceProject, WorkspaceProjectRegistry, WorkspaceUiState, WorkspaceUiStateStore


class NyxWorkspaceWindow(Gtk.ApplicationWindow):
    """Standalone desktop workspace shell with sidebar sections."""

    SECTION_TITLES = {
        "workspace": "Workspace",
        "database": "Database",
        "automations": "Automations",
        "calendar": "Calendar",
        "config": "Config",
        "context": "Context",
        "maintenance": "Maintenance",
    }

    def __init__(
        self,
        *,
        application: Gtk.Application,
        config: NyxConfig,
        logger: logging.Logger,
        initial_section: str,
        project_registry: WorkspaceProjectRegistry,
        state_store: WorkspaceUiStateStore,
    ) -> None:
        super().__init__(application=application)
        self.config = config
        self.logger = logger
        self.project_registry = project_registry
        self.state_store = state_store
        self.projects = project_registry.load()
        self.state = self._initial_state(initial_section)
        self.set_title("Nyx Workspace")
        self.set_default_size(config.ui.workspace_width, config.ui.workspace_height)
        self.add_css_class("nyx-window")
        self._build_layout()
        self._apply_state()

    def _initial_state(self, initial_section: str) -> WorkspaceUiState:
        """Return the workspace state that should drive the initial shell."""

        loaded = self.state_store.load()
        loaded.selected_section = initial_section or loaded.selected_section or "workspace"
        if loaded.selected_section not in self.SECTION_TITLES:
            loaded.selected_section = "workspace"
        loaded.provider_name = loaded.provider_name or self.config.models.default
        loaded.mode = loaded.mode or self.config.ui.workspace_default_mode
        loaded.access_mode = loaded.access_mode or self.config.ui.workspace_default_access
        return loaded

    def _build_layout(self) -> None:
        """Create the 3-pane shell used by the initial workspace PR."""

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class("nyx-workspace-root")
        self.set_child(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("nyx-workspace-header")
        root.append(header)

        title_block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.append(title_block)

        title = Gtk.Label(label="Nyx Workspace", xalign=0.0)
        title.add_css_class("nyx-section-title")
        title.add_css_class("nyx-workspace-title")
        title_block.append(title)

        subtitle = Gtk.Label(
            label="Project-first coding workspace with Database, Automations, Calendar, Config, Context, and Maintenance sections.",
            xalign=0.0,
        )
        subtitle.set_wrap(True)
        subtitle.add_css_class("nyx-sidebar-copy")
        title_block.append(subtitle)

        header.append(Gtk.Box(hexpand=True))

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search projects, threads, and database sections")
        self.search_entry.set_size_request(320, -1)
        self.search_entry.connect("search-changed", self._on_search_changed)
        header.append(self.search_entry)

        self.provider_chip = Gtk.Label(xalign=0.5)
        self.provider_chip.add_css_class("nyx-chip")
        header.append(self.provider_chip)

        self.mode_chip = Gtk.Label(xalign=0.5)
        self.mode_chip.add_css_class("nyx-chip")
        header.append(self.mode_chip)

        self.access_chip = Gtk.Label(xalign=0.5)
        self.access_chip.add_css_class("nyx-chip")
        header.append(self.access_chip)

        shell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        shell.add_css_class("nyx-stage")
        shell.add_css_class("nyx-workspace-shell")
        shell.set_margin_top(12)
        shell.set_margin_bottom(12)
        shell.set_margin_start(12)
        shell.set_margin_end(12)
        root.append(shell)

        self.nav = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.nav.add_css_class("nyx-rail")
        self.nav.set_size_request(150, -1)
        shell.append(self.nav)

        self.nav_buttons: dict[str, Gtk.Button] = {}
        for section, title_text in self.SECTION_TITLES.items():
            button = Gtk.Button(label=title_text)
            button.add_css_class("nyx-workspace-nav-button")
            button.connect("clicked", self._on_section_clicked, section)
            self.nav.append(button)
            self.nav_buttons[section] = button
        self.nav.append(Gtk.Box(vexpand=True))

        main_shell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        main_shell.set_hexpand(True)
        main_shell.set_vexpand(True)
        shell.append(main_shell)

        self.project_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.project_pane.add_css_class("nyx-history-pane")
        self.project_pane.set_margin_start(4)
        self.project_pane.set_margin_top(4)
        self.project_pane.set_margin_bottom(4)
        self.project_pane.set_margin_end(4)
        self.project_pane.set_size_request(self.config.ui.workspace_sidebar_width, -1)
        main_shell.append(self.project_pane)

        project_title = Gtk.Label(label="Projects", xalign=0.0)
        project_title.add_css_class("nyx-section-title")
        project_title.add_css_class("nyx-sidebar-title")
        self.project_pane.append(project_title)

        project_copy = Gtk.Label(
            label="Tracked Git repositories will appear here. This first workspace slice wires the shell, project registry, and section navigation.",
            xalign=0.0,
        )
        project_copy.set_wrap(True)
        project_copy.add_css_class("nyx-sidebar-copy")
        self.project_pane.append(project_copy)

        self.project_list = Gtk.ListBox()
        self.project_list.add_css_class("nyx-session-list")
        self.project_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.project_list.connect("row-selected", self._on_project_selected)
        self.project_list.set_placeholder(Gtk.Label(label="No tracked projects yet."))

        project_scroll = Gtk.ScrolledWindow()
        project_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        project_scroll.set_vexpand(True)
        project_scroll.set_child(self.project_list)
        self.project_pane.append(project_scroll)

        detail_shell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        detail_shell.set_hexpand(True)
        detail_shell.set_vexpand(True)
        main_shell.append(detail_shell)

        self.thread_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.thread_pane.add_css_class("nyx-history-pane")
        self.thread_pane.set_margin_top(4)
        self.thread_pane.set_margin_bottom(4)
        self.thread_pane.set_margin_end(4)
        self.thread_pane.set_size_request(self.config.ui.workspace_thread_list_width, -1)
        detail_shell.append(self.thread_pane)

        thread_title = Gtk.Label(label="Threads", xalign=0.0)
        thread_title.add_css_class("nyx-section-title")
        thread_title.add_css_class("nyx-sidebar-title")
        self.thread_pane.append(thread_title)

        thread_copy = Gtk.Label(
            label="Project-scoped workspace threads, plan runs, and approvals will land here in the next slice.",
            xalign=0.0,
        )
        thread_copy.set_wrap(True)
        thread_copy.add_css_class("nyx-sidebar-copy")
        self.thread_pane.append(thread_copy)

        self.thread_list = Gtk.ListBox()
        self.thread_list.add_css_class("nyx-session-list")
        self.thread_list.set_placeholder(Gtk.Label(label="No threads yet."))

        thread_scroll = Gtk.ScrolledWindow()
        thread_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        thread_scroll.set_vexpand(True)
        thread_scroll.set_child(self.thread_list)
        self.thread_pane.append(thread_scroll)

        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.add_css_class("nyx-thread-pane")
        self.content_stack.set_margin_top(4)
        self.content_stack.set_margin_bottom(4)
        self.content_stack.set_margin_end(4)
        self.content_stack.set_hexpand(True)
        self.content_stack.set_vexpand(True)
        self.content_stack.set_size_request(self.config.ui.workspace_detail_width, -1)
        detail_shell.append(self.content_stack)

        for section, title_text in self.SECTION_TITLES.items():
            self.content_stack.add_titled(self._build_section_page(section, title_text), section, title_text)

        self._rebuild_project_list()

    def _build_section_page(self, section: str, title_text: str) -> Gtk.Widget:
        """Create one placeholder page for a top-level workspace section."""

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        page.add_css_class("nyx-thread-pane")

        title = Gtk.Label(label=title_text, xalign=0.0)
        title.add_css_class("nyx-section-title")
        page.append(title)

        description = Gtk.Label(
            xalign=0.0,
            label=self._section_copy(section),
        )
        description.set_wrap(True)
        description.add_css_class("nyx-sidebar-copy")
        page.append(description)

        if section == "workspace":
            page.append(self._workspace_preview())
        elif section == "database":
            page.append(self._database_preview())
        else:
            page.append(self._placeholder_card(f"{title_text} is scaffolded in this PR and will be filled in the next feature slices."))
        return page

    def _workspace_preview(self) -> Gtk.Widget:
        """Return the workspace-mode preview surface."""

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("nyx-workspace-card")

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        card.append(controls)

        self.provider_combo = Gtk.DropDown.new_from_strings([provider.name for provider in self.config.models.providers])
        self.provider_combo.connect("notify::selected", self._on_provider_changed)
        controls.append(self.provider_combo)

        self.mode_combo = Gtk.DropDown.new_from_strings(["chat", "plan"])
        self.mode_combo.connect("notify::selected", self._on_mode_changed)
        controls.append(self.mode_combo)

        self.access_combo = Gtk.DropDown.new_from_strings(["supervised", "full"])
        self.access_combo.connect("notify::selected", self._on_access_changed)
        controls.append(self.access_combo)

        preview = Gtk.TextView()
        preview.set_editable(False)
        preview.set_cursor_visible(False)
        preview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        preview.set_monospace(True)
        preview.add_css_class("nyx-thread-view")
        preview_buffer = preview.get_buffer()
        preview_buffer.set_text(
            "## Workspace\n\n"
            "This first PR adds the project-first shell, section navigation, state persistence, provider/mode/access selectors, and the dedicated Database destination.\n\n"
            "Next slices will add tracked repo projects, thread persistence, agent runs, and repo-aware tooling."
        )

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(preview)
        card.append(scroll)

        composer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        composer.add_css_class("nyx-composer-dock")
        card.append(composer)

        composer_label = Gtk.Label(
            label="Composer and run controls are scaffolded for later PRs.",
            xalign=0.0,
        )
        composer_label.add_css_class("nyx-hint")
        composer.append(composer_label)

        entry = Gtk.TextView()
        entry.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        entry.set_monospace(True)
        entry.set_size_request(-1, 96)
        entry.add_css_class("nyx-popup-input")
        entry.get_buffer().set_text("Explain the current repo and suggest the next refactor.")
        composer.append(entry)
        return card

    def _database_preview(self) -> Gtk.Widget:
        """Return the database-section preview surface."""

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        subsections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        subsections.add_css_class("nyx-history-pane")
        subsections.set_size_request(220, -1)
        box.append(subsections)

        nav_title = Gtk.Label(label="Database", xalign=0.0)
        nav_title.add_css_class("nyx-section-title")
        subsections.append(nav_title)
        for label in [
            "Overview",
            "Conversations",
            "Knowledge",
            "Calendar",
            "Automations",
            "Config",
            "Context Sources",
            "Maintenance",
        ]:
            row = Gtk.Label(label=label, xalign=0.0)
            row.add_css_class("nyx-history-title")
            subsections.append(row)

        detail = self._placeholder_card(
            "Database is now a first-class section inside the workspace shell. "
            "The upcoming slices will fill this with the admin viewer/editor for conversations, notes, tasks, memory, RAG, calendar, automations, config, context sources, and maintenance actions."
        )
        detail.set_hexpand(True)
        box.append(detail)
        return box

    def _placeholder_card(self, text: str) -> Gtk.Widget:
        """Return one styled placeholder card."""

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("nyx-workspace-card")
        label = Gtk.Label(label=text, xalign=0.0)
        label.set_wrap(True)
        label.add_css_class("nyx-sidebar-copy")
        card.append(label)
        return card

    def _section_copy(self, section: str) -> str:
        """Return descriptive text for one shell section."""

        return {
            "workspace": "Large project-centric chat workspace with provider, mode, and access controls.",
            "database": "Unified persistent-data viewer/editor for conversations, knowledge, RAG, config, calendar, automations, context, and maintenance.",
            "automations": "Macros, skills, and later project quick actions.",
            "calendar": "Calendar events and backend status in a dedicated section.",
            "config": "Workspace and Nyx configuration, with provider capability settings.",
            "context": "Health/status for every context source Nyx can access.",
            "maintenance": "Repair, rebuild, export, and cleanup actions.",
        }[section]

    def _rebuild_project_list(self) -> None:
        """Rebuild the tracked project list from the saved registry."""

        while True:
            row = self.project_list.get_row_at_index(0)
            if row is None:
                break
            self.project_list.remove(row)

        for project in self.projects:
            row = Gtk.ListBoxRow()
            row.set_selectable(True)
            row.set_activatable(True)
            row.project = project

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.add_css_class("nyx-history-row")

            title = Gtk.Label(label=project.display_name, xalign=0.0)
            title.add_css_class("nyx-history-title")
            box.append(title)

            subtitle = Gtk.Label(label=project.root_path, xalign=0.0)
            subtitle.add_css_class("nyx-history-subtitle")
            subtitle.set_wrap(True)
            box.append(subtitle)

            meta = Gtk.Label(
                label=f"provider: {project.preferred_provider or self.config.models.default}",
                xalign=0.0,
            )
            meta.add_css_class("nyx-history-preview")
            box.append(meta)

            row.set_child(box)
            self.project_list.append(row)

    def do_close_request(self) -> bool:
        """Persist shell state when the window closes."""

        self._save_state()
        return super().do_close_request()

    def _apply_state(self) -> None:
        """Apply the persisted UI state to the shell widgets."""

        self.search_entry.set_text(self.state.search_text)
        self.provider_chip.set_label(self.state.provider_name or self.config.models.default)
        self.mode_chip.set_label(self.state.mode or self.config.ui.workspace_default_mode)
        self.access_chip.set_label(self.state.access_mode or self.config.ui.workspace_default_access)
        self.content_stack.set_visible_child_name(self.state.selected_section)

        for section, button in self.nav_buttons.items():
            if section == self.state.selected_section:
                button.add_css_class("active")
            else:
                button.remove_css_class("active")

        provider_names = [provider.name for provider in self.config.models.providers]
        if provider_names and (self.state.provider_name or self.config.models.default) in provider_names:
            self.provider_combo.set_selected(provider_names.index(self.state.provider_name or self.config.models.default))
        self.mode_combo.set_selected(1 if (self.state.mode or self.config.ui.workspace_default_mode) == "plan" else 0)
        self.access_combo.set_selected(1 if (self.state.access_mode or self.config.ui.workspace_default_access) == "full" else 0)

        if self.state.selected_project_id:
            row_index = next(
                (
                    index
                    for index, project in enumerate(self.projects)
                    if project.project_id == self.state.selected_project_id
                ),
                None,
            )
            if row_index is not None:
                row = self.project_list.get_row_at_index(row_index)
                if row is not None:
                    self.project_list.select_row(row)

    def _save_state(self) -> None:
        """Persist the current shell state to disk."""

        self.state_store.save(self.state)

    def _on_section_clicked(self, button: Gtk.Button, section: str) -> None:
        """Switch the top-level section shown in the content stack."""

        del button
        self.state.selected_section = section
        self._apply_state()
        self._save_state()

    def _on_project_selected(self, list_box: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        """Remember the selected project from the registry list."""

        del list_box
        project = getattr(row, "project", None)
        if isinstance(project, WorkspaceProject):
            self.state.selected_project_id = project.project_id
            self._save_state()

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        """Persist the global search text used by the shell."""

        self.state.search_text = entry.get_text()
        self._save_state()

    def _on_provider_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        """Persist provider selection from the workspace preview controls."""

        item = dropdown.get_selected_item()
        if isinstance(item, Gtk.StringObject):
            self.state.provider_name = item.get_string()
            self._apply_state()
            self._save_state()

    def _on_mode_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        """Persist mode selection from the workspace preview controls."""

        item = dropdown.get_selected_item()
        if isinstance(item, Gtk.StringObject):
            self.state.mode = item.get_string()
            self._apply_state()
            self._save_state()

    def _on_access_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        """Persist access-mode selection from the workspace preview controls."""

        item = dropdown.get_selected_item()
        if isinstance(item, Gtk.StringObject):
            self.state.access_mode = item.get_string()
            self._apply_state()
            self._save_state()


class NyxWorkspaceApplication(Gtk.Application):
    """GTK application wrapper for the standalone workspace shell."""

    def __init__(
        self,
        *,
        config: NyxConfig,
        logger: logging.Logger,
        initial_section: str,
    ) -> None:
        super().__init__(application_id="dev.nyx.workspace")
        self.config = config
        self.logger = logger
        self.initial_section = initial_section
        self.window: NyxWorkspaceWindow | None = None
        self.theme = resolve_theme(config, logger=logger)
        self.project_registry = WorkspaceProjectRegistry()
        self.state_store = WorkspaceUiStateStore()

    def do_activate(self) -> None:
        """Create and present the workspace window."""

        install_ui_css(self.theme, self.config.ui.font)
        if self.window is None:
            self.window = NyxWorkspaceWindow(
                application=self,
                config=self.config,
                logger=self.logger,
                initial_section=self.initial_section,
                project_registry=self.project_registry,
                state_store=self.state_store,
            )
        self.window.present()


def run_workspace(
    *,
    config: NyxConfig,
    logger: logging.Logger,
    initial_section: str = "workspace",
) -> int:
    """Run the standalone Nyx workspace application."""

    app = NyxWorkspaceApplication(config=config, logger=logger, initial_section=initial_section)
    return app.run([])
