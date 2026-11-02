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
  };
  const localized = (failure, fallback) => errors[failure?.code]
    || errors[failure?.message]
    || fallback;
  const reactionFlags = new Set(["reactions_enabled","reactions_private_enabled","reactions_group_enabled",
    "reactions_collection_private_enabled","reactions_collection_group_enabled","vision_review"]);
  const flags = [["enabled","启用语义目录"],["memory_enabled","语义记忆检索"],
    ["reactions_enabled","启用表情包表达"],["reactions_private_enabled","私聊使用表情包"],
    ["reactions_group_enabled","群聊使用表情包"],
    ["reactions_collection_private_enabled","从私聊自动收录"],
    ["reactions_collection_group_enabled","从群聊自动收录"],
    ["slang_enabled","群聊黑话目录"],["slang_learning","黑话自动学习"],
    ["slang_web_search_enabled","黑话联网参考"],["vision_review","识图复核"]];
  const providerFields = [["embedding_provider","向量模型（嵌入）","embedding"],["rerank_provider","候选重排","rerank"],["analysis_provider","视觉与词义分析","analysis"]];
  const semanticNumberFields = [["timeout_seconds","模型超时 / 秒",1,30],["batch_size","索引批次",1,64],
    ["learning_interval_seconds","群词义整理间隔 / 分钟",1,1440],
    ["slang_web_search_candidates","联网检索词数 / 个",1,12],["slang_web_search_results","每词参考条数",1,5]];
  const reactionNumberFields = [["reactions_trigger_probability","自动发送概率 / %",0,100,1],
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
  let root,scope,abort,state,kind="reaction",view="all",after={},sessionId="",busy=false,editing=null,result=null,message="",generation=0,draft=null,conflict=false,selectedTarget=null,selectedSelection=null,selectedScopeKey="",globalSettingsOnly=false;
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
  function editor(item) {
    let html='<div class="semantic-editor-drawer"><div class="semantic-editor-backdrop" data-semantic="close" aria-hidden="true"></div><form id="semanticEditor" class="semantic-editor"><div class="semantic-heading"><h3>'+escape(item.payload.name||item.payload.term||"素材详情")+'</h3><button type="button" class="icon-button" data-semantic="close" aria-label="关闭编辑" title="关闭"><i data-lucide="x"></i></button></div>';
    const source=imageSource(item.image_url||item.payload?.image_url||item.payload?.url);
    if(source)html+='<img src="'+escape(source)+'" alt="'+escape(item.payload.name||"表情原图")+'" loading="lazy">';
    html+=fields[kind].map(([id,label])=>'<label>'+label+'<textarea name="'+id+'" rows="'+(id==="description"||id==="meaning"?3:2)+'" maxlength="'+(kind==="slang"?500:1500)+'">'+escape(item.payload[id]||"")+'</textarea></label>').join("");
    html+=toggle("item_enabled","允许检索使用",!!item.enabled);
    html+='<div class="semantic-actions"><button class="button primary" type="submit" '+(busy?"disabled":"")+'>保存修改</button>';
    if(kind==="reaction")html+='<button class="button" type="button" data-semantic="analyze" '+(busy?"disabled":"")+'>补全空白字段</button>';
    if(kind==="reaction")html+='<button class="icon-button danger" type="button" data-semantic="delete" aria-label="删除表情素材" title="删除表情素材" '+(busy?"disabled":"")+'><i data-lucide="trash-2"></i></button>';
    return html+'<button class="button" type="button" data-semantic="close">取消</button></div></form></div>';
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
    let html='<div class="settings-heading"><div><h2>'+(globalSettingsOnly?'全局语义策略':'语义目录')+'</h2></div><span class="semantic-state '+escape(state.state)+'">'+escape({ready:"嵌入已就绪",lexical_fallback:"关键词回退",disabled:"已停用"}[state.state]||"状态待核对")+'</span></div>';
    if(message)html+='<p class="semantic-feedback'+(conflict?" semantic-conflict":"")+'" role="status">'+escape(message)+'</p>';
    if(!globalSettingsOnly){
    html+='<section class="semantic-learning-catalog" aria-label="语义条目"><header class="semantic-learning-heading"><h3>语义条目 · 阵列</h3><span>'+escape(counts)+'</span></header>';
    if(group&&state.sessions?.length)html+='<label class="semantic-session">群会话<select id="semanticSession" aria-label="群会话">'+state.sessions.map((id,index)=>'<option value="'+escape(id)+'" '+(id===state.session_id?'selected':'')+'>'+escape(id===target()?.group_id?'主群会话':'群会话 '+(index+1))+'</option>').join('')+'</select></label>';
    if(!state.selection_ready)html+='<p class="semantic-learning-empty" role="status">该群会话暂无语义条目</p>';
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
      if(editing)html+=editor(editing);
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
    if(!product.hosted()){root.innerHTML='<div class="settings-heading"><div><h2>语义理解与素材</h2></div></div><p class="memory-notice" role="status">角色与会话目录暂未读到。</p><div class="semantic-actions"><button class="button" type="button" data-workspace-retry>重新读取</button></div>';return;}
    if(!state){
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
      root.innerHTML=renderLearning(cfg,group);
      window.lucide?.createIcons({nodes:root.querySelectorAll("[data-lucide]")});
      return;
    }
    const heading=view==="expression"?"表情配图":group?"群词义与语义设置":"语义理解与学习素材";
    let html='<div class="settings-heading"><div><h2>'+heading+'</h2></div><span class="semantic-state '+escape(state.state)+'">'+escape({ready:"嵌入已就绪",lexical_fallback:"关键词回退",disabled:"已停用"}[state.state]||"状态待核对")+'</span></div>';
    if(message)html+='<p class="semantic-feedback'+(conflict?" semantic-conflict":"")+'" role="status">'+escape(message)+'</p>';
    html+='<section class="semantic-section"><div class="semantic-heading"><h3>'+(view==="expression"?"表情使用与自动收录":"语义模型与学习")+'</h3></div><form id="semanticSettings">';
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
    const reactionTotal=Math.max(0,Number.isFinite(reactionTotalValue)&&reactionTotalValue>0?reactionTotalValue:reactionEnabled);
    if(kinds.length)html+='<section class="semantic-section"><div class="semantic-heading"><h3>'+(view==="expression"?"表情库":"群词义素材")+'</h3><span>'+(view==="expression"?'可用 '+reactionEnabled+' / '+reactionTotal+' 项':'群词义 '+(state.catalog?.slang?.enabled||0)+' 项')+'</span></div>';
    if(group&&state.sessions?.length)html+='<label class="semantic-session">群会话<select id="semanticSession" aria-label="群会话">'+state.sessions.map((id,index)=>'<option value="'+escape(id)+'" '+(id===state.session_id?'selected':'')+'>'+escape(id===target()?.group_id?'主群会话':'群会话 '+(index+1))+'</option>').join('')+'</select></label>';
    if(kinds.length&&!state.selection_ready)html+='<p class="memory-notice">当前群会话尚无可验证的成员输入；收到群消息后即可管理。</p>';
    else if(kinds.length) {
      /* 表情 / 群词义两个切页：按 ARIA tabs 写全（tablist + tab + 一个 tabpanel），
         之前只给了 role="tab"，读屏器报「标签页」却找不到受控面板。 */
      if(kinds.length>1)html+='<div class="semantic-toolbar"><div class="semantic-tabs" role="tablist"><button role="tab" id="semantic-tab-reaction" aria-controls="semantic-panel" aria-selected="'+(kind==="reaction")+'" data-semantic-kind="reaction">表情</button><button role="tab" id="semantic-tab-slang" aria-controls="semantic-panel" aria-selected="'+(kind==="slang")+'" data-semantic-kind="slang">群词义</button></div>';
      else html+='<div class="semantic-toolbar"><span class="semantic-kind-label">'+(kind==="reaction"?"表情素材":"当前群词义")+'</span>';
      html+='<form id="semanticSearch"><input name="query" type="search" maxlength="2000" placeholder="检索'+(kind==="reaction"?"表情":"群词义")+'" aria-label="检索素材"><button class="button" '+(busy?"disabled":"")+'>检索</button></form></div>';
      html+=`<div id="semantic-panel" role="tabpanel"${kinds.length>1?` aria-labelledby="semantic-tab-${kind}"`:""}>`;
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
      if(editing)html+=editor(editing);
      html+='</div>';
    }
    if(kinds.length)html+='</section>';
    root.innerHTML=html;
    window.lucide?.createIcons({nodes:root.querySelectorAll("[data-lucide]")});
  }
  function collectionSummary(summary,catalog={}) {
    const totals=summary?.totals||{},recent=summary?.recent||[];
    const labels={added:"已自动启用",pending_confirmation:"待人工确认",duplicate:"重复跳过",
      rejected:"审核拒绝",failed:"处理失败",cancelled:"配置变更后取消",checking:"正在审核"};
    const scopes=[["private_capture","私聊"],["group_capture","群聊"]];
    const enabled=Math.max(0,Number(catalog.enabled||0));
    /* 部分旧宿主只返回 enabled；可用素材不能显示成多于全部素材。 */
    const rawTotal=Number(catalog.total);
    const total=Math.max(0,Number.isFinite(rawTotal)&&rawTotal>0?rawTotal:enabled);
    const pending=Math.max(0,Number(catalog.pending||0)+Number(totals.private_capture?.pending_confirmation||0)+Number(totals.group_capture?.pending_confirmation||0));
    const recognized=Math.min(total,Math.max(0,Number(catalog.recognized??Math.max(0,enabled-pending))));
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
  async function load(append=false){
    if(!current()||!product.hosted()||(append&&busy))return;
    const ticket=++generation;
    const old=append?state?.items||[]:[];
    if(append||!state)message="";
    busy=true;if(!append)state=null;render();
    try{
      const group=target()?.scope_kind==="group";
      const kinds=view==="learning"?visibleKinds(group):[kind];
      const fetchPages=()=>Promise.all(kinds.map(type=>append&&!after[type]
        ?Promise.resolve({items:[],next_after:null})
        :api("GET",{...selection(),kind:type,...(append&&after[type]?{after:after[type]}:{})})));
      let pages=await fetchPages();
      if(!active(ticket))return;
      if(!append&&!sessionId&&pages[0]&&!pages[0].selection_ready&&pages[0].sessions?.length){
        sessionId=pages[0].sessions[0];pages=await fetchPages();
      }
      if(!active(ticket))return;
      state={...pages[0],items:[...old,...pages.flatMap(page=>page.items||[])]};
      after=Object.fromEntries(kinds.map((type,index)=>[type,pages[index]?.next_after||""]));
    }
    catch(error){if(active(ticket))message=localized(error,"读取失败，请重试。");}
    finally{if(active(ticket)){busy=false;render();}}
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
      await load();
    }catch(error){
      if(!active(ticket))return;
      busy=false;
      if(action==="configure"&&String(error.message||"").indexOf("settings changed")>=0){
        /* 版本冲突：服务端已被别处改过。把这次提交的内容留作草稿，重读拿到新版本号，
           用户填的值不被服务端旧值覆盖，也不会显示成「已保存」。 */
        conflict=true;
        draft={...(payload.changes||{})};
        message="这份配置已被别处修改。你填的值仍保留在下面，核对后再次保存即可按新版本写入。";
        try{await load();}catch(_){render();return;}
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
    finally{if(active(ticket)){busy=false;render();root.querySelector("#semanticEditor")?.scrollIntoView({block:"nearest"});}}
  }
  function mount(node,options={}){
    unmount();root=node;selectedTarget=options.target||null;selectedSelection=options.selection||null;selectedScopeKey=typeof options.scopeKey==="string"?options.scopeKey:"";
    globalSettingsOnly=options.globalSettingsOnly===true;
    scope=selectedScopeKey||product.scopeKey();state=null;result=null;after={};sessionId="";editing=null;message="";busy=false;
    view=["learning","expression"].includes(options?.view)?options.view:"all";
    kind=view==="learning"&&target()?.scope_kind==="group"?"slang":"reaction";abort=new AbortController();
    node.addEventListener("click",async event=>{
      const item=event.target.closest("[data-semantic-open]");if(item){kind=item.dataset.semanticKind||kind;open(item.dataset.semanticOpen);return;}
      const tab=event.target.closest("[data-semantic-kind]");if(tab){kind=tab.dataset.semanticKind;result=null;editing=null;after={};message="";load();return;}
      const command=event.target.closest("[data-semantic]")?.dataset.semantic;
      if(command==="close"){editing=null;render();}
      if(command==="retry")load();
      if(command==="more")load(true);
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
      if(event.target.id==="semanticSession"){sessionId=event.target.value;result=null;editing=null;after={};message="";load();return;}
      if(event.target.name==="kind"){kind=event.target.value;result=null;editing=null;return;}
      if(event.target.id!=="semanticFile"||!event.target.files?.length)return;
      importFiles(Array.from(event.target.files));
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
    await load();
  }
  async function importFiles(files) {
    if(busy||!current())return;
    const selected=files.slice(0,20),omitted=Math.max(0,files.length-selected.length);
    const ticket=++generation;let created=0,duplicates=0,failed=omitted,lastError="";
    busy=true;message="正在导入表情素材…";render();
    for(const file of selected){
      if(!active(ticket))return;
      if(file.size>5*1024*1024){failed++;lastError="host_image_limit";continue;}
      try{
        const data=await readDataUrl(file);
        const result=await api("POST",{...selection(),action:"import",content:data.split(",",2)[1],metadata:{name:file.name}});
        if(!active(ticket))return;
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
    await load();
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
  function unmount(){generation++;abort?.abort();abort=null;root=null;scope=null;state=null;editing=null;busy=false;selectedTarget=null;selectedSelection=null;selectedScopeKey="";globalSettingsOnly=false;}
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
