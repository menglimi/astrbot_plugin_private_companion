"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "陪伴小屋", "storage-page.js"), "utf8");
function button(attributes) {
  return {hasAttribute: name => attributes.includes(name), closest: () => null};
}
function setup() {
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
        cleanup_preview: {snapshot_count: 3, remove_count: 2, remove_bytes: 512, snapshot_ids: ["a", "b"]}};
    },
    async apiPost(route, body) {
      calls.push(["POST", route, body]);
      return {active_root: "D:\\AstrBotData", selected_path: body.path || "",
        selected_backup_path: body.backup_path || "", backup_root: body.backup_path || "D:\\AstrBotData\\backups",
        desired_root: "D:\\AstrBotData", restart_required: false,
        policy: {retention_count: body.retention_count || 1, retention_days: body.retention_days || 10},
        inventory: {total_bytes: 0, free_bytes: 4096, categories: {}},
        cleanup_preview: {snapshot_count: 1, remove_count: 0, remove_bytes: 0}};
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
