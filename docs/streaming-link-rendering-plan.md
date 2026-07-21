# 스트리밍 응답 링크 실시간 렌더링 구현 계획

## 1. 검토 결론

구현 가능하며, 서버의 SSE 계약이나 Agent 실행 코드는 변경할 필요가 없다. 변경 범위는 Playground 프런트엔드의 링크 렌더링과 정적 자산 캐시 키에 한정할 수 있다.

단, 현재 `renderAnswerLinks()`를 매 delta마다 그대로 호출하는 방식은 채택하지 않는다. 아직 닫히지 않은 Markdown 링크 `[제목](https://example...` 안의 URL을 일반 URL 패턴이 먼저 잡아, 잘못된 임시 링크와 raw Markdown 문법이 함께 보일 수 있기 때문이다.

권장 방식은 다음 네 가지 원칙을 따른다.

1. 스트리밍 원문을 DOM과 별도의 문자열에 누적한다.
2. 완성된 Markdown 링크와 경계가 확인된 일반 URL만 링크로 렌더링한다.
3. 현재 작성 중인 링크 꼬리는 텍스트로 보류한다.
4. DOM 갱신은 `requestAnimationFrame`으로 프레임당 최대 한 번 수행하고, 완료 시 서버의 최종 답변으로 다시 확정 렌더링한다.

검토 상태: 설계 완료, 구현 전.

## 2. 현재 동작과 원인

관련 코드는 `src/mnemome/service/static/app.js`에 있다.

- `renderAnswerLinks()`는 Markdown 링크와 일반 HTTP(S) URL을 찾아 안전한 DOM `<a>` 요소로 바꾼다. 현재 위치는 약 361~377행이다.
- 스트리밍 `delta` 이벤트는 약 591~600행에서 `responseText.textContent += payload.delta`로 원문을 일반 텍스트로 추가한다.
- 전체 스트림이 끝난 뒤 약 608행에서만 `renderAnswerLinks()`가 호출된다.

따라서 링크 문법이 이미 완성된 시점에도 전체 답변의 `complete` 이벤트가 도착하기 전까지는 링크가 활성화되지 않는다. 서버는 이미 delta를 정상적으로 전달하고 있으므로, 문제는 전적으로 프런트엔드의 중간 렌더링 정책에 있다.

### 추가 검토 결과

- 서버는 모델 chunk 경계를 그대로 delta로 전달한다(`src/mnemome/service/demo.py` 약 507~512행). 따라서 링크 문법은 `[`, 라벨, `](`, URL, `)` 중 어느 위치에서도 나뉠 수 있다.
- `complete` payload의 `answer`가 서버의 최종 기준 문자열이다(`src/mnemome/service/demo.py` 약 607~608행). 중간 DOM보다 이 값을 우선해야 한다.
- 현재 테스트는 SSE 이벤트의 존재와 순서는 확인하지만 브라우저 DOM에서 링크가 생성되는 시점은 검증하지 않는다(`tests/test_lotte_integration.py` 약 182~220행).
- `sourceLabel()`의 `host.endsWith(domain)`은 `notreuters.com`도 Reuters로 분류할 수 있다. 링크 렌더러를 수정할 때 정확한 호스트이거나 점(`.`) 경계가 있는 하위 도메인인 경우만 일치하도록 보강하는 것이 안전하다.
- 채팅 목록은 이미 `aria-live="polite"`다. 전체 paragraph children을 프레임마다 교체하면 화면 낭독기가 누적 답변을 반복 공지할 수 있으므로 스트리밍 응답에 `aria-busy` 상태가 필요하다.

## 3. 목표와 비목표

### 목표

- `[Reuters 기사](https://www.reuters.com/...)`의 닫는 `)`가 도착하는 즉시 링크로 전환한다.
- 일반 URL은 URL 뒤의 공백, 줄바꿈 등 경계가 확인되는 즉시 링크로 전환한다.
- 아직 전송 중인 URL을 잘못된 `href`로 활성화하지 않는다.
- 기존 최종 답변, 저장된 대화 재생, 중지 및 오류 처리 동작을 보존한다.
- 현재 보안 속성인 `target="_blank"`와 `rel="noopener noreferrer"`를 유지한다.
- 빠른 delta에서도 불필요한 DOM 재생성과 스크롤 갱신을 제한한다.

### 비목표

- 전체 Markdown 파서 도입
- 굵게, 목록, 표, 코드 블록 등 다른 Markdown 문법 지원
- SSE 이벤트 형식 또는 백엔드 스트리밍 로직 변경
- 링크 스타일이나 채팅 레이아웃 변경
- `http:` 및 `https:` 이외의 URL 스킴 허용

