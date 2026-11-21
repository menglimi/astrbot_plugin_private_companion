"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const read = (...parts) => fs.readFileSync(path.join(ROOT, ...parts), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));
const settle = async (rounds = 8) => { for (let i = 0; i < rounds; i++) await flush(); };

function loadHomeSidebar() {
  const window = {};
  const document = { addEventListener() {} };
  vm.runInNewContext(read("pages", "陪伴小屋", "home-sidebar.js"), {
    window, document, Intl, Date, Number, String, Object, Array, Set, Map, RegExp, Math, Boolean,
  });
  return window.CompanionHomeSidebar;
}

const NOW = Date.parse("2026-11-21T00:00:00Z");
const makeStatus = (values = {}, settings = {}) => ({
  settings: { enabled: true, planning_timezone: "Asia/Shanghai", ...settings },
  state: { revision: 1, values },
  occurrences: { items: [], partial: false },
  sleep_schedule: { enabled: false },
});
const makeLife = (activities = []) => ({ activities, config: { diary_enabled: false } });

test("Issue #1: 否定子句的文学化健康/饥饿/睡眠/忙碌表述不再误判为异常或特殊状态", () => {
  const { vital, specialState } = loadHomeSidebar();

  const negatedHealth = [
    "手腕和指尖都暖和灵活，肩背舒展轻快，浑身稳当得很，没什么酸沉不舒服的地方",
    "挑不出什么酸软或不适",
    "没有什么酸沉不适感",
    "没有半点不适",
    "没有闷胀或不适",
    "并无明显酸软、闷胀或不适",
    "没有发烧、头痛或不适",
    "身体状态正常，暂无明显不适",
    "浑身轻快，并没有觉得哪里不舒服",
    "一点不适都没有",
    "并未觉得有任何酸痛或不适",
    "浑身舒展，暂无其他不适",
    "没有别的不舒服",
  ];
  for (const sample of negatedHealth) {
    const result = vital("health", sample);
    assert.equal(result.visual, "well", `health vital(${sample}) should be well`);
    assert.equal(result.label, "良好", `health label(${sample}) should be 良好`);
    assert.equal(result.tone, "ok", `health tone(${sample}) should be ok`);
    assert.equal(specialState("health", sample), false, `specialState(health, ${sample}) should be false`);
  }
  assert.equal(vital("health", "胃部像一团小小的棉花").visual, "recorded");

  const negatedHunger = [
    "沉睡中肠胃温和安稳，完全没有饥饿感",
    "没有任何扰人的饥饿感",
    "尚未觉察到饥饿",
    "胃里安稳，半点饥饿感也没有",
    "没有吃撑，也不觉得饿",
    "刚喝了热水，完全不饿",
    "谈不上有多饿",
  ];
  for (const sample of negatedHunger) {
    const result = vital("hunger", sample);
    assert.equal(result.visual, "sated", `hunger vital(${sample}) should be sated`);
    assert.equal(result.label, "不饿", `hunger label(${sample}) should be 不饿`);
    assert.equal(specialState("hunger", sample), false, `specialState(hunger, ${sample}) should be false`);
  }
  assert.notEqual(vital("hunger", "没有吃撑，并无饱胀感").visual, "full");

  const negatedSleepy = [
    "神智清醒沉静，完全没有困倦感",
    "并没有真犯困",
    "一点也不困",
    "丝毫困意也无",
    "精神很好，并不想睡",
  ];
  for (const sample of negatedSleepy) {
    const result = vital("sleep", sample);
    assert.equal(result.visual, "awake", `sleep vital(${sample}) should be awake`);
    assert.equal(result.label, "清醒", `sleep label(${sample}) should be 清醒`);
    assert.equal(specialState("sleep", sample), false, `specialState(sleep, ${sample}) should be false`);
  }
  assert.notEqual(vital("sleep", "没有失眠，暂无睡眠不足").visual, "restless");
  assert.equal(specialState("sleep", "没有失眠，暂无睡眠不足"), false);
  assert.notEqual(vital("sleep", "还没入睡").visual, "rest");
  assert.notEqual(vital("sleep", "没有睡着").visual, "rest");

  for (const sample of ["没在忙什么正经事", "不忙，没什么忙碌的事", "并不算在忙", "暂时没忙什么", "目前不怎么忙"]) {
    assert.equal(specialState("busy", sample), false, `specialState(busy, ${sample}) should be false`);
  }
});

