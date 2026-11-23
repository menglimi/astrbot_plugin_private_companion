"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const PAGE_DIR = path.join(__dirname, "..", "pages", "\u966a\u4f34\u5c0f\u5c4b");

function loadEngineWindow() {
  const win = {
    addEventListener() {},
    removeEventListener() {},
    CompanionWorkbench: {
      escapeHTML: s => String(s ?? "").replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
      }[c])),
    },
  };
  const code = fs.readFileSync(path.join(PAGE_DIR, "search-engine.js"), "utf8");
  vm.runInNewContext(code, {
    window: win,
    globalThis: win,
    console,
    RegExp,
    Math,
    Date,
    JSON,
    Object,
    Array,
    Map,
    Set,
    Number,
    String,
    Boolean,
  });
  return win;
}

function makeFakeElement(attrs = {}) {
  const listeners = new Map();
  const el = {
    value: "",
    textContent: "",
    innerHTML: "",
    hidden: false,
    disabled: false,
    dataset: {},
    style: {},
    children: [],
    classList: {
      _set: new Set(),
      add(...cls) { cls.forEach(c => this._set.add(c)); },
      remove(...cls) { cls.forEach(c => this._set.delete(c)); },
      contains(c) { return this._set.has(c); },
      toggle(c, force) {
        if (force === undefined) {
          if (this._set.has(c)) { this._set.delete(c); return false; }
          this._set.add(c); return true;
        }
        if (force) this._set.add(c); else this._set.delete(c);
        return Boolean(force);
      },
    },
    setAttribute(k, v) { this[k] = v; },
    getAttribute(k) { return this[k] ?? null; },
    addEventListener(type, fn) { listeners.set(type, fn); },
    querySelector: () => null,
    querySelectorAll: () => [],
    closest: () => null,
    ...attrs,
  };
  return el;
}

