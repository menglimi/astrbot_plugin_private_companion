/* 记忆设置区的工作台适配层。

   这一区此前自带一套字段渲染、草稿与保存生命周期，现在归 workbench-page.js。
   与表达页相比它有三处独有形状，正是最容易写错的地方，所以单独成测：

   1. 读取侧下发**短名** `enabled`，写入侧后端按**长名** `memory.enabled` 查表
      （`ConfigurationService._normalize_patch` 按 field_id 查表，且记忆路由
      自己的测试就断言长名）。
   2. 读 `edit_token`、写 `expected_edit_token`（`memory_api.py` 的 required 集合）。
   3. 读取时**不能带 query**（该路由显式拒绝查询串），且保存确认扁平返回、不带 status。

   另外覆盖宿主每次 render 整块重写 innerHTML 时的 attach() 复用路径。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PAGE = '../pages/陪伴小屋/';
const workbench = fs.readFileSync(path.resolve(__dirname, PAGE + 'workbench-page.js'), 'utf8');
const page = fs.readFileSync(path.resolve(__dirname, PAGE + 'memory-settings-section.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const drains = async (times = 12) => { for (let index = 0; index < times; index += 1) await flush(); };
const TOKEN = 'a'.repeat(64);
const HOST = 'astrbot_plugin_private_companion';
const PREFIX = '/' + HOST + '/';
const MEMORY_PATH = PREFIX + 'memory';
const SETTINGS_ROUTE = 'memory/settings';
const plain = value => JSON.parse(JSON.stringify(value));

/* 记忆分区的注册表契约（短名）。**逐条照 companion_v2/memory/configuration.py 抄**：
   下面这份 DEFAULTS 的键集合就是「后端注册了哪些字段」的断言基准，
   加字段时两边一起改。 */
const DEFAULTS = {
  enabled: true, automatic_context: true, context_items: 4, context_chars: 6000,
  query_items: 20, query_chars: 20000, source_chars: 8000, turn_chars: 64000, turn_calls: 24,
  core_items: 4, core_chars: 6000, inclusion_chars: 24000,
  reflection_enabled: false, reflection_timezone: 'Asia/Shanghai', reflection_time: '23:50',
  reflection_response_format: 'text', reflection_auto_repair: true,
  group_episode_memory_enabled: true, group_episode_refresh_minutes: 180,
  group_episode_min_messages: 12, max_group_episodes: 10,
  dialogue_episode_memory_enabled: true, dialogue_episode_refresh_messages: 8,
  dialogue_episode_refresh_minutes: 90, max_dialogue_episodes: 12,
  habit_learning_enabled: true, food_menu_recommendation_enabled: true,
  habit_min_count: 3, habit_max_items: 24, habit_refresh_minutes: 360,
};
const LIMITS = {context_items: [0, 16], context_chars: [256, 24000], query_items: [1, 50],
  query_chars: [256, 48000], source_chars: [256, 24000], turn_chars: [1024, 200000],
  turn_calls: [1, 32], core_items: [0, 16], core_chars: [256, 24000], inclusion_chars: [512, 96000],
  group_episode_refresh_minutes: [30, 1440], group_episode_min_messages: [1, 64],
  max_group_episodes: [1, 32], dialogue_episode_refresh_messages: [1, 64],
  dialogue_episode_refresh_minutes: [5, 1440], max_dialogue_episodes: [1, 32],
  habit_min_count: [2, 20], habit_max_items: [8, 80], habit_refresh_minutes: [30, 4320]};
const BOOLEAN = new Set(['enabled', 'automatic_context', 'reflection_enabled', 'reflection_auto_repair',
  'group_episode_memory_enabled', 'dialogue_episode_memory_enabled', 'habit_learning_enabled',
  'food_menu_recommendation_enabled']);
const FIELDS = Object.fromEntries(Object.entries(DEFAULTS).map(([name, value]) => {
  const entry = {kind: BOOLEAN.has(name) ? 'boolean' : typeof value === 'number' ? 'integer' : 'text',
    default: value, apply_mode: 'immediate', editable_layers: ['installation', 'role', 'window']};
  if (LIMITS[name]) [entry.minimum, entry.maximum] = LIMITS[name];
  return [name, entry];
}));

/* 记忆设置读取是**扁平**载荷（不带 sections 包裹）。 */
function settingsStatus(changes = {}, token = TOKEN) {
  const values = {...DEFAULTS, ...changes};
  return {values, edit_token: token, revision: 1, scope: 'window', apply_mode: 'immediate',
    group_id: 'memory', layers: ['installation', 'role', 'window'],
    provenance: Object.fromEntries(Object.keys(values).map(name =>
      [name, {layer: 'window', explicit: true, revision: 1}])),
    fields: FIELDS};
}

