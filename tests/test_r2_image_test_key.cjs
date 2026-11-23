"use strict";

/* Round-2 F-07: an endpoint test keeps one operation key per (endpoint,
   configuration revision).  A pending/unknown result must be re-read with the
   same key (the service dedupes by operation_key and never dispatches again);
   only a terminal result or a changed configuration may allocate a new key.
   Every assertion here is offline: the bridge is a fake and no request leaves
   this process. */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const workbenchSource = fs.readFileSync(path.resolve(__dirname, "../pages/陪伴小屋/workbench-page.js"), "utf8");
const source = fs.readFileSync(path.resolve(__dirname, "../pages/陪伴小屋/models-page.js"), "utf8");
const flush = async () => {
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
};
const TOKEN = "a".repeat(64);

function task(kind, provider, lane = "foreground") {
  const field = value => ({value, inherited_value: value, source: "default", inherited_source: "default", explicit: false});
  return {
    kind, lane, capabilities: ["text"], edit_token: TOKEN, application_state: "applied",
    fields: {
      provider: {value: provider, inherited_value: provider, source: "window", inherited_source: "installation", explicit: false},
      fallback: field(""), prompt: field(""), max_output_tokens: field(4096), timeout_seconds: field(60),
      replacement_rules: field("[]"), fallback_on_refusal: field(false),
      refusal_terms: field("很抱歉，我无法"), replacement_scope: field("all"),
    },
  };
}

function status() {
  return {
    tasks: [task("conversation.reply", "primary"), task("memory.reflect", "primary", "background")],
    providers: [{id: "primary", capabilities: ["text"], max_output_tokens: 8192}],
    host_state: "running",
  };
}

const ACTIONS = {
  actions: [
    {action: "image.generate", enabled: true, owner: "image-plugin", provider_id: "native-image",
      loaded: true, current: true, registered: true, state: "ready"},
    {action: "image.edit", enabled: true, owner: "image-plugin", provider_id: "native-image",
      loaded: true, current: true, registered: true, state: "ready"},
    {action: "image.mask_edit", enabled: true, owner: "image-plugin", provider_id: "native-image",
      loaded: true, current: true, registered: true, state: "ready"},
  ],
  enabled: {"image.generate": true, "image.edit": true, "image.mask_edit": true},
  authorization: "checked_for_each_invocation", module: {state: "running"},
};

function configuration(revision = "image-r1", endpoints = null) {
  return {
    revision,
    limits: {max_endpoints: 24, timeout_min: 1, timeout_max: 1800},
    settings: {
      enabled: true, backend: "auto",
      endpoints: endpoints || [
        {id: "primary", name: "主用模型", enabled: true, platform: "openai", model: "image-v1",
          width: 1024, height: 1024, base_url: "https://api.example.test/v1", timeout_seconds: 180,
          api_key_set: true, max_reference_images: 1},
        {id: "secondary", name: "备用模型", enabled: true, platform: "openai", model: "image-v2",
          width: 1024, height: 1024, base_url: "https://backup.example.test/v1", timeout_seconds: 180,
          api_key_set: false, max_reference_images: 1},
      ],
      sd_webui: {base_url: "http://127.0.0.1:7860", timeout_seconds: 300, parameters: {}},
      comfyui: {base_url: "", timeout_seconds: 300, workflows: {}},
      legacy_options: {},
      operation_routes: {generate: ["online:primary", "online:secondary"], edit: ["online:primary"]},
    },
  };
}

/* ``session`` is the per-tab storage backing store.  Reusing one Map across
   two harnesses simulates a page reload without sharing module state. */
