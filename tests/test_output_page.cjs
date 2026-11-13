"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "../pages/陪伴小屋/output-page.js"), "utf8");
const selectors = [
  '[data-output-content-stat="works"]',
  '[data-output-content-stat="candidates"]',
  '[data-output-content-stat="updated"]',
  "[data-output-content-preview]",
  "[data-output-content-preview-count]",
  "[data-output-content-preview-updated]",
  "[data-output-native-notice]",
];

function fixture(nai = null, album = {}) {
  const nodes = new Map(selectors.map(selector => [selector, [{textContent: "初始值", hidden: false,
    classList: {toggle() {}}}]]));
  const single = new Map([
    ["#outputAlbumWorkspace", {innerHTML: "", hidden: false}],
    ["#outputAlbumLightbox", {innerHTML: "", hidden: true}],
    ["[data-output-preview-image]", {hidden: true, src: "", alt: "", removeAttribute() {}}],
    [".output-image-frame", {dataset: {}}],
    [".output-image-status span", {textContent: ""}],
    ["[data-output-native-selector]", {innerHTML: ""}],
    ["#outputNaiWorkspace", {innerHTML: ""}],
    ["#outputObservationWorkspace", {innerHTML: ""}],
  ]);
  const requests = [];
  let observabilityFailure = false;
  let reconnectFailureCode = "";
  let outputFailure = false;
  let manifestFailure = false;
  let observationItems = [];
  const listeners = new Map();
  let outputSettings = {backend: "auto", enabled: false, endpoints: [], comfyui: {}, sd_webui: {},
    operation_routes: {generate: [], edit: []}};
  const role = {role_id: "role-a", model_api: "model", image_material_api: "image-materials"};
  const window = {
    CompanionProduct: {
      scopeKey: () => "role-a",
      role: () => role,
      targets: () => album.targets || [],
      relative: value => value,
      async request(method, endpoint, body) {
        requests.push({method, endpoint, body});
        if (album.request && /\/(album|references)(\/|$)/.test(endpoint)) return album.request(method, endpoint, body);
        if (endpoint === "integration/manifest") {
          if (manifestFailure) throw Object.assign(new Error("network failed"), {code: "network_error"});
          return {nai};
        }
        if (endpoint === "model/image/observability") {
          if (observabilityFailure) throw Object.assign(new Error("network failed"), {code: "network_error"});
          return {images: {total: 1, items: observationItems}, content: {available: true, works_count: 2,
            candidate_count: 1, updated_at: "2026-10-12T04:05:00Z"}};
        }
        if (endpoint === "model/image/output/save") outputSettings = body.changes;
        if (endpoint === "model/image/output" && outputFailure) throw new Error("角色配置暂不可读");
        if (endpoint === "model/image/output" || endpoint === "model/image/output/save")
          return {settings: outputSettings, nai, revision: "r1", host_registered: false};
        if (endpoint === "model/image/output/reconnect") {
          if (reconnectFailureCode) throw Object.assign(new Error(reconnectFailureCode), {code: reconnectFailureCode});
          return {reconnected: true, host_registered: true};
        }
        if (endpoint === "model/image/review/providers") return {providers: []};
        if (endpoint === "image-materials/album") return {items: [{image_id: "img-1",
          thumbnail: "data:image/png;base64,AAA", width: 512, height: 512,
          created_at: "2026-10-12T04:05:00Z", action: "image.generate"}], next_cursor: null};
        if (endpoint === "image-materials/album/export") return {filename: "album.zip",
          data_url: "data:application/zip;base64,AAA", count: 1};
        if (endpoint === "image-materials/album/hide") return {hidden: 1, hidden_total: 1};
        if (endpoint === "image-materials/album/open-folder") return {path: "C:\\data", opened: true};
        throw new Error(`unexpected request: ${method} ${endpoint}`);
      },
    },
    dispatchEvent() {},
    document: {createElement: () => ({click() {}, remove() {}}), body: {appendChild() {}}},
  };
  const root = {
    isConnected: true,
    addEventListener(type, listener) {listeners.set(type, listener);},
    removeEventListener() {},
    contains() {return true;},
    querySelector(selector) {return single.get(selector) || null;},
    querySelectorAll(selector) {return nodes.get(selector) || [];},
  };
  const context = {window, document: window.document, crypto: {randomUUID: () => "synthetic-op"}, console, AbortController, Event, setTimeout, clearTimeout, Promise, Math, Date, JSON,
    Object, String, Array, Map, Set, Error};
  vm.runInNewContext(source, context, {filename: "output-page.js"});
  return {page: window.CompanionOutputPage, nodes, single, requests, root,
    toggleNai(operation, checked) {
      const target = {matches: selector => selector === "[data-output-nai-operation]",
        dataset: {outputNaiOperation: operation}, checked};
      listeners.get("input")({target});
    },
    failObservability(value) {observabilityFailure = value;},
    setObservations(value) {observationItems = value;},
    clearRole() {delete role.model_api; delete role.image_material_api;},
    failOutput(value) {outputFailure = value;},
    failManifest(value) {manifestFailure = value;},
    failReconnect(code) {reconnectFailureCode = code;},
    clickReconnect() {
      const target = {disabled: false, dataset: {outputNativeAction: "reconnect"}};
      listeners.get("click")?.({target: {closest: () => target}});
    },
    clickAlbum(index) {
      const target = {disabled: false, dataset: {outputNativeAction: "album-open",
        outputImageIndex: String(index)}};
      listeners.get("click")?.({target: {closest: () => target}});
    },
    clickAction(action, dataset = {}) {
      const target = {disabled: false, dataset: {outputNativeAction: action, ...dataset}};
      listeners.get("click")?.({target: {closest: () => target}});
    },
    toggleAlbum(imageId, checked = true) {
      const target = {matches: selector => selector === "[data-output-album-select]",
        dataset: {outputAlbumSelect: imageId}, checked};
      listeners.get("change")?.({target});
    }};
}

