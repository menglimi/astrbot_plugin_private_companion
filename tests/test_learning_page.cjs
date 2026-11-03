/* 学习与反思页（培育轨道）测试。
 *
 * 这一页有三条查出来的契约，每一条都值一条断言：
 *   ① 动作由后端给。`RoleGrowthManagement._project()` 按状态与来源可读性算出 actions；
 *      页面自己按状态推断会给出一个后端一定拒绝的按钮。
 *   ② 来源不可读时后端把 title/description/progress_note 一律置 null——
 *      页面必须说"读不到"，不能显示成一张空卡。
 *   ③ 全局策略与个人授权分开；本页只管理记录，不再保存某个私聊对象的权限。
 *
 * 另外两条是别的页也踩过的：只提交动过的字段、状态动作必须先过 actions 门。
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/learning-page.js'), 'utf8');
const HOST = 'astrbot_plugin_private_companion';
const GROWTH = '/' + HOST + '/growth';
const flush = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 8; i++) await flush(); };

/* 一条成长记录，形状照 `_project()` 的返回。
   actions 是后端算出来的，夹具要能逐条改，测试才有意义。 */
function record(id, state, kind = 'skill', extra = {}) {
  return {
    growth_id: id, revision: 2, kind, state,
    title: id + ' 的标题', description: id + ' 的描述', progress_note: '最近的进展',
    source_count: 2, unavailable_reason: null,
    proposed_by: 'user-1', confirmed_by: state === 'proposed' ? null : 'user-1',
    changed_by: 'user-1',
    created_at: '2026-09-01T02:00:00+00:00', updated_at: '2026-09-20T03:00:00+00:00',
    actions: [], view_token: 'v'.repeat(64),
    ...extra,
  };
}
/* 后端按状态给的默认动作集（照 _project 写）。 */
const ACTIONS_FOR = {
  proposed: ['confirm', 'revise', 'withdraw', 'retire'],
  active: ['revise', 'pause', 'complete', 'retire'],
  paused: ['resume', 'revise', 'complete', 'retire'],
  achieved: ['retire'],
  retired: ['retire'],
};
function withActions(rows) {
  return rows.map(row => ({...row, actions: row.actions.length ? row.actions : ACTIONS_FOR[row.state] || []}));
}
function review(id, extra = {}) {
  return {
    review_id: id, growth_id: 'g-1', base_revision: 2, growth_title: 'g-1 的标题',
    progress_note: '今天练了一小时', receipt_stream: 'receipts', created_at: '2026-09-25T01:00:00+00:00',
    unavailable_reason: null, actions: ['accept', 'reject'], view_token: 'w'.repeat(64),
    ...extra,
  };
}
function access(extra = {}) {
  return {
    settings: {read_enabled: true, proposals_enabled: true, ...(extra.settings || {})},
    effective: {read_enabled: {allowed: true, reason: null},
                proposals_enabled: {allowed: true, reason: null}, ...(extra.effective || {})},
    edit_token: 'a'.repeat(64),
  };
}

