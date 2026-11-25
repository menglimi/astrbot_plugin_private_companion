"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.join(__dirname, "..");
const PAGE = path.join(ROOT, "pages", "陪伴小屋");
const HOST = "astrbot_plugin_private_companion";
const PREFIX = `/${HOST}/`;
const reviewSource = fs.readFileSync(path.join(PAGE, "learning-review-page.js"), "utf8");
const hubCss = fs.readFileSync(path.join(PAGE, "learning-hub.css"), "utf8");
const samplesSource = fs.readFileSync(path.join(PAGE, "expression-samples-page.js"), "utf8");
const flush = () => new Promise(resolve => setTimeout(resolve, 0));

const SOURCE = {
  key: "role-a",
  label: "私聊 · 角色 A",
  growthApi: `${PREFIX}targets/profile-a/roles/role-a/growth`,
  replyApi: `${PREFIX}targets/profile-a/roles/role-a/reply`,
};

function createHarness({growth = [], style = [], self = []} = {}) {
  const bridge = {
    apiGet: async route => ({
      status: "ok",
      data: {
        items: route.endsWith("/progress") ? growth
          : route.endsWith("/style") ? style
            : self,
        next_cursor: null,
      },
    }),
    apiPost: async () => ({status: "ok", data: {}}),
  };
  const rootListeners = new Map();
  const windowListeners = new Map();
  let queueListNode = {scrollTop: 0};
  let queueOuterNode = {scrollTop: 0};
  const scrolledKeys = [];
  let openDialog = false;

  const root = {
    isConnected: true,
    _html: "",
    ownerDocument: {
      querySelector: selector => (selector === "dialog[open]" && openDialog ? {} : null),
    },
    get innerHTML() { return this._html; },
    set innerHTML(value) {
      this._html = value;
      queueListNode = {scrollTop: 0};
      queueOuterNode = {scrollTop: 0};
    },
    addEventListener(name, cb) { rootListeners.set(name, cb); },
    removeEventListener(name) { rootListeners.delete(name); },
    querySelector(selector) {
      if (selector === ".lh-review-queue-list") return queueListNode;
      if (selector === ".lh-review-queue") return queueOuterNode;
      return null;
    },
    querySelectorAll(selector) {
      if (selector !== "[data-review-select]") return [];
      const matches = [...this._html.matchAll(/data-review-select="([^"]+)"/g)];
      return matches.map(match => {
        const key = match[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&");
        const wrap = {scrollIntoView: opts => scrolledKeys.push({key, block: opts?.block})};
        return {
          dataset: {reviewSelect: key},
          closest: probe => (probe === ".lh-review-row-wrap" ? wrap : null),
        };
      });
    },
  };

  const win = {
    CompanionProduct: {bridgeNow: () => bridge, unwrap: value => value},
    lucide: {createIcons() {}},
    addEventListener(name, cb) { windowListeners.set(name, cb); },
    removeEventListener(name) { windowListeners.delete(name); },
  };
  const context = vm.createContext({
    window: win, setTimeout, clearTimeout, Date, Math, Number, Object, Array, String, Set, Map,
  });
  vm.runInContext(reviewSource, context);
  const page = context.window.CompanionLearningReviewPage;

  async function mount() {
    page.mount(root, {sources: [SOURCE]});
    for (let attempt = 0; attempt < 80 && page.state?.loading; attempt += 1) await flush();
  }
  const click = (selector, dataset = {}) => rootListeners.get("click")?.({
    target: {closest: probe => (probe === selector ? {dataset} : null)},
  });
  const changeCheck = (key, checked = true) => rootListeners.get("change")?.({
    target: {
      checked,
      closest: probe => (probe === "[data-review-check]" ? {checked, dataset: {reviewCheck: key}} : null),
    },
  });
  const keydown = (key, extra = {}) => {
    let prevented = false;
    windowListeners.get("keydown")?.({
      key,
      metaKey: false,
      ctrlKey: false,
      altKey: false,
      defaultPrevented: false,
      target: {tagName: "BODY", isContentEditable: false, getAttribute: () => null, closest: () => null},
      preventDefault() { prevented = true; },
      ...extra,
    });
    return prevented;
  };
  return {
    page, root, rootListeners, windowListeners, scrolledKeys,
    get queueList() { return queueListNode; },
    get queueOuter() { return queueOuterNode; },
    setOpenDialog(value) { openDialog = value; },
    mount, click, changeCheck, keydown,
  };
}

test("审阅中心修正错别字与分类文案，去除「风格采拈 / 进度模式 / 用户认知 / 摘要」", async () => {
  const now = "2026-11-20T10:00:00+08:00";
  const h = createHarness({
    growth: [{
      review_id: "g1", growth_title: "规律作息", progress_note: "连续三天早睡", base_revision: 1,
      created_at: now, actions: ["accept", "reject"], view_token: "tg1", evidence: ["作息回执"],
    }],
    style: [{
      signal_id: "s1", dimension: "diction", source_kind: "conversation_input",
      proposal: "用温和短句安抚", observation: "对方疲惫时短句更有效", confidence: 0.78,
      expression: {kind: "style", pattern: "先歇会儿吧", situation: "对方表达累了时", avoid: "严肃讨论时不用"},
      created_at: now, actions: ["adopt", "reject"], view_token: "ts1", evidence: ["历史回复"],
    }, {
      signal_id: "s2", dimension: "formatting", source_kind: "shared_pack",
      proposal: "分点归纳", observation: "复杂问题分点更清楚", confidence: 0.85,
      expression: {kind: "grammar", pattern: "先说结论，再补两句原因", situation: "解释复杂安排时"},
      created_at: "2026-11-20T09:00:00+08:00", actions: ["adopt", "reject"], view_token: "ts2", evidence: [],
    }, {
      signal_id: "s3", dimension: "sentence_rhythm", source_kind: "legacy_expression",
      proposal: "缩短句子节奏", observation: "短句更有呼吸感", confidence: 0.72,
      created_at: "2026-11-20T08:00:00+08:00", actions: ["adopt", "reject"], view_token: "ts3", evidence: [],
    }],
    self: [{
      item_id: "r1", kind: "ask_user", observation: "是否保留晚安提醒", next_action: "向对方确认提醒时间",
      created_at: "2026-11-20T07:00:00+08:00", actions: ["accept", "reject"], view_token: "tr1", evidence: ["复盘摘要"],
    }],
  });
  await h.mount();
  const html = h.root.innerHTML;
  assert.match(html, /data-review-filter="growth"[^>]*>进展核实<span>1<\/span>/);
  assert.match(html, /data-review-filter="style"[^>]*>风格采纳<span>3<\/span>/);
  assert.match(html, /data-review-filter="self"[^>]*>复盘确认<span>1<\/span>/);
  assert.doesNotMatch(html, /风格采拈|进度模式|用户认知|>摘要</);
  assert.match(html, /lh-review-type is-growth">进展核实/);
  assert.match(html, /lh-review-type is-style">情境表达/);
  assert.match(html, /lh-review-type is-style">语法句式/);
  assert.match(html, /lh-review-type is-style">局部节奏/);
  assert.match(html, /lh-review-type is-self">复盘确认/);
  h.page.unmount();
});

test("来源标签按记录类型准确区分，详情区仅在元信息条展示一次不重复", async () => {
  const h = createHarness({
    growth: [{
      review_id: "g1", growth_title: "成长条目", progress_note: "进展说明", base_revision: 1,
      created_at: "2026-11-20T12:00:00+08:00", actions: ["accept", "reject"], view_token: "t1", evidence: [],
    }],
    self: [{
      item_id: "r1", kind: "ask_user", observation: "复盘条目", next_action: "确认安排",
      created_at: "2026-11-20T11:00:00+08:00", actions: ["accept", "reject"], view_token: "t2", evidence: [],
    }],
    style: [
      {signal_id: "s-conv", source_kind: "conversation_input", proposal: "来自对话", created_at: "2026-11-20T10:00:00+08:00", actions: ["adopt", "reject"], view_token: "t3", evidence: []},
      {signal_id: "s-pack", source_kind: "shared_pack", source_title: "示例包", proposal: "来自分享包", created_at: "2026-11-20T09:00:00+08:00", actions: ["adopt", "reject"], view_token: "t4", evidence: []},
      {signal_id: "s-leg", source_kind: "legacy_expression", proposal: "来自旧版", created_at: "2026-11-20T08:00:00+08:00", actions: ["adopt", "reject"], view_token: "t5", evidence: []},
      {signal_id: "s-def", source_kind: "confirmed_reply", proposal: "来自角色回复", created_at: "2026-11-20T07:00:00+08:00", actions: ["adopt", "reject"], view_token: "t6", evidence: []},
    ],
  });
  await h.mount();
  const expectations = [
    ["role-a::growth::g1", "成长记录"],
    ["role-a::self::r1", "每日复盘"],
    ["role-a::style::s-conv", "学习记录"],
    ["role-a::style::s-pack", "分享包"],
    ["role-a::style::s-leg", "旧版表达"],
    ["role-a::style::s-def", "角色表达"],
  ];
  for (const [key, expectedSource] of expectations) {
    h.click("[data-review-select]", {reviewSelect: key});
    const detail = h.root.innerHTML.slice(h.root.innerHTML.indexOf('<article class="lh-review-detail">'));
    const chips = detail.slice(detail.indexOf("lh-review-detail-chips"), detail.indexOf("lh-review-position"));
    assert.ok(!chips.includes(`>${expectedSource}<`), `${key} 顶部标签不应重复渲染来源 ${expectedSource}`);
    assert.match(detail, new RegExp(`<dt>来源<\\/dt><dd>${expectedSource}<\\/dd>`), `${key} 来源应为 ${expectedSource}`);
    if (key.includes("::growth::") || key.includes("::self::")) {
      assert.doesNotMatch(detail, /<dt>来源<\/dt><dd>角色表达<\/dd>/);
    }
  }
  h.page.unmount();
});

test("恢复维度、置信度与观察块，同时消除无 expression 时 proposal 标题与引述重复", async () => {
  const h = createHarness({
    style: [{
      signal_id: "s-rich", dimension: "diction", confidence: 0.78, source_kind: "conversation_input",
      proposal: "先安抚情绪再给出拆解建议",
      observation: "近三次对方催进度时先稳住情绪效果最好",
      expression: {kind: "style", pattern: "别急，咱们一步步来", situation: "对方感到焦虑或赶时间时", avoid: "对方只是随口打趣时不要说教"},
      applicability: {channels: ["private"], relationship_stages: ["close"], emotion_gates: ["low"], intent: "comfort", keywords: ["赶进度"]},
      created_at: "2026-11-20T12:00:00+08:00", actions: ["adopt", "reject"], view_token: "t1",
      evidence: ["依据一", "依据二", "依据三"],
    }, {
      signal_id: "s-legacy", dimension: "sentence_rhythm", source_kind: "legacy_expression",
      proposal: "晚安前保持一句简短问候",
      observation: "睡前对话通常节奏偏轻",
      created_at: "2026-11-20T11:00:00+08:00", actions: ["adopt", "reject"], view_token: "t2",
      evidence: ["依据 1", "依据 2", "依据 3", "依据 4"],
    }],
  });
  await h.mount();

  let detail = h.root.innerHTML.slice(h.root.innerHTML.indexOf('<article class="lh-review-detail">'));
  assert.match(detail, /lh-review-chip is-metric">措辞 · 78%<\/span>/);
  assert.match(detail, /<h2>别急，咱们一步步来<\/h2>/);
  assert.match(detail, /<p>对方感到焦虑或赶时间时<\/p>/);
  assert.match(detail, /<blockquote class="lh-review-quote"><p>先安抚情绪再给出拆解建议<\/p><\/blockquote>/);
  assert.match(detail, /<section class="lh-review-observation"><span>观察<\/span><p>近三次对方催进度时先稳住情绪效果最好<\/p><\/section>/);
  assert.match(detail, /何时不用<\/span><p>对方只是随口打趣时不要说教<\/p>/);
  assert.match(detail, /<em>频道<\/em>私聊/);
  assert.match(detail, /<details class="lh-review-evidence" open>/, "3 条及以内依据默认展开");

  h.click("[data-review-select]", {reviewSelect: "role-a::style::s-legacy"});
  detail = h.root.innerHTML.slice(h.root.innerHTML.indexOf('<article class="lh-review-detail">'));
  assert.match(detail, /lh-review-chip is-metric">句读节奏<\/span>/, "缺少 confidence 时不伪造 0%");
  assert.match(detail, /<h2>晚安前保持一句简短问候<\/h2>/);
  assert.doesNotMatch(detail, /<blockquote class="lh-review-quote">/, "proposal 已作标题时不得在 blockquote 重复");
  assert.match(detail, /<section class="lh-review-observation"><span>观察<\/span><p>睡前对话通常节奏偏轻<\/p><\/section>/);
  assert.match(detail, /<details class="lh-review-evidence"><summary>/, "超过 3 条依据默认折叠以保持手稿可读");
  h.page.unmount();
});

test("queueNode 面向 .lh-review-queue-list，重绘与刷新保留滚动位置且切筛选复位", async () => {
  const h = createHarness({
    growth: [
      {review_id: "g1", growth_title: "目标 1", progress_note: "进展 1", base_revision: 1, created_at: "2026-11-20T12:00:00+08:00", actions: ["accept", "reject"], view_token: "t1", evidence: []},
      {review_id: "g2", growth_title: "目标 2", progress_note: "进展 2", base_revision: 1, created_at: "2026-11-20T11:00:00+08:00", actions: ["accept", "reject"], view_token: "t2", evidence: []},
    ],
  });
  await h.mount();
  h.queueList.scrollTop = 260;
  h.click("[data-review-select]", {reviewSelect: "role-a::growth::g2"});
  assert.equal(h.queueList.scrollTop, 260, "选中条目重绘后保留队列滚动位置");
  assert.equal(h.queueOuter.scrollTop, 0);

  h.changeCheck("role-a::growth::g2", true);
  assert.equal(h.queueList.scrollTop, 260, "勾选复选框重绘后保留队列滚动位置");

  h.click("[data-review-refresh]");
  for (let attempt = 0; attempt < 80 && h.page.state.loading; attempt += 1) await flush();
  assert.equal(h.queueList.scrollTop, 260, "刷新后保留 .lh-review-queue-list 滚动位置");

  h.click("[data-review-filter]", {reviewFilter: "growth"});
  assert.equal(h.queueList.scrollTop, 0, "切换类型筛选后队列滚动位置复位到顶部");
  h.page.unmount();
});

test("window 级 J/K/Escape 快捷键在多次重绘后持续生效，过滤输入态并在卸载时清理", async () => {
  const h = createHarness({
    growth: [
      {review_id: "g1", growth_title: "第一条", progress_note: "p1", base_revision: 1, created_at: "2026-11-20T12:00:00+08:00", actions: ["accept", "reject"], view_token: "t1", evidence: []},
      {review_id: "g2", growth_title: "第二条", progress_note: "p2", base_revision: 1, created_at: "2026-11-20T11:00:00+08:00", actions: ["accept", "reject"], view_token: "t2", evidence: []},
      {review_id: "g3", growth_title: "第三条", progress_note: "p3", base_revision: 1, created_at: "2026-11-20T10:00:00+08:00", actions: ["accept", "reject"], view_token: "t3", evidence: []},
    ],
  });
  await h.mount();
  assert.match(h.root.innerHTML, /data-review-prev/);
  assert.match(h.root.innerHTML, /data-review-next/);
  assert.doesNotMatch(h.root.innerHTML, /data-review-skip|>略过</, "移除与 J 下一条重复的略过按钮");
  assert.equal(h.windowListeners.has("keydown"), true);
  assert.equal(h.rootListeners.has("keydown"), false);
  assert.equal(h.page.state.selected, "role-a::growth::g1");

  assert.equal(h.keydown("j"), true);
  assert.equal(h.page.state.selected, "role-a::growth::g2");
  assert.deepEqual(h.scrolledKeys.at(-1), {key: "role-a::growth::g2", block: "nearest"});

  assert.equal(h.keydown("J"), true);
  assert.equal(h.page.state.selected, "role-a::growth::g3", "多次 innerHTML 重绘后 J 仍持续有效");
  assert.deepEqual(h.scrolledKeys.at(-1), {key: "role-a::growth::g3", block: "nearest"});

  assert.equal(h.keydown("k"), true);
  assert.equal(h.page.state.selected, "role-a::growth::g2");

  // 输入元素、修饰键、打开弹窗、断连 DOM 均不得触发快捷键
  assert.equal(h.keydown("j", {target: {tagName: "INPUT"}}), false);
  assert.equal(h.keydown("j", {target: {tagName: "TEXTAREA"}}), false);
  assert.equal(h.keydown("j", {target: {tagName: "SELECT"}}), false);
  assert.equal(h.keydown("j", {target: {tagName: "DIV", isContentEditable: true}}), false);
  assert.equal(h.keydown("j", {ctrlKey: true}), false);
  h.setOpenDialog(true);
  assert.equal(h.keydown("j"), false);
  h.setOpenDialog(false);
  assert.equal(h.page.state.selected, "role-a::growth::g2");

  // Escape 取消待确认条
  h.click("[data-review-stage]", {reviewKey: "role-a::growth::g2", reviewStage: "accept"});
  assert.ok(h.page.state.confirming);
  assert.equal(h.keydown("Escape"), true);
  assert.equal(h.page.state.confirming, null);

  // root 断开连接后不触发
  h.root.isConnected = false;
  assert.equal(h.keydown("k"), false);
  assert.equal(h.page.state.selected, "role-a::growth::g2");

  h.page.unmount();
  assert.equal(h.windowListeners.size, 0, "unmount 必须解绑 window keydown 监听");
  assert.equal(h.rootListeners.size, 0, "unmount 必须解绑 root click/change 监听");
});

test("左置复选框与常驻批量采纳/批量驳回按钮，零勾选不抖动也不弹空确认", async () => {
  const h = createHarness({
    growth: [{
      review_id: "g1", growth_title: "目标 1", progress_note: "进展 1", base_revision: 1,
      created_at: "2026-11-20T12:00:00+08:00", actions: ["accept", "reject"], view_token: "t1", evidence: [],
    }],
  });
  await h.mount();
  const html = h.root.innerHTML;
  const rowStart = html.indexOf('class="lh-review-row-wrap');
  const checkPos = html.indexOf('class="lh-review-check"', rowStart);
  const indexPos = html.indexOf('class="lh-review-index"', rowStart);
  const rowPos = html.indexOf('<button type="button" class="lh-review-row', rowStart);
  assert.ok(checkPos > rowStart && checkPos < indexPos && indexPos < rowPos, "复选框位于序号和行内容左侧");
  assert.match(html, /data-review-bulk="accept" disabled>批量采纳<\/button>/);
  assert.match(html, /data-review-bulk="reject" disabled>批量驳回<\/button>/);

  h.click("[data-review-bulk]", {reviewBulk: "reject"});
  assert.equal(h.page.state.confirming, null, "0 项勾选时触发批量按钮不得打开空确认条");

  h.changeCheck("role-a::growth::g1", true);
  assert.match(h.root.innerHTML, /data-review-bulk="accept">批量采纳 · 1<\/button>/);
  assert.match(h.root.innerHTML, /data-review-bulk="reject">批量驳回 · 1<\/button>/);
  h.click("[data-review-bulk]", {reviewBulk: "accept"});
  assert.deepEqual({...h.page.state.confirming}, {bulk: true, action: "accept"});
  // 取消勾选后自动收起过期批量确认条
  h.changeCheck("role-a::growth::g1", false);
  assert.equal(h.page.state.confirming, null);

  // 刷新后若已勾选项失去可执行动作，不得残留「确认批量采纳 0 条记录？」
  h.changeCheck("role-a::growth::g1", true);
  h.click("[data-review-bulk]", {reviewBulk: "accept"});
  assert.ok(h.page.state.confirming?.bulk);
  h.page.state.items[0].actions = ["reject"];
  h.click("[data-review-refresh]");
  for (let attempt = 0; attempt < 80 && h.page.state.loading; attempt += 1) await flush();
  assert.equal(h.page.state.confirming?.bulk, true, "刷新重新拿回 accept 动作时确认条仍有效");
  h.page.state.checked.clear();
  h.click("[data-review-refresh]");
  for (let attempt = 0; attempt < 80 && h.page.state.loading; attempt += 1) await flush();
  assert.equal(h.page.state.confirming, null, "刷新后勾选清空时必须收起批量确认条");
  h.page.unmount();
});

test("learning-hub.css 清理串扰、非法 -var()、双栏独立滚动、对比度与窄屏导航可达性", () => {
  assert.doesNotMatch(hubCss, /-var\(/, "不得存在非法 -var() 语法");
  assert.doesNotMatch(hubCss, /\.lh-review-bulk(?![\w-])/, "不得残留废弃 .lh-review-bulk 规则");
  assert.doesNotMatch(hubCss, /\.shell-panel-head\s*\{\s*display\s*:\s*none/i, "不得隐藏 .shell-panel-head（「回到小屋」必须始终可达）");
  assert.doesNotMatch(hubCss, /\.learning-hub-nav\s*\{\s*display\s*:\s*none/i, "不得在任何断点隐藏 .learning-hub-nav");
  assert.match(hubCss, /body:has\(\.learning-hub\.is-review\)\s+#homeShell\[data-away="true"\]\s+\.stage-region\{visibility:hidden!important/);

  // 05 表达学习观察素材使用独立 .lh-samples-filters，零 CSS 串扰
  assert.match(samplesSource, /class="lh-samples-filters"/);
  assert.doesNotMatch(samplesSource, /lh-review-filters/);
  assert.match(hubCss, /\.lh-samples-filters\s*\{/);
  for (const match of hubCss.matchAll(/(^|\}|\n)([^\n{}]*\.lh-review-filters[^\n{]*)\{/g)) {
    assert.match(match[2], /\.learning-review-workspace/, `审阅筛选器样式必须限定在 .learning-review-workspace 内：${match[2]}`);
  }

  // 自然页面流式展开 + 右侧详情台 sticky 全视口吸顶，禁止固定小盒锁高与滚轮截留
  assert.match(hubCss, /\.learning-review-workspace \.lh-review-layout\{[^}]*grid-template-columns:minmax\(270px,340px\) minmax\(0,1fr\)[^}]*align-items:start[^}]*height:auto;max-height:none/);
  assert.doesNotMatch(hubCss, /max-height:clamp\(420px,calc\(100dvh - 320px\),760px\)/, "不得锁死 420–760px 矮盒限制双栏高度");
  assert.match(hubCss, /\.learning-review-workspace \.lh-review-queue\{[^}]*height:auto;overflow:visible/);
  assert.match(hubCss, /\.learning-review-workspace \.lh-review-detail\{[^}]*position:sticky;top:var\(--sp-3\)[^}]*max-height:calc\(100dvh - 32px\)/);
  assert.match(hubCss, /\.learning-review-workspace \.lh-review-queue-list\{[^}]*overflow-y:visible;overscroll-behavior:auto/);
  assert.match(hubCss, /\.learning-review-workspace \.lh-review-detail-body\{[^}]*overflow-y:auto;overscroll-behavior:auto/);
  assert.doesNotMatch(hubCss, /overscroll-behavior:contain/, "审阅中心不得截留滚轮阻断主页面自然滚动");
  assert.match(hubCss, /\.learning-review-workspace \.lh-review-empty-state\{/);
  assert.match(hubCss, /@container learning-hub \(max-width:820px\)/);

  // 选中行与三色语义徽章对比度分离（无伪装融合）
  assert.match(hubCss, /\.learning-review-workspace \.lh-review-row-wrap\.is-selected\{[^}]*border-color:var\(--t-accent\);background:var\(--t-surface-raised\);box-shadow:inset 3px 0 0 var\(--t-accent-solid\)/);
  assert.match(hubCss, /\.learning-review-workspace \.lh-review-meta-grid\{[^}]*grid-template-columns:minmax\(0,1\.8fr\) minmax\(0,0\.9fr\) minmax\(0,0\.9fr\)/);
  assert.match(hubCss, /\.learning-review-workspace \.lh-review-type\.is-growth\{background:var\(--t-accent-quiet\);color:var\(--t-accent-ink\)\}/);
  assert.match(hubCss, /\.learning-review-workspace \.lh-review-type\.is-style\{background:var\(--t-warn-quiet\);color:var\(--t-warn-ink\)\}/);
  assert.match(hubCss, /\.learning-review-workspace \.lh-review-type\.is-self\{background:var\(--t-ok-quiet\);color:var\(--t-ok-ink\)\}/);

  // 学习工作台通用响应式修复：外框圆角与内层单行滚动轨解耦 + 窄容器尾部柔和渐隐 + 5 列看板 auto-fit 防挤压
  assert.match(hubCss, /\.learning-hub\{[^}]*container-type:inline-size;container-name:learning-hub/);
  assert.match(hubCss, /\.learning-hub-nav\{[^}]*border:1px solid var\(--t-line\);border-radius:var\(--r-md\)/);
  assert.match(hubCss, /\.learning-hub-tabs\{[^}]*display:flex;flex-wrap:nowrap[^}]*overflow-x:auto/);
  assert.match(hubCss, /\.learning-hub-tab\.is-active,\.learning-hub-tab\.is-active:hover\{[^}]*background:var\(--t-accent-solid\);color:var\(--t-on-solid\)/);
  assert.match(hubCss, /@container learning-hub \(max-width:740px\)\{[\s\S]*?\.learning-hub-tabs\{[\s\S]*?mask-image:linear-gradient\(to right,black 0,black calc\(100% - 22px\),transparent 100%\)/);
  assert.match(hubCss, /\.lh-overview-lanes\{[^}]*grid-template-columns:repeat\(auto-fit,minmax\(min\(220px,100%\),1fr\)\)/);
  const posMedia1320 = hubCss.indexOf("@media(max-width:1320px)");
  const posMedia880 = hubCss.indexOf("@media(max-width:880px)");
  const posContainer740 = hubCss.indexOf("@container learning-hub (max-width:740px)");
  const posMedia520 = hubCss.indexOf("@media(max-width:520px)");
  assert.ok(
    posMedia1320 > 0 && posMedia1320 < posMedia880 && posMedia880 < posContainer740 && posContainer740 < posMedia520,
    "学习导航断点必须按 1320 -> 880 -> 740 -> 520 严格降序排列，避免宽断点覆盖窄容器",
  );
});
