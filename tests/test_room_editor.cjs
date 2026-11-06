/* 布置房间（room-editor.js）的挂载点合同。
 *
 * 布置时工作台临时替换小屋右栏的状态与日程；小屋场景留在左侧原位。
 * 这里锁住挂载、恢复和保存语义，不依赖浏览器或 jsdom。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const PAGE = path.join(__dirname, "..", "pages", "陪伴小屋");
const SOURCE = fs.readFileSync(path.join(PAGE, "room-editor.js"), "utf8");

function makeNode(name) {
  const node = {
    name, children: [], parent: null, attributes: {}, dataset: {}, removed: false,
    classList: {
      set: new Set(),
      add(...names) { names.forEach(item => this.set.add(item)); },
      remove(...names) { names.forEach(item => this.set.delete(item)); },
      contains(name) { return this.set.has(name); },
    },
    setAttribute(key, value) { this.attributes[key] = value; },
    removeAttribute(key) { delete this.attributes[key]; },
    append(...children) {
      children.forEach(child => { child.parent = this; this.children.push(child); });
      return children[0];
    },
    remove() {
      this.removed = true;
      const at = this.parent?.children.indexOf(this) ?? -1;
      if (at >= 0) this.parent.children.splice(at, 1);
      this.parent = null;
    },
    querySelector(selector) { return selector === "canvas" ? this.canvas || null : null; },
    focus() { this.focused = true; },
    isConnected: true,
  };
  Object.defineProperty(node, "innerHTML", {
    get() { return this._html || ""; },
    set(value) {
      this._html = value;
      this.children.forEach(child => { child.parent = null; });
      this.children = [];
      this.onHTML?.(value);
    },
  });
  return node;
}

function harness({animationDuration = "0s"} = {}) {
  const body = makeNode("body");
  const main = makeNode("main");
  main.hidden = true;
  const shell = makeNode("homeShell");
  const stageRegion = makeNode("stage-region");
  const stage = makeNode("room-stage");
  const canvas = makeNode("canvas");
  stage.canvas = canvas;
  stageRegion.append(stage);
  shell.append(stageRegion);
  const side = makeNode("shell-side");
  shell.append(side);
  body.append(shell);
  body.append(main);

  let editor = null;
  side.onHTML = html => {
    if (html.includes('id="roomEditor"')) {
      editor = makeNode("roomEditor");
      side.append(editor);
    } else {
      editor = null;
      side.append(makeNode("state-band"), makeNode("schedule-band"));
    }
  };
  side.innerHTML = "<state-band></state-band><schedule-band></schedule-band>";

  const selectors = {
    ".room-stage": stage,
    ".room-stage canvas": canvas,
    "#shellSide": side,
    "#main": main,
    "#roomEditor": () => (editor && !editor.removed ? editor : null),
  };
  const document = {
    body,
    activeElement: null,
    querySelector: selector => (selector === "#main" ? main : null),
  };
  const arrangement = {draft: null};
  const exitTimers = [];
  const sandbox = {
    document,
    window: {
      scrollY: 120,
      getComputedStyle: () => ({animationName: animationDuration === "0s" ? "none" : "room-editor-leave", animationDuration}),
      setTimeout(callback, delay) { exitTimers.push({callback, delay}); return exitTimers.length; },
    },
    escapeHTML: value => String(value ?? ""),
    I: () => "<i></i>",
    icons() {},
    $: selector => {
      const found = selectors[selector];
      return typeof found === "function" ? found() : (found || null);
    },
    get arrangementDraft() { return arrangement.draft; },
    set arrangementDraft(value) { arrangement.draft = value; },
    voxelController: {
      editing: false,
      layout: {items: {desk: {x: 1}}},
      getLayout() { return this.layout; },
      setLayout(layout) { this.layout = layout; },
      getCatalog() {
        return {
          houseStyles: {classic: {label: "原木小屋", materials: "木地板"}, washitsu: {label: "日式和室", materials: "榻榻米"}},
          styles: {oak: "原木日常", cream: "奶油逸居"},
          styleDetails: {oak: {note: "温润木色"}, cream: {note: "柔和浅色"}},
          furniture: {desk: "书桌"},
          objectModels: {desk: {basic: {label: "常规", surfaces: ["floor", "wall"]}}},
          surfaces: {floor: "地面", wall: "墙面"},
        };
      },
      getState() { return {houseStyle: "classic", storedFurniture: []}; },
      updateHouseStyle(value) { this.updatedHouseStyle = value; },
      updateFurniture(value) { this.updatedFurniture = value; },
      selectFurniture(value) { this.selectedFurniture = value; },
      restoreDefaults() { this.defaultsRestored = true; },
      setEditing(value) { this.editing = value; },
      resize() { this.resized = (this.resized || 0) + 1; },
    },
    state: {scene: "day", modules: {}, roomLayouts: {}},
    directory: [],
    sceneTimes: [["day", "白天"], ["night", "夜晚"]],
    roleName: () => "角色",
    layoutKey: () => "role-a",
    closeDetail() {},
    toast() {},
    saved() {},
    syncEnvironment() {},
    renderShellSide() {
      side.setAttribute("aria-label", arrangement.draft ? "布置房间工作台" : "此刻的状态与今日安排");
      if (arrangement.draft) return;
      side.innerHTML = "<state-band></state-band><schedule-band></schedule-band>";
    },
    updateFurnitureDirectory() {},
    console,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox);
  return {
    sandbox, body, main, shell, stage, stageRegion, side, editor: () => editor,
    exitTimers,
    runExitTimers() { exitTimers.splice(0).forEach(timer => timer.callback()); },
  };
}

test("进入布置后工作台替换右栏，场景仍留在原位", () => {
  const {sandbox, body, main, shell, stage, stageRegion, side, editor} = harness();

  sandbox.openRoomEditor();

  const view = editor();
  assert.ok(view, "应当插入一个 #roomEditor");
  assert.equal(side.children.includes(view), true, "工作台应挂在状态与日程所在的右栏");
  assert.equal(body.children.includes(view), false, "工作台不能追加到页面底部");
  assert.equal(stage.parent, stageRegion, "3D 场景应当留在小屋原位");
  assert.equal(shell.children.includes(stageRegion), true);
  assert.equal(body.classList.contains("room-editing"), true);
  assert.equal(side.attributes["aria-label"], "布置房间工作台");
  assert.equal(main.hidden, true, "布置不应改变路由主内容区");

  sandbox.renderShellSide();
  assert.equal(editor(), view, "布置期间右栏状态刷新不能覆盖工作台");
});

test("取消布置后恢复状态与日程，场景和主内容区不变", () => {
  const {sandbox, body, main, shell, stage, stageRegion, side, editor} = harness();

  sandbox.openRoomEditor();
  const view = editor();
  sandbox.finishRoomEditor(false);

  assert.equal(view.removed, true, "退出时应移除工作台");
  assert.equal(body.classList.contains("room-editing"), false);
  assert.equal(side.children.map(item => item.name).join(","), "state-band,schedule-band");
  assert.equal(side.attributes["aria-label"], "此刻的状态与今日安排");
  assert.equal(stage.parent, stageRegion, "场景不需要在退出时搬回");
  assert.equal(shell.children.includes(stageRegion), true);
  assert.equal(main.hidden, true);
});

test("退出时等工作台缓动结束后再恢复状态栏", () => {
  const {sandbox, body, side, editor, exitTimers, runExitTimers} = harness({animationDuration: "240ms"});

  sandbox.openRoomEditor();
  const view = editor();
  sandbox.finishRoomEditor(false);

  assert.equal(view.removed, false, "缓动期间工作台节点应保留");
  assert.equal(body.classList.contains("room-editing"), false);
  assert.equal(body.classList.contains("room-editing-exit"), true);
  assert.equal(exitTimers[0].delay, 240);

  runExitTimers();
  assert.equal(view.removed, true);
  assert.equal(body.classList.contains("room-editing-exit"), false);
  assert.equal(side.children.map(item => item.name).join(","), "state-band,schedule-band");
});

test("保存布置时保留当前布局", () => {
  const {sandbox, side} = harness();

  sandbox.openRoomEditor();
  sandbox.handleRoomEditorChange({hasAttribute: name => name === "data-room-time", value: "night"});
  sandbox.voxelController.layout = {items: {desk: {x: 9}}};
  sandbox.finishRoomEditor(true);

  assert.equal(sandbox.state.roomLayouts["role-a"].items.desk.x, 9);
  assert.equal(sandbox.state.scene, "day", "预览时段不会随家具布局保存");
  assert.equal(sandbox.voxelController.layout.items.desk.x, 9);
  assert.equal(side.children.map(item => item.name).join(","), "state-band,schedule-band");
});

test("布局样式与右栏重绘共同守住工作台位置", () => {
  const css = fs.readFileSync(path.join(PAGE, "styles.css"), "utf8");
  assert.match(css, /\.room-editing \.shell-side\s*\{/);
  assert.doesNotMatch(css, /\.room-editing[^{}]*#homeShell[^{}]*\{\s*display:\s*none/);
  assert.doesNotMatch(css, /\.room-editing[^{}]*#main[^{}]*\{\s*display:\s*none/);

  const shellCSS = fs.readFileSync(path.join(PAGE, "shell.css"), "utf8");
  assert.match(shellCSS, /body\.room-editing \.shell-body\s*\{[^}]*grid-template-columns:/s);
  assert.match(shellCSS, /@keyframes room-editor-enter/);
  assert.match(shellCSS, /@keyframes room-editor-leave/);

  const app = fs.readFileSync(path.join(PAGE, "app.js"), "utf8");
  assert.match(app, /function renderShellSide\(\)\s*\{[^}]*if\(arrangementDraft\)return;/s);

  const html = fs.readFileSync(path.join(PAGE, "index.html"), "utf8");
  assert.match(html, /data-detail="arrange"/);
  /* 只要求带缓存戳，不钉死某一轮的名字：布局改版时版本号本来就会换，
     钉死它会让这条断言在下一次改版时红在一个和「工作台位置」无关的地方。 */
  assert.match(html, /room-editor\.js\?v=/);
  assert.match(html, /app\.js\?v=/);

  /* 样式自 7.0.16 起走合包：index.html 只引用一张由
     tools/sync_framework_page.py 按 css-sources.json 拼出来的 bundle.css，
     styles.css 不再是单独的 link。这条断言原先直接找 styles.css 的 link，
     合包之后它永远找不到——而工作台的布局其实没丢，于是它红在一个和
     「工作台位置」无关的地方。

     合包把「漏引」换了个样子：styles.css 从清单里消失、或者改了规则忘了
     重新构建，页面都会静默少掉工作台的高度与网格。预览构建器按同一份清单
     内联，本地目视也看不出差别。所以三段分开断言，断在哪一段都能指名道姓。 */
  assert.match(html, /bundle\.css\?v=[0-9a-f]{16}/,
    "index.html 没有引用 bundle.css：工作台一张样式表都没加载");
  const cssSources = JSON.parse(fs.readFileSync(path.join(PAGE, "css-sources.json"), "utf8"));
  assert.ok(cssSources.includes("styles.css"),
    "css-sources.json 里没有 styles.css：重新构建后工作台布局会整段消失");
  /* 逐字比对而不是只看「有没有这条选择器」：改了 styles.css 却没重新构建时，
     合包里留着的是旧数值，页面按旧布局排版，只比选择器名是发现不了的。 */
  const heightRule = css.match(/\.room-editing \.shell-side\s*\{[^}]*height:[^;]+;/s);
  assert.ok(heightRule, "styles.css 里找不到 .room-editing .shell-side 的高度规则");
  const bundle = fs.readFileSync(path.join(PAGE, "bundle.css"), "utf8");
  assert.ok(bundle.includes(heightRule[0]),
    "bundle.css 的工作台高度规则与 styles.css 不一致：改完样式请运行 python -X utf8 tools/sync_framework_page.py 重新生成");
});