/* 一个能被 event.target.closest() 解析的最小元素桩。
   click() 里的 el.id / el.dataset 与 closest('button') 都要能用。 */
function element(id = '', dataset = {}) {
  const node = {
    id, dataset, disabled: false, isConnected: true,
    closest: selector => (selector === 'button' ? (id ? node : null) : null),
    querySelectorAll: () => [], querySelector: () => null,
    addEventListener() {}, removeEventListener() {},
    classList: {add() {}, remove() {}, contains: () => false},
    focus() {},
  };
  return node;
}

function harness({hosted = true, memoryApi = MEMORY_PATH} = {}) {
  const requests = [], store = new Map();
  let inner = '', workbenchHtml = '';
  const hosts = new Map();
  /* 本页把 click/change/submit 绑在根节点上，handler 是闭包私有的。
     桩把注册的监听器收集起来，测试用 dispatch() 走真实的事件路径。 */
  const listeners = {};
  /* 工作台把 input/click 绑在它的挂载点（记忆设置宿主）上，
     测试要能按真实路径驱动它，所以宿主子节点也收集监听器。 */
  const hostListeners = {};
  const childNode = () => ({
    isConnected: true, dataset: {},
    classList: {add() {}, remove() {}, contains: () => false},
    querySelectorAll: () => [], querySelector: () => null,
    addEventListener: (type, fn) => { hostListeners[type] = fn; },
    removeEventListener: type => { delete hostListeners[type]; },
    set innerHTML(value) { workbenchHtml = value; },
    get innerHTML() { return workbenchHtml; },
  });
  const node = {
    isConnected: true, dataset: {},
    classList: {add() {}, remove() {}, contains: () => false},
    querySelectorAll: () => [],
    querySelector: selector => hosts.get(selector) || null,
    addEventListener: (type, fn) => { listeners[type] = fn; },
    removeEventListener: type => { delete listeners[type]; },
    contains: () => true,
  };
  Object.defineProperty(node, 'innerHTML', {
    get: () => inner,
    /* 宿主整块重写 innerHTML：记忆设置区的子节点随之重建。
       与真实页面一致，重建后由 render() 再调 mountSettings()。 */
    set: value => {
      inner = value;
      workbenchHtml = value;
      hosts.set('#memory-settings-host',
        /memory-settings-host/.test(value) ? childNode() : null);
    },
  });
  const sessionStorage = {getItem: key => store.get(key) || null,
    setItem: (key, value) => store.set(key, value), removeItem: key => store.delete(key)};
  const window = {
    addEventListener() {}, removeEventListener() {},
    CompanionProduct: {
      hosted: () => hosted,
      role: () => (memoryApi ? {memory_api: memoryApi} : {}),
      identity: () => ({installation_id: 'inst', operator_id: 'admin'}),
      selection: () => null, choose() {},
    },
    AstrBotPluginPage: {
      ready: () => Promise.resolve({pluginName: HOST}),
      apiGet: (route, body) => new Promise((resolve, reject) =>
        requests.push({method: 'GET', route, body, resolve, reject})),
      apiPost: (route, body) => new Promise((resolve, reject) =>
        requests.push({method: 'POST', route, body, resolve, reject})),
    },
  };
  const document = {
    createElement: () => childNode(),
    querySelector: () => null, addEventListener() {},
    body: {classList: {toggle() {}}}, hidden: false,
    /* render() 会读 document.activeElement.id 来恢复焦点，桩不能给 null。 */
    activeElement: {id: ''},
  };
  const sandbox = {window, document, sessionStorage, localStorage: sessionStorage,
    crypto: {randomUUID: () => 'memory-key'}, setTimeout: () => 0, clearTimeout() {},
    URLSearchParams, Math, Number, Object, JSON, Array, String, CSS: {escape: s => s},
    Date, Promise, Set, Map, RegExp, Error, console};
  vm.runInNewContext(workbench, sandbox);
  vm.runInNewContext(page, sandbox);
  return {page: window.CompanionMemorySettingsSection, node, requests, store,
    /* app.js 以 mount(el, {preview}) 调用本页；preview 是静态预览用的渲染函数。 */
    mount: () => window.CompanionMemorySettingsSection.mount(node),
    wbClick: target => listeners.click({target}),
    click: target => listeners.click({target}),
    /* 工作台自己的点击（保存 / 恢复继承 / 核对）走它挂载点上的监听器。 */
    get html() { return inner; },
    get workbenchHtml() { return workbenchHtml; },
    wbSectionClick: id => listeners.click({target: sectionButton(id)}),
    wbSectionKeydown: (id, key) => listeners.keydown({
      target: sectionButton(id), key, preventDefault() { this.defaultPrevented = true; },
    })};
}

