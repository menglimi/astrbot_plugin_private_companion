/*
 * 行动页（QQ 空间状态摘要卡与其他行动能力）视觉结构与令牌回归护栏。
 *
 * 锁定关键契约：
 *   1. QQ 空间顶部收束为统一卡片头（标题 + 状态徽章 + 版本 + 右侧操作按钮）
 *      与 2×2 等高结构化轻卡网格（凭据状态 / 授权与能力 / 能力开关 / 频控与主题策略）；
 *   2. 行动页按钮统一使用标准 var(--r-sm) 圆角，不再出现漂浮大胶囊按钮；
 *   3. QQ 空间设置说明段落仅在行动页作用域内放宽 max-width:none，不修改共享工作台；
 *   4. 零硬编码色值、零未声明令牌，窄容器自动回落单列。
 *
 * 用 node --test 运行。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const PAGE = path.join(ROOT, "pages", "陪伴小屋");
const read = (name) => fs.readFileSync(path.join(PAGE, name), "utf8");

function stripComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, "");
}

function loadAppHelpers() {
  const APP = read("app.js");
  const noop = () => {};
  const makeEl = () => ({
    id: "", dataset: {}, style: {}, hidden: false, value: "", textContent: "", innerHTML: "",
    classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
    addEventListener: noop, removeEventListener: noop, setAttribute: noop, removeAttribute: noop,
    getAttribute: () => null, hasAttribute: () => false,
    querySelector: () => null, querySelectorAll: () => [],
  });
  const document = {
    readyState: "complete", hidden: false,
    documentElement: makeEl(), head: makeEl(), body: makeEl(),
    addEventListener: noop, removeEventListener: noop,
    getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
    createElement: makeEl,
  };
  const window = {
    document, console, navigator: { language: "zh-CN" },
    localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
    sessionStorage: { getItem: () => null, setItem: noop, removeItem: noop },
    addEventListener: noop, removeEventListener: noop,
    matchMedia: () => ({ matches: false, addEventListener: noop, removeEventListener: noop }),
    location: { hash: "", href: "http://localhost/", search: "", pathname: "/" },
    history: { replaceState: noop, pushState: noop },
    innerWidth: 1440, innerHeight: 900,
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    requestAnimationFrame: (cb) => { cb(0); return 0; }, cancelAnimationFrame: noop,
    setTimeout: () => 0, clearTimeout: noop, setInterval: () => 0, clearInterval: noop,
    CompanionProduct: {
      role: () => null, roleLabel: () => "", scopeKey: () => "",
      appearance: () => ({ motion: "full" }), targets: () => [], manifest: null,
      connect: () => new Promise(noop), capability: () => null, statusLabel: () => "",
    },
    CompanionShell: { section: "action", selectSection: noop, selectView: noop, selectFurniture: noop },
  };
  const context = vm.createContext({
    window, document, console, navigator: window.navigator,
    localStorage: window.localStorage, sessionStorage: window.sessionStorage,
    location: window.location, history: window.history,
    setTimeout: window.setTimeout, clearTimeout: window.clearTimeout,
    setInterval: window.setInterval, clearInterval: window.clearInterval,
    queueMicrotask, structuredClone,
    requestAnimationFrame: window.requestAnimationFrame, cancelAnimationFrame: noop,
    matchMedia: window.matchMedia, getComputedStyle: window.getComputedStyle,
    Intl, URL, URLSearchParams, TextEncoder, TextDecoder, crypto: require("node:crypto").webcrypto,
  });
  try {
    vm.runInContext(APP, context, { filename: "app.js" });
  } catch (error) {
    assert.match(error.message, /addEventListener/, `app.js 顶层加载异常：${error.message}`);
  }
  return {
    context,
    run: (code) => vm.runInContext(code, context),
  };
}

test("QQ 空间状态摘要卡渲染统一头部按钮组与 2×2 四格轻卡", () => {
  const { run } = loadAppHelpers();
  const sampleStatus = {
    installed: true,
    active: true,
    role_enabled: true,
    invoke_granted: true,
    publish_granted: true,
    package_version: "1.0.0",
    credential: { state: "unknown", source: "none", uin_masked: "", reason: "" },
    consented: {
      "qzone.feed.read": true,
      "qzone.post.like": true,
      "qzone.post.comment": true,
      "qzone.post.publish": true,
    },
    runtime: {
      settings: {
        enabled: true,
        like_enabled: true,
        comment_enabled: true,
        publish_enabled: true,
        feed_recent_posts: 10,
        action_min_interval_minutes: 30,
        max_actions_per_tick: 1,
        daily_limits_enabled: false,
        topic_auto_enabled: true,
        topic_require_confirmation: false,
      },
    },
    catalogue: {
      topics: Array.from({ length: 7 }, (_, i) => ({ id: `t${i}`, label: `主题 ${i + 1}`, scenes: ["conversation"] })),
      scenes: { conversation: "对话" },
    },
  };
  run(`qzoneState.status = ${JSON.stringify(sampleStatus)};`);
  const html = run("qzoneStatusMarkup(qzoneState.status, true)");

  assert.match(html, /class="action-qzone-head"/);
  assert.match(html, /id="action-qzone-title">QQ 空间<\/h2>/);
  assert.match(html, /class="action-qzone-badge is-ok"[\s\S]*?已启用/);
  assert.match(html, /扩展版本 1\.0\.0/);
  assert.match(html, /class="action-qzone-head-actions"[\s\S]*?data-qzone-action="refresh-credentials"[\s\S]*?刷新凭据[\s\S]*?data-qzone-action="refresh-status"[\s\S]*?重新读取/);

  for (const tile of ["credential", "consent", "switches", "policy"]) {
    assert.match(html, new RegExp(`data-qzone-tile="${tile}"`), `缺少结构化信息轻卡：${tile}`);
  }
  assert.match(html, /能力 4 \/ 4/);
  assert.match(html, /4 \/ 4 开启/);
  assert.match(html, /已启用主题 7 个/);
  assert.match(html, /每次读取动态 10 条/);
  assert.match(html, /每日说说次数 无限/);

  const singleHtml = run("qzoneStatusMarkup({installed:false, package_version:'1.0.0', credential:{state:'unknown'}}, false)");
  assert.match(singleHtml, /class="action-qzone-grid is-single"/, "单卡状态应通栏铺满，不留半宽空白");
});

test("action-page.css 锁定 --r-sm 按钮圆角、2×2 响应网格、作用域说明宽与零硬编码色值", () => {
  const css = stripComments(read("action-page.css"));
  const bundle = stripComments(read("bundle.css"));

  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/, "action-page.css 不应包含硬编码十六进制色值");
  assert.doesNotMatch(css, /\b(?:rgba?|hsla?)\(/, "action-page.css 不应包含硬编码函数色值");

  assert.match(css, /\.action-page\s+\.button\s*\{[^}]*border-radius:\s*var\(--r-sm\)/);
  assert.match(css, /\.action-page\s+\.button\.small\s*\{[^}]*border-radius:\s*var\(--r-sm\)/);
  assert.match(css, /\.action-qzone-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(css, /@container\s+qzone-status\s*\(max-width:\s*660px\)\s*\{[\s\S]*?\.action-qzone-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);

  assert.match(
    css,
    /\.action-qzone\s+#qzoneConfigWorkspace\s+\.wb-card-copy\s+p[\s\S]*?max-width:\s*none/,
    "应在行动页作用域内解除 QQ 空间设置说明段落的 62ch 窄栏限制",
  );
  assert.match(css, /\.action-capability-list\s*\{[^}]*border-radius:\s*var\(--r-md\)/);
  assert.match(css, /\.action-capability-status\s*\{[^}]*border-radius:\s*var\(--r-xs\)/);

  assert.match(bundle, /\.action-qzone-grid\s*\{/, "bundle.css 必须同步包含重构后的行动页样式");
});
