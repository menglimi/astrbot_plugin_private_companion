/* Editable V2 image backends. One draft is shared by the overview and detail page. */
"use strict";
window.CompanionOutputPage=(()=>{
  const BACKENDS={auto:"自动",online_api:"在线 API",comfyui:"ComfyUI",sd_webui:"SD WebUI"};
  const PLATFORMS={auto:"自动识别",openai:"OpenAI 兼容接口",minimax:"MiniMax"};
  const ALBUM_PAGE_SIZES=[24,48,60];
  const ALBUM_FILTERS=[["all","全部"],["image.generate","文生图"],["edit","改图"],["wardrobe.outfit","每日穿搭"]];
  const ALBUM_ACTION_LABELS={"image.generate":"文生图","image.edit":"改图","image.mask_edit":"蒙版改图","wardrobe.outfit":"每日穿搭"};
  const ALBUM_BATCH_PAGES=3;
  const ALBUM_FULL_CACHE_MAX=12;
  const REFERENCE_ROLES=[["identity","身份"],["outfit","穿搭"],["pose","姿态"],["scene","场景"],["style","风格"],["background","背景"],["continuity","连续性"],["edit_source","编辑源"],["edit_mask","编辑遮罩"]];
  const ERRORS={model_image_output_unavailable:"生图伴侣尚未加载，请更新插件并重新读取状态。",
    model_image_output_conflict:"配置已在其他页面更新，请重新读取后核对。",
    model_image_output_busy:"正在执行生图任务，请等待当前任务结束后保存。",
    model_image_output_invalid:"配置未保存，请核对地址、端点和数值。",
    model_image_output_unconfigured:"还没有可用的生图模型：先添加并启用一个在线模型，或配置本地后端，再打开总开关。",
    model_image_output_store_failed:"配置写不回插件数据目录，请检查目录权限或占用后重试。",
    model_image_output_failed:"操作未完成，请重新读取状态。",
    staging_not_initialized:"V2 执行器尚未初始化，请先加载陪伴小屋配置。",
    staging_session_still_stopping:"V2 当前会话仍在停止，请稍等片刻后再重连。",
    staging_host_terminated:"V2 执行器已停止，请重新启动插件后再重连。",
    staging_configuration_invalid:"V2 配置未通过校验，请检查宿主管理中的运行配置。",
    staging_native_reconnect_failed:"V2 执行器重连失败，请查看 AstrBot 日志中的 CompanionV2 错误后再试。",
    generated_album_limit_invalid:"每页加载数量超出允许范围（1–60 张）。",
    generated_album_selection_invalid:"请选择 1–24 张不重复的产出图片。",
    generated_album_export_too_large:"选中的图片总大小超过导出上限，请少选几张。",
    generated_album_folder_unavailable:"产出图库目录当前不可用。",
    generated_album_open_failed:"无法打开产出图库目录，请检查运行环境权限。",
    generated_image_missing:"选中的图片已经不在图库中，请刷新后重试。",
    photo_reference_disabled:"参考图功能当前关闭，请先在「参考图与衣柜」里开启。",
    reference_roles_invalid:"请至少选择一个参考用途。",
    reference_label_invalid:"参考图名称不能超过 120 个字符。",
    reference_operation_invalid:"这次登记没有生成有效的操作编号，请重新提交。",
    reference_operation_conflict:"这张图的登记编号已经对应了另一组资料，请重新选择后提交。",
    reference_scope_invalid:"参考素材只支持已绑定的私聊角色。",
    reference_asset_missing:"图片还没有保存到参考图库，请稍后重试。",
    image_usage_unavailable:"NAI 未提供费用信息，当前金额硬限制下未提交请求",
    nai_plugin_unavailable:"NAI 插件不可用，请检查启用和联动配置",
    nai_generation_timed_out:"NAI 等待超时，结果未知，未重复生成",
    nai_generation_failed:"NAI 调用未取得可确认结果，未重复生成",
    nai_generation_unconfirmed:"NAI 未返回可确认的图片，未重复生成",
    nai_request_rejected:"NAI 拒绝了本次生图参数或参考图模式（未扣费），请检查 NAI 插件模型与参考图设置",
    nai_generation_interrupted:"NAI 调用已中断，结果未知，未重复生成",
    nai_output_unavailable:"NAI 返回的图片暂不可读取"};
  let root=null,abort=null,ticket=0,scope="",base=null,draft=null,revision="",busy="",notice="",bad=false,hostRegistered=false,expanded=new Set(),tests=new Map(),testKeys=new Map(),observation=null,observationBusy=false,observationError="",observationSeq=0,observationRefreshedAt="",sceneEditorOpen=false,sceneDraft=null,sceneError="",sceneSaving=false;
  const formatClock=(date=new Date())=>`${String(date.getHours()).padStart(2,"0")}:${String(date.getMinutes()).padStart(2,"0")}:${String(date.getSeconds()).padStart(2,"0")}`;
  let albumSources=[],albumItems=[],albumCursor=null,albumTotal=null,albumLoaded=false,albumLoading=false,albumBatchLoading=false,albumBatchStep=0,albumBatchTotal=0,albumError="",albumSelected=-1,albumSelectedId="",albumSelectedIds=new Set(),albumNotice="",albumBusy="";
  let albumPageSize=24,albumFilter="all",albumAutoLoad=true,albumObserver=null,albumZoomed=false,albumCopyFeedback="",albumCopyTimer=null,albumStandaloneItem=null;
  const fullImageCache=new Map(),fullImagePending=new Map(),albumFullError=new Map();
  let albumReferenceOpen=false,albumReferenceImageId="",albumReferenceLabel="",albumReferenceRoles=new Set(["identity"]),albumReferenceError="";
  let naiStatus=null;
  let works=[],worksLoaded=false,worksBusy=false,workTicket=0,workSelection=null,authoringStatus=null;
  const AUTHORING_ERROR_LABELS={
    content_authoring_output:"模型未返回合法的创作 JSON 格式，可等待自动重试或检查写作模型路由。",
    content_authoring_disabled:"创作开关当前未开启。",
    content_authoring_capacity:"写作任务通道繁忙，稍后会自动重试。",
    task_lane_full:"后台任务通道繁忙，稍后会自动重试。",
    model_usage_unknown:"写作模型调用未返回完整结果，稍后会自动重试。",
    model_task_unregistered:"写作模型任务路由尚未注册，请在「模型与连接」确认写作模型。",
    content_provider_unavailable:"Content 创作拓展尚未就绪。",
    life_sleeping:"当时角色正在睡眠，已暂停定时创作。",
    life_schedule_changed:"对应日程时段已变更或结束。",
    content_candidate_conflict:"作品候选稿已更新，请先手动审阅再续写。",
    capability_role_not_enabled:"当前角色未开启 Content 拓展权限。",
  };
  const product=()=>window.CompanionProduct;
  const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const endpointLabel=item=>String(item?.name||"").trim()||String(item?.model||"").trim()||"未命名模型";
  const icon=name=>`<i data-lucide="${name}" aria-hidden="true"></i>`;
  const clone=value=>JSON.parse(JSON.stringify(value));
  const live=id=>root?.isConnected&&id===ticket&&scope===(product()?.scopeKey?.()||"");
  const dirty=()=>!!draft&&JSON.stringify(draft)!==JSON.stringify(base);
  const endpoint=()=>product().relative(product().role().model_api)+"/image/output";
  const observationEndpoint=()=>product().relative(product().role().model_api)+"/image/observability";
  const albumEndpoint=()=>{
    const endpoint=product()?.role?.()?.image_material_api;
    if(typeof endpoint!=="string"||!endpoint)throw Object.assign(new Error("当前角色尚无可读取的图片相册"),{code:"image_material_unavailable"});
    return product().relative(endpoint)+"/album";
  };
  // The cottage selects a role, while album ownership stays with each account.
  // Read only routes advertised for this role by the existing target directory.
  function roleAlbumSources(){
    const current=product()?.role?.(),seen=new Set(),sources=[];
    const targets=product()?.targets?.()||[];
    for(const target of targets){
      if(target.scope_kind!=="private")continue;
      for(const persona of target.roles||[]){
        if(!current?.role_id||persona.role_id!==current.role_id||!persona.image_material_api)continue;
        const endpoint=product().relative(persona.image_material_api)+"/album";
        // A concrete window and its setup template expose the same account album.
        const key=target.platform_id&&target.account_id&&target.bot_id
          ?JSON.stringify([target.platform_id,target.account_id,target.bot_id]):endpoint;
        if(seen.has(key))continue;
        seen.add(key);sources.push({endpoint,cursor:null,done:false,total:null});
      }
    }
    return sources.length?sources:[{endpoint:albumEndpoint(),cursor:null,done:false,total:null}];
  }
  function imageAlbumEndpoint(imageId){
    const item=albumItems.find(item=>item.image_id===imageId);
    if(!item?._albumEndpoint)throw new Error("图片所属账号尚未确认，请刷新图库");
    return item._albumEndpoint;
  }
  function visibleSelectedIds(){
    return filteredAlbumItems().map(item=>item.image_id).filter(imageId=>albumSelectedIds.has(imageId));
  }
  function selectedAlbumGroups(){
    const groups=new Map();
    for(const imageId of visibleSelectedIds()){
      const endpoint=imageAlbumEndpoint(imageId);
      if(!groups.has(endpoint))groups.set(endpoint,[]);
      groups.get(endpoint).push(imageId);
    }
    return groups;
  }
  const options=(values,current)=>Object.entries(values).map(([key,label])=>`<option value="${esc(key)}"${key===current?" selected":""}>${esc(label)}</option>`).join("");
  function updateMarkup(node,html){
    if(node.outputMarkup===html)return;
    node.outputMarkup=html;node.innerHTML=html;
  }
  const field=(label,path,value,type="text",attrs="",help="")=>`<label class="output-native-field"><span>${label}</span><input type="${type}" data-output-native-field="${esc(path)}" value="${esc(value)}" ${attrs}${busy?" disabled":""}>${help?`<small class="output-native-help">${esc(help)}</small>`:""}</label>`;
  const keyField=(path,item)=>`<div class="output-key-field">${field("API Key",path+".api_key","","password",`autocomplete="new-password" placeholder="${item.api_key_set?"已保存 · 留空保留":"粘贴服务商密钥"}"`,item.api_key_set?"输入新密钥会替换；留空会保留已保存密钥。":"如果服务商不要求鉴权，可留空。")}${item.api_key_set?`<label class="output-native-check"><input type="checkbox" data-output-native-field="${esc(path)}.clear_api_key"${item.clear_api_key?" checked":""}${busy?" disabled":""}>清除已保存密钥</label>`:""}</div>`;
  const platformGuide=platform=>platform==="minimax"?"MiniMax 使用 /image_generation，仅支持文生图。":platform==="auto"?"自动仅按域名中的 minimax 字样识别；自建代理请手动选择协议。":"OpenAI 兼容接口使用 /images/generations；改图使用 /images/edits。";
  const actions=()=>`<div class="output-native-actions"><button type="button" class="button small primary" data-output-native-action="save"${busy||!dirty()?" disabled":""}>${busy==="save"?"保存中…":"保存图片模型配置"}</button><button type="button" class="button small" data-output-native-action="refresh"${busy?" disabled":""}>重新读取</button><span>${dirty()?"有未保存的改动":"已保存"}</span></div>`;
  function formatTime(value){
    if(!value)return "—";
    const time=new Date(value);
    return Number.isNaN(time.getTime())?"—":new Intl.DateTimeFormat("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}).format(time);
  }
  function updatePreviewFromAlbum(){
    const image=root?.querySelector("[data-output-preview-image]");
    const frame=root?.querySelector(".output-image-frame");
    const status=root?.querySelector(".output-image-status span");
    if(!image||!frame||!status)return;
    const latest=albumItems[0];
    if(!latest?.thumbnail){image.hidden=true;image.removeAttribute("src");status.textContent="还没有生成图片";frame.dataset.imageState="unavailable";return;}
    image.alt=`${latest.width||""} × ${latest.height||""} 最近生成图片`;
    image.hidden=false;image.src=latest.thumbnail;
    status.textContent="最近生成的图片暂不可用";
  }
  function formatBytes(value){
    const bytes=Number(value);
    if(!Number.isFinite(bytes)||bytes<=0)return "";
    if(bytes<1024)return `${bytes} B`;
    if(bytes<1024*1024)return `${(bytes/1024).toFixed(bytes<10*1024?1:0)} KB`;
    return `${(bytes/(1024*1024)).toFixed(bytes<10*1024*1024?2:1)} MB`;
  }
  function formatMimeLabel(mime){
    const value=String(mime||"").toLowerCase();
    return ({"image/png":"PNG","image/jpeg":"JPEG","image/webp":"WEBP","image/gif":"GIF"})[value]
      ||(value?value.replace(/^image\//,"").toUpperCase():"");
  }
  function albumActionLabel(action){
    return ALBUM_ACTION_LABELS[action]||action||"生成图片";
  }
  function fallbackAlbumFilename(item){
    const mime=String(item?.mime_type||"").toLowerCase();
    const ext=({"image/png":".png","image/jpeg":".jpg","image/webp":".webp","image/gif":".gif"})[mime]||".png";
    const idPart=String(item?.image_id||"image").replace(/[^a-zA-Z0-9_-]+/g,"-").slice(-24)||"image";
    return `companion-${idPart}${ext}`;
  }
  function matchesAlbumFilter(item){
    if(albumFilter==="all")return true;
    if(albumFilter==="edit")return item.action==="image.edit"||item.action==="image.mask_edit";
    return item.action===albumFilter;
  }
  function filteredAlbumItems(){
    return albumFilter==="all"?albumItems:albumItems.filter(matchesAlbumFilter);
  }
  function computeAlbumTotal(sources){
    if(!sources.length)return null;
    if(!sources.every(source=>typeof source.total==="number"&&Number.isFinite(source.total)&&source.total>=0))return null;
    const sum=sources.reduce((acc,source)=>acc+source.total,0);
    return Math.max(albumItems.length,sum);
  }
  function revokeObjectUrlSafe(url){
    if(!url)return;
    try{if(typeof URL!=="undefined"&&typeof URL.revokeObjectURL==="function")URL.revokeObjectURL(url);}catch{}
  }
  function clearFullImageCache(){
    for(const entry of fullImageCache.values()){
      if(entry?.isBlobUrl)revokeObjectUrlSafe(entry.url);
    }
    fullImageCache.clear();
    fullImagePending.clear();
    albumFullError.clear();
  }
  function evictFullImage(imageId){
    const existing=fullImageCache.get(imageId);
    if(existing){
      if(existing.isBlobUrl)revokeObjectUrlSafe(existing.url);
      fullImageCache.delete(imageId);
    }
    fullImagePending.delete(imageId);
    albumFullError.delete(imageId);
  }
  function getCachedFullImage(imageId){
    if(!fullImageCache.has(imageId))return null;
    const entry=fullImageCache.get(imageId);
    fullImageCache.delete(imageId);
    fullImageCache.set(imageId,entry);
    return entry;
  }
  function putCachedFullImage(imageId,entry){
    if(fullImageCache.has(imageId)){
      const previous=fullImageCache.get(imageId);
      if(previous?.isBlobUrl&&previous.url!==entry.url)revokeObjectUrlSafe(previous.url);
      fullImageCache.delete(imageId);
    }
    fullImageCache.set(imageId,entry);
    while(fullImageCache.size>ALBUM_FULL_CACHE_MAX){
      const oldestKey=fullImageCache.keys().next().value;
      const oldest=fullImageCache.get(oldestKey);
      if(oldest?.isBlobUrl)revokeObjectUrlSafe(oldest.url);
      fullImageCache.delete(oldestKey);
    }
    return entry;
  }
  function dataUrlToViewerUrl(dataUrl,fallbackMime){
    if(typeof dataUrl!=="string"||!dataUrl.startsWith("data:"))return {url:"",isBlobUrl:false};
    if(typeof URL==="undefined"||typeof URL.createObjectURL!=="function"
      ||typeof Blob!=="function"||typeof atob!=="function"||typeof Uint8Array!=="function"){
      return {url:dataUrl,isBlobUrl:false};
    }
    try{
      const comma=dataUrl.indexOf(",");
      if(comma<0)return {url:dataUrl,isBlobUrl:false};
      const header=dataUrl.slice(5,comma);
      const mime=header.replace(/;base64$/i,"")||fallbackMime||"application/octet-stream";
      const binary=atob(dataUrl.slice(comma+1));
      const bytes=new Uint8Array(binary.length);
      for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);
      const blob=new Blob([bytes],{type:mime});
      return {url:URL.createObjectURL(blob),isBlobUrl:true};
    }catch{
      return {url:dataUrl,isBlobUrl:false};
    }
  }
  function disconnectAlbumObserver(){
    try{albumObserver?.disconnect?.();}catch{}
    albumObserver=null;
  }
  function bindAlbumSentinel(){
    disconnectAlbumObserver();
    if(!albumAutoLoad||!albumCursor||albumLoading||albumBatchLoading||albumError)return;
    if(typeof IntersectionObserver!=="function")return;
    const sentinel=root?.querySelector?.("[data-output-album-sentinel]");
    if(!sentinel)return;
    albumObserver=new IntersectionObserver(entries=>{
      if(entries.some(entry=>entry.isIntersecting)&&albumAutoLoad&&albumCursor&&!albumLoading&&!albumBatchLoading&&!albumError){
        void loadAlbum(true);
      }
    },{rootMargin:"300px 0px"});
    albumObserver.observe(sentinel);
  }
  function syncAlbumSelectedIndex(){
    if(!albumSelectedId){albumSelected=-1;return;}
    const visible=filteredAlbumItems();
    albumSelected=visible.findIndex(item=>item.image_id===albumSelectedId);
    if(albumSelected<0&&!albumItems.some(item=>item.image_id===albumSelectedId)){
      albumSelectedId="";
      albumZoomed=false;
    }
  }
  function albumMeta(item){
    const size=[item.width,item.height].filter(value=>value!=null&&value!=="").join(" × ");
    return [size,formatMimeLabel(item.mime_type),formatBytes(item.size_bytes),formatTime(item.created_at)].filter(Boolean).join(" · ");
  }
  function formatWeather(weather){
    if(!weather||typeof weather!=="object")return "";
    const temp=weather.temperature_c!==undefined&&weather.temperature_c!==null&&weather.temperature_c!==""?`${weather.temperature_c}°C`:"";
    return [weather.role_location,weather.condition,temp].filter(Boolean).join(" · ");
  }
  function ensureLightboxHost(){
    const host=root?.querySelector("#outputAlbumLightbox");
    if(!host)return null;
    if(root&&host.parentElement&&host.parentElement!==root&&typeof root.appendChild==="function"){
      root.appendChild(host);
    }
    return host;
  }
  function currentLightboxItem(){
    if(albumStandaloneItem&&(!albumSelectedId||albumStandaloneItem.image_id===albumSelectedId))return albumStandaloneItem;
    if(!albumSelectedId)return null;
    const visible=filteredAlbumItems();
    return visible.find(candidate=>candidate.image_id===albumSelectedId)
      ||albumItems.find(candidate=>candidate.image_id===albumSelectedId)
      ||null;
  }
  function albumCard(item,index){
    const isOutfit=item.action==="wardrobe.outfit";
    const headline=isOutfit&&item.local_date?item.local_date:formatTime(item.created_at);
    const meta=[item.width&&item.height?item.width+' × '+item.height:'',formatMimeLabel(item.mime_type),albumActionLabel(item.action)].filter(Boolean).join(' · ')||'生成图片';
    const checked=albumSelectedIds.has(item.image_id);
    const tip=item.filename?` title="${esc(item.filename)}"`:"";
    const badge=isOutfit?'<span class="output-album-card-badge">每日穿搭</span>':'';
    return '<article class="output-album-card'+(checked?' is-selected':'')+'"><label class="output-album-select"><input type="checkbox" data-output-album-select="'+esc(item.image_id)+'"'+(checked?' checked':'')+' aria-label="选择产出图片 '+(index+1)+'"></label>'+badge+'<button type="button" class="output-album-open" data-output-native-action="album-open" data-output-image-index="'+index+'"'+tip+' aria-label="查看产出图片 '+(index+1)+'"><img src="'+esc(item.thumbnail)+'" alt="'+esc(item.filename||albumActionLabel(item.action))+'" loading="lazy" decoding="async"><span class="output-album-card-meta"><strong>'+esc(headline)+'</strong><small>'+esc(meta)+'</small></span></button></article>';
  }
  function renderAlbumReference(){
    if(!albumReferenceOpen)return "";
    const item=albumItems.find(candidate=>candidate.image_id===albumReferenceImageId);
    if(!item)return "";
    const roles=REFERENCE_ROLES.map(([value,label])=>`<label class="output-reference-role"><input type="checkbox" data-output-reference-role="${value}"${albumReferenceRoles.has(value)?" checked":""}>${label}</label>`).join("");
    return `<section class="output-album-reference" role="dialog" aria-label="加入参考图">
      <header><div><strong>加入参考图</strong><small>${esc(albumMeta(item))}</small></div><button type="button" class="output-icon-button" data-output-native-action="album-reference-close" aria-label="关闭">${icon("x")}</button></header>
      <div class="output-reference-body"><img src="${esc(item.thumbnail)}" alt="选中的产出图">
        <div class="output-reference-fields"><label>名称（可选）<input type="text" maxlength="120" data-output-reference-label value="${esc(albumReferenceLabel)}" placeholder="例如：春日头像"></label>
        <fieldset><legend>允许的参考用途</legend><div class="output-reference-roles">${roles}</div></fieldset></div></div>
      ${albumReferenceError?`<p class="output-reference-error" role="alert">${esc(albumReferenceError)}</p>`:""}
      <footer><button type="button" class="button small" data-output-native-action="album-reference-close">取消</button><button type="button" class="button small primary" data-output-native-action="album-reference-save"${albumReferenceRoles.size&&!albumBusy?"":" disabled"}>${albumBusy==="reference"?"正在加入…":"确认加入"}</button></footer>
    </section>`;
  }
  function renderAlbum(){
    const host=root?.querySelector("#outputAlbumWorkspace");
    if(!host)return;
    disconnectAlbumObserver();
    syncAlbumSelectedIndex();
    const visible=filteredAlbumItems();
    let body="";
    if(albumLoading&&!albumItems.length)body='<div class="output-album-state is-loading"><strong>正在读取产出图库</strong><span>只展示当前角色已经生成并保存的图片。</span></div>';
    else if(albumError&&!albumItems.length)body=`<div class="output-album-state is-error"><strong>产出图库暂不可读</strong><span>${esc(albumError)}</span><button type="button" class="button small" data-output-native-action="album-refresh">重新读取</button></div>`;
    else if(!albumLoaded&&!albumItems.length)body='<div class="output-album-state"><strong>等待读取产出图库</strong><span>点击刷新图库读取当前角色的生成记录。</span></div>';
    else if(!albumItems.length)body='<div class="output-album-state"><strong>还没有生成图片</strong><span>完成一次图像生成后，图片会自动出现在这里。</span></div>';
    else{
      const countText=albumTotal!==null
        ?`已加载 <b>${albumItems.length}</b> / 共 <b>${albumTotal}</b> 张`
        :`已加载 <b>${albumItems.length}</b> 张`;
      const filterNote=albumFilter!=="all"?` · 当前筛选显示 ${visible.length} 张`:"";
      const statusText=albumCursor?"还有更早的产出":"已全部加载";
      const filterButtons=ALBUM_FILTERS.map(([id,label])=>`<button type="button" class="output-album-pill${albumFilter===id?" is-active":""}" data-output-native-action="album-filter" data-output-album-filter="${id}" aria-pressed="${albumFilter===id}">${label}</button>`).join("");
      const sizeButtons=ALBUM_PAGE_SIZES.map(size=>`<button type="button" class="output-album-pill${albumPageSize===size?" is-active":""}" data-output-native-action="album-page-size" data-output-album-size="${size}" aria-pressed="${albumPageSize===size}"${albumLoading||albumBatchLoading?" disabled":""}>${size}</button>`).join("");
      body=`<div class="output-album-head-bar">
        <div class="output-album-summary"><span class="output-album-count">${countText}${esc(filterNote)}</span><span class="output-album-progress">${statusText}</span></div>
        <div class="output-album-controls">
          <div class="output-album-pills" role="group" aria-label="按类型筛选">${filterButtons}</div>
          <div class="output-album-pills" role="group" aria-label="每页加载数量"><span class="output-album-pills-label">每页</span>${sizeButtons}</div>
          <label class="output-album-check output-album-autoload"><input type="checkbox" data-output-album-autoload${albumAutoLoad?" checked":""}>自动滚动加载</label>
        </div>
      </div>`;
    }
    if(albumItems.length){
      const selected=visibleSelectedIds().length;
      const all=visible.length>0&&visible.every(item=>albumSelectedIds.has(item.image_id));
      body+='<div class="output-album-toolbar"><label class="output-album-check"><input type="checkbox" data-output-album-select-all'+(all?' checked':'')+' aria-label="全选产出图片">全选</label><span class="output-album-selected">已选 <b>'+selected+'</b> 张</span><div class="output-album-actions"><button type="button" class="button small primary" data-output-native-action="album-export"'+(selected&&!albumBusy?'':' disabled')+'>'+(albumBusy==='export'?'正在导出…':'导出选中')+'</button><button type="button" class="button small" data-output-native-action="album-reference"'+(selected===1&&!albumBusy?'':' disabled')+'>加入参考图</button><button type="button" class="button small danger" data-output-native-action="album-hide"'+(selected&&!albumBusy?'':' disabled')+'>'+(albumBusy==='hide'?'正在删除…':'删除选中')+'</button><button type="button" class="button small" data-output-native-action="album-open-folder"'+(albumBusy?' disabled':'')+'>'+(albumBusy==='folder'?'正在打开…':'打开图库目录')+'</button></div></div>';
    }
    body+=renderAlbumReference();
    if(albumNotice)body+='<div class="output-album-notice'+(albumError?' is-error':'')+'" role="status">'+esc(albumNotice)+'</div>';
    if(albumItems.length){
      if(visible.length)body+='<div class="output-album-grid">'+visible.map(albumCard).join('')+'</div>';
      else body+='<div class="output-album-state"><strong>当前筛选条件下暂无已加载图片</strong><span>可切换回「全部」，或继续加载更早的产出。</span><button type="button" class="button small" data-output-native-action="album-filter" data-output-album-filter="all">显示全部</button></div>';
    }
    if(albumCursor||albumItems.length>=16){
      const batchLabel=albumBatchLoading?`正在连续加载（${albumBatchStep}/${albumBatchTotal}）…`:`连续加载多批`;
      const moreLabel=albumLoading&&!albumBatchLoading?"正在读取…":`再加载 ${albumPageSize} 张`;
      body+='<div class="output-album-more">'
        +(albumCursor?'<div class="output-album-sentinel" data-output-album-sentinel aria-hidden="true"></div>':'')
        +(albumCursor?'<button type="button" class="button small" data-output-native-action="album-more"'+(albumLoading||albumBatchLoading?' disabled':'')+'>'+moreLabel+'</button><button type="button" class="button small" data-output-native-action="album-load-batch"'+(albumLoading||albumBatchLoading?' disabled':'')+'>'+batchLabel+'</button>':'')
        +(albumItems.length>=16?'<button type="button" class="button small output-album-top" data-output-native-action="album-top">'+icon('arrow-up')+'<span>回到顶部</span></button>':'')
        +'</div>';
    }
    host.innerHTML=body;
    bindAlbumSentinel();
    renderAlbumLightbox();
    window.lucide?.createIcons?.({attrs:{'aria-hidden':'true'}});
  }
  async function loadAlbum(append=false){
    if(albumLoading||(append&&!albumCursor))return;
    const id=ticket;
    albumLoading=true;
    if(!append){albumError="";albumNotice="";albumTotal=null;}
    renderAlbum();
    try{
      const sources=append?albumSources:roleAlbumSources();
      const pending=sources.filter(source=>!source.done);
      const results=await Promise.allSettled(pending.map(async source=>{
        const params=source.cursor?{cursor:source.cursor,limit:albumPageSize}:{limit:albumPageSize};
        const data=await product().request("GET",source.endpoint,params);
        if(!Array.isArray(data?.items))throw new Error("产出图库返回格式不完整");
        if(data.next_cursor&&data.next_cursor===source.cursor)throw new Error("产出图库分页未前进，请刷新图库");
        return data;
      }));
      if(!live(id))return;
      let items=append?[...albumItems]:[],errors=[];
      results.forEach((result,index)=>{
        const source=pending[index];
        if(result.status==="rejected"){
          const error=result.reason;
          errors.push(ERRORS[error?.code]||ERRORS[error?.message]||error?.message||"产出图库暂不可读");
          return;
        }
        const data=result.value;
        items.push(...data.items.filter(item=>item&&item.image_id&&item.thumbnail)
          .map(item=>({...item,_albumEndpoint:source.endpoint})));
        source.cursor=typeof data.next_cursor==="string"&&data.next_cursor?data.next_cursor:null;
        source.done=!source.cursor;
        if(typeof data.total==="number"&&Number.isFinite(data.total)&&data.total>=0)source.total=data.total;
      });
      albumSources=sources;
      // The same account may have multiple native windows; never show duplicates.
      const known=new Set();
      albumItems=items.filter(item=>{if(known.has(item.image_id))return false;known.add(item.image_id);return true;})
        .sort((a,b)=>String(b.created_at||"").localeCompare(String(a.created_at||""))||String(a.image_id).localeCompare(String(b.image_id)));
      albumSelectedIds=new Set([...albumSelectedIds].filter(imageId=>known.has(imageId)));
      if(!append||!known.has(albumSelectedId)){albumSelectedId="";albumZoomed=false;}
      syncAlbumSelectedIndex();
      albumTotal=computeAlbumTotal(sources);
      updatePreviewFromAlbum();
      albumCursor=sources.some(source=>!source.done);
      albumLoaded=true;albumError=errors.join("；");
      albumNotice=errors.length?`部分账号图库尚未读取（${errors.length}）：${albumError}；可读取更早的产出重试。`:"";
    }catch(error){
      if(!live(id))return;
      albumError=ERRORS[error?.code]||ERRORS[error?.message]||error?.message||"产出图库暂不可读";
      if(!albumItems.length){
        updatePreviewFromAlbum();
        const status=root.querySelector(".output-image-status span");
        if(status)status.textContent=product()?.role()?.image_material_api?"最近产出暂不可读，请重新读取":"选择账号与人格后显示最近产出";
      }
    }finally{if(live(id)){albumLoading=false;renderAlbum();}}
  }
  async function loadAlbumBatch(pages=ALBUM_BATCH_PAGES){
    if(albumLoading||albumBatchLoading||!albumCursor)return;
    const id=ticket;
    albumBatchLoading=true;albumBatchStep=0;albumBatchTotal=pages;
    try{
      for(let step=1;step<=pages;step++){
        if(!live(id)||!albumCursor)break;
        albumBatchStep=step;
        await loadAlbum(true);
        if(!live(id)||albumError||!albumCursor)break;
      }
    }finally{
      if(live(id)){albumBatchLoading=false;albumBatchStep=0;albumBatchTotal=0;renderAlbum();}
    }
  }
  async function loadFullAlbumImage(item,{force=false}={}){
    if(!item?.image_id)return null;
    if(!force){
      const cached=getCachedFullImage(item.image_id);
      if(cached)return cached;
    }
    if(fullImagePending.has(item.image_id))return fullImagePending.get(item.image_id);
    const id=ticket,imageId=item.image_id,endpoint=item._albumEndpoint;
    if(!endpoint)return null;
    albumFullError.delete(imageId);
    const task=(async()=>{
      try{
        const data=await product().request("GET",endpoint+"/image",{image_id:imageId},{timeoutMs:45000});
        if(!live(id))return null;
        if(typeof data?.data_url!=="string"||!data.data_url.startsWith("data:")){
          throw new Error("原图返回格式不完整");
        }
        const viewer=dataUrlToViewerUrl(data.data_url,data.mime_type||item.mime_type);
        return putCachedFullImage(imageId,{
          image_id:imageId,
          filename:data.filename||item.filename||fallbackAlbumFilename(item),
          mime_type:data.mime_type||item.mime_type||"",
          width:data.width??item.width,
          height:data.height??item.height,
          size_bytes:data.size_bytes??item.size_bytes,
          created_at:data.created_at||item.created_at||"",
          action:data.action||item.action||"",
          local_date:data.local_date||item.local_date||"",
          weather:data.weather||item.weather||null,
          activities:Array.isArray(data.activities)?data.activities:(Array.isArray(item.activities)?item.activities:[]),
          url:viewer.url,
          isBlobUrl:viewer.isBlobUrl,
          dataUrl:data.data_url,
        });
      }catch(error){
        if(live(id)){
          albumFullError.set(imageId,ERRORS[error?.code]||ERRORS[error?.message]||"原图暂不可用，当前显示预览图");
        }
        return null;
      }finally{
        fullImagePending.delete(imageId);
        if(live(id)&&currentLightboxItem()?.image_id===imageId)renderAlbumLightbox();
      }
    })();
    fullImagePending.set(imageId,task);
    return task;
  }
  async function copyAlbumFilename(filename){
    if(!filename)return;
    let copied=false;
    try{
      if(typeof navigator!=="undefined"&&navigator.clipboard?.writeText){
        await navigator.clipboard.writeText(filename);
        copied=true;
      }
    }catch{}
    if(!copied&&typeof document.createElement==="function"){
      try{
        const area=document.createElement("textarea");
        area.value=filename;
        area.setAttribute?.("readonly","");
        if(area.style)area.style.cssText="position:fixed;opacity:0;pointer-events:none;";
        document.body.appendChild(area);
        area.select?.();
        copied=typeof document.execCommand==="function"&&document.execCommand("copy");
        area.remove?.();
      }catch{}
    }
    clearTimeout(albumCopyTimer);
    albumCopyFeedback=copied?"已复制":"请手动复制";
    renderAlbumLightbox();
    albumCopyTimer=setTimeout(()=>{albumCopyFeedback="";if(currentLightboxItem())renderAlbumLightbox();},2000);
  }
  async function downloadFullAlbumImage(item){
    if(!item)return;
    const entry=getCachedFullImage(item.image_id)||await loadFullAlbumImage(item,{force:true});
    if(entry?.url){
      downloadDataUrl(entry.url,entry.filename||item.filename||fallbackAlbumFilename(item));
      return;
    }
    if(!item._albumEndpoint&&item.thumbnail){
      downloadDataUrl(item.thumbnail,item.filename||fallbackAlbumFilename(item));
    }
  }
  let albumLightboxCloseTimer=null;
  function canAnimateLightbox(node){
    return Boolean(
      node
      &&typeof requestAnimationFrame==="function"
      &&typeof window.getComputedStyle==="function"
      &&!document.body?.classList?.contains("reduce-motion")
      &&document.documentElement?.dataset?.motion!=="off"
      &&!window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches
    );
  }
  function closeAlbumLightbox(){
    if(albumLightboxCloseTimer)return;
    albumStandaloneItem=null;albumSelectedId="";albumSelected=-1;albumZoomed=false;albumCopyFeedback="";
    clearTimeout(albumCopyTimer);
    const host=ensureLightboxHost();
    if(!host)return;
    if(!canAnimateLightbox(host)||host.hidden){
      host.classList?.remove?.("is-open","is-closing");
      host.hidden=true;host.innerHTML="";
      return;
    }
    if(host.contains?.(document.activeElement))document.activeElement?.blur?.();
    host.setAttribute?.("inert","");
    host.classList.remove("is-open");
    host.classList.add("is-closing");
    const myTicket=ticket;
    const delay=document.documentElement?.dataset?.motion==="low"?110:185;
    albumLightboxCloseTimer=setTimeout(()=>{
      albumLightboxCloseTimer=null;
      if(live(myTicket)&&!currentLightboxItem()){
        host.removeAttribute?.("inert");
        host.classList?.remove?.("is-open","is-closing");
        host.hidden=true;host.innerHTML="";
      }
    },delay);
  }
  function renderAlbumLightbox(){
    const host=ensureLightboxHost();
    if(!host)return;
    const item=currentLightboxItem();
    if(!item){
      clearTimeout(albumLightboxCloseTimer);albumLightboxCloseTimer=null;
      host.classList?.remove?.("is-open","is-closing");
      host.hidden=true;host.innerHTML="";
      return;
    }
    clearTimeout(albumLightboxCloseTimer);albumLightboxCloseTimer=null;
    const wasClosed=host.hidden||!host.classList?.contains?.("is-open");
    const standalone=!!(albumStandaloneItem&&item===albumStandaloneItem);
    const visible=filteredAlbumItems();
    const list=standalone?[item]:(visible.some(candidate=>candidate.image_id===item.image_id)?visible:albumItems);
    const position=standalone?0:list.findIndex(candidate=>candidate.image_id===item.image_id);
    albumSelected=position;
    const fullEntry=getCachedFullImage(item.image_id);
    const isLoadingFull=fullImagePending.has(item.image_id);
    const fullError=albumFullError.get(item.image_id)||"";
    const meta=fullEntry||item;
    const isOutfit=meta.action==="wardrobe.outfit";
    const filename=meta.filename||item.filename||fallbackAlbumFilename(item);
    const imageSrc=fullEntry?.url||item.thumbnail;
    const weatherText=formatWeather(meta.weather||item.weather);
    const localDate=meta.local_date||item.local_date||"";
    const activities=Array.isArray(meta.activities)&&meta.activities.length
      ?meta.activities:(Array.isArray(item.activities)?item.activities:[]);
    const specs=[
      meta.width&&meta.height?`${meta.width} × ${meta.height}`:"",
      formatMimeLabel(meta.mime_type),
      formatBytes(meta.size_bytes),
      localDate||"",
      meta.created_at?formatTime(meta.created_at):"",
      weatherText,
    ].filter(Boolean).join(" · ");
    const badge=isLoadingFull
      ?'<span class="output-album-full-badge is-loading" role="status"><span class="output-album-mini-spinner" aria-hidden="true"></span>加载原图中…</span>'
      :fullEntry?'<span class="output-album-full-badge is-ready">高清原图</span>'
      :fullError?`<button type="button" class="output-album-full-badge is-error" data-output-native-action="album-retry-full" title="${esc(fullError)}">预览图 · 重试加载原图</button>`
      :"";
    const downloadControl=fullEntry?.url
      ?`<a class="button small primary output-album-download" href="${esc(fullEntry.url)}" download="${esc(filename)}" rel="noopener" data-output-album-download>${icon("download")}<span>下载原图</span></a>`
      :!item._albumEndpoint&&item.thumbnail
      ?`<a class="button small primary output-album-download" href="${esc(item.thumbnail)}" download="${esc(filename)}" rel="noopener" data-output-album-download>${icon("download")}<span>下载原图</span></a>`
      :`<button type="button" class="button small primary output-album-download" data-output-native-action="album-download"${isLoadingFull?" disabled":""}>${icon("download")}<span>${isLoadingFull?"加载原图中…":"下载原图"}</span></button>`;
    const showNav=!standalone&&list.length>1;
    const posChip=showNav?`<span class="output-album-lightbox-pos">${position+1} / ${list.length}</span>`
      :(localDate?`<span class="output-album-lightbox-pos">${esc(localDate)}</span>`:`<span class="output-album-lightbox-pos">1 / 1</span>`);
    host.hidden=false;
    host.removeAttribute?.("inert");
    host.classList?.remove?.("is-closing");
    host.innerHTML=`<button type="button" class="output-album-lightbox-mask" data-output-native-action="album-close" aria-label="关闭"></button>
      <section class="output-album-lightbox-card" role="dialog" aria-modal="true" aria-label="${isOutfit?"每日穿搭原图预览":"产出原图预览"}">
        <header>
          <div class="output-album-lightbox-head-copy">
            <strong>${isOutfit?"每日穿搭图":"产出图片"}</strong>
            ${posChip}
            <span class="output-album-lightbox-tag">${esc(albumActionLabel(meta.action))}</span>
          </div>
          <div class="output-album-lightbox-head-actions">
            <button type="button" class="button small" data-output-native-action="album-zoom" aria-pressed="${albumZoomed}">${icon(albumZoomed?"minimize-2":"maximize-2")}<span>${albumZoomed?"适应画面":"1:1 细节"}</span></button>
            <button type="button" class="output-icon-button" data-output-native-action="album-close" aria-label="关闭">${icon("x")}</button>
          </div>
        </header>
        <div class="output-album-lightbox-stage${albumZoomed?" is-zoomed":""}${showNav?"":" is-single"}">
          ${showNav?`<button type="button" class="output-album-nav prev" data-output-native-action="album-prev" aria-label="上一张"${position<=0?" disabled":""}>${icon("chevron-left")}</button>`:""}
          <div class="output-album-lightbox-viewport">
            <img class="output-album-lightbox-img" src="${esc(imageSrc)}" alt="${esc(filename)}" data-output-native-action="album-zoom" title="${albumZoomed?"点击适应窗口":"点击查看 1:1 细节"}">
            ${badge}
          </div>
          ${showNav?`<button type="button" class="output-album-nav next" data-output-native-action="album-next" aria-label="下一张"${position>=list.length-1?" disabled":""}>${icon("chevron-right")}</button>`:""}
        </div>
        <footer class="output-album-lightbox-foot">
          <div class="output-album-lightbox-meta">
            <div class="output-album-filename-row">
              <code class="output-album-filename" title="${esc(filename)}">${esc(filename)}</code>
              <button type="button" class="output-album-copy" data-output-native-action="album-copy-name" data-output-filename="${esc(filename)}" aria-label="复制文件名">${icon(albumCopyFeedback==="已复制"?"check":"copy")}<span>${esc(albumCopyFeedback||"复制文件名")}</span></button>
            </div>
            <small class="output-album-specs">${esc(specs||albumActionLabel(meta.action))}</small>
            ${activities.length?`<small class="output-album-outfit-context">当日安排：${esc(activities.join("、"))}</small>`:""}
          </div>
          <div class="output-album-lightbox-foot-actions">${downloadControl}</div>
        </footer>
      </section>`;
    if(wasClosed&&canAnimateLightbox(host)){
      host.classList?.remove?.("is-open");
      requestAnimationFrame(()=>{if(!host.hidden&&currentLightboxItem())host.classList?.add?.("is-open");});
    }else{
      host.classList?.add?.("is-open");
    }
    window.lucide?.createIcons?.({attrs:{"aria-hidden":"true"}});
  }
  function openAlbum(index){
    const visible=filteredAlbumItems();
    if(!Number.isInteger(index)||index<0||index>=visible.length)return;
    const item=visible[index];
    albumStandaloneItem=null;albumSelectedId=item.image_id;albumSelected=index;albumZoomed=false;albumCopyFeedback="";
    clearTimeout(albumCopyTimer);
    if(!getCachedFullImage(item.image_id))void loadFullAlbumImage(item);
    renderAlbumLightbox();
  }
  function openLightbox(item){
    if(!root?.isConnected||!item?.thumbnail)return false;
    const imageId=typeof item.image_id==="string"&&item.image_id?item.image_id:`preview:${item.local_date||Date.now()}`;
    const normalized={...item,image_id:imageId};
    albumStandaloneItem=normalized;
    albumSelectedId=imageId;albumSelected=0;albumZoomed=false;albumCopyFeedback="";
    clearTimeout(albumCopyTimer);
    if(normalized._albumEndpoint&&!getCachedFullImage(imageId))void loadFullAlbumImage(normalized);
    renderAlbumLightbox();
    return true;
  }
  function stepAlbum(delta){
    if(albumStandaloneItem)return;
    const visible=filteredAlbumItems();
    if(!visible.length)return;
    const current=visible.findIndex(item=>item.image_id===albumSelectedId);
    const next=Math.max(0,Math.min(visible.length-1,(current<0?0:current)+delta));
    openAlbum(next);
  }
  function downloadDataUrl(dataUrl,filename){
    const link=document.createElement("a");
    link.href=dataUrl;link.download=filename||"companion-album.zip";link.rel="noopener";
    document.body.appendChild(link);link.click();link.remove();
  }
  function toggleAlbumSelection(imageId,checked){
    if(checked)albumSelectedIds.add(imageId);else albumSelectedIds.delete(imageId);
    renderAlbum();
  }
  function selectAllAlbums(checked){
    const visible=filteredAlbumItems();
    if(checked){for(const item of visible)albumSelectedIds.add(item.image_id);}
    else{for(const item of visible)albumSelectedIds.delete(item.image_id);}
    renderAlbum();
  }
  async function exportAlbum(){
    const ids=visibleSelectedIds();
    if(albumBusy||!ids.length)return;
    const id=ticket;
    albumBusy="export";albumNotice="正在打包选中的图片…";albumError="";renderAlbum();
    try{
      const groups=selectedAlbumGroups();let count=0,archives=0,errors=[];
      for(const [endpoint,imageIds] of groups){
        try{
          const data=await product().request("POST",endpoint+"/export",{image_ids:imageIds});
          if(!live(id))return;
          if(typeof data?.data_url!=="string"||typeof data.filename!=="string")throw new Error("导出文件格式不完整");
          downloadDataUrl(data.data_url,groups.size>1?`${++archives}-${data.filename}`:data.filename);
          count+=Number(data.count)||imageIds.length;
        }catch(error){errors.push(ERRORS[error?.code]||error?.message||"导出失败");}
        if(!live(id))return;
      }
      albumError=errors.join("；");
      albumNotice=`已生成 ${count} 张图片的导出文件${groups.size>1?"（按来源账号分别下载压缩包）":""}${albumError?`；部分导出失败：${albumError}`:""}`;
    }catch(error){if(live(id)){albumError=ERRORS[error?.code]||ERRORS[error?.message]||error?.message||"导出失败";albumNotice=albumError;}}
    finally{if(live(id)){albumBusy="";renderAlbum();}}
  }
  async function hideAlbum(){
    const ids=visibleSelectedIds();
    if(albumBusy||!ids.length)return;
    const id=ticket;
    const confirmed=await window.CompanionConfirm?.({title:"从图库删除",
      message:`确定从图库删除选中的 ${ids.length} 张图片？图片会从当前角色图库中移除，不会删除原始来源记录。`,
      confirmText:"删除",cancelText:"取消",danger:true});
    if(!live(id)||albumBusy)return;
    if(confirmed==null){albumError="当前页面无法弹出确认框，未执行删除。";albumNotice=albumError;renderAlbum();return;}
    const currentIds=visibleSelectedIds();
    if(confirmed!==true||ids.length!==currentIds.length||ids.some((imageId,index)=>imageId!==currentIds[index]))return;
    albumBusy="hide";albumNotice="正在从图库删除…";albumError="";renderAlbum();
    try{
      const groups=selectedAlbumGroups();let count=0,errors=[];
      for(const [endpoint,imageIds] of groups){
        try{
          const data=await product().request("POST",endpoint+"/hide",{image_ids:imageIds});
          if(!live(id))return;
          const removed=new Set(imageIds);albumItems=albumItems.filter(item=>!removed.has(item.image_id));
          for(const imageId of imageIds){albumSelectedIds.delete(imageId);evictFullImage(imageId);}
          const removedCount=Number(data?.hidden)||imageIds.length;
          const matchedSource=albumSources.find(source=>source.endpoint===endpoint);
          if(matchedSource&&typeof matchedSource.total==="number")matchedSource.total=Math.max(0,matchedSource.total-removedCount);
          count+=removedCount;
        }catch(error){errors.push(ERRORS[error?.code]||error?.message||"删除失败");}
        if(!live(id))return;
      }
      if(!albumItems.some(item=>item.image_id===albumSelectedId)){albumSelectedId="";albumZoomed=false;}
      syncAlbumSelectedIndex();
      albumTotal=computeAlbumTotal(albumSources);
      albumError=errors.join("；");
      albumNotice=`已从图库删除 ${count} 张图片${albumError?`；部分删除未确认：${albumError}`:""}`;
      updatePreviewFromAlbum();
    }catch(error){if(live(id)){albumError=ERRORS[error?.code]||ERRORS[error?.message]||error?.message||"删除失败";albumNotice=albumError;}}
    finally{if(live(id)){albumBusy="";renderAlbum();}}
  }
  async function openAlbumFolder(){
    if(albumBusy)return;
    const id=ticket;albumBusy="folder";albumNotice="正在同步并打开图库目录…";albumError="";renderAlbum();
    try{
      const data=await product().request("POST",albumEndpoint()+"/open-folder",{});
      if(!live(id))return;
      const total=typeof data?.total_count==="number"&&Number.isFinite(data.total_count)&&data.total_count>=0?data.total_count:NaN;
      const synced=typeof data?.synced_count==="number"&&Number.isFinite(data.synced_count)&&data.synced_count>0?data.synced_count:0;
      const summary=Number.isFinite(total)?(synced?`（已同步 ${synced} 张，共 ${total} 张）`:`（共 ${total} 张）`):"";
      albumNotice=`已打开产出图库目录${summary}：${data?.path||"产出图库目录"}`;
    }catch(error){if(live(id)){albumError=ERRORS[error?.code]||ERRORS[error?.message]||error?.message||"无法打开产出图库目录";albumNotice=albumError;}}
    finally{if(live(id)){albumBusy="";renderAlbum();}}
  }
  function openAlbumReference(){
    const ids=visibleSelectedIds();
    if(albumBusy||ids.length!==1)return;
    albumReferenceImageId=ids[0];
    albumReferenceLabel="";albumReferenceRoles=new Set(["identity"]);albumReferenceError="";albumReferenceOpen=true;renderAlbum();
  }
  function closeAlbumReference(){
    if(albumBusy==="reference")return;
    albumReferenceOpen=false;albumReferenceImageId="";albumReferenceError="";renderAlbum();
  }
  async function saveAlbumReference(){
    if(albumBusy||!albumReferenceOpen||!albumReferenceImageId||!albumReferenceRoles.size)return;
    const id=ticket,imageId=albumReferenceImageId,label=albumReferenceLabel.trim();
    albumBusy="reference";albumReferenceError="";renderAlbum();
    try{
      await product().request("POST",imageAlbumEndpoint(imageId).replace(/\/album$/,"")+"/references",{image_id:imageId,label,
        roles:[...albumReferenceRoles],operation_key:crypto.randomUUID()});
      if(!live(id))return;
      albumReferenceOpen=false;albumReferenceImageId="";
      albumNotice=`已加入参考图库${label?`：${label}`:""}`;
    }catch(error){
      if(live(id))albumReferenceError=ERRORS[error?.code]||ERRORS[error?.message]||error?.message||"加入参考图失败";
    }finally{if(live(id)){albumBusy="";renderAlbum();}}
  }
  function renderScenePresets(){
    const host=root?.querySelector("#outputScenePresetList");
    const editor=root?.querySelector("#outputScenePresetEditor");
    if(!host||!editor)return;
    const field=root.querySelector('[data-wb-field="scene_presets_json"]');
    const configuration=root.querySelector("#imageOutputWorkspace")?.companionConfigEditor?.state?.instances?.[0]?.state;
    root.querySelectorAll('[data-output-native-action="refresh-preferences"]').forEach(button=>{button.disabled=sceneSaving||!!configuration?.pending;});
    const manager=root.querySelector('[data-output-native-action="manage-scenes"]');
    if(manager){
      manager.disabled=!field||sceneSaving;
      manager.title=field?"管理场景预设":"角色配置尚未就绪";
    }
    field?.closest(".wb-field")?.classList.add("is-scene-json-managed");
    let presets=[];
    try{presets=field?JSON.parse(field.value):null;}catch{presets=null;}
    const html=!field?`<span class="output-observation-muted">场景配置暂不可用</span><button type="button" class="output-inline-link" data-go="manage" data-manage="host">账号与会话 ${icon("arrow-up-right")}</button>`
      :!Array.isArray(presets)?'<span class="output-observation-muted">场景预设需要修正</span>'
      :presets.length?presets.map(item=>`<span class="output-scene-preset">${esc(item.name||"未命名")}</span>`).join("")
      :'<span class="output-observation-muted">没有配置场景预设</span>';
    updateMarkup(host,html);
    editor.hidden=!sceneEditorOpen;
    if(!sceneEditorOpen)return;
    if(!field){
      editor.innerHTML='<p class="output-observation-muted">角色配置尚未就绪</p>';
      return;
    }
    if(!Array.isArray(sceneDraft))sceneDraft=Array.isArray(presets)?clone(presets):[];
    const editorHTML=`<div class="output-scene-editor-list">${sceneDraft.map((item,index)=>`<article class="output-scene-editor-row"><label><span>场景名称</span><input type="text" maxlength="24" data-output-scene-index="${index}" data-output-scene-field="name" value="${esc(item.name||"")}"${sceneSaving?" disabled":""}></label><label><span>画面提示词</span><textarea rows="2" maxlength="800" data-output-scene-index="${index}" data-output-scene-field="prompt"${sceneSaving?" disabled":""}>${esc(item.prompt||"")}</textarea></label><button type="button" class="output-icon-button" data-output-native-action="remove-scene" data-output-scene-index="${index}" aria-label="移除${esc(item.name||"场景")}" title="移除场景"${sceneSaving?" disabled":""}>${icon("x")}</button></article>`).join("")}</div><div class="output-scene-editor-actions"><span role="status">${esc(sceneError||`${sceneDraft.length} / 12 个场景`)}</span><button type="button" class="button small" data-output-native-action="refresh-preferences"${sceneSaving||configuration?.pending?" disabled":""}>${icon("refresh-cw")}重新读取</button><button type="button" class="button small" data-output-native-action="add-scene"${sceneSaving||sceneDraft.length>=12?" disabled":""}>${icon("plus")}添加场景</button><button type="button" class="button small primary" data-output-native-action="save-scenes"${sceneSaving?" disabled":""}>${sceneSaving?"正在保存…":"保存场景预设"}</button></div>`;
    updateMarkup(editor,editorHTML);
    window.lucide?.createIcons?.({attrs:{"aria-hidden":"true"}});
  }
  async function saveScenePresets(){
    if(sceneSaving)return;
    const field=root.querySelector('[data-wb-field="scene_presets_json"]');
    if(!field||!Array.isArray(sceneDraft)){sceneError="场景配置尚未读取";renderScenePresets();return;}
    const names=sceneDraft.map(item=>String(item.name||"").trim());
    if(sceneDraft.length>12||names.some(name=>!name)||sceneDraft.some(item=>!String(item.prompt||"").trim())
      ||new Set(names).size!==names.length){sceneError="请填写不重复的场景名称和画面提示词";renderScenePresets();return;}
    sceneDraft=sceneDraft.map((item,index)=>({...item,name:names[index],prompt:String(item.prompt||"").trim()}));
    const id=ticket;
    sceneSaving=true;
    sceneError="正在保存场景预设…";
    renderScenePresets();
    const editor=root.querySelector("#imageOutputWorkspace")?.companionConfigEditor;
    let result;
    try{
      result=await editor?.saveField?.("image.output","scene_presets_json",JSON.stringify(sceneDraft))||{state:"unavailable"};
    }catch(error){result={state:"unknown",message:error?.message||""};}
    if(!live(id))return;
    sceneSaving=false;
    sceneError=result.state==="saved"?"场景预设已保存"
      :result.state==="conflict"?(result.message||"配置已更新，请重新读取后核对。")
        :result.state==="rejected"?(result.message||"场景预设保存失败")
          :result.state==="unknown"?"保存结果尚未确认，请核对原保存结果"
            :result.state==="dirty"?"请先保存配置工作区中的其他改动，再保存场景预设"
            :result.state==="unchanged"?(result.message||"场景预设没有变化")
              :"场景配置当前不可用，请先读取角色配置";
    renderScenePresets();
  }
  async function refreshPreferences(){
    if(sceneSaving)return;
    const editor=root?.querySelector("#imageOutputWorkspace")?.companionConfigEditor;
    if(!editor)return;
    const configuration=editor.state.instances[0]?.state;
    if(configuration?.busy||configuration?.loading)return;
    if(configuration?.pending){sceneError="请先核对原保存结果";renderScenePresets();return;}
    const id=ticket;
    sceneSaving=true;sceneError="正在读取最新配置…";renderScenePresets();
    try{
      await editor.read("image.output");
      if(live(id))sceneError=configuration?.status?"已读取最新配置":"配置暂不可读，请重试";
    }catch{if(live(id))sceneError="配置暂不可读，请重试";}
    finally{if(live(id)){sceneSaving=false;renderScenePresets();}}
  }
  function renderObservation(){
    if(!root?.isConnected)return;
    root.querySelectorAll?.('[data-output-native-action="observability"]').forEach(button=>{
      button.disabled=observationBusy;
      button.classList?.toggle?.("is-refreshing",observationBusy);
      button.setAttribute?.("aria-busy",observationBusy?"true":"false");
    });
    const host=root.querySelector("#outputObservationWorkspace");
    if(!host)return;
    if(observationBusy&&!observation){host.innerHTML='<span class="output-observation-muted">正在读取最近调用…</span>';return;}
    if(!observation){
      host.innerHTML=`<div class="output-observation-empty" role="status">${esc(observationError||"还没有读取到生图记录")}<button type="button" class="output-inline-link" data-output-native-action="observability">重新读取 ${icon("refresh-cw")}</button></div>`;
      return;
    }
    const status={queued:"排队中",running:"生成中",succeeded:"已完成",failed:"失败",unknown:"结果未确认"};
    const action={"image.generate":"文生图","image.edit":"改图","image.mask_edit":"蒙版改图"};
    const items=observation.items||[];
    const rows=items.map(item=>`<li class="output-observation-row"><span class="output-observation-state is-${esc(item.state)}"></span><span class="output-observation-main"><strong>${esc(action[item.action]||item.action)}</strong><small>${esc(formatTime(item.created_at))}</small></span><span class="output-observation-result">${esc(status[item.state]||item.state)}${item.elapsed_ms==null?"":` · ${(item.elapsed_ms/1000).toFixed(1)} 秒`}${item.error_code?` · ${esc(ERRORS[item.error_code]||item.error_code)}`:""}</span></li>`).join("");
    host.innerHTML=`<div class="output-observation-summary">${Number(observation.total)||0} 次调用 <span>最近 ${items.length} 条</span>${observationRefreshedAt?`<span class="refresh-stamp" role="status">刚刚已刷新 ${esc(observationRefreshedAt)}</span>`:""}</div>${rows?`<ul class="output-observation-list">${rows}</ul>`:'<div class="output-observation-empty">当前角色还没有生图调用记录</div>'}`;
  }
  const worksTarget=()=>{
    const target=product()?.roleTarget?.(),role=product()?.role?.();
    if(!target?.target_id||!role?.role_id||target.scope_kind!=="private")throw new Error("请先选择已绑定的私聊角色");
    return {target_id:target.target_id,role_id:role.role_id};
  };
  function renderWorks(){
    const host=root?.querySelector("[data-output-works-list]");
    if(!host)return;
    const statusBanner=authoringStatus?.state==="failed"
      ?`<div class="output-observation-empty" role="status">最近一次定时创作未完成${authoringStatus.window_title?`（${esc(authoringStatus.window_title)}）`:""}：${esc(AUTHORING_ERROR_LABELS[authoringStatus.code]||authoringStatus.code||"未知原因")}${authoringStatus.at?` · ${esc(formatTime(authoringStatus.at))}`:""}${authoringStatus.retryable?" · 稍后将自动重试":""}</div>`
      :"";
    const sections=[["writing","正在创作"],["finished","已完成作品"]];
    host.innerHTML=statusBanner+sections.map(([state,label])=>{
      const rows=works.map((item,index)=>({item,index})).filter(({item})=>item.state===state);
      return `<section><h3>${label}</h3><ul class="output-work-list">${rows.map(({item,index})=>`
        <li><button type="button" data-output-native-action="work-preview" data-work-index="${index}">
          <strong>${esc(item.title||"未命名作品")}</strong>
          <small>${esc([item.work_type,formatTime(item.candidate_at||item.created_at),item.candidate_finished?"已写完，待采用":item.candidate_version?"有续写候选稿":""].filter(Boolean).join(" · "))}</small>
        </button></li>`).join("")||'<li class="output-work-empty">暂无作品</li>'}</ul></section>`;
    }).join("");
  }
  async function loadWorks(){
    const host=root?.querySelector("[data-output-works-list]");
    if(!host||worksBusy)return;
    const id=ticket;
    worksBusy=true;host.textContent="正在读取作品…";
    try{
      const data=await product().request("GET","works/library",worksTarget());
      if(!live(id))return;
      works=Array.isArray(data.works)?data.works:[];
      authoringStatus=data.authoring_status&&typeof data.authoring_status==="object"?data.authoring_status:null;
      worksLoaded=true;
      renderWorks();
      const dates=works.map(item=>item.candidate_at||item.created_at).sort();
      updateContentSummary({available:true,works_count:works.length,
        candidate_count:works.filter(item=>item.candidate_version).length,updated_at:dates.at(-1)});
    }catch{
      if(live(id)){worksLoaded=false;host.textContent="暂时无法读取作品。请核对当前私聊角色，然后刷新。";}
    }finally{if(live(id))worksBusy=false;}
  }
  async function previewWork(item, candidate){
    const dialog=root?.querySelector("[data-output-work-dialog]");
    if(!dialog||!item)return;
    const id=ticket,serial=++workTicket;
    workSelection={item,candidate};
    const body=dialog.querySelector("[data-output-work-text]");
    const actions=dialog.querySelector("[data-output-work-actions]");
    dialog.querySelector("[data-output-work-title]").textContent=item.title||"未命名作品";
    actions.innerHTML=item.candidate_version?`
      <button class="button small" type="button" data-output-native-action="work-head" aria-pressed="${!candidate}">当前正文</button>
      <button class="button small" type="button" data-output-native-action="work-candidate" aria-pressed="${candidate}">续写候选稿</button>
      ${candidate&&(item.candidate_append_only?'<span>旧版续写片段，请等待完整候选稿。</span>':'<button class="button small primary" type="button" data-output-native-action="work-adopt" disabled>采用这份稿</button>')||""}`:"";
    body.textContent="正在读取正文…";
    if(!dialog.open)dialog.showModal();
    try{
      const params={...worksTarget(),work_id:item.work_id,version_id:candidate?item.candidate_version:item.head_version};
      const data=await product().request("GET","works/preview",params);
      if(!live(id)||serial!==workTicket||!dialog.open)return;
      body.replaceChildren();
      const chapters=Array.isArray(data.chapters)?data.chapters:[];
      for(const chapter of chapters){
        const section=document.createElement("section");
        if(chapter.title&&chapter.title!==item.title){
          const heading=document.createElement("h3");heading.textContent=chapter.title;section.append(heading);
        }
        const text=document.createElement("p");text.textContent=chapter.text||"";section.append(text);body.append(section);
      }
      if(!chapters.length)body.textContent="这个版本没有正文。";
      const adopt=actions.querySelector('[data-output-native-action="work-adopt"]');
      if(adopt&&chapters.length)adopt.disabled=false;
    }catch{
      if(live(id)&&serial===workTicket)body.textContent="这个版本暂时无法读取。";
    }
  }
  async function adoptWork(button){
    if(!workSelection?.candidate)return;
    const {item}=workSelection,id=ticket,serial=workTicket;
    const dialog=button.closest("dialog");
    if(!live(id)||serial!==workTicket||!dialog.open)return;
    button.disabled=true;
    try{
      await product().request("POST","works/adopt",{...worksTarget(),
        work_id:item.work_id,version_id:item.candidate_version,expected_revision:item.revision});
      if(live(id)){
        dialog.close();
        await loadWorks();
        void refreshObservability();
      }
    }catch(error){
      if(live(id)&&serial===workTicket){
        const message=document.createElement("span");
        message.setAttribute("role","alert");
        message.textContent=["revision_conflict","stale_candidate"].includes(error.code)
          ?"作品已经更新，请关闭窗口并刷新后再采用。":"暂未确认采用。请刷新作品核对当前版本。";
        button.after(message);
      }
    }
  }
  function updateContentSummary(content){
    const available=content?.available===true;
    const works=available?Number(content.works_count)||0:null;
    const candidates=available?Number(content.candidate_count)||0:null;
    const updated=available?formatTime(content.updated_at):"—";
    root?.querySelectorAll('[data-output-content-stat="works"]').forEach(node=>{node.textContent=works===null?"—":String(works);});
    root?.querySelectorAll('[data-output-content-stat="candidates"]').forEach(node=>{node.textContent=candidates===null?"—":String(candidates);});
    root?.querySelectorAll('[data-output-content-stat="updated"]').forEach(node=>{node.textContent=updated;});
    const body=available?`${works} 部已关联作品 · ${candidates} 份续写候选稿`:"Content 作品数据暂不可用";
    const meta=available?`最近更新 ${updated}`:"—";
    for(const selector of ["[data-output-content-preview]","[data-output-content-teaser]"])root?.querySelectorAll(selector).forEach(node=>{node.textContent=body;});
    for(const selector of ["[data-output-content-preview-count]","[data-output-content-teaser-meta]"])root?.querySelectorAll(selector).forEach(node=>{node.textContent=available?`${works} 部已关联`:"—";});
    root?.querySelectorAll("[data-output-content-preview-updated]").forEach(node=>{node.textContent=meta;});
  }
  function selector(){
    const host=root?.querySelector("[data-output-native-selector]");
    if(!host)return;
    host.innerHTML=draft?`<div class="output-native-route-summary"><strong>文生图与改图独立路由</strong><span>每个功能的模型与优先级在「模型与连接 → 生图模型」分别设置。</span></div><label class="output-backend-enable"><input type="checkbox" data-output-native-field="enabled"${draft.enabled?" checked":""}${busy?" disabled":""}>启用 V2 生图后端</label>${!hostRegistered&&draft.enabled?`<div class="output-config-unavailable" role="status">当前 V2 工具目录尚未接入此图片提供方。<button type="button" class="button small" data-output-native-action="reconnect"${busy?" disabled":""}>重连 V2 执行器</button></div>`:""}`:`<div class="output-config-unavailable" role="status"><span>${esc(notice||"正在读取生图模型配置…")}</span>${!product()?.role()?.model_api?`<button type="button" class="output-inline-link" data-go="manage" data-manage="host">账号与会话 ${icon("arrow-up-right")}</button>`:""}</div>`;
  }
  function naiBackend(){
    const status=naiStatus||{},labels={generate:"文生图",edit:"参考图改图"};
    const service=status.call_mode==="openai"?(status.api_profile==="starbridge"?"星桥（Almoon）":"OpenAI 兼容接口"):"nai.sta1n.cn 直连";
    const reason=!naiStatus?(busy?"正在检测 NAI 插件…":"NAI 状态暂不可读，请重新检测"):!status.installed?"未安装 NAI 生图插件":!status.available?({nai_plugin_disabled:"NAI 插件已停用，请先在插件管理中启用",nai_plugin_incompatible:"NAI 插件版本不支持陪伴联动，请先更新"})[status.reason]||"NAI 尚未就绪，请检查插件的陪伴联动开关与接口配置":"已就绪";
    const format=status.prompt_format==="nai"?"NAI 英文标签":status.prompt_format==="natural"?"英文自然语言":"跟随 NAI 插件";
    const fields=[["接入服务",service],["生图模型",status.default_model||"跟随 NAI 插件"],["画风",status.artist_slot_name||"跟随 NAI 插件当前画风"],["默认尺寸",status.default_size||"跟随 NAI 插件"],["提示词格式",format],["参考图模式",status.call_mode==="direct"?"当前接入方式不支持":({vibe:"氛围参考（不固定人物身份）",img2img:"图生图",director:"精准参考"})[status.reference_mode]||"跟随 NAI 插件"]];
    return `<section class="output-capability-card output-nai-backend" data-output-nai><header class="output-capability-heading"><h2>NAI 后端</h2><span role="status">${esc(reason)}</span><button type="button" class="output-icon-button${busy==="read"?" is-refreshing":""}" data-output-native-action="refresh" title="重新检测 NAI 插件" aria-label="重新检测 NAI 插件" aria-busy="${busy==="read"?"true":"false"}"${busy?" disabled":""}>${icon("refresh-cw")}</button></header><div class="output-endpoint-guidance"><p>模型、画师串（画风槽位）、尺寸、采样参数和默认负面提示词均沿用「NAI 生图插件」的配置；地址与密钥也由 NAI 插件管理。</p><p>人物和服装按陪伴当前角色与本次要求绘制，不套用 NAI 的固定角色模板；不叠加下方的画面风格。提示词格式跟随 NAI 的「陪伴直连提示词模式」。</p></div>${status.installed?`<dl class="output-nai-defaults">${fields.map(([label,value])=>`<div class="output-native-field"><dt>${label}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>`:""}<div class="output-nai-operations">${Object.entries(labels).map(([operation,label])=>{
      const selected=draft?.operation_routes?.[operation]?.includes("nai"),supported=status.operations?.includes(operation);
      return `<label class="output-native-check"><input type="checkbox" data-output-nai-operation="${operation}"${selected?" checked":""}${!draft||busy||(!supported&&!selected)?" disabled":""}>${label}${operation==="edit"&&status.available&&!supported?"（当前接入方式不支持）":""}</label>`;
    }).join("")}</div>${draft?actions():""}</section>`;
  }
  function queue(){
    if(!draft)return `<div class="output-config-unavailable" role="status"><span>${esc(notice||"正在读取生图后端…")}</span><button type="button" class="button small" data-output-native-action="refresh">重新读取</button></div>`;
    return `<section class="output-capability-queue"><header class="output-capability-heading"><h2>在线图片模型</h2><span>${draft.endpoints.length} 个模型</span><div><button type="button" class="button small" data-output-native-action="add"${busy||draft.endpoints.length>=24?" disabled":""}>${icon("plus")}添加模型</button></div></header>
      <div class="output-endpoint-guidance"><p>此处管理模型连接；实际优先级分别由模型页的文生图与改图队列控制。</p><p>「生成测试图」会真实提交一张图片生成请求，可能产生费用；图片不会发送到聊天。</p></div>
      <div class="output-endpoint-list">${draft.endpoints.map((item,index)=>{
        const open=expanded.has(item.id),path=`endpoints.${index}`;
        const test=tests.get(item.id);
        return `<article class="output-endpoint-card"><header><button type="button" class="output-endpoint-toggle" data-output-native-action="expand" data-output-endpoint="${esc(item.id)}" aria-expanded="${open}">${icon("chevron-right")}<strong>${esc(endpointLabel(item))}</strong></button><div class="output-endpoint-tools"><label class="output-native-check"><input type="checkbox" data-output-native-field="${path}.enabled"${item.enabled?" checked":""}${busy?" disabled":""}>${item.enabled?"已启用":"已停用"}</label><button type="button" class="button small" title="${test?.status==="unknown"?"核对同一次测试，不会再次提交生成请求":"真实生成一张图片，可能产生费用"}" data-output-native-action="test" data-output-endpoint="${esc(item.id)}"${busy||dirty()||!item.enabled?" disabled":""}>${busy===`test:${item.id}`?"生成中…":test?.status==="unknown"?"核对测试结果":"生成测试图"}</button><button type="button" class="output-icon-button" aria-label="删除图片模型" title="删除图片模型" data-output-native-action="remove" data-output-endpoint="${esc(item.id)}"${busy?" disabled":""}>${icon("x")}</button></div></header>
        <div class="output-endpoint-collapse${open?" is-open":""}" data-output-endpoint-collapse><div class="output-endpoint-edit"${open?"":" inert"}><div class="output-endpoint-grid"><label class="output-native-field"><span>平台协议</span><select data-output-native-field="${path}.platform"${busy?" disabled":""}>${options(PLATFORMS,item.platform)}</select><small class="output-native-help">${platformGuide(item.platform)}</small></label>${field("图片模型 ID",path+".model",item.model,"text",'placeholder="如 image-01"',"填写服务商提供的模型标识。")}${field("宽度（像素）",path+".width",item.width,"number",'min="64" max="8192" step="64"')}${field("高度（像素）",path+".height",item.height,"number",'min="64" max="8192" step="64"')}</div><div class="output-endpoint-address">${field("API 根地址",path+".base_url",item.base_url,"url",'placeholder="https://api.example.com/v1"',"可填写服务根地址或完整生图地址；查询参数会保留，路径按所选协议补全。")}${keyField(path,item)}</div><div class="output-endpoint-extra">${field("自定义名称",path+".name",item.name,"text",'placeholder="例如：主力生图"',"留空则显示模型 ID。")}${field("超时秒数",path+".timeout_seconds",item.timeout_seconds,"number",'min="1" max="1800" step="1"',"保存后下一次生图任务生效；正在执行的任务保持原时限。")}</div></div></div>
        ${test?`<div class="output-endpoint-result${test.status==="succeeded"?" is-ready":""}" role="status">${test.status==="succeeded"?"最近测试通过":test.status==="unknown"?"测试结果尚未确认":"测试未通过"} · ${Math.round(test.elapsed_ms||0)/1000} 秒${test.status!=="succeeded"?` · ${esc(test.error||"请核对接口配置")}`:""}</div>`:""}</article>`;
      }).join("")||'<div class="output-endpoint-empty">尚未添加在线生图端点</div>'}</div>${actions()}</section>`;
  }
  function local(){
    if(!draft)return "";
    return `<section class="output-capability-card"><header><h2>本地与工具后端</h2></header>${["comfyui","sd_webui"].map(name=>{
      const item=draft[name]||{};
      return `<section class="output-local-editor"><header><h3>${BACKENDS[name]}</h3></header><div class="output-endpoint-address">${field("API 地址",name+".base_url",item.base_url||"","url")}${keyField(name,item)}</div><div class="output-local-grid">${field("等待秒数",name+".timeout_seconds",item.timeout_seconds||300,"number",'min="1" max="1800"')}</div><label class="output-native-field"><span>${name==="comfyui"?"文生图、普通改图与蒙版局部改图工作流（JSON）":"通用参数（JSON）"}</span><textarea rows="3" spellcheck="false" data-output-native-field="${name}.${name==="comfyui"?"workflows":"parameters"}"${busy?" disabled":""}>${esc(typeof item[name==="comfyui"?"workflows":"parameters"]==="string"?item[name==="comfyui"?"workflows":"parameters"]:JSON.stringify(item[name==="comfyui"?"workflows":"parameters"]||{},null,2))}</textarea>${name==="sd_webui"?'<small class="output-native-help">通用参数可在这里维护；文生图和改图的专属参数可在模型页分别覆盖。</small>':""}</label></section>`;
    }).join("")}${actions()}</section>`;
  }
  function render(){
    if(!root?.isConnected)return;
    selector();
    const naiHost=root.querySelector("#outputNaiWorkspace"),queueHost=root.querySelector("#outputBackendWorkspace"),localHost=root.querySelector("#outputLocalWorkspace");
    if(naiHost){
      naiHost.hidden=naiStatus?.installed===false;
      naiHost.innerHTML=naiHost.hidden?"":naiBackend();
    }
    if(queueHost)queueHost.innerHTML=queue();
    if(localHost)localHost.innerHTML=local();
    root.querySelectorAll("[data-output-native-notice]").forEach(host=>{host.textContent=notice;host.hidden=!notice;host.classList.toggle("is-error",bad);});
    renderScenePresets();
    renderAlbum();
    renderObservation();
    window.lucide?.createIcons?.({attrs:{"aria-hidden":"true"}});
  }
  async function refreshObservability(manual=false){
    if(observationBusy)return;
    const id=ticket,seq=++observationSeq;
    observationBusy=true;observationError="";renderObservation();
    try{
      const data=await product().request("GET",observationEndpoint());
      if(!live(id)||seq!==observationSeq)return;
      observation=data?.images||{total:0,items:[]};
      updateContentSummary(data?.content||null);
      if(manual)observationRefreshedAt=formatClock();
    }catch(error){
      if(!live(id)||seq!==observationSeq)return;
      observation=null;observationError=ERRORS[error.code]||ERRORS[error.message]||"观测记录暂不可读";
      updateContentSummary(null);
    }finally{
      if(live(id)&&seq===observationSeq){observationBusy=false;renderObservation();window.lucide?.createIcons?.({attrs:{"aria-hidden":"true"}});}
    }
  }
  async function refresh(){
    if(busy)return;
    const id=ticket;busy="read";notice="";naiStatus=null;render();
    try{
      if(!product()?.role()?.model_api){
        base=draft=null;revision="";hostRegistered=false;naiStatus=null;bad=false;
        notice="请先在「账号与会话」配置账号并绑定人格，再选择当前角色的生图后端。";
        const data=await product().request("GET","integration/manifest");
        if(live(id))naiStatus=data?.nai||null;
        return;
      }
      const data=await product().request("GET",endpoint());
      if(!live(id))return;
      if(!data?.settings||typeof data.revision!=="string")throw Error("返回的生图后端配置不完整");
      base=clone(data.settings);draft=clone(data.settings);revision=data.revision;hostRegistered=data.host_registered===true;bad=false;
      naiStatus=data.nai||null;
      if(!expanded.size&&draft.endpoints[0])expanded.add(draft.endpoints[0].id);
      void refreshReviewModels(id);
    }catch(error){if(live(id)){bad=true;notice=!product()?.role()?.model_api?"NAI 状态暂不可读；请重新检测，或先在「账号与会话」完成账号与人格配置。":ERRORS[error.code]||ERRORS[error.message]||error.message||"生图后端暂不可读";}}
    finally{if(live(id)){busy="";render();}}
  }
  async function save(){
    if(busy||!dirty())return;
    const id=ticket;busy="save";notice="";render();
    try{
      const data=await product().request("POST",endpoint()+"/save",{revision,changes:clone(draft)});
      if(!live(id))return;
      base=clone(data.settings);draft=clone(data.settings);revision=data.revision;hostRegistered=data.host_registered===true;bad=false;
      naiStatus=data.nai||null;
      notice="生图后端已保存";
    }catch(error){if(live(id)){bad=true;notice=ERRORS[error.code]||ERRORS[error.message]||"保存结果尚未确认，请重新读取核对。";}}
    finally{if(live(id)){busy="";render();}}
  }
  async function refreshReviewModels(id){
    try{
      const path=product().relative(product().role().model_api)+"/image/review/providers";
      const data=await product().request("GET",path);
      if(!live(id))return;
      const choices={"":"跟随任务路由"};
      for(const item of data.providers||[])if(typeof item.id==="string"&&item.id)choices[item.id]=item.name||item.id;
      window.CompanionImageReviewModelChoices=choices;
      window.dispatchEvent(new Event("companion:catalogs-updated"));
    }catch{}
  }
  async function reconnect(){
    if(busy)return;
    const id=ticket;busy="reconnect";notice="正在重连 V2 执行器…";bad=false;render();
    try{
      const data=await product().request("POST",endpoint()+"/reconnect",{});
      if(!live(id))return;
      hostRegistered=Boolean(data.reconnected&&data.host_registered);
      notice=hostRegistered?"V2 图片能力已接入当前会话":data.reconnected?"V2 会话已重连，但当前没有可用的图片操作接入":"V2 会话未能接入图片能力";
      bad=!hostRegistered;
    }catch(error){if(live(id)){bad=true;notice=ERRORS[error.code]||ERRORS[error.message]||"V2 执行器重连失败，请重新读取状态。";}}
    finally{if(live(id)){busy="";render();}}
  }
  async function testEndpoint(endpointId){
    if(busy||dirty())return;
    const id=ticket;busy=`test:${endpointId}`;notice="";render();
    const previous=tests.get(endpointId);
    if(previous?.status!=="unknown")testKeys.set(endpointId,crypto.randomUUID());
    try{
      const bridge=await product().bridgeFor();
      const data=product().unwrap(await bridge.apiPost(endpoint()+"/test",{endpoint_id:endpointId,operation_key:testKeys.get(endpointId)}));
      if(live(id)){tests.set(endpointId,data);bad=false;}
    }catch(error){if(live(id)){tests.set(endpointId,{status:"unknown",error:"请核对原测试结果"});bad=true;notice=ERRORS[error.code]||ERRORS[error.message]||"测试结果暂不可读";}}
    finally{if(live(id)){busy="";render();}}
  }
  function input(event){
    const target=event.target;
    if(target.matches("[data-output-reference-label]")){albumReferenceLabel=target.value;return;}
    if(target.matches("[data-output-reference-role]")){
      if(target.checked)albumReferenceRoles.add(target.dataset.outputReferenceRole);
      else albumReferenceRoles.delete(target.dataset.outputReferenceRole);
      albumReferenceError="";renderAlbum();return;
    }
    if(target.matches("[data-output-nai-operation]")&&draft&&!busy){
      const operation=target.dataset.outputNaiOperation;
      if(!["generate","edit"].includes(operation))return;
      draft.operation_routes??={generate:[],edit:[]};
      const routes=draft.operation_routes[operation]||[];
      draft.operation_routes[operation]=target.checked?[...new Set([...routes,"nai"])]:routes.filter(route=>route!=="nai");
      if(target.checked)draft.enabled=true;
      render();return;
    }
    if(target.matches("[data-output-album-autoload]")){
      albumAutoLoad=Boolean(target.checked);
      if(albumAutoLoad)bindAlbumSentinel();else disconnectAlbumObserver();
      return;
    }
    if(target.matches("[data-output-album-select-all]")){selectAllAlbums(target.checked);return;}
    if(target.matches("[data-output-album-select]")){toggleAlbumSelection(target.dataset.outputAlbumSelect,target.checked);return;}
    if(target.matches("[data-output-scene-index][data-output-scene-field]")&&Array.isArray(sceneDraft)){
      const index=Number(target.dataset.outputSceneIndex);
      if(sceneDraft[index])sceneDraft[index][target.dataset.outputSceneField]=target.value;
      sceneError="";
      return;
    }
    if(target.matches('[data-wb-field="scene_presets_json"]')){renderScenePresets();return;}
    if(!target.matches("[data-output-native-field]")||!draft||busy)return;
    const path=target.dataset.outputNativeField.split(".");let object=draft;
    for(const key of path.slice(0,-1)){object[key]??={};object=object[key];}
    object[path.at(-1)]=target.type==="checkbox"?target.checked:target.type==="number"?Number(target.value):target.value;
    if(path.at(-1)==="clear_api_key"&&target.checked)object.api_key="";
    if(path.at(-1)==="api_key"&&target.value)object.clear_api_key=false;
    if(path.at(-1)==="enabled")render();
    root.querySelectorAll('[data-output-native-action="save"]').forEach(button=>{button.disabled=!dirty();});
    root.querySelectorAll('[data-output-native-action="test"]').forEach(button=>{button.disabled=dirty()||!draft.endpoints.find(item=>item.id===button.dataset.outputEndpoint)?.enabled;});
    if(path.at(-1)==="platform"){
      const help=target.closest(".output-native-field")?.querySelector(".output-native-help");
      if(help)help.textContent=platformGuide(target.value);
    }
    if(target.dataset.outputNativeField==="enabled")render();
  }
  function click(event){
    const target=event.target.closest("[data-output-native-action]");
    if(!target||!root?.contains(target)||target.disabled)return;
    const action=target.dataset.outputNativeAction,identifier=target.dataset.outputEndpoint;
    if(busy&&!action.startsWith("album-")&&!['works-refresh','work-preview','work-head','work-candidate','work-adopt'].includes(action))return;
    if(action==="refresh"){refresh();return;}
    if(action==="save"){save();return;}
    if(action==="test"){testEndpoint(identifier);return;}
    if(action==="reconnect"){reconnect();return;}
    if(action==="refresh-preferences"){void refreshPreferences();return;}
    if(action==="observability"){refreshObservability(true);return;}
    if(action==="works-refresh"){void loadWorks();return;}
    if(action==="work-preview"){
      const item=works[Number(target.dataset.workIndex)];
      void previewWork(item,!!item?.candidate_version);return;
    }
    if(action==="work-head"||action==="work-candidate"){
      void previewWork(workSelection?.item,action==="work-candidate");return;
    }
    if(action==="work-adopt"){void adoptWork(target);return;}
    if(action==="jump-observability"){
      root.querySelector('#output-tab-capabilities')?.click();
      requestAnimationFrame(()=>root.querySelector("#outputObservationSection")?.scrollIntoView({behavior:"smooth",block:"start"}));
      return;
    }
    if(action==="manage-scenes"){
      const field=root.querySelector('[data-wb-field="scene_presets_json"]');
      if(!field){sceneError="角色配置尚未就绪";sceneEditorOpen=true;renderScenePresets();return;}
      try{sceneDraft=clone(JSON.parse(field.value));}catch{sceneDraft=[];}
      sceneEditorOpen=!sceneEditorOpen;sceneError="";renderScenePresets();
      if(sceneEditorOpen)root.querySelector("#outputScenePresetEditor")?.scrollIntoView({behavior:"smooth",block:"nearest"});
      return;
    }
    if(action==="add-scene"){
      if(sceneSaving||!Array.isArray(sceneDraft)||sceneDraft.length>=12)return;
      sceneDraft.push({id:`scene-${crypto.randomUUID().replace(/-/g,"").slice(0,12)}`,name:"新场景",prompt:""});
      sceneError="";renderScenePresets();return;
    }
    if(action==="remove-scene"){
      if(sceneSaving||!Array.isArray(sceneDraft))return;
      sceneDraft.splice(Number(target.dataset.outputSceneIndex),1);
      sceneError="";renderScenePresets();return;
    }
    if(action==="save-scenes"){
      void saveScenePresets();
      return;
    }
    if(action==="album-refresh"){void loadAlbum(false);return;}
    if(action==="album-export"){void exportAlbum();return;}
    if(action==="album-hide"){void hideAlbum();return;}
    if(action==="album-reference"){openAlbumReference();return;}
    if(action==="album-reference-close"){closeAlbumReference();return;}
    if(action==="album-reference-save"){void saveAlbumReference();return;}
    if(action==="album-open-folder"){void openAlbumFolder();return;}
    if(action==="album-more"){void loadAlbum(true);return;}
    if(action==="album-load-batch"){void loadAlbumBatch(ALBUM_BATCH_PAGES);return;}
    if(action==="album-filter"){
      const next=target.dataset.outputAlbumFilter||"all";
      if(ALBUM_FILTERS.some(([id])=>id===next)&&next!==albumFilter){albumFilter=next;albumSelectedIds.clear();renderAlbum();}
      return;
    }
    if(action==="album-page-size"){
      const next=Number(target.dataset.outputAlbumSize);
      if(ALBUM_PAGE_SIZES.includes(next)&&next!==albumPageSize&&!albumLoading&&!albumBatchLoading){
        albumPageSize=next;
        void loadAlbum(false);
      }
      return;
    }
    if(action==="album-top"){
      const reduced=typeof matchMedia==="function"&&matchMedia("(prefers-reduced-motion: reduce)").matches;
      root?.querySelector("#outputAlbumWorkspace")?.scrollIntoView?.({behavior:reduced?"instant":"smooth",block:"start"});
      return;
    }
    if(action==="album-open"){openAlbum(Number(target.dataset.outputImageIndex));return;}
    if(action==="album-close"){closeAlbumLightbox();return;}
    if(action==="album-prev"){stepAlbum(-1);return;}
    if(action==="album-next"){stepAlbum(1);return;}
    if(action==="album-zoom"){
      if(!currentLightboxItem())return;
      albumZoomed=!albumZoomed;renderAlbumLightbox();return;
    }
    if(action==="album-retry-full"){
      const item=currentLightboxItem();
      if(item){void loadFullAlbumImage(item,{force:true});renderAlbumLightbox();}
      return;
    }
    if(action==="album-copy-name"){
      void copyAlbumFilename(target.dataset.outputFilename||"");
      return;
    }
    if(action==="album-download"){
      const item=currentLightboxItem();
      if(item)void downloadFullAlbumImage(item);
      return;
    }
    if(!draft)return;
    const index=draft.endpoints.findIndex(item=>item.id===identifier);
    if(action==="expand"){
      const nextOpen=!expanded.has(identifier);
      if(nextOpen)expanded.add(identifier);else expanded.delete(identifier);
      const card=target.closest?.(".output-endpoint-card");
      const collapse=card?.querySelector?.("[data-output-endpoint-collapse]");
      if(collapse){
        target.setAttribute?.("aria-expanded",String(nextOpen));
        collapse.classList?.toggle?.("is-open",nextOpen);
        const edit=collapse.querySelector?.(".output-endpoint-edit");
        if(edit&&typeof edit.toggleAttribute==="function")edit.toggleAttribute("inert",!nextOpen);
        return;
      }
    }
    if(action==="add"){
      const item={id:crypto.randomUUID(),name:"",enabled:false,
        platform:"auto",base_url:"",api_key:"",model:"",width:1024,height:1024,timeout_seconds:180,
        quality:"",response_format:""};
      draft.endpoints.push(item);expanded.add(item.id);
      draft.operation_routes??={generate:[],edit:[]};
      for(const operation of ["generate","edit"]){draft.operation_routes[operation]??=[];if(!draft.operation_routes[operation].includes(`online:${item.id}`))draft.operation_routes[operation].push(`online:${item.id}`);}
    }
    if(action==="remove"&&index>=0){draft.endpoints.splice(index,1);for(const operation of ["generate","edit"])if(Array.isArray(draft.operation_routes?.[operation]))draft.operation_routes[operation]=draft.operation_routes[operation].filter(route=>route!==`online:${identifier}`);}
    render();
  }
  function keydown(event){
    if(!currentLightboxItem()||!root?.isConnected)return;
    const tag=String(event.target?.tagName||"").toUpperCase();
    if(tag==="INPUT"||tag==="TEXTAREA"||tag==="SELECT"||event.target?.isContentEditable)return;
    if(event.key==="Escape"){event.preventDefault?.();closeAlbumLightbox();return;}
    if(event.key==="ArrowLeft"){event.preventDefault?.();stepAlbum(-1);return;}
    if(event.key==="ArrowRight"){event.preventDefault?.();stepAlbum(1);}
  }
  function unmount(){
    ticket++;abort?.abort();abort=null;disconnectAlbumObserver();clearFullImageCache();clearTimeout(albumCopyTimer);albumCopyTimer=null;clearTimeout(albumLightboxCloseTimer);albumLightboxCloseTimer=null;
    root=null;draft=base=null;naiStatus=null;busy="";scope="";observation=null;observationBusy=false;observationSeq++;observationRefreshedAt="";sceneEditorOpen=false;sceneDraft=null;sceneSaving=false;
    albumSources=[];albumItems=[];albumCursor=null;albumTotal=null;albumLoaded=false;albumLoading=false;albumBatchLoading=false;albumBatchStep=0;albumBatchTotal=0;albumError="";albumStandaloneItem=null;albumSelected=-1;albumSelectedId="";albumSelectedIds=new Set();albumNotice="";albumBusy="";albumFilter="all";albumZoomed=false;albumCopyFeedback="";
    albumReferenceOpen=false;albumReferenceImageId="";albumReferenceLabel="";albumReferenceRoles=new Set(["identity"]);albumReferenceError="";
    authoringStatus=null;
  }
  function mount(node){
    unmount();root=node;scope=product()?.scopeKey?.()||"";works=[];worksLoaded=false;worksBusy=false;workSelection=null;authoringStatus=null;expanded=new Set();tests=new Map();testKeys=new Map();notice="";bad=false;observation=null;observationError="";observationRefreshedAt="";sceneError="";
    if(!root)return;
    abort=new AbortController();
    root.addEventListener("input",input,{signal:abort.signal});
    root.addEventListener("change",input,{signal:abort.signal});
    root.addEventListener("click",click,{signal:abort.signal});
    if(typeof window.addEventListener==="function")window.addEventListener("keydown",keydown,{signal:abort.signal});
    refresh();
    loadAlbum(false);
    refreshObservability();
    if(!root.querySelector("#output-panel-creation")?.hidden)showWorks();
  }
  function showWorks(){if(!worksLoaded)void loadWorks();}
  return {mount,unmount,refresh,refreshAlbum:()=>loadAlbum(false),openLightbox,refreshObservability,showWorks,configUpdated:renderScenePresets};
})();
