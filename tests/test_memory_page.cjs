/* 事实与经历页（档案库）测试。
 *
 * 这一页的行动逻辑、幂等键与待确认台账都跑在模块私有状态上，
 * 只能靠 vm 跑真实文件 + 假桥来驱动。夹具的职责是让「第一个请求真的发出去」，
 * 因为历史上有过一次票号取错位置、整页在读到目录前就返回的静默失败。
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/memory-page.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 8; i++) await flush(); };

/* 后端 / 列表下发的事实条目：只带列表页会给的字段。
   context_mode 是「核心记忆」的来源，/detail 不重复下发它。 */
function fact(atomId, revision, state = 'active', contextMode = 'default', predicate = '喜欢的东西') {
  return {
    atom_id: atomId, revision, state, context_mode: contextMode, predicate,
    representation: `${atomId} 的展示表述`, object: '某个对象',
  };
}

function targets() {
  return {
    installation_id: 'inst', operator_id: 'operator',
    targets: [{
      target_id: 'target-1', scope_kind: 'private', participant_id: 'user-1', platform_id: 'aiocqhttp', account_id: 'bot-1',
      roles: [{role_id: 'role-1', memory_api: '/astrbot_plugin_private_companion/memory'}],
    }],
  };
}

/* 归属判定下发的文档：字段名就是后端 ``MemoryOwnership.document()`` 的那几个。
   页面只按这些字段说话，不写死伙伴名字——所以夹具也必须走同一个形状。 */
function ownership(owner = 'self', reason = 'owner_module_absent', displayName = '') {
  return { owner, reason, module: '', provider: '', display_name: displayName };
}

/* 挂过的页面实例，供 test.after 收尾卸载（见文件末尾）。
   必须声明在使用它的 harness() **之前**：const 有暂时性死区，
   写在后面会在首次调用 harness 时抛 ReferenceError。 */
const HANDLES = [];

