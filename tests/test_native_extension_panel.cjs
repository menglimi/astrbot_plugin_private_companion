/* Focused browser checks against packaged settings schemas, with synthetic APIs. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {test, before, after} = require("node:test");
/* 浏览器验收依赖 playwright；没装它的机器上这条检查等于没跑，不是产品坏了。
   直接 require 会让整份文件以 MODULE_NOT_FOUND 失败，把「跳过一次验收」报成
   「面板坏了」——红灯指向错的东西，比不跑更坏。 */
let chromium = null;
try { ({chromium} = require("playwright")); } catch (error) { chromium = null; }
if (!chromium) {
  test("原生扩展面板浏览器验收", {skip: "未安装 playwright，跳过真实渲染检查"}, () => {});
  return;
}
const root = path.resolve(__dirname, "..");
const pageRoot = path.join(root, "pages/陪伴小屋");
const source = fs.readFileSync(path.join(pageRoot, "native-extension-panel.js"), "utf8");
const managementSource = fs.readFileSync(path.join(pageRoot, "native-extension-management.js"), "utf8");
const css = ["tokens.css", "tokens-bridge.css", "styles.css", "native-extension-panel.css"]
  .map(name => fs.readFileSync(path.join(pageRoot, name), "utf8")).join("\n");
const records = Object.fromEntries(["reality", "together", "game", "live"].map(name => {
  const base = path.join(root, "astrbot_host/native_extensions", name);
  return [name, {settings:JSON.parse(fs.readFileSync(path.join(base, "settings-defaults.json"), "utf8")),
    schema:JSON.parse(fs.readFileSync(path.join(base, "settings-schema.json"), "utf8")),
    revision:"revision-" + name, runtime:{state:"running", error_code:null}}];
}));
records.reality.management = {mihome:{auth:{logged_in:false}, login:{running:false, status:"idle", qr_available:false}, devices:[]},
  audio_output:{selected_device_id:"system_default", label:"系统默认输出", playback_volume:35, devices:[{id:"system_default", name:"系统默认输出"}]},
  camera:{devices:[], boundary:"按明确任务读取单帧，画面交给所选视觉模型。"},
  users:[{user_id:"42", label:"测试用户", consent:{confirmed:false, local_audio:false},
    policy:{proactive_voice_enabled:false, playback_volume:35}, alarm:{enabled:false, time:"07:30", days:[0,1,2,3,4], delivery_mode:"chat_on_failure"},
    custom_reminders:[{id:"reminder-1", status:"scheduled", topic:"吃早餐", scheduled_text:"10-12 07:30"}],
    camera:{eligible:true, consented:false, enabled:false, proactive_mode:"off", proactive_max_daily:-1}}]};
records.live.management = {connected:false, authenticated:false, hotkeys:[], error_code:null};
let browser;
before(async () => { browser = await chromium.launch({headless:true,
  ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? {executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH} : {})}); });
after(async () => { await browser?.close(); });

