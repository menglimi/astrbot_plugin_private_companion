"use strict";

/* Offline page contracts for the round-1 image protocol fix (F-02).
   The model page owns the per-endpoint generation parameters, so it must show
   and persist "server default" quality plus the explicit legacy response format.
   The output page must draft schema-consistent endpoint defaults. */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const MODELS_SOURCE = fs.readFileSync(path.resolve(__dirname, "../pages/陪伴小屋/models-page.js"), "utf8");
const OUTPUT_SOURCE = fs.readFileSync(path.resolve(__dirname, "../pages/陪伴小屋/output-page.js"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));
/* The pages race bridge calls against a long rejection timer.  The timer keeps
   the Node event loop alive after the assertions finish, so unref it: the
   rejection path still works, the test process just does not wait 45s. */
const guardTimeout = (callback, delay) => {
  const timer = setTimeout(callback, delay);
  timer.unref?.();
  return timer;
};

function modelsStatus() {
  return {
    tasks: [{kind: "conversation.reply", lane: "foreground", capabilities: ["text"], edit_token: "a".repeat(64),
      application_state: "applied", fields: {}}],
    providers: [{id: "primary", capabilities: ["text"], max_output_tokens: 8192}],
    host_state: "running",
  };
}

function imageStatus() {
  return {
    actions: [
      {action: "image.generate", enabled: true, owner: "image-plugin", provider_id: "native-image",
        loaded: true, current: true, registered: true, state: "ready"},
      {action: "image.edit", enabled: true, owner: "image-plugin", provider_id: "native-image",
        loaded: true, current: true, registered: true, state: "ready"},
    ],
    enabled: {"image.generate": true, "image.edit": true, "image.mask_edit": false},
    authorization: "checked_for_each_invocation", module: {state: "running"},
  };
}

function imageConfiguration(endpointOverrides = {}) {
  return {
    revision: "image-r1",
    limits: {max_endpoints: 24, timeout_min: 1, timeout_max: 1800},
    settings: {
      enabled: true, backend: "online_api", legacy_backend: "", legacy_options: {}, network: {},
      endpoints: [{
        id: "primary", name: "Primary", enabled: true, platform: "openai", model: "gpt-image-1",
        width: 1024, height: 1024, size_label: "1024x1024", max_reference_images: 1,
        base_url: "https://api.example.test/v1", timeout_seconds: 60, api_key_set: true,
        ...endpointOverrides,
      }],
      sd_webui: {}, comfyui: {},
      operation_routes: {generate: ["online:primary"], edit: ["online:primary"]},
    },
  };
}

/* Minimal DOM surface used by the two pages: event dispatch, request capture. */
function pageRoot() {
  const handlers = {};
  return {
    isConnected: true, innerHTML: "",
    addEventListener(type, handler) { handlers[type] = handler; },
    removeEventListener(type) { delete handlers[type]; },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    fire(type, selector, dataset) {
      const target = {
        dataset: dataset || {}, value: dataset?.value, checked: dataset?.checked,
        disabled: false,
        matches: candidate => candidate === selector,
        closest: candidate => (candidate === selector ? {dataset: dataset || {}} : null),
      };
      handlers[type]({type, target});
    },
    click(selector, dataset) { this.fire("click", selector, dataset); },
    change(selector, dataset) { this.fire("change", selector, dataset); },
  };
}

function modelsHarness() {
  const requests = [];
  const store = new Map();
  const window = {
    CompanionProduct: {
      hosted: () => true,
      role: () => ({model_api: "/astrbot_plugin_private_companion/model"}),
      identity: () => ({installation_id: "inst", operator_id: "operator"}),
      bridgeNow: () => window.AstrBotPluginPage,
      bridgeFor: () => ({then: resolve => resolve(window.AstrBotPluginPage)}),
    },
    AstrBotPluginPage: {
      apiGet: (route, params) => new Promise((resolve, reject) => requests.push({method: "GET", route, params, resolve, reject})),
      apiPost: (route, values) => new Promise((resolve, reject) => requests.push({method: "POST", route, values, resolve, reject})),
    },
    sessionStorage: {
      getItem: key => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => store.set(key, value),
      removeItem: key => store.delete(key),
    },
    crypto: {randomUUID: () => "key-" + requests.length},
  };
  vm.runInNewContext(MODELS_SOURCE, {window, crypto: window.crypto, AbortController, setTimeout: guardTimeout, clearTimeout});
  return {page: window.CompanionModelsPage, requests};
}

