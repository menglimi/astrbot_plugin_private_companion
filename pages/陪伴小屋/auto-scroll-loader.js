/* 陪伴小屋统一自动滚动加载控制器（CompanionAutoScroll）
 *
 * 为全站增量分页列表、图库与流式记录提供低耦合的 IntersectionObserver 自动加载能力：
 * - 支持页面视口（scrollRoot = null）与嵌套滚动容器（scrollRoot = HTMLElement）
 * - 节流防抖（默认 260ms）+ 在途并发锁，防止重叠请求
 * - 游标 / 条数推进校验，防止游标停滞导致死循环
 * - 连续自动加载安全阀（默认 12 次），超限暂停并提示手动继续，手动滚动后重置
 * - 失败熔断：保留已加载内容并展示温和错误提示 + 「点击重试」
 * - 全局持久化开关（默认开启，localStorage 读写全程 try/catch 并附内存兜底）
 * - 统一底部状态条：加载脉动、手动兜底按钮、全部加载统计、>=16 条自动出现「↑ 回到顶部」
 */
(function initCompanionAutoScroll(global){
  "use strict";

  const DEFAULT_STORAGE_KEY = "companion.autoload.v1";
  const DEFAULT_ROOT_MARGIN = "280px 0px";
  const DEFAULT_MIN_INTERVAL_MS = 260;
  const DEFAULT_MAX_AUTO_BURST = 12;
  const DEFAULT_SHOW_TOP_THRESHOLD = 16;

  // 沙盒 iframe 或禁用 storage 时的内存偏好兜底
  const memoryPrefs = new Map();
  // 按 storageKey 分组的活动实例回调，实现同一页面多列表开关同步
  const prefSubscribers = new Map();

  function parseStoredBool(raw){
    if(raw === "0" || raw === "false")return false;
    if(raw === "1" || raw === "true")return true;
    return null;
  }

  function readPref(storageKey){
    const key = storageKey || DEFAULT_STORAGE_KEY;
    try{
      if(global.localStorage){
        const parsed = parseStoredBool(global.localStorage.getItem(key));
        if(parsed !== null){
          memoryPrefs.set(key, parsed);
          return parsed;
        }
      }
    }catch{
      // Opaque origin / SecurityError：静默回落至内存缓存
    }
    if(memoryPrefs.has(key))return memoryPrefs.get(key);
    return true;
  }

  function writePref(storageKey, enabled){
    const key = storageKey || DEFAULT_STORAGE_KEY;
    const value = Boolean(enabled);
    memoryPrefs.set(key, value);
    try{
      if(global.localStorage){
        global.localStorage.setItem(key, value ? "1" : "0");
      }
    }catch{
      // 沙盒无写入权限时仅保留内存状态
    }
    notifyPrefSubscribers(key, value);
  }

  function notifyPrefSubscribers(storageKey, enabled){
    const listeners = prefSubscribers.get(storageKey);
    if(!listeners || !listeners.size)return;
    for(const listener of Array.from(listeners)){
      try{ listener(enabled); }catch{}
    }
  }

  function subscribePref(storageKey, listener){
    const key = storageKey || DEFAULT_STORAGE_KEY;
    let set = prefSubscribers.get(key);
    if(!set){
      set = new Set();
      prefSubscribers.set(key, set);
    }
    set.add(listener);
    return () => {
      const current = prefSubscribers.get(key);
      if(!current)return;
      current.delete(listener);
      if(!current.size)prefSubscribers.delete(key);
    };
  }

  function readVal(source, fallback){
    if(typeof source === "function"){
      try{ return source(); }catch{ return fallback; }
    }
    return source === undefined ? fallback : source;
  }

  function escapeHtml(text){
    return String(text ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function serializeCursor(value){
    if(value === undefined)return "__undef__";
    if(value === null)return "__null__";
    if(typeof value === "string" || typeof value === "number" || typeof value === "boolean"){
      return `${typeof value}:${String(value)}`;
    }
    try{ return `json:${JSON.stringify(value)}`; }
    catch{ return `str:${String(value)}`; }
  }

  function attrsToHtml(attrs){
    if(!attrs || typeof attrs !== "object")return "";
    return Object.entries(attrs)
      .filter(([name, value]) => name && value !== false && value !== null && value !== undefined)
      .map(([name, value]) => {
        const safeName = String(name).replace(/[^a-zA-Z0-9_:-]/g, "");
        if(!safeName)return "";
        return value === "" || value === true ? safeName : `${safeName}="${escapeHtml(value)}"`;
      })
      .filter(Boolean)
      .join(" ");
  }

  function prefersReducedMotion(){
    try{
      const motion = global.document?.documentElement?.dataset?.motion;
      if(motion === "off" || motion === "low")return true;
      if(typeof global.matchMedia === "function" && global.matchMedia("(prefers-reduced-motion: reduce)").matches){
        return true;
      }
    }catch{}
    return false;
  }

  function supportsIntersectionObserver(){
    return typeof global.IntersectionObserver === "function";
  }

  function nowMs(){
    return Date.now();
  }

  function mount(rawOptions){
    const options = rawOptions && typeof rawOptions === "object" ? rawOptions : {};
    const storageKey = options.storageKey || DEFAULT_STORAGE_KEY;
    const rootMargin = typeof options.rootMargin === "string" && options.rootMargin.trim()
      ? options.rootMargin.trim()
      : DEFAULT_ROOT_MARGIN;
    const minIntervalMs = Number.isFinite(options.minIntervalMs) && options.minIntervalMs >= 0
      ? Number(options.minIntervalMs)
      : DEFAULT_MIN_INTERVAL_MS;
    const maxAutoBurst = Number.isFinite(options.maxAutoBurst) && options.maxAutoBurst > 0
      ? Math.floor(Number(options.maxAutoBurst))
      : DEFAULT_MAX_AUTO_BURST;
    const showTopThreshold = Number.isFinite(options.showTopThreshold) && options.showTopThreshold >= 0
      ? Math.floor(Number(options.showTopThreshold))
      : DEFAULT_SHOW_TOP_THRESHOLD;
    const itemLabel = typeof options.itemLabel === "string" && options.itemLabel.trim()
      ? options.itemLabel.trim()
      : "项";
    const showToggle = options.showToggle !== false;
    const sentinelAttr = typeof options.sentinelAttr === "string"
      ? options.sentinelAttr.replace(/[^a-zA-Z0-9_:-]/g, "")
      : "";

    let host = options.host || null;
    let scrollRoot = options.scrollRoot || null;
    let enabled = readPref(storageKey);
    let destroyed = false;
    let inFlight = false;
    let burstCount = 0;
    let burstPaused = false;
    let stalled = false;
    let internalError = false;
    let internalErrorText = "";
    let lastTriggerAt = 0;
    let intersecting = false;
    let debounceTimer = null;
    let observer = null;
    let observedSentinel = null;
    let observedHost = null;
    let observerScrollRoot = undefined;
    const loadScrollSettleMs = options.loadScrollSettleMs === undefined
      ? 150
      : Math.max(0, Number(options.loadScrollSettleMs) || 0);
    let boundHost = null;
    let boundScrollTarget = null;
    let ignoringProgrammaticScroll = false;
    let programmaticScrollTimer = null;
    let ignoringLoadScroll = false;
    let loadScrollGuardTimer = null;

    function readHasMore(){
      return Boolean(readVal(options.hasMore, false));
    }

    function readExternalLoading(){
      return Boolean(readVal(options.isLoading, false));
    }

    function readIsLoading(){
      return inFlight || readExternalLoading();
    }

    function readExternalError(){
      return Boolean(readVal(options.hasError, false));
    }

    function readHasError(){
      return internalError || readExternalError();
    }

    function readLoadedCount(){
      if(options.loadedCount !== undefined){
        const value = Number(readVal(options.loadedCount, 0));
        return Number.isFinite(value) && value >= 0 ? value : 0;
      }
      if(options.totalCount !== undefined){
        const value = Number(readVal(options.totalCount, 0));
        return Number.isFinite(value) && value >= 0 ? value : 0;
      }
      return 0;
    }

    function readTotalCount(){
      if(options.totalCount !== undefined){
        const value = Number(readVal(options.totalCount, null));
        if(Number.isFinite(value) && value >= 0)return value;
      }
      return readLoadedCount();
    }

    function readErrorMessage(){
      const externalMsg = readVal(options.errorMessage, "");
      if(typeof externalMsg === "string" && externalMsg.trim())return externalMsg.trim();
      if(internalErrorText)return internalErrorText;
      return "加载更多失败，已保留当前已加载内容";
    }

    function hasCursorTracker(){
      return typeof options.cursor === "function";
    }

    function hasCountTracker(){
      return typeof options.loadedCount === "function" || typeof options.totalCount === "function";
    }

    function canAutoLoad(){
      return !destroyed
        && enabled
        && supportsIntersectionObserver()
        && readHasMore()
        && !readIsLoading()
        && !readHasError()
        && !stalled
        && !burstPaused
        && burstCount < maxAutoBurst;
    }

    function clearDebounce(){
      if(debounceTimer !== null){
        global.clearTimeout(debounceTimer);
        debounceTimer = null;
      }
    }

    function disconnectObserver(){
      if(observer){
        try{ observer.disconnect(); }catch{}
        observer = null;
      }
      observedSentinel = null;
      observedHost = null;
      observerScrollRoot = undefined;
      intersecting = false;
    }

    function unbindHostListeners(){
      if(boundHost){
        boundHost.removeEventListener?.("click", handleHostClick);
        boundHost.removeEventListener?.("change", handleHostChange);
        boundHost = null;
      }
    }

    function unbindScrollListeners(){
      if(boundScrollTarget){
        boundScrollTarget.removeEventListener?.("scroll", handleScrollEvent);
        boundScrollTarget.removeEventListener?.("wheel", handleDirectUserScroll);
        boundScrollTarget.removeEventListener?.("touchmove", handleDirectUserScroll);
        boundScrollTarget = null;
      }
    }

    function bindScrollListeners(){
      if(!host){
        unbindScrollListeners();
        return;
      }
      const target = scrollRoot || global;
      if(target === boundScrollTarget)return;
      unbindScrollListeners();
      if(target && typeof target.addEventListener === "function"){
        target.addEventListener("scroll", handleScrollEvent, { passive: true });
        target.addEventListener("wheel", handleDirectUserScroll, { passive: true });
        target.addEventListener("touchmove", handleDirectUserScroll, { passive: true });
        boundScrollTarget = target;
      }
    }

    function resetBurstAfterUserScroll(){
      if(burstCount === 0 && !burstPaused)return;
      const wasPaused = burstPaused;
      burstCount = 0;
      burstPaused = false;
      if(wasPaused){
        render();
        syncObserver();
      }
    }

    function handleScrollEvent(){
      if(destroyed || ignoringProgrammaticScroll || ignoringLoadScroll || inFlight)return;
      resetBurstAfterUserScroll();
    }

    function handleDirectUserScroll(){
      if(destroyed || ignoringProgrammaticScroll)return;
      resetBurstAfterUserScroll();
    }

    function scrollToTop(){
      const behavior = prefersReducedMotion() ? "auto" : "smooth";
      ignoringProgrammaticScroll = true;
      if(programmaticScrollTimer !== null){
        global.clearTimeout(programmaticScrollTimer);
      }
      programmaticScrollTimer = global.setTimeout(() => {
        ignoringProgrammaticScroll = false;
        programmaticScrollTimer = null;
      }, 450);

      try{
        if(scrollRoot){
          if(typeof scrollRoot.scrollTo === "function"){
            scrollRoot.scrollTo({ top: 0, behavior });
          }else{
            scrollRoot.scrollTop = 0;
          }
          return;
        }
        const customTarget = readVal(options.scrollTopTarget, null);
        if(customTarget && typeof customTarget.scrollIntoView === "function"){
          customTarget.scrollIntoView({ behavior, block: "start" });
          return;
        }
        if(typeof global.scrollTo === "function"){
          global.scrollTo({ top: 0, behavior });
        }
      }catch{}
    }

    function scheduleAutoLoad(){
      if(!canAutoLoad())return;
      const elapsed = lastTriggerAt > 0 ? nowMs() - lastTriggerAt : minIntervalMs;
      if(elapsed >= minIntervalMs){
        clearDebounce();
        triggerLoad(false);
        return;
      }
      if(debounceTimer !== null)return;
      debounceTimer = global.setTimeout(() => {
        debounceTimer = null;
        if(intersecting && canAutoLoad()){
          triggerLoad(false);
        }
      }, Math.max(0, minIntervalMs - elapsed));
    }

    function handleIntersect(entries){
      if(destroyed)return;
      const hit = Array.isArray(entries) && entries.some((entry) => Boolean(entry && entry.isIntersecting));
      intersecting = hit;
      if(!hit){
        clearDebounce();
        return;
      }
      if(canAutoLoad()){
        scheduleAutoLoad();
      }
    }

    function syncObserver(){
      if(destroyed || !host || !enabled || !supportsIntersectionObserver() || !readHasMore() || readHasError() || stalled || burstPaused){
        disconnectObserver();
        return;
      }
      const sentinel = typeof host.querySelector === "function"
        ? host.querySelector("[data-casl-sentinel]")
        : null;
      if(!sentinel){
        disconnectObserver();
        return;
      }
      const nextRoot = scrollRoot || null;
      if(observer && observerScrollRoot === nextRoot && observedSentinel === sentinel){
        return;
      }
      disconnectObserver();
      try{
        observer = new global.IntersectionObserver(handleIntersect, {
          root: nextRoot,
          rootMargin,
        });
        observerScrollRoot = nextRoot;
        observedSentinel = sentinel;
        observedHost = host;
        observer.observe(sentinel);
      }catch{
        disconnectObserver();
      }
    }

    async function triggerLoad(manual){
      if(destroyed)return false;
      const isManual = Boolean(manual);
      if(inFlight || readExternalLoading())return false;

      const isRetry = isManual && (readHasError() || stalled);
      if(isManual){
        burstCount = 0;
        burstPaused = false;
        stalled = false;
        internalError = false;
        internalErrorText = "";
        clearDebounce();
      }else{
        if(!canAutoLoad())return false;
        if(burstCount >= maxAutoBurst){
          burstPaused = true;
          render();
          syncObserver();
          return false;
        }
      }

      const callback = isRetry && typeof options.onRetry === "function"
        ? options.onRetry
        : options.onLoadMore;
      if(typeof callback !== "function")return false;

      const trackCursor = hasCursorTracker();
      const trackCount = hasCountTracker();
      const beforeCursor = trackCursor ? serializeCursor(readVal(options.cursor, undefined)) : null;
      const beforeCount = trackCount ? readLoadedCount() : null;

      inFlight = true;
      ignoringLoadScroll = true;
      if(loadScrollGuardTimer !== null){
        global.clearTimeout(loadScrollGuardTimer);
        loadScrollGuardTimer = null;
      }
      lastTriggerAt = nowMs();
      if(!isManual){
        burstCount += 1;
      }
      render();

      let threw = false;
      try{
        await callback();
      }catch(error){
        threw = true;
        internalError = true;
        internalErrorText = typeof error?.message === "string" && error.message.trim()
          ? error.message.trim()
          : "";
      }finally{
        inFlight = false;
        if(!destroyed){
          if(loadScrollGuardTimer !== null){
            global.clearTimeout(loadScrollGuardTimer);
          }
          if(loadScrollSettleMs > 0){
            loadScrollGuardTimer = global.setTimeout(() => {
              ignoringLoadScroll = false;
              loadScrollGuardTimer = null;
            }, loadScrollSettleMs);
          }else{
            ignoringLoadScroll = false;
          }
        }
      }

      if(destroyed)return false;

      const hasErr = threw || readExternalError();
      const stillMore = readHasMore();

      if(!hasErr && stillMore && (trackCursor || trackCount)){
        const afterCursor = trackCursor ? serializeCursor(readVal(options.cursor, undefined)) : null;
        const afterCount = trackCount ? readLoadedCount() : null;
        const cursorChanged = trackCursor && afterCursor !== beforeCursor;
        const countIncreased = trackCount && Number.isFinite(beforeCount) && Number.isFinite(afterCount) && afterCount > beforeCount;
        if(!cursorChanged && !countIncreased){
          stalled = true;
        }
      }

      if(!isManual && !hasErr && !stalled && stillMore && burstCount >= maxAutoBurst){
        burstPaused = true;
      }

      render();
      syncObserver();
      return !hasErr && !stalled;
    }

    function handleHostClick(event){
      if(destroyed)return;
      const target = event?.target?.closest?.("[data-casl-action]");
      if(!target)return;
      const action = target.getAttribute("data-casl-action");
      if(action === "more" || action === "retry"){
        event.preventDefault?.();
        event.stopPropagation?.();
        triggerLoad(true);
      }else if(action === "top"){
        event.preventDefault?.();
        event.stopPropagation?.();
        scrollToTop();
      }
    }

    function handleHostChange(event){
      if(destroyed)return;
      const target = event?.target?.closest?.("[data-casl-toggle]");
      if(!target)return;
      event.stopPropagation?.();
      writePref(storageKey, Boolean(target.checked));
    }

    function bindHostListeners(){
      if(!host){
        unbindHostListeners();
        return;
      }
      if(host === boundHost)return;
      unbindHostListeners();
      if(typeof host.addEventListener === "function"){
        host.addEventListener("click", handleHostClick);
        host.addEventListener("change", handleHostChange);
        boundHost = host;
      }
    }

    function render(){
      if(destroyed || !host)return;
      const more = readHasMore();
      const loading = readIsLoading();
      const errored = readHasError();
      const loaded = readLoadedCount();
      const total = readTotalCount();
      const ioSupported = supportsIntersectionObserver();
      const manualLabel = typeof options.manualButtonLabel === "function"
        ? String(readVal(options.manualButtonLabel, "加载更多") || "加载更多")
        : String(options.manualButtonLabel || "加载更多");
      const manualAttrs = attrsToHtml(
        typeof options.manualButtonAttrs === "function"
          ? readVal(options.manualButtonAttrs, null)
          : options.manualButtonAttrs
      );
      const extraActions = typeof options.extraActionsHtml === "function"
        ? String(readVal(options.extraActionsHtml, "") || "")
        : String(options.extraActionsHtml || "");

      // 空状态且无更多/无加载/无错误时保持干净，避免打扰各页既有空状态文案
      if(!more && !loading && !errored && loaded <= 0){
        host.innerHTML = "";
        return;
      }

      let state = "idle";
      if(loading)state = "loading";
      else if(errored)state = "error";
      else if(stalled)state = "stalled";
      else if(burstPaused)state = "paused";
      else if(!more)state = "complete";
      else if(!ioSupported)state = "fallback";
      else if(!enabled)state = "manual";

      const showSentinel = more && enabled && ioSupported && !errored && !stalled && !burstPaused;
      const sentinelHtml = showSentinel
        ? `<div class="casl-sentinel" data-casl-sentinel${sentinelAttr ? ` ${sentinelAttr}` : ""} aria-hidden="true"></div>`
        : "";

      let statusHtml = "";
      let controlsHtml = "";

      if(state === "loading"){
        statusHtml = `<span class="casl-status is-loading" role="status" aria-live="polite" aria-busy="true">`
          + `<span class="casl-dots" aria-hidden="true"><i></i><i></i><i></i></span>`
          + `<span>正在加载更多…</span></span>`;
      }else if(state === "error"){
        statusHtml = `<span class="casl-status is-error" role="status" aria-live="polite">${escapeHtml(readErrorMessage())}</span>`;
        controlsHtml = `<button class="casl-btn is-retry" type="button" data-casl-action="retry"${manualAttrs ? ` ${manualAttrs}` : ""}>点击重试</button>`;
      }else if(state === "stalled"){
        statusHtml = `<span class="casl-status is-warn" role="status" aria-live="polite">暂未读取到新的内容，已暂停自动加载</span>`;
        controlsHtml = `<button class="casl-btn" type="button" data-casl-action="retry"${manualAttrs ? ` ${manualAttrs}` : ""}>点击重试</button>`;
      }else if(state === "paused"){
        statusHtml = `<span class="casl-status is-warn" role="status" aria-live="polite">已连续自动加载 ${burstCount} 次，已暂停</span>`;
        controlsHtml = `<button class="casl-btn" type="button" data-casl-action="more"${manualAttrs ? ` ${manualAttrs}` : ""}>点击继续加载</button>`;
      }else if(state === "complete"){
        const summaryCount = total > loaded ? `${loaded} / ${total}` : `${total || loaded}`;
        statusHtml = `<span class="casl-status is-complete" role="status">已加载全部 ${escapeHtml(summaryCount)} ${escapeHtml(itemLabel)}</span>`;
        if(loaded >= showTopThreshold){
          controlsHtml = `<button class="casl-btn is-top" type="button" data-casl-action="top">↑ 回到顶部</button>`;
        }
      }else if(state === "fallback"){
        statusHtml = `<span class="casl-status">当前环境不支持自动滚动</span>`;
        controlsHtml = `<button class="casl-btn" type="button" data-casl-action="more"${manualAttrs ? ` ${manualAttrs}` : ""}>${escapeHtml(manualLabel)}</button>`;
      }else if(state === "manual"){
        statusHtml = `<span class="casl-status">自动滚动加载已关闭</span>`;
        controlsHtml = `<button class="casl-btn" type="button" data-casl-action="more"${manualAttrs ? ` ${manualAttrs}` : ""}>${escapeHtml(manualLabel)}</button>`;
      }else{
        // state === "idle"（自动滚动已开启且还有更多）：保留次级手动按钮供键盘操作与兼容兜底
        statusHtml = `<span class="casl-status">向下滚动自动加载更多</span>`;
        controlsHtml = `<button class="casl-btn is-subtle" type="button" data-casl-action="more"${manualAttrs ? ` ${manualAttrs}` : ""}>${escapeHtml(manualLabel)}</button>`;
      }

      const toggleHtml = showToggle && ioSupported
        ? `<label class="casl-toggle"><input type="checkbox" data-casl-toggle${enabled ? " checked" : ""}><span>自动滚动加载</span></label>`
        : "";

      host.innerHTML = `${sentinelHtml}<div class="casl-bar" data-casl-state="${state}">`
        + `<div class="casl-main">${statusHtml}</div>`
        + `<div class="casl-actions">${controlsHtml}${extraActions}${toggleHtml}</div>`
        + `</div>`;
    }

    function onExternalPrefChange(nextEnabled){
      if(destroyed)return;
      enabled = Boolean(nextEnabled);
      if(enabled){
        burstCount = 0;
        burstPaused = false;
      }else{
        clearDebounce();
      }
      if(typeof options.onToggle === "function"){
        try{ options.onToggle(enabled); }catch{}
      }
      render();
      syncObserver();
    }

    const unsubscribePref = subscribePref(storageKey, onExternalPrefChange);

    function handleWindowStorage(event){
      if(destroyed || !event || event.key !== storageKey)return;
      const parsed = parseStoredBool(event.newValue);
      if(parsed !== null && parsed !== enabled){
        memoryPrefs.set(storageKey, parsed);
        onExternalPrefChange(parsed);
      }
    }

    if(typeof global.addEventListener === "function"){
      global.addEventListener("storage", handleWindowStorage);
    }

    function refresh(nextHost, nextScrollRoot){
      if(destroyed)return;
      if(nextHost !== undefined){
        host = nextHost;
      }
      if(nextScrollRoot !== undefined){
        scrollRoot = nextScrollRoot;
      }
      if(!readExternalError() && !inFlight){
        // 外部状态清除了错误（例如切换筛选或重试后重新加载）时同步放开内部错误锁
        if(options.hasError !== undefined && internalError && !readExternalError()){
          internalError = false;
          internalErrorText = "";
        }
      }
      bindHostListeners();
      bindScrollListeners();
      render();
      syncObserver();
    }

    function reset(){
      if(destroyed)return;
      burstCount = 0;
      burstPaused = false;
      stalled = false;
      internalError = false;
      internalErrorText = "";
      lastTriggerAt = 0;
      clearDebounce();
      render();
      syncObserver();
    }

    function setEnabled(next){
      writePref(storageKey, Boolean(next));
    }

    function isEnabled(){
      return enabled;
    }

    function getState(){
      return {
        destroyed,
        enabled,
        inFlight,
        loading: readIsLoading(),
        hasMore: readHasMore(),
        hasError: readHasError(),
        burstCount,
        burstPaused,
        stalled,
        intersecting,
      };
    }

    function destroy(){
      if(destroyed)return;
      destroyed = true;
      clearDebounce();
      if(programmaticScrollTimer !== null){
        global.clearTimeout(programmaticScrollTimer);
        programmaticScrollTimer = null;
      }
      if(loadScrollGuardTimer !== null){
        global.clearTimeout(loadScrollGuardTimer);
        loadScrollGuardTimer = null;
      }
      ignoringProgrammaticScroll = false;
      ignoringLoadScroll = false;
      disconnectObserver();
      unbindHostListeners();
      unbindScrollListeners();
      unsubscribePref();
      if(typeof global.removeEventListener === "function"){
        global.removeEventListener("storage", handleWindowStorage);
      }
      host = null;
      scrollRoot = null;
    }

    // 初次挂载
    refresh(host, scrollRoot);

    return {
      refresh,
      reset,
      destroy,
      setEnabled,
      isEnabled,
      triggerLoad,
      getState,
    };
  }

  global.CompanionAutoScroll = {
    DEFAULT_STORAGE_KEY,
    mount,
    isEnabled: readPref,
    setEnabled(enabled, storageKey){
      writePref(storageKey || DEFAULT_STORAGE_KEY, Boolean(enabled));
    },
  };
})(typeof window !== "undefined" ? window : globalThis);
