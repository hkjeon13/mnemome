from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import json
import logging
import os
import secrets
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..contracts import SourceRef

logger = logging.getLogger("mnemome.service.demo.imports")

HF_DATASET_VIEWER = "https://datasets-server.huggingface.co"
MAX_LOCAL_ROWS = 2_000
MAX_HF_ROWS = 1_000
MAX_PREPARATIONS = 100
MAX_IMPORT_JOBS = 100
MAX_IMPORT_MEMORIES = 40
MAX_DEMO_MEMORIES = 500
DEFAULT_SAMPLE_SIZE = 5


class DemoImportSourceBody(BaseModel):
    type: Literal["local", "huggingface"]
    file_name: str | None = Field(default=None, max_length=300)
    repo_id: str | None = Field(default=None, max_length=300)
    config: str | None = Field(default="default", max_length=200)
    split: str | None = Field(default="train", max_length=200)
    token: str | None = Field(default=None, max_length=1_000)


class DemoImportPrepareBody(BaseModel):
    source: DemoImportSourceBody
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_LOCAL_ROWS)
    instructions: str = Field(default="", max_length=2_000)
    sample_size: int = Field(default=DEFAULT_SAMPLE_SIZE, ge=1, le=20)


class DemoImportPreviewBody(BaseModel):
    code: str = Field(min_length=1, max_length=8_000)
    sample_size: int = Field(default=DEFAULT_SAMPLE_SIZE, ge=1, le=20)


class DemoImportProcessBody(BaseModel):
    code: str = Field(min_length=1, max_length=8_000)


@dataclass(slots=True)
class _Preparation:
    preparation_id: str
    tenant_id: str
    source: DemoImportSourceBody
    rows: list[dict[str, Any]]
    features: list[dict[str, Any]]
    total_rows: int
    profile: dict[str, Any]
    code: str
    generator: str


@dataclass(slots=True)
class _ImportJob:
    job_id: str
    tenant_id: str
    preparation_id: str
    source_label: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: str = "QUEUED"
    stage: str = "대기 중"
    resumable_stage: str = "대기 중"
    progress: int = 0
    completed_sessions: int = 0
    created_memories: int = 0
    created_cultural_memories: int = 0
    total_sessions: int = 0
    memory_counts: dict[str, int] = field(
        default_factory=lambda: {
            "conversation": 0,
            "fact": 0,
            "preference": 0,
            "episode": 0,
            "culture": 0,
        }
    )
    result: dict[str, Any] | None = None
    error: str | None = None
    resume_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def __post_init__(self) -> None:
        self.resume_event.set()

    def payload(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "preparation_id": self.preparation_id,
            "source_label": self.source_label,
            "created_at": self.created_at,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "completed_sessions": self.completed_sessions,
            "created_memories": self.created_memories,
            "created_cultural_memories": self.created_cultural_memories,
            "total_sessions": self.total_sessions,
            "memory_counts": dict(self.memory_counts),
            "result": self.result,
            "error": self.error,
        }


def _json_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _field_name(keys: list[str], exact: tuple[str, ...], contains: tuple[str, ...]) -> str | None:
    folded = {key.casefold(): key for key in keys}
    for name in exact:
        if name.casefold() in folded:
            return folded[name.casefold()]
    return next(
        (key for key in keys if any(fragment in key.casefold() for fragment in contains)),
        None,
    )


def _sample_nested_rows(rows: list[dict[str, Any]], field: str | None) -> list[dict[str, Any]]:
    if not field:
        return []
    for row in rows:
        candidate = row.get(field)
        if isinstance(candidate, list):
            nested = [item for item in candidate if isinstance(item, dict)]
            if nested:
                return nested
    return []


def _profile_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = sorted({str(key) for row in rows for key in row})
    session_field = _field_name(
        keys,
        ("sessionId", "session_id", "conversation_id", "dialogue_id", "thread_id"),
        ("session", "conversation_id", "dialog", "thread"),
    )
    list_fields = [key for key in keys if any(isinstance(row.get(key), list) for row in rows)]
    conversation_field = _field_name(
        list_fields,
        ("conversation", "messages", "turns", "dialogue"),
        ("conversation", "message", "turn", "dialog"),
    )
    nested = _sample_nested_rows(rows, conversation_field)
    turn_keys = sorted({str(key) for turn in nested for key in turn})
    scalar_keys = turn_keys or keys
    content_field = _field_name(
        scalar_keys,
        ("content", "text", "message", "utterance"),
        ("content", "text", "message", "utterance"),
    )
    role_field = _field_name(
        scalar_keys,
        ("role", "speaker", "author", "from"),
        ("role", "speaker", "author"),
    )
    timestamp_field = _field_name(
        scalar_keys,
        ("timestamp", "created_at", "time", "datetime"),
        ("timestamp", "created", "datetime", "time"),
    )
    order_field = _field_name(
        scalar_keys,
        ("turn_index", "turn_id", "sequence", "order", "index"),
        ("turn_index", "sequence", "order"),
    )

    identifiers = [str(row.get(session_field)) for row in rows] if session_field else []
    duplicate_count = len(identifiers) - len(set(identifiers)) if identifiers else 0
    duplicate_rate = duplicate_count / len(identifiers) if identifiers else 0.0
    reused_id_suspected = False
    if session_field and order_field and not conversation_field:
        orders: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            session_id = str(row.get(session_field))
            order = str(row.get(order_field))
            if order in orders[session_id]:
                reused_id_suspected = True
                break
            orders[session_id].add(order)

    if reused_id_suspected:
        layout = "REUSED_SESSION_ID_SUSPECTED"
        confidence = 0.62
    elif conversation_field and duplicate_count:
        layout = "SESSION_FRAGMENT_PER_ROW"
        confidence = 0.9
    elif conversation_field:
        layout = "SESSION_PER_ROW"
        confidence = 0.94
    elif session_field and content_field and duplicate_count:
        layout = "TURN_PER_ROW"
        confidence = 0.88
    else:
        layout = "MIXED_OR_AMBIGUOUS"
        confidence = 0.45

    warnings: list[str] = []
    if not session_field:
        warnings.append("session 식별자 후보를 찾지 못해 row index를 임시 ID로 사용합니다.")
    if layout == "TURN_PER_ROW" and not order_field:
        warnings.append("명시적인 turn order가 없어 source row 순서를 사용합니다.")
    if reused_id_suspected:
        warnings.append(
            "같은 session ID에서 turn order가 반복됩니다. "
            "ID 재사용 여부를 확인하기 전에는 Processing할 수 없습니다."
        )

    return {
        "layout": layout,
        "confidence": confidence,
        "session_field": session_field,
        "conversation_field": conversation_field,
        "content_field": content_field,
        "role_field": role_field,
        "timestamp_field": timestamp_field,
        "order_field": order_field,
        "duplicate_rate": round(duplicate_rate, 3),
        "sampled_rows": len(rows),
        "fields": keys,
        "warnings": warnings,
    }


