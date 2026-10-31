const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/semantic-page.js'), 'utf8');
const styles = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/semantic-page.css'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function catalog(kind, name) {
  return {
    state: 'ready', selection_ready: true, settings: {revision: 0}, providers: {},
    catalog: {reaction: {enabled: 1}, slang: {enabled: 1}},
    sessions: ['group-1', 'group-2'], session_id: 'group-1', next_after: null,
    items: [{id: kind + '-1', kind, revision: 1, enabled: true,
      payload: kind === 'reaction' ? {name} : {term: name, meaning: name}}],
  };
}

function harness(group = false) {
  const requests = [];
  const window = {
    CompanionProduct: {
      hosted: () => true, scopeKey: () => 'same-role',
      selection: () => ({target_id: 'target', role_id: 'role'}),
      target: () => group ? {scope_kind: 'group', group_id: 'group-1'} : {scope_kind: 'private'},
      unwrap: value => value,
      /* 页面统一走 bridgeFor() 取桥，夹具必须提供。刻意返回同步 thenable：
         用 Promise.resolve 会多引入一个微任务，而下面几个用例断言的是
         「mount 之后 requests[0] 立刻可用」——多一拍就取到 undefined。 */
      bridgeFor: () => ({then: resolve => resolve(window.AstrBotPluginPage)}),
      /* 同步取桥。页面先问它，没有再走 bridgeFor()——这样常规路径不发散时序。 */
      bridgeNow: () => window.AstrBotPluginPage,
    },
    AstrBotPluginPage: {
      apiGet: (route, values) => new Promise((resolve, reject) => requests.push({method: 'GET', route, values, resolve, reject})),
      apiPost: (route, values) => new Promise((resolve, reject) => requests.push({method: 'POST', route, values, resolve, reject})),
    },
    lucide: {createIcons() {}},
    CompanionConfirm: () => Promise.resolve(true),
  };
  /* FormData 的替身：page 用 new FormData(form) 读表单，而这里的 form 是
     提交时构造的桩，所以直接用它的 values 映射。 */
  class FormDataStub {
    constructor(form) { this.map = (form && form.values) || {}; }
    has(name) { return Boolean(this.map[name]); }
    get(name) { return name in this.map ? this.map[name] : null; }
  }
  class FileReaderStub {
    readAsDataURL(file) { this.result = file.dataUrl; this.onload?.(); }
  }
  vm.runInNewContext(source, {window, AbortController, FormData: FormDataStub, FileReader: FileReaderStub});
  function root() {
    const handlers = {};
    return {
      isConnected: true, innerHTML: '',
      addEventListener(type, handler) { handlers[type] = handler; },
      querySelectorAll() { return []; },
      querySelector() { return null; },
      click(selector, dataset) {
        return handlers.click({target: {closest: candidate => candidate === selector ? {dataset} : null}});
      },
      change(id, files) { handlers.change({target: {id, files}}); },
      /* 设置表单的提交：values 是 FormData 的替身内容。
         数值保持原类型，否则 Number() 读到字符串会得到 NaN。 */
      submit(id, values) {
        const map = values || {};
        handlers.submit({preventDefault() {}, target: {id, values: map}});
      },
    };
  }
  return {page: window.CompanionSemanticPage, requests, root};
}

test('late response from a previous mount cannot overwrite the current catalog', async () => {
  const {page, requests, root} = harness();
  const first = root(), second = root();
  page.mount(first);
  page.unmount();
  page.mount(second);
  requests[1].resolve(catalog('reaction', 'current reaction'));
  await flush();
  requests[0].resolve(catalog('reaction', 'stale reaction'));
  await flush();
  assert.match(second.innerHTML, /current reaction/);
  assert.doesNotMatch(second.innerHTML, /stale reaction/);
});

test('switching catalogs discards an outstanding item detail', async () => {
  const {page, requests, root} = harness(true);
  const node = root();
  page.mount(node);
  requests[0].resolve(catalog('reaction', 'reaction entry'));
  await flush();
  node.click('[data-semantic-open]', {semanticOpen: 'reaction-1'});
  assert.equal(requests[1].values.kind, 'reaction');
  node.click('[data-semantic-kind]', {semanticKind: 'slang'});
  assert.equal(requests[2].values.kind, 'slang');
  requests[2].resolve(catalog('slang', 'current slang'));
  await flush();
  requests[1].resolve({item: {id: 'reaction-1', revision: 1, enabled: true,
    payload: {name: 'stale detail'}}});
  await flush();
  assert.match(node.innerHTML, /current slang/);
  assert.doesNotMatch(node.innerHTML, /stale detail|id="semanticEditor"/);
});

