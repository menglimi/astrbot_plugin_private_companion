import { renderDeliveryAudit } from "./delivery-audit.js?v=20260921-text-params-v4";

const bridge = window.AstrBotPluginPage || (() => {
  try {
    return window.parent && window.parent !== window
      ? window.parent.AstrBotPluginPage
      : null;
  } catch (error) {
    return null;
  }
})();
const LABELS = {
  ROLE: "ROLE",
  CONTENT: "内容",
  "原始记录": "原始记录",
  VALUE: "值",
  PLATFORM: "平台",
  MESSAGES: "消息数",
  UPDATED: "更新时间",
  TOKENS: "令牌数",
  REVISION: "REVISION",
  SNAPSHOTS: "快照",
  "高级字段": "高级字段",
  "NO EDITABLE FIELDS": "没有可编辑字段",
  think: "思考",
  encrypted: "加密签名",
  tool_calls: "工具调用",
  tool_call_id: "工具调用标识",
  reasoning: "推理过程",
  thinking: "思考过程",
  text: "文本",
  type: "类型",
  id: "标识",
  function: "函数",
  extra_content: "额外内容",

  OBJECT: "对象",
  INVALID: "无效",
  PART: "内容段",
};

const ROLE_LABELS = {
  system: "系统",
  user: "用户",
  assistant: "助手",
  tool: "工具",
  _checkpoint: "内部检查点",
};

const TYPE_LABELS = {
  text: "文本",
  think: "思考",
  image_url: "图片",
  audio_url: "音频",
  function: "工具调用",
  VALUE: "值",
  OBJECT: "对象",
};

function labelOf(value) {
  return value;
}

function roleLabel(value) {
  return value;
}

function typeLabel(value) {
  return value;
}

function reasonLabel(value) {
  return value === "before-edit" ? "编辑前备份" : value === "before-restore" ? "恢复前备份" : value || "未知原因";
}

const elements = {
  connection: document.querySelector(".connection-state"),
  connectionLabel: document.getElementById("connection-label"),
  pageTitle: document.getElementById("page-title"),
  pageDescription: document.getElementById("page-description"),
  search: document.getElementById("conversation-search"),
  conversationList: document.getElementById("conversation-list"),
  conversationTotal: document.getElementById("conversation-total"),
  previousPage: document.getElementById("previous-page"),
  nextPage: document.getElementById("next-page"),
  pageIndicator: document.getElementById("page-indicator"),
  emptyState: document.getElementById("empty-state"),
  editor: document.getElementById("editor"),
  conversationTitle: document.getElementById("conversation-title"),
  conversationUmo: document.getElementById("conversation-umo"),
  conversationMeta: document.getElementById("conversation-meta"),
  save: document.getElementById("save-button"),
  refresh: document.getElementById("refresh-button"),
  addMessage: document.getElementById("add-message-button"),
  discard: document.getElementById("discard-button"),
  health: document.getElementById("health-strip"),
  undoStrip: document.getElementById("undo-strip"),
  undoDelete: document.getElementById("undo-delete"),
  messageList: document.getElementById("message-list"),
  messagePager: document.getElementById("message-pager"),
  previousMessagePage: document.getElementById("previous-message-page"),
  nextMessagePage: document.getElementById("next-message-page"),
  messagePageIndicator: document.getElementById("message-page-indicator"),
  contextPanel: document.getElementById("context-panel"),
  snapshotsPanel: document.getElementById("snapshots-panel"),
  snapshotList: document.getElementById("snapshot-list"),
  snapshotConfig: document.getElementById("snapshot-config-state"),
  tabs: Array.from(document.querySelectorAll(".tab")),
  confirmDialog: document.getElementById("confirm-dialog"),
  confirmTitle: document.getElementById("confirm-title"),
  confirmMessage: document.getElementById("confirm-message"),
  confirmAccept: document.getElementById("confirm-accept"),
  toast: document.getElementById("toast"),
};

const state = {
  page: 1,
  pageSize: 30,
  total: 0,
  conversations: [],
  search: "",
  selected: null,
  revision: "",
  messages: [],
  messagePage: 1,
  messagePageSize: 30,
  dirty: false,
  busy: false,
  activeTab: "context",
  lastDeletion: null,
  invalidFields: new Set(),
  snapshots: [],
  autoSnapshotsEnabled: false,
  filterThinkingForLlm: false,
  userFilter: "",
};

