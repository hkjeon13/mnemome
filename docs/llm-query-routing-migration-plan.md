# 사용자 Query 키워드 분기 전수 조사와 LLM 라우팅 전환 계획

## 구현 상태

2026-07-22 로컬 작업 트리에 Phase 1-3을 구현했다.

- `QueryRoute`, `LlmQueryRouter`, schema validation과 Agent-decides fallback 추가
- 선호 저장, 현재 실행과 information route를 단일 LLM 호출로 통합
- `_needs_fresh_search()`와 freshness keyword 제어 분기 제거
- route 결정은 run outcome에 기록하고 공개 chat/SSE payload는 유지
- contract, invalid schema/provider failure와 대화 간 선호 적용 회귀 테스트 추가

아직 커밋, 원격 반영, Phase 4 제한 배포와 public acceptance 검증은 수행하지 않았다.

## 1. 결론

조사 시점에 사용자 자연어의 의미를 키워드로 판정해 실행 경로를 바꾸는 잔여 코드는 하나였다.

- `src/mnemome/service/demo.py::_needs_fresh_search()`
  - `뉴스`, `news`, `최신`, `최근`, `오늘`, `현재`, `실시간`, `검색해`, `찾아줘` 중 하나가 있으면 최신 정보 요청으로 간주한다.
  - 이 결과가 참이면 Agent prompt에 `search_retrieve(domain='news', limit=15)` 강제 지시를 추가한다.

직전 변경에서 선호 탐지는 이미 키워드 휴리스틱을 제거하고 LLM 기반
`_analyze_preference_route()`로 바뀌었다. 다만 이 라우터와 최신성 분기가 분리되어 있어 한 Query에
LLM 분석과 키워드 분석이 동시에 적용된다. 권장안은 별도 LLM 호출을 하나 더 추가하는 것이 아니라,
현재 선호 분석 호출을 `QueryRoute` 통합 라우터로 확장해 선호 지속성, 현재 요청 수행 여부와 정보
소스 경로를 한 번에 구조화하는 것이다.

검토 기준 시점의 작업 트리에는 다음 미커밋 변경이 존재하며, 본 계획은 그 상태를 기준으로 한다.

- `src/mnemome/service/demo.py`: LLM 기반 선호 라우터
- `tests/test_lotte_integration.py`: 조건부 선호의 다음 대화 반영 회귀 테스트

## 2. 조사 범위와 판정 기준

조사 범위:

- `src/mnemome/**/*.py`
- `src/mnemome/service/static/**/*`
- `tests/**/*`
- 사용자 Query, memory content, prompt와 tool/domain 선택에 관여하는 문자열 비교, 정규식,
  `casefold()`, `any()` 및 포함 검사

LLM 전환 대상으로 분류하는 기준:

1. 입력이 사용자의 자연어다.
2. 문자열 표현을 근거로 의미나 의도를 추론한다.
3. 판정 결과가 memory write, search 강제, tool/domain 선택 또는 응답 정책을 바꾼다.

반대로 인증 헤더, enum, event type, URL hostname, 저장 포맷 marker처럼 정확한 문법이나 계약을
검증하는 분기는 결정론적으로 유지한다.

## 3. 발견 항목

