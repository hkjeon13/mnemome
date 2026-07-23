from __future__ import annotations

from typing import Any

from .adapters import InMemoryStores, PostgresStores, SqliteStores
from .application import MnemomeApplication
from .contracts import (
    AgentDescriptor,
    AgentEvent,
    AgentRun,
    Checkpoint,
    ContextBundle,
    CulturalArtifact,
    CulturalSnapshot,
    FactInput,
    OpenRunRequest,
    ResolvedCulturalArtifact,
    SourceRef,
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


class CulturalMemoryFacade:
    """Library profile for governing and resolving versioned cultural memory."""

    def __init__(self, application: MnemomeApplication) -> None:
        self._application = application

    async def create(
        self,
        tenant_id: str,
        scope: str,
        claim: str,
        *,
        conditions: tuple[str, ...] = (),
        restrictions: tuple[str, ...] = (),
        recovery: str | None = None,
        evidence_refs: tuple[SourceRef, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> CulturalArtifact:
        return await self._application.create_cultural_artifact(
            tenant_id,
            scope,
            claim,
            conditions=conditions,
            restrictions=restrictions,
            recovery=recovery,
            evidence_refs=evidence_refs,
            metadata=metadata,
        )

    async def revise(
        self, tenant_id: str, artifact_id: str, **changes: Any
    ) -> CulturalArtifact:
        return await self._application.revise_cultural_artifact(
            tenant_id, artifact_id, **changes
        )

    async def withdraw(self, tenant_id: str, artifact_id: str) -> CulturalArtifact:
        return await self._application.withdraw_cultural_artifact(tenant_id, artifact_id)

    async def list(
        self, tenant_id: str, *, scope: str | None = None, include_withdrawn: bool = False
    ) -> tuple[CulturalArtifact, ...]:
        return await self._application.list_cultural_artifacts(
            tenant_id, scope=scope, include_withdrawn=include_withdrawn
        )

    async def publish(
        self,
        tenant_id: str,
        scope: str,
        *,
        artifact_ids: tuple[str, ...] | None = None,
        policy_version: str = "culture-policy-v1",
    ) -> CulturalSnapshot:
        return await self._application.publish_cultural_snapshot(
            tenant_id,
            scope,
            artifact_ids=artifact_ids,
            policy_version=policy_version,
        )

    async def resolve(
        self, tenant_id: str, scope: str = "default"
    ) -> tuple[CulturalSnapshot | None, tuple[ResolvedCulturalArtifact, ...]]:
        return await self._application.resolve_cultural_snapshot(tenant_id, scope)


class Mnemome:
    def __init__(self, stores: Stores) -> None:
        self.application = MnemomeApplication(stores)
        self.agent_environment = AgentEnvironmentFacade(self.application)
        self.cultural_memory = CulturalMemoryFacade(self.application)

    @classmethod
    def in_memory(cls) -> Mnemome:
        return cls(InMemoryStores())

    @classmethod
    def sqlite(cls, path: str) -> Mnemome:
        return cls(SqliteStores(path))

    @classmethod
    def postgres(
        cls,
        database_url: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        command_timeout: float = 5.0,
    ) -> Mnemome:
        return cls(
            PostgresStores(
                database_url,
                min_pool_size=min_pool_size,
                max_pool_size=max_pool_size,
                command_timeout=command_timeout,
            )
        )

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
