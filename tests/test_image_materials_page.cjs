"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "../pages/陪伴小屋");
const SOURCE = fs.readFileSync(path.join(ROOT, "image-materials-page.js"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "output-page.css"), "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));
const ok = data => ({status: "ok", data});
const emptyOutfit = {local_date: "2026-10-01", state: "empty"};
const image = id => ({
  image_id: id, created_at: `2026-10-01T0${id.slice(-1)}:00:00Z`,
  action: "generate", width: 768, height: 1024,
  thumbnail: "data:image/jpeg;base64,ZmFrZQ==",
});

function inventory({enabled = true, dailyOutfit = enabled, label = "已登记参考图", wardrobe = [], references} = {}) {
  return {
    settings: {
      references: {enabled, daily_outfit_enabled: dailyOutfit},
      wardrobe: {enabled},
    },
    references: references || (label ? [{asset_id: "asset-1", label, roles: ["outfit"], state: "active", mime_type: "image/jpeg", revision: 1}] : []),
    wardrobe,
    daily_outfit: null,
  };
}

function makeNode() {
  const handlers = new Map();
  const submit = {disabled: false};
  const outfit = {
    set innerHTML(html) {
      node.innerHTML = node.innerHTML.replace(/(<section[^>]*data-im-outfit-photo[^>]*>)[\s\S]*?(<\/section>)/,
        (_full, start, end) => start + html + end);
    },
    querySelectorAll() { return []; },
  };
  const matches = (target, selector) => target?.matches?.(selector) === true;
  const targetFor = (selector, dataset = {}, extra = {}) => ({
    ...extra,
    dataset,
    matches: candidate => candidate === selector || (candidate === "input[type=text]" && extra.type === "text"),
    closest: candidate => candidate.split(",").map(value => value.trim()).includes(selector) ? target : null,
  });
  let target;
  const node = {
    isConnected: true,
    innerHTML: "",
    addEventListener(type, handler) { handlers.set(type, handler); },
    removeEventListener(type) { handlers.delete(type); },
    contains() { return true; },
    querySelector(selector) {
      if (selector === "[data-im-outfit-photo]") return outfit;
      return selector === "[data-im-wardrobe-submit]" ? submit : null;
    },
    fire(type, next) { target = next; handlers.get(type)?.({type, target}); },
    click(selector, dataset = {}) { node.fire("click", targetFor(selector, dataset)); },
    input(selector, value) { node.fire("input", targetFor(selector, {}, {value})); },
    change(selector, dataset = {}, extra = {}) { node.fire("change", targetFor(selector, dataset, extra)); },
    submit,
  };
  return node;
}

function harness({role = () => ({image_material_api: "image-materials"}), scope = () => "scope-a", response} = {}) {
  const requests = [];
  const deferred = [];
  const product = {
    scopeKey: scope,
    role,
    relative: path => path,
    bridgeNow: () => bridge,
  };
  const bridge = {
    apiGet(pathname, query) {
      const request = {method: "GET", pathname, query};
      requests.push(request);
      return response ? response(request) : Promise.resolve(ok(pathname.endsWith("/album") ? {items: [], next_cursor: null} : inventory({label: "默认"})));
    },
    apiPost(pathname, body) {
      const request = {method: "POST", pathname, body};
      requests.push(request);
      return response ? response(request) : Promise.resolve(ok({}));
    },
  };
  const context = {
    window: {CompanionProduct: product, crypto: {randomUUID: () => `uuid-${requests.length}`}},
    console, AbortController, setTimeout, clearTimeout, Promise, Math, Date, Error,
    FileReader: class {
      readAsDataURL(file) { this.result = file.dataUrl; setImmediate(() => this.onload?.()); }
    },
    Object, String, Array, Set, JSON,
  };
  vm.runInNewContext(SOURCE, context, {filename: "image-materials-page.js"});
  return {page: context.window.CompanionImageMaterialsPage, node: makeNode(), requests, deferred, product};
}

test("未绑定角色只显示等待绑定，不发素材请求", () => {
  const fixture = harness({role: () => null, scope: () => ""});
  fixture.page.mount(fixture.node);
  assert.match(fixture.node.innerHTML, /等待私聊角色绑定/);
  assert.equal(fixture.requests.length, 0);
});

test("关闭配置时参考图和衣柜控件保持禁用，路由使用插件内相对地址", async () => {
  const fixture = harness({response: request => Promise.resolve(ok(
    request.pathname.endsWith("/album") ? {items: [image("img-1")], next_cursor: null} : inventory({enabled: false}),
  ))});
  fixture.page.mount(fixture.node);
  await tick();
  fixture.node.click("[data-im-select]", {imSelect: "img-1"});
  assert.match(fixture.node.innerHTML, /data-im-register[^>]*disabled/);
  assert.match(fixture.node.innerHTML, /data-im-upload[^>]*disabled/);
  assert.match(fixture.node.innerHTML, /data-im-wardrobe-submit[^>]*disabled/);
  assert.ok(fixture.requests.every(request => !request.pathname.startsWith("/astrbot_plugin_")));
});

test("本地图片可预览后加入当前角色的参考图库", async () => {
  const posted = [];
  const fixture = harness({response: request => {
    if (request.method === "GET" && request.pathname.endsWith("/album")) return Promise.resolve(ok({items: [], next_cursor: null}));
    if (request.method === "GET") return Promise.resolve(ok(inventory({label: ""})));
    posted.push(request);
    return Promise.resolve(ok({asset_id: "asset-uploaded"}));
  }});
  fixture.page.mount(fixture.node);
  await tick();
  const file = {
    name: "春日形象.png", type: "image/png", size: 24,
    dataUrl: "data:image/png;base64,aGVsbG8=",
  };
  fixture.node.change("[data-im-upload-file]", {}, {files: [file]});
  await tick();
  assert.match(fixture.node.innerHTML, /春日形象\.png/);
  fixture.node.click("[data-im-upload]");
  await tick();
  assert.equal(posted[0].pathname, "image-materials/references/upload");
  assert.equal(posted[0].body.label, "春日形象");
  assert.equal(posted[0].body.data_url, file.dataUrl);
  assert.deepEqual([...posted[0].body.roles], ["identity"]);
});

test("图库支持搜索、用途筛选和带修订号的编辑", async () => {
  const posted = [];
  const references = [
    {asset_id: "asset-face", label: "春日形象", roles: ["identity"], state: "active", mime_type: "image/jpeg", revision: 7, thumbnail: "data:image/jpeg;base64,ZmFrZQ=="},
    {asset_id: "asset-outfit", label: "米白穿搭", roles: ["outfit"], state: "active", mime_type: "image/png", revision: 3, thumbnail: "data:image/jpeg;base64,ZmFrZQ=="},
  ];
  const fixture = harness({response: request => {
    if (request.method === "GET" && request.pathname.endsWith("/album")) return Promise.resolve(ok({items: [], next_cursor: null}));
    if (request.method === "GET") return Promise.resolve(ok(inventory({label: "", references})));
    posted.push(request);
    return Promise.resolve(ok({}));
  }});
  fixture.page.mount(fixture.node);
  await tick();
  assert.match(fixture.node.innerHTML, /data-im-reference-card="asset-face"/);
  fixture.node.input("[data-im-search]", "春日");
  assert.match(fixture.node.innerHTML, /data-im-reference-card="asset-face"/);
  assert.doesNotMatch(fixture.node.innerHTML, /data-im-reference-card="asset-outfit"/);
  fixture.node.input("[data-im-search]", "");
  fixture.node.change("[data-im-filter-role]", {}, {value: "outfit"});
  assert.doesNotMatch(fixture.node.innerHTML, /data-im-reference-card="asset-face"/);
  assert.match(fixture.node.innerHTML, /data-im-reference-card="asset-outfit"/);
  fixture.node.change("[data-im-filter-role]", {}, {value: ""});
  fixture.node.click("[data-im-edit-ref]", {imEditRef: "asset-face"});
  fixture.node.input("[data-im-edit-label]", "春日头像");
  fixture.node.change("[data-im-edit-role]", {imEditRole: "pose"}, {checked: true});
  fixture.node.click("[data-im-save-ref]", {imSaveRef: "asset-face"});
  await tick();
  assert.equal(posted[0].pathname, "image-materials/references/update");
  assert.equal(posted[0].body.expected_revision, 7);
  assert.equal(posted[0].body.label, "春日头像");
  assert.deepEqual([...posted[0].body.roles].sort(), ["identity", "pose"]);
});

test("素材适用条件失焦时保留保存按钮，点击后提交最新文本", async () => {
  const posted = [];
  const fixture = harness({response: request => {
    if (request.method === "GET") return Promise.resolve(ok(request.pathname.endsWith("/album")
      ? {items: [], next_cursor: null} : inventory()));
    posted.push(request);
    return Promise.resolve(ok({}));
  }});
  fixture.page.mount(fixture.node);
  await tick();
  fixture.node.click("[data-im-edit-ref]", {imEditRef: "asset-1"});
  const markup = fixture.node.innerHTML;
  fixture.node.change("[data-im-edit-usage]", {imEditUsage: "scenes"}, {
    type: "text", tagName: "INPUT", value: "卧室，居家",
  });
  assert.equal(fixture.node.innerHTML, markup, "blur must not replace the button receiving the click");
  fixture.node.click("[data-im-save-ref]", {imSaveRef: "asset-1"});
  await tick();
  assert.equal(posted.length, 1);
  assert.deepEqual([...posted[0].body.usage.scenes], ["卧室", "居家"]);
});

test("图库排序会提交完整顺序，并在保存后成为新的基线", async () => {
  const posted = [];
  const references = ["asset-a", "asset-b", "asset-c"].map((asset_id, index) => ({
    asset_id, label: `参考图 ${index + 1}`, roles: ["identity"], state: "active",
    mime_type: "image/jpeg", revision: 1,
  }));
  const fixture = harness({response: request => {
    if (request.method === "GET" && request.pathname.endsWith("/album")) return Promise.resolve(ok({items: [], next_cursor: null}));
    if (request.method === "GET") return Promise.resolve(ok(inventory({label: "", references})));
    posted.push(request);
    return Promise.resolve(ok({asset_ids: request.body.asset_ids}));
  }});
  fixture.page.mount(fixture.node);
  await tick();

  fixture.node.click("[data-im-move-ref]", {imMoveRef: "down", imAsset: "asset-a"});
  const moved = fixture.node.innerHTML;
  assert.ok(moved.indexOf('data-im-reference-card="asset-b"') < moved.indexOf('data-im-reference-card="asset-a"'));
  assert.match(moved, /顺序未保存/);
  fixture.node.click("[data-im-save-gallery]");
  await tick();

  assert.equal(posted[0].pathname, "image-materials/references/order");
  assert.deepEqual([...posted[0].body.expected_asset_ids], ["asset-a", "asset-b", "asset-c"]);
  assert.deepEqual([...posted[0].body.asset_ids], ["asset-b", "asset-a", "asset-c"]);
  assert.doesNotMatch(fixture.node.innerHTML, /顺序未保存/);
  assert.match(fixture.node.innerHTML, /data-im-save-gallery disabled/);
});

test("图库并发变化时保留可用排序并基于最新内容重试", async () => {
  const posted = [];
  let orderConflict = true;
  const references = ids => ids.map((asset_id, index) => ({
    asset_id, label: asset_id, roles: ["identity"], state: "active", revision: index + 1,
  }));
  const fixture = harness({response: request => {
    if (request.method === "GET" && request.pathname.endsWith("/album")) return Promise.resolve(ok({items: [], next_cursor: null}));
    if (request.method === "GET") return Promise.resolve(ok(inventory({label: "", references: references(["asset-a", "asset-b"])})));
    posted.push(request);
    if (orderConflict) {
      orderConflict = false;
      return Promise.reject(Object.assign(new Error("reference_order_conflict"), {code: "reference_order_conflict"}));
    }
    return Promise.resolve(ok({asset_ids: request.body.asset_ids}));
  }});
  fixture.page.mount(fixture.node);
  await tick();
  fixture.node.click("[data-im-move-ref]", {imMoveRef: "down", imAsset: "asset-a"});
  fixture.node.click("[data-im-save-gallery]");
  await tick();
  assert.match(fixture.node.innerHTML, /图库内容已更新/);
  assert.match(fixture.node.innerHTML, /顺序未保存/);

  fixture.node.click("[data-im-save-gallery]");
  await tick();
  assert.deepEqual([...posted[1].body.expected_asset_ids], ["asset-a", "asset-b"]);
  assert.deepEqual([...posted[1].body.asset_ids], ["asset-b", "asset-a"]);
  assert.doesNotMatch(fixture.node.innerHTML, /顺序未保存/);
});

test("角色切换后旧请求不能覆盖新角色", async () => {
  let selected = "a";
  const pending = [];
  const fixture = harness({
    role: () => ({image_material_api: "image-materials"}),
    scope: () => selected,
    response: request => new Promise((resolve, reject) => pending.push({request, resolve, reject})),
  });
  fixture.page.mount(fixture.node);
  selected = "b";
  fixture.page.refresh();
  assert.equal(pending.length, 6);
  pending[3].resolve(ok(inventory({label: "新角色"})));
  pending[4].resolve(ok({items: [image("img-new")], next_cursor: null}));
  pending[5].resolve(ok({...emptyOutfit, state: "succeeded", photo: {
    thumbnail: "data:image/jpeg;base64,bmV3", activities: ["新角色的行程"],
  }}));
  await tick();
  pending[0].resolve(ok(inventory({label: "旧角色"})));
  pending[1].resolve(ok({items: [image("img-old")], next_cursor: null}));
  pending[2].resolve(ok({...emptyOutfit, state: "succeeded", photo: {
    thumbnail: "data:image/jpeg;base64,b2xk", activities: ["旧角色的行程"],
  }}));
  await tick();
  assert.match(fixture.node.innerHTML, /新角色/);
  assert.match(fixture.node.innerHTML, /新角色的行程/);
  assert.doesNotMatch(fixture.node.innerHTML, /旧角色/);
  assert.doesNotMatch(fixture.node.innerHTML, /img-old/);
});

test("空衣柜可以生成穿搭图并预览当天的行程和天气", async () => {
  let outfit = emptyOutfit;
  const fixture = harness({response: request => {
    if (request.pathname.endsWith("/album")) return Promise.resolve(ok({items: [], next_cursor: null}));
    if (request.pathname.endsWith("/daily-outfit/photo")) return Promise.resolve(ok(outfit));
    if (request.pathname.endsWith("/daily-outfit/generate") && request.method === "POST") {
      outfit = {...emptyOutfit, state: "succeeded", photo: {
        thumbnail: "data:image/jpeg;base64,ZmFrZQ==",
        activities: ["乘车去展馆", "户外散步"],
        weather: {role_location: "杭州", condition: "小雨", temperature_c: 14},
      }};
      return Promise.resolve(ok({local_date: outfit.local_date, state: "created"}));
    }
    return Promise.resolve(ok(inventory({enabled: false, dailyOutfit: true, label: ""})));
  }});
  fixture.page.mount(fixture.node);
  await tick();
  assert.match(fixture.node.innerHTML, /今天还没有穿搭图/);
  assert.doesNotMatch(fixture.node.innerHTML, /data-im-generate-outfit[^>]*disabled/);
  fixture.node.click("[data-im-generate-outfit]");
  await tick();
  const post = fixture.requests.find(request => request.method === "POST");
  assert.equal(post.pathname, "image-materials/daily-outfit/generate");
  assert.ok(post.body.operation_key);
  assert.match(fixture.node.innerHTML, /alt="今日角色穿搭图"/);
  assert.match(fixture.node.innerHTML, /重新生成/);
  assert.match(fixture.node.innerHTML, /乘车去展馆、户外散步/);
  assert.match(fixture.node.innerHTML, /杭州 · 小雨 · 14°C/);
});

test("相册分页、参考登记、撤回、衣柜和每日穿搭均沿用当前角色路由", async () => {
  const posted = [];
  let albumPage = 0;
  let current = inventory({label: "穿搭参考"});
  current.wardrobe = [{item_id: "item-1", name: "米白针织衫", tags: ["日常"], state: "active", asset_id: "asset-1"}];
  const fixture = harness({response: request => {
    if (request.method === "GET" && request.pathname.endsWith("/album")) {
      albumPage += 1;
      return Promise.resolve(ok(albumPage === 1 ? {items: [image("img-1")], next_cursor: "cursor-1"} : {items: [image("img-2")], next_cursor: null}));
    }
    if (request.method === "GET") return Promise.resolve(ok(current));
    posted.push(request);
    if (request.pathname.endsWith("/references")) current.references = [{asset_id: "asset-1", label: "新参考", roles: ["outfit"], state: "active", mime_type: "image/jpeg"}];
    if (request.pathname.endsWith("/daily-outfit")) current.daily_outfit = {item_id: "item-1", name: "米白针织衫"};
    return Promise.resolve(ok({}));
  }});
  fixture.page.mount(fixture.node);
  await tick();
  assert.match(fixture.node.innerHTML, /读取更早的产出/);
  fixture.node.click("[data-im-load-more]");
  await tick();
  assert.match(fixture.node.innerHTML, /img-2/);
  fixture.node.click("[data-im-select]", {imSelect: "img-1"});
  fixture.node.input("[data-im-reference-label]", "新参考");
  fixture.node.click("[data-im-register]");
  await tick();
  assert.equal(posted[0].pathname, "image-materials/references");
  assert.deepEqual([...posted[0].body.roles], ["identity"]);
  fixture.node.click("[data-im-withdraw-ref]", {imWithdrawRef: "asset-1"});
  await tick();
  fixture.node.input("[data-im-wardrobe-name]", "米白针织衫");
  assert.equal(fixture.node.submit.disabled, false);
  fixture.node.click("[data-im-wardrobe-submit]");
  await tick();
  fixture.node.click("[data-im-daily]", {imDaily: "item-1"});
  await tick();
  assert.deepEqual(posted.map(item => item.pathname), [
    "image-materials/references", "image-materials/references/withdraw",
    "image-materials/wardrobe", "image-materials/daily-outfit",
  ]);
  assert.ok(posted.every(item => item.body == null || item.body.operation_key || item.pathname.endsWith("/withdraw") || item.pathname.endsWith("/daily-outfit")));
});

test("后端错误码翻译成页面提示，窄屏样式收拢为单列", async () => {
  const fixture = harness({response: request => {
    if (request.method === "GET" && request.pathname.endsWith("/album")) return Promise.resolve(ok({items: [image("img-1")], next_cursor: null}));
    if (request.method === "GET") return Promise.resolve(ok(inventory()));
    return Promise.reject(Object.assign(new Error("photo_reference_disabled"), {code: "photo_reference_disabled"}));
  }});
  fixture.page.mount(fixture.node);
  await tick();
  fixture.node.click("[data-im-select]", {imSelect: "img-1"});
  fixture.node.click("[data-im-register]");
  await tick();
  assert.match(fixture.node.innerHTML, /参考图功能当前关闭/);
  assert.match(CSS, /@media\(max-width:1050px\)\{[^}]*\}[\s\S]*?\.image-materials-columns\{grid-template-columns:minmax\(0,1fr\)\}/);
  assert.match(CSS, /@media\(max-width:430px\)/);
});

