"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const PAGE = path.resolve(__dirname, "../pages/陪伴小屋");
const readPage = name => fs.readFileSync(path.join(PAGE, name), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));

test("全站 6 套主题均声明完整玉露柔边标签页令牌且主/次标签栏全部接入", () => {
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
    for (const token of expectedPerTheme) {
      assert.ok(body.includes(`${token}:`), `${theme} 主题缺少 ${token}`);
    }
  }
  assert.match(tokens, /--t-tab-radius:\s*var\(--r-sm\)/);
  assert.match(tokens, /--t-tab-track-radius:\s*var\(--r-md\)/);
  assert.match(tokens, /--t-tab-ease:\s*cubic-bezier\(0\.22,1,0\.36,1\)/);

  // 4 大主顶栏 + 4 处次级筛选胶囊统一采用玉露柔边激活态，不再使用深绿实心 --t-accent-solid
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

  // 清理掉 learning-page.css 重复定义 & semantic-page.css 死代码
  const learningPageCss = readPage("learning-page.css");
  assert.doesNotMatch(learningPageCss, /\.learning-hub-nav\s*\{/);
  const semanticCss = readPage("semantic-page.css");
  assert.doesNotMatch(semanticCss, /\.perception-config-nav button\.active\s*\{/);
});

test("模型与连接顶栏锚定在首行且采用双区均衡布局，Laya 页面按分组对齐并支持滑块调优", async () => {
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

  // #6: tabBar 始终排在 semanticBanner 之前，且拆为 is-primary / is-ops 无突兀竖线
  const tabsIdx = node.innerHTML.indexOf("data-model-tabs-wrap");
  const bannerIdx = node.innerHTML.indexOf("打开向量模型配置");
  assert.ok(tabsIdx >= 0 && bannerIdx > tabsIdx, "tabBar 必须位于功能模型提示条之前以固定顶部锚点");
  assert.match(node.innerHTML, /model-tabs-group is-primary/);
  assert.match(node.innerHTML, /model-tabs-group is-ops/);
  assert.doesNotMatch(node.innerHTML, /model-tabs-sep/);

  // #10: 切换 Laya，验证页头总开关、折叠引导、4 张高价值状态卡（含生效模型名与近期表现）与滑块联动
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

  // 拖动滑块可正确更新草稿并标脏
  node.fire("change", "[data-laya-range]", {layaRange: "followup_confidence_threshold", value: "52"});
  assert.equal(window.CompanionModelsPage.isDirty(), true);
  assert.match(node.innerHTML, /52% · 推荐 40–55%/);

  window.CompanionModelsPage.unmount();
});