test("Content summary renders real counts and clears stale values after a failed reread", async () => {
  const state = fixture();
  state.page.mount(state.root);
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(state.nodes.get('[data-output-content-stat="works"]')[0].textContent, "2");
  assert.equal(state.nodes.get('[data-output-content-stat="candidates"]')[0].textContent, "1");
  assert.match(state.nodes.get("[data-output-content-preview]")[0].textContent, /2 部已关联作品 · 1 份续写候选稿/);
  assert.ok(state.requests.some(request => request.endpoint === "model/image/observability"));

  state.failObservability(true);
  await state.page.refreshObservability();
  assert.equal(state.nodes.get('[data-output-content-stat="works"]')[0].textContent, "—");
  assert.equal(state.nodes.get('[data-output-content-stat="candidates"]')[0].textContent, "—");
  assert.equal(state.nodes.get("[data-output-content-preview]")[0].textContent, "Content 作品数据暂不可用");

  state.page.unmount();
});

test("Reconnect failures show a specific V2 session recovery hint", async () => {
  const state = fixture();
  state.page.mount(state.root);
  await new Promise(resolve => setImmediate(resolve));
  state.failReconnect("staging_session_still_stopping");
  state.clickReconnect();
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(state.nodes.get("[data-output-native-notice]")[0].textContent,
    "V2 当前会话仍在停止，请稍等片刻后再重连。");
  state.page.unmount();
});

test("NAI failures in the existing image log show a Chinese explanation", async () => {
  const state = fixture();
  state.setObservations([{action: "image.generate", state: "failed",
    created_at: "2026-11-02T05:00:00Z", error_code: "image_usage_unavailable"}]);
  state.page.mount(state.root);
  await new Promise(resolve => setImmediate(resolve));
  const html = state.single.get("#outputObservationWorkspace").innerHTML;
  assert.match(html, /NAI 未提供费用信息，当前金额硬限制下未提交请求/);
  assert.doesNotMatch(html, /image_usage_unavailable/);
  state.page.unmount();
});

