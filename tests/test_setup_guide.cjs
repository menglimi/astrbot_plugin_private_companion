const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const sourcePath = process.env.COMPANION_SETUP_GUIDE_SOURCE
  ? path.resolve(process.env.COMPANION_SETUP_GUIDE_SOURCE)
  : path.resolve(__dirname, '../pages/陪伴小屋/setup-guide.js');
const source = fs.readFileSync(sourcePath, 'utf8');
const migrationReviewSource = fs.readFileSync(path.join(path.dirname(sourcePath), 'migration-config-review.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const settle = async () => { for (let i = 0; i < 12; i += 1) await flush(); };

function harness({status, responses = {}, targets = [], refreshedTargets = targets, accounts = [], personas = [],
  bridgeResponses = {}, configEditorsAvailable = true, confirmResult = true, directoryError = null}) {
  const calls = [];
  const bridgeCalls = [];
  const editorCalls = [];
  const editorStops = [];
  const replyMounts = [];
  const stateMounts = [];
  const modelMounts = [];
  let modelBusy = false;
  const listeners = {};
  const navigation = [];
  const prompts = [];
  const sessionValues = new Map();
  let directoryRefreshes = 0;
  let currentTargets = targets;
  let html = '';
  let currentStatus = status;
  const mountHosts = {
    '#setupAdvancedEditorHost': {},
    '#setupQuickReplyHost': {},
    '#setupQuickDecisionHost': {},
    '#setupQuickStateHost': {},
    '#setupStepEditor': {},
    '#setupLegacySettingsHost': {},
  };
  const node = {
    addEventListener: (name, fn) => { listeners[name] = fn; },
    removeEventListener: name => { delete listeners[name]; },
    querySelector: selector => mountHosts[selector] || null,
  };
  Object.defineProperty(node, 'innerHTML', {get: () => html, set: value => { html = value; }});
  const product = {
    request(method, route, body, options) {
      calls.push({method, route, body, options});
      const key = method + ' ' + route;
      let result = responses[key];
      if (Array.isArray(result)) result = result.length > 1 ? result.shift() : result[0];
      if (typeof result === 'function') result = result(body);
      if (result instanceof Error) return Promise.reject(result);
      if (result !== undefined) return Promise.resolve(result);
      if (method === 'GET' && route === 'setup/status') return Promise.resolve(currentStatus);
      if (method === 'GET' && route === 'setup/proactive-test/status') return Promise.resolve(currentStatus);
      return Promise.resolve({});
    },
    targets: () => currentTargets,
    accounts: () => accounts,
    personas: () => personas,
    async refreshDirectory() {
      directoryRefreshes += 1;
      if (directoryError) throw directoryError;
      currentTargets = refreshedTargets;
    },
    role: () => configEditorsAvailable ? {reply_api: '/astrbot_plugin_private_companion/reply/current',
      model_api: '/astrbot_plugin_private_companion/model'} : null,
    scopeKey: () => 'installation-a:target-a:role-a',
    unwrap: response => response,
    relative(address) {
      const prefix = '/astrbot_plugin_private_companion/';
      assert.ok(address.startsWith(prefix));
      return address.slice(prefix.length);
    },
  };
  const document = {querySelector: () => null};
  const bridge = {apiPost(route, body) {
    bridgeCalls.push({route, body});
    const result = bridgeResponses[route];
    return Promise.resolve(typeof result === 'function' ? result(body) : result || {});
  }};
  const configEditors = configEditorsAvailable ? {create(kinds, rootProvider, views, endpoint) {
    editorCalls.push({kinds: [...kinds], host: rootProvider(), views, endpoint});
    return {restore() {}, paint() {}, read() {}, stop() { editorStops.push([...kinds]); }};
  }} : null;
  const sandbox = {window: {CompanionProduct: product, CompanionConfigEditors: configEditors,
    navigate: (...args) => navigation.push(args),
    CompanionConfirm: async options => { prompts.push(options); return confirmResult; },
    CompanionWorkbench: configEditorsAvailable ? {create() {}} : null, AstrBotPluginPage: bridge,
    CompanionReplyPage: {mount(element, options) { replyMounts.push({element, options}); }, unmount() {}},
    CompanionStatePage: {mount(element, options) { stateMounts.push({element, options}); }, unmount() {}}}, document, Promise, Set, Map, String,
    Number, Object, Array, RegExp, Error, Date, JSON, Math, setTimeout, clearTimeout,
    sessionStorage: {getItem: key => sessionValues.get(key) || null, setItem: (key, value) => sessionValues.set(key, value),
      removeItem: key => sessionValues.delete(key)}};
  sandbox.window.CompanionModelsPage = {mount(element, options) { modelMounts.push({element, options}); }, unmount() {},
    isDirty: () => false, isBusy: () => modelBusy};
  vm.runInNewContext(fs.readFileSync(path.join(path.dirname(sourcePath), 'migration-config-review.js'), 'utf8'), sandbox);
  vm.runInNewContext(fs.readFileSync(path.join(path.dirname(sourcePath), 'migration-config-table.js'), 'utf8'), sandbox);
  vm.runInNewContext(source, sandbox);
  return {
    calls,
    bridgeCalls,
    navigation,
    prompts,
    get html() { return html; },
    mount: options => sandbox.window.CompanionSetup.mount(node, options),
    mountMigration: () => sandbox.window.CompanionSetup.mountMigration(node),
    promptUpgradeAtStartup: callback => sandbox.window.CompanionSetup.promptUpgradeAtStartup(callback),
    continueUpgrade: upgrade => sandbox.window.CompanionSetup.continueUpgrade(upgrade),
    get directoryRefreshes() { return directoryRefreshes; },
    click(action) {
      listeners.click({target: {closest(selector) {
        if (selector === '[data-setup-action]' && !action.startsWith('legacy-')) return {dataset: {setupAction: action}};
        if (selector === '[data-setup-legacy-action]' && action.startsWith('legacy-')) return {dataset: {setupLegacyAction: action}};
        return null;
      }}});
    },
    clickLegacyDestination(kind, personaId, destinationId = '') {
      listeners.click({target: {closest(selector) {
        if (selector === '[data-setup-legacy-batch-add]' && kind === 'add') {
          return {dataset: {setupLegacyBatchAdd: personaId}};
        }
        if (selector === '[data-setup-legacy-batch-remove]' && kind === 'remove') {
          return {dataset: {setupLegacyBatchRemove: personaId, destinationId}};
        }
        return null;
      }}});
    },
    clickBlock(block) {
      listeners.click({target: {closest: selector => selector === '[data-setup-advanced-block]'
        ? {dataset: {setupAdvancedBlock: block}} : null}});
    },
    clickGuideBlock(block) {
      listeners.click({target: {closest: selector => selector === '[data-setup-open-block]'
        ? {dataset: {setupOpenBlock: block}} : null}});
    },
    clickMigrationView(view, mapping = false) {
      const attribute = mapping ? 'setupLegacyMappingView' : 'setupLegacyView';
      const selector = mapping ? '[data-setup-legacy-mapping-view]' : '[data-setup-legacy-view]';
      listeners.click({target: {closest: query => query === selector ? {dataset: {[attribute]: view}} : null}});
    },
    clickMigrationStep(step) {
      listeners.click({target: {closest: selector => selector === '[data-setup-legacy-step]'
        ? {dataset: {setupLegacyStep: String(step)}} : null}});
    },
    clickMigrationWizard(action) {
      listeners.click({target: {closest: selector => selector === '[data-setup-legacy-wizard]'
        ? {dataset: {setupLegacyWizard: action}} : null}});
    },
    input(value, dataset = {}) {
      listeners.input({target: {value, dataset, matches: () => false}});
    },
    chooseMigrationConfig(row, choice) {
      listeners.click({target: {closest: selector => selector === '[data-migration-config-action]'
        ? {dataset: {migrationConfigAction: 'choose', row, choice}} : null}});
    },
    clickMigrationShortcut(kind, value) {
      const attribute = {detail: 'setupLegacyDetail', settings: 'setupLegacySettings', back: 'setupLegacySettingsBack'}[kind] || 'setupLegacyOpenMapping';
      const selector = {detail: '[data-setup-legacy-detail]', settings: '[data-setup-legacy-settings]', back: '[data-setup-legacy-settings-back]'}[kind] || '[data-setup-legacy-open-mapping]';
      listeners.click({target: {closest: query => query === selector ? {dataset: {[attribute]: value}} : null}});
    },
    unmount: () => sandbox.window.CompanionSetup.unmount(),
    change(name, value, dataset = {}, extra = {}) {
      listeners.change({target: {name, value, dataset, ...extra}});
    },
    updateStatus: value => { currentStatus = value; },
    editorCalls,
    editorStops,
    replyMounts,
    stateMounts,
    modelMounts,
    setModelBusy: value => { modelBusy = value; },
  };
}

const readyStatus = extra => ({step: 'complete', prepared: true, ready: true,
  completed: ['model', 'knowledge', 'relationships'], skipped: [], proactive_test: {state: 'delivered'}, ...extra});

test('migration role setup falls back to the setup catalog before staging starts', async () => {
  const route = 'host/targets/t-first/roles/role-a/legacy';
  const catalog = {
    accounts: [{platform_id: 'qq-main', account_id: '100', bot_id: 'bot-a', account_name: '测试 Bot'}],
    personas: [{bot_id: 'bot-a', platform_id: 'qq-main', account_id: '100',
                persona_id: 'original', role_id: 'role-a', label: '原人格', mapped: true}],
    targets: [{target_id: 't-first', scope_kind: 'private', platform_id: 'qq-main',
               account_id: '100', bot_id: 'bot-a', participant_id: '*',
               roles: [{role_id: 'role-a', persona_ids: ['original'],
                        legacy_api: '/astrbot_plugin_private_companion/' + route}]}],
  };
  const error = Object.assign(new Error('staging_not_initialized'), {code: 'staging_not_initialized'});
  const page = harness({targets: [], refreshedTargets: [], directoryError: error,
    responses: {'GET setup/targets': catalog}});
  page.mountMigration();
  await settle();
  assert.match(page.html, /当前角色/);
  assert.match(page.html, /就用这个角色/);
  assert.doesNotMatch(page.html, /角色目录暂未更新/);
  page.clickMigrationWizard('refresh-roles');
  await settle();
  assert.match(page.html, /当前角色/);
  assert.doesNotMatch(page.html, /角色目录暂未更新/);
  assert.match(page.html, /就用这个角色/);
  page.clickMigrationWizard('next');
  await settle();
  assert.match(page.html, /data-migration-step="2"/);
});

test('the review is read-only and migration starts only after an explicit confirmation', async () => {
  const route = 'host/targets/first/roles/role-a/legacy';
  const rows = [
    {id: 'enabled', selection_id: 'enabled', selection_unit: 'enabled', field: 'proactive.contact.enabled',
      label: '主动联系', before: true, after: false, current_known: true, status: 'merge'},
    {id: 'interval', selection_id: 'interval', selection_unit: 'interval', field: 'proactive.contact.interval_seconds',
      label: '主动间隔', before: 600, after: 900, current_known: true, status: 'confirm'},
  ];
  const discovery = {findings: [{source_id: 'private.host_configuration', state: 'ready'}]};
  const plan = {...discovery, change_count: 2, plan_token: 'reviewed',
    configuration_review: {version: 1, rows}, scope_status: [{available: true}]};
  const freshPlan = {...plan, plan_token: 'reviewed-fresh'};
  const emptyPlan = {...plan, change_count: 0, plan_token: 'reviewed-empty'};
  const scope = {token: 'scope-token',
    personas: [{source_id: '', auto: true, target_id: 'role-a'},
      {source_id: 'old', auto: false, target_id: null, reason: 'persona_not_assigned_to_role'}],
    groups: [], private: {contacts: 0, rejected: 0},
    review: [{kind: 'persona', source_id: 'old', target_id: null, reason: 'persona_not_assigned_to_role'}],
    counts: {personas: 2, personas_auto: 1, groups: 0, groups_auto: 0, review: 1}};
  const batch = {token: 'scope-token',
    items: [{kind: 'persona', persona_id: '', state: 'imported', reason: null},
      {kind: 'persona', persona_id: 'old', state: 'review', reason: 'persona_not_assigned_to_role'}],
    counts: {imported: 1, review: 1, failed: 0}, memory_database_skipped: true};
  const page = harness({status: readyStatus(), targets: [{target_id: 'first', roles: [
    {role_id: 'role-a', legacy_api: '/astrbot_plugin_private_companion/' + route}]}], responses: {
    ['GET ' + route]: discovery,
    ['GET ' + route + '/plan']: [plan, freshPlan, emptyPlan],
    ['POST ' + route + '/apply']: {selection_applied: true, configuration_imported: true,
      completion_recorded: true, kept_count: 1, written_count: 2, errors: []},
    ['GET ' + route + '/scope']: scope,
    ['POST ' + route + '/batch-apply']: batch,
    ['GET ' + route + '/data-plan']: {plan_token: 'data-token'},
  }});
  page.mountMigration();
  await settle();
  page.clickMigrationWizard('next');
  await settle();

  /* 第 2 步（核对）只读：摘要、紧凑表、范围，三步之外没有写按钮。 */
  assert.match(page.html, /data-migration-step="2"/);
  assert.match(page.html, /配置将改写 <strong>2<\/strong> 项 · 失效 <strong>0<\/strong> 项 · 保留 <strong>0<\/strong> 项/);
  assert.match(page.html, /setup-migration-compact-table/);
  assert.match(page.html, /<th scope="col">项目<\/th><th scope="col">现在<\/th><th scope="col">迁移后<\/th><th scope="col">影响与原因<\/th>/);
  assert.match(page.html, /主动联系/);
  assert.match(page.html, /主动间隔/);
  assert.match(page.html, /data-setup-legacy-wizard="manual"/);
  assert.doesNotMatch(page.html, /data-setup-legacy-action="legacy-auto"/);
  assert.doesNotMatch(page.html, /写入当前版本/);
  assert.equal(page.calls.some(call => call.method === 'POST'), false,
    '核对页不得写入');

  /* 逐项迁移已移出向导：旧配置表和逐项导入只在独立面板出现。 */
  page.clickMigrationWizard('manual');
  await settle();
  assert.match(page.html, /data-setup-legacy-wizard="back-to-oneclick"/);
  assert.match(page.html, /migration-config-table/);
  assert.match(page.html, /data-setup-legacy-action="legacy-apply"/);
  page.clickMigrationWizard('back-to-oneclick');
  await settle();
  assert.match(page.html, /data-migration-step="2"/);

  /* 进入迁移页不自动执行，必须先选迁移方式再点「开始迁移」。 */
  page.clickMigrationWizard('next');
  await settle();
  assert.match(page.html, /data-migration-step="3"/);
  assert.match(page.html, /写入配置 2 项 \+ 导入 1 个人格 \/ 0 个群 \/ 0 位联系人/);
  assert.match(page.html, /data-setup-legacy="config-mode"[^>]*value="inherit"[^>]*checked/);
  assert.match(page.html, /data-setup-legacy-wizard="run"[^>]*>开始迁移</);
  assert.equal(page.calls.some(call => call.method === 'POST'), false,
    '进入迁移页不得写入');

  page.clickMigrationWizard('run');
  await settle();
  const saved = page.calls.find(call => call.method === 'POST' && call.route.endsWith('/apply'));
  assert.deepEqual(JSON.parse(JSON.stringify(saved.body)), {
    plan_token: 'reviewed-fresh', configuration_scope: 'plugin', selected_fields: ['enabled', 'interval'],
  });
  const batchCall = page.calls.find(call => call.method === 'POST' && call.route.endsWith('/batch-apply'));
  assert.ok(batchCall, '确认后才执行迁移');
  assert.deepEqual(JSON.parse(JSON.stringify(batchCall.body.personas)), ['', 'old']);
  assert.deepEqual(JSON.parse(JSON.stringify(batchCall.body.groups)), []);
  assert.equal(batchCall.body.token, 'scope-token');
  assert.match(page.html, /1 已导入 · 1 需确认 · 0 失败/);
  assert.match(page.html, /单项失败不会中断其余项目/);

  /* 第二次执行重新试算：配置无变化时不重复 apply。 */
  const planCallsBeforeRetry = page.calls.filter(call => call.route === route + '/plan').length;
  const applyCallsBeforeRetry = page.calls.filter(call => call.method === 'POST' && call.route.endsWith('/apply')).length;
  page.clickMigrationWizard('run');
  await settle();
  assert.equal(page.calls.filter(call => call.route === route + '/plan').length, planCallsBeforeRetry + 1,
    '重试必须先重新试算');
  assert.equal(page.calls.filter(call => call.method === 'POST' && call.route.endsWith('/apply')).length,
    applyCallsBeforeRetry, '配置已是最新时不得重发旧 token');
  assert.match(page.html, /配置已是最新，无需写入/);

  page.clickMigrationWizard('next');
  await settle();
  assert.match(page.html, /data-migration-step="4"/);
  assert.match(page.html, /<h3>结果汇总<\/h3>/);
  assert.match(page.html, /<h3>仍需处理<\/h3>/);
  assert.match(page.html, /<h3>逐项回执<\/h3>/);
  assert.match(page.html, /data-go="life"/);
});

test('first upgrade opens migration with the newly saved destination available', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', scope_kind: 'private', roles: [{role_id: 'r-first',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]},
    {target_id: 't-group', scope_kind: 'group', roles: [{role_id: 'r-group',
      legacy_api: '/astrbot_plugin_private_companion/host/targets/t-group/roles/r-group/legacy'}]}];
  const page = harness({status: {upgrade: {present: true, configured: false,
    can_prepare: true, plan_token: 'reviewed-source'}}, refreshedTargets: targets,
    responses: {'POST setup/upgrade': {saved: true, started: true}}});
  page.mount();
  assert.doesNotMatch(page.html, /暂时读不到设置进度/);
  await settle();
  page.click('prepare-upgrade');
  await settle();
  assert.deepEqual(page.navigation, [['manage', 'data', 'migration']]);
  page.mountMigration();
  await settle();
  assert.ok(page.calls.some(call => call.method === 'GET' && call.route === route),
    '核对是第一步的下一屏，迁移页应在进入时只读读取旧版来源');
  page.clickMigrationWizard('next');
  await settle();
  assert.equal(page.calls.filter(call => call.method === 'GET' && call.route === route).length, 1,
    '进入核对页不应重复扫描');
  assert.match(page.html, /data-migration-step="2"/);
});

