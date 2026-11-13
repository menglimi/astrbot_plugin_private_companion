/* 入场序列的行为测试。
 *
 * 这套断言锁定用户真正能看到的时序：首帧先在小屋中央近景，
 * 相机与容器沿同一条缓动曲线拉回默认机位，最后才交给 UI 入场动画。
 * 同时保留布局完整性、可跳过、降级和几何落点等护栏，避免修动效时把
 * 场景或首屏闸门重新带回不可用状态。
 *
 * 用 node --test 运行。entrance.js 依赖 window 与 document，
 * 所以这里给一个最小 DOM 替身，不用 jsdom。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "pages", "陪伴小屋", "entrance.js"), "utf8");

/* 包里真实存在的七个机位。fixture 的家具 id 必须落在这个集合里，
   否则飞入目标永远是 overview，测试就测不到真正想测的东西。 */
const STATIONS = ["calendar", "desk", "shelf", "wardrobe", "bed", "radio", "garden"];

/* 最小 DOM 替身：只需要 createElement / appendChild / remove /
   addEventListener / removeEventListener，以及 matchMedia。
   窗口级监听（resize）单独记，因为收尾时要求摘干净。 */
function stubDom({
  reducedMotion = false,
  viewport = {width: 1320, height: 760},
  /* 小屋容器的静息矩形。默认值取自真实布局：
     左栏 232 + 内边距 22 + 右栏 296 + 间距 20，高由 min-height:380px 兜住。
     刻意不等于视口——若两者相同，cover 框与静息框重合，
     stageGeometry 会退化成 scale=1，就测不出缩放有没有生效。 */
  rect = {left: 254, top: 212, width: 728, height: 380},
} = {}) {
  const listeners = new Map();
  const windowListeners = new Map();
  const makeNode = () => {
    const node = {
      children: [],
      classes: new Set(),
      attributes: {},
      /* style 要支持 setProperty / removeProperty / 直接赋值（clipPath）。 */
      style: {
        values: {},
        setProperty(name, value) { this.values[name] = value; },
        removeProperty(name) { delete this.values[name]; },
      },
      classList: {
        _classes: null,
        add(...names) { names.forEach(name => this._classes.add(name)); },
        remove(...names) { names.forEach(name => this._classes.delete(name)); },
        contains(name) { return this._classes.has(name); },
      },
      setAttribute(key, value) { this.attributes[key] = value; },
      appendChild(child) { this.children.push(child); return child; },
      remove() { this.removed = true; },
      /* 入场要用它量静息矩形——没有它 stageGeometry 会返回 null。 */
      getBoundingClientRect() { return rect; },
      addEventListener(type, handler) {
        if (!listeners.has(type)) listeners.set(type, new Set());
        listeners.get(type).add(handler);
      },
      removeEventListener(type, handler) { listeners.get(type)?.delete(handler); },
      get listenerCount() {
        return [...listeners.values()].reduce((sum, set) => sum + set.size, 0);
      },
    };
    node.classList._classes = node.classes;
    return node;
  };
  const host = makeNode();
  /* 遥测每帧采样相机，所以要给 requestAnimationFrame 与 performance。
     测试里不需要真的按帧跑，用 setTimeout 近似即可——采样失败不影响序列逻辑，
     但缺了这两个 API 会直接在启动时抛错。 */
  const frames = [];
  let clock = 0;
  const context = vm.createContext({
    window: {
      matchMedia: () => ({matches: reducedMotion}),
      innerWidth: viewport.width,
      innerHeight: viewport.height,
      addEventListener(type, handler) {
        if (!windowListeners.has(type)) windowListeners.set(type, new Set());
        windowListeners.get(type).add(handler);
      },
      removeEventListener(type, handler) { windowListeners.get(type)?.delete(handler); },
    },
    document: {
      body: makeNode(),
      /* entrance.js 把算出来的 --entrance-ui-at 写到根元素上，
         所以替身里也得有它，否则那条赋值会被静默跳过、测试测了个空。 */
      documentElement: makeNode(),
      createElement: makeNode,
      addEventListener: (...args) => host.addEventListener(...args),
      removeEventListener: (...args) => host.removeEventListener(...args),
    },
    EventTarget,
    Event,
    console,
    setTimeout,
    clearTimeout,
    Promise,
    performance: {now: () => (clock += 16)},
    requestAnimationFrame: callback => {
      const timer = setTimeout(() => callback(clock), 16);
      frames.push(timer);
      return timer;
    },
    cancelAnimationFrame: timer => clearTimeout(timer),
  });
  vm.runInContext(SOURCE, context);
  /* `document` 也一并返回：--entrance-ui-at 写在它上面，断言要读。 */
  return {entrance: context.window.CompanionEntrance, host, listeners, windowListeners,
    makeNode, frames, viewport, document: context.document};
}

/* 假体素控制器。setLayout 按真实语义处理：stored:true 的条目不可见，
   其余可见，缺的 id 落到默认布局（这里默认布局就是全量，所以缺 id 等于
   仍然可见——这正是坑一的语义，夹具必须照着实现才测得出问题）。 */
