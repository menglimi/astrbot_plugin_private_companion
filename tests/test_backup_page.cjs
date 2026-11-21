/* 备份与还原工作区：桥没就位时那个「重新读取」按钮到底做了什么。
 *
 * 这个模块此前没有测试，而它踩过一个只有运行到才能发现的坑：
 *
 *   const wait = product?.whenBridgeReady?.() ?? Promise.resolve(window.AstrBotPluginPage);
 *
 * `??` 的右半边会**立刻求值**。product 没提供等待器时（页面先于
 * product-page.js 就绪、或换了个不提供这个方法的实现），左半边是 undefined，
 * 于是捕到的是 `Promise.resolve(undefined)`——一个**成功**的 promise，
 * 里面躺着 undefined。接着 `.then` 里 `found?.apiGet` 判空抛出
 * bridge_unavailable，用户点了「重新读取」得到一句「还没接上」，
 * 而真正该发生的「去等桥」根本没发生。
 *
 * 断言的形状与其余页面一致：`bridgeNow() || bridgeFor()`——
 * 同步看一眼不抛，只有真没出现才等。
 *
 * 用 node --test 运行。DOM 是手搓替身，不引 jsdom。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "pages", "陪伴小屋", "backup-page.js"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));
/* load() 里要过「取桥 → 两个请求 → 重绘」几拍，单次 flush 不够。 */
const settle = async (rounds = 6) => { for (let i = 0; i < rounds; i += 1) await flush(); };

/* 最小 DOM。只够 mount + 点击委托跑起来：页面写 root.innerHTML，
   读 root.querySelector("#backupNote")，并往 root 上挂 click / input。 */
function element(attributes = {}) {
  const node = {
    _attributes: {...attributes},
    _listeners: {},
    innerHTML: "",
    value: "",
    /* live() 会看 root?.isConnected——缺了它 paint() 会静默早退，
       页面什么都不渲染，断言全部落空。 */
    isConnected: true,
    addEventListener(type, handler) { this._listeners[type] = handler; },
    removeEventListener(type) { delete this._listeners[type]; },
    hasAttribute(name) { return name in this._attributes; },
    getAttribute(name) { return this._attributes[name] ?? null; },
    closest(selector) {
      /* 页面用逗号分隔的属性选择器找按钮。替身只需回答「是不是这个按钮」。 */
      const names = selector.split(",").map(part =>
        part.trim().replace(/^\[|\]$/g, ""));
      return names.some(name => name in this._attributes) ? this : null;
    },
    querySelector() { return null; },
    contains() { return true; },
    fire(type, event) { (this._listeners[type] || (() => {}))(event); },
  };
  return node;
}

function harness({ product, bridge } = {}) {
  const window = {};
  if (product) window.CompanionProduct = product;
  if (bridge) window.AstrBotPluginPage = bridge;
  const document = {addEventListener() {}, removeEventListener() {}};
  vm.runInNewContext(SOURCE, {window, document, setTimeout, clearTimeout,
    Promise, Object, JSON, Array, String, Number, Boolean, Math, Date, Error,
    console, RegExp, encodeURIComponent, decodeURIComponent});
  return window.CompanionBackupPage;
}

/* 一个「桥还没出现」的 product 替身，记下谁被调用过。 */
function waitingProduct(found) {
  const calls = [];
  return {
    calls,
    bridgeNow() { calls.push("bridgeNow"); return null; },
    bridgeFor() {
      calls.push("bridgeFor");
      return found
        ? Promise.resolve(found)
        : Promise.reject(Object.assign(new Error("bridge_unavailable"),
            {code: "bridge_unavailable"}));
    },
  };
}

test("桥缺席时不会永久停在「正在读取」", async () => {
  /* 修之前的行为：mount 看到 !bridge 就直接 paint() 返回，而 data.loading
     仍是 true，于是渲染出来只有一句「正在读取快照列表…」——没有说明、没有出口，
     桥永远不来就永远停在那里。而那句注释还写着「给一个重新读取的入口」，
     实际并不存在。 */
  const product = waitingProduct(null);
  const page = harness({product});
  const root = element();

  page.mount(root);
  await settle();

  assert.doesNotMatch(root.innerHTML, /正在读取快照列表/,
    "读不到时不该停在加载态");
  assert.match(root.innerHTML, /data-backup-retry/,
    `读不到就应当给一个出口，实际渲染：${root.innerHTML.slice(0, 200)}`);
});

