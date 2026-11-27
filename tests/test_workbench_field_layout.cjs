/* 配置编辑器字段网格的护栏。
 *
 * 起因：`.wb-fields` 曾是 `display:flex;flex-direction:column`——每个字段独占一整行，
 * 一个九个数字字段的分区要滚九屏，而数字框还被拉伸到整幅宽，右侧全是空白。
 * 那条规则本身没有任何检查守着，谁都能在不知情的情况下改回去。
 *
 * 这里把「字段要按宽度分列」「长控件整幅通栏」「窄屏回落单列」「数字框不拉伸」
 * 四件事落成断言。判断依据是 workbench.css 的实际声明，不是文档。
 *
 * 用 node --test 运行。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const DIR = path.join(__dirname, "..", "pages", "陪伴小屋");
const SOURCE = fs.readFileSync(path.join(DIR, "workbench.css"), "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "");

/* 取某选择器在顶层的声明；同名重复出现时合并，便于逐条断言。 */
function topRules(source) {
  const rules = new Map();
  for (const [selector, body] of walk(source)) {
    if (selector === null) continue;
    for (const part of selector.split(",")) {
      const key = part.trim();
      if (!key || key.startsWith("@")) continue;
      rules.set(key, (rules.get(key) ? rules.get(key) + ";" : "") + body.trim());
    }
  }
  return rules;
}

/* 取 max-width 断点内的声明，键是 `.selector|max-width:760px`。 */
function mediaRules(source) {
  const rules = new Map();
  for (const [selector, body] of walk(source)) {
    if (selector === null || !selector.startsWith("@media") || !/max-width/.test(selector)) continue;
    const query = (selector.match(/max-width:\s*\d+px/) || [""])[0].replace(/\s+/g, "");
    for (const [inner, innerBody] of walk(body)) {
      if (inner === null || inner.startsWith("@")) continue;
      for (const part of inner.split(",")) {
        const key = part.trim();
        if (key) rules.set(key + "|" + query, (rules.get(key) || "") + innerBody.trim());
      }
    }
  }
  return rules;
}

/* 极简 CSS 遍历：产出 [选择器, 声明块]；`@media` 这类前置块的选择器带 @ 返回，
   其内容由调用方按需再遍历一次。注释已在读入时剥掉。 */
function* walk(source) {
  let index = 0;
  let prelude = "";
  while (index < source.length) {
    const character = source[index];
    if (character === "{") {
      const selector = prelude.trim();
      let depth = 1;
      let body = "";
      index += 1;
      while (index < source.length && depth > 0) {
        if (source[index] === "{") depth += 1;
        else if (source[index] === "}") {
          depth -= 1;
          if (depth === 0) break;
        }
        body += source[index];
        index += 1;
      }
      index += 1;
      prelude = "";
      yield [selector, body];
      continue;
    }
    if (character === "}") {
      prelude = "";
      index += 1;
      continue;
    }
    prelude += character;
    index += 1;
  }
}

const TOP = topRules(SOURCE);
const MEDIA = mediaRules(SOURCE);
const rule = selector => TOP.get(selector) || "";
const mediaRule = (selector, query) => MEDIA.get(selector + "|" + query) || "";

