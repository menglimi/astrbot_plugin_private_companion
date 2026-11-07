/* 主动与联系页的样式契约：版式骨架、开关、以及动效的三条硬规则。
 *
 * 为什么样式也要有测试：这一页的 CSS 里有三类东西**坏了也不报错**——
 *   ① 写了 @keyframes 却没人引用（规则是死的，比如改版前的 ct-shift：
 *      JS 把类加在即将被 innerHTML 覆盖的节点上，动画一帧都没跑过）；
 *   ② 新加的动画没进 prefers-reduced-motion 块（对晕动症用户就是没做）；
 *   ③ var(--t-xxx) 写错一个字母（浏览器静默用继承值，颜色"看起来还行"）。
 * 三类都只能靠断言发现，肉眼看页面是看不出来的。
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
const squash = text => text.replace(/\s+/g, " ").trim();

const INDEX = read("index.html");
const CSS = read("contact-page.css");
const RAW_CSS = withoutComments(CSS);
const JS = read("contact-page.js");
const {assertStylesheetBundled} = require("./page-styles.cjs");
const REDUCED_AT = RAW_CSS.indexOf("@media (prefers-reduced-motion:reduce)");
const REDUCED = REDUCED_AT < 0 ? "" : RAW_CSS.slice(REDUCED_AT);

/* 把样式表切成「声明块」：跳过 @keyframes 里的 from/to/58% 步骤，
   它们不是选择器，也不该被要求出现在减弱动效块里。 */
function rules(source) {
  const found = [];
  for (const match of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = squash(match[1]);
    if (!selector || selector.startsWith("@")) continue;
    found.push({selector, body: match[2], at: match.index});
  }
  return found;
}
const animationName = body => {
  const declaration = /(?:^|;)\s*animation\s*:\s*([^;]+)/.exec(body);
  if (!declaration) return null;
  const first = squash(declaration[1]).split(" ")[0];
  /* `animation:ct-breathe 2.6s ...` 的第一段是名字；`animation:2.6s ...` 不是。 */
  return /^[a-z][a-z0-9-]*$/i.test(first) ? first : null;
};

test("样式表与脚本都被引用，并带缓存串", () => {
  /* 漏引的表现是「页面还有结构，只是丑」——预览页按同一份引用列表内联，
     所以本地看起来也一致，目视检查发现不了。样式表现在由 bundle.css 承载，
     脚本仍然逐个加载。 */
  assertStylesheetBundled("contact-page.css");
  assert.match(INDEX, /src="contact-page\.js\?v=[\w.-]+"/, "index.html 没有引用 contact-page.js");
});

test("每个 @keyframes 都被引用，每个被引用的动画都有定义", () => {
  const defined = new Set([...RAW_CSS.matchAll(/@keyframes\s+([a-z0-9-]+)/gi)].map(m => m[1]));
  const used = new Set();
  for (const rule of rules(RAW_CSS)) {
    const name = animationName(rule.body);
    if (name) used.add(name);
  }
  for (const name of defined) {
    assert.ok(used.has(name), "@keyframes " + name + " 没有任何规则引用它：这是一条死动画");
  }
  for (const name of used) {
    assert.ok(defined.has(name), "动画 " + name + " 没有 @keyframes 定义");
  }
});

test("每一条动画都在减弱动效块里被压到瞬时", () => {
  /* 约定是 animation-duration:.001ms 而不是 animation:none：
     关掉动画会让等待 transitionend 的逻辑永远不结算。
     新加动画时忘了登记，就是漏了一个用户群——所以这条按规则逐条比，
     而不是维护一份「记得登记的动画清单」。
     比对的是**归零那条规则的选择器表**，不是整块文本：
     `.ct-dial.is-entering` 在块里还出现过一次（复位 opacity/transform），
     用整块文本做子串匹配的话，把它从时长归零那行删掉也照样通过。 */
  assert.ok(REDUCED, "contact-page.css 里找不到 prefers-reduced-motion 块");
  const neutralized = new Set();
  for (const rule of rules(RAW_CSS)) {
    if (rule.at < REDUCED_AT || !rule.body.includes("animation-duration")) continue;
    for (const part of rule.selector.split(",")) neutralized.add(squash(part));
  }
  assert.ok(neutralized.size >= 10, "减弱动效块里的选择器太少，这个检查可能没在读真正的样式表");
  const animated = rules(RAW_CSS).filter(rule => rule.at < REDUCED_AT && animationName(rule.body));
  assert.ok(animated.length >= 10, "动画规则少于预期，这个检查可能没在读真正的样式表");
  for (const rule of animated) {
    assert.ok(neutralized.has(rule.selector),
      "动画规则 " + rule.selector + " 没有进 prefers-reduced-motion 的时长归零列表");
  }
  assert.ok(REDUCED.includes("animation-duration:.001ms !important"), "减弱动效块要用时长归零的写法");
  assert.ok(REDUCED.includes("animation-delay:0s !important"),
    "延迟也要归零：涟漪与问候弧最长延到 ~312ms，只压时长会先空白再突然出现");
  assert.ok(!/animation\s*:\s*none/.test(REDUCED), "减弱动效块不能用 animation:none");
});