## 4. 설계 결정

### 4.1 DOM이 아닌 별도 원문 버퍼를 진실의 원천으로 사용

`sendChat()`의 지역 상태에 다음 값을 추가한다.

```js
let streamedAnswer = "";
let answerRenderFrame = null;
```

`responseText.textContent`를 누적 원문으로 사용하면 안 된다. 링크가 `<a>`로 변환된 뒤 `textContent`를 읽으면 Markdown URL과 문법은 사라지고 화면에 보이는 라벨만 남기 때문이다. 이 상태에서 오류나 중지 메시지를 붙이면 원래 링크를 복구할 수 없다.

모든 delta는 먼저 `streamedAnswer`에 추가한다.

```js
streamedAnswer += payload.delta || "";
```

### 4.2 현재 작성 중인 링크 꼬리를 분리

새 순수 함수 `pendingStreamingLinkStart(text)`를 추가한다. 이 함수는 문자열 끝에서 아직 완성되지 않은 링크 후보의 시작 위치를 반환하고, 후보가 없으면 `text.length`를 반환한다.

검출 대상은 다음 두 종류다.

1. 닫는 `)`가 아직 없는 Markdown 링크 꼬리

   ```text
   [문서 제목](https://example.com/path
   ```

2. 공백이나 줄바꿈 같은 종료 경계가 아직 없는 문자열 끝의 일반 URL

   ```text
   자세한 내용: https://example.com/path
   ```

구현 시 현재 정규식의 지원 범위와 일치하도록 HTTP(S)만 대상으로 한다. Markdown 링크 후보와 일반 URL 후보가 겹치면 더 앞쪽의 시작 위치를 사용한다. 따라서 미완성 Markdown 링크 전체가 텍스트로 유지되고, 내부 URL만 먼저 링크가 되는 현상을 막을 수 있다.

개념 코드는 다음과 같다.

```js
function pendingStreamingLinkStart(text) {
  const markdownTail = text.match(/\[[^\]\n]*\]\(https?:\/\/[^\s)]*$/);
  const bareUrlTail = text.match(/https?:\/\/[^\s<>()]*$/);
  const starts = [markdownTail, bareUrlTail]
    .filter(Boolean)
    .map((match) => match.index);
  return starts.length ? Math.min(...starts) : text.length;
}
```

실제 구현에서는 빈 URL, 정규식 인덱스, 기존 패턴과의 일관성을 테스트로 확인한 뒤 반영한다.

### 4.3 스트리밍 전용 렌더러 추가

기존 `renderAnswerLinks(element, text)`는 최종 답변과 저장된 대화 재생에 그대로 사용한다. 그 위에 스트리밍 전용 래퍼를 추가한다.

```js
function renderStreamingAnswerLinks(element, text) {
  const pendingStart = pendingStreamingLinkStart(text);
  renderAnswerLinks(element, text.slice(0, pendingStart));
  element.append(document.createTextNode(text.slice(pendingStart)));
}
```

동작은 다음과 같다.

- 완성된 Markdown 링크: 즉시 `<a>`로 변환
- 닫는 `)`가 없는 Markdown 링크: 해당 링크 전체를 텍스트로 유지
- 문자열 끝에서 계속 길어지는 일반 URL: 텍스트로 유지
- 일반 URL 뒤에 공백이나 줄바꿈 도착: 그 프레임부터 `<a>`로 변환
- 스트림 완료: 보류 여부와 관계없이 기존 최종 렌더러로 확정

### 4.4 렌더링을 프레임당 한 번으로 제한

delta마다 `replaceChildren()`를 실행하지 않고 렌더링을 예약한다.

```js
function scheduleAnswerRender() {
  if (answerRenderFrame !== null) return;
  answerRenderFrame = requestAnimationFrame(() => {
    answerRenderFrame = null;
    renderStreamingAnswerLinks(responseText, streamedAnswer);
    elements.conversation.scrollTop = elements.conversation.scrollHeight;
  });
}
```

현재 모델 출력은 서버에서 최대 700 output token으로 제한되어 있다. 이 범위에서는 프레임당 전체 답변을 다시 파싱하는 단순한 O(n) 방식이 충분히 작고, 별도 Markdown 라이브러리나 복잡한 DOM diff보다 유지보수 위험이 낮다.

### 4.5 완료, 오류, 중지 시 예약 프레임 정리