test("Issue #1: 真实正向不适/饥饿/困倦/忙碌与转折子句保持准确归类且无回溯卡顿", () => {
  const { vital, specialState } = loadHomeSidebar();

  const positiveHealth = [
    "有点不舒服",
    "完全不舒服",
    "一点也不舒服",
    "肩背酸沉不适",
    "肩背有些酸沉不舒服",
    "没睡好，有点头疼",
    "没有发烧，但有点头疼",
    "没有发烧只是有点不舒服",
    "不适感没有消退",
    "状态有点不稳定",
    "今天精神欠佳",
    "有点不太舒服",
    "并不太舒服",
  ];
  for (const sample of positiveHealth) {
    const result = vital("health", sample);
    assert.equal(result.visual, "strained", `health vital(${sample}) should be strained`);
    assert.equal(result.label, "不适", `health label(${sample}) should be 不适`);
    assert.equal(result.tone, "warn");
    assert.equal(specialState("health", sample), true, `specialState(health, ${sample}) should be true`);
  }

  for (const sample of ["有点饿了", "肚子饿得发慌", "没吃饱", "还没吃饱", "没有吃饱", "没有吃撑，但有点饿", "饿"]) {
    const result = vital("hunger", sample);
    assert.equal(result.visual, "hungry", `hunger vital(${sample}) should be hungry`);
    assert.equal(result.label, "饿了");
    assert.equal(specialState("hunger", sample), true, `specialState(hunger, ${sample}) should be true`);
  }
  assert.equal(vital("hunger", "吃撑了").visual, "full");

  for (const sample of ["好困想睡", "有些困", "虽然清醒但很疲惫", "犯困", "困"]) {
    const result = vital("sleep", sample);
    assert.equal(result.visual, "sleepy", `sleep vital(${sample}) should be sleepy`);
    assert.equal(result.label, "困倦");
  }
  for (const sample of ["难以入睡", "无法入睡", "睡不着", "没睡好", "失眠"]) {
    const result = vital("sleep", sample);
    assert.equal(result.visual, "restless", `sleep vital(${sample}) should be restless`);
    assert.equal(specialState("sleep", sample), true);
  }

  for (const sample of ["现在正忙着", "正在忙写稿", "忙着整理书架", "有点忙", "不得不忙着收尾", "忙"]) {
    assert.equal(specialState("busy", sample), true, `specialState(busy, ${sample}) should be true`);
  }

  const pathological = "没有" + "明显酸沉或".repeat(60) + "普通描述";
  const started = Date.now();
  assert.equal(vital("health", pathological).visual, "recorded");
  assert.ok(Date.now() - started < 200, "clause negation matching must stay linear-time");
});

test("Issue #1: home-sidebar 宽行判断按渲染标签长度而非原文长度计算", () => {
  const { renderState } = loadHomeSidebar();
  const state = makeStatus({
    energy: 88,
    health: "手腕和指尖都暖和灵活，肩背舒展轻快，浑身稳当得很，没什么酸沉不舒服的地方",
    hunger: "沉睡中肠胃温和安稳，完全没有饥饿感",
    cycle: "黄体期",
    mood: "安静踏实",
    sleep: "清醒",
  }, { home_show_busy: false, home_show_location: false });
  const html = renderState({ now: NOW, state, life: makeLife() });
  assert.match(html, /class="home-state-row" data-state-kind="health" data-visual="well" data-tone="ok"/);
  assert.match(html, /data-state-kind="health"[\s\S]*?<strong>良好<\/strong>/);
  assert.match(html, /class="home-state-row" data-state-kind="hunger" data-visual="sated" data-tone="ok"/);
  assert.match(html, /data-state-kind="hunger"[\s\S]*?<strong>不饿<\/strong>/);
  assert.match(html, /class="home-state-row is-wide" data-state-kind="mood"/);

  state.state.values.health = "胃部像一团小小的棉花";
  const wideHtml = renderState({ now: NOW, state, life: makeLife() });
  assert.match(wideHtml, /class="home-state-row is-wide" data-state-kind="health" data-visual="recorded"/);
});

