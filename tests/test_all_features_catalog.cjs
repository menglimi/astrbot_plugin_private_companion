/* 「全部功能」目录重构与全站中文化回归契约测试。
 *
 * 覆盖：
 *   1. 彻底移除 6.7.0 死表与裸英文兜底（row.help||row.field）
 *   2. 复合键索引（sectionName.field）零碰撞保留全部 10 个 enabled 与 2 个 group_enabled
 *   3. 按 sectionName 而非 group_id 查表，彻底消除三遍英文
 *   4. 多目标并行加载角色级 + 安装级分区、作用域角标与按分区 endpoint 分流保存
 *   5. 详情弹窗滚定位 [data-wb-field] 与完整错误映射
 *   6. 全局搜索 configSearchLocations / pageLabels、作品列表、防骚扰、配置导入导出、模型/维护任务名零裸英文泄漏
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const PAGE_DIR = path.join(__dirname, "..", "pages", "陪伴小屋");
const readPage = name => fs.readFileSync(path.join(PAGE_DIR, name), "utf8");

const WORKBENCH_SRC = readPage("workbench-page.js");
const CONFIG_EDITOR_SRC = readPage("config-editor.js");
const APP_SRC = readPage("app.js");
const DECISION_SRC = readPage("decision-page.js");
const CONFIG_PORT_SRC = readPage("config-port-page.js");
const MODELS_SRC = readPage("models-page.js");
const MAINTENANCE_SRC = readPage("maintenance-page.js");

const HAS_CJK = /[\u4e00-\u9fff]/;

function loadConfigSandbox() {
  const window = {
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {},
  };
  const document = {
    documentElement: {dataset: {}},
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
  };
  const storage = {getItem: () => null, setItem() {}, removeItem() {}};
  const ctx = vm.createContext({
    window,
    document,
    sessionStorage: storage,
    localStorage: storage,
    Intl,
    Date,
    Object,
    Array,
    Number,
    String,
    JSON,
    Math,
    URLSearchParams,
    setTimeout,
    clearTimeout,
  });
  vm.runInContext(WORKBENCH_SRC, ctx, {filename: "workbench-page.js"});
  vm.runInContext(CONFIG_EDITOR_SRC, ctx, {filename: "config-editor.js"});
  return ctx;
}

function loadAllFeaturesSandbox(options = {}) {
  const ctx = loadConfigSandbox();
  const requests = [];
  const scrolled = [];
  const dialogMounts = [];
  const start = APP_SRC.indexOf("const allFeatureDomainOrder=");
  const end = APP_SRC.indexOf("/* 分发表只保留");
  assert.ok(start > 0 && end > start, "找不到 app.js 全部功能代码块");

  ctx.window.CompanionProduct = {
    scopeKey: () => options.scopeKey || "role-a",
    role: () => ({config_api: options.configApi ?? "/astrbot_plugin_private_companion/host/targets/t1/roles/r1/config"}),
    errorLabel: code => ({dashboard_identity_required: "请先登录管理界面"}[code] || "当前设置未能确认，请刷新并核对账号、人格与模型。"),
    request: async (method, route, body) => {
      requests.push({method, route, body: body ? JSON.parse(JSON.stringify(body)) : undefined});
      if (typeof options.onRequest === "function") return options.onRequest(method, route, body);
      return {sections: {}};
    },
  };
  ctx.product = ctx.window.CompanionProduct;
  ctx.manageTab = "all";
  ctx.paintPanel = () => {};
  ctx.configEditorMounts = [];
  ctx.allFeatureDialogMount = null;
  ctx.configEditors = (id, kinds, title) => `<section id="${id}" data-kinds="${kinds.join(",")}">${title}</section>`;
  ctx.openDetailDialog = () => true;
  ctx.mountConfigEditor = (id, kinds, views) => {
    const selectedSections = [];
    const mount = {id, kinds, views, selectedSections, selectSection(secId) { selectedSections.push(secId); }, unmount() {}};
    dialogMounts.push(mount);
    return mount;
  };
  ctx.requestAnimationFrame = cb => { cb(0); return 0; };
  ctx.CSS = {escape: value => String(value)};
  ctx.document.querySelector = selector => {
    if (options.detailField && selector.includes(`[data-wb-field="${options.detailField}"]`)) {
      return {scrollIntoView(arg) { scrolled.push({selector, arg}); }};
    }
    return null;
  };
  ctx.I = name => `<i data-lucide="${name}"></i>`;
  ctx.escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
  ctx.normalizeSearchText = value => String(value ?? "").normalize("NFKC").toLowerCase();
  ctx.button = (text, attrs, kind) => `<button class="button ${kind}" ${attrs}>${text}</button>`;

  vm.runInContext(APP_SRC.slice(start, end) + `
window.__allFeaturesApi = {
  state: allFeaturesState,
  domains: allFeatureDomainOrder,
  domainOf: allFeatureDomainOf,
  domainSections: allFeatureDomainSections,
  index: allFeatureIndex,
  rows: allFeatureRows,
  board: allFeatureBoardGroups,
  render: renderAllFeatureBoard,
  load: loadAllFeatures,
  save: saveAllFeatures,
  openDetail: openAllFeatureDetail,
  errorText: allFeatureErrorText,
  fieldSpec: allFeatureFieldSpec,
};`, ctx, {filename: "app-all-features.js"});
  return {ctx, api: ctx.window.__allFeaturesApi, requests, scrolled, dialogMounts};
}

test("app.js 已彻底移除 6.7.0 死表并禁止裸英文简介兜底", () => {
  assert.doesNotMatch(APP_SRC, /\bALL_FEATURE_GROUPS\b/);
  assert.doesNotMatch(APP_SRC, /\bALL_FEATURE_META\b/);
  assert.doesNotMatch(APP_SRC, /row\.help\|\|row\.field/);
});

test("复合键索引保留全部 10 个 enabled 与 2 个 group_enabled 且零三遍英文", () => {
  const {api} = loadAllFeaturesSandbox();
  const sections = {
    "content.authoring": {group_id: "content.authoring", scope: "role", edit_token: "t1",
      settings: {enabled: true, cover_enabled: false}, fields: {enabled: {kind: "boolean"}, cover_enabled: {kind: "boolean"}}},
    web_understanding: {group_id: "conversation.web", scope: "window", edit_token: "t2",
      settings: {enabled: true}, fields: {enabled: {kind: "boolean"}}},
    relay: {group_id: "conversation.relay", scope: "window", edit_token: "t3",
      settings: {enabled: true, member_relay_enabled: true, prefer_relationship_names: true, require_confirm: true},
      fields: {enabled: {kind: "boolean"}, member_relay_enabled: {kind: "boolean"}, prefer_relationship_names: {kind: "boolean"}, require_confirm: {kind: "boolean"}}},
    qzone: {group_id: "qzone", scope: "role", edit_token: "t4",
      settings: {enabled: true, like_enabled: true, comment_enabled: true, publish_enabled: true},
      fields: {enabled: {kind: "boolean"}, like_enabled: {kind: "boolean"}, comment_enabled: {kind: "boolean"}, publish_enabled: {kind: "boolean"}}},
    "role.growth.skills": {group_id: "role.growth.skills", scope: "role", edit_token: "t5",
      settings: {enabled: true}, fields: {enabled: {kind: "boolean"}}},
    life: {group_id: "role.life", scope: "window", edit_token: "t6",
      settings: {enabled: true, sleep_enabled: true, diary_enabled: true},
      fields: {enabled: {kind: "boolean"}, sleep_enabled: {kind: "boolean", write_layer: "role"}, diary_enabled: {kind: "boolean", write_layer: "window"}}},
    library: {group_id: "library", scope: "role", edit_token: "t7",
      settings: {enabled: false}, fields: {enabled: {kind: "boolean"}}},
    wardrobe: {group_id: "wardrobe", scope: "role", edit_token: "t8",
      settings: {enabled: false}, fields: {enabled: {kind: "boolean"}}},
    "photo.reference": {group_id: "photo.reference", scope: "role", edit_token: "t9",
      settings: {enabled: true, group_enabled: false}, fields: {enabled: {kind: "boolean"}, group_enabled: {kind: "boolean"}}},
    "image.output": {group_id: "conversation.image_output", scope: "role", edit_token: "t10",
      settings: {allow_user_requests: true, group_enabled: true}, fields: {allow_user_requests: {kind: "boolean"}, group_enabled: {kind: "boolean"}}},
    "proactive.contact": {group_id: "proactive.contact", scope: "installation", edit_token: "t11",
      settings: {enabled: true, "affection.enabled": true}, fields: {enabled: {kind: "boolean"}, "affection.enabled": {kind: "boolean"}}},
    reaction: {group_id: "expression.reaction", scope: "installation", edit_token: "t12",
      settings: {low_latency_enabled: true, embedding_backfill_enabled: true}, fields: {low_latency_enabled: {kind: "boolean"}, embedding_backfill_enabled: {kind: "boolean"}}},
    budget: {group_id: "model.budget", scope: "installation", edit_token: "t13",
      settings: {enable_daily_token_soft_limit: false}, fields: {enable_daily_token_soft_limit: {kind: "boolean"}}},
    private_access_installation: {group_id: "private_access", scope: "installation", edit_token: "t14",
      settings: {stranger_notice_enabled: true}, fields: {stranger_notice_enabled: {kind: "boolean"}}},
  };
  api.state.sections = sections;
  api.state.draft = Object.fromEntries(Object.entries(sections).map(([k, v]) => [k, {...v.settings}]));
  api.state.loaded = true;

  const index = api.index();
  const enabledKeys = Object.keys(index).filter(k => k.endsWith(".enabled") && !k.startsWith("proactive.contact.affection."));
  assert.equal(enabledKeys.length, 10, `10 个分区的 enabled 总开关必须全部保留，实际：${enabledKeys.join(", ")}`);
  assert.ok(index["image.output.group_enabled"], "image.output.group_enabled 不能被 photo.reference 覆盖");
  assert.ok(index["photo.reference.group_enabled"], "photo.reference.group_enabled 必须存在");

  const board = api.board();
  assert.equal(board.some(g => g.domain === "其他功能"), false, "已知分区不应落入其他功能");
  const allRows = board.flatMap(g => g.rows).filter(r => !r.link);
  for (const row of allRows) {
    assert.ok(HAS_CJK.test(row.label), `${row.key} 缺少中文标题: ${row.label}`);
    assert.notEqual(row.label, row.field, `${row.key} 标题不能退化为字段键`);
    assert.ok(HAS_CJK.test(row.help), `${row.key} 缺少中文简介: ${row.help}`);
    assert.notEqual(row.help, row.field, `${row.key} 简介不能退化为字段键`);
  }
  const qzoneComment = allRows.find(r => r.key === "qzone.comment_enabled");
  assert.equal(qzoneComment.label, "允许回复空间评论");
  assert.match(qzoneComment.help, /不影响私聊或群聊/);
});

test("多目标模式并行拉取角色级与安装级分区并按各自 endpoint 分流保存", async () => {
  const roleApi = "host/targets/t1/roles/r1/config";
  const {api, requests} = loadAllFeaturesSandbox({
    configApi: "/astrbot_plugin_private_companion/" + roleApi,
    onRequest(method, route) {
      if (method === "GET" && route === roleApi) {
        return {sections: {
          life: {group_id: "role.life", scope: "window", edit_token: "tok-life",
            settings: {diary_enabled: true}, fields: {diary_enabled: {kind: "boolean", write_layer: "window"}}},
        }};
      }
      if (method === "GET" && route === "config") {
        return {sections: {
          budget: {group_id: "model.budget", scope: "installation", edit_token: "tok-budget",
            settings: {enable_daily_token_soft_limit: false}, fields: {enable_daily_token_soft_limit: {kind: "boolean"}}},
        }};
      }
      return {result: {operation_id: "ok", replayed: false}};
    },
  });

  await api.load();
  assert.deepEqual(requests.map(r => [r.method, r.route]), [["GET", roleApi], ["GET", "config"]]);
  const html = api.render();
  assert.match(html, /每天自动写日记/);
  assert.match(html, /启用每日软限额/);
  assert.match(html, /当前角色\/窗口/);
  assert.match(html, /所有角色共用/);

  api.state.draft.life.diary_enabled = false;
  api.state.draft.budget.enable_daily_token_soft_limit = true;
  requests.length = 0;
  await api.save();

  const posts = requests.filter(r => r.method === "POST");
  assert.equal(posts.length, 2);
  const lifePost = posts.find(p => p.body.section === "life");
  const budgetPost = posts.find(p => p.body.section === "budget");
  assert.equal(lifePost.route, roleApi + "/configure");
  assert.deepEqual(lifePost.body.changes, {diary_enabled: false});
  assert.equal(budgetPost.route, "config/configure");
  assert.deepEqual(budgetPost.body.changes, {enable_daily_token_soft_limit: true});
});

test("详情弹窗使用 data-wb-field 滚定位、自动切到子分段且错误文本走统一字典不漏机器码", () => {
  const {api, scrolled, dialogMounts} = loadAllFeaturesSandbox({detailField: "sleep_enabled"});
  api.state.sections = {
    life: {group_id: "role.life", scope: "window", edit_token: "t",
      settings: {sleep_enabled: false}, fields: {sleep_enabled: {kind: "boolean"}}},
  };
  api.openDetail("life", "sleep_enabled");
  assert.equal(scrolled.length, 1);
  assert.match(scrolled[0].selector, /\[data-wb-field="sleep_enabled"\]/);
  assert.deepEqual(JSON.parse(JSON.stringify(dialogMounts[0].views)), {life: {initialSection: "sleep"}});
  assert.deepEqual(dialogMounts[0].selectedSections, ["sleep"]);

  assert.match(api.errorText({code: "life_sleep_window_invalid"}), /0 到 360 分钟/);
  assert.match(api.errorText({code: "qzone_scene_list_invalid"}), /半角逗号/);
  assert.match(api.errorText({code: "configuration_section_read_failed"}), /配置分区读取失败/);
  assert.equal(api.errorText(new Error("当前角色没有配置入口")), "当前角色没有配置入口");
  assert.doesNotMatch(api.errorText({code: "unknown_internal_machine_code"}), /unknown_internal_machine_code/);
});

test("默认工作台在异步 read() 完成前调用 selectSection 能在加载后直接激活目标子分段", async () => {
  const ctx = loadConfigSandbox();
  const holders = {};
  const painted = {};
  const pending = [];
  const root = {
    isConnected: true, dataset: {}, textContent: "", classList: {add() {}},
    appendChild(node) {
      holders[node.dataset.configHost] = node;
      Object.defineProperty(node, "innerHTML", {
        get: () => node._html || "",
        set: value => { node._html = value; painted[node.dataset.configHost] = value; },
      });
    },
    querySelector: sel => {
      const m = /^\[data-config-host="(.+)"\]$/.exec(sel);
      return m ? holders[m[1]] || null : null;
    },
  };
  ctx.document.createElement = () => ({
    dataset: {}, classList: {add() {}}, textContent: "", innerHTML: "",
    appendChild() {}, querySelector: () => null, querySelectorAll: () => [],
    addEventListener() {}, removeEventListener() {},
  });
  ctx.window.CompanionProduct = {
    hosted: () => true,
    role: () => ({role_id: "role-a", config_api: "/astrbot_plugin_private_companion/config"}),
    roleTarget: () => ({bot_id: "bot-a", scope_kind: "private"}),
    identity: () => ({installation_id: "inst", operator_id: "admin"}),
    scopeKey: () => "scope-a",
  };
  ctx.window.AstrBotPluginPage = {
    apiGet: (route, body) => new Promise((resolve, reject) => pending.push({route, body, resolve, reject})),
    apiPost: async () => ({}),
  };

  const editor = ctx.window.CompanionConfigEditors.create(["life"], () => root);
  editor.restore();
  editor.paint();
  editor.read();
  /* read() 尚未返回（state.container === null），此时先调用 selectSection('sleep') */
  editor.selectSection("sleep");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(pending.length, 1);
  pending[0].resolve({
    sections: {
      life: {
        section: "life", group_id: "role.life", scope: "window",
        edit_token: "a".repeat(64), revision: 1, configuration_token: "cfg",
        apply_mode: "immediate", layers: ["installation", "role", "window"],
        settings: {
          enabled: true, diary_enabled: true, diary_time: "23:50", timezone: "Asia/Shanghai",
          sleep_enabled: false, sleep_time: "00:30", sleep_timezone: "Asia/Shanghai",
          sleep_variation_minutes: 20, sleep_window_minutes: 120, sleep_minutes: 480,
          reading_enabled: false, derive_enabled: true,
        },
        provenance: Object.fromEntries(
          ["enabled", "diary_enabled", "diary_time", "timezone", "sleep_enabled", "sleep_time",
            "sleep_timezone", "sleep_variation_minutes", "sleep_window_minutes", "sleep_minutes",
            "reading_enabled", "derive_enabled"].map(k => [k, {layer: "role", explicit: true, revision: 1}])
        ),
        fields: {
          enabled: {kind: "boolean", default: false, editable_layers: ["window"], write_layer: "window"},
          diary_enabled: {kind: "boolean", default: true, editable_layers: ["window"], write_layer: "window"},
          diary_time: {kind: "text", default: "23:50", editable_layers: ["window"], write_layer: "window"},
          timezone: {kind: "text", default: "Asia/Shanghai", editable_layers: ["window"], write_layer: "window"},
          sleep_enabled: {kind: "boolean", default: false, editable_layers: ["role"], write_layer: "role"},
          sleep_time: {kind: "text", default: "00:30", editable_layers: ["role"], write_layer: "role"},
          sleep_timezone: {kind: "text", default: "Asia/Shanghai", editable_layers: ["role"], write_layer: "role"},
          sleep_variation_minutes: {kind: "integer", default: 20, minimum: 0, maximum: 120, editable_layers: ["role"], write_layer: "role"},
          sleep_window_minutes: {kind: "integer", default: 120, minimum: 0, maximum: 360, editable_layers: ["role"], write_layer: "role"},
          sleep_minutes: {kind: "integer", default: 480, minimum: 1, maximum: 1440, editable_layers: ["role"], write_layer: "role"},
          reading_enabled: {kind: "boolean", default: false, editable_layers: ["window"], write_layer: "window"},
          derive_enabled: {kind: "boolean", default: true, editable_layers: ["window"], write_layer: "window"},
        },
      },
    },
  });
  for (let i = 0; i < 6; i += 1) await new Promise(resolve => setImmediate(resolve));

  const html = painted.life || "";
  assert.match(html, /data-wb-section-panel="sleep"[^>]*>(?!.*hidden)/, "sleep 子分段应处于可见激活态");
  assert.doesNotMatch(html, /data-wb-section-panel="sleep"[^>]*\bhidden\b/, "sleep 子分段不能被 hidden 遮住");
  assert.match(html, /data-wb-section-panel="routine"[^>]*\bhidden\b/, "首个子分段 routine 应被隐藏");
  editor.unmount();
});