test("CompanionSearchEngine: 5-layer fuzzy scoring covers synonyms, pinyin, initials and 1-char typos", () => {
  const win = loadEngineWindow();
  const engine = win.CompanionSearchEngine;
  const registry = win.CompanionSearchRegistry;
  assert.ok(engine && typeof engine.search === "function");
  assert.ok(registry && typeof registry.collectSettings === "function");

  const sampleItems = [
    ...registry.collectSettings(),
    {
      group: "proactive.contact",
      groupTitle: "主动联系",
      section: "quiet",
      sectionTitle: "免打扰",
      field: "quiet_hours_enabled",
      label: "免打扰与安静时段",
      help: "夜间或忙碌时段暂缓主动发消息与打扰。",
      aliases: ["安静时段", "防骚扰", "勿扰"],
    },
    {
      group: "relay",
      groupTitle: "递话",
      section: "relay",
      sectionTitle: "跨窗口递话",
      field: "relay_enabled",
      label: "允许跨窗口递话",
      help: "在授权的私聊与群聊窗口之间代为转告消息。",
      aliases: ["转告", "传话", "relay"],
    },
    {
      group: "poke",
      groupTitle: "互动",
      section: "poke",
      sectionTitle: "戳一戳",
      field: "poke_enabled",
      label: "启用戳一戳回应",
      help: "收到拍一拍或戳一戳时自动回应。",
      aliases: ["拍一拍", "poke"],
    },
  ];
  const prepared = engine.prepare(sampleItems, {
    getTitle: item => item.label,
    getDesc: item => item.help,
    getKey: item => item.field,
    getAliases: item => item.aliases || [],
    getGroup: item => `${item.groupTitle || ""} ${item.sectionTitle || ""}`.trim(),
  });

  /* 1. Unified name & legacy aliases -> model.provider_id */
  for (const query of ["陪伴通用模型", "共享启动模型", "插件启动模型", "全局默认模型", "插件任务模型 Provider"]) {
    const hits = engine.search(prepared, query);
    assert.ok(hits.length > 0, `Expected hits for ${query}`);
    assert.equal(hits[0].item.field, "model.provider_id", `Expected model.provider_id at #1 for "${query}", got ${hits[0].item.field}`);
  }

  /* 2. Fallback model & homophone typo */
  for (const query of ["全局备用模型", "回退模型", "fallback", "倍用模型", "beiyong"]) {
    const hits = engine.search(prepared, query);
    assert.ok(hits.length > 0, `Expected hits for ${query}`);
    assert.equal(hits[0].item.field, "model.fallback_provider_id", `Expected model.fallback_provider_id at #1 for "${query}", got ${hits[0].item.field}`);
  }

  /* 3. Pinyin initials & full pinyin */
  const pbHits = engine.search(prepared, "pbtymx");
  assert.ok(pbHits.length > 0);
  assert.equal(pbHits[0].item.field, "model.provider_id");
  assert.match(pbHits[0].reason, /拼音/);

  const fdrHits = engine.search(prepared, "fdr");
  assert.ok(fdrHits.some(h => h.item.field === "quiet_hours_enabled"), "Expected fdr to match 免打扰 via 防打扰 synonym/pinyin");

  /* 4. 1-char typo tolerance */
  const typoModel = engine.search(prepared, "陪伴统用模型");
  assert.ok(typoModel.length > 0);
  assert.equal(typoModel[0].item.field, "model.provider_id");

  const typoRelay = engine.search(prepared, "递画");
  assert.ok(typoRelay.some(h => h.item.field === "relay_enabled"), "Expected 递画 typo to match 递话");

  /* 5. Domain synonyms */
  assert.ok(engine.search(prepared, "画图").some(h => h.item.field === "image_generation_providers"));
  assert.ok(engine.search(prepared, "转告").some(h => h.item.field === "relay_enabled"));
  assert.ok(engine.search(prepared, "poke").some(h => h.item.field === "poke_enabled"));

  /* 6. Polyphone handling (角色 -> juese / jiaose) */
  const jsHits = engine.search(prepared, "juese");
  const jiaoseHits = engine.search(prepared, "jiaose");
  assert.ok(jsHits.length > 0, "Expected juese to match 角色 items");
  assert.ok(jiaoseHits.length > 0, "Expected jiaose to match 角色 items");

  /* 7. Multi-word AND matching must never drop the second keyword on synonym phrases */
  const qzoneSample = engine.prepare([
    { field: "qzone.like_enabled", label: "QQ 空间点赞", help: "自动给好友动态点赞。", groupTitle: "QQ 空间", aliases: ["qzone"] },
    { field: "qzone.comment_enabled", label: "QQ 空间评论", help: "自动评论或回复好友空间动态。", groupTitle: "QQ 空间", aliases: ["qzone"] },
    { field: "qzone.post_enabled", label: "QQ 空间发说说", help: "主动发布空间说说与生活动态。", groupTitle: "QQ 空间", aliases: ["qzone"] },
  ], {
    getTitle: x => x.label,
    getDesc: x => x.help,
    getKey: x => x.field,
    getAliases: x => x.aliases || [],
    getGroup: x => x.groupTitle || "",
  });
  for (const multiQ of ["qq空间 评论", "qq 空间 评论"]) {
    const res = engine.search(qzoneSample, multiQ);
    assert.equal(res.length, 1, `Multi-word query "${multiQ}" must only match QQ 空间评论, got ${JSON.stringify(res.map(r => r.item.field))}`);
    assert.equal(res[0].item.field, "qzone.comment_enabled");
  }
  assert.equal(engine.search(qzoneSample, "qq 空间").length, 3, 'Exact multi-word phrase "qq 空间" matches all 3 QZone entries');

  /* 8. No false positives on unrelated gibberish */
  assert.equal(engine.search(prepared, "不存在的火星配置xyz").length, 0);
});

test("CompanionSearchEngine.highlight escapes HTML/XSS and regex metacharacters cleanly", () => {
  const win = loadEngineWindow();
  const { highlight } = win.CompanionSearchEngine;

  const xss = highlight('<img src=x onerror="alert(1)">陪伴通用模型', ["陪伴通用", "<img"]);
  assert.doesNotMatch(xss, /<img/);
  assert.match(xss, /&lt;mark&gt;|&lt;img/);
  assert.match(xss, /<mark>陪伴通用<\/mark>/);

  const meta = highlight("price (USD) + a.b*c?", ["(USD)", "a.b*c?"]);
  assert.match(meta, /<mark>\(USD\)<\/mark>/);
  assert.match(meta, /<mark>a\.b\*c\?<\/mark>/);

  const cased = highlight("ComfyUI 工作流", ["comfyui"]);
  assert.equal(cased, "<mark>ComfyUI</mark> 工作流");
});

