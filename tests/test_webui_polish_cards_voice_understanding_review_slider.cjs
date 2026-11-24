"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const PAGE = path.join(ROOT, "pages", "陪伴小屋");
const read = name => fs.readFileSync(path.join(PAGE, name), "utf8");
const HOST = "astrbot_plugin_private_companion";
const PREFIX = `/${HOST}/`;
const flush = () => new Promise(resolve => setImmediate(resolve));

test("主动联系滑条：权重滑条归一化 --p 补偿半圆点且去重重合比例步进 0.05", async () => {
  const js = read("contact-page.js");
  const css = read("contact-page.css");

  assert.match(css, /\.ct-weight-track\{[^}]*--ct-track:4px;[^}]*--ct-thumb:14px/);
  assert.match(css, /\.ct-weight-fill\{[^}]*width:calc\(var\(--p,0\) \* \(100% - var\(--ct-thumb\)\) \+ var\(--ct-thumb\) \/ 2\)/);
  assert.match(css, /\.ct-weight input\[type="range"\]::-webkit-slider-thumb\{[^}]*margin-top:calc\(\(var\(--ct-track\) - var\(--ct-thumb\)\) \/ 2\)/);
  assert.match(css, /\.ct-quota input\[type="range"\]::-webkit-slider-thumb\{[^}]*margin-top:calc\(\(var\(--ct-track\) - var\(--ct-thumb\)\) \/ 2\)/);
  assert.doesNotMatch(css, /\.ct-weight::before\{[^}]*right:26px/, "旧版硬编码 right:26px 错位伪元素应已移除");

  const window = {
    navigator: {clipboard: {writeText: async () => {}}},
    CompanionProduct: {
      hosted: () => true,
      scopeKey: () => "scope-1",
      identity: () => ({installation_id: "install-1", operator_id: "admin-1"}),
      role: () => ({role_id: "role-1", perception_native_api: `${PREFIX}perception-native`, config_api: `${PREFIX}config`}),
      bridgeNow: () => window.AstrBotPluginPage,
      bridgeFor: () => Promise.resolve(window.AstrBotPluginPage),
    },
    AstrBotPluginPage: {
      ready: () => Promise.resolve({pluginName: HOST}),
      apiGet: async route => route === "config"
        ? {status: "ok", data: {sections: {"proactive.contact": {
            edit_token: "a".repeat(64), scope: "installation", fields: {},
            settings: {
              enabled: true, automatic_enabled: true, dedup_enabled: true, dedup_last_message_enabled: true,
              dedup_min_overlap_ratio: 0.35, dedup_min_shared_tokens: 2,
              "chat.enabled": true, "chat.weight": 3.9,
              "care.enabled": true, "care.weight": 0.1,
              "play.enabled": true, "play.weight": 5.0,
            },
          }}}}
        : {status: "ok", data: {state: "ready", items: [], count: 0}},
      apiPost: async () => ({status: "ok", data: {}}),
    },
    document: {
      addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
      getElementById: () => null, documentElement: {dataset: {}},
      body: {classList: {add() {}, remove() {}, toggle() {}}}, title: "",
    },
    history: {state: null, replaceState() {}},
    matchMedia: () => ({matches: false}),
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
  };
  vm.runInNewContext(js, {
    window, document: window.document, navigator: window.navigator, console, Intl, Date, JSON, Math,
    Number, Object, Array, String, Set, Map, Boolean, Error, Promise,
    localStorage: window.localStorage, sessionStorage: window.localStorage, URL,
    matchMedia: window.matchMedia, setTimeout, clearTimeout, setInterval, clearInterval,
  });
  const handlers = {};
  const host = {
    isConnected: true, innerHTML: "",
    addEventListener(type, fn) { handlers[type] = fn; },
    removeEventListener() {}, contains: () => true,
    querySelector: () => null, querySelectorAll: () => [],
  };
  await window.CompanionContactPage.mount(host);
  for (let i = 0; i < 6; i++) await flush();

  // 切到契机矩阵，验证归一化比例 (3.9 - 0.1) / 4.9 = 0.775510、0.1 -> 0.000000、5.0 -> 1.000000
  handlers.click({target: {closest: s => s === "[data-view]" ? {dataset: {view: "purposes"}} : null}});
  assert.match(host.innerHTML, /class="ct-weight-track"/);
  assert.match(host.innerHTML, /data-field="chat\.weight"[\s\S]*?style="--p:0\.775510"/);
  assert.match(host.innerHTML, /data-field="care\.weight"[\s\S]*?style="--p:0\.000000"/);
  assert.match(host.innerHTML, /data-field="play\.weight"[\s\S]*?style="--p:1\.000000"/);

  // 切到额度与去重，验证 dedup_min_overlap_ratio 使用 step="0.05" 并格式化 0.35
  handlers.click({target: {closest: s => s === "[data-view]" ? {dataset: {view: "budgets"}} : null}});
  assert.match(host.innerHTML, /min="0" max="1" step="0\.05"[\s\S]*?data-field="dedup_min_overlap_ratio" value="0\.35" style="--p:0\.350000"/);
  assert.match(host.innerHTML, /<b>0\.35<\/b>/);

  // 验证拖动时实时同步 --p 与两位小数显示
  const props = {};
  const shown = {textContent: ""};
  const range = {
    type: "range", min: "0", max: "1", step: "0.05", value: "0.6",
    dataset: {field: "dedup_min_overlap_ratio"},
    style: {setProperty(k, v) { props[k] = v; }},
    closest(sel) {
      if (sel === "[data-field]") return this;
      if (sel === ".ct-quota") return {querySelector: s => s === ".ct-quota-value b" ? shown : null};
      return null;
    },
  };
  handlers.input({target: range});
  assert.equal(props["--p"], "0.600000");
  assert.equal(shown.textContent, "0.60");
  window.CompanionContactPage.unmount();
});

