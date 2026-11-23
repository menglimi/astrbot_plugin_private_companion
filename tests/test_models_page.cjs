const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const workbench = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/workbench-page.js'), 'utf8');
const source = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/models-page.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const TOKEN = 'a'.repeat(64);

/* An inherited task: every field follows the upstream layer. */
function task(kind, provider, capabilities = ['text'], lane = 'foreground') {
  return {
    kind, lane, capabilities, edit_token: TOKEN, application_state: 'applied',
    fields: {
      provider: {value: provider, inherited_value: provider, source: 'window', inherited_source: 'installation', explicit: false},
      fallback: {value: '', inherited_value: '', source: 'default', inherited_source: 'default', explicit: false},
      prompt: {value: '', inherited_value: '', source: 'default', inherited_source: 'default', explicit: false},
      max_output_tokens: {value: 4096, inherited_value: 4096, source: 'window', inherited_source: 'installation', explicit: false},
      timeout_seconds: {value: 60, inherited_value: 60, source: 'default', inherited_source: 'default', explicit: false},
      replacement_rules: {value: '[]', inherited_value: '[]', source: 'default', inherited_source: 'default', explicit: false},
      fallback_on_refusal: {value: false, inherited_value: false, source: 'default', inherited_source: 'default', explicit: false},
      refusal_terms: {value: '很抱歉，我无法\nrequest could not be submitted', inherited_value: '很抱歉，我无法\nrequest could not be submitted', source: 'default', inherited_source: 'default', explicit: false},
      replacement_scope: {value: 'all', inherited_value: 'all', source: 'default', inherited_source: 'default', explicit: false},
    },
  };
}

/* Base routing fields with explicit values, so a saved task still shows changes. */
function baseFields(provider) {
  return {
    provider: {value: provider, inherited_value: 'primary', source: 'window', inherited_source: 'installation', explicit: true},
    fallback: {value: '', inherited_value: '', source: 'default', inherited_source: 'default', explicit: false},
    prompt: {value: '', inherited_value: '', source: 'default', inherited_source: 'default', explicit: false},
    max_output_tokens: {value: 4096, inherited_value: 4096, source: 'window', inherited_source: 'installation', explicit: true},
    timeout_seconds: {value: 60, inherited_value: 60, source: 'default', inherited_source: 'default', explicit: false},
    replacement_rules: {value: '[]', inherited_value: '[]', source: 'window', inherited_source: 'default', explicit: false},
    fallback_on_refusal: {value: false, inherited_value: false, source: 'default', inherited_source: 'default', explicit: false},
    refusal_terms: {value: '很抱歉，我无法\nrequest could not be submitted', inherited_value: '很抱歉，我无法\nrequest could not be submitted', source: 'default', inherited_source: 'default', explicit: false},
    replacement_scope: {value: 'all', inherited_value: 'all', source: 'default', inherited_source: 'default', explicit: false},
  };
}

/* A task payload carrying the replacement fields a current V2 host returns. */
function editable(kind, provider, capabilities = ['text'], lane = 'foreground') {
  return {kind, lane, capabilities, edit_token: TOKEN, application_state: 'applied', fields: baseFields(provider)};
}

function status() {
  return {
    tasks: [
      {kind: 'conversation.reply', lane: 'foreground', capabilities: ['text'], edit_token: TOKEN, application_state: 'applied', fields: baseFields('primary')},
      {kind: 'memory.reflect', lane: 'background', capabilities: ['text'], edit_token: TOKEN, application_state: 'applied', fields: baseFields('primary')},
    ],
    providers: [
      {id: 'primary', capabilities: ['text'], max_output_tokens: 8192},
      {id: 'secondary', capabilities: ['text'], max_output_tokens: 8192},
    ],
    host_state: 'running',
  };
}

function harness() {
  const requests = [];
  const store = new Map();
  const audioBlobs = [], revokedAudioUrls = [];
  const window = {
    CompanionProduct: {
      hosted: () => true,
      role: () => ({model_api: '/astrbot_plugin_private_companion/model'}),
      identity: () => ({installation_id: 'inst', operator_id: 'operator'}),
      /* 页面取桥先问 bridgeNow()，没有再 await bridgeFor()。
         这里两者都给，保持同步路径不被打断。 */
      bridgeNow: () => window.AstrBotPluginPage,
      bridgeFor: () => ({then: resolve => resolve(window.AstrBotPluginPage)}),
    },
    AstrBotPluginPage: {
      apiGet: (route, params, options) => new Promise((resolve, reject) => {
        if (/[?#]/.test(route)) return reject(new Error('invalid plugin endpoint'));
        requests.push({method: 'GET', route, params, options, resolve, reject});
      }),
      apiPost: (route, values, options) => new Promise((resolve, reject) => requests.push({method: 'POST', route, values, options, resolve, reject})),
    },
    sessionStorage: {
      getItem: key => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => store.set(key, value),
      removeItem: key => store.delete(key),
    },
    crypto: {randomUUID: () => 'key-' + requests.length},
  };
  window.atob = atob;
  window.Blob = Blob;
  window.URL = {
    createObjectURL: blob => { audioBlobs.push(blob); return `blob:preview-${audioBlobs.length}`; },
    revokeObjectURL: url => revokedAudioUrls.push(url),
  };
  vm.runInNewContext(workbench, {window, setTimeout, clearTimeout});
  vm.runInNewContext(source, {window, crypto: window.crypto, AbortController, setTimeout, clearTimeout});
  function root() {
    const handlers = {};
    const providerPills = [];
    return {
      isConnected: true, innerHTML: '',
      addEventListener(type, handler) { handlers[type] = handler; },
      removeEventListener(type) { delete handlers[type]; },
      querySelectorAll(selector) { return selector === '[data-provider-pill]' ? providerPills : []; },
      querySelector() { return null; },
      providerPills,
      addProviderPill(provider) {
        const classes = new Set();
        const pill = {dataset: {providerPill: provider}, classList: {
          toggle(name, force) { if (force) classes.add(name); else classes.delete(name); },
          contains(name) { return classes.has(name); },
        }};
        providerPills.push(pill);
        return pill;
      },
      pointer(type, pill, related = null) {
        const wrap = item => item ? {closest: selector => selector === '[data-provider-pill]' ? item : null} : null;
        handlers[type]({type, target: wrap(pill), relatedTarget: wrap(related)});
      },
      fire(type, selector, dataset) {
        handlers[type]({type, target: {dataset: dataset || {}, value: dataset?.value, checked: dataset?.checked, valueAsNumber: dataset?.valueAsNumber,
          matches: candidate => candidate === selector, closest: candidate => (candidate === selector ? {dataset} : null)}});
      },
      click(selector, dataset) { this.fire('click', selector, dataset); },
      input(selector, dataset) { this.fire('input', selector, dataset); },
      change(selector, dataset) { this.fire('change', selector, dataset); },
    };
  }
  return {page: window.CompanionModelsPage, requests, root, store, audioBlobs, revokedAudioUrls, product: window.CompanionProduct};
  return {page: window.CompanionModelsPage, product: window.CompanionProduct,
    requests, root, store, audioBlobs, revokedAudioUrls};
}

test('embedded migration models read and save through the explicit destination endpoint', async () => {
  const h = harness(), node = h.root();
  const endpoint = '/astrbot_plugin_private_companion/host/targets/import/roles/destination/model';
  h.page.mount(node, {endpoint});
  assert.equal(h.requests[0].route, 'host/targets/import/roles/destination/model');
  h.requests[0].resolve(status());
  await flush();
  node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved, 'the destination task should be editable');
  assert.equal(h.page.isBusy(), true, 'the embedded guide must wait for an in-flight save');
  assert.equal(saved.route, 'host/targets/import/roles/destination/model/configure');
  assert.equal(h.requests.some(request => request.route === 'model' || request.route === 'model/configure'), false);
  saved.resolve({replayed: false, task: task('memory.reflect', 'secondary', ['text'], 'background')});
  await flush();
  assert.equal(h.page.isBusy(), false);
  h.page.unmount();
});

test('an invalid explicit migration model endpoint cannot fall back to another role', () => {
  for (const endpoint of ['', '/other-plugin/model', '/astrbot_plugin_private_companion/model?role=other']) {
    const h = harness(), node = h.root();
    h.page.mount(node, {endpoint});
    assert.equal(h.requests.length, 0);
    assert.match(node.innerHTML, /没有模型配置工作区/);
  }
});

test('registered review, media and authoring tasks have searchable Chinese names in every model view', async () => {
  const h = harness(), node = h.root(), data = status();
  const labels = {
    'proactive.judge': '主动联系复核',
    'conversation.image_review': '图片审核',
    'conversation.context_image_caption': '图片摘要',
    'content.authoring.draft': '内容起草',
    'conversation.image_narration': '当前图片转述',
    'conversation.video_narration': '原生视频转述',
  };
  data.tasks = Object.keys(labels).map(kind => task(kind, 'primary', ['text'], 'background'));
  data.tasks.push(task('future.optional_task', 'primary'));
  h.page.mount(node);
  h.requests[0].resolve(data);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  for (const [kind, label] of Object.entries(labels)) {
    const row = node.innerHTML.split(`data-task-row="${kind}"`)[1].split('</li>')[0];
    assert.ok(row.includes(`<b>${label}</b>`), kind);
    assert.ok(row.includes(`aria-label="选择 ${label}"`), kind);
    assert.ok(node.innerHTML.includes(`data-model-open="${kind}" aria-label="编辑 ${label}"`), kind);
  }
  assert.match(node.innerHTML, /data-model-open="future.optional_task"/);
  node.click('[data-model-open]', {modelOpen: 'proactive.judge'});
  const card = node.innerHTML.split('data-fncard="proactive.judge"')[1].split('</article>')[0];
  assert.ok(card.includes('proactive.judge'), 'retain internal task keys for diagnosis in inspector');
  node.click('[data-inspector-close]');
  node.input('[data-model-search]', {value: '主动联系复核'});
  assert.match(node.innerHTML, /data-task-row="proactive\.judge"/);
  assert.doesNotMatch(node.innerHTML, /data-task-row="conversation\.image_review"/);
  node.input('[data-model-search]', {value: ''});
  node.click('[data-model-quick-toggle]');
  for (const label of Object.values(labels)) assert.ok(node.innerHTML.includes(label), label);
  assert.doesNotMatch(node.innerHTML, /日程规划|角色状态/,
    'quick configuration covers registered tasks rather than unavailable placeholders');
  h.page.unmount();
});

test('video narration offers only native-video providers and saves the selected model', async () => {
  const h = harness(), node = h.root(), data = status();
  data.tasks.push(editable('conversation.video_narration', 'gemini-a', ['text', 'video'], 'background'));
  data.providers.push(
    {id: 'gemini-a', model: 'gemini-3.8-flash', capabilities: ['text', 'video'], max_output_tokens: 8192},
    {id: 'gemini-b', model: 'gemini-3.8-pro', capabilities: ['text', 'video'], max_output_tokens: 8192},
  );
  h.page.mount(node);
  h.requests[0].resolve(data);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.click('[data-model-open]', {modelOpen: 'conversation.video_narration'});
  const card = node.innerHTML.split('data-fncard="conversation.video_narration"')[1].split('</article>')[0];
  assert.match(card, /<option value="gemini-a" selected>gemini-a · gemini-3\.8-flash<\/option>/);
  assert.match(card, /<option value="gemini-b"[^>]*>gemini-b · gemini-3\.8-pro<\/option>/);
  assert.doesNotMatch(card, /<option value="primary"|<option value="secondary"/);
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'conversation.video_narration', value: 'gemini-b'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'conversation.video_narration'});
  const post = h.requests.find(request => request.method === 'POST');
  assert.deepEqual({...post.values.changes}, {provider: 'gemini-b'});
  post.resolve({replayed: false, task: editable('conversation.video_narration', 'gemini-b', ['text', 'video'], 'background')});
  await flush();
  h.page.unmount();
});

test('core reply uses AstrBot routing and saves only its effective extra prompt', async () => {
  const h = harness(), node = h.root(), data = status();
  const core = data.tasks[0];
  core.execution_path = 'astrbot_core';
  core.editable_fields = ['prompt'];
  core.routing_note = '由 AstrBot 当前会话选择模型。';
  h.page.mount(node);
  h.requests[0].resolve(data);
  await flush();
  node.click('[data-model-open]', {modelOpen: 'conversation.reply'});
  const card = node.innerHTML.split('data-fncard="conversation.reply"')[1].split('</article>')[0];
  assert.match(card, /AstrBot 当前会话/);
  assert.match(card, /data-model-field="prompt"/);
  assert.doesNotMatch(card, /data-model-field="provider"|data-provider-test/);
  node.change('[data-model-field]', {modelField: 'provider', modelKind: core.kind, value: 'secondary'});
  node.change('[data-model-field]', {modelField: 'prompt', modelKind: core.kind, value: '自然分段，直接聊天。'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: core.kind});
  const post = h.requests.find(request => request.method === 'POST');
  assert.deepEqual(JSON.parse(JSON.stringify(post.values.changes)), {prompt: '自然分段，直接聊天。'});
  assert.deepEqual(JSON.parse(JSON.stringify(post.values.reset)), []);
  post.resolve({replayed: false, task: core});
  await flush();
  node.click('[data-model-sub]', {modelSub: 'pol'});
  assert.match(node.innerHTML, /<option value="conversation\.reply"/);
  assert.match(node.innerHTML, /<option value="memory\.reflect"/);
  h.page.unmount();
});

test('the routing matrix lists every registered task with its provider', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  assert.equal(requests[0].route, 'model');
  assert.deepEqual(JSON.parse(JSON.stringify(requests[0].params)), {layer: 'role'});
  requests[0].resolve(status());
  await flush();
  assert.match(node.innerHTML, /任务/);
  assert.match(node.innerHTML, /对话回复/);
  assert.match(node.innerHTML, /事实整理/);
  assert.match(node.innerHTML, /2<\/b><span>可用模型/);
  assert.match(node.innerHTML, /前台/);
  assert.doesNotMatch(node.innerHTML, /保存本任务/);
});

test('loaded AstrBot providers are immediately available without a registration screen', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});

  assert.match(node.innerHTML, /<option value="secondary"/);
  assert.doesNotMatch(node.innerHTML, /模型通道|data-pool-/);
  assert.equal(requests.some(item => /\/pool(?:\/|$)/.test(item.route)), false);
});

test('a slow reply from a previous mount cannot overwrite the current matrix', async () => {
  const {page, requests, root} = harness();
  const first = root(), second = root();
  page.mount(first);
  page.unmount();
  page.mount(second);
  requests[1].resolve({tasks: [task('conversation.reply', 'primary')], providers: [{id: 'primary', capabilities: ['text'], max_output_tokens: 8192}], host_state: 'running'});
  await flush();
  requests[0].resolve(status());
  await flush();
  assert.match(second.innerHTML, /对话回复/);
  assert.doesNotMatch(second.innerHTML, /事实整理/);
});

test('switching a provider inline raises one queued change and posting saves that task only', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
  assert.match(node.innerHTML, /1<\/b> 项改动待保存/);
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  const post = requests.find(request => request.method === 'POST');
  assert.equal(post.route, 'model/configure');
  assert.deepEqual(JSON.parse(JSON.stringify(post.values)),
    {kind: 'memory.reflect', layer: 'role', operation_key: post.values.operation_key, edit_token: TOKEN, changes: {provider: 'secondary'}, reset: []});
  post.resolve({replayed: false, task: task('memory.reflect', 'secondary', ['text'], 'background')});
  await flush();
  assert.match(node.innerHTML, /已保存 1 项/);
  assert.doesNotMatch(node.innerHTML, /项改动待保存/);
});

test('an explicit fallback equal to the primary is normalized instead of blocking the save', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const data = status();
  data.tasks[1].fields.fallback = {value: 'primary', inherited_value: '', source: 'window',
    inherited_source: 'default', explicit: true};
  requests[0].resolve(data);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.change('[data-model-field]', {modelField: 'prompt', modelKind: 'memory.reflect', value: '保持简短'});
  assert.match(node.innerHTML, /项改动待保存/);
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  const post = requests.find(request => request.method === 'POST');
  assert.equal(post.values.changes.fallback, '', 'a duplicate fallback must be cleared explicitly');
  assert.equal(post.values.changes.prompt, '保持简短');
});

test('multi-select assigns one provider to several tasks and keeps capability mismatches out', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.click('[data-model-pick]', {modelPick: 'conversation.reply'});
  node.click('[data-model-pick]', {modelPick: 'memory.reflect'});
  assert.match(node.innerHTML, /已选 <strong>2<\/strong> 项任务/);
  node.change('[data-model-assign]', {value: 'secondary', matches: () => true});
  assert.match(node.innerHTML, /已把 2 项任务指派给 secondary/);
  assert.match(node.innerHTML, /2<\/b> 项改动待保存/);
});

test('restoring inheritance resets explicit fields through the reset list', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const explicit = status();
  explicit.tasks[0].fields.provider = {value: 'secondary', inherited_value: 'primary', source: 'window', inherited_source: 'installation', explicit: true};
  requests[0].resolve(explicit);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.click('[data-model-pick]', {modelPick: 'conversation.reply'});
  node.click('[data-model-action]', {modelAction: 'inherit'});
  assert.match(node.innerHTML, /已把 1 项任务改为跟随上级配置/);
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'conversation.reply'});
  const post = requests.find(request => request.method === 'POST');
  assert.deepEqual([...post.values.reset], ['provider', 'max_output_tokens']);
  assert.deepEqual({...post.values.changes}, {});
  post.resolve({replayed: false, task: task('conversation.reply', 'primary')});
  await flush();
});

test('the precise editor keeps provider filtering compact and localizes capability labels', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  assert.match(node.innerHTML, /class="model-mlgd-box"/);
  assert.doesNotMatch(node.innerHTML, /class="model-mlgd-box" open/);
  assert.match(node.innerHTML, /data-model-provider-filter/);
  node.click('[data-model-open]', {modelOpen: 'conversation.reply'});
  assert.match(node.innerHTML, /主模型提供方/);
  assert.match(node.innerHTML, /class="model-rl"><span>文本生成<\/span>/);
  assert.doesNotMatch(node.innerHTML, /class="model-rl"><span>text<\/span>/);
  node.click('[data-inspector-close]');
  node.change('[data-model-provider-filter]', {value: 'secondary'});
  assert.match(node.innerHTML, /<option value="secondary" selected>/);
  assert.match(node.innerHTML, /没有匹配的任务/);
  node.change('[data-model-provider-filter]', {value: ''});
  assert.match(node.innerHTML, /data-task-row="conversation.reply"/);
  node.click('[data-model-view]', {modelView: 'providers'});
  assert.match(node.innerHTML, /2 \/ 2 项任务走这里/);
  assert.match(node.innerHTML, /0 \/ 2 项任务走这里/);
  assert.match(node.innerHTML, /还有 2 项能力相符的任务由其他模型提供方承担/);
});

test('an unreadable host offers a retry that loads the matrix', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].reject(Object.assign(new Error('read failed'), {code: 'staging_not_initialized'}));
  await flush();
  assert.match(node.innerHTML, /角色数据尚未就绪/);
  node.click('[data-model-action]', {modelAction: 'refresh'});
  requests[1].resolve(status());
  await flush();
  assert.match(node.innerHTML, /对话回复/);
  assert.doesNotMatch(node.innerHTML, /角色数据尚未就绪/);
});

test('a real dashboard bridge rejects with the code inside Error.message', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].reject(new Error('staging_not_initialized'));
  await flush();
  assert.match(node.innerHTML, /角色数据尚未就绪/);
  assert.doesNotMatch(node.innerHTML, /无法读取任务配置，请重新读取状态/);
});

test('provider search narrows tasks and a routed task opens its editor', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-view]', {modelView: 'providers'});
  node.input('[data-model-search]', {value: '事实'});
  assert.match(node.innerHTML, /事实整理/);
  assert.doesNotMatch(node.innerHTML, /对话回复/);
  node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
  assert.match(node.innerHTML, /data-model-view="tasks" aria-pressed="true"/);
  assert.match(node.innerHTML, /model-fncard open/);
  assert.match(node.innerHTML, /任务附加提示词/);
  node.click('[data-model-sub]', {modelSub: 'pol'});
  assert.match(node.innerHTML, /data-model-pol-task/);
  assert.doesNotMatch(node.innerHTML, /\bProvider\b|MODEL ROUTING/);
});

test('a lower-capacity provider adjusts an explicit draft output limit before saving', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const constrained = status();
  constrained.providers[1].max_output_tokens = 2048;
  constrained.tasks[1].fields.max_output_tokens = {value: 8192, inherited_value: 8192,
    source: 'window', inherited_source: 'installation', explicit: true};
  requests[0].resolve(constrained);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
  assert.match(node.innerHTML, /输出上限已调整为 2048/);
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  const post = requests.find(request => request.method === 'POST');
  assert.deepEqual({...post.values.changes}, {provider: 'secondary', max_output_tokens: 2048});
  const saved = task('memory.reflect', 'secondary', ['text'], 'background');
  saved.fields.max_output_tokens = {...saved.fields.max_output_tokens, value: 2048, explicit: true};
  post.resolve({replayed: false, task: saved});
  await flush();
  assert.match(node.innerHTML, /已保存 1 项/);
});

/* 默认值提高后，选中低上限 Provider 是常事：继承的默认值必须继续跟随，
   只提示「发送时按上限生效」，绝不能被写成显式值钉死。 */