function stubController({throwOn = null} = {}) {
  const calls = [];
  const DEFAULT = {
    version: 1,
    houseStyle: "classic",
    items: {
      rug: {x: 0, y: 0, z: 0, rotation: 0, style: "oak", model: "rug", surface: "floor"},
      desk: {x: 1, y: 0, z: 1, rotation: 0, style: "oak", model: "desk", surface: "floor"},
      wardrobe: {x: 1.2, y: 0.5, z: -1, rotation: 0, style: "oak", model: "wardrobe", surface: "floor"},
      shelf: {x: 1, y: 1.8, z: -1, rotation: 0, style: "oak", model: "shelf", surface: "floor"},
      bed: {x: 3, y: 2, z: 0, rotation: 0, style: "oak", model: "bed", surface: "floor"},
      garden: {x: 2, y: 3.4, z: 2, rotation: 0, style: "oak", model: "garden", surface: "floor"},
    },
  };
  let current = structuredClone(DEFAULT);
  const visible = layout => Object.entries(layout.items || {})
    .filter(([, item]) => item.stored !== true).map(([id]) => id);
  return {
    calls,
    getCatalog: () => ({furniture: Object.fromEntries(Object.keys(DEFAULT.items).map(id => [id, {}]))}),
    getLayout: () => structuredClone(current),
    setLayout(next) {
      if (throwOn === "setLayout") throw new Error("布局设置失败");
      current = structuredClone(next);
      calls.push({type: "setLayout", visible: visible(next), total: Object.keys(next.items || {}).length});
    },
    setLight(name) { calls.push({type: "setLight", name}); },
    focus(id) { calls.push({type: "focus", id}); },
    setEntranceProgress(value) { calls.push({type: "entranceProgress", value}); },
    setConstructionProgress(value) { calls.push({type: "constructionProgress", value}); },
    getState: () => ({camera: {position: [13.5, 12.8, 16.2], target: [0, 1.15, 0.5], fov: 38},
      moving: false, tourRunning: false, shotIndex: 0}),
  };
}

test("四个阶段按顺序到达并收在 done", async () => {
  const {entrance, host} = stubDom();
  const phases = [];
  const run = entrance.run(stubController(), host, {onPhase: phase => phases.push(phase)});
  await run.done;
  assert.deepStrictEqual(phases, ["prime", "pull", "settle", "done"]);
});

test("中央近景先于拉远，并与容器共用同一进度", async () => {
  const {entrance} = stubDom();
  assert.ok(entrance.TIMING.prime >= 180 && entrance.TIMING.prime <= 300,
    "近景应同步定位，并给启动幕消退留一小段可见停留");
  assert.ok(entrance.TIMING.pullBack >= 1000,
    `拉远只有 ${entrance.TIMING.pullBack}ms，容器缩放会显得生硬`);
  const reveal = entrance.UI_REVEAL;
  assert.ok(entrance.TIMING.settle >= reveal.pause + reveal.stagger + reveal.duration,
    "定格时间必须覆盖最后一组 UI 的落场");
});

test("首帧同步中央近景，拉远最终写入 1", async () => {
  const {entrance, host} = stubDom();
  const controller = stubController();
  const stage = {
    style: {
      values: {},
      setProperty(name, value) { this.values[name] = value; },
      removeProperty(name) { delete this.values[name]; },
    },
    getBoundingClientRect() { return {left: 254, top: 212, width: 728, height: 380}; },
  };
  const run = entrance.run(controller, host, {
    stage,
    viewport: {width: 1320, height: 760},
  });
  await run.done;
  const progress = controller.calls
    .filter(call => call.type === "entranceProgress")
    .map(call => call.value);
  assert.ok(progress.length >= 2, "入口没有驱动体素相机进度");
  assert.strictEqual(progress[0], 0, "首个相机进度不是中央近景");
  assert.strictEqual(progress.at(-1), 1, "拉远结束没有写入最终进度 1");
  for (let index = 1; index < progress.length; index += 1) {
    assert.ok(progress[index] >= progress[index - 1] - 1e-9,
      `相机进度倒退：${progress[index - 1]} → ${progress[index]}`);
  }
});

test("入口不重建家具，只在开始与定格各写一次布局", async () => {
  const {entrance, host} = stubDom();
  const controller = stubController();
  const run = entrance.run(controller, host, {});
  await run.done;
  const steps = controller.calls.filter(call => call.type === "setLayout");
  assert.strictEqual(steps.length, 2, `入口重建了 ${steps.length} 次布局，应该只保留起始和定格`);
  assert.deepStrictEqual(steps[0].visible, steps.at(-1).visible,
    "起始近景与最终机位的家具集合不一致");
  assert.strictEqual(steps.at(-1).total, 6, "定格时家具布局不完整");
});

test("状态页搭建连续推进建造面，不切换家具布局", async () => {
  const {entrance, host} = stubDom();
  const controller = stubController();
  const run = entrance.run(controller, host, {buildFromBottom: true});
  await run.done;
  const builds = controller.calls.filter(call => call.type === "constructionProgress");
  assert.ok(builds.length >= 5, `只有 ${builds.length} 个建造进度，没有连续搭建`);
  assert.strictEqual(builds[0].value, 0, "建造没有从空场景开始");
  assert.strictEqual(builds.at(-1).value, 1, "建造结束后仍有未完成部分");
  let previous = 0;
  for (const build of builds.slice(1)) {
    assert.ok(build.value >= previous, "建造进度倒退");
    previous = build.value;
  }
  assert.ok(builds.some(build => build.value > 0 && build.value < 1), "没有中间建造帧");
  assert.deepStrictEqual(controller.calls.filter(call => call.type === "setLayout"), [],
    "状态页动画仍在切换家具布局");
});

test("状态页搭建跳过时同步显示完整小屋", async () => {
  const {entrance, host} = stubDom();
  const controller = stubController();
  const run = entrance.run(controller, host, {buildFromBottom: true});
  run.skip();
  await run.done;
  const builds = controller.calls.filter(call => call.type === "constructionProgress");
  assert.deepStrictEqual(builds.map(build => build.value), [0, 1], "跳过时没有同步清除建造裁切");
  assert.deepStrictEqual(controller.calls.filter(call => call.type === "setLayout"), [],
    "跳过时改写了现有家具布局");
});

test("状态页建造不缩放或裁切容器", async () => {
  const {entrance, host, makeNode} = stubDom();
  const stage = makeNode();
  const run = entrance.run(stubController(), host, {
    buildFromBottom: true,
    stage,
    viewport: {width: 1320, height: 760},
  });
  await run.done;
  assert.deepStrictEqual(Object.keys(stage.style.values), [], "状态页建造修改了容器尺寸或裁切样式");
});

