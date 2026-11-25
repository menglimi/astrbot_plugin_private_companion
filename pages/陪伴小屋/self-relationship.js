"use strict";
(() => {
  const state = {roleKey: "", root: null, people: [], selectedKey: "", person: null, section: null,
    contactSection: null, contactDrafts: {}, contactDirty: new Set(), contactPending: null,
    loading: false, busy: false, error: "", message: "", failed: false, dirty: new Set(), drafts: {}, pending: null, ticket: 0, confirmation: null};
  const esc = value => String(value ?? "").replace(/[&<>\"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;", "'":"&#39;"}[char]));
  const product = () => window.CompanionProduct;
  const routeOf = person => ({platform: person.platform, bot_id: person.bot_id,
    account_id: person.account_id, role_id: person.role_id});
  const routeKey = person => JSON.stringify([person.subject_id, person.platform, person.bot_id,
    person.account_id, person.role_id]);
  const operationKey = () => window.crypto?.randomUUID?.()
    || `relationship-${Date.now()}-${Math.random().toString(36).slice(2)}`;

  const fieldLabels = {default_nickname: "对用户的称呼", role_relation: "关系定位",
    user_gender: "性别", user_birthday: "生日", user_occupation: "职业", interaction: "相处方式",
    extra: "其他补充", enabled: "主动联系", automatic_enabled: "后台主动联系",
    quiet_enabled: "私聊免打扰", note: "个人联系偏好", style_prompt: "表达偏好"};
  const hint = (text, label = "查看生效范围") => window.CompanionWorkbench.scopeHintMarkup(text, label);
  const isDirty = () => state.dirty.size > 0 || state.contactDirty.size > 0;
  const changedFields = () => [...state.dirty, ...state.contactDirty].map(name => fieldLabels[name] || name);
  function discardChanges() {
    state.drafts = {...(state.section?.settings || {})};
    state.contactDrafts = {...(state.contactSection?.settings || {})};
    state.dirty.clear();
    state.contactDirty.clear();
    state.pending = null;
    state.contactPending = null;
    render();
    return true;
  }
  async function saveBeforeLeave() {
    if (state.loading || state.busy) return {error: "正在处理，请稍后再试。"};
    if (state.dirty.size) await save();
    if (state.dirty.size) return {error: state.error || "关系资料尚未保存。"};
    if (state.contactDirty.size) await saveContact();
    return isDirty() ? {error: state.error || "主动联系设置尚未保存。"} : true;
  }
  function confirmChanges() {
    if (state.confirmation) return state.confirmation;
    if (state.loading || state.busy) return Promise.resolve(false);
    if (!isDirty()) return Promise.resolve(true);
    state.confirmation = Promise.resolve(window.CompanionConfirm({
      title: "还有未保存的改动",
      message: "可以保存后继续，或放弃这些改动。",
      details: [{title: `${product().roleLabel(product().role())} 与 ${state.person.display_name || state.person.native_account_id} 的关系`, fields: changedFields()}],
      confirmText: "放弃并继续", cancelText: "继续编辑",
      onSave: saveBeforeLeave, onConfirm: discardChanges, danger: true,
    })).then(ok => ok === true).finally(() => { state.confirmation = null; });
    return state.confirmation;
  }

  function render() {
    const root = state.root;
    if (!root) return;
    const api = product();
    const role = api.role();
    const person = state.person;
    const options = state.people.length > 1
      ? `<label class="self-relationship-picker"><span>核心用户</span><select data-self-core-picker aria-label="选择核心用户"${state.loading || state.busy ? " disabled" : ""}><option value="">选择核心用户</option>${state.people.map(item => `<option value="${esc(routeKey(item))}"${person && routeKey(person) === routeKey(item) ? " selected" : ""}>${esc(item.display_name || item.native_account_id || "未命名用户")}${state.people.some(other => other !== item && other.display_name === item.display_name) ? ` · ${esc(item.account_name || item.account_id || item.native_account_id)}` : ""}</option>`).join("")}</select></label>`
      : "";
    const error = state.error ? `<p class="self-relationship-feedback is-error" role="alert">${esc(state.error)}</p>` : "";
    const message = state.message ? `<p class="self-relationship-feedback${state.failed ? " is-error" : ""}" role="status">${esc(state.message)}</p>` : "";
    if (!role) {
      root.innerHTML = `<div class="self-relationship-empty" role="status">尚未选择角色</div>`;
      return;
    }
    if (state.loading && !person) {
      root.innerHTML = `<div class="self-relationship-empty" role="status">正在读取核心用户…</div>`;
      return;
    }
    if (!state.people.length) {
      root.innerHTML = `<div class="self-relationship-empty"><strong>当前角色尚未设置核心用户</strong><button type="button" class="button small" data-go="manage" data-manage="identity-people">打开成员管理</button></div>${error}`;
      return;
    }
    if (!person) {
      root.innerHTML = `<div class="self-relationship-empty"><strong>选择要编辑的核心用户</strong>${options}</div>${error}`;
      return;
    }
    const platform = api.platformLabel?.({platform_id: person.platform, platform_name: person.platform_name}) || person.platform || "未知平台";
    const account = person.account_name || person.account_id || "未知账号";
    const name = person.display_name || person.native_account_id || "未命名用户";
    const identity = `<header class="self-relationship-identity"><span class="self-relationship-avatar" aria-hidden="true"><i data-lucide="user-round"></i></span><div class="self-relationship-identity-main"><div class="self-relationship-name-row"><h3>${esc(name)}</h3><span class="self-relationship-badge">核心用户</span>${options}</div><div class="self-relationship-meta"><span><b>角色</b>${esc(api.roleLabel(role))}</span><span><b>Bot</b>${esc(account)}</span>${hint(`平台：${platform}；用户账号：${person.native_account_id || "—"}。这里只选择关系资料的编辑对象，不会切换页面的会话。`, "查看用户信息")}</div></div><button type="button" class="self-relationship-refresh" data-self-relationship-action="reload" aria-label="重新读取" title="重新读取"><i data-lucide="refresh-cw"></i></button></header>`;
    const settings = state.section?.settings || {};
    const fields = [
      ["default_nickname", "对用户的称呼", "line", 100],
      ["role_relation", "关系定位", "line", 240],
      ["user_gender", "性别", "line", 80],
      ["user_birthday", "生日", "line", 100],
      ["user_occupation", "职业", "line", 160],
      ["interaction", "相处方式", "area", 900],
      ["extra", "其他补充", "area", 900],
    ];
    const controls = fields.map(([name, label, kind, limit]) => `<label class="self-relationship-field${kind === "area" || name === "user_occupation" ? " is-wide" : ""}"><span>${label}</span>${kind === "area"
      ? `<textarea data-self-relationship-field="${name}" rows="${name === "interaction" ? 5 : 4}" maxlength="${limit}"${state.loading || state.busy || !state.section ? " disabled" : ""}>${esc(state.drafts[name] ?? settings[name] ?? "")}</textarea>`
      : `<input type="text" data-self-relationship-field="${name}" maxlength="${limit}" value="${esc(state.drafts[name] ?? settings[name] ?? "")}"${state.loading || state.busy || !state.section ? " disabled" : ""}>`}</label>`).join("");
    const contactSettings = state.contactSection?.settings || {};
    const contactFields = [
      ["enabled", "主动联系", "toggle"],
      ["automatic_enabled", "后台主动联系", "toggle"],
      ["quiet_enabled", "私聊免打扰", "toggle"],
      ["note", "个人联系偏好", "area"],
      ["style_prompt", "表达偏好", "area"],
    ];
    const contactControls = contactFields.map(([name, label, kind]) => {
      const provenance = state.contactSection?.provenance?.[name] || {};
      const explicit = provenance.explicit === true;
      const source = explicit ? "此私聊单独设置"
        : provenance.layer === "relationship" ? "继承自该用户的关系设置"
        : provenance.layer === "role" ? "继承自角色默认" : "继承自全局默认";
      const current = state.contactDrafts[name] ?? contactSettings[name];
      if (kind === "toggle") return `<div class="self-contact-switch"><label>
        <input type="checkbox" data-self-contact-field="${name}"${current === true ? " checked" : ""}
          aria-label="${label}"${state.loading || state.busy || !state.contactSection ? " disabled" : ""}>
        <strong>${label}</strong></label>${hint(source, `${label}的来源`)}</div>`;
      return `<div class="self-relationship-field is-wide"><div class="self-contact-field-label"><label for="self-contact-${name}">${label}</label>${hint(source, `${label}的来源`)}</div>
        <textarea id="self-contact-${name}" data-self-contact-field="${name}" rows="${name === "note" ? 4 : 5}" maxlength="2000"
          ${state.loading || state.busy || !state.contactSection ? " disabled" : ""}>${esc(current ?? "")}</textarea></div>`;
    }).join("");
    const contactPanel = state.contactSection ? `<section class="self-contact-settings" aria-label="该用户私聊的主动联系设置">
      <header class="self-contact-heading"><h4>主动联系</h4><span>${esc(name)}的私聊</span></header>
      <div class="self-contact-fields">${contactControls}</div>
      <footer class="self-relationship-actions"><span>${state.contactDirty.size ? `${state.contactDirty.size} 项未保存`
        : state.loading ? "正在读取…" : "已同步"}</span><div class="self-save-actions wb-save-context">${hint(`保存到：${api.roleLabel(role)} 与 ${name}的私聊。Bot：${account}。`)}<button type="button" class="button small primary" title="${esc(`保存到：${api.roleLabel(role)} 与 ${name}的私聊`)}"
          data-self-relationship-action="save-contact"${state.contactDirty.size && !state.busy && !state.loading ? "" : " disabled"}>
          ${state.busy && state.contactPending ? "保存中…" : "保存"}</button></div></footer>
    </section>` : "";
    const waiting = state.loading && !state.section
      ? `<p class="self-relationship-empty" role="status">正在读取关系资料…</p>` : "";
    const actions = `<footer class="self-relationship-actions"><span>${state.dirty.size ? `${state.dirty.size} 项未保存` : state.loading ? "正在读取…" : "已同步"}</span><div class="self-save-actions wb-save-context">${hint(`保存到：${api.roleLabel(role)} 与 ${name}的关系。Bot：${account}。`)}<button type="button" class="button small primary" title="${esc(`保存到：${api.roleLabel(role)} 与 ${name}的关系`)}" data-self-relationship-action="save"${state.dirty.size && state.section && !state.busy && !state.loading ? "" : " disabled"}>${state.busy ? "保存中" : "保存"}</button></div></footer>`;
    root.innerHTML = `${identity}${error}${message}${waiting}<div class="self-relationship-fields">${controls}</div>${actions}${contactPanel}`;
    window.lucide?.createIcons();
  }

  function errorText(error) {
    return product().errorLabel?.(error?.code) || error?.message || "读取失败，请重新读取后重试。";
  }

  async function readProfile(ticket, person) {
    try {
      const result = await product().request("GET", "acquaintance/settings", {
        subject_id: person.subject_id, ...routeOf(person),
      });
      if (ticket !== state.ticket || state.person !== person) return;
      state.section = result.sections?.profile || null;
      state.contactSection = result.sections?.contact || null;
      if (!state.section) throw new Error("核心用户关系资料暂不可读取。");
      state.drafts = {...state.section.settings};
      state.contactDrafts = {...(state.contactSection?.settings || {})};
      state.dirty.clear();
      state.contactDirty.clear();
      state.pending = null;
      state.contactPending = null;
    } catch (error) {
      if (ticket === state.ticket) state.error = errorText(error);
    }
  }

  async function readDirectory() {
    const api = product();
    const role = api.role();
    const owner = api.roleTarget?.();
    const ticket = ++state.ticket;
    state.loading = true;
    state.error = "";
    state.message = "";
    state.failed = false;
    render();
    try {
      const directory = await api.acquaintanceDirectory();
      if (ticket !== state.ticket) return;
      const matching = (directory.persons || []).filter(person => person.private_target === true
        && person.role_id === role?.role_id && person.bot_id === owner?.bot_id
        && person.identity_tier === "core");
      const byRoute = new Map();
      for (const person of matching) {
        const existing = byRoute.get(routeKey(person));
        if (!existing || person.settings_available === true && !existing.settings_available)
          byRoute.set(routeKey(person), person);
      }
      state.people = [...byRoute.values()].sort((a, b) =>
        String(a.display_name || a.native_account_id).localeCompare(String(b.display_name || b.native_account_id), "zh-CN"));
      const selected = state.people.find(person => routeKey(person) === state.selectedKey)
        || (state.people.length === 1 ? state.people[0] : null);
      state.person = selected || null;
      state.selectedKey = selected ? routeKey(selected) : "";
      state.section = null;
      state.contactSection = null;
      state.dirty.clear();
      state.contactDirty.clear();
      state.drafts = {};
      state.contactDrafts = {};
      state.pending = null;
      state.contactPending = null;
      if (selected?.settings_available === true) await readProfile(ticket, selected);
      else if (selected) state.error = "该核心用户没有当前角色下可编辑的私聊资料。";
    } catch (error) {
      if (ticket === state.ticket) state.error = errorText(error);
    } finally {
      if (ticket === state.ticket) {
        state.loading = false;
        render();
      }
    }
  }

  async function save() {
    const person = state.person;
    const section = state.section;
    if (state.busy || state.loading || !person || !section || !state.dirty.size) return;
    const changes = Object.fromEntries([...state.dirty].map(name => [name, state.drafts[name]]));
    const fingerprint = JSON.stringify(changes);
    if (state.pending?.fingerprint !== fingerprint)
      state.pending = {fingerprint, key: operationKey()};
    const ticket = state.ticket;
    state.busy = true;
    state.message = "";
    render();
    try {
      const result = await product().request("POST", "acquaintance/settings", {
        operation_key: state.pending.key,
        subject_id: person.subject_id,
        route: routeOf(person),
        sections: {profile: {edit_token: section.edit_token, changes}},
      });
      if (ticket !== state.ticket) return;
      state.section = result.sections?.profile || section;
      state.drafts = {...state.section.settings};
      state.dirty.clear();
      state.pending = null;
      state.error = "";
      state.message = "关系资料已保存。";
      state.failed = false;
    } catch (error) {
      if (ticket === state.ticket) {
        state.error = errorText(error);
        state.failed = true;
      }
    } finally {
      if (ticket === state.ticket) {
        state.busy = false;
        render();
      }
    }
  }

  async function saveContact() {
    const person = state.person;
    const section = state.contactSection;
    if (state.busy || state.loading || !person || !section || !state.contactDirty.size) return;
    const changes = Object.fromEntries([...state.contactDirty].map(name => [name, state.contactDrafts[name]]));
    const fingerprint = JSON.stringify(changes);
    if (state.contactPending?.fingerprint !== fingerprint)
      state.contactPending = {fingerprint, key: operationKey()};
    const ticket = state.ticket;
    state.busy = true;
    state.message = "";
    render();
    try {
      const result = await product().request("POST", "acquaintance/settings", {
        operation_key: state.contactPending.key,
        subject_id: person.subject_id,
        route: routeOf(person),
        sections: {contact: {edit_token: section.edit_token, changes}},
      });
      if (ticket !== state.ticket) return;
      state.contactSection = result.sections?.contact || section;
      state.contactDrafts = {...state.contactSection.settings};
      state.contactDirty.clear();
      state.contactPending = null;
      state.error = "";
      state.message = "该私聊窗口的主动联系设置已保存。";
      state.failed = false;
    } catch (error) {
      if (ticket === state.ticket) {
        state.error = errorText(error);
        state.failed = true;
      }
    } finally {
      if (ticket === state.ticket) {
        state.busy = false;
        render();
      }
    }
  }

  function updateContactField(input) {
    const name = input.dataset.selfContactField;
    const next = input.type === "checkbox" ? input.checked : input.value;
    state.contactDrafts[name] = next;
    if (next === (state.contactSection?.settings?.[name] ?? (input.type === "checkbox" ? false : "")))
      state.contactDirty.delete(name);
    else state.contactDirty.add(name);
    state.contactPending = null;
    const saveButton = state.root?.querySelector('[data-self-relationship-action="save-contact"]');
    if (saveButton) saveButton.disabled = !state.contactDirty.size || !state.contactSection || state.busy || state.loading;
    const count = state.root?.querySelector(".self-contact-settings .self-relationship-actions>span");
    if (count) count.textContent = state.contactDirty.size ? `${state.contactDirty.size} 项未保存` : "已同步";
  }

  function bind(root) {
    root.addEventListener("focusin", event => window.CompanionWorkbench.scopeHintReset(event));
    root.addEventListener("focusout", event => window.CompanionWorkbench.scopeHintReset(event));
    root.addEventListener("pointerover", event => window.CompanionWorkbench.scopeHintReset(event));
    root.addEventListener("pointerout", event => window.CompanionWorkbench.scopeHintReset(event));
    root.addEventListener("keydown", event => window.CompanionWorkbench.scopeHintKeydown(event));
    root.addEventListener("input", event => {
      const contact = event.target.closest?.("[data-self-contact-field]");
      if (contact && contact.type !== "checkbox") { updateContactField(contact); return; }
      const input = event.target.closest?.("[data-self-relationship-field]");
      if (!input) return;
      const name = input.dataset.selfRelationshipField;
      state.drafts[name] = input.value;
      if (input.value === (state.section?.settings?.[name] ?? "")) state.dirty.delete(name);
      else state.dirty.add(name);
      state.pending = null;
      const saveButton = root.querySelector('[data-self-relationship-action="save"]');
      if (saveButton) saveButton.disabled = !state.dirty.size || !state.section || state.busy || state.loading;
      const count = root.querySelector(".self-relationship-actions>span");
      if (count) count.textContent = state.dirty.size ? `${state.dirty.size} 项未保存` : "已同步";
    });
    root.addEventListener("change", async event => {
      const contact = event.target.closest?.("[data-self-contact-field]");
      if (contact) { updateContactField(contact); return; }
      const picker = event.target.closest?.("[data-self-core-picker]");
      if (!picker) return;
      const person = state.people.find(item => routeKey(item) === picker.value);
      picker.value = state.selectedKey;
      if (!person || person === state.person || !await confirmChanges()) return;
      state.person = person;
      state.selectedKey = routeKey(person);
      state.section = null;
      state.contactSection = null;
      state.dirty.clear();
      state.contactDirty.clear();
      state.drafts = {};
      state.contactDrafts = {};
      state.pending = null;
      state.contactPending = null;
      state.error = "";
      state.message = "";
      const ticket = ++state.ticket;
      state.loading = true;
      render();
      if (person.settings_available === true) {
        void readProfile(ticket, person).finally(() => {
          if (ticket === state.ticket) {
            state.loading = false;
            render();
          }
        });
      } else {
        state.error = "该核心用户没有当前角色下可编辑的私聊资料。";
        state.loading = false;
        render();
      }
    });
    root.addEventListener("click", event => {
      if (window.CompanionWorkbench.scopeHintClick(event)) return;
      const action = event.target.closest?.("[data-self-relationship-action]")?.dataset.selfRelationshipAction;
      if (action === "reload") void confirmChanges().then(ok => { if (ok) return readDirectory(); });
      if (action === "save") void save();
      if (action === "save-contact") void saveContact();
    });
  }

  function mount(root) {
    if (!root) return;
    if (state.root !== root) {
      state.root = root;
      bind(root);
    }
    const role = product().role();
    const owner = product().roleTarget?.();
    const roleKey = JSON.stringify([role?.role_id || "", owner?.bot_id || ""]);
    if (roleKey !== state.roleKey) {
      state.roleKey = roleKey;
      ++state.ticket;
      state.loading = false;
      state.busy = false;
      state.selectedKey = "";
      state.person = null;
      state.section = null;
      state.contactSection = null;
      state.people = [];
      state.dirty.clear();
      state.contactDirty.clear();
      state.drafts = {};
      state.contactDrafts = {};
      state.pending = null;
      state.contactPending = null;
      if (role) void readDirectory();
      else render();
    } else render();
  }

  document.addEventListener("companion:relationship-profile-updated", () => {
    if (!state.person || !state.root?.isConnected || isDirty() || state.busy) return;
    const ticket = ++state.ticket;
    state.loading = true;
    render();
    void readProfile(ticket, state.person).finally(() => {
      if (ticket === state.ticket) {
        state.loading = false;
        render();
      }
    });
  });
  window.CompanionSelfRelationship = {mount, isDirty, changedFields, saveBeforeLeave, discardChanges};
})();
