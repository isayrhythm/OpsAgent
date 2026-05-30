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
const UI_DELTA_INITIAL_REVEAL_MS = 120;
const UI_DELTA_STEP_REVEAL_MS = 460;

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
  "sup",
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
  "COLD1 是什么基因？",
  "LOC_Os04g54860 有突变体种子吗？",
  "LOC_Os07g48050 可能跟哪些性状相关？",
  "水稻耐盐相关基因有哪些？",
  "HY2 的功能研究路径是什么？",
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
    webSearchEnabled: Boolean(session.webSearchEnabled),
    messages: Array.isArray(session.messages)
      ? session.messages.map((message) => {
          const taskId = String(message.taskId || "");
          const eventsUrl = String(message.eventsUrl || "");
          const resumable = Boolean(message.streaming && taskId && eventsUrl);
          const disconnected = Boolean(message.streaming && !resumable && !message.content);
          return {
            id: message.id || crypto.randomUUID(),
            role: message.role === "assistant" ? "agent" : message.role,
            content: String(message.content || ""),
            contextContent: String(message.contextContent || message.content || ""),
            createdAt: Number(message.createdAt || Date.now()),
            usage: message.usage,
            uiBlocks: Array.isArray(message.uiBlocks) ? message.uiBlocks : [],
            webSources: Array.isArray(message.webSources) ? message.webSources : [],
            status: message.status || (disconnected ? "任务连接已中断，请重新发送" : null),
            streaming: resumable,
            taskId: resumable ? taskId : null,
            eventsUrl: resumable ? eventsUrl : null,
          };
        })
      : [],
    attachments: Array.isArray(session.attachments) ? session.attachments : [],
    detachedFiles: Array.isArray(session.detachedFiles) ? session.detachedFiles : [],
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

function newSession(title = "新对话", webSearchEnabled = false) {
  const now = Date.now();
  return {
    id: crypto.randomUUID(),
    title: title ? title.slice(0, 42) : "新对话",
    manualTitle: false,
    createdAt: now,
    updatedAt: now,
    pinnedAt: null,
    webSearchEnabled,
    messages: [],
    attachments: [],
    detachedFiles: [],
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

function sourceMapFromSources(sources = []) {
  if (!Array.isArray(sources) || !sources.length) {
    return new Map();
  }
  return new Map(
    sources
      .filter((source) => source?.index && source?.url)
      .map((source) => [String(source.index), String(source.url)])
  );
}

function createCitationNode(documentRef, index, url) {
  const link = documentRef.createElement("a");
  link.href = url;
  link.textContent = `[${index}]`;
  link.className = "citation-link";
  link.target = "_blank";
  link.rel = "noreferrer noopener";
  link.title = "打开搜索来源";
  return link;
}

function linkCitationTextNodes(root, sources = []) {
  const sourceMap = sourceMapFromSources(sources);
  if (!sourceMap.size) {
    return;
  }
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent || parent.closest("a, code, pre")) {
        return NodeFilter.FILTER_REJECT;
      }
      return /(?:\[\^?\d+(?:\s*[,，]\s*\d+)*\]|【\d+(?:\s*[,，]\s*\d+)*】)/.test(node.nodeValue || "")
        ? NodeFilter.FILTER_ACCEPT
        : NodeFilter.FILTER_REJECT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) {
    nodes.push(walker.currentNode);
  }

  for (const node of nodes) {
    const text = node.nodeValue || "";
    const fragment = document.createDocumentFragment();
    let cursor = 0;
    const citationPattern = /(\[\^?\d+(?:\s*[,，]\s*\d+)*\]|【\d+(?:\s*[,，]\s*\d+)*】)/g;
    for (const match of text.matchAll(citationPattern)) {
      const start = match.index || 0;
      if (start > cursor) {
        fragment.append(document.createTextNode(text.slice(cursor, start)));
      }
      const indexes = match[0].match(/\d+/g) || [];
      let linked = false;
      for (const index of indexes) {
        const url = sourceMap.get(index);
        if (!url) {
          continue;
        }
        fragment.append(createCitationNode(document, index, url));
        linked = true;
      }
      if (!linked) {
        fragment.append(document.createTextNode(match[0]));
      }
      cursor = start + match[0].length;
    }
    if (cursor < text.length) {
      fragment.append(document.createTextNode(text.slice(cursor)));
    }
    node.replaceWith(fragment);
  }
}