test("拉远不通过连续 focus 打断镜头", async () => {
  const {entrance, host} = stubDom();
  const controller = stubController();
  const run = entrance.run(controller, host, {});
  await run.done;
  assert.deepStrictEqual(controller.calls.filter(call => call.type === "focus"), [],
    "拉远不应重复调用 focus，避免打断当前相机进度");
});

test("skip 在序列还在跑的时候就能调用", async () => {
  const {entrance, host} = stubDom();
  const controller = stubController();
  const phases = [];
  const started = Date.now();
  const run = entrance.run(controller, host, {onPhase: phase => phases.push(phase)});
  /* 关键：run 立即返回句柄，不等序列跑完。 */
  assert.strictEqual(typeof run.skip, "function",
    "run() 没有立即返回可用的 skip——调用方只能跳过已经结束的序列");
  await new Promise(resolve => setTimeout(resolve, 40));
  run.skip();
  await run.done;
  const elapsed = Date.now() - started;
  assert.ok(elapsed < 1500, `跳过用了 ${elapsed}ms，没有真正打断`);
  assert.strictEqual(phases.at(-1), "done");
});

test("减少动效偏好时不播放", async () => {
  const {entrance, host} = stubDom({reducedMotion: true});
  const controller = stubController();
  const phases = [];
  const run = entrance.run(controller, host, {onPhase: phase => phases.push(phase)});
  await run.done;
  assert.strictEqual(run.skipped, true);
  assert.deepStrictEqual(phases, ["done"]);
  assert.strictEqual(controller.calls.length, 0, "减少动效时不该碰场景");
});

test("中途抛错也收在终态", async () => {
  const {entrance, host} = stubDom();
  const controller = stubController({throwOn: "setLayout"});
  const phases = [];
  const run = entrance.run(controller, host, {onPhase: phase => phases.push(phase)});
  await run.done;
  assert.strictEqual(phases.at(-1), "done", "异常后没有到达 done，页面会卡住");
});

test("异常且控制器不可用时也不留残缺", async () => {
  /* 异常发生在控制器已经不可用的场景里（切页销毁）。此时不该再去补布局，
     但必须收在 done——否则页面永远停在小屋为空的状态。 */
  const {entrance, host} = stubDom();
  let disposed = false;
  const controller = stubController();
  const guarded = {
    ...controller,
    setLayout(next) { if (disposed) throw new Error("场景已销毁"); return controller.setLayout(next); },
  };
  const phases = [];
  const run = entrance.run(guarded, host, {onPhase: phase => phases.push(phase)});
  await new Promise(resolve => setTimeout(resolve, 30));
  disposed = true;
  await run.done;
  assert.strictEqual(phases.at(-1), "done");
});

test("收尾摘掉监听，不留累积", async () => {
  const {entrance, host} = stubDom();
  const before = host.listenerCount;
  const run = entrance.run(stubController(), host, {});
  await run.done;
  assert.strictEqual(host.listenerCount, before,
    `收尾后还有 ${host.listenerCount - before} 个监听没摘掉`);
});

test("控制器缺失时直接 done，不抛错", async () => {
  const {entrance, host} = stubDom();
  const phases = [];
  const run = entrance.run(null, host, {onPhase: phase => phases.push(phase)});
  await run.done;
  assert.strictEqual(run.skipped, true);
  assert.deepStrictEqual(phases, ["done"]);
});

test("中途 skip 后不再触碰控制器", async () => {
  /* app.js 在切页时会先 skip 再 dispose 控制器。若 skip 之后序列还继续
     调用 focus/setLayout，就是对已销毁的场景操作——所以必须验证
     skip 之后调用就停了，而不是「很快会停」。 */
  const {entrance, host} = stubDom();
  let disposed = false;
  const controller = stubController();
  const violations = [];
  const guarded = {
    ...controller,
    setLayout(next) { if (disposed) violations.push("setLayout"); return controller.setLayout(next); },
    focus(id) { if (disposed) violations.push("focus"); return controller.focus(id); },
    setLight(name) { if (disposed) violations.push("setLight"); return controller.setLight(name); },
  };
  const run = entrance.run(guarded, host, {});
  await new Promise(resolve => setTimeout(resolve, 30));
  run.skip();
  disposed = true;
  await run.done;
  assert.deepStrictEqual(violations, [],
    `skip 之后仍在调用已销毁的控制器：${violations.join(", ")}`);
});

test("跳过之后定时器不会再改动场景", async () => {
  /* 拉远段由 rAF 驱动。跳过时必须把它停掉，否则切页之后还会去写
     已销毁场景的相机进度。 */
  const {entrance, host} = stubDom();
  let disposed = false;
  const controller = stubController();
  const violations = [];
  const guarded = {
    ...controller,
    setLayout(next) { if (disposed) violations.push("setLayout"); return controller.setLayout(next); },
    setEntranceProgress(value) {
      if (disposed) violations.push("setEntranceProgress");
      return controller.setEntranceProgress(value);
    },
  };
  const stage = {
    style: {
      values: {},
      setProperty(name, value) { this.values[name] = value; },
      removeProperty(name) { delete this.values[name]; },
    },
    getBoundingClientRect() { return {left: 254, top: 212, width: 728, height: 380}; },
  };
  const run = entrance.run(guarded, host, {
    stage,
    viewport: {width: 1320, height: 760},
  });
  /* 在拉远仍未结束时跳过，才能测到残留 rAF。 */
  await new Promise(resolve => setTimeout(resolve, 780));
  run.skip();
  disposed = true;
  await run.done;
  await new Promise(resolve => setTimeout(resolve, 1400));
  assert.deepStrictEqual(violations, [],
    `跳过之后定时器仍在写布局：${violations.join(", ")}`);
});

