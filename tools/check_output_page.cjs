/* Writable output acceptance through AstrBot asset rewriting and its iframe sandbox. */
"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs/promises"), http = require("node:http"), path = require("node:path");
const {spawnSync} = require("node:child_process");
const {chromium} = require("playwright");
const ROOT = path.resolve(process.env.COMPANION_SOURCE_ROOT || path.join(__dirname, ".."));
const PAGE = path.join(ROOT, "pages", "陪伴小屋");
const HOST_ROOT = process.env.COMPANION_HOST_ROOT || path.resolve(__dirname, "../../../..");
const HOST = "astrbot_plugin_private_companion", TOKEN = "synthetic-output-qa";
const PREFIX = "/api/plugin/page/content/" + HOST + "/" + encodeURIComponent("陪伴小屋") + "/";
const ROLE = "host/targets/first/roles/role-a";
const ok = data => ({status: "ok", data});
const fail = code => ({status: "error", message: code, data: {code}});
const usage = () => ({base_identity:false,outfit_category:"",outfit_lock:"request",preferred_scene:"",scenes:[],times:[],excluded_scenes:[],excluded_times:[]});

function hostAssets() {
  const script = `
import json, os, tempfile
from pathlib import Path
from types import SimpleNamespace
with tempfile.TemporaryDirectory(prefix="output-page-qa-") as data_root:
    os.environ["ASTRBOT_ROOT"] = data_root
    from astrbot.dashboard.services.plugin_page_service import PluginPageService
    from tools.host_page_render import render_css, render_html
    from companion_v2.configuration.registry import FieldRegistry
    from companion_v2.configuration.contract import contract_of
    from companion_v2.conversation.media import output_settings, quota, review
    from companion_v2.references import configuration, wardrobe
    from companion_v2.conversation.content import authoring_service
    root, sections = Path.cwd(), {}
    page = root / "pages" / "陪伴小屋"
    service = PluginPageService(SimpleNamespace(plugin_store_path=str(root.parent)))
    query, args = {"asset_token":"synthetic-output-qa"}, ("astrbot_plugin_private_companion","陪伴小屋")
    assets = {"index.html":render_html(service,(page/"index.html").read_text(encoding="utf-8"),*args,file_name="index.html",theme="light",extra_query_params=query)}
    for css in page.glob("*.css"):
        assets[css.name] = render_css(service,css.read_text(encoding="utf-8"),*args,file_name=css.name,extra_query_params=query)
    for name,module in {"image.output":output_settings,"quota":quota,"image_review":review,"photo.reference":configuration,"wardrobe":wardrobe,"content.authoring":authoring_service}.items():
        fields = module.configuration_fields()
        registry, group = FieldRegistry(version="output-qa.v1",fields=fields), fields[0].group_id
        settings = {field.field_id.removeprefix(group+"."):field.default for field in fields}
        sections[name] = {"group_id":group,"scope":fields[0].layers[-1].value,"layers":[layer.value for layer in fields[0].layers],"settings":settings,"fields":contract_of(registry,group),"edit_token":"a"*64,"provenance":{key:{"layer":None,"explicit":False,"revision":0} for key in settings}}
    print(json.dumps({"assets":assets,"sections":sections},ensure_ascii=False))
`;
  const result = spawnSync(path.join(HOST_ROOT,"backend/python/python.exe"),["-X","utf8","-c",script],{
    cwd:ROOT,encoding:"utf8",maxBuffer:4*1024*1024,env:{...process.env,PYTHONPATH:[ROOT,path.join(HOST_ROOT,"backend/app")].join(path.delimiter),PYTHONIOENCODING:"utf-8"}});
  assert.equal(result.status,0,result.stderr); return JSON.parse(result.stdout);
}