test("CompanionSearchEngine performance stays well under latency budget on 1500 entries", () => {
  const win = loadEngineWindow();
  const engine = win.CompanionSearchEngine;
  const synthetic = [];
  for (let i = 0; i < 1500; i += 1) {
    synthetic.push({
      id: `item_${i}`,
      label: i === 777 ? "陪伴通用模型（全局默认）" : `功能配置项 ${i} · 对话表达与记忆整理开关`,
      help: `控制第 ${i} 个陪伴行为的开启状态、频率与回退策略`,
      field: `section_${i % 20}.field_${i}`,
      aliases: i === 777 ? ["共享启动模型", "插件启动模型"] : [`别名${i}`],
    });
  }
  const prepared = engine.prepare(synthetic, {
    getTitle: x => x.label,
    getDesc: x => x.help,
    getKey: x => x.field,
    getAliases: x => x.aliases,
  });

  const queries = ["陪伴通用模型", "pbtymx", "共享启动模型", "陪伴统用模型", "记忆整理"];
  const start = performance.now();
  for (const q of queries) {
    const res = engine.search(prepared, q);
    assert.ok(res.length > 0, `Expected hits for ${q}`);
  }
  const avgMs = (performance.now() - start) / queries.length;
  assert.ok(avgMs < 25, `Average search latency over 1500 items should be < 25ms, got ${avgMs.toFixed(2)}ms`);
});

function createAllFeaturesHarness(extraSections = {}) {
  const engineWin = loadEngineWindow();
  const appSrc = fs.readFileSync(path.join(PAGE_DIR, "app.js"), "utf8");
  const startIdx = appSrc.indexOf("const allFeatureDomainOrder=");
  const endIdx = appSrc.indexOf("/* 分发表只保留");
  assert.ok(startIdx > 0 && endIdx > startIdx, "Failed to locate all-features slice in app.js");
  const slice = appSrc.slice(startIdx, endIdx);

  const openedGlobalQueries = [];
  let paintedHtml = "";
  const sandbox = {
    window: {
      CompanionSearchEngine: engineWin.CompanionSearchEngine,
      CompanionSearchRegistry: engineWin.CompanionSearchRegistry,
      CompanionConfigEditors: {
        spec: {
          relay: {
            title: "跨窗口与跨群递话",
            labels: { enabled: "允许跨窗口递话", confirm_before_send: "递话前先确认" },
            helps: { enabled: "在私聊与群聊间代为转告消息。", confirm_before_send: "递话前再次确认收件人。" },
          },
          "device.phone": {
            title: "手机使用",
            labels: { adb_enabled: "启用手机 ADB 控制" },
            helps: { adb_enabled: "授权角色通过 ADB 操作已连接手机。" },
          },
          busy_gate: {
            title: "忙碌顺延",
            labels: { enabled: "启用免打扰顺延" },
            helps: { enabled: "忙碌或安静时段暂缓打扰。" },
          },
        },
      },
      CompanionProduct: {
        scopeKey: () => "role-1",
        role: () => ({ config_api: "/astrbot_plugin_private_companion/host/r1/config" }),
        target: () => ({ scope_kind: "private" }),
        request: async () => ({ sections: {} }),
      },
    },
    document: {
      querySelector: () => null,
      querySelectorAll: () => [],
    },
    product: {
      role: () => ({ config_api: "/astrbot_plugin_private_companion/host/r1/config" }),
      target: () => ({ scope_kind: "private" }),
    },
    manageTab: "all",
    escapeHTML: engineWin.CompanionWorkbench.escapeHTML,
    button: (label, attrs = "", cls = "") => `<button type="button" class="button ${cls}" ${attrs}>${label}</button>`,
    I: () => "",
    icons: () => {},
    consoleLoading: s => s,
    pageNeedsTarget: () => "",
    paintPanel: () => { paintedHtml = sandbox.__af.renderAllFeatureBoard(); },
    openSearch: q => { openedGlobalQueries.push(q); },
    openSearchSetting: async () => {},
    console,
    Date,
    Object,
    Array,
    Map,
    Set,
    JSON,
    Math,
    RegExp,
  };
  vm.createContext(sandbox);
  vm.runInContext(
    `${slice}
     globalThis.__af = {
       allFeaturesState,
       allFeatureBoardGroups,
       renderAllFeatureBoard,
       renderAllFeatureSectionsMarkup,
       filterAllFeatures,
       extractDedicatedAllFeatureRows,
     };`,
    sandbox
  );

  const baseSections = {
    relay: {
      section: "relay",
      fields: { enabled: { kind: "boolean" }, confirm_before_send: { kind: "boolean" } },
      settings: { enabled: true, confirm_before_send: false },
    },
    "device.phone": {
      section: "device.phone",
      fields: { adb_enabled: { kind: "boolean" } },
      settings: { adb_enabled: true },
    },
    busy_gate: {
      section: "busy_gate",
      fields: { enabled: { kind: "boolean" } },
      settings: { enabled: false },
    },
    ...extraSections,
  };
  sandbox.__af.allFeaturesState.loaded = true;
  sandbox.__af.allFeaturesState.sections = baseSections;
  sandbox.__af.allFeaturesState.draft = {
    relay: { enabled: true, confirm_before_send: false },
    "device.phone": { adb_enabled: true },
    busy_gate: { enabled: false },
  };
  return { sandbox, af: sandbox.__af, openedGlobalQueries, getPaintedHtml: () => paintedHtml };
}