test("机位清单与体素包一致", async () => {
  /* 我列的机位若有一个包里不存在，飞入那段就永远回落成全景。
     反过来漏掉一个也不会报错，只是少一个候选。所以直接对表。 */
  const bundle = fs.readFileSync(
    path.join(__dirname, "..", "pages", "陪伴小屋", "assets", "voxel-room.js"), "utf8");
  const from = bundle.indexOf("window.PrivateCompanionHomeRoom3D");
  /* 注意是 slice(from, from + 6000)。写成 slice(from, 6000) 会取到空串
     （起点比终点大），于是「解析出 0 个机位」——这条断言会以
     「体素包机位表解析失败」的面目失败，而不是安静地放过。 */
  const head = bundle.slice(from, from + 6000);
  const declared = new Set([...head.matchAll(
    /(\w+):\{label:"[^"]*",target:\[[^\]]*\],position:\[[^\]]*\],fov:\d+\}/g
  )].map(match => match[1]));
  assert.ok(declared.size >= 5, `体素包机位表只解析出 ${declared.size} 个，解析式可能失效了`);
  const mine = new Set([...SOURCE.matchAll(/const STATIONS = \[([^\]]*)\]/g)]
    .flatMap(match => [...match[1].matchAll(/"([^"]+)"/g)].map(item => item[1])));
  const wrong = [...mine].filter(id => !declared.has(id));
  assert.deepStrictEqual(wrong, [],
    `entrance.js 里列了包里不存在的机位：${wrong.join(", ")}——飞入会静默回落成全景`);
});

/* === 全屏容器：几何 ===
 *
 * 这一组锁的是「容器从满屏缩到最终尺寸」的数学。它极易写对一半——
 * 落点差一整个容器尺寸在代码里看不出来（公式看着都像对的），
 * 只有数值断言能发现。这里对四个视口尺寸分别验算：
 * progress=0 必须盖住视口，progress=1 必须精确落在静息矩形上。
 *
 * 复现 CSS 的定位：容器 fixed + left:50% + bottom:0 + translateX(-50%)，
 * 自然尺寸是 cover 框，变换原点是它的底边中点。
 */
function landedRect(geometry, progress) {
  const scale = 1 + (geometry.scale - 1) * progress;
  const tx = geometry.translateX * progress;
  const ty = geometry.translateY * progress;
  const W = geometry.viewport.width;
  const H = geometry.viewport.height;
  const w = geometry.cover.width;
  const h = geometry.cover.height;
  /* 未变换时的左上角。 */
  const x0 = W / 2 - w / 2;
  const y0 = H - h;
  /* 原点 = 未变换矩形的底边中点。 */
  const ox = x0 + w / 2;
  const oy = y0 + h;
  return {
    left: ox + (x0 - ox) * scale + tx,
    top: oy + (y0 - oy) * scale + ty,
    width: w * scale,
    height: h * scale,
  };
}

test("开屏时容器盖住整个视口", () => {
  const {entrance, viewport} = stubDom();
  const geometry = entrance.stageGeometry(
    {getBoundingClientRect: () => ({left: 254, top: 212, width: 728, height: 380})}, viewport);
  const start = landedRect(geometry, 0);
  assert.ok(start.left <= 0.5 && start.top <= 0.5,
    `开屏时左上角在视口内（${start.left.toFixed(1)}, ${start.top.toFixed(1)}），没盖满`);
  assert.ok(start.width >= viewport.width - 0.5 && start.height >= viewport.height - 0.5,
    `开屏时尺寸 ${start.width.toFixed(0)}x${start.height.toFixed(0)} 小于视口`);
});

test("收尾时容器精确落在静息矩形上", () => {
  /* 这条是最要紧的：落点若差一点，摘掉行内样式交回栅格时小屋会跳一下。
     坑：translate 曾乘上 scale 因子，落点偏了一整个容器尺寸——
     公式看着仍像对的，只有算出来才发现。 */
  const cases = [
    {viewport: {width: 1320, height: 760}, rect: {left: 254, top: 212, width: 728, height: 380}},
    {viewport: {width: 1920, height: 1080}, rect: {left: 254, top: 260, width: 1348, height: 497}},
    {viewport: {width: 1440, height: 900}, rect: {left: 254, top: 236, width: 868, height: 414}},
    {viewport: {width: 900, height: 600}, rect: {left: 22, top: 150, width: 856, height: 380}},
  ];
  for (const item of cases) {
    const {entrance} = stubDom({viewport: item.viewport, rect: item.rect});
    const geometry = entrance.stageGeometry({getBoundingClientRect: () => item.rect}, item.viewport);
    assert.ok(geometry, `${item.viewport.width}x${item.viewport.height} 量不出几何`);
    const end = landedRect(geometry, 1);
    const tolerance = 0.01;
    assert.ok(Math.abs(end.left - item.rect.left) < tolerance
      && Math.abs(end.top - item.rect.top) < tolerance
      && Math.abs(end.width - item.rect.width) < tolerance
      && Math.abs(end.height - item.rect.height) < tolerance,
      `${item.viewport.width}x${item.viewport.height} 落点不准：`
      + `实际 (${end.left.toFixed(2)}, ${end.top.toFixed(2)}) ${end.width.toFixed(2)}x${end.height.toFixed(2)}，`
      + `期望 (${item.rect.left}, ${item.rect.top}) ${item.rect.width}x${item.rect.height}`);
  }
});

test("cover 框与静息同比例，不会拉伸房间", () => {
  /* 静息框与视口宽高比不同（约 1.92 : 1.74）。若直接按视口铺满再缩放，
     3D 场景会被压扁。cover 框保证全程只有一个等比缩放。 */
  const rect = {left: 254, top: 212, width: 728, height: 380};
  const {entrance} = stubDom({rect});
  const geometry = entrance.stageGeometry({getBoundingClientRect: () => rect},
    {width: 1320, height: 760});
  const coverRatio = geometry.cover.width / geometry.cover.height;
  const restRatio = rect.width / rect.height;
  assert.ok(Math.abs(coverRatio - restRatio) < 1e-6,
    `cover 比例 ${coverRatio.toFixed(4)} 与静息比例 ${restRatio.toFixed(4)} 不一致，画面会拉伸`);
});

test("容器曲线起手轻、收尾缓，且精确落在 1", () => {
  /* 用户要的是「逐渐缩小」。早先的曲线最大斜率在 t=0.01——
     第一帧就走完一半，观感是「啪」地缩一下再慢慢爬。 */
  const {entrance} = stubDom();
  assert.strictEqual(entrance.pullCurve(1), 1, "收尾不是精确的 1，撤掉变换时会跳一下");
  assert.strictEqual(entrance.pullCurve(0), 0);
  const early = entrance.pullCurve(0.1);
  assert.ok(early < 0.15, `起手太猛：t=0.10 已经走了 ${(early * 100).toFixed(1)}%`);
  /* 最大斜率应当落在中段，而不是第一帧。 */
  let peak = 0;
  for (let index = 1; index < 200; index += 1) {
    const t = index / 200;
    const slope = (entrance.pullCurve(t) - entrance.pullCurve(t - 0.005)) / 0.005;
    if (slope > peak) peak = t;
  }
  assert.ok(peak > 0.12, `最大推进落在 t=${peak.toFixed(2)}，起手过猛`);
  /* 单调：中途不能倒退。 */
  let previous = -1;
  for (let index = 0; index <= 500; index += 1) {
    const value = entrance.pullCurve(index / 500);
    assert.ok(value >= previous - 1e-9, `曲线在 t=${(index / 500).toFixed(2)} 处倒退`);
    previous = value;
  }
});

test("UI 从实际落位开始，留出缓冲后再浮现", () => {
  /* 用户要的顺序是「镜头拉远到默认机位，随后其他 UI 才浮现」。
     若 UI 在 0ms 就开始淡入，小屋还在全屏时左栏已经浮现，
     读起来像页面在组装，而不是小屋缩进页面。

      这里踩过两次：先是 CSS 写 1920ms 而序列实际要 3720ms；后来改成
      「pullBack + settle」也仍是错的——那两段不是从序列开头算起的。
      所以真值改成由 entrance.js 按 TIMING 现算并写到根元素，
      CSS 里那份只是脚本没跑时的缺省值。 */
  const css = fs.readFileSync(
    path.join(__dirname, "..", "pages", "陪伴小屋", "entrance.css"), "utf8");
  const declared = css.match(/--entrance-ui-pause\s*,\s*(\d+)ms/);
  assert.ok(declared, "UI 没有落位缓冲");
  const {entrance} = stubDom();
  const expected = entrance.TIMING.prime + entrance.TIMING.pullBack + entrance.UI_REVEAL.pause;
  assert.strictEqual(entrance.UI_AT, expected,
    `UI_AT 是 ${entrance.UI_AT}ms，而拉远走完在 ${expected}ms——UI 会在拉远途中冒出来`);
  assert.strictEqual(Number(declared[1]), entrance.UI_REVEAL.pause);
  assert.match(css, /\.is-entrance\.entrance-reveal\s+\.rail/,
    "UI 动画必须等实际落位回调添加 entrance-reveal 后才开始");
});

test("run() 把算出的浮现时刻写到根元素上", async () => {
  /* CSS 里的数字只是缺省值。真值要由 JS 覆盖，否则改了 TIMING 而忘了改
     CSS，UI 就会在错误的一刻出现——而且不会报错，只是观感不对。 */
  const {entrance, host, document: doc} = stubDom();
  const run = entrance.run(stubController(), host, {});
  const written = doc.documentElement.style.values["--entrance-ui-at"];
  assert.ok(written, "run() 没有写 --entrance-ui-at，UI 浮现时刻只用 CSS 缺省值");
  assert.strictEqual(written, `${entrance.UI_AT}ms`);
  await run.done;
});

test("小屋那一格不参与 UI 淡入", () => {
  /* 入场的主角是全屏的小屋。把它和左栏一起压成 opacity:0，整整 4 秒
     屏幕上就只剩一层波纹——「全屏展示小屋」根本没发生。
     它靠 clip-path 边际线与场景亮度自己出场。 */
  const css = fs.readFileSync(
    path.join(__dirname, "..", "pages", "陪伴小屋", "entrance.css"), "utf8");
  const group = css.match(/\.is-entrance\s+\.rail,[\s\S]*?\{([^}]*)\}/);
  assert.ok(group, "entrance.css 里找不到 UI 浮现那一组选择器");
  assert.doesNotMatch(group[0], /\.is-entrance\s+\.stage-region\s*[,{]/,
    "小屋那一格被放进淡入组了：入场期间它不可见，全屏开场等于没播");
  /* 但小屋自己的操作面（速览条、重置/布置）要跟着 UI 一起出现。 */
  assert.match(css, /\.is-entrance\s+\.stage-region\s+\.stage-furniture-strip/,
    "家具速览条没有跟着 UI 浮现，会在开场时就挂在全屏小屋下面");
});

test("启动幕在首屏闸门关闭期间可见", () => {
  /* 体素包 677 KB，加载期间 body 是 visibility:hidden。启动幕靠自己的
     visibility:visible 顶掉继承来的 hidden——少了这一行，它和页面一起
     消失，那段等待又变回纯白屏。 */
  const css = fs.readFileSync(
    path.join(__dirname, "..", "pages", "陪伴小屋", "entrance.css"), "utf8");
  const boot = css.match(/\.room-boot\{([\s\S]*?)\}/);
  assert.ok(boot, "entrance.css 里没有 .room-boot");
  assert.match(boot[1], /visibility\s*:\s*visible/,
    "启动幕没有改回 visibility:visible，会跟着被隐藏的 body 一起不见");
  assert.match(css, /:root\[data-room-ready\]\s+\.room-boot/,
    "小屋就绪后没有收起启动幕，它会一直盖在页面上");
  /* 它是静态写在 HTML 里的：由脚本补的话，它自己也要等脚本，等于没补。 */
  assert.match(INDEX, /class="room-boot"/,
    "启动幕没有写死在 index.html 里，加载慢时仍是一屏空白");
});

test("启动幕随机预载一张单次动图并遵从减少动态效果", () => {
  const assets = [
    "pages/陪伴小屋/assets/room-loading-tea-coffee.webp",
    "pages/陪伴小屋/assets/room-loading-mug.webp",
  ];
  for (const asset of assets) {
    const bytes = fs.readFileSync(path.join(__dirname, "..", asset));
    assert.ok(bytes.length > 100_000, `${asset} 没有随包提供`);
    assert.equal(bytes.subarray(8, 12).toString("ascii"), "WEBP",
      `${asset} 不是有效的 WebP 动图`);
  }
  assert.match(INDEX, /Math\.floor\(Math\.random\(\)\s*\*\s*choices\.length\)/,
    "启动幕没有在两张动图之间随机选择");
  assert.match(INDEX, /<template[^>]+data-room-boot-gif="assets\/room-loading-tea-coffee\.webp[^>]*>[\s\S]*?<img[^>]+src="assets\/room-loading-tea-coffee\.webp/,
    "茶咖啡杯没有以宿主可重写的静态 src 放进 inert template");
  assert.match(INDEX, /<template[^>]+data-room-boot-gif="assets\/room-loading-mug\.webp[^>]*>[\s\S]*?<img[^>]+src="assets\/room-loading-mug\.webp/,
    "马克杯没有以宿主可重写的静态 src 放进 inert template");
  assert.match(INDEX, /preload\.rel\s*=\s*"preload"[\s\S]*?preload\.as\s*=\s*"image"[\s\S]*?preload\.href\s*=\s*candidate\.src[\s\S]*?fetchpriority[\s\S]*?document\.head\.appendChild\(preload\)/,
    "启动图没有用宿主已重写的最终地址低优先级预载");
  assert.match(INDEX, /root\.dataset\.motion==="low"\|\|root\.dataset\.motion==="off"/,
    "小屋减少动态效果设置没有跳过动图");
  assert.match(INDEX, /prefers-reduced-motion: reduce/,
    "系统减少动态效果偏好没有跳过动图");
  assert.match(INDEX, /image\.replaceWith\(clone\)/,
    "随机选中的动图没有从 inert template 挂入静态启动幕");
});

test("全屏阶段不交互，标题与按钮仍保留布局空间", () => {
  /* 开场时用户不该能拖动旋转场景；同时跳过要挂到 document 上，
     否则事件被 pointer-events:none 吃掉，点击跳过用不了。 */
  const css = fs.readFileSync(
    path.join(__dirname, "..", "pages", "陪伴小屋", "entrance.css"), "utf8");
  assert.ok(/\.is-entrance\s+\.stage-region\s+\.voxel-room\s*\{[^}]*pointer-events\s*:\s*none/
    .test(css), "全屏阶段没有关掉指针事件，开场会被拖动打断");
  assert.match(css, /\.is-entrance\s+\.stage-region\s+\.stage-role,[\s\S]*?opacity\s*:\s*0/,
    "场景内标题应当用透明度收起，不能改变场景布局");
  assert.doesNotMatch(css, /display\s*:\s*none/,
    "入口样式不能拿走标题、家具条的布局空间");
});

test("跳过监听挂在 document 上，且收尾摘干净", async () => {
  const {entrance, host, windowListeners} = stubDom();
  const before = host.listenerCount;
  const run = entrance.run(stubController(), host, {});
  /* 关键：pointerdown 必须能跳过。挂在 host 上时，
     pointer-events:none 会让事件到不了，跳过就失效。 */
  const pointer = host.listeners?.get?.("pointerdown");
  await new Promise(resolve => setTimeout(resolve, 30));
  run.skip();
  await run.done;
  assert.strictEqual(host.listenerCount, before,
    `收尾后还有 ${host.listenerCount - before} 个监听没摘掉`);
  const resizeLeft = [...(windowListeners.get("resize") || [])].length;
  assert.strictEqual(resizeLeft, 0, "窗口 resize 监听没有摘掉，会累积");
});

test("没有 stage 时序列照常跑完", async () => {
  /* app.js 在小屋尺寸不可测时会略过入场；直接用 run 时也可能不传 stage。
     这条保证「不传 stage」不会让序列崩掉。 */
  const {entrance, host} = stubDom();
  const controller = stubController();
  const phases = [];
  const run = entrance.run(controller, host, {onPhase: phase => phases.push(phase)});
  await run.done;
  assert.strictEqual(phases.at(-1), "done");
  assert.ok(controller.calls.some(call => call.type === "setLight"), "没有 stage 时仍应完成场景定格");
});

test("带 stage 时收尾清掉全部行内覆盖", async () => {
  /* 漏一个变量，小屋会被永久钉在全屏尺寸，或者留一条裁切线。 */
  const {entrance, host} = stubDom();
  const node = {
    style: {
      values: {},
      setProperty(name, value) { this.values[name] = value; },
      removeProperty(name) { delete this.values[name]; },
    },
    /* stageGeometry 要量矩形，没有它序列会在启动时抛错。 */
    getBoundingClientRect() { return {left: 254, top: 212, width: 728, height: 380}; },
  };
  const run = entrance.run(stubController(), host, {
    stage: node,
    viewport: {width: 1320, height: 760},
  });
  await run.done;
  const left = Object.keys(node.style.values);
  assert.deepStrictEqual(left, [],
    `收尾后容器上还留着行内覆盖：${left.join(", ")}`);
});

test("cover 保留小数比例，跳过先通知页面撤定位再清变量", async () => {
  const {entrance, host, makeNode} = stubDom();
  const stage = makeNode();
  const rect = {left: 123.25, top: 57.5, width: 721.3, height: 417.7};
  stage.getBoundingClientRect = () => rect;
  const geometry = entrance.stageGeometry(stage, {width: 1320, height: 760});
  let notified = false;
  const handle = entrance.run(stubController(), host, {
    stage, geometry,
    onPhase: phase => {
      if (phase === "done") {
        notified = true;
        assert.ok(stage.style.values["--entrance-w"],
          "页面撤掉固定定位之前不能删除 cover 尺寸，否则会回落到满屏缺省值");
      }
    },
  });
  const width = parseFloat(stage.style.values["--entrance-w"]);
  const height = parseFloat(stage.style.values["--entrance-h"]);
  assert.ok(Math.abs(width / height - rect.width / rect.height) < 1e-8,
    "cover 尺寸被取整，首屏和终点比例产生误差");
  handle.skip();
  await handle.done;
  assert.ok(notified);
  assert.deepStrictEqual(Object.keys(stage.style.values), []);
});

test("页面收尾回调抛错仍会清覆盖并完成", async () => {
  const {entrance, host, makeNode} = stubDom({reducedMotion: true});
  const stage = makeNode();
  stage.style.setProperty("--entrance-w", "1320px");
  const handle = entrance.run(stubController(), host, {
    stage, onPhase: () => { throw new Error("模拟页面回调失败"); },
  });
  await handle.done;
  assert.deepStrictEqual(Object.keys(stage.style.values), []);
});


/* === 小屋就绪闸门 ===
 *
 * 用户报的现象：**UI 全部加载好了小屋才出现**。
 *
 * 根因是加载顺序：左栏、顶栏、右栏状态卡、下方面板都是纯 DOM，先就位；
 * 而体素包（assets/voxel-room.js，677 KB、defer）要下载解析完，app.js 才
 * 第一次 mountStage()。中间那几百毫秒里 .room-stage 只是个空盒子。
 *
 * 入场序列本身也没能补上：entrance.css 把 .stage-region 也放进了 UI 浮现组
 * （opacity:0 + 延迟），于是「全屏展示小屋」那一拍小屋自己就是隐形的；而
 * data-room-ready 又只在序列**结束**时才写，整段动画都在不可见的 body 上播完。
 * 结果就是用户看到的：白屏若干秒，然后所有东西（包括小屋卡片）一起冒出来。
 *
 * 现在的契约是三条，缺一条就回到上面那种观感：
 *   1. 闸门存在；放行由**入场启动**触发（app.js 的 startEntrance，小屋一挂载
 *      就放行），不再等它演完。UI 该何时出现交给 entrance.css 的 .is-entrance
 *      那一组，与 --entrance-ui-at（真值由 entrance.js 现算写到根元素）。
 *   2. 小屋那一格**不在** UI 浮现组里——它是入场的主角，全程可见。
 *   3. 体素包加载期间由 .room-boot 启动幕填上，不留白屏；体素脚本的 onload
 *      只记资源本身到了（data-room-asset）；兜底超时覆盖入场根本没启动的情形。
 */
const INDEX = fs.readFileSync(
  path.join(__dirname, "..", "pages", "陪伴小屋", "index.html"), "utf8");
const TOKENS = fs.readFileSync(
  path.join(__dirname, "..", "pages", "陪伴小屋", "tokens.css"), "utf8");

test("首屏在小屋就绪前不显示任何界面", () => {
  assert.match(TOKENS, /:root:not\(\[data-room-ready\]\)\s*body\s*\{\s*visibility:hidden/,
    "tokens.css 缺少小屋就绪闸门：UI 会先画出来，小屋后到");
});

test("体素包自己的 onload 只记资源到了，不直接放行首屏", () => {
  /* 放行太早就是在动画演到一半时亮起。必须写在这一行上是因为 defer 脚本
     按文档顺序执行，换成在末尾统一绑定的话，包可能在监听挂上之前就加载完了
     （缓存命中时几乎必然如此），连「资源到了」都记不到。 */
  const tag = INDEX.match(/<script[^>]*voxel-room\.js[^>]*>/);
  assert.ok(tag, "index.html 里找不到体素包的 script 标签");
  assert.match(tag[0], /onload="[^"]*roomAsset/, "体素包加载完没有记下资源已到");
  assert.match(tag[0], /onerror="[^"]*roomAsset/, "体素包加载失败没有记下资源已到");
  assert.doesNotMatch(tag[0], /roomReady/,
    "体素包加载完就放行首屏，会让小屋在入场演到一半时突然出现");
});