test('late command receipt cannot reload a newly selected catalog', async () => {
  const {page, requests, root} = harness(true);
  const node = root();
  page.mount(node);
  requests[0].resolve(catalog('reaction', 'reaction entry'));
  await flush();
  node.click('[data-semantic]', {semantic: 'reindex'});
  assert.equal(requests[1].method, 'POST');
  node.click('[data-semantic-kind]', {semanticKind: 'slang'});
  requests[2].resolve(catalog('slang', 'current slang'));
  await flush();
  requests[1].resolve({state: 'scheduled'});
  await flush();
  assert.equal(requests.length, 3);
  assert.match(node.innerHTML, /current slang/);
  assert.doesNotMatch(node.innerHTML, /索引重建已安排/);
});

test('initial read failure offers a retry that loads the catalog', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].reject(new Error('read failed'));
  await flush();
  assert.match(node.innerHTML, /读取失败，请重试/);
  assert.doesNotMatch(node.innerHTML, /read failed/);
  assert.match(node.innerHTML, /data-semantic="retry"/);
  node.click('[data-semantic]', {semantic: 'retry'});
  requests[1].resolve(catalog('reaction', 'recovered reaction'));
  await flush();
  assert.match(node.innerHTML, /recovered reaction/);
  assert.doesNotMatch(node.innerHTML, /data-semantic="retry"/);
});

/* 设置表单的桩：全部可编辑字段都参与版本冲突与草稿往返。 */
function settingsCatalog(revision = 3, overrides = {}) {
  return {
    state: 'ready', selection_ready: true, providers: {embedding: [], rerank: [], analysis: []},
    settings: {revision, enabled: true, memory_enabled: true, reactions_enabled: true,
      reactions_private_enabled: true, reactions_group_enabled: false,
      reactions_collection_private_enabled: false, reactions_collection_group_enabled: false,
      reactions_collection_private_probability: 0.1, reactions_collection_group_probability: 0.1,
      reactions_collection_mode: 'automatic',
      reactions_trigger_probability: 0.2, reactions_cooldown_seconds: 180,
      reactions_delivery_mode: 'separate_after', reactions_image_format: 'image',
      slang_enabled: true, slang_learning: true, slang_web_search_enabled: false,
      vision_review: true,
      embedding_provider: '', rerank_provider: '', analysis_provider: '',
      timeout_seconds: 8, batch_size: 24, learning_interval_seconds: 21600,
      slang_web_search_candidates: 4, slang_web_search_results: 2, ...overrides},
    catalog: {reaction: {enabled: 1}, slang: {enabled: 1}},
    sessions: [], session_id: null, items: [], next_after: null,
  };
}

const FORM = {enabled: true, memory_enabled: true, reactions_enabled: true,
  reactions_private_enabled: true, reactions_group_enabled: false,
  reactions_collection_private_enabled: true, reactions_collection_group_enabled: false,
  reactions_collection_private_probability: 15, reactions_collection_group_probability: 20,
  reactions_collection_mode: 'confirm',
  reactions_trigger_probability: 60, reactions_cooldown_seconds: 120,
  reactions_delivery_mode: 'same_message', reactions_image_format: 'qq_emoji',
  slang_enabled: true, slang_learning: true, slang_web_search_enabled: false,
  vision_review: true,
  embedding_provider: '', rerank_provider: '', analysis_provider: '',
  timeout_seconds: 12, batch_size: 24, learning_interval_seconds: 360,
  slang_web_search_candidates: 6, slang_web_search_results: 3};

