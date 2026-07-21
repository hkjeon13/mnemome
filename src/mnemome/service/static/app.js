const state = {
  memories: [],
  kind: "",
  query: "",
  busy: false,
  clearableCount: 0,
  abortController: null,
};

const elements = {
  memoryList: document.querySelector("#memory-list"),
  memorySearch: document.querySelector("#memory-search"),
  filterTabs: document.querySelector(".filter-tabs"),
  memoryDialog: document.querySelector("#memory-dialog"),
  openMemoryForm: document.querySelector("#open-memory-form"),
  clearSessionMemories: document.querySelector("#clear-session-memories"),
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
  toast: document.querySelector("#toast"),
};

const kindLabels = {
  fact: "FACT · 사실",
  preference: "PREFERENCE · 선호",
  episode: "EPISODE · 경험",
  conversation: "CONVERSATION · 대화",
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
    const haystack = `${memory.content} ${(memory.tags || []).join(" ")}`.toLowerCase();
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
    card.className = `memory-card${memory.is_seed ? " seeded" : ""}`;
    card.dataset.memoryId = memory.id;

    const type = document.createElement("span");
    type.className = `memory-type ${memory.kind}`;
    type.textContent = kindLabels[memory.kind] || memory.kind;

    const content = document.createElement("p");
    content.textContent = memory.content;
    content.title = memory.content;

    const tags = document.createElement("div");
    tags.className = "tag-row";
    for (const tag of [...(memory.tags || []).slice(0, 3), formatDate(memory.created_at)]) {
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
    } else {
      const remove = document.createElement("button");
      remove.className = "delete-memory";
      remove.type = "button";
      remove.title = "기억 비활성화";
      remove.setAttribute("aria-label", "기억 비활성화");
      remove.textContent = "×";
      remove.addEventListener("click", () => deleteMemory(memory.id));
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
  const confirmed = window.confirm(
    `현재 브라우저 세션에서 생성된 기억 ${state.clearableCount}개를 비울까요? 기본 샘플 3개는 유지됩니다.`,
  );
  if (!confirmed) return;
  elements.clearSessionMemories.disabled = true;
  try {
    const result = await api("/demo/api/memories", { method: "DELETE" });
    await loadMemories();
    showToast(`사용자 기억 ${result.cleared}개를 비웠습니다. 기본 샘플은 유지됩니다.`);
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
  if (meta.length) {
    const metadata = document.createElement("div");
    metadata.className = "message-meta";
    for (const item of meta) {
      const chip = document.createElement("span");
      chip.textContent = item;
      metadata.append(chip);
    }
    body.append(metadata);
  }
  message.append(avatar, body);
  elements.conversation.append(message);
  elements.conversation.scrollTop = elements.conversation.scrollHeight;
  return message;
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
  elements.traceSection.hidden = false;
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

async function sendChat(query) {
  if (state.busy || !query.trim()) return;
  const controller = new AbortController();
  state.abortController = controller;
  setChatBusy(true);
  elements.starterPrompts.hidden = true;
  appendMessage("user", query.trim());
  const typing = appendMessage("assistant", "관련 기억을 검색하고 있습니다…");
  typing.classList.add("typing");
  try {
    const result = await api("/demo/api/chat", {
      method: "POST",
      body: JSON.stringify({ query: query.trim() }),
      signal: controller.signal,
    });
    typing.remove();
    appendMessage("assistant", result.answer, [
      `${result.runtime}`,
      `recall ${result.recalled.length}`,
      `${result.elapsed_ms} ms`,
      result.run_id.slice(0, 18),
    ]);
    renderAgentTrace(result);
    await loadMemories();
    if (result.preference_captured) showToast("대화에서 선호 지시를 감지해 장기 기억에 저장했습니다.");
  } catch (error) {
    typing.remove();
    if (error.name === "AbortError") appendMessage("assistant", "응답 생성을 중지했습니다.");
    else appendMessage("assistant", `실행 중 문제가 발생했습니다: ${error.message}`);
  } finally {
    state.abortController = null;
    setChatBusy(false);
    elements.chatInput.focus();
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

elements.openMemoryForm.addEventListener("click", () => elements.memoryDialog.showModal());
elements.clearSessionMemories.addEventListener("click", clearSessionMemories);
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
  const query = elements.chatInput.value;
  elements.chatInput.value = "";
  sendChat(query);
});

elements.chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
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
  try {
    await loadMemories();
  } catch (error) {
    showToast(error.message, "error");
  }
}

initialize();