test("入场定格处兜底放行首屏", () => {
  /* 真正在正常路径上放行的已经不是这里，而是 startEntrance()（见下一条）。
     这行保留是幂等兜底：入场启动后若走到异常分支，本函数仍会被调用。 */
  const APP = fs.readFileSync(
    path.join(__dirname, "..", "pages", "陪伴小屋", "app.js"), "utf8");
  const settle = APP.match(/function settleEntrance\(region\)\{([\s\S]*?)\n\}/);
  assert.ok(settle, "app.js 里找不到 settleEntrance()");
  assert.match(settle[1], /dataset\.roomReady/,
    "入场定格后没有放行首屏：整页会一直等到兜底超时才可见");
});

test("入场启动时放行首屏，而不是等它演完", () => {
  /* 这是「UI 全部显示后，小屋对应的卡片才生硬地补齐」的根因护栏。
     data-room-ready 曾经只在 settleEntrance()（序列末）写入，而 tokens.css
     用它把 body 设成 visibility:hidden——整段搭建动画都在不可见的 body 上
     播完，用户看到的是白屏若干秒后所有东西一起出现。 */
  const APP = fs.readFileSync(
    path.join(__dirname, "..", "pages", "陪伴小屋", "app.js"), "utf8");
  const body = APP.match(/function startEntranceRun\(mount,buildFromBottom\)\{([\s\S]*?)\n\}/);
  assert.ok(body, "app.js 里找不到 startEntrance()");
  const marked = body[1].indexOf("classList.add('is-entrance')");
  const runAt = body[1].indexOf("CompanionEntrance.run");
  assert.ok(marked >= 0 && runAt > marked, "startEntrance 里找不到加类与启动序列的顺序");
  /* 先启动序列让 prime 同步把镜头放到中央近景，再由 ready 放行首屏；
     否则首个可见帧仍可能是默认全景。 */
  const ready = body[1].indexOf("handle.ready?.then", runAt);
  const release = body[1].indexOf('dataset.roomReady=""', ready);
  assert.ok(ready > runAt && release > ready,
    "首屏没有等入口 prime 完成后再放行");
});

