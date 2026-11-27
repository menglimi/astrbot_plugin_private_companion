const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const page = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/state-page.js'), 'utf8');
const sidebar = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/home-sidebar.js'), 'utf8');
const maintenance = fs.readFileSync(
  path.resolve(__dirname, '../companion_v2/role_work/state/maintenance.py'), 'utf8');

test('failed maintenance no longer leaves an unclickable 更新中 button', () => {
  assert.doesNotMatch(page, /pending&&!pending\.retryable/,
    'a failed optimistic pending record must not disable the manual update button');
  assert.match(page, /const pending=\{operation_key/,
    'a manual click must create a fresh operation instead of returning early');
  assert.match(page, /pending\?"重试更新"/);
});

test('maintenance failures retry shortly and carry a retryable result', () => {
  assert.match(maintenance, /"retryable": True/);
  assert.match(maintenance, /timedelta\(minutes=5\)/);
  assert.match(maintenance, /next_at = \?/,
    'a failed maintenance must pull the next scan forward instead of waiting a full period');
});

test('state page translates provider_request_failed and state_view_stale, handles queued, and refreshes sidebar', () => {
  assert.match(page, /state_view_stale:/);
  assert.match(page, /provider_request_failed:formatProviderFailure\(result\)/);
  assert.match(page, /result\?\.state==="queued"/);
  assert.match(page, /current\?\.maintain_token/);
  assert.match(page, /下方编辑区有未保存修改或待核对请求/);
  assert.match(page, /已刷新 · /);
  assert.match(page, /refreshSummary\?\.\(\)/);
});

test('home sidebar translates provider_request_failed with HTTP status, provider, and fallback status', () => {
  const vm = require('node:vm');
  const window = {};
  const document = { addEventListener: () => {} };
  vm.runInNewContext(sidebar, { window, document });
  const { renderSchedule } = window.CompanionHomeSidebar;
  const html = renderSchedule({
    now: Date.parse('2026-09-30T04:00:00Z'),
    state: {
      settings: { enabled: true, planning_timezone: 'Asia/Shanghai' },
      state: { revision: 1, values: {} },
      occurrences: { items: [], partial: false },
      sleep_schedule: { enabled: false },
      planning: {
        enabled: true,
        calendar_enabled: true,
        next_check: '2026-09-30T04:01:00Z',
        daily_result: {
          state: 'failed',
          code: 'provider_request_failed',
          http_status: 429,
          provider: 'sta1n/gemini-flash',
          fallback_attempted: false,
        },
        next_review_at: '2026-09-30T10:00:00Z',
      },
    },
    life: { activities: [], config: { diary_enabled: false } },
  });
  assert.match(html, /当日规划未完成：模型额度或频次受限（HTTP 429 · sta1n\/gemini-flash）/);
  const htmlFallback = renderSchedule({
    now: Date.parse('2026-09-30T04:00:00Z'),
    state: {
      settings: { enabled: true, planning_timezone: 'Asia/Shanghai' },
      state: { revision: 1, values: {} },
      occurrences: { items: [], partial: false },
      sleep_schedule: { enabled: false },
      planning: {
        enabled: true,
        calendar_enabled: true,
        next_check: '2026-09-30T04:01:00Z',
        daily_result: {
          state: 'failed',
          code: 'provider_request_failed',
          http_status: 503,
          provider: 'backup/model',
          fallback_attempted: true,
        },
        next_review_at: '2026-09-30T10:00:00Z',
      },
    },
    life: { activities: [], config: { diary_enabled: false } },
  });
  assert.match(htmlFallback, /当日规划未完成：上游模型服务暂时不可用（HTTP 503 · backup\/model · 已尝试备用模型）/);
});