test("All-features page keeps enabled + disabled === total === domainSum and eliminates orphan '其他功能'", () => {
  const { af } = createAllFeaturesHarness();
  const groups = af.allFeatureBoardGroups();
  const html = af.renderAllFeatureBoard();

  /* 1. No orphan '其他功能' bucket; device.phone is grouped under '理解、知识与感知' */
  assert.equal(groups.some(g => g.domain === "其他功能"), false, "device.phone must not fall into 其他功能");
  const perceptionGroup = groups.find(g => g.domain === "理解、知识与感知");
  assert.ok(perceptionGroup && perceptionGroup.rows.some(r => r.key === "device.phone.adb_enabled"));

  /* 2. relay.relay-settings is a header link button, NOT a switch row counted in totals */
  const relayGroup = groups.find(g => g.domain === "跨窗口与跨群递话");
  assert.ok(relayGroup);
  assert.equal(relayGroup.rows.length, 2, "Only the 2 boolean switches belong in relay rows");
  assert.equal(relayGroup.headerLinks.length, 1);
  assert.equal(relayGroup.headerLinks[0].label, "递话目标与规则");
  assert.match(html, /all-feature-header-link[^>]*><span>递话目标与规则<\/span>/);
  assert.doesNotMatch(html, /data-all-feature-key="relay\.relay-settings"/);

  /* 3. Strict count invariance: enabled + disabled === total === sum(group.rows.length) */
  const totalFromGroups = groups.reduce((acc, g) => acc + g.rows.length, 0);
  assert.equal(totalFromGroups, 4);
  assert.match(html, /<span>已开启<\/span><b>2 \/ 4<\/b>/);
  assert.match(html, /<span>全部领域<\/span><small>4<\/small>/);
  assert.match(html, /data-all-feature-status="all"[^>]*><span>全部状态<\/span><small>4<\/small>/);
  assert.match(html, /data-all-feature-status="enabled"[^>]*><span>已开启<\/span><small>2<\/small>/);
  assert.match(html, /data-all-feature-status="disabled"[^>]*><span>已关闭<\/span><small>2<\/small>/);

  /* 4. Native extensions strip renders 4 clean cards without inflating switch total */
  for (const extDetail of ["cap-game", "cap-live", "cap-reality", "cap-together"]) {
    assert.match(html, new RegExp(`data-detail="${extDetail}"`));
  }

  /* 5. HTML5 semantics: filter toolbar uses <div class="all-functions-search"><label for="allFeatureFilterInput"> (never wraps button inside <label>) */
  assert.match(html, /<div class="all-functions-search"><label for="allFeatureFilterInput">筛选功能<\/label>/);
  assert.match(html, /<input id="allFeatureFilterInput"[^>]*data-all-feature-filter/);
  assert.doesNotMatch(html, /<label class="all-functions-search">/);
});

