"use strict";
(() => {
  if (window.parent !== window) document.documentElement.classList.add("is-embedded");
  const $ = id => document.getElementById(id);
  const STAGES = ["positioning", "personality", "voice", "background", "review"];
  const LABELS = {optimize: "拟人优化", create: "创建人格"};
  const STAGE_META = {
    positioning: {
      num: "01",
      title: "定位与关系",
      createLead: "她可以从一个很小的念头开始",
      createDesc: "先定下她是谁、和你是什么关系（比如青梅、合租搭档或安静的同事），想到什么就说什么。",
      createPlaceholder: "例如：想做一个嘴硬心软的图书馆学姐，平时会提醒我别熬夜…（也可直接点上方卡片开聊）",
      optimizePlaceholder: "例如：觉得她回复太像客服，希望语气更松弛自然一点…（也可直接点上方痛点胶囊）",
    },
    personality: {
      num: "02",
      title: "性格与反差",
      createLead: "聊聊她的性格底色和可爱反差",
      createDesc: "说说她平时给人的第一印象、藏起来的小缺点，或者熟悉之后才会露出的另一面。",
      createPlaceholder: "例如：表面冷静克制，但对方真的低落时会笨拙地关心人…",
      optimizePlaceholder: "说说她在性格表达或情绪反应上哪里容易崩（OOC）…",
    },
    voice: {
      num: "03",
      title: "说话方式",
      createLead: "定下她平时聊天说话的味道",
      createDesc: "决定她偏短句还是爱吐槽、喜不喜欢用语气词，以及绝对不要出现的机械话术。",
      createPlaceholder: "例如：像微信日常聊天一样多用短句，不写括号动作旁白，少用反问…",
      optimizePlaceholder: "说说哪些口吻或句式读起来像机器，希望改成怎样的聊天节奏…",
    },
    background: {
      num: "04",
      title: "背景与偏好",
      createLead: "补一点日常习惯与喜欢的事",
      createDesc: "加上她的生活作息、偏爱的小事或一两段过往经历，聊起来会更有活人感。",
      createPlaceholder: "例如：喜欢雨天听琴声和黑咖啡，讨厌被敷衍应付…",
      optimizePlaceholder: "说说哪些背景细节想保留、精简，或挪到「单独保留的背景」里…",
    },
    review: {
      num: "05",
      title: "审核与试演",
      createLead: "检查设定并试聊几句看看像不像她",
      createDesc: "确认右侧已记录的设定无误后，点「整理成稿」或「情景试演」感受实际说话效果。",
      createPlaceholder: "说说最后还想微调的地方，或直接点击右侧「整理成稿」…",
      optimizePlaceholder: "说说还需要收口微调的地方，或点右侧「对照原文」「情景试演」检查效果…",
    },
  };
  const LOCAL_PREFIX = "companion.persona-designer.v1.";
  const OPTIMIZE_QUICK_CHIPS = [
    "说话太像 AI 客服",
    "回复太啰嗦",
    "人设容易崩",
    "口吻再自然点",
  ];
  const CREATE_STARTER_CARDS = [
    {text: "我有一个大致的角色想法", hint: "直接描述身份、关系或第一印象"},
    {text: "还没想好，先帮我找方向", hint: "先给几个具体灵感选项让我挑"},
    {text: "从我熟悉的性格出发", hint: "先定反差感，再慢慢补全细节"},
  ];
  const STALE_STAGE_INPUT_RE = /^我们先聊“([^”]+)”这一部分。$/;
  const errors = {
    dashboard_identity_required: "请从已登录的 AstrBot 管理界面打开此页。",
    core_access_required: "当前账号没有管理本安装的权限。",
    persona_designer_invalid: "设计内容过长或格式不完整，请检查后重试。",
    persona_designer_unavailable: "新的她暂时不可用，当前编辑内容仍保留，可以稍后重试。",
    persona_designer_model_rejected: "模型没有接受这次设计请求；可以换一个文本模型，或缩短人设/对话后重试。",
    persona_designer_model_missing: "所选文本模型尚未加载，请重新选择模型。",
    persona_designer_source_required: "先输入人设，或从“切换人设”载入一份。",
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
  let bridge, root = fresh(), options = {models: [], personas: [], persona_summaries: {}}, loaded = false, disposed = false;
  let working = false, nextBefore = null, pollTimer, loadingDrafts = false, polling = false;
  let pendingPersonaId = "", entryMode = "", pickerFilter = "", autoPreloading = false;
  let sourceExpandedOverride = null;
  const session = () => root.sessions[root.activeMode];
  const doc = () => session().document;
  const generating = () => Object.values(root.sessions).some(s => s.pending);
  const errorText = error => errors[error?.code] || errors[error?.message]
    || "这次操作没有完成，当前编辑内容仍保留，请稍后重试。";

  function sanitizeDocument(raw, mode) {
    const base = newDocument(mode);
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    base.stage = STAGES.includes(raw.stage) ? raw.stage : "positioning";
    for (const [key, max] of [["provider_id", 200], ["source_persona_id", 200], ["source_text", 30000],
      ["confirmed", 16000], ["draft", 65536], ["lorebook", 20000], ["input", 8000]]) {
      base[key] = typeof raw[key] === "string" ? raw[key].slice(0, max) : "";
    }
    if (Array.isArray(raw.messages)) {
      base.messages = raw.messages.filter(m => m && typeof m === "object"
        && (m.role === "user" || m.role === "assistant") && typeof m.content === "string")
        .slice(-100).map(m => ({role: m.role, content: m.content.slice(0, 16000)}));
    }
    if (Array.isArray(raw.options)) {
      base.options = raw.options.filter(v => typeof v === "string" && v.trim()).slice(0, 3).map(v => v.slice(0, 300));
    }
    if (!base.messages.length && STALE_STAGE_INPUT_RE.test(base.input.trim())) {
      base.input = "";
    }
    return base;
  }
  function hasDocumentContent(d) {
    if (!d) return false;
    return d.messages.length > 0
      || ["source_text", "confirmed", "draft", "lorebook", "input"].some(key => String(d[key] || "").trim());
  }
  function readDirectLocal(mode) {
    try {
      const raw = localStorage.getItem(LOCAL_PREFIX + mode);
      if (typeof raw !== "string" || !raw) return null;
      return sanitizeDocument(JSON.parse(raw), mode);
    } catch { return null; }
  }
  const MAX_LOCAL_DRAFT_BYTES = 500000;
  function writeDirectLocal(mode, document) {
    try {
      if (document === null) { localStorage.removeItem(LOCAL_PREFIX + mode); return; }
      const encoded = JSON.stringify(document);
      if (encoded.length > MAX_LOCAL_DRAFT_BYTES) return;
      localStorage.setItem(LOCAL_PREFIX + mode, encoded);
    } catch {}
  }
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
  function syncLocalDraft(mode, documentOrNull) {
    writeDirectLocal(mode, documentOrNull);
    if (!disposed && bridge) {
      api("POST", "designer/local-draft", {mode, document: documentOrNull}).catch(() => {});
    }
  }
  function persist(syncMode = null) {
    if (disposed || !bridge) return Promise.resolve();
    const modes = syncMode && syncMode !== root.activeMode ? [root.activeMode, syncMode] : [root.activeMode];
    for (const mode of modes) {
      const currentDoc = root.sessions[mode]?.document;
      if (currentDoc) syncLocalDraft(mode, hasDocumentContent(currentDoc) ? currentDoc : null);
    }
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
    ["source-text", "confirmed", "draft-text", "lorebook", "choose-persona", "choose-model", "quick-reset"].forEach(id => {
      if ($(id)) $(id).disabled = ownBusy || working;
    });
    $("choose-persona").disabled ||= anyBusy || !!root.pendingSave;
    if ($("quick-reset")) $("quick-reset").disabled ||= anyBusy || !!root.pendingSave;
    ["generate-draft", "tryout"].forEach(id => $(id).disabled = !loaded || anyBusy || working || !modelAvailable);
    $("tryout").disabled ||= !d.draft.trim();
    $("generate-draft").textContent = d.draft.trim() ? "重新整理成稿" : "整理成稿";
    const hasContent = hasDocumentContent(d);
    $("save-draft").disabled = !loaded || !!root.pendingSave || anyBusy || working || !hasContent;
    $("retry-save").hidden = !root.retrySave;
    $("retry-save").disabled = !!root.pendingSave || anyBusy || working;
    $("compare").disabled = root.activeMode !== "optimize" || !d.source_text;
    $("copy-draft").disabled = !d.draft;
    $("copy-lorebook").disabled = !d.lorebook;
    if ($("send-to-workshop")) $("send-to-workshop").disabled = !(d.draft.trim() || d.source_text.trim());
    $("source-count").textContent = Array.from(d.source_text).length.toLocaleString("zh-CN") + " 字";
    $("draft-count").textContent = Array.from(d.draft).length.toLocaleString("zh-CN") + " 字";
    document.querySelectorAll("[data-stage]").forEach(el => el.disabled = ownBusy);
    document.querySelectorAll("#suggestions button").forEach(el => el.disabled = anyBusy || working || !modelAvailable);
  }
  function isSourceExpanded(d) {
    if (sourceExpandedOverride !== null) return sourceExpandedOverride;
    if (!loaded || autoPreloading) return false;
    return !String(d.source_text || "").trim();
  }
  function paintSourcePanel(d) {
    const hasSource = Boolean(String(d.source_text || "").trim());
    const expanded = isSourceExpanded(d);
    const dot = $("source-status-dot");
    if (dot) dot.dataset.state = hasSource ? "ready" : "empty";
    const labelEl = $("source-label");
    if (labelEl) {
      if (hasSource && d.source_persona_id) {
        labelEl.textContent = "已载入起点人设：" + d.source_persona_id;
        labelEl.title = "起点来自：" + d.source_persona_id + " · 点右侧「展开查看/编辑」可随时微调";
      } else if (hasSource) {
        labelEl.textContent = "已准备起点人设文本";
        labelEl.title = "点右侧「展开查看/编辑」可查看或修改起点文本";
      } else if (!loaded || autoPreloading) {
        labelEl.textContent = "正在读取当前起点人设…";
        labelEl.title = "正在读取当前人设，请稍候";
      } else {
        labelEl.textContent = "尚未载入起点人设";
        labelEl.title = "请选择人设或直接在下方粘贴起点文本";
      }
    }
    const body = $("source-editor-body");
    if (body) body.hidden = !expanded;
    const toggle = $("toggle-source-editor");
    if (toggle) {
      toggle.setAttribute("aria-expanded", String(expanded));
      toggle.textContent = expanded ? "收起文本" : "展开查看/编辑";
    }
  }
  function render() {
    const d = doc();
    const stageMeta = STAGE_META[d.stage] || STAGE_META.positioning;
    document.querySelectorAll("[data-mode]").forEach(el => el.setAttribute("aria-pressed", String(el.dataset.mode === root.activeMode)));
    document.querySelectorAll("[data-stage]").forEach(el => {
      if (el.dataset.stage === d.stage) el.setAttribute("aria-current", "step");
      else el.removeAttribute("aria-current");
    });
    $("source-panel").hidden = d.mode !== "optimize";
    if (d.mode === "optimize") paintSourcePanel(d);
    for (const [id, key] of [["source-text", "source_text"], ["chat-input", "input"], ["confirmed", "confirmed"], ["draft-text", "draft"], ["lorebook", "lorebook"]]) {
      if ($(id).value !== d[key]) $(id).value = d[key];
    }
    $("chat-input").placeholder = d.mode === "optimize" ? stageMeta.optimizePlaceholder : stageMeta.createPlaceholder;
    const model = options.models.find(m => m.id === d.provider_id);
    const modelLabel = model ? model.name : d.provider_id ? "模型已不可用 · 重新选择" : "选择模型";
    const modelNameEl = $("choose-model-name");
    if (modelNameEl) modelNameEl.textContent = modelLabel;
    else $("choose-model").textContent = model ? "模型 · " + model.name : modelLabel;
    $("choose-model").title = model ? `当前模型：${model.name}（${model.id}）` : (d.provider_id || "选择用于人设设计的文本模型");
    const log = $("messages");
    log.replaceChildren();
    log.classList.toggle("has-chat", d.messages.length > 0);
    if (!d.messages.length) {
      const empty = node("div", undefined, "empty-guide");
      const hasSource = Boolean(String(d.source_text || "").trim());
      if (d.mode === "optimize") {
        empty.append(
          node("span", "拟人优化 · 痛点直达", "empty-stage-pill"),
          node("strong", hasSource ? "已就绪起点人设，直接点下方痛点或说说哪里不像她" : "先载入或粘贴人设起点，再告诉我哪里读起来像机器"),
          node("p", hasSource ? "模型与起点文本都已自动备好；挑一个最贴近的痛点胶囊，或在下方随便说一句就能开始逐段微调。" : "可点上方「切换人设」读取 AstrBot 现有人设，或直接粘贴文本后开始微调。"),
        );
        const steps = node("ol", undefined, "empty-steps");
        for (const [num, text] of [["1", "点选常见痛点"], ["2", "逐段微调口吻"], ["3", "对照原文复制落地"]]) {
          const li = node("li");
          li.append(node("span", num, "step-num"), node("span", text));
          steps.append(li);
        }
        empty.append(steps);
      } else {
        empty.append(
          node("span", `当前阶段 · ${stageMeta.num} ${stageMeta.title}`, "empty-stage-pill"),
          node("strong", stageMeta.createLead),
          node("p", stageMeta.createDesc),
        );
        const steps = node("ol", undefined, "empty-steps");
        for (const [num, text] of [["1", "随便聊一句灵感"], ["2", "右侧实时记下设定"], ["3", "一键整理成人设稿"]]) {
          const li = node("li");
          li.append(node("span", num, "step-num"), node("span", text));
          steps.append(li);
        }
        empty.append(steps);
      }
      log.append(empty);
    }
    for (const message of d.messages) {
      const article = node("article", undefined, "chat-message " + message.role);
      article.append(node("span", message.role === "user" ? "你" : "设计助手", "speaker"), node("p", message.content));
      log.append(article);
    }
    log.scrollTop = log.scrollHeight;
    const suggestions = $("suggestions");
    const suggestionsWrap = $("suggestions-wrap");
    const suggestionsLabel = $("suggestions-label");
    suggestions.replaceChildren();
    const isCreateStarter = !d.options.length && d.mode === "create" && !d.messages.length;
    const isOptimizeStarter = !d.options.length && d.mode === "optimize" && !d.messages.length;
    suggestions.classList.toggle("is-starter-cards", isCreateStarter);
    if (isCreateStarter) {
      if (suggestionsLabel) suggestionsLabel.textContent = "不知道怎么开头？点一张卡片立刻开聊";
      for (const item of CREATE_STARTER_CARDS) {
        const button = node("button", undefined, "button small starter-card");
        button.type = "button";
        button.append(node("strong", item.text), node("small", item.hint));
        button.addEventListener("click", () => sendTurn("chat", item.text));
        suggestions.append(button);
      }
      if (suggestionsWrap) suggestionsWrap.hidden = false;
    } else if (isOptimizeStarter) {
      if (suggestionsLabel) suggestionsLabel.textContent = "常见痛点，点一下直接开始优化";
      for (const choice of OPTIMIZE_QUICK_CHIPS) {
        const button = node("button", choice, "button small chip-pain");
        button.type = "button";
        button.addEventListener("click", () => sendTurn("chat", choice));
        suggestions.append(button);
      }
      if (suggestionsWrap) suggestionsWrap.hidden = false;
    } else if (d.options.length) {
      if (suggestionsLabel) suggestionsLabel.textContent = "也可以直接选一个方向继续";
      for (const choice of d.options) {
        const button = node("button", choice, "button small");
        button.type = "button";
        button.addEventListener("click", () => sendTurn("chat", choice));
        suggestions.append(button);
      }
      if (suggestionsWrap) suggestionsWrap.hidden = false;
    } else {
      if (suggestionsLabel) suggestionsLabel.textContent = "";
      if (suggestionsWrap) suggestionsWrap.hidden = true;
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
  function toneForName(name) {
    let hash = 0;
    for (const ch of String(name || "")) hash = (hash * 31 + ch.codePointAt(0)) % 4;
    return String(hash);
  }
  function paintPersonaCards(selectedId) {
    const grid = $("persona-card-grid");
    if (!grid) return;
    const items = options.personas || [];
    const summaries = options.persona_summaries || {};
    const currentVal = selectedId !== undefined ? selectedId : $("persona-select").value;
    const query = pickerFilter.trim().toLowerCase();
    const filtered = items.filter(item => {
      if (!query) return true;
      const summary = summaries[item.id];
      const hay = ((item.name || item.id) + " " + (summary?.preview || "")).toLowerCase();
      return hay.includes(query);
    });
    const countEl = $("persona-match-count");
    if (countEl) countEl.textContent = items.length ? (query ? `${filtered.length}/${items.length} 份` : `共 ${items.length} 份`) : "";
    const activeEl = document.activeElement;
    const hadGridFocus = Boolean(activeEl && grid.contains(activeEl));
    const prevFocusedId = hadGridFocus ? (activeEl.dataset?.personaCard || "") : "";
    grid.replaceChildren();
    if (!filtered.length) {
      grid.append(node("p", items.length ? "没有匹配该关键词的人设，可清空搜索重试。" : "暂无可选 AstrBot 人设。", "persona-pick-empty"));
      return;
    }
    const selectedIndex = filtered.findIndex(item => item.id === currentVal);
    const tabIndexTarget = selectedIndex >= 0 ? selectedIndex : 0;
    const renderedCards = [];
    filtered.forEach((item, index) => {
      const label = item.name || item.id;
      const summary = summaries[item.id];
      const selected = item.id === currentVal;
      const active = Boolean(item.default || summary?.active);
      const card = node("button", undefined, "persona-pick-card" + (selected ? " is-selected" : ""));
      card.type = "button";
      card.setAttribute("role", "option");
      card.setAttribute("aria-selected", String(selected));
      card.tabIndex = index === tabIndexTarget ? 0 : -1;
      card.dataset.personaCard = item.id;

      const avatar = node("span", Array.from(label)[0] || "人", "persona-pick-avatar");
      avatar.dataset.tone = toneForName(label);
      avatar.setAttribute("aria-hidden", "true");

      const body = node("span", undefined, "persona-pick-body");
      const head = node("span", undefined, "persona-pick-head");
      head.append(node("span", label, "persona-pick-name"));
      if (active) head.append(node("span", "使用中", "persona-pick-active"));
      if (summary && Number.isFinite(summary.chars)) head.append(node("span", summary.chars + " 字", "persona-pick-chars"));
      body.append(head, node("span", summary?.preview || "点击选中并载入这份人设", "persona-pick-preview"));

      card.append(avatar, body);
      card.addEventListener("click", () => {
        $("persona-select").value = item.id;
        paintPersonaCards(item.id);
      });
      card.addEventListener("dblclick", () => {
        $("persona-select").value = item.id;
        $("persona-form").requestSubmit();
      });
      grid.append(card);
      renderedCards.push(card);
    });
    if (hadGridFocus && renderedCards.length) {
      const targetId = currentVal || prevFocusedId;
      const target = renderedCards.find(c => c.dataset.personaCard === targetId) || renderedCards[tabIndexTarget] || renderedCards[0];
      if (target) {
        renderedCards.forEach(c => { c.tabIndex = c === target ? 0 : -1; });
        target.focus({preventScroll: true});
      }
    }
  }
  function ensureDefaultModelSelected() {
    if (!options.models.length) return false;
    const fallbackId = options.models[0].id;
    let changed = false;
    for (const mode of ["optimize", "create"]) {
      const targetDoc = root.sessions[mode]?.document;
      if (!targetDoc) continue;
      if (!targetDoc.provider_id || !options.models.some(m => m.id === targetDoc.provider_id)) {
        targetDoc.provider_id = fallbackId;
        changed = true;
      }
    }
    return changed;
  }
  async function maybeAutoPreloadOptimizePersona() {
    if (autoPreloading || disposed || working || generating()) return;
    const optSession = root.sessions.optimize;
    const optDoc = optSession?.document;
    if (root.activeMode !== "optimize" || !optDoc || optSession.manuallyCleared) return;
    if (optDoc.source_text.trim() || optDoc.source_persona_id || optDoc.messages.length) return;
    const targetId = options.default_persona_id || options.personas[0]?.id || "";
    if (!targetId) return;
    autoPreloading = true;
    try {
      const result = await api("GET", "designer/persona", {id: targetId});
      if (disposed || root.sessions.optimize !== optSession || optSession.manuallyCleared) return;
      if (optDoc.source_text.trim() || optDoc.source_persona_id || optDoc.messages.length) return;
      if (result.id === targetId && typeof result.text === "string" && result.text.trim()) {
        optDoc.source_persona_id = targetId;
        optDoc.source_text = result.text;
        sourceExpandedOverride = null;
        await persist();
        render();
      }
    } catch {} finally {
      autoPreloading = false;
      if (!disposed) render();
    }
  }
  let optionsBusy = false, optionsSeq = 0;
  const formatClock = (date = new Date()) => `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}:${String(date.getSeconds()).padStart(2, "0")}`;
  function setOptionRefreshState(active) {
    for (const id of ["refresh-options", "refresh-personas"]) {
      const btn = $(id);
      if (!btn) continue;
      btn.disabled = active;
      btn.classList?.toggle?.("is-refreshing", active);
      btn.setAttribute?.("aria-busy", active ? "true" : "false");
      btn.textContent = active ? "读取中…" : "重新读取";
    }
  }
  async function loadOptions(manual = false) {
    if (optionsBusy) return;
    const seq = ++optionsSeq;
    optionsBusy = true;
    setOptionRefreshState(true);
    if (manual) notice("");
    try {
      const next = await api("GET", "designer/options", {});
      if (disposed || seq !== optionsSeq) return;
      if (!Array.isArray(next.models) || !Array.isArray(next.personas)) throw new Error("persona_designer_response_invalid");
      options = Object.assign({persona_summaries: {}}, next);
      loaded = true;
      const modelAutoPicked = ensureDefaultModelSelected();
      const selectedPersona = doc().source_persona_id || options.default_persona_id || options.personas[0]?.id || "";
      fillSelect("model-select", options.models, doc().provider_id, "暂无文本模型");
      fillSelect("persona-select", options.personas, selectedPersona, "暂无人设");
      paintPersonaCards(selectedPersona);
      $("model-empty").hidden = !!options.models.length;
      $("persona-empty").hidden = !!options.personas.length;
      $("confirm-model").disabled = !options.models.length;
      $("load-persona").disabled = !options.personas.length;
      if (modelAutoPicked) await persist();
      if (options.persona_issue) notice(options.persona_issue);
      else if (manual) notice(`刚刚已刷新 ${formatClock()}`);
      void maybeAutoPreloadOptimizePersona();
      render();
    } catch (error) {
      if (!disposed && seq === optionsSeq) notice(errorText(error), true);
    } finally {
      if (seq === optionsSeq) {
        optionsBusy = false;
        setOptionRefreshState(false);
      }
    }
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
    if (d.mode === "optimize" && !d.source_text.trim()) {
      sourceExpandedOverride = true;
      render();
      notice(errors.persona_designer_source_required, true);
      $("source-text").focus();
      return;
    }
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
        await persist(mode);
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
  const DRAFT_AUTOLOAD_KEY = "companion.autoload.v1";
  const DRAFT_MIN_INTERVAL_MS = 260;
  const DRAFT_MAX_AUTO_BURST = 12;
  const DRAFT_TOP_THRESHOLD = 16;
  function readDraftAutoPref() {
    try {
      const raw = localStorage.getItem(DRAFT_AUTOLOAD_KEY);
      if (raw === "0" || raw === "false") return false;
      if (raw === "1" || raw === "true") return true;
    } catch {}
    return true;
  }
  let draftAutoEnabled = readDraftAutoPref();
  let draftCount = 0, draftAppendError = "", draftAutoBurst = 0, draftBurstPaused = false, draftStalled = false;
  let draftLastTriggerAt = 0, draftIntersecting = false, draftDebounceTimer = null, draftObserver = null;
  let draftProgrammaticScroll = false, draftProgrammaticTimer = null;
  let draftReqSeq = 0;

  function isDraftsDialogOpen() {
    return !disposed && Boolean($("drafts-dialog")?.open);
  }
  function writeDraftAutoPref(enabled) {
    draftAutoEnabled = Boolean(enabled);
    try { localStorage.setItem(DRAFT_AUTOLOAD_KEY, draftAutoEnabled ? "1" : "0"); } catch {}
  }
  function clearDraftAutoTimer() {
    if (draftDebounceTimer !== null) { clearTimeout(draftDebounceTimer); draftDebounceTimer = null; }
  }
  function disconnectDraftObserver() {
    try { draftObserver?.disconnect?.(); } catch {}
    draftObserver = null;
    draftIntersecting = false;
    clearDraftAutoTimer();
  }
  function canDraftAutoLoad() {
    return isDraftsDialogOpen() && draftAutoEnabled && typeof IntersectionObserver === "function"
      && Boolean(nextBefore) && !loadingDrafts && !draftAppendError && !draftStalled
      && !draftBurstPaused && draftAutoBurst < DRAFT_MAX_AUTO_BURST;
  }
  function scheduleDraftAutoLoad() {
    if (!canDraftAutoLoad()) return;
    const elapsed = draftLastTriggerAt > 0 ? Date.now() - draftLastTriggerAt : DRAFT_MIN_INTERVAL_MS;
    if (elapsed >= DRAFT_MIN_INTERVAL_MS) {
      clearDraftAutoTimer();
      void listDrafts(nextBefore, {auto: true});
      return;
    }
    if (draftDebounceTimer !== null) return;
    draftDebounceTimer = setTimeout(() => {
      draftDebounceTimer = null;
      if (draftIntersecting && canDraftAutoLoad()) void listDrafts(nextBefore, {auto: true});
    }, Math.max(0, DRAFT_MIN_INTERVAL_MS - elapsed));
  }
  function renderDraftScrollBar() {
    const list = $("draft-list");
    if (!list) return;
    list.querySelector("[data-draft-sentinel]")?.remove();
    list.querySelector("[data-draft-scroll-bar]")?.remove();
    $("more-drafts").hidden = true;
    if (!isDraftsDialogOpen() || (!draftCount && !nextBefore && !loadingDrafts && !draftAppendError)) {
      disconnectDraftObserver();
      return;
    }
    const ioSupported = typeof IntersectionObserver === "function";
    const showSentinel = Boolean(nextBefore) && draftAutoEnabled && ioSupported && !draftAppendError && !draftStalled && !draftBurstPaused;
    let sentinel = null;
    if (showSentinel) {
      sentinel = node("div", undefined, "draft-scroll-sentinel");
      sentinel.dataset.draftSentinel = "";
      sentinel.setAttribute("aria-hidden", "true");
      list.append(sentinel);
    }
    const bar = node("div", undefined, "draft-scroll-bar");
    bar.dataset.draftScrollBar = "";
    const status = node("span", undefined, "draft-scroll-status");
    status.setAttribute("role", "status");
    const actions = node("div", undefined, "draft-scroll-actions");
    if (loadingDrafts) {
      bar.dataset.state = "loading";
      const dots = node("span", undefined, "draft-scroll-dots");
      dots.setAttribute("aria-hidden", "true");
      dots.append(node("i"), node("i"), node("i"));
      status.append(dots, node("span", "正在加载更早的版本…"));
    } else if (draftAppendError) {
      bar.dataset.state = "error";
      status.textContent = draftAppendError;
      const retry = node("button", "点击重试", "button small");
      retry.type = "button";
      retry.addEventListener("click", () => { draftAppendError = ""; draftStalled = false; draftBurstPaused = false; draftAutoBurst = 0; void listDrafts(nextBefore); });
      actions.append(retry);
    } else if (draftStalled) {
      bar.dataset.state = "warn";
      status.textContent = "暂未读取到更早的版本，已暂停自动加载";
      const retry = node("button", "点击重试", "button small");
      retry.type = "button";
      retry.addEventListener("click", () => { draftStalled = false; draftBurstPaused = false; draftAutoBurst = 0; void listDrafts(nextBefore); });
      actions.append(retry);
    } else if (draftBurstPaused) {
      bar.dataset.state = "warn";
      status.textContent = `已连续自动加载 ${draftAutoBurst} 次，已暂停`;
      const cont = node("button", "点击继续加载", "button small");
      cont.type = "button";
      cont.addEventListener("click", () => { draftBurstPaused = false; draftAutoBurst = 0; void listDrafts(nextBefore); });
      actions.append(cont);
    } else if (!nextBefore) {
      bar.dataset.state = "complete";
      status.textContent = `已加载全部 ${draftCount} 个版本`;
      if (draftCount >= DRAFT_TOP_THRESHOLD) {
        const topBtn = node("button", "↑ 回到顶部", "button small");
        topBtn.type = "button";
        topBtn.addEventListener("click", () => {
          const reduced = document.documentElement?.dataset?.motion === "off"
            || document.documentElement?.dataset?.motion === "low"
            || Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
          draftProgrammaticScroll = true;
          clearTimeout(draftProgrammaticTimer);
          draftProgrammaticTimer = setTimeout(() => { draftProgrammaticScroll = false; }, 450);
          if (typeof list.scrollTo === "function") list.scrollTo({top: 0, behavior: reduced ? "auto" : "smooth"});
          else list.scrollTop = 0;
        });
        actions.append(topBtn);
      }
    } else {
      bar.dataset.state = !ioSupported ? "fallback" : draftAutoEnabled ? "idle" : "manual";
      status.textContent = !ioSupported ? "当前环境不支持自动滚动" : draftAutoEnabled ? "下滑自动加载更早版本" : "自动滚动加载已关闭";
      const moreBtn = node("button", "更早的版本", "button small");
      moreBtn.type = "button";
      moreBtn.addEventListener("click", () => { draftBurstPaused = false; draftAutoBurst = 0; void listDrafts(nextBefore); });
      actions.append(moreBtn);
    }
    if (ioSupported) {
      const toggleLabel = node("label", undefined, "draft-scroll-toggle");
      const toggle = document.createElement("input");
      toggle.type = "checkbox";
      toggle.checked = draftAutoEnabled;
      toggle.addEventListener("change", () => {
        writeDraftAutoPref(toggle.checked);
        if (draftAutoEnabled) { draftAutoBurst = 0; draftBurstPaused = false; }
        else clearDraftAutoTimer();
        renderDraftScrollBar();
      });
      toggleLabel.append(toggle, node("span", "自动滚动加载"));
      actions.append(toggleLabel);
    }
    bar.append(status, actions);
    list.append(bar);
    disconnectDraftObserver();
    if (sentinel && showSentinel) {
      draftObserver = new IntersectionObserver(entries => {
        if (!isDraftsDialogOpen()) return;
        const hit = entries.some(entry => Boolean(entry && entry.isIntersecting));
        draftIntersecting = hit;
        if (!hit) { clearDraftAutoTimer(); return; }
        if (canDraftAutoLoad()) scheduleDraftAutoLoad();
      }, {root: list, rootMargin: "160px 0px"});
      draftObserver.observe(sentinel);
    }
  }
  function handleDraftListScroll() {
    if (disposed || draftProgrammaticScroll) return;
    if (draftAutoBurst === 0 && !draftBurstPaused) return;
    const wasPaused = draftBurstPaused;
    draftAutoBurst = 0;
    draftBurstPaused = false;
    if (wasPaused) renderDraftScrollBar();
  }
  async function listDrafts(before = 0, options = {}) {
    if (!isDraftsDialogOpen()) return;
    if (before && loadingDrafts) return;
    const isAuto = options && options.auto === true;
    if (isAuto && !canDraftAutoLoad()) return;
    const reqSeq = ++draftReqSeq;
    loadingDrafts = true;
    draftLastTriggerAt = Date.now();
    if (!before) {
      draftCount = 0;
      draftAppendError = "";
      draftAutoBurst = 0;
      draftBurstPaused = false;
      draftStalled = false;
      nextBefore = 0;
      disconnectDraftObserver();
      $("draft-list").replaceChildren(node("p", "正在读取…", "muted"));
    } else {
      draftAppendError = "";
      if (isAuto) draftAutoBurst += 1;
      else { draftAutoBurst = 0; draftBurstPaused = false; draftStalled = false; }
      renderDraftScrollBar();
    }
    $("more-drafts").disabled = true;
    const prevBefore = before;
    const prevCount = draftCount;
    try {
      const result = await api("GET", "designer/drafts", before ? {before} : {});
      if (reqSeq !== draftReqSeq || !isDraftsDialogOpen()) return;
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
            sourceExpandedOverride = null;
            await persist();
            if (entryMode && entryMode !== d.mode) {
              entryMode = d.mode;
              parent.postMessage({channel: "companion-workspace", kind: "designer-mode", mode: d.mode}, "*");
            }
            $("drafts-dialog").close(); notice("已读取“" + saved.title + "”。"); render();
          } catch (error) { notice(errorText(error), true); } finally { working = false; button.disabled = false; controls(); }
        });
        row.append(description, button); $("draft-list").append(row);
        draftCount += 1;
      }
      nextBefore = result.next_before || 0;
      if (before && nextBefore && nextBefore === prevBefore && draftCount === prevCount) {
        draftStalled = true;
      }
      if (isAuto && !draftStalled && nextBefore && draftAutoBurst >= DRAFT_MAX_AUTO_BURST) {
        draftBurstPaused = true;
      }
    } catch (error) {
      if (reqSeq !== draftReqSeq || !isDraftsDialogOpen()) return;
      if (!before) {
        $("draft-list").replaceChildren(node("p", errorText(error), "muted"));
      } else {
        draftAppendError = errorText(error);
      }
    }
    finally {
      if (reqSeq === draftReqSeq) {
        loadingDrafts = false;
        $("more-drafts").disabled = false;
        if (isDraftsDialogOpen()) renderDraftScrollBar();
        else disconnectDraftObserver();
      }
    }
  }
  async function copyText(id) {
    const input = $(id);
    const done = id === "lorebook" ? "已复制背景，可以到 AstrBot 的世界知识中粘贴。"
      : "已复制正文，去 AstrBot「人格设置」粘贴即可；这里不会直接改线上人格。";
    try { await navigator.clipboard.writeText(input.value); notice(done); }
    catch {
      input.focus(); input.select();
      try { if (!document.execCommand("copy")) throw new Error(); notice(done); }
      catch { notice("已选中文本，请使用系统的复制操作。"); }
    }
  }
  function selectMode(mode) {
    const provider = doc().provider_id; root.activeMode = mode;
    if (!doc().provider_id) doc().provider_id = provider;
    sourceExpandedOverride = null;
  }
  document.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => {
    selectMode(button.dataset.mode);
    persist(); notice(session().error || "", !!session().error);
    void maybeAutoPreloadOptimizePersona();
    render();
  }));
  document.querySelectorAll("[data-stage]").forEach(button => button.addEventListener("click", () => {
    doc().stage = button.dataset.stage;
    persist(); render(); $("chat-input").focus();
  }));
  if ($("toggle-source-editor")) {
    $("toggle-source-editor").addEventListener("click", () => {
      const nextExpanded = !isSourceExpanded(doc());
      sourceExpandedOverride = nextExpanded;
      paintSourcePanel(doc());
      if (nextExpanded) $("source-text").focus();
    });
  }
  for (const [id, key] of [["source-text", "source_text"], ["chat-input", "input"], ["confirmed", "confirmed"], ["draft-text", "draft"], ["lorebook", "lorebook"]]) {
    $(id).addEventListener("input", () => {
      doc()[key] = $(id).value;
      if (key === "source_text") {
        sourceExpandedOverride = true;
        paintSourcePanel(doc());
      }
      persist(); controls();
    });
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
    const selected = doc().source_persona_id || options.default_persona_id || options.personas[0]?.id || "";
    pickerFilter = "";
    if ($("persona-search")) $("persona-search").value = "";
    fillSelect("persona-select", options.personas, selected, "暂无人设");
    paintPersonaCards(selected);
    $("persona-dialog").showModal();
  });
  if ($("persona-search")) {
    $("persona-search").addEventListener("input", () => {
      pickerFilter = $("persona-search").value;
      paintPersonaCards();
    });
  }
  if ($("persona-card-grid")) {
    $("persona-card-grid").addEventListener("keydown", event => {
      const cards = Array.from($("persona-card-grid").querySelectorAll(".persona-pick-card:not(:disabled)"));
      if (!cards.length) return;
      const current = event.target.closest(".persona-pick-card");
      const index = cards.indexOf(current);
      if (index < 0) return;
      let next = -1;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % cards.length;
      else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + cards.length) % cards.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = cards.length - 1;
      if (next < 0) return;
      event.preventDefault();
      cards.forEach((card, idx) => { card.tabIndex = idx === next ? 0 : -1; });
      cards[next].focus();
    });
  }
  $("persona-select").addEventListener("change", () => paintPersonaCards($("persona-select").value));
  async function loadPersona(id) {
    const current = session(), mode = root.activeMode;
    if (!id || working || generating() || root.pendingSave) return;
    working = true; $("load-persona").disabled = true; controls();
    try {
      const result = await api("GET", "designer/persona", {id});
      if (result.id !== id || typeof result.text !== "string") throw new Error("persona_designer_response_invalid");
      if (current.document.source_persona_id !== id) {
        const document = newDocument(mode);
        document.provider_id = current.document.provider_id;
        document.source_persona_id = id; document.source_text = result.text;
        root.sessions[mode] = {document};
      } else {
        current.document.source_text = result.text;
        delete current.manuallyCleared;
      }
      sourceExpandedOverride = null;
      await persist(); $("persona-dialog").close(); $("switch-persona-dialog").close();
      notice("已载入人设，可以直接点下方快捷痛点或输入想调整的地方。"); render();
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
  $("refresh-options").addEventListener("click", () => loadOptions(true)); $("refresh-personas").addEventListener("click", () => loadOptions(true));
  ["save-draft"].forEach(id => $(id).addEventListener("click", openSave));
  $("retry-save").addEventListener("click", () => saveSnapshot(root.retrySave));
  $("save-form").addEventListener("submit", event => {
    event.preventDefault(); const title = $("draft-title").value.trim(); if (!title) return;
    const snapshot = {id: uid(), title, state: copy(doc())}; $("save-dialog").close(); saveSnapshot(snapshot);
  });
  $("open-drafts").addEventListener("click", () => {
    draftAutoEnabled = readDraftAutoPref();
    $("drafts-dialog").showModal();
    listDrafts();
  });
  $("drafts-dialog").addEventListener("close", () => {
    draftReqSeq += 1;
    loadingDrafts = false;
    $("more-drafts").disabled = false;
    disconnectDraftObserver();
    clearTimeout(draftProgrammaticTimer);
  });
  window.addEventListener("storage", event => {
    if (disposed || !event || event.key !== DRAFT_AUTOLOAD_KEY) return;
    const raw = event.newValue;
    const next = (raw === "0" || raw === "false") ? false
      : (raw === "1" || raw === "true") ? true
      : readDraftAutoPref();
    if (next === draftAutoEnabled) return;
    draftAutoEnabled = next;
    if (draftAutoEnabled) { draftAutoBurst = 0; draftBurstPaused = false; }
    else clearDraftAutoTimer();
    if (isDraftsDialogOpen()) renderDraftScrollBar();
  });
  $("draft-list").addEventListener("scroll", handleDraftListScroll, {passive: true});
  $("draft-list").addEventListener("wheel", handleDraftListScroll, {passive: true});
  $("draft-list").addEventListener("touchmove", handleDraftListScroll, {passive: true});
  window.addEventListener("pagehide", () => {
    draftReqSeq += 1;
    loadingDrafts = false;
    disconnectDraftObserver();
    clearTimeout(draftProgrammaticTimer);
  });
  $("more-drafts").addEventListener("click", () => listDrafts(nextBefore));
  $("new-design").addEventListener("click", () => { $("drafts-dialog").close(); $("reset-dialog").showModal(); });
  if ($("quick-reset")) $("quick-reset").addEventListener("click", () => $("reset-dialog").showModal());
  $("confirm-reset").addEventListener("click", () => {
    if (generating() || root.pendingSave) { notice("请先等待本轮操作完成，再重新开始。", true); $("reset-dialog").close(); return; }
    const mode = root.activeMode;
    const provider = doc().provider_id;
    root.sessions[mode] = {document: newDocument(mode), manuallyCleared: true};
    doc().provider_id = provider;
    sourceExpandedOverride = null;
    syncLocalDraft(mode, null);
    persist(); $("reset-dialog").close(); notice("已清空当前模式进度，可重新开始。"); render();
  });
  $("copy-draft").addEventListener("click", () => copyText("draft-text")); $("copy-lorebook").addEventListener("click", () => copyText("lorebook"));
  if ($("send-to-workshop")) {
    $("send-to-workshop").addEventListener("click", () => {
      const d = doc();
      const text = (d.draft || "").trim() || (d.source_text || "").trim();
      if (!text) return;
      if (window.parent && window.parent !== window) {
        window.parent.postMessage({
          channel: "companion-workspace",
          kind: "send-to-workshop",
          text,
          personaId: d.source_persona_id || "",
        }, "*");
        notice("正在送往「规范人格」逐段核对…");
      } else {
        copyText(d.draft.trim() ? "draft-text" : "source-text");
      }
    });
  }
  $("compare").addEventListener("click", () => {
    $("compare-source").textContent = doc().source_text; $("compare-draft").textContent = doc().draft; $("compare-dialog").showModal();
  });
  async function restoreLocalModes() {
    for (const mode of ["optimize", "create"]) {
      const target = root.sessions[mode];
      if (!target || hasDocumentContent(target.document)) continue;
      let restored = readDirectLocal(mode);
      if (!restored) {
        try {
          const remote = await api("GET", "designer/local-draft", {mode});
          restored = sanitizeDocument(remote?.document, mode);
        } catch {}
      }
      if (restored && hasDocumentContent(restored)) {
        target.document = restored;
      }
    }
  }
  async function initialize() {
    try {
      bridge = window.AstrBotPluginPage;
      const context = await bridge.ready();
      entryMode = ["create", "optimize"].includes(context?.tab) ? context.tab : "";
      const cached = await api("GET", "designer/client-state", {});
      root = cached.state?.version === 1 ? cached.state : fresh();
      for (const mode of ["optimize", "create"]) {
        const currentDoc = root.sessions?.[mode]?.document;
        if (currentDoc && !currentDoc.messages?.length && STALE_STAGE_INPUT_RE.test(String(currentDoc.input || "").trim())) {
          currentDoc.input = "";
        }
      }
      await restoreLocalModes();
      if (entryMode) selectMode(entryMode);
      document.documentElement.classList.toggle("has-entry-mode", !!entryMode);
      document.querySelector(".mode-switch").hidden = !!entryMode;
      await persist();
      render(); await loadOptions();
      if (session().error) notice(session().error, true);
      await poll();
    } catch (error) { notice(errorText(error), true); }
  }
  window.addEventListener("pagehide", () => { disposed = true; optionsSeq++; optionsBusy = false; clearTimeout(pollTimer); root = fresh(); render(); });
  window.addEventListener("pageshow", event => { if (event.persisted) { disposed = false; initialize(); } });
  render(); initialize();
})();
