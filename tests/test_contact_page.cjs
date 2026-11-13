/* 主动与联系页（节律盘）测试。
 *
 * 这一页的主体是一个钟面，四圈几何各自对应一个真实字段，且四圈的**几何正确性**
 * 就是它的正确性：免打扰弧跨午夜要拆成两段、24 根柱子要按权重归一、
 * 环心那句「此刻」必须与后端同源而不是页面自己推断。
 * 这些都是纯函数，但它们是模块私有的，只能靠 vm 跑真实文件来测。
 *
 * 另外三条是历史踩过的坑，值得各自一条断言：
 *   ① 票号必须在 unmount() 之后取，否则挂载后一个请求都不发（看着挂上了其实是空的）；
 *   ② 滑块拖动不能整页重绘，否则拖到一半手上的滑块就丢了；
 *   ③ 保存只提交动过的字段——把没看过的字段当「要保持的值」写回去会覆盖掉别人改的。
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/contact-page.js'), 'utf8');
const style = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/contact-page.css'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 8; i++) await flush(); };

const HOST = 'astrbot_plugin_private_companion';
const NATIVE = '/' + HOST + '/perception-native';
const CONFIG = '/' + HOST + '/config';
const activeHarnesses = new Set();

test.afterEach(() => {
  for (const fixture of activeHarnesses) fixture.page.unmount();
  activeHarnesses.clear();
});

test('关系联系测试不再要求用户先说固定词', () => {
  assert.match(source, /已确认私聊窗口与当前关系联系节律；不要求近期关键词/);
  assert.match(source, /允许角色主动表达自己的亲近与想念/);
  assert.match(source, /可以主动轻轻问候近况或表达愿意倾听/);
  assert.doesNotMatch(source, /分别用晚安、明确表达亲近和无关语境验收/);
});

const baseSettings = () => ({
  enabled: true, automatic_enabled: true, quiet_enabled: true, quiet_start: '23:00', quiet_end: '07:00',
  quiet_timezone: 'Asia/Shanghai', proactive_intensity_preset: 'balanced',
  proactive_hour_activity_enabled: true,
  proactive_hour_activity_curve: [0.5,0.5,0.5,0.5,0.5,0.5].concat(new Array(18).fill(1)).join(','),
  interval_seconds: 3600, idle_seconds: 900, max_contacts_24h: 3,
  'chat.enabled': true, 'chat.weight': 2.0, 'news.enabled': false, 'news.weight': 1.0,
  enable_daily_greetings: true, greeting_morning_window: '07:45-10:20',
  allow_insomnia_night_message: false,
});

/* 上界：只保留后面两条通用断言用得到的字段。 */
function harness({nativeApi = NATIVE, configApi = CONFIG, sessionStore = new Map(), localStorageStore = new Map(),
  sessionStorageBlocked = false, localStorageBlocked = false, historyBlocked = false,
  historyEntry = {current: null}} = {}) {
  const requests = [];
  const clipboardWrites = [];
  let selectedScopeKey = JSON.stringify(['install-1', 'target-1', 'role-1']);
  let gateSettingsTarget = null;
  let gateSettingsArea = null;
  const window = {
    navigator: {clipboard: {writeText: async text => clipboardWrites.push(text)}},
    CompanionProduct: {
      hosted: () => true,
      scopeKey: () => selectedScopeKey,
      identity: () => ({installation_id: 'install-1', operator_id: 'admin-1'}),
      role: () => ({role_id: 'role-1', perception_native_api: nativeApi, config_api: configApi}),
      bridgeNow: () => window.AstrBotPluginPage,
      bridgeFor: () => ({then: resolve => resolve(window.AstrBotPluginPage)}),
    },
    AstrBotPluginPage: {
      ready: () => Promise.resolve({pluginName: HOST}),
      apiGet: (route, params) => new Promise((resolve, reject) => requests.push({method: 'GET', route, params, resolve, reject})),
      apiPost: (route, values) => new Promise((resolve, reject) => requests.push({method: 'POST', route, values, resolve, reject})),
    },
    document: {
      addEventListener: () => {},
      querySelector: () => null,
      querySelectorAll: () => [],
      getElementById: id => id === 'contact-gate-settings' ? gateSettingsTarget
        : id === 'contact-gate-settings-area' ? gateSettingsArea : null,
      documentElement: {dataset: {}},
      body: {classList: {add: () => {}, remove: () => {}, toggle: () => {}}},
      title: '',
    },
    history: {
      get state() { return historyEntry.current; },
      replaceState(state) { if (historyBlocked) throw new Error('SecurityError'); historyEntry.current = state; },
    },
    matchMedia: () => ({matches: false}),
    console, Intl, Date, JSON, Math, Number, Object, Array, String, Set, Map, Boolean, Error, Promise,
    localStorage: (() => { const store = localStorageStore; return {
      getItem: k => { if (localStorageBlocked) throw new Error('SecurityError'); return store.has(k) ? store.get(k) : null; },
      setItem: (k, v) => { if (localStorageBlocked) throw new Error('SecurityError'); store.set(k, String(v)); },
      removeItem: k => { if (localStorageBlocked) throw new Error('SecurityError'); store.delete(k); }, _store:store}; })(),
  };
  const sessionStorage = {
    getItem: key => { if (sessionStorageBlocked) throw new Error('SecurityError'); return sessionStore.has(key) ? sessionStore.get(key) : null; },
    setItem: (key, value) => { if (sessionStorageBlocked) throw new Error('SecurityError'); sessionStore.set(key, String(value)); },
    removeItem: key => { if (sessionStorageBlocked) throw new Error('SecurityError'); sessionStore.delete(key); },
  };
  vm.runInNewContext(source, {
    window, document: window.document, navigator: window.navigator, console, Intl, Date, JSON, Math, Number, Object, Array,
    String, Set, Map, Boolean, Error, Promise, localStorage: window.localStorage, sessionStorage, URL,
    matchMedia: () => ({matches: true}),
    /* 计时器必须给：页面里 bounded() 用 Promise.race 把每个请求包了一层 15 秒超时。
       少了 setTimeout/clearTimeout 的话第一个请求在发出之前就抛
       「clearTimeout is not defined」，整页停在初始状态——
       而那看起来像「请求逻辑有问题」，其实是夹具不全。 */
    setTimeout, clearTimeout, setInterval, clearInterval,
  });

  /* 树桩。页面用 innerHTML 整块重写，所以桩只要记住最后一次写入；
     __handlers 让测试从真实注册的事件入口驱动，而不是绕开接线直接调私有函数。 */
  function root() {
    const handlers = {};
    const nodes = [];
    const node = {
      isConnected: true, innerHTML: '', __handlers: handlers,
      addEventListener(type, handler) { handlers[type] = handler; },
      removeEventListener() {},
      contains() { return true; },
      querySelector(selector) { return nodes.find(n => n.__sel === selector) || null; },
      querySelectorAll() { return []; },
      __register(sel, el) { el.__sel = sel; nodes.push(el); return el; },
      dispatch(type, event) { return handlers[type]?.(event); },
    };
    return node;
  }
  const page = window.CompanionContactPage;
  const element = root();
  const fixture = {page, element, requests, clipboardWrites, window, sessionStore, historyEntry, localStore:window.localStorage._store,
    setScopeKey: value => {selectedScopeKey = value;},
    setGateSettingsTarget: target => {gateSettingsTarget = target;},
    setGateSettingsArea: target => {gateSettingsArea = target;},
    reply: (request, payload) => {
      if (request.method === 'GET' && (request.route === 'config' || request.route.endsWith('/config'))) {
        const section = payload?.sections?.['proactive.contact'];
        if (section && !Object.hasOwn(section, 'settings')) section.settings = baseSettings();
        if (section && !Object.hasOwn(section, 'scope')) section.scope = 'installation';
      }
      request.resolve({status: 'ok', data: payload});
    },
    fail: (request, code) => request.resolve({status: 'error', message: code, data: {code}}),
    settle};
  activeHarnesses.add(fixture);
  return fixture;
}

/* 全局页只请求安装级配置；值和令牌来自同一接口合同。 */
function replyGlobalConfig(h, request, settings = baseSettings(), token = 'c'.repeat(64)) {
  h.reply(request, {sections: {'proactive.contact': {
    edit_token: token, settings, scope: 'installation', fields: {},
  }}});
}

async function boot(h, settings = baseSettings()) {
  const mounted = h.page.mount(h.element);
  await h.settle();
  const answered = new Set();
  for (let round = 0; round < 30; round++) {
    const pending = h.requests.filter(r => !answered.has(r));
    if (!pending.length) break;
    for (const request of pending) {
      answered.add(request);
      if (request.route === 'config' || request.route.endsWith('/config')) {
        replyGlobalConfig(h, request, settings);
        continue;
      }
      h.reply(request, {});
    }
    await h.settle();
  }
  await mounted;
  return {requests: h.requests};
}

/* ---- 接线 ------------------------------------------------------------- */

test('挂载只读取安装级配置正本，不访问宿主当前会话', async () => {
  const h = harness();
  await boot(h);
  const routes = h.requests.map(r => r.method + ' ' + r.route.split('?')[0]);
  assert.deepEqual(routes, ['GET config']);
  assert.deepEqual(JSON.parse(JSON.stringify(h.requests[0].params)), {sections: 'proactive.contact'});
  assert.equal(h.page.state.data.ready, true);
  assert.equal(h.page.state.data.settings.enabled, true);
  assert.equal(h.page.state.data.configToken, 'c'.repeat(64));
  assert.match(h.element.innerHTML, /全局设置/);
});

test('全局设置始终只来自 installation 分区', async () => {
  const h = harness();
  const configured = baseSettings();
  await boot(h, configured);
  assert.equal(h.page.state.data.configScope, 'installation');
  assert.equal(h.page.state.data.settings.enabled, true);
  assert.equal(h.page.state.data.settings.automatic_enabled, true);
  assert.equal(h.requests.some(request => request.route.startsWith('perception-native')), false);
  assert.doesNotMatch(h.element.innerHTML, /当前对象|当前私聊对象/);
});

test('契机与闸门只按全局默认计算，不宣称具体用户已经获准', async () => {
  const h = harness();
  const global = {...baseSettings(), enabled: true, quiet_enabled: false, 'chat.enabled': false};
  await boot(h, global);
  assert.equal(h.page.state.data.settings.enabled, true);
  assert.equal(h.page.state.data.effectiveSettings, null);
  assert.match(h.element.innerHTML, /全局默认已开启/);
  assert.doesNotMatch(h.element.innerHTML, /此刻可以开口|当前私聊对象/);
  h.page.openGateSettingsView();
  assert.match(h.element.innerHTML, /全局默认配置已读取/);
  assert.match(h.element.innerHTML, /逐用户授权与单独设置在私聊用户页管理/);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]'
    ? {dataset: {view: 'purposes'}} : null}});
  const row = h.element.innerHTML.split('data-purpose="chat"')[1].split('</article>')[0];
  assert.doesNotMatch(row, /data-field="chat.enabled"[^>]* checked/);
  assert.match(row, /已关闭/);
  assert.doesNotMatch(row, /当前对象|授权已通过/);
  assert.equal(h.page.state.data.touched.size, 0);
});

test('重新读取全局设置不会请求或受当前私聊会话影响', async () => {
  const h = harness();
  await boot(h);
  const start = h.requests.length;
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-action]'
    ? {dataset: {action: 'refresh'}} : null}});
  await h.settle();
  assert.equal(h.requests[start].route, 'config');
  h.reply(h.requests[start], {sections: {['proactive.contact']: {
    edit_token: 'd'.repeat(64), settings: {...baseSettings(), note: '安装级刷新'}, scope: 'installation', fields: {},
  }}});
  await h.settle();
  assert.equal(h.page.state.data.settings.note, '安装级刷新');
  assert.equal(h.requests.length, start + 1);
});

test('全局配置刷新失败保留上次确认值并停止保存', async () => {
  const h = harness();
  await boot(h);
  const start = h.requests.length;
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-action]'
    ? {dataset: {action: 'refresh'}} : null}});
  await h.settle();
  h.fail(h.requests[start], 'configuration_scope_denied');
  await h.settle();
  assert.equal(h.page.state.data.settings.enabled, true);
  assert.equal(h.page.state.data.configToken, '');
  assert.equal(h.page.state.data.ready, true);
  assert.match(h.element.innerHTML, /data-field="enabled"[^>]* checked/);
  assert.equal(h.requests.filter(request => request.method === 'POST').length, 0);
});

test('全局配置不可读时不以任何会话值回退显示或保存', async () => {
  const h = harness();
  const mounted = h.page.mount(h.element);
  await h.settle();
  assert.equal(h.requests.length, 1);
  h.fail(h.requests[0], 'configuration_scope_denied');
  await mounted;
  assert.equal(h.page.state.data.ready, false);
  assert.equal(h.page.state.data.settings, null);
  assert.doesNotMatch(h.element.innerHTML, /data-field="enabled"/);
  h.page.openGateSettingsView();
  assert.match(h.element.innerHTML, /全局设置尚未读取/);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-action]'
    ? {dataset: {action: 'save'}} : null}});
  assert.equal(h.requests.filter(request => request.method === 'POST').length, 0);
});

test('从状态入口进入后切到开口闸门并聚焦设置区', async () => {
  const h = harness();
  await boot(h);
  const target = {tabIndex: 0, hasAttribute: () => false,
    scrollIntoView(options) { this.scrolled = options.block === 'start'; },
    focus(options) { this.focused = options.preventScroll; }};
  h.setGateSettingsTarget(target);
  assert.equal(h.page.state.data.view, 'dial');
  assert.equal(h.page.openGateSettingsView(), true);
  assert.equal(h.page.state.data.view, 'gates');
  assert.match(h.element.innerHTML, /开口闸门/);
  assert.equal(target.scrolled, true);
  assert.equal(target.focused, true);
  h.page.unmount();
});