async function openImageConfiguration(overrides) {
  const harness = modelsHarness(), node = pageRoot();
  harness.page.mount(node);
  harness.requests[0].resolve(modelsStatus());
  await flush();
  node.click("[data-model-tab]", {modelTab: "image"});
  const imageGet = harness.requests.find(request => request.method === "GET" && request.route === "model/image");
  assert.ok(imageGet, "the image tab reads the native image status");
  imageGet.resolve(imageStatus());
  const schemaGet = harness.requests.find(request => request.route.endsWith("/image-schema"));
  assert.ok(schemaGet, "the image tab reads its backend field schema");
  schemaGet.resolve({image_provider_group: {}});
  await flush();
  const configGet = harness.requests.find(request => request.method === "GET" && request.route.endsWith("/image/output"));
  assert.ok(configGet, "the image tab reads the configured endpoints");
  configGet.resolve(imageConfiguration(overrides));
  await flush();
  return {page: harness.page, requests: harness.requests, node};
}

function selectOptions(html, field) {
  const tail = html.split(`data-img-config-field="${field}"`)[1];
  assert.ok(tail, `${field} must be rendered`);
  const select = tail.split("</select>")[0];
  return {
    values: [...select.matchAll(/<option value="([^"]*)"/g)].map(match => match[1]),
    selected: [...select.matchAll(/<option value="([^"]*)" selected>/g)].map(match => match[1]),
    select,
  };
}

test("the model page defaults to server quality and no response format", async () => {
  const {node} = await openImageConfiguration();

  const quality = selectOptions(node.innerHTML, "endpoints.0.quality");
  assert.deepEqual(quality.values, ["", "auto", "low", "medium", "high", "standard", "hd"]);
  assert.deepEqual(quality.selected, [""], "an unset quality means the server default");
  assert.match(quality.select, /服务端默认/);

  const format = selectOptions(node.innerHTML, "endpoints.0.response_format");
  assert.deepEqual(format.values, ["", "b64_json"]);
  assert.deepEqual(format.selected, [""], "no response_format is requested by default");
});

test("an explicitly saved legacy choice is shown as selected", async () => {
  const {node} = await openImageConfiguration({quality: "standard", response_format: "b64_json"});

  assert.deepEqual(selectOptions(node.innerHTML, "endpoints.0.quality").selected, ["standard"]);
  assert.deepEqual(selectOptions(node.innerHTML, "endpoints.0.response_format").selected, ["b64_json"]);
  assert.match(node.innerHTML, /保存一次即按服务端默认清理/);
});

test("GPT Image endpoints explain that the legacy standard quality is not sent", async () => {
  const {node} = await openImageConfiguration();

  assert.match(node.innerHTML, /GPT Image 只接受自动\/低\/中\/高/);
  assert.match(node.innerHTML, /升级前保存的「标准\/高清」/);
});

test("dall-e endpoints keep the legacy vocabulary explanation", async () => {
  const {node} = await openImageConfiguration({model: "dall-e-3"});

  assert.match(node.innerHTML, /standard\/hd 只对 dall-e 系列有效/);
});

test("the model page saves the explicit legacy opt-in and keeps it after a roundtrip", async () => {
  const {node, requests} = await openImageConfiguration({model: "dall-e-3"});

  node.change("[data-img-config-field]", {imgConfigField: "endpoints.0.quality", value: "hd"});
  node.change("[data-img-config-field]", {imgConfigField: "endpoints.0.response_format", value: "b64_json"});
  assert.deepEqual(selectOptions(node.innerHTML, "endpoints.0.quality").selected, ["hd"]);

  node.click("[data-img-config-save]");
  const save = requests.find(request => request.method === "POST" && request.route.endsWith("/image/output/save"));
  assert.ok(save, "saving posts the whole draft");
  const endpoint = save.values.changes.endpoints[0];
  assert.equal(endpoint.quality, "hd");
  assert.equal(endpoint.response_format, "b64_json");

  save.resolve({...imageConfiguration(), revision: "image-r2", settings: save.values.changes});
  await flush();

  assert.deepEqual(selectOptions(node.innerHTML, "endpoints.0.quality").selected, ["hd"]);
  assert.deepEqual(selectOptions(node.innerHTML, "endpoints.0.response_format").selected, ["b64_json"]);
});