test("Issue #2a: life 工作区按需轮询、隐藏暂停、无变化零 DOM 重建且不重播入场动画", async () => {
  const lifeJs = read("pages", "日记与生活", "app.js");
  const lifeCss = read("pages", "日记与生活", "style.css");
  const mirroredCss = read("pages", "陪伴小屋", "workspaces", "life", "style.css");
  const bundleJs = read("pages", "陪伴小屋", "workspace-bundles", "life.js");
  assert.equal(lifeCss, mirroredCss, "standalone and workspace life style.css must stay synced");
  assert.match(lifeCss, /#journal-list\.is-initial-render \.journal-link \{ animation: rise/);
  assert.match(lifeCss, /\.activity-list\.is-initial-render \.activity \{ animation: rise/);
  assert.doesNotMatch(lifeCss, /(?:^|\n)#journal-list \.journal-link \{ animation: rise/);
  assert.doesNotMatch(lifeCss, /(?:^|\n)\.activity \{ animation: rise/);
  assert.match(bundleJs, /is-initial-render/);

  class FakeClassList {
    constructor() { this.set = new Set(); }
    add(name) { this.set.add(name); }
    remove(name) { this.set.delete(name); }
    toggle(name, force) {
      if (force === undefined) { if (this.set.has(name)) this.set.delete(name); else this.set.add(name); }
      else if (force) this.set.add(name);
      else this.set.delete(name);
    }
    contains(name) { return this.set.has(name); }
  }
  class FakeElement {
    constructor(tag = "div") {
      this.tag = tag;
      this.listeners = new Map();
      this.children = [];
      this.dataset = {};
      this.attributes = {};
      this.classList = new FakeClassList();
      this.value = "";
      this.textContent = "";
      this.hidden = false;
      this.disabled = false;
      this.checked = false;
      this.open = false;
      this.replaceChildrenCount = 0;
    }
    addEventListener(name, cb) { this.listeners.set(name, cb); }
    replaceChildren(...children) { this.replaceChildrenCount += 1; this.children = children; }
    append(...children) { this.children.push(...children); }
    querySelectorAll() { return this.children; }
    setAttribute(name, value) { this.attributes[name] = value; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    click() { this.listeners.get("click")?.({ preventDefault() {} }); }
  }

  const ids = new Set([...lifeJs.matchAll(/\$\("([^"]+)"\)/g)].map(m => m[1]));
  for (const extra of ["reading-list", "sleep-list"]) ids.add(extra);
  const elements = new Map([...ids].map(id => [id, new FakeElement()]));
  const docListeners = {};
  const document = {
    hidden: false,
    getElementById: id => elements.get(id) || new FakeElement(),
    createElement: tag => new FakeElement(tag),
    querySelectorAll: () => [],
    addEventListener(name, cb) { (docListeners[name] ||= []).push(cb); },
  };
  const timers = new Map();
  let nextTimerId = 1;
  const scheduledDelays = [];
  const fakeSetTimeout = (cb, delay) => {
    const id = nextTimerId++;
    timers.set(id, { cb, delay });
    scheduledDelays.push(delay);
    return id;
  };
  const fakeClearTimeout = id => { timers.delete(id); };

  const basePayload = () => ({
    role_id: "role-1",
    host_state: "running",
    config: {
      enabled: true, diary_enabled: true, sleep_enabled: true, reading_enabled: true,
      derive_enabled: false, timezone: "Asia/Shanghai", diary_time: "23:50", sleep_time: "00:30",
      sleep_timezone: "Asia/Shanghai", sleep_variation_minutes: 20, sleep_window_minutes: 120,
      sleep_minutes: 480,
    },
    journals: [
      { kind: "diary", local_date: "2026-11-20", revision: 1, state: "active", body: "第一篇日记", view_token: "jt-1" },
      { kind: "diary", local_date: "2026-11-19", revision: 1, state: "active", body: "第二篇日记", view_token: "jt-2" },
    ],
    activities: [
      {
        activity_id: "sleep-1", revision: 1, kind: "sleep", state: "active", title: "睡眠休息",
        started_at: "2026-11-20T16:30:00Z", due_at: "2026-11-21T00:30:00Z",
        sleep: { kind: "night", target_minutes: 480, slept_seconds: 1200 }, view_token: "st-1",
      },
      {
        activity_id: "read-1", revision: 1, kind: "reading", state: "planned", title: "夜读摘录",
        started_at: "2026-11-20T12:00:00Z", due_at: null,
        book_progress: { completed: 1, total: 5 }, view_token: "rt-1",
      },
    ],
    receipts: [{ operation_id: "op-1", state: "succeeded" }],
    running: [],
    next_diary_at: null, next_sleep_at: null, next_reading_at: null,
    next_date: null, next_activity: null, view_token: "vt-1", edit_token: "et-1",
    sleep_schedule: { enabled: true, executor_state: "available", executor_is_current: true, physiology: { sleep_pressure: 35, workload_score: 20 } },
  });

  let currentPayload = basePayload();
  const bridge = {
    ready: async () => bridge,
    getContext: () => ({ display: { role_name: "测试角色" } }),
    apiGet: async () => JSON.parse(JSON.stringify(currentPayload)),
    apiPost: async () => ({}),
  };

  vm.runInNewContext(lifeJs, {
    window: { crypto: { randomUUID: () => "uuid-1" }, AstrBotPluginPage: bridge },
    document,
    Option: class extends FakeElement { constructor(label, value) { super("option"); this.textContent = label; this.value = value; } },
    setTimeout: fakeSetTimeout,
    clearTimeout: fakeClearTimeout,
    Intl, Date, JSON, Math, Number, String, Object, Array, Set, Map, Boolean, Error, Promise,
  });
  await settle();

  // Initial render populates every section once with one-shot entrance animation.
  assert.equal(elements.get("journal-list").replaceChildrenCount, 1);
  assert.equal(elements.get("journal-detail").replaceChildrenCount, 1);
  assert.equal(elements.get("reading-list").replaceChildrenCount, 1);
  assert.equal(elements.get("sleep-list").replaceChildrenCount, 1);
  assert.equal(elements.get("receipt-list").replaceChildrenCount, 1);
  assert.equal(elements.get("sleep-list").classList.contains("is-initial-render"), true);
  assert.equal(elements.get("journal-list").classList.contains("is-initial-render"), true);
  // Active sleep alone (with running empty) polls calmly at 30s, never 3s.
  assert.equal(scheduledDelays.at(-1), 30000);

  // Clicking another journal updates detail without rebuilding #journal-list.
  elements.get("journal-list").children[1].click();
  assert.equal(elements.get("journal-list").replaceChildrenCount, 1);
  assert.equal(elements.get("journal-detail").replaceChildrenCount, 2);
  assert.equal(elements.get("journal-list").children[1].attributes["aria-current"], "true");

  // Unchanged background polls (even with ticking slept_seconds / sleep_pressure) produce ZERO DOM teardown.
  for (let tick = 0; tick < 3; tick++) {
    currentPayload.activities[0].sleep.slept_seconds += 30;
    currentPayload.sleep_schedule.physiology.sleep_pressure += 1;
    const [[id, entry]] = [...timers.entries()];
    timers.delete(id);
    entry.cb();
    await settle();
  }
  assert.equal(elements.get("journal-list").replaceChildrenCount, 1);
  assert.equal(elements.get("journal-detail").replaceChildrenCount, 2);
  assert.equal(elements.get("reading-list").replaceChildrenCount, 1);
  assert.equal(elements.get("sleep-list").replaceChildrenCount, 1);
  assert.equal(elements.get("receipt-list").replaceChildrenCount, 1);

  // When running task starts, polling accelerates to 3s; when sleep changes state, ONLY #sleep-list rebuilds without entrance animation replay.
  currentPayload.running = [{ kind: "diary" }];
  currentPayload.activities[0].state = "completed";
  currentPayload.activities[0].revision = 2;
  {
    const [[id, entry]] = [...timers.entries()];
    timers.delete(id);
    entry.cb();
    await settle();
  }
  assert.equal(scheduledDelays.at(-1), 3000);
  assert.equal(elements.get("sleep-list").replaceChildrenCount, 2);
  assert.equal(elements.get("sleep-list").classList.contains("is-initial-render"), false);
  assert.equal(elements.get("journal-list").replaceChildrenCount, 1);
  assert.equal(elements.get("reading-list").replaceChildrenCount, 1);
  assert.equal(elements.get("receipt-list").replaceChildrenCount, 1);

  // Hiding document clears pending timer cleanly and pauses polling.
  assert.equal(timers.size, 1);
  document.hidden = true;
  for (const cb of docListeners.visibilitychange || []) cb();
  assert.equal(timers.size, 0);

  // Showing document resumes via background refresh.
  currentPayload.running = [];
  document.hidden = false;
  for (const cb of docListeners.visibilitychange || []) cb();
  await settle();
  assert.equal(timers.size, 1);
  assert.equal(scheduledDelays.at(-1), 15000, "reading_enabled with no active sleep polls at 15s");
});

test("Issue #2b: memory-page 事实流不轮询，整理视图静默轮询无变化零重绘", async () => {
  const source = read("pages", "陪伴小屋", "memory-page.js");
  const timers = new Map();
  let nextTimerId = 1;
  let reflectionsData = [{ batch_id: "b-1", state: "running", summary: "整理中", receipts: {}, remaining_sources: 2 }];
  let overviewData = { local: { reflections: { total: 1, by_state: { running: 1 } } }, linkage: { state: "not_owner" } };

  const bridge = {
    ready: async () => ({ pluginName: "astrbot_plugin_private_companion" }),
    apiGet: async (route, query) => {
      if (route === "page/ownership") return { owner: "self", reason: "owner_module_absent", display_name: "" };
      if (route === "page/targets") return {
        installation_id: "inst", operator_id: "op",
        targets: [{ target_id: "t-1", scope_kind: "private", roles: [{ role_id: "r-1", memory_api: "/astrbot_plugin_private_companion/memory" }] }],
      };
      if (route === "memory") return { status: "ok", data: { items: [{ atom_id: "a-1", revision: 1, state: "active", representation: "事实一" }], next_cursor: null } };
      if (route === "memory/reflections") {
        if (query?.batch_id) {
          const found = reflectionsData.find(item => item.batch_id === query.batch_id) || reflectionsData[0];
          return { status: "ok", data: JSON.parse(JSON.stringify(found)) };
        }
        return { status: "ok", data: { items: JSON.parse(JSON.stringify(reflectionsData)), next_cursor: null } };
      }
      if (route === "memory/overview") return { status: "ok", data: JSON.parse(JSON.stringify(overviewData)) };
      return { status: "ok", data: {} };
    },
    apiPost: async () => ({ status: "ok", data: {} }),
  };

  const window = {
    AstrBotPluginPage: bridge,
    CompanionProduct: { hosted: () => true, selection: () => null, choose: () => {}, bridgeNow: () => bridge, bridgeFor: async () => bridge },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    crypto: { randomUUID: () => "k-1" },
    matchMedia: () => ({ matches: true }),
    requestAnimationFrame: () => 0,
    performance: { now: () => 0 },
  };
  const document = {
    hidden: false,
    addEventListener() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    body: { classList: { add() {}, remove() {}, toggle() {} } },
    title: "",
  };
  vm.runInNewContext(source, {
    window, document, crypto: window.crypto,
    setTimeout: (cb, ms) => { const id = nextTimerId++; timers.set(id, { cb, ms }); return id; },
    clearTimeout: id => { timers.delete(id); },
    URL, Blob, console, Intl, Date, JSON, Math, Number, Object, Array, String, Set, Map, Boolean, Error, Promise,
    CSS: { escape: v => v },
  });

  let htmlWrites = 0, htmlValue = "";
  const handlers = {}, inside = new Set();
  const root = {
    isConnected: true,
    get innerHTML() { return htmlValue; },
    set innerHTML(v) { htmlWrites += 1; htmlValue = v; },
    addEventListener(type, cb) { handlers[type] = cb; },
    removeEventListener() {},
    contains(el) { return inside.has(el); },
    querySelector: () => null,
    querySelectorAll: () => [],
  };

  const page = window.CompanionMemoryPage;
  await page.mount(root);
  await settle();

  // Default area is "facts": must NOT schedule any 8s polling timer.
  assert.equal(page.state.data.area, "facts");
  assert.equal([...timers.values()].filter(t => t.ms === 8000).length, 0, "facts view must not poll every 8s");

  // Switch to "reflections" area: schedules 8s silent poll.
  const refBtn = { disabled: false, id: "memory-area-reflections", dataset: {} };
  refBtn.closest = () => refBtn;
  inside.add(refBtn);
  handlers.click({ target: refBtn });
  await settle();
  const pollTimers = () => [...timers.entries()].filter(([, t]) => t.ms === 8000);
  assert.equal(pollTimers().length, 1);

  const writesBeforePoll = htmlWrites;
  const [[pollId, pollEntry]] = pollTimers();
  timers.delete(pollId);
  pollEntry.cb();
  assert.equal(page.state.data.loading, false, "background poll must not flash data.loading = true");
  await settle();
  assert.equal(htmlWrites, writesBeforePoll, "unchanged reflections poll must cause zero innerHTML writes");

  // When reflections data actually changes, render runs once (not twice).
  reflectionsData = [{ batch_id: "b-1", state: "complete", summary: "整理完成结果", receipts: {}, remaining_sources: 0 }];
  const [[pollId2, pollEntry2]] = pollTimers();
  timers.delete(pollId2);
  pollEntry2.cb();
  await settle();
  assert.equal(htmlWrites, writesBeforePoll + 1);
  assert.match(htmlValue, /整理完成/);

  page.unmount();
});

test("Issue #2c: state-page 后台轮询静默执行且忽略易变 observed_at 零重绘", async () => {
  const source = read("pages", "陪伴小屋", "state-page.js");
  const timers = new Map();
  let nextTimerId = 1, observedSeq = 1, healthText = "手腕暖和灵活，没什么酸沉不舒服的地方";
  let lucideCalls = 0, htmlWrites = 0, htmlValue = "";

  const targets = [{ target_id: "t-1", bot_id: "bot-1", scope_kind: "private", roles: [{ role_id: "r-1", state_api: "/astrbot_plugin_private_companion/state/t-1" }] }];
  const bridge = {
    ready: async () => ({ pluginName: "astrbot_plugin_private_companion" }),
    apiGet: async () => ({
      status: "ok",
      data: {
        state: { revision: 5, view_token: "vt-5", values: { energy: 85, health: healthText } },
        settings: { enabled: true, energy_enabled: true, health_enabled: true, presence_enabled: true, typing_enabled: false, online_status_enabled: false },
        sleep_schedule: { enabled: false },
        effective: { presence_enabled: { allowed: true } },
        presentation_diagnostics: { observed_at: `2026-11-21T00:00:${String(observedSeq++).padStart(2, "0")}Z`, custom_status: { state: "idle" } },
        presentation: {},
      },
    }),
  };

  const window = {
    lucide: { createIcons() { lucideCalls += 1; } },
    CompanionProduct: {
      hosted: () => true, scopeKey: () => "scope-1", bridgeNow: () => bridge,
      directory: () => ({ installation_id: "inst", operator_id: "op", targets }),
      selection: () => ({ target_id: "t-1", bot_id: "bot-1", role_id: "r-1" }),
      roleTarget: () => targets[0],
    },
    CompanionStateEditor: {
      create: () => ({ setScope() {}, setStatus() {}, invalidate() {}, markup: () => "", destroy() {}, discardIfPristine: () => true, interacting: false }),
    },
  };
  const document = { hidden: false, addEventListener() {}, querySelector: () => null, body: { classList: { toggle() {} } } };
  const root = {
    isConnected: true,
    get innerHTML() { return htmlValue; },
    set innerHTML(v) { htmlWrites += 1; htmlValue = v; },
    addEventListener() {}, removeEventListener() {},
    querySelector: () => null, querySelectorAll: () => [], contains: () => false,
  };

  vm.runInNewContext(source, {
    window, document, Promise, Intl, Date, console, JSON, Object, Array, String, Number, Set, Map, Boolean, Error,
    setTimeout: (cb, ms) => { const id = nextTimerId++; timers.set(id, { cb, ms }); return id; },
    clearTimeout: id => { timers.delete(id); },
  });

  const page = window.CompanionStatePage;
  await page.mount(root);
  await settle();

  const pollTimers = () => [...timers.entries()].filter(([, t]) => t.ms === 15000);
  assert.equal(pollTimers().length, 1);
  const writesAfterMount = htmlWrites;
  const lucideAfterMount = lucideCalls;

  // Unchanged state with ticking presentation_diagnostics.observed_at produces ZERO innerHTML writes or icon scans.
  const [[id1, entry1]] = pollTimers();
  timers.delete(id1);
  entry1.cb();
  await settle();
  assert.equal(htmlWrites, writesAfterMount, "unchanged state poll must not touch root.innerHTML");
  assert.equal(lucideCalls, lucideAfterMount, "unchanged state poll must not re-scan lucide icons");

  // Genuine state change renders ONCE (never twice for loading flash).
  healthText = "肩背有点酸沉";
  const [[id2, entry2]] = pollTimers();
  timers.delete(id2);
  entry2.cb();
  await settle();
  assert.equal(htmlWrites, writesAfterMount + 1);
  assert.match(htmlValue, /肩背有点酸沉/);

  page.unmount();
});

test("Issue #2d: app.js renderShellSide 对比缓存标记，无变化及仅分钟推进时不重建此刻的状态", async () => {
  const page = name => read("pages", "陪伴小屋", name);
  let sideWrites = 0, sideHtml = "", lucideCalls = 0;
  let stateHtml = '<button class="home-state-board">良好</button>';
  let scheduleHtml = '<div class="home-agenda-status">当前空闲 · 08:00</div>';
  let ageText = "5 分钟前";
  let productChanged = null;

  const ageNode = { textContent: ageText, remove() {} };
  const agendaNode = {
    innerHTML: scheduleHtml,
    writes: 0,
    querySelector: () => null,
  };
  let agendaInner = scheduleHtml;
  Object.defineProperty(agendaNode, "innerHTML", {
    get() { return agendaInner; },
    set(v) { agendaNode.writes += 1; agendaInner = v; },
  });

  function makeNode() {
    const classes = new Set();
    return {
      innerHTML: "", dataset: {}, isConnected: true,
      addEventListener() {}, setAttribute() {}, removeAttribute() {},
      querySelector: () => null, querySelectorAll: () => [], closest: () => null,
      focus() {}, remove() {}, insertAdjacentHTML() {},
      classList: { add: n => classes.add(n), remove: n => classes.delete(n), contains: n => classes.has(n), toggle: (n, on) => on ? classes.add(n) : classes.delete(n) },
    };
  }
  const names = ["navigationList", "main", "shellPanel", "shellSide", "stageFurniture", "stageScene", "detailDialog", "searchDialog", "globalSearch", "searchButton", "toast", "homeShell"];
  const nodes = Object.fromEntries(names.map(n => ["#" + n, makeNode()]));
  for (const s of ["[data-home-environment]", "[data-voxel-room]"]) nodes[s] = makeNode();
  Object.defineProperty(nodes["#shellSide"], "innerHTML", {
    get() { return sideHtml; },
    set(v) { sideWrites += 1; sideHtml = v; agendaInner = scheduleHtml; ageNode.textContent = ageText; },
  });
  nodes["#shellSide"].querySelector = selector => {
    if (selector === "[data-home-state-age]") return ageNode;
    if (selector === "[data-home-agenda-clock]") return agendaNode;
    return null;
  };

  const document = {
    documentElement: { dataset: { performanceMode: "lite" } },
    body: makeNode(),
    hidden: false,
    addEventListener() {},
    getElementById: id => nodes["#" + id] || null,
    querySelector(s) {
      if (s === "[data-home-state-age]") return sideHtml ? ageNode : null;
      if (s === "[data-home-agenda-clock]") return sideHtml ? agendaNode : null;
      return nodes[s] || null;
    },
    querySelectorAll: () => [],
    createElement: () => makeNode(),
  };
  const window = {
    addEventListener() {}, scrollTo() {}, matchMedia: () => ({ matches: false }),
    lucide: { createIcons(opts) { if (opts?.root === nodes["#shellSide"]) lucideCalls += 1; } },
    CompanionProduct: {
      connect: cb => { productChanged = cb; return new Promise(() => {}); },
      scopeKey: () => "role-a", hosted: () => false,
      role: () => null, roleTarget: () => null, capability: () => null,
      appearance: () => ({ motion: "full" }), renderScope() {},
      connection: () => ({ state: "ready" }),
      stateCard: () => stateHtml,
      scheduleCard: () => scheduleHtml,
      stateAge: () => ageText,
      syncLight() {},
      refreshSummary: async () => { productChanged?.({ kind: "state-refresh" }); return true; },
      unmountWorkspace() {}, mountWorkspace() {},
      data: {}, targets: () => [], workspace: () => "",
    },
  };
  const context = vm.createContext({
    window, document, location: { hash: "#life" },
    history: { replaceState() {}, pushState() {} },
    structuredClone, localStorage: { getItem: () => null, setItem() {} },
    matchMedia: window.matchMedia, console,
    setTimeout: () => 1, clearTimeout() {},
    requestAnimationFrame: () => 1, queueMicrotask: () => {},
    ResizeObserver: class { observe() {} },
  });
  for (const name of ["shell.js", "home-environment.js", "app.js"]) vm.runInContext(page(name), context);

  const initialWrites = sideWrites;
  const initialLucide = lucideCalls;
  assert.ok(initialWrites >= 1);

  // Unchanged 15s refresh produces ZERO #shellSide.innerHTML writes and ZERO Lucide re-scans.
  await vm.runInContext("refreshHomeAgendaClock(true)", context);
  assert.equal(sideWrites, initialWrites, "unchanged sidebar poll must skip #shellSide.innerHTML");
  assert.equal(lucideCalls, initialLucide, "unchanged sidebar poll must skip lucide re-scan");

  // Minute rollover updates ONLY ageNode.textContent & agendaNode.innerHTML without rebuilding #shellSide.
  ageText = "6 分钟前";
  scheduleHtml = '<div class="home-agenda-status">当前空闲 · 08:01</div>';
  await vm.runInContext("refreshHomeAgendaClock(true)", context);
  assert.equal(sideWrites, initialWrites, "schedule/age tick must not tear down 此刻的状态 DOM");
  assert.equal(lucideCalls, initialLucide, "schedule/age tick must not re-scan lucide icons");
  assert.equal(ageNode.textContent, "6 分钟前");
  assert.equal(agendaNode.innerHTML, scheduleHtml);

  // Genuine state change updates #shellSide.innerHTML once and refreshes icons.
  stateHtml = '<button class="home-state-board">有点饿</button>';
  await vm.runInContext("refreshHomeAgendaClock(true)", context);
  assert.equal(sideWrites, initialWrites + 1);
  assert.equal(lucideCalls, initialLucide + 1);
});

test("Issue #2e: maintenance-page 45s 后台轮询静默对比快照，仅 generated_at 变化时零重绘", async () => {
  const source = read("pages", "陪伴小屋", "maintenance-page.js");
  let intervalCb = null;
  let generatedAt = "2026-11-21T08:00:00";
  let callsCount = 3;
  let htmlWrites = 0, htmlValue = "", lucideCalls = 0;
  const updatedNode = { textContent: "" };

  const bridge = {
    apiGet: async rawRoute => {
      const route = rawRoute.startsWith("/") ? rawRoute : "/" + rawRoute;
      if (route.endsWith("/maintenance")) {
        return {
          generated_at: generatedAt,
          summary: { calls: callsCount, tokens: 1200 },
          totals: { calls: callsCount, tokens: 1200 },
          daily: [{ day: "2026-11-21", calls: callsCount, tokens: 1200, providers: [], tasks: [] }],
          history: [],
          recent: [],
          budget: {},
          reply_efficiency: { generated_at: generatedAt, window_start: "2026-11-14T08:00:00", summary: { samples: 0 }, daily: [], recent: [] },
        };
      }
      if (route.endsWith("/balance")) return { configured: false, available: false, state: "not_configured" };
      if (route.endsWith("/voice")) return { providers: [] };
      if (route.endsWith("/image")) return { actions: [] };
      return { tasks: [], providers: [] };
    },
    apiPost: async () => ({}),
  };

  const window = {
    CompanionProduct: {
      role: () => ({ model_api: "/astrbot_plugin_private_companion/model", config_api: "/astrbot_plugin_private_companion/config", role_id: "role-a" }),
      bridgeNow: () => bridge,
    },
    lucide: { createIcons() { lucideCalls += 1; } },
    setInterval: (cb, ms) => { assert.equal(ms, 45000); intervalCb = cb; return 1; },
    clearInterval() {},
    setTimeout: () => 1,
    clearTimeout() {},
  };
  const document = { visibilityState: "visible" };
  const root = {
    get innerHTML() { return htmlValue; },
    set innerHTML(v) { htmlWrites += 1; htmlValue = v; },
    addEventListener() {}, removeEventListener() {},
    querySelector: selector => selector === ".maint-updated" ? updatedNode : null,
    querySelectorAll: () => [],
  };

  vm.runInNewContext(source, {
    window, document, URL, Blob, Intl, Date, Number, Object, String, Math, Array, Set, Promise, URLSearchParams, JSON,
  });

  const page = window.CompanionMaintenancePage;
  page.mount(root);
  await settle();

  assert.ok(intervalCb, "45s maintenance polling interval must be registered");
  const writesAfterMount = htmlWrites;
  const lucideAfterMount = lucideCalls;

  // Background 45s poll with identical payload except new generated_at / window_start: ZERO DOM rebuild.
  generatedAt = "2026-11-21T08:00:45";
  intervalCb();
  await settle();
  assert.equal(htmlWrites, writesAfterMount, "unchanged maintenance poll must not rebuild root.innerHTML");
  assert.equal(lucideCalls, lucideAfterMount, "unchanged maintenance poll must not re-scan lucide icons");
  assert.match(updatedNode.textContent, /同步于 /);

  // Background 45s poll with genuine metric change renders ONCE (no pre-fetch loading flash).
  generatedAt = "2026-11-21T08:01:30";
  callsCount = 4;
  intervalCb();
  await settle();
  assert.equal(htmlWrites, writesAfterMount + 1);
  assert.equal(lucideCalls, lucideAfterMount + 1);

  page.unmount();
});

test("Issue #2f: maintenance-page 轻量结构指纹零 JSON.stringify，且 45s 瞬时失败保留快照不掉离线页", async () => {
  const source = read("pages", "陪伴小屋", "maintenance-page.js");
  let intervalCb = null;
  let generatedAt = "2026-11-21T08:00:00";
  let spentTokens = 1200;
  let failNext = null;
  let htmlWrites = 0, htmlValue = "", lucideCalls = 0, stringifyCalls = 0;
  const updatedNode = { textContent: "" };
  const heavyEvents = Array.from({ length: 120 }, (_, i) => ({
    at: "2026-11-21T07:59:00",
    kind: "cache_miss",
    reason: "prefix_changed",
    signature: { context_hashes: Array.from({ length: 64 }, (__, j) => `h-${i}-${j}`) },
  }));

  const bridge = {
    apiGet: async rawRoute => {
      const route = rawRoute.startsWith("/") ? rawRoute : "/" + rawRoute;
      if (route.endsWith("/maintenance")) {
        if (failNext) {
          const err = failNext;
          failNext = null;
          throw err;
        }
        return {
          generated_at: generatedAt,
          summary: { calls: 3, tokens: 4500 },
          totals: { calls: 3, tokens: 4500 },
          daily: [{ day: "2026-11-21", calls: 3, tokens: 4500, providers: [], tasks: [] }],
          history: [{ day: "2026-11-21", calls: 3, tokens: 4500, providers: [], tasks: [] }],
          recent: [{ at: "2026-11-21T07:58:00", kind: "conversation.reply", provider: "<main>", state: "generated", tokens: 1500 }],
          budget: { hard_limit_enabled: true, limit_tokens: 10000, spent_tokens: spentTokens, held_tokens: 0 },
          cache_diagnostics: { daily: [{ day: "2026-11-21", signature_calls: 12, baseline_calls: 10 }], recent_events: heavyEvents },
          reply_efficiency: { generated_at: generatedAt, window_start: "2026-11-14T08:00:00", summary: { samples: 1 }, daily: [], recent: [] },
        };
      }
      if (route.endsWith("/balance")) return { configured: false, available: false, state: "not_configured" };
      if (route.endsWith("/voice")) return { providers: [] };
      if (route.endsWith("/image")) return { actions: [] };
      return { tasks: [], providers: [] };
    },
    apiPost: async () => ({}),
  };

  const window = {
    CompanionProduct: {
      role: () => ({ model_api: "/astrbot_plugin_private_companion/model", config_api: "/astrbot_plugin_private_companion/config", role_id: "role-a" }),
      bridgeNow: () => bridge,
    },
    lucide: { createIcons() { lucideCalls += 1; } },
    setInterval: (cb, ms) => { assert.equal(ms, 45000); intervalCb = cb; return 1; },
    clearInterval() {},
    setTimeout: () => 1,
    clearTimeout() {},
  };
  const document = { visibilityState: "visible" };
  const root = {
    get innerHTML() { return htmlValue; },
    set innerHTML(v) { htmlWrites += 1; htmlValue = v; },
    addEventListener() {}, removeEventListener() {},
    querySelector: selector => selector === ".maint-updated" ? updatedNode : null,
    querySelectorAll: () => [],
  };
  const trackedJson = {
    parse: (...args) => JSON.parse(...args),
    stringify: (...args) => { stringifyCalls += 1; return JSON.stringify(...args); },
  };

  vm.runInNewContext(source, {
    window, document, URL, Blob, Intl, Date, Number, Object, String, Math, Array, Set, Promise, URLSearchParams,
    JSON: trackedJson,
  });

  const page = window.CompanionMaintenancePage;
  page.mount(root);
  await settle();

  const writesAfterMount = htmlWrites;
  const lucideAfterMount = lucideCalls;

  // Unchanged 45s poll on heavy payload: 0 JSON.stringify calls, 0 DOM rebuilds.
  generatedAt = "2026-11-21T08:00:45";
  intervalCb();
  await settle();
  assert.equal(stringifyCalls, 0, "lightweight maintenanceSnapshot must never call JSON.stringify on heavy payload");
  assert.equal(htmlWrites, writesAfterMount);
  assert.equal(lucideCalls, lucideAfterMount);

  // Transient 45s poll failure (bridge_timeout): preserves dashboard, renders 同步异常 + inline retry notice once.
  failNext = Object.assign(new Error("timeout of 30000ms exceeded"), { code: "bridge_timeout" });
  intervalCb();
  await settle();
  assert.equal(htmlWrites, writesAfterMount + 1);
  assert.match(htmlValue, /4,500/);
  assert.match(htmlValue, /data-state="warn"><i><\/i>同步异常/);
  assert.match(htmlValue, /data-maint-sync-notice/);
  assert.match(htmlValue, /读取超时（超过 30 秒）/);
  assert.doesNotMatch(htmlValue, /维护面板暂时离线/);

  // Budget change + recovery triggers single re-render and clears error notice.
  spentTokens = 2600;
  generatedAt = "2026-11-21T08:01:30";
  intervalCb();
  await settle();
  assert.equal(htmlWrites, writesAfterMount + 2);
  assert.match(htmlValue, /data-state="ok"><i><\/i>已连接/);
  assert.doesNotMatch(htmlValue, /data-maint-sync-notice/);
  assert.equal(stringifyCalls, 0);

  page.unmount();
});