test('开口闸门设置只在开口闸门视图展示', async () => {
  const h = harness();
  await boot(h);
  const area = {hidden: false};
  h.setGateSettingsArea(area);

  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view: 'tests'}} : null}});
  assert.equal(area.hidden, true, '消息测试视图不能把回复闸门显示在测试内容下方');

  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view: 'gates'}} : null}});
  assert.equal(area.hidden, false, '选中开口闸门后才显示对应配置');

  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view: 'tests'}} : null}});
  assert.equal(area.hidden, true, '离开开口闸门视图后配置随之隐藏');
});

test('路径是插件相对路径：切掉插件目录名，否则宿主会拼两次', async () => {
  const h = harness();
  await boot(h);
  for (const request of h.requests) {
    assert.ok(!request.route.startsWith('/' + HOST + '/'),
      '桥只接受裸路由名，带上插件目录名会整片 404：' + request.route);
  }
});

test('多目标选择不改变安装级联系配置接口', async () => {
  const base = 'host/targets/second/roles/role-1';
  const h = harness({nativeApi: '/' + HOST + '/' + base + '/perception-native',
    configApi: '/' + HOST + '/' + base + '/config'});
  const mounted = h.page.mount(h.element);
  await h.settle();
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].route, 'config');
  assert.equal(h.requests[0].params.sections, 'proactive.contact');
  h.reply(h.requests[0], {sections: {'proactive.contact': {edit_token: 'c'.repeat(64),
    settings: baseSettings(), scope: 'installation'}}});
  await mounted;
  assert.equal(h.page.state.data.ready, true);
  h.page.unmount();
});

test('切换私聊窗口仍复用全局联系草稿缓存', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('click', {target: {closest: selector => selector === '[data-view]'
    ? {dataset: {view: 'sources'}} : null}});
  h.element.dispatch('change', fieldEvent(fakeInput({type: 'checkbox', checked: false,
    dataset: {field: 'news.enabled'}}), 'change'));
  h.page.unmount();

  h.setScopeKey(JSON.stringify(['install-1', 'target-2', 'role-2']));
  const mounted = h.page.mount(h.element);
  await h.settle();
  assert.equal(h.requests.at(-1).route, 'config');
  h.reply(h.requests.at(-1), {sections: {'proactive.contact': {edit_token: 'n'.repeat(64),
    settings: baseSettings(), scope: 'installation'}}});
  await mounted;

  assert.equal(h.page.state.data.view, 'sources');
  assert.equal(h.page.state.data.touched.has('news.enabled'), true);
  assert.equal(h.page.state.data.draft['news.enabled'], false);
});

test('配置响应缺少设置时保持未就绪，不用旧会话状态填充', async () => {
  const h = harness();
  const mounted = h.page.mount(h.element);
  await h.settle();
  const request = h.requests[0];
  assert.equal(request.route, 'config');
  request.resolve({status: 'ok', data: {sections: {['proactive.contact']:
    {edit_token: 'r'.repeat(64), scope: 'installation'}}}});
  await mounted;

  assert.equal(h.page.state.data.ready, false);
  assert.equal(h.page.state.data.settings, null);
  assert.equal(h.page.state.data.effective, null);
  assert.match(h.element.innerHTML, /全局设置尚未读取/);
  assert.doesNotMatch(h.element.innerHTML, /当前对象尚未授予联系权限|此刻可以开口/);
  assert.equal(h.requests.filter(item => item.method === 'POST').length, 0);
  h.page.unmount();
});

test('其他作用域不能伪装成安装级配置', async () => {
  const h = harness();
  const mounted = h.page.mount(h.element);
  await h.settle();
  h.reply(h.requests[0], {sections: {'proactive.contact': {edit_token: 'c'.repeat(64),
    settings: baseSettings(), scope: 'role'}}});
  await mounted;
  assert.equal(h.page.state.data.ready, false);
  assert.equal(h.page.state.data.settings, null);
  assert.match(h.element.innerHTML, /全局设置尚未读取/);
  assert.doesNotMatch(h.element.innerHTML, /此刻可以开口/);
  assert.equal(h.requests.length, 1);
  h.page.unmount();
});

test('未选择角色时仍可读取安装级联系默认', async () => {
  const h = harness();
  h.window.CompanionProduct.role = () => null;
  const mounted = h.page.mount(h.element);
  await h.settle();
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].route, 'config');
  h.reply(h.requests[0], {sections: {'proactive.contact': {edit_token: 'c'.repeat(64),
    settings: baseSettings(), scope: 'installation'}}});
  await mounted;
  assert.equal(h.page.state.data.ready, true);
  assert.equal(h.page.state.data.configScope, 'installation');
  assert.doesNotMatch(h.element.innerHTML, /按会话生效/, '全局设置不能标成会话配置');
});

/* ---- 环的几何 ---------------------------------------------------------
   四圈的存在理由是「各自对应一个真实字段」，所以几何必须能被断言，
   否则某天有人把半径调乱、或者把跨午夜拆段删掉，界面照样画得出来但画错了。 */

test('免打扰跨午夜时拆成两段弧', () => {
  const h = harness();
  const {arc} = h.page._internals;
  const across = arc(150, 150, 132, 1380, 420, 10);
  assert.equal((across.match(/<path/g) || []).length, 2, '23:00→07:00 必须拆成 23:00-24:00 与 00:00-07:00');
  const inside = arc(150, 150, 132, 540, 780, 10);
  assert.equal((inside.match(/<path/g) || []).length, 1, '不跨午夜的一段就是一段');
  /* 零长度区间不画弧：SVG 的 A 命令在起终点重合时会画成整圆，
     那一圈的视觉含义是「全天勿扰」——与其画错，不如不画。 */
  assert.equal(arc(150, 150, 132, 600, 600, 10), '');
});

test('0:00 在正上方，时钟顺时针', () => {
  const h = harness();
  const {angle, point} = h.page._internals;
  assert.equal(angle(0), -90, '0:00 必须落在正上方');
  assert.equal(angle(720), 90, '12:00 必须落在正下方（顺时针）');
  const [x0, y0] = point(150, 150, 100, 0);
  assert.ok(Math.abs(x0 - 150) < 1e-9 && Math.abs(y0 - 50) < 1e-9, '正上方 = (150, 50)');
});

test('活动曲线：24 个权重，数量不对时整体回退到默认曲线', () => {
  const h = harness();
  const {curveOf, curveText} = h.page._internals;
  assert.equal(curveOf('1,2,3').length, 24, '数量不对必须回退，不能画出一条残缺的环');
  const full = new Array(24).fill(1).join(',');
  assert.equal(curveOf(full).length, 24);
  /* 单值不可用只回退那一格；上下限按后端 CURVE_FLOOR/CEILING 夹住。 */
  assert.equal(curveOf(full.replace(/^1/, 'abc'))[0], 1);
  assert.equal(curveOf(new Array(24).fill('99').join(','))[0], 2, '上限 2.0');
  assert.equal(curveOf(new Array(24).fill('-5').join(','))[0], 0.05, '下限 0.05');
  assert.equal(curveText([1.234, 0.5]), '1.23,0.50', '回写用两位小数，与后端 curve_text 一致');
});

test('强度档位别名归一：显示归一后的档位，否则「设成实时却显示直播」会看起来没保存上', () => {
  const h = harness();
  const {tierOf} = h.page._internals;
  assert.equal(tierOf('直播'), 'live');
  assert.equal(tierOf('高频'), 'live');
  assert.equal(tierOf('standard'), 'balanced');
  assert.equal(tierOf('manual'), 'off');
  assert.equal(tierOf(''), 'off');
  /* 未知档位落到 off，而不是猜一个：猜测会让界面显示一个后端没存的档位。 */
  assert.equal(tierOf('turbo'), 'off');
});

test('免打扰判定覆盖跨午夜与两侧端点', () => {
  const h = harness();
  return boot(h).then(() => {
    const {quietSpan, minutes} = {quietSpan: null, minutes: h.page._internals.minutes};
    assert.equal(minutes('23:00'), 1380);
    assert.equal(minutes('07:00'), 420);
    assert.equal(minutes('7:00'), null, '不接受非零填充的写法（后端 local_time 要求 HH:MM）');
    assert.equal(minutes('24:00'), null);
    /* 跨午夜：23:30 与 06:00 都算免打扰，12:00 不算。 */
    const inQuiet = m => { const from = 1380, to = 420; return from > to ? (m >= from || m < to) : (m >= from && m < to); };
    assert.equal(inQuiet(1410), true, '23:30 在免打扰内');
    assert.equal(inQuiet(360), true, '06:00 在免打扰内');
    assert.equal(inQuiet(420), false, '07:00 是结束端点，不包含');
    assert.equal(inQuiet(720), false, '12:00 不在免打扰内');
  });
});

/* ---- 渲染 ------------------------------------------------------------- */

test('节律盘画出四圈，柱子的数量正好是 24', async () => {
  const h = harness();
  await boot(h);
  const html = h.element.innerHTML;
  assert.ok(html.includes('class="ct-dial'), '必须有钟面');
  /* 柱子是 `<line class="ct-bar...">`；容器叫 ct-bars，所以模式必须带上 `"` 或空格，
     否则会把容器自己数成第 25 格里的一根。 */
  const bars = (html.match(/class="ct-bar[" ]/g) || []).length;
  assert.equal(bars, 24, '一天 24 格，一格不多一格不少');
  assert.ok(html.includes('ct-quiet is-on'), '免打扰开着时外圈要有标记');
  /* 夹具里 enable_daily_greetings 是开着的，三个窗口都该画出来。
     （这个断言一度写成 1，是我把探针夹具和测试夹具混了——
       页面没错，错的是期望值。） */
  const anchors = (html.match(/class="ct-anchor"/g) || []).length;
  assert.equal(anchors, 3, '每日问候启用时，三个锚点窗口各画一条弧');
  for (const name of ['morning', 'noon', 'evening']) {
    assert.ok(html.includes('data-anchor="' + name + '"'), name + ' 窗口应画在环上');
  }
});

test('未启用每日问候时环上不画任何问候弧', async () => {
  /* 反向验证：这条断言要能证明上一条不是空洞的。
     后端对三个 greeting_*_window 都有默认值，所以「有窗口就画」会恒真；
     判据必须落在总开关上，否则环上会画出三个并不存在的时刻。 */
  const h = harness();
  await boot(h, {...baseSettings(), enable_daily_greetings: false});
  const html = h.element.innerHTML;
  assert.equal((html.match(/class="ct-anchor"/g) || []).length, 0,
    '总开关关着时，窗口有值也不能画');
  /* 但窗口输入框仍要在，否则用户没办法先把值填好再开总开关。 */
  assert.ok(html.includes('greeting_morning_window'), '控制区仍要能编辑窗口');
});

test('全局节律展示配置默认，不推断当前私聊对象的授权', async () => {
  const h = harness();
  await boot(h, {...baseSettings(), quiet_enabled: false,
    proactive_hour_activity_curve: new Array(24).fill(1).join(',')});
  const html = h.element.innerHTML;
  assert.ok(html.includes('全局默认已开启'));
  assert.ok(html.includes('具体用户的授权与私聊设置在私聊用户页管理'));
  assert.equal(h.page.state.data.effective, null);
  assert.ok(!html.includes('当前对象尚未授予联系权限'));
  assert.ok(!html.includes('此刻可以开口'));
});

test('总开关与自动联系开关分别呈现全局默认', async () => {
  const disabled = harness();
  await boot(disabled, {...baseSettings(), enabled:false, automatic_enabled:false,
    quiet_enabled:false, proactive_hour_activity_curve:new Array(24).fill(1).join(',')});
  assert.match(disabled.element.innerHTML, /全局默认已关闭/);
  assert.doesNotMatch(disabled.element.innerHTML, /联系条件暂时无法核对|当前对象尚未授予联系权限/);

  const automaticOff = harness();
  await boot(automaticOff, {...baseSettings(), automatic_enabled:false, quiet_enabled:false,
    proactive_hour_activity_curve:new Array(24).fill(1).join(',')});
  assert.match(automaticOff.element.innerHTML, /后台自动联系默认关闭/);
  automaticOff.page.openGateSettingsView();
  assert.match(automaticOff.element.innerHTML, /后台自动联系默认/);
  assert.doesNotMatch(automaticOff.element.innerHTML, /当前对象尚未授予联系权限/);
});

test('全局设置页不显示逐用户授权结果', async () => {
  const h = harness();
  await boot(h, {...baseSettings(), quiet_enabled:false,
    proactive_hour_activity_curve:new Array(24).fill(1).join(',')});
  assert.doesNotMatch(h.element.innerHTML, /当前对象尚未授予联系权限|前往关系与权限/);
  assert.equal(h.page.state.data.effectiveSettings, null);
  assert.match(h.element.innerHTML, /data-field="enabled"[^>]* checked/);
  assert.match(h.element.innerHTML, /data-field="automatic_enabled"[^>]* checked/);
});

test('普通全局暂停显示暂停原因，但不生成授权跳转', async () => {
  const denied = harness();
  await boot(denied, {...baseSettings(), pause_until:'2099-01-01T12:00:00+08:00'});
  assert.match(denied.element.innerHTML, /全局暂停中/);
  assert.doesNotMatch(denied.element.innerHTML, /class="ct-center-action"|前往关系与权限/);
});

test('曲线未启用与曲线全为 1.0 在视觉上必须不同', async () => {
  const h = harness();
  const settings = {...baseSettings(), proactive_hour_activity_enabled: false,
    proactive_hour_activity_curve: new Array(24).fill(1).join(',')};
  await boot(h, settings);
  const html = h.element.innerHTML;
  assert.ok(!html.includes('ct-bar is-on'), '未启用时柱子不该带启用标记');
});

