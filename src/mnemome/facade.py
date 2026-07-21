from __future__ import annotations

from typing import Any

from .adapters import InMemoryStores, SqliteStores
from .application import MnemomeApplication
from .contracts import (
    AgentDescriptor,
    AgentEvent,
    AgentRun,
    Checkpoint,
    ContextBundle,
    FactInput,
    OpenRunRequest,
)
from .ports import Stores


class AgentEnvironment:
    def __init__(
        self, application: MnemomeApplication, run: AgentRun, context: ContextBundle
    ) -> None:
        self._application = application
        self.run = run
        self._context = context

    async def get_context(self) -> ContextBundle:
        return self._context

    async def record_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        caller_event_id: str | None = None,
    ) -> AgentEvent:
        return await self._application.append_agent_event(
            self.run.tenant_id,
            self.run.run_id,
            event_type,
            payload,
            caller_event_id=caller_event_id,
        )

    async def checkpoint(
        self, checkpoint_ref: str, metadata: dict[str, Any] | None = None
    ) -> Checkpoint:
        return await self._application.record_checkpoint(
            self.run.tenant_id, self.run.run_id, checkpoint_ref, metadata
        )

    async def complete(
        self,
        outcome: dict[str, Any],
        *,
        response_ref: str | None = None,
        facts: tuple[FactInput, ...] = (),
    ) -> AgentRun:
        self.run = await self._application.complete_run(
            self.run.tenant_id,
            self.run.run_id,
            outcome,
            response_ref=response_ref,
            facts=facts,
        )
        return self.run

    async def fail(self, failure: dict[str, Any]) -> AgentRun:
        self.run = await self._application.fail_run(
            self.run.tenant_id, self.run.run_id, failure
        )
        return self.run


class AgentEnvironmentFacade:
    def __init__(self, application: MnemomeApplication) -> None:
        self._application = application

    async def open_run(self, request: OpenRunRequest) -> AgentEnvironment:
        run, context = await self._application.open_run(request)
        return AgentEnvironment(self._application, run, context)


class Mnemome:
    def __init__(self, stores: Stores) -> None:
        self.application = MnemomeApplication(stores)
        self.agent_environment = AgentEnvironmentFacade(self.application)

    @classmethod
    def in_memory(cls) -> Mnemome:
        return cls(InMemoryStores())

    @classmethod
    def sqlite(cls, path: str) -> Mnemome:
        return cls(SqliteStores(path))

    async def __aenter__(self) -> Mnemome:
        await self.initialize()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def initialize(self) -> None:
        await self.application.initialize()

    async def close(self) -> None:
        await self.application.close()

    async def register_agent(
        self, tenant_id: str, name: str, capabilities: tuple[str, ...] = ()
    ) -> AgentDescriptor:
        return await self.application.register_agent(tenant_id, name, capabilities)