test('a configured legacy connection falls through to the remaining setup and verification', async () => {
  const states = [];
  const page = harness({status: {
    upgrade: {present: true, configured: true, can_prepare: false},
    prepared: true, ready: false, step: 'proactive_test',
    completed: ['model', 'knowledge', 'relationships', 'proactive', 'life', 'group'],
    skipped: [], proactive_test: null,
    binding: {participant_id: 'u-1', platform_id: 'aiocqhttp', account_id: 'bot-1'},
    model: {provider_id: 'provider-1', providers: [{}]}, host: {accepting: true},
  }});
  page.mount({onState: info => states.push(info)});
  await settle();
  assert.match(page.html, /旧版连接已接续/);
  assert.match(page.html, /直接继续下方设置与主动消息测试即可/);
  assert.match(page.html, /继续迁移/);
  assert.match(page.html, /第 8 步 · 约 1 分钟/);
  assert.match(page.html, /测试主动消息/);
  assert.doesNotMatch(page.html, /setup-upgrade-steps|STEP [0-9]|第 5 步 · 约 1 分钟/);
  assert.equal(states.at(-1)?.upgrading, false);
});

test('upgrade navigation survives a pre-staging directory refresh failure', async () => {
  const error = Object.assign(new Error('staging_not_initialized'), {code: 'staging_not_initialized'});
  const page = harness({status: {upgrade: {present: true, configured: false,
    can_prepare: true, plan_token: 'reviewed-source'}}, directoryError: error,
    responses: {'POST setup/upgrade': {saved: true, started: true}}});
  page.mount();
  await settle();
  page.click('prepare-upgrade');
  await settle();
  assert.deepEqual(page.navigation, [['manage', 'data', 'migration']]);
});

test('startup detects an old configuration and offers a direct upgrade guide once', async () => {
  const page = harness({status: {upgrade: {present: true, configured: false, can_prepare: true,
    source_sha256: 'legacy-config-fingerprint'}}});
  const continued = [];
  assert.equal(await page.promptUpgradeAtStartup(upgrade => continued.push(upgrade)), true);
  assert.equal(page.prompts.length, 1);
  assert.equal(page.prompts[0].title, '发现旧版配置');
  assert.match(page.prompts[0].message, /直接打开旧版资料迁入/);
  assert.deepEqual(page.navigation, []);
  assert.deepEqual(continued, [{present: true, configured: false, can_prepare: true,
    source_sha256: 'legacy-config-fingerprint'}]);
  assert.equal(await page.promptUpgradeAtStartup(() => assert.fail('dismissed startup prompt must not continue twice')), false);
  assert.equal(page.prompts.length, 1);
});

test('confirming the startup prompt prepares the legacy connection and opens migration directly', async () => {
  const upgrade = {present: true, configured: false, can_prepare: true,
    plan_token: 'reviewed-source', source_sha256: 'legacy-config-fingerprint'};
  const page = harness({status: {upgrade}, responses: {'POST setup/upgrade': {saved: true, started: true}}});
  assert.equal(await page.promptUpgradeAtStartup(current => page.continueUpgrade(current)), true);
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls.filter(call => call.method === 'POST')
    .map(call => ({route: call.route, body: call.body})))), [
    {route: 'setup/upgrade', body: {confirmed: true, plan_token: 'reviewed-source'}},
  ]);
  assert.equal(page.directoryRefreshes, 1);
  assert.deepEqual(page.navigation, [['manage', 'data', 'migration']]);
});

test('startup upgrade preparation failure does not open migration', async () => {
  const page = harness({status: {upgrade: {present: true, configured: false, can_prepare: true,
    plan_token: 'reviewed-source'}}, responses: {'POST setup/upgrade': {started: false, startup_error: 'host unavailable'}}});
  await assert.rejects(page.promptUpgradeAtStartup(upgrade => page.continueUpgrade(upgrade)), /host unavailable/);
  assert.deepEqual(page.navigation, []);
  assert.equal(page.directoryRefreshes, 0);
  assert.equal(await page.promptUpgradeAtStartup(() => {}), true,
    'a failed preparation should be offered again instead of being suppressed for the session');
  assert.equal(page.prompts.length, 2);
});

test('startup guide explains when an old mapping cannot be fully verified', async () => {
  const page = harness({status: {upgrade: {present: true, configured: false, can_prepare: false,
    source_sha256: 'legacy-config-fingerprint'}}});
  assert.equal(await page.promptUpgradeAtStartup(upgrade => page.continueUpgrade(upgrade)), true);
  assert.match(page.prompts[0].message, /直接打开「账号与会话」核对/);
  assert.deepEqual(page.navigation, [['manage', 'host']]);
});

test('private memory migration confirms the selected role and exposes retained work', async () => {
  const route = 'host/targets/private/roles/role/legacy';
  const page = harness({status: readyStatus(), targets: [{target_id: 'private', roles: [
    {role_id: 'role', role_name: '星缘', legacy_api: '/astrbot_plugin_private_companion/' + route},
  ]}], responses: {
    ['GET ' + route + '/private-memory-plan']: {plan_token: 'private-plan',
      matched_person_count: 1, matched_material_count: 74},
    ['POST ' + route + '/private-memory-apply']: {imported: {memories: 40, timeline: 34}},
    ['GET ' + route + '/archived-work']: {archives: [{archive_id: '<archive>', areas: {work: {jobs: 2}}}]},
  }});
  page.mountMigration();
  await settle();
  page.change('', 'private', {setupLegacy: 'target'});
  page.change('', 'role', {setupLegacy: 'role'});
  await openLegacyManual(page);
  page.click('legacy-private-memory-plan');
  await settle();
  assert.match(page.html, /74 条历史资料/);
  page.click('legacy-private-memory-apply');
  await settle();
  assert.equal(page.calls.filter(c => c.method === 'POST' && c.route.endsWith('/private-memory-apply')).length, 0);
  page.change('', '', {setupLegacy: 'private-memory-confirm'}, {checked: true});
  page.click('legacy-private-memory-apply');
  await settle();
  const applied = page.calls.find(c => c.method === 'POST' && c.route.endsWith('/private-memory-apply'));
  assert.deepEqual(JSON.parse(JSON.stringify(applied.body)), {plan_token: 'private-plan', confirmed: true});
  assert.equal(applied.options.timeoutMs, 300000);
  assert.match(page.html, /历史记忆 40 条，时间线 34 条/);
  page.click('legacy-archived-work');
  await settle();
  assert.match(page.html, /&lt;archive&gt;/);
  page.change('', 'D:/other-v1', {setupLegacy: 'root'});
  page.click('legacy-private-memory-apply');
  await settle();
  assert.doesNotMatch(page.html, /74 条历史资料|历史记忆 40 条|&lt;archive&gt;/);
  assert.equal(page.calls.filter(c => c.method === 'POST' && c.route.endsWith('/private-memory-apply')).length, 1);
});

test('migration guide uses old and current version labels in its visible copy', async () => {
  const page = harness({status: readyStatus()});
  page.mountMigration();
  await settle();
  assert.match(page.html, /迁移设置/);
  assert.doesNotMatch(page.html, /\bV[12]\b/);
  /* 4 步：设置角色 → 核对 → 迁移 → 完成。 */
  assert.match(page.html, /第 <b>1<\/b> \/ 4 步/);
  for (const [step, label] of [[1, '设置角色'], [2, '核对'], [3, '迁移'], [4, '完成']]) {
    assert.match(page.html, new RegExp(`data-setup-legacy-step="${step}"[\\s\\S]*?<strong>${label}</strong>`),
      `第 ${step} 步应是 ${label}`);
  }
  assert.match(page.html, /data-setup-legacy-step="4"/);
  assert.doesNotMatch(page.html, /data-setup-legacy-step="[5-8]"/);
  assert.doesNotMatch(page.html, /共 08 步/);
});

test('late private memory preview is discarded after changing the V1 source', async () => {
  const route = 'host/targets/private/roles/role/legacy';
  let complete;
  const pending = new Promise(resolve => { complete = resolve; });
  const page = harness({status: readyStatus(), targets: [{target_id: 'private', roles: [
    {role_id: 'role', legacy_api: '/astrbot_plugin_private_companion/' + route},
  ]}], responses: {['GET ' + route + '/private-memory-plan']: pending}});
  page.mountMigration();
  await settle();
  page.change('', 'private', {setupLegacy: 'target'});
  page.change('', 'role', {setupLegacy: 'role'});
  page.click('legacy-private-memory-plan');
  await settle();
  page.change('', 'D:/another-source', {setupLegacy: 'root'});
  complete({plan_token: 'stale-plan', matched_person_count: 1, matched_material_count: 74});
  await settle();
  assert.doesNotMatch(page.html, /74 条历史资料/);
  page.change('', '', {setupLegacy: 'private-memory-confirm'}, {checked: true});
  page.click('legacy-private-memory-apply');
  await settle();
  assert.equal(page.calls.filter(c => c.method === 'POST' && c.route.endsWith('/private-memory-apply')).length, 0);
});

async function plannedMultiDestinationPage(firstApplyResponse) {
  const firstRoute = 'host/targets/t-first/roles/r-first/legacy';
  const secondRoute = 'host/targets/t-second/roles/r-second/legacy';
  const targets = [
    {target_id: 't-first', display_name: '账号一', roles: [{role_id: 'r-first', role_name: '角色一',
      legacy_api: '/astrbot_plugin_private_companion/' + firstRoute}]},
    {target_id: 't-second', display_name: '账号二', roles: [{role_id: 'r-second', role_name: '角色二',
      legacy_api: '/astrbot_plugin_private_companion/' + secondRoute}]},
  ];
  const plan = (token, personaId) => ({plan_token: token, source_persona_id: personaId,
    source_fingerprint: 'source-fingerprint', archive: {fingerprint: 'archive-fingerprint'},
    reaction_assets: {batch_count: 1}, journals: {batch_count: 0},
    reference_images: {batch_count: 1}, wardrobe_items: {batch_count: 1, image_count: 1},
    worldbook: {general_count: 0, member_count: 0, base_entries_matches: false, base_members_matches: false}});
  const basePlan = {plan_token: 'base-plan', archive: {fingerprint: 'archive-fingerprint'},
    persona_sources: [{persona_id: 'persona-a', label: 'V1 人格：persona-a', state: 'ready'}],
    reaction_assets: {batch_count: 1}, journals: {batch_count: 0}, worldbook: {general_count: 0, member_count: 0},
    group_networks: [], memory_database: {state: 'absent'}, warnings: []};
  const firstResult = firstApplyResponse === undefined ? {source_persona_id: 'persona-a', errors: [],
    reaction_created: 1, reaction_remaining: 0, journal_created: 0, journal_remaining: 0,
    journal_derivatives_remaining: 0} : firstApplyResponse;
  const page = harness({status: readyStatus(), targets, responses: {
    ['GET ' + firstRoute + '/data-plan']: [basePlan, plan('preview-first', 'persona-a'), plan('fresh-first', 'persona-a')],
    ['GET ' + secondRoute + '/data-plan']: [plan('preview-second', 'persona-a'), plan('fresh-second', 'persona-a')],
    ['POST ' + firstRoute + '/data-apply']: firstResult,
    ['POST ' + secondRoute + '/data-apply']: {source_persona_id: 'persona-a', errors: [],
      reaction_created: 1, reaction_remaining: 0, journal_created: 0, journal_remaining: 0,
      journal_derivatives_remaining: 0},
  }});
  page.mountMigration();
  await settle();
  page.change('', 't-first', {setupLegacy: 'target'});
  page.change('', 'r-first', {setupLegacy: 'role'});
  await openLegacyManual(page);
  page.click('legacy-data-plan');
  await settle();
  return {page, firstRoute, secondRoute};
}

async function legacyPersonaMappingPage({targets, personaSources}) {
  const prefix = '/astrbot_plugin_private_companion/';
  const route = targets[0].roles[0].legacy_api.slice(prefix.length);
  const basePlan = {plan_token: 'persona-base-plan',
    persona_sources: personaSources, reaction_assets: {batch_count: 1},
    journals: {batch_count: 0}, worldbook: {general_count: 0, member_count: 0},
    group_networks: [], memory_database: {state: 'absent'}, warnings: []};
  const preview = {plan_token: 'persona-preview-plan', source_persona_id: 'persona-a',
    source_fingerprint: 'source-fingerprint', archive: {fingerprint: 'archive-fingerprint'},
    reaction_assets: {batch_count: 1}, journals: {batch_count: 0},
    worldbook: {general_count: 0, member_count: 0, base_entries_matches: false, base_members_matches: false},
    reference_images: {batch_count: 0}, wardrobe_items: {batch_count: 0}};
  const fresh = {...preview, plan_token: 'persona-fresh-plan'};
  const page = harness({status: readyStatus(), targets, responses: {
    ['GET ' + route + '/data-plan']: [basePlan, preview, fresh],
    ['POST ' + route + '/data-apply']: {source_persona_id: 'persona-a', errors: [],
      reaction_created: 1, reaction_remaining: 0, journal_created: 0, journal_remaining: 0,
      journal_derivatives_remaining: 0},
  }});
  page.mountMigration();
  await settle();
  page.change('', targets[0].target_id, {setupLegacy: 'target'});
  page.change('', targets[0].roles[0].role_id, {setupLegacy: 'role'});
  await enterMigrationDataStep(page, {mapping: true});
  /* 逐项路径仍从资料清单开始：先读取清单，再逐个人格试算。 */
  page.click('legacy-data-plan');
  await settle();
  return {page, route};
}

async function enterMigrationDataStep(page, {mapping = false} = {}) {
  /* 逐项迁移已移出向导：从这里进入独立的逐项面板。 */
  page.clickMigrationWizard('manual');
  await settle();
  if (mapping) {
    page.clickMigrationView('personas', true);
    await settle();
  }
}

async function openLegacyManual(page) {
  page.clickMigrationWizard('manual');
  await settle();
}

test('first-run stages start with binding, then model, knowledge, relationships and active test', async () => {
  const page = harness({status: {step: 'binding', mode: {accounts: 'multiple', personas: 'multiple'}, ready: false}});
  page.mount();
  await settle();

  const positions = ['核心管理员窗口', '插件模型', '世界知识', '关系教学', '主动消息测试']
    .map(label => page.html.indexOf(label));
  assert.ok(positions.every(index => index >= 0));
  assert.deepEqual([...positions].sort((a, b) => a - b), positions);
  assert.match(page.html, /多个人格/);
  assert.doesNotMatch(page.html, /选择运行规模|单账号|单目标运行实例/);
  assert.doesNotMatch(page.html, /十年|预览模式|review|授权有效期/);
});