/* 这一块现在是独立模块：mount(element) 直接用 role().memory_api，
   不再有目录读取与分区切换。 */
async function openSettings(harnessed) {
  harnessed.mount();
  await drains();
  return harnessed;
}

/* 内核的「恢复继承」按钮：click 时用 closest('[data-wb-reset]') 取字段名。 */
const resetButton = name => ({dataset: {wbReset: name},
  closest: selector => (selector === '[data-wb-reset]' ? {dataset: {wbReset: name}} : null)});
const sectionButton = name => ({closest: selector =>
  selector === '[data-wb-section]' ? {dataset: {wbSection: name}} : null});

test('every registry field has a label and a section home', async () => {
  /* 这一条防的是「凭印象写字段名」：写错的字段名不会报错，
     只会掉进末尾的兜底分组并显示成原始 id。字段清单从注册表抄在 DEFAULTS 里，
     与 companion_v2/memory/configuration.py 一一对应。 */
  const pageSource = fs.readFileSync(path.resolve(__dirname, PAGE + 'memory-settings-section.js'), 'utf8');
  const sectionBlock = pageSource.slice(pageSource.indexOf('const sections = ['),
    pageSource.indexOf('let host = null'));
  const declared = new Set();
  for (const match of sectionBlock.matchAll(/fields: \[([^\]]*)\]/g)) {
    for (const name of match[1].matchAll(/"([a-z_]+)"/g)) declared.add(name[1]);
  }
  const labelBlock = pageSource.slice(pageSource.indexOf('const labels = {'),
    pageSource.indexOf('const helps = {'));
  const labelled = new Set([...labelBlock.matchAll(/([a-z_]+):/g)].map(m => m[1]));
  const registered = Object.keys(DEFAULTS);
  assert.deepEqual(registered.filter(name => !declared.has(name)), [],
    'every registered field must be declared in a section');
  assert.deepEqual(registered.filter(name => !labelled.has(name)), [],
    'every registered field needs a label');
  /* 反向：页面声明的名字都必须真的注册过（防拼错）。 */
  assert.deepEqual([...declared].filter(name => !registered.includes(name)), [],
    'a declared name that is not registered renders with a raw id');
});

test('the settings area reads the flat memory payload without a query', async () => {
  const harnessed = harness();
  await openSettings(harnessed);
  const read = harnessed.requests.find(r => r.route === SETTINGS_ROUTE);
  assert.ok(read, 'opening the settings area reads the memory settings route');
  assert.equal(read.method, 'GET');
  /* 该路由显式拒绝 query（memory_api.py 的 `if request.query:`），
     带一个 ?sections= 就会整个读失败。 */
  assert.equal(read.body, null, 'the memory settings route must be read without a query');
  read.resolve(settingsStatus());
  await drains();
  const live = harnessed.page.state.instance;
  assert.ok(live, 'the workbench instance is reachable for tests');
  assert.equal(live.state.base.enabled, true);
  /* 工作台把读取侧的 values 归一成 settings，后续只认这一个名字。 */
  assert.equal(live.state.status.settings.context_items, 4);
  assert.equal(live.state.status.edit_token, TOKEN);
  /* 四段分组都要出现，字段才不会被挤进末尾的兜底组。
     用 plain() 转成宿主 realm 的数组：vm 里的 Array.prototype 与测试进程不同，
     直接 deepStrictEqual 会因为「结构相同但不是同一个引用」而失败。 */
  assert.deepEqual(plain(live.state.container.groups.map(g => g.id)),
    ['core', 'reflection', 'episode', 'habit']);
  assert.match(harnessed.workbenchHtml, /role="tablist"/);
  assert.match(harnessed.workbenchHtml, /data-wb-section="core"/);
});