test("多分区保存部分失败时保留已成功分区的基线与新令牌，避免重试版本冲突", async () => {
  let failBudget = true;
  const roleApi = "host/targets/t1/roles/r1/config";
  const {api, requests} = loadAllFeaturesSandbox({
    configApi: "/astrbot_plugin_private_companion/" + roleApi,
    onRequest(method, route, body) {
      if (method === "GET" && route === roleApi) {
        return {sections: {
          life: {group_id: "role.life", scope: "window", edit_token: "tok-life-1",
            settings: {diary_enabled: true}, fields: {diary_enabled: {kind: "boolean"}}},
        }};
      }
      if (method === "GET" && route === "config") {
        return {sections: {
          budget: {group_id: "model.budget", scope: "installation", edit_token: "tok-budget-1",
            settings: {enable_daily_token_soft_limit: false}, fields: {enable_daily_token_soft_limit: {kind: "boolean"}}},
        }};
      }
      if (method === "POST" && body.section === "life") {
        return {result: {operation_id: "op-life", replayed: false, edit_token: "tok-life-2"}};
      }
      if (method === "POST" && body.section === "budget") {
        if (failBudget) throw Object.assign(new Error("core_access_required"), {code: "core_access_required"});
        return {result: {operation_id: "op-budget", replayed: false, edit_token: "tok-budget-2"}};
      }
      return {};
    },
  });
  await api.load();
  api.state.draft.life.diary_enabled = false;
  api.state.draft.budget.enable_daily_token_soft_limit = true;
  requests.length = 0;
  await api.save();
  assert.match(api.state.error, /管理权限/);
  assert.equal(api.state.sections.life.settings.diary_enabled, false);
  assert.equal(api.state.sections.life.edit_token, "tok-life-2");

  failBudget = false;
  requests.length = 0;
  await api.save();
  const retryPosts = requests.filter(r => r.method === "POST");
  assert.equal(retryPosts.length, 1);
  assert.equal(retryPosts[0].body.section, "budget");
});

