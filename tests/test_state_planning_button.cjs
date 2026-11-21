const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const crypto = require('node:crypto');

const source = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/state-editor.js'), 'utf8');

function editorFor(settings, {roleToken = 'role-token'} = {}) {
  const window = {CompanionStateMaintenance: {
    fields: kind => kind === 'planning'
      ? ['calendar_enabled', 'planning_timezone', 'planning_daily_items'] : [],
    validField: () => true,
    titles: {planning: '日程与规划'},
    canAlign: () => false,
  }, CompanionStateContact: {}};
  vm.runInNewContext(source, {window, structuredClone, JSON, crypto, console});
  const editor = window.CompanionStateEditor.create({
    changed() {}, failed() {}, refresh: async () => null, api: async () => ({}),
  });
  editor.setScope({installation_id: 'install', operator_id: 'admin', path: 'role/state'});
  editor.setStatus({edit_token: 'window-token', role_edit_token: roleToken,
    settings, state: {values: {}}});
  return editor;
}

test('日程按钮不会被旧快照里缺少的新字段禁用', () => {
  const editor = editorFor({calendar_enabled: true, planning_timezone: 'Asia/Shanghai'});
  assert.equal(editor.open('planning'), true,
    'present fields are valid; one absent optional field must not block the editor');
  editor.destroy();
});
