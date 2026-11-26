"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "../pages/陪伴小屋/self-relationship.js"), "utf8");
const profile = {edit_token: "profile-v1", settings: {default_nickname: "主人", role_relation: "朋友",
  user_gender: "", user_birthday: "", user_occupation: "", interaction: "", extra: ""}};
const contact = {edit_token: "contact-v1", scope: "window", settings: {enabled: true,
  automatic_enabled: false, quiet_enabled: false, note: "", style_prompt: ""}, provenance: {
  enabled: {explicit: false}, automatic_enabled: {explicit: false}, quiet_enabled: {explicit: false},
  note: {explicit: false}, style_prompt: {explicit: false},
}};

function person(subject, changes = {}) {
  return {subject_id: subject, native_account_id: subject, display_name: subject, platform: "qq-main",
    bot_id: "bot-a", account_id: "account-a", role_id: "role-a", private_target: true,
    settings_available: true, identity_tier: "core", ...changes};
}

function harness(persons) {
  const calls = [];
  const confirms = [];
  const behavior = {choice: "cancel", failPost: false, role: "role-a", bot: "bot-a", directory: null, get: null};
  const listeners = {};
  const saveButton = {disabled: true};
  const status = {textContent: ""};
  const contactStatus = {textContent: ""};
  const root = {innerHTML: "", handlers: {}, addEventListener(type, callback) { this.handlers[type] = callback; },
    querySelector(selector) { return selector.includes("data-self-relationship-action") ? saveButton
      : selector === ".self-contact-settings .self-relationship-actions>span" ? contactStatus
      : selector === ".self-relationship-actions>span" ? status : null; }};
  const product = {
    role: () => behavior.role ? ({role_id: behavior.role}) : null, roleTarget: () => ({bot_id: behavior.bot}),
    roleLabel: () => "澄汐", platformLabel: item => ({"qq-main": "QQ · OneBot", telegram: "Telegram"})[item.platform_name || item.platform_id],
    acquaintanceDirectory: async () => behavior.directory ? behavior.directory() : ({persons}), errorLabel: () => "读取失败。",
    request: async (method, route, body) => {
      calls.push({method, route, body});
      if (method === "GET") return behavior.get ? behavior.get(body) : {sections: {profile, contact}};
      if (behavior.failPost) throw new Error("保存失败");
      const sections = {};
      if (body.sections.profile) sections.profile = {...profile, edit_token: "profile-v2",
        settings: {...profile.settings, ...body.sections.profile.changes}};
      if (body.sections.contact) sections.contact = {...contact, edit_token: "contact-v2",
        settings: {...contact.settings, ...body.sections.contact.changes}, provenance: {
          ...contact.provenance,
          ...Object.fromEntries(Object.keys(body.sections.contact.changes).map(name => [name, {explicit: true}])),
        }};
      return {sections};
    },
  };
  const document = {addEventListener(type, callback) {listeners[type] = callback;}};
  const window = {CompanionProduct: product, crypto: {randomUUID: () => "operation-1"},
    CompanionWorkbench: {scopeHintMarkup: (text, label) => `<span class="wb-context"><button aria-label="${label}">ⓘ</button><span>${text}</span></span>`,
      scopeHintClick: () => false, scopeHintKeydown() {}, scopeHintReset() {}},
    CompanionConfirm: async options => {
      confirms.push(options);
      if (behavior.choice === "save") return await options.onSave() === true;
      if (behavior.choice === "discard") return await options.onConfirm() === true;
      return false;
    }};
  vm.runInNewContext(source, {window, document, Promise, Date, Math, JSON, Object, String, Map, Set, Array, Error});
  return {root, calls, confirms, behavior, listeners, editor: window.CompanionSelfRelationship, saveButton, status, contactStatus, mount: window.CompanionSelfRelationship.mount};
}

test("Telegram 核心用户按通道类型显示平台", async () => {
  const h = harness([person("core-1", {platform: "default", platform_name: "telegram"})]);
  h.mount(h.root);
  await settle();
  assert.match(h.root.innerHTML, /平台：Telegram；/);
});

const settle = async () => { for (let index = 0; index < 8; index++) await new Promise(resolve => setImmediate(resolve)); };

