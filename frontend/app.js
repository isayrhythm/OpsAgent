const appEl = document.querySelector(".app");
const messagesEl = document.querySelector("#messages");
const formEl = document.querySelector("#chatForm");
const inputEl = document.querySelector("#messageInput");
const sendButtonEl = document.querySelector("#sendButton");
const attachButtonEl = document.querySelector("#attachButton");
const fileInputEl = document.querySelector("#fileInput");
const attachmentTrayEl = document.querySelector("#attachmentTray");
const apiBaseEl = document.querySelector("#apiBase");
const newChatButtonEl = document.querySelector("#newChatButton");
const menuButtonEl = document.querySelector("#menuButton");
const connectionStateEl = document.querySelector("#connectionState");
const conversationListEl = document.querySelector("#conversationList");

const STORAGE_KEY = "opsagent.sessions.v1";
const ACTIVE_SESSION_KEY = "opsagent.active_session.v1";
const USER_ID_KEY = "opsagent.user_id.v1";
const ASSISTANT_LABEL = "Ops";
const userId = localStorage.getItem(USER_ID_KEY) || crypto.randomUUID();
localStorage.setItem(USER_ID_KEY, userId);
marked.setOptions({
  async: false,
  breaks: false,
  gfm: true,
  mangle: false,
  headerIds: false,
});
const ALLOWED_MARKDOWN_TAGS = new Set([
  "a",
  "blockquote",
  "br",
  "code",
  "del",
  "em",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "li",
  "ol",
  "p",
  "pre",
  "strong",
  "table",
  "tbody",
  "td",
  "th",
  "thead",
  "tr",
  "ul",
]);
const ALLOWED_MARKDOWN_ATTRS = {
  a: new Set(["href", "title"]),
  code: new Set(["class"]),
  td: new Set(["align"]),
  th: new Set(["align"]),
};
const COPY_ICON = `
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <rect x="9" y="9" width="10" height="10" rx="2"></rect>
    <path d="M5 15V7a2 2 0 0 1 2-2h8"></path>
  </svg>
`;
const CHECK_ICON = `
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="m5 12 4 4L19 6"></path>
  </svg>
`;

const suggestions = [
  "查询水稻 LOC_Os09g03110 的基因信息",
  "不指定物种查询 LOC_Os09g03110，告诉我命中的物种和标准 ID",
  "不指定物种查询 ABF1，请分别列出水稻、玉米、大豆命中的标准 ID 和匹配来源",
  "Zm00001eb000020 这个玉米基因有什么功能注释？",
  "Glyma.15G027500 的大豆基因基本信息是什么？",
  "把 GmW82.15G028400 转换成标准大豆基因 ID 并查询信息",
];

let sessions = loadSessions();
let activeSessionId = localStorage.getItem(ACTIVE_SESSION_KEY);
if (!sessions.some((session) => session.id === activeSessionId)) {
  activeSessionId = sessions[0]?.id ?? null;
}

function apiBase() {
  return apiBaseEl.value.trim().replace(/\/$/, "");
}

function loadSessions() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveSessions() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions.slice(0, 30)));
}

function setActiveSession(sessionId) {
  activeSessionId = sessionId;
  if (sessionId) {
    localStorage.setItem(ACTIVE_SESSION_KEY, sessionId);
  } else {
    localStorage.removeItem(ACTIVE_SESSION_KEY);
  }
}

function currentSession() {
  return sessions.find((session) => session.id === activeSessionId) ?? null;
}

function createSession(firstMessage = "") {
  const now = Date.now();
  const session = {
    id: crypto.randomUUID(),
    title: firstMessage ? firstMessage.slice(0, 42) : "New chat",
    createdAt: now,
    updatedAt: now,
    messages: [],
    attachments: [],
  };
  sessions.unshift(session);
  setActiveSession(session.id);
  saveSessions();
  renderConversations();
  return session;
}

function updateSessionTitle(session) {
  const firstUserMessage = session.messages.find((message) => message.role === "user");
  session.title = firstUserMessage ? firstUserMessage.content.slice(0, 42) : "New chat";
  session.updatedAt = Date.now();
}

