"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/reply-diagnostics.js"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));

function element() {
  const handlers = {}, el = {innerHTML: "", isConnected: true,
    set textContent(value) { this.innerHTML = value; },
    addEventListener(name, fn) { handlers[name] = fn; },
    removeEventListener(name) { delete handlers[name]; },
    refresh() { handlers.click?.({target:{closest: () => true}}); }};
  return el;
}
function setup() {
  const scope = {key:"private-a", target:{scope_kind:"private", participant_id:"a"}, endpoint:"/plugin/a/reply"};
  const requests = [];
  const product = {scopeKey:() => scope.key, target:() => scope.target,
    role:() => { throw new Error("代表窗口不能用于读取历史"); }, windowRole:() => ({reply_api:scope.endpoint}),
    relative:route => { if (!route?.startsWith("/plugin/")) throw new Error(); return route.slice(8); },
    request:(method, route, query) => new Promise((resolve, reject) => requests.push({method, route, query, resolve, reject})),
    errorLabel:() => "请检查连接。"};
  const window = {CompanionProduct:product};
  vm.runInNewContext(source, {window});
  return {page:window.CompanionReplyDiagnostics, scope, requests};
}
const result = marker => ({recent:[{turn_type:marker, calls:[], parts:[]}]});

test('只读显式窗口的接口；重复渲染复用在途请求和结果，切换后忽略较迟返回的旧记录', async () => {
  const {page, scope, requests} = setup();
  const first = element(), next = element();
  page.mount(first); page.mount(next);
  assert.deepEqual(requests.map(r => [r.method, r.route, r.query.view]), [['GET','a/reply','diagnostics']]);
  Object.assign(scope, {key:'group-b',target:{scope_kind:'group',group_id:'b'},endpoint:'/plugin/groups/b/reply'});
  page.mount(next);
  requests[1].resolve(result('new-group-result')); await flush();
  assert.match(next.innerHTML,/new-group-result/);
  requests[0].resolve(result('first-private-result')); await flush();
  assert.doesNotMatch(next.innerHTML,/first-private-result/);
  const repainted = element(); page.mount(repainted);
  assert.match(repainted.innerHTML,/new-group-result/);
  assert.equal(requests.length, 2);
  scope.target = null; scope.key = '';
  page.mount(repainted);
  assert.match(repainted.innerHTML,/请选择/);
  assert.doesNotMatch(repainted.innerHTML,/new-group-result/);
});

test('未选窗口、通配模板与未发布接口都不取历史；拒绝访问可重试，离开后不写回', async () => {
  const {page, scope, requests} = setup(), root = element();
  for (const target of [null, {scope_kind:'private',participant_id:'*'}, {scope_kind:'group',group_id:'*'}]) {
    scope.target = target; page.mount(root);
    assert.match(root.innerHTML,/请选择/);
  }
  scope.target = {scope_kind:'group',group_id:'b'}; scope.endpoint = null; page.mount(root);
  assert.equal(requests.length, 0);
  scope.endpoint = '/plugin/groups/b/reply'; page.mount(root);
  requests[0].reject({code:'reply_window_required'}); await flush();
  assert.match(root.innerHTML,/不会显示其他窗口/);
  root.refresh(); root.refresh(); assert.equal(requests.length, 2);
  page.unmount(); const oldMarkup = root.innerHTML;
  requests[1].resolve(result('after-unmount')); await flush();
  assert.equal(root.innerHTML, oldMarkup);
});
