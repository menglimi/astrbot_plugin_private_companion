const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../pages/陪伴小屋/workbench-page.js'), 'utf8');
function harness() {
  const window = {CompanionProduct: {
    targets: () => [{target_id:'group-b', display_name:'测试群', roles:[{role_id:'role-b',role_name:'唯'}]}],
    target: () => {throw new Error('must not infer an editor owner from the current window');},
    role: () => {throw new Error('must not infer an editor owner from the current role');},
    targetLabel: target => target.display_name,
    roleLabel: role => role.role_name,
  }};
  vm.runInNewContext(source, {window});
  return window.CompanionWorkbench;
}
test('scope labels follow the editor endpoint and its actual write layer', () => {
  const wb = harness();
  const object = wb.scopeObjectForEndpoint('/astrbot_plugin_private_companion/targets/group-b/roles/role-b/config');
  assert.equal(wb.saveScopeLabel('role',object), '唯的角色默认');
  assert.equal(wb.saveScopeLabel('window',object), '测试群的覆盖设置（角色：唯）');
  assert.equal(wb.saveScopeLabel('role',{...object,account:'QQ 官方'}),'唯的角色默认（Bot：QQ 官方）');
  assert.equal(wb.saveScopeLabel('installation',{...object,account:'QQ 官方'}),'全局默认设置');
  assert.equal(wb.saveScopeLabel('installation',object), '全局默认设置');
  const missing = wb.scopeObjectForEndpoint('/astrbot_plugin_private_companion/targets/missing/roles/role-b/config');
  assert.equal(wb.saveScopeLabel('window', missing), '当前窗口覆盖');
  assert.equal(wb.scopeObjectForEndpoint('/astrbot_plugin_private_companion/config').role, '');
});
test('scope details are escaped, linked to a separate non-submit disclosure and have unique IDs', () => {
  const wb = harness();
  const first = wb.scopeHintMarkup('保存到：<测试群>\n继承自：角色默认');
  const second = wb.scopeHintMarkup('其他范围');
  assert.match(first, /type="button"[^>]*data-scope-hint/);
  assert.match(first, /aria-expanded="false"/);
  const id = /aria-controls="([^"]+)"/.exec(first)[1];
  assert.match(first, new RegExp(`aria-describedby="${id}"`));
  assert.match(first, new RegExp(`id="${id}" role="tooltip"`));
  assert.match(first, /保存到：&lt;测试群&gt;/);
  assert.doesNotMatch(first, /data-wb-action|data-model-action|data-layer=/);
  assert.notEqual(id, /aria-controls="([^"]+)"/.exec(second)[1]);
});
test('touch toggles and Escape dismisses the hint without invoking save', () => {
  const wb = harness(), attributes = {'aria-expanded':'false'}, classes = new Set();
  const context = {classList:{toggle(name,on) { on ? classes.add(name) : classes.delete(name); }, remove(name){classes.delete(name);}, add(name){classes.add(name);}},
    querySelector:selector=>selector === "[data-scope-hint]" ? button : null};
  const button = {closest: selector => selector === '.wb-context' ? context : button,
    getAttribute:key=>attributes[key],setAttribute:(key,value)=>attributes[key]=value};
  assert.equal(wb.scopeHintClick({target:button}),true);
  assert.equal(attributes['aria-expanded'],'true');
  assert.ok(classes.has('is-open'));
  wb.scopeHintClick({target:button});
  assert.equal(attributes['aria-expanded'],'false');
  assert.ok(classes.has('is-dismissed'));
  wb.scopeHintClick({target:button});
  let prevented = false;
  wb.scopeHintKeydown({target:button,key:'Escape',preventDefault:()=>prevented=true});
  assert.ok(prevented);
  assert.equal(attributes['aria-expanded'],'false');
  wb.scopeHintReset({target:button});
  assert.ok(!classes.has('is-dismissed'));
  assert.equal(wb.scopeHintClick({target:{closest:()=>null}}),false);
});