function harness() {
  const requests = [];
  const window = {
    CompanionProduct: {
      hosted: () => true,
      role: () => ({role_id: 'role-1', growth_api: GROWTH}),
      bridgeNow: () => window.AstrBotPluginPage,
      bridgeFor: () => ({then: resolve => resolve(window.AstrBotPluginPage)}),
    },
    AstrBotPluginPage: {
      ready: () => Promise.resolve({pluginName: HOST}),
      apiGet: (route, params) => new Promise((resolve, reject) => requests.push({method: 'GET', route, params, resolve, reject})),
      apiPost: (route, values) => new Promise((resolve, reject) => requests.push({method: 'POST', route, values, resolve, reject})),
    },
    document: {
      addEventListener: () => {}, querySelector: () => null, querySelectorAll: () => [],
      body: {classList: {add: () => {}, remove: () => {}, toggle: () => {}}}, title: '',
    },
    matchMedia: () => ({matches: false}),
    console, Intl, Date, JSON, Math, Number, Object, Array, String, Set, Map, Boolean, Error, Promise,
    localStorage: {getItem: () => null, setItem: () => {}, removeItem: () => {}},
  };
  vm.runInNewContext(source, {
    window, document: window.document, console, Intl, Date, JSON, Math, Number, Object, Array,
    String, Set, Map, Boolean, Error, Promise, localStorage: window.localStorage, URL,
    /* 计时器必须给：bounded() 用 Promise.race 包了 15 秒超时，
       少了 setTimeout 第一个请求在发出前就抛「clearTimeout is not defined」，
       整页停在初始状态——看起来像请求逻辑有问题，其实是夹具不全。 */
    setTimeout, clearTimeout, setInterval, clearInterval,
    /* 修订表单用 FormData 取值；桩只实现 get。 */
    FormData: class { constructor() {} get() { return ""; } },
  });

  function root() {
    const handlers = {};
    const node = {
      isConnected: true, innerHTML: '', __handlers: handlers,
      addEventListener(type, handler) { handlers[type] = handler; },
      removeEventListener() {},
      contains() { return true; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      dispatch(type, event) { return handlers[type]?.(event); },
    };
    return node;
  }
  const page = window.CompanionLearningPage;
  const element = root();
  return {page, element, requests, window,
    reply: (request, payload) => request.resolve({status: 'ok', data: payload}),
    fail: (request, code) => request.resolve({status: 'error', message: code, data: {code}}),
    settle};
}

/* 列表与审阅都从挂载时并行发起，所以夹具要循环答「谁在飞就答谁」。 */
async function boot(h, rows = [], reviews = [], extra = {}, selection = {}) {
  const mounted = h.page.mount(h.element, {growthApi:selection.growthApi || GROWTH,
    scopeKey:selection.scopeKey || 'selected-growth-scope', layout:selection.layout,
    roleLabel:selection.roleLabel || '选定角色'});
  await h.settle();
  const answered = new Set();
  for (let round = 0; round < 20; round++) {
    const pending = h.requests.filter(r => !answered.has(r));
    if (!pending.length) break;
    for (const request of pending) {
      answered.add(request);
      if (request.route.endsWith('/evidence')) {
        h.reply(request, {items:[{text:'本轮可核验的原始依据',issued_at:'2026-10-07T08:00:00+00:00'}]});
        continue;
      }
      if (request.route.endsWith('/progress')) {
        const sourceReviews=extra.reviewsByRoute?.[request.route.slice(0,-'/progress'.length)]||reviews;
        h.reply(request, {items: sourceReviews, next_cursor: extra.reviewNext || null,
          context_key: 'ctx', role_id: 'role-1'});
        continue;
      }
      if (request.route === 'growth' || request.route.endsWith('/growth')) {
        const sourceRows=extra.rowsByRoute?.[request.route]||rows;
        h.reply(request, {items: withActions(sourceRows),
          next_cursor: extra.next || null,
          context_key: 'ctx', role_id: 'role-1',
          profile_id: 'profile-1', operator_name: 'operator',
          access: extra.access === null ? undefined : (extra.access || access())});
        continue;
      }
      h.reply(request, {});
    }
    await h.settle();
  }
  await mounted;
  return {requests: h.requests};
}

/* 桩元素：closest 按「自己命中哪些选择器」回答。 */
function node(attrs) {
  const self = {
    dataset: attrs, type: '', checked: attrs.checked === true, value: '',
    closest(sel) {
      const probes = {view: '[data-view]', action: '[data-action]', act: '[data-act]',
                      id: '[data-id]', open: '[data-open]', review: '[data-review]',
                      confirm: '[data-confirm]', filterState: '[data-filter-state]',
                      filterKind: '[data-filter-kind]', access: '[data-access]'};
      for (const [key, pattern] of Object.entries(probes)) {
        if (attrs[key] !== undefined && sel === pattern) return self;
      }
      return null;
    },
  };
  return self;
}
const click = (h, attrs) => h.element.dispatch('click',
  {target: node(attrs), preventDefault() {}, stopPropagation() {}});
const change = (h, attrs) => h.element.dispatch('change', {target: node(attrs), preventDefault() {}});

/* ---- 接线 ------------------------------------------------------------- */

test('挂载后同时读列表与待审阅，路径是插件相对路径', async () => {
  const h = harness();
  await boot(h, [record('g-1', 'active')], [review('r-1')]);
  const routes = h.requests.map(r => r.method + ' ' + r.route.split('?')[0]).sort();
  assert.equal(routes.join('|'), 'GET growth|GET growth/progress');
  for (const request of h.requests) {
    assert.ok(!request.route.startsWith('/' + HOST + '/'),
      '桥只接受裸路由名，带上插件目录名会整族 404：' + request.route);
  }
  assert.equal(h.page.state.data.ready, true);
  assert.equal(h.page.state.data.items.length, 1);
  assert.equal(h.page.state.data.reviews.length, 1);
  assert.match(h.element.innerHTML, /记录归属 · 选定角色/);
  assert.doesNotMatch(h.element.innerHTML, /跨私聊汇总|私聊记录范围/);
});

test('成长链路读取详情凭证并使用工作台布局', async () => {
  const h = harness();
  await boot(h, [record('g-1','active')], [], {}, {layout:'chain',roleLabel:'角色 A'});
  assert.ok(h.requests.some(request => request.method === 'GET' && request.route === 'growth/evidence'
    && request.params.growth_id === 'g-1'));
  assert.match(h.element.innerHTML, /lea-chain-grid/);
  assert.match(h.element.innerHTML, /依据与凭证/);
  assert.match(h.element.innerHTML, /本轮可核验的原始依据/);
  assert.doesNotMatch(h.element.innerHTML, /权限开关/);
});

test('没有角色路由时不发请求，并说明原因', async () => {
  const h = harness();
  await h.page.mount(h.element);
  await h.settle();
  assert.equal(h.requests.length, 0);
  assert.equal(h.page.state.data.ready, false);
  assert.match(h.element.innerHTML, /还没有可读取的成长记录/);
});

test('成长列表使用学习工作台选定的角色路由，不读取当前聊天角色', async () => {
  const h = harness();
  h.window.CompanionProduct.role = () => ({role_id:'current-chat', growth_api:'/' + HOST + '/current-chat/growth'});
  await boot(h, [record('g-1', 'active')], [], {},
    {growthApi:'/' + HOST + '/targets/profile-2/roles/role-2/growth', scopeKey:'profile-2:role-2'});
  assert.ok(h.requests.some(request => request.route === 'targets/profile-2/roles/role-2/growth'));
  assert.ok(!h.requests.some(request => request.route.includes('current-chat')));
});

test('成长记录页显示当前私聊对象的两项授权，并与记录请求共用响应', async () => {
  const h = harness();
  await boot(h, [record('g-1', 'active')]);
  const gets = h.requests.filter(r => r.method === 'GET');
  assert.equal(gets.length, 2, '读取只有列表与审阅两次：' + gets.map(r => r.route).join(','));
  assert.match(h.element.innerHTML, /私聊对象授权/);
  assert.match(h.element.innerHTML, /data-access="read_enabled"/);
  assert.match(h.element.innerHTML, /data-access="proposals_enabled"/);
  assert.match(h.element.innerHTML, /选定角色/);
});

test('授权开关携带当前编辑令牌且只写入所选对象', async () => {
  const h = harness();
  await boot(h, [record('g-1', 'active')]);
  change(h, {access:'read_enabled', source:'growth', checked:false});
  await h.settle();
  const request = h.requests.find(r => r.method === 'POST' && r.route === 'growth/command');
  assert.ok(request, '授权变更走成长专用命令');
  assert.equal(request.values.action, 'configure');
  assert.equal(request.values.edit_token, 'a'.repeat(64));
  assert.deepEqual({...request.values.changes}, {read_enabled:false});
  assert.match(request.values.operation_key, /configure-read_enabled:/);
  assert.ok(request.values.operation_key.length <= 128);
  h.reply(request, {access:access({settings:{read_enabled:false}})});
  await h.settle();
  assert.equal(h.page.state.data.accesses.growth.settings.read_enabled, false);
  assert.match(h.element.innerHTML, /已更新「选定角色」的成长权限/);
});

/* ---- 动作门：这一页最重要的一条 ---------------------------------------
   后端按状态算出 actions。页面自己按状态推断，就会给出一个后端一定拒绝的按钮，
   而用户点了才知道不行——那是最差的一种失败。 */

test('卡片上只出现后端给的动作，页面不自己推断', async () => {
  const h = harness();
  /* proposed 的记录后端给 confirm/revise/withdraw/retire。
     卡片上只显示前两个（详情里给全部），但**绝不能**出现 pause——那不是这个状态的动作。 */
  await boot(h, [record('g-1', 'proposed')]);
  const html = h.element.innerHTML;
  const shown = [...html.matchAll(/data-act="([a-z]+)"/g)].map(m => m[1]);
  assert.ok(shown.length >= 2, '要有动作按钮');
  assert.ok(!shown.includes('pause'), 'proposed 状态不该出现「暂停」');
  assert.ok(!shown.includes('resume'), 'proposed 状态不该出现「继续」');
  for (const action of shown) {
    assert.ok(ACTIONS_FOR.proposed.includes(action),
      '出现了后端没给的动作：' + action);
  }
});

test('后端收回动作后，点旧按钮不发请求而是刷新', async () => {
  const h = harness();
  await boot(h, [record('g-1', 'proposed')]);
  /* 模拟"后端不再给 confirm"：直接把记录的动作改掉，再点。 */
  h.page.state.data.items[0].actions = ['retire'];
  const before = h.requests.length;
  click(h, {act: 'confirm', id: 'g-1'});
  await h.settle();
  const posted = h.requests.slice(before).filter(r => r.method === 'POST');
  assert.equal(posted.length, 0, '后端没给的动作绝不能发出去');
  assert.equal(h.page.state.data.error, true);
  assert.ok(h.page.state.data.notice.includes('不接受'), '要说明为什么');
});

test('状态动作的载荷是后端要的那五个字段', async () => {
  const h = harness();
  await boot(h, [record('g-1', 'active')]);
  click(h, {act: 'pause', id: 'g-1'});
  await h.settle();
  const post = h.requests.find(r => r.method === 'POST');
  assert.ok(post, '要有一次 POST');
  assert.equal(post.route, 'growth/command');
  /* 跨 realm 陷阱：vm 里创建的对象原型与本 realm 不同，
     deepEqual 会把内容相同的两个对象判成不等。转成字符串比。 */
  assert.equal(Object.keys(post.values).sort().join(","),
    'action,expected_revision,growth_id,operation_key,view_token');
  assert.equal(post.values.action, 'pause');
  assert.equal(post.values.expected_revision, 2, '必须带上读到的版本');
  assert.equal(post.values.view_token, 'v'.repeat(64));
});

test('修订额外带标题与正文，且空标题不发', async () => {
  const h = harness();
  await boot(h, [record('g-1', 'active')]);
  click(h, {act: 'revise', id: 'g-1'});
  assert.equal(h.page.state.data.detailId, 'g-1', '修订要先打开详情');
  const before = h.requests.length;
  /* 空标题应当被挡下。 */
  h.element.dispatch('submit', {target: {closest: sel => sel === '[data-form="revise"]' ? {} : null},
    preventDefault() {}});
  await h.settle();
  assert.equal(h.requests.slice(before).filter(r => r.method === 'POST').length, 0);
  assert.ok(h.page.state.data.notice.includes('标题不能为空'));
});

/* ---- 找不到来源：后端把内容抹掉，页面要说出来 -------------------------- */

test('来源不可读取时说明读不到，而不是显示空卡', async () => {
  const h = harness();
  const blind = record('g-9', 'active', 'goal', {
    title: null, description: null, progress_note: null,
    unavailable_reason: 'growth_source_expired', source_count: 3,
    actions: ['retire'],
  });
  await boot(h, [blind]);
  const html = h.element.innerHTML;
  assert.ok(html.includes('来源已不可读取'), '卡片要说清读不到');
  assert.ok(!html.includes('未命名记录'), '不能把 null 标题显示成「未命名」——那听起来像还没起名');
  assert.ok(html.includes('原来源已过期'), '要翻译 unavailable_reason');
  assert.ok(html.includes('is-blind'), '要有专门的视觉标记');
  /* 详情里也要说清楚，并且要说明为什么只能归档。 */
  click(h, {id: 'g-9'});
  assert.match(h.element.innerHTML, /来源不可读；仅可归档/);
  click(h, {action:'close-detail'});
  click(h, {act:'retire',id:'g-9'});
  assert.match(h.element.innerHTML, /来源已不可读取的记录/);
  assert.doesNotMatch(h.element.innerHTML, /lea-confirm-target[^>]*>[^<]*g-9/);
});

/* ---- 审阅队列 --------------------------------------------------------- */

test('依据读不出来时只剩不采纳，并解释为什么', async () => {
  const h = harness();
  await boot(h, [record('g-1', 'active')],
    [review('r-1', {progress_note: null, unavailable_reason: 'source_expired', actions: ['reject']})]);
  click(h, {view: 'reviews'});
  const html = h.element.innerHTML;
  assert.ok(html.includes('不采纳'), '要给出可用的那个动作');
  assert.ok(!/data-review="accept"/.test(html), '依据不可读时后端不给采纳，页面也不能给');
  assert.match(html, /依据不可读取，无法采纳/);
});

test('审阅裁决的载荷只有那五个字段', async () => {
  const h = harness();
  await boot(h, [record('g-1', 'active')], [review('r-1')]);
  click(h, {view: 'reviews'});
  click(h, {review: 'accept', id: 'r-1'});
  await h.settle();
  const post = h.requests.find(r => r.method === 'POST');
  assert.equal(post.route, 'growth/progress/command');
  /* growth_api 对 progress_command 做的是 set(data) != fields 的严格相等校验，
     多一个字段就整条拒绝。 */
  assert.equal(Object.keys(post.values).sort().join(","),
    'action,expected_revision,operation_key,review_id,view_token');
  assert.equal(post.values.review_id, 'r-1');
  assert.equal(post.values.action, 'accept');
});

/* ---- 状态与轨道 ------------------------------------------------------- */

test('五段都在轨道上，空段也留着', async () => {
  const h = harness();
  await boot(h, [record('g-1', 'active'), record('g-2', 'achieved', 'goal')]);
  const html = h.element.innerHTML;
  for (const key of ['proposed', 'active', 'paused', 'achieved']) {
    assert.ok(html.includes('data-lane="' + key + '"'), '主线缺了这一段：' + key);
  }
  /* 归档段在没有任何归档记录时不渲染——它是旁支，不是主线的一段。 */
  assert.ok(!html.includes('data-lane="retired"'), '没有归档记录时不该画归档段');
  const retired = record('g-3', 'retired');
  await boot(h, [record('g-1', 'active'), retired]);
  assert.ok(h.element.innerHTML.includes('data-lane="retired"'), '有归档记录时要画出来');
});

test('不认识的状态单独列出，不混进已知段', async () => {
  const h = harness();
  /* 把一条记录的状态改成这一版页面不认识的取值。 */
  await boot(h, [record('g-1', 'active', 'skill', {state: 'archived_by_newer_version'})]);
  const html = h.element.innerHTML;
  assert.ok(html.includes('状态未识别'), '新状态要单独列出来');
  assert.doesNotMatch(html, /archived_by_newer_version/, '未知状态以可读说明呈现');
  /* 关键：不能把它塞进任何已知状态段——塞进 achieved 会误报「已结束」，
     塞进 active 会误报「还在推进」。
     逐段切开比，而且只看**卡片**部分：未知段自己的提示文案里就写着这个状态名，
     用整段文本判会把那条解释误认成"塞进去了"。 */
  const lanes = html.split('data-lane="').slice(1);
  assert.equal(lanes.length, 4, '该只有四条主线');
  for (const lane of lanes) {
    const key = lane.slice(0, lane.indexOf('"'));
    const cards = lane.split('data-id="').slice(1);
    for (const card of cards) {
      assert.ok(!card.includes('archived_by_newer_version'),
        '未知状态的记录被塞进了 ' + key + ' 段的卡片里——那会误报进度');
    }
    assert.ok(!lane.includes('aria-label="归档"'), '未知状态不该被当成归档渲染');
  }
});

test('筛选走服务端参数，不本地过滤', async () => {
  const h = harness();
  await boot(h, [record('g-1', 'active')]);
  const before = h.requests.length;
  click(h, {filterState: 'proposed'});
  await h.settle();
  const request = h.requests.slice(before).find(r => r.method === 'GET');
  assert.ok(request, '筛选要重新读一次');
  assert.equal(request.route, 'growth');
  assert.equal(request.params.state, 'proposed', '筛选条件应通过页面桥参数传递');
});

/* ---- 不可逆动作要确认 ------------------------------------------------- */

test('归档与撤回要先确认，取消则什么都不发', async () => {
  const h = harness();
  await boot(h, [record('g-1', 'proposed')]);
  click(h, {act: 'retire', id: 'g-1'});
  assert.ok(h.element.innerHTML.includes('lea-confirm'), '不可逆动作要出确认条');
  const before = h.requests.length;
  click(h, {confirm: 'no'});
  await h.settle();
  assert.equal(h.requests.slice(before).filter(r => r.method === 'POST').length, 0, '取消就什么都不发');
  assert.ok(!h.element.innerHTML.includes('lea-confirm'), '取消后确认条要收起来');
});

test('确认条的两条路径都在同一个 click 处理器里判定', async () => {
  const h = harness();
  await boot(h, [record('g-1', 'proposed')]);
  click(h, {act: 'withdraw', id: 'g-1'});
  click(h, {confirm: 'yes'});
  await h.settle();
  const post = h.requests.find(r => r.method === 'POST');
  assert.equal(post.values.action, 'withdraw');
});

/* ---- 渲染完整性 ------------------------------------------------------- */

test('三个记录视图都能渲染出内容', async () => {
  const h = harness();
  await boot(h, [record('g-1', 'active'), record('g-2', 'proposed')], [review('r-1')]);
  const markers = {track: 'lea-lanes', reviews: 'lea-reviews', sources: 'lea-sources'};
  for (const [view, marker] of Object.entries(markers)) {
    click(h, {view});
    assert.ok(h.element.innerHTML.includes(marker), view + ' 视图渲染为空');
  }
});

test('卸载后清干净，重复挂载不叠加', async () => {
  const h = harness();
  await boot(h, [record('g-1', 'active')]);
  h.page.unmount();
  assert.equal(h.page.state.data, null);
  await boot(h, [record('g-1', 'active')]);
  assert.equal(h.page.state.data.ready, true);
});

/* ---- 分页：写操作与刷新不能把已经翻出来的页丢掉 ------------------------
 *
 * 这一页的分页状态是 `listCursors` / `reviewCursors`（每个来源一条 after_id）
 * 加上已读页数。历史实现里 `load()` 一律先把游标清空再读第一页，
 * 所以「暂停/确认/修订/采纳」之后用户刚翻出来的第二页就不见了——
 * 重新点「读取下一页」也不一定能再拿到同样的位置。
 *
 * 下面几条用例把「写操作与刷新只重读已加载的页」和「已显示的记录不能减少」
 * 固化下来。夹具按游标串页：每个用例自己给出第 1..N 页。
 */

async function drive(h, answer) {
  const answered = new Set();
  for (let round = 0; round < 80; round++) {
    const pending = h.requests.filter(request => !answered.has(request));
    if (!pending.length) {
      await h.settle();
      if (!h.requests.some(request => !answered.has(request))) return;
      continue;
    }
    for (const request of pending) {
      answered.add(request);
      const payload = answer(request);
      if (payload && payload.__fail) h.fail(request, payload.code);
      else if (payload && payload.__error) h.fail(request, payload.__error);
      else h.reply(request, payload ?? {});
    }
    await h.settle();
  }
  throw new Error('页面请求没有安静下来');
}
function pickPage(pages, after) {
  const index = after
    ? pages.findIndex((entry, position) => position > 0 && pages[position - 1].next === after)
    : 0;
  return pages[index < 0 ? pages.length - 1 : index];
}
/* pages: {list:[{items,next}], reviews:[{items,next}]}，每页按上一页的 next 串起来。 */
function pagedAnswer(pages) {
  return request => {
    if (request.route.endsWith('/evidence')) {
      return {items: [{text: '本轮可核验的原始依据', issued_at: '2026-10-07T08:00:00+00:00'}]};
    }
    const after = request.params?.after_id || '';
    if (request.route.endsWith('/progress')) {
      const entry = pickPage(pages.reviews, after);
      return {items: entry.items, next_cursor: entry.next, context_key: 'ctx', role_id: 'role-1'};
    }
    if (request.route === 'growth' || request.route.endsWith('/growth')) {
      const entry = pickPage(pages.list, after);
      return {items: withActions(entry.items), next_cursor: entry.next, context_key: 'ctx',
        role_id: 'role-1', profile_id: 'profile-1', operator_name: 'operator', access: access()};
    }
    return {};
  };
}
async function mountPaged(h, pages, options = {}) {
  const mounted = h.page.mount(h.element, {growthApi: GROWTH, scopeKey: 'selected-growth-scope',
    roleLabel: '选定角色', ...options});
  await drive(h, pagedAnswer(pages));
  await mounted;
  return h;
}
const listReads = (h, after = 0) => h.requests.slice(after)
  .filter(request => request.method === 'GET' && (request.route === 'growth' || request.route.endsWith('/growth')));
const reviewReads = (h, after = 0) => h.requests.slice(after)
  .filter(request => request.method === 'GET' && request.route.endsWith('/progress'));
const cursorsOf = requests => requests.map(request => request.params?.after_id || '(first)');

test('动作完成后：已加载的第二页与游标都保留，刷新重读已加载的页', async () => {
  const h = harness();
  const pages = {
    list: [
      {items: [record('g-1', 'active'), record('g-2', 'active')], next: 'c1'},
      {items: [record('g-3', 'active'), record('g-4', 'active')], next: 'c2'},
    ],
    reviews: [{items: [], next: null}],
  };
  await mountPaged(h, pages);
  assert.equal(h.page.state.data.items.length, 2, '第一页两条');
  click(h, {action: 'more'});
  await drive(h, pagedAnswer(pages));
  assert.equal(h.page.state.data.items.length, 4, '两页四条');
  const sourceKey = Object.keys(h.page.state.data.listCursors)[0];
  assert.equal(h.page.state.data.listCursors[sourceKey], 'c2', '第二页读完后的游标');
  const before = h.requests.length;
  /* 后端在暂停之后返回的是 paused 状态。 */
  pages.list[0].items = [record('g-1', 'paused'), record('g-2', 'active')];
  click(h, {act: 'pause', id: 'g-1'});
  await drive(h, request => request.method === 'POST' ? {revision: 3} : pagedAnswer(pages)(request));
  assert.deepEqual(cursorsOf(listReads(h, before)), ['(first)', 'c1'],
    '动作后的刷新应重读已加载的两页，而不是只剩第一页');
  assert.equal(h.page.state.data.items.length, 4, '动作不该让已经显示的记录消失');
  assert.equal(h.page.state.data.listCursors[sourceKey], 'c2', '游标不能被清空');
});

test('裁决一条审阅后：其余审阅记录与游标保留', async () => {
  const h = harness();
  const pages = {
    list: [{items: [record('g-1', 'active')], next: null}],
    reviews: [
      {items: [review('r-1'), review('r-2')], next: 'p1'},
      {items: [review('r-3')], next: 'p2'},
    ],
  };
  await mountPaged(h, pages);
  click(h, {view: 'reviews'});
  await drive(h, pagedAnswer(pages));
  click(h, {action: 'more-reviews'});
  await drive(h, pagedAnswer(pages));
  assert.equal(h.page.state.data.reviews.length, 3, '两页三条待审阅');
  const reviewKey = Object.keys(h.page.state.data.reviewCursors)[0];
  assert.equal(h.page.state.data.reviewCursors[reviewKey], 'p2', '第二页读完后的审阅游标');
  pages.reviews[0].items = [review('r-2')];
  click(h, {review: 'accept', id: 'r-1'});
  await drive(h, request => request.method === 'POST' ? {} : pagedAnswer(pages)(request));
  assert.equal(h.page.state.data.reviews.length, 2, '裁决掉一条，其余保留');
  assert.equal(h.page.state.data.reviewCursors[reviewKey], 'p2', '审阅游标不能被清空');
});

test('重新读取：列表页与审阅页都按已加载的页数重读，一条都不少', async () => {
  const h = harness();
  const pages = {
    list: [
      {items: [record('g-1', 'active'), record('g-2', 'active')], next: 'c1'},
      {items: [record('g-3', 'active'), record('g-4', 'active')], next: 'c2'},
    ],
    reviews: [
      {items: [review('r-1')], next: 'p1'},
      {items: [review('r-2')], next: 'p2'},
    ],
  };
  await mountPaged(h, pages);
  click(h, {action: 'more'});
  await drive(h, pagedAnswer(pages));
  click(h, {view: 'reviews'});
  await drive(h, pagedAnswer(pages));
  click(h, {action: 'more-reviews'});
  await drive(h, pagedAnswer(pages));
  assert.equal(h.page.state.data.items.length, 4);
  assert.equal(h.page.state.data.reviews.length, 2);
  const before = h.requests.length;
  click(h, {action: 'refresh'});
  await drive(h, pagedAnswer(pages));
  assert.deepEqual(cursorsOf(listReads(h, before)), ['(first)', 'c1'],
    '列表：已加载的两页都要重读');
  assert.deepEqual(cursorsOf(reviewReads(h, before)), ['(first)', 'p1'],
    '审阅：已加载的两页都要重读');
  assert.equal(h.page.state.data.items.length, 4, '刷新后记录数不能减少');
  assert.equal(h.page.state.data.reviews.length, 2, '刷新后审阅数不能减少');
});

test('加载更多：重叠的一页不会出现两份记录', async () => {
  const h = harness();
  const pages = {
    list: [
      {items: [record('g-1', 'active'), record('g-2', 'active')], next: 'c1'},
      {items: [record('g-2', 'active'), record('g-3', 'active')], next: null},
    ],
    reviews: [{items: [], next: null}],
  };
  await mountPaged(h, pages);
  click(h, {action: 'more'});
  await drive(h, pagedAnswer(pages));
  assert.equal(h.page.state.data.items.length, 3, '重叠的那条只算一次');
});

test('刷新失败或某一页变短：已显示的记录保留，并提示没有读到最新内容', async () => {
  const h = harness();
  const pages = {
    list: [
      {items: [record('g-1', 'active'), record('g-2', 'active')], next: 'c1'},
      {items: [record('g-3', 'active'), record('g-4', 'active')], next: 'c2'},
    ],
    reviews: [{items: [], next: null}],
  };
  await mountPaged(h, pages);
  click(h, {action: 'more'});
  await drive(h, pagedAnswer(pages));
  assert.equal(h.page.state.data.items.length, 4, '现场两页四条');

  /* 刷新时列表请求全部失败：保留旧记录，只说清这次没读到。 */
  click(h, {action: 'refresh'});
  await drive(h, request => request.route === 'growth'
    ? {__fail: true} : pagedAnswer(pages)(request));
  assert.equal(h.page.state.data.items.length, 4, '刷新失败不能把已经显示的记录抹掉');
  assert.match(h.element.innerHTML, /已保留当前内容/, '要说清这次没读到，并说明内容已保留');

  /* 再刷新一次，第二页这次只剩一条（比上次短）：旧的那条仍要留着。 */
  const shrunk = {
    list: [
      {items: [record('g-1', 'active'), record('g-2', 'active')], next: 'c1'},
      {items: [record('g-3', 'active')], next: null},
    ],
    reviews: pages.reviews,
  };
  click(h, {action: 'refresh'});
  await drive(h, pagedAnswer(shrunk));
  assert.equal(h.page.state.data.items.length, 4, '页变短时已显示的记录保留');
  assert.ok(h.page.state.data.items.some(row => row.growth_id === 'g-4'), '上一次读到的第四条还在');
});

/* ---- 确认条：链式版式与详情视图也必须渲染 ---------------------------------
 *
 * 回归：确认条此前只在轨道视图（trackView）里渲染。而生产唯一版式是 chain，
 * 详情视图也不渲染它——点「归档 / 撤回」只把 data.confirm 置上再原样重绘：
 * DOM 不变、0 请求、notice 空，动作永远发不出去。
 */

test('链式版式：归档先出确认条，确认后只发一次请求并原地刷新', async () => {
  const h = harness();
  const pages = {
    list: [
      {items: [record('g-1', 'active'), record('g-2', 'active')], next: 'c1'},
      {items: [record('g-3', 'active')], next: null},
    ],
    reviews: [{items: [], next: null}],
  };
  await mountPaged(h, pages, {layout: 'chain'});
  click(h, {action: 'more'});
  await drive(h, pagedAnswer(pages));
  assert.equal(h.page.state.data.items.length, 3, '现场两页三条');
  const before = h.requests.length;
  click(h, {act: 'retire', id: 'g-1'});
  assert.match(h.element.innerHTML, /lea-confirm/, '链式版式点归档要出确认条');
  assert.match(h.element.innerHTML, /确认归档/, '确认条要写清动作');
  click(h, {confirm: 'yes'});
  await drive(h, request => request.method === 'POST' ? {revision: 3} : pagedAnswer(pages)(request));
  const posts = h.requests.slice(before).filter(request => request.method === 'POST');
  assert.equal(posts.length, 1, '确认只发一次请求');
  assert.deepEqual(Object.keys(posts[0].values).sort(),
    ['action', 'expected_revision', 'growth_id', 'operation_key', 'view_token']);
  assert.equal(posts[0].values.action, 'retire');
  assert.equal(posts[0].values.growth_id, 'g-1');
  assert.deepEqual(cursorsOf(listReads(h, before)), ['(first)', 'c1'],
    '归档成功后走原地刷新：重读已加载的两页，不退回第一页');
});

test('链式版式：撤回同样要先确认，且只发一次请求', async () => {
  const h = harness();
  const pages = {list: [{items: [record('g-1', 'proposed')], next: null}],
    reviews: [{items: [], next: null}]};
  await mountPaged(h, pages, {layout: 'chain'});
  const before = h.requests.length;
  click(h, {act: 'withdraw', id: 'g-1'});
  assert.match(h.element.innerHTML, /lea-confirm/, '链式版式点撤回要出确认条');
  assert.match(h.element.innerHTML, /确认撤回/, '确认条要写清动作');
  click(h, {confirm: 'yes'});
  await drive(h, request => request.method === 'POST' ? {revision: 2} : pagedAnswer(pages)(request));
  const posts = h.requests.slice(before).filter(request => request.method === 'POST');
  assert.equal(posts.length, 1, '确认只发一次请求');
  assert.equal(posts[0].values.action, 'withdraw');
});

test('详情视图：归档也要出确认条', async () => {
  const h = harness();
  const pages = {list: [{items: [record('g-1', 'active')], next: null}],
    reviews: [{items: [], next: null}]};
  await mountPaged(h, pages);
  click(h, {open: 'g-1'});
  assert.match(h.element.innerHTML, /lea-detail/, '详情视图已经打开');
  click(h, {act: 'retire', id: 'g-1'});
  assert.match(h.element.innerHTML, /lea-confirm/, '详情视图点归档也要出确认条');
  assert.match(h.element.innerHTML, /确认归档/);
});

test('确认条取消：链式版式点取消什么都不发', async () => {
  const h = harness();
  const pages = {list: [{items: [record('g-1', 'active')], next: null}],
    reviews: [{items: [], next: null}]};
  await mountPaged(h, pages, {layout: 'chain'});
  click(h, {act: 'retire', id: 'g-1'});
  assert.match(h.element.innerHTML, /lea-confirm/);
  const before = h.requests.length;
  click(h, {confirm: 'no'});
  await h.settle();
  assert.equal(h.requests.slice(before).filter(request => request.method === 'POST').length, 0,
    '取消就什么都不发');
  assert.doesNotMatch(h.element.innerHTML, /lea-confirm/, '取消后确认条要收起来');
});

test('深链补读到多页后，重新读取的请求数有上限', async () => {
  const h = harness();
  const pages = {list: [], reviews: [{items: [], next: null}]};
  for (let index = 0; index < 12; index++) {
    pages.list.push({items: [record('g-' + (index + 1), 'active')],
      next: index < 11 ? 'c' + (index + 1) : null});
  }
  await mountPaged(h, pages, {layout: 'chain', recordFocus: {growth_id: 'g-12'}});
  assert.equal(listReads(h).length, 12, '深链一路扫到第 12 页才找到目标记录');
  assert.equal(h.page.state.data.items.length, 12);
  const before = h.requests.length;
  click(h, {action: 'refresh'});
  await drive(h, pagedAnswer(pages));
  assert.equal(listReads(h, before).length, 1,
    '刷新只重读用户可见的那一页；深链扫过的页不能被当成「已加载页」逐页重读');
  assert.equal(h.page.state.data.items.length, 12, '深链读到的记录仍然在列表里');
  assert.match(h.element.innerHTML, /g-12 的标题/, '目标记录仍在详情里');
  const second = h.requests.length;
  click(h, {action: 'refresh'});
  await drive(h, pagedAnswer(pages));
  assert.equal(listReads(h, second).length, 1, '第二次刷新也只重读一页');
});
