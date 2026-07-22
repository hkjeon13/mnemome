const conversationStorageKey = "mnemome_active_conversation_id";

function createConversationId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `chat_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 12)}`;
}

function readActiveConversationId() {
  try {
    const stored = window.sessionStorage.getItem(conversationStorageKey);
    return stored || createConversationId();
  } catch {
    return createConversationId();
  }
}

function setActiveConversationId(conversationId) {
  state.conversationId = conversationId || createConversationId();
  try { window.sessionStorage.setItem(conversationStorageKey, state.conversationId); }
  catch { /* Session storage can be unavailable in privacy modes. */ }
}

const state = {
  memories: [],
  kind: "",
  query: "",
  busy: false,
  clearableCount: 0,
  selectedMemoryId: null,
  abortController: null,
  isComposing: false,
  submitAfterComposition: false,
  culturalSnapshot: null,
  culturalArtifacts: [],
  importSourceType: "local",
  importRows: [],
  importPreparationId: null,
  importPreviewFresh: false,
  importProcessingAllowed: false,
  importBusy: false,
  importJobs: [],
  importJobPollTimer: null,
  importJobsInitialized: false,
  conversationId: readActiveConversationId(),
};

const elements = {
  memoryList: document.querySelector("#memory-list"),
  memorySearch: document.querySelector("#memory-search"),
  memorySearchField: document.querySelector(".search-field"),
  filterTabs: document.querySelector(".filter-tabs"),
  sidebarTabs: document.querySelector(".sidebar-tabs"),
  memoryView: document.querySelector("#memory-view"),
  cultureView: document.querySelector("#culture-view"),
  cultureList: document.querySelector("#culture-list"),
  cultureSnapshotMeta: document.querySelector("#culture-snapshot-meta"),
  traceTab: document.querySelector("#trace-view-tab"),
  memoryDialog: document.querySelector("#memory-dialog"),
  newConversationDialog: document.querySelector("#new-conversation-dialog"),
  clearMemoriesDialog: document.querySelector("#clear-memories-dialog"),
  clearMemoryCount: document.querySelector("#clearable-memory-total"),
  openMemoryForm: document.querySelector("#open-memory-form"),
  openNewConversation: document.querySelector("#open-new-conversation"),
  clearSessionMemories: document.querySelector("#clear-session-memories"),
  importJobQueue: document.querySelector("#import-job-queue"),
  importJobList: document.querySelector("#import-job-list"),
  importJobCount: document.querySelector("#import-job-count"),
  toggleMemoryPanel: document.querySelector("#toggle-memory-panel"),
  memoryPanel: document.querySelector(".memory-panel"),
  workspace: document.querySelector(".workspace"),
  memoryForm: document.querySelector("#memory-form"),
  memoryKind: document.querySelector("#memory-kind"),
  memoryContent: document.querySelector("#memory-content"),
  memoryTags: document.querySelector("#memory-tags"),
  chatForm: document.querySelector("#chat-form"),
  chatInput: document.querySelector("#chat-input"),
  conversation: document.querySelector("#conversation"),
  starterPrompts: document.querySelector("#starter-prompts"),
  sendButton: document.querySelector(".send-button"),
  traceSection: document.querySelector("#trace-section"),
  traceRunId: document.querySelector("#trace-run-id"),
  traceSummary: document.querySelector("#trace-summary"),
  executionSteps: document.querySelector("#execution-steps"),
  memoryRoutes: document.querySelector("#memory-routes"),
  importDialog: document.querySelector("#import-dialog"),
  openImportStudio: document.querySelector("#open-import-studio"),
  closeImportStudio: document.querySelector("#close-import-studio"),
  importSourceTabs: document.querySelector(".import-source-tabs"),
  importFile: document.querySelector("#import-file"),
  importFileLabel: document.querySelector("#import-file-label"),
  importHfRepo: document.querySelector("#import-hf-repo"),
  importHfConfig: document.querySelector("#import-hf-config"),
  importHfSplit: document.querySelector("#import-hf-split"),
  importHfToken: document.querySelector("#import-hf-token"),
  importInstructions: document.querySelector("#import-instructions"),
  analyzeImportSource: document.querySelector("#analyze-import-source"),
  importProfile: document.querySelector("#import-profile"),
  importLayout: document.querySelector("#import-layout"),
  importConfidence: document.querySelector("#import-confidence"),
  importSessionKey: document.querySelector("#import-session-key"),
  importOrderKey: document.querySelector("#import-order-key"),
  importTotalRows: document.querySelector("#import-total-rows"),
  importSourceSummary: document.querySelector("#import-source-summary"),
  importOriginal: document.querySelector("#import-original code"),
  importCode: document.querySelector("#import-code"),
  importResult: document.querySelector("#import-result code"),
  originalMeta: document.querySelector("#original-meta"),
  codeMeta: document.querySelector("#code-meta"),
  resultMeta: document.querySelector("#result-meta"),
  importNotices: document.querySelector("#import-notices"),
  importProgress: document.querySelector("#import-progress"),
  importProgressTitle: document.querySelector("#import-progress-title"),
  importProgressDetail: document.querySelector("#import-progress-detail"),
  runImportPreview: document.querySelector("#run-import-preview"),
  processImport: document.querySelector("#process-import"),
  toast: document.querySelector("#toast"),
};

const initialGuideMessage = elements.conversation.querySelector(".assistant-message");
const compactLayoutQuery = window.matchMedia("(max-width: 56.25rem)");

const kindLabels = {
  fact: "FACT · 사실",
  preference: "PREFERENCE · 선호",
  episode: "EPISODE · 경험",
  conversation: "CONVERSATION · 세션",
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "요청을 처리하지 못했습니다.");
  return payload;
}

async function streamApi(path, options, onEvent) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "응답 스트림을 시작하지 못했습니다.");
  }
  if (!response.body) throw new Error("이 브라우저에서 응답 스트림을 읽을 수 없습니다.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary).replaceAll("\r", "");
      buffer = buffer.slice(boundary + 2);
      let event = "message";
      const data = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) onEvent(event, JSON.parse(data.join("\n")));
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
}

function showToast(message, type = "success") {
  elements.toast.textContent = message;
  elements.toast.className = `toast show ${type === "error" ? "error" : ""}`;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => { elements.toast.className = "toast"; }, 2800);
}

function formatDate(value) {
  try { return new Intl.DateTimeFormat("ko", { month: "short", day: "numeric" }).format(new Date(value)); }
  catch { return ""; }
}

