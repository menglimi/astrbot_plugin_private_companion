"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "..", "pages", "关系与权限", "member-editor.js"), "utf8");
const slice = source.slice(source.indexOf("  function accountLinksPane("), source.indexOf("  function render()"));

class Element {
  constructor(tag, text, cls) {this.tag = tag; this.text = text; this.className = cls; this.children = []; this.events = {};}
  append(...items) {this.children.push(...items);}
  addEventListener(name, handler) {this.events[name] = handler;}
  setAttribute(name, value) {this[name] = value;}
}
const route = suffix => ({platform: "qq-" + suffix, bot_id: "bot-" + suffix, account_id: "login-" + suffix, role_id: "role-a"});
const person = {subject_id: "user-a", ...route("a")};
const candidates = ["a", "b"].map(s => ({subject_id: "user-" + s, route: route(s), label: "Account " + s}));
const member = candidate => ({subject_id: candidate.subject_id, ...candidate.route, native_account_id: candidate.label});
function fixture({group = null, failPost = false, confirm = true, features = false, memory = false, hostConfirm = true} = {}) {
  let counter = 0;
  const jobs = [], posts = [], confirmations = [], state = {};
  const data = {revision: 0, group, candidates, ...(features ? {
    supported: {settings: true, identity: true, proactive: true},
    fields: {profile: ["relationship.profile.default_nickname", "relationship.profile.extra"],
      reply: ["conversation.presentation.style_prompt"], contact: ["proactive.contact.enabled"]}} : {}), ...(memory ? {supported: {memory: true}} : {})};
  const bridge = {apiGet: async () => ({status: "ok", data}), apiPost: async (url, body) => {
    posts.push({url, body: structuredClone(body)});
    if (failPost) throw new Error("ambiguous timeout");
    return {status: "ok", data: {revision: 1, group}};
  }};
  if (hostConfirm) bridge.confirm = async options => {
    confirmations.push(structuredClone(options));
    return typeof confirm === "function" ? confirm(options) : confirm;
  };
  const context = vm.createContext({ROOT: "acquaintance", current: {services: {bridge}},
    element: (tag, text, cls) => new Element(tag, text, cls), queueMicrotask: fn => jobs.push(fn),
    routeKey: p => JSON.stringify([p.platform, p.bot_id, p.account_id, p.role_id]),
    parameters: p => ({subject_id: p.subject_id, ...route("a")}), routeOf: p => route("a"),
    operation: () => "operation-" + (++counter), redraw: () => {}, errorText: (_, fallback) => fallback,
    window: {confirm: () => {throw new Error("native confirm blocked by sandbox");}}, unwrap: envelope => envelope.data,
  });
  vm.runInContext(slice + "\nthis.pane = accountLinksPane;", context);
  return {state, posts, bridge, data, confirmations, async render() {
    context.pane(state, person);
    while (jobs.length) await jobs.shift()();
    return context.pane(state, person);
  }};
}
function all(root) {return [root, ...root.children.flatMap(item => item instanceof Element ? all(item) : [])];}
function button(root, label) {return all(root).find(item => item.tag === "button" && item.text === label);}
const settle = async () => {for (let i = 0; i < 3; ++i) await new Promise(resolve => setImmediate(resolve));};

test("opening the pane is read-only and context sharing is opt-in", async () => {
  const f = fixture(); const pane = await f.render();
  const inputs = all(pane).filter(e => e.tag === "input");
  assert.equal(inputs.length, 3); assert.equal(inputs[0].disabled, true);
  assert.equal(inputs[2].checked, false); assert.equal(f.posts.length, 0);
  assert.equal(f.state.accountLinks.shareIdentity, false);
});

test("save requires another explicitly selected private account", async () => {
  const f = fixture(); const pane = await f.render();
  button(pane, "保存账号关联与共享开关").events.click(); await settle();
  assert.equal(f.posts.length, 0); assert.ok(f.state.accountLinks.error.includes("至少一个"));
});

