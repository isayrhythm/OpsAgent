const appEl = document.querySelector(".app");
const messagesEl = document.querySelector("#messages");
const formEl = document.querySelector("#chatForm");
const inputEl = document.querySelector("#messageInput");
const sendButtonEl = document.querySelector("#sendButton");
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
const md = window.markdownit({
  html: false,
  linkify: true,
  breaks: false,
});

const suggestions = [
  "查询 AT1G00001 在 leaf 和 root 的表达量",
  "AT1G00003 在所有组织的表达量是多少？",
  "比较 AT1G00002 和 AT1G00004 在 flower 的表达量",
  "查询拟南芥基因表达数据里 root 组织的记录",
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

function addMessageToSession(role, content, sessionId = activeSessionId) {
  const session = sessions.find((item) => item.id === sessionId) ?? createSession(content);
  session.messages.push({role, content, createdAt: Date.now()});
  updateSessionTitle(session);
  saveSessions();
  renderConversations();
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
  target.innerHTML = md.render(content);
}

function renderTurn(content, role = "agent") {
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

  turn.appendChild(avatar);
  turn.appendChild(bubble);
  thread.appendChild(turn);
  scrollToBottom();
  return bubble;
}

function renderCurrentSession() {
  const session = currentSession();
  if (!session || !session.messages.length) {
    renderEmptyState();
    return;
  }

  messagesEl.innerHTML = "";
  const thread = document.createElement("div");
  thread.className = "thread";
  messagesEl.appendChild(thread);
  for (const message of session.messages) {
    renderTurn(message.content, message.role);
  }
  scrollToBottom();
}

function addAssistantStreamTurn() {
  const bubble = renderTurn("", "agent");
  bubble.textContent = "";

  const status = document.createElement("div");
  status.className = "thinking-status";
  status.textContent = "Submitting task";

  const text = document.createElement("div");
  text.className = "stream-text";
  text.innerHTML = "";

  bubble.appendChild(status);
  bubble.appendChild(text);
  return {bubble, status, text, answer: ""};
}

function updateThinking(streamTurn, text, kind = "active") {
  streamTurn.status.textContent = text;
  streamTurn.status.className = `thinking-status ${kind}`;
  scrollToBottom();
}

function hideThinking(streamTurn) {
  streamTurn.status.classList.add("is-hidden");
}

function appendAnswerDelta(streamTurn, delta) {
  streamTurn.answer += delta;
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
    updateThinking(streamTurn, payload.status);
  });

  source.addEventListener("answer_delta", (event) => {
    const payload = JSON.parse(event.data);
    appendAnswerDelta(streamTurn, payload.data?.delta ?? "");
  });

  source.addEventListener("result", (event) => {
    const payload = JSON.parse(event.data);
    const answer = payload.data.answer || JSON.stringify(payload.data, null, 2);
    if (!streamTurn.answer) {
      appendAnswerDelta(streamTurn, answer);
    }
    addMessageToSession("agent", answer, sessionId);
  });

  source.addEventListener("error", (event) => {
    const text = event.data ? JSON.parse(event.data).status : "SSE connection failed";
    updateThinking(streamTurn, text, "error");
    addMessageToSession("agent", `请求失败：${text}`, sessionId);
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
  const history = (currentSession()?.messages ?? []).slice(-20).map((item) => ({
    role: item.role === "agent" ? "assistant" : item.role,
    content: item.content,
  }));

  renderTurn(text, "user");
  addMessageToSession("user", text, sessionId);
  inputEl.value = "";
  resizeComposer();
  sendButtonEl.disabled = true;
  const streamTurn = addAssistantStreamTurn();

  try {
    const response = await fetch(`${apiBase()}/api/chat`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message: text, user_id: userId, session_id: sessionId, history}),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    listenToTask(payload.events_url, streamTurn, sessionId);
  } catch (error) {
    updateThinking(streamTurn, `Request failed: ${error.message}`, "error");
    addMessageToSession("agent", `请求失败：${error.message}`, sessionId);
    sendButtonEl.disabled = false;
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
newChatButtonEl.addEventListener("click", () => {
  setActiveSession(null);
  renderConversations();
  renderEmptyState();
  inputEl.focus();
});
menuButtonEl.addEventListener("click", () => appEl.classList.toggle("sidebar-open"));

renderConversations();
renderCurrentSession();
checkApi();