| 위치 | 현재 방식 | 실행 영향 | 판정 | 조치 |
| --- | --- | --- | --- | --- |
| `src/mnemome/service/demo.py:336-349` | 최신성 키워드 포함 여부 | `search_retrieve`의 news 검색을 강제 | 의미 기반 키워드 라우팅 | 통합 LLM 라우터로 교체 |
| `src/mnemome/service/demo.py:292-333` | LLM이 지속 선호와 일회성 요청 분류 | preference 저장 여부와 정규화 | 이미 LLM 기반 | 통합 `QueryRoute`로 흡수 |
| `src/mnemome/retrieval.py` | MeCab/NLTK/regex 토큰과 BM25 | 관련 memory 후보 순위 | lexical retrieval, 의도 분기 아님 | 현 범위에서 유지 |
| `src/mnemome/service/demo.py:194-201` | `[사용자 질문]` marker 파싱 | 저장된 conversation의 원 Query 복원 | 내부 직렬화 계약 | 결정론적으로 유지 |
| `src/mnemome/service/demo.py:352-356` | 환경변수 tool allowlist | Agent에 노출할 MCP tool 제한 | 보안·운영 설정 | 반드시 결정론적으로 유지 |
| `src/mnemome/service/static/app.js:345-358` | URL hostname 정확 일치 | 출처 링크의 표시 이름 | URL 구조 판정 | 결정론적으로 유지 |
| `src/mnemome/service/static/app.js:364-405` | Markdown/URL 정규식 | 안전한 링크·볼드 DOM 생성 | 문법 파싱 | 결정론적으로 유지 |
| API schema와 application의 kind/status 비교 | enum과 상태 비교 | 계약·상태 전이 | domain invariant | 결정론적으로 유지 |

따라서 이번 전환 범위는 `_needs_fresh_search()` 제거와 기존 선호 라우터의 통합이다. BM25를 embedding
또는 semantic retrieval로 바꾸는 일은 별도의 검색 품질 과제이며, 이번 “키워드 기반 분기 제거”에
섞지 않는다.

## 4. 현재 문제

### 4.1 거짓 양성

다음 질문은 `뉴스`를 포함하지만 최신 뉴스 검색 요청이 아니다.

```text
내가 지금까지 뉴스 물어본 기업들은?
```

현재 구현은 이를 최신 정보 요청으로 판정해 news 검색을 강제한다. 실제 의도는 과거 대화와 장기
기억 조회다.

조건부 선호 설정도 같은 문제가 있다.

```text
엔비디아 뉴스 나타낼 때 하이닉스, 삼성전자도 같이
```

이 문장은 이후 동작을 저장하라는 의미이지만 `뉴스`가 포함되어 있으므로 선호 저장과 동시에 현재
뉴스 검색까지 실행된다. “설정”과 “지금 실행”이라는 speech act가 분리되지 않는다.

### 4.2 거짓 음성

다음 요청은 시의성이 핵심이지만 현재 marker를 포함하지 않을 수 있다.

```text
방금 발표된 엔비디아 실적이 예상치를 넘었어?
이번 분기 삼성전자 잠정 실적 알려줘.
오늘 장 마감 후 나온 공시 내용 요약해 줘.
```

표현이 marker 목록에서 벗어나면 검색 강제 지시가 빠지고, 과거 memory나 모델 지식이 현재 사실처럼
사용될 위험이 있다.

### 4.3 분류 결과의 결합 불가능

현재 선호 라우터는 `persistent_preference | one_shot`만 반환하고, 최신성 분기는 별도 boolean이다.
따라서 아래와 같은 복합 의도를 하나의 일관된 결정으로 표현할 수 없다.

- 선호만 저장하고 현재 검색은 하지 않기
- 선호를 저장하면서 이번에도 바로 적용하기
- 과거 대화만 조회하기
- 최신 news를 반드시 검색하기
- 외부 정보이지만 news가 아니라 일반 web/tool 판단을 Agent에 맡기기

## 5. 목표와 비목표

### 목표

- 자연어 키워드 포함 여부가 아니라 LLM의 구조화된 의미 분석으로 Query 경로를 결정한다.
- 선호 저장과 정보 검색 경로를 한 번의 LLM 호출에서 함께 결정한다.
- `내가 지금까지 뉴스 물어본 기업들은?`를 memory 중심 요청으로 분류한다.
- `엔비디아 뉴스`와 동의 표현을 fresh news 검색으로 분류한다.
- 조건부 선호 설정과 현재 작업 실행 여부를 분리한다.
- 잘못된 schema, timeout과 provider 오류에서 키워드 fallback으로 되돌아가지 않는다.
- 현재 `/demo/api/chat`, `/demo/api/chat/stream` 및 SSE payload 계약을 유지한다.
- Core/Application 계층은 일반 목적 LLM inference에 의존하지 않고 demo service shell 안에 경계를 둔다.