최종 렌더 전에 예약된 프레임을 취소해야 늦게 실행된 스트리밍 렌더가 최종 DOM을 덮어쓰지 않는다.

```js
function cancelAnswerRender() {
  if (answerRenderFrame === null) return;
  cancelAnimationFrame(answerRenderFrame);
  answerRenderFrame = null;
}
```

정상 완료 시 서버의 `result.answer`를 최종 진실로 사용한다.

```js
cancelAnswerRender();
const finalAnswer = result.answer || streamedAnswer;
renderAnswerLinks(responseText, finalAnswer);
```

오류 또는 사용자 중지 시에는 DOM의 `textContent`가 아니라 `streamedAnswer`에 오류 문구를 붙인 뒤 최종 링크 렌더러를 사용한다.

```js
cancelAnswerRender();
const partialAnswer = streamedAnswer
  ? `${streamedAnswer}\n\n${errorText}`
  : errorText;
renderAnswerLinks(responseText, partialAnswer);
```

`finally`에서도 예약 프레임을 정리해 예외 경로의 누수를 방지한다.

### 4.6 출처 도메인 라벨 경계 보강

기존 알려진 출처 분류는 단순 suffix 비교 대신 정확한 호스트 또는 실제 하위 도메인만 허용한다.

```js
const knownSource = knownSources.find(([domain]) =>
  host === domain || host.endsWith(`.${domain}`));
```

이 변경은 링크 허용 범위를 넓히지 않는다. `notreuters.com` 같은 유사 도메인이 `Reuters 기사`로 잘못 표시되는 것만 막는다.

### 4.7 live region의 반복 공지 억제

응답 메시지에 스트리밍 동안 `aria-busy="true"`를 설정하고, 정상 완료·오류·중지에서 제거한다. 시각적으로는 계속 실시간 렌더링하지만 보조 기술은 미완성 응답을 매 프레임 전체 문장으로 반복 공지하지 않고 확정 상태를 인식할 수 있다.

```js
responseMessage.setAttribute("aria-busy", "true");
// 정상 완료 또는 catch
responseMessage.removeAttribute("aria-busy");
```

실제 브라우저 접근성 트리에서 busy 상태 해제 후 최종 응답이 노출되는지 확인한다.

### 4.8 첫 구현에서 유지할 기존 제한

현재 일반 URL 정규식은 문장 끝의 마침표나 쉼표를 URL 일부로 포함할 수 있다. 이 문제는 실시간 렌더링으로 새로 생기는 문제가 아니며 URL 토큰화 규칙을 별도로 넓히는 변경이므로 첫 패치에서는 기존 의미를 유지한다. 후속 하드닝에서는 `. , ! ? ; :` 등의 말단 문장부호를 `href` 밖의 text node로 분리한다.

또한 `replaceChildren()` 기반 전체 재렌더 중 사용자가 이미 활성화된 링크에 키보드 포커스를 두면 다음 프레임에 포커스가 해제될 수 있다. 현재 최대 700-token 데모 답변과 짧은 스트리밍 시간을 고려해 첫 구현에서는 수용하되, 실사용 검증에서 문제가 확인되면 “확정된 prefix node 유지 + pending tail만 교체”하는 증분 렌더러를 2단계로 적용한다.

## 5. `sendChat()` 변경 흐름

구현 순서는 다음과 같다.

1. `receivedDelta` 옆에 `streamedAnswer`와 `answerRenderFrame`을 선언한다.
2. 첫 delta에서 기존과 동일하게 계획 UI와 typing 상태를 제거한다.
3. `responseText.textContent += ...`를 제거한다.
4. delta를 `streamedAnswer`에 누적하고 `scheduleAnswerRender()`를 호출한다.
5. 스트림이 끝나면 예약 프레임을 취소하고 `result.answer || streamedAnswer`를 최종 렌더링한다.
6. 오류와 중지 경로가 `responseText.textContent` 대신 `streamedAnswer`를 사용하도록 바꾼다.
7. 최종 `finally`에서 예약 프레임을 취소한다.

의사 코드는 다음과 같다.

```js
} else if (event === "delta") {
  const delta = payload.delta || "";
  if (!delta) return;
  if (!receivedDelta) {
    receivedDelta = true;
    responseText.removeAttribute("aria-label");
    responseText.className = "";
    responseMessage.classList.remove("typing");
  }
  streamedAnswer += delta;
  scheduleAnswerRender();
}
```

## 6. 파일별 변경 계획

### `src/mnemome/service/static/app.js`

