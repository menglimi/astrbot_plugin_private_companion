/* 08 每日复盘（self-review-page.js）的分页与写操作测试。
 *
 * 这一页的问题不在"读不到"，而在**读到的第二页会被下一次操作整块抹掉**：
 *   · `load({force:true})` 会把 items / stopped 全清空、把每个来源的 next 归零，
 *     裁决一条建议、点一次「重新读取」，用户已经翻出来的页就没了；
 *   · 「加载更多」直接把新一页 concat 在尾部，没有按 (来源, item_id) 去重；
 *   · 重绘是整块 innerHTML，滚动与展开的证据块会一起丢。
 *
 * 夹具与 learning-page 的测试同风格：vm + 手写假 window/root，请求按需应答。
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/self-review-page.js'), 'utf8');
const HOST = 'astrbot_plugin_private_companion';
const API = '/' + HOST + '/host/targets/t-1/roles/r-1/growth';
const flush = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 10; i++) await flush(); };

function item(id, extra = {}) {
  return {
    item_id: id, run_id: 'run-1', kind: 'ask_user',
    observation: id + ' 的观察', next_action: id + ' 的建议',
    confidence: 0.8, expires_at: '2099-01-01T00:00:00+00:00',
    created_at: '2026-10-01T00:00:00+00:00', state: 'pending',
    summary: '本次复盘', evidence: ['可核验的依据'], automatic: false,
    unavailable_reason: null, actions: ['accept', 'reject'],
    view_token: 'v'.repeat(64), ...extra,
  };
}
function reviewPolicy() {
  return {window_hours: 24, scan_seconds: 86400, empty_retry_seconds: 3600, minimum_evidence: 1};
}
function page(items, next = null, extra = {}) {
  return {items, next_cursor: next, expired_in_page: 0, reviewPolicy: undefined,
    policy: reviewPolicy(), context_key: 'ctx', role_id: 'r-1', ...extra};
}

function harness() {
  const requests = [];
  const window = {
    CompanionProduct: {bridgeNow: () => window.AstrBotPluginPage},
    AstrBotPluginPage: {
      ready: () => Promise.resolve({pluginName: HOST}),
      apiGet: (route, params) => new Promise((resolve, reject) =>
        requests.push({method: 'GET', route, params, resolve, reject})),
      apiPost: (route, body) => new Promise((resolve, reject) =>
        requests.push({method: 'POST', route, body, resolve, reject})),
    },
  };
  vm.runInNewContext(source, {
    window,
    document: {addEventListener: () => {}, querySelector: () => null, querySelectorAll: () => []},
    setTimeout, clearTimeout, console, Intl, Date, JSON, Math, Number, Object, Array,
    String, Set, Map, Boolean, Error, Promise,
  });
  const handlers = {};
  const element = {
    isConnected: true, innerHTML: '',
    addEventListener(type, handler) { handlers[type] = handler; },
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    dispatch(type, event) { return handlers[type]?.(event); },
  };
  return {
    page: window.CompanionSelfReviewPage, element, requests,
    reply: (request, payload) => request.resolve({status: 'ok', data: payload}),
    fail: (request, code) => request.resolve({status: 'error', message: code, data: {code}}),
    /* 请求本身没有走完：桥接直接拒绝，没有任何错误码可读。 */
    drop: request => request.reject(new Error('network')),
    settle,
  };
}

/* 桩元素：closest 只回答本页真的会问的那几个选择器。 */
const PROBES = {
  reviewRefresh: '[data-review-refresh]',
  reviewMore: '[data-review-more]',
  reviewAction: '[data-review-action]',
  reviewState: '[data-review-state]',
};
function node(attrs) {
  const self = {
    dataset: attrs,
    closest(selector) {
      for (const [key, pattern] of Object.entries(PROBES)) {
        if (attrs[key] !== undefined && selector === pattern) return self;
      }
      return null;
    },
  };
  return self;
}
const click = (h, attrs) => h.element.dispatch('click',
  {target: node(attrs), preventDefault() {}, stopPropagation() {}});

/* 应答所有在飞的请求，直到页面安静下来。 */
async function drive(h, answer) {
  const answered = new Set();
  for (let round = 0; round < 60; round++) {
    const pending = h.requests.filter(request => !answered.has(request));
    if (!pending.length) {
      await h.settle();
      if (!h.requests.some(request => !answered.has(request))) return;
      continue;
    }
    for (const request of pending) {
      answered.add(request);
      const payload = answer(request);
      if (payload && payload.__drop) h.drop(request);
      else if (payload && payload.__error) h.fail(request, payload.__error);
      else h.reply(request, payload ?? {});
    }
    await h.settle();
  }
  throw new Error('页面请求没有安静下来');
}
/* 按游标串起来的活动页：pages[0] 是没有 after_id 的第一页。 */
function activePager(pages, decided = []) {
  return request => {
    if (!request.route.endsWith('/self-review')) return {};
    if (request.params.state === 'decided') return page(decided);
    const after = request.params.after_id || '';
    const index = after ? pages.findIndex((entry, position) =>
      position > 0 && pages[position - 1].next === after) : 0;
    const entry = pages[index < 0 ? pages.length - 1 : index];
    return page(entry.items, entry.next, entry.extra);
  };
}
async function mount(h, answer, options = {}) {
  const mounted = h.page.mount(h.element, {
    sources: [{api: API, key: 'source-1', label: '角色 A'}], ...options,
  });
  await drive(h, answer);
  await mounted;
  return h;
}
const cards = h => (h.element.innerHTML.match(/class="sr-item"/g) || []).length;
const growthReads = (h, after = 0) => h.requests.slice(after)
  .filter(request => request.method === 'GET' && request.route === 'growth'
    || request.method === 'GET' && /\/growth$/.test(request.route));