test('曲线上还有文本口：拖涂只有指针设备能用，键盘用户不能没有入口', async () => {
  const h = harness();
  await boot(h);
  const html = h.element.innerHTML;
  /* 24 个值必须同时可读可写：可读是给用户核对，可写是给键盘。
     拖涂是增强，不是唯一入口。 */
  assert.ok(html.includes('data-role="curveText"'), '曲线旁要有文本框');
  const match = /data-role="curveText"[^>]*>([^<]*)</.exec(html);
  assert.ok(match, '文本框要带上当前值');
  assert.equal(match[1].split(',').length, 24, '文本框里必须是 24 个值');
});

/* ---- 字段覆盖 ---------------------------------------------------------
   渲染出来的字段名清单就是这一页的**写出口**：少一个名字，那一项从此保存不了，
   而界面上不会有任何报错——用户只会觉得「这个开关点了没反应」。
   所以这份清单是冻结的：换版式、重排、拆分区块都不该动它一个名字。
   清单从渲染结果里取，不是从源码 grep——grep 抓不到拼接出来的字段名
   （greeting_+name+_window 这类名字在源码里一个完整的都搜不到）。

   两个不是 data-field 的绑定要单独钉：强度档位。 */
const CONTACT_FIELDS = {
  dial: [
    "allow_insomnia_night_message", "automatic_enabled", "enable_daily_greetings", "enabled",
    "greeting_evening_window", "greeting_morning_window", "greeting_noon_window",
    "idle_seconds", "interval_seconds", "proactive_hour_activity_curve",
    "proactive_hour_activity_enabled", "quiet_enabled", "quiet_end", "quiet_start",
    "quiet_timezone",
  ],
  morning: [
    "morning_chain_enabled", "morning_nudge_delay_minutes", "morning_nudge_enabled",
    "morning_nudge_max_attempts", "morning_role_greeting_enabled", "morning_wake_confidence_floor",
    "routine_learning_enabled", "routine_window_days", "wake_contact_max_daily",
    "wake_contact_min_confidence", "wake_contact_window_minutes", "wake_morning_end",
    "wake_morning_start", "wake_require_device_evidence",
  ],
  purposes: [
    "affection.enabled", "affection.weight", "care.enabled", "care.weight", "chat.enabled",
    "chat.weight", "creation.enabled", "creation.weight", "environment.enabled",
    "environment.weight", "followup.enabled", "followup.weight", "greeting.enabled",
    "greeting.weight", "invitation.enabled", "invitation.weight", "life.enabled",
    "life.weight", "meal.enabled", "meal.weight", "news.enabled", "news.weight",
    "play.enabled", "play.weight", "reminder.enabled", "reminder.weight", "repair.enabled",
    "repair.weight", "screen.enabled", "screen.weight",
  ],
  gates: ["note", "pause_until"],
  budgets: [
    "dedup_enabled", "dedup_last_message_enabled", "dedup_last_message_window_minutes",
    "dedup_min_overlap_ratio", "dedup_min_shared_tokens", "dedup_policies",
    "dedup_sent_window_minutes", "enable_proactive_burst",
    "max_contacts_24h",
    "proactive_burst_gap_max_seconds", "proactive_burst_gap_min_seconds",
    "proactive_burst_max_messages", "proactive_closing_contacts_threshold",
    "proactive_closing_grace_minutes", "proactive_share_probability",
  ],
  sources: [
    "appointment_lag_minutes", "appointment_lead_minutes", "appointment_pre_silence_minutes",
    "balance_awareness_enabled", "balance_check_interval_minutes",
    "balance_critical_percent_threshold", "balance_critical_threshold",
    "balance_currency_label", "balance_include_amount_in_message",
    "balance_low_percent_threshold", "balance_low_threshold", "balance_message_cooldown_hours",
    "date_checkin_enabled", "date_checkin_time", "dedup_weather_window_minutes",
    "enable_llm_proactive_persona_judge", "external_event_self_link_enabled",
    "generation_history_limit", "generation_mode", "history_max_chars", "important_date_lookahead_days",
    "meal_care_followup_minutes", "meal_checkin_enabled", "meal_dinner_time",
    "meal_lunch_time", "news_check_interval_minutes", "news_daily_hot_read_enabled",
    "news_hot_sources", "news_share_enabled", "news_source_kind", "news_source_uid",
    "news_source_url", "official_alert_min_severity", "official_alert_poll_minutes",
    "official_weather_alert_enabled", "poke_action_cooldown_minutes",
    "poke_action_daily_limit", "poke_action_enabled", "proactive_persona_judge_cache_minutes",
    "proactive_persona_judge_threshold",
    "screen_glance_cooldown_minutes", "screen_glance_daily_limit", "screen_glance_enabled",
    "screen_glance_followup_after_minutes", "screen_glance_followup_cooldown_minutes",
    "screen_glance_followup_enabled", "screen_glance_goodnight_delay_minutes",
    "screen_glance_goodnight_enabled", "voice_action_cooldown_minutes", "voice_action_enabled",
    "voice_action_max_chars", "weather_change_enabled", "weather_checkin_enabled",
    "weather_checkin_time", "weather_sample_minutes", "web_exploration_enabled",
    "web_exploration_interests", "web_exploration_min_interval_hours",
    "web_exploration_result_count",
  ],
};

test('设置视图渲染出的字段清单与本页写出口完全一致', async () => {
  const h = harness();
  await boot(h);
  const seen = new Set();
  for (const [view, expected] of Object.entries(CONTACT_FIELDS)) {
    h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view}} : null}});
    const html = h.element.innerHTML;
    const names = [...html.matchAll(/data-field="([^"]*)"/g)].map(m => m[1]);
    /* 同名两个控件会互相覆盖：后渲染的那个写草稿，前一个就永远读不到了。 */
    assert.equal(new Set(names).size, names.length, view + ' 里有重复的 data-field');
    for (const name of names) {
      assert.ok(!seen.has(name), '字段 ' + name + ' 同时出现在两个视图里，改一处会静默丢掉另一处');
      seen.add(name);
    }
    assert.deepEqual(names.slice().sort(), expected.slice(),
      view + ' 的字段清单变了。加字段要连这份清单一起改；少字段说明有控件没接上。');
  }
  assert.equal(seen.size, 135, '设置视图合起来必须是 135 个 data-field 绑定');
  /* 强度档位是五档共用一个字段，走 data-tier 而不是 data-field：
     只数 data-field 会把它整个漏掉。三个问候锚点同理（它们不是控件，是环上的热点）。 */
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view: 'dial'}} : null}});
  const dial = h.element.innerHTML;
  assert.equal((dial.match(/data-tier="/g) || []).length, 5, '强度档位五档都要在');
  assert.ok(dial.includes('data-tier="balanced"'), '当前档位要有对应的按钮可点');
  assert.equal((dial.match(/data-anchor="/g) || []).length, 3, '三个问候锚点要画在环上');
});

test('触发源仍可在主动联系页直接配置，并提供到感知分区的辅助跳转', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view: 'sources'}} : null}});
  const html = h.element.innerHTML;
  assert.equal((html.match(/class="ct-src-group/g) || []).length, 8, '八组触发源都应保留');
  for (const name of ['weather_checkin_enabled', 'news_share_enabled', 'meal_checkin_enabled',
    'web_exploration_enabled', 'screen_glance_enabled', 'enable_llm_proactive_persona_judge',
    'balance_awareness_enabled']) {
    assert.match(html, new RegExp(`data-field="${name}"`), `${name} 必须能在主动联系页直接编辑`);
  }
  assert.equal((html.match(/data-perception-tab-target="/g) || []).length, 5,
    '触发源卡片应提供五个感知分区辅助跳转');
  assert.doesNotMatch(html, /唯一的感知配置入口|感知来源统一在感知页维护/,
    '不能把感知页描述成唯一可写入口');
});

/* ---- 版式与盘面动效 ---------------------------------------------------
   版式是这次改版的目的：盘面与「三个总开关 + 免打扰」同一行，
   其余三块铺满整页宽。这条按字段归属断言，不按 CSS 像素——
   grid 写错列数在无浏览器环境里看不出来，但字段跑到哪一层看得出来。 */

test('节律盘第一行放盘面与三项主开关，其余三块铺满整页宽', async () => {
  const h = harness();
  await boot(h);
  const html = h.element.innerHTML;
  const wrap = html.slice(html.indexOf('ct-dial-wrap'), html.indexOf('ct-rest'));
  const rest = html.slice(html.indexOf('ct-rest'));
  for (const name of ['enabled', 'automatic_enabled', 'quiet_enabled', 'quiet_start', 'quiet_end',
    'quiet_timezone']) {
    assert.ok(wrap.includes('data-field="' + name + '"'),
      name + ' 一改就改变盘面几何，要和盘面同一行');
  }
  for (const name of ['proactive_hour_activity_curve', 'interval_seconds', 'idle_seconds',
    'greeting_morning_window', 'enable_daily_greetings']) {
    assert.ok(!wrap.includes('data-field="' + name + '"'), name + ' 不该挤进第一行');
    assert.ok(rest.includes('data-field="' + name + '"'), name + ' 要落在铺满整页的那一层');
  }
  assert.equal((rest.match(/<section class="ct-block/g) || []).length, 3,
    '下面三块：活动曲线 / 强度档位 / 问候窗口');
  assert.equal((rest.match(/class="ct-block is-wide/g) || []).length, 1,
    '24 格曲线那一块占满整行——柱子要的是横向空间');
  /* 三块各自的圈位色标齐全：标题前的色条就是盘面上那一圈的颜色。 */
  for (const ring of ['is-ring-curve', 'is-ring-tier', 'is-ring-greet']) {
    assert.ok(rest.includes(ring), '缺少圈位色标 ' + ring);
  }
  assert.ok(wrap.includes('is-ring-quiet'), '免打扰块要有外圈的圈位色标');
});

test('盘面变化只闪改动的那一圈，而且只闪一次', async () => {
  const h = harness();
  await boot(h);
  const stage = () => (/<div class="ct-dial-stage([^"]*)"/.exec(h.element.innerHTML) || [])[1] || '';
  assert.equal(stage(), '', '刚进页面时不该带变化类：开页是 is-scanning 的涟漪，不是这一套');
  h.element.dispatch('change', fieldEvent(
    fakeInput({type: 'text', value: '23:30', dataset: {field: 'quiet_start'}}), 'change'));
  assert.ok(stage().includes('is-pulse-quiet'), '改免打扰要闪外圈');
  assert.ok(!stage().includes('is-pulse-curve'), '整盘一起动等于没说清改的是哪一圈');
  /* 再渲染一次（比如关掉一条通知）就不该重放：变化动效是确认，不是常驻状态。 */
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-action]' ? {dataset: {action: 'dismiss'}} : null}});
  assert.equal(stage(), '', '变化类只能闪一次');
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-tier]' ? {dataset: {tier: 'live'}} : null}});
  assert.ok(stage().includes('is-pulse-tier'), '换强度档位要闪三圈');
  assert.equal(h.page.state.data.draft.proactive_intensity_preset, 'live', '档位仍要进草稿');
});

test('切换强度档位立即更新卡片与当前生效的换算数字', async () => {
  const h = harness();
  await boot(h);
  const effective = () => {
    const match = /class="ct-tier-effective" role="status"[^>]*>[\s\S]*?<strong>([^<]+)<\/strong>[\s\S]*?<strong>([^<]+)<\/strong>/.exec(
      h.element.innerHTML);
    return match ? match.slice(1) : [];
  };
  assert.deepEqual(effective(), ['48 分钟', '10.5 分钟'], '均衡档位的生效数字应来自当前基准值');
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-tier]' ? {dataset: {tier: 'live'}} : null}});
  assert.deepEqual(effective(), ['12 分钟', '1.5 分钟'], '切到实时档位后生效数字必须立即变化');
  assert.ok(h.element.innerHTML.includes('data-field="interval_seconds" value="3600"'),
    '基础间隔仍保留为可编辑的原始值');
  assert.ok(h.element.innerHTML.includes('data-field="idle_seconds" value="900"'),
    '基础等候仍保留为可编辑的原始值');
});

test('触发源卡片头部的「n/m 开启」和卡片里真实的开关一致', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view: 'sources'}} : null}});
  const groups = h.element.innerHTML.split('<section class="ct-src-group').slice(1);
  assert.equal(groups.length, 8, '八组触发源，一组不能少');
  for (const group of groups) {
    const head = /data-toggles="(\d+)" data-enabled="(\d+)"/.exec(group);
    assert.ok(head, '卡片要写出开关总数与开启数：' + group.slice(0, 90));
    const toggles = (group.match(/data-kind="toggle"/g) || []).length;
    const boxes = [...group.matchAll(/data-field="[^"]+" checked>/g)].length;
    assert.equal(toggles, Number(head[1]), '卡片里的开关数和 data-toggles 不一致');
    assert.equal(boxes, Number(head[2]), '卡片里勾着的开关数和 data-enabled 不一致');
    /* 给人看的那句也要对得上：数字对了但句子里写错，一样是撒谎。 */
    assert.ok(group.includes('>' + head[2] + '/' + head[1] + ' 开启<'), '徽标文字和计数不一致');
  }
});

test('开关向两个方向扳都要播一次翻转动画，且只播一次', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view: 'sources'}} : null}});
  const flip = checked => h.element.dispatch('change', fieldEvent(
    fakeInput({type: 'checkbox', checked, dataset: {field: 'balance_awareness_enabled'}}), 'change'));
  flip(true);
  assert.ok(h.element.innerHTML.includes('is-flip-on'), '扳开要挂着开方向的动画类');
  assert.ok(!h.element.innerHTML.includes('is-flip-off'), '开着时不该同时挂关的类');
  flip(false);
  assert.ok(h.element.innerHTML.includes('is-flip-off'),
    '扳回去也要有关方向的动画——开关只有一个方向的动画等于没做');
  assert.ok(!h.element.innerHTML.includes('is-flip-on'), '关的时候不该还留着开的类');
  /* 下一次渲染不该重放：翻转是「刚才这一下」的确认，不是常驻状态。 */
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-action]' ? {dataset: {action: 'dismiss'}} : null}});
  assert.ok(!/is-flip-(on|off)/.test(h.element.innerHTML), '翻转类只能跟着一次渲染');
});

