const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "../pages/陪伴小屋/role-profile-draft.js"), "utf8");
const testSource = source.replace(/return \{markup, open: openDialog, isDirty,?([^;]*)\};/,
  "return {markup, open: openDialog, isDirty,$1, apply, recoverPendingApply, restorePendingApply, storePendingApply, get state() { return state; }};");
assert.notEqual(testSource, source, "test seam must match the private module return");

function harness(sessionStore, outcomes, options = {}) {
  const calls = [], notices = [], events = [];
  const fields = options.fields || {};
  const fieldControl = selector => {
    const match = /^\[data-role-profile-draft-value="(.+)"\]$/.exec(selector);
    return match ? {value: String(fields[match[1]] ?? "")} : null;
  };
  const dialog = {open: true, querySelector: selector => fieldControl(selector), querySelectorAll: () => []};
  const noticeNode = {textContent: "", dataset: {}};
  /* 让 harness 能注册事件监听：判据要真的派发一次「外部已保存」事件。 */
  const listeners = {};
  const document = {
    querySelector(selector) {
      if (selector === "#roleProfileDraftDialog") return dialog;
      if (selector === "[data-role-profile-draft-notice]") return noticeNode;
      return fieldControl(selector);
    },
    querySelectorAll(selector) {
      if (selector === "[data-role-profile-draft-select]:checked") {
        return (options.selected || []).map(value => ({value}));
      }
      return [];
    },
    addEventListener(type, handler) {
      (listeners[type] = listeners[type] || []).push(handler);
    },
    dispatchEvent(event) {
      events.push(event.type);
      (listeners[event.type] || []).forEach(handler => handler(event));
    },
  };
  const window = {
    CompanionProduct: {
      identity: () => ({installation_id: "installation-a", operator_id: "operator-a"}),
      scopeKey: () => "target-a:role-a",
      role: () => ({config_api: "config"}),
      relative: value => value,
      request: async (method, endpoint, body) => {
        calls.push({method, endpoint, body: body && JSON.parse(JSON.stringify(body))});
        if (method === "GET") {
          if (options.section) return JSON.parse(JSON.stringify(options.section));
          throw new Error("status read unavailable");
        }
        return outcomes.shift()();
      },
    },
  };
  if (options.history) window.history = options.history;
  const context = {
    window, document,
    Event: class { constructor(type) { this.type = type; } },
    setTimeout, clearTimeout, Math, JSON, Object, Array, String, Number, Set, Map, Error, Promise,
  };
  context.sessionStorage = options.sessionStorage || {
    getItem: key => sessionStore.has(key) ? sessionStore.get(key) : null,
    setItem: (key, value) => sessionStore.set(key, String(value)),
    removeItem: key => sessionStore.delete(key),
  };
  if (options.localStorage) context.localStorage = options.localStorage;
  vm.runInNewContext(testSource, context);
  return {module: window.CompanionRoleProfileDraft, calls, notices, events, noticeNode, document};
}

test("an unknown role-profile save survives reload and confirms by replaying the same request", async () => {
  const sessionStore = new Map();
  const pending = {target: "role_profile", scopeKey: "target-a:role-a", endpoint: "config/configure",
    skipped_count: 0, body: {section: "role_profile", operation_key: "stable-operation",
      edit_token: "e".repeat(64), changes: {name: "小伴"}}};
  const first = harness(sessionStore, [() => Promise.reject(Object.assign(new Error("timeout"), {code: "bridge_timeout"}))]);
  first.module.state.target = pending.target;
  first.module.state.scopeKey = pending.scopeKey;
  first.module.state.pendingApply = pending;
  assert.equal(first.module.storePendingApply(pending), true);

  await first.module.recoverPendingApply();
  assert.deepEqual(first.calls[0], {method: "POST", endpoint: pending.endpoint, body: pending.body});
  assert.equal(first.module.state.pendingApply.body.operation_key, "stable-operation");
  assert.match(first.noticeNode.textContent, /保存结果尚未确认/);

  const second = harness(sessionStore, [() => Promise.resolve({result: {
    operation_id: "stored-operation", replayed: true,
  }})]);
  second.module.state.target = pending.target;
  second.module.state.pendingApply = second.module.restorePendingApply(
    pending.target, pending.scopeKey, pending.endpoint);
  assert.equal(second.module.state.pendingApply.body.operation_key, "stable-operation");
  await second.module.recoverPendingApply();

  const posts = second.calls.filter(call => call.method === "POST");
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].body, pending.body);
  assert.equal(second.module.state.pendingApply, null);
  assert.equal(sessionStore.size, 0);
  assert.match(second.noticeNode.textContent, /原保存已确认/);
  assert.match(second.noticeNode.textContent, /未能刷新/);
  assert.doesNotMatch(second.noticeNode.textContent, /保存结果尚未确认/);
  assert.deepEqual(second.events, ["companion:role-profile-updated"]);
});

