"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/host-options-page.js"), "utf8");
const styles = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/host-options.css"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));
const drain = async (count = 8) => {for (let index = 0; index < count; index += 1) await flush();};

function harness(rootId = "forwardIntakeWorkspace") {
  const calls = [], handlers = {};
  let snapshot = {
    voice_reply: {enabled: false, probability: 1, private_probability: -1, group_probability: -1},
    poke: {enabled: false, trigger_probability: 0.25, back_probability: 0, back_times: 1,
      model_reply_enabled: false, model_reply_probability: 0.3, replies: ["我在"]},
    forward_intake: {max_forwards: 4, max_nodes: 32, max_depth: 4, parse_nested: true},
    forward_bounds: {max_forwards: 4, max_nodes: 300, max_depth: 4},
    edit_tokens: {voice: "v".repeat(64), poke: "p".repeat(64), forward_intake: "f".repeat(64)},
    apply_modes: {voice: "restart_host", poke: "immediate", forward_intake: "immediate_next_input"},
  };
  const bridge = {
    async apiGet(route) {calls.push({method: "GET", route});return {status: "ok", data: snapshot};},
    async apiPost(route, body) {
      calls.push({method: "POST", route, body});
      snapshot = route === "host-options/poke"
        ? {...snapshot, poke: {...snapshot.poke, ...body.changes}}
        : {...snapshot, forward_intake: {...snapshot.forward_intake, ...body.changes}};
      return {status: "ok", data: {...snapshot, saved: true,
        apply_mode: route === "host-options/poke" ? "immediate" : "immediate_next_input"}};
    },
  };
  const window = {CompanionProduct: {bridgeNow: () => bridge}};
  const context = vm.createContext({window, setTimeout, clearTimeout});
  vm.runInContext(source, context);
  const root = {id: rootId, dataset: {}, innerHTML: "",
    addEventListener(type, handler) {handlers[type] = handler;},
    removeEventListener(type) {delete handlers[type];}};
  return {page: window.CompanionHostOptionsPage, root, handlers, calls};
}

test("host option rows adapt to a narrow panel even in a wide viewport", () => {
  assert.match(styles, /container-name:\s*host-options/);
  const narrowRule = styles.match(/@container\s+host-options\s*\(max-width:\s*560px\)\s*\{([\s\S]*?)\n\}/)?.[1] || "";
  assert.match(narrowRule, /\.host-options-row/);
  assert.match(narrowRule, /grid-template-columns:\s*minmax\(0,\s*1fr\)/);
});

test("forward intake page renders bounded controls, saves all values and exposes the search owner", async () => {
  const h = harness();
  h.page.mount({forwardRoot: h.root});
  await drain();
  assert.match(h.root.innerHTML, /name="max_forwards" min="1" max="4"/);
  assert.match(h.root.innerHTML, /name="max_nodes" min="1" max="300"/);
  assert.match(h.root.innerHTML, /name="max_depth" min="1" max="4"/);
  assert.match(h.root.innerHTML, /name="parse_nested"/);
  assert.match(h.root.innerHTML, /单条消息最大转发数/);
  assert.equal(h.page.searchEntries().filter(item => item.group === "forward_intake").length, 4);

  const values = {max_forwards: "2", max_nodes: "80", max_depth: "2"};
  const message = {textContent: "", hidden: true};
  const button = {disabled: false, textContent: "保存合并转发上限"};
  const controls = Object.fromEntries(Object.entries(values).map(([name, value]) => [name, {value}]));
  controls.parse_nested = {checked: false};
  const form = {dataset: {hostForm: "forward_intake"},
    querySelector(selector) {
      const field = selector.match(/^\[name="(.+)"\]$/)?.[1];
      return field ? controls[field] : selector.includes("button") ? button : message;
    }};
  let prevented = false;
  h.handlers.submit({target: {closest: () => form}, preventDefault() {prevented = true;}});
  await drain();
  const saved = h.calls.find(call => call.method === "POST");
  assert.equal(prevented, true);
  assert.equal(saved.route, "host-options/forward_intake");
  assert.deepEqual(JSON.parse(JSON.stringify(saved.body.changes)),
    {max_forwards: 2, max_nodes: 80, max_depth: 2, parse_nested: false});
  assert.match(h.root.innerHTML, /已保存/);
});

