/* 小屋首页的结构契约：清单式外壳，以及那些「写死的示例状态」不许回来。
 *
 * 用户对首页的要求是「继续拓展页建设」时定的：与管理页同一套语汇。
 * 首页此前是另一套——右栏三张 overview-card、生活观察三张同款卡、
 * 家具目录一张四列等宽卡片网格。三处都带 radius + shadow + border，
 * 与管理页的分隔线清单并存，同一个插件两种读法。
 *
 * 这个文件守三件事：
 *   1. 首页样式进得了页面（清单 → 合包 → 引用），且新 class 都有定义
 *   2. 首页不再渲染卡片壳（overview-card / furniture-card / home-summary-grid）
 *   3. directory 表里不再有写死的示例状态
 *
 * 第 3 条是这次真正抓到的缺陷：基础家具每件都挂着一条
 * `label: configSnapshot.base.dailyDiary ? '已开启' : '未启用'`，
 * 而 configSnapshot.base 恒为 {}（config-snapshot.js 里就是 `base:{}`，
 * 发行版快照没有这些字段）。于是七件基础家具永远显示「未启用」；
 * 另有两件挂着写死的示例词——'示例记录'、'示例照片'。用户看到的是假状态。
 *
 * 首页样式自 7.0.16 起走合包：index.html 只引用一张 bundle.css，源文件是否
 * 进页面由 css-sources.json 的清单决定，tools/sync_framework_page.py 按清单
 * 顺序把每张表拼进去，并在每段前留一行以文件名命名的注释。于是
 * 「首页有样式」是条三段的链——清单里有 home.css、合包里含它的规则、页面
 * 引用 bundle.css。任何一段断了，首页的 .home-band / .home-row / .home-item
 * 都没有样式；而预览构建器按同样的引用列表内联，本地看起来一样「正常」，
 * 目视检查发现不了。这条链就是下面第一个用例守的东西。
 *
 * 用 node --test 运行。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const DIR = path.join(__dirname, "..", "pages", "陪伴小屋");
const read = name => fs.readFileSync(path.join(DIR, name), "utf8");
const withoutComments = source => source.replace(/\/\*[\s\S]*?\*\//g, "");

/* 一张样式表里出现过的选择器原文。注释先剥掉——注释里提到的类名不算定义。 */
const selectorsOf = source => {
  const out = new Set();
  for (const match of withoutComments(source).matchAll(/([^{}]+)\{/g)) {
    for (const part of match[1].split(",")) {
      const selector = part.trim();
      if (selector) out.add(selector);
    }
  }
  return out;
};

const INDEX = read("index.html");
const APP = withoutComments(read("app.js"));
const PRODUCT = withoutComments(read("product-page.js"));
const BUNDLE = read("bundle.css");
const CSS_SOURCES = JSON.parse(read("css-sources.json"));

const linkedStyles = () => [...INDEX.matchAll(
  /<link\s+rel="stylesheet"\s+href="([^"?]+)[^"]*"/g)].map(m => m[1]);

/* 合包按「以文件名命名的注释行」分段；取同名段落。段落边界由构建脚本决定，
   所以这里跟着同一份约定读，不另立一套解析。 */
function bundleSection(name) {
  const sections = new Map();
  let previous = null;
  for (const match of BUNDLE.matchAll(/^\/\* ([^ ]+\.css) \*\/$/gm)) {
    if (previous) sections.set(previous.name, BUNDLE.slice(previous.end + 1, match.index));
    previous = { name: match[1], end: match.index + match[0].length };
  }
  return sections.get(name);
}

test("首页样式表被 index.html 引用", () => {
  /* 与管理页 console.css 那次同一个理由：漏引时首页整片没有结构样式，
     而预览构建器按同样的引用列表内联，所以本地看起来也「一致」，
     任何目视检查都发现不了。合包之后 home.css 不再是单独的 link，
     直接比文件名只会把已经生效的样式判成缺失，所以这里认合包那一条。 */
  assert.ok(linkedStyles().includes("bundle.css"),
    "index.html 没有引用 bundle.css：首页一张样式表都没有加载");
});

test("home.css 在合包清单里，且规则一条不少地进了 bundle.css", () => {
  /* 合包把「漏引」换了个样子：把 home.css 从 css-sources.json 里删掉、
     或者改了 home.css 却忘了重新构建，页面都会静默少掉一整段样式。
     两种情况都要红，所以三段分开断言。 */
  assert.ok(CSS_SOURCES.includes("home.css"),
    "css-sources.json 里没有 home.css：重新构建后首页样式会整段消失");
  const section = bundleSection("home.css");
  assert.ok(section,
    "bundle.css 里找不到 home.css 段落：运行 python -X utf8 tools/sync_framework_page.py 重新生成");
  /* 关键选择器点名，失败时一眼看出丢的是哪一块。 */
  for (const selector of [".home-band", ".home-row", ".home-item", ".voxel-fallback-actions"]) {
    assert.ok(section.includes(selector), `bundle.css 的 home.css 段落里没有 ${selector}`);
  }
  /* 剩下的逐条核对：改了 home.css 没重新构建时，新选择器不会出现在合包里。 */
  const missing = [...selectorsOf(read("home.css"))].filter(sel => !section.includes(sel));
  assert.deepStrictEqual(missing, [],
    `home.css 有规则没进 bundle.css（改完样式忘了重新构建？）：${missing.slice(0, 5).join("、")}`);
});

