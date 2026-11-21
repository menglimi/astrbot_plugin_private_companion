"use strict";
window.CompanionLearningReviewPage = (() => {
  const PREFIX = "/astrbot_plugin_private_companion/";
  const DIMENSION = {
    sentence_rhythm: "句读节奏",
    emoji: "表情符号",
    punctuation: "标点",
    directness: "直接程度",
    diction: "措辞",
    formatting: "排版",
    other: "其他",
  };
  const ACTION = {accept:"确认申报",reject:"驳回",adopt:"采纳建议"};
  const FILTERS = [["all","全部"],["growth","进展核实"],["style","风格采纳"],["self","复盘确认"]];
  const CHANNEL = {private:"私聊",group:"群聊",proactive:"主动消息",qzone:"QQ 空间",tts:"语音"};
  const RELATIONSHIP = {stranger:"陌生",familiar:"熟悉",close:"亲近",any:"不限"};
  const EMOTION = {normal:"常态",positive:"积极",low:"低落",guarded:"谨慎",any:"不限"};
  const INTENT = {acknowledgement:"确认接话",question:"提问",request:"请求",help:"求助",comfort:"安慰",
    play:"玩笑",tease:"打趣",intimacy:"亲昵",boundary:"边界",emotion:"情绪表达",casual:"闲聊",
    proactive:"主动联系",any:"不限"};
  const ERRORS = {growth_view_stale:"记录已变化，请重新读取。",growth_progress_decided:"这条进展已经处理。",
    expression_view_stale:"候选已变化，请重新读取。",expression_decided:"这条规则已经处理。",
    self_review_view_stale:"指导已变化，请重新读取。",self_review_decided:"这条指导已经处理。",
    bridge_unavailable:"连接暂不可用，请重新读取。",staging_not_initialized:"角色数据尚未加载，请重新读取。"};
  const e = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const norm = value => typeof value === "string" ? value.trim() : "";
  const sameText = (left, right) => Boolean(left && right && left === right);
  const when = value => typeof value === "string" && Number.isFinite(Date.parse(value))
    ? new Date(value).toLocaleString("zh-CN",{hour12:false,month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "时间未知";
  const relative = value => typeof value === "string" && value.startsWith(PREFIX) ? value.slice(PREFIX.length) : "";
  let root = null, ticket = 0, readTicket = 0, sources = [], pages = [], data = null;
  const live = current => root?.isConnected && current === ticket;
  function routeFor(source, base, suffix) {
    return base + suffix;
  }

  async function api(method, route, value) {
    const product = window.CompanionProduct;
    const bridge = product?.bridgeNow?.() || await product?.bridgeFor?.();
    if (!bridge?.apiGet) throw Object.assign(new Error("bridge_unavailable"), {code:"bridge_unavailable"});
    let timer,response;
    try{
      response=await Promise.race([method === "GET" ? bridge.apiGet(route, value) : bridge.apiPost(route, value),
        new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error("bridge_timeout")),15000);})]);
    }finally{clearTimeout(timer);}
    if (response?.status === "error") throw Object.assign(new Error(response.message || "request_failed"), {code:response.data?.code || response.code || response.message});
    if (response?.status === "ok" && response.data !== undefined) return response.data;
    return product?.unwrap ? product.unwrap(response) : response?.data ?? response;
  }

  /* 每页记下「已经读过的游标」（第 1 页是 ""）：刷新时按它原地重读同一批页，
     而不是退回只读第 1 页。 */
  function buildPages() {
    return sources.flatMap(source => {
      const growth = relative(source.growthApi || source.api), reply = relative(source.replyApi);
      return [
        growth && {source,type:"growth",path:routeFor(source,growth,"/progress"),query:{limit:20}},
        reply && {source,type:"style",path:routeFor(source,reply,"/style"),query:{state:"pending",limit:20}},
        growth && {source,type:"self",path:routeFor(source,growth,"/self-review"),query:{state:"pending",limit:20}},
      ].filter(Boolean).map(page => ({...page,items:[],cursors:[""],next:"",loaded:false}));
    });
  }
  const ID_FIELD = {growth:"review_id",style:"signal_id",self:"item_id"};
  /* 按 key 合并：就地更新同一条、保留刷新时后端没有返回的旧行。
     待裁决队列会因为「别处处理掉了」而变短，撤掉旧行等于用户眼前少了几条。 */
  function mergeRows(previous, incoming) {
    const merged = previous.slice();
    const positions = new Map(merged.map((row, position) => [keyFor(row), position]));
    incoming.forEach(row => {
      const position = positions.get(keyFor(row));
      if (position === undefined) {positions.set(keyFor(row), merged.length); merged.push(row);}
      else merged[position] = row;
    });
    return merged;
  }
  function dedupeRows(rows) {
    const seen = new Set();
    return rows.filter(row => {
      const key = keyFor(row);
      if (seen.has(key)) return false;
      seen.add(key); return true;
    });
  }
  /* 待批阅队列自身是滚动容器（`.lh-review-queue-list`，兼容回退 `.lh-review-queue`），重绘前记位置、重绘后复位。 */
  function queueNode() {
    try {
      return root?.querySelector?.(".lh-review-queue-list")
        || root?.querySelector?.(".lh-review-queue")
        || null;
    } catch (_) { return null; }
  }
  /* 按游标列表逐页读。中途某页读不到就停在该页：已经读到的行仍然有效，
     下一页游标保持原样，下次「加载更多」还会重试它。 */
  async function readChain(page, cursors) {
    const rows = [];
    let next = "", failed = false;
    for (const cursor of cursors) {
      let value;
      try {
        value = await api("GET",page.path,{...page.query,...(cursor?{after_id:cursor}:{})});
      } catch (error) { failed = true; break; }
      if (!Array.isArray(value?.items)) { failed = true; break; }
      rows.push(...value.items.filter(item=>page.type!=="self"||item.kind==="ask_user")
        .map(item=>({...item,_type:page.type,_source:page.source,_id:item[ID_FIELD[page.type]]})));
      next = typeof value.next_cursor === "string" ? value.next_cursor : "";
    }
    return {rows, next, failed};
  }
  /* 读取有三种模式：
       reset   首次挂载/换筛选：重建页记录，只读第 1 页（用户主动重置）；
       append  「加载更多」：往后读一页并追加；
       refresh 重新读取 / 裁决后：按已读游标原地重读同一批页并按 key 合并，
               **不减少**已经显示的行，也不清掉选中与勾选。
     除用户主动切筛选外，任何刷新都不走 reset —— 那是「队列被打回第一页」的根源。 */
  async function read(current, mode = "reset") {
    const append = mode === true || mode === "append";
    const refresh = mode === "refresh";
    if (!live(current) || !data || data.writing || ((append || refresh) && data.loading)) return;
    const request = ++readTicket;
    const scrollTop = append || refresh ? queueNode()?.scrollTop : undefined;
    if (!append && !refresh) {
      const previousNotice = data?.notice || "";
      const previousError = !!data?.error;
      data = {loading:true, writing:false, items:[], selected:"", checked:new Set(), filter:data?.filter || "all",
        notice:previousNotice, error:previousError, confirming:null, errors:0};
      pages = buildPages();
    } else data.loading = true;
    render({resetScroll: !append && !refresh});
    const wanted = pages
      .filter(page => (append ? Boolean(page.next) : true))
      .map(page => ({page, cursors: append ? [page.next]
        /* 刷新时重读已加载的整条游标链；首次读取失败过的页（loaded=false）重试第 1 页。 */
        : page.loaded ? page.cursors.slice() : [""]}));
    const fetched = await Promise.allSettled(wanted.map(({page,cursors}) => readChain(page,cursors)));
    if (!live(current) || request !== readTicket) return;
    data.errors = 0;
    fetched.forEach((result,index) => {
      const {page,cursors} = wanted[index];
      if (result.status === "rejected") { data.errors++; return; }
      const value = result.value;
      if (value.failed) data.errors++;
      /* 一页都没读到：旧行与旧游标原地不动，等于这次刷新什么也没换成。 */
      if (value.failed && !value.rows.length) return;
      if (!append && !refresh) {
        page.items = dedupeRows(value.rows);
        page.cursors = [""];
      } else if (append) {
        page.items = dedupeRows(page.items.concat(value.rows));
        page.cursors = page.cursors.concat(cursors);
      } else {
        page.items = mergeRows(page.items, value.rows);
        if (!value.failed) page.cursors = cursors.slice();
      }
      if (!value.failed) {
        /* 服务端把同一个游标又发回来时收掉「加载更多」，避免原地打转。 */
        const requested = cursors[cursors.length - 1] || "";
        page.next = (refresh ? value.next : (value.next === requested ? "" : value.next));
      }
      page.loaded = true;
    });
    const seen = new Set();
    data.items = pages.flatMap(page=>page.items).filter(item=>{
      const key=keyFor(item);if(seen.has(key))return false;seen.add(key);return true;
    });
    data.items.sort((a,b) => (Date.parse(b.created_at || "") || 0) - (Date.parse(a.created_at || "") || 0));
    data.items.forEach(keyFor);
    /* 选中与勾选按 key 保留：刷新不该把用户正在看/正在挑的那几条换掉。 */
    const present = new Set(data.items.map(item => keyFor(item)));
    data.checked = new Set([...data.checked].filter(key => present.has(key)));
    if (data.confirming?.key && !present.has(data.confirming.key)) data.confirming = null;
    if (data.confirming?.bulk) {
      const bulkAction = data.confirming.action;
      const visible = data.items.filter(item => data.filter === "all" || item._type === data.filter);
      const hasEligible = data.checked.size > 0 && visible.some(item => data.checked.has(keyFor(item))
        && actions(item).includes(bulkAction === "accept" ? (item._type === "style" ? "adopt" : "accept") : "reject"));
      if (!hasEligible) data.confirming = null;
    }
    if (!present.has(data.selected)) data.selected = data.items[0]?._key || "";
    data.loading = false;
    render({resetScroll: !append && !refresh});
    const node = queueNode();
    if (node && Number.isFinite(scrollTop)) node.scrollTop = scrollTop;
  }

  function keyFor(item) { return item._key || (item._key = `${item._source.key}::${item._type}::${item._id}`); }
  function selectedItem() { return data?.items.find(item => keyFor(item) === data.selected) || null; }
  function title(item) {
    if (item._type === "growth") return norm(item.growth_title) || "目标已不可读取";
    if (item._type === "style") {
      const pattern = norm(item.expression?.pattern);
      if (pattern && !/^[-_—\s[\]()（）]+$/.test(pattern)) return pattern;
      return norm(item.proposal) || norm(item.observation) || "表达候选";
    }
    return norm(item.observation) || "需要确认的指导";
  }
  function typeFor(item) {
    if (item._type === "growth") return "进展核实";
    if (item._type === "self") return "复盘确认";
    if (item.expression?.kind === "grammar") return "语法句式";
    if (item.dimension === "sentence_rhythm") return "局部节奏";
    return "情境表达";
  }
  function sourceKindLabel(item) {
    if (item._type === "growth") return "成长记录";
    if (item._type === "self") return "每日复盘";
    if (item.source_kind === "shared_pack") return "分享包";
    if (item.source_kind === "legacy_expression") return "旧版表达";
    if (item.source_kind === "conversation_input") return "学习记录";
    return "角色表达";
  }
  function styleMetricLabel(item) {
    if (item._type !== "style") return "";
    const bits = [];
    if (item.dimension) bits.push(DIMENSION[item.dimension] || item.dimension);
    if (typeof item.confidence === "number" && Number.isFinite(item.confidence)) {
      bits.push(`${Math.round(item.confidence * 100)}%`);
    }
    return bits.join(" · ");
  }
  function subtitleFor(item) {
    if (item._type === "style") return norm(item.expression?.situation);
    if (item._type === "growth") return "成长记录中提出的进展，等待你核实";
    return "每日复盘中整理出的建议，等待你确认";
  }
  function quoteFor(item, itemTitle, itemSubtitle) {
    const raw = item._type === "growth" ? norm(item.progress_note)
      : item._type === "style" ? norm(item.proposal)
        : norm(item.next_action);
    if (!raw || sameText(raw, itemTitle) || sameText(raw, itemSubtitle)) return "";
    return raw;
  }
  function observationFor(item, itemTitle, itemSubtitle, itemQuote) {
    const raw = norm(item.observation);
    if (!raw || sameText(raw, itemTitle) || sameText(raw, itemSubtitle) || sameText(raw, itemQuote)) return "";
    return raw;
  }
  function conditionChips(item) {
    const applicability = item.applicability;
    if (!applicability || typeof applicability !== "object") return [];
    const values = value => (Array.isArray(value) ? value : []).filter(entry => typeof entry === "string" && entry);
    const labels = (entries, map) => entries.map(entry => map[entry] || entry).join("、");
    const chips = [];
    const channels = values(applicability.channels);
    if (channels.length) chips.push(["频道", labels(channels, CHANNEL)]);
    const relationships = values(applicability.relationship_stages);
    if (relationships.length) chips.push(["关系", labels(relationships, RELATIONSHIP)]);
    const emotions = values(applicability.emotion_gates);
    if (emotions.length) chips.push(["情绪", labels(emotions, EMOTION)]);
    const intent = typeof applicability.intent === "string" ? applicability.intent : "";
    if (intent) chips.push(["意图", INTENT[intent] || intent]);
    const keywords = values(applicability.keywords);
    if (keywords.length) chips.push(["关键词", keywords.join("、")]);
    return chips;
  }
  function evidence(item) {
    if (Array.isArray(item.evidence)) return item.evidence;
    return [];
  }
  function actions(item) {
    if (item._type === "growth") return item.actions || [];
    if (item._type === "style") return item.actions || [];
    return item.actions || [];
  }
  function labelAction(item, action) {
    if (item._type === "self" && action === "accept") return "采纳指导";
    return ACTION[action] || "处理";
  }
  function render(options = {}) {
    if (!root || !data) return;
    const savedScroll = options.resetScroll ? undefined : queueNode()?.scrollTop;
    const visible = data.items.filter(item => data.filter === "all" || item._type === data.filter);
    if(!visible.some(item=>keyFor(item)===data.selected))data.selected=visible[0]?._key||"";
    const current = selectedItem();
    const currentIndex = current ? visible.findIndex(item => keyFor(item) === keyFor(current)) : -1;
    const position = currentIndex + 1;
    const counts = {all:data.items.length,growth:data.items.filter(item=>item._type==="growth").length,
      style:data.items.filter(item=>item._type==="style").length,self:data.items.filter(item=>item._type==="self").length};
    const selectedVisible=visible.filter(item=>data.checked.has(keyFor(item)));
    const bulkAccept=selectedVisible.filter(item=>actions(item).includes(item._type==="style"?"adopt":"accept")).length;
    const bulkReject=selectedVisible.filter(item=>actions(item).includes("reject")).length;
    const list = visible.length ? visible.map((item,index) => {
      const key = keyFor(item);
      const itemTitle = title(item);
      const selected = key === data.selected;
      return `<div class="lh-review-row-wrap${selected?" is-selected":""}">
        <input class="lh-review-check" type="checkbox" data-review-check="${e(key)}" aria-label="选择${e(itemTitle)}"${data.checked.has(key)?" checked":""}${data.writing?" disabled":""}>
        <span class="lh-review-index">${String(index+1).padStart(2,"0")}</span>
        <button type="button" class="lh-review-row${selected?" is-selected":""}"
          data-review-select="${e(key)}" aria-pressed="${selected}">
          <span class="lh-review-row-top">
            <span class="lh-review-type is-${item._type}">${typeFor(item)}</span>
            <time class="lh-review-row-time">${e(when(item.created_at))}</time>
          </span>
          <strong class="lh-review-row-title">${e(itemTitle)}</strong>
          <small class="lh-review-row-source">${e(item._source.label)} · ${e(sourceKindLabel(item))}</small>
        </button></div>`;
    }).join("")
      : `<div class="lh-review-empty">${data.loading?"正在读取…":"没有待处理记录"}</div>`;
    let detail = `<div class="lh-review-detail-empty">选择一条记录</div>`;
    if (current) {
      const currentKey = keyFor(current);
      const itemTitle = title(current);
      const subtitle = subtitleFor(current);
      const quote = quoteFor(current, itemTitle, subtitle);
      const observation = observationFor(current, itemTitle, subtitle, quote);
      const metric = styleMetricLabel(current);
      const proof = evidence(current);
      const conditions = conditionChips(current);
      const avoid = current._type === "style" ? norm(current.expression?.avoid) : "";
      const confirmation = data.confirming?.key === currentKey
        ? `<div class="lh-review-confirm" role="alertdialog"><span>确认${e(labelAction(current,data.confirming.action))}？</span>
          <button type="button" data-review-confirm="yes"${data.writing?" disabled":""}>确认</button><button type="button" data-review-confirm="no">取消</button></div>` : "";
      const detailActions = [...actions(current)].sort((left,right)=>(left==="reject"?0:1)-(right==="reject"?0:1))
        .map(action => action === "reject"
          ? `<button type="button" class="lh-review-reject" data-review-stage="reject" data-review-key="${e(currentKey)}" ${data.writing?"disabled":""}>${labelAction(current,action)}</button>`
          : `<button type="button" class="lh-command is-primary"
          data-review-stage="${e(action)}" data-review-key="${e(currentKey)}" ${data.writing?"disabled":""}>${labelAction(current,action)}</button>`).join("");
      const metaBits = [];
      if (current.last_injected_at) metaBits.push(`最近注入 ${when(current.last_injected_at)}`);
      if (current.decided_at) metaBits.push(`决定于 ${when(current.decided_at)}`);
      if (Number(current.injections) > 0) metaBits.push(`注入 ${Number(current.injections)} 次`);
      const meta = metaBits.join(" · ");
      const source = current._source.label;
      const canPrev = currentIndex > 0;
      const canNext = currentIndex >= 0 && currentIndex < visible.length - 1;
      detail = `<article class="lh-review-detail">
        <div class="lh-review-detail-top">
          <header class="lh-review-detail-head">
            <div class="lh-review-detail-chips">
              <span class="lh-review-chip is-pending"><i></i>待批阅</span>
              <span class="lh-review-chip is-type is-${current._type}">${typeFor(current)}</span>
              ${metric?`<span class="lh-review-chip is-metric">${e(metric)}</span>`:""}
            </div>
            <span class="lh-review-position">第 ${String(position).padStart(2,"0")} / ${String(visible.length).padStart(2,"0")} 条</span>
          </header>
          <dl class="lh-review-meta-grid">
            <div><dt>归属</dt><dd>${e(source)}</dd></div>
            <div><dt>来源</dt><dd>${e(sourceKindLabel(current))}</dd></div>
            <div><dt>记录于</dt><dd>${e(when(current.created_at))}</dd></div>
          </dl>
        </div>
        <div class="lh-review-detail-body">
          <div class="lh-review-detail-title"><h2>${e(itemTitle)}</h2>${subtitle?`<p>${e(subtitle)}</p>`:""}</div>
          ${quote?`<blockquote class="lh-review-quote"><p>${e(quote)}</p></blockquote>`:""}
          ${observation?`<section class="lh-review-observation"><span>观察</span><p>${e(observation)}</p></section>`:""}
          ${avoid?`<div class="lh-review-avoid"><span>何时不用</span><p>${e(avoid)}</p></div>`:""}
          ${conditions.length?`<div class="lh-review-conditions"><span>适用条件</span><div>${conditions.map(([label,value])=>`<span class="lh-review-condition"><em>${label}</em>${e(value)}</span>`).join("")}</div></div>`:""}
          ${current._type==="style"&&current.source_kind==="shared_pack"?`<p class="lh-review-meta">分享包 · ${e(current.source_title||"导入规则")}</p>`:""}
          ${current._type==="style"&&(current.positive_feedback||current.negative_feedback)?`<p class="lh-review-meta">明确反馈 · 认可 ${e(current.positive_feedback||0)} / 不喜欢 ${e(current.negative_feedback||0)}</p>`:""}
          ${current._type==="style"&&current.review_reason?`<p class="lh-review-feedback-reason">${e(current.review_reason)}</p>`:""}
          ${current.unavailable_reason?`<p class="lh-review-unavailable">依据已不可读取，只能驳回。</p>`:""}
          ${proof.length?`<details class="lh-review-evidence"${proof.length<=3?" open":""}><summary>依据 · ${proof.length} 条</summary><ol>${proof.map(text=>`<li>${e(text)}</li>`).join("")}</ol></details>`:""}
        </div>
        <div class="lh-review-detail-bottom">
          ${confirmation}
          <footer class="lh-review-detail-foot">
            <div class="lh-review-detail-meta">${e(meta)}</div>
            <div class="lh-review-detail-actions">
              <button type="button" class="lh-review-nav-btn" data-review-prev${canPrev?"":" disabled"}><kbd>K</kbd> 上一条</button>
              <button type="button" class="lh-review-nav-btn" data-review-next${canNext?"":" disabled"}><kbd>J</kbd> 下一条</button>
              ${detailActions}
            </div>
          </footer>
        </div></article>`;
    }
    const bulkConfirm=data.confirming?.bulk?`<div class="lh-review-confirm lh-review-bulk-confirm" role="alertdialog">
      <span>确认${data.confirming.action==="accept"?"批量采纳":"批量驳回"} ${data.confirming.action==="accept"?bulkAccept:bulkReject} 条记录？</span>
      <button type="button" data-review-confirm="yes"${data.writing?" disabled":""}>确认</button><button type="button" data-review-confirm="no">取消</button></div>`:"";
    root.innerHTML = `<header class="lh-review-head"><div class="lh-review-head-copy">
        <h2>审阅中心</h2>
        <p>把陪伴里学到的分寸与进展整理成手稿，逐条核实与裁决。</p></div>
      <button type="button" class="lh-review-refresh" data-review-refresh aria-label="重新读取" title="重新读取" ${data.loading||data.writing?"disabled":""}><i data-lucide="refresh-cw" aria-hidden="true"></i><span>刷新</span></button></header>
      <div class="lh-review-toolbar"><div class="lh-review-filters" role="group" aria-label="审阅类型">
        ${FILTERS.map(([id,label])=>
          `<button type="button" data-review-filter="${id}" aria-pressed="${data.filter===id}">${label}<span>${counts[id]}</span></button>`).join("")}</div>
        <div class="lh-review-selection"><label class="lh-review-select-all"><input type="checkbox" data-review-check-all${visible.length&&selectedVisible.length===visible.length?" checked":""}${!visible.length||data.writing?" disabled":""}>全选</label>
          <span class="lh-review-selected-count">已选 ${selectedVisible.length}</span>
          <button type="button" class="lh-review-bulk-accept" data-review-bulk="accept"${!bulkAccept||data.writing?" disabled":""}>批量采纳${bulkAccept?` · ${bulkAccept}`:""}</button>
          <button type="button" class="lh-review-bulk-reject" data-review-bulk="reject"${!bulkReject||data.writing?" disabled":""}>批量驳回${bulkReject?` · ${bulkReject}`:""}</button></div></div>
      ${data.notice?`<p class="lh-inline-status ${data.error?"is-error":""}" role="status">${e(data.notice)}</p>`:""}
      ${data.errors?`<p class="lh-inline-status" role="status">部分记录归属暂时无法读取</p>`:""}
      ${bulkConfirm}<div class="lh-review-layout"><section class="lh-review-queue">
        <header class="lh-review-queue-head"><div><h3>待批阅</h3><p>按记录时间排列 · 逐条读，逐条定</p></div><span>${visible.length} / ${data.items.length}</span></header>
        <div class="lh-review-queue-list">${list}</div>
        <footer class="lh-review-queue-foot"><span>显示 ${visible.length} / ${data.items.length} 条</span>
          ${pages.some(page=>page.next)?`<button type="button" class="lh-command" data-review-more${data.loading||data.writing?" disabled":""}>加载更多</button>`:""}</footer></section>
        ${detail}</div>`;
    if (Number.isFinite(savedScroll)) {
      const node = queueNode();
      if (node) node.scrollTop = savedScroll;
    }
    window.lucide?.createIcons({attrs:{"aria-hidden":"true"}});
  }

  /* 裁决成功后就地把这条从待裁决队列里摘掉：它已经不在 pending 里了。
     随后的 refresh 会原地更新其余已加载的页，队列不会被打回第一页。 */
  function dropItem(item) {
    const key = keyFor(item);
    pages.forEach(page => {page.items = page.items.filter(row => keyFor(row) !== key);});
    data.items = data.items.filter(row => keyFor(row) !== key);
    const checked = new Set(data.checked);
    checked.delete(key);
    data.checked = checked;
    if (data.selected === key) data.selected = "";
    if (data.confirming?.key === key) data.confirming = null;
  }
  async function decide(item, action) {
    if (data.writing || !item || !actions(item).includes(action)) return;
    const current = ticket;
    data.writing = true; data.confirming = null; data.notice = ""; data.error=false; render();
    try {
      const base = item._type === "style" ? relative(item._source.replyApi) : relative(item._source.growthApi || item._source.api);
      let path, body;
      const operation_key = `learning-review-${Date.now()}-${Math.random().toString(36).slice(2,10)}`;
      if (item._type === "growth") {
        path = base + "/progress/command";
        body = {review_id:item.review_id,action,expected_revision:item.base_revision,view_token:item.view_token,operation_key};
      } else if (item._type === "style") {
        path = base + "/style/command";
        body = {signal_id:item.signal_id,action,view_token:item.view_token,operation_key};
      } else {
        path = base + "/self-review/command";
        body = {item_id:item.item_id,action,view_token:item.view_token,operation_key};
      }
      await api("POST",routeFor(item._source,"",path),body);
      if (!live(current)) return;
      /* 先取消在途读取：那份响应是裁决前的快照，回来时会把刚摘掉的行又 concat 回队列。
         同时清掉 loading，否则紧接着的 refresh 会被自己的护栏挡掉。 */
      readTicket += 1; data.loading = false;
      dropItem(item);
      data.writing = false; data.notice = action==="reject"?"已驳回。":item._type==="growth"?"已确认申报。":"已采纳。";
      await read(current, "refresh");
    } catch (error) {
      if (!live(current)) return;
      data.writing = false; data.error = true; data.notice = ERRORS[error?.code] || "操作未完成，请重新读取。"; render();
    }
  }

  async function decideBatch(action) {
    if(data.writing)return;
    const visible=data.items.filter(item=>data.filter==="all"||item._type===data.filter);
    const selected=visible.filter(item=>data.checked.has(keyFor(item)));
    const requests=selected.map(item=>({item,command:item._type==="style"?(action==="accept"?"adopt":"reject"):action}))
      .filter(({item,command})=>actions(item).includes(command));
    if(!requests.length){data.notice="所选记录没有可用的对应操作。";data.error=true;render();return;}
    const current=ticket;
    data.writing=true;data.confirming=null;data.notice="";render();
    let completed=0,failed=null;
    const decided=[];
    for(const {item,command} of requests){
      if(!live(current))return;
      try{
        const base=item._type==="style"?relative(item._source.replyApi):relative(item._source.growthApi||item._source.api);
        const operation_key=`learning-review-batch-${Date.now()}-${Math.random().toString(36).slice(2,10)}`;
        if(item._type==="growth")await api("POST",routeFor(item._source,base,"/progress/command"),{
          review_id:item.review_id,action:command,expected_revision:item.base_revision,view_token:item.view_token,operation_key});
        else if(item._type==="style")await api("POST",routeFor(item._source,base,"/style/command"),{
          signal_id:item.signal_id,action:command,view_token:item.view_token,operation_key});
        else await api("POST",routeFor(item._source,base,"/self-review/command"),{
          item_id:item.item_id,action:command,view_token:item.view_token,operation_key});
        decided.push(item);
        completed++;
      }catch(error){failed=error;break;}
    }
    if(!live(current))return;
    data.writing=false;data.checked.clear();data.confirming=null;
    /* 同上：批量裁决后先取消在途读取，再原地刷新。 */
    readTicket += 1; data.loading = false;
    decided.forEach(dropItem);
    await read(current, "refresh");
    if(!live(current))return;
    data.notice=failed?`已处理 ${completed} 条，其余未提交。${ERRORS[failed?.code]||"请刷新后核对。"}`:`已处理 ${completed} 条。`;
    data.error=!!failed;render();
  }

  function scrollSelectedIntoView() {
    if (!root?.querySelectorAll || !data?.selected) return;
    try {
      const rows = root.querySelectorAll("[data-review-select]");
      for (const row of rows) {
        if (row?.dataset?.reviewSelect === data.selected) {
          const target = row.closest?.(".lh-review-row-wrap") || row;
          target.scrollIntoView?.({block:"nearest"});
          break;
        }
      }
    } catch (_) { /* 精简测试 DOM 无需滚动 */ }
  }
  function selectStep(delta) {
    if (!data) return;
    const visible = data.items.filter(item => data.filter === "all" || item._type === data.filter);
    const index = visible.findIndex(item => keyFor(item) === data.selected);
    const nextIndex = index + delta;
    if (index < 0 || nextIndex < 0 || nextIndex >= visible.length) return;
    data.selected = keyFor(visible[nextIndex]);
    data.confirming = null;
    render();
    scrollSelectedIntoView();
  }
  function isEditableTarget(target) {
    if (!target) return false;
    const tag = String(target.tagName || "").toUpperCase();
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    if (target.isContentEditable) return true;
    const editable = target.getAttribute?.("contenteditable");
    if (editable === "" || editable === "true") return true;
    if (target.closest?.("input, textarea, select, [contenteditable=''], [contenteditable='true']")) return true;
    return false;
  }
  function onKeydown(event) {
    if (!root?.isConnected || !data || data.writing) return;
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
    if (isEditableTarget(event.target)) return;
    if (root.ownerDocument?.querySelector?.("dialog[open]")) return;
    const key = String(event.key || "").toLowerCase();
    if (key === "j") {
      event.preventDefault?.();
      selectStep(1);
    } else if (key === "k") {
      event.preventDefault?.();
      selectStep(-1);
    } else if (key === "escape" && data.confirming) {
      event.preventDefault?.();
      data.confirming = null;
      render();
    }
  }
  function onClick(event) {
    if(!data||data.writing)return;
    const filter = event.target.closest("[data-review-filter]");
    if (filter) { data.filter=filter.dataset.reviewFilter; data.confirming=null; render({resetScroll:true}); return; }
    if (event.target.closest("[data-review-refresh]")) { void read(ticket, "refresh"); return; }
    if (event.target.closest("[data-review-more]")) { void read(ticket, "append"); return; }
    if (event.target.closest("[data-review-prev]")) { selectStep(-1); return; }
    if (event.target.closest("[data-review-next]")) { selectStep(1); return; }
    const bulk=event.target.closest("[data-review-bulk]");
    if(bulk){
      const action=bulk.dataset.reviewBulk;
      const visible=data.items.filter(item=>data.filter==="all"||item._type===data.filter);
      const eligible=visible.filter(item=>data.checked.has(keyFor(item))
        &&actions(item).includes(action==="accept"?(item._type==="style"?"adopt":"accept"):"reject"));
      if(!eligible.length)return;
      data.confirming={bulk:true,action};render();return;
    }
    const select = event.target.closest("[data-review-select]");
    if (select) { data.selected=select.dataset.reviewSelect; data.confirming=null; render(); return; }
    const staged = event.target.closest("[data-review-stage]");
    if (staged) { data.confirming={key:staged.dataset.reviewKey,action:staged.dataset.reviewStage}; render(); return; }
    const confirm = event.target.closest("[data-review-confirm]");
    if (confirm) {
      if (confirm.dataset.reviewConfirm==="no") { data.confirming=null; render(); return; }
      if(data.confirming?.bulk){void decideBatch(data.confirming.action);return;}
      const item=selectedItem(), action=data.confirming?.action;
      if (item) void decide(item,action);
    }
  }
  function onChange(event){
    if(!data||data.writing)return;
    const checkbox=event.target.closest("[data-review-check]");
    if(checkbox){checkbox.checked?data.checked.add(checkbox.dataset.reviewCheck):data.checked.delete(checkbox.dataset.reviewCheck);if(data.confirming?.bulk)data.confirming=null;render();return;}
    if(event.target.closest("[data-review-check-all]")){
      const visible=data.items.filter(item=>data.filter==="all"||item._type===data.filter);
      data.checked.clear();if(event.target.checked)visible.forEach(item=>data.checked.add(keyFor(item)));if(data.confirming?.bulk)data.confirming=null;render();
    }
  }
  function mount(host, options = {}) {
    unmount(); root=host; sources=Array.isArray(options.sources)?options.sources:[];
    root?.addEventListener("click",onClick);root?.addEventListener("change",onChange);window.addEventListener?.("keydown",onKeydown);
    data={loading:false,writing:false,items:[],selected:"",checked:new Set(),filter:"all",notice:"",confirming:null,errors:0};
    const current=++ticket;
    if (sources.length) void read(current); else render();
  }
  function unmount() { ticket++;readTicket++; root?.removeEventListener("click",onClick);root?.removeEventListener("change",onChange);window.removeEventListener?.("keydown",onKeydown);root=null;sources=[];pages=[];data=null; }
  return {mount,unmount,get state(){return data;}};
})();
