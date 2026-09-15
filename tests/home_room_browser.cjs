/* Targeted browser integration check. All API requests are intercepted; no bot
 * data is changed. Run with PC_PLAYWRIGHT_DIR pointing to playwright's package
 * directory, and optionally PC_ROOM_ARTIFACTS for screenshots. */
const { createRequire } = require('node:module');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const { createRoomLayoutFixture } = require('./room_layout_fixture.cjs');
const { chromium } = process.env.PC_PLAYWRIGHT_DIR
  ? createRequire(path.join(process.env.PC_PLAYWRIGHT_DIR, 'package.json'))('playwright')
  : require('playwright');
const pageRoot = path.resolve(__dirname, '../pages/companion-panel');
const fixture = {
  plugin: { bot_name: '小满' }, features: [],
  daily_state: { date: '2026-09-13', energy: 78, mood_bias: '宁静', location: '家中 · 窗边', weather: '多云，24°C', sleep: '昨夜睡得很好', note: '窗边的薄荷又长了新叶。' },
  daily_timeline: { segments: [
    { window: '08:00–09:00', activity: '早餐与晨间阅读', clock_status: 'completed', evidence_lifecycle: 'completed' },
    { window: '14:00–16:00', activity: '读完那本没看完的书', clock_status: 'active', evidence_lifecycle: 'planned' },
    { window: '17:00–18:00', activity: '准备晚餐', clock_status: 'planned', evidence_lifecycle: 'planned' },
  ] },
  life_observation: {
    current_plan: { time: '14:00', end: '16:00', activity: '读完那本没看完的书', clock_status: 'active' },
    dream: { date: '2026-09-13', label: '旧书店里的雨', content: '梦见一家很小的书店，窗外下着雨。书页间夹着一张写到一半的明信片，落款是一个熟悉的名字。', afterglow: '醒来还记得纸张的气味。' },
    diaries: [
      { date: '2026-09-12', summary: '傍晚一起散了会儿步，记住了路边一间小店。', body: '从巷口慢慢走回来，路灯已经亮了。' },
      { date: '2026-09-13', summary: '今天读到一段很喜欢的话，想找个合适的时候讲给你听。', body: '午后的光从窗边移到了书桌。\n\n书里写：把每个平凡的日子认真过完，也是一件很了不起的事。\n\n我给薄荷浇了水，又在便签上添了一件想做的事。' },
    ],
  },
  daily_outfit: { enabled: true, available: false },
  companion_plugins: { content: { installed: true, enabled: true, available: true } },
  bookshelf: { public_count: 2, public_books: [ { title: '雨停之前', summary: '关于一封没能寄出的信。' }, { title: '小城来信', summary: '一些平凡日子的收藏。' } ],
    memo_notes: { active: 2, items: [ { id: 'memo-1', content: '周末去逛旧书店', status: 'active', repeat: 'none' }, { id: 'memo-2', content: '记得给薄荷浇水', status: 'active', repeat: 'none' } ] },
    secret_books: [{ title: '不应显示的夹层资料', body: '不可从这里读取' }],
  },
  news: { enabled: true, last_digest: { headline: '城市里，那些安静生长的口袋花园', source: '生活观察', impression: '喜欢把一小块闲置的空地慢慢变成花园的想法。', link: 'https://example.com/garden' } },
  web_exploration: { enabled: true, last_digest: { topic: '在窗边养一盆薄荷', note: '薄荷喜欢明亮的散射光，浇水前先摸摸土壤。' } },
  personal_goals: { active_count: 2, items: [ { title: '每月读完两本书', progress: 50, status_label: '进行中', next_step: '继续读完《小城来信》' }, { title: '让窗台长成小花园', progress: 35, status_label: '进行中', next_step: '记录薄荷的新叶' } ] },
};

