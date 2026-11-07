/* Integrated cottage acceptance using the installed SDK and synthetic owners only. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');

async function run({browser, outputDir, bridgePath, replyOnly=false, identityOnly=false, wildcardOnly=false}) {
  const root=path.resolve(__dirname,'../pages/陪伴小屋');
  await fs.mkdir(outputDir,{recursive:true});
  const sdk=await fs.readFile(bridgePath,'utf8');
  const HOST='astrbot_plugin_private_companion';
  const qa={calls:[],posts:[],mode:'direct',catalogMode:wildcardOnly?'wildcard':'multi',initialized:true,deny:false,late:null,growthState:'proposed',semanticRevision:1,semanticReactions:true};
  const internalIdentity={installation_id:'synthetic-installation-internal',operator_id:'dashboard:qa-internal'};
  const replyDefaults={enabled:false,llm_controlled:true,rules_enabled:false,rule_mode:'sentences',threshold:120,min_segment_chars:12,max_segments:4,prompt:'',streaming:false,part_gap_ms:0,image_position:'after_text',
    voice_enabled:false,voice_trigger_mode:'always',voice_generation_mode:'plain',voice_segment_mode:'each',voice_delivery_mode:'voice_and_text',voice_position:'after_media',voice_language:'zh',voice_foreign_text_mode:'original',
    voice_probability:1,voice_cooldown_seconds:0,voice_max_speech_chars:1800,voice_timeout_seconds:30,voice_trigger_keywords:'',voice_provider_id:'',voice_zh_provider_id:'',voice_ja_provider_id:'',voice_en_provider_id:'',voice_extra_prompt:''};
  const replies=Object.fromEntries(['a','b'].map(id=>[id,{settings:{...replyDefaults},revision:1,operations:new Map()}]));
  const replyStatus=id=>({settings:replies[id].settings,edit_token:String(replies[id].revision).padStart(64,'0'),configuration_token:'configuration-'+replies[id].revision,
    recent:[{batch_id:'collecting-'+id,state:'collecting',created_at:'2026-09-26T06:00:00Z',captured:null,calls:[],parts:[]},
      {batch_id:'voice-'+id,state:'complete',created_at:'2026-09-26T05:00:00Z',captured:{settings:{...replyDefaults,voice_enabled:true,streaming:true},configuration_token:'older'},calls:[],parts:[
        {sequence:1,state:'delivered',chars:3,candidate_reason:'planned',render_state:'ready',component_order:['text','voice'],text_hidden:0,speech_chars:3},
        {sequence:2,state:'delivered',chars:3,candidate_reason:'planned',render_state:'failed',component_order:['text','image','image'],text_hidden:0,speech_chars:3},
        {sequence:3,state:'unknown',chars:3,candidate_reason:'planned',render_state:'ready',component_order:['voice'],text_hidden:1,speech_chars:3}]},
      {batch_id:'legacy-'+id,state:'complete',created_at:'2026-09-25T05:00:00Z',captured:null,calls:[],parts:[
        {sequence:1,state:'delivered',chars:3,candidate_reason:null,render_state:null,component_order:null,text_hidden:null,speech_chars:null}]}],tts:{state:'ready',providers:[{id:'tts-main'},{id:'tts-ja'}]}});
  const full=relative=>'/'+HOST+'/'+relative;
  const ok=data=>qa.mode==='direct'?data:qa.mode==='nested'?{data:{status:'ok',data}}:{status:'ok',data};
  const failed=code=>({status:'error',data:{code}});
  function catalog() {
    return {mode:qa.catalogMode,...internalIdentity,acquaintance_api:full('acquaintance'),targets:[
      ...(qa.catalogMode==='single'?['a']:['a','b']).map((id,index)=>({target_id:id,scope_kind:'private',participant_id:'user-'+id,
        platform_id:'qq',account_id:'bot-'+id,bot_id:'bot-'+id,roles:[{role_id:index?'晚星':'晨星',
          ...Object.fromEntries(['life','state','growth','memory','relationships','reply'].map(name=>[name+'_api',full('host/targets/'+id+'/roles/role-'+id+'/'+name)]))}]})),
      ...(qa.catalogMode==='single'?[]:[{target_id:'group',scope_kind:'group',group_id:qa.catalogMode==='wildcard'?'*':'group-1',platform_id:'qq',account_id:'bot-a',bot_id:'bot-a',roles:[{role_id:'晨星',life_api:null,state_api:null,growth_api:null,memory_api:null,relationships_api:null,
        ...(qa.catalogMode==='wildcard'?{reply_api:full('host/targets/group/roles/role-group/reply')}: {})}]}])]};
  }
  function accountDirectory() {
    const targets=catalog().targets.map(target=>({...target,bot_id:target.bot_id||target.account_id,
      account_name:target.account_id==='bot-a'?'测试账号一':'测试账号二',
      display_name:target.scope_kind==='group'?'测试群':target.target_id==='a'?'测试联系人一':'测试联系人二',
      roles:target.roles.map(role=>({role_id:role.role_id,role_name:role.role_id,persona_ids:[role.role_id]})),
      session_key:'session-'+target.target_id,native:true,managed:true,persona_id:target.roles[0].role_id,
      llm_enabled:true,session_enabled:true}));
    const accounts=[...new Map(targets.map(target=>[target.bot_id,{
      platform_id:target.platform_id,account_id:target.account_id,bot_id:target.bot_id,account_name:target.account_name,
    }])).values()];
    const personas=[...new Map(targets.flatMap(target=>target.roles.map(role=>[target.bot_id+'|'+role.role_id,{
      bot_id:target.bot_id,role_id:role.role_id,persona_id:role.role_id,label:role.role_name,
    }]))).values()];
    return {mode:qa.catalogMode,enabled:true,accounts,personas,
      sessions:targets.filter(target=>target.scope_kind==='private'),groups:targets.filter(target=>target.scope_kind==='group'),
      discovered:{accounts,personas,targets}};
  }
  const growth=id=>({role_id:id==='a'?'晨星':'晚星',items:[{growth_id:'growth-'+id,revision:1,kind:'goal',state:qa.growthState,
    title:'练习水彩 '+id,description:'本周画一幅',progress_note:'留意窗边的光',source_count:1,view_token:'growth-token-'+id,
    actions:qa.growthState==='proposed'?['confirm','revise']:['pause','complete','retire']}],next_cursor:null});
  const life=id=>({role_id:id==='a'?'晨星':'晚星',participant:'user-'+id,host_state:'new',view_token:'life-'+id,edit_token:'edit-'+id,
    config:{enabled:false,diary_enabled:true,diary_time:'23:50',timezone:'Asia/Shanghai',sleep_enabled:false,sleep_time:'00:30',sleep_timezone:'Asia/Shanghai',sleep_minutes:480,reading_enabled:false,derive_enabled:false},
    sleep_schedule:{enabled:false,owner:{bot_id:'bot-'+id,role_id:id},executor_state:'unavailable'},
    journals:[{kind:'diary',local_date:'2026-09-25',revision:1,state:'active',body:'已保存的生活正文 '+id,summary:'生活正文 '+id,afterglow:'',dream_fragments:[],view_token:'journal-'+id}],
    activities:[],receipts:[],running:[],next_date:null,next_activity:null});
  async function rpc(message) {
    qa.calls.push(message);const {action,endpoint,params={},body={}}=message;
    if(qa.deny)return failed('core_access_required');
    if(endpoint==='page/targets')return qa.initialized?ok(catalog()):failed('staging_not_initialized');
    if(endpoint==='acquaintance'){
      assert.equal(action,'api:get');
      return ok({...internalIdentity,profile_id:'测试运行实例',operator_name:'测试管理员',persons:[],groups:[],
        counts:{total:0,private_chat:0,group_member:0,both:0},page_managed_capabilities:[]});
    }
    if(endpoint==='acquaintance/strangers')return ok({items:[]});
    if(/^host\/targets\/([ab])\/roles\/role-\1\/config$/.test(endpoint)){
      assert.equal(action,'api:get');
      assert.equal(typeof params.sections,'string');
      return ok({sections:{[params.sections]:{settings:{},fields:{},provenance:{},edit_token:'c'.repeat(64),
        revision:1,scope:'role',layers:['installation','role'],section:params.sections}}});
    }
    if(endpoint==='integration/manifest')return ok({product:{version:'2.1.0'},host:{enabled:true},
      core:[{id:'life',label:'生活、日记与成长',description:'已实现的本体生活模块'}],
      extensions:[
        {capability_id:'content',extension_id:'content',owner:'companion_v2',label:'创作与 QQ 空间',description:'生成与编辑',version:'2.1.0',enabled:true,runtime_ready:true,assembled:true,installed_revision:1,installed_package_version:'2.1.0',status_connected:true,integration_state:'connected',reason:'V2 原生模块已启用'},
        {capability_id:'image',extension_id:'image',owner:'companion_v2',label:'生图与相册',description:'生成与编辑',version:'2.1.0',enabled:false,runtime_ready:false,assembled:true,installed_revision:2,installed_package_version:'2.1.0',status_connected:true,integration_state:'disabled',reason:'模块已暂停'},
        {capability_id:'screen',extension_id:'companion.screen.native',owner:'companion_v2',label:'屏幕陪伴',version:'2.1.0',enabled:true,runtime_ready:true,assembled:true,installed_revision:1,installed_package_version:'2.1.0',status_connected:true,integration_state:'connected',reason:'V2 原生模块已启用'},
        {capability_id:'reality',extension_id:'reality',owner:'companion_v2',label:'现实触及',version:'2.1.0',enabled:true,runtime_ready:true,assembled:true,installed_revision:1,installed_package_version:'2.1.0',status_connected:true,integration_state:'connected',reason:'V2 原生模块已启用'},
        {capability_id:'together',extension_id:'together',owner:'companion_v2',label:'共处与实时活动',version:'2.1.0',enabled:true,runtime_ready:true,assembled:true,installed_revision:1,installed_package_version:'2.1.0',status_connected:true,integration_state:'connected',reason:'V2 原生模块已启用'},
        {capability_id:'game',extension_id:'game',owner:'companion_v2',label:'共同游玩',version:'2.1.0',enabled:true,runtime_ready:true,assembled:true,installed_revision:1,installed_package_version:'2.1.0',status_connected:true,integration_state:'connected',reason:'V2 原生模块已启用'},
        {capability_id:'live',extension_id:'live',owner:'companion_v2',label:'直播与表演',version:'2.1.0',enabled:true,runtime_ready:true,assembled:true,installed_revision:1,installed_package_version:'2.1.0',status_connected:true,integration_state:'connected',reason:'V2 原生模块已启用'}],
      providers:[], summary:{connected_count:6,extension_ready_count:6,unassembled_extension_count:0,installed_count:0,missing_count:0}});
    if(endpoint==='staging/status')return {status:'error',message:'未找到该路由',data:{}};
    if(endpoint==='host/performance')return ok({enabled:false,mode:'standard'});
    if(endpoint==='page/display-preferences')return ok({preferences:{}});
    if(endpoint==='setup/status')return ok({});
    if(endpoint==='host/status')return ok({mode:'multi',enabled:true,...internalIdentity,state:'new',accepting:false,
      delivery_mode:'preview',group_delivery_mode:'preview',targets:accountDirectory().discovered.targets});
    if(endpoint==='accounts'){
      assert.equal(action,'api:get');return ok(accountDirectory());
    }
    if(endpoint==='page/ownership')return ok({owner:'self',reason:'owner_module_absent'});
    if(endpoint==='reply/recalls')return ok({items:[],next_cursor:null});
    if(endpoint==='perception')return ok({providers:{screen:{available:false},reality:{available:false}}});
    if(endpoint==='semantic'){
      const selection=action==='api:get'?params:body;
      assert.equal(selection.target_id,'b');assert.equal(selection.role_id,'晚星');
      if(action==='api:post'){
        assert.equal(body.action,'configure');assert.equal(body.revision,qa.semanticRevision);
        qa.posts.push({endpoint,body});qa.semanticReactions=body.changes.reactions_enabled;qa.semanticRevision++;
        return ok({revision:qa.semanticRevision});
      }
      return ok({state:'ready',selection_ready:true,settings:{revision:qa.semanticRevision,enabled:true,memory_enabled:true,
        reactions_enabled:qa.semanticReactions,slang_enabled:false,slang_learning:false,vision_review:false,
        embedding_provider:'',rerank_provider:'',analysis_provider:'',timeout_seconds:10,batch_size:16,learning_interval_seconds:3600},
        providers:{embedding:[],rerank:[],analysis:[]},catalog:{reaction:{enabled:0},slang:{enabled:0}},sessions:[],items:[],next_after:null,errors:{}});
    }
    if(/^host\/targets\/group\/roles\/role-group\/groups\/300\/reply(?:\/.*)?$/.test(endpoint)){
      assert.equal(action,'api:get');
      return ok(replyStatus('a'));
    }
    const match=/^host\/targets\/([ab])\/roles\/role-[ab]\/(life|state|growth|memory|relationships|reply)(\/command|\/reflections|\/configure|\/preview)?$/.exec(endpoint);
    assert(match,'unexpected endpoint '+endpoint);
    const [,id,domain,command]=match;
    if(domain==='reply'){
      if(action==='api:get')return ok(replyStatus(id));
      qa.posts.push({endpoint,body});
      if(command==='/configure'){
        const value=replies[id],replayed=value.operations.has(body.operation_key);
        if(replayed)assert.deepEqual(body,value.operations.get(body.operation_key));
        else{
          if(body.edit_token!==replyStatus(id).edit_token)return failed('configuration_revision_conflict');
          value.operations.set(body.operation_key,body);Object.assign(value.settings,body.changes);value.revision++;
        }
        if(qa.dropReplyReceipt){qa.dropReplyReceipt=false;return {};}
        return ok({replayed,operation_id:'operation-'+body.operation_key,status:replyStatus(id)});
      }
      assert.equal(command,'/preview');assert.equal(body.image_count,2);
      assert.equal(body.settings.voice_segment_mode,'last');assert.equal(body.settings.voice_language,'ja');
      assert.equal(body.settings.voice_probability,.333);
      return ok({mode:'offline_preview',parts:['你好。\n','またね。'],count:2,characters:8,minimum_spacing_ms:0,image_position:'before_text',image_count:2,
        components:[{speech:'',success_order:['text'],fallback_order:['text'],text_hidden:false},{speech:'またね。',success_order:['image','image','voice'],fallback_order:['image','image','text'],text_hidden:true}],
        voice:{enabled:true,provider_id:'tts-ja',instruction:'语音偏好'},instruction:'分段偏好'});
    }
    if(action==='api:post') {
      qa.posts.push({endpoint,body});assert(command);assert(body.operation_key);
      if(domain==='life'){assert.equal(body.view_token,'life-'+id);assert.equal(body.edit_token,'edit-'+id);return ok({state:'completed'});}
      if(domain==='growth'){assert.equal(body.view_token,'growth-token-'+id);assert.equal(body.growth_id,'growth-'+id);qa.growthState='active';return ok({item:growth(id).items[0]});}
      throw Error('unexpected mutation');
    }
    if(domain==='life')return ok(life(id));
    if(domain==='growth')return ok(growth(id));
    if(domain==='memory')return command==='/reflections'
      ?ok({items:[],next_cursor:null,scope:'private_memory_reflections'})
      :ok({items:[{atom_id:'fact-'+id,state:'active',revision:1,predicate:'当前事实',representation:'已保存的事实 '+id}],next_cursor:null});
    if(domain==='relationships')return ok({role_id:id==='a'?'晨星':'晚星',subject_id:'user-'+id,scope_label:'测试私聊',operator_id:'qa',operator_name:'测试管理员',items:[],next_cursor:null});
    if(domain==='state')return ok({state:{state:'ready',revision:1,values:{energy:id==='a'?71:83,mood:'平静 '+id,health:'稳定',hunger:'不饿',sleep:'清醒',busy:'空闲'}},settings:{},sleep_schedule:{enabled:false,owner:{bot_id:id}},presentation:{state:'preview'}});
  }
  const wrapper=`<!doctype html><meta charset="utf-8"><style>body{margin:0}iframe{width:100%;height:100vh;border:0;display:block}</style><iframe id="cottage" title="V2.0 小屋" sandbox="allow-scripts allow-forms allow-downloads" src="/page/index.html"></iframe><script>
    addEventListener('message',async event=>{const frame=document.getElementById('cottage'),m=event.data;
      if(event.source!==frame.contentWindow||m.channel!=='astrbot-plugin-page')return;
      const send=value=>frame.contentWindow.postMessage({channel:'astrbot-plugin-page',...value},'*');
      if(m.kind==='ready'){send({kind:'context',context:{pluginName:'${HOST}',pageName:'陪伴小屋'}});return;}
      if(m.kind!=='request')return;
      try{const r=await fetch('/rpc',{method:'POST',body:JSON.stringify(m)});send({kind:'response',requestId:m.requestId,ok:true,data:await r.json()});}
      catch{send({kind:'response',requestId:m.requestId,ok:false,error:'test unavailable'});}
    });<\/script>`;
  const serverErrors=[];
  const server=http.createServer(async(req,res)=>{
    try {
      res.setHeader('Access-Control-Allow-Origin','*');
      const url=new URL(req.url,'http://127.0.0.1');
      if(url.pathname==='/'){res.setHeader('Content-Type','text/html;charset=utf-8');res.end(wrapper);return;}
      if(url.pathname==='/bridge.js'){res.setHeader('Content-Type','text/javascript;charset=utf-8');res.end(sdk);return;}
      if(url.pathname==='/rpc'){const chunks=[];for await(const chunk of req)chunks.push(chunk);res.setHeader('Content-Type','application/json');res.end(JSON.stringify(await rpc(JSON.parse(Buffer.concat(chunks).toString('utf8')))));return;}
      if(!url.pathname.startsWith('/page/')){res.writeHead(404).end();return;}
      const relative=decodeURIComponent(url.pathname.slice(6)),file=path.resolve(root,relative);
      if(!file.startsWith(root+path.sep)){res.writeHead(404).end();return;}
      let body=await fs.readFile(file);
      if(path.extname(file)==='.html')body=Buffer.from(body.toString('utf8').replace('</body>','<script src="/bridge.js"></script></body>'));
      res.setHeader('Content-Type',({'.html':'text/html;charset=utf-8','.js':'text/javascript;charset=utf-8','.css':'text/css;charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.webp':'image/webp'})[path.extname(file)]||'application/octet-stream');res.end(body);
    } catch(error){if(error.code!=='ENOENT')serverErrors.push(String(error.stack));res.writeHead(500).end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const page=await browser.newPage({viewport:{width:1440,height:1050},reducedMotion:'reduce'});
  const browserErrors=[];page.on('pageerror',e=>browserErrors.push(e.message));
  const shell=()=>page.frameLocator('#cottage');
  const inner=()=>shell().frameLocator('.product-workspace-frame');
  const checks=[];
  const ready=async()=>{await shell().locator('body').evaluate(()=>new Promise((resolve,reject)=>{
    const until=Date.now()+15000;
    const inspect=()=>window.CompanionProduct?.connection().state==='connected'?resolve():
      Date.now()>until?reject(Error('product directory timed out')):setTimeout(inspect,20);
    inspect();
  }));};
  const layout=async()=>assert.equal(await shell().locator('body').evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  const capture=async(name,fullPage=true)=>{await page.screenshot({path:path.join(outputDir,name+'.png'),fullPage});};
  const visit=async hash=>{
    await shell().locator('body').evaluate((_body,value)=>{location.hash=value;},hash);
    await shell().locator('body').evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  };
  const privateFields=async()=>{
    for(const frame of page.frames().slice(1)){
      assert.doesNotMatch(await frame.locator('body').innerText(),/synthetic-installation-internal|dashboard:qa-internal/);
    }
  };
  try {
    await page.goto('http://127.0.0.1:'+server.address().port);await ready();
    if(wildcardOnly){
      await shell().locator('body').evaluate(()=>{location.hash='#manage/identity';});
      const directory=shell().frameLocator('[data-workspace="identity"] iframe');
      await directory.locator('body').waitFor();
      await directory.locator('body').evaluate(()=>parent.postMessage({channel:'companion-workspace',kind:'navigate',
        target:{tab:'group-reply',platform_id:'qq',bot_id:'bot-a',account_id:'bot-a',group_id:'300',role_id:'晨星'}},'*'));
      await shell().locator('body').evaluate(()=>new Promise((resolve,reject)=>{
        const until=Date.now()+5000;
        const inspect=()=>window.CompanionProduct.target()?.group_id==='300'?resolve():
          Date.now()>until?reject(Error('wildcard group navigation timed out')):setTimeout(inspect,20);
        inspect();
      }));
      assert.equal(await shell().locator('body').evaluate(()=>window.CompanionProduct.role().reply_api),
        full('host/targets/group/roles/role-group/groups/300/reply'));
      assert.equal(await shell().locator('body').evaluate(()=>location.hash),'#manage/decision');
      assert(qa.calls.some(call=>call.endpoint==='host/targets/group/roles/role-group/groups/300/reply'));
      const assets=await shell().locator('body').evaluate(async()=>{
        const fonts=await Promise.all([400,600].map(async weight=>{
          try{return (await document.fonts.load(`${weight} 16px MiSans`,'陪伴小屋')).length;}
          catch(error){return error.name+': '+error.message;}
        }));
        const images=await Promise.all(['mug','tea-coffee'].map(async name=>{
          try{
            const image=new Image();image.src=`assets/room-loading-${name}.webp`;await image.decode();
            return [image.naturalWidth,image.naturalHeight];
          }catch(error){return error.name+': '+error.message;}
        }));
        return {fonts,images};
      });
      assert.deepEqual(assets,{fonts:[1,1],images:[[300,300],[300,300]]});
      for(const [width,height] of [[1440,1050],[390,844],[320,720]]){
        await page.setViewportSize({width,height});await layout();
        await capture('wildcard-'+width,false);
      }
      assert.deepEqual(browserErrors,[]);assert.deepEqual(serverErrors,[]);
      const report={checks:['wildcard group navigation reads the concrete group reply endpoint',
        'subset fonts and animated WebP assets load at desktop and mobile widths'],passed:2,browserErrors,serverErrors};
      await fs.writeFile(path.join(outputDir,'wildcard-results.json'),JSON.stringify(report,null,2)+'\n','utf8');return report;
    }
    if(identityOnly){
      const directory=()=>shell().frameLocator('[data-workspace="identity"] iframe');
      const relationships=()=>shell().frameLocator('[data-workspace="relationships"] iframe');
      const identityReady=async id=>{
        await directory().locator('#sync-state').filter({hasText:'V2 数据已同步'}).waitFor();
        await relationships().locator('#role').filter({hasText:id==='a'?'晨星':'晚星'}).waitFor();
      };
      const privateFields=async()=>{
        for(const text of [await shell().locator('body').innerText(),await directory().locator('body').innerText(),
          await relationships().locator('body').innerText()]){
          assert.doesNotMatch(text,/身份与范围|synthetic-installation-internal|dashboard:qa-internal/);
        }
        assert.deepEqual(await shell().locator('body').evaluate(()=>window.CompanionProduct.identity()),internalIdentity);
      };
      await shell().locator('body').evaluate(()=>{location.hash='#manage/identity';});
      await identityReady('a');
      for(const mode of ['multi','single']){
        if(mode==='single'){
          qa.catalogMode=mode;
          await page.setViewportSize({width:1440,height:1050});
          await shell().locator('[data-product-refresh]').click();await ready();await identityReady('a');
          assert.equal(await shell().locator('#productScope').isVisible(),false);
          assert.equal(await shell().locator('body').evaluate(()=>window.CompanionProduct.targets().length),1);
        }
        for(const [width,height] of [[1440,1050],[390,844],[320,720]]){
          await page.setViewportSize({width,height});await privateFields();await layout();
          await capture('product-identity-'+mode+'-'+width,false);
          checks.push(mode+' identity page keeps runtime identifiers internal at '+width+' pixels');
        }
        if(mode==='multi'){
          await shell().locator('#product-target').selectOption('b');await ready();await identityReady('b');
          await privateFields();
          assert(qa.calls.some(call=>call.endpoint==='host/targets/b/roles/role-b/relationships'));
          checks.push('relationship workspace follows target changes while the acquaintance directory remains available');
        }
      }
      assert(qa.calls.some(call=>call.endpoint==='acquaintance'));
      assert.equal(qa.posts.length,0);
      assert.deepEqual(browserErrors,[]);assert.deepEqual(serverErrors,[]);
      const report={checks,passed:checks.length,browserErrors,serverErrors,posts:qa.posts.length};
      await fs.writeFile(path.join(outputDir,'identity-results.json'),JSON.stringify(report,null,2)+'\n','utf8');return report;
    }
    if(replyOnly){
      const field=name=>shell().locator(`[data-reply-field="${name}"]`);
      const action=name=>shell().locator(`[data-reply-action="${name}"]`);
      const loaded=()=>shell().locator('.reply-notice').filter({hasText:'已读取'}).waitFor();
      await shell().locator('[data-shell-section="expression"]').click();await loaded();
      assert.match(await shell().locator('.reply-record').first().locator('summary').innerText(),/尚未固定配置/);
      await shell().locator('.reply-record').nth(1).locator('summary').click();
      const diagnostic=await shell().locator('.reply-record').nth(1).innerText();
      assert.match(diagnostic,/语音组件已准备/);assert.match(diagnostic,/TTS 合成失败，改发图文/);
      assert.match(diagnostic,/结果未知/);assert.match(diagnostic,/文字已隐藏/);
      await shell().locator('.reply-record').nth(2).locator('summary').click();
      assert.match(await shell().locator('.reply-record').nth(2).innerText(),/语音诊断未记录/);
      checks.push('recent replies separate candidate, synthesis, component order and unknown QQ receipt; legacy rows remain unrecorded');
      await field('enabled').check();await field('streaming').check();await field('voice_enabled').check();
      await field('image_position').selectOption('before_text');await field('voice_generation_mode').selectOption('model_tag');
      await field('voice_segment_mode').selectOption('last');await field('voice_delivery_mode').selectOption('voice_only');
      await field('voice_position').selectOption('before_text');await field('voice_language').selectOption('ja');
      await field('voice_provider_id').selectOption('tts-main');
      await shell().locator('.reply-voice details summary').click();
      await field('voice_ja_provider_id').selectOption('tts-ja');await field('voice_probability').fill('33.3');
      await shell().locator('#reply-example').fill('你好。\n<<PRIVATE_COMPANION_SPLIT>>\n<pc_tts>またね。</pc_tts>');
      await shell().locator('#reply-image-count').fill('2');await action('preview').click();
      await shell().locator('#reply-preview-result').filter({hasText:'候选朗读'}).waitFor();
      const preview=await shell().locator('#reply-preview-result').innerText();
      assert.match(preview,/图片 → 图片 → 语音/);assert.match(preview,/图片 → 图片 → 文字/);assert.doesNotMatch(preview,/<pc_tts>/);
      assert.equal(replies.a.settings.voice_enabled,false);await layout();await capture('reply-voice-desktop');
      await shell().locator('.reply-voice details summary').click();
      assert.equal(await field('voice_probability').inputValue(),'33.3');
      assert.equal(await field('voice_probability').evaluate(element=>element.validity.valid),true);
      await shell().locator('.reply-voice').evaluate(element=>element.scrollIntoView({block:'start'}));
      await capture('reply-voice-controls-desktop',false);
      await shell().locator('.reply-voice details').evaluate(element=>element.scrollIntoView({block:'start'}));
      await capture('reply-voice-advanced-desktop',false);
      checks.push('combined draft previews segmentation, Japanese speech, two leading images and synthesis fallback without saving');
      qa.dropReplyReceipt=true;await shell().locator('#reply-save').click();
      await action('retry').waitFor();assert.equal(await field('voice_enabled').isDisabled(),true);
      const original=qa.posts.at(-1);assert.equal(original.body.changes.voice_probability,.333);
      await shell().locator('#product-target').selectOption('b');await ready();await loaded();
      assert.equal(await field('voice_enabled').isChecked(),false);
      await shell().locator('#product-target').selectOption('a');await ready();
      await action('retry').waitFor();await action('retry').click();
      await shell().locator('.reply-notice').filter({hasText:'原保存已确认'}).waitFor();
      assert.deepEqual(qa.posts.at(-1).body,original.body);assert.equal(replies.a.operations.size,1);
      assert.equal(await field('voice_enabled').isChecked(),true);
      assert.equal(await field('voice_provider_id').inputValue(),'tts-main');
      checks.push('unknown save receipts survive target switching and retry the original operation without crossing object settings');
      replies.a.settings.voice_provider_id='retired-provider';replies.a.revision++;
      await action('refresh').click();await shell().locator('.reply-notice').filter({hasText:'配置已更新'}).waitFor();
      assert.equal(await shell().locator('#reply-save').isDisabled(),true);await action('reset').click();
      assert.equal(await field('voice_provider_id').inputValue(),'retired-provider');
      assert.match(await field('voice_provider_id').innerText(),/暂不可用/);
      checks.push('concurrent edits preserve drafts and retain saved providers missing from the live catalog');
      for(const [width,height] of [[390,844],[320,720]]){
        await page.setViewportSize({width,height});await layout();
        await shell().locator('.reply-voice').scrollIntoViewIfNeeded();await capture('reply-voice-'+width,false);
        const overflow=await shell().locator('#replyWorkspace input,#replyWorkspace select,#replyWorkspace textarea').evaluateAll(elements=>
          elements.filter(el=>el.getClientRects().length).some(el=>el.getBoundingClientRect().right>innerWidth));
        assert.equal(overflow,false);
      }
      await field('voice_enabled').uncheck();await shell().locator('#reply-save').click();
      await shell().locator('.reply-notice').filter({hasText:'已保存。'}).waitFor();assert.equal(replies.a.settings.voice_enabled,false);
      await page.reload();await ready();await shell().locator('body').evaluate(()=>{location.hash='#manage/expression';});await loaded();
      assert.equal(await field('voice_enabled').isChecked(),false);await layout();
      checks.push('voice controls fit 1440, 390 and 320 pixels; mobile saves survive a full page reload');
      assert.deepEqual(browserErrors,[]);assert.deepEqual(serverErrors,[]);
      const report={checks,passed:checks.length,browserErrors,serverErrors,posts:qa.posts.length};
      await fs.writeFile(path.join(outputDir,'reply-results.json'),JSON.stringify(report,null,2)+'\n','utf8');return report;
    }
    const nav=await shell().locator('#shellNav .shell-nav-group').evaluateAll(groups=>groups.map(group=>({
      title:group.querySelector('.shell-nav-title')?.textContent||'',
      links:[...group.querySelectorAll('.shell-nav-link')].map(link=>({label:link.querySelector('span').textContent,
        section:link.dataset.shellSection||link.dataset.shellView})),
    })));
    assert.deepEqual(nav.map(group=>[group.title,...group.links.map(link=>link.label)]),[
      ['生活','状态与日程','日记与生活'],
      ['专题配置','关系网','主动与联系','事实与经历','学习与反思','模型与连接','治理与维护'],
      ['能力配置','感知','理解','决策','行动','产出','表达','自我'],
      ['设置','开始使用','外观与设置','账号与会话','资料与维护'],
    ]);
    /* 每个分组都必须有标题：第一组曾经是 untitled，六项入口悬在左栏顶部
       没有任何分组名，而下面两组有——同一列里一半有标题一半没有。 */
    assert.deepEqual(nav.filter(group=>!group.title).map(group=>group.links[0]?.label),[]);
    assert.equal(new Set(nav.flatMap(group=>group.links).map(link=>link.section)).size,19);
    checks.push('all 17 native sections plus the two life views remain reachable in the current sidebar');
    assert.match(await shell().locator('.home-state-board').getAttribute('aria-label'),/71 \/ 100/);
    assert.doesNotMatch(await shell().locator('body').innerText(),/草木之间|9 月 23 日|望舒/);
    assert.equal(qa.posts.length,0);await layout();await capture('product-home-desktop');
    const canvas=shell().locator('canvas');assert(await canvas.count()>0);
    await canvas.screenshot({path:path.join(outputDir,'product-room-desktop.png')});
    await visit('#life/observation');
    await shell().locator('#shellPanel').filter({hasText:'生活正文 a'}).waitFor();
    checks.push('hosted home and observation use the selected role records without design samples or writes');
    await visit('#life/directory');
    await shell().locator('[data-furniture-filter="extensions"]').click();
    const extensionRows=shell().locator('#directoryGrid .home-item');
    assert.equal(await extensionRows.count(),7);
    assert.deepEqual(await extensionRows.locator('.home-item-main strong').allTextContents(),
      ['创作书架','画面工作室','共处电话','游戏棋桌','屏幕台','现实设备台','直播与表演']);
    checks.push('all seven native extensions remain in the home furniture directory');
    await shell().locator('#railBrand').click();
    await shell().locator('[data-detail="arrange"]').click();
    await capture('product-room-editor');
    await shell().locator('body').press('Escape');
    checks.push('voxel scene and nonmodal room editor remain reachable');
    await shell().locator('#product-target').selectOption('b');await ready();
    assert.match(await shell().locator('.home-state-board').getAttribute('aria-label'),/83 \/ 100/);
    await visit('#life/observation');
    await shell().locator('#shellPanel').filter({hasText:'生活正文 b'}).waitFor();
    assert.doesNotMatch(await shell().locator('#shellPanel').innerText(),/生活正文 a/);
    await shell().locator('.home-state-board').click();
    await shell().locator('#roleStateWorkspace [data-state-feature="energy"]').waitFor();
    assert.match(await shell().locator('#roleStateWorkspace').innerText(),/83/);
    assert.equal(await shell().locator('.product-workspace-frame').count(),0);
    checks.push('calendar navigation opens the selected role state and calendar workspace');
    await shell().locator('[data-shell-furniture="desk"]').click();
    await inner().locator('#role').filter({hasText:'晚星'}).waitFor();
    assert.equal(await inner().locator('#start-host').isVisible(),false);
    await inner().locator('#settings summary').click();
    await inner().locator('#config-form button[type="submit"]').click();
    await inner().locator('#notice').filter({hasText:'操作已完成'}).waitFor();
    assert.equal(qa.posts.at(-1).endpoint,'host/targets/b/roles/role-b/life/command');
    checks.push('embedded life workspace reads and saves through the selected target path');
    await shell().locator('[data-shell-section="identity"]').click();
    await shell().frameLocator('[data-workspace="relationships"] iframe').locator('#role').filter({hasText:'晚星'}).waitFor();
    await shell().frameLocator('[data-workspace="identity"] iframe').locator('#sync-state').filter({hasText:'V2 数据已同步'}).waitFor();
    await privateFields();
    await capture('product-relationships');
    checks.push('relationships reuse the same selected target and role');
    await shell().locator('[data-shell-section="memory"]').click();
    await shell().locator('#memory-area-facts').click();
    await shell().locator('#memoryWorkspace').filter({hasText:'已保存的事实 b'}).waitFor();
    checks.push('memory navigation opens saved records for the selected role');
    await shell().locator('[data-shell-section="host"]').click();
    await inner().locator('#accepting').filter({hasText:'未接管消息'}).waitFor();
    await inner().locator('.status-panel[aria-busy="false"]').waitFor();
    assert.equal(await inner().locator('#accepting').innerText(),'未接管消息');
    assert.match(await inner().locator('#topology-counts').innerText(),/2 个账号.*2 个人格.*3 个窗口/);
    await privateFields();await capture('product-host-topology');
    const postCount=qa.posts.length;
    await shell().locator('#railBrand').click();
    await shell().locator('[data-shell-furniture="garden"]').click();
    await shell().locator('[data-growth-action="confirm"]').click();
    await shell().locator('#growth-form button[type="submit"]').click();
    await shell().locator('[data-growth-action="complete"]').waitFor();
    assert.equal(qa.posts.length,postCount+1);assert.equal(qa.posts.at(-1).body.growth_id,'growth-b');
    checks.push('host status is read-only; growth commands preserve scope, revision and view token');
    await shell().locator('[data-shell-section="expression"]').click();
    await shell().locator('#semanticSettings').waitFor({state:'attached'});
    await shell().locator('#semanticSettings input[name="reactions_enabled"]').uncheck();
    await shell().locator('#semanticSettings button[type="submit"]').click();
    await shell().locator('.semantic-feedback').filter({hasText:'已保存'}).waitFor();
    assert.equal(qa.posts.at(-1).endpoint,'semantic');
    assert.equal(qa.posts.at(-1).body.changes.reactions_enabled,false);
    checks.push('expression settings save through the selected target and role');
    await shell().locator('[data-shell-section="perception"]').click();
    await shell().locator('[data-perception-tab="config-screen"]').click();
    await shell().locator('#perception-detail-device-access summary').click();
    await shell().locator('#perceptionProviderWorkspace [data-native-extension-host="reality"]').waitFor();
    assert.doesNotMatch(await shell().locator('#perceptionProviderWorkspace').innerText(),/plugin-page|extension\/plugins|astrbot_plugin_.*_companion/);
    await layout();await page.setViewportSize({width:390,height:844});await layout();
    await shell().locator('body').evaluate(()=>{location.hash='#manage/semantic';});
    await shell().locator('body').evaluate(()=>new Promise(resolve=>{
      if(location.hash==='#manage/perception'){resolve();return;}
      addEventListener('hashchange',resolve,{once:true});
    }));
    assert.equal(await shell().locator('body').evaluate(()=>location.hash),'#manage/perception');
    checks.push('perception uses the V2 native Reality panel and exposes no legacy plugin page links');
    qa.mode='nested';await shell().locator('[data-product-refresh]').click();await ready();
    await shell().locator('#railBrand').press('Enter');
    assert.equal(await shell().locator('body').evaluate(()=>location.hash),'#life');
    assert.match(await shell().locator('.home-state-board').getAttribute('aria-label'),/83 \/ 100/);
    await shell().locator('#product-target').selectOption('group');await ready();
    await visit('#life/observation');
    assert.doesNotMatch(await shell().locator('#shellPanel').innerText(),/生活正文 b/);
    checks.push('direct and nested envelopes work; group selection clears private data');
    await shell().locator('#product-target').selectOption('a');await ready();
    await shell().locator('#railBrand').click();
    for(const [width,height] of [[390,844],[320,720]]){
      await page.setViewportSize({width,height});await layout();await privateFields();
      await capture('product-home-'+width,false);
      await canvas.screenshot({path:path.join(outputDir,'product-room-'+width+'.png')});
    }
    checks.push('no horizontal overflow at 1440, 390 and 320 pixels');
    await shell().locator('[data-shell-section="host"]').press('Enter');
    await inner().locator('#accepting').filter({hasText:'未接管消息'}).waitFor();
    await inner().locator('.status-panel[aria-busy="false"]').waitFor();
    await privateFields();
    await shell().locator('#railBrand').press('Enter');
    assert.equal(await shell().locator('#shellPanel').isVisible(),false);
    checks.push('mobile sidebar reaches account settings and returns to the cottage');
    qa.initialized=false;await shell().locator('[data-product-refresh]').click();
    await shell().locator('[data-host-badge]').filter({hasText:'待初始化'}).waitFor({state:'attached'});
    await visit('#life/directory');
    assert.match(await shell().locator('#shellPanel').innerText(),/画面工作室/);
    assert.doesNotMatch(await shell().locator('body').innerText(),/生活正文 a/);
    checks.push('extension inventory remains on the home furniture directory before host initialization');
    qa.deny=true;await shell().locator('[data-product-refresh]').click();
    await shell().locator('#productScope').filter({hasText:'没有管理权限'}).waitFor();
    assert.doesNotMatch(await shell().locator('body').innerText(),/生图与相册|生活正文/);
    await privateFields();
    qa.deny=false;qa.initialized=true;qa.mode='direct';
    await page.setViewportSize({width:1440,height:1050});
    await shell().locator('[data-product-refresh]').click();await ready();
    await shell().locator('#product-target').selectOption('b');await ready();
    await shell().locator('[data-shell-section="perception"]').click();
    await layout();await capture('product-perception-desktop',false);
    await shell().locator('[data-perception-tab="config-screen"]').click();
    await shell().locator('#perception-detail-device-access summary').click();
    await shell().locator('#perceptionProviderWorkspace').scrollIntoViewIfNeeded();await capture('product-perception-providers-desktop',false);
    await page.setViewportSize({width:390,height:844});await layout();await shell().locator('body').evaluate(()=>scrollTo(0,0));await capture('product-perception-mobile',false);
    await page.setViewportSize({width:320,height:720});await layout();await shell().locator('body').evaluate(()=>scrollTo(0,0));await capture('product-perception-narrow',false);
    await privateFields();assert.deepEqual(browserErrors,[]);assert.deepEqual(serverErrors,[]);
    checks.push('permission failure clears cached directory and records');
    const report={checks,passed:checks.length,browserErrors,serverErrors,posts:qa.posts.length};
    await fs.writeFile(path.join(outputDir,'product-results.json'),JSON.stringify(report,null,2)+'\n','utf8');return report;
  } catch(error){
    await fs.writeFile(path.join(outputDir,'product-failure.json'),JSON.stringify({error:error.message,checks,browserErrors,serverErrors,calls:qa.calls},null,2)+'\n','utf8');
    await capture('product-failure');throw error;
  }
  finally{await page.close();await new Promise(resolve=>server.close(resolve));}
}
module.exports={run};
if(require.main===module)(async()=>{const browser=await require('playwright').chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH});try{console.log(JSON.stringify(await run({browser,outputDir:process.argv[2],bridgePath:process.argv[3],replyOnly:process.argv.includes('--reply-only'),identityOnly:process.argv.includes('--identity-only'),wildcardOnly:process.argv.includes('--wildcard-only')}),null,2));}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