test("NAI output controls read plugin status and save independent generation and edit routes", async () => {
  const state = fixture({installed: true, available: true, call_mode: "openai",
    api_profile: "starbridge", default_model: "nai-diffusion-5-full", operations: ["generate", "edit"]});
  state.page.mount(state.root);
  await new Promise(resolve => setImmediate(resolve));
  const host = state.single.get("#outputNaiWorkspace");
  assert.match(host.innerHTML, /output-capability-card output-nai-backend/);
  assert.match(host.innerHTML, /采样参数和默认负面提示词均沿用/);
  assert.match(host.innerHTML, /星桥（Almoon）/);
  assert.match(host.innerHTML, /nai-diffusion-5-full/);
  assert.doesNotMatch(host.innerHTML, /data-output-nai-operation="edit"[^>]*disabled/);
  state.toggleNai("generate", true);
  state.toggleNai("edit", true);
  state.toggleNai("edit", false);
  state.clickAction("save");
  await new Promise(resolve => setImmediate(resolve));
  const saved = state.requests.find(item => item.endpoint.endsWith("/save"));
  assert.deepEqual(JSON.parse(JSON.stringify(saved.body.changes.operation_routes)),
    {generate: ["nai"], edit: []});
  assert.equal(saved.body.changes.enabled, true);
  state.page.unmount();
});

test("NAI direct mode keeps unsupported edit controls disabled", async () => {
  const state = fixture({installed: true, available: true, call_mode: "direct", operations: ["generate"]});
  state.page.mount(state.root);
  await new Promise(resolve => setImmediate(resolve));
  const html = state.single.get("#outputNaiWorkspace").innerHTML;
  assert.match(html, /data-output-nai-operation="edit" disabled/);
  assert.doesNotMatch(html, /data-output-nai-operation="generate" disabled/);
  state.page.unmount();
});

test("an uninstalled NAI plugin has no configuration controls or dead links", async () => {
  const state = fixture({installed: false, available: false, operations: []});
  state.page.mount(state.root);
  await new Promise(resolve => setImmediate(resolve));
  const host = state.single.get("#outputNaiWorkspace");
  assert.equal(host.hidden, true);
  assert.equal(host.innerHTML, "");
  state.page.unmount();
});

test("an installed but disabled NAI plugin shows the repair action without enabling generation", async () => {
  const state = fixture({installed: true, available: false, reason: "nai_plugin_disabled", operations: []});
  state.page.mount(state.root);
  await new Promise(resolve => setImmediate(resolve));
  const host = state.single.get("#outputNaiWorkspace");
  assert.equal(host.hidden, false);
  assert.match(host.innerHTML, /NAI 插件已停用/);
  assert.match(host.innerHTML, /data-output-nai-operation="generate" disabled/);
  assert.doesNotMatch(host.innerHTML, /href=/);
  state.page.unmount();
});

test("a role reread failure clears a previous NAI ready state", async () => {
  const state = fixture({installed: true, available: true, operations: ["generate"]});
  state.page.mount(state.root);
  await new Promise(resolve => setImmediate(resolve));
  state.failOutput(true);
  await state.page.refresh();
  assert.match(state.single.get("#outputNaiWorkspace").innerHTML, /NAI 状态暂不可读/);
  assert.doesNotMatch(state.single.get("#outputNaiWorkspace").innerHTML, /已就绪/);
  state.page.unmount();
});

