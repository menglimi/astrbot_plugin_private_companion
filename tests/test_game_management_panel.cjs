"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const rootDir = path.resolve(__dirname, "..");
const panelSource = fs.readFileSync(path.join(rootDir, "pages/陪伴小屋/native-extension-panel.js"), "utf8");
const managementSource = fs.readFileSync(path.join(rootDir, "pages/陪伴小屋/native-extension-management.js"), "utf8");
const gameDefaults = JSON.parse(fs.readFileSync(path.join(rootDir, "astrbot_host/native_extensions/game/settings-defaults.json"), "utf8"));
const gameSchema = JSON.parse(fs.readFileSync(path.join(rootDir, "astrbot_host/native_extensions/game/settings-schema.json"), "utf8"));

const settle = async () => {
  for (let i = 0; i < 10; i += 1) await new Promise(resolve => setImmediate(resolve));
};

function makeRoot() {
  const buttons = new Map();
  let html = "";
  const noticeNode = {textContent: "", classList: {toggle() {}}};
  const root = {
    isConnected: true,
    listeners: {},
    attributes: {},
    set innerHTML(value) {
      html = String(value);
      buttons.clear();
      const pattern = /<button\b([^>]*)>([\s\S]*?)<\/button>/g;
      let match;
      while ((match = pattern.exec(html)) !== null) {
        const attrs = match[1];
        const actionMatch = attrs.match(/data-native-action="([^"]+)"/);
        if (actionMatch) {
          buttons.set(actionMatch[1], {
            dataset: {nativeAction: actionMatch[1]},
            disabled: false,
            hasAttribute: name => new RegExp(`\\b${name}\\b`).test(attrs),
            closest: sel => sel === "button" ? buttons.get(actionMatch[1]) : null,
          });
        }
      }
    },
    get innerHTML() { return html; },
    addEventListener(name, fn) { this.listeners[name] = fn; },
    removeEventListener(name) { delete this.listeners[name]; },
    setAttribute(name, value) { this.attributes[name] = value; },
    contains: () => true,
    querySelector(selector) {
      if (selector === "[data-native-action-notice]") return noticeNode;
      return null;
    },
    querySelectorAll(selector) {
      if (selector.includes("[data-native-action]")) return [...buttons.values()];
      return [];
    },
    button(action) { return buttons.get(action); },
    noticeNode,
  };
  return root;
}

function gameManagement(overrides = {}) {
  return {
    server: {
      enabled: true, running: true, host: "127.0.0.1", port: 8765,
      public_base_url: "", external_base_url: "", access_host: "",
      local_base_url: "http://127.0.0.1:8765", ...(overrides.server || {}),
    },
    xiangqi_engine: {
      available: false, managed: false, configured: false, configured_path: "",
      configured_missing: false, path: "", nnue_path: "", version: "",
      install_root: "/data/pikafish", allow_download: true, running: false, error: "",
      guidance: "尚未安装 Pikafish 引擎；点击「下载 Pikafish 引擎」即可自动安装。",
      ...(overrides.xiangqi_engine || {}),
    },
    tunnel: {
      installed: true, source: "managed", path: "/data/cloudflared/cloudflared",
      configured_path: "", configured_missing: false, running: false, ready: false,
      url: "", error: "", allow_download: true, download_dir: "/data/cloudflared",
      guidance: "已检测到 cloudflared（托管目录），可按需提供临时公网访问。",
      ...(overrides.tunnel || {}),
    },
    native_games: [],
    rooms: [],
    actions: ["cloudflared_check", "cloudflared_install", "tunnel_start", "tunnel_stop", "xiangqi_check", "xiangqi_install"],
  };
}

