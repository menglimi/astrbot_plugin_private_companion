const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

class Element {
  constructor() {
    this.listeners = new Map();
    this.children = [];
    this.dataset = {};
    this.value = "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.open = false;
  }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
  querySelectorAll() { return this.children; }
  setAttribute() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
  click() { this.listeners.get("click")?.({preventDefault() {}}); }
  submit() { this.listeners.get("submit")?.({preventDefault() {}}); }
}

test("life workspace sends with getRandomValues and localizes command errors", async () => {
  const sourcePath = path.join(__dirname, "..", "pages", "日记与生活", "app.js");
  const source = fs.readFileSync(sourcePath, "utf8");
  const ids = new Set([...source.matchAll(/\$\("([^"]+)"\)/g)].map((match) => match[1]));
  const elements = new Map([...ids].map((id) => [id, new Element()]));
  const document = {
    hidden: false,
    getElementById: (id) => elements.get(id) || new Element(),
    createElement: () => new Element(),
    querySelectorAll: () => [],
    addEventListener() {},
  };
  const requests = [];
  const status = {
    role_id: "role-test",
    host_state: "running",
    config: {
      enabled: true, diary_enabled: true, sleep_enabled: true, reading_enabled: false,
      derive_enabled: false, timezone: "UTC", diary_time: "22:00", sleep_time: "23:00",
      sleep_timezone: "UTC", sleep_variation_minutes: 0, sleep_window_minutes: 60,
      sleep_minutes: 480,
    },
    journals: [], activities: [], receipts: [], running: [],
    next_diary_at: null, next_sleep_at: null, next_date: null, next_activity: null,
    view_token: "view-test", edit_token: "edit-test", sleep_schedule: null,
  };
  const bridge = {
    ready: async () => bridge,
    getContext: () => ({display: {role_name: "测试角色"}}),
    apiGet: async () => status,
    apiPost: async (endpoint, body) => {
      requests.push({endpoint, body});
      return {status: "error", data: {code: "life_command_invalid"}};
    },
  };
  const webCrypto = {
    getRandomValues(bytes) {
      bytes.fill(10);
      return bytes;
    },
  };
  const sandbox = {
    window: {crypto: webCrypto, AstrBotPluginPage: bridge},
    document,
    Option: class extends Element {
      constructor(label, value) { super(); this.textContent = label; this.value = value; }
    },
    setTimeout,
    clearTimeout,
  };
  vm.runInNewContext(source, sandbox, {filename: sourcePath});
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  elements.get("start-sleep").click();
  elements.get("editor-form").submit();
  await new Promise(setImmediate);

  assert.equal(requests.length, 1);
  assert.equal(requests[0].endpoint, "life/command");
  assert.match(requests[0].body.operation_key, /^(?:[0-9a-f]{32}|life-[0-9]+-[a-z0-9]+)$/);
  assert.equal(elements.get("notice").textContent, "生活记录或操作信息已变化，请刷新后重新尝试。");
});

test("life workspace mobile compact spacing overrides roomy density in cascade order", () => {
  const cssPath = path.join(__dirname, "..", "pages", "日记与生活", "style.css");
  const css = fs.readFileSync(cssPath, "utf8");
  const densityMainIdx = css.indexOf(":root[data-density] main { padding: var(--pad); }");
  const embeddedRoomyIdx = css.indexOf(':root[data-embedded="true"][data-density="roomy"] main');
  assert.ok(densityMainIdx > 0, "density main rule should exist");
  assert.ok(embeddedRoomyIdx > densityMainIdx, "embedded roomy density rule should follow density main");

  const tail = css.slice(embeddedRoomyIdx);
  const mobileBlockMatch = tail.match(/@media\s*\(max-width:\s*640px\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(mobileBlockMatch, "mobile <=640px media block should appear after density rules");
  const mobileBlock = mobileBlockMatch[1];
  assert.match(
    mobileBlock,
    /:root\[data-density\]\s+main\s*\{\s*padding:\s*14px\s+14px\b/,
    "mobile <=640px must re-assert compact standalone main padding after density rules",
  );
  assert.match(
    mobileBlock,
    /:root\[data-embedded="true"\]\[data-density\]\s+main\s*\{\s*padding:\s*10px\s+12px\s+16px\s*;/,
    "mobile <=640px must re-assert compact embedded main padding after embedded density rules",
  );
  assert.match(
    mobileBlock,
    /:root\[data-density\]\s+:is\(\.activity,\s*\.retry-form\)\s*\{\s*padding:\s*14px\s+15px\s*;/,
    "mobile <=640px must re-assert compact card padding after density rules",
  );
});