function renderMemories() {
  const search = state.query.trim().toLowerCase();
  const filtered = state.memories.filter((memory) => {
    const matchesKind = !state.kind || memory.kind === state.kind;
    const turnText = (memory.conversation?.turns || []).map((turn) => turn.content).join(" ");
    const haystack = `${memory.content} ${turnText} ${(memory.tags || []).join(" ")}`.toLowerCase();
    return matchesKind && (!search || haystack.includes(search));
  });

  elements.memoryList.replaceChildren();
  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = search ? "검색 조건에 맞는 기억이 없습니다." : "아직 저장된 기억이 없습니다.";
    elements.memoryList.append(empty);
    return;
  }

  for (const memory of filtered) {
    const card = document.createElement("article");
    const selected = memory.id === state.selectedMemoryId;
    card.className = `memory-card${memory.is_seed ? " seeded" : ""}${memory.is_imported ? " imported" : ""}${selected ? " selected" : ""}`;
    card.dataset.memoryId = memory.id;

    if (memory.kind === "conversation") {
      card.classList.add("conversation-memory");
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-pressed", String(selected));
      card.setAttribute("aria-label", `대화 세션 열기: ${memory.conversation?.query || memory.content}`);
      card.addEventListener("click", () => openConversationMemory(memory));
      card.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        openConversationMemory(memory);
      });
    }

    const type = document.createElement("span");
    type.className = `memory-type ${memory.kind}`;
    type.textContent = kindLabels[memory.kind] || memory.kind;

    const content = document.createElement("p");
    const displayContent = memory.kind === "conversation"
      ? (memory.conversation?.query || memory.content)
      : memory.content;
    content.textContent = displayContent;
    content.title = displayContent;

    const tags = document.createElement("div");
    tags.className = "tag-row";
    const sessionTurnLabel = memory.kind === "conversation" && memory.conversation?.turn_count
      ? `${memory.conversation.turn_count}턴`
      : "";
    for (const tag of [sessionTurnLabel, ...(memory.tags || []).slice(0, 3), formatDate(memory.created_at)]) {
      if (!tag) continue;
      const chip = document.createElement("span");
      chip.textContent = tag;
      tags.append(chip);
    }

    if (memory.is_seed) {
      const seed = document.createElement("span");
      seed.className = "seed-badge";
      seed.textContent = "기본 샘플";
      card.append(type, content, tags, seed);
    } else if (memory.is_imported) {
      const imported = document.createElement("span");
      imported.className = "seed-badge import-badge";
      imported.textContent = "가져오기";
      imported.title = "Processing 작업의 … 메뉴에서 관련 메모리와 함께 삭제할 수 있습니다.";
      card.append(type, content, tags, imported);
    } else {
      const remove = document.createElement("button");
      remove.className = "delete-memory";
      remove.type = "button";
      remove.title = "기억 비활성화";
      remove.setAttribute("aria-label", "기억 비활성화");
      remove.textContent = "×";
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteMemory(memory.id);
      });
      card.append(type, content, tags, remove);
    }
    elements.memoryList.append(card);
  }
}

async function loadMemories() {
  const payload = await api("/demo/api/memories");
  state.memories = payload.items;
  state.clearableCount = payload.clearable_count || 0;
  elements.clearSessionMemories.disabled = state.clearableCount === 0;
  elements.clearSessionMemories.title = state.clearableCount
    ? `현재 세션의 사용자 기억 ${state.clearableCount}개 비우기`
    : "비울 사용자 기억이 없습니다";
  renderMemories();
}

function renderCulturalMemory() {
  elements.cultureList.replaceChildren();
  const snapshot = state.culturalSnapshot;
  if (!snapshot) {
    elements.cultureSnapshotMeta.textContent = "게시된 문화적 스냅샷이 없습니다.";
    return;
  }
  elements.cultureSnapshotMeta.textContent =
    `Snapshot v${snapshot.version} · ${snapshot.scope} · ${snapshot.policy_version}`;
  for (const artifact of state.culturalArtifacts) {
    const card = document.createElement("article");
    card.className = "culture-card";
    const label = document.createElement("span");
    label.className = "culture-card-label";
    label.textContent = `CULTURE · v${artifact.version}`;
    const claim = document.createElement("p");
    claim.className = "culture-claim";
    claim.textContent = artifact.claim;
    card.append(label, claim);
    if (artifact.conditions?.length) {
      const condition = document.createElement("p");
      condition.className = "culture-detail";
      condition.textContent = `적용 조건 · ${artifact.conditions.join(" · ")}`;
      card.append(condition);
    }
    if (artifact.restrictions?.length) {
      const restriction = document.createElement("p");
      restriction.className = "culture-detail restriction";
      restriction.textContent = `주의 · ${artifact.restrictions.join(" · ")}`;
      card.append(restriction);
    }
    elements.cultureList.append(card);
  }
}

async function loadCulturalMemory() {
  const payload = await api("/demo/api/cultural-snapshot");
  state.culturalSnapshot = payload.snapshot;
  state.culturalArtifacts = payload.items || [];
  renderCulturalMemory();
}

async function createMemory() {
  const content = elements.memoryContent.value.trim();
  if (!content) return;
  const tags = elements.memoryTags.value.split(",").map((tag) => tag.trim()).filter(Boolean).slice(0, 10);
  await api("/demo/api/memories", {
    method: "POST",
    body: JSON.stringify({ content, kind: elements.memoryKind.value, tags }),
  });
  elements.memoryForm.reset();
  elements.memoryDialog.close();
  await loadMemories();
  showToast("새 기억을 저장했습니다.");
}

async function deleteMemory(memoryId) {
  try {
    await api(`/demo/api/memories/${encodeURIComponent(memoryId)}`, { method: "DELETE" });
    await loadMemories();
    showToast("기억을 비활성화했습니다.");
  } catch (error) { showToast(error.message, "error"); }
}

async function clearSessionMemories() {
  if (!state.clearableCount) return;
  elements.clearSessionMemories.disabled = true;
  try {
    const result = await api("/demo/api/memories", { method: "DELETE" });
    await loadMemories();
    showToast(`사용자 기억 ${result.cleared}개를 비웠습니다. 기본 샘플과 가져온 데이터는 유지됩니다.`);
  } catch (error) {
    showToast(error.message, "error");
    elements.clearSessionMemories.disabled = false;
  }
}

function appendMessage(role, text, meta = []) {
  const message = document.createElement("div");
  message.className = `message ${role === "user" ? "user-message" : "assistant-message"}`;
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "U" : "M";
  const body = document.createElement("div");
  body.className = "message-body";
  const author = document.createElement("span");
  author.className = "message-author";
  author.textContent = role === "user" ? "You" : "Mnemome Guide";
  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  body.append(author, paragraph);
  message.append(avatar, body);
  elements.conversation.append(message);
  if (meta.length) appendMessageMeta(message, meta);
  elements.conversation.scrollTop = elements.conversation.scrollHeight;
  return message;
}

function appendMessageMeta(message, meta) {
  const body = message.querySelector(".message-body");
  const metadata = document.createElement("div");
  metadata.className = "message-meta";
  for (const item of meta) {
    const chip = document.createElement("span");
    chip.textContent = item;
    metadata.append(chip);
  }
  body.append(metadata);
}