function addMessageToSession(role, content, sessionId = activeSessionId, meta = {}) {
  const session = sessions.find((item) => item.id === sessionId) ?? createSession(content);
  session.messages.push({role, content, createdAt: Date.now(), ...meta});
  updateSessionTitle(session);
  saveSessions();
  renderConversations();
}

function textSize(value) {
  const text = String(value ?? "");
  const cjkCount = (text.match(/[\u3400-\u9fff\uf900-\ufaff]/g) ?? []).length;
  const asciiText = text.replace(/[\u3400-\u9fff\uf900-\ufaff]/g, "");
  const asciiCount = (asciiText.match(/[A-Za-z0-9_.,;:!?()[\]{}'"`~@#$%^&*+=/\\|-]/g) ?? []).length;
  return Math.ceil(cjkCount * 0.6 + asciiCount / 4);
}

function usageFromParts(input, history, output = "") {
  const inputTokens = textSize(input);
  const historyTokens = history.reduce((total, item) => total + textSize(item.content), 0);
  const outputTokens = textSize(output);
  return normalizeUsage({
    input: inputTokens,
    history: historyTokens,
    internal: 0,
    output: outputTokens,
  });
}

function normalizeUsage(usage, cumulativeBase = usage?.cumulativeBase ?? 0) {
  const normalized = {
    input: Number(usage?.input ?? 0),
    history: Number(usage?.history ?? 0),
    internal: Number(usage?.internal ?? 0),
    output: Number(usage?.output ?? 0),
    cumulativeBase: Number(cumulativeBase ?? 0),
  };
  normalized.total = normalized.input + normalized.history + normalized.internal + normalized.output;
  normalized.cumulative = normalized.cumulativeBase + normalized.total;
  return normalized;
}

function previousSessionUsageTotal(session) {
  return (session?.messages ?? []).reduce((total, message) => {
    if (message.role !== "agent" || !message.usage) {
      return total;
    }
    return total + Number(message.usage.total ?? 0);
  }, 0);
}

function usageLabel(usage) {
  const normalized = normalizeUsage(usage);
  return `本轮 ${normalized.total} / 累计 ${normalized.cumulative} tokens`;
}

function usageTitle(usage) {
  const normalized = normalizeUsage(usage);
  return `估算 tokens：本轮 ${normalized.total} / 累计 ${normalized.cumulative}。输入 ${normalized.input} / 历史 ${normalized.history} / 调用 ${normalized.internal} / 输出 ${normalized.output}`;
}

function formatBytes(value) {
  const bytes = Number(value ?? 0);
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function renderAttachmentTray() {
  const attachments = currentSession()?.attachments ?? [];
  attachmentTrayEl.innerHTML = "";
  attachmentTrayEl.classList.toggle("is-empty", attachments.length === 0);
  for (const file of attachments) {
    const chip = document.createElement("div");
    chip.className = "attachment-chip";
    chip.title = `${file.filename} (${formatBytes(file.size)})`;
    chip.innerHTML = `<span></span><small></small>`;
    chip.querySelector("span").textContent = file.filename;
    chip.querySelector("small").textContent = formatBytes(file.size);
    attachmentTrayEl.appendChild(chip);
  }
}

function renderConversations() {
  conversationListEl.innerHTML = "";
  if (!sessions.length) {
    const empty = document.createElement("div");
    empty.className = "conversation";
    empty.textContent = "No conversations yet";
    conversationListEl.appendChild(empty);
    return;
  }

  for (const session of sessions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `conversation ${session.id === activeSessionId ? "active" : ""}`;
    button.textContent = session.title || "New chat";
    button.title = session.title || "New chat";
    button.addEventListener("click", () => {
      setActiveSession(session.id);
      renderConversations();
      renderCurrentSession();
      renderAttachmentTray();
      appEl.classList.remove("sidebar-open");
    });
    conversationListEl.appendChild(button);
  }
}

function renderEmptyState() {
  messagesEl.innerHTML = "";
  const shell = document.createElement("div");
  shell.className = "empty-state";
  shell.innerHTML = `
    <div class="empty-inner">
      <h1>What can I help with?</h1>
      <div class="suggestions"></div>
    </div>
  `;

  const list = shell.querySelector(".suggestions");
  for (const text of suggestions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggestion";
    button.textContent = text;
    button.addEventListener("click", () => submitMessage(text));
    list.appendChild(button);
  }
  messagesEl.appendChild(shell);
}

function ensureThread() {
  let thread = messagesEl.querySelector(".thread");
  if (thread) {
    return thread;
  }

  messagesEl.innerHTML = "";
  thread = document.createElement("div");
  thread.className = "thread";
  messagesEl.appendChild(thread);
  return thread;
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  });
}

function renderMarkdown(target, content) {
  target.innerHTML = sanitizeMarkdownHtml(marked.parse(String(content ?? "")));
  for (const link of target.querySelectorAll("a[href]")) {
    link.target = "_blank";
    link.rel = "noreferrer noopener";
  }
}

function sanitizeMarkdownHtml(html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  for (const element of [...template.content.querySelectorAll("*")]) {
    const tagName = element.tagName.toLowerCase();
    if (!ALLOWED_MARKDOWN_TAGS.has(tagName)) {
      element.replaceWith(...element.childNodes);
      continue;
    }

    const allowedAttrs = ALLOWED_MARKDOWN_ATTRS[tagName] ?? new Set();
    for (const attr of [...element.attributes]) {
      const attrName = attr.name.toLowerCase();
      if (!allowedAttrs.has(attrName)) {
        element.removeAttribute(attr.name);
      }
    }

    if (tagName === "a") {
      const href = element.getAttribute("href") ?? "";
      if (!/^(https?:|mailto:|#|\/)/i.test(href)) {
        element.removeAttribute("href");
      }
    }
  }
  return template.innerHTML;
}

async function copyText(text, button) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.className = "copy-fallback";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    setCopyButtonState(button, true);
    button.classList.add("copied");
    window.setTimeout(() => {
      setCopyButtonState(button, false);
      button.classList.remove("copied");
    }, 1200);
  } catch {
    button.setAttribute("aria-label", "复制失败");
    button.title = "复制失败";
    window.setTimeout(() => {
      setCopyButtonState(button, false);
    }, 1200);
  }
}

function setCopyButtonState(button, copied = false) {
  button.innerHTML = copied ? CHECK_ICON : COPY_ICON;
  button.setAttribute("aria-label", copied ? "已复制" : "复制消息");
  button.title = copied ? "已复制" : "复制消息";
}

function createCopyActions(getText) {
  const actions = document.createElement("div");
  actions.className = "message-actions";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy-button icon-copy-button";
  setCopyButtonState(button);
  button.addEventListener("click", () => copyText(getText(), button));
  actions.appendChild(button);
  return actions;
}

function renderUsageMeta(target, usage) {
  if (!usage) {
    return;
  }
  const normalized = normalizeUsage(usage);
  const meta = document.createElement("div");
  meta.className = "token-meta";
  meta.textContent = usageLabel(normalized);
  meta.title = usageTitle(normalized);
  target.appendChild(meta);
}

function renderTurn(content, role = "agent", meta = {}) {
  const thread = ensureThread();
  const turn = document.createElement("article");
  turn.className = `turn ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "你" : ASSISTANT_LABEL;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  const body = document.createElement("div");
  body.className = "message-content";
  if (role === "agent") {
    renderMarkdown(body, content);
  } else {
    body.textContent = content;
  }
  bubble.appendChild(body);

  const footer = document.createElement("div");
  footer.className = "message-footer";
  if (role === "agent") {
    renderUsageMeta(footer, meta.usage);
  }
  footer.appendChild(createCopyActions(() => content));

  turn.appendChild(avatar);
  turn.appendChild(bubble);
  turn.appendChild(footer);
  thread.appendChild(turn);
  scrollToBottom();
  return bubble;
}

function renderCurrentSession() {
  const session = currentSession();
  if (!session || !session.messages.length) {
    renderEmptyState();
    renderAttachmentTray();
    return;
  }

  messagesEl.innerHTML = "";
  const thread = document.createElement("div");
  thread.className = "thread";
  messagesEl.appendChild(thread);
  let cumulativeBase = 0;
  for (const message of session.messages) {
    const meta = {...message};
    if (message.role === "agent" && message.usage) {
      meta.usage = normalizeUsage(message.usage, cumulativeBase);
      cumulativeBase += meta.usage.total;
    }
    renderTurn(message.content, message.role, meta);
  }
  scrollToBottom();
  renderAttachmentTray();
}

function addAssistantStreamTurn(usage) {
  const bubble = renderTurn("", "agent");
  bubble.parentElement?.querySelector(".message-footer")?.remove();
  bubble.textContent = "";

  const statusRow = document.createElement("div");
  statusRow.className = "thinking-row";

  const status = document.createElement("div");
  status.className = "thinking-status";
  status.textContent = "Submitting task";

  const tokenMeter = document.createElement("div");
  tokenMeter.className = "token-meter";
  tokenMeter.textContent = usageLabel(usage);
  tokenMeter.title = usageTitle(usage);

  const text = document.createElement("div");
  text.className = "stream-text";
  text.innerHTML = "";

  statusRow.appendChild(status);
  bubble.appendChild(statusRow);
  bubble.appendChild(text);
  const streamTurn = {bubble, status, statusRow, tokenMeter, text, answer: "", usage};
  streamTurn.activeAgents = new Map();
  const footer = document.createElement("div");
  footer.className = "message-footer";
  footer.appendChild(tokenMeter);
  footer.appendChild(createCopyActions(() => streamTurn.answer));
  bubble.after(footer);
  return streamTurn;
}

function updateTokenMeter(streamTurn) {
  streamTurn.usage = normalizeUsage(streamTurn.usage);
  streamTurn.tokenMeter.textContent = usageLabel(streamTurn.usage);
  streamTurn.tokenMeter.title = usageTitle(streamTurn.usage);
}

function addInternalUsage(streamTurn, amount) {
  streamTurn.usage.internal = (streamTurn.usage.internal ?? 0) + amount;
  updateTokenMeter(streamTurn);
}

function updateThinking(streamTurn, text, kind = "active") {
  streamTurn.status.textContent = text;
  streamTurn.status.className = `thinking-status ${kind}`;
  scrollToBottom();
}

function agentDisplayName(name) {
  return String(name ?? "").trim();
}

function updateAgentThinking(streamTurn, payload) {
  const agent = agentDisplayName(payload.data?.agent);
  const state = payload.data?.agent_state;
  if (!agent || !state) {
    updateThinking(streamTurn, payload.status);
    return;
  }

  if (state === "done") {
    streamTurn.activeAgents.delete(agent);
  } else {
    streamTurn.activeAgents.set(agent, Boolean(payload.data?.retry));
  }

  const agents = [...streamTurn.activeAgents.entries()];
  if (!agents.length) {
    updateThinking(streamTurn, payload.status);
    return;
  }
  if (agents.length === 1) {
    const [name, isRetry] = agents[0];
    updateThinking(streamTurn, isRetry ? `正在重新调用 ${name} 智能体` : `正在调用 ${name} 智能体`);
    return;
  }

  const names = agents.map(([name, isRetry]) => (isRetry ? `${name}(重试)` : name)).join("、");
  updateThinking(streamTurn, `正在调用 ${agents.length} 个智能体：${names}`);
}

function hideThinking(streamTurn) {
  streamTurn.status.classList.add("is-hidden");
  streamTurn.statusRow.classList.add("answering");
}

function appendAnswerDelta(streamTurn, delta) {
  streamTurn.answer += delta;
  streamTurn.usage.output = textSize(streamTurn.answer);
  updateTokenMeter(streamTurn);
  renderMarkdown(streamTurn.text, streamTurn.answer);
  hideThinking(streamTurn);
  scrollToBottom();
}

function setConnection(text, ok = true) {
  connectionStateEl.textContent = text;
  connectionStateEl.style.color = ok ? "var(--ok)" : "var(--danger)";
}

async function checkApi() {
  try {
    const response = await fetch(`${apiBase()}/api/health`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    setConnection("API connected", true);
  } catch {
    setConnection("API disconnected", false);
  }
}

function listenToTask(eventsUrl, streamTurn, sessionId) {
  const source = new EventSource(`${apiBase()}${eventsUrl}`);

  source.addEventListener("progress", (event) => {
    const payload = JSON.parse(event.data);
    updateAgentThinking(streamTurn, payload);
  });

  source.addEventListener("answer_delta", (event) => {
    const payload = JSON.parse(event.data);
    appendAnswerDelta(streamTurn, payload.data?.delta ?? "");
  });

  source.addEventListener("thinking_delta", (event) => {
    const payload = JSON.parse(event.data);
    const amount = payload.data?.delta ? textSize(payload.data.delta) : Math.ceil((payload.data?.delta_length ?? 0) / 3);
    addInternalUsage(streamTurn, amount);
  });

  source.addEventListener("result", (event) => {
    const payload = JSON.parse(event.data);
    const answer = payload.data.answer || JSON.stringify(payload.data, null, 2);
    if (!streamTurn.answer) {
      appendAnswerDelta(streamTurn, answer);
    }
    addMessageToSession("agent", answer, sessionId, {usage: streamTurn.usage});
  });

  source.addEventListener("error", (event) => {
    const text = event.data ? JSON.parse(event.data).status : "SSE connection failed";
    updateThinking(streamTurn, text, "error");
    addMessageToSession("agent", `请求失败：${text}`, sessionId, {usage: streamTurn.usage});
    sendButtonEl.disabled = false;
    source.close();
  });

  source.addEventListener("end", () => {
    sendButtonEl.disabled = false;
    source.close();
    checkApi();
  });
}

async function submitMessage(message) {
  const text = message.trim();
  if (!text || sendButtonEl.disabled) {
    return;
  }

  if (!currentSession()) {
    createSession(text);
  }
  const sessionId = activeSessionId;
  const attachments = currentSession()?.attachments ?? [];
  const history = (currentSession()?.messages ?? []).slice(-20).map((item) => ({
    role: item.role === "agent" ? "assistant" : item.role,
    content: item.content,
  }));
  const usage = normalizeUsage(usageFromParts(text, history), previousSessionUsageTotal(currentSession()));

  renderTurn(text, "user");
  addMessageToSession("user", text, sessionId);
  inputEl.value = "";
  resizeComposer();
  sendButtonEl.disabled = true;
  const streamTurn = addAssistantStreamTurn(usage);

  try {
    const response = await fetch(`${apiBase()}/api/chat`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message: text, user_id: userId, session_id: sessionId, history, attachments}),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    listenToTask(payload.events_url, streamTurn, sessionId);
  } catch (error) {
    updateThinking(streamTurn, `Request failed: ${error.message}`, "error");
    addMessageToSession("agent", `请求失败：${error.message}`, sessionId, {usage: streamTurn.usage});
    sendButtonEl.disabled = false;
  }
}

async function uploadSelectedFiles(files) {
  const selected = [...files];
  if (!selected.length) {
    return;
  }
  if (!currentSession()) {
    createSession("Uploaded files");
    renderCurrentSession();
  }

  const session = currentSession();
  const formData = new FormData();
  formData.append("user_id", userId);
  formData.append("session_id", session.id);
  for (const file of selected) {
    formData.append("files", file);
  }

  attachButtonEl.disabled = true;
  try {
    const response = await fetch(`${apiBase()}/api/uploads`, {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    session.attachments = [...(session.attachments ?? []), ...payload.files];
    session.updatedAt = Date.now();
    saveSessions();
    renderConversations();
    renderAttachmentTray();
  } catch (error) {
    setConnection(`Upload failed: ${error.message}`, false);
  } finally {
    attachButtonEl.disabled = false;
    fileInputEl.value = "";
  }
}

function resizeComposer() {
  inputEl.style.height = "auto";
  inputEl.style.height = `${Math.min(inputEl.scrollHeight, 180)}px`;
}

formEl.addEventListener("submit", (event) => {
  event.preventDefault();
  submitMessage(inputEl.value);
});

inputEl.addEventListener("input", resizeComposer);
inputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    formEl.requestSubmit();
  }
});

apiBaseEl.addEventListener("change", checkApi);
attachButtonEl.addEventListener("click", () => fileInputEl.click());
fileInputEl.addEventListener("change", () => uploadSelectedFiles(fileInputEl.files));
newChatButtonEl.addEventListener("click", () => {
  setActiveSession(null);
  renderConversations();
  renderEmptyState();
  renderAttachmentTray();
  inputEl.focus();
});
menuButtonEl.addEventListener("click", () => appEl.classList.toggle("sidebar-open"));

renderConversations();
renderCurrentSession();
renderAttachmentTray();
checkApi();