test("桥已经在时读得到列表，直接渲染内容", async () => {
  const bridge = {apiGet() { return Promise.resolve({status: "ok", data: {snapshots: []}}); },
    apiPost() { return Promise.resolve({status: "ok", data: {}}); }};
  const product = waitingProduct(bridge);
  const page = harness({product});
  const root = element();

  page.mount(root);
  await settle();

  assert.match(root.innerHTML, /暂无快照/,
    "读到空列表时使用简洁空态");
  assert.doesNotMatch(root.innerHTML, /data-backup-retry/,
    "读到了就不该摆重试按钮");
});

test("点重新读取会重新取桥", async () => {
  /* `??` 那个坑的护栏：只问一次 window.AstrBotPluginPage 的实现不会去等，
     用户点重新读取只会再得一句没接上。 */
  const product = waitingProduct(null);
  const page = harness({product});
  const root = element();

  page.mount(root);
  await settle();
  product.calls.length = 0;

  root.fire("click", {target: element({"data-backup-retry": ""})});
  await settle();

  assert.ok(product.calls.includes("bridgeNow"),
    `点重新读取应当重新取桥，实际只调了 ${JSON.stringify(product.calls)}`);
});

test("桥已经在时不去等，直接用", async () => {
  const bridge = {apiGet() { return Promise.resolve({status: "ok", data: {snapshots: []}}); },
    apiPost() { return Promise.resolve({status: "ok", data: {}}); }};
  let waited = false;
  const product = {
    bridgeNow() { return bridge; },
    bridgeFor() { waited = true; return Promise.resolve(bridge); },
  };
  const page = harness({product});
  const root = element();
  page.mount(root);

  const retry = element({"data-backup-retry": ""});
  root.fire("click", {target: retry});
  await flush();
  assert.strictEqual(waited, false, "桥已在就不该多等一拍，那会拖慢每一次读取");
});

test("桥等不到时如实报「没接上」，不抛类型错误", async () => {
  const product = waitingProduct(null);
  const page = harness({product});
  const root = element();
  page.mount(root);

  root.fire("click", {target: element({"data-backup-retry": ""})});
  await settle();

  assert.match(root.innerHTML, /还没有接上|没有接上/,
    "应当说清是桥没接上，而不是笼统的「读取失败」");
  assert.doesNotMatch(root.innerHTML, /Cannot read propert|undefined is not/,
    "不该把类型错误泄到界面上");
});

test("product 没提供等待器时也不静默变成「已接上」", () => {
  /* `??` 的右半边返回 Promise.resolve(undefined) 时，found 是 undefined，
     等价于「没有桥」——但代码路径上它被当成一个「成功的等待」。
     正确的形状应当落到拒绝分支。这里用没有 bridgeFor 的 product 复现。 */
  const product = {bridgeNow() { return null; }};
  const page = harness({product});
  const root = element();
  page.mount(root);

  root.fire("click", {target: element({"data-backup-retry": ""})});

  /* 同步阶段不该抛出去；异步起来后应当落到错误态。 */
  return settle().then(() => {
    assert.match(root.innerHTML, /还没有接上|没有接上|读取|失败|重试/,
      "缺等待器时应当落到错误态，而不是停在「正在读取」");
  });
});

/* 7.0.7 的旧格式快照只能还原主库。后端要求暂存时显式带上 confirmed_scope，
   否则以 backup_legacy_confirmation_required 拒绝——这是有意让「一次只还原
   主库」必须由人确认。页面若漏传，用户会永远卡在「确认暂存」那一步。 */