test('a lower-capacity provider never pins an inherited default output limit', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const data = status();
  data.providers[1].max_output_tokens = 2048;
  data.tasks[1].fields.max_output_tokens = {value: 8192, inherited_value: 8192,
    source: 'default', inherited_source: 'default', explicit: false};
  requests[0].resolve(data);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
  assert.doesNotMatch(node.innerHTML, /输出上限已调整为/);
  assert.match(node.innerHTML, /继承的默认输出上限 8192 高于所选模型提供方的 2048，发送时会按 2048 生效/);
  const card = node.innerHTML.split('data-fncard="memory.reflect"')[1].split('</article>')[0];
  assert.match(card, /data-field-state="max_output_tokens" data-inherited="true"/);
  assert.doesNotMatch(card, /data-model-inherit-reset="max_output_tokens"/);
  assert.match(card, /data-model-field="max_output_tokens"[^>]*value="8192"/);
  assert.doesNotMatch(card, /value="2048"/);
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  const post = requests.find(request => request.method === 'POST');
  assert.deepEqual({...post.values.changes}, {provider: 'secondary'});
  assert.deepEqual([...post.values.reset], []);
  post.resolve({replayed: false, task: task('memory.reflect', 'secondary', ['text'], 'background')});
  await flush();
  assert.match(node.innerHTML, /已保存 1 项/);
});

/* 值来自上级显式配置（安装级保存、页面在角色层编辑）时，路由会按显式值拒绝；
   页面只提示，不偷偷改成显式。 */
test('an output limit saved upstream warns instead of being rewritten', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const data = status();
  data.providers[1].max_output_tokens = 2048;
  data.tasks[1].fields.max_output_tokens = {value: 8192, inherited_value: 8192,
    source: 'installation', inherited_source: 'installation', explicit: false};
  requests[0].resolve(data);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
  assert.match(node.innerHTML, /跟随上级不会被改写/);
  const card = node.innerHTML.split('data-fncard="memory.reflect"')[1].split('</article>')[0];
  assert.match(card, /data-field-state="max_output_tokens" data-inherited="true"/);
  assert.match(card, /value="8192"/);
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  assert.equal(requests.some(request => request.method === 'POST'), false);
  assert.match(node.innerHTML, /继承的输出上限 8192 超过所选模型提供方的 2048，请显式设置上限或改回上级/);
});

/* 页面与路由共用同一条上限：替换规则的目标 Provider 也算在内。 */
test('the editor ceiling follows replacement-rule targets like routing does', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const data = status();
  data.providers.push({id: 'small', capabilities: ['text'], max_output_tokens: 1024});
  data.tasks[1].fields.max_output_tokens = {value: 8192, inherited_value: 8192,
    source: 'default', inherited_source: 'default', explicit: false};
  data.tasks[1].fields.replacement_rules = {value: JSON.stringify([{id: 'small-rule', provider_id: 'small',
    keywords: ['私有']}]), inherited_value: '[]', source: 'window', inherited_source: 'default', explicit: true};
  requests[0].resolve(data);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
  assert.match(node.innerHTML, /发送时会按 1024 生效/);
});

/* 回复任务的上限是插件全局「最大输出 Token」（加地板）算出来的，不是 Provider
   自报的能力。提示必须指向真正能改的那一项，否则用户会去模型提供方设置里找一
   个根本不存在的上限。 */
test('a plugin-global output ceiling points the hint at the plugin setting', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const data = status();
  data.providers[0] = {id: 'primary', capabilities: ['text'], max_output_tokens: 8192,
    max_output_tokens_basis: 'plugin_global', plugin_global_max_output_tokens: 8192};
  data.tasks[1].fields.max_output_tokens = {value: 16384, inherited_value: 16384,
    source: 'window', inherited_source: 'installation', explicit: true};
  requests[0].resolve(data);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
  const card = node.innerHTML.split('data-fncard="memory.reflect"')[1].split('</article>')[0];
  assert.match(card, /输出上限 16384 超过插件全局『最大输出 Token』（当前 8192）。/);
  assert.match(card, /请先把「配置 → 插件 → 我会永远陪着你 → 最大输出 Token」调高，或把本任务上限调到 8192。/);
  assert.doesNotMatch(card, /所选模型提供方/);
  assert.doesNotMatch(card, /该任务至少需要/);
  assert.match(card, /max="8192" step="1" data-model-field="max_output_tokens"/);
});

test('a host-provider output ceiling keeps the original wording', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const data = status();
  data.providers[1] = {id: 'secondary', capabilities: ['text'], max_output_tokens: 2048,
    max_output_tokens_basis: 'host_provider', plugin_global_max_output_tokens: 65536};
  data.tasks[1].fields.provider = {value: 'secondary', inherited_value: 'primary',
    source: 'window', inherited_source: 'installation', explicit: true};
  data.tasks[1].fields.max_output_tokens = {value: 8192, inherited_value: 4096,
    source: 'window', inherited_source: 'installation', explicit: true};
  requests[0].resolve(data);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
  const card = node.innerHTML.split('data-fncard="memory.reflect"')[1].split('</article>')[0];
  assert.match(card, /输出上限 8192 超过所选模型提供方的 2048，保存会被拒绝。/);
  assert.doesNotMatch(card, /插件全局/);
});

/* 全局值低于结构任务的下限时插件按地板上报；不解释一句，界面上的 8192 就像
   凭空冒出来的。 */
test('a plugin-global ceiling below the task floor explains the floor', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const data = status();
  data.providers[0] = {id: 'primary', capabilities: ['text'], max_output_tokens: 8192,
    max_output_tokens_basis: 'plugin_global', plugin_global_max_output_tokens: 2048, floor_applied: 8192};
  data.tasks[1].fields.max_output_tokens = {value: 16384, inherited_value: 16384,
    source: 'window', inherited_source: 'installation', explicit: true};
  requests[0].resolve(data);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
  const card = node.innerHTML.split('data-fncard="memory.reflect"')[1].split('</article>')[0];
  assert.match(card, /插件全局『最大输出 Token』（当前 2048）/);
  assert.match(card, /该任务至少需要 8192，插件按 8192 上报。/);
});

test('a limit inside the ceiling shows no hint', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const data = status();
  data.providers[0] = {id: 'primary', capabilities: ['text'], max_output_tokens: 8192,
    max_output_tokens_basis: 'plugin_global', plugin_global_max_output_tokens: 2048, floor_applied: 8192};
  requests[0].resolve(data);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
  const card = node.innerHTML.split('data-fncard="memory.reflect"')[1].split('</article>')[0];
  assert.doesNotMatch(card, /输出上限 4096 超过/);
  assert.doesNotMatch(card, /插件全局/);
});

test('typing a prompt keeps the editor mounted until the field changes', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
  const before = node.innerHTML;
  node.input('[data-model-field]', {modelField: 'prompt', modelKind: 'memory.reflect', value: '保留当次语境'});
  assert.equal(node.innerHTML, before);
  node.change('[data-model-field]', {modelField: 'prompt', modelKind: 'memory.reflect', value: '保留当次语境'});
  assert.match(node.innerHTML, /1<\/b> 项改动待保存/);
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  const post = requests.find(request => request.method === 'POST');
  assert.equal(post.values.changes.prompt, '保留当次语境');
  post.resolve({replayed: false, task: task('memory.reflect', 'primary', ['text'], 'background')});
  await flush();
});

test('an uncertain save keeps its original operation and blocks further edits until retry', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  const first = requests.find(request => request.method === 'POST');
  node.click('[data-model-action]', {modelAction: 'inherit'});
  assert.equal(requests.filter(request => request.method === 'POST').length, 1);
  first.reject(new Error('network interrupted'));
  await flush();
  node.click('[data-model-action]', {modelAction: 'refresh'});
  const latest = status();
  latest.tasks[1].edit_token = 'b'.repeat(64);
  latest.tasks[1].fields.prompt.value = 'new remote prompt';
  requests.at(-1).resolve(latest);
  await flush();
  assert.equal(requests.filter(request => request.method === 'POST').length, 1,
    'reading fresh status must not replay an uncertain write');
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'primary'});
  node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
  assert.match(node.innerHTML, /上次保存结果未确认/);
  assert.match(node.innerHTML, /1<\/b> 项保存结果待核对/);
  assert.match(node.innerHTML, /data-model-action="save-all"[^>]*>核对保存/);
  const uncertainCard = node.innerHTML.split('data-fncard="memory.reflect"')[1].split('</article>')[0];
  assert.match(uncertainCard, /data-model-action="save-one"[^>]*>核对保存/);
  assert.doesNotMatch(uncertainCard, /data-model-action="save-one"[^>]*\sdisabled(?:\s|>)/);
  assert.match(uncertainCard, /data-model-field="provider"[^>]*disabled/);
  assert.doesNotMatch(uncertainCard, /data-model-action="retry"/);
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  const second = requests.filter(request => request.method === 'POST')[1];
  assert.deepEqual(JSON.parse(JSON.stringify(second.values)), JSON.parse(JSON.stringify(first.values)));
  second.resolve({replayed: true, task: task('memory.reflect', 'secondary', ['text'], 'background')});
  await flush();
  assert.match(node.innerHTML, /原保存已确认/);
  assert.doesNotMatch(node.innerHTML, /上次保存结果未确认/);
});

test('a bridge timeout automatically re-sends the same idempotent save once', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  const first = requests.find(request => request.method === 'POST');
  first.reject(Object.assign(new Error('bridge_timeout'), {code: 'bridge_timeout'}));
  await flush();
  const posts = requests.filter(request => request.method === 'POST');
  assert.equal(posts.length, 2, 'one timed-out save should be reconciled automatically');
  assert.equal(posts[1].values.operation_key, first.values.operation_key,
    'the retry must reuse the idempotency key');
  posts[1].resolve({replayed: true, task: task('memory.reflect', 'secondary', ['text'], 'background')});
  await flush();
  assert.match(node.innerHTML, /原保存已确认/);
  assert.equal(page.isBusy(), false);
  assert.doesNotMatch(node.innerHTML, /项保存结果待核对/);
  page.unmount();
});
test('switching to the voice tab lazily loads, saves, and plays a synthesized sample result', async () => {
  const {page, requests, root, audioBlobs, revokedAudioUrls} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'voice'});
  const get = requests.find(r => r.method === 'GET' && r.route === 'model/voice');
  assert.ok(get, 'voice tab should trigger a GET to model/voice');
  const voice = {languages: [{k: 'zh', n: '中文', bind: ''}, {k: 'ja', n: '日语', bind: ''}, {k: 'en', n: '英语', bind: ''}],
    channels: [{k: 'private', n: '私聊', bindings: {zh: '', ja: '', en: ''}},
      {k: 'group', n: '群聊', bindings: {zh: '', ja: '', en: ''}}],
    providers: [{id: 'tts-live', model: 's2.1-pro-free', voice: '示例音色甲'}], timeout_seconds: 30,
    voice_enabled: true, edit_token: TOKEN};
  get.resolve(voice);
  await flush();
  assert.match(node.innerHTML, /语音模型/);
  assert.match(node.innerHTML, /按语种配置语音合成/);
  assert.match(node.innerHTML, /私聊 \/ 群聊 TTS 总览/);
  assert.match(node.innerHTML, /data-voice-matrix-lang="zh"/);
  node.change('[data-voice-provider]', {value: 'tts-live'});
  node.change('[data-voice-channel]', {voiceChannel: 'private', value: 'tts-live'});
  node.change('[data-voice-channel]', {voiceChannel: 'group', voiceMatrixLang: 'en', value: 'tts-live'});
  node.click('[data-voice-save]');
  const post = requests.find(r => r.method === 'POST' && r.route === 'model/voice/save');
  assert.ok(post, 'voice save should POST to model/voice/save');
  assert.equal(post.values.changes.voice_ja_provider_id, 'tts-live');
  assert.equal(post.values.changes.voice_private_ja_provider_id, 'tts-live');
  assert.equal(post.values.changes.voice_group_en_provider_id, 'tts-live');
  post.resolve({...voice, languages: voice.languages.map(item => item.k === 'ja' ? {...item, bind: 'tts-live'} : item)});
  await flush();
  assert.match(node.innerHTML, /语音配置已保存/);
  node.click('[data-ve-test]');
  const testRequest = requests.find(r => r.route === 'model/voice/test');
  assert.deepEqual({...testRequest.values}, {provider_id: 'tts-live', language: 'ja'});
  testRequest.resolve({status: 'ok', data: {provider_id: 'tts-live', status: 'ok', latency_ms: 25, code: '',
    audio_base64: Buffer.from('RIFF0000WAVE').toString('base64'), audio_mime_type: 'audio/wav'}});
  await flush();
  assert.match(node.innerHTML, /语音合成成功/);
  assert.match(node.innerHTML, /本次结果 · 试听/);
  assert.match(node.innerHTML, /<audio class="model-test-audio" controls preload="none" src="blob:preview-1"/);
  assert.equal(audioBlobs[0].type, 'audio/wav');
  node.click('[data-voice-lang]', {voiceLang: 'en'});
  assert.doesNotMatch(node.innerHTML, /blob:preview-1/);
  assert.deepEqual(revokedAudioUrls, ['blob:preview-1']);
});

test('a busy host preserves the voice draft and leaves save available', async () => {
  const h = harness(), node = h.root();
  h.page.mount(node);
  h.requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'voice'});
  const voice = {languages: [{k: 'ja', n: '日语', bind: ''}], providers: [],
    timeout_seconds: 30, voice_enabled: true, edit_token: TOKEN};
  h.requests.find(r => r.route === 'model/voice').resolve(voice);
  await flush();
  node.change('[data-voice-timeout]', {value: '45', valueAsNumber: 45});
  node.click('[data-voice-save]');
  h.requests.at(-1).resolve({status: 'error', message: 'host_busy', data: {code: 'host_busy'}});
  await flush();
  assert.match(node.innerHTML, /本次没有写入，草稿已保留/);
  assert.doesNotMatch(node.innerHTML, /data-voice-retry/);
  assert.match(node.innerHTML, /data-voice-timeout value="45"/);
  assert.doesNotMatch(node.innerHTML, /data-voice-save[^>]*disabled/);
  node.click('[data-voice-save]');
  const retry = h.requests.at(-1);
  assert.equal(retry.values.changes.voice_timeout_seconds, 45);
  retry.resolve({...voice, timeout_seconds: 45});
  await flush();
  assert.equal(h.page.isDirty(), false);
  h.page.unmount();
});

test('a timed-out voice save reconciles with the same operation and retains uncertainty if the host is busy', async () => {
  const h = harness(), node = h.root();
  h.page.mount(node);
  h.requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'voice'});
  const voice = {languages: [{k: 'ja', n: '日语', bind: ''}], providers: [],
    timeout_seconds: 30, voice_enabled: true, edit_token: TOKEN};
  h.requests.find(r => r.route === 'model/voice').resolve(voice);
  await flush();
  node.change('[data-voice-timeout]', {value: '45', valueAsNumber: 45});
  node.click('[data-voice-save]');
  const original = h.requests.at(-1);
  original.reject(Object.assign(new Error('bridge_timeout'), {code: 'bridge_timeout'}));
  await flush();
  const automatic = h.requests.at(-1);
  assert.notEqual(automatic, original);
  assert.equal(automatic.values.operation_key, original.values.operation_key);
  automatic.resolve({status: 'error', message: 'host_busy', data: {code: 'host_busy'}});
  await flush();
  assert.match(node.innerHTML, /原保存结果仍待核对/);
  assert.match(node.innerHTML, /data-voice-retry/);
  node.click('[data-voice-retry]');
  const reconciliation = h.requests.at(-1);
  assert.deepEqual(reconciliation.values, original.values);
  reconciliation.resolve({...voice, timeout_seconds: 45, replayed: true});
  await flush();
  assert.match(node.innerHTML, /原保存已确认/);
  assert.doesNotMatch(node.innerHTML, /data-voice-retry/);
  assert.equal(h.page.isDirty(), false);
  h.page.unmount();
});

test('a dashboard bridge inner voice result with status ok is kept as the result', async () => {
  const {page, requests, root, audioBlobs} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'voice'});
  requests.find(r => r.route === 'model/voice').resolve({
    languages: [{k: 'zh', n: '中文', bind: ''}, {k: 'ja', n: '日语', bind: 'tts-live'}, {k: 'en', n: '英语', bind: ''}],
    providers: [{id: 'tts-live'}], timeout_seconds: 30, voice_enabled: true, edit_token: TOKEN,
  });
  await flush();
  node.click('[data-ve-test]');
  const request = requests.find(r => r.route === 'model/voice/test');
  request.resolve({status: 'ok', provider_id: 'tts-live', latency_ms: 17, code: '', stage: 'audio_read',
    audio_base64: Buffer.from('RIFF0000WAVE').toString('base64'), audio_mime_type: 'audio/wav'});
  await flush();
  assert.match(node.innerHTML, /语音合成成功/);
  assert.match(node.innerHTML, /本次结果 · 试听/);
  assert.equal(audioBlobs[0].type, 'audio/wav');
  assert.doesNotMatch(node.innerHTML, /invalid_response/);
});

test('a direct bridge voice diagnostic keeps its stage and safe error text', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'voice'});
  requests.find(r => r.route === 'model/voice').resolve({
    languages: [{k: 'zh', n: '中文', bind: ''}, {k: 'ja', n: '日语', bind: 'tts-error'}, {k: 'en', n: '英语', bind: ''}],
    providers: [{id: 'tts-error'}], timeout_seconds: 30, voice_enabled: true, edit_token: TOKEN,
  });
  await flush();
  node.click('[data-ve-test]');
  requests.find(r => r.route === 'model/voice/test').resolve({status: 'unknown', provider_id: 'tts-error',
    code: 'model_voice_probe_failed', stage: 'synthesis', exception_type: 'RuntimeError',
    error: 'Fish Audio API request failed: status 400', suggestion: '核对 API 地址、密钥和模型参数。'});
  await flush();
  assert.match(node.innerHTML, /阶段：调用 Provider 合成/);
  assert.match(node.innerHTML, /错误：Fish Audio API request failed: status 400/);
  assert.doesNotMatch(node.innerHTML, /invalid_response/);
});

test('a voice bridge rejection keeps the machine code instead of becoming invalid_response', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'voice'});
  requests.find(r => r.route === 'model/voice').resolve({
    languages: [{k: 'zh', n: '中文', bind: ''}, {k: 'ja', n: '日语', bind: 'tts-error'}, {k: 'en', n: '英语', bind: ''}],
    providers: [{id: 'tts-error'}], timeout_seconds: 30, voice_enabled: true, edit_token: TOKEN,
  });
  await flush();
  node.click('[data-ve-test]');
  requests.find(r => r.route === 'model/voice/test').reject(new Error('model_voice_probe_failed'));
  await flush();
  assert.match(node.innerHTML, /语音合成未返回可确认结果/);
  assert.match(node.innerHTML, /阶段：请求/);
  assert.doesNotMatch(node.innerHTML, /invalid_response/);
});

test('voice synthesis failures show the provider stage, full safe error, and suggestion', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'voice'});
  const get = requests.find(r => r.method === 'GET' && r.route === 'model/voice');
  get.resolve({languages: [{k: 'zh', n: '中文', bind: ''}, {k: 'ja', n: '日语', bind: 'tts-error'}, {k: 'en', n: '英语', bind: ''}],
    providers: [{id: 'tts-error'}], timeout_seconds: 30, voice_enabled: true, edit_token: TOKEN});
  await flush();
  node.click('[data-ve-test]');
  const testRequest = requests.find(r => r.route === 'model/voice/test');
  testRequest.resolve({status: 'ok', data: {provider_id: 'tts-error', status: 'unknown', latency_ms: 4033,
    code: 'model_voice_probe_failed', stage: 'synthesis', exception_type: 'RuntimeError',
    error: 'Fish Audio API request failed: status 400', suggestion: '核对 API 地址、密钥和模型参数。', request_id: 'abc123def456'}});
  await flush();
  assert.match(node.innerHTML, /语音合成未返回可确认结果/);
  assert.match(node.innerHTML, /阶段：调用 Provider 合成/);
  assert.match(node.innerHTML, /错误：Fish Audio API request failed: status 400/);
  assert.match(node.innerHTML, /建议：核对 API 地址、密钥和模型参数/);
  assert.match(node.innerHTML, /诊断号：abc123def456/);
});

test('TTS provider credentials remain visible as a masked draft through redraw and save', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'voice'});
  const voice = {languages: [{k: 'zh', n: '中文', bind: ''}], providers: [], timeout_seconds: 30,
    voice_enabled: true, edit_token: TOKEN};
  requests.find(r => r.route === 'model/voice').resolve(voice);
  await flush();
  node.click('[data-voice-editor]');
  const catalog = requests.find(r => r.route === 'model/tts/providers');
  catalog.resolve({templates: [], items: [{id: 'tts-live', type: 'openai', label: 'OpenAI', enable: false,
    loaded: false, fields: [{key: 'api_key', type: 'string', label: 'API Key', secret: true, set: false, options: []}]}]});
  await flush();

  node.change('[data-tts-field]', {ttsField: 'api_key', value: 'new-tts-key'});
  assert.match(node.innerHTML, /type="password" data-tts-field="api_key" value="new-tts-key"/);
  node.click('[data-tts-save]');
  const post = requests.find(r => r.route === 'model/tts/providers/update');
  assert.equal(post.values.values.api_key, 'new-tts-key');
  assert.match(node.innerHTML, /type="password" data-tts-field="api_key" value="new-tts-key"/,
    'the save-in-progress redraw must keep the masked draft in the form');
  post.resolve({templates: [], items: [{id: 'tts-live', type: 'openai', label: 'OpenAI', enable: false,
    loaded: false, fields: [{key: 'api_key', type: 'string', label: 'API Key', secret: true, set: true, options: []}]}]});
  await flush();
  assert.match(node.innerHTML, /已配置 · 留空表示保留/);
  assert.match(node.innerHTML, /type="password" data-tts-field="api_key" value=""/,
    'saved credentials must still be redacted after the response');
});