test("开页涟漪是一次性的、按环形距离错峰的波", () => {
  const ripple = rules(RAW_CSS).find(rule => rule.body.includes("ct-bar-ripple"));
  assert.ok(ripple, "找不到柱子的涟漪动画");
  assert.ok(/animation-delay:calc\(var\(--i/.test(ripple.body),
    "涟漪要按每根柱子的 --i 错峰，否则 24 根一起亮，看着是闪烁不是涟漪");
  assert.ok(!/infinite/.test(ripple.body), "开页涟漪只能跑一次");
  /* --i 由 JS 逐根给出（到 0:00 的环形距离），这里只确认它真的在写。
     写成下标的话波前只有一个方向，是「逐根加载」不是涟漪。 */
  assert.ok(JS.includes("--i:${Math.min(hour, 24 - hour)}"), "柱子要拿到环形距离作为 --i");
  assert.ok(JS.includes("style=\"--a:${index}\""), "三条问候弧要拿到序号作为 --a");
});

test("盘面变化只闪改动的那一圈，且类由渲染给出", () => {
  /* 改版前 ct-shift 是加在**即将被 innerHTML 覆盖**的节点上的，
     所以那条规则从没跑过。类必须由渲染时的状态决定。 */
  assert.ok(JS.includes("is-pulse-"), "盘面变化要用 is-pulse-* 类");
  assert.ok(!/classList\.add\("is-shifting"\)/.test(JS),
    "不能再在 render() 之前往 DOM 加动画类：那一节点马上就被换掉了");
  for (const part of ["quiet", "curve", "greet", "cursor", "center", "tier"]) {
    assert.ok(CSS.includes(".ct-dial-stage.is-pulse-" + part),
      "盘面的 " + part + " 这一片没有对应的变化动效");
  }
});

test("开关保留真复选框、焦点环与未保存标记", () => {
  /* 三条都是可访问性/状态契约，不随画法改动：
     真 checkbox 决定键盘与读屏能不能用，:focus-visible 决定看不看得见焦点，
     is-dirty 决定用户知不知道这一项还没保存。 */
  assert.ok(/\.ct-switch input:focus-visible \+ \.ct-switch-track\{/.test(RAW_CSS),
    "开关要有键盘焦点环");
  assert.ok(RAW_CSS.includes(".ct-switch input:checked + .ct-switch-track"),
    "开关的开启态要由 :checked 驱动（真复选框）");
  const dirty = rules(RAW_CSS).find(rule => rule.selector.includes(".ct-switch.is-dirty"));
  assert.ok(dirty && dirty.body.includes("var(--t-caution)"), "未保存标记要覆盖开关");
  assert.ok(JS.includes("type=\"checkbox\" data-field="), "开关必须还是真复选框");
});

test("触发源的行版式与主配置区是两套，不共用竖排字段", () => {
  /* 触发源用「标签在左、控件在右」的紧凑行；主配置区是竖排字段。
     混用会让某一行悄悄退回竖排，而两套版式各自都「没错」。 */
  assert.ok(CSS.includes(".ct-src-row{"), "触发源要有一行一件事的版式");
  assert.ok(CSS.includes(".ct-src-group.is-wide"), "条目多的组要能占满整行");
  assert.ok(JS.includes('data-kind="toggle"'), "开关行要标出种类，卡片头部的计数按它数");
  assert.ok(JS.includes("function srcToggle") && JS.includes("function srcNumber"),
    "触发源要有自己的一套行控件");
});

test("引用到的主题令牌都在 tokens.css 里有定义", () => {
  /* 写错一个字母不会报错：var() 静默失效，颜色退回继承值，
     在某一套主题下"看着还行"，换主题才崩。 */
  const tokens = withoutComments(read("tokens.css"));
  const defined = new Set([...tokens.matchAll(/(--t-[a-z0-9-]+)\s*:/g)].map(m => m[1]));
  const used = new Set([...RAW_CSS.matchAll(/var\((--t-[a-z0-9-]+)/g)].map(m => m[1]));
  assert.ok(used.size > 20, "引用到的令牌太少，这个检查可能没在读真正的样式表");
  for (const name of used) {
    assert.ok(defined.has(name), "contact-page.css 引用了未定义的主题令牌 " + name);
  }
});