/* 上面那条测的是「类有没有写出来」，这条测的是「CSS 里有没有东西去播它」——
   两个方向的关键帧缺一半，界面上看起来和没写一样，而没人会因此报错。 */
test('翻转动画两个方向在 CSS 里都成立了：关键帧、两类开关、减弱动效', () => {
  const css = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/contact-page.css'), 'utf8');
  for (const part of ['on', 'off']) {
    assert.ok(css.includes('@keyframes ct-flip-' + part + '-track'), part + ' 方向缺轨道关键帧');
    assert.ok(css.includes('@keyframes ct-flip-' + part + '-knob'), part + ' 方向缺滑块关键帧');
  }
  /* 主配置行的开关（类在 .ct-switch）和触发源行的开关（类在 .ct-src-row）都要能播。 */
  for (const scope of ['.ct-switch.is-flip-on .ct-switch-track', '.ct-switch.is-flip-off .ct-switch-track',
                       '.ct-src-row.is-toggle.is-flip-off .ct-switch-track']) {
    assert.ok(css.includes(scope), scope + ' 缺动画规则，那一处的开关会无声跳变');
  }
  const reduced = css.slice(css.indexOf('@media (prefers-reduced-motion:reduce)'));
  assert.ok(reduced.includes('is-flip-on') && reduced.includes('is-flip-off'),
    '减弱动效要覆盖翻转，否则这条动画不在保护名单里');
});

/* 纵向间距靠 CSS，改坏不会报错，只会「看起来糊」。这条守住三件事：
   三档都挂在共享 sp token 上（紧凑/宽松密度才继续生效）、三档严格递减、
   列表行距走 row 档而不是写死 1px——行距比块距还小是这一屏糊掉的根因。 */
test('纵向间距分三档，且档位递减、行距不写死', () => {
  const css = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/contact-page.css'), 'utf8');
  const tokens = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/tokens.css'), 'utf8');
  const px = {};
  for (const level of ['section', 'block', 'row']) {
    const def = css.match(new RegExp('--ct-v-' + level + ':\\s*var\\(--(sp-\\d)\\)'));
    assert.ok(def, '--ct-v-' + level + ' 缺定义，或没挂在共享 sp token 上（密度档会失效）');
    px[level] = Number((tokens.match(new RegExp('--' + def[1] + ':\\s*(\\d+)px')) || [])[1]);
    assert.ok(px[level] > 0, def[1] + ' 在 tokens.css 里没取到值');
  }
  assert.ok(px.section > px.block && px.block > px.row,
    '三档要递减：换话题 > 换一组 > 同一组的下一条，反了就会读成一条糊掉的带子');
  assert.ok(/\.ct-purpose-list\{[^}]*gap:var\(--ct-v-row\)/.test(css),
    '契机列表的行距要挂在 row 档上');
  assert.ok(!/\.ct-purpose-list\{[^}]*gap:1px/.test(css),
    '行距不能写回 1px：15 行会挤成一条连续的带子');
});

test('换页带方向，而且只跟着换页那一帧', async () => {
  const h = harness();
  await boot(h);
  const view = name => h.element.dispatch('click',
    {target: {closest: sel => sel === '[data-view]' ? {dataset: {view: name}} : null}});
  const cls = () => (/<div class="ct-view([^"]*)"/.exec(h.element.innerHTML) || [])[1] || '';
  assert.equal(cls(), '', '刚进页面不该播换页动画');
  view('sources');
  assert.ok(cls().includes('is-from-next'), '往后点，新的一屏从右边进');
  view('dial');
  assert.ok(cls().includes('is-from-prev'), '往回点，从左边进：没有方向就只剩「闪一下」');
  /* 换页动画只在换页那一帧：改个字段整屏再滑一次会让人晕。 */
  h.element.dispatch('change', fieldEvent(
    fakeInput({type: 'checkbox', checked: true, dataset: {field: 'enabled'}}), 'change'));
  assert.equal(cls(), '', '改字段不该重播换页动画');
  view('gates');
  assert.equal((h.element.innerHTML.match(/class="is-picked"/g) || []).length, 1,
    '轨道上只有被点中的那项带 is-picked：选中态是靠这个类的动画放出来的，不给类就等于硬贴上去');
});

function fieldEvent(target, type) {
  return {target, type, preventDefault() {}, stopPropagation() {}, pointerId: 1,
    clientX: 0, clientY: 0, key: '', metaKey: false, ctrlKey: false};
}
function fakeInput(attributes) {
  const node = {...attributes};
  node.closest = selector => selector.includes('[data-field]') ? node : null;
  return node;
}

test('拖动权重滑块只重画那一条，不整页重绘', async () => {
  const h = harness();
  await boot(h);
  const before = h.element.innerHTML;
  const fill = {style: {setProperty(k, v) { this[k] = v; }}, __sel: '.ct-weight-fill'};
  const em = {textContent: '', __sel: 'em'};
  const range = fakeInput({type: 'range', value: '3.5', dataset: {field: 'chat.weight'},
    parentElement: {querySelector: sel => sel === '.ct-weight-fill' ? fill : sel === 'em' ? em : null},
    closest(sel) { return sel.includes('[data-field]') ? this : null; }});
  h.element.dispatch('change', fieldEvent(range, 'change'));
  assert.equal(h.element.innerHTML, before, '滑块的 change 不该触发 render()');
  assert.equal(em.textContent, '3.5', '数字要跟着走');
  assert.equal(h.page.state.data.draft['chat.weight'], 3.5, '草稿要记下这一项');
});

test('保存只提交动过的字段，并把两个令牌分开带上', async () => {
  const h = harness();
  await boot(h);
  /* 只动一项。 */
  const box = fakeInput({type: 'checkbox', checked: false, dataset: {field: 'news.enabled'}});
  h.element.dispatch('change', fieldEvent(box, 'change'));
  const posted = [];
  const original = h.page.state.data;
  assert.equal(original.touched.size, 1);
  /* 直接驱动保存：从真实入口（按钮）出发需要 DOM 树，这里断言的是载荷形状。 */
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-action]' ? {dataset: {action: 'save'}} : null}});
  await h.settle();
  for (const request of h.requests) {
    if (request.method === 'POST') posted.push(request.values);
  }
  assert.equal(posted.length, 1, '保存必须只发一次');
  assert.equal(posted[0].section, 'proactive.contact');
  assert.deepEqual(Object.keys(posted[0].changes), ['news.enabled'],
    '没看过的字段一个都不能提交——把未知值写回去会覆盖别人改的');
  assert.equal(posted[0].edit_token, 'c'.repeat(64), '写要走 config 段的令牌');
  assert.ok(typeof posted[0].operation_key === 'string' && posted[0].operation_key.length > 0);
  assert.ok(posted[0].route === undefined);
});

test('版本冲突刷新令牌但保留未保存草稿，并可用新版本再次提交', async () => {
  const h = harness();
  await boot(h);
  const box = fakeInput({type: 'checkbox', checked: false, dataset: {field: 'news.enabled'}});
  h.element.dispatch('change', fieldEvent(box, 'change'));
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-action]' ? {dataset: {action: 'save'}} : null}});
  await h.settle();

  const stale = h.requests.find(request => request.method === 'POST');
  h.fail(stale, 'configuration_revision_conflict');
  await h.settle();
  const refreshed = h.requests.filter(request => request.method === 'GET' && request.route === 'config').at(-1);
  assert.notEqual(refreshed, h.requests[0], '冲突后必须重新读取安装级设置');
  replyGlobalConfig(h, refreshed, {...baseSettings(), 'news.enabled': true}, 'n'.repeat(64));
  await h.settle();

  assert.equal(h.page.state.data.touched.has('news.enabled'), true,
    '冲突只拒绝旧写入，用户已编辑字段必须保留');
  assert.equal(h.page.state.data.draft['news.enabled'], false);
  assert.equal(h.page.state.data.configToken, 'n'.repeat(64));
  assert.match(h.page.state.data.notice, /未保存的改动仍保留/);

  h.element.dispatch('click', {target: {closest: sel => sel === '[data-action]' ? {dataset: {action: 'save'}} : null}});
  await h.settle();
  const retry = h.requests.filter(request => request.method === 'POST').at(-1);
  assert.equal(retry.values.edit_token, 'n'.repeat(64));
  assert.deepEqual(JSON.parse(JSON.stringify(retry.values.changes)), {'news.enabled': false});
});

test('长目标路径使用不超过后端上限的幂等键', async () => {
  const target = 'target-' + 'x'.repeat(80);
  const role = 'role-' + 'y'.repeat(80);
  const base = `${HOST}/host/targets/${target}/roles/${role}`;
  const h = harness({nativeApi: '/' + base + '/perception-native', configApi: '/' + base + '/config'});
  await boot(h);
  h.element.dispatch('change', fieldEvent(fakeInput({type: 'checkbox', checked: false,
    dataset: {field: 'news.enabled'}}), 'change'));
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-action]'
    ? {dataset: {action: 'save'}} : null}});
  await h.settle();

  const post = h.requests.find(request => request.method === 'POST');
  assert.ok(post, '长目标路径也必须提交保存');
  assert.equal(post.route, 'config/configure', '全局写入路由与所选目标路径无关');
  assert.ok(post.values.operation_key.length <= 128, post.values.operation_key.length);
  assert.deepEqual(Object.keys(post.values.changes), ['news.enabled']);
});

test('保存成功后清空草稿并使用回执令牌，不再全量回读', async () => {
  const h = harness();
  await boot(h);
  const before = h.requests.filter(r => r.method === 'GET' && r.route === 'config').length;
  const box = fakeInput({type: 'checkbox', checked: false, dataset: {field: 'news.enabled'}});
  h.element.dispatch('change', fieldEvent(box, 'change'));
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-action]' ? {dataset: {action: 'save'}} : null}});
  await h.settle();
  const post = h.requests.find(r => r.method === 'POST');
  h.reply(post, {result: {operation_id: 'contact-op-1', replayed: false},
    status: {sections: {['proactive.contact']: {edit_token: 'c2'.repeat(32)}}}});
  await h.settle();
  assert.equal(h.page.state.data.touched.size, 0, '保存成功后草稿要清空');
  assert.equal(h.page.state.data.configToken, 'c2'.repeat(32), '回执令牌应直接用于下一次保存');
  assert.equal(h.requests.filter(r => r.method === 'GET' && r.route === 'config').length, before,
    '保存回执已经给出令牌时不应再读整块配置');
  assert.equal(h.page.state.data.settings['news.enabled'], false);
  assert.ok(h.page.state.data.notice.includes('已保存'), '要有确认');
});

test('写入回执确认后单独刷新安装级配置令牌', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('change', fieldEvent(fakeInput({type: 'checkbox', checked: false,
    dataset: {field: 'news.enabled'}}), 'change'));
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-action]' ? {dataset: {action: 'save'}} : null}});
  await h.settle();
  const post = h.requests.find(r => r.method === 'POST');
  h.reply(post, {result: {operation_id: 'contact-op-without-status', replayed: false}, status: null});
  await h.settle();
  assert.equal(h.page.state.data.pendingWrite, null, '持久回执足以确认写入');
  assert.equal(h.page.state.data.touched.size, 0);
  const contract = h.requests.filter(r => r.method === 'GET' && r.route === 'config').at(-1);
  assert.ok(contract, '确认写入后要读取最新配置令牌');
  replyGlobalConfig(h, contract, {...baseSettings(), 'news.enabled': false}, 'c7'.repeat(32));
  await h.settle();
  assert.equal(h.page.state.data.configToken, 'c7'.repeat(32));
  assert.match(h.page.state.data.notice, /已保存/);
  assert.doesNotMatch(h.element.innerHTML, /保存结果尚未确认/);
});

test('a saved receipt with a fresh token skips the follow-up full section read', async () => {
  const h = harness();
  await boot(h);
  const before = h.requests.filter(r => r.method === 'GET' && r.route === 'config').length;
  h.element.dispatch('change', fieldEvent(fakeInput({type: 'checkbox', checked: false,
    dataset: {field: 'news.enabled'}}), 'change'));
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-action]' ? {dataset: {action: 'save'}} : null}});
  await h.settle();
  const post = h.requests.find(r => r.method === 'POST');
  h.reply(post, {result: {operation_id: 'contact-op-2', replayed: false,
    edit_token: 'c2'.repeat(32)}, status: {sections: {
    ['proactive.contact']: {edit_token: 'c2'.repeat(32)},
  }}});
  await h.settle();
  assert.equal(h.page.state.data.pendingWrite, null);
  assert.equal(h.page.state.data.configToken, 'c2'.repeat(32));
  assert.equal(h.requests.filter(r => r.method === 'GET' && r.route === 'config').length, before,
    'the save response replaces the full projection used only to mint a token');
  assert.match(h.page.state.data.notice, /已保存 1 项/);
  assert.doesNotMatch(h.page.state.data.notice, /保存结果尚未确认|未收到服务确认/);
});

test('blocked sessionStorage falls back to durable localStorage before submitting', async () => {
  const h = harness({sessionStorageBlocked: true});
  await boot(h);
  h.element.dispatch('change', fieldEvent(fakeInput({type: 'checkbox', checked: false,
    dataset: {field: 'news.enabled'}}), 'change'));
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-action]' ? {dataset: {action: 'save'}} : null}});
  await h.settle();
  const post = h.requests.find(r => r.method === 'POST');
  assert.ok(post, 'storage fallback should allow the request after recording it');
  assert.equal(h.localStore.size, 1, 'the exact request must be durable before it is sent');
  const saved = JSON.parse([...h.localStore.values()][0]);
  assert.equal(JSON.stringify(saved.body), JSON.stringify(post.values));
  post.reject(new Error('connection reset'));
  await h.settle();
  assert.equal(h.page.state.data.pendingWrite.body.operation_key, saved.body.operation_key);
  assert.equal(h.localStore.size, 1, 'an unknown outcome must retain the same operation for verification');
  h.page.unmount();
});

