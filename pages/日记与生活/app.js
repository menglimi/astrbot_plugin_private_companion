"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
  const names = { planned: "待开始", active: "进行中", paused: "已暂停", completed: "已完成", complete: "已完成", cancelled: "已取消", deleted: "已删除", absent: "尚未书写", written: "正文已保存", succeeded: "成功", failed: "失败", unknown: "结果未知", accepted: "已接纳", uncertain: "结果未知", running: "运行中", new: "尚未启动", stopped: "已停止", draining: "正在停止" };
  const errors = { dashboard_identity_required: "请从已登录的 AstrBot 管理界面打开本页面。", staging_not_initialized: "角色数据尚未就绪，请在「账号与会话」保存角色映射；已保存可重新读取状态。",
    host_not_running: "请先启动当前 V2 配置。", life_not_bound: "请先启动当前 V2 配置，以载入原角色。", authorization_revoked: "执行权限已关闭或撤销，请确认生活日程设置。",
    authorization_missing: "请先启用生活日程与执行权限。", life_view_stale: "记录或权限已经变化，请刷新后重新确认。", life_reading_unresolved: "原阅读结果尚未确定，不能重复调用。",
    life_reading_active: "当前片段仍在阅读，请完成或取消后再入睡。", life_sleeping: "角色正在睡眠，请先醒来再阅读。", life_command_invalid: "生活记录或操作信息已变化，请刷新后重新尝试。", journal_trigger_unresolved: "原书写仍在进行、结果未知或已经成功，不能重复执行。",
    configuration_revision_conflict: "设置已变化，请刷新后重新保存。", core_access_required: "当前账号没有本体管理权限。", source_revoked: "原文已撤回，依赖它的内容已停止使用。" };
  let bridge, state, selected, dialogAction, dialogItem, busy = false, dirty = false, tab = "archive";
  let datePages = [null], datePage = 0, activityCursor = null, polling = null;
  const pending = new Map();
  const booleanFields = ["enabled", "diary_enabled", "sleep_enabled", "reading_enabled", "derive_enabled"];
  const configFields = [...booleanFields, "timezone", "diary_time", "sleep_time", "sleep_timezone", "sleep_variation_minutes", "sleep_window_minutes", "sleep_minutes"];
  const date = value => value ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
  const kind = item => item.kind === "dream" ? "梦境" : "日记";
  const display = () => bridge?.getContext?.()?.display || {};
  function roleName() {
    if (display().role_name || state.role_name) return display().role_name || state.role_name;
    const name = state.role_id;
    return typeof name === "string" && !/^(?:role[-_:]|staging[-_:]|dashboard:)|^[a-f0-9]{24,}$/i.test(name)
      ? name : "当前角色";
  }
  function localizedError(value) {
    const code = value?.code || value?.data?.code || value?.response?.data?.code || value?.message;
    return Object.assign(new Error(errors[code] || "操作未完成，请刷新确认记录状态后重试。"), {code});
  }
  function operationKey() {
    const webCrypto = window.crypto;
    if (typeof webCrypto?.randomUUID === "function") return webCrypto.randomUUID();
    if (typeof webCrypto?.getRandomValues === "function") {
      return Array.from(webCrypto.getRandomValues(new Uint8Array(16)),
        value => value.toString(16).padStart(2, "0")).join("");
    }
    return "life-" + Date.now() + "-" + Math.random().toString(36).slice(2);
  }
  function unwrap(response) {
    const wrapped = ["ok", "error"].includes(response?.status) && response?.data !== undefined ? response : ["ok", "error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") throw localizedError(wrapped);
    // Current AstrBot bridge returns the already-unwrapped data; older bridges return the envelope.
    const data = wrapped ? wrapped.data : response;
    if (!data || typeof data !== "object") throw new Error("未收到执行结果，请刷新确认后重试本次操作。");
    return data;
  }
  function notice(text, error = false) { $("notice").textContent = text; $("notice").className = error ? "error" : ""; $("notice").hidden = !text; }
  function controls(value) {
    busy = value;
    document.querySelectorAll("button, select").forEach(b => { b.disabled = value; });
    $("close-dialog").disabled = false;
    $("newer").disabled = value || datePage === 0; $("older").disabled = value || !state?.next_date;
    $("more-activities").disabled = value || !state?.next_activity;
    $("first-activities").disabled = value || !activityCursor;
    $("start-host").disabled = value || state?.host_state === "running";
    $("stop-host").disabled = value || !state || ["new", "stopped"].includes(state.host_state);
    ["write-diary", "start-sleep"].forEach(id => { $(id).disabled = value || !state?.config.enabled || state?.host_state !== "running"; });
    /* 配置没读到（state 为 null）时别让用户对着能点的表单白点：整组禁用，
       具体原因由 load() 的提示给出，刷新成功后自动恢复。 */
    $("config-form").querySelectorAll("input, button").forEach(el => { el.disabled = value || !state; });
  }
  async function api(method, body) {
    try {
      const response = method === "GET" ? await bridge.apiGet("life", body) : await bridge.apiPost("life/command", body);
      return unwrap(response);
    } catch (error) { throw localizedError(error); }
  }
  async function send(action, args = {}, item = null) {
    if (busy || !state) return;
    const body = { action, view_token: item?.view_token || state.view_token, ...args };
    if (item) {
      body.expected_revision = item.revision;
      if (item.activity_id) body.activity_id = item.activity_id;
      else { body.kind = item.kind; body.local_date = item.local_date; }
    }
    const signature = JSON.stringify(body);
    if (!pending.has(signature)) pending.set(signature, { ...body, operation_key: operationKey() });
    controls(true); $("dialog-error").hidden = true;
    notice(["diary", "dream", "derive"].includes(action) ? "正在书写…" : "正在保存…");
    try {
      const result = await api("POST", pending.get(signature));
      pending.delete(signature);
      if (action === "configure") dirty = false;
      $("editor").close();
      const retryOf = action === "diary" ? String(args.retry_of || "") : "";
      if (retryOf && result.state === "unknown") blockedRetries.add(retryOf);
      notice(result.state === "failed" || result.state === "unknown" ? "本次书写未完成，详情已保留在执行记录。" : "操作已完成。", ["failed", "unknown"].includes(result.state));
      await load(true);
    } catch (error) {
      const retryOf = action === "diary" ? String(args.retry_of || "") : "";
      if (retryOf && error.code === "journal_trigger_unresolved") {
        blockedRetries.add(retryOf);
        renderReceipts();
        notice(errors.journal_trigger_unresolved, true);
      } else {
        notice(error.message, true);
      }
      if ($("editor").open) { $("dialog-error").textContent = error.message; $("dialog-error").hidden = false; }
    } finally { controls(false); }
  }
  function actionButton(label, action, item, direct = false) {
    const b = node("button", label, ["delete", "cancel", "withdraw"].includes(action) ? "danger" : "secondary");
    b.type = "button"; b.addEventListener("click", () => direct ? send(action, {}, item) : open(action, item)); return b;
  }
  function renderArchive() {
    const list = $("journal-list"); list.replaceChildren();
    if (!state.journals.length) list.append(node("p", "这里还没有日记或梦境。", "empty"));
    selected = state.journals.find(j => j.kind === selected?.kind && j.local_date === selected?.local_date) || state.journals[0] || null;
    for (const item of state.journals) {
      const b = node("button", `${item.local_date} · ${kind(item)}`, "journal-link"); b.type = "button";
      b.setAttribute("aria-current", String(item === selected));
      b.append(node("small", item.state === "deleted" ? "已删除" : item.unavailable_reason ? "来源不可用" : (item.body || item.afterglow || "").slice(0, 45)));
      b.addEventListener("click", () => { selected = item; renderArchive(); }); list.append(b);
    }
    const detail = $("journal-detail"); detail.replaceChildren();
    if (!selected) { detail.append(node("p", "选择一篇日记或梦境。", "empty")); return; }
    const item = selected;
    detail.append(node("p", item.local_date, "eyebrow"), node("h2", kind(item)));
    if (item.state === "deleted") detail.append(node("p", "这篇记录已删除。可以主动填写正文后恢复。", "prose"));
    else if (item.unavailable_reason) detail.append(node("p", "依赖的原来源已不可用，正文与摘要已隐藏。", "prose"));
    else {
      detail.append(node("p", item.body, "prose"));
      if (item.afterglow) detail.append(node("p", item.afterglow, "afterglow"));
      if (item.summary) { const summary = node("div", undefined, "journal-summary"); summary.append(node("h3", "书写索引"), node("p", item.summary)); detail.append(summary); }
      if (item.dream_fragments?.length) { const ul = node("ul"); item.dream_fragments.forEach(s => ul.append(node("li", s))); detail.append(ul); }
    }
    const actions = node("div", undefined, "actions");
    if (item.state === "deleted") actions.append(actionButton("填写正文并恢复", "restore", item));
    else { if (!item.unavailable_reason) actions.append(actionButton("更正正文", "edit", item), actionButton("提取索引", "derive", item, true)); actions.append(actionButton("删除", "delete", item)); }
    detail.append(actions);
  }
  function renderActivities() {
    for (const type of ["reading", "sleep"]) {
      const list = $(type + "-list"); list.replaceChildren();
      const items = state.activities.filter(a => a.kind === type);
      if (!items.length) list.append(node("p", type === "reading" ? "本批暂无阅读片段。" : "本批没有睡眠记录。", "empty"));
      for (const item of items) {
        const card = node("article", undefined, "activity");
        card.append(node("h3", item.title), node("span", names[item.state] || "状态待核对", "badge"));
        card.append(node("p", `${date(item.started_at)}${item.due_at ? " · 计划醒来 " + date(item.due_at) : ""}`, "muted"));
        if (item.unavailable_reason) card.append(node("p", "原文已不可用，停止后续阅读并隐藏读后感。", "prose"));
        else if (item.reflection) card.append(node("p", item.reflection, "prose"));
        if (item.task && item.task.state !== "succeeded") card.append(node("p", "阅读执行：" + (names[item.task.state] || "状态待核对"), "muted"));
        const actions = node("div", undefined, "actions");
        if (type === "reading" && ["planned", "paused"].includes(item.state) && !item.unavailable_reason && item.task?.state !== "unknown") actions.append(actionButton(item.state === "paused" ? "重新阅读" : "开始阅读", "read", item, true));
        if (type === "sleep" && item.state === "active") actions.append(actionButton("现在醒来", "wake", item));
        if (type === "sleep" && item.state === "completed") actions.append(actionButton("记录 / 重试梦境", "dream", item));
        if (["planned", "active", "paused"].includes(item.state)) actions.append(actionButton("取消", "cancel", item));
        if (type === "reading" && !item.unavailable_reason) actions.append(actionButton("撤回原文", "withdraw", item));
        card.append(actions); list.append(card);
      }
    }
  }
  const blockedRetries = new Set();
  function renderReceipts() {
    const list = $("receipt-list"); list.replaceChildren();
    const receipts = [...new Map(state.receipts.map(r => [r.operation_id || r.key, r])).values()].reverse();
    if (!receipts.length) list.append(node("p", "暂无书写执行记录。", "empty"));
    receipts.forEach((item, index) => {
      const card = node("article", undefined, "activity");
      card.append(node("strong", `书写记录 ${index + 1} · ${names[item.state] || "状态待核对"}`));
      if (item.code) card.append(node("p", errors[item.code] || "本次书写未完成，请核对模型与执行权限后重试。", "muted"));
      list.append(card);
    });
    // 只有“已结论的失败”才能重试；unknown/处理中/已受理的那条不能再次提交，
    // 否则同一天会多出第二条书写。retryable=false 由后端按操作状态给出。
    const failed = receipts.map((item, index) => ({item, index}))
      .filter(({item}) => item.state === "failed" && item.operation_id && item.retryable !== false);
    const unresolved = state.journal_unresolved === true;
    for (const id of ["retry-id", "dream-retry"]) {
      const select = $(id), previous = select.value;
      select.replaceChildren(new Option(id === "dream-retry" ? "首次记录" : unresolved ? "有一次书写结果未确定，暂不能重试" : failed.length ? "选择失败的书写" : "暂无已确定失败的书写", ""),
        ...failed.map(({item, index}) => new Option(`书写记录 ${index + 1} · 失败`, item.operation_id || item.key)));
      select.value = failed.some(({item}) => (item.operation_id || item.key) === previous) ? previous : "";
    }
    $("retry-form").hidden = unresolved || failed.length === 0;
  }
  function renderSleepPlan() {
    const plan = state.sleep_schedule;
    if (!plan) {
      $("sleep-scope").textContent = "暂不可用"; $("sleep-executor").textContent = "—";
      $("sleep-next").textContent = "—"; $("sleep-active-wake").textContent = "—";
      $("sleep-pressure").textContent = "—"; $("sleep-debt").textContent = "—";
      $("sleep-workload").textContent = "—"; $("sleep-recommended").textContent = "—"; return;
    }
    $("sleep-scope").textContent = plan.enabled ? "当前角色 · 各会话共享" : "未启用";
    const executorName = plan.executor_is_current ? "当前私聊窗口" : "同一角色的其他私聊窗口";
    $("sleep-executor").textContent = !plan.enabled ? "未安排"
      : plan.executor_state !== "available" ? "暂无有效授权窗口"
        : `${executorName}${plan.executor_is_current ? "（本页）" : ""}`;
    $("sleep-next").textContent = plan.next_sleep_at
      ? `${date(plan.next_sleep_at)} · ${date(plan.next_wake_at)}` : "未安排";
    $("sleep-active-wake").textContent = plan.active_wake_at ? date(plan.active_wake_at) : "当前没有进行中的睡眠";
    const profile = plan.physiology || {};
    $("sleep-pressure").textContent = profile.sleep_pressure == null ? "暂无记录" : `${profile.sleep_pressure} / 100`;
    $("sleep-debt").textContent = profile.has_sleep_history ? `${Math.round(profile.sleep_debt_minutes || 0)} 分钟` : "暂无记录";
    $("sleep-workload").textContent = `${Math.round(profile.workload_score || 0)} / 100`;
    $("sleep-recommended").textContent = plan.recommended_duration_minutes
      ? `${Math.round(plan.recommended_duration_minutes / 60 * 10) / 10} 小时` : "—";
  }
  function render() {
    $("role").textContent = roleName();
    $("participant").textContent = display().subject_name || "当前聊天对象";
    $("host-state").textContent = names[state.host_state] || "状态待核对";
    $("routine-state").textContent = state.config.enabled ? "已启用" : "未启用";
    $("next-routines").textContent = [state.next_diary_at ? "下次日记：" + date(state.next_diary_at) : "", state.next_sleep_at ? "下次入睡：" + date(state.next_sleep_at) : ""].filter(Boolean).join(" · ");
    renderSleepPlan();
    if (!dirty) configFields.forEach(k => { if (booleanFields.includes(k)) $(k).checked = state.config[k]; else $(k).value = state.config[k]; });
    renderArchive(); renderActivities(); renderReceipts(); controls(busy);
    if (state.failure_code) notice(errors[state.failure_code] || "生活日程已暂停，请检查执行权限与设置。", true);
  }
  async function load(preserveNotice = false) {
    if (!bridge) return;
    try {
      state = await api("GET", { ...(datePages[datePage] ? { before: datePages[datePage] } : {}), ...(activityCursor ? { after_activity: activityCursor } : {}) });
      if (!preserveNotice) notice(""); render();
      clearTimeout(polling);
      if (state.running.length || state.activities.some(a => a.state === "active") || state.config.reading_enabled) polling = setTimeout(() => { if (!busy && !document.hidden) load(true); }, 3000);
    } catch (error) {
      state = selected = null;
      ["journal-list", "journal-detail", "reading-list", "sleep-list", "receipt-list", "retry-id", "dream-retry"].forEach(id => $(id).replaceChildren());
      $("retry-form").hidden = true;
      for (const id of ["role", "participant", "sleep-scope", "sleep-executor", "sleep-next", "sleep-active-wake", "sleep-pressure", "sleep-debt", "sleep-workload", "sleep-recommended"]) $(id).textContent = "—";
      /* 配置/状态没读到时给出明确状态，而不是一排“—”：用户能分清是
         「没有配置」还是「这次没读到」，也知道下一步点哪里。 */
      $("routine-state").textContent = "读取失败";
      $("host-state").textContent = "暂不可用";
      $("settings").open = true;
      $("editor").close();
      notice(`${error.message} 配置暂时无法读取或保存，请点「刷新记录」重试。`, true);
      controls(false);
    }
  }
  function open(action, item = null) {
    dialogAction = action; dialogItem = item;
    const titles = { add_text: "添加阅读原文", sleep: "开始睡眠", edit: "更正正文", restore: "主动恢复正文", delete: "删除这篇书写", cancel: "取消活动", withdraw: "撤回阅读原文", wake: "结束这段睡眠", dream: "记录或重试梦境" };
    $("dialog-title").textContent = titles[action];
    $("dialog-description").textContent = { withdraw: "整份原文及其所有片段将停止使用，依赖它的私人书写也将隐藏。", delete: "删除后自动书写不会覆盖此记录，恢复需要主动填写正文。", wake: "确认醒来后，会独立记录这段睡眠的梦境。", add_text: "保存完整原文，较长资料会拆为连续阅读片段。收存不会标记已读。" }[action] || "确认当前记录后保存。";
    $("title-field").hidden = action !== "add_text";
    $("body-field").hidden = !["add_text", "edit", "restore"].includes(action);
    $("glow-field").hidden = !["edit", "restore"].includes(action) || item?.kind !== "dream";
    $("minutes-field").hidden = action !== "sleep"; $("retry-field").hidden = action !== "dream";
    $("text-title").value = ""; $("text-body").value = item?.body || ""; $("text-glow").value = item?.afterglow || ""; $("dream-retry").value = "";
    $("text-title").required = action === "add_text"; $("text-body").required = action === "add_text" || (["edit", "restore"].includes(action) && item?.kind !== "dream");
    $("minutes").value = state.config.sleep_minutes; $("dialog-error").hidden = true;
    $("editor").showModal();
  }
  document.querySelectorAll("[data-tab]").forEach(b => b.addEventListener("click", () => {
    tab = b.dataset.tab; document.querySelectorAll("[data-tab]").forEach(x => x.setAttribute("aria-current", x === b ? "page" : "false"));
    document.querySelectorAll(".tab-panel").forEach(p => { p.hidden = p.id !== tab; }); $("activity-pages").hidden = !["reading", "sleep"].includes(tab);
  }));
  $("config-form").addEventListener("input", () => { dirty = true; });
  $("config-form").addEventListener("submit", e => {
    e.preventDefault();
    /* 状态没读到时 state 仍是 null：这里以前会直接读 state.edit_token 抛错，
       表单看起来“点了没反应”。先把原因说清楚，再让用户刷新。 */
    if (!state) { $("settings").open = true; notice("配置尚未读取成功，请先点「刷新记录」重试后再保存。", true); return; }
    try {
      const changes = {};
      configFields.forEach(k => { changes[k] = booleanFields.includes(k) ? $(k).checked : ["sleep_minutes", "sleep_variation_minutes", "sleep_window_minutes"].includes(k) ? Number($(k).value) : $(k).value; });
      send("configure", { changes, edit_token: state.edit_token });
    } catch (error) { notice(error?.message || "保存失败，请刷新后重试。", true); }
  });
  $("editor-form").addEventListener("submit", e => {
    e.preventDefault(); const args = {};
    if (dialogAction === "add_text") { args.title = $("text-title").value; args.text = $("text-body").value; }
    if (["edit", "restore"].includes(dialogAction)) { args.body = $("text-body").value; args.afterglow = dialogItem.kind === "dream" ? $("text-glow").value : ""; }
    if (dialogAction === "sleep") args.minutes = Number($("minutes").value);
    if (dialogAction === "dream" && $("dream-retry").value.trim()) args.retry_of = $("dream-retry").value.trim();
    send(dialogAction, args, dialogItem);
  });
  $("retry-form").addEventListener("submit", e => { e.preventDefault(); send("diary", { local_date: $("retry-date").value, retry_of: $("retry-id").value.trim() }); });
  $("close-dialog").addEventListener("click", () => $("editor").close());
  $("refresh").addEventListener("click", () => load());
  async function hostControl(action) {
    if (busy || !bridge) return;
    controls(true); notice(action === "start" ? "正在载入当前角色并启动…" : "正在停止运行…");
    const call = async name => unwrap(await bridge.apiPost("staging/" + name, {}));
    try {
      if (action === "start") {
        const status = unwrap(await bridge.apiGet("staging/status", {}));
        if (!status.runtime_open) { await call("probe"); await call("initialize"); }
      }
      await call(action); await load();
    } catch (error) { notice(localizedError(error).message, true); } finally { controls(false); }
  }
  $("start-host").addEventListener("click", () => hostControl("start"));
  $("stop-host").addEventListener("click", () => hostControl("stop"));
  $("add-text").addEventListener("click", () => { if (state) open("add_text"); });
  $("start-sleep").addEventListener("click", () => open("sleep"));
  $("write-diary").addEventListener("click", () => send("diary"));
  $("older").addEventListener("click", () => { datePages[++datePage] = state.next_date; load(); });
  $("newer").addEventListener("click", () => { datePage--; load(); });
  $("more-activities").addEventListener("click", () => { activityCursor = state.next_activity; load(); });
  $("first-activities").addEventListener("click", () => { activityCursor = null; load(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && !busy) load(true); });
  document.addEventListener("companion-mappings-change", () => { if (bridge && !busy) void load(true); });
  (async () => { try { await window.AstrBotPluginPage.ready(); bridge = window.AstrBotPluginPage; await load(); } catch (_) { notice("无法连接管理界面，请从 AstrBot 插件页面重新打开。", true); } })();
})();
