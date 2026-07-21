from __future__ import annotations

import asyncio

from ..contracts import (
    AgentDescriptor,
    AgentEvent,
    AgentRun,
    Checkpoint,
    CulturalArtifact,
    CulturalSnapshot,
    DomainEvent,
    MemoryFact,
)


class InMemoryStores:
    """Reference adapter for embedding, tests, and adapter conformance."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._agents: dict[tuple[str, str], AgentDescriptor] = {}
        self._runs: dict[tuple[str, str], AgentRun] = {}
        self._events: dict[tuple[str, str], list[AgentEvent]] = {}
        self._checkpoints: dict[tuple[str, str], Checkpoint] = {}
        self._facts: dict[tuple[str, str], MemoryFact] = {}
        self._cultural_artifacts: dict[tuple[str, str], CulturalArtifact] = {}
        self._cultural_snapshots: dict[tuple[str, str], CulturalSnapshot] = {}
        self._active_cultural_snapshots: dict[tuple[str, str], str] = {}
        self.domain_events: list[DomainEvent] = []

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def save_agent(self, agent: AgentDescriptor) -> None:
        async with self._lock:
            self._agents[(agent.tenant_id, agent.agent_id)] = agent

    async def get_agent(self, tenant_id: str, agent_id: str) -> AgentDescriptor | None:
        return self._agents.get((tenant_id, agent_id))

    async def save_run(self, run: AgentRun) -> None:
        async with self._lock:
            self._runs[(run.tenant_id, run.run_id)] = run

    async def get_run(self, tenant_id: str, run_id: str) -> AgentRun | None:
        return self._runs.get((tenant_id, run_id))

    async def append_agent_event(self, event: AgentEvent) -> None:
        async with self._lock:
            bucket = self._events.setdefault((event.tenant_id, event.run_id), [])
            if any(existing.event_id == event.event_id for existing in bucket):
                return
            bucket.append(event)

    async def list_agent_events(self, tenant_id: str, run_id: str) -> list[AgentEvent]:
        return list(self._events.get((tenant_id, run_id), ()))

    async def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        async with self._lock:
            self._checkpoints[(checkpoint.tenant_id, checkpoint.run_id)] = checkpoint

    async def save_fact(self, fact: MemoryFact) -> None:
        async with self._lock:
            self._facts[(fact.tenant_id, fact.fact_id)] = fact

    async def get_fact(self, tenant_id: str, fact_id: str) -> MemoryFact | None:
        return self._facts.get((tenant_id, fact_id))

    async def list_facts(self, tenant_id: str) -> list[MemoryFact]:
        return [fact for (owner, _), fact in self._facts.items() if owner == tenant_id]

    async def save_cultural_artifact(self, artifact: CulturalArtifact) -> None:
        async with self._lock:
            self._cultural_artifacts[(artifact.tenant_id, artifact.artifact_id)] = artifact

    async def get_cultural_artifact(
        self, tenant_id: str, artifact_id: str
    ) -> CulturalArtifact | None:
        return self._cultural_artifacts.get((tenant_id, artifact_id))

    async def list_cultural_artifacts(
        self, tenant_id: str, scope: str | None = None
    ) -> list[CulturalArtifact]:
        return [
            artifact
            for (owner, _), artifact in self._cultural_artifacts.items()
            if owner == tenant_id and (scope is None or artifact.scope == scope)
        ]

    async def save_cultural_snapshot(self, snapshot: CulturalSnapshot) -> None:
        async with self._lock:
            self._cultural_snapshots[(snapshot.tenant_id, snapshot.snapshot_id)] = snapshot

    async def get_cultural_snapshot(
        self, tenant_id: str, snapshot_id: str
    ) -> CulturalSnapshot | None:
        return self._cultural_snapshots.get((tenant_id, snapshot_id))

    async def get_active_cultural_snapshot(
        self, tenant_id: str, scope: str
    ) -> CulturalSnapshot | None:
        snapshot_id = self._active_cultural_snapshots.get((tenant_id, scope))
        return self._cultural_snapshots.get((tenant_id, snapshot_id)) if snapshot_id else None

    async def activate_cultural_snapshot(self, snapshot: CulturalSnapshot) -> None:
        async with self._lock:
            self._cultural_snapshots[(snapshot.tenant_id, snapshot.snapshot_id)] = snapshot
            self._active_cultural_snapshots[
                (snapshot.tenant_id, snapshot.scope)
            ] = snapshot.snapshot_id

    async def append_domain_event(self, event: DomainEvent) -> None:
        async with self._lock:
            if not any(existing.event_id == event.event_id for existing in self.domain_events):
                self.domain_events.append(event)
