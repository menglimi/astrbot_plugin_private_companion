"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const page = name => fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋", name), "utf8");
const engineURL = "https://host.invalid/api/plugin/page/asset/voxel-room.js?v=fixture&asset_token=rewritten";

function element() {
  const classes = new Set();
  return {innerHTML: "", dataset: {}, isConnected: true, addEventListener() {}, setAttribute() {},
    removeAttribute() {}, querySelector: () => null, querySelectorAll: () => [], closest: () => null,
    focus() {}, remove() {}, insertAdjacentHTML() {},
    classList: {add: name => classes.add(name), remove: name => classes.delete(name),
      contains: name => classes.has(name), toggle: (name, on) => on ? classes.add(name) : classes.delete(name)}};
}

// Run the real app, shell and editor (in their published order) with recorded timers, navigation,
// and a substitute only for the host/data and WebGL APIs. No open tab can leak IO into the tests.
function cottage({mode = 'lite', hash = '#life', engineLoaded = false, hidden = false} = {}) {
  const names = ['navigationList','main','shellPanel','shellSide','stageFurniture','stageScene','detailDialog',
    'searchDialog','globalSearch','searchButton','toast','homeShell'];
  const nodes = Object.fromEntries(names.map(name => ['#' + name, element()]));
  for (const selector of ['[data-home-environment]', '[data-voxel-room]']) nodes[selector] = element();
  const editor = element(), canvas = element(), directoryCount = element();
  const timers = new Map(), microtasks = [], frames = [], warnings = [], scripts = [], listeners = {};
  let nextId = 0, scope = 'role-a';
  const events = {mounts: 0, disposals: 0, entrances: 0, summaries: 0, scopes: 0, workspaces: 0};
  const document = {
    documentElement: {dataset: {performanceMode: mode}}, body: element(), hidden,
    addEventListener(name, cb) { (listeners[name] ||= []).push(cb); },
    getElementById(id) { return id === 'companion-full-assets'
      ? {content: {querySelector: s => s === 'script[data-voxel-engine]' ? {src: engineURL} : null}}
      : nodes['#' + id] || null; },
    querySelector(s) {
      if (s === '.room-stage canvas') return events.mounts > events.disposals ? canvas : null;
      if (s === '#roomEditor') return nodes['#shellSide'].innerHTML.includes('id="roomEditor"') ? editor : null;
      if (s === '[data-home-agenda-clock]') return nodes['#shellSide'].innerHTML.includes('data-home-agenda-clock') ? directoryCount : null;
      return nodes[s] || null;
    },
    querySelectorAll: () => [], createElement: () => element(), head: {appendChild: script => scripts.push(script)},
  };
  const window = {addEventListener() {}, scrollTo() {}, matchMedia: () => ({matches: false}),
    CompanionProduct: {connect: () => new Promise(() => {}), scopeKey: () => scope, hosted: () => false,
      role: () => null, roleTarget: () => null, capability: () => null, appearance: () => ({motion: 'full'}),
      renderScope() { events.scopes++; }, connection: () => ({state: 'checking'}),
      stateCard: () => '<p>当前状态</p>', scheduleCard: () => '<p>今日安排</p>', syncLight() {},
      refreshSummary: async () => { events.summaries++; }, unmountWorkspace() {}, mountWorkspace() {events.workspaces++;},
      data: {}, targets: () => [], workspace: () => '<div class="product-workspace-frame"></div>'},
    CompanionEntrance: {run() {events.entrances++; return {done: Promise.resolve(), ready: Promise.resolve()};}}};
  const engine = {mount(_node, options) {
    if (mode === 'lite') {
      assert.ok(document.body.classList.contains('room-editing'), 'measure after the stage expands');
      assert.equal(options.reducedMotion, true);
    }
    events.mounts++;
    const layout = options.layout || {items: {}};
    return {setFurnitureCatalog() {}, getLayout: () => layout, setLayout() {}, setStationLabels() {}, setAtmosphere() {}, setLight() {},
      setActive() {}, resize() {}, setEditing() {}, getState: () => ({storedFurniture: [], houseStyle: 'classic'}),
      getCatalog: () => ({houseStyles: {}, styles: {}, surfaces: {}}), dispose() { events.disposals++; }};
  }};
  if (engineLoaded) window.PrivateCompanionHomeRoom3D = engine;
  const location = {hash};
  const history = {replaceState: (_s, _t, next) => { location.hash = next; }, pushState: (_s, _t, next) => { location.hash = next; }};
  const context = vm.createContext({window, document, location, history, structuredClone, localStorage: {getItem: () => null, setItem() {}},
    matchMedia: window.matchMedia, console: {warn: (...args) => warnings.push(args)},
    setTimeout(cb, delay) { timers.set(++nextId, {cb, delay}); return nextId; }, clearTimeout: id => timers.delete(id),
    requestAnimationFrame: cb => frames.push(cb), queueMicrotask: cb => microtasks.push(cb),
    ResizeObserver: class { observe() {} }});
  const run = source => vm.runInContext(source, context);
  for (const name of ['shell.js','home-environment.js','app.js','room-editor.js']) run(page(name));
  return {run, window, document, nodes, frames, microtasks, timers, events, scripts, warnings, location,
    setScope(key) { scope = key; }, loaded() { window.PrivateCompanionHomeRoom3D = engine; scripts.at(-1).onload(); },
    setHidden(hidden) { document.hidden = hidden; for (const cb of listeners.visibilitychange || []) cb(); }};
}

