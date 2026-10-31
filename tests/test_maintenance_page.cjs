const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/maintenance-page.js'), 'utf8');
const styles = fs.readFileSync(path.resolve(__dirname, '../pages/陪伴小屋/maintenance-page.css'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const localDay = value => {
  const date = value || new Date();
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-');
};
const localStamp = value => {
  const date = value || new Date();
  return `${localDay(date)}T${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}`;
};
const daily = (day, values = {}) => ({
  day, calls: 0, tokens: 0, cost_microusd: 0, unconfirmed_calls: 0, failures: 0,
  unknown_calls: 0, active_calls: 0, cache_reported_calls: 0, cache_telemetry_calls: 0,
  cache_input_tokens: 0, cached_input_tokens: 0, cache_creation_tokens: 0,
  cache_other_input_tokens: 0, input_tokens: 0, output_tokens: 0,
  avg_latency_ms: null, providers: [], tasks: [], ...values,
});

function harness(responses) {
  const listeners = {};
  const requests = [];
  const csvExports = [];
  const downloads = [];
  const confirmations = [];
  const audioPreviews = [], revokedAudioUrls = [];
  const timers = new Map();
  let nextTimerId = 1;
  let modelReads = 0;
  const bridge = {
    apiGet: async route => {
      requests.push(['GET', route]);
      if (route.endsWith('/memory')) {
        const value = responses.memory;
        return typeof value === 'function' ? value(requests) : value || {rss_bytes: 64 * 1024 * 1024,
          rss_source: 'test', python_tracing_active: false, python_heap_current_bytes: null};
      }
      if (route.endsWith('/maintenance')) return responses.maintenance;
      if (route.endsWith('/balance')) return responses.balance || {
        configured: false, available: false, provider: 'newapi', provider_label: 'NewAPI',
        currency_label: '额度', state: 'not_configured',
      };
      if (route.endsWith('/voice')) return responses.voice || {providers: []};
      if (route.endsWith('/image')) return responses.image || {actions: []};
      if (route === '/model') {
        const value = Array.isArray(responses.model)
          ? responses.model[Math.min(modelReads++, responses.model.length - 1)]
          : responses.model;
        return typeof value === 'function' ? value(modelReads++) : value || {tasks: [], providers: []};
      }
      throw new Error('unexpected GET ' + route);
    },
    apiPost: async (route, body) => {
      requests.push(['POST', route, body]);
      const value = responses.posts?.[route];
      const result = typeof value === 'function' ? value(body, requests) : value;
      return result || {};
    },
  };
  const window = {
    CompanionProduct: {
      role: () => ({model_api: '/astrbot_plugin_private_companion/model', role_id: 'role-a'}),
      bridgeNow: () => bridge,
    },
    lucide: {createIcons() {}},
    CompanionConfirm: request => {
      confirmations.push(typeof request === 'string' ? request : request?.message);
      return Promise.resolve(responses.confirmations?.length ? responses.confirmations.shift() : true);
    },
  };
  const root = {
    innerHTML: '',
    addEventListener(type, handler) { listeners[type] = handler; },
    removeEventListener(type) { delete listeners[type]; },
    querySelector() { return null; },
    click(selector, dataset = {}) {
      listeners.click({target: {closest: value => value === selector ? {dataset} : null}});
    },
    change(selector, value) {
      listeners.change({target: {
        value,
        matches: candidate => candidate === selector,
      }});
    },
    input(value, selector = '[data-call-search]') {
      listeners.input({target: {
        value, selectionStart: value.length, selectionEnd: value.length,
        matches: candidate => candidate === selector,
      }});
    },
    pointer(type, target, relatedTarget = null) {
      listeners[type]?.({type, target, relatedTarget});
    },
  };
  window.setTimeout = (callback, delay = 0) => {
    if (!delay) { callback(); return 0; }
    const id = nextTimerId++;
    timers.set(id, {callback, delay});
    return id;
  };
  window.clearTimeout = id => timers.delete(id);
  const document = {createElement: () => ({click() { downloads.push(this.download); }})};
  const url = {createObjectURL: blob => {
    if (blob.type.startsWith('audio/')) { audioPreviews.push(blob); return 'blob:maintenance-audio'; }
    csvExports.push(blob.parts.join(''));
    return 'blob:maintenance';
  }, revokeObjectURL: value => revokedAudioUrls.push(value)};
  class BlobMock { constructor(parts, options = {}) { this.parts = parts; this.type = options.type || ''; } }
  window.URL = url;
  window.Blob = BlobMock;
  window.atob = value => Buffer.from(value, 'base64').toString('binary');
  vm.runInNewContext(source, {window, document, URL: url, Blob: BlobMock, Intl, Date, Number, Object, String, Math,
    Array, Set, Promise});
  return {
    page: window.CompanionMaintenancePage, requests, root, exports: csvExports, downloads, confirmations, audioPreviews, revokedAudioUrls,
    advanceTimers: elapsed => {
      for (const [id, timer] of [...timers]) {
        if (timer.delay <= elapsed) { timers.delete(id); timer.callback(); }
      }
    },
  };
}

test('daily token trend stacks usage, scales the axis, and breaks totals at unknown days', async () => {
  const today = localDay();
  const dayOffset = offset => localDay(new Date(Date.now() + offset * 86400000));
  const rows = [
    daily(dayOffset(-3), {calls: 1, tokens: 6100, input_tokens: 4800, cached_input_tokens: 1800,
      cache_reported_calls: 1, output_tokens: 1300}),
    daily(dayOffset(-2), {calls: 1, tokens: 7400, input_tokens: 5900, cached_input_tokens: 2400, output_tokens: 1500}),
    daily(dayOffset(-1), {calls: 1, tokens: null, input_tokens: null, cached_input_tokens: null, output_tokens: null}),
    daily(today, {calls: 1, tokens: 8500, input_tokens: 6500, cached_input_tokens: 2500, output_tokens: 2000}),
  ];
  const h = harness({maintenance: {
    generated_at: today + 'T12:00:00', summary: {calls: 3, tokens: 22000}, totals: {calls: 3, tokens: 22000},
    daily: rows, history: rows, recent: [], budget: {},
  }});
  h.page.mount(h.root);
  await flush();
  const dailyTrend = h.root.innerHTML.split('data-trend-panel="daily"')[1].split('data-trend-panel="hourly"')[0];
  assert.match(dailyTrend, /data-series="cached">缓存输入/);
  assert.match(dailyTrend, /data-series="input">其余输入/);
  assert.match(dailyTrend, /data-series="output">输出/);
  assert.match(dailyTrend, /data-series="total">总计/);
  assert.match(dailyTrend, /class="maint-chart-axis"[\s\S]*?>10K<\/span>/);
  assert.match(dailyTrend, /class="maint-chart-total-line"[^>]*points="[^"]+"/);
  assert.match(dailyTrend, /title="[^"]+缓存输入 2,500 · 其余输入（含缓存未知） 4,000 · 输出 2,000"/);
  assert.match(dailyTrend, /title="[^"]+缓存输入 1,800 · 未命中缓存输入 3,000 · 输出 1,300"/);
  const slots = Number(dailyTrend.match(/data-count="(\d+)"/)[1]);
  assert.equal((dailyTrend.match(/class="maint-chart-total-dot"/g) || []).length, slots - 1);
  assert.equal((dailyTrend.match(/class="maint-chart-total-line"/g) || []).length, 1);
  assert.match(styles, /\.maint-token-trend-panel \.maint-chart-day:hover \.maint-chart-day-point/);
  assert.match(styles, /\.maint-token-trend-panel \.maint-chart-day small\[aria-hidden="true"\] \{ visibility:hidden; \}/);
  assert.match(styles, /@keyframes maint-chart-line-in/);
  assert.match(styles, /@keyframes maint-chart-dot-in/);
  assert.match(styles, /@keyframes maint-chart-day-point-in/);
});

test('core efficiency and cache views retain the native measurement basis', async () => {
  const at = localStamp(), today = localDay();
  const coreUsage = {execution_path: 'astrbot_core', cost_availability: 'unavailable', summary: {calls: 2, tokens: 1200},
    daily: [daily(today, {calls: 2, tokens: 1200, cost_microusd: null})], recent: [
      {kind: 'conversation.astrbot_core', execution_path: 'astrbot_core', at, state: 'returned', tokens: 1200,
        input_tokens: 1000, output_tokens: 200, cached_input_tokens: null, cost_microusd: null},
    ], coverage: {turns: 1, observed_requests: 2, fallback_usage_observations: 0, partial_request_observation_turns: 1}, cache_diagnostics: {}};
  const report = {summary: {samples: 1, completed: 1, send_returned_samples: 1,
      median_first_send_returned_ms: 2200, median_end_to_end_send_returned_ms: 3100},
    coverage: {core_samples: 1, core_send_returned: 1, core_request_observation: 1},
    by_source: {astrbot_core: {samples: 1}, legacy_v2_conversation: {completed: 0}},
    recent: [{turn_id: 'core-turn-one', execution_path: 'astrbot_core', outcome: 'completed', received_at: at, input_count: 1,
      model_calls: 2, model_call_count_available: true, model_call_count_complete: false,
      usage_observation_count: 3, send_returned_parts: 2, part_count: 2,
      timestamps: {received_at: at, waiting_llm_at: at, first_send_returned_at: at, last_send_returned_at: at},
      stages: {first_send_returned_ms: 2200, end_to_end_send_returned_ms: 3100,
        plugin_preparation_ms: 100, model_request_ms: 900, waiting_llm_to_request_hook_ms: 600,
        request_hook_to_agent_ms: 200, tool_ms: 300, agent_ms: 1800, send_ms: 400},
      trace: {model_calls: [{position: 3, state: 'returned', measurement_basis: 'runner_aggregate', tokens: 1200}],
        preparation_steps: [{name: 'context_build', state: 'returned', started_at: at, elapsed_ms: 100}],
        materials: [{owner: 'private_companion.v2', purpose: 'memory', state: 'ready', elapsed_ms: 35, chars: 1200}],
        offered_tools: ['read_file'], tool_calls: [], offered_tool_count: 4}}], daily: []};
  const h = harness({maintenance: {generated_at: at, summary: {calls: 2, tokens: 1200},
      daily: [], history: [], recent: [],
      usage_sources: {astrbot_core: coreUsage}, reply_efficiency: report}});
  h.page.mount(h.root, {activeView: 'efficiency'});
  await flush();
  const efficiency = h.root.innerHTML.split('data-maint-view="efficiency"')[1].split('data-maint-view="recent"')[0];
  assert.match(efficiency, /核心发送已返回/);
  assert.match(efficiency, /平台确认未观测/);
  assert.match(efficiency, /首次发送返回<\/span><strong>2.2 秒/);
  assert.match(efficiency, /已观测请求 2 次 · 完整次数未知/);
  assert.match(efficiency, /用量观察 3 条/);
  assert.match(efficiency, /用量观察 3<\/strong>/);
  assert.match(efficiency, /单次回复分项耗时/);
  assert.match(efficiency, /已观测模型请求累计<\/span><strong>900 毫秒/);
  assert.match(efficiency, /等待 LLM 至插件准备<\/span><strong>600 毫秒/);
  assert.match(efficiency, /插件准备至 Agent<\/span><strong>200 毫秒/);
  assert.match(efficiency, /进入 AstrBot 请求队列/);
  assert.match(efficiency, /Agent 总时长<\/span><strong>1.8 秒/);
  assert.match(efficiency, /插件准备 · 素材准备/);
  assert.match(efficiency, /记忆召回/);
  assert.match(efficiency, /已注入 · 35 毫秒 · 1,200 字符/);
  assert.match(efficiency, /read_file/);
  assert.doesNotMatch(efficiency, /模型调用 2 次/);
  assert.doesNotMatch(efficiency, /2 \/ 2 段送达|1 条完整送达/);
  let expanded = true;
  h.root.querySelectorAll = selector => selector === 'details[data-reply-sample]'
    ? [{dataset: {replySample: 'core-turn-one'}, get open() { return expanded; }}] : [];
  h.root.click('[data-refresh]');
  await flush();
  assert.match(h.root.innerHTML, /data-reply-sample="core-turn-one" open/);
  expanded = false;
  h.root.click('[data-refresh]');
  await flush();
  assert.doesNotMatch(h.root.innerHTML, /data-reply-sample="core-turn-one" open/);
  h.root.click('[data-token-source]', {tokenSource: 'astrbot_core'});
  h.root.click('[data-maint-nav]', {maintNav: 'cache'});
  const cache = h.root.innerHTML.split('data-maint-view="cache"')[1].split('data-maint-view="efficiency"')[0];
  assert.match(cache, /SDK 默认缓存 0 不作为缓存实报/);
  h.root.click('[data-maint-nav]', {maintNav: 'overview'});
  const overview = h.root.innerHTML.split('data-maint-view="overview"')[1].split('data-maint-view="cache"')[0];
  assert.match(overview, /核心费用未提供/);
  assert.match(overview, /2 次实际请求/);
  assert.match(overview, /1 回合仅观测部分请求/);
  h.root.click('[data-export-tokens]');
  const scopeRow = h.exports[0].split('\r\n')[1].split(',');
  assert.equal(scopeRow[9], '""');
  assert.match(h.exports[0], /已计价部分微美元（未知留空）/);
  h.page.unmount();
});

test('reply efficiency shows a chronological timeline and separates cumulative latency', async () => {
  const received = localStamp(new Date(Date.now() - 8000));
  const lastInput = localStamp(new Date(Date.now() - 7000));
  const modelStarted = localStamp(new Date(Date.now() - 6000));
  const modelFinished = localStamp(new Date(Date.now() - 4000));
  const pluginStarted = localStamp(new Date(Date.now() - 4700));
  const pluginFinished = localStamp(new Date(Date.now() - 4200));
  const secondModelStarted = localStamp(new Date(Date.now() - 4100));
  const dispatched = localStamp(new Date(Date.now() - 3000));
  const firstDelivered = localStamp(new Date(Date.now() - 2000));
  const delivered = localStamp(new Date(Date.now() - 1000));
  const h = harness({maintenance: {
    generated_at: localStamp(), summary: {}, totals: {}, daily: [], history: [], recent: [],
    reply_efficiency: {
      generated_at: localStamp(), window_days: 7,
      summary: {samples: 3, completed: 1, partial: 1, median_end_to_end_ms: 7000,
        p95_end_to_end_ms: 7000, median_first_reply_ms: 6000, median_pre_model_ms: 2000,
        median_last_input_to_model_ms: 1000, median_model_ms: 2000, median_post_model_ms: 1000,
        median_delivery_ms: 2000, laya_calls: 4, laya_skipped: 2, laya_failed: 1},
      coverage: {received: 3, model_started: 2, model_finished: 2, first_dispatched: 2,
        first_delivered: 2, final_delivered: 1},
      daily: [{day: localDay(), samples: 1, completed: 1, median_end_to_end_ms: 7000}],
      recent: [
        {received_at: received, received_time_source: 'input_event', outcome: 'completed', input_count: 3, part_count: 2, delivered_parts: 2,
          model_calls: 2, timestamps: {received_at: received, flow_due_at: localStamp(new Date(Date.now() - 6500)),
            generation_started_at: localStamp(new Date(Date.now() - 6300)), first_model_started_at: modelStarted,
            last_input_received_at: lastInput, last_model_finished_at: modelFinished,
            first_dispatched_at: dispatched, first_delivered_at: firstDelivered, last_delivered_at: delivered},
          stages: {pre_model_ms: 2000, last_input_to_model_ms: 1000, model_ms: 2000,
            post_model_ms: 1000, first_reply_ms: 6000, delivery_ms: 2000, end_to_end_ms: 7000},
          trace: {input_wait_ms: 500, queue_wait_ms: 200, preparation_ms: 300,
            materials: [{owner: 'memory.recall', purpose: 'recent_memory', state: 'ready', payload: 'private body'}],
            offered_tool_count: 1, offered_tools: ['calendar.query'],
            model_calls: [{position: 0, state: 'generated', started_at: modelStarted, elapsed_ms: 1250,
              provider: 'sensenova', tokens: null, input_tokens: null, output_tokens: null,
              failure_code: 'provider_request_failed', response_diagnostics: {version: 2,
                completion_present: false, finish_reason: 'missing', parse_state: 'not_received',
                raw: null, parsed: null, visible: null, failure_code: 'provider_request_failed',
                error_type: 'RateLimitError', error_code: 'provider_rate_limited', status_code: 429}},
              {position: 1, state: 'generated', started_at: secondModelStarted, elapsed_ms: 100}],
            tool_calls: [{sequence: 1, action: 'calendar.query', kind: 'extension', state: 'succeeded',
              started_at: pluginStarted, finished_at: pluginFinished, elapsed_ms: 600,
              extension_started_at: pluginStarted, extension_finished_at: pluginFinished, extension_elapsed_ms: 500}]}},
        {received_at: received, received_time_source: 'input_event', outcome: 'partial', input_count: 2, part_count: 3,
          delivered_parts: 1, failed_parts: 1, unknown_parts: 1,
          model_calls: 1, timestamps: {received_at: received, last_input_received_at: lastInput,
            first_model_started_at: modelStarted, last_model_finished_at: modelFinished,
            first_dispatched_at: dispatched, first_delivered_at: firstDelivered},
          stages: {pre_model_ms: 2000, last_input_to_model_ms: 1000, model_ms: 2000,
            post_model_ms: 1000, first_reply_ms: 6000, delivery_ms: null, end_to_end_ms: null}},
        {received_at: received, received_time_source: 'batch_created', outcome: 'unknown', input_count: 1, part_count: 1, delivered_parts: 0,
          model_calls: 1, active_model_calls: 1, timestamps: {received_at: received},
          stages: {pre_model_ms: null, model_ms: 1000, post_model_ms: null, first_reply_ms: null,
            delivery_ms: null, end_to_end_ms: null}},
      ],
    },
  }, voice: {providers: []}, image: {actions: []}});
  const changedViews = [];
  h.page.mount(h.root, {activeView: 'efficiency', onViewChange: view => changedViews.push(view)});
  await flush();
  assert.match(h.root.innerHTML, /data-maint-nav="efficiency" aria-current="page">回复效率/);
  assert.match(h.root.innerHTML, /<h2>近期回复效率<\/h2>/);
  assert.doesNotMatch(h.root.innerHTML, /data-export-tokens/);
  assert.doesNotMatch(h.root.innerHTML, /时间缺失时显示/);
  const page = h.root.innerHTML.split('data-maint-view="efficiency"')[1].split('data-maint-view="recent"')[0];
  assert.match(page, /P50 端到端/);
  assert.match(page, /P50 末条消息至模型/);
  assert.match(page, /P50 模型往返区间/);
  assert.match(page, /Laya 4 次调用 \/ 2 次跳过 \/ 1 次失败/);
  assert.match(page, /首段响应/);
  assert.match(page, /完整送达/);
  assert.match(page, /3 条输入 · 2 \/ 2 段送达 · 模型调用 2 次 · 插件 1 次/);
  assert.match(page, /末条消息/);
  assert.match(page, /7\.0 秒/);
  assert.match(page, /起点累计/);
  assert.match(page, /从首条消息起算/);
  assert.match(page, /从批次创建起算/);
  assert.match(page, /data-stage="input_wait" data-observed="true"/);
  assert.match(page, /data-stage="queue_wait" data-observed="true"/);
  assert.match(page, /data-stage="preparation" data-observed="true"/);
  assert.match(page, /失败 1 段 · 回执未知 1 段/);
  assert.match(page, /data-stage="model"/);
  assert.match(page, /data-stage="delivery"/);
  assert.match(page, /data-event="first_delivered_at"/);
  assert.match(page, /首段回执/);
  assert.match(page, /末次成功回执/);
  assert.match(page, /完整回执/);
  assert.match(page, /部分送达/);
  assert.match(page, /调用中 · 1 次活跃/);
  assert.match(page, /上下文与工具注入/);
  assert.match(page, /memory\.recall \/ recent_memory/);
  assert.match(page, /calendar\.query/);
  assert.match(page, /扩展插件 · calendar\.query/);
  assert.match(page, /data-kind="extension"/);
  assert.match(page, /Provider 请求失败/);
  assert.match(page, /HTTP 429/);
  assert.match(page, /RateLimitError/);
  assert.match(page, /未收到完整响应内容/);
  assert.match(page, /用量未确认 · 输入 未确认 · 输出 未确认/);
  assert.match(page, /宿主其他插件钩子未采集/);
  assert.match(page, /data-observed="false"/);
  assert.match(page, /未观测/);
  assert.doesNotMatch(page, /消息正文|private body/);
  const timelineStart = page.indexOf('<ol class="maint-efficiency-timeline">');
  const timelineEnd = page.indexOf('</ol>', timelineStart);
  const timeline = page.slice(timelineStart, timelineEnd);
  let previousEvent = -1;
  for (const event of ['received_at', 'last_input_received_at', 'first_model_started_at',
    'last_model_finished_at', 'first_dispatched_at', 'first_delivered_at', 'last_delivered_at']) {
    const position = timeline.indexOf(`data-event="${event}"`);
    assert.ok(position > previousEvent, `${event} should follow the previous timeline event`);
    previousEvent = position;
  }
  h.root.click('[data-maint-nav]', {maintNav: 'recent'});
  assert.deepEqual(changedViews, ['efficiency', 'recent']);
});

test('reply efficiency charts core and historical daily latency as separate measured series', async () => {
  const h = harness({maintenance: {
    generated_at: localStamp(), summary: {}, totals: {}, daily: [], history: [], recent: [],
    reply_efficiency: {
      summary: {samples: 3}, coverage: {core_samples: 1},
      by_source: {astrbot_core: {samples: 1}, legacy_v2_conversation: {samples: 2}},
      daily: [
        {day: '2026-10-11', samples: 2, core_samples: 0, median_end_to_end_ms: 6400,
          p95_end_to_end_ms: 9200, median_end_to_end_send_returned_ms: null},
        {day: '2026-10-12', samples: 1, core_samples: 1, median_end_to_end_ms: null,
          median_end_to_end_send_returned_ms: 3200, p95_end_to_end_send_returned_ms: 3200},
      ],
      recent: [],
    },
  }});
  h.page.mount(h.root, {activeView: 'efficiency'});
  await flush();
  const page = h.root.innerHTML.split('data-maint-view="efficiency"')[1].split('data-maint-view="recent"')[0];
  assert.match(page, /<h3>每日回复时长<\/h3>/);
  assert.match(page, /核心发送返回/);
  assert.match(page, /历史平台回执/);
  assert.match(page, /每日回复时长中位数柱状图，共用纵轴/);
  assert.match(page, /单位：秒/);
  assert.match(page, /--tick-position:0%">8<\/span>/);
  assert.match(page, /data-series="legacy" style="--bar-height:[1-9]\d*%" title="历史平台回执 · 2026-10-11 · P50 6\.4 秒 · P95 9\.2 秒"/);
  assert.match(page, /data-series="core" style="--bar-height:[1-9]\d*%" title="核心发送返回 · 2026-10-12 · P50 3\.2 秒 · P95 3\.2 秒"/);
  assert.match(page, /data-series="core" aria-hidden="true"><\/span>/);
  assert.match(page, /role="group" aria-label="2026-10-11：历史平台回执 P50 6\.4 秒，P95 9\.2 秒"/);
  assert.match(page, /datetime="2026-10-11"><span class="maint-efficiency-date-full">10\/11<\/span><span class="maint-efficiency-date-compact" aria-hidden="true">11日<\/span><\/time>/);
  assert.match(page, /10\/11 – 10\/12/);
  assert.match(styles, /\.maint-efficiency-day:hover \.maint-efficiency-trend-bar/);
  assert.match(styles, /@media \(prefers-reduced-motion:reduce\)[\s\S]*\.maint-efficiency-trend-bar/);
  h.page.unmount();
});

test('daily latency keeps unobserved days distinct from a measured zero and exposes focusable readouts', async () => {
  const report = {summary: {samples: 2}, coverage: {core_samples: 2},
    by_source: {astrbot_core: {samples: 2}}, recent: [], daily: [
      {day: '2026-10-11', samples: 1, median_end_to_end_send_returned_ms: null},
      {day: '2026-10-12', samples: 1, median_end_to_end_send_returned_ms: 0},
    ]};
  const h = harness({maintenance: {generated_at: localStamp(), summary: {}, totals: {}, daily: [],
    history: [], recent: [], reply_efficiency: report}});
  h.page.mount(h.root, {activeView: 'efficiency'});
  await flush();
  assert.match(h.root.innerHTML, /aria-label="2026-10-11：回复时长未观测" tabindex="0"/);
  assert.match(h.root.innerHTML, /aria-label="2026-10-12：核心发送返回 P50 0 毫秒" tabindex="0"/);
  assert.match(h.root.innerHTML, /单位：毫秒/);
  assert.match(h.root.innerHTML, /P50 0 毫秒<\/b>/);
  h.page.unmount();
  report.daily[1].median_end_to_end_send_returned_ms = null;
  const unknown = harness({maintenance: {generated_at: localStamp(), summary: {}, totals: {}, daily: [],
    history: [], recent: [], reply_efficiency: report}});
  unknown.page.mount(unknown.root, {activeView: 'efficiency'});
  await flush();
  assert.doesNotMatch(unknown.root.innerHTML, /class="maint-efficiency-trend-chart"/);
  assert.match(unknown.root.innerHTML, /暂无核心发送返回或历史回执样本/);
  unknown.page.unmount();
});

test('dialogue and plugin views switch totals, rankings, cache, history, traces and CSV together', async () => {
  const today = localDay(), previous = localDay(new Date(Date.now() - 86400000));
  const report = (kind, provider, current, prior, cacheCalls) => {
    const usage = tokens => ({calls: 1, tokens, cost_microusd: tokens * 10, input_tokens: tokens - 100,
      output_tokens: 100, cache_reported_calls: 1, cache_telemetry_calls: 1,
      cache_input_tokens: tokens - 100, cached_input_tokens: 100});
    const day = (key, tokens) => daily(key, {...usage(tokens), key,
      providers: [{key: provider, ...usage(tokens)}], tasks: [{key: kind, ...usage(tokens)}]});
    const total = {...usage(current + prior), calls: 2,
      providers: [{key: provider, ...usage(current + prior)}], tasks: [{key: kind, ...usage(current + prior)}]};
    return {summary: total, totals: total, daily: [day(previous, prior), day(today, current)],
      history: [day(previous, prior), day(today, current)],
      windows: Object.fromEntries(['7d', '30d'].map(period => [period, {start_day: previous, end_day: today,
        totals: total, providers: total.providers, tasks: total.tasks}])),
      hourly: [{day: today, hour: '08', calls: 1, tokens: current}],
      recent: [{at: today + 'T08:00:00', kind, provider, state: 'generated', tokens: current,
        input_tokens: current - 100, output_tokens: 100, cached_input_tokens: 100}],
      cache_diagnostics: {daily: [{day: today, signature_calls: cacheCalls, baseline_calls: cacheCalls}], recent_events: []}};
  };
  const dialogue = report('conversation.reply', 'dialogue-provider', 11000, 2000, 3);
  const plugin = report('proactive.compose', 'plugin-provider', 1300, 500, 1);
  const totals = {...daily(today, {tokens: 12300, calls: 2}), providers: [], tasks: []};
  const h = harness({maintenance: {generated_at: today + 'T09:00:00', summary: totals, totals,
    daily: [totals], history: [totals], recent: [...dialogue.recent, ...plugin.recent], budget: {spent_tokens: 1300},
    usage_sources: {conversation: dialogue, plugin}}});
  h.page.mount(h.root);
  await flush();
  assert.match(h.root.innerHTML, /角色总用量 = 对话主链 \+ 插件附加任务/);
  assert.doesNotMatch(h.root.innerHTML, /陪伴插件 · 当前角色/);

  h.root.click('[data-token-source]', {tokenSource: 'plugin'});
  const overview = () => h.root.innerHTML.split('data-maint-view="overview"')[1].split('data-maint-view="prompts"')[0];
  assert.match(overview(), /Provider 已确认 Token<\/span><strong>1,300<\/strong>/);
  assert.match(overview(), /US\$0\.013000 已计价部分/);
  assert.match(overview(), /data-rank-key="proactive\.compose"/);
  assert.doesNotMatch(overview(), /data-task-settings-kind="conversation\.reply"|dialogue-provider/);
  assert.match(overview(), /插件任务预算/);
  assert.match(overview(), /仅插件任务/);
  assert.match(overview(), /已入账 <b>1,300<\/b>/);
  // Comparison rows always use their own source, even when the detail view changes.
  const comparison = overview().split('maint-source-compare-list')[1].split('maint-source-compare-note')[0];
  assert.match(comparison, /data-token-source="conversation"[\s\S]*?<b>11,000<\/b>/);
  assert.match(comparison, /data-token-source="companion"[\s\S]*?<b>12,300<\/b>/);
  h.root.click('[data-maint-nav]', {maintNav: 'recent'});
  const recent = h.root.innerHTML.split('data-maint-view="recent"')[1];
  assert.match(recent, /插件附加任务 · 1 \/ 1 条/);
  assert.match(recent, /plugin-provider/);
  assert.doesNotMatch(recent, /dialogue-provider/);
  h.root.click('[data-export-tokens]');
  assert.match(h.exports[0], /插件附加任务/);
  assert.match(h.exports[0], /proactive\.compose/);
  assert.doesNotMatch(h.exports[0], /conversation\.reply|dialogue-provider/);
  assert.match(h.exports[0], /"每日","[^"]+","1300"/);
  assert.match(h.downloads[0], /tokens-plugin-/);
  h.root.click('[data-period]', {period: 'total'});
  assert.match(overview(), /Provider 已确认 Token<\/span><strong>1,800<\/strong>/);
  h.root.click('[data-period]', {period: '7d'});
  assert.match(overview(), /Provider 已确认 Token<\/span><strong>1,800<\/strong>/);
  h.root.click('[data-select-day]', {selectDay: previous});
  assert.match(overview(), /Provider 已确认 Token<\/span><strong>500<\/strong>/);
  h.root.click('[data-period]', {period: 'today'});
  h.root.click('[data-rank-kind]', {rankKind: 'tasks', rankKey: 'proactive.compose'});
  h.root.click('[data-token-source]', {tokenSource: 'conversation'});
  assert.match(overview(), /Provider 已确认 Token<\/span><strong>11,000<\/strong>/);
  assert.match(overview(), /data-rank-key="conversation\.reply"/);
  assert.doesNotMatch(overview(), /data-task-settings-kind="proactive\.compose"|plugin-provider/);
  const dialogueRecent = h.root.innerHTML.split('data-maint-view="recent"')[1];
  assert.match(dialogueRecent, /对话 · AstrBot 核心与历史 · 1 \/ 1 条/);
  assert.doesNotMatch(dialogueRecent, /已按排行筛选|plugin-provider/);
  h.root.click('[data-maint-nav]', {maintNav: 'cache'});
  const cache = h.root.innerHTML.split('data-maint-view="cache"')[1].split('data-maint-view="recent"')[0];
  assert.match(cache, /请求前缀签名/);
  assert.match(cache, /已采集 3 次 · 等待可比调用/);
  assert.doesNotMatch(cache, /已采集 1 次/);
  assert.match(cache, /aria-label="缓存诊断来源"/);
  assert.match(cache, /data-token-source="conversation" aria-pressed="true"/);
  assert.match(cache, /aria-label="缓存诊断时间范围"/);
  h.root.click('[data-token-source]', {tokenSource: 'plugin'});
  const pluginCache = h.root.innerHTML.split('data-maint-view="cache"')[1].split('data-maint-view="recent"')[0];
  assert.match(pluginCache, /data-token-source="plugin" aria-pressed="true"/);
  assert.match(pluginCache, /已采集 1 次 · 等待可比调用/);
  assert.doesNotMatch(pluginCache, /已采集 3 次/);
  h.page.unmount();
});

test('Live and Game keep native ledgers separate and describe missing statistics as an internal service', async () => {
  const today = localDay();
  const usage = {calls: 2, tokens: 90, reported_tokens: 90, estimated_tokens: 0,
    input_tokens: 80, output_tokens: 10, unconfirmed_calls: 1, cache_telemetry_calls: 0,
    cache_reported_calls: 0, cached_input_tokens: null, cache_creation_tokens: null};
  const totals = daily(today, {calls: 1, tokens: 10000});
  const h = harness({maintenance: {generated_at: today + 'T09:00:00',
    summary: totals, totals, daily: [totals], history: [totals], recent: [], budget: {},
    external_token_sources: {
      live: {available: true, installed: true, display_name: '直播与表演', totals: usage,
        daily: [{key: today, ...usage}], history: [{key: today, ...usage}],
        recent: [{at: today + 'T08:00:00', kind: 'live.auto_reply', provider: 'live-model',
          state: 'generated', tokens: 90}], by_provider: [], by_task: []},
      game: {available: false, installed: true, display_name: '共同游玩'},
    }}});
  h.page.mount(h.root);
  await flush();
  assert.match(h.root.innerHTML, /data-maint-view="overview"/);
  h.root.click('[data-token-source]', {tokenSource: 'live'});
  const overview = () => h.root.innerHTML.split('data-maint-view="overview"')[1].split('data-maint-view="prompts"')[0];
  assert.match(overview(), /直播与表演 独立账本/);
  assert.match(overview(), /已确认 Token<\/span><strong>90<\/strong>/);
  assert.match(overview(), /无回执/);
  h.root.click('[data-maint-nav]', {maintNav: 'recent'});
  const recent = h.root.innerHTML.split('data-maint-view="recent"')[1];
  assert.match(recent, /直播与表演 · 1 \/ 1 条/);
  assert.match(recent, /live-model/);
  h.root.click('[data-token-source]', {tokenSource: 'game'});
  assert.match(overview(), /内部能力已装配，统计暂不可用/);
  assert.doesNotMatch(overview(), /插件已加载，统计桥暂不可用/);
  h.root.click('[data-token-source]', {tokenSource: 'companion'});
  assert.match(overview(), /10,000/);
  h.page.unmount();
});

test('cache tables retain real provider receipts and exclude missing hit rates from low samples', async () => {
  const today = localDay();
  const provider = {...daily(today, {calls: 2, cache_reported_calls: 1, cache_telemetry_calls: 1,
    cache_input_tokens: 2000, cached_input_tokens: 200, cache_other_input_tokens: 1800}), key: 'provider <test>'};
  const totals = {...provider, providers: [provider], cache_low_calls: 1};
  const h = harness({maintenance: {generated_at: localStamp(), summary: totals, totals, daily: [totals],
    history: [totals], budget: {}, providers: [], recent: [
      {at: localStamp(), kind: 'conversation.reply', provider: provider.key, input_tokens: 2000,
        cached_input_tokens: 200, cache_hit_rate: 10, state: 'generated'},
      {at: localStamp(), kind: 'conversation.reply', provider: 'unknown-receipt', input_tokens: 2500,
        cached_input_tokens: 0, cache_hit_rate: null, state: 'generated'},
    ]}});
  h.page.mount(h.root, {activeView: 'cache'});
  await flush();
  const cache = h.root.innerHTML.split('data-maint-view="cache"')[1].split('data-maint-view="recent"')[0];
  assert.match(cache, /class="maint-cache-table maint-cache-provider-table"/);
  assert.match(cache, /class="maint-cache-table maint-cache-low-table"/);
  assert.match(cache, /provider &lt;test&gt;/);
  assert.doesNotMatch(cache, /provider <test>|unknown-receipt/);
  assert.match(cache, /data-series="coverage"><span>确认覆盖<\/span><strong>50\.0%<\/strong>/);
  assert.match(cache, /<td>2,000<\/td>/);
  assert.equal((cache.match(/data-set-call-filter="cache-low"/g) || []).length, 1);
  h.root.click('[data-set-call-filter]', {setCallFilter: 'cache-low'});
  assert.match(h.root.innerHTML, /data-maint-nav="recent" aria-current="page"/);
  h.page.unmount();
});

test('cache hit comparisons use the preceding whole seven- or thirty-day window', async () => {
  const today = localDay();
  for (const [period, days] of [['7d', 7], ['30d', 30]]) {
    const history = Array.from({length: days * 2}, (_, offset) => {
      const date = new Date(today + 'T12:00:00');
      date.setDate(date.getDate() - offset);
      return daily(localDay(date), {calls: 1, cache_reported_calls: 1, cache_telemetry_calls: 1,
        cache_input_tokens: 100, cached_input_tokens: offset < days ? 80 : 20,
        cache_other_input_tokens: offset < days ? 20 : 80});
    });
    const totals = daily(today, {calls: days, cache_reported_calls: days, cache_telemetry_calls: days,
      cache_input_tokens: days * 100, cached_input_tokens: days * 80, cache_other_input_tokens: days * 20});
    const h = harness({maintenance: {generated_at: localStamp(), summary: totals, totals,
      daily: history, history, windows: {[period]: {totals, providers: [], tasks: []}}, recent: [], budget: {}, providers: []}});
    h.page.mount(h.root, {activeView: 'cache'});
    await flush();
    h.root.click('[data-period]', {period});
    const cache = h.root.innerHTML.split('data-maint-view="cache"')[1].split('data-maint-view="recent"')[0];
    assert.match(cache, new RegExp('较前 ' + days + ' 日 \\+60\\.0 个百分点 · 前期 20\\.0%'));
    h.page.unmount();
  }
});

test('cache diagnostics summarize request context and show event snapshots', async () => {
  const today = localDay();
  const yesterdayDate = new Date(today + 'T00:00:00');
  yesterdayDate.setDate(yesterdayDate.getDate() - 1);
  const yesterday = localDay(yesterdayDate);
  const totals = daily(today, {calls: 1, tokens: 100, input_tokens: 80, output_tokens: 20,
    cache_reported_calls: 1, cache_telemetry_calls: 1, cache_input_tokens: 80,
    cached_input_tokens: 10, cache_other_input_tokens: 70});
  const previousDay = daily(yesterday, {calls: 1, cache_input_tokens: 100, cached_input_tokens: 40});
  const event = {at: localStamp(), session_ref: 'a1b2c3d4', severity: 'high',
    previous_provider: 'provider-b', provider: 'provider-a',
    changes: ['provider_change', 'model_change', 'context_reset', 'context_availability_change'],
    cache_hit_rate: 0, cache_hit_delta: null,
    before: {context_materials: 4, context_chars: 800, context_bytes: 1638,
      context_est_tokens: 400, system_est_tokens: 1000, tool_est_tokens: 250,
      message_est_tokens: 500, message_chars: 300, message_bytes: 600, request_est_tokens: 2150,
      deferred_materials: 1, unavailable_materials: 2, messages: 6, images: 1, audio: 0, video: 0,
      tools_fingerprint: '11111111'},
    after: {context_materials: 0, context_chars: 0, context_bytes: 0,
      context_est_tokens: 0, system_est_tokens: 1200, tool_est_tokens: 300,
      message_est_tokens: 1100, message_chars: 400, message_bytes: 800, request_est_tokens: 2600,
      deferred_materials: 0, unavailable_materials: 1, messages: 6, images: 0, audio: 0, video: 0,
      tools_fingerprint: '22222222'}};
  const h = harness({maintenance: {generated_at: localStamp(), summary: totals, totals, daily: [totals],
    history: [previousDay, totals], hourly: [], recent: [], budget: {}, providers: [], runtime: {},
    cache_diagnostics: {daily: [{day: today, signature_calls: 2, compared_calls: 1,
      baseline_calls: 1, changed_calls: 1, high_severity_events: 1, medium_severity_events: 0,
      low_severity_events: 0, context_resets: 1, model_changes: 1,
      provider_changes: 1, context_availability_changes: 1,
      cache_hit_comparable_changes: 0, cache_hit_drop_after_change_calls: 0, cache_hit_delta_points_total: 0,
      route_compared_calls: 1, route_stable_calls: 0, route_cache_calls: 0,
      system_compared_calls: 1, system_stable_calls: 1, system_cache_calls: 1,
      system_cache_input_tokens_total: 1000, system_cached_input_tokens_total: 300,
      context_stats_calls: 2, context_text_stats_calls: 2, request_stats_calls: 2,
      context_availability_calls: 2,
      context_estimate_calls: 2, system_estimate_calls: 2, tool_estimate_calls: 2,
      message_estimate_calls: 2, request_estimate_calls: 2,
      context_est_tokens_total: 1200, system_est_tokens_total: 2000, tool_est_tokens_total: 600,
      message_est_tokens_total: 1000, request_est_tokens_total: 4800,
      request_profile_calls: 2, profile_context_est_tokens_total: 1200,
      profile_message_est_tokens_total: 1000, profile_system_est_tokens_total: 2000,
      profile_tool_est_tokens_total: 600, profile_request_est_tokens_total: 4800,
      context_materials_total: 8, context_chars_total: 1600, context_bytes_total: 3276,
      message_chars_total: 800, message_bytes_total: 1600,
      deferred_materials_total: 2, unavailable_materials_total: 1,
      messages_total: 12, system_messages_total: 4, system_bytes_total: 6000,
      tools_total: 6, tool_bytes_total: 4096, images_total: 2, audio_total: 0, video_total: 0}],
      recent_events: Array.from({length: 22}, () => event), event_sample_limit: 80}}});
  h.page.mount(h.root);
  await flush();
  h.root.click('[data-maint-nav]', {maintNav: 'cache'});
  const cache = h.root.innerHTML.split('data-maint-view="cache"')[1].split('data-maint-view="recent"')[0];
  assert.match(cache, /模型切换/);
  assert.match(cache, /参考材料 \/ 请求/);
  assert.match(cache, /约 600 Token/);
  assert.match(cache, /约 1,000 Token/);
  assert.match(cache, /约 300 Token/);
  assert.match(cache, /消息上下文/);
  assert.match(cache, /完整请求/);
  assert.match(cache, /Provider 切换/);
  assert.match(cache, /服务路由/);
  assert.match(cache, /完整前缀/);
  assert.match(cache, /本期结构变化/);
  assert.match(cache, /高 1/);
  assert.match(cache, /常见变化标签/);
  assert.ok(cache.indexOf('缓存结构变化事件') < cache.indexOf('请求上下文统计'));
  assert.match(cache, /稳定 1 \/ 可判定 1 · 命中回执 1 次/);
  assert.match(cache, /稳定命中率/);
  assert.match(cache, /较前一日 -27\.5 个百分点 · 前期 40\.0%/);
  assert.equal((cache.match(/class="maint-cache-metric"/g) || []).length, 4);
  assert.match(cache, /缓存命中率 12\.5%/);
  assert.match(cache, /pathLength="100" stroke="url\(#maint-cache-rate-gradient\)" stroke-dasharray="12\.5 100" style="--cache-rate:12\.5" stroke-linecap="round"/);
  const zipBands = [...cache.matchAll(/<div class="maint-cache-zip" data-facets="28">[\s\S]*?<\/div>/g)]
    .map(match => match[0]);
  assert.equal(zipBands.length, 2);
  for (const band of zipBands) assert.equal((band.match(/<polygon\b/g) || []).length, 28);
  assert.match(cache, /animation-delay:24ms/);
  assert.match(cache, /animation-delay:672ms/);
  assert.match(styles, /\.maint-cache-zip polygon\s*\{[^}]*stroke-width:4/);
  assert.match(styles, /@keyframes maint-cache-zip-pop/);
  assert.match(styles, /\.maint-cache-mini-meter > i\s*\{[^}]*transition:width 500ms/);
  assert.match(styles, /\.maint-token-redesign,\s*\.maint-cache-panel\s*\{[^}]*--maint-token-series-1:#A17BFF;[^}]*--maint-token-series-2:#3FE0A6;[^}]*--maint-token-series-3:#FFC258;/s);
  assert.match(styles, /\.maint-cache-panel \[data-part="hit"\][^}]*--cache-part:var\(--maint-token-series-2\)/);
  assert.match(styles, /\.maint-cache-panel \[data-part="write"\] \{ --cache-part:#3EE0D0; \}/);
  assert.match(styles, /\.maint-cache-panel \[data-part="miss"\][^}]*--cache-part:var\(--maint-token-series-1\)/);
  assert.match(styles, /\.maint-cache-panel \[data-part="output"\],[^}]*--cache-part:#FFC258/);
  assert.match(styles, /#maint-cache-rate-gradient stop:nth-of-type\(1\) \{ stop-color:#A17BFF; \}/);
  assert.match(styles, /#maint-cache-rate-gradient stop:nth-of-type\(2\) \{ stop-color:#6FA2FF; \}/);
  assert.match(styles, /#maint-cache-rate-gradient stop:nth-of-type\(3\) \{ stop-color:#3EE0D0; \}/);
  assert.match(styles, /\.maint-chart-legend > \[data-series="cached"\]::before \{ background:var\(--maint-token-series-2\); \}/);
  assert.match(styles, /\.maint-chart-legend > \[data-series="input"\]::before \{ background:var\(--maint-token-series-1\); \}/);
  assert.match(styles, /@media \(prefers-reduced-motion:reduce\)\s*\{[^}]*\.maint-cache-ring-fill/s);
  assert.match(styles, /\.maint-cache-composition-section\[data-active-part="hit"\]/);
  assert.match(styles, /\.maint-cache-context-section\[data-active-part="materials"\]/);
  const compositionSection = {dataset: {}};
  const contextSection = {dataset: {}};
  const partTarget = (section, part) => ({
    dataset: {part},
    closest(selector) {
      if (selector === "[data-part]") return this;
      if (selector === ".maint-cache-composition-section, .maint-cache-context-section") return section;
      return null;
    },
  });
  const hitPart = partTarget(compositionSection, "hit");
  const writePart = partTarget(compositionSection, "write");
  h.root.pointer("pointerover", hitPart);
  assert.equal(compositionSection.dataset.activePart, "hit");
  h.root.pointer("pointerout", hitPart, writePart);
  assert.equal(compositionSection.dataset.activePart, "write");
  h.root.pointer("pointerout", writePart, {closest: () => null});
  assert.equal(compositionSection.dataset.activePart, undefined);
  const materialPart = partTarget(contextSection, "materials");
  h.root.pointer("pointerover", materialPart);
  assert.equal(contextSection.dataset.activePart, "materials");
  h.root.pointer("pointerout", materialPart, {closest: () => null});
  assert.equal(contextSection.dataset.activePart, undefined);
  assert.match(cache, /data-series="coverage"><span>确认覆盖<\/span><strong>100\.0%<\/strong>/);
  assert.match(cache, /Token 构成拉链图：缓存命中 10 Token（10\.0%），缓存写入 0 Token（0\.0%），普通输入 70 Token（70\.0%），输出 20 Token（20\.0%）/);
  assert.match(cache, /请求上下文拉链图：参考材料 600 Token（25\.0%），消息上下文 500 Token（20\.8%），system 消息 1,000 Token（41\.7%），工具定义 300 Token（12\.5%）/);
  assert.match(cache, /完整采集 2 次 · 单次请求均值/);
  assert.equal((cache.match(/class="maint-cache-stability"/g) || []).length, 6);
  assert.match(cache, /class="maint-cache-table maint-cache-event-table"/);
  assert.equal((cache.match(/<details class="maint-cache-event-detail">/g) || []).length, 20);
  assert.match(cache, /缓存结构变化事件/);
  assert.match(cache, /最近显示 20 条 · 样本池 80 条/);
  assert.equal((cache.match(/maint-cache-signature-event/g) || []).length, 20);
  assert.match(cache, /变化后命中率/);
  assert.match(cache, /约 2,400 Token/);
  assert.match(cache, /4 条/);
  assert.match(cache, /800 字 · 1\.6 KiB/);
  assert.match(cache, /请求消息 6 条/);
  assert.match(cache, /Provider 切换/);
  assert.match(cache, /模型切换/);
  assert.match(cache, /data-severity="high"/);
  assert.match(cache, /会话 a1b2c3d4/);
  assert.match(cache, /tools 11111111 → 22222222/);
  assert.match(cache, /provider-b → provider-a/);
  assert.match(cache, /Provider 已切换 · 不跨 Provider 比较 · 本次命中 0%/);
  assert.match(cache, /前 材料 4 条 · 800 字 · 1\.6 KiB · 约 400 Token · 延后 1 · 不可用 2 → 后 材料 0 条 · 0 字 · 0 B · 约 0 Token · 延后 0 · 不可用 1/);
  assert.match(cache, /前 消息正文（不含参考材料） 约 500 Token · 完整请求 约 2,150 Token · system 约 1,000 Token · 工具 约 250 Token/);
  h.page.unmount();
  h.root.pointer("pointerover", hitPart);
  assert.equal(compositionSection.dataset.activePart, undefined);
});

test('missing usage breakdown never substitutes role totals for plugin or dialogue', async () => {
  const today = localDay();
  const h = harness({maintenance: {generated_at: localStamp(), summary: {tokens: 5000, calls: 1},
    daily: [daily(today, {tokens: 5000, calls: 1})], recent: [{at: localStamp(), kind: 'conversation.reply',
      provider: 'dialogue-provider', tokens: 5000, state: 'generated'}]}});
  h.page.mount(h.root);
  await flush();
  for (const tokenSource of ['plugin', 'conversation']) {
    h.root.click('[data-token-source]', {tokenSource});
    assert.match(h.root.innerHTML, /分项统计尚未同步/);
    assert.doesNotMatch(h.root.innerHTML, /class="maint-token-hero maint-token-summary"/);
    const recent = h.root.innerHTML.split('data-maint-view="recent"')[1];
    assert.doesNotMatch(recent, /dialogue-provider/);
    h.root.click('[data-export-tokens]');
  }
  assert.equal(h.exports.length, 0);
  h.root.click('[data-token-source]', {tokenSource: 'main'});
  assert.match(h.root.innerHTML, /AstrBot 全局用量暂不可用/);
  h.page.unmount();
});

test('AstrBot all-session statistics connect the host ledger and export only the selected period', async () => {
  const today = localDay(), oldDay = localDay(new Date(Date.now() - 40 * 86400000));
  const hostToday = daily(today, {tokens: 370, calls: 2, input_tokens: 340, output_tokens: 30,
    reported_tokens: 370, cached_input_tokens: 40, cost_microusd: null, cache_reported_calls: 0,
    cache_telemetry_calls: 1, providers: [{key: 'host-provider', tokens: 370, calls: 2}],
    tasks: [{key: 'conversation.reply', tokens: 370, calls: 2}]});
  const hostOld = daily(oldDay, {tokens: 99, calls: 1, providers: [{key: 'old-host-provider', tokens: 99, calls: 1}]});
  const role = daily(today, {tokens: 5000, calls: 1});
  const main = {available: true, installed: true, display_name: 'AstrBot 全部会话',
    totals: {...hostToday, tokens: 469, calls: 3}, daily: [hostOld, hostToday], history: [hostOld, hostToday],
    by_provider: [{key: 'host-provider', tokens: 370, calls: 2}, {key: 'old-host-provider', tokens: 99, calls: 1}],
    recent: [{at: today + 'T08:00:00', kind: 'conversation.reply', provider: 'host-provider', tokens: 160}],
    note: '按核心回复轮次汇总，包含工具续轮；缓存命中率与费用未提供。'};
  const h = harness({maintenance: {generated_at: localStamp(), summary: role, totals: role,
    daily: [role], recent: [{at: localStamp(), kind: 'conversation.reply', provider: 'role-provider', tokens: 5000}],
    budget: {spent_tokens: 123}, external_token_sources: {main}}});
  h.page.mount(h.root);
  await flush();
  assert.match(h.root.innerHTML, /data-token-source="main"[^>]*>[\s\S]*?<small data-state="已连接">已连接/);
  h.root.click('[data-token-source]', {tokenSource: 'main'});
  const overview = () => h.root.innerHTML.split('data-maint-view="overview"')[1].split('data-maint-view="prompts"')[0];
  assert.match(overview(), /今日 · 来源 Token<\/span><strong>370<\/strong>/);
  assert.match(overview(), /2 次回复/);
  assert.match(overview(), /缓存命中<\/span><strong>40<\/strong>/);
  assert.match(overview(), /暂无命中率/);
  assert.match(overview(), /缓存输入 40 · 其余输入（含缓存未知） 300 · 输出 30/);
  assert.doesNotMatch(overview(), /未命中缓存输入/);
  assert.doesNotMatch(overview(), /AstrBot 全局用量暂不可用|今日插件任务预算|role-provider/);
  h.root.click('[data-period]', {period: '7d'});
  h.root.click('[data-export-tokens]');
  assert.match(h.exports[0], /AstrBot 全部会话/);
  assert.match(h.exports[0], /host-provider/);
  assert.doesNotMatch(h.exports[0], /role-provider|old-host-provider|5000/);
  assert.equal(h.exports[0].split('\r\n')[1].split(',')[9], '""');
  assert.match(h.downloads[0], /tokens-main-/);
  h.root.click('[data-period]', {period: 'total'});
  assert.match(overview(), /累计 · 来源 Token<\/span><strong>469<\/strong>/);
  h.root.click('[data-maint-nav]', {maintNav: 'recent'});
  const recent = h.root.innerHTML.split('data-maint-view="recent"')[1];
  assert.match(recent, /host-provider/);
  assert.doesNotMatch(recent, /role-provider/);
  h.page.unmount();
});

test('a failed host statistics read shows its real unavailable state instead of a fixed missing integration', async () => {
  const h = harness({maintenance: {generated_at: localStamp(), summary: {tokens: 5000}, daily: [], recent: [],
    external_token_sources: {main: {available: false, installed: true, display_name: 'AstrBot 全部会话',
      reason: 'AstrBot 全局用量读取失败，请稍后重新读取。'}}}});
  h.page.mount(h.root);
  await flush();
  assert.match(h.root.innerHTML, /data-token-source="main"[^>]*>[\s\S]*?<small data-state="暂不可用">暂不可用/);
  h.root.click('[data-token-source]', {tokenSource: 'main'});
  assert.match(h.root.innerHTML, /AstrBot 全局用量读取失败/);
  h.root.click('[data-export-tokens]');
  assert.equal(h.exports.length, 0);
  h.page.unmount();
});

test('recent calls show response stages and missing parsed text from body-free metadata', async () => {
  const sampleTime = new Date(); sampleTime.setHours(12, 30, 0, 0);
  const h = harness({maintenance: {generated_at: localStamp(), summary: {calls: 1, tokens: 0},
    daily: [], recent: [{at: localStamp(),
    kind: 'conversation.reply', provider: 'dialogue-provider', tokens: null, state: 'unknown',
    failure: 'native_response_parse_failed', response_diagnostics: {version: 2, completion_present: true,
      parse_state: 'native_rejected', finish_reason: 'stop', raw: {chars: 2, has_internal_markers: true},
      parsed: null, visible: null}},
    {at: localStamp(new Date(sampleTime.getTime() - 60000)), kind: 'conversation.reply', provider: 'dialogue-provider',
      tokens: null, state: 'unknown', failure: 'provider_request_failed', response_diagnostics: {version: 2,
        completion_present: false, parse_state: 'not_received', finish_reason: 'missing', raw: null, parsed: null,
        visible: null, failure_code: 'provider_request_failed', error_type: 'APIConnectionError',
        error_code: null, status_code: null}}]}});
  h.page.mount(h.root);
  await flush();
  h.root.click('[data-maint-nav]', {maintNav: 'recent'});
  assert.match(h.root.innerHTML, /宿主解析失败/);
  assert.match(h.root.innerHTML, /原始 2 字 · 含标签样式标记/);
  assert.match(h.root.innerHTML, /宿主解析 未记录 · 待呈现 未记录/);
  assert.match(h.root.innerHTML, /Provider 请求失败/);
  assert.match(h.root.innerHTML, /结束原因 未返回/);
  assert.match(h.root.innerHTML, /APIConnectionError/);
  assert.match(h.root.innerHTML, /未收到响应正文/);
  assert.doesNotMatch(h.root.innerHTML, /原始未取得文本/);
  h.page.unmount();
});

test('maintenance TTS diagnostics play the generated audio and release old preview URLs', async () => {
  const audioBase64 = Buffer.from('RIFF0000WAVE').toString('base64');
  const h = harness({
    maintenance: {generated_at: localStamp(), summary: {calls: 0, tokens: 0}, totals: {}, daily: [], history: [],
      windows: {}, providers: [], recent: [], budget: {}, runtime: {}},
    voice: {providers: [{id: 'voice-a'}]}, image: {actions: []}, model: {tasks: [], providers: []},
    posts: {'/model/voice/test': {provider_id: 'voice-a', status: 'ok', latency_ms: 22,
      audio_base64: audioBase64, audio_mime_type: 'audio/wav'}},
  });
  h.page.mount(h.root, {activeView: 'diagnostics'});
  await flush();
  h.root.click('[data-voice-test]');
  await flush();
  assert.match(h.root.innerHTML, /本次结果 · 试听/);
  assert.match(h.root.innerHTML, /<audio controls preload="none" src="blob:maintenance-audio"/);
  assert.equal(h.audioPreviews[0].type, 'audio/wav');

  h.root.change('[data-voice-language]', 'ja');
  assert.doesNotMatch(h.root.innerHTML, /blob:maintenance-audio|TTS 合成通过/);
  assert.deepEqual(h.revokedAudioUrls, ['blob:maintenance-audio']);
  h.root.click('[data-voice-test]');
  await flush();
  h.page.unmount();
  assert.deepEqual(h.revokedAudioUrls, ['blob:maintenance-audio', 'blob:maintenance-audio']);
});

test('maintenance groups troubleshooting content by workflow and uses responsive test layouts', async () => {
  const h = harness({
    maintenance: {generated_at: localStamp(), summary: {calls: 0, tokens: 0}, totals: {}, daily: [], history: [],
      windows: {}, providers: [], recent: [], budget: {}, runtime: {}},
    voice: {providers: []}, image: {actions: []}, model: {tasks: [], providers: []},
  });
  h.page.mount(h.root, {activeView: 'diagnostics'});
  await flush();

  const groups = ['maint-diagnostic-rail', 'maint-diagnostic-focus', 'maint-diagnostic-inspection',
    'maint-diagnostic-signals', 'maint-diagnostic-activity', 'maint-diagnostic-checks',
    'maint-evidence-drawer', 'maint-faq'].map(name => h.root.innerHTML.indexOf('class="' + name + '"'));
  assert.ok(groups.every(index => index >= 0));
  assert.deepEqual(groups, [...groups].sort((left, right) => left - right));
  assert.match(h.root.innerHTML, /class="maint-diagnostic-workspace"[\s\S]*role="group" aria-label="问题类型"/);
  assert.match(styles, /\.maint-diagnostic-workspace\s*\{[^}]*grid-template-columns:minmax\(200px, 230px\)/s);
  assert.match(styles, /@container maint-diagnostics \(max-width: 920px\)/);
  assert.match(styles, /@container maint-diagnostics \(max-width: 640px\)/);
  assert.match(styles, /\.maint-diagnostic-categories\s*\{[^}]*flex-wrap:nowrap/s);
  assert.match(h.root.innerHTML, /class="maint-diagnostic-checks"[\s\S]*class="maint-proactive-test"[\s\S]*class="maint-memory-diagnostic"[\s\S]*class="maint-test-zone"/);
  assert.match(h.root.innerHTML, /class="maint-test-row maint-test-row--provider"/);
  assert.match(h.root.innerHTML, /class="maint-test-row maint-test-row--voice"/);
  assert.match(h.root.innerHTML, /class="maint-test-row maint-test-row--image"/);
  assert.match(styles, /container-name:maint-diagnostics/);
  assert.match(styles, /@container maint-diagnostics \(max-width: 960px\)/);
  assert.match(styles, /@container maint-diagnostics \(max-width: 760px\)/);
  h.page.unmount();
});

test('prompt route opens the editor directly and internal tabs update the route state', async () => {
  const h = harness({
    maintenance: {generated_at: localStamp(), summary: {calls: 0, tokens: 0}, totals: {}, daily: [], history: [],
      windows: {}, providers: [], recent: [], budget: {}, runtime: {}},
    voice: {providers: []}, image: {actions: []}, model: {tasks: [], providers: []},
  });
  const changedViews = [];
  h.page.mount(h.root, {activeView: 'prompts', onViewChange: view => changedViews.push(view)});
  await flush();

  assert.match(h.root.innerHTML, /data-maint-nav="prompts" aria-current="page"/);
  assert.match(h.root.innerHTML, /data-maint-view="prompts"><section class="maint-prompt-section"/);
  h.root.click('[data-maint-nav]', {maintNav: 'cache'});
  assert.deepEqual(changedViews, ['prompts', 'cache']);
  assert.match(h.root.innerHTML, /data-maint-nav="cache" aria-current="page"/);
});

test('maintenance offers today, total, date drilldown, ranked detail, cache and diagnostic filters', async () => {
  const today = localDay(new Date());
  const sampleTime = new Date(); sampleTime.setHours(12, 30, 0, 0);
  const previous = localDay(new Date(Date.now() - 86400000));
  const provider = {key: '<main>', calls: 1, tokens: 120, cost_microusd: 15, failures: 0,
    input_tokens: 100, output_tokens: 20, cache_reported_calls: 1, cache_telemetry_calls: 1,
    cache_input_tokens: 100, cached_input_tokens: 40, cache_creation_tokens: 10,
    cache_other_input_tokens: 50, avg_latency_ms: 120};
  const task = {...provider, key: 'conversation.reply'};
  const current = daily(today, {...provider, calls: 1, tokens: 120, providers: [provider], tasks: [task]});
  const prior = daily(previous, {calls: 2, tokens: 80, input_tokens: 60, output_tokens: 20, providers: [], tasks: []});
  const hour = String(sampleTime.getHours()).padStart(2, '0');
  const recent = Array.from({length: 12}, (_, index) => ({kind: 'conversation.reply', provider: '<main>',
    state: 'generated', tokens: 120 + index, cost_microusd: 15, input_tokens: 100, cached_input_tokens: 40,
    cache_creation_tokens: 10, output_tokens: 20, cache_hit_rate: 40, latency_ms: 120,
    at: localStamp(new Date(sampleTime.getTime() - index * 60000)), failure: index === 11 ? 'provider_request_failed' : null}));
  const responses = {
    maintenance: {
      generated_at: localStamp(),
      summary: {calls: 3, tokens: 200, unconfirmed_calls: 0},
      totals: {calls: 3, tokens: 200, cost_microusd: 25, input_tokens: 160, output_tokens: 40,
        failures: 0, cache_reported_calls: 1, cache_telemetry_calls: 1, cache_input_tokens: 100,
        cached_input_tokens: 40, cache_creation_tokens: 10, cache_other_input_tokens: 50,
        providers: [provider], tasks: [task]},
      daily: [prior, current], history: [prior, current],
      windows: {
        '7d': {start_day: previous, end_day: today, totals: {calls: 3, tokens: 200, cost_microusd: 25,
          input_tokens: 160, output_tokens: 40, failures: 0, providers: [provider], tasks: [task]}, providers: [provider], tasks: [task]},
        '30d': {start_day: previous, end_day: today, totals: {calls: 3, tokens: 200, cost_microusd: 25,
          input_tokens: 160, output_tokens: 40, failures: 0, providers: [provider], tasks: [task]}, providers: [provider], tasks: [task]},
      },
      hourly: [{day: today, hour, calls: 1, tokens: 120, cost_microusd: 15}],
      by_provider: [provider], by_task: [task],
      recent,
      budget: {hard_limit_enabled: true, limit_tokens: 1000, spent_tokens: 100, held_tokens: 20, soft_limit_enabled: true, soft_limit_tokens: 500},
      providers: [{id: '<main>', model: 'model-a'}],
      runtime: {
        storage: {journal_mode: 'wal', synchronous: 2, busy_timeout_ms: 5000, foreign_keys: true, page_count: 88, freelist_count: 2, schema_version: 49},
        inbound_no_replies: [
          {event_ref: '1234567890abcdef1234567890abcdef', channel: 'private',
            reason_code: 'staging_not_accepting', observed_at: localStamp(), scope: 'current_role'},
          {event_ref: 'fedcba0987654321fedcba0987654321', channel: 'private',
            reason_code: 'conversation_task_failed', code: 'model_budget_exhausted', observed_at: localStamp(), scope: 'current_role'},
          {event_ref: 'abcdef1234567890abcdef1234567890', channel: 'group',
            reason_code: 'core_reply_send_denied', code: 'host_client_offline', observed_at: localStamp(), scope: 'current_role'},
        ],
        conversation: [{created_at: localStamp(), batch_state: 'closed', flow_state: 'blocked', flow_reason: 'provider_failure',
          input_count: 2, part_count: 2, delivered_parts: 1, failed_parts: 1, unknown_parts: 0, pending_parts: 0}],
        identity_scope: {dashboard_operator: 'verified', role_access: 'verified', token_ledger: 'current_role'},
        debounce_trace: [{at: localStamp(), batch_state: 'ready', flow_state: 'complete', reason: 'debounce',
          input_count: 2, part_count: 1, due_at: localStamp(), deadline_at: localStamp(), quiet_window_ms: 600, merge_window_ms: 2000}],
        prompt_materials: [{owner: 'memory.recall', purpose: 'conversation', state: 'included', uses: 2}],
        proactive: {calls: 3, latest_at: localStamp()},
      },
    },
    voice: {providers: [{id: 'voice-a'}]},
    image: {actions: [{action: 'image.generate', registered: true, current: true}]},
    balance: {configured: true, available: true, provider: 'newapi', provider_label: 'NewAPI',
      currency_label: 'USD', amount: '12.5', observed_at: new Date().toISOString(), state: 'available'},
    posts: {
      '/model/test': {provider_id: '<main>', status: 'ok', latency_ms: 41},
      '/model/voice/test': {provider_id: 'voice-a', status: 'ok', latency_ms: 55,
        audio_base64: Buffer.from('RIFF0000WAVE').toString('base64'), audio_mime_type: 'audio/wav'},
      '/model/image/test': {action: 'image.generate', registered: true, current: true},
      '/model/test/storage': {status: 'ok', integrity: ['ok'], foreign_key_issues: 0, elapsed_ms: 9},
      '/setup/proactive-diagnostic': {state: 'failed', purpose: 'affection', purpose_label: '想念与亲近',
        variant: null, recipient: 'core_administrator', stage: 'purpose', code: 'contact_purpose_paused',
        error: 'the selected proactive purpose is not currently authorized', suggestion: '检查所选目的的暂停状态和恢复时间。',
        stages: [{stage: 'authorization', state: 'passed'}, {stage: 'purpose', state: 'failed'}]},
    },
    model: {tasks: [{kind: 'conversation.reply', capabilities: [], fields: {provider: {value: '<main>'}, fallback: {value: ''}, max_output_tokens: {value: 1024}}, application_state: 'applied'}],
      providers: [{id: '<main>', capabilities: [], max_output_tokens: 4096}]},
  };
  const {page, requests, root, exports, downloads, confirmations} = harness(responses);
  page.mount(root);
  await flush();

  assert.deepEqual(requests.slice(0, 4).map(item => item[0]), ['GET', 'GET', 'GET', 'GET']);
  assert.match(root.innerHTML, /运行态势/);
  assert.match(root.innerHTML, /Token 统计管理/);
  assert.match(root.innerHTML, /V2 当前没有接收入站消息/);
  assert.match(root.innerHTML, /已生成回复，发送前复核未通过/);
  assert.match(root.innerHTML, /该账号的原生连接已断开/);
  assert.match(root.innerHTML, /当前角色 · 私聊/);
  assert.match(root.innerHTML, /120/);
  assert.match(root.innerHTML, /周期对比/);
  assert.match(root.innerHTML, /40\.0% 命中/);
  assert.match(root.innerHTML, /Provider 消耗排行/);
  assert.match(root.innerHTML, /任务消耗排行/);
  assert.match(root.innerHTML, /class="maint-ranking-feature" data-task-settings-kind="conversation\.reply"/);
  assert.match(root.innerHTML, /class="maint-ranking-filter" data-rank-kind="tasks" data-rank-key="conversation\.reply"/);
  assert.match(styles, /\.maint-ranking-feature:focus-visible/);
  assert.match(root.innerHTML, /每日明细/);
  assert.match(root.innerHTML, /后台软限额/);
  assert.match(root.innerHTML, /maint-budget-meterline/);
  assert.match(root.innerHTML, /软限额 500/);
  assert.match(root.innerHTML, /硬限额 1,000/);
  assert.match(root.innerHTML, /maint-budget-tick[^>]*--limit-pos:50%/);
  assert.match(styles, /\.maint-budget-limits/);
  assert.match(styles, /\.maint-budget-meterline \.maint-budget-tick/);
  assert.match(root.innerHTML, /统计口径/);
  assert.match(root.innerHTML, /当前角色的核心回复与插件任务/);
  assert.match(root.innerHTML, /模型任务路由/);
  assert.match(root.innerHTML, /路由正常/);
  assert.match(root.innerHTML, /data-call-search/);
  assert.match(root.innerHTML, /12 \/ 12 条/);
  assert.match(root.innerHTML, /事件时间线/);
  assert.match(root.innerHTML, /诊断说明与常见问题/);
  assert.match(root.innerHTML, /TTS 合成/);
  assert.match(root.innerHTML, /原生图片工具/);
  assert.match(root.innerHTML, /服务商可能计费/);
  assert.match(root.innerHTML, /data-token-source="memory"/);
  assert.match(root.innerHTML, /NewAPI 账户余额/);
  assert.match(root.innerHTML, /12\.5 USD/);
  assert.match(root.innerHTML, /aria-label="治理与维护二级导航"/);
  assert.match(root.innerHTML, /data-maint-nav="overview" aria-current="page"/);
  assert.match(root.innerHTML, /data-maint-view="diagnostics" hidden/);
  assert.match(root.innerHTML, /data-maint-view="cache" hidden/);
  assert.match(root.innerHTML, /data-maint-view="prompts" hidden/);
  assert.match(root.innerHTML, /data-slots="14"/);
  assert.match(root.innerHTML, /data-slots="48"/);
  assert.match(root.innerHTML, /class="maint-token-hero maint-token-summary maint-token-dashboard-summary"/);
  assert.match(root.innerHTML, /已确认 \d+ 次/);
  assert.match(root.innerHTML, /未确认 \d+ 次/);
  const summaryStart = root.innerHTML.indexOf('<div class="maint-token-hero maint-token-summary maint-token-dashboard-summary">');
  const balanceCard = root.innerHTML.indexOf('<div class="maint-balance-availability"', summaryStart);
  const usageCard = root.innerHTML.indexOf('<div class="maint-usage-total maint-token-summary-card">', summaryStart);
  const budgetCard = root.innerHTML.indexOf('<div class="maint-budget-block maint-token-summary-card"', summaryStart);
  assert.ok(summaryStart >= 0 && balanceCard < usageCard && usageCard < budgetCard);
  assert.match(root.innerHTML, /data-token-detail="daily" aria-selected="true"/);
  assert.match(root.innerHTML, /data-token-breakdown="providers" aria-pressed="true"/);
  assert.match(root.innerHTML, /data-series="cached" style="--segment-height:\d+px;--segment-offset:0px"/);
  assert.match(root.innerHTML, /data-series="input" style="--segment-height:\d+px;--segment-offset:\d+px"/);
  root.click('[data-trend-mode]', {trendMode: 'hourly'});
  assert.match(root.innerHTML, /data-trend-mode="hourly" aria-pressed="true"/);
  assert.match(root.innerHTML, /data-trend-panel="daily" hidden/);
  root.click('[data-token-breakdown]', {tokenBreakdown: 'tasks'});
  assert.match(root.innerHTML, /data-token-breakdown="tasks" aria-pressed="true"/);
  assert.match(root.innerHTML, /data-token-breakdown-panel="providers" hidden/);
  root.click('[data-token-detail]', {tokenDetail: 'providers'});
  assert.match(root.innerHTML, /data-token-detail="providers" aria-selected="true"/);
  assert.match(root.innerHTML, /data-token-detail-panel="daily" hidden/);
  root.click('[data-trend-mode]', {trendMode: 'daily'});
  root.click('[data-token-breakdown]', {tokenBreakdown: 'providers'});
  root.click('[data-token-detail]', {tokenDetail: 'daily'});
  assert.match(root.innerHTML, /估算 Token/);
  assert.match(root.innerHTML, /调用确认率/);
  assert.match(root.innerHTML, /maint-th-pair"><span>缓存命中<\/span><span>缓存写入<\/span>/);
  assert.ok(requests.some(item => item[0] === 'GET' && item[1].endsWith('/balance')));
  assert.match(styles, /\.maint-hour-chart\.maint-hour-chart-48\s*\{[^}]*grid-template-columns:\s*repeat\(48, minmax\(9px, 1fr\)\)/s);
  assert.match(styles, /\.maint-token-hero\.maint-token-summary\.maint-token-dashboard-summary\s*\{[^}]*grid-template-columns:/s);
  assert.doesNotMatch(styles, /\.maint-token-hero\.maint-token-summary\s*>\s*\.maint-budget-block\s*\{/);
  assert.match(styles, /\.maint-token-source-control \.maint-source-tabs\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit/s);
  assert.match(styles, /\.maint-token-toolbar\s*\{[^}]*grid-template-columns:\s*minmax\(0,1fr\)/s);
  assert.match(styles, /\.maint-token-trend-panel \.maint-chart-column > i\s*\{[^}]*bottom:var\(--segment-offset,0px\)/s);
  assert.match(styles, /--maint-token-series-1:#A17BFF/);
  assert.match(styles, /--maint-token-series-2:#3FE0A6/);
  assert.match(styles, /--maint-token-series-3:#FFC258/);
  root.click('[data-maint-nav]', {maintNav: 'cache'});
  assert.match(root.innerHTML, /data-maint-nav="cache" aria-current="page"/);
  assert.match(root.innerHTML, /data-maint-view="overview" hidden/);
  assert.match(root.innerHTML, /data-maint-view="cache"><section class="maint-cache-panel"/);
  assert.match(root.innerHTML, /data-maint-view="diagnostics" hidden/);
  root.click('[data-maint-nav]', {maintNav: 'diagnostics'});
  assert.match(root.innerHTML, /data-maint-nav="diagnostics" aria-current="page"/);
  assert.match(root.innerHTML, /data-maint-view="diagnostics"><section class="maint-diagnostics-panel"/);
  assert.match(root.innerHTML, /data-maint-view="cache" hidden/);
  root.click('[data-maint-nav]', {maintNav: 'prompts'});
  assert.match(root.innerHTML, /data-maint-view="prompts"><section class="maint-prompt-section"/);
  assert.match(root.innerHTML, /data-maint-view="diagnostics" hidden/);
  root.click('[data-maint-nav]', {maintNav: 'overview'});
  assert.match(root.innerHTML, /role="img" aria-label="Token 构成拉链图：缓存命中 40 Token/);
  assert.match(root.innerHTML, /请求前缀签名/);
  assert.match(root.innerHTML, /来源同周期对照/);
  assert.match(root.innerHTML, /各账本独立统计/);
  assert.match(root.innerHTML, /回执覆盖/);
  assert.match(root.innerHTML, /上下文重置/);
  assert.match(root.innerHTML, /system 前缀/);
  assert.match(root.innerHTML, /工具定义/);
  assert.match(root.innerHTML, /材料前缀/);
  assert.match(root.innerHTML, /升级后尚未采集/);
  assert.match(root.innerHTML, /请求前缀签名/);
  assert.match(root.innerHTML, /近期事件与运行快照/);
  assert.match(root.innerHTML, /V2 SQLite 存储/);
  assert.match(root.innerHTML, /本次管理请求/);
  assert.match(root.innerHTML, /统一身份与隔离/);
  const balanceReads = requests.filter(item => item[0] === 'GET' && item[1].endsWith('/balance')).length;
  root.click('[data-balance-refresh]');
  await flush();
  assert.equal(requests.filter(item => item[0] === 'GET' && item[1].endsWith('/balance')).length, balanceReads + 1);
  assert.match(root.innerHTML, /消息聚合与防抖追踪/);
  assert.match(root.innerHTML, /安静窗 600 ms/);
  assert.match(root.innerHTML, /memory\.recall/);
  assert.match(root.innerHTML, /<strong>3<\/strong><span>条已记录主动触达<\/span>/);
  assert.match(root.innerHTML, /1 已送达 \/ 1 失败/);
  assert.match(root.innerHTML, /&lt;main&gt;/);
  assert.doesNotMatch(root.innerHTML, /<main>/);

  root.click('[data-token-source]', {tokenSource: 'memory'});
  assert.match(root.innerHTML, /来源 \/ 未连接/);
  assert.match(root.innerHTML, /记忆插件 Token/);
  assert.doesNotMatch(root.innerHTML, /class="maint-token-hero"/);
  assert.match(root.innerHTML, /当前角色数据不会替代它/);
  root.click('[data-token-source]', {tokenSource: 'companion'});
  assert.match(root.innerHTML, /class="maint-token-hero(?: |")/);

  root.click('[data-period]', {period: 'total'});
  assert.match(root.innerHTML, /aria-pressed=\"true\" class=\"is-active\">总计/);
  assert.match(root.innerHTML, /200/);
  root.click('[data-period]', {period: '7d'});
  assert.match(root.innerHTML, /aria-pressed="true" class="is-active">7 日/);
  assert.match(root.innerHTML, /近 7 日 · Provider 已确认 Token/);
  root.click('[data-period]', {period: '30d'});
  assert.match(root.innerHTML, /aria-pressed="true" class="is-active">30 日/);
  root.click('[data-period]', {period: 'today'});
  root.input('provider_request_failed');
  assert.match(root.innerHTML, /1 \/ 12 条/);
  assert.match(root.innerHTML, /Provider 请求失败/);
  root.input('');
  assert.match(root.innerHTML, /12 \/ 12 条/);
  assert.equal((root.innerHTML.match(/class="maint-call-row/g) || []).length, 10);
  root.click('[data-export-tokens]');
  assert.equal((exports[0].match(/^"调用"/gm) || []).length, 12);
  assert.equal(downloads.length, 1);
  root.click('[data-select-day]', {selectDay: previous});
  assert.match(root.innerHTML, /aria-pressed=\"true\" class=\"is-active\">按日期/);
  assert.match(root.innerHTML, new RegExp('value=\"' + previous + '\"'));
  assert.match(root.innerHTML, /80/);

  root.click('[data-period]', {period: 'today'});
  root.click('[data-select-hour]', {selectHour: today + 'T' + hour, selectDay: today});
  assert.match(root.innerHTML, new RegExp('已聚焦时段：' + today + ' ' + hour + ':00'));

  root.click('[data-diagnostic-severity]', {diagnosticSeverity: 'error'});
  assert.match(root.innerHTML, /data-diagnostic-severity=\"error\" aria-pressed=\"true\"/);
  root.click('[data-diagnostic-category]', {diagnosticCategory: 'model'});
  assert.match(root.innerHTML, /模型链路工作区/);
  assert.match(root.innerHTML, /data-category="model"/);
  assert.doesNotMatch(root.innerHTML, /<article data-severity="[^"]+" data-category="delivery"/);
  assert.match(root.innerHTML, /探测通过就代表真实回复正常吗/);
  root.click('[data-diagnostic-category]', {diagnosticCategory: 'delivery'});
  assert.match(root.innerHTML, /消息收口工作区/);
  assert.match(root.innerHTML, /部分片段失败后会自动补发吗/);
  assert.match(root.innerHTML, /data-category="delivery"/);
  assert.match(root.innerHTML, /data-evidence-kind="debounce"/);
  root.click('[data-diagnostic-category]', {diagnosticCategory: 'identity'});
  assert.match(root.innerHTML, /data-diagnostic-category="identity" aria-pressed="true" class="is-active" title="管理身份与角色账本边界/);
  assert.match(root.innerHTML, /data-evidence-kind="identity"/);
  assert.match(root.innerHTML, /管理身份校验为什么只显示结论/);
  let evidenceOpened = false;
  let focusedIdentity = false;
  root.querySelector = selector => selector === '[data-focus-target="identity-evidence"]' ? {
    closest: value => value === 'details.maint-evidence-drawer' ? {set open(value) { evidenceOpened = value; }} : null,
    scrollIntoView: () => { focusedIdentity = true; },
  } : null;
  root.click('[data-focus-panel]', {focusPanel: 'identity-evidence'});
  assert.equal(evidenceOpened, true);
  assert.equal(focusedIdentity, true);
  root.click('[data-diagnostic-category]', {diagnosticCategory: 'storage'});
  assert.match(root.innerHTML, /data-focus-panel="test-zone">运行只读检查/);
  let focusedStorageCheck = false;
  root.querySelector = selector => selector === '[data-focus-target="test-zone"]' ? {
    closest: () => null,
    scrollIntoView: () => { focusedStorageCheck = true; },
  } : null;
  root.click('[data-focus-panel]', {focusPanel: 'test-zone'});
  assert.equal(focusedStorageCheck, true);
  root.click('[data-diagnostic-category]', {diagnosticCategory: 'all'});
  root.click('[data-diagnostic-severity]', {diagnosticSeverity: 'all'});
  assert.match(root.innerHTML, /插件本地硬预算已触发/);
  assert.match(root.innerHTML, /model_budget_exhausted/);
  assert.match(root.innerHTML, /主动消息链路测试/);
  assert.match(root.innerHTML, /作品与阅读/);
  assert.match(root.innerHTML, /外部资讯/);
  assert.match(root.innerHTML, /关系表达类按已确认私聊窗口和用途设置验证，事件类按当前有效来源验证/);
  assert.doesNotMatch(root.innerHTML, /当前没有安全的主动消息测试接口/);
  assert.match(root.innerHTML, /data-focus-target="proactive-test"/);
  assert.match(root.innerHTML, /其他运行诊断/);
  assert.match(root.innerHTML, /先处理当前分类信号，再运行按需检测/);
  root.change('[data-proactive-purpose]', 'affection');
  root.click('[data-proactive-test]');
  assert.match(root.innerHTML, /确认真实投递/);
  assert.match(root.innerHTML, /确认并开始投递/);
  assert.equal(requests.some(item => item[0] === 'POST' && item[1] === '/setup/proactive-diagnostic'), false);
  root.click('[data-proactive-cancel]');
  assert.match(root.innerHTML, /已取消，本次未调用模型，也未发送消息/);
  assert.equal(requests.some(item => item[0] === 'POST' && item[1] === '/setup/proactive-diagnostic'), false);
  root.click('[data-proactive-test]');
  root.click('[data-proactive-confirm]');
  await flush();
  const proactiveCall = requests.find(item => item[0] === 'POST' && item[1] === '/setup/proactive-diagnostic');
  assert.deepEqual(JSON.parse(JSON.stringify(proactiveCall[2])), {purpose: 'affection', confirm_delivery: true});
  assert.match(root.innerHTML, /the selected proactive purpose is not currently authorized/);
  assert.match(root.innerHTML, /所选联系目的当前已暂停/);
  assert.match(root.innerHTML, /展开技术详情/);
  assert.match(root.innerHTML, /核心管理员/);
  assert.match(root.innerHTML, /目的开关/);
  assert.equal(confirmations.length, 0);
  root.click('[data-rank-kind]', {rankKind: 'providers', rankKey: '<main>'});
  assert.match(root.innerHTML, /已按排行筛选：&lt;main&gt;/);
  root.click('[data-rank-kind]', {rankKind: 'tasks', rankKey: 'conversation.reply'});
  assert.match(root.innerHTML, /已按排行筛选：对话回复/);

  root.click('[data-period]', {period: 'today'});
  root.click('[data-storage-test]');
  await flush();
  assert.ok(requests.some(item => item[0] === 'POST' && item[1] === '/model/test/storage'));
  assert.match(root.innerHTML, /SQLite 完整性检查通过/);
  assert.match(root.innerHTML, /完整性 ok/);
  assert.match(root.innerHTML, /外键问题 0/);
  root.click('[data-provider-probe]');
  await flush();
  assert.match(root.innerHTML, /41 ms/);
  assert.match(root.innerHTML, /Provider 可响应/);
  root.click('[data-voice-test]');
  await flush();
  assert.match(root.innerHTML, /55 ms/);
  root.click('[data-image-test]');
  await flush();
  assert.deepEqual(requests.filter(item => item[0] === 'POST').map(item => item[1]),
    ['/setup/proactive-diagnostic', '/model/test/storage', '/model/test', '/model/voice/test', '/model/image/test']);
  assert.match(root.innerHTML, /注册检查通过/);
  page.unmount();
});

test('empty today rankings identify their scope and link to seven-day rankings', async () => {
  const today = localDay(new Date());
  const previous = localDay(new Date(Date.now() - 86400000));
  const provider = {key: 'provider-a', calls: 2, tokens: 120, input_tokens: 100, output_tokens: 20};
  const task = {...provider, key: 'conversation.reply'};
  const prior = daily(previous, {calls: 2, tokens: 120, providers: [provider], tasks: [task]});
  const current = daily(today);
  const {page, root} = harness({
    maintenance: {
      generated_at: localStamp(), summary: {calls: 2, tokens: 120}, totals: {calls: 2, tokens: 120,
        providers: [provider], tasks: [task]},
      daily: [prior, current], history: [prior, current],
      windows: {'7d': {totals: {calls: 2, tokens: 120}, providers: [provider], tasks: [task]},
        '30d': {totals: {calls: 2, tokens: 120}, providers: [provider], tasks: [task]}},
      hourly: [], recent: [], budget: {}, providers: [], runtime: {},
    },
  });
  page.mount(root);
  await flush();

  assert.equal((root.innerHTML.match(/今日 · 0 项/g) || []).length, 2);
  assert.match(root.innerHTML, /仅统计用量 · 硬预算关闭/);
  assert.doesNotMatch(root.innerHTML, /aria-valuemax="0"/);
  assert.equal((root.innerHTML.match(/查看近 7 日排行/g) || []).length, 2);
  root.click('[data-period]', {period: '7d'});
  assert.equal((root.innerHTML.match(/近 7 日 · 1 项/g) || []).length, 2);
  assert.match(root.innerHTML, /120/);
  assert.doesNotMatch(root.innerHTML, /查看近 7 日排行/);
  page.unmount();
});

test('image diagnostics use the namespaced native action id returned by the host', async () => {
  const {page, requests, root} = harness({
    maintenance: {generated_at: localStamp(), summary: {calls: 0, tokens: 0}, totals: {}, daily: [], history: [],
      windows: {}, providers: [], recent: [], budget: {}, runtime: {}},
    voice: {providers: []},
    image: {actions: [{action: 'image.generate', enabled: true, loaded: true, current: true, registered: true, state: 'ready'}]},
    model: {tasks: [], providers: []},
    posts: {'/model/image/test': {action: 'image.generate', enabled: true, loaded: true, current: true,
      registered: true, state: 'ready', check: 'registration_only'}},
  });
  page.mount(root, {activeView: 'diagnostics'});
  await flush();

  root.click('[data-diagnostic-category]', {diagnosticCategory: 'image_generation'});
  assert.match(root.innerHTML, /data-image-action[^>]*>[\s\S]*?<option value="image\.generate" selected>image\.generate<\/option>/);
  assert.match(root.innerHTML, /工具已注册/);
  assert.doesNotMatch(root.innerHTML, /没有当前注册的生图工具/);
  assert.doesNotMatch(root.innerHTML, /识图工具|没有当前注册的识图工具/);

  root.click('[data-image-test]');
  await flush();
  const imageTest = requests.find(([method, route]) => method === 'POST' && route === '/model/image/test');
  assert.ok(imageTest);
  assert.deepEqual(JSON.parse(JSON.stringify(imageTest[2])), {action: 'image.generate'});
  assert.match(root.innerHTML, /注册检查通过/);
  page.unmount();
});

test('memory diagnostics sample real host RSS on demand and stop polling when requested', async () => {
  let sampleIndex = 0;
  const {page, requests, root, advanceTimers} = harness({
    maintenance: {generated_at: localStamp(), summary: {calls: 0, tokens: 0}, totals: {}, daily: [], history: [],
      windows: {}, providers: [], recent: [], budget: {}, runtime: {}},
    voice: {providers: []}, image: {actions: []}, model: {tasks: [], providers: []},
    memory: () => ({rss_bytes: (100 + sampleIndex++ * 8) * 1024 * 1024, rss_source: 'windows_working_set',
      python_tracing_active: false, python_heap_current_bytes: null}),
  });
  page.mount(root);
  await flush();
  assert.equal(requests.some(item => item[0] === 'GET' && item[1].endsWith('/memory')), false);
  root.click('[data-maint-nav]', {maintNav: 'diagnostics'});
  assert.match(root.innerHTML, /内存增长检测/);
  assert.match(root.innerHTML, /不能单独归因到 V2/);
  assert.match(root.innerHTML, /本功能不会启动全局堆追踪/);

  root.click('[data-memory-start]');
  await flush();
  for (let index = 0; index < 3; index += 1) {
    advanceTimers(10000);
    await flush();
  }
  assert.equal(requests.filter(item => item[0] === 'GET' && item[1].endsWith('/memory')).length, 4);
  assert.match(root.innerHTML, /正在采样/);
  root.click('[data-memory-stop]');
  assert.match(root.innerHTML, /出现增长嫌疑/);
  assert.match(root.innerHTML, /进程工作集在本窗口净增 24\.0 MiB/);
  assert.match(root.innerHTML, /已手动停止/);
  advanceTimers(60000);
  await flush();
  assert.equal(requests.filter(item => item[0] === 'GET' && item[1].endsWith('/memory')).length, 4);
  page.unmount();
});

test('leaving the diagnostics view stops memory sampling', async () => {
  const {page, requests, root, advanceTimers} = harness({
    maintenance: {generated_at: localStamp(), summary: {calls: 0, tokens: 0}, totals: {}, daily: [], history: [],
      windows: {}, providers: [], recent: [], budget: {}, runtime: {}},
    voice: {providers: []}, image: {actions: []}, model: {tasks: [], providers: []},
  });
  page.mount(root, {activeView: 'diagnostics'});
  await flush();
  root.click('[data-memory-start]');
  await flush();
  root.click('[data-maint-nav]', {maintNav: 'overview'});
  advanceTimers(60000);
  await flush();
  assert.equal(requests.filter(item => item[0] === 'GET' && item[1].endsWith('/memory')).length, 1);
  assert.match(root.innerHTML, /已离开排障页，采样已停止/);
  page.unmount();
});

test('proactive diagnostic shows an in-page confirmation, running state and request failure', async () => {
  let rejectRequest;
  const pendingRequest = new Promise((_, reject) => { rejectRequest = reject; });
  const {page, requests, root, confirmations} = harness({
    maintenance: {generated_at: localStamp(), summary: {calls: 0, tokens: 0}, totals: {}, daily: [], history: [],
      windows: {}, providers: [], recent: [], budget: {}, runtime: {}},
    voice: {providers: []}, image: {actions: []}, model: {tasks: [], providers: []},
    posts: {'/setup/proactive-diagnostic': () => pendingRequest},
  });
  page.mount(root);
  await flush();

  root.click('[data-proactive-test]');
  assert.match(root.innerHTML, /确认真实投递/);
  assert.equal(requests.some(item => item[0] === 'POST' && item[1] === '/setup/proactive-diagnostic'), false);
  root.click('[data-proactive-confirm]');
  assert.match(root.innerHTML, /正在运行完整主动消息链路/);
  assert.ok(requests.some(item => item[0] === 'POST' && item[1] === '/setup/proactive-diagnostic'));
  assert.equal(confirmations.length, 0);

  rejectRequest(new Error('gateway timeout'));
  await flush();
  assert.match(root.innerHTML, /gateway timeout/);
  assert.match(root.innerHTML, /确认没有送达前不要再次启动/);
  page.unmount();
});

test('proactive binding failure explains the separate administrator permission and window confirmation', async () => {
  const {page, root} = harness({
    maintenance: {generated_at: localStamp(), summary: {calls: 0, tokens: 0}, totals: {}, daily: [], history: [],
      windows: {}, providers: [], recent: [], budget: {}, runtime: {}},
    voice: {providers: []}, image: {actions: []}, model: {tasks: [], providers: []},
    posts: {'/setup/proactive-diagnostic': {state: 'failed', purpose: 'affection',
      code: 'setup_binding_confirmation_required', stage: 'binding',
      error: 'confirm the core administrator private window first', stages: [
        {stage: 'authorization', state: 'passed'}, {stage: 'binding', state: 'failed'},
      ]}},
  });
  page.mount(root);
  await flush();
  root.click('[data-proactive-test]');
  root.click('[data-proactive-confirm]');
  await flush();
  assert.match(root.innerHTML, /核心管理员窗口尚未绑定确认/);
  assert.match(root.innerHTML, /用户页的“管理员权限”决定谁是核心管理员/);
  assert.match(root.innerHTML, /开始使用.*确认同一私聊窗口/);
  page.unmount();
});

test('proactive authorization diagnostics distinguish Dashboard identity from core permission', async () => {
  for (const [code, title, explanation] of [
    ['dashboard_identity_required', 'Dashboard 登录身份不可用', 'API Key 不会被当作管理员账号使用'],
    ['unbound_identity', 'Dashboard 身份尚未完成绑定', '还没有在这份 AstrBot 安装中完成绑定'],
    ['binding_revoked', 'Dashboard 身份绑定已撤销', '绑定已被撤销'],
    ['binding_expired', 'Dashboard 身份绑定已过期', '绑定已经过期'],
    ['binding_not_active', 'Dashboard 身份绑定尚未生效', '绑定尚未到生效时间'],
    ['binding_rebound', 'Dashboard 身份已重新绑定', '已经被重新绑定'],
    ['scope_installation_mismatch', 'Dashboard 身份不属于当前安装', '属于另一份 AstrBot 安装'],
    ['scope_not_granted', '当前 Dashboard 身份未获该配置范围授权', '没有本次主动测试所需的配置范围授权'],
    ['core_access_required', '当前 Dashboard 身份没有核心管理权限', 'Dashboard 登录成功，但这个身份没有本体核心管理权限'],
    ['authorization_denied', '当前 Dashboard 身份没有配置管理权限', '无法管理主动联系配置'],
    ['authorization_revoked', '当前 Dashboard 身份的管理授权已撤销', '系统不会在测试时自动恢复'],
    ['authorization_expired', '当前 Dashboard 身份的管理授权已过期', '管理授权已经过期'],
    ['authorization_not_active', '当前 Dashboard 身份的管理授权尚未生效', '管理授权尚未生效'],
  ]) {
    const {page, root} = harness({
      maintenance: {generated_at: localStamp(), summary: {calls: 0, tokens: 0}, totals: {}, daily: [], history: [],
        windows: {}, providers: [], recent: [], budget: {}, runtime: {}},
      voice: {providers: []}, image: {actions: []}, model: {tasks: [], providers: []},
      posts: {'/setup/proactive-diagnostic': {state: 'failed', purpose: 'screen', purpose_label: '已授权屏幕观察',
        stage: 'authorization', code, failure_title: title,
        suggestion: explanation, error: code, stages: [{stage: 'authorization', state: 'failed'}]}},
    });
    page.mount(root);
    await flush();
    root.change('[data-proactive-purpose]', 'screen');
    root.click('[data-proactive-test]');
    root.click('[data-proactive-confirm]');
    await flush();
    assert.match(root.innerHTML, new RegExp('测试未通过 · ' + title));
    assert.match(root.innerHTML, new RegExp(explanation));
    assert.match(root.innerHTML, /Dashboard 身份与核心权限<\/span><strong>失败/);
    page.unmount();
  }
});

test('proactive diagnostic explains model skip and shows generation as completed', async () => {
  const {page, root} = harness({
    maintenance: {generated_at: localStamp(), summary: {calls: 0, tokens: 0}, totals: {}, daily: [], history: [],
      windows: {}, providers: [], recent: [], budget: {}, runtime: {}},
    voice: {providers: []}, image: {actions: []}, model: {tasks: [], providers: []},
    posts: {'/setup/proactive-diagnostic': {state: 'skipped', purpose: 'care', purpose_label: '情绪关怀',
      skip_reason: 'model_decision', model_skip_reason: 'no_natural_opening', stages: [
        {stage: 'trusted_candidate', state: 'passed'}, {stage: 'model_generation', state: 'passed'},
        {stage: 'persona_review', state: 'skipped'}, {stage: 'delivery', state: 'skipped'},
      ]}},
  });
  page.mount(root);
  await flush();
  assert.match(root.innerHTML, /手动测试不占日常联系额度/);
  root.click('[data-proactive-test]');
  root.click('[data-proactive-confirm]');
  await flush();
  assert.match(root.innerHTML, /本次未投递 · 模型判断当前不适合发送/);
  assert.match(root.innerHTML, /原因：当前没有自然的开口点/);
  assert.match(root.innerHTML, /模型生成<\/span><strong>通过/);
  assert.match(root.innerHTML, /人格复核<\/span><strong>未执行/);
  assert.doesNotMatch(root.innerHTML, /模型选择跳过/);
  page.unmount();
});

test('proactive diagnostic localizes exhausted attempt budget and identifies the admission stage', async () => {
  const {page, root} = harness({
    maintenance: {generated_at: localStamp(), summary: {calls: 0, tokens: 0}, totals: {}, daily: [], history: [],
      windows: {}, providers: [], recent: [], budget: {}, runtime: {}},
    voice: {providers: []}, image: {actions: []}, model: {tasks: [], providers: []},
    posts: {'/setup/proactive-diagnostic': {state: 'failed', purpose: 'care', purpose_label: '情绪关怀',
      stage: 'attempt_budget', code: 'contact_attempt_budget',
      failure_title: '主动模型调用次数上限已触发',
      suggestion: '该上限已不再单独限制；现在只按窗口每日实际联系额度限制发送。',
      error: 'contact model-call allowance is exhausted', stages: [
        {stage: 'authorization', state: 'passed'}, {stage: 'settings', state: 'passed'},
        {stage: 'purpose', state: 'passed'}, {stage: 'trusted_candidate', state: 'passed'},
        {stage: 'attempt_budget', state: 'failed'},
      ]}},
  });
  page.mount(root);
  await flush();
  root.click('[data-proactive-test]');
  root.click('[data-proactive-confirm]');
  await flush();
  assert.match(root.innerHTML, /测试未通过 · 主动模型调用次数上限已触发/);
  assert.match(root.innerHTML, /该上限已不再单独限制/);
  assert.doesNotMatch(root.innerHTML, /模型决策调用额度已用 12 \/ 12/);
  assert.match(root.innerHTML, /模型调用次数上限<\/span><strong>失败/);
  assert.doesNotMatch(root.innerHTML, /旧版本的主动模型调用/, 'diagnostics must not label the record as old-version data');
  assert.match(root.innerHTML, /展开技术详情/);
  page.unmount();
});

test('maintenance manually clears transient diagnostic results without touching the ledger', async () => {
  const h = harness({
    maintenance: {generated_at: localStamp(), summary: {calls: 1, tokens: 12}, totals: {}, daily: [], history: [],
      windows: {}, providers: [{id: '<main>', model: 'model-a'}], recent: [], budget: {}, runtime: {}},
    voice: {providers: [{id: 'voice-a'}]}, image: {actions: [{action: 'image.generate', registered: true, current: true}]},
    model: {tasks: [], providers: [{id: '<main>', capabilities: []}]},
    posts: {
      '/model/test': {provider_id: '<main>', status: 'ok', latency_ms: 18},
      '/model/voice/test': {provider_id: 'voice-a', status: 'ok', latency_ms: 19,
        audio_base64: Buffer.from('RIFF0000WAVE').toString('base64'), audio_mime_type: 'audio/wav'},
      '/model/image/test': {action: 'image.generate', registered: true, current: true},
      '/model/test/storage': {status: 'ok', integrity: ['ok'], foreign_key_issues: 0, elapsed_ms: 7},
    },
  });
  h.page.mount(h.root, {activeView: 'diagnostics'});
  await flush();
  h.root.click('[data-provider-probe]');
  await flush();
  assert.match(h.root.innerHTML, /文本 Provider 探测通过/);
  h.root.click('[data-voice-test]');
  await flush();
  h.root.click('[data-image-test]');
  await flush();
  h.root.click('[data-storage-test]');
  await flush();
  assert.match(h.root.innerHTML, /TTS 合成通过/);
  assert.match(h.root.innerHTML, /注册检查通过/);
  assert.match(h.root.innerHTML, /SQLite 完整性检查通过/);
  const requestCount = h.requests.length;
  h.root.click('[data-clear-diagnostic-results]');
  assert.doesNotMatch(h.root.innerHTML, /文本 Provider 探测通过/);
  assert.doesNotMatch(h.root.innerHTML, /TTS 合成通过|注册检查通过|SQLite 完整性检查通过/);
  assert.match(h.root.innerHTML, /本页测试结果已清除；运行账本没有改动/);
  assert.deepEqual(h.revokedAudioUrls, ['blob:maintenance-audio']);
  assert.equal(h.requests.length, requestCount);
  h.page.unmount();
});

test('maintenance automatically clears transient diagnostic results after fifteen minutes', async () => {
  const h = harness({
    maintenance: {generated_at: localStamp(), summary: {calls: 1, tokens: 12}, totals: {}, daily: [], history: [],
      windows: {}, providers: [{id: '<main>', model: 'model-a'}], recent: [], budget: {}, runtime: {}},
    voice: {providers: []}, image: {actions: []}, model: {tasks: [], providers: [{id: '<main>', capabilities: []}]},
    posts: {'/model/test': {provider_id: '<main>', status: 'ok', latency_ms: 18}},
  });
  h.page.mount(h.root, {activeView: 'diagnostics'});
  await flush();
  h.root.click('[data-provider-probe]');
  await flush();
  assert.match(h.root.innerHTML, /文本 Provider 探测通过/);
  h.advanceTimers(15 * 60 * 1000);
  assert.doesNotMatch(h.root.innerHTML, /文本 Provider 探测通过/);
  assert.match(h.root.innerHTML, /本页测试结果已自动清除（15 分钟）；运行账本没有改动/);
  h.page.unmount();
});

test('diagnostic results survive leaving and reopening the maintenance route', async () => {
  const h = harness({
    maintenance: {generated_at: localStamp(), summary: {calls: 0, tokens: 0}, totals: {}, daily: [], history: [],
      windows: {}, providers: [{id: '<main>', model: 'model-a'}], recent: [], budget: {}, runtime: {}},
    voice: {providers: []}, image: {actions: []}, model: {tasks: [], providers: [{id: '<main>', capabilities: []}]},
    posts: {'/model/test': {provider_id: '<main>', status: 'ok', latency_ms: 18}},
  });
  h.page.mount(h.root, {activeView: 'diagnostics'});
  await flush();
  h.root.click('[data-provider-probe]');
  await flush();
  assert.match(h.root.innerHTML, /文本 Provider 探测通过/);

  h.page.unmount();
  h.page.mount(h.root, {activeView: 'diagnostics'});
  assert.match(h.root.innerHTML, /文本 Provider 探测通过/);
  await flush();
  assert.match(h.root.innerHTML, /文本 Provider 探测通过/);
  h.page.unmount();
});

test('successful account balance stays visible when maintenance route remounts', async () => {
  const h = harness({
    maintenance: {generated_at: localStamp(), summary: {calls: 0, tokens: 0}, totals: {}, daily: [], history: [],
      windows: {}, providers: [], recent: [], budget: {}, runtime: {}},
    voice: {providers: []}, image: {actions: []}, model: {tasks: [], providers: []},
    balance: {configured: true, available: true, provider: 'newapi', provider_label: 'NewAPI',
      currency_label: 'USD', amount: '12.50', observed_at: new Date().toISOString(), state: 'available'},
  });
  h.page.mount(h.root);
  await flush();
  assert.match(h.root.innerHTML, /NewAPI 账户余额/);
  assert.match(h.root.innerHTML, /12\.5 USD/);

  h.page.unmount();
  h.page.mount(h.root);
  assert.match(h.root.innerHTML, /NewAPI 账户余额/);
  assert.match(h.root.innerHTML, /12\.5 USD/);
  h.page.unmount();

  const missingAmount = harness({
    maintenance: {generated_at: localStamp(), summary: {calls: 0, tokens: 0}, totals: {}, daily: [], history: [],
      windows: {}, providers: [], recent: [], budget: {}, runtime: {}},
    voice: {providers: []}, image: {actions: []}, model: {tasks: [], providers: []},
    balance: {configured: true, available: true, provider: 'newapi', provider_label: 'NewAPI',
      currency_label: 'USD', amount: null, observed_at: new Date().toISOString(), state: 'available'},
  });
  missingAmount.page.mount(missingAmount.root);
  await flush();
  assert.match(missingAmount.root.innerHTML, /未提供余额/);
  assert.doesNotMatch(missingAmount.root.innerHTML, /<b>0 USD<\/b>/);
  missingAmount.page.unmount();
});

test('maintenance shows connected external token ledgers without mixing their budgets', async () => {
  const today = localDay(new Date());
  const previous = localDay(new Date(Date.now() - 86400000));
  const dimension = (key, tokens) => ({key, calls: 1, tokens, input_tokens: tokens - 10,
    output_tokens: 10, failures: 0, reported_tokens: tokens - 15, estimated_tokens: 15, avg_latency_ms: 80});
  const day = (key, tokens, reported, estimated) => ({...dimension('day', tokens), key, day: key, reported_tokens: reported, estimated_tokens: estimated,
    providers: [dimension('memory-model', tokens)], tasks: [dimension('memory.recall', tokens)]});
  const responses = {
    maintenance: {
      generated_at: new Date().toISOString(), summary: {calls: 0, tokens: 0}, totals: {calls: 0, tokens: 0},
      daily: [daily(today)], history: [], recent: [{at: today + 'T08:00:00', kind: 'conversation.reply',
        provider: 'v2-role-provider', tokens: 999, input_tokens: 800, output_tokens: 199}], budget: {}, providers: [],
      external_token_sources: {memory: {
        available: true, installed: true, display_name: '记忆伴侣', updated_at: new Date().toISOString(),
        linkage: {state: 'ready', display_name: '记忆伴侣', counts: {profile: 2, preference: 3,
          relationship: 1, private_conversation: 4, other: 0, total: 10}, categories: [
          {key: 'profile', count: 2}, {key: 'preference', count: 3}, {key: 'relationship', count: 1},
          {key: 'private_conversation', count: 4}, {key: 'other', count: 0},
        ]},
        totals: {calls: 2, tokens: 115, reported_tokens: 70, estimated_tokens: 45, input_tokens: 85,
          output_tokens: 30, failures: 0, cached_input_tokens: 20, cache_creation_tokens: 5, avg_latency_ms: 80},
        by_provider: [dimension('memory-model', 115)], by_task: [dimension('memory.recall', 115)],
        history: [day(previous, 40, 20, 20), day(today, 75, 50, 25)],
        daily: [day(previous, 40, 20, 20), day(today, 75, 50, 25)],
        hourly: [{key: today + 'T09', calls: 2, tokens: 115}],
        recent: [{at: today + 'T09:00:00', kind: 'memory.recall', provider: 'memory-model', state: 'generated',
          tokens: 75, input_tokens: 65, output_tokens: 10, reported_tokens: 60, estimated_tokens: 15, latency_ms: 80}],
      }},
    },
  };
  const {page, root} = harness(responses);
  page.mount(root);
  await flush();
  root.click('[data-token-source]', {tokenSource: 'memory'});
  assert.match(root.innerHTML, /记忆伴侣 独立账本/);
  assert.match(root.innerHTML, /75/);
  assert.match(root.innerHTML, /50/);
  assert.match(root.innerHTML, /25/);
  assert.match(root.innerHTML, /V2 角色预算保持独立|不计入 V2 角色预算/);
  assert.match(root.innerHTML, /V2 联动摘要/);
  assert.match(root.innerHTML, /只读分类计数，不含记忆正文/);
  assert.match(root.innerHTML, /私聊内容/);
  assert.match(root.innerHTML, /<dt>合计<\/dt><dd>10<\/dd>/);
  const externalOverview = root.innerHTML.split('data-maint-view="overview"')[1].split('data-maint-view="prompts"')[0];
  assert.match(externalOverview, /已确认 Token/);
  assert.doesNotMatch(externalOverview, /后台插件任务硬预算|ACCOUNT BALANCE|data-balance-refresh/);
  root.click('[data-maint-nav]', {maintNav: 'recent'});
  const externalRecent = root.innerHTML.split('data-maint-view="recent"')[1];
  assert.match(externalRecent, /记忆伴侣 · 1 \/ 1 条/);
  assert.match(externalRecent, /memory-model/);
  assert.doesNotMatch(externalRecent, /v2-role-provider/);
  assert.match(externalRecent, /数据源：记忆伴侣独立账本/);
  root.click('[data-maint-nav]', {maintNav: 'cache'});
  const externalCache = root.innerHTML.split('data-maint-view="cache"')[1].split('data-maint-view="recent"')[0];
  assert.match(externalCache, /该来源没有提供请求前缀签名和完整缓存诊断数据/);
  assert.doesNotMatch(externalCache, /请求前缀签名<\/span><strong>/);
  root.click('[data-period]', {period: '7d'});
  assert.match(root.innerHTML, /115/);
  assert.match(root.innerHTML, /70/);
  assert.match(root.innerHTML, /45/);
  assert.match(root.innerHTML, /记忆伴侣 独立账本/);
  page.unmount();
});

test('maintenance keeps Reality usage unknown independently of Together and core replies', async () => {
  const today = localDay();
  const previous = localDay(new Date(Date.now() - 86400000));
  const unknown = daily(today, {calls: 1, tokens: null, reported_tokens: null, estimated_tokens: 0,
    unconfirmed_calls: 1, input_tokens: null, output_tokens: null, cached_input_tokens: null,
    cache_creation_tokens: null, avg_latency_ms: 80,
    providers: [{key: 'camera-vision', calls: 1, tokens: null, input_tokens: null, output_tokens: null,
      reported_tokens: null, estimated_tokens: 0, cached_input_tokens: null, cache_creation_tokens: null}],
    tasks: [{key: 'reality_touch_camera', calls: 1, tokens: null, reported_tokens: null,
      estimated_tokens: 0, cached_input_tokens: null, cache_creation_tokens: null}]});
  const reported = daily(previous, {calls: 1, tokens: 120, reported_tokens: 120, estimated_tokens: 0,
    input_tokens: 100, output_tokens: 20, cached_input_tokens: null, cache_creation_tokens: null});
  const h = harness({maintenance: {generated_at: today + 'T09:00:00', summary: daily(today),
    totals: daily(today), daily: [], history: [], providers: [], budget: {},
    recent: [{at: today + 'T08:00:00', kind: 'conversation.reply', provider: 'core-reply-model', tokens: 999}],
    external_token_sources: {
      together: {available: false, installed: false},
      reality: {available: true, installed: true, display_name: '现实观察',
        totals: {...reported, calls: 2, unconfirmed_calls: 1}, history: [reported, unknown], daily: [reported, unknown],
        by_provider: unknown.providers, by_task: unknown.tasks, recent: [
          {at: today + 'T09:00:00', kind: 'reality_touch_camera', provider: 'camera-vision', state: 'generated',
            tokens: null, reported_tokens: null, estimated_tokens: 0, input_tokens: null, output_tokens: null,
            cache_hit_reported: false, cache_creation_tokens: null, latency_ms: 80, usage_availability: 'unavailable'},
        ]},
    }}});
  h.page.mount(h.root);
  await flush();
  h.root.click('[data-token-source]', {tokenSource: 'reality'});
  assert.match(h.root.innerHTML, /现实观察 独立账本/);
  assert.match(h.root.innerHTML, /今日 · 来源 Token<\/span><strong>未知<\/strong>/);
  assert.match(h.root.innerHTML, /用量回执未知 1 次/);
  assert.match(h.root.innerHTML, /缓存写入<\/span><strong>暂无回执/);
  assert.match(h.root.innerHTML, /用量回执不完整/);
  assert.doesNotMatch(h.root.innerHTML, /-100\.0%/);
  assert.match(h.root.innerHTML, new RegExp('title="' + today + ' · 未知 Token · 1 次调用"'));
  h.root.click('[data-maint-nav]', {maintNav: 'recent'});
  const recent = h.root.innerHTML.split('data-maint-view="recent"')[1];
  assert.match(recent, /现实观察 · 1 \/ 1 条/);
  assert.match(recent, /摄像头视觉分析/);
  assert.match(recent, /camera-vision/);
  assert.match(recent, /已完成<small>用量回执未知<\/small>/);
  assert.doesNotMatch(recent, /core-reply-model/);
  h.root.click('[data-period]', {period: '7d'});
  assert.match(h.root.innerHTML, /近 7 日 · 来源 Token<\/span><strong>120<\/strong>/);
  assert.match(h.root.innerHTML, /用量回执未知 1 次/);
  assert.match(h.root.innerHTML, /缓存写入<\/span><strong>暂无回执/);
  h.root.click('[data-token-source]', {tokenSource: 'together'});
  assert.match(h.root.innerHTML, /一起房间内部统计暂不可用/);
  h.page.unmount();

  const onlyUnknown = harness({maintenance: {generated_at: today + 'T09:00:00', summary: daily(today),
    totals: daily(today), providers: [], budget: {}, daily: [], history: [],
    recent: [], external_token_sources: {reality: {available: true, installed: true, display_name: '现实观察',
      totals: unknown, daily: [unknown], history: [unknown], by_provider: [], by_task: [], recent: []}}}});
  onlyUnknown.page.mount(onlyUnknown.root);
  await flush();
  onlyUnknown.root.click('[data-token-source]', {tokenSource: 'reality'});
  for (const period of ['7d', '30d', 'total']) {
    onlyUnknown.root.click('[data-period]', {period});
    assert.match(onlyUnknown.root.innerHTML, /来源 Token<\/span><strong>未知<\/strong>/);
    assert.match(onlyUnknown.root.innerHTML, /输入 \/ 输出<\/span><strong>无回执<\/strong>/);
    assert.match(onlyUnknown.root.innerHTML, /缓存写入<\/span><strong>暂无回执/);
  }
  onlyUnknown.page.unmount();
});

test('maintenance explains quota held by calls awaiting usage receipts', async () => {
  const today = localDay();
  const h = harness({maintenance: {generated_at: today + 'T09:00:00', summary: daily(today), totals: daily(today),
    daily: [daily(today)], history: [], recent: [], providers: [],
    budget: {hard_limit_enabled: true, limit_tokens: 3000000, spent_tokens: 0, held_tokens: 2956296,
      pending_usage_calls: 30, pending_usage_tokens: 2956296,
      reservation_sources: [{kind: 'calendar.plan', state: 'unknown', calls: 30, held_tokens: 2956296}]}}});
  h.page.mount(h.root);
  await flush();
  assert.match(h.root.innerHTML, /等待用量回执 30 次 · 占用预留 2,956,296 Token/);
  assert.match(h.root.innerHTML, /日程规划 · 结果未知/);
  assert.match(h.root.innerHTML, /已入账 <b>0<\/b>/);
  assert.match(h.root.innerHTML, /软限额关闭 · 本地硬预算已启用/);
});

test('disabled local budgets retain usage records without an exhaustion warning', async () => {
  const today = localDay();
  const h = harness({maintenance: {generated_at: today + 'T09:00:00', summary: daily(today), totals: daily(today),
    daily: [daily(today)], history: [], recent: [], providers: [],
    budget: {hard_limit_enabled: false, soft_limit_enabled: false, limit_tokens: 1000,
      spent_tokens: 2000, held_tokens: 3000, pending_usage_calls: 1, pending_usage_tokens: 3000,
      reservation_sources: [{kind: 'calendar.plan', state: 'unknown', calls: 1, held_tokens: 3000}]}}});
  h.page.mount(h.root);
  await flush();
  assert.match(h.root.innerHTML, /仅统计用量 · 硬预算关闭/);
  assert.match(h.root.innerHTML, /本地限额已关闭 · 保留用量统计/);
  assert.match(h.root.innerHTML, /已入账 <b>2,000<\/b>/);
  assert.match(h.root.innerHTML, /等待用量回执 1 次 · 占用预留 3,000 Token/);
  h.root.click('[data-maint-nav]', {maintNav: 'diagnostics'});
  assert.doesNotMatch(h.root.innerHTML, /今日硬预算接近上限/);
  h.page.unmount();
});

test('maintenance distinguishes empty usage from cache receipts without computable hit rate', async () => {
  const today = localDay(new Date());
  const response = {
    generated_at: new Date().toISOString(), summary: {calls: 0, unconfirmed_calls: 0},
    totals: {calls: 0, tokens: 0, providers: [], tasks: []}, daily: [daily(today)], history: [],
    recent: [], budget: {}, providers: [],
  };
  const empty = harness({maintenance: response, voice: {providers: []}, image: {actions: []}});
  empty.page.mount(empty.root);
  await flush();
  assert.match(empty.root.innerHTML, /这个范围没有模型调用/);
  assert.match(empty.root.innerHTML, /没有已加载的 V2 文本 Provider/);
  assert.match(empty.root.innerHTML, /暂无可计算命中率/);
  assert.match(empty.root.innerHTML, /收到用量后显示 Token 构成/);
  assert.match(empty.root.innerHTML, /等待完整采集的请求剖面/);
  assert.match(empty.root.innerHTML, /data-series="coverage"><span>确认覆盖<\/span><strong>—<\/strong>/);
  empty.page.unmount();

  response.summary.calls = 1;
  response.daily[0] = daily(today, {calls: 1, tokens: 20, cache_telemetry_calls: 1, cache_creation_tokens: 8});
  response.history = [response.daily[0]];
  const partial = harness({maintenance: response, voice: {providers: []}, image: {actions: []}});
  partial.page.mount(partial.root);
  await flush();
  assert.match(partial.root.innerHTML, /暂无命中率|等待确认/);
  assert.match(partial.root.innerHTML, /写入量仍按 Provider 实报展示/);
  assert.match(partial.root.innerHTML, /缓存写入/);
  partial.page.unmount();
});

test('maintenance completes the V1 task prompt workspace on the V2 model contract', async () => {
  const today = localDay(new Date());
  const baseMaintenance = {
    generated_at: new Date().toISOString(),
    summary: {calls: 0, tokens: 0, unconfirmed_calls: 0},
    totals: {calls: 0, tokens: 0, providers: [], tasks: []},
    daily: [daily(today)], history: [], recent: [], budget: {}, providers: [],
  };
  const task = (kind, prompt, token, explicit = false) => ({
    kind, lane: 'foreground', capabilities: ['text'], default_prompt: prompt, prompt_version: '7', edit_token: token,
    application_state: 'applied', fields: {
      prompt: {value: explicit ? '已有自定义提示词' : prompt, inherited_value: prompt,
        source: explicit ? 'window' : 'default', inherited_source: 'default', explicit},
    },
  });
  const diary = task('role.journal.diary', '日记整理规则：{{persona_context}}', 'c'.repeat(64));
  diary.fields.prompt = {value: '角色级日记提示', inherited_value: '角色级日记提示',
    source: 'role', inherited_source: 'role', explicit: false};
  const dream = task('role.journal.dream', '', 'g'.repeat(64));
  dream.fields.prompt = {value: '角色级梦境提示', inherited_value: '角色级梦境提示',
    source: 'role', inherited_source: 'role', explicit: false};
  const imageReview = task('image_review', '图片审核规则：{{image_observations}}', 'f'.repeat(64));
  delete imageReview.default_prompt;
  const initial = {
    tasks: [
      task('conversation.reply', '回复规则：{{task_input}}\n{{role_instruction}}', 'a'.repeat(64)),
      task('proactive.compose', '主动联系规则：{{recent_history}}', 'b'.repeat(64), true),
      diary,
      dream,
      imageReview,
    ],
    providers: [{id: '<main>', capabilities: ['text'], max_output_tokens: 4096}],
  };
  let latest = {
    ...initial,
    tasks: initial.tasks.map(item => item.kind === 'conversation.reply'
      ? {...item, edit_token: 'd'.repeat(64), fields: {...item.fields,
        prompt: {...item.fields.prompt, value: '服务器最新完整提示词', source: 'window', explicit: true}}}
      : item),
  };
  const saved = [];
  const responses = {
    maintenance: baseMaintenance,
    model: [initial, latest],
    confirmations: [false, true],
    posts: {
      '/model/configure': body => {
        saved.push(body);
        if (saved.length === 1) {
          return {status: 'error', data: {code: 'configuration_revision_conflict'}};
        }
        const updated = latest.tasks.find(item => item.kind === body.kind);
        return {task: {...updated, edit_token: 'e'.repeat(64), fields: {...updated.fields,
          prompt: {...updated.fields.prompt, value: body.reset.length ? updated.default_prompt : body.changes.prompt,
            source: body.reset.length ? 'default' : 'window', explicit: !body.reset.length}}}};
      },
    },
  };
  const {page, requests, root, confirmations} = harness(responses);
  page.mount(root, {activeView: 'prompts'});
  await flush();

  assert.match(root.innerHTML, /模型任务内置默认提示/);
  assert.match(root.innerHTML, /运行时提示组成/);
  assert.match(root.innerHTML, /业务任务指令/);
  assert.match(root.innerHTML, /本页可编辑/);
  assert.match(root.innerHTML, /恢复角色级继承值/);
  assert.match(root.innerHTML, /回复规则：\{\{task_input\}\}/);
  assert.match(root.innerHTML, /内置版本/);
  assert.match(root.innerHTML, /v7/);
  assert.match(root.innerHTML, /动态字段/);
  assert.match(root.innerHTML, /本次任务的结构化输入/);
  assert.match(root.innerHTML, /当前角色的角色指令/);
  assert.match(root.innerHTML, /data-prompt-task="image_review"/);
  assert.match(root.innerHTML, /data-prompt-group="视觉与媒体"/);
  assert.doesNotMatch(root.innerHTML, /完整内置任务提示词|完整有效提示词/);
  root.click('[data-prompt-task]', {promptTask: 'image_review'});
  assert.match(root.innerHTML, /（内置提示未返回）/);

  root.input('日记', '[data-prompt-search]');
  assert.match(root.innerHTML, /data-prompt-task="role\.journal\.diary"/);
  assert.doesNotMatch(root.innerHTML, /data-prompt-task="conversation\.reply"/);
  root.input('', '[data-prompt-search]');
  root.click('[data-prompt-mode]', {promptMode: 'customized'});
  assert.match(root.innerHTML, /data-prompt-task="proactive\.compose"/);
  assert.match(root.innerHTML, /data-prompt-task="role\.journal\.diary"/);
  assert.doesNotMatch(root.innerHTML, /data-prompt-task="conversation\.reply"/);
  root.click('[data-prompt-task]', {promptTask: 'role.journal.dream'});
  assert.match(root.innerHTML, /（内置任务提示为空）/);
  assert.match(root.innerHTML, /角色级梦境提示/);
  root.click('[data-prompt-mode]', {promptMode: 'all'});
  root.click('[data-prompt-group]', {promptGroup: '对话'});
  assert.match(root.innerHTML, /data-prompt-task="conversation\.reply"/);
  assert.doesNotMatch(root.innerHTML, /data-prompt-task="role\.journal\.diary"/);
  root.click('[data-prompt-task]', {promptTask: 'conversation.reply'});

  root.input('编辑后的完整提示词', '[data-prompt-draft]');
  assert.match(root.innerHTML, /有未保存更改/);
  root.click('[data-prompt-save]');
  await flush();
  assert.equal(saved[0].kind, 'conversation.reply');
  assert.equal(saved[0].changes.prompt, '编辑后的完整提示词');
  assert.equal(saved[0].reset.length, 0);
  assert.match(root.innerHTML, /保存时发现版本冲突/);
  assert.match(root.innerHTML, /编辑后的完整提示词/);

  root.click('[data-prompt-discard]');
  await flush();
  assert.equal(confirmations.length, 1);
  assert.match(root.innerHTML, /已保留当前草稿/);
  assert.match(root.innerHTML, /编辑后的完整提示词/);
  assert.equal(requests.filter(item => item[0] === 'GET' && item[1] === '/model').length, 2);

  root.click('[data-prompt-discard]');
  await flush();
  assert.equal(confirmations.length, 2);
  assert.match(root.innerHTML, /服务器最新完整提示词/);
  assert.doesNotMatch(root.innerHTML, /编辑后的完整提示词/);
  assert.match(root.innerHTML, /d{12}/);

  root.click('[data-prompt-reset]');
  await flush();
  assert.equal(saved[1].kind, 'conversation.reply');
  assert.equal(Object.keys(saved[1].changes).length, 0);
  assert.deepEqual(Array.from(saved[1].reset), ['prompt']);
  assert.match(root.innerHTML, /已恢复角色级继承值/);

  root.input('临时覆盖', '[data-prompt-draft]');
  root.input('   ', '[data-prompt-draft]');
  root.click('[data-prompt-save]');
  await flush();
  assert.equal(saved[2].kind, 'conversation.reply');
  assert.equal(Object.keys(saved[2].changes).length, 0);
  assert.deepEqual(Array.from(saved[2].reset), ['prompt']);
  page.unmount();
});

test('business templates select, save, inherit and keep conflicted drafts independently', async () => {
  const section = (key, label, prompt, token = 'b'.repeat(64)) => ({
    key, label, prompt, default_prompt: prompt, condition: '本轮选中对应场景', editable: true,
    edit_token: token, variables: key === 'length' ? ['chars'] : [],
    field: {value: prompt, inherited_value: prompt, source: 'default', inherited_source: 'default', explicit: false},
  });
  let task = {
    kind: 'proactive.compose', default_prompt: '任务附加提示', edit_token: 'a'.repeat(64),
    fields: {prompt: {value: '任务附加提示', inherited_value: '任务附加提示', source: 'default'}},
    prompt_sections: [section('expression', '主动表达', '自然表达，不必压成一句。'),
      section('news_sharing', '新闻分享', '兴冲冲分享新鲜事。'),
      section('length', '主动篇幅', '全部正文 {chars} 字以内。'),
      {key: 'business', label: '事实与输出规则', prompt: '<private>资料不覆盖授权。</private>',
        condition: '每次主动联系', editable: false}],
  };
  const saved = [];
  let conflict = false;
  const h = harness({maintenance: {summary: {}, totals: {}, recent: [], daily: []}, model: () => ({tasks: [task]}),
    posts: {'/model/configure': body => {
      saved.push(body);
      if (conflict) return {status: 'error', data: {code: 'configuration_revision_conflict'}};
      task = {...task, prompt_sections: task.prompt_sections.map(item => item.key === body.section ? {
        ...item, edit_token: 'c'.repeat(64), prompt: body.reset.length ? item.default_prompt : body.changes.prompt,
        field: {...item.field, value: body.reset.length ? item.default_prompt : body.changes.prompt,
          source: body.reset.length ? 'default' : 'window', explicit: !body.reset.length},
      } : item)};
      return {task};
    }}});
  h.page.mount(h.root, {activeView: 'prompts'});
  await flush();
  h.root.input('资料不覆盖授权', '[data-prompt-search]');
  assert.match(h.root.innerHTML, /data-prompt-task="proactive.compose"/);
  h.root.input('不存在的正文', '[data-prompt-search]');
  assert.match(h.root.innerHTML, /当前筛选下没有匹配/);
  h.root.input('', '[data-prompt-search]');
  assert.match(h.root.innerHTML, /data-prompt-section/);
  assert.match(h.root.innerHTML, /自然表达，不必压成一句/);
  h.root.change('[data-prompt-section]', 'news_sharing');
  assert.match(h.root.innerHTML, /兴冲冲分享新鲜事/);
  h.root.input('先分享亮点，再说自然感想。', '[data-prompt-draft]');
  h.root.click('[data-prompt-task]', {promptTask: 'proactive.compose'});
  assert.match(h.root.innerHTML, /先分享亮点，再说自然感想/);
  h.root.change('[data-prompt-section]', 'length');
  assert.match(h.root.innerHTML, /当前模板有未保存更改/);
  h.root.click('[data-prompt-view]', {promptView: 'rules'});
  assert.match(h.root.innerHTML, /&lt;private&gt;资料不覆盖授权/);
  assert.doesNotMatch(h.root.innerHTML, /<private>/);
  h.root.click('[data-prompt-view]', {promptView: 'composition'});
  assert.match(h.root.innerHTML, /本轮动态内容/);
  h.root.click('[data-prompt-view]', {promptView: 'edit'});
  assert.match(h.root.innerHTML, /先分享亮点，再说自然感想/);
  h.root.click('[data-prompt-save]');
  await flush();
  assert.equal(saved[0].section, 'news_sharing');
  assert.equal(saved[0].edit_token, 'b'.repeat(64));
  assert.equal(saved[0].changes.prompt, '先分享亮点，再说自然感想。');
  assert.match(h.root.innerHTML, /提示词已保存/);
  h.root.click('[data-prompt-reset]');
  await flush();
  assert.equal(saved[1].section, 'news_sharing');
  assert.equal(saved[1].edit_token, 'c'.repeat(64));
  assert.deepEqual(Array.from(saved[1].reset), ['prompt']);
  assert.match(h.root.innerHTML, /已恢复上级继承值/);
  assert.match(h.root.innerHTML, /兴冲冲分享新鲜事/);
  h.root.change('[data-prompt-section]', 'length');
  assert.match(h.root.innerHTML, /当前强度档位的总字数/);
  conflict = true;
  h.root.input('全部正文 {chars} 字以内，保持自然段落。', '[data-prompt-draft]');
  h.root.click('[data-prompt-save]');
  await flush();
  assert.equal(saved[2].section, 'length');
  assert.match(h.root.innerHTML, /保存时发现版本冲突/);
  assert.match(h.root.innerHTML, /保持自然段落/);
  h.root.click('[data-prompt-reset]');
  await flush();
  assert.equal(saved.length, 3);
  h.page.unmount();
});