function clearRenderedConversation() {
  for (const child of [...elements.conversation.children]) {
    if (child !== initialGuideMessage && child !== elements.starterPrompts) child.remove();
  }
}

function openConversationMemory(memory, { announce = true } = {}) {
  if (memory.kind !== "conversation") return;
  stopChat();
  clearRenderedConversation();
  initialGuideMessage.hidden = true;
  elements.starterPrompts.hidden = true;
  const turns = memory.conversation?.turns || [];
  if (turns.length) {
    for (const turn of turns) {
      const message = appendMessage(turn.role, turn.content);
      if (turn.role === "assistant") renderAnswerMarkdown(message.querySelector("p"), turn.content);
    }
  } else {
    const query = memory.conversation?.query?.trim();
    if (query) appendMessage("user", query);
    const answer = memory.conversation?.answer || memory.content;
    const responseMessage = appendMessage("assistant", answer);
    renderAnswerMarkdown(responseMessage.querySelector("p"), answer);
  }
  if (memory.conversation?.is_live_session && memory.conversation?.session_id) {
    setActiveConversationId(memory.conversation.session_id);
  } else {
    setActiveConversationId(createConversationId());
  }
  state.selectedMemoryId = memory.id;
  renderMemories();
  elements.chatInput.value = "";
  elements.conversation.scrollTop = 0;
  if (announce) showToast("대화 세션을 불러왔습니다.");
}

function sourceLabel(urlValue) {
  try {
    const host = new URL(urlValue).hostname.replace(/^www\./, "");
    const knownSources = [
      ["nvidia.com", "NVIDIA 보도자료"],
      ["reuters.com", "Reuters 기사"],
      ["bloomberg.com", "Bloomberg 기사"],
      ["yna.co.kr", "연합뉴스 기사"],
      ["news.naver.com", "네이버 뉴스"],
    ];
    const knownSource = knownSources.find(
      ([domain]) => host === domain || host.endsWith(`.${domain}`),
    );
    return knownSource?.[1] || `${host} 출처`;
  } catch {
    return "출처 보기";
  }
}

function appendInlineMarkdown(element, text) {
  const pattern = /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>()]+)|\*\*([^\n]+?)\*\*|<br\s*\/?>/gi;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    element.append(document.createTextNode(text.slice(cursor, match.index)));
    if (match[0].toLowerCase().startsWith("<br")) {
      element.append(document.createElement("br"));
    } else if (match[4] !== undefined) {
      const strong = document.createElement("strong");
      strong.textContent = match[4];
      element.append(strong);
    } else {
      const url = match[2] || match[3];
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = match[1] || sourceLabel(url);
      element.append(link);
    }
    cursor = match.index + match[0].length;
  }
  element.append(document.createTextNode(text.slice(cursor)));
}

function markdownTableCells(line) {
  let source = line.trim();
  if (!source.includes("|")) return null;
  if (source.startsWith("|")) source = source.slice(1);
  if (source.endsWith("|") && !source.endsWith("\\|")) source = source.slice(0, -1);
  const cells = [];
  let cell = "";
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (character === "\\" && source[index + 1] === "|") {
      cell += "|";
      index += 1;
    } else if (character === "|") {
      cells.push(cell.trim());
      cell = "";
    } else {
      cell += character;
    }
  }
  cells.push(cell.trim());
  return cells.length >= 2 ? cells : null;
}

function markdownTableAlignment(cell) {
  const value = cell.trim();
  if (!/^:?-{3,}:?$/.test(value)) return null;
  if (value.startsWith(":") && value.endsWith(":")) return "center";
  if (value.endsWith(":")) return "right";
  return "left";
}

function markdownTableAt(lines, start) {
  const headers = markdownTableCells(lines[start] || "");
  const delimiters = markdownTableCells(lines[start + 1] || "");
  if (!headers || !delimiters || headers.length !== delimiters.length) return null;
  const alignments = delimiters.map(markdownTableAlignment);
  if (alignments.some((alignment) => alignment === null)) return null;
  const rows = [];
  let next = start + 2;
  while (next < lines.length) {
    const row = markdownTableCells(lines[next]);
    if (!row) break;
    rows.push(headers.map((_, index) => row[index] || ""));
    next += 1;
  }
  return { headers, alignments, rows, next };
}

function appendMarkdownTable(element, parsed) {
  const wrapper = document.createElement("span");
  wrapper.className = "markdown-table-wrap";
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headerRow = document.createElement("tr");
  parsed.headers.forEach((content, index) => {
    const cell = document.createElement("th");
    cell.style.textAlign = parsed.alignments[index];
    appendInlineMarkdown(cell, content);
    headerRow.append(cell);
  });
  head.append(headerRow);
  const body = document.createElement("tbody");
  for (const row of parsed.rows) {
    const tableRow = document.createElement("tr");
    row.forEach((content, index) => {
      const cell = document.createElement("td");
      cell.style.textAlign = parsed.alignments[index];
      appendInlineMarkdown(cell, content);
      tableRow.append(cell);
    });
    body.append(tableRow);
  }
  table.append(head, body);
  wrapper.append(table);
  element.append(wrapper);
}

function renderAnswerMarkdown(element, text) {
  element.replaceChildren();
  element.className = "markdown-rendered";
  const lines = text.split("\n");
  let textBuffer = [];
  const flushText = () => {
    if (!textBuffer.length) return;
    const prose = document.createElement("span");
    prose.className = "markdown-text";
    appendInlineMarkdown(prose, textBuffer.join("\n"));
    element.append(prose);
    textBuffer = [];
  };
  for (let index = 0; index < lines.length;) {
    const table = markdownTableAt(lines, index);
    if (!table) {
      textBuffer.push(lines[index]);
      index += 1;
      continue;
    }
    flushText();
    appendMarkdownTable(element, table);
    index = table.next;
  }
  flushText();
}

function pendingStreamingMarkdownStart(text) {
  const markdownTail = text.match(/\[[^\]\n]*\]\(https?:\/\/[^\s)]*$/);
  const bareUrlTail = text.match(/https?:\/\/[^\s<>()]*$/);
  let strongTailStart = null;
  for (const match of text.matchAll(/\*\*/g)) {
    strongTailStart = strongTailStart === null ? match.index : null;
  }
  const starts = [markdownTail, bareUrlTail]
    .filter(Boolean)
    .map((match) => match.index);
  if (strongTailStart !== null) starts.push(strongTailStart);
  return starts.length ? Math.min(...starts) : text.length;
}

function renderStreamingAnswerMarkdown(element, text) {
  const pendingStart = pendingStreamingMarkdownStart(text);
  renderAnswerMarkdown(element, text.slice(0, pendingStart));
  element.append(document.createTextNode(text.slice(pendingStart)));
}

function planStepKey(step) {
  return step.index === null || step.index === undefined ? step.title : String(step.index);
}

