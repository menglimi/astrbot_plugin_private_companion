/* 自我页（角色档案、相处资料、世界知识）深度排版与交互回归护栏。
 *
 * 锁定以下核心视觉与结构契约：
 *   1. 顶部吸顶章节胶囊栏（.section-chips）：top:0 无顶缝、不透明微磨砂底色、清晰边框、
 *      桌面自动换行 + 完整 scroll-margin-top 避让，绝不压盖/裁切下方章节页眉；
 *   2. 「基础资料」卡片：统一标签在上/控件在下网格、姓名与性别首行配对、生日统一 40px 控件条
 *      且空错误节点不占高度、年龄感/职业/种族通栏并采用可自动换行的 .wb-line-grow 控件；
 *   3. 长文本域与阅读体验：156px–204px 起步高度 + field-sizing:content/JS 回退自适应至 360px 上限、
 *      1.68 舒适行高、表格等宽数字字数徽标与单定义设定与经历分段头；
 *   4. 右下角迷你小屋避让：.self-page:not(.is-questionnaire-focus) 底部预留 >= 112px 安全留白
 *      与 scroll-padding-bottom，桌面端保护底部操作栏；
 *   5. 全量 8 章节 + 相处资料 + 世界知识巡检：01–08 眉标完整无过期 platform_names、消除各章节与
 *      相处资料的 50% 孤儿空洞、保持全部 DOM 钩子兼容与六套主题零硬编码色值/零裸 fr/零级联遮蔽。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const PAGE = path.join(ROOT, "pages", "陪伴小屋");
const readPage = name => fs.readFileSync(path.join(PAGE, name), "utf8");
const stripComments = css => css.replace(/\/\*[\s\S]*?\*\//g, "");

const selfCssRaw = readPage("self-page.css");
const selfCss = stripComments(selfCssRaw);
const tokensCss = stripComments(readPage("tokens.css"));
const workbenchJs = readPage("workbench-page.js");
const configEditorJs = readPage("config-editor.js");
const selfRelJs = readPage("self-relationship.js");
const appJs = readPage("app.js");

function parseCssRules(source) {
  const clean = stripComments(source);
  const rules = [];
  let pos = 0;

  function splitSelectors(input) {
    const parts = [];
    let depth = 0;
    let quote = null;
    let buf = "";
    for (let i = 0; i < input.length; i += 1) {
      const ch = input[i];
      if (quote) {
        buf += ch;
        if (ch === "\\") buf += input[++i] || "";
        else if (ch === quote) quote = null;
        continue;
      }
      if (ch === "\"" || ch === "'") { quote = ch; buf += ch; continue; }
      if (ch === "(" || ch === "[") depth += 1;
      else if (ch === ")" || ch === "]") depth = Math.max(0, depth - 1);
      else if (ch === "," && depth === 0) { parts.push(buf.trim()); buf = ""; continue; }
      buf += ch;
    }
    if (buf.trim()) parts.push(buf.trim());
    return parts;
  }

  function readBlock(contexts) {
    let prelude = "";
    while (pos < clean.length) {
      const ch = clean[pos];
      if (ch === "{") {
        const header = prelude.replace(/\s+/g, " ").trim();
        prelude = "";
        pos += 1;
        if (/^@(?:media|container|supports)\b/i.test(header)) {
          readBlock([...contexts, header]);
        } else if (header.startsWith("@")) {
          let depth = 1;
          while (pos < clean.length && depth > 0) {
            if (clean[pos] === "{") depth += 1;
            else if (clean[pos] === "}") depth -= 1;
            pos += 1;
          }
        } else {
          let depth = 1;
          let body = "";
          while (pos < clean.length && depth > 0) {
            if (clean[pos] === "{") depth += 1;
            else if (clean[pos] === "}") {
              depth -= 1;
              if (depth === 0) { pos += 1; break; }
            }
            body += clean[pos];
            pos += 1;
          }
          const normalized = body.replace(/\s+/g, " ").trim();
          for (const selector of splitSelectors(header)) {
            if (selector) rules.push({contexts, selector, body: normalized});
          }
        }
        continue;
      }
      if (ch === "}") { pos += 1; return; }
      if (ch === ";") { prelude = ""; pos += 1; continue; }
      prelude += ch;
      pos += 1;
    }
  }

  readBlock([]);
  return rules;
}

const rules = parseCssRules(selfCssRaw);
const topBodies = selector => rules
  .filter(rule => rule.contexts.length === 0 && rule.selector === selector)
  .map(rule => rule.body)
  .join(";");

test("吸顶章节栏零遮挡：top:0 磨砂不透明底、清晰留白、8 章节眉标完整且锚点具备 scroll-margin-top", () => {
  const chips = topBodies(".section-chips");
  assert.match(chips, /position:\s*sticky/, ".section-chips 必须保持吸顶定位");
  assert.match(chips, /top:\s*0(?:\s*;|$)/, ".section-chips 应贴齐顶部 top:0，避免出现 12px 漏字缝隙");
  assert.doesNotMatch(chips, /top:\s*12px/, "不得残留导致顶部漏字和下压遮挡的 top:12px");
  assert.match(chips, /backdrop-filter:\s*blur\(/, ".section-chips 应提供微磨砂底色");
  assert.match(chips, /background:\s*color-mix\(in srgb,\s*var\(--t-surface\)\s*9\d%/,
    ".section-chips 应使用高不透明度语义表层背景，避免下方文字透叠");
  assert.match(chips, /margin:\s*0\s+0\s+var\(--sp-4\)/, ".section-chips 下方应保留清晰间距");

  const wrapRule = rules.find(rule =>
    rule.contexts.some(ctx => /@container\s+self-journal\s+\(min-width:\s*651px\)/.test(ctx))
    && rule.selector === ".section-chips");
  assert.ok(wrapRule && /flex-wrap:\s*wrap/.test(wrapRule.body) && /overflow-x:\s*visible/.test(wrapRule.body));

  for (const anchor of [
    "#selfChapterIntro",
    "#roleProfileConfigWorkspace",
    "#roleProfileConfigWorkspace .wb-section-panel",
    "#roleProfileConfigWorkspace .wb-field",
    "#selfProfileOrganizer",
    "#relationshipProfileConfigWorkspace",
  ]) {
    const body = topBodies(anchor);
    assert.match(body, /scroll-margin-top:\s*var\(--self-sticky-clearance/,
      `${anchor} 必须声明 scroll-margin-top 避开吸顶章节栏`);
  }

  assert.match(topBodies(".deck-panels"), /--self-sticky-clearance:\s*76px/);
  assert.ok(
    rules.some(rule => rule.contexts.some(ctx => /@container\s+self-journal\s+\(max-width:\s*840px\)/.test(ctx))
      && rule.selector === ".deck-panels"
      && /--self-sticky-clearance:\s*118px/.test(rule.body)),
    "中等宽容器双行换行时应提升 --self-sticky-clearance 至 118px",
  );
  assert.ok(
    rules.some(rule => rule.selector.includes("large-text") && rule.selector.includes(".deck-panels")
      && /--self-sticky-clearance:\s*128px/.test(rule.body)),
    "大字号模式应进一步提高吸顶避让高度",
  );

  assert.match(topBodies("#roleProfileConfigWorkspace .wb-section-panel::before"), /display:\s*none/,
    "单章节卡片内部不再重复渲染英文伪元素眉标，由统一 #selfChapterIntro 承载");
  assert.match(topBodies("#roleProfileConfigWorkspace .wb-section-panel>.wb-group:only-child>.wb-group-head"), /display:\s*none/,
    "单分组章节不再与上方 #selfChapterIntro 重复渲染双标题");
  assert.doesNotMatch(selfCss, /01 \/ IDENTITY|08 \/ EXPRESSION/, "self-page.css 不得残留生硬英文章节眉标");
  assert.doesNotMatch(appJs, /01 \/ IDENTITY|08 \/ EXPRESSION/, "app.js 不得残留生硬英文章节眉标");
  for (const [sectionId, chapterNo, chapterTitle] of [
    ["identity", "第 01 章", "基础身份"],
    ["appearance", "第 02 章", "外貌与识别"],
    ["personality", "第 03 章", "性格与习惯"],
    ["voice", "第 04 章", "表达口吻"],
    ["motivations", "第 05 章", "内在动机"],
    ["boundaries", "第 06 章", "互动边界"],
    ["world", "第 07 章", "世界与场景"],
    ["expression_learning", "第 08 章", "表达学习范围"],
  ]) {
    assert.ok(appJs.includes(`${sectionId}:['${chapterNo}','${chapterTitle}'`),
      `app.js SELF_CHAPTER_COPY 应包含优雅中文眉标 ${chapterNo} ${chapterTitle}`);
  }
  assert.match(appJs, /document\.getElementById\('selfChapterIntro'\)\|\|document\.getElementById\('roleProfileConfigWorkspace'\)/,
    "点击章节胶囊应定位到 #selfChapterIntro 而非 #roleProfileConfigWorkspace，杜绝吸顶栏裁切章节横幅");
  assert.match(topBodies('.self-page .wb-journal [data-wb-row="main_companion_subject"] .wb-control select'), /max-width:\s*380px/);
  assert.match(topBodies('.self-page .wb-journal [data-wb-section-panel="expression_learning"] .wb-segmented'), /grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(topBodies(".self-page .wb-journal .wb-identity-picker .wb-source-count"), /margin-left:\s*auto/);
  assert.doesNotMatch(selfCss, /platform_names/, "已下线的 platform_names 不应残留在 self-page.css 中");
});

test("基础资料卡片：统一标签在上网格、年龄感/职业/生日/种族通栏且生日控件条高度一致", () => {
  const identityRows = topBodies(".wb-identity-rows");
  assert.match(identityRows, /grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);

  const fieldMain = topBodies(".self-page .wb-journal .wb-identity-rows .wb-field-main");
  assert.match(fieldMain, /grid-template-columns:\s*minmax\(0,\s*1fr\)/,
    "基础资料行应采用清爽的标签在上、控件在下单列栈式结构");
  assert.doesNotMatch(fieldMain, /96px|76px/, "不得残留浪费水平空间的 76px/96px 左侧标签窄槽");

  assert.doesNotMatch(selfCss, /\.wb-field:has\(\.wb-date\)\s+\.wb-field-main\s*>\s*\.wb-control\{[^}]*grid-column:\s*2/,
    "移除旧的 wb-date grid-column:2 错位覆盖");

  const fullWidthIdentity = rules.find(rule =>
    rule.contexts.length === 0
    && rule.selector.startsWith(".self-page .wb-journal .wb-identity-rows :is(")
    && /grid-column:\s*1\s*\/\s*-1/.test(rule.body));
  assert.ok(fullWidthIdentity, "基础资料卡缺少长字段通栏规则");
  for (const field of ["birthday", "age", "occupation", "species"]) {
    assert.ok(fullWidthIdentity.selector.includes(`[data-wb-row="${field}"]`),
      `基础资料字段 ${field} 应通栏展示避免截断`);
  }

  for (const sel of [
    ".self-page .wb-journal .wb-date-text",
    ".self-page .wb-journal .wb-date input[type=date]",
    ".self-page .wb-journal .wb-date-flag",
  ]) {
    assert.match(topBodies(sel), /min-height:\s*40px/, `${sel} 应统一为 40px 控件高度`);
  }
  assert.match(topBodies(".self-page .wb-journal .wb-date-clear"), /height:\s*40px/);
  assert.match(topBodies(".self-page .wb-journal .wb-date-error:empty"), /display:\s*none/,
    "空的生日错误提示节点不得占用垂直间距");
});

test("长描述字段与多行正文域：可换行自适应高度、舒展最小高度与清晰字数徽标", () => {
  const lineGrow = topBodies(".self-page .wb-journal .wb-control textarea.wb-line-grow");
  assert.match(lineGrow, /min-height:\s*40px/);
  assert.match(lineGrow, /max-height:\s*180px/);
  assert.match(lineGrow, /field-sizing:\s*content/);

  const narrative = topBodies(".self-page .wb-journal .wb-control textarea:not(.wb-line-grow):not([data-wb-quote-line])");
  assert.match(narrative, /min-height:\s*156px/);
  assert.match(narrative, /max-height:\s*360px/);
  assert.match(narrative, /field-sizing:\s*content/);
  assert.match(narrative, /line-height:\s*1\.68/);

  const tallMid = rules.find(rule =>
    rule.contexts.length === 0
    && rule.selector.includes('[data-wb-row="key_lore"]')
    && /min-height:\s*188px/.test(rule.body));
  const tallMax = rules.find(rule =>
    rule.contexts.length === 0
    && rule.selector.includes('[data-wb-row="identity"]')
    && rule.selector.includes('[data-wb-row="personality"]')
    && rule.selector.includes('[data-wb-row="world"]')
    && /min-height:\s*204px/.test(rule.body));
  assert.ok(tallMid, "1000–1800 字长文本域（如关键设定、外貌补充、动机、边界）应具备 188px 舒展最小高度");
  assert.ok(tallMax, "2000–4200 字超长文本域（身份经历、性格描述、世界设定）应具备 204px 舒展最小高度");

  const counter = topBodies(".self-page .wb-journal .wb-text-length");
  assert.match(counter, /border-radius:\s*var\(--r-pill\)/);
  assert.match(counter, /font-variant-numeric:\s*tabular-nums/);
});

test("底部迷你小屋安全留白：.self-page 预留 >=112px 底部空间且桌面保护底部操作条", () => {
  const safeBottom = topBodies(".self-page:not(.is-questionnaire-focus)");
  assert.match(safeBottom, /padding-bottom:\s*calc\(max\(var\(--shell-pip-h,\s*112px\),\s*112px\)\s*\+\s*var\(--sp-2\)\)/,
    ".self-page（非沉浸问卷态）必须预留 >= 112px 底部安全留白以避开右下角迷你小屋");
  assert.match(safeBottom, /scroll-padding-bottom:\s*calc\(max\(var\(--shell-pip-h,\s*112px\),\s*112px\)\s*\+\s*var\(--sp-4\)\)/);

  const pipRightClearance = rules.filter(rule =>
    rule.contexts.some(ctx => /@media\s*\(min-width:\s*761px\)/.test(ctx))
    && /padding-right:\s*calc\(var\(--shell-pip-w/.test(rule.body));
  assert.ok(pipRightClearance.some(rule => rule.selector.includes(".self-organizer")),
    "桌面离开首页态应为底部人格整理条预留右侧小屋避让空间");
  assert.ok(pipRightClearance.some(rule => rule.selector.includes(".self-relationship-actions")),
    "桌面离开首页态应为相处资料保存条预留右侧小屋避让空间");
});

test("全量 8 章节与相处资料零孤儿空洞、主题令牌完备且响应式级联无遮蔽", () => {
  const fullWidthRows = rules.find(rule =>
    rule.contexts.length === 0
    && rule.selector.startsWith(".self-page .wb-journal [data-wb-row]:is(")
    && /grid-column:\s*1\s*\/\s*-1/.test(rule.body));
  assert.ok(fullWidthRows);
  for (const field of [
    "clothing",
    "signature_phrases",
    "first_person",
    "network",
    "main_companion_subject",
    "appearance_points",
    "image_identity_hint",
  ]) {
    assert.ok(fullWidthRows.selector.includes(`[data-wb-row="${field}"]`),
      `字段 ${field} 应通栏排布，避免孤儿空洞或控件挤压`);
  }

  assert.match(topBodies(".self-contact-fields"), /grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)/,
    "相处资料主动联系 3 项开关应在桌面端 3 列均匀排布，消除 2+1 孤儿空洞");
  assert.match(topBodies(".self-knowledge-heading"), /border:\s*1px solid var\(--t-line\)/,
    "世界知识标签页标题区应具备统一视觉规范");

  /* 零硬编码色值（仅允许 tokens 语义变量与 color-mix）。 */
  assert.doesNotMatch(selfCss, /#[0-9a-fA-F]{3,8}\b|\brgba?\(/i,
    "self-page.css 不应包含硬编码十六进制或 rgb/rgba 色值");
  assert.doesNotMatch(selfCss, /-var\(/, "self-page.css 不得存在非法 -var() 语法");

  /* 零裸 fr 轨道。 */
  for (const match of selfCss.matchAll(/grid-template-columns\s*:\s*([^;{}]+)/g)) {
    let val = match[1];
    for (let i = 0; i < 6; i += 1) {
      const next = val.replace(/minmax\([^()]*\)/g, "MM").replace(/min\([^()]*\)/g, "MN");
      if (next === val) break;
      val = next;
    }
    assert.doesNotMatch(val, /(?<![a-zA-Z0-9_-])(?:\d*\.\d+|\d+)fr\b/,
      `self-page.css 不得包含裸 fr 轨道：${match[1]}`);
  }

  /* 所有 --t-* / --sp-* / --r-* / --fs-* 引用都必须在 tokens.css 中已声明。 */
  const declared = new Set([...tokensCss.matchAll(/(--(?:t|sp|r|fs)-[a-zA-Z0-9_-]+)\s*:/g)].map(m => m[1]));
  const missing = [...selfCss.matchAll(/var\(\s*(--(?:t|sp|r|fs)-[a-zA-Z0-9_-]+)/g)]
    .map(m => m[1])
    .filter(token => !declared.has(token));
  assert.deepEqual([...new Set(missing)], [], `self-page.css 引用了未声明令牌：${missing.join(", ")}`);

  /* 零自查询容器（@container self-journal 内不得直接选择 .self-page）。 */
  for (const rule of rules) {
    if (rule.contexts.some(ctx => /@container\s+self-journal\b/.test(ctx))) {
      assert.notEqual(rule.selector, ".self-page", "@container self-journal 不得查询容器自身 .self-page");
    }
  }

  /* 关键响应式断点源码顺序：桌面声明 < @media(max-width:720px) < @container(max-width:740px) < (max-width:650px) < (max-width:380px)。 */
  const introDesktopIdx = selfCss.indexOf(".self-chapter-intro{");
  const media720Idx = selfCss.lastIndexOf("@media (max-width:720px)");
  const container740Idx = selfCss.indexOf("@container self-journal (max-width:740px)");
  const container650Idx = selfCss.indexOf("@container self-journal (max-width:650px)");
  const container380Idx = selfCss.indexOf("@container self-journal (max-width:380px)");
  assert.ok(introDesktopIdx >= 0 && media720Idx > introDesktopIdx, "@media(max-width:720px) 必须位于桌面声明之后");
  assert.ok(container740Idx > media720Idx, "@container(max-width:740px) 必须位于 @media(max-width:720px) 之后");
  assert.ok(container650Idx > container740Idx, "@container(max-width:650px) 必须位于 740px 容器之后");
  assert.ok(container380Idx > container650Idx, "@container(max-width:380px) 必须位于 650px 容器之后");

  /* 兼容红线：核心 DOM 钩子与函数名全部保留。 */
  assert.match(appJs, /function selectSelfProfileSection\(section\)/);
  assert.match(appJs, /id="selfChapterIntro"/);
  assert.match(appJs, /configEditors\('roleProfileConfigWorkspace'/);
  assert.match(appJs, /id="relationshipProfileConfigWorkspace"/);
});

test("workbench-page.js 运行时：journal 模式下长事实字段渲染为可换行 .wb-line-grow，切换章节触发自动测高", async () => {
  const requests = [];
  const holders = {};
  const painted = {};
  const grewNodes = [];

  function makeTextareaNode(mode, scrollHeight) {
    return {
      dataset: {wbAutogrow: mode},
      scrollHeight,
      style: {height: ""},
    };
  }
  function makeQuoteNode(scrollHeight) {
    return {scrollHeight, style: {height: ""}};
  }

  const fakeVoicePanel = {
    dataset: {wbSectionPanel: "voice"},
    hidden: true,
    querySelectorAll(selector) {
      if (selector === "[data-wb-quote-line]") return [makeQuoteNode(72)];
      if (selector === "[data-wb-autogrow]") return [makeTextareaNode("line", 66), makeTextareaNode("area", 420)];
      return [];
    },
  };
  const fakeIdentityPanel = {
    dataset: {wbSectionPanel: "identity"},
    hidden: false,
    querySelectorAll() { return []; },
  };
  const root = {
    isConnected: true,
    dataset: {},
    textContent: "",
    classList: {add() {}},
    appendChild(node) {
      holders[node.dataset.configHost] = node;
      Object.defineProperty(node, "innerHTML", {
        get: () => node._html || "",
        set: value => { node._html = value; painted[node.dataset.configHost] = value; },
      });
    },
    querySelector: selector => {
      const match = /^\[data-config-host="(.+)"\]$/.exec(selector);
      return match ? holders[match[1]] || null : null;
    },
  };
  const window = {
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {},
    CompanionProduct: {
      hosted: () => true,
      role: () => ({role_id: "role-a", reply_api: "/astrbot_plugin_private_companion/reply"}),
      roleTarget: () => ({bot_id: "bot-a", scope_kind: "private"}),
      identity: () => ({installation_id: "inst", operator_id: "admin"}),
      scopeKey: () => "scope-a",
    },
    AstrBotPluginPage: {
      apiGet: (route, body) => new Promise((resolve, reject) => requests.push({method: "GET", route, body, resolve, reject})),
      apiPost: (route, body) => new Promise((resolve, reject) => requests.push({method: "POST", route, body, resolve, reject})),
    },
  };
  const document = {
    createElement: () => ({
      dataset: {},
      classList: {add() {}},
      textContent: "",
      innerHTML: "",
      appendChild() {},
      querySelector: () => null,
      querySelectorAll(selector) {
        if (selector === "[data-wb-section-panel]") return [fakeIdentityPanel, fakeVoicePanel];
        if (selector === "[data-wb-autogrow]") {
          const line = makeTextareaNode("line", 88);
          const area = makeTextareaNode("area", 500);
          grewNodes.push(line, area);
          return [line, area];
        }
        return [];
      },
      addEventListener(type, fn) { this.listeners[type] = fn; },
      removeEventListener(type) { delete this.listeners[type]; },
      listeners: {},
    }),
    querySelector: () => null,
    querySelectorAll: () => [],
    hidden: false,
  };
  const sandbox = {
    window,
    document,
    sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    Event: class Event { constructor(type) { this.type = type; } },
    crypto: {randomUUID: () => "self-key"},
    setTimeout,
    clearTimeout,
    URLSearchParams,
    Math,
    Number,
    Object,
    JSON,
    Array,
    String,
  };
  vm.runInNewContext(workbenchJs, sandbox);
  vm.runInNewContext(configEditorJs, sandbox);

  const editors = window.CompanionConfigEditors.create(["role_profile"], () => root, {
    role_profile: {
      presentation: "journal",
      sectionTabPrefix: "self-chapter-",
      initialSection: "identity",
      navigationLimit: 9,
    },
  });
  editors.restore();
  editors.paint();
  editors.read();
  for (let i = 0; i < 6; i += 1) await new Promise(resolve => setImmediate(resolve));

  const fieldNames = [
    "name", "gender", "birthday", "age", "occupation", "species", "key_lore", "identity",
    "character_extra", "avatar_data_url", "appearance_points", "hair", "eyes", "clothing",
    "image_identity_hint", "appearance", "voice_examples", "world_name", "era", "tone",
  ];
  const settings = Object.fromEntries(fieldNames.map(name => [name, ""]));
  settings.name = "澄汐";
  settings.age = "25岁左右（夏季出生；成长经历横跨校园与独奏演奏期）";
  settings.occupation = "青年古典钢琴演奏家（国际钢琴比赛入围者，常年在不同城市巡演与录音）";
  settings.key_lore = "测试关键设定";
  settings.identity = "测试身份经历补充";
  settings.voice_examples = "今天的风很安静。\n你先说，我听着。";
  requests[0].resolve({
    sections: {
      role_profile: {
        settings,
        edit_token: "a".repeat(64),
        revision: 1,
        configuration_token: "configuration",
        group_id: "role_profile",
        scope: "role",
        apply_mode: "immediate",
        layers: ["installation", "role"],
        provenance: Object.fromEntries(fieldNames.map(name => [name, {layer: "role", explicit: true, revision: 1}])),
        fields: Object.fromEntries(fieldNames.map(name => [name, {
          kind: "text",
          default: "",
          maximum_chars: name === "identity" ? 2000 : name === "key_lore" ? 1000 : 240,
          apply_mode: "immediate",
          editable_layers: ["role"],
        }])),
        section: "role_profile",
      },
    },
  });
  for (let i = 0; i < 6; i += 1) await new Promise(resolve => setImmediate(resolve));

  const html = painted.role_profile;
  assert.match(html, /<input type="text" data-wb-field="name"/, "姓名保持单行文本输入");
  assert.match(html, /<input type="text" data-wb-field="avatar_data_url"/, "隐藏头像字段保持单行文本输入");
  for (const field of ["age", "occupation", "appearance_points", "hair", "eyes", "clothing", "image_identity_hint", "world_name", "era", "tone"]) {
    assert.match(
      html,
      new RegExp(`<textarea class="wb-line-grow" data-wb-field="${field}" data-wb-autogrow="line" rows="1"`),
      `journal 模式下 ${field} 应渲染为可换行自动增高的 .wb-line-grow 文本域`,
    );
  }
  assert.match(html, /<textarea data-wb-field="key_lore" data-wb-autogrow="area"/, "长正文域应标记 data-wb-autogrow=\"area\"");
  assert.match(html, /<textarea data-wb-field="identity" data-wb-autogrow="area"/);

  /* 非 field-sizing 回退时自动高度受上限保护（line <= 180px, area <= 360px）。 */
  assert.equal(grewNodes[0].style.height, "88px");
  assert.equal(grewNodes[1].style.height, "360px");

  /* 切换隐藏章节到 voice 时应展开对应面板并重新测高。 */
  let quoteSized = false;
  let lineSized = false;
  let areaSized = false;
  fakeVoicePanel.querySelectorAll = selector => {
    if (selector === "[data-wb-quote-line]") {
      const node = makeQuoteNode(72);
      return [new Proxy(node, {get(t, k) { if (k === "style") return new Proxy(t.style, {set(st, sk, v) { if (sk === "height" && v === "72px") quoteSized = true; st[sk] = v; return true; }}); return t[k]; }})];
    }
    if (selector === "[data-wb-autogrow]") {
      const lineNode = makeTextareaNode("line", 66);
      const areaNode = makeTextareaNode("area", 420);
      return [
        new Proxy(lineNode, {get(t, k) { if (k === "style") return new Proxy(t.style, {set(st, sk, v) { if (sk === "height" && v === "66px") lineSized = true; st[sk] = v; return true; }}); return t[k]; }}),
        new Proxy(areaNode, {get(t, k) { if (k === "style") return new Proxy(t.style, {set(st, sk, v) { if (sk === "height" && v === "360px") areaSized = true; st[sk] = v; return true; }}); return t[k]; }}),
      ];
    }
    return [];
  };
  editors.selectSection("voice");
  assert.equal(fakeVoicePanel.hidden, false);
  assert.equal(fakeIdentityPanel.hidden, true);
  assert.equal(quoteSized, true, "切到口吻章节时应对刚显示的台词气泡重新测高");
  assert.equal(lineSized, true, "切到新章节时应对可换行字段重新测高");
  assert.equal(areaSized, true, "切到新章节时应对正文域测高并限制在 360px 上限内");

  /* 编辑 .wb-line-grow 时同步更新草稿并按 180px 上限自动增高。 */
  let dirtyMarked = false;
  const occRow = {
    classList: {
      toggle(cls, flag) { if (cls === "is-dirty" && flag) dirtyMarked = true; },
      contains() { return false; },
    },
    querySelector: () => null,
  };
  const occInput = {
    tagName: "TEXTAREA",
    value: "更新后的长职业描述，包含多段履历与演出经历，不再在半宽单行里截断。",
    dataset: {wbField: "occupation", wbAutogrow: "line"},
    scrollHeight: 240,
    style: {height: ""},
    setAttribute() {},
    closest(selector) {
      if (selector === "[data-wb-field]") return this;
      if (selector === ".wb-field") return occRow;
      return null;
    },
  };
  holders.role_profile.listeners.input({target: occInput});
  assert.equal(editors.state.instances[0].state.draft.occupation, occInput.value);
  assert.equal(dirtyMarked, true);
  assert.equal(occInput.style.height, "180px", "可换行字段在 JS 回退模式下受 180px 上限保护");

  /* 当浏览器原生支持 field-sizing: content 时，JS 不写死内联高度以保留原生流式缩放。 */
  sandbox.CSS = {supports: (prop, val) => prop === "field-sizing" && val === "content"};
  occInput.style.height = "";
  occInput.value = "二次修改";
  holders.role_profile.listeners.input({target: occInput});
  assert.equal(occInput.style.height, "", "原生支持 field-sizing: content 时不应写入内联 height");
});

test("self-relationship.js 运行时：职业字段通栏消除孤儿空洞并保留全部 data-self-relationship-field 钩子", async () => {
  const root = {
    innerHTML: "",
    addEventListener() {},
    querySelector: () => null,
  };
  const window = {
    CompanionProduct: {
      role: () => ({role_id: "role-a"}),
      roleTarget: () => ({bot_id: "bot-a"}),
      roleLabel: () => "澄汐",
      platformLabel: () => "QQ",
      acquaintanceDirectory: async () => ({
        persons: [{
          subject_id: "core-1",
          native_account_id: "core-1",
          display_name: "阿澈",
          platform: "qq-main",
          bot_id: "bot-a",
          account_id: "account-a",
          role_id: "role-a",
          private_target: true,
          settings_available: true,
          identity_tier: "core",
        }],
      }),
      request: async () => ({
        sections: {
          profile: {
            edit_token: "p-1",
            settings: {
              default_nickname: "阿澈",
              role_relation: "搭档",
              user_gender: "男",
              user_birthday: "4月17日",
              user_occupation: "杂志编辑 / 音乐专栏撰稿人",
              interaction: "日常自然对话",
              extra: "",
            },
          },
          contact: {
            edit_token: "c-1",
            settings: {enabled: true, automatic_enabled: true, quiet_enabled: false, note: "", style_prompt: ""},
            provenance: {},
          },
        },
      }),
    },
    CompanionWorkbench: {
      scopeHintMarkup: text => `<span>${text}</span>`,
      scopeHintClick: () => false,
      scopeHintKeydown() {},
      scopeHintReset() {},
    },
  };
  const document = {addEventListener() {}};
  vm.runInNewContext(selfRelJs, {window, document, Promise, Date, Math, JSON, Object, String, Map, Set, Array, Error});
  window.CompanionSelfRelationship.mount(root);
  for (let i = 0; i < 8; i += 1) await new Promise(resolve => setImmediate(resolve));

  assert.match(root.innerHTML, /class="self-relationship-field is-wide"><span>职业<\/span><input type="text" data-self-relationship-field="user_occupation"/,
    "相处资料的职业字段应标记 is-wide 通栏，避免 5 项单行字段在两列网格中留下右侧孤儿空洞");
  for (const field of ["default_nickname", "role_relation", "user_gender", "user_birthday", "user_occupation", "interaction", "extra"]) {
    assert.match(root.innerHTML, new RegExp(`data-self-relationship-field="${field}"`));
  }
  for (const field of ["enabled", "automatic_enabled", "quiet_enabled", "note", "style_prompt"]) {
    assert.match(root.innerHTML, new RegExp(`data-self-contact-field="${field}"`));
  }
});
