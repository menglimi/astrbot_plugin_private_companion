"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "../pages/陪伴小屋/game-access.js"), "utf8");
const settle = async () => {
  for (let i = 0; i < 8; i += 1) await new Promise(resolve => setImmediate(resolve));
};
const status = (token, consented = false, manage = true) => ({edit_token:token, can_manage:manage,
  games:[{game_type:"gomoku", consented, invokable:consented}]});

function harness(request, single = false) {
  let selected = "first", sequence = 0;
  const calls = [];
  const product = {
    hosted: () => true, scopeKey: () => selected, scopeLabel: () => selected,
    selection: () => ({target_id:selected,role_id:"star"}),
    targets: () => [
      {target_id:"first",scope_kind:"private",participant_id:"1805987832",roles:[{role_id:"star"}]},
      {target_id:"second",scope_kind:"private",participant_id:"995051631",roles:[{role_id:"star"}]},
      {target_id:"other-role",scope_kind:"private",participant_id:"100",roles:[{role_id:"other"}]},
      {target_id:"group",scope_kind:"group",group_id:"200",roles:[{role_id:"star"}]},
    ].filter(item => !single || item.target_id !== "second"),
    target: () => product.targets().find(item => item.target_id === selected),
    targetLabel: target => `私聊 ${target.participant_id}`,
    accountLabel: () => "QQ 3491542998",
    choose: async (id, role, quiet) => {
      assert.equal(role, "star"); assert.equal(quiet, true); selected = id;
    },
    role: () => ({game_access_api:`/plugin/${selected}/game-access`}),
    relative: value => value,
    async request(method, route, body) {
      calls.push({method, route, body});
      return request(method, route, body);
    },
  };
  const window = {CompanionProduct:product};
  vm.runInNewContext(source, {window, Promise, Map, String, crypto:{randomUUID:() => String(++sequence)}});
  function node() {
    return {innerHTML:"", isConnected:true, listeners:{},
      addEventListener(name, fn) { this.listeners[name] = fn; },
      removeEventListener(name) { delete this.listeners[name]; }};
  }
  return {access:window.CompanionGameAccess, calls, node,
    select: value => { selected = value; },
    change: (root, value) => root.listeners.change({target:{value,matches:() => true}}),
    click: (root, kind) => root.listeners.click({target:{closest:() => ({dataset:{gameAccess:kind}})}})};
}

test("只读账号不提供或执行游戏授权", async () => {
  const h = harness(async () => status("read-only", false, false));
  const root = h.node();
  h.access.mount(root); await settle();
  assert.match(root.innerHTML, /当前私聊游戏权限/);
  assert.doesNotMatch(root.innerHTML, /data-game-access="gomoku"/);
  h.click(root, "gomoku"); await settle();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].method, "GET");
});

test("私聊选择展示目标号码并仅向所选窗口保存授权", async () => {
  const h = harness(async (method, route) => method === "GET" ? status(route)
    : {status:status(route, true)});
  const root = h.node(); h.access.mount(root); await settle();
  assert.match(root.innerHTML, /QQ 3491542998 · 私聊 1805987832/);
  assert.match(root.innerHTML, /value="second"[^>]*>QQ 3491542998 · 私聊 995051631/);
  assert.doesNotMatch(root.innerHTML, /value="other-role"|value="group"/);
  await h.change(root, "second"); await settle();
  assert.match(root.innerHTML, /value="second" selected/);
  h.click(root, "gomoku"); await settle();
  const posts = h.calls.filter(call => call.method === "POST");
  assert.equal(posts.length, 1);
  assert.equal(posts[0].route, "/plugin/second/game-access/configure");
  assert.equal(posts[0].body.edit_token, "/plugin/second/game-access");
  h.access.unmount();
  assert.equal(root.listeners.change, undefined);
});

test("结果未知时核对同一操作，旧私聊的回执不覆盖新窗口", async () => {
  let attempts = 0, finish;
  const h = harness(async (method, route) => {
    if (method === "GET") return status(route.includes("second") ? "second-token" : "first-token");
    if (++attempts === 1) throw {code:"network_error"};
    return new Promise(resolve => { finish = resolve; });
  });
  const first = h.node(); h.access.mount(first); await settle();
  h.click(first, "gomoku"); await settle();
  assert.match(first.innerHTML, /核对五子棋原请求/);
  h.click(first, "gomoku"); await settle();
  const posts = h.calls.filter(call => call.method === "POST");
  assert.equal(posts.length, 2);
  assert.equal(posts[0].body, posts[1].body);
  h.select("second"); const second = h.node(); h.access.mount(second); await settle();
  finish({status:status("first-enabled", true)}); await settle();
  assert.match(second.innerHTML, /允许五子棋/);
  assert.doesNotMatch(second.innerHTML, /已允许|撤回五子棋/);
  assert.equal(first.listeners.click, undefined);
  h.access.unmount();
  assert.equal(second.listeners.click, undefined);
});

test("失效的授权快照会自动更新再保存", async () => {
  let reads = 0, posts = 0;
  const h = harness(async (method) => {
    if (method === "GET") return status(++reads === 1 ? "old-token" : "fresh-token");
    if (++posts === 1) throw {code:"game_access_changed"};
    return {status:status("saved-token", true)};
  });
  const root = h.node(); h.access.mount(root); await settle();
  h.click(root, "gomoku"); await settle();
  assert.equal(reads, 2);
  h.click(root, "gomoku"); await settle();
  const commands = h.calls.filter(call => call.method === "POST").map(call => call.body);
  assert.equal(commands[1].edit_token, "fresh-token");
  assert.notEqual(commands[0].operation_key, commands[1].operation_key);
  assert.match(root.innerHTML, /撤回五子棋/);
});

test("只有一个私聊时也提供明确选择入口，不自动授权", async () => {
  const h = harness(async () => status("token"), true);
  h.select("group");
  const root = h.node(); h.access.mount(root); await settle();
  assert.match(root.innerHTML, /data-game-access-target/);
  assert.match(root.innerHTML, /请选择私聊窗口/);
  await h.change(root, "first"); await settle();
  assert.match(root.innerHTML, /value="first" selected/);
  assert.equal(h.calls.filter(call => call.method === "POST").length, 0);
  h.access.unmount();
});