test("NAI remains visible before account and persona setup without saving any routes", async () => {
  const state = fixture({installed: true, available: true, call_mode: "direct",
    default_model: "nai-diffusion-4-5-full", operations: ["generate"]});
  state.clearRole();
  state.page.mount(state.root);
  await new Promise(resolve => setImmediate(resolve));
  const html = state.single.get("[data-output-native-selector]").innerHTML
    + state.single.get("#outputNaiWorkspace").innerHTML;
  assert.match(html, /NAI 后端/);
  assert.match(html, /已就绪/);
  assert.match(html, /nai-diffusion-4-5-full/);
  assert.match(html, /配置账号并绑定人格/);
  assert.match(html, /data-manage="host"/);
  assert.match(html, /data-output-nai-operation="generate" disabled/);
  assert.match(html, /data-output-nai-operation="edit" disabled/);
  assert.equal(state.single.get(".output-image-status span").textContent, "选择账号与人格后显示最近产出");
  state.toggleNai("generate", true);
  state.clickAction("save");
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(state.requests.some(item => item.endpoint === "integration/manifest"));
  assert.ok(state.requests.every(item => item.method === "GET"));
  assert.ok(state.requests.every(item => !item.endpoint.startsWith("model/")));
  state.page.unmount();
});

test("a failed role configuration read keeps the NAI section visible with disabled controls", async () => {
  const state = fixture({installed: true, available: true, call_mode: "direct", operations: ["generate"]});
  state.failOutput(true);
  state.page.mount(state.root);
  await new Promise(resolve => setImmediate(resolve));
  const html = state.single.get("[data-output-native-selector]").innerHTML
    + state.single.get("#outputNaiWorkspace").innerHTML;
  assert.match(html, /角色配置暂不可读/);
  assert.match(html, /NAI 后端/);
  assert.match(html, /NAI 状态暂不可读/);
  assert.doesNotMatch(html, /未检测到已启用/);
  assert.match(html, /data-output-nai-operation="generate" disabled/);
  state.page.unmount();
});

test("unconfigured NAI rereads do not retain a stale ready state after a detection failure", async () => {
  const state = fixture({installed: true, available: true, call_mode: "direct", operations: ["generate"]});
  state.clearRole();
  state.page.mount(state.root);
  await new Promise(resolve => setImmediate(resolve));
  state.failManifest(true);
  await state.page.refresh();
  const html = state.single.get("#outputNaiWorkspace").innerHTML;
  assert.match(html, /NAI 状态暂不可读/);
  assert.doesNotMatch(html, /已就绪/);
  assert.match(html, /data-output-nai-operation="generate" disabled/);
  state.page.unmount();
});

test("output page renders the generated image gallery and opens a preview", async () => {
  const state = fixture();
  state.page.mount(state.root);
  await new Promise(resolve => setImmediate(resolve));

  const gallery = state.single.get("#outputAlbumWorkspace");
  assert.match(gallery.innerHTML, /output-album-card/);
  assert.match(gallery.innerHTML, /data-output-native-action="album-open"/);
  assert.match(gallery.innerHTML, /data:image\/png;base64,AAA/);

  state.clickAlbum(0);
  const lightbox = state.single.get("#outputAlbumLightbox");
  assert.equal(lightbox.hidden, false);
  assert.match(lightbox.innerHTML, /output-album-lightbox-card/);
  assert.match(lightbox.innerHTML, /1 \/ 1/);
  state.page.unmount();
});

test("gallery supports selection, export, delete and open-folder actions", async () => {
  const state = fixture();
  state.page.mount(state.root);
  await new Promise(resolve => setImmediate(resolve));

  const gallery = state.single.get("#outputAlbumWorkspace");
  assert.match(gallery.innerHTML, /导出选中/);
  assert.match(gallery.innerHTML, /删除选中/);
  assert.match(gallery.innerHTML, /打开数据目录/);

  state.toggleAlbum("img-1", true);
  assert.match(gallery.innerHTML, /已选 <b>1<\/b> 张/);

  state.clickAction("album-export");
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(state.requests.some(request => request.method === "POST" &&
    request.endpoint === "image-materials/album/export"));

  state.clickAction("album-hide");
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(state.requests.some(request => request.method === "POST" &&
    request.endpoint === "image-materials/album/hide"));
  assert.doesNotMatch(gallery.innerHTML, /output-album-card/);

  state.clickAction("album-open-folder");
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(state.requests.some(request => request.method === "POST" &&
    request.endpoint === "image-materials/album/open-folder"));
  state.page.unmount();
});

