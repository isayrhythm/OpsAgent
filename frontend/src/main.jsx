import React from "react";
import {createPortal} from "react-dom";
import {createRoot} from "react-dom/client";
import {marked} from "marked";

import "./styles.css";

marked.setOptions({
  async: false,
  breaks: false,
  gfm: true,
});

const STORAGE_KEY = "opsagent.sessions.v2";
const LEGACY_STORAGE_KEY = "opsagent.sessions.v1";
const ACTIVE_SESSION_KEY = "opsagent.active_session.v1";
const USER_ID_KEY = "opsagent.user_id.v1";
const API_BASE_KEY = "opsagent.api_base.v1";
const DEFAULT_API_BASE = "http://127.0.0.1:8001";
const ASSISTANT_NAME = "OpsAgent";
const DAY_MS = 24 * 60 * 60 * 1000;
const MENU_WIDTH = 172;
const MENU_HEIGHT = 156;

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

const suggestions = [
  "解释一下大模型量化模型是什么",
  "查询水稻 LOC_Os09g03110 的基因表达信息",
  "我上传了文件，帮我根据文件名和大小判断下一步怎么处理",
  "如果没有涉及专门能力，就先按普通聊天回答我",
];

function loadUserId() {
  const cached = localStorage.getItem(USER_ID_KEY);
  if (cached) {
    return cached;
  }
  const id = crypto.randomUUID();
  localStorage.setItem(USER_ID_KEY, id);
  return id;
}

function normalizeSession(session) {
  return {
    id: session.id || crypto.randomUUID(),
    title: session.title || "新对话",
    manualTitle: Boolean(session.manualTitle),
    createdAt: Number(session.createdAt || Date.now()),
    updatedAt: Number(session.updatedAt || session.createdAt || Date.now()),
    pinnedAt: session.pinnedAt || null,
    messages: Array.isArray(session.messages)
      ? session.messages.map((message) => ({
          id: message.id || crypto.randomUUID(),
          role: message.role === "assistant" ? "agent" : message.role,
          content: String(message.content || ""),
          createdAt: Number(message.createdAt || Date.now()),
          usage: message.usage,
        }))
      : [],
    attachments: Array.isArray(session.attachments) ? session.attachments : [],
  };
}

function loadSessions() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY) || localStorage.getItem(LEGACY_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? sortSessions(parsed.map(normalizeSession)) : [];
  } catch {
    return [];
  }
}

function sortSessions(items) {
  return [...items].sort((first, second) => {
    const pinned = Number(second.pinnedAt || 0) - Number(first.pinnedAt || 0);
    if (pinned !== 0) {
      return pinned;
    }
    return Number(second.updatedAt || 0) - Number(first.updatedAt || 0);
  });
}

function newSession(title = "新对话") {
  const now = Date.now();
  return {
    id: crypto.randomUUID(),
    title: title ? title.slice(0, 42) : "新对话",
    manualTitle: false,
    createdAt: now,
    updatedAt: now,
    pinnedAt: null,
    messages: [],
    attachments: [],
  };
}