test('first-run stages follow the old guide layout with grouped steps, time hints and a checklist', async () => {
  const page = harness({status: {step: 'model', mode: {accounts: 'single', personas: 'single'}, ready: false}});
  page.mount();
  await settle();

  // 旧版引导的四段分组标题 + 「第 N 步 · 约 N 分钟」时间提示。
  for (const group of ['目标用户与平台', '核心模型配置', '主动功能与其他配置', '主动消息测试']) {
    assert.match(page.html, new RegExp(group));
  }
  assert.match(page.html, /第 2 步 · 约 3 分钟/);
  // 每步上方有说明与要点清单，表单在下方。
  assert.match(page.html, /setup-stage-banner/);
  assert.match(page.html, /setup-checklist/);
  assert.match(page.html, /选择插件通用模型/);
});

test('confirming the core window advances to model without preparing early', async () => {
  const page = harness({status: {step: 'binding', mode: {accounts: 'single', personas: 'single'},
    mode_requires_reload: false, binding: {pending: true, account_id: '100', participant_id: '200'},
    binding_confirmed: false, ready: false}});
  page.mount();
  await settle();
  assert.match(page.html, /我会永远陪着你/);
  assert.match(page.html, /确认此窗口/);
  page.click('confirm-binding');
  await settle();

  assert.ok(page.calls.some(item => item.method === 'POST' && item.route === 'setup/binding/confirm'));
  assert.ok(!page.calls.some(item => item.route === 'setup/prepare'));
});

test('fixed profile binding conflicts remain visible after plugin reload', async () => {
  const reason = '固定运行档的绑定条件发生变化，请检查：Bot 账号、私聊对象。模型参数变化不会阻止连接。';
  const page = harness({status: {step: 'model', mode: {accounts: 'single', personas: 'single'},
    host: {last_failure: {code: 'staging_profile_conflict', reason}},
    model: {provider_id: 'native-model', providers: [{id: 'native-model', name: 'Fixture model'}]},
    ready: false}});
  page.mount();
  await settle();

  assert.match(page.html, /Bot 账号、私聊对象/);
  assert.match(page.html, /模型参数变化不会阻止连接/);
});

test('binding diagnostics show readable labels instead of raw internal details', async () => {
  const page = harness({status: {step: 'model', host: {last_failure: {code: 'staging_profile_conflict',
    reason: 'internal profile / installation-id', details: ['staging-role', 'dashboard:hash']}}, ready: false}});
  page.mount();
  await settle();
  assert.match(page.html, /绑定条件发生变化/);
  assert.doesNotMatch(page.html, /internal profile|installation-id|staging-role|dashboard:hash/);
  page.updateStatus({step: 'binding', settings_error: 'staging_settings_invalid', ready: false});
  page.click('refresh');
  await settle();
  assert.match(page.html, /运行设置尚未就绪/);
  assert.doesNotMatch(page.html, /staging_settings_invalid/);
  page.updateStatus({step: 'binding', binding: {pending: true, platform_id: 'default',
    account_id: 'internal-bot', participant_id: 'internal-subject', persona_id: '[%None]'}, ready: false});
  page.click('refresh');
  await settle();
  assert.match(page.html, /QQ · OneBot/);
  assert.match(page.html, /默认人格/);
  assert.doesNotMatch(page.html, /default|internal-bot|internal-subject|\[%None\]/);
});

test('setup uses loaded providers directly for task routing', async () => {
  const model = {step: 'model', mode: {accounts: 'single', personas: 'single'}, ready: false,
    prepared: false, model: {provider_id: '', providers: [
      {id: 'native-model', name: 'Fixture model'}, {id: 'secondary-model', name: 'Second model'}]}};
  const prepared = {...model, prepared: true, model: {...model.model, provider_id: 'native-model'}};
  const task = {kind: 'proactive.compose', lane: 'background', capabilities: ['text'], edit_token: 'a'.repeat(64),
    fields: {provider: {value: 'native-model'}, fallback: {value: ''}}};
  const knowledge = {...prepared, step: 'knowledge', completed: ['model']};
  const page = harness({status: model, responses: {
    'GET setup/status': [model, prepared, knowledge],
    'GET model': {data: {status: 'ok', data: {providers: [
      {id: 'native-model', name: 'Fixture model', capabilities: ['text']},
      {id: 'secondary-model', name: 'Second model', capabilities: ['text']}], tasks: [task]}}},
    'POST setup/model': {provider_id: 'native-model'},
    'POST model/configure': {task, replayed: false},
  }});
  page.mount();
  await settle();
  assert.match(page.html, /初始主 Provider/);
  assert.doesNotMatch(page.html, /setup-additional-provider|初始 Provider 之外最多/);
  page.change('setup-provider', 'native-model');
  page.click('save-model');
  await settle();

  const save = page.calls.find(item => item.method === 'POST' && item.route === 'setup/model');
  assert.deepEqual(JSON.parse(JSON.stringify(save.body)), {provider_id: 'native-model'});
  assert.match(page.html, /任务模型路由/);
  assert.match(page.html, /主动联系/);
  page.change('', 'secondary-model', {setupModelTask: 'proactive.compose', setupModelField: 'provider'});
  page.click('save-task:proactive.compose');
  await settle();
  const route = page.calls.find(item => item.method === 'POST' && item.route === 'model/configure');
  assert.deepEqual(JSON.parse(JSON.stringify(route.body)), {kind: 'proactive.compose',
    operation_key: route.body.operation_key, edit_token: 'a'.repeat(64),
    changes: {provider: 'secondary-model', fallback: ''}, reset: []});
  assert.ok(!page.calls.some(item => item.route === 'setup/prepare'));
  assert.match(page.html, /世界知识/);
});

test('persona drafting and acquaintance-card saving stay inside the setup guide', async () => {
  const profile = {edit_token: 'b'.repeat(64), settings: {name: '', key_lore: ''}};
  const status = {step: 'knowledge', prepared: true, ready: false,
    mode: {accounts: 'single', personas: 'single'}, model: {provider_id: 'native-model', providers: []}};
  const page = harness({status, responses: {
    'GET setup/profile': {role_profile: profile, relationship_profile: profile},
    'GET setup/status': status,
    'POST setup/profile/save': {saved: true},
  }, bridgeResponses: {'setup/profile-draft': body => ({target: body.target,
    draft: {fields: {name: '航海者', key_lore: '熟悉潮汐和星图'}, notes: ['请核对推断']},
    source: {name: '当前人格', source_preview: '以海图辨认方向'},
    knowledge: [{title: '群岛航路', source: 'V2 世界资料'}], limits: {name: 200, key_lore: 2000}})}});
  page.mount();
  await settle();
  page.change('', '补充：她从小学会读海图', {profileSupplement: ''});
  page.click('generate-profile');
  await settle();

  assert.equal(page.bridgeCalls.length, 1);
  assert.equal(page.bridgeCalls[0].body.target, 'role_profile');
  assert.match(page.html, /V2 世界资料/);
  page.change('', '海图学者', {profileField: 'name'});
  page.click('save-profile');
  await settle();
  const saved = page.calls.find(item => item.method === 'POST' && item.route === 'setup/profile/save');
  assert.equal(saved.body.target, 'role_profile');
  assert.deepEqual(JSON.parse(JSON.stringify(saved.body.changes)), {name: '海图学者', key_lore: '熟悉潮汐和星图'});
  assert.match(page.html, /已保存 2 项角色设定/);
  assert.doesNotMatch(page.html, /data-go="manage"/);
});

test('a unique destination preselects the role and reads the source without writing', async () => {
  const discovery = {v1_root: 'V1-data-root', findings: [
    {source_id: 'private.host_configuration', state: 'ready', detail: '可以迁移到 V2。'},
    {source_id: 'private.core_profiles', state: 'data_ready', detail: '日记、世界书和素材导入器已就绪。'},
    {source_id: 'private.work_and_delivery', state: 'documented_skip', detail: '旧投递结果不会重放。'},
    {source_id: 'memory.timeline_and_governance', state: 'data_ready', detail: '完整记忆库快照可归档。'},
  ]};
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', account_name: '账号一', roles: [{role_id: 'r-first',
    role_name: '默认人格', legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const page = harness({status: readyStatus(), targets, responses: {['GET ' + route]: discovery}});
  page.mountMigration();
  await settle();

  assert.equal(page.calls.some(item => item.route === 'setup/status'), false,
    'the standalone migration workspace must not depend on first-run state');
  assert.match(page.html, /data-migration-step="1"/);
  assert.match(page.html, /当前角色/);
  assert.match(page.html, /默认人格/);
  assert.match(page.html, /就用这个角色/);
  assert.match(page.html, /旧版只读/);
  assert.match(page.html, /<option value="t-first" selected>账号一/);
  assert.match(page.html, /data-setup-legacy="bot"/);
  assert.match(page.html, /data-setup-legacy="role"/);
  assert.match(page.html, /<option value="r-first" selected>默认人格/);
  const firstScan = page.calls.find(item => item.method === 'GET' && item.route === route);
  assert.ok(firstScan, '进入第一步后只读读取来源');
  assert.deepEqual(JSON.parse(JSON.stringify(firstScan.body)), {configuration_scope: 'plugin'});
  assert.equal(page.calls.some(item => item.method === 'POST' && item.route.startsWith(route)), false,
    'preselection must never write migration data');
  assert.match(page.html, /V1-data-root/);
  assert.match(page.html, /来源/);

  page.clickMigrationWizard('next');
  await settle();
  assert.match(page.html, /data-migration-step="2"/);
  assert.match(page.html, /配置将改写/);
  assert.match(page.html, /迁移范围/);
  assert.equal(page.calls.filter(item => item.method === 'GET' && item.route === route).length, 1,
    '进入核对页不重复扫描');

  await openLegacyManual(page);
  assert.match(page.html, /Reality 摄像头与音频/);
  assert.match(page.html, /重新检查旧版/);
  page.change('', 'all', {setupLegacy: 'configuration-scope'});
  page.click('legacy-scan');
  await settle();
  const scan = page.calls.filter(item => item.method === 'GET' && item.route === route).at(-1);
  assert.deepEqual(JSON.parse(JSON.stringify(scan.body)),
    {configuration_scope: 'all'});
  assert.deepEqual(JSON.parse(JSON.stringify(scan.options)), {timeoutMs: 300000});
  page.change('', 'reality', {setupLegacy: 'configuration-scope'});
  page.click('legacy-scan');
  await settle();
  const realityScan = page.calls.filter(item => item.route === route).at(-1);
  assert.deepEqual(JSON.parse(JSON.stringify(realityScan.body)),
    {configuration_scope: 'reality'});
  assert.match(page.html, /可转换/);
  assert.match(page.html, /可导入数据/);
  assert.match(page.html, /按边界保留/);
  assert.match(page.html, /完整记忆库快照可归档/);
});

test('the role-first step lists multiple Bots and roles before any migration target exists', async () => {
  const page = harness({status: readyStatus(), targets: [], responses: {
    'GET setup/targets': {
      accounts: [
        {platform_id: 'aiocqhttp', account_id: '10001', bot_id: 'bot-a', account_name: '主 Bot'},
        {platform_id: 'aiocqhttp', account_id: '10002', bot_id: 'bot-b', account_name: '备用 Bot'},
      ],
      personas: [
        {platform_id: 'aiocqhttp', account_id: '10001', bot_id: 'bot-a', persona_id: '星缘', role_id: 'role-a', label: '星缘', mapped: false},
        {platform_id: 'aiocqhttp', account_id: '10001', bot_id: 'bot-a', persona_id: '烛雨', role_id: 'role-b', label: '烛雨', mapped: false},
        {platform_id: 'aiocqhttp', account_id: '10002', bot_id: 'bot-b', persona_id: '珝环', role_id: 'role-c', label: '珝环', mapped: false},
      ],
      targets: [], directory: {complete: true, issues: []},
    },
  }});
  page.mountMigration();
  await settle();

  assert.match(page.html, /先设置接收角色/);
  assert.match(page.html, /Bot 与伴侣角色/);
  assert.match(page.html, /选择本次检查所用的 Bot 和角色/);
  assert.match(page.html, /主 Bot/);
  assert.match(page.html, /备用 Bot/);
  assert.match(page.html, /星缘/);
  assert.match(page.html, /烛雨/);
  assert.match(page.html, /珝环/);
  assert.match(page.html, /尚未建立角色/);
  assert.doesNotMatch(page.html, /<option value="role-a"[^>]*>[^<]*可接收迁移/);
  assert.equal(page.calls.some(item => item.method === 'GET' && item.route.includes('/legacy')), false);
  page.change('', JSON.stringify(['aiocqhttp', 'bot-a', '10001']), {setupLegacy: 'bot'});
  page.clickMigrationWizard('create-role');
  assert.match(page.html, /AstrBot 人格（至少选择一个）/);
  assert.match(page.html, /type="checkbox" data-setup-legacy="new-persona"/);
  assert.match(page.html, /创建角色并继续/);
  assert.deepEqual(page.navigation, []);
});

test('the migration guide creates a role inline and selects its refreshed destination', async () => {
  const account = {platform_id: 'aiocqhttp', account_id: '10001', bot_id: 'bot-a', account_name: '主 Bot'};
  const persona = {platform_id: 'aiocqhttp', account_id: '10001', bot_id: 'bot-a',
    persona_id: '星缘', role_id: 'derived', label: '星缘', mapped: false};
  const secondPersona = {...persona, persona_id: '烛雨', label: '烛雨'};
  const route = 'host/targets/private-one/roles/new-role/legacy';
  const targets = [{...account, target_id: 'private-one', scope_kind: 'private', roles: [
    {role_id: '新角色', role_name: '新角色', legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const page = harness({status: readyStatus(), targets: [], refreshedTargets: targets, responses: {
    'GET setup/targets': [{accounts: [account], personas: [persona, secondPersona]},
      {accounts: [account], personas: [persona, secondPersona].map(item =>
        ({...item, role_id: '新角色', mapped: true}))}],
    'POST accounts/role': {saved: true},
  }});
  page.mountMigration();
  await settle();
  page.clickMigrationWizard('create-role');
  page.input('新角色', {setupLegacy: 'new-role-name'});
  assert.match(page.html, /data-setup-legacy-wizard="save-role" disabled/);
  page.change('', '星缘', {setupLegacy: 'new-persona'}, {checked: true});
  assert.ok(page.html.indexOf('data-setup-legacy="new-role-name"')
    < page.html.indexOf('data-setup-legacy="new-persona"'));
  page.change('', '星缘', {setupLegacy: 'new-persona'}, {checked: false});
  assert.match(page.html, /data-setup-legacy-wizard="save-role" disabled/);
  page.change('', '星缘', {setupLegacy: 'new-persona'}, {checked: true});
  page.change('', '烛雨', {setupLegacy: 'new-persona'}, {checked: true});
  assert.match(page.html, /已选择 2 个/);
  page.clickMigrationWizard('save-role');
  await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls.find(call => call.route === 'accounts/role').body)),
    {platform_id: 'aiocqhttp', account_id: '10001', bot_id: 'bot-a',
      persona_ids: ['星缘', '烛雨'], role_id: '新角色'});
  assert.equal(page.directoryRefreshes, 1);
  assert.match(page.html, /<option value="新角色" selected>/);
  assert.match(page.html, /角色已创建，下一步：核对旧版配置与范围/);
  assert.doesNotMatch(page.html, /data-setup-legacy-wizard="next" disabled/);
  assert.deepEqual(page.navigation, []);
});

test('a failed inline role save keeps the form and does not claim the role exists', async () => {
  const account = {platform_id: 'aiocqhttp', account_id: '10001', bot_id: 'bot-a'};
  const persona = {...account, persona_id: '星缘', role_id: 'derived', mapped: false};
  const failure = Object.assign(new Error('account_role_persona_in_use'), {code: 'account_role_persona_in_use'});
  const page = harness({status: readyStatus(), responses: {
    'GET setup/targets': {accounts: [account], personas: [persona]},
    'POST accounts/role': failure,
    'GET accounts': {personas: []},
  }});
  page.mountMigration();
  await settle();
  page.clickMigrationWizard('create-role');
  page.change('', '星缘', {setupLegacy: 'new-persona'}, {checked: true});
  page.input('新角色', {setupLegacy: 'new-role-name'});
  page.clickMigrationWizard('save-role');
  await settle();
  assert.match(page.html, /这个 AstrBot 人格已归入角色/);
  assert.match(page.html, /value="新角色"/);
  assert.equal(page.directoryRefreshes, 0);
});

test('the first-run page discovers an upgrade when there is one unambiguous V2 destination', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', account_name: '账号一', roles: [{role_id: 'r-first',
    role_name: '默认人格', legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const page = harness({status: readyStatus(), targets, responses: {
    ['GET ' + route]: {v1_root: 'V1-data-root', findings: [], install: {
      phase: 'upgrade_pending', pending: true, v1_present: true, v1_root: 'V1-data-root',
      configuration_imported: false}},
  }});
  page.mount();
  await settle();

  assert.ok(page.calls.some(item => item.method === 'GET' && item.route === route),
    'the completed setup page may add the read-only upgrade card without blocking setup');
  assert.match(page.html, /旧版的数据还在/);
  assert.match(page.html, /V1-data-root/);
  assert.equal(page.calls.some(item => item.method === 'POST' && item.route.startsWith(route)), false,
    'the setup-page must not write migration data');
});