function renderPlanProgress(element, steps, stepStatuses) {
  element.replaceChildren();
  element.removeAttribute("aria-label");
  element.className = "plan-progress";
  const heading = document.createElement("strong");
  heading.textContent = "진행 계획";
  const list = document.createElement("ol");
  for (const step of steps) {
    const item = document.createElement("li");
    const status = stepStatuses.get(planStepKey(step)) || "pending";
    item.classList.add(status);
    const marker = document.createElement("span");
    marker.className = "step-indicator";
    if (status === "complete") {
      marker.setAttribute("aria-label", "완료");
      marker.textContent = "✓";
    } else {
      marker.setAttribute("aria-label", status === "running" ? "진행 중" : "시작 대기 중");
      marker.append(document.createElement("i"));
    }
    const title = document.createElement("span");
    title.textContent = step.title;
    item.append(marker, title);
    list.append(item);
  }
  element.append(heading, list);
}

function compactMs(value) {
  if (value === null || value === undefined) return "";
  return `${Number(value).toLocaleString("ko-KR")} ms`;
}

function renderExecutionStep(stage, title, detail, status = "ok") {
  const row = document.createElement("div");
  row.className = `execution-step ${status === "ok" ? "complete" : status}`;
  const marker = document.createElement("span");
  marker.className = "step-marker";
  marker.textContent = stage;
  const copy = document.createElement("div");
  const heading = document.createElement("strong");
  heading.textContent = title;
  const metadata = document.createElement("span");
  metadata.textContent = detail;
  copy.append(heading, metadata);
  const state = document.createElement("span");
  state.className = "step-state";
  state.textContent = status === "ok" ? "완료" : status;
  row.append(marker, copy, state);
  elements.executionSteps.append(row);
}

function renderAgentTrace(result) {
  const trace = result.execution_trace || {};
  const plan = trace.plan || {};
  const steps = Array.isArray(trace.steps) ? trace.steps : [];
  const mcp = result.mcp || {};
  elements.traceRunId.textContent = result.run_id;
  const mcpSummary = mcp.status === "connected" ? ` · ${mcp.tool_count} MCP tools` : " · MCP unavailable";
  elements.traceSummary.textContent = `${steps.length} steps · ${trace.llm_calls || 0} LLM calls${mcpSummary} · ${compactMs(trace.total_latency_ms || result.elapsed_ms)}`;
  elements.executionSteps.replaceChildren();
  renderExecutionStep(
    "MCP",
    "내부 도구 브리지",
    mcp.status === "connected" ? `${mcp.tool_count}개 안전 도구 연결` : "메모리 전용 fallback",
    mcp.status === "connected" ? "ok" : mcp.status || "unavailable",
  );
  renderExecutionStep(
    "PLAN",
    plan.title || "Direct response",
    `${plan.step_count || steps.length}개 step · ${compactMs(plan.latency_ms)}`,
    plan.status || "ok",
  );
  for (const step of steps) {
    const tool = step.tool && step.tool !== "None" ? step.tool : "final_answer";
    renderExecutionStep(
      String(step.index).padStart(2, "0"),
      step.title,
      `${tool}${step.latency_ms === null || step.latency_ms === undefined ? "" : ` · ${compactMs(step.latency_ms)}`}`,
      step.status || "ok",
    );
  }

  const memoryTrace = result.memory_trace || {};
  const routes = [
    ["LTM", memoryTrace.long_term],
    ["STM", memoryTrace.short_term],
    ["CULTURE", memoryTrace.cultural],
  ];
  elements.memoryRoutes.replaceChildren();
  for (const [code, route] of routes) {
    if (!route) continue;
    const card = document.createElement("div");
    card.className = `memory-route ${route.status}`;
    const badge = document.createElement("span");
    badge.className = "route-code";
    badge.textContent = code;
    const copy = document.createElement("div");
    const heading = document.createElement("strong");
    heading.textContent = route.label;
    const detail = document.createElement("p");
    detail.textContent = route.detail;
    const extra = document.createElement("span");
    if (code === "LTM") {
      const kinds = (route.kinds || []).join(" · ") || "관련 기억 없음";
      extra.textContent = `${route.count}개 회상 · ${kinds}${route.retriever ? ` · ${route.retriever}` : ""}`;
    }
    else if (code === "STM") extra.textContent = `${route.count}개 문맥 · 현재 run 한정`;
    else extra.textContent = route.status === "applied" ? route.snapshot_id : "미적용 · provider 없음";
    copy.append(heading, detail, extra);
    const state = document.createElement("span");
    state.className = "route-state";
    state.textContent = route.status === "applied" ? "적용됨" : route.status === "empty" ? "비어 있음" : "미적용";
    card.append(badge, copy, state);
    elements.memoryRoutes.append(card);
  }
  elements.traceSection.classList.add("has-run");
  elements.traceTab.hidden = false;
  showSidebarView("trace");
  elements.traceSection.scrollTop = 0;
}

function setChatBusy(busy) {
  state.busy = busy;
  elements.sendButton.classList.toggle("is-stopping", busy);
  elements.sendButton.setAttribute("aria-label", busy ? "응답 생성 중지" : "질문 보내기");
  elements.sendButton.title = busy ? "응답 생성 중지" : "질문 보내기";
}

function stopChat() {
  if (!state.busy || !state.abortController) return;
  state.abortController.abort();
}

function traceEmpty(message) {
  const empty = document.createElement("div");
  empty.className = "trace-empty";
  empty.textContent = message;
  return empty;
}

function startNewConversation({ focusInput = true } = {}) {
  stopChat();
  clearRenderedConversation();
  initialGuideMessage.hidden = false;
  elements.starterPrompts.hidden = false;
  state.selectedMemoryId = null;
  setActiveConversationId(createConversationId());
  renderMemories();
  elements.conversation.scrollTop = 0;
  elements.chatInput.value = "";
  elements.traceRunId.textContent = "실행 대기 중";
  elements.traceSummary.textContent = "아직 실행 기록이 없습니다";
  elements.executionSteps.replaceChildren(traceEmpty("질문을 보내면 실제 plan 제목과 중간 step이 표시됩니다."));
  elements.memoryRoutes.replaceChildren(traceEmpty("실행 후 각 메모리 계층의 적용 여부와 범위를 표시합니다."));
  elements.traceSection.classList.remove("has-run");
  elements.traceTab.hidden = true;
  showSidebarView("memory");
  showToast("새 대화를 시작했습니다. 저장된 메모리는 유지됩니다.");
  if (focusInput) elements.chatInput.focus({ preventScroll: true });
  else elements.chatInput.blur();
}

