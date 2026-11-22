/* 「主动与联系」整页排版深度重构契约测试。
 *
 * 守住以下布局与交互不变量：
 *   1. 顶部横向分段标签栏替代第 3 层竖向侧栏（零 206px 挤压、草木绿胶囊态、键盘左右/首尾导航、8 个 data-view 完整）；
 *   2. 底部为悬浮迷你小屋留足安全滚动空间（零硬编码 px 间距，兼容尺度审计）；
 *   3. 外部触发源 8 组全部采用通栏结构化卡片 + 子块分组 + 2 列对齐网格，宽文本满宽不截断，行内控件严格右对齐，主开关关闭时子项柔和降权；
 *   4. 额度与去重、晨间唤醒、消息测试、开口闸门与主动检视均消除高度错位空洞与标签挤折；
 *   5. 100% 保留 data-view / data-field / data-source / data-purpose 等搜索联动锚点。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const PAGE_DIR = path.join(ROOT, "pages", "陪伴小屋");
const JS = fs.readFileSync(path.join(PAGE_DIR, "contact-page.js"), "utf8");
const CSS = fs.readFileSync(path.join(PAGE_DIR, "contact-page.css"), "utf8");
const CLEAN_CSS = CSS.replace(/\/\*[\s\S]*?\*\//g, "");

const HOST = "astrbot_plugin_private_companion";
const flush = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };

function mountContactPage(settingsOverrides = {}) {
  const requests = [];
  const baseSettings = {
    enabled: true, automatic_enabled: true, quiet_enabled: true,
    quiet_start: "23:00", quiet_end: "07:00", quiet_timezone: "Asia/Shanghai",
    proactive_intensity_preset: "balanced", proactive_hour_activity_enabled: true,
    proactive_hour_activity_curve: new Array(24).fill("1.00").join(","),
    interval_seconds: 3600, idle_seconds: 900, max_contacts_24h: 3,
    enable_daily_greetings: true, greeting_morning_window: "07:45-10:20",
    morning_chain_enabled: true, routine_learning_enabled: true, morning_nudge_enabled: false,
    dedup_enabled: false, dedup_last_message_enabled: true, enable_proactive_burst: false,
    weather_checkin_enabled: false, weather_change_enabled: true,
    official_weather_alert_enabled: false,
    news_share_enabled: true, news_daily_hot_read_enabled: false, news_hot_sources: "weibo,hackernews",
    meal_checkin_enabled: false, date_checkin_enabled: true,
    web_exploration_enabled: false, external_event_self_link_enabled: true,
    screen_glance_enabled: false, screen_glance_goodnight_enabled: false, screen_glance_followup_enabled: false,
    voice_action_enabled: true, poke_action_enabled: false,
    enable_llm_proactive_persona_judge: false,
    balance_awareness_enabled: false,
    ...settingsOverrides,
  };
  const window = {
    navigator: {clipboard: {writeText: async () => {}}},
    CompanionProduct: {
      hosted: () => true,
      scopeKey: () => JSON.stringify(["install-1", "target-1", "role-1"]),
      identity: () => ({installation_id: "install-1", operator_id: "admin-1"}),
      role: () => ({role_id: "role-1", perception_native_api: "/" + HOST + "/perception-native", config_api: "/" + HOST + "/config"}),
      bridgeNow: () => window.AstrBotPluginPage,
      bridgeFor: () => Promise.resolve(window.AstrBotPluginPage),
    },
    AstrBotPluginPage: {
      ready: () => Promise.resolve({pluginName: HOST}),
      apiGet: (route, params) => new Promise((resolve) => {
        requests.push({method: "GET", route, params});
        if (route === "config") {
          resolve({status: "ok", data: {sections: {"proactive.contact": {
            edit_token: "a".repeat(64), scope: "installation", settings: baseSettings, fields: {},
          }}}});
          return;
        }
        if (route === "setup/proactive-recipients") {
          resolve({status: "ok", data: {candidates: [{target_id: "core-1", label: "核心管理员 · QQ"}], selected_target_id: "core-1"}});
          return;
        }
        resolve({status: "ok", data: {state: "ready", items: [], count: 0, shared_count: 0, trace_count: 0, history_count: 0}});
      }),
      apiPost: () => Promise.resolve({status: "ok", data: {}}),
    },
    document: {
      addEventListener: () => {},
      querySelector: () => null,
      querySelectorAll: () => [],
      getElementById: () => null,
      documentElement: {dataset: {}},
      body: {classList: {add: () => {}, remove: () => {}, toggle: () => {}}},
      title: "",
    },
    history: {state: null, replaceState: () => {}},
    matchMedia: () => ({matches: false}),
    console, Intl, Date, JSON, Math, Number, Object, Array, String, Set, Map, Boolean, Error, Promise,
    localStorage: {getItem: () => null, setItem: () => {}, removeItem: () => {}},
  };
  const sessionStorage = {getItem: () => null, setItem: () => {}, removeItem: () => {}};
  vm.runInNewContext(JS, {
    window, document: window.document, navigator: window.navigator, console, Intl, Date, JSON, Math,
    Number, Object, Array, String, Set, Map, Boolean, Error, Promise,
    localStorage: window.localStorage, sessionStorage, URL, matchMedia: () => ({matches: false}),
    setTimeout, clearTimeout, setInterval, clearInterval,
  });
  const handlers = {};
  const focused = [];
  const host = {
    isConnected: true,
    innerHTML: "",
    addEventListener(type, fn) { handlers[type] = fn; },
    removeEventListener() {},
    contains() { return true; },
    querySelector(selector) {
      if (selector.startsWith(".ct-rail [data-view=")) {
        return {focus() { focused.push(selector); }};
      }
      return null;
    },
    querySelectorAll() { return []; },
    dispatch(type, event) { return handlers[type]?.(event); },
  };
  return {page: window.CompanionContactPage, host, requests, focused};
}

test("顶部横向分段标签栏：移除第 3 层竖向侧栏、保留 8 个视图与键盘左右/首尾导航", async () => {
  assert.match(CLEAN_CSS, /\.ct-app\{[^}]*grid-template-columns:minmax\(0,1fr\)/,
    ".ct-app 应改为单列垂直布局，彻底取消 206px 第 3 层竖向侧栏");
  assert.doesNotMatch(CLEAN_CSS, /\.ct-app\{[^}]*206px/,
    ".ct-app 不得保留旧版 206px 竖向导航列宽");
  assert.doesNotMatch(CLEAN_CSS, /\.ct-rail::before/,
    ".ct-rail 不得残留竖向时间线伪元素");
  assert.match(CLEAN_CSS, /\.ct-rail\{[^}]*display:flex;flex-wrap:wrap/,
    ".ct-rail 应升级为顶部横向分段标签栏");
  assert.match(CLEAN_CSS, /\.ct-rail button\[aria-pressed="true"\]\{[^}]*background:var\(--t-accent-quiet\)[^}]*color:var\(--t-accent-ink\)/s,
    "选中标签应使用柔和草木绿胶囊语义令牌");
  assert.match(CLEAN_CSS, /\.ct-rail button:focus-visible\{[^}]*outline:2px solid var\(--t-focus\)/,
    "顶部标签按钮必须具备清晰键盘焦点环");
  assert.match(CLEAN_CSS, /@container contact-workspace \(max-width:820px\)[\s\S]*?\.ct-rail\{display:grid;grid-template-columns:repeat\(4,minmax\(0,1fr\)\)\}/,
    "中屏容器下 8 个标签应自动折叠为 4×2 均衡网格");
  assert.match(CLEAN_CSS, /@container contact-workspace \(max-width:560px\)[\s\S]*?\.ct-rail\{[^}]*overflow-x:auto/,
    "窄屏容器下标签栏应平滑横向滚动");
  assert.match(CLEAN_CSS, /\.ct-main\{[^}]*padding-bottom:calc\(var\(--shell-pip-h,calc\(var\(--sp-8\) \* 2\)\) \+ var\(--sp-5\)\)/,
    ".ct-main 底部必须基于尺度令牌与画中画高度留出 >= 112px 安全空间");

  const {page, host, focused} = mountContactPage();
  try {
    await page.mount(host);
    await settle();
    const navHtml = host.innerHTML.match(/<nav class="ct-rail"[\s\S]*?<\/nav>/)?.[0] || "";
    const viewButtons = [...navHtml.matchAll(/data-view="([^"]+)"/g)].map(m => m[1]);
    assert.deepEqual(viewButtons, ["dial", "morning", "tests", "purposes", "gates", "budgets", "sources", "materials"]);

    const keyEvent = key => ({
      key,
      metaKey: false,
      ctrlKey: false,
      preventDefault() {},
      target: {closest: sel => sel === ".ct-rail [data-view]" ? {dataset: {view: page.state.data.view}} : null},
    });
    host.dispatch("keydown", keyEvent("ArrowRight"));
    assert.equal(page.state.data.view, "morning");
    assert.equal(focused.at(-1), '.ct-rail [data-view="morning"]');

    host.dispatch("keydown", keyEvent("End"));
    assert.equal(page.state.data.view, "materials");
    assert.equal(focused.at(-1), '.ct-rail [data-view="materials"]');

    host.dispatch("keydown", keyEvent("Home"));
    assert.equal(page.state.data.view, "dial");

    host.dispatch("keydown", keyEvent("ArrowLeft"));
    assert.equal(page.state.data.view, "materials");
  } finally {
    page.unmount();
  }
});

test("外部触发源：全通栏卡片、清晰子块分组、主开关关闭柔和降权、控件右对齐与宽文本不截断", async () => {
  assert.match(CLEAN_CSS, /\.ct-sources\{[^}]*grid-template-columns:minmax\(0,1fr\)/,
    ".ct-sources 全部 8 组应为通栏结构化卡片，杜绝相邻卡片高低错位空洞");
  assert.match(CLEAN_CSS, /\.ct-src-group\{[^}]*container-name:ct-src-group/,
    ".ct-src-group 应声明独立容器以便内部 2 列网格自适应折叠");
  assert.match(CLEAN_CSS, /\.ct-src-control\{[^}]*justify-content:flex-end[^}]*justify-self:end/s,
    "行内控件容器必须统一右对齐");
  assert.match(CLEAN_CSS, /\.ct-src-row\.is-stacked input\[type="text"\]\{[^}]*width:100%/,
    "宽文本输入框必须满宽展示，绝不裁切 weibo,hackernews 等配置");
  assert.match(CLEAN_CSS, /\.ct-src-row\.is-compact input\[type="text"\]\{[^}]*width:104px/,
    "时刻/短文本输入框应使用固定宽度，防止百分比混入导致浏览器 auto 列居中漂移");
  assert.match(CLEAN_CSS, /\.ct-src-section\.is-branch-off > \.ct-src-section-fields\{opacity:\.62\}/,
    "主开关关闭时从属子项应柔和降权");

  const {page, host} = mountContactPage();
  try {
    await page.mount(host);
    await settle();
    host.dispatch("click", {target: {closest: sel => sel === "[data-view]" ? {dataset: {view: "sources"}} : null}});
    const html = host.innerHTML;

    for (const groupId of ["weather", "weather-alert", "news", "meal", "explore", "screen", "context", "balance"]) {
      assert.ok(html.includes(`data-source="${groupId}"`), `缺少触发源卡片 ${groupId}`);
    }
    assert.ok((html.match(/class="ct-src-section[" ]/g) || []).length >= 12,
      "触发源应按独立功能清晰拆分为多个结构化子块");

    const newsBlock = html.split('data-source="news"')[1].split("</section>")[0];
    assert.equal((newsBlock.match(/class="ct-src-section[" ]/g) || []).length, 2,
      "资讯卡片应将「分享资讯」与「每日热点」拆分为 2 个独立子块");
    assert.match(newsBlock, /class="ct-src-section is-branch-off"[\s\S]*?data-field="news_daily_hot_read_enabled"[\s\S]*?未启用 · 以下子项暂不生效[\s\S]*?data-field="news_hot_sources"/,
      "每日热点关闭时其子块应标记为 is-branch-off 且提示子项暂不生效");
    for (const wideField of ["news_source_uid", "news_source_url", "news_hot_sources"]) {
      assert.match(newsBlock, new RegExp(`class="ct-src-row is-stacked[^"]*"[^>]*>[\\s\\S]*?data-field="${wideField}"`),
        `${wideField} 必须采用 .is-stacked 标签在上、满宽输入在下版式`);
    }

    const mealBlock = html.split('data-source="meal"')[1].split("</section>")[0];
    assert.equal((mealBlock.match(/class="ct-src-section[" ]/g) || []).length, 2,
      "饭点与生日纪念日应拆分为 2 个独立子块");
    assert.match(mealBlock, /class="ct-src-section is-branch-off"[\s\S]*?data-field="meal_checkin_enabled"/,
      "饭点话题关闭时对应子块应柔和降权");
    assert.match(mealBlock, /class="ct-src-section"[\s\S]*?data-field="date_checkin_enabled" checked>/,
      "生日与纪念日问候开启时对应子块保持高亮");
    for (const clockField of ["meal_lunch_time", "meal_dinner_time", "date_checkin_time"]) {
      assert.match(mealBlock, new RegExp(`class="ct-src-row is-compact[^"]*"[^>]*>[\\s\\S]*?data-field="${clockField}"`),
        `${clockField} 应采用 .is-compact 紧凑定宽右对齐`);
    }

    const exploreBlock = html.split('data-source="explore"')[1].split("</section>")[0];
    assert.match(exploreBlock, /class="ct-src-row is-stacked is-span[^"]*"[^>]*>[\s\S]*?data-field="web_exploration_interests"/,
      "关注方向应采用满宽通栏输入");

    const screenBlock = html.split('data-source="screen"')[1].split("</section>")[0];
    assert.equal((screenBlock.match(/class="ct-src-section[" ]/g) || []).length, 3,
      "屏幕与动作应按屏幕观察 / 允许发语音 / 允许戳一戳拆为 3 个独立子块");
  } finally {
    page.unmount();
  }
});

test("其余子视图精修：额度与去重通栏分层、页头徽标、消息测试与开口闸门视觉细节到位", async () => {
  assert.match(CLEAN_CSS, /\.ct-badge\{[^}]*border-radius:var\(--r-pill\)/,
    ".ct-badge 应有清晰胶囊样式而非裸文本");
  assert.match(CLEAN_CSS, /\.ct-budgets\{[^}]*grid-template-columns:minmax\(0,1fr\)/,
    ".ct-budgets 应采用通栏结构化卡片，消除每日额度下方大片空白");
  assert.match(CLEAN_CSS, /\.ct-test-recipient\{[^}]*border:1px solid var\(--t-line\)[^}]*border-radius:var\(--r-md\)/s,
    ".ct-test-recipient 应具象为统一卡片");
  assert.match(CLEAN_CSS, /\.ct-test-details>div\{[^}]*grid-template-columns:96px minmax\(0,1fr\)/,
    "消息测试详情标签列宽应足够容纳「生产器验收场景」不拆字");
  assert.match(CLEAN_CSS, /@container ct-main \(min-width:700px\) and \(max-width:1059px\)[\s\S]*?\.ct-gates > \.ct-gate:last-of-type:nth-of-type\(odd\)\{grid-column:1 \/ -1\}/,
    "2 列容器下 5 道开口闸门尾项应自动通栏，避免 2+2+1 孤儿留白");
  assert.match(CLEAN_CSS, /@container ct-block \(max-width:520px\)[\s\S]*?\.ct-block\.is-wake > \.ct-field/,
    "晨间唤醒「清醒确认」应支持容器级窄宽折行");

  const {page, host} = mountContactPage();
  try {
    await page.mount(host);
    await settle();
    host.dispatch("click", {target: {closest: sel => sel === "[data-view]" ? {dataset: {view: "budgets"}} : null}});
    const html = host.innerHTML;
    assert.match(html, /class="ct-budget-sliders is-branch-off"/, "重复抑制关闭时子滑块应柔和降权");
    assert.match(html, /class="ct-budget-sub is-branch-off"[\s\S]*?未启用 · 以下连发参数暂不生效/,
      "允许连发关闭时连发参数子块应柔和降权");
  } finally {
    page.unmount();
  }
});
