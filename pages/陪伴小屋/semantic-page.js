/* Live semantic settings and scoped catalog administration. */
"use strict";
window.CompanionSemanticPage = (() => {
  const product = window.CompanionProduct;
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const fields = {reaction:[["name","名称"],["description","画面与动作"],["visible_text","可见文字"],["tags","标签"],["emotions","情绪"],["intents","适用回应"]],slang:[["meaning","含义"],["usage","用法"]]};
  const errors = {
    staging_not_initialized:"角色数据尚未加载，请重新读取。",
    staging_host_terminated:"陪伴服务已停止，请重新读取。",
    dashboard_identity_required:"当前登录状态不可用，请重新打开陪伴小屋。",
    authorization_denied:"当前对象尚未授予读取语义素材所需权限，请在关系与权限页核对。",
    core_access_required:"当前管理账号没有语义素材管理权限。",
    semantic_request_invalid:"语义素材请求与当前接口不匹配，请更新页面后重试。",
    semantic_group_input_required:"当前群会话还没有可核实的成员输入；收到群消息后即可管理。",
    semantic_target_unavailable:"当前选择的对象或角色不可用，请重新选择。",
    semantic_scope_changed:"会话范围已变化，请重新选择对象后刷新。",
    semantic_stopped:"语义服务所在的后台已停止，请重新读取角色状态。",
    supervisor_fenced:"语义服务暂不可用，请重新读取。",
    semantic_model_failed:"语义模型调用失败，请核对嵌入模型配置后重试。",
    semantic_store_busy:"语义库暂时忙（有后台任务正在写入），请稍后重试。",
    semantic_operation_failed:"这次操作没有完成，请稍后重试；持续出现请看插件日志。",
    semantic_conflict:"配置已变化，请刷新核对最新版本。",
    semantic_item_missing:"这条素材已不存在，请刷新列表。",
    semantic_result_changed:"结果已变化，请刷新后核对。",
    semantic_action_unavailable:"当前状态下不能执行该操作，请刷新后重试。",
    semantic_offer_conflict:"该修改与已有结果冲突，请刷新核对。",
    slang_evidence_unavailable:"群黑话缺少可用证据，暂时无法整理。",
    bridge_timeout:"连接超时，请核对连接后重试。",
    invalid_response:"配置数据不完整，请刷新核对。",
    reaction_capture_review_invalid:"视觉审核没有返回有效结论，本次图片未收录。",
    reaction_collection:"自动收录的视觉审核暂不可用，请检查视觉模型配置。",
    host_image_limit:"图片超过单张 5 MB 限制，请缩小后重试。",
    host_image_format_unsupported:"图片格式不受支持，请使用 PNG、JPEG、WebP 或 GIF。",
    host_image_invalid:"图片内容无效、为空或已损坏，请更换完好的图片文件后重试。",
    host_animation_limit:"动图帧数或像素超过上限（最多 256 帧），请压缩或裁剪后重试。",
    semantic_vision_provider_unavailable:"当前没有可用的视觉模型，请先核对支持识图的模型配置。",
    semantic_provider_unavailable:"当前没有可用的语义模型，请先核对模型服务配置。",
    image_read_failed:"本地图片读取失败，请重新选择文件后重试。",
  };
  const localized = (failure, fallback) => errors[failure?.code]
    || errors[failure?.message]
    || fallback;
  const reactionFlags = new Set(["reactions_enabled","reactions_private_enabled","reactions_group_enabled",
    "reactions_proactive_enabled",
    "reactions_collection_private_enabled","reactions_collection_group_enabled","vision_review"]);
  const flags = [["enabled","启用语义目录"],["memory_enabled","语义记忆检索"],
    ["reactions_enabled","启用表情包表达"],["reactions_private_enabled","私聊使用表情包"],
    ["reactions_group_enabled","群聊使用表情包"],
    ["reactions_proactive_enabled","允许主动消息带表情包"],
    ["reactions_collection_private_enabled","从私聊自动收录"],
    ["reactions_collection_group_enabled","从群聊自动收录"],
    ["slang_enabled","群聊黑话目录"],["slang_learning","黑话自动学习"],
    ["slang_web_search_enabled","黑话联网参考"],["vision_review","识图复核"]];
  const providerFields = [["embedding_provider","向量模型（嵌入）","embedding"],["rerank_provider","候选重排","rerank"],["analysis_provider","视觉与词义分析","analysis"]];
  const semanticNumberFields = [["timeout_seconds","模型超时 / 秒",1,30],["batch_size","索引批次",1,64],
    ["learning_interval_seconds","群词义整理间隔 / 分钟",1,1440],
    ["slang_web_search_candidates","联网检索词数 / 个",1,12],["slang_web_search_results","每词参考条数",1,5]];
  const reactionNumberFields = [["reactions_trigger_probability","表情包触发概率 / %",0,100,1],
    ["reactions_cooldown_seconds","发送间隔 / 秒",0,3600,1],
    ["reactions_collection_private_probability","私聊收录概率 / %",0,100,1],
    ["reactions_collection_group_probability","群聊收录概率 / %",0,100,1]];
  const numberFields = [...semanticNumberFields,...reactionNumberFields];
  const percentageFields = new Set(["reactions_trigger_probability","reactions_collection_private_probability",
    "reactions_collection_group_probability"]);
  const selectFields = [["reactions_delivery_mode","发送位置",[["separate_after","正文后单独发送"],
    ["same_message","与正文同条发送"],["separate_before","正文前单独发送"]]],
    ["reactions_image_format","发送格式",[["image","普通图片"],["qq_emoji","QQ 表情"]]],
    ["reactions_collection_mode","收录审核通过后",[["automatic","自动启用"],["confirm","待人工确认"]]]];
  /* 这一页发送所有可编辑字段（不是只发改动），而 revision 冲突后必须重读
     才能拿到新版本号。重读会把 state 换成服务端的值，所以本地草稿要单独留一份，
     否则用户刚填的值会被服务端旧值覆盖。 */
  let root,scope,abort,state,kind="reaction",view="all",after={},loaded={},deletedIds=new Set(),sessionId="",busy=false,refreshing=false,refreshedAt="",editing=null,result=null,message="",generation=0,draft=null,conflict=false,selectedTarget=null,selectedSelection=null,selectedScopeKey="",globalSettingsOnly=false;
  let drawerEl=null,drawerCloseTimer=0,drawerOpenFrame=0;
  function reducedMotion(){
    if(typeof document==="undefined")return true;
    return document.documentElement?.dataset?.motion==="off"
      ||document.body?.classList?.contains?.("reduce-motion")
      ||Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
  }
  function clearDrawerTimers(){
    if(drawerCloseTimer&&typeof clearTimeout==="function")clearTimeout(drawerCloseTimer);
    if(drawerOpenFrame&&typeof cancelAnimationFrame==="function")cancelAnimationFrame(drawerOpenFrame);
    drawerCloseTimer=0;drawerOpenFrame=0;
  }
  const MAX_REFRESH_PAGES = 8;
  const formatClock = (date = new Date()) => `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}:${String(date.getSeconds()).padStart(2, "0")}`;
  const visibleFlags = () => view === "expression"
    ? flags.filter(([id]) => reactionFlags.has(id))
    : view === "learning" ? flags.filter(([id]) => !reactionFlags.has(id) || id === "vision_review") : flags;
  const visibleProviders = () => view === "expression" ? [] : providerFields;
  const visibleNumbers = () => view === "expression" ? reactionNumberFields
    : view === "learning" ? semanticNumberFields : numberFields;
  const visibleSelects = () => view === "learning" ? [] : selectFields;
  const visibleKinds = group => view === "expression" ? ["reaction"]
    : view === "learning" ? (group ? ["slang","reaction"] : ["reaction"])
      : (group ? ["reaction","slang"] : ["reaction"]);
  const selection = () => globalSettingsOnly?{global:true}:(()=>{const value=selectedSelection||product.selection();return {target_id:value.target_id,role_id:value.role_id,...(sessionId?{session_id:sessionId}:{})};})();
  const target = () => globalSettingsOnly ? null : selectedTarget || product.target();
  const current = () => root?.isConnected && scope===(selectedScopeKey||product.scopeKey());
  const active = ticket => ticket===generation && current();
  /* 表情库按角色归属，即使群聊尚未收到首条消息也可直接管理；群黑话仍需群会话就绪。 */
  const catalogReady = (targetKind = kind) => targetKind === "reaction"
    ? Boolean(state?.selection_ready || state?.reaction_ready)
    : Boolean(state?.selection_ready);
  async function api(method,values) {
    /* 等桥就绪，而不是当场读 window.AstrBotPluginPage。
       主页面此前不引页面桥 SDK，桥是在 product.connect() 里动态补上的；
       这一页若在它补好之前就发请求，读到的是 undefined，用户看到的是
       「未连接」——一个只在几百毫秒窗口里出现的假故障。 */
    const bridge=product.bridgeNow()||await product.bridgeFor();
    return product.unwrap(await (method==="GET"?bridge.apiGet("semantic",values):bridge.apiPost("semantic",values)));
  }
  const toggle=(id,label,value) => '<label class="semantic-toggle"><span>'+label+'</span><input type="checkbox" name="'+id+'" '+(value?"checked":"")+'></label>';
  const options=(list,value) => '<option value="">自动选择</option>'+(list||[]).map(p=>'<option value="'+escape(p.id)+'" '+(p.id===value?"selected":"")+'>'+escape(p.name||p.id)+'</option>').join("");
  const imageSource=value=>{
    const source=String(value||"").trim();
    return /^(?:data:image\/[a-z0-9.+-]+;|https:\/\/|http:\/\/|\/)/i.test(source)?source:"";
  };
  function editorInner(item) {
    let html='<div class="semantic-editor-backdrop" data-semantic="close" aria-hidden="true"></div><form id="semanticEditor" class="semantic-editor"><div class="semantic-heading"><h3>'+escape(item.payload.name||item.payload.term||"素材详情")+'</h3><button type="button" class="icon-button" data-semantic="close" aria-label="关闭编辑" title="关闭"><i data-lucide="x"></i></button></div>';
    const source=imageSource(item.image_url||item.payload?.image_url||item.payload?.url);
    if(source)html+='<img src="'+escape(source)+'" alt="'+escape(item.payload.name||"表情原图")+'" loading="lazy">';
    html+=fields[kind].map(([id,label])=>'<label>'+label+'<textarea name="'+id+'" rows="'+(id==="description"||id==="meaning"?3:2)+'" maxlength="'+(kind==="slang"?500:1500)+'">'+escape(item.payload[id]||"")+'</textarea></label>').join("");
    html+=toggle("item_enabled","允许检索使用",!!item.enabled);
    html+='<div class="semantic-actions"><button class="button primary" type="submit" '+(busy?"disabled":"")+'>保存修改</button>';
    if(kind==="reaction")html+='<button class="button" type="button" data-semantic="analyze" '+(busy?"disabled":"")+'>补全空白字段</button>';
    if(kind==="reaction")html+='<button class="icon-button danger" type="button" data-semantic="delete" aria-label="删除表情素材" title="删除表情素材" '+(busy?"disabled":"")+'><i data-lucide="trash-2"></i></button>';
    return html+'<button class="button" type="button" data-semantic="close">取消</button></div></form>';
  }
  function editor(item) {
    return '<div class="semantic-editor-drawer is-open" aria-hidden="false">'+editorInner(item)+'</div>';
  }
  function commitView(mainHtml) {
    if(typeof document==="undefined"||typeof root?.insertAdjacentHTML!=="function"){
      root.innerHTML=mainHtml+(editing?editor(editing):"");
      window.lucide?.createIcons({nodes:root.querySelectorAll("[data-lucide]")});
      return;
    }
    if(!drawerEl||drawerEl.parentNode!==root){
      drawerEl=document.createElement("div");
      drawerEl.className="semantic-editor-drawer";
      drawerEl.setAttribute("aria-hidden","true");
      root.innerHTML="";
      root.appendChild(drawerEl);
    }else{
      while(root.firstChild&&root.firstChild!==drawerEl)root.removeChild(root.firstChild);
    }
    drawerEl.insertAdjacentHTML("beforebegin",mainHtml);
    clearDrawerTimers();
    const wasOpen=drawerEl.classList.contains("is-open");
    if(editing){
      drawerEl.innerHTML=editorInner(editing);
      drawerEl.setAttribute("aria-hidden","false");
      if(wasOpen||reducedMotion()||typeof requestAnimationFrame!=="function"){
        drawerEl.classList.add("is-open");
      }else{
        void drawerEl.offsetWidth;
        drawerOpenFrame=requestAnimationFrame(()=>{
          drawerOpenFrame=0;
          if(editing&&drawerEl?.isConnected)drawerEl.classList.add("is-open");
        });
      }
    }else{
      drawerEl.classList.remove("is-open");
      drawerEl.setAttribute("aria-hidden","true");
      if(wasOpen&&!reducedMotion()&&typeof setTimeout==="function"){
        drawerCloseTimer=setTimeout(()=>{
          drawerCloseTimer=0;
          if(!editing&&drawerEl?.isConnected)drawerEl.innerHTML="";
        },240);
      }else{
        drawerEl.innerHTML="";
      }
    }
    window.lucide?.createIcons({nodes:root.querySelectorAll("[data-lucide]")});
  }
  function pendingAnalysis(item) {
    const payload=item?.payload||{},itemKind=item?.kind||kind;
    return itemKind==="reaction"&&payload.analysis==="pending";
  }
  function pendingAnalysisCount(){
    return state?.catalog?.reaction?.pending??0;
  }
  function batchAnalyzeButton(){
    const count=pendingAnalysisCount();
    return '<button class="button" type="button" data-semantic="analyze-pending" '+(busy||!count?'disabled':'')+'>批量识别待补全'+(count?' ('+count+')':'')+'</button>';
  }
  function renderLearning(cfg,group) {
    const current=shown=>draft?{...cfg,...draft}[shown]:cfg[shown];
    const catalog=state.catalog||{},slangCount=Number(catalog.slang?.total||0),reactionCount=Number(catalog.reaction?.total||0);
    const counts=group?`${slangCount} 条黑话 · ${reactionCount} 项素材`:`${reactionCount} 项素材`;
    let html='<div class="settings-heading"><div><h2>'+(globalSettingsOnly?'全局语义策略':'语义目录')+'</h2></div><div class="refresh-cluster"><span class="semantic-state '+escape(state.state)+'">'+escape({ready:"嵌入已就绪",lexical_fallback:"关键词回退",disabled:"已停用"}[state.state]||"状态待核对")+'</span>'+(refreshedAt?'<span class="refresh-stamp" role="status">刚刚已刷新 '+escape(refreshedAt)+'</span>':'')+'<button type="button" class="lh-refresh'+(refreshing?' is-refreshing':'')+'" data-semantic="refresh" aria-label="重新读取" title="重新读取" aria-busy="'+(refreshing?'true':'false')+'" '+(busy?'disabled':'')+'><i data-lucide="refresh-cw"></i></button></div></div>';
    if(message)html+='<p class="semantic-feedback'+(conflict?" semantic-conflict":"")+'" role="status">'+escape(message)+'</p>';
    if(!globalSettingsOnly){
    html+='<section class="semantic-learning-catalog" aria-label="语义条目"><header class="semantic-learning-heading"><h3>语义条目 · 阵列</h3><span>'+escape(counts)+'</span></header>';
    if(group&&state.sessions?.length)html+='<label class="semantic-session">群会话<select id="semanticSession" aria-label="群会话">'+state.sessions.map((id,index)=>'<option value="'+escape(id)+'" '+(id===state.session_id?'selected':'')+'>'+escape(id===target()?.group_id?'主群会话':'群会话 '+(index+1))+'</option>').join('')+'</select></label>';
    if(!state.selection_ready&&!state.reaction_ready)html+='<p class="semantic-learning-empty" role="status">该群会话暂无语义条目</p>';
    else {
      html+='<div class="semantic-learning-toolbar"><form id="semanticSearch">';
      if(group)html+='<select name="kind" aria-label="素材类型"><option value="slang" '+(kind==="slang"?'selected':'')+'>群黑话</option><option value="reaction" '+(kind==="reaction"?'selected':'')+'>反应素材</option></select>';
      html+='<input name="query" type="search" maxlength="2000" placeholder="检索语义条目" aria-label="检索语义条目"><button class="button" '+(busy?'disabled':'')+'>检索</button></form>'+
        '<label class="semantic-import button"><span>导入素材</span><input id="semanticFile" type="file" multiple accept="image/png,image/jpeg,image/webp,image/gif" '+(busy?'disabled':'')+'></label>'+
        (kind==="reaction"?batchAnalyzeButton():"")+'</div>';
      if(result)html+='<div class="semantic-results"><strong>检索结果</strong><span>'+(result.embedded||0)+'/'+(result.eligible||0)+' 已建索引</span>'+(result.items||[]).map((item,index)=>'<button data-semantic-open="'+escape(item.id)+'" data-semantic-kind="'+escape(item.kind||kind)+'">'+escape(item.payload.name||item.payload.term||'未命名条目 '+(index+1))+'</button>').join('')+'</div>';
      const rows=(state.items||[]).map(item=>{
        const payload=item.payload||{},itemKind=item.kind||kind,slang=itemKind==="slang";
        const migrated=payload.collection_source==="legacy_migration"||Boolean(payload.legacy_source_id||payload.legacy_filename);
        const source={private_capture:"私聊归纳",group_capture:"群聊归纳",legacy_migration:"V1 表情迁移"}[payload.collection_source]||(migrated?"V1 表情迁移":"手工收录");
        const title=payload.name||payload.term||"未命名";
        const detail=payload.meaning||payload.description||payload.usage||"";
        const sourceUrl=imageSource(item.thumbnail_url||item.image_url||payload.image_url||payload.url);
        const media=slang?'<span class="semantic-row-placeholder semantic-row-slang-mark" aria-hidden="true"><i data-lucide="messages-square"></i></span>':sourceUrl
          ?'<img src="'+escape(sourceUrl)+'" alt="" loading="lazy">'
          :'<span class="semantic-row-placeholder" aria-hidden="true"><i data-lucide="image"></i></span>';
        const stateLabel=item.vector_ready?"向量就绪":"向量待重建";
        const rowKind=slang?"群黑话":"反应素材";
        return '<article class="semantic-row"><div class="semantic-row-media">'+media+'</div><div class="semantic-row-body"><div class="semantic-row-title"><span class="semantic-row-kind">'+rowKind+'</span><strong>'+escape(title)+'</strong></div><p>'+escape(detail)+'</p><small>'+escape(source)+' · v'+escape(item.revision)+' · '+(item.enabled?'已启用':'已停用')+' · '+stateLabel+(item.source_valid===false?' · 来源已失效':'')+'</small></div><button class="button small" type="button" data-semantic-open="'+escape(item.id)+'" data-semantic-kind="'+escape(itemKind)+'">编辑</button></article>';
      }).join('');
      html+='<div class="semantic-list">'+rows+'</div>';
      if(!state.items?.length)html+='<p class="semantic-learning-empty">暂无语义条目</p>';
      if(Object.values(after).some(Boolean))html+='<button class="button" data-semantic="more" '+(busy?'disabled':'')+'>加载更多</button>';
    }
    html+='</section>';
    }
    const currentFlags=visibleFlags();
    const coreFlags=currentFlags.filter(([id])=>["enabled","vision_review"].includes(id));
    const memoryFlags=currentFlags.filter(([id])=>["memory_enabled","slang_enabled","slang_learning","slang_web_search_enabled"].includes(id));
    const coreNumbers=semanticNumberFields.filter(([id])=>["timeout_seconds","batch_size"].includes(id));
    const memoryNumbers=semanticNumberFields.filter(([id])=>!coreNumbers.some(([field])=>field===id));
    const flagMarkup=items=>'<div class="semantic-flags">'+items.map(([id,label])=>toggle(id,label,current(id))).join('')+'</div>';
    const providerMarkup='<div class="semantic-providers">'+providerFields.map(([id,label,type])=>'<label>'+label+'<select name="'+id+'">'+options(state.providers?.[type],current(id))+'</select></label>').join('')+'</div>'
      +(!(state.providers?.embedding||[]).length?'<p class="semantic-meta">没有读到向量模型。请先在 AstrBot 的模型服务页添加 embedding 类型的 Provider，再回到这里选择。</p>':'');
    const numberMarkup=items=>'<div class="semantic-providers">'+items.map(([id,label,min,max,step])=>{
      const raw=current(id),value=id==="learning_interval_seconds"&&Number.isFinite(Number(raw))?Math.round(Number(raw)/60):raw;
      return '<label>'+label+'<input name="'+id+'" type="number" min="'+min+'" max="'+max+'" step="'+(step||1)+'" value="'+escape(value??'')+'"></label>';
    }).join('')+'</div>';
    html+='<form id="semanticSettings" class="semantic-learning-settings"><section class="semantic-config-panel"><header><h3>语义目录与提供方</h3></header>'+
      flagMarkup(coreFlags)+providerMarkup+numberMarkup(coreNumbers)+'</section>'+
      '<section class="semantic-config-panel"><header><h3>语义记忆与群聊黑话</h3></header>'+
      flagMarkup(memoryFlags)+numberMarkup(memoryNumbers)+
      '<nav class="semantic-learning-links" aria-label="相关配置"><button type="button" data-go="manage" data-manage="models"><span>语义提供方</span><i data-lucide="arrow-up-right"></i></button>'+
      '<button type="button" data-go="manage" data-manage="expression" data-manage-subtab="reaction"><span>语义反应素材</span><i data-lucide="arrow-up-right"></i></button>'+
      '<button type="button" data-go="memories"><span>每日反思与经历</span><i data-lucide="arrow-up-right"></i></button></nav></section>'+
      '<div class="semantic-learning-actions"><button class="button primary" type="submit" '+(busy?'disabled':'')+'>'+(draft?'保存草稿':'保存配置')+'</button>'+
      (draft?'<button class="button" type="button" data-semantic="discard">放弃草稿</button>':'')+
      (globalSettingsOnly?'':'<button class="button" type="button" data-semantic="reindex" '+(busy||!state.selection_ready?'disabled':'')+'>重建当前索引</button>')+'</div></form>';
    return html;
  }
  function render() {
    if(!current())return;
    root?.classList?.toggle?.("semantic-expression-view", view === "expression");
    root?.classList?.toggle?.("semantic-learning-view", view !== "expression");
    if(!product.hosted()){clearDrawerTimers();drawerEl=null;root.innerHTML='<div class="settings-heading"><div><h2>语义理解与素材</h2></div></div><p class="memory-notice" role="status">角色与会话目录暂未读到。</p><div class="semantic-actions"><button class="button" type="button" data-workspace-retry>重新读取</button></div>';return;}
    if(!state){
      clearDrawerTimers();drawerEl=null;
      const action=busy?"":'<div class="semantic-actions"><button class="button" type="button" data-semantic="retry">重新读取</button></div>';
      root.innerHTML='<div class="settings-heading"><div><h2>语义理解与素材</h2></div></div>'
        +'<p class="memory-notice" role="status">'+escape(message||"正在读取实际配置…")+'</p>'+action;
      return;
    }
    const cfg=state.settings,group=target()?.scope_kind==="group";
    /* 有草稿时按草稿渲染：冲突重读之后用户填的值仍在。 */
    const shown=draft?{...cfg,...draft}:cfg;
    const currentViewFlags=visibleFlags(),currentViewProviders=visibleProviders(),currentViewNumbers=visibleNumbers();
    const kinds=visibleKinds(group);
    if(view==="learning"){
      commitView(renderLearning(cfg,group));
      return;
    }
    const heading=view==="expression"?"表情配图":group?"群词义与语义设置":"语义理解与学习素材";
    let html='<div class="settings-heading"><div><h2>'+heading+'</h2></div><div class="refresh-cluster"><span class="semantic-state '+escape(state.state)+'">'+escape({ready:"嵌入已就绪",lexical_fallback:"关键词回退",disabled:"已停用"}[state.state]||"状态待核对")+'</span>'+(refreshedAt?'<span class="refresh-stamp" role="status">刚刚已刷新 '+escape(refreshedAt)+'</span>':'')+'<button type="button" class="lh-refresh'+(refreshing?' is-refreshing':'')+'" data-semantic="refresh" aria-label="重新读取" title="重新读取" aria-busy="'+(refreshing?'true':'false')+'" '+(busy?'disabled':'')+'><i data-lucide="refresh-cw"></i></button></div></div>';
    if(message)html+='<p class="semantic-feedback'+(conflict?" semantic-conflict":"")+'" role="status">'+escape(message)+'</p>';
    html+='<section class="semantic-section"><div class="semantic-heading"><h3>'+(view==="expression"?"表情使用与自动收录":"语义模型与学习")+(view==="expression"?window.CompanionWorkbench.scopeHintMarkup("表情包使用、自动收录与检索策略由所有账号与角色共用。\n主动联系使用表情包需开启「允许主动消息带表情包」。", "查看表情设置范围与使用说明"):"")+'</h3></div><form id="semanticSettings">';
    html+='<div class="semantic-flags">'+currentViewFlags.map(([id,label])=>toggle(id,label,shown[id])).join("")+'</div>';
    const currentViewSelects=visibleSelects();
    if(currentViewProviders.length||currentViewNumbers.length||currentViewSelects.length)html+='<div class="semantic-providers">';
    currentViewProviders.forEach(([id,label,type])=>html+='<label>'+label+'<select name="'+id+'">'+options(state.providers?.[type],shown[id])+'</select></label>');
    currentViewNumbers.forEach(([id,label,min,max,step])=>{const value=percentageFields.has(id)?Number(shown[id]||0)*100:
      id==="learning_interval_seconds"?Math.round(Number(shown[id]||0)/60):shown[id];
      html+='<label>'+label+'<input name="'+id+'" type="number" min="'+min+'" max="'+max+'" step="'+(step||1)+'" value="'+value+'"></label>';});
    currentViewSelects.forEach(([id,label,choices])=>html+='<label>'+label+'<select name="'+id+'">'+choices.map(([value,text])=>'<option value="'+escape(value)+'" '+(shown[id]===value?'selected':'')+'>'+escape(text)+'</option>').join('')+'</select></label>');
    if(currentViewProviders.length||currentViewNumbers.length||currentViewSelects.length)html+='</div>';
    if(currentViewProviders.length&&!(state.providers?.embedding||[]).length)html+='<p class="semantic-meta">没有读到向量模型。请先在 AstrBot 的模型服务页添加 embedding 类型的 Provider，再回到这里选择。</p>';
    html+='<div class="semantic-actions"><button class="button primary" type="submit" '+(busy?"disabled":"")+'>'+(draft?'保存草稿':'保存配置')+'</button>';
    /* 冲突时更要能放弃：否则用户只剩「按新版本再存一次」一条路。 */
    if(draft)html+='<button class="button" type="button" data-semantic="discard">放弃草稿</button>';
    html+='<button class="button" type="button" data-semantic="reindex" '+(busy||!state.selection_ready?"disabled":"")+'>重建当前索引</button></div></form>';
    const recentErrors=Object.keys(state.errors||{}).length
      ?'最近异常：'+escape([...new Set(Object.values(state.errors).map(code=>localized({code},"语义处理暂未完成，请核对模型配置。")))].join("、")) : "";
    if(recentErrors)html+='<p class="semantic-meta">'+recentErrors+'</p>';
    html+='</section>';
    if(view==="expression")html+=collectionSummary(state.reaction_collection,state.catalog?.reaction);
    const reactionCatalog=state.catalog?.reaction||{};
    const reactionEnabled=Math.max(0,Number(reactionCatalog.enabled||0));
    const reactionTotalValue=Number(reactionCatalog.total);
    const reactionTotal=Math.max(reactionEnabled,Number.isFinite(reactionTotalValue)&&reactionTotalValue>=0?reactionTotalValue:0);
    html+='<section class="semantic-section"><div class="semantic-heading"><h3>'+(view==="expression"?"表情库":"群词义素材")+(view==="expression"?window.CompanionWorkbench.scopeHintMarkup("图库属于当前小屋角色；切换小屋角色即可查看其表情。", "查看表情库范围"):"")+'</h3><span>'+(view==="expression"?'可用 '+reactionEnabled+' / '+reactionTotal+' 项':'群词义 '+(state.catalog?.slang?.enabled||0)+' 项')+'</span></div>';
    if(group&&state.sessions?.length)html+='<label class="semantic-session">群会话<select id="semanticSession" aria-label="群会话">'+state.sessions.map((id,index)=>'<option value="'+escape(id)+'" '+(id===state.session_id?'selected':'')+'>'+escape(id===target()?.group_id?'主群会话':'群会话 '+(index+1))+'</option>').join('')+'</select></label>';
    if(kinds.length===1&&!catalogReady(kinds[0]))html+='<p class="memory-notice">当前群会话尚无可验证的成员输入；收到群消息后即可管理。</p>';
    else if(kinds.length) {
      /* 表情 / 群词义两个切页：按 ARIA tabs 写全（tablist + tab + 一个 tabpanel），
         之前只给了 role="tab"，读屏器报「标签页」却找不到受控面板。 */
      if(kinds.length>1)html+='<div class="semantic-toolbar"><div class="semantic-tabs" role="tablist"><button role="tab" id="semantic-tab-reaction" aria-controls="semantic-panel" aria-selected="'+(kind==="reaction")+'" data-semantic-kind="reaction">表情</button><button role="tab" id="semantic-tab-slang" aria-controls="semantic-panel" aria-selected="'+(kind==="slang")+'" data-semantic-kind="slang">群词义</button></div>';
      else html+='<div class="semantic-toolbar"><span class="semantic-kind-label">'+(kind==="reaction"?"表情素材":"当前群词义")+'</span>';
      if(catalogReady())html+='<form id="semanticSearch"><input name="query" type="search" maxlength="2000" placeholder="检索'+(kind==="reaction"?"表情":"群词义")+'" aria-label="检索素材"><button class="button" '+(busy?"disabled":"")+'>检索</button></form>';
      html+='</div>';
      html+=`<div id="semantic-panel" role="tabpanel"${kinds.length>1?` aria-labelledby="semantic-tab-${kind}"`:""}>`;
      if(!catalogReady())html+='<p class="memory-notice">当前群会话尚无可验证的成员输入；收到群消息后即可管理。</p>';
      else {
      if(kind==="reaction")html+='<label class="semantic-import button"><span>导入素材</span><input id="semanticFile" type="file" multiple accept="image/png,image/jpeg,image/webp,image/gif" '+(busy?"disabled":"")+'></label>'+batchAnalyzeButton();
      if(result)html+='<div class="semantic-results"><strong>检索结果 · '+(result.mode==="hybrid"?"语义与关键词":"关键词")+'</strong><span>'+(result.embedded||0)+'/'+(result.eligible||0)+' 已建索引'+(result.fallback_reason?" · 模型暂不可用":"")+'</span>'+(result.items||[]).map((item,index)=>'<button data-semantic-open="'+escape(item.id)+'">'+escape(item.payload.name||item.payload.term||'未命名素材 '+(index+1))+' · '+Number(item.score).toFixed(2)+'</button>').join("")+'</div>';
      html+='<div class="semantic-list">'+(state.items||[]).map(item=>{const payload=item.payload||{};const migrated=payload.collection_source==="legacy_migration"||Boolean(payload.legacy_source_id||payload.legacy_filename);const source={private_capture:"私聊自动收录",group_capture:"群聊自动收录",legacy_migration:"V1 表情迁移"}[payload.collection_source]||(migrated?"V1 表情迁移":"手动收录");
        const analysisCode=migrated&&(!payload.analysis||payload.analysis==="pending")
          ?(payload.manual_fields?.length?"legacy_imported":"legacy_pending"):payload.analysis;
        const analysis={auto_reviewed:"已通过视觉审核",analyzed:"已识别",legacy_imported:"V1 迁移保留标注",legacy_pending:"V1 迁移 · 待补全",pending:"待识别",manual:"手工标注"}[analysisCode]||"待标注";
        const collectionState=payload.collection_state==="awaiting_confirmation"?"待人工确认":"";
        const sourceUrl=imageSource(item.thumbnail_url||item.image_url||payload.image_url||payload.url);
        const media=sourceUrl?'<img src="'+escape(sourceUrl)+'" alt="" loading="lazy">':'<span class="semantic-row-placeholder" aria-hidden="true"><i data-lucide="image"></i></span>';
        const title=payload.name||payload.term||"未命名";
        const detail=payload.description||payload.meaning||"待标注";
        return '<article class="semantic-row" data-semantic-open="'+escape(item.id)+'"><div class="semantic-row-media">'+media+'<span class="semantic-row-state '+(item.enabled?"is-enabled":"is-disabled")+'">'+(item.enabled?"已启用":"已停用")+'</span></div><div class="semantic-row-body"><strong>'+escape(title)+'</strong><p>'+escape(detail)+'</p><small>'+escape(collectionState||source)+' · '+escape(analysis)+(item.source_valid===false?" · 来源已失效":"")+'</small></div><button class="button small" type="button" data-semantic-open="'+escape(item.id)+'">编辑</button></article>';}).join("")+'</div>';
      if(!state.items?.length)html+='<p class="memory-notice">当前没有素材。</p>';
      if(state.next_after)html+='<button class="button" data-semantic="more" '+(busy?'disabled':'')+'>加载更多</button>';
      }
      html+='</div>';
    }
    if(kinds.length)html+='</section>';
    commitView(html);
  }
  function collectionSummary(summary,catalog={}) {
    const totals=summary?.totals||{},recent=summary?.recent||[];
    const labels={added:"已自动启用",pending_confirmation:"待人工确认",duplicate:"重复跳过",
      rejected:"审核拒绝",failed:"处理失败",cancelled:"配置变更后取消",checking:"正在审核"};
    const scopes=[["private_capture","私聊"],["group_capture","群聊"]];
    const enabled=Math.max(0,Number(catalog.enabled||0));
    /* 部分旧宿主只返回 enabled；可用素材不能显示成多于全部素材。 */
    const rawTotal=Number(catalog.total);
    const total=Math.max(enabled,Number.isFinite(rawTotal)&&rawTotal>=0?rawTotal:0);
    /* 待处理与识别比例只按当前图库实际状态计算，不叠加单调递增的历史收录累计值。 */
    const pendingAnalysis=Math.max(0,Number(catalog.pending||0));
    const awaitingConfirmation=Math.max(0,Number(catalog.awaiting_confirmation||0));
    const pending=pendingAnalysis+awaitingConfirmation;
    const recognized=Math.min(total,Math.max(0,
      typeof catalog.recognized==="number"&&Number.isFinite(catalog.recognized)
        ?catalog.recognized:Math.max(0,total-pendingAnalysis)));
    const ratio=total?Math.min(100,Math.round(recognized/total*100)):0;
    const cards=[["全部素材",total],["可用",enabled],["已识别",recognized],["待处理",pending]]
      .map(([label,value])=>'<div class="semantic-catalog-stat"><strong>'+value+'</strong><span>'+label+'</span></div>').join('');
    const stats=scopes.map(([scope,label])=>{
      const counts=totals[scope]||{};
      const value=["added","pending_confirmation","duplicate","rejected","failed","cancelled"]
        .filter(status=>Number(counts[status]||0)>0)
        .map(status=>labels[status]+" "+counts[status]).join(" · ")||"暂无收录记录";
      return '<div class="semantic-collection-scope"><strong>'+label+'</strong><span>'+escape(value)+'</span></div>';
    }).join("");
    const entries=recent.length?recent.slice(0,5).map(row=>'<li><span>'+escape(scopes.find(([scope])=>scope===row.scope)?.[1]||"会话")+' · '+escape(labels[row.status]||"状态待核对")+'</span><time>'+escape(formatMoment(row.updated_at))+'</time></li>').join(""):'<li class="semantic-collection-empty">当前范围还没有自动收录记录。</li>';
    return '<section class="semantic-collection-summary" aria-label="表情库状态"><div class="semantic-catalog-stats">'+cards+'</div><div class="semantic-catalog-progress"><strong>自动识别 '+ratio+'%</strong><span><i style="width:'+ratio+'%"></i></span></div><div class="semantic-collection-scopes">'+stats+'</div><ul>'+entries+'</ul></section>';
  }
  function formatMoment(value) {
    if(typeof value!=="number"||!Number.isFinite(value))return "时间未知";
    try{return new Date(value*1000).toLocaleString();}catch(_){return "时间未知";}
  }
  /* 按 id 去重/合并：刷新时后端的窗口可能比上一次短，撤掉旧行等于用户眼前少了几条。 */
  function dedupeRows(rows){
    const seen=new Set();
    return rows.filter(row=>{
      const id=String(row?.id||"");
      if(!id||seen.has(id))return false;
      seen.add(id);return true;
    });
  }
  function mergeRows(previous,incoming){
    const updates=new Map();
    incoming.forEach(row=>{
      const id=String(row?.id||"");
      if(id&&!updates.has(id))updates.set(id,row);
    });
    const existingIds=new Set(previous.map(row=>String(row?.id||"")).filter(Boolean));
    const added=[];
    const seenAdded=new Set();
    incoming.forEach(row=>{
      const id=String(row?.id||"");
      if(!id||existingIds.has(id)||seenAdded.has(id))return;
      seenAdded.add(id);
      added.push(row);
    });
    const updated=previous.map(row=>{
      const id=String(row?.id||"");
      return id&&updates.has(id)?updates.get(id):row;
    });
    return dedupeRows([...added,...updated]);
  }
  /* 目录列表跟着整页滚，重绘前后把窗口位置复位（本页也受 overflow-anchor:none 影响）。 */
  function rememberScroll(){
    return typeof window.scrollY==="number"&&Number.isFinite(window.scrollY)?window.scrollY:null;
  }
  function restoreScroll(value){
    if(value===null||typeof window.scrollTo!=="function")return;
    if(Math.abs((Number(window.scrollY)||0)-value)<=1)return;
    window.scrollTo(0,value);
  }
  /* 按模式逐页读一个 kind：refresh 沿服务端实时 next_after 重读已加载页数，消除游标漂移；
     中途某页失败时停在该页并标记 failed=true，保留前缀与旧行。 */
  async function readChain(type, options = {}) {
    const append = options.append === true;
    const rounds = append ? 1 : Math.min(MAX_REFRESH_PAGES, Math.max(1, Number(options.rounds) || 1));
    const rows = [], used = [];
    let cursor = append ? (options.startCursor || "") : "";
    let next = "", meta = null, failed = false, failure = null;
    for (let round = 0; round < rounds; round += 1) {
      let page;
      try { page = await api("GET", {...selection(), kind:type, ...(cursor ? {after:cursor} : {})}); }
      catch (error) { failed = true; failure = error; break; }
      if (!Array.isArray(page?.items)) { failed = true; break; }
      if (!meta) meta = page;
      used.push(cursor);
      rows.push(...page.items);
      const nextCursor = typeof page.next_after === "string" && page.next_after ? page.next_after : "";
      next = nextCursor === cursor ? "" : nextCursor;
      if (!next) break;
      cursor = next;
    }
    return {type, rows, next, used, meta, failed, failure};
  }
  /* 读取有三种模式：
       reset   首次挂载/换类型/换会话：重建页记录，只读第 1 页；
       append  「加载更多」：往后读一页并追加；
       refresh 写操作后/重新读取：沿实时游标重读已加载页数，完整成功时以服务端结果权威替换；
               仅当某个类型失败时保留该类型旧项并明示（导入后仍有下一页时保留顺移项）。 */
  async function load(modeOrOptions = "reset") {
    const opts = typeof modeOrOptions === "object" && modeOrOptions !== null
      ? modeOrOptions
      : {mode: modeOrOptions === true ? "append" : String(modeOrOptions || "reset")};
    const mode = opts.mode || "reset";
    const append = mode === "append", refresh = mode === "refresh";
    const manual = opts.manual === true, afterImport = opts.afterImport === true;
    if (!current() || !product.hosted() || ((append || refresh) && busy)) return;
    const ticket = ++generation;
    const previous = state;
    const oldItems = previous?.items || [];
    const scrollTop = (append || refresh) ? rememberScroll() : null;
    if (!append && !refresh) { message = ""; refreshedAt = ""; }
    else if (manual) { message = ""; conflict = false; }
    busy = true; refreshing = refresh;
    if (!append && !refresh) state = null;
    render();
    try {
      const group = target()?.scope_kind === "group";
      const kinds = view === "learning" ? visibleKinds(group) : [kind];
      const optionsFor = type => refresh
        ? {append: false, startCursor: "", rounds: Math.max(1, loaded[type]?.length || 1)}
        : append ? (after[type] ? {append: true, startCursor: after[type], rounds: 1} : null)
          : {append: false, startCursor: "", rounds: 1};
      const fetchAll = () => Promise.all(kinds.map(type => {
        const plan = optionsFor(type);
        return plan ? readChain(type, plan) : Promise.resolve({type, rows:[], next:"", used:[], meta:null, failed:false, failure:null, skipped:true});
      }));
      let chains = await fetchAll();
      if (!active(ticket)) return;
      if (!append && !refresh && !sessionId && chains[0]?.meta && !chains[0].meta.selection_ready && chains[0].meta.sessions?.length) {
        sessionId = chains[0].meta.sessions[0]; chains = await fetchAll();
      }
      if (!active(ticket)) return;
      const fresh = chains.find(chain => chain.meta)?.meta || null;
      const failure = chains.find(chain => chain.failure)?.failure || null;
      const anyFailed = chains.some(chain => chain.failed);
      if (!fresh) {
        /* 一页都没读到：旧内容原地保留，只提示这次没读到（保留具体错误码的翻译）。 */
        message = failure ? localized(failure, "读取失败，请重试。") : localized({code:"invalid_response"}, "读取失败，请重试。");
        return;
      }
      const rowsByKind = type => oldItems.filter(item => (item.kind || kind) === type);
      const nextRows = [];
      chains.forEach(chain => {
        const cleanIncoming = chain.rows.filter(item => !deletedIds.has(String(item?.id || "")));
        const prevKind = rowsByKind(chain.type);
        if (chain.skipped) {
          nextRows.push(...prevKind);
          return;
        }
        if (chain.failed) {
          /* 部分失败：保留该类型旧项并合并已读到的前缀，不缩小页数。 */
          nextRows.push(...(chain.rows.length ? mergeRows(prevKind, cleanIncoming) : prevKind));
          return;
        }
        if (!append && !refresh) {
          loaded[chain.type] = chain.used.length ? chain.used.slice() : [""];
          nextRows.push(...dedupeRows(cleanIncoming));
        } else if (append) {
          loaded[chain.type] = (loaded[chain.type] || [""]).concat(chain.used);
          nextRows.push(...dedupeRows([...prevKind, ...cleanIncoming]));
        } else {
          loaded[chain.type] = chain.used.length ? chain.used.slice() : [""];
          /* 导入新增后若第一页仍未读完（存在下一页），保留被新条目顺移到后页的旧项；
             其余完整刷新以服务端返回权威替换，外部已删除素材即时移除。 */
          nextRows.push(...(afterImport && chain.next ? mergeRows(prevKind, cleanIncoming) : dedupeRows(cleanIncoming)));
        }
        after[chain.type] = chain.next;
      });
      const rows = dedupeRows(nextRows);
      state = {...fresh, items:rows, next_after:after[kind] || null};
      if (anyFailed || failure) {
        const detail = failure ? localized(failure, "") : "";
        message = detail ? `部分素材读取失败：${detail}` : "部分素材读取失败，已显示其余内容。";
      } else if (manual) {
        refreshedAt = formatClock();
      }
    }
    catch (error) { if (active(ticket)) message = localized(error, "读取失败，请重试。"); }
    finally { if (active(ticket)) { busy = false; refreshing = false; render(); if (scrollTop !== null) restoreScroll(scrollTop); } }
  }
  async function run(action,payload={}){
    if(busy||!current())return;
    const ticket=++generation;
    busy=true;message="正在处理…";conflict=false;render();
    try{
      const data=await api("POST",{...selection(),action,...payload});
      if(!active(ticket))return;
      if(action==="search"){result=data;message="";busy=false;render();return;}
      message={configure:"配置已保存",reindex:"索引重建已安排",import:data.created?"表情已收录":"原图已存在，已跳过重复项",analyze:"空白字段已补全，人工标注已保留",edit:"素材已保存",delete:"表情素材已删除"}[action];
      editing=null;busy=false;
      /* 配置保存成功才清草稿；素材类操作不该动设置草稿。 */
      if(action==="configure")draft=null;
      if(action==="import"&&data?.item?.id)deletedIds.delete(String(data.item.id));
      /* 删除掉的素材要记住：先就地摘掉，后续刷新若把旧数据返回来也不能再显示。 */
      if(action==="delete"&&payload.id){
        deletedIds.add(String(payload.id));
        if(state)state={...state,items:(state.items||[]).filter(item=>String(item.id)!==String(payload.id))};
      }
      await load(action === "import" ? {mode:"refresh", afterImport:true} : "refresh");
    }catch(error){
      if(!active(ticket))return;
      busy=false;
      if(action==="configure"&&String(error.message||"").indexOf("settings changed")>=0){
        /* 版本冲突：服务端已被别处改过。把这次提交的内容留作草稿，重读拿到新版本号，
           用户填的值不被服务端旧值覆盖，也不会显示成「已保存」。 */
        conflict=true;
        draft={...(payload.changes||{})};
        message="这份配置已被别处修改。你填的值仍保留在下面，核对后再次保存即可按新版本写入。";
        try{await load("refresh");}catch(_){render();return;}
        return;
      }
      message=localized(error,"操作失败，请刷新核对。");
      render();
    }
  }
  async function open(id){
    if(busy)return;const ticket=++generation;busy=true;message="正在读取原件…";render();
    try{const data=await api("GET",{...selection(),kind,id});if(active(ticket)){editing={...data.item,image_url:data.image_url};message="";}}
    catch(error){if(active(ticket))message=localized(error,"原件暂不可用");}
    finally{if(active(ticket)){busy=false;render();root.querySelector('#semanticEditor [data-semantic="close"]')?.focus?.({preventScroll:true});}}
  }
  function mount(node,options={}){
    unmount();root=node;selectedTarget=options.target||null;selectedSelection=options.selection||null;selectedScopeKey=typeof options.scopeKey==="string"?options.scopeKey:"";
    globalSettingsOnly=options.globalSettingsOnly===true;
    scope=selectedScopeKey||product.scopeKey();state=null;result=null;after={};loaded={};deletedIds=new Set();sessionId="";editing=null;message="";busy=false;
    view=["learning","expression"].includes(options?.view)?options.view:"all";
    kind=view==="learning"&&target()?.scope_kind==="group"?"slang":"reaction";abort=new AbortController();
    for(const type of ["focusin","focusout","pointerover","pointerout"])
      node.addEventListener(type,window.CompanionWorkbench.scopeHintReset,{signal:abort.signal});
    node.addEventListener("keydown",window.CompanionWorkbench.scopeHintKeydown,{signal:abort.signal});
    if(typeof document!=="undefined"&&typeof document.addEventListener==="function"){
      document.addEventListener("keydown",event=>{
        if(event.key==="Escape"&&editing&&current()){event.preventDefault();editing=null;render();}
      },{signal:abort.signal});
    }
    node.addEventListener("click",async event=>{
      if(window.CompanionWorkbench.scopeHintClick(event))return;
      const item=event.target.closest("[data-semantic-open]");if(item){kind=item.dataset.semanticKind||kind;open(item.dataset.semanticOpen);return;}
      const tab=event.target.closest("[data-semantic-kind]");if(tab){kind=tab.dataset.semanticKind;result=null;editing=null;after={};loaded={};message="";load();return;}
      const command=event.target.closest("[data-semantic]")?.dataset.semantic;
      if(command==="close"){editing=null;render();}
      if(command==="retry")load();
      if(command==="refresh")load({mode:"refresh", manual:true});
      if(command==="more")load("append");
      if(command==="reindex")run("reindex");
      if(command==="discard"){draft=null;conflict=false;message="已放弃草稿，显示当前已保存配置。";render();}
      if(command==="analyze"&&editing)run("analyze",{id:editing.id});
      if(command==="analyze-pending")analyzePending();
      if(command==="delete"&&editing&&kind==="reaction"){
        const item=editing, selectedKind=kind;
        const ticket=generation;
        const accepted=typeof window.CompanionConfirm==="function"&&await window.CompanionConfirm({
          title:"删除表情素材",message:"删除这条表情素材及其原图？",confirmText:"删除素材",danger:true,
        });
        if(accepted&&ticket===generation&&editing===item&&kind===selectedKind)run("delete",{kind:"reaction",id:item.id,revision:item.revision});
      }
    },{signal:abort.signal});
    node.addEventListener("submit",event=>{
      event.preventDefault();const form=event.target,values=new FormData(form);
      if(form.id==="semanticSettings"){
        const changes={},visibleFlagIds=new Set(visibleFlags().map(([id])=>id));
        flags.forEach(([id])=>changes[id]=visibleFlagIds.has(id)?values.has(id):shownValue(id));
        const visibleProviderIds=new Set(visibleProviders().map(([id])=>id));
        providerFields.forEach(([id])=>changes[id]=visibleProviderIds.has(id)?values.get(id)||"":shownValue(id));
        const visibleNumberIds=new Set(visibleNumbers().map(([id])=>id));
        numberFields.forEach(([id])=>{const value=Number(values.get(id));
          changes[id]=visibleNumberIds.has(id)?percentageFields.has(id)?value/100:
            id==="learning_interval_seconds"?value*60:value:shownValue(id);});
        const visibleSelectIds=new Set(visibleSelects().map(([id])=>id));
        selectFields.forEach(([id])=>changes[id]=visibleSelectIds.has(id)?values.get(id):shownValue(id));
        /* 这份内容只在版本冲突时才当作草稿保留：普通失败（类型/范围不合法）
           并没有写出任何东西，标成草稿会让人以为需要核对。 */
        run("configure",{changes,revision:state.settings.revision});
      }
      if(form.id==="semanticSearch")run("search",{kind:values.get("kind")||kind,query:values.get("query")});
      if(form.id==="semanticEditor"&&editing){
        const changes={};fields[kind].forEach(([id])=>changes[id]=values.get(id));
        run("edit",{kind,id:editing.id,revision:editing.revision,values:changes,enabled:values.has("item_enabled")});
      }
    },{signal:abort.signal});
    node.addEventListener("change",event=>{
      if(event.target.id==="semanticSession"){sessionId=event.target.value;result=null;editing=null;after={};loaded={};message="";load();return;}
      if(event.target.name==="kind"){kind=event.target.value;result=null;editing=null;return;}
      if(event.target.id!=="semanticFile"||!event.target.files?.length)return;
      const files=Array.from(event.target.files);
      try{event.target.value="";}catch(_){}
      importFiles(files);
    },{signal:abort.signal});
    load();
  }
  async function analyzePending() {
    if(busy||!current())return;
    const ticket=++generation;
    const candidates=new Map((state?.items||[]).filter(pendingAnalysis).map(item=>[item.id,item]));
    const seen=new Set();
    let cursor=after.reaction||"";
    busy=true;conflict=false;message="正在扫描全部表情素材…";render();
    try{
      // The catalog is cursor-paginated. Reading only state.items misses all
      // unloaded pages, including when page one has no pending entries.
      while(cursor){
        if(!active(ticket))return;
        if(seen.has(cursor))throw new Error("semantic_pagination_loop");
        seen.add(cursor);
        const page=await api("GET",{...selection(),kind:"reaction",after:cursor});
        if(!active(ticket))return;
        if(!Array.isArray(page?.items))throw new Error("semantic_pagination_invalid");
        for(const item of page.items)if(pendingAnalysis(item))candidates.set(item.id,item);
        cursor=page.next_after||"";
        message="正在扫描全部表情素材，已发现 "+candidates.size+" 张待补全…";render();
      }
    }catch(error){
      if(!active(ticket))return;
      busy=false;message="剩余素材读取失败，尚未开始批量识别，请重试。";render();return;
    }
    const total=candidates.size;
    if(!total){busy=false;message="没有需要补全的表情素材。";render();return;}
    let done=0,failed=0,lastError="";
    message="正在批量识别 0/"+total+"…";render();
    for(const item of candidates.values()){
      if(!active(ticket))return;
      try{
        const data=await api("POST",{...selection(),action:"analyze",id:item.id});
        if(!active(ticket))return;
        if(data?.item)Object.assign(item,data.item);
        done++;
      }catch(error){
        if(!active(ticket))return;
        failed++;lastError=error?.code||error?.message||"";
      }
      message="正在批量识别 "+(done+failed)+"/"+total+"…";render();
    }
    if(!active(ticket))return;
    busy=false;
    const parts=[];
    if(done)parts.push("已识别 "+done+" 张");
    if(failed)parts.push("失败 "+failed+" 张");
    message=parts.join("，")||"没有可识别的表情素材";
    if(failed&&!done)message=localized({code:lastError},"批量识别失败，请核对视觉模型配置后重试。");
    await load("refresh");
  }
  async function importFiles(files) {
    if(busy||!current())return;
    const selected=files.slice(0,20),omitted=Math.max(0,files.length-selected.length);
    const ticket=++generation;let created=0,duplicates=0,failed=omitted,lastError="";
    busy=true;message="正在导入表情素材…";render();
    for(const file of selected){
      if(!active(ticket))return;
      if(typeof file.size==="number"&&file.size<=0){failed++;lastError="host_image_invalid";continue;}
      if(file.size>5*1024*1024){failed++;lastError="host_image_limit";continue;}
      try{
        const data=await readDataUrl(file);
        const comma=data.indexOf(",");
        const encoded=comma>=0?data.slice(comma+1).trim():"";
        if(!encoded){failed++;lastError="host_image_invalid";continue;}
        const metadata=file.name?{source_filename:String(file.name)}:{};
        const result=await api("POST",{...selection(),action:"import",content:encoded,metadata});
        if(!active(ticket))return;
        if(result?.item?.id)deletedIds.delete(String(result.item.id));
        if(result.created)created++;else duplicates++;
      }catch(error){failed++;lastError=error?.code||error?.message||"";}
    }
    if(!active(ticket))return;
    const parts=[];
    if(created)parts.push("新增 "+created+" 张");
    if(duplicates)parts.push("重复跳过 "+duplicates+" 张");
    if(failed)parts.push("失败 "+failed+" 张"+(omitted?"（超出每批 20 张）":""));
    if(created)parts.push("可点「批量识别待补全」补全空白字段");
    busy=false;message=parts.join("，")||"没有可导入的图片";
    if(failed&&lastError&&!created&&!duplicates)message=localized({code:lastError},"批量导入失败，请检查图片格式和大小。");
    await load({mode:"refresh", afterImport:true});
  }
  function readDataUrl(file) {
    return new Promise((resolve,reject)=>{
      const reader=new FileReader();
      reader.onload=()=>resolve(String(reader.result||""));
      reader.onerror=()=>reject(new Error("image_read_failed"));
      reader.readAsDataURL(file);
    });
  }
  function shownValue(id){return (draft?{...state.settings,...draft}:state.settings)[id];}
  function unmount(){clearDrawerTimers();drawerEl=null;if(root)window.CompanionWorkbench.dismissScopeHints(root);generation++;abort?.abort();abort=null;root=null;scope=null;state=null;editing=null;busy=false;refreshing=false;refreshedAt="";selectedTarget=null;selectedSelection=null;selectedScopeKey="";globalSettingsOnly=false;}
  /* 页面级未保存守卫（page-guards.js）：草稿存在就说明有未提交的改法。
     unmount 已经把 draft 之外的引用清掉，这里跟着 root 一起判断。 */
  function isDirty(){return !!draft&&!!root;}
  function changedFields(){return Object.keys(draft||{}).map(id=>
    [...flags,...providerFields,...numberFields,...selectFields].find(item=>item[0]===id)?.[1]||id);}
  async function saveBeforeLeave(){
    if(!isDirty())return true;
    if(busy)return {error:'语义配置正在处理，请稍后保存。'};
    await run('configure',{changes:{...draft},revision:state.settings.revision});
    return !isDirty()?true:{error:message||'语义配置未保存，草稿仍保留。'};
  }
  function discardChanges(){if(busy)return {error:'正在处理，请稍后切换。'};draft=null;conflict=false;return true;}
  return {mount,unmount,isDirty,changedFields,saveBeforeLeave,discardChanges};
})();
