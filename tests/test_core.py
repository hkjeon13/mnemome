from __future__ import annotations

import pytest

from mnemome import FactInput, Mnemome, OpenRunRequest, RunStatus, SourceRef
from mnemome.errors import ConflictError, NotFoundError
from mnemome.retrieval import MecabNltkTokenizer


@pytest.mark.asyncio
async def test_embedded_facade_run_and_provenance_recall() -> None:
    memory = Mnemome.in_memory()
    await memory.initialize()
    agent = await memory.register_agent("tenant-a", "incident-agent", ("memory.read",))

    environment = await memory.agent_environment.open_run(
        OpenRunRequest(
            tenant_id="tenant-a",
            agent_id=agent.agent_id,
            retrieval_text="cache incident",
        )
    )
    initial = await environment.get_context()
    assert initial.recalled_facts == ()
    first_event = await environment.record_event(
        "observation", {"message": "cache exhausted"}, caller_event_id="host-event-1"
    )
    duplicate = await environment.record_event(
        "observation", {"message": "ignored duplicate"}, caller_event_id="host-event-1"
    )
    assert duplicate == first_event

    await environment.checkpoint("object://checkpoints/1")
    completed = await environment.complete(
        {"status": "resolved"},
        response_ref="host-response-1",
        facts=(
            FactInput(
                statement="Cache exhaustion caused the incident",
                confidence=0.9,
                sources=(SourceRef("agent_event", first_event.event_id),),
            ),
        ),
    )
    assert completed.status == RunStatus.COMPLETED

    recalled = await memory.application.recall("tenant-a", "cache incident")
    assert len(recalled) == 1
    assert recalled[0].sources[0].source_id == first_event.event_id

    with pytest.raises(ConflictError):
        await environment.record_event("observation", {})


@pytest.mark.asyncio
async def test_tenant_scope_is_mandatory() -> None:
    memory = Mnemome.in_memory()
    await memory.initialize()
    agent = await memory.register_agent("tenant-a", "agent")

    with pytest.raises(NotFoundError):
        await memory.agent_environment.open_run(
            OpenRunRequest(tenant_id="tenant-b", agent_id=agent.agent_id)
        )

    fact = await memory.application.create_fact(
        "tenant-a",
        "private fact",
        sources=(SourceRef("external", "source-1"),),
    )
    with pytest.raises(NotFoundError):
        await memory.application.get_fact("tenant-b", fact.fact_id)
    assert await memory.application.recall("tenant-b", "private") == ()


@pytest.mark.asyncio
async def test_correction_preserves_original_and_supersedes_it() -> None:
    memory = Mnemome.in_memory()
    await memory.initialize()
    original = await memory.application.create_fact(
        "tenant-a",
        "The timeout is 10 seconds",
        sources=(SourceRef("run", "run-1"),),
    )
    corrected = await memory.application.correct_fact(
        "tenant-a",
        original.fact_id,
        "The timeout is 30 seconds",
        confidence=1.0,
        sources=(SourceRef("run", "run-2"),),
    )
    stored_original = await memory.application.get_fact("tenant-a", original.fact_id)
    assert stored_original.status.value == "SUPERSEDED"
    assert corrected.supersedes_fact_id == original.fact_id
    assert [item.fact_id for item in await memory.application.recall("tenant-a", "timeout")] == [
        corrected.fact_id
    ]


@pytest.mark.asyncio
async def test_memory_kind_tags_and_metadata_are_preserved() -> None:
    memory = Mnemome.in_memory()
    await memory.initialize()
    fact = await memory.application.create_fact(
        "tenant-a",
        "한국어 답변을 선호합니다",
        sources=(SourceRef("profile", "user-1"),),
        kind="preference",
        tags=("language", "korean"),
        metadata={"priority": "high"},
    )

    listed = await memory.application.list_facts("tenant-a", kind="preference")
    assert listed == (fact,)
    assert listed[0].tags == ("korean", "language")
    assert listed[0].metadata == {"priority": "high"}


@pytest.mark.asyncio
async def test_bm25_recall_normalizes_korean_particles() -> None:
    memory = Mnemome.in_memory()
    await memory.initialize()
    article = await memory.application.create_fact(
        "tenant-a",
        (
            "엔비디아의 독주에 제동을 건 AMD와 삼성전자, SK하이닉스가 "
            "고대역폭메모리의 큰손으로 부상했다는 소식입니다. "
            "이들은 엔비디아와 경쟁하고 있습니다."
        ),
        sources=(SourceRef("test", "nvidia-article"),),
        kind="conversation",
    )
    await memory.application.create_fact(
        "tenant-a",
        "Mnemome 데모는 Docker Compose로 배포됩니다.",
        sources=(SourceRef("test", "deployment"),),
        kind="fact",
    )

    recalled = await memory.application.recall(
        "tenant-a",
        "이전에 엔비디아 관련 기사에 대해서 답변한 내용 기억해?",
    )

    assert recalled
    assert recalled[0].fact_id == article.fact_id
    assert recalled[0].score > 0


def test_tokenizer_falls_back_when_mecab_dictionary_is_missing(monkeypatch) -> None:
    import konlpy.tag
    import pecab

    class MissingDictionaryMecab:
        def __init__(self) -> None:
            raise Exception("dictionary is missing")

    class FallbackPecab:
        pass

    monkeypatch.setattr(konlpy.tag, "Mecab", MissingDictionaryMecab)
    monkeypatch.setattr(pecab, "PeCab", FallbackPecab)

    assert isinstance(MecabNltkTokenizer._build_mecab(), FallbackPecab)
