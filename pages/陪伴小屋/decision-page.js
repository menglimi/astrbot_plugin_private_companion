"use strict";
window.CompanionDecisionPage = (() => {
  /* 第四项是页签的作用域，省略表示哪个窗口都能看。防骚扰是私聊入站守卫：
     这组接口只挂在私聊 reply 路由族下，群聊 reply 路由族没有注册它，所以它
     只在私聊窗口出现——暴露在群聊窗口里只会是一张永远读不到数据的空页。 */
  const views = [
    ["overview", "layout-dashboard", "总览"],
    ["response", "bell", "回应判断"],
    ["tempo", "clock-3", "参与节奏"],
    ["safety", "shield", "沉默与安全"],
    ["guard", "shield-alert", "防骚扰", "private"],
    ["group", "users-round", "群聊参与"],
  ];
  const replyViewSections = {
    response: ["wakeup_direct", "wakeup_interest", "wakeup_members"],
    tempo: ["group_continuation", "private_debounce", "wakeup_cold_group", "wakeup_fatigue"],
    safety: ["silence_policy", "group_air_guard", "private_closing", "group_content_safety"],
  };
  let root = null;
  let activeView = "overview";
  let replyStatus = null;
  /* 当前窗口的作用域（private / group）。由挂载方给出；空串表示还没读到，
     按「不知道」处理，只显示不分作用域的页签。 */
  let scopeKind = "";
  /* 群聊参与的两个挂载点各自上报自己那一段，必须分开存：角色层上报的是整组
     28 个字段（含按角色层解析的插话三键），本群窗口只上报 5 个插话键。塞进
     同一个槽，后到的角色层上报就会把本群覆盖值打回角色层默认。 */
  let roleStatus = null;
  let windowStatus = null;
  const reportedStatus = mount => mount === "window" ? windowStatus : roleStatus;
  const harassment = {endpoint: "", data: null, draft: null, loading: false, busy: false,
    notice: "", bad: false, missing: false, tab: "detect", manual: {scope: "", user: "", hours: 24},
    exported: ""};

  /* 页签按作用域收口：群聊窗口不出现只面向私聊的页签，也不替它发请求。
     没读到作用域（空串）时按「不知道」处理，同样不显示——宁可晚一帧出现，
     也不要在群聊窗口里先摆一张读不到数据的页。 */
  const scopeViews = () => views.filter(([, , , scope]) => !scope || scope === scopeKind);
  const viewScoped = id => scopeViews().some(([view]) => view === id);
  const harassmentApplies = () => scopeKind === "private";
  /* 宿主匹配不到路由时回的是「未找到该路由」：请求根本没进插件，重试与刷新
     都会得到同一句话，所以这类失败不再给重试按钮，只说明它为何不适用。 */
  const routeMissing = error => /未找到该路由|route (?:was )?not found|no matching route/i.test(
    String(error?.message || error?.code || ""));

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
  /* 群聊参与的分区挂在两处挂载点，各自上报自己那一段：角色层默认
     （#groupParticipationConfigWorkspace，整组 28 个字段）与本群窗口
     （#groupInterjectionConfigWorkspace，只有 5 个插话键）。两处拿到的都是
     适配层对象（config-editor.js 的返回），它的 state 里只有 instances；
     真正的 status/draft/base 在内核实例上。所以取值走两条路：挂载点上的
     onStatus 上行回调，还没到就回落到实例数组上的草稿。 */
  const participationEditors = () => ({
    role: root?.querySelector?.("#groupParticipationConfigWorkspace")?.companionConfigEditor || null,
    window: root?.querySelector?.("#groupInterjectionConfigWorkspace")?.companionConfigEditor || null,
  });
  const instanceState = editor => {
    const list = Array.isArray(editor?.state?.instances) ? editor.state.instances : [];
    for (const instance of list) {
      if (instance?.state && typeof instance.state === "object") return instance.state;
    }
    return null;
  };
  const instanceSettings = state => {
    const value = state?.draft || state?.base || state?.status?.settings;
    return value && typeof value === "object" ? value : null;
  };
  /* 一个挂载点的读取状态。ready 只在**这个来源确实读到过配置**时成立；读取
     失败（内核把实例标成 denied 并清空 draft/base）单独成态——「读不到」与
     「读到的是关闭」是两件不同的事实，合成一句就会把前者说成后者。 */
  const participationSource = mount => {
    const reported = reportedStatus(mount);
    const state = instanceState(participationEditors()[mount]);
    const settings = instanceSettings(state)
      || (reported?.settings && typeof reported.settings === "object" ? reported.settings : null);
    if (state?.denied === true) return {phase: "failed", settings};
    if (settings) return {phase: "ready", settings};
    if (state) return {phase: "loading", settings: null};
    return {phase: reported ? "ready" : "empty", settings};
  };
  /* 合并顺序：角色层默认在前，本群窗口在后覆盖同名键。
     本群挂载点上报的 5 个插话键就是这一群解析后的有效值（本群没改过时等于
     角色层），所以窗口层必须压住角色层，这正是「角色默认 + 本群细调」的语义；
     反过来合并会让本群细调被角色层的下一次刷新打回默认。其余 23 个字段只有
     角色层提供，不受合并顺序影响。
     只并入已读到的来源：读失败的那一侧留下的旧草稿不能当成当前值参与合并。 */
  const participationLayer = mount => {
    const source = participationSource(mount);
    return source.phase === "ready" && source.settings ? source.settings : null;
  };
  const participation = () => ({
    ...participationLayer("role"),
    ...participationLayer("window"),
  });
  const stat = (label, value, note, tone = "") => `
    <article class="decision-stat${tone ? ` is-${tone}` : ""}">
      <span>${label}</span><strong>${value}</strong><small>${note}</small>
    </article>`;
  const pending = () => '<span aria-busy="true">正在读取…</span>';
  /* 一张卡只对**它自己的来源**负责：来源没读到配置时，值写「—」、说明写这个
     来源的状态，绝不写「该能力未开启」——那是把「读不到」说成「没开」。 */
  const card = (label, mount, value, note) => {
    const source = participationSource(mount);
    if (source.phase === "ready") return stat(label, value, note);
    if (source.phase === "failed") {
      return stat(label, "—",
        `读取失败 <button type="button" class="button small" data-participation-retry="${mount}">重新读取</button>`);
    }
    return stat(label, "—", source.phase === "loading" ? pending() : "未选择群聊");
  };
  /* 顶栏那一行同样按来源说真话：来源没读到就不写成「关闭」。 */
  const sourceFlag = (mount, settings, key) => {
    const phase = participationSource(mount).phase;
    if (phase === "ready") return flag(settings, key);
    return phase === "loading" ? "读取中" : phase === "failed" ? "读取失败" : "—";
  };
  const signal = (label, value) => `<span class="decision-signal"><b>${label}</b><span>${value}</span></span>`;
  const badge = (label, active) => `<span class="decision-state${active ? " is-on" : ""}">${label} · ${active ? "开启" : "关闭"}</span>`;

  function overviewMarkup() {
    const s = setting();
    const g = participation();
    const replyReady = ready(s), roleReady = participationSource("role").phase === "ready";
    const records = Array.isArray(replyStatus?.participation_recent) ? replyStatus.participation_recent : null;
    const limit = replyStatus?.participation_limit ?? s.group_wakeup_log_limit;
    const guardFields = ["enable_group_injection_guard", "enable_group_privacy_guard", "enable_group_reality_promise_guard"];
    const activeGuards = replyReady ? guardFields.filter(key => on(s, key)).length : null;
    const atmosphereFields = ["enable_group_mood_detection", "enable_group_roleplay_strength",
      "enable_group_moments", "enable_group_moment_portrait"];
    const atmosphereCount = roleReady
      ? on(g, "enable_group_social_context") ? atmosphereFields.filter(key => on(g, key)).length : 0 : null;
    const topicStatus = roleReady
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
          activeGuards === null ? pending() : "注入 · 隐私 · 现实承诺", activeGuards === 3 ? "ok" : "")}
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
            <span class="decision-step-number">4</span><span class="decision-step-copy"><b>群聊参与</b><small>氛围与话题线供当前回复参考，插话或跟读另行判定</small><em>氛围参考 ${atmosphereCount === null ? "—" : `${atmosphereCount} / 4`} · 话题线 ${sourceFlag("role", g, "enable_group_topic_threads")} · 跟读 ${sourceFlag("role", g, "enable_group_repeat_follow")}</em></span>
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
              ${signal("氛围参考", sourceFlag("role", g, "enable_group_social_context"))}
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
    const replyReady = ready(s);
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
          on(s, "group_wakeup_cold_group_enabled") ? `静默 ${shown(s.group_wakeup_cold_group_idle_minutes)} 分钟后评估` : replyReady ? "只作为判断材料" : pending())}
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
          guards === null ? pending() : "注入 · 隐私 · 现实承诺", guards === 3 ? "ok" : "")}
        ${stat("空气护栏窗口", shown(s.group_air_guard_window_seconds, " 秒"), "窗口内统计角色自己的发言")}
        ${stat("回复快照", "回合开始定格", "拦截只调整措辞，不改变人格与授权")}
      </div>`;
    }
    const atmosphere = ["enable_group_mood_detection", "enable_group_roleplay_strength",
      "enable_group_moments", "enable_group_moment_portrait"].filter(key => on(g, key)).length;
    /* 四张卡各自的来源（与配置分区声明一致）：话题线、氛围、复读来自角色层
       默认挂载点；主动插话整段挂在本群窗口挂载点（那里只上报 5 个插话键）。 */
    return `<div class="decision-stats decision-stats-four">
      ${card("群聊话题线", "role", flag(g, "enable_group_topic_threads"),
        on(g, "enable_group_topic_threads")
          ? `最多保留 ${shown(g.group_topic_thread_max)} 条 · 90 分钟无更新后失效` : "该能力未开启")}
      ${card("氛围参考", "role", on(g, "enable_group_social_context") ? `${atmosphere} / 4 开` : "0 / 4 开",
        on(g, "enable_group_social_context") ? "氛围、扮演、名场面与人物线索" : "该能力未开启")}
      ${card("主动插话", "window", flag(g, "enable_group_interjection"),
        on(g, "enable_group_interjection")
          ? `间隔 ${shown(g.group_interject_min_interval_minutes)} 分钟 · 每日上限 ${shown(g.group_interject_max_daily)} 次`
          : "该能力未开启")}
      ${card("复读跟读", "role", flag(g, "enable_group_repeat_follow"),
        on(g, "enable_group_repeat_follow")
          ? `阈值 ${shown(g.group_repeat_trigger_threshold)} 人次 · 概率 ${shown(g.group_repeat_follow_probability)}`
          : "该能力未开启")}
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
        guard: !harassmentApplies() ? "只对私聊窗口生效"
          : harassment.data ? `已拦截 ${harassment.data.entries.length} 人 · ${harassment.data.settings.enabled ? "已开启" : "未开启"}` : "读取中",
        group: `复读跟读 · ${sourceFlag("role", g, "enable_group_repeat_follow")}`,
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
    if (view === "guard" && harassmentApplies()) void loadHarassment();
    window.CompanionReplyPage?.setDecisionView?.(view);
    const groupEditor = root.querySelector?.("#groupParticipationConfigWorkspace")?.companionConfigEditor;
    const groupSections = view === "safety" ? ["joke_guard"]
      : view === "group" ? ["topics", "atmosphere", "repeat"] : [];
    groupEditor?.setVisibleGroups?.(groupSections, groupSections[0] || "");
    renderPanels();
    return true;
  }

  function onClick(event) {
    const retry = event.target?.closest?.("[data-participation-retry]");
    if (retry) {
      /* 只重读这一张卡的来源：读失败的那一处才需要重新发起，另一处不该被连带
         清空重绘。没有点名来源时两处都读，避免旧页面元素上没有这个属性。 */
      const editors = participationEditors();
      const mount = retry.dataset?.participationRetry;
      if (mount === "role" || mount === "window") editors[mount]?.read?.();
      else for (const editor of Object.values(editors)) editor?.read?.();
      renderPanels();
      return;
    }
    const button = event.target?.closest?.("[data-decision-tab], [data-decision-open]");
    if (!button) return;
    const view = button.dataset.decisionTab || button.dataset.decisionOpen;
    if (view) setView(view);
  }

  function onKeydown(event) {
    const button = event.target?.closest?.("[data-decision-tab]");
    if (!button) return;
    /* 只在当前作用域真的露出的页签之间移动：跨到没渲染的那一个，
       焦点会落到 null 上，按键就断了。 */
    const tabs = scopeViews();
    if (tabs.length < 2) return;
    const index = tabs.findIndex(([id]) => id === button.dataset.decisionTab);
    const next = event.key === "ArrowRight" ? (index + 1) % tabs.length
      : event.key === "ArrowLeft" ? (index - 1 + tabs.length) % tabs.length
        : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
    if (next < 0) return;
    event.preventDefault();
    const target = root?.querySelector?.(`[data-decision-tab="${tabs[next][0]}"]`);
    target?.focus?.();
    setView(tabs[next][0]);
  }

  function onGroupInput(event) {
    if (event.target?.closest?.("#groupParticipationConfigWorkspace, #groupInterjectionConfigWorkspace")) renderPanels();
  }

  function render(groupSettingsMarkup = "") {
    if (!root) return;
    const tabs = scopeViews().map(([id, iconName, label]) => `
      <button type="button" role="tab" class="decision-tab${activeView === id ? " is-active" : ""}"
        id="decision-tab-${id}" aria-controls="decision-panel-${id}" aria-selected="${activeView === id}"
        tabindex="${activeView === id ? "0" : "-1"}" data-decision-tab="${id}">
        ${icon(iconName)}<span>${label}</span></button>`).join("");
    /* 面板按全部页签渲染，只有没有页签的那一个改用 aria-label：外部入口
       （搜索设置、上次停留的视图）落到不适用于当前作用域的页时，给一段说明
       文字，而不是一片空白。 */
    const panels = views.map(([id, , label]) => `<section id="decision-panel-${id}" class="decision-panel"
      role="tabpanel" ${viewScoped(id) ? `aria-labelledby="decision-tab-${id}"` : `aria-label="${label}"`}
      data-decision-panel="${id}"${activeView === id ? "" : " hidden"}></section>`).join("");
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


  const guardEscape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
  const guardTime = value => {
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds <= 0) return "永久";
    return new Date(seconds * 1000).toLocaleString("zh-CN", {hour12: false});
  };
  const guardCount = value => {
    const text = String(value ?? "").replace(/，/g, ",").replace(/\n/g, ",");
    return text.split(",").map(item => item.trim()).filter(Boolean).length;
  };
  const guardDetectors = [
    {key: "rate_enabled", label: "刷屏频率", hint: "窗口内消息总数超过上限", window: "rate_window_seconds",
      max: "rate_max_messages", unit: "条", seconds: true},
    {key: "burst_enabled", label: "短时突发", hint: "更短的窗口内连续消息", window: "burst_window_seconds",
      max: "burst_max_messages", unit: "条", seconds: true},
    {key: "duplicate_enabled", label: "复读", hint: "同一句话反复发送", window: "duplicate_window_seconds",
      max: "duplicate_max_repeats", unit: "次", seconds: true},
    {key: "mention_enabled", label: "@轰炸", hint: "窗口内 @ 次数", window: "mention_window_seconds",
      max: "mention_max", unit: "次", seconds: true},
    {key: "link_enabled", label: "链接轰炸", hint: "窗口内链接数量", window: "link_window_seconds",
      max: "link_max", unit: "条", seconds: true},
    {key: "media_enabled", label: "媒体刷屏", hint: "只发表情、图片或语音", window: "media_window_seconds",
      max: "media_max", unit: "条", seconds: true},
    {key: "split_enabled", label: "拆句轰炸", hint: "连续发很短的句子", window: "split_window_seconds",
      max: "split_max", unit: "条", seconds: true},
  ];
  const guardTabs = [["detect", "拦截判断"], ["action", "拦截处置"], ["list", "黑名单管理"]];
  const guardField = (key, label, type, draft, extra = "") => {
    const value = draft[key];
    if (type === "bool") return `<label class="guard-check"><input type="checkbox" data-guard-field="${key}" ${value ? "checked" : ""} ${extra}>${label}</label>`;
    if (type === "number") return `<label>${label}<input type="number" data-guard-field="${key}" value="${guardEscape(value)}" ${extra}></label>`;
    if (type === "select") {
      return `<label>${label}<select data-guard-field="${key}" ${extra}>${["silent", "text"].map(item =>
        `<option value="${item}" ${value === item ? "selected" : ""}>${item === "silent" ? "静默不回复" : "回一句固定话"}</option>`).join("")}</select></label>`;
    }
    if (type === "textarea") return `<label class="guard-wide">${label}<textarea rows="3" data-guard-field="${key}" ${extra}>${guardEscape(value)}</textarea></label>`;
    return `<label class="guard-wide">${label}<input type="text" data-guard-field="${key}" value="${guardEscape(value)}" ${extra}></label>`;
  };

  function guardMarkup() {
    const data = harassment.data;
    /* 三态分开写：不适用于当前作用域、正在读、读失败。第一种是说明文字，
       不发请求也没有按钮；最后一种才给重试，而「未找到该路由」连重试都不给。 */
    if (!harassmentApplies()) return `<div class="decision-empty" role="status">
      <strong>防骚扰只对私聊窗口生效</strong>
      <p>它拦的是进入模型的私聊消息，群聊窗口没有这组设置。切到这个角色的私聊窗口后再调整。</p></div>`;
    if (!harassment.endpoint) return `<div class="decision-empty" role="status">
      <strong>没有拿到防骚扰接口</strong>
      <p>当前窗口的回复路由没有提供这组设置，可以重新载入页面再看。</p></div>`;
    /* 还没有快照也没有失败记录，就是还没读到：挂载后第一次渲染也走这里，
       不会先闪一下「读取失败 + 重试」。 */
    if (!data && !harassment.draft && !harassment.bad) return `<div class="decision-empty" role="status" aria-busy="true">
      <strong>正在读取防骚扰设置…</strong></div>`;
    const draft = harassment.draft;
    if (!draft) {
      /* 路由没注册时请求根本没进插件，重试与刷新都是同一句话，所以不给按钮。 */
      if (harassment.missing) return `<div class="decision-empty" role="status">
        <strong>当前插件版本未提供防骚扰接口</strong>
        <p>这组接口只对私聊窗口提供。若已经处在私聊窗口，说明这个版本还没有防骚扰设置，后续版本会带上。</p></div>`;
      return `<div class="decision-empty" role="status">
        <strong>暂时无法读取防骚扰设置</strong><p>${guardEscape(harassment.notice || "请稍后重试。")}</p>
      <button type="button" class="button small" data-guard-retry>重试</button></div>`;
    }
    const disabled = harassment.busy ? "disabled" : "";
    const tabs = guardTabs.map(([id, label]) => `<button type="button" class="section-chip${harassment.tab === id ? " is-active" : ""}"
      data-guard-tab="${id}" aria-pressed="${harassment.tab === id}">${label}</button>`).join("");
    let body = "";
    if (harassment.tab === "detect") {
      const keyword = `<div class="guard-settings">
        ${guardField("keywords_strong", "强词（命中直接拉黑）", "textarea", draft, disabled)}
        ${guardField("keywords_weak", "弱词（命中先冷却）", "textarea", draft, disabled)}
        ${guardField("keyword_regex", "正则（可选）", "text", draft, disabled)}
        ${guardField("judge_enabled", "判断模型（预留，当前版本未接入）", "bool", draft, "disabled")}
        ${guardField("model_tag_enabled", "模型输出标签（预留，当前版本未接入）", "bool", draft, "disabled")}
      </div>
      <div class="guard-signals">${guardDetectors.map(item => `<div class="guard-signal">
        ${guardField(item.key, item.label, "bool", draft, disabled)}
        <small>${item.hint}</small>
        <div class="guard-signal-params">
          <label>窗口（秒）<input type="number" data-guard-field="${item.window}" value="${guardEscape(draft[item.window])}" ${disabled}></label>
          <label>上限（${item.unit}）<input type="number" data-guard-field="${item.max}" value="${guardEscape(draft[item.max])}" ${disabled}></label>
        </div></div>`).join("")}</div>`;
      body = `${keyword}${body}${keyword ? "" : ""}`;
    } else if (harassment.tab === "action") {
      body = `<div class="guard-settings">
        ${guardField("dry_run", "试运行（只记录，不真正拦截）", "bool", draft, disabled)}
        ${guardField("cooldown_seconds", "冷却时长（秒）", "number", draft, disabled)}
        ${guardField("auto_block_hours", "自动拉黑（小时，0=永久）", "number", draft, disabled)}
        ${guardField("strike_block_at", "第几次命中升级为拉黑", "number", draft, disabled)}
        ${guardField("strike_permanent_at", "第几次命中升级为永久", "number", draft, disabled)}
        ${guardField("strike_decay_hours", "命中次数重置（小时）", "number", draft, disabled)}
        ${guardField("notice_mode", "被拦时", "select", draft, disabled)}
        ${guardField("notice_text", "固定话术", "textarea", draft, disabled)}
        ${guardField("exempt_admins", "豁免指定用户", "bool", draft, disabled)}
        ${guardField("exempt_ids", "豁免用户 ID（逗号或换行）", "textarea", draft, disabled)}
      </div>`;
    } else {
      const entries = data?.entries || [];
      const rows = entries.length ? entries.map(item => `<tr>
        <td><code>${guardEscape(item.user_id)}</code><small>${guardEscape(item.scope)}</small></td>
        <td>${(item.reasons || []).map(reason => `<span class="guard-tag">${guardEscape(data.reasons?.[reason] || reason)}</span>`).join("")}
          <small>${guardEscape(item.evidence || "")}</small></td>
        <td>${item.kind === "cooldown" ? "冷却" : "拉黑"}<small>到 ${guardTime(item.until)}</small></td>
        <td class="guard-actions">
          <button type="button" class="button small" data-guard-unblock="${guardEscape(item.user_id)}" data-guard-scope="${guardEscape(item.scope)}" ${disabled}>解除</button>
          <button type="button" class="button small" data-guard-extend="${guardEscape(item.user_id)}" data-guard-scope="${guardEscape(item.scope)}" data-guard-hours="24" ${disabled}>+1 天</button>
          <button type="button" class="button small" data-guard-extend="${guardEscape(item.user_id)}" data-guard-scope="${guardEscape(item.scope)}" data-guard-hours="0" ${disabled}>永久</button>
          <button type="button" class="button small" data-guard-false="${guardEscape(item.user_id)}" data-guard-scope="${guardEscape(item.scope)}" ${disabled}>误伤</button>
        </td></tr>`).join("") : `<tr><td colspan="4" class="decision-empty-row">名单还是空的。命中规则后会自动出现在这里，也可以手动加入。</td></tr>`;
      const audits = (data?.audit || []).slice(0, 20).map(item => `<li><span>${guardTime(item.at)}</span>
        <b>${guardEscape(item.action)}</b><code>${guardEscape(item.user_id)}</code>
        <small>${(item.reasons || []).map(reason => data.reasons?.[reason] || reason).join("、")} ${guardEscape(item.evidence || "")}</small></li>`).join("");
      body = `<div class="guard-directory"><div class="guard-heading"><h3>黑名单目录</h3><span>${entries.length} 人</span></div>
        <table class="guard-table"><thead><tr><th>用户</th><th>原因</th><th>期限</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div>
        <div class="guard-settings guard-manual">
          <label>手动拉黑 · 窗口 / 会话<input type="text" data-guard-manual="scope" value="${guardEscape(harassment.manual.scope)}" placeholder="umo 或会话标识"></label>
          <label>用户 ID<input type="text" data-guard-manual="user" value="${guardEscape(harassment.manual.user)}" placeholder="平台账号 ID"></label>
          <label>时长（小时，0=永久）<input type="number" data-guard-manual="hours" value="${guardEscape(harassment.manual.hours)}"></label>
          <button type="button" class="button primary" data-guard-manual-add ${disabled}>加入黑名单</button>
        </div>
        <div class="guard-audit"><div class="guard-heading"><h3>最近记录</h3><span>${audits ? "" : "暂无"}</span></div><ol>${audits}</ol></div>
        <div class="guard-settings guard-transfer">
          <button type="button" class="button small" data-guard-export ${disabled}>导出</button>
          <button type="button" class="button small" data-guard-import ${disabled}>从下方 JSON 导入</button>
        </div>
        <textarea class="guard-json" rows="4" data-guard-json placeholder="导出后在这里编辑或粘贴 JSON，再点导入">${guardEscape(harassment.exported || "")}</textarea>`;
    }
    return `<div class="guard-panel">
      <div class="decision-stats decision-stats-four">
        ${stat("总开关", draft.enabled ? "已开启" : "未开启", draft.dry_run ? "试运行中 · 只记录" : "正式拦截")}
        ${stat("判断", `${guardCount(draft.keywords_strong) + guardCount(draft.keywords_weak)} 词 · ${guardDetectors.filter(item => draft[item.key]).length} 个信号`, "关键词与行为信号并行")}
        ${stat("处置", draft.dry_run ? "只记录" : `${Math.round(draft.cooldown_seconds / 60)} 分钟冷却`, `第 ${draft.strike_block_at} 次升级拉黑`)}
        ${stat("黑名单", `${(data?.entries || []).length} 人`, "在「黑名单管理」里解除")}
      </div>
      <div class="guard-topbar">
        <nav class="section-chips" aria-label="防骚扰分区">${tabs}</nav>
        <label class="guard-master"><input type="checkbox" data-guard-field="enabled" ${draft.enabled ? "checked" : ""} ${disabled}>启用防骚扰</label>
      </div>
      <div class="guard-body">${body}</div>
      <div class="guard-footer"><button type="button" class="button primary" data-guard-save ${disabled}>保存设置</button>
        ${harassment.notice ? `<span class="model-notice${harassment.bad ? " error" : ""}">${guardEscape(harassment.notice)}</span>` : ""}</div>
    </div>`;
  }

  async function guardRequest(method, path, body) {
    const bridge = window.CompanionProduct?.bridgeNow?.() || await window.CompanionProduct?.bridgeFor?.();
    if (!bridge) throw new Error("bridge_unavailable");
    const endpoint = harassment.endpoint + path;
    const response = method === "GET" ? await bridge.apiGet(endpoint) : await bridge.apiPost(endpoint, body);
    /* 失败回执有两层要认。
     *   1. 信封本身就是失败：{"status":"error","message":…}，带 data 或不带都算。
     *   2. 外层是 ok、内层才是失败：{"status":"ok","data":{"status":"error",…}}。
     * 只认第一层的话，第二种会被当成一份快照，页面渲染出一张空表单；
     * 只认 data 的话，第一种里没有 data 的那种同样会被当成空快照。两层都拦。 */
    const wrapped = response?.status === "error" ? response
      : ["ok", "error"].includes(response?.status) && response?.data !== undefined ? response
        : ["ok", "error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") throw Object.assign(new Error(wrapped.message || "request_failed"), {code: wrapped.message});
    if (wrapped?.data?.status === "error") throw Object.assign(new Error(wrapped.data.message || "request_failed"), {code: wrapped.data.message});
    const value = wrapped ? wrapped.data : response;
    if (!value || typeof value !== "object") throw new Error("invalid_response");
    return value;
  }

  function guardAdopt(snapshot) {
    harassment.data = snapshot;
    harassment.draft = {...snapshot.settings};
    harassment.missing = false;
  }

  async function loadHarassment() {
    if (!harassment.endpoint || harassment.loading || harassment.busy) return;
    harassment.loading = true;
    harassment.notice = "";
    harassment.missing = false;
    /* 重试期间要回到「正在读取」，不能继续挂着上一次的失败文案。 */
    harassment.bad = false;
    renderPanels();
    try {
      guardAdopt(await guardRequest("GET", ""));
      harassment.bad = false;
    } catch (error) {
      harassment.bad = true;
      harassment.missing = routeMissing(error);
      harassment.notice = harassment.missing
        ? "该页只对私聊窗口生效，当前插件版本也可能没有提供这组接口。"
        : "读取防骚扰设置失败：" + (error?.message || "未知错误");
    } finally {
      harassment.loading = false;
      renderPanels();
    }
  }

  async function guardMutate(path, body, done) {
    if (harassment.busy) return;
    harassment.busy = true;
    harassment.notice = "正在处理…";
    renderPanels();
    try {
      const snapshot = await guardRequest("POST", path, body);
      guardAdopt(snapshot);
      harassment.notice = done || "已保存。";
      harassment.bad = false;
    } catch (error) {
      harassment.bad = true;
      harassment.notice = "操作失败：" + (error?.message || "未知错误");
    } finally {
      harassment.busy = false;
      renderPanels();
    }
  }

  const saveHarassment = () => guardMutate("/settings", {changes: harassment.draft}, "防骚扰设置已保存。");

  function onGuardInput(event) {
    const field = event.target?.dataset?.guardField;
    const manual = event.target?.dataset?.guardManual;
    if (manual) {
      harassment.manual[manual] = event.target.value;
      return;
    }
    if (event.target?.dataset?.guardJson !== undefined) {
      harassment.exported = event.target.value;
      return;
    }
    if (!field) return;
    harassment.draft ||= {};
    if (event.target.type === "checkbox") harassment.draft[field] = !!event.target.checked;
    else if (event.target.type === "number") harassment.draft[field] = Number(event.target.value);
    else harassment.draft[field] = event.target.value;
  }

  function onGuardClick(event) {
    const tab = event.target?.closest?.("[data-guard-tab]");
    if (tab) { harassment.tab = tab.dataset.guardTab; renderPanels(); return; }
    if (event.target?.closest?.("[data-guard-save]")) { void saveHarassment(); return; }
    if (event.target?.closest?.("[data-guard-retry]")) { void loadHarassment(); return; }
    const unblock = event.target?.closest?.("[data-guard-unblock]");
    if (unblock) {
      void guardMutate("/unblock", {scope: unblock.dataset.guardScope,
        user_id: unblock.dataset.guardUnblock}, "已解除。");
      return;
    }
    const extend = event.target?.closest?.("[data-guard-extend]");
    if (extend) {
      void guardMutate("/extend", {scope: extend.dataset.guardScope,
        user_id: extend.dataset.guardExtend, hours: Number(extend.dataset.guardHours)}, "期限已更新。");
      return;
    }
    const falsePositive = event.target?.closest?.("[data-guard-false]");
    if (falsePositive) {
      void guardMutate("/false-positive", {scope: falsePositive.dataset.guardScope,
        user_id: falsePositive.dataset.guardFalse, note: "页面标记误伤"}, "已按误伤解除。");
      return;
    }
    if (event.target?.closest?.("[data-guard-manual-add]")) {
      const scope = String(harassment.manual.scope || "").trim();
      const user = String(harassment.manual.user || "").trim();
      if (!scope || !user) { harassment.notice = "手动拉黑需要填写窗口和用户 ID。"; harassment.bad = true; renderPanels(); return; }
      void guardMutate("/block", {scope, user_id: user, hours: Number(harassment.manual.hours) || 0,
        note: "页面手动加入"}, "已加入黑名单。");
      return;
    }
    if (event.target?.closest?.("[data-guard-export]")) {
      harassment.busy = true;
      void guardRequest("POST", "/export", {scope: ""}).then(document => {
        harassment.exported = JSON.stringify(document, null, 2);
        harassment.notice = "已导出到下框。";
        harassment.bad = false;
      }).catch(error => {
        harassment.notice = "导出失败：" + (error?.message || "未知错误");
        harassment.bad = true;
      }).finally(() => { harassment.busy = false; renderPanels(); });
      return;
    }
    if (event.target?.closest?.("[data-guard-import]")) {
      let document;
      try { document = JSON.parse(harassment.exported || "{}"); }
      catch { harassment.notice = "下方 JSON 无法解析。"; harassment.bad = true; renderPanels(); return; }
      void guardMutate("/import", {document}, "已导入。");
    }
  }

  function mount(node, options = {}) {
    if (!node) return;
    if (root !== node) {
      unmount();
      root = node;
    }
    scopeKind = typeof options.scopeKind === "string" ? options.scopeKind : "";
    /* 挂载方给的是插件相对路径（页面桥只收这种），这里只管在后面拼子路径。 */
    harassment.endpoint = typeof options.harassmentApi === "string" ? options.harassmentApi : "";
    render(options.groupSettingsMarkup || "");
    if (harassment.endpoint && harassmentApplies() && !harassment.data) void loadHarassment();
  }

  function updateReplyStatus(status) {
    replyStatus = status || null;
    renderPanels();
  }

  /* 两处挂载点各自上报，按挂载点分开存，永不写同一个槽：角色层一次刷新/保存
     就会重报整组 28 个字段，写在同一个槽里必然把本群窗口的覆盖值打回默认。
     来源既可由调用方点名，也能从上报表上认出来（角色层 scope=role、本群
     scope=window）。同一个挂载点重复上报时合并，跨挂载点绝不合并。 */
  function updateParticipationStatus(status, mount) {
    if (!status || typeof status !== "object") return;
    const target = mount === "window" || (mount !== "role" && status.scope === "window")
      ? "window" : "role";
    const settings = status.settings && typeof status.settings === "object" ? status.settings : null;
    const merged = {...reportedStatus(target), settings: {...(reportedStatus(target)?.settings || {}), ...settings}};
    if (target === "window") windowStatus = merged; else roleStatus = merged;
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
    scopeKind = "";
    harassment.endpoint = "";
    harassment.data = null;
    harassment.draft = null;
    harassment.notice = "";
    harassment.missing = false;
    replyStatus = null;
    roleStatus = null;
    windowStatus = null;
  }

  return {mount, unmount, setView, updateReplyStatus, updateParticipationStatus};
})();
