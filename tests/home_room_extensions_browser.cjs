/* Exercise the actual room, using local HTTP fixtures and disposable storage. */
const { createRequire } = require('node:module');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const { createRoomLayoutFixture } = require('./room_layout_fixture.cjs');
const { chromium } = process.env.PC_PLAYWRIGHT_DIR
  ? createRequire(path.join(process.env.PC_PLAYWRIGHT_DIR, 'package.json'))('playwright') : require('playwright');
const pageRoot = path.resolve(__dirname, '../pages/companion-panel');

test('extension furniture: solid placement, wall planes, variants, routes and saved rooms', { timeout: 180000 }, async () => {
  const server = http.createServer(async (req, res) => {
    try {
      const rel = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).slice(1) || 'index.html';
      const file = path.resolve(pageRoot, rel);
      if (!file.startsWith(pageRoot + path.sep)) throw Error('outside page');
      res.setHeader('Content-Type', file.endsWith('.html') ? 'text/html; charset=utf-8' : file.endsWith('.css') ? 'text/css; charset=utf-8' : file.endsWith('.js') ? 'application/javascript; charset=utf-8' : 'image/jpeg');
      res.end(await fs.readFile(file));
    } catch (_) { res.writeHead(404); res.end('Not found'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true, args: [`--explicitly-allowed-ports=${server.address().port}`] });
  try {
    const page = await browser.newPage({ viewport: { width: 1512, height: 1120 }, reducedMotion: 'reduce' });
    const errors = [], writes = [];
    page.on('pageerror', error => errors.push(error.stack));
    const roomLayouts = createRoomLayoutFixture();
    await page.route('**/api/v1/**', async route => {
      if (await roomLayouts.handle(route)) return;
      // The app's existing migration-notice acknowledgement runs at startup.
      if (route.request().method() !== 'GET' && !route.request().url().endsWith('/extension-migration-notice/update')) writes.push(route.request().url());
      const data = route.request().url().includes('migration') ? { dismissed: true } : { plugin: { bot_name: '小满' }, daily_state: { date: '2026-09-15', location: '家中' }, companion_plugins: { image: { installed: true, enabled: true, available: true } } };
      return route.fulfill({ json: { success: true, data } });
    });
    await page.addInitScript(() => {
      window.__PRIVATE_COMPANION_STANDALONE__ = { standalone: true, mode: 'standalone', apiBase: '/api/v1' };
      localStorage.setItem('pc_home_room_view_v1', '3d');
      localStorage.setItem('pc_extension_migration_notice_6_2_2_dismissed', '1');
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    async function enterRoom() {
      await page.locator('.annotations [data-tab="home"]').click();
      await page.waitForFunction(() => document.querySelector('[data-home-room-3d-mount]')?.dataset.voxelCount);
      await page.waitForFunction(() => !document.querySelector('[data-room-edit]').disabled);
      await page.evaluate(() => { window.roomApi = PrivateCompanionHomeRoom3D.mount(document.querySelector('[data-home-room-3d-mount]')); });
    }
    async function capture(name) {
      if (!process.env.PC_ROOM_ARTIFACTS) return;
      await fs.mkdir(process.env.PC_ROOM_ARTIFACTS, { recursive: true });
      await page.locator('#homeRoomRoot').screenshot({ path: path.join(process.env.PC_ROOM_ARTIFACTS, name + '.png') });
    }
    await enterRoom();
    const original = await page.evaluate(() => roomApi.getLayout());
    assert.deepEqual(await page.evaluate(() => roomApi.getState().placementIssues), {});
    await capture('room-extension-overview');
    await page.locator('[data-room-edit]').click();
    await page.locator('#homeRoomFurniture').selectOption('game');
    assert.equal(await page.locator('#homeRoomObjectModel option').count(), 3);
    // A floor object cannot be placed through the desk or the door swing.
    const prior = await page.evaluate(() => roomApi.getLayout());
    await page.evaluate(() => { const desk = roomApi.getLayout().items.desk; roomApi.updateFurniture({ x: desk.x, z: desk.z }); });
    assert.deepEqual(await page.evaluate(() => roomApi.getLayout()), prior);
    assert.match(await page.locator('#homeRoomPlacementHint').innerText(), /重叠/);
    await page.evaluate(() => roomApi.updateFurniture({ x: 4.8, z: 1.2 }));
    assert.deepEqual(await page.evaluate(() => roomApi.getLayout()), prior);
    assert.match(await page.locator('#homeRoomPlacementHint').innerText(), /房门/);
    for (const [id, models] of Object.entries({ game: ['arcade', 'console', 'chess'], image: ['instant', 'gallery', 'camera'], together: ['wallPhone', 'telephone'] })) {
      await page.locator('#homeRoomFurniture').selectOption(id);
      for (const model of models) {
        await page.locator('#homeRoomObjectModel').selectOption(model);
        assert.equal((await page.evaluate(id => roomApi.getLayout().items[id], id)).model, model);
        assert.deepEqual(await page.evaluate(() => roomApi.getState().placementIssues), {});
      }
    }
    await page.locator('#homeRoomFurniture').selectOption('together');
    await page.locator('#homeRoomObjectModel').selectOption('wallPhone');
    const mounted = await page.evaluate(() => roomApi.getLayout());
    assert.equal(mounted.items.together.surface, 'back-wall');
    assert.ok(await page.locator('#homeRoomFurnitureY').isVisible());
    assert.equal(await page.locator('#homeRoomFurnitureRotation').isVisible(), false);
    await page.evaluate(() => roomApi.updateFurniture({ x: -3.6, y: 2.8 }));
    assert.deepEqual(await page.evaluate(() => roomApi.getLayout()), mounted, 'window cannot receive a wall phone');
    assert.match(await page.locator('#homeRoomPlacementHint').innerText(), /窗户/);
    // A wall phone can sit above low furniture without colliding in height.
    await page.evaluate(() => roomApi.updateFurniture({ x: 2.2, y: 3.5 }));
    assert.equal((await page.evaluate(() => roomApi.getLayout())).items.together.y, 3.5);
    // Raycast to a visible wall phone and actually drag it on its vertical plane.
    const findPoint = async label => page.locator('[data-home-room-3d-mount] canvas').evaluate((canvas, label) => {
      const r = canvas.getBoundingClientRect();
      for (let y = .12; y < .9; y += .015) for (let x = .14; x < .9; x += .015) {
        const p = { x: r.left + r.width * x, y: r.top + r.height * y };
        canvas.dispatchEvent(new PointerEvent('pointermove', { clientX: p.x, clientY: p.y, pointerType: 'mouse' }));
        const tip = document.querySelector('.home-room-hover-title');
        if (!tip.hidden && tip.textContent === label) return p;
      } return null;
    }, label);
    await page.locator('[data-home-room-3d-mount] canvas').scrollIntoViewIfNeeded();
    const start = await findPoint('在一起电话');
    assert.ok(start, 'wall phone must be pickable');
    const beforeDrag = await page.evaluate(() => roomApi.getLayout());
    await page.mouse.move(start.x, start.y); await page.mouse.down();
    await page.mouse.move(start.x, start.y + 16, { steps: 5 }); await page.mouse.up();
    const afterDrag = await page.evaluate(() => roomApi.getLayout());
    assert.notEqual(afterDrag.items.together.y, beforeDrag.items.together.y, 'wall dragging changes elevation');
    assert.equal(afterDrag.items.together.z, beforeDrag.items.together.z, 'wall normal remains anchored');
    assert.deepEqual(await page.evaluate(() => roomApi.getState().placementIssues), {});
    await page.locator('[data-room-undo]').click();
    assert.deepEqual(await page.evaluate(() => roomApi.getLayout()), beforeDrag, 'one gesture is one undo');
    await page.locator('[data-room-redo]').click();
    assert.deepEqual(await page.evaluate(() => roomApi.getLayout()), afterDrag);
    // Check left-wall coordinates and the half-height wall with a small picture.
    await page.locator('#homeRoomFurniture').selectOption('art');
    await page.locator('#homeRoomSurface').selectOption('left-wall');
    await page.evaluate(() => roomApi.updateFurniture({ z: 5, y: .85 }));
    const leftWall = await page.evaluate(() => roomApi.getLayout());
    assert.equal(leftWall.items.art.surface, 'left-wall');
    assert.equal(leftWall.items.art.z, 5);
    await page.locator('[data-room-nudge="0.25,0"]').click();
    assert.equal((await page.evaluate(() => roomApi.getLayout())).items.art.z, 4.75);
    assert.equal((await page.evaluate(() => roomApi.getLayout())).items.art.x, leftWall.items.art.x);
    await page.locator('[data-room-nudge="0,-0.25"]').focus(); await page.keyboard.press('ArrowUp');
    assert.ok((await page.evaluate(() => roomApi.getLayout())).items.art.y <= 1, 'low wall constrains the top edge');
    assert.deepEqual(await page.evaluate(() => roomApi.getState().placementIssues), {});
    await page.locator('#homeRoomFurniture').selectOption('together');
    await page.locator('[data-room-style="ink"]').click();
    await capture('room-extension-wall-editor');
    const saved = await page.evaluate(() => roomApi.getLayout());
    await page.locator('[data-room-save]').click(); await page.locator('#homeRoomEditor').waitFor({ state: 'hidden' }); await page.reload(); await enterRoom();
    assert.deepEqual(await page.evaluate(() => roomApi.getLayout()), saved, 'all wall/model/finish fields survive save and reload');
    await page.evaluate(() => { PrivateCompanionHomeRoom.resetPersona(); PrivateCompanionHomeRoom.render({ ...homeRoomContext(), personaId: 'extension-other-persona' }); });
    assert.deepEqual(await page.evaluate(() => roomApi.getLayout()), original);
    await page.evaluate(() => { PrivateCompanionHomeRoom.resetPersona(); renderHomeRoom(); });
    await page.waitForFunction(() => !document.querySelector('[data-room-edit]').disabled);
    assert.deepEqual(await page.evaluate(() => roomApi.getLayout()), saved);
    // Importing an old overlapping floor layout is repaired and becomes stable.
    await page.evaluate(original => {
      const legacy = { version: 1, items: {} };
      for (const [id, item] of Object.entries(original.items)) if (!['game', 'image', 'together', 'calendar', 'art'].includes(id)) {
        const { x, z, rotation, style } = item; legacy.items[id] = { x, z, rotation, style };
      }
      legacy.items.stool.x = legacy.items.desk.x; legacy.items.stool.z = legacy.items.desk.z;
      roomApi.setLayout(legacy);
    }, original);
    assert.deepEqual(await page.evaluate(() => roomApi.getState().placementIssues), {});
    const repaired = await page.evaluate(() => roomApi.getLayout());
    await page.evaluate(layout => roomApi.setLayout(layout), repaired);
    assert.deepEqual(await page.evaluate(() => roomApi.getLayout()), repaired);
    await page.evaluate(layout => roomApi.setLayout(layout), saved);
    const select = id => page.locator(`#homeRoomStations [data-home-room-station="${id}"]`).click();
    for (const id of ['game', 'together']) {
      await select(id); assert.match(await page.locator('#homeRoomInspectorBody').innerText(), /尚未接通/);
      assert.ok(await page.locator('[data-room-customize]').isEnabled());
      assert.equal(await page.locator('#homeRoomInspectorBody [data-home-room-destination]').count(), 0);
    }
    await select('image');
    await page.locator('#homeRoomInspectorBody [data-home-room-destination="image"]').click();
    await page.waitForFunction(() => document.getElementById('panel-image').classList.contains('is-active'));
    await enterRoom();
    // Availability changes must update the current detail and dock together.
    for (const [status, label] of [[{ installed: false }, '未安装'], [{ installed: true, enabled: false }, '未启用'], [{ installed: true, enabled: true, available: false }, '暂不可用']]) {
      await page.evaluate(status => { state.overview.companion_plugins.image = status; renderHomeRoom(); }, status);
      await select('image');
      assert.match(await page.locator('.room-extension-state').innerText(), new RegExp(label));
      assert.equal(await page.locator('#homeRoomInspectorBody [data-home-room-destination]').count(), 0);
    }
    await page.evaluate(() => { state.overview.companion_plugins.image = { installed: true, enabled: true, available: true }; renderHomeRoom(); });
    await select('game'); await capture('room-game-details');
    await page.setViewportSize({ width: 430, height: 932 });
    await select('together'); await capture('room-extension-mobile');
    assert.equal(await page.locator('#homeRoomRoot').evaluate(el => el.scrollWidth > el.clientWidth + 1), false);
    await page.locator('[data-room-customize="together"]').click();
    await page.locator('#homeRoomFurnitureY').fill('3.5'); await page.locator('#homeRoomFurnitureY').press('Tab');
    await capture('room-wall-controls-mobile');
    assert.deepEqual(await page.evaluate(() => roomApi.getState().placementIssues), {});
    await page.locator('[data-room-cancel]').click();
    assert.deepEqual(await page.evaluate(() => roomApi.getLayout()), saved);
    await select('image');
    assert.ok(await page.locator('#homeRoomInspectorBody [data-home-room-destination="image"]').isVisible(), 'the mobile furniture list retains the real extension route');
    assert.deepEqual(errors, []);
    assert.deepEqual(writes, [], 'room placement and previews must not invoke extension business actions');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
});
