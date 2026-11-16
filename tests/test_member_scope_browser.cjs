"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),http=require("node:http");
let chromium;try{({chromium}=require("playwright"));}catch(_){}
const pageRoot=path.join(__dirname,"..","pages","陪伴小屋"),memberRoot=path.join(__dirname,"..","pages","关系与权限");
const read=name=>fs.readFileSync(path.join(pageRoot,name),"utf8");
const {fixture,labels,roleKey}=require("./fixtures/role_scope_browser.cjs");
const {cookiePreferences}=require("./fixtures/browser_preferences_server.cjs");

test("整页关系网切换角色保留目标成员，并在角色切换前处理草稿",{skip:chromium?false:"未安装 playwright"},async t=>{
  const data=fixture();data.directory.acquaintance_api="/astrbot_plugin_private_companion/acquaintance";
  const persons=["a","b"].map(id=>({subject_id:"user-"+id,native_account_id:"user-"+id,display_name:"成员"+id,
    platform:"qq_official",account_id:id==="a"?"10001":"10002",account_name:"测试账号"+id,
    bot_id:"bot-"+id,role_id:"shared-role",role_name:labels[id],persona_ids:[labels[id]],revision:1,
    private_chat:true,private_target:true,settings_available:true,identity_tier:"standard",groups:{},
    total:{msg_count:2,active_days:1},private:{msg_count:2,active_days:1}}));
  const rpc=(method,endpoint,params)=>{
    if(endpoint==="acquaintance")return {persons,groups:[],group_networks:[],counts:{total:2,private_chat:2},group_access:{accounts:[]}};
    if(endpoint==="acquaintance/authorization")return {subject_id:params.subject_id,identity_tier:"standard",identity_revision:1,items:[]};
    if(endpoint==="acquaintance/settings")return {sections:{},section_errors:{}};
    if(endpoint==="acquaintance/strangers")return {items:[]};
    return data.rpc(method,endpoint,params);
  };
  const css=JSON.parse(read("css-sources.json")).map(read).join("\n");
  let workspace=fs.readFileSync(path.join(memberRoot,"workspace.html"),"utf8").replace(/<script[^>]*src="[^"]+"[^>]*><\/script>/g,"");
  workspace=workspace.replace('<link rel="stylesheet" href="style.css">',"<style>"+fs.readFileSync(path.join(memberRoot,"style.css"),"utf8")+"\n"+read("workspace-theme.css")+"</style>");
  const scripts=[read("embedded-bridge.js"),read("assets/lucide.min.js"),... ["member-editor.js","app.js"].map(name=>fs.readFileSync(path.join(memberRoot,name),"utf8"))].join("\n");
  workspace=workspace.replace("</body>",()=>"<script>"+scripts.replace(/<\/script/g,"<\\/script")+"</script></body>");
  const server=http.createServer(async(request,response)=>{
    const file=decodeURIComponent(new URL(request.url,"http://test").pathname);
    if(file==="/rpc"){
      let body="";for await(const chunk of request)body+=chunk;const {method,endpoint,params}=JSON.parse(body);
      response.setHeader("Content-Type","application/json");response.end(JSON.stringify(endpoint==="page/browser-preferences"?cookiePreferences(request,response,method,params):rpc(method,endpoint,params)));return;
    }
    if(file==="/workspace-bundles/identity.js"){response.setHeader("Content-Type","application/javascript");response.end("window.CompanionWorkspaceBundle={identity:"+JSON.stringify(workspace)+"};");return;}
    if(file==="/bundle.css"){response.setHeader("Content-Type","text/css");response.end(css);return;}
    try{const filename=file==="/"?"index.html":file.slice(1);response.setHeader("Content-Type",({".js":"application/javascript",".css":"text/css",".html":"text/html",".json":"application/json",".svg":"image/svg+xml"})[path.extname(filename)]||"application/octet-stream");response.end(fs.readFileSync(path.join(pageRoot,filename)));}catch(_){response.writeHead(404);response.end();}
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  const browser=await chromium.launch({headless:true,args:["--disable-dev-shm-usage"],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  t.after(async()=>{await browser.close();await new Promise(resolve=>server.close(resolve));});
  const page=await browser.newPage({viewport:{width:1200,height:850},reducedMotion:"reduce"});page.setDefaultTimeout(10000);
  const errors=[];page.on("pageerror",error=>errors.push(error.message));
  await page.addInitScript(()=>{
    if(window!==window.top)return;
    const request=(method,endpoint,params)=>fetch("/rpc",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({method,endpoint,params})}).then(response=>response.json());
    window.AstrBotPluginPage={ready:async()=>({pluginName:"astrbot_plugin_private_companion",pageName:"陪伴小屋"}),apiGet:(endpoint,params)=>request("GET",endpoint,params),apiPost:(endpoint,params)=>request("POST",endpoint,params),onContext:()=>()=>{}};
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/#manage/identity-people`);
  const frame=page.frameLocator('.product-workspace-frame');
  await frame.locator('.person-card').filter({hasText:"成员b"}).click();
  await frame.getByRole("button",{name:"切换到该角色并编辑"}).waitFor();
  const original=page.frames().find(item=>item.parentFrame());
  await original.evaluate(()=>{window.memberFrameSentinel="same-frame";});
  await frame.getByRole("button",{name:"切换到该角色并编辑"}).click();
  await page.waitForFunction(()=>window.CompanionProduct.selection().bot_id==="bot-b");
  await frame.getByRole("textbox",{name:"目录显示名称"}).waitFor();
  assert.equal(await original.evaluate(()=>window.memberFrameSentinel),"same-frame");
  assert.match(await frame.locator(".member-name-row").textContent(),/成员b/);
  assert.equal(await page.locator("[data-role-card-name]").textContent(),labels.b);
  await frame.getByRole("textbox",{name:"目录显示名称"}).fill("未保存备注");
  await page.waitForFunction(()=>window.CompanionProduct.workspaceDirtyDrafts().length===1);
  await page.evaluate(key=>{void window.CompanionProduct.chooseRoleKey(key);},roleKey("a"));
  await page.getByRole("button",{name:"继续编辑",exact:true}).click();
  assert.equal(await page.evaluate(()=>window.CompanionProduct.selection().bot_id),"bot-b");
  assert.equal(await frame.getByRole("textbox",{name:"目录显示名称"}).inputValue(),"未保存备注");
  assert.equal(await original.evaluate(()=>window.memberFrameSentinel),"same-frame");
  assert.deepEqual(errors,[]);
});