test("a confirmed role-profile rejection clears the recoverable request", async () => {
  const sessionStore = new Map();
  const pending = {target: "role_profile", scopeKey: "target-a:role-a", endpoint: "config/configure",
    body: {section: "role_profile", operation_key: "rejected-operation",
      edit_token: "f".repeat(64), changes: {name: "小伴"}}};
  const app = harness(sessionStore, [() => Promise.reject(Object.assign(new Error("revision changed"), {
    code: "configuration_revision_conflict", confirmed: true,
  }))]);
  app.module.state.target = pending.target;
  app.module.state.scopeKey = pending.scopeKey;
  app.module.state.pendingApply = pending;
  app.module.storePendingApply(pending);

  await app.module.recoverPendingApply();
  assert.equal(app.module.state.pendingApply, null);
  assert.equal(sessionStore.size, 0);
  assert.match(app.noticeNode.textContent, /刚刚更新过/);
});

test("role-profile draft exposes an explicit text model selector and sends it", async () => {
  assert.match(source, /data-role-profile-draft-provider/);
  assert.match(source, /整理模型/);
  assert.match(source, /默认模型|当前聊天模型|default_provider_id/);
  assert.match(source, /provider_id: providerId \|\| ""/);
});

test("a blocked Web Storage save still posts once and remains recoverable", async () => {
  const blocked = {
    getItem() { throw new Error("SecurityError"); },
    setItem() { throw new Error("SecurityError"); },
    removeItem() { throw new Error("SecurityError"); },
  };
  const history = {state: {kept: "dashboard"}, replaceState(next) { this.state = next; }};
  const section = {sections: {role_profile: {settings: {name: ""}, edit_token: "e".repeat(64)}}};
  const first = harness(new Map(), [
    () => Promise.reject(Object.assign(new Error("timeout"), {code: "bridge_timeout"})),
  ], {
    sessionStorage: blocked, localStorage: blocked, history, section,
    fields: {name: "Xiao Ban"}, selected: ["name"],
  });
  first.module.state.target = "role_profile";
  first.module.state.scopeKey = "target-a:role-a";
  first.module.state.draft = {fields: {name: "Xiao Ban"}};
  first.module.state.selectedFields = new Set(["name"]);
  first.module.state.limits = {name: 100};
  first.module.state.totalLimit = 30000;
  first.module.state.current = {settings: {name: ""}};

  await first.module.apply("empty");
  const firstPosts = first.calls.filter(call => call.method === "POST");
  assert.equal(firstPosts.length, 1);
  assert.equal(firstPosts[0].body.changes.name, "Xiao Ban");
  assert.ok(first.module.state.pendingApply, "the in-memory request must survive an unknown result");
  assert.equal(first.module.state.pendingApply.body.operation_key, firstPosts[0].body.operation_key);
  assert.doesNotMatch(first.noticeNode.textContent, /当前浏览器无法记录|尚未提交/);
  assert.match(first.noticeNode.textContent, /保存结果尚未确认/);

  const stored = history.state.__companion_role_profile_apply_pending_v1;
  assert.equal(typeof stored, "object");
  const entries = Object.values(stored);
  assert.equal(entries.length, 1);
  assert.equal(JSON.parse(entries[0]).body.operation_key, firstPosts[0].body.operation_key);

  const second = harness(new Map(), [
    () => Promise.resolve({result: {operation_id: "stored-operation", replayed: true}}),
  ], {sessionStorage: blocked, localStorage: blocked, history, section});
  const restored = second.module.restorePendingApply("role_profile", "target-a:role-a", "config/configure");
  assert.ok(restored, "history.state must restore the unknown request after reload");
  assert.equal(restored.body.operation_key, firstPosts[0].body.operation_key);
  second.module.state.target = "role_profile";
  second.module.state.scopeKey = "target-a:role-a";
  second.module.state.pendingApply = restored;
  await second.module.recoverPendingApply();

  const secondPosts = second.calls.filter(call => call.method === "POST");
  assert.equal(secondPosts.length, 1);
  assert.equal(second.module.state.pendingApply, null);
  assert.equal(history.state.kept, "dashboard");
  assert.equal(history.state.__companion_role_profile_apply_pending_v1, undefined);
  assert.match(second.noticeNode.textContent, /原保存已确认/);
});

