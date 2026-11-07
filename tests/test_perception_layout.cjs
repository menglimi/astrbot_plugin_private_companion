/* 感知仪表板的窄屏布局。

   现场（2026-11-05，390px）：卡片文字竖成一列一个字。两个原因叠在一起：
   1. `.perception-board` 在语义样式里被重新声明成两列，位置排在前面那条
      `@media(max-width:900px)` 单列规则**之后**——同权重下源码顺序更晚者胜，
      手机上于是仍是两列，每张卡只剩 ~180px；
   2. 卡片内部的英雄/小卡把「状态徽标」放在文字右边，`36px 图标 + auto 徽标 +
      14px 箭头` 把 `minmax(0,1fr)` 的文字列压到 0 宽。
   这条测试不跑浏览器，只锁住两件事：单列规则必须排在双列声明之后；徽标必须
   落在第二行（grid-template-areas），而不是和文字抢第一行。
*/
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const CSS = fs.readFileSync(path.join(__dirname, "..", "pages", "陪伴小屋", "semantic-page.css"), "utf8");

test("窄屏单列规则排在两列声明之后，否则会被源码顺序盖掉", () => {
  const twoColumn = CSS.lastIndexOf(".perception-board{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))");
  const pattern = /@media\(max-width:900px\)\{\s*\.perception-board\{grid-template-columns:minmax\(0,1fr\)\}/g;
  const positions = [...CSS.matchAll(pattern)].map(match => match.index);
  assert.ok(twoColumn >= 0, "找不到 .perception-board 的两列声明");
  assert.ok(positions.length, "缺少 ≤900px 的 .perception-board 单列规则");
  assert.ok(Math.max(...positions) > twoColumn, "单列规则必须放在最后的双列声明之后");
});

test("英雄卡与小卡的徽标落到第二行，文字列不再被挤零", () => {
  for (const selector of [".perception-hero{", ".perception-mini{"]) {
    const start = CSS.indexOf(selector);
    assert.ok(start >= 0, `找不到 ${selector}`);
    const block = CSS.slice(start, CSS.indexOf("}", start));
    assert.match(block, /grid-template-areas:"icon copy chevron" "\. badge \."/,
      `${selector} 应把徽标放到第二行`);
    assert.match(block, /grid-template-columns:\d+px minmax\(0,1fr\) \d+px/,
      `${selector} 的文字列应是 minmax(0,1fr)，而不是被 auto 列吃掉`);
  }
});

test("小卡网格按最小列宽换行，而不是继续三等分", () => {
  assert.match(CSS, /\.perception-mini-grid\.is-three\{grid-template-columns:repeat\(auto-fit,minmax\(170px,1fr\)\)\}/);
  assert.match(CSS, /\.perception-mini-grid\.is-two\{grid-template-columns:repeat\(auto-fit,minmax\(180px,1fr\)\)\}/);
});