/* ───────────────────── 分页 ───────────────────── */

test('加载更多：按 (来源, 条目) 去重，重叠的一页不会出现两份', async () => {
  const h = harness();
  const pages = [
    {items: [item('i-1'), item('i-2')], next: 'c1'},
    {items: [item('i-2'), item('i-3')], next: null},
  ];
  await mount(h, activePager(pages));
  assert.equal(cards(h), 2, '第一页两条');
  click(h, {reviewMore: ''});
  await drive(h, activePager(pages));
  assert.equal(cards(h), 3, '第二页与第一页重叠一条，去重后是三条');
  assert.equal((h.element.innerHTML.match(/i-2 的观察/g) || []).length, 1,
    '重叠的那条只应出现一次');
});

test('重新读取：已经加载过的页要原地重读，不是打回第一页', async () => {
  const h = harness();
  const pages = [
    {items: [item('i-1'), item('i-2')], next: 'c1'},
    {items: [item('i-3'), item('i-4')], next: 'c2'},
  ];
  await mount(h, activePager(pages));
  click(h, {reviewMore: ''});
  await drive(h, activePager(pages));
  assert.equal(cards(h), 4, '现场两页四条');
  const before = h.requests.length;
  click(h, {reviewRefresh: ''});
  await drive(h, activePager(pages));
  const active = h.requests.slice(before)
    .filter(request => request.method === 'GET' && /\/self-review$/.test(request.route)
      && request.params.state === 'active');
  const cursors = active.map(request => request.params.after_id || '(first)');
  assert.deepEqual(cursors, ['(first)', 'c1'],
    '重新读取应把已加载的两页都重读一遍，实际请求：' + cursors.join(','));
  assert.equal(cards(h), 4, '重读之后条目数不能减少');
});

test('重新读取时某页失败：保留已显示的条目，并把失败说清楚', async () => {
  const h = harness();
  const pages = [
    {items: [item('i-1'), item('i-2')], next: 'c1'},
    {items: [item('i-3'), item('i-4')], next: null},
  ];
  await mount(h, activePager(pages));
  click(h, {reviewMore: ''});
  await drive(h, activePager(pages));
  assert.equal(cards(h), 4);
  click(h, {reviewRefresh: ''});
  await drive(h, request => {
    const payload = activePager(pages)(request);
    /* 第二页（带 after_id 的那次）失败。 */
    if (request.params?.after_id === 'c1') return {__error: 'bridge_timeout'};
    return payload;
  });
  assert.equal(cards(h), 4, '一页失败不能把已经显示的条目抹掉');
  assert.match(h.element.innerHTML, /暂时无法读取|暂不可读/, '要说清有一页没读到');
});

/* ───────────────────── 写操作 ───────────────────── */

test('采纳一条建议后：已加载的两页仍然在，且不会只重读第一页', async () => {
  const h = harness();
  const pages = [
    {items: [item('i-1'), item('i-2')], next: 'c1'},
    {items: [item('i-3'), item('i-4')], next: 'c2'},
  ];
  await mount(h, activePager(pages));
  click(h, {reviewMore: ''});
  await drive(h, activePager(pages));
  assert.equal(cards(h), 4, '现场两页四条');
  const before = h.requests.length;
  click(h, {reviewAction: 'accept', reviewId: 'i-3', reviewSource: 'source-1'});
  click(h, {reviewAction: 'confirm', reviewId: 'i-3', reviewSource: 'source-1'});
  /* 后端在采纳之后会把这条变成 accepted；下一批读取要把这个状态带回来。 */
  const accepted = [
    {items: [item('i-1'), item('i-2')], next: 'c1'},
    {items: [item('i-3', {state: 'accepted', actions: []}), item('i-4')], next: 'c2'},
  ];
  await drive(h, request => request.method === 'POST'
    ? {item_id: 'i-3', state: 'accepted', replayed: false}
    : activePager(accepted)(request));
  assert.equal(cards(h), 4, '采纳一条建议不应让另外三条消失');
  const active = h.requests.slice(before)
    .filter(request => request.method === 'GET' && /self-review$/.test(request.route)
      && request.params.state === 'active');
  const cursors = active.map(request => request.params.after_id || '(first)');
  assert.deepEqual(cursors, ['(first)', 'c1'], '写操作后的刷新应重读已加载的两页');
  assert.match(h.element.innerHTML, /生效中/, '被采纳的那条要显示为生效中');
});