test("旧格式快照：预览说明只还原主库，暂存回传 confirmed_scope", async () => {
  const identifier = "a".repeat(32);
  const hash = "h".repeat(64);
  const calls = [];
  const bridge = {
    apiGet(route) {
      if (route === "backup") {
        return Promise.resolve({status: "ok", data: {problems: [], snapshots: [{
          snapshot_id: identifier, format: "companion-v2.installation-backup.v1",
          created_at: "2026-10-01T00:00:00+00:00", note: "旧备份", byte_size: 1024,
          artifact_count: 1, installation_id: "x", schema_version: "s",
          content_hash: hash, restorable: true, restore_scope: "main_database_only"}]}});
      }
      if (route === "backup/pending") {
        return Promise.resolve({status: "ok", data: {pending: null, cancellable: false}});
      }
      if (route === "backup/restore/preview") {
        return Promise.resolve({status: "ok", data: {
          snapshot_id: identifier, current: {}, effect: "requires_restart", restorable: true,
          restore_scope: "main_database_only",
          warnings: ["legacy_main_database_only", "side_stores_not_restored", "mixed_generation_risk"],
          preserves: ["semantic-catalog.sqlite3", "content_v2/"],
          credentials: {policy: "preserved", files: ["extensions/game/trusted_browsers.json"], keys: []},
          verified: {manifest: {content_hash: hash, created_at: "2026-10-01T00:00:00+00:00",
            note: "旧备份", byte_size: 1024}}}});
      }
      return Promise.resolve({status: "ok", data: {}});
    },
    apiPost(route, body) {
      calls.push([route, body]);
      return Promise.resolve({status: "ok", data: {safety_snapshot_id: "b".repeat(32)}});
    },
  };
  const page = harness({bridge});
  const root = element();
  page.mount(root);
  await settle(10);

  assert.match(root.innerHTML, /旧格式 · 仅主库/, "列表上就要标出这是只还原主库的旧快照");

  root.fire("click", {target: element({"data-backup-preview": identifier})});
  await settle(10);
  assert.match(root.innerHTML, /只还原主库/, "预览必须说清范围");
  assert.match(root.innerHTML, /semantic-catalog\.sqlite3/, "预览必须点名不还原的旁库");
  assert.match(root.innerHTML, /保持现在的内容/, "预览必须说明旁库保留当前内容");
  assert.match(root.innerHTML, /凭据/, "预览要说明凭据会被保留");

  root.fire("click", {target: element({"data-backup-stage": ""})});
  await settle();
  root.fire("click", {target: element({"data-backup-confirm": ""})});
  await settle(10);

  const restore = calls.find(([route]) => route === "backup/restore");
  assert.ok(restore, `没有发出还原请求，实际调用 ${JSON.stringify(calls)}`);
  assert.equal(restore[1].confirmed_scope, "main_database_only");
  assert.equal(restore[1].snapshot_id, identifier);
});

/* 一个无法应用的 pending 会一直挡住新的还原。页面必须显示原因，并给一个
   二次确认的「放弃」出口；它只清队列，不删任何快照。 */
test("卡住的还原请求：显示原因，二次确认后放弃", async () => {
  const calls = [];
  const bridge = {
    apiGet(route) {
      if (route === "backup") {
        return Promise.resolve({status: "ok", data: {snapshots: [], problems: []}});
      }
      if (route === "backup/pending") {
        return Promise.resolve({status: "ok", data: {
          pending: null, restorable: false, cancellable: true,
          problem: {code: "backup_pending_legacy", detail: "written by the single-database format"}}});
      }
      return Promise.resolve({status: "ok", data: {}});
    },
    apiPost(route, body) {
      calls.push([route, body]);
      return Promise.resolve({status: "ok", data: {cancelled: true, safety_snapshot_id: "c".repeat(32),
        safety_failure: null, journals_finalized: [], snapshots_deleted: []}});
    },
  };
  const page = harness({bridge});
  const root = element();
  page.mount(root);
  await settle(10);

  assert.match(root.innerHTML, /卡住|无法应用/, "必须说明这个请求挡住了后续还原");
  assert.match(root.innerHTML, /backup_pending_legacy/, "要给出机器码，便于对照日志");

  root.fire("click", {target: element({"data-backup-cancel-request": ""})});
  await settle();
  assert.match(root.innerHTML, /确认放弃/, "放弃前必须二次确认");
  assert.strictEqual(calls.length, 0, "确认之前不能发出请求");

  root.fire("click", {target: element({"data-backup-cancel-confirm": ""})});
  await settle(10);
  const cancel = calls.find(([route]) => route === "backup/restore/cancel");
  assert.ok(cancel, `没有发出放弃请求，实际调用 ${JSON.stringify(calls)}`);
  /* 跨 vm 上下文的对象原型不同，deepStrictEqual 会误报；断言字段即可。 */
  assert.strictEqual(cancel[1].confirm, true);
  assert.match(root.innerHTML, /没有删除任何快照/, "结果要说明没有删快照");
});

