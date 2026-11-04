/* 全局搜索的索引、排序、截断与命中高亮，直接从 app.js 的真实函数段求值。
   断言的是搜索契约本身：分词归一化、页面项与配置项都能被搜到、排序与截断、
   高亮后仍然转义正文。不对内部辅助函数的名字做假设。 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "陪伴小屋", "app.js"), "utf8");
const start = source.indexOf("const searchIndex=");
const end = source.indexOf("const tokenTaskSettings=");
if (start < 0 || !(end > start)) throw new Error("search section not found");
/* 高亮复用页面自己的转义规则，测试里不另抄一份 HTML 实体表。 */
const escapeHelper = /^const escapeHTML = .*$/m.exec(source);
if (!escapeHelper) throw new Error("escapeHTML helper not found");

const EXPOSED = `window.searchTest = {
  searchIndex, searchTerms, normalizeSearchText, textSearchScore, pageSearchScore,
  settingSearchScore, searchableConfigEntries, invalidateSearchCache, highlightSearchText,
  escapeSearchPattern, rankedSearchResults, renderSearch, SEARCH_RESULT_LIMIT,
  visible: () => visibleSearchResults,
  setRoute(routeName, manageTabName, lifeTabName) {
    globalThis.route = routeName;
    globalThis.manageTab = manageTabName || "";
    globalThis.lifeTab = lifeTabName || "";
  },
};`;

/* 每个用例一份独立上下文：页面模块与搜索缓存互不串味。 */
function harness() {
  const nodes = {
    "#globalSearch": {value: "", attributes: {},
      setAttribute(name, value) { this.attributes[name] = value; }},
    "#searchCount": {textContent: ""},
    "[data-clear-search]": {hidden: true},
    "#searchResults": {innerHTML: "", querySelectorAll() { return []; }},
  };
  const sandbox = {
    window: {},
    document: {querySelectorAll() { return []; }},
    $: selector => nodes[selector] || null,
    icons() {},
    I: () => '<i data-icon="stub"></i>',
    route: "manage",
    manageTab: "",
    lifeTab: "",
    console,
  };
  vm.runInContext(`${escapeHelper[0]}\n${source.slice(start, end)}\n${EXPOSED}`, vm.createContext(sandbox));
  const api = sandbox.window.searchTest;
  const providerNames = ["CompanionConfigEditors", "CompanionReplyPage", "CompanionHostOptionsPage",
    "CompanionStateMaintenance", "CompanionMemorySettingsSection", "CompanionNativePerceptionPage"];
  return {
    api,
    nodes,
    setQuery(query) { nodes["#globalSearch"].value = query; },
    /* providers 传 null 表示页面模块都还没挂上，也就是冷启动时的真实状态。 */
    setProviders(providers) {
      for (const name of providerNames) {
        if (providers && providers[name]) sandbox.window[name] = providers[name];
        else delete sandbox.window[name];
      }
      api.invalidateSearchCache();
    },
  };
}

/* 配置项索引来自各页面模块的 searchEntries()，这里给真实字段键与落地位置。 */
function configEntry(overrides = {}) {
  return {
    group: "proactive.contact",
    section: "contact",
    field: "contact_frequency",
    label: "主动联系频率",
    sectionTitle: "联系设置",
    groupTitle: "主动联系",
    help: "免打扰时段与联系节奏",
    aliases: ["proactive_frequency"],
    ...overrides,
  };
}

/* searchableConfigEntries 产出的 texts 是预归一化后的字段，测试按同样的形状给出。 */
function configResult(page, overrides = {}) {
  const entry = configEntry(overrides);
  const normalize = page.api.normalizeSearchText;
  return {entry, location: {next: "manage", tab: "contact"}, texts: {
    label: normalize(entry.label), field: normalize(entry.field),
    sectionTitle: normalize(entry.sectionTitle), groupTitle: normalize(entry.groupTitle),
    help: normalize(entry.help), aliases: entry.aliases.map(normalize),
  }};
}