test("工作台提供 V1 式房屋与家具卡片、数值坐标和方向微调", () => {
  const {sandbox, side} = harness();
  sandbox.openRoomEditor();

  assert.match(side.innerHTML, /房屋样式[\s\S]*data-room-house="classic"[\s\S]*data-room-time/);
  assert.match(side.innerHTML, /data-room-furniture/);
  assert.match(side.innerHTML, /家具样式[\s\S]*data-room-style="oak"/);
  assert.match(side.innerHTML, /data-room-coordinate="x"[\s\S]*data-room-coordinate="z"[\s\S]*data-room-coordinate="rotation"/);
  assert.match(side.innerHTML, /data-room-nudge="up"[\s\S]*data-room-nudge="left"[\s\S]*data-room-nudge="right"[\s\S]*data-room-nudge="down"/);
  assert.match(side.innerHTML, /data-room-rotate="-45"[\s\S]*data-room-rotate="45"/);
  assert.match(side.innerHTML, /data-room-defaults/);
  assert.doesNotMatch(side.innerHTML, /data-room-tab=|data-room-panel=/);
  assert.match(SOURCE, /aria-live="polite"/);
  assert.match(SOURCE, /title="向右旋转 45 度"/);

  const css = fs.readFileSync(path.join(PAGE, "styles.css"), "utf8");
  assert.match(css, /\.room-editing \.shell-side\s*\{[^}]*height:\s*min\(82vh,\s*calc\(min\(54vh,\s*36vw\)\s*\+\s*116px\)\)/s);
  assert.match(css, /\.room-editor-controls\s*\{[^}]*display:\s*flex/s);
  assert.match(css, /\.room-editor-group\s*\{/);
  assert.match(css, /\.room-choice-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2/s);
  assert.match(css, /\.room-coordinate-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3/s);
  assert.match(css, /\.room-editor-nudge[^{}]*:active/);
  assert.match(css, /@container[^{}]*max-width:\s*620px/s);
});

test("房屋卡片、家具样式、数值输入和默认布局连接到控制器", () => {
  const {sandbox} = harness();
  sandbox.openRoomEditor();
  sandbox.handleRoomEditorClick({dataset: {roomHouse: "washitsu"}, hasAttribute: () => false});
  assert.equal(sandbox.voxelController.updatedHouseStyle, "washitsu");

  sandbox.handleRoomEditorClick({dataset: {roomStyle: "cream"}, hasAttribute: () => false});
  assert.equal(sandbox.voxelController.updatedFurniture.style, "cream");

  sandbox.handleRoomEditorChange({dataset: {roomCoordinate: "x"}, value: "1.75", hasAttribute: name => name === "data-room-coordinate"});
  assert.equal(sandbox.voxelController.updatedFurniture.x, 1.75);

  sandbox.handleRoomEditorClick({dataset: {}, hasAttribute: name => name === "data-room-defaults"});
  assert.equal(sandbox.voxelController.defaultsRestored, true);
  assert.ok(sandbox.arrangementDraft, "editing controls must keep the draft open");
});

test("小屋右栏先显示 V2 状态，再显示今日安排", () => {
  const app = fs.readFileSync(path.join(PAGE, "app.js"), "utf8");
  assert.match(app, /host\.innerHTML=homeStateBand\(\)\+homeScheduleBand\(\)/);
  assert.match(app, /function homeStateBand\(\)\{return sideBand\('此刻的状态'/);
  assert.match(app, /function homeScheduleBand\(\)\{return sideBand\('今日安排'/);
  assert.match(app, /data-home-agenda-clock/);
  assert.match(app, /async function refreshHomeAgendaClock\(refresh=false\)/);
  assert.match(app, /function armHomeAgendaClock\(\)[\s\S]*?await refreshHomeAgendaClock\(true\);[\s\S]*?armHomeAgendaClock\(\);[\s\S]*?\},15000\)/);
  assert.match(app, /aria-label',arrangementDraft\?'布置房间工作台':'此刻的状态与今日安排'/);
});
