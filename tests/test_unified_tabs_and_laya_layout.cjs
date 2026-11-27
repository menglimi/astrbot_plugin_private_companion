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
