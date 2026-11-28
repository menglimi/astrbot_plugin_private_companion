"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "../pages/陪伴小屋/state-page.js"), "utf8");

function harness({feature, legacyHostFeature} = {}) {
  const listeners = {}, calls = [], editorOpens = [], calendarActions = [];
  const window = {
    CompanionProduct: {hosted: () => false, bridgeNow: () => ({ready: () => new Promise(() => {})})},
    CompanionContactPage: {openGateSettingsView: () => calls.push(["open-gates"])},
    CompanionCalendarEditor: {create: () => ({
      click(event) {
        const button = event.target.closest("[data-calendar-edit]");
        if (!button) return false;
        calendarActions.push(button.dataset.calendarEdit);
        return true;
      },
    })},
    CompanionStateEditor: {create: () => ({
      open: kind => editorOpens.push(kind), setScope() {}, invalidate() {}, setStatus() {},
      discardIfPristine: () => true, markup: () => "", destroy() {},
      click: () => false,
      interacting: false, dirty: false, busy: false,
    })},
    navigate: (...args) => calls.push(["navigate", ...args]),
  };
  const document = {addEventListener() {}, body: {classList: {toggle() {}}}, querySelector() { return null; }};
  const root = {isConnected: true, dataset: {stateFeature: legacyHostFeature},
    addEventListener: (name, handler) => {listeners[name] = handler;},
    removeEventListener() {}, contains() { return false; }, querySelectorAll() { return []; },
    querySelector(selector) { return selector === "#state-feature-detail" ? {scrollIntoView() {}} : null; }};
  vm.runInNewContext(source, {window, document, console, Promise, Date, Intl,
    setTimeout: () => 1, clearTimeout() {}, matchMedia: () => ({matches: true})});
  const page = window.CompanionStatePage;
  void page.mount(root, {feature});
  function dispatch(matches = {}) {
    listeners.click({target: {closest: selector =>
      matches[selector] || (selector === "[data-state-feature]" && legacyHostFeature ? root : null)}});
  }
  return {calls, editorOpens, calendarActions, page, click(feature) {
    const button = {dataset: {stateFeature: feature}};
    dispatch({"button[data-state-feature]": button, "[data-state-feature]": button});
  }, clickGate(feature) {
    dispatch({"[data-state-gate]": {dataset: {stateGate: feature}}});
  }, clickBlank: dispatch, clickCalendar(action) {
    dispatch({"[data-calendar-edit]": {dataset: {calendarEdit: action}}});
  }};
}

test("睡眠和忙碌先打开状态详情，再由回复阀门按钮导航", () => {
  for (const feature of ["sleep", "busy"]) {
    const h = harness();
    h.click(feature);
    assert.deepEqual(h.calls, [], feature);
    h.clickGate(feature);
    assert.deepEqual(h.calls, [["navigate", "manage", "contact"], ["open-gates"]], feature);
    h.page.unmount();
  }
});

test("尚未读取真实状态时不会创建编辑草稿", () => {
  const h = harness();
  h.click("energy");
  h.click("health");
  assert.deepEqual(h.editorOpens, []);
  h.page.unmount();
});

test("功能卡只负责展开，预置展开后再次点击不会收起", () => {
  const h = harness();
  h.click("planning");
  assert.equal(h.page.currentFeature(), "planning");
  h.click("planning");
  assert.equal(h.page.currentFeature(), "planning");
  h.page.unmount();
});

test("日历宿主属性不会把空白或计划按钮的点击当成功能卡", () => {
  const h = harness({feature: "planning", legacyHostFeature: "planning"});
  h.clickCalendar("create");
  assert.deepEqual(h.calendarActions, ["create"]);

  h.click("energy");
  h.clickBlank();
  assert.equal(h.page.currentFeature(), "energy");
  h.page.unmount();
});