test("游戏管理面板展示象棋引擎与 cloudflared 状态、按钮禁用约束并派发管理动作", async () => {
  const requests = [];
  let currentRecord = {
    settings: structuredClone(gameDefaults),
    schema: structuredClone(gameSchema),
    revision: "rev-game-1",
    runtime: {state: "running", error_code: null},
    management: gameManagement(),
  };
  const window = {
    CompanionProduct: {
      errorLabel: () => "",
      async request(method, endpoint, body, options) {
        requests.push({method, endpoint, body, options});
        if (endpoint === "extensions/action") {
          if (body.action === "xiangqi_install") {
            currentRecord.management.xiangqi_engine = {
              ...currentRecord.management.xiangqi_engine,
              available: true, managed: true, version: "2025-01-01",
              path: "/data/pikafish/pikafish", nnue_path: "/data/pikafish/pikafish.nnue",
              guidance: "Pikafish 象棋引擎已就绪（托管安装）。",
            };
          } else if (body.action === "tunnel_start") {
            currentRecord.management.tunnel = {
              ...currentRecord.management.tunnel,
              running: true, ready: true, url: "https://demo.trycloudflare.com",
            };
            return {...structuredClone(currentRecord), result: {completed: true, url: "https://demo.trycloudflare.com"}};
          }
          return {...structuredClone(currentRecord), result: {completed: true}};
        }
        return {extensions: {game: structuredClone(currentRecord)}};
      },
    },
  };
  vm.runInNewContext(managementSource, {window, setTimeout, clearTimeout, Promise, Map, Set, Array, Object, String, Number, Boolean, JSON, RegExp, Error});
  const root = makeRoot();
  const instance = window.CompanionNativeExtensionManagement.mount(root, {
    extensionId: "game", record: structuredClone(currentRecord), instance: 1,
  });
  assert.match(root.innerHTML, /中国象棋引擎（Pikafish）/);
  assert.match(root.innerHTML, /网页房间与临时公网（cloudflared）/);
  assert.match(root.innerHTML, /托管目录/);
  assert.equal(root.button("xiangqi_install").disabled, false);
  assert.equal(root.button("tunnel_start").disabled, false);
  assert.equal(root.button("tunnel_stop").disabled, true);

  root.listeners.click({target: root.button("xiangqi_install")});
  await settle();
  assert.equal(requests[0].endpoint, "extensions/action");
  assert.deepEqual(JSON.parse(JSON.stringify(requests[0].body)), {extension_id: "game", action: "xiangqi_install", payload: {}});
  assert.equal(requests[0].options.timeoutMs, 245000);
  assert.match(root.noticeNode.textContent, /Pikafish 象棋引擎已安装并就绪/);
  assert.match(root.innerHTML, /已就绪 · 2025-01-01/);

  root.listeners.click({target: root.button("tunnel_start")});
  await settle();
  assert.equal(requests[1].body.action, "tunnel_start");
  assert.match(root.noticeNode.textContent, /https:\/\/demo\.trycloudflare\.com/);
  assert.equal(root.button("tunnel_start").disabled, true);
  assert.equal(root.button("tunnel_stop").disabled, false);

  // 配置固定公网地址且禁止下载时，对应按钮自动禁用
  const locked = structuredClone(currentRecord);
  locked.management = gameManagement({
    server: {public_base_url: "https://games.example.com"},
    xiangqi_engine: {allow_download: false},
    tunnel: {allow_download: false},
  });
  instance.update(locked);
  assert.equal(root.button("xiangqi_install").disabled, true);
  assert.equal(root.button("cloudflared_install").disabled, true);
  assert.equal(root.button("tunnel_start").disabled, true);
  instance.stop();
});

test("game 面板导出 syncRuntime 并与 CompanionGameAccess 双向联动刷新", async () => {
  let accessRefreshes = 0;
  let managementReads = 0;
  const requests = [];
  const window = {
    CompanionGameAccess: {refresh: () => { accessRefreshes += 1; }},
    CompanionNativeExtensionManagement: {
      mount: (_el, options) => ({
        stop() {},
        setBlocked() {},
        update() {},
        read(opts) { managementReads += 1; return opts; },
        options,
      }),
    },
    CompanionProduct: {
      extension: () => ({owner: "companion_v2", assembled: true, desired_state: "active", installed_revision: 3}),
      errorLabel: () => "",
      refreshIntegrations: async () => {},
      manageExtension: async () => {},
      async request(method, endpoint, body) {
        requests.push({method, endpoint, body});
        return {
          extensions: {
            game: {
              settings: structuredClone(gameDefaults),
              schema: structuredClone(gameSchema),
              revision: "rev-1",
              runtime: {state: "inactive", error_code: null},
              management: gameManagement(),
            },
          },
        };
      },
    },
  };
  const statusNode = {innerHTML: ""};
  const lifecycleNode = {innerHTML: ""};
  const noticeNode = {textContent: "", classList: {toggle() {}}};
  const managementSlot = {};
  const panelRoot = {
    isConnected: true,
    innerHTML: "",
    addEventListener() {},
    removeEventListener() {},
    setAttribute() {},
    querySelector(sel) {
      if (sel === "[data-native-management]") return managementSlot;
      if (sel === "[data-native-runtime]") return statusNode;
      if (sel === "[data-native-lifecycle]") return lifecycleNode;
      if (sel === "[data-native-notice]") return noticeNode;
      return null;
    },
    querySelectorAll() { return []; },
  };
  vm.runInNewContext(panelSource, {
    window, document: {activeElement: null}, setTimeout, clearTimeout, Promise, Map, Set, Array, Object, String, Number, Boolean, JSON, RegExp, Error,
  });
  const panel = window.CompanionNativeExtensionPanel.mount(panelRoot, {extensionId: "game"});
  await settle();
  assert.ok(panel.management);
  assert.match(statusNode.innerHTML, /未运行/);
  panel.draft.native_v2.gomoku_enabled = false;
  window.CompanionNativeExtensionPanel.syncRuntime("game", {state: "running", error_code: null});
  assert.match(statusNode.innerHTML, /运行中/);
  assert.equal(managementReads, 1);
  assert.equal(panel.draft.native_v2.gomoku_enabled, false, "syncRuntime must preserve unsaved settings draft");

  await panel.lifecycle("pause");
  assert.equal(accessRefreshes, 1);
  window.CompanionNativeExtensionPanel.unmount(panelRoot);
});