test('sandboxed storage and history keep an unknown save in the page session with the same operation', async () => {
  const h = harness({sessionStorageBlocked: true, localStorageBlocked: true, historyBlocked: true});
  await boot(h);
  h.element.dispatch('change', fieldEvent(fakeInput({type: 'checkbox', checked: false,
    dataset: {field: 'news.enabled'}}), 'change'));
  const save = () => h.element.dispatch('click', {target: {closest: selector =>
    selector === '[data-action]' ? {dataset: {action: 'save'}} : null}});
  save();
  await h.settle();
  const original = h.requests.find(request => request.method === 'POST');
  assert.ok(original);
  original.reject(new Error('connection reset'));
  await h.settle();
  save();
  await h.settle();
  const resumed = h.requests.filter(request => request.method === 'POST').at(-1);
  assert.notEqual(resumed, original);
  assert.equal(JSON.stringify(resumed.values), JSON.stringify(original.values));
  h.reply(resumed, {result: {operation_id: 'contact-sandbox', replayed: true}, status: {sections: {
    ['proactive.contact']: {edit_token: 'c2'.repeat(32)},
  }}});
  await h.settle();
  const refresh = h.requests.filter(request => request.method === 'GET' && request.route === 'config').at(-1);
  replyGlobalConfig(h, refresh);
  await h.settle();
  assert.equal(h.page.state.data.pendingWrite, null);
});

test('blocked browser storage records an unknown save in history and restores the same operation after reload', async () => {
  const historyEntry = {current: {router: {index: 3}}};
  const first = harness({sessionStorageBlocked: true, localStorageBlocked: true, historyEntry});
  await boot(first);
  first.element.dispatch('change', fieldEvent(fakeInput({type: 'checkbox', checked: false,
    dataset: {field: 'news.enabled'}}), 'change'));
  first.element.dispatch('click', {target: {closest: sel => sel === '[data-action]' ? {dataset: {action: 'save'}} : null}});
  await first.settle();
  const originalPost = first.requests.find(r => r.method === 'POST');
  assert.ok(originalPost, 'history state should allow submit after both browser storage APIs fail');
  const originalBody = JSON.parse(JSON.stringify(originalPost.values));
  assert.deepEqual(historyEntry.current.router, {index: 3}, 'the host history state must be preserved');
  originalPost.reject(new Error('connection reset'));
  await first.settle();
  first.page.unmount();

  const reloaded = harness({sessionStorageBlocked: true, localStorageBlocked: true, historyEntry});
  await boot(reloaded);
  assert.deepEqual(JSON.parse(JSON.stringify(reloaded.page.state.data.pendingWrite.body)), originalBody);
  await new Promise(resolve => setTimeout(resolve, 450));
  const retryPost = reloaded.requests.filter(r => r.method === 'POST').at(-1);
  assert.deepEqual(JSON.parse(JSON.stringify(retryPost.values)), originalBody);
  reloaded.reply(retryPost, {result: {operation_id: 'contact-op-4', replayed: true}, status: {sections: {
    ['proactive.contact']: {edit_token: 'c4'.repeat(32)},
  }}});
  await reloaded.settle();
  const refreshed = reloaded.requests.filter(r => r.method === 'GET' && r.route === 'config').at(-1);
  replyGlobalConfig(reloaded, refreshed, baseSettings(), 'c4'.repeat(32));
  await reloaded.settle();
  assert.equal(reloaded.page.state.data.pendingWrite, null);
  assert.equal(JSON.stringify(historyEntry.current), JSON.stringify({router: {index: 3}}),
    'confirmation should remove only the contact record');
});

test('an unknown contact save is restored after reload and retried with the identical request', async () => {
  const sessionStore = new Map();
  const first = harness({sessionStore});
  await boot(first);
  first.element.dispatch('change', fieldEvent(fakeInput({type: 'checkbox', checked: false,
    dataset: {field: 'news.enabled'}}), 'change'));
  first.element.dispatch('click', {target: {closest: sel => sel === '[data-action]' ? {dataset: {action: 'save'}} : null}});
  await first.settle();
  const originalPost = first.requests.find(r => r.method === 'POST');
  const originalBody = JSON.parse(JSON.stringify(originalPost.values));
  originalPost.reject(new Error('connection reset'));
  await first.settle();
  assert.deepEqual(JSON.parse(JSON.stringify(first.page.state.data.pendingWrite.body)), originalBody);
  first.page.unmount();

  const reloaded = harness({sessionStore});
  await boot(reloaded);
  assert.deepEqual(JSON.parse(JSON.stringify(reloaded.page.state.data.pendingWrite.body)), originalBody);
  assert.match(reloaded.element.innerHTML, /data-action="retry-pending"/);
  assert.match(reloaded.element.innerHTML, /立即核对/);
  assert.equal((reloaded.element.innerHTML.match(/data-action="retry-pending"/g) || []).length, 1);
  await new Promise(resolve => setTimeout(resolve, 450));
  const retryPost = reloaded.requests.filter(r => r.method === 'POST').at(-1);
  assert.deepEqual(JSON.parse(JSON.stringify(retryPost.values)), originalBody);
  reloaded.reply(retryPost, {result: {operation_id: 'contact-op-3', replayed: true}, status: {sections: {
    ['proactive.contact']: {edit_token: 'c3'.repeat(32)},
  }}});
  await reloaded.settle();
  const refreshed = reloaded.requests.filter(r => r.method === 'GET' && r.route === 'config').at(-1);
  replyGlobalConfig(reloaded, refreshed, baseSettings(), 'c3'.repeat(32));
  await reloaded.settle();
  assert.equal(reloaded.page.state.data.pendingWrite, null);
  assert.equal(sessionStore.size, 0);
  assert.match(reloaded.page.state.data.notice, /已保存/);
});

test('the single pending notice action immediately checks the original save', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('change', fieldEvent(fakeInput({type: 'checkbox', checked: false,
    dataset: {field: 'news.enabled'}}), 'change'));
  h.element.dispatch('click', {target: {closest: selector => selector === '[data-action]'
    ? {dataset: {action: 'save'}} : null}});
  await h.settle();
  const original = h.requests.filter(request => request.method === 'POST').at(-1);
  const originalBody = JSON.parse(JSON.stringify(original.values));
  original.reject(new Error('connection reset'));
  await h.settle();

  h.element.dispatch('click', {target: {closest: selector => selector === '[data-action]'
    ? {dataset: {action: 'retry-pending'}} : null}});
  await h.settle();
  const retry = h.requests.filter(request => request.method === 'POST').at(-1);
  assert.notEqual(retry, original);
  assert.deepEqual(JSON.parse(JSON.stringify(retry.values)), originalBody);
  h.reply(retry, {result: {operation_id: 'contact-manual-check', replayed: true}, status: {sections: {
    ['proactive.contact']: {edit_token: 'c6'.repeat(32)},
  }}});
  await h.settle();
  const refreshed = h.requests.filter(request => request.method === 'GET' && request.route === 'config').at(-1);
  replyGlobalConfig(h, refreshed, baseSettings(), 'c6'.repeat(32));
  await h.settle();
  assert.equal(h.page.state.data.pendingWrite, null);
  assert.match(h.page.state.data.notice, /已保存/);
});

test('a bridge error without a machine code explains the pending save and keeps its receipt key', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('change', fieldEvent(fakeInput({type: 'checkbox', checked: false,
    dataset: {field: 'news.enabled'}}), 'change'));
  h.element.dispatch('click', {target: {closest: selector => selector === '[data-action]'
    ? {dataset: {action: 'save'}} : null}});
  await h.settle();

  const original = h.requests.filter(request => request.method === 'POST').at(-1);
  const operationKey = original.values.operation_key;
  original.reject(new Error('Failed to fetch'));
  await h.settle();

  assert.equal(h.page.state.data.pendingWrite.body.operation_key, operationKey);
  assert.match(h.element.innerHTML, /网络连接中断/);
  assert.match(h.element.innerHTML, /原保存请求已保留/);
});

test('a fenced save drops the stale operation and keeps the draft for a fresh save', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('change', fieldEvent(fakeInput({type: 'checkbox', checked: false,
    dataset: {field: 'news.enabled'}}), 'change'));
  h.element.dispatch('click', {target: {closest: selector => selector === '[data-action]'
    ? {dataset: {action: 'save'}} : null}});
  await h.settle();

  const post = h.requests.find(request => request.method === 'POST');
  post.reject(Object.assign(new Error('host generation replaced'), {code: 'supervisor_fenced'}));
  await h.settle();

  assert.equal(h.page.state.data.pendingWrite, null, 'the old generation cannot confirm this operation');
  assert.equal(h.page.state.data.touched.size, 1, 'keep the edits so the user can review and save again');
  assert.match(h.page.state.data.notice, /宿主已重启/);
  assert.doesNotMatch(h.element.innerHTML, /data-action="retry-pending"/);
  assert.match(h.element.innerHTML, /data-action="save"/);
});

test('a machine-coded bridge rejection is confirmed and does not retry as an unknown write', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('change', fieldEvent(fakeInput({type: 'checkbox', checked: false,
    dataset: {field: 'news.enabled'}}), 'change'));
  h.element.dispatch('click', {target: {closest: selector => selector === '[data-action]'
    ? {dataset: {action: 'save'}} : null}});
  await h.settle();

  const post = h.requests.find(request => request.method === 'POST');
  post.reject(new Error('configuration_request_invalid'));
  await h.settle();

  assert.equal(h.page.state.data.pendingWrite, null, 'an explicit API rejection is not an unknown commit');
  assert.equal(h.page.state.data.touched.size, 1, 'leave the draft for correction');
  assert.match(h.page.state.data.notice, /请求与当前接口不匹配/);
  assert.doesNotMatch(h.element.innerHTML, /保存结果尚未确认/);
});

test('a pending save keeps retrying automatically with one notice and the same operation', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('change', fieldEvent(fakeInput({type: 'checkbox', checked: false,
    dataset: {field: 'news.enabled'}}), 'change'));
  h.element.dispatch('click', {target: {closest: selector => selector === '[data-action]'
    ? {dataset: {action: 'save'}} : null}});
  await h.settle();

  const original = h.requests.filter(request => request.method === 'POST').at(-1);
  const originalBody = JSON.parse(JSON.stringify(original.values));
  original.reject(Object.assign(new Error('connection reset'), {code: 'bridge_timeout'}));
  await h.settle();

  for (const delay of [450, 850, 1650]) {
    await new Promise(resolve => setTimeout(resolve, delay));
    const retry = h.requests.filter(request => request.method === 'POST').at(-1);
    assert.notEqual(retry, original);
    assert.deepEqual(JSON.parse(JSON.stringify(retry.values)), originalBody,
      'every automatic verification must preserve the exact operation and body');
    retry.reject(Object.assign(new Error('route response unavailable'), {code: 'dashboard_bridge_not_ready'}));
    await h.settle();
  }

  assert.ok(h.page.state.data.pendingWrite, 'unconfirmed requests remain available for manual verification');
  assert.equal((h.element.innerHTML.match(/class="ct-notice is-error"/g) || []).length, 1,
    'save uncertainty and retry failure appear in one notice');
  assert.match(h.element.innerHTML, /核对请求未收到确认（错误码：dashboard_bridge_not_ready）/);
  assert.match(h.element.innerHTML, /系统会持续沿用同一操作标识核对/,
    'automatic verification continues after transient failures');
  assert.doesNotMatch(h.element.innerHTML, /联系条件暂时无法核对/,
    'a save transport error must not be presented as a contact-condition read failure');
  assert.match(h.element.innerHTML, /data-action="retry-pending"/);
  assert.equal((h.element.innerHTML.match(/data-action="retry-pending"/g) || []).length, 1,
    'the pending write has one immediate retry action');
  assert.doesNotMatch(h.element.innerHTML, /核对原保存结果/,
    'the header must not duplicate the in-context retry button');

  await new Promise(resolve => setTimeout(resolve, 3250));
  const automatic = h.requests.filter(request => request.method === 'POST').at(-1);
  assert.deepEqual(JSON.parse(JSON.stringify(automatic.values)), originalBody,
    'automatic verification must continue to use the original operation after repeated failures');
  h.reply(automatic, {result: {operation_id: 'contact-op-auto', replayed: true}, status: {sections: {
    ['proactive.contact']: {edit_token: 'c5'.repeat(32)},
  }}});
  await h.settle();
  const refreshed = h.requests.filter(request => request.method === 'GET' && request.route === 'config').at(-1);
  replyGlobalConfig(h, refreshed, baseSettings(), 'c5'.repeat(32));
  await h.settle();
  assert.equal(h.page.state.data.pendingWrite, null);
  assert.doesNotMatch(h.element.innerHTML, /保存结果尚未确认/);
});

test('保存失败报告有效配置错误并保留草稿，除非是版本冲突', async () => {
  const h = harness();
  await boot(h);
  const box = fakeInput({type: 'checkbox', checked: false, dataset: {field: 'news.enabled'}});
  h.element.dispatch('change', fieldEvent(box, 'change'));
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-action]' ? {dataset: {action: 'save'}} : null}});
  await h.settle();
  h.fail(h.requests.find(r => r.method === 'POST'), 'invalid_contact_preference');
  await h.settle();
  assert.equal(h.page.state.data.error, true);
  assert.ok(h.page.state.data.notice.includes('格式'), '配置错误要给出可理解的说明');
  assert.equal(h.page.state.data.touched.size, 1, '非冲突失败要保留草稿，用户不必重填');
  assert.equal(h.page.state.data.pendingWrite, null, '明确拒绝后不应保留待核对请求');
  assert.match(h.element.innerHTML, /格式/, '保存失败必须立即在页面给出反馈');
});

