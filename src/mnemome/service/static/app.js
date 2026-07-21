const state = { memories: [], kind: "", query: "", busy: false };

const elements = {
  runtimeStatus: document.querySelector("[data-testid='runtime-status']"),
  runtimeLabel: document.querySelector("#runtime-label"),
  memoryCount: document.querySelector("#memory-count"),
  memoryList: document.querySelector("#memory-list"),
  memorySearch: document.querySelector("#memory-search"),
  filterTabs: document.querySelector(".filter-tabs"),
  memoryDialog: document.querySelector("#memory-dialog"),
  openMemoryForm: document.querySelector("#open-memory-form"),
  memoryForm: document.querySelector("#memory-form"),
  memoryKind: document.querySelector("#memory-kind"),
  memoryContent: document.querySelector("#memory-content"),
  memoryTags: document.querySelector("#memory-tags"),
  chatForm: document.querySelector("#chat-form"),
  chatInput: document.querySelector("#chat-input"),
  conversation: document.querySelector("#conversation"),
  starterPrompts: document.querySelector("#starter-prompts"),
  sendButton: document.querySelector(".send-button"),
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

  elements.memoryCount.textContent = String(state.memories.length);
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
    card.className = "memory-card";
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

    const remove = document.createElement("button");
    remove.className = "delete-memory";
    remove.type = "button";
    remove.title = "기억 비활성화";
    remove.setAttribute("aria-label", "기억 비활성화");
    remove.textContent = "×";
    remove.addEventListener("click", () => deleteMemory(memory.id));

    card.append(type, content, tags, remove);
    elements.memoryList.append(card);
  }
}

async function loadMemories() {
  const payload = await api("/demo/api/memories");
  state.memories = payload.items;
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

async function sendChat(query) {
  if (state.busy || !query.trim()) return;
  state.busy = true;
  elements.sendButton.disabled = true;
  elements.starterPrompts.hidden = true;
  appendMessage("user", query.trim());
  const typing = appendMessage("assistant", "관련 기억을 검색하고 있습니다…");
  typing.classList.add("typing");
  try {
    const result = await api("/demo/api/chat", {
      method: "POST",
      body: JSON.stringify({ query: query.trim() }),
    });
    typing.remove();
    appendMessage("assistant", result.answer, [
      `${result.runtime}`,
      `recall ${result.recalled.length}`,
      `${result.elapsed_ms} ms`,
      result.run_id.slice(0, 18),
    ]);
    await loadMemories();
  } catch (error) {
    typing.remove();
    appendMessage("assistant", `실행 중 문제가 발생했습니다: ${error.message}`);
  } finally {
    state.busy = false;
    elements.sendButton.disabled = false;
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
elements.memoryForm.addEventListener("submit", async (event) => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  try { await createMemory(); }
  catch (error) { showToast(error.message, "error"); }
});

elements.chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = elements.chatInput.value;
  elements.chatInput.value = "";
  sendChat(query);
});

elements.chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.chatForm.requestSubmit();
  }
});

elements.starterPrompts.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (button) sendChat(button.textContent);
});

async function initialize() {
  try {
    const status = await api("/demo/api/status");
    elements.runtimeStatus.classList.add("ready");
    elements.runtimeLabel.textContent = `${status.runtime} · ${status.model || "모델 미설정"}`;
    await loadMemories();
  } catch (error) {
    elements.runtimeLabel.textContent = "연결 실패";
    showToast(error.message, "error");
  }
}

initialize();