test("字段容器按可用宽度分列，而不是每字段一整行", () => {
  const fields = rule(".wb-fields");
  assert.match(fields, /display:\s*grid/,
    ".wb-fields 不是 grid：字段会退回单列堆叠（一个数字字段占一整行）");
  const autoFit = fields.match(/grid-template-columns:\s*repeat\(auto-fit,\s*minmax\((?:min\((\d+)px,\s*100%\)|(\d+)px)/);
  assert.ok(autoFit, ".wb-fields 需要 repeat(auto-fit,minmax(min(Npx,100%),1fr)) 才能在宽屏并排、窄屏安全回落");
  const minimum = Number(autoFit[1] || autoFit[2]);
  /* 在 AstrBot 双侧栏（~880–912px 内容区）里，360–520px 的列宽下限既能让 1280/1440 桌面
     舒适排成两列，又能在带侧栏或中等窗口里自动回落单列。 */
  assert.ok(minimum >= 360 && minimum <= 520,
    `列宽下限 ${minimum}px 不在合理区间：太小会挤窄卡片，太大又在 904px 面板下退回一行一个字段`);
  assert.doesNotMatch(fields, /flex-direction:\s*column/,
    ".wb-fields 又变成纵向堆叠了：这正是本次修复要消掉的那个形状");
});

test("字段保留层轨与主体，空的恢复继承槽位按需折叠", () => {
  assert.match(rule(".wb-field"), /display:\s*grid/);
  assert.match(rule(".wb-field"), /grid-template-columns:\s*12px\s+minmax\(0,\s*1fr\)/);
  assert.match(rule(".wb-field:where(:has(>.wb-field-side:not(:empty)))"),
    /grid-template-columns:\s*12px\s+minmax\(0,\s*1fr\)\s+auto/);
  assert.match(rule(".wb-field>.wb-field-side:empty"), /display:\s*none/);
  assert.match(rule(".wb-field>.wb-field-main"), /grid-column:2/);
  assert.match(rule(".wb-context-tip:not(:popover-open)"), /display:none/);
  assert.match(rule(".wb-rail"), /display:flex;flex-direction:column/);
  assert.match(rule(".wb-node.is-source"), /background:var\(--t-accent\)/);
});

test("长控件整幅通栏，不被挤进窄列", () => {
  const wide = [...TOP.entries()]
    .filter(([, body]) => /grid-column:\s*1\s*\/\s*-1/.test(body))
    .map(([selector]) => (selector.match(/:has\(([^)]+)\)/) || [])[1])
    .filter(Boolean);
  for (const needed of [
    "textarea",
    ".wb-word-editor",
    ".wb-keywords",
    ".wb-replacement-editor",
    ".wb-source-picker",
    ".wb-order-editor",
    ".wb-date",
    ".wb-tagset",
    ".wb-quotes",
  ]) {
    assert.ok(wide.includes(needed),
      `长控件 ${needed} 没有通栏规则：多行文本/关键词/替换规则/发送顺序会被塞进一列里`);
  }
});

test("窄屏回落单列", () => {
  const narrow = mediaRule(".wb-fields", "max-width:760px");
  assert.match(narrow, /grid-template-columns:\s*minmax\(0,\s*1fr\)/,
    "窄屏断点里没有把字段回落成单列：小屏会被压成两三列，标签与控件互相挤");
});

test("数字输入框按内容取宽，不再拉伸到整幅", () => {
  assert.doesNotMatch(rule(".wb-number"), /flex:\s*1(?![.\d])/,
    "数字框容器又回到整行拉伸：输入框会在宽单元格里拉成一条长带");
  assert.match(rule(".wb-number input"), /flex:\s*0\s+1\s+\d+px/,
    ".wb-number input 没有宽度上限：范围提示会被推到很远的地方");
});

test("共享工作台全宽分组白卡、3px 纤细层轨、智能跨列与非侵入式脏态保存条", () => {
  const decisionCss = fs.readFileSync(path.join(DIR, "decision-page.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  assert.match(rule(".wb-card-copy p"), /max-width:\s*none/, "卡片介绍段落不应被 62ch 硬截断成窄列三行");
  assert.match(rule(".wb-rail"), /width:\s*3px/, "层轨应使用精致 3px 细色条而非厚重 11px 色块");
  assert.match(rule(".wb-rail-legend"), /width:\s*fit-content/, "层轨图例在网格容器内应保持紧凑胶囊宽");
  assert.match(rule(".wb-field.is-span-full"), /grid-column:\s*1\s*\/\s*-1/);
  assert.match(rule(".wb-actions"), /position:\s*static/, "未修改态保存条应安静落底，不腰斩底部控件");
  assert.match(rule(".wb-actions.is-dirty"), /position:\s*sticky/, "仅在存在未保存改动时吸底浮起保存条");
  assert.match(mediaRule(".wb-actions.is-dirty", "max-width:760px"), /position:\s*static/, "窄屏脏态保存条也不应遮挡字段");
  assert.match(decisionCss, /\.decision-editors \.wb-card-body\{[^}]*grid-template-columns:minmax\(0,1fr\)/,
    "决策页配置分组应全宽单列堆叠，根除字段数不等分组并排扯出的大块空白");
  assert.match(decisionCss, /\.decision-page\{[^}]*padding-bottom:var\(--sp-6\)/);
  assert.match(rule(".models-budget-section .wb-card:not(.wb-journal):not(.wb-policy) .wb-group"),
    /border:\s*0[\s\S]*background:\s*transparent/, "外层自带卡片壳的预算卡内不应再嵌套一层分组白卡边框");
  assert.match(rule(".models-budget-section .wb-card .wb-actions"),
    /position:\s*static[\s\S]*border:\s*0[\s\S]*border-top:\s*1px solid var\(--t-line\)/,
    "预算卡内部动作条应保持无三边残框且不浮起遮挡");
  assert.match(rule(".models-budget-section .wb-card .wb-actions.is-dirty"),
    /position:\s*static[\s\S]*border:\s*0[\s\S]*border-top:\s*1px solid var\(--t-line\)/,
    "预算卡脏态内部动作条也应保持无三边残框且不浮起遮挡");
  assert.match(rule(".models-budget-section .wb-card .wb-rail-legend"),
    /padding:\s*var\(--sp-1\)\s+var\(--sp-3\)/, "预算卡内胶囊层轨图例应保留左右内边距不挤字");

  // 运行时验证 computeGroupSpans：单字段卡、主开关+4子项、3项对等开关与混合奇偶段落零空洞
  const vm = require("node:vm");
  const wbSource = fs.readFileSync(path.join(DIR, "workbench-page.js"), "utf8");
  const win = {addEventListener() {}, removeEventListener() {}};
  const ctx = vm.createContext({
    window: win,
    document: {documentElement: {dataset: {}}, addEventListener() {}, removeEventListener() {}},
    console, Intl, Date, JSON, Math, Number, Object, Array, String, Set, Map, Boolean, Error, Promise,
    Event: class Event { constructor(t) { this.type = t; } },
    CustomEvent: class CustomEvent { constructor(t, o) { this.type = t; this.detail = o?.detail; } },
    sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    setTimeout: () => 1, clearTimeout() {},
  });
  vm.runInContext(wbSource, ctx);

  const host = {innerHTML: "", addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [], closest: () => null};
  const statusPayload = {
    edit_token: "a".repeat(64),
    scope: "role",
    layers: ["installation", "role"],
    settings: {
      solo_flag: true,
      master_on: true, child_a: true, child_b: true, child_c: true, child_d: true,
      peer_1: true, peer_2: true, peer_3: true,
      img_master: true, img_cache: true, img_max: 300, img_prompt: "", img_a: 10, img_b: 20, img_c: 30,
    },
    fields: {
      solo_flag: {kind: "boolean"},
      master_on: {kind: "boolean"}, child_a: {kind: "boolean"}, child_b: {kind: "boolean"}, child_c: {kind: "boolean"}, child_d: {kind: "boolean"},
      peer_1: {kind: "boolean"}, peer_2: {kind: "boolean"}, peer_3: {kind: "boolean"},
      img_master: {kind: "boolean"}, img_cache: {kind: "boolean"}, img_max: {kind: "integer"},
      img_prompt: {kind: "text"}, img_a: {kind: "integer"}, img_b: {kind: "integer"}, img_c: {kind: "integer"},
    },
    provenance: {},
  };
  win.CompanionProduct = {
    hosted: () => true,
    bridgeNow: () => ({apiGet: async () => ({status: "ok", data: statusPayload}), apiPost: async () => ({status: "ok", data: {}})}),
  };
  const inst = win.CompanionWorkbench.create({
    group: "test_layout",
    title: "测试分组布局",
    mount: () => host,
    endpoint: "/astrbot_plugin_private_companion/test",
    navigationLimit: 0,
    sections: [
      {id: "solo", title: "单项分组", help: "单字段说明", fields: ["solo_flag"]},
      {id: "atmos", title: "主开关领衔", help: "总开关加四子项", fields: ["master_on", "child_a", "child_b", "child_c", "child_d"]},
      {id: "trio", title: "三项对等开关", help: "三列对称", fields: ["peer_1", "peer_2", "peer_3"]},
      {id: "mixed", title: "混合字段分组", help: "主开关+文本域+三数字", fields: ["img_master", "img_cache", "img_max", "img_prompt", "img_a", "img_b", "img_c"]},
    ],
  });
  return inst.refresh().then(() => {
    const html = host.innerHTML;
    assert.match(html, /class="wb-group is-single-field" data-wb-group="solo"/);
    assert.match(html, /class="wb-group-count">1 项<\/span>/);
    assert.match(html, /class="wb-group-count">5 项<\/span>/);
    assert.match(html, /class="wb-field is-span-full is-master-field" data-wb-row="master_on"/);
    assert.match(html, /class="wb-field" data-wb-row="child_a"/);
    assert.match(html, /data-wb-group="trio"[\s\S]*?class="wb-fields" data-wb-grid="trio"/);
    assert.match(html, /class="wb-field is-span-full is-master-field" data-wb-row="img_master"/);
    assert.match(html, /class="wb-field is-span-full" data-wb-row="img_c"/);
    inst.unmount();
  });
});

test("source-checklist 与 identity-checklist 勾选变更后即时同步 .wb-actions.is-dirty", async () => {
  const vm = require("node:vm");
  const wbSource = fs.readFileSync(path.join(DIR, "workbench-page.js"), "utf8");
  const win = {addEventListener() {}, removeEventListener() {}};
  const ctx = vm.createContext({
    window: win,
    document: {documentElement: {dataset: {}}, addEventListener() {}, removeEventListener() {}},
    console, Intl, Date, JSON, Math, Number, Object, Array, String, Set, Map, Boolean, Error, Promise,
    Event: class Event { constructor(t) { this.type = t; } },
    CustomEvent: class CustomEvent { constructor(t, o) { this.type = t; this.detail = o?.detail; } },
    sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    setTimeout: () => 1, clearTimeout() {},
  });
  vm.runInContext(wbSource, ctx);

  function makeClassList(initial = []) {
    const set = new Set(initial);
    return {
      contains: c => set.has(c),
      toggle: (c, force) => { const on = force === undefined ? !set.has(c) : Boolean(force); if (on) set.add(c); else set.delete(c); return on; },
    };
  }
  const listeners = {};
  const noteEl = {textContent: "与已保存一致"};
  const resetBtn = {disabled: true};
  const actionsEl = {
    classList: makeClassList(["wb-actions"]),
    querySelector: sel => sel === ".wb-actions-note" ? noteEl : sel === '[data-wb-action="reset"]' ? resetBtn : null,
  };
  const host = {
    innerHTML: "",
    addEventListener: (t, fn) => { listeners[t] = fn; },
    removeEventListener: () => {},
    querySelector: sel => sel === ".wb-actions" ? actionsEl : null,
    querySelectorAll: () => [],
    closest: () => null,
  };
  const statusPayload = {
    edit_token: "b".repeat(64),
    scope: "role",
    layers: ["installation", "role"],
    settings: {kb_sources: "", target_identities: "[]", source_limit: 16},
    fields: {
      kb_sources: {kind: "text"},
      target_identities: {kind: "text"},
      source_limit: {kind: "integer"},
    },
    provenance: {},
  };
  win.CompanionProduct = {
    hosted: () => true,
    bridgeNow: () => ({apiGet: async () => ({status: "ok", data: statusPayload}), apiPost: async () => ({status: "ok", data: {}})}),
  };
  const inst = win.CompanionWorkbench.create({
    group: "test_checklists",
    title: "清单脏态同步测试",
    mount: () => host,
    endpoint: "/astrbot_plugin_private_companion/test-checklists",
    navigationLimit: 0,
    inputs: {kb_sources: "source-checklist", target_identities: "identity-checklist"},
    catalogs: {kb_sources: () => [{id: "kb1", name: "世界观", documents: []}], target_identities: () => [{id: "u1", label: "林夏"}]},
    sections: [{id: "s", title: "清单", fields: ["kb_sources", "target_identities"]}],
  });
  inst.mount();
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(inst.state.draft, "挂载刷新后应已加载配置草稿");
  assert.equal(actionsEl.classList.contains("is-dirty"), false);

  // 1. 勾选 source-checklist 复选框 -> .wb-actions.is-dirty 立即置 true
  const srcRow = {classList: makeClassList(["wb-field"])};
  const srcCount = {textContent: ""};
  const srcControl = {
    querySelectorAll: () => [],
    querySelector: sel => sel === ".wb-source-count" ? srcCount : null,
  };
  listeners.change({
    type: "change",
    target: {
      type: "checkbox",
      checked: true,
      dataset: {wbField: "kb_sources", wbSourceBase: "kb1"},
      closest: sel => sel === ".wb-source-picker" ? srcControl : sel === ".wb-field" ? srcRow : null,
    },
  });
  assert.equal(actionsEl.classList.contains("is-dirty"), true, "勾选知识源后保存条应立即标记 is-dirty");
  assert.equal(noteEl.textContent, "有未保存的改动");
  assert.equal(resetBtn.disabled, false);

  // 取消勾选回到原值 -> 恢复干净态
  listeners.change({
    type: "change",
    target: {
      type: "checkbox",
      checked: false,
      dataset: {wbField: "kb_sources", wbSourceBase: "kb1"},
      closest: sel => sel === ".wb-source-picker" ? srcControl : sel === ".wb-field" ? srcRow : null,
    },
  });
  assert.equal(actionsEl.classList.contains("is-dirty"), false, "取消勾选回到基线后保存条应取消 is-dirty");
  assert.equal(resetBtn.disabled, true);

  // 2. 勾选 identity-checklist 复选框 -> .wb-actions.is-dirty 立即置 true
  const idRow = {classList: makeClassList(["wb-field"])};
  const idCount = {textContent: ""};
  const checkedItem = {dataset: {wbIdentityId: "u1"}};
  const idControl = {
    querySelectorAll: sel => sel === "[data-wb-identity-id]:checked" ? [checkedItem] : [],
    querySelector: sel => sel === ".wb-source-count" ? idCount : null,
  };
  listeners.change({
    type: "change",
    target: {
      type: "checkbox",
      checked: true,
      dataset: {wbField: "target_identities", wbIdentityId: "u1"},
      closest: sel => sel === ".wb-identity-picker" ? idControl : sel === ".wb-field" ? idRow : null,
    },
  });
  assert.equal(actionsEl.classList.contains("is-dirty"), true, "勾选成员对象后保存条应立即标记 is-dirty");
  assert.equal(idCount.textContent, "已选 1 项");
  assert.equal(noteEl.textContent, "有未保存的改动");
  assert.equal(resetBtn.disabled, false);

  inst.unmount();
});