function harness(session = new Map(), prefix = "key") {
  const requests = [];
  let uuid = 0;
  const window = {
    CompanionProduct: {
      hosted: () => true,
      role: () => ({model_api: "/astrbot_plugin_private_companion/model"}),
      identity: () => ({installation_id: "inst", operator_id: "operator"}),
      bridgeNow: () => window.AstrBotPluginPage,
      bridgeFor: () => ({then: resolve => resolve(window.AstrBotPluginPage)}),
    },
    AstrBotPluginPage: {
      apiGet: (route, params) => new Promise((resolve, reject) => {
        requests.push({method: "GET", route, params, resolve, reject});
      }),
      apiPost: (route, values) => new Promise((resolve, reject) => {
        requests.push({method: "POST", route, values, resolve, reject});
      }),
    },
    sessionStorage: {
      getItem: key => (session.has(key) ? session.get(key) : null),
      setItem: (key, value) => session.set(key, value),
      removeItem: key => session.delete(key),
    },
    crypto: {randomUUID: () => `${prefix}-${++uuid}`},
  };
  vm.runInNewContext(workbenchSource, {window, setTimeout, clearTimeout});
  vm.runInNewContext(source, {window, sessionStorage: window.sessionStorage, crypto: window.crypto,
    AbortController, setTimeout, clearTimeout});
  function root() {
    const handlers = {};
    return {
      isConnected: true, innerHTML: "",
      addEventListener(type, handler) {handlers[type] = handler;},
      removeEventListener(type) {delete handlers[type];},
      querySelectorAll() {return [];},
      querySelector() {return null;},
      fire(type, selector, dataset) {
        handlers[type]({type, target: {dataset: dataset || {}, value: dataset?.value, checked: dataset?.checked,
          matches: candidate => candidate === selector,
          closest: candidate => (candidate === selector ? {dataset: dataset || {}} : null)}});
      },
      click(selector, dataset) {this.fire("click", selector, dataset);},
      input(selector, dataset) {this.fire("input", selector, dataset);},
      change(selector, dataset) {this.fire("change", selector, dataset);},
    };
  }
  return {page: window.CompanionModelsPage, requests, root, session};
}

function testPosts(h) {
  return h.requests.filter(request => request.method === "POST" && request.route === "model/image/output/test");
}

function buttonFor(html, endpointId) {
  const marker = `data-img-endpoint-test="${endpointId}"`;
  const start = html.indexOf(marker);
  if (start < 0) return "";
  const open = html.lastIndexOf("<button", start);
  return html.slice(open, html.indexOf("</button>", start) + "</button>".length);
}

/* Mount, open the image tab and load the endpoint configuration. */
async function open(h, config = configuration()) {
  const node = h.root();
  h.page.mount(node);
  h.requests.find(request => request.method === "GET" && request.route === "model").resolve(status());
  await flush();
  node.click("[data-model-tab]", {modelTab: "image"});
  h.requests.find(request => request.method === "GET" && request.route === "model/image-schema").resolve({});
  h.requests.find(request => request.method === "GET" && request.route === "model/image").resolve(ACTIONS);
  await flush();
  h.requests.find(request => request.method === "GET" && request.route === "model/image/output").resolve(config);
  await flush();
  return node;
}

function clickTest(node, endpointId = "primary") {
  node.click("[data-img-endpoint-test]", {imgEndpointTest: endpointId});
}

test("an unknown result reuses its operation key and offers a read-only recheck", async () => {
  const h = harness();
  const node = await open(h);

  clickTest(node);
  let posts = testPosts(h);
  assert.equal(posts.length, 1, "the first click submits one test");
  const first = posts[0];
  assert.deepEqual(Object.keys(first.values).sort(), ["endpoint_id", "operation_key"],
    "the page must not smuggle configuration into the request payload to keep a key");
  const key = first.values.operation_key;
  assert.equal(first.values.endpoint_id, "primary");

  first.resolve({status: "unknown", elapsed_ms: 8400, error: "backend_result_unconfirmed"});
  await flush();
  assert.match(node.innerHTML, /结果未知/);
  assert.match(buttonFor(node.innerHTML, "primary"), /核对原测试/,
    "an unconfirmed test must offer a read-only recheck instead of a new generation");

  clickTest(node);
  posts = testPosts(h);
  assert.equal(posts.length, 2);
  assert.equal(posts[1].values.operation_key, key,
    "the recheck must reuse the operation key so the service dedupes instead of dispatching again");
  assert.equal(posts[1].values.endpoint_id, "primary");

  posts[1].resolve({status: "succeeded", elapsed_ms: 4, error: ""});
  await flush();
  assert.match(buttonFor(node.innerHTML, "primary"), /生成测试图/,
    "a confirmed result is terminal: the next click starts a new test");
  assert.match(node.innerHTML, /测试成功 · 沿用上次结果/,
    "a replayed confirmation must not pretend to be a fresh generation");

  clickTest(node);
  posts = testPosts(h);
  assert.equal(posts.length, 3);
  assert.notEqual(posts[0].values.operation_key, posts[2].values.operation_key,
    "a terminal succeeded result must rotate the operation key");
  posts[2].resolve({status: "succeeded", elapsed_ms: 900, error: ""});
  await flush();
  h.page.unmount();
});