async function sendChat(query) {
  if (state.busy || !query.trim()) return;
  const controller = new AbortController();
  state.abortController = controller;
  setChatBusy(true);
  elements.starterPrompts.hidden = true;
  appendMessage("user", query.trim());
  const responseMessage = appendMessage("assistant", "");
  const responseText = responseMessage.querySelector("p");
  responseText.setAttribute("aria-label", "실행 계획 생성 중");
  responseMessage.setAttribute("aria-busy", "true");
  responseMessage.classList.add("typing");
  let receivedDelta = false;
  let streamedAnswer = "";
  let answerRenderFrame = null;
  let result = null;
  let plannedSteps = [];
  let stepStatuses = new Map();
  const cancelAnswerRender = () => {
    if (answerRenderFrame === null) return;
    cancelAnimationFrame(answerRenderFrame);
    answerRenderFrame = null;
  };
  const scheduleAnswerRender = () => {
    if (answerRenderFrame !== null) return;
    answerRenderFrame = requestAnimationFrame(() => {
      answerRenderFrame = null;
      renderStreamingAnswerMarkdown(responseText, streamedAnswer);
      elements.conversation.scrollTop = elements.conversation.scrollHeight;
    });
  };
  try {
    await streamApi(
      "/demo/api/chat/stream",
      {
        method: "POST",
        body: JSON.stringify({
          query: query.trim(),
          conversation_id: state.conversationId,
        }),
        signal: controller.signal,
      },
      (event, payload) => {
        if (event === "progress") {
          if (payload.kind === "plan" && Array.isArray(payload.steps)) {
            plannedSteps = payload.steps;
            stepStatuses = new Map();
            responseMessage.classList.remove("typing");
            renderPlanProgress(responseText, plannedSteps, stepStatuses);
          } else if (payload.kind === "step_start" && plannedSteps.length) {
            for (const [key, status] of stepStatuses) {
              if (status === "running") stepStatuses.set(key, "complete");
            }
            const step = plannedSteps.find((candidate) =>
              (payload.index !== null && payload.index !== undefined &&
                String(candidate.index) === String(payload.index)) ||
              (payload.title && candidate.title === payload.title));
            if (step) stepStatuses.set(planStepKey(step), "running");
            renderPlanProgress(responseText, plannedSteps, stepStatuses);
          } else if (payload.kind === "step_complete" && plannedSteps.length) {
            const step = plannedSteps.find((candidate) =>
              String(candidate.index) === String(payload.index));
            if (step) stepStatuses.set(planStepKey(step), "complete");
            renderPlanProgress(responseText, plannedSteps, stepStatuses);
          }
          elements.conversation.scrollTop = elements.conversation.scrollHeight;
        } else if (event === "delta") {
          const delta = payload.delta || "";
          if (!delta) return;
          if (!receivedDelta) {
            receivedDelta = true;
            responseText.textContent = "";
            responseText.removeAttribute("aria-label");
            responseText.className = "";
            responseMessage.classList.remove("typing");
          }
          streamedAnswer += delta;
          scheduleAnswerRender();
        } else if (event === "complete") result = payload;
        else if (event === "error") throw new Error(payload.message || "응답 스트림이 중단되었습니다.");
      },
    );
    if (!result) throw new Error("완료되지 않은 응답 스트림입니다.");
    cancelAnswerRender();
    responseMessage.classList.remove("typing");
    renderAnswerMarkdown(responseText, result.answer || streamedAnswer);
    responseMessage.removeAttribute("aria-busy");
    appendMessageMeta(responseMessage, [
      `recall ${result.recalled.length}`,
      `${result.elapsed_ms} ms`,
    ]);
    if (result.conversation_id) setActiveConversationId(result.conversation_id);
    state.selectedMemoryId = result.conversation_memory_id || state.selectedMemoryId;
    renderAgentTrace(result);
    await loadMemories();
  } catch (error) {
    cancelAnswerRender();
    responseMessage.classList.remove("typing");
    responseText.removeAttribute("aria-label");
    responseText.className = "";
    const errorText = error.name === "AbortError"
      ? "응답 생성을 중지했습니다."
      : `실행 중 문제가 발생했습니다: ${error.message}`;
    const partialAnswer = receivedDelta ? `${streamedAnswer}\n\n${errorText}` : errorText;
    renderAnswerMarkdown(responseText, partialAnswer);
    responseMessage.removeAttribute("aria-busy");
  } finally {
    cancelAnswerRender();
    responseMessage.removeAttribute("aria-busy");
    state.abortController = null;
    setChatBusy(false);
    elements.chatInput.focus({ preventScroll: true });
  }
}

const importLayoutLabels = {
  SESSION_PER_ROW: "1 row = 1 session",
  TURN_PER_ROW: "여러 row = 1 session",
  SESSION_FRAGMENT_PER_ROW: "Session fragments",
  REUSED_SESSION_ID_SUSPECTED: "ID 재사용 의심",
  MIXED_OR_AMBIGUOUS: "구조 확인 필요",
};

function prettyJson(value) {
  const text = JSON.stringify(value, null, 2);
  return text.length > 24000 ? `${text.slice(0, 24000)}\n… preview truncated` : text;
}

function setImportStatus(status, title, detail) {
  elements.importProgress.className = `import-progress${status ? ` ${status}` : ""}`;
  elements.importProgressTitle.textContent = title;
  elements.importProgressDetail.textContent = detail;
}

function setImportBusy(busy) {
  state.importBusy = busy;
  elements.analyzeImportSource.disabled = busy;
  elements.runImportPreview.disabled = busy || !state.importPreparationId;
  elements.processImport.disabled = busy || !state.importPreviewFresh || !state.importProcessingAllowed;
  elements.closeImportStudio.disabled = busy;
}

function invalidateImportPreview(message = "code가 변경되었습니다. preview를 다시 실행해 주세요.") {
  state.importPreviewFresh = false;
  elements.processImport.disabled = true;
  if (state.importPreparationId) setImportStatus("", "Preview 확인 필요", message);
}

function renderImportWarnings(warnings = []) {
  elements.importNotices.replaceChildren();
  elements.importNotices.hidden = !warnings.length;
  for (const warning of warnings) {
    const item = document.createElement("span");
    item.textContent = warning;
    elements.importNotices.append(item);
  }
}

