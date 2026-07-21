from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FactStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    SUPPRESSED = "SUPPRESSED"


@dataclass(frozen=True, slots=True)
class AgentDescriptor:
    agent_id: str
    tenant_id: str
    name: str
    version: int
    capabilities: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SourceRef:
    source_type: str
    source_id: str
    span: str | None = None


@dataclass(frozen=True, slots=True)
class RecalledFact:
    fact_id: str
    statement: str
    confidence: float
    sources: tuple[SourceRef, ...]
    score: float


@dataclass(frozen=True, slots=True)
class ContextBundle:
    run_id: str
    context_version: int
    cultural_snapshot_id: str
    recalled_facts: tuple[RecalledFact, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OpenRunRequest:
    tenant_id: str
    agent_id: str
    agent_descriptor_version: int | None = None
    retrieval_text: str = ""
    workspace_id: str | None = None
    recall: bool = True
    write_episode: bool = True
    cultural_scope: str = "default"
    query_ref: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRun:
    run_id: str
    tenant_id: str
    agent_id: str
    agent_descriptor_version: int
    status: RunStatus
    context_version: int
    cultural_snapshot_id: str
    workspace_id: str | None
    query_ref: str | None
    write_episode: bool
    created_at: datetime
    updated_at: datetime
    outcome: dict[str, Any] | None = None
    response_ref: str | None = None
    failure: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AgentEvent:
    event_id: str
    run_id: str
    tenant_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class Checkpoint:
    checkpoint_id: str
    run_id: str
    tenant_id: str
    context_version: int
    checkpoint_ref: str
    metadata: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FactInput:
    statement: str
    confidence: float = 1.0
    sources: tuple[SourceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryFact:
    fact_id: str
    tenant_id: str
    statement: str
    confidence: float
    status: FactStatus
    sources: tuple[SourceRef, ...]
    created_at: datetime
    kind: str = "fact"
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    supersedes_fact_id: str | None = None


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_id: str
    event_type: str
    tenant_id: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    occurred_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