let searchTimer = null;
let toastTimer = null;

function createElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function deepClone(value) {
  if (typeof structuredClone === "function") {
    return structuredClone(value);
  }
  return JSON.parse(JSON.stringify(value));
}

function formatDate(timestamp) {
  if (!timestamp) return "—";
  const milliseconds = timestamp > 10_000_000_000 ? timestamp : timestamp * 1000;
  return new Intl.DateTimeFormat(bridge.getLocale() || "zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(milliseconds));
}

function shortId(value, size = 10) {
  if (!value) return "—";
  return value.length > size ? `${value.slice(0, size)}…` : value;
}

function showToast(message) {
  window.clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  toastTimer = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 4200);
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function setBusy(busy) {
  state.busy = busy;
  elements.save.disabled = busy || !state.dirty;
  elements.refresh.disabled = busy;
  elements.addMessage.disabled = busy;
  elements.previousMessagePage.disabled = busy || state.messagePage <= 1;
  elements.nextMessagePage.disabled =
    busy || state.messagePage >= Math.max(1, Math.ceil(state.messages.length / state.messagePageSize));

  elements.discard.disabled = busy;
  elements.previousPage.disabled = busy || state.page <= 1;
  elements.nextPage.disabled =
    busy || state.page >= Math.max(1, Math.ceil(state.total / state.pageSize));
}

function markDirty() {
  state.dirty = true;
  elements.discard.hidden = false;
  elements.save.disabled = state.busy;
}

function resetDirty() {
  state.dirty = false;
  state.lastDeletion = null;
  state.invalidFields.clear();
  elements.discard.hidden = true;
  elements.save.disabled = true;
  elements.undoStrip.hidden = true;
}

function confirmAction(title, message, acceptLabel = "确认") {
  if (!elements.confirmDialog?.showModal) {
    return Promise.resolve(window.confirm(`${title}\n\n${message}`));
  }
  elements.confirmTitle.textContent = title;
  elements.confirmMessage.textContent = message;
  elements.confirmAccept.textContent = acceptLabel;
  elements.confirmDialog.returnValue = "";
  elements.confirmDialog.showModal();
  return new Promise((resolve) => {
    elements.confirmDialog.addEventListener(
      "close",
      () => resolve(elements.confirmDialog.returnValue === "confirm"),
      { once: true },
    );
  });
}

async function canDiscardLocalChanges() {
  if (!state.dirty) return true;
  return confirmAction(
    "放弃未保存修改？",
    "当前页面中的编辑和删除尚未写入 AstrBot。继续后这些本地修改会丢失。",
    "放弃修改",
  );
}

function renderLoading(container, text = "正在加载…") {
  container.replaceChildren(createElement("p", "loading-line", text));
}

async function loadConversations() {
  renderLoading(elements.conversationList);
  setBusy(true);
  try {
    const result = await bridge.apiGet("page/conversations", {
      page: state.page,
      page_size: state.pageSize,
      search: state.userFilter ? "" : state.search,
      exact_umo: state.userFilter,
    });
    state.conversations = Array.isArray(result.items) ? result.items : [];
    state.total = Number(result.total) || 0;
    state.page = Number(result.page) || 1;
    renderConversations();
    if (state.userFilter && !state.selected && state.conversations.length) {
      state.selected = state.conversations[0];
      renderConversations();
      await loadSelectedConversation();
    }
  } catch (error) {
    elements.conversationList.replaceChildren(
      createElement("p", "empty-list", `读取失败：${errorMessage(error)}`),
    );
    showToast(`读取对话列表失败：${errorMessage(error)}`);
  } finally {
    setBusy(false);
  }
}

function renderConversations() {
  elements.conversationList.replaceChildren();
  elements.conversationTotal.textContent = String(state.total);
  const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
  elements.pageIndicator.textContent = `${state.page} / ${totalPages}`;

  if (!state.conversations.length) {
    elements.conversationList.append(
      createElement("p", "empty-list", "没有匹配的对话。"),
    );
  }

  for (const conversation of state.conversations) {
    const button = createElement("button", "conversation-item");
    button.type = "button";
    if (
      state.selected?.conversationId === conversation.conversationId &&
      state.selected?.umo === conversation.umo
    ) {
      button.classList.add("is-active");
    }
    const meta = createElement(
      "span",
      "conversation-item-meta",
      `${conversation.platformId || "未知平台"} · ${conversation.umo}`,
    );
    meta.dataset.time = formatDate(conversation.updatedAt);
    button.append(
      createElement(
        "span",
        "conversation-item-title",
        conversation.displayName || conversation.title || "未命名对话",
      ),
      meta,
    );
    button.title = `${conversation.umo}\n${conversation.conversationId}`;
    button.addEventListener("click", () => selectConversation(conversation));
    elements.conversationList.append(button);
  }

  setBusy(state.busy);
}

async function selectConversation(conversation) {
  if (
    state.selected?.conversationId === conversation.conversationId &&
    state.selected?.umo === conversation.umo
  ) {
    return;
  }
  if (!(await canDiscardLocalChanges())) return;
  state.selected = conversation;
  renderConversations();
  await loadSelectedConversation();
}

async function loadSelectedConversation() {
  if (!state.selected) return;
  elements.emptyState.hidden = true;
  elements.editor.hidden = false;
  renderLoading(elements.messageList, "正在加载上下文…");
  setBusy(true);
  try {
    const result = await bridge.apiGet("page/conversation", {
      conversation_id: state.selected.conversationId,
      umo: state.selected.umo,
    });
    applyDetail(result);
    if (state.activeTab === "snapshots") {
      await loadSnapshots();
    }
  } catch (error) {
    elements.messageList.replaceChildren(
      createElement("p", "empty-list", `读取失败：${errorMessage(error)}`),
    );
    showToast(`读取上下文失败：${errorMessage(error)}`);
  } finally {
    setBusy(false);
  }
}

function applyDetail(payload) {
  state.selected = payload.conversation;
  renderDeliveryAudit(bridge, state.selected, elements.conversationMeta);
  state.revision = payload.revision;
  state.autoSnapshotsEnabled = Boolean(payload.autoSnapshotsEnabled);
  state.filterThinkingForLlm = Boolean(payload.filterThinkingForLlm);
  state.messages = (payload.messages || []).map((item) => ({
    ...deepClone(item),
    editing: false,
    isNew: false,
  }));
  state.messagePage = Math.max(1, Math.ceil(state.messages.length / state.messagePageSize));
  resetDirty();
  renderConversations();
  renderEditorHeader();
  renderMessages();
}

function renderEditorHeader() {
  const conversation = state.selected;
  if (!conversation) return;
  elements.conversationTitle.textContent =
    conversation.title || "未命名对话";
  elements.conversationUmo.textContent = conversation.umo;
  elements.conversationUmo.title = conversation.umo;

  const metadata = [
    ["平台", conversation.platformId || "—"],
    ["消息数", String(state.messages.length)],
    ["更新时间", formatDate(conversation.updatedAt)],
    ["token", String(conversation.tokenUsage || 0)],
    ["REVISION", shortId(state.revision, 12)],
    ["快照", state.autoSnapshotsEnabled ? "自动开启" : "仅手动"],
    ["思维链", state.filterThinkingForLlm ? "不发送" : "发送"],
  ];
  elements.conversationMeta.replaceChildren();
  for (const [label, value] of metadata) {
    const item = createElement("div", "meta-item");
    item.append(
      createElement("span", "field-label", labelOf(label)),
      createElement("span", "", value),
    );
    elements.conversationMeta.append(item);
  }
}

function messageRole(item) {
  const message = item.message;
  return message && typeof message === "object" && !Array.isArray(message)
    ? message.role || "无效"
    : "无效";
}

function isCheckpoint(item) {
  return messageRole(item) === "_checkpoint";
}

function isThinkingPart(part) {
  return Boolean(
    part &&
      typeof part === "object" &&
      !Array.isArray(part) &&
      ["think", "thinking"].includes(String(part.type || "").toLowerCase()),
  );
}

function renderMessages() {
  state.invalidFields.clear();
  elements.messageList.replaceChildren();
  renderHealth();
  renderUndo();

  if (!state.messages.length) {
    elements.messagePager.hidden = true;
    elements.messageList.append(
      createElement(
        "p",
        "empty-list",
        "此对话没有上下文消息。保存后，下一轮模型请求将从空历史开始。",
      ),
    );
    return;
  }

  const totalPages = Math.max(
    1,
    Math.ceil(state.messages.length / state.messagePageSize),
  );
  state.messagePage = Math.min(Math.max(1, state.messagePage), totalPages);
  elements.messagePager.hidden = false;
  elements.messagePageIndicator.textContent = `${state.messagePage} / ${totalPages}`;
  const startIndex = (state.messagePage - 1) * state.messagePageSize;
  const visibleMessages = state.messages.slice(
    startIndex,
    startIndex + state.messagePageSize,
  );
  visibleMessages.forEach((item, offset) => {
    elements.messageList.append(renderMessageCard(item, startIndex + offset));
  });
  setBusy(state.busy);
}
function renderHealth() {
  const invalidCount = state.messages.filter((item) => item.issue).length;
  elements.health.classList.toggle("has-issues", invalidCount > 0);
  if (invalidCount) {
    elements.health.textContent =
      `${invalidCount} 条损坏记录需要删除后才能保存；ROLE 与 TYPE 始终只读。`;
  } else {
    elements.health.textContent =
      `${state.messages.length} 条消息可编辑；${state.filterThinkingForLlm ? "思维链仅供查看，发送给 LLM 前会过滤。" : "思维链当前允许编辑。"}`;
  }
}

function renderUndo() {
  elements.undoStrip.hidden = !state.lastDeletion;
}

function messagePreview(content) {
  if (typeof content === "string") return content.replace(/\s+/g, " ").trim() || "（空消息）";
  if (Array.isArray(content)) return content.map((part) => typeof part === "object" ? (part.text || part.content || part.thinking || part.reasoning || "") : String(part)).join(" ").replace(/\s+/g, " ").trim() || "（多段内容）";
  if (content && typeof content === "object") return String(content.text || content.content || content.thinking || content.reasoning || "（结构化内容）").replace(/\s+/g, " ").trim();
  return String(content ?? "（空消息）");
}

function renderMessageCard(item, index) {
  const card = createElement("article", `message-card chat-card role-${messageRole(item)}`);
  if (item.issue) card.classList.add("has-issue");
  if (item.expanded || item.editing || item.issue) card.classList.add("is-expanded");
  const message = item.message || {};
  const header = createElement("header", "message-card-header");
  const roleLine = createElement("div", "role-line");
  roleLine.append(createElement("span", "role-badge", roleLabel(messageRole(item))), createElement("span", "message-number", `#${String(index + 1).padStart(2, "0")}`));
  const preview = createElement("button", "message-preview", messagePreview(message.content));
  preview.type = "button";
  preview.title = item.expanded ? "收起详情" : "展开消息、日志与思考路径";
  preview.addEventListener("click", () => { item.expanded = !item.expanded; renderMessages(); });
  const actions = createElement("div", "card-actions");
  const expand = createElement("button", "", item.expanded ? "收起" : "展开");
  expand.type = "button";
  expand.addEventListener("click", () => { item.expanded = !item.expanded; renderMessages(); });
  actions.append(expand);
  if (!item.issue && !isCheckpoint(item)) {
    const edit = createElement("button", "", item.editing ? "完成" : "编辑");
    edit.type = "button";
    edit.addEventListener("click", () => { item.editing = !item.editing; item.expanded = item.editing; renderMessages(); });
    actions.append(edit);
  }
  const remove = createElement("button", "", "删除");
  remove.type = "button";
  remove.addEventListener("click", () => deleteMessage(index));
  actions.append(remove);
  header.append(roleLine, actions);
  card.append(header, preview);
  if (!item.expanded && !item.editing && !item.issue) return card;
  const detail = createElement("div", "message-detail");
  if (item.issue) {
    detail.append(createElement("p", "issue-note", item.issue));
    appendReadonlyField(detail, "原始记录", item.message, true);
  } else if (item.editing && !isCheckpoint(item)) {
    const textSection = createElement("section", "message-detail-section");
    textSection.append(createElement("h3", "message-detail-title", "消息文本"));
    renderEditableContent(textSection, message);
    const parameterSection = createElement("section", "message-detail-section technical-parameters");
    parameterSection.append(createElement("h3", "message-detail-title", "技术参数"));
    renderExtraFields(parameterSection, message, true);
    detail.append(textSection, parameterSection);
  } else {
    const textSection = createElement("section", "message-detail-section");
    textSection.append(createElement("h3", "message-detail-title", "消息文本"));
    let visibleText = "";
    if (typeof message.content === "string") {
      visibleText = message.content;
    } else if (Array.isArray(message.content)) {
      visibleText = message.content
        .map((part) => {
          if (typeof part === "string") return part;
          if (!part || typeof part !== "object") return "";
          return typeof part.text === "string" ? part.text : "";
        })
        .filter(Boolean)
        .join("\n");
    } else if (message.content && typeof message.content === "object") {
      visibleText = typeof message.content.text === "string" ? message.content.text : "";
    }
    appendReadonlyField(
      textSection,
      "正文",
      visibleText || "[非文本消息；内容结构见技术参数]",
    );
    const parameterSection = createElement("section", "message-detail-section technical-parameters");
    parameterSection.append(createElement("h3", "message-detail-title", "技术参数"));
    const technicalParameters = Object.fromEntries(
      Object.entries(message).filter(([key]) => key !== "content"),
    );
    if (Array.isArray(message.content)) {
      technicalParameters.contentStructure = message.content.map((part) => {
        if (!part || typeof part !== "object") return { type: typeof part };
        return Object.fromEntries(
          Object.entries(part).filter(([key]) => key !== "text"),
        );
      });
    } else if (message.content && typeof message.content === "object") {
      technicalParameters.contentStructure = Object.fromEntries(
        Object.entries(message.content).filter(([key]) => key !== "text"),
      );
    } else {
      technicalParameters.contentStructure = { type: typeof message.content };
    }
    appendReadonlyField(parameterSection, "参数 JSON（不含消息文本）", technicalParameters, true);
    detail.append(textSection, parameterSection);
  }
  card.append(detail);
  return card;
}
function renderReadonlyContent(parent, content) {
  if (Array.isArray(content)) {
    const field = createElement("div", "field-block");
    field.append(createElement("span", "field-label", "content"));
    if (!content.length) {
      field.append(createElement("p", "readonly-value", "[]"));
    } else {
      content.forEach((part, partIndex) => {
        field.append(renderPart(part, partIndex, false, null, null));
      });
    }
    parent.append(field);
    return;
  }

  if (content && typeof content === "object") {
    const field = createElement("div", "field-block");
    field.append(
      createElement("span", "field-label", "content"),
      renderPart(content, 0, false, null, null),
    );
    parent.append(field);
    return;
  }
  appendReadonlyField(parent, "content", content);
}

function renderEditableContent(parent, message) {
  const content = message.content;
  if (Array.isArray(content)) {
    const field = createElement("div", "field-block");
    field.append(createElement("span", "field-label", "content"));
    if (!content.length) {
      field.append(createElement("p", "readonly-value", "[]"));
    } else {
      content.forEach((part, partIndex) => {
        field.append(
          renderPart(
            part,
            partIndex,
            true,
            message,
            ["content", partIndex],
            () => {
              message.content.splice(partIndex, 1);
              markDirty();
              renderMessages();
            },
          ),
        );
      });
    }
    parent.append(field);
    return;
  }

  if (content && typeof content === "object") {
    const field = createElement("div", "field-block");
    field.append(
      createElement("span", "field-label", "content"),
      renderPart(content, 0, true, message, ["content"]),
    );
    parent.append(field);
    return;
  }
  appendEditableField(parent, "content", message, ["content"], content);
}

function renderPart(part, partIndex, editable, root, path, onDelete) {
  const thinkingReadonly = state.filterThinkingForLlm && isThinkingPart(part);
  const block = createElement("div", thinkingReadonly ? "part-block thinking-readonly" : "part-block");
  const heading = createElement("div", "part-heading");
  heading.append(
    createElement("span", "part-index", `内容段 ${partIndex + 1}`),
    createElement(
      "span",
      "part-type",
      part && typeof part === "object" && !Array.isArray(part)
        ? "type: " + String(part.type || "object")
        : "type: value",
    ),
  );
  if (editable && onDelete && !thinkingReadonly) {
    const removePart = createElement("button", "part-delete", "删除此段");
    removePart.type = "button";
    removePart.addEventListener("click", onDelete);
    heading.append(removePart);
  }
  block.append(heading);

  if (!part || typeof part !== "object" || Array.isArray(part)) {
    if (editable && !thinkingReadonly) {
      appendEditableField(block, "值", root, path, part);
    } else {
      appendReadonlyField(block, "值", part, true);
    }
    return block;
  }

  const keys = Object.keys(part).filter((key) => key !== "type");
  if (!keys.length) {
    block.append(createElement("p", "readonly-value", "无可编辑字段"));
    return block;
  }
  for (const key of keys) {
    if (editable && !thinkingReadonly) {
      appendEditableField(block, key, root, [...path, key], part[key]);
    } else {
      appendReadonlyField(block, key, part[key]);
    }
  }
  return block;
}

function renderExtraFields(parent, message, editable) {
  const keys = Object.keys(message).filter(
    (key) => key !== "role" && key !== "content",
  );
  if (!keys.length) return;

  const details = createElement("details", "advanced-fields");
  if (keys.some((key) => /thinking|reasoning/i.test(key))) {
    details.open = true;
  }
  details.append(createElement("summary", "", "请求日志与思考路径"));
  const content = createElement("div", "advanced-content");
  for (const key of keys) {
    if (editable) {
      appendEditableField(content, key, message, [key], message[key]);
    } else {
      appendReadonlyField(content, key, message[key]);
    }
  }
  details.append(content);
  parent.append(details);
}

function appendReadonlyField(parent, label, value, forceJson = false) {
  const block = createElement("div", "field-block");
  block.append(createElement("span", "field-label", labelOf(label)));
  const shouldUseJson =
    forceJson || (typeof value === "object" && value !== null);
  let text;
  if (value === undefined) {
    text = "undefined";
  } else if (shouldUseJson) {
    try {
      text = JSON.stringify(value, null, 2);
    } catch {
      text = String(value);
    }
  } else if (value === null) {
    text = "null";
  } else {
    text = String(value);
  }
  block.append(
    createElement(
      "pre",
      `readonly-value${shouldUseJson ? " readonly-json" : ""}`,
      text,
    ),
  );
  parent.append(block);
}

function setAtPath(root, path, value) {
  let cursor = root;
  for (let index = 0; index < path.length - 1; index += 1) {
    cursor = cursor[path[index]];
  }
  cursor[path[path.length - 1]] = value;
}

function setFieldValidity(field, valid) {
  field.classList.toggle("is-invalid", !valid);
  if (valid) {
    state.invalidFields.delete(field);
    field.removeAttribute("aria-invalid");
  } else {
    state.invalidFields.add(field);
    field.setAttribute("aria-invalid", "true");
  }
}

function autoGrow(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 88), 520)}px`;
}

function appendEditableField(parent, label, root, path, value) {
  const block = createElement("label", "field-block");
  block.append(createElement("span", "field-label", labelOf(label)));

  if (typeof value === "string") {
    const textarea = createElement("textarea");
    textarea.value = value;
    textarea.spellcheck = true;
    textarea.addEventListener("input", () => {
      setAtPath(root, path, textarea.value);
      markDirty();
      autoGrow(textarea);
    });
    block.append(textarea);
    requestAnimationFrame(() => autoGrow(textarea));
    parent.append(block);
    return;
  }

  if (typeof value === "number") {
    const input = createElement("input");
    input.type = "number";
    input.step = "any";
    input.value = String(value);
    input.addEventListener("input", () => {
      const parsed = Number(input.value);
      const valid = input.value.trim() !== "" && Number.isFinite(parsed);
      setFieldValidity(input, valid);
      if (valid) {
        setAtPath(root, path, parsed);
        markDirty();
      }
    });
    block.append(input);
    parent.append(block);
    return;
  }

  if (typeof value === "boolean") {
    const select = createElement("select");
    const trueOption = createElement("option", "", "true");
    trueOption.value = "true";
    const falseOption = createElement("option", "", "false");
    falseOption.value = "false";
    select.append(trueOption, falseOption);
    select.value = String(value);
    select.addEventListener("change", () => {
      setAtPath(root, path, select.value === "true");
      markDirty();
    });
    block.append(select);
    parent.append(block);
    return;
  }

  const textarea = createElement("textarea", "json-editor");
  textarea.spellcheck = false;
  textarea.value = JSON.stringify(value === undefined ? null : value, null, 2);
  textarea.addEventListener("input", () => {
    try {
      const parsed = JSON.parse(textarea.value);
      setFieldValidity(textarea, true);
      setAtPath(root, path, parsed);
      markDirty();
    } catch {
      setFieldValidity(textarea, false);
    }
    autoGrow(textarea);
  });
  block.append(textarea);
  requestAnimationFrame(() => autoGrow(textarea));
  parent.append(block);
}

function addMessage() {
  if (!state.selected || state.busy) return;
  const item = {
    sourceIndex: null,
    sourceHash: null,
    message: { role: "user", content: "" },
    issue: null,
    editing: true,
    isNew: true,
  };
  state.messages.push(item);
  state.messagePage = Math.max(1, Math.ceil(state.messages.length / state.messagePageSize));
  markDirty();
  renderEditorHeader();
  renderMessages();
  requestAnimationFrame(() => {
    const cards = elements.messageList.querySelectorAll(".message-card");
    cards[cards.length - 1]?.scrollIntoView({ behavior: "smooth", block: "center" });
  });
}
async function deleteMessage(index) {
  const item = state.messages[index];
  const confirmed = await confirmAction(
    "删除这条上下文？",
    `角色：${roleLabel(messageRole(item))}。删除会先停留在页面中，点击“保存修改”后才会写入 AstrBot。`,
    "删除",
  );
  if (!confirmed) return;
  state.lastDeletion = { item, index };
  state.messages.splice(index, 1);
  markDirty();
  renderEditorHeader();
  renderMessages();
}

function undoDelete() {
  if (!state.lastDeletion) return;
  const { item, index } = state.lastDeletion;
  state.messages.splice(Math.min(index, state.messages.length), 0, item);
  state.messagePage = Math.floor(index / state.messagePageSize) + 1;
  state.lastDeletion = null;
  markDirty();
  renderEditorHeader();
  renderMessages();
}

async function saveChanges() {
  if (!state.selected || !state.dirty || state.busy) return;
  if (state.invalidFields.size) {
    showToast("存在无效的 JSON 或数值，请修正后再保存。");
    return;
  }
  const invalidRecord = state.messages.find((item) => item.issue);
  if (invalidRecord) {
    showToast("损坏记录不能保留或修改，请先将其删除。");
    return;
  }

  setBusy(true);
  elements.save.textContent = "正在保存…";
  try {
    const result = await bridge.apiPost("page/conversation/save", {
      conversationId: state.selected.conversationId,
      umo: state.selected.umo,
      baseRevision: state.revision,
      messages: state.messages.map((item) => ({
        sourceIndex: item.sourceIndex,
        sourceHash: item.sourceHash,
        message: item.message,
      })),
    });
    applyDetail(result);
    showToast(
      result.saved
        ? state.autoSnapshotsEnabled
          ? "上下文已保存，旧版本已加入快照。"
          : "上下文已保存；自动快照当前已关闭。"
        : "内容没有变化，无需写入。",
    );
    if (state.activeTab === "snapshots") await loadSnapshots();
  } catch (error) {
    showToast(`保存失败：${errorMessage(error)}`);
  } finally {
    elements.save.textContent = "保存修改";
    setBusy(false);
  }
}

async function loadSnapshots() {
  if (!state.selected) return;
  renderLoading(elements.snapshotList, "正在加载快照…");
  try {
    const result = await bridge.apiGet("page/snapshots", {
      conversation_id: state.selected.conversationId,
      umo: state.selected.umo,
    });
    state.snapshots = Array.isArray(result.items) ? result.items : [];
    renderSnapshots();
  } catch (error) {
    elements.snapshotList.replaceChildren(
      createElement("p", "empty-list", `读取失败：${errorMessage(error)}`),
    );
  }
}

function renderSnapshots() {
  elements.snapshotConfig.textContent = state.autoSnapshotsEnabled
    ? "自动快照已开启：每次保存或恢复前保留当前版本。"
    : "自动快照已关闭：保存不会新建快照，但已有快照仍可恢复。";
  elements.snapshotList.replaceChildren();
  if (!state.snapshots.length) {
    elements.snapshotList.append(
      createElement("p", "empty-list", "还没有快照。开启自动快照并保存后会出现在这里。"),
    );
    return;
  }
  for (const snapshot of state.snapshots) {
    const row = createElement("div", "snapshot-row");
    row.append(
      createElement("span", "snapshot-time", formatDate(snapshot.createdAt)),
      createElement(
        "span",
        "snapshot-detail",
        `${snapshot.messageCount} 条消息 · ${reasonLabel(snapshot.reason)}`,
      ),
    );
    const restore = createElement("button", "", "恢复");
    restore.type = "button";
    restore.addEventListener("click", () => restoreSnapshot(snapshot));
    row.append(restore);
    elements.snapshotList.append(row);
  }
}

async function restoreSnapshot(snapshot) {
  if (!state.selected || state.busy) return;
  const confirmed = await confirmAction(
    "恢复这个快照？",
    `${formatDate(snapshot.createdAt)} · ${snapshot.messageCount} 条消息。当前数据库内容会先自动备份，再替换为该版本。未保存的页面修改会被丢弃。`,
    "恢复",
  );
  if (!confirmed) return;

  setBusy(true);
  try {
    const result = await bridge.apiPost("page/snapshots/restore", {
      conversationId: state.selected.conversationId,
      umo: state.selected.umo,
      baseRevision: state.revision,
      snapshotId: snapshot.snapshotId,
    });
    applyDetail(result);
    await loadSnapshots();
    showToast(state.autoSnapshotsEnabled ? "快照已恢复；恢复前的内容也已自动备份。" : "快照已恢复；自动快照当前已关闭。");
  } catch (error) {
    showToast(`恢复失败：${errorMessage(error)}`);
  } finally {
    setBusy(false);
  }
}

async function switchTab(tabName) {
  state.activeTab = tabName;
  for (const tab of elements.tabs) {
    tab.classList.toggle("is-active", tab.dataset.tab === tabName);
  }
  elements.contextPanel.hidden = tabName !== "context";
  elements.snapshotsPanel.hidden = tabName !== "snapshots";
  if (tabName === "snapshots" && state.selected) {
    await loadSnapshots();
  }
}

elements.search.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    state.search = elements.search.value.trim();
    state.page = 1;
    loadConversations();
  }, 300);
});

elements.previousPage.addEventListener("click", () => {
  if (state.page <= 1) return;
  state.page -= 1;
  loadConversations();
});

elements.nextPage.addEventListener("click", () => {
  if (state.page >= Math.ceil(state.total / state.pageSize)) return;
  state.page += 1;
  loadConversations();
});

elements.previousMessagePage.addEventListener("click", () => {
  if (state.messagePage <= 1) return;
  state.messagePage -= 1;
  renderMessages();
  elements.contextPanel.scrollIntoView({ block: "start" });
});
elements.nextMessagePage.addEventListener("click", () => {
  const totalPages = Math.max(1, Math.ceil(state.messages.length / state.messagePageSize));
  if (state.messagePage >= totalPages) return;
  state.messagePage += 1;
  renderMessages();
  elements.contextPanel.scrollIntoView({ block: "start" });
});

elements.save.addEventListener("click", saveChanges);

elements.addMessage.addEventListener("click", addMessage);
elements.undoDelete.addEventListener("click", undoDelete);
elements.refresh.addEventListener("click", async () => {
  if (!(await canDiscardLocalChanges())) return;
  await loadSelectedConversation();
});
elements.discard.addEventListener("click", async () => {
  if (!(await canDiscardLocalChanges())) return;
  await loadSelectedConversation();
});
elements.tabs.forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
});

window.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    saveChanges();
  }
});

window.addEventListener("beforeunload", (event) => {
  if (!state.dirty) return;
  event.preventDefault();
  event.returnValue = "";
});

async function initialize() {
  if (!bridge) throw new Error("未检测到陪伴面板页面桥接");
  const pageParams = new URLSearchParams(window.location.search);
  const embeddedMeta = document.querySelector('meta[name="private-companion-embedded"]')?.content;
  const userMeta = document.querySelector('meta[name="private-companion-context-user"]')?.content;
  const embedded = pageParams.get("embedded") === "1" || embeddedMeta === "1";
  const userFilter = String(pageParams.get("user_id") || userMeta || "").trim();
  document.documentElement.classList.toggle("is-embedded", embedded);
  if (userFilter) {
    state.userFilter = userFilter;
    state.search = userFilter;
    elements.search.value = userFilter;
    elements.search.readOnly = true;
    elements.search.title = "当前用户过滤由陪伴面板控制";
  }
  await bridge.ready();
  const renderPageIdentity = () => {
    const title = "上下文";
    const description = "";
    document.title = title;
    elements.pageTitle.textContent = title;
    elements.pageDescription.textContent = description;
  };
  renderPageIdentity();
  const unsubscribe = bridge.onContext(renderPageIdentity);
  window.addEventListener("beforeunload", unsubscribe, { once: true });

  elements.connection.classList.add("is-ready");
  elements.connectionLabel.textContent = "已就绪";
  await loadConversations();
}

initialize().catch((error) => {
  elements.connectionLabel.textContent = "出错";
  showToast(`插件页面初始化失败：${errorMessage(error)}`);
});