test("首页用到的 class 都真的被定义了", () => {
  const linked = linkedStyles();
  const defined = new Set();
  for (const name of linked) {
    const source = withoutComments(read(name));
    for (const m of source.matchAll(/([^{}]+)\{/g)) {
      for (const part of m[1].split(",")) {
        const sel = part.trim();
        if (sel) defined.add(sel);
      }
    }
    // 形状 token 之类只出现在媒体查询里的选择器也一并收
    for (const m of source.matchAll(/\.([a-z][a-z0-9-]*)/g)) defined.add("." + m[1]);
  }
  /* 只查首页专有的前缀。通用 button / pill 散落在别处，不在这里重复断言。 */
  const used = new Set();
  for (const source of [APP, PRODUCT]) {
    for (const m of source.matchAll(/class="([^"]+)"/g)) {
      for (const name of m[1].split(/\s+/)) {
        if (/^(home-|room-doors|room-bar|room-foot|voxel-fallback)/.test(name)) used.add(name);
      }
    }
  }
  assert.ok(used.size >= 10, `没抓到首页的 class，选择器变了？抓到 ${used.size} 个`);
  const missing = [...used].filter(name => !defined.has("." + name));
  assert.deepStrictEqual(missing, [],
    `这些 class 被首页用到了但没有任何已加载的样式表定义：${missing.join("、")}`);
});

test("voxel-fallback-actions 有样式", () => {
  /* 体素小屋加载失败时的两个按钮容器。app.js 渲染它，但没有一张表定义过它——
     与管理页 console.css 漏引是同一个毛病，只是范围小到没人报过。 */
  const source = withoutComments(read("home.css"));
  assert.match(source, /\.voxel-fallback-actions\s*\{/,
    ".voxel-fallback-actions 又变成无样式的容器了");
});

test("首页不恢复旧的嵌套卡片壳", () => {
  /* 分区保留分隔线，避免恢复旧的等宽嵌套卡片。
     用户指定的右栏心情、位置与忙碌摘要允许使用紧凑卡片。 */
  for (const [name, source] of [["app.js", APP], ["product-page.js", PRODUCT]]) {
    for (const cls of ["overview-card", "furniture-card", "home-summary-grid",
                       "home-section-heading", "section-eyebrow", "furniture-grid"]) {
      assert.doesNotMatch(source, new RegExp('[\\s"]' + cls + '[\\s"]'),
        `${name} 里又出现了卡片壳 class「${cls}」——首页已经改成清单式`);
    }
  }
});

test("首页分区与家具清单维持分隔线", () => {
  /* 同 console.css 那条：分区之间用分隔线，不限制状态摘要的独立卡片。 */
  const source = withoutComments(read("home.css"));
  const band = source.match(/\.home-band\{([\s\S]*?)\n\}/);
  assert.ok(band, "home.css 里找不到 .home-band");
  assert.doesNotMatch(band[1], /border:\s*1px solid/,
    ".home-band 又画上整圈边框了：分区应当用 border-top 分隔线");
  assert.doesNotMatch(band[1], /box-shadow/,
    ".home-band 又加了阴影：那是卡片语汇");
  const item = source.match(/\.home-item\{([\s\S]*?)\n\}/);
  assert.ok(item, "home.css 里找不到 .home-item");
  assert.doesNotMatch(item[1], /border-radius/,
    ".home-item 又画成卡片了：家具目录是清单行，不是卡");
});

test("首页说明限宽，不会拉成通栏", () => {
  const source = withoutComments(read("home.css"));
  for (const name of ["home-band-note", "home-row-main small", "home-note"]) {
    const key = name.includes(" ") ? name.split(" ")[0] : name;
    const rule = source.match(new RegExp("\\." + key + "([^{}]*)\\{([\\s\\S]*?)\\}"));
    assert.ok(rule, `home.css 里找不到 .${key}`);
    const all = [...source.matchAll(new RegExp("\\." + key + "([^{}]*)\\{([^}]*)\\}", "g"))]
      .map(m => m[2]).join("\n");
    assert.match(all, /max-width:\s*\d+ch/,
      `.${key} 没有限宽：说明会拉成通栏，读起来一眼看不到头`);
  }
});

test("directory 表里没有写死的示例状态", () => {
  /* 这次真正抓到的缺陷。基础家具的状态标签此前读 configSnapshot.base.*，
     那份快照恒为 {}，于是七件永远显示「未启用」；另有两件写着
     '示例记录' 与 '示例照片'。三类都是假状态，用户拿不到真信息。 */
  const m = APP.match(/const directory\s*=\s*\[/);
  assert.ok(m, "找不到 directory 表");
  let depth = 0, end = m.index;
  for (let k = m.index + m[0].length - 1; k < APP.length; k += 1) {
    if (APP[k] === "[") depth += 1;
    else if (APP[k] === "]") { depth -= 1; if (depth === 0) { end = k + 1; break; } }
  }
  const block = APP.slice(m.index, end);
  assert.doesNotMatch(block, /示例/, "directory 表里又有「示例」字样了——那是假状态");
  assert.doesNotMatch(block, /configSnapshot/,
    "directory 表又去读 config-snapshot 了：那份快照的 base 恒为 {}，读它只会得到「未启用」");
});

test("家具状态只有一个判据来源", () => {
  /* 家具能不能用，只由扩展自报状态与本地「移出房间」决定。
     新增判据必须走 furnitureUnavailable()，不要在别处另写一份。 */
  assert.match(APP, /function furnitureUnavailable\(item\)/,
    "找不到 furnitureUnavailable()：家具可用性的唯一判据没了");
  assert.match(APP, /function furnitureStatus\(item\)/,
    "找不到 furnitureStatus()：家具状态标签的唯一出口没了");
});