def _row_access(field: str | None, fallback: str) -> str:
    return f"row[{json.dumps(field)}]" if field else fallback


def _heuristic_code(profile: dict[str, Any]) -> str:
    session_field = profile.get("session_field")
    if profile.get("layout") == "REUSED_SESSION_ID_SUSPECTED" and session_field:
        # Keep suspicious identifiers separate in preview instead of merging them silently.
        session = f'str(row[{json.dumps(session_field)}]) + ":row-" + str(ctx["row_index"])'
    else:
        session = _row_access(session_field, 'str(ctx["row_index"])')
    conversation = profile.get("conversation_field")
    if conversation:
        nested_rows = profile
        content = nested_rows.get("content_field") or "content"
        role = nested_rows.get("role_field") or "role"
        timestamp = nested_rows.get("timestamp_field") or "timestamp"
        order = nested_rows.get("order_field")
        arguments = [
            f"row[{json.dumps(conversation)}]",
            f"content={json.dumps(content)}",
            f"role={json.dumps(role)}",
            f"timestamp={json.dumps(timestamp)}",
        ]
        if order:
            arguments.append(f"order={json.dumps(order)}")
        mapped = f"map_turns({', '.join(arguments)})"
        return (
            "def transform(row, ctx):\n"
            "    return {\n"
            f'        "sessionId": str({session}),\n'
            f'        "conversation": {mapped},\n'
            "    }\n"
        )

    content = _row_access(profile.get("content_field"), "json_text(row)")
    role = _row_access(profile.get("role_field"), '"user"')
    timestamp = _row_access(profile.get("timestamp_field"), '""')
    order = profile.get("order_field")
    order_line = f',\n                "_order": row[{json.dumps(order)}]' if order else ""
    return (
        "def transform(row, ctx):\n"
        "    return {\n"
        f'        "sessionId": str({session}),\n'
        '        "conversation": [\n'
        "            {\n"
        f'                "content": str({content}),\n'
        f'                "role": normalize_role({role}),\n'
        f'                "timestamp": str({timestamp}){order_line},\n'
        "            }\n"
        "        ],\n"
        "    }\n"
    )


def _normalize_role(value: Any) -> str:
    normalized = str(value or "user").strip().casefold()
    aliases = {
        "human": "user",
        "customer": "user",
        "사용자": "user",
        "ai": "assistant",
        "bot": "assistant",
        "agent": "assistant",
        "assistant/analysis": "assistant",
        "assistant/final": "assistant",
    }
    return aliases.get(
        normalized, normalized if normalized in {"user", "assistant", "system", "tool"} else "user"
    )


