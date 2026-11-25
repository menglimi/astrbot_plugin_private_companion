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
  let scope = "role-a";
  const window = {
    CompanionConfirm: album.confirm || (async () => true),
    CompanionProduct: {
      scopeKey: () => scope,
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
        if (endpoint === "image-materials/album/open-folder") return {
          path: "C:\\data\\media\\generated-album\\role-a",
          opened: true,
          synced_count: 1,
          total_count: 1,
        };
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
    changeScope(value) {scope = value;},
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
  assert.match(gallery.innerHTML, /打开图库目录/);
  assert.doesNotMatch(gallery.innerHTML, /打开数据目录/);
  assert.doesNotMatch(gallery.innerHTML, /data-output-native-action="album-refresh"/);

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
  assert.match(gallery.innerHTML, /已打开产出图库目录（已同步 1 张，共 1 张）：C:\\data\\media\\generated-album\\role-a/);
  state.page.unmount();
});

test("album delete waits for the page dialog and refuses a stale role", async () => {
  let answer;
  const state = fixture(null, {confirm: options => {
    assert.equal(options.danger, true);
    return new Promise(resolve => {answer = resolve;});
  }});
  state.page.mount(state.root);
  await settleAlbum();
  state.toggleAlbum("img-1", true);
  state.clickAction("album-hide");
  assert.equal(typeof answer, "function");
  answer(false);
  await settleAlbum();
  state.clickAction("album-hide");
  state.changeScope("role-b");
  answer(true);
  await settleAlbum();
  assert.ok(!state.requests.some(request => request.endpoint.endsWith("/hide")));
  state.page.unmount();
});