- `pendingStreamingLinkStart()` 추가
- `renderStreamingAnswerLinks()` 추가
- `sendChat()`에 원문 버퍼와 프레임 스케줄러 추가
- 정상 완료, 오류, 중지 경로에서 예약 프레임 취소
- 오류 경로의 원문 보존 로직 수정
- 알려진 출처 도메인 비교를 정확한 호스트 경계로 보강
- 스트리밍 응답의 `aria-busy` 설정 및 해제

### `src/mnemome/service/static/index.html`

- `app.js` 쿼리 키를 새 값으로 갱신한다.
- 예: `app.js?v=20260721-streaming-links`
- 운영 CDN 및 브라우저가 이전 스크립트를 계속 사용하는 것을 방지하기 위한 필수 변경이다.

### `tests/test_lotte_integration.py`

- 새 스크립트 캐시 키가 Playground HTML에 포함되는지 확인한다.
- 스트리밍 렌더러, 별도 원문 버퍼, 프레임 제한 코드가 정적 자산에 포함되는지 최소 회귀 검사를 추가한다.
- 기존 SSE `ready → progress → delta → complete` 검사는 그대로 유지한다.

### `design-qa.md`

- 실제 구현 단계에서 동일 viewport의 스트리밍 전·중·완료 상태를 캡처한다.
- 링크 전환 시 레이아웃, 스크롤, 링크 스타일이 변하지 않았음을 기록한다.

백엔드 Python 파일과 CSS는 변경하지 않는다.

## 7. 테스트 계획

현재 저장소에는 JavaScript DOM 단위 테스트 프레임워크가 없다. 따라서 이번 변경에서는 기존 Python 통합 테스트와 브라우저의 결정적 DOM 검증을 함께 사용한다. 정적 문자열 검사만으로 링크 동작이 검증되었다고 간주하지 않는다.

### 7.1 자동화된 저장소 검사

```bash
uv run ruff check .
uv run --extra dev pytest
```

최소 확인 항목:

- Playground와 정적 JavaScript가 정상 제공됨
- SSE 이벤트 순서와 완료 payload가 유지됨
- 새 캐시 키가 HTML에 포함됨
- 기존 IME, 계획 진행, 메모리 재생 관련 정적 회귀 검사가 유지됨

### 7.2 브라우저 단위 시나리오

로컬 Playground를 브라우저에서 열고, detached `<p>` 요소에 스트리밍 렌더러를 직접 호출해 각 단계의 DOM을 검사한다.

| 시나리오 | 입력 순서 | 기대 결과 |
| --- | --- | --- |
| 분할된 Markdown 링크 | `[Reuters](` → `https://reuters.com/a` → `)` | 마지막 단계 전에는 anchor 0개, `)` 직후 anchor 1개 |
| 분할된 일반 URL | `https://example.` → `com/path` → 공백 | 공백 전에는 anchor 0개, 공백 직후 anchor 1개 |
| 링크 뒤 텍스트 계속 생성 | 완성 링크 → 일반 문장 delta | 링크가 답변 완료 전부터 유지됨 |
| 링크 여러 개 | Markdown 링크 2개를 여러 delta로 전송 | 완성된 링크만 순서대로 활성화됨 |
| 악성 스킴 | `[클릭](javascript:alert(1))` | anchor가 생성되지 않고 텍스트로 남음 |
| 유사 출처 도메인 | `reuters.com` 및 `notreuters.com` | 전자만 Reuters 라벨, 후자는 일반 출처 라벨 |
| 중지 | 완성 링크 뒤 생성 중지 | 링크와 부분 답변이 보존되고 중지 문구가 붙음 |
| 오류 | 완성 링크 뒤 error 이벤트 | 링크 원문이 손실되지 않고 오류 문구가 붙음 |
| 최종 답변 우선 | delta 누적값과 `result.answer`가 다름 | 완료 후 DOM은 `result.answer`와 일치 |
| 저장 대화 재생 | 링크가 있는 conversation memory 선택 | 기존과 동일하게 즉시 링크 렌더링 |
| 한국어 및 줄바꿈 | 한글 문장과 여러 줄 사이에 링크 포함 | 문자 손실 및 줄바꿈 변화 없음 |

각 anchor에서 다음 속성을 확인한다.

- 표시 라벨
- 절대 `href`
- `target="_blank"`
- `rel="noopener noreferrer"`

### 7.3 실제 UI 검증

