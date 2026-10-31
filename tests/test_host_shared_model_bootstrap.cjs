"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "\u5bbf\u4e3b\u7ba1\u7406", "app.js"), "utf8");
const functions = source.slice(source.indexOf("  function modelProviderName"),
  source.indexOf("  function editorRow"));

function harness(model, saveResult = null, status = null) {
  const nodes = new Map(), calls = {api: [], notices: [], reads: 0};
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, {id, innerHTML: "", value: "", textContent: "", disabled: false,
      dataset: {}, hidden: false});
    return nodes.get(id);
  };
  const sandbox = {
    __$: $,
    __escape: value => String(value ?? "").replace(/[&<>"']/g, character =>
      ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character])),
    __notice: (message, kind) => calls.notices.push({message, kind}),
    __api: async (method, endpoint, body) => { calls.api.push({method, endpoint, body}); return saveResult; },
    __busy: value => calls.busy = value,
    __read: () => { calls.reads++; },
  };
  const preamble = `let busy = false, modelSaving = false;
    let status = ${JSON.stringify(status)};
    let modelConfig = ${JSON.stringify(model)};
    const accountApi = "accounts";
    const $ = globalThis.__$;
    const escapeHTML = globalThis.__escape;
    function notice(message, kind) { globalThis.__notice(message, kind); }
    async function api(method, endpoint, body) { return globalThis.__api(method, endpoint, body); }
    function setLoading() {}
    function setBusy(value) { busy = value; globalThis.__busy(value); }
    async function readStatus() { globalThis.__read(); }
    function failureMessage(code) { return code; }
    function publicError(error) { return error?.message || String(error); }
    ${functions}
    globalThis.renderSharedModel = renderSharedModel;
    globalThis.saveSharedModel = saveSharedModel;
    globalThis.pageState = () => ({busy, modelSaving, modelConfig});`;
  vm.createContext(sandbox);
  vm.runInContext(preamble, sandbox);
  return {sandbox, nodes, calls};
}

test("shared model panel lists loaded providers and requires a selection", () => {
  const h = harness({provider_id: "", providers: [{id: "p1", name: "Provider One"}]});
  h.sandbox.renderSharedModel();
  assert.match(h.nodes.get("shared-model-provider").innerHTML, /Provider One/);
  assert.equal(h.nodes.get("shared-model-provider").value, "");
  assert.equal(h.nodes.get("save-shared-model").disabled, true);
  assert.match(h.nodes.get("shared-model-note").textContent, /尚未选择/);

  h.nodes.get("shared-model-provider").value = "p1";
  h.sandbox.renderSharedModel();
  assert.equal(h.nodes.get("shared-model-provider").value, "p1");
  assert.equal(h.nodes.get("save-shared-model").disabled, false);
});

test("shared model options do not repeat the id when the provider has no name", () => {
  const h = harness({provider_id: "", providers: [
    {id: "p1", name: "p1"},
    {id: "p2", name: "Provider Two"},
    {id: "p3", name: ""},
  ]});
  h.sandbox.renderSharedModel();
  const html = h.nodes.get("shared-model-provider").innerHTML;
  assert.doesNotMatch(html, /p1（p1）/);
  assert.match(html, /<option value="p1">p1<\/option>/);
  assert.match(html, /<option value="p2">Provider Two（p2）<\/option>/);
  assert.match(html, /<option value="p3">p3<\/option>/);
});

test("running host note no longer promises prepare-and-start after saving", () => {
  const model = {provider_id: "p1", providers: [{id: "p1", name: "Provider One"}]};

  const idle = harness(model);
  idle.sandbox.renderSharedModel();
  assert.match(idle.nodes.get("shared-model-note").textContent, /准备并启动/);

  const running = harness(model, null, {state: "running", accepting: true});
  running.sandbox.renderSharedModel();
  const note = running.nodes.get("shared-model-note").textContent;
  assert.doesNotMatch(note, /准备并启动/);
  assert.match(note, /宿主已在运行/);
});

test("saving the shared model calls the bootstrap endpoint and reports readiness", async () => {
  const model = {provider_id: "p1", providers: [{id: "p1", name: "Provider One"}]};
  const h = harness(model, {model, prepared: true});
  h.sandbox.renderSharedModel();
  await h.sandbox.saveSharedModel();
  assert.equal(JSON.stringify(h.calls.api[0]), JSON.stringify({method: "POST", endpoint: "accounts/model-provider", body: {provider_id: "p1"}}));
  assert.equal(h.calls.reads, 1);
  assert.match(h.calls.notices.at(-1).message, /共享模型已保存/);
  assert.equal(h.sandbox.pageState().busy, false);
  assert.equal(h.sandbox.pageState().modelSaving, false);
});
