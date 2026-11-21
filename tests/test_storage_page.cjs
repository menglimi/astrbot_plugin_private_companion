"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "陪伴小屋", "storage-page.js"), "utf8");
function button(attributes) {
  return {hasAttribute: name => attributes.includes(name), closest() {return this;}, setAttribute(name, value) {this[name] = value;}};
}
function setup({failedCleanup = false} = {}) {
  const calls = [];
  const document = {getElementById() { return null; }};
  const window = {AstrBotPluginPage: {
    async apiGet(route) {
      calls.push(["GET", route]);
      return {active_root: "D:\\AstrBotData\\astrbot_plugin_private_companion",
        selected_path: "D:\\AstrBotData", desired_root: "D:\\AstrBotData\\astrbot_plugin_private_companion",
        selected_backup_path: "", backup_root: "D:\\AstrBotData\\astrbot_plugin_private_companion\\backups",
        restart_required: false, policy: {retention_count: 1, retention_days: 10},
        inventory: {total_bytes: 2048, free_bytes: 4096, backup_free_bytes: 4096, categories: {
          database: {bytes: 1024, files: 1}, backups: {bytes: 1024, files: 2}, other: {bytes: 0, files: 0}}},
        cleanup_preview: {snapshot_count: 3, remove_count: 2, remove_bytes: 512, snapshot_ids: ["a", "b"], future_count: failedCleanup ? 1 : 0}};
    },
    async apiPost(route, body) {
      calls.push(["POST", route, body]);
      return {active_root: "D:\\AstrBotData", selected_path: body.path || "",
        selected_backup_path: body.backup_path || "", backup_root: body.backup_path || "D:\\AstrBotData\\backups",
        desired_root: "D:\\AstrBotData", restart_required: false,
        policy: {retention_count: body.retention_count || 1, retention_days: body.retention_days || 10},
        auto_backup: {auto_backup_enabled: body.auto_backup_enabled,
          auto_backup_interval_minutes: body.auto_backup_interval_minutes},
        inventory: {total_bytes: 0, free_bytes: 4096, categories: {}},
        cleanup_preview: {snapshot_count: 1, remove_count: 0, remove_bytes: 0},
        cleanup_result: failedCleanup ? {removed_count: 1, removed_bytes: 512, failed: true, failed_count: 1} : null};
    },
  }};
  const context = {window, document, setTimeout, clearTimeout, Promise, Number, String, Object, Array, Error};
  vm.runInNewContext(source, context);
  const handlers = {};
  const root = {isConnected: true, innerHTML: "", addEventListener(name, fn) { handlers[name] = fn; }};
  window.CompanionStoragePage.mount(root);
  return {window, root, handlers, calls};
}

test("storage view renders measured usage and editable location and retention", async () => {
  const {root, calls} = setup();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.match(root.innerHTML, /插件资料占用/);
  assert.match(root.innerHTML, /运行资料/);
  assert.match(root.innerHTML, /备份快照/);
  assert.match(root.innerHTML, /name="path"/);
  assert.match(root.innerHTML, /name="backup_path"/);
  assert.match(root.innerHTML, /name="retention_count"/);
  assert.match(root.innerHTML, /name="retention_days"/);
  assert.deepEqual(calls[0], ["GET", "storage"]);
});

test("saving the backup location sends it through the storage API", async () => {
  const {handlers, calls} = setup();
  await new Promise(resolve => setTimeout(resolve, 0));
  handlers.input({target: {name: "backup_path", value: "E:\\AstrBotSnapshots"}});
  let prevented = false;
  handlers.submit({target: {matches: selector => selector === "[data-storage-form]"},
    preventDefault() { prevented = true; }});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(prevented, true);
  assert.equal(calls.at(-1)[0], "POST");
  assert.equal(calls.at(-1)[2].backup_path, "E:\\AstrBotSnapshots");
});

test("cleanup presents an explicit confirmation before issuing the destructive request", async () => {
  const {root, handlers, calls} = setup();
  await new Promise(resolve => setTimeout(resolve, 0));
  handlers.click({target: {closest: () => button(["data-storage-clean-review"])}});
  assert.match(root.innerHTML, /确认清理/);
  assert.equal(calls.filter(call => call[0] === "POST").length, 0);
  handlers.click({target: {closest: () => button(["data-storage-clean-confirm"])}});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(JSON.stringify(calls.at(-1)), JSON.stringify(["POST", "storage", {action: "clean"}]));
  assert.match(root.innerHTML, /已清理 0 份快照/);
});

test("runtime cache cleanup posts only the cache action and keeps the page data", async () => {
  const {root, handlers, calls} = setup();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.match(root.innerHTML, /运行缓存/);
  assert.match(root.innerHTML, /data-storage-cache-clear/);
  handlers.click({target: {closest: () => button(["data-storage-cache-clear"])}});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(JSON.stringify(calls.at(-1)), JSON.stringify(["POST", "storage", {action: "cache"}]));
  assert.match(root.innerHTML, /已清理运行缓存/);
  // The cache response carries no storage inventory; the measured usage stays.
  assert.match(root.innerHTML, /插件资料占用/);
});

test("saving automatic backup sends the enabled flag and numeric interval", async () => {
  const {root, handlers, calls} = setup();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.match(root.innerHTML, /class="switch"[^>]*role="switch" aria-checked="false"/);
  assert.match(root.innerHTML, /name="auto_backup_interval_minutes"[^>]*value="1440"/);
  const toggle = button(["data-storage-auto-toggle"]);
  handlers.click({target: toggle});
  assert.equal(toggle["aria-checked"], "true");
  handlers.input({target: {name: "auto_backup_interval_minutes", value: "1"}});
  handlers.submit({target: {matches: selector => selector === "[data-storage-form]"}, preventDefault() {}});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(calls.at(-1)[2].auto_backup_enabled, true);
  assert.equal(calls.at(-1)[2].auto_backup_interval_minutes, 1);
  assert.match(root.innerHTML, /role="switch" aria-checked="true"/);
});

test("storage distinguishes partial cleanup and future-dated snapshots", async () => {
  const {root, handlers} = setup({failedCleanup: true});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.match(root.innerHTML, /1 份快照的时间晚于当前时间，暂不参与轮转/);
  handlers.click({target: button(["data-storage-clean-review"])});
  handlers.click({target: button(["data-storage-clean-confirm"])});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.match(root.innerHTML, /storage-message is-error/);
  assert.match(root.innerHTML, /已清理 1 份快照.*另有 1 份未能清理/);
});