test('section navigation switches modules without dropping drafts', async () => {
  const harnessed = harness();
  await openSettings(harnessed);
  harnessed.requests.find(r => r.route === SETTINGS_ROUTE).resolve(settingsStatus());
  await drains();
  const live = harnessed.page.state.instance;
  live.state.draft.enabled = false;

  harnessed.wbSectionClick('habit');
  assert.match(harnessed.workbenchHtml, /aria-selected="true"[^>]*data-wb-section="habit"/);
  assert.match(harnessed.workbenchHtml, /data-wb-section-count="core">1 项已改/);
  assert.equal(live.state.draft.enabled, false);

  harnessed.wbSectionKeydown('habit', 'ArrowDown');
  assert.match(harnessed.workbenchHtml, /aria-selected="true"[^>]*data-wb-section="core"/);
  assert.equal(live.state.draft.enabled, false);
});

test('a save qualifies short names and sends expected_edit_token', async () => {
  const harnessed = harness();
  await openSettings(harnessed);
  harnessed.requests.find(r => r.route === SETTINGS_ROUTE).resolve(settingsStatus());
  await drains();
  const live = harnessed.page.state.instance;
  /* 默认 enabled 是 true，所以要改成 false 才算一次真实变更。 */
  live.state.draft.enabled = false;
  live.state.draft.context_items = 6;
  const saving = live.save(false);
  await drains();
  const write = harnessed.requests.find(r => r.route === SETTINGS_ROUTE + '/command');
  assert.ok(write, 'the save goes to /settings/command');
  assert.deepEqual(plain(write.body), {
    operation_key: 'memory-key',
    /* 读 edit_token、写 expected_edit_token：一个名字满足不了两侧。 */
    expected_edit_token: TOKEN,
    /* 短名进草稿，长名上网线——后端按 field_id 查表。 */
    changes: {'memory.enabled': false, 'memory.context_items': 6},
    /* 声明了 resetKey 的分区总会带上这个字段（记忆路由允许它为空的数组）。 */
    reset_to_inherit: [],
  });
  /* 保存确认扁平、不带 status；token 已轮换，内核要自己补一次读取。 */
  write.resolve({operation_id: 'op', replayed: false, revision: 2, changed_fields: ['memory.enabled']});
  await saving;
  await drains();
  assert.equal(live.state.pending, null, 'a confirmed save clears the pending request');
  assert.equal(live.state.savedState, ' is-saved');
  const reread = harnessed.requests.filter(r => r.route === SETTINGS_ROUTE);
  assert.equal(reread.length, 2, 'the rotated edit token forces one re-read');
});

test('reset sends qualified names in reset_to_inherit', async () => {
  const harnessed = harness();
  await openSettings(harnessed);
  /* 这一条要「字段被显式标成恢复继承」，所以先让后端把它标成继承态
     （provenance.explicit 为 false），「恢复继承」按钮才会出现。 */
  const inherited = settingsStatus();
  inherited.provenance.enabled = {layer: 'installation', explicit: false, revision: 1};
  harnessed.requests.find(r => r.route === SETTINGS_ROUTE).resolve(inherited);
  await drains();
  const live = harnessed.page.state.instance;
  /* 走真实点击：内核的 onClick 认 [data-wb-reset]。 */
  harnessed.wbClick(resetButton('enabled'));
  await drains();
  const saving = live.save(false);
  await drains();
  const write = harnessed.requests.find(r => r.route === SETTINGS_ROUTE + '/command');
  assert.ok(write, 'the reset dispatches');
  /* 恢复继承要显式告诉后端「清掉本层的值」，而不是写一份与继承值相同的副本；
     名字同样要补成长名。 */
  assert.deepEqual(write.body.reset_to_inherit, ['memory.enabled']);
  write.resolve({operation_id: 'op', replayed: false, revision: 3, changed_fields: []});
  await saving;
  await drains();
});

test('a host without the memory route refuses instead of showing an empty editor', async () => {
  /* 角色没有 memory_api（如群聊目标）：不发请求，直接说明去哪儿配。 */
  const wrong = harness({memoryApi: '/somewhere-else/memory'});
  wrong.mount();
  await drains();
  assert.equal(wrong.requests.length, 0, 'an unusable route must not be called');
  assert.match(wrong.html, /没有可用的整理设置/);

  /* 角色根本没有这个字段。 */
  const absent = harness({memoryApi: null});
  absent.mount();
  await drains();
  assert.equal(absent.requests.length, 0);
  assert.match(absent.html, /没有可用的整理设置/);

  /* 非托管环境（静态预览）：不假装能读，也不发请求。 */
  const offline = harness({hosted: false});
  offline.mount();
  await drains();
  assert.equal(offline.requests.length, 0, 'preview must not call the bridge');
  assert.match(offline.html, /请从已登录的 AstrBot 管理界面/);
});