(async()=>{
  const fixture=hostAssets(), sections=fixture.sections;
  const output=path.resolve(process.argv[2]||path.join(ROOT,"build/ui-output-qa")); await fs.mkdir(output,{recursive:true});
  const imageUrl=PREFIX+"assets/chengxi.jpg?asset_token="+TOKEN;
  const references=["ref-a","ref-b","ref-c"].map((asset_id,index)=>({asset_id,label:["居家服（睡前）","校园日常","正式场合礼服"][index],roles:["identity","outfit"],usage:usage(),state:"active",revision:1,thumbnail:imageUrl,mime_type:"image/jpeg",size_bytes:4096}));
  const wardrobe=[{item_id:"wardrobe-a",name:"燕麦色风衣",description:"",tags:["日常"],asset_id:"ref-a",state:"active",revision:1}];
  const album=[1,2].map(id=>({image_id:"image-"+id,created_at:"2026-10-07T03:00:00Z",action:"image.generate",width:768,height:1024,thumbnail:imageUrl}));
  let daily=null,failImage=false,orderConflict=false,sceneConflict=false,backendRevision=1;
  let backend={enabled:true,backend:"auto",endpoints:[{id:"endpoint-a",name:"主用端点",enabled:true,platform:"openai",model:"image-qa",base_url:"https://example.invalid/v1",width:1024,height:1024,timeout_seconds:180,api_key:"",api_key_set:true}],comfyui:{base_url:"",timeout_seconds:300,workflows:{},api_key:"",api_key_set:false},sd_webui:{base_url:"",timeout_seconds:300,parameters:{},api_key:"",api_key_set:false}};
  const posts=[],imageRequests=[],receipts=new Map();
  const snapshot=()=>({settings:structuredClone(backend),revision:"revision-"+backendRevision,host_registered:true});
  const inventory=()=>({references:structuredClone(references),wardrobe:structuredClone(wardrobe),daily_outfit:structuredClone(daily),scene_presets:JSON.parse(sections["image.output"].settings.scene_presets_json),settings:{references:structuredClone(sections["photo.reference"].settings),wardrobe:structuredClone(sections.wardrobe.settings)}});
  function api(method,endpoint,body,query){
    if(method==="GET"){
      if(endpoint==="page/targets")return ok({installation_id:"synthetic-installation",targets:[{target_id:"first",scope_kind:"private",participant_id:"200",platform_id:"qq-main",account_id:"100",bot_id:"bot-a",roles:[{role_id:"role-a",role_name:"澄汐",config_api:"/"+HOST+"/"+ROLE+"/config",model_api:"/"+HOST+"/"+ROLE+"/model",image_material_api:"/"+HOST+"/"+ROLE+"/image-materials"}]}]});
      if(endpoint==="integration/manifest")return ok({extensions:["image","content"].map(id=>({capability_id:id,extension_id:id,owner:"companion_v2",label:id,integration_state:"connected",assembled:true,enabled:true,runtime_ready:true,integration_label:"可用"})),providers:[],summary:{connected_count:2,extension_ready_count:2,provider_count:0}});
      if(endpoint===ROLE+"/config")return ok({sections:Object.fromEntries(Object.entries(sections).filter(([kind])=>!query.sections||query.sections.split(",").includes(kind)).map(([kind,value])=>[kind,structuredClone(value)]))});
      if(endpoint===ROLE+"/model/image/output")return ok(snapshot());
      if(endpoint===ROLE+"/model/image/review/providers")return ok({providers:[{id:"vision-a",name:"视觉审核"}]});
      if(endpoint===ROLE+"/model/image/observability")return ok({images:{total:1,items:[{action:"image.generate",state:"succeeded",created_at:"2026-10-07T03:00:00Z",elapsed_ms:1200}]},content:{available:true,works_count:2,candidate_count:1,updated_at:"2026-10-07T04:00:00Z"}});
      if(endpoint===ROLE+"/image-materials")return ok(inventory());
      if(endpoint===ROLE+"/image-materials/album")return ok({items:structuredClone(album),next_cursor:null});
      return fail("invalid_response");
    }
    posts.push({endpoint,body:structuredClone(body)});
    if(endpoint===ROLE+"/config/configure"){
      const key=endpoint+"|"+body.operation_key;
      if(receipts.has(key))return ok({...receipts.get(key),result:{...receipts.get(key).result,replayed:true}});
      const current=sections[body.section];
      if(body.section==="image.output"&&sceneConflict){sceneConflict=false;current.edit_token="b".repeat(64);return fail("configuration_revision_conflict");}
      if(!current||current.edit_token!==body.edit_token)return fail("configuration_revision_conflict");
      assert.ok(Object.keys(body.changes).every(key=>key in current.settings));
      Object.assign(current.settings,body.changes);current.edit_token=posts.length.toString(16).padStart(64,"0");
      const result={result:{operation_id:"saved-"+posts.length,replayed:false},status:{sections:structuredClone(sections)}};
      receipts.set(key,result);return ok(result);
    }
    if(endpoint===ROLE+"/model/image/output/save"){
      if(body.revision!=="revision-"+backendRevision)return fail("model_image_output_conflict");
      backend=structuredClone(body.changes);backendRevision++;return ok(snapshot());
    }
    if(endpoint===ROLE+"/model/image/output/test"){assert.ok(body.operation_key);return ok({status:"succeeded",elapsed_ms:140});}
    if(endpoint===ROLE+"/image-materials/references/order"){
      if(orderConflict){orderConflict=false;references.push({...structuredClone(references[0]),asset_id:"ref-concurrent",label:"新加入参考"});return fail("reference_order_conflict");}
      const current=references.filter(item=>item.state==="active");
      if(JSON.stringify(body.expected_asset_ids)!==JSON.stringify(current.map(item=>item.asset_id)))return fail("reference_order_conflict");
      assert.deepEqual([...body.asset_ids].sort(),current.map(item=>item.asset_id).sort());
      const ordered=body.asset_ids.map(id=>current.find(item=>item.asset_id===id));
      references.splice(0,references.length,...ordered,...references.filter(item=>item.state!=="active"));return ok({asset_ids:body.asset_ids});
    }
    if(endpoint===ROLE+"/image-materials/references/update"){
      const item=references.find(item=>item.asset_id===body.asset_id);
      if(item.revision!==body.expected_revision)return fail("reference_revision_conflict");
      if(body.usage.base_identity)for(const other of references)if(other.asset_id!==item.asset_id&&other.usage.base_identity){other.usage.base_identity=false;other.revision++;}
      Object.assign(item,{label:body.label,roles:body.roles,usage:body.usage,revision:item.revision+1});return ok(structuredClone(item));
    }
    if([ROLE+"/image-materials/references/upload",ROLE+"/image-materials/references"].includes(endpoint)){
      const item={asset_id:"ref-added-"+posts.length,label:body.label,roles:body.roles,usage:usage(),revision:1,state:"active",mime_type:"image/png",size_bytes:24,thumbnail:body.data_url||imageUrl};
      references.push(item);return ok(structuredClone(item));
    }
    if(endpoint===ROLE+"/image-materials/wardrobe"){
      const item={...body,item_id:"wardrobe-added-"+posts.length,revision:1,state:"active"};wardrobe.push(item);return ok(structuredClone(item));
    }
    if(endpoint===ROLE+"/image-materials/daily-outfit"){
      daily={...structuredClone(wardrobe.find(item=>item.item_id===body.item_id)),local_date:"2026-10-07"};return ok(structuredClone(daily));
    }
    if(endpoint===ROLE+"/image-materials/references/withdraw"){
      const item=references.find(item=>item.asset_id===body.asset_id);if(item.revision!==body.expected_revision)return fail("reference_revision_conflict");
      item.state="withdrawn";item.revision++;return ok({withdrawn:true});
    }
    if(endpoint===ROLE+"/image-materials/wardrobe/withdraw"){
      wardrobe.find(item=>item.item_id===body.item_id).state="withdrawn";if(daily?.item_id===body.item_id)daily=null;return ok({withdrawn:true});
    }
    return fail("invalid_response");
  }
  const server=http.createServer(async(request,response)=>{
    try{
      response.setHeader("access-control-allow-origin","*");
      const url=new URL(request.url,"http://127.0.0.1");
      if(url.pathname==="/qa"){
        response.setHeader("access-control-allow-origin","*");response.setHeader("content-type","application/json; charset=utf-8");
        const chunks=[];for await(const chunk of request)chunks.push(chunk);
        response.end(JSON.stringify(api(request.method,url.searchParams.get("endpoint"),chunks.length?JSON.parse(Buffer.concat(chunks).toString("utf8")):{},JSON.parse(url.searchParams.get("query")||"{}"))));return;
      }
      if(url.pathname==="/dashboard"){
        response.setHeader("content-type","text/html; charset=utf-8");response.end(`<!doctype html><meta charset="utf-8"><style>body{margin:0}iframe{width:100%;height:100vh;border:0;display:block}</style><iframe title="陪伴小屋" sandbox="allow-scripts allow-forms" src="${PREFIX}index.html?asset_token=${TOKEN}#manage/output"></iframe>`);return;
      }
      if(url.searchParams.get("asset_token")!==TOKEN){response.writeHead(401).end();return;}
      if(url.pathname==="/api/plugin/page/bridge-sdk.js"){
        response.setHeader("content-type","text/javascript; charset=utf-8");response.end(`const outputQA=(method,endpoint,body,query)=>fetch('/qa?endpoint='+encodeURIComponent(endpoint)+'&query='+encodeURIComponent(JSON.stringify(query||{})),{method,...(method==='POST'?{body:JSON.stringify(body)}:{})}).then(r=>r.json());window.AstrBotPluginPage={ready:async()=>({pluginName:'${HOST}'}),onContext:()=>()=>{},apiGet:(endpoint,query)=>outputQA('GET',endpoint,null,query),apiPost:(endpoint,body)=>outputQA('POST',endpoint,body)};`);return;
      }
      if(!url.pathname.startsWith(PREFIX)){response.writeHead(404).end();return;}
      const relative=decodeURIComponent(url.pathname.slice(PREFIX.length)),file=path.resolve(PAGE,relative);
      if(!file.startsWith(PAGE+path.sep)){response.writeHead(403).end();return;}
      if(relative==="assets/chengxi.jpg"){imageRequests.push(request.url);if(failImage){response.writeHead(404).end();return;}}
      const types={".html":"text/html; charset=utf-8",".css":"text/css; charset=utf-8",".js":"text/javascript; charset=utf-8",".jpg":"image/jpeg",".png":"image/png",".gif":"image/gif",".woff2":"font/woff2"};
      response.setHeader("content-type",types[path.extname(file)]||"application/octet-stream");response.setHeader("cache-control","no-store");response.end(fixture.assets[relative]||await fs.readFile(file));
    }catch(error){response.writeHead(500).end(String(error));}
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));let browser;
  try{
    browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH});
    const page=await browser.newPage({viewport:{width:1559,height:1000},reducedMotion:"reduce"});page.setDefaultTimeout(12000);
    const errors=[];page.on("pageerror",error=>errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/dashboard`);
    const frame=page.frames().find(item=>item.url().includes(PREFIX));assert.ok(frame);
    const assertTitle=async()=>assert.equal(await frame.locator(".shell-panel-head h1, .shell-panel-head h2").first().innerText(),"产出");
    const open=async id=>{
      if(await frame.locator(".output-back-link").isVisible())await frame.locator(".output-back-link").click();
      await frame.locator(id==="capabilities"?'[data-output-open="capabilities"]':`[data-output-tab="${id}"]`).first().click();await assertTitle();
    };
    const screenshots=[];
    const capture=async name=>{
      await frame.evaluate(async()=>{await document.fonts.ready;window.scrollTo(0,0);});
      const height=await frame.evaluate(()=>document.documentElement.scrollHeight);
      await page.setViewportSize({width:page.viewportSize().width,height:Math.max(1000,height)});
      await frame.evaluate(()=>window.scrollTo(0,0));
      const buffer=await frame.locator("#shellPanel").screenshot({path:path.join(output,name+".png")});
      const pixels=await frame.evaluate(async data=>{
        const image=new Image();image.src="data:image/png;base64,"+data;await image.decode();
        const canvas=document.createElement("canvas");canvas.width=image.width;canvas.height=image.height;
        const context=canvas.getContext("2d");context.drawImage(image,0,0);
        const colors=new Set(),bottomColors=new Set(),pixels=context.getImageData(0,0,canvas.width,canvas.height).data;
        for(let y=0;y<canvas.height;y+=4)for(let x=0;x<canvas.width;x+=4){
          const offset=(y*canvas.width+x)*4,color=(pixels[offset]<<16)|(pixels[offset+1]<<8)|pixels[offset+2];
          colors.add(color);if(y>=canvas.height*.8)bottomColors.add(color);
        }
        return {width:canvas.width,height:canvas.height,colors:colors.size,bottomColors:bottomColors.size};
      },buffer.toString("base64"));
      assert.ok(pixels.colors>32&&pixels.bottomColors>8,`blank or clipped screenshot ${name}: ${JSON.stringify(pixels)}`);
      screenshots.push({name,...pixels});
    };
    const saveCard=async id=>{await frame.locator(`[data-wb-card="${id}"] .wb-save`).click();await frame.locator(`[data-wb-card="${id}"] .wb-save.is-saved`).waitFor();};
    const toggleWorkbenchField=async selector=>{const input=frame.locator(selector);await input.locator("xpath=..").click();assert.equal(await input.isChecked(),true,`toggle ${selector}`);};
    const saveBackend=async()=>{await frame.locator('[data-output-native-action="save"]:visible').first().click();await frame.locator('[data-output-native-notice]:visible').first().filter({hasText:"生图后端已保存"}).waitFor();};
    await frame.locator('.output-image-frame[data-image-state="ready"]').waitFor();
    await frame.locator('#imageControlWorkspace [data-wb-field="group_max_daily"]').waitFor();await assertTitle();
    await frame.locator('[data-wb-field="group_max_daily"]').fill("2");await saveCard("quota");assert.equal(sections.quota.settings.group_max_daily,2);
    await frame.locator('[data-wb-field="group_nsfw_image_review_mode"][value="dual"]').check();await saveCard("image_review");assert.equal(sections.image_review.settings.group_nsfw_image_review_mode,"dual");
    await frame.locator('[data-output-mode="image"]').focus();await page.keyboard.press("ArrowRight");await frame.locator("#output-preview-story").waitFor({state:"visible"});
    await frame.locator('.output-preview-action[data-output-open="creation"]').click();await assertTitle();
    await frame.locator('[data-wb-field="direction_prompt"]').fill("窗边雨夜，短篇随笔");await saveCard("content.authoring");assert.equal(sections["content.authoring"].settings.direction_prompt,"窗边雨夜，短篇随笔");
    await open("capabilities");
    await frame.getByText("端点按列表顺序排列",{exact:false}).waitFor();
    await frame.getByText("会真实提交一张图片生成请求",{exact:false}).waitFor();
    await frame.locator('[data-output-native-field="endpoints.0.platform"]').waitFor();
    assert.equal(await frame.locator('[data-output-native-field="endpoints.0.base_url"]').getAttribute("placeholder"),"https://api.example.com/v1");
    assert.match(await frame.locator('[data-output-native-action="test"][data-output-endpoint="endpoint-a"]').getAttribute("title"),/可能产生费用/);
    await frame.locator('[data-output-native-action="test"][data-output-endpoint="endpoint-a"]').filter({hasText:"生成测试图"}).waitFor();
    const platform=frame.locator('[data-output-native-field="endpoints.0.platform"]');
    await platform.focus();await platform.selectOption("minimax");assert.equal(await platform.evaluate(node=>document.activeElement===node),true);
    await frame.getByText("MiniMax 使用 /image_generation",{exact:false}).waitFor();await platform.selectOption("openai");
    await frame.locator('[data-output-native-field="endpoints.0.name"]').fill("真实保存主端点");await frame.locator('[data-output-native-action="add"]').click();
    await frame.locator('[data-output-native-field="endpoints.1.name"]').fill("备用端点");await frame.locator('[data-output-native-action="up"]').last().click();await saveBackend();assert.equal(backend.endpoints[0].name,"备用端点");
    await frame.locator('[data-output-native-action="test"][data-output-endpoint="endpoint-a"]').click();await frame.locator(".output-endpoint-result").filter({hasText:"最近测试通过"}).waitFor();
    await frame.locator('[data-output-native-action="remove"]').first().click();await saveBackend();assert.equal(backend.endpoints.length,1);
    await frame.locator('[data-output-native-action="manage-scenes"]').click();await frame.locator('[data-output-scene-field="name"]').first().fill("睡前卧室");await frame.locator('[data-output-native-action="add-scene"]').click();
    await frame.locator('[data-output-scene-field="name"]').last().fill("雨夜窗边");await frame.locator('[data-output-scene-field="prompt"]').last().fill("rainy window, quiet evening");sceneConflict=true;await frame.locator('[data-output-native-action="save-scenes"]').click();
    await frame.locator("#outputScenePresetEditor [role=status]").filter({hasText:"配置已经更新"}).waitFor();
    await frame.locator('#outputScenePresetEditor [data-output-native-action="refresh-preferences"]').click();
    await frame.locator("#outputScenePresetEditor [role=status]").filter({hasText:"已读取最新配置"}).waitFor();
    assert.equal(await frame.locator('[data-output-scene-field="name"]').last().inputValue(),"雨夜窗边");
    await frame.locator('[data-output-native-action="save-scenes"]').click();
    await frame.locator("#outputScenePresetEditor [role=status]").filter({hasText:"场景预设已保存"}).waitFor();
    assert.equal(JSON.parse(sections["image.output"].settings.scene_presets_json).at(-1).name,"雨夜窗边");assert.ok(posts.some(item=>item.endpoint===ROLE+"/config/configure"&&item.body.section==="image.output"&&item.body.changes.scene_presets_json));
    await frame.locator('[data-output-native-action="remove-scene"]').last().click();await frame.locator('[data-output-native-action="save-scenes"]').click();await frame.locator("#outputScenePresetEditor [role=status]").filter({hasText:"场景预设已保存"}).waitFor();
    await frame.locator('[data-wb-field="prompt_format"][value="natural"]').check();
    await frame.locator('[data-wb-field="style"][value="anime"]').check();
    await frame.locator('[data-wb-field="negative_mode"][value="merge"]').check();
    await frame.locator('[data-wb-field="selfie_fixed_prompt"]').fill("stable facial features, quiet evening");await saveCard("image.output");
    assert.equal(sections["image.output"].settings.prompt_format,"natural");assert.equal(sections["image.output"].settings.style,"anime");assert.equal(sections["image.output"].settings.negative_mode,"merge");
    await frame.locator('[data-output-native-action="manage-scenes"]').click();
    await open("materials");
    await toggleWorkbenchField('[data-wb-card="photo.reference"] [data-wb-field="enabled"]');await toggleWorkbenchField('[data-wb-field="daily_outfit_enabled"]');await saveCard("photo.reference");
    await toggleWorkbenchField('[data-wb-card="wardrobe"] [data-wb-field="enabled"]');await saveCard("wardrobe");
    await frame.locator('[data-im-edit-ref="ref-a"]').first().click();await frame.locator('[data-im-edit-usage="base_identity"]').check();await frame.locator('[data-im-quick-roles="identity"]').click();
    await frame.locator('[data-im-edit-usage="outfit_category"]').selectOption("home");await frame.locator('[data-im-edit-usage="outfit_lock"]').selectOption("reference");await frame.locator('[data-im-edit-usage="preferred_scene"]').selectOption("home-night");
    await frame.locator('[data-im-edit-usage="scenes"]').fill("居家，卧室");await frame.locator('[data-im-save-ref="ref-a"]').click();await frame.locator(".image-materials-notice").filter({hasText:"参考图信息已更新"}).waitFor();assert.ok(posts.some(item=>item.endpoint===ROLE+"/image-materials/references/update"));
    assert.equal(references.find(item=>item.asset_id==="ref-a").usage.base_identity,true);assert.ok(references.find(item=>item.asset_id==="ref-a").roles.includes("outfit"));
    await frame.locator('[data-im-move-ref="down"][data-im-asset="ref-a"]').click();orderConflict=true;await frame.locator('[data-im-save-gallery]').click();await frame.locator(".image-materials-notice").filter({hasText:"图库内容已更新"}).waitFor();
    await frame.locator('[data-im-save-gallery]').click();await frame.locator(".image-materials-notice").filter({hasText:"图库顺序已保存"}).waitFor();assert.deepEqual(references.map(item=>item.asset_id),["ref-b","ref-a","ref-c","ref-concurrent"]);
    await frame.locator('[data-im-toggle-upload]').click();await frame.locator('[data-im-upload-file]').setInputFiles({name:"上传参考.jpg",mimeType:"image/jpeg",buffer:await fs.readFile(path.join(PAGE,"assets/chengxi.jpg"))});
    await frame.locator('[data-im-upload]').click();await frame.locator(".image-materials-notice").filter({hasText:"已添加到当前角色"}).waitFor();
    await frame.locator('[data-im-select="image-1"]').click();await frame.locator('[data-im-reference-label]').fill("相册登记");await frame.locator('[data-im-register]').click();await frame.locator(".image-materials-notice").filter({hasText:"已登记为参考素材"}).waitFor();
    await frame.locator('[data-im-toggle-wardrobe]').click();await frame.locator('[data-im-wardrobe-name]').fill("白色针织衫");await frame.locator('[data-im-wardrobe-asset]').selectOption("ref-a");await frame.locator('[data-im-wardrobe-submit]').click();await frame.locator(".image-materials-notice").filter({hasText:"衣柜条目已加入"}).waitFor();
    const itemId=wardrobe.at(-1).item_id;await frame.locator(`[data-im-daily="${itemId}"]`).click();await frame.locator(".image-materials-daily").filter({hasText:"白色针织衫"}).waitFor();assert.equal(daily.item_id,itemId);
    await frame.locator('[data-im-refresh]').first().click();await frame.locator('[data-im-reference-card="ref-a"]').waitFor();assert.deepEqual(await frame.locator('[data-im-reference-card]').evaluateAll(nodes=>nodes.map(node=>node.dataset.imReferenceCard)),references.filter(item=>item.state==="active").map(item=>item.asset_id));
    await frame.locator('[data-im-edit-ref="ref-a"]').first().click();assert.equal(await frame.locator('[data-im-edit-usage="outfit_category"]').inputValue(),"home");
    for(const width of [1559,960,390,320]){
      await page.setViewportSize({width,height:1000});await frame.evaluate(width=>{document.documentElement.dataset.typeScale=width<1000?"xl":"m";},width);
      for(const id of ["visual","materials","creation","capabilities"]){
        await open(id);
        if(id==="visual")await frame.locator('[data-output-mode="image"]').click();
        assert.equal(await frame.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`overflow ${id} ${width}`);
        assert.equal(await frame.locator('[data-wb-row="scene_presets_json"]').isVisible(),false);
        const switchRows=await frame.locator('.output-page .wb-field:visible:has(.wb-switch) .wb-field-main').evaluateAll(nodes=>nodes.map(node=>getComputedStyle(node).flexDirection));
        assert.ok(switchRows.every(direction=>direction==="row"),`switch alignment ${id} ${width}`);
        const clipped=await frame.locator(".output-page button:visible").evaluateAll(nodes=>nodes.filter(node=>node.scrollWidth>node.clientWidth+2).map(node=>node.textContent));assert.deepEqual(clipped,[],`clipped ${id} ${width}`);
        await capture(`${id}-${width}`);
      }
    }
    await page.setViewportSize({width:1559,height:1000});for(const theme of ["night","contrast","dusk"]){await frame.evaluate(theme=>{document.documentElement.dataset.theme=theme;},theme);await open("visual");await capture(`visual-${theme}`);}
    await page.reload();const reloaded=page.frames().find(item=>item.url().includes(PREFIX));await reloaded.locator('[data-wb-field="group_max_daily"]').waitFor();assert.equal(await reloaded.locator('[data-wb-field="group_max_daily"]').inputValue(),"2");
    failImage=true;await page.reload();const failed=page.frames().find(item=>item.url().includes(PREFIX));await failed.locator('.output-image-frame[data-image-state="unavailable"]').waitFor();assert.equal(await failed.locator('[data-output-preview-image]').isVisible(),false);
    assert.ok(imageRequests.length);assert.ok(imageRequests.every(url=>new URL(url,"http://127.0.0.1").searchParams.get("asset_token")===TOKEN));assert.deepEqual(errors,[]);
    const report={result:"PASS",posts:posts.length,viewports:[1559,960,390,320],checks:["host sandbox and token","shared title","quota and review save","authoring direction","backend queue and endpoint test","scene edit and removal","scene conflict recovery preserves draft","style and prompt preferences","usage and gallery conflict","upload and album registration","wardrobe and daily outfit","theme and mobile layout","reload persistence","image fallback","nonblank full-panel screenshots"],screenshots,output};
    await fs.writeFile(path.join(output,"report.json"),JSON.stringify(report,null,2)+"\n","utf8");
    console.log(JSON.stringify({...report,screenshots:screenshots.length},null,2));
  }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