test('the first-run page does not guess between multiple V2 migration destinations', async () => {
  const firstRoute = 'host/targets/t-first/roles/r-first/legacy';
  const secondRoute = 'host/targets/t-second/roles/r-second/legacy';
  const targets = [
    {target_id: 't-first', roles: [{role_id: 'r-first',
      legacy_api: '/astrbot_plugin_private_companion/' + firstRoute}]},
    {target_id: 't-second', roles: [{role_id: 'r-second',
      legacy_api: '/astrbot_plugin_private_companion/' + secondRoute}]},
  ];
  const page = harness({status: readyStatus(), targets});
  page.mount();
  await settle();

  assert.equal(page.calls.some(item => item.route === firstRoute || item.route === secondRoute), false,
    'ambiguous destinations remain a deliberate choice on the migration page');
  assert.doesNotMatch(page.html, /旧版的数据还在/);
});

test('a slow V1 discovery does not hold up the role-first migration page', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', roles: [{role_id: 'r-first',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  let finishDiscovery;
  const pendingDiscovery = new Promise(resolve => { finishDiscovery = resolve; });
  const page = harness({status: readyStatus(), targets, responses: {
    ['GET ' + route]: pendingDiscovery,
  }});
  page.mountMigration();
  await settle();

  assert.match(page.html, /当前角色/);
  assert.match(page.html, /data-migration-step="1"/);
  assert.doesNotMatch(page.html, /旧版的数据还在/);
  assert.ok(page.calls.some(item => item.method === 'GET' && item.route === route),
    '进入迁移页后会异步读取旧版来源');

  page.clickMigrationWizard('next');
  await settle();
  assert.match(page.html, /data-migration-step="2"/);
  assert.match(page.html, /正在只读检查旧版来源/);

  finishDiscovery({v1_root: 'V1-data-root', findings: [], install: {
    phase: 'upgrade_pending', pending: true, v1_present: true, v1_root: 'V1-data-root',
    configuration_imported: false,
  }});
  await settle();
  assert.match(page.html, /V1-data-root/);
  assert.doesNotMatch(page.html, /正在只读检查旧版来源/);
});

test('upgrade confirmation keeps configuration and data migration statuses separate', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', roles: [{role_id: 'r-first',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const page = harness({status: readyStatus(), targets, responses: {
    ['GET ' + route]: {v1_root: 'V1-data-root', findings: [], install: {
      phase: 'upgrade_restart_check', restart_observed: true, v1_present: true,
      configuration_imported: true, data_migration_status: 'tracked_separately'}},
    ['POST ' + route + '/upgrade-confirm']: {state: 'verified', install: {
      phase: 'upgrade_cleanup', restart_observed: true, upgrade_verified: true,
      v1_present: true, data_migration_status: 'tracked_separately'}},
  }});
  page.mountMigration();
  await settle();
  page.change('', 't-first', {setupLegacy: 'target'});
  page.change('', 'r-first', {setupLegacy: 'role'});
  page.click('legacy-scan');
  await settle();
  await openLegacyManual(page);

  await settle();
  assert.match(page.html, /请核对重载后的绑定、模型与回复/);
  assert.match(page.html, /配置状态不代表资料已迁移/);
  assert.match(page.html, /data-setup-legacy-action="legacy-upgrade-confirm"/);
  assert.doesNotMatch(page.html, /配置与资料已迁移/);

  page.click('legacy-upgrade-confirm');
  await settle();
  assert.ok(page.calls.some(item => item.method === 'POST' && item.route === route + '/upgrade-confirm'));
  assert.match(page.html, /重载后的配置核对已完成/);
  assert.match(page.html, /资料迁移进度请在下方单独查看/);
});

test('upgrade guide completes discovery, config inheritance and post-restart confirmation', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', roles: [{role_id: 'r-first',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const checkedSources = [
    {source_id: 'private.host_configuration', state: 'ready'},
    {source_id: 'reality.configuration', state: 'absent'},
  ];
  const beforeRestart = harness({status: readyStatus(), targets, responses: {
    ['GET ' + route]: [
      {v1_root: 'V1-data-root', findings: checkedSources.slice(0, 1), install: {
        phase: 'upgrade_pending', pending: true, v1_present: true, configuration_imported: false,
        data_migration_status: 'not_inferred'}},
      {v1_root: 'V1-data-root', findings: checkedSources, install: {
        phase: 'upgrade_pending', pending: true, v1_present: true, configuration_imported: false,
        data_migration_status: 'not_inferred'}},
      {v1_root: 'V1-data-root', findings: checkedSources, install: {
        phase: 'upgrade_restart_check', restart_observed: false, v1_present: true,
        configuration_imported: true, configuration_scopes: ['all'],
        data_migration_status: 'not_inferred'}},
    ],
    ['POST ' + route + '/auto']: {state: 'imported', written_count: 3, configuration_imported: true,
      completion_recorded: true, restart_required: true, data_import_required: true},
  }});
  beforeRestart.mountMigration();
  await settle();
  beforeRestart.change('', 't-first', {setupLegacy: 'target'});
  beforeRestart.change('', 'r-first', {setupLegacy: 'role'});
  beforeRestart.click('legacy-scan');
  await settle();
  await openLegacyManual(beforeRestart);

  await settle();
  assert.match(beforeRestart.html, /立即继承旧版配置/);
  beforeRestart.click('legacy-auto');
  await settle();
  const autoCall = beforeRestart.calls.find(item => item.method === 'POST' && item.route === route + '/auto');
  assert.deepEqual(JSON.parse(JSON.stringify(autoCall.body)), {});
  assert.match(beforeRestart.html, /旧版配置已继承/);
  assert.match(beforeRestart.html, /插件级设置需要重载本插件或重启 AstrBot 后生效/);
  assert.match(beforeRestart.html, /重载本插件或重启 AstrBot 后回来核对绑定、模型与回复/);
  assert.doesNotMatch(beforeRestart.html, /data-setup-legacy-action="legacy-upgrade-confirm"/);

  const afterRestart = harness({status: readyStatus(), targets, responses: {
    ['GET ' + route]: {v1_root: 'V1-data-root', findings: checkedSources, install: {
      phase: 'upgrade_restart_check', restart_observed: true, v1_present: true,
      configuration_imported: true, configuration_scopes: ['all'],
      data_migration_status: 'not_inferred'}},
    ['POST ' + route + '/upgrade-confirm']: {state: 'verified', install: {
      phase: 'upgrade_cleanup', restart_observed: true, upgrade_verified: true,
      v1_present: true, data_migration_status: 'not_inferred'}},
  }});
  afterRestart.mountMigration();
  await settle();
  afterRestart.change('', 't-first', {setupLegacy: 'target'});
  afterRestart.change('', 'r-first', {setupLegacy: 'role'});
  afterRestart.click('legacy-scan');
  await settle();
  await openLegacyManual(afterRestart);
  await settle();
  assert.match(afterRestart.html, /请核对重载后的绑定、模型与回复/);
  assert.match(afterRestart.html, /确认重载后运行正常/);
  assert.match(afterRestart.html, /配置状态不代表资料已迁移/);

  afterRestart.click('legacy-upgrade-confirm');
  await settle();
  const confirmCall = afterRestart.calls.find(item =>
    item.method === 'POST' && item.route === route + '/upgrade-confirm');
  assert.deepEqual(JSON.parse(JSON.stringify(confirmCall.body)), {confirmed: true});
  assert.match(afterRestart.html, /重载后的配置核对已完成/);
  assert.match(afterRestart.html, /资料迁移进度请在下方单独查看/);
});

test('upgrade guide blocks automatic inheritance when a configuration source is unverified', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', roles: [{role_id: 'r-first',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}];

  for (const unavailable of [
    {state: 'missing', findings: [],
      label: /缺少检查记录/},
  ]) {
    const page = harness({status: readyStatus(), targets, responses: {
      ['GET ' + route]: [
        {v1_root: 'V1-data-root', findings: [{source_id: 'private.host_configuration', state: 'ready'}],
          install: {phase: 'upgrade_pending', pending: true, v1_present: true}},
        {v1_root: 'V1-data-root', findings: unavailable.findings,
          install: {phase: 'upgrade_pending', pending: true, v1_present: true}},
      ],
    }});
    page.mountMigration();
    await settle();
    page.change('', 't-first', {setupLegacy: 'target'});
    page.change('', 'r-first', {setupLegacy: 'role'});
    page.click('legacy-scan');
    await settle();
    await openLegacyManual(page);

    page.click('legacy-auto');
    await settle();
    assert.equal(page.calls.some(item => item.method === 'POST' && item.route === route + '/auto'), false,
      `should not submit when the source is ${unavailable.state}`);
    assert.match(page.html, /旧版陪伴插件配置/);
    assert.match(page.html, unavailable.label);
  }

  const realityUnreadable = harness({status: readyStatus(), targets, responses: {
    ['GET ' + route]: [
      {v1_root: 'V1-data-root', findings: [{source_id: 'private.host_configuration', state: 'ready'}],
        install: {phase: 'upgrade_pending', pending: true, v1_present: true}},
      {v1_root: 'V1-data-root', findings: [
        {source_id: 'private.host_configuration', state: 'ready'},
        {source_id: 'reality.configuration', state: 'unreadable', detail: '配置文件格式错误。'},
      ], install: {phase: 'upgrade_pending', pending: true, v1_present: true}},
    ],
    ['POST ' + route + '/auto']: {state: 'imported', written_count: 1,
      configuration_imported: true, completion_recorded: true},
  }});
  realityUnreadable.mountMigration();
  await settle();
  realityUnreadable.change('', 't-first', {setupLegacy: 'target'});
  realityUnreadable.change('', 'r-first', {setupLegacy: 'role'});
  realityUnreadable.click('legacy-scan');
  await settle();
  await openLegacyManual(realityUnreadable);
  realityUnreadable.click('legacy-auto');
  await settle();
  assert.equal(realityUnreadable.calls.some(item => item.method === 'POST' && item.route === route + '/auto'), true,
    'Reality readability must not block plugin-scope inheritance');
});

test('upgrade guide identifies legacy imports whose configuration scope was never recorded', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', roles: [{role_id: 'r-first',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const page = harness({status: readyStatus(), targets, responses: {
    ['GET ' + route]: {v1_root: 'V1-data-root', findings: [], install: {
      phase: 'upgrade_restart_check', restart_observed: true, v1_present: true,
      configuration_imported: true, configuration_scope_unknown: true,
      configuration_scopes: [], data_migration_status: 'not_inferred'}},
  }});
  page.mountMigration();
  await settle();
  page.change('', 't-first', {setupLegacy: 'target'});
  page.change('', 'r-first', {setupLegacy: 'role'});
  page.click('legacy-scan');
  await settle();
  await openLegacyManual(page);

  assert.match(page.html, /旧版配置迁移记录，但原记录没有保存迁移范围/);
  assert.doesNotMatch(page.html, /旧版配置已迁移。请核对重载后的绑定/);
});

test('multiple migration destinations wait for explicit target and role selection', async () => {
  const route = 'host/targets/t-first/roles/role-digest/legacy';
  const secondRoute = 'host/targets/t-second/roles/r-second/legacy';
  const catalog = [
    {target_id: 't-first', bot_id: 'bot-a', roles: [{role_id: 'r-first',
      persona_ids: ['persona-a'], legacy_api: '/astrbot_plugin_private_companion/' + route}]},
    {target_id: 't-second', bot_id: 'bot-b', roles: [{role_id: 'r-second',
      persona_ids: ['persona-b'], legacy_api: '/astrbot_plugin_private_companion/' + secondRoute}]},
  ];
  const page = harness({status: readyStatus(), targets: catalog,
    responses: {[`GET ${route}`]: {findings: [], v1_root: 'selected-root'},
      [`GET ${secondRoute}`]: {findings: [], v1_root: 'other-root'}}});
  page.mountMigration();
  await settle();
  assert.equal(page.calls.some(item => item.route === route), false);
  assert.match(page.html, /data-setup-legacy="bot"/);
  assert.match(page.html, /data-setup-legacy="role"/);

  page.change('', JSON.stringify(['', 'bot-a', '']), {setupLegacy: 'bot'});
  page.change('', 'r-first', {setupLegacy: 'role'});
  page.clickMigrationWizard('next');
  await settle();
  const scanned = page.calls.find(item => item.method === 'GET' && item.route === route);
  assert.ok(scanned);
  assert.deepEqual(JSON.parse(JSON.stringify(scanned.body)), {configuration_scope: 'plugin'});
  assert.equal(page.calls.some(item => item.route === 'legacy'), false);
  assert.match(page.html, /selected-root/);
});

test('a slow migration scan shows its phase while the extended request is pending', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', roles: [{role_id: 'r-first',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  let finishScan;
  const scanResponse = new Promise(resolve => { finishScan = resolve; });
  const page = harness({status: readyStatus(), targets, responses: {[`GET ${route}`]: scanResponse}});
  page.mountMigration();
  await settle();
  page.change('', 't-first', {setupLegacy: 'target'});
  page.change('', 'r-first', {setupLegacy: 'role'});

  page.click('legacy-scan');
  const scan = page.calls.find(item => item.method === 'GET' && item.route === route);
  assert.deepEqual(JSON.parse(JSON.stringify(scan.options)), {timeoutMs: 300000});
  assert.match(page.html, /正在只读检查旧版来源/);

  finishScan({findings: [], v1_root: 'selected-root'});
  await settle();
  assert.doesNotMatch(page.html, /正在只读检查旧版来源/);
});

test('completed setup is a one-time receipt with a direct route to daily use', async () => {
  const page = harness({status: readyStatus({host: {accepting: false}})});
  page.mount();
  await settle();

  assert.match(page.html, /首次设置已完成/);
  assert.doesNotMatch(page.html, /运行模式|共享运行实例|单目标模式/);
  assert.match(page.html, /不需要重新走向导/);
  assert.match(page.html, /data-go="life"/);
  assert.doesNotMatch(page.html, /setup-legacy-disclosure|data-setup-legacy-action|V1 配置与运行数据迁移/);
  assert.match(page.html, /首次设置已保留，当前运行实例尚未接收消息/);
  assert.match(page.html, /data-setup-action="reconnect-host"/);
  assert.doesNotMatch(page.html, /setup-step-count|选择运行规模|绑定核心管理员窗口/);
});

test('start page lists the configuration guide and opens the matching editor in place', async () => {
  const page = harness({status: readyStatus()});
  page.mount();
  await settle();

  assert.match(page.html, /setup-guide-list/);
  for (const label of ['回复与分段', '人物与资料', '陌生号处理', '日程与作息', '主动联系', '群聊参与', '模型路由']) {
    assert.match(page.html, new RegExp(label));
  }
  // 旧版引导是一页清单：点某一项就在本页展开对应设置，不跳去别的页面。
  page.clickGuideBlock('relationships');
  assert.match(page.html, /setup-advanced-shell/);
  assert.deepEqual(page.editorCalls.at(-1).kinds,
    ['role_profile', 'relationship_profile', 'knowledge', 'private_access']);
  page.clickGuideBlock('strangers');
  assert.deepEqual(page.editorCalls.at(-1).kinds, ['private_access_installation']);
});

test('the guide carries the proactive, schedule and group steps with their editors in place', async () => {
  const steps = [
    ['proactive', '主动功能与强度', ['proactive.contact']],
    ['life', '日程与观察', ['life']],
    ['group', '群聊配置', ['group_participation']],
  ];
  for (const [step, label, kinds] of steps) {
    const page = harness({status: {step, prepared: true, ready: false,
      mode: {accounts: 'single', personas: 'single'}}});
    page.mount();
    await settle();
    assert.match(page.html, new RegExp(label), step);
    assert.match(page.html, /id="setupStepEditor"/, step);
    assert.match(page.html, /data-setup-action="complete-step"/, step);
    assert.match(page.html, /data-setup-action="skip-step"/, step);
    assert.deepEqual(page.editorCalls.at(-1).kinds, kinds, step);
  }
});

