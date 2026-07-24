from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime
from typing import Any

from .contracts import (
    AgentDescriptor,
    AgentEvent,
    AgentRun,
    Checkpoint,
    ContextBundle,
    CulturalArtifact,
    CulturalArtifactStatus,
    CulturalSnapshot,
    DomainEvent,
    FactInput,
    FactStatus,
    MemoryFact,
    OpenRunRequest,
    RecalledFact,
    ResolvedCulturalArtifact,
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
        snapshot, cultural_artifacts = await self.resolve_cultural_snapshot(
            request.tenant_id, request.cultural_scope
        )
        now = utc_now()
        run = AgentRun(
            run_id=self.ids.new("run"),
            tenant_id=request.tenant_id,
            agent_id=agent.agent_id,
            agent_descriptor_version=agent.version,
            status=RunStatus.ACTIVE,
            context_version=1,
            cultural_snapshot_id=(
                snapshot.snapshot_id
                if snapshot is not None
                else f"csp_none_{request.cultural_scope.replace('/', '_')}"
            ),
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
            cultural_artifacts=cultural_artifacts,
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
        self,
        tenant_id: str,
        query: str,
        *,
        limit: int = 10,
        mode: str = "semantic",
        kind: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        order: str | None = None,
        exclude_tags: tuple[str, ...] = (),
    ) -> tuple[RecalledFact, ...]:
        if limit < 1 or limit > 100:
            raise ValidationError("Recall limit must be between 1 and 100")
        if mode not in {"semantic", "recent", "temporal", "hybrid"}:
            raise ValidationError("Unsupported recall mode")
        resolved_order = order or (
            "created_at_desc" if mode in {"recent", "temporal"} else "relevance"
        )
        if resolved_order not in {"relevance", "created_at_desc", "created_at_asc"}:
            raise ValidationError("Unsupported recall order")
        excluded = set(exclude_tags)
        active_facts = [
            fact
            for fact in await self.stores.list_facts(tenant_id)
            if fact.status == FactStatus.ACTIVE
            and (kind is None or fact.kind == kind)
            and (created_after is None or fact.created_at >= created_after)
            and (created_before is None or fact.created_at < created_before)
            and not excluded.intersection(fact.tags)
        ]

        if mode in {"recent", "temporal"}:
            reverse = resolved_order != "created_at_asc"
            ordered = sorted(
                active_facts,
                key=lambda fact: (fact.created_at, fact.fact_id),
                reverse=reverse,
            )
            return tuple(
                RecalledFact(
                    fact_id=fact.fact_id,
                    statement=fact.statement,
                    confidence=fact.confidence,
                    sources=fact.sources,
                    score=0.0,
                    kind=fact.kind,
                    tags=fact.tags,
                    metadata=fact.metadata,
                    created_at=fact.created_at,
                    conversation_id=str(fact.metadata.get("conversation_id") or "") or None,
                    rank=rank,
                    match_reason=resolved_order,
                )
                for rank, fact in enumerate(ordered[:limit], start=1)
            )

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
                    kind=fact.kind,
                    tags=fact.tags,
                    metadata=fact.metadata,
                    created_at=fact.created_at,
                    conversation_id=str(fact.metadata.get("conversation_id") or "") or None,
                )
            )
        if mode == "hybrid":
            ranked.sort(
                key=lambda item: (
                    item.score,
                    item.confidence,
                    item.created_at
                    or datetime.min.replace(
                        tzinfo=created_after.tzinfo if created_after else None
                    ),
                    item.fact_id,
                ),
                reverse=True,
            )
            match_reason = "time_filtered_relevance"
        elif resolved_order == "created_at_desc":
            ranked.sort(
                key=lambda item: (item.created_at or datetime.min, item.fact_id),
                reverse=True,
            )
            match_reason = resolved_order
        elif resolved_order == "created_at_asc":
            ranked.sort(key=lambda item: (item.created_at or datetime.min, item.fact_id))
            match_reason = resolved_order
        else:
            ranked.sort(key=lambda item: (item.score, item.confidence), reverse=True)
            match_reason = "relevance"
        return tuple(
            replace(item, rank=rank, match_reason=match_reason)
            for rank, item in enumerate(ranked[:limit], start=1)
        )

    async def create_cultural_artifact(
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
        normalized_scope = scope.strip()
        if not tenant_id.strip() or not normalized_scope or not claim.strip():
            raise ValidationError("tenant_id, cultural scope, and claim are required")
        artifact = CulturalArtifact(
            artifact_id=self.ids.new("car"),
            tenant_id=tenant_id,
            scope=normalized_scope,
            version=1,
            claim=claim.strip(),
            conditions=tuple(item.strip() for item in conditions if item.strip()),
            restrictions=tuple(item.strip() for item in restrictions if item.strip()),
            recovery=recovery.strip() if recovery and recovery.strip() else None,
            evidence_refs=evidence_refs,
            status=CulturalArtifactStatus.DRAFT,
            metadata=dict(metadata or {}),
            supersedes_artifact_id=None,
            created_at=utc_now(),
        )
        await self.stores.save_cultural_artifact(artifact)
        await self._emit(
            tenant_id,
            "culture.artifact.created.v1",
            "cultural_artifact",
            artifact.artifact_id,
            artifact.version,
            {"scope": artifact.scope},
        )
        return artifact

    async def get_cultural_artifact(
        self, tenant_id: str, artifact_id: str
    ) -> CulturalArtifact:
        artifact = await self.stores.get_cultural_artifact(tenant_id, artifact_id)
        if artifact is None:
            raise NotFoundError("Cultural artifact was not found in the current tenant")
        return artifact

    async def list_cultural_artifacts(
        self,
        tenant_id: str,
        *,
        scope: str | None = None,
        include_withdrawn: bool = False,
    ) -> tuple[CulturalArtifact, ...]:
        artifacts = await self.stores.list_cultural_artifacts(tenant_id, scope)
        return tuple(
            artifact
            for artifact in artifacts
            if include_withdrawn or artifact.status != CulturalArtifactStatus.WITHDRAWN
        )

    async def revise_cultural_artifact(
        self,
        tenant_id: str,
        artifact_id: str,
        *,
        claim: str,
        conditions: tuple[str, ...] = (),
        restrictions: tuple[str, ...] = (),
        recovery: str | None = None,
        evidence_refs: tuple[SourceRef, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> CulturalArtifact:
        original = await self.get_cultural_artifact(tenant_id, artifact_id)
        if original.status == CulturalArtifactStatus.WITHDRAWN:
            raise ConflictError("A withdrawn cultural artifact cannot be revised")
        if not claim.strip():
            raise ValidationError("Cultural claim is required")
        revised = CulturalArtifact(
            artifact_id=self.ids.new("car"),
            tenant_id=tenant_id,
            scope=original.scope,
            version=original.version + 1,
            claim=claim.strip(),
            conditions=tuple(item.strip() for item in conditions if item.strip()),
            restrictions=tuple(item.strip() for item in restrictions if item.strip()),
            recovery=recovery.strip() if recovery and recovery.strip() else None,
            evidence_refs=evidence_refs,
            status=CulturalArtifactStatus.DRAFT,
            metadata=dict(metadata or original.metadata),
            supersedes_artifact_id=original.artifact_id,
            created_at=utc_now(),
        )
        await self.stores.save_cultural_artifact(revised)
        return revised

    async def withdraw_cultural_artifact(
        self, tenant_id: str, artifact_id: str
    ) -> CulturalArtifact:
        artifact = await self.get_cultural_artifact(tenant_id, artifact_id)
        if artifact.status == CulturalArtifactStatus.WITHDRAWN:
            return artifact
        withdrawn = replace(artifact, status=CulturalArtifactStatus.WITHDRAWN)
        await self.stores.save_cultural_artifact(withdrawn)
        await self._emit(
            tenant_id,
            "culture.artifact.withdrawn.v1",
            "cultural_artifact",
            artifact.artifact_id,
            artifact.version,
            {"scope": artifact.scope},
        )
        return withdrawn

    async def publish_cultural_snapshot(
        self,
        tenant_id: str,
        scope: str,
        *,
        artifact_ids: tuple[str, ...] | None = None,
        policy_version: str = "culture-policy-v1",
    ) -> CulturalSnapshot:
        normalized_scope = scope.strip()
        if not normalized_scope:
            raise ValidationError("Cultural scope is required")
        async with self._mutation_lock:
            artifacts = await self.stores.list_cultural_artifacts(tenant_id, normalized_scope)
            by_id = {artifact.artifact_id: artifact for artifact in artifacts}
            if artifact_ids is not None:
                unknown = [artifact_id for artifact_id in artifact_ids if artifact_id not in by_id]
                if unknown:
                    raise NotFoundError(
                        "A cultural artifact was not found in the requested scope",
                        details={"artifact_ids": unknown},
                    )
                selected = [by_id[artifact_id] for artifact_id in artifact_ids]
            else:
                superseded = {
                    artifact.supersedes_artifact_id
                    for artifact in artifacts
                    if artifact.supersedes_artifact_id
                    and artifact.status != CulturalArtifactStatus.WITHDRAWN
                }
                selected = [
                    artifact
                    for artifact in artifacts
                    if artifact.status != CulturalArtifactStatus.WITHDRAWN
                    and artifact.artifact_id not in superseded
                ]
            if any(artifact.status == CulturalArtifactStatus.WITHDRAWN for artifact in selected):
                raise ConflictError("Withdrawn cultural artifacts cannot be published")
            selected.sort(key=lambda artifact: artifact.artifact_id)
            for artifact in selected:
                if artifact.status == CulturalArtifactStatus.DRAFT:
                    published = replace(artifact, status=CulturalArtifactStatus.PUBLISHED)
                    await self.stores.save_cultural_artifact(published)
            previous = await self.stores.get_active_cultural_snapshot(
                tenant_id, normalized_scope
            )
            canonical = [
                {
                    "id": artifact.artifact_id,
                    "version": artifact.version,
                    "claim": artifact.claim,
                    "conditions": artifact.conditions,
                    "restrictions": artifact.restrictions,
                    "recovery": artifact.recovery,
                }
                for artifact in selected
            ]
            digest = hashlib.sha256(
                json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            snapshot = CulturalSnapshot(
                snapshot_id=self.ids.new("csp"),
                tenant_id=tenant_id,
                scope=normalized_scope,
                version=(previous.version + 1 if previous else 1),
                artifact_ids=tuple(artifact.artifact_id for artifact in selected),
                content_digest=digest,
                policy_version=policy_version.strip() or "culture-policy-v1",
                previous_snapshot_id=previous.snapshot_id if previous else None,
                created_at=utc_now(),
            )
            await self.stores.save_cultural_snapshot(snapshot)
            await self.stores.activate_cultural_snapshot(snapshot)
            await self._emit(
                tenant_id,
                "culture.snapshot.published.v1",
                "cultural_snapshot",
                snapshot.snapshot_id,
                snapshot.version,
                {"scope": snapshot.scope, "artifact_count": len(snapshot.artifact_ids)},
            )
            return snapshot

    async def get_cultural_snapshot(
        self, tenant_id: str, snapshot_id: str
    ) -> CulturalSnapshot:
        snapshot = await self.stores.get_cultural_snapshot(tenant_id, snapshot_id)
        if snapshot is None:
            raise NotFoundError("Cultural snapshot was not found in the current tenant")
        return snapshot

    async def resolve_cultural_snapshot(
        self, tenant_id: str, scope: str = "default"
    ) -> tuple[CulturalSnapshot | None, tuple[ResolvedCulturalArtifact, ...]]:
        snapshot = await self.stores.get_active_cultural_snapshot(tenant_id, scope)
        if snapshot is None:
            return None, ()
        resolved: list[ResolvedCulturalArtifact] = []
        for artifact_id in snapshot.artifact_ids:
            artifact = await self.stores.get_cultural_artifact(tenant_id, artifact_id)
            if artifact is None:
                raise ConflictError(
                    "A published cultural snapshot references a missing artifact",
                    details={"snapshot_id": snapshot.snapshot_id, "artifact_id": artifact_id},
                )
            resolved.append(
                ResolvedCulturalArtifact(
                    artifact_id=artifact.artifact_id,
                    version=artifact.version,
                    claim=artifact.claim,
                    conditions=artifact.conditions,
                    restrictions=artifact.restrictions,
                    recovery=artifact.recovery,
                    evidence_refs=artifact.evidence_refs,
                )
            )
        return snapshot, tuple(resolved)

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