test("保存分区时切换角色会停止旧批次并保留新角色的配置和令牌", async () => {
  let resolveSave;
  let started;
  const firstPost = new Promise(resolve => { started = resolve; });
  const cfg = "/astrbot_plugin_private_companion/host/targets/t/roles/";
  const options = {
    scopeKey: "role-a",
    configApi: cfg + "role-a/config",
    onRequest(method, route, body) {
      if (method === "POST") {
        assert.equal(body.edit_token, "life-role-a");
        return new Promise(resolve => {
          resolveSave = resolve;
          started();
        });
      }
      if (route === "config") return {sections: {}};
      const token = options.scopeKey;
      const section = name => ({
        scope: "window", edit_token: name + "-" + token,
        settings: {enabled: true}, fields: {enabled: {kind: "boolean"}},
      });
      return {sections: {life: section("life"), relay: section("relay")}};
    },
  };
  const {api, requests} = loadAllFeaturesSandbox(options);
  await api.load();
  api.state.draft.life.enabled = false;
  api.state.draft.relay.enabled = false;
  const saving = api.save();
  await firstPost;

  options.scopeKey = "role-b";
  options.configApi = cfg + "role-b/config";
  await api.load();
  resolveSave({result: {edit_token: "life-a-next"}});
  await saving;

  assert.equal(requests.filter(row => row.method === "POST").length, 1);
  assert.equal(api.state.sections.life.edit_token, "life-role-b");
  assert.equal(api.state.sections.life.settings.enabled, true);
  assert.equal(api.state.sections.relay.edit_token, "relay-role-b");
  assert.equal(api.state.notice, "");
  assert.equal(api.state.saving, false);
});