test('quick setup edits common V2 settings in place without leaving the start page', async () => {
  const task = {kind: 'proactive.compose', lane: 'background', capabilities: ['text'], edit_token: 'a'.repeat(64),
    fields: {provider: {value: 'fast-model'}, fallback: {value: ''}}};
  const page = harness({status: readyStatus({model: {provider_id: 'base-model', providers: [
      {id: 'base-model', name: 'Base model'}, {id: 'fast-model', name: 'Fast model'}]}}), responses: {
    'GET model': {providers: [
      {id: 'fast-model', name: 'Fast model', capabilities: ['text']},
      {id: 'careful-model', name: 'Careful model', capabilities: ['text']}], tasks: [task]},
    'POST model/configure': {task, replayed: false},
  }});
  page.mount();
  await settle();
  assert.match(page.html, /data-setup-action="advanced-open"/);
  page.click('advanced-open');

  for (const label of ['回复与分段', '人物与资料', '日程与作息', '主动联系', '群聊参与', '模型路由']) assert.match(page.html, new RegExp(label));
  assert.match(page.html, /id="setupQuickReplyHost"/);
  assert.match(page.html, /id="setupQuickStateHost"/);
  assert.doesNotMatch(page.html, /data-go=|data-manage=/);
  assert.equal(page.replyMounts.at(-1).options.expressionModule, 'expression-text');
  assert.equal(page.replyMounts.at(-1).options.section, 'segment');

  page.clickBlock('relationships');
  assert.deepEqual(page.editorCalls.at(-1).kinds, ['role_profile', 'relationship_profile', 'knowledge', 'private_access']);
  assert.equal(page.editorCalls.at(-1).views.knowledge.navigationLimit, 0);
  page.clickBlock('life');
  assert.equal(page.stateMounts.at(-1).options.feature, 'planning');
  assert.deepEqual([...page.editorCalls.at(-1).views.life.sectionIds], ['routine', 'sleep']);
  page.clickBlock('action');
  assert.deepEqual(page.editorCalls.at(-1).kinds, ['proactive.contact']);
  page.clickBlock('group');
  assert.equal(page.replyMounts.at(-1).options.view, 'decision');
  assert.deepEqual(page.editorCalls.at(-1).kinds, ['group_participation']);
  page.clickBlock('runtime');
  await settle();
  assert.match(page.html, /陪伴通用模型/);
  assert.match(page.html, /data-setup-model-task="proactive.compose"/);
  assert.doesNotMatch(page.html, /data-go=|data-manage=/);
  page.change('', 'careful-model', {setupModelTask: 'proactive.compose', setupModelField: 'provider'});
  page.click('save-task:proactive.compose');
  await settle();
  const saved = page.calls.find(item => item.method === 'POST' && item.route === 'model/configure');
  assert.equal(saved.body.kind, 'proactive.compose');
  assert.equal(saved.body.changes.provider, 'careful-model');
  page.click('advanced-close');
  assert.match(page.html, /首次设置已完成/);
});

test('quick model routes expose a retry after a failed read without retrying in a loop', async () => {
  const routeData = {providers: [{id: 'fast-model', name: 'Fast model', capabilities: ['text']}], tasks: []};
  const page = harness({status: readyStatus(), responses: {
    'GET model': [new Error('route unavailable'), {data: routeData}],
  }});
  page.mount();
  await settle();
  page.click('advanced-open');
  page.clickBlock('runtime');
  await settle();
  assert.equal(page.calls.filter(item => item.route === 'model').length, 1);
  assert.match(page.html, /route unavailable/);
  page.click('retry-model-routes');
  await settle();
  assert.equal(page.calls.filter(item => item.route === 'model').length, 2);
  assert.match(page.html, /当前没有这些后台任务的模型路由/);
});

test('a running completed setup does not ask the user to reconnect or repeat setup', async () => {
  const page = harness({status: readyStatus({host: {accepting: true}})});
  page.mount();
  await settle();

  assert.match(page.html, /当前版本正在接收消息/);
  assert.doesNotMatch(page.html, /data-setup-action="reconnect-host"/);
  assert.doesNotMatch(page.html, /选择运行规模|绑定核心管理员窗口|setup-step-count/);
});

test('an unknown test can be confirmed as received without submitting another send', async () => {
  const status = readyStatus({step: 'proactive_test', ready: false,
    proactive_test: {state: 'unknown', attempt_id: 'existing-test'}});
  const confirmed = readyStatus({host: {accepting: true}, proactive_test: {
    state: 'received', attempt_id: 'existing-test', confirmed_at: '2026-10-02T04:17:47Z'}});
  const page = harness({status, responses: {'GET setup/status': [status, confirmed],
    'POST setup/proactive-test/confirm': {state: 'received', ready: true}}});
  page.mount();
  await settle();
  assert.match(page.html, /已收到测试消息/);
  assert.doesNotMatch(page.html, /data-setup-action="proactive-test"/);
  page.click('confirm-test-received');
  await settle();
  const call = page.calls.find(item => item.route === 'setup/proactive-test/confirm');
  assert.deepEqual(JSON.parse(JSON.stringify(call.body)), {attempt_id: 'existing-test'});
  assert.equal(page.calls.some(item => item.route === 'setup/proactive-test'), false);
  assert.match(page.html, /首次设置已完成/);
  assert.match(page.html, /你已确认收到测试消息/);
  assert.doesNotMatch(page.html, /平台已确认发送测试消息/);
});

test('the proactive test requires explicit consent and polls the real delivery result', async () => {
  const initial = {step: 'proactive_test', ready: false, proactive_test: null};
  const started = {...initial, proactive_test: {state: 'started', attempt_id: 'attempt-1'}};
  const delivered = readyStatus({proactive_test: {state: 'delivered', attempt_id: 'attempt-1',
    finished_at: '2026-10-02T04:17:47Z'}});
  const page = harness({status: initial, responses: {
    'GET setup/status': initial,
    'GET setup/proactive-test/status': delivered,
    'POST setup/proactive-test': {state: 'started', attempt_id: 'attempt-1', ready: false},
  }});
  page.mount();
  await settle();
  page.change('', '', {setupProactiveOptIn: ''}, {checked: true});
  page.click('proactive-test');
  await settle();

  const call = page.calls.find(item => item.method === 'POST' && item.route === 'setup/proactive-test');
  assert.deepEqual(JSON.parse(JSON.stringify(call.body)), {enable_proactive: true});
  assert.match(page.html, /首次设置已完成/);
  assert.equal(page.calls.filter(item => item.method === 'POST' && item.route === 'setup/proactive-test').length, 1);
  assert.equal(page.calls.filter(item => item.route === 'setup/status').length, 1);
});

test('an in-flight test shows its real stage and stale polling cannot undo recipient confirmation', async () => {
  let releasePoll;
  const started = readyStatus({step: 'proactive_test', ready: false,
    proactive_test: {state: 'started', attempt_id: 'same-attempt',
      stages: [{stage: 'model_generation', state: 'running'}, {stage: 'delivery', state: 'pending'}]}});
  const page = harness({status: started, responses: {
    'GET setup/proactive-test/status': () => new Promise(resolve => { releasePoll = resolve; }),
    'POST setup/proactive-test/confirm': {ready: true, state: 'received',
      proactive_test: {state: 'received', attempt_id: 'same-attempt'}},
  }});
  page.mount();
  await settle();
  assert.match(page.html, /主动测试进行中/);
  assert.match(page.html, /模型生成：进行中/);
  assert.match(page.html, /平台投递：待处理/);
  assert.doesNotMatch(page.html, /主动测试结果未知/);
  page.click('confirm-test-received');
  await settle();
  assert.match(page.html, /首次设置已完成/);
  releasePoll(started);
  await settle();
  assert.match(page.html, /首次设置已完成/);
  assert.equal(page.calls.filter(item => item.route === 'setup/status').length, 1);
  page.unmount();
});

test('a failed test displays the recorded failure and keeps the home entry available', async () => {
  const page = harness({status: readyStatus({ready: false, step: 'proactive_test',
    proactive_test: {state: 'failed', attempt_id: 'failed-model', error_code: 'setup_contact_generation_failed',
      stages: [{stage: 'model_generation', state: 'failed', detail: 'model_provider_unavailable'}]}})});
  page.mount();
  await settle();
  assert.match(page.html, /模型生成：失败/);
  assert.match(page.html, /model_provider_unavailable/);
  assert.match(page.html, /进入陪伴小屋/);
  assert.doesNotMatch(page.html, /首次设置已完成/);
  page.unmount();
});

test('migration apply sends the reviewed plan token and refuses locally without a plan', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', roles: [{role_id: 'r-first',
    role_name: '星缘', legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const discovery = {v1_root: 'V1-root', findings: [{source_id: 'private.host_configuration', state: 'ready'}]};
  const plan = {v1_root: 'V1-root', findings: discovery.findings, change_count: 3, plan_token: 'reviewed-token',
    scope_status: [{scope: 'role.state', available: true}, {scope: 'plugin_config', available: true}],
    intents: [{group_id: 'role.state', values: {planning_enabled: true}, confidence: {planning_enabled: 'high'},
      sources: {planning_enabled: ['life_schedule_config.enable_daily_plan']}},
    {group_id: 'plugin', target_kind: 'plugin_config', values: {'poke.enabled': true}, confidence: {'poke.enabled': 'high'},
      sources: {'poke.enabled': ['reactive_poke_config.enable_reactive_poke']}}],
    differences: [{audit: true, disposition: 'manual_required', source_key: 'reactive_poke_config.reactive_poke_back_prompts',
      explain: 'V2 没有被动反戳提示词配置。'}], warnings: []};
  const result = {v1_root: 'V1-root', written_count: 3, written: ['role.state.weather', 'poke.enabled'],
    restart_required: true, skipped: []};
  const page = harness({status: readyStatus(), targets, responses: {
    ['GET ' + route]: discovery,
    ['GET ' + route + '/plan']: plan,
    ['POST ' + route + '/apply']: result,
  }});
  page.mountMigration();
  await settle();
  await openLegacyManual(page);

  page.change('', 'proactive', {setupLegacy: 'configuration-scope'});
  assert.match(page.html, /主动消息与天气基础设置/);
  page.click('legacy-scan');
  await settle();
  const scan = page.calls.filter(item => item.route === route).at(-1);
  assert.deepEqual(JSON.parse(JSON.stringify(scan.body)),
    {configuration_scope: 'proactive'});
  assert.deepEqual(JSON.parse(JSON.stringify(scan.options)), {timeoutMs: 300000});
  page.click('legacy-plan');
  await settle();
  const planned = page.calls.filter(item => item.route === route + '/plan').at(-1);
  assert.deepEqual(JSON.parse(JSON.stringify(planned.body)),
    {configuration_scope: 'proactive'});
  assert.deepEqual(JSON.parse(JSON.stringify(planned.options)), {timeoutMs: 300000});
  assert.match(page.html, /拟写入字段与数值/);
  assert.match(page.html, /配置兼容说明（1）/);
  assert.match(page.html, /reactive_poke_config\.reactive_poke_back_prompts/);
  assert.match(page.html, /暂不支持的旧设置（1）/);
  assert.match(page.html, /role\.state\.planning_enabled/);
  assert.match(page.html, /poke\.enabled/);
  assert.match(page.html, /插件设置需重启生效/);
  assert.match(page.html, /life_schedule_config\.enable_daily_plan/);
  page.click('legacy-apply');
  await settle();
  const applied = page.calls.find(item => item.method === 'POST' && item.route === route + '/apply');
  assert.deepEqual(JSON.parse(JSON.stringify(applied.body)), {
    plan_token: 'reviewed-token', configuration_scope: 'proactive'});
  assert.deepEqual(JSON.parse(JSON.stringify(applied.options)), {timeoutMs: 300000});
  assert.match(page.html, /已写入 3 项当前版本设置/);
  assert.match(page.html, /请重载本插件或重启 AstrBot 后回来核对/);

  /* 没有试算计划时，逐项面板本地拒绝写入，不发请求。 */
  const noPlan = harness({status: readyStatus(), targets, responses: {
    ['GET ' + route]: discovery,
  }});
  noPlan.mountMigration();
  await settle();
  await openLegacyManual(noPlan);
  noPlan.click('legacy-apply');
  await settle();
  assert.equal(noPlan.calls.some(item => item.method === 'POST' && item.route === route + '/apply'), false);
});

test('configuration compatibility separates unsupported settings from actionable tasks and replacement mechanisms', async () => {
  const route = 'host/targets/migration/roles/destination/legacy';
  const records = [
    ['not_migrated', 'basic_config.enable_p4_b_legacy_score_isolation'],
    ['manual_required', 'reactive_poke_config.reactive_poke_back_prompts'],
    ['manual_mapping', 'model_assignment_config.FAST_RESPONSE_PROVIDER_ID'],
    ['manual_mapping', 'forward_message_config.forward_message_image_limit'],
    ['implemented_elsewhere', 'basic_config.bot_name'],
    ['not_applicable', 'basic_config.enable_standalone_webui'],
    ['future_status', 'future.setting'],
  ];
  const page = harness({status: readyStatus(), targets: [{target_id: 'migration', roles: [{role_id: 'destination',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}], responses: {
    ['GET ' + route]: {findings: []},
    ['GET ' + route + '/plan']: {plan_token: 'review-token', change_count: 0, warnings: [],
      differences: records.map(([disposition, source_key]) => ({audit: true, disposition, source_key, explain: '技术说明'}))},
  }});
  page.mountMigration();
  await settle();
  page.clickMigrationWizard('next');
  await settle();
  assert.match(page.html, /配置将改写 <strong>0<\/strong> 项 · 失效 <strong>7<\/strong> 项 · 保留 <strong>0<\/strong> 项/);
  assert.match(page.html, /setup-migration-table-badge is-lost">失效/);
  assert.match(page.html, /basic_config\.enable_p4_b_legacy_score_isolation/);
  assert.doesNotMatch(page.html, /data-setup-legacy-action="legacy-auto"/);
  await openLegacyManual(page);
  page.change('', 'plugin', {setupLegacy: 'configuration-scope'});
  page.click('legacy-scan');
  await settle();
  page.click('legacy-plan');
  await settle();
  assert.match(page.html, /配置兼容说明（7）/);
  for (const title of ['可手动调整（1）', '暂不支持的旧设置（1）', '改由新版机制处理（2）',
    '未沿用的旧设置（1）', '当前不适用（1）', '尚待核实（1）']) assert.ok(page.html.includes(title), title);
  assert.doesNotMatch(page.html, /配置未自动迁移|需补充配置|需确认映射|原样保留/);
  const unsupported = page.html.split('暂不支持的旧设置（1）')[1].split('改由新版机制处理（2）')[0];
  assert.doesNotMatch(unsupported, /data-setup-legacy-settings/);
  assert.match(page.html, /data-setup-legacy-settings="models"/);
  page.clickMigrationShortcut('detail', 'audit-settings');
  assert.ok(page.html.includes('data-migration-detail="audit-settings" open'));
});

test('migration settings use the chosen destination and keep selections while refreshing old plans', async () => {
  const route = 'host/targets/migration/roles/destination/legacy';
  const destination = '/astrbot_plugin_private_companion/host/targets/migration/roles/destination';
  const page = harness({status: readyStatus(), targets: [{target_id: 'migration', roles: [{role_id: 'destination',
    role_name: '接收角色', legacy_api: '/astrbot_plugin_private_companion/' + route,
    model_api: destination + '/model', config_api: destination + '/config'}]}], responses: {
    ['GET ' + route]: {findings: [{source_id: 'private.host_configuration', state: 'ready'}]},
    ['GET ' + route + '/plan']: {plan_token: 'fresh-plan', change_count: 0, warnings: [], differences: []},
    ['GET ' + route + '/data-plan']: migrationDataFixture({journals: {batch_count: 3}}),
  }});
  page.mountMigration();
  await settle();
  page.change('', 'proactive', {setupLegacy: 'configuration-scope'});
  page.click('legacy-scan');
  await settle();
  page.click('legacy-plan');
  await settle();
  page.clickMigrationView('mapping');
  await settle();
  page.change('', '', {setupLegacy: 'import-journals'}, {checked: true});
  page.clickMigrationShortcut('settings', 'models');
  assert.equal(page.modelMounts.at(-1).options.endpoint, destination + '/model');
  assert.match(page.html, /迁移配置调整/);
  assert.match(page.html, /接收角色/);
  const previousPlans = page.calls.filter(call => call.route === route + '/plan').length;
  page.setModelBusy(true);
  page.clickMigrationShortcut('back', '');
  assert.match(page.html, /迁移配置调整/);
  assert.equal(page.calls.filter(call => call.route === route + '/plan').length, previousPlans);
  page.setModelBusy(false);
  page.clickMigrationShortcut('back', '');
  await settle();
  assert.equal(page.calls.filter(call => call.route === route + '/plan').length, previousPlans + 1);
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls.filter(call => call.route === route + '/plan').at(-1).body)),
    {configuration_scope: 'proactive'});
  assert.equal(page.navigation.length, 0, 'settings stay inside the guide');
  assert.equal(page.calls.some(call => call.method === 'POST'), false, 'opening or returning must not write');
  page.clickMigrationShortcut('settings', 'expression');
  assert.equal(page.editorCalls.at(-1).endpoint, destination + '/config');
  assert.deepEqual([...page.editorCalls.at(-1).kinds], ['expression.learning']);
  page.clickMigrationShortcut('back', '');
  await settle();
  page.clickMigrationView('mapping');
  await settle();
  assert.match(page.html, /data-setup-legacy="import-journals" checked/);
  page.clickMigrationShortcut('settings', 'news');
  assert.equal(page.editorCalls.at(-1).endpoint, '', 'global news settings must retain installation ownership');
  assert.deepEqual([...page.editorCalls.at(-1).views['proactive.contact'].sectionIds], ['news']);
  assert.match(page.html, /全局设置/);
  page.clickMigrationShortcut('back', '');
  await settle();
  const mounts = page.modelMounts.length + page.editorCalls.length;
  page.clickMigrationShortcut('settings', 'unreviewed-setting');
  assert.equal(page.modelMounts.length + page.editorCalls.length, mounts);
});