function sanitizeMarkdown(html, apiBase = DEFAULT_API_BASE, sources = []) {
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
        if (/^\[?\^?\d+\]?$/.test(element.textContent.trim())) {
          element.classList.add("citation-link");
          element.setAttribute("title", "打开搜索来源");
        }
      }
    }
  }
  linkCitationTextNodes(template.content, sources);
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
        .replace(/__([^_\n]*?\S[^_\n]*?)\s*__/g, (_match, text) => `<strong>${text.trimEnd()}</strong>`)
        .replace(
          /\b([A-Za-z][A-Za-z0-9_.-]{1,48})\s*[（(](https?:\/\/[^\s（）)]+)[）)]/g,
          (_match, label, url) => `[${label}](${url})`,
        )
        .replace(
          /\b([A-Za-z][A-Za-z0-9_.-]{0,80})\^\{([A-Za-z0-9_.-]{1,24})\}/g,
          (_match, gene, label) => `${gene}<sup>${label}</sup>`,
        );
    })
    .join("");
}

function isPdfAttachment(file) {
  const filename = String(file?.filename || "").toLowerCase();
  const contentType = String(file?.content_type || file?.contentType || "").toLowerCase();
  return filename.endsWith(".pdf") || contentType.includes("pdf") || file?.intake?.data_type === "pdf_document";
}

function pdfContextForHistory(files = []) {
  const sections = files
    .filter(isPdfAttachment)
    .map((file) => {
      const intake = file.intake || {};
      if (intake.status !== "ready") {
        return [
          `[PDF attachment: ${file.filename}]`,
          `status: ${intake.status || "unknown"}`,
          `reason: ${intake.reason || "PDF text extraction failed"}`,
        ].join("\n");
      }
      return [
        `[PDF attachment: ${file.filename}]`,
        `path: ${file.path || intake.source_path || ""}`,
        `title: ${intake.title || ""}`,
        `pages: ${intake.parsed_pages || "?"}/${intake.page_count || "?"}`,
        `text_file: ${intake.text_file || ""}`,
        "text_excerpt:",
        String(intake.text_excerpt || ""),
      ].join("\n");
    });
  if (!sections.length) {
    return "";
  }
  return `PDF 文献上下文（由上传文件解析得到，后续回答可引用；不要编造未出现的信息）：\n${sections.join("\n\n")}`;
}

function renderMarkdown(content, apiBase, sources) {
  return sanitizeMarkdown(marked.parse(normalizeMarkdown(content)), apiBase, sources);
}

