/* Local browser fixtures: no real weather/provider calls or bot writes. */
const { createRequire } = require('node:module');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const { chromium } = process.env.PC_PLAYWRIGHT_DIR
  ? createRequire(path.join(process.env.PC_PLAYWRIGHT_DIR, 'package.json'))('playwright') : require('playwright');
const pageRoot = path.resolve(__dirname, '../pages/companion-panel');

test('room atmosphere: real weather states, daylight, quiet visits, lifecycle and mobile', { timeout: 240000 }, async () => {
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
    page.setDefaultTimeout(12000);
    const errors = [], writes = [];
    let weatherRequests = 0, failure = false;
    let serverTime = Date.parse('2026-09-15T14:20:00+08:00') / 1000;
    let weather = { status: 'live', condition: 'rain', summary: '小雨，约 18°C，体感 17°C', source: 'qweather', location: '杭州', temperature_c: 18, updated_at: serverTime - 120, stale_after_seconds: 5400 };
    page.on('pageerror', error => errors.push(error.stack));
    await page.route('**/api/v1/**', route => {
      const request = route.request(), pathname = new URL(request.url()).pathname;
      if (request.method() !== 'GET' && !pathname.endsWith('/extension-migration-notice/update')) writes.push(pathname);
      let data = pathname.includes('migration') ? { dismissed: true } : { plugin: { bot_name: '小满' }, daily_state: { date: '2026-09-15', location: '家中', note: '窗边的薄荷，又长出了新叶。' } };
      if (pathname.endsWith('/home-room/layout')) data = { layout: null };
      if (pathname.endsWith('/home-room/environment')) {
        weatherRequests++;
        if (failure) return route.fulfill({ status: 503, json: { success: false, message: '天气服务暂不可用' } });
        data = { server_ts: serverTime, timezone: 'Asia/Shanghai', weather };
      }
      return route.fulfill({ json: { success: true, data } });
    });
    await page.addInitScript(() => {
      window.__PRIVATE_COMPANION_STANDALONE__ = { standalone: true, mode: 'standalone', apiBase: '/api/v1' };
      localStorage.setItem('pc_home_room_view_v1', '3d');
      localStorage.setItem('pc_extension_migration_notice_6_2_2_dismissed', '1');
    });
    const enterRoom = async () => {
      await page.locator('.annotations [data-tab="home"]').click();
      await page.waitForFunction(() => document.querySelector('[data-home-room-3d-mount]')?.dataset.voxelCount);
      await page.evaluate(() => { window.roomApi = PrivateCompanionHomeRoom3D.mount(document.querySelector('[data-home-room-3d-mount]')); });
    };
    const ambient = () => page.evaluate(() => roomApi.getAmbientState());
    const refresh = async () => {
      const prior = weatherRequests;
      await page.locator('[data-room-weather-refresh]').click();
      await page.waitForFunction(() => !document.querySelector('[data-room-weather-refresh]').disabled);
      assert.equal(weatherRequests, prior + 1);
    };
    const capture = async name => {
      if (!process.env.PC_ROOM_ARTIFACTS) return;
      await fs.mkdir(process.env.PC_ROOM_ARTIFACTS, { recursive: true });
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ fullPage: true, path: path.join(process.env.PC_ROOM_ARTIFACTS, name + '.png') });
    };
    await page.goto(`http://127.0.0.1:${server.address().port}/`); await enterRoom();
    await page.waitForFunction(() => document.getElementById('homeRoomWeather').textContent.includes('杭州'));
    assert.match(await page.locator('#homeRoomWeather').innerText(), /杭州.*雨.*18°C/);
    assert.match(await page.locator('#homeRoomWeatherDetail').innerText(), /实况天气.*和风天气.*2 分钟/);
    assert.equal(await page.locator('#homeRoomClock').innerText(), '14:20');
    assert.equal((await ambient()).light, 'day');
    assert.equal((await ambient()).condition, 'rain');
    assert.equal((await ambient()).precipitation, true);
    const originalLayout = await page.evaluate(() => roomApi.getLayout());
    await page.locator('[data-room-encounter]').click();
    assert.equal((await ambient()).event, 'steam', 'rainy days offer an indoor tea moment');
    assert.equal((await ambient()).motion, false);
    assert.match(await page.locator('#homeRoomMoment').innerText(), /热气/);
    await capture('rainy-tea-desktop');
    await page.clock.install();
    const before = await ambient();
    await page.clock.runFor(500);
    assert.equal((await ambient()).eventElapsed, before.eventElapsed, 'system reduced motion freezes the pose');
    await page.locator('[data-room-edit]').click();
    assert.equal((await ambient()).paused, true);
    assert.ok(await page.locator('[data-room-encounter]').isDisabled());
    await page.clock.runFor(15000);
    assert.equal((await ambient()).event, 'steam', 'editing preserves the remaining visit');
    await page.locator('[data-room-cancel]').click();
    assert.deepEqual(await page.evaluate(() => roomApi.getLayout()), originalLayout);
    await page.clock.runFor(13000);
    assert.equal((await ambient()).event, null);
    failure = true; await refresh();
    assert.match(await page.locator('#homeRoomWeatherDetail').innerText(), /更新未成功.*上次天气/);
    assert.equal((await ambient()).condition, 'rain');
    failure = false;
    weather = { ...weather, status: 'stale', condition: 'snow', summary: '雪，-4°C', temperature_c: -4, updated_at: serverTime - 7200 };
    await refresh();
    assert.match(await page.locator('#homeRoomWeatherDetail').innerText(), /上次天气.*2 小时/);
    assert.equal((await ambient()).condition, 'snow');
    await capture('snow-desktop');
    weather = { ...weather, status: 'disabled', summary: '', condition: 'unknown', temperature_c: null, location: '' };
    await refresh();
    assert.match(await page.locator('#homeRoomWeather').innerText(), /未开启/);
    assert.equal((await ambient()).condition, 'unknown');
    assert.equal((await ambient()).precipitation, false);
    weather = { ...weather, status: 'live', condition: 'clear', summary: '晴，22°C', source: 'qweather', temperature_c: 22, location: '杭州', updated_at: serverTime };
    serverTime = Date.parse('2026-09-15T18:20:00+08:00') / 1000; await refresh();
    assert.equal((await ambient()).light, 'dusk');
    await capture('dusk-desktop');
    await page.locator('.room-atmosphere summary').click();
    await page.locator('#homeRoomLightMode').selectOption('night');
    assert.equal((await ambient()).light, 'night');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.clock.runFor(100);
    await page.waitForFunction(() => roomApi.getAmbientState().motion);
    assert.equal((await ambient()).motion, true);
    await page.locator('[data-room-encounter]').click();
    const event = (await ambient()).event;
    assert.ok(['steam', 'fireflies'].includes(event));
    await page.clock.runFor(500);
    assert.ok((await ambient()).eventElapsed > 0, 'motion animates a visit');
    await page.evaluate(() => roomApi.toggleTour());
    const duringTour = (await ambient()).eventElapsed;
    await page.clock.runFor(500);
    assert.equal((await ambient()).eventElapsed, duringTour, 'cinematic touring pauses the visit');
    await page.evaluate(() => roomApi.toggleTour());
    await page.locator('#homeRoomMotion').uncheck();
    const frozen = (await ambient()).eventElapsed;
    await page.clock.runFor(500); assert.equal((await ambient()).eventElapsed, frozen);
    await page.locator('.room-atmosphere summary').click();
    await capture('night-visit-desktop');
    // Tab deactivation pauses weather polling and the visit, without replaying
    // time spent elsewhere when the room becomes active again.
    await page.evaluate(() => PrivateCompanionHomeRoom.setActive(false));
    const requestsBeforePause = weatherRequests;
    await page.clock.fastForward(600000);
    assert.equal(weatherRequests, requestsBeforePause);
    assert.equal((await ambient()).eventElapsed, frozen);
    await page.evaluate(() => PrivateCompanionHomeRoom.setActive(true));
    await page.waitForFunction(() => !document.querySelector('[data-room-weather-refresh]').disabled);
    assert.equal(weatherRequests, requestsBeforePause + 1);
    assert.equal((await ambient()).light, 'night', 'weather refresh preserves a manual light setting');
    await page.clock.runFor(13000);
    await page.locator('.room-atmosphere summary').click();
    await page.locator('#homeRoomEvents').uncheck();
    assert.equal((await ambient()).events, false);
    await page.clock.runFor(80000);
    assert.equal((await ambient()).event, null);
    await page.locator('[data-room-encounter]').click();
    assert.ok((await ambient()).event, 'manual visits still work with automatic events disabled');
    await page.locator('#homeRoomEvents').check();
    await page.evaluate(() => roomApi.resetAtmosphere());
    const historyCount = await page.locator('#homeRoomMomentHistory li').count();
    await page.clock.runFor(76000);
    assert.ok(await page.locator('#homeRoomMomentHistory li').count() > historyCount || (await ambient()).event, 'an idle active room gets an occasional event');
    await page.locator('#homeRoomLightMode').selectOption('auto');
    assert.equal((await ambient()).light, 'dusk');
    await page.locator('.room-atmosphere summary').click();
    // Late previous-persona responses must never restore its city or moments.
    await page.evaluate(async () => {
      PrivateCompanionHomeRoom.resetPersona();
      const context = homeRoomContext();
      window.oldEnvironmentResolve = null;
      PrivateCompanionHomeRoom.render({ ...context, personaId: 'room-old', fetchJson: endpoint => endpoint === '/home-room/environment' ? new Promise(resolve => { window.oldEnvironmentResolve = resolve; }) : context.fetchJson(endpoint) });
      PrivateCompanionHomeRoom.resetPersona();
      PrivateCompanionHomeRoom.render({ ...context, personaId: 'room-new', fetchJson: endpoint => endpoint === '/home-room/environment' ? Promise.resolve({ server_ts: Date.now() / 1000, timezone: 'Asia/Shanghai', weather: { status: 'live', condition: 'cloudy', summary: '多云', location: '新地点', source: 'qweather', updated_at: Date.now() / 1000 } }) : context.fetchJson(endpoint) });
      await Promise.resolve();
      window.oldEnvironmentResolve({ server_ts: Date.now() / 1000, weather: { status: 'live', condition: 'snow', summary: '雪', location: '旧地点' } });
    });
    await page.waitForFunction(() => document.getElementById('homeRoomWeather').textContent.includes('新地点'));
    assert.equal((await ambient()).condition, 'cloudy');
    assert.match(await page.locator('#homeRoomMomentHistory').textContent(), /还没有遇见/);
    assert.deepEqual(await page.evaluate(() => roomApi.getState().placementIssues), {});
    await page.setViewportSize({ width: 430, height: 932 });
    await page.locator('.room-atmosphere summary').click();
    await capture('atmosphere-mobile');
    const labelContrast = await page.locator('.room-atmosphere-options').evaluate(el => {
      const luminance = rgb => rgb.match(/[\d.]+/g).slice(0, 3).map(Number).map(v => { const c = v / 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; }).reduce((sum, c, i) => sum + c * [.2126, .7152, .0722][i], 0);
      const bg = luminance(getComputedStyle(el).backgroundColor);
      return [...el.querySelectorAll('label')].map(label => { const fg = luminance(getComputedStyle(label).color); return (Math.max(bg, fg) + .05) / (Math.min(bg, fg) + .05); });
    });
    assert.ok(labelContrast.every(ratio => ratio >= 4.5), 'night settings text must remain readable');
    assert.equal(await page.locator('#homeRoomRoot').evaluate(el => el.scrollWidth > el.clientWidth + 1), false);
    for (const selector of ['.room-environment', '.room-atmosphere-options']) {
      const rect = await page.locator(selector).boundingBox();
      assert.ok(rect.x >= 0 && rect.x + rect.width <= 431, `${selector} fits the mobile screen`);
    }
    await page.locator('.room-atmosphere summary').click();
    await page.locator('[data-room-edit]').click();
    assert.ok(await page.locator('[data-room-encounter]').isDisabled());
    assert.ok(await page.locator('#homeRoomWeather').isVisible());
    assert.equal((await ambient()).paused, true);
    await page.locator('[data-room-cancel]').click();
    await page.locator('.room-atmosphere summary').click();
    await page.locator('#homeRoomLightMode').selectOption('night');
    await page.locator('#homeRoomEvents').uncheck();
    await page.reload(); await enterRoom();
    assert.equal((await ambient()).light, 'night');
    assert.equal((await ambient()).events, false);
    assert.equal((await ambient()).motion, false, 'atmosphere preferences survive reload');
    assert.deepEqual(writes, []); assert.deepEqual(errors, []);
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
});