### 비목표

- MCP allowlist를 LLM에게 맡기기
- LLM이 임의 tool 이름이나 URL을 생성해 직접 실행하도록 허용하기
- BM25 memory retrieval을 embedding 검색으로 교체하기
- 전체 Agent planner/tool execution을 새 라우터로 대체하기
- Cultural Memory의 조건 적용을 별도 규칙 엔진으로 다시 구현하기
- 공개 API schema 변경

## 6. 목표 라우팅 계약

`PreferenceRoute`를 다음과 같은 통합 typed contract로 확장한다. 실제 이름은
`DemoQueryRoute` 또는 `QueryRouteDecision`을 권장한다.

```python
class QueryRoute(BaseModel):
    version: Literal["query-route-v1"]
    interaction: Literal[
        "answer",
        "store_preference",
        "answer_and_store_preference",
    ]
    preference_instruction: str | None
    information_route: Literal[
        "fresh_news",
        "fresh_web",
        "memory_context",
        "general_or_agent_decides",
    ]
    search_query: str | None
    confidence: float = Field(ge=0, le=1)
```

필드 불변조건:

- `store_preference` 계열이면 `preference_instruction`이 비어 있으면 안 된다.
- `fresh_news`이면 `search_query`가 필요하고 실제 실행 domain은 서버가 `news`로 고정한다.
- `fresh_web`이면 실제 실행 domain은 서버가 `web`으로 고정한다.
- `memory_context`와 `general_or_agent_decides`에서는 router가 tool 이름을 반환하지 않는다.
- `search_query`는 원 Query의 핵심 대상과 news 의도를 보존해야 하며 새로운 사실을 추가하면 안 된다.
- `confidence`는 관측·fallback 정책용이며 단독으로 side effect 권한을 넓히지 않는다.

예시:

| Query | interaction | information_route | 기대 효과 |
| --- | --- | --- | --- |
| `엔비디아 뉴스` | `answer` | `fresh_news` | news 검색 강제 |
| `엔비디아 관련 최근 동향 알려줘` | `answer` | `fresh_news` | marker 외 표현도 검색 |
| `내가 지금까지 뉴스 물어본 기업들은?` | `answer` | `memory_context` | 과거 memory 중심, news 검색 미강제 |
| `엔비디아 뉴스 나타낼 때 삼성전자도 같이` | `store_preference` | `general_or_agent_decides` | 선호 저장 후 확인 응답, 불필요한 검색 없음 |
| `앞으로 엔비디아 뉴스에 삼성전자도 넣고 지금 뉴스도 보여줘` | `answer_and_store_preference` | `fresh_news` | 저장과 현재 검색 모두 수행 |
| `CUDA가 뭐야?` | `answer` | `general_or_agent_decides` | Agent가 도구 필요 여부 결정 |

## 7. 실행 구조

### 7.1 별도 service adapter로 분리

`demo.py` 안에 prompt, parsing과 fallback을 계속 늘리지 말고 다음 모듈로 분리한다.

```text
src/mnemome/service/query_routing.py
  QueryRoute
  QueryRouter protocol
  LlmQueryRouter
  QueryRoutingError
```

`demo.py`는 다음 application orchestration만 담당한다.

1. `route = await query_router.route(query)`
2. route schema와 불변조건 검증
3. 필요한 경우 preference 저장
4. route에 따라 bounded search instruction 구성
5. Agent 실행
6. route trace와 결과 기록

이 경계는 `ssd/03-service-and-module-architecture.md`의 library-first 원칙을 지킨다. 사용자 Query에 대한
일반 inference는 demo service shell의 책임이며, `application.py`, domain, ports에는 LLM client를 추가하지
않는다.

### 7.2 호출 수

현재 모든 demo Query가 선호 분석용 LLM 호출을 이미 한 번 수행한다. 통합 라우터는 이 호출의 output
schema와 prompt를 확장하는 방식으로 구현한다. 최신성 판단용 두 번째 LLM 호출은 추가하지 않는다.

### 7.3 search 강제 정책