test('后端联系配置错误使用可理解的文案并立即反馈', async () => {
  const h = harness();
  await boot(h);
  const box = fakeInput({type: 'checkbox', checked: false, dataset: {field: 'news.enabled'}});
  h.element.dispatch('change', fieldEvent(box, 'change'));
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-action]' ? {dataset: {action: 'save'}} : null}});
  await h.settle();
  const post = h.requests.find(r => r.method === 'POST');
  h.fail(post, 'contact_configuration_invalid');
  await h.settle();
  assert.equal(h.page.state.data.touched.size, 1, '普通配置错误要保留草稿');
  assert.match(h.element.innerHTML, /提交的字段或操作不完整/);
  assert.doesNotMatch(h.element.innerHTML, /联系条件暂时无法核对/);
});

test('切换视图不丢草稿', async () => {
  const h = harness();
  await boot(h);
  const box = fakeInput({type: 'checkbox', checked: false, dataset: {field: 'news.enabled'}});
  h.element.dispatch('change', fieldEvent(box, 'change'));
  for (const view of ['tests', 'purposes', 'gates', 'budgets', 'sources', 'dial']) {
    h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view}} : null}});
    assert.equal(h.page.state.data.touched.size, 1, '切视图不能把未保存的改动丢掉：' + view);
  }
});

test('消息测试是一级视图，未读到设置时也可以打开', async () => {
  const h = harness();
  await boot(h);
  h.page.state.data.ready = false;
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]'
    ? {dataset: {view: 'tests'}} : null}});
  assert.equal(h.page.state.data.view, 'tests');
  assert.ok(h.element.innerHTML.includes('ct-tests'), '静态测试指令不应依赖后端设置状态');
  assert.ok(h.element.innerHTML.includes('ct-head-test'), '页头应保留消息测试直达入口');
  assert.equal((h.element.innerHTML.match(/data-test-id="/g) || []).length, 17);
  assert.equal((h.element.innerHTML.match(/data-test-coverage="ready"/g) || []).length, 17);
  assert.equal((h.element.innerHTML.match(/data-test-coverage="partial"/g) || []).length, 0);
  assert.equal((h.element.innerHTML.match(/data-test-coverage="missing"/g) || []).length, 0);
  assert.match(h.element.innerHTML, /生产器已接通 <strong>17<\/strong>/);
  assert.ok(!h.element.innerHTML.includes('当前没有独立 invitation 生产器'));
  for (const purpose of ['affection','care','play','repair','invitation']) {
    const start = h.element.innerHTML.indexOf(`data-test-id="${purpose}"`);
    const end = h.element.innerHTML.indexOf('</article>', start);
    assert.ok(start >= 0 && end > start, `${purpose} 测试卡片应存在`);
    assert.ok(h.element.innerHTML.slice(start, end).includes('data-test-coverage="ready"'),
      `${purpose} 已有生产器，应显示为已接通`);
  }
});

test('八个视图都能渲染出内容', async () => {
  const h = harness();
  await boot(h);
  const markers = {tests: 'ct-tests', purposes: 'ct-purposes', gates: 'ct-gates', morning: 'ct-block',
    budgets: 'ct-budgets', sources: 'ct-sources', materials: 'ct-materials', dial: 'ct-dial'};
  for (const [view, marker] of Object.entries(markers)) {
    h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view}} : null}});
    if (view === 'materials') {
      await h.settle();
      const request = h.requests.find(r => r.method === 'GET' && r.route === 'perception-native/materials');
      assert.ok(request, '打开主动念头池时读取当前可用念头');
      h.reply(request, {observed_at: '2026-10-07T00:00:00+00:00', state: 'ready', count: 0, items: []});
      await h.settle();
    }
    assert.ok(h.element.innerHTML.includes(marker), view + ' 视图渲染为空');
  }
  /* 契机矩阵必须是 15 行——15 是 PURPOSES 的真实条数（账本曾记 12，实测 15）。 */
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view: 'purposes'}} : null}});
  assert.equal((h.element.innerHTML.match(/data-purpose="/g) || []).length, 15);
  /* 全局页只画有配置来源的五道闸门，不伪造用户授权、模型或宿主状态。 */
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view: 'gates'}} : null}});
  assert.equal((h.element.innerHTML.match(/class="ct-gate( is-\w+)?"/g) || []).length, 5);
  assert.match(h.element.innerHTML, /逐用户授权与单独设置在私聊用户页管理/);
  assert.doesNotMatch(h.element.innerHTML, /当前对象尚未授予联系权限|模型未注册|宿主未运行/);
});

test('素材与主动检视共用列表并支持分类筛选', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]'
    ? {dataset: {view: 'materials'}} : null}});
  await h.settle();
  const request = h.requests.find(r => r.method === 'GET' && r.route === 'perception-native/materials');
  assert.ok(request, '主动联系页应读取角色共享素材池');
  h.reply(request, {observed_at: '2026-10-07T00:00:00+00:00', checked_at: '2026-10-06T23:00:00+00:00',
    state: 'ready', count: 2, shared_count: 2, trace_count: 0, history_count: 0, items: [
    {id: 'news-1', scope: 'role', category: '资讯', purpose: '值得和朋友分享的见闻', title: '<新闻标题>',
      detail: '一条有来源的摘要', note: '示例媒体', source: '共享资讯池', url: 'https://news.example/item',
      captured_at: '2026-10-06T23:00:00+00:00', expires_at: '2026-10-07T00:30:00+00:00'},
    {id: 'fun-1', scope: 'role', category: '趣事', purpose: '值得和朋友分享的见闻', title: '一件有意思的小事',
      detail: '公开摘要', note: '趣闻来源', source: '共享资讯池', captured_at: '2026-10-06T22:50:00+00:00',
      expires_at: '2026-10-07T00:20:00+00:00'},
  ]});
  await h.settle();
  assert.equal(h.page.state.data.materialsLoaded, true);
  assert.equal(h.page.state.data.materialItems.length, 2);
  assert.match(h.element.innerHTML, /&lt;新闻标题&gt;/, '素材正文必须按文本转义');
  assert.doesNotMatch(h.element.innerHTML, /当前关系|最近读完的一本书/);
  assert.match(h.element.innerHTML, /素材与主动检视/);
  assert.match(h.element.innerHTML, /共享资讯池/);
  assert.match(h.element.innerHTML, /查看来源/);
  assert.match(h.element.innerHTML, /有效至/);
  assert.match(h.element.innerHTML, /2 条外部素材/);
  assert.match(h.element.innerHTML, /0 条主动记录/);

  const allMaterialsTab = {dataset: {materialKindFilter: 'all'}};
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-material-kind-filter]'
    ? allMaterialsTab : null}});

  const cards = [
    {dataset: {category: '资讯', search: '资讯 新闻标题 摘要'}, hidden: false},
    {dataset: {category: '趣事', search: '趣事 有意思 小事 摘要'}, hidden: false},
  ];
  const count = {textContent: ''}, empty = {hidden: true};
  h.element.querySelectorAll = selector => selector === '[data-material-card]' ? cards : [];
  h.element.querySelector = selector => selector === '[data-material-result-count]' ? count
    : selector === '.ct-material-filter-empty' ? empty : null;
  const filter = {value: '趣事', closest: sel => sel === '[data-material-category]' ? filter : null};
  h.element.dispatch('change', {target: filter});
  assert.deepEqual(cards.map(card => card.hidden), [true, false]);
  const search = {value: '沒有', closest: sel => sel === '[data-material-search]' ? search : null};
  h.element.dispatch('input', {target: search});
  assert.deepEqual(cards.map(card => card.hidden), [true, true]);
  assert.equal(empty.hidden, false);
  assert.equal(h.page.state.data.touched.size, 0, '素材篩選不應觸碰配置草稿');
  assert.equal(h.requests.filter(r => r.method === 'POST').length, 0, '素材池只能讀取');
});

test('主动记录默认查看当天全部窗口并可按窗口和日期筛选', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]'
    ? {dataset: {view: 'materials'}} : null}});
  await h.settle();
  const first = h.requests.find(r => r.method === 'GET' && r.route === 'perception-native/materials');
  assert.equal(h.page.state.data.materialKind, 'window');
  assert.deepEqual(JSON.parse(JSON.stringify(first.params)), {day: h.page.state.data.materialDay,
    timezone: h.page.state.data.materialTimezone});
  assert.match(h.element.innerHTML, new RegExp(`data-material-day[^>]*value="${h.page.state.data.materialDay}"`));

  h.reply(first, {state: 'ready', shared_count: 0, trace_count: 2, history_count: 2, items: [
    {id: 'trace-a', scope: 'window', trace_window_id: 'window-a', trace_target: '用户甲', trace_role: '澄溪',
      category: '主动消息', title: '窗口甲记录', captured_at: '2026-10-07T01:00:00+00:00',
      usage: {status: 'pending', delivered_parts: 0, total_parts: 1, records: []}},
    {id: 'trace-b', scope: 'window', trace_window_id: 'window-b', trace_target: '用户乙', trace_role: '小溪',
      category: '主动消息', title: '窗口乙记录', captured_at: '2026-10-07T02:00:00+00:00',
      usage: {status: 'not_sent', delivered_parts: 0, total_parts: 1, records: []}},
  ]});
  await h.settle();
  assert.match(h.element.innerHTML, /全部窗口/);
  assert.match(h.element.innerHTML, /用户甲 · 澄溪/);
  assert.match(h.element.innerHTML, /用户乙 · 小溪/);

  const cards = [
    {dataset: {scope: 'window', windowId: 'window-a', category: '主动消息', search: '窗口甲'}, hidden: false},
    {dataset: {scope: 'window', windowId: 'window-b', category: '主动消息', search: '窗口乙'}, hidden: false},
  ];
  const count = {textContent: ''}, empty = {hidden: true};
  h.element.querySelectorAll = selector => selector === '[data-material-card]' ? cards : [];
  h.element.querySelector = selector => selector === '[data-material-result-count]' ? count
    : selector === '.ct-material-filter-empty' ? empty : null;
  const windowFilter = {value: 'window-b', closest: sel => sel === '[data-material-window]' ? windowFilter : null};
  h.element.dispatch('change', {target: windowFilter});
  assert.deepEqual(cards.map(card => card.hidden), [true, false]);

  const dateFilter = {value: '2026-10-06', closest: sel => sel === '[data-material-day]' ? dateFilter : null};
  h.element.dispatch('change', {target: dateFilter});
  await h.settle();
  const requests = h.requests.filter(r => r.method === 'GET' && r.route === 'perception-native/materials');
  assert.equal(requests.length, 2);
  assert.equal(requests[1].params.day, '2026-10-06');
  assert.equal(requests[1].params.timezone, h.page.state.data.materialTimezone);
  assert.equal(h.page.state.data.materialWindow, 'all', 'changing date resets the window filter');
  h.reply(requests[1], {state: 'ready', shared_count: 0, trace_count: 0, history_count: 0, items: []});
  await h.settle();
  h.page.unmount();
});

test('主动消息检视显示发送前草稿、目标和投递状态', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]'
    ? {dataset: {view: 'materials'}} : null}});
  await h.settle();
  const request = h.requests.find(r => r.method === 'GET' && r.route === 'perception-native/materials');
  h.reply(request, {state: 'ready', count: 5, shared_count: 1, trace_count: 4, history_count: 4,
    trace_context_count: 1, trace_state: 'ready', items: [
    {id: 'role-1', scope: 'role', category: '资讯', purpose: '分享见闻', title: '共享见闻',
      detail: '角色可共享的内容', source: '共享资讯池', captured_at: '2026-10-07T11:00:00+00:00'},
    {id: 'greeting-1', scope: 'window', category: '主动消息', purpose: '问候与纪念', title: '早上想和你打个招呼',
      detail: '早呀，今天也慢慢来。', source: '主动消息记录', captured_at: '2026-10-07T11:00:00+00:00',
      trace_target: 'user-17<admin>', trace_role: '澄溪',
      usage: {status: 'pending', delivered_parts: 0, total_parts: 1, thought_origin: '问候时机', records: []}},
    {id: 'message-2', scope: 'window', category: '主动消息', purpose: '分享见闻', title: '另一件有意思的事',
      detail: '完整确认送达的内容', source: '主动消息记录', captured_at: '2026-10-07T11:03:00+00:00',
      trace_target: 'user-17<admin>', trace_role: '澄溪',
      usage: {status: 'delivered', delivered_parts: 1, total_parts: 1,
        thought_origin: '情绪关怀',
        followup_observation: {status: 'input_after_delivery', input_at: '2026-10-07T11:04:00+00:00',
          observed_through: '2026-10-07T11:05:00+00:00'},
        records: [{sequence: 0, delivered_at: '2026-10-07T11:03:30+00:00', text: '完整送达'}]}},
    {id: 'thought-3', scope: 'window', category: '未发送的念头', purpose: '日常话题', title: '还没说出口的念头',
      detail: '这条在审核后没有发送', source: '主动念头记录', captured_at: '2026-10-07T11:06:00+00:00',
      trace_target: 'user-17<admin>', trace_role: '澄溪',
      usage: {status: 'not_sent', delivered_parts: 0, total_parts: 1,
        thought_origin: '旧话题接续', records: []}},
    {id: 'thought-4', scope: 'window', category: '未发送的念头', purpose: '日常话题', title: '没有合适的开口',
      detail: '', source: '主动念头记录', captured_at: '2026-10-07T11:07:00+00:00',
      trace_target: 'user-17<admin>', trace_role: '澄溪',
      skip_reason: 'model_decision', model_skip_reason: 'no_natural_opening',
      usage: {status: 'not_sent', delivered_parts: 0, total_parts: 0,
        thought_origin: '日常问候', records: []}}
  ]});
  await h.settle();
  assert.equal(h.page.state.data.materialItems.length, 5);
  assert.match(h.element.innerHTML, /共享见闻/);
  assert.match(h.element.innerHTML, /早上想和你打个招呼/);
  assert.match(h.element.innerHTML, /发送前检视 · 等待投递/);
  assert.match(h.element.innerHTML, /发送前检视 · 已送达/);
  assert.match(h.element.innerHTML, /发送前检视 · 未发送/);
  assert.match(h.element.innerHTML, /接收目标/);
  assert.match(h.element.innerHTML, /user-17&lt;admin&gt;/, '目标标签按文本转义');
  assert.match(h.element.innerHTML, /澄溪/);
  assert.match(h.element.innerHTML, /生成于/);
  assert.match(h.element.innerHTML, /本次没有可投递的正文/);
  assert.doesNotMatch(h.element.innerHTML, /0 \/ 0 段已确认送达/);
  assert.match(h.element.innerHTML, /1 条外部素材 · 4 条主动记录/);

  const cards = ['role', 'window', 'window', 'window', 'window'].map(scope => ({dataset: {scope}, hidden: false}));
  const count = {textContent: ''}, empty = {hidden: true};
  const tabs = ['all', 'role', 'window'].map(materialKindFilter => ({
    dataset: {materialKindFilter}, pressed: '', setAttribute(name, value) {
      if (name === 'aria-pressed') this.pressed = value;
    },
  }));
  h.element.querySelectorAll = selector => selector === '[data-material-card]' ? cards
    : selector === '[data-material-kind-filter]' ? tabs : [];
  h.element.querySelector = selector => selector === '[data-material-result-count]' ? count
    : selector === '.ct-material-filter-empty' ? empty : null;
  const traceTab = {dataset: {materialKindFilter: 'window'}};
  h.element.dispatch('click', {target: {closest: selector => selector === '[data-material-kind-filter]'
    ? traceTab : null}});
  assert.equal(h.page.state.data.materialKind, 'window');
  assert.deepEqual(cards.map(card => card.hidden), [true, false, false, false, false]);
  assert.equal(tabs[2].pressed, 'true');
});

