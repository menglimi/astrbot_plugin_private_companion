"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/catalog.html"), "utf8");
const source = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].at(-1)?.[1];
assert.ok(source, "catalog inline script is present");

function openCatalog(bridge, search = "?asset_token=old") {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {
      dataset: {}, hidden: false, textContent: "", value: id === "searchInput" ? "no-match" : "",
      listeners: {}, addEventListener(type, listener) {this.listeners[type] = listener;},
      replaceChildren() {},
    });
    return nodes.get(id);
  };
  const assigned = [];
  const location = {
    href: "https://example.test/api/plugin/page/content/astrbot_plugin_private_companion/%E9%99%AA%E4%BC%B4%E5%B0%8F%E5%B1%8B/catalog.html" + search,
    search,
    assign(url) {assigned.push(url);},
  };
  const document = {
    getElementById: node,
    createElement() {return {append() {}, setAttribute() {}, className: "", textContent: ""};},
    documentElement: {dataset: {}},
  };
  const window = {AstrBotPluginPage: bridge, addEventListener() {}};
  vm.runInNewContext(source, {window, document, location, URL, URLSearchParams,
    localStorage: {getItem: () => null}, setTimeout, clearTimeout});
  return {node, assigned, click: id => {
    let prevented = false;
    const result = node(id).listeners.click({preventDefault() {prevented = true;}});
    return Promise.resolve(result).then(() => prevented);
  }};
}

test("catalog returns home with a fresh host asset token", async () => {
  const page = openCatalog({
    ready: async () => {},
    apiGet: async endpoint => {
      assert.equal(endpoint, "page/workspace-entry");
      return {content_path: "/api/plugin/page/content/astrbot_plugin_private_companion/%E9%99%AA%E4%BC%B4%E5%B0%8F%E5%B1%8B/?asset_token=fresh"};
    },
  });
  assert.equal(await page.click("backHome"), true);
  assert.equal(page.assigned[0],
    "https://example.test/api/plugin/page/content/astrbot_plugin_private_companion/%E9%99%AA%E4%BC%B4%E5%B0%8F%E5%B1%8B/index.html?asset_token=fresh#life");
});

test("catalog leaves a visible recovery path when token refresh fails", async () => {
  const page = openCatalog({apiGet: async () => {throw new Error("unauthorized");}});
  assert.equal(await page.click("brandHome"), true);
  assert.equal(page.assigned.length, 0);
  assert.equal(page.node("navigationNotice").hidden, false);
  assert.match(page.node("navigationNotice").textContent, /重新打开陪伴小屋/);
});

test("catalog static preview keeps its direct home link", async () => {
  const page = openCatalog(undefined, "");
  assert.equal(await page.click("backHome"), false);
  assert.equal(page.node("backHome").href, "./#life");
});