test('migration can use an explicitly selected V1 directory through scan, plan and apply', async () => {
  const root = 'C:\\AstrBot\\plugin_data\\astrbot_plugin_private_companion';
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const planRoute = route + '/plan';
  const targets = [{target_id: 't-first', roles: [{role_id: 'r-first',
    role_name: '星缘', legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const discovery = {v1_root: root, findings: [{source_id: 'private.host_configuration', state: 'ready'}]};
  const plan = {v1_root: root, findings: discovery.findings, change_count: 1, plan_token: 'selected-root-token',
    scope_status: [{scope: 'role.state', available: true}], intents: [], differences: [], warnings: []};
  const page = harness({status: readyStatus(), targets, responses: {
    ['GET ' + route]: discovery,
    ['GET ' + planRoute]: plan,
    ['POST ' + route + '/apply']: {v1_root: root, written_count: 1, written: ['role.state.weather'], skipped: []},
  }});
  page.mountMigration();
  await settle();
  page.change('', 't-first', {setupLegacy: 'target'});
  page.change('', 'r-first', {setupLegacy: 'role'});
  const scansBeforeRoot = page.calls.filter(item => item.method === 'GET' && item.route === route).length;
  page.change('', root, {setupLegacy: 'root'});
  assert.equal(page.calls.filter(item => item.method === 'GET' && item.route === route).length, scansBeforeRoot,
    'changing the optional source directory must not start a new scan implicitly');
  page.change('', 'proactive', {setupLegacy: 'configuration-scope'});
  page.click('legacy-scan');
  await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls.filter(item => item.method === 'GET' && item.route === route).at(-1).body)),
    {configuration_scope: 'proactive', v1_root: root});
  page.click('legacy-plan');
  await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls.filter(item => item.route === planRoute).at(-1).body)),
    {configuration_scope: 'proactive', v1_root: root});
  page.click('legacy-apply');
  await settle();
  const applied = page.calls.find(item => item.method === 'POST' && item.route === route + '/apply');
  assert.deepEqual(JSON.parse(JSON.stringify(applied.body)), {
    plan_token: 'selected-root-token', configuration_scope: 'proactive', v1_root: root});
});

test('data migration uses current-target availability and reports capped group imports', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', roles: [{role_id: 'r-first', role_name: '星缘',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const plan = {
    plan_token: 'data-plan-token',
    reaction_assets: {total: 0, ready: 0, batch_count: 0, batch_bytes: 0},
    journals: {total: 0, ready: 0, batch_count: 0, already_present: 0, samples: [], conflicts: []},
    worldbook: {total: 3, available_count: 0, general_count: 0, member_count: 0,
      disabled_member_count: 0, group_count: 3, selected_group_entry_count: 0,
      requires_id_confirmation: false, groups: [{source_group_id: '789', entry_count: 3}],
      rejected: [], samples: []},
    profile_data: {private_user_profiles: 0, worldbook_profiles: 0, dream_fragments: 0},
    work_delivery: {}, world_assets: {}, persona_sources: [],
    group_networks: [{source_group_id: '789', member_count: 256, edge_count: 80,
      omitted_member_count: 4, omitted_edge_count: 179, rejected_member_count: 0,
      rejected_edge_count: 0}],
    group_target_available: false, target_private_available: true, warnings: [],
  };
  const page = harness({status: readyStatus(), targets,
    responses: {['GET ' + route + '/data-plan']: plan}});
  page.mountMigration();
  await settle();
  page.change('', 't-first', {setupLegacy: 'target'});
  page.change('', 'r-first', {setupLegacy: 'role'});
  await enterMigrationDataStep(page);
  page.click('legacy-data-plan');
  await settle();

  const dataPlan = page.calls.find(item => item.route === route + '/data-plan');
  assert.deepEqual(JSON.parse(JSON.stringify(dataPlan.options)), {timeoutMs: 300000});

  assert.match(page.html, /当前目标可导入 0 条/);
  assert.match(page.html, /data-setup-legacy="import-worldbook"[^>]*disabled/);
  page.change('', '789', {setupLegacy: 'data-group'});
  page.click('legacy-data-plan');
  await settle();
  assert.match(page.html, /另有 4 位成员、179 条互动边超过当前版本上限/);
});

test('data migration does not present an unplanned memory database as empty', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', roles: [{role_id: 'r-first', role_name: '星缘',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const page = harness({status: readyStatus(), targets});
  page.mountMigration();
  await settle();
  page.change('', 't-first', {setupLegacy: 'target'});
  page.change('', 'r-first', {setupLegacy: 'role'});
  await openLegacyManual(page);

  assert.match(page.html, /独立记忆库（试算后显示规模）/);
  assert.match(page.html, /先读取资料清单/);
  assert.doesNotMatch(page.html, /独立记忆库（0 条 \/ 0 MB）/);
});

test('single-target migration exposes references and can import wardrobe text without gallery images', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', roles: [{role_id: 'r-first', role_name: '星缘',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const plan = {plan_token: 'reference-plan', source_persona_id: '',
    reaction_assets: {batch_count: 0}, journals: {batch_count: 0}, worldbook: {general_count: 0, member_count: 0},
    reference_images: {ready: 1, batch_count: 1, rejected: []},
    wardrobe_items: {ready: 1, batch_count: 1, image_count: 0, rejected: []},
    persona_sources: [{persona_id: '', label: '主角色（companions.json）', state: 'ready'}],
    archive: {state: 'absent'}, memory_database: {state: 'absent'},
    target_private_available: true, group_target_available: false, group_networks: []};
  const page = harness({status: readyStatus(), targets, responses: {
    ['GET ' + route + '/data-plan']: [plan, plan],
    ['POST ' + route + '/data-apply']: {source_persona_id: '', errors: [],
      reference_images: {reference_created: 0, reference_remaining: 0,
        wardrobe_created: 1, wardrobe_remaining: 0, wardrobe_images_skipped: 0}},
  }});
  page.mountMigration();
  await settle();
  assert.match(page.html, /先设置接收角色/);
  page.change('', 't-first', {setupLegacy: 'target'});
  page.change('', 'r-first', {setupLegacy: 'role'});
  await enterMigrationDataStep(page);
  page.click('legacy-data-plan');
  await settle();
  page.change('', '', {setupLegacy: 'import-reference-images'}, {checked: true});
  assert.match(page.html, /图片随角色使用，不按旧版私聊用户区分/);
  page.change('', '', {setupLegacy: 'import-reference-images'}, {checked: false});
  page.change('', '', {setupLegacy: 'import-wardrobe-items'}, {checked: true});
  page.change('', '', {setupLegacy: 'confirm-reference-persona-mapping'}, {checked: true});
  page.click('legacy-data-apply');
  await settle();

  const applied = page.calls.find(item => item.method === 'POST' && item.route === route + '/data-apply');
  assert.equal(applied.body.import_reference_images, false);
  assert.equal(applied.body.import_wardrobe_items, true);
  assert.equal(applied.body.confirm_reference_persona_mapping, true);
  assert.match(page.html, /衣柜新增 1 件/);
});

test('learning relationship profiles are counted and can be selected without original worldbook entries', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', roles: [{role_id: 'r-first', role_name: '星缘',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const plan = {plan_token: 'learning-social-plan', reaction_assets: {}, journals: {},
    worldbook: {general_count: 0, member_entry_count: 0, member_count: 2, member_profile_count: 2,
      available_count: 2, requires_id_confirmation: true, base_members_matches: false},
    persona_sources: [], group_networks: [], memory_database: {state: 'absent'},
    target_private_available: true, group_target_available: false};
  const page = harness({status: readyStatus(), targets, responses: {
    ['GET ' + route + '/data-plan']: plan,
    ['POST ' + route + '/data-apply']: {errors: [], worldbook: {members_imported: 2}}}});
  page.mountMigration();
  await settle();
  page.change('', 't-first', {setupLegacy: 'target'});
  page.change('', 'r-first', {setupLegacy: 'role'});
  await openLegacyManual(page);
  page.click('legacy-data-plan');
  await settle();
  assert.match(page.html, /2 个学习关系网节点/);
  assert.match(page.html, /人物描述、身份备注、相处边界与重要印象一起迁移/);
  assert.doesNotMatch(page.html, /data-setup-legacy="import-worldbook"[^>]*disabled/);
  page.change('', '', {setupLegacy: 'import-worldbook'}, {checked: true});
  page.change('', '', {setupLegacy: 'confirm-member-ids'}, {checked: true});
  assert.doesNotMatch(page.html, /data-setup-legacy-action="legacy-data-apply"[^>]*disabled/);
  page.click('legacy-data-apply');
  await settle();
  const applied = page.calls.find(item => item.method === 'POST' && item.route === route + '/data-apply');
  assert.equal(applied.body.import_worldbook, true);
  assert.equal(applied.body.confirm_member_ids, true);
});

test('旧版私聊用户单独试算和迁入，不必同时选择世界书或归档', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', roles: [{role_id: 'r-first', role_name: '星缘',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const plan = {plan_token: 'contacts-plan', reaction_assets: {}, journals: {}, worldbook: {},
    private_contacts: {ready: 2, already_present: 0, maintained_count: 1,
      samples: [{native_account_id: '200', nickname: '旧称呼', maintained: true, message_count: 12}]},
    persona_sources: [], group_networks: [], memory_database: {state: 'absent'},
    target_private_available: true, group_target_available: false};
  const page = harness({status: readyStatus(), targets, responses: {
    ['GET ' + route + '/data-plan']: plan,
    ['POST ' + route + '/data-apply']: {errors: [], private_contacts: {created: 2, pending: []}}}});
  page.mountMigration();
  await settle();
  page.change('', 't-first', {setupLegacy: 'target'});
  page.change('', 'r-first', {setupLegacy: 'role'});
  await openLegacyManual(page);
  page.click('legacy-data-plan');
  await settle();
  assert.match(page.html, /私聊用户与权限（2 位用户 · 0 位权限待迁入）/);
  assert.match(page.html, /旧称呼 · 200 · 手动维护 · 12 条历史消息/);
  assert.doesNotMatch(page.html, /data-setup-legacy-action="legacy-data-apply"[^>]*disabled/);
  page.click('legacy-data-apply');
  await settle();
  const applied = page.calls.find(item => item.method === 'POST' && item.route === route + '/data-apply');
  assert.equal(applied.body.import_private_contacts, true);
  assert.equal(applied.body.import_worldbook, false);
  assert.match(page.html, /旧版私聊用户/);
});

test('私聊用户卡已存在时，迁入页仍允许补迁管理员和私聊权限', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id:'t-first',roles:[{role_id:'r-first',legacy_api:'/astrbot_plugin_private_companion/'+route}]}];
  const plan = {plan_token:'authority-only',reaction_assets:{},journals:{},worldbook:{},
    private_contacts:{ready:0,already_present:4,authorization:{ready:1,core_users:1,permission_count:2}},
    persona_sources:[],group_networks:[],memory_database:{state:'absent'},target_private_available:true};
  const page = harness({status:readyStatus(),targets,responses:{['GET '+route+'/data-plan']:plan,
    ['POST '+route+'/data-apply']:{private_contacts:{created:0,authorization:{identity_restored:1,permissions_restored:2}}}}});
  page.mountMigration();await settle();
  page.change('','t-first',{setupLegacy:'target'});page.change('','r-first',{setupLegacy:'role'});
  await openLegacyManual(page);
  page.click('legacy-data-plan');await settle();
  assert.match(page.html,/0 位用户 · 1 位权限待迁入/);
  assert.doesNotMatch(page.html,/data-setup-legacy-action="legacy-data-apply"[^>]*disabled/);
  page.click('legacy-data-apply');await settle();
  assert.equal(page.calls.find(item=>item.method==='POST'&&item.route===route+'/data-apply').body.import_private_contacts,true);
});

test('关闭独立记忆库后不携带已自动选中的群记忆映射，不阻断单独迁入用户', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', roles: [{role_id: 'r-first',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const ownerId = 'a'.repeat(64);
  const plan = {plan_token: 'contacts-only', reaction_assets: {}, journals: {}, worldbook: {},
    private_contacts: {ready: 4}, persona_sources: [], group_networks: [],
    memory_database: {state: 'ready', assets: [{path: 'memory.db'}], identity_inventory: {owners: [
      {owner_id: ownerId, source: {scope: 'group', group_id: '555', platform: 'qq', bot_id: '1'}}]},
      group_targets: [{target_id: 't-group', group_id: '555', platform_id: 'qq', bot_id: '1',
        roles: [{role_id: 'r-group'}]}]},
    target_private_available: true};
  const page = harness({status: readyStatus(), targets, responses: {
    ['GET ' + route + '/data-plan']: plan,
    ['POST ' + route + '/data-apply']: {private_contacts: {created: 4}}}});
  page.mountMigration();
  await settle();
  page.change('', 't-first', {setupLegacy: 'target'});
  page.change('', 'r-first', {setupLegacy: 'role'});
  await openLegacyManual(page);
  page.click('legacy-data-plan');
  await settle();
  page.change('', '', {setupLegacy: 'import-memory-database'}, {checked: false});
  page.click('legacy-data-plan');
  await settle();
  const read = page.calls.filter(item => item.route === route + '/data-plan').at(-1);
  assert.equal(read.body.memory_group_mappings, undefined);
  assert.equal(read.body.memory_owner_ids, undefined);
  page.click('legacy-data-apply');
  await settle();
  const applied = page.calls.find(item => item.method === 'POST' && item.route === route + '/data-apply');
  assert.ok(applied);
  assert.equal(applied.body.import_private_contacts, true);
  assert.equal(applied.body.import_memory_database, false);
  assert.equal(applied.body.confirm_memory_mapping, false);
  assert.deepEqual(JSON.parse(JSON.stringify(applied.body.memory_group_mappings)), []);
  assert.deepEqual(JSON.parse(JSON.stringify(applied.body.memory_owner_ids)), []);
});