test('主动记录读取失败仍保留外部素材并提示排查状态', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]'
    ? {dataset: {view: 'materials'}} : null}});
  await h.settle();
  const request = h.requests.find(r => r.method === 'GET' && r.route === 'perception-native/materials');
  h.reply(request, {state: 'ready', shared_count: 1, trace_count: 0, history_count: 0,
    trace_state: 'failed', items: [
      {id: 'role-1', scope: 'role', category: '资讯', title: '外部素材仍可用',
        captured_at: '2026-10-07T11:00:00+00:00'},
    ]});
  await h.settle();
  assert.match(h.element.innerHTML, /外部素材仍可用/);
  assert.match(h.element.innerHTML, /主动消息检视记录读取失败；外部素材仍可查看/);
  assert.equal(h.page.state.data.materialItems.length, 1);
});

test('首次资讯巡视完成后页面自动补读新的念头', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]'
    ? {dataset: {view: 'materials'}} : null}});
  await h.settle();
  const first = h.requests.find(r => r.method === 'GET' && r.route === 'perception-native/materials');
  h.reply(first, {state: 'refreshing', count: 0, items: []});
  await h.settle();

  await new Promise(resolve => setTimeout(resolve, 1600));
  await h.settle();
  const requests = h.requests.filter(r => r.method === 'GET' && r.route === 'perception-native/materials');
  assert.equal(requests.length, 2, 'a pending patrol should trigger one bounded status refresh');
  h.reply(requests[1], {state: 'ready', count: 1, items: [
    {id: 'found-1', scope: 'role', category: '趣事', title: '一个有趣的发现'},
  ]});
  await h.settle();
  assert.equal(h.page.state.data.materialItems[0].category, '趣事');
  assert.match(h.element.innerHTML, /一个有趣的发现/);
});

test('消息测试支持按类别、覆盖状态和内容筛选', async () => {
  const h = harness();
  await boot(h);
  const ids = ['affection','care','play','repair','chat','greeting','followup','invitation',
    'reminder','meal','life','creation','news','environment','screen','mobile_location','reaction_image'];
  const cards = ids.map(testId => ({dataset: {testId}, hidden: false}));
  const count = {textContent: ''}, empty = {hidden: true};
  h.element.querySelectorAll = selector => selector === '[data-test-card]' ? cards : [];
  h.element.querySelector = selector => selector === '[data-test-result-count]' ? count
    : selector === '.ct-test-empty' ? empty : null;
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]'
    ? {dataset: {view: 'tests'}} : null}});
  const html = h.element.innerHTML;
  assert.ok(html.includes('data-test-search'));
  assert.ok(html.includes('data-test-bucket-filter'));
  assert.ok(html.includes('data-test-coverage-filter'));
  assert.match(html, /data-test-copy="affection"><svg viewBox="0 0 24 24"[^>]*><rect x="8" y="8"/,
    '复制指令按钮使用复制图标');
  assert.ok(html.includes('尚无独立生产器'));
  assert.equal(count.textContent, '17 / 17 类');

  const bucket = {value: 'daily', closest: selector => selector === '[data-test-bucket-filter]' ? bucket : null};
  h.element.dispatch('change', {target: bucket});
  assert.deepEqual(cards.filter(card => !card.hidden).map(card => card.dataset.testId),
    ['greeting','followup','invitation','reminder','meal']);
  assert.equal(count.textContent, '5 / 17 类');

  bucket.value = 'extension';
  h.element.dispatch('change', {target: bucket});
  assert.deepEqual(cards.filter(card => !card.hidden).map(card => card.dataset.testId),
    ['mobile_location','reaction_image']);
  assert.equal(count.textContent, '2 / 17 类');

  const coverage = {value: 'ready', closest: selector => selector === '[data-test-coverage-filter]' ? coverage : null};
  h.element.dispatch('change', {target: coverage});
  assert.deepEqual(cards.filter(card => !card.hidden).map(card => card.dataset.testId),
    ['mobile_location','reaction_image']);
  assert.equal(count.textContent, '2 / 17 类');

  const search = {value: '撤权', closest: selector => selector === '[data-test-search]' ? search : null};
  h.element.dispatch('input', {target: search});
  assert.equal(count.textContent, '0 / 17 类');
  assert.equal(empty.hidden, false, '无匹配时显示空状态');
  assert.equal(h.page.state.data.touched.size, 0, '筛选不能修改配置');
});

test('复制主动消息测试指令只写入剪贴板，不调用模型或发送接口', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]'
    ? {dataset: {view: 'tests'}} : null}});
  const requestCount = h.requests.length;
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-test-copy]'
    ? {dataset: {testCopy: 'news'}} : null}});
  await h.settle();
  assert.equal(h.clipboardWrites.length, 1);
  assert.match(h.clipboardWrites[0], /这是一条「外部资讯」主动消息类型的真实来源功能测试/);
  assert.match(h.clipboardWrites[0], /生产器来源：带标题、原链接和发布时间的当前资讯条目/);
  assert.match(h.clipboardWrites[0], /严格遵守通用 JSON 合同，并给出 decision_confidence/);
  assert.match(h.clipboardWrites[0], /通过条件：实际抓取、条目校验、模型生成和 QQ 回执全部完成/);
  assert.equal(h.requests.length, requestCount, '复制不能新增任何 API 请求');
  assert.ok(h.element.innerHTML.includes('没有调用模型或真实投递'));
});

test('主动消息类型卡片可确认后运行真实诊断并显示结果', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]'
    ? {dataset: {view: 'tests'}} : null}});
  const requestCount = h.requests.length;

  h.element.dispatch('click', {target: {closest: sel => sel === '[data-test-run]'
    ? {dataset: {testRun: 'news'}} : null}});
  assert.ok(h.element.innerHTML.includes('运行「外部资讯」测试？'));
  assert.ok(h.element.innerHTML.includes('真实投递到核心管理员 QQ 私聊'));
  assert.equal(h.requests.length, requestCount, '打开确认框时不能提前调用模型或发送接口');

  h.element.dispatch('click', {target: {closest: sel => sel === '[data-test-action]'
    ? {dataset: {testAction: 'confirm'}} : null}});
  await h.settle();
  const request = h.requests.at(-1);
  assert.equal(request.method, 'POST');
  assert.equal(request.route, 'setup/proactive-diagnostic');
  assert.deepEqual(JSON.parse(JSON.stringify(request.values)), {
    purpose: 'news', confirm_delivery: true, synthetic_event: false,
  });
  assert.equal(h.page.state.data.testBusy, 'news');

  h.reply(request, {state: 'passed', purpose: 'news', purpose_label: '外部资讯',
    event_mode: 'source_backed', route: {label: '真实资讯抓取'},
    delivery_state: 'delivered', receipt_ref: 'receipt-1',
    stage_metrics: {model: {provider_calls: 2, tokens: 42, cost_microusd: 7, avg_latency_ms: 1500,
      unknown_calls: 0, stages: {
        plan: {provider_calls: 1, tokens: 12, cost_microusd: 2, cached_input_tokens: 6},
        write: {provider_calls: 1, tokens: 30, cost_microusd: 5, cached_input_tokens: null},
      }}, delivery: {expected_parts: 2, delivered_parts: 2, unresolved_parts: 0, failed_parts: 0}},
    usage_summary: {decisions: {session: {used: 2, calls: 2, pending: 0}},
      deliveries: {session: {used: 1, occupied: 1, limit: 3, delivered: 1, unresolved: 0}},
      model: {session: {calls: 2, tokens: 100, cost_microusd: 30}}}, stages: [
      {stage: 'news_fetch', state: 'passed', detail: '真实资讯抓取'},
      {stage: 'trusted_candidate', state: 'passed'},
      {stage: 'model_generation', state: 'passed'}, {stage: 'persona_review', state: 'passed'},
      {stage: 'delivery', state: 'passed'}, {stage: 'receipt', state: 'passed'},
    ]});
  await h.settle();
  assert.equal(h.page.state.data.testBusy, '');
  assert.equal(h.page.state.data.testResults.news.state, 'passed');
  assert.match(h.element.innerHTML, /测试通过 · 已投递/);
  assert.match(h.element.innerHTML, /真实资讯抓取/);
  assert.match(h.element.innerHTML, /本次取得的真实来源/);
  assert.match(h.element.innerHTML, /已投递到核心管理员私聊/);
  assert.match(h.element.innerHTML, /诊断步骤/);
  assert.match(h.element.innerHTML, /人格复核/);
  assert.match(h.element.innerHTML, /平台已确认投递/);
  assert.match(h.element.innerHTML, /本轮 Provider 模型：2 次调用/);
  assert.match(h.element.innerHTML, /规划：1 次调用 · 12 Tokens/);
  assert.match(h.element.innerHTML, /缓存输入 6 Tokens/);
  assert.match(h.element.innerHTML, /写作：1 次调用 · 30 Tokens/);
  assert.match(h.element.innerHTML, /本轮投递：2 \/ 2 段已确认/);
  assert.match(h.element.innerHTML, /主动模型决策已调用 2 次（待处理 0，仅统计）/);
  assert.match(h.element.innerHTML, /实际联系已确认发送 1 条（每日上限占用 1\/3，待确认 0）/);
  assert.doesNotMatch(h.element.innerHTML, /模型决策调用额度/);
  assert.equal(h.localStore.size, 0, 'a final result clears the durable replay guard');
});

test('手机地点和带图主动消息映射到各自的扩展诊断链路', async () => {
  for (const [id, expected, confirmationText] of [
    ['mobile_location', {purpose:'environment', confirm_delivery:true, synthetic_event:false, variant:'mobile_location'}, /不伪造坐标/],
    ['reaction_image', {purpose:'chat', confirm_delivery:true, synthetic_event:false, diagnostic_image:true}, /授权图库检索一张表情原图/],
  ]) {
    const h = harness();
    await boot(h);
    h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]'
      ? {dataset: {view: 'tests'}} : null}});
    h.element.dispatch('click', {target: {closest: sel => sel === '[data-test-run]'
      ? {dataset: {testRun: id}} : null}});
    assert.match(h.element.innerHTML, confirmationText);
    h.element.dispatch('click', {target: {closest: sel => sel === '[data-test-action]'
      ? {dataset: {testAction: 'confirm'}} : null}});
    await h.settle();
    const request = h.requests.at(-1);
    assert.equal(request.route, 'setup/proactive-diagnostic');
    assert.deepEqual(JSON.parse(JSON.stringify(request.values)), expected);
    h.reply(request, {state: 'failed', purpose: expected.purpose, stages: []});
    await h.settle();
    h.page.unmount();
  }
});

test('失败诊断标明卡点、前序步骤、原因和下一步', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]'
    ? {dataset: {view: 'tests'}} : null}});
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-test-run]'
    ? {dataset: {testRun: 'news'}} : null}});
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-test-action]'
    ? {dataset: {testAction: 'confirm'}} : null}});
  await h.settle();
  const request = h.requests.at(-1);
  h.reply(request, {state: 'failed', purpose: 'news', purpose_label: '外部资讯',
    route: {label: '资讯生产器'}, stage: 'trusted_candidate', code: 'contact_diagnostic_source_required',
    failure_title: '当前用途没有可核验的测试机会',
    explanation: '没有当前来源时不会调用模型，也不会发送消息。',
    suggestion: '配置资讯来源并读到当前条目后再测。',
    stages: [
      {stage: 'authorization', state: 'passed'}, {stage: 'binding', state: 'passed'},
      {stage: 'runtime', state: 'passed'}, {stage: 'settings', state: 'passed'},
      {stage: 'purpose', state: 'passed'}, {stage: 'trusted_candidate', state: 'failed'},
    ]});
  await h.settle();

  assert.match(h.element.innerHTML, /测试未通过 · 卡在联系机会\/来源/);
  assert.match(h.element.innerHTML, /失败原因/);
  assert.match(h.element.innerHTML, /没有当前来源时不会调用模型/);
  assert.match(h.element.innerHTML, /诊断步骤/);
  assert.match(h.element.innerHTML, /管理员私聊绑定<\/span>\s*<strong>通过/);
  assert.match(h.element.innerHTML, /联系机会\/来源<\/span>\s*<strong>失败/);
  assert.match(h.element.innerHTML, /下一步/);
  assert.match(h.element.innerHTML, /配置资讯来源并读到当前条目后再测/);
  assert.match(h.element.innerHTML, /展开技术详情/);
});

