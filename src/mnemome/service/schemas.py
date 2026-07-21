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
