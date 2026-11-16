"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {browserPreferencesServer,cookieName} = require("./fixtures/browser_preferences_server.cjs");
const {roleKey} = require("./fixtures/role_scope_browser.cjs");

let chromium;
try { ({chromium} = require("playwright")); } catch (_) {}

test("真实 Chromium 沙箱经合成 Dashboard 桥持久保存浏览器偏好", {skip:chromium ? false : "未安装 playwright"}, async t => {
  const server = await browserPreferencesServer();
  const profiles = fs.mkdtempSync(path.join(os.tmpdir(),"companion-browser-preferences-"));
  const errors = [];
  let context, page, frame;
  const options = {headless:true,args:["--disable-dev-shm-usage"],viewport:{width:1200,height:850},reducedMotion:"reduce",
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {})};
  t.after(async()=>{
    await context?.close();
    await server.close();
    fs.rmSync(profiles,{recursive:true,force:true});
  });
  const attach = async()=>{
    frame = page.frames().find(item=>item.url().includes("/index.html"));
    await frame.waitForFunction(()=>window.CompanionProduct?.connection().state==="connected");
    await frame.locator("[data-role-card-name]").waitFor();
  };
  const launch = async name=>{
    context=await chromium.launchPersistentContext(path.join(profiles,name),options);
    page=context.pages()[0] || await context.newPage();
    page.setDefaultTimeout(10000);
    page.on("pageerror",error=>errors.push(error.message));
    await page.goto(server.url);
    await attach();
  };
  const reload = async()=>{await page.reload();await attach();};
  const selection = ()=>frame.evaluate(()=>CompanionProduct.selection());
  const preferences = ()=>frame.evaluate(async()=>(await CompanionProduct.request("GET","page/browser-preferences")).preferences);
  const choose = (target)=>frame.evaluate(target=>CompanionProduct.choose(target,"shared-role"),target);
  const chooseRole = id=>frame.evaluate(key=>CompanionProduct.chooseRoleKey(key),roleKey(id));
  const expectPosition = async position=>{
    await frame.waitForFunction(expected=>{
      const box=document.querySelector("#stageScene").getBoundingClientRect();
      const main=document.querySelector(".shell-body").getBoundingClientRect();
      return Math.abs(Math.min(innerWidth,main.right)-box.right-expected.right)<1
        && Math.abs(Math.min(innerHeight,main.bottom)-box.bottom-expected.bottom)<1;
    },position);
  };
  let savedPosition;

  await t.test("沙箱禁止 localStorage，角色、显式群窗口和拖动位置仍通过 Cookie 保存",async()=>{
    await launch("retained");
    assert.equal(await frame.evaluate(()=>{try{localStorage.getItem("test");return "allowed";}catch(error){return error.name;}}),"SecurityError");
    assert.equal(await page.locator("iframe").getAttribute("sandbox"),"allow-scripts allow-forms allow-downloads");
    assert.equal(await choose("group-a"),true);
    const box=await frame.locator("#stageScene").boundingBox();
    await page.mouse.move(box.x+50,box.y+70);
    await page.mouse.down();
    await page.mouse.move(box.x-110,box.y-100,{steps:5});
    await page.mouse.up();
    savedPosition=await frame.evaluate(()=>{
      const box=document.querySelector("#stageScene").getBoundingClientRect();
      const main=document.querySelector(".shell-body").getBoundingClientRect();
      return {right:Math.min(innerWidth,main.right)-box.right,bottom:Math.min(innerHeight,main.bottom)-box.bottom};
    });
    await frame.waitForFunction(async expected=>{
      const {preferences}=await CompanionProduct.request("GET","page/browser-preferences");
      return preferences.room_card_position?.right===expected.right && preferences.room_card_position?.bottom===expected.bottom;
    },savedPosition);
    assert.deepEqual(await preferences(),{selection:{target_id:"group-a",role_id:"shared-role",bot_id:"bot-a"},room_card_position:savedPosition});
    const cookie=(await context.cookies()).find(item=>item.name===cookieName);
    assert.equal(cookie.httpOnly,true);
    assert.ok(cookie.expires>Date.now()/1000);
    await reload();
    assert.equal((await selection()).target_id,"group-a");
    await expectPosition(savedPosition);
  });

  await t.test("角色切换保留卡片位置，窗口清空及位置复位互不覆盖",async()=>{
    assert.equal(await chooseRole("b"),true);
    assert.deepEqual(await selection(),{target_id:null,role_id:"shared-role",bot_id:"bot-b"});
    assert.deepEqual((await preferences()).room_card_position,savedPosition);
    await choose("private-b");
    await frame.evaluate(()=>CompanionProduct.clearWindow());
    await reload();
    assert.equal((await selection()).bot_id,"bot-b");
    assert.equal((await selection()).target_id,null);
    await expectPosition(savedPosition);
    await frame.locator("#stageScene").hover();
    await frame.locator("[data-role-card-switch]").click();
    await frame.locator("[data-role-card-reset]").click();
    await frame.waitForFunction(async()=> (await CompanionProduct.request("GET","page/browser-preferences")).preferences.room_card_position===null);
    assert.deepEqual((await preferences()).selection,{target_id:null,role_id:"shared-role",bot_id:"bot-b"});
    await reload();
    assert.equal(await frame.evaluate(()=>document.querySelector(".stage-region").style.getPropertyValue("--role-card-left")),"");
    await choose("private-b");
    assert.equal(await frame.evaluate(position=>CompanionProduct.saveBrowserPreferences({room_card_position:position}),savedPosition),true);
  });

  await t.test("关闭并重新打开相同浏览器资料后恢复角色、窗口与位置",async()=>{
    await context.close(); context=null;
    await launch("retained");
    assert.deepEqual(await selection(),{target_id:"private-b",role_id:"shared-role",bot_id:"bot-b"});
    await expectPosition(savedPosition);
    assert.equal(await frame.evaluate(()=>CompanionProduct.browserPreferencesNotice()),"");
  });

  await t.test("不同浏览器资料不共享同一 Dashboard 账号的选择",async()=>{
    await context.close(); context=null;
    await launch("independent");
    assert.deepEqual(await selection(),{target_id:null,role_id:"shared-role",bot_id:"bot-a"});
    assert.equal((await preferences()).room_card_position,null);
  });

  await t.test("偏好接口故障不阻止角色切换，页面显示无法保存的提示",async()=>{
    server.setFailure(true);
    assert.equal(await chooseRole("b"),true);
    assert.equal((await selection()).bot_id,"bot-b");
    assert.match(await frame.evaluate(()=>CompanionProduct.browserPreferencesNotice()),/Cookie.*未能保存/);
    await frame.locator("#stageScene").hover();
    await frame.locator("[data-role-card-switch]").click();
    assert.match(await frame.locator("[data-role-card-status]").textContent(),/Cookie.*未能保存/);
    server.setFailure(false);
    assert.equal(await chooseRole("a"),true);
    assert.equal(await frame.evaluate(()=>CompanionProduct.browserPreferencesNotice()),"");
  });

  await t.test("浏览器禁止 Cookie 时仍可切换，重读检测未保存并提示",async()=>{
    await context.close(); context=null;
    const profile=path.join(profiles,"blocked","Default");
    fs.mkdirSync(profile,{recursive:true});
    fs.writeFileSync(path.join(profile,"Preferences"),JSON.stringify({profile:{default_content_setting_values:{cookies:2}}}));
    await launch("blocked");
    assert.equal(await chooseRole("b"),true);
    assert.equal((await selection()).bot_id,"bot-b");
    assert.equal((await context.cookies()).some(item=>item.name===cookieName),false,"Chromium must actually refuse the Cookie");
    assert.match(await frame.evaluate(()=>CompanionProduct.browserPreferencesNotice()),/Cookie.*未能保存/);
    await reload();
    assert.equal((await selection()).bot_id,"bot-a");
  });

  assert.deepEqual(errors,[]);
  // 表达页自动 POST 只读分段预览；持久化写入必须仅为浏览器偏好。
  assert.deepEqual(server.data.calls.filter(call=>call.method==="POST"
    && call.endpoint!=="page/browser-preferences"
    && !/^host\/targets\/[^/]+\/roles\/[^/]+\/reply\/preview$/.test(call.endpoint)),[]);
});