test("forward intake page rejects a value beyond the host-provided structural bound", async () => {
  const h = harness();
  h.page.mount({forwardRoot: h.root});
  await drain();
  const message = {textContent: "", hidden: true};
  const controls = {max_forwards: {value: "4"}, max_nodes: {value: "301"}, max_depth: {value: "4"},
    parse_nested: {checked: true}};
  const form = {dataset: {hostForm: "forward_intake"}, querySelector(selector) {
    const field = selector.match(/^\[name="(.+)"\]$/)?.[1];
    return field ? controls[field] : message;
  }};
  h.handlers.submit({target: {closest: () => form}, preventDefault() {}});
  await drain();
  assert.equal(h.calls.some(call => call.method === "POST"), false);
  assert.match(message.textContent, /节点数请输入 1 到 300/);
});

test("received-poke page renders and saves a bounded repeated-poke count", async () => {
  const h = harness("pokePolicyWorkspace");
  h.page.mount({pokeRoot: h.root});
  await drain();
  assert.match(h.root.innerHTML, /name="back_times" min="1" max="5" step="1"/);
  assert.match(h.root.innerHTML, /连续回戳次数/);
  assert.equal(h.page.searchEntries().find(item => item.field === "back_times")?.label, "连续回戳次数");
  assert.match(h.root.innerHTML, /name="model_reply_enabled"/);
  assert.match(h.root.innerHTML, /name="model_reply_probability" min="0" max="100" step="1"/);
  assert.equal(h.page.searchEntries().find(item => item.field === "model_reply_probability")?.label, "模型回复概率");

  const message = {textContent: "", hidden: true};
  const button = {disabled: false, textContent: "保存戳一戳设置"};
  const controls = {enabled: {checked: true}, trigger_probability: {value: "25"},
    back_probability: {value: "50"}, back_times: {value: "4"}, model_reply_enabled: {checked: true},
    model_reply_probability: {value: "65"}, replies: {value: "我在"},
    context_reply_enabled: {checked: false}, context_delay_seconds: {value: "3"}};
  const form = {dataset: {hostForm: "poke"}, querySelector(selector) {
    const field = selector.match(/^\[name="(.+)"\]$/)?.[1];
    return field ? controls[field] : selector.includes("button") ? button : message;
  }};
  h.handlers.submit({target: {closest: () => form}, preventDefault() {}});
  await drain();
  const saved = h.calls.find(call => call.method === "POST");
  assert.equal(saved.route, "host-options/poke");
  assert.deepEqual(JSON.parse(JSON.stringify(saved.body.changes)), {enabled: true, trigger_probability: 0.25,
    back_probability: 0.5, back_times: 4, model_reply_enabled: true, model_reply_probability: 0.65,
    context_reply_enabled: false, context_delay_seconds: 3, replies: ["我在"]});
  assert.match(h.root.innerHTML, /value="4"/);
});

test("received-poke page refuses a repeat count outside its supported range", async () => {
  const h = harness("pokePolicyWorkspace");
  h.page.mount({pokeRoot: h.root});
  await drain();
  const message = {textContent: "", hidden: true};
  const controls = {enabled: {checked: true}, trigger_probability: {value: "25"},
    back_probability: {value: "50"}, back_times: {value: "6"}, model_reply_enabled: {checked: false},
    model_reply_probability: {value: "30"}, replies: {value: "我在"}};
  const form = {dataset: {hostForm: "poke"}, querySelector(selector) {
    const field = selector.match(/^\[name="(.+)"\]$/)?.[1];
    return field ? controls[field] : message;
  }};
  h.handlers.submit({target: {closest: () => form}, preventDefault() {}});
  await drain();
  assert.equal(h.calls.some(call => call.method === "POST"), false);
  assert.match(message.textContent, /连续回戳次数请输入 1 到 5/);
});