test("role-profile apply never gates submission on pending persistence", () => {
  assert.doesNotMatch(source, /if \(!storePendingApply\(pending\)\) \{\s*state\.pendingApply = null/);
  assert.match(source, /pendingApplyMemory/);
  assert.match(source, /writeHistoryPendingApply/);
});

/* 🔴 情绪 2026-11-02 报障：「自我」界面已经保存了，但切换到别的界面就提示
 * 「还有未保存的改动」。
 *
 * 根因：`isDirty()` 比的是 `fieldValues` 与 `draft.fields`，而草稿一旦生成就
 * 一直留在 state 里、**从不随外部保存更新**。用户在「基础身份」保存之后，
 * 草稿这边仍拿生成时的旧基线比对 ⇒ 恒为脏 ⇒ 拦下所有换页。
 *
 * 这条判据必须验「保存后真的不再算脏」。只验 `isDirty` 存在是抓不住的 ——
 * 它在 bug 状态下也存在，只是永远返回 true。 */
test("an external profile save clears the stale draft so leaving stops warning", () => {
  const h = harness(new Map(), []);
  const state = h.module.state;

  /* 造出「草稿已生成、用户改过某个字段」的状态。 */
  state.target = "role_profile";
  state.draft = {fields: {name: "旧名字", birthday: "2000-11-26"}};
  state.fieldValues = {name: "新名字"};
  state.selectedFields = new Set(["name"]);

  assert.equal(h.module.isDirty(), true, "改过且未保存时应当是脏的（前置条件）");
  assert.deepEqual(h.module.changedFields(), ["name"]);

  /* 主编辑页保存成功 —— 工作台会派发这个事件。 */
  h.document.dispatchEvent({type: "companion:role-profile-updated"});

  assert.equal(h.module.isDirty(), false,
    "外部保存后草稿必须不再算脏，否则每次换页都会误报未保存");
  assert.deepEqual(h.module.changedFields(), []);
});

/* 反向保护：不相关的事件不能顺手把草稿清掉，否则用户真的会丢掉未保存的编辑。 */
test("an unrelated event must not discard the draft", () => {
  const h = harness(new Map(), []);
  const state = h.module.state;
  state.target = "role_profile";
  state.draft = {fields: {name: "旧名字"}};
  state.fieldValues = {name: "新名字"};

  h.document.dispatchEvent({type: "companion:relationship-profile-updated"});
  assert.equal(h.module.isDirty(), true, "关系资料的事件不能清掉角色草稿");

  h.document.dispatchEvent({type: "companion:something-else"});
  assert.equal(h.module.isDirty(), true, "无关事件不能影响草稿");
});