/* 按路由分发响应。routes 是 route 后缀 -> 值或函数(请求) 的映射。 */
function harness(routes, ownerDocument) {
  /* 归属是静态读数：默认给「V2 自己来」。想测让位或读不到，用第二个参数覆盖。 */
  const ownershipDocument = ownerDocument === undefined ? ownership() : ownerDocument;
  const requests = [];
  const store = new Map();
  const timers = realTimers();
  const window = {
    CompanionProduct: {
      hosted: () => true,
      selection: () => null,
      choose: () => {},
      bridgeNow: () => window.AstrBotPluginPage,
      bridgeFor: () => ({then: resolve => resolve(window.AstrBotPluginPage)}),
    },
    AstrBotPluginPage: {
      ready: () => Promise.resolve({pluginName: 'astrbot_plugin_private_companion'}),
      /* 归属是静态读数，随手回答掉。若让它排队，用例里那些「手动回答目录、
         再 settle() 若干拍」的写法会卡在这一条上：目录请求根本还没发出去，
         测试就把 settle 走完了。 */
      apiGet: (route, values) => new Promise((resolve, reject) => {
        if (route === 'page/ownership') { resolve(ownershipDocument); return; }
        requests.push({method: 'GET', route, values, resolve, reject});
      }),
      apiPost: (route, values) => new Promise((resolve, reject) => requests.push({method: 'POST', route, values, resolve, reject})),
    },
    localStorage: {
      getItem: key => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => store.set(key, String(value)),
      removeItem: key => store.delete(key),
    },
    crypto: {randomUUID: () => 'key-' + requests.length},
    matchMedia: () => ({matches: false}),
    requestAnimationFrame: () => 0,
    performance: {now: () => 0},
    /* setChrome 会在挂载与卸载各写一次顶栏徽标与面包屑，并且给 body 挂一个
       memory-connected 类。这几样给它空实现即可——本测试断言的是页面主体，不是外壳。 */
    document: {
      addEventListener: () => {},
      querySelector: () => null,
      querySelectorAll: () => [],
      body: {classList: {add: () => {}, remove: () => {}, toggle: () => {}}},
      title: '',
    },
    console,
    Intl, Date, JSON, Math, Number, Object, Array, String, Set, Map, Boolean, Error, Promise,
  };
  vm.runInNewContext(source, {
    window, crypto: window.crypto, document: window.document,
    setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
    URL, Blob, console, Intl, Date, JSON, Math, Number, Object, Array, String, Set, Map, Boolean, Error, Promise,
    CSS: {escape: value => value},
  });

  /* 宿主会整块重写 innerHTML，所以这个桩只需要记住最近一次写入的标记。
     __handlers 让测试能从真实注册的事件入口驱动，而不是绕开接线直接调私有函数。 */
  function root() {
    const handlers = {};
    /* click 处理器里有一条 `root.contains(el)` 守卫：元素已经不在当前树里就丢弃这次点击。
       真实 DOM 里这是防「重绘后点到旧节点」，桩里必须让它通过，否则整条点击链测不到。 */
    const inside = new Set();
    const node = {
      isConnected: true, innerHTML: '',
      __handlers: handlers, __inside: inside,
      addEventListener(type, handler) { handlers[type] = handler; },
      removeEventListener() {},
      contains(el) { return inside.has(el); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      getElementsByClassName() { return []; },
      dispatch(type, event) { return handlers[type]?.(event); },
    };
    return node;
  }

  const page = window.CompanionMemoryPage;
  const element = root();

  /* 回答一个在飞请求。route 是插件相对路径，如 'memory' 或 'memory/reflections'。
     响应形状照 memory_api 的信封：{status:'ok', data:{...}}。 */
  function reply(request, payload) {
    request.resolve({status: 'ok', data: payload});
  }
  function fail(request, code) {
    request.resolve({status: 'error', message: code, data: {code}});
  }
  /* 真实计时器：这里只是让出事件循环，不动时钟。 */
  const tick = async () => { for (let i = 0; i < 8; i++) await flush(); };

  /* 登记实例以便收尾卸载。页面在「整理与修复」视图里有 8 秒轮询（schedule()），
     在浏览器里是对的，在 Node 里却会把事件循环撑住——表现是测试全绿、
     进程不退出，CI 上就是"跑完了但一直不返回"。卸载会 stopTimer()。 */
  HANDLES.push(page);
  return {page, element, requests, reply, fail, settle: tick, window, timers};
}

/* 把目录 + 事实流这两步一次跑完：这是「页面真的活了」的最小证据。
 *
 * 事实流的预读是**串行**的（发一页、等确认、再决定要不要发下一页），
 * 所以夹具不能一次把所有分页请求都答应——那一刻后面几页还没发出来。
 * 这里做成「谁在飞就答谁」的循环，直到没有未决请求为止。 */
async function boot(h, facts, extra = {}) {
  const mounted = h.page.mount(h.element);
  await h.settle();

  const answered = new Set();
  let factsServed = 0;
  for (let round = 0; round < 30; round++) {
    const pending = h.requests.filter(r => !answered.has(r));
    if (!pending.length) break;
    for (const request of pending) {
      answered.add(request);
      /* 归属是挂载后的第一个请求（它必须在目录之前，这样目录失败时页面仍能说出
         记忆由谁提供）。夹具必须认识这条路由，否则它会带着一个未回答的请求卡在
         队列里，把后面所有用例的响应顺序错开。 */
      if (request.route === 'page/ownership') { h.reply(request, ownership()); continue; }
      if (request.route === 'page/targets') { h.reply(request, targets()); continue; }
      if (request.route === 'memory' && request.method === 'GET') {
        const page = facts.slice(factsServed * 50, factsServed * 50 + 50);
        factsServed += 1;
        h.reply(request, {
          items: page, next_cursor: page.length === 50 ? page[page.length - 1].atom_id : null,
          operator_id: 'operator', subject_id: 'subject',
          supported_visibility: ['private'], physical_erasure: 'not_supported',
        });
        continue;
      }
      const payload = extra[request.route.slice('memory'.length)];
      if (payload === undefined) { h.reply(request, {}); continue; }
      h.reply(request, typeof payload === 'function' ? payload(request) : payload);
    }
    await h.settle();
  }
  assert.ok(answered.size, '挂载后必须至少发出一个请求');
  assert.ok(h.requests.some(r => r.route === 'page/targets'), '挂载后必须请求目标目录');
  assert.ok(h.requests.some(r => r.route === 'memory' && r.method === 'GET'), '选到目标后必须读取事实列表');
  await mounted;
  return {factsServed};
}

test('挂载先拿归属，再读目录，再读事实流', async () => {
  const h = harness();
  await boot(h, [fact('a-1', 1)]);
  /* 归属是静态读数，夹具在 apiGet 里直接答掉，所以它不进队列——这也是可断言的
     契约：它不该占住队列、更不该让目录等它。顺序上它必须最先（它决定顶上那句
     话，而目录可能失败）；目录没读到就不知道要读哪个对象的记忆，所以目录又在
     事实流之前。 */
  assert.equal(h.requests.some(r => r.route === 'page/ownership'), false,
    '归属不该进队列：它是静态读数，让目录等它没有道理');
  const routes = h.requests.map(r => r.method + ' ' + r.route);
  assert.equal(routes[0], 'GET page/targets');
  assert.equal(routes[1], 'GET memory');
  assert.equal(h.page.state.data.ownership.owner, 'self');
  /* 跨 realm 陷阱：vm 里创建的数组原型与本 realm 不同，
     deepStrictEqual 会把内容相同的两个数组判成不等。转成字符串比。 */
  assert.equal(h.page.state.data.corpus.map(item => item.atom_id).join(","), 'a-1');
  assert.equal(h.page.state.data.ready, true);
});
test('事实流预读多页，直到后端不再给游标', async () => {
  const many = Array.from({length: 120}, (_, i) => fact('a-' + String(i).padStart(3, '0'), 1));
  const h = harness();
  await boot(h, many);
  /* 每页 50：120 条要三页，最后一页不再给游标就停，不会白读第四页。 */
  assert.equal(h.requests.filter(r => r.route === 'memory' && r.method === 'GET').length, 3);
  assert.equal(h.page.state.data.corpus.length, 120);
  assert.equal(h.page.state.data.hasMore, false);
});

test('超出预读窗口时留一个游标继续读更早的事实', async () => {
  const many = Array.from({length: 250}, (_, i) => fact('a-' + String(i).padStart(3, '0'), 1));
  const h = harness();
  await boot(h, many);
  /* 预读上限 4 页 = 200 条；剩下的靠游标，而不是一次读完。 */
  assert.equal(h.page.state.data.corpus.length, 200);
  assert.equal(h.page.state.data.hasMore, true);
  assert.equal(h.page.state.data.next, 'a-199');
});

test('语料比例带按生命周期统计，未知状态不算进有效', async () => {
  const h = harness();
  await boot(h, [
    fact('a-1', 1, 'active'), fact('a-2', 1, 'active'),
    fact('a-3', 1, 'superseded'), fact('a-4', 1, 'retracted'),
    fact('a-5', 1, 'something-new-from-a-later-backend'),
  ]);
  const markup = h.element.innerHTML;
  /* 有效 2 / 被取代 1 / 已撤回 1 / 未知归入已取代 => 2 */
  assert.match(markup, /data-count="5"/);
  assert.match(markup, /mem-state-active/);
  assert.match(markup, /mem-state-retracted/);
  /* 未知状态不能被说成「有效」——那是把后端的新取值伪装成正常数据 */
  const band = markup.slice(markup.indexOf('mem-band'), markup.indexOf('mem-legend'));
  assert.equal((band.match(/mem-state-active/g) || []).length, 1);
  assert.equal((band.match(/mem-state-superseded/g) || []).length, 1);
});

test('事实页读取全量统计并展示已授权的外部记忆分类计数', async () => {
  const h = harness();
  await boot(h, [fact('a-1', 1)], {'/overview': {
    local: {
      facts: {total: 13, active: 9, by_state: {active: 9, superseded: 2, retracted: 1, forgotten: 1}},
      experiences: {total: 4, active: 3}, sources: {available_messages: 27},
      reflections: {total: 12, by_state: {complete: 3, empty: 4, failed: 2, running: 3}},
    },
    linkage: {state: 'ready', display_name: '记忆插件', counts: {profile: 2, preference: 3,
      relationship: 1, private_conversation: 5, other: 0, total: 11}, categories: [
      {key: 'profile', count: 2}, {key: 'preference', count: 3}, {key: 'relationship', count: 1},
      {key: 'private_conversation', count: 5}, {key: 'other', count: 0},
    ]},
  }});
  const markup = h.element.innerHTML.replace(/<[^>]*>/g, '');
  assert.match(markup, /9 \/ 13/);
  assert.match(markup, /3 \/ 4/);
  assert.match(markup, /27/);
  assert.match(markup, /整理完成 3/);
  assert.match(markup, /已接通/);
  assert.match(markup, /个人资料/);
  assert.match(markup, /私聊内容/);
  assert.equal(h.page.state.data.overview.linkage.counts.total, 11);
});

test('整理视图可切到全部批次并请求完成与无新增记录', async () => {
  const h = harness();
  await boot(h, []);
  const area = {disabled: false, id: 'memory-area-reflections', dataset: {}};
  area.closest = () => area;
  h.element.__inside.add(area);
  h.element.dispatch('click', {target: area});
  await h.settle();
  const pending = h.requests.find(item => item.route === 'memory/reflections');
  assert.equal(pending.values.history, '0');
  h.reply(pending, {items: [], next_cursor: null});
  const firstOverview = h.requests.filter(item => item.route === 'memory/overview').at(-1);
  h.reply(firstOverview, {local: {reflections: {total: 2, by_state: {complete: 1, empty: 1}}}, linkage: {state: 'not_owner'}});
  await h.settle();

  const toggle = {disabled: false, id: '', dataset: {reflectionView: 'history'}};
  toggle.closest = () => toggle;
  h.element.__inside.add(toggle);
  h.element.dispatch('click', {target: toggle});
  await h.settle();
  const history = h.requests.filter(item => item.route === 'memory/reflections').at(-1);
  assert.equal(history.values.history, '1');
  h.reply(history, {items: [{batch_id: 'complete-batch', state: 'complete', state_label: '整理完成',
    summary: '已保存结果', receipts: {}, remaining_sources: 0}], next_cursor: null});
  const secondOverview = h.requests.filter(item => item.route === 'memory/overview').at(-1);
  h.reply(secondOverview, {local: {reflections: {total: 2, by_state: {complete: 1, empty: 1}}}, linkage: {state: 'not_owner'}});
  await h.settle();
  assert.equal(h.page.state.data.history, true);
  assert.match(h.element.innerHTML, /全部批次/);
  assert.match(h.element.innerHTML, /整理完成/);
});

test('筛选与搜索都在本地完成，不再发请求', async () => {
  const h = harness();
  await boot(h, [
    fact('a-1', 1, 'active', 'default', '喜欢的食物'),
    fact('a-2', 1, 'retracted', 'default', '害怕的东西'),
  ]);
  const before = h.requests.length;
  /* 筛选与搜索是纯本地派生：语料已在内存里，切状态或敲搜索词不该再打后端。
     这两条路径不发请求，所以计数不变就是断言。 */
  h.page.state.data.filter = 'retracted';
  h.page.state.data.search = '害怕';
  await h.settle();
  assert.equal(h.requests.length, before);
  /* 语料没被筛选动过——筛选只影响渲染，不改数据。 */
  assert.equal(h.page.state.data.corpus.length, 2);
});

test('选中事实会读详情，并顺手带出版本历史', async () => {
  const h = harness();
  /* 必须先 boot 才能点得动卡片：列表没加载时页面上根本没有那条卡片，
     直接调 click 测到的是「没有卡片」而不是「卡片点不开」。 */
  await boot(h, [fact('a-1', 3, 'active', 'pinned')]);
  assert.equal(h.page.state.data.corpus.length, 1);

  /* 走事件入口而不是直接调私有函数：点击处理器里 el.dataset.factId 的分支
     正是列表卡片的接线，跳过去就测不到「卡片能不能点开」。
     click 处理器先 closest("button") 再读 dataset，所以这里两处都要给。 */
  const click = h.element.__handlers.click;
  assert.ok(click, '挂载时应当注册 click 处理器');
  /* 造一个「在树里的卡片节点」：id 为空（避开按 id 分派的那些分支），
     dataset.factId 指向列表里那条事实。 */
  const card = {disabled: false, id: '', dataset: {factId: 'a-1'}};
  card.closest = () => card;
  h.element.__inside.add(card);
  click({target: {closest: () => card}});
  await h.settle();

  const detail = h.requests.find(r => r.route === 'memory/detail');
  assert.ok(detail, '点开事实后必须请求详情');
  assert.equal(detail.values.atom_id, 'a-1');
  assert.equal(detail.values.revision, 3);
  h.reply(detail, {item: {atom_id: 'a-1', revision: 3, state: 'active', representation: '展示表述', object: '某个对象'},
    sources: [{handle: 'internal-source', source_kind: 'human_message'}], session_id: 's-1'});
  await h.settle();

  /* 版本历史是这一页的核心信息，打开一条事实就该带出来，不该再点一次。 */
  const revisions = h.requests.find(r => r.route === 'memory/revisions');
  assert.ok(revisions, '打开详情后应当顺手请求版本历史');
  h.reply(revisions, {items: [{revision: 1, state: 'active', representation: '第一版'}, {revision: 3, state: 'active', representation: '第三版'}]});
  await h.settle();

  assert.equal(h.page.state.data.revisions.length, 2);
  /* pinned 由列表项带过来：/detail 不重复下发 context_mode，
     但「是不是核心记忆」是详情要显示的东西，漏了这条详情上就少一个徽章。 */
  assert.equal(h.page.state.data.factDetail.item.context_mode, 'pinned');
  assert.match(h.element.innerHTML, /核心事实/);
  assert.match(h.element.innerHTML, /第三版/);
  assert.match(h.element.innerHTML, /对方消息/);
  assert.doesNotMatch(h.element.innerHTML.replace(/<[^>]*>/g, ''), /human_message|internal-source/);
  assert.match(h.element.innerHTML, /data-source-handle="internal-source"/);
});

test('归档恢复显示可读记录并提交原始标识', async () => {
  const h = harness();
  await boot(h, []);
  h.window.CompanionConfirm = () => Promise.resolve(true);
  const area = {disabled: false, id: 'memory-area-archive', dataset: {}};
  area.closest = () => area;
  h.element.__inside.add(area);
  h.element.dispatch('click', {target: area});
  await h.settle();
  const preview = {counts: {eligible: 1, already_archived: 1, retained: 1}, idle_days: 180,
    archive_token: 'internal-token', scope: 'durable_relationship_facts', erasure: 'none',
    items: [{atom_id: 'internal-eligible', predicate: '饮用偏好', representation: '喜欢茶', idle_since: '2026-01-01T00:00:00Z'}],
    archived_items: [{atom_id: 'internal-archived', predicate: '阅读偏好', representation: '喜欢诗歌'}]};
  h.reply(h.requests.find(request => request.route === 'memory/archive/preview'), preview);
  await h.settle();
  const visible = h.element.innerHTML.replace(/<[^>]*>/g, '');
  assert.match(visible, /饮用偏好|喜欢诗歌/);
  assert.doesNotMatch(visible, /internal-|atom_id|durable_relationship_facts/);
  assert.match(h.element.innerHTML, /data-archive-restore="internal-archived"/);
  h.element.querySelectorAll = selector => selector === '[data-archive-restore]:checked'
    ? [{dataset: {archiveRestore: 'internal-archived'}}] : [];
  const restore = {disabled: false, id: 'memory-archive-restore-submit', dataset: {}};
  restore.closest = () => restore;
  h.element.__inside.add(restore);
  h.element.dispatch('click', {target: restore});
  await h.settle();
  const request = h.requests.find(item => item.route === 'memory/archive/restore');
  assert.equal(request.values.atom_ids.join(','), 'internal-archived');
  h.reply(request, {items: [{atom_id: 'internal-archived'}]});
  await h.settle();
  h.reply(h.requests.filter(item => item.route === 'memory/archive/preview').at(-1), {...preview, archived_items: []});
  await h.settle();
  assert.match(h.element.innerHTML, /当前没有已归档的事实/);
});

test('群聊目标不给 memory_api 时，整页给一条说明而不是空表', async () => {
  const h = harness();
  const mounted = h.page.mount(h.element);
  await h.settle();
  h.reply(h.requests.find(r => r.route === 'page/targets'), {
    installation_id: 'inst', operator_id: 'operator',
    targets: [{
      target_id: 'g-1', scope_kind: 'group', group_id: 'group-1', platform_id: 'aiocqhttp', account_id: 'bot-1',
      roles: [{role_id: 'role-1'}],
    }],
  });
  await h.settle();
  await mounted;
  assert.match(h.element.innerHTML, /群聊记录独立管理/);
  assert.equal(h.requests.filter(r => r.route === 'memory').length, 0);
});

test('目录失败时给一句人话，不留旧内容', async () => {
  const h = harness();
  const mounted = h.page.mount(h.element);
  await h.settle();
  h.fail(h.requests.find(r => r.route === 'page/targets'), 'core_access_required');
  await h.settle();
  await mounted;
  assert.match(h.element.innerHTML, /没有 事实管理权限/);
  assert.equal(h.page.state.data.ready, false);
  assert.equal(h.page.state.data.catalog, null);
});

/* 计时器一律用真实的。
 *
 * 试过压缩：把页面里的长等待折短。但这一页有两处按时间互相牵制的东西——
 * whenBridgeReady 的 10 秒放弃，和 bounded() 包裹每个请求的 15 秒竞速。
 * 压缩任一个都会改变两者的相对关系，于是「桥读到了但请求被判超时」这种
 * 只存在于夹具里的失败就会出现。测页面就不要动它的时钟。
 *
 * 代价是「桥连不上」那个用例要真的等十秒。只有一个，接受。 */
function realTimers() {
  return {setTimeout: (fn, ms, ...rest) => setTimeout(fn, ms, ...rest), clearTimeout: id => clearTimeout(id)};
}

test('桥连不上时报 bridge_unavailable，不伪装成内容', async () => {
  const h = harness();
  /* 把桥和 product 一起摘掉：这时页面只能靠自己轮询，十秒后必须落到
     bridge_unavailable，而不是退回一份示例记录。 */
  h.window.AstrBotPluginPage = undefined;
  h.window.CompanionProduct = {hosted: () => false, bridgeNow: () => null, bridgeFor: () => null};
  const mounted = h.page.mount(h.element);
  await h.settle();
  await mounted;
  assert.match(h.element.innerHTML, /还没有接上 AstrBot/);
  assert.doesNotMatch(h.element.innerHTML, /展示表述/);
  assert.equal(h.page.state.data.ready, false);
});

test('unmount 之后迟到的响应不会写回页面', async () => {
  const h = harness();
  const mounted = h.page.mount(h.element);
  await h.settle();
  const directory = h.requests.find(r => r.route === 'page/targets');
  h.page.unmount();
  h.reply(directory, targets());
  await h.settle();
  await mounted;
  assert.equal(h.page.state.data, null);
});

/* ── 归属文案（记忆由谁提供） ────────────────────────────────────────
   这三条钉的是「页面按后端判定说话」：让位时点名伙伴，自己来时说明原因，
   读不到时不假装知道。名字只能来自 display_name，页面不许写死。 */

test('让位时只标外部提供方，不写长提示', async () => {
  const h = harness(undefined, ownership('external', 'external_owner_active', '某个记忆插件'));
  await boot(h, [fact('a-1', 1)]);
  const text = h.element.innerHTML;
  assert.match(text, /长期记录：〈某个记忆插件〉（外部）/);
  /* 名字来自 display_name：换一个名字，页面说的就得跟着换。 */
  assert.doesNotMatch(text, /我会牢牢记住你/);
});

test('自己来时只标本插件事实库', async () => {
  const h = harness(undefined, ownership('self', 'owner_handle_disabled', '某个记忆插件'));
  await boot(h, [fact('a-1', 1)]);
  assert.match(h.element.innerHTML, /长期记录：本插件事实库/);
});

test('归属读不到时按「V2 自己来」说，不留空也不假装知道', async () => {
  /* owner 不是字符串 = 后端没给出可用判定（路由缺失、响应残缺）。
     这时页面必须说「读不出来」并给出兜底口径，而不是留空让人猜。 */
  const h = harness(undefined, {owner: null, reason: ''});
  await boot(h, [fact('a-1', 1)]);
  assert.match(h.element.innerHTML, /记录归属暂时读不出来/);
  assert.match(h.element.innerHTML, /注入与工具都会照常出现/);
});

test('归属形状能读但取值不认识时，仍按「V2 自己来」说清口径', async () => {
  /* owner 是字符串但不在允许集合里：页面不该崩，也不该假装知道伙伴是谁，
     而是走「自己来」那一支，用兜底原因说明。 */
  const h = harness(undefined, {owner: 'nonsense', reason: 'nonsense', display_name: ''});
  await boot(h, [fact('a-1', 1)]);
  assert.match(h.element.innerHTML, /长期记录：本插件事实库/);
});


/* 收尾：把所有挂过的页面卸掉，让进程能自然退出。
   放在 after() 里而不是每个用例末尾——用例中途失败时也该清理，
   否则失败现场会被一个不退出的进程盖住。 */
test.after(() => {
  for (const page of HANDLES) { try { page.unmount(); } catch (error) { /* 没挂干净就算了 */ } }
  HANDLES.length = 0;
});