test("核心关系资料按当前角色读取，多位核心用户时明确选择并保存 profile 分区", async () => {
  const h = harness([
    person("core-1"), person("core-2", {account_id: "account-b"}),
    person("other-role", {role_id: "role-b"}), person("other-bot", {bot_id: "bot-b"}), person("regular", {identity_tier: "standard"}),
  ]);
  h.mount(h.root);
  await settle();
  assert.match(h.root.innerHTML, /选择要编辑的核心用户/);
  assert.match(h.root.innerHTML, /core-1/);
  assert.match(h.root.innerHTML, /core-2/);
  assert.doesNotMatch(h.root.innerHTML, /other-role|other-bot|regular/);
  assert.equal(h.calls.length, 0, "未选择用户时不能读取或编辑任意私聊对象的资料");

  const selectedKey = JSON.stringify(["core-2", "qq-main", "bot-a", "account-b", "role-a"]);
  const picker = {value: selectedKey, closest(selector) { return selector === "[data-self-core-picker]" ? this : null; }};
  h.root.handlers.change({target: picker});
  await settle();
  assert.match(h.root.innerHTML, /核心用户/);
  assert.match(h.root.innerHTML, /core-2/);
  assert.match(h.root.innerHTML, /QQ · OneBot/);
  assert.match(h.root.innerHTML, /<b>角色<\/b>澄汐/);
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls[0])), {method: "GET", route: "acquaintance/settings", body: {
    subject_id: "core-2", platform: "qq-main", bot_id: "bot-a", account_id: "account-b", role_id: "role-a",
  }});

  const input = {value: "小屋主人", dataset: {selfRelationshipField: "default_nickname"},
    closest(selector) { return selector === "[data-self-relationship-field]" ? this : null; }};
  input.value = "阿澈";
  h.root.handlers.input({target: input});
  assert.equal(h.saveButton.disabled, false);
  const action = {dataset: {selfRelationshipAction: "save"},
    closest(selector) { return selector === "[data-self-relationship-action]" ? this : null; }};
  h.root.handlers.click({target: action});
  await settle();
  const saved = h.calls[1];
  assert.equal(saved.method, "POST");
  assert.equal(saved.route, "acquaintance/settings");
  assert.equal(saved.body.operation_key, "operation-1");
  assert.equal(saved.body.subject_id, "core-2");
  assert.deepEqual(JSON.parse(JSON.stringify(saved.body.route)), {platform: "qq-main", bot_id: "bot-a", account_id: "account-b", role_id: "role-a"});
  assert.deepEqual(JSON.parse(JSON.stringify(saved.body.sections.profile)), {edit_token: "profile-v1", changes: {default_nickname: "阿澈"}});
  assert.match(h.root.innerHTML, /关系资料已保存/);
});

test("私聊用户页编辑主动联系覆盖，只提交该用户实际修改的 contact 字段", async () => {
  const h = harness([person("core-1")]);
  h.mount(h.root);
  await settle();
  assert.match(h.root.innerHTML, /主动联系/);
  assert.match(h.root.innerHTML, /继承自全局默认/);

  const enabled = {type: "checkbox", checked: false, dataset: {selfContactField: "enabled"},
    closest(selector) { return selector === "[data-self-contact-field]" ? this : null; }};
  h.root.handlers.change({target: enabled});
  const note = {type: "textarea", value: "只對這位用戶放慢聯絡節奏", dataset: {selfContactField: "note"},
    closest(selector) { return selector === "[data-self-contact-field]" ? this : null; }};
  h.root.handlers.input({target: note});
  assert.equal(h.contactStatus.textContent, "2 项未保存");

  const action = {dataset: {selfRelationshipAction: "save-contact"},
    closest(selector) { return selector === "[data-self-relationship-action]" ? this : null; }};
  h.root.handlers.click({target: action});
  await settle();
  const saved = h.calls.at(-1);
  assert.equal(saved.method, "POST");
  assert.equal(saved.route, "acquaintance/settings");
  assert.deepEqual(JSON.parse(JSON.stringify(saved.body.sections)), {contact: {
    edit_token: "contact-v1", changes: {enabled: false, note: "只對這位用戶放慢聯絡節奏"},
  }});
  assert.match(h.root.innerHTML, /该私聊窗口的主动联系设置已保存/);
  assert.match(h.root.innerHTML, /此私聊单独设置/);
});

function pickerFor(subject, account = "account-a") {
  return {value: JSON.stringify([subject, "qq-main", "bot-a", account, "role-a"]),
    closest(selector) { return selector === "[data-self-core-picker]" ? this : null; }};
}
function editProfile(h, value) {
  h.root.handlers.input({target: {value, dataset: {selfRelationshipField: "default_nickname"},
    closest(selector) { return selector === "[data-self-relationship-field]" ? this : null; }}});
}
function editContact(h) {
  h.root.handlers.change({target: {type: "checkbox", checked: false, dataset: {selfContactField: "enabled"},
    closest(selector) { return selector === "[data-self-contact-field]" ? this : null; }}});
}