test('data migration previews the independent memory database and sends its explicit selection', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', roles: [{role_id: 'r-first', role_name: '星缘',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const plan = {
    plan_token: 'memory-plan-token',
    reaction_assets: {total: 0, ready: 0, batch_count: 0, batch_bytes: 0},
    journals: {total: 0, ready: 0, batch_count: 0, already_present: 0, samples: [], conflicts: []},
    worldbook: {total: 0, available_count: 0, general_count: 0, member_count: 0,
      disabled_member_count: 0, group_count: 0, selected_group_entry_count: 0,
      requires_id_confirmation: false, groups: [], rejected: [], samples: []},
    profile_data: {}, work_delivery: {}, world_assets: {}, persona_sources: [
      {persona_id: '', label: '主角色（companions.json）', state: 'ready'},
      {persona_id: 'persona-b', label: 'V1 人格：persona-b', state: 'ready'},
    ], group_networks: [],
    group_target_available: false, target_private_available: true, warnings: [],
    archive: {state: 'incomplete', file_count: 1047, total_bytes: 1056942445, excluded_count: 1},
    memory_database: {state: 'incomplete', table_count: 2, record_count: 3, total_bytes: 1024,
      excluded_count: 1, assets: [{path: 'memory_companion.db'}],
      table_counts: {memories: 1, timeline: 2}},
  };
  const page = harness({status: readyStatus(), targets,
    responses: {['GET ' + route + '/data-plan']: plan}});
  page.mountMigration();
  await settle();
  page.change('', 't-first', {setupLegacy: 'target'});
  page.change('', 'r-first', {setupLegacy: 'role'});
  await enterMigrationDataStep(page);
  page.click('legacy-data-plan');
  await settle();

  assert.match(page.html, /一份旧版人格资料可以添加多个 Bot 和伴侣角色去向/);
  assert.match(page.html, /旧版人格：persona-b/);
  assert.match(page.html, /独立记忆库表清单（2）/);
  assert.match(page.html, /data-setup-legacy="import-memory-database"[^>]*checked/);
  assert.doesNotMatch(page.html.match(/<input[^>]*data-setup-legacy="import-memory-database"[^>]*>/)?.[0] || '', /disabled/);
  assert.match(page.html, /其余可读文件可以归档，清单会标记不完整/);
  const archiveControl = page.html.match(/<input[^>]*data-setup-legacy="import-archive"[^>]*>/)?.[0] || '';
  assert.doesNotMatch(archiveControl, /disabled/);
  assert.match(archiveControl, /checked/);
  assert.match(page.html, /尽可能归档可读的原始资料/);
  assert.match(page.html, /data-setup-legacy-action="legacy-data-apply"/);
  page.click('legacy-data-apply');
  await settle();
  const applied = page.calls.find(item => item.method === 'POST' && item.route === route + '/data-apply');
  assert.ok(applied, JSON.stringify(page.calls));
  assert.deepEqual(JSON.parse(JSON.stringify(applied.options)), {timeoutMs: 300000});
  assert.equal(applied.body.import_memory_database, true);
  assert.equal(applied.body.import_archive, true);
});