test("全局搜索覆盖 image.output / web_understanding / expression.learning 且 pageLabels 零英文泄漏", () => {
  const ctx = loadConfigSandbox();
  for (const name of ["reply-page.js", "host-options-page.js", "state-maintenance.js",
    "memory-settings-section.js", "perception-native-page.js"]) {
    vm.runInContext(readPage(name), ctx, {filename: name});
  }
  const s1 = APP_SRC.indexOf("const configSearchLocations={");
  const s2 = APP_SRC.indexOf("let visibleSearchResults=[];");
  vm.runInContext(APP_SRC.slice(s1, s2) + "\nwindow.__searchLoc = {configSearchLocations, configSearchLocation, replySearchLocation};", ctx, {filename: "app-search-loc.js"});
  const {configSearchLocations, configSearchLocation, replySearchLocation} = ctx.window.__searchLoc;

  const pageLabelsMatch = APP_SRC.match(/const pageLabels=(\{[^}]+\});/);
  assert.ok(pageLabelsMatch, "找不到 renderSearch 内的 pageLabels");
  const pageLabels = vm.runInNewContext(`(${pageLabelsMatch[1]})`);

  const providers = [
    ctx.window.CompanionConfigEditors,
    ctx.window.CompanionReplyPage,
    ctx.window.CompanionHostOptionsPage,
    ctx.window.CompanionStateMaintenance,
    ctx.window.CompanionMemorySettingsSection,
    ctx.window.CompanionNativePerceptionPage,
  ];
  let total = 0;
  for (const provider of providers) {
    for (const entry of provider?.searchEntries?.() || []) {
      total += 1;
      const loc = entry.group === "conversation.presentation"
        ? replySearchLocation(entry)
        : configSearchLocation(entry);
      assert.ok(loc, `搜索项 ${entry.group}.${entry.field} 缺少跳转位置`);
      assert.ok(pageLabels[loc.tab], `pageLabels 缺少页面标签: ${loc.tab}`);
      assert.ok(HAS_CJK.test(pageLabels[loc.tab]), `pageLabels[${loc.tab}] 必须为中文`);
    }
  }
  assert.ok(total >= 700, `应覆盖 700+ 条可搜索配置，实际 ${total}`);
  assert.ok(configSearchLocations["image.output"], "configSearchLocations 必须包含 image.output");
  assert.ok(configSearchLocations["web_understanding"], "configSearchLocations 必须包含 web_understanding");
  assert.ok(configSearchLocations["expression.learning"], "configSearchLocations 必须包含 expression.learning");
});