test("a bridge failure is an unknown result, never an error with a fresh key", async () => {
  const h = harness();
  const node = await open(h);

  clickTest(node);
  const first = testPosts(h)[0];
  const key = first.values.operation_key;
  first.reject(Object.assign(new Error("bridge unavailable"), {code: "bridge_timeout"}));
  await flush();

  assert.match(buttonFor(node.innerHTML, "primary"), /核对原测试/,
    "a failed bridge call may still be running remotely; the page must offer the recheck");
  assert.match(node.innerHTML, /结果未知/);
  assert.doesNotMatch(node.innerHTML, /测试未完成/, "an unconfirmed call must not be rendered as a client error");
  assert.doesNotMatch(node.innerHTML, /invalid_response/);

  clickTest(node);
  const second = testPosts(h)[1];
  assert.equal(second.values.operation_key, key,
    "a bridge failure must keep the key; a fresh key would submit a second paid generation");
  second.resolve({status: "succeeded", elapsed_ms: 700, error: ""});
  await flush();
  h.page.unmount();
});

test("prepared and dispatching replay states stay pending and keep one key", async () => {
  const h = harness();
  const node = await open(h);

  clickTest(node);
  const key = testPosts(h)[0].values.operation_key;
  testPosts(h)[0].resolve({status: "prepared", elapsed_ms: 0, error: ""});
  await flush();
  assert.doesNotMatch(node.innerHTML, /invalid_response|测试未完成/,
    "internal service states must be projected to a safe message");
  assert.match(node.innerHTML, /请求已提交/);
  assert.match(buttonFor(node.innerHTML, "primary"), /核对原测试/);

  clickTest(node);
  assert.equal(testPosts(h)[1].values.operation_key, key,
    "a prepared operation is still in flight; the recheck keeps the same key");
  assert.match(buttonFor(node.innerHTML, "primary"), /核对中…/);

  testPosts(h)[1].resolve({status: "dispatching", elapsed_ms: 0, error: ""});
  await flush();
  assert.doesNotMatch(node.innerHTML, /invalid_response|测试未完成/);
  assert.match(node.innerHTML, /请求已提交/);

  clickTest(node);
  assert.equal(testPosts(h)[2].values.operation_key, key,
    "dispatching is still the same operation; the key must not rotate");
  testPosts(h)[2].resolve({status: "succeeded", elapsed_ms: 600, error: ""});
  await flush();
  h.page.unmount();
});

test("stopped and failed results are terminal and rotate the key", async () => {
  const h = harness();
  const node = await open(h);

  clickTest(node);
  const first = testPosts(h)[0];
  first.resolve({status: "stopped", elapsed_ms: 0, error: "runtime_restarted"});
  await flush();
  assert.doesNotMatch(node.innerHTML, /invalid_response|测试未完成/);
  assert.match(node.innerHTML, /测试已中断/);
  assert.match(buttonFor(node.innerHTML, "primary"), /生成测试图/,
    "a stopped operation never dispatched, so a new test is safe");

  clickTest(node);
  const second = testPosts(h)[1];
  assert.notEqual(second.values.operation_key, first.values.operation_key);
  second.resolve({status: "failed", elapsed_ms: 240, error: "native_endpoint_rejected"});
  await flush();
  assert.match(node.innerHTML, /生成失败/);
  assert.match(buttonFor(node.innerHTML, "primary"), /生成测试图/);

  clickTest(node);
  assert.notEqual(testPosts(h)[2].values.operation_key, second.values.operation_key,
    "a failed result is terminal and must rotate the key");
  testPosts(h)[2].resolve({status: "succeeded", elapsed_ms: 640, error: ""});
  await flush();
  h.page.unmount();
});

test("saving a new configuration revision rotates the key even after an unknown result", async () => {
  const h = harness();
  const config = configuration();
  const node = await open(h, config);

  clickTest(node);
  const first = testPosts(h)[0];
  const key = first.values.operation_key;
  first.resolve({status: "unknown", elapsed_ms: 8400, error: "backend_result_unconfirmed"});
  await flush();

  /* The same revision still reuses the key... */
  clickTest(node);
  assert.equal(testPosts(h)[1].values.operation_key, key);
  testPosts(h)[1].resolve({status: "unknown", elapsed_ms: 8400, error: "backend_result_unconfirmed"});
  await flush();

  /* ...but the backend fingerprint does not contain base_url/api_key, so a
     changed revision must allocate a new key or the old result is replayed. */
  node.change("[data-img-config-field]", {imgConfigField: "endpoints.0.model", value: "image-v3"});
  node.click("[data-img-config-save]");
  const save = h.requests.find(request => request.method === "POST" && request.route === "model/image/output/save");
  assert.ok(save, "the edited configuration is saved");
  const saved = JSON.parse(JSON.stringify(save.values.changes));
  save.resolve({revision: "image-r2", settings: saved, limits: config.limits});
  await flush();
  assert.equal(h.page.isDirty(), false);

  assert.doesNotMatch(node.innerHTML, /结果未知/,
    "a saved configuration invalidates the previous test result");
  assert.match(buttonFor(node.innerHTML, "primary"), /生成测试图/);

  clickTest(node);
  const next = testPosts(h).at(-1);
  assert.notEqual(next.values.operation_key, key,
    "a changed configuration must never replay the previous result under the old key");
  next.resolve({status: "succeeded", elapsed_ms: 800, error: ""});
  await flush();
  h.page.unmount();
});