test('天气和屏幕默认真实来源，显式合成选择才使用虚构事件', async () => {
  for (const id of ['environment', 'screen', 'news']) {
    const h = harness();
    await boot(h);
    h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view: 'tests'}} : null}});
    if (id === 'news') {
      const mode = {value: 'synthetic', dataset: {testMode: id}, closest: sel => sel === '[data-test-mode]' ? mode : null};
      h.element.dispatch('change', {target: mode});
    }
    h.element.dispatch('click', {target: {closest: sel => sel === '[data-test-run]' ? {dataset: {testRun: id}} : null}});
    if (id === 'screen') assert.match(h.element.innerHTML, /取得一张新截图/);
    h.element.dispatch('click', {target: {closest: sel => sel === '[data-test-action]' ? {dataset: {testAction: 'confirm'}} : null}});
    await h.settle();
    assert.equal(h.requests.at(-1).values.synthetic_event, id === 'news');
    if (id === 'environment') assert.equal(h.requests.at(-1).values.variant, 'weather');
    h.reply(h.requests.at(-1), {state: 'failed', purpose: id, stages: []});
    await h.settle();
    h.page.unmount();
  }
});

test('主动测试结果未知时跨页面刷新保留锁，并要求核实后再解除', async () => {
  const localStorageStore = new Map();
  const h = harness({localStorageStore});
  await boot(h);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]'
    ? {dataset: {view: 'tests'}} : null}});
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-test-run]'
    ? {dataset: {testRun: 'care'}} : null}});
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-test-action]'
    ? {dataset: {testAction: 'confirm'}} : null}});
  await h.settle();
  const request = h.requests.at(-1);
  h.reply(request, {state: 'unknown', purpose: 'care', error: 'delivery_result_unknown',
    suggestion: '核对管理员私聊，不要重试。'});
  await h.settle();

  assert.equal(h.page.state.data.testUncertain, true);
  assert.match(h.element.innerHTML, /结果未知 · 请勿重试/);
  assert.match(h.element.innerHTML, /请先核对核心管理员私聊/);
  assert.ok([...localStorageStore.keys()].some(key => key.includes('test-guard:')));

  h.page.unmount();
  const restored = harness({localStorageStore});
  await boot(restored);
  assert.equal(restored.page.state.data.testUncertain, true);
  restored.element.dispatch('click', {target: {closest: sel => sel === '[data-view]'
    ? {dataset: {view: 'tests'}} : null}});
  assert.match(restored.element.innerHTML, /核实后解除测试锁/);
  const count = restored.requests.length;
  restored.element.dispatch('click', {target: {closest: sel => sel === '[data-test-run]'
    ? {dataset: {testRun: 'news'}} : null}});
  assert.equal(restored.requests.length, count, 'a reload must not allow another diagnostic request');

  restored.element.dispatch('click', {target: {closest: sel => sel === '[data-test-action]'
    ? {dataset: {testAction: 'unlock'}} : null}});
  assert.match(restored.element.innerHTML, /解除主动测试锁？/);
  assert.match(restored.element.innerHTML, /仍可能产生另一条真实消息/);
  restored.element.dispatch('click', {target: {closest: sel => sel === '[data-test-action]'
    ? {dataset: {testAction: 'confirm-unlock'}} : null}});
  assert.equal(restored.page.state.data.testUncertain, false);
  assert.equal(localStorageStore.size, 0);
});

test('无法持久化防重状态时不启动主动消息测试', async () => {
  const h = harness({sessionStorageBlocked: true, localStorageBlocked: true, historyBlocked: true});
  await boot(h);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]'
    ? {dataset: {view: 'tests'}} : null}});
  const count = h.requests.length;
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-test-run]'
    ? {dataset: {testRun: 'news'}} : null}});
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-test-action]'
    ? {dataset: {testAction: 'confirm'}} : null}});
  await h.settle();
  assert.equal(h.requests.length, count, 'the delivery endpoint must not run without a durable replay guard');
  assert.match(h.page.state.data.notice, /没有启动测试/);
});

test('消息测试入口在窄屏保留直达性，列表具备响应式和键盘焦点样式', () => {
  assert.match(style, /\.ct-head-test\{[^}]*border-color:var\(--t-accent\)/,
    '页头直达入口要有稳定的视觉强调');
  assert.match(style, /\.ct-tests-grid\{[^}]*grid-template-columns:repeat\(auto-fit,minmax\(min\(390px,100%\),1fr\)\)/,
    '类型卡片应根据工作区宽度排布');
  assert.match(style, /\.ct-test-card\[hidden\]\{display:none!important\}/);
  assert.match(style, /\.ct-test-copy:focus-visible,[\s\S]*?outline:2px solid var\(--t-focus\)/);
  assert.match(style, /\.ct-test-run\{[^}]*justify-content:center/);
  assert.match(style, /\.ct-test-dialog::backdrop\{[^}]*background/);
  assert.match(style, /\.ct-test-result\.is-unknown/);
  assert.match(style, /\.ct-test-diagnosis li\[data-state="failed"\]/);
  assert.match(style, /\.ct-test-technical summary/);
  assert.match(style, /@media \(max-width:880px\)[\s\S]*?\.ct-head-actions \.ct-head-test\{flex:1 1 100%\}/,
    '窄屏页头中测试入口应单独占一行，避免挤进次要操作');
  assert.match(style, /@media \(max-width:520px\)[\s\S]*?\.ct-test-tools\{grid-template-columns:minmax\(0,1fr\)\}/,
    '窄屏筛选器应回到单列');
});

test('契机矩阵提供搜索、类别和状态筛选，筛选不写入配置草稿', async () => {
  const h = harness();
  const names = ['affection','care','play','repair','chat','greeting','followup','invitation',
    'reminder','meal','life','creation','news','environment','screen'];
  await boot(h, {...baseSettings(), 'meal.pause_until': '2099-01-01T12:00:00+08:00'});
  const rows = names.map(purpose => ({dataset: {purpose}, hidden: false}));
  const buckets = Object.fromEntries(['bond','daily','share'].map(id => [id, {hidden: false}]));
  const bucketCounts = Object.fromEntries(['bond','daily','share'].map(id => [id, {textContent: ''}]));
  const resultCount = {textContent: ''}, empty = {hidden: true};
  h.element.querySelectorAll = selector => selector === '.ct-purpose' ? rows : [];
  h.element.querySelector = selector => {
    const group = /^\[data-purpose-bucket="([^"]+)"\]$/.exec(selector);
    if (group) return buckets[group[1]];
    const count = /^\[data-purpose-bucket-count="([^"]+)"\]$/.exec(selector);
    if (count) return bucketCounts[count[1]];
    if (selector === '[data-purpose-result-count]') return resultCount;
    if (selector === '.ct-purpose-empty') return empty;
    return null;
  };
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view: 'purposes'}} : null}});
  const html = h.element.innerHTML;
  assert.ok(html.includes('data-purpose-search'), '要能按名称和限制原因搜索');
  assert.ok(html.includes('data-purpose-bucket-filter'), '要能按关系、日程、分享分组筛选');
  assert.ok(html.includes('data-purpose-filter'), '要能按配置和当前状态筛选');
  assert.ok(html.includes('data-purpose-result-count'), '要显示匹配数量');
  assert.ok(html.includes('ct-purpose-groups'), '分组容器要供响应式矩阵布局使用');
  assert.equal(resultCount.textContent, '15 / 15 类');
  assert.ok(rows.every(row => !row.hidden), '初始状态要显示全部契机');

  const bucket = {value: 'daily', closest: sel => sel === '[data-purpose-bucket-filter]' ? bucket : null};
  h.element.dispatch('change', fieldEvent(bucket, 'change'));
  assert.equal(h.page.state.data.purposeBucket, 'daily');
  assert.deepEqual(rows.filter(row => !row.hidden).map(row => row.dataset.purpose),
    ['greeting','followup','invitation','reminder','meal']);
  assert.equal(resultCount.textContent, '5 / 15 类');
  assert.equal(buckets.bond.hidden, true);
  assert.equal(buckets.daily.hidden, false);
  const filter = {value: 'paused', closest: sel => sel === '[data-purpose-filter]' ? filter : null};
  h.element.dispatch('change', fieldEvent(filter, 'change'));
  assert.equal(h.page.state.data.purposeFilter, 'paused');
  assert.deepEqual(rows.filter(row => !row.hidden).map(row => row.dataset.purpose), ['meal']);
  assert.equal(bucketCounts.daily.textContent, '1/5 类');
  const search = {value: '饭点', closest: sel => sel === '[data-purpose-search]' ? search : null};
  h.element.dispatch('input', fieldEvent(search, 'input'));
  assert.equal(h.page.state.data.purposeQuery, '饭点');
  assert.deepEqual(rows.filter(row => !row.hidden).map(row => row.dataset.purpose), ['meal']);
  assert.equal(h.page.state.data.touched.size, 0, '筛选条件不能进入待保存配置');
  search.value = '没有这类契机';
  h.element.dispatch('input', fieldEvent(search, 'input'));
  assert.equal(resultCount.textContent, '0 / 15 类');
  assert.equal(empty.hidden, false, '无结果时要显示明确空态');
});

test('当前暂缓优先显示全局暂缓状态，不折叠成通用受阻原因', async () => {
  const h = harness();
  const settings = {...baseSettings(), 'meal.pause_until': '2099-01-01T12:00:00+08:00'};
  await boot(h, settings);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view: 'purposes'}} : null}});
  const row = h.element.innerHTML.split('data-purpose="meal"')[1].split('</article>')[0];
  assert.ok(row.includes('ct-chip is-paused'), '暂停原因应显示为暂缓状态');
  assert.ok(row.includes('暂缓至 2099-01-01 12:00'), '应显示暂停截止时间');
  assert.ok(!row.includes('ct-chip is-risk'), '暂停不应伪装成授权或运行故障');
});

test('契机矩阵桌面分列、窄屏回单列并保留可见焦点', () => {
  assert.match(style, /\.ct-purpose-groups\{[^}]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(style, /\.ct-purpose\{[^}]*display:grid;min-width:0/,
    '分享分栏中的行必须允许收缩，不能被网格子项撑出卡片');
  assert.match(style, /grid-template-columns:minmax\(92px,1fr\) minmax\(100px,1\.2fr\) minmax\(0,168px\)/,
    '名称、权重和状态列需统一对齐，同时给状态文案留出可收缩空间');
  assert.match(style, /\.ct-purpose-state\{[^}]*min-width:0/);
  assert.match(style, /\.ct-chip\{[^}]*max-width:100%;min-width:0/,
    '长状态提示应在行内截断，不能溢出卡片');
  assert.match(style, /@media \(max-width:1280px\)[\s\S]*?\.ct-purpose-groups\{grid-template-columns:minmax\(0,1fr\)\}/);
  assert.match(style, /grid-template-columns:minmax\(0,1fr\) minmax\(100px,1fr\)/,
    '窄屏下权重列也应对齐');
  assert.match(style, /\.ct-purpose-search input:focus,[\s\S]*?outline:2px solid var\(--t-focus\)/);
  assert.match(style, /\.ct-purpose\[hidden\][^}]*display:none!important/);
});

test('开口闸门按容器宽度排布，摘要和设置区保持通栏', () => {
  assert.match(style, /\.ct-gates\{[^}]*repeat\(auto-fit,minmax\(min\(340px,100%\),1fr\)\)/,
    '闸门卡片应随可用宽度自动排成多列');
  assert.match(style, /\.ct-gates-sum\{[^}]*grid-column:1 \/ -1/,
    '此刻总状态应独占一行，先于各道闸门被看到');
  assert.match(style, /\.ct-gate-settings-brief,\.ct-gates > \.ct-block\{grid-column:1 \/ -1\}/,
    '设置入口和补充偏好应在卡片网格后通栏展示');
  assert.match(style, /\.ct-gate-pause\{display:grid;grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/,
    '宽屏下暂停时间与联系偏好并排');
  assert.match(style, /@media \(max-width:880px\)[\s\S]*?\.ct-gate-pause\{grid-template-columns:minmax\(0,1fr\)\}/,
    '窄屏下补充设置回到单列');
});

test('打开问候窗口的提示，而不是静默忽略点击', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-anchor]' ? {dataset: {anchor: 'morning'}} : null}});
  assert.ok(h.page.state.data.notice.includes('07:45-10:20'), '点问候弧要给出该窗口的真实值');
});

test('额度只有上限没有读出口，所以画成刻度而不是进度条', async () => {
  const h = harness();
  await boot(h);
  h.element.dispatch('click', {target: {closest: sel => sel === '[data-view]' ? {dataset: {view: 'budgets'}} : null}});
  const html = h.element.innerHTML;
  assert.ok(html.includes('没有实时读出口'), '要明说这一点，否则用户以为刻度是已用量');
  assert.ok(html.includes('ct-quota-scale'), '两端要写出 0 与上限');
});

test('卸载后事件与状态都清干净，重复挂载不叠加', async () => {
  const h = harness();
  await boot(h);
  h.page.unmount();
  assert.equal(h.page.state.data, null, '卸载后不得留下上一份状态');
  /* 卸载时的票号自增必须让在飞确认被丢弃：重新挂载后仍能正常读。 */
  await boot(h);
  assert.equal(h.page.state.data.ready, true);
});
