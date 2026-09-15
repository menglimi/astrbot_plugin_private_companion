/* Furniture-catalog integration checks against the real panel. All companion
 * requests are fixture responses; local browser storage is disposable. */
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

test('room house and furniture catalog: variants, caching, history, persistence, picking and responsive controls', { timeout: 180000 }, async () => {
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
  const browser = await chromium.launch({ headless: true, args: [`--explicitly-allowed-ports=${server.address().port}`] });
  try {
    const page = await browser.newPage({ viewport: { width: 1512, height: 1120 }, reducedMotion: 'reduce' });
    page.setDefaultTimeout(12000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.stack));
    const roomLayouts = createRoomLayoutFixture();
    await page.route('**/api/v1/**', async route => {
      if (await roomLayouts.handle(route)) return;
      return route.fulfill({ json: { success: true, data: {
      plugin: { bot_name: '小满' }, daily_state: { date: '2026-09-14', location: '家中' },
    } } });
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
      await page.evaluate(() => { window.roomStyleApi = PrivateCompanionHomeRoom3D.mount(document.querySelector('[data-home-room-3d-mount]')); });
    }
    await enterRoom();
    const original = await page.evaluate(() => roomStyleApi.getLayout());
    const catalog = await page.evaluate(() => roomStyleApi.getCatalog());
    const newStyles = ['rattan', 'walnut', 'loft', 'blossom', 'nordic'];
    assert.equal(Object.keys(catalog.furniture).length, 15); assert.equal(Object.keys(catalog.styles).length, 8);
    const baselineCount = Number(await page.locator('[data-home-room-3d-mount]').getAttribute('data-voxel-count'));
    assert.ok(baselineCount < 6000, 'unselected models must not add to startup geometry');
    const capture = async name => {
      if (!process.env.PC_ROOM_ARTIFACTS) return;
      await fs.mkdir(process.env.PC_ROOM_ARTIFACTS, { recursive: true });
      await page.locator('#homeRoomRoot').screenshot({ path: path.join(process.env.PC_ROOM_ARTIFACTS, name + '.png') });
    };
    // Build every new furniture/style combination; select each again to prove
    // caching does not duplicate geometry or mutate positions on reload.
    const positions = await page.evaluate(({ newStyles, original }) => {
      const snapshots = {};
      for (const style of newStyles) {
        const layout = structuredClone(original);
        for (const item of Object.values(layout.items)) item.style = style;
        roomStyleApi.setLayout(layout); snapshots[style] = roomStyleApi.getLayout();
      }
      return snapshots;
    }, { newStyles, original });
    for (const [style, saved] of Object.entries(positions)) {
      for (const [id, item] of Object.entries(saved.items)) {
        assert.equal(item.style, style, `${id} must support ${style}`);
        assert.ok(Number.isFinite(item.x) && Number.isFinite(item.z));
        assert.ok(Math.abs(item.x - original.items[id].x) < .8 && Math.abs(item.z - original.items[id].z) < .8, 'larger styles should use a nearby free position');
      }
    }
    const builtCount = Number(await page.locator('[data-home-room-3d-mount]').getAttribute('data-voxel-count'));
    for (const style of newStyles) {
      await page.evaluate(saved => roomStyleApi.setLayout(saved), positions[style]);
      assert.deepEqual(await page.evaluate(() => roomStyleApi.getLayout()), positions[style]);
      assert.deepEqual(await page.evaluate(() => roomStyleApi.getState().placementIssues), {}, 'style bounds must prevent overlap');
      await capture('room-style-' + style);
    }
    assert.equal(Number(await page.locator('[data-home-room-3d-mount]').getAttribute('data-voxel-count')), builtCount);
    await page.evaluate(saved => roomStyleApi.setLayout(saved), original);
    assert.deepEqual(await page.evaluate(() => roomStyleApi.getLayout()), original, 'old layouts retain their original transforms');
    const houseStyles = Object.keys(catalog.houseStyles);
    assert.equal(houseStyles.length, 6);
    await page.evaluate(original => {
      const legacy = structuredClone(original); delete legacy.houseStyle;
      roomStyleApi.setLayout(legacy);
    }, original);
    assert.deepEqual(await page.evaluate(() => roomStyleApi.getLayout()), original, 'legacy rooms without a shell choice keep the original house');
    const houseFrames = [];
    for (const houseStyle of houseStyles) {
      await page.evaluate(layout => roomStyleApi.setLayout(layout), { ...original, houseStyle });
      const current = await page.evaluate(() => roomStyleApi.getLayout());
      assert.equal(current.houseStyle, houseStyle);
      assert.deepEqual(current.items, original.items, 'house changes must preserve furniture positions and styles');
      houseFrames.push(await page.locator('[data-home-room-3d-mount]').screenshot());
      await capture('room-house-' + houseStyle);
      await page.locator('#homeRoomStations [data-home-room-station="desk"]').click();
      assert.equal(await page.locator('#homeRoomInspectorTitle').innerText(), '手账与便签');
      await page.locator('[data-home-room-camera-reset]').click();
    }
    assert.equal(new Set(houseFrames.map(frame => require('node:crypto').createHash('sha256').update(frame).digest('hex'))).size, 6, 'each house must render a different appearance');
    const houseBuiltCount = Number(await page.locator('[data-home-room-3d-mount]').getAttribute('data-voxel-count'));
    await page.evaluate(({ original, houseStyles }) => {
      for (const houseStyle of houseStyles) roomStyleApi.setLayout({ ...original, houseStyle });
      for (const invalid of ['missing-house', { toString: null }, null]) roomStyleApi.setLayout({ ...original, houseStyle: invalid });
    }, { original, houseStyles });
    assert.equal(Number(await page.locator('[data-home-room-3d-mount]').getAttribute('data-voxel-count')), houseBuiltCount, 'cached house switches do not grow scene geometry');
    assert.deepEqual(await page.evaluate(() => roomStyleApi.getLayout()), original, 'invalid house values fall back safely');
    await page.locator('[data-room-edit]').click();
    await page.locator('#homeRoomHousePicker summary').click();
    assert.equal(await page.locator('[data-room-house-style]').count(), 6);
    await page.locator('[data-room-house-style="loft"]').click();
    assert.equal(await page.locator('#homeRoomHouseLabel').innerText(), '红砖阁楼');
    await page.locator('[data-room-undo]').click();
    assert.equal((await page.evaluate(() => roomStyleApi.getLayout())).houseStyle, 'classic');
    await page.locator('[data-room-redo]').click();
    assert.equal((await page.evaluate(() => roomStyleApi.getLayout())).houseStyle, 'loft');
    await page.locator('[data-room-house-style="french"]').focus(); await page.keyboard.press('Enter');
    await capture('room-house-editor-desktop');
    await page.locator('[data-room-defaults]').click();
    assert.deepEqual(await page.evaluate(() => roomStyleApi.getLayout()), original);
    await page.locator('[data-room-undo]').click();
    assert.equal((await page.evaluate(() => roomStyleApi.getLayout())).houseStyle, 'french');
    await page.locator('#homeRoomHousePicker summary').click();
    assert.equal(await page.locator('[data-room-style]').count(), 8);
    await page.locator('#homeRoomFurniture').selectOption('lounge');
    assert.match(await page.locator('[data-room-style="blossom"]').innerText(), /花瓣靠背椅/);
    await page.locator('[data-room-style="blossom"]').click();
    assert.equal(await page.locator('[data-room-style="blossom"]').getAttribute('aria-pressed'), 'true');
    assert.match(await page.locator('#homeRoomStyleNote').innerText(), /樱粉/);
    await page.locator('[data-room-undo]').click();
    assert.equal((await page.evaluate(() => roomStyleApi.getLayout())).items.lounge.style, 'oak');
    await page.locator('[data-room-redo]').click();
    assert.equal((await page.evaluate(() => roomStyleApi.getLayout())).items.lounge.style, 'blossom');
    await page.locator('#homeRoomFurniture').selectOption('wardrobe');
    assert.match(await page.locator('[data-room-style="rattan"]').innerText(), /藤芯双门柜/);
    await page.locator('[data-room-style="rattan"]').click();
    await page.locator('#homeRoomFurniture').selectOption('readingLamp');
    await page.locator('[data-room-style="walnut"]').focus(); await page.keyboard.press('Enter');
    assert.equal((await page.evaluate(() => roomStyleApi.getLayout())).items.readingLamp.style, 'walnut');
    await capture('room-style-catalog-desktop');
    const mixed = await page.evaluate(() => roomStyleApi.getLayout());
    assert.equal(mixed.houseStyle, 'french');
    await page.locator('[data-room-save]').click(); await page.locator('#homeRoomEditor').waitFor({ state: 'hidden' }); await page.reload(); await enterRoom();
    assert.deepEqual(await page.evaluate(() => roomStyleApi.getLayout()), mixed);
    await page.locator('#homeRoomStations [data-home-room-station="wardrobe"]').click();
    await page.waitForTimeout(150);
    assert.equal(await page.locator('#homeRoomInspectorTitle').innerText(), '今日衣柜');
    await capture('room-style-rattan-wardrobe-open');
    await page.locator('[data-home-room-camera-reset]').click();
    await page.evaluate(() => {
      window.roomHouseDoorEvents = [];
      document.getElementById('homeRoomRoot').addEventListener('companion:room-door', event => roomHouseDoorEvents.push(event.detail));
    });
    await page.locator('[data-room-door]').click();
    const openAngle = await page.evaluate(() => roomStyleApi.getState().doorAngle);
    assert.ok(Math.abs(openAngle) > 1.5);
    await page.locator('[data-room-edit]').click();
    await page.locator('#homeRoomHousePicker summary').click();
    await page.locator('[data-room-house-style="cabin"]').click();
    assert.equal(await page.evaluate(() => roomStyleApi.getState().doorAngle), openAngle, 'switching shells preserves the shared hinged door');
    await capture('room-house-cabin-open');
    await page.locator('[data-room-cancel]').click();
    assert.deepEqual(await page.evaluate(() => roomStyleApi.getLayout()), mixed);
    await page.locator('[data-room-door]').click();
    assert.deepEqual(await page.evaluate(() => roomHouseDoorEvents.map(event => event.open)), [true, false], 'shell edits must not emit navigation/door actions');
    // House styles follow the same persona isolation as furniture layouts.
    await page.evaluate(() => { PrivateCompanionHomeRoom.resetPersona(); PrivateCompanionHomeRoom.render({ ...homeRoomContext(), personaId: 'house-other-persona' }); });
    assert.deepEqual(await page.evaluate(() => roomStyleApi.getLayout()), original);
    await page.evaluate(() => { PrivateCompanionHomeRoom.resetPersona(); renderHomeRoom(); });
    await page.waitForFunction(() => !document.querySelector('[data-room-edit]').disabled);
    assert.deepEqual(await page.evaluate(() => roomStyleApi.getLayout()), mixed);
    await page.locator('[data-room-edit]').click();
    await page.locator('#homeRoomFurniture').selectOption('lounge');
    // Replaced bodies must still be pickable through the scene raycaster.
    const picked = await page.locator('[data-home-room-3d-mount] canvas').evaluate(canvas => {
      const rect = canvas.getBoundingClientRect();
      for (let y = .2; y < .9; y += .025) for (let x = .15; x < .85; x += .025) {
        canvas.dispatchEvent(new PointerEvent('pointermove', { clientX: rect.left + rect.width * x, clientY: rect.top + rect.height * y, pointerType: 'mouse' }));
        const tooltip = document.querySelector('.home-room-hover-title');
        if (!tooltip.hidden && tooltip.textContent === '阅读扶手椅') return true;
      } return false;
    });
    assert.equal(picked, true);
    await page.setViewportSize({ width: 430, height: 932 });
    await page.locator('#homeRoomHousePicker').evaluate(el => { el.open = true; });
    await page.locator('[data-room-house-style="coastal"]').click();
    await capture('room-house-editor-mobile');
    assert.equal(await page.locator('#homeRoomHouseOptions').evaluate(el => el.scrollWidth > el.clientWidth + 1), false);
    await page.locator('#homeRoomHousePicker summary').click();
    await page.locator('#homeRoomFurniture').selectOption('bed');
    await page.locator('[data-room-style="nordic"]').click();
    await capture('room-style-catalog-mobile');
    assert.equal(await page.locator('#homeRoomRoot').evaluate(el => el.scrollWidth > el.clientWidth + 1), false);
    assert.equal(await page.locator('#homeRoomStyleOptions').evaluate(el => el.scrollWidth > el.clientWidth + 1), false);
    assert.equal(await page.locator('[data-room-style]').evaluateAll(buttons => buttons.some(button => button.scrollWidth > button.clientWidth + 1)), false);
    await page.locator('[data-room-cancel]').click();
    assert.deepEqual(await page.evaluate(() => roomStyleApi.getLayout()), mixed);
    assert.deepEqual(errors, []);
    console.log('50 furniture models, 6 house styles, cached geometry, legacy compatibility, independent mixing, persona save/reload, undo/redo, shared doors and responsive controls passed');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
});