test("each endpoint owns its key, so switching models never reuses another key", async () => {
  const h = harness();
  const node = await open(h);

  clickTest(node, "primary");
  const primary = testPosts(h)[0];
  const primaryKey = primary.values.operation_key;
  primary.resolve({status: "unknown", elapsed_ms: 8400, error: "backend_result_unconfirmed"});
  await flush();

  clickTest(node, "secondary");
  const secondary = testPosts(h)[1];
  assert.equal(secondary.values.endpoint_id, "secondary");
  assert.notEqual(secondary.values.operation_key, primaryKey,
    "a different endpoint is a different operation");
  secondary.resolve({status: "succeeded", elapsed_ms: 500, error: ""});
  await flush();

  clickTest(node, "primary");
  assert.equal(testPosts(h).at(-1).values.operation_key, primaryKey,
    "returning to the first endpoint must still recheck its own unconfirmed operation");
  testPosts(h).at(-1).resolve({status: "succeeded", elapsed_ms: 5, error: ""});
  await flush();
  h.page.unmount();
});

test("re-reading the same configuration revision keeps the unconfirmed key", async () => {
  const h = harness();
  const config = configuration();
  const node = await open(h, config);

  clickTest(node);
  const first = testPosts(h)[0];
  const key = first.values.operation_key;
  first.resolve({status: "unknown", elapsed_ms: 8400, error: "backend_result_unconfirmed"});
  await flush();

  node.click("[data-img-config-refresh]");
  const refresh = h.requests.filter(request => request.method === "GET" && request.route === "model/image/output").at(-1);
  assert.ok(refresh, "the page rereads the configuration on request");
  refresh.resolve(config);
  await flush();
  assert.match(buttonFor(node.innerHTML, "primary"), /核对原测试/,
    "a reread of the same revision must not turn the recheck back into a new generation");

  clickTest(node);
  assert.equal(testPosts(h).at(-1).values.operation_key, key,
    "the same revision keeps its key across a configuration reread");
  testPosts(h).at(-1).resolve({status: "succeeded", elapsed_ms: 5, error: ""});
  await flush();
  h.page.unmount();
});

test("a reload keeps the unconfirmed key so a recheck still cannot double-charge", async () => {
  const session = new Map();
  const first = harness(session);
  const firstNode = await open(first);
  clickTest(firstNode);
  const key = testPosts(first)[0].values.operation_key;
  testPosts(first)[0].resolve({status: "unknown", elapsed_ms: 8400, error: "backend_result_unconfirmed"});
  await flush();
  first.page.unmount();

  /* A reload loses module state but not the tab's session storage. */
  const second = harness(session, "reload");
  const secondNode = await open(second);
  assert.match(secondNode.innerHTML, /结果未知/, "the unconfirmed test is still visible after a reload");
  assert.match(buttonFor(secondNode.innerHTML, "primary"), /核对原测试/);

  clickTest(secondNode);
  assert.equal(testPosts(second).at(-1).values.operation_key, key,
    "after a reload the recheck must still reuse the original operation key");
  testPosts(second).at(-1).resolve({status: "succeeded", elapsed_ms: 6, error: ""});
  await flush();
  assert.equal(session.size, 0, "a terminal result clears the stored key");
  second.page.unmount();
});

test("a malformed bridge response is unconfirmed and keeps the key", async () => {
  const h = harness();
  const node = await open(h);

  clickTest(node);
  const first = testPosts(h)[0];
  const key = first.values.operation_key;
  first.resolve({status: "something-new", elapsed_ms: 0, error: ""});
  await flush();

  assert.doesNotMatch(node.innerHTML, /invalid_response|测试未完成/,
    "an unknown service status must not surface as a protocol error");
  assert.match(node.innerHTML, /结果未知/);
  assert.match(buttonFor(node.innerHTML, "primary"), /核对原测试/);

  clickTest(node);
  assert.equal(testPosts(h).at(-1).values.operation_key, key,
    "an unparsable response may still hide a dispatched generation; keep the key");
  testPosts(h).at(-1).resolve({status: "succeeded", elapsed_ms: 400, error: ""});
  await flush();
  h.page.unmount();
});