function applyUiDelta(blocks, delta) {
  const current = Array.isArray(blocks) ? blocks : [];
  if (delta?.action === "start" && delta.block?.id) {
    const block = {...delta.block, steps: []};
    return [...current.filter((item) => item.id !== block.id), block];
  }
  if (delta?.action === "step" && delta.block_id && delta.step) {
    return current.map((block) => {
      if (block.id !== delta.block_id) {
        return block;
      }
      const stepKey = String(delta.step.step || "");
      const steps = [...(block.steps || []).filter((step) => String(step.step || "") !== stepKey), delta.step];
      return {...block, steps};
    });
  }
  return current;
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
  const [uploadState, setUploadState] = React.useState({sessionId: null, status: "", running: false});
  const [draftWebSearchEnabled, setDraftWebSearchEnabled] = React.useState(false);
  const [draggingFiles, setDraggingFiles] = React.useState(false);
  const [userId] = React.useState(loadUserId);
  const messagesRef = React.useRef(null);
  const fileInputRef = React.useRef(null);
  const dragDepthRef = React.useRef(0);
  const uploadClearTimerRef = React.useRef(null);
  const sourceCacheRef = React.useRef(new Map());
  const taskSourcesRef = React.useRef(new Map());
  const uiDeltaQueuesRef = React.useRef(new Map());
  const uiDeltaTimersRef = React.useRef(new Map());

  const activeSession = React.useMemo(
    () => sessions.find((session) => session.id === activeSessionId) || null,
    [sessions, activeSessionId],
  );
  const webSearchEnabled = activeSession ? Boolean(activeSession.webSearchEnabled) : draftWebSearchEnabled;
  const activeUploadStatus = uploadState.sessionId === activeSessionId ? uploadState.status : "";
  const activeUploading = uploadState.sessionId === activeSessionId && uploadState.running;
  const activeTaskMessage = [...(activeSession?.messages || [])]
    .reverse()
    .find((message) => message.role === "agent" && message.streaming && message.taskId && message.eventsUrl);

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

  React.useEffect(
    () => () => {
      for (const timer of uiDeltaTimersRef.current.values()) {
        window.clearTimeout(timer);
      }
      if (uploadClearTimerRef.current) {
        window.clearTimeout(uploadClearTimerRef.current);
      }
      for (const source of taskSourcesRef.current.values()) {
        source.close();
      }
      taskSourcesRef.current.clear();
      uiDeltaTimersRef.current.clear();
      uiDeltaQueuesRef.current.clear();
    },
    [],
  );

  React.useEffect(() => {
    const node = messagesRef.current;
    if (!node) {
      return;
    }
    requestAnimationFrame(() => {
      node.scrollTop = node.scrollHeight;
    });
  }, [activeSession?.messages, activeSessionId]);

  React.useEffect(() => {
    for (const session of sessions) {
      for (const message of session.messages || []) {
        if (!message.streaming || !message.eventsUrl || !message.taskId || taskSourcesRef.current.has(message.id)) {
          continue;
        }
        updateMessage(session.id, message.id, (messageItem) => ({
          ...messageItem,
          content: "",
          status: "正在恢复任务",
          uiBlocks: [],
          webSources: [],
          usage: normalizeUsage({...messageItem.usage, internal: 0, output: 0}),
        }));
        listenToTask(message.eventsUrl, session.id, message.id);
      }
    }
  }, [sessions, apiBase]);

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

  function setUploadForSession(sessionId, status, running) {
    if (uploadClearTimerRef.current) {
      window.clearTimeout(uploadClearTimerRef.current);
      uploadClearTimerRef.current = null;
    }
    setUploadState({sessionId, status, running});
  }

  function clearUploadForSessionSoon(sessionId) {
    if (uploadClearTimerRef.current) {
      window.clearTimeout(uploadClearTimerRef.current);
    }
    uploadClearTimerRef.current = window.setTimeout(() => {
      setUploadState((current) =>
        current.sessionId === sessionId && !current.running ? {sessionId: null, status: "", running: false} : current,
      );
      uploadClearTimerRef.current = null;
    }, 3000);
  }

  function uiDeltaQueueKey(sessionId, messageId) {
    return `${sessionId}:${messageId}`;
  }

  function scheduleUiDeltaReveal(key, sessionId, messageId, delay) {
    if (uiDeltaTimersRef.current.has(key)) {
      return;
    }
    const timer = window.setTimeout(() => {
      uiDeltaTimersRef.current.delete(key);
      const queue = uiDeltaQueuesRef.current.get(key) || [];
      const payload = queue.shift();
      if (!payload) {
        uiDeltaQueuesRef.current.delete(key);
        return;
      }
      updateMessage(sessionId, messageId, (messageItem) => ({
        ...messageItem,
        status: messageItem.content ? null : payload.status || messageItem.status,
        uiBlocks: applyUiDelta(messageItem.uiBlocks, payload.data),
      }));
      if (queue.length) {
        scheduleUiDeltaReveal(key, sessionId, messageId, UI_DELTA_STEP_REVEAL_MS);
      } else {
        uiDeltaQueuesRef.current.delete(key);
      }
    }, delay);
    uiDeltaTimersRef.current.set(key, timer);
  }

  function queueUiDelta(sessionId, messageId, payload) {
    const key = uiDeltaQueueKey(sessionId, messageId);
    const queue = uiDeltaQueuesRef.current.get(key) || [];
    queue.push(payload);
    uiDeltaQueuesRef.current.set(key, queue);
    scheduleUiDeltaReveal(key, sessionId, messageId, UI_DELTA_INITIAL_REVEAL_MS);
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
    const session = newSession(title, draftWebSearchEnabled);
    setActiveSessionId(session.id);
    commitSessions((current) => [session, ...current]);
    return session;
  }

  function handleNewChat() {
    setActiveSessionId(null);
    setOpenMenuId(null);
    setSidebarOpen(false);
    setDraftWebSearchEnabled(false);
  }

  function handleSelectSession(sessionId) {
    setActiveSessionId(sessionId);
    setOpenMenuId(null);
    setSidebarOpen(false);
  }

  function handleToggleWebSearch() {
    if (!activeSession) {
      setDraftWebSearchEnabled((enabled) => !enabled);
      return;
    }
    patchSession(activeSession.id, (session) => ({
      ...session,
      webSearchEnabled: !session.webSearchEnabled,
    }));
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

    setUploadForSession(session.id, "正在上传文件", true);
    try {
      const response = await fetch(`${normalizedApiBase()}/api/uploads`, {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      setUploadForSession(session.id, "文件已保存，等待 intake", true);
      await listenToUploadIntake(payload.events_url, session.id);
    } catch (error) {
      setUploadForSession(session.id, `上传失败：${error.message}`, false);
      setConnection({label: `Upload failed: ${error.message}`, ok: false});
    } finally {
      setUploadState((current) => (current.sessionId === session.id ? {...current, running: false} : current));
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }

  function listenToUploadIntake(eventsUrl, sessionId) {
    return new Promise((resolve, reject) => {
      const source = new EventSource(`${normalizedApiBase()}${eventsUrl}`);
      let receivedResult = false;

      source.addEventListener("progress", (event) => {
        const payload = JSON.parse(event.data);
        setUploadForSession(sessionId, payload.status || "正在 intake 上传文件", true);
      });

      source.addEventListener("result", (event) => {
        const payload = JSON.parse(event.data);
        const files = payload.data?.files || [];
        const uploadedNames = new Set(files.map((file) => file.filename));
        patchSession(sessionId, (item) => ({
          ...item,
          attachments: [...(item.attachments || []), ...files],
          detachedFiles: (item.detachedFiles || []).filter((file) => !uploadedNames.has(file.filename)),
        }));
        receivedResult = true;
        setUploadForSession(sessionId, payload.status || "上传文件 intake 完成", false);
        clearUploadForSessionSoon(sessionId);
      });

      source.addEventListener("error", (event) => {
        const payload = event.data ? JSON.parse(event.data) : {status: "Upload intake SSE connection failed"};
        source.close();
        reject(new Error(payload.status));
      });

      source.addEventListener("end", () => {
        source.close();
        if (!receivedResult) {
          reject(new Error("Upload intake ended without files"));
          return;
        }
        resolve();
      });
    });
  }

  function removeAttachment(sessionId, fileId) {
    patchSession(sessionId, (item) => {
      const removed = (item.attachments || []).find((file) => file.file_id === fileId);
      const detached = removed
        ? [...(item.detachedFiles || []).filter((file) => file.file_id !== fileId), {file_id: removed.file_id, filename: removed.filename}]
        : item.detachedFiles || [];
      return {
        ...item,
        attachments: (item.attachments || []).filter((file) => file.file_id !== fileId),
        detachedFiles: detached.slice(-12),
      };
    });
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
    if (!text || submitting || activeUploading) {
      return;
    }

    const session = activeSession || createAndActivate(text);
    const history = (session.messages || [])
      .filter((item) => String(item.content || "").trim())
      .slice(-20)
      .map((item) => ({
        role: item.role === "agent" ? "assistant" : item.role,
        content: item.contextContent || item.content,
      }));
    const usage = normalizeUsage(estimateUsage(text, history), previousUsageTotal(session));
    const requestAttachments = session.attachments || [];
    const pdfContext = pdfContextForHistory(requestAttachments);
    const userMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      contextContent: pdfContext ? `${text}\n\n${pdfContext}` : text,
      createdAt: Date.now(),
    };
    const assistantMessage = {
      id: crypto.randomUUID(),
      role: "agent",
      content: "",
      uiBlocks: [],
      createdAt: Date.now(),
      status: "正在提交任务",
      streaming: true,
      usage,
    };

    patchSession(session.id, (item) => ({
      ...item,
      messages: [...item.messages, userMessage, assistantMessage],
      attachments: (item.attachments || []).filter((file) => !isPdfAttachment(file)),
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
          attachments: requestAttachments,
          detached_files: session.detachedFiles || [],
          web_search: webSearchEnabled,
        }),
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      updateMessage(session.id, assistantMessage.id, (messageItem) => ({
        ...messageItem,
        taskId: payload.task_id,
        eventsUrl: payload.events_url,
      }));
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
    if (taskSourcesRef.current.has(messageId)) {
      return;
    }
    const source = new EventSource(`${normalizedApiBase()}${eventsUrl}`);
    const activeAgents = new Map();
    taskSourcesRef.current.set(messageId, source);
    sourceCacheRef.current.delete(messageId);

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
        const cachedSources = sourceCacheRef.current.get(messageId);
        return {
          ...messageItem,
          content,
          webSources: cachedSources || messageItem.webSources || [],
          status: null,
          usage: normalizeUsage({...messageItem.usage, output: textSize(content)}),
        };
      });
    });

    source.addEventListener("ui_delta", (event) => {
      const payload = JSON.parse(event.data);
      queueUiDelta(sessionId, messageId, payload);
    });

    source.addEventListener("source_delta", (event) => {
      const payload = JSON.parse(event.data);
      const sources = payload.data?.sources || [];
      sourceCacheRef.current.set(messageId, sources);
      updateMessage(sessionId, messageId, (messageItem) => ({
        ...messageItem,
        webSources: sources,
        status: messageItem.content ? null : payload.status || messageItem.status,
      }));
    });

    source.addEventListener("result", (event) => {
      const payload = JSON.parse(event.data);
      const answer = payload.data?.answer || JSON.stringify(payload.data || {}, null, 2);
      if (payload.data?.web_sources) {
        sourceCacheRef.current.set(messageId, payload.data.web_sources);
      }
      updateMessage(sessionId, messageId, (messageItem) => {
        const content = messageItem.content || answer;
        return {
          ...messageItem,
          content,
          webSources: payload.data?.web_sources || sourceCacheRef.current.get(messageId) || messageItem.webSources || [],
          status: null,
          streaming: false,
          usage: normalizeUsage({...messageItem.usage, output: textSize(content)}),
        };
      });
    });

    source.addEventListener("cancelled", (event) => {
      const payload = JSON.parse(event.data);
      updateMessage(sessionId, messageId, (messageItem) => ({
        ...messageItem,
        content: messageItem.content || "已停止当前任务。",
        status: payload.status || null,
        streaming: false,
      }));
      setSubmitting(false);
      taskSourcesRef.current.delete(messageId);
      source.close();
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
      taskSourcesRef.current.delete(messageId);
      source.close();
    });

    source.addEventListener("end", () => {
      updateMessage(sessionId, messageId, (messageItem) => ({
        ...messageItem,
        status: null,
        streaming: false,
      }));
      setSubmitting(false);
      taskSourcesRef.current.delete(messageId);
      source.close();
      checkApi();
    });
  }

  async function cancelTask() {
    if (!activeSession || !activeTaskMessage?.taskId) {
      return;
    }
    updateMessage(activeSession.id, activeTaskMessage.id, (messageItem) => ({
      ...messageItem,
      status: "正在停止任务",
    }));
    try {
      const response = await fetch(`${normalizedApiBase()}/api/tasks/${activeTaskMessage.taskId}/cancel`, {method: "POST"});
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
    } catch (error) {
      updateMessage(activeSession.id, activeTaskMessage.id, (messageItem) => ({
        ...messageItem,
        status: `停止失败：${error.message}`,
      }));
    }
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
          disabled={submitting || activeUploading || Boolean(activeTaskMessage)}
          canCancel={Boolean(activeTaskMessage)}
          onCancel={cancelTask}
          uploading={activeUploading}
          uploadStatus={activeUploadStatus}
          webSearchEnabled={webSearchEnabled}
          onToggleWebSearch={handleToggleWebSearch}
          attachments={activeSession?.attachments || []}
          onUploadClick={() => fileInputRef.current?.click()}
          onFiles={uploadFiles}
          onRemoveFile={(fileId) => activeSession && removeAttachment(activeSession.id, fileId)}
          fileInputRef={fileInputRef}
        />
        {draggingFiles ? <FileDropOverlay uploading={activeUploading} /> : null}
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
  const bubbleClass = `bubble ${!isUser && message.status && !message.content && !message.uiBlocks?.length ? "thinking-only" : ""}`;
  return (
    <article className={`message-turn ${isUser ? "user" : "agent"}`}>
      {!isUser ? <div className="avatar">Ops</div> : null}
      <div className="message-stack">
        <div className={bubbleClass}>
          {!isUser && message.status ? <ThinkingPill text={message.status} /> : null}
          {isUser ? (
            <p>{message.content}</p>
          ) : message.content ? (
            <div
              className="markdown-body"
              dangerouslySetInnerHTML={{__html: renderMarkdown(message.content, apiBase, message.webSources)}}
            />
          ) : null}
          {!isUser && message.uiBlocks?.length ? <ResearchPathBlocks blocks={message.uiBlocks} /> : null}
        </div>
        <div className={`message-meta ${isUser ? "user-meta" : ""}`}>
          {!isUser && message.usage ? <span>{usageLabel(message.usage)}</span> : null}
          <CopyButton text={message.content} />
        </div>
      </div>
    </article>
  );
}