function multiAlbum(options = {}) {
  const image = (id, date) => ({image_id:id, thumbnail: `data:image/png;base64,${id}`, created_at:date});
  const targets = [
    {scope_kind: "private", roles:[{role_id: "role-a", image_material_api: "image-materials"}]},
    {scope_kind: "private", roles:[{role_id: "role-a", image_material_api: "wechat-materials"}]},
    {scope_kind: "private", roles:[{role_id: "role-a", image_material_api: "wechat-materials"}]},
    {scope_kind: "private", roles:[{role_id: "other", image_material_api: "other-materials"}]},
    {scope_kind: "group", roles:[{role_id: "role-a", image_material_api: "group-materials"}]},
  ];
  let fail = !!options.fail, failAction = "", hold = null;
  const state = fixture(null, {targets:options.targets||targets, request: async (method, endpoint, body) => {
    const native = endpoint.startsWith("wechat-materials/");
    if (native && (fail || (failAction && endpoint.endsWith(failAction)))) throw new Error("synthetic native unavailable");
    if (method === "GET") {
      if (options.hold && native) return new Promise(resolve => {hold = resolve;});
      if (native) return {items:[image(body?.cursor ? "wx-old" : "wx-new", body?.cursor ? "2026-10-01" : "2026-11-12")], next_cursor:body?.cursor ? null : "wx-cursor"};
      return {items:[image("qq-image", "2026-11-11")],next_cursor:null};
    }
    const expected = native ? ["wx-new", "wx-old"] : ["qq-image"];
    for (const id of body?.image_ids || (body?.image_id ? [body.image_id] : [])) assert.ok(expected.includes(id), `wrong account: ${endpoint}: ${id}`);
    if (endpoint.endsWith("/export")) return {filename:"album.zip", data_url:"data:application/zip;base64,AAA", count:body.image_ids.length};
    if (endpoint.endsWith("/hide")) return {hidden:body.image_ids.length};
    if (endpoint.endsWith("/references")) return {};
    throw new Error(`unexpected ${endpoint}`);
  }});
  return {...state, recover: () => {fail=false;failAction="";}, failAction: action => {failAction=action;}, release: () => hold?.({items:[image("wx-delayed", "2026-11-12")],next_cursor:null})};
}
const settleAlbum = () => new Promise(resolve => setImmediate(resolve));

test("role gallery includes QQ and WeChat in the existing gallery without an account switch", async () => {
  const s=multiAlbum();s.page.mount(s.root);await settleAlbum();
  const html=s.single.get("#outputAlbumWorkspace").innerHTML;
  assert.match(html,/wx-new/);assert.match(html,/qq-image/);
  assert.ok(html.indexOf('base64,wx-new') < html.indexOf('base64,qq-image'));
  assert.equal(s.requests.filter(r=>r.method==="GET"&&r.endpoint==="wechat-materials/album").length,1);
  assert.ok(!s.requests.some(r=>/other-materials|group-materials/.test(r.endpoint)));
  assert.doesNotMatch(html,/<select|account-switch/);
  s.clickAction("album-more");await settleAlbum();
  assert.match(s.single.get("#outputAlbumWorkspace").innerHTML,/wx-old/);
  assert.equal(s.requests.filter(r=>r.method==="GET"&&r.endpoint==="image-materials/album").length,1);
  assert.equal(s.requests.find(r=>r.body?.cursor).body.cursor,"wx-cursor");
  s.page.unmount();
});