test('a provider shared across languages warns and can unbind the others before saving', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'voice'});
  const get = requests.find(r => r.method === 'GET' && r.route === 'model/voice');
  const voice = {languages: [{k: 'zh', n: '中文', bind: 'tts-live'}, {k: 'ja', n: '日语', bind: 'tts-live'}, {k: 'en', n: '英语', bind: ''}],
    providers: [{id: 'tts-live'}], timeout_seconds: 30, voice_enabled: true, edit_token: TOKEN};
  get.resolve(voice);
  await flush();
  assert.match(node.innerHTML, /同时绑定到中文/);
  node.click('[data-voice-unbind]');
  assert.match(node.innerHTML, /已解除其他语种的同一服务绑定/);
  node.click('[data-voice-save]');
  const post = requests.find(r => r.method === 'POST' && r.route === 'model/voice/save');
  assert.equal(post.values.changes.voice_zh_provider_id, '');
  assert.ok(!Object.hasOwn(post.values.changes, 'voice_ja_provider_id'), 'untouched language must not be re-sent');
});

test('image page separates generation and editing model queues and saves parameters', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'image'});
  const get = requests.find(r => r.method === 'GET' && r.route === 'model/image');
  assert.ok(get, 'image tab should trigger a GET to model/image');
  const schemaRequest = requests.find(r => r.method === 'GET' && r.route.endsWith('/image-schema'));
  assert.ok(schemaRequest, 'the image tab loads its backend field schema from the selected model route');
  assert.match(schemaRequest.route, /^model\/image-schema$/);
  schemaRequest.resolve({image_provider_group: {sd_webui: {label: 'SD WebUI', fields: {
    endpoint: {kind: 'text', group: 'connection', label: '服务地址'},
    model: {kind: 'text', group: 'connection', label: '模型名'},
    steps: {kind: 'integer', group: 'sampling', storage: 'parameters', label: '采样步数', default: 28, min: 1, max: 150},
    cfg_scale: {kind: 'number', group: 'sampling', storage: 'parameters', label: 'CFG', default: 7, min: 1, max: 30, step: 0.1},
    sampler_name: {kind: 'text', group: 'sampling', storage: 'parameters', label: '采样器'},
  }}}});
  const image = {actions: [
    {action: 'image.generate', enabled: true, owner: 'image-plugin', provider_id: 'native-image', loaded: true, current: true, registered: true, state: 'ready'},
    {action: 'image.edit', enabled: true, owner: 'image-plugin', provider_id: 'native-image', loaded: true, current: true, registered: true, state: 'ready'},
    {action: 'image.mask_edit', enabled: true, owner: 'image-plugin', provider_id: 'native-image', loaded: true, current: false, registered: false, state: 'unsupported', diagnostic_code: 'native_image_operation_unavailable'},
  ], enabled: {'image.generate': true, 'image.edit': true, 'image.mask_edit': true}, authorization: 'checked_for_each_invocation',
    module: {state: 'running'}};
  get.resolve(image);
  await flush();
  node.click('[data-img-view]', {imgView: 'edit'});
  assert.match(node.innerHTML, /当前队列未提供/);
  assert.match(node.innerHTML, /当前生图模型队列未提供此操作/);
  assert.doesNotMatch(node.innerHTML, /Image 模块运行中：/);
  assert.doesNotMatch(node.innerHTML, /Image 能力声明已失效/);
  const maskRow = node.innerHTML.split('<strong>蒙版局部改图</strong>')[1].split('</li>')[0];
  assert.doesNotMatch(maskRow, /声明一致|已注册/);
  node.click('[data-img-view]', {imgView: 'generate'});

  const configRequest = requests.find(r => r.method === 'GET' && r.route.endsWith('/image/output'));
  assert.ok(configRequest, 'the model page reads the configuration used by the image backend');
  const config = {revision: 'image-r1', limits: {max_endpoints: 24}, settings: {
    enabled: true, backend: 'auto',
    endpoints: [
      {id: 'primary', name: '主用模型', enabled: true, platform: 'openai', model: 'image-v1', width: 1024, height: 1024,
        base_url: 'https://api.example.test/v1', timeout_seconds: 180, api_key_set: true},
      {id: 'secondary', name: '备用模型', enabled: true, platform: 'openai', model: 'image-v2', width: 1024, height: 1024,
        base_url: 'https://backup.example.test/v1', timeout_seconds: 180, api_key_set: false, max_reference_images: 0},
    ],
    sd_webui: {base_url: 'http://127.0.0.1:7860', timeout_seconds: 300, parameters: {}},
    comfyui: {base_url: 'http://127.0.0.1:8188', timeout_seconds: 300, workflows: {
      generate: {workflow: {'1': {class_type: 'SaveImage', inputs: {}}}, mapping: {fields: {}, output_node: '1'}},
    }},
    legacy_options: {image_companion_selected_workflows: {generate: 'old-text.json'},
      image_companion_extra_workflows: ['old-insert.json']},
    operation_routes: {generate: ['online:primary', 'comfyui', 'sd_webui'], edit: ['online:secondary', 'online:primary']},
  }};
  configRequest.resolve(config);
  await flush();
  assert.match(node.innerHTML, /<details class="model-img-route-details" open><summary data-img-route-expand="online:primary"/);
  node.click('[data-img-route-expand]', {imgRouteExpand: 'online:primary'});
  assert.match(node.innerHTML, /<details class="model-img-route-details"><summary data-img-route-expand="online:primary"/);
  node.click('[data-img-route-expand]', {imgRouteExpand: 'online:primary'});
  assert.match(node.innerHTML, /文生图与改图/);
  assert.match(node.innerHTML, /model-img-route-meta/);
  assert.match(node.innerHTML, /model-img-route-toggle/);
  assert.match(node.innerHTML, /展开配置|收起配置/);
  assert.match(node.innerHTML, /data-img-endpoint-test="primary"/,
    'saved enabled online models expose a real generation test');
  assert.equal((node.innerHTML.match(/class="model-imgconfig"/g) || []).length, 1,
    'the model editor stays inside one framed section');
  assert.match(node.innerHTML, /data-img-view="generate"/);
  assert.match(node.innerHTML, /data-img-view="edit"/);
  assert.match(node.innerHTML, /class="model-img-route-name" data-img-config-field="endpoints\.0\.name" value="主用模型"/);
  assert.match(node.innerHTML, /aria-label="自定义模型名称"/);
  assert.doesNotMatch(node.innerHTML, /端点名称/);
  assert.match(node.innerHTML, /data-img-config-field="endpoints\.0\.model"/);
  assert.match(node.innerHTML, /data-img-config-field="endpoints\.0\.quality"/);
  assert.match(node.innerHTML, /data-img-config-field="endpoints\.0\.max_reference_images"/);
  assert.match(node.innerHTML, /data-img-config-field="sd_webui\.parameters\.generate\.steps"/,
    'SD sampling controls are rendered from the loaded schema');
  assert.doesNotMatch(node.innerHTML, /data-img-config-field="sd_webui\.parameters\.generate\.(endpoint|model)"/,
    'connection metadata must not be written into the SD sampling object');
  assert.match(node.innerHTML, /<legend>连接<\/legend>/);
  assert.match(node.innerHTML, /<legend>工作流<\/legend>/);
  assert.match(node.innerHTML, /已绑定 · 1 个节点/);
  assert.match(node.innerHTML, /工作流与网络选项/);
  assert.match(node.innerHTML, /old-text\.json/);
  assert.match(node.innerHTML, /data-img-config-json="sd\.generate"/);
  assert.doesNotMatch(node.innerHTML, /data-img-view="mask_edit"/,
    'masked editing belongs to the edit workflow, not a third navigation item');

  node.click('[data-img-endpoint-test]', {imgEndpointTest: 'primary'});
  const outputTest = requests.find(r => r.method === 'POST' && r.route.endsWith('/image/output/test'));
  assert.ok(outputTest, 'the button calls the saved endpoint generation test');
  assert.equal(outputTest.values.endpoint_id, 'primary');
  assert.equal(typeof outputTest.values.operation_key, 'string');
  assert.ok(outputTest.values.operation_key.length > 0);
  outputTest.resolve({status: 'succeeded', elapsed_ms: 1250, error: ''});
  await flush();
  assert.match(node.innerHTML, /测试成功 · 1250 ms/);

  node.click('[data-img-view]', {imgView: 'edit'});
  assert.match(node.innerHTML, /普通改图与蒙版局部改图共用模型队列/);
  assert.match(node.innerHTML, /未启用参考图改图/);
  assert.match(node.innerHTML, /image\.edit/);
  assert.match(node.innerHTML, /image\.mask_edit/);
  assert.match(node.innerHTML, /改图模型与优先级/);
  node.click('[data-img-route-move]', {imgRouteMove: 'up', imgRouteIndex: '1'});
  node.input('[data-img-config-field]', {imgConfigField: 'endpoints.0.name', value: '  主力生图  '});
  node.change('[data-img-config-field]', {imgConfigField: 'endpoints.0.model', value: 'image-v3'});
  node.change('[data-img-config-field]', {imgConfigField: 'endpoints.0.quality', value: 'hd'});
  node.change('[data-img-config-field]', {imgConfigField: 'endpoints.0.max_reference_images', value: 4});
  node.click('[data-img-view]', {imgView: 'generate'});
  node.change('[data-img-config-field]', {imgConfigField: 'sd_webui.base_url', value: 'http://127.0.0.1:7861'});
  node.change('[data-img-config-field]', {imgConfigField: 'sd_webui.parameters.generate.steps', value: 32});
  node.change('[data-img-config-field]', {imgConfigField: 'sd_webui.parameters.generate.cfg_scale', value: 6.5});
  node.input('[data-img-config-json]', {imgConfigJson: 'sd.generate', value: '{"steps":24,"cfg_scale":6.5,"sampler_name":"Euler"}'});
  assert.equal(page.isDirty(), true);
  const unsavedFields = Array.from(page.changedFields());
  assert.ok(unsavedFields.includes('生图模型 · 调用顺序 · 改图'));
  assert.ok(unsavedFields.includes('生图模型 · 主力生图 · 模型 ID'));
  assert.ok(unsavedFields.includes('生图模型 · 主力生图 · 自定义名称'));
  assert.ok(unsavedFields.includes('生图模型 · SD WebUI · 生图参数 · 文生图 · 采样步数'));
  assert.doesNotMatch(unsavedFields.join('\n'), /image-v3|127\.0\.0\.1|Euler/,
    'the leave prompt lists field labels without exposing configuration values');
  const leaveSave = page.saveBeforeLeave();
  const configSave = requests.find(r => r.method === 'POST' && r.route.endsWith('/image/output/save'));
  assert.ok(configSave, 'saving model configuration uses the native image configuration API');
  assert.deepEqual(JSON.parse(JSON.stringify(configSave.values.changes.operation_routes.edit)),
    ['online:primary', 'online:secondary'], 'editing priority is independent and reorderable');
  assert.equal(configSave.values.changes.endpoints[0].name, '主力生图');
  assert.equal(configSave.values.changes.endpoints[0].model, 'image-v3');
  assert.equal(configSave.values.changes.endpoints[0].quality, 'hd');
  assert.equal(configSave.values.changes.endpoints[0].max_reference_images, 4);
  assert.equal(configSave.values.changes.sd_webui.base_url, 'http://127.0.0.1:7861');
  assert.deepEqual(JSON.parse(JSON.stringify(configSave.values.changes.sd_webui.parameters.generate)),
    {steps: 32, cfg_scale: 6.5, sampler_name: 'Euler'},
    'function-specific sampling fields and advanced values are saved together');
  configSave.resolve({...config, revision: 'image-r2', settings: configSave.values.changes});
  assert.equal(await leaveSave, true);
  await flush();
  assert.equal(page.isDirty(), false);
  assert.match(node.innerHTML, /生图模型与优先级已保存/);

  node.click('[data-img-endpoint-test]', {imgEndpointTest: 'primary'});
  const uncertainTest = requests.filter(r => r.method === 'POST' && r.route.endsWith('/image/output/test')).at(-1);
  uncertainTest.resolve({status: 'unknown', elapsed_ms: 8400, error: 'backend_result_unconfirmed'});
  await flush();
  assert.match(node.innerHTML, /结果未知 · backend_result_unconfirmed/,
    'an uncertain provider result is surfaced without an automatic retry');

  node.click('[data-img-view]', {imgView: 'edit'});
  node.click('[data-img-test]', {imgTest: 'image.mask_edit'});
  const maskCheck = requests.find(r => r.route === 'model/image/test');
  assert.equal(maskCheck.values.action, 'image.mask_edit');
  maskCheck.resolve({...image.actions[2], check: 'registration_only'});
  await flush();
  assert.match(node.innerHTML, /蒙版局部改图未就绪：当前生图模型队列未提供此操作/);
  node.change('[data-img-toggle]', {imgToggle: 'image.edit', checked: false});
  node.change('[data-img-toggle]', {imgToggle: 'image.mask_edit', checked: false});
  node.click('[data-img-save]');
  const post = requests.find(r => r.method === 'POST' && r.route === 'model/image/save');
  assert.ok(post, 'image save should POST to model/image/save');
  assert.deepEqual({...post.values.enabled}, {'image.generate': true, 'image.edit': false, 'image.mask_edit': false});
  post.resolve({...image, enabled: post.values.enabled, actions: image.actions.map(item =>
    ['image.edit', 'image.mask_edit'].includes(item.action) ? {...item, enabled: false, state: 'disabled'} : item)});
  await flush();
  assert.match(node.innerHTML, /生图开关已保存/);
  node.click('[data-img-test]', {imgTest: 'image.generate'});
  const check = requests.find(r => r.route === 'model/image/test' && r.values.action === 'image.generate');
  assert.equal(check.values.action, 'image.generate');
  check.resolve({...image.actions[0], check: 'registration_only'});
  await flush();
  assert.match(node.innerHTML, /已注册，可由后续真实任务调用/);
});

test('NAI routes show plugin defaults in each operation queue without duplicate credentials', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'image'});
  requests.find(item => item.route === 'model/image').resolve({actions: [], enabled: {
    'image.generate': true, 'image.edit': true, 'image.mask_edit': false,
  }});
  await flush();
  requests.find(item => item.route.endsWith('/image/output')).resolve({
    revision: 'nai-r1', nai: {available: true, api_profile: 'starbridge', call_mode: 'openai',
      default_model: 'nai-diffusion-5-full', default_size: '竖图', operations: ['generate', 'edit']},
    settings: {enabled: true, backend: 'nai', endpoints: [], comfyui: {}, sd_webui: {},
      operation_routes: {generate: ['nai'], edit: ['nai']}},
  });
  await flush();
  assert.match(node.innerHTML, /data-img-route-card="nai"/);
  assert.match(node.innerHTML, /星桥（Almoon）/);
  assert.match(node.innerHTML, /nai-diffusion-5-full/);
  assert.doesNotMatch(node.innerHTML, /data-img-config-field="nai\.(api_key|base_url)"/);
  node.click('[data-img-view]', {imgView: 'edit'});
  assert.match(node.innerHTML, /可用于参考图改图/);
  page.unmount();
});

test('NAI has a visible add entry that enables output and blocks unsupported direct editing', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'image'});
  requests.find(item => item.route === 'model/image').resolve({actions: [], enabled: {
    'image.generate': true, 'image.edit': true, 'image.mask_edit': false,
  }});
  await flush();
  const config = {
    revision: 'nai-add-r1', nai: {available: true, call_mode: 'direct',
      default_model: 'nai-diffusion-4-5-full', operations: ['generate']},
    settings: {enabled: false, backend: 'auto', endpoints: [], comfyui: {}, sd_webui: {},
      operation_routes: {generate: [], edit: []}},
  };
  requests.find(item => item.route.endsWith('/image/output')).resolve(config);
  await flush();
  assert.match(node.innerHTML, /data-img-nai-source/);
  assert.match(node.innerHTML, /加入文生图队列/);
  node.click('[data-img-nai-add]');
  assert.match(node.innerHTML, /data-img-route-card="nai"/);
  assert.match(node.innerHTML, /生图后端已启用/);
  node.click('[data-img-config-save]');
  const saved = requests.find(item => item.route.endsWith('/image/output/save'));
  assert.ok(saved);
  assert.equal(saved.values.changes.enabled, true);
  assert.deepEqual(JSON.parse(JSON.stringify(saved.values.changes.operation_routes)),
    {generate: ['nai'], edit: []});
  saved.resolve({...config, revision: 'nai-add-r2', settings: saved.values.changes});
  await flush();
  node.click('[data-img-view]', {imgView: 'edit'});
  assert.match(node.innerHTML, /当前接入方式不支持此功能/);
  assert.match(node.innerHTML, /data-img-nai-add disabled/);
  node.click('[data-img-nai-add]');
  assert.doesNotMatch(node.innerHTML, /data-img-route-card="nai"/);
  page.unmount();
});

test('an uninstalled NAI plugin has no add entry and preserves saved routes for removal', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'image'});
  requests.find(item => item.route === 'model/image').resolve({actions: [], enabled: {
    'image.generate': true, 'image.edit': false, 'image.mask_edit': false,
  }});
  await flush();
  requests.find(item => item.route.endsWith('/image/output')).resolve({
    revision: 'nai-missing', nai: {installed: false, available: false, operations: []},
    settings: {enabled: true, backend: 'auto', endpoints: [], comfyui: {}, sd_webui: {},
      operation_routes: {generate: ['nai'], edit: []}},
  });
  await flush();
  assert.match(node.innerHTML, /data-img-route-card="nai"/);
  assert.match(node.innerHTML, /NAI 插件未就绪/);
  assert.doesNotMatch(node.innerHTML, /data-img-nai-source/);
  node.click('[data-img-route-remove]', {imgRouteRemove: 'nai'});
  assert.doesNotMatch(node.innerHTML, /data-img-route-card="nai"/);
  assert.doesNotMatch(node.innerHTML, /data-img-nai-add/);
  assert.doesNotMatch(node.innerHTML, /option value="nai"/);
  page.unmount();
});

test('switching tabs round-trip keeps the functional matrix without reloading', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'voice'});
  requests.find(r => r.route === 'model/voice').resolve({languages: [{k: 'zh', n: '中文', bind: ''}, {k: 'ja', n: '日语', bind: ''}, {k: 'en', n: '英语', bind: ''}], providers: [], timeout_seconds: 30, voice_enabled: false, edit_token: TOKEN});
  await flush();
  node.click('[data-model-tab]', {modelTab: 'fn'});
  await flush();
  assert.match(node.innerHTML, /对话回复/);
  assert.match(node.innerHTML, /事实整理/);
  // exactly one functional GET happened on mount; switching back reused in-memory state
  const fnGets = requests.filter(r => r.method === 'GET' && r.route === 'model');
  assert.equal(fnGets.length, 1, 'functional matrix must not be re-fetched on tab return');
});

test('the functional page exposes the flattened two-level subtabs and unified routing list', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  assert.match(node.innerHTML, /任务路由/);
  assert.match(node.innerHTML, /替换策略/);
  assert.match(node.innerHTML, /需要模型的功能/);
  assert.match(node.innerHTML, /可用模型/);
  assert.match(node.innerHTML, /对话回复/);
  assert.match(node.innerHTML, /按业务分组/);
  assert.match(node.innerHTML, /模型提供方图例/);
  assert.match(node.innerHTML, /class="model-mlgd-box"/);
  assert.doesNotMatch(node.innerHTML, /class="model-mlgd-box" open/);
  node.click('[data-model-av]', {modelAv: 'lane'});
  assert.match(node.innerHTML, /前台/);
  assert.match(node.innerHTML, /后台/);
});

test('the situation map shows the inherited window provider as fallback when no task fallback is set', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const data = status();
  for (const item of data.tasks) {
    item.fields.provider.value = 'secondary';
    item.fields.provider.inherited_value = 'primary';
    item.fields.fallback.value = '';
    item.fields.fallback.inherited_value = '';
  }
  requests[0].resolve(data);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'map'});
  assert.match(node.innerHTML, /data-provider-pill="secondary"/);
  assert.ok((node.innerHTML.match(/data-provider-pill="primary"/g) || []).length >= 2,
    "the inherited window provider should appear as each task's fallback");
  assert.match(node.innerHTML, /0<\/b><span>暂无回退/);
});

test('hovering a provider pill highlights every occurrence of that provider', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const data = status();
  data.tasks[0].fields.fallback.value = 'secondary';
  data.tasks[1].fields.fallback.value = 'primary';
  requests[0].resolve(data);
  await flush();
  const routeSection = node.innerHTML.split('class="model-route-groups"')[1] || '';
  assert.equal((routeSection.match(/data-provider-pill="primary"/g) || []).length, 3);

  const primary = [node.addProviderPill('primary'), node.addProviderPill('primary'), node.addProviderPill('primary')];
  const secondary = node.addProviderPill('secondary');
  node.pointer('pointerover', primary[0]);
  assert.ok(primary.every(pill => pill.classList.contains('is-hovered')));
  assert.equal(secondary.classList.contains('is-hovered'), false);
  node.pointer('pointerout', primary[0]);
  assert.ok(primary.every(pill => !pill.classList.contains('is-hovered')));
});

