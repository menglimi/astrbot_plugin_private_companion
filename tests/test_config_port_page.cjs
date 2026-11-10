"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "pages", "陪伴小屋", "config-port-page.js"), "utf8");

function button(attributes) {
  return {hasAttribute: name => attributes.includes(name), closest: () => null, disabled: false};
}

function setup() {
  const calls = [];
  const created = [];
  const document = {
    createElement(tag) {
      const link = {tag, href: "", download: "", click() { created.push(link); }, remove() {}};
      return link;
    },
    body: {appendChild() {}},
  };
  const window = {
    document,
    CompanionProduct: {
      async request(method, route, body) {
        calls.push([method, route, body]);
        return method === "GET"
          ? {schema: "companion.config-export.v1", sections: {quota: {settings: {enabled: true}}}, unavailable: {}}
          : {applied: {quota: ["enabled"]}, failed: {}};
      },
    },
    CompanionConfirm: async () => true,
  };
  const context = {window, document, Blob, URL: {createObjectURL: () => "blob:config", revokeObjectURL() {}},
    setTimeout, clearTimeout, Promise, Object, Array, Error, Number, String, JSON, Date, Math};
  vm.runInNewContext(source, context);
  const handlers = {};
  const root = {isConnected: true, innerHTML: "", addEventListener(name, fn) { handlers[name] = fn; },
    contains() { return true; }, querySelector() { return null; }};
  window.CompanionConfigPortPage.mount(root);
  return {window, root, handlers, calls, created};
}

test("export downloads a JSON document from the portable config route", async () => {
  const {root, handlers, calls, created} = setup();
  assert.match(root.innerHTML, /配置导入导出/);
  assert.match(root.innerHTML, /data-config-port-export/);
  handlers.click({target: {closest: () => button(["data-config-port-export"])}});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(calls[0][0], "GET");
  assert.equal(calls[0][1], "config/port/export");
  assert.equal(created.length, 1);
  assert.match(created[0].download, /^companion-config-\d{8}-\d{4}\.json$/);
  assert.match(root.innerHTML, /已导出 1 个分区/);
});

test("import uploads the chosen document and reports applied sections", async () => {
  const {root, handlers, calls} = setup();
  let clicked = 0;
  root.querySelector = selector => selector === "[data-config-port-file]"
    ? {click() { clicked += 1; }} : null;
  handlers.click({target: {closest: () => button(["data-config-port-import"])}});
  assert.equal(clicked, 1);
  const document = {schema: "companion.config-export.v1", sections: {quota: {settings: {enabled: true}}}};
  handlers.change({target: {matches: () => true, value: "x", files: [{size: 100,
    async text() { return JSON.stringify(document); }}]}});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(calls.at(-1)[0], "POST");
  assert.equal(calls.at(-1)[1], "config/port/import");
  assert.equal(JSON.stringify(calls.at(-1)[2]), JSON.stringify({document}));
  assert.match(root.innerHTML, /已导入 1 个分区/);
});
