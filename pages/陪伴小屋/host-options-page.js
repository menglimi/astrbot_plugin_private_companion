"use strict";
window.CompanionHostOptionsPage = (() => {
  const errors = {
    bridge_unavailable: "页面连接尚未就绪，请稍后重试。",
    bridge_timeout: "插件暂时没有响应，请稍后重试。",
    dashboard_identity_required: "请登录 AstrBot Dashboard 后再修改插件设置。",
    staging_not_initialized: "先在「账号与会话」完成 V2 初始化，再修改这些设置。",
    host_options_conflict: "设置已在别处更新。请重新读取后再编辑。",
    host_options_invalid: "设置值不符合范围要求，请检查后重试。",
    host_options_save_unavailable: "当前 AstrBot 环境不支持从页面保存插件设置。",
    host_options_save_failed: "设置未能写入，请检查插件配置权限。",
    host_options_unavailable: "插件设置暂时不可读取。",
  };
  const state = {voice: null, poke: null, forwardIntake: null, forwardBounds: {},
    editTokens: {}, applyModes: {}, loading: false,
    error: "", notices: {}, roots: [], generation: 0, saving: ""};
  let listeners = [];
  let onStatus = null;

  const esc = value => String(value ?? "").replace(/[&<>"']/g,
    ch => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[ch]));
  const percent = value => Math.round(Number(value || 0) * 100);
  const unwrap = response => {
    const wrapped = ["ok", "error"].includes(response?.status) && response?.data !== undefined ? response
      : ["ok", "error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") {
      const code = wrapped.data?.code || (/^[a-z][a-z0-9_]{2,60}$/.test(wrapped.message || "")
        ? wrapped.message : "host_options_unavailable");
      const error = new Error(errors[code] || errors.host_options_unavailable);
      error.code = code;
      throw error;
    }
    const value = wrapped ? wrapped.data : response;
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(errors.host_options_unavailable);
    return value;
  };
  async function api(method, endpoint, body) {
    const product = window.CompanionProduct;
    const bridge = product?.bridgeNow?.() || (product?.bridgeFor ? await product.bridgeFor() : window.AstrBotPluginPage);
    if (!bridge?.apiGet || !bridge?.apiPost) {
      const error = new Error(errors.bridge_unavailable);error.code = "bridge_unavailable";throw error;
    }
    let timer;
    try {
      const call = method === "GET" ? bridge.apiGet(endpoint) : bridge.apiPost(endpoint, body);
      const response = await Promise.race([call, new Promise((_, reject) => {
        timer = setTimeout(() => {const error = new Error(errors.bridge_timeout);error.code = "bridge_timeout";reject(error);}, 15000);
      })]);
      return unwrap(response);
    } finally { clearTimeout(timer); }
  }

  function messageMarkup(group) {
    const notice = state.notices[group] || "";
    return `<p class="host-options-message" role="status" data-host-options-message ${notice ? "" : "hidden"}>${esc(notice)}</p>`;
  }

  function renderVoice() {
    const value = state.voice;
    if (state.loading && !value) return '<p class="host-options-empty" role="status">正在读取语音默认值…</p>';
    if (!value) return `<div class="host-options-empty" role="status"><p>${esc(state.error || "语音设置暂时不可用。")}</p>
      <button type="button" class="button small" data-host-reload>重新读取</button></div>`;
    const base = percent(value.probability);
    const override = (field, title, help) => {
      const inherited = Number(value[field]) < 0;
      const own = inherited ? base : percent(value[field]);
      return `<div class="host-options-row" data-override-row="${field}">
        <div class="host-options-copy"><strong>${title}</strong><small>${help}</small></div>
        <div class="host-options-control">
          <label class="host-options-inherit"><input type="checkbox" name="${field}_inherit" data-inherit-field="${field}"
            data-wb-field="${field}" ${inherited ? "checked" : ""}>继承基础概率（${base}%）</label>
          <label class="host-options-percent" data-value-field="${field}" ${inherited ? "hidden" : ""}>
            <input type="number" name="${field}" min="0" max="100" step="1" inputmode="numeric"
              data-wb-field="${field}" value="${own}" ${inherited ? "disabled" : ""}>
            <span>%</span>
          </label>
        </div>
      </div>`;
    };
    return `<section class="host-options-panel" aria-label="语音默认值">
      <header class="host-options-heading"><div><h3>语音回复 · 开关与概率</h3>
        <p>这一页只管“发不发语音、多大概率发”。发音人、音色、语速与试听在「模型与连接 → 语音」，分段与发送位置在「表达 → 语音」。</p></div></header>
      <nav class="host-options-jumps" aria-label="语音的其他配置位置">
        <a class="button small" href="#manage/models">发音人与音色 · 模型与连接</a>
        <a class="button small" href="#manage/models">试听与语种 · 同上（语音配置）</a>
        <a class="button small" href="#manage/expression/text">分段与发送位置 · 表达 / 文本</a>
      </nav>
      ${!value.enabled ? '<p class="host-options-warning" role="note">语音总开关关闭时，对象页启用语音也不会合成。</p>' : ""}
      <form data-host-form="voice" novalidate>
        <div class="host-options-row"><div class="host-options-copy"><strong>允许语音回复</strong>
          <small>全局总开关。对象的「启用语音」也需要打开。</small></div>
          <label class="host-options-switch"><input type="checkbox" name="enabled" data-wb-field="enabled" ${value.enabled ? "checked" : ""}>
            <span>${value.enabled ? "已开启" : "已关闭"}</span></label></div>
        <div class="host-options-row"><div class="host-options-copy"><strong>基础触发概率</strong>
          <small>私聊或群聊没有覆盖值时使用。</small></div>
          <label class="host-options-percent"><input type="number" name="probability" min="0" max="100" step="1"
            inputmode="numeric" data-wb-field="probability" value="${base}"><span>%</span></label></div>
        ${override("private_probability", "私聊默认概率", "仅作为未单独设置的私聊对象默认值。")}
        ${override("group_probability", "群聊默认概率", "仅作为未单独设置的群聊对象默认值。")}
        <footer class="host-options-footer">${messageMarkup("voice")}
          <button type="submit" class="button small primary" ${state.saving === "voice" ? "disabled" : ""}>${state.saving === "voice" ? "正在保存…" : "保存语音默认值"}</button>
        </footer>
      </form>
    </section>`;
  }

  function renderPoke() {
    const value = state.poke;
    if (state.loading && !value) return '<p class="host-options-empty" role="status">正在读取戳一戳回应设置…</p>';
    if (!value) return `<div class="host-options-empty" role="status"><p>${esc(state.error || "戳一戳回应设置暂时不可用。")}</p>
      <button type="button" class="button small" data-host-reload>重新读取</button></div>`;
    const replies = value.replies.map(esc).join("\n");
    return `<section class="host-options-panel" aria-label="收到戳一戳回应">
      <header class="host-options-heading"><div><h3>收到戳一戳</h3>
        <p>仅响应已配置目标发给当前 Bot 的 OneBot 戳一戳。保存后立即生效。</p></div></header>
      <form data-host-form="poke" novalidate>
        <div class="host-options-row"><div class="host-options-copy"><strong>回应收到的戳一戳</strong>
          <small>关闭后仍可单独保留下方短句和概率设置。</small></div>
          <label class="host-options-switch"><input type="checkbox" name="enabled" data-wb-field="enabled" ${value.enabled ? "checked" : ""}>
            <span>${value.enabled ? "已开启" : "已关闭"}</span></label></div>
        <div class="host-options-row"><div class="host-options-copy"><strong>回复概率</strong>
          <small>决定收到戳一戳后是否回复。</small></div>
          <label class="host-options-percent"><input type="number" name="trigger_probability" min="0" max="100" step="1"
            inputmode="numeric" data-wb-field="trigger_probability" value="${percent(value.trigger_probability)}"><span>%</span></label></div>
        <div class="host-options-row"><div class="host-options-copy"><strong>回戳概率</strong>
          <small>平台支持时，决定回复后是否再戳回去。</small></div>
          <label class="host-options-percent"><input type="number" name="back_probability" min="0" max="100" step="1"
            inputmode="numeric" data-wb-field="back_probability" value="${percent(value.back_probability)}"><span>%</span></label></div>
        <div class="host-options-row"><div class="host-options-copy"><strong>连续回戳次数</strong>
          <small>命中回戳后连续戳回对方的次数。</small></div>
          <input class="host-options-number" type="number" name="back_times" min="1" max="5" step="1"
            inputmode="numeric" data-wb-field="back_times" value="${value.back_times}"></div>
        <div class="host-options-row"><div class="host-options-copy"><strong>模型即兴回复</strong>
          <small>开启后由戳一戳专用模型生成短回复，生成失败时使用预设短句。</small></div>
          <label class="host-options-switch"><input type="checkbox" name="model_reply_enabled" data-wb-field="model_reply_enabled" ${value.model_reply_enabled ? "checked" : ""}>
            <span>${value.model_reply_enabled ? "已开启" : "已关闭"}</span></label></div>
        <div class="host-options-row"><div class="host-options-copy"><strong>模型回复概率</strong>
          <small>在已决定回应的戳一戳中，命中后使用模型生成。</small></div>
          <label class="host-options-percent"><input type="number" name="model_reply_probability" min="0" max="100" step="1"
            inputmode="numeric" data-wb-field="model_reply_probability" value="${percent(value.model_reply_probability)}"><span>%</span></label></div>
        <div class="host-options-row"><div class="host-options-copy"><strong>拍一拍上下文接话</strong>
          <small>默认关闭，仅群聊生效：拍一拍发生数秒后，模型查看最近几条群消息，有需要接的话题才回一句，否则保持安静。与上面的拍一拍回应相互独立。</small></div>
          <label class="host-options-switch"><input type="checkbox" name="context_reply_enabled" data-wb-field="context_reply_enabled" ${value.context_reply_enabled ? "checked" : ""}>
            <span>${value.context_reply_enabled ? "已开启" : "已关闭"}</span></label></div>
        <div class="host-options-row"><div class="host-options-copy"><strong>上下文接话延迟秒数</strong>
          <small>拍一拍后等待多少秒再查看群消息。0 = 瞬时快照（立即读取，先拍后说的话来不及进入上下文）；1–30 秒 = 等待后读取。</small></div>
          <input class="host-options-number" type="number" name="context_delay_seconds" min="0" max="30" step="1"
            inputmode="numeric" data-wb-field="context_delay_seconds" value="${value.context_delay_seconds}"></div>
        <label class="host-options-replies"><span><strong>可选回复</strong><small>每行一句，1–20 句；每句最多 120 字。</small></span>
          <textarea name="replies" rows="4" maxlength="2500" data-wb-field="replies">${replies}</textarea></label>
        <footer class="host-options-footer">${messageMarkup("poke")}
          <button type="submit" class="button small primary" ${state.saving === "poke" ? "disabled" : ""}>${state.saving === "poke" ? "正在保存…" : "保存戳一戳设置"}</button>
        </footer>
      </form>
    </section>`;
  }

  function renderForwardIntake() {
    const value = state.forwardIntake;
    if (state.loading && !value) return '<p class="host-options-empty" role="status">正在读取合并转发设置…</p>';
    if (!value) return `<div class="host-options-empty" role="status"><p>${esc(state.error || "合并转发设置暂时不可用。")}</p>
      <button type="button" class="button small" data-host-reload>重新读取</button></div>`;
    const fields = [
      ["max_forwards", "单条消息最大转发数"],
      ["max_nodes", "单次最多展开节点数"],
      ["max_depth", "最大嵌套深度"],
    ];
    return `<section class="host-options-panel" aria-label="合并转发设置">
      <header class="host-options-heading"><div><h3>合并转发理解</h3></div></header>
      <form data-host-form="forward_intake" novalidate>
        ${fields.map(([name, title]) => `<label class="host-options-row">
          <span class="host-options-copy"><strong>${title}</strong><small>1–${state.forwardBounds[name]}</small></span>
          <input class="host-options-number" type="number" name="${name}" min="1" max="${state.forwardBounds[name]}"
            step="1" inputmode="numeric" data-wb-field="${name}" value="${value[name]}">
        </label>`).join("")}
        <div class="host-options-row"><div class="host-options-copy"><strong>展开嵌套合并转发</strong>
          <small>关闭时保留未展开提示，不读取内部节点。</small></div>
          <label class="host-options-switch"><input type="checkbox" name="parse_nested" data-wb-field="parse_nested" ${value.parse_nested ? "checked" : ""}>
            <span>${value.parse_nested ? "已开启" : "已关闭"}</span></label></div>
        <footer class="host-options-footer">${messageMarkup("forward_intake")}
          <button type="submit" class="button small primary" ${state.saving === "forward_intake" ? "disabled" : ""}>
            ${state.saving === "forward_intake" ? "正在保存…" : "保存合并转发设置"}</button>
        </footer>
      </form>
    </section>`;
  }

  function paint() {
    for (const root of state.roots) {
      if (!root) continue;
      if (root.id === "voiceReplyPolicyWorkspace") root.innerHTML = renderVoice();
      if (root.id === "pokePolicyWorkspace") root.innerHTML = renderPoke();
      if (root.id === "forwardIntakeWorkspace") root.innerHTML = renderForwardIntake();
    }
  }

  function adopt(value) {
    state.voice = value.voice_reply || null;
    state.poke = value.poke || null;
    state.forwardIntake = value.forward_intake || null;
    state.forwardBounds = value.forward_bounds || {};
    state.editTokens = value.edit_tokens || {};
    state.applyModes = value.apply_modes || {};
    onStatus?.(state);
  }

  async function read() {
    const ticket = ++state.generation;
    state.loading = true;state.error = "";paint();
    try {
      const value = await api("GET", "host-options");
      if (ticket !== state.generation) return;
      adopt(value);
    } catch (error) {
      if (ticket !== state.generation) return;
      const code = error.code || error.message;
      state.error = errors[code] || (error.code ? errors.host_options_unavailable : error.message)
        || errors.host_options_unavailable;
      state.voice = null;state.poke = null;state.forwardIntake = null;
    } finally {
      if (ticket === state.generation) {state.loading = false;paint();}
    }
  }

  const formElement = (form, name) => form.querySelector(`[name="${name}"]`);
  function fraction(form, name, inheritName = "") {
    if (inheritName && formElement(form, inheritName)?.checked) return -1;
    const raw = formElement(form, name)?.value ?? "";
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0 || value > 100 || raw.trim() === "")
      throw new Error("概率请填写 0 到 100 之间的整数。");
    if (!Number.isInteger(value)) throw new Error("概率请填写整数百分比。");
    return value / 100;
  }

  async function save(group, form) {
    if (!state.editTokens[group] || state.saving) return;
    let changes;
    try {
      if (group === "voice") {
        changes = {enabled: !!formElement(form, "enabled")?.checked,
          probability: fraction(form, "probability"),
          private_probability: fraction(form, "private_probability", "private_probability_inherit"),
          group_probability: fraction(form, "group_probability", "group_probability_inherit")};
      } else if (group === "poke") {
        const replies = String(formElement(form, "replies")?.value || "").split(/\r?\n/).map(item => item.trim()).filter(Boolean);
        if (replies.length < 1 || replies.length > 20 || replies.some(item => item.length > 120))
          throw new Error("请填写 1 到 20 句回复，每句不超过 120 字。");
        const backTimesRaw = String(formElement(form, "back_times")?.value ?? "");
        const backTimes = Number(backTimesRaw);
        if (!Number.isInteger(backTimes) || backTimes < 1 || backTimes > 5)
          throw new Error("连续回戳次数请输入 1 到 5 之间的整数。");
        const delayRaw = String(formElement(form, "context_delay_seconds")?.value ?? "");
        const delaySeconds = Number(delayRaw);
        if (!Number.isInteger(delaySeconds) || delaySeconds < 0 || delaySeconds > 30)
          throw new Error("上下文接话延迟请输入 0 到 30 之间的整数。");
        changes = {enabled: !!formElement(form, "enabled")?.checked,
          trigger_probability: fraction(form, "trigger_probability"),
          back_probability: fraction(form, "back_probability"), back_times: backTimes,
          model_reply_enabled: !!formElement(form, "model_reply_enabled")?.checked,
          model_reply_probability: fraction(form, "model_reply_probability"), replies,
          context_reply_enabled: !!formElement(form, "context_reply_enabled")?.checked,
          context_delay_seconds: delaySeconds};
      } else {
        changes = {};
        const labels = {max_forwards: "每条消息中的转发数", max_nodes: "节点数", max_depth: "嵌套深度"};
        for (const name of ["max_forwards", "max_nodes", "max_depth"]) {
          const raw = String(formElement(form, name)?.value ?? "");
          const value = Number(raw);
          if (!Number.isInteger(value) || value < 1 || value > state.forwardBounds[name])
            throw new Error(`${labels[name]}请输入 1 到 ${state.forwardBounds[name]} 之间的整数。`);
          changes[name] = value;
        }
        changes.parse_nested = !!formElement(form, "parse_nested")?.checked;
      }
    } catch (error) {
      const message = form.querySelector("[data-host-options-message]");
      if (message) {message.textContent = error.message;message.hidden = false;}
      return;
    }

    state.saving = group;
    const button = form.querySelector('button[type="submit"]');
    const message = form.querySelector("[data-host-options-message]");
    if (button) {button.disabled = true;button.textContent = "正在保存…";}
    if (message) {message.textContent = "";message.hidden = true;}
    let saved = false;
    try {
      const value = await api("POST", `host-options/${group}`, {edit_token: state.editTokens[group], changes});
      adopt(value);
      state.notices[group] = group === "voice" ? "已保存。插件重启后应用到新回复。"
        : group === "poke" ? "已保存，收到戳一戳的回应设置已生效。" : "已保存";
      saved = true;
    } catch (error) {
      const code = error.code || error.message;
      const text = errors[code] || error.message || errors.host_options_save_failed;
      if (message) {message.textContent = text + (code === "host_options_conflict" ? " 请先重新读取。" : "");message.hidden = false;}
    } finally {
      state.saving = "";
      if (saved) paint();
      else if (button) {button.disabled = false;button.textContent = group === "voice" ? "保存语音默认值"
        : group === "poke" ? "保存戳一戳设置" : "保存合并转发设置";}
    }
  }

  function onChange(event) {
    const input = event.target?.closest?.("[data-inherit-field]");
    if (!input) return;
    const field = input.dataset.inheritField;
    const root = input.closest("[data-host-options-root]");
    const wrapper = root?.querySelector(`[data-value-field="${field}"]`);
    const number = wrapper?.querySelector("input");
    if (wrapper) wrapper.hidden = input.checked;
    if (number) number.disabled = input.checked;
  }

  function onSubmit(event) {
    const form = event.target?.closest?.("form[data-host-form]");
    if (!form) return;
    event.preventDefault();
    void save(form.dataset.hostForm, form);
  }

  function onClick(event) {
    if (event.target?.closest?.("[data-host-reload]")) {
      event.preventDefault();void read();
    }
  }

  function unmount() {
    state.generation += 1;
    for (const [root, type, handler] of listeners) root.removeEventListener(type, handler);
    listeners = [];state.roots = [];
    onStatus = null;
  }

  function mount({voiceRoot = null, pokeRoot = null, forwardRoot = null, onStatus: statusCallback = null} = {}) {
    unmount();
    onStatus = typeof statusCallback === "function" ? statusCallback : null;
    state.roots = [voiceRoot, pokeRoot, forwardRoot].filter(Boolean);
    for (const root of state.roots) {
      root.dataset.hostOptionsRoot = "";
      for (const [type, handler] of [["change", onChange], ["submit", onSubmit], ["click", onClick]]) {
        root.addEventListener(type, handler);listeners.push([root, type, handler]);
      }
    }
    void read();
  }

  function searchEntries() {
    const voice = [
      ["enabled", "允许语音回复", "全局总开关", ["TTS", "语音总开关"]],
      ["probability", "基础触发概率", "语音默认值", ["TTS", "默认概率"]],
      ["private_probability", "私聊默认概率", "语音默认值", ["TTS", "私聊覆盖"]],
      ["group_probability", "群聊默认概率", "语音默认值", ["TTS", "群聊覆盖"]],
    ].map(([field, label, sectionTitle, aliases]) => ({group: "voice_reply", groupTitle: "语音默认值",
      field, label, help: "", section: "defaults", sectionTitle, sectionCount: 1, aliases}));
    const poke = [
      ["enabled", "回应收到的戳一戳", "收到戳一戳", ["戳一戳", "戳戳"]],
      ["trigger_probability", "回复概率", "收到戳一戳", ["回应概率", "戳一戳"]],
      ["back_probability", "回戳概率", "收到戳一戳", ["戳一戳"]],
      ["back_times", "连续回戳次数", "收到戳一戳", ["连戳", "回戳次数"]],
      ["model_reply_enabled", "模型即兴回复", "收到戳一戳", ["模型", "即兴回复"]],
      ["model_reply_probability", "模型回复概率", "收到戳一戳", ["模型", "即兴回复"]],
      ["replies", "可选回复", "收到戳一戳", ["戳戳文案", "预设短句"]],
    ].map(([field, label, sectionTitle, aliases]) => ({group: "poke", groupTitle: "平台动作",
      field, label, help: "", section: "received", sectionTitle, sectionCount: 1, aliases}));
    const forward = [
      ["max_forwards", "每条消息中的转发数", "每次摄取的数量上限", ["合并转发", "上限"]],
      ["max_nodes", "单次最多展开节点数", "转发节点上限", ["合并转发", "节点", "300"]],
      ["max_depth", "嵌套深度", "转发嵌套层数", ["合并转发", "深度"]],
      ["parse_nested", "展开嵌套合并转发", "关闭时保留未展开提示", ["嵌套", "开关"]],
    ].map(([field, label, sectionTitle, aliases]) => ({group: "forward_intake", groupTitle: "合并转发理解",
      field, label, help: "", section: "intake", sectionTitle, sectionCount: 1, aliases}));
    return [...voice, ...poke, ...forward];
  }

  return {mount, unmount, searchEntries, get state() {return state;}};
})();
