/* Reproduce AstrBot's opaque-origin iframe. API calls use disposable fixtures. */
const { createRequire } = require('node:module');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const { chromium } = process.env.PC_PLAYWRIGHT_DIR
  ? createRequire(path.join(process.env.PC_PLAYWRIGHT_DIR, 'package.json'))('playwright') : require('playwright');
const pageRoot = path.resolve(__dirname, '../pages/companion-panel');
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const gate = () => ({ started: deferred(), release: deferred() });

test('room persistence: sandbox, reload, failed writes, concurrent drafts, persona races and legacy storage', { timeout: 150000 }, async () => {
  const server = http.createServer(async (req, res) => {
    try {
      if (req.url === '/embedded') {
        res.setHeader('Content-Type', 'text/html; charset=utf-8');
        res.end('<!doctype html><html><body style="margin:0"><iframe title="陪伴面板" src="/index.html" sandbox="allow-scripts allow-forms allow-downloads" style="width:100%;height:100vh;border:0"></iframe></body></html>');
        return;
      }
      const file = path.resolve(pageRoot, decodeURIComponent(new URL(req.url, 'http://localhost').pathname).slice(1));
      if (!file.startsWith(pageRoot + path.sep)) throw Error('outside fixture');
      res.setHeader('Access-Control-Allow-Origin', '*');
      res.setHeader('Content-Type', file.endsWith('.html') ? 'text/html; charset=utf-8' : file.endsWith('.css') ? 'text/css; charset=utf-8' : file.endsWith('.js') ? 'application/javascript; charset=utf-8' : 'image/jpeg');
      res.end(await fs.readFile(file));
    } catch (_) { res.writeHead(404); res.end('Not found'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true, args: [`--explicitly-allowed-ports=${server.address().port}`] });
  try {
    const page = await browser.newPage({ viewport: { width: 1512, height: 1120 }, reducedMotion: 'reduce' });
    page.setDefaultTimeout(12000);
    const errors = [], layouts = new Map(), writes = [];
    let failSave = false, holdSave = null, holdRead = null, missingAck = false;
    page.on('pageerror', error => errors.push(error.stack));
    await page.exposeBinding('__roomFixtureRequest', async (_source, method, endpoint, payload) => {
      const pathname = new URL(endpoint, 'http://fixture').pathname;
      const persona = payload?._persona_id || 'primary';
      let data = pathname.includes('migration') ? { dismissed: true } : {
        plugin: { bot_name: '小满' }, daily_state: { date: '2026-09-15', location: '家中' },
      };
      if (pathname.endsWith('/home-room/layout/update')) {
        writes.push({ persona, layout: structuredClone(payload.layout) });
        const waiting = holdSave; holdSave = null;
        if (waiting) { waiting.started.resolve(); await waiting.release.promise; }
        if (failSave) return { success: false, error: '模拟写入失败' };
        if (missingAck) return { success: true, data: {} };
        layouts.set(persona, structuredClone(payload.layout));
        data = { saved: true, layout: payload.layout };
      } else if (pathname.endsWith('/home-room/layout')) {
        data = { layout: layouts.has(persona) ? structuredClone(layouts.get(persona)) : null };
        const waiting = holdRead; holdRead = null;
        if (waiting) { waiting.started.resolve(); await waiting.release.promise; }
      }
      return { success: true, data };
    });
    await page.addInitScript(() => {
      window.AstrBotPluginPage = {
        ready: async () => ({}), getContext: () => ({}),
        apiGet: (endpoint, params) => window.__roomFixtureRequest('GET', endpoint, params),
        apiPost: (endpoint, body) => window.__roomFixtureRequest('POST', endpoint, body),
      };
    });
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.goto(origin + '/embedded');
    let frame = page.frames().find(item => item.parentFrame());
    const ready = async () => frame.waitForFunction(() => document.querySelector('[data-home-room-3d-mount]')?.dataset.voxelCount && !document.querySelector('[data-room-edit]').disabled);
    const enter = async () => { await frame.locator('.annotations [data-tab="home"]').click(); await ready(); };
    const layout = () => frame.evaluate(() => PrivateCompanionHomeRoom3D.mount(document.querySelector('[data-home-room-3d-mount]')).getLayout());
    const choose = async style => {
      if (!(await frame.locator('#homeRoomHousePicker').evaluate(el => el.open))) await frame.locator('#homeRoomHousePicker summary').click();
      await frame.locator(`[data-room-house-style="${style}"]`).click();
    };
    const save = async () => { await frame.locator('[data-room-save]').click(); await frame.locator('#homeRoomEditor').waitFor({ state: 'hidden' }); };
    const switchPersona = async id => frame.evaluate(personaId => {
      PrivateCompanionHomeRoom.resetPersona();
      PrivateCompanionHomeRoom.render({ ...homeRoomContext(), personaId });
    }, id);
    const capture = async name => {
      if (!process.env.PC_ROOM_ARTIFACTS) return;
      await fs.mkdir(process.env.PC_ROOM_ARTIFACTS, { recursive: true });
      await frame.locator('#homeRoomRoot').screenshot({ path: path.join(process.env.PC_ROOM_ARTIFACTS, name + '.png') });
    };
    await enter();
    assert.equal(await frame.evaluate(() => { try { localStorage.setItem('probe', '1'); return 'allowed'; } catch (error) { return error.name; } }), 'SecurityError');
    const original = await layout();
    await frame.locator('[data-room-edit]').click(); await choose('loft');
    await frame.locator('#homeRoomFurniture').selectOption('bed');
    await frame.locator('[data-room-style="ink"]').click();
    const firstSaved = await layout(); await save();
    assert.deepEqual(layouts.get('primary'), firstSaved);
    await page.reload(); frame = page.frames().find(item => item.parentFrame()); await enter();
    assert.deepEqual(await layout(), firstSaved, 'opaque-origin reload must recover the server layout');
    await capture('room-storage-sandbox-saved');

    await frame.locator('[data-room-edit]').click(); await choose('cabin');
    const retryDraft = await layout(); failSave = true;
    await frame.locator('[data-room-save]').click();
    await frame.waitForFunction(() => document.getElementById('homeRoomLayoutStatus').textContent === '保存失败');
    assert.deepEqual(await layout(), retryDraft);
    assert.deepEqual(layouts.get('primary'), firstSaved);
    assert.equal(await frame.locator('#homeRoomEditor').isVisible(), true);
    failSave = false; missingAck = true;
    await frame.locator('[data-room-save]').click();
    await frame.waitForFunction(() => document.getElementById('homeRoomLayoutStatus').textContent === '保存失败');
    assert.deepEqual(layouts.get('primary'), firstSaved, 'an unconfirmed response cannot be reported as saved');
    missingAck = false; await save(); assert.deepEqual(layouts.get('primary'), retryDraft);

    await frame.locator('[data-room-edit]').click(); await choose('coastal');
    const snapshot = await layout(), saving = gate(); holdSave = saving;
    await frame.locator('[data-room-save]').click(); await saving.started.promise;
    assert.equal(await frame.locator('[data-room-save]').isDisabled(), true);
    assert.equal(await frame.locator('[data-room-save]').innerText(), '保存中…');
    const count = writes.length;
    await frame.locator('[data-room-save]').dispatchEvent('click'); assert.equal(writes.length, count);
    await choose('washitsu');
    saving.release.resolve();
    await frame.waitForFunction(() => !document.querySelector('[data-room-save]').disabled);
    assert.equal(await frame.locator('#homeRoomEditor').isVisible(), true);
    assert.equal((await layout()).houseStyle, 'washitsu');
    assert.deepEqual(layouts.get('primary'), snapshot);
    await frame.locator('[data-room-cancel]').click(); assert.deepEqual(await layout(), snapshot);

    await frame.locator('[data-room-edit]').click(); await choose('french');
    const late = gate(); holdSave = late;
    await frame.locator('[data-room-save]').click(); await late.started.promise;
    await switchPersona('second'); await ready(); assert.deepEqual(await layout(), original);
    await frame.locator('[data-room-edit]').click(); await choose('cabin');
    const secondDraft = await layout(); late.release.resolve();
    await frame.waitForFunction(() => !document.querySelector('[data-room-edit]').disabled);
    assert.deepEqual(await layout(), secondDraft);
    assert.equal(await frame.locator('#homeRoomEditor').isVisible(), true);
    await save(); assert.deepEqual(layouts.get('second'), secondDraft);
    const reading = gate(); holdRead = reading;
    await switchPersona('primary'); await reading.started.promise;
    assert.equal(await frame.locator('[data-room-edit]').isDisabled(), true);
    await switchPersona('second'); await ready(); reading.release.resolve();
    await frame.evaluate(() => new Promise(resolve => setTimeout(resolve, 80)));
    assert.deepEqual(await layout(), secondDraft, 'late persona reads cannot replace the active room');

    await page.setViewportSize({ width: 430, height: 932 });
    await frame.locator('[data-room-edit]').click(); await choose('washitsu'); await save();
    await frame.locator('[data-room-edit]').click(); await capture('room-storage-mobile');
    assert.equal(await frame.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false);
    await frame.locator('[data-room-cancel]').click();

    // Old standalone layouts remain readable; only an explicit save migrates.
    layouts.clear(); await page.goto(origin + '/index.html'); frame = page.mainFrame();
    await frame.evaluate(value => localStorage.setItem('pc_home_room_layout_v1:primary', JSON.stringify(value)), firstSaved);
    await enter(); assert.deepEqual(await layout(), firstSaved); assert.equal(layouts.size, 0);
    await frame.locator('[data-room-edit]').click(); await save();
    assert.deepEqual(layouts.get('primary'), firstSaved);
    await frame.evaluate(() => localStorage.removeItem('pc_home_room_layout_v1:primary'));
    await page.reload(); await enter(); assert.deepEqual(await layout(), firstSaved);
    // Scene failure still permits reading through the furniture directory.
    // Removing the old view must not strand users without WebGL/module load.
    await page.route('**/home-room-3d.js*', route => route.abort());
    await page.reload();
    await frame.locator('.annotations [data-tab="home"]').click();
    await frame.locator('[data-home-room-3d-error]').waitFor({ state: 'visible' });
    assert.equal(await frame.locator('[data-home-room-scene-2d], button[data-home-room-view]').count(), 0);
    assert.equal(await frame.locator('[data-room-edit]').isDisabled(), true);
    await frame.locator('#homeRoomStations [data-home-room-station="desk"]').click();
    assert.equal(await frame.locator('#homeRoomInspectorTitle').innerText(), '手账与便签');
    await page.unroute('**/home-room-3d.js*');
    await frame.locator('[data-home-room-retry-3d]').click(); await ready();
    assert.deepEqual(await layout(), firstSaved, 'retry restores the saved room');
    assert.deepEqual(errors, []);
    console.log('opaque iframe storage denial, server save/reload, draft retry, acknowledgement, persona races, legacy migration and mobile passed');
  } finally {
    await browser.close(); await new Promise(resolve => server.close(resolve));
  }
});
