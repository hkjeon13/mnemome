from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from .contracts import (
    AgentDescriptor,
    AgentEvent,
    AgentRun,
    Checkpoint,
    ContextBundle,
    DomainEvent,
    FactInput,
    FactStatus,
    MemoryFact,
    OpenRunRequest,
    RecalledFact,
    RunStatus,
    SourceRef,
    utc_now,
)
from .errors import ConflictError, NotFoundError, ValidationError
from .ids import SortableIdGenerator
from .ports import IdGenerator, Stores
from .retrieval import bm25_scores

_TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
class MnemomeApplication:
    """Transport-independent use cases shared by embedded and service profiles."""

    def __init__(self, stores: Stores, *, ids: IdGenerator | None = None) -> None:
        self.stores = stores
        self.ids = ids or SortableIdGenerator()
        self._mutation_lock = asyncio.Lock()

    async def initialize(self) -> None:
        await self.stores.initialize()

    async def close(self) -> None:
        await self.stores.close()

    async def register_agent(
        self, tenant_id: str, name: str, capabilities: tuple[str, ...] = ()
    ) -> AgentDescriptor:
        if not tenant_id.strip() or not name.strip():
            raise ValidationError("tenant_id and agent name are required")
        agent = AgentDescriptor(
            agent_id=self.ids.new("agt"),
            tenant_id=tenant_id,
            name=name.strip(),
            version=1,
            capabilities=tuple(sorted(set(capabilities))),
            created_at=utc_now(),
        )
        await self.stores.save_agent(agent)
        await self._emit(
            tenant_id,
            "agent.registered.v1",
            "agent",
            agent.agent_id,
            1,
            {"name": agent.name},
        )
        return agent

    async def open_run(self, request: OpenRunRequest) -> tuple[AgentRun, ContextBundle]:
        agent = await self.stores.get_agent(request.tenant_id, request.agent_id)
        if agent is None:
            raise NotFoundError("Agent was not found in the current tenant")
        if (
            request.agent_descriptor_version is not None
            and request.agent_descriptor_version != agent.version
        ):
            raise ConflictError(
                "The requested agent descriptor version is not current",
                details={"current_version": agent.version},
            )
        recalled = (
            await self.recall(request.tenant_id, request.retrieval_text, limit=8)
            if request.recall and request.retrieval_text
            else ()
        )
        now = utc_now()
        run = AgentRun(
            run_id=self.ids.new("run"),
            tenant_id=request.tenant_id,
            agent_id=agent.agent_id,
            agent_descriptor_version=agent.version,
            status=RunStatus.ACTIVE,
            context_version=1,
            cultural_snapshot_id=f"csp_none_{request.cultural_scope.replace('/', '_')}",
            workspace_id=request.workspace_id,
            query_ref=request.query_ref,
            write_episode=request.write_episode,
            created_at=now,
            updated_at=now,
        )
        context = ContextBundle(
            run_id=run.run_id,
            context_version=run.context_version,
            cultural_snapshot_id=run.cultural_snapshot_id,
            recalled_facts=recalled,
            created_at=now,
        )
        await self.stores.save_run(run)
        await self._emit(
            run.tenant_id,
            "agent.run.opened.v1",
            "agent_run",
            run.run_id,
            run.context_version,
            {"agent_id": run.agent_id, "snapshot_id": run.cultural_snapshot_id},
        )
        return run, context

    async def get_run(self, tenant_id: str, run_id: str) -> AgentRun:
        run = await self.stores.get_run(tenant_id, run_id)
        if run is None:
            raise NotFoundError("Run was not found in the current tenant")
        return run

    async def append_agent_event(
        self,
        tenant_id: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        caller_event_id: str | None = None,
    ) -> AgentEvent:
        if not event_type.strip():
            raise ValidationError("event_type is required")
        async with self._mutation_lock:
            run = await self.get_run(tenant_id, run_id)
            self._require_active(run)
            existing = await self.stores.list_agent_events(tenant_id, run_id)
            event_id = caller_event_id or self.ids.new("aev")
            duplicate = next((event for event in existing if event.event_id == event_id), None)
            if duplicate:
                return duplicate
            event = AgentEvent(
                event_id=event_id,
                run_id=run_id,
                tenant_id=tenant_id,
                sequence=len(existing) + 1,
                event_type=event_type.strip(),
                payload=payload,
                occurred_at=utc_now(),
            )
            await self.stores.append_agent_event(event)
            updated = replace(
                run, context_version=run.context_version + 1, updated_at=event.occurred_at
            )
            await self.stores.save_run(updated)
            return event

    async def record_checkpoint(
        self,
        tenant_id: str,
        run_id: str,
        checkpoint_ref: str,
        metadata: dict[str, Any] | None = None,
    ) -> Checkpoint:
        if not checkpoint_ref.strip():
            raise ValidationError("checkpoint_ref is required")
        async with self._mutation_lock:
            run = await self.get_run(tenant_id, run_id)
            self._require_active(run)
            now = utc_now()
            checkpoint = Checkpoint(
                checkpoint_id=self.ids.new("chk"),
                run_id=run.run_id,
                tenant_id=tenant_id,
                context_version=run.context_version + 1,
                checkpoint_ref=checkpoint_ref,
                metadata=metadata or {},
                created_at=now,
            )
            await self.stores.save_checkpoint(checkpoint)
            await self.stores.save_run(
                replace(run, context_version=checkpoint.context_version, updated_at=now)
            )
            return checkpoint

    async def complete_run(
        self,
        tenant_id: str,
        run_id: str,
        outcome: dict[str, Any],
        *,
        response_ref: str | None = None,
        facts: tuple[FactInput, ...] = (),
    ) -> AgentRun:
        async with self._mutation_lock:
            run = await self.get_run(tenant_id, run_id)
            self._require_active(run)
            now = utc_now()
            completed = replace(
                run,
                status=RunStatus.COMPLETED,
                outcome=outcome,
                response_ref=response_ref,
                context_version=run.context_version + 1,
                updated_at=now,
            )
            await self.stores.save_run(completed)
            if run.write_episode:
                for fact in facts:
                    sources = fact.sources or (
                        SourceRef(source_type="agent_run", source_id=run.run_id),
                    )
                    await self.create_fact(
                        tenant_id,
                        fact.statement,
                        confidence=fact.confidence,
                        sources=sources,
                    )
            await self._emit(
                tenant_id,
                "agent.run.completed.v1",
                "agent_run",
                run.run_id,
                completed.context_version,
                {"response_ref": response_ref, "fact_count": len(facts)},
            )
            return completed

    async def fail_run(
        self, tenant_id: str, run_id: str, failure: dict[str, Any]
    ) -> AgentRun:
        async with self._mutation_lock:
            run = await self.get_run(tenant_id, run_id)
            self._require_active(run)
            now = utc_now()
            failed = replace(
                run,
                status=RunStatus.FAILED,
                failure=failure,
                context_version=run.context_version + 1,
                updated_at=now,
            )
            await self.stores.save_run(failed)
            await self._emit(
                tenant_id,
                "agent.run.failed.v1",
                "agent_run",
                run.run_id,
                failed.context_version,
                {},
            )
            return failed

    async def request_cancel(self, tenant_id: str, run_id: str) -> AgentRun:
        async with self._mutation_lock:
            run = await self.get_run(tenant_id, run_id)
            self._require_active(run)
            updated = replace(run, status=RunStatus.CANCEL_REQUESTED, updated_at=utc_now())
            await self.stores.save_run(updated)
            return updated

    async def list_agent_events(self, tenant_id: str, run_id: str) -> list[AgentEvent]:
        await self.get_run(tenant_id, run_id)
        return await self.stores.list_agent_events(tenant_id, run_id)

    async def create_fact(
        self,
        tenant_id: str,
        statement: str,
        *,
        confidence: float = 1.0,
        sources: tuple[SourceRef, ...],
        kind: str = "fact",
        tags: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
        fact_id: str | None = None,
        supersedes_fact_id: str | None = None,
    ) -> MemoryFact:
        if not statement.strip():
            raise ValidationError("Fact statement is required")
        if not 0 <= confidence <= 1:
            raise ValidationError("Fact confidence must be between 0 and 1")
        if not sources:
            raise ValidationError("A derived fact must have at least one source reference")
        normalized_kind = kind.strip().lower()
        if normalized_kind not in {"fact", "preference", "episode", "conversation"}:
            raise ValidationError("Unsupported memory kind")
        fact = MemoryFact(
            fact_id=fact_id or self.ids.new("fac"),
            tenant_id=tenant_id,
            statement=statement.strip(),
            confidence=confidence,
            status=FactStatus.ACTIVE,
            sources=sources,
            created_at=utc_now(),
            kind=normalized_kind,
            tags=tuple(sorted({tag.strip() for tag in tags if tag.strip()})),
            metadata=dict(metadata or {}),
            supersedes_fact_id=supersedes_fact_id,
        )
        await self.stores.save_fact(fact)
        await self._emit(
            tenant_id,
            "memory.fact.recorded.v1",
            "memory_fact",
            fact.fact_id,
            1,
            {"source_count": len(sources)},
        )
        return fact

    async def list_facts(
        self,
        tenant_id: str,
        *,
        kind: str | None = None,
        include_suppressed: bool = False,
        limit: int = 100,
    ) -> tuple[MemoryFact, ...]:
        if limit < 1 or limit > 500:
            raise ValidationError("Memory list limit must be between 1 and 500")
        normalized_kind = kind.strip().lower() if kind else None
        facts = await self.stores.list_facts(tenant_id)
        filtered = [
            fact
            for fact in facts
            if (include_suppressed or fact.status == FactStatus.ACTIVE)
            and (normalized_kind is None or fact.kind == normalized_kind)
        ]
        return tuple(filtered[:limit])

    async def get_fact(self, tenant_id: str, fact_id: str) -> MemoryFact:
        fact = await self.stores.get_fact(tenant_id, fact_id)
        if fact is None:
            raise NotFoundError("Memory fact was not found in the current tenant")
        return fact

    async def correct_fact(
        self,
        tenant_id: str,
        fact_id: str,
        statement: str,
        *,
        confidence: float,
        sources: tuple[SourceRef, ...],
    ) -> MemoryFact:
        original = await self.get_fact(tenant_id, fact_id)
        if original.status != FactStatus.ACTIVE:
            raise ConflictError("Only an active fact can be corrected")
        corrected = await self.create_fact(
            tenant_id,
            statement,
            confidence=confidence,
            sources=sources,
            kind=original.kind,
            tags=original.tags,
            metadata=original.metadata,
            supersedes_fact_id=original.fact_id,
        )
        await self.stores.save_fact(replace(original, status=FactStatus.SUPERSEDED))
        return corrected

    async def suppress_fact(self, tenant_id: str, fact_id: str) -> MemoryFact:
        fact = await self.get_fact(tenant_id, fact_id)
        suppressed = replace(fact, status=FactStatus.SUPPRESSED)
        await self.stores.save_fact(suppressed)
        return suppressed

    async def recall(
        self, tenant_id: str, query: str, *, limit: int = 10
    ) -> tuple[RecalledFact, ...]:
        if limit < 1 or limit > 100:
            raise ValidationError("Recall limit must be between 1 and 100")
        active_facts = [
            fact
            for fact in await self.stores.list_facts(tenant_id)
            if fact.status == FactStatus.ACTIVE
        ]
        scores = bm25_scores(
            query,
            [(fact.fact_id, fact.statement, fact.confidence) for fact in active_facts],
        )
        ranked: list[RecalledFact] = []
        for fact in active_facts:
            score = scores.get(fact.fact_id)
            if score is None:
                continue
            ranked.append(
                RecalledFact(
                    fact_id=fact.fact_id,
                    statement=fact.statement,
                    confidence=fact.confidence,
                    sources=fact.sources,
                    score=score,
                )
            )
        ranked.sort(key=lambda item: (item.score, item.confidence), reverse=True)
        return tuple(ranked[:limit])

    def _require_active(self, run: AgentRun) -> None:
        if run.status in _TERMINAL_STATUSES:
            raise ConflictError(
                "The run is already terminal", details={"current_status": run.status.value}
            )
        if run.status == RunStatus.CANCEL_REQUESTED:
            raise ConflictError(
                "The run has a pending cancellation request",
                details={"current_status": run.status.value},
            )

    async def _emit(
        self,
        tenant_id: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        payload: dict[str, Any],
    ) -> None:
        await self.stores.append_domain_event(
            DomainEvent(
                event_id=self.ids.new("evt"),
                event_type=event_type,
                tenant_id=tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                occurred_at=utc_now(),
                payload=payload,
            )
        )
