/* 页面样式表必须真的被引用，并且真的定义了管理页用到的 class。
 *
 * 用户贴的决策页截图：一片同字号同灰度的字从左上角一路铺下来，
 * 标题、说明、控件分不出层次。根因不是配色也不是间距，而是——
 *
 *   console.css 定义了 .console / .console-section / .console-section-head /
 *   .console-row / .console-mount-head 等管理页的**全部**结构样式，
 *   而 index.html 从来没引用过它。
 *
 * navigation.css 里留着一句「配置页外壳已迁到 console.css（.console /
 * .console-section / .boundary）」——迁移做了一半就断了，引用那一步没做。
 * 于是那一屏十几处 class 一条规则都没有，渲染出来是浏览器默认样式堆叠的
 * 裸 HTML：<h2>、<p>、<div> 按默认边距依次排下去，看起来就是「一坨文字」。
 *
 * 为什么长期没被发现：预览构建器（tools/build_page_preview.py）按
 * **同样的引用列表**收集样式并内联。漏引时预览也无样式，和真实页面表现
 * 一致，所以「本地看过了」并不构成任何验证。这条测试就是补这个缺口。
 *
 * 7.0.16 起页面改成一张合包：tools/sync_framework_page.py 按
 * css-sources.json 的顺序把源表拼成 bundle.css，每张表前留一行以文件名
 * 命名的注释，index.html 只留 <link ... href="bundle.css?v=...">。于是
 * 「管理页有样式」从「有一条 console.css 的 link」变成三段链：
 *
 *   清单里有 console.css  →  合包里含它的规则  →  页面引用 bundle.css
 *
 * 任何一段断了，.console-* 结构样式都会整段失效，症状和当年漏引一样。
 * 只断言那条直接 link 会把已经生效的样式判成缺失（这正是 7.0.16 之后
 * 本文件一直红着的原因），所以下面按链逐段核对。
 *
 * 用 node --test 运行。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const DIR = path.join(__dirname, "..", "pages", "陪伴小屋");
const INDEX = fs.readFileSync(path.join(DIR, "index.html"), "utf8");
const APP = fs.readFileSync(path.join(DIR, "app.js"), "utf8");
const BUNDLE = fs.readFileSync(path.join(DIR, "bundle.css"), "utf8");
const CSS_SOURCES = JSON.parse(fs.readFileSync(path.join(DIR, "css-sources.json"), "utf8"));

/* 剥掉注释再比对选择器——注释里出现某个类名不算「有样式」。 */
const withoutComments = source => source.replace(/\/\*[\s\S]*?\*\//g, "");

const linkedStyles = () => [...INDEX.matchAll(
  /<link\s+rel="stylesheet"\s+href="([^"?]+)[^"]*"/g)].map(m => m[1]);

/* 合包按「以文件名命名的注释行」分段，段边界由构建脚本决定；这里跟着
   同一份约定读，不另立一套解析。 */
function bundleSection(name) {
  const sections = new Map();
  let previous = null;
  for (const m of BUNDLE.matchAll(/^\/\* ([^ ]+\.css) \*\/$/gm)) {
    if (previous) sections.set(previous.name, BUNDLE.slice(previous.end + 1, m.index));
    previous = { name: m[1], end: m.index + m[0].length };
  }
  return sections.get(name);
}