test("open-folder notice reports existing count without sync noise and maps album folder errors", async () => {
  let mode = "already-synced";
  const state = fixture(null, {
    request: async (method, endpoint) => {
      if (method === "GET" && endpoint === "image-materials/album") {
        return {items: [{image_id: "img-1", thumbnail: "data:image/png;base64,AAA", width: 512, height: 512,
          created_at: "2026-10-12T04:05:00Z", action: "image.generate"}], next_cursor: null};
      }
      if (method === "POST" && endpoint === "image-materials/album/open-folder") {
        if (mode === "already-synced") {
          return {path: "C:\\data\\media\\generated-album\\role-a", opened: true, synced_count: 0, total_count: 3};
        }
        throw Object.assign(new Error("failed"), {code: "generated_album_open_failed"});
      }
      throw new Error(`unexpected ${method} ${endpoint}`);
    },
  });
  state.page.mount(state.root);
  await new Promise(resolve => setImmediate(resolve));

  state.clickAction("album-open-folder");
  await new Promise(resolve => setImmediate(resolve));
  const gallery = state.single.get("#outputAlbumWorkspace");
  assert.match(gallery.innerHTML, /已打开产出图库目录（共 3 张）：C:\\data\\media\\generated-album\\role-a/);

  mode = "error";
  state.clickAction("album-open-folder");
  await new Promise(resolve => setImmediate(resolve));
  assert.match(gallery.innerHTML, /无法打开产出图库目录，请检查运行环境权限。/);
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

function fullGalleryFixture(options = {}) {
  const nodes = new Map(selectors.map(selector => [selector, [{textContent: "初始值", hidden: false,
    classList: {toggle() {}}}]]));
  const sentinelNode = {dataset: {outputAlbumSentinel: ""}};
  const single = new Map([
    ["#outputAlbumWorkspace", {innerHTML: "", hidden: false, scrolled: false, scrollIntoView() { this.scrolled = true; }}],
    ["#outputAlbumLightbox", {innerHTML: "", hidden: true}],
    ["[data-output-album-sentinel]", sentinelNode],
    ["[data-output-preview-image]", {hidden: true, src: "", alt: "", removeAttribute() {}}],
    [".output-image-frame", {dataset: {}}],
    [".output-image-status span", {textContent: ""}],
    ["[data-output-native-selector]", {innerHTML: ""}],
    ["#outputNaiWorkspace", {innerHTML: ""}],
    ["#outputObservationWorkspace", {innerHTML: ""}],
  ]);
  const requests = [], listeners = new Map(), windowListeners = new Map();
  const createdUrls = [], revokedUrls = [], observedSentinels = [];
  let observerCallback = null, observerDisconnected = 0, blobCounter = 0;
  let clipboardText = "";
  const role = {role_id: "role-a", model_api: "model", image_material_api: "image-materials"};
  const window = {
    CompanionProduct: {
      scopeKey: () => "role-a",
      role: () => role,
      targets: () => [],
      relative: value => value,
      async request(method, endpoint, body, requestOptions) {
        requests.push({method, endpoint, body, requestOptions});
        if (options.request && /\/album(\/|$)/.test(endpoint)) return options.request(method, endpoint, body, requestOptions);
        if (endpoint === "model/image/observability") return {images: {total: 0, items: []}, content: {available: false}};
        if (endpoint === "model/image/output") return {settings: {backend: "auto", enabled: false, endpoints: [], comfyui: {}, sd_webui: {}, operation_routes: {generate: [], edit: []}}, nai: null, revision: "r1", host_registered: false};
        if (endpoint === "model/image/review/providers") return {providers: []};
        throw new Error(`unexpected request: ${method} ${endpoint}`);
      },
    },
    addEventListener(type, fn) { windowListeners.set(type, fn); },
    removeEventListener() {},
    dispatchEvent() {},
    document: {createElement: () => ({click() {}, remove() {}, select() {}, style: {}}), body: {appendChild() {}}},
  };
  const root = {
    isConnected: true,
    addEventListener(type, listener) { listeners.set(type, listener); },
    removeEventListener() {},
    contains() { return true; },
    querySelector(selector) { return single.get(selector) || null; },
    querySelectorAll(selector) { return nodes.get(selector) || []; },
  };
  class FakeIntersectionObserver {
    constructor(cb, init) { observerCallback = cb; this.init = init; }
    observe(el) { observedSentinels.push(el); }
    disconnect() { observerDisconnected += 1; }
  }
  class FakeBlob { constructor(parts, opts) { this.parts = parts; this.type = opts?.type || ""; } }
  const FakeURL = {
    createObjectURL(blob) { const url = `blob:companion-${++blobCounter}`; createdUrls.push({url, type: blob.type}); return url; },
    revokeObjectURL(url) { revokedUrls.push(url); },
  };
  const context = {
    window, document: window.document,
    navigator: {clipboard: {writeText: async text => { clipboardText = text; }}},
    IntersectionObserver: FakeIntersectionObserver,
    Blob: FakeBlob, URL: FakeURL, atob: str => Buffer.from(str, "base64").toString("binary"), Uint8Array,
    crypto: {randomUUID: () => "synthetic-op"}, console, AbortController, Event, setTimeout, clearTimeout,
    Promise, Math, Date, JSON, Object, String, Array, Map, Set, Error, Number, Boolean,
  };
  vm.runInNewContext(source, context, {filename: "output-page.js"});
  return {
    page: window.CompanionOutputPage,
    single, requests, root, createdUrls, revokedUrls, observedSentinels,
    get clipboardText() { return clipboardText; },
    get observerDisconnected() { return observerDisconnected; },
    triggerSentinel() { observerCallback?.([{isIntersecting: true}]); },
    clickAction(action, dataset = {}) {
      const target = {disabled: false, dataset: {outputNativeAction: action, ...dataset}};
      listeners.get("click")?.({target: {closest: () => target}});
    },
    toggleAutoLoad(checked) {
      const target = {matches: selector => selector === "[data-output-album-autoload]", checked};
      listeners.get("change")?.({target});
    },
    pressKey(key) {
      let prevented = false;
      windowListeners.get("keydown")?.({key, target: {tagName: "BODY"}, preventDefault() { prevented = true; }});
      return prevented;
    },
  };
}

test("gallery top bar renders total count, page sizes, type filter, auto-scroll sentinel, and batch load without dropping lightbox selection", async () => {
  const makeItem = (idx, action = "image.generate") => ({
    image_id: `img-${idx}`,
    filename: `20261012-040506-generate-${String(idx).padStart(12, "0")}.png`,
    mime_type: "image/png",
    size_bytes: 204800,
    width: 1024,
    height: 1536,
    created_at: `2026-10-${String(30 - idx).padStart(2, "0")}T04:05:06Z`,
    action,
    thumbnail: `data:image/jpeg;base64,thumb-${idx}`,
  });
  let pagesServed = 0;
  const s = fullGalleryFixture({
    request: async (method, endpoint, body) => {
      if (method === "GET" && endpoint === "image-materials/album") {
        pagesServed += 1;
        if (!body?.cursor) {
          return {items: [makeItem(1, "image.generate"), makeItem(2, "image.edit")], next_cursor: "cursor-page-2", total: 5};
        }
        if (body.cursor === "cursor-page-2") {
          return {items: [makeItem(3, "image.generate"), makeItem(4, "image.mask_edit")], next_cursor: "cursor-page-3", total: 5};
        }
        return {items: [makeItem(5, "image.generate")], next_cursor: null, total: 5};
      }
      if (method === "GET" && endpoint === "image-materials/album/image") {
        return {
          image_id: body.image_id,
          filename: `full-${body.image_id}.png`,
          mime_type: "image/png",
          width: 1024,
          height: 1536,
          size_bytes: 2500000,
          created_at: "2026-10-29T04:05:06Z",
          action: "image.generate",
          data_url: "data:image/png;base64,iVBORw0KGgo=",
        };
      }
      throw new Error(`unexpected ${method} ${endpoint}`);
    },
  });
  s.page.mount(s.root);
  await settleAlbum();

  const gallery = s.single.get("#outputAlbumWorkspace");
  assert.match(gallery.innerHTML, /已加载 <b>2<\/b> \/ 共 <b>5<\/b> 张/);
  assert.match(gallery.innerHTML, /data-output-album-size="24"/);
  assert.match(gallery.innerHTML, /data-output-album-size="48"/);
  assert.match(gallery.innerHTML, /data-output-album-size="60"/);
  assert.equal(s.requests.find(r => r.endpoint === "image-materials/album").body.limit, 24);
  assert.ok(s.observedSentinels.length >= 1, "auto-scroll observer attaches to sentinel by default");

  // Open Lightbox on image #1, then trigger auto-scroll append: Lightbox MUST stay open on img-1!
  s.clickAction("album-open", {outputImageIndex: "0"});
  await settleAlbum();
  assert.equal(s.single.get("#outputAlbumLightbox").hidden, false);
  assert.match(s.single.get("#outputAlbumLightbox").innerHTML, /1 \/ 2/);

  s.triggerSentinel();
  await settleAlbum();
  assert.match(gallery.innerHTML, /已加载 <b>4<\/b> \/ 共 <b>5<\/b> 张/);
  assert.equal(s.single.get("#outputAlbumLightbox").hidden, false, "appending pages must preserve open lightbox");
  assert.match(s.single.get("#outputAlbumLightbox").innerHTML, /1 \/ 4/);

  // Type filter narrows visible cards without losing loaded items.
  s.clickAction("album-filter", {outputAlbumFilter: "edit"});
  assert.match(gallery.innerHTML, /当前筛选显示 2 张/);
  assert.doesNotMatch(gallery.innerHTML, /thumb-1/);
  assert.match(gallery.innerHTML, /thumb-2/);
  assert.match(gallery.innerHTML, /thumb-4/);
  s.clickAction("album-filter", {outputAlbumFilter: "all"});

  // Batch load fetches remaining page(s) and stops cleanly.
  s.clickAction("album-load-batch");
  await settleAlbum();
  await settleAlbum();
  assert.match(gallery.innerHTML, /已加载 <b>5<\/b> \/ 共 <b>5<\/b> 张/);
  assert.match(gallery.innerHTML, /已全部加载/);

  // Switching page size to 48 reloads with limit=48.
  s.clickAction("album-page-size", {outputAlbumSize: "48"});
  await settleAlbum();
  assert.equal(s.requests.filter(r => r.endpoint === "image-materials/album").at(-1).body.limit, 48);

  // Turning off auto-scroll disconnects IntersectionObserver.
  const beforeDisconnect = s.observerDisconnected;
  s.toggleAutoLoad(false);
  assert.ok(s.observerDisconnected > beforeDisconnect);

  s.page.unmount();
});

test("lightbox loads full-res Blob URL with LRU eviction, zoom, keyboard nav, filename copy, and download link", async () => {
  const items = Array.from({length: 14}, (_, idx) => ({
    image_id: `img-${idx + 1}`,
    filename: `20261012-040506-generate-${String(idx + 1).padStart(12, "0")}.png`,
    mime_type: "image/png",
    size_bytes: 1536000,
    width: 1024,
    height: 1536,
    created_at: `2026-10-${String(28 - idx).padStart(2, "0")}T04:05:06Z`,
    action: "image.generate",
    thumbnail: `data:image/jpeg;base64,thumb-${idx + 1}`,
  }));
  let releaseFirst = null;
  const s = fullGalleryFixture({
    request: async (method, endpoint, body) => {
      if (method === "GET" && endpoint === "image-materials/album") {
        return {items, next_cursor: null, total: items.length};
      }
      if (method === "GET" && endpoint === "image-materials/album/image") {
        const payload = {
          image_id: body.image_id,
          filename: `20261012-040506-generate-${body.image_id}.png`,
          mime_type: "image/png",
          width: 1024,
          height: 1536,
          size_bytes: 2516582,
          created_at: "2026-10-28T04:05:06Z",
          action: "image.generate",
          data_url: "data:image/png;base64,iVBORw0KGgo=",
        };
        if (body.image_id === "img-1" && !releaseFirst) {
          return new Promise(resolve => { releaseFirst = () => resolve(payload); });
        }
        return payload;
      }
      throw new Error(`unexpected ${method} ${endpoint}`);
    },
  });
  s.page.mount(s.root);
  await settleAlbum();

  // Instant open shows thumbnail + loading badge before full image resolves.
  s.clickAction("album-open", {outputImageIndex: "0"});
  const lb = s.single.get("#outputAlbumLightbox");
  assert.equal(lb.hidden, false);
  assert.match(lb.innerHTML, /thumb-1/);
  assert.match(lb.innerHTML, /加载原图中…/);

  // Resolving /album/image upgrades seamlessly to Blob URL, full filename, size, and <a download>.
  releaseFirst();
  await settleAlbum();
  assert.match(lb.innerHTML, /blob:companion-1/);
  assert.match(lb.innerHTML, /高清原图/);
  assert.match(lb.innerHTML, /20261012-040506-generate-img-1\.png/);
  assert.match(lb.innerHTML, /1024 × 1536 · PNG · 2\.40 MB/);
  assert.match(lb.innerHTML, /<a[^>]+href="blob:companion-1"[^>]+download="20261012-040506-generate-img-1\.png"/);

  // Copy filename action copies full filename.
  s.clickAction("album-copy-name", {outputFilename: "20261012-040506-generate-img-1.png"});
  await settleAlbum();
  assert.equal(s.clipboardText, "20261012-040506-generate-img-1.png");

  // Click zoom toggles is-zoomed class.
  s.clickAction("album-zoom");
  assert.match(lb.innerHTML, /output-album-lightbox-stage is-zoomed/);
  s.clickAction("album-zoom");
  assert.doesNotMatch(lb.innerHTML, /is-zoomed/);

  // Keyboard ArrowRight / ArrowLeft steps through images; stepping through 13+ evicts LRU > 12 via revokeObjectURL.
  for (let step = 1; step < 14; step += 1) {
    assert.equal(s.pressKey("ArrowRight"), true);
    await settleAlbum();
  }
  assert.equal(s.createdUrls.length, 14);
  assert.equal(s.revokedUrls.length, 2, "LRU cache capped at 12 revokes oldest 2 Blob URLs");
  assert.equal(s.revokedUrls[0], "blob:companion-1");
  assert.equal(s.revokedUrls[1], "blob:companion-2");

  assert.equal(s.pressKey("ArrowLeft"), true);
  await settleAlbum();
  assert.match(lb.innerHTML, /13 \/ 14/);

  // Escape closes lightbox cleanly.
  assert.equal(s.pressKey("Escape"), true);
  assert.equal(lb.hidden, true);

  // Unmount revokes all remaining cached Blob URLs.
  s.page.unmount();
  assert.equal(s.revokedUrls.length, 14);
});

test("gallery supports daily outfit filter pill and standalone openLightbox relocates host and loads full-res outfit", async () => {
  const outfitId = "wardrobe-outfit:a1b2c3d4:2026-11-22:deadbeef";
  const outfitFile = "20261122-013000-wardrobe-outfit-0123456789ab.png";
  const s = fullGalleryFixture({
    request(method, endpoint, body) {
      if (method === "GET" && endpoint === "image-materials/album") {
        return {
          total: 2,
          next_cursor: null,
          items: [
            {
              image_id: outfitId,
              filename: outfitFile,
              mime_type: "image/png",
              size_bytes: 1_500_000,
              width: 832,
              height: 1216,
              created_at: "2026-11-22T01:30:00Z",
              action: "wardrobe.outfit",
              local_date: "2026-11-22",
              weather: {role_location: "维也纳", condition: "晴", temperature_c: 3},
              activities: ["午间开指热身", "冬季巡演曲目定稿"],
              thumbnail: "data:image/jpeg;base64,outfit-thumb",
            },
            {
              image_id: "img-gen-1",
              filename: "20261121-100000-generate-111111111111.png",
              mime_type: "image/png",
              size_bytes: 500_000,
              width: 768,
              height: 1024,
              created_at: "2026-11-21T10:00:00Z",
              action: "image.generate",
              thumbnail: "data:image/jpeg;base64,gen-thumb",
            },
          ],
        };
      }
      if (method === "GET" && endpoint === "image-materials/album/image") {
        return {
          image_id: body.image_id,
          filename: outfitFile,
          mime_type: "image/png",
          width: 832,
          height: 1216,
          size_bytes: 1_500_000,
          created_at: "2026-11-22T01:30:00Z",
          action: "wardrobe.outfit",
          local_date: "2026-11-22",
          weather: {role_location: "维也纳", condition: "晴", temperature_c: 3},
          activities: ["午间开指热身", "冬季巡演曲目定稿"],
          data_url: "data:image/png;base64,aGVsbG8=",
        };
      }
      throw new Error(`unexpected ${method} ${endpoint}`);
    },
  });
  // Simulate #outputAlbumLightbox starting inside hidden #output-panel-gallery so ensureLightboxHost relocates it.
  const lb = s.single.get("#outputAlbumLightbox");
  lb.parentElement = {id: "output-panel-gallery"};
  let relocatedToRoot = false;
  s.root.appendChild = node => {
    if (node === lb) {
      lb.parentElement = s.root;
      relocatedToRoot = true;
    }
  };

  s.page.mount(s.root);
  await settleAlbum();
  const ws = s.single.get("#outputAlbumWorkspace");
  assert.match(ws.innerHTML, /data-output-album-filter="wardrobe\.outfit"/);
  assert.match(ws.innerHTML, /output-album-card-badge">每日穿搭</);

  // Filter down to daily outfits only.
  s.clickAction("album-filter", {outputAlbumFilter: "wardrobe.outfit"});
  assert.match(ws.innerHTML, /当前筛选显示 1 张/);
  assert.match(ws.innerHTML, /2026-11-22/);
  assert.doesNotMatch(ws.innerHTML, /img-gen-1/);

  // Standalone openLightbox (invoked from daily outfit card in materials tab) relocates host & loads full-res PNG.
  const opened = s.page.openLightbox({
    image_id: outfitId,
    filename: outfitFile,
    mime_type: "image/png",
    width: 832,
    height: 1216,
    size_bytes: 1_500_000,
    created_at: "2026-11-22T01:30:00Z",
    action: "wardrobe.outfit",
    local_date: "2026-11-22",
    weather: {role_location: "维也纳", condition: "晴", temperature_c: 3},
    activities: ["午间开指热身", "冬季巡演曲目定稿"],
    thumbnail: "data:image/jpeg;base64,outfit-thumb",
    _albumEndpoint: "image-materials/album",
  });
  assert.equal(opened, true);
  assert.equal(relocatedToRoot, true, "lightbox must relocate out of hidden gallery panel to .output-page root");
  assert.equal(lb.hidden, false);
  await settleAlbum();
  assert.match(lb.innerHTML, /每日穿搭图/);
  assert.match(lb.innerHTML, /维也纳 · 晴 · 3°C/);
  assert.match(lb.innerHTML, /午间开指热身、冬季巡演曲目定稿/);
  assert.match(lb.innerHTML, /blob:companion-1/);
  assert.match(lb.innerHTML, /<a[^>]+href="blob:companion-1"[^>]+download="20261122-013000-wardrobe-outfit-0123456789ab\.png"/);
  assert.equal(s.pressKey("Escape"), true);
  assert.equal(lb.hidden, true);
  s.page.unmount();
});

test("Switching album filter clears hidden selection so bulk actions cannot affect filtered-out items", async () => {
  const s = fixture(null, {
    request(method, endpoint, body) {
      if (endpoint === "image-materials/album") {
        return {
          items: [
            {
              image_id: "img-gen-1",
              thumbnail: "data:image/png;base64,GEN",
              width: 512,
              height: 512,
              created_at: "2026-11-22T02:00:00Z",
              action: "image.generate",
            },
            {
              image_id: "img-edit-1",
              thumbnail: "data:image/png;base64,EDIT",
              width: 512,
              height: 512,
              created_at: "2026-11-22T01:00:00Z",
              action: "image.edit",
            },
          ],
          next_cursor: null,
          total: 2,
        };
      }
      if (endpoint === "image-materials/album/hide") return {hidden: body.image_ids.length};
      if (endpoint === "image-materials/album/export") {
        return {filename: "album.zip", data_url: "data:application/zip;base64,AAA", count: body.image_ids.length};
      }
      throw new Error(`unexpected ${method} ${endpoint}`);
    },
  });

  s.page.mount(s.root);
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  const ws = s.single.get("#outputAlbumWorkspace");

  // Select a generated image under "all", then switch filter to "edit".
  s.toggleAlbum("img-gen-1", true);
  assert.match(ws.innerHTML, /已选 <b>1<\/b> 张/);

  s.clickAction("album-filter", {outputAlbumFilter: "edit"});
  assert.match(ws.innerHTML, /当前筛选显示 1 张/);
  assert.match(ws.innerHTML, /已选 <b>0<\/b> 张/);

  // Attempting bulk hide/export after filter switch must not send any request for the hidden image.
  s.clickAction("album-hide");
  s.clickAction("album-export");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(
    s.requests.some(req => req.endpoint.endsWith("/hide") || req.endpoint.endsWith("/export")),
    false,
    "bulk actions must not touch images hidden by the active filter",
  );
  s.page.unmount();
});
