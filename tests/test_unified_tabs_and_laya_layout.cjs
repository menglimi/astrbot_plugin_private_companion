"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const PAGE = path.resolve(__dirname, "../pages/陪伴小屋");
const readPage = name => fs.readFileSync(path.join(PAGE, name), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));

function hexToRgb(hex) {
  const clean = hex.trim().replace(/^#/, "");
  const full = clean.length === 3 ? clean.split("").map(c => c + c).join("") : clean;
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16),
  ];
}

function resolveColor(expr, vars) {
  const value = expr.trim();
  if (value.startsWith("#")) return hexToRgb(value);
  const varMatch = /^var\((--[a-z0-9-]+)\)$/.exec(value);
  if (varMatch && vars.has(varMatch[1])) return resolveColor(vars.get(varMatch[1]), vars);
  const mixMatch = /^color-mix\(in srgb,\s*([^,]+?)\s+(\d+(?:\.\d+)?)%,\s*(.+)\)$/.exec(value);
  if (mixMatch) {
    const a = resolveColor(mixMatch[1], vars);
    const pct = Number(mixMatch[2]) / 100;
    const b = resolveColor(mixMatch[3], vars);
    return a.map((channel, idx) => channel * pct + b[idx] * (1 - pct));
  }
  throw new Error(`Unsupported color expression: ${expr}`);
}