function renderImportPayload(payload) {
  state.importPreparationId = payload.preparation_id;
  state.importPreviewFresh = true;
  state.importProcessingAllowed = Boolean(payload.processing_allowed);
  const profile = payload.profile || {};
  const stats = payload.stats || {};
  const source = payload.source || {};
  const totalRows = Number(source.total_rows || 0);
  const previewRows = Number(stats.input_rows || 0);

  elements.importProfile.hidden = false;
  elements.importLayout.textContent = importLayoutLabels[profile.layout] || profile.layout || "—";
  elements.importConfidence.textContent = `${Math.round((profile.confidence || 0) * 100)}%`;
  elements.importSessionKey.textContent = profile.session_field || "row index";
  elements.importOrderKey.textContent = profile.order_field || "source order";
  elements.importTotalRows.textContent = `${totalRows.toLocaleString("ko-KR")} rows`;
  elements.importSourceSummary.textContent = source.label || source.type || "source";
  elements.importOriginal.textContent = prettyJson(payload.original || []);
  elements.importCode.value = payload.code || "";
  elements.importCode.disabled = false;
  elements.importResult.textContent = prettyJson(payload.result || []);
  elements.originalMeta.textContent = `${previewRows.toLocaleString("ko-KR")} / ${totalRows.toLocaleString("ko-KR")} rows`;
  elements.codeMeta.textContent = `${payload.generator || "generated"} · ${payload.code_digest || ""}`;
  elements.resultMeta.textContent = `${stats.sessions || 0} sessions · ${stats.turns || 0} turns`;
  renderImportWarnings(payload.warnings || []);

  const canProcess = state.importProcessingAllowed;
  setImportStatus(
    canProcess ? "ready" : "error",
    canProcess ? "Preview 준비 완료" : "Processing 전 확인이 필요합니다",
    canProcess
      ? `전체 ${totalRows.toLocaleString("ko-KR")} rows 중 ${previewRows.toLocaleString("ko-KR")} rows를 미리봤습니다 · ${stats.sessions || 0} sessions`
      : (payload.warnings || ["전체 처리 조건을 충족하지 못했습니다."]).at(-1),
  );
  setImportBusy(false);
}

async function parseImportFile(file) {
  if (!file) return [];
  if (file.size > 5 * 1024 * 1024) throw new Error("데모에서는 5MB 이하 JSON/JSONL 파일을 지원합니다.");
  const text = await file.text();
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    const rows = text.split(/\r?\n/).filter((line) => line.trim());
    try { parsed = rows.map((line) => JSON.parse(line)); }
    catch { throw new Error("JSON 또는 JSONL 형식을 확인해 주세요."); }
  }
  if (Array.isArray(parsed)) return parsed;
  if (Array.isArray(parsed.rows)) return parsed.rows;
  if (Array.isArray(parsed.sessions)) return parsed.sessions;
  if (Array.isArray(parsed.conversations)) return parsed.conversations;
  if (parsed && typeof parsed === "object") return [parsed];
  throw new Error("최상위 JSON은 object 또는 row 배열이어야 합니다.");
}

function resetImportPreparation() {
  state.importPreparationId = null;
  state.importPreviewFresh = false;
  state.importProcessingAllowed = false;
  elements.importProfile.hidden = true;
  elements.importCode.disabled = true;
  elements.runImportPreview.disabled = true;
  elements.processImport.disabled = true;
  renderImportWarnings([]);
  setImportStatus("", "Source를 분석해 주세요", "preview에서는 메모리를 저장하지 않습니다.");
}

async function prepareImport() {
  try {
    let source;
    if (state.importSourceType === "local") {
      if (!state.importRows.length) throw new Error("먼저 JSON 또는 JSONL 파일을 선택해 주세요.");
      if (state.importRows.length > 2000) throw new Error("데모에서는 local row 2,000개까지 처리합니다.");
      source = {
        type: "local",
        file_name: elements.importFile.files[0]?.name || "local.json",
      };
    } else {
      if (!elements.importHfRepo.value.trim()) throw new Error("Hugging Face Dataset ID를 입력해 주세요.");
      source = {
        type: "huggingface",
        repo_id: elements.importHfRepo.value.trim(),
        config: elements.importHfConfig.value.trim() || "default",
        split: elements.importHfSplit.value.trim() || "train",
        token: elements.importHfToken.value.trim() || null,
      };
    }
    setImportBusy(true);
    setImportStatus("busy", "샘플 구조를 분석하는 중", "layout을 판별하고 transform code를 생성합니다.");
    const payload = await api("/demo/api/imports/prepare", {
      method: "POST",
      body: JSON.stringify({
        source,
        rows: state.importSourceType === "local" ? state.importRows : [],
        instructions: elements.importInstructions.value.trim(),
        sample_size: 5,
      }),
    });
    elements.importHfToken.value = "";
    renderImportPayload(payload);
  } catch (error) {
    setImportBusy(false);
    setImportStatus("error", "샘플 분석 실패", error.message);
    showToast(error.message, "error");
  }
}

async function runImportPreview() {
  if (!state.importPreparationId) return;
  setImportBusy(true);
  setImportStatus("busy", "수정한 code를 실행하는 중", "sample row만 안전한 제한형 evaluator로 처리합니다.");
  try {
    const payload = await api(`/demo/api/imports/${encodeURIComponent(state.importPreparationId)}/preview`, {
      method: "POST",
      body: JSON.stringify({ code: elements.importCode.value, sample_size: 5 }),
    });
    renderImportPayload(payload);
  } catch (error) {
    setImportBusy(false);
    state.importPreviewFresh = false;
    setImportStatus("error", "Preview 실행 실패", error.message);
    showToast(error.message, "error");
  }
}

function setImportJobIndicator(jobs = []) {
  const activeJobs = jobs.filter((job) => ["QUEUED", "RUNNING", "PAUSED"].includes(job.status));
  const active = activeJobs.some((job) => job.status !== "PAUSED");
  elements.openImportStudio.classList.toggle("is-processing", active);
  const label = activeJobs.length
    ? `대화 데이터 작업 ${activeJobs.length}개 진행 중`
    : "대화 데이터 가져오기";
  elements.openImportStudio.setAttribute("aria-label", label);
  elements.openImportStudio.setAttribute("title", label);
}

const importJobStatusLabels = {
  QUEUED: "대기",
  RUNNING: "처리 중",
  PAUSED: "일시정지",
  COMPLETED: "완료",
  FAILED: "실패",
  CANCELLED: "삭제 중",
  INTERRUPTED: "중단됨",
};

function importJobCountText(job) {
  const counts = job.memory_counts || {};
  const parts = [
    ["대화", counts.conversation],
    ["사실", counts.fact],
    ["선호", counts.preference],
    ["경험", counts.episode],
    ["문화", counts.culture],
  ].filter(([, count]) => Number(count || 0) > 0);
  return parts.length ? parts.map(([label, count]) => `${label} ${count}`).join(" · ") : "메모리 분석 대기";
}

function closeImportJobMenus(except = null) {
  for (const card of elements.importJobList.querySelectorAll(".import-job-card.menu-open")) {
    if (card !== except) card.classList.remove("menu-open");
  }
}