test('驳回一条建议后：它离开列表，刷新也不会把它带回来', async () => {
  const h = harness();
  const pages = [
    {items: [item('i-1'), item('i-2')], next: 'c1'},
    {items: [item('i-3'), item('i-4')], next: 'c2'},
  ];
  await mount(h, activePager(pages));
  click(h, {reviewMore: ''});
  await drive(h, activePager(pages));
  assert.equal(cards(h), 4);
  click(h, {reviewAction: 'reject', reviewId: 'i-3', reviewSource: 'source-1'});
  const rejected = [
    {items: [item('i-1'), item('i-2')], next: 'c1'},
    {items: [item('i-4')], next: 'c2'},
  ];
  await drive(h, request => request.method === 'POST'
    ? {item_id: 'i-3', state: 'rejected', replayed: false}
    : activePager(rejected)(request));
  assert.equal(cards(h), 3, '被驳回的那条应当离开生效列表');
  assert.doesNotMatch(h.element.innerHTML, /i-3 的观察/, '刷新不能把它带回来');
});

test('已停止注入的建议恢复可读后，不会同时出现在生效与停止两处', async () => {
  const h = harness();
  let readable = false;
  const answer = request => {
    if (!request.route.endsWith('/self-review')) return {};
    if (request.params.state === 'decided') return page([], null);
    return readable
      ? page([item('i-1')], null)
      : page([item('i-1', {unavailable_reason: 'evidence_expired'}), item('i-2')], null);
  };
  await mount(h, answer);
  assert.equal(cards(h), 2, '一条生效、一条已停止注入');
  assert.match(h.element.innerHTML, /已停止注入/);
  readable = true;
  click(h, {reviewRefresh: ''});
  await drive(h, answer);
  assert.equal(cards(h), 2, '恢复可读后只能有一张 i-1 的卡，不能两处各渲染一次');
  assert.doesNotMatch(h.element.innerHTML, /已停止注入/, '恢复后不再挂在「已停止注入」里');
  assert.equal((h.element.innerHTML.match(/i-1 的观察/g) || []).length, 1);
});

/* ───────────────────── 读态（失败 / 空 / 未知码） ───────────────────── */

/* 这一页曾经把「后端 500」讲成「当前状态未能确认」，因为它只看错误码：
   请求本身没走完时没有码，于是落进兜底文案，和「后端明确拒绝」混为一谈。 */
test('请求没有走完：说「读取失败」，不说「当前状态未能确认」', async () => {
  const h = harness();
  await mount(h, () => ({__drop: true}));
  assert.equal(h.page.state.data.phase, 'failed');
  assert.match(h.element.innerHTML, /读取失败/);
  assert.doesNotMatch(h.element.innerHTML, /当前状态未能确认/);
});

test('整页读不到：不出现「0 条」，也不说「暂无生效中的指导」', async () => {
  const h = harness();
  await mount(h, () => ({__error: 'self_review_evidence_unavailable'}));
  const html = h.element.innerHTML;
  assert.equal(h.page.state.data.phase, 'failed');
  assert.doesNotMatch(html, /0 条/, '失败态不能把「没读到」写成「0 条」');
  assert.doesNotMatch(html, /暂无生效中的指导/, '失败态不能说成「没有指导」');
  assert.doesNotMatch(html, /sr-kpis/, '失败态不渲染 KPI');
  assert.match(html, /原始依据已不可读取/, '要说清后端拒绝的原因');
  assert.match(html, /class="button small" type="button" data-review-refresh[^>]*>重新读取</,
    '失败态要给出一次重试');
});

test('后端返回未收录的错误码：有专属文案，不落到笼统兜底', async () => {
  const h = harness();
  await mount(h, () => ({__error: 'self_review_unmapped_probe'}));
  const html = h.element.innerHTML;
  assert.match(html, /未收录/);
  assert.match(html, /self_review_unmapped_probe/, '要带上后端给出的码，便于核对');
  assert.doesNotMatch(html, /当前状态未能确认/);
});

test('读取成功但一条都没有：仍然说「暂无复盘记录」，并显示最近一次复盘状态', async () => {
  const h = harness();
  await mount(h, () => page([], null,
    {status: {state: 'failed', code: 'self_review_output_invalid'}}));
  const html = h.element.innerHTML;
  assert.equal(h.page.state.data.phase, 'ready');
  assert.match(html, /暂无复盘记录/);
  assert.doesNotMatch(html, /读取失败|当前状态未能确认/);
  assert.match(html, /最近一次复盘状态/);
  assert.match(html, /上次失败/, '后端注入的最近一次状态要被渲染出来');
  assert.match(html, /self_review_output_invalid/);
});
