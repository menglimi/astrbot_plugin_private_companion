"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const STAGES = ["positioning", "personality", "voice", "background", "review"];
  const LABELS = {optimize: "拟人优化", create: "创建人格"};
  const errors = {
    dashboard_identity_required: "请从已登录的 AstrBot 管理界面打开此页。",
    core_access_required: "当前账号没有管理本安装的权限。",
    persona_designer_invalid: "设计内容过长或格式不完整，请检查后重试。",
    persona_designer_unavailable: "新的她暂时不可用，当前编辑内容仍保留，可以稍后重试。",
    persona_designer_model_missing: "所选文本模型尚未加载，请重新选择模型。",
    persona_designer_source_required: "先输入人设，或从“选择人设”载入一份。",
    persona_designer_draft_required: "先生成或填写一份人设稿，再进行情景试演。",
    persona_designer_response_invalid: "模型返回的设计结果不完整，请重试本轮或换一个文本模型。",
    persona_designer_context_full: "这次设计的内容较长，请先保存草稿，再精简对话后继续。",
    persona_designer_busy: "还有一轮设计正在生成，请等待完成或先停止生成。",
    persona_designer_timeout: "本轮生成超时，输入与设计内容仍保留，可以重试。",
    persona_designer_cancelled: "本轮生成已停止，输入与设计内容仍保留。",
    persona_designer_stopped: "功能已停止，请重新打开插件页面。",
    persona_designer_model_changed: "模型已经重新加载，请重新发起本轮设计。",
    persona_designer_persona_missing: "这份人设已不在 AstrBot 列表中，请重新选择。",
    persona_designer_source_large: "这份人设较长，请手动选取需要优化的部分。",
    persona_designer_draft_missing: "这份草稿不存在或不属于当前账号。",
    persona_designer_save_conflict: "草稿内容已变化，请作为新版本保存。",
    bridge_timeout: "未能确认本次操作的结果，当前内容仍保留。保存操作可重试确认。",
  };
  const newDocument = mode => ({mode, stage: "positioning", provider_id: "", source_persona_id: "", source_text: "",
    confirmed: "", draft: "", lorebook: "", input: "", messages: [], options: []});
  const fresh = () => ({version: 1, activeMode: "optimize", sessions: {
    optimize: {document: newDocument("optimize")}, create: {document: newDocument("create")}}, pendingSave: null});
  const copy = value => JSON.parse(JSON.stringify(value));
  const uid = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, "0")).join("");
  const node = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  };
  let bridge, root = fresh(), options = {models: [], personas: []}, loaded = false, disposed = false;
  let working = false, nextBefore = null, pollTimer, loadingDrafts = false, polling = false;
  let pendingPersonaId = "";
  const session = () => root.sessions[root.activeMode];
  const doc = () => session().document;
  const generating = () => Object.values(root.sessions).some(s => s.pending);
  const errorText = error => errors[error?.code] || errors[error?.message]
    || "这次操作没有完成，当前编辑内容仍保留，请稍后重试。";
  function unwrap(response) {
    const wrapped = ["ok", "error"].includes(response?.status) ? response
      : response?.status === undefined && ["ok", "error"].includes(response?.data?.status) ? response.data : null;
    if (wrapped?.status === "error") throw Object.assign(new Error(wrapped.message), {code: wrapped.message});
    const result = wrapped ? wrapped.data : response;
    if (!result || typeof result !== "object") throw new Error("persona_designer_response_invalid");
    return result;
  }
  async function api(method, path, body, timeoutMs = 18000) {
    return unwrap(await bridge[method === "GET" ? "apiGet" : "apiPost"](path, body, {timeoutMs}));
  }
  function notice(message = "", error = false) {
    $("notice").textContent = message;
    $("notice").classList.toggle("error", error);
    $("notice").hidden = !message;
  }
  function persist() {
    if (disposed || !bridge) return Promise.resolve();
    return api("POST", "designer/client-state", root).catch(() => {
      notice("页面内的进度暂未同步，请先保存草稿，再切换其他页面。", true);
    });
  }
  function controls() {
    const d = doc(), ownBusy = !!session().pending, anyBusy = generating();
    const modelAvailable = options.models.some(m => m.id === d.provider_id);
    $("send-turn").disabled = !loaded || anyBusy || working || !modelAvailable;
    $("send-turn").textContent = ownBusy ? "正在共创…" : d.input.trim() ? "发送想法" : d.messages.length ? "继续共创" : "开始共创";
    $("stop-turn").hidden = !ownBusy;
    $("retry-turn").hidden = !session().retry || anyBusy;
    $("generation-state").textContent = ownBusy ? "正在推敲，请稍候…" : anyBusy ? "另一模式正在生成" : "";
    ["source-text", "confirmed", "draft-text", "lorebook", "choose-persona", "choose-model"].forEach(id => $(id).disabled = ownBusy || working);
    $("choose-persona").disabled ||= anyBusy || !!root.pendingSave;
    ["generate-draft", "tryout"].forEach(id => $(id).disabled = !loaded || anyBusy || working || !modelAvailable);
    $("tryout").disabled ||= !d.draft.trim();
    ["save-draft", "save-version"].forEach(id => $(id).disabled = !loaded || !!root.pendingSave || anyBusy || working);
    $("retry-save").hidden = !root.retrySave;
    $("retry-save").disabled = !!root.pendingSave || anyBusy || working;
    $("compare").disabled = root.activeMode !== "optimize" || !d.source_text;
    $("copy-draft").disabled = !d.draft;
    $("copy-lorebook").disabled = !d.lorebook;
    $("source-count").textContent = Array.from(d.source_text).length + " 字";
    $("draft-count").textContent = Array.from(d.draft).length + " 字";
    document.querySelectorAll("[data-stage]").forEach(el => el.disabled = ownBusy);
    document.querySelectorAll("#suggestions button").forEach(el => el.disabled = anyBusy || working || !modelAvailable);
  }
  function render() {
    const d = doc();
    document.querySelectorAll("[data-mode]").forEach(el => el.setAttribute("aria-pressed", String(el.dataset.mode === root.activeMode)));
    document.querySelectorAll("[data-stage]").forEach(el => {
      if (el.dataset.stage === d.stage) el.setAttribute("aria-current", "step");
      else el.removeAttribute("aria-current");
    });
    $("source-panel").hidden = d.mode !== "optimize";
    for (const [id, key] of [["source-text", "source_text"], ["chat-input", "input"], ["confirmed", "confirmed"], ["draft-text", "draft"], ["lorebook", "lorebook"]]) {
      if ($(id).value !== d[key]) $(id).value = d[key];
    }
    $("source-label").textContent = d.source_persona_id ? "起点来自：" + d.source_persona_id + " · 可在此编辑" : "可以直接编辑这份起点文本。";
    const model = options.models.find(m => m.id === d.provider_id);
    $("choose-model").textContent = model ? "模型 · " + model.name : d.provider_id ? "模型已不可用 · 重新选择" : "选择模型";
    $("choose-model").title = d.provider_id || "仅用于此页的人设设计";
    const log = $("messages");
    log.replaceChildren();
    if (!d.messages.length) {
      const empty = node("div", undefined, "empty-message");
      empty.append(node("strong", d.mode === "optimize" ? "先看看哪些地方值得调整。" : "她可以从一个很小的念头开始。"),
        node("p", d.mode === "optimize" ? "提供原人设后，说说你遇到的问题，或直接开始，让助手给出具体建议。" : "描述一种性格、一段关系或几句你喜欢的话；还没有方向也没关系。"));
      log.append(empty);
    }
    for (const message of d.messages) {
      const article = node("article", undefined, "chat-message " + message.role);
      article.append(node("span", message.role === "user" ? "你" : "设计助手", "speaker"), node("p", message.content));
      log.append(article);
    }
    log.scrollTop = log.scrollHeight;
    const suggestions = $("suggestions");
    suggestions.replaceChildren();
    const choices = d.options.length ? d.options : d.mode === "create" && !d.messages.length
      ? ["我有一个大致的角色想法", "还没想好，先帮我找方向", "从我熟悉的性格出发"] : [];
    for (const choice of choices) {
      const button = node("button", choice, "button small"); button.type = "button";
      button.addEventListener("click", () => sendTurn("chat", choice)); suggestions.append(button);
    }
    controls();
  }
  function fillSelect(id, items, selected, emptyLabel) {
    const select = $(id); select.replaceChildren();
    if (!items.length) select.append(new Option(emptyLabel, ""));
    else {
      if (!selected || !items.some(item => item.id === selected)) select.append(new Option("请选择", ""));
      items.forEach(item => select.append(new Option(item.name + (item.default ? " · AstrBot 配置使用" : ""), item.id)));
    }
    select.value = selected || "";
    select.disabled = !items.length;
  }
  async function loadOptions() {
    try {
      options = await api("GET", "designer/options", {});
      if (!Array.isArray(options.models) || !Array.isArray(options.personas)) throw new Error("persona_designer_response_invalid");
      loaded = true;
      fillSelect("model-select", options.models, doc().provider_id, "暂无文本模型");
      fillSelect("persona-select", options.personas, doc().source_persona_id || options.default_persona_id || options.personas[0]?.id, "暂无人设");
      $("model-empty").hidden = !!options.models.length;
      $("persona-empty").hidden = !!options.personas.length;
      $("confirm-model").disabled = !options.models.length;
      $("load-persona").disabled = !options.personas.length;
      if (options.persona_issue) notice(options.persona_issue);
      render();
    } catch (error) { notice(errorText(error), true); }
  }
  function validReply(value) {
    return value && typeof value.reply === "string" && value.reply.trim() && value.reply.length <= 16000
      && ["confirmed", "draft", "lorebook"].every(key => typeof value[key] === "string")
      && value.confirmed.length <= 16000 && value.draft.length <= 65536 && value.lorebook.length <= 20000
      && STAGES.includes(value.stage) && Array.isArray(value.options) && value.options.length <= 3
      && value.options.every(value => typeof value === "string" && value.length <= 300);
  }
  async function sendTurn(action, text, retry) {
    if (generating() || working) return;
    const current = session(), d = doc();
    if (!options.models.some(m => m.id === d.provider_id)) { $("model-dialog").showModal(); return; }
    if (d.mode === "optimize" && !d.source_text.trim()) { notice(errors.persona_designer_source_required, true); $("source-text").focus(); return; }
    if (action === "tryout" && !d.draft.trim()) { notice(errors.persona_designer_draft_required, true); return; }
    const user = text || (action === "draft" ? "请根据已经确认的内容，整理一份可编辑的人设稿。"
      : action === "tryout" ? "请按当前人设稿进行日常聊天、工具结果转述、被指出疏漏三组情景试演。"
      : d.input.trim() ? d.input : d.mode === "optimize" ? "请先分析这份人设，给出具体的优化建议，和我逐步确认。" : "我还没有完整想法，请给几个具体方向，和我逐步设计。" );
    const state = {...copy(d), messages: retry ? copy(retry.state.messages)
      : [...copy(d.messages), {role: "user", content: user}], options: []};
    if (state.messages.length > 98) { notice("当前对话较长，请保存草稿后开始新的设计。", true); return; }
    const request = {request_id: uid(), action, state};
    current.pending = {id: request.request_id, action, state,
      input: action === "chat" && state.messages[state.messages.length - 1]?.content === d.input ? d.input : null};
    delete current.retry; delete current.error;
    notice(); render();
    await persist();
    if (disposed) return;
    // The parent retains the receipt if this iframe is replaced during the call.
    api("POST", "designer/turn", request, 135000).catch(() => {}).finally(() => { if (!disposed) poll(); });
    schedulePoll();
  }
  function schedulePoll() {
    clearTimeout(pollTimer);
    if (!disposed && (generating() || root.pendingSave)) pollTimer = setTimeout(poll, 800);
  }
  async function poll() {
    clearTimeout(pollTimer);
    if (disposed || polling) return;
    polling = true;
    try {
      for (const [mode, current] of Object.entries(root.sessions)) {
        const pending = current.pending;
        if (!pending) continue;
        const receipt = await api("GET", "designer/client-result", {id: "turn:" + pending.id});
        if (disposed || current.pending !== pending || receipt.status === "pending") continue;
        let failure;
        try {
          if (receipt.status === "missing") throw new Error("persona_designer_stopped");
          if (receipt.status === "failed") throw Object.assign(new Error(receipt.code), {code: receipt.code});
          const reply = unwrap(receipt.data);
          if (!validReply(reply)) throw new Error("persona_designer_response_invalid");
          Object.assign(current.document, {messages: [...pending.state.messages, {role: "assistant", content: reply.reply}],
            options: reply.options, confirmed: reply.confirmed, stage: reply.stage, draft: reply.draft, lorebook: reply.lorebook});
          if (pending.input !== null && current.document.input === pending.input) current.document.input = "";
        } catch (error) { failure = errorText(error); current.retry = {action: pending.action, state: pending.state}; current.error = failure; }
        delete current.pending;
        await persist();
        await api("POST", "designer/client-ack", {id: "turn:" + pending.id});
        if (mode === root.activeMode) { notice(failure || "", !!failure); render(); }
        else { notice(LABELS[mode] + (failure ? "本轮未完成，可切回重试。" : "本轮已完成，可以切回查看。"), !!failure); controls(); }
      }
      const save = root.pendingSave;
      if (save) {
        const receipt = await api("GET", "designer/client-result", {id: "save:" + save.id});
        if (!disposed && root.pendingSave === save && receipt.status !== "pending") {
          try {
            if (receipt.status !== "done") throw Object.assign(new Error(receipt.code || "bridge_timeout"), {code: receipt.code});
            unwrap(receipt.data);
            root.pendingSave = null; root.retrySave = null;
            notice("已保存“" + save.title + "”，下次可从已存草稿继续。");
          } catch (error) { root.pendingSave = null; root.retrySave = save; notice(errorText(error) + " 可点击“确认上次保存”重试确认。", true); }
          await persist(); await api("POST", "designer/client-ack", {id: "save:" + save.id}); controls();
        }
      }
    } catch (error) { if (!disposed) notice(errorText(error), true); }
    polling = false; schedulePoll();
  }
  async function stopTurn() {
    const pending = session().pending;
    if (!pending) return;
    try { await api("POST", "designer/cancel", {request_id: pending.id}); notice("已请求停止，正在确认本轮状态。"); }
    catch (error) { notice(errorText(error), true); }
  }
  function openSave() {
    $("draft-title").value = LABELS[root.activeMode] + " · " + new Date().toLocaleString("zh-CN", {hour12: false});
    $("save-dialog").showModal();
  }
  async function saveSnapshot(snapshot) {
    if (root.pendingSave) return;
    root.pendingSave = snapshot; notice("正在保存草稿…"); controls();
    await persist();
    if (disposed) return;
    api("POST", "designer/drafts", snapshot).catch(() => {}).finally(() => { if (!disposed) poll(); });
    schedulePoll();
  }
  async function listDrafts(before = 0) {
    if (loadingDrafts) return;
    loadingDrafts = true;
    $("more-drafts").disabled = true;
    if (!before) $("draft-list").replaceChildren(node("p", "正在读取…", "muted"));
    try {
      const result = await api("GET", "designer/drafts", before ? {before} : {});
      if (!Array.isArray(result.items)) throw new Error("persona_designer_response_invalid");
      if (!before) $("draft-list").replaceChildren();
      if (!result.items.length && !before) $("draft-list").append(node("p", "还没有手动保存过的草稿。", "muted"));
      for (const item of result.items) {
        const row = node("article", undefined, "saved-item"), description = node("div");
        description.append(node("strong", item.title), node("small", new Date(item.created_at).toLocaleString("zh-CN", {hour12: false})));
        const button = node("button", "读取", "button small"); button.type = "button";
        button.addEventListener("click", async () => {
          if (generating() || root.pendingSave) { notice("请先等待本轮操作完成，再读取草稿。", true); $("drafts-dialog").close(); return; }
          working = true; controls(); button.disabled = true;
          try {
            const saved = await api("GET", "designer/draft", {id: item.id});
            const d = saved.state;
            if (!["optimize", "create"].includes(d?.mode) || !Array.isArray(d.messages)) throw new Error("persona_designer_response_invalid");
            root.sessions[d.mode] = {document: d}; root.activeMode = d.mode;
            await persist(); $("drafts-dialog").close(); notice("已读取“" + saved.title + "”。"); render();
          } catch (error) { notice(errorText(error), true); } finally { working = false; button.disabled = false; controls(); }
        });
        row.append(description, button); $("draft-list").append(row);
      }
      nextBefore = result.next_before;
      $("more-drafts").hidden = !nextBefore;
    } catch (error) { $("draft-list").replaceChildren(node("p", errorText(error), "muted")); }
    finally { loadingDrafts = false; $("more-drafts").disabled = false; }
  }
  async function copyText(id) {
    const input = $(id);
    try { await navigator.clipboard.writeText(input.value); notice("已复制，可以到 AstrBot 中自行填入。"); }
    catch {
      input.focus(); input.select();
      try { if (!document.execCommand("copy")) throw new Error(); notice("已复制。"); }
      catch { notice("已选中文本，请使用系统的复制操作。"); }
    }
  }
  document.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => {
    const provider = doc().provider_id; root.activeMode = button.dataset.mode;
    if (!doc().provider_id) doc().provider_id = provider;
    persist(); notice(session().error || "", !!session().error); render();
  }));
  document.querySelectorAll("[data-stage]").forEach(button => button.addEventListener("click", () => {
    doc().stage = button.dataset.stage;
    if (!doc().input) doc().input = "我们先聊“" + button.textContent.replace(/^\d+/, "").trim() + "”这一部分。";
    persist(); render(); $("chat-input").focus();
  }));
  for (const [id, key] of [["source-text", "source_text"], ["chat-input", "input"], ["confirmed", "confirmed"], ["draft-text", "draft"], ["lorebook", "lorebook"]]) {
    $(id).addEventListener("input", () => { doc()[key] = $(id).value; persist(); controls(); });
  }
  document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
  $("composer").addEventListener("submit", event => { event.preventDefault(); sendTurn("chat"); });
  $("chat-input").addEventListener("keydown", event => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.isComposing) { event.preventDefault(); sendTurn("chat"); }
  });
  $("generate-draft").addEventListener("click", () => sendTurn("draft"));
  $("tryout").addEventListener("click", () => sendTurn("tryout"));
  $("retry-turn").addEventListener("click", () => { const retry = session().retry; if (retry) sendTurn(retry.action, null, retry); });
  $("stop-turn").addEventListener("click", stopTurn);
  $("choose-model").addEventListener("click", () => {
    fillSelect("model-select", options.models, doc().provider_id, "暂无文本模型"); $("model-dialog").showModal();
  });
  $("model-form").addEventListener("submit", event => {
    event.preventDefault(); if (!$("model-select").value) return;
    doc().provider_id = $("model-select").value; persist(); $("model-dialog").close(); notice(); render();
  });
  $("choose-persona").addEventListener("click", () => {
    fillSelect("persona-select", options.personas, doc().source_persona_id || options.default_persona_id || options.personas[0]?.id, "暂无人设"); $("persona-dialog").showModal();
  });
  async function loadPersona(id) {
    const current = session(), mode = root.activeMode;
    if (!id || working || generating() || root.pendingSave) return;
    working = true; $("load-persona").disabled = true; controls();
    try {
      const result = await api("GET", "designer/persona", {id});
      if (result.id !== id || typeof result.text !== "string") throw new Error("persona_designer_response_invalid");
      // Fetch first: a failed read must leave the previous design recoverable.
      if (current.document.source_persona_id !== id) {
        const document = newDocument(mode);
        document.provider_id = current.document.provider_id;
        document.source_persona_id = id; document.source_text = result.text;
        root.sessions[mode] = {document};
      } else {
        current.document.source_text = result.text;
      }
      await persist(); $("persona-dialog").close(); $("switch-persona-dialog").close();
      notice("已载入人设，可以先说说希望调整的地方。"); render();
    } catch (error) {
      notice(errorText(error), true); $("persona-dialog").close(); $("switch-persona-dialog").close();
    }
    finally { working = false; $("load-persona").disabled = !options.personas.length; controls(); }
  }
  $("persona-form").addEventListener("submit", event => {
    event.preventDefault(); const id = $("persona-select").value, d = doc();
    if (!id || working || generating() || root.pendingSave) return;
    const hasContent = d.messages.length || ["source_text", "confirmed", "draft", "lorebook", "input"].some(key => d[key].length);
    if (id !== d.source_persona_id && hasContent) {
      pendingPersonaId = id; $("persona-dialog").close(); $("switch-persona-dialog").showModal();
    } else loadPersona(id);
  });
  $("confirm-persona-switch").addEventListener("click", () => loadPersona(pendingPersonaId));
  $("switch-persona-dialog").addEventListener("close", () => { pendingPersonaId = ""; });
  $("refresh-options").addEventListener("click", loadOptions); $("refresh-personas").addEventListener("click", loadOptions);
  ["save-draft", "save-version"].forEach(id => $(id).addEventListener("click", openSave));
  $("retry-save").addEventListener("click", () => saveSnapshot(root.retrySave));
  $("save-form").addEventListener("submit", event => {
    event.preventDefault(); const title = $("draft-title").value.trim(); if (!title) return;
    const snapshot = {id: uid(), title, state: copy(doc())}; $("save-dialog").close(); saveSnapshot(snapshot);
  });
  $("open-drafts").addEventListener("click", () => { $("drafts-dialog").showModal(); listDrafts(); });
  $("more-drafts").addEventListener("click", () => listDrafts(nextBefore));
  $("new-design").addEventListener("click", () => { $("drafts-dialog").close(); $("reset-dialog").showModal(); });
  $("confirm-reset").addEventListener("click", () => {
    if (generating() || root.pendingSave) { notice("请先等待本轮操作完成，再重新开始。", true); $("reset-dialog").close(); return; }
    const provider = doc().provider_id;
    root.sessions[root.activeMode] = {document: newDocument(root.activeMode)};
    doc().provider_id = provider; persist(); $("reset-dialog").close(); notice(); render();
  });
  $("copy-draft").addEventListener("click", () => copyText("draft-text")); $("copy-lorebook").addEventListener("click", () => copyText("lorebook"));
  $("compare").addEventListener("click", () => {
    $("compare-source").textContent = doc().source_text; $("compare-draft").textContent = doc().draft; $("compare-dialog").showModal();
  });
  async function initialize() {
    try {
      bridge = window.AstrBotPluginPage; await bridge.ready();
      const cached = await api("GET", "designer/client-state", {});
      root = cached.state?.version === 1 ? cached.state : fresh();
      render(); await loadOptions();
      if (session().error) notice(session().error, true);
      await poll();
    } catch (error) { notice(errorText(error), true); }
  }
  window.addEventListener("pagehide", () => { disposed = true; clearTimeout(pollTimer); root = fresh(); render(); });
  window.addEventListener("pageshow", event => { if (event.persisted) { disposed = false; initialize(); } });
  render(); initialize();
})();