test('voxel room: data, interactions, persona isolation, camera, accessibility and layout', { timeout: 240000 }, async () => {
  const server = http.createServer(async (req, res) => {
    try {
      const rel = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).slice(1) || 'index.html';
      const file = path.resolve(pageRoot, rel);
      if (!file.startsWith(pageRoot + path.sep)) throw Error('outside page');
      const body = await fs.readFile(file);
      res.setHeader('Content-Type', file.endsWith('.html') ? 'text/html; charset=utf-8' : file.endsWith('.css') ? 'text/css; charset=utf-8' : file.endsWith('.js') ? 'application/javascript; charset=utf-8' : 'image/jpeg');
      res.end(body);
    } catch (_) { res.writeHead(404); res.end('Not found'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  // Windows may allocate a low ephemeral port on Chromium's blocked list.
  const browser = await chromium.launch({ headless: true, args: [`--explicitly-allowed-ports=${server.address().port}`] });
  try {
    const page = await browser.newPage({ viewport: { width: 1512, height: 1120 }, deviceScaleFactor: 1 });
    page.setDefaultTimeout(10000);
    const errors = [], mutations = [];
    const roomLayouts = createRoomLayoutFixture();
    let failSave = false;
    page.on('pageerror', error => { errors.push(error.message); console.error(error.stack); });
    await page.route('**/api/v1/**', async route => {
      if (await roomLayouts.handle(route)) return;
      const name = new URL(route.request().url()).pathname;
      let data = {};
      if (name.endsWith('/overview')) data = structuredClone(fixture);
      else if (name.includes('/calendar')) data = { today: { events: [] } };
      else if (name.includes('migration')) data = { dismissed: true };
      else if (name.endsWith('/memo/list')) data = { memo_notes: fixture.bookshelf.memo_notes };
      else if (name.endsWith('/memo/update')) {
        const payload = route.request().postDataJSON(); mutations.push(payload);
        if (failSave) { await route.fulfill({ status: 500, json: { error: '模拟保存失败' } }); return; }
        const notes = fixture.bookshelf.memo_notes;
        if (payload.action === 'save') notes.items.unshift({ id: 'memo-test', content: payload.content, status: 'active' });
        else notes.items.find(note => note.id === payload.id).status = payload.action === 'complete' ? 'completed' : 'active';
        notes.active = notes.items.filter(note => note.status === 'active').length;
        data = { memo_notes: notes };
      }
      await route.fulfill({ json: { success: true, data } });
    });
    await page.addInitScript(() => {
      window.__PRIVATE_COMPANION_STANDALONE__ = { standalone: true, mode: 'standalone', apiBase: '/api/v1' };
      localStorage.setItem('pc_home_room_view_v1', '2d'); // Retired preference must not hide the voxel scene.
      localStorage.setItem('pc_extension_migration_notice_6_2_2_dismissed', '1');
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.locator('.annotations [data-tab="home"]').click();
    await page.waitForFunction(() => document.querySelector('[data-home-room-3d-mount]')?.dataset.voxelCount);
    const voxelCount = Number(await page.locator('[data-home-room-3d-mount]').getAttribute('data-voxel-count'));
    assert.ok(voxelCount > 3500);
    assert.equal(await page.locator('#homeRoomBotName').innerText(), '小满');
    const capture = async name => {
      if (!process.env.PC_ROOM_ARTIFACTS) return;
      await fs.mkdir(process.env.PC_ROOM_ARTIFACTS, { recursive: true });
      await page.locator('#homeRoomRoot').screenshot({ path: path.join(process.env.PC_ROOM_ARTIFACTS, name + '.png'), timeout: 15000 });
    };
    await capture('voxel-room-desktop');
    assert.equal(await page.locator('.home-room-hover-title').isVisible(), false);
    assert.equal(await page.locator('[data-room-object]').count(), 0);
    // Wait for the panel entrance transition to make the canvas hit-testable.
    // Synthetic raycast probes can run while CSS still disables pointer events.
    await page.waitForFunction(() => {
      const canvas = document.querySelector('[data-home-room-3d-mount] canvas'), r = canvas.getBoundingClientRect();
      return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === canvas;
    });
    // Pick a visible furniture surface through the actual scene raycaster.
    const hoverPoint = await page.locator('[data-home-room-3d-mount] canvas').evaluate(canvas => {
      const rect = canvas.getBoundingClientRect();
      for (let y = .22; y < .82; y += .06) for (let x = .2; x < .8; x += .06) {
        const point = { x: rect.left + rect.width * x, y: rect.top + rect.height * y };
        canvas.dispatchEvent(new PointerEvent('pointermove', { clientX: point.x, clientY: point.y, pointerType: 'mouse' }));
        const tooltip = document.querySelector('.home-room-hover-title');
        if (!tooltip.hidden && !/房门/.test(tooltip.textContent)) return { ...point, title: tooltip.textContent };
      }
      return null;
    });
    assert.ok(hoverPoint, 'a visible furniture surface must be hoverable');
    await page.mouse.move(hoverPoint.x, hoverPoint.y);
    assert.equal(await page.locator('.home-room-hover-title').innerText(), hoverPoint.title);
    await capture('voxel-room-hover');
    await page.mouse.move(0, 0);
    await page.waitForFunction(() => document.querySelector('.home-room-hover-title').hidden);
    assert.equal(await page.locator('.home-room-hover-title').isVisible(), false);
    await page.mouse.click(hoverPoint.x, hoverPoint.y);
    assert.equal(await page.locator('#homeRoomInspectorTitle').innerText(), hoverPoint.title);
    await page.locator('[data-room-close]').click();
    console.log('hover-only labels and direct furniture picking passed');
    const select = id => page.locator(`#homeRoomStations [data-home-room-station="${id}"]`).click();
    console.log('scene rendered:', voxelCount, 'blocks');
    await select('calendar');
    const rows = await page.locator('.room-agenda li').allTextContents();
    assert.match(rows[0], /已确认完成/);
    assert.match(rows[1], /当前时间段/);
    assert.doesNotMatch(rows[1], /执行中|已确认完成/);
    await select('desk');
    await page.locator('[data-room-page="1"]').click();
    assert.match(await page.locator('#homeRoomInspectorBody').innerText(), /2026-09-12/);
    await page.locator('[data-room-page="-1"]').click();
    await page.locator('#homeRoomMemoDraft').fill('周末去植物园');
    await page.locator('#homeRoomMemoForm [type="submit"]').click();
    await page.getByRole('button', { name: '完成便签：周末去植物园', exact: true }).waitFor();
    await page.getByRole('button', { name: '完成便签：周末去植物园', exact: true }).click();
    await page.getByRole('button', { name: '恢复便签：周末去植物园', exact: true }).waitFor();
    assert.equal(mutations[0].content, '周末去植物园');
    assert.equal(mutations[1].action, 'complete');
    failSave = true;
    await page.locator('#homeRoomMemoDraft').fill('失败后仍保留的草稿');
    await page.locator('#homeRoomMemoForm [type="submit"]').click();
    await page.locator('.room-error').waitFor();
    assert.equal(await page.locator('#homeRoomMemoDraft').inputValue(), '失败后仍保留的草稿');
    failSave = false;
    await page.locator('#homeRoomMemoDraft').fill('');
    await page.locator('.room-reading summary').click();
    await page.locator('#homeRoomMemoDraft').fill('刷新时保留焦点');
    await page.evaluate(() => renderHomeRoom());
    assert.equal(await page.evaluate(() => document.activeElement.id), 'homeRoomMemoDraft');
    assert.equal(await page.locator('#homeRoomMemoDraft').inputValue(), '刷新时保留焦点');
    assert.equal(await page.locator('.room-reading').getAttribute('open'), '');
    await page.locator('#homeRoomMemoDraft').fill('');
    await select('desk');
    await capture('voxel-room-desk');
    console.log('diary and memo round trips passed');
    await select('shelf');
    assert.match(await page.locator('#homeRoomInspectorBody').innerText(), /雨停之前/);
    assert.doesNotMatch(await page.locator('#homeRoomInspectorBody').innerText(), /不应显示/);
    await select('radio');
    assert.equal(await page.locator('.room-source').getAttribute('href'), 'https://example.com/garden');
    await select('garden');
    assert.equal(await page.locator('.room-goal progress').first().getAttribute('value'), '50');
    await select('wardrobe');
    const outfitData = 'data:image/png;base64,' + (await page.locator('[data-home-room-3d-mount] canvas').screenshot()).toString('base64');
    await page.route('**/api/v1/daily_outfit/image_data*', route => route.fulfill({ json: { success: true, data: { data_url: outfitData } } }));
    await page.evaluate(() => { state.overview.daily_outfit = { enabled: true, available: true, date: '2026-09-13', image_data_url: '/daily_outfit/image_data?date=2026-09-13' }; renderHomeRoom(); });
    await page.locator('.room-outfit-preview img').waitFor();
    assert.equal(await page.locator('.room-outfit-preview img').getAttribute('src'), outfitData);
    await page.evaluate(() => { state.overview.daily_outfit = { enabled: true, available: false }; renderHomeRoom(); });
    assert.equal(await page.locator('.room-outfit-preview img').count(), 0);
    await page.waitForTimeout(1900);
    await capture('voxel-room-wardrobe');
    await select('bed');
    assert.match(await page.locator('#homeRoomInspectorBody').innerText(), /旧书店里的雨/);
    await page.locator('[data-home-room-theme]').click();
    await page.locator('[data-room-close]').click();
    await page.waitForTimeout(1900);
    await capture('voxel-room-night');
    await page.locator('[data-home-room-theme]').click();
    // Camera reset is in the canvas toolbar. Furniture editing is independent
    // of companion data, with a transactional draft and per-persona storage.
    const sceneState = () => page.evaluate(() => window.PrivateCompanionHomeRoom3D.mount(document.querySelector('[data-home-room-3d-mount]')).getState());
    const layout = () => page.evaluate(() => window.PrivateCompanionHomeRoom3D.mount(document.querySelector('[data-home-room-3d-mount]')).getLayout());
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.locator('[data-home-room-camera-reset]').click();
    const originalLayout = await layout();
    await page.evaluate(() => {
      const api = window.PrivateCompanionHomeRoom3D.mount(document.querySelector('[data-home-room-3d-mount]'));
      api.setLayout({ version: 1, items: { bed: { x: 'bad', z: 1e100, rotation: null, style: { toString: null } } } });
    });
    const normalized = await layout();
    assert.equal(normalized.items.bed.style, 'oak'); assert.ok(normalized.items.bed.z < 4);
    await page.evaluate(saved => window.PrivateCompanionHomeRoom3D.mount(document.querySelector('[data-home-room-3d-mount]')).setLayout(saved), originalLayout);
    await page.locator('[data-room-edit]').click();
    assert.equal(await page.locator('#homeRoomEditor').isVisible(), true);
    await page.locator('#homeRoomFurniture').selectOption('stool');
    await page.locator('[data-room-style="ink"]').click();
    await page.locator('[data-room-nudge="0.25,0"]').click();
    await page.locator('[data-room-rotate="45"]').click();
    let edited = await layout();
    assert.equal(edited.items.stool.style, 'ink');
    assert.equal(edited.items.stool.x, originalLayout.items.stool.x + .25);
    assert.equal(edited.items.stool.rotation, 45);
    await page.locator('[data-room-undo]').click();
    assert.equal((await layout()).items.stool.rotation, 0);
    await page.locator('[data-room-redo]').click();
    assert.equal((await layout()).items.stool.rotation, 45);
    await page.locator('#homeRoomFurnitureX').fill('0');
    await page.locator('#homeRoomFurnitureX').press('Tab');
    assert.equal((await layout()).items.stool.x, 0);
    await page.locator('[data-room-style="cream"]').click();
    assert.equal((await layout()).items.stool.style, 'cream');
    await capture('voxel-room-editor');
    // Actual pointer capture, floor-plane dragging, and one-step undo.
    const dragPoint = await page.locator('[data-home-room-3d-mount] canvas').evaluate(canvas => {
      const rect = canvas.getBoundingClientRect();
      for (let y = .2; y < .9; y += .025) for (let x = .15; x < .9; x += .025) {
        const point = { x: rect.left + rect.width * x, y: rect.top + rect.height * y };
        canvas.dispatchEvent(new PointerEvent('pointermove', { clientX: point.x, clientY: point.y, pointerType: 'mouse' }));
        const tooltip = document.querySelector('.home-room-hover-title');
        if (!tooltip.hidden && tooltip.textContent === '茶几') return point;
      } return null;
    });
    assert.ok(dragPoint, 'an editable furniture surface must be draggable');
    const beforeDrag = await layout();
    await page.mouse.move(dragPoint.x, dragPoint.y); await page.mouse.down();
    await page.mouse.move(dragPoint.x + 55, dragPoint.y + 30, { steps: 8 }); await page.mouse.up();
    assert.notDeepEqual((await layout()).items.stool, beforeDrag.items.stool);
    await page.locator('[data-room-undo]').click(); assert.deepEqual(await layout(), beforeDrag);
    await page.locator('[data-room-redo]').click();
    const beforeDefault = await layout();
    await page.locator('[data-room-defaults]').click(); assert.deepEqual(await layout(), originalLayout);
    await page.locator('[data-room-undo]').click(); assert.deepEqual(await layout(), beforeDefault);
    const saved = await layout();
    await page.locator('[data-room-save]').click();
    await page.locator('#homeRoomEditor').waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#homeRoomEditor').isVisible(), false);
    await page.reload(); await page.locator('.annotations [data-tab="home"]').click();
    await page.waitForFunction(() => document.querySelector('[data-home-room-3d-mount]')?.dataset.voxelCount);
    assert.deepEqual(await layout(), saved, 'saved placement and style must survive a full reload');
    await page.locator('[data-room-edit]').click();
    await page.locator('#homeRoomFurniture').selectOption('stool');
    await page.locator('[data-room-style="oak"]').click();
    await page.locator('[data-room-cancel]').click(); assert.deepEqual(await layout(), saved);
    await page.locator('[data-room-edit]').click();
    await page.locator('#homeRoomFurniture').selectOption('image');
    await page.locator('#homeRoomEditorTitle').focus();
    await page.keyboard.press('ArrowRight'); await page.keyboard.press('r');
    assert.equal((await layout()).items.image.rotation, 45);
    await page.keyboard.press('Control+z'); assert.equal((await layout()).items.image.rotation, 0);
    roomLayouts.failSave = true;
    const unsaved = await layout(); await page.locator('[data-room-save]').click();
    await page.waitForFunction(() => document.getElementById('homeRoomLayoutStatus').textContent === '保存失败');
    assert.equal(await page.locator('#homeRoomLayoutStatus').innerText(), '保存失败');
    assert.deepEqual(await layout(), unsaved); assert.equal(await page.locator('#homeRoomEditor').isVisible(), true);
    roomLayouts.failSave = false;
    await page.locator('[data-room-cancel]').click(); assert.deepEqual(await layout(), saved);
    // Invalid persisted input is normalized, and the previous persona's layout
    // is never carried into another persona or overwritten by render refresh.
    await page.evaluate(() => {
      window.__roomOriginalPersona = homeRoomContext().personaId;
      window.PrivateCompanionHomeRoom.resetPersona();
      window.PrivateCompanionHomeRoom.render({ ...homeRoomContext(), personaId: 'room-other-persona' });
    });
    assert.deepEqual(await layout(), originalLayout);
    await page.locator('[data-room-edit]').click();
    await page.locator('#homeRoomFurniture').selectOption('bed');
    await page.locator('[data-room-style="ink"]').click();
    await page.locator('[data-room-save]').click();
    await page.locator('#homeRoomEditor').waitFor({ state: 'hidden' });
    await page.evaluate(() => { window.PrivateCompanionHomeRoom.resetPersona(); renderHomeRoom(); });
    await page.waitForFunction(() => !document.querySelector('[data-room-edit]').disabled);
    assert.deepEqual(await layout(), saved);
    await page.evaluate(() => { renderHomeRoom(); }); assert.deepEqual(await layout(), saved);
    await page.evaluate(() => {
      window.__doorEvents = [];
      document.getElementById('homeRoomRoot').addEventListener('companion:room-door', event => window.__doorEvents.push(event.detail));
    });
    await page.locator('[data-room-door]').click();
    assert.equal((await sceneState()).doorOpen, true);
    assert.ok(Math.abs((await sceneState()).doorAngle) > 1.5);
    await capture('voxel-room-door');
    await page.locator('[data-room-door]').click();
    assert.equal((await sceneState()).doorAngle, 0);
    const doorEvents = await page.evaluate(() => window.__doorEvents);
    assert.deepEqual(doorEvents.map(event => event.open), [true, false]);
    assert.equal(doorEvents[0].doorId, 'entrance'); assert.equal(doorEvents[0].version, 1);
    console.log('furniture styles, drag, history, cancel, reload, persona layout and door extension passed');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.locator('[data-home-room-camera-tour]').click();
    assert.equal(await page.locator('[data-home-room-camera-tour]').getAttribute('aria-pressed'), 'true');
    const shot = await page.locator('#homeRoomShot').innerText();
    await page.waitForTimeout(5500);
    await capture('voxel-room-cinematic');
    await page.waitForFunction(label => document.getElementById('homeRoomShot').textContent !== label, shot, { timeout: 30000 });
    const canvas = page.locator('[data-home-room-3d-mount] canvas');
    await canvas.dispatchEvent('pointerdown', { clientX: 300, clientY: 300, pointerId: 1, button: 0, pointerType: 'mouse' });
    await canvas.dispatchEvent('pointerup', { clientX: 330, clientY: 300, pointerId: 1, button: 0, pointerType: 'mouse' });
    assert.equal(await page.locator('[data-home-room-camera-tour]').getAttribute('aria-pressed'), 'false');
    await page.locator('[data-room-next-shot]').click();
    assert.equal((await sceneState()).tourRunning, true);
    await page.locator('[data-home-room-camera-reset]').click();
    await page.waitForFunction(() => !window.PrivateCompanionHomeRoom3D.mount(document.querySelector('[data-home-room-3d-mount]')).getState().moving);
    const reset = await sceneState();
    assert.equal(reset.tourRunning, false); assert.equal(reset.camera.fov, 38); assert.deepEqual(reset.camera.up, [0, 1, 0]);
    assert.ok(Math.abs(reset.camera.position[0] - 13.5) < .01);
    await page.locator('[data-home-room-camera-reset]').click();
    await page.waitForTimeout(1700);
    assert.equal((await sceneState()).moving, false, 'reset at overview must not create a degenerate curve');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.locator('[data-home-room-camera-tour]').click();
    assert.equal(await page.locator('[data-home-room-camera-tour]').getAttribute('aria-pressed'), 'false');
    console.log('camera choreography and reduced motion passed');
    await select('calendar');
    await page.locator('#homeRoomInspector [data-home-room-destination="calendar"]').click();
    await page.waitForFunction(() => document.getElementById('panel-memory').classList.contains('is-active'));
    await page.locator('.annotations [data-tab="home"]').click();
    await page.waitForFunction(() => document.getElementById('panel-home').classList.contains('is-active'));
    await select('desk');
    await page.locator('#homeRoomMemoDraft').fill('上个人格的草稿');
    // Resolve an old request after a persona reset. Neither its text nor the
    // old draft may enter the new persona's cache or inspector.
    await page.evaluate(() => {
      window.__oldMemoApplyCount = 0;
      const ctx = homeRoomContext();
      ctx.postJson = () => new Promise(resolve => { window.__resolveOldMemo = resolve; });
      ctx.applyMemoPayload = () => { window.__oldMemoApplyCount++; };
      window.PrivateCompanionHomeRoom.render(ctx);
    });
    await page.locator('#homeRoomMemoForm [type="submit"]').click();
    await page.evaluate(() => {
      window.PrivateCompanionHomeRoom.resetPersona();
      state.overview = { plugin: { bot_name: '另一个人格' }, companion_plugins: { content: { installed: false } } };
      state.memoNotes = { items: [], active: 0 }; state.calendar = null;
      renderHomeRoom();
      window.__resolveOldMemo({ memo_notes: { items: [{ content: '不应串入的旧请求' }] } });
    });
    await select('desk');
    assert.equal(await page.locator('#homeRoomMemoDraft').inputValue(), '');
    assert.doesNotMatch(await page.locator('#homeRoomRoot').innerText(), /上个人格的草稿|不应串入|旧书店里的雨/);
    assert.equal(await page.evaluate(() => window.__oldMemoApplyCount), 0);
    await select('shelf');
    assert.match(await page.locator('#homeRoomInspectorBody').innerText(), /创作扩展未就绪/);
    assert.equal(await page.locator('#homeRoomInspector [data-home-room-destination="creative"]').count(), 0);
    await page.evaluate(data => { state.overview = data; state.memoNotes = data.bookshelf.memo_notes; renderHomeRoom(); }, fixture);
    await page.locator('[data-room-close]').click();
    await page.setViewportSize({ width: 430, height: 932 });
    await capture('voxel-room-mobile');
    await page.locator('[data-room-edit]').click();
    await page.locator('#homeRoomFurniture').selectOption('lounge');
    await page.locator('[data-room-style="cream"]').click();
    await capture('voxel-room-mobile-editor');
    assert.equal(await page.locator('#homeRoomEditor').evaluate(el => el.scrollWidth > el.clientWidth + 1), false);
    await page.locator('[data-room-cancel]').click();
    const overflow = await page.locator('#homeRoomRoot').evaluate(root => root.scrollWidth > root.clientWidth + 1);
    assert.equal(overflow, false);
    await select('desk');
    await capture('voxel-room-mobile-desk');
    assert.equal(await page.locator('#homeRoomInspector').isVisible(), true);
    await page.locator('#homeRoomInspectorTitle').focus();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#homeRoomInspector').isVisible(), false);
    assert.equal(await page.evaluate(() => document.activeElement.dataset.homeRoomStation), 'desk');
    assert.equal(await page.locator('button[data-home-room-view], [data-home-room-scene-2d]').count(), 0);
    await select('desk');
    assert.equal(await page.locator('#homeRoomInspectorTitle').innerText(), '手账与便签');
    assert.deepEqual(errors, []);
    console.log('persona isolation, destinations, mobile, keyboard and retired view preference passed');
  } finally {
    await browser.close(); await new Promise(resolve => server.close(resolve));
  }
});
