const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const page = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/state-page.js'), 'utf8');
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