LLM은 high-level `information_route`만 결정한다. 서버가 허용된 route를 다음처럼 결정론적으로 번역한다.

```text
fresh_news -> search_retrieve(domain="news", limit=15)
fresh_web  -> search_retrieve(domain="web", bounded limit)
memory_context -> fresh search 강제 없음, memory context 강조
general_or_agent_decides -> 기존 Agent planner에 위임
```

MCP tool 발견, allowlist와 실제 tool execution은 기존 Lotte Agent 경계를 유지한다. Router가 반환한 임의
tool 이름은 절대 실행하지 않는다.

### 7.4 preference 적용 정책

- `store_preference`: 정규화된 선호를 저장하고 이번 응답은 저장 확인에 집중한다.
- `answer_and_store_preference`: 선호를 저장한 뒤 같은 run의 memory context에도 즉시 포함한다.
- exact-content 중복 검사에는 기존 `casefold()` 비교를 유지한다. 이는 의미 라우팅이 아니라 저장
  idempotency의 최소 deterministic guard다.
- semantic duplicate 병합은 별도 과제로 둔다. 첫 전환에서 기존 preference를 자동 overwrite하거나
  supersede하지 않는다.

## 8. 실패와 안전 정책

LLM router 실패 시 keyword fallback을 사용하지 않는다.

권장 fallback:

```text
interaction = answer
information_route = general_or_agent_decides
preference_instruction = null
search_query = null
fallback_reason = timeout | invalid_schema | provider_error
```

이 정책의 의미:

- schema가 검증되지 않은 상태에서는 장기 preference write를 하지 않는다.
- main Agent의 기존 “현재 사실은 MCP 결과를 우선 사용” 지침은 유지한다.
- router 실패를 사용자 Query 실패로 바로 확대하지 않고 Agent planner가 도구 필요 여부를 판단하게 한다.
- fallback을 정상 LLM 결정처럼 숨기지 않고 trace와 metric에 기록한다.

보안 원칙:

- Router prompt에 MCP credential, API key 또는 다른 tenant memory를 넣지 않는다.
- Router가 생성한 text를 system prompt로 직접 승격하지 않는다. typed enum과 길이 제한을 검증한 후
  서버가 준비한 고정 template에 값만 삽입한다.
- preference instruction은 최대 길이, control character와 prompt delimiter 검증을 거친다.
- 자유 형식 chain-of-thought를 요청하거나 저장하지 않는다.

## 9. 관측 가능성

각 run trace 또는 내부 metadata에 다음을 기록한다.

```text
router_version
router_model
route_latency_ms
interaction
information_route
confidence
fallback_used
fallback_reason
preference_captured
search_enforced
search_domain
```

기록하지 않을 것:

- 자유 형식 내부 reasoning
- credential 또는 MCP session 정보
- 사용자 Query 전체의 중복 로그

운영 지표:

- route별 요청 수
- invalid schema, timeout과 provider error 비율
- preference capture 비율과 중복 비율
- `fresh_news` 이후 실제 `search_retrieve` 실행 성공률
- router p50/p95 latency와 token/cost
- fallback 이후 Agent가 search tool을 선택한 비율

## 10. 테스트 계획

### 10.1 순수 contract test

새 `tests/test_query_routing.py`에서 다음을 검증한다.

- valid structured output parsing
- enum 외 값 거부
- persistent route에서 빈 preference instruction 거부
- fresh route에서 빈 search query 거부
- Markdown fence가 있는 JSON 처리 여부를 명시적으로 결정
- timeout, invalid JSON과 provider error가 `general_or_agent_decides` fallback으로 변환
- router output의 임의 tool 이름 또는 추가 필드를 허용하지 않음

### 10.2 의미 회귀 dataset

최소 fixture:

| 분류 | Query | 기대 route |
| --- | --- | --- |
| 직접 뉴스 | `엔비디아 뉴스` | `fresh_news` |
| 동의 표현 | `엔비디아 관련 새 소식 정리해 줘` | `fresh_news` |
| 시점 의존 | `방금 나온 엔비디아 실적이 예상치를 넘었어?` | `fresh_news` 또는 정책상 `fresh_web` |
| 과거 기억 | `내가 지금까지 뉴스 물어본 기업들은?` | `memory_context` |
| 선호 저장만 | `엔비디아 뉴스 나타낼 때 삼성전자도 같이` | `store_preference` + no forced search |
| 저장+실행 | `앞으로 삼성전자도 포함하고 지금 뉴스 보여줘` | `answer_and_store_preference` + `fresh_news` |
| 일반 지식 | `CUDA가 뭐야?` | `general_or_agent_decides` |
| 선호 조회 | `내가 선호하는 답변 방식은?` | `memory_context`, preference write 없음 |
| 모호함 | `엔비디아 어때?` | `general_or_agent_decides` |

실제 provider를 매 PR마다 호출하는 exact-string test는 만들지 않는다. deterministic fake model로 contract와
orchestration을 검증하고, 별도 evaluation job에서 모델 버전별 의미 정확도를 표본 평가한다.

### 10.3 integration test

`tests/test_lotte_integration.py`에서 다음 E2E 계약을 검증한다.

1. 조건부 선호 저장 후 다음 Query의 context에 정규화된 preference가 포함된다.
2. 선호 저장만 하는 Query는 news search 강제 instruction을 만들지 않는다.
3. `내가 지금까지 뉴스 물어본 기업들은?`는 memory context를 사용하고 news 검색을 강제하지 않는다.
4. `엔비디아 뉴스`는 `search_retrieve`, `domain=news`, `limit=15` 지시를 받는다.
5. router 실패 시 preference가 저장되지 않지만 chat은 계속 완료된다.
6. `/demo/api/chat`와 `/demo/api/chat/stream`의 기존 response/SSE schema가 변하지 않는다.

### 10.4 평가 gate

`ssd/14-testing-and-quality-strategy.md`의 비결정적 평가 원칙에 맞춰 exact wording 대신 typed route의
precision/recall을 본다.

- `fresh_news` false positive rate
- time-sensitive request false negative rate
- preference store precision
- memory-context intent accuracy
- 복합 의도 정확도
- latency와 token/cost 증가

초기 승인 기준 예시:

- 안전성 fixture에서 time-sensitive false negative 0건
- memory-query fixture에서 forced-news-search false positive 0건
- preference write precision 98% 이상
- 현재 구조 대비 추가 model call 0회
- router 실패 시 장기 memory 오염 0건

## 11. 단계별 구현 순서

### Phase 0. 기준선 고정

1. 현재 `_needs_fresh_search()`의 positive/negative fixture를 수집한다.
2. production에서 확인된 오분류 사례를 anonymized evaluation case로 추가한다.
3. 현재 router latency, token과 forced news search 빈도를 기록한다.

완료 조건:

- baseline dataset과 metric 정의가 리뷰 가능하다.
- 기존 공개 API/SSE contract snapshot이 있다.

### Phase 1. 통합 route contract 도입

1. `query_routing.py`와 `QueryRoute` schema를 추가한다.
2. 현재 `PreferenceRoute` prompt를 통합 prompt로 교체한다.
3. schema validation, timeout과 fallback을 adapter 안에 캡슐화한다.
4. route version과 latency trace를 추가한다.

완료 조건:

- 한 번의 LLM 호출로 preference와 information route를 반환한다.
- invalid route가 memory write나 tool 실행으로 이어지지 않는다.

### Phase 2. orchestration 연결

1. `demo.py`가 `QueryRoute`에 따라 preference를 저장한다.
2. `fresh_news`에만 고정 news search instruction을 추가한다.
3. `memory_context`에는 과거 기억 조회 목적을 명확히 전달한다.
4. `store_preference`에서는 불필요한 현재 news 검색을 생략한다.
5. `_needs_fresh_search()`와 기존 keyword unit test를 제거한다.

완료 조건:

- 사용자 자연어를 대상으로 한 keyword control-flow branch가 남지 않는다.
- MCP allowlist와 schema validation은 결정론적으로 유지된다.

