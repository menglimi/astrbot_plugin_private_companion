"use strict";
window.CompanionRoleProfileDraft = (() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
  const icon = name => '<i data-lucide="' + name + '" aria-hidden="true"></i>';
  const state = {target: "role_profile", draft: null, source: null, knowledge: [], fieldValues: {}, selectedFields: null, limits: null, totalLimit: 30000, current: null, scopeKey: "", pendingApply: null, personaId: "", personas: [], providerId: "", providers: [], defaultProviderId: "", lastProviderId: "", supplements: {role_profile: "", relationship_profile: ""}, busy: false, busyKind: "", ticket: 0};
  const errorText = {
    dashboard_identity_required: "请从已登录的 AstrBot 管理界面打开陪伴小屋。",
    core_access_required: "当前账号没有管理权限。",
    staging_not_initialized: "角色数据尚未就绪，请在「账号与会话」保存角色映射；已保存可重新读取状态。",
    role_profile_source_unavailable: "当前没有可读取的 AstrBot 人格，请先选择一个已配置人格。",
    host_persona_unavailable: "所选 AstrBot 人格已不可用，请重新读取。",
    role_profile_source_too_large: "当前人格文本较长，请先整理底稿后再生成。",
    role_profile_model_unavailable: "当前模型暂时不能整理档案，请检查模型与连接设置。",
    role_profile_source_changed: "角色或人格刚刚发生变化，请重新读取后再整理。",
    role_profile_context_unavailable: "当前角色档案或 V2 已导入的世界知识暂时无法读取；为避免漏掉设定，请稍后重试。",
    relationship_profile_scope_invalid: "相处资料只在已初始化的私聊对象中可用，请先选择一位用户。",
    relationship_profile_configuration_invalid: "关系资料总字数或单项长度超出可保存范围，请删减后再试。",
    role_profile_draft_timeout: "模型整理超时；已保留你填写的补充设定，可以稍后重试。",
    role_profile_draft_invalid: "模型没有返回可用的档案草稿，请稍后重试。",
    role_profile_draft_failed: "整理没有完成，请检查当前模型后重试。",
    configuration_revision_conflict: "角色档案刚刚更新过；草稿还在，请重新读取后核对。",
    role_profile_configuration_invalid: "档案总字数或单项长度超出可保存范围，请删减后再试。",
  };

  function markup() {
    return '<dialog class="role-profile-draft-dialog" id="roleProfileDraftDialog" aria-labelledby="roleProfileDraftTitle">' +
      '<div class="role-profile-draft-shell"><header class="role-profile-draft-head">' +
      '<div><span data-role-profile-draft-target>PERSONA → ROLE CANON</span><h2 id="roleProfileDraftTitle">整理角色资料</h2></div>' +
      '<button class="icon-button" type="button" data-role-profile-draft-close aria-label="关闭" title="关闭">' + icon("x") + '</button></header>' +
      '<section class="role-profile-draft-compose" aria-label="补充角色设定">' +
      '<div class="role-profile-draft-source-picks"><label class="role-profile-draft-persona">AstrBot 人格<select data-role-profile-draft-persona aria-label="选择整理来源人格"></select></label>' +
      '<label class="role-profile-draft-persona">整理模型<select data-role-profile-draft-provider aria-label="选择整理使用的文本模型"><option value="">跟随当前聊天模型</option></select></label></div>' +
      '<label for="roleProfileDraftSupplement" data-role-profile-draft-supplement-label>补充已确认的角色设定</label>' +
      '<textarea id="roleProfileDraftSupplement" rows="3" maxlength="4000" placeholder="例如：她其实是人造人，生日是第一次醒来的那天；不擅长直接表达担心。"></textarea>' +
      '<div class="role-profile-draft-compose-foot"><p>人格原文提供角色底稿，角色档案与世界知识补足稳定设定；缺少依据的字段会留空。</p>' +
      '<div class="role-profile-draft-compose-actions"><button class="button small" type="button" data-role-profile-draft-knowledge-open>' +
      icon("book-open-text") + '<span>管理世界知识</span></button>' +
      '<button class="button small primary" type="button" data-role-profile-draft-generate>' + icon("sparkles") + '<span>生成可编辑草稿</span></button></div></div></section>' +
      '<p class="role-profile-draft-notice" data-role-profile-draft-notice role="status" aria-live="polite">会参考当前人格、已保存角色档案和符合范围的世界资料；草稿只供检查，不会自动保存。</p>' +
      '<div class="role-profile-draft-recovery" data-role-profile-draft-recovery hidden><span>上次保存结果尚未确认；核对会沿用同一操作标识。</span>' +
      '<button class="button small" type="button" data-role-profile-draft-recover>核对原保存结果</button></div>' +
      '<section class="role-profile-draft-output" data-role-profile-draft-output hidden aria-label="角色资料草稿">' +
      '<div class="role-profile-draft-source" data-role-profile-draft-source></div>' +
      '<div class="role-profile-draft-knowledge" data-role-profile-draft-knowledge></div>' +
      '<div class="role-profile-draft-notes" data-role-profile-draft-notes hidden></div>' +
      '<div class="role-profile-draft-tools"><div><strong data-role-profile-draft-count>—</strong><span>项有内容</span></div>' +
      '<div><button type="button" class="button small" data-role-profile-draft-select-empty>选中空白项</button>' +
      '<button type="button" class="button small" data-role-profile-draft-clear>清空选择</button></div></div>' +
      '<div class="role-profile-draft-groups" data-role-profile-draft-groups></div></section>' +
      '<footer class="role-profile-draft-actions" data-role-profile-draft-actions hidden><span data-role-profile-draft-budget></span>' +
      '<div><button class="button small" type="button" data-role-profile-draft-close>稍后再看</button>' +
      '<button class="button small" type="button" data-role-profile-draft-apply="empty" disabled>填入所选空白字段</button>' +
      '<button class="button small primary" type="button" data-role-profile-draft-apply="replace" disabled>覆盖所选字段</button></div></footer></div></dialog>';
  }

  function currentScope() { return window.CompanionProduct?.scopeKey?.() || ""; }
  function notice(text, bad = false) {
    const target = $("[data-role-profile-draft-notice]");
    if (!target) return;
    target.textContent = text;
    target.dataset.state = bad ? "error" : "ready";
  }
  function apiError(error) {
    return errorText[error?.code] || errorText[error?.message] || "本次操作没有完成，请核对当前状态后重试。";
  }
  function operationKey() {
    return window.crypto?.randomUUID?.() || "role-profile-draft-" + Date.now() + "-" + Math.random().toString(36).slice(2);
  }
  /* Sandboxed Dashboard plugin pages have an opaque origin, so Web Storage can
     throw SecurityError. Keep the pending request in page memory first, then
     try session/local storage, and use history.state as the reload fallback.
     Persistence failures must not block the actual POST. */
  const pendingApplyMemory = new Map();
  const PENDING_APPLY_HISTORY_KEY = "__companion_role_profile_apply_pending_v1";
  const PENDING_APPLY_HISTORY_PREVIOUS = "__companion_role_profile_apply_previous_state_v1";
  function pendingApplyKey(target, scope, endpoint) {
    const identity = window.CompanionProduct?.identity?.() || {};
    return "companion-role-profile-apply.v1:" + JSON.stringify([
      identity.installation_id || "", identity.operator_id || "", scope, target, endpoint,
    ]);
  }
  function writeHistoryPendingApply(key, value) {
    try {
      const current = window.history.state;
      const base = current && typeof current === "object" && !Array.isArray(current)
        ? {...current} : current == null ? {} : {[PENDING_APPLY_HISTORY_PREVIOUS]: current};
      const pending = base[PENDING_APPLY_HISTORY_KEY] && typeof base[PENDING_APPLY_HISTORY_KEY] === "object"
        ? {...base[PENDING_APPLY_HISTORY_KEY]} : {};
      if (value) pending[key] = JSON.stringify(value);
      else delete pending[key];
      if (Object.keys(pending).length) base[PENDING_APPLY_HISTORY_KEY] = pending;
      else delete base[PENDING_APPLY_HISTORY_KEY];
      const next = !Object.keys(base).length ? null
        : Object.keys(base).length === 1 && PENDING_APPLY_HISTORY_PREVIOUS in base
          ? base[PENDING_APPLY_HISTORY_PREVIOUS] : base;
      window.history.replaceState(next, "");
      return true;
    } catch { return false; }
  }
  function readHistoryPendingApply(key) {
    try {
      const stored = window.history.state?.[PENDING_APPLY_HISTORY_KEY]?.[key];
      return typeof stored === "string" ? JSON.parse(stored) : null;
    } catch { return null; }
  }
  function storePendingApply(value = state.pendingApply) {
    if (!value) return false;
    const key = pendingApplyKey(value.target, value.scopeKey, value.endpoint);
    let text;
    try { text = JSON.stringify(value); } catch { return false; }
    pendingApplyMemory.set(key, text);
    for (const storage of [() => sessionStorage, () => localStorage]) {
      try { storage().setItem(key, text); return true; } catch {}
    }
    return writeHistoryPendingApply(key, value) || pendingApplyMemory.has(key);
  }
  function restorePendingApply(target, scope, endpoint) {
    if (!endpoint) return null;
    const key = pendingApplyKey(target, scope, endpoint);
    for (const read of [
      () => pendingApplyMemory.get(key),
      () => sessionStorage.getItem(key),
      () => localStorage.getItem(key),
      () => readHistoryPendingApply(key),
    ]) try {
      const stored = read();
      const value = typeof stored === "string" ? JSON.parse(stored) : stored;
      const body = value?.body;
      if (value?.target === target && value?.scopeKey === scope && value.endpoint === endpoint
          && body?.section === target && typeof body.operation_key === "string"
          && body.operation_key.length > 0 && body.operation_key.length <= 128
          && typeof body.edit_token === "string" && body.edit_token.length === 64
          && body.changes && typeof body.changes === "object" && !Array.isArray(body.changes)
          && Object.keys(body.changes).length) return value;
    } catch {}
    return null;
  }
  function clearPendingApply(value = state.pendingApply) {
    if (value) {
      const key = pendingApplyKey(value.target, value.scopeKey, value.endpoint);
      pendingApplyMemory.delete(key);
      for (const storage of [() => sessionStorage, () => localStorage]) {
        try { storage().removeItem(key); } catch {}
      }
      writeHistoryPendingApply(key, null);
    }
    state.pendingApply = null;
    renderPendingApply();
  }
  function renderPendingApply() {
    const dialog = $("#roleProfileDraftDialog");
    if (!dialog) return;
    const pending = state.pendingApply;
    const panel = $("[data-role-profile-draft-recovery]", dialog);
    const button = $("[data-role-profile-draft-recover]", dialog);
    if (panel) panel.hidden = !pending;
    if (button) button.disabled = state.busy || !pending;
    for (const control of dialog.querySelectorAll(
      "[data-role-profile-draft-select], [data-role-profile-draft-value], #roleProfileDraftSupplement, [data-role-profile-draft-persona], [data-role-profile-draft-provider]")) {
      control.disabled = !!pending || state.busy;
    }
    updateMeter();
  }
  async function personaRequest(role, target, supplement, personaId, providerId) {
    const product = window.CompanionProduct;
    const bridge = product?.bridgeNow?.() || (product?.bridgeFor ? await product.bridgeFor() : window.AstrBotPluginPage);
    if (!bridge?.apiPost || !role?.persona_api) throw new Error("当前角色没有人格工作区。");
    const route = product.relative(role.persona_api) + "/profile-draft";
    let timer;
    try {
      const response = await Promise.race([
        bridge.apiPost(route, {target, supplement, persona_id: personaId, provider_id: providerId || ""}),
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(Object.assign(new Error("请求等待过久"), {code: "role_profile_draft_timeout"})), 240000);
        }),
      ]);
      return product.unwrap(response);
    } finally { clearTimeout(timer); }
  }
  async function loadPersonaChoices(role) {
    const select = $("[data-role-profile-draft-persona]");
    if (!select || !role?.persona_api) return;
    select.disabled = true;
    try {
      const product = window.CompanionProduct;
      const bridge = product?.bridgeNow?.() || (product?.bridgeFor ? await product.bridgeFor() : window.AstrBotPluginPage);
      const value = product.unwrap(await bridge.apiGet(product.relative(role.persona_api), {limit: 1}));
      state.personas = Array.isArray(value.personas) ? value.personas : [];
      state.providers = Array.isArray(value.providers) ? value.providers : [];
      state.defaultProviderId = String(value.default_provider_id || "");
      const providerSelect = $("[data-role-profile-draft-provider]");
      if (providerSelect) {
        providerSelect.replaceChildren();
        const follow = document.createElement("option");
        follow.value = "";
        follow.textContent = state.defaultProviderId ? `跟随当前聊天模型 · ${state.defaultProviderId}` : "跟随当前聊天模型";
        providerSelect.append(follow);
        for (const provider of state.providers) {
          const option = document.createElement("option");
          option.value = provider.id;
          option.textContent = provider.name && provider.name !== provider.id
            ? `${provider.name} · ${provider.id}` : provider.id;
          option.selected = provider.id === state.providerId;
          providerSelect.append(option);
        }
        if (state.providerId && !state.providers.some(item => item.id === state.providerId)) state.providerId = "";
        providerSelect.value = state.providerId;
        providerSelect.disabled = !!state.pendingApply || state.busy;
      }
      const currentId = value.persona?.persona_id || "";
      if (!state.personaId || !state.personas.some(item => item.value === state.personaId)) state.personaId = currentId;
      select.replaceChildren();
      for (const persona of state.personas) {
        const option = document.createElement("option");
        option.value = persona.value;
        option.textContent = persona.label || persona.value;
        option.selected = persona.value === state.personaId;
        select.append(option);
      }
      if (!state.personas.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "没有可用人格";
        select.append(option);
      }
      select.disabled = !!state.pendingApply || state.busy || state.personas.length < 2;
      select.value = state.personaId;
    } catch (error) {
      notice(apiError(error), true);
    } finally {
      select.disabled = !!state.pendingApply || state.busy || state.personas.length < 2;
    }
  }
  async function readCurrent(role, target = state.target) {
    const product = window.CompanionProduct;
    if (!role?.config_api) throw new Error("当前对象没有角色档案配置入口。");
    const value = await product.request("GET", product.relative(role.config_api), {sections: target});
    const section = value?.sections?.[target];
    if (!section || typeof section.settings !== "object" || typeof section.edit_token !== "string") {
      throw new Error("没有读到当前角色档案。");
    }
    return section;
  }
  function configEndpoint(role, product = window.CompanionProduct) {
    if (!role?.config_api || typeof product?.relative !== "function") return "";
    try { return product.relative(role.config_api) + "/configure"; } catch { return ""; }
  }
  function profileSpec() { return window.CompanionConfigEditors?.spec?.[state.target] || {}; }
  function setTargetPresentation(target) {
    state.target = target;
    const relationship = target === "relationship_profile";
    const head = $(`[data-role-profile-draft-target]`);
    const title = $("#roleProfileDraftTitle");
    const label = $(`[data-role-profile-draft-supplement-label]`);
    const supplement = $("#roleProfileDraftSupplement");
    if (head) head.textContent = relationship ? "PERSONA → RELATIONSHIP" : "PERSONA → ROLE CANON";
    if (title) title.textContent = relationship ? "整理当前关系资料" : "整理角色资料";
    if (label) label.textContent = relationship ? "补充当前用户的已确认资料" : "补充已确认的角色设定";
    if (supplement) supplement.placeholder = relationship
      ? "例如：她平时希望被称为小舟；她是角色的搭档，彼此会直接提醒休息。"
      : "例如：她其实是人造人，生日是第一次醒来的那天；不擅长直接表达担心。";
    const knowledgeButton = $(`[data-role-profile-draft-knowledge-open]`);
    if (knowledgeButton) knowledgeButton.hidden = relationship;
  }
  function draftValue(field) {
    return Object.prototype.hasOwnProperty.call(state.fieldValues, field)
      ? state.fieldValues[field] : state.draft?.fields?.[field] || "";
  }
  function hasDraftContent(field, value) {
    const text = String(value ?? "");
    return !!text.trim();
  }
  function oldValue(field) { return state.current?.settings?.[field] || ""; }
  function selectedFields() {
    return [...document.querySelectorAll("[data-role-profile-draft-select]:checked")]
      .map(input => input.value)
      .filter(field => hasDraftContent(field, $(`[data-role-profile-draft-value="${field}"]`)?.value));
  }
  function updateMeter() {
    const selected = selectedFields();
    const budget = $("[data-role-profile-draft-budget]");
    const actions = $("[data-role-profile-draft-actions]");
    let fieldOver = false;
    const values = {...(state.current?.settings || {})};
    for (const field of selected) {
      const textarea = $("[data-role-profile-draft-value='" + field + "']");
      values[field] = textarea?.value || "";
      const maximum = Number(state.limits?.[field] || 0);
      if (maximum && String(values[field]).length > maximum) fieldOver = true;
    }
    const total = Object.values(values).reduce((sum, value) => sum + String(value || "").length, 0);
    if (budget) {
      budget.textContent = selected.length + " 项已选 · 档案 " + total.toLocaleString("zh-CN") + " / " +
        Number(state.totalLimit || 30000).toLocaleString("zh-CN") + " 字";
      budget.dataset.over = total > Number(state.totalLimit || 30000) ? "true" : "false";
      budget.dataset.fieldOver = fieldOver ? "true" : "false";
    }
    if (actions) for (const button of actions.querySelectorAll("[data-role-profile-draft-apply]")) {
      button.disabled = state.busy || !!state.pendingApply || !state.current || !selected.length || fieldOver || total > Number(state.totalLimit || 30000);
    }
  }
  function paintDraft() {
    const root = $("#roleProfileDraftDialog");
    if (!root || !state.draft) return;
    const output = $("[data-role-profile-draft-output]", root);
    const actions = $("[data-role-profile-draft-actions]", root);
    const spec = profileSpec();
    const source = state.source || {};
    const inferred = new Set(state.draft.inferred_fields || []);
    output.hidden = false;
    actions.hidden = false;
    $("[data-role-profile-draft-source]", root).innerHTML =
      '<div class="role-profile-draft-source-mark">' + icon("file-text") + '</div><div>' +
      '<strong>' + escapeHTML(source.name || source.persona_id || "当前 AstrBot 人格") + '</strong>' +
      '<small>' + Number(source.source_chars || 0).toLocaleString("zh-CN") + " 字 · " + escapeHTML(source.persona_id || "未命名人格") +
      (state.lastProviderId ? " · 模型 " + escapeHTML(state.lastProviderId) : "") + '</small>' +
      '<p>' + escapeHTML(source.source_preview || "") + "</p></div>";
    const knowledge = Array.isArray(state.knowledge) ? state.knowledge : [];
    const knowledgeHost = $("[data-role-profile-draft-knowledge]", root);
    const relationship = state.target === "relationship_profile";
    knowledgeHost.hidden = relationship;
    if (relationship) knowledgeHost.innerHTML = "";
    else knowledgeHost.innerHTML = '<div class="role-profile-draft-knowledge-head"><div><span>WORLD KNOWLEDGE</span><strong>' +
      (knowledge.length ? "本次引用 " + knowledge.length + " 条已导入资料" : "本次没有可用的已导入资料") +
      '</strong></div>' + '</div>' +
      (knowledge.length ? '<div class="role-profile-draft-knowledge-list">' + knowledge.map(item =>
        '<details><summary><strong>' + escapeHTML(item.title || "未命名资料") + '</strong><small>' +
        escapeHTML(item.source || "已导入世界知识") + '</small></summary><p>' + escapeHTML(item.text || "") + '</p></details>'
      ).join("") + "</div>" : '<p class="role-profile-draft-knowledge-empty">当前没有命中的 V2 世界资料。补充世界规则、角色经历或人物关系时，可先在「管理世界知识」配置本机来源并启用导入；对话引用还需单独开启。用户关系和群聊专属内容不会写入角色正本。</p>');
    const notes = state.draft.notes || [];
    const noteHost = $("[data-role-profile-draft-notes]", root);
    noteHost.hidden = !notes.length && !inferred.size;
    noteHost.innerHTML = (inferred.size
      ? '<p>' + icon("sparkles") + '<span>' + inferred.size + " 项含有限推断，请重点核对标记。</span></p>" : "") +
      notes.map(item => "<p>" + icon("info") + "<span>" + escapeHTML(item) + "</span></p>").join("");
    const groups = Array.isArray(spec.sections) ? spec.sections : [];
    let present = 0;
    if (state.selectedFields === null) {
      state.selectedFields = new Set(Object.keys(state.draft.fields || {})
        .filter(field => hasDraftContent(field, draftValue(field)) && !hasDraftContent(field, oldValue(field))));
    }
    const fieldsMarkup = groups.map((group, index) => {
      const fields = (group.fields || []).map(field => {
        const value = draftValue(field);
        const hasContent = hasDraftContent(field, value);
        if (hasContent) present++;
        const current = oldValue(field);
        const checked = hasContent && !!state.selectedFields?.has(field);
        const maximum = Number(state.limits?.[field] || 0);
        const label = spec.labels?.[field] || field;
        const help = spec.helps?.[field] || "";
        const rows = Number(spec.rows?.[field] || 3);
        const choices = spec.choices?.[field];
        const control = choices && typeof choices === "object"
          ? '<select data-role-profile-draft-value="' + escapeHTML(field) + '" data-limit="' + maximum + '" aria-label="' +
            escapeHTML(label) + '">' + Object.entries(choices).map(([optionValue, optionLabel]) =>
              '<option value="' + escapeHTML(optionValue) + '"' + (optionValue === value ? " selected" : "") + '>' +
              escapeHTML(optionLabel) + '</option>').join("") + '</select>'
          : '<textarea data-role-profile-draft-value="' + escapeHTML(field) + '" data-limit="' + maximum + '" rows="' + rows +
            '" placeholder="' + (value.trim() ? "检查并修改整理结果" : "暂时没有可靠内容，可在这里补充") + '">' + escapeHTML(value) + '</textarea>';
        const oldDisplay = choices?.[current] || current;
        const old = current.trim()
          ? '<details class="role-profile-draft-existing"><summary>已有设定</summary><p>' + escapeHTML(oldDisplay) + "</p></details>"
          : '<small class="role-profile-draft-empty">当前为空</small>';
        return '<article class="role-profile-draft-field' + (inferred.has(field) ? " is-inferred" : "") + '">' +
          '<label class="role-profile-draft-check"><input type="checkbox" data-role-profile-draft-select value="' + escapeHTML(field) + '"' +
          (checked ? " checked" : "") + (hasContent ? "" : " disabled") + ' aria-label="选择' + escapeHTML(label) + '">' +
          "<span>" + escapeHTML(label) + "</span>" + (inferred.has(field) ? "<i>" + icon("sparkles") + "推断</i>" : "") + "</label>" +
          control +
          '<div class="role-profile-draft-field-meta"><small>' + escapeHTML(help) + "</small><span data-role-profile-draft-length=\"" +
          escapeHTML(field) + "\">" + value.length + (maximum ? " / " + maximum : "") + "</span></div>" + old + "</article>";
      }).join("");
      const count = (group.fields || []).filter(field => hasDraftContent(field, draftValue(field))).length;
      return '<details class="role-profile-draft-group" data-role-profile-draft-group data-field-count="' +
        (group.fields || []).length + '"' + (index === 0 ? " open" : "") + "><summary><span>" +
        escapeHTML(group.title) + '</span><small data-role-profile-draft-group-count>' + count + " / " +
        (group.fields || []).length + " 项有内容</small></summary>" +
        '<p class="role-profile-draft-group-help">' + escapeHTML(group.help || "") + '</p><div class="role-profile-draft-field-list">' +
        fields + "</div></details>";
    }).join("");
    $("[data-role-profile-draft-groups]", root).innerHTML = fieldsMarkup;
    $("[data-role-profile-draft-count]", root).textContent = String(present);
    updateMeter();
    window.lucide?.createIcons({attrs: {"aria-hidden": "true"}});
  }
  function updateFieldCounts(root = $("#roleProfileDraftDialog")) {
    if (!root) return;
    const filled = control => hasDraftContent(control.dataset.roleProfileDraftValue, control.value);
    const all = [...root.querySelectorAll("[data-role-profile-draft-value]")];
    const count = all.filter(filled).length;
    $("[data-role-profile-draft-count]", root).textContent = String(count);
    for (const group of root.querySelectorAll("[data-role-profile-draft-group]")) {
      const fields = [...group.querySelectorAll("[data-role-profile-draft-value]")];
      const counter = group.querySelector("[data-role-profile-draft-group-count]");
      if (counter) counter.textContent = fields.filter(filled).length + " / " + group.dataset.fieldCount + " 项有内容";
    }
  }
  function resetGenerateButton() {
    const button = $("[data-role-profile-draft-generate]");
    if (!button) return;
    button.disabled = state.busy;
    button.innerHTML = icon("sparkles") + "<span>" + (state.draft ? "重新生成草稿" : "生成可编辑草稿") + "</span>";
    window.lucide?.createIcons({attrs: {"aria-hidden": "true"}});
  }
  function cancelPendingGeneration() {
    state.ticket++;
    if (state.busyKind !== "generate") return;
    state.busy = false;
    state.busyKind = "";
    resetGenerateButton();
    updateMeter();
  }
  function closeDialog() {
    cancelPendingGeneration();
    $("#roleProfileDraftDialog")?.close();
  }
  function openDialog(target = "role_profile") {
    const dialog = $("#roleProfileDraftDialog");
    if (!dialog) return;
    if (!["role_profile", "relationship_profile"].includes(target)) target = "role_profile";
    const scope = currentScope();
    const targetChanged = state.target !== target;
    if (targetChanged || state.scopeKey && state.scopeKey !== scope) {
      state.draft = state.source = state.current = state.limits = null;
      state.knowledge = [];
      state.fieldValues = {};
      state.selectedFields = null;
      state.totalLimit = target === "relationship_profile" ? 3000 : 30000;
      state.scopeKey = "";
      $("[data-role-profile-draft-output]", dialog).hidden = true;
      $("[data-role-profile-draft-actions]", dialog).hidden = true;
    }
    setTargetPresentation(target);
    const product = window.CompanionProduct;
    const endpoint = configEndpoint(product?.role?.(), product);
    state.pendingApply = restorePendingApply(target, scope, endpoint);
    if (state.pendingApply) {
      state.scopeKey = scope;
      notice("上次保存结果尚未确认；核对会沿用同一操作标识。", true);
    }
    $("#roleProfileDraftSupplement", dialog).value = state.supplements[target];
    if (state.draft && state.scopeKey === scope && state.target === target) paintDraft();
    if (!dialog.open) dialog.showModal();
    void loadPersonaChoices(window.CompanionProduct?.role?.());
    resetGenerateButton();
    renderPendingApply();
    window.lucide?.createIcons({attrs: {"aria-hidden": "true"}});
  }
  async function generate() {
    const dialog = $("#roleProfileDraftDialog");
    if (!dialog || state.busy) return;
    const product = window.CompanionProduct;
    const role = product?.role?.();
    const target = state.target;
    const personaId = $("[data-role-profile-draft-persona]", dialog)?.value || state.personaId;
    const providerId = $("[data-role-profile-draft-provider]", dialog)?.value || "";
    state.providerId = providerId;
    const scope = currentScope();
    const ticket = ++state.ticket;
    state.supplements[target] = $("#roleProfileDraftSupplement", dialog)?.value || "";
    state.busy = true;
    state.busyKind = "generate";
    const button = $("[data-role-profile-draft-generate]", dialog);
    button.disabled = true;
    button.innerHTML = '<span class="role-profile-draft-spinner" aria-hidden="true"></span><span>正在整理</span>';
    notice(target === "relationship_profile"
      ? "正在读取当前人格并整理这段关系资料…"
      : "正在读取当前人格并整理角色资料…");
    try {
      const payload = await personaRequest(role, target, state.supplements[target], personaId, providerId);
      if (ticket !== state.ticket || !dialog.open) return;
      if (payload.target !== target) throw Object.assign(new Error("草稿目标已经变化，请重新整理。"), {code: "role_profile_source_changed"});
      if (scope !== currentScope()) throw Object.assign(new Error("角色或对象已切换，请重新整理。"), {code: "role_profile_source_changed"});
      let current = null;
      let currentError = null;
      try { current = await readCurrent(role, target); }
      catch (error) { currentError = error; }
      if (ticket !== state.ticket || !dialog.open) return;
      state.draft = payload.draft;
      state.source = payload.source;
      state.knowledge = Array.isArray(payload.knowledge) ? payload.knowledge : [];
      state.fieldValues = {};
      state.selectedFields = null;
      state.limits = payload.limits;
      state.totalLimit = Number(payload.total_limit || (target === "relationship_profile" ? 3000 : 30000));
      state.scopeKey = scope;
      state.current = current;
      state.lastProviderId = String(payload.provider_id || providerId || "");
      if (currentError) notice("草稿已生成，但当前资料没有读到；暂时不能写入。" + apiError(currentError), true);
      else notice(target === "relationship_profile"
        ? "草稿只用于当前私聊关系；请核对用户资料与关系描述，再选择要保存的字段。"
        : "草稿已结合当前角色档案生成；本次引用的世界资料列在下方，请检查内容和推断标记。");
      paintDraft();
    } catch (error) {
      if (ticket === state.ticket) notice(apiError(error), true);
    } finally {
      if (ticket === state.ticket) {
        state.busy = false;
        state.busyKind = "";
        if (button.isConnected) resetGenerateButton();
        updateMeter();
      }
    }
  }
  async function submitPendingApply(pending) {
    const dialog = $("#roleProfileDraftDialog");
    const product = window.CompanionProduct;
    const role = product?.role?.();
    if (!dialog || !pending) return;
    if (pending.target !== state.target || pending.scopeKey !== currentScope()
        || pending.endpoint !== configEndpoint(role, product)) {
      notice("对象、角色或配置入口已变化；请切回原对象后核对这次保存。", true);
      return;
    }
    state.busy = true;
    state.busyKind = "apply";
    resetGenerateButton();
    updateMeter();
    renderPendingApply();
    try {
      const result = await product.request("POST", pending.endpoint, pending.body);
      const receipt = result?.result ?? result;
      if (typeof receipt?.operation_id !== "string" || typeof receipt?.replayed !== "boolean") {
        notice("服务回执不完整；保存结果尚未确认。核对时会沿用同一操作标识。", true);
        return;
      }
      const changes = pending.body.changes;
      clearPendingApply(pending);
      for (const field of Object.keys(changes)) state.selectedFields?.delete(field);
      /* 🔴 已写入的字段要同时更新草稿基线与当前值，否则 isDirty() 恒为真。
       *
       * isDirty() 比的是 fieldValues 与 draft.fields：
       *   保存成功后 draft.fields 仍是**生成草稿时**的旧值，
       *   fieldValues 仍是**用户改过**的新值 ⇒ 两者恒不相等
       *   ⇒ 换任何分区都弹「还有未保存的改动」，可用户明明已经保存了。
       * （情绪 2026-11-02 报障：自我界面保存后切换仍提示未保存。）
       *
       * 所以把两边都对齐到**刚写进去的值**：textarea 继续显示保存后的内容
       * （不回落到旧草稿），而基线与当前值相等 ⇒ 不再计为未保存。
       *
       * ⚠️ 只处理真正写进去的那几个字段，不整个清空：用户可能还改了别的字段
       * 但没勾选，那些改动必须继续算未保存，不能被这次保存顺手掩盖。 */
      if (state.draft) {
        state.draft.fields = state.draft.fields || {};
        for (const [field, value] of Object.entries(changes)) {
          state.draft.fields[field] = value;
          state.fieldValues[field] = value;
        }
      }
      document.dispatchEvent(new Event(pending.target === "relationship_profile"
        ? "companion:relationship-profile-updated" : "companion:role-profile-updated"));
      const savedMessage = (receipt.replayed ? "原保存已确认" : "已保存") + " " + Object.keys(changes).length +
        (pending.target === "relationship_profile" ? " 项相处资料" : " 项角色资料") +
        (pending.skipped_count ? "，" + pending.skipped_count + " 项已有内容未覆盖" : "") + "。";
      try {
        state.current = await readCurrent(role, pending.target);
        if (state.draft) paintDraft();
        notice(savedMessage);
      } catch {
        state.current = null;
        notice(savedMessage + "但当前资料未能刷新，请重新打开后核对。", true);
      }
    } catch (error) {
      if (error.confirmed === true) clearPendingApply(pending);
      notice(error.confirmed === true ? apiError(error)
        : "保存结果尚未确认；点击“核对原保存结果”会沿用同一请求和操作标识。", true);
    } finally {
      state.busy = false;
      state.busyKind = "";
      resetGenerateButton();
      renderPendingApply();
      updateMeter();
    }
  }
  async function recoverPendingApply() {
    if (state.busy || !state.pendingApply) return;
    await submitPendingApply(state.pendingApply);
  }
  async function apply(mode) {
    const dialog = $("#roleProfileDraftDialog");
    const product = window.CompanionProduct;
    if (!dialog || state.busy) return;
    if (state.pendingApply) { await recoverPendingApply(); return; }
    if (!state.draft) return;
    if (!state.scopeKey || currentScope() !== state.scopeKey) {
      notice("会话对象或角色已切换，请重新整理这份草稿。", true);
      return;
    }
    const role = product?.role?.();
    const endpoint = configEndpoint(role, product);
    if (!endpoint) { notice("当前角色没有配置保存入口。", true); return; }
    const values = Object.fromEntries(selectedFields().map(field => {
      const textarea = $("[data-role-profile-draft-value=\"" + field + "\"]", dialog);
      return [field, textarea?.value || ""];
    }));
    if (!Object.keys(values).length) { notice("先勾选至少一项有内容的资料。", true); return; }
    state.busy = true;
    state.busyKind = "apply";
    resetGenerateButton();
    updateMeter();
    const buttons = [...dialog.querySelectorAll("[data-role-profile-draft-apply]")];
    buttons.forEach(button => { button.disabled = true; });
    try {
      const fresh = await readCurrent(role, state.target);
      const changes = {};
      const skipped = [];
      for (const [field, value] of Object.entries(values)) {
        const maximum = Number(state.limits?.[field] || 0);
        if (maximum && value.length > maximum) throw new Error((profileSpec().labels?.[field] || field) + " 超过 " + maximum + " 字。");
        if (mode === "empty" && String(fresh.settings[field] || "").trim()) { skipped.push(field); continue; }
        changes[field] = value;
      }
      const combined = {...fresh.settings, ...changes};
      const total = Object.values(combined).reduce((sum, value) => sum + String(value || "").length, 0);
      if (total > Number(state.totalLimit || 30000)) throw new Error("保存后资料将达到 " + total.toLocaleString("zh-CN") +
        " 字，超过 " + Number(state.totalLimit || 30000).toLocaleString("zh-CN") + " 字上限。");
      if (!Object.keys(changes).length) {
        state.current = fresh;
        paintDraft();
        notice("所选字段已有内容，没有写入；选择“覆盖所选字段”可替换它们。");
        return;
      }
      const pending = {target:state.target, scopeKey:state.scopeKey, endpoint, skipped_count:skipped.length,
        body:{section:state.target, operation_key:operationKey(), edit_token:fresh.edit_token, changes}};
      state.pendingApply = pending;
      if (!storePendingApply(pending)) {
        /* 落盘失败只损失刷新后的核对能力，本次提交与结果核对仍照常进行。 */
        notice("本次保存无法在刷新页面后继续核对；如未确认结果，请在离开前点「核对原保存结果」。");
      }
      renderPendingApply();
      await submitPendingApply(pending);
    } catch (error) {
      notice(apiError(error), true);
    } finally {
      state.busy = false;
      state.busyKind = "";
      resetGenerateButton();
      updateMeter();
    }
  }
  function syncField(field, control) {
    const row = control.closest(".role-profile-draft-field");
    const checkbox = row?.querySelector("[data-role-profile-draft-select]");
    state.fieldValues[field] = control.value;
    if (checkbox && hasDraftContent(field, control.value)) {
      checkbox.disabled = false;
      checkbox.checked = true;
      state.selectedFields?.add(field);
    }
    if (checkbox && !hasDraftContent(field, control.value)) {
      checkbox.checked = false;
      checkbox.disabled = true;
      state.selectedFields?.delete(field);
    }
    const counter = row?.querySelector("[data-role-profile-draft-length=\"" + field + "\"]");
    const limit = Number(control.dataset.limit || 0);
    if (counter) {
      counter.textContent = control.value.length + (limit ? " / " + limit : "");
      counter.dataset.over = limit && control.value.length > limit ? "true" : "false";
    }
    updateFieldCounts();
    updateMeter();
  }

  document.addEventListener("click", event => {
    const open = event.target.closest?.("[data-role-profile-draft-open]");
    if (open) {
      event.preventDefault();
      openDialog(open.dataset.roleProfileDraftOpen || "role_profile");
      /* 从「角色自述」画布进入时，把画布内容作为补充设定的初始值；
         该目标已有补充稿时不覆盖，避免辛苦写的文字被画布内容顶掉。 */
      if (open.dataset.selfwriteSource !== undefined) {
        const canvas = $("#selfwriteCanvas");
        const supplement = $("#roleProfileDraftSupplement");
        if (canvas && supplement && canvas.value.trim() && !state.supplements[state.target]) {
          supplement.value = canvas.value;
          state.supplements[state.target] = supplement.value;
        }
      }
      return;
    }
    const close = event.target.closest?.("[data-role-profile-draft-close]");
    if (close) { event.preventDefault(); closeDialog(); return; }
    if (event.target.closest?.("[data-role-profile-draft-recover]")) {
      event.preventDefault(); void recoverPendingApply(); return;
    }
    if (event.target.matches?.("#roleProfileDraftDialog")) { closeDialog(); return; }
    if (event.target.closest?.("[data-role-profile-draft-knowledge-open]")) {
      event.preventDefault();
      closeDialog();
      document.querySelector('.deck-tabs [data-self-pane="knowledge"]')?.click();
      return;
    }
    if (event.target.closest?.("[data-role-profile-draft-generate]")) { event.preventDefault(); void generate(); return; }
    if (event.target.closest?.("[data-role-profile-draft-select-empty]")) {
      event.preventDefault();
      state.selectedFields = new Set();
      for (const input of document.querySelectorAll("[data-role-profile-draft-select]")) {
        const value = $("[data-role-profile-draft-value=\"" + input.value + "\"]")?.value || "";
        input.checked = hasDraftContent(input.value, value) && !hasDraftContent(input.value, oldValue(input.value));
        if (input.checked) state.selectedFields.add(input.value);
      }
      updateMeter(); return;
    }
    if (event.target.closest?.("[data-role-profile-draft-clear]")) {
      event.preventDefault();
      state.selectedFields = new Set();
      document.querySelectorAll("[data-role-profile-draft-select]").forEach(input => { input.checked = false; });
      updateMeter(); return;
    }
    const applyButton = event.target.closest?.("[data-role-profile-draft-apply]");
    if (applyButton) { event.preventDefault(); void apply(applyButton.dataset.roleProfileDraftApply); }
  });
  document.addEventListener("input", event => {
    const textarea = event.target.closest?.("textarea[data-role-profile-draft-value]");
    if (textarea) syncField(textarea.dataset.roleProfileDraftValue, textarea);
    if (event.target.matches?.("#roleProfileDraftSupplement")) state.supplements[state.target] = event.target.value;
  });
  document.addEventListener("change", event => {
    const choice = event.target.closest?.("select[data-role-profile-draft-value]");
    if (choice) syncField(choice.dataset.roleProfileDraftValue, choice);
    if (event.target.matches?.("[data-role-profile-draft-select]")) {
      const value = $("[data-role-profile-draft-value=\"" + event.target.value + "\"]")?.value;
      if (event.target.checked && hasDraftContent(event.target.value, value)) state.selectedFields?.add(event.target.value);
      else state.selectedFields?.delete(event.target.value);
      updateMeter();
    }
  });
  document.addEventListener("cancel", event => {
    if (event.target?.id === "roleProfileDraftDialog") cancelPendingGeneration();
  }, true);

  /* 🔴 主编辑页保存成功后，把草稿的脏状态抹掉。
   *
   * `isDirty()` 比的是 fieldValues 与 draft.fields；而草稿一旦生成过，
   * 那份 draft 对象就一直留在 state 里，**从不随外部保存更新**。
   * 于是用户在主页面（角色档案/基础身份）保存之后，草稿这边仍然
   * 认为「有改动没提交」，切任何分区都弹「还有未保存的改动」。
   * （情绪 2026-11-02 报障：自我界面保存后切换仍提示未保存。）
   *
   * 外部保存意味着草稿已经过期（它的基线是保存前的资料），继续拿旧基线
   * 判脏必然误报；直接丢弃草稿比假装它还准确更安全 —— 用户要看新内容
   * 重新生成一次即可，而误报会拦住所有正常换页。 */
  const dropStaleDraft = target => {
    if (state.target !== target || !state.draft) return;
    state.draft = null;
    state.fieldValues = {};
    state.selectedFields = null;
  };
  document.addEventListener("companion:role-profile-updated",
    () => dropStaleDraft("role_profile"));
  document.addEventListener("companion:relationship-profile-updated",
    () => dropStaleDraft("relationship_profile"));

  /* 页面级未保存守卫（page-guards.js）用：草稿里改过但没提交的字段。
     valueOf() 的回落规则是「fieldValues 优先，否则用 draft.fields」，
     所以和 draft 里的原值比一遍即可。 */
  function isDirty() {
    const draft = state.draft;
    if (!draft || !state.fieldValues) return false;
    return Object.entries(state.fieldValues).some(([field, value]) =>
      String(value ?? "") !== String(draft.fields?.[field] ?? ""));
  }

  function changedFields() {
    return Object.entries(state.fieldValues || {}).filter(([field,value]) =>
      String(value ?? '') !== String(state.draft?.fields?.[field] ?? ''))
      .map(([field]) => profileSpec().labels?.[field] || field);
  }
  function discardChanges() {
    if(state.busy)return {error:'档案正在处理，请稍后切换。'};
    state.fieldValues = {};
    return true;
  }
  function saveBeforeLeave() {
    return {error:'请在角色档案草稿中勾选要写入的字段，再使用“填入所选空白字段”或“覆盖所选字段”保存。'};
  }
  return {markup, open: openDialog, isDirty, changedFields, saveBeforeLeave, discardChanges};
})();