function autoTitle(session) {
  if (session.manualTitle) {
    return session.title;
  }
  const firstUser = session.messages.find((message) => message.role === "user");
  return firstUser?.content?.slice(0, 42) || session.title || "新对话";
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function textSize(value) {
  const text = String(value || "");
  const cjkCount = (text.match(/[\u3400-\u9fff\uf900-\ufaff]/g) || []).length;
  const asciiText = text.replace(/[\u3400-\u9fff\uf900-\ufaff]/g, "");
  const asciiCount = (asciiText.match(/[A-Za-z0-9_.,;:!?()[\]{}'"`~@#$%^&*+=/\\|-]/g) || []).length;
  return Math.ceil(cjkCount * 0.6 + asciiCount / 4);
}

function normalizeUsage(usage, cumulativeBase = usage?.cumulativeBase || 0) {
  const normalized = {
    input: Number(usage?.input || 0),
    history: Number(usage?.history || 0),
    internal: Number(usage?.internal || 0),
    output: Number(usage?.output || 0),
    cumulativeBase: Number(cumulativeBase || 0),
  };
  normalized.total = normalized.input + normalized.history + normalized.internal + normalized.output;
  normalized.cumulative = normalized.cumulativeBase + normalized.total;
  return normalized;
}

function estimateUsage(input, history, output = "") {
  return normalizeUsage({
    input: textSize(input),
    history: history.reduce((total, item) => total + textSize(item.content), 0),
    internal: 0,
    output: textSize(output),
  });
}

function previousUsageTotal(session) {
  return (session?.messages || []).reduce((total, message) => {
    if (message.role !== "agent" || !message.usage) {
      return total;
    }
    return total + Number(message.usage.total || 0);
  }, 0);
}

function usageLabel(usage) {
  if (!usage) {
    return "";
  }
  const normalized = normalizeUsage(usage);
  return `本轮 ${normalized.total} / 累计 ${normalized.cumulative} tokens`;
}

function sanitizeMarkdown(html, apiBase = DEFAULT_API_BASE) {
  const template = document.createElement("template");
  template.innerHTML = html;
  for (const element of [...template.content.querySelectorAll("*")]) {
    const tagName = element.tagName.toLowerCase();
    if (!ALLOWED_MARKDOWN_TAGS.has(tagName)) {
      element.replaceWith(...element.childNodes);
      continue;
    }

    const allowedAttrs = ALLOWED_MARKDOWN_ATTRS[tagName] || new Set();
    for (const attr of [...element.attributes]) {
      const attrName = attr.name.toLowerCase();
      if (!allowedAttrs.has(attrName)) {
        element.removeAttribute(attr.name);
      }
    }

    if (tagName === "a") {
      let href = element.getAttribute("href") || "";
      if (!/^(https?:|mailto:|#|\/)/i.test(href)) {
        element.removeAttribute("href");
      } else {
        if (href.startsWith("/api/")) {
          href = `${String(apiBase || DEFAULT_API_BASE).replace(/\/$/, "")}${href}`;
          element.setAttribute("href", href);
        }
        element.setAttribute("target", "_blank");
        element.setAttribute("rel", "noreferrer noopener");
      }
    }
  }
  return template.innerHTML;
}

function normalizeMarkdown(content) {
  return String(content || "")
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((part) => {
      if (part.startsWith("`")) {
        return part;
      }
      return part
        .replace(/\*\*([^*\n]*?\S[^*\n]*?)\s*\*\*/g, (_match, text) => `<strong>${text.trimEnd()}</strong>`)
        .replace(/__([^_\n]*?\S[^_\n]*?)\s*__/g, (_match, text) => `<strong>${text.trimEnd()}</strong>`);
    })
    .join("");
}

function renderMarkdown(content, apiBase) {
  return sanitizeMarkdown(marked.parse(normalizeMarkdown(content)), apiBase);
}

function groupLabel(session) {
  if (session.pinnedAt) {
    return "置顶";
  }
  const updated = new Date(session.updatedAt || session.createdAt || Date.now());
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = new Date(updated.getFullYear(), updated.getMonth(), updated.getDate()).getTime();
  const diff = Math.floor((today - day) / DAY_MS);
  if (diff <= 0) {
    return "今天";
  }
  if (diff === 1) {
    return "昨天";
  }
  if (diff < 7) {
    return "7 天内";
  }
  return "更早";
}

function groupedSessions(sessions) {
  const groups = [];
  for (const session of sessions) {
    const label = groupLabel(session);
    const last = groups[groups.length - 1];
    if (!last || last.label !== label) {
      groups.push({label, items: [session]});
    } else {
      last.items.push(session);
    }
  }
  return groups;
}

function agentStatus(payload, activeAgents) {
  const agent = String(payload.data?.agent || "").trim();
  const state = payload.data?.agent_state;
  if (!agent || !state) {
    return payload.status;
  }

  if (state === "done") {
    activeAgents.delete(agent);
  } else {
    activeAgents.set(agent, Boolean(payload.data?.retry));
  }

  const agents = [...activeAgents.entries()];
  if (!agents.length) {
    return payload.status;
  }
  if (agents.length === 1) {
    const [name, isRetry] = agents[0];
    return isRetry ? `正在重新调用 ${name} 智能体` : `正在调用 ${name} 智能体`;
  }
  return `正在调用 ${agents.length} 个智能体：${agents.map(([name]) => name).join("、")}`;
}

function App() {
  const [sessions, setSessions] = React.useState(loadSessions);
  const [activeSessionId, setActiveSessionId] = React.useState(() => {
    const loaded = loadSessions();
    const cached = localStorage.getItem(ACTIVE_SESSION_KEY);
    return loaded.some((session) => session.id === cached) ? cached : loaded[0]?.id || null;
  });
  const [apiBase, setApiBase] = React.useState(() => localStorage.getItem(API_BASE_KEY) || DEFAULT_API_BASE);
  const [connection, setConnection] = React.useState({label: "检查 API", ok: false});
  const [sidebarOpen, setSidebarOpen] = React.useState(false);
  const [openMenuId, setOpenMenuId] = React.useState(null);
  const [input, setInput] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [uploading, setUploading] = React.useState(false);
  const [draggingFiles, setDraggingFiles] = React.useState(false);
  const [userId] = React.useState(loadUserId);
  const messagesRef = React.useRef(null);
  const fileInputRef = React.useRef(null);
  const dragDepthRef = React.useRef(0);

  const activeSession = React.useMemo(
    () => sessions.find((session) => session.id === activeSessionId) || null,
    [sessions, activeSessionId],
  );

  React.useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions.slice(0, 30)));
  }, [sessions]);

  React.useEffect(() => {
    if (activeSessionId) {
      localStorage.setItem(ACTIVE_SESSION_KEY, activeSessionId);
    } else {
      localStorage.removeItem(ACTIVE_SESSION_KEY);
    }
  }, [activeSessionId]);

  React.useEffect(() => {
    localStorage.setItem(API_BASE_KEY, apiBase);
  }, [apiBase]);

  React.useEffect(() => {
    checkApi();
  }, []);

  React.useEffect(() => {
    const node = messagesRef.current;
    if (!node) {
      return;
    }
    requestAnimationFrame(() => {
      node.scrollTop = node.scrollHeight;
    });
  }, [activeSession?.messages, activeSessionId]);

  function normalizedApiBase() {
    return apiBase.trim().replace(/\/$/, "");
  }

  function commitSessions(updater) {
    setSessions((current) => sortSessions(updater(current)).slice(0, 30));
  }

  function patchSession(sessionId, updater) {
    commitSessions((current) =>
      current.map((session) => {
        if (session.id !== sessionId) {
          return session;
        }
        const next = updater(session);
        return {...next, title: autoTitle(next), updatedAt: Date.now()};
      }),
    );
  }

  function updateMessage(sessionId, messageId, updater) {
    setSessions((current) =>
      sortSessions(
        current.map((session) => {
          if (session.id !== sessionId) {
            return session;
          }
          return {
            ...session,
            updatedAt: Date.now(),
            messages: session.messages.map((message) => (message.id === messageId ? updater(message) : message)),
          };
        }),
      ),
    );
  }

  async function checkApi() {
    try {
      const response = await fetch(`${normalizedApiBase()}/api/health`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      setConnection({label: "API connected", ok: true});
    } catch {
      setConnection({label: "API disconnected", ok: false});
    }
  }

  function createAndActivate(title = "新对话") {
    const session = newSession(title);
    setActiveSessionId(session.id);
    commitSessions((current) => [session, ...current]);
    return session;
  }

  function handleNewChat() {
    setActiveSessionId(null);
    setOpenMenuId(null);
    setSidebarOpen(false);
  }

  function handleSelectSession(sessionId) {
    setActiveSessionId(sessionId);
    setOpenMenuId(null);
    setSidebarOpen(false);
  }

  function handleRenameSession(sessionId) {
    const session = sessions.find((item) => item.id === sessionId);
    if (!session) {
      return;
    }
    const nextTitle = window.prompt("重命名会话", session.title || "新对话");
    if (nextTitle === null) {
      return;
    }
    const trimmed = nextTitle.trim();
    if (!trimmed) {
      return;
    }
    patchSession(sessionId, (item) => ({...item, title: trimmed.slice(0, 80), manualTitle: true}));
    setOpenMenuId(null);
  }

  function handleTogglePin(sessionId) {
    patchSession(sessionId, (session) => ({...session, pinnedAt: session.pinnedAt ? null : Date.now()}));
    setOpenMenuId(null);
  }

  function handleDeleteSession(sessionId) {
    const session = sessions.find((item) => item.id === sessionId);
    if (!session) {
      return;
    }
    if (!window.confirm(`删除会话「${session.title || "新对话"}」？`)) {
      return;
    }
    const remaining = sessions.filter((item) => item.id !== sessionId);
    setSessions(sortSessions(remaining));
    if (activeSessionId === sessionId) {
      setActiveSessionId(remaining[0]?.id || null);
    }
    setOpenMenuId(null);
  }

  async function uploadFiles(fileList) {
    const files = [...fileList];
    if (!files.length) {
      return;
    }

    const session = activeSession || createAndActivate("上传文件");
    const formData = new FormData();
    formData.append("user_id", userId);
    formData.append("session_id", session.id);
    for (const file of files) {
      formData.append("files", file);
    }

    setUploading(true);
    try {
      const response = await fetch(`${normalizedApiBase()}/api/uploads`, {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      patchSession(session.id, (item) => ({
        ...item,
        attachments: [...(item.attachments || []), ...(payload.files || [])],
      }));
    } catch (error) {
      setConnection({label: `Upload failed: ${error.message}`, ok: false});
    } finally {
      setUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }

  function hasDraggedFiles(event) {
    return [...(event.dataTransfer?.types || [])].includes("Files");
  }

  function handleWorkspaceDragEnter(event) {
    if (!hasDraggedFiles(event)) {
      return;
    }
    event.preventDefault();
    dragDepthRef.current += 1;
    setDraggingFiles(true);
  }

  function handleWorkspaceDragOver(event) {
    if (!hasDraggedFiles(event)) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setDraggingFiles(true);
  }

  function handleWorkspaceDragLeave(event) {
    if (!hasDraggedFiles(event)) {
      return;
    }
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) {
      setDraggingFiles(false);
    }
  }

  function handleWorkspaceDrop(event) {
    if (!hasDraggedFiles(event)) {
      return;
    }
    event.preventDefault();
    dragDepthRef.current = 0;
    setDraggingFiles(false);
    uploadFiles(event.dataTransfer.files);
  }

  async function submitMessage(message) {
    const text = message.trim();
    if (!text || submitting) {
      return;
    }

    const session = activeSession || createAndActivate(text);
    const history = (session.messages || []).slice(-20).map((item) => ({
      role: item.role === "agent" ? "assistant" : item.role,
      content: item.content,
    }));
    const usage = normalizeUsage(estimateUsage(text, history), previousUsageTotal(session));
    const userMessage = {id: crypto.randomUUID(), role: "user", content: text, createdAt: Date.now()};
    const assistantMessage = {
      id: crypto.randomUUID(),
      role: "agent",
      content: "",
      createdAt: Date.now(),
      status: "正在提交任务",
      streaming: true,
      usage,
    };

    patchSession(session.id, (item) => ({
      ...item,
      messages: [...item.messages, userMessage, assistantMessage],
    }));
    setInput("");
    setSubmitting(true);

    try {
      const response = await fetch(`${normalizedApiBase()}/api/chat`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          message: text,
          user_id: userId,
          session_id: session.id,
          history,
          attachments: session.attachments || [],
        }),
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      listenToTask(payload.events_url, session.id, assistantMessage.id);
    } catch (error) {
      updateMessage(session.id, assistantMessage.id, (messageItem) => ({
        ...messageItem,
        content: `请求失败：${error.message}`,
        status: "请求失败",
        streaming: false,
      }));
      setSubmitting(false);
    }
  }

  function listenToTask(eventsUrl, sessionId, messageId) {
    const source = new EventSource(`${normalizedApiBase()}${eventsUrl}`);
    const activeAgents = new Map();

    source.addEventListener("progress", (event) => {
      const payload = JSON.parse(event.data);
      const status = agentStatus(payload, activeAgents);
      updateMessage(sessionId, messageId, (messageItem) => ({...messageItem, status}));
    });

    source.addEventListener("thinking_delta", (event) => {
      const payload = JSON.parse(event.data);
      const amount = payload.data?.delta ? textSize(payload.data.delta) : Math.ceil((payload.data?.delta_length || 0) / 3);
      updateMessage(sessionId, messageId, (messageItem) => ({
        ...messageItem,
        usage: normalizeUsage({...messageItem.usage, internal: Number(messageItem.usage?.internal || 0) + amount}),
      }));
    });

    source.addEventListener("answer_delta", (event) => {
      const payload = JSON.parse(event.data);
      const delta = payload.data?.delta || "";
      updateMessage(sessionId, messageId, (messageItem) => {
        const content = `${messageItem.content || ""}${delta}`;
        return {
          ...messageItem,
          content,
          status: null,
          usage: normalizeUsage({...messageItem.usage, output: textSize(content)}),
        };
      });
    });

    source.addEventListener("result", (event) => {
      const payload = JSON.parse(event.data);
      const answer = payload.data?.answer || JSON.stringify(payload.data || {}, null, 2);
      updateMessage(sessionId, messageId, (messageItem) => {
        const content = messageItem.content || answer;
        return {
          ...messageItem,
          content,
          status: null,
          streaming: false,
          usage: normalizeUsage({...messageItem.usage, output: textSize(content)}),
        };
      });
    });

    source.addEventListener("error", (event) => {
      const payload = event.data ? JSON.parse(event.data) : {status: "SSE connection failed"};
      updateMessage(sessionId, messageId, (messageItem) => ({
        ...messageItem,
        content: messageItem.content || `请求失败：${payload.status}`,
        status: payload.status,
        streaming: false,
      }));
      setSubmitting(false);
      source.close();
    });

    source.addEventListener("end", () => {
      updateMessage(sessionId, messageId, (messageItem) => ({
        ...messageItem,
        status: null,
        streaming: false,
      }));
      setSubmitting(false);
      source.close();
      checkApi();
    });
  }

  return (
    <div className={`app-shell ${sidebarOpen ? "sidebar-open" : ""}`}>
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        openMenuId={openMenuId}
        onNewChat={handleNewChat}
        onSelectSession={handleSelectSession}
        onToggleMenu={(sessionId) => setOpenMenuId(openMenuId === sessionId ? null : sessionId)}
        onRename={handleRenameSession}
        onTogglePin={handleTogglePin}
        onDelete={handleDeleteSession}
        apiBase={apiBase}
        onApiBaseChange={setApiBase}
        onCheckApi={checkApi}
      />

      <main
        className={`workspace ${draggingFiles ? "file-dragging" : ""}`}
        onClick={() => openMenuId && setOpenMenuId(null)}
        onDragEnter={handleWorkspaceDragEnter}
        onDragOver={handleWorkspaceDragOver}
        onDragLeave={handleWorkspaceDragLeave}
        onDrop={handleWorkspaceDrop}
      >
        <Topbar connection={connection} onMenu={() => setSidebarOpen(true)} activeTitle={activeSession?.title} />
        <section className="message-scroll" ref={messagesRef}>
          {!activeSession || !activeSession.messages.length ? (
            <EmptyState onPick={submitMessage} />
          ) : (
            <MessageList messages={activeSession.messages} apiBase={normalizedApiBase()} />
          )}
        </section>
        <Composer
          value={input}
          onChange={setInput}
          onSubmit={submitMessage}
          disabled={submitting}
          uploading={uploading}
          attachments={activeSession?.attachments || []}
          onUploadClick={() => fileInputRef.current?.click()}
          onFiles={uploadFiles}
          fileInputRef={fileInputRef}
        />
        {draggingFiles ? <FileDropOverlay uploading={uploading} /> : null}
      </main>
    </div>
  );
}

function Sidebar({
  sessions,
  activeSessionId,
  openMenuId,
  onNewChat,
  onSelectSession,
  onToggleMenu,
  onRename,
  onTogglePin,
  onDelete,
  apiBase,
  onApiBaseChange,
  onCheckApi,
}) {
  const [menuPosition, setMenuPosition] = React.useState(null);
  const activeMenuSession = sessions.find((session) => session.id === openMenuId) || null;

  React.useEffect(() => {
    if (!openMenuId) {
      setMenuPosition(null);
    }
  }, [openMenuId]);

  function handleToggleMenu(event, sessionId) {
    event.stopPropagation();
    if (openMenuId === sessionId) {
      setMenuPosition(null);
    } else {
      setMenuPosition(getConversationMenuPosition(event.currentTarget));
    }
    onToggleMenu(sessionId);
  }

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">O</div>
        <div>
          <strong>OpsAgent</strong>
          <span>Agent workspace</span>
        </div>
      </div>

      <button className="new-chat" type="button" onClick={onNewChat}>
        <span>+</span>
        新对话
      </button>

      <div className="conversation-list">
        {sessions.length === 0 ? (
          <div className="empty-conversations">还没有历史对话</div>
        ) : (
          groupedSessions(sessions).map((group) => (
            <section className="conversation-group" key={group.label}>
              <h2>{group.label}</h2>
              {group.items.map((session) => (
                <div className={`conversation-row ${session.id === activeSessionId ? "active" : ""}`} key={session.id}>
                  <button className="conversation-title" type="button" onClick={() => onSelectSession(session.id)}>
                    {session.title || "新对话"}
                  </button>
                  <button
                    className="conversation-more"
                    type="button"
                    aria-label="会话操作"
                    onClick={(event) => handleToggleMenu(event, session.id)}
                  >
                    <span />
                    <span />
                    <span />
                  </button>
                  {openMenuId === session.id ? (
                    <div className="conversation-menu" onClick={(event) => event.stopPropagation()}>
                      <button type="button" onClick={() => onRename(session.id)}>
                        <PencilIcon />
                        重命名
                      </button>
                      <button type="button" onClick={() => onTogglePin(session.id)}>
                        <PinIcon />
                        {session.pinnedAt ? "取消置顶" : "置顶"}
                      </button>
                      <button className="danger" type="button" onClick={() => onDelete(session.id)}>
                        <TrashIcon />
                        删除
                      </button>
                    </div>
                  ) : null}
                </div>
              ))}
            </section>
          ))
        )}
      </div>

      <div className="sidebar-settings">
        <label htmlFor="apiBase">API endpoint</label>
        <div className="api-row">
          <input
            id="apiBase"
            value={apiBase}
            onBlur={onCheckApi}
            onChange={(event) => onApiBaseChange(event.target.value)}
          />
          <button type="button" onClick={onCheckApi}>
            检查
          </button>
        </div>
      </div>
      {activeMenuSession && menuPosition ? (
        <ConversationMenuPortal
          session={activeMenuSession}
          position={menuPosition}
          onRename={onRename}
          onTogglePin={onTogglePin}
          onDelete={onDelete}
        />
      ) : null}
    </aside>
  );
}

function getConversationMenuPosition(anchor) {
  const rect = anchor.getBoundingClientRect();
  const left = Math.max(12, Math.min(rect.right - MENU_WIDTH, window.innerWidth - MENU_WIDTH - 12));
  const opensUp = rect.bottom + 8 + MENU_HEIGHT > window.innerHeight - 12;
  const top = opensUp ? Math.max(12, rect.top - MENU_HEIGHT - 8) : rect.bottom + 8;
  return {left, top};
}

function ConversationMenuPortal({session, position, onRename, onTogglePin, onDelete}) {
  if (typeof document === "undefined") {
    return null;
  }

  return createPortal(
    <div
      className="conversation-menu conversation-menu-floating"
      style={{left: `${position.left}px`, top: `${position.top}px`}}
      onClick={(event) => event.stopPropagation()}
    >
      <button type="button" onClick={() => onRename(session.id)}>
        <PencilIcon />
        重命名
      </button>
      <button type="button" onClick={() => onTogglePin(session.id)}>
        <PinIcon />
        {session.pinnedAt ? "取消置顶" : "置顶"}
      </button>
      <button className="danger" type="button" onClick={() => onDelete(session.id)}>
        <TrashIcon />
        删除
      </button>
    </div>,
    document.body,
  );
}

function Topbar({connection, onMenu, activeTitle}) {
  return (
    <header className="topbar">
      <button className="mobile-menu" type="button" onClick={onMenu} aria-label="打开侧边栏">
        <span />
        <span />
      </button>
      <div className="topbar-title">
        <strong>{activeTitle || ASSISTANT_NAME}</strong>
        <span className={connection.ok ? "ok" : "bad"}>{connection.label}</span>
      </div>
    </header>
  );
}

function FileDropOverlay({uploading}) {
  return (
    <div className="file-drop-overlay" aria-hidden="true">
      <div className="file-drop-card">
        <strong>{uploading ? "正在上传" : "松开上传文件"}</strong>
        <span>文件会加入当前对话并先完成 intake。</span>
      </div>
    </div>
  );
}

function EmptyState({onPick}) {
  return (
    <div className="empty-state">
      <div className="empty-card">
        <p className="eyebrow">OpsAgent</p>
        <h1>今天想处理什么？</h1>
        <p>直接聊天即可。需要专门能力时，后端会自动选择并执行 skill。</p>
      </div>
      <div className="suggestions">
        {suggestions.map((item) => (
          <button key={item} type="button" onClick={() => onPick(item)}>
            {item}
          </button>
        ))}
      </div>
    </div>
  );
}

function MessageList({messages, apiBase}) {
  let cumulativeBase = 0;
  return (
    <div className="thread">
      {messages.map((message) => {
        const usage = message.role === "agent" && message.usage ? normalizeUsage(message.usage, cumulativeBase) : null;
        if (usage) {
          cumulativeBase += usage.total;
        }
        return <MessageTurn key={message.id} message={{...message, usage}} apiBase={apiBase} />;
      })}
    </div>
  );
}

function MessageTurn({message, apiBase}) {
  const isUser = message.role === "user";
  const bubbleClass = `bubble ${!isUser && message.status && !message.content ? "thinking-only" : ""}`;
  return (
    <article className={`message-turn ${isUser ? "user" : "agent"}`}>
      {!isUser ? <div className="avatar">Ops</div> : null}
      <div className="message-stack">
        <div className={bubbleClass}>
          {!isUser && message.status ? <ThinkingPill text={message.status} /> : null}
          {isUser ? (
            <p>{message.content}</p>
          ) : message.content ? (
            <div className="markdown-body" dangerouslySetInnerHTML={{__html: renderMarkdown(message.content, apiBase)}} />
          ) : null}
        </div>
        {!isUser ? (
          <div className="message-meta">
            {message.usage ? <span>{usageLabel(message.usage)}</span> : null}
            <CopyButton text={message.content} />
          </div>
        ) : null}
      </div>
    </article>
  );
}

function ThinkingPill({text}) {
  return (
    <div className="thinking-pill">
      <span>{text}</span>
    </div>
  );
}

function Composer({
  value,
  onChange,
  onSubmit,
  disabled,
  uploading,
  attachments,
  onUploadClick,
  onFiles,
  fileInputRef,
}) {
  const textareaRef = React.useRef(null);

  React.useEffect(() => {
    const node = textareaRef.current;
    if (!node) {
      return;
    }
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 180)}px`;
  }, [value]);

  return (
    <div className="composer-shell">
      {attachments.length ? (
        <div className="attachment-tray">
          {attachments.map((file) => (
            <div className="attachment-chip" key={file.file_id} title={file.path || file.filename}>
              <span>{file.filename}</span>
              <small>{formatBytes(file.size)}</small>
            </div>
          ))}
        </div>
      ) : null}
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit(value);
        }}
      >
        <button className="tool-button" type="button" onClick={onUploadClick} disabled={uploading} aria-label="上传文件">
          {uploading ? "…" : "+"}
        </button>
        <input ref={fileInputRef} type="file" multiple hidden onChange={(event) => onFiles(event.target.files)} />
        <textarea
          ref={textareaRef}
          value={value}
          rows={1}
          placeholder="给 OpsAgent 发消息"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              onSubmit(value);
            }
          }}
        />
        <button className="send-button" type="submit" disabled={disabled || !value.trim()} aria-label="发送">
          <SendIcon />
        </button>
      </form>
      <p className="composer-hint">OpsAgent 可能会犯错，重要结果请核验。</p>
    </div>
  );
}

function CopyButton({text}) {
  const [copied, setCopied] = React.useState(false);

  async function copy() {
    if (!text) {
      return;
    }
    await navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

  return (
    <button className="copy-button" type="button" onClick={copy}>
      {copied ? "已复制" : "复制"}
    </button>
  );
}

function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 12h14" />
      <path d="m13 6 6 6-6 6" />
    </svg>
  );
}

function PencilIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </svg>
  );
}

function PinIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="m14 4 6 6-3 1-4 7-2-2-5 4 4-5-2-2 7-4Z" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="M19 6l-1 14H6L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </svg>
  );
}

createRoot(document.getElementById("root")).render(<App />);