test("未知服务异常使用操作提示且不回显内部消息", async () => {
  const fixture = harness({response: () => Promise.reject(new Error("internal failure at dashboard:synthetic-admin"))});
  fixture.page.mount(fixture.node);
  await tick();
  assert.doesNotMatch(fixture.node.innerHTML, /internal failure|dashboard:synthetic-admin/);
  assert.match(fixture.node.innerHTML, /未读到|读取|刷新/);
});

test("点击每日穿搭图复用高清 Lightbox 并校验左侧配置卡操作按钮防折行样式", async () => {
  const openedItems = [];
  const outfitPhoto = {
    local_date: "2026-11-22",
    state: "succeeded",
    photo: {
      image_id: "wardrobe-outfit:a1b2c3d4:2026-11-22:deadbeef",
      filename: "20261122-013000-wardrobe-outfit-0123456789ab.png",
      mime_type: "image/png",
      width: 832,
      height: 1216,
      size_bytes: 1_500_000,
      created_at: "2026-11-22T01:30:00Z",
      local_date: "2026-11-22",
      thumbnail: "data:image/jpeg;base64,ZmFrZQ==",
      weather: {role_location: "维也纳", condition: "晴", temperature_c: 3},
      activities: ["午间开指热身"],
    },
  };
  const requests = [];
  const bridge = {
    apiGet(pathname, query) {
      requests.push({method: "GET", pathname, query});
      if (pathname.endsWith("/album")) return Promise.resolve(ok({items: [], next_cursor: null}));
      if (pathname.endsWith("/daily-outfit/photo")) return Promise.resolve(ok(outfitPhoto));
      return Promise.resolve(ok(inventory({enabled: true, dailyOutfit: true})));
    },
    apiPost: () => Promise.resolve(ok({})),
  };
  const context = {
    window: {
      CompanionProduct: {
        scopeKey: () => "scope-a",
        role: () => ({image_material_api: "image-materials"}),
        relative: value => value,
        bridgeNow: () => bridge,
      },
      CompanionOutputPage: {
        openLightbox(item) { openedItems.push(item); return true; },
      },
      crypto: {randomUUID: () => "uuid-outfit"},
    },
    console, AbortController, setTimeout, clearTimeout, Promise, Math, Date, Error,
    Object, String, Array, Set, JSON,
  };
  vm.runInNewContext(SOURCE, context, {filename: "image-materials-page.js"});
  const node = makeNode();
  context.window.CompanionImageMaterialsPage.mount(node);
  await tick();

  assert.match(node.innerHTML, /data-im-open-outfit/);
  assert.match(node.innerHTML, /查看原图/);
  node.click("[data-im-open-outfit]");
  assert.equal(openedItems.length, 1);
  assert.equal(openedItems[0].image_id, outfitPhoto.photo.image_id);
  assert.equal(openedItems[0].filename, outfitPhoto.photo.filename);
  assert.equal(openedItems[0].action, "wardrobe.outfit");
  assert.equal(openedItems[0].local_date, "2026-11-22");
  assert.equal(openedItems[0]._albumEndpoint, "image-materials/album");
  assert.equal(openedItems[0].weather.role_location, "维也纳");

  assert.match(CSS, /#homeShell\[data-away="true"\] \.output-material-settings \.wb-actions\{padding-right:0\}/);
  assert.match(CSS, /\.output-page \.wb-save,\.output-page \.wb-actions \.button\{[^}]*white-space:nowrap/);
  assert.match(CSS, /\.image-materials-outfit-trigger\{[^}]*cursor:zoom-in/);
});

test("衣柜默认隐藏已撤回衣物，点击切换按钮后单独分组展示", async () => {
  const current = inventory({
    wardrobe: [
      {item_id: "item-active", name: "浅灰羊毛大衣", tags: ["秋冬"], state: "active", asset_id: null},
      {item_id: "item-old-1", name: "旧版校服", tags: ["已淘汰"], state: "withdrawn", asset_id: null},
      {item_id: "item-old-2", name: "旧版睡衣", tags: ["居家"], state: "withdrawn", asset_id: null},
    ],
  });
  current.wardrobe_counts = {active: 1, withdrawn: 2};
  const fixture = harness({response: request => {
    if (request.pathname.endsWith("/album")) return Promise.resolve(ok({items: [], next_cursor: null}));
    if (request.pathname.endsWith("/daily-outfit/photo")) return Promise.resolve(ok(emptyOutfit));
    return Promise.resolve(ok(current));
  }});
  fixture.page.mount(fixture.node);
  await tick();

  assert.match(fixture.node.innerHTML, /1 \/ 24 件生效/);
  assert.match(fixture.node.innerHTML, /浅灰羊毛大衣/);
  assert.doesNotMatch(fixture.node.innerHTML, /旧版校服/);
  assert.doesNotMatch(fixture.node.innerHTML, /旧版睡衣/);
  assert.match(fixture.node.innerHTML, /显示已撤回 \(2\)/);

  fixture.node.click("[data-im-toggle-withdrawn-wardrobe]");
  assert.match(fixture.node.innerHTML, /隐藏已撤回/);
  assert.match(fixture.node.innerHTML, /已撤回衣物（2）/);
  assert.match(fixture.node.innerHTML, /旧版校服/);
  assert.match(fixture.node.innerHTML, /旧版睡衣/);

  fixture.node.click("[data-im-toggle-withdrawn-wardrobe]");
  assert.doesNotMatch(fixture.node.innerHTML, /旧版校服/);
});