test('a revision conflict re-reads and keeps the values the user just entered', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(settingsCatalog(3));
  await flush();
  node.submit('semanticSettings', FORM);
  assert.equal(node.innerHTML.includes('草稿待核对'), false, 'no draft before submitting');
  assert.equal(requests[1].values.action, 'configure');
  assert.equal(requests[1].values.revision, 3);
  assert.equal(requests[1].values.changes.timeout_seconds, 12, 'the submitted numbers must be numbers');
  assert.equal(requests[1].values.changes.learning_interval_seconds, 21600,
    'the minute control must store seconds');
  assert.match(node.innerHTML, /群词义整理间隔 \/ 分钟[\s\S]*value="360"/);
  /* 联网参考的三项：开关必须是布尔，两个计数必须是数字。夹具缺字段时
     values.get() 返回 null，Number(null) 会得到 0，而后端拒绝 0。 */
  assert.equal(requests[1].values.changes.slang_web_search_enabled, false,
    'the search switch must be a boolean, never a missing key');
  assert.equal(requests[1].values.changes.slang_web_search_candidates, 6);
  assert.equal(requests[1].values.changes.slang_web_search_results, 3);
  assert.equal(requests[1].values.changes.reactions_collection_private_probability, 0.15);
  assert.equal(requests[1].values.changes.reactions_collection_group_probability, 0.2);
  assert.equal(requests[1].values.changes.reactions_collection_mode, 'confirm');
  assert.equal(requests[1].values.changes.reactions_trigger_probability, 0.6);
  assert.equal(requests[1].values.changes.reactions_delivery_mode, 'same_message');
  /* 别处先改过：服务端拒绝这次 CAS。 */
  requests[1].reject(new Error('settings changed; refresh before saving'));
  await flush();
  /* 重读一次拿新版本号。 */
  assert.equal(requests[2].method, 'GET');
  /* 重读返回的是服务端旧值（timeout 8），用户填的是 12。 */
  requests[2].resolve(settingsCatalog(4));
  await flush();
  assert.match(node.innerHTML, /已被别处修改/);
  assert.match(node.innerHTML, /value="12"/, 'the user input must survive the reload');
  assert.match(node.innerHTML, /semantic-conflict/);
  /* 再次提交按新版本号写入，且带的仍是用户的草稿。 */
  node.submit('semanticSettings', FORM);
  assert.equal(requests[3].values.revision, 4);
  assert.equal(requests[3].values.changes.timeout_seconds, 12);
});

test('a successful save clears the draft instead of leaving it labelled', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(settingsCatalog(3));
  await flush();
  node.submit('semanticSettings', {...FORM, timeout_seconds: 15});
  requests[1].resolve({});
  await flush();
  /* 保存成功 → reload 显示服务端值（这里仍是 8），且不再标草稿。 */
  assert.equal(requests[2].method, 'GET');
  requests[2].resolve(settingsCatalog(4, {timeout_seconds: 15}));
  await flush();
  assert.doesNotMatch(node.innerHTML, /草稿待核对/);
  assert.match(node.innerHTML, /value="15"/);
});

test('discarding a draft restores the saved values', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(settingsCatalog(3));
  await flush();
  node.submit('semanticSettings', {...FORM, timeout_seconds: 12});
  requests[1].reject(new Error('settings changed; refresh before saving'));
  await flush();
  requests[2].resolve(settingsCatalog(4));
  await flush();
  assert.match(node.innerHTML, /data-semantic="discard"/);
  node.click('[data-semantic]', {semantic: 'discard'});
  assert.doesNotMatch(node.innerHTML, /草稿待核对/);
  assert.match(node.innerHTML, /value="8"/, 'discard shows the saved value again');
});

test('a non-conflict failure does not fabricate a draft or a reload', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(settingsCatalog(3));
  await flush();
  node.submit('semanticSettings', FORM);
  requests[1].reject(new Error('unsupported reaction delivery setting'));
  await flush();
  assert.equal(requests.length, 2, 'a plain failure must not trigger a re-read');
  assert.match(node.innerHTML, /操作失败，请刷新核对/);
  assert.doesNotMatch(node.innerHTML, /unsupported reaction delivery setting/);
  assert.doesNotMatch(node.innerHTML, /草稿待核对/);
});

test('a machine code is translated instead of shown raw', async () => {
  /* 后端把机器码放在信封的 message 上（页面桥只转发 message），共享的
     CompanionProduct.labels 表不含这里用到的多数码。裸码不该出现在页面上。 */
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].reject(new Error('staging_not_initialized'));
  await flush();
  assert.doesNotMatch(node.innerHTML, /staging_not_initialized/);
  assert.match(node.innerHTML, /角色数据尚未加载，请重新读取/);
});

test('unloaded role data offers an inline reload', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].reject(new Error('staging_not_initialized'));
  await flush();
  assert.match(node.innerHTML, /data-semantic="retry"/);
  assert.doesNotMatch(node.innerHTML, /data-manage="host"/);

  requests[1] = null;
  const other = root();
  page.mount(other);
  requests[2].reject(new Error('read failed'));
  await flush();
  assert.match(other.innerHTML, /data-semantic="retry"/);
  assert.doesNotMatch(other.innerHTML, /data-manage="host"/);
});