test("save sends one complete route per member and does not flatten the route", async () => {
  const f = fixture(); const pane = await f.render();
  const inputs = all(pane).filter(e => e.tag === "input");
  inputs[1].checked = true; inputs[1].events.change(); inputs[2].checked = true; inputs[2].events.change();
  button(pane, "保存账号关联与共享开关").events.click(); await settle();
  assert.equal(f.posts.length, 1);
  const body = f.posts[0].body;
  assert.equal(body.members.length, 2); assert.equal(body.share_context, true);
  assert.equal(body.subject_id, "user-a"); assert.equal(body.platform, undefined);
  assert.deepEqual(Object.keys(body).sort(), ["expected_revision", "members", "operation_key", "route", "share_context", "subject_id"]);
});

test("an ambiguous save locks the draft and retries with the identical operation", async () => {
  const f = fixture({failPost: true}); let pane = await f.render();
  const input = all(pane).filter(e => e.tag === "input")[1]; input.checked = true; input.events.change();
  button(pane, "保存账号关联与共享开关").events.click(); await settle();
  assert.ok(f.state.accountLinks.pending);
  pane = await f.render();
  assert.equal(all(pane).find(e => e.tag === "fieldset").disabled, true);
  button(pane, "重试原操作（不重复创建）").events.click(); await settle();
  assert.equal(f.posts.length, 2); assert.deepEqual(f.posts[0], f.posts[1]);
});

test("unlink needs confirmation and does not remove chat data", async () => {
  const group = {share_context: true, members: candidates.map(member)};
  const f = fixture({group, confirm: false}); const pane = await f.render();
  button(pane, "解除整组关联").events.click(); await settle(); assert.equal(f.posts.length, 0);
});


test("all sharing categories default off and advanced fields are collapsed", async () => {
  const f = fixture({features: true}); const pane = await f.render();
  assert.equal(f.state.accountLinks.shareIdentity, false);
  for (const values of Object.values(f.state.accountLinks.sharing)) assert.equal(values.size, 0);
  assert.ok(all(pane).filter(e => e.tag === "details").every(e => e.open === false));
  assert.equal(f.posts.length, 0);
});

test("new shared fields require a chosen seed account", async () => {
  const f = fixture({features: true}); let pane = await f.render();
  f.state.accountLinks.selected.add(JSON.stringify([candidates[1].subject_id, JSON.stringify(Object.values(candidates[1].route))]));
  f.state.accountLinks.sharing.profile.add("relationship.profile.default_nickname");
  button(pane, "保存账号关联与共享开关").events.click(); await settle();
  assert.equal(f.posts.length, 0); assert.ok(f.state.accountLinks.error.includes("初始来源"));
  const seed = all(pane).find(e => e.tag === "select" && e["aria-label"] === "共享初始来源账号");
  seed.value = JSON.stringify([candidates[0].subject_id, JSON.stringify(Object.values(candidates[0].route))]); seed.events.change();
  button(pane, "保存账号关联与共享开关").events.click(); await settle();
  assert.equal(f.posts.length, 1);
  assert.deepEqual(f.posts[0].body.sharing.profile, ["relationship.profile.default_nickname"]);
  assert.deepEqual(f.posts[0].body.seed, {subject_id: candidates[0].subject_id, route: candidates[0].route});
});

test("identity sharing has its own confirmation and cannot be enabled by category toggles", async () => {
  const f = fixture({features: true, confirm: false}); const pane = await f.render();
  f.state.accountLinks.selected.add(JSON.stringify([candidates[1].subject_id, JSON.stringify(Object.values(candidates[1].route))]));
  f.state.accountLinks.shareIdentity = true;
  f.state.accountLinks.seedKey = JSON.stringify([candidates[0].subject_id, JSON.stringify(Object.values(candidates[0].route))]);
  button(pane, "保存账号关联与共享开关").events.click(); await settle();
  assert.equal(f.posts.length, 0);
  f.state.accountLinks.shareIdentity = false;
  button(pane, "保存账号关联与共享开关").events.click(); await settle();
  assert.equal(f.posts.length, 1); assert.equal(f.posts[0].body.identity.enabled, false);
});