test("配置深链接不播入场时也放行首屏", () => {
  /* 带 #manage/xxx 打开时小屋一挂上就是缩略条，不播入场，于是没人走到
     settleEntrance —— 少了放行就只能等兜底超时，用户一进页面先白七秒。 */
  const APP = fs.readFileSync(
    path.join(__dirname, "..", "pages", "陪伴小屋", "app.js"), "utf8");
  const call = APP.match(/if\(shell\?\.view==='state'\)scheduleStatusBuild\(mount\);([\s\S]{0,200})/);
  assert.ok(call, "找不到首屏启动调度点");
  assert.match(call[1], /else if\(!shell\?\.section\)startEntrance\(mount,true\);\s*else document\.documentElement\.dataset\.roomReady/,
    "深链接不播入场时没有放行首屏，只能等兜底超时");
});

test("冷启动打开完整小屋时也从底部搭建", () => {
  const APP = fs.readFileSync(
    path.join(__dirname, "..", "pages", "陪伴小屋", "app.js"), "utf8");
  const mount = APP.match(/function mountStage\(\)\{([\s\S]*?)\n\}/);
  assert.ok(mount, "app.js 里找不到 mountStage()");
  const startup = mount[1].match(/queueMicrotask\(\(\)=>\{([\s\S]*?)\n\s+\}\);/);
  assert.ok(startup, "首次挂载没有等待本轮 DOM 更新后再启动入场");
  assert.match(startup[1], /if\(shell\?\.view==='state'\)scheduleStatusBuild\(mount\);/,
    "状态深链接应安排自下而上搭建");
  assert.match(startup[1], /if\(!shell\?\.section\)startEntrance\(mount,true\)/,
    "首次打开完整小屋仍在播放普通容器拉远，没有自下而上搭建");
  assert.match(startup[1], /else document\.documentElement\.dataset\.roomReady=""/,
    "配置分区深链接没有放行首屏");
});

