"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../pages/陪伴小屋/understanding-page.js"), "utf8");
const tabIds = ["overview", "image", "video", "file", "forwards"];

function harness() {
  const calls = [];
  const attributes = node => ({
    dataset: node,
    setAttribute(name, value) { this[name] = value; },
    focus() { this.focused = true; },
  });
  const tabs = tabIds.map(id => attributes({understandingTab: id}));
  const panels = tabIds.map(id => ({dataset: {understandingPanel: id}, hidden: id !== "overview"}));
  const stats = tabIds.map(id => ({dataset: {understandingStats: id}, innerHTML: ""}));
  const slots = Object.fromEntries(tabIds.map(id => [id, {appendChild(child) { child.parentElement = this; }}]));
  const editor = {id: "contextImageWorkspace", parentElement: slots.overview};
  const heading = {textContent: ""};
  const handlers = {};
  const root = {
    dataset: {understandingActive: "overview"},
    addEventListener(type, listener) { handlers[type] = listener; },
    removeEventListener(type) { delete handlers[type]; },
    contains() { return true; },
    closest() { return {previousElementSibling: {querySelector: () => heading}}; },
    querySelectorAll(selector) {
      if (selector === "[data-understanding-tab]") return tabs;
      if (selector === "[data-understanding-panel]") return panels;
      if (selector === "[data-understanding-stats]") return stats;
      return [];
    },
    querySelector(selector) {
      if (selector === "#contextImageWorkspace") return editor;
      const slot = /^\[data-understanding-editor-slot="(.+)"\]$/.exec(selector);
      return slot ? slots[slot[1]] : null;
    },
  };
  const window = {CompanionReplyPage: {setPerceptionModule: id => calls.push(id)}};
  vm.runInNewContext(source, {window});
  return {page: window.CompanionUnderstandingPage, calls, root, tabs, panels, stats, slots, editor, heading, handlers};
}

test("understanding tabs show live settings and keep the shared editor in the active panel", () => {
  const h = harness();
  h.page.updateReplyStatus({settings: {
    enable_private_image_vision_cache: true, private_image_vision_cache_max_items: 300,
    private_image_provider_failure_cooldown_seconds: 60, context_image_caption_max_items: 2,
    video_understanding_enabled: true, video_understanding_max_chars: 2400,
    video_understanding_wait_seconds: 20, file_understanding_enabled: false,
    file_understanding_max_size_mb: 8, file_understanding_timeout_seconds: 15,
    file_understanding_format_groups: '["文本","PDF","Word（DOCX）","Excel（XLSX）","PowerPoint（PPTX）"]',
    image_understanding_private_enabled: true, image_understanding_group_enabled: true,
    video_understanding_private_enabled: false, video_understanding_group_enabled: true,
    file_understanding_private_enabled: true, file_understanding_group_enabled: false,
  }});
  h.page.updateHostOptions({forwardIntake: {max_forwards: 4, max_nodes: 32, max_depth: 4}});
  h.page.mount(h.root);

  assert.match(h.stats[0].innerHTML, /视觉转述缓存上限/);
  assert.match(h.stats[0].innerHTML, /已启用 4 项/);
  assert.equal(h.editor.parentElement, h.slots.overview);

  h.handlers.click({target: {closest: selector => selector === "[data-understanding-tab]" ? h.tabs[2] : null}});
  assert.equal(h.panels[2].hidden, false);
  assert.equal(h.panels[0].hidden, true);
  assert.equal(h.editor.parentElement, h.slots.video);
  assert.deepEqual(h.calls, ["overview", "video"]);
  assert.match(h.stats[2].innerHTML, /20 秒/);
  assert.equal(h.heading.textContent, "视频理解");

  h.handlers.click({target: {closest: selector => selector === "[data-understanding-tab]" ? h.tabs[4] : null}});
  assert.equal(h.editor.parentElement, h.slots.forwards);
  assert.match(h.stats[4].innerHTML, />32</);
});

test("understanding reports tab changes for route synchronization", () => {
  const h = harness(), changes = [];
  h.page.mount(h.root, {onTabChange: tab => changes.push(tab)});
  h.page.select("video");
  h.page.select("video");
  h.page.select("unknown");
  assert.deepEqual(changes, ["video"]);
  h.page.unmount();
  h.page.select("image");
  assert.deepEqual(changes, ["video"]);
});

test("understanding video tab reflects ffmpeg readiness and compression budget metrics", () => {
  const ready = harness();
  ready.page.updateReplyStatus({
    settings: {
      video_understanding_enabled: true,
      video_understanding_target_mb: 10,
      video_understanding_max_duration_seconds: 120,
      video_understanding_oversize_action: "compress",
      video_understanding_quoted_enabled: true,
      video_understanding_qa_enabled: true,
      video_understanding_max_chars: 2400,
      video_understanding_wait_seconds: 45,
    },
    video: {ffmpeg_available: true, ffprobe_available: true},
  });
  ready.page.mount(ready.root, {initialTab: "video"});
  assert.match(ready.stats[2].innerHTML, /已就绪/);
  assert.match(ready.stats[2].innerHTML, /10 MB · 120 秒/);
  assert.match(ready.stats[2].innerHTML, /超限：压缩/);
  assert.match(ready.stats[2].innerHTML, /含引用视频 · 定向问答/);
  ready.page.unmount();

  const missing = harness();
  missing.page.updateReplyStatus({
    settings: {video_understanding_enabled: true, video_understanding_oversize_action: "reject"},
    video: {ffmpeg_available: false, ffprobe_available: false},
  });
  missing.page.mount(missing.root, {initialTab: "video"});
  assert.match(missing.stats[2].innerHTML, /未检测到/);
  assert.match(missing.stats[2].innerHTML, /超限：拒收/);
  missing.page.unmount();
});