test("不完整的快照会单独列出来，而不是悄悄消失", async () => {
  const identifier = "d".repeat(32);
  const calls = [];
  const bridge = {
    apiGet(route) {
      if (route === "backup") {
        return Promise.resolve({status: "ok", data: {snapshots: [],
          problems: [{snapshot_id: identifier, reason: "bundle_or_main_database_missing"}]}});
      }
      return Promise.resolve({status: "ok", data: {pending: null, cancellable: false}});
    },
    apiPost(route, body) {
      calls.push([route, body]);
      return Promise.resolve({status: "ok", data: {snapshot_id: identifier, reason: "bundle_missing",
        moved: ["companion-v2-" + identifier + ".json"], quarantine_directory: ".incomplete"}});
    },
  };
  const page = harness({bridge});
  const root = element();
  page.mount(root);
  await settle(10);
  assert.match(root.innerHTML, /不完整的快照/);
  assert.match(root.innerHTML, /d{32}/, "要列出是哪一个快照");
  assert.match(root.innerHTML, /bundle_or_main_database_missing/);

  root.fire("click", {target: element({"data-backup-quarantine": identifier})});
  await settle();
  assert.match(root.innerHTML, /确认隔离/, "隔离前必须二次确认");
  assert.strictEqual(calls.length, 0, "确认之前不能发出请求");

  root.fire("click", {target: element({"data-backup-quarantine-confirm": identifier})});
  await settle(10);
  const quarantine = calls.find(([route]) => route === "backup/quarantine");
  assert.ok(quarantine, `没有发出隔离请求，实际调用 ${JSON.stringify(calls)}`);
  assert.strictEqual(quarantine[1].snapshot_id, identifier);
  assert.strictEqual(quarantine[1].confirm, true);
  assert.match(root.innerHTML, /没有删除任何文件/);
});

test("逐份删除复用行内危险按钮，并只在二次确认后发送所选快照", async () => {
  const id = "a".repeat(32), hash = "b".repeat(64), calls = [];
  let rows = [{snapshot_id: id, content_hash: hash, note: "待删的备份", created_at: "2026-11-18T00:00:00Z"}];
  let pending = {snapshot_id: id};
  const bridge = {
    async apiGet(route) { return route === "backup" ? {snapshots: rows} : {pending}; },
    async apiPost(route, body) { calls.push([route, body]); rows = []; return {snapshot_id: id}; },
  };
  const page = harness({bridge});
  const root = element();
  page.mount(root);
  await settle(10);
  assert.match(root.innerHTML, /data-backup-delete="[a-f0-9]+" disabled/);
  root.fire("click", {target: element({"data-backup-delete": id})});
  assert.doesNotMatch(root.innerHTML, /确认删除/);
  page.unmount();
  pending = null;
  page.mount(root);
  await settle(10);
  root.fire("click", {target: element({"data-backup-delete": id})});
  assert.match(root.innerHTML, /class="button small danger" data-backup-delete-confirm=/);
  assert.match(root.innerHTML, /删除后无法用这份快照还原/);
  assert.equal(calls.length, 0);
  root.fire("click", {target: element({"data-backup-delete-abort": ""})});
  assert.doesNotMatch(root.innerHTML, /确认删除/);
  root.fire("click", {target: element({"data-backup-delete": id})});
  root.fire("click", {target: element({"data-backup-delete-confirm": id})});
  await settle(10);
  assert.equal(JSON.stringify(calls), JSON.stringify([["backup/delete", {snapshot_id: id, confirmed_hash: hash, confirm: true}]]));
  assert.match(root.innerHTML, /已删除.*备份快照/);
  assert.doesNotMatch(root.innerHTML, /待删的备份/);
});