test('quick config fans a slot provider out to every task via model/configure', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.click('[data-model-cm]', {modelCm: 'quick'});
  assert.match(node.innerHTML, /覆盖/);
  node.change('[data-model-slot]', {modelSlot: '对话', value: 'secondary'});
  node.click('[data-model-slot-apply]', {modelSlotApply: '对话'});
  const posts = requests.filter(r => r.method === 'POST' && r.route === 'model/configure');
  assert.ok(posts.length >= 1, 'slot override should POST at least one model/configure');
  posts.forEach(p => p.resolve({replayed: false, task: task('conversation.reply', 'secondary')}));
  await flush();
  assert.match(node.innerHTML, /已保存/);
});

test('quick config saves both image tasks sequentially from the same page snapshot', async () => {
  const h = harness(), node = h.root(), data = status();
  data.tasks = [task('conversation.context_image_caption', 'primary', ['text', 'image']),
    task('conversation.image_narration', 'primary', ['text', 'image'])];
  data.providers.forEach(provider => provider.capabilities.push('image'));
  h.page.mount(node);
  h.requests[0].resolve(data);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.change('[data-model-slot]', {modelSlot: '对话', value: 'secondary'});
  node.click('[data-model-slot-apply]', {modelSlotApply: '对话'});
  for (const item of data.tasks) {
    const post = h.requests.at(-1);
    assert.equal(post.method, 'POST');
    assert.equal(post.values.kind, item.kind);
    assert.equal(post.values.edit_token, TOKEN);
    assert.deepEqual({...post.values.changes}, {provider: 'secondary'});
    post.resolve({replayed: false, task: editable(item.kind, 'secondary', ['text', 'image'])});
    await flush();
  }
  assert.equal(h.requests.filter(request => request.method === 'POST').length, 2);
  assert.match(node.innerHTML, /已保存 2 项/);
  h.page.unmount();
});

test('a save conflict reloads current values and preserves only the fields edited locally', async () => {
  const h = harness(), node = h.root();
  h.page.mount(node);
  h.requests[0].resolve(status());
  await flush();
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  h.requests[1].resolve({status: 'error', message: 'configuration_revision_conflict', data: null});
  await flush();
  assert.equal(h.requests[2]?.method, 'GET', 'a confirmed conflict must read the current configuration');
  const latest = status();
  latest.tasks[1].edit_token = 'b'.repeat(64);
  latest.tasks[1].fields.prompt = {value: 'new remote prompt', explicit: true, source: 'role', inherited_value: '', inherited_source: 'default'};
  h.requests[2].resolve(latest);
  await flush();
  assert.equal(h.requests.filter(r => r.method === 'POST').length, 1, 'refresh must not silently retry a write');
  assert.equal(h.store.size, 0, 'a rejected operation is no longer uncertain');
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  const saved = h.requests[3];
  assert.equal(saved?.values.edit_token, latest.tasks[1].edit_token);
  assert.deepEqual({...saved.values.changes}, {provider: 'secondary'}, 'the remote prompt must not be overwritten by an untouched old value');
  saved.resolve({replayed: false, task: editable('memory.reflect', 'secondary')});
  await flush();
  h.page.unmount();
});

test('a real field conflict shows both values and rereads before adopting a new save baseline', async () => {
  const h = harness(), node = h.root(), initial = status();
  initial.providers.push({id: 'third', capabilities: ['text'], max_output_tokens: 8192});
  h.page.mount(node);
  h.requests[0].resolve(initial);
  await flush();
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  h.requests[1].resolve({status: 'error', message: 'configuration_revision_conflict', data: null});
  await flush();
  const latest = structuredClone(initial);
  latest.tasks[1].edit_token = 'b'.repeat(64);
  latest.tasks[1].fields.provider.value = 'third';
  assert.equal(h.requests[2]?.method, 'GET');
  h.requests[2].resolve(latest);
  await flush();
  assert.match(node.innerHTML, /当前值[\s\S]*third[\s\S]*草稿[\s\S]*secondary/);
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  assert.equal(h.requests.length, 3, 'a conflicting draft must wait for review');
  node.click('[data-model-action]', {modelAction: 'rebase', modelKind: 'memory.reflect'});
  assert.equal(h.requests[3]?.method, 'GET', 'review must not reuse a stale cached token');
  const freshest = structuredClone(latest);
  freshest.tasks[1].edit_token = 'c'.repeat(64);
  freshest.tasks[1].fields.prompt.value = 'changed during review';
  freshest.tasks[1].fields.prompt.explicit = true;
  h.requests[3].resolve(freshest);
  await flush();
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  assert.equal(h.requests[4]?.values.edit_token, freshest.tasks[1].edit_token);
  assert.deepEqual({...h.requests[4].values.changes}, {provider: 'secondary'});
  h.requests[4].resolve({replayed: false, task: editable('memory.reflect', 'secondary')});
  await flush();
  h.page.unmount();
});

test('repeated conflict refreshes never turn untouched remote fields into local edits', async () => {
  const h = harness(), node = h.root(), initial = status();
  initial.providers.push({id: 'third', capabilities: ['text'], max_output_tokens: 8192});
  initial.tasks[1].fields.prompt = {value: 'original', explicit: true, source: 'role', inherited_value: '', inherited_source: 'default'};
  h.page.mount(node);
  h.requests[0].resolve(initial);
  await flush();
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  h.requests[1].resolve({status: 'error', message: 'configuration_revision_conflict', data: null});
  await flush();
  const latest = structuredClone(initial);
  latest.tasks[1].edit_token = 'b'.repeat(64);
  latest.tasks[1].fields.provider.value = 'third';
  latest.tasks[1].fields.prompt.value = 'remote change';
  h.requests[2].resolve(latest);
  await flush();
  node.click('[data-model-action]', {modelAction: 'refresh'});
  h.requests[3].resolve(structuredClone(latest));
  await flush();
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  assert.equal(h.requests.length, 4, 'a refresh must not clear an unresolved field conflict');
  node.click('[data-model-action]', {modelAction: 'rebase', modelKind: 'memory.reflect'});
  const freshest = structuredClone(latest);
  freshest.tasks[1].edit_token = 'c'.repeat(64);
  freshest.tasks[1].fields.prompt.value = 'another remote change';
  h.requests[4].resolve(freshest);
  await flush();
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  assert.deepEqual({...h.requests[5].values.changes}, {provider: 'secondary'});
  h.requests[5].resolve({replayed: false, task: editable('memory.reflect', 'secondary')});
  await flush();
  h.page.unmount();
});

test('a validation rejection leaves the task editable without a revision-conflict lock', async () => {
  const h = harness(), node = h.root();
  h.page.mount(node);
  h.requests[0].resolve(status());
  await flush();
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  h.requests[1].resolve({status: 'error', message: 'model_provider_unavailable', data: null});
  await flush();
  assert.equal(h.requests.length, 2);
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  assert.equal(h.requests[2]?.method, 'POST', 'a validation error must allow a corrected save');
  h.requests[2].resolve({replayed: false, task: editable('memory.reflect', 'secondary')});
  await flush();
  h.page.unmount();
});

test('replacement policy edits keyword rules into the task replacement_rules field', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-sub]', {modelSub: 'pol'});
  assert.match(node.innerHTML, /关键词换模/);
  assert.match(node.innerHTML, /高价时段替代/);
  assert.match(node.innerHTML, /拒答替代/);
  assert.match(node.innerHTML, /作用范围/);
  node.click('[data-rule-add]');
  assert.match(node.innerHTML, /data-rule-card="conversation.reply:0"/);
  node.change('[data-rule-keywords]', {ruleKeywords: 'conversation.reply:0', value: '写代码, 报错'});
  node.change('[data-rule-provider]', {ruleProvider: 'conversation.reply:0', value: 'secondary'});
  node.change('[data-rule-match]', {ruleMatch: 'conversation.reply:0', value: 'exact'});
  node.click('[data-policy-save]', {policySave: 'conversation.reply'});
  const post = requests.find(request => request.method === 'POST');
  const rules = JSON.parse(post.values.changes.replacement_rules);
  assert.equal(rules.length, 1);
  assert.deepEqual(rules[0].keywords, ['写代码', '报错']);
  assert.equal(rules[0].provider_id, 'secondary');
  assert.equal(rules[0].match_mode, 'exact');
  assert.equal(rules[0].priority, 0);
  post.resolve({replayed: false, task: editable('conversation.reply', 'primary')});
  await flush();
  assert.match(node.innerHTML, /已保存 1 项/);
});

test('the refusal switch saves fallback_on_refusal for the task', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-sub]', {modelSub: 'pol'});
  assert.match(node.innerHTML, /class="model-policy-bar"/);
  assert.doesNotMatch(node.innerHTML, /替换策略工作台|model-policy-layout|class="model-policy-mode"/);
  assert.doesNotMatch(node.innerHTML, /已读取当前任务配置/);
  node.click('[data-model-pm]', {modelPm: 'refuse'});
  assert.match(node.innerHTML, /拒答替代/);
  node.change('[data-refusal-switch]', {checked: true});
  assert.match(node.innerHTML, /data-refusal-provider/);
  assert.match(node.innerHTML, /data-refusal-terms/);
  node.change('[data-refusal-provider]', {value: 'secondary'});
  node.change('[data-refusal-terms]', {value: 'model says no\nrequest rejected'});
  assert.match(node.innerHTML, /已识别 <b data-refusal-count>2<\/b> 条/);
  node.click('[data-policy-save]', {policySave: 'conversation.reply'});
  const post = requests.find(request => request.method === 'POST');
  assert.deepEqual({...post.values.changes}, {fallback: 'secondary', fallback_on_refusal: true,
    refusal_terms: 'model says no\nrequest rejected'});
  post.resolve({replayed: false, task: editable('conversation.reply', 'primary')});
  await flush();
  assert.match(node.innerHTML, /已保存 1 项/);
});

test('the replacement policy includes the core reply for time-window provider switches', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const data = status();
  data.tasks[0].execution_path = 'astrbot_core';
  requests[0].resolve(data);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'pol'});
  const selectorStart = node.innerHTML.indexOf('<select data-model-pol-task');
  const selectorEnd = node.innerHTML.indexOf('</select>', selectorStart);
  const selector = selectorStart >= 0 && selectorEnd >= 0 ? node.innerHTML.slice(selectorStart, selectorEnd + 9) : '';
  assert.match(selector, /conversation\.reply/);
  assert.match(selector, /memory\.reflect/);
});

test('the high-price preset writes two keyword-free time rules with a timezone', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-sub]', {modelSub: 'pol'});
  node.click('[data-model-pm]', {modelPm: 'peak'});
  assert.match(node.innerHTML, /高价时段替代/);
  node.change('[data-peak-provider]', {peakProvider: '', value: 'secondary'});
  node.click('[data-peak-preset]');
  node.click('[data-peak-apply]');
  node.click('[data-policy-save]', {policySave: 'conversation.reply'});
  const post = requests.find(request => request.method === 'POST');
  const rules = JSON.parse(post.values.changes.replacement_rules);
  assert.equal(rules.length, 2);
  assert.deepEqual(rules[0].keywords, []);
  assert.equal(rules[0].provider_id, 'secondary');
  assert.deepEqual(rules.map(rule => [rule.time_window.start, rule.time_window.end]), [['09:00', '12:00'], ['14:00', '18:00']]);
  assert.equal(rules[0].time_window.timezone, 'Asia/Shanghai');
  post.resolve({replayed: false, task: editable('conversation.reply', 'primary')});
  await flush();
});

test('a task whose routes are inherited shows the upstream rule count instead of editing them', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const shared = status();
  shared.tasks[0].fields.replacement_rules = {value: '[]', inherited_value: JSON.stringify([
    {name: '共享规则', keywords: ['代码'], provider_id: 'secondary'},
  ]), source: 'default', inherited_source: 'installation', explicit: false};
  requests[0].resolve(shared);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'pol'});
  assert.match(node.innerHTML, /共享规则/);
  assert.match(node.innerHTML, /上级默认规则 1 条/);
  node.click('[data-policy-discard]', {policyDiscard: 'conversation.reply'});
  assert.match(node.innerHTML, /已放弃/);
});

test('the situation map lists rule counts and routes a selected task into the editor', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const mapped = status();
  mapped.tasks[0].fields.replacement_rules = {value: JSON.stringify([{name: '代码问题', keywords: ['代码'], provider_id: 'secondary'}]), inherited_value: '[]', source: 'window', inherited_source: 'default', explicit: true};
  requests[0].resolve(mapped);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'map'});
  assert.match(node.innerHTML, /1 规则/);
  node.click('[data-map-pick]', {mapPick: 'conversation.reply'});
  assert.match(node.innerHTML, /已选 <strong>1<\/strong> 项任务/);
  node.click('[data-map-test]');
  const probe = requests.find(r => r.route === 'model/test');
  assert.deepEqual({...probe.values}, {provider_id: 'primary'});
  /* The live Dashboard bridge resolves the inner result directly.  Its probe
     also has status: "ok", so this catches the same collision as TTS audio. */
  probe.resolve({provider_id: 'primary', status: 'ok', latency_ms: 15, code: ''});
  await flush();
  assert.match(node.innerHTML, /连接成功/);
  node.click('[data-map-clear]');
  assert.doesNotMatch(node.innerHTML, /data-map-test/);
  node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
  assert.match(node.innerHTML, /model-fncard open/);
  assert.match(node.innerHTML, /任务附加提示词/);
});

test('the scope selector saves replacement_scope for the current task', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-sub]', {modelSub: 'pol'});
  assert.match(node.innerHTML, /仅插件调用/);
  assert.match(node.innerHTML, /仅普通对话模型/);
  assert.match(node.innerHTML, /全部生效/);
  node.click('[data-model-scope]', {modelScope: 'plugin'});
  assert.match(node.innerHTML, /替换范围已改动/);
  node.click('[data-policy-save]', {policySave: 'conversation.reply'});
  const post = requests.find(r => r.route === 'model/configure');
  assert.equal(post.values.changes.replacement_scope, 'plugin');
  post.resolve({replayed: false, task: editable('conversation.reply', 'primary')});
  await flush();
  assert.match(node.innerHTML, /已保存 1 项/);
});

test('two same-type providers show their model names so they are tellable apart', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  /* 宿主新增的 model 字段：同一个 AstrBot 上挂两个同类型 Provider 时，
     只显示 id 的话下拉里是两行一样的字，选错只能靠试。 */
  requests[0].resolve({
    tasks: [{kind: 'conversation.reply', lane: 'foreground', capabilities: ['text'], edit_token: TOKEN,
             application_state: 'applied', fields: baseFields('primary')}],
    providers: [
      {id: 'primary', capabilities: ['text'], max_output_tokens: 8192, model: 'deepseek-v4-flash'},
      {id: 'secondary', capabilities: ['text'], max_output_tokens: 8192, model: 'deepseek-v4-pro'},
    ],
    host_state: 'running',
  });
  await flush();
  node.click('[data-model-open]', {modelOpen: 'conversation.reply'});
  /* 主 Provider 的下拉里两项必须带模型名。 */
  assert.match(node.innerHTML, /primary · deepseek-v4-flash/);
  assert.match(node.innerHTML, /secondary · deepseek-v4-pro/);
  /* Provider 视图里模型名单独占一行。 */
  node.click('[data-model-view]', {modelView: 'providers'});
  assert.match(node.innerHTML, /class="model-provider-model">deepseek-v4-flash</);
});

test('a provider without a model name degrades to its bare id', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  /* 本地测试适配器没有"模型"这个概念，字段允许为空。
     空值不能拼出 "primary · " 这种尾巴，也不能渲染出一个空的模型名行。 */
  requests[0].resolve({
    tasks: [{kind: 'conversation.reply', lane: 'foreground', capabilities: ['text'], edit_token: TOKEN,
             application_state: 'applied', fields: baseFields('primary')}],
    providers: [{id: 'primary', capabilities: ['text'], max_output_tokens: 8192, model: ''}],
    host_state: 'running',
  });
  await flush();
  assert.doesNotMatch(node.innerHTML, /primary ·/);
  node.click('[data-model-view]', {modelView: 'providers'});
  assert.doesNotMatch(node.innerHTML, /model-provider-model/);
});



test('the routing page does not render a global provider catalog cloud', () => {
  const css = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/models-page.css'), 'utf8');
  assert.doesNotMatch(source, /class="model-catalog"|AstrBot 模型列表|path \+ "\/catalog"/);
  assert.doesNotMatch(css, /\.model-catalog/);
  assert.match(source, /<i>令牌<\/i>/);
  assert.match(source, /主模型提供方/);
  assert.match(source, /备用模型提供方/);
});

test('search service binds and saves an AstrBot DeepSeek Provider id', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();

  node.click('[data-model-tab]', {modelTab: 'search'});
  const read = requests.find(item => item.method === 'GET' && item.route === 'model/search');
  assert.ok(read);
  read.resolve({deepseek_provider_id: '', revision: 'search-r1', provider_available: true,
    providers: [{id: 'deepseek_main', name: 'DeepSeek 主账号'}]});
  await flush();
  assert.match(node.innerHTML, /DeepSeek 搜索服务/);
  assert.match(node.innerHTML, /Search \/ Fetch API/);

  node.change('[data-search-provider]', {value: 'deepseek_main'});
  assert.equal(page.isDirty(), true);
  assert.ok(Array.from(page.changedFields()).includes('联网搜索凭据来源'));
  node.click('[data-search-save]');
  const save = requests.find(item => item.method === 'POST' && item.route === 'model/search/save');
  assert.deepEqual(JSON.parse(JSON.stringify(save.values)), {
    revision: 'search-r1', changes: {deepseek_provider_id: 'deepseek_main', source_priority: 'auto'},
  });
  save.resolve({deepseek_provider_id: 'deepseek_main', source_priority: 'auto', revision: 'search-r2', provider_available: true,
    providers: [{id: 'deepseek_main', name: 'DeepSeek 主账号'}]});
  await flush();
  assert.match(node.innerHTML, /后续搜索立即生效/);
  assert.equal(page.isDirty(), false);
  page.unmount();
});

test('group Laya reads global settings and offers a visible same-role conversation switch', async () => {
  const h = harness(), node = h.root(), chosen = [];
  const group = {target_id: 'group', scope_kind: 'group', roles: [{role_id: 'role'}]};
  const privateTarget = {target_id: 'private', scope_kind: 'private', roles: [{role_id: 'role'}]};
  h.product.selection = () => ({target_id: 'group', role_id: 'role'});
  h.product.target = () => group;
  h.product.targets = () => [group, privateTarget];
  h.product.targetLabel = target => target.scope_kind === 'group' ? '群聊 测试群' : '私聊 测试用户';
  h.product.choose = (targetId, roleId) => chosen.push([targetId, roleId]);
  h.page.mount(node, {endpoint: '/astrbot_plugin_private_companion/host/targets/group/roles/role/model'});
  h.requests[0].resolve({...status(), scope_kind: 'group'});
  await flush();
  node.click('[data-model-tab]', {modelTab: 'laya'});
  h.requests.find(item => item.route.endsWith('/laya')).resolve(layaRemoteStatus());
  h.requests.find(item => item.route.endsWith('/laya/logs')).resolve({events: [], available: false});
  await flush();

  assert.match(node.innerHTML, /群聊 测试群/);
  assert.match(node.innerHTML, /data-laya-target/);
  assert.match(node.innerHTML, /私聊 测试用户/);
  assert.match(node.innerHTML, /当前群聊的近期决策记录暂不能按会话汇总/);
  assert.match(node.innerHTML, /Laya 决策模型/);
  node.change('[data-laya-target]', {value: 'private'});
  assert.deepEqual(chosen, [['private', 'role']]);
  h.page.unmount();
});

test('Laya configuration still renders when the log request fails', async () => {
  const h = harness(), node = h.root();
  h.page.mount(node);
  h.requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'laya'});
  h.requests.find(item => item.route === 'model/laya').resolve(layaRemoteStatus());
  h.requests.find(item => item.route === 'model/laya/logs').reject(new Error('logs_unavailable'));
  await flush();

  assert.match(node.innerHTML, /Laya 决策模型/);
  assert.match(node.innerHTML, /近期记录暂不可用；Laya 配置仍可查看和保存/);
  assert.doesNotMatch(node.innerHTML, /无法读取 Laya 配置/);
  h.page.unmount();
});