test("自我状态详情卡：状态详情卡支持 narrative/metric 自适应排版、心情四维微条、单收起按钮与快捷配置条", async () => {
  const stateJs = read("state-page.js");
  const editorJs = read("state-editor.js");
  const stateConfigCss = read("state-config.css");

  assert.match(stateConfigCss, /\.state-workspace-overview\[data-value-kind="narrative"\] \.state-workspace-current>strong\{[^}]*font-size:var\(--fs-lead\)/);
  assert.match(stateConfigCss, /\.state-mood-axes\{[^}]*grid-template-columns:repeat\(4,minmax\(0,1fr\)\)/);
  assert.match(stateConfigCss, /\.state-feature-action-bar\{/);

  const targets = [{
    target_id: "t1", bot_id: "bot-1", scope_kind: "private",
    roles: [{role_id: "r1", state_api: `${PREFIX}state/t1`}],
  }];
  const status = {
    role_edit_token: "role-tok", edit_token: "win-tok",
    sleep_schedule: {}, effective: {}, presentation: {}, presentation_diagnostics: {},
    settings: {
      enabled: true, energy_enabled: true, hunger_enabled: true, health_enabled: true, mood_enabled: true,
      humanized_state_intensity: 60, hunger_expression_prompt: "",
    },
    state: {
      state: "active", updated_at: "2026-11-24T12:00:00Z",
      values: {
        energy: 78,
        hunger: "肚子稍微有点空了，午后走动多了些，想先喝口温水再垫两块小饼干",
        mood: {description: "心情挺安稳，带着点淡淡的愉快", joy: 7, anger: 1, sorrow: 2, fear: 1, baseline: "平和"},
      },
    },
  };
  const bridge = {
    ready: async () => ({pluginName: HOST}),
    apiGet: async () => ({status: "ok", data: status}),
  };
  const window = {
    CompanionProduct: {
      hosted: () => true, scopeKey: () => "t1:r1", bridgeNow: () => bridge,
      directory: () => ({installation_id: "i1", operator_id: "op1", targets}),
      selection: () => ({target_id: "t1", bot_id: "bot-1", role_id: "r1"}),
      roleTarget: () => targets[0],
    },
    CompanionStateMaintenance: {
      fields: kind => kind === "hunger" ? ["hunger_enabled"] : [],
      validField: () => true,
      canAlign: () => false,
      titles: {hunger: "饥饿模拟", intensity: "能量表达", mood: "心情模拟"},
      actionSummary: () => "已启用饥饿模拟 · 已设自定义表达",
      weatherLocationParts: () => ({}),
    },
    CompanionStateContact: {},
  };
  const document = {addEventListener() {}, querySelector: () => null, body: {classList: {toggle() {}}}, hidden: false};
  const ctx = vm.createContext({window, document, Promise, Intl, Date, console, structuredClone, JSON, setTimeout: () => 1, clearTimeout() {}});
  vm.runInContext(editorJs, ctx);
  vm.runInContext(stateJs, ctx);

  const mountFeature = async feature => {
    const root = {isConnected: true, innerHTML: "", addEventListener() {}, removeEventListener() {},
      querySelector: () => null, querySelectorAll: () => [], contains: () => false};
    await window.CompanionStatePage.mount(root, {section: "state", feature});
    for (let i = 0; i < 6; i++) await flush();
    const html = root.innerHTML;
    window.CompanionStatePage.unmount();
    return html;
  };

  const hungerHtml = await mountFeature("hunger");
  assert.match(hungerHtml, /data-value-kind="narrative"/);
  assert.match(hungerHtml, /data-feature-tone="warn"/);
  assert.doesNotMatch(hungerHtml, /state-feature-back/, "头部应去除重复返回按钮，仅保留右侧单个收起按钮");
  assert.equal((hungerHtml.match(/data-state-feature-close/g) || []).length, 1);
  assert.doesNotMatch(hungerHtml, /<dt>生效范围<\/dt>/, "头部已有范围胶囊，元信息条不应重复展示生效范围");
  assert.match(hungerHtml, /class="state-feature-action-bar"/);

  const moodHtml = await mountFeature("mood");
  assert.match(moodHtml, /data-value-kind="narrative"/);
  assert.match(moodHtml, /class="state-mood-axes"/);
  assert.match(moodHtml, /data-axis="joy"[\s\S]*?>喜<[\s\S]*?>7</);
  assert.match(moodHtml, /基线 平和/);

  const energyHtml = await mountFeature("energy");
  assert.match(energyHtml, /data-value-kind="metric"/);
  assert.match(energyHtml, /78 \/ 100/);
  assert.match(energyHtml, /<meter min="0" max="100" value="78"/);
});

test("表达语音配置：表达语音配置精简标签、控件底对齐、去除无图例竖条且仅脏态吸底保存", () => {
  const replyJs = read("reply-page.js");
  const voiceCss = read("voice-page.css");
  const wbCss = read("workbench.css");
  const wbJs = read("workbench-page.js");

  assert.match(replyJs, /voice_segment_mode:\s*"朗读段落"/);
  assert.doesNotMatch(replyJs, /voice_segment_mode:\s*"朗读段落（整段会等文字收齐后合成一次）"/);
  assert.match(voiceCss, /#replyWorkspace\.expression-voice-focus \.wb-field>\.wb-rail\{display:none\}/);
  assert.match(voiceCss, /#replyWorkspace\.expression-voice-focus \.wb-fields>\.wb-field>\.wb-field-main\{[^}]*display:flex;flex-direction:column;height:100%/);
  assert.match(voiceCss, /#replyWorkspace\.expression-voice-focus \.wb-fields>\.wb-field>\.wb-field-main>\.wb-control\{[^}]*margin-top:auto/);
  assert.match(voiceCss, /#replyWorkspace\.expression-voice-focus \.wb-actions\{[^}]*position:static/);
  assert.match(voiceCss, /#replyWorkspace\.expression-voice-focus \.wb-actions\.is-dirty\{[^}]*position:sticky/);
  const ctrlInputIdx = voiceCss.indexOf("#replyWorkspace.expression-voice-focus .wb-control input");
  const numInputIdx = voiceCss.indexOf("#replyWorkspace.expression-voice-focus .wb-control .wb-number input");
  assert.ok(ctrlInputIdx > 0 && numInputIdx > ctrlInputIdx, ".wb-control .wb-number input 必须位于 .wb-control input 之后，避免同权重被 width:100% 覆盖");
  assert.doesNotMatch(wbCss, /#0002/, "workbench.css 气泡阴影应走主题令牌 --t-shadow-lift");
  assert.match(wbJs, /class="wb-actions-status"/, "保存范围 ⓘ 应移至右侧状态区，不再夹在按钮之间");
});

test("理解页与宿主开关：理解页合并转发精简层次、自定义胶囊开关、清理视频说明泄漏与子面板 06", async () => {
  const appJs = read("app.js");
  const hostJs = read("host-options-page.js");
  const hostCss = read("host-options.css");
  const replyJs = read("reply-page.js");

  assert.doesNotMatch(appJs, /子面板 06/, "理解总览不应残留开发期编号「子面板 06」");
  assert.doesNotMatch(appJs, /转发摄取上限/, "合并转发页签不应残留误导性的全宽「转发摄取上限」跳链");
  const forwardsSection = appJs.slice(appJs.indexOf('data-understanding-panel="forwards"'));
  const editorPos = forwardsSection.indexOf('data-understanding-editor-slot="forwards"');
  const intakePos = forwardsSection.indexOf('id="forwardIntakeWorkspace"');
  assert.ok(editorPos > 0 && intakePos > editorPos, "合并转发页签应先展示当前窗口改写开关，再展示全局解析上限");

  const understandingCss = read("understanding-page.css");
  assert.match(understandingCss, /\.understanding-stats-compact \.understanding-stat:nth-child\(2\)\{border-right:0\}/, "两列折行时第 2 项须移除右边框，避免与容器边框叠出双线");
  assert.match(hostCss, /\.host-options-switch > span::before\s*\{[^}]*transform:\s*translateY\(-50%\)/);
  assert.match(hostCss, /\.host-options-switch input:checked \+ span::before\s*\{[^}]*background:\s*var\(--t-accent-solid\)/);
  assert.match(hostCss, /\.host-options-switch input:checked \+ span::after\s*\{[^}]*transform:\s*translate\(16px,\s*-50%\)/);
  assert.match(hostCss, /\.host-options-metrics-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)/);

  // 运行时验证 host-options 开关带 role="switch"、单位文案以及切换即时更新状态文案
  const handlers = {};
  const bridge = {
    apiGet: async () => ({
      status: "ok",
      data: {
        forward_intake: {max_forwards: 4, max_nodes: 32, max_depth: 4, parse_nested: true},
        forward_bounds: {max_forwards: 4, max_nodes: 300, max_depth: 4},
        edit_tokens: {forward_intake: "f".repeat(64)},
        apply_modes: {forward_intake: "immediate_next_input"},
      },
    }),
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const win = {CompanionProduct: {bridgeNow: () => bridge}};
  vm.runInContext(hostJs, vm.createContext({window: win, setTimeout, clearTimeout}));
  const root = {id: "forwardIntakeWorkspace", dataset: {}, innerHTML: "",
    addEventListener(t, h) { handlers[t] = h; }, removeEventListener() {}};
  win.CompanionHostOptionsPage.mount({forwardRoot: root});
  for (let i = 0; i < 6; i++) await flush();
  assert.match(root.innerHTML, /class="host-options-metrics-grid"/);
  assert.match(root.innerHTML, /role="switch" name="parse_nested"/);
  assert.match(root.innerHTML, /范围 1–300 个节点/);
  const span = {textContent: "已开启"};
  handlers.change({target: {
    checked: false,
    parentElement: {querySelector: s => s === "span" ? span : null},
    closest: s => s.includes(".host-options-switch") ? {checked: false, parentElement: {querySelector: q => q === "span" ? span : null}} : null,
  }});
  assert.equal(span.textContent, "已关闭");
  win.CompanionHostOptionsPage.unmount();

  // 验证 reply-page.js 按理解页签动态切换 scopeNote，不再把视频说明泄漏到合并转发/图片/文件页签
  assert.match(replyJs, /function perceptionScopeNote\(module\)/);
  assert.match(replyJs, /if \(module === "forwards"\)/);
});

test("审阅中心空态：审阅中心 0 条空态合并为单卡温暖引导并附学习分栏直达入口", async () => {
  const reviewJs = read("learning-review-page.js");
  const bridge = {
    apiGet: async () => ({status: "ok", data: {items: [], next_cursor: null}}),
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const root = {
    isConnected: true, innerHTML: "",
    ownerDocument: {querySelector: () => null},
    addEventListener() {}, removeEventListener() {},
    querySelector: () => null, querySelectorAll: () => [],
  };
  const win = {
    CompanionProduct: {bridgeNow: () => bridge, unwrap: v => v},
    lucide: {createIcons() {}},
    addEventListener() {}, removeEventListener() {},
  };
  const ctx = vm.createContext({window: win, setTimeout, clearTimeout, Date, Math, Number, Object, Array, String, Set, Map});
  vm.runInContext(reviewJs, ctx);
  const page = ctx.window.CompanionLearningReviewPage;
  page.mount(root, {sources: [{key: "r1", label: "角色 A", growthApi: `${PREFIX}t/r/growth`, replyApi: `${PREFIX}t/r/reply`}]});
  for (let i = 0; i < 40 && page.state?.loading; i++) await flush();

  assert.match(root.innerHTML, /class="lh-review-empty-state"/);
  assert.match(root.innerHTML, /当前没有待批阅记录/);
  assert.match(root.innerHTML, /href="#manage\/learning\/chain"/);
  assert.match(root.innerHTML, /href="#manage\/learning\/expression"/);
  assert.match(root.innerHTML, /href="#manage\/learning\/reflection"/);
  assert.doesNotMatch(root.innerHTML, /class="lh-review-detail-empty"/, "空态不应再同时渲染左右两个空盒子");
  page.unmount();
});

test("感知总览未选私聊窗口：感知总览未选择私聊窗口时，外部资讯、重要日期与余额感知不误报未配置/未启用", async () => {
  const overviewJs = read("perception-overview.js");
  const fixedNow = new Date("2026-10-01T10:00:00Z");
  class FixedDate extends Date {
    constructor(...args) { super(...(args.length ? args : [fixedNow])); }
    static now() { return fixedNow.getTime(); }
  }
  for (const windowKind of ["none", "group", "other_bot"]) {
    const parentNodes = {
      '[data-perception-tab="config-injection"]': {hidden: false},
      "#perception-detail-mihome": {hidden: true},
      "#mihomeStatusWorkspace": {innerHTML: ""},
      "#perceptionRecallWorkspace": {innerHTML: ""},
      "#perceptionInjectionWorkspace": {innerHTML: ""},
      "#perceptionBalanceSourceWorkspace": {innerHTML: "", addEventListener() {}, querySelector: () => null},
    };
    const root = {
      isConnected: true, innerHTML: "",
      addEventListener() {}, removeEventListener() {},
      querySelector: () => ({textContent: "", hidden: false, focus() {}}),
      parentElement: {querySelector: sel => parentNodes[sel] || null},
    };
    const product = {
      scopeKey: () => `role-1:${windowKind}`,
      role: () => ({role_id: "role-1", model_api: `${PREFIX}model`, perception_native_api: `${PREFIX}perception-native`, reply_api: "reply"}),
      provider: () => ({integration_state: "missing", reason: "未安装"}),
      statusLabel: v => v,
      data: {state: {state: {values: {}, settings: {}}}},
      stateValues: () => null,
      selection: () => ({target_id: windowKind === "none" ? null : "t1", bot_id: "bot-1", role_id: "role-1"}),
      roleSelection: () => ({target_id: "rep-1", bot_id: "bot-1", role_id: "role-1"}),
      target: () => windowKind === "none" ? null : {target_id: "t1", bot_id: windowKind === "other_bot" ? "bot-2" : "bot-1", scope_kind: windowKind === "group" ? "group" : "private"},
      windowRole: () => product.role(),
      relative: v => v, unwrap: v => v,
      request: async (_m, route) => {
        if (route.endsWith("/runtime")) return {observed_at: "2026-10-01T10:00:00Z", weather: {state: "available", location: "杭州", condition: "晴", temperature_c: 22}};
        if (route.endsWith("/materials")) return {state: "ready", checked_at: "2026-10-01T09:50:00Z", count: 2, items: [{title: "AI 早报一"}, {title: "AI 早报二"}]};
        if (route.endsWith("/voice")) return {languages: [], providers: [], voice_enabled: true};
        if (route.endsWith("/image")) return {actions: []};
        if (route === "huawei-health/status") return {bound: false};
        return {tasks: [], providers: []};
      },
      bridgeNow: () => ({
        apiGet: async route => route === "host-options/balance"
          ? {service_balance: {source: "none", source_state: "not_configured"}, edit_tokens: {balance: "b".repeat(64)}}
          : {settings: {}},
        apiPost: async () => ({}),
      }),
    };
    const native = {
      subscribe(cb) {
        cb({ready: true, windowRequired: true, environment: {locationName: "杭州", weatherReady: true}, model: {}, contact: {}, news: {}});
        return () => {};
      },
      refresh() {},
    };
    const win = {CompanionProduct: product, CompanionNativePerceptionPage: native, CompanionPerceptionTabs: {activate() {}}};
    vm.runInNewContext(overviewJs, {
      window: win, Date: FixedDate, Intl, JSON, Number, String, Array, Object, RegExp,
      AbortController, setInterval: () => 1, clearInterval() {}, setTimeout, clearTimeout,
    });
    win.CompanionPerceptionOverviewPage.mount(root);
    for (let i = 0; i < 6; i++) await flush();

    assert.doesNotMatch(root.innerHTML, /尚未配置外部来源/, `${windowKind} 下外部资讯不得误报尚未配置`);
    assert.doesNotMatch(root.innerHTML, /暂无近期生日与纪念日/, `${windowKind} 下重要日期不得误报暂无生日纪念日`);
    assert.doesNotMatch(root.innerHTML, /服务余额联系契机未启用/, `${windowKind} 下余额感知不得误报未启用`);
    assert.match(win.CompanionPerceptionOverviewPage.preview("ext-external"), /未选择窗口/);
    assert.match(win.CompanionPerceptionOverviewPage.preview("ext-external"), /请选择私聊窗口/);
    assert.match(win.CompanionPerceptionOverviewPage.preview("env-dates"), /未选择窗口/);
    assert.match(win.CompanionPerceptionOverviewPage.preview("env-dates"), /请先选择当前角色的私聊窗口/);
    win.CompanionPerceptionOverviewPage.unmount();
  }
});