def _map_turns(
    values: Any,
    *,
    content: str,
    role: str,
    timestamp: str,
    order: str | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise ValueError("map_turns의 입력은 list여야 합니다.")
    turns: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError("conversation 항목은 object여야 합니다.")
        turn = {
            "content": str(value.get(content, "")),
            "role": _normalize_role(value.get(role, "user")),
            "timestamp": str(value.get(timestamp, "")),
        }
        if order:
            turn["_order"] = value.get(order, index)
        turns.append(turn)
    return turns


class _SafeTransform:
    def __init__(self, source: str) -> None:
        try:
            tree = ast.parse(source, mode="exec")
        except SyntaxError as error:
            raise ValueError(f"코드 문법 오류: {error.msg} (line {error.lineno})") from error
        if len(list(ast.walk(tree))) > 300:
            raise ValueError("변환 코드가 너무 복잡합니다.")
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        if len(functions) != 1 or functions[0].name != "transform":
            raise ValueError("transform 함수 하나만 정의할 수 있습니다.")
        function = functions[0]
        if (
            function.decorator_list
            or len(function.body) != 1
            or not isinstance(function.body[0], ast.Return)
        ):
            raise ValueError("transform 함수에는 return 문 하나만 사용할 수 있습니다.")
        if not 1 <= len(function.args.args) <= 2:
            raise ValueError("transform(row) 또는 transform(row, ctx) 형식이어야 합니다.")
        if len(tree.body) != 1:
            raise ValueError("transform 함수 밖의 코드는 허용되지 않습니다.")
        self._expression = function.body[0].value

    def run(self, row: dict[str, Any], row_index: int) -> Any:
        return self._eval(self._expression, {"row": row, "ctx": {"row_index": row_index}})

    def _eval(self, node: ast.AST, env: dict[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            raise ValueError(f"허용되지 않은 이름입니다: {node.id}")
        if isinstance(node, ast.Dict):
            return {
                self._eval(key, env): self._eval(value, env)
                for key, value in zip(node.keys, node.values, strict=True)
                if key is not None
            }
        if isinstance(node, (ast.List, ast.Tuple)):
            return [self._eval(item, env) for item in node.elts]
        if isinstance(node, ast.Subscript):
            value = self._eval(node.value, env)
            key = self._eval(node.slice, env)
            try:
                return value[key]
            except (KeyError, IndexError, TypeError) as error:
                raise ValueError(f"필드를 읽을 수 없습니다: {key}") from error
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._eval(node.left, env) + self._eval(node.right, env)
        if isinstance(node, ast.IfExp):
            return self._eval(node.body if self._eval(node.test, env) else node.orelse, env)
        if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
            left = self._eval(node.left, env)
            right = self._eval(node.comparators[0], env)
            operation = node.ops[0]
            if isinstance(operation, ast.Eq):
                return left == right
            if isinstance(operation, ast.NotEq):
                return left != right
            raise ValueError("== 또는 != 비교만 사용할 수 있습니다.")
        if isinstance(node, ast.JoinedStr):
            return "".join(str(self._eval(value, env)) for value in node.values)
        if isinstance(node, ast.FormattedValue):
            return self._eval(node.value, env)
        if isinstance(node, ast.Call):
            args = [self._eval(argument, env) for argument in node.args]
            kwargs = {item.arg: self._eval(item.value, env) for item in node.keywords if item.arg}
            if isinstance(node.func, ast.Name):
                functions = {
                    "str": str,
                    "int": int,
                    "float": float,
                    "normalize_role": _normalize_role,
                    "map_turns": _map_turns,
                    "json_text": lambda value: json.dumps(value, ensure_ascii=False, default=str),
                }
                function = functions.get(node.func.id)
                if function is None:
                    raise ValueError(f"허용되지 않은 함수입니다: {node.func.id}")
                return function(*args, **kwargs)
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(self._eval(node.func.value, env), dict)
            ):
                return self._eval(node.func.value, env).get(*args)
            raise ValueError("허용되지 않은 함수 호출입니다.")
        raise ValueError(f"허용되지 않은 Python 문법입니다: {type(node).__name__}")


def _order_key(value: Any) -> tuple[int, Any]:
    if isinstance(value, (int, float)):
        return (0, value)
    return (1, str(value))


def _transform_rows(rows: list[dict[str, Any]], code: str) -> dict[str, Any]:
    transform = _SafeTransform(code)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dropped = 0
    fragments = 0
    for row_index, row in enumerate(rows):
        output = transform.run(row, row_index)
        if output is None:
            dropped += 1
            continue
        values = output if isinstance(output, list) else [output]
        for fragment in values:
            if not isinstance(fragment, dict):
                raise ValueError(f"row {row_index}: fragment는 object여야 합니다.")
            session_id = str(fragment.get("sessionId", "")).strip()
            turns = fragment.get("conversation")
            if not session_id:
                raise ValueError(f"row {row_index}: sessionId가 비어 있습니다.")
            if not isinstance(turns, list) or not turns:
                raise ValueError(f"row {row_index}: conversation은 비어 있지 않은 list여야 합니다.")
            fragments += 1
            for turn_index, turn in enumerate(turns):
                if not isinstance(turn, dict):
                    raise ValueError(f"row {row_index}: turn은 object여야 합니다.")
                grouped[session_id].append(
                    {
                        "content": str(turn.get("content", "")),
                        "role": _normalize_role(turn.get("role")),
                        "timestamp": str(turn.get("timestamp", "")),
                        "_order": turn.get("_order"),
                        "_source_order": (row_index, turn_index),
                    }
                )

    sessions: list[dict[str, Any]] = []
    for session_id, turns in grouped.items():
        explicit = [turn.get("_order") for turn in turns]
        if all(value is not None for value in explicit):
            if len({_json_digest(value) for value in explicit}) != len(explicit):
                raise ValueError(f"session {session_id}: _order 값이 중복됩니다.")
            turns.sort(key=lambda turn: _order_key(turn["_order"]))
        else:
            turns.sort(key=lambda turn: turn["_source_order"])
        conversation = [
            {
                "content": turn["content"],
                "role": turn["role"],
                "timestamp": turn["timestamp"],
            }
            for turn in turns
        ]
        sessions.append({"sessionId": session_id, "conversation": conversation})
    sessions.sort(key=lambda item: item["sessionId"])
    return {
        "sessions": sessions,
        "stats": {
            "input_rows": len(rows),
            "dropped_rows": dropped,
            "fragments": fragments,
            "sessions": len(sessions),
            "turns": sum(len(item["conversation"]) for item in sessions),
        },
    }


def _strip_code_fence(value: str) -> str:
    text = value.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines:
        lines.pop(0)
    if lines and lines[-1].strip() == "```":
        lines.pop()
    return "\n".join(lines).strip()


async def _llm_code(
    rows: list[dict[str, Any]],
    profile: dict[str, Any],
    instructions: str,
    fallback: str,
) -> tuple[str, str]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return fallback, "structure profiler"
    try:
        from lotte_agent.models import AsyncOpenAIClient

        model = AsyncOpenAIClient(
            api_key=api_key,
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini",
            base_url=os.getenv("OPENAI_BASE_URL", "").strip() or None,
        )
        prompt = (
            "Create only a restricted Python transform function. It must have exactly one return "
            "statement and no imports, assignments, loops, comprehensions, attributes, or "
            "global state. "
            "Allowed helpers: str, int, float, normalize_role, map_turns, json_text. "
            "Signature: def transform(row, ctx). Return None, one object, or a list of objects. "
            "Each object must be {sessionId: string, conversation: [{content, role, timestamp, "
            "optional _order}]}. Multiple rows with the same sessionId are grouped "
            "automatically.\n\n"
            f"Profile:\n{json.dumps(profile, ensure_ascii=False)}\n\n"
            f"Sample:\n{json.dumps(rows[:3], ensure_ascii=False, default=str)[:12000]}\n\n"
            f"User instructions:\n{instructions or 'Infer the safest mapping.'}"
        )
        output = await model.generate(
            [
                {"role": "system", "content": "Return only executable transform code."},
                {"role": "user", "content": prompt},
            ]
        )
        candidate = _strip_code_fence(str(output.text or ""))
        _transform_rows(rows[: min(len(rows), 10)], candidate)
        return candidate, "LLM generated"
    except Exception:
        logger.exception("Import transform generation failed; using structure profiler")
        return fallback, "structure profiler fallback"


def _memory_extraction_fallback(conversation: list[dict[str, Any]]) -> dict[str, list[Any]]:
    first_user = next(
        (
            str(turn.get("content") or "").strip()
            for turn in conversation
            if turn.get("role") == "user"
        ),
        "",
    )
    last_assistant = next(
        (
            str(turn.get("content") or "").strip()
            for turn in reversed(conversation)
            if turn.get("role") == "assistant"
        ),
        "",
    )
    episode = ""
    if first_user and last_assistant:
        episode = (
            f"사용자가 '{first_user[:500]}'라고 요청했고, "
            f"Assistant가 '{last_assistant[:900]}'라고 응답했다."
        )
    return {
        "facts": [],
        "preferences": [],
        "episodes": [{"content": episode, "confidence": 0.55}] if episode else [],
        "culture": [],
    }


def _bounded_extraction(payload: Any) -> dict[str, list[dict[str, Any]]]:
    source = payload if isinstance(payload, dict) else {}

    def items(name: str, limit: int) -> list[dict[str, Any]]:
        raw = source.get(name, [])
        if not isinstance(raw, list):
            return []
        return [dict(item) for item in raw[:limit] if isinstance(item, dict)]

    return {
        "facts": items("facts", 3),
        "preferences": items("preferences", 2),
        "episodes": items("episodes", 2),
        "culture": items("culture", 1),
    }


async def _extract_session_memories(
    conversation: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    fallback = _memory_extraction_fallback(conversation)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return fallback
    try:
        from lotte_agent.models import AsyncOpenAIClient

        model = AsyncOpenAIClient(
            api_key=api_key,
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini",
            base_url=os.getenv("OPENAI_BASE_URL", "").strip() or None,
        )
        transcript = json.dumps(conversation, ensure_ascii=False, default=str)[:20_000]
        output = await model.generate(
            [
                {
                    "role": "system",
                    "content": (
                        "대화에서 장기 기억으로 재사용할 명시적 정보만 추출한다. "
                        "추측하거나 일반 상식을 "
                        "추가하지 않는다. JSON object만 반환한다. schema: "
                        '{"facts":[{"content":str,"confidence":0..1}],'
                        '"preferences":[{"content":str,"condition":str,"action":str,"confidence":0..1}],'
                        '"episodes":[{"content":str,"confidence":0..1}],'
                        '"culture":[{"claim":str,"conditions":[str],"restrictions":[str],'
                        '"recovery":str,"confidence":0..1}]}. '
                        "facts는 지속 가능한 사용자/업무 사실, preferences는 반복 적용할 "
                        "사용자 선호, episodes는 중요한 수행 경험이나 결과, culture는 개인 "
                        "선호가 아닌 명시적인 공유 규범만 포함한다. 각 배열은 최대 facts 3, "
                        "preferences 2, episodes 2, culture 1개다."
                    ),
                },
                {"role": "user", "content": transcript},
            ]
        )
        parsed = json.loads(_strip_code_fence(str(output.text or "")))
        return _bounded_extraction(parsed)
    except Exception:
        logger.exception("Import memory extraction failed; using conservative fallback")
        return fallback


def _feature_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    features = []
    for key in sorted({str(key) for row in rows for key in row}):
        value = next((row.get(key) for row in rows if row.get(key) is not None), None)
        kind = (
            "list"
            if isinstance(value, list)
            else "object"
            if isinstance(value, dict)
            else type(value).__name__
        )
        features.append({"name": key, "type": kind})
    return features


class DemoImportStudio:
    def __init__(self) -> None:
        self._preparations: OrderedDict[str, _Preparation] = OrderedDict()
        self._jobs: OrderedDict[str, _ImportJob] = OrderedDict()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def _hf_request(
        self,
        source: DemoImportSourceBody,
        endpoint: str,
        **params: Any,
    ) -> dict[str, Any]:
        if not source.repo_id or "/" not in source.repo_id:
            raise HTTPException(
                status_code=422,
                detail="Hugging Face dataset ID는 namespace/name 형식이어야 합니다.",
            )
        query = {
            "dataset": source.repo_id,
            "config": source.config or "default",
            "split": source.split or "train",
            **params,
        }
        headers = {"Authorization": f"Bearer {source.token}"} if source.token else {}
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                response = await client.get(
                    f"{HF_DATASET_VIEWER}/{endpoint}", params=query, headers=headers
                )
        except httpx.HTTPError as error:
            raise HTTPException(
                status_code=502, detail="Hugging Face Dataset Viewer에 연결하지 못했습니다."
            ) from error
        if response.status_code in {401, 403}:
            raise HTTPException(
                status_code=403, detail="Hugging Face token 또는 dataset 접근 권한을 확인해 주세요."
            )
        if response.status_code == 404:
            raise HTTPException(
                status_code=404, detail="dataset, config 또는 split을 찾을 수 없습니다."
            )
        if response.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail="Hugging Face 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.",
            )
        if not response.is_success:
            raise HTTPException(
                status_code=502, detail="Hugging Face dataset을 불러오지 못했습니다."
            )
        payload = response.json()
        if isinstance(payload, dict) and payload.get("error"):
            raise HTTPException(status_code=422, detail=str(payload["error"])[:300])
        return payload

    async def _hf_first_rows(
        self, source: DemoImportSourceBody
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        payload = await self._hf_request(source, "first-rows")
        rows = [item.get("row", {}) for item in payload.get("rows", []) if isinstance(item, dict)]
        if not rows:
            raise HTTPException(status_code=422, detail="preview 가능한 row가 없습니다.")
        return (
            rows,
            list(payload.get("features", [])),
            int(payload.get("num_rows_total") or len(rows)),
        )

    async def prepare(self, tenant_id: str, body: DemoImportPrepareBody) -> dict[str, Any]:
        if body.source.type == "local":
            if not body.rows:
                raise HTTPException(status_code=422, detail="JSON 또는 JSONL row가 필요합니다.")
            rows = body.rows
            features = _feature_payload(rows)
            total_rows = len(rows)
        else:
            rows, features, total_rows = await self._hf_first_rows(body.source)

        profile = _profile_rows(rows)
        fallback = _heuristic_code(profile)
        code, generator = await _llm_code(rows, profile, body.instructions, fallback)
        try:
            result = _transform_rows(rows[: body.sample_size], code)
        except ValueError:
            code = fallback
            generator = "structure profiler fallback"
            result = _transform_rows(rows[: body.sample_size], code)

        preparation_id = f"imp_{secrets.token_urlsafe(9)}"
        preparation = _Preparation(
            preparation_id=preparation_id,
            tenant_id=tenant_id,
            source=body.source,
            rows=rows,
            features=features,
            total_rows=total_rows,
            profile=profile,
            code=code,
            generator=generator,
        )
        async with self._lock:
            self._preparations[preparation_id] = preparation
            self._preparations.move_to_end(preparation_id)
            while len(self._preparations) > MAX_PREPARATIONS:
                self._preparations.popitem(last=False)
        return self._preview_payload(preparation, rows[: body.sample_size], code, result)

    def _get(self, tenant_id: str, preparation_id: str) -> _Preparation:
        preparation = self._preparations.get(preparation_id)
        if preparation is None or preparation.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="import preparation을 찾을 수 없습니다.")
        return preparation

    def _preview_payload(
        self,
        preparation: _Preparation,
        rows: list[dict[str, Any]],
        code: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        total_rows = preparation.total_rows
        processing_allowed = (
            total_rows
            <= (MAX_HF_ROWS if preparation.source.type == "huggingface" else MAX_LOCAL_ROWS)
            and preparation.profile.get("layout") != "REUSED_SESSION_ID_SUSPECTED"
        )
        return {
            "preparation_id": preparation.preparation_id,
            "source": {
                "type": preparation.source.type,
                "label": preparation.source.file_name or preparation.source.repo_id,
                "config": preparation.source.config,
                "split": preparation.source.split,
                "total_rows": total_rows,
                "demo_limit": MAX_HF_ROWS
                if preparation.source.type == "huggingface"
                else MAX_LOCAL_ROWS,
            },
            "features": preparation.features,
            "profile": preparation.profile,
            "original": rows,
            "code": code,
            "code_digest": hashlib.sha256(code.encode()).hexdigest()[:12],
            "generator": preparation.generator,
            "result": result["sessions"][:5],
            "stats": result["stats"],
            "processing_allowed": processing_allowed,
            "warnings": [
                *preparation.profile.get("warnings", []),
                *(
                    [f"데모는 최대 {MAX_HF_ROWS:,} rows까지 전체 처리합니다."]
                    if preparation.source.type == "huggingface" and total_rows > MAX_HF_ROWS
                    else []
                ),
            ],
        }

    async def preview(
        self,
        tenant_id: str,
        preparation_id: str,
        body: DemoImportPreviewBody,
    ) -> dict[str, Any]:
        preparation = self._get(tenant_id, preparation_id)
        rows = preparation.rows[: body.sample_size]
        try:
            result = _transform_rows(rows, body.code)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        preparation.code = body.code
        preparation.generator = "user edited"
        return self._preview_payload(preparation, rows, body.code, result)

    async def _all_rows(
        self, preparation: _Preparation, job: _ImportJob | None = None
    ) -> list[dict[str, Any]]:
        if preparation.source.type == "local":
            return preparation.rows
        if preparation.total_rows > MAX_HF_ROWS:
            raise HTTPException(
                status_code=422,
                detail=f"데모는 Hugging Face dataset을 {MAX_HF_ROWS:,} rows까지 처리합니다.",
            )
        rows: list[dict[str, Any]] = []
        for offset in range(0, preparation.total_rows, 100):
            if job is not None:
                await self._checkpoint(job)
            payload = await self._hf_request(
                preparation.source,
                "rows",
                offset=offset,
                length=min(100, preparation.total_rows - offset),
            )
            rows.extend(
                item.get("row", {}) for item in payload.get("rows", []) if isinstance(item, dict)
            )
        return rows

    async def process(
        self,
        tenant_id: str,
        preparation_id: str,
        body: DemoImportProcessBody,
        application: Any,
    ) -> dict[str, Any]:
        preparation = self._get(tenant_id, preparation_id)
        if preparation.profile.get("layout") == "REUSED_SESSION_ID_SUSPECTED":
            raise HTTPException(
                status_code=422,
                detail=(
                    "같은 session ID에서 turn order가 반복됩니다. "
                    "원본 ID 매핑을 수정한 뒤 다시 분석해 주세요."
                ),
            )
        try:
            _transform_rows(preparation.rows[:DEFAULT_SAMPLE_SIZE], body.code)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        job = _ImportJob(
            job_id=f"mij_{secrets.token_urlsafe(8)}",
            tenant_id=tenant_id,
            preparation_id=preparation_id,
            source_label=(
                preparation.source.file_name or preparation.source.repo_id or "대화 데이터"
            )[:300],
        )
        async with self._lock:
            self._jobs[job.job_id] = job
            self._jobs.move_to_end(job.job_id)
            while len(self._jobs) > MAX_IMPORT_JOBS:
                removable = next(
                    (
                        job_id
                        for job_id, candidate in self._jobs.items()
                        if candidate.status in {"COMPLETED", "FAILED", "CANCELLED"}
                    ),
                    None,
                )
                if removable is None:
                    break
                self._jobs.pop(removable, None)

        task = asyncio.create_task(
            self._run_process(job, preparation, body.code, application),
            name=f"demo-import-{job.job_id}",
        )
        self._tasks[job.job_id] = task
        task.add_done_callback(lambda _task, job_id=job.job_id: self._tasks.pop(job_id, None))
        return job.payload()

    def job_status(self, tenant_id: str, job_id: str) -> dict[str, Any]:
        return self._job(tenant_id, job_id).payload()

    async def list_jobs(self, tenant_id: str, application: Any) -> dict[str, Any]:
        jobs = [job.payload() for job in self._jobs.values() if job.tenant_id == tenant_id]
        live_ids = {job["job_id"] for job in jobs}
        recovered: dict[str, dict[str, Any]] = {}
        memories = await application.list_facts(
            tenant_id, include_suppressed=False, limit=MAX_DEMO_MEMORIES
        )
        for memory in memories:
            job_id = str(memory.metadata.get("import_job_id") or "")
            if not job_id or job_id in live_ids:
                continue
            item = recovered.setdefault(
                job_id,
                {
                    "job_id": job_id,
                    "preparation_id": "",
                    "source_label": str(memory.metadata.get("source_label") or "대화 데이터"),
                    "created_at": memory.created_at.isoformat(),
                    "status": "INTERRUPTED",
                    "stage": "서버 재시작 후 복구된 작업",
                    "progress": 0,
                    "completed_sessions": 0,
                    "created_memories": 0,
                    "created_cultural_memories": 0,
                    "total_sessions": 0,
                    "memory_counts": {
                        "conversation": 0,
                        "fact": 0,
                        "preference": 0,
                        "episode": 0,
                        "culture": 0,
                    },
                    "result": None,
                    "error": "서버 재시작으로 실행 상태가 종료되었습니다.",
                    "_sessions": set(),
                },
            )
            item["created_memories"] += 1
            if memory.kind in item["memory_counts"]:
                item["memory_counts"][memory.kind] += 1
            session_id = str(memory.metadata.get("source_session_id") or "")
            if session_id:
                item["_sessions"].add(session_id)
            item["total_sessions"] = max(
                item["total_sessions"],
                int(memory.metadata.get("import_total_sessions") or 0),
            )

        for artifact in await application.list_cultural_artifacts(tenant_id, scope="default"):
            job_id = str(artifact.metadata.get("import_job_id") or "")
            if not job_id or job_id in live_ids:
                continue
            item = recovered.get(job_id)
            if item is None:
                continue
            item["created_cultural_memories"] += 1
            item["memory_counts"]["culture"] += 1

        for item in recovered.values():
            item["completed_sessions"] = len(item.pop("_sessions"))
            if item["total_sessions"] and item["completed_sessions"] >= item["total_sessions"]:
                item["status"] = "COMPLETED"
                item["stage"] = "메모리 반영 완료"
                item["progress"] = 100
                item["error"] = None
            jobs.append(item)
        jobs.sort(key=lambda item: item["created_at"], reverse=True)
        return {"items": jobs}

    def _job(self, tenant_id: str, job_id: str) -> _ImportJob:
        job = self._jobs.get(job_id)
        if job is None or job.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="import job을 찾을 수 없습니다.")
        return job

    async def pause_job(self, tenant_id: str, job_id: str) -> dict[str, Any]:
        job = self._job(tenant_id, job_id)
        if job.status not in {"QUEUED", "RUNNING"}:
            raise HTTPException(status_code=409, detail="실행 중인 작업만 일시정지할 수 있습니다.")
        job.resumable_stage = job.stage
        job.resume_event.clear()
        job.status = "PAUSED"
        job.stage = "일시정지됨"
        return job.payload()

    async def resume_job(self, tenant_id: str, job_id: str) -> dict[str, Any]:
        job = self._job(tenant_id, job_id)
        if job.status != "PAUSED":
            raise HTTPException(status_code=409, detail="일시정지된 작업만 재개할 수 있습니다.")
        job.status = "RUNNING"
        job.stage = job.resumable_stage
        job.resume_event.set()
        return job.payload()

    async def delete_job(
        self,
        tenant_id: str,
        job_id: str,
        application: Any,
    ) -> dict[str, int | bool]:
        job = self._jobs.get(job_id)
        if job is not None and job.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="import job을 찾을 수 없습니다.")
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        deleted_memories = 0
        found = job is not None
        for memory in await application.list_facts(
            tenant_id, include_suppressed=False, limit=MAX_DEMO_MEMORIES
        ):
            if str(memory.metadata.get("import_job_id") or "") != job_id:
                continue
            found = True
            await application.suppress_fact(tenant_id, memory.fact_id)
            deleted_memories += 1

        deleted_cultural = 0
        artifacts = await application.list_cultural_artifacts(tenant_id, scope="default")
        for artifact in artifacts:
            if str(artifact.metadata.get("import_job_id") or "") != job_id:
                continue
            found = True
            await application.withdraw_cultural_artifact(tenant_id, artifact.artifact_id)
            deleted_cultural += 1
        if deleted_cultural:
            await application.publish_cultural_snapshot(
                tenant_id,
                "default",
                policy_version="mnemome-import-auto-v1",
            )
        if not found:
            raise HTTPException(status_code=404, detail="import job을 찾을 수 없습니다.")

        async with self._lock:
            self._jobs.pop(job_id, None)
            self._tasks.pop(job_id, None)
        return {
            "deleted": True,
            "deleted_memories": deleted_memories,
            "deleted_cultural_memories": deleted_cultural,
        }

    async def _checkpoint(self, job: _ImportJob) -> None:
        if job.resume_event.is_set():
            return
        await job.resume_event.wait()

    async def _run_process(
        self,
        job: _ImportJob,
        preparation: _Preparation,
        code: str,
        application: Any,
    ) -> None:
        try:
            job.resumable_stage = "전체 row 불러오는 중"
            if job.resume_event.is_set():
                job.status = "RUNNING"
                job.stage = job.resumable_stage
            job.progress = 10
            await asyncio.sleep(0)
            await self._checkpoint(job)
            result = await self._execute_process(job, preparation, code, application)
            job.result = result
            job.status = "COMPLETED"
            job.stage = "메모리 반영 완료"
            job.progress = 100
        except HTTPException as error:
            job.status = "FAILED"
            job.stage = "Processing 실패"
            job.error = str(error.detail)[:500]
        except asyncio.CancelledError:
            job.status = "CANCELLED"
            job.stage = "삭제 중"
            raise
        except Exception:
            logger.exception("Background import job failed", extra={"job_id": job.job_id})
            job.status = "FAILED"
            job.stage = "Processing 실패"
            job.error = "백그라운드 처리 중 문제가 발생했습니다."
        finally:
            preparation.source.token = None

    async def _execute_process(
        self,
        job: _ImportJob,
        preparation: _Preparation,
        code: str,
        application: Any,
    ) -> dict[str, Any]:
        tenant_id = job.tenant_id
        rows = await self._all_rows(preparation, job)
        job.stage = "session 대화로 변환하는 중"
        job.progress = 35
        try:
            result = _transform_rows(rows, code)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        sessions = result["sessions"]
        job.total_sessions = len(sessions)
        if len(sessions) > MAX_IMPORT_MEMORIES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"데모에서는 한 번에 session {MAX_IMPORT_MEMORIES}개까지 메모리로 저장합니다."
                ),
            )
        job.stage = "대화와 파생 메모리를 분석하는 중"
        job.resumable_stage = job.stage
        job.progress = 40
        existing = await application.list_facts(tenant_id, limit=MAX_DEMO_MEMORIES)
        capacity = max(0, MAX_DEMO_MEMORIES - len(existing))
        existing_keys = {
            str(item.metadata.get("import_key"))
            for item in existing
            if item.metadata.get("import_key")
        }
        code_digest = hashlib.sha256(code.encode()).hexdigest()
        created = 0
        created_cultural = 0
        duplicates = 0
        for index, session in enumerate(sessions):
            await self._checkpoint(job)
            job.stage = f"session {index + 1}/{len(sessions)} 분석 중"
            job.resumable_stage = job.stage
            import_key = _json_digest(
                {
                    "source": preparation.source.file_name or preparation.source.repo_id,
                    "session": session,
                    "code": code_digest,
                }
            )
            if import_key in existing_keys:
                duplicates += 1
                job.completed_sessions = index + 1
                job.progress = 40 + int((index + 1) / max(1, len(sessions)) * 55)
                continue
            if created >= capacity:
                raise HTTPException(
                    status_code=409,
                    detail=f"데모 세션의 메모리 {MAX_DEMO_MEMORIES}개 제한을 초과합니다.",
                )
            conversation = session["conversation"]
            first_user = next(
                (turn["content"] for turn in conversation if turn["role"] == "user"),
                conversation[0]["content"],
            )
            last_assistant = next(
                (turn["content"] for turn in reversed(conversation) if turn["role"] == "assistant"),
                conversation[-1]["content"],
            )
            statement = last_assistant.strip() or first_user.strip() or "가져온 대화"
            await self._checkpoint(job)
            conversation_memory = await application.create_fact(
                tenant_id,
                statement[:20_000],
                sources=(SourceRef("demo_import", f"{job.job_id}:{session['sessionId']}"),),
                kind="conversation",
                tags=("imported", "conversation"),
                metadata={
                    "created_via": "import_studio",
                    "import_job_id": job.job_id,
                    "import_memory_role": "source",
                    "source_label": preparation.source.file_name or preparation.source.repo_id,
                    "source_session_id": session["sessionId"],
                    "import_session_index": index,
                    "import_total_sessions": len(sessions),
                    "task_text": f"[사용자 질문]\n{first_user[:10_000]}",
                    "turn_count": len(conversation),
                    "transform_code_digest": code_digest,
                    "import_key": import_key,
                },
            )
            existing_keys.add(import_key)
            created += 1
            job.memory_counts["conversation"] += 1
            job.created_memories = created

            await self._checkpoint(job)
            extraction = await _extract_session_memories(conversation)
            await self._checkpoint(job)
            base_metadata = {
                "created_via": "import_studio_auto_extract",
                "import_job_id": job.job_id,
                "import_memory_role": "derived",
                "source_label": preparation.source.file_name or preparation.source.repo_id,
                "source_session_id": session["sessionId"],
                "import_session_index": index,
                "import_total_sessions": len(sessions),
                "source_conversation_memory_id": conversation_memory.fact_id,
                "transform_code_digest": code_digest,
            }
            for kind, extraction_key in (
                ("fact", "facts"),
                ("preference", "preferences"),
                ("episode", "episodes"),
            ):
                for item_index, item in enumerate(extraction[extraction_key]):
                    await self._checkpoint(job)
                    content = str(item.get("content") or "").strip()
                    if not content:
                        continue
                    if created >= capacity:
                        raise HTTPException(
                            status_code=409,
                            detail=(f"데모 세션의 메모리 {MAX_DEMO_MEMORIES}개 제한을 초과합니다."),
                        )
                    try:
                        confidence = min(1.0, max(0.0, float(item.get("confidence", 0.75))))
                    except (TypeError, ValueError):
                        confidence = 0.75
                    metadata = dict(base_metadata)
                    if kind == "preference":
                        metadata.update(
                            {
                                "preference_condition": str(item.get("condition") or "").strip(),
                                "preference_action": str(item.get("action") or content).strip(),
                            }
                        )
                    await application.create_fact(
                        tenant_id,
                        content[:20_000],
                        confidence=confidence,
                        sources=(
                            SourceRef(
                                "demo_import_derived",
                                f"{job.job_id}:{session['sessionId']}:{kind}:{item_index}",
                            ),
                        ),
                        kind=kind,
                        tags=("imported", "auto-extracted", kind),
                        metadata=metadata,
                    )
                    created += 1
                    job.memory_counts[kind] += 1
                    job.created_memories = created

            cultural_created_for_session = False
            for item_index, item in enumerate(extraction["culture"]):
                await self._checkpoint(job)
                claim = str(item.get("claim") or "").strip()
                if not claim:
                    continue
                conditions = item.get("conditions")
                restrictions = item.get("restrictions")
                try:
                    confidence = min(1.0, max(0.0, float(item.get("confidence", 0.75))))
                except (TypeError, ValueError):
                    confidence = 0.75
                await application.create_cultural_artifact(
                    tenant_id,
                    "default",
                    claim[:20_000],
                    conditions=tuple(
                        str(value).strip()[:2_000]
                        for value in (conditions if isinstance(conditions, list) else [])
                        if str(value).strip()
                    ),
                    restrictions=tuple(
                        str(value).strip()[:2_000]
                        for value in (restrictions if isinstance(restrictions, list) else [])
                        if str(value).strip()
                    ),
                    recovery=str(item.get("recovery") or "").strip()[:4_000] or None,
                    evidence_refs=(
                        SourceRef(
                            "demo_import_derived",
                            f"{job.job_id}:{session['sessionId']}:culture:{item_index}",
                        ),
                    ),
                    metadata={
                        **base_metadata,
                        "confidence": confidence,
                        "read_only": True,
                        "label": "대화 데이터에서 자동 추출",
                    },
                )
                created_cultural += 1
                job.created_cultural_memories = created_cultural
                job.memory_counts["culture"] += 1
                cultural_created_for_session = True
            if cultural_created_for_session:
                await application.publish_cultural_snapshot(
                    tenant_id,
                    "default",
                    policy_version="mnemome-import-auto-v1",
                )

            job.completed_sessions = index + 1
            job.progress = 40 + int((index + 1) / max(1, len(sessions)) * 55)
        await self._checkpoint(job)
        return {
            "created": created,
            "created_cultural": created_cultural,
            "duplicates": duplicates,
            "sessions": len(sessions),
            "turns": result["stats"]["turns"],
            "code_digest": code_digest[:12],
        }