test('an unavailable semantic service offers an inline reload', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].reject(Object.assign(new Error('unavailable'), {code: 'supervisor_fenced'}));
  await flush();
  assert.match(node.innerHTML, /语义服务暂不可用/);
  assert.match(node.innerHTML, /data-semantic="retry"/);
  assert.doesNotMatch(node.innerHTML, /读取失败|data-manage="host"/);
});

test('unnamed semantic search results keep internal IDs only in action attributes', async () => {
  const {page, requests, root} = harness();
  const node = root();page.mount(node);
  requests[0].resolve(settingsCatalog());await flush();
  node.submit('semanticSearch', {query:'表情'});
  requests[1].resolve({mode:'keyword',embedded:0,eligible:1,
    items:[{id:'internal-material-opaque',payload:{},score:.5}]});await flush();
  assert.match(node.innerHTML, /data-semantic-open="internal-material-opaque">未命名素材 1 · 0.50<\/button>/);
  assert.doesNotMatch(node.innerHTML, />internal-material-opaque/);
});

test('expression settings expose per-scope collection controls and preserve stored probabilities', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node, {view: 'expression'});
  requests[0].resolve(settingsCatalog());
  await flush();
  assert.match(node.innerHTML, /从私聊自动收录/);
  assert.match(node.innerHTML, /从群聊自动收录/);
  assert.match(node.innerHTML, /私聊收录概率 \/ %/);
  assert.match(node.innerHTML, /value="10"/);
  assert.match(node.innerHTML, /semantic-catalog-stats/);
  assert.match(node.innerHTML, /自动识别/);
  assert.match(node.innerHTML, /可用 1 \/ 1 项/);
  assert.match(node.innerHTML, /待人工确认/);
  node.submit('semanticSettings', FORM);
  assert.equal(requests[1].values.changes.reactions_collection_private_probability, 0.15);
  assert.equal(requests[1].values.changes.reactions_collection_group_probability, 0.2);
  assert.equal(requests[1].values.changes.reactions_delivery_mode, 'same_message');
  assert.equal(requests[1].values.changes.reactions_private_enabled, true);
});

test('reaction editor can delete the current revision and then reload the catalog', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node, {view: 'expression'});
  requests[0].resolve(catalog('reaction', 'auto-collected sticker'));
  await flush();
  node.click('[data-semantic-open]', {semanticOpen: 'reaction-1'});
  requests[1].resolve({item: {id: 'reaction-1', revision: 4, enabled: true,
    payload: {name: 'auto-collected sticker', description: 'a wave'}}});
  await flush();
  assert.match(node.innerHTML, /data-semantic="delete"/);
  await node.click('[data-semantic]', {semantic: 'delete'});
  assert.equal(requests[2].values.action, 'delete');
  assert.equal(requests[2].values.id, 'reaction-1');
  assert.equal(requests[2].values.revision, 4);
  requests[2].resolve({deleted: true});
  await flush();
  assert.equal(requests[3].method, 'GET');
  requests[3].resolve({...catalog('reaction', ''), items: [], catalog: {reaction: {enabled: 0}}});
  await flush();
  assert.match(node.innerHTML, /表情素材已删除/);
  assert.match(node.innerHTML, /当前没有素材/);
});

test('batch import reports new, duplicate, failed, and out-of-limit files without stopping', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node, {view: 'expression'});
  requests[0].resolve({...settingsCatalog(), reaction_collection: {totals: {}, recent: []}});
  await flush();
  node.change('semanticFile', [
    {name: 'new.png', size: 10, dataUrl: 'data:image/png;base64,AA=='},
    {name: 'duplicate.png', size: 10, dataUrl: 'data:image/png;base64,AA=='},
    {name: 'large.png', size: 5 * 1024 * 1024 + 1, dataUrl: ''},
  ]);
  await flush();
  assert.equal(requests[1].values.action, 'import');
  requests[1].resolve({created: true});
  await flush();
  assert.equal(requests[2].values.action, 'import');
  requests[2].resolve({created: false});
  await flush();
  assert.equal(requests[3].method, 'GET');
  requests[3].resolve({...settingsCatalog(), reaction_collection: {totals: {}, recent: []}});
  await flush();
  assert.match(node.innerHTML, /新增 1 张，重复跳过 1 张，失败 1 张/);
});