test("作品列表映射 work_type 与错误码，不向用户暴露裸英文码", () => {
  const ctx = loadConfigSandbox();
  const s1 = APP_SRC.indexOf("function contentWorkTypeLabel(");
  const s2 = APP_SRC.indexOf("function contentAssembleButton(");
  assert.ok(s1 > 0 && s2 > s1);
  vm.runInContext(APP_SRC.slice(s1, s2), ctx, {filename: "app-works.js"});

  assert.equal(ctx.contentWorkTypeLabel("story"), "故事");
  assert.equal(ctx.contentWorkTypeLabel("poem"), "诗歌");
  assert.equal(ctx.contentWorkTypeLabel("essay"), "随笔");
  assert.equal(ctx.contentWorkTypeLabel("随笔"), "随笔");
  assert.match(ctx.contentWorksErrorText({code: "works_request_invalid"}), /私聊角色/);
  assert.match(ctx.contentWorksErrorText({code: "works_unavailable"}), /作品库当前不可用/);
  assert.doesNotMatch(ctx.contentWorksErrorText({code: "works_unavailable"}), /works_unavailable/);
});

test("防骚扰 guardRequest 错误映射为中文且不泄漏 bridge_unavailable 等机器码", async () => {
  const ctx = loadConfigSandbox();
  ctx.window.lucide = {createIcons() {}};
  ctx.window.CompanionProduct = {
    bridgeNow: () => null,
    bridgeFor: async () => null,
  };
  vm.runInContext(DECISION_SRC, ctx, {filename: "decision-page.js"});
  const panels = new Map(["overview", "response", "tempo", "safety", "guard", "group"]
    .map(v => [v, {dataset: {decisionPanel: v}, innerHTML: "", hidden: false}]));
  const root = {
    innerHTML: "", dataset: {},
    querySelector(sel) {
      const m = sel.match(/^\[data-decision-panel="([^"]+)"\]$/);
      return m ? panels.get(m[1]) || null : null;
    },
    querySelectorAll: sel => sel === "[data-decision-panel]" ? [...panels.values()] : [],
    addEventListener() {}, removeEventListener() {},
  };
  ctx.window.CompanionDecisionPage.mount(root, {scopeKind: "private", harassmentApi: "reply/harassment"});
  await new Promise(resolve => setTimeout(resolve, 10));
  const guardHtml = panels.get("guard").innerHTML;
  assert.match(guardHtml, /读取防骚扰设置失败：.*管理界面/);
  assert.doesNotMatch(guardHtml, /bridge_unavailable/);
});

