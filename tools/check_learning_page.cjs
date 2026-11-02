/* Learning workspace acceptance with AstrBot's asset rewriting and bridge SDK. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const http = require("node:http");
const {spawnSync} = require("node:child_process");
const {chromium} = require("playwright");
const ROOT = path.resolve(process.env.COMPANION_SOURCE_ROOT || path.join(__dirname,".."));
const HOST_ROOT = process.env.COMPANION_HOST_ROOT || path.resolve(ROOT,"../../.."), PAGE = path.join(ROOT,"pages","陪伴小屋");
const HOST = "astrbot_plugin_private_companion", TOKEN = "synthetic-learning-qa";
const PREFIX = "/api/plugin/page/content/"+HOST+"/"+encodeURIComponent("陪伴小屋")+"/";
const ROLE = "host/targets/first/roles/role-a", GROUP_ROLE = "host/groups/group-a/roles/role-a";
const ok = data=>({status:"ok",data}), fail = code=>({status:"error",message:code,data:{code}});

function hostAssets() {
  const script = `
import json, os, tempfile
from pathlib import Path
from types import SimpleNamespace
with tempfile.TemporaryDirectory(prefix="learning-page-qa-") as data_root:
    os.environ["ASTRBOT_ROOT"] = data_root
    from astrbot.dashboard.services.plugin_page_service import PluginPageService
    from tools.host_page_render import render_css, render_html
    from companion_v2.configuration.registry import FieldRegistry
    from companion_v2.configuration.contract import contract_of
    from companion_v2.expression.configuration import configuration_fields
    from companion_v2.role_work.growth.configuration import skill_configuration_fields
    from companion_v2.storage.semantic import SemanticStore
    root, sections = Path.cwd(), {}
    page = root / "pages" / "陪伴小屋"
    service = PluginPageService(SimpleNamespace(plugin_store_path=str(root.parent)))
    args, query = ("astrbot_plugin_private_companion", "陪伴小屋"), {"asset_token":"synthetic-learning-qa"}
    assets = {"index.html":render_html(service,(page/"index.html").read_text(encoding="utf-8"),*args,file_name="index.html",theme="light",extra_query_params=query)}
    for css in page.glob("*.css"):
        assets[css.name] = render_css(service,css.read_text(encoding="utf-8"),*args,file_name=css.name,extra_query_params=query)
    for fields in (configuration_fields(), skill_configuration_fields()):
        group = fields[0].group_id
        registry = FieldRegistry(version="learning-qa.v1", fields=fields)
        settings = {field.field_id.removeprefix(group+"."):field.default for field in fields}
        sections[group] = {"group_id":group,"scope":"window","layers":[layer.value for layer in fields[0].layers],"settings":settings,"fields":contract_of(registry,group),"edit_token":"a"*64,"provenance":{key:{"layer":None,"explicit":False,"revision":0} for key in settings}}
    store = SemanticStore(Path(data_root))
    print(json.dumps({"assets":assets,"sections":sections,"semantic":store.settings()},ensure_ascii=False))
`;
  const result = spawnSync(path.join(HOST_ROOT,"backend/python/python.exe"),["-X","utf8","-c",script],{
    cwd:ROOT,encoding:"utf8",maxBuffer:5*1024*1024,
    env:{...process.env,PYTHONPATH:[ROOT,path.join(HOST_ROOT,"backend/app")].join(path.delimiter),PYTHONIOENCODING:"utf-8"},
  });
  assert.equal(result.status,0,result.stderr);return JSON.parse(result.stdout);
}

(async()=>{
  const fixture=hostAssets(), sections=fixture.sections, semanticSettings=fixture.semantic;
  const output=path.resolve(process.argv[2]||path.join(HOST_ROOT,"verification","learning-ui-qa"));await fs.mkdir(output,{recursive:true});
  const sdk=await fs.readFile(path.join(HOST_ROOT,"backend/app/astrbot/dashboard/plugin_page_bridge.js"),"utf8");
  const now=new Date().toISOString(), future=new Date(Date.now()+86400000).toISOString(), past=new Date(Date.now()-3600000).toISOString();
  const growth=["proposed","active","paused","achieved","retired"].map((state,index)=>({
    growth_id:"growth-"+index,state,kind:index===1?"skill":"goal",title:["学会独立规划周末采购","掌握基础和面与发酵","坚持每天散步 30 分钟","能自己做三人晚餐","练习草图"][index],
    revision:1,description:"按目前的实际进展推进",progress_note:index===1?"已独立完成一次和面，继续练习发酵":"",created_at:now,updated_at:now,
    source_count:2,proposed_by:"model",confirmed_by:index?"dashboard:qa":null,changed_by:"model",
    actions:[],view_token:"growth-token-"+index,unavailable_reason:index===4?"source_revoked":null,
  }));
  function growthActions(row){row.actions=row.unavailable_reason?["retire"]:{proposed:["confirm","revise","withdraw","retire"],active:row.kind==="goal"?["revise","pause","complete","retire"]:["revise","pause","retire"],paused:["resume","revise","retire"],achieved:["retire"],retired:[]}[row.state];return row;}
  const progress=[{review_id:"progress-a",growth_id:"growth-1",growth_title:growth[1].title,progress_note:"已经独立完成了三次练习",base_revision:1,created_at:now,actions:["accept","reject"],view_token:"progress-token",evidence:["已确认的练习回执"],state:"pending"}];
  const signals=[
    {signal_id:"signal-a",dimension:"sentence_rhythm",proposal:"延续短句节奏，重点句单独成行",observation:"最近的回复多以短句收束",state:"adopted",injections:46,last_injected_at:now},
    {signal_id:"signal-b",dimension:"diction",proposal:"保持平称与昵称混用，避免敬语化",observation:"最近回复持续使用熟悉称呼",state:"adopted",injections:31,last_injected_at:now},
    {signal_id:"signal-c",dimension:"formatting",proposal:"减少列表式排版",observation:"列表在闲聊中偏多",state:"retired",injections:12},
    {signal_id:"signal-pending",dimension:"punctuation",proposal:"减少连续感叹号",observation:"感叹号偏密",state:"pending",injections:0},
    {signal_id:"signal-invalid",dimension:"other",proposal:"不可见旧内容",observation:"不可见旧观察",state:"retired",injections:3,unavailable_reason:"expression_sample_changed"},
  ].map((row,index)=>({...row,run_id:"run-"+index,created_at:now,decided_at:now,confidence:.8,evidence:["已送达的角色回复原文"],view_token:"style-token-"+index}));
  const guidance=[
    {item_id:"review-active",kind:"keep",state:"accepted",observation:"回执与表达相符",next_action:"回复请求前先确认授权来源",automatic:true,expires_at:future},
    {item_id:"review-pending",kind:"ask_user",state:"pending",observation:"散步计划仍待确认",next_action:"确认用户是否愿意提前安排散步",automatic:false,expires_at:future},
    {item_id:"review-invalid",kind:"try_next_time",state:"accepted",automatic:true,expires_at:future,unavailable_reason:"self_review_evidence_changed"},
    {item_id:"review-expired",kind:"keep",state:"accepted",automatic:true,expires_at:past,next_action:"先核对日程"},
  ].map(row=>({...row,run_id:"review-run",created_at:now,confidence:.8,evidence:row.unavailable_reason?[]:["可核验的插件工作回执"],view_token:"review-token",actions:row.state==="pending"?["accept","reject"]:[]}));
  const semanticItems=[{id:"slang-a",kind:"slang",enabled:true,revision:2,vector_ready:true,payload:{term:"绝绝子",meaning:"极好、棒",usage:"夸人或夸东西",collection_source:"group_capture"}},
    {id:"reaction-a",kind:"reaction",enabled:true,revision:1,vector_ready:false,payload:{name:"赞同",description:"点头认可",visible_text:"嗯嗯",tags:"赞同",emotions:"平静",intents:"确认"},image_url:PREFIX+"assets/chengxi.jpg?asset_token="+TOKEN}];
  const sampleRows=Array.from({length:30},(_,index)=>({handle:"input:private-"+index,
    text:`嗯哼，那就这么定啦。${index}`,sender_id:"200",received_at:now,state:"pending",decided_at:null,
    view_token:"sample-token-"+index,actions:["approve","reject"]}));
  const groupSamples=Array.from({length:25},(_,index)=>({...sampleRows[index],handle:"input:group-"+index,sender_id:"201"}));
  const learnJobs=new Map();let failSamples=false,holdNextSamples=false,releaseSampleRead=null;
  const posts=[],calls=[],errors=[],receipts=new Map();let failRead=false;
  function api(method,endpoint,body,query){
    const parsed=new URL(endpoint,"http://qa.invalid"),route=parsed.pathname.replace(/^\//,"");
    query={...Object.fromEntries(parsed.searchParams),...query};calls.push({method,route,query});
    if(method==="GET"){
      if(route==="page/targets")return ok({installation_id:"learning-qa",operator_id:"dashboard:qa",targets:[
        {target_id:"first",scope_kind:"private",participant_id:"200",platform_id:"qq-main",account_id:"100",bot_id:"bot-a",roles:[{role_id:"role-a",role_name:"星缘",growth_api:"/"+HOST+"/"+ROLE+"/growth",reply_api:"/"+HOST+"/"+ROLE+"/reply",config_api:"/"+HOST+"/"+ROLE+"/config"}]},
        {target_id:"group-a",scope_kind:"group",group_id:"group-a",platform_id:"qq-main",account_id:"100",bot_id:"bot-a",roles:[{role_id:"role-a",role_name:"星缘",reply_api:"/"+HOST+"/"+GROUP_ROLE+"/reply",group_memory_api:"/"+HOST+"/"+GROUP_ROLE+"/memory"}]},
      ]});
      if(route==="integration/manifest")return ok({extensions:[],providers:[],core:[],summary:{connected_count:0,extension_ready_count:0,provider_count:0}});
      if(failRead&&route.endsWith("/style"))return fail("staging_not_initialized");
      if(route.endsWith("/growth/evidence"))return ok({items:[{text:"今天已经完成了一次和面，发酵还需要再练习",issued_at:now}]});
      if(route.endsWith("/growth/progress"))return ok({items:progress.filter(row=>row.state==="pending"),next_cursor:null});
      if(route.endsWith("/growth/self-review"))return ok({items:guidance.filter(row=>query.state==="pending"?row.state==="pending":query.state==="active"?row.state==="accepted":row.state==="rejected"||row.expires_at<=now),next_cursor:null,policy:{window_hours:24,minimum_evidence:1,empty_retry_seconds:3600,scan_seconds:86400}});
      if(route.endsWith("/growth"))return ok({items:growth.map(growthActions),next_cursor:null,access:{settings:{read_enabled:true,proposals_enabled:true},effective:{read_enabled:{allowed:true},proposals_enabled:{allowed:true}},edit_token:"access-token"}});
      if(route.endsWith("/reply/style"))return ok({items:signals.filter(row=>row.state===query.state&&(!query.dimension||row.dimension===query.dimension)).map(row=>({...row,proposal:row.unavailable_reason?null:row.proposal,observation:row.unavailable_reason?null:row.observation,evidence:row.unavailable_reason?[]:row.evidence,actions:row.state==="pending"?["adopt","reject"]:row.state==="adopted"?["retire"]:row.state==="retired"&&!row.unavailable_reason?["restore"]:[]})),next_cursor:null});
      if(route.endsWith("/reply/style/samples")){
        if(failSamples)return fail("expression_sample_unavailable");
        const group=route.includes("/groups/"),rows=group?groupSamples:sampleRows,settings=sections["expression.learning"].settings;
        const count=state=>rows.filter(row=>row.state===state).length,eligible=row=>row.state==="approved"||!settings.sample_review_enabled&&row.state==="pending";
        const offset=query.after_id?rows.findIndex(row=>row.handle===query.after_id)+1:0;
        const matching=rows.slice(offset).filter(row=>query.state==="all"||!query.state||row.state===query.state),limit=Number(query.limit||24);
        const job=learnJobs.get(route),running=job&&Date.now()-job<750;
        const result=ok({items:matching.slice(0,limit).map(row=>({...row,eligible:eligible(row)})),counts:{pending:count("pending"),approved:count("approved"),rejected:count("rejected")},
          next_cursor:matching.length>limit?matching[limit-1].handle:null,sample_review_enabled:settings.sample_review_enabled,
          runtime:{running:!!running,last_result:job?{state:running?"running":"succeeded"}:null,
            learning_enabled:group?settings.group_learning_enabled:settings.private_learning_enabled,
            application_enabled:group?settings.group_application_enabled:settings.private_application_enabled,
            minimum_samples:group?settings.group_min_samples:settings.min_samples,eligible_samples:Math.min(settings.sample_limit,rows.filter(eligible).length),
            new_messages:group?25:null,required_new_messages:group?settings.group_new_messages:null,
            group_batches_today:0,group_daily_batch_limit:settings.group_daily_batch_limit}});
        if(holdNextSamples){holdNextSamples=false;return new Promise(resolve=>{releaseSampleRead=()=>resolve(result);});}
        return result;
      }
      if(route.endsWith("/reply/expression"))return ok(structuredClone(sections["expression.learning"]));
      if(route.endsWith("/config"))return ok({sections:{"role.growth.skills":structuredClone(sections["role.growth.skills"])}});
      if(route==="semantic"){
        const rows=semanticItems.filter(row=>row.kind===(query.kind||"reaction"));
        return ok({state:semanticSettings.enabled?"ready":"disabled",selection_ready:true,settings:structuredClone(semanticSettings),sessions:query.target_id==="group-a"?["group-a"]:[],session_id:query.session_id||"group-a",providers:{analysis:[{id:"analysis-qa",name:"gemini-qa"}],embedding:[{id:"embedding-qa",name:"text-embedding-3-small"}],rerank:[{id:"rerank-qa",name:"reranker-qa"}]},catalog:{slang:{total:1},reaction:{total:1}},items:structuredClone(rows),...(query.id?{item:structuredClone(rows.find(row=>row.id===query.id))}:{}),next_after:null});
      }
      return ok({items:[],next_cursor:null});
    }
    posts.push({route,body:structuredClone(body),query});
    if(route.endsWith("/reply/expression/configure")||route.endsWith("/config/configure")){
      const group=route.includes("/expression/")?"expression.learning":body.section;
      const current=sections[group],key=route+"|"+body.operation_key;
      if(receipts.has(key))return ok(receipts.get(key));
      assert.equal(body.edit_token,current.edit_token);assert.ok(body.operation_key);
      Object.assign(current.settings,body.changes);current.edit_token=posts.length.toString(16).padStart(64,"0");
      const status=group==="expression.learning"?structuredClone(current):{sections:{[group]:structuredClone(current)}};
      const result={result:{replayed:false,operation_id:"saved-"+posts.length},status};receipts.set(key,result);return ok(result);
    }
    if(route.endsWith("/growth/progress/command")){progress.find(row=>row.review_id===body.review_id).state=body.action==="accept"?"accepted":"rejected";return ok({state:body.action});}
    if(route.endsWith("/growth/self-review/command")){guidance.find(row=>row.item_id===body.item_id).state=body.action==="accept"?"accepted":"rejected";return ok({state:body.action});}
    if(route.endsWith("/reply/style/command")){const row=signals.find(row=>row.signal_id===body.signal_id);row.state={adopt:"adopted",retire:"retired",restore:"adopted",reject:"rejected"}[body.action];return ok({state:row.state});}
    if(route.endsWith("/reply/style/samples/command")){
      const rows=route.includes("/groups/")?groupSamples:sampleRows,row=rows.find(item=>item.handle===body.handle);
      assert.equal(body.view_token,row.view_token);assert.ok(body.operation_key);
      row.state=body.action==="approve"?"approved":"rejected";row.decided_at=now;row.view_token+="-new";
      row.actions=row.state==="approved"?["reject"]:["approve"];return ok({state:row.state});
    }
    if(route.endsWith("/reply/style/learn")){learnJobs.set(route.replace(/learn$/,"samples"),Date.now());return ok({state:"queued"});}
    if(route.endsWith("/growth/command")){
      if(body.action==="configure")return ok({saved:true});
      const row=growth.find(row=>row.growth_id===body.growth_id);assert.equal(body.expected_revision,row.revision);
      if(body.action==="revise")Object.assign(row,{title:body.title,description:body.description,progress_note:body.progress_note});
      else row.state={confirm:"active",pause:"paused",resume:"active",complete:"achieved",retire:"retired",withdraw:"retired"}[body.action];
      row.revision++;return ok({revision:row.revision});
    }
    if(route==="semantic"){
      if(body.action==="configure"){assert.equal(body.revision,semanticSettings.revision);Object.assign(semanticSettings,body.changes);semanticSettings.revision++;return ok({});}
      if(body.action==="edit"){const row=semanticItems.find(row=>row.id===body.id);Object.assign(row.payload,body.values);row.enabled=body.enabled;row.revision++;return ok({});}
      if(body.action==="search")return ok({items:semanticItems.filter(row=>row.kind===body.kind),embedded:1,eligible:1});
      if(body.action==="reindex")return ok({state:"scheduled"});
    }
    return fail("invalid_response");
  }
  const wrapper=`<!doctype html><meta charset="utf-8"><style>body{margin:0}iframe{width:100%;height:100vh;border:0;display:block}</style><iframe id="cottage" title="陪伴小屋" sandbox="allow-scripts allow-forms" src="${PREFIX}index.html?asset_token=${TOKEN}#manage/learning/overview"></iframe><script>
    addEventListener('message',async event=>{const frame=document.getElementById('cottage'),m=event.data;if(event.source!==frame.contentWindow||m.channel!=='astrbot-plugin-page')return;
    const send=data=>frame.contentWindow.postMessage({channel:'astrbot-plugin-page',...data},'*');if(m.kind==='ready'){send({kind:'context',context:{pluginName:'${HOST}',pageName:'陪伴小屋'}});return;}if(m.kind!=='request')return;
    const response=await fetch('/rpc',{method:'POST',body:JSON.stringify(m)});send({kind:'response',requestId:m.requestId,ok:true,data:await response.json()});});</script>`;
  const server=http.createServer(async(req,res)=>{
    try{
      const url=new URL(req.url,"http://127.0.0.1");res.setHeader("cache-control","no-store");
      if(url.pathname==="/"){res.setHeader("content-type","text/html; charset=utf-8");res.end(wrapper);return;}
      if(url.pathname==="/rpc"){
        const chunks=[];for await(const chunk of req)chunks.push(chunk);const m=JSON.parse(Buffer.concat(chunks).toString("utf8"));
        res.setHeader("content-type","application/json; charset=utf-8");res.end(JSON.stringify(await api(m.action==="api:get"?"GET":"POST",m.endpoint,m.body||{},m.params||m.query||{})));return;
      }
      if(url.pathname==="/api/plugin/page/bridge-sdk.js"){res.setHeader("content-type","text/javascript; charset=utf-8");res.end(sdk);return;}
      if(!url.pathname.startsWith(PREFIX)){res.writeHead(404).end();return;}
      assert.equal(url.searchParams.get("asset_token"),TOKEN);
      const relative=decodeURIComponent(url.pathname.slice(PREFIX.length)),file=path.resolve(PAGE,relative);assert.ok(file.startsWith(PAGE+path.sep));
      const mime={".html":"text/html; charset=utf-8",".css":"text/css; charset=utf-8",".js":"text/javascript; charset=utf-8",".jpg":"image/jpeg",".png":"image/png",".woff2":"font/woff2"};
      res.setHeader("content-type",mime[path.extname(file)]||"application/octet-stream");res.end(fixture.assets[relative]||await fs.readFile(file));
    }catch(error){errors.push(String(error));res.writeHead(500).end(String(error));}
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));let browser;
  try{
    browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH});
    const page=await browser.newPage({viewport:{width:1559,height:1000},reducedMotion:"reduce"});page.setDefaultTimeout(12000);page.on("pageerror",error=>errors.push(error.message));
    await page.goto("http://127.0.0.1:"+server.address().port);
    const frame=page.frames().find(item=>item.url().includes(PREFIX));assert.ok(frame);
    const capture=async filename=>{
      const height=await frame.evaluate(()=>Math.max(document.documentElement.scrollHeight,document.body.scrollHeight));
      // Expand only the QA wrapper so content below the iframe viewport is not
      // clipped into blank space by the browser's element screenshot.
      await page.locator('#cottage').evaluate((node,value)=>{node.style.height=`${value}px`;},height);
      try{
        await frame.evaluate(()=>window.scrollTo(0,0));
        const screenshot=path.join(output,filename);await frame.locator('.learning-hub').screenshot({path:screenshot});return screenshot;
      }finally{
        await page.locator('#cottage').evaluate(node=>{node.style.height="100vh";});
        await page.evaluate(()=>window.scrollTo(0,0));
      }
    };
    const wait=async predicate=>{await frame.waitForFunction(predicate);};
    const open=async tab=>{await frame.locator(`.learning-hub-tab[href="#manage/learning/${tab}"]`).click();
      const checks={overview:()=>window.CompanionLearningOverviewPage?.state?.loading===false,review:()=>window.CompanionLearningReviewPage?.state?.loading===false,
        chain:()=>window.CompanionLearningPage?.state?.data?.ready&&!window.CompanionLearningPage.state.data.loading,
        expression:()=>window.CompanionExpressionSignalsPage?.state?.busy===false&&window.CompanionExpressionSamplesPage?.state?.items?.length>0&&!window.CompanionExpressionSamplesPage.state.busy&&document.querySelector('#expressionWorkspace [data-wb-field="window_hours"]'),
        semantic:()=>document.querySelector("#semanticSettings"),reflection:()=>document.querySelector(".sr-effective")};await wait(checks[tab]);};
    await wait(()=>window.CompanionLearningOverviewPage?.state?.loading===false);
    await frame.locator('[data-lh-action="confirm"]').click();await wait(()=>window.CompanionLearningOverviewPage?.state?.growth?.[0]?.state==="active"&&!window.CompanionLearningOverviewPage.state.loading);
    await frame.locator('[data-lh-open="growth-1"]').click();await wait(()=>window.CompanionLearningPage?.state?.data?.evidence?.length===1);
    await frame.locator('.lea-chain-revision [name="title"]').fill("掌握和面与发酵的基础流程");await frame.locator('.lea-chain-revision [type="submit"]').click();
    await frame.locator('.lea-notice').filter({hasText:"修订已完成"}).waitFor();assert.equal(growth[1].title,"掌握和面与发酵的基础流程");
    await frame.locator('[data-wb-word-input="custom_skills"]').fill("烹饪");await frame.locator('[data-wb-word-add="custom_skills"]').click();
    await frame.locator('[data-wb-field="schedule_influence_strength"]').selectOption("2");await frame.locator('#growthConfigWorkspace [data-wb-action="save"]').click();
    await frame.locator('#growthConfigWorkspace .wb-notice').filter({hasText:"已保存"}).waitFor();assert.equal(sections["role.growth.skills"].settings.schedule_influence_strength,2);
    await open("review");await frame.locator('[data-review-filter="growth"]').click();await frame.locator('[data-review-stage="accept"]').click();await frame.locator('[data-review-confirm="yes"]').click();
    await frame.locator('.lh-inline-status').filter({hasText:"已确认申报"}).waitFor();assert.equal(progress[0].state,"accepted");
    await open("expression");await frame.locator('[data-signal-action="retire"][data-id="signal-a"]').click();await frame.locator('[data-signal-confirm="yes"]').click();
    await frame.locator('.lh-inline-status').filter({hasText:"规则已停用"}).waitFor();await frame.locator('[data-signal-action="restore"][data-id="signal-a"]').click();
    await frame.locator('.lh-inline-status').filter({hasText:"规则已恢复启用"}).waitFor();assert.equal(signals[0].state,"adopted");
    assert.equal(await frame.locator('[data-signal-action="restore"][data-id="signal-invalid"]').count(),0);
    assert.equal(await frame.locator('.lh-signal-card').filter({hasText:"不可见旧内容"}).count(),0);
    await frame.locator('#expressionWorkspace [data-wb-field="rules_per_turn"]').fill("6");await frame.locator('#expressionWorkspace [data-wb-field="window_hours"]').fill("96");
    const expressionFields=Object.keys(sections["expression.learning"].settings);
    assert.equal(expressionFields.length,22);
    for(const field of expressionFields)assert.equal(await frame.locator(`#expressionWorkspace [data-wb-field="${field}"]`).count(),1,`reachable expression field: ${field}`);
    await frame.locator('#expressionWorkspace [data-wb-field="learning_mode"]').selectOption("aggressive");
    for(const field of ["sample_review_enabled","group_learning_enabled"]){
      const input=frame.locator(`#expressionWorkspace [data-wb-field="${field}"]`);
      if(!await input.isChecked())await input.locator("..").click();
      assert.equal(await input.isChecked(),true,`toggle enabled: ${field}`);
    }
    await frame.locator('#expressionWorkspace [data-wb-field="group_member_mode"]').selectOption("selected");
    await frame.locator('#expressionWorkspace [data-wb-field="group_member_ids"]').fill("201,202");
    await frame.locator('#expressionWorkspace [data-wb-action="save"]').click();await frame.locator('#expressionWorkspace .wb-notice').filter({hasText:"已保存"}).waitFor();assert.equal(sections["expression.learning"].settings.rules_per_turn,6);
    await wait(()=>!window.CompanionExpressionSamplesPage.state.busy&&window.CompanionExpressionSamplesPage.state.items.length===24);
    assert.equal(sections["expression.learning"].settings.learning_mode,"aggressive");
    assert.equal(sections["expression.learning"].settings.group_member_ids,"201,202");
    const inputHeight=await frame.locator('#expressionWorkspace [data-wb-field="rules_per_turn"]').evaluate(el=>el.getBoundingClientRect().height);
    assert.ok(inputHeight<=60,`compact numeric control: ${inputHeight}`);
    assert.equal(await frame.locator('[data-sample-learn]').isDisabled(),true,"manual review blocks unapproved samples");
    await frame.locator('[data-sample-more]').click();await wait(()=>window.CompanionExpressionSamplesPage.state.items.length===30&&!window.CompanionExpressionSamplesPage.state.busy);
    await frame.locator('[data-sample-action="reject"]').first().click();await frame.locator('.lh-inline-status').filter({hasText:"素材已排除"}).waitFor();
    await frame.locator('[data-sample-filter="rejected"]').click();await frame.locator('[data-sample-action="approve"]').first().waitFor();
    await frame.locator('[data-sample-action="approve"]').first().click();await frame.locator('.lh-inline-status').filter({hasText:"素材已允许学习"}).waitFor();
    await frame.locator('[data-sample-filter="pending"]').click();await wait(()=>!window.CompanionExpressionSamplesPage.state.busy);
    for(let index=0;index<4;index++){
      await frame.locator('[data-sample-action="approve"]').first().click();
      await wait(()=>!window.CompanionExpressionSamplesPage.state.busy&&!window.CompanionExpressionSamplesPage.state.writing);
    }
    assert.equal(await frame.locator('[data-sample-learn]').isEnabled(),true);
    await frame.locator('[data-sample-learn]').click();await wait(()=>window.CompanionExpressionSamplesPage.state.runtime.running===true);
    await wait(()=>window.CompanionExpressionSamplesPage.state.runtime.last_result?.state==="succeeded"&&!window.CompanionExpressionSamplesPage.state.busy);
    assert.equal(await frame.locator('.lh-samples-progress').filter({hasText:"学习完成"}).count(),1);
    const readsBefore=calls.filter(row=>row.route.endsWith('/style')).length;
    assert.ok(readsBefore>0,"learning completion refreshes the rule library");
    holdNextSamples=true;await frame.locator('[data-sample-refresh]').click();
    const holdDeadline=Date.now()+12000;while(!releaseSampleRead){assert.ok(Date.now()<holdDeadline,"held sample read started");await new Promise(resolve=>setImmediate(resolve));}
    await frame.locator('[data-learning-scope-target="expression"]').selectOption("group-a");
    await wait(()=>window.CompanionExpressionSamplesPage.state.items[0]?.handle==="input:group-0"&&!window.CompanionExpressionSamplesPage.state.busy);
    releaseSampleRead();releaseSampleRead=null;
    await frame.waitForTimeout(100);
    assert.ok((await frame.evaluate(()=>window.CompanionExpressionSamplesPage.state.items)).every(row=>row.handle.startsWith("input:group-")),"a stale response cannot replace the new scope");
    await frame.locator('[data-learning-scope-target="expression"]').selectOption("first");await wait(()=>window.CompanionExpressionSamplesPage.state.items[0]?.handle.startsWith("input:private-")&&!window.CompanionExpressionSamplesPage.state.busy);
    failSamples=true;await frame.locator('[data-sample-refresh]').click();await frame.locator('.lh-inline-status.is-error').filter({hasText:"素材"}).waitFor();
    failSamples=false;await frame.locator('[data-sample-refresh]').click();await wait(()=>window.CompanionExpressionSamplesPage.state.items.length===24&&!window.CompanionExpressionSamplesPage.state.busy);
    await open("semantic");await frame.locator('[data-learning-scope-target="semantic"]').selectOption("group-a");await frame.locator('[data-semantic-open="slang-a"]').waitFor();
    await frame.locator('[data-semantic-open="slang-a"]').click();await frame.locator('#semanticEditor [name="usage"]').fill("用于称赞人或物");await frame.locator('#semanticEditor [type="submit"]').click();
    await frame.locator('.semantic-feedback').filter({hasText:"素材已保存"}).waitFor();assert.equal(semanticItems[0].payload.usage,"用于称赞人或物");
    await frame.locator('#semanticSettings [name="timeout_seconds"]').fill("12");await frame.locator('#semanticSettings [type="submit"]').click();
    await frame.locator('.semantic-feedback').filter({hasText:"配置已保存"}).waitFor();assert.equal(semanticSettings.timeout_seconds,12);
    await open("reflection");assert.equal(await frame.locator('.sr-effective .sr-item').count(),1);assert.equal(await frame.locator('.sr-stopped .sr-item').count(),2);
    const screenshots=[];
    for(const width of [1559,960,390,320]){
      await page.setViewportSize({width,height:1000});await frame.evaluate(width=>{document.documentElement.dataset.typeScale=width<1000?"xl":"m";},width);
      for(const tab of ["overview","review","chain","expression","semantic","reflection"]){
        await open(tab);await frame.evaluate(async()=>{await document.fonts.ready;window.scrollTo(0,0);});
        assert.equal(await frame.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`horizontal overflow: ${tab} ${width}`);
        const clipped=await frame.locator('.learning-hub button:visible, .learning-hub-tab').evaluateAll(nodes=>nodes.filter(node=>node.scrollWidth>node.clientWidth+2).map(node=>node.textContent.trim()));assert.deepEqual(clipped,[],`clipped controls: ${tab} ${width}`);
        screenshots.push(await capture(`${tab}-${width}.png`));
      }
    }
    await page.setViewportSize({width:1559,height:1000});const themes=[];
    for(const theme of ["courtyard","night","contrast","dusk"]){
      await frame.evaluate(theme=>{document.documentElement.dataset.theme=theme;},theme);await open("expression");
      themes.push(await frame.locator('.lh-signal-card').first().evaluate(el=>getComputedStyle(el).backgroundColor));
      screenshots.push(await capture(`expression-${theme}.png`));
    }
    assert.ok(new Set(themes).size>1,"learning colors follow the theme");
    failRead=true;await open("expression");await frame.locator('[data-signal-refresh]').click();
    await frame.locator('.lh-inline-status').filter({hasText:"规则暂时无法读取"}).waitFor();failRead=false;
    await frame.locator('[data-signal-refresh]').click();await frame.locator('.lh-signal-card').first().waitFor();
    await page.reload();const reloaded=page.frames().find(item=>item.url().includes(PREFIX));
    await reloaded.locator('.learning-hub-tab[href="#manage/learning/expression"]').click();
    await reloaded.locator('#expressionWorkspace [data-wb-field="rules_per_turn"]').waitFor();
    assert.equal(await reloaded.locator('#expressionWorkspace [data-wb-field="rules_per_turn"]').inputValue(),"6");
    assert.equal(await reloaded.locator('#expressionWorkspace [data-wb-field="learning_mode"]').inputValue(),"aggressive");
    assert.equal(await reloaded.locator('#expressionWorkspace [data-wb-field="sample_review_enabled"]').isChecked(),true);
    await reloaded.evaluate(()=>window.CompanionExpressionSamplesPage.unmount());const sampleReadCount=calls.filter(row=>row.route.endsWith('/style/samples')).length;
    await reloaded.waitForTimeout(200);assert.equal(calls.filter(row=>row.route.endsWith('/style/samples')).length,sampleReadCount,"unmount stops sample polling");
    assert.deepEqual(errors,[]);const report={result:"PASS",reads:calls.filter(row=>row.method==="GET").length,writes:posts.length,viewports:[1559,960,390,320],themes:["courtyard","night","contrast","dusk"],checks:["six subpages","overview commands and deep links","growth revision and evidence","skill configuration","review decision refresh","style retire and restore","invalid evidence hidden","all 22 expression fields reachable","sampling and injection save","compact numeric inputs","sample pagination and review","learning progress polling and library refresh","stale window read discarded","group semantic edit and configuration","daily review active and stopped","mobile overflow and control fit","SDK sandbox and assets","inline read recovery","saved values survive reload","sample polling stops on unmount"],screenshots};
    await fs.writeFile(path.join(output,"learning-results.json"),JSON.stringify(report,null,2)+"\n","utf8");console.log(JSON.stringify({...report,screenshots:screenshots.length},null,2));
  }catch(error){
    if(browser){for(const page of browser.contexts().flatMap(context=>context.pages())){
      const frame=page.frames().find(item=>item.url().includes(PREFIX));if(frame){
        await page.screenshot({path:path.join(output,"failure.png"),fullPage:true}).catch(()=>{});
        await fs.writeFile(path.join(output,"failure-state.json"),JSON.stringify({posts,calls:calls.slice(-12),errors,
          content:await frame.locator('.learning-hub').innerText().catch(()=>"")},null,2)+"\n","utf8");
      }
    }}
    console.error(error);throw error;
  }
  finally{releaseSampleRead?.();if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
