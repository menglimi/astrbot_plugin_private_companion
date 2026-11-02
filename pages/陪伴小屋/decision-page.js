"use strict";
window.CompanionDecisionPage = (() => {
  const views = [
    ["overview", "layout-dashboard", "总览"],
    ["response", "bell", "回应判断"],
    ["tempo", "clock-3", "参与节奏"],
    ["safety", "shield", "沉默与安全"],
    ["guard", "shield-alert", "防骚扰"],
    ["group", "users-round", "群聊参与"],
  ];
  const replyViewSections = {
    response: ["wakeup_direct", "wakeup_interest", "wakeup_members"],
    tempo: ["wakeup_cold_group", "wakeup_fatigue"],
    safety: ["silence_policy", "group_air_guard", "private_closing", "group_content_safety"],
  };
  let root = null;
  let activeView = "overview";
  let replyStatus = null;
  const harassment = {endpoint: "", data: null, draft: null, loading: false, busy: false, notice: "", bad: false};

  const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const number = value => value == null || value === "" ? null
    : Number.isFinite(Number(value)) ? Number(value) : null;
  const count = value => {
    if (Array.isArray(value)) return value.filter(item => String(item).trim()).length;
    if (typeof value !== "string" || !value.trim()) return 0;
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed.filter(item => String(item).trim()).length;
    } catch {}
    return value.split(/[\n,，;；]/).map(item => item.trim()).filter(Boolean).length;
  };
  const on = (settings, key) => settings?.[key] === true;
  const ready = settings => Boolean(settings && Object.keys(settings).length);
  const flag = (settings, key) => ready(settings) ? (on(settings, key) ? "开启" : "关闭") : "—";
  const listCount = (settings, key) => ready(settings) ? `${count(settings[key])} 个` : "—";
  const shown = (value, suffix = "") => {
    const parsed = number(value);
    return parsed === null ? "—" : `${parsed}${suffix}`;
  };
  const setting = () => replyStatus?.settings || {};
  const participation = () => {
    const editor = root?.querySelector?.("#groupParticipationConfigWorkspace")?.companionConfigEditor;
    const groupEditor = root?.querySelector?.("#groupInterjectionConfigWorkspace")?.companionConfigEditor;
    const defaults = editor?.state?.draft || editor?.state?.base || editor?.state?.status?.settings || {};
    const local = groupEditor?.state?.draft || groupEditor?.state?.base || groupEditor?.state?.status?.settings || {};
    return {...defaults, ...local};
  };
  const stat = (label, value, note, tone = "") => `
    <article class="decision-stat${tone ? ` is-${tone}` : ""}">
      <span>${label}</span><strong>${value}</strong><small>${note}</small>
    </article>`;
  const signal = (label, value) => `<span class="decision-signal"><b>${label}</b><span>${value}</span></span>`;
  const badge = (label, active) => `<span class="decision-state${active ? " is-on" : ""}">${label} · ${active ? "开启" : "关闭"}</span>`;

  function overviewMarkup() {
    const s = setting();
    const g = participation();
    const replyReady = ready(s), groupReady = ready(g);
    const records = Array.isArray(replyStatus?.participation_recent) ? replyStatus.participation_recent : null;
    const limit = replyStatus?.participation_limit ?? s.group_wakeup_log_limit;
    const guardFields = ["enable_group_injection_guard", "enable_group_privacy_guard", "enable_group_reality_promise_guard"];
    const activeGuards = replyReady ? guardFields.filter(key => on(s, key)).length : null;
    const atmosphereFields = ["enable_group_mood_detection", "enable_group_roleplay_strength",
      "enable_group_moments", "enable_group_moment_portrait"];
    const atmosphereCount = groupReady
      ? on(g, "enable_group_social_context") ? atmosphereFields.filter(key => on(g, key)).length : 0 : null;
    const topicStatus = groupReady
      ? `${flag(g, "enable_group_topic_threads")}${on(g, "enable_group_topic_threads")
        ? ` · 最多保留 ${shown(g.group_topic_thread_max)} 条` : ""}` : "—";
    const directCount = replyReady ? count(s.group_wakeup_direct_words) : null;
    const interestCount = replyReady ? count(s.group_wakeup_interest_keywords) : null;
    const memberCount = replyReady ? count(s.group_wakeup_primary_member_ids) : null;
    return `
      <div class="decision-stats decision-stats-five">
        ${stat("近期群聊判定", records ? `${records.length} / ${shown(limit)} 条` : "—",
          records ? "当前群聊窗口 · 不含消息正文" : "选择群聊窗口后读取")}
        ${stat("参与疲劳上限", shown(s.group_wakeup_fatigue_limit, " 点"),
          `每 ${shown(s.group_wakeup_fatigue_decay_minutes)} 分钟恢复 1 点`)}
        ${stat("内容护栏", activeGuards === null ? "— / 3" : `${activeGuards} / 3 开`,
          activeGuards === null ? "读取配置后显示" : "注入 · 隐私 · 现实承诺", activeGuards === 3 ? "ok" : "")}
        ${stat("回应冷却", shown(s.group_wakeup_cooldown_seconds, " 秒"), "两次兴趣参与之间的最短间隔")}
        ${stat("判定记录上限", shown(limit, " 条"), records ? `当前保留 ${records.length} 条` : "只记录判定结果与原因")}
      </div>
      <section class="decision-chain" aria-labelledby="decision-chain-title">
        <div class="decision-block-heading"><div><h2 id="decision-chain-title">四段判断链</h2><p>每条群消息依次经过四段判断</p></div>
          <span class="decision-chain-rule">任一段未通过 · 不回应</span></div>
        <div class="decision-chain-grid">
          <button type="button" class="decision-chain-step" data-decision-open="response">
            <span class="decision-step-number">1</span><span class="decision-step-copy"><b>回应判断</b><small>直呼、提问、兴趣与图片相关性</small><em>${directCount === null ? "—" : `${directCount} 个`} 直呼词 · 提问识别 ${flag(s, "group_wakeup_question_enabled")}</em></span>${icon("arrow-right")}
          </button>
          <button type="button" class="decision-chain-step" data-decision-open="tempo">
            <span class="decision-step-number">2</span><span class="decision-step-copy"><b>参与节奏</b><small>冷群重启、等待、疲劳与冷却</small><em>冷群重启 ${flag(s, "group_wakeup_cold_group_enabled")} · 疲劳上限 ${shown(s.group_wakeup_fatigue_limit)} 点</em></span>${icon("arrow-right")}
          </button>
          <button type="button" class="decision-chain-step" data-decision-open="safety">
            <span class="decision-step-number">3</span><span class="decision-step-copy"><b>沉默与安全</b><small>撤回、收尾、空气护栏与内容边界</small><em>撤回沉默 ${flag(s, "recall_silence_enabled")} · 收尾 ${flag(s, "private_closing_guard_enabled")} · 空气护栏 ${flag(s, "group_air_guard_enabled")}</em></span>${icon("arrow-right")}
          </button>
          <button type="button" class="decision-chain-step" data-decision-open="group">
            <span class="decision-step-number">4</span><span class="decision-step-copy"><b>群聊参与</b><small>氛围与话题线供当前回复参考，插话或跟读另行判定</small><em>氛围参考 ${atmosphereCount === null ? "—" : `${atmosphereCount} / 4`} · 话题线 ${groupReady ? flag(g, "enable_group_topic_threads") : "—"} · 跟读 ${flag(g, "enable_group_repeat_follow")}</em></span>
          </button>
        </div>
      </section>
      <div class="decision-overview-columns">
        <section class="decision-overview-section">
          <div class="decision-block-heading"><div><h2>回应准入</h2><p>命中明确条件才直接参与，兴趣话题按概率判断</p></div></div>
          <div class="decision-signal-groups">
            <div><strong>明确参与信号</strong><div class="decision-signals">
              ${signal("直呼词", directCount === null ? "—" : `${directCount} 个`)}
              ${signal("角色名称", flag(s, "group_wakeup_bot_name_enabled"))}
              ${signal("提问识别", flag(s, "group_wakeup_question_enabled"))}
              ${signal("主要成员", memberCount === null ? "—" : `${memberCount} 人`)}
            </div></div>
            <div><strong>参考信号</strong><div class="decision-signals">
              ${signal("上下文关联", listCount(s, "group_wakeup_context_words"))}
              ${signal("兴趣关键词", interestCount === null ? "—" : `${interestCount} 个`)}
              ${signal("图片相关性", flag(s, "group_wakeup_image_enabled"))}
              ${signal("氛围参考", flag(g, "enable_group_social_context"))}
            </div></div>
          </div>
        </section>
        <section class="decision-overview-section">
          <div class="decision-block-heading"><div><h2>参考材料与快照</h2><p>只使用当前窗口允许读取的内容</p></div></div>
          <div class="decision-reference-list">
            <div><b>群聊氛围</b><span>${atmosphereCount === null ? "—" : `${atmosphereCount} / 4 项参考开启`}</span></div>
            <div><b>群聊话题线</b><span>${topicStatus}</span></div>
            <div><b>回复快照</b><span>${replyStatus?.apply_mode === "next_sealed_reply" ? "回合开始定格" : replyReady ? "按当前配置读取" : "—"}</span></div>
            <div><b>判定记录</b><span>${records ? `${records.length} 条 · 不含消息正文` : "切换到群聊窗口查看"}</span></div>
          </div>
        </section>
      </div>
      <nav class="decision-shortcuts" aria-label="相关设置">
        <button type="button" data-go="manage" data-manage="models"><span><b>模型与连接</b><small>判断与生成所用模型</small></span>${icon("arrow-up-right")}</button>
        <button type="button" data-go="manage" data-manage="understanding"><span><b>群聊回应理解</b><small>消息、图片与转发上下文</small></span>${icon("arrow-up-right")}</button>
        <button type="button" data-go="manage" data-manage="expression"><span><b>表达约束</b><small>说话方式与分段投递</small></span>${icon("arrow-up-right")}</button>
        <button type="button" data-go="manage" data-manage="learning"><span><b>群聊词义</b><small>群内表达与记忆资料</small></span>${icon("arrow-up-right")}</button>
      </nav>`;
  }

  function panelMarkup(view) {
    if (view === "guard") return guardMarkup();
    const s = setting();
    const g = participation();
    const replyReady = ready(s), groupReady = ready(g);
    if (view === "overview") return overviewMarkup();
    if (view === "response") {
      return `<div class="decision-stats decision-stats-four">
        ${stat("专属直呼词", listCount(s, "group_wakeup_direct_words"), "同一角色的群聊共用")}
        ${stat("兴趣候选概率", shown(s.group_wakeup_interest_probability), "候选通过筛选后仍可旁听")}
        ${stat("提问识别阈值", shown(s.group_wakeup_question_threshold), "0–100 · 越低越敏感")}
        ${stat("图片候选概率", flag(s, "group_wakeup_image_enabled"), `${shown(s.group_wakeup_image_weight)} 抽取概率`)}
      </div>`;
    }
    if (view === "tempo") {
      return `<div class="decision-stats decision-stats-three">
        ${stat("冷群重启", flag(s, "group_wakeup_cold_group_enabled"),
          on(s, "group_wakeup_cold_group_enabled") ? `静默 ${shown(s.group_wakeup_cold_group_idle_minutes)} 分钟后评估` : replyReady ? "只作为判断材料" : "读取配置后显示")}
        ${stat("参与疲劳上限", shown(s.group_wakeup_fatigue_limit, " 点"), `每 ${shown(s.group_wakeup_fatigue_decay_minutes)} 分钟恢复 1 点`)}
        ${stat("回应冷却", shown(s.group_wakeup_cooldown_seconds, " 秒"), "兴趣话题参与的最短间隔")}
      </div>`;
    }
    if (view === "safety") {
      const guards = replyReady ? ["enable_group_injection_guard", "enable_group_privacy_guard", "enable_group_reality_promise_guard"]
        .filter(key => on(s, key)).length : null;
      return `<div class="decision-stats decision-stats-four">
        ${stat("沉默策略", `${flag(s, "recall_silence_enabled")} · ${flag(s, "private_closing_guard_enabled")}`,
          "撤回沉默 · 私聊收尾沉默")}
        ${stat("内容护栏", guards === null ? "— / 3" : `${guards} / 3 开`,
          guards === null ? "读取配置后显示" : "注入 · 隐私 · 现实承诺", guards === 3 ? "ok" : "")}
        ${stat("空气护栏窗口", shown(s.group_air_guard_window_seconds, " 秒"), "窗口内统计角色自己的发言")}
        ${stat("回复快照", "回合开始定格", "拦截只调整措辞，不改变人格与授权")}
      </div>`;
    }
    const atmosphere = ["enable_group_mood_detection", "enable_group_roleplay_strength",
      "enable_group_moments", "enable_group_moment_portrait"].filter(key => on(g, key)).length;
    return `<div class="decision-stats decision-stats-four">
      ${stat("群聊话题线", flag(g, "enable_group_topic_threads"),
        groupReady ? `最多保留 ${shown(g.group_topic_thread_max)} 条 · 90 分钟无更新后失效` : "读取配置后显示")}
      ${stat("氛围参考", groupReady ? `${on(g, "enable_group_social_context") ? atmosphere : 0} / 4 开` : "— / 4",
        groupReady ? "氛围、扮演、名场面与人物线索" : "读取配置后显示")}
      ${stat("主动插话", flag(g, "enable_group_interjection"),
        groupReady ? `间隔 ${shown(g.group_interject_min_interval_minutes)} 分钟 · 每日上限 ${shown(g.group_interject_max_daily)} 次` : "读取配置后显示")}
      ${stat("复读跟读", flag(g, "enable_group_repeat_follow"),
        `阈值 ${shown(g.group_repeat_trigger_threshold)} 人次 · 概率 ${shown(g.group_repeat_follow_probability)}`)}
    </div>`;
  }

  function renderPanels() {
    if (!root) return;
    for (const [view] of views) {
      const panel = root.querySelector?.(`[data-decision-panel="${view}"]`);
      if (panel) panel.innerHTML = panelMarkup(view);
    }
    const current = root.querySelector?.("[data-decision-current]");
    if (current) {
      const s = setting(), g = participation();
      const replyReady = ready(s);
      const states = {
        overview: "四段独立 · 只回应不发起",
        response: `总闸 · ${replyReady ? on(s, "group_wakeup_enabled") ? "默认开" : "已关闭" : "读取中"}`,
        tempo: `冷群重启 · ${flag(s, "group_wakeup_cold_group_enabled")}`,
        safety: `沉默 · ${flag(s, "recall_silence_enabled")} · 护栏 ${replyReady ? ["enable_group_injection_guard", "enable_group_privacy_guard", "enable_group_reality_promise_guard"].filter(key => on(s, key)).length + " / 3" : "读取中"}`,
        guard: harassment.data ? `已拦截 ${harassment.data.entries.length} 人 · ${harassment.data.settings.enabled ? "已开启" : "未开启"}` : "读取中",
        group: `复读跟读 · ${flag(g, "enable_group_repeat_follow")}`,
      };
      current.textContent = states[activeView] || states.overview;
    }
  }

  function setView(view) {
    if (!views.some(([id]) => id === view) || !root) return false;
    activeView = view;
    root.dataset.activeView = view;
    for (const button of root.querySelectorAll?.("[data-decision-tab]") || []) {
      const active = button.dataset.decisionTab === view;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      button.classList.toggle("is-active", active);
    }
    for (const panel of root.querySelectorAll?.("[data-decision-panel]") || []) {
      panel.hidden = panel.dataset.decisionPanel !== view;
    }
    const reply = root.querySelector?.("#decisionReplyWorkspace");
    if (reply) reply.hidden = !["response", "tempo", "safety"].includes(view);
    const groupWrap = root.querySelector?.("[data-decision-editor='participation']");
    if (groupWrap) groupWrap.hidden = !["safety", "group"].includes(view);
    const groupOnly = root.querySelector?.("[data-decision-group-only]");
    if (groupOnly) groupOnly.hidden = view !== "group";
    const jokeHeading = root.querySelector?.("[data-decision-joke-heading]");
    if (jokeHeading) jokeHeading.hidden = view !== "safety";
    const domainLink = root.querySelector?.("[data-decision-domain-link]");
    if (domainLink) domainLink.hidden = view !== "group";
    if (view === "guard") void loadHarassment();
    window.CompanionReplyPage?.setDecisionView?.(view);
    const groupEditor = root.querySelector?.("#groupParticipationConfigWorkspace")?.companionConfigEditor;
    const groupSections = view === "safety" ? ["joke_guard"]
      : view === "group" ? ["topics", "atmosphere", "repeat"] : [];
    groupEditor?.setVisibleGroups?.(groupSections, groupSections[0] || "");
    renderPanels();
    return true;
  }

  function onClick(event) {
    const button = event.target?.closest?.("[data-decision-tab], [data-decision-open]");
    if (!button) return;
    const view = button.dataset.decisionTab || button.dataset.decisionOpen;
    if (view) setView(view);
  }

  function onKeydown(event) {
    const button = event.target?.closest?.("[data-decision-tab]");
    if (!button) return;
    const index = views.findIndex(([id]) => id === button.dataset.decisionTab);
    const next = event.key === "ArrowRight" ? (index + 1) % views.length
      : event.key === "ArrowLeft" ? (index - 1 + views.length) % views.length
        : event.key === "Home" ? 0 : event.key === "End" ? views.length - 1 : -1;
    if (next < 0) return;
    event.preventDefault();
    const target = root?.querySelector?.(`[data-decision-tab="${views[next][0]}"]`);
    target?.focus?.();
    setView(views[next][0]);
  }

  function onGroupInput(event) {
    if (event.target?.closest?.("#groupParticipationConfigWorkspace, #groupInterjectionConfigWorkspace")) renderPanels();
  }

  function render(groupSettingsMarkup = "") {
    if (!root) return;
    const tabs = views.map(([id, iconName, label]) => `
      <button type="button" role="tab" class="decision-tab${activeView === id ? " is-active" : ""}"
        id="decision-tab-${id}" aria-controls="decision-panel-${id}" aria-selected="${activeView === id}"
        tabindex="${activeView === id ? "0" : "-1"}" data-decision-tab="${id}">
        ${icon(iconName)}<span>${label}</span></button>`).join("");
    const panels = views.map(([id]) => `<section id="decision-panel-${id}" class="decision-panel"
      role="tabpanel" aria-labelledby="decision-tab-${id}" data-decision-panel="${id}"${activeView === id ? "" : " hidden"}></section>`).join("");
    root.innerHTML = `<div class="decision-page" data-active-view="${activeView}">
      <nav class="decision-tabs" aria-label="群聊决策分区">
        <div class="decision-tab-list" role="tablist">${tabs}</div>
        <span class="decision-current" data-decision-current></span></nav>
      <div class="decision-panels">${panels}</div>
      <div class="decision-editors">
        <section id="decisionReplyWorkspace" class="decision-reply-workspace" aria-label="回应判断设置" hidden></section>
        <div class="decision-group-workspace" data-decision-editor="participation" hidden>
          <div class="decision-joke-heading" data-decision-joke-heading hidden><span>附加边界</span><h3>玩笑护栏</h3></div>
          ${groupSettingsMarkup}
        </div>
      </div>
      <button type="button" class="decision-domain-link" data-decision-domain-link data-go="manage" data-manage="expression" hidden>
        <span><b>表达去噪与腔调</b><small>查看表达页设置</small></span>${icon("arrow-up-right")}
      </button>
    </div>`;
    root.removeEventListener?.("click", onClick);
    root.removeEventListener?.("keydown", onKeydown);
    root.removeEventListener?.("input", onGroupInput);
    root.removeEventListener?.("change", onGroupInput);
    root.addEventListener?.("click", onClick);
    root.addEventListener?.("keydown", onKeydown);
    root.addEventListener?.("input", onGroupInput);
    root.addEventListener?.("change", onGroupInput);
    root.removeEventListener?.("click", onGuardClick);
    root.removeEventListener?.("input", onGuardInput);
    root.removeEventListener?.("change", onGuardInput);
    root.addEventListener?.("click", onGuardClick);
    root.addEventListener?.("input", onGuardInput);
    root.addEventListener?.("change", onGuardInput);
    renderPanels();
    setView(activeView);
    window.lucide?.createIcons?.({nodes: root.querySelectorAll?.("[data-lucide]")});
  }


  function guardEscape(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
  }

  function guardTime(value) {
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds <= 0) return "—";
    const date = new Date(seconds * 1000);
    return date.toLocaleString("zh-CN", {hour12: false});
  }

  function guardMarkup() {
    const data = harassment.data;
    if (!harassment.endpoint) {
      return `<div class="decision-empty"><strong>防骚扰不可用</strong><p>当前页面没有拿到设置接口，请刷新后重试。</p></div>`;
    }
    if (harassment.loading && !data) return `<div class="decision-empty"><strong>正在读取防骚扰设置…</strong></div>`;
    const draft = harassment.draft || (data ? {...data.settings, keywords: data.settings.keywords || ""} : null);
    if (!draft) {
      return `<div class="decision-empty"><strong>暂时无法读取防骚扰设置</strong><p>${guardEscape(harassment.notice || "请稍后重试。")}</p></div>`;
    }
    const entries = data?.entries || [];
    const rows = entries.length ? entries.map(item => `
      <tr>
        <td><code>${guardEscape(item.user_id)}</code><small>${guardEscape(item.scope)}</small></td>
        <td>${guardEscape(item.reason_text || item.reason || "手动加入")}${item.count ? ` · ${Number(item.count)} 条` : ""}</td>
        <td>${guardTime(item.updated_at || item.created_at)}</td>
        <td><button type="button" class="button small" data-guard-unblock="${guardEscape(item.user_id)}"
          data-guard-scope="${guardEscape(item.scope)}" ${harassment.busy ? "disabled" : ""}>解除</button></td>
      </tr>`).join("") : `<tr><td colspan="4" class="decision-empty-row">黑名单还是空的。命中限流或关键词后，对应用户会自动出现在这里。</td></tr>`;
    return `<div class="guard-panel">
      <div class="decision-stats decision-stats-four">
        ${stat("限流", draft.enabled ? "已开启" : "未开启", `每 ${shown(draft.window_seconds)} 秒窗口`)}
        ${stat("阈值", `${shown(draft.max_messages)} 条`, "超过后自动移入黑名单")}
        ${stat("关键词", count(draft.keywords) + " 个", "命中任意一个也会拦截")}
        ${stat("当前黑名单", `${entries.length} 人`, "可随时解除")}
      </div>
      <div class="guard-settings">
        <label class="guard-switch"><input type="checkbox" data-guard-field="enabled" ${draft.enabled ? "checked" : ""} ${harassment.busy ? "disabled" : ""}>启用防骚扰</label>
        <label>时间窗口（秒）<input type="number" min="10" max="86400" data-guard-field="window_seconds" value="${guardEscape(draft.window_seconds)}" ${harassment.busy ? "disabled" : ""}></label>
        <label>窗口内最多消息<input type="number" min="2" max="500" data-guard-field="max_messages" value="${guardEscape(draft.max_messages)}" ${harassment.busy ? "disabled" : ""}></label>
        <label class="guard-wide">触发关键词（逗号或换行分隔）<textarea rows="3" data-guard-field="keywords" ${harassment.busy ? "disabled" : ""}>${guardEscape(draft.keywords)}</textarea></label>
        <button type="button" class="button primary" data-guard-save ${harassment.busy ? "disabled" : ""}>保存防骚扰设置</button>
      </div>
      <div class="guard-directory">
        <div class="guard-heading"><h3>黑名单目录</h3><span>${entries.length} 人</span></div>
        <table class="guard-table"><thead><tr><th>用户</th><th>原因</th><th>时间</th><th></th></tr></thead>
          <tbody>${rows}</tbody></table>
      </div>
      ${harassment.notice ? `<div class="model-notice${harassment.bad ? " error" : ""}">${guardEscape(harassment.notice)}</div>` : ""}
    </div>`;
  }

  async function guardRequest(method, path, body) {
    const bridge = window.CompanionProduct?.bridgeNow?.() || await window.CompanionProduct?.bridgeFor?.();
    if (!bridge) throw new Error("bridge_unavailable");
    const endpoint = harassment.endpoint + path;
    const response = method === "GET" ? await bridge.apiGet(endpoint) : await bridge.apiPost(endpoint, body);
    const wrapped = ["ok", "error"].includes(response?.status) && response?.data !== undefined ? response
      : ["ok", "error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") throw Object.assign(new Error(wrapped.message || "request_failed"), {code: wrapped.message});
    const value = wrapped ? wrapped.data : response;
    if (!value || typeof value !== "object") throw new Error("invalid_response");
    return value;
  }

  async function loadHarassment() {
    if (!harassment.endpoint || harassment.loading || harassment.busy) return;
    harassment.loading = true;
    harassment.notice = "";
    renderPanels();
    try {
      const data = await guardRequest("GET", "");
      harassment.data = data;
      harassment.draft = {...data.settings};
      harassment.bad = false;
    } catch (error) {
      harassment.bad = true;
      harassment.notice = "读取防骚扰设置失败：" + (error?.message || "未知错误");
    } finally {
      harassment.loading = false;
      renderPanels();
    }
  }

  async function saveHarassment() {
    if (!harassment.draft || harassment.busy) return;
    harassment.busy = true;
    harassment.notice = "正在保存防骚扰设置…";
    renderPanels();
    try {
      const data = await guardRequest("POST", "/settings", {changes: harassment.draft});
      harassment.data = data;
      harassment.draft = {...data.settings};
      harassment.notice = "防骚扰设置已保存。";
      harassment.bad = false;
    } catch (error) {
      harassment.bad = true;
      harassment.notice = "保存失败：" + (error?.message || "未知错误");
    } finally {
      harassment.busy = false;
      renderPanels();
    }
  }

  async function unblockHarassment(userId, scope) {
    if (harassment.busy) return;
    harassment.busy = true;
    harassment.notice = "正在解除…";
    renderPanels();
    try {
      const data = await guardRequest("POST", "/unblock", {scope, user_id: userId});
      harassment.data = {...(harassment.data || {}), entries: data.entries};
      harassment.notice = "已解除该用户的拦截。";
      harassment.bad = false;
    } catch (error) {
      harassment.bad = true;
      harassment.notice = "解除失败：" + (error?.message || "未知错误");
    } finally {
      harassment.busy = false;
      renderPanels();
    }
  }

  function onGuardInput(event) {
    const field = event.target?.dataset?.guardField;
    if (!field) return;
    harassment.draft ||= {};
    if (field === "enabled") harassment.draft.enabled = !!event.target.checked;
    else if (field === "window_seconds" || field === "max_messages") harassment.draft[field] = Number(event.target.value);
    else harassment.draft[field] = event.target.value;
  }

  function onGuardClick(event) {
    const save = event.target?.closest?.("[data-guard-save]");
    if (save) { void saveHarassment(); return; }
    const unblock = event.target?.closest?.("[data-guard-unblock]");
    if (unblock) {
      void unblockHarassment(unblock.dataset.guardUnblock, unblock.dataset.guardScope);
      return;
    }
    if (event.target?.closest?.("[data-guard-retry]")) void loadHarassment();
  }

  function mount(node, options = {}) {
    if (!node) return;
    if (root !== node) {
      unmount();
      root = node;
    }
    harassment.endpoint = typeof options.harassmentApi === "string" ? options.harassmentApi : "";
    render(options.groupSettingsMarkup || "");
    if (harassment.endpoint && !harassment.data) void loadHarassment();
  }

  function updateReplyStatus(status) {
    replyStatus = status || null;
    renderPanels();
  }

  function updateParticipationStatus() {
    renderPanels();
  }

  function unmount() {
    if (root) {
      root.removeEventListener?.("click", onClick);
      root.removeEventListener?.("keydown", onKeydown);
      root.removeEventListener?.("input", onGroupInput);
      root.removeEventListener?.("change", onGroupInput);
      root.removeEventListener?.("click", onGuardClick);
      root.removeEventListener?.("input", onGuardInput);
      root.removeEventListener?.("change", onGuardInput);
    }
    root = null;
    harassment.endpoint = "";
    harassment.data = null;
    harassment.draft = null;
    harassment.notice = "";
    replyStatus = null;
  }

  return {mount, unmount, setView, updateReplyStatus, updateParticipationStatus};
})();