test("role gallery sends exports hide and references to each image's original account", async () => {
  const s=multiAlbum();s.page.mount(s.root);await settleAlbum();
  s.toggleAlbum("wx-new");s.clickAction("album-reference");s.clickAction("album-reference-save");await settleAlbum();
  assert.ok(s.requests.some(r=>r.endpoint==="wechat-materials/references"&&r.body.image_id==="wx-new"));
  s.toggleAlbum("qq-image");s.clickAction("album-export");await settleAlbum();
  assert.equal(s.requests.filter(r=>r.endpoint.endsWith("/export")).length,2);
  s.clickAction("album-hide");await settleAlbum();
  assert.equal(s.requests.filter(r=>r.endpoint.endsWith("/hide")).length,2);
  assert.doesNotMatch(s.single.get("#outputAlbumWorkspace").innerHTML,/output-album-card/);s.page.unmount();
});

test("role gallery reports a failed account while retaining successful images and permits retry", async () => {
  const s=multiAlbum({fail:true});s.page.mount(s.root);await settleAlbum();
  let html=s.single.get("#outputAlbumWorkspace").innerHTML;assert.match(html,/qq-image/);assert.match(html,/synthetic native unavailable/);
  s.recover();s.clickAction("album-more");await settleAlbum();
  html=s.single.get("#outputAlbumWorkspace").innerHTML;assert.match(html,/wx-new/);assert.doesNotMatch(html,/synthetic native unavailable/);s.page.unmount();
});

test("role gallery discards late multi account responses after unmount", async () => {
  const s=multiAlbum({hold:true});s.page.mount(s.root);await settleAlbum();s.page.unmount();
  const html=s.single.get("#outputAlbumWorkspace").innerHTML;s.release();await settleAlbum();
  assert.equal(s.single.get("#outputAlbumWorkspace").innerHTML,html);
});

test("role gallery preserves the failed account selection after a partial hide", async () => {
  const s=multiAlbum();s.page.mount(s.root);await settleAlbum();
  s.toggleAlbum("qq-image");s.toggleAlbum("wx-new");s.failAction("/hide");
  s.clickAction("album-hide");await settleAlbum();
  let html=s.single.get("#outputAlbumWorkspace").innerHTML;
  assert.match(html,/wx-new/);assert.doesNotMatch(html,/qq-image/);assert.match(html,/部分删除未确认/);
  assert.match(html,/已选 <b>1<\/b> 张/);
  s.recover();s.clickAction("album-hide");await settleAlbum();
  assert.equal(s.requests.filter(r=>r.endpoint==="image-materials/album/hide").length,1);
  assert.equal(s.requests.filter(r=>r.endpoint==="wechat-materials/album/hide").length,2);s.page.unmount();
});

test("role gallery reports partial export failures without automatically resending", async () => {
  const s=multiAlbum();s.page.mount(s.root);await settleAlbum();
  s.toggleAlbum("qq-image");s.toggleAlbum("wx-new");s.failAction("/export");
  s.clickAction("album-export");await settleAlbum();
  const html=s.single.get("#outputAlbumWorkspace").innerHTML;
  assert.match(html,/部分导出失败/);assert.match(html,/wx-new/);assert.match(html,/qq-image/);
  assert.equal(s.requests.filter(r=>r.endpoint.endsWith("/export")).length,2);s.page.unmount();
});

test("role gallery reads one album per account even when the directory lists setup and concrete windows", async () => {
  const role=endpoint=>({role_id:"role-a",image_material_api:endpoint});
  const target=(id,endpoint)=>({scope_kind:"private",platform_id:id,account_id:id,bot_id:`bot-${id}`,roles:[role(endpoint)]});
  const s=multiAlbum({targets:[target("qq","image-materials"),target("wx","wechat-materials"),target("wx","alias-materials")]});
  s.page.mount(s.root);await settleAlbum();
  assert.match(s.single.get("#outputAlbumWorkspace").innerHTML,/wx-new/);
  assert.ok(!s.requests.some(r=>r.endpoint.startsWith("alias-materials")));s.page.unmount();
});
