"""Intent routing for Nyx."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from nyx.bridges.base import SystemBridge
from nyx.calendar.service import CalendarService
from nyx.config import NyxConfig
from nyx.modules.calendar import CalendarModule
from nyx.modules.cross_device_sync import CrossDeviceSyncModule
from nyx.modules.git_github import GitHubModule
from nyx.modules.macros import MacrosModule
from nyx.modules.memory import MemoryModule
from nyx.modules.notes import NotesModule
from nyx.modules.rag import RagModule
from nyx.modules.screen_context import ScreenContextModule
from nyx.modules.skills import SkillsModule
from nyx.modules.system_control import SystemControlModule
from nyx.modules.system_monitor import SystemMonitorModule
from nyx.modules.tasks import TasksModule
from nyx.modules.web_lookup import WebLookupModule
from nyx.providers.base import ProviderError, ProviderMessage
from nyx.providers.registry import ProviderQueryResult, ProviderRegistry
from nyx.rag import ChromaRagStore, OllamaEmbedder, RagService
from nyx.sync import CrossDeviceSyncService
from nyx.web import WebLookupService


@dataclass(slots=True)
class IntentRequest:
    """Input passed from CLI or UI layers into the intent router."""

    text: str
    model_override: str | None
    yolo: bool
    conversation_messages: list[ProviderMessage] | None = None


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


@dataclass(slots=True)
class ModuleRouteSpec:
    """Lazy route definition for one intent-handling module."""

    name: str
    intent: str
    matcher: Callable[[str], bool]
    planning_error_message: str
    execution_error_message: str


class IntentRouter:
    """Intent router built on the provider registry contract."""

    def __init__(
        self,
        config: NyxConfig,
        bridge: SystemBridge,
        provider_registry: ProviderRegistry,
        calendar_module: CalendarModule | None = None,
        cross_device_sync_module: CrossDeviceSyncModule | None = None,
        git_github_module: GitHubModule | None = None,
        macros_module: MacrosModule | None = None,
        memory_module: MemoryModule | None = None,
        notes_module: NotesModule | None = None,
        rag_module: RagModule | None = None,
        screen_context_module: ScreenContextModule | None = None,
        skills_module: SkillsModule | None = None,
        system_monitor_module: SystemMonitorModule | None = None,
        system_control_module: SystemControlModule | None = None,
        tasks_module: TasksModule | None = None,
        web_lookup_module: WebLookupModule | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the router with explicit dependencies."""

        self.config = config
        self.bridge = bridge
        self.provider_registry = provider_registry
        self.logger = logger or logging.getLogger("nyx.intent_router")
        self._module_instances: dict[str, Any] = {}
        self._module_overrides = {
            "calendar": calendar_module,
            "cross_device_sync": cross_device_sync_module,
            "git_github": git_github_module,
            "macros": macros_module,
            "memory": memory_module,
            "notes": notes_module,
            "rag": rag_module,
            "screen_context": screen_context_module,
            "skills": skills_module,
            "system_control": system_control_module,
            "system_monitor": system_monitor_module,
            "tasks": tasks_module,
            "web_lookup": web_lookup_module,
        }
        self._module_factories = self._build_module_factories()
        self._route_specs = self._build_route_specs()

    async def route(self, request: IntentRequest) -> IntentResult:
        """Route a prompt through the provider layer and feature modules."""

        requested_provider = request.model_override or self.config.models.default
        self.logger.info(
            "Routing request with provider=%s yolo=%s",
            requested_provider,
            request.yolo,
        )

        for spec in self._route_specs:
            if spec.matcher(request.text):
                return await self._dispatch_module_request(spec, request)

        skills_module = self._get_module("skills")
        skill_result = await skills_module.maybe_handle(
            request_text=request.text,
            model_override=request.model_override,
        )
        if skill_result is not None:
            return IntentResult(
                response_text=skill_result.response_text,
                intent="skills",
                target_module="skills",
                used_model=skill_result.used_model,
                degraded=skill_result.degraded,
                model_name=skill_result.model_name,
                token_count=skill_result.token_count,
            )

        try:
            if request.conversation_messages:
                provider_result = await self.provider_registry.query_messages(
                    messages=request.conversation_messages,
                    context={},
                    preferred_provider_name=request.model_override,
                )
            else:
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

    def reload_config(self, new_config: NyxConfig, provider_registry: ProviderRegistry) -> None:
        """Reload runtime config and clear lazy module instances."""

        self.config = new_config
        self.provider_registry = provider_registry
        self._module_instances.clear()
        self._module_factories = self._build_module_factories()
        self._route_specs = self._build_route_specs()

    def _build_module_factories(self) -> dict[str, Callable[[], Any]]:
        """Build lazy factories for every feature module."""

        return {
            "calendar": lambda: CalendarModule(
                config=self.config,
                provider_registry=self.provider_registry,
                calendar_service=CalendarService(config=self.config, logger=self.logger),
                logger=self.logger,
            ),
            "cross_device_sync": lambda: CrossDeviceSyncModule(
                config=self.config,
                provider_registry=self.provider_registry,
                sync_service=CrossDeviceSyncService(config=self.config, logger=self.logger),
                logger=self.logger,
            ),
            "git_github": lambda: GitHubModule(
                config=self.config,
                provider_registry=self.provider_registry,
                logger=self.logger,
            ),
            "macros": lambda: MacrosModule(
                config=self.config,
                bridge=self.bridge,
                provider_registry=self.provider_registry,
                logger=self.logger,
            ),
            "memory": lambda: MemoryModule(
                config=self.config,
                provider_registry=self.provider_registry,
                logger=self.logger,
            ),
            "notes": lambda: NotesModule(
                config=self.config,
                provider_registry=self.provider_registry,
                logger=self.logger,
            ),
            "rag": lambda: RagModule(
                config=self.config,
                provider_registry=self.provider_registry,
                rag_service=RagService(
                    config=self.config,
                    store=ChromaRagStore(db_path=self.config.rag.db_path, logger=self.logger),
                    embedder=OllamaEmbedder(config=self.config, logger=self.logger),
                    logger=self.logger,
                ),
                logger=self.logger,
            ),
            "screen_context": lambda: ScreenContextModule(
                config=self.config,
                bridge=self.bridge,
                provider_registry=self.provider_registry,
                logger=self.logger,
            ),
            "skills": lambda: SkillsModule(
                config=self.config,
                bridge=self.bridge,
                provider_registry=self.provider_registry,
                logger=self.logger,
            ),
            "system_control": lambda: SystemControlModule(
                config=self.config,
                bridge=self.bridge,
                provider_registry=self.provider_registry,
                logger=self.logger,
            ),
            "system_monitor": lambda: SystemMonitorModule(
                config=self.config,
                provider_registry=self.provider_registry,
                logger=self.logger,
            ),
            "tasks": lambda: TasksModule(
                config=self.config,
                provider_registry=self.provider_registry,
                logger=self.logger,
            ),
            "web_lookup": lambda: WebLookupModule(
                config=self.config,
                provider_registry=self.provider_registry,
                web_service=WebLookupService(config=self.config, logger=self.logger),
                logger=self.logger,
            ),
        }

    def _build_route_specs(self) -> list[ModuleRouteSpec]:
        """Build ordered route specs without instantiating modules."""

        return [
            self._module_spec(
                "memory",
                "memory",
                MemoryModule.matches_request,
                "Nyx could not plan the memory update: {error}",
                "Nyx could not execute the memory action: {error}",
            ),
            self._module_spec(
                "macros",
                "macros",
                MacrosModule.matches_request,
                "Nyx could not plan the macro action: {error}",
                "Nyx could not execute the macro action: {error}",
            ),
            self._module_spec(
                "calendar",
                "calendar",
                CalendarModule.matches_request,
                "Nyx could not plan the calendar action: {error}",
                "Nyx could not execute the calendar action: {error}",
            ),
            self._module_spec(
                "cross_device_sync",
                "cross_device_sync",
                CrossDeviceSyncModule.matches_request,
                "Nyx could not plan the cross-device sync action: {error}",
                "Nyx could not execute the cross-device sync action: {error}",
            ),
            self._module_spec(
                "git_github",
                "git_github",
                GitHubModule.matches_request,
                "Nyx could not plan the git/github action: {error}",
                "Nyx could not execute the git/github action: {error}",
            ),
            self._module_spec(
                "screen_context",
                "screen_context",
                ScreenContextModule.matches_request,
                "Nyx could not analyze the screen: {error}",
                "Nyx could not execute the screen-context action: {error}",
            ),
            self._module_spec(
                "web_lookup",
                "web_lookup",
                WebLookupModule.matches_request,
                "Nyx could not plan the web lookup: {error}",
                "Nyx could not execute the web lookup: {error}",
            ),
            self._module_spec(
                "rag",
                "rag",
                RagModule.matches_request,
                "Nyx could not plan the local search: {error}",
                "Nyx could not execute the local search: {error}",
            ),
            self._module_spec(
                "tasks",
                "tasks",
                TasksModule.matches_request,
                "Nyx could not plan the task action: {error}",
                "Nyx could not execute the task action: {error}",
            ),
            self._module_spec(
                "notes",
                "notes",
                NotesModule.matches_request,
                "Nyx could not plan the notes action: {error}",
                "Nyx could not execute the notes action: {error}",
            ),
            self._module_spec(
                "system_monitor",
                "system_monitor",
                SystemMonitorModule.matches_request,
                "Nyx could not plan the monitor action: {error}",
                "Nyx could not execute the monitor action: {error}",
            ),
            self._module_spec(
                "system_control",
                "system_control",
                SystemControlModule.matches_request,
                "Nyx could not plan the system action: {error}",
                "Nyx could not execute the system action: {error}",
            ),
        ]

    def _module_spec(
        self,
        name: str,
        intent: str,
        matcher: Callable[[str], bool],
        planning_error_message: str,
        execution_error_message: str,
    ) -> ModuleRouteSpec:
        """Create one module route specification."""

        return ModuleRouteSpec(
            name=name,
            intent=intent,
            matcher=matcher,
            planning_error_message=planning_error_message,
            execution_error_message=execution_error_message,
        )

    def _get_module(self, name: str) -> Any:
        """Return a lazily initialized module instance."""

        if name in self._module_instances:
            return self._module_instances[name]

        override = self._module_overrides.get(name)
        if override is not None:
            self._module_instances[name] = override
            return override

        factory = self._module_factories[name]
        module = factory()
        self._module_instances[name] = module
        return module

    async def _dispatch_module_request(
        self,
        spec: ModuleRouteSpec,
        request: IntentRequest,
    ) -> IntentResult:
        """Dispatch a routed request into one module using shared error handling."""

        requested_provider = request.model_override or self.config.models.default
        module = self._get_module(spec.name)
        try:
            module_result = await module.handle(
                request_text=request.text,
                model_override=request.model_override,
            )
        except ProviderError as exc:
            self.logger.warning("%s planning failed: %s", spec.name.replace("_", "-"), exc)
            return IntentResult(
                response_text=spec.planning_error_message.format(error=exc),
                intent=spec.intent,
                target_module=spec.name,
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )
        except Exception as exc:
            self.logger.exception("%s routing failed.", spec.name.replace("_", "-").capitalize())
            return IntentResult(
                response_text=spec.execution_error_message.format(error=exc),
                intent=spec.intent,
                target_module=spec.name,
                used_model=requested_provider,
                degraded=True,
                model_name=None,
                token_count=None,
            )

        return IntentResult(
            response_text=module_result.response_text,
            intent=spec.intent,
            target_module=spec.name,
            used_model=module_result.used_model,
            degraded=module_result.degraded,
            model_name=module_result.model_name,
            token_count=module_result.token_count,
        )

    def _result_from_provider(self, provider_result: ProviderQueryResult) -> IntentResult:
        """Convert one provider registry result into a router result."""

        return IntentResult(
            response_text=provider_result.text,
            intent="unclassified",
            target_module=None,
            used_model=provider_result.provider_name,
            degraded=provider_result.degraded,
            model_name=provider_result.model_name,
            token_count=provider_result.token_count,
        )