test("account links reuse editor buttons, switches and labeled select fields", async () => {
  const f = fixture({features: true}); const pane = await f.render();
  assert.equal(button(pane, "重新读取关联账号").className, "secondary");
  assert.equal(button(pane, "保存账号关联与共享开关").className, "member-primary");
  const nodes = all(pane);
  assert.equal(nodes.filter(e => e.tag === "fieldset").length, 4);
  assert.ok(nodes.filter(e => e.tag === "fieldset").every(e => e.className === "member-link-section"));
  assert.equal(nodes.filter(e => e.tag === "label" && e.className?.includes("member-switch-row")).length, 5);
  for (const select of nodes.filter(e => e.tag === "select")) {
    const label = nodes.find(e => e.tag === "label" && e.children.includes(select));
    assert.ok(label?.className.includes("member-field"));
    assert.ok(select["aria-label"]);
  }
});

test("unlink stays a distinct secondary action in the shared save bar", async () => {
  const f = fixture({group: {share_context: false, members: candidates.map(member)}});
  const pane = await f.render();
  const savebar = all(pane).find(e => e.className === "member-savebar member-link-actions");
  assert.ok(savebar.children.includes(button(pane, "保存账号关联与共享开关")));
  assert.ok(savebar.children.includes(button(pane, "解除整组关联")));
  assert.equal(button(pane, "解除整组关联").className, "secondary member-link-unlink");
});

