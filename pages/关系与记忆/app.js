"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const endpoint = "relationships";
  const labels = { proposed: "待审阅", reviewed: "已审阅", adopted: "已采用", retracted: "已撤回" };
  const actionLabels = { review: "审阅通过", adopt: "采用到对话", revise: "更正理解", retract: "撤回记录" };
  const success = { review: "已审阅，可以在确认后采用到对话。", adopt: "已采用，后续对话可以参考这条理解。",
    revise: "已保存更正，记录已回到待审阅状态。", retract: "已撤回，后续对话不再使用这条记录。" };
  const errors = {
    dashboard_identity_required: "请从已登录的 AstrBot 管理界面打开本页面。",
    staging_not_initialized: "角色数据尚未就绪，请在「账号与会话」保存角色映射；已保存可重新读取状态。",
    staging_host_terminated: "插件已经停止，请重新打开管理页面。",
    core_access_required: "当前账号没有本体管理权限。", binding_revoked: "当前管理身份已被撤销。",
    relationship_view_stale: "管理范围或权限已经变化，请刷新记录后再操作。",
    relationship_target_changed: "角色或关系对象已经变化，请重新加载当前管理范围。",
    relationship_memory_conflict: "记录已变化，请刷新查看最新版本后再操作。",
    relationship_memory_not_found: "当前管理范围中已没有这条记录。",
    source_revoked: "原来源已撤回，无法继续采用或更正这条理解。",
    source_expired: "原来源已过期，无法继续采用或更正这条理解。",
  };
  let bridge, inventory = [], selected = null, context = null, hostStatus = null;
  let availabilityCode = null, busy = false;
  let cursors = [null], page = 0, nextCursor = null, editAction = null, pending = null, requestSequence = 0;
  const display = () => bridge?.getContext?.()?.display || {};
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const kind = (item) => item.kind === "emotion_episode" ? "情绪片段" : "关系背景";
  const date = (value) => new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric" }).format(new Date(value));
  function notice(message, error = false) {
    $("notice").textContent = message;
    $("notice").className = error ? "error" : "";
    $("notice").hidden = !message;
  }
  function controls(value) {
    busy = value;
    document.querySelectorAll("button, select").forEach((item) => { item.disabled = value; });
    $("previous").disabled = value || page === 0;
    $("next").disabled = value || !nextCursor;
    $("interpretation").readOnly = value;
    $("list").setAttribute("aria-busy", String(value));
  }
  function errorCode(value) {
    const candidates = [value?.code, value?.data?.code, value?.response?.code,
      value?.response?.data?.code, value?.detail?.code];
    const knownCode = candidates.find(item => typeof item === "string" && errors[item]);
    if (knownCode) return knownCode;
    const message = [value?.message, value?.reason, value?.response?.message]
      .find(item => typeof item === "string" && item) || "";
    // Some AstrBot bridge versions reject the request with the server's English
    // reason instead of returning the error envelope. Keep that detail out of
    // the UI while preserving the stable error state.
    if (/initialized v2 profile|staging profile first|staging profile before/i.test(message)) return "staging_not_initialized";
    if (/dashboard|sign in/i.test(message)) return "dashboard_identity_required";
    return candidates.find(item => typeof item === "string" && item) || null;
  }
  function localizedError(value, fallback = "操作未完成，请刷新确认记录状态后重试。") {
    const code = errorCode(value);
    const error = new Error(errors[code] || fallback);
    error.code = code || "";
    return error;
  }
  function unwrap(response) {
    const wrapped = ["ok", "error"].includes(response?.status) && response?.data !== undefined ? response
      : ["ok", "error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") throw localizedError(wrapped);
    const data = wrapped ? wrapped.data : response;
    if (!data || typeof data !== "object") throw localizedError(null, "未收到服务结果，请刷新确认后重试。");
    return data;
  }
  async function api(method, body) {
    try {
      const response = method === "GET" ? await bridge.apiGet(endpoint, body) : await bridge.apiPost(endpoint + "/command", body);
      return unwrap(response);
    } catch (error) {
      if (error instanceof Error && error.code && errors[error.code]) throw error;
      throw localizedError(error);
    }
  }
  async function readHostStatus() {
    try { return unwrap(await bridge.apiGet("staging/status", {})); }
    catch { return null; }
  }
  function unavailableCopy() {
    if (availabilityCode === "staging_not_initialized") {
      return ["角色数据尚未就绪", "请在「账号与会话」保存角色映射；已保存可重新读取状态。"];
    }
    return ["暂时无法读取关系记录", "请确认已从 AstrBot 管理界面打开本页，然后刷新记录。"];
  }
  function renderList() {
    $("list").replaceChildren();
    $("count").textContent = availabilityCode === "staging_not_initialized" ? "尚未加载" : `本页 ${inventory.length} 条`;
    $("page-number").textContent = `第 ${page + 1} 页`;
    if (!inventory.length) {
      if (availabilityCode) {
        const [title, message] = unavailableCopy();
        const card = node("div", undefined, "availability-card");
        card.append(node("strong", title), node("p", message));
        if (availabilityCode === "staging_not_initialized" && hostStatus) {
          const status = hostStatus.enabled === false ? "当前配置开关未启用。"
            : hostStatus.configured === false ? "角色数据正在自动准备。"
              : "角色状态暂未读到。";
          card.append(node("small", status));
        }
        $("list").append(card);
      } else $("list").append(node("p", "当前范围没有符合条件的记录。", "empty"));
    }
    inventory.forEach((item) => {
      const button = node("button", undefined, "record");
      button.type = "button";
      button.setAttribute("aria-current", String(selected?.memory_id === item.memory_id));
      const title = node("span", undefined, "record-title");
      title.append(node("strong", kind(item)), node("span", labels[item.state], "badge " + item.state));
      button.append(title, node("span", item.interpretation ?? "来源已不可用，正文已隐藏。", "record-preview"),
        node("span", `${date(item.observed_at)} · ${item.source_count} 个来源`, "record-date"));
      button.addEventListener("click", () => {
        if (busy) return;
        selected = item; pending = null; renderList(); renderDetail();
        $("detail-heading").focus({ preventScroll: true });
        if (window.innerWidth <= 760) $("detail").scrollIntoView({ block: "start" });
      });
      $("list").append(button);
    });
  }
  function renderDetail() {
    const panel = $("detail");
    panel.replaceChildren();
    if (!selected) {
      const empty = node("div", undefined, "placeholder");
      const [title, message] = availabilityCode ? unavailableCopy() : ["选择一条记录", ""];
      const heading = node("h2", title); heading.id = "detail-heading";
      empty.append(heading);
      if (message) empty.append(node("p", message));
      panel.append(empty); return;
    }
    const item = selected;
    const heading = node("h2", kind(item)); heading.id = "detail-heading"; heading.tabIndex = -1;
    const head = node("div", undefined, "section-head"); head.append(heading, node("span", labels[item.state], "badge " + item.state));
    const meta = node("div", undefined, "detail-meta");
    meta.append(node("span", `观察于 ${date(item.observed_at)}`), node("span", `第 ${item.revision} 版`));
    if (item.emotion_subject) meta.append(node("span", item.emotion_subject === "role" ? "角色的感受" : "对关系对象情绪的理解"));
    const text = node("p", item.interpretation ?? "来源已失效或不再允许读取，正文已隐藏。可以撤回这条记录。", "memory-text");
    const evidence = node("p", item.unavailable_reason ? "来源状态：不可用" : `${item.source_count} 个原来源当前有效 · 内容仍是角色的主观理解`, "evidence");
    const attribution = node("div", undefined, "attribution");
    const names = { reviewed_by: "审阅", adopted_by: "采用", revised_by: "更正", retracted_by: "撤回" };
    Object.entries(names).forEach(([field, label]) => {
      const id = item.attribution[field];
      if (id) {
        const name = id === context.operator_id ? context.operator_name || "当前管理员"
          : id === context.role_id ? display().role_name || "当前角色"
            : id === context.subject_id ? display().subject_name || "当前聊天对象"
              : id.startsWith("dashboard:") ? "其他管理员" : "其他操作者";
        attribution.append(node("span", `${label}：${name}`));
      }
    });
    const actions = node("div", undefined, "actions");
    item.actions.forEach((action) => {
      const button = node("button", actionLabels[action], action === "retract" ? "danger" : action === "revise" ? "secondary" : "primary");
      button.type = "button";
      button.addEventListener("click", () => action === "revise" || action === "retract" ? openEditor(action) : submit(action));
      actions.append(button);
    });
    panel.append(head, meta, text, evidence, attribution, actions);
  }
  async function load(preferredId) {
    const sequence = ++requestSequence;
    controls(true);
    try {
      hostStatus = await readHostStatus();
      const params = { limit: 20 };
      if ($("state").value) params.state = $("state").value;
      if (cursors[page]) params.after_id = cursors[page];
      const result = await api("GET", params);
      if (sequence !== requestSequence) return;
      availabilityCode = null;
      context = result; inventory = result.items; nextCursor = result.next_cursor;
      selected = inventory.find((item) => item.memory_id === preferredId) ?? null;
      $("role").textContent = display().role_name || result.role_name || "当前角色";
      $("subject").textContent = display().subject_name || result.subject_name || "当前聊天对象";
      $("scope").textContent = display().target_label || "当前私聊窗口";
      $("operator").textContent = result.operator_name;
      renderList(); renderDetail();
    } catch (error) {
      availabilityCode = error.code || "relationship_unavailable";
      inventory = []; selected = null; nextCursor = null;
      for (const id of ["role", "subject", "scope", "operator"]) $(id).textContent = "—";
      renderList(); renderDetail(); notice(error.message || "读取失败，请稍后刷新。", true);
    } finally { if (sequence === requestSequence) controls(false); }
  }
  function openEditor(action) {
    if (busy || !selected) return;
    editAction = action; pending = null;
    $("dialog-heading").textContent = actionLabels[action];
    $("dialog-description").textContent = action === "revise" ? "更正后会重新进入待审阅状态，原版本会保留。" : "撤回后，后续对话将不再使用这条理解。已发送消息的确认会保留。";
    $("text-fields").hidden = action !== "revise";
    $("interpretation").value = selected.interpretation ?? "";
    $("confirm").textContent = action === "revise" ? "保存更正" : "确认撤回";
    $("dialog-error").hidden = true;
    $("editor").showModal();
    if (action === "revise") $("interpretation").focus();
  }
  async function submit(action) {
    if (busy || !selected) return;
    if (action === "revise" && !$("interpretation").value.trim()) {
      $("dialog-error").textContent = "请填写完整的更正内容。"; $("dialog-error").hidden = false; return;
    }
    const body = { memory_id: selected.memory_id, action, expected_revision: selected.revision, view_token: selected.view_token };
    if (action === "revise") body.interpretation = $("interpretation").value;
    const fingerprint = JSON.stringify(body);
    // A lost response keeps the same key for a deliberate retry of the same command.
    const nextUuid = () => {
      if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
      const bytes = new Uint8Array(16);
      if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") crypto.getRandomValues(bytes);
      else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = [...bytes].map(v => v.toString(16).padStart(2, "0")).join("");
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    };
    if (pending?.fingerprint !== fingerprint) pending = { fingerprint, key: nextUuid() };
    body.operation_key = pending.key;
    controls(true); notice(""); $("dialog-error").hidden = true;
    try {
      const result = await api("POST", body);
      pending = null; $("editor").close();
      notice(success[action]);
      await load(result.item.memory_id);
    } catch (error) {
      const message = error.message || "未收到操作结果。可重试本次操作，或刷新确认记录状态。";
      if ($("editor").open) { $("dialog-error").textContent = message; $("dialog-error").hidden = false; }
      else notice(message, true);
    } finally { controls(false); }
  }
  $("refresh").addEventListener("click", () => { notice(""); load(selected?.memory_id); });
  $("state").addEventListener("change", () => { page = 0; cursors = [null]; pending = null; notice(""); load(); });
  $("previous").addEventListener("click", () => { if (page > 0) { page--; load(); } });
  $("next").addEventListener("click", () => { if (nextCursor) { cursors[++page] = nextCursor; load(); } });
  $("change-form").addEventListener("submit", (event) => { event.preventDefault(); submit(editAction); });
  for (const id of ["close-dialog", "cancel-dialog"]) $(id).addEventListener("click", () => { if (!busy) $("editor").close(); });
  $("editor").addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
  document.addEventListener("companion-mappings-change", () => {
    if (!bridge || busy) return;
    if (bridge.getContext?.()?.display?.scope_kind === "group") {
      if (typeof window.CompanionGroupMemoryPage?.refresh === "function") void window.CompanionGroupMemoryPage.refresh();
      else $("gm-refresh")?.click();
    } else void load(selected?.memory_id);
  });
  async function start() {
    bridge = window.AstrBotPluginPage;
    if (!bridge) {
      availabilityCode = "dashboard_identity_required";
      controls(true); renderList(); renderDetail(); notice(errors.dashboard_identity_required, true); return;
    }
    try {
      const context = await bridge.ready();
      if (context?.display?.scope_kind === "group") { window.CompanionGroupMemoryPage?.mount(bridge, context); return; }
      await load();
    }
    catch (error) {
      availabilityCode = errorCode(error) || "dashboard_identity_required";
      controls(true); renderList(); renderDetail();
      notice(localizedError(error, errors.dashboard_identity_required).message, true);
    }
  }
  start();
})();