test("状态视图进入时安排搭建，离开时跳过当前动画", () => {
  const APP = fs.readFileSync(
    path.join(__dirname, "..", "pages", "陪伴小屋", "app.js"), "utf8");
  assert.match(APP, /shellView==='state'&&\(!shellViewInitialized\|\|shellRenderedView!=='state'\)\)[\s\S]{0,160}scheduleStatusBuild\(mount\)/,
    "状态视图进入没有安排小屋搭建");
  assert.match(APP, /shellRenderedView==='state'&&shellView!=='state'\)entranceHandle\?\.skip\(\)/,
    "离开状态视图时没有中断搭建");
  assert.match(APP, /buildFromBottom,\s*\/\* 定格后交回当前时段的光照/,
    "状态页启动时没有选择自下而上的搭建模式");
  assert.match(APP, /if\(!buildFromBottom\)document\.body\.classList\.add\('is-entrance-pull'\)/,
    "状态页仍然触发容器全屏缩放");
  assert.match(APP, /stage:buildFromBottom\?null:mount/,
    "状态页仍将舞台容器交给拉远动画");
});

test("入场每个早退路径都放行首屏", () => {
  /* 闸门只能由放行信号打开。入场里每一个「不播了」的 return 之后都不会再有
     人走到 settleEntrance，漏掉一个就是一条「对着空屏等兜底超时」的路径。
     这条把它作为一个整体契约钉住，逐个新增早退时不必再补测试。 */
  const APP = fs.readFileSync(
    path.join(__dirname, "..", "pages", "陪伴小屋", "app.js"), "utf8");
  const body = APP.match(/function startEntranceRun\(mount,buildFromBottom\)\{([\s\S]*?)\n\}/);
  assert.ok(body, "app.js 里找不到 startEntrance()");
  const returns = [...body[1].matchAll(/return;/g)].length;
  const releases = [...body[1].matchAll(/dataset\.roomReady/g)].length;
  assert.ok(returns > 0, "startEntrance 里应当有早退分支");
  assert.ok(releases >= returns,
    `startEntrance 有 ${returns} 处早退、只有 ${releases} 处放行：`
    + "每处早退后面都不会有人走到 settleEntrance，漏一处就是一条"
    + "「对着空屏等兜底超时」的路径");
});

test("体素包迟迟不来时有兜底，页面不会一直白着", () => {
  /* 要给满状态页搭建序列（约 3830ms）再加余量。给短了就是
     「动画没演完 UI 先亮」；给太短还会让体素包慢时整个界面长时间不可见。 */
  const fallback = INDEX.match(/setTimeout\(function\(\)\{\s*document\.documentElement\.dataset\.roomReady=""\s*;?\s*\}\s*,\s*(\d+)\s*\)/);
  assert.ok(fallback, "缺少兜底超时：体素包被拦截或入场没启动时整页永远不可见");
  assert.ok(Number(fallback[1]) >= 5000,
    `兜底 ${fallback[1]}ms 早于入场序列走完，UI 会中途亮起`);
});

test("预览页自己放行，不靠兜底超时", () => {
  /* 预览页没有入场序列（entrance.js 不在内联范围内），没人来放行；
     补在内联引导脚本里，免得看预览要干等七秒白屏。 */
  const preview = path.join(__dirname, "..", "pages", "陪伴小屋", "shell-preview.html");
  if (!fs.existsSync(preview)) return;
  assert.match(fs.readFileSync(preview, "utf8"), /dataset\.roomReady=""/,
    "预览页没有放行首屏，开局会白屏");
});