test('one button recognizes every pending reaction card and exposes progress', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node, {view: 'expression'});
  const pending = ['one', 'two'].map((name, index) => ({
    id: 'reaction-' + (index + 1), kind: 'reaction', revision: 1, enabled: true,
    payload: {name, analysis: 'pending'},
  }));
  requests[0].resolve({...settingsCatalog(), catalog: {reaction: {pending: 2}}, items: pending});
  await flush();
  assert.match(node.innerHTML, /批量识别待补全 \(2\)/);

  node.click('[data-semantic]', {semantic: 'analyze-pending'});
  assert.equal(requests[1].values.action, 'analyze');
  assert.equal(requests[1].values.id, 'reaction-1');
  requests[1].resolve({item: {...pending[0], payload: {name: 'one', analysis: 'manual'}}});
  await flush();
  assert.equal(requests[2].values.action, 'analyze');
  assert.equal(requests[2].values.id, 'reaction-2');
  requests[2].resolve({item: {...pending[1], payload: {name: 'two', analysis: 'manual'}}});
  await flush();

  assert.equal(requests[3].method, 'GET');
  requests[3].resolve({...settingsCatalog(), items: pending.map(item => ({
    ...item, payload: {...item.payload, analysis: 'manual'},
  }))});
  await flush();
  assert.match(node.innerHTML, /已识别 2 张/);
  assert.match(node.innerHTML, /批量识别待补全<\/button>/);
});

test('batch analysis scans unloaded pages even when the visible page has no pending assets', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node, {view: 'expression'});
  const full = index => ({
    id: 'reaction-' + String(index).padStart(3, '0'), kind: 'reaction', revision: 1,
    enabled: true, payload: {name: 'asset ' + index, analysis: index >= 25 ? 'pending' : 'manual'},
  });
  const first = Array.from({length: 24}, (_, i) => full(i + 1));
  const second = [full(25), full(26)];
  requests[0].resolve({...settingsCatalog(), catalog: {reaction: {total: 26, pending: 2}},
    items: first, next_after: 'reaction-024'});
  await flush();
  assert.match(node.innerHTML, /批量识别待补全 \(2\)/);
  node.click('[data-semantic]', {semantic: 'analyze-pending'});
  assert.equal(requests[1].method, 'GET');
  assert.equal(requests[1].values.kind, 'reaction');
  assert.equal(requests[1].values.after, 'reaction-024');
  requests[1].resolve({...settingsCatalog(), items: second, next_after: null});
  await flush();
  assert.equal(requests[2].values.action, 'analyze');
  assert.equal(requests[2].values.id, 'reaction-025');
  requests[2].resolve({item: {...second[0], payload: {...second[0].payload, analysis: 'analyzed'}}});
  await flush();
  assert.equal(requests[3].values.id, 'reaction-026');
  requests[3].resolve({item: {...second[1], payload: {...second[1].payload, analysis: 'analyzed'}}});
  await flush();
  assert.equal(requests[4].method, 'GET');
  requests[4].resolve({...settingsCatalog(), catalog: {reaction: {total: 26, pending: 0}},
    items: first, next_after: 'reaction-024'});
  await flush();
  assert.match(node.innerHTML, /已识别 2 张/);
  assert.match(node.innerHTML, /批量识别待补全<\/button>/);
  assert.equal(requests.filter(item => item.method === 'POST').length, 2);
});

test('batch analysis aborts on failed pagination before calling any vision model', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node, {view: 'expression'});
  requests[0].resolve({...settingsCatalog(), catalog: {reaction: {total: 30, pending: 5}},
    items: [{id:'reaction-1',kind:'reaction',payload:{analysis:'pending'}}],
    next_after:'reaction-1'});
  await flush();
  node.click('[data-semantic]', {semantic:'analyze-pending'});
  assert.equal(requests[1].method,'GET');
  requests[1].reject(new Error('temporary failure'));
  await flush();
  assert.match(node.innerHTML, /剩余素材读取失败/);
  assert.equal(requests.filter(item => item.method === 'POST').length, 0);
});

test('automatic collection status is visible without exposing event or asset identifiers', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node, {view: 'expression'});
  requests[0].resolve({...settingsCatalog(),
    catalog: {reaction: {enabled: 2, disabled: 1, total: 3}},
    reaction_collection: {totals: {private_capture: {added: 4, duplicate: 2}}, recent: [
      {scope: 'private_capture', status: 'duplicate', code: null, updated_at: 1790900000,
        item_id: 'private-material-id'},
    ]},
  });
  await flush();
  assert.match(node.innerHTML, /已自动启用 4 · 重复跳过 2/);
  assert.match(node.innerHTML, /重复跳过/);
  assert.match(node.innerHTML, /可用 2 \/ 3 项/);
  assert.doesNotMatch(node.innerHTML, /private-material-id/);
});