test('lite 首页、状态深链、目录与配置页不自动挂载 3D 或等待入场', () => {
  for (const hash of ['#life', '#life/state', '#manage/appearance']) {
    const c = cottage({hash, engineLoaded: true}); // Even a cached engine is opt-in.
    assert.equal(c.document.documentElement.dataset.roomReady, '');
    assert.equal(c.location.hash, hash);
    assert.match(c.nodes['#stageFurniture'].innerHTML, /data-shell-furniture="desk"/);
    c.run("navigate('life','state')");
    assert.match(c.nodes['#shellPanel'].innerHTML, /id="roleStateWorkspace"/);
    c.run("navigate('life','directory')");
    assert.match(c.nodes['#shellPanel'].innerHTML, /data-furniture="calendar"/);
    assert.doesNotMatch(c.nodes['#shellPanel'].innerHTML, /data-locate-furniture/);
    c.run("openFurniture('calendar')");
    assert.equal(c.location.hash, '#life/state');
    c.run("chooseFurniture('desk')");
    assert.match(c.nodes['#shellPanel'].innerHTML, /product-workspace-frame/);
    c.run("navigate('manage','appearance')");
    assert.match(c.nodes['#shellPanel'].innerHTML, /id="appearanceWorkspace"/);
    assert.ok(c.events.scopes && c.events.workspaces);
    assert.deepEqual([c.events.mounts, c.microtasks.length, c.events.entrances, c.scripts.length], [0, 0, 0, 0]);
    assert.deepEqual(c.warnings, []);
  }
});

test('lite 只有进入布置才按宿主地址下载一次，保存或取消后释放场景', async () => {
  const c = cottage();
  c.run("showDetail('arrange')");
  const duplicate = c.run('openRoomEditor()');
  assert.equal(c.scripts.length, 1);
  assert.equal(c.scripts[0].src, engineURL);
  assert.equal(c.events.mounts, 0);
  c.loaded(); await duplicate;
  assert.equal(c.events.mounts, 1);
  assert.match(c.nodes['#shellSide'].innerHTML, /data-room-save/);
  assert.equal(c.microtasks.length, 0);
  c.run('finishRoomEditor(true)');
  assert.ok(c.run('state.roomLayouts[layoutKey()]'));
  assert.equal(c.events.disposals, 1);
  assert.equal(c.document.body.classList.contains('room-editing'), false);
  assert.match(c.nodes['#shellSide'].innerHTML, /今日安排/);
  c.run("navigate('life','state');backHome()");
  assert.equal(c.events.mounts, 1);
  await c.run('openRoomEditor()');
  c.run('finishRoomEditor(false)');
  assert.equal(c.scripts.length, 1);
  assert.equal(c.events.disposals, 2);
  assert.deepEqual(c.warnings, []);
});

