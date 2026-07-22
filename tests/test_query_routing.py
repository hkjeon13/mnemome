from __future__ import annotations

import json

import pytest
from lotte_agent.models.model_types import ModelOutput
from pydantic import ValidationError

from mnemome.service.query_routing import LlmQueryRouter, QueryRoute


class FakeRoutingModel:
    def __init__(self, result: str | Exception) -> None:
        self.result = result
        self.calls = 0

    async def generate(self, messages, *args, **kwargs):
        del messages, args, kwargs
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return ModelOutput(model="router-test", text=self.result, finish_reason="stop")


def route_payload(**overrides) -> str:
    payload = {
        "version": "query-route-v1",
        "interaction": "answer",
        "preference_instruction": None,
        "information_route": "general_or_agent_decides",
        "search_query": None,
        "confidence": 0.9,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_llm_query_router_parses_fresh_news_route() -> None:
    model = FakeRoutingModel(
        route_payload(
            information_route="fresh_news",
            search_query="엔비디아 뉴스",
            confidence=0.99,
        )
    )

    result = await LlmQueryRouter(model, model_name="router-test").route("엔비디아 새 소식")

    assert result.fallback_used is False
    assert result.route.information_route == "fresh_news"
    assert result.route.search_query == "엔비디아 뉴스"
    assert model.calls == 1


@pytest.mark.asyncio
async def test_llm_query_router_parses_conditional_preference_without_keywords() -> None:
    model = FakeRoutingModel(
        route_payload(
            interaction="store_preference",
            preference_instruction=(
                "엔비디아 뉴스를 요청하면 SK하이닉스와 삼성전자 관련 뉴스도 함께 포함한다."
            ),
        )
    )

    result = await LlmQueryRouter(model, model_name="router-test").route(
        "엔비디아 뉴스 나타낼 때 하이닉스, 삼성전자도 같이"
    )

    assert result.route.interaction == "store_preference"
    assert result.route.information_route == "general_or_agent_decides"
    assert result.route.preference_instruction


@pytest.mark.asyncio
async def test_llm_query_router_parses_combined_preference_and_news_request() -> None:
    model = FakeRoutingModel(
        route_payload(
            interaction="answer_and_store_preference",
            preference_instruction="엔비디아 뉴스에는 삼성전자 관련 뉴스도 함께 포함한다.",
            information_route="fresh_news",
            search_query="엔비디아 삼성전자 뉴스",
            confidence=0.97,
        )
    )

    result = await LlmQueryRouter(model, model_name="router-test").route(
        "앞으로 삼성전자도 포함하고 지금 엔비디아 뉴스 보여줘"
    )

    assert result.route.interaction == "answer_and_store_preference"
    assert result.route.information_route == "fresh_news"
    assert result.route.search_query == "엔비디아 삼성전자 뉴스"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_result", "fallback_reason"),
    [
        ("not-json", "invalid_schema"),
        (route_payload(extra_tool="search_retrieve"), "invalid_schema"),
        (TimeoutError("slow router"), "timeout"),
        (RuntimeError("provider unavailable"), "provider_error"),
    ],
)
async def test_llm_query_router_fails_back_without_keyword_routing(
    model_result: str | Exception, fallback_reason: str
) -> None:
    result = await LlmQueryRouter(
        FakeRoutingModel(model_result), model_name="router-test"
    ).route("엔비디아 뉴스")

    assert result.fallback_used is True
    assert result.fallback_reason == fallback_reason
    assert result.route.interaction == "answer"
    assert result.route.information_route == "general_or_agent_decides"
    assert result.route.preference_instruction is None
    assert result.route.search_query is None


def test_query_route_rejects_inconsistent_side_effect_contracts() -> None:
    with pytest.raises(ValidationError):
        QueryRoute.model_validate_json(
            route_payload(interaction="store_preference", preference_instruction=None)
        )
    with pytest.raises(ValidationError):
        QueryRoute.model_validate_json(
            route_payload(information_route="fresh_news", search_query=None)
        )
    with pytest.raises(ValidationError):
        QueryRoute.model_validate_json(
            route_payload(
                interaction="store_preference",
                preference_instruction="앞으로 한국어로 답한다.",
                information_route="fresh_news",
                search_query="엔비디아 뉴스",
            )
        )