test('learning semantic catalog loads group slang and reaction entries and saves its settings', async () => {
  const {page, requests, root} = harness(true);
  const node = root();
  page.mount(node, {view: 'learning'});
  assert.deepEqual(requests.map(request => request.values.kind), ['slang', 'reaction']);
  const slang = {...settingsCatalog(), items: [{id: 'slang-1', kind: 'slang', revision: 2,
    enabled: true, vector_ready: true, source_valid: true,
    payload: {term: '小灯', meaning: '提醒喝水', usage: '群内昵称', collection_source: 'group_capture'}}]};
  const reactions = {...settingsCatalog(), items: [{id: 'reaction-1', kind: 'reaction', revision: 3,
    enabled: false, vector_ready: false, thumbnail_url: 'data:image/jpeg;base64,AA==',
    payload: {name: '挥手', description: '挥手告别'}}]};
  requests[0].resolve(slang);
  requests[1].resolve(reactions);
  await flush();
  assert.match(node.innerHTML, /小灯/);
  assert.match(node.innerHTML, /挥手/);
  assert.match(node.innerHTML, /<img src="data:image\/jpeg;base64,AA==" alt="" loading="lazy">/);
  assert.match(node.innerHTML, /语义目录与提供方/);
  assert.match(node.innerHTML, /语义记忆与群聊黑话/);
  assert.match(node.innerHTML, /语义反应素材/);
  assert.match(node.innerHTML, /向量就绪/);
  assert.match(node.innerHTML, /semantic-learning-links/);

  node.submit('semanticSettings', {enabled: true, vision_review: false, memory_enabled: false,
    slang_enabled: true, slang_learning: false, slang_web_search_enabled: true,
    embedding_provider: 'embed-a', rerank_provider: '', analysis_provider: '',
    timeout_seconds: 12, batch_size: 32, learning_interval_seconds: 30,
    slang_web_search_candidates: 6, slang_web_search_results: 3});
  assert.equal(requests[2].method, 'POST');
  assert.equal(requests[2].values.action, 'configure');
  assert.equal(requests[2].values.changes.memory_enabled, false);
  assert.equal(requests[2].values.changes.slang_learning, false);
  assert.equal(requests[2].values.changes.embedding_provider, 'embed-a');
  assert.equal(requests[2].values.changes.timeout_seconds, 12);
  assert.equal(requests[2].values.changes.learning_interval_seconds, 1800);
  assert.equal(requests[2].values.changes.reactions_trigger_probability, 0.2,
    'settings hidden in this view remain preserved in the full configuration patch');
});

test('global learning settings read and save without a chat target', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node, {view:'learning', globalSettingsOnly:true, scopeKey:'global-semantic-settings'});
  assert.equal(requests[0].method, 'GET');
  assert.equal(requests[0].values.global, true);
  assert.equal(requests[0].values.target_id, undefined);
  assert.equal(requests[0].values.role_id, undefined);
  requests[0].resolve({...settingsCatalog(), selection_ready:false, scope_kind:'global'});
  await flush();
  assert.match(node.innerHTML, /全局语义策略/);
  assert.doesNotMatch(node.innerHTML, /semantic-learning-catalog|重建当前索引/);
  node.submit('semanticSettings', {...FORM, learning_interval_seconds:30});
  assert.equal(requests[1].method, 'POST');
  assert.equal(requests[1].values.global, true);
  assert.equal(requests[1].values.target_id, undefined);
  assert.equal(requests[1].values.role_id, undefined);
  requests[1].resolve({});
  await flush();
  assert.equal(requests[2].method, 'GET');
  assert.equal(requests[2].values.global, true);
  page.unmount();
});

test('learning semantic layout uses theme tokens and collapses cleanly on narrow screens', () => {
  const block = styles.slice(styles.indexOf('/* 学习页语义目录'));
  assert.match(block, /\.semantic-learning-settings\{display:grid;grid-template-columns:minmax\(0,1fr\) minmax\(0,1fr\)/);
  assert.match(block, /@media\(max-width:900px\)\{\.semantic-learning-settings\{grid-template-columns:minmax\(0,1fr\)\}/);
  assert.match(block, /var\(--t-surface-raised\)/);
  assert.match(block, /var\(--t-focus\)/);
  assert.doesNotMatch(block, /#[0-9a-f]{3,8}\b/i);
});
