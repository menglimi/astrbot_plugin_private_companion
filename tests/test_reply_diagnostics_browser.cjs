"use strict";
const assert = require('node:assert/strict');
const test = require('node:test');
const {browserPreferencesServer} = require('./fixtures/browser_preferences_server.cjs');
const {roleKey} = require('./fixtures/role_scope_browser.cjs');
let chromium;
try { ({chromium} = require('playwright')); } catch (_) {}

test('从设置和排障进入本会话诊断，浏览器只在诊断页请求逐轮记录', {skip:chromium ? false : '未安装 playwright'}, async t => {
  const {data, url, close} = await browserPreferencesServer();
  t.after(close);
  const browser = await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:390,height:844}, reducedMotion:'reduce'});
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(url);
  const frame = page.frameLocator('#plugin');
  const nav = frame.locator('#shellNav');
  const jump = frame.getByRole('link', {name:'查看本会话回复诊断'});
  const records = frame.locator('#replyDiagnosticsWorkspace');
  const windowPicker = frame.locator('[data-role-window]');
  const historyReads = () => data.calls.filter(call => call.params?.view === 'diagnostics');
  await jump.waitFor();
  await frame.locator('body').evaluate((_, key) => CompanionProduct.chooseRoleKey(key), roleKey('a'));
  await nav.locator('[data-shell-section="decision"]').click();
  assert.equal(await jump.count(), 1);
  await jump.click();
  await records.getByText(/请选择当前角色的具体私聊或群聊窗口/).waitFor();
  assert.equal(historyReads().length, 0);

  await windowPicker.selectOption('private-a');
  await records.locator('.reply-record summary').click();
  await records.getByText(/模型失败分类：TestProviderError（测试私聊线路）/).waitFor();
  await windowPicker.selectOption('group-a');
  await records.locator('.reply-record summary').click();
  await records.getByText(/模型失败分类：TestProviderError（测试群线路）/).waitFor();
  assert.doesNotMatch(await records.innerHTML(), /测试私聊线路/);

  await frame.getByRole('button', {name:'返回排障 · 消息收口'}).click();
  await frame.locator('[data-diagnostic-category="delivery"][aria-pressed="true"]').waitFor();
  await frame.getByRole('button', {name:'查看本会话回复诊断'}).click();
  await records.locator('.reply-record').waitFor();
  await nav.locator('[data-shell-section="expression"]').click();
  assert.equal(await jump.count(), 1);
  assert.equal(await frame.locator('.reply-record').count(), 0);
  await jump.click();
  await records.locator('.reply-record').waitFor();
  assert.equal(await windowPicker.inputValue(), 'group-a');
  assert.deepEqual(historyReads().map(call => call.endpoint.split('/')[2]), ['private-a','group-a','group-a','group-a']);
  assert.deepEqual(errors, []);
});