test('an exact one-to-one persona match is only a removable candidate until the user previews and imports', async () => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const targets = [{target_id: 't-first', display_name: '账号一', roles: [{role_id: 'r-first',
    role_name: '角色一', persona_ids: ['persona-a'],
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}];
  const personaSources = [{persona_id: 'persona-a', label: 'V1 人格：persona-a', state: 'ready'}];
  const {page} = await legacyPersonaMappingPage({targets, personaSources});

  assert.match(page.html, /唯一人格 ID 匹配候选待核对/);
  assert.match(page.html, /旧版人格 ID 与一个当前版本角色精确匹配/);
  assert.match(page.html, /data-setup-legacy-batch-target[^>]*>[\s\S]*?<option value="t-first" selected>/);
  assert.match(page.html, /data-setup-legacy-batch-role[^>]*>[\s\S]*?<option value="r-first" selected\s*>/);
  assert.doesNotMatch(page.html, /data-setup-legacy-action="legacy-data-batch-apply"/);

  page.clickLegacyDestination('remove', 'persona-a', 'destination-1');
  assert.match(page.html, /尚未添加目标/);
  assert.doesNotMatch(page.html, /唯一人格 ID 匹配候选待核对/,
    'removing a suggestion must not make it reappear on the next render');
  page.clickLegacyDestination('add', 'persona-a');
  page.change('', 't-first', {setupLegacyBatchTarget: '', personaId: 'persona-a', destinationId: 'destination-2'});
  page.change('', 'r-first', {setupLegacyBatchRole: '', personaId: 'persona-a', destinationId: 'destination-2'});
  assert.doesNotMatch(page.html, /唯一人格 ID 匹配候选待核对/);

  const beforePreview = page.calls.length;
  page.click('legacy-data-batch-plan');
  await settle();
  assert.equal(page.calls.slice(beforePreview).filter(item => item.method === 'GET'
    && item.route === route + '/data-plan' && item.body?.source_persona_id === 'persona-a').length, 1);
  assert.equal(page.calls.some(item => item.method === 'POST' && item.route === route + '/data-apply'), false,
    'candidate selection and preview must never write imported data');
  assert.match(page.html, /data-setup-legacy-action="legacy-data-batch-apply"/);

  page.click('legacy-data-batch-apply');
  await settle();
  assert.equal(page.calls.filter(item => item.method === 'POST' && item.route === route + '/data-apply').length, 1,
    'the explicit import action is the first operation that writes persona data');
});

test('persona ID candidates stay unselected when matching is one-to-many or many-to-one', async t => {
  const route = 'host/targets/t-first/roles/r-first/legacy';
  const role = (targetId, roleId, personaIds) => ({target_id: targetId, display_name: targetId, roles: [{
    role_id: roleId, persona_ids: personaIds,
    legacy_api: '/astrbot_plugin_private_companion/' + `host/targets/${targetId}/roles/${roleId}/legacy`,
  }]});
  const cases = [
    ['one V1 persona matches multiple V2 roles', {
      targets: [role('t-first', 'r-first', ['persona-a']), role('t-second', 'r-second', ['persona-a'])],
      personaSources: [{persona_id: 'persona-a', state: 'ready'}],
    }],
    ['multiple V1 personas claim one V2 role', {
      targets: [role('t-first', 'r-first', ['persona-a', 'persona-b'])],
      personaSources: [{persona_id: 'persona-a', state: 'ready'}, {persona_id: 'persona-b', state: 'ready'}],
    }],
  ];
  for (const [label, fixture] of cases) {
    await t.test(label, async () => {
      const {page} = await legacyPersonaMappingPage(fixture);
      assert.match(page.html, /尚未添加目标/);
      assert.doesNotMatch(page.html, /data-destination-row=/);
      assert.doesNotMatch(page.html, /唯一人格 ID 匹配候选待核对/);
    });
  }
});

test('a V1 persona can map to multiple explicitly selected V2 destinations and each imports serially', async () => {
  const {page, firstRoute, secondRoute} = await plannedMultiDestinationPage();
  const firstPlanRoute = firstRoute + '/data-plan';
  const secondPlanRoute = secondRoute + '/data-plan';
  assert.equal(page.calls.filter(item => item.route === firstPlanRoute).length, 1);

  page.click('legacy-data-batch-plan');
  await settle();
  assert.equal(page.calls.filter(item => item.route === firstPlanRoute).length, 1,
    'an unassigned V1 persona must not fan out to any V2 target');

  page.clickLegacyDestination('add', 'persona-a');
  page.clickLegacyDestination('add', 'persona-a');
  page.change('', 't-first', {setupLegacyBatchTarget: '', personaId: 'persona-a', destinationId: 'destination-1'});
  page.change('', 'r-first', {setupLegacyBatchRole: '', personaId: 'persona-a', destinationId: 'destination-1'});
  page.change('', 't-first', {setupLegacyBatchTarget: '', personaId: 'persona-a', destinationId: 'destination-2'});
  page.change('', 'r-first', {setupLegacyBatchRole: '', personaId: 'persona-a', destinationId: 'destination-2'});
  assert.match(page.html, /已阻止重复的当前版本目标角色映射/);
  assert.match(page.html, /该当前版本目标角色已映射到另一个目的地/);
  page.change('', 't-second', {setupLegacyBatchTarget: '', personaId: 'persona-a', destinationId: 'destination-2'});
  page.change('', 'r-second', {setupLegacyBatchRole: '', personaId: 'persona-a', destinationId: 'destination-2'});

  const planningStart = page.calls.length;
  page.click('legacy-data-batch-plan');
  await settle();
  assert.deepEqual(page.calls.slice(planningStart).filter(item => [firstPlanRoute, secondPlanRoute].includes(item.route)
    && item.body?.source_persona_id === 'persona-a')
    .map(item => [item.method, item.route]), [
    ['GET', firstPlanRoute], ['GET', secondPlanRoute],
  ]);
  assert.deepEqual(page.calls.slice(planningStart).filter(item => [firstPlanRoute, secondPlanRoute].includes(item.route)
    && item.body?.source_persona_id === 'persona-a').map(item => JSON.parse(JSON.stringify(item.body))), [
    {source_persona_id: 'persona-a'}, {source_persona_id: 'persona-a'},
  ]);
  assert.match(page.html, /已试算 2 \/ 2 个目的地/);

  const applyingStart = page.calls.length;
  page.click('legacy-data-batch-apply');
  await settle();
  assert.deepEqual(page.calls.slice(applyingStart).filter(item =>
    item.route.endsWith('/data-plan') || item.route.endsWith('/data-apply'))
    .map(item => [item.method, item.route]), [
    ['GET', firstPlanRoute], ['POST', firstRoute + '/data-apply'],
    ['GET', secondPlanRoute], ['POST', secondRoute + '/data-apply'],
  ]);
  for (const call of page.calls.filter(item => item.method === 'POST' && item.route.endsWith('/data-apply'))) {
    assert.equal(call.body.source_persona_id, 'persona-a');
    assert.equal(call.body.import_reactions, true);
  }
  assert.match(page.html, /各目标结果已分别保留/);
});

test('multi-destination migration sends wardrobe selection and keeps remaining materials resumable', async () => {
  const result = {source_persona_id: 'persona-a', errors: [], reaction_created: 1,
    reaction_remaining: 0, journal_created: 0, journal_remaining: 0,
    journal_derivatives_remaining: 0,
    reference_images: {reference_created: 0, reference_remaining: 0,
      wardrobe_created: 1, wardrobe_remaining: 1, wardrobe_images_skipped: 0}};
  const {page, firstRoute} = await plannedMultiDestinationPage(result);
  page.clickLegacyDestination('add', 'persona-a');
  page.change('', 't-first', {setupLegacyBatchTarget: '', personaId: 'persona-a', destinationId: 'destination-1'});
  page.change('', 'r-first', {setupLegacyBatchRole: '', personaId: 'persona-a', destinationId: 'destination-1'});
  page.click('legacy-data-batch-plan');
  await settle();
  page.change('', '', {setupLegacyBatchOption: 'wardrobe-items', personaId: 'persona-a',
    destinationId: 'destination-1'}, {checked: true});
  page.change('', '', {setupLegacyBatchOption: 'confirm-reference-persona', personaId: 'persona-a',
    destinationId: 'destination-1'}, {checked: true});
  page.click('legacy-data-batch-apply');
  await settle();

  const applied = page.calls.find(item => item.method === 'POST' && item.route === firstRoute + '/data-apply');
  assert.equal(applied.body.import_reference_images, false);
  assert.equal(applied.body.import_wardrobe_items, true);
  assert.equal(applied.body.confirm_reference_persona_mapping, true);
  assert.match(page.html, /仍有后续批次或未导入项/);
  assert.match(page.html, /衣物 1 条待继续/);
});

test('a failed or unknown destination apply stops before later destinations', async t => {
  for (const [label, response, statusText] of [
    ['known item failure', {source_persona_id: 'persona-a', errors: [{code: 'write_failed'}]}, '执行失败'],
    ['unknown response', new Error('network timeout'), '结果未确认'],
  ]) {
    await t.test(label, async () => {
      const {page, firstRoute, secondRoute} = await plannedMultiDestinationPage(response);
      page.clickLegacyDestination('add', 'persona-a');
      page.clickLegacyDestination('add', 'persona-a');
      page.change('', 't-first', {setupLegacyBatchTarget: '', personaId: 'persona-a', destinationId: 'destination-1'});
      page.change('', 'r-first', {setupLegacyBatchRole: '', personaId: 'persona-a', destinationId: 'destination-1'});
      page.change('', 't-second', {setupLegacyBatchTarget: '', personaId: 'persona-a', destinationId: 'destination-2'});
      page.change('', 'r-second', {setupLegacyBatchRole: '', personaId: 'persona-a', destinationId: 'destination-2'});
      page.click('legacy-data-batch-plan');
      await settle();

      const secondPlanCount = page.calls.filter(item => item.route === secondRoute + '/data-plan').length;
      page.click('legacy-data-batch-apply');
      await settle();
      assert.equal(page.calls.filter(item => item.method === 'POST' && item.route === firstRoute + '/data-apply').length, 1);
      assert.equal(page.calls.filter(item => item.method === 'POST' && item.route === secondRoute + '/data-apply').length, 0);
      assert.equal(page.calls.filter(item => item.route === secondRoute + '/data-plan').length, secondPlanCount,
        'a later destination must not be rechecked after an earlier apply failure or unknown result');
      assert.match(page.html, new RegExp(statusText));
      assert.match(page.html, /目的地执行已停止|后续目的地未执行/);
    });
  }
});

function migrationDataFixture(extra = {}) {
  return {plan_token: 'data-current', source_persona_id: '',
    persona_sources: [{persona_id: '', label: '主角色', state: 'ready'},
      {persona_id: 'persona-b', label: '角色乙', state: 'ready'}],
    reaction_assets: {batch_count: 1}, journals: {}, expressions: {},
    worldbook: {general_count: 0, member_count: 0}, group_networks: [],
    archive: {state: 'ready', file_count: 2, total_bytes: 512, fingerprint: 'archive'},
    memory_database: {state: 'absent'}, target_private_available: true, ...extra};
}

test('mapping opens with a read-only inventory and source changes retain choices while refreshing the plan', async () => {
  const route = 'host/targets/private/roles/role/legacy';
  let finishPersona;
  const personaPending = new Promise(resolve => { finishPersona = resolve; });
  const initial = migrationDataFixture();
  const changed = migrationDataFixture({plan_token: 'data-persona-b', source_persona_id: 'persona-b',
    group_networks: [{source_group_id: 'new-group', member_count: 2, edge_count: 1}]});
  const page = harness({status: readyStatus(), targets: [{target_id: 'private', scope_kind: 'private',
    roles: [{role_id: 'role', legacy_api: '/astrbot_plugin_private_companion/' + route}]}], responses: {
    ['GET ' + route + '/data-plan']: [initial, personaPending, {...changed, plan_token: 'data-group'}],
    ['POST ' + route + '/data-apply']: {reaction_created: 1, errors: []},
  }});
  page.mountMigration();
  await settle();
  page.clickMigrationView('mapping');
  await settle();
  assert.equal(page.calls.filter(call => call.route.endsWith('/data-plan')).length, 1);
  assert.equal(page.calls.some(call => call.method === 'POST'), false);
  page.change('', 'persona-b', {setupLegacy: 'data-persona'});
  assert.match(page.html, /data-setup-legacy="data-persona"\s*>/);
  assert.match(page.html, /<option value="persona-b" selected/);
  assert.match(page.html, /aria-busy="true"/);
  assert.match(page.html, /正在刷新当前选择，可继续调整资料范围与映射/);
  assert.doesNotMatch(page.html, /data-setup-legacy-mapping-view="personas"[^>]*disabled/);
  assert.equal(page.calls.at(-1).body.source_persona_id, 'persona-b');
  finishPersona(changed);
  await settle();
  assert.match(page.html, /<option value="new-group"/);
  page.change('', 'new-group', {setupLegacy: 'data-group'});
  await settle();
  const query = page.calls.filter(call => call.route.endsWith('/data-plan')).at(-1).body;
  assert.equal(query.source_persona_id, 'persona-b');
  assert.equal(query.source_group_id, 'new-group');
  assert.match(page.html, /data-setup-legacy="data-group"\s*>/);
});

test('archive opt-out reaches the apply request and missing confirmations keep import disabled', async () => {
  const route = 'host/targets/private/roles/role/legacy';
  const plan = migrationDataFixture({journals: {batch_count: 2}});
  const page = harness({status: readyStatus(), targets: [{target_id: 'private', roles: [{role_id: 'role',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}], responses: {
    ['GET ' + route + '/data-plan']: plan,
    ['POST ' + route + '/data-apply']: {reaction_created: 1, journal_created: 2, errors: []},
  }});
  page.mountMigration();
  await settle();
  await openLegacyManual(page);
  page.click('legacy-data-plan');
  await settle();
  page.change('', '', {setupLegacy: 'import-archive'}, {checked: false});
  assert.doesNotMatch(page.html.match(/<input[^>]*data-setup-legacy="import-archive"[^>]*>/)[0], /checked/);
  page.change('', '', {setupLegacy: 'import-journals'}, {checked: true});
  assert.match(page.html, /data-setup-legacy-action="legacy-data-apply" disabled/);
  assert.match(page.html, /尚需确认：日记所属角色/);
  page.change('', '', {setupLegacy: 'confirm-persona-mapping'}, {checked: true});
  assert.doesNotMatch(page.html, /data-setup-legacy-action="legacy-data-apply" disabled/);
  page.click('legacy-data-apply');
  await settle();
  const applied = page.calls.find(call => call.method === 'POST' && call.route.endsWith('/data-apply'));
  assert.equal(applied.body.import_archive, false);
  assert.equal(applied.body.import_journals, true);
  assert.equal(applied.body.confirm_persona_mapping, true);
});

test('member-profile-only group sources can be selected and mapped through the group API', async () => {
  const route = 'host/targets/private/roles/role/legacy';
  const groupRoute = 'host/targets/group/roles/role/legacy-group-data';
  const plan = migrationDataFixture({worldbook: {member_profile_groups: [{source_group_id: '789', member_count: 2}]}});
  const page = harness({status: readyStatus(), targets: [{target_id: 'private', scope_kind: 'private', roles: [
    {role_id: 'role', legacy_api: '/astrbot_plugin_private_companion/' + route}]},
    {target_id: 'group', scope_kind: 'group', roles: [{role_id: 'role',
      legacy_group_data_api: '/astrbot_plugin_private_companion/' + groupRoute}]}], responses: {
    ['GET ' + route + '/data-plan']: plan,
    ['GET ' + groupRoute + '/plan']: {plan_token: 'group-plan', source_group_id: '789', group_worldbook: {members: 2, entries: 0}},
    ['POST ' + groupRoute + '/apply']: {group_worldbook: {state: 'imported', members_imported: 2}},
  }});
  page.mountMigration();
  await settle();
  await openLegacyManual(page);
  page.click('legacy-data-plan');
  await settle();
  assert.match(page.html, /<option value="789"[^>]*>群 789[^<]*2 份成员资料/);
  page.change('', '789', {setupLegacy: 'data-group'});
  await settle();
  page.clickMigrationView('groups', true);
  assert.match(page.html, /选择对应的当前群与角色，核对群号后迁入群资料和已学表达/);
  assert.match(page.html, /表达只进入同号群/);
  page.change('', 'group', {setupLegacy: 'group-target'});
  page.change('', 'role', {setupLegacy: 'group-role'});
  page.click('legacy-group-data-plan');
  await settle();
  const preview = page.calls.find(call => call.route === groupRoute + '/plan');
  assert.equal(preview.body.source_group_id, '789');
  page.change('', '', {setupLegacy: 'group-confirm-mapping'}, {checked: true});
  page.change('', '', {setupLegacy: 'group-confirm-member-ids'}, {checked: true});
  page.click('legacy-group-data-apply');
  await settle();
  const applied = page.calls.find(call => call.method === 'POST' && call.route === groupRoute + '/apply');
  assert.equal(applied.body.import_group_network, false);
  assert.equal(applied.body.import_group_worldbook, true);
  assert.equal(applied.body.confirm_member_ids, true);
});

test('one chosen persona can preview member-only worldbook without deciding unrelated personas', async () => {
  const route = 'host/targets/private/roles/role/legacy';
  const base = migrationDataFixture();
  const scoped = migrationDataFixture({source_persona_id: 'persona-b',
    worldbook: {general_count: 0, member_count: 2, requires_id_confirmation: true}});
  const page = harness({status: readyStatus(), targets: [{target_id: 'private', roles: [{role_id: 'role',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}], responses: {
    ['GET ' + route + '/data-plan']: [base, scoped],
  }});
  page.mountMigration();
  await settle();
  /* 默认路径先读来源，第 2 步自动汇总范围；逐项能力在「高级：逐项迁移」里。 */
  await enterMigrationDataStep(page, {mapping: true});
  page.click('legacy-data-plan');
  await settle();
  page.clickLegacyDestination('add', 'persona-b');
  page.change('', 'private', {setupLegacyBatchTarget: '', personaId: 'persona-b', destinationId: 'destination-1'});
  page.change('', 'role', {setupLegacyBatchRole: '', personaId: 'persona-b', destinationId: 'destination-1'});
  page.click('legacy-data-batch-plan');
  await settle();
  assert.equal(page.calls.filter(call => call.route.endsWith('/data-plan')).length, 2);
  assert.doesNotMatch(page.html, /data-setup-legacy-batch-option="worldbook"[^>]*disabled/);
  page.change('', '', {setupLegacyBatchOption: 'worldbook', personaId: 'persona-b', destinationId: 'destination-1'}, {checked: true});
  assert.match(page.html, /data-setup-legacy-batch-option="confirm-members"/);
});

test('late migration responses cannot repopulate a newly mounted workspace', async () => {
  const route = 'host/targets/private/roles/role/legacy';
  let finishPlan;
  const pending = new Promise(resolve => { finishPlan = resolve; });
  const page = harness({status: readyStatus(), targets: [{target_id: 'private', roles: [{role_id: 'role',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}], responses: {['GET ' + route + '/data-plan']: pending}});
  page.mountMigration();
  await settle();
  await openLegacyManual(page);
  page.click('legacy-data-plan');
  page.unmount();
  page.mountMigration();
  await settle();
  await openLegacyManual(page);
  finishPlan(migrationDataFixture({persona_sources: [{persona_id: 'previous-page', state: 'ready'}]}));
  await settle();
  assert.doesNotMatch(page.html, /previous-page/);
  assert.match(page.html, /尚未试算|先读取资料清单/);
  assert.doesNotMatch(page.html, /aria-busy="true"/);
});

test('a pending configuration scan does not block mapping navigation or inventory selections', async () => {
  const route = 'host/targets/private/roles/role/legacy';
  let finishScan;
  const pending = new Promise(resolve => { finishScan = resolve; });
  const page = harness({status: readyStatus(), targets: [{target_id: 'private', roles: [{role_id: 'role',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}], responses: {
    ['GET ' + route]: pending,
    ['GET ' + route + '/data-plan']: migrationDataFixture(),
  }});
  page.mountMigration();
  page.clickMigrationWizard('next');
  page.clickMigrationView('mapping');
  await settle();
  assert.equal(page.calls.filter(call => call.route.endsWith('/data-plan')).length, 1);
  assert.match(page.html, /正在只读检查旧版来源/);
  assert.match(page.html, /aria-busy="true"/);
  page.change('', '', {setupLegacy: 'import-reactions'}, {checked: false});
  assert.doesNotMatch(page.html.match(/<input[^>]*data-setup-legacy="import-reactions"[^>]*>/)[0], /checked/);
  page.clickMigrationView('personas', true);
  page.clickLegacyDestination('add', 'persona-b');
  assert.match(page.html, /data-setup-legacy-batch-target/);
  assert.match(page.html, /data-setup-legacy-mapping-view="personas" aria-pressed="true"/);
  finishScan({findings: [{source_id: 'private.host_configuration', state: 'ready'}]});
  await settle();
  assert.doesNotMatch(page.html, /aria-busy="true"/);
});

test('out-of-order persona and group previews preserve the latest selection and edits', async t => {
  for (const field of ['data-persona', 'data-group']) {
    for (const failOlder of [false, true]) {
      await t.test(`${field}, older request ${failOlder ? 'fails' : 'finishes'}`, async () => {
        const route = 'host/targets/private/roles/role/legacy';
        let finishOlder, rejectOlder;
        const pending = new Promise((resolve, reject) => { finishOlder = resolve; rejectOlder = reject; });
        const initial = migrationDataFixture({group_networks: [{source_group_id: 'g1'}, {source_group_id: 'g2'}]});
        const latest = migrationDataFixture({plan_token: 'latest', reaction_assets: {batch_count: 2},
          source_group_id: field === 'data-group' ? 'g2' : '', group_networks: initial.group_networks});
        const page = harness({status: readyStatus(), targets: [{target_id: 'private', roles: [{role_id: 'role',
          legacy_api: '/astrbot_plugin_private_companion/' + route}]}], responses: {
          ['GET ' + route + '/data-plan']: [initial, pending, latest],
        }});
        page.mountMigration();
        await settle();
        page.clickMigrationView('mapping');
        await settle();
        page.change('', field === 'data-persona' ? 'persona-b' : 'g1', {setupLegacy: field});
        page.change('', '', {setupLegacy: 'import-reactions'}, {checked: false});
        page.change('', field === 'data-persona' ? '' : 'g2', {setupLegacy: field});
        await settle();
        assert.equal(page.calls.filter(call => call.route.endsWith('/data-plan')).length, 3);
        assert.match(page.html, /表情素材（2 张）/);
        assert.doesNotMatch(page.html, /aria-busy="true"/);
        if (failOlder) rejectOlder(new Error('旧请求的错误不能覆盖新选择'));
        else finishOlder(migrationDataFixture({reaction_assets: {batch_count: 99}}));
        await settle();
        assert.match(page.html, /表情素材（2 张）/);
        assert.doesNotMatch(page.html, /表情素材（99 张）|旧请求的错误不能覆盖新选择/);
        assert.doesNotMatch(page.html.match(/<input[^>]*data-setup-legacy="import-reactions"[^>]*>/)[0], /checked/);
        if (field === 'data-group') assert.match(page.html, /<option value="g2" selected/);
      });
    }
  }
});

test('an import locks conflicting edits and duplicate writes while result navigation stays available', async () => {
  const route = 'host/targets/private/roles/role/legacy';
  let finishImport;
  const pending = new Promise(resolve => { finishImport = resolve; });
  const page = harness({status: readyStatus(), targets: [{target_id: 'private', roles: [{role_id: 'role',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}], responses: {
    ['GET ' + route + '/data-plan']: migrationDataFixture(),
    ['POST ' + route + '/data-apply']: pending,
  }});
  page.mountMigration();
  await settle();
  page.clickMigrationView('mapping');
  await settle();
  page.click('legacy-data-apply');
  assert.match(page.html, /class="setup-legacy-data-controls" disabled/);
  page.change('', 'persona-b', {setupLegacy: 'data-persona'});
  page.click('legacy-data-apply');
  page.click('legacy-data-plan');
  page.clickMigrationView('review');
  assert.match(page.html, /data-setup-legacy-view="review" aria-pressed="true"/);
  assert.match(page.html, /data-migration-step="2"/);
  assert.equal(page.calls.filter(call => call.method === 'POST').length, 1);
  assert.equal(page.calls.filter(call => call.route.endsWith('/data-plan')).length, 1);
  finishImport({reaction_created: 1, reaction_remaining: 0, errors: []});
  await settle();
  await openLegacyManual(page);
  assert.match(page.html, /最近一次资料导入/);
  assert.match(page.html, /表情素材/);
  assert.doesNotMatch(page.html, /class="setup-legacy-data-controls" disabled/);
  assert.equal(page.calls.filter(call => call.route.endsWith('/data-plan')).at(-1).body.source_persona_id, undefined);
  page.click('legacy-data-plan');
  await settle();
  assert.match(page.html, /最近一次资料导入/);
  assert.match(page.html, /表情素材/);
});

test('one selected persona can import without mapping unrelated personas', async () => {
  const route = 'host/targets/private/roles/role/legacy';
  const plan = migrationDataFixture({source_persona_id: 'persona-b', source_fingerprint: 'source'});
  const page = harness({status: readyStatus(), targets: [{target_id: 'private', roles: [{role_id: 'role',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}], responses: {
    ['GET ' + route + '/data-plan']: [migrationDataFixture(), plan],
    ['POST ' + route + '/data-apply']: {source_persona_id: 'persona-b', reaction_created: 1, errors: []},
  }});
  page.mountMigration();
  await settle();
  page.clickMigrationView('mapping');
  await settle();
  page.clickLegacyDestination('add', 'persona-b');
  page.change('', 'private', {setupLegacyBatchTarget: '', personaId: 'persona-b', destinationId: 'destination-1'});
  page.change('', 'role', {setupLegacyBatchRole: '', personaId: 'persona-b', destinationId: 'destination-1'});
  page.click('legacy-data-batch-plan');
  await settle();
  page.click('legacy-data-batch-apply');
  await settle();
  const applied = page.calls.filter(call => call.method === 'POST' && call.route.endsWith('/data-apply'));
  assert.equal(applied.length, 1);
  assert.equal(applied[0].body.source_persona_id, 'persona-b');
  assert.match(page.html, /本批已完成/);
});

test('review keeps three read-only blocks and a separate per-item entry', async () => {
  const route = 'host/targets/private/roles/role/legacy';
  const page = harness({status: readyStatus(), targets: [{target_id: 'private', roles: [{role_id: 'role',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}], responses: {
    ['GET ' + route]: {findings: [{source_id: 'private.host_configuration', state: 'unreadable'}]},
    ['GET ' + route + '/data-plan']: migrationDataFixture({journals: {batch_count: 3}}),
  }});
  page.mountMigration();
  await settle();
  page.clickMigrationWizard('next');
  await settle();
  assert.match(page.html, /data-migration-step="2"/);
  /* 核对页：状态行 + 恰好三个区块，没有任何迁移写入按钮。 */
  const reviewHtml = page.html.split('setup-migration-review-step')[1].split('setup-migration-run-step')[0];
  assert.match(reviewHtml, /旧版来源/);
  assert.match(reviewHtml, /重新检测/);
  assert.match(reviewHtml, /<h3>配置变化<\/h3>/);
  assert.match(reviewHtml, /<h3>变更与失效明细<\/h3>/);
  assert.match(reviewHtml, /<h3>迁移范围<\/h3>/);
  assert.equal((reviewHtml.match(/<h3>/g) || []).length, 3);
  assert.doesNotMatch(page.html, /data-setup-legacy-action="legacy-auto"/);
  assert.doesNotMatch(page.html, /写入当前版本/);
  assert.match(page.html, /data-setup-legacy-wizard="manual"/);

  await openLegacyManual(page);
  assert.match(page.html, /逐项处理旧版资料/);
  assert.match(page.html, /data-setup-legacy-wizard="back-to-oneclick"/);
  page.clickMigrationView('current', true);
  assert.match(page.html, /data-setup-legacy-mapping-view="current" aria-pressed="true"/);
  page.clickMigrationWizard('back-to-oneclick');
  await settle();
  assert.match(page.html, /data-migration-step="2"/);
});

test('a group preview cannot restore a mapping after its destination changes', async () => {
  const route = 'host/targets/private/roles/role/legacy';
  const groupRoute = 'host/targets/group/roles/role/legacy-group-data';
  let finishOld;
  const pending = new Promise(resolve => { finishOld = resolve; });
  const page = harness({status: readyStatus(), targets: [{target_id: 'private', roles: [{role_id: 'role',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]},
    {target_id: 'group', scope_kind: 'group', roles: [{role_id: 'role', legacy_group_data_api:
      '/astrbot_plugin_private_companion/' + groupRoute}]}], responses: {
    ['GET ' + route + '/data-plan']: migrationDataFixture({group_networks: [{source_group_id: 'g1'}]}),
    ['GET ' + groupRoute + '/plan']: pending,
  }});
  page.mountMigration();
  await settle();
  page.clickMigrationView('mapping');
  await settle();
  page.change('', 'g1', {setupLegacy: 'data-group'});
  await settle();
  page.change('', 'group', {setupLegacy: 'group-target'});
  page.change('', 'role', {setupLegacy: 'group-role'});
  page.click('legacy-group-data-plan');
  page.change('', '', {setupLegacy: 'group-target'});
  finishOld({plan_token: 'stale', source_group_id: 'g1', target: {group_id: 'stale-destination'}});
  await settle();
  assert.doesNotMatch(page.html, /stale-destination|data-setup-legacy="group-confirm-mapping"/);
});

test('a persona preview cannot attach its plan to an edited destination', async () => {
  const route = 'host/targets/private/roles/role/legacy';
  let finishOld;
  const pending = new Promise(resolve => { finishOld = resolve; });
  const page = harness({status: readyStatus(), targets: [{target_id: 'private', roles: [{role_id: 'role',
    legacy_api: '/astrbot_plugin_private_companion/' + route}]}], responses: {
    ['GET ' + route + '/data-plan']: [migrationDataFixture(), pending],
  }});
  page.mountMigration();
  await settle();
  page.clickMigrationView('mapping');
  await settle();
  page.clickLegacyDestination('add', 'persona-b');
  page.change('', 'private', {setupLegacyBatchTarget: '', personaId: 'persona-b', destinationId: 'destination-1'});
  page.change('', 'role', {setupLegacyBatchRole: '', personaId: 'persona-b', destinationId: 'destination-1'});
  page.click('legacy-data-batch-plan');
  page.change('', '', {setupLegacyBatchRole: '', personaId: 'persona-b', destinationId: 'destination-1'});
  finishOld(migrationDataFixture({source_persona_id: 'persona-b', expressions: {remaining: 99}}));
  await settle();
  assert.doesNotMatch(page.html, /data-setup-legacy-batch-option="expressions"/);
  assert.doesNotMatch(page.html, /data-setup-legacy-action="legacy-data-batch-apply"/);
});
