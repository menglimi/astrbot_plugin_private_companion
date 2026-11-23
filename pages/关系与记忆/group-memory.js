"use strict";
window.CompanionGroupMemoryPage = (() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  const unwrap = response => {
    const wrapped = ["ok", "error"].includes(response?.status) && response?.data !== undefined ? response
      : ["ok", "error"].includes(response?.data?.status) && response?.data?.data !== undefined ? response.data : null;
    if (wrapped?.status === "error") throw Object.assign(new Error(wrapped.data?.code || "request_failed"), {code: wrapped.data?.code});
    return wrapped ? wrapped.data : response;
  };
  const labels = {pending:"待审阅", reviewed:"已审阅", dismissed:"已忽略"};
  const createUuid = () => {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const bytes = new Uint8Array(16);
    if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") crypto.getRandomValues(bytes);
    else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map(v => v.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  };
  const clockNow = () => {
    const d = new Date(), p = n => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  };
  let activeRefresh = null;
  function confirmAction(message) {
    const dialog = document.createElement("dialog");
    dialog.className = "gm-confirm-dialog";
    dialog.setAttribute("aria-labelledby", "gm-confirm-title");
    dialog.setAttribute("aria-describedby", "gm-confirm-message");
    const title = document.createElement("h2");
    title.id = "gm-confirm-title";
    title.textContent = "确认撤回群公共陈述";
    const description = document.createElement("p");
    description.id = "gm-confirm-message";
    description.textContent = message;
    const actions = document.createElement("div");
    actions.className = "gm-confirm-actions";
    const cancel = document.createElement("button");
    cancel.type = "button"; cancel.className = "secondary"; cancel.textContent = "取消";
    const accept = document.createElement("button");
    accept.type = "button"; accept.className = "danger"; accept.textContent = "确认撤回";
    actions.append(cancel, accept);
    dialog.append(title, description, actions);
    document.body.append(dialog);
    return new Promise(resolve => {
      let settled = false;
      const finish = accepted => {
        if (settled) return;
        settled = true;
        if (dialog.open) dialog.close();
        dialog.remove();
        resolve(accepted);
      };
      cancel.addEventListener("click", () => finish(false));
      accept.addEventListener("click", () => finish(true));
      dialog.addEventListener("cancel", event => { event.preventDefault(); finish(false); });
      dialog.addEventListener("click", event => {
        if (event.target !== dialog) return;
        const bounds = dialog.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right
            || event.clientY < bounds.top || event.clientY > bounds.bottom) finish(false);
      });
      try { dialog.showModal(); cancel.focus({preventScroll:true}); }
      catch { finish(false); }
    });
  }
  function mount(bridge, context) {
    const main = document.querySelector("main");
    if (!main) return;
    const state = {view:"archive", filter:"active", reviewFilter:"pending", items:[], selected:null,
      detail:null, safety:[], cursor:null, after:"", page:0, cursors:[null], busy:false, pending:null, receipt:null,
      loadSeq:0, detailSeq:0, refreshedAt:""};
    main.innerHTML = `<header class="heading"><div><span class="eyebrow">群聊档案 <span>·</span> ${esc(context.display?.target_label || "当前群")}</span><h1>群公共记忆</h1><p class="intro">查看本群共享的成员陈述及安全审阅记录。管理员撤回会影响后续引用，不修改成员身份或权限。</p></div><div class="refresh-cluster"><span id="gm-refresh-stamp" class="refresh-stamp" aria-live="polite" hidden></span><button id="gm-refresh" class="secondary" type="button" aria-busy="false">刷新</button></div></header>
      <section class="scope" aria-label="当前管理范围"><div><span>当前角色</span><strong>${esc(context.display?.role_name || "当前角色")}</strong></div><div><span>管理范围</span><strong>${esc(context.display?.target_label || "当前群")}</strong></div><div><span>数据范围</span><strong>群内共享档案</strong></div><div><span>操作人</span><strong>已登录管理员</strong></div></section>
      <div id="gm-notice" role="status" aria-live="polite" hidden></div>
      <nav class="gm-tabs" aria-label="群档案视图"><button type="button" data-gm-view="archive" aria-pressed="true">共享陈述</button><button type="button" data-gm-view="safety" aria-pressed="false">安全审阅</button></nav>
      <div class="workspace"><section class="inventory"><div class="section-head"><h2 id="gm-list-heading">共享陈述</h2><span id="gm-count" class="muted"></span></div><label class="filter-label" for="gm-filter">档案状态</label><select id="gm-filter"><option value="active">生效中</option><option value="retracted">已撤回</option></select><div id="gm-list" class="record-list" aria-busy="true"><p class="empty">正在读取群档案…</p></div><div class="pagination"><button id="gm-previous" class="secondary" type="button" disabled>上一页</button><span id="gm-page">第 1 页</span><button id="gm-next" class="secondary" type="button" disabled>下一页</button></div></section><section id="gm-detail" class="detail" aria-live="polite"><div class="placeholder"><h2>选择一条陈述</h2></div></section></div>`;
    const $ = id => document.getElementById(id);
    const notice = (text, error=false) => { const el=$("gm-notice"); el.textContent=text; el.hidden=!text; el.className=error?"error":""; };
    const itemId = item => state.view === "archive" ? item?.memory_id : item?.hit_id;
    const syncRefreshButton = (refreshing = false) => {
      const btn = $("gm-refresh");
      if (btn) {
        btn.disabled = state.busy;
        btn.setAttribute("aria-busy", refreshing ? "true" : "false");
        btn.classList?.toggle?.("is-refreshing", refreshing);
        btn.textContent = refreshing ? "刷新中…" : "刷新";
      }
      const stamp = $("gm-refresh-stamp");
      if (stamp) {
        stamp.textContent = state.refreshedAt ? `刚刚已刷新 ${state.refreshedAt}` : "";
        stamp.hidden = !state.refreshedAt;
      }
    };
    async function get(path, params={}) { return unwrap(await bridge.apiGet(path, params)); }
    async function post(path, body) { return unwrap(await bridge.apiPost(path, body)); }
    function renderList() {
      const list=$("gm-list"); list.replaceChildren();
      $("gm-count").textContent=`${state.items.length} 条`;
      $("gm-page").textContent=`第 ${state.page+1} 页`;
      $("gm-previous").disabled=state.busy||state.page===0;
      $("gm-next").disabled=state.busy||!state.cursor;
      syncRefreshButton(false);
      if (!state.items.length) { list.append(Object.assign(document.createElement("p"),{className:"empty",textContent:"当前范围没有记录。"})); return; }
      const selectedId = itemId(state.selected);
      state.items.forEach(item=>{
        const button=document.createElement("button"); button.type="button"; button.className="record";
        const currentId = itemId(item);
        button.setAttribute("aria-current",String(Boolean(selectedId && currentId && selectedId === currentId)));
        const title=state.view==="archive"?(item.statement||item.unavailable_reason||"来源当前不可用"):`${item.rule_id || "安全规则"} · ${labels[item.review_state] || "待审阅"}`;
        const summary=state.view==="archive"?`成员 ${item.speaker_id || "未知"} · 第 ${item.revision} 版`:`成员 ${item.member_id || "未知"} · ${item.severity || "未分级"}`;
        button.innerHTML=`<span class="record-title"><strong>${esc(title)}</strong><span class="badge">${esc(state.view==="archive"?item.state:item.direction||"安全命中")}</span></span><span class="record-preview">${esc(summary)}</span><span class="record-date">${esc(item.said_at||item.occurred_at||item.updated_at||"")}</span>`;
        button.addEventListener("click",()=>{state.selected=item;state.detail=null;renderList();renderDetail();if(state.view==="archive")loadDetail(item);});
        list.append(button);
      });
    }
    function renderDetail() {
      const panel=$("gm-detail"); panel.replaceChildren();
      const item=state.selected;
      if(!item){panel.innerHTML=`<div class="placeholder"><h2>${state.view==="archive"?"选择一条陈述":"选择一条安全记录"}</h2>${state.receipt?`<p class="evidence">操作回执：${esc(state.receipt.operation_id)} · ${esc(state.receipt.state||"已记录")}</p>`:""}</div>`;return;}
      const head=document.createElement("div");head.className="section-head";
      const title=document.createElement("h2");title.id="detail-heading";title.textContent=state.view==="archive"?(item.statement||"群成员陈述"):item.rule_id||"安全命中";
      const badge=document.createElement("span");badge.className="badge";badge.textContent=state.view==="archive"?item.state:(labels[item.review_state]||"待审阅");head.append(title,badge);panel.append(head);
      if(state.view==="archive"){
        const evidence=state.detail?.evidence;
        const text=document.createElement("p");text.className="memory-text";text.textContent=evidence?.text||item.unavailable_reason||"读取原始群消息中…";panel.append(text);
        const meta=document.createElement("p");meta.className="evidence";meta.textContent=evidence?`成员 ${evidence.speaker_id} · ${evidence.said_at} · 原始群消息`:"详情只显示与该陈述绑定的原始群消息。";panel.append(meta);
        if(state.view==="archive"&&item.state==="active"){
          const actions=document.createElement("div");actions.className="actions";
          const retract=document.createElement("button");retract.type="button";retract.className="danger";retract.textContent="管理员撤回";
          retract.addEventListener("click",()=>retractItem(item));actions.append(retract);panel.append(actions);
        }
      }else{
        const body=document.createElement("p");body.className="memory-text";body.textContent=JSON.stringify(item.evidence||{},null,2);panel.append(body);
        if(item.review_state==="pending"){
          const actions=document.createElement("div");actions.className="actions";
          [["reviewed","标记已审阅"],["dismissed","忽略此命中"]].forEach(([value,label])=>{const button=document.createElement("button");button.type="button";button.className=value==="reviewed"?"primary":"secondary";button.textContent=label;button.addEventListener("click",()=>reviewHit(item,value));actions.append(button);});panel.append(actions);
        }
        const note=document.createElement("p");note.className="evidence";note.textContent="安全审阅只记录管理员判断，不会更改成员身份或授权。";panel.append(note);
      }
      if(state.receipt){const receipt=document.createElement("p");receipt.className="evidence";receipt.textContent=`操作回执：${state.receipt.operation_id} · ${state.receipt.state || "已记录"}`;panel.append(receipt);}
    }
    async function loadDetail(item) {
      const seq = ++state.detailSeq;
      const memoryId = item?.memory_id;
      const view = state.view;
      try {
        const detail = await post("group-memory/detail",{memory_id:memoryId,revision:item.revision});
        if (seq !== state.detailSeq || state.view !== view || state.selected?.memory_id !== memoryId) return;
        state.detail = detail;
        renderDetail();
      } catch {
        if (seq !== state.detailSeq || state.view !== view || state.selected?.memory_id !== memoryId) return;
        notice("原始群消息当前无法读取；陈述状态仍可在列表中确认。",true);
      }
    }
    async function load({manual = false} = {}) {
      if (manual && state.busy) return;
      const seq = ++state.loadSeq;
      state.detailSeq += 1;
      const view = state.view;
      const keepId = manual ? itemId(state.selected) : null;
      let failed = false;
      state.busy = true; notice(""); $("gm-list").setAttribute("aria-busy", "true");
      syncRefreshButton(manual);
      try {
        const path = view === "archive" ? "group-memory" : "group-memory/safety";
        const params = {limit: 20, ...(state.after ? {after_id: state.after} : {})};
        if (view === "archive") params.state = state.filter;
        else if (state.reviewFilter) params.review_state = state.reviewFilter;
        const result = await get(path, params);
        if (seq !== state.loadSeq || state.view !== view) return;
        state.items = result.items || []; state.cursor = result.next_cursor || null;
        state.selected = keepId ? (state.items.find(row => itemId(row) === keepId) || null) : null;
        if (!state.selected) state.detail = null;
        if (manual) state.refreshedAt = clockNow();
      } catch (error) {
        if (seq !== state.loadSeq || state.view !== view) return;
        failed = true;
        state.items = []; state.selected = null; state.detail = null;
        notice("当前账号或群档案不可用，请确认已登录且拥有本体管理权限后刷新。", true);
      } finally {
        if (seq !== state.loadSeq) return;
        state.busy = false; $("gm-list").setAttribute("aria-busy", "false");
        renderList(); renderDetail();
        if (!failed && state.selected && view === "archive" && !state.detail) void loadDetail(state.selected);
      }
    }
    async function retractItem(item) {
      if(!await confirmAction("后续对话将不再引用这条陈述；原始成员消息不会删除。"))return;
      const body={memory_id:item.memory_id,expected_revision:item.revision,view_token:item.view_token,reason:"管理员在群公共记忆页面撤回"};
      const fingerprint=JSON.stringify(body);if(state.pending?.fingerprint!==fingerprint)state.pending={fingerprint,key:createUuid()};body.operation_key=state.pending.key;
      state.busy=true;try{const result=await post("group-memory/retract",body);state.receipt=await get("group-memory/operation",{operation_id:result.operation_id});state.pending=null;notice("群公共陈述已撤回；原始消息未删除。",false);await load();}
      catch(error){notice("撤回结果尚未确认。刷新列表核对状态后，可重试同一操作。",true);}
      finally{state.busy=false;}
    }
    async function reviewHit(item,review_state){
      const body={hit_id:item.hit_id,review_state,note:"",operation_key:createUuid()};state.busy=true;
      try{await post("group-memory/safety/review",body);notice("安全审阅已记录，成员权限未改变。");await load();}
      catch{notice("审阅结果未确认，请刷新该群的安全记录。",true);}
      finally{state.busy=false;}
    }
    document.querySelectorAll("[data-gm-view]").forEach(button=>button.addEventListener("click",()=>{
      state.view=button.dataset.gmView;state.page=0;state.after="";state.items=[];state.selected=null;state.detail=null;state.receipt=null;
      document.querySelectorAll("[data-gm-view]").forEach(tab=>tab.setAttribute("aria-pressed",String(tab===button)));
      $("gm-filter").hidden=false;
      $("gm-filter").innerHTML=state.view==="archive"
        ?'<option value="active">生效中</option><option value="retracted">已撤回</option>'
        :'<option value="pending">待审阅</option><option value="reviewed">已审阅</option><option value="dismissed">已忽略</option><option value="">全部状态</option>';
      $("gm-filter").value=state.view==="archive"?state.filter:state.reviewFilter;
      $("gm-list-heading").textContent=state.view==="archive"?"共享陈述":"安全审阅";
      $("gm-filter").previousElementSibling.hidden=state.view!=="archive";load();
    }));
    $("gm-filter").addEventListener("change",()=>{
      if(state.view==="archive")state.filter=$("gm-filter").value;
      else state.reviewFilter=$("gm-filter").value;
      state.page=0;state.after="";state.cursors=[null];load();
    });
    $("gm-refresh").addEventListener("click",()=>load({manual:true}));
    $("gm-previous").addEventListener("click",()=>{if(state.page>0){state.page--;state.after=state.cursors[state.page]||"";load();}});
    $("gm-next").addEventListener("click",()=>{if(state.cursor){state.cursors??=[];state.cursors[state.page+1]=state.cursor;state.after=state.cursor;state.page++;load();}});
    activeRefresh = () => load({manual:true});
    load();
  }
  return {mount, refresh: () => activeRefresh?.()};
})();