function relativeLuminance([r, g, b]) {
  const lin = [r, g, b].map(v => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

function contrastRatio(rgbA, rgbB) {
  const l1 = relativeLuminance(rgbA);
  const l2 = relativeLuminance(rgbB);
  const [hi, lo] = l1 >= l2 ? [l1, l2] : [l2, l1];
  return (hi + 0.05) / (lo + 0.05);
}

test("全站 6 套主题均声明完整标签页语义令牌、满足 WCAG AA 对比度且主/次标签栏全部接入", () => {
  const tokens = readPage("tokens.css");
  const expectedPerTheme = [
    "--t-tab-track",
    "--t-tab-hover-bg",
    "--t-tab-active-bg",
    "--t-tab-active-fg",
    "--t-tab-active-border",
    "--t-tab-active-shadow",
  ];
  const blocks = [...tokens.matchAll(/([^{}]+)\{([^{}]+)\}/g)];
  for (const theme of ["dawn", "courtyard", "dusk", "night", "walnut", "contrast"]) {
    const body = blocks
      .filter(([, sel]) => sel.includes(`data-theme="${theme}"`))
      .map(([, , content]) => content)
      .join("\n");
    const vars = new Map();
    for (const [, k, v] of body.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
      vars.set(k.trim(), v.trim());
    }
    for (const token of expectedPerTheme) {
      assert.ok(vars.has(token), `${theme} 主题缺少 ${token}`);
    }
    const ratio = contrastRatio(
      resolveColor(vars.get("--t-tab-active-fg"), vars),
      resolveColor(vars.get("--t-tab-active-bg"), vars),
    );
    assert.ok(ratio >= 4.5, `${theme} 主题激活标签对比度过低：${ratio.toFixed(2)}:1`);
  }
  assert.match(tokens, /--t-tab-radius:\s*var\(--r-sm\)/);
  assert.match(tokens, /--t-tab-track-radius:\s*var\(--r-md\)/);
  assert.match(tokens, /--t-tab-ease:\s*cubic-bezier\(0\.22,1,0\.36,1\)/);

  // 主顶栏与次级筛选胶囊统一采用柔边激活态令牌，不再使用厚重实心 --t-accent-solid
  const hubCss = readPage("learning-hub.css");
  assert.match(hubCss, /\.learning-hub-tab\{[^}]*border-radius:var\(--t-tab-radius\)/);
  assert.match(hubCss, /\.learning-hub-tab\.is-active,\.learning-hub-tab\.is-active:hover\{[^}]*background:var\(--t-tab-active-bg\)/);
  assert.match(hubCss, /\.lh-samples-filters button\[aria-pressed="true"\]\{[^}]*background:var\(--t-tab-active-bg\)/);
  assert.match(hubCss, /\.lh-signal-filter\.is-active\{[^}]*background:var\(--t-tab-active-bg\)/);
  assert.match(hubCss, /\.lh-review-filters button\[aria-pressed="true"\]\{[^}]*background:var\(--t-tab-active-bg\)/);

  const outputCss = readPage("output-page.css");
  assert.match(outputCss, /\.output-config-tabs\{[^}]*background:var\(--t-tab-track\)/);
  assert.match(outputCss, /\.output-config-tab\[aria-selected="true"\]\{[^}]*background:var\(--t-tab-active-bg\)/);

  const understandingCss = readPage("understanding-page.css");
  assert.match(understandingCss, /\.understanding-tabs button\[aria-selected=true\]\{[^}]*background:var\(--t-tab-active-bg\)/);

  const decisionCss = readPage("decision-page.css");
  assert.match(decisionCss, /\.decision-tab\.is-active\{[^}]*background:var\(--t-tab-active-bg\)/);

  const pricingCss = readPage("pricing-workbench.css");
  assert.match(pricingCss, /\.pw-day-pill\.on\s*\{[^}]*background:\s*var\(--t-tab-active-bg\)/);

  // 清理 learning-page.css 重复定义与 semantic-page.css 旧深色死代码
  const learningPageCss = readPage("learning-page.css");
  assert.doesNotMatch(learningPageCss, /\.learning-hub-nav\s*\{/);
  const semanticCss = readPage("semantic-page.css");
  assert.doesNotMatch(semanticCss, /\.perception-config-nav button\.active\s*\{/);
});

test("模型与连接顶栏锚定在首行并均衡分组，Laya 页面分组对齐且实时输入同步解锁保存按钮", async () => {
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
      getItem: k => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, v),
      removeItem: k => store.delete(k),
    },
    crypto: {randomUUID: () => "id-" + requests.length},
  };
  vm.runInNewContext(readPage("workbench-page.js"), {window, setTimeout, clearTimeout});
  vm.runInNewContext(readPage("models-page.js"), {window, crypto: window.crypto, AbortController, setTimeout, clearTimeout});

  const handlers = {};
  const saveBtnStub = {disabled: true};
  const numInputStub = {value: "45"};
  const rangeInputStub = {value: "45"};
  const recPillStub = {textContent: ""};
  const node = {
    isConnected: true,
    innerHTML: "",
    addEventListener(type, fn) { handlers[type] = fn; },
    removeEventListener(type) { delete handlers[type]; },
    querySelector(sel) {
      if (sel === "[data-laya-save]") return saveBtnStub;
      if (sel === '[data-laya-field="followup_confidence_threshold"]') return numInputStub;
      if (sel === '[data-laya-range="followup_confidence_threshold"]') return rangeInputStub;
      if (sel === '[data-laya-rec="followup_confidence_threshold"]') return recPillStub;
      return null;
    },
    querySelectorAll() { return []; },
    fire(type, selector, dataset) {
      handlers[type]({
        type,
        target: {
          type: dataset?.inputType || "text",
          dataset: dataset || {},
          value: dataset?.value,
          checked: dataset?.checked,
          valueAsNumber: dataset?.valueAsNumber,
          matches: s => s === selector,
          closest: s => (s === selector ? {dataset} : null),
        },
      });
    },
  };

  window.CompanionModelsPage.mount(node);
  requests[0].resolve({
    tasks: [],
    providers: [{id: "p1", capabilities: ["text"], max_output_tokens: 4096}],
    host_state: "running",
  });
  await flush();

  // 模型顶栏锚定在提示条之前，分组采用双区均衡排布且不含突兀短竖线
  const tabsIdx = node.innerHTML.indexOf("data-model-tabs-wrap");
  const bannerIdx = node.innerHTML.indexOf("打开向量模型配置");
  assert.ok(tabsIdx >= 0 && bannerIdx > tabsIdx, "tabBar 必须位于功能模型提示条之前以固定顶部锚点");
  assert.match(node.innerHTML, /model-tabs-group is-primary/);
  assert.match(node.innerHTML, /model-tabs-group is-ops/);
  assert.doesNotMatch(node.innerHTML, /model-tabs-sep/);

  // 切换 Laya，验证页头总开关、折叠引导、4 张高价值状态卡（含生效模型名与近期表现）与滑块联动
  node.fire("click", "[data-model-tab]", {modelTab: "laya"});
  requests.find(r => r.route === "model/laya").resolve({
    enabled: true,
    mode: "remote",
    request_format: "native",
    endpoint: "https://laya.example.test/v1/systemone",
    api_key_set: true,
    proxy: "",
    remote_model: "NeoHorse-Jev-4B",
    default_model: "multilingual",
    timeout_seconds: 12,
    followup_confidence_threshold: 45,
    decision_fields: {reply_style: true, tool_need: true},
    revision: "rev-1",
  });
  requests.find(r => r.route === "model/laya/logs").resolve({
    events: [
      {status: "ok", elapsed_ms: 120, observed_at: "2026-11-27T01:00:00Z"},
      {status: "ok", elapsed_ms: 160, observed_at: "2026-11-27T01:01:00Z"},
    ],
  });
  await flush();

  assert.match(node.innerHTML, /class="model-laya-master"[^>]*data-enabled="1"/);
  assert.match(node.innerHTML, /<details class="model-laya-guide">/);
  assert.match(node.innerHTML, /NeoHorse-Jev-4B/);
  assert.match(node.innerHTML, /100% · 140 ms/);
  assert.match(node.innerHTML, /class="model-laya-presets"[\s\S]*data-laya-preset="laya"/);
  assert.match(node.innerHTML, /data-laya-range="followup_confidence_threshold"/);
  assert.doesNotMatch(node.innerHTML, /<label[^>]*>[\s\S]*?<label class="model-switch"/);

  // 实时 input（未触发 change）即刻解锁「保存 Laya 配置」并同步数字框与提示胶囊
  saveBtnStub.disabled = true;
  node.fire("input", "[data-laya-range]", {layaRange: "followup_confidence_threshold", value: "52"});
  assert.equal(saveBtnStub.disabled, false, "实时拖动滑块时应立即解锁保存按钮");
  assert.equal(numInputStub.value, "52");
  assert.equal(recPillStub.textContent, "52% · 推荐 40–55%");

  // 恢复原值时再次锁定保存按钮
  node.fire("input", "[data-laya-range]", {layaRange: "followup_confidence_threshold", value: "45"});
  assert.equal(saveBtnStub.disabled, true, "回到原始未改动值时应重新禁用保存按钮");

  // 文本输入框实时 input 同样立即解锁保存按钮
  node.fire("input", "[data-laya-field]", {layaField: "remote_model", value: "NeoHorse-Jev-4B-v2"});
  assert.equal(saveBtnStub.disabled, false, "实时编辑文本字段时应立即解锁保存按钮");
  assert.equal(window.CompanionModelsPage.isDirty(), true);

  window.CompanionModelsPage.unmount();
});

test("Laya 下半区采用双栏调试日志工作台 + 可折叠部署抽屉，清洗 Windows \\\\?\\ 前缀并修正标签对齐", async () => {
  const requests = [];
  const store = new Map();
  const copied = [];
  const window = {
    CompanionProduct: {
      hosted: () => true,
      role: () => ({model_api: "/astrbot_plugin_private_companion/model"}),
      identity: () => ({installation_id: "inst-laya-2", operator_id: "operator"}),
      bridgeNow: () => window.AstrBotPluginPage,
      bridgeFor: () => ({then: resolve => resolve(window.AstrBotPluginPage)}),
    },
    AstrBotPluginPage: {
      apiGet: (route, params) => new Promise((resolve, reject) => requests.push({method: "GET", route, params, resolve, reject})),
      apiPost: (route, values) => new Promise((resolve, reject) => requests.push({method: "POST", route, values, resolve, reject})),
    },
    sessionStorage: {
      getItem: k => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, v),
      removeItem: k => store.delete(k),
    },
    crypto: {randomUUID: () => "id-" + requests.length},
  };
  const navigator = {
    clipboard: {writeText: async text => { copied.push(text); }},
  };
  vm.runInNewContext(readPage("workbench-page.js"), {window, setTimeout, clearTimeout});
  vm.runInNewContext(readPage("models-page.js"), {window, navigator, crypto: window.crypto, AbortController, setTimeout, clearTimeout});

  const handlers = {};
  const node = {
    isConnected: true,
    innerHTML: "",
    addEventListener(type, fn) { handlers[type] = fn; },
    removeEventListener(type) { delete handlers[type]; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    fire(type, selector, dataset) {
      handlers[type]({
        type,
        target: {
          type: dataset?.inputType || "text",
          dataset: dataset || {},
          value: dataset?.value,
          checked: dataset?.checked,
          valueAsNumber: dataset?.valueAsNumber,
          matches: s => s === selector,
          closest: s => (s === selector ? {dataset} : null),
        },
      });
    },
  };

  window.CompanionModelsPage.mount(node);
  requests[0].resolve({tasks: [], providers: [], host_state: "running"});
  await flush();

  node.fire("click", "[data-model-tab]", {modelTab: "laya"});
  requests.find(r => r.route === "model/laya").resolve({
    enabled: true,
    mode: "remote",
    request_format: "native",
    endpoint: "https://laya.example.test/v1/systemone",
    api_key_set: true,
    proxy: "",
    remote_model: "multilingual",
    default_model: "multilingual",
    timeout_seconds: 12,
    followup_confidence_threshold: 45,
    package_installed: false,
    python_executable: "\\\\?\\D:\\AstrBot\\backend\\python\\python.exe",
    model_cache_path: "\\\\?\\D:\\cache\\huggingface\\hub",
    install_command: "& '\\\\?\\D:\\AstrBot\\backend\\python\\python.exe' -m pip install --no-deps 'laya==0.4.0'",
    remote_install_command: "& '\\\\?\\D:\\AstrBot\\backend\\python\\python.exe' -m pip install 'fastapi>=0.110.0'",
    remote_start_command: "$env:LAYA_DEFAULT_MODEL='multilingual'; & '\\\\?\\D:\\AstrBot\\backend\\python\\python.exe' -m laya.serve",
    decision_fields: {reply_style: true},
    revision: "rev-2",
  });
  requests.find(r => r.route === "model/laya/logs").resolve({events: []});
  await flush();

  // 双栏对称工作台（测试 + 日志）位于部署命令抽屉之前
  const wbIdx = node.innerHTML.indexOf('class="model-laya-workbench"');
  const deployIdx = node.innerHTML.indexOf("model-laya-deploy-card");
  assert.ok(wbIdx >= 0 && deployIdx > wbIdx, "调试测试与调用日志双栏工作台应位于部署抽屉上方");
  assert.match(node.innerHTML, /class="model-card model-laya-test-card"/);
  assert.match(node.innerHTML, /class="model-card model-laya-logs-card"/);

  // 远程模式下部署抽屉默认收起，点击可平滑展开
  assert.match(node.innerHTML, /data-laya-deploy-toggle aria-expanded="false"/);
  assert.match(node.innerHTML, /展开查看 3 条命令/);
  assert.match(node.innerHTML, /class="model-laya-deploy-grid is-two-col"/);
  node.fire("click", "[data-laya-deploy-toggle]", {});
  assert.match(node.innerHTML, /data-laya-deploy-toggle aria-expanded="true"/);
  assert.match(node.innerHTML, /class="model-card model-laya-deploy-card is-open"/);

  // 页面渲染和剪贴板复制均彻底去除 Windows \\?\ 扩展路径前缀
  assert.doesNotMatch(node.innerHTML, /\\\\\?\\/, "渲染文本中不应残留 \\\\?\\ 前缀");
  assert.match(node.innerHTML, /D:\\AstrBot\\backend\\python\\python\.exe/);
  assert.match(node.innerHTML, /D:\\cache\\huggingface\\hub/);
  node.fire("click", "[data-laya-copy]", {layaCopy: "remote_start_command"});
  await flush();
  assert.equal(copied.length, 1);
  assert.ok(!copied[0].includes("\\\\?\\"), "复制到剪贴板的命令不应包含 \\\\?\\ 前缀");
  assert.match(copied[0], /D:\\AstrBot\\backend\\python\\python\.exe/);

  // 切换至本地模式且未安装时，清空显式开关后默认展开以便即时引导安装
  node.fire("click", "[data-laya-deploy-toggle]", {});
  assert.match(node.innerHTML, /data-laya-deploy-toggle aria-expanded="false"/);

  // 校验 CSS 契约：标签行高 28px 消除 4.8px 偏移、全宽部署抽屉与双栏工作台、现代后台任务预算卡片
  const modelsCss = readPage("models-page.css");
  assert.match(modelsCss, /\.model-laya-label\{[^}]*min-height:28px/);
  assert.match(modelsCss, /\.model-laya-label \.model-pill\{[^}]*padding:1px var\(--sp-2\)/);
  assert.match(modelsCss, /\.model-laya-workbench\{[^}]*grid-template-columns:minmax\(0,1fr\) minmax\(0,1\.15fr\)/);
  assert.match(modelsCss, /\.model-laya-deploy-shell\{[^}]*grid-template-rows:0fr/);
  assert.match(modelsCss, /\.model-laya-codebox\{/);

  const selfCss = readPage("self-page.css");
  assert.match(selfCss, /\.models-budget-section\{[^}]*grid-template-columns:minmax\(0,1fr\)/);
  assert.match(selfCss, /\.models-budget-section \.wb-fields\{[^}]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(selfCss, /\.models-budget-status\[data-budget-status="on"\]\{/);

  const appJs = readPage("app.js");
  assert.match(appJs, /class="models-budget-head"/);
  assert.match(appJs, /data-budget-status="off"/);

  window.CompanionModelsPage.unmount();
});

test("配置工作台将超大数字上限人性化格式化（如 0 – 1 万亿）且保留小中范围原貌", async () => {
  const requests = [];
  const window = {
    addEventListener() {},
    removeEventListener() {},
    CompanionProduct: {
      hosted: () => true,
      role: () => ({config_api: "/astrbot_plugin_private_companion/config"}),
      bridgeNow: () => window.AstrBotPluginPage,
      bridgeFor: () => ({then: resolve => resolve(window.AstrBotPluginPage)}),
    },
    AstrBotPluginPage: {
      apiGet: (route, params) => new Promise((resolve, reject) => requests.push({method: "GET", route, params, resolve, reject})),
      apiPost: (route, values) => new Promise((resolve, reject) => requests.push({method: "POST", route, values, resolve, reject})),
    },
  };
  const document = {
    hidden: false,
    body: {appendChild() {}},
    createElement: () => ({style: {}, setAttribute() {}, remove() {}, addEventListener() {}, querySelectorAll: () => []}),
  };
  vm.runInNewContext(readPage("workbench-page.js"), {window, document, setTimeout, clearTimeout});

  const node = {
    isConnected: true,
    innerHTML: "",
    addEventListener() {},
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  const wb = window.CompanionWorkbench.create({
    group: "budget",
    requestSection: "budget",
    endpoint: "/astrbot_plugin_private_companion/config",
    mount: () => node,
    title: "每日 Token 软限额",
    order: ["enable_daily_token_soft_limit", "daily_token_soft_limit", "small_window"],
    labels: {
      enable_daily_token_soft_limit: "启用每日软限额",
      daily_token_soft_limit: "每日软限额（token）",
      small_window: "常规上限",
    },
  });
  wb.mount();
  await flush();
  assert.ok(requests.length >= 1, "工作台挂载后应请求配置接口");
  requests[0].resolve({
    section: "budget",
    group_id: "budget",
    scope: "role",
    edit_token: "a".repeat(64),
    revision: 1,
    configuration_token: "configuration",
    settings: {enable_daily_token_soft_limit: false, daily_token_soft_limit: 500000, small_window: 3600},
    provenance: {
      enable_daily_token_soft_limit: {layer: "role", explicit: true, revision: 1},
      daily_token_soft_limit: {layer: "role", explicit: true, revision: 1},
      small_window: {layer: "role", explicit: true, revision: 1},
    },
    layers: ["installation", "role"],
    fields: {
      enable_daily_token_soft_limit: {kind: "boolean", default: false, apply_mode: "immediate", editable_layers: ["installation", "role"]},
      daily_token_soft_limit: {kind: "integer", default: 500000, minimum: 0, maximum: 1000000000000, apply_mode: "immediate", editable_layers: ["installation", "role"]},
      small_window: {kind: "integer", default: 3600, minimum: 0, maximum: 86400, apply_mode: "immediate", editable_layers: ["installation", "role"]},
    },
  });
  await flush();

  assert.match(node.innerHTML, /<span class="wb-range" title="允许范围：0 – 1,000,000,000,000">0 – 1 万亿<\/span>/);
  assert.match(node.innerHTML, /min="0" max="1000000000000"/, "原生输入框仍保留精确数字校验上下界");
  assert.match(node.innerHTML, /<span class="wb-range">0 – 86400<\/span>/, "常规数字范围保持原样");
  wb.unmount();
});
