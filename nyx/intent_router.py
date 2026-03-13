"""Intent routing for the current Nyx phase.

The router now supports dedicated Phase 6 and Phase 7 feature modules while
keeping the general provider-backed response flow for other prompts. Routing is
still intentionally lightweight until later context and module phases arrive.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging

from nyx.bridges.base import SystemBridge
from nyx.calendar.service import CalendarService
from nyx.config import NyxConfig
from nyx.modules.calendar import CalendarModule
from nyx.modules.git_github import GitHubModule
from nyx.modules.macros import MacrosModule
from nyx.modules.memory import MemoryModule
from nyx.modules.notes import NotesModule
from nyx.modules.rag import RagModule
from nyx.modules.screen_context import ScreenContextModule
from nyx.modules.system_control import SystemControlModule
from nyx.modules.tasks import TasksModule
from nyx.providers.base import ProviderError
from nyx.providers.registry import ProviderQueryResult, ProviderRegistry
from nyx.rag import ChromaRagStore, OllamaEmbedder, RagService


@dataclass(slots=True)
class IntentRequest:
    """Input passed from CLI or future UI layers into the intent router."""

    text: str
    model_override: str | None
    yolo: bool


@dataclass(slots=True)
class IntentResult:
    """Router output returned to callers after intent classification."""

    response_text: str
    intent: str
    target_module: str | None
    used_model: str | None
    degraded: bool
    model_name: str | None = None
    token_count: int | None = None


class IntentRouter:
    """Current Nyx intent router built on the provider registry contract."""

    def __init__(
        self,
        config: NyxConfig,
        bridge: SystemBridge,
        provider_registry: ProviderRegistry,
        calendar_module: CalendarModule | None = None,
        git_github_module: GitHubModule | None = None,
        macros_module: MacrosModule | None = None,
        memory_module: MemoryModule | None = None,
        notes_module: NotesModule | None = None,
        rag_module: RagModule | None = None,
        screen_context_module: ScreenContextModule | None = None,
        system_control_module: SystemControlModule | None = None,
        tasks_module: TasksModule | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the router with explicit dependencies.

        Args:
            config: Loaded Nyx configuration.
            bridge: Active system bridge implementation. It is stored now so the
                routing API matches later phases and backs system-control
                execution paths.
            provider_registry: Provider registry responsible for model selection,
                availability checks, and fallback behavior.
            calendar_module: Optional prebuilt calendar module.
            git_github_module: Optional prebuilt git/github module.
            macros_module: Optional prebuilt macros module.
            memory_module: Optional prebuilt memory module.
            notes_module: Optional prebuilt notes module.
            rag_module: Optional prebuilt RAG module.
            screen_context_module: Optional prebuilt screen-context module.
            system_control_module: Optional prebuilt system-control module.
            tasks_module: Optional prebuilt tasks module.
            logger: Optional logger for router diagnostics.
        """

        self.config = config
        self.bridge = bridge
        self.provider_registry = provider_registry
        self.logger = logger or logging.getLogger("nyx.intent_router")
        self.calendar_module = calendar_module or CalendarModule(
            config=config,
            provider_registry=provider_registry,
            calendar_service=CalendarService(config=config, logger=self.logger),
            logger=self.logger,
        )
        self.git_github_module = git_github_module or GitHubModule(
            config=config,
            provider_registry=provider_registry,
            logger=self.logger,
        )
        self.macros_module = macros_module or MacrosModule(
            config=config,
            bridge=bridge,
            provider_registry=provider_registry,
            logger=self.logger,
        )
        self.memory_module = memory_module or MemoryModule(
            config=config,
            provider_registry=provider_registry,
            logger=self.logger,
        )
        self.notes_module = notes_module or NotesModule(
            config=config,
            provider_registry=provider_registry,
            logger=self.logger,
        )
        self.tasks_module = tasks_module or TasksModule(
            config=config,
            provider_registry=provider_registry,
            logger=self.logger,
        )
        self.rag_module = rag_module or RagModule(
            config=config,
            provider_registry=provider_registry,
            rag_service=RagService(
                config=config,
                store=ChromaRagStore(db_path=config.rag.db_path, logger=self.logger),
                embedder=OllamaEmbedder(config=config, logger=self.logger),
                logger=self.logger,
            ),
            logger=self.logger,
        )
        self.screen_context_module = screen_context_module or ScreenContextModule(
            config=config,
            bridge=bridge,
            provider_registry=provider_registry,
            logger=self.logger,
        )
        self.system_control_module = system_control_module or SystemControlModule(
            config=config,
            bridge=bridge,
            provider_registry=provider_registry,
            logger=self.logger,
        )

    async def route(self, request: IntentRequest) -> IntentResult:
        """Route a prompt through the provider layer.

        Args:
            request: The user request to route.

        Returns:
            An ``IntentResult`` containing the selected provider output or a
            degraded fallback message when no configured providers succeed.
        """

        requested_provider = request.model_override or self.config.models.default
        self.logger.info(
            "Routing request with provider=%s yolo=%s",
            requested_provider,
            request.yolo,
        )

        if self.memory_module.matches_request(request.text):
            return await self._route_memory(request)

        if self.macros_module.matches_request(request.text):
            return await self._route_macros(request)

        if self.calendar_module.matches_request(request.text):
            return await self._route_calendar(request)

        if self.git_github_module.matches_request(request.text):
            return await self._route_git_github(request)

        if self.screen_context_module.matches_request(request.text):
            return await self._route_screen_context(request)

        if self.rag_module.matches_request(request.text):
            return await self._route_rag(request)

        if self.tasks_module.matches_request(request.text):
            return await self._route_tasks(request)

        if self.notes_module.matches_request(request.text):
            return await self._route_notes(request)

        if self.system_control_module.matches_request(request.text):
            return await self._route_system_control(request)

        try:
            provider_result = await self.provider_registry.query(
                prompt=request.text,
                context={},
                preferred_provider_name=request.model_override,
            )
        except ProviderError as exc:
            self.logger.warning("Provider query failed: %s", exc)
            return IntentResult(
                response_text=f"Nyx could not reach any configured providers: {exc}",
                intent="unclassified",
                target_module=None,
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )

        return self._result_from_provider(provider_result)

    async def _route_system_control(self, request: IntentRequest) -> IntentResult:
        """Dispatch an obvious system-control request into the Phase 6 module."""

        try:
            module_result = await self.system_control_module.handle(
                request_text=request.text,
                model_override=request.model_override,
            )
        except ProviderError as exc:
            self.logger.warning("System-control planning failed: %s", exc)
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not plan the system action: {exc}",
                intent="system_control",
                target_module="system_control",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )
        except Exception as exc:
            self.logger.exception("System-control routing failed.")
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not execute the system action: {exc}",
                intent="system_control",
                target_module="system_control",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )

        return IntentResult(
            response_text=module_result.response_text,
            intent="system_control",
            target_module="system_control",
            used_model=module_result.used_model,
            degraded=module_result.degraded,
            model_name=module_result.model_name,
            token_count=module_result.token_count,
        )

    async def _route_macros(self, request: IntentRequest) -> IntentResult:
        """Dispatch an obvious macros request into the Phase 15 module."""

        try:
            module_result = await self.macros_module.handle(
                request_text=request.text,
                model_override=request.model_override,
            )
        except ProviderError as exc:
            self.logger.warning("Macros planning failed: %s", exc)
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not plan the macro action: {exc}",
                intent="macros",
                target_module="macros",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )
        except Exception:
            self.logger.exception("Macros routing failed.")
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text="Nyx could not execute the macro action.",
                intent="macros",
                target_module="macros",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )

        return IntentResult(
            response_text=module_result.response_text,
            intent="macros",
            target_module="macros",
            used_model=module_result.used_model,
            degraded=module_result.degraded,
            model_name=module_result.model_name,
            token_count=module_result.token_count,
        )

    async def _route_notes(self, request: IntentRequest) -> IntentResult:
        """Dispatch an obvious notes request into the Phase 7 module."""

        try:
            module_result = await self.notes_module.handle(
                request_text=request.text,
                model_override=request.model_override,
            )
        except ProviderError as exc:
            self.logger.warning("Notes planning failed: %s", exc)
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not plan the notes action: {exc}",
                intent="notes",
                target_module="notes",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )
        except Exception as exc:
            self.logger.exception("Notes routing failed.")
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not execute the notes action: {exc}",
                intent="notes",
                target_module="notes",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )

        return IntentResult(
            response_text=module_result.response_text,
            intent="notes",
            target_module="notes",
            used_model=module_result.used_model,
            degraded=module_result.degraded,
            model_name=module_result.model_name,
            token_count=module_result.token_count,
        )

    async def _route_memory(self, request: IntentRequest) -> IntentResult:
        """Dispatch an obvious persistent-memory request into the Phase 10 module."""

        try:
            module_result = await self.memory_module.handle(
                request_text=request.text,
                model_override=request.model_override,
            )
        except ProviderError as exc:
            self.logger.warning("Memory planning failed: %s", exc)
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not plan the memory update: {exc}",
                intent="memory",
                target_module="memory",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )
        except Exception as exc:
            self.logger.exception("Memory routing failed.")
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not execute the memory action: {exc}",
                intent="memory",
                target_module="memory",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )

        return IntentResult(
            response_text=module_result.response_text,
            intent="memory",
            target_module="memory",
            used_model=module_result.used_model,
            degraded=module_result.degraded,
            model_name=module_result.model_name,
            token_count=module_result.token_count,
        )

    async def _route_tasks(self, request: IntentRequest) -> IntentResult:
        """Dispatch an obvious tasks request into the Phase 13 module."""

        try:
            module_result = await self.tasks_module.handle(
                request_text=request.text,
                model_override=request.model_override,
            )
        except ProviderError as exc:
            self.logger.warning("Tasks planning failed: %s", exc)
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not plan the task action: {exc}",
                intent="tasks",
                target_module="tasks",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )
        except Exception as exc:
            self.logger.exception("Tasks routing failed.")
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not execute the task action: {exc}",
                intent="tasks",
                target_module="tasks",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )

        return IntentResult(
            response_text=module_result.response_text,
            intent="tasks",
            target_module="tasks",
            used_model=module_result.used_model,
            degraded=module_result.degraded,
            model_name=module_result.model_name,
            token_count=module_result.token_count,
        )

    async def _route_git_github(self, request: IntentRequest) -> IntentResult:
        """Dispatch an obvious git/GitHub request into the Phase 12 module."""

        try:
            module_result = await self.git_github_module.handle(
                request_text=request.text,
                model_override=request.model_override,
            )
        except ProviderError as exc:
            self.logger.warning("Git/GitHub planning failed: %s", exc)
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not plan the git/github action: {exc}",
                intent="git_github",
                target_module="git_github",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )
        except Exception as exc:
            self.logger.exception("Git/GitHub routing failed.")
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not execute the git/github action: {exc}",
                intent="git_github",
                target_module="git_github",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )

        return IntentResult(
            response_text=module_result.response_text,
            intent="git_github",
            target_module="git_github",
            used_model=module_result.used_model,
            degraded=module_result.degraded,
            model_name=module_result.model_name,
            token_count=module_result.token_count,
        )

    async def _route_calendar(self, request: IntentRequest) -> IntentResult:
        """Dispatch an obvious calendar request into the Phase 14 module."""

        try:
            module_result = await self.calendar_module.handle(
                request_text=request.text,
                model_override=request.model_override,
            )
        except ProviderError as exc:
            self.logger.warning("Calendar planning failed: %s", exc)
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not plan the calendar action: {exc}",
                intent="calendar",
                target_module="calendar",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )
        except Exception as exc:
            self.logger.exception("Calendar routing failed.")
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not execute the calendar action: {exc}",
                intent="calendar",
                target_module="calendar",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )

        return IntentResult(
            response_text=module_result.response_text,
            intent="calendar",
            target_module="calendar",
            used_model=module_result.used_model,
            degraded=module_result.degraded,
            model_name=module_result.model_name,
            token_count=module_result.token_count,
        )

    async def _route_screen_context(self, request: IntentRequest) -> IntentResult:
        """Dispatch an explicit screen-analysis request into the Phase 11 module."""

        try:
            module_result = await self.screen_context_module.handle(
                request_text=request.text,
                model_override=request.model_override,
            )
        except ProviderError as exc:
            self.logger.warning("Screen-context planning failed: %s", exc)
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not analyze the screen: {exc}",
                intent="screen_context",
                target_module="screen_context",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )
        except Exception as exc:
            self.logger.exception("Screen-context routing failed.")
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not analyze the screen: {exc}",
                intent="screen_context",
                target_module="screen_context",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )

        return IntentResult(
            response_text=module_result.response_text,
            intent="screen_context",
            target_module="screen_context",
            used_model=module_result.used_model,
            degraded=module_result.degraded,
            model_name=module_result.model_name,
            token_count=module_result.token_count,
        )

    async def _route_rag(self, request: IntentRequest) -> IntentResult:
        """Dispatch an obvious RAG lookup request into the Phase 8 module."""

        try:
            module_result = await self.rag_module.handle(
                request_text=request.text,
                model_override=request.model_override,
            )
        except ProviderError as exc:
            self.logger.warning("RAG planning failed: %s", exc)
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not plan the RAG lookup: {exc}",
                intent="rag",
                target_module="rag",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )
        except Exception as exc:
            self.logger.exception("RAG routing failed.")
            requested_provider = request.model_override or self.config.models.default
            return IntentResult(
                response_text=f"Nyx could not execute the RAG lookup: {exc}",
                intent="rag",
                target_module="rag",
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )

        return IntentResult(
            response_text=module_result.response_text,
            intent="rag",
            target_module="rag",
            used_model=module_result.used_model,
            degraded=module_result.degraded,
            model_name=module_result.model_name,
            token_count=module_result.token_count,
        )

    def _result_from_provider(self, provider_result: ProviderQueryResult) -> IntentResult:
        """Convert provider output into the current router result contract."""

        return IntentResult(
            response_text=provider_result.text,
            intent="unclassified",
            target_module=None,
            used_model=provider_result.provider_name,
            degraded=provider_result.fallback_used,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
        )