test("分词按统一口径切开中英文标点，并归一化全角与大小写", () => {
  const page = harness();
  assert.deepEqual(Array.from(page.api.searchTerms("日记，生活/睡眠|阅读")),
    ["日记", "生活", "睡眠", "阅读"]);
  assert.deepEqual(Array.from(page.api.searchTerms("Ｑｚｏｎｅ 设置；语音、投递")),
    ["qzone", "设置", "语音", "投递"]);
  /* 全空白不是一个关键词，空查询走的是"列出全部页面入口"的分支。 */
  assert.deepEqual(Array.from(page.api.searchTerms("   ")), []);
});

test("页面入口能被搜到，排序按相关度且标题命中高于说明命中", () => {
  const page = harness();
  page.setProviders(null);

  const results = page.api.rankedSearchResults("日记");
  assert.ok(results.length >= 2, "页面标题与说明都应参与命中");
  assert.equal(results[0].data[0], "日记与生活");
  assert.ok(Array.from(results, result => result.kind).every(kind => kind === "page"),
    "没有配置模块时结果里只有页面入口");

  const scores = Array.from(results, result => result.score);
  for (let index = 1; index < scores.length; index += 1) {
    assert.ok(scores[index] <= scores[index - 1], "结果必须按分数从高到低排列");
  }
  const descriptionOnly = results.findIndex(result => result.data[0] === "生活观察");
  assert.ok(descriptionOnly > 0, "只在说明里命中的页面也应出现");
  assert.ok(results[descriptionOnly].score < results[0].score, "标题命中排在说明命中之前");

  /* 标题完全相同比标题前缀命中更相关：同一套权重表里的精确加成。 */
  const exact = page.api.rankedSearchResults("便签")[0];
  assert.equal(exact.data[0], "便签");
  assert.ok(exact.score > results[0].score);
});

test("空查询按索引原顺序列出全部页面入口", () => {
  const page = harness();
  page.setProviders(null);

  const results = page.api.rankedSearchResults("");
  assert.deepEqual(Array.from(results, result => result.data[0]),
    Array.from(page.api.searchIndex, entry => entry[0]));
  assert.ok(Array.from(results, result => result.kind).every(kind => kind === "page"));
  assert.ok(Array.from(results, result => result.score).every(score => score === 0));
});

test("配置项经页面模块的 searchEntries 进入索引，带落地位置并按键去重", () => {
  const page = harness();
  page.setProviders({CompanionConfigEditors: {searchEntries: () => [
    configEntry(),
    /* 同一个 group/section/field 只算一个入口，否则结果里会出现两条一模一样的行。 */
    configEntry(),
    configEntry({field: "contact_cadence", label: "联系节奏", help: "每天联系几次", aliases: []}),
    /* 没有落地位置的分组不能进结果，点了也跳不过去。 */
    configEntry({group: "no.such.group", field: "orphan_field", label: "孤立配置"}),
  ]}});
  assert.deepEqual(Array.from(page.api.searchableConfigEntries(), result => result.entry.field),
    ["contact_frequency", "contact_cadence"]);

  const results = page.api.rankedSearchResults("免打扰时段");
  assert.equal(results.length, 1);
  assert.equal(results[0].kind, "setting");
  assert.equal(results[0].entry.field, "contact_frequency");
  assert.equal(results[0].location.tab, "contact");

  /* 别名参与命中：用户记得住的往往是英文键名。 */
  const byAlias = page.api.rankedSearchResults("proactive_frequency");
  assert.equal(byAlias.length, 1);
  assert.equal(byAlias[0].kind, "setting");
  assert.equal(byAlias[0].entry.label, "主动联系频率");
});

