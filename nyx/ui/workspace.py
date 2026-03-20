"""Standalone GTK workspace window for longer Nyx sessions."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from nyx.config import NyxConfig
from nyx.ui.styles import install_ui_css
from nyx.ui.theme import resolve_theme
from nyx.workspace import (
    NyxWorkspaceFacade,
    WorkspaceProjectRecord,
    WorkspaceProjectSummary,
    WorkspaceRepoError,
    WorkspaceThreadRecord,
    WorkspaceUiState,
    WorkspaceUiStateStore,
)


class NyxWorkspaceWindow(Gtk.ApplicationWindow):
    """Standalone desktop workspace shell with real project and thread storage."""

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
        facade: NyxWorkspaceFacade,
        state_store: WorkspaceUiStateStore,
    ) -> None:
        super().__init__(application=application)
        self.config = config
        self.logger = logger
        self.facade = facade
        self.state_store = state_store
        self.state = self._initial_state(initial_section)
        self.project_summaries: list[WorkspaceProjectSummary] = []
        self.threads: list[WorkspaceThreadRecord] = []
        self.set_title("Nyx Workspace")
        self.set_default_size(config.ui.workspace_width, config.ui.workspace_height)
        self.add_css_class("nyx-window")
        self._build_layout()
        self._reload_workspace_data()
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
        """Create the shell used by the workspace, database, and later sections."""

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
        self.search_entry.set_placeholder_text("Search workspace projects and threads")
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
            label="Track Git repositories here. Selecting a project loads its workspace threads and repo-aware defaults.",
            xalign=0.0,
        )
        project_copy.set_wrap(True)
        project_copy.add_css_class("nyx-sidebar-copy")
        self.project_pane.append(project_copy)

        self.project_path_entry = Gtk.Entry()
        self.project_path_entry.set_placeholder_text("/path/to/repo")
        self.project_pane.append(self.project_path_entry)

        project_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.project_pane.append(project_actions)

        self.add_repo_button = Gtk.Button(label="Add Repo")
        self.add_repo_button.add_css_class("nyx-button-strong")
        self.add_repo_button.connect("clicked", self._on_add_repo_clicked)
        project_actions.append(self.add_repo_button)

        self.add_current_button = Gtk.Button(label="Add Current")
        self.add_current_button.add_css_class("nyx-button-soft")
        self.add_current_button.connect("clicked", self._on_add_current_repo_clicked)
        project_actions.append(self.add_current_button)

        self.remove_project_button = Gtk.Button(label="Remove")
        self.remove_project_button.add_css_class("nyx-button-soft")
        self.remove_project_button.connect("clicked", self._on_remove_project_clicked)
        project_actions.append(self.remove_project_button)

        self.project_notice_label = Gtk.Label(xalign=0.0)
        self.project_notice_label.set_wrap(True)
        self.project_notice_label.add_css_class("nyx-settings-help")
        self.project_pane.append(self.project_notice_label)

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
            label="Create project-scoped workspace threads here. Later slices will attach runs, plans, diffs, and approvals.",
            xalign=0.0,
        )
        thread_copy.set_wrap(True)
        thread_copy.add_css_class("nyx-sidebar-copy")
        self.thread_pane.append(thread_copy)

        self.thread_title_entry = Gtk.Entry()
        self.thread_title_entry.set_placeholder_text("Thread title")
        self.thread_pane.append(self.thread_title_entry)

        thread_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.thread_pane.append(thread_actions)

        self.new_thread_button = Gtk.Button(label="New")
        self.new_thread_button.add_css_class("nyx-button-strong")
        self.new_thread_button.connect("clicked", self._on_new_thread_clicked)
        thread_actions.append(self.new_thread_button)

        self.rename_thread_button = Gtk.Button(label="Rename")
        self.rename_thread_button.add_css_class("nyx-button-soft")
        self.rename_thread_button.connect("clicked", self._on_rename_thread_clicked)
        thread_actions.append(self.rename_thread_button)

        self.archive_thread_button = Gtk.Button(label="Archive")
        self.archive_thread_button.add_css_class("nyx-button-soft")
        self.archive_thread_button.connect("clicked", self._on_archive_thread_clicked)
        thread_actions.append(self.archive_thread_button)

        self.delete_thread_button = Gtk.Button(label="Delete")
        self.delete_thread_button.add_css_class("nyx-button-soft")
        self.delete_thread_button.connect("clicked", self._on_delete_thread_clicked)
        thread_actions.append(self.delete_thread_button)

        self.thread_notice_label = Gtk.Label(xalign=0.0)
        self.thread_notice_label.set_wrap(True)
        self.thread_notice_label.add_css_class("nyx-settings-help")
        self.thread_pane.append(self.thread_notice_label)

        self.thread_list = Gtk.ListBox()
        self.thread_list.add_css_class("nyx-session-list")
        self.thread_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.thread_list.connect("row-selected", self._on_thread_selected)
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

    def _build_section_page(self, section: str, title_text: str) -> Gtk.Widget:
        """Create one page for a top-level workspace section."""

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
            page.append(self._workspace_page())
        elif section == "database":
            page.append(self._database_preview())
        else:
            page.append(
                self._placeholder_card(
                    f"{title_text} is scaffolded in this PR and will be filled in the next feature slices."
                )
            )
        return page

    def _workspace_page(self) -> Gtk.Widget:
        """Return the real workspace detail surface."""

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

        self.workspace_status_label = Gtk.Label(xalign=0.0)
        self.workspace_status_label.add_css_class("nyx-hint")
        controls.append(self.workspace_status_label)

        metadata = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.append(metadata)

        self.workspace_project_label = Gtk.Label(xalign=0.0)
        self.workspace_project_label.add_css_class("nyx-history-title")
        metadata.append(self.workspace_project_label)

        self.workspace_branch_label = Gtk.Label(xalign=0.0)
        self.workspace_branch_label.add_css_class("nyx-history-subtitle")
        metadata.append(self.workspace_branch_label)

        self.workspace_thread_label = Gtk.Label(xalign=0.0)
        self.workspace_thread_label.add_css_class("nyx-history-preview")
        metadata.append(self.workspace_thread_label)

        self.workspace_detail_view = Gtk.TextView()
        self.workspace_detail_view.set_editable(False)
        self.workspace_detail_view.set_cursor_visible(False)
        self.workspace_detail_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.workspace_detail_view.set_monospace(True)
        self.workspace_detail_view.add_css_class("nyx-thread-view")

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self.workspace_detail_view)
        card.append(scroll)

        composer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        composer.add_css_class("nyx-composer-dock")
        card.append(composer)

        composer_label = Gtk.Label(
            label="Composer is scaffolded. Later slices will connect this to provider-backed project threads.",
            xalign=0.0,
        )
        composer_label.add_css_class("nyx-hint")
        composer.append(composer_label)

        entry = Gtk.TextView()
        entry.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        entry.set_monospace(True)
        entry.set_size_request(-1, 96)
        entry.add_css_class("nyx-popup-input")
        entry.get_buffer().set_text("Inspect the selected project and suggest the next implementation step.")
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
            "Database is still a first-class section inside the workspace shell. "
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
            "workspace": "Large project-centric chat workspace with real tracked repos and persisted project threads.",
            "database": "Unified persistent-data viewer/editor for conversations, knowledge, RAG, config, calendar, automations, context, and maintenance.",
            "automations": "Macros, skills, and later project quick actions.",
            "calendar": "Calendar events and backend status in a dedicated section.",
            "config": "Workspace and Nyx configuration, with provider capability settings.",
            "context": "Health/status for every context source Nyx can access.",
            "maintenance": "Repair, rebuild, export, and cleanup actions.",
        }[section]

    def do_close_request(self) -> bool:
        """Persist shell state when the window closes."""

        self._save_state()
        return super().do_close_request()

    def _reload_workspace_data(self) -> None:
        """Reload project and thread data from the persistent facade."""

        query = self.state.search_text if self.state.selected_section == "workspace" else ""
        self.project_summaries = self.facade.list_projects(search=query or None)
        if self.state.selected_project_id and not any(
            summary.project.project_id == self.state.selected_project_id
            for summary in self.project_summaries
        ):
            self.state.selected_project_id = None
            self.state.selected_thread_id = None
        if self.state.selected_project_id:
            self.threads = self.facade.list_threads(
                self.state.selected_project_id,
                search=query or None,
            )
        else:
            self.threads = []
        if self.state.selected_thread_id and not any(
            thread.thread_id == self.state.selected_thread_id
            for thread in self.threads
        ):
            self.state.selected_thread_id = None

    def _rebuild_project_list(self) -> None:
        """Rebuild the tracked project list from persisted workspace data."""

        while True:
            row = self.project_list.get_row_at_index(0)
            if row is None:
                break
            self.project_list.remove(row)

        for summary in self.project_summaries:
            row = Gtk.ListBoxRow()
            row.set_selectable(True)
            row.set_activatable(True)
            row.project_summary = summary

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.add_css_class("nyx-history-row")

            title = Gtk.Label(label=summary.project.name, xalign=0.0)
            title.add_css_class("nyx-history-title")
            title.set_wrap(True)
            box.append(title)

            subtitle = Gtk.Label(label=summary.project.repo_path, xalign=0.0)
            subtitle.add_css_class("nyx-history-subtitle")
            subtitle.set_wrap(True)
            box.append(subtitle)

            meta_label = self._project_meta_text(summary)
            meta = Gtk.Label(label=meta_label, xalign=0.0)
            meta.add_css_class("nyx-history-preview")
            meta.set_wrap(True)
            box.append(meta)

            row.set_child(box)
            self.project_list.append(row)

    def _rebuild_thread_list(self) -> None:
        """Rebuild the selected project's thread list."""

        while True:
            row = self.thread_list.get_row_at_index(0)
            if row is None:
                break
            self.thread_list.remove(row)

        for thread in self.threads:
            row = Gtk.ListBoxRow()
            row.set_selectable(True)
            row.set_activatable(True)
            row.thread = thread

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.add_css_class("nyx-history-row")

            title = Gtk.Label(label=thread.title, xalign=0.0)
            title.add_css_class("nyx-history-title")
            title.set_wrap(True)
            box.append(title)

            subtitle = Gtk.Label(label=self._thread_meta_text(thread), xalign=0.0)
            subtitle.add_css_class("nyx-history-subtitle")
            subtitle.set_wrap(True)
            box.append(subtitle)

            preview = Gtk.Label(
                label=thread.summary or "No run summary yet. This thread is ready for future plan/chat flows.",
                xalign=0.0,
            )
            preview.add_css_class("nyx-history-preview")
            preview.set_wrap(True)
            box.append(preview)

            row.set_child(box)
            self.thread_list.append(row)

    def _apply_state(self) -> None:
        """Apply the persisted UI state to the shell widgets."""

        self.search_entry.set_text(self.state.search_text)
        self.provider_chip.set_label(self.state.provider_name or self.config.models.default)
        self.mode_chip.set_label(self.state.mode or self.config.ui.workspace_default_mode)
        self.access_chip.set_label(self.state.access_mode or self.config.ui.workspace_default_access)
        self.content_stack.set_visible_child_name(self.state.selected_section)

        workspace_visible = self.state.selected_section == "workspace"
        self.project_pane.set_visible(workspace_visible)
        self.thread_pane.set_visible(workspace_visible)

        for section, button in self.nav_buttons.items():
            if section == self.state.selected_section:
                button.add_css_class("active")
            else:
                button.remove_css_class("active")

        provider_names = [provider.name for provider in self.config.models.providers]
        current_provider = self.state.provider_name or self.config.models.default
        if provider_names and current_provider in provider_names:
            self.provider_combo.set_selected(provider_names.index(current_provider))
        self.mode_combo.set_selected(1 if (self.state.mode or self.config.ui.workspace_default_mode) == "plan" else 0)
        self.access_combo.set_selected(1 if (self.state.access_mode or self.config.ui.workspace_default_access) == "full" else 0)

        self._rebuild_project_list()
        self._rebuild_thread_list()
        self._select_project_row()
        self._select_thread_row()
        self._update_actions()
        self._refresh_workspace_detail()

    def _save_state(self) -> None:
        """Persist the current shell state to disk."""

        self.state_store.save(self.state)

    def _select_project_row(self) -> None:
        """Select the persisted project row if it is visible."""

        if not self.state.selected_project_id:
            self.project_list.unselect_all()
            return
        for index, summary in enumerate(self.project_summaries):
            if summary.project.project_id == self.state.selected_project_id:
                row = self.project_list.get_row_at_index(index)
                if row is not None:
                    self.project_list.select_row(row)
                return
        self.project_list.unselect_all()

    def _select_thread_row(self) -> None:
        """Select the persisted thread row if it is visible."""

        if not self.state.selected_thread_id:
            self.thread_list.unselect_all()
            return
        for index, thread in enumerate(self.threads):
            if thread.thread_id == self.state.selected_thread_id:
                row = self.thread_list.get_row_at_index(index)
                if row is not None:
                    self.thread_list.select_row(row)
                return
        self.thread_list.unselect_all()

    def _selected_project(self) -> WorkspaceProjectRecord | None:
        """Return the currently selected project record."""

        return self.facade.get_project(self.state.selected_project_id)

    def _selected_thread(self) -> WorkspaceThreadRecord | None:
        """Return the currently selected thread record."""

        return self.facade.get_thread(self.state.selected_thread_id)

    def _selected_project_summary(self) -> WorkspaceProjectSummary | None:
        """Return the currently selected project summary if visible."""

        for summary in self.project_summaries:
            if summary.project.project_id == self.state.selected_project_id:
                return summary
        return None

    def _set_project_notice(self, message: str) -> None:
        """Show one project-pane status message."""

        self.project_notice_label.set_label(message)

    def _set_thread_notice(self, message: str) -> None:
        """Show one thread-pane status message."""

        self.thread_notice_label.set_label(message)

    def _set_workspace_status(self, message: str) -> None:
        """Show one workspace-page status message."""

        self.workspace_status_label.set_label(message)

    def _update_actions(self) -> None:
        """Update button sensitivity and labels based on current selection."""

        project = self._selected_project()
        thread = self._selected_thread()
        has_project = project is not None
        has_thread = thread is not None
        self.remove_project_button.set_sensitive(has_project)
        self.new_thread_button.set_sensitive(has_project)
        self.rename_thread_button.set_sensitive(has_thread)
        self.delete_thread_button.set_sensitive(has_thread)
        self.archive_thread_button.set_sensitive(has_thread)
        self.archive_thread_button.set_label("Unarchive" if has_thread and thread.archived else "Archive")
        if not has_thread:
            self.thread_title_entry.set_text("")
        elif self.thread_title_entry.get_text().strip() != thread.title:
            self.thread_title_entry.set_text(thread.title)

    def _refresh_workspace_detail(self) -> None:
        """Refresh the main workspace detail page from the current selection."""

        summary = self._selected_project_summary()
        thread = self._selected_thread()

        if summary is None:
            self.workspace_project_label.set_label("No project selected")
            self.workspace_branch_label.set_label("Add a Git repository from the left pane to begin.")
            self.workspace_thread_label.set_label("Workspace threads are stored locally in workspace.db.")
            self._set_detail_text(
                "## Workspace\n\n"
                "Add a Git repository to create a real tracked workspace project.\n\n"
                "This slice persists projects and threads locally so the next slices can attach agent runs, plans, diffs, and terminal artifacts."
            )
            return

        project = summary.project
        repo_status = summary.repo_status
        self.workspace_project_label.set_label(project.name)
        if repo_status is not None:
            dirty_text = "dirty" if repo_status.dirty else "clean"
            branch_text = repo_status.branch_name or "(detached)"
            self.workspace_branch_label.set_label(f"{branch_text}  •  {dirty_text}  •  {project.repo_path}")
        else:
            self.workspace_branch_label.set_label(project.repo_path)

        if thread is None:
            self.workspace_thread_label.set_label("No thread selected for this project.")
            self._set_detail_text(
                f"## {project.name}\n\n"
                "This repository is now tracked by the workspace.\n\n"
                "- Create a thread from the middle pane.\n"
                "- The thread will persist provider, mode, access, branch, and summary metadata.\n"
                "- Agent runs, diffs, and plans will attach in later slices."
            )
            return

        self.workspace_thread_label.set_label(
            f"{thread.title}  •  provider={thread.provider_name or self.config.models.default}  •  mode={thread.mode}  •  access={thread.access_mode}  •  status={thread.status}"
        )
        thread_summary = thread.summary or "No run summary has been recorded yet."
        self._set_detail_text(
            f"## {thread.title}\n\n"
            f"- Project: {project.name}\n"
            f"- Branch: {thread.branch_name or '(detached)'}\n"
            f"- Worktree: {thread.worktree_path or project.repo_path}\n"
            f"- Updated: {thread.updated_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"- Archived: {'yes' if thread.archived else 'no'}\n\n"
            f"{thread_summary}"
        )

    def _set_detail_text(self, text: str) -> None:
        """Replace the main detail text view contents."""

        self.workspace_detail_view.get_buffer().set_text(text)

    def _project_meta_text(self, summary: WorkspaceProjectSummary) -> str:
        """Return one compact project-row metadata string."""

        if summary.repo_status is None:
            return "Git status unavailable"
        dirty = "dirty" if summary.repo_status.dirty else "clean"
        branch = summary.repo_status.branch_name or "(detached)"
        return f"{branch}  •  {dirty}"

    def _thread_meta_text(self, thread: WorkspaceThreadRecord) -> str:
        """Return one compact thread-row metadata string."""

        updated = thread.updated_at.strftime("%Y-%m-%d %H:%M")
        archived = "archived" if thread.archived else "active"
        return (
            f"{updated}  •  {thread.provider_name or self.config.models.default}  •  "
            f"{thread.mode}/{thread.access_mode}  •  {archived}"
        )

    def _reload_and_apply(self) -> None:
        """Reload workspace data and refresh the shell."""

        self._reload_workspace_data()
        self._apply_state()
        self._save_state()

    def _on_section_clicked(self, button: Gtk.Button, section: str) -> None:
        """Switch the top-level section shown in the content stack."""

        del button
        self.state.selected_section = section
        if section == "workspace":
            self._reload_workspace_data()
        self._apply_state()
        self._save_state()

    def _on_project_selected(self, list_box: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        """Remember the selected project from the tracked project list."""

        del list_box
        summary = getattr(row, "project_summary", None)
        if isinstance(summary, WorkspaceProjectSummary):
            self.state.selected_project_id = summary.project.project_id
            self.state.selected_thread_id = None
            self._set_project_notice("")
            self._set_thread_notice("")
        self._reload_and_apply()

    def _on_thread_selected(self, list_box: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        """Remember the selected thread for the current project."""

        del list_box
        thread = getattr(row, "thread", None)
        self.state.selected_thread_id = thread.thread_id if isinstance(thread, WorkspaceThreadRecord) else None
        self._update_actions()
        self._refresh_workspace_detail()
        self._save_state()

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        """Persist the global search text and rebuild the visible workspace lists."""

        self.state.search_text = entry.get_text()
        if self.state.selected_section == "workspace":
            self._reload_and_apply()
            return
        self._save_state()

    def _on_provider_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        """Persist provider selection from the workspace controls."""

        item = dropdown.get_selected_item()
        if isinstance(item, Gtk.StringObject):
            self.state.provider_name = item.get_string()
            self._apply_state()
            self._save_state()

    def _on_mode_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        """Persist mode selection from the workspace controls."""

        item = dropdown.get_selected_item()
        if isinstance(item, Gtk.StringObject):
            self.state.mode = item.get_string()
            self._apply_state()
            self._save_state()

    def _on_access_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        """Persist access-mode selection from the workspace controls."""

        item = dropdown.get_selected_item()
        if isinstance(item, Gtk.StringObject):
            self.state.access_mode = item.get_string()
            self._apply_state()
            self._save_state()

    def _on_add_repo_clicked(self, button: Gtk.Button) -> None:
        """Add one repository from the typed path entry."""

        del button
        repo_path = self.project_path_entry.get_text().strip()
        if not repo_path:
            self._set_project_notice("Enter a repository path first.")
            return
        try:
            project = self.facade.add_project(repo_path)
        except WorkspaceRepoError as exc:
            self._set_project_notice(str(exc))
            return
        self.state.selected_project_id = project.project_id
        self.state.selected_thread_id = None
        self.project_path_entry.set_text("")
        self._set_project_notice(f"Added {project.name}.")
        self._set_workspace_status("Project added.")
        self._reload_and_apply()

    def _on_add_current_repo_clicked(self, button: Gtk.Button) -> None:
        """Add the current working directory as a tracked repo project."""

        del button
        self.project_path_entry.set_text(str(Path.cwd()))
        self._on_add_repo_clicked(None)

    def _on_remove_project_clicked(self, button: Gtk.Button) -> None:
        """Remove the selected project from the workspace DB."""

        del button
        project = self._selected_project()
        if project is None:
            self._set_project_notice("Select a project first.")
            return
        self.facade.remove_project(project.project_id)
        self.state.selected_project_id = None
        self.state.selected_thread_id = None
        self._set_project_notice(f"Removed {project.name} from the workspace.")
        self._set_workspace_status("Project removed.")
        self._reload_and_apply()

    def _on_new_thread_clicked(self, button: Gtk.Button) -> None:
        """Create one new thread for the selected project."""

        del button
        project = self._selected_project()
        if project is None:
            self._set_thread_notice("Select a project before creating a thread.")
            return
        try:
            thread = self.facade.create_thread(
                project.project_id,
                self.thread_title_entry.get_text().strip() or None,
            )
        except WorkspaceRepoError as exc:
            self._set_thread_notice(str(exc))
            return
        self.state.selected_thread_id = thread.thread_id
        self._set_thread_notice(f"Created thread: {thread.title}")
        self._set_workspace_status("Thread created.")
        self._reload_and_apply()

    def _on_rename_thread_clicked(self, button: Gtk.Button) -> None:
        """Rename the selected thread from the title entry."""

        del button
        thread = self._selected_thread()
        if thread is None:
            self._set_thread_notice("Select a thread first.")
            return
        try:
            renamed = self.facade.rename_thread(thread.thread_id, self.thread_title_entry.get_text())
        except WorkspaceRepoError as exc:
            self._set_thread_notice(str(exc))
            return
        if renamed is None:
            self._set_thread_notice("The selected thread no longer exists.")
            self.state.selected_thread_id = None
        else:
            self.state.selected_thread_id = renamed.thread_id
            self._set_thread_notice(f"Renamed thread to {renamed.title}.")
            self._set_workspace_status("Thread renamed.")
        self._reload_and_apply()

    def _on_archive_thread_clicked(self, button: Gtk.Button) -> None:
        """Archive or unarchive the selected thread."""

        del button
        thread = self._selected_thread()
        if thread is None:
            self._set_thread_notice("Select a thread first.")
            return
        updated = self.facade.set_thread_archived(thread.thread_id, not thread.archived)
        if updated is None:
            self._set_thread_notice("The selected thread no longer exists.")
            self.state.selected_thread_id = None
        else:
            self.state.selected_thread_id = updated.thread_id
            verb = "Unarchived" if thread.archived else "Archived"
            self._set_thread_notice(f"{verb} {updated.title}.")
            self._set_workspace_status(f"{verb} thread.")
        self._reload_and_apply()

    def _on_delete_thread_clicked(self, button: Gtk.Button) -> None:
        """Delete the selected thread."""

        del button
        thread = self._selected_thread()
        if thread is None:
            self._set_thread_notice("Select a thread first.")
            return
        self.facade.delete_thread(thread.thread_id)
        self.state.selected_thread_id = None
        self._set_thread_notice(f"Deleted {thread.title}.")
        self._set_workspace_status("Thread deleted.")
        self._reload_and_apply()


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
        self.facade = NyxWorkspaceFacade(config=config, logger=logger)
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
                facade=self.facade,
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
