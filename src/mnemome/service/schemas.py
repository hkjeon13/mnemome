from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RegisterAgentBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    capabilities: list[str] = Field(default_factory=list, max_length=100)


class ContextRequestBody(BaseModel):
    query_ref: str | None = Field(default=None, max_length=500)
    retrieval_text: str = Field(default="", max_length=20_000)
    store_query_content: bool = False


class MemoryPolicyBody(BaseModel):
    recall: bool = True
    write_episode: bool = True
    cultural_scope: str = Field(default="default", max_length=200)


class OpenRunBody(BaseModel):
    agent_id: str
    agent_descriptor_version: int | None = None
    context_request: ContextRequestBody = Field(default_factory=ContextRequestBody)
    workspace_id: str | None = None
    memory_policy: MemoryPolicyBody = Field(default_factory=MemoryPolicyBody)


class AgentEventBody(BaseModel):
    event_type: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)
    event_id: str | None = Field(default=None, max_length=200)


class CheckpointBody(BaseModel):
    checkpoint_ref: str = Field(min_length=1, max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceRefBody(BaseModel):
    source_type: str = Field(min_length=1, max_length=100)
    source_id: str = Field(min_length=1, max_length=500)
    span: str | None = Field(default=None, max_length=1000)


class FactInputBody(BaseModel):
    statement: str = Field(min_length=1, max_length=20_000)
    confidence: float = Field(default=1.0, ge=0, le=1)
    sources: list[SourceRefBody] = Field(default_factory=list)


class CreateFactBody(FactInputBody):
    kind: str = Field(default="fact", pattern="^(fact|preference|episode|conversation)$")
    tags: list[str] = Field(default_factory=list, max_length=30)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompleteRunBody(BaseModel):
    outcome: dict[str, Any]
    response_ref: str | None = Field(default=None, max_length=2000)
    facts: list[FactInputBody] = Field(default_factory=list, max_length=100)


class FailRunBody(BaseModel):
    failure: dict[str, Any]


class CorrectFactBody(BaseModel):
    statement: str = Field(min_length=1, max_length=20_000)
    confidence: float = Field(default=1.0, ge=0, le=1)
    sources: list[SourceRefBody] = Field(min_length=1)


class CulturalArtifactBody(BaseModel):
    scope: str = Field(default="default", min_length=1, max_length=200)
    claim: str = Field(min_length=1, max_length=20_000)
    conditions: list[str] = Field(default_factory=list, max_length=50)
    restrictions: list[str] = Field(default_factory=list, max_length=50)
    recovery: str | None = Field(default=None, max_length=10_000)
    evidence_refs: list[SourceRefBody] = Field(default_factory=list, max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviseCulturalArtifactBody(BaseModel):
    claim: str = Field(min_length=1, max_length=20_000)
    conditions: list[str] = Field(default_factory=list, max_length=50)
    restrictions: list[str] = Field(default_factory=list, max_length=50)
    recovery: str | None = Field(default=None, max_length=10_000)
    evidence_refs: list[SourceRefBody] = Field(default_factory=list, max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PublishCulturalSnapshotBody(BaseModel):
    scope: str = Field(default="default", min_length=1, max_length=200)
    artifact_ids: list[str] | None = Field(default=None, max_length=500)
    policy_version: str = Field(default="culture-policy-v1", min_length=1, max_length=200)
