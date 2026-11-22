/* PR-PC-UI-6 回归测试：陪伴小屋 7 个家具面板深度美化、嵌入态紧凑顶栏与真实状态映射。
 *
 * 覆盖：
 *   1. 日记书桌 / 睡眠与梦境（pages/日记与生活/）紧凑顶栏、4 列 KPI 状态条、info 提示条、
 *      分层睡眠生理指标与作息排程卡片，以及与 workspaces/life & workspace-bundles/life.js 同步；
 *   2. 六套主题（courtyard / dawn / dusk / night / walnut / contrast）完整声明且核心前背景
 *      全部达到 WCAG AA+ (>= 4.5:1) 对比度；
 *   3. 嵌入态焦点区分（日记书桌默认折叠睡眠概览呈现摘要胶囊，睡眠与梦境/独立页默认展开）、
 *      进度条宽度/语义色与背景轮询零重复 DOM 重建、零 localStorage/sessionStorage 依赖；
 *   4. 全部 7 个家具面板（desk / calendar / shelf / bed / radio / garden / wardrobe）真实状态
 *      映射与原位状态刷新（消灭错误「配置未知」且不重载 iframe）；
 *   5. 成长花架（product.growthMarkup）、结构化说明面板（shelf / radio / wardrobe）、
 *      product-page.css 零硬编码色与 state-page.css 容器响应式契约。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const read = (...parts) => fs.readFileSync(path.join(ROOT, ...parts), "utf8").replace(/\r\n/g, "\n");
const stripComments = css => css.replace(/\/\*[\s\S]*?\*\//g, "");

function hexToRgb(hex) {
  const clean = hex.replace(/^#/, "");
  const full = clean.length === 3 ? clean.split("").map(ch => ch + ch).join("") : clean;
  return [0, 2, 4].map(offset => parseInt(full.slice(offset, offset + 2), 16));
}

function relativeLuminance([r, g, b]) {
  const channel = v => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrastRatio(fgHex, bgHex) {
  const l1 = relativeLuminance(hexToRgb(fgHex));
  const l2 = relativeLuminance(hexToRgb(bgHex));
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

function extractThemeMaps(css) {
  const clean = stripComments(css);
  const themes = new Map();
  for (const match of clean.matchAll(/([^{}]+)\{([^{}]+)\}/g)) {
    const [, selector, body] = match;
    for (const name of ["courtyard", "dawn", "dusk", "night", "walnut", "contrast"]) {
      if (!selector.includes(`data-theme="${name}"`)) continue;
      if (!themes.has(name)) themes.set(name, {});
      const map = themes.get(name);
      for (const decl of body.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+)/gi)) {
        map[decl[1]] = decl[2].trim();
      }
    }
  }
  return themes;
}

function runLifeWorkspace({ embedded = false, focusTab = "", status, rejectLoad = false }) {
  const sourcePath = path.join(ROOT, "pages", "日记与生活", "app.js");
  const source = fs.readFileSync(sourcePath, "utf8");
  const ids = new Set([...source.matchAll(/\$\("([^"]+)"\)/g)].map(m => m[1]));
  for (const extra of ["reading-list", "sleep-list", "sleep-overview", "sleep-summary-pills", "life-toolbar",
    "host-state-card", "routine-state-card", "sleep-pressure-card", "sleep-pressure-bar",
    "sleep-debt-card", "sleep-debt-hint", "sleep-workload-card", "sleep-workload-bar",
    "sleep-recommended-card", "sleep-active-wake-card"]) {
    ids.add(extra);
  }
  class FakeElement {
    constructor(tag = "div") {
      this.tag = tag;
      this.listeners = new Map();
      this.children = [];
      this.dataset = {};
      this.style = {};
      this.attributes = {};
      this.value = "";
      this.textContent = "";
      this.className = "";
      this.hidden = false;
      this.disabled = false;
      this.checked = false;
      this.open = tag === "details";
      this.replaceChildrenCount = 0;
      this.classList = { toggle() {}, add() {}, remove() {} };
    }
    addEventListener(name, cb) {
      if (!this.listeners.has(name)) this.listeners.set(name, []);
      this.listeners.get(name).push(cb);
    }
    dispatch(name, event = {}) {
      for (const cb of this.listeners.get(name) || []) cb(event);
    }
    replaceChildren(...children) { this.replaceChildrenCount += 1; this.children = children; }
    append(...children) { this.children.push(...children); }
    querySelectorAll() { return this.children; }
    setAttribute(name, value) { this.attributes[name] = value; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    click() { this.dispatch("click", { preventDefault() {} }); }
  }
  const elements = new Map([...ids].map(id => [id, new FakeElement(id === "sleep-overview" ? "details" : "div")]));
  if (embedded) {
    elements.get("start-host").hidden = true;
    elements.get("stop-host").hidden = true;
  }
  const tabButtons = ["archive", "reading", "sleep", "receipts"].map(name => {
    const btn = new FakeElement("button");
    btn.dataset.tab = name;
    return btn;
  });
  const tabPanels = ["archive", "reading", "sleep", "receipts"].map(name => {
    const panel = new FakeElement("section");
    panel.id = name;
    panel.hidden = name !== "archive";
    return panel;
  });
  const docListeners = new Map();
  const document = {
    hidden: false,
    documentElement: { dataset: {} },
    getElementById: id => elements.get(id) || new FakeElement(),
    createElement: tag => new FakeElement(tag),
    querySelectorAll: selector => selector === "[data-tab]" ? tabButtons : selector === ".tab-panel" ? tabPanels : [],
    addEventListener(name, cb) {
      if (!docListeners.has(name)) docListeners.set(name, []);
      docListeners.get(name).push(cb);
    },
  };
  const bridge = {
    ready: async () => bridge,
    getContext: () => ({
      ...(embedded ? { pageName: "life" } : {}),
      ...(focusTab ? { tab: focusTab } : {}),
      display: { role_name: "kazusa", subject_name: "有所思" },
    }),
    apiGet: async () => {
      if (rejectLoad) throw Object.assign(new Error("life_not_bound"), { code: "life_not_bound" });
      return structuredClone(status);
    },
    apiPost: async () => ({ status: "ok", data: { state: "completed" } }),
  };
  const sandbox = {
    window: {
      crypto: { getRandomValues(bytes) { bytes.fill(7); return bytes; } },
      AstrBotPluginPage: bridge,
    },
    document,
    Intl,
    Date,
    Option: class extends FakeElement {
      constructor(label, value) { super("option"); this.textContent = label; this.value = value; }
    },
    setTimeout: () => 1,
    clearTimeout: () => {},
  };
  vm.runInNewContext(source, sandbox, { filename: sourcePath });
  return { elements, tabButtons, tabPanels, document, docListeners };
}

function makeSampleLifeStatus(overrides = {}) {
  return {
    role_id: "kazusa",
    host_state: "running",
    config: {
      enabled: true, diary_enabled: true, sleep_enabled: true, reading_enabled: false,
      derive_enabled: true, timezone: "Asia/Shanghai", diary_time: "23:10", sleep_time: "00:34",
      sleep_timezone: "Asia/Shanghai", sleep_variation_minutes: 20, sleep_window_minutes: 120,
      sleep_minutes: 480,
    },
    next_diary_at: "2026-11-22T15:10:00Z",
    next_sleep_at: "2026-11-22T16:34:00Z",
    sleep_schedule: {
      enabled: true,
      executor_is_current: true,
      executor_state: "available",
      next_sleep_at: "2026-11-22T16:34:00Z",
      next_wake_at: "2026-11-23T00:34:00Z",
      active_wake_at: "2026-11-22T01:12:00Z",
      recommended_duration_minutes: 510,
      physiology: {
        sleep_pressure: 68,
        sleep_debt_minutes: 95,
        workload_score: 54,
        has_sleep_history: true,
      },
    },
    journals: [],
    activities: [],
    receipts: [],
    running: [],
    next_date: null,
    next_activity: null,
    view_token: "v1",
    edit_token: "e1",
    ...overrides,
  };
}

function createFurnitureAppHarness(initial = {}) {
  const source = read("pages", "陪伴小屋", "app.js");
  const start = source.indexOf("const directory=[");
  const directoryEnd = source.indexOf("const furnitureFilters=");
  const directoryBlock = source.slice(start, directoryEnd);
  const helperStart = source.indexOf("function integrationProvider(");
  const helperEnd = source.indexOf("async function probeHost(");
  const integrationBlock = source.slice(helperStart, helperEnd);
  const capStart = source.indexOf("function capabilityStatus(");
  const capEnd = source.indexOf("/* 主题色卡不再硬编码色值");
  const capBlock = source.slice(capStart, capEnd);
  const panelStart = source.indexOf("function panelHead(");
  const panelEnd = source.indexOf("/* 默认面板（没选家具也没选分区）");
  const panelBlock = source.slice(panelStart, panelEnd);

  const chipNode = {
    dataset: {},
    textContent: "",
    className: "",
  };
  let activeChip = null;

  const stateData = {
    catalog: "catalog" in initial ? initial.catalog : { installation_id: "inst", operator_id: "op", targets: [] },
    role: "role" in initial ? initial.role : { role_id: "kazusa", life_api: "/life", state_api: "/state", growth_api: "/growth" },
    manifest: "manifest" in initial ? initial.manifest : { extensions: [], providers: [] },
    data: "data" in initial ? initial.data : {},
    connectionState: "connectionState" in initial ? initial.connectionState : "connected",
    hosted: "hosted" in initial ? initial.hosted : true,
    unavailableCodes: "unavailableCodes" in initial ? initial.unavailableCodes : {},
    outfitImage: "outfitImage" in initial ? initial.outfitImage : null,
    roleName: "roleName" in initial ? initial.roleName : "kazusa",
  };

  const product = {
    get manifest() { return stateData.manifest; },
    get data() { return stateData.data; },
    hosted: () => stateData.hosted,
    connection: () => ({ state: stateData.connectionState }),
    directory: () => stateData.catalog,
    role: () => stateData.role,
    roleLabel: () => stateData.roleName,
    unavailableCode: name => stateData.unavailableCodes[name] || "",
    extension: id => stateData.manifest?.extensions?.find(item => item.capability_id === id) || null,
    capability: id => stateData.manifest?.extensions?.find(item => item.capability_id === id)
      || stateData.manifest?.providers?.find(item => item.capability_id === id) || null,
    statusLabel: s => ({ connected: "已连接", missing: "未安装" }[s] || "状态未知"),
    workspace: (kind, tab) => `<section class="product-workspace" data-workspace="${kind}" data-workspace-tab="${tab}"></section>`,
    growthMarkup: () => '<div class="product-growth">growth</div>',
  };

  const sandbox = {
    product,
    integrationSnapshot: stateData.manifest,
    state: { modules: {} },
    escapeHTML: value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
    I: name => `<i data-lucide="${name}"></i>`,
    button: (label, attrs = "") => `<button type="button" ${attrs}>${label}</button>`,
    chip: (text, type = "") => `<span class="pill ${type}">${text}</span>`,
    renderRoleState: (_section, feature) => `<section id="roleStateWorkspace" data-state-feature="${feature}"></section>`,
    renderHomePanel: () => "",
    outfitImageSrc: () => stateData.outfitImage,
    $: selector => selector === "#shellPanel [data-furniture-status-chip]" ? activeChip : null,
  };

  vm.runInNewContext(
    `${directoryBlock}\n${integrationBlock}\n${capBlock}\n${panelBlock}\n` +
    `globalThis.exports = { furniturePanelStatus, syncFurniturePanelStatus, renderFurniturePanel, setIntegration(m){ integrationSnapshot = m; } };`,
    sandbox
  );

  return {
    stateData,
    chipNode,
    setActiveChip(id) {
      chipNode.dataset = { furnitureStatusChip: id };
      activeChip = chipNode;
    },
    syncManifest(manifest) {
      stateData.manifest = manifest;
      sandbox.exports.setIntegration(manifest);
    },
    ...sandbox.exports,
  };
}

test("日记与生活工作区：嵌入态紧凑 ~44px 顶栏、4 列 KPI、info 提示条与结构化睡眠卡片，且三份投影同步", () => {
  const html = read("pages", "日记与生活", "workspace.html");
  const css = read("pages", "日记与生活", "style.css");
  const js = read("pages", "日记与生活", "app.js");

  assert.equal(html.replace('<script src="/api/plugin/page/bridge-sdk.js" defer></script>', '<script src="../../embedded-bridge.js" defer></script>'),
    read("pages", "陪伴小屋", "workspaces", "life", "index.html"), "workspaces/life/index.html 应与源码同步");
  assert.equal(css, read("pages", "陪伴小屋", "workspaces", "life", "style.css"), "workspaces/life/style.css 应与源码同步");
  assert.equal(js, read("pages", "陪伴小屋", "workspaces", "life", "app.js"), "workspaces/life/app.js 应与源码同步");

  const bundle = read("pages", "陪伴小屋", "workspace-bundles", "life.js");
  assert.match(bundle, /life-toolbar/);
  assert.match(bundle, /sleep-overview/);
  assert.match(bundle, /scope-banner/);

  // 紧凑顶栏与嵌入态零巨型留白契约
  assert.match(html, /<header id="life-toolbar" class="life-toolbar">/);
  assert.match(html, /class="eyebrow-dot"/);
  assert.match(html, /class="kpi-card"/);
  assert.match(html, /class="scope-banner"/);
  assert.match(html, /<details id="sleep-overview" class="sleep-overview" open>/);
  assert.match(html, /id="sleep-pressure-bar"/);
  assert.match(html, /id="sleep-workload-bar"/);

  const cleanCss = stripComments(css);
  assert.match(cleanCss, /:root\[data-embedded="true"\]\s+main\s*\{[^}]*padding:\s*12px\s+16px\s+20px/);
  assert.match(cleanCss, /header\.life-toolbar\s*\{[^}]*min-height:\s*44px[^}]*border-bottom:\s*1px solid var\(--line\)/);
  assert.match(cleanCss, /\.scope\s*\{[^}]*grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(cleanCss, /\.sleep-physiology\s*\{[^}]*grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(cleanCss, /\.sleep-plan\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
  assert.doesNotMatch(cleanCss, /#4f53a8|#f5f5fa/i, "不得残留旧版冷紫色硬编码");
  assert.doesNotMatch(cleanCss, /box-shadow:\s*0\s+22px\s+80px\s+var\(--shadow\)/, "不得残留非法 var(--shadow) 阴影简写");
  assert.doesNotMatch(js, /\b(?:localStorage|sessionStorage)\b/, "日记与生活工作区不得硬依赖浏览器本地存储");
});

test("日记与生活工作区：六套主题色板齐全且核心前景/背景对比度全部满足 WCAG AA (>= 4.5:1)", () => {
  const css = read("pages", "日记与生活", "style.css");
  const themes = extractThemeMaps(css);
  assert.equal(themes.size, 6);

  const pairs = [
    ["--ink", "--page"],
    ["--ink", "--surface"],
    ["--ink", "--surface-muted"],
    ["--muted", "--surface"],
    ["--muted", "--surface-muted"],
    ["--accent-strong", "--accent-soft"],
    ["--success", "--success-soft"],
    ["--warning", "--warning-soft"],
    ["--danger", "--danger-soft"],
    ["--on-solid", "--accent-solid"],
    ["--on-solid", "--accent-solid-hover"],
  ];

  for (const [name, map] of themes) {
    for (const [fg, bg] of pairs) {
      assert.ok(map[fg] && map[bg], `${name} 缺少 ${fg} 或 ${bg}`);
      const ratio = contrastRatio(map[fg], map[bg]);
      assert.ok(ratio >= 4.5, `${name}: ${fg} (${map[fg]}) on ${bg} (${map[bg]}) 仅 ${ratio.toFixed(2)}:1 (< 4.5:1)`);
    }
  }

  for (const darkName of ["night", "walnut"]) {
    const dark = themes.get(darkName);
    assert.ok(relativeLuminance(hexToRgb(dark["--page"])) < 0.05, `${darkName} 页面底色必须是深色`);
    assert.ok(contrastRatio(dark["--ink"], dark["--surface"]) >= 7.0, `${darkName} 正文对比度应 >= 7:1`);
  }
});

test("日记与生活工作区运行时：焦点区分折叠/展开睡眠卡片、进度条渲染与轮询零冗余 DOM 重建", async () => {
  // 1) 嵌入态 · 日记书桌（tab = archive）：默认收起睡眠详情卡片以突出私人书写，同时展示紧凑摘要胶囊
  const desk = runLifeWorkspace({ embedded: true, focusTab: "archive", status: makeSampleLifeStatus() });
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  assert.equal(desk.document.documentElement.dataset.embedded, "true");
  assert.equal(desk.document.documentElement.dataset.focusTab, "archive");
  assert.equal(desk.elements.get("sleep-overview").open, false, "日记书桌嵌入态应默认收起睡眠详情");
  assert.equal(desk.elements.get("host-state-card").dataset.tone, "ok");
  assert.equal(desk.elements.get("routine-state-card").dataset.tone, "ok");
  assert.equal(desk.elements.get("sleep-pressure-bar").style.width, "68%");
  assert.equal(desk.elements.get("sleep-workload-bar").style.width, "54%");
  assert.equal(desk.elements.get("sleep-debt-card").dataset.tone, "warn");
  assert.equal(desk.elements.get("sleep-active-wake-card").dataset.tone, "active");
  assert.equal(desk.elements.get("sleep-summary-pills").children.length, 4);
  assert.equal(desk.elements.get("sleep-summary-pills").children[0].textContent, "睡眠中");
  assert.equal(desk.elements.get("sleep-summary-pills").children[1].textContent, "压力 68/100");
  assert.equal(desk.elements.get("next-routines").hidden, false);

  // 切到睡眠记录页签后自动展开睡眠仪表盘
  desk.tabButtons.find(b => b.dataset.tab === "sleep").click();
  assert.equal(desk.elements.get("sleep-overview").open, true);

  // 重复刷新相同数据时摘要胶囊不重复重建 DOM
  const beforeRebuilds = desk.elements.get("sleep-summary-pills").replaceChildrenCount;
  desk.elements.get("refresh").click();
  await new Promise(setImmediate);
  await new Promise(setImmediate);
  assert.equal(desk.elements.get("sleep-summary-pills").replaceChildrenCount, beforeRebuilds);

  // 2) 嵌入态 · 睡眠与梦境（tab = sleep）：默认展开睡眠仪表盘
  const bed = runLifeWorkspace({ embedded: true, focusTab: "sleep", status: makeSampleLifeStatus({ next_diary_at: null, next_sleep_at: null }) });
  await new Promise(setImmediate);
  await new Promise(setImmediate);
  assert.equal(bed.elements.get("sleep-overview").open, true, "睡眠与梦境嵌入态应默认展开睡眠仪表盘");
  assert.equal(bed.elements.get("next-routines").hidden, true, "无下次例程时间时应隐藏提示胶囊");

  // 3) 独立页模式（非嵌入）：默认展开睡眠仪表盘，保持独立页兼容
  const standalone = runLifeWorkspace({ embedded: false, focusTab: "", status: makeSampleLifeStatus() });
  await new Promise(setImmediate);
  await new Promise(setImmediate);
  assert.equal(standalone.document.documentElement.dataset.embedded, undefined);
  assert.equal(standalone.elements.get("sleep-overview").open, true);

  // 4) 读取失败态：标红状态卡片并清空进度条与摘要胶囊
  const failed = runLifeWorkspace({ embedded: true, focusTab: "archive", status: makeSampleLifeStatus(), rejectLoad: true });
  await new Promise(setImmediate);
  await new Promise(setImmediate);
  assert.equal(failed.elements.get("host-state-card").dataset.tone, "danger");
  assert.equal(failed.elements.get("sleep-pressure-bar").style.width, "0%");
  assert.equal(failed.elements.get("sleep-summary-pills").children.length, 0);
});

test("全部 7 个家具面板顶栏状态映射准确（消除错误「配置未知」），且支持原位实时刷新", () => {
  const h = createFurnitureAppHarness({
    manifest: {
      extensions: [
        { capability_id: "content", extension_id: "content", assembled: true, desired_state: "active", integration_state: "connected", runtime_ready: true, authorization: { ready: true }, reason: "已就绪" },
        { capability_id: "image", extension_id: "image", assembled: true, desired_state: "active", integration_state: "connected", runtime_ready: true },
      ],
      providers: [],
    },
    data: {
      life: makeSampleLifeStatus({ sleep_schedule: { ...makeSampleLifeStatus().sleep_schedule, active_wake_at: null } }),
      state: { settings: { calendar_enabled: true, planning_enabled: true, schedule_simulation_mode: "planned" } },
      growth: { items: [{ kind: "goal", state: "active", title: "曲目定稿" }] },
    },
  });

  // 1. 日记书桌
  const deskHtml = h.renderFurniturePanel("desk");
  assert.match(deskHtml, /data-furniture-status-chip="desk">运行中 · 已启用<\/span>/);
  assert.doesNotMatch(deskHtml, /配置未知/);

  // 2. 睡眠与梦境（清醒自动作息态 -> 运行中 · 已启用；睡眠中 -> 睡眠中 lavender）
  const bedAwakeHtml = h.renderFurniturePanel("bed");
  assert.match(bedAwakeHtml, /data-furniture-status-chip="bed">运行中 · 已启用<\/span>/);
  assert.doesNotMatch(bedAwakeHtml, /配置未知/);

  h.stateData.data.life.sleep_schedule.active_wake_at = "2026-11-22T01:12:00Z";
  h.setActiveChip("bed");
  h.syncFurniturePanelStatus();
  assert.equal(h.chipNode.textContent, "睡眠中");
  assert.equal(h.chipNode.className, "pill lavender");

  // 3. 生活挂历
  const calHtml = h.renderFurniturePanel("calendar");
  assert.match(calHtml, /data-furniture-status-chip="calendar">自动规划 · 已启用<\/span>/);
  assert.doesNotMatch(calHtml, /配置未知/);
  h.stateData.data.state.settings.schedule_simulation_mode = "continuous";
  assert.deepEqual([...h.furniturePanelStatus("calendar")], ["连续日程 · 运行中", ""]);
  h.stateData.data.state.settings.calendar_enabled = false;
  assert.deepEqual([...h.furniturePanelStatus("calendar")], ["日程已关闭", "gray"]);

  // 4. 成长花架
  const gardenHtml = h.renderFurniturePanel("garden");
  assert.match(gardenHtml, /data-furniture-status-chip="garden">进行中 1 项<\/span>/);
  assert.doesNotMatch(gardenHtml, /配置未知/);
  h.stateData.data.growth.items = [];
  assert.deepEqual([...h.furniturePanelStatus("garden")], ["已就绪 · 暂无记录", "gray"]);

  // 5. 创作书架（保留 tone 语义色并呈现结构化说明卡）
  const shelfHtml = h.renderFurniturePanel("shelf");
  assert.match(shelfHtml, /data-furniture-status-chip="shelf">已装配<\/span>/);
  assert.match(shelfHtml, /furniture-brief-structured/);
  assert.match(shelfHtml, /data-detail="books"/);
  assert.match(shelfHtml, /data-output-target="creation"/);
  h.syncManifest({ extensions: [{ capability_id: "content", assembled: false, desired_state: "discarded", integration_state: "unassembled" }], providers: [] });
  assert.deepEqual([...h.furniturePanelStatus("shelf", { cap: "content" })], ["未装配", "gray"]);

  // 6. 见闻电台
  const radioHtml = h.renderFurniturePanel("radio");
  assert.match(radioHtml, /data-furniture-status-chip="radio">基础能力 · 可用<\/span>/);
  assert.match(radioHtml, /furniture-brief-tiles/);
  assert.match(radioHtml, /data-detail="discoveries"/);
  assert.doesNotMatch(radioHtml, /配置未知/);

  // 7. 衣柜陈设（含副标题、状态芯片、结构化说明卡与既有动作契约；区分有无 image 能力）
  const wardrobeBasicHtml = h.renderFurniturePanel("wardrobe");
  assert.match(wardrobeBasicHtml, /每日穿搭、参考素材与衣柜陈设/);
  assert.match(wardrobeBasicHtml, /data-furniture-status-chip="wardrobe">基础陈设<\/span>/);
  h.syncManifest({
    extensions: [
      { capability_id: "image", extension_id: "image", assembled: true, desired_state: "active", integration_state: "connected", runtime_ready: true },
    ],
    providers: [],
  });
  const wardrobeHtml = h.renderFurniturePanel("wardrobe");
  assert.match(wardrobeHtml, /data-furniture-status-chip="wardrobe">已装配<\/span>/);
  assert.match(wardrobeHtml, /data-detail="arrange"/);
  assert.match(wardrobeHtml, /data-manage="output" data-output-target="materials"/);

  // 加载态不误报配置未知；真正未连接且无数据时才回落配置未知
  const loadingHarness = createFurnitureAppHarness({ catalog: null, data: {}, connectionState: "checking", manifest: null });
  assert.deepEqual([...loadingHarness.furniturePanelStatus("desk")], ["读取中…", "gray"]);
  assert.deepEqual([...loadingHarness.furniturePanelStatus("bed")], ["读取中…", "gray"]);
  const unknownHarness = createFurnitureAppHarness({ catalog: null, role: null, data: {}, connectionState: "unavailable", hosted: false, manifest: null });
  assert.deepEqual([...unknownHarness.furniturePanelStatus("desk")], ["配置未知", "gray"]);
  assert.deepEqual([...unknownHarness.furniturePanelStatus("radio")], ["配置未知", "gray"]);
});

test("成长花架（product-page.js）、product-page.css 零硬编码色与生活挂历容器响应式契约", () => {
  const productCss = stripComments(read("pages", "陪伴小屋", "product-page.css"));
  const productJs = read("pages", "陪伴小屋", "product-page.js");
  const stateCss = stripComments(read("pages", "陪伴小屋", "state-page.css"));

  // product-page.css：零硬编码色、零裸 fr、使用 --t-canvas 贴合 6 主题、含家具结构化样式
  assert.doesNotMatch(productCss, /#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(/, "product-page.css 必须 100% 走语义令牌");
  assert.match(productCss, /\.product-workspace\{[^}]*background:var\(--t-canvas\)/);
  assert.match(productCss, /\.product-workspace-frame\{[^}]*background:var\(--t-canvas\)/);
  assert.match(productCss, /\.furniture-brief-structured\{[^}]*container-name:furniture-brief/);
  assert.match(productCss, /\.product-growth\{[^}]*container-name:product-growth/);
  assert.match(productCss, /\.product-growth-notice:empty,\.product-growth-error:empty\{display:none\}/);

  // product-page.js growthMarkup 契约：紧凑摘要栏、类型胶囊、空描述不输出空段落、空页不渲染翻页栏
  assert.match(productJs, /class="product-growth-toolbar"/);
  assert.match(productJs, /class="product-growth-card"/);
  assert.match(productJs, /class="product-growth-empty"/);
  assert.match(productJs, /growthCursor\|\|page\.next_cursor/);

  // state-page.css：嵌入生活挂历窄容器响应式与紧凑顶栏
  assert.match(stateCss, /\.shell-panel-body #roleStateWorkspace \.state-page-heading/);
  assert.match(stateCss, /@container state-workspace \(max-width:860px\)[\s\S]*?\.state-extra-nav\{grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(stateCss, /@container state-workspace \(max-width:680px\)[\s\S]*?\.state-workspace-overview\{grid-template-columns:minmax\(0,1fr\)/);
  assert.match(stateCss, /@container state-workspace \(max-width:460px\)[\s\S]*?\.state-dashboard-grid\{grid-template-columns:minmax\(0,1fr\)\}/);
});
