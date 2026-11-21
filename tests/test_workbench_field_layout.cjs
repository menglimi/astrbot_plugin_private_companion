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
  const autoFit = fields.match(/grid-template-columns:\s*repeat\(auto-fit,\s*minmax\((\d+)px/);
  assert.ok(autoFit, ".wb-fields 需要 repeat(auto-fit,minmax(Npx,1fr)) 才能在宽屏并排、窄屏回落");
  const minimum = Number(autoFit[1]);
  /* 字段行是「标签在左、控件在右」的双栏，一列至少要有 12em 标签 + 180px
     控件的位置；低于 420 会把标签折成两行，高于 640 又退回一行一个字段。 */
  assert.ok(minimum >= 420 && minimum <= 640,
    `列宽下限 ${minimum}px 不在合理区间：太小会把标签挤成两行，太大又退回一行一个字段`);
  assert.doesNotMatch(fields, /flex-direction:\s*column/,
    ".wb-fields 又变成纵向堆叠了：这正是本次修复要消掉的那个形状");
});

test("字段保留层轨、主体与操作三列，范围说明按需显示", () => {
  assert.match(rule(".wb-field"), /display:\s*grid/);
  /* 第三列是固定的「恢复继承」槽位：按 auto 排时，只有被覆盖的行有按钮，
     同一列的控件右边缘会相差一个按钮宽，看上去一左一右。 */
  assert.match(rule(".wb-field"), /grid-template-columns:\s*12px\s+minmax\(0,\s*1fr\)\s+64px/);
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
  for (const needed of ["textarea", ".wb-word-editor", ".wb-order-editor"]) {
    assert.ok(wide.includes(needed),
      `长控件 ${needed} 没有通栏规则：多行文本/关键词/发送顺序会被塞进一列里`);
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