test("extractDedicatedAllFeatureRows matches real backend shapes for reply, state, perception, memory, host-options and self-review", () => {
  const { sandbox, af } = createAllFeaturesHarness();
  sandbox.window.CompanionReplyPage = {
    searchEntries: () => [
      { group: "conversation.presentation", section: "segment", sectionTitle: "分段回复", field: "enabled", label: "启用私聊分段" },
      { group: "conversation.presentation", section: "media_permissions", sectionTitle: "媒体权限", field: "allow_image", label: "允许理解图片" },
    ],
  };
  sandbox.window.CompanionStateMaintenance = {
    searchEntries: () => [
      { group: "role.state", section: "state", sectionTitle: "状态维护", field: "enabled", label: "启用角色状态维护" },
    ],
  };
  sandbox.window.CompanionNativePerceptionPage = {
    searchEntries: () => [
      { group: "perception.native", section: "model", sectionTitle: "感知模型", field: "model_route_enabled", label: "标明模型路由" },
      { group: "perception.native", section: "news", sectionTitle: "外部资讯", field: "news_share_enabled", label: "允许分享外部资讯" },
    ],
  };
  sandbox.window.CompanionMemorySettingsSection = {
    searchEntries: () => [
      { group: "memory", section: "reflection", sectionTitle: "整理策略", field: "reflection_enabled", label: "启用记忆反思整理" },
      { group: "memory", section: "core", sectionTitle: "基础记忆", field: "enabled", label: "启用长期记忆" },
    ],
  };
  sandbox.window.CompanionHostOptionsPage = {
    searchEntries: () => [
      { group: "voice_reply", groupTitle: "全局语音默认值", section: "voice_reply", field: "enabled", label: "启用语音朗读总开关" },
      { group: "poke", groupTitle: "戳一戳", section: "poke", field: "enabled", label: "启用戳一戳回应" },
      { group: "forward_intake", groupTitle: "合并转发", section: "forward_intake", field: "enabled", label: "启用合并转发读取" },
    ],
  };

  const rows = af.extractDedicatedAllFeatureRows({
    /* Real reply_api returns {settings, configuration_token} without a fields map */
    replyPayload: { settings: { enabled: true, allow_image: false, streaming_mode: "auto" }, configuration_token: "tok" },
    /* Real state_api returns {state, settings, sleep_schedule} */
    statePayload: { settings: { enabled: true } },
    /* Real perception_native_api returns {environment, model, news} */
    perceptionPayload: { model: { settings: { model_route_enabled: true } }, news: { settings: { news_share_enabled: false } } },
    /* Real memory_api/settings returns {fields, values} (NOT settings!) */
    memoryPayload: {
      fields: { reflection_enabled: { kind: "boolean" }, enabled: { kind: "boolean" }, context_items: { kind: "integer" } },
      values: { reflection_enabled: true, enabled: true, context_items: 8 },
    },
    /* Real host-options returns {voice_reply, poke, forward_intake} (NOT voice!) */
    hostOptionsPayload: {
      voice_reply: { enabled: true },
      poke: { enabled: false },
      forward_intake: { enabled: true },
    },
    /* Real self-review/settings returns {enabled} */
    selfReviewPayload: { enabled: true },
    catalogPayload: {
      plugin_config: [
        { path: "performance.enabled", section: "performance", section_title: "性能模式", label: "启用性能模式", type: "bool", current_value: false },
      ],
    },
  });

  const byKey = new Map(rows.map(r => [r.key, r]));
  assert.equal(byKey.get("reply.enabled")?.value, true, "reply_api boolean must be extracted without requiring fields map");
  assert.equal(byKey.get("reply.allow_image")?.value, false);
  assert.equal(byKey.get("role_state.enabled")?.value, true);
  assert.equal(byKey.get("perception_native.model_route_enabled")?.value, true);
  assert.equal(byKey.get("perception_native.news_share_enabled")?.value, false);
  assert.equal(byKey.get("memory_settings.reflection_enabled")?.value, true, "memory_api/settings must read from values key");
  assert.equal(byKey.get("memory_settings.enabled")?.value, true);
  assert.equal(byKey.get("host_options.voice_reply.enabled")?.value, true, "host-options must read from voice_reply key");
  assert.equal(byKey.get("host_options.poke.enabled")?.value, false);
  assert.equal(byKey.get("host_options.forward_intake.enabled")?.value, true);
  assert.equal(byKey.get("self_review.enabled")?.value, true);
  assert.equal(byKey.get("plugin_global.performance.enabled")?.value, false);
  assert.equal(rows.length, 12);
});