function renderImportJobs() {
  elements.importJobList.replaceChildren();
  elements.importJobQueue.hidden = state.importJobs.length === 0;
  elements.importJobCount.textContent = `${state.importJobs.length}`;
  setImportJobIndicator(state.importJobs);
  for (const job of state.importJobs) {
    const card = document.createElement("article");
    card.className = `import-job-card status-${String(job.status || "").toLowerCase()}`;
    card.dataset.jobId = job.job_id;

    const summary = document.createElement("div");
    summary.className = "import-job-summary";
    const titleWrap = document.createElement("div");
    titleWrap.className = "import-job-title";
    const title = document.createElement("strong");
    title.textContent = job.source_label || "대화 데이터";
    const status = document.createElement("span");
    status.className = "import-job-status";
    status.textContent = importJobStatusLabels[job.status] || job.status;
    titleWrap.append(title, status);

    const more = document.createElement("button");
    more.className = "import-job-more";
    more.type = "button";
    more.dataset.jobMenu = job.job_id;
    more.setAttribute("aria-label", `${title.textContent} 작업 메뉴`);
    more.textContent = "…";
    summary.append(titleWrap, more);

    const stage = document.createElement("div");
    stage.className = "import-job-stage";
    stage.textContent = job.error || job.stage || "대기 중";
    const progress = document.createElement("div");
    progress.className = "import-job-progress";
    const bar = document.createElement("span");
    bar.style.width = `${Math.max(0, Math.min(100, Number(job.progress || 0)))}%`;
    progress.append(bar);
    const meta = document.createElement("div");
    meta.className = "import-job-meta";
    const sessions = job.total_sessions
      ? `${job.completed_sessions}/${job.total_sessions} sessions`
      : `${Number(job.progress || 0)}%`;
    meta.textContent = `${sessions} · ${importJobCountText(job)}`;

    const menu = document.createElement("div");
    menu.className = "import-job-menu";
    if (["QUEUED", "RUNNING"].includes(job.status)) {
      const pause = document.createElement("button");
      pause.type = "button";
      pause.dataset.jobAction = "pause";
      pause.textContent = "일시정지";
      menu.append(pause);
    }
    if (job.status === "PAUSED") {
      const resume = document.createElement("button");
      resume.type = "button";
      resume.dataset.jobAction = "resume";
      resume.textContent = "재개";
      menu.append(resume);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger";
    remove.dataset.jobAction = "delete";
    remove.textContent = "삭제";
    menu.append(remove);
    card.append(summary, stage, progress, meta, menu);
    elements.importJobList.append(card);
  }
}

async function loadImportJobs({ announce = true } = {}) {
  const previous = new Map(state.importJobs.map((job) => [job.job_id, job.status]));
  const previousMemorySnapshot = state.importJobs
    .map((job) => `${job.job_id}:${job.created_memories}`)
    .join("|");
  const previousCultureSnapshot = state.importJobs
    .map((job) => `${job.job_id}:${job.created_cultural_memories}`)
    .join("|");
  const payload = await api("/demo/api/imports/jobs");
  const jobs = payload.items || [];
  const nextMemorySnapshot = jobs
    .map((job) => `${job.job_id}:${job.created_memories}`)
    .join("|");
  const nextCultureSnapshot = jobs
    .map((job) => `${job.job_id}:${job.created_cultural_memories}`)
    .join("|");
  state.importJobs = jobs;
  renderImportJobs();

  const refreshes = [];
  if (state.importJobsInitialized && nextMemorySnapshot !== previousMemorySnapshot) {
    refreshes.push(loadMemories());
  }
  if (state.importJobsInitialized && nextCultureSnapshot !== previousCultureSnapshot) {
    refreshes.push(loadCulturalMemory());
  }
  if (refreshes.length) await Promise.all(refreshes);
  if (announce && state.importJobsInitialized) {
    for (const job of jobs) {
      const oldStatus = previous.get(job.job_id);
      if (oldStatus && oldStatus !== "COMPLETED" && job.status === "COMPLETED") {
        showToast(`${job.source_label || "대화 데이터"} Processing이 완료되었습니다.`);
      }
      if (oldStatus && oldStatus !== "FAILED" && job.status === "FAILED") {
        showToast(job.error || `${job.source_label || "대화 데이터"} Processing이 실패했습니다.`, "error");
      }
    }
  }
  state.importJobsInitialized = true;
  return jobs;
}

async function pollImportJobs() {
  window.clearTimeout(state.importJobPollTimer);
  try {
    const jobs = await loadImportJobs();
    if (jobs.some((job) => ["QUEUED", "RUNNING"].includes(job.status))) {
      state.importJobPollTimer = window.setTimeout(pollImportJobs, 1000);
    }
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function processImport() {
  if (!state.importPreparationId || !state.importPreviewFresh) return;
  setImportBusy(true);
  setImportStatus("busy", "백그라운드 작업을 시작하는 중", "작업이 시작되면 팝업을 닫을 수 있습니다.");
  try {
    const job = await api(`/demo/api/imports/${encodeURIComponent(state.importPreparationId)}/process`, {
      method: "POST",
      body: JSON.stringify({ code: elements.importCode.value }),
    });
    state.importPreviewFresh = false;
    setImportBusy(false);
    elements.importDialog.close();
    resetImportPreparation();
    await loadImportJobs({ announce: false });
    showToast(`${job.source_label || "대화 데이터"} Processing을 시작했습니다.`);
    pollImportJobs();
  } catch (error) {
    setImportBusy(false);
    setImportStatus("error", "Processing 실패", error.message);
    showToast(error.message, "error");
  }
}

async function controlImportJob(jobId, action) {
  const job = state.importJobs.find((candidate) => candidate.job_id === jobId);
  if (!job) return;
  if (action === "delete") {
    const confirmed = window.confirm(
      `${job.source_label || "이 데이터"}와 여기서 생성된 대화·사실·선호·경험·문화 메모리를 모두 삭제할까요?`,
    );
    if (!confirmed) return;
  }
  try {
    const method = action === "delete" ? "DELETE" : "POST";
    const path = action === "delete"
      ? `/demo/api/imports/jobs/${encodeURIComponent(jobId)}`
      : `/demo/api/imports/jobs/${encodeURIComponent(jobId)}/${action}`;
    const result = await api(path, { method });
    closeImportJobMenus();
    await Promise.all([loadImportJobs({ announce: false }), loadMemories(), loadCulturalMemory()]);
    if (action === "delete") {
      showToast(
        `가져오기 작업과 연결된 메모리 ${result.deleted_memories || 0}개를 삭제했습니다.`,
      );
    } else {
      showToast(action === "pause" ? "Processing을 일시정지했습니다." : "Processing을 재개했습니다.");
      if (action === "resume") pollImportJobs();
    }
  } catch (error) {
    showToast(error.message, "error");
  }
}

elements.memorySearch.addEventListener("input", (event) => {
  state.query = event.target.value;
  renderMemories();
});

elements.filterTabs.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-kind]");
  if (!button) return;
  state.kind = button.dataset.kind;
  for (const tab of elements.filterTabs.querySelectorAll("button")) tab.classList.toggle("active", tab === button);
  renderMemories();
});