async function harness(name, viewport = {width:1360, height:900}, panelOptions = {}) {
  const page = await browser.newPage({viewport});
  page.setDefaultTimeout(5000);
  await page.setContent('<!doctype html><html data-theme="courtyard" data-theme-ready="true" data-room-ready="true"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><dialog open class="detail-dialog detail-dialog-native-extension"><header class="dialog-head"><h2>内置能力设置</h2><button class="icon-button" type="button" aria-label="关闭详情">&times;</button></header><div class="dialog-body"><div id="native-root"></div></div></dialog></body></html>');
  await page.addStyleTag({content:css});
  await page.evaluate(({records, name}) => {
    window.records = structuredClone(records); window.requests = [];
    window.confirmationRequests = []; window.confirmationResult = false; window.nativeConfirmCalls = 0;
    window.confirm = () => { window.nativeConfirmCalls++; return true; };
    window.CompanionConfirm = async options => {
      window.confirmationRequests.push(options);
      return window.confirmationResult;
    };
    window.providers = Object.fromEntries(Object.keys(records).map(key => [key, {capability_id:key,
      extension_id:key, owner:"companion_v2", desired_state:"active", assembled:true, installed_revision:7}]));
    function merge(target, updates) {
      for (const [key, value] of Object.entries(updates)) {
        if (value && typeof value === "object" && !Array.isArray(value)) merge(target[key] ??= {}, value);
        else target[key] = value;
      }
    }
    window.CompanionProduct = {
      provider:key => window.providers[key], errorLabel:() => "", refreshIntegrations:async () => {},
      target:() => ({scope_kind:"private", participant_id:"42"}),
      request:async (method, endpoint, body) => {
        window.requests.push({method, endpoint, body});
        if (window.requestGate) await window.requestGate;
        if (window.requestError) throw window.requestError;
        if (method === "GET") return structuredClone({extensions:window.records});
        const record = window.records[body.extension_id];
        if (endpoint === "extensions/action") {
          if (window.actionError) throw window.actionError;
          const data = record.management;
          if (body.action === "mihome_start_login") data.mihome.login = {running:true, status:"waiting", qr_available:true, qr_revision:"qr-1",
            qr_image:"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFxoAAAAASUVORK5CYII="};
          if (body.action === "mihome_login_status") { data.mihome.auth.logged_in = true; data.mihome.login = {running:false, status:"success", qr_available:false}; }
          if (body.action === "mihome_sync_devices") data.mihome.devices = [{name:"测试灯", room_name:"书房", online:true}];
          if (body.action === "mihome_logout") { data.mihome.auth.logged_in = false; data.mihome.login = {running:false, status:"idle", qr_available:false}; }
          if (body.action === "consent_camera") { data.users[0].camera.consented = body.payload.confirmed; data.users[0].camera.enabled = body.payload.confirmed; }
          if (body.action === "update_camera_policy") data.users[0].camera = {...data.users[0].camera, enabled:body.payload.camera_enabled,
            proactive_mode:body.payload.proactive_mode, proactive_max_daily:body.payload.proactive_max_daily};
          if (body.action === "scan_camera") data.camera.devices = [{name:"测试摄像头", index:0}];
          if (body.action === "test_camera") return structuredClone({...record, result:{status:"success", captured:true,
            message:"已完成一次单帧完整视觉理解", observation:{summary:"合成测试画面"}},
            camera_preview:{user_id:body.payload.user_id, captured_at:1, data_url:"data:image/jpeg;base64,AA==", ephemeral:true}});
          if (body.action === "select_audio") {
            data.audio_output.selected_device_id = body.payload.device_id;
            data.audio_output.playback_volume = body.payload.playback_volume;
          }
          if (body.action === "test_audio") return structuredClone({...record, result:{played:true}});
          if (body.action === "mobile_pairing_token") {
            record.settings.mobile.enabled = true;
            record.settings.mobile.pairing_token_configured = true;
            return structuredClone({...record, result:{pairing_token:"synthetic-mobile-token", running:true}});
          }
          if (body.action === "request_audio_consent") return structuredClone({...record,
            result:{confirmation_required:true, prompt:"请本人在私聊发送：我理解风险并确认授权"}});
          if (body.action === "save") data.users[0].alarm = {...data.users[0].alarm, ...body.payload};
          if (body.action === "update_audio_policy") data.users[0].policy = {...data.users[0].policy, ...body.payload};
          if (body.action === "cancel_reminder") data.users[0].custom_reminders = [];
          if (body.action === "connect") data.connected = true;
          if (body.action === "request_auth") { if (window.authGate) await window.authGate; data.connected = true; data.authenticated = true; }
          if (body.action === "list_hotkeys") data.hotkeys = [{hotkeyID:"synthetic-hotkey-1", name:"开心", type:"ToggleExpression"}];
          if (body.extension_id === "live") data.error_code = null;
          return structuredClone({...record, result:{completed:true}});
        }
        if (record.revision !== body.revision) throw {code:"native_settings_conflict"};
        merge(record.settings, body.updates); record.revision += "-saved";
        return structuredClone(record);
      },
      manageExtension:async (key, action, revision, operation) => {
        window.requests.push({endpoint:"integration/lifecycle", body:{extension_id:key, action, revision, operation}});
        window.providers[key].desired_state = action === "pause" ? "paused" : "active";
        window.records[key].runtime.state = action === "pause" ? "inactive" : "running";
      },
    };
  }, {records, name});
  await page.addScriptTag({content:managementSource});
  await page.addScriptTag({content:source});
  await page.evaluate(({name, panelOptions}) => {
    window.panel = window.CompanionNativeExtensionPanel.mount(document.getElementById("native-root"), {extensionId:name, ...panelOptions});
  }, {name, panelOptions});
  await page.locator("[data-native-form]").waitFor();
  return page;
}