test("CSS polish contracts: hides native search cancel buttons, enlarges 3-tier search modal and respects data-motion", () => {
  const stylesCss = fs.readFileSync(path.join(PAGE_DIR, "styles.css"), "utf8");
  const workspaceCss = fs.readFileSync(path.join(PAGE_DIR, "workspace-theme.css"), "utf8");
  assert.match(stylesCss, /#globalSearch::-webkit-search-cancel-button/);
  assert.match(stylesCss, /\[data-all-feature-filter\]::-webkit-search-cancel-button/);
  assert.match(stylesCss, /\.search-dialog\{[^}]*width:min\(860px,90vw\)/);
  assert.match(stylesCss, /#searchDialog\.search-dialog\[open\]\{display:flex;flex-direction:column;[^}]*animation:search-dialog-in \.22s cubic-bezier\(\.22,1,\.36,1\)/);
  assert.match(stylesCss, /#searchDialog\.search-dialog\.is-closing\{pointer-events:none;animation:search-dialog-out \.16s/);
  assert.match(stylesCss, /#searchDialog\.search-dialog\[open\]::backdrop\{[^}]*animation:search-backdrop-in/);
  assert.match(stylesCss, /#searchDialog\.search-dialog\.is-closing::backdrop\{pointer-events:none;animation:search-backdrop-out/);
  assert.match(stylesCss, /@media\(prefers-reduced-motion:reduce\)\{[^}]*#searchDialog\.search-dialog\[open\]/);
  assert.match(stylesCss, /\.search-results\{[^}]*flex:1 1 auto;min-height:360px/);
  assert.match(stylesCss, /\.search-result-help\{[^}]*-webkit-line-clamp:2/);
  assert.match(stylesCss, /\.search-result-icon\{/);
  assert.match(stylesCss, /\.search-result-arrow\{/);
  assert.match(workspaceCss, /\[data-motion="off"\].*\.is-search-target/);
  assert.match(workspaceCss, /\[data-motion="low"\].*\.is-search-target/);
});

test("Global search dialog bidirectional open/close animation handles rapid toggle, focus restore and reduced motion", () => {
  const appSrc = fs.readFileSync(path.join(PAGE_DIR, "app.js"), "utf8");
  const startIdx = appSrc.indexOf("let searchDialogAnimToken=0;");
  const endIdx = appSrc.indexOf("document.addEventListener('click',event=>{");
  assert.ok(startIdx > 0 && endIdx > startIdx, "Could not locate openSearch/closeSearch slice in app.js");
  const slice = appSrc.slice(startIdx, endIdx);

  let reducedPref = false;
  let focusCalls = [];
  const triggerBtn = {
    isConnected: true,
    focus(opts) { focusCalls.push({ el: "trigger", opts }); sandbox.document.activeElement = triggerBtn; },
  };
  const searchBtn = {
    isConnected: true,
    focus(opts) { focusCalls.push({ el: "searchButton", opts }); sandbox.document.activeElement = searchBtn; },
  };
  const inputEl = {
    value: "",
    focus() { focusCalls.push({ el: "globalSearch" }); sandbox.document.activeElement = inputEl; },
    setSelectionRange() {},
  };
  const animListeners = [];
  const classSet = new Set();
  const dlg = {
    open: false,
    offsetWidth: 860,
    classList: {
      add: c => classSet.add(c),
      remove: c => classSet.delete(c),
      contains: c => classSet.has(c),
    },
    contains: node => node === inputEl,
    showModal() { this.open = true; },
    close() { this.open = false; },
    addEventListener(type, fn) { if (type === "animationend") animListeners.push(fn); },
    removeEventListener(type, fn) {
      const idx = animListeners.indexOf(fn);
      if (idx >= 0) animListeners.splice(idx, 1);
    },
  };

  const sandbox = {
    state: { reduceMotion: false },
    searchScope: "all",
    searchRenderTimer: 0,
    $: sel => ({ "#searchDialog": dlg, "#globalSearch": inputEl, "#searchButton": searchBtn, "#detailDialog": { open: false } }[sel] || null),
    document: { activeElement: triggerBtn, body: { classList: { contains: () => false } }, documentElement: { dataset: {} } },
    window: { matchMedia: () => ({ matches: reducedPref }) },
    closeDetail() {},
    invalidateSearchCache() {},
    renderSearch() {},
    ensureSearchCatalog() { return Promise.resolve(null); },
    setTimeout,
    clearTimeout,
  };
  vm.createContext(sandbox);
  vm.runInContext(`${slice}\nglobalThis.__sd = { openSearch, closeSearch };`, sandbox);

  /* 1. Open records trigger focus and opens modal */
  sandbox.__sd.openSearch("通用");
  assert.equal(dlg.open, true);
  assert.equal(inputEl.value, "通用");
  assert.equal(classSet.has("is-closing"), false);
  assert.equal(focusCalls.at(-1).el, "globalSearch");

  /* 2. Close adds is-closing and waits for animationend before closing + restoring focus */
  sandbox.__sd.closeSearch({ restoreFocus: true });
  assert.equal(dlg.open, true, "Dialog stays open during 160ms exit animation");
  assert.equal(classSet.has("is-closing"), true);

  /* 3. Rapid reopen mid-close cancels pending exit with zero race */
  const staleListener = animListeners[0];
  sandbox.__sd.openSearch("备用");
  assert.equal(dlg.open, true);
  assert.equal(classSet.has("is-closing"), false);
  assert.equal(inputEl.value, "备用");
  staleListener?.({ target: dlg });
  assert.equal(dlg.open, true, "Stale animationend from cancelled close must not close reopened dialog");

  /* 4. Normal close finishes on animationend and restores focus to original trigger */
  animListeners.length = 0;
  sandbox.__sd.closeSearch({ restoreFocus: true });
  assert.equal(classSet.has("is-closing"), true);
  animListeners[0]?.({ target: dlg });
  assert.equal(dlg.open, false);
  assert.equal(classSet.has("is-closing"), false);
  assert.equal(focusCalls.at(-1).el, "trigger");

  /* 5. Reduced motion closes immediately and synchronously restores focus */
  reducedPref = true;
  sandbox.document.activeElement = triggerBtn;
  sandbox.__sd.openSearch();
  assert.equal(dlg.open, true);
  sandbox.__sd.closeSearch({ restoreFocus: true });
  assert.equal(dlg.open, false, "Reduced motion closes synchronously without exit delay");
  assert.equal(classSet.has("is-closing"), false);
  assert.equal(focusCalls.at(-1).el, "trigger");

  /* 6. Result jump closeSearch({restoreFocus:false}) does not hijack focus back to trigger */
  focusCalls = [];
  sandbox.__sd.openSearch();
  sandbox.__sd.closeSearch({ restoreFocus: false });
  assert.equal(dlg.open, false);
  assert.equal(focusCalls.some(c => c.el === "trigger" || c.el === "searchButton"), false);
});

test("Direct alias substring hit keeps alias reason instead of misleading typo badge", () => {
  const win = loadEngineWindow();
  const engine = win.CompanionSearchEngine;
  const prepared = engine.prepare([
    { label: "模型与连接", help: "功能任务路由", key: "models", aliases: ["陪伴通用模型", "模型"] },
  ], {
    getTitle: x => x.label,
    getDesc: x => x.help,
    getKey: x => x.key,
    getAliases: x => x.aliases,
  });
  const hits = engine.search(prepared, "通用");
  assert.equal(hits.length, 1);
  assert.equal(hits[0].reason, "别名：陪伴通用模型");
});

test("All-features fuzzy filter supports pinyin/synonyms, highlights matches, persists query and handles 0-hit handoff", () => {
  const { sandbox, af } = createAllFeaturesHarness();

  const hostEl = makeFakeElement();
  const countEl = makeFakeElement();
  const clearBtn = makeFakeElement({ hidden: true });

  sandbox.document.querySelector = sel => ({
    "#allFeatureResultsHost": hostEl,
    "[data-all-feature-count]": countEl,
    "[data-all-feature-clear-filter]": clearBtn,
  }[sel] || null);

  /* Synonym search ("转告" -> "允许跨窗口递话") */
  af.filterAllFeatures("转告");
  assert.equal(af.allFeaturesState.query, "转告");
  assert.equal(countEl.textContent, "2 项");
  assert.equal(clearBtn.hidden, false);
  assert.match(hostEl.innerHTML, /data-all-feature-key="relay\.enabled"/);
  assert.doesNotMatch(hostEl.innerHTML, /data-all-feature-key="busy_gate\.enabled"/);
  assert.match(hostEl.innerHTML, /<mark>转告<\/mark>|<mark>递话<\/mark>/);

  /* Pinyin initials search ("fdr" -> "免打扰") */
  af.filterAllFeatures("fdr");
  assert.equal(countEl.textContent, "1 项");
  assert.match(hostEl.innerHTML, /data-all-feature-key="busy_gate\.enabled"/);
  assert.doesNotMatch(hostEl.innerHTML, /data-all-feature-key="relay\.enabled"/);

  /* 0-hit search shows empty state with clear + global-search handoff */
  af.filterAllFeatures("陪伴通用模型");
  assert.equal(countEl.textContent, "0 项");
  assert.match(hostEl.innerHTML, /data-all-feature-clear-filter/);
  assert.match(hostEl.innerHTML, /data-all-feature-global-search/);
  assert.match(hostEl.innerHTML, /带此词搜全站配置/);

  /* Clearing restores exact total count (never empty string) and hides clear button */
  af.filterAllFeatures("");
  assert.equal(af.allFeaturesState.query, "");
  assert.equal(countEl.textContent, "4 项");
  assert.equal(clearBtn.hidden, true);
  assert.match(hostEl.innerHTML, /data-all-feature-key="relay\.enabled"/);
  assert.match(hostEl.innerHTML, /data-all-feature-key="device\.phone\.adb_enabled"/);
  assert.match(hostEl.innerHTML, /data-all-feature-key="busy_gate\.enabled"/);
});

test("Setup guide keeps 陪伴通用模型 editable after initialization and saves primary + fallback", async () => {
  const setupSrc = fs.readFileSync(path.join(PAGE_DIR, "setup-guide.js"), "utf8");
  const apiCalls = [];
  const listeners = new Map();
  let rootHtml = "";
  const rootEl = {
    innerHTML: "",
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener(type, fn) { listeners.set(type, fn); },
    removeEventListener(type) { listeners.delete(type); },
  };
  Object.defineProperty(rootEl, "innerHTML", {
    get() { return rootHtml; },
    set(val) { rootHtml = String(val); },
  });

  const setupStatus = {
    ready: true,
    prepared: true,
    step: "ready",
    connected: true,
    model: {
      provider_id: "p1",
      fallback_provider_id: "p2",
      providers: [
        { id: "p1", name: "Primary LLM" },
        { id: "p2", name: "Backup LLM" },
        { id: "p3", name: "Third LLM" },
      ],
    },
    proactive: { ready: true },
    binding: { bound: true },
    persona: { ready: true },
    steps: [],
  };

  const win = {
    document: {
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {},
      removeEventListener() {},
    },
    location: { hash: "", pathname: "/", search: "" },
    addEventListener() {},
    removeEventListener() {},
    CompanionProduct: {
      role: () => ({ model_api: "/astrbot_plugin_private_companion/host/r1/models" }),
      targets: () => [],
      accounts: () => [],
      personas: () => [],
      scopeKey: () => "r1",
      hosted: () => true,
      unwrap: response => response,
      relative: address => address.replace(/^\/astrbot_plugin_private_companion\//, ""),
      refreshDirectory: async () => {},
      request: async (method, endpoint, body) => {
        apiCalls.push({ method, endpoint, body });
        if (endpoint === "setup/status") return setupStatus;
        if (endpoint === "accounts/model-provider") {
          setupStatus.model.provider_id = body.provider_id;
          setupStatus.model.fallback_provider_id = body.fallback_provider_id ?? setupStatus.model.fallback_provider_id;
          return { model: setupStatus.model, prepared: true };
        }
        if (endpoint.endsWith("/models")) return { providers: [], tasks: [] };
        return {};
      },
      refresh: async () => {},
    },
    CompanionWorkbench: {
      escapeHTML: s => String(s ?? "").replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
      }[c])),
    },
  };

  vm.runInNewContext(setupSrc, {
    window: win,
    document: win.document,
    globalThis: win,
    console,
    setTimeout,
    clearTimeout,
    URLSearchParams,
    Date,
    Math,
    JSON,
    Object,
    Array,
    Map,
    Set,
    Promise,
  });

  win.CompanionSetup.mount(rootEl);
  win.CompanionSetup.openQuickBlock("runtime");
  await new Promise(resolve => setTimeout(resolve, 10));

  assert.match(rootHtml, /name="setup-provider"/, "Initialized setup guide must still render editable primary provider select");
  assert.match(rootHtml, /name="setup-fallback-provider"/, "Initialized setup guide must render optional fallback provider select");
  assert.match(rootHtml, /陪伴通用模型（全局默认）/);
  assert.doesNotMatch(rootHtml, /只读|不可在此修改/, "Must never lock primary model as read-only after initialization");

  /* Simulate user changing fallback to p3 and saving via accounts/model-provider */
  listeners.get("change")?.({ target: { name: "setup-fallback-provider", value: "p3", matches: () => false } });
  listeners.get("click")?.({
    target: { closest: sel => (sel === "[data-setup-action]" ? { dataset: { setupAction: "save-model" } } : null) },
  });
  await new Promise(resolve => setTimeout(resolve, 10));

  const saveCall = apiCalls.find(c => c.method === "POST" && c.endpoint === "accounts/model-provider");
  assert.ok(saveCall, "Saving initialized default model must POST accounts/model-provider");
  assert.equal(saveCall.body.provider_id, "p1");
  assert.equal(saveCall.body.fallback_provider_id, "p3");
});