test('Laya local page explains CPU-only torch and disables the broken test', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'laya'});
  requests.find(item => item.method === 'GET' && item.route === 'model/laya').resolve({
    enabled: false, mode: 'local', endpoint: '', default_model: 'multilingual',
    api_key_set: false, timeout_seconds: 20, followup_confidence_threshold: 0,
    package_installed: true, package_version: '0.4.0',
    decision_fields: {turn_state: true, action: true, reply_intent: true, reply_style: true,
      reply_form: true, segment_mode: true, tool_need: true},
    model_cache_ready: true, model_cache_path: 'C:/cache/huggingface/hub',
    local_runtime_state: 'gpu_unavailable', torch_version: '2.14.1+cpu',
    cuda_version: '', cuda_available: false,
    python_executable: 'C:/AstrBot/python.exe',
    install_command: "& 'C:/AstrBot/python.exe' -m pip install 'laya==0.4.0'",
    remote_install_command: "& 'C:/AstrBot/python.exe' -m pip install 'laya[serve]==0.4.0'",
    remote_start_command: "$env:LAYA_DEFAULT_MODEL='multilingual'; & 'C:/AstrBot/python.exe' -m laya.serve",
    revision: 'laya-cpu'});
  requests.find(item => item.method === 'GET' && item.route === 'model/laya/logs').resolve({events: []});
  await flush();
  assert.match(node.innerHTML, /CUDA 不可用/);
  assert.match(node.innerHTML, /2\.14\.1\+cpu/);
  assert.match(node.innerHTML, /安装与驱动匹配的 CUDA 版 PyTorch/);
  assert.match(node.innerHTML, /data-laya-test[^>]*disabled/);
  assert.doesNotMatch(node.innerHTML, /下载并验证模型[^<]*<\/button>/);
});

test('Laya page exposes official local install, first-run download, and SystemOne remote setup', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'laya'});
  const statusRequest = requests.find(item => item.method === 'GET' && item.route === 'model/laya');
  assert.ok(statusRequest);
  statusRequest.resolve({enabled: false, mode: 'local', endpoint: '', default_model: 'multilingual',
    api_key_set: false, timeout_seconds: 20, followup_confidence_threshold: 0,
    package_installed: true, package_version: '0.4.0',
    decision_fields: {turn_state: true, action: true, reply_intent: true, reply_style: true,
      reply_form: true, segment_mode: true, tool_need: true},
    model_cache_ready: false, model_cache_path: 'C:/cache/huggingface/hub',
    python_executable: 'C:/AstrBot/python.exe',
    install_command: "& 'C:/AstrBot/python.exe' -m pip install 'laya==0.4.0'",
    remote_install_command: "& 'C:/AstrBot/python.exe' -m pip install 'laya[serve]==0.4.0'",
    remote_start_command: "$env:LAYA_DEFAULT_MODEL='multilingual'; & 'C:/AstrBot/python.exe' -m laya.serve",
    revision: 'laya-revision'});
  requests.find(item => item.method === 'GET' && item.route === 'model/laya/logs').resolve({events: [
    {status: 'failed', source: 'conversation', mode: 'local', elapsed_ms: 1200,
      reason_code: 'laya_inference_failed', reason: 'Laya 本地推理失败：RuntimeError',
      request: {model: 'multilingual', sent: true, state: '{"facts":{"user_text":"hello"}}',
        questions: {action: {type: 'choice'}}},
      response: {format: 'python_object', body: '{"answers":{}}', value: {answers: {}}},
      processing: {status: 'failed', reason_code: 'laya_response_invalid',
        stages: [{name: 'contract_conversion', status: 'failed'}]},
      enabled_fields: {turn_state: true, action: false, reply_intent: true, reply_style: false,
        reply_form: false, segment_mode: false, tool_need: false}, observed_at: new Date().toISOString()},
  ]});
  await flush();
  assert.match(node.innerHTML, /本地 Python 包/);
  assert.match(node.innerHTML, /配置全局生效/);
  assert.match(node.innerHTML, /本地检查点目录/);
  assert.match(node.innerHTML, /下载并验证模型/);
  assert.match(node.innerHTML, /C:\/AstrBot\/python.exe/);
  assert.match(node.innerHTML, /pip install .*laya==0\.4\.0/);
  assert.doesNotMatch(node.innerHTML, /网关地址|data-laya-preset/);
  assert.match(node.innerHTML, /判断回复形式/);
  assert.match(node.innerHTML, /判断分段倾向/);
  assert.match(node.innerHTML, /连续接话置信阈值/);
  assert.match(node.innerHTML, /0 关闭；仅约束非点名续接/);
  assert.match(node.innerHTML, /Laya 调用日志/);
  assert.match(node.innerHTML, /模型输入 · state 与分类问题/);
  assert.match(node.innerHTML, /模型原始响应/);
  assert.match(node.innerHTML, /合同解析、范围修正与实际应用/);
  assert.match(node.innerHTML, /contract_conversion/);
  assert.match(node.innerHTML, /实际对话/);
  assert.match(node.innerHTML, /失败/);
  assert.match(node.innerHTML, /当时启用：判断是否说完 · 判断回复意图/);

  node.change('[data-laya-decision]', {layaDecision: 'tool_need', checked: false});
  node.change('[data-laya-field]', {layaField: 'followup_confidence_threshold', value: 55});
  node.change('[data-laya-field]', {layaField: 'local_model_path', value: 'C:/models/laya-finetuned'});
  assert.deepEqual(Array.from(page.changedFields()).sort(),
    ['Laya · 判断能力需求', 'Laya · 连续接话置信阈值', 'Laya · 本地模型路径'].sort());
  node.click('[data-laya-save]');
  const saveRequest = requests.find(item => item.method === 'POST' && item.route === 'model/laya/save');
  assert.equal(saveRequest.values.changes.decision_fields.tool_need, false);
  assert.equal(saveRequest.values.changes.local_model_path, 'C:/models/laya-finetuned');
  assert.equal(saveRequest.values.changes.followup_confidence_threshold, 55);
  saveRequest.resolve({enabled: false, mode: 'local', endpoint: '', default_model: 'multilingual',
    local_model_path: 'C:/models/laya-finetuned', followup_confidence_threshold: 55,
    api_key_set: false, timeout_seconds: 20, decision_fields: {...saveRequest.values.changes.decision_fields},
    package_installed: true, package_version: '0.4.0', model_cache_ready: false,
    model_cache_path: 'C:/cache/huggingface/hub', python_executable: 'C:/AstrBot/python.exe',
    install_command: "& 'C:/AstrBot/python.exe' -m pip install 'laya==0.4.0'", revision: 'laya-revision-2'});
  await flush();

  node.change('[data-laya-field]', {layaField: 'mode', value: 'remote'});
  assert.match(node.innerHTML, /服务地址/);
  assert.match(node.innerHTML, /\/v1\/systemone/);
  assert.match(node.innerHTML, /复制服务启动命令/);
  assert.match(node.innerHTML, /data-laya-save/);
  assert.equal(page.isDirty(), true);
  assert.equal(page.discardChanges(), true);
  assert.equal(page.isDirty(), false);
  page.mount(root());
  requests.filter(item => item.method === 'GET' && item.route === 'model').at(-1).resolve(status());
  await flush();
  assert.equal(page.isDirty(), false, 'discarded cached model scopes cannot block later navigation');
  page.unmount();
});

test('Laya page offers an OpenAI-compatible gateway backend with its own address, key and model name', async () => {
  const {page, requests, root} = harness();
  const node = root();
  const layaStatus = overrides => ({enabled: true, mode: 'remote', endpoint: 'https://laya.test/v1',
    remote_model: '', default_model: 'multilingual', api_key_set: true, timeout_seconds: 20,
    followup_confidence_threshold: 0,
    decision_fields: {turn_state: true, action: true, reply_intent: true, reply_style: true,
      reply_form: true, segment_mode: true, tool_need: true},
    package_installed: false, package_version: '', model_cache_ready: false,
    model_cache_path: 'C:/cache/huggingface/hub', python_executable: 'C:/AstrBot/python.exe',
    install_command: "& 'C:/AstrBot/python.exe' -m pip install 'laya==0.4.0'",
    remote_install_command: '', remote_start_command: '', revision: 'laya-remote', ...overrides});

  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'laya'});
  requests.find(item => item.method === 'GET' && item.route === 'model/laya').resolve(layaStatus({}));
  requests.find(item => item.method === 'GET' && item.route === 'model/laya/logs').resolve({events: []});
  await flush();
  assert.match(node.innerHTML, /<option value="jev"[^>]*>OpenAI 兼容网关<\/option>/,
    'the access-mode list offers the gateway backend');
  assert.doesNotMatch(node.innerHTML, /<option value="jev"[^>]*>Jev 判断服务<\/option>/,
    'the old judgment-service label is gone');
  assert.match(node.innerHTML, /仅用于只提供 \/v1\/chat\/completions 的第三方中转/);
  assert.match(node.innerHTML, /TypeSafe 官方 Jev 请选「远程 HTTP 服务」/);

  node.change('[data-laya-field]', {layaField: 'mode', value: 'jev'});
  assert.match(node.innerHTML, /网关地址/);
  assert.match(node.innerHTML, /\/v1\/chat\/completions/);
  assert.match(node.innerHTML, /判断模型名/);
  assert.match(node.innerHTML, /留空使用 jev-latest/);
  assert.match(node.innerHTML, /OpenAI 兼容网关<\/h3>/);
  assert.match(node.innerHTML, /官方 TypeSafe Jev 不提供/);
  assert.doesNotMatch(node.innerHTML, /Jev 判断服务/);
  assert.doesNotMatch(node.innerHTML, /pip install .*laya==0\.4\.0/, 'a gateway backend needs no local package');
  assert.doesNotMatch(node.innerHTML, /data-laya-test[^>]*disabled/);

  node.change('[data-laya-field]', {layaField: 'endpoint', value: 'https://jev.example.test/v1'});
  node.change('[data-laya-field]', {layaField: 'remote_model', value: 'jev-1.14.0'});
  node.change('[data-laya-field]', {layaField: 'api_key', value: 'jev-secret'});
  assert.equal(page.isDirty(), true);
  node.click('[data-laya-save]');
  const saveRequest = requests.find(item => item.method === 'POST' && item.route === 'model/laya/save');
  assert.equal(saveRequest.values.changes.mode, 'jev');
  assert.equal(saveRequest.values.changes.endpoint, 'https://jev.example.test/v1');
  assert.equal(saveRequest.values.changes.remote_model, 'jev-1.14.0');
  assert.equal(saveRequest.values.changes.api_key, 'jev-secret');
  saveRequest.resolve(layaStatus({mode: 'jev', endpoint: 'https://jev.example.test/v1',
    remote_model: 'jev-1.14.0', default_model: 'jev-1.14.0', revision: 'laya-jev'}));
  await flush();
  assert.equal(page.isDirty(), false);
  assert.match(node.innerHTML, /判断模型：<code>jev-1\.14\.0<\/code>/);

  node.change('[data-laya-field]', {layaField: 'mode', value: 'local'});
  assert.match(node.innerHTML, /pip install .*laya==0\.4\.0/);
  assert.match(node.innerHTML, /本地检查点目录/);
  assert.doesNotMatch(node.innerHTML, /判断服务地址/);
  node.change('[data-laya-field]', {layaField: 'mode', value: 'jev'});

  node.click('[data-laya-test]');
  requests.find(item => item.method === 'POST' && item.route === 'model/laya/test')
    .reject(new Error('laya_auth_failed'));
  await flush();
  assert.match(node.innerHTML, /判断服务鉴权失败/, 'a rejected key keeps its own actionable message');
  /* 失败后页面会自动补拉运行记录；结算这两个读请求，避免测试留下悬挂的桥定时器。 */
  requests.filter(item => item.method === 'GET' && item.route === 'model/laya').at(-1)
    .resolve(layaStatus({mode: 'jev', endpoint: 'https://jev.example.test/v1',
      remote_model: 'jev-1.14.0', default_model: 'jev-1.14.0', revision: 'laya-jev'}));
  requests.filter(item => item.method === 'GET' && item.route === 'model/laya/logs').at(-1).resolve({events: []});
  await flush();
  page.unmount();
});

test('Laya remote mode offers official SystemOne presets that only fill the draft', async () => {
  const {page, node, requests} = await openLayaPage();
  assert.match(node.innerHTML, /data-laya-preset="laya"[^>]*>填入 Laya 官方</);
  assert.match(node.innerHTML, /data-laya-preset="typesafe"[^>]*>填入 TypeSafe Jev 官方</);
  assert.equal(page.isDirty(), false);

  node.click('[data-laya-preset]', {layaPreset: 'laya'});
  assert.match(node.innerHTML, /已填入「Laya 官方」的 SystemOne 地址与模型名，请填写 API Key 后保存。/);
  assert.match(node.innerHTML, /value="http:\/\/127\.0\.0\.1:8000\/v1\/systemone"/);
  assert.match(node.innerHTML, /value="multilingual"/);
  assert.equal(page.isDirty(), true);
  assert.equal(requests.filter(item => item.method === 'POST' && item.route === 'model/laya/save').length, 0,
    'a preset never saves by itself');

  node.click('[data-laya-preset]', {layaPreset: 'typesafe'});
  assert.match(node.innerHTML, /已填入「TypeSafe Jev 官方」的 SystemOne 地址与模型名，请填写 API Key 后保存。/);
  assert.match(node.innerHTML, /value="https:\/\/api\.typesafe\.ai\/v1\/systemone"/);
  assert.match(node.innerHTML, /value="jev-latest"/);

  node.click('[data-laya-save]');
  const saveRequest = requests.find(item => item.method === 'POST' && item.route === 'model/laya/save');
  assert.equal(saveRequest.values.changes.endpoint, 'https://api.typesafe.ai/v1/systemone');
  assert.equal(saveRequest.values.changes.remote_model, 'jev-latest');
  assert.equal(saveRequest.values.changes.mode, undefined, 'the preset keeps the remote access mode');
  saveRequest.resolve(layaRemoteStatus({endpoint: 'https://api.typesafe.ai/v1/systemone',
    remote_model: 'jev-latest', default_model: 'jev-latest', revision: 'laya-preset'}));
  await flush();
  page.unmount();
});

test('Laya gateway mode warns when the address belongs to SystemOne', async () => {
  const {page, node} = await openLayaPage();
  node.change('[data-laya-field]', {layaField: 'mode', value: 'jev'});
  node.change('[data-laya-field]', {layaField: 'endpoint', value: 'https://api.typesafe.ai/v1/systemone'});

  assert.match(node.innerHTML, /<div class="model-alert">当前接入方式是「OpenAI 兼容网关」/);
  assert.match(node.innerHTML, /TypeSafe 官方 Jev 请切换到「远程 HTTP 服务」/);
  assert.doesNotMatch(node.innerHTML, /data-laya-save[^>]*disabled/,
    'the warning only explains the mismatch; the backend refuses the save');
  page.unmount();
});

test('Laya remote mode warns when the address belongs to a chat gateway', async () => {
  const {page, node} = await openLayaPage();
  node.change('[data-laya-field]', {layaField: 'endpoint', value: 'https://gateway.test/v1/chat/completions'});

  assert.match(node.innerHTML, /当前接入方式是「远程 HTTP 服务」/);
  assert.match(node.innerHTML, /或切换到「OpenAI 兼容网关」/);
  page.unmount();
});

test('switching Laya out of remote resets a leftover request format', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'laya'});
  requests.find(item => item.method === 'GET' && item.route === 'model/laya')
    .resolve(layaRemoteStatus({request_format: 'cloudflare', endpoint: 'https://laya.test/v1'}));
  requests.find(item => item.method === 'GET' && item.route === 'model/laya/logs').resolve({events: []});
  await flush();

  node.change('[data-laya-field]', {layaField: 'mode', value: 'jev'});
  node.change('[data-laya-field]', {layaField: 'endpoint', value: 'https://gateway.test/v1'});
  node.click('[data-laya-save]');
  const saveRequest = requests.find(item => item.method === 'POST' && item.route === 'model/laya/save');
  assert.equal(saveRequest.values.changes.request_format, 'native');
  saveRequest.resolve(layaRemoteStatus({mode: 'jev', endpoint: 'https://gateway.test/v1',
    request_format: 'native', default_model: 'jev-latest', revision: 'laya-format'}));
  await flush();
  page.unmount();
});

function layaRemoteStatus(overrides) {
  return {enabled: true, mode: 'remote', endpoint: 'https://laya.test/v1', request_format: 'native',
    remote_model: '', default_model: 'multilingual', api_key_set: true, proxy: '', proxy_set: false,
    timeout_seconds: 20,
    followup_confidence_threshold: 0,
    decision_fields: {turn_state: true, action: true, reply_intent: true, reply_style: true,
      reply_form: true, segment_mode: true, tool_need: true},
    package_installed: false, package_version: '', model_cache_ready: false,
    model_cache_path: 'C:/cache/huggingface/hub', python_executable: 'C:/AstrBot/python.exe',
    install_command: "& 'C:/AstrBot/python.exe' -m pip install 'laya==0.4.0'",
    remote_install_command: '', remote_start_command: '', revision: 'laya-remote', ...overrides};
}

async function openLayaPage() {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'laya'});
  requests.find(item => item.method === 'GET' && item.route === 'model/laya').resolve(layaRemoteStatus());
  requests.find(item => item.method === 'GET' && item.route === 'model/laya/logs').resolve({events: []});
  await flush();
  return {page, node, requests};
}

test('Laya remote page prefills the saved proxy and treats an empty field as direct', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  node.click('[data-model-tab]', {modelTab: 'laya'});
  requests.find(item => item.method === 'GET' && item.route === 'model/laya')
    .resolve(layaRemoteStatus({proxy: 'http://127.0.0.1:12450', proxy_set: true, revision: 'laya-proxy'}));
  requests.find(item => item.method === 'GET' && item.route === 'model/laya/logs').resolve({events: []});
  await flush();

  assert.match(node.innerHTML, /data-laya-field="proxy"/);
  assert.match(node.innerHTML, /可选，仅 Laya 判断请求使用/);
  assert.match(node.innerHTML, /留空表示直连/);
  assert.match(node.innerHTML, /示例 http:\/\/127\.0\.0\.1:7890/);
  /* The saved address is prefilled, so the operator can tell whether Laya's
     requests are actually tunnelled without opening settings.json. */
  assert.match(node.innerHTML, /<input type="text" data-laya-field="proxy" value="http:\/\/127\.0\.0\.1:12450"[^>]*>/);
  assert.match(node.innerHTML, /<span class="model-pill ok">经代理<\/span>/);
  assert.equal(page.isDirty(), false);

  node.change('[data-laya-field]', {layaField: 'mode', value: 'local'});
  assert.doesNotMatch(node.innerHTML, /data-laya-field="proxy"/);
  node.change('[data-laya-field]', {layaField: 'mode', value: 'remote'});
  assert.match(node.innerHTML, /data-laya-field="proxy"/);
  assert.match(node.innerHTML, /value="http:\/\/127\.0\.0\.1:12450"/);

  /* Clearing the box is an explicit "direct connection" instruction, not
     "keep the saved proxy"; the badge flips while the draft is unsaved. */
  node.change('[data-laya-field]', {layaField: 'proxy', value: ''});
  assert.match(node.innerHTML, /<span class="model-pill">直连<\/span>/);
  assert.equal(page.isDirty(), true);
  assert.ok(Array.from(page.changedFields()).includes('Laya · 代理地址'));
  node.click('[data-laya-save]');
  const save = requests.find(item => item.method === 'POST' && item.route === 'model/laya/save');
  assert.equal(save.values.changes.proxy, '');
  save.resolve(layaRemoteStatus({proxy: '', proxy_set: false, revision: 'laya-proxy-cleared'}));
  await flush();

  assert.equal(page.isDirty(), false);
  assert.match(node.innerHTML, /<input type="text" data-laya-field="proxy" value=""[^>]*>/);
  assert.match(node.innerHTML, /<span class="model-pill">直连<\/span>/);

  /* Every save carries the effective proxy, so "not submitted" can never be
     mistaken for "keep the old value". */
  node.change('[data-laya-field]', {layaField: 'remote_model', value: 'jev-1.14.0'});
  node.click('[data-laya-save]');
  const resend = requests.filter(item => item.method === 'POST' && item.route === 'model/laya/save').at(-1);
  assert.equal(resend.values.changes.proxy, '');
  assert.equal(resend.values.changes.remote_model, 'jev-1.14.0');
  resend.resolve(layaRemoteStatus({proxy: '', proxy_set: false, revision: 'laya-proxy-r2'}));
  await flush();
  page.unmount();
});

/* 失败后的自动刷新由 catch 里的 layaRefreshQuiet 发起；测试要显式结算这两个读请求，
   页面才会走完 finally 并把这次失败行渲染进运行记录。 */
function settleLayaRefresh(requests, events) {
  const statusRead = requests.filter(item => item.method === 'GET' && item.route === 'model/laya').at(-1);
  const logRead = requests.filter(item => item.method === 'GET' && item.route === 'model/laya/logs').at(-1);
  statusRead.resolve(layaRemoteStatus());
  logRead.resolve({events});
}

test('Laya 451 refusal shows the server reason and the legal-region explanation', async () => {
  const {page, node, requests} = await openLayaPage();
  node.click('[data-laya-test]');
  const probe = requests.find(item => item.method === 'POST' && item.route === 'model/laya/test');
  assert.ok(probe, 'the connection test goes through the page bridge');
  probe.resolve({status: 'error', message: 'laya_http_error',
    reason: 'Laya HTTP 451：{"title":"Typesafe is not available in your region.","status":451}',
    data: {code: 'laya_http_error'}});
  await flush();
  settleLayaRefresh(requests, []);
  await flush();

  assert.match(node.innerHTML, /451/);
  assert.match(node.innerHTML, /地区/);
  assert.match(node.innerHTML, /不是插件故障/);
  assert.match(node.innerHTML, /代理地址/);
  assert.match(node.innerHTML, /服务端原文/);
  assert.match(node.innerHTML, /Typesafe is not available in your region\./,
    'the redacted server text must survive in the message');
  assert.doesNotMatch(node.innerHTML, /详细信息见下方/,
    'a status code must no longer replace the server explanation');
  page.unmount();
});

