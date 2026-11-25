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

test("移动端响应式：语音脏态取消吸底、外壳留白收紧、状态胶囊折行、滑条触控扩大与审阅单列平滑定位", async () => {
  const voiceCss = read("voice-page.css");
  const shellCss = read("shell.css");
  const stateCss = read("state-config.css");
  const contactCss = read("contact-page.css");
  const understandingCss = read("understanding-page.css");
  const hostCss = read("host-options.css");
  const hubCss = read("learning-hub.css");
  const reviewJs = read("learning-review-page.js");

  // 语音页：手机端同时覆盖脏态 .wb-actions.is-dirty、箭头右上固定、触屏放大 ⓘ 热区
  assert.match(voiceCss, /@media\(max-width:760px\)\{[\s\S]*?#replyWorkspace\.expression-voice-focus \.wb-actions\.is-dirty\{position:static;bottom:auto\}/);
  assert.match(voiceCss, /@media\(pointer:coarse\),\(max-width:760px\)\{[\s\S]*?\.wb-context-toggle\{width:32px;height:32px/);
  assert.match(voiceCss, /\.voice-global-summary::after\{position:absolute;top:var\(--sp-3\);right:var\(--sp-3\)\}/);

  // 全局外壳：<=560px 收紧内边距与间距（全 token）
  assert.match(shellCss, /@media \(max-width: 560px\)\s*\{\s*:root\s*\{\s*--shell-pad:\s*var\(--sp-2\);\s*\}\s*#homeShell \.shell-body\s*\{\s*gap:\s*var\(--sp-2\);\s*\}\s*\.shell-panel-body\s*\{\s*padding:\s*var\(--sp-3\);\s*\}\s*\}/);

  // 状态详情卡：长胶囊安全折行、min(200px,100%)、38px 关闭按钮、<=420px 紧凑间距
  assert.match(stateCss, /\.state-feature-action-copy\{[^}]*min-width:min\(200px,100%\)/);
  assert.match(stateCss, /@media\(max-width:700px\)\{[\s\S]*?\.state-feature-scope-pill\{white-space:normal;overflow-wrap:anywhere/);
  assert.match(stateCss, /@media\(max-width:700px\)\{[\s\S]*?\.state-feature-close\{width:38px;height:38px\}/);
  assert.match(stateCss, /@media\(max-width:420px\)\{[\s\S]*?\.state-home-feature-controls\{gap:var\(--sp-2\) var\(--sp-3\)\}/);

  // 主动联系滑条：触屏/手机 36px 热区 + 18px 圆点（居中公式沿用）、<=520px 单列满宽
  assert.match(contactCss, /@media \(pointer:coarse\),\(max-width:760px\)\{\s*\.ct-weight-track\{--ct-thumb:18px;height:36px\}\s*\.ct-weight input\[type="range"\]\{height:36px\}\s*\.ct-quota input\[type="range"\]\{--ct-thumb:18px;height:36px\}\s*\}/);
  assert.match(contactCss, /@media \(max-width:520px\)\{[\s\S]*?\.ct-purpose\{grid-template-columns:minmax\(0,1fr\)/);

  // 理解页 & 宿主选项：长 badge 窄屏安全折行左对齐
  assert.match(understandingCss, /\.understanding-scope-badge\{white-space:normal;text-align:left;max-width:100%;overflow-wrap:anywhere\}/);
  assert.match(hostCss, /@media \(max-width: 560px\)\s*\{\s*\.host-options-scope-badge\s*\{[^}]*white-space:\s*normal;[^}]*text-align:\s*left/);

  // 审阅中心 CSS：手机/触屏隐藏 kbd、显示返回队列按钮、底栏 2 列触控优化
  assert.match(hubCss, /\.lh-review-back-queue\{display:inline-flex\}/);
  assert.match(hubCss, /\.lh-review-nav-btn kbd\{display:none\}/);
  assert.match(hubCss, /@media\(pointer:coarse\)\{[\s\S]*?min-height:40px/);

  // 审阅中心运行时：390px 单列点击队列项平滑滚向详情卡，点击「返回队列」滚回队列，减弱动效降级 auto
  for (const reduced of [false, true]) {
    const scrolls = [];
    const handlers = {};
    const detailNode = {scrollIntoView: opts => scrolls.push({target: "detail", ...opts})};
    const queueNode = {scrollIntoView: opts => scrolls.push({target: "queue", ...opts})};
    const bridge = {
      apiGet: async route => ({
        status: "ok",
        data: {
          items: route.endsWith("/progress") ? [
            {review_id: "g1", growth_title: "第一条", progress_note: "进展一", base_revision: 1, created_at: "2026-11-24T10:00:00Z", actions: ["accept", "reject"], view_token: "t1", evidence: []},
            {review_id: "g2", growth_title: "第二条", progress_note: "进展二", base_revision: 1, created_at: "2026-11-24T09:00:00Z", actions: ["accept", "reject"], view_token: "t2", evidence: []},
          ] : [],
          next_cursor: null,
        },
      }),
      apiPost: async () => ({status: "ok", data: {}}),
    };
    const root = {
      isConnected: true, innerHTML: "", clientWidth: 390,
      ownerDocument: {
        documentElement: {dataset: {}},
        querySelector: () => null,
      },
      addEventListener(type, fn) { handlers[type] = fn; },
      removeEventListener() {},
      querySelector(sel) {
        if (sel === ".lh-review-detail") return detailNode;
        if (sel === ".lh-review-queue") return queueNode;
        return null;
      },
      querySelectorAll: () => [],
    };
    const win = {
      CompanionProduct: {bridgeNow: () => bridge, unwrap: v => v},
      lucide: {createIcons() {}},
      matchMedia: q => ({matches: q.includes("max-width: 820px") ? true : q.includes("prefers-reduced-motion") ? reduced : false}),
      addEventListener() {}, removeEventListener() {},
    };
    const ctx = vm.createContext({window: win, setTimeout, clearTimeout, Date, Math, Number, Object, Array, String, Set, Map});
    vm.runInContext(reviewJs, ctx);
    const page = ctx.window.CompanionLearningReviewPage;
    page.mount(root, {sources: [{key: "r1", label: "角色 A", growthApi: `${PREFIX}t/r/growth`, replyApi: `${PREFIX}t/r/reply`}]});
    for (let i = 0; i < 40 && page.state?.loading; i++) await flush();

    assert.match(root.innerHTML, /data-review-back-queue/);
    handlers.click({target: {closest: s => s === "[data-review-select]" ? {dataset: {reviewSelect: "r1::growth::g2"}} : null}});
    assert.deepEqual(scrolls[0], {target: "detail", behavior: reduced ? "auto" : "smooth", block: "start"});

    handlers.click({target: {closest: s => s === "[data-review-back-queue]" ? {dataset: {}} : null}});
    assert.deepEqual(scrolls[1], {target: "queue", behavior: reduced ? "auto" : "smooth", block: "start"});
    page.unmount();
  }
});

test("状态弹窗与详情卡动效：#state-home-settings 持久化复用且保留滚动焦点，.state-feature-detail 仅切卡入场", async () => {
  const stateCss = read("state-page.css");
  const stateConfigCss = read("state-config.css");
  const stateJs = read("state-page.js");
  const editorJs = read("state-editor.js");

  assert.doesNotMatch(stateCss, /\.state-feature-detail\{[^}]*animation:state-feature-enter/);
  assert.match(stateCss, /\.state-feature-detail\.is-entering\{animation:state-feature-enter/);
  assert.match(stateCss, /@media\(prefers-reduced-motion:reduce\)\{[^}]*\.state-feature-detail\.is-entering/);
  assert.match(stateConfigCss, /\.state-home-settings \.state-config-body>\*\{flex:0 0 auto\}/);
  assert.match(stateConfigCss, /\.state-home-settings \.state-home-settings-body:not\(:has\(#state-edit-form\)\)>\*\{flex:0 0 auto\}/);

  // 最小化 Live DOM 宿主，用于验证持久化 <dialog> 节点引用、showModal 调用次数、scrollTop 与焦点恢复
  let activeEl = null;
  const makeNode = tag => {
    const attrs = {};
    const node = {
      tagName: tag.toUpperCase(),
      id: "",
      className: "",
      parentNode: null,
      children: [],
      isConnected: true,
      open: false,
      scrollTop: 0,
      showModalCalls: 0,
      closeCalls: 0,
      _html: "",
      _scrollBody: null,
      _fieldNodes: new Map(),
      listeners: {},
      setAttribute(k, v) { attrs[k] = String(v); },
      getAttribute(k) { return Object.hasOwn(attrs, k) ? attrs[k] : null; },
      hasAttribute(k) { return Object.hasOwn(attrs, k); },
      addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
      removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter(h => h !== fn); },
      appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
      showModal() { this.open = true; this.showModalCalls += 1; },
      close() { this.open = false; this.closeCalls += 1; },
      getBoundingClientRect: () => ({left: 0, top: 0, right: 600, bottom: 500}),
      contains(target) {
        if (!target) return false;
        if (target === this) return true;
        if (this._fieldNodes && [...this._fieldNodes.values()].includes(target)) return true;
        return this.children.some(child => child.contains(target));
      },
      querySelector(sel) {
        if (sel === "#state-home-settings") {
          if (this.id === "state-home-settings") return this;
          for (const child of this.children) { const hit = child.querySelector(sel); if (hit) return hit; }
          return null;
        }
        if (sel === ".state-config-body") {
          if (this._scrollBody) return this._scrollBody;
          for (const child of this.children) { const hit = child.querySelector(sel); if (hit) return hit; }
          return null;
        }
        if (sel === ".state-home-settings-body") {
          if (this.className.includes("state-home-settings-body")) return this;
          for (const child of this.children) { const hit = child.querySelector(sel); if (hit) return hit; }
          return null;
        }
        const fieldMatch = /^\[data-state-field="([^"]+)"\]$/.exec(sel);
        if (fieldMatch) {
          const name = fieldMatch[1];
          if (this._fieldNodes?.has(name)) return this._fieldNodes.get(name);
          for (const child of this.children) { const hit = child.querySelector(sel); if (hit) return hit; }
          return null;
        }
        return null;
      },
      querySelectorAll: () => [],
      get innerHTML() { return this._html; },
      set innerHTML(val) {
        this._html = String(val ?? "");
        this._scrollBody = this._html.includes('class="state-config-body"') ? {scrollTop: 0} : null;
        this._fieldNodes = new Map();
        for (const match of this._html.matchAll(/data-state-field="([^"]+)"/g)) {
          const field = match[1];
          if (!this._fieldNodes.has(field)) {
            const ctrl = {
              dataset: {stateField: field},
              getAttribute: k => k === "data-state-field" ? field : null,
              hasAttribute: k => k === "data-state-field",
              focusCalls: 0,
              focus() { this.focusCalls += 1; activeEl = this; },
            };
            this._fieldNodes.set(field, ctrl);
          }
        }
      },
    };
    return node;
  };

  const targets = [{
    target_id: "t1", bot_id: "bot-1", scope_kind: "private",
    roles: [{role_id: "r1", state_api: `${PREFIX}state/t1`}],
  }];
  let revision = 1;
  const makeStatus = () => ({
    role_edit_token: "role-tok", edit_token: "win-tok",
    sleep_schedule: {}, effective: {}, presentation: {}, presentation_diagnostics: {},
    settings: {
      enabled: true, energy_enabled: true, hunger_enabled: true, health_enabled: true, mood_enabled: true,
      humanized_state_intensity: 60, hunger_expression_prompt: "",
    },
    state: {
      state: "active", revision, updated_at: "2026-11-24T12:00:00Z",
      values: {energy: 78, hunger: "有点饿了", mood: {description: "心情挺安稳", joy: 7, anger: 1, sorrow: 2, fear: 1}},
    },
  });
  const bridge = {
    ready: async () => ({pluginName: HOST}),
    apiGet: async () => ({status: "ok", data: makeStatus()}),
  };
  const win = {
    CompanionProduct: {
      hosted: () => true, scopeKey: () => "t1:r1", bridgeNow: () => bridge,
      directory: () => ({installation_id: "i1", operator_id: "op1", targets}),
      selection: () => ({target_id: "t1", bot_id: "bot-1", role_id: "r1"}),
      roleTarget: () => targets[0],
    },
    CompanionStateMaintenance: {
      fields: kind => kind === "home" ? ["enabled", "hunger_enabled"] : kind === "hunger" ? ["hunger_enabled"] : [],
      normalized: (_kind, values) => ({...values}),
      validField: () => true,
      canAlign: () => false,
      titles: {home: "状态与首页展示", hunger: "饥饿模拟", mood: "心情模拟"},
      actionSummary: () => "已启用",
      form: () => '<fieldset class="state-config-fields"><input type="checkbox" data-state-field="hunger_enabled" checked></fieldset>',
      weatherLocationParts: () => ({}),
    },
    CompanionStateContact: {},
    matchMedia: () => ({matches: false}),
  };
  const doc = {
    hidden: false,
    documentElement: {dataset: {}},
    body: {classList: {contains: () => false, toggle() {}}},
    get activeElement() { return activeEl; },
    createElement: tag => makeNode(tag),
    addEventListener() {},
    querySelector: () => null,
    getElementById: () => null,
  };
  const ctx = vm.createContext({
    window: win, document: doc, Promise, Intl, Date, console, structuredClone, JSON,
    setTimeout: () => 1, clearTimeout() {},
  });
  vm.runInContext(editorJs, ctx);
  vm.runInContext(stateJs, ctx);

  const root = makeNode("div");
  await win.CompanionStatePage.mount(root, {section: "state"});
  for (let i = 0; i < 6; i++) await flush();

  // 打开 #state-home-settings：首次打开调用一次 showModal()
  win.CompanionStatePage.openSettings();
  const dialog = root.querySelector("#state-home-settings");
  assert.ok(dialog, "Live DOM 模式下应挂载持久化 #state-home-settings");
  assert.equal(dialog.open, true);
  assert.equal(dialog.showModalCalls, 1);
  assert.equal(dialog.closeCalls, 0);

  // 点击弹窗内「配置状态与首页展示」触发重渲染：同一 <dialog> 实例不关闭、不重播 showModal
  root.listeners.click[0]({
    target: {closest: sel => sel === "[data-state-edit]" ? {dataset: {stateEdit: "home"}, disabled: false} : null},
  });
  assert.equal(root.querySelector("#state-home-settings"), dialog, "重渲染后必须保持同一个 <dialog> DOM 实例");
  assert.equal(dialog.showModalCalls, 1, "已打开的弹窗在内容重渲染时不得重调 showModal()");
  assert.equal(dialog.closeCalls, 0, "已打开的弹窗在内容重渲染时不得先 close() 再打开");

  // 模拟在弹窗内滚动到 210px 并聚焦开关，再触发刷新重渲染，验证 scrollTop 和焦点完整保留
  const scrollBody = dialog.querySelector(".state-config-body");
  assert.ok(scrollBody);
  scrollBody.scrollTop = 210;
  const fieldNode = dialog.querySelector('[data-state-field="hunger_enabled"]');
  assert.ok(fieldNode);
  activeEl = fieldNode;
  revision = 2;
  root.listeners.click[0]({
    target: {closest: sel => sel === "#state-refresh, #state-home-settings-refresh" ? {} : null},
  });
  for (let i = 0; i < 6; i++) await flush();
  assert.equal(root.querySelector("#state-home-settings"), dialog);
  assert.equal(dialog.showModalCalls, 1);
  assert.equal(dialog.closeCalls, 0);
  assert.equal(dialog.querySelector(".state-config-body").scrollTop, 210, "弹窗滚动条位置必须保留");
  assert.equal(activeEl?.dataset?.stateField, "hunger_enabled", "弹窗内当前聚焦控件必须保留焦点");

  // 关闭弹窗，测试 .state-feature-detail.is-entering 仅在切换详情卡首帧挂上
  root.listeners.click[0]({target: {closest: sel => sel === "[data-state-close-home-settings]" ? {} : null}});
  assert.equal(dialog.open, false);
  assert.equal(dialog.closeCalls, 1);

  const mainSlot = root.children[0];
  assert.equal(win.CompanionStatePage.openFeature("hunger"), true);
  assert.match(mainSlot.innerHTML, /class="state-feature-detail state-config-page is-entering"/, "首次展开饥饿卡应带 is-entering");

  // 同卡内触发刷新重渲染，不得重播入场动效
  revision = 3;
  root.listeners.click[0]({
    target: {closest: sel => sel === "#state-refresh, #state-home-settings-refresh" ? {} : null},
  });
  for (let i = 0; i < 6; i++) await flush();
  assert.match(mainSlot.innerHTML, /class="state-feature-detail state-config-page" data-state-config-feature="hunger"/);
  assert.doesNotMatch(mainSlot.innerHTML, /state-feature-detail[^"]*is-entering/, "同卡重渲染不得重挂 is-entering");

  // 切换到新卡「心情」时再次播放一次入场动效
  assert.equal(win.CompanionStatePage.openFeature("mood"), true);
  assert.match(mainSlot.innerHTML, /class="state-feature-detail state-config-page is-entering" data-state-config-feature="mood"/);
  win.CompanionStatePage.unmount();
});

test("主动联系闸门与测试弹窗：.ct-gate 仅进页播放，[data-test-confirm-dialog] 持久化复用且不重播 showModal", async () => {
  const contactCss = read("contact-page.css");
  const contactJs = read("contact-page.js");

  assert.doesNotMatch(contactCss, /(^|\n)\.ct-gate\{[^}]*animation:ct-gate-in/);
  assert.match(contactCss, /\.ct-gates\.is-entering \.ct-gate\{[^}]*animation:ct-gate-in/);
  assert.match(contactCss, /@media \(prefers-reduced-motion:reduce\)\{[\s\S]*?\.ct-gates\.is-entering \.ct-gate/);

  const makeNode = tag => {
    const attrs = {};
    return {
      tagName: tag.toUpperCase(),
      className: "",
      parentNode: null,
      children: [],
      isConnected: true,
      open: false,
      showModalCalls: 0,
      closeCalls: 0,
      innerHTML: "",
      listeners: {},
      setAttribute(k, v) { attrs[k] = String(v); },
      getAttribute(k) { return Object.hasOwn(attrs, k) ? attrs[k] : null; },
      hasAttribute(k) { return Object.hasOwn(attrs, k); },
      addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
      removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter(h => h !== fn); },
      appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
      showModal() { this.open = true; this.showModalCalls += 1; },
      close() { this.open = false; this.closeCalls += 1; },
      getBoundingClientRect: () => ({left: 0, top: 0, right: 500, bottom: 400}),
      contains: () => true,
      querySelector(sel) {
        if (sel === "[data-test-confirm-dialog]") {
          if (this.hasAttribute("data-test-confirm-dialog")) return this;
          for (const child of this.children) { const hit = child.querySelector(sel); if (hit) return hit; }
          return null;
        }
        return null;
      },
      querySelectorAll: () => [],
    };
  };

  const win = {
    navigator: {clipboard: {writeText: async () => {}}},
    CompanionProduct: {
      hosted: () => true,
      scopeKey: () => "scope-live",
      identity: () => ({installation_id: "install-1", operator_id: "admin-1"}),
      role: () => ({role_id: "role-1", perception_native_api: `${PREFIX}perception-native`, config_api: `${PREFIX}config`}),
      bridgeNow: () => win.AstrBotPluginPage,
      bridgeFor: () => Promise.resolve(win.AstrBotPluginPage),
    },
    AstrBotPluginPage: {
      ready: () => Promise.resolve({pluginName: HOST}),
      apiGet: async route => {
        if (route === "config") {
          return {status: "ok", data: {sections: {"proactive.contact": {
            edit_token: "a".repeat(64), scope: "installation", fields: {},
            settings: {enabled: true, automatic_enabled: true, quiet_enabled: false, note: ""},
          }}}};
        }
        if (route === "setup/proactive-recipients") {
          return {status: "ok", data: {
            selected_target_id: "target-1",
            candidates: [{target_id: "target-1", label: "核心管理员"}],
          }};
        }
        return {status: "ok", data: {state: "ready", items: [], count: 0}};
      },
      apiPost: async () => ({status: "ok", data: {}}),
    },
    document: {
      createElement: tag => makeNode(tag),
      addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
      getElementById: () => null, documentElement: {dataset: {}},
      body: {classList: {contains: () => false, add() {}, remove() {}, toggle() {}}}, title: "",
    },
    history: {state: null, replaceState() {}},
    matchMedia: () => ({matches: false}),
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
  };
  vm.runInNewContext(contactJs, {
    window: win, document: win.document, navigator: win.navigator, console, Intl, Date, JSON, Math,
    Number, Object, Array, String, Set, Map, Boolean, Error, Promise,
    localStorage: win.localStorage, sessionStorage: win.localStorage, URL,
    matchMedia: win.matchMedia, setTimeout, clearTimeout, setInterval, clearInterval,
  });

  const root = makeNode("div");
  await win.CompanionContactPage.mount(root);
  for (let i = 0; i < 6; i++) await flush();
  const mainSlot = root.children[0];

  // 首次切到「当前状态与闸门」：带 is-entering 播放入场动效
  root.listeners.click[0]({target: {closest: s => s === "[data-view]" ? {dataset: {view: "gates"}} : null}});
  assert.match(mainSlot.innerHTML, /class="ct-gates is-entering"/);

  // 同页内改字段/触发重渲染：不再带 is-entering，避免 5 张闸门卡反复滑入
  root.listeners.change[0]({
    target: {
      type: "textarea", value: "新备注", dataset: {field: "note"},
      closest: s => s === "[data-field]" ? {type: "textarea", value: "新备注", dataset: {field: "note"}} : null,
    },
  });
  assert.match(mainSlot.innerHTML, /class="ct-gates">/);
  assert.doesNotMatch(mainSlot.innerHTML, /ct-gates is-entering/);

  // 切到「消息测试」并打开确认弹窗：验证持久化 <dialog> 在多次重渲染间复用且不重调 showModal
  root.listeners.click[0]({target: {closest: s => s === "[data-view]" ? {dataset: {view: "tests"}} : null}});
  for (let i = 0; i < 6; i++) await flush();
  root.listeners.click[0]({target: {closest: s => s === "[data-test-run]" ? {dataset: {testRun: "care"}} : null}});

  const dialog = root.querySelector("[data-test-confirm-dialog]");
  assert.ok(dialog, "Live DOM 模式下应挂载持久化 [data-test-confirm-dialog]");
  assert.equal(dialog.open, true);
  assert.equal(dialog.showModalCalls, 1);
  assert.equal(dialog.closeCalls, 0);
  assert.match(dialog.innerHTML, /运行「/);

  // 弹窗开启期间触发重渲染（例如关闭提示横幅）：同一节点、不关闭、不重调 showModal
  root.listeners.click[0]({target: {closest: s => s === "[data-action]" ? {dataset: {action: "dismiss"}} : null}});
  assert.equal(root.querySelector("[data-test-confirm-dialog]"), dialog);
  assert.equal(dialog.open, true);
  assert.equal(dialog.showModalCalls, 1);
  assert.equal(dialog.closeCalls, 0);

  // 点击取消关闭弹窗
  root.listeners.click[0]({target: {closest: s => s === "[data-test-action]" ? {dataset: {testAction: "cancel"}} : null}});
  assert.equal(dialog.open, false);
  assert.equal(dialog.closeCalls, 1);
  win.CompanionContactPage.unmount();
});

test("审阅中心详情滚动保留：同条二次确认与取消保持 .lh-review-detail-body 位置，切条回顶且子项防压缩", async () => {
  const hubCss = read("learning-hub.css");
  const reviewJs = read("learning-review-page.js");
  assert.match(hubCss, /\.learning-review-workspace \.lh-review-detail-body>\*\{flex:0 0 auto\}/);

  const handlers = {};
  let detailBody = {scrollTop: 0};
  const bridge = {
    apiGet: async route => ({
      status: "ok",
      data: {
        items: route.endsWith("/progress") ? [
          {review_id: "g1", growth_title: "第一条", progress_note: "进展一", base_revision: 1, created_at: "2026-11-24T10:00:00Z", actions: ["accept", "reject"], view_token: "t1", evidence: ["依据一"]},
          {review_id: "g2", growth_title: "第二条", progress_note: "进展二", base_revision: 1, created_at: "2026-11-24T09:00:00Z", actions: ["accept", "reject"], view_token: "t2", evidence: ["依据二"]},
        ] : [],
        next_cursor: null,
      },
    }),
    apiPost: async () => ({status: "ok", data: {}}),
  };
  let html = "";
  const root = {
    isConnected: true,
    get innerHTML() { return html; },
    set innerHTML(val) {
      html = String(val ?? "");
      // 模拟浏览器重写 innerHTML 后生成全新 .lh-review-detail-body 节点（初始 scrollTop = 0）
      detailBody = {scrollTop: 0};
    },
    ownerDocument: {documentElement: {dataset: {}}, querySelector: () => null},
    addEventListener(type, fn) { handlers[type] = fn; },
    removeEventListener() {},
    querySelector(sel) {
      if (sel === ".lh-review-detail-body") return detailBody;
      return null;
    },
    querySelectorAll: () => [],
  };
  const win = {
    CompanionProduct: {bridgeNow: () => bridge, unwrap: v => v},
    lucide: {createIcons() {}},
    matchMedia: () => ({matches: false}),
    addEventListener() {}, removeEventListener() {},
  };
  const ctx = vm.createContext({window: win, setTimeout, clearTimeout, Date, Math, Number, Object, Array, String, Set, Map});
  vm.runInContext(reviewJs, ctx);
  const page = ctx.window.CompanionLearningReviewPage;
  page.mount(root, {sources: [{key: "r1", label: "角色 A", growthApi: `${PREFIX}t/r/growth`, replyApi: `${PREFIX}t/r/reply`}]});
  for (let i = 0; i < 40 && page.state?.loading; i++) await flush();

  // 滚动详情区至 260px，点击「确认申报」展开确认条：滚动位置保留
  detailBody.scrollTop = 260;
  handlers.click({target: {closest: s => s === "[data-review-stage]" ? {dataset: {reviewKey: "r1::growth::g1", reviewStage: "accept"}} : null}});
  assert.equal(detailBody.scrollTop, 260, "展开二次确认条后详情正文 scrollTop 必须保留");

  // 点击「取消」收起确认条：滚动位置仍保留
  handlers.click({target: {closest: s => s === "[data-review-confirm]" ? {dataset: {reviewConfirm: "no"}} : null}});
  assert.equal(detailBody.scrollTop, 260, "取消确认条后详情正文 scrollTop 必须保留");

  // 切换到另一条记录：详情正文自动回顶
  handlers.click({target: {closest: s => s === "[data-review-select]" ? {dataset: {reviewSelect: "r1::growth::g2"}} : null}});
  assert.equal(detailBody.scrollTop, 0, "切换到新记录时详情正文应回到顶部");
  page.unmount();
});
