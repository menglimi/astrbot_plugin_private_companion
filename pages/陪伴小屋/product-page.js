/* Navigation and status for capabilities owned by the V2 plugin. */
"use strict";
/* Mobile dashboards are often served over plain HTTP, where crypto.randomUUID
   is not exposed.  Every editor uses it for idempotency keys; without this
   fallback a save can throw after marking the editor busy and leave it stuck. */
(() => {
  const target = globalThis.crypto;
  if (!target || typeof target.randomUUID === "function") return;
  const randomUUID = () => {
    const bytes = new Uint8Array(16);
    if (typeof target.getRandomValues === "function") target.getRandomValues(bytes);
    else for (let index = 0; index < bytes.length; index++) bytes[index] = Math.floor(Math.random() * 256);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  };
  try { Object.defineProperty(target, "randomUUID", {value: randomUUID, configurable: true}); }
  catch { target.randomUUID = randomUUID; }
})();
window.CompanionProduct = (() => {
  const HOST = "astrbot_plugin_private_companion", PREFIX = "/" + HOST + "/";
  const bundledExtensions = {content:"content", reality:"reality", together:"together", game:"game", live:"live", image:"image", screen:"companion.screen.native"};
  const e = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const labels = {connected:"已连接", installed:"已安装 · 未上报状态", missing:"未安装", disabled:"已停用",
    waiting:"已安装 · 尚未就绪", degraded:"状态读取失败 · 可重新检查", conflict:"重复安装", stopped:"插件已停止", unknown:"状态未知",
    unassembled:"未装配", assembled:"已装配"};
  const states = {planned:"待开始", active:"进行中", paused:"已暂停", completed:"已完成", cancelled:"已取消",
    written:"已保存", proposed:"待确认", achieved:"已达成", retired:"已归档", running:"运行中", new:"尚未启动", stopped:"已停止"};
  const errors = {staging_not_initialized:"角色数据尚未就绪，请在「账号与会话」保存角色映射；首次设置已保留，已保存可重新读取状态。", staging_host_terminated:"角色已停止，重新打开插件页面即可恢复。",
    dashboard_identity_required:"请从已登录的 AstrBot 管理界面打开陪伴小屋。", core_access_required:"当前账号没有管理权限。",
    perception_request_invalid:"感知设置内容无效，请核对当前设置后再保存。",
    perception_provider_unavailable:"感知服务尚未就绪，请核对模块运行状态。",
    perception_save_failed:"感知设置未能完成保存，请刷新核对。",
    integration_request_invalid:"家具清单读取请求无效，请重新读取状态。",
    bridge_timeout:"连接超时，请刷新核对。", invalid_response:"数据不完整，请刷新核对。", scope_changed:"对象已切换，请重新打开工作区。",
    /* labels 是「这一项现在能不能用」，不是开发进度。
     写「已接入」的那种说法回答的是「这一块做到哪一步了」，用户不关心；
     「状态读取正常 / 适配器尚未加载」才是他打开页面想知道的。 */
  /* 桥没接上。这句原先**不在表里**，于是它落进 fail() 的兜底
       「暂不可读取，请刷新核对。」，再套一层显示成「连接失败，请刷新核对。」
       ——一句什么都没说的提示，用户既不知道是什么没接上，也不知道下一步做什么。
       现在说清三件事：缺的是页面桥、通常是宿主页面没加载到、可以重试。 */
    bridge_unavailable:"页面还没有接上 AstrBot。若这是从管理界面打开的插件页，刷新一次通常就好；若反复出现，请确认插件已启用。",
    /* 首次设置：每个码说明「卡在哪一步、怎么办」，而不是把后端原因抛给用户。 */
    setup_request_invalid:"这次操作缺少必要的信息，请重新走一遍当前步骤。",
    setup_targets_failed:"没能读出可用的会话对象，请确认 Bot 已登录并刷新。",
    setup_start_failed:"启动失败，请确认目标会话与模型设置已经填好。",
    setup_connect_failed:"连接没有完成，请检查上面的提示后重试。",
    setup_status_unavailable:"读取不到设置进度，请重新打开插件页面。",
    setup_conversation_unavailable:"还没有跟它说过话，先在这个对象的私聊里发一条消息。",
    setup_test_confirmation_invalid:"测试记录已变化，请刷新后确认收到的是本次测试消息。",
    setup_test_non_replayable:"这次测试已经发送过，请在收到后确认；本次不会重发。",
    /* 引导没走完和「测试已经跑过」是两件无关的事：前者要做的是去补步骤，
       后者要做的才是去确认已经发出去的那条。少了这一行，新码会落进
       fail() 的兜底「暂不可读取，请刷新核对。」——用户会以为页面读不到数据。 */
    setup_steps_incomplete:"还有引导步骤没有完成，请先完成（或跳过）剩下的步骤，再做这次测试。",
    staging_delivery_not_enabled:"这个运行实例没有可用的 OneBot 私聊通道。请核对绑定的 Bot 账号、私聊对象和人格。",
    staging_delivery_window_invalid:"当前 OneBot 投递通道配置无效，请核对账号与目标。",
    staging_delivery_profile_required:"这个窗口的角色配置尚未就绪，请重新读取账号与会话。",
    staging_delivery_session_active:"当前运行实例正在接收消息，请先停止再修改目标。",
    staging_delivery_expiry_invalid:"角色配置无效，请重新读取连接状态。",
    staging_delivery_unauthorized:"该目标没有可用的发送通道，请核对账号与目标配置。",
    staging_delivery_authorization_failed:"发送通道校验失败，请检查目标与会话。",
    authorization_denied:"当前关系设置不允许在这个会话中回复。",
    authorization_expired:"当前关系权限已失效，请检查关系与权限设置。",
    authorization_not_active:"当前关系权限尚未生效，请检查关系与权限设置。",
    authorization_revoked:"当前关系设置已收回相关权限。",
    scope_not_granted:"当前关系没有允许这项操作。",
    staging_delivery_grant_changed:"当前目标或关系权限发生变化，请重新读取状态。",
    staging_review_only:"当前运行实例未开放正式消息接收。",
    staging_session_mode_conflict:"当前运行实例正在接收消息，请先停止后再切换模式。",
    staging_session_still_stopping:"上一会话仍在收尾，请稍后重新完成连接。",
    capability_descriptor_conflict:"拓展的能力声明与保留版本冲突，请更新扩展的包版本后重新确认启用。",
    capability_revision_conflict:"拓展状态已被其他操作更新，已重新读取当前状态。",
    capability_not_installed:"这件拓展尚未装配，请刷新家具状态后重试。",
    capability_not_active:"这件拓展当前不能暂停，请刷新后核对状态。",
    capability_not_paused:"这件拓展当前不能恢复，请刷新后核对状态。",
    capability_not_discardable:"这件拓展当前不能卸载，请刷新后核对状态。",
    integration_extension_unavailable:"V2 内置拓展暂不可用，请重新读取家具状态。",
    integration_lifecycle_request_invalid:"拓展操作无效，请刷新后重试。",
    host_not_running:"角色还没开始接收消息，请先点「开始连接」。",
    staging_preview_disabled:"当前运行实例只保留正式投递确认，没有可查看的草稿。",
    staging_draft_unavailable:"那一条回复已经失效，请再发一条消息。",
    staging_profile_conflict:"固定运行实例的绑定发生变化。请检查账号、私聊对象或人格；模型参数变化不会阻止连接。",
    staging_settings_invalid:"运行设置无效，请核对账号、私聊对象、人格与模型。",
    staging_settings_incomplete:"运行设置尚未填写完整，请补齐账号、私聊对象、人格与模型。",
    /* V1 迁移：每个码说清「卡在哪、下一步做什么」。迁移块自己会先把缺的生效范围
       列出来，所以这几句多半出现在「数据变过了」或「V1 目录不在了」这类真实意外上。 */
    legacy_root_missing:"没有找到旧版数据目录。它通常与当前版本的数据目录平级；也可以在插件配置的「兼容｜旧版资料迁入」里直接填绝对路径。",
    legacy_root_invalid:"这个路径不是目录，请改填旧版数据目录。",
    legacy_plan_stale:"旧版数据在这次试算之后变过，请重新试算再执行，这样写入的才是你看到的那一份。",
    legacy_nothing_to_migrate:"这份旧版数据里没有可以自动迁移的来源。",
    legacy_scope_unavailable:"这份角色设置里没有要写入的配置分组，这一组会跳过，其余照常。",
    legacy_apply_failed:"迁移没有写入任何设置，请重新试算后再试一次。",
    legacy_request_invalid:"这次操作缺少必要的信息，请重新走一遍迁移的三步。"};
  /* 主题六套，按明暗分两组，每套对应小屋的一个时段或陈设风格。
     色值不在这里写——tokens.css 是唯一色源，这里只有名字与分组。
     色卡预览靠给元素套上 data-theme 真实渲染，而不是各写一份色值副本
     （旧写法把 bg/accent/quiet 硬编码在 app.js 里，加一套主题要改两个文件，
      漏改一处色卡就和实际不符）。 */
  const themes = ["courtyard","dawn","dusk","night","walnut","contrast"];
  const themeKey = "companion-v2-theme";
  const themeLightKey = "companion-v2-theme-follow-light";
  /* 每套主题在小屋里的对应光照。tokens.css 也各自声明了 --t-scene-light，
     两处必须一致；audit_theme_tokens.py 会校验 tokens.css 那一份的存在性。 */
  const themeLight = {courtyard:"day", dawn:"dawn", dusk:"dusk", night:"night", walnut:"dusk", contrast:"day"};
  /* 显示维度：主题之外还有密度、圆角、字号、动效。
     每个维度声明存储键、可选值与默认值；引导脚本 index.html 读同一组键。 */
  const dimensions = {
    theme:{key:themeKey,values:themes,fallback:"courtyard",dataset:"theme"},
    density:{key:"companion-v2-density",values:["compact","standard","roomy"],fallback:"standard",dataset:"density"},
    radius:{key:"companion-v2-radius",values:["sharp","standard","round"],fallback:"standard",dataset:"radius"},
    typeScale:{key:"companion-v2-type-scale",values:["s","m","l","xl"],fallback:"m",dataset:"typeScale"},
    motion:{key:"companion-v2-motion",values:["full","low","off"],fallback:"full",dataset:"motion"},
  };
  /* 桥接探测。
   *
   * 旧写法要求 window.AstrBotPluginPage 与 meta 标签同时存在，任一缺失就
   * 掉进「设计预览」模式，于是 38 处分支渲染「请从已登录的管理界面打开」——
   * 用户在真实宿主里看到的就是这句「连不上 AstrBot」。
   *
   * 现在改成：只要桥在就算接上，meta 标签只作为辅助信号（宿主可能还没有
   * 加这个标签，页面不该因此拒绝工作）。真的连不上时由请求失败确认来报错，
   * 而不是靠一个前置判断猜。
   *
   * 桥是宿主注入的，注入时机晚于脚本执行是正常的，所以这里还提供一个
   * 等待器：宿主在 ready 之前调 bridge() 会拿到 null，调用方可以重试。 */
  const bridgeOf = () => (window.AstrBotPluginPage && typeof window.AstrBotPluginPage.apiGet === "function")
    ? window.AstrBotPluginPage : null;
  /* 保留 hosted() 这个名字（很多调用点用它判断能力），但语义改为「桥可用」。 */
  const hosted = () => !!bridgeOf();

  /* 自己把页面桥 SDK 拉进来。
   *
   * 为什么需要这一步：`AstrBotPluginPage` 不是宿主无条件塞进页面的全局变量，
   * 它由 /api/plugin/page/bridge-sdk.js 定义。四个工作区的 workspace.html 都
   * 显式引了它，主页面此前没有——此时 bridgeOf() 永远为假，整页每个工作区
   * 都渲染「尚未读到宿主目录」，症状是「拓展页连不上 AstrBot」。
   *
   * 但也不能武断断言「宿主一定不注入」：有些宿主会在重写 HTML 时自动补上
   * 这条脚本（本地验收夹具 tools/check_product_page.cjs 就是这么做的）。
   * 所以这里做成**先等、等不到再拉**：宿主若已注入，什么都不发生；宿主若
   * 不注入，也能自愈。两种情况都成立，不必先弄清宿主版本。
   *
   * 只在 document 还是 loading 状态时追加，且全页面只试一次——脚本是在
   * 解析期被发现的，晚了就不再有机会执行，那时只能报失败给用户重试。 */
  /* 页面桥 SDK 的候选地址，按可能性排序，依次尝试。
   *
   * 为什么不写死一个：`AstrBotPluginPage` 由 AstrBot 的 plugin_page_bridge.js
   * 定义，而这条路由的对外路径在不同版本/不同托管方式下不完全一样。
   * 仓库里三处独立证据（tools/check_life_page.cjs、tools/check_relationship_page.cjs、
   * tests/demo_host_process.py）都指向 `/api/plugin/page/bridge-sdk.js`，
   * 所以它排第一；后面两个是相对形式，用于页面被挂在带前缀的路径下时。
   *
   * 写死一个的代价很不对称：猜对了只是省一个 404，猜错了整页连不上宿主，
   * 而症状只是一句「连接失败」。所以宁可多试。 */
  const SDK_CANDIDATES = [
    "/api/plugin/page/bridge-sdk.js",
    "api/plugin/page/bridge-sdk.js",
    "../api/plugin/page/bridge-sdk.js",
  ];
  let sdkAttempt = 0;

  /* 已经引过的脚本不重复引。用 includes 而不是完全匹配属性：
     宿主可能带上查询串或写成别的等价形式。

     但只在「它还有可能成功」时才算数：一个已经加载完、桥却仍没定义的标签是
     死标签——最常见的是宿主注入那条带 asset_token 的脚本因 token 失败。此时
     必须把它摘掉再试自己的候选地址，否则宿主一旦注入失败，页面就再没有第二次
     机会：那些候选是唯一不依赖宿主的路。 */
  function sdkAlreadyPresent(ready) {
    const tags = [...document.querySelectorAll("script[src]")]
      .filter(script => String(script.getAttribute("src") || script.src || "")
        .includes("bridge-sdk.js"));
    const dead = tags.filter(script => script.readyState === "loaded" || script.readyState === "complete");
    if (ready && dead.length) {
      for (const script of dead) script.remove();
      /* 摘掉之后当作「没有」处理，让调用方继续试候选。 */
      return tags.some(script => !dead.includes(script));
    }
    return tags.length > 0;
  }

  /* 正在加载的那个标签。等待器每一拍都会来问一次，没有这个护栏就会在
     同步路径上连着插两三个同样的 script（等待器入口一次、第一拍一次），
     并且把 sdkAttempt 推过头，把候选表白白用掉。 */
  let sdkLoading = null;

  function requestBridgeSdk() {
    if (bridgeOf()) return;
    if (sdkLoading) return;
    if (sdkAlreadyPresent(false)) return;
    if (sdkAttempt >= SDK_CANDIDATES.length) return;
    const path = SDK_CANDIDATES[sdkAttempt++];
    /* 不能按 readyState 设限。这个模块是 `defer` 的，执行时 readyState 已经是
       "interactive"——那之后动态插进去的脚本仍然会执行，但按
       `readyState === "loading"` 判断就会永远不插入，整页连不上桥。 */
    const script = document.createElement("script");
    script.src = path;
    script.addEventListener("load", () => {
      /* 加载成功但桥没定义（路由返回了别的东西）：继续试下一个候选。 */
      sdkLoading = null;
      if (!bridgeOf()) requestBridgeSdk();
    });
    script.addEventListener("error", () => {
      /* 这个地址没有：试下一个。全都试完就停下，由等待器超时报错，
         不再无限重试——那只会刷满控制台。 */
      sdkLoading = null;
      requestBridgeSdk();
    });
    sdkLoading = script;
    document.head.appendChild(script);
  }

  /* 各页取桥的统一入口，写成 `bridgeNow() || await bridgeFor()`。
   *
   * 为什么是这个形状：
   *   - 桥通常早就就位（product.connect() 先跑），那时 bridgeNow() 命中，
   *     整条路径是同步的。单写 `await bridgeFor()` 会让每个 api() 至少多
   *     一拍微任务——那会改变调用方看到的时序，mount 之后立刻发请求的
   *     用例会读到空数组。
   *   - 桥真的还没出现时才走 bridgeFor() 等待。它的失败路径抛出的是可翻译
   *     的 bridge_unavailable，而不是 TypeError。
   *
   * 一个写错过的替代方案记在这里，免得下次再写：
   *   `await (CompanionProduct?.whenBridgeReady?.() ?? Promise.resolve(window.AstrBotPluginPage))`
   * `??` 的右半边会**立刻求值**——product 没提供等待器时（测试夹具、或某个
   * 页面先于 product-page.js 就绪），拿到的是 undefined，接着 bridge.apiGet
   * 抛 TypeError，用户看到的是一句无从翻译的类型错误。 */
  function bridgeNow() {
    return bridgeOf();
  }

  async function bridgeFor() {
    if (typeof whenBridgeReady === "function") return await whenBridgeReady();
    const found = bridgeOf();
    if (!found) throw Object.assign(new Error(errors.bridge_unavailable), {code: "bridge_unavailable"});
    return found;
  }

  /* 等桥就绪：先看有没有；没有就先尝试自己引 SDK，再等它出现。
     waitFor 毫秒内没等到才算真的没接上。 */
  function whenBridgeReady(timeout = 10000) {
    if (bridgeOf()) return Promise.resolve(bridgeOf());
    requestBridgeSdk();
    return new Promise((resolve, reject) => {
      const started = Date.now();
      const tick = () => {
        const found = bridgeOf();
        if (found) return resolve(found);
        /* 桥还没到，且还没试过自己引——补一次（首屏可能因为 readyState
           已经走完 loading 而没引上，这里给一个迟到的机会）。 */
        /* 还在等桥就继续把候选试下去（首次会从这里启动第一个）。
           全失败时 requestBridgeSdk 自己会停下，不再重复插。 */
        requestBridgeSdk();
        if (Date.now() - started >= timeout) {
          return reject(Object.assign(new Error(errors.bridge_unavailable), {code: "bridge_unavailable"}));
        }
        setTimeout(tick, 80);
      };
      tick();
    });
  }
  const fail = (code, confirmed = false) => Object.assign(new Error(errors[code] || "暂不可读取，请刷新核对。"),
    {code, ...(confirmed ? {confirmed:true} : {})});
  let bridge, catalog, manifest, data = {}, failures = {}, failureCodes = {}, selected = {target_id:null,role_id:null};
  let bridgeReady = false;
  let releaseAppearanceContext = null;
  let generation = 0, changed = () => {}, loading = false, notice = "";
  const workspaceFrames = new Map();
  const workspaceBundleLoads = new Map();
  let growthPage = null, growthCursor = null, growthBusy = false;
  const pendingGrowth = new Map();
  const appearance = {};
  for (const [name, spec] of Object.entries(dimensions)) {
    let value = spec.fallback;
    try { const saved = localStorage.getItem(spec.key); if (spec.values.includes(saved)) value = saved; } catch (_) {}
    appearance[name] = value;
  }
  const currentTheme = () => appearance.theme;
  /* 「跟随时段」默认关。开了之后界面主题跟着小屋当前光照走，
     但只在时段真的变了才切——每次渲染都切会把用户手选的主题冲掉。 */
  let followLight = false;
  try { followLight = localStorage.getItem(themeLightKey) === "on"; } catch (_) {}
  let lastSceneLight = "";
  /* AstrBot 把插件页放在 sandbox="allow-scripts" 的 iframe 里，没有
     allow-same-origin。这个 iframe 是 opaque origin，localStorage 会直接抛
     SecurityError。以前只写 localStorage，于是每次刷新网页外观都回到默认；
     桥接 API 走宿主同源请求，所以把同一组偏好按 Dashboard 账号存到插件数据。
     localStorage 仍然保留：页面直接打开、或未来宿主放开 same-origin 时它仍是
     零延迟的首屏缓存。 */
  let preferencesTouched = 0, preferencesApplying = false, preferenceSaveTimer = null;
  const displayPreferencesSnapshot = () => ({
    appearance:{...appearance},
    followLight:Boolean(followLight),
    largeText:Boolean(window.CompanionDisplayPreferences?.largeText?.()),
  });
  const preferencesAreDefault = () => Object.entries(dimensions).every(([name, spec]) => appearance[name] === spec.fallback)
    && !followLight && !window.CompanionDisplayPreferences?.largeText?.();
  function scheduleDisplayPreferencesSave() {
    if (preferencesApplying || !bridgeReady) return;
    if (preferenceSaveTimer) clearTimeout(preferenceSaveTimer);
    preferenceSaveTimer = setTimeout(() => {
      preferenceSaveTimer = null;
      if (preferencesApplying || !bridgeReady) return;
      api("POST", "page/display-preferences", displayPreferencesSnapshot()).catch(error => {
        console.warn("[CompanionV2] Display preferences were not saved", {
          code:error?.code || "display_preferences_unavailable",
        });
      });
    }, 120);
  }
  function applyDisplayPreferences(saved) {
    if (!saved || typeof saved !== "object" || !saved.appearance || typeof saved.appearance !== "object") return false;
    preferencesApplying = true;
    try {
      for (const [name, spec] of Object.entries(dimensions)) {
        if (spec.values.includes(saved.appearance[name])) appearance[name] = saved.appearance[name];
      }
      followLight = saved.followLight === true;
      applyAppearance();
      sendWorkspace({kind:"appearance",appearance:{...appearance}});
      if (typeof saved.largeText === "boolean") {
        window.CompanionDisplayPreferences?.setLargeText?.(saved.largeText);
      }
    } finally { preferencesApplying = false; }
    return true;
  }
  function displayPreferencesChanged() {
    if (preferencesApplying) return;
    preferencesTouched += 1;
    scheduleDisplayPreferencesSave();
  }
  function applyAppearance() {
    const root = document.documentElement;
    for (const [name, spec] of Object.entries(dimensions)) root.dataset[spec.dataset] = appearance[name];
    root.dataset.themeReady = "";
  }
  /* 主题叠化：自定义属性不能插值，所以不逐属性过渡，
     而是盖一层用旧主题渲染的覆层再淡出——视觉上是两个主题叠化。
     覆层要先把旧主题的 data-theme 写回去取色，取完再换新主题、淡出覆层。 */
  function crossfadeTheme(next) {
    const root = document.documentElement;
    const previous = root.dataset.theme;
    const reduced = appearance.motion !== "full"
      || matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    /* 入场期间不叠化，直接换色。
       理由：叠化覆层是「整页快照」，而入场时小屋是 position:fixed
       且正在被 transform 缩放——快照会把那个中间状态连同被裁切的一半
       一起复制下来，淡出过程中能看到一层撕裂的影子。
       入场只有几秒，这几秒里换主题直接生效即可（本来也没有对比基准
       可言，用户正盯着小屋）。 */
    const entering = document.body.classList.contains("is-entrance");
    if (reduced || entering || !previous || previous === next) {
      root.dataset.theme = next;
      return;
    }
    const veil = document.createElement("div");
    veil.className = "theme-crossfade";
    veil.setAttribute("aria-hidden", "true");
    veil.dataset.theme = previous;
    document.body.appendChild(veil);
    // 强制一次布局，让覆层带着旧主题的底色完成首次绘制，否则首帧就是透明的。
    void veil.offsetHeight;
    root.dataset.theme = next;
    veil.classList.add("is-fading");
    veil.addEventListener("transitionend", () => veil.remove(), {once:true});
    // 兜底：动效维度可能在过渡途中被改掉，transitionend 就不再触发。
    setTimeout(() => veil.remove(), 1000);
  }
  function setAppearance(name, value) {
    const spec = dimensions[name];
    if (!spec || !spec.values.includes(value) || appearance[name] === value) return appearance[name];
    if (name === "theme") crossfadeTheme(value);
    appearance[name] = value;
    if (name !== "theme") applyAppearance();
    try { localStorage.setItem(spec.key, value); } catch (_) {}
    displayPreferencesChanged();
    if (name === "theme") sendWorkspace({kind:"theme",theme:value});
    else sendWorkspace({kind:"appearance",name,value});
    return appearance[name];
  }
  function setFollowLight(on) {
    const next = !!on, changed = followLight !== next;
    followLight = next;
    try { localStorage.setItem(themeLightKey, followLight ? "on" : "off"); } catch (_) {}
    if (changed) displayPreferencesChanged();
    return followLight;
  }
  /* 小屋光照变化时调用。跟随时段开着才会切主题，
     且必须与当前主题的对应光照不同才动，避免同一时段反复写。 */
  function syncLight(light) {
    if (!light || light === lastSceneLight) return null;
    lastSceneLight = light;
    if (!followLight) return null;
    const match = Object.entries(themeLight).find(([, value]) => value === light);
    // 一个光照对应多套主题（黄昏同时是暮色与深林），此时保留用户当前选择。
    if (!match || themeLight[appearance.theme] === light) return null;
    const candidates = Object.entries(themeLight).filter(([, value]) => value === light).map(([id]) => id);
    const next = candidates.length === 1 ? candidates[0] : appearance.theme;
    return next === appearance.theme ? null : setAppearance("theme", next);
  }
  function resetAppearance() {
    for (const [name, spec] of Object.entries(dimensions)) setAppearance(name, spec.fallback);
    return {...appearance};
  }
  applyAppearance();
  function setTheme(theme) { return setAppearance("theme", theme); }
  function sendWorkspace(payload) {
    for (const owner of workspaceFrames.keys()) {
      if (owner.isConnected) owner.contentWindow.postMessage({channel:"companion-workspace",...payload},"*");
    }
  }
  const target = () => catalog?.targets.find(item => item.target_id === selected.target_id);
  const targetPreference = item => [
    item.scope_kind === "private" ? 1 : 0,
    item.scope_kind === "private" && item.participant_id && item.participant_id !== "*" ? 1 : 0,
    item.display_name ? 1 : 0,
  ];
  const prefersTarget = (candidate, current) => {
    const left = targetPreference(candidate), right = targetPreference(current);
    return left.some((value, index) => value !== right[index] && value > right[index]);
  };
  const roleChoices = () => {
    const choices = new Map();
    for (const item of catalog?.targets || []) for (const persona of item.roles || []) {
      const previous = choices.get(persona.role_id);
      if (!previous || prefersTarget(item, previous.target)) {
        choices.set(persona.role_id, {target:item, role:persona});
      }
    }
    return [...choices.values()].sort((a,b) => roleLabel(a.role).localeCompare(roleLabel(b.role), "zh-CN")
      || accountLabel(a.target).localeCompare(accountLabel(b.target), "zh-CN"));
  };
  const roleTarget = () => {
    const current = target();
    if (current?.scope_kind === "private" && current.participant_id !== "*"
        && current.roles.some(item => item.role_id === selected.role_id)) return current;
    return roleChoices().find(item => item.role.role_id === selected.role_id)?.target || null;
  };
  const role = () => {
    const current = target()?.roles.find(item => item.role_id === selected.role_id);
    const owner = roleTarget()?.roles.find(item => item.role_id === selected.role_id);
    if (!owner) return current;
    if (target()?.scope_kind !== "group" || owner === current) return owner;
    return {...owner, reply_api:current.reply_api || owner.reply_api,
      group_memory_api:current.group_memory_api || null};
  };
  const scopeKey = () => JSON.stringify([catalog?.installation_id, selected.target_id, selected.role_id]);
  /* 后端 outcome_response() 把机器码同时放在 message 与 data.code 上，因为页面桥
     只转发 message、会丢掉信封其余字段。所以取码要看两处，不能只认 data.code；
     下面 api() 里的英文措辞兜底只是老桥接形态的退路。 */
  const CODE = /^[a-z][a-z0-9_]{2,60}$/;
  const codeOf = wrapped => wrapped?.data?.code
    || (CODE.test(wrapped?.message || "") ? wrapped.message : "") || "";
  /* The current Dashboard bridge resolves the inner ``data`` object.  Older
     bridges can still return the whole envelope, while some legitimate inner
     payloads use ``status: "ok"`` for their own diagnostic state.  Envelope
     detection therefore also checks the key shape before unwrapping. */
  const ENVELOPE_KEYS = new Set(["status", "message", "reason", "data", "error", "code"]);
  const hasOwn = (value, name) => !!value && typeof value === "object"
    && Object.prototype.hasOwnProperty.call(value, name);
  const isEnvelope = value => !!value && typeof value === "object" && !Array.isArray(value)
    && ["ok", "error"].includes(value.status) && hasOwn(value, "data")
    && Object.keys(value).every(name => ENVELOPE_KEYS.has(name));
  function unwrap(response) {
    const dataOnly = response && typeof response === "object" && !Array.isArray(response)
      && Object.keys(response).length === 1 && hasOwn(response, "data");
    const wrapped = isEnvelope(response) ? response : dataOnly && isEnvelope(response.data) ? response.data : null;
    if (wrapped?.status === "error") throw fail(codeOf(wrapped) || "invalid_response", true);
    let value = wrapped ? wrapped.data : response;
    /* A bridge shim may preserve one anonymous data layer without the
       envelope fields.  Only unwrap the exact {data: ...} shape. */
    if (!wrapped && dataOnly
        && response.data && typeof response.data === "object" && !Array.isArray(response.data)) value = response.data;
    if (!value || typeof value !== "object" || Array.isArray(value)) throw fail("invalid_response");
    return value;
  }
  const DEFAULT_API_TIMEOUT_MS = 15000;
  const MAX_API_TIMEOUT_MS = 300000;
  const requestTimeout = options => Number.isInteger(options?.timeoutMs)
    && options.timeoutMs >= 1000
    ? Math.min(options.timeoutMs, MAX_API_TIMEOUT_MS) : DEFAULT_API_TIMEOUT_MS;
  const diagnosticEndpoint = value => String(value || "").split("?", 1)[0]
    .replace(/(^|\/)targets\/[^/]+/g, "$1targets/:target")
    .replace(/(^|\/)roles\/[^/]+/g, "$1roles/:role");
  async function bounded(promise, timeoutMs = DEFAULT_API_TIMEOUT_MS) {
    let timer;
    try { return await Promise.race([promise, new Promise((_,reject) => {timer = setTimeout(() => reject(fail("bridge_timeout")),timeoutMs);})]); }
    finally { clearTimeout(timer); }
  }
  async function api(method, path, body, options) {
    const timeoutMs = requestTimeout(options), startedAt = Date.now();
    try { return unwrap(await bounded(method === "GET" ? bridge.apiGet(path,body) : bridge.apiPost(path,body), timeoutMs)); }
    catch(error) {
      if (error?.code === "bridge_timeout") {
        console.warn("[CompanionV2] Dashboard request timed out", {
          method, endpoint: diagnosticEndpoint(path), elapsed_ms: Date.now() - startedAt, timeout_ms: timeoutMs,
        });
      }
      /* 老版页面桥会把机器码放在 Error.message 或 Error.code 上，未必经过
         unwrap()。先把已知码送回同一张表，避免 setup-guide 直接显示英文串。 */
      if (error.code && errors[error.code]) throw fail(error.code, error.confirmed === true);
      if (CODE.test(error.message || "") && errors[error.message]) throw fail(error.message, error.confirmed === true);
      if (error.code) throw fail(error.code, error.confirmed === true);
      if (/initialized|explicit V2 profile/i.test(error.message)) throw fail("staging_not_initialized");
      if (/Dashboard|sign in/i.test(error.message)) throw fail("dashboard_identity_required");
      throw fail(CODE.test(error.message || "") ? error.message : "invalid_response");
    }
  }
  /* 桥的 endpoint 是**插件相对**路径：不带前导斜杠，也**不带插件目录名**。
   *
   * 目录给的是绝对地址 `/astrbot_plugin_private_companion/host/status`，
   * 交给桥之前要切掉 `PREFIX`，送出的才是 `host/status`。宿主拿插件名与
   * endpoint 拼成真实路由，所以带上目录名等于拼两次，直接 404：
   *   /astrbot_plugin_private_companion/astrbot_plugin_private_companion/host/status
   *
   * 这里曾经写成「只去掉前导斜杠、保留目录名」，注释还断言「两种宿主都成立」——
   * 那是没验证过的猜测。反证有三处：
   *   - tools/check_host_management_page.cjs:29 跑的是**真实 SDK**，
   *     它断言 endpoint 不以 "/" 开头（"bridge endpoints remain plugin-relative"），
   *     并按裸名分发（host/status、staging/probe…）；
   *   - tools/check_product_page.cjs 的合成 SDK 按 `'/'+HOST+'/'+relative`
   *     把相对名还原成绝对地址，RPC 同样只认裸名；
   *   - docs/CORE_HOST_MANAGEMENT_PAGE_20260924.md：「所有请求使用插件相对路径」。
   * 用户看到的「未找到该路由」+「Plugin bridge endpoint is invalid.」就是这两次拼接。
   *
   * 各工作区页（memory / state / perception-native / reply / models）用的都是
   * 切掉目录名的写法，这里跟上，全站只有一种口径。 */
  function relative(path) {
    if (typeof path !== "string" || !path.startsWith(PREFIX)) throw fail("invalid_response");
    const value = path.slice(PREFIX.length);
    if (!value || /[?#%\\:]/.test(value) || value.split("/").some(part => !part || part === "." || part === "..")) throw fail("invalid_response");
    return value;
  }

  /* 工作区空态要说得准：桥没到、目录没读、还没有窗口目标、有目标但没角色、
     有角色但没有模型路由——这五种情况的下一步完全不同，用一句「请选择已关联
     角色的私聊窗口」盖住，用户就会以为映射白做了（4.29 上就是这么报的）。 */
  function workspaceState() {
    if (!bridgeReady || !bridge) return {code:"bridge"};
    if (!catalog) return {code:"catalog"};
    const targets = catalog.targets || [];
    if (!targets.length) return {code:"targets"};
    const current = role();
    if (!current) return {code:"role", targets:targets.length};
    if (typeof current.model_api !== "string" || !current.model_api) {
      return {code:"model_api", targets:targets.length};
    }
    if (!catalog.operator_id) return {code:"operator", targets:targets.length};
    return {code:"ready", targets:targets.length};
  }
  const workspaceMessages = {
    bridge: "正在连接 AstrBot 管理界面…",
    catalog: "正在读取账号与会话…",
    targets: "还没有选定窗口目标：到「账号与会话」勾选要交给插件处理的窗口并保存目标。",
    role: "这个账号下还没有已映射的角色：到「账号与会话」保存角色映射。",
    model_api: "当前窗口没有模型路由：回到「账号与会话」重新保存角色映射后再读取。",
    operator: "Dashboard 身份还没就绪，请重新登录管理界面后再打开。",
  };
  function workspaceHint() {
    const state = workspaceState();
    return state.code === "ready" ? "" : (workspaceMessages[state.code] || "工作区暂时读不到，请重新读取。");
  }
  const workspaceDiagnosis = () => ({...workspaceState(), hint: workspaceHint(),
    targets: (catalog?.targets || []).length});
  function chooseDefault() {
    const choices = roleChoices();
    const choice = choices.find(item => item.role.role_id === selected.role_id) || choices[0];
    const current = target();
    const context = current?.roles.some(item => item.role_id === choice?.role.role_id)
      ? current : choice?.target;
    selected = {target_id:context?.target_id ?? null, role_id:choice?.role.role_id ?? null};
  }
  let summaryRequest = 0;
  const SUMMARY_CACHE_MS = 45000;
  const SUMMARY_CACHE_KEY = "companion-product-summary.v2";
  const summaryCache = new Map();
  function summaryCacheKey() {
    return [catalog?.installation_id || "", selected.target_id || "", selected.role_id || ""].join(":");
  }
  function readSummaryCache(key) {
    const memory = summaryCache.get(key);
    if (memory && Date.now() - memory.at <= SUMMARY_CACHE_MS) return memory;
    try {
      const all = JSON.parse(window.sessionStorage?.getItem(SUMMARY_CACHE_KEY) || "{}");
      const value = all?.[key];
      if (value && Date.now() - Number(value.at || 0) <= SUMMARY_CACHE_MS) {
        summaryCache.set(key, value);
        return value;
      }
    } catch {}
    return null;
  }
  function writeSummaryCache(key, value) {
    summaryCache.set(key, value);
    try {
      const all = JSON.parse(window.sessionStorage?.getItem(SUMMARY_CACHE_KEY) || "{}");
      all[key] = value;
      const entries = Object.entries(all).filter(([, item]) => item && Date.now() - Number(item.at || 0) <= SUMMARY_CACHE_MS).slice(-8);
      window.sessionStorage?.setItem(SUMMARY_CACHE_KEY, JSON.stringify(Object.fromEntries(entries)));
    } catch {}
  }
  function paintCachedSummary(cached) {
    data = {...(cached.data || {})};
    failures = {...(cached.failures || {})};
    failureCodes = {...(cached.failureCodes || {})};
    changed({kind:"state-refresh"});
  }
  async function loadSummary(ticket, {force=false} = {}) {
    const key = summaryCacheKey();
    const cached = force ? null : readSummaryCache(key);
    if (cached) {
      paintCachedSummary(cached);
      setTimeout(() => {
        if (ticket !== generation) return;
        loadSummary(ticket, {force:true}).then(ok => { if (ok && ticket === generation) changed({kind:"state-refresh"}); }).catch(() => {});
      }, 0);
      return true;
    }
    const request = ++summaryRequest;
    const current = role();
    const names = ["life","state","growth"].filter(name => current?.[name+"_api"]);
    if (target()?.scope_kind === "private") {
      names.push(...["memory","relationships"].filter(name => current?.[name+"_api"]));
    }
    const results = await Promise.allSettled(names.map(name => api("GET",relative(current[name+"_api"]),["memory","relationships","growth"].includes(name)?{limit:3}:undefined)));
    if (ticket !== generation || request !== summaryRequest) return false;
    const fresh = {}, rejected = {}, rejectedCodes = {};
    results.forEach((result,index) => {
      if(result.status === "fulfilled") fresh[names[index]] = result.value;
      else {
        const code = result.reason?.code || "";
        rejected[names[index]] = errors[code] || "暂不可读取";
        rejectedCodes[names[index]] = code;
      }
    });
    data = fresh; failures = rejected; failureCodes = rejectedCodes;
    if (Object.keys(fresh).length) {
      writeSummaryCache(key, {at: Date.now(), data: fresh, failures: rejected, failureCodes: rejectedCodes});
    }
    return true;
  }
  async function refreshSummary() {
    if(!bridgeReady||!catalog||!role())return false;
    const ticket=generation;
    const refreshed=await loadSummary(ticket,{force:true});
    if(!refreshed||ticket!==generation)return false;
    changed({kind:"state-refresh"});
    return true;
  }
  async function connect(callback) {
    if (callback) changed = callback;
    const ticket = ++generation;
    loading = true; notice = "正在读取角色与会话…"; data = {}; failures = {}; failureCodes = {};
    catalog = manifest = null; growthPage = null; bridgeReady = false;
    changed();
    try {
      /* 先等桥出现，而不是直接判断 hosted() 然后放弃。
         宿主注入桥晚于脚本执行是正常时序，旧写法会因此判成「未连接」。 */
      bridge = await whenBridgeReady();
      if (ticket !== generation) return;
      const context = await bounded(bridge.ready());
      if (ticket !== generation) return;
      if (context?.pluginName !== HOST) throw fail("dashboard_identity_required");
      bridgeReady = true;
      // The SDK writes its light/dark alias on every context update. Restore the
      // cottage's browser preference after that write, including future updates.
      releaseAppearanceContext?.();
      const restoreAppearance = () => {
        applyAppearance();
        sendWorkspace({kind:"appearance",appearance:{...appearance}});
      };
      releaseAppearanceContext = bridge.onContext?.(restoreAppearance) || null;
      restoreAppearance();
      const settle = promise => promise.then(value => ({status:"fulfilled",value}), reason => ({status:"rejected",reason}));
      const preferencesRevision = preferencesTouched, preferencesChangedBeforeLoad = preferencesRevision > 0;
      const preferencesTask = settle(api("GET","page/display-preferences"));
      const manifestTask = settle(api("GET","integration/manifest"));
      const directoryTask = settle(api("GET","page/targets"));
      preferencesTask.then(result => {
        if (ticket !== generation || preferencesRevision !== preferencesTouched) return;
        if (result.status !== "fulfilled") return;
        if (preferencesChangedBeforeLoad) { scheduleDisplayPreferencesSave(); return; }
        const saved = result.value?.preferences;
        if (saved) applyDisplayPreferences(saved);
        else if (!preferencesAreDefault()) scheduleDisplayPreferencesSave();
      });
      const directoryResult = await directoryTask;
      if (ticket !== generation) return;
      let summaryTask = null;
      if (directoryResult.status === "fulfilled") {
        const directory = directoryResult.value;
        if (!Array.isArray(directory.targets) || !directory.installation_id || directory.targets.some(t => !Array.isArray(t.roles))) throw fail("invalid_response");
        catalog = directory; chooseDefault(); notice = "";
        loading = false; changed();
        summaryTask = loadSummary(ticket);
      } else notice = errors[directoryResult.reason?.code] || "暂时无法读取授权对象。请在账号与会话中检查连接。";
      const manifestResult = await manifestTask;
      if (ticket !== generation) return;
      manifest = manifestResult.status === "fulfilled" ? manifestResult.value : null;
      if (summaryTask) await summaryTask;
      if (!manifest && !notice) notice = "核心工作区已连接，插件能力目录暂不可读取。";
    } catch(error) {if(ticket === generation) {notice = errors[error.code] || "连接失败，请刷新核对。";catalog = manifest = null;data = {};failures = {};failureCodes = {};}}
    finally {if(ticket === generation) {loading = false; changed();}}
  }
  async function refreshDirectoryAfterMapping() {
    if (!bridgeReady || !bridge) return;
    const ticket = ++generation;
    const previous = {...selected};
    const results = await Promise.allSettled([api("GET", "page/targets"), api("GET", "integration/manifest")]);
    if (ticket !== generation) return;
    if (results[0].status !== "fulfilled") throw results[0].reason;
    const directory = results[0].value;
    if (!Array.isArray(directory.targets) || !directory.installation_id
        || directory.targets.some(item => !Array.isArray(item.roles))) throw fail("invalid_response");
    catalog = directory;
    if (results[1].status === "fulfilled") manifest = results[1].value;
    const retained = catalog.targets.find(item => item.target_id === previous.target_id
      && item.roles.some(roleItem => roleItem.role_id === previous.role_id));
    if (retained) selected = previous;
    else chooseDefault();
    growthPage = null; growthCursor = null;
    rebindWorkspaceContexts();
    loading = false; changed();
    await loadSummary(ticket);
    if (ticket !== generation) return;
    loading = false;
    notice = results[1].status === "fulfilled" ? "" : "角色映射已更新，能力目录暂未读到。";
    renderScope();
    sendWorkspace({kind:"host-mappings-refreshed", selection:{...selected}});
    changed({kind:"host-mappings", selection:{...selected}});
  }
  async function manageExtension(capabilityId, action, expectedRevision, operationKey) {
    const item = manifest?.extensions?.find(extension => extension.capability_id === capabilityId);
    if (!item || item.extension_id !== bundledExtensions[capabilityId]
        || !["assemble","update","pause","resume","uninstall"].includes(action)
        || !Number.isInteger(expectedRevision) || expectedRevision < 0
        || typeof operationKey !== "string" || !operationKey) throw fail("integration_lifecycle_request_invalid");
    const result = await api("POST", "integration/lifecycle", {
      extension_id: item.extension_id, action, expected_revision: expectedRevision, operation_key: operationKey,
      // 装配后由服务端补一次角色启用；装配按钮开在当前角色的小屋里，所以把
      // 当前选中的角色和目标一起送过去，避免多角色安装时猜默认角色。
      ...(selected.role_id && selected.target_id
        ? {role_id: selected.role_id, target_id: selected.target_id} : {}),
    });
    await refreshIntegrations();
    return result;
  }
  async function refreshIntegrations() {
    manifest = await api("GET", "integration/manifest");
    changed({kind:"integration-refresh"});
    return manifest;
  }
  function acquaintanceDirectory() {
    if (typeof catalog?.acquaintance_api !== "string") return Promise.reject(fail("invalid_response"));
    return api("GET", relative(catalog.acquaintance_api));
  }
  async function choose(targetId, roleId, quiet=false) {
    const next = catalog?.targets.find(t => t.target_id === targetId);
    if (!next?.roles.some(r => r.role_id === roleId)) return;
    if (selected.target_id === targetId && selected.role_id === roleId) return;
    const ticket = ++generation;
    selected = {target_id:targetId,role_id:roleId}; data = {}; failures = {}; failureCodes = {}; growthPage = null; growthCursor = null;
    rebindWorkspaceContexts();
    loading = true;
    if (!quiet) changed();
    loading = false;
    if (!quiet) changed();
    await loadSummary(ticket);
    if(ticket === generation) {if(!quiet) changed(); else renderScope();}
  }
  function chooseRole(roleId, quiet=false) {
    const current = target();
    const context = current?.roles.some(item => item.role_id === roleId)
      ? current : roleChoices().find(item => item.role.role_id === roleId)?.target;
    if (context) return choose(context.target_id, roleId, quiet);
  }
  /* 连接状态。这里**不再有 preview 分支**——「设计预览」曾经是个永久状态，
     一旦进去就无限显示「连接 AstrBot 后显示真实角色」，而桥往往只是还没注入。
     现在只有四种真实状态，且都带可执行的下一步。 */
  function connection() {
    return {state:loading?"checking":catalog?"connected":manifest?"ready":"unavailable",
      label:loading?"小屋 · 正在读取":catalog?"小屋 · 管理接口可用":manifest?"小屋 · 待初始化":"小屋 · 连接待检查",
      detail:notice || `已读取 ${catalog?.targets.length||0} 个管理对象 · ${(manifest?.summary?.connected_count)||0} 项拓展状态已读取`};
  }
  function identity() { return {installation_id: catalog?.installation_id || null, operator_id: catalog?.operator_id || null}; }
  /* Show selectors only when the loaded directory has a real choice to make. */
  function scoped() {
    return hosted() && Boolean(catalog && roleChoices().length > 1);
  }
  function scopeVisible() {
    return scoped() || hosted() && !catalog && Boolean(loading || notice);
  }
  function targetLabel(item) {
    const kind = item.scope_kind === "group" || item.group_id ? "群聊" : "私聊";
    const id = nativeId(item.group_id || item.participant_id);
    return `${kind} ${item.display_name ? item.display_name + (id ? `（${id}）` : "") : id || "当前对象"}`;
  }
  const nativeId = value => /^\d+$/.test(String(value || "")) ? String(value) : "";
  const subjectLabel = item => item?.display_name || nativeId(item?.group_id || item?.participant_id) || "当前聊天对象";
  const platformLabel = item => ({qq:"QQ · OneBot",default:"QQ · OneBot",aiocqhttp:"QQ · OneBot",onebot:"QQ · OneBot",qq_official:"QQ 官方机器人",qq_official_webhook:"QQ 官方机器人（Webhook）"})[item?.platform_name || item?.platform_id] || "当前平台";
  const accountLabel = item => {
    const id = nativeId(item.account_id);
    return item.account_name ? item.account_name + (id ? `（${id}）` : "") : id ? `QQ ${id}` : "当前账号";
  };
  function readableLabel(value, fallback) {
    const text = typeof value === "string" ? value.trim() : "";
    return !text || /^(?:role[-_:]|staging[-_:]|dashboard:|onebot-user:)/i.test(text)
      || /^[a-f0-9]{24,}$/i.test(text) ? fallback : text;
  }
  function roleLabel(item = role(), index = null) {
    if (!item) return "当前角色";
    const personas = Array.isArray(item.persona_ids) ? item.persona_ids.filter(Boolean).map(name => name === "[%None]" ? "默认人格" : name) : [];
    return item.role_name || personas.join("、") || readableLabel(item.role_id, Number.isInteger(index) ? `角色 ${index + 1}` : "当前角色");
  }
  function scopeLabel() {
    const owner = roleTarget();
    return owner ? roleLabel() : "尚未选择角色";
  }
  function actorLabel(id) {
    if (!id) return "";
    if (id === catalog?.operator_id) return catalog.operator_name || "当前管理员";
    const actorRole = target()?.roles.find(item => item.role_id === id);
    if (actorRole) return roleLabel(actorRole);
    if (id === "onebot-user:" + target()?.participant_id) return target().display_name || "当前聊天对象";
    return id.startsWith("dashboard:") ? "其他管理员" : id.startsWith("onebot-user:") ? "授权用户" : "其他操作者";
  }
  function executorLabel(plan) {
    return plan.executor_is_current ? "当前私聊窗口" : "同一角色的其他私聊窗口";
  }
  function renderScope(fallbackRole = "陪伴") {
    const root = document.getElementById("stageRole"); if(!root) return;
    const choices = roleChoices();
    const currentLabel = role() ? roleLabel() : fallbackRole;
    if (catalog && role() && choices.length > 1) {
      root.innerHTML = `<label class="stage-role-picker"><span class="stage-role-title"><i data-lucide="house" aria-hidden="true"></i><span>${e(currentLabel)}的小屋</span></span><i class="stage-role-caret" data-lucide="chevron-down" aria-hidden="true"></i><select id="product-role" aria-label="切换人格或角色" ${loading?"disabled":""}>${choices.map(item=>`<option value="${e(item.role.role_id)}" ${item.role.role_id===selected.role_id?"selected":""}>${e(roleLabel(item.role))}</option>`).join("")}</select></label>`;
      return;
    }
    root.innerHTML = `<h2 class="stage-role-title"><i data-lucide="house" aria-hidden="true"></i><span>${e(currentLabel)}的小屋</span></h2>`;
  }
  const empty = text => `<div class="product-empty">${e(text)}</div>`;
  function unavailable(name) {return loading?"正在读取…":failures[name] || (!catalog?notice:!role()?.[name+"_api"]?"当前角色尚未提供这个工作区":"尚无记录");}
  const when = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("zh-CN", {month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "未安排";
  /* 首页概览行。
   *
   * 这里原先是 `card(title, action, content)`——一张带圆角、阴影与边框的
   * overview-card。首页右栏三张、生活观察三张，六张同款卡在屏上重量一样，
   * 层级只能靠嵌套表示；而同一时期管理页已经改成「分隔线 + 单行」的清单。
   * 同一个插件两种读法，用户在管理页学会的扫视方式到首页就不成立。
   *
   * 现在只产出**行**，不再产出卡。分区标题由 app.js 给，行本身只有
   * 一个标签、一句说明、一个右侧动作——与管理页的 .console-row 同形状。
   * 说明限宽交给 home.css 的 .home-row-main small（max-width:62ch），
   * 所以这里不重复限宽。 */
  const row = (label, note, action) =>
    `<div class="home-row"><span class="home-row-main"><strong>${label}</strong>${note?`<small>${note}</small>`:""}</span>${action||""}</div>`;
  const rows = items => `<div class="home-rows">${items.join("")}</div>`;
  /* 动作按钮沿用旧的 data-* 契约（data-life / data-go / data-home-section），
     事件委托在 app.js，不因为换了外壳而失效。 */
  const link = (text, attrs) => `<button class="button small" ${attrs}>${text}</button>`;
  function stateCard() {
    return window.CompanionHomeSidebar.renderState({state:data.state,life:data.life,loading,unavailable,
      unavailableCode:name=>failureCodes[name]||""});
  }
  function scheduleCard() {
    return window.CompanionHomeSidebar.renderSchedule({state:data.state,life:data.life,loading,unavailable,
      unavailableCode:name=>failureCodes[name]||""});
  }
  function capabilityCard() {
    const summary = manifest?.summary;
    if(!summary) return rows([row("拓展能力", e(unavailable("capability")),
      link("能力目录", 'data-home-section="furniture"'))]);
    const ready = Number.isInteger(summary.extension_ready_count) ? summary.extension_ready_count : 0;
    const unassembled = Number.isInteger(summary.unassembled_extension_count) ? summary.unassembled_extension_count : 0;
    return rows([
      row("原生拓展运行中", "七项能力由 V2 本体装配和管理。",
        `<span class="home-row-value"><b>${ready}</b><small>项</small></span>`),
      row("待装配", "数据会保留，装配后即可继续使用。",
        `<span class="home-row-value"><b>${unassembled}</b><small>项</small></span>`),
      row("能力目录", "", link("打开", 'data-home-section="furniture"')),
    ]);
  }
  function growthCard() {
    const items = data.growth?.items||[];
    if(!items.length) return rows([row("目标与成长", e(unavailable("growth")),
      link("查看成长", 'data-go="manage" data-manage="learning"'))]);
    return rows([
      ...items.map(item => row(e(item.title||"来源暂不可用"),
        e(item.progress_note||item.description||""),
        `<span class="home-row-value"><small>${e(states[item.state]||"状态待核对")}</small></span>`)),
      row("全部记录", "", link("查看成长", 'data-go="manage" data-manage="learning"')),
    ]);
  }
  function memoryCard() {
    const items = data.memory?.items||[];
    return rows([
      ...(items.length
        ? items.slice(0,3).map(item => row(e(item.representation||item.predicate||"已保存事实"), ""))
        : [row("最近的事实", e(unavailable("memory")))]),
      row("记忆工作区", "", link("查看", 'data-go="memories"')),
      row("人物与关系", "", link("查看", 'data-go="people"')),
    ]);
  }
  /* 工作区容器。
   *
   * 这里的 iframe 是**空壳**：内容由 mountWorkspace() 用 srcdoc 填。
   * 所以只要 mountWorkspace() 不会执行（桥没接上），它就是一块有尺寸、
   * 没内容的方框——900px 高、带边框、里面一片空白。用户看到的正是这个。
   *
   * 过去的写法对 host 这一支没有任何前置判断（其余三个会看 role()[name+"_api"]），
   * 于是「没有宿主」直接呈现成「一块空框」。空框不表达任何东西：它不说明
   * 发生了什么，也不给下一步。所以这里改成——内容填不进去时，就不要 iframe，
   * 换成一个说得清的块。
   */
  /* 工作区容器。
   *
   * 这里的 iframe 是**空壳**：内容由 mountWorkspace() 用 srcdoc 填。
   * 所以只要 mountWorkspace() 不会执行（桥没接上），它就是一块有尺寸、
   * 没内容的方框——900px 高、带边框、里面一片空白。用户看到的正是这个。
   *
   * 过去的写法对 host 这一支没有任何前置判断（其余三个会看 role()[name+"_api"]），
   * 于是「没有宿主」直接呈现成「一块空框」。空框不表达任何东西：它不说明
   * 发生了什么，也不给下一步。
   *
   * 账号与会话负责初始化目录，因此只依赖已验证的宿主桥。
   * 其余工作区先检查目录，再检查当前对象对应的入口。
   */
  function workspace(name, tab="") {
    if(!["host","life","relationships","identity","persona"].includes(name)) return "";
    const title = name === "identity"
      ? ({network:"关系网",people:"用户",groups:"群聊"}[tab] || "关系管理")
      : {host:"账号与会话",life:"日记与生活",relationships:"人物与关系",persona:"人格问卷"}[name];
    const available = name === "host" ? bridgeReady : !loading && !!catalog;
    if(!available) {
      /* 不用 I()：那是 app.js 里的顶层 const，product-page.js 是独立 IIFE，
         访问不到。这一块也不需要图标——一句状态加两个出口就够了。 */
      return `<section class="product-workspace-blank">
        <div><strong>${title}</strong><p>${loading?"正在读取角色与会话…":"角色与会话目录暂未读到，这个功能页暂时无法加载。"}</p></div>
        <div class="product-workspace-blank-actions"><button class="button small" type="button" data-workspace-retry>重新读取</button><button class="button small" type="button" data-go="manage" data-manage="host">账号与会话</button></div>
      </section>`;
    }
    if(name==="identity" && !catalog.acquaintance_api) return empty("关系网入口尚未加载，请重新读取角色与会话。");
    if(name!=="host" && name!=="identity" && !role()?.[name+"_api"]) return empty(unavailable(name));
    return `<section class="product-workspace" data-workspace="${name}" data-workspace-tab="${e(tab)}" data-load-state="loading" aria-busy="true"><div class="product-workspace-loading" role="status" aria-live="polite"><span class="product-workspace-spinner" aria-hidden="true"></span><span data-workspace-status>正在打开${e(title)}…</span><button class="button small" type="button" data-workspace-load-retry hidden>重试</button></div><iframe title="${title}工作区" class="product-workspace-frame" sandbox="allow-scripts allow-forms"></iframe></section>`;
  }
  /* 工作区仍由唯一公开页面承载，页内路由保留原有宿主桥上下文。 */
  function workspaceAddress(name, tab="") {
    return {host:"#manage",life:"#life/diary"+(["reading","sleep","receipts"].includes(tab)?"/"+tab:""),
      relationships:"#manage/governance/relationship",identity:"#manage/identity",persona:"#manage/governance/questionnaire"}[name]||"";
  }
  function unmountWorkspace() {
    for(const context of workspaceFrames.values())clearTimeout(context.loadTimer);
    workspaceFrames.clear();
  }
  function setWorkspaceLoadState(owner, state, message) {
    const section=owner.parentElement;
    if(!section)return;
    section.dataset.loadState=state;
    section.setAttribute("aria-busy",String(state==="loading"));
    const status=section.querySelector("[data-workspace-status]");
    if(status&&message)status.textContent=message;
    const retry=section.querySelector("[data-workspace-load-retry]");
    if(retry)retry.hidden=state!=="error";
  }
  function requestWorkspaceBundle(name, source) {
    const script=document.createElement("script");
    script.src=source;
    script.async=true;
    return new Promise((resolve,reject)=>{
      script.onload=()=>{
        const html=window.CompanionWorkspaceBundle?.[name];
        if(typeof html==="string")resolve(html);
        else {
          const error=new Error("workspace bundle missing");
          error.workspaceCode="bundle_missing";
          reject(error);
        }
      };
      script.onerror=()=>{
        const error=new Error("workspace bundle request failed");
        error.workspaceCode="bundle_request_failed";
        reject(error);
      };
      document.head.appendChild(script);
    });
  }
  function workspaceLoadMessage(error) {
    if(error?.workspaceCode==="entry_auth")
      return "工作区授权刷新需要有效的 AstrBot 登录状态。请重新登录控制台后重试；已有数据不受影响。";
    if(error?.workspaceCode==="entry_request")
      return "AstrBot 没能生成新的工作区资源授权。请刷新陪伴小屋后重试；若仍失败，请检查插件 API 日志。已有数据不受影响。";
    if(error?.workspaceCode==="entry_network")
      return "工作区授权刷新请求未完成。请检查 AstrBot 控制台连接并重试；已有数据不受影响。";
    if(error?.workspaceCode==="entry_invalid")
      return "AstrBot 未返回有效的工作区入口信息。请刷新插件页后重试；已有数据不受影响。";
    if(error?.workspaceCode==="bundle_missing_token")
      return "工作区资源地址缺少访问授权。请重新打开陪伴小屋后重试；已有数据不受影响。";
    if(error?.workspaceAfterRefresh)
      return "授权已刷新，但工作区资源仍无法读取。请刷新插件页后重试；若仍失败，请检查 AstrBot 插件资源加载日志。已有数据不受影响。";
    if(error?.workspaceCode==="bundle_missing")
      return "工作区资源已返回，但内容不完整。请刷新插件页；若仍失败，请重新构建并加载插件包。已有数据不受影响。";
    return "工作区资源无法读取，请刷新插件页后重试；已有数据不受影响。";
  }
  async function refreshWorkspaceAssetUrl(source) {
    const assetUrl=new URL(source,window.location.href);
    const bridge=bridgeOf();
    if(!bridge?.apiGet) {
      const error=new Error("workspace page bridge unavailable");
      error.workspaceCode="entry_network";
      throw error;
    }
    let entry;
    try {
      entry=await bridge.apiGet("page/workspace-entry",{});
    } catch(cause) {
      const code=cause?.code||cause?.message;
      const error=new Error("workspace entry request failed");
      error.workspaceCode=code==="dashboard_identity_required"?"entry_auth"
        :code==="Network Error"||code==="Failed to fetch"?"entry_network":"entry_request";
      throw error;
    }
    const contentPath=entry?.content_path;
    if(typeof contentPath!=="string") {
      const error=new Error("workspace entry configuration is invalid");
      error.workspaceCode="entry_invalid";
      throw error;
    }
    const entryUrlWithToken=new URL(contentPath,window.location.href);
    const token=entryUrlWithToken.searchParams.get("asset_token");
    if(entryUrlWithToken.origin!==assetUrl.origin||!token
        ||!entryUrlWithToken.pathname.startsWith(`/api/plugin/page/content/${HOST}/`)) {
      const error=new Error("workspace entry configuration is invalid");
      error.workspaceCode="entry_invalid";
      throw error;
    }
    assetUrl.searchParams.set("asset_token",token);
    return assetUrl.href;
  }
  function loadWorkspaceBundle(name) {
    const existing=window.CompanionWorkspaceBundle?.[name];
    if(typeof existing==="string")return Promise.resolve(existing);
    if(workspaceBundleLoads.has(name))return workspaceBundleLoads.get(name);
    const template=document.querySelector(`template[data-workspace-bundle="${name}"]`);
    const source=template?.content?.querySelector("script")?.src;
    if(!source)return Promise.reject(new Error("工作区资源地址未发布"));
    const pending=(async()=>{
      try { return await requestWorkspaceBundle(name,source); }
      catch(error) {
        if(!new URL(source,window.location.href).searchParams.has("asset_token")) {
          error.workspaceCode="bundle_missing_token";
          throw error;
        }
        const refreshedSource=await refreshWorkspaceAssetUrl(source);
        try { return await requestWorkspaceBundle(name,refreshedSource); }
        catch(retryError) {
          retryError.workspaceAfterRefresh=true;
          throw retryError;
        }
      }
    })().catch(error=>{
      workspaceBundleLoads.delete(name);
      throw error;
    });
    workspaceBundleLoads.set(name,pending);
    return pending;
  }
  function startWorkspace(owner, context) {
    const title={host:"账号与会话",life:"日记与生活",relationships:"人物与关系",identity:"关系管理",persona:"人格问卷"}[context.kind]||"工作区";
    setWorkspaceLoadState(owner,"loading",`正在打开${title}…`);
    clearTimeout(context.loadTimer);
    context.loadTimer=setTimeout(()=>{
      if(workspaceFrames.get(owner)===context&&owner.parentElement?.dataset.loadState==="loading")
        setWorkspaceLoadState(owner,"error","工作区打开时间较长，页面暂时没有响应，可以重试。");
    },20000);
    loadWorkspaceBundle(context.kind).then(html=>{
      if(workspaceFrames.get(owner)!==context||!owner.isConnected)return;
      owner.srcdoc=html;
    }).catch(error=>{
      if(workspaceFrames.get(owner)!==context||!owner.isConnected)return;
      clearTimeout(context.loadTimer);
      setWorkspaceLoadState(owner,"error",workspaceLoadMessage(error));
    });
  }
  function workspaceContext(owner) {
    const kind=owner.parentElement.dataset.workspace;
    const selectedTarget=target(),selectedRole=role();
    const groupArchive=kind==="relationships"&&selectedTarget?.scope_kind==="group";
    return {key:scopeKey(),kind,tab:owner.parentElement.dataset.workspaceTab,
      scope_kind:selectedTarget?.scope_kind,
      accounts_api:catalog?.accounts_api,
      api:kind==="identity"?catalog?.acquaintance_api
        :groupArchive?selectedRole?.group_memory_api:selectedRole?.[kind+"_api"]};
  }
  function sendWorkspaceContext(owner, context) {
    owner.contentWindow.postMessage({channel:"companion-workspace",kind:"context",
      context:{pluginName:HOST,pageName:context.kind,tab:context.tab,
        display:{role_name:roleLabel(),subject_name:subjectLabel(target()),scope_kind:context.scope_kind,
          target_label:target()?targetLabel(target()):"当前会话"}}},"*");
  }
  function rebindWorkspaceContexts() {
    for (const [owner, previous] of workspaceFrames) {
      if (!owner.isConnected) {workspaceFrames.delete(owner);continue;}
      const context=workspaceContext(owner);
      context.lifetime=previous.lifetime;
      // Account and person directories have installation scope. Keep their DOM
      // and in-flight reads while the selected role's routing context changes.
      if (["host","identity"].includes(context.kind)) Object.assign(previous,context);
      else {
        clearTimeout(previous.loadTimer);
        workspaceFrames.set(owner,context);
      }
      if (!["host","identity"].includes(context.kind) && previous.key!==context.key) {
        setWorkspaceLoadState(owner,"loading","正在切换当前对象…");
        if(window.CompanionWorkspaceBundle?.[context.kind])owner.srcdoc=window.CompanionWorkspaceBundle[context.kind];
        else startWorkspace(owner,context);
      } else sendWorkspaceContext(owner,context);
    }
  }
  function mountWorkspace() {
    unmountWorkspace();
    if(!bridgeReady) return;
    for (const owner of document.querySelectorAll(".product-workspace-frame")) {
      const context=workspaceContext(owner);
      context.lifetime={};
      workspaceFrames.set(owner,context);
      if(window.CompanionWorkspaceBundle?.[context.kind])owner.srcdoc=window.CompanionWorkspaceBundle[context.kind];
      else startWorkspace(owner,context);
    }
  }
  document.addEventListener("click",event=>{
    const button=event.target?.closest?.("[data-workspace-load-retry]");
    if(!button)return;
    const owner=button.closest("[data-workspace]")?.querySelector(".product-workspace-frame");
    const previous=owner&&workspaceFrames.get(owner);
    if(!owner||!previous)return;
    clearTimeout(previous.loadTimer);
    const context={...previous,lifetime:{}};
    workspaceFrames.set(owner,context);
    startWorkspace(owner,context);
  });
  window.addEventListener("message",async event=>{
    const message=event.data;
    if(message?.channel!=="companion-workspace")return;
    const owner=[...workspaceFrames.keys()].find(frame=>event.source===frame.contentWindow);
    if(!owner)return;
    const context=workspaceFrames.get(owner);
    const valid=()=>{if(!bridgeReady||workspaceFrames.get(owner)!==context||!owner.isConnected
      || (!["host","identity"].includes(context.kind)&&scopeKey()!==context.key))throw fail("scope_changed");};
    const send=payload=>{if(workspaceFrames.get(owner)?.lifetime===context.lifetime&&owner.isConnected)
      owner.contentWindow.postMessage({channel:"companion-workspace",...payload},"*");};
    if(message.kind==="ready"){
      clearTimeout(context.loadTimer);
      setWorkspaceLoadState(owner,"ready");
      // Display labels never participate in identity checks or request routing.
      sendWorkspaceContext(owner,context);
      send({kind:"appearance",appearance:{...appearance}});return;
    }
    if(message.kind==="resize"){if(Number.isFinite(message.height))owner.style.height=Math.max(620,Math.min(2400,message.height+12))+"px";return;}
    if(message.kind==="host-mappings-updated"){
      if(context.kind!=="host")return;
      try { valid();await refreshDirectoryAfterMapping(); }
      catch(error) { send({kind:"host-mappings-refresh-failed",code:error?.code||"invalid_response"}); }
      return;
    }
    if(message.kind==="navigate"){
      valid();
      const target=message.target;
      if(["group-reply","group-memory"].includes(target?.tab)){
        const navigationFailure=text=>send({kind:"navigate-failed",tab:target.tab,message:text});
        const destination=catalog?.targets.find(item=>item.scope_kind==="group"
          &&item.platform_id===target.platform_id&&item.bot_id===target.bot_id
          &&item.account_id===target.account_id&&item.group_id===target.group_id);
        const role=destination?.roles.find(item=>item.role_id===target.role_id);
        const api=target.tab==="group-reply"?role?.reply_api:role?.group_memory_api;
        if(typeof api!=="string"){
          navigationFailure("这个群的人格或角色映射已经变化，请重新读取群聊目录后再试。");
          return;
        }
        try{
          await choose(destination.target_id,role.role_id,true);
        }catch{
          navigationFailure("打开本群配置时未能切换角色，请重新读取群聊目录后重试。");
          return;
        }
        if(selected.target_id===destination.target_id&&selected.role_id===role.role_id
          &&typeof window.navigate==="function"){
          window.navigate("manage",target.tab==="group-reply"?"decision":"governance",
            target.tab==="group-reply"?"":"relationship");
        }else{
          navigationFailure("未能进入该群的配置页，请重新读取群聊目录后重试。");
        }
        return;
      }
      const routes={
        host:{tab:"host"},
        setup:{tab:"setup"},
        models:{tab:"models"},
        perception:{tab:"perception",subtab:"config-screen"},
        selfProfile:{tab:"governance",subtab:"profile"},
      };
      const route=routes[target?.tab];
      if(!route) return;
      // Workspace actions are constrained to known cottage destinations. This
      // keeps an embedded member page from turning a display hint into an
      // arbitrary top-level navigation.
      if(typeof window.navigate==="function") window.navigate("manage", route.tab, route.subtab || "");
      return;
    }
    if(message.kind==="open-settings"){
      /* Only the identity directory may ask for its own settings dialog, and
         only for the fixed key this build renders. */
      if(context.kind!=="identity"||message.target!=="private-access")return;
      try{ valid(); }catch{ return; }
      window.CompanionIdentitySettings?.open?.(message.target);
      return;
    }
    if(message.kind!=="request"||typeof message.requestId!=="string"||message.requestId.length>80)return;
    const startedAt=Date.now();
    let method="",path="",timeoutMs=DEFAULT_API_TIMEOUT_MS;
    try {
      valid();
      method=message.method;path=message.path;
      if(!["GET","POST"].includes(method)||typeof path!=="string")throw fail("invalid_response");
      let mapped;
      if(context.kind==="host") {
        const allowed=(method==="GET"&&path==="host/status")
          || (method==="POST"&&path==="host/start") || path==="accounts"
          || (method==="POST"&&path==="accounts/model-provider")
          || (method==="POST"&&["accounts/window-link","accounts/window-unlink"].includes(path))
          || (method==="GET"&&path==="acquaintance"&&typeof catalog?.acquaintance_api==="string");
        if(!allowed)throw fail("invalid_response");
        mapped=path==="acquaintance"?relative(catalog.acquaintance_api):path;
      } else if(context.kind==="identity") {
        const allowed=(method==="GET"&&["acquaintance","acquaintance/authorization","acquaintance/settings","acquaintance/strangers"].includes(path))
           ||(method==="POST"&&["acquaintance/authorization","acquaintance/settings","acquaintance/identity","acquaintance/admin-name"].includes(path));
        if(!allowed)throw fail("invalid_response");
        mapped=relative(context.api)+path.slice("acquaintance".length);
      } else if(context.kind==="relationships"&&context.scope_kind==="group") {
        const suffix=path.slice("group-memory".length);
        const allowed=(method==="GET"&&["", "/safety", "/operation"].includes(suffix))
          ||(method==="POST"&&["/detail", "/retract", "/safety/review"].includes(suffix));
        if(!path.startsWith("group-memory")||!allowed)throw fail("invalid_response");
        mapped=relative(context.api)+suffix;
      } else {
        const name=context.kind;
        const personaStandardize=name==="persona"&&path==="persona/standardize"&&method==="POST";
        if(!personaStandardize&&((path!==name&&path!==name+"/command")||(method==="GET")!==(path===name)))
          throw fail("invalid_response");
        mapped=relative(context.api)+(personaStandardize?"/standardize":path===name?"":"/command");
      }
      // Preserve original response envelopes and error reasons for each workspace.
      timeoutMs=requestTimeout({timeoutMs:message.timeoutMs});
      const result=await bounded(method==="GET"?bridge.apiGet(mapped,message.body):bridge.apiPost(mapped,message.body),timeoutMs);valid();
      send({kind:"response",requestId:message.requestId,ok:true,data:result});
    } catch(error){
      const code=typeof error?.code==="string"?error.code.slice(0,80):"";
      const rawReason=[error?.reason,error?.message].find(value=>typeof value==="string"&&value.trim());
      const reason=rawReason?rawReason.replace(/[\u0000-\u001f\u007f]/g," ").slice(0,1200):"";
      const expectedCode=code&&(Object.hasOwn(errors,code)||/(?:_invalid|_required|_denied|_conflict|_changed|_not_found|_unavailable|_running|_forbidden|_expired|_not_initialized)$/.test(code));
      if(!error?.confirmed&&(code==="bridge_timeout"||!expectedCode))console.warn("[CompanionV2] Workspace request failed",{
        method,endpoint:diagnosticEndpoint(path),elapsed_ms:Date.now()-startedAt,
        timeout_ms:timeoutMs,code:code||"unknown",confirmed:false,
      });
      send({kind:"response",requestId:message.requestId,ok:false,
        error:errors[code]||reason||"请求未完成，请刷新核对当前状态后重试。",
        code:code||undefined,reason:reason||undefined,confirmed:error?.confirmed===true});
    }
  });
  function extensionsMarkup(ids) {
    const extensions = manifest?.extensions?.filter(p=>!ids||ids.includes(p.capability_id))||[];
    if(!extensions.length) return empty(loading?"正在读取能力目录…":"尚未读取 V2 内置拓展目录。请刷新，或在账号与会话中检查连接。");
    return `<div class="capabilities-grid">${extensions.map(p=>`<article class="panel capability"><div class="capability-top"><div><h3>${e(p.label)}</h3><small>V2 本体内置 · ${e(p.version||"未读取")}</small></div><span class="pill ${p.integration_state==="connected"?"":"gray"}">${e(labels[p.integration_state]||"状态未知")}</span></div><p>${e(p.description)}</p><div class="capability-footer"><span>${e(p.reason)}</span><button class="button small" data-product-provider="${e(p.capability_id)}">查看入口</button></div></article>`).join("")}</div>`;
  }
  function providersMarkup(ids) {
    const providers = manifest?.providers?.filter(p=>!ids||ids.includes(p.capability_id))||[];
    if(!providers.length) return "";
    return `<div class="capabilities-grid">${providers.map(p=>`<article class="panel capability"><div class="capability-top"><div><h3>${e(p.label)}</h3><small>${e(p.version||"未安装")}</small></div><span class="pill ${p.integration_state==="connected"?"":"gray"}">${e(labels[p.integration_state]||"状态未知")}</span></div><p>${e(p.description)}</p><div class="capability-footer"><span>${e(p.reason)}</span><button class="button small" data-product-provider="${e(p.capability_id)}">查看入口</button></div></article>`).join("")}</div>`;
  }
  function capabilitiesMarkup() {
    const external = providersMarkup();
    return `<div class="settings-heading"><div><h2>能力目录</h2></div></div><div class="product-domain-grid">${(manifest?.core||[]).map(item=>`<article><strong>${e(item.label)}</strong><p>${e(item.description)}</p><span>已包含</span></article>`).join("")}</div><h3 class="product-section-title">V2 内置拓展</h3>${extensionsMarkup()}${external?`<h3 class="product-section-title">外部兼容状态</h3>${external}`:""}`;
  }
  function providerAddress(id, preferredPage="") {
    if (bundledExtensions[id]) return "";
    const item=manifest?.providers?.find(p=>p.capability_id===id);
    if(!item?.installed||typeof item.plugin_id!=="string"||!item.plugin_id)return "";
    const pages=(Array.isArray(item.pages)?item.pages:[]).filter(page=>typeof page==="string"&&page.trim());
    const page=pages.includes(preferredPage)?preferredPage:pages[0];
    const route=page?`/#/plugin-page/${encodeURIComponent(item.plugin_id)}/${encodeURIComponent(page)}`:"/#/extension/plugins";
    try{return new URL(route,window.location.href).href;}catch(_){return "";}
  }
  function providerLinks(item) {
    if(item.capability_id === "content") return `<div class="dialog-actions"><button type="button" class="button small" data-go="manage" data-manage="output" data-output-target="creation">创作与作品</button></div>`;
    if(["reality","together","game","live"].includes(item.capability_id)) return `<div class="dialog-actions"><button type="button" class="button small" data-product-provider="${e(item.capability_id)}">设置与运行</button></div>`;
    const bundled = item.owner === "companion_v2" || Boolean(bundledExtensions[item.capability_id]);
    if(!bundled && !item.installed)return "";
    const pages=Array.isArray(item.pages)?item.pages.filter(page=>typeof page==="string"&&page.trim()):[];
    if(!pages.length&&["content","image"].includes(item.capability_id)) {
      const image=item.capability_id==="image";
      return `<div class="dialog-actions"><button type="button" class="button small" data-go="manage" data-manage="output" data-output-target="${image?"materials":"creation"}"><i data-lucide="${image?"images":"notebook-pen"}" aria-hidden="true"></i>${image?"产出相册与衣柜":"创作与作品"}</button></div>`;
    }
    const entries=pages.length?pages:[""];
    return entries.map((page,index)=>{
      const address=providerAddress(item.capability_id,page);
      const id=`provider-page-${item.capability_id}-${index}`;
      return address?`<div class="perception-address"><label for="${e(id)}">${page?e(page):"插件管理"}页面地址</label><div><input id="${e(id)}" readonly value="${e(address)}" aria-label="${page?e(page):"插件管理"}页面地址"><button type="button" class="button small" data-copy-address="${e(id)}" title="复制页面地址"><i data-lucide="copy" aria-hidden="true"></i><span>复制地址</span></button></div></div>`:"";
    }).join("");
  }
  function providerDetail(id) {
    const item=manifest?.extensions?.find(p=>p.capability_id===id) || manifest?.providers?.find(p=>p.capability_id===id);
    const bundled=Boolean(manifest?.extensions?.some(p=>p.capability_id===id));
    return {title:item?.label||"拓展能力",body:item?`<p>${e(item.description)}</p><p class="product-muted">${e(item.reason)}</p><dl class="definition-list"><div><dt>${bundled?"V2 内置版本":"插件版本"}</dt><dd>${e(item.version||"尚未读取")}</dd></div>${bundled?`<div><dt>装配版本</dt><dd>${e(item.installed_package_version||"未装配")}</dd></div>`:""}<div><dt>配置开关</dt><dd>${item.enabled===true?"开启":item.enabled===false?"关闭":"尚未读取"}</dd></div><div><dt>运行状态</dt><dd>${item.runtime_ready===true?"已就绪":item.runtime_ready===false?"尚未就绪":"请重新读取"}</dd></div></dl>${providerLinks(item)}`:empty("尚未读取此能力状态，请刷新能力目录。")};
  }
  function growthMarkup() {
    const page=growthPage||data.growth;
    return `<div class="settings-heading"><div><h2>目标与成长</h2></div><button class="button small" data-growth-load ${growthBusy?"disabled":""}>刷新</button></div><p id="growth-notice" role="status"></p>${page?`<div class="product-growth-list">${page.items.map((item,index)=>`<article class="panel content-panel"><div class="section-toolbar"><h3>${e(item.title||"来源暂不可用")}</h3><span class="pill">${e(states[item.state]||"状态待核对")}</span></div><p>${e(item.description||"")}</p><p>${e(item.progress_note||"")}</p><p class="product-muted">${item.kind==="goal"?"目标":"技能"} · ${item.source_count} 个来源 · 版本 ${item.revision}</p><div class="dialog-actions">${item.actions.map(action=>`<button class="button small" data-growth-index="${index}" data-growth-action="${action}" ${growthBusy?"disabled":""}>${({confirm:"确认",revise:"修订",pause:"暂停",resume:"继续",complete:"已完成",withdraw:"撤回",retire:"归档"})[action]||"其他操作"}</button>`).join("")}</div></article>`).join("")||empty("暂无目标或技能记录。")}</div><div class="dialog-actions"><button class="button small" data-growth-first>返回首批</button><button class="button small" data-growth-next ${!page.next_cursor||growthBusy?"disabled":""}>下一批</button></div>`:empty(unavailable("growth"))}<dialog id="growth-confirm" class="detail-dialog"><form id="growth-form" class="product-growth-form"><h2 id="growth-confirm-title"></h2><p id="growth-confirm-description"></p><div id="growth-edit-fields"><label>标题<input name="title" maxlength="200" required></label><label>描述<textarea name="description" rows="3"></textarea></label><label>进展<textarea name="progress_note" rows="3"></textarea></label></div><p id="growth-command-error" role="status"></p><div class="dialog-actions"><button type="submit" class="button primary">确认保存</button><button type="button" class="button" data-growth-close>取消</button></div></form></dialog>`;
  }
  async function loadGrowth(cursor=null) {
    if(growthBusy||!role()?.growth_api)return;
    const ticket=generation;growthBusy=true;
    try {const result=await api("GET",relative(role().growth_api),{limit:20,...(cursor?{after_id:cursor}:{})});if(ticket===generation){growthPage=result;growthCursor=cursor;growthBusy=false;changed();}}
    catch(error){const el=document.getElementById("growth-notice");if(ticket===generation&&el)el.textContent=errors[error.code]||"读取失败，请刷新核对。";}
    finally{growthBusy=false;}
  }
  let growthCommand=null;
  function openGrowth(action,index) {
    const item=(growthPage||data.growth)?.items[index];if(!item?.actions.includes(action))return;
    const dialog=document.getElementById("growth-confirm"),form=document.getElementById("growth-form");
    growthCommand={item,action,key:scopeKey(),path:relative(role().growth_api)};
    document.getElementById("growth-confirm-title").textContent=({revise:"修订记录",confirm:"确认成长",complete:"标记为已完成",pause:"暂停目标",resume:"继续目标",withdraw:"撤回提议",retire:"归档记录"})[action];
    document.getElementById("growth-confirm-description").textContent=item.title||"当前记录";
    document.getElementById("growth-edit-fields").hidden=action!=="revise";
    for(const field of ["title","description","progress_note"]) {form.elements[field].value=item[field]||"";form.elements[field].disabled=action!=="revise";}
    document.getElementById("growth-command-error").textContent="";dialog.showModal();
  }
  document.addEventListener("submit",async event=>{
    if(event.target.id!=="growth-form")return;event.preventDefault();
    if(growthBusy||!growthCommand||scopeKey()!==growthCommand.key)return;
    const command=growthCommand, form=event.target,dialog=document.getElementById("growth-confirm"),ticket=generation;
    const payload={action:command.action,growth_id:command.item.growth_id,expected_revision:command.item.revision,view_token:command.item.view_token};
    if(command.action==="revise")for(const field of ["title","description","progress_note"])payload[field]=form.elements[field].value;
    const signature=command.path+JSON.stringify(payload);
    if(!pendingGrowth.has(signature))pendingGrowth.set(signature,{...payload,operation_key:crypto.randomUUID()});
    growthBusy=true;form.querySelectorAll("button").forEach(el=>el.disabled=true);
    try{await api("POST",command.path+"/command",pendingGrowth.get(signature));if(ticket!==generation)return;pendingGrowth.delete(signature);dialog.close();growthBusy=false;await loadGrowth(growthCursor);}
    catch(error){if(ticket===generation)document.getElementById("growth-command-error").textContent="保存未完成，请核对后重试；重试沿用本次请求。";}
    finally{growthBusy=false;form.querySelectorAll("button").forEach(el=>el.disabled=false);}
  });
  document.addEventListener("change",event=>{
    if(event.target.id==="product-role")chooseRole(event.target.value);
  });
  document.addEventListener("click",event=>{
    const el=event.target.closest("button");if(!el)return;
    if(el.hasAttribute("data-growth-load"))loadGrowth(growthCursor);
    if(el.hasAttribute("data-growth-first"))loadGrowth();
    if(el.hasAttribute("data-growth-next"))loadGrowth((growthPage||data.growth)?.next_cursor);
    if(el.dataset.growthAction)openGrowth(el.dataset.growthAction,Number(el.dataset.growthIndex));
    if(el.hasAttribute("data-growth-close"))document.getElementById("growth-confirm").close();
  });
  /* 「生活观察」那几个页签要按真实字段取值，所以在这里露出三个读取器，
     页面层不再自己编一份示例。三者都返回 undefined/非数组表示「读不到」，
     由 unavailable() 给出原因；返回空数组表示「读到了，但还没有记录」——
     这两件事在界面上要说得不一样。 */
  const lifeActivities = () => Array.isArray(data.life?.activities) ? data.life.activities : null;
  const lifeJournals  = () => Array.isArray(data.life?.journals) ? data.life.journals : null;
  const stateValues   = () => data.state?.state?.values || null;
  const stateLabel = value => states[value] || "";
  const unavailableReason = name => unavailable(name);
  return {hosted,unwrap,connect,refreshSummary,refreshIntegrations,manageExtension,choose,chooseRole,selection:()=>({...selected}),scopeKey,role,target,roleTarget,roleChoices,identity,connection,renderScope,scopeVisible,
    /* 首次使用引导走同一套桥接与错误翻译，别让它自己再实现一遍超时与判据。 */
    request:api,acquaintanceDirectory,refreshDirectory:refreshDirectoryAfterMapping,
    errorLabel:code=>errors[code]||"当前设置未能确认，请刷新并核对账号、人格与模型。",
    theme:currentTheme,setTheme,appearance:()=>({...appearance}),setAppearance,resetAppearance,dimensions,
    displayPreferences:displayPreferencesSnapshot,displayPreferencesChanged,
    themeLight,lightOf:id=>themeLight[id]||"day",followLight:()=>followLight,setFollowLight,syncLight,
    targets:()=>catalog?.targets||[],
    directory:()=>catalog,
    /* 账号和宿主人格是角色准备目录，不是聊天窗口目录。迁移向导用它们
       展示多 Bot / 多角色拓扑，再用 targets 给可接收迁移的角色补上真实路由。 */
    accounts:()=>catalog?.accounts||[], personas:()=>catalog?.personas||[],
    targetLabel,subjectLabel,platformLabel,accountLabel,roleLabel,scopeLabel,actorLabel,executorLabel,
    get manifest(){return manifest;},get data(){return data;},stateCard,scheduleCard,capabilityCard,growthCard,memoryCard,
    workspace,unmountWorkspace,mountWorkspace,workspaceAddress,providerAddress,extensionsMarkup,providersMarkup,capabilitiesMarkup,providerDetail,growthMarkup,
    extension:id=>manifest?.extensions?.find(p=>p.capability_id===id),
    provider:id=>manifest?.providers?.find(p=>p.capability_id===id),
    capability:id=>manifest?.extensions?.find(p=>p.capability_id===id)||manifest?.providers?.find(p=>p.capability_id===id),
    statusLabel:id=>labels[id]||"状态未知",unavailable,
    workspaceDiagnosis,workspaceHint,
    when,lifeActivities,lifeJournals,stateValues,stateLabel,unavailableReason,
    unavailableCode:name=>failureCodes[name]||"",
    /* 各页共用一个「等桥注进来」的实现。桥的注入晚于脚本执行是正常时序，
       谁都不该自己再写一遍超时与判据——写岔一次就是一个假性未连接。 */
    whenBridgeReady,bridgeNow,bridgeFor,
    /* 暴露给测试：endpoint 口径只有这一处定义，它被改错过一次
       （带上插件目录名 → 宿主拼两次 → 整片「未找到该路由」）。 */
    relative};
})();