function ResearchPathBlocks({blocks}) {
  return (
    <div className="research-paths">
      {blocks
        .filter((block) => block.type === "gene_function_research_path")
        .map((block) => (
          <ResearchPathBlock key={block.id} block={block} />
        ))}
    </div>
  );
}

function ResearchPathBlock({block}) {
  return (
    <section className="research-path">
      <header className="research-path-head">
        <div>
          <p className="research-path-kicker">Gene Function Research Path</p>
          <h3>{block.gene_id}</h3>
        </div>
        {block.paper_id ? <span>{block.paper_id}</span> : null}
      </header>
      <p className="research-path-title">{block.title}</p>
      <div className="research-steps">
        {(block.steps || []).map((step) => (
          <ResearchStep key={`${block.id}-${step.step}`} step={step} />
        ))}
      </div>
    </section>
  );
}

function ResearchStep({step}) {
  return (
    <article className="research-step">
      <div className="research-step-mark">
        <strong>{step.step}</strong>
      </div>
      <details className="research-step-card">
        <summary className="research-step-top">
          <h4>{step.stage_operation}</h4>
          {step.figures ? <span>{step.figures}</span> : null}
        </summary>
        <div className="research-step-details">
          <ResearchStepField label="Hypothesis" value={step.hypothesis} />
          <ResearchStepField label="Methods" value={step.methods} />
          <ResearchStepField label="Results" value={step.results} />
          <ResearchStepField label="Conclusion" value={step.step_conclusion} />
        </div>
      </details>
    </article>
  );
}

