const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

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
  return {page: window.CompanionModelsPage, requests, root, store, audioBlobs, revokedAudioUrls};
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
  node.click('[data-model-cm]', {modelCm: 'precise'});
  for (const [kind, label] of Object.entries(labels)) {
    const card = node.innerHTML.split(`data-fncard="${kind}"`)[1].split('</article>')[0];
    assert.ok(card.includes(`<b>${label}</b>`), kind);
    assert.ok(card.includes(`aria-label="选择 ${label}"`), kind);
    assert.ok(card.includes(kind), 'retain internal task keys for diagnosis');
  }
  node.input('[data-model-search]', {value: '主动联系复核'});
  assert.match(node.innerHTML, /data-fncard="proactive.judge"/);
  assert.doesNotMatch(node.innerHTML, /data-fncard="conversation\.image_review"/);
  node.input('[data-model-search]', {value: ''});
  node.click('[data-model-sub]', {modelSub: 'map'});
  for (const [kind, label] of Object.entries(labels)) {
    assert.ok(node.innerHTML.includes(`data-model-open="${kind}" aria-label="编辑 ${label}"`), kind);
  }
  assert.match(node.innerHTML, /data-model-open="future.optional_task"/);
  node.click('[data-model-sub]', {modelSub: 'cfg'});
  node.click('[data-model-cm]', {modelCm: 'quick'});
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
  assert.doesNotMatch(node.innerHTML, /<option value="conversation.reply"/);
  assert.match(node.innerHTML, /<option value="memory.reflect"/);
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
  assert.match(node.innerHTML, /记忆整理/);
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
  assert.doesNotMatch(second.innerHTML, /记忆整理/);
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
  assert.doesNotMatch(node.innerHTML, /class="model-mlgd"/);
  assert.match(node.innerHTML, /data-model-provider-filter/);
  assert.match(node.innerHTML, /主模型提供方/);
  assert.match(node.innerHTML, /class="model-rl"><span>文本生成<\/span>/);
  assert.doesNotMatch(node.innerHTML, /class="model-rl"><span>text<\/span>/);
  node.change('[data-model-provider-filter]', {value: 'secondary'});
  assert.match(node.innerHTML, /<option value="secondary" selected>/);
  assert.match(node.innerHTML, /没有匹配的任务/);
  node.change('[data-model-provider-filter]', {value: ''});
  assert.match(node.innerHTML, /data-fncard="conversation.reply"/);
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
  node.input('[data-model-search]', {value: '记忆'});
  assert.match(node.innerHTML, /记忆整理/);
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
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
  assert.doesNotMatch(node.innerHTML, /输出上限已调整为/);
  assert.match(node.innerHTML, /继承的默认输出上限 8192 高于所选模型提供方的 2048，发送时会按 2048 生效/);
  const card = node.innerHTML.split('data-fncard="memory.reflect"')[1].split('</article>')[0];
  assert.match(card, /data-model-inherit="max_output_tokens"[^>]*checked/);
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
  node.change('[data-model-field]', {modelField: 'provider', modelKind: 'memory.reflect', value: 'secondary'});
  assert.match(node.innerHTML, /跟随上级不会被改写/);
  const card = node.innerHTML.split('data-fncard="memory.reflect"')[1].split('</article>')[0];
  assert.match(card, /data-model-inherit="max_output_tokens"[^>]*checked/);
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
  assert.match(node.innerHTML, /记忆整理/);
  // exactly one functional GET happened on mount; switching back reused in-memory state
  const fnGets = requests.filter(r => r.method === 'GET' && r.route === 'model');
  assert.equal(fnGets.length, 1, 'functional matrix must not be re-fetched on tab return');
});

test('the functional page exposes the design subtabs and the situation map derives routing', async () => {
  const {page, requests, root} = harness();
  const node = root();
  page.mount(node);
  requests[0].resolve(status());
  await flush();
  assert.match(node.innerHTML, /态势图/);
  assert.match(node.innerHTML, /模型配置/);
  assert.match(node.innerHTML, /替换策略/);
  node.click('[data-model-sub]', {modelSub: 'map'});
  assert.match(node.innerHTML, /需要模型的功能/);
  assert.match(node.innerHTML, /可用模型/);
  assert.match(node.innerHTML, /对话回复/);
  assert.match(node.innerHTML, /按能力槽/);
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
  node.click('[data-model-sub]', {modelSub: 'map'});
  assert.equal((node.innerHTML.match(/data-provider-pill="primary"/g) || []).length, 3);

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

test('replacement policy keeps ordinary AstrBot replies out of the task selector', async () => {
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
  assert.doesNotMatch(selector, /conversation\.reply/);
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
  assert.match(node.innerHTML, /已选 <b>1<\/b> 项任务/);
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