test('a failed Laya test refreshes the run log without a manual refresh', async () => {
  const {page, node, requests} = await openLayaPage();
  const logReadsBefore = requests.filter(item => item.method === 'GET' && item.route === 'model/laya/logs').length;
  assert.match(node.innerHTML, /暂无 Laya 调用记录/);

  node.click('[data-laya-test]');
  requests.find(item => item.method === 'POST' && item.route === 'model/laya/test')
    .resolve({status: 'error', message: 'laya_http_error',
      reason: 'Laya HTTP 403：forbidden by policy', data: {code: 'laya_http_error'}});
  await flush();

  const logReadsAfter = requests.filter(item => item.method === 'GET' && item.route === 'model/laya/logs').length;
  assert.equal(logReadsAfter, logReadsBefore + 1, 'the failed call must trigger a log read by itself');
  settleLayaRefresh(requests, [{status: 'failed', source: 'probe', mode: 'remote', elapsed_ms: 850,
    reason_code: 'laya_http_error', reason: 'Laya HTTP 403：forbidden by policy',
    observed_at: new Date().toISOString()}]);
  await flush();

  assert.match(node.innerHTML, /data-state="failed"/);
  assert.match(node.innerHTML, /forbidden by policy/);
  assert.doesNotMatch(node.innerHTML, /暂无 Laya 调用记录/);
  page.unmount();
});

test('Laya connection test forwards its long timeout to the page bridge', async () => {
  const {page, node, requests} = await openLayaPage();
  node.click('[data-laya-test]');
  const probe = requests.find(item => item.method === 'POST' && item.route === 'model/laya/test');
  assert.equal(probe.options?.timeoutMs, 600000,
    'a checkpoint download must not hit the bridge default timeout');

  probe.reject(new Error('laya_auth_failed'));
  await flush();
  settleLayaRefresh(requests, []);
  await flush();
  assert.match(node.innerHTML, /判断服务鉴权失败/, 'machine codes keep their existing message');
  page.unmount();
});

test('未选窗口时只编辑角色默认，不读取或保存窗口层', async () => {
  const h = harness(), node = h.root();
  h.product.selection = () => ({bot_id: 'bot-a', role_id: 'role-a', target_id: null});
  h.page.mount(node, {sub: 'cfg'});
  assert.equal(h.requests[0].params.layer, 'role');
  h.requests[0].resolve(status()); await flush();
  assert.match(node.innerHTML, /data-model-layer="window"[^>]*disabled/);
  node.click('[data-model-layer]', {modelLayer: 'window'});
  assert.equal(h.requests.length, 1);
  node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.equal(saved.values.layer, 'role');
  saved.resolve({replayed: false, task: task('memory.reflect', 'secondary')}); await flush();
  h.page.unmount();
});

test('群窗口模型覆盖使用本群接口，角色模型服务使用同角色完整入口', async () => {
  const h = harness(), node = h.root();
  const base = '/astrbot_plugin_private_companion/host/targets/';
  h.product.selection = () => ({bot_id: 'bot-a', role_id: 'role-a', target_id: 'group-a'});
  h.product.role = () => ({role_id: 'role-a', model_api: base + 'group-a/roles/role-a/model'});
  h.product.roleTarget = () => ({scope_kind: 'private', roles: [{role_id: 'role-a', model_api: base + 'private-a/roles/role-a/model'}]});
  h.page.mount(node, {sub: 'cfg'});
  assert.equal(h.requests[0].route, 'host/targets/group-a/roles/role-a/model');
  h.requests[0].resolve(status()); await flush();
  node.click('[data-model-layer]', {modelLayer: 'window'});
  assert.ok(h.requests[1], node.innerHTML);
  assert.equal(h.requests[1].route, 'host/targets/group-a/roles/role-a/model');
  assert.equal(h.requests[1].params.layer, 'window');
  const windowStatus = status();
  windowStatus.tasks.forEach(task => { task.configuration_layer = 'window'; });
  h.requests[1].resolve(windowStatus); await flush();
  node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'memory.reflect'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.equal(saved.route, 'host/targets/group-a/roles/role-a/model/configure');
  assert.equal(saved.values.layer, 'window');
  saved.resolve({replayed: false, task: task('memory.reflect', 'secondary')}); await flush();
  node.click('[data-model-tab]', {modelTab: 'laya'});
  const service = h.requests.filter(request => request.route.endsWith('/laya') || request.route.endsWith('/laya/logs'));
  assert.equal(service.length, 2);
  assert.ok(service.every(request => request.route.startsWith('host/targets/private-a/roles/role-a/')));
  service.forEach(request => request.resolve({})); await flush();
  h.page.unmount();
});

test('仅有群入口的角色不请求不存在的模型连接服务', async () => {
  const h = harness(), node = h.root();
  h.product.selection = () => ({bot_id: 'bot-a', role_id: 'role-a', target_id: null});
  h.product.roleTarget = () => ({scope_kind: 'group'});
  h.page.mount(node);
  h.requests[0].resolve(status()); await flush();
  node.click('[data-model-tab]', {modelTab: 'laya'});
  assert.equal(h.requests.length, 1);
  assert.match(node.innerHTML, /只有群聊模型入口/);
  h.page.unmount();
});

test('the primary provider select offers an explicit follow-parent option', async () => {
  const h = harness(), node = h.root(), data = status();
  /* A dead id saved on an editable layer: the backend keeps its failure
     visible instead of recovering it, which is the state the parent label
     has to describe. */
  const stale = task('content.authoring.draft', 'gone', ['text'], 'background');
  stale.fields.provider = {value: 'gone', inherited_value: 'gone', source: 'role',
    inherited_source: 'role', explicit: false};
  data.tasks = [stale];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  const card = node.innerHTML.split('data-fncard="content.authoring.draft"')[1].split('</article>')[0];
  assert.match(card, /<option value="__inherit__" selected>跟随上级（角色默认：gone · 当前不可用）<\/option>/);
  /* Inherited: the select stays on the parent option and does not present the
     stale id as selected; the caption still names the inherited route. */
  assert.doesNotMatch(card, /<option value="gone" selected>/);
  assert.match(card, /跟随上级时为 角色默认：gone/);
  h.page.unmount();
});

test('choosing the parent option submits a reset instead of an explicit provider', async () => {
  const h = harness(), node = h.root(), data = status();
  data.tasks = [editable('content.authoring.draft', 'primary')];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'content.authoring.draft', value: '__inherit__'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved, 'choosing the parent option should produce a save');
  assert.equal(JSON.stringify(saved.values.reset), JSON.stringify(['provider']));
  assert.equal(Object.hasOwn(saved.values.changes, 'provider'), false);
  h.page.unmount();
});

test('choosing the parent option re-fits an explicit output limit to the parent ceiling', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'small');
  item.fields.provider = {value: 'small', inherited_value: 'big', source: 'role',
    inherited_source: 'installation', explicit: true};
  item.fields.max_output_tokens = {value: 4096, inherited_value: 4096, source: 'role',
    inherited_source: 'installation', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'small', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'big', model: '', capabilities: ['text'], max_output_tokens: 1024},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'content.authoring.draft', value: '__inherit__'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved, 'the parent option should still be saveable after the limit is re-fitted');
  assert.equal(saved.values.changes.max_output_tokens, 1024);
  h.page.unmount();
});

test('the inherit checkbox re-fits an explicit output limit like the parent option', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'small');
  item.fields.provider = {value: 'small', inherited_value: 'big', source: 'role',
    inherited_source: 'installation', explicit: true};
  item.fields.max_output_tokens = {value: 4096, inherited_value: 4096, source: 'role',
    inherited_source: 'installation', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'small', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'big', model: '', capabilities: ['text'], max_output_tokens: 1024},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-inherit]', {modelInherit: 'provider',
    modelKind: 'content.authoring.draft', checked: true});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved, 'the checkbox path should re-fit the limit before save');
  assert.equal(saved.values.changes.max_output_tokens, 1024);
  h.page.unmount();
});

test('following a parent whose explicit provider is blank is refused with an explanation', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.provider = {value: 'primary', inherited_value: '', source: 'window',
    inherited_source: 'role', explicit: true};
  data.tasks = [item];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'provider',
    modelKind: 'content.authoring.draft', value: '__inherit__'});
  assert.match(node.innerHTML, /上级（角色默认）保存的主模型是空值/);
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  assert.equal(h.requests.filter(request => request.method === 'POST').length, 0,
    'the refused switch must not turn into a save');
  h.page.unmount();
});

test('the inherit checkbox also refuses a blank parent provider', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.provider = {value: 'primary', inherited_value: '', source: 'window',
    inherited_source: 'role', explicit: true};
  data.tasks = [item];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-inherit]', {modelInherit: 'provider',
    modelKind: 'content.authoring.draft', checked: true});
  assert.match(node.innerHTML, /上级（角色默认）保存的主模型是空值/);
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  assert.equal(h.requests.filter(request => request.method === 'POST').length, 0,
    'the refused checkbox must not turn into a save');
  h.page.unmount();
});

test('bulk restore skips a task whose parent provider is blank', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  const data = status();
  data.tasks[0].fields.provider = {value: 'primary', inherited_value: '', source: 'window',
    inherited_source: 'role', explicit: true};
  requests[0].resolve(data);
  await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.click('[data-model-pick]', {modelPick: 'conversation.reply'});
  node.click('[data-model-action]', {modelAction: 'inherit'});
  assert.match(node.innerHTML, /1 项已跳过：对话回复（上级主模型为空）/);
  page.unmount();
});

test('a fallback cleared by recovery is shown as disabled instead of inherited', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: '', inherited_value: '', source: 'default',
    inherited_source: 'default', explicit: false, cleared: true};
  data.tasks = [item];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  const card = node.innerHTML.split('data-fncard="content.authoring.draft"')[1].split('</article>')[0];
  assert.match(card, /已停用（本层保存的备用与主模型重复）/);
  assert.doesNotMatch(card, /<option value="" selected>不使用备用模型<\/option>/);
  h.page.unmount();
});

test('following an unavailable parent provider is refused with an explanation', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.provider = {value: 'primary', inherited_value: 'removed-id', source: 'window',
    inherited_source: 'role', explicit: true};
  data.tasks = [item];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'provider',
    modelKind: 'content.authoring.draft', value: '__inherit__'});
  assert.match(node.innerHTML, /主模型 removed-id 当前不可用/);
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  assert.equal(h.requests.filter(request => request.method === 'POST').length, 0);
  h.page.unmount();
});

test('a cleared field renders exactly one disabled option', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: '', inherited_value: '', source: 'default',
    inherited_source: 'default', explicit: false, cleared: true};
  data.tasks = [item];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  const card = node.innerHTML.split('data-fncard="content.authoring.draft"')[1].split('</article>')[0];
  assert.equal((card.match(/value="__cleared__"/g) || []).length, 1);
  h.page.unmount();
});

test('following the parent for a cleared fallback resets its stored row', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: '', inherited_value: '', source: 'default',
    inherited_source: 'default', explicit: false, cleared: true};
  data.tasks = [item];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-inherit]', {modelInherit: 'fallback',
    modelKind: 'content.authoring.draft', checked: true});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.deepEqual([...saved.values.reset], ['fallback']);
  assert.equal(Object.hasOwn(saved.values.changes, 'provider'), false);
  h.page.unmount();
});

test('following a parent provider that lacks the task capability is refused', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary', ['text', 'image']);
  item.fields.provider = {value: 'primary', inherited_value: 'text-only', source: 'window',
    inherited_source: 'role', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'primary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'text-only', model: '', capabilities: ['text'], max_output_tokens: 8192},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'provider',
    modelKind: 'content.authoring.draft', value: '__inherit__'});
  assert.match(node.innerHTML, /主模型 text-only 当前不可用/);
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  assert.equal(h.requests.filter(request => request.method === 'POST').length, 0);
  h.page.unmount();
});

test('picking a provider for a cleared fallback shows that choice', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: '', inherited_value: '', source: 'default',
    inherited_source: 'default', explicit: false, cleared: true};
  data.tasks = [item];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'fallback',
    modelKind: 'content.authoring.draft', value: 'secondary'});
  const card = node.innerHTML.split('data-fncard="content.authoring.draft"')[1].split('</article>')[0];
  assert.doesNotMatch(card, /已停用（本层保存的备用/);
  assert.match(card, /<option value="secondary" selected>/);
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.equal(saved.values.changes.fallback, 'secondary');
  h.page.unmount();
});

test('the cleared marker cannot be chosen as a value', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: '', inherited_value: '', source: 'default',
    inherited_source: 'default', explicit: false, cleared: true};
  data.tasks = [item];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  const card = node.innerHTML.split('data-fncard="content.authoring.draft"')[1].split('</article>')[0];
  assert.match(card, /<option value="__cleared__" selected disabled>/);
  h.page.unmount();
});

test('a cleared fallback can be explicitly cleared to no fallback', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: '', inherited_value: 'secondary', source: 'default',
    inherited_source: 'role', explicit: false, cleared: true};
  data.tasks = [item];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'fallback',
    modelKind: 'content.authoring.draft', value: ''});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved, 'explicitly choosing no fallback must be saveable');
  assert.equal(saved.values.changes.fallback, '');
  h.page.unmount();
});

test('a cleared fallback does not count as a configured fallback', async () => {
  const h = harness(), node = h.root(), data = status();
  for (const task of data.tasks) {
    task.fields.provider = {value: 'primary', inherited_value: 'primary', source: 'installation',
      inherited_source: 'installation', explicit: false};
    task.fields.fallback = {value: '', inherited_value: 'secondary', source: 'default',
      inherited_source: 'role', explicit: false, cleared: true};
  }
  h.page.mount(node);
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-sub]', {modelSub: 'map'});
  assert.match(node.innerHTML, /<b>0<\/b><span>已设回退<\/span>/);
  h.page.unmount();
});

test('bulk restore names an unavailable parent as unavailable, not blank', async () => {
  const h = harness(), node = h.root(), data = status();
  data.tasks[0].fields.provider = {value: 'primary', inherited_value: 'removed-id',
    source: 'window', inherited_source: 'role', explicit: true};
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.click('[data-model-pick]', {modelPick: 'conversation.reply'});
  node.click('[data-model-action]', {modelAction: 'inherit'});
  assert.match(node.innerHTML, /对话回复（上级模型不可用）/);
  h.page.unmount();
});

test('a chosen no-fallback survives a configuration refresh', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: '', inherited_value: 'secondary', source: 'default',
    inherited_source: 'role', explicit: false, cleared: true};
  data.tasks = [item];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'fallback',
    modelKind: 'content.authoring.draft', value: ''});
  node.click('[data-model-action]', {modelAction: 'refresh'});
  h.requests.at(-1).resolve(data); await flush();
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved, 'the explicit no-fallback choice must survive the refresh');
  assert.equal(saved.values.changes.fallback, '');
  h.page.unmount();
});

test('a cleared field counts as configured in the map stats', async () => {
  const h = harness(), node = h.root(), data = status();
  /* One task inherits everything except a cleared fallback row; the other is
     fully explicit. Both must count as "configured on this layer". */
  const clearedTask = task('content.authoring.draft', 'primary', ['text'], 'background');
  clearedTask.fields.fallback = {value: '', inherited_value: 'secondary', source: 'installation',
    inherited_source: 'installation', explicit: false, cleared: true};
  data.tasks = [clearedTask, editable('memory.reflect', 'primary')];
  h.page.mount(node);
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-sub]', {modelSub: 'map'});
  assert.match(node.innerHTML, /<b>2<\/b><span>单独设置<\/span>/);
  h.page.unmount();
});

test('changing the primary clears a stored fallback that was disabled', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: '', inherited_value: 'secondary', source: 'default',
    inherited_source: 'role', explicit: false, cleared: true};
  data.tasks = [item];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'provider',
    modelKind: 'content.authoring.draft', value: 'secondary'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.equal(saved.values.changes.provider, 'secondary');
  assert.equal(saved.values.changes.fallback, '');
  h.page.unmount();
});

test('an emptied inherited fallback does not count as a fallback', async () => {
  const h = harness(), node = h.root(), data = status();
  for (const task of data.tasks) {
    task.fields.fallback = {value: '', inherited_value: '', source: 'default',
      inherited_source: 'default', explicit: false, emptied: true};
  }
  h.page.mount(node);
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-sub]', {modelSub: 'map'});
  assert.match(node.innerHTML, /<b>0<\/b><span>已设回退<\/span>/);
  h.page.unmount();
});

test('an inherited fallback shows the route that is actually used', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.max_output_tokens = {value: 4096, inherited_value: 4096, source: 'window',
    inherited_source: 'installation', explicit: true};
  item.fields.fallback = {value: 'secondary', inherited_value: '', source: 'host_conversation',
    inherited_source: 'default', explicit: false};
  data.tasks = [item];
  data.providers = [
    {id: 'primary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'secondary', model: '', capabilities: ['text'], max_output_tokens: 1024},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  const card = node.innerHTML.split('data-fncard="content.authoring.draft"')[1].split('</article>')[0];
  assert.match(card, /<option value="secondary" selected>/);
  assert.doesNotMatch(card, /<option value="" selected>不使用备用模型<\/option>/);
  /* The output limit is checked against the fallback dispatch actually uses. */
  assert.match(card, /1024/);
  h.page.unmount();
});

test('an emptied fallback is not shown as an inherited fallback', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.max_output_tokens = {value: 4096, inherited_value: 4096, source: 'window',
    inherited_source: 'installation', explicit: true};
  item.fields.fallback = {value: '', inherited_value: 'secondary', source: 'default',
    inherited_source: 'host_conversation', explicit: false, emptied: true};
  data.tasks = [item];
  data.providers = [
    {id: 'primary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'secondary', model: '', capabilities: ['text'], max_output_tokens: 1024},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  const card = node.innerHTML.split('data-fncard="content.authoring.draft"')[1].split('</article>')[0];
  assert.match(card, /<option value="">不使用备用模型<\/option>/);
  assert.doesNotMatch(card, /<option value="secondary" selected>/);
  /* The emptied fallback must not cap the output limit either. */
  assert.doesNotMatch(card, /1024/);
  h.page.unmount();
});

test('switching an explicit fallback to follow the parent uses the parent ceiling', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: 'local-fb', inherited_value: 'parent-fb', source: 'window',
    inherited_source: 'role', explicit: true,
    parent_stored: 'parent-fb', parent_stored_usable: true};
  item.fields.max_output_tokens = {value: 4096, inherited_value: 4096, source: 'window',
    inherited_source: 'installation', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'primary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'local-fb', model: '', capabilities: ['text'], max_output_tokens: 1024},
    {id: 'parent-fb', model: '', capabilities: ['text'], max_output_tokens: 8192},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-inherit]', {modelInherit: 'fallback',
    modelKind: 'content.authoring.draft', checked: true});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved);
  assert.deepEqual([...saved.values.reset], ['fallback']);
  assert.equal(Object.hasOwn(saved.values.changes, 'max_output_tokens'), false);
  h.page.unmount();
});

test('a cleared field alone does not make the draft dirty', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: '', inherited_value: 'secondary', source: 'default',
    inherited_source: 'role', explicit: false, cleared: true};
  data.tasks = [item];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  const card = node.innerHTML.split('data-fncard="content.authoring.draft"')[1].split('</article>')[0];
  assert.match(card, /data-model-action="save-one"[^>]*disabled/);
  h.page.unmount();
});

test('a substituted fallback names the stored row it replaced', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: '', inherited_value: '', source: 'default',
    inherited_source: 'default', explicit: false, replaced: true, stored_value: 'primary'};
  item.fields.fallback.value = 'secondary';
  item.fields.fallback.inherited_value = 'secondary';
  data.tasks = [item];
  data.providers = [
    {id: 'primary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'secondary', model: '', capabilities: ['text'], max_output_tokens: 8192},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  const card = node.innerHTML.split('data-fncard="content.authoring.draft"')[1].split('</article>')[0];
  assert.match(card, /本层保存的 primary 已被恢复替换/);
  h.page.unmount();
});

test('following the parent for a cleared fallback uses the parent ceiling', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: '', inherited_value: 'secondary', source: 'default',
    inherited_source: 'role', explicit: false, cleared: true, emptied: true,
    parent_stored: 'secondary', parent_stored_usable: true};
  item.fields.max_output_tokens = {value: 4096, inherited_value: 4096, source: 'window',
    inherited_source: 'installation', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'primary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'secondary', model: '', capabilities: ['text'], max_output_tokens: 1024},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-inherit]', {modelInherit: 'fallback',
    modelKind: 'content.authoring.draft', checked: true});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved);
  assert.deepEqual([...saved.values.reset], ['fallback']);
  assert.equal(saved.values.changes.max_output_tokens, 1024);
  h.page.unmount();
});

test('a replaced field counts as configured and can be reset', async () => {
  const h = harness(), node = h.root(), data = status();
  const clearedTask = task('content.authoring.draft', 'primary', ['text'], 'background');
  clearedTask.fields.fallback = {value: 'secondary', inherited_value: 'secondary', source: 'default',
    inherited_source: 'default', explicit: false, replaced: true, stored_value: 'primary'};
  data.tasks = [clearedTask, editable('memory.reflect', 'primary')];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-sub]', {modelSub: 'map'});
  assert.match(node.innerHTML, /<b>2<\/b><span>单独设置<\/span>/);
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-inherit]', {modelInherit: 'fallback',
    modelKind: 'content.authoring.draft', checked: true});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved);
  assert.deepEqual([...saved.values.reset], ['fallback']);
  h.page.unmount();
});