test('下载布置资源时切换页面、角色或隐藏标签页不会启动迟到的场景', async () => {
  for (const change of [c => c.run("navigate('manage','appearance')"), c => c.setScope('role-b'), c => c.setHidden(true)]) {
    const c = cottage();
    const opening = c.run('openRoomEditor()');
    change(c); c.loaded(); await opening;
    assert.equal(c.events.mounts, 0);
    assert.equal(c.document.body.classList.contains('room-editing'), false);
  }
});

test('按需加载失败仍能导航和重试', async () => {
  const c = cottage();
  const failed = c.run('openRoomEditor()');
  c.scripts[0].onerror(); await failed;
  assert.equal(c.document.body.classList.contains('room-editing'), false);
  assert.match(c.nodes['#toast'].textContent, /重试/);
  c.run("navigate('manage','appearance');backHome()");
  const retry = c.run('openRoomEditor()');
  assert.equal(c.scripts.length, 2);
  c.loaded(); await retry;
  assert.equal(c.events.mounts, 1);
});

test('lite 首页用分钟刷新，进入管理页和后台时取消天气及摘要定时器', async () => {
  const c = cottage();
  const clock = [...c.timers.values()].find(t => t.delay === 60000);
  assert.ok(clock);
  await clock.cb(); assert.equal(c.events.summaries, 1);
  c.run("navigate('manage','appearance')");
  assert.equal(c.timers.size, 0);
  c.setHidden(true); c.setHidden(false);
  assert.equal(c.events.summaries, 1);
  assert.equal(c.timers.size, 0);
  c.run('backHome()'); assert.equal(c.timers.size, 2);
  c.setHidden(true); assert.equal(c.timers.size, 0);
  c.setHidden(false); assert.equal(c.timers.size, 2);
  assert.equal(c.events.summaries, 2);
});

test('WebGL 不可用时回到紧凑首页，家具和配置页仍可用', async () => {
  const c = cottage({engineLoaded: true});
  c.window.PrivateCompanionHomeRoom3D.mount = () => { throw new Error('WebGL unavailable'); };
  await c.run('openRoomEditor()');
  assert.equal(c.document.body.classList.contains('room-editing'), false);
  assert.equal(c.run('arrangementDraft'), null);
  assert.match(c.nodes['#toast'].textContent, /重试/);
  c.run("navigate('life','directory')");
  assert.match(c.nodes['#shellPanel'].innerHTML, /data-furniture="desk"/);
  c.run("navigate('manage','appearance')");
  assert.equal(c.location.hash, '#manage/appearance');
});

test('lite 后台打开没有时钟或加载请求，恢复可见时补上首页', () => {
  const c = cottage({hidden: true});
  assert.equal(c.timers.size, 0);
  assert.equal(c.nodes['#shellSide'].innerHTML, '');
  c.setHidden(false);
  assert.match(c.nodes['#shellSide'].innerHTML, /今日安排/);
  assert.equal(c.events.summaries, 1);
  assert.equal(c.timers.size, 2);
  assert.equal(c.events.mounts, 0);
});

test('full 模式继续挂载场景、安排入场并保持 15 秒摘要刷新', () => {
  const c = cottage({mode: 'full', engineLoaded: true});
  assert.equal(c.events.mounts, 1);
  for (const task of c.microtasks.splice(0)) task();
  assert.equal(c.events.entrances, 1);
  assert.ok([...c.timers.values()].some(t => t.delay === 15000));
  c.run("navigate('life','directory')");
  assert.match(c.nodes['#shellPanel'].innerHTML, /data-locate-furniture="calendar"/);
});