test("配置导入导出展示中文分区名与统一中文错误信息", async () => {
  const ctx = loadConfigSandbox();
  const root = {
    isConnected: true, innerHTML: "",
    listeners: {},
    addEventListener(name, fn) { this.listeners[name] = fn; },
    contains: () => true,
    querySelector: () => null,
  };
  ctx.window.document = {
    createElement: () => ({href: "", download: "", click() {}, remove() {}}),
    body: {appendChild() {}},
  };
  ctx.Blob = Blob;
  ctx.URL = {createObjectURL: () => "blob:x", revokeObjectURL() {}};
  ctx.window.CompanionConfirm = async () => true;
  ctx.window.CompanionProduct = {
    selection: () => ({target_id: "t1", role_id: "r1", bot_id: "b1"}),
    roleSelection: () => ({target_id: "t1", role_id: "r1", bot_id: "b1"}),
    target: () => ({target_id: "t1", bot_id: "b1"}),
    roleTarget: () => ({target_id: "t1", bot_id: "b1"}),
    accountLabel: () => "测试 Bot",
    roleLabel: () => "测试角色",
    targetLabel: () => "私聊测试",
    async request(method) {
      if (method === "GET") {
        return {
          schema: "companion.config-export.v1",
          sections: {quota: {settings: {}}},
          unavailable: {busy_gate: "configuration_port_window_required", qzone: "qzone_scene_list_invalid"},
        };
      }
      return {
        applied: {},
        failed: {group_participation: "group_participation_configuration_invalid"},
      };
    },
  };
  vm.runInContext(CONFIG_PORT_SRC, ctx, {filename: "config-port-page.js"});
  ctx.window.CompanionConfigPortPage.mount(root);
  root.listeners.click({target: {closest: () => ({hasAttribute: a => a === "data-config-port-export", disabled: false})}});
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.match(root.innerHTML, /忙碌时的回复与联系（需要明确选择窗口）/);
  assert.match(root.innerHTML, /QQ 空间设置（QQ 空间主题的场景清单/);
  assert.doesNotMatch(root.innerHTML, /busy_gate|qzone_scene_list_invalid/);

  root.listeners.change({target: {matches: () => true, value: "x", files: [{size: 64,
    async text() { return JSON.stringify({schema: "companion.config-export.v1", sections: {group_participation: {settings: {}}}}); }}]}});
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.match(root.innerHTML, /群聊参与（请核对群聊参与设置/);
  assert.doesNotMatch(root.innerHTML, /group_participation_configuration_invalid/);
});

test("模型与维护页面登记 conversation.poke_reply 与 reality.text 任务中文名", () => {
  assert.match(MODELS_SRC, /\["conversation\.poke_reply",\s*"戳一戳回复"/);
  assert.match(MODELS_SRC, /\["reality\.text",\s*"现实提醒文案"/);
  assert.match(MAINTENANCE_SRC, /"conversation\.poke_reply":\s*"戳一戳回复"/);
  assert.match(MAINTENANCE_SRC, /"reality\.text":\s*"现实提醒文案"/);
});