test('following the parent for a replaced fallback uses the parent ceiling', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: 'replaced-fb', inherited_value: 'parent-fb', source: 'default',
    inherited_source: 'role', explicit: false, replaced: true, stored_value: 'primary'};
  item.fields.max_output_tokens = {value: 4096, inherited_value: 4096, source: 'window',
    inherited_source: 'installation', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'primary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'replaced-fb', model: '', capabilities: ['text'], max_output_tokens: 1024},
    {id: 'parent-fb', model: '', capabilities: ['text'], max_output_tokens: 8192},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-inherit]', {modelInherit: 'fallback',
    modelKind: 'content.authoring.draft', checked: true});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved);
  assert.deepEqual([...saved.values.reset], ['fallback']);
  assert.equal(Object.hasOwn(saved.values.changes, 'max_output_tokens'), false);
  h.page.unmount();
});

test('changing the primary clears a replaced fallback row', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: 'replaced-fb', inherited_value: 'replaced-fb', source: 'default',
    inherited_source: 'default', explicit: false, replaced: true, stored_value: 'primary'};
  data.tasks = [item];
  data.providers = [
    {id: 'primary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'secondary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'replaced-fb', model: '', capabilities: ['text'], max_output_tokens: 8192},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'provider',
    modelKind: 'content.authoring.draft', value: 'secondary'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.equal(saved.values.changes.fallback, 'replaced-fb',
    'the fallback the card shows must survive the primary change');
  h.page.unmount();
});

test('an emptied fallback returns to the ceiling when the primary moves', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: '', inherited_value: '', source: 'default',
    inherited_source: 'default', explicit: false, emptied: true, stored_value: 'secondary'};
  item.fields.max_output_tokens = {value: 4096, inherited_value: 4096, source: 'window',
    inherited_source: 'installation', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'primary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'big', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'secondary', model: '', capabilities: ['text'], max_output_tokens: 1024},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'provider',
    modelKind: 'content.authoring.draft', value: 'big'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved);
  assert.equal(saved.values.changes.max_output_tokens, 1024);
  h.page.unmount();
});

test('an emptied fallback uses the recovered route when the primary moves', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: '', inherited_value: '', source: 'default',
    inherited_source: 'default', explicit: false, emptied: true,
    stored_value: 'secondary', recovered_value: 'big'};
  item.fields.max_output_tokens = {value: 4096, inherited_value: 4096, source: 'window',
    inherited_source: 'installation', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'primary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'big', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'big2', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'secondary', model: '', capabilities: ['text'], max_output_tokens: 1024},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'provider',
    modelKind: 'content.authoring.draft', value: 'big2'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved);
  assert.equal(Object.hasOwn(saved.values.changes, 'max_output_tokens'), false);
  h.page.unmount();
});

test('an emptied fallback matching the new primary is not shown as a fallback', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: '', inherited_value: '', source: 'default',
    inherited_source: 'default', explicit: false, emptied: true,
    stored_value: 'secondary', recovered_value: 'big'};
  item.fields.max_output_tokens = {value: 4096, inherited_value: 4096, source: 'window',
    inherited_source: 'installation', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'primary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'big', model: '', capabilities: ['text'], max_output_tokens: 8192},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'provider',
    modelKind: 'content.authoring.draft', value: 'big'});
  const card = node.innerHTML.split('data-fncard="content.authoring.draft"')[1].split('</article>')[0];
  const fallbackSelect = card.split('data-model-field="fallback"')[1].split('</select>')[0];
  assert.doesNotMatch(fallbackSelect, /value="big" selected/);
  assert.match(fallbackSelect, /<option value="">不使用备用模型<\/option>/);
  h.page.unmount();
});

test('resetting an explicit fallback shows the parent stored value', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: 'fb', inherited_value: 'sub-fb', parent_stored: 'stored-fb',
    source: 'window', inherited_source: 'role', explicit: true, parent_stored_usable: true};
  item.fields.max_output_tokens = {value: 4096, inherited_value: 4096, source: 'window',
    inherited_source: 'installation', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'primary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'fb', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'sub-fb', model: '', capabilities: ['text'], max_output_tokens: 1024},
    {id: 'stored-fb', model: '', capabilities: ['text'], max_output_tokens: 8192},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-inherit]', {modelInherit: 'fallback',
    modelKind: 'content.authoring.draft', checked: true});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved);
  assert.deepEqual([...saved.values.reset], ['fallback']);
  assert.equal(Object.hasOwn(saved.values.changes, 'max_output_tokens'), false);
  h.page.unmount();
});

test('following the parent for the primary revives an emptied fallback', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'S');
  item.fields.provider = {value: 'S', inherited_value: 'P', source: 'window',
    inherited_source: 'role', explicit: true};
  item.fields.fallback = {value: '', inherited_value: '', source: 'default',
    inherited_source: 'default', explicit: false, emptied: true, recovered_value: 'S'};
  item.fields.max_output_tokens = {value: 100, inherited_value: 100, source: 'window',
    inherited_source: 'installation', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'S', model: '', capabilities: ['text'], max_output_tokens: 50},
    {id: 'P', model: '', capabilities: ['text'], max_output_tokens: 8192},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'provider',
    modelKind: 'content.authoring.draft', value: '__inherit__'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved);
  assert.equal(saved.values.changes.max_output_tokens, 50);
  h.page.unmount();
});

test('an inherited primary does not use the parent stored fallback on reset', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'S');
  item.fields.provider = {value: 'S', inherited_value: 'P', source: 'installation',
    inherited_source: 'installation', explicit: false};
  item.fields.fallback = {value: 'P', inherited_value: '', parent_stored: 'S',
    source: 'window', inherited_source: 'role', explicit: true};
  item.fields.max_output_tokens = {value: 100, inherited_value: 100, source: 'window',
    inherited_source: 'installation', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'S', model: '', capabilities: ['text'], max_output_tokens: 50},
    {id: 'P', model: '', capabilities: ['text'], max_output_tokens: 8192},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-inherit]', {modelInherit: 'fallback',
    modelKind: 'content.authoring.draft', checked: true});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved);
  assert.deepEqual([...saved.values.reset], ['fallback']);
  assert.equal(Object.hasOwn(saved.values.changes, 'max_output_tokens'), false);
  h.page.unmount();
});

test('resetting a fallback with an unusable parent row leaves it unnamed', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: 'fb', inherited_value: '', parent_stored: 'removed-fb',
    parent_stored_usable: false, source: 'window', inherited_source: 'role', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'primary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'fb', model: '', capabilities: ['text'], max_output_tokens: 8192},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-inherit]', {modelInherit: 'fallback',
    modelKind: 'content.authoring.draft', checked: true});
  const card = node.innerHTML.split('data-fncard="content.authoring.draft"')[1].split('</article>')[0];
  assert.match(card, /跟随上级（保存后按当前主模型解析）/);
  assert.doesNotMatch(card, /value="removed-fb" selected/);
  h.page.unmount();
});

test('confirming a replaced fallback value saves it explicitly', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: 'secondary', inherited_value: 'secondary', source: 'default',
    inherited_source: 'default', explicit: false, replaced: true, stored_value: 'primary'};
  data.tasks = [item];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'fallback',
    modelKind: 'content.authoring.draft', value: 'secondary'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved);
  assert.equal(saved.values.changes.fallback, 'secondary');
  h.page.unmount();
});

test('an inherited replaced fallback uses the parent stored row when the primary moves', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: 'task-default', inherited_value: 'task-default', source: 'default',
    inherited_source: 'installation', explicit: false,
    parent_stored: 'host-fb', parent_stored_usable: true};
  item.fields.max_output_tokens = {value: 4096, inherited_value: 4096, source: 'window',
    inherited_source: 'installation', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'primary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'big', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'task-default', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'host-fb', model: '', capabilities: ['text'], max_output_tokens: 1024},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'provider',
    modelKind: 'content.authoring.draft', value: 'big'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved);
  assert.equal(saved.values.changes.max_output_tokens, 1024);
  h.page.unmount();
});

test('a stale inherited fallback keeps the recovered route when the primary moves', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'primary');
  item.fields.fallback = {value: 'recovered-fb', inherited_value: 'recovered-fb', source: 'default',
    inherited_source: 'installation', explicit: false,
    parent_stored: 'removed-fb', parent_stored_usable: false};
  item.fields.max_output_tokens = {value: 4096, inherited_value: 4096, source: 'window',
    inherited_source: 'installation', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'primary', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'big', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'recovered-fb', model: '', capabilities: ['text'], max_output_tokens: 1000},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'provider',
    modelKind: 'content.authoring.draft', value: 'big'});
  node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'content.authoring.draft'});
  const saved = h.requests.find(request => request.method === 'POST');
  assert.ok(saved);
  assert.equal(saved.values.changes.max_output_tokens, 1000);
  h.page.unmount();
});

test('following the parent for both fields leaves a colliding fallback unnamed', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'S');
  item.fields.provider = {value: 'S', inherited_value: 'H', source: 'window',
    inherited_source: 'host_conversation', explicit: true};
  item.fields.fallback = {value: 'FB', inherited_value: '', parent_stored: 'H',
    parent_stored_usable: true, source: 'window', inherited_source: 'installation', explicit: true};
  data.tasks = [item];
  data.providers = [
    {id: 'S', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'H', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'FB', model: '', capabilities: ['text'], max_output_tokens: 8192},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'provider',
    modelKind: 'content.authoring.draft', value: '__inherit__'});
  node.change('[data-model-inherit]', {modelInherit: 'fallback',
    modelKind: 'content.authoring.draft', checked: true});
  const card = node.innerHTML.split('data-fncard="content.authoring.draft"')[1].split('</article>')[0];
  assert.match(card, /跟随上级（保存后按当前主模型解析）/);
  assert.doesNotMatch(card, /<option value="H" selected>/);
  h.page.unmount();
});

test('a recovered parent primary keeps an inherited fallback unnamed', async () => {
  const h = harness(), node = h.root(), data = status();
  const item = editable('content.authoring.draft', 'S');
  item.fields.provider = {value: 'S', inherited_value: 'H', source: 'window',
    inherited_source: 'host_conversation', explicit: true};
  item.fields.fallback = {value: 'H', inherited_value: 'H', source: 'installation',
    inherited_source: 'installation', explicit: false,
    parent_stored: 'H', parent_stored_usable: true};
  data.tasks = [item];
  data.providers = [
    {id: 'S', model: '', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'H', model: '', capabilities: ['text'], max_output_tokens: 8192},
  ];
  h.page.mount(node, {sub: 'cfg'});
  h.requests[0].resolve(data); await flush();
  node.click('[data-model-open]', {modelOpen: 'content.authoring.draft'});
  node.change('[data-model-field]', {modelField: 'provider',
    modelKind: 'content.authoring.draft', value: '__inherit__'});
  const card = node.innerHTML.split('data-fncard="content.authoring.draft"')[1].split('</article>')[0];
  assert.match(card, /跟随上级（保存后按当前主模型解析）/);
  h.page.unmount();
});

test('main panel Laya logs default to all sources, switch to current, and show scope/action summary', async () => {
  const {page, node, requests} = await openLayaPage();
  const initialLogRead = requests.find(item => item.method === 'GET' && item.route === 'model/laya/logs');
  assert.equal(initialLogRead.params?.scope, 'all');
  assert.match(node.innerHTML, /data-laya-logs-scope="all"[^>]*aria-pressed="true"/);
  assert.match(node.innerHTML, /data-laya-logs-scope="current"[^>]*aria-pressed="false"/);

  node.click('[data-laya-logs-scope]', {layaLogsScope: 'current'});
  const currentRead = requests.filter(item => item.method === 'GET' && item.route === 'model/laya/logs').at(-1);
  assert.equal(currentRead.params?.scope, 'current');
  currentRead.resolve({
    scope: 'current',
    events: [{
      status: 'ok', source: 'conversation', mode: 'remote', elapsed_ms: 145,
      decision: {action: 'silent'}, processing: {action_confidence: 0.824},
      scope_info: {kind: 'group', native_scope_id: '103111519'},
      observed_at: new Date().toISOString(),
    }],
  });
  await flush();
  assert.match(node.innerHTML, /当前会话 · 最近 50 条/);
  assert.match(node.innerHTML, /群聊 103…519/);
  assert.match(node.innerHTML, /action=silent \(82\.4%\)/);

  node.click('[data-laya-logs-scope]', {layaLogsScope: 'all'});
  const allRead = requests.filter(item => item.method === 'GET' && item.route === 'model/laya/logs').at(-1);
  assert.equal(allRead.params?.scope, 'all');
  allRead.resolve({
    scope: 'all',
    events: [{
      status: 'ok', source: 'role_intervention', mode: 'remote', elapsed_ms: 90,
      decision: {intervention_need: 'none'},
      scope_info: {kind: 'private', native_scope_id: '153000127'},
      observed_at: new Date().toISOString(),
    }],
  });
  await flush();
  assert.match(node.innerHTML, /全部来源 · 最近 50 条/);
  assert.match(node.innerHTML, /私聊 153…127/);
  page.unmount();
});

test('group Laya page shows scoped group logs without scope switcher when available', async () => {
  const h = harness(), node = h.root();
  h.page.mount(node, {endpoint: '/astrbot_plugin_private_companion/host/targets/group/roles/role/model'});
  h.requests[0].resolve({...status(), scope_kind: 'group'});
  await flush();
  node.click('[data-model-tab]', {modelTab: 'laya'});
  h.requests.find(item => item.route.endsWith('/laya')).resolve(layaRemoteStatus());
  const logRead = h.requests.find(item => item.route.endsWith('/laya/logs'));
  assert.equal(logRead.params, undefined, 'group laya/logs must not pass query params');
  logRead.resolve({
    available: true,
    scope_kind: 'group',
    events: [{
      status: 'ok', source: 'conversation', mode: 'remote', elapsed_ms: 112,
      decision: {action: 'reply'}, processing: {action_confidence: 0.91},
      scope_info: {kind: 'group', native_scope_id: '103111519'},
      observed_at: new Date().toISOString(),
    }],
  });
  await flush();

  assert.match(node.innerHTML, /本群运行记录 · 最近 50 条/);
  assert.match(node.innerHTML, /群聊 103…519/);
  assert.match(node.innerHTML, /action=reply \(91\.0%\)/);
  assert.doesNotMatch(node.innerHTML, /data-laya-logs-scope/);
  h.page.unmount();
});

test('Laya test and preview surface 401/403 auth summary from ok-enveloped bridge failure payloads', async () => {
  const {page, node, requests} = await openLayaPage();
  node.click('[data-laya-test]');
  requests.find(item => item.method === 'POST' && item.route === 'model/laya/test').resolve({
    status: 'failed',
    code: 'laya_auth_failed',
    reason: 'Laya HTTP 403 · MODEL_ACCESS_DENIED: 当前模型仅对灰度用户开放',
  });
  await flush();
  settleLayaRefresh(requests, []);
  await flush();
  assert.match(node.innerHTML, /Laya HTTP 403/);
  assert.match(node.innerHTML, /服务端拒绝访问（403 Forbidden）/);
  assert.match(node.innerHTML, /服务端原文：MODEL_ACCESS_DENIED: 当前模型仅对灰度用户开放/);

  node.input('[data-laya-preview]', {value: 'hello'});
  node.click('[data-laya-decide]');
  requests.find(item => item.method === 'POST' && item.route === 'model/laya/decide').resolve({
    status: 'failed',
    code: 'laya_auth_failed',
    reason: 'Laya HTTP 401 · invalid_api_key: Incorrect API key provided',
  });
  await flush();
  settleLayaRefresh(requests, []);
  await flush();
  assert.match(node.innerHTML, /Laya HTTP 401/);
  assert.match(node.innerHTML, /鉴权失败（401 Unauthorized）/);
  assert.match(node.innerHTML, /服务端原文：invalid_api_key: Incorrect API key provided/);
  page.unmount();
});

test('video narration accepts OpenAI video relay providers alongside native video models and saves fallback', async () => {
  const h = harness(), node = h.root(), data = status();
  data.tasks.push(editable('conversation.video_narration', 'gemini-a', ['text', 'video'], 'background'));
  data.providers.push(
    {id: 'gemini-a', model: 'gemini-3.8-flash', capabilities: ['text', 'video'], max_output_tokens: 8192},
    {id: 'relay-vision', model: 'gpt-4o-video-relay', capabilities: ['text', 'image'], supports_video_relay: true, max_output_tokens: 8192},
    {id: 'text-only', model: 'plain-llm', capabilities: ['text'], supports_video_relay: false, max_output_tokens: 8192},
  );
  h.page.mount(node);
  try {
    h.requests[0].resolve(data);
    await flush();
    node.click('[data-model-sub]', {modelSub: 'cfg'});
    node.click('[data-model-cm]', {modelCm: 'precise'});
    node.click('[data-model-view]', {modelView: 'providers'});
    assert.match(node.innerHTML, /视觉转接视频/);
    node.click('[data-model-view]', {modelView: 'tasks'});
    node.click('[data-model-open]', {modelOpen: 'conversation.video_narration'});
    const card = node.innerHTML.split('data-fncard="conversation.video_narration"')[1].split('</article>')[0];
    assert.match(card, /<option value="gemini-a" selected>gemini-a · gemini-3\.8-flash<\/option>/);
    assert.match(card, /<option value="relay-vision"[^>]*>relay-vision · gpt-4o-video-relay<\/option>/);
    assert.doesNotMatch(card, /<option value="primary"|<option value="secondary"|<option value="text-only"/);
    node.change('[data-model-field]', {modelField: 'fallback', modelKind: 'conversation.video_narration', value: 'relay-vision'});
    node.click('[data-model-action]', {modelAction: 'save-one', modelKind: 'conversation.video_narration'});
    const post = h.requests.find(request => request.method === 'POST');
    assert.deepEqual({...post.values.changes}, {fallback: 'relay-vision'});
    const savedTask = editable('conversation.video_narration', 'gemini-a', ['text', 'video'], 'background');
    savedTask.fields.fallback = {value: 'relay-vision', inherited_value: null, explicit: true};
    post.resolve({replayed: false, task: savedTask});
    await flush();
  } finally {
    h.page.unmount();
  }
});

test('global defaults card edits, validates collision, tracks dirty state, and saves via model/defaults', async () => {
  const h = harness(), node = h.root(), data = status();
  data.global_defaults = {
    provider_id: 'primary',
    fallback_provider_id: '',
    effective_provider_id: 'primary',
    effective_fallback_provider_id: '',
    editable: true,
  };
  h.page.mount(node);
  try {
    h.requests[0].resolve(data);
    await flush();
    assert.match(node.innerHTML, /陪伴通用模型（全局默认）/);
    assert.equal(h.page.isDirty(), false);
    // Picking fallback identical to primary triggers collision alert and disables save
    node.change('[data-global-default-field]', {globalDefaultField: 'fallback_provider_id', value: 'primary'});
    assert.match(node.innerHTML, /全局备用模型不能与全局主模型相同/);
    assert.match(node.innerHTML, /data-global-default-save[^>]*disabled/);
    assert.equal(h.page.isDirty(), true);
    assert.ok(h.page.changedFields().some(item => item.includes('陪伴通用模型')));
    // Choosing distinct fallback enables save and posts to model/defaults
    node.change('[data-global-default-field]', {globalDefaultField: 'fallback_provider_id', value: 'secondary'});
    assert.doesNotMatch(node.innerHTML, /data-global-default-save[^>]*disabled/);
    node.click('[data-global-default-save]');
    const post = h.requests.find(r => r.method === 'POST' && r.route === 'model/defaults');
    assert.ok(post, 'should POST to model/defaults');
    assert.deepEqual({...post.values}, {provider_id: 'primary', fallback_provider_id: 'secondary'});
    assert.equal(h.page.isBusy(), true);
    post.resolve({
      saved: true,
      restart_required: true,
      fallback_cleared_duplicate: false,
      global_defaults: {
        provider_id: 'primary',
        fallback_provider_id: 'secondary',
        effective_provider_id: 'primary',
        effective_fallback_provider_id: 'secondary',
        editable: true,
      },
    });
    await flush();
    const silentRefresh = h.requests.filter(r => r.method === 'GET' && r.route === 'model').at(-1);
    assert.ok(silentRefresh && silentRefresh !== h.requests[0], 'saveGlobalDefaults should silently re-read GET model');
    const refreshed = status();
    refreshed.global_defaults = {
      provider_id: 'primary',
      fallback_provider_id: 'secondary',
      effective_provider_id: 'primary',
      effective_fallback_provider_id: 'secondary',
      editable: true,
    };
    refreshed.tasks[1].fields.fallback = {
      value: 'secondary', inherited_value: 'secondary', source: 'installation', inherited_source: 'installation', explicit: false,
    };
    silentRefresh.resolve(refreshed);
    await flush();
    assert.equal(h.page.isBusy(), false);
    assert.equal(h.page.isDirty(), false);
    assert.match(node.innerHTML, /重启插件后新全局默认将完全生效/);
    const reflectRow = node.innerHTML.split('data-task-row="memory.reflect"')[1].split('</li>')[0];
    assert.match(reflectRow, /secondary/, 'inherited task row should immediately show updated global fallback');
  } finally {
    h.page.unmount();
  }
});

