"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
  const names = { planned: "待开始", active: "进行中", paused: "已暂停", completed: "已完成", complete: "已完成", cancelled: "已取消", deleted: "已删除", absent: "尚未书写", written: "正文已保存", succeeded: "成功", failed: "失败", unknown: "结果未知", accepted: "已接纳", uncertain: "结果未知", running: "运行中", new: "尚未启动", stopped: "已停止", draining: "正在停止" };
  const errors = { dashboard_identity_required: "请从已登录的 AstrBot 管理界面打开本页面。", staging_not_initialized: "角色数据尚未就绪，请在「账号与会话」保存角色映射；已保存可重新读取状态。",
    host_not_running: "请先启动当前 V2 配置。", life_not_bound: "请先启动当前 V2 配置，以载入原角色。", authorization_revoked: "执行权限已关闭或撤销，请确认生活日程设置。",
    authorization_missing: "请先启用生活日程与执行权限。", life_view_stale: "记录或权限已经变化，请刷新后重新确认。", life_reading_unresolved: "原阅读结果尚未确定，不能重复调用。",
    life_reading_active: "当前片段仍在阅读，请完成或取消后再入睡。", life_sleeping: "角色正在睡眠，请先醒来再阅读。", life_command_invalid: "生活记录或操作信息已变化，请刷新后重新尝试。", journal_trigger_unresolved: "原书写仍在进行、结果未知或已经成功，不能重复执行。",
    configuration_revision_conflict: "设置已变化，请刷新后重新保存。", core_access_required: "当前账号没有本体管理权限。", source_revoked: "原文已撤回，依赖它的内容已停止使用。", life_activity_not_removable: "只有已完成或已取消的阅读记录可以删除。" };
  let bridge, state, selected, dialogAction, dialogItem, busy = false, loading = false, loadSeq = 0, dirty = false, tab = "archive";
  let datePages = [null], datePage = 0, activityCursor = null, readingCursor = null, sleepCursor = null, polling = null;
  let embedded = false, sleepOverviewUserToggled = false, settingSleepOverview = false;
  const formatClock = (date = new Date()) => `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}:${String(date.getSeconds()).padStart(2, "0")}`;
  function setRefreshState(active) {
    const btn = $("refresh");
    if (!btn) return;
    btn.disabled = active || busy;
    btn.classList?.toggle?.("is-refreshing", active);
    btn.setAttribute?.("aria-busy", active ? "true" : "false");
    btn.textContent = active ? "刷新中…" : "刷新记录";
  }
  const renderedKeys = { journalList: null, journalDetail: null, reading: null, sleep: null, receipts: null, sleepSummary: null };
  const animatedLists = new Set();
  const pending = new Map();
  function setTone(id, tone) {
    const el = $(id);
    if (!el?.dataset) return;
    if (tone) el.dataset.tone = tone;
    else delete el.dataset.tone;
  }
  function setMeter(id, ratio) {
    const bar = $(id);
    if (!bar?.style) return;
    const clamped = Number.isFinite(ratio) ? Math.max(0, Math.min(100, Math.round(ratio))) : 0;
    bar.style.width = `${clamped}%`;
  }
  function syncEmbeddedMode() {
    const context = bridge?.getContext?.();
    embedded = Boolean(context?.pageName === "life") || $("start-host")?.hidden === true;
    if (document.documentElement?.dataset) {
      if (embedded) document.documentElement.dataset.embedded = "true";
      else delete document.documentElement.dataset.embedded;
    }
  }
  function syncSleepOverviewForTab(currentTab) {
    if (document.documentElement?.dataset) document.documentElement.dataset.focusTab = currentTab;
    const details = $("sleep-overview");
    if (!details || sleepOverviewUserToggled) return;
    const shouldOpen = !embedded || currentTab === "sleep";
    if (details.open !== shouldOpen) {
      settingSleepOverview = true;
      details.open = shouldOpen;
      settingSleepOverview = false;
    }
  }
  function renderSleepSummaryPills(pills) {
    const host = $("sleep-summary-pills");
    if (!host) return;
    const key = JSON.stringify(pills);
    if (key === renderedKeys.sleepSummary) return;
    renderedKeys.sleepSummary = key;
    host.replaceChildren();
    for (const [text, tone] of pills) {
      const pill = node("span", text, "sleep-pill");
      if (tone && pill.dataset) pill.dataset.tone = tone;
      host.append(pill);
    }
  }
  function resetRenderKeys() {
    for (const key of Object.keys(renderedKeys)) renderedKeys[key] = null;
  }
  function applyEntranceClass(list, listId, hasItems, background = false) {
    const animate = !background && hasItems && !animatedLists.has(listId);
    if (animate) animatedLists.add(listId);
    list.classList?.toggle?.("is-initial-render", animate);
  }
  function activitySignature(item) {
    return {
      activity_id: item.activity_id, revision: item.revision, kind: item.kind, state: item.state,
      title: item.title, started_at: item.started_at, due_at: item.due_at,
      book_progress: item.book_progress || null, unavailable_reason: item.unavailable_reason || null,
      reflection: item.reflection || null, task_state: item.task?.state || null,
      view_token: item.view_token || null,
    };
  }
  function pollDelay(currentState) {
    if (!currentState || document.hidden) return 0;
    if (currentState.running?.length > 0) return 3000;
    if (currentState.activities.some(a => a.state === "active" && a.kind !== "sleep")) return 15000;
    if (currentState.activities.some(a => a.state === "active")) return 30000;
    if (currentState.config?.reading_enabled) return 15000;
    return 0;
  }
  function schedulePolling() {
    clearTimeout(polling);
    polling = null;
    const delay = pollDelay(state);
    if (delay > 0) {
      polling = setTimeout(() => {
        polling = null;
        if (!busy && !document.hidden) load(true, true);
      }, delay);
    }
  }
  const booleanFields = ["enabled", "diary_enabled", "sleep_enabled", "reading_enabled", "reading_share_in_group", "derive_enabled"];
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
  function activeActivityCursor() {
    if (tab === "reading") return readingCursor;
    if (tab === "sleep") return sleepCursor;
    return activityCursor;
  }
  function nextActivityCursorForTab() {
    if (!state) return null;
    if (tab === "reading") return "next_reading_activity" in state ? state.next_reading_activity : state.next_activity;
    if (tab === "sleep") return "next_sleep_activity" in state ? state.next_sleep_activity : state.next_activity;
    return state.next_activity;
  }
  function controls(value) {
    busy = value;
    document.querySelectorAll("button, select").forEach(b => { b.disabled = value; });
    $("close-dialog").disabled = false;
    $("newer").disabled = value || datePage === 0; $("older").disabled = value || !state?.next_date;
    $("more-activities").disabled = value || !nextActivityCursorForTab();
    $("first-activities").disabled = value || !activeActivityCursor();
    $("start-host").disabled = value || state?.host_state === "running";
    $("stop-host").disabled = value || !state || ["new", "stopped"].includes(state.host_state);
    ["write-diary", "start-sleep"].forEach(id => { $(id).disabled = value || !state?.config.enabled || state?.host_state !== "running"; });
    /* 配置没读到（state 为 null）时别让用户对着能点的表单白点：整组禁用，
       具体原因由 load() 的提示给出，刷新成功后自动恢复。 */
    $("config-form").querySelectorAll("input, button").forEach(el => { el.disabled = value || !state; });
    if ($("refresh")) $("refresh").disabled = value || loading;
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
  function renderArchive(background = false) {
    selected = state.journals.find(j => j.kind === selected?.kind && j.local_date === selected?.local_date) || state.journals[0] || null;
    const selectedKey = selected ? `${selected.kind}:${selected.local_date}` : "";
    const list = $("journal-list");
    const listKey = JSON.stringify(state.journals);
    if (listKey !== renderedKeys.journalList) {
      renderedKeys.journalList = listKey;
      applyEntranceClass(list, "journal-list", state.journals.length > 0, background);
      list.replaceChildren();
      if (!state.journals.length) list.append(node("p", "这里还没有日记或梦境。", "empty"));
      for (const item of state.journals) {
        const b = node("button", `${item.local_date} · ${kind(item)}`, "journal-link"); b.type = "button";
        b.dataset.journalKey = `${item.kind}:${item.local_date}`;
        b.setAttribute("aria-current", String(item === selected));
        b.append(node("small", item.state === "deleted" ? "已删除" : item.unavailable_reason ? "来源不可用" : (item.body || item.afterglow || "").slice(0, 45)));
        b.addEventListener("click", () => { selected = item; renderArchive(false); }); list.append(b);
      }
    } else {
      list.querySelectorAll(".journal-link").forEach(btn => {
        if (btn.dataset && "journalKey" in btn.dataset) btn.setAttribute("aria-current", String(btn.dataset.journalKey === selectedKey));
      });
    }
    const detailKey = JSON.stringify(selected);
    if (detailKey === renderedKeys.journalDetail) return;
    renderedKeys.journalDetail = detailKey;
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
  function renderActivities(background = false) {
    for (const type of ["reading", "sleep"]) {
      const dedicated = type === "reading" ? state.reading_activities : state.sleep_activities;
      const items = Array.isArray(dedicated) ? dedicated : state.activities.filter(a => a.kind === type);
      const key = JSON.stringify(type === "reading"
        ? { items: items.map(activitySignature), reading_enabled: Boolean(state.config?.reading_enabled), next_reading_at: state.next_reading_at || null }
        : { items: items.map(activitySignature) });
      if (key === renderedKeys[type]) continue;
      renderedKeys[type] = key;
      const list = $(type + "-list");
      applyEntranceClass(list, type + "-list", items.length > 0, background);
      list.replaceChildren();
      if (!items.length) list.append(node("p", type === "reading" ? "本批暂无阅读片段。" : "本批没有睡眠记录。", "empty"));
      for (const item of items) {
        const card = node("article", undefined, "activity");
        card.append(node("h3", item.title), node("span", names[item.state] || "状态待核对", "badge"));
        card.append(node("p", `${date(item.started_at)}${item.due_at ? " · 计划醒来 " + date(item.due_at) : ""}`, "muted"));
        if (type === "reading" && item.book_progress && !item.unavailable_reason) {
          const {completed, total} = item.book_progress;
          card.append(node("p", `${Math.floor(completed * 100 / total)}% · 全书 ${completed} / ${total} 片段已读`, "muted"));
          if (item.state === "planned" && state.config.reading_enabled) {
            card.append(node("p", state.next_reading_at
              ? "下次阅读时段 " + date(state.next_reading_at) : "等待日历中的阅读时段", "muted"));
          }
        }
        if (item.unavailable_reason) card.append(node("p", "原文已不可用，停止后续阅读并隐藏读后感。", "prose"));
        else if (item.reflection) card.append(node("p", item.reflection, "prose"));
        if (item.task && item.task.state !== "succeeded") card.append(node("p", "阅读执行：" + (names[item.task.state] || "状态待核对"), "muted"));
        const actions = node("div", undefined, "actions");
        if (type === "reading" && ["planned", "paused"].includes(item.state) && !item.unavailable_reason && item.task?.state !== "unknown") actions.append(actionButton(item.state === "paused" ? "重新阅读" : "现在阅读", "read", item, true));
        if (type === "sleep" && item.state === "active") actions.append(actionButton("现在醒来", "wake", item));
        if (type === "sleep" && item.state === "completed") actions.append(actionButton("记录 / 重试梦境", "dream", item));
        if (["planned", "active", "paused"].includes(item.state)) actions.append(actionButton("取消", "cancel", item));
        if (type === "reading" && !item.unavailable_reason) actions.append(actionButton("撤回原文", "withdraw", item));
        if (type === "reading" && ["completed", "cancelled"].includes(item.state)) actions.append(actionButton("删除", "remove", item));
        card.append(actions); list.append(card);
      }
    }
  }
  const blockedRetries = new Set();
  function renderReceipts(background = false) {
    const receipts = [...new Map(state.receipts.map(r => [r.operation_id || r.key, r])).values()].reverse();
    const unresolved = state.journal_unresolved === true;
    const key = JSON.stringify({ receipts, unresolved, blocked: [...blockedRetries].sort() });
    if (key === renderedKeys.receipts) return;
    renderedKeys.receipts = key;
    const list = $("receipt-list");
    applyEntranceClass(list, "receipt-list", receipts.length > 0, background);
    list.replaceChildren();
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
      $("sleep-workload").textContent = "—"; $("sleep-recommended").textContent = "—";
      setMeter("sleep-pressure-bar", 0); setMeter("sleep-workload-bar", 0);
      for (const id of ["sleep-pressure-card", "sleep-debt-card", "sleep-workload-card", "sleep-recommended-card", "sleep-active-wake-card"]) setTone(id, "");
      $("sleep-debt-hint").textContent = "累计睡眠缺口";
      renderSleepSummaryPills([]);
      return;
    }
    $("sleep-scope").textContent = plan.enabled ? "当前角色 · 各会话共享" : "未启用";
    const executorLabel = plan.executor_window?.label || "";
    const executorName = plan.executor_is_current
      ? "当前私聊窗口（本页）"
      : (executorLabel || "同一角色的其他私聊窗口");
    $("sleep-executor").textContent = !plan.enabled ? "未安排"
      : plan.executor_state !== "available" ? "暂无有效授权窗口"
        : executorName;
    $("sleep-next").textContent = plan.next_sleep_at
      ? `${date(plan.next_sleep_at)} · ${date(plan.next_wake_at)}` : "未安排";
    $("sleep-active-wake").textContent = plan.active_wake_at ? date(plan.active_wake_at) : "当前没有进行中的睡眠";
    setTone("sleep-active-wake-card", plan.active_wake_at ? "active" : "");
    const profile = plan.physiology || {};
    const pressure = profile.sleep_pressure == null ? null : Number(profile.sleep_pressure);
    const pressureTone = pressure == null ? "" : pressure >= 90 ? "danger" : pressure >= 75 ? "warn" : pressure >= 45 ? "accent" : "ok";
    $("sleep-pressure").textContent = pressure == null ? "暂无记录" : `${profile.sleep_pressure} / 100`;
    setTone("sleep-pressure-card", pressureTone);
    setMeter("sleep-pressure-bar", pressure ?? 0);
    const debt = Math.round(profile.sleep_debt_minutes || 0);
    const debtTone = !profile.has_sleep_history ? "" : debt >= 90 ? "warn" : debt > 0 ? "accent" : "ok";
    $("sleep-debt").textContent = profile.has_sleep_history ? `${debt} 分钟` : "暂无记录";
    setTone("sleep-debt-card", debtTone);
    $("sleep-debt-hint").textContent = !profile.has_sleep_history ? "累计睡眠缺口"
      : debt >= 90 ? "建议尽早补觉" : debt > 0 ? "轻度睡眠缺口" : "作息充足稳定";
    const workload = Math.round(profile.workload_score || 0);
    const workloadTone = workload >= 75 ? "warn" : workload >= 45 ? "accent" : "ok";
    $("sleep-workload").textContent = `${workload} / 100`;
    setTone("sleep-workload-card", workloadTone);
    setMeter("sleep-workload-bar", workload);
    const recommendedHours = plan.recommended_duration_minutes
      ? Math.round(plan.recommended_duration_minutes / 60 * 10) / 10 : null;
    $("sleep-recommended").textContent = recommendedHours != null ? `${recommendedHours} 小时` : "—";
    setTone("sleep-recommended-card", recommendedHours != null ? "accent" : "");
    const summaryPills = [];
    if (plan.active_wake_at) summaryPills.push(["睡眠中", "accent"]);
    if (pressure != null) summaryPills.push([`压力 ${profile.sleep_pressure}/100`, pressureTone]);
    if (profile.has_sleep_history) summaryPills.push([`欠睡 ${debt} 分钟`, debtTone]);
    if (recommendedHours != null) summaryPills.push([`推荐 ${recommendedHours} 小时`, ""]);
    renderSleepSummaryPills(summaryPills);
  }
  function render(background = false) {
    $("role").textContent = roleName();
    $("participant").textContent = display().subject_name || display().target_label || state.window_label || "当前聊天对象";
    $("host-state").textContent = names[state.host_state] || "状态待核对";
    $("routine-state").textContent = state.config.enabled ? "已启用" : "未启用";
    const hostTone = state.host_state === "running" ? "ok" : state.host_state === "draining" ? "warn" : "muted";
    setTone("life-toolbar", hostTone);
    setTone("host-state-card", hostTone);
    setTone("routine-state-card", state.config.enabled ? "ok" : "muted");
    const nextText = [state.next_diary_at ? "下次日记：" + date(state.next_diary_at) : "", state.next_sleep_at ? "下次入睡：" + date(state.next_sleep_at) : ""].filter(Boolean).join(" · ");
    $("next-routines").textContent = nextText;
    $("next-routines").hidden = !nextText;
    renderSleepPlan();
    if (!dirty) configFields.forEach(k => {
      const input = $(k);
      if (booleanFields.includes(k)) { const next = Boolean(state.config[k]); if (input.checked !== next) input.checked = next; }
      else { const next = String(state.config[k] ?? ""); if (String(input.value ?? "") !== next) input.value = state.config[k]; }
    });
    renderArchive(background); renderActivities(background); renderReceipts(background); controls(busy);
    if (state.failure_code) notice(errors[state.failure_code] || "生活日程已暂停，请检查执行权限与设置。", true);
  }
  async function load(preserveNotice = false, background = false, manual = false) {
    if (!bridge) return;
    if (manual && loading) return;
    const seq = ++loadSeq;
    loading = true;
    if (!background) setRefreshState(true);
    if (manual) notice("");
    try {
      const next = await api("GET", {
        ...(datePages[datePage] ? { before: datePages[datePage] } : {}),
        ...(activityCursor ? { after_activity: activityCursor } : {}),
        ...(readingCursor ? { after_reading: readingCursor } : {}),
        ...(sleepCursor ? { after_sleep: sleepCursor } : {}),
      });
      if (seq !== loadSeq) return;
      state = next;
      if (!preserveNotice) notice(manual ? `刚刚已刷新 ${formatClock()}` : "");
      render(background);
      schedulePolling();
    } catch (error) {
      if (seq !== loadSeq) return;
      state = selected = null;
      resetRenderKeys();
      clearTimeout(polling); polling = null;
      ["journal-list", "journal-detail", "reading-list", "sleep-list", "receipt-list", "retry-id", "dream-retry"].forEach(id => $(id).replaceChildren());
      $("retry-form").hidden = true;
      for (const id of ["role", "participant", "sleep-scope", "sleep-executor", "sleep-next", "sleep-active-wake", "sleep-pressure", "sleep-debt", "sleep-workload", "sleep-recommended"]) $(id).textContent = "—";
      $("next-routines").textContent = "";
      $("next-routines").hidden = true;
      setMeter("sleep-pressure-bar", 0); setMeter("sleep-workload-bar", 0);
      for (const id of ["sleep-pressure-card", "sleep-debt-card", "sleep-workload-card", "sleep-recommended-card", "sleep-active-wake-card"]) setTone(id, "");
      $("sleep-debt-hint").textContent = "累计睡眠缺口";
      renderSleepSummaryPills([]);
      /* 配置/状态没读到时给出明确状态，而不是一排“—”：用户能分清是
         「没有配置」还是「这次没读到」，也知道下一步点哪里。 */
      $("routine-state").textContent = "读取失败";
      $("host-state").textContent = "暂不可用";
      setTone("life-toolbar", "danger");
      setTone("host-state-card", "danger");
      setTone("routine-state-card", "danger");
      $("settings").open = true;
      $("editor").close();
      notice(`${error.message} 配置暂时无法读取或保存，请点「刷新记录」重试。`, true);
      controls(false);
    } finally {
      if (seq === loadSeq) {
        loading = false;
        setRefreshState(false);
      }
    }
  }
  function open(action, item = null) {
    dialogAction = action; dialogItem = item;
    const titles = { add_text: "添加阅读原文", sleep: "开始睡眠", edit: "更正正文", restore: "主动恢复正文", delete: "删除这篇书写", cancel: "取消活动", withdraw: "撤回阅读原文", remove: "删除阅读记录", wake: "结束这段睡眠", dream: "记录或重试梦境" };
    $("dialog-title").textContent = titles[action];
    $("dialog-description").textContent = { withdraw: "整份原文及其所有片段将停止使用，依赖它的私人书写也将隐藏。", remove: "这条阅读记录会从书架移除；原文与依赖它的私人书写也会停止使用，删除后无法恢复。", delete: "删除后自动书写不会覆盖此记录，恢复需要主动填写正文。", wake: "确认醒来后，会独立记录这段睡眠的梦境。", add_text: "保存完整原文，较长资料会拆为连续阅读片段。收存不会标记已读。" }[action] || "确认当前记录后保存。";
    $("title-field").hidden = action !== "add_text";
    $("body-field").hidden = !["add_text", "edit", "restore"].includes(action);
    $("glow-field").hidden = !["edit", "restore"].includes(action) || item?.kind !== "dream";
    $("minutes-field").hidden = action !== "sleep"; $("retry-field").hidden = action !== "dream";
    $("text-title").value = ""; $("text-body").value = item?.body || ""; $("text-glow").value = item?.afterglow || ""; $("dream-retry").value = "";
    $("text-title").required = action === "add_text"; $("text-body").required = action === "add_text" || (["edit", "restore"].includes(action) && item?.kind !== "dream");
    $("minutes").value = state.config.sleep_minutes; $("dialog-error").hidden = true;
    $("editor").showModal();
  }
  $("sleep-overview")?.addEventListener?.("toggle", () => {
    if (!settingSleepOverview) sleepOverviewUserToggled = true;
  });
  document.querySelectorAll("[data-tab]").forEach(b => b.addEventListener("click", () => {
    tab = b.dataset.tab; document.querySelectorAll("[data-tab]").forEach(x => x.setAttribute("aria-current", x === b ? "page" : "false"));
    document.querySelectorAll(".tab-panel").forEach(p => { p.hidden = p.id !== tab; }); $("activity-pages").hidden = !["reading", "sleep"].includes(tab);
    syncSleepOverviewForTab(tab);
    controls(busy);
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
  $("refresh").addEventListener("click", () => load(false, false, true));
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
  $("more-activities").addEventListener("click", () => {
    if (tab === "reading" && "next_reading_activity" in state) readingCursor = state.next_reading_activity;
    else if (tab === "sleep" && "next_sleep_activity" in state) sleepCursor = state.next_sleep_activity;
    else activityCursor = state.next_activity;
    load();
  });
  $("first-activities").addEventListener("click", () => {
    if (tab === "reading" && "next_reading_activity" in (state || {})) readingCursor = null;
    else if (tab === "sleep" && "next_sleep_activity" in (state || {})) sleepCursor = null;
    else activityCursor = null;
    load();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clearTimeout(polling); polling = null; return; }
    if (bridge && !busy) load(true, true);
  });
  document.addEventListener("companion-mappings-change", () => { if (bridge && !busy) void load(true, true); });
  document.addEventListener("companion-context-change", event => {
    syncEmbeddedMode();
    if (event?.detail?.tab) {
      sleepOverviewUserToggled = false;
      syncSleepOverviewForTab(event.detail.tab);
    }
  });
  (async () => {
    try {
      await window.AstrBotPluginPage.ready();
      bridge = window.AstrBotPluginPage;
      syncEmbeddedMode();
      syncSleepOverviewForTab(bridge?.getContext?.()?.tab || tab);
      await load();
    } catch (_) { notice("无法连接管理界面，请从 AstrBot 插件页面重新打开。", true); }
  })();
})();