elements.openImportStudio.addEventListener("click", () => elements.importDialog.showModal());
elements.closeImportStudio.addEventListener("click", () => {
  if (!state.importBusy) elements.importDialog.close();
});
elements.importDialog.addEventListener("cancel", (event) => {
  if (state.importBusy) event.preventDefault();
});
elements.importSourceTabs.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-import-source]");
  if (!button || state.importBusy) return;
  state.importSourceType = button.dataset.importSource;
  for (const tab of elements.importSourceTabs.querySelectorAll("button")) {
    const active = tab === button;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  }
  for (const panel of elements.importDialog.querySelectorAll("[data-import-source-panel]")) {
    panel.hidden = panel.dataset.importSourcePanel !== state.importSourceType;
  }
  resetImportPreparation();
});
elements.importFile.addEventListener("change", async () => {
  const file = elements.importFile.files[0];
  resetImportPreparation();
  if (!file) {
    state.importRows = [];
    elements.importFileLabel.textContent = "JSON 또는 JSONL 선택";
    return;
  }
  elements.importFileLabel.textContent = file.name;
  setImportStatus("busy", "파일을 읽는 중", `${(file.size / 1024).toFixed(1)} KB`);
  try {
    state.importRows = await parseImportFile(file);
    setImportStatus("ready", `${state.importRows.length.toLocaleString("ko-KR")} rows 준비됨`, "샘플 분석을 실행해 주세요.");
  } catch (error) {
    state.importRows = [];
    setImportStatus("error", "파일을 읽지 못했습니다", error.message);
    showToast(error.message, "error");
  }
});
elements.analyzeImportSource.addEventListener("click", prepareImport);
elements.importCode.addEventListener("input", () => invalidateImportPreview());
elements.runImportPreview.addEventListener("click", runImportPreview);
elements.processImport.addEventListener("click", processImport);
elements.importJobList.addEventListener("click", (event) => {
  const menuButton = event.target.closest("button[data-job-menu]");
  if (menuButton) {
    const card = menuButton.closest(".import-job-card");
    const willOpen = !card.classList.contains("menu-open");
    closeImportJobMenus(card);
    card.classList.toggle("menu-open", willOpen);
    return;
  }
  const actionButton = event.target.closest("button[data-job-action]");
  if (!actionButton) return;
  const card = actionButton.closest(".import-job-card");
  controlImportJob(card.dataset.jobId, actionButton.dataset.jobAction);
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".import-job-card")) closeImportJobMenus();
});

elements.openMemoryForm.addEventListener("click", () => elements.memoryDialog.showModal());
elements.openNewConversation.addEventListener("click", () => {
  if (compactLayoutQuery.matches) {
    startNewConversation({ focusInput: false });
    setMemoryPanelCollapsed(true);
    return;
  }
  elements.newConversationDialog.showModal();
});
elements.newConversationDialog.addEventListener("close", () => {
  if (elements.newConversationDialog.returnValue === "confirm") startNewConversation();
  elements.newConversationDialog.returnValue = "";
});
elements.clearSessionMemories.addEventListener("click", () => {
  elements.clearMemoryCount.textContent = `${state.clearableCount}개`;
  elements.clearMemoriesDialog.showModal();
});
elements.clearMemoriesDialog.addEventListener("close", () => {
  if (elements.clearMemoriesDialog.returnValue === "confirm") clearSessionMemories();
  elements.clearMemoriesDialog.returnValue = "";
});
function showSidebarView(view) {
  const traceActive = view === "trace" && !elements.traceTab.hidden;
  const cultureActive = view === "culture";
  elements.memoryView.hidden = traceActive || cultureActive;
  elements.cultureView.hidden = !cultureActive;
  elements.traceSection.hidden = !traceActive;
  elements.memoryPanel.classList.toggle("trace-active", traceActive);
  elements.memoryPanel.classList.toggle("culture-active", cultureActive);
  const activeView = traceActive ? "trace" : cultureActive ? "culture" : "memory";
  for (const tab of elements.sidebarTabs.querySelectorAll("button[data-sidebar-view]")) {
    const selected = tab.dataset.sidebarView === activeView;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  }
}

elements.sidebarTabs.addEventListener("click", (event) => {
  const tab = event.target.closest("button[data-sidebar-view]");
  if (tab && !tab.hidden) showSidebarView(tab.dataset.sidebarView);
});

function setMemoryPanelCollapsed(collapsed) {
  elements.memoryPanel.classList.toggle("is-collapsed", collapsed);
  elements.workspace.classList.toggle("memory-collapsed", collapsed);
  elements.toggleMemoryPanel.setAttribute("aria-expanded", String(!collapsed));
  elements.toggleMemoryPanel.setAttribute("aria-label", collapsed ? "메모리 패널 열기" : "메모리 패널 접기");
  elements.toggleMemoryPanel.setAttribute("title", collapsed ? "메모리 패널 열기" : "메모리 패널 접기");
}

elements.toggleMemoryPanel.addEventListener("click", () => {
  setMemoryPanelCollapsed(!elements.memoryPanel.classList.contains("is-collapsed"));
});
elements.memorySearchField.addEventListener("click", () => {
  if (!elements.memoryPanel.classList.contains("is-collapsed")) return;
  setMemoryPanelCollapsed(false);
  showSidebarView("memory");
  window.requestAnimationFrame(() => elements.memorySearch.focus());
});
elements.memoryForm.addEventListener("submit", async (event) => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  try { await createMemory(); }
  catch (error) { showToast(error.message, "error"); }
});

elements.chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (state.busy) {
    stopChat();
    return;
  }
  state.submitAfterComposition = false;
  const query = elements.chatInput.value;
  elements.chatInput.value = "";
  sendChat(query);
});

elements.chatInput.addEventListener("compositionstart", () => {
  state.isComposing = true;
});

elements.chatInput.addEventListener("compositionend", () => {
  state.isComposing = false;
  if (!state.submitAfterComposition) return;
  state.submitAfterComposition = false;
  window.requestAnimationFrame(() => {
    if (!state.busy) elements.chatForm.requestSubmit();
  });
});

elements.chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    if (event.isComposing || state.isComposing || event.keyCode === 229) {
      state.submitAfterComposition = true;
      return;
    }
    event.preventDefault();
    if (state.busy) return;
    elements.chatForm.requestSubmit();
  }
});

elements.starterPrompts.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (button) sendChat(button.textContent);
});

async function initialize() {
  if (compactLayoutQuery.matches) {
    setMemoryPanelCollapsed(true);
  }
  try {
    await Promise.all([loadMemories(), loadCulturalMemory(), loadImportJobs({ announce: false })]);
    const activeConversation = state.memories.find(
      (memory) => memory.conversation?.is_live_session
        && memory.conversation?.session_id === state.conversationId,
    );
    if (activeConversation) openConversationMemory(activeConversation, { announce: false });
    pollImportJobs();
  } catch (error) {
    showToast(error.message, "error");
  }
}

initialize();