test("account link styles inherit theme tokens instead of a separate fixed palette", () => {
  const css = fs.readFileSync(path.join(__dirname, "..", "pages", "关系与权限", "style.css"), "utf8");
  const links = css.slice(css.indexOf("/* Account links reuse"));
  assert.ok(links.includes("var(--line)"));
  assert.ok(links.includes("var(--surface-soft)"));
  assert.ok(links.includes("var(--teal-soft)"));
  assert.ok(!links.includes("var(--border"));
  assert.ok(!links.includes("var(--surface-alt"));
  assert.ok(!/#[0-9a-f]{3,8}\b/i.test(links));
});


function keyFor(candidate) {return JSON.stringify([candidate.subject_id, JSON.stringify(Object.values(candidate.route))]);}
async function randomFixture() {
  const f = fixture({features: true}); await f.render();
  candidates.forEach(item => f.state.accountLinks.selected.add(keyFor(item)));
  f.state.accountLinks.proactiveMode = "random";
  return f;
}
test("random mode exposes default equal weights only for selected accounts", async () => {
  const f = await randomFixture(); const pane = await f.render();
  const weights = all(pane).filter(e => e.tag === "input" && e.type === "number");
  assert.equal(weights.length, 2); assert.ok(weights.every(e => e.value === "1"));
  assert.ok(all(pane).some(e => e.text === "参考占比 50.0%"));
  f.state.accountLinks.selected.delete(keyFor(candidates[1]));
  assert.equal(all(await f.render()).filter(e => e.tag === "input" && e.type === "number").length, 1);
});
test("random weights submit complete member routes and proportional weights", async () => {
  const f = await randomFixture(); const pane = await f.render();
  const weights = all(pane).filter(e => e.tag === "input" && e.type === "number");
  weights[0].value = "3"; weights[0].events.change();
  button(pane, "保存账号关联与共享开关").events.click(); await settle();
  assert.equal(f.posts.length, 1);
  assert.deepEqual(f.posts[0].body.proactive.weights.map(e => e.weight), [3, 1]);
  assert.equal(f.posts[0].body.proactive.weights[0].member.subject_id, candidates[0].subject_id);
  assert.equal(f.posts[0].body.proactive.weights[0].member.bot_id, candidates[0].route.bot_id);
});
test("invalid and all-zero random weights block submission", async () => {
  for (const value of ["", "-1", "1001", "1.5", "0"]) {
    const f = await randomFixture(); const pane = await f.render();
    const weights = all(pane).filter(e => e.tag === "input" && e.type === "number");
    weights[0].value = value; weights[0].events.change();
    weights[1].value = "0"; weights[1].events.change();
    button(pane, "保存账号关联与共享开关").events.click(); await settle();
    assert.equal(f.posts.length, 0); assert.ok(f.state.accountLinks.error.includes("随机权重"));
  }
});
test("saved random weights are restored when reopening", async () => {
  const f = fixture({features: true, group: {share_context: false, members: candidates.map(member),
    proactive: {mode: "random", preferred: null, weights: [
      {member: member(candidates[0]), weight: 0}, {member: member(candidates[1]), weight: 8}]}}});
  const pane = await f.render();
  assert.deepEqual(all(pane).filter(e => e.tag === "input" && e.type === "number").map(e => e.value), ["0", "8"]);
});

async function memoryFixture(options = {}) {
  const f = fixture({memory: true, ...options}); const pane = await f.render();
  const account = all(pane).filter(e => e.tag === "input")[1];
  account.checked = true; account.events.change();
  return f;
}
test("long-term memory is a separate opt-in with labeled existing controls", async () => {
  const f = await memoryFixture(); const pane = await f.render();
  assert.equal(f.state.accountLinks.shareMemory, false);
  assert.equal(f.state.accountLinks.memoryHistory, "since_enabled");
  const select = all(pane).find(e => e.tag === "select");
  assert.equal(select.disabled, true); assert.equal(select["aria-label"], "长期记忆共享范围");
  const label = all(pane).find(e => e.tag === "label" && e.children.includes(select));
  assert.ok(label.className.includes("member-field"));
  button(pane, "保存账号关联与共享开关").events.click(); await settle();
  assert.deepEqual(f.posts[0].body.memory, {enabled: false, history: "since_enabled"});
  assert.equal(f.posts[0].body.share_context, false);
});
test("new memory sharing and historical sharing use distinct confirmations", async () => {
  for (const history of ["since_enabled", "all"]) {
    const f = await memoryFixture(); f.state.accountLinks.shareMemory = true;
    f.state.accountLinks.memoryHistory = history;
    const pane = await f.render();
    button(pane, "保存账号关联与共享开关").events.click(); await settle();
    assert.equal(f.posts[0].body.memory.confirmation, history === "all"
      ? "include-existing-linked-memory" : "share-linked-long-term-memory");
  }
});
test("declining memory confirmation sends no mutation", async () => {
  const f = await memoryFixture({confirm: false}); f.state.accountLinks.shareMemory = true;
  const pane = await f.render(); button(pane, "保存账号关联与共享开关").events.click(); await settle();
  assert.equal(f.posts.length, 0);
});
test("saved memory range is restored independently of chat context", async () => {
  const f = await memoryFixture({group: {members: candidates.map(member), share_context: false,
    memory: {enabled: true, history: "all"}}});
  assert.equal(f.state.accountLinks.shareMemory, true); assert.equal(f.state.accountLinks.memoryHistory, "all");
  assert.equal(f.state.accountLinks.shareContext, false);
});


test("sandboxed memory save uses the host dialog for both ranges, never native confirm", async () => {
  for (const history of ["since_enabled", "all"]) {
    const f = await memoryFixture();
    f.state.accountLinks.shareMemory = true; f.state.accountLinks.memoryHistory = history;
    const pane = await f.render(); button(pane, "保存账号关联与共享开关").events.click(); await settle();
    assert.equal(f.confirmations.length, 1);
    assert.equal(f.confirmations[0].title, history === "all" ? "共享已有历史长期记忆" : "共享开启后的长期记忆");
    assert.equal(f.posts.length, 1);
    assert.equal(f.posts[0].body.memory.confirmation, history === "all"
      ? "include-existing-linked-memory" : "share-linked-long-term-memory");
    assert.equal(f.state.accountLinks.busy, false);
  }
});

test("waiting for host consent locks editing and blocks duplicate saves", async () => {
  let answer;
  const f = await memoryFixture({confirm: () => new Promise(resolve => {answer = resolve;})});
  f.state.accountLinks.shareMemory = true;
  let pane = await f.render(); const save = button(pane, "保存账号关联与共享开关");
  save.events.click(); save.events.click(); await settle();
  assert.equal(f.confirmations.length, 1); assert.equal(f.posts.length, 0);
  pane = await f.render(); assert.equal(button(pane, "保存账号关联与共享开关").disabled, true);
  assert.ok(all(pane).filter(e => e.tag === "fieldset").every(e => e.disabled));
  answer(true); await settle(); assert.equal(f.posts.length, 1); assert.equal(f.state.accountLinks.busy, false);
});

test("unavailable host confirmation falls back to explicit inline consent, not silent cancellation", async () => {
  for (const options of [{hostConfirm: false}, {confirm: null}, {confirm: () => {throw new Error("host unavailable");}}]) {
    const f = await memoryFixture(options); f.state.accountLinks.shareMemory = true;
    let pane = await f.render(); button(pane, "保存账号关联与共享开关").events.click(); await settle();
    assert.equal(f.posts.length, 0); assert.ok(f.state.accountLinks.confirmation);
    pane = await f.render();
    const prompt = all(pane).find(e => e["aria-label"] === "共享开启后的长期记忆");
    assert.ok(prompt); assert.equal(prompt.className, "member-link-section");
    button(prompt, "确认共享").events.click(); button(prompt, "确认共享").events.click(); await settle();
    assert.equal(f.posts.length, 1); assert.equal(f.state.accountLinks.confirmation, null);
    assert.equal(f.state.accountLinks.busy, false);
  }
});

test("inline cancellation sends nothing, unlocks the draft and allows a later save", async () => {
  const f = await memoryFixture({hostConfirm: false}); f.state.accountLinks.shareMemory = true;
  let pane = await f.render(); button(pane, "保存账号关联与共享开关").events.click(); await settle();
  pane = await f.render(); button(pane, "取消").events.click(); await settle();
  assert.equal(f.posts.length, 0); assert.equal(f.state.accountLinks.busy, false);
  assert.match(f.state.accountLinks.notice, /已取消/);
  pane = await f.render(); button(pane, "保存账号关联与共享开关").events.click(); await settle();
  pane = await f.render(); button(pane, "确认共享").events.click(); await settle();
  assert.equal(f.posts.length, 1);
});

test("identity and unlink consent also use the host confirmation bridge in a sandbox", async () => {
  const f = fixture({features: true}); let pane = await f.render();
  f.state.accountLinks.selected.add(keyFor(candidates[1])); f.state.accountLinks.shareIdentity = true;
  f.state.accountLinks.seedKey = keyFor(candidates[0]);
  button(pane, "保存账号关联与共享开关").events.click(); await settle();
  assert.equal(f.confirmations[0].title, "共享身份与权限");
  assert.equal(f.posts[0].body.identity.confirmation, "share-private-identity-and-permissions");
  const g = fixture({group: {members: candidates.map(member)}}); pane = await g.render();
  button(pane, "解除整组关联").events.click(); await settle();
  assert.equal(g.confirmations[0].title, "解除整组账号关联"); assert.equal(g.posts.length, 1);
  assert.ok(g.posts[0].url.endsWith("/unlink"));
});

test("retrying an ambiguous confirmed memory save reuses its consent and operation key", async () => {
  const f = await memoryFixture({failPost: true}); f.state.accountLinks.shareMemory = true;
  let pane = await f.render(); button(pane, "保存账号关联与共享开关").events.click(); await settle();
  assert.equal(f.posts.length, 1); assert.equal(f.confirmations.length, 1);
  pane = await f.render(); button(pane, "重试原操作（不重复创建）").events.click(); await settle();
  assert.equal(f.posts.length, 2); assert.equal(f.confirmations.length, 1);
  assert.deepEqual(f.posts[0], f.posts[1]);
});