function selectorsOf(name) {
  const source = withoutComments(fs.readFileSync(path.join(DIR, name), "utf8"));
  const out = new Set();
  for (const m of source.matchAll(/([^{}]+)\{/g)) {
    for (const part of m[1].split(",")) {
      const trimmed = part.trim();
      if (trimmed) out.add(trimmed);
    }
  }
  return out;
}

test("console.css 经合包进入页面，而不是靠一条直接 link", () => {
  /* 这一条是整个文件存在的理由：三段链任何一段断了，管理页都没有结构
     样式，而预览页会以同样的方式缺样式，所以任何本地检查都发现不了。 */
  assert.ok(linkedStyles().includes("bundle.css"),
    "index.html 没有引用 bundle.css：管理页的 .console-* 结构样式全部失效，"
    + "十几个配置页会渲染成浏览器默认样式的裸 HTML");
  assert.ok(CSS_SOURCES.includes("console.css"),
    "css-sources.json 里没有 console.css：重新构建后管理页结构样式会整段消失");
  const section = bundleSection("console.css");
  assert.ok(section,
    "bundle.css 里找不到 console.css 段落：运行 "
    + "python -X utf8 tools/sync_framework_page.py 重新生成");
  /* 关键选择器点名，失败时一眼看出丢的是哪一块。 */
  for (const selector of [".console", ".console-section", ".console-section-head",
                          ".console-row", ".console-mount-head"]) {
    assert.ok(section.includes(selector),
      `bundle.css 的 console.css 段落里没有 ${selector}`);
  }
  /* 剩下的逐条核对：改了 console.css 却没重新构建时，新选择器不会出现在
     合包里；整段样式就这样静默地少掉。 */
  const missing = [...selectorsOf("console.css")].filter(sel => !section.includes(sel));
  assert.deepStrictEqual(missing, [],
    `console.css 有规则没进 bundle.css（改完样式忘了重新构建？）：${missing.slice(0, 5).join("、")}`);
});

test("所有被引用的样式表都存在", () => {
  for (const name of linkedStyles()) {
    assert.ok(fs.existsSync(path.join(DIR, name)),
      `index.html 引用了不存在的样式表 ${name}`);
  }
});

test("管理页用到的 class 都真的被某张已加载的表定义了", () => {
  /* 反向守：将来有人把 .console-row 之类挪进一张没被引用的表里，
     或者新加 class 忘了写样式，这条会红。
     只查 console-* / config-editors 这类管理页专有的前缀——
     通用 class（button / pill）散落在别处，不在这里重复断言。 */
  const loaded = linkedStyles().map(selectorsOf);
  const used = new Set();
  for (const m of APP.matchAll(/class="([^"]+)"/g)) {
    for (const name of m[1].split(/\s+/)) {
      if (/^(console-|config-editor)/.test(name)) used.add(name);
    }
  }
  assert.ok(used.size > 5, `没抓到管理页的 class，选择器变了？抓到 ${used.size} 个`);
  const missing = [...used].filter(name =>
    !loaded.some(set => [...set].some(sel => sel.includes("." + name))));
  assert.deepStrictEqual(missing, [],
    `这些 class 被用到了但没有任何已加载的样式表定义它们：${missing.join("、")}`);
});

test("console.css 里没有残留的卡片边框", () => {
  /* 分区之间用分隔线（border-top），不用卡片边框（border + radius + shadow）。
     卡片会让人以为是可点的对象，而且一层套一层之后整屏都是等宽的框。
     这条拦住回退到「卡片墙」。 */
  const source = withoutComments(fs.readFileSync(path.join(DIR, "console.css"), "utf8"));
  const section = source.match(/\.console-section\{([\s\S]*?)\n\}/);
  assert.ok(section, "console.css 里找不到 .console-section");
  assert.doesNotMatch(section[1], /border:\s*1px solid/,
    ".console-section 又画上整圈边框了：分区应当用 border-top 分隔线");
  assert.doesNotMatch(section[1], /box-shadow/,
    ".console-section 又加了阴影：那是卡片语汇，不是清单语汇");
});

test("分区说明限宽，不会拉成通栏", () => {
  /* 说明句不限宽时会在宽屏上拉成一整行、又变回「一坨文字」。
     62ch 是刻意选的：中文按字符宽度折算约 60 字上下，正好一行半以内。 */
  const source = withoutComments(fs.readFileSync(path.join(DIR, "console.css"), "utf8"));
  for (const name of ["console-section-note", "console-mount-note"]) {
    const rule = source.match(new RegExp("\\." + name + "\\{([\\s\\S]*?)\\}"));
    assert.ok(rule, `console.css 里找不到 .${name}`);
    assert.match(rule[1], /max-width:\s*\d+ch/,
      `.${name} 没有限宽：说明会拉成通栏，读起来一眼看不到头`);
  }
});
