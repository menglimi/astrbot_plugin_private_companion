"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");

let chromium;
try { ({chromium} = require("playwright")); } catch (_) {}
const pageRoot = path.join(__dirname, "..", "pages", "陪伴小屋");
const read = name => fs.readFileSync(path.join(pageRoot, name), "utf8");
const {fixture, labels, roleKey} = require("./fixtures/role_scope_browser.cjs");
const {cookiePreferences} = require("./fixtures/browser_preferences_server.cjs");

test("完整小屋按当前 Bot 与角色切换工作区", {skip:chromium ? false : "未安装 playwright"}, async t => {
  const data = fixture();
  const css = JSON.parse(read("css-sources.json")).map(read).join("\n");
  const server = http.createServer(async (request, response) => {
    const pathname = decodeURIComponent(new URL(request.url, "http://test").pathname);
    if (pathname === "/rpc") {
      let raw = "";
      for await (const chunk of request) raw += chunk;
      const {method,endpoint,params} = JSON.parse(raw);
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify(endpoint === "page/browser-preferences"
        ? cookiePreferences(request,response,method,params) : data.rpc(method,endpoint,params)));
      return;
    }
    const file = pathname === "/" ? "index.html" : pathname.slice(1);
    if (file === "bundle.css") { response.setHeader("Content-Type", "text/css"); response.end(css); return; }
    const types = {".js":"application/javascript",".css":"text/css",".html":"text/html",".webp":"image/webp",".json":"application/json",".svg":"image/svg+xml"};
    try {
      const content = fs.readFileSync(path.join(pageRoot,file));
      response.setHeader("Content-Type",types[path.extname(file)] || "application/octet-stream");
      response.end(content);
    } catch (_) { response.writeHead(404); response.end(); }
  });
  await new Promise(resolve => server.listen(0,"127.0.0.1",resolve));
  const browser = await chromium.launch({headless:true,args:["--disable-dev-shm-usage"],
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {})});
  t.after(async () => { await browser.close(); await new Promise(resolve => server.close(resolve)); });
  const page = await browser.newPage({viewport:{width:1200,height:850},reducedMotion:"reduce"});
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on("pageerror",error => errors.push(error.stack || error.message));
  page.on("console",message => { if (message.text().includes("工作区挂载失败")) errors.push(message.text()); });
  await page.addInitScript(() => {
    const request = (method,endpoint,params) => fetch("/rpc",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({method,endpoint,params})}).then(response=>response.json());
    window.AstrBotPluginPage = {ready:async()=>({pluginName:"astrbot_plugin_private_companion",pageName:"陪伴小屋"}),
      apiGet:(endpoint,params)=>request("GET",endpoint,params),apiPost:(endpoint,params)=>request("POST",endpoint,params),onContext:()=>()=>{}};
  });
  const url = `http://127.0.0.1:${server.address().port}/`;
  await page.goto(url + "#manage/expression/reaction");
  await page.waitForFunction(() => window.CompanionProduct?.connection().state === "connected" && window.CompanionSemanticPage);
  const navigate = (tab,subtab="") => page.evaluate(([tab,subtab])=>window.navigate("manage",tab,subtab),[tab,subtab]);
  const selectRole = async id => {
    await page.locator("#stageScene").hover();
    await page.locator("[data-role-card-switch]").click();
    await page.locator("[data-role-card-choice]").selectOption(roleKey(id));
    await page.waitForFunction(id=>window.CompanionProduct.selection().bot_id === "bot-"+id,id);
  };
  const expectRole = async id => {
    assert.equal(await page.locator("[data-role-card-name]").textContent(),labels[id]);
  };
  const openModelConfig = async () => {
    await page.locator("[data-model-sub='cfg']").click();
    await page.locator("[data-model-cm='precise']").click();
  };

  await t.test("表情库跟随小屋角色，并且切换不会暗选私聊窗口", async () => {
    await page.evaluate(key=>CompanionProduct.chooseRoleKey(key),roleKey("a"));
    await page.locator("#semanticExpressionWorkspace").getByText(labels.a+"的表情",{exact:true}).waitFor();
    await expectRole("a");
    assert.equal(await page.locator("[data-role-window]").count(),0);
    await page.evaluate(()=>CompanionProduct.choose("group-a","shared-role"));
    await selectRole("b");
    await page.locator("#semanticExpressionWorkspace").getByText(labels.b+"的表情",{exact:true}).waitFor();
    await expectRole("b");
    assert.equal(await page.locator("[data-role-window]").count(),0);
    assert.equal(await page.evaluate(()=>CompanionProduct.selection().target_id),null);
    assert.equal(await page.locator("#semanticExpressionWorkspace").getByText(labels.a+"的表情",{exact:true}).count(),0);
  });

  await t.test("成长链路只显示当前角色显式选择的窗口", async () => {
    await navigate("learning","chain");
    await expectRole("b");
    assert.equal(await page.locator("[data-role-window]").count(),1);
    assert.equal(await page.locator("#shellPanel > .shell-panel-head + .role-context + .shell-panel-body").count(),1);
    assert.equal(await page.locator("[data-learning-scope-target]").count(),0);
    await page.getByText("请选择当前角色的私聊窗口以查看成长记录。",{exact:true}).waitFor();
    await page.locator("[data-role-window]").selectOption("private-b");
    await page.locator("#learningWorkspace").getByText(labels.b+"的成长",{exact:true}).first().waitFor();
    await selectRole("a");
    await expectRole("a");
    assert.equal(await page.locator("[data-role-window]").inputValue(),"");
    await page.locator("[data-role-window]").selectOption("private-a");
    await page.locator("#learningWorkspace").getByText(labels.a+"的成长",{exact:true}).first().waitFor();
    assert.equal(await page.locator("#learningWorkspace").getByText(labels.b+"的成长",{exact:true}).count(),0);
  });

  await t.test("事实与经历切换后清除旧窗口并按新窗口重读", async () => {
    await navigate("memory");
    assert.equal(await page.locator("[data-role-window]").count(),1);
    assert.equal(await page.locator("#shellPanel > .shell-panel-head + .role-context + .shell-panel-body").count(),1);
    assert.equal(await page.locator("#memory-target").count(),0);
    await page.locator("#memoryWorkspace").getByText(labels.a+"的事实",{exact:true}).first().waitFor();
    await selectRole("b");
    await expectRole("b");
    assert.equal(await page.locator("[data-role-window]").inputValue(),"");
    assert.equal(await page.locator("#memoryWorkspace").getByText(labels.a+"的事实",{exact:true}).count(),0);
    await page.locator("[data-role-window]").selectOption("private-b");
    await page.locator("#memoryWorkspace").getByText(labels.b+"的事实",{exact:true}).first().waitFor();
  });

  await t.test("会话入口只出现在需要它的页面与表达分区", async () => {
    await navigate("expression","reaction");
    assert.equal(await page.locator("[data-role-window]").count(),0);
    const expression=page.locator("#semanticExpressionWorkspace");
    const settingsHint=expression.getByRole("button",{name:"查看表情设置范围与使用说明"});
    await settingsHint.waitFor();
    assert.equal(await expression.locator(".semantic-feedback").count(),0);
    const settingsTip=expression.locator("[role=tooltip]").filter({hasText:"所有账号与角色共用"});
    assert.equal(await settingsTip.isVisible(),false);
    await settingsHint.hover();
    await settingsTip.waitFor({state:"visible"});
    await settingsHint.focus();
    await page.keyboard.press("Escape");
    await settingsTip.waitFor({state:"hidden"});
    const galleryHint=expression.getByRole("button",{name:"查看表情库范围"});
    await galleryHint.focus();
    await expression.locator("[role=tooltip]").filter({hasText:"图库属于当前小屋角色"}).waitFor({state:"visible"});
    await page.locator('[data-expression-module="expression-text"]').click();
    assert.equal(await page.locator("[data-role-window]").count(),1);
    assert.equal(await page.locator("#shellPanel > .shell-panel-head + .role-context + .shell-panel-body").count(),1);
    await page.locator('[data-expression-module="expression-reaction"]').click();
    assert.equal(await page.locator("[data-role-window]").count(),0);
    await navigate("learning","strategy");
    assert.equal(await page.locator("[data-role-window]").count(),0);
    await navigate("data","storage");
    assert.equal(await page.locator(".role-context-badge").textContent(),"全局");
    assert.equal(await page.locator("[data-role-window]").count(),0);
    await navigate("data","port");
    assert.equal(await page.locator("[data-role-window]").count(),1);
    assert.equal(await page.locator("#shellPanel > .shell-panel-head + .role-context + .shell-panel-body").count(),1);
  });

  await t.test("模型页切换账号，群窗口使用群接口，未选窗口禁用窗口覆盖", async () => {
    await navigate("models");
    await openModelConfig();
    await page.waitForFunction(()=>document.querySelector("select[data-model-field='provider']")?.value === "model-b");
    await selectRole("a");
    await expectRole("a");
    await openModelConfig();
    await page.waitForFunction(()=>document.querySelector("select[data-model-field='provider']")?.value === "model-a");
    assert.equal(await page.locator("[data-model-layer='window']").isDisabled(),true);
    await page.locator("[data-role-window]").selectOption("group-a");
    await page.waitForFunction(()=>CompanionProduct.selection().target_id === "group-a");
    await openModelConfig();
    await page.locator("[data-model-layer='window']").click();
    await page.waitForFunction(()=>document.querySelector("[data-model-layer='window']")?.getAttribute("aria-pressed")==="true");
    assert.ok(data.calls.some(call=>call.endpoint.endsWith("/group-a/roles/shared-role/model") && call.params.layer === "window"));
  });

  await t.test("有真实模型草稿时取消切换保留角色与输入", async () => {
    await page.locator("[data-model-layer='role']").click();
    await page.locator("[data-model-sub='cfg']").click();
    await page.locator("[data-model-cm='precise']").click();
    await page.locator("[data-model-inherit='prompt'][data-model-kind='memory.reflect']").uncheck();
    const prompt = page.locator("textarea[data-model-field='prompt'][data-model-kind='memory.reflect']");
    await prompt.fill("测试草稿：切换取消后保留");
    await page.waitForFunction(()=>CompanionModelsPage.isDirty());
    assert.equal(await page.locator("#roleCardMenu").isVisible(),false,"打开前菜单应关闭");
    await page.locator("#stageScene").hover();
    await page.locator("[data-role-card-switch]").click();
    assert.equal(await page.locator("#roleCardMenu").isVisible(),true,"编辑框失焦后第一次点击仍应打开角色菜单");
    await page.locator("[data-role-card-choice]").selectOption(roleKey("b"));
    await page.locator("#confirmationDialog[open]").waitFor();
    await page.locator("#confirmationCancel").click();
    await expectRole("a");
    assert.equal(await prompt.inputValue(),"测试草稿：切换取消后保留");
    assert.equal(await page.evaluate(()=>CompanionProduct.selection().target_id),"group-a");
  });

  assert.deepEqual(errors,[]);
  assert.equal(data.calls.filter(call=>call.method === "POST" && !call.endpoint.startsWith("page/")).length,0,"浏览器验证不得保存业务配置");
});