### Phase 3. 회귀·품질 검증

1. contract, integration과 semantic fixture를 실행한다.
2. `uv run ruff check .`, `uv run pytest -q`, `uv build`를 통과한다.
3. recorded/fake model로 failure injection을 수행한다.
4. 동일 Query에 대해 route 결정, preference 저장과 tool plan이 추적 가능한지 확인한다.

완료 조건:

- Section 10의 acceptance criteria 충족
- API와 SSE contract diff 없음
- router 실패 시 chat completion 경로 유지

### Phase 4. 제한 배포와 public 검증

1. route version feature flag 또는 tenant canary로 제한 반영한다.
2. 기존 keyword 결과는 초기 관측 기간에 평가용 shadow 값으로만 비교하고 실행 제어에는 사용하지 않는다.
3. false positive/negative, latency, fallback과 tool success를 확인한다.
4. 안정화 후 shadow keyword 계산 코드와 flag를 제거한다.

public acceptance scenario:

1. 새 세션에서 조건부 선호를 저장한다.
2. 새 대화를 시작한다.
3. 짧은 `엔비디아 뉴스` Query로 저장된 조건과 fresh search가 함께 적용되는지 확인한다.
4. `내가 지금까지 뉴스 물어본 기업들은?`에서 외부 news 검색이 강제되지 않는지 확인한다.
5. trace에서 route version, information route, preference capture와 search enforcement를 확인한다.

## 12. 예상 변경 파일

| 파일 | 변경 |
| --- | --- |
| `src/mnemome/service/query_routing.py` | typed route, LLM adapter, validation과 fallback 추가 |
| `src/mnemome/service/demo.py` | 통합 route orchestration, `_needs_fresh_search()` 제거 |
| `tests/test_query_routing.py` | schema, fallback과 의미 fixture 추가 |
| `tests/test_lotte_integration.py` | memory/news/복합 의도 E2E 회귀 보강 |
| `ssd/14-testing-and-quality-strategy.md` | online query router evaluation 항목 반영 |
| `docs/deployment.md` | route model/version, timeout과 관측 설정이 추가될 경우 운영 설명 갱신 |

## 13. 위험과 완화

| 위험 | 영향 | 완화 |
| --- | --- | --- |
| LLM 비결정성 | 같은 표현의 route 변동 | temperature 0, typed schema, versioned prompt, semantic fixture |
| Router timeout | 모든 chat latency 증가 | 짧은 timeout, 1 attempt budget, Agent-decides fallback |
| 잘못된 preference write | 새 대화까지 오염 | validated persistent route만 write, 원문/provenance 저장, 삭제 가능 유지 |
| Router prompt injection | route enum 또는 instruction 오염 | user text delimiter, strict schema, enum/length 검증, arbitrary tool 금지 |
| 과도한 forced search | 비용·지연과 관련성 저하 | `memory_context`와 `store_preference` route 분리, false-positive gate |
| 최신 검색 누락 | 오래된 답변을 현재 사실로 사용 | time-sensitive safety dataset, main Agent freshness guard 유지 |
| Core에 inference 책임 유입 | library/service 경계 훼손 | demo service adapter에만 LLM router 배치 |
| rollout 중 이중 기준 | 재현성 저하 | route version 기록, keyword는 shadow-only 후 제거 |

## 14. 최종 완료 조건

- `_needs_fresh_search()`와 freshness keyword 목록이 삭제되어 있다.
- 사용자 자연어에 대한 실행 분기는 validated `QueryRoute`만 사용한다.
- 선호 저장, 현재 실행과 information source route가 한 결정으로 추적된다.
- `뉴스`라는 단어만으로 외부 검색이 강제되지 않는다.
- 동의 표현과 간접적 시점 표현도 fresh route로 분류된다.
- router 오류에서 keyword fallback 없이 Agent가 안전하게 계속 실행된다.
- MCP allowlist, auth, enum, URL 및 protocol parsing은 결정론적으로 유지된다.
- 전체 정적 검사, 테스트, 빌드와 public acceptance scenario가 통과한다.