test('quick group assign supports 4-tier safe fallback, primary inherit reset, and collision guard', async () => {
  const h = harness(), node = h.root(), data = status();
  data.tasks = [
    editable('conversation.poke_reply', 'primary'),
    editable('conversation.image_narration', 'secondary', ['text', 'image']),
  ];
  data.tasks[0].fields.fallback = {value: 'secondary', inherited_value: '', source: 'role', inherited_source: 'default', explicit: true};
  data.providers = [
    {id: 'primary', model: 'm1', capabilities: ['text'], max_output_tokens: 8192},
    {id: 'secondary', model: 'm2', capabilities: ['text', 'image'], max_output_tokens: 8192},
    {id: 'backup', model: 'm3', capabilities: ['text', 'image'], max_output_tokens: 8192},
  ];
  h.page.mount(node);
  try {
    h.requests[0].resolve(data);
    await flush();
    // Collapsed by default; expand quick group assign
    assert.doesNotMatch(node.innerHTML, /data-model-slot="对话"/);
    node.click('[data-model-quick-toggle]');
    assert.match(node.innerHTML, /data-model-slot="对话"/);
    assert.match(node.innerHTML, /data-model-slot-fallback="对话"/);
    assert.match(node.innerHTML, /保持各任务现有备用不变/);
    assert.match(node.innerHTML, /data-model-slot-apply="对话"[^>]*disabled/);

    // Collision between primary & fallback disables Apply
    node.change('[data-model-slot]', {modelSlot: '对话', value: 'backup'});
    node.change('[data-model-slot-fallback]', {modelSlotFallback: '对话', value: 'backup'});
    assert.match(node.innerHTML, /主模型与备用模型不能相同/);
    assert.match(node.innerHTML, /data-model-slot-apply="对话"[^>]*disabled/);

    // Fallback-only update ("仅应用备用模型"): keeps primary untouched, skips primary===fallback collision
    node.change('[data-model-slot]', {modelSlot: '对话', value: '__keep__'});
    node.change('[data-model-slot-fallback]', {modelSlotFallback: '对话', value: 'secondary'});
    assert.match(node.innerHTML, /仅应用备用模型/);
    node.click('[data-model-slot-apply]', {modelSlotApply: '对话'});
    // poke_reply already has fallback=secondary (not dirty), image_narration has primary=secondary (collides -> skipped)
    assert.equal(h.requests.filter(r => r.method === 'POST').length, 0);
    assert.match(node.innerHTML, /当前图片转述（备用与主模型 secondary 相同）/);

    // Clear fallback to __none__ across group -> explicitly sets fallback=""
    node.change('[data-model-slot-fallback]', {modelSlotFallback: '对话', value: '__none__'});
    node.click('[data-model-slot-apply]', {modelSlotApply: '对话'});
    const nonePosts = [];
    for (let k = 0; k < 2; k++) {
      const post = h.requests.at(-1);
      if (post.method !== 'POST') break;
      nonePosts.push(post);
      assert.equal(post.values.changes.fallback, '');
      assert.equal(Object.hasOwn(post.values.changes, 'provider'), false, 'must not touch primary when __keep__');
      post.resolve({replayed: false, task: editable(post.values.kind, post.values.kind === 'conversation.poke_reply' ? 'primary' : 'secondary', post.values.kind === 'conversation.image_narration' ? ['text', 'image'] : ['text'])});
      await flush();
    }
    assert.equal(nonePosts.length, 2);

    // Primary __inherit__ resets explicit provider cleanly (fixing old disabled-button bug)
    node.change('[data-model-slot]', {modelSlot: '对话', value: '__inherit__'});
    node.change('[data-model-slot-fallback]', {modelSlotFallback: '对话', value: '__keep__'});
    assert.match(node.innerHTML, /仅应用主模型/);
    assert.doesNotMatch(node.innerHTML, /data-model-slot-apply="对话"[^>]*disabled/);
    node.click('[data-model-slot-apply]', {modelSlotApply: '对话'});
    const resetPost = h.requests.at(-1);
    assert.equal(resetPost.method, 'POST');
    assert.ok(resetPost.values.reset.includes('provider'));
    resetPost.resolve({replayed: false, task: task(resetPost.values.kind, 'primary')});
    await flush();
    const secondReset = h.requests.at(-1);
    if (secondReset !== resetPost && secondReset.method === 'POST') {
      secondReset.resolve({replayed: false, task: task(secondReset.values.kind, 'primary', ['text', 'image'])});
      await flush();
    }
  } finally {
    h.page.unmount();
  }
});

test('inspector drawer replaces cockpit checkboxes with inline restore buttons, stepper, policy jump and keyboard nav', async () => {
  const docHandlers = {};
  const requests = [];
  const store = new Map();
  const win = {
    CompanionProduct: {
      hosted: () => true,
      role: () => ({model_api: '/astrbot_plugin_private_companion/model'}),
      identity: () => ({installation_id: 'inst', operator_id: 'operator'}),
      bridgeNow: () => win.AstrBotPluginPage,
      bridgeFor: () => Promise.resolve(win.AstrBotPluginPage),
    },
    AstrBotPluginPage: {
      apiGet: (route, params, options) => new Promise((resolve, reject) => requests.push({method: 'GET', route, params, options, resolve, reject})),
      apiPost: (route, values, options) => new Promise((resolve, reject) => requests.push({method: 'POST', route, values, options, resolve, reject})),
    },
    sessionStorage: {getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: k => store.delete(k)},
    crypto: {randomUUID: () => 'k-' + requests.length},
  };
  const doc = {
    addEventListener(type, fn) { docHandlers[type] = fn; },
    removeEventListener(type) { delete docHandlers[type]; },
  };
  vm.runInNewContext(workbench, {window: win, document: doc, setTimeout, clearTimeout});
  vm.runInNewContext(source, {window: win, document: doc, crypto: win.crypto, AbortController, setTimeout, clearTimeout});
  const handlers = {};
  const node = {
    isConnected: true, innerHTML: '',
    addEventListener(type, fn) { handlers[type] = fn; },
    removeEventListener(type) { delete handlers[type]; },
    querySelectorAll() { return []; }, querySelector() { return null; },
    click(sel, dataset) {
      handlers.click({type: 'click', target: {dataset: dataset || {}, matches: c => c === sel, closest: c => c === sel ? {dataset: dataset || {}} : null}});
    },
  };
  win.CompanionModelsPage.mount(node);
  try {
    requests[0].resolve(status());
    await flush();
    // Cockpit cards are NOT mounted until a task is opened
    assert.doesNotMatch(node.innerHTML, /data-fncard=/);
    assert.match(node.innerHTML, /data-task-row="conversation\.reply"/);
    assert.match(node.innerHTML, /data-task-row="memory\.reflect"/);

    // Open first task -> Inspector Drawer renders without 9-checkbox .model-inh matrix
    node.click('[data-model-open]', {modelOpen: 'conversation.reply'});
    assert.match(node.innerHTML, /data-fncard="conversation\.reply"/);
    assert.doesNotMatch(node.innerHTML, /class="model-inh"/);
    assert.match(node.innerHTML, /data-model-inherit-reset="provider"/);
    assert.match(node.innerHTML, /data-field-state="fallback" data-inherited="true"/);

    // Inline ↺ 恢复继承 resets provider override directly
    node.click('[data-model-inherit-reset]', {modelInheritReset: 'provider', modelKind: 'conversation.reply'});
    assert.match(node.innerHTML, /data-field-state="provider" data-inherited="true"/);

    // Stepper & ArrowDown advance to memory.reflect; Escape closes drawer
    docHandlers.keydown({key: 'ArrowDown', target: {tagName: 'BODY'}, preventDefault() {}});
    assert.match(node.innerHTML, /data-fncard="memory\.reflect"/);
    docHandlers.keydown({key: 'ArrowUp', target: {tagName: 'BODY'}, preventDefault() {}});
    assert.match(node.innerHTML, /data-fncard="conversation\.reply"/);
    // ArrowDown while typing in textarea must NOT step away
    docHandlers.keydown({key: 'ArrowDown', target: {tagName: 'TEXTAREA'}, preventDefault() {}});
    assert.match(node.innerHTML, /data-fncard="conversation\.reply"/);
    docHandlers.keydown({key: 'Escape', target: {tagName: 'BODY'}, preventDefault() {}});
    assert.doesNotMatch(node.innerHTML, /data-fncard=/);

    // Tier 3 policy link jumps straight to Replacement Policy for that task
    node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
    node.click('[data-open-policy]', {openPolicy: 'memory.reflect'});
    assert.match(node.innerHTML, /data-model-pol-task/);
    assert.match(node.innerHTML, /<option value="memory\.reflect" selected>/);
  } finally {
    win.CompanionModelsPage.unmount();
    assert.equal(docHandlers.keydown, undefined, 'unmount must remove document keydown listener');
  }
});

test('multi-select dock assigns fallback across selected tasks with collision protection', async () => {
  const h = harness(), node = h.root(), data = status();
  data.tasks[1].fields.provider.value = 'secondary';
  h.page.mount(node);
  try {
    h.requests[0].resolve(data);
    await flush();
    node.click('[data-model-pick]', {modelPick: 'conversation.reply'});
    node.click('[data-model-pick]', {modelPick: 'memory.reflect'});
    assert.match(node.innerHTML, /data-model-assign-fallback/);
    node.change('[data-model-assign-fallback]', {value: 'secondary'});
    assert.match(node.innerHTML, /已将 1 项任务的备用模型设为「secondary」/);
    assert.match(node.innerHTML, /事实整理（与主模型相同）/);
  } finally {
    h.page.unmount();
  }
});

test('image schema retries after failure and dirty image config refresh reports a clear notice', async () => {
  const h = harness(), node = h.root();
  h.page.mount(node);
  try {
    h.requests[0].resolve(status());
    await flush();
    node.click('[data-model-tab]', {modelTab: 'image'});
    const firstSchema = h.requests.find(r => r.route === 'model/image-schema');
    assert.ok(firstSchema);
    firstSchema.reject(new Error('network_error'));
    h.requests.find(r => r.route === 'model/image').resolve({
      actions: [], enabled: {'image.generate': true, 'image.edit': false, 'image.mask_edit': false},
    });
    await flush();
    const outReq = h.requests.find(r => r.route === 'model/image/output');
    outReq.resolve({
      revision: 'img-r1',
      settings: {enabled: false, backend: 'auto', endpoints: [], comfyui: {}, sd_webui: {},
        operation_routes: {generate: [], edit: []}},
    });
    await flush();

    // Dirty the image config draft and click refresh -> shows non-destructive notice instead of silent no-op
    node.change('[data-img-config-field]', {imgConfigField: 'enabled', checked: true});
    const outCountBefore = h.requests.filter(r => r.route === 'model/image/output').length;
    node.click('[data-img-config-refresh]');
    assert.equal(h.requests.filter(r => r.route === 'model/image/output').length, outCountBefore);
    assert.match(node.innerHTML, /当前有未保存的生图配置草稿，已保留本地修改未覆盖/);

    // Clicking image tab refresh retries failed image-schema load
    node.click('[data-img-refresh]');
    const schemaReqs = h.requests.filter(r => r.route === 'model/image-schema');
    assert.equal(schemaReqs.length, 2, 'failed image-schema must retry on refresh');
    schemaReqs[1].resolve({image_provider_group: {sd_webui: {label: 'SD WebUI', fields: {}}}});
    h.requests.filter(r => r.route === 'model/image').at(-1).resolve({
      actions: [], enabled: {'image.generate': true, 'image.edit': false, 'image.mask_edit': false},
    });
    await flush();
  } finally {
    h.page.unmount();
  }
});

test('inspector field labels never wrap inherit-reset buttons and bind via explicit for/id pairs', async () => {
  const h = harness(), node = h.root(), data = status();
  const coreTask = editable('conversation.reply', 'primary');
  coreTask.fields.prompt = {value: '自定义核心提示', inherited_value: '', source: 'role', inherited_source: 'default', explicit: true};
  const normalTask = editable('memory.reflect', 'primary');
  normalTask.fields.provider = {value: 'primary', inherited_value: 'secondary', source: 'role', inherited_source: 'installation', explicit: true};
  normalTask.fields.fallback = {value: 'secondary', inherited_value: '', source: 'role', inherited_source: 'default', explicit: true};
  normalTask.fields.timeout_seconds = {value: 45, inherited_value: 20, source: 'role', inherited_source: 'default', explicit: true};
  normalTask.fields.max_output_tokens = {value: 1024, inherited_value: 4096, source: 'role', inherited_source: 'default', explicit: true};
  normalTask.fields.prompt = {value: '自定义附加提示', inherited_value: '', source: 'role', inherited_source: 'default', explicit: true};
  data.tasks = [coreTask, normalTask];
  h.page.mount(node);
  try {
    h.requests[0].resolve(data);
    await flush();

    // Check normal task inspector
    node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
    const normalCard = node.innerHTML.split('data-fncard="memory.reflect"')[1].split('</article>')[0];
    const normalLabels = [...normalCard.matchAll(/<label\b[^>]*>[\s\S]*?<\/label>/g)].map(m => m[0]);
    assert.ok(normalLabels.length >= 5, 'normal inspector should render labels for all 5 fields');
    for (const lbl of normalLabels) {
      assert.doesNotMatch(lbl, /<button\b|data-model-inherit-reset/, 'label must never wrap an inherit-reset button');
      const forMatch = lbl.match(/for="([^"]+)"/);
      assert.ok(forMatch, `label must have explicit for attribute: ${lbl}`);
      assert.ok(normalCard.includes(`id="${forMatch[1]}"`), `matching control id="${forMatch[1]}" must exist`);
    }
    assert.match(normalCard, /class="model-adv-chev"/, 'advanced details summary should render disclosure chevron');

    // Check core reply inspector
    node.click('[data-model-open]', {modelOpen: 'conversation.reply'});
    const coreCard = node.innerHTML.split('data-fncard="conversation.reply"')[1].split('</article>')[0];
    const coreLabels = [...coreCard.matchAll(/<label\b[^>]*>[\s\S]*?<\/label>/g)].map(m => m[0]);
    assert.ok(coreLabels.length >= 1, 'core reply inspector should render prompt label');
    for (const lbl of coreLabels) {
      assert.doesNotMatch(lbl, /<button\b|data-model-inherit-reset/, 'core reply label must never wrap an inherit-reset button');
      const forMatch = lbl.match(/for="([^"]+)"/);
      assert.ok(forMatch, `core reply label must have explicit for attribute: ${lbl}`);
      assert.ok(coreCard.includes(`id="${forMatch[1]}"`), `matching control id="${forMatch[1]}" must exist`);
    }
  } finally {
    h.page.unmount();
  }
});

test('inspector render preserves body scrollTop on same-task re-render and resets on task switch', async () => {
  const h = harness(), node = h.root(), data = status();
  let currentBody = {scrollTop: 0};
  const origQuerySelector = node.querySelector.bind(node);
  node.querySelector = sel => {
    if (sel === '[data-fncard]') {
      const m = node.innerHTML.match(/data-fncard="([^"]+)"/);
      return m ? {dataset: {fncard: m[1]}, getAttribute: () => m[1]} : null;
    }
    if (sel === '.model-insp-body') return node.innerHTML.includes('class="model-insp-body"') ? currentBody : null;
    return origQuerySelector(sel);
  };
  h.page.mount(node);
  try {
    h.requests[0].resolve(data);
    await flush();
    node.click('[data-model-open]', {modelOpen: 'memory.reflect'});
    // Simulate user scrolling inspector body down, then resetting provider (which triggers full render)
    currentBody = {scrollTop: 185};
    node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
    assert.equal(currentBody.scrollTop, 185, 're-rendering the same open task should preserve scrollTop');

    // Simulate switching to another task -> fresh body must NOT inherit previous task scrollTop
    const nextBody = {scrollTop: 0};
    let reads = 0;
    node.querySelector = sel => {
      if (sel === '[data-fncard]') {
        const m = node.innerHTML.match(/data-fncard="([^"]+)"/);
        return m ? {dataset: {fncard: m[1]}, getAttribute: () => m[1]} : null;
      }
      if (sel === '.model-insp-body') {
        reads += 1;
        return reads === 1 ? currentBody : nextBody;
      }
      return origQuerySelector(sel);
    };
    node.click('[data-model-open]', {modelOpen: 'conversation.reply'});
    assert.equal(nextBody.scrollTop, 0, 'opening a different task should not restore previous task scrollTop');
  } finally {
    h.page.unmount();
  }
});

test('saveGlobalDefaults preserves success state if silent GET refresh fails', async () => {
  const h = harness(), node = h.root(), data = status();
  data.global_defaults = {
    provider_id: 'primary',
    fallback_provider_id: '',
    effective_provider_id: 'primary',
    effective_fallback_provider_id: '',
    editable: true,
  };
  h.page.mount(node);
  try {
    h.requests[0].resolve(data);
    await flush();
    node.change('[data-global-default-field]', {globalDefaultField: 'fallback_provider_id', value: 'secondary'});
    node.click('[data-global-default-save]');
    const post = h.requests.find(r => r.method === 'POST' && r.route === 'model/defaults');
    post.resolve({
      saved: true,
      restart_required: false,
      fallback_cleared_duplicate: false,
      global_defaults: {
        provider_id: 'primary',
        fallback_provider_id: 'secondary',
        effective_provider_id: 'primary',
        effective_fallback_provider_id: 'secondary',
        editable: true,
      },
    });
    await flush();
    const silentRefresh = h.requests.filter(r => r.method === 'GET' && r.route === 'model').at(-1);
    silentRefresh.reject(new Error('transient_timeout'));
    await flush();
    assert.equal(h.page.isBusy(), false);
    assert.equal(h.page.isDirty(), false);
    assert.match(node.innerHTML, /陪伴通用模型已保存/);
    assert.doesNotMatch(node.innerHTML, /保存失败/);
  } finally {
    h.page.unmount();
  }
});

test('models-page.css pins narrow-screen routing row edit button to top-right and hides empty diff chips', () => {
  const css = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/models-page.css'), 'utf8');
  assert.match(css, /\.model-diff-chips:empty\s*\{\s*display:\s*none\s*\}/);
  assert.match(css, /\.model-rrow-edit\s*\{[^}]*grid-column:\s*5/);
  const containerBlock = css.match(/@container\s+model-workspace\s*\(max-width:\s*720px\)\s*\{[\s\S]*?\n\}/);
  assert.ok(containerBlock, 'missing @container model-workspace (max-width:720px) block');
  assert.match(containerBlock[0], /\.model-rrow-edit\s*\{[^}]*grid-column:\s*3;\s*grid-row:\s*1/);
  const mediaBlock = css.match(/@media\(max-width:960px\)\s*\{\s*\.model-img-workbench[\s\S]*?\n\}/);
  assert.ok(mediaBlock, 'missing trailing @media(max-width:960px) block');
  assert.match(mediaBlock[0], /\.model-rrow-edit\s*\{[^}]*grid-column:\s*3;\s*grid-row:\s*1/);
  assert.match(css, /\.model-insp-body\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column/);
  assert.match(css, /\.model-insp-body\s*>\s*\*\s*\{\s*flex:\s*0\s+0\s+auto\s*\}/);
  const advRule = css.match(/\.model-adv\s*\{[^}]*\}/);
  assert.ok(advRule, 'missing .model-adv rule');
  assert.doesNotMatch(advRule[0], /overflow:\s*hidden/, '.model-adv must not clip expanded textarea');
  assert.match(css, /\.model-drawer\s*\{[^}]*translate3d\(100%,\s*0,\s*0\)[^}]*visibility:\s*hidden[^}]*pointer-events:\s*none/);
  assert.match(css, /\.model-drawer\.on\s*\{[^}]*translate3d\(0,\s*0,\s*0\)[^}]*cubic-bezier\(0\.22,\s*1,\s*0\.36,\s*1\)/);
  assert.match(css, /:root\[data-motion="off"\]\s+\.model-mask/);
  assert.match(css, /\.model-rrow-fb\s*\{[^}]*inline-flex[^}]*white-space:\s*nowrap/);
  assert.match(css, /\.model-cur-item\s+\.model-cur\s*\{[^}]*text-overflow:\s*ellipsis[^}]*white-space:\s*nowrap/);
  assert.match(css, /\.model-sc-rf\s+\.r\s+b\s*\{[^}]*white-space:\s*nowrap/);
  assert.match(css, /\.model-insp-top\s*\{[^}]*flex-wrap:\s*nowrap/);
  assert.match(css, /\.model-dock\s*\{[^}]*pointer-events:\s*none[^}]*translate3d\(0,\s*16px,\s*0\)\s*scale\(0\.985\)/);
  assert.match(css, /\.model-dock\.is-open\s*\{[^}]*translate3d\(0,\s*0,\s*0\)\s*scale\(1\)/);
  assert.match(css, /\.model-rgroup-body\s*\{[^}]*grid-template-rows:\s*1fr/);
  assert.match(css, /\.model-rgroup\.is-collapsed\s+\.model-rgroup-body\s*\{[^}]*grid-template-rows:\s*0fr/);
  assert.match(css, /\.model-collapse-shell\s*\{[^}]*grid-template-rows:\s*0fr/);
  assert.match(css, /\.model-adv-shell\s*\{[^}]*grid-template-rows:\s*0fr/);
  assert.match(css, /\.model-adv\.is-open:not\(\.is-animating\)\s*>\s*\.model-adv-shell\s*>\s*\.model-adv-inner[^}]*overflow:\s*visible/);
});