function ResearchStepField({label, value}) {
  if (!value) {
    return null;
  }
  return (
    <div className="research-step-field">
      <b>{label}</b>
      <p>{value}</p>
    </div>
  );
}

function ThinkingPill({text}) {
  if (String(text || "").includes("gene_phenotype_prediction")) {
    return <GenePredictorThinking retry={String(text || "").includes("重新")} />;
  }
  return (
    <div className="thinking-pill">
      <span>{text}</span>
    </div>
  );
}

function GenePredictorThinking() {
  return (
    <div className="gene-predictor-thinking" aria-label="正在运行图神经网络表型预测">
      <div className="gene-predictor-graph" aria-hidden="true">
        <svg className="gene-predictor-svg" viewBox="0 0 260 150" role="img">
          <g className="graph-edges">
            <line x1="55" y1="76" x2="122" y2="32" />
            <line x1="55" y1="76" x2="98" y2="122" />
            <line x1="122" y1="32" x2="198" y2="28" />
            <line x1="122" y1="32" x2="154" y2="78" />
            <line x1="154" y1="78" x2="198" y2="28" />
            <line x1="154" y1="78" x2="210" y2="82" />
            <line x1="154" y1="78" x2="190" y2="124" />
            <line x1="98" y1="122" x2="154" y2="78" />
            <line x1="98" y1="122" x2="190" y2="124" />
            <line x1="198" y1="28" x2="210" y2="82" />
            <line x1="210" y1="82" x2="190" y2="124" />
          </g>
          <g className="graph-flow-edges">
            <line className="flow-edge flow-edge-a" x1="55" y1="76" x2="190" y2="124" />
            <line className="flow-edge flow-edge-b" x1="55" y1="76" x2="198" y2="28" />
            <line className="flow-edge flow-edge-c" x1="98" y1="122" x2="210" y2="82" />
          </g>
          <g className="graph-nodes">
            <circle className="graph-node node-gene" cx="55" cy="76" r="14" />
            <circle className="graph-node node-a" cx="122" cy="32" r="10" />
            <circle className="graph-node node-b" cx="198" cy="28" r="13" />
            <circle className="graph-node node-c" cx="210" cy="82" r="13" />
            <circle className="graph-node node-d" cx="190" cy="124" r="13" />
            <circle className="graph-node node-e" cx="98" cy="122" r="12" />
            <circle className="graph-node node-f" cx="154" cy="78" r="13" />
            <circle className="graph-node node-g" cx="178" cy="62" r="10" />
          </g>
          <g className="graph-particles">
            <circle className="flow-particle particle-a" r="4" />
            <circle className="flow-particle particle-b" r="4" />
            <circle className="flow-particle particle-c" r="4" />
          </g>
        </svg>
      </div>
    </div>
  );
}