test("核心用户切换取消保持原对象和两处草稿，保存后才读取新对象", async () => {
  const h = harness([person("core-1"), person("core-2")]);
  h.mount(h.root);
  await settle();
  await h.root.handlers.change({target: pickerFor("core-1")});
  await settle();
  editProfile(h, "尚未保存的称呼");
  editContact(h);
  const picker = pickerFor("core-2");
  await h.root.handlers.change({target: picker});
  assert.equal(picker.value, pickerFor("core-1").value);
  assert.equal(h.calls.length, 1);
  assert.equal(h.editor.isDirty(), true);
  assert.deepEqual([...h.editor.changedFields()], ["对用户的称呼", "主动联系"]);
  h.mount(h.root);
  assert.match(h.root.innerHTML, /value="尚未保存的称呼"/);
  assert.match(h.root.innerHTML, /core-1的私聊/);
  h.behavior.choice = "save";
  await h.root.handlers.change({target: pickerFor("core-2")});
  await settle();
  assert.deepEqual(h.calls.map(call => [call.method, call.body.subject_id]),
    [["GET", "core-1"], ["POST", "core-1"], ["POST", "core-1"], ["GET", "core-2"]]);
  assert.equal(h.calls[1].body.sections.profile.changes.default_nickname, "尚未保存的称呼");
  assert.equal(h.calls[2].body.sections.contact.changes.enabled, false);
  assert.equal(h.editor.isDirty(), false);
  assert.match(h.root.innerHTML, /core-2的私聊/);
});

test("保存失败保留原对象，放弃后切换不会提交旧草稿", async () => {
  const h = harness([person("core-1"), person("core-2")]);
  h.mount(h.root);
  await settle();
  await h.root.handlers.change({target: pickerFor("core-1")});
  await settle();
  editProfile(h, "草稿");
  h.behavior.choice = "save";
  h.behavior.failPost = true;
  await h.root.handlers.change({target: pickerFor("core-2")});
  assert.equal(h.editor.isDirty(), true);
  assert.match(h.root.innerHTML, /value="草稿"/);
  assert.match(h.root.innerHTML, /core-1的私聊/);
  assert.equal(h.calls.at(-1).method, "POST");
  h.behavior.choice = "discard";
  await h.root.handlers.change({target: pickerFor("core-2")});
  await settle();
  assert.equal(h.editor.isDirty(), false);
  assert.equal(h.calls.at(-1).method, "GET");
  assert.equal(h.calls.at(-1).body.subject_id, "core-2");
});

test("重新读取和外部资料更新不能静默清除草稿", async () => {
  const h = harness([person("core-1")]);
  h.root.isConnected = true;
  h.mount(h.root);
  await settle();
  editProfile(h, "保留草稿");
  const action = {dataset: {selfRelationshipAction: "reload"},
    closest(selector) { return selector === "[data-self-relationship-action]" ? this : null; }};
  h.root.handlers.click({target: action});
  await settle();
  assert.equal(h.confirms.length, 1);
  assert.equal(h.calls.length, 1);
  h.listeners["companion:relationship-profile-updated"]();
  await settle();
  assert.equal(h.calls.length, 1);
  assert.equal(h.editor.isDirty(), true);
  h.behavior.choice = "discard";
  h.root.handlers.click({target: action});
  await settle();
  assert.equal(h.calls.length, 2);
  assert.equal(h.editor.isDirty(), false);
});

test("同一核心用户有多个可编辑账号时不默认取第一条", async () => {
  const h = harness([person("core-1"), person("core-1", {account_id: "account-b"})]);
  h.mount(h.root);
  await settle();
  assert.equal(h.calls.length, 0);
  assert.match(h.root.innerHTML, /选择要编辑的核心用户/);
});

test("旧角色异步读取不会覆盖新角色或重新填入已清空的选择", async () => {
  const h = harness([person("core-a"), person("core-b", {role_id: "role-b", bot_id: "bot-b"})]);
  let finishOld;
  h.behavior.get = body => body.subject_id === "core-a"
    ? new Promise(resolve => { finishOld = resolve; })
    : {sections: {profile: {...profile, settings: {...profile.settings, default_nickname: "新角色称呼"}}, contact}};
  h.mount(h.root);
  await settle();
  h.behavior.role = "role-b";
  h.behavior.bot = "bot-b";
  h.mount(h.root);
  await settle();
  finishOld({sections: {profile: {...profile, settings: {...profile.settings, default_nickname: "旧请求"}}, contact}});
  await settle();
  assert.match(h.root.innerHTML, /core-b的私聊/);
  assert.match(h.root.innerHTML, /新角色称呼/);
  assert.doesNotMatch(h.root.innerHTML, /旧请求|core-a的私聊/);

  let finishDirectory;
  h.behavior.directory = () => new Promise(resolve => { finishDirectory = resolve; });
  h.behavior.role = "role-a";
  h.behavior.bot = "bot-a";
  h.mount(h.root);
  h.behavior.role = null;
  h.mount(h.root);
  const before = h.calls.length;
  finishDirectory({persons: [person("core-a")]});
  await settle();
  assert.match(h.root.innerHTML, /尚未选择角色/);
  assert.equal(h.calls.length, before, "清空选择后不读取旧角色的成员资料");
});