test("配置项评分：字段分档、多词全中、精确与同标签页加成", () => {
  const page = harness();
  page.setProviders(null);
  const terms = query => page.api.searchTerms(query);
  const normalized = query => page.api.normalizeSearchText(query);
  const score = (query, result) => page.api.settingSearchScore(result, terms(query), normalized(query));

  /* textSearchScore 自己负责归一化：全角、大写先折平再比较。 */
  assert.equal(page.api.textSearchScore("ＡＢＣ设置", "abc", [120, 94, 72]), 94);
  assert.equal(page.api.textSearchScore("主动联系", "主动联系", [120, 94, 72]), 120);
  assert.equal(page.api.textSearchScore("主动联系说明", "主动联系", [120, 94, 72]), 94);
  assert.equal(page.api.textSearchScore("我的主动联系", "主动联系", [120, 94, 72]), 72);
  assert.equal(page.api.textSearchScore("别的", "主动联系", [120, 94, 72]), 0);

  const result = configResult(page);
  assert.ok(score("主动联系频率", result) > 0);
  assert.ok(score("主动", result) > 0, "单个关键词命中标签即可");
  assert.ok(score("proactive_frequency", result) > 0, "别名也参与命中");
  /* 历史调用点把原始条目直接交给打分函数：没有预归一化文本时当场归一化，两边同分。 */
  assert.equal(page.api.settingSearchScore(configEntry(), terms("主动"), normalized("主动")),
    score("主动", result), "包装对象与原始条目两种形状打分一致");
  assert.equal(score("主动 不存在的词", result), -1, "多个关键词必须全部命中");
  assert.equal(score("不存在的词", result), -1);

  /* 命中的字段越靠前分越高：标签权重高于说明权重。 */
  assert.ok(page.api.textSearchScore(result.texts.label, "主动", [120, 94, 72])
    > page.api.textSearchScore(result.texts.help, "免打扰", [22, 18, 14]));

  /* 标签精确匹配有额外加成，前缀命中没有。 */
  assert.ok(score("主动联系频率", result) > score("主动联系频", result));

  /* 落地位置只在当前就停在对应标签页时才加分。 */
  const location = {next: "manage", tab: "contact"};
  const withoutBonus = score("主动联系频率", result);
  assert.equal(page.api.settingSearchScore(result, terms("主动联系频率"),
    normalized("主动联系频率"), location), withoutBonus, "当前不在该标签页时不加落地分");
  page.api.setRoute("manage", "contact", "");
  assert.ok(page.api.settingSearchScore(result, terms("主动联系频率"),
    normalized("主动联系频率"), location) > withoutBonus, "落在当前标签页时加分更多");
});

test("结果截断到上限，计数给出真实总数，命中的片段带高亮", () => {
  const page = harness();
  page.api.setRoute("manage", "contact", "");
  page.setProviders({CompanionConfigEditors: {searchEntries: () => Array.from({length: 60}, (_, index) => configEntry({
    field: `contact_field_${index}`,
    label: `免打扰配置 ${index}`,
  }))}});

  page.setQuery("免打扰");
  page.api.renderSearch();

  const total = page.api.rankedSearchResults("免打扰").length;
  assert.ok(total > page.api.SEARCH_RESULT_LIMIT, "总数要超过上限才谈得上截断");
  assert.equal(page.api.SEARCH_RESULT_LIMIT, 50);
  assert.equal(page.api.visible().length, 50, "只渲染上限以内的结果");
  assert.ok(Array.from(page.api.visible()).every(result => result.kind === "setting"));

  const html = page.nodes["#searchResults"].innerHTML;
  assert.equal((html.match(/data-search-result="/g) || []).length, 50);
  assert.equal(page.nodes["#searchCount"].textContent, `显示前 50 项，共 ${total} 项`);
  /* 高亮落在渲染出来的片段上，而不只在高亮函数里成立。 */
  assert.match(html, /<mark>免打扰<\/mark>/);
  assert.equal(page.nodes["[data-clear-search]"].hidden, false);
});

test("高亮保留原始大小写、转义正文，并把正则元字符当作字面量", () => {
  const page = harness();
  const {highlightSearchText} = page.api;

  assert.match(highlightSearchText("日记与生活 <草稿>", ["日记"]), /<mark>日记<\/mark>/);
  assert.match(highlightSearchText("日记与生活 <草稿>", ["日记"]), /&lt;草稿&gt;/);
  assert.equal(highlightSearchText("QZone 设置", ["qzone"]), "<mark>QZone</mark> 设置",
    "匹配不区分大小写，但标记里保留原文");
  assert.equal(highlightSearchText("日记与生活", ["日记", "生活"]),
    "<mark>日记</mark>与<mark>生活</mark>", "一次扫描标出全部关键词");
  /* "." 是字面量而不是通配符，否则会把无关文本一起框进去。 */
  assert.equal(highlightSearchText("axb a.b", ["a.b"]), "axb <mark>a.b</mark>");
  assert.equal(highlightSearchText("<草稿>", []), "&lt;草稿&gt;");
  assert.equal(page.api.escapeSearchPattern("a+b(c)[d]"), "a\\+b\\(c\\)\\[d\\]");
});