function Composer({
  value,
  onChange,
  onSubmit,
  disabled,
  canCancel,
  onCancel,
  uploading,
  uploadStatus,
  webSearchEnabled,
  onToggleWebSearch,
  attachments,
  onUploadClick,
  onFiles,
  onRemoveFile,
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
      {uploadStatus ? <p className={`upload-status ${uploading ? "running" : ""}`}>{uploadStatus}</p> : null}
      {attachments.length ? (
        <div className="attachment-tray">
          {attachments.map((file) => (
            <div className="attachment-chip" key={file.file_id} title={file.path || file.filename}>
              <span>{file.filename}</span>
              <small>{formatBytes(file.size)}</small>
              <button
                className="attachment-remove"
                type="button"
                onClick={() => onRemoveFile(file.file_id)}
                aria-label={`从当前对话卸载 ${file.filename}`}
                title="从当前对话卸载"
              >
                x
              </button>
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
        {canCancel ? (
          <button className="send-button stop-button" type="button" onClick={onCancel} aria-label="停止当前任务">
            <StopIcon />
          </button>
        ) : (
          <button className="send-button" type="submit" disabled={disabled || !value.trim()} aria-label="发送">
            <SendIcon />
          </button>
        )}
        <div className="composer-actions">
          <button
            className={`composer-mode ${webSearchEnabled ? "active" : ""}`}
            type="button"
            onClick={onToggleWebSearch}
            disabled={disabled}
            aria-pressed={webSearchEnabled}
          >
            <SearchIcon />
            网络搜索
          </button>
        </div>
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

function StopIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="7" y="7" width="10" height="10" rx="1.5" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <path d="m16.5 16.5 4 4" />
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
