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
const prefix = "/astrbot_plugin_private_companion/";
const labels = {a: "测试角色甲", b: "测试角色乙"};
const token = "c".repeat(64);
const roleKey = id => JSON.stringify(["bot-" + id, "shared-role"]);

function fixture() {
  const calls = [];
  const target = (id, group = false) => {
    const targetId = (group ? "group-" : "private-") + id;
    const base = `${prefix}host/targets/${targetId}/roles/shared-role/`;
    return {target_id: targetId, bot_id: "bot-" + id, account_id: id === "a" ? "10001" : "10002",
      account_name: "测试账号" + id, platform_id: "qq_official", scope_kind: group ? "group" : "private",
      display_name: (group ? "测试群" : "测试用户") + id, ...(group ? {group_id:"group-1"} : {participant_id:"user-" + id}),
      roles: [{role_id:"shared-role",role_name:labels[id],persona_ids:[labels[id]],
        ...Object.fromEntries(["life", "state", "growth", "memory", "relationships", "reply", "model", "config"].map(name => [name + "_api", base + name]))}]};
  };
  const targets = [target("a"), target("a", true), target("b")];
  const directory = {installation_id:"synthetic-role-scope",operator_id:"dashboard:synthetic",operator_name:"测试管理员",targets};
  const field = value => ({value, inherited_value:value,source:"role",inherited_source:"installation",explicit:false});
  const task = (id,layer = "role") => ({kind:"memory.reflect",lane:"background",capabilities:["text"],edit_token:token,
    application_state:"applied",configuration_layer:layer,fields:{provider:field("model-" + id),fallback:field(""),
      prompt:field(""),max_output_tokens:field(4096),timeout_seconds:field(60),replacement_rules:field("[]"),
      fallback_on_refusal:field(false),refusal_terms:field(""),replacement_scope:field("all")}});
  function rpc(method, endpoint, params = {}) {
    params ||= {};
    calls.push({method, endpoint, params});
    const id = /(?:private|group)-b/.test(endpoint) || params.target_id === "private-b" ? "b" : "a";
    if (endpoint === "host/performance") return {enabled:false,mode:"standard"};
    if (endpoint === "page/display-preferences") return {preferences:{motion:"off"}};
    if (endpoint === "page/targets") return directory;
    if (endpoint === "integration/manifest") return {product:{version:"test"},host:{enabled:true},core:[],extensions:[],providers:[],summary:{}};
    if (endpoint === "page/ownership") return {owner:"self",reason:"owner_module_absent"};
    if (endpoint === "host/status") return {enabled:true,state:"running",mode:"multi",targets};
    if (endpoint === "accounts") return {accounts:[],personas:[],groups:[targets[1]],sessions:[targets[0],targets[2]],discovered:{targets}};
    if (endpoint === "semantic") return {state:"ready",selection_ready:true,settings:{revision:1,enabled:true,reactions_enabled:true},
      catalog:{reaction:{total:1,enabled:1}},providers:{embedding:[],rerank:[],analysis:[]},sessions:[],next_after:null,
      items:[{id:"reaction-"+id,kind:"reaction",revision:1,enabled:true,payload:{name:labels[id]+"的表情",description:"合成表情资料"}}]};
    if (endpoint.endsWith("/model")) return {tasks:[task(id,params.layer)],providers:[{id:"model-"+id,capabilities:["text"],max_output_tokens:8192}],host_state:"running"};
    if (endpoint.endsWith("/config")) return {sections:Object.fromEntries(String(params.sections || "").split(",").map(section => [section,
      {section,settings:{},fields:{},provenance:{},edit_token:token,revision:1,scope:"role",layers:["installation","role"]}]))};
    if (endpoint.endsWith("/memory/overview")) return {facts:{active:1,total:1},episodes:{total:0},external_memory:{}};
    if (endpoint.endsWith("/memory")) return {items:[{atom_id:"fact-"+id,state:"active",revision:1,predicate:"当前事实",representation:labels[id]+"的事实"}],next_cursor:null};
    if (endpoint.endsWith("/growth")) return {role_id:"shared-role",items:[{growth_id:"growth-"+id,revision:1,kind:"goal",state:"active",
      title:labels[id]+"的成长",description:"合成成长目标",progress_note:"",source_count:1,view_token:"growth-"+id,actions:[]}],next_cursor:null};
    if (endpoint.endsWith("/life")) return {role_id:"shared-role",participant:"user-"+id,host_state:"running",view_token:"life",edit_token:"life-edit",
      config:{enabled:false,diary_enabled:false},sleep_schedule:{enabled:false,owner:{bot_id:"bot-"+id}},journals:[],activities:[],receipts:[],running:[]};
    if (endpoint.endsWith("/state")) return {state:{state:"ready",revision:1,values:{energy:75,mood:"平静",health:"稳定",hunger:"不饿",sleep:"清醒",busy:"空闲"}},settings:{},sleep_schedule:{enabled:false,owner:{bot_id:"bot-"+id}},presentation:{state:"preview"}};
    if (endpoint.endsWith("/reply")) return {settings:{enabled:true,llm_controlled:true,threshold:1,max_segments:10,min_segment_chars:1,rule_mode:"sentences",prompt:"",streaming:false,part_gap_ms:0,image_position:"after_text"},edit_token:token,recent:[],tts:{state:"ready",providers:[]}};
    if (endpoint.endsWith("/relationships")) return {role_id:"shared-role",subject_id:"user-"+id,items:[],next_cursor:null};
    return {items:[],events:[],candidates:[],signals:[],next_cursor:null,settings:{},revision:1};
  }
  return {calls,rpc,directory};
}

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
      response.end(JSON.stringify(data.rpc(method,endpoint,params)));
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
    localStorage.setItem("companion-v2-motion","off");
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
    assert.equal(await page.locator(".role-context strong").textContent(),labels[id]);
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
    await page.locator("[data-role-window]").selectOption("group-a");
    await selectRole("b");
    await page.locator("#semanticExpressionWorkspace").getByText(labels.b+"的表情",{exact:true}).waitFor();
    await expectRole("b");
    assert.equal(await page.locator("[data-role-window]").inputValue(),"");
    assert.equal(await page.evaluate(()=>CompanionProduct.selection().target_id),null);
    assert.equal(await page.locator("#semanticExpressionWorkspace").getByText(labels.a+"的表情",{exact:true}).count(),0);
  });

  await t.test("成长链路只显示当前角色显式选择的窗口", async () => {
    await navigate("learning","chain");
    await expectRole("b");
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
    await page.locator("#memoryWorkspace").getByText(labels.a+"的事实",{exact:true}).first().waitFor();
    await selectRole("b");
    await expectRole("b");
    assert.equal(await page.locator("[data-role-window]").inputValue(),"");
    assert.equal(await page.locator("#memoryWorkspace").getByText(labels.a+"的事实",{exact:true}).count(),0);
    await page.locator("[data-role-window]").selectOption("private-b");
    await page.locator("#memoryWorkspace").getByText(labels.b+"的事实",{exact:true}).first().waitFor();
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
