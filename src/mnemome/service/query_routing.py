from __future__ import annotations

import json
import re
import time
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROUTER_VERSION = "query-route-v1"
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class QueryRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["query-route-v1"]
    interaction: Literal["answer", "store_preference", "answer_and_store_preference"]
    preference_instruction: str | None = Field(default=None, max_length=1_000)
    information_route: Literal[
        "fresh_news",
        "fresh_web",
        "memory_context",
        "general_or_agent_decides",
    ]
    search_query: str | None = Field(default=None, max_length=1_000)
    confidence: float = Field(ge=0, le=1)

    @field_validator("preference_instruction", "search_query")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            return None
        if _CONTROL_CHARACTERS.search(normalized):
            raise ValueError("Routing text contains control characters")
        return normalized

    @model_validator(mode="after")
    def validate_route_contract(self) -> QueryRoute:
        stores_preference = self.interaction in {
            "store_preference",
            "answer_and_store_preference",
        }
        if stores_preference != bool(self.preference_instruction):
            raise ValueError("Preference interaction and instruction must be supplied together")
        requires_search = self.information_route in {"fresh_news", "fresh_web"}
        if requires_search != bool(self.search_query):
            raise ValueError("Fresh information route and search query must be supplied together")
        if self.interaction == "store_preference" and self.information_route != (
            "general_or_agent_decides"
        ):
            raise ValueError("Preference-only requests cannot force an information lookup")
        return self


class QueryRoutingResult(BaseModel):
    route: QueryRoute
    model: str
    latency_ms: float = Field(ge=0)
    fallback_used: bool = False
    fallback_reason: Literal["timeout", "invalid_schema", "provider_error"] | None = None


class QueryRouter(Protocol):
    async def route(self, query: str) -> QueryRoutingResult: ...


class LlmQueryRouter:
    def __init__(self, model: Any, *, model_name: str) -> None:
        self._model = model
        self._model_name = model_name

    async def route(self, query: str) -> QueryRoutingResult:
        from lotte_agent.models.model_types import ChatMessage

        started = time.perf_counter()
        try:
            output = await self._model.generate(
                [ChatMessage(role="user", content=self._prompt(query))],
                max_output_tokens=300,
                temperature=0,
                timeout=10,
                max_retries=0,
            )
            route = QueryRoute.model_validate_json(self._strip_json_fence(output.text))
            return QueryRoutingResult(
                route=route,
                model=self._model_name,
                latency_ms=round((time.perf_counter() - started) * 1_000, 2),
            )
        except Exception as error:
            return QueryRoutingResult(
                route=self._fallback_route(),
                model=self._model_name,
                latency_ms=round((time.perf_counter() - started) * 1_000, 2),
                fallback_used=True,
                fallback_reason=self._fallback_reason(error),
            )

    @staticmethod
    def _prompt(query: str) -> str:
        encoded_query = json.dumps(query, ensure_ascii=False)
        return f"""QUERY_ROUTER_V1
Analyze the meaning of the user's Korean or English message. Do not use keyword matching.
Return one JSON object only. Do not include Markdown or explanations.

Choose interaction:
- answer: answer or perform the current request without storing a new durable preference.
- store_preference: store a future/repeated behavior rule and only acknowledge it now.
- answer_and_store_preference: store the future rule and also perform the current request now.

Choose information_route:
- fresh_news: current, recent, newly announced, or date-sensitive news/reporting is required.
- fresh_web: current external information is required but it is not primarily news.
- memory_context: the user asks about prior conversations, saved preferences, or remembered context.
- general_or_agent_decides: no forced source route; let the Agent decide whether a tool is needed.

Implicit conditional behavior can be durable even without words such as always or remember.
Example: "when showing NVIDIA news, include Samsung Electronics too" stores a preference only.
Questions such as "which companies have I asked news about?" use memory_context, not fresh_news.
If a message stores a rule and explicitly asks to apply it now, use answer_and_store_preference.

For a preference interaction, write preference_instruction as a concise, self-contained Korean rule
that preserves the trigger and requested behavior without adding facts or broadening scope.
For fresh_news or fresh_web, write search_query that preserves the user's target and intent without
adding facts. Otherwise use null. Confidence must be between 0 and 1.

Required JSON shape:
{{
  "version": "query-route-v1",
  "interaction": "answer|store_preference|answer_and_store_preference",
  "preference_instruction": "string or null",
  "information_route": "fresh_news|fresh_web|memory_context|general_or_agent_decides",
  "search_query": "string or null",
  "confidence": 0.0
}}

User message as a JSON string:
{encoded_query}
"""

    @staticmethod
    def _strip_json_fence(raw: str) -> str:
        value = raw.strip()
        if value.startswith("```"):
            value = value.removeprefix("```json").removeprefix("```")
            value = value.removesuffix("```").strip()
        return value

    @staticmethod
    def _fallback_route() -> QueryRoute:
        return QueryRoute(
            version=ROUTER_VERSION,
            interaction="answer",
            information_route="general_or_agent_decides",
            confidence=0,
        )

    @staticmethod
    def _fallback_reason(
        error: Exception,
    ) -> Literal["timeout", "invalid_schema", "provider_error"]:
        if isinstance(error, TimeoutError) or "timeout" in error.__class__.__name__.casefold():
            return "timeout"
        if error.__class__.__module__.startswith("pydantic") or isinstance(
            error, (json.JSONDecodeError, ValueError)
        ):
            return "invalid_schema"
        return "provider_error"