test("four packaged schemas expose every scalar setting and usable template lists", async () => {
  for (const name of Object.keys(records)) {
    const page = await harness(name);
    try {
      const result = await page.evaluate(() => {
        function leaves(schema, prefix = []) {
          return Object.entries(schema).flatMap(([key, field]) => field.type === "object"
            ? leaves(field.items || {}, [...prefix, key])
            : field.type === "template_list" ? [] : [JSON.stringify([...prefix, key])]);
        }
        return {expected:leaves(window.panel.record.schema), actual:window.panel.fields.map(item => JSON.stringify(item.path)),
          kinds:[...new Set(window.panel.fields.map(item => item.field.type))],
          missing:window.panel.fields.filter(item => !document.getElementById(item.id)).map(item => item.path)};
      });
      assert.deepEqual(result.actual.sort(), result.expected.sort(), name + " config fields");
      assert.deepEqual(result.missing, []);
      assert.equal(await page.getByRole("button", {name:"保存并应用", exact:true}).isDisabled(), true, name + " starts without an invented change");
      assert.ok(result.kinds.includes("bool"));
      if (name === "live") {
        await page.getByRole("button", {name:"Live2D 表情", exact:true}).click();
        await page.getByRole("button", {name:"添加Live2D 表情按键条目", exact:true}).click();
        const template = await page.evaluate(() => window.panel.collect().l2d_hotkeys[0]);
        assert.equal(template.__template_key, "default");
        assert.equal(template.enabled, true);
        assert.equal(template.duration, 3);
        assert.ok(await page.getByRole("button", {name:"移除第 1 个Live2D 表情按键条目"}).isVisible());
        await page.getByRole("button", {name:"移除第 1 个Live2D 表情按键条目"}).click();
        assert.equal(await page.evaluate(() => window.panel.collect().l2d_hotkeys.length), 0);
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    } finally { await page.close(); }
  }
});

test("save sends typed nested changes and retains blank secrets", async () => {
  const page = await harness("reality");
  try {
    await page.evaluate(() => {
      const config = window.records.reality.settings;
      config.audio ??= {}; config.audio.test_secret = "synthetic-secret";
      window.panel.record.schema.audio.items.test_secret = {type:"string", description:"测试密钥"};
      window.panel.record.settings.audio = {...window.panel.record.settings.audio, test_secret:"", test_secret_configured:true};
      window.panel.draft = structuredClone(window.panel.record.settings); window.panel.render();
    });
    assert.doesNotMatch(await page.locator("#native-root").innerHTML(), /synthetic-secret/);
    await page.evaluate(() => {
      const field = window.panel.fields.find(item => item.path.length === 1 && item.path[0] === "enabled");
      const input = document.getElementById(field.id); input.checked = !input.checked;
      input.dispatchEvent(new Event("input", {bubbles:true}));
      const numeric = window.panel.fields.find(item => item.path.length > 1 && item.field.type === "int");
      window.numericChange = {path:numeric.path, value:Number(document.getElementById(numeric.id).value) + 1};
      const numericInput = document.getElementById(numeric.id); numericInput.value = window.numericChange.value;
      numericInput.dispatchEvent(new Event("input", {bubbles:true}));
    });
    await page.getByRole("button", {name:"保存并应用", exact:true}).click();
    await page.locator("[data-native-notice]").filter({hasText:"设置已保存并应用"}).waitFor();
    const request = await page.evaluate(() => window.requests.find(item => item.endpoint === "extensions/configure"));
    assert.equal(request.method, "POST");
    assert.equal(request.body.extension_id, "reality");
    assert.equal(request.body.revision, "revision-reality");
    assert.equal(typeof request.body.updates.enabled, "boolean");
    const numeric = await page.evaluate(() => window.numericChange);
    const nested = numeric.path.reduce((value, key) => value[key], request.body.updates);
    assert.equal(nested, numeric.value);
    assert.equal(typeof nested, "number");
    assert.doesNotMatch(JSON.stringify(request.body.updates), /test_secret|synthetic-secret/);
    assert.equal(await page.evaluate(() => window.records.reality.settings.audio.test_secret), "synthetic-secret");
  } finally { await page.close(); }
});

test("asynchronous module startup updates status without replacing unsaved settings", async () => {
  const page = await harness("game");
  try {
    await page.evaluate(() => {
      const field = window.panel.fields.find(item => item.path.join(".") === "native_v2.tictactoe_enabled");
      const input = document.getElementById(field.id); input.checked = !input.checked;
      window.unsavedGameSwitch = input.checked;
      input.dispatchEvent(new Event("input", {bubbles:true}));
      window.panel.record.runtime.state = "starting";
      window.panel.updateStatus();
      window.records.game.runtime.state = "failed";
    });
    await page.locator("[data-native-runtime]").filter({hasText:"启动失败"}).waitFor();
    assert.equal(await page.evaluate(() => window.panel.dirty), true);
    assert.equal(await page.evaluate(() => window.panel.collect().native_v2.tictactoe_enabled === window.unsavedGameSwitch), true);
    assert.equal(await page.evaluate(() => window.requests.filter(item => item.endpoint === "extensions/configure").length), 0);
  } finally { await page.close(); }
});

test("pause uses module lifecycle and preserves a configuration draft", async () => {
  const page = await harness("together");
  try {
    await page.getByRole("searchbox", {name:"查找一起房间设置项"}).fill("监听地址");
    const target = page.getByLabel("监听地址", {exact:true});
    await target.fill("127.0.0.2");
    await page.getByRole("button", {name:"暂停模块", exact:true}).click();
    await page.getByRole("button", {name:"启用模块", exact:true}).waitFor();
    assert.equal(await target.inputValue(), "127.0.0.2");
    assert.equal(await page.evaluate(() => window.panel.dirty), true);
    const command = await page.evaluate(() => window.requests.find(item => item.endpoint === "integration/lifecycle"));
    assert.equal(command.body.extension_id, "together");
    assert.equal(command.body.revision, 7);
    assert.equal(command.body.action, "pause");
    assert.equal(typeof command.body.operation, "string");
    assert.ok(command.body.operation.length > 0);
    await page.getByRole("button", {name:"保存并应用", exact:true}).click();
    await page.locator("[data-native-notice]").filter({hasText:"设置已保存并应用"}).waitFor();
    assert.equal(await page.evaluate(() => window.providers.together.desired_state), "paused");
  } finally { await page.close(); }
});

test("mobile settings stay within the viewport and search reaches hidden groups", async () => {
  const page = await harness("live", {width:390, height:844});
  try {
    await page.getByRole("searchbox", {name:"查找直播与表演设置项"}).fill("OBS WebSocket 地址");
    assert.equal(await page.getByLabel("OBS WebSocket 地址", {exact:true}).isVisible(), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    const input = await page.getByLabel("OBS WebSocket 地址", {exact:true}).boundingBox();
    assert.ok(input.x >= 0 && input.x + input.width <= 390);
    await page.getByRole("searchbox", {name:"查找直播与表演设置项"}).fill("");
    const mobileLayout = await page.evaluate(() => {
      const dialog = document.querySelector(".detail-dialog-native-extension");
      const head = dialog.querySelector(".dialog-head");
      const close = head.querySelector(".icon-button");
      const nav = document.querySelector(".native-extension-nav");
      const savebar = document.querySelector(".native-extension-savebar");
      const field = document.getElementById(window.panel.fields.find(item => item.path.join(".") === "obs_ws_host").id);
      const rect = node => { const box = node.getBoundingClientRect(); return {width:box.width, height:box.height, top:box.top, bottom:box.bottom}; };
      return {dialog:rect(dialog), head:rect(head), nav:rect(nav), savebar:rect(savebar), field:rect(field), viewport:{width:innerWidth, height:innerHeight},
        navOverflow:nav.scrollWidth > nav.clientWidth, closeHeight:rect(close).height,
        hasDragHandle:getComputedStyle(head, "::before").display !== "none"};
    });
    assert.equal(mobileLayout.dialog.width, 390);
    assert.equal(mobileLayout.dialog.height, 844);
    assert.ok(mobileLayout.head.height <= 73);
    assert.ok(mobileLayout.closeHeight >= 44);
    assert.equal(mobileLayout.hasDragHandle, false);
    assert.ok(mobileLayout.field.height >= 44);
    assert.ok(mobileLayout.savebar.bottom <= mobileLayout.viewport.height + 1);
    assert.equal(mobileLayout.navOverflow, true);
    const navStick = await page.evaluate(() => {
      const dialog = document.querySelector(".detail-dialog-native-extension");
      const nav = dialog.querySelector(".native-extension-nav");
      dialog.scrollTop += nav.getBoundingClientRect().top - dialog.querySelector(".dialog-head").getBoundingClientRect().bottom;
      return {navTop:nav.getBoundingClientRect().top, headBottom:dialog.querySelector(".dialog-head").getBoundingClientRect().bottom};
    });
    assert.ok(navStick.navTop >= navStick.headBottom - 1);
    await page.setViewportSize({width:320, height:740});
    const narrowLayout = await page.evaluate(() => {
      const dialog = document.querySelector(".detail-dialog-native-extension");
      const field = document.getElementById(window.panel.fields.find(item => item.path.join(".") === "obs_ws_host").id);
      const box = field.getBoundingClientRect();
      return {width:dialog.getBoundingClientRect().width, viewportWidth:innerWidth,
        documentWidth:document.documentElement.scrollWidth, fieldLeft:box.left, fieldRight:box.right};
    });
    assert.equal(narrowLayout.width, 320);
    assert.equal(narrowLayout.documentWidth, narrowLayout.viewportWidth);
    assert.ok(narrowLayout.fieldLeft >= 0 && narrowLayout.fieldRight <= 320);
    await page.setViewportSize({width:390, height:844});
    if (process.env.COMPANION_UI_OUTPUT) {
      fs.mkdirSync(process.env.COMPANION_UI_OUTPUT, {recursive:true});
      await page.screenshot({path:path.join(process.env.COMPANION_UI_OUTPUT, "native-settings-mobile.png")});
      await page.setViewportSize({width:1360, height:900});
      await page.getByRole("searchbox", {name:"查找直播与表演设置项"}).fill("");
      await page.screenshot({path:path.join(process.env.COMPANION_UI_OUTPUT, "native-settings-desktop.png")});
    }
  } finally { await page.close(); }
});

test("mobile pairing generates and reveals a token once, while rotation is explicit", async () => {
  const page = await harness("reality", {width:390, height:844}, {onlyGroup:"mobile"});
  try {
    assert.equal(await page.getByRole("button", {name:"生成并启用网关", exact:true}).isVisible(), true);
    assert.match(await page.locator("[data-mobile-token-result]").textContent(), /同时启用/);
    await page.getByRole("button", {name:"生成并启用网关", exact:true}).click();
    const token = page.locator(".mobile-pairing-token input");
    await token.waitFor();
    assert.equal(await token.inputValue(), "synthetic-mobile-token");
    assert.equal(await page.getByRole("button", {name:"轮换配对令牌", exact:true}).isVisible(), true);
    assert.match(await page.locator("[data-mobile-token-result]").textContent(), /复制令牌/);
    assert.match(await page.locator(".mobile-gateway-status").textContent(), /health/);
    await page.getByRole("button", {name:"轮换配对令牌", exact:true}).click();
    await page.waitForFunction(() => window.confirmationRequests.length === 1);
    await page.waitForFunction(() => !window.panel.actionBusy);
    assert.equal((await page.evaluate(() => window.requests.filter(item => item.endpoint === "extensions/action").length)), 1);
    assert.equal(await page.evaluate(() => window.nativeConfirmCalls), 0);
    await page.evaluate(() => { window.confirmationResult = true; });
    await page.getByRole("button", {name:"轮换配对令牌", exact:true}).click();
    await page.waitForFunction(() => window.requests.filter(item => item.endpoint === "extensions/action").length === 2);
    const requests = await page.evaluate(() => window.requests.filter(item => item.endpoint === "extensions/action"));
    assert.deepEqual(requests.map(item => item.body.payload), [{rotate:false}, {rotate:true}]);
    const confirmation = await page.evaluate(() => window.confirmationRequests.at(-1));
    assert.equal(confirmation.title, "轮换手机配对令牌？");
    assert.equal(confirmation.danger, true);
    assert.equal(await token.inputValue(), "synthetic-mobile-token");
  } finally { await page.close(); }
});

test("MiHome actions are explicit and QR refreshes only read local state", async () => {
  const page = await harness("reality");
  try {
    assert.equal(await page.evaluate(() => window.requests.filter(row => row.method === "POST").length), 0);
    await page.getByRole("button", {name:"扫码登录", exact:true}).click();
    await page.getByRole("img", {name:"米家登录二维码"}).waitFor();
    await page.waitForFunction(() => window.requests.filter(row => row.method === "GET").length > 1);
    const login = await page.evaluate(() => window.requests.filter(row => row.method === "POST"));
    assert.equal(login.length, 1);
    assert.deepEqual(login[0].body, {extension_id:"reality", action:"mihome_start_login", payload:{}});
    if (process.env.COMPANION_UI_OUTPUT) {
      fs.mkdirSync(process.env.COMPANION_UI_OUTPUT, {recursive:true});
      await page.screenshot({path:path.join(process.env.COMPANION_UI_OUTPUT, "native-mihome-desktop.png")});
    }
    await page.getByRole("button", {name:"检查登录状态", exact:true}).click();
    await page.getByRole("button", {name:"同步设备", exact:true}).waitFor();
    await page.getByRole("button", {name:"同步设备", exact:true}).click();
    await page.getByText("测试灯", {exact:true}).waitFor();
    assert.equal(await page.getByRole("img", {name:"米家登录二维码"}).count(), 0);
    await page.getByRole("button", {name:"退出米家账号", exact:true}).click();
    await page.getByRole("button", {name:"扫码登录", exact:true}).waitFor();
    const actions = await page.evaluate(() => window.requests.filter(row => row.endpoint === "extensions/action").map(row => row.body));
    assert.deepEqual(actions.map(row => row.action), ["mihome_start_login", "mihome_login_status", "mihome_sync_devices", "mihome_logout"]);
    assert.deepEqual(actions[1].payload, {qr_revision:"qr-1"});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  } finally { await page.close(); }
});

test("camera confirmation and policy use the actual native action payload", async () => {
  const page = await harness("reality", {width:390, height:844});
  try {
    await page.locator("[data-native-camera-details] > summary").click();
    const grant = page.getByRole("button", {name:"确认摄像头授权", exact:true});
    assert.equal(await grant.isDisabled(), true);
    await page.locator("[data-native-camera-consent]").check();
    await grant.click();
    await page.locator(".native-management-camera-state").filter({hasText:"已确认授权"}).waitFor();
    await page.locator("[data-native-camera-mode]").selectOption("ask");
    await page.locator("[data-native-camera-daily]").fill("2");
    if (process.env.COMPANION_UI_OUTPUT) {
      fs.mkdirSync(process.env.COMPANION_UI_OUTPUT, {recursive:true});
      await page.screenshot({path:path.join(process.env.COMPANION_UI_OUTPUT, "native-camera-mobile.png")});
    }
    await page.getByRole("button", {name:"保存摄像头策略", exact:true}).click();
    await page.locator("[data-native-action-notice]").filter({hasText:"保存摄像头策略已完成"}).waitFor();
    await page.getByRole("button", {name:"扫描摄像头", exact:true}).click();
    await page.locator(".native-management-camera-devices li").filter({hasText:"测试摄像头"}).waitFor();
    await page.getByRole("button", {name:"测试摄像头调用", exact:true}).click();
    await page.locator("[data-native-camera-test]").filter({hasText:"合成测试画面"}).waitFor();
    assert.equal(await page.locator(".native-management-camera-preview").getAttribute("src"), "data:image/jpeg;base64,AA==");
    await page.locator("[data-native-audio-details] > summary").click();
    await page.locator('[data-native-audio-input="output_volume"]').fill("28");
    await page.getByRole("button", {name:"保存音频输出", exact:true}).click();
    await page.locator("[data-native-action-notice]").filter({hasText:"保存音频输出已完成"}).waitFor();
    await page.getByRole("button", {name:"播放设备测试音", exact:true}).click();
    await page.locator("[data-native-action-notice]").filter({hasText:"播放设备测试音已完成"}).waitFor();
    await page.getByRole("button", {name:"撤销摄像头授权", exact:true}).click();
    await page.locator(".native-management-camera-state").filter({hasText:"尚未确认"}).waitFor();
    const actions = await page.evaluate(() => window.requests.filter(row => row.endpoint === "extensions/action").map(row => row.body));
    assert.deepEqual(actions[0], {extension_id:"reality", action:"consent_camera", payload:{user_id:"42", confirmed:true}});
    assert.deepEqual(actions[1], {extension_id:"reality", action:"update_camera_policy", payload:{user_id:"42", camera_enabled:true, proactive_mode:"ask", proactive_max_daily:2}});
    assert.deepEqual(actions[2], {extension_id:"reality", action:"scan_camera", payload:{}});
    assert.deepEqual(actions[3], {extension_id:"reality", action:"test_camera", payload:{user_id:"42", purpose:"拓展页摄像头单帧测试"}});
    assert.deepEqual(actions[4], {extension_id:"reality", action:"select_audio", payload:{device_id:"system_default", playback_volume:28}});
    assert.deepEqual(actions[5], {extension_id:"reality", action:"test_audio", payload:{playback_volume:28}});
    assert.deepEqual(actions[6].payload, {user_id:"42", confirmed:false});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  } finally { await page.close(); }
});

test("audio and wakeup controls keep real confirmation separate and preserve unrelated edits", async () => {
  const page = await harness("reality", {width:390, height:844});
  try {
    await page.locator("[data-native-audio-details] > summary").click();
    await page.locator("[data-native-alarm-time]").fill("08:15");
    await page.locator("[data-native-alarm-message]").fill("早餐做好了，快起床呀");
    await page.getByRole("button", {name:"准备本人音频授权", exact:true}).click();
    await page.locator("[data-native-action-notice]").filter({hasText:"请本人在私聊发送"}).waitFor();
    assert.equal(await page.evaluate(() => window.records.reality.management.users[0].consent.confirmed), false);
    assert.equal(await page.locator("[data-native-alarm-time]").inputValue(), "08:15");
    await page.locator("[data-native-audio-proactive]").check();
    await page.locator('[data-native-audio-input="policy_volume"]').fill("28");
    await page.getByRole("button", {name:"保存语音策略", exact:true}).click();
    await page.locator("[data-native-action-notice]").filter({hasText:"保存语音策略已完成"}).waitFor();
    assert.equal(await page.locator("[data-native-alarm-time]").inputValue(), "08:15");
    await page.locator('[data-native-alarm-day="4"]').uncheck();
    await page.locator("[data-native-alarm-enabled]").check();
    await page.getByRole("button", {name:"保存叫醒安排", exact:true}).click();
    await page.locator("[data-native-action-notice]").filter({hasText:"保存叫醒安排已完成"}).waitFor();
    const requests = await page.evaluate(() => window.requests.filter(item => item.endpoint === "extensions/action"));
    assert.equal(requests[0].body.action, "request_audio_consent");
    assert.deepEqual(requests[0].body.payload, {user_id:"42"});
    assert.deepEqual(requests[1].body.payload, {user_id:"42", proactive_voice_enabled:true, playback_volume:28});
    const alarm = requests[2].body.payload;
    assert.equal(alarm.user_id, "42");
    assert.equal(alarm.enabled, true);
    assert.equal(alarm.time, "08:15");
    assert.equal(alarm.message, "早餐做好了，快起床呀");
    assert.deepEqual(alarm.days, [0,1,2,3]);
    assert.equal(alarm.delivery_mode, "chat_on_failure");
    assert.equal(typeof alarm.repeat_count, "number");
    assert.equal(alarm.require_acknowledgement, true);
    await page.getByRole("button", {name:"取消这条提醒", exact:true}).click();
    await page.locator("[data-native-action-notice]").filter({hasText:"取消提醒已完成"}).waitFor();
    assert.deepEqual(await page.evaluate(() => window.requests.at(-1).body.payload), {user_id:"42", reminder_id:"reminder-1"});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await page.evaluate(() => window.requests.filter(item => item.endpoint === "extensions/configure").length), 0);
  } finally { await page.close(); }
});

test("VTS authorization shows waiting, keeps the draft, and imports a fetched hotkey locally", async () => {
  const page = await harness("live");
  try {
    assert.equal(await page.getByRole("button", {name:"读取热键", exact:true}).isDisabled(), true);
    await page.evaluate(() => {
      window.actionError = {code:"native_vts_authorization_required"};
      window.records.live.management.connected = true;
      window.records.live.management.error_code = "native_vts_authorization_required";
    });
    await page.getByRole("button", {name:"连接 VTube Studio", exact:true}).click();
    await page.locator("[data-native-action-notice]").filter({hasText:"请求 VTS 授权"}).waitFor();
    assert.equal(await page.evaluate(() => window.requests.filter(row => row.method === "POST").length), 1);
    await page.evaluate(() => {
      window.actionError = null;
      window.authGate = new Promise(resolve => { window.allowVts = resolve; });
    });
    await page.getByRole("button", {name:"请求 VTS 授权", exact:true}).click();
    await page.locator("[data-native-action-notice]").filter({hasText:"正在等待授权结果"}).waitFor();
    await page.evaluate(() => {
      const field = window.panel.fields.find(item => item.path.join(".") === "auto_discover");
      const input = document.getElementById(field.id); input.checked = !input.checked;
      input.dispatchEvent(new Event("input", {bubbles:true}));
      window.unsavedDiscovery = input.checked;
    });
    assert.equal(await page.getByRole("button", {name:"保存并应用", exact:true}).isDisabled(), true);
    await page.evaluate(() => window.allowVts());
    await page.locator("[data-native-action-notice]").filter({hasText:"请求 VTS 授权已完成"}).waitFor();
    assert.equal(await page.evaluate(() => window.panel.collect().auto_discover === window.unsavedDiscovery), true);
    await page.getByRole("button", {name:"读取热键", exact:true}).click();
    await page.getByRole("button", {name:"添加到表情", exact:true}).waitFor();
    await page.getByRole("button", {name:"添加到表情", exact:true}).click();
    const result = await page.evaluate(() => ({rows:window.panel.collect().l2d_hotkeys, dirty:window.panel.dirty,
      actions:window.requests.filter(row => row.endpoint === "extensions/action").map(row => row.body)}));
    assert.equal(result.rows[0].hotkey_id, "synthetic-hotkey-1");
    assert.equal(result.rows[0].tag, "开心");
    assert.equal(result.dirty, true);
    assert.deepEqual(result.actions.map(row => row.action), ["connect", "request_auth", "list_hotkeys"]);
    assert.ok(result.actions.every(row => row.extension_id === "live" && Object.keys(row.payload).length === 0));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  } finally { await page.close(); }
});