- 답변이 생성되는 동안 완성된 출처 링크가 즉시 파란 링크 스타일로 바뀌는지 확인한다.
- 링크 전환 전후에 문단 너비와 줄바꿈이 비정상적으로 튀지 않는지 확인한다.
- 자동 스크롤이 현재 답변 하단을 유지하는지 확인한다.
- 링크가 생성되는 동안 콘솔 warning/error가 없는지 확인한다.
- 빠르게 중지 버튼을 눌러도 늦은 animation frame이 최종 메시지를 덮어쓰지 않는지 확인한다.
- 키보드로 링크에 접근할 수 있고 새 탭 열기 동작이 유지되는지 확인한다.

## 8. 위험과 대응

| 위험 | 영향 | 대응 |
| --- | --- | --- |
| 미완성 Markdown 내부 URL이 먼저 링크가 됨 | raw 문법과 잘못된 링크가 함께 노출 | 미완성 Markdown 시작점과 URL 시작점 중 앞쪽부터 보류 |
| 문자열 끝 URL을 너무 일찍 활성화 | 클릭 시 잘린 주소로 이동 | 경계 문자가 도착하거나 스트림이 끝날 때까지 보류 |
| 매 token DOM 전체 교체 | 불필요한 렌더 및 스크롤 비용 | `requestAnimationFrame`으로 프레임당 한 번 제한 |
| 예약 프레임이 완료 DOM을 덮어씀 | 최종 답변이 다시 부분 답변으로 변함 | 완료, 오류, 중지, finally에서 프레임 취소 |
| DOM에서 원문을 역추출 | Markdown URL이 라벨로 치환되어 손실 | `streamedAnswer`를 유일한 스트리밍 원문으로 사용 |
| `innerHTML` 기반 Markdown 처리 | XSS 위험 | 기존처럼 `createTextNode()`와 `createElement("a")`만 사용 |
| 알려진 출처 suffix 오분류 | 유사 도메인을 신뢰 출처처럼 표시 | 정확한 호스트 또는 `.` 경계가 있는 하위 도메인만 일치 |
| CDN이 이전 JavaScript 제공 | 배포 후에도 기존 현상 지속 | `index.html`의 `app.js` 캐시 키 변경 및 공개 URL 검증 |
| live region이 빈번한 재렌더를 반복 공지 | 스크린 리더 사용성 저하 가능 | 응답 message에 스트리밍 동안 `aria-busy` 적용 후 모든 종료 경로에서 제거 |
| 재렌더 중 링크 포커스 소실 | 키보드 사용자가 스트림 도중 링크를 탐색하기 어려움 | 첫 구현에서 브라우저 검증; 문제 확인 시 확정 prefix를 보존하는 증분 렌더러로 전환 |

## 9. 완료 기준

다음 조건을 모두 충족하면 구현 완료로 본다.

- 완성된 Markdown 링크가 전체 답변 완료 전에 활성화된다.
- 아직 전송 중인 Markdown 링크와 일반 URL은 클릭 가능한 잘린 링크가 되지 않는다.
- 완료 후 렌더 결과가 현재 최종 렌더 결과와 동일하다.
- 중지와 오류 경로에서 부분 답변 및 URL 원문이 손실되지 않는다.
- 링크는 HTTP(S)만 허용하고 기존 보안 속성을 유지한다.
- 유사 도메인이 알려진 출처 라벨로 오분류되지 않는다.
- 빠른 delta에서도 렌더는 프레임당 최대 한 번 실행된다.
- 정상 완료, 오류, 중지 후 `aria-busy`가 남지 않는다.
- 기존 Python 테스트와 브라우저 시나리오가 모두 통과한다.
- 운영 배포 후 새 JavaScript 캐시 키, 실시간 링크 전환, 콘솔 무오류를 공개 URL에서 확인한다.

## 10. 구현 순서와 예상 변경 규모

1. 순수 링크 꼬리 검출 함수와 스트리밍 렌더러 추가
2. `sendChat()` 원문 버퍼 및 frame 스케줄러 적용
3. 완료, 오류, 중지 정리 경로 수정
4. 정적 자산 캐시 키와 통합 테스트 갱신
5. 로컬 자동 테스트
6. 브라우저 분할-delta 시나리오 및 실제 채팅 검증
7. QA 기록, 커밋, 운영 데이터 백업, 배포, 공개 URL 재검증

예상 변경은 프런트엔드 JavaScript 약 40~70행, HTML 캐시 키 1행, 통합 테스트 수 행이다. 외부 의존성 추가와 데이터 마이그레이션은 없다.