test("a model added on the model page starts on server defaults", async () => {
  const {node, requests} = await openImageConfiguration();

  node.click("[data-img-model-add]");
  node.click("[data-img-config-save]");
  const save = requests.filter(request => request.method === "POST" && request.route.endsWith("/image/output/save")).at(-1);
  const added = save.values.changes.endpoints.at(-1);

  assert.equal(added.quality, "", "a new endpoint must not force the standard quality");
  assert.equal(added.response_format, "", "a new endpoint must not force b64_json");
});

/* ------------------------------------------------------------------------- */
/* Output page: endpoint drafts must match the saved schema.                  */
/* ------------------------------------------------------------------------- */

function outputHarness() {
  const requests = [];
  const nodes = new Map();
  const node = selector => {
    if (!nodes.has(selector)) {
      nodes.set(selector, {innerHTML: "", textContent: "", hidden: false, classList: {toggle() {}},
        querySelector() { return null; }});
    }
    return nodes.get(selector);
  };
  const listeners = new Map();
  const window = {
    CompanionProduct: {
      scopeKey: () => "role-a",
      role: () => ({model_api: "model", image_material_api: "image-materials"}),
      relative: value => value,
      request(method, endpoint, body) {
        if (endpoint === "model/image/output" && method === "GET") {
          return new Promise(resolve => requests.push({method, endpoint, resolve}));
        }
        if (endpoint === "model/image/observability") {
          return Promise.resolve({images: {total: 0, items: []}, content: {available: false}});
        }
        if (endpoint === "image-materials/album") return Promise.resolve({items: []});
        if (endpoint === "model/image/review/providers") return Promise.resolve({providers: []});
        return new Promise((resolve, reject) => requests.push({method, endpoint, values: body, resolve, reject}));
      },
    },
    dispatchEvent() {},
  };
  const root = {
    isConnected: true,
    addEventListener(type, listener) { listeners.set(type, listener); },
    removeEventListener() {},
    contains() { return true; },
    querySelector: selector => (["#outputBackendWorkspace", "#outputLocalWorkspace",
      "[data-output-native-selector]"].includes(selector) ? node(selector) : null),
    querySelectorAll() { return []; },
  };
  const context = {window, crypto: {randomUUID: () => "image-test-id"}, console, AbortController, Event,
    setTimeout: guardTimeout, clearTimeout, Promise, Math, Date, JSON, Object, String, Array, Map, Set, Error};
  vm.runInNewContext(OUTPUT_SOURCE, context, {filename: "output-page.js"});
  return {
    page: window.CompanionOutputPage, requests, root,
    click(action) {
      const target = {dataset: {outputNativeAction: action}, disabled: false};
      listeners.get("click")({target: {closest: () => target}});
    },
  };
}

test("the output page drafts server-default quality and no response format for a new model", async () => {
  const harness = outputHarness();
  harness.page.mount(harness.root);
  const read = harness.requests.find(request => request.endpoint === "model/image/output");
  assert.ok(read, "mounting reads the image output configuration");
  read.resolve({revision: "r1", host_registered: true, settings: {
    enabled: false, backend: "auto", endpoints: [], sd_webui: {}, comfyui: {},
    operation_routes: {generate: [], edit: []},
  }});
  await flush();

  harness.click("add");
  harness.click("save");
  await flush();

  const save = harness.requests.find(request => request.method === "POST" && request.endpoint === "model/image/output/save");
  assert.ok(save, "adding a model and saving posts the draft");
  const endpoint = save.values.changes.endpoints[0];
  assert.equal(endpoint.quality, "");
  assert.equal(endpoint.response_format, "");
});

test("an xAI endpoint uses Grok Imagine's own protocol fields", async () => {
  const {node} = await openImageConfiguration({
    platform: "xai", model: "grok-imagine-image-2.0", base_url: "https://api.x.ai/v1",
    quality: "low", response_format: "", max_reference_images: 5,
  });

  const quality = selectOptions(node.innerHTML, "endpoints.0.quality");
  assert.deepEqual(quality.values, ["", "auto", "low", "medium"]);
  assert.deepEqual(quality.selected, ["low"]);
  assert.match(node.innerHTML, /xAI 仅 grok-imagine-image-2\.0/);
  assert.match(node.innerHTML, /1k、1\.5k、2k/);
  assert.match(node.innerHTML, /xAI 改图最多 5 张参考图/);
  assert.match(node.innerHTML, /xAI Grok Imagine/);
  assert.ok(selectOptions(node.innerHTML, "endpoints.0.platform").values.includes("xai"));
});
