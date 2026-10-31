const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PAGE = '../pages/陪伴小屋/';
const workbench = fs.readFileSync(path.resolve(__dirname, PAGE + 'workbench-page.js'), 'utf8');
const adapter = fs.readFileSync(path.resolve(__dirname, PAGE + 'reply-page.js'), 'utf8');
const appPage = fs.readFileSync(path.resolve(__dirname, PAGE + 'app.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const drains = async (times = 8) => { for (let index = 0; index < times; index += 1) await flush(); };
const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const TOKEN = 'a'.repeat(64);
const PREFIX = '/astrbot_plugin_private_companion/';
const plain = value => JSON.parse(JSON.stringify(value));

/* The registered group is window-scoped; these are the fields the page edits. */
const DEFAULTS = {
  enabled: false, llm_controlled: false, rules_enabled: true, rule_mode: 'words',
  split_words: '["。","？","！","~","…","“","\\n"]', match_width_variants: true,
  rule_regex: '.*?[。？！~…\\n]+|.+$',
  threshold: 500, min_segment_chars: 5, max_segments: 5, prompt: '', streaming: false, streaming_mode: 'buffered',
  part_gap_mode: 'fixed', part_gap_ms: 0, part_gap_min_ms: 1500, part_gap_max_ms: 3500,
  part_gap_log_base: 1.8, image_position: 'after_text', voice_enabled: false,
  group_wakeup_enabled: true, group_wakeup_direct_words: '[]',
  group_wakeup_context_words: '[]', group_wakeup_interest_keywords: '[]',
  group_wakeup_question_enabled: false, group_wakeup_bot_name: '',
  group_wakeup_primary_member_ids: '[]', group_wakeup_owner_direct_words: '[]', group_tool_profile: 'full',
  group_wakeup_interest_probability: 0.18, group_wakeup_question_threshold: 65,
  group_wakeup_cold_group_enabled: false, group_wakeup_cold_group_threshold: 65,
  group_wakeup_cold_group_idle_minutes: 25, group_wakeup_cooldown_seconds: 90,
  group_wakeup_fatigue_limit: 5, group_wakeup_fatigue_decay_minutes: 90,
  group_wakeup_bot_name_enabled: true, group_wakeup_generated_keyword_limit: 24,
  group_wakeup_topic_interest_max_boost: 45, group_wakeup_debounce_pending_penalty: 65,
  group_wakeup_image_enabled: false, group_wakeup_image_weight: 0.2,
  group_wakeup_short_text_wait_ms: 15000, group_wakeup_log_limit: 80,
  group_air_guard_enabled: true, group_high_intensity_enabled: false,
  group_air_guard_window_seconds: 60,
  recall_silence_enabled: true,
  group_air_guard_max_bot_replies: 3, group_air_guard_cooldown_seconds: 150,
  group_air_guard_polite_loop_limit: 2,
  private_closing_guard_enabled: true, private_closing_guard_window_seconds: 1800,
  private_closing_guard_prompt_enabled: true,
  private_debounce_enabled: true, private_debounce_min_ms: 1500,
  private_debounce_max_ms: 4000, private_debounce_total_ms: 8000,
  group_conversation_followup_enabled: true, group_conversation_followup_seconds: 120,
  group_conversation_followup_max_turns: 3,
  enable_group_injection_guard: true, enable_group_privacy_guard: true,
  enable_group_reality_promise_guard: true, enable_context_image_captioning: false,
  context_image_caption_max_items: 2, enable_private_image_self_recognition: false,
  image_understanding_private_enabled: true, image_understanding_group_enabled: true,
  enable_private_image_vision_cache: true, private_image_vision_cache_max_items: 300,
  private_image_vision_custom_prompt: '', private_image_vision_max_chars: 1200,
  private_image_vision_wait_seconds: 8, private_image_provider_failure_cooldown_seconds: 60,
  video_understanding_enabled: true, video_understanding_max_chars: 2400,
  video_understanding_private_enabled: true, video_understanding_group_enabled: true,
  video_understanding_wait_seconds: 20, video_understanding_prompt: '',
  file_understanding_enabled: false, file_understanding_formats: 'text_and_documents',
  file_understanding_format_groups: '["文本","PDF","Word（DOCX）","Excel（XLSX）","PowerPoint（PPTX）"]',
  file_understanding_private_enabled: true, file_understanding_group_enabled: true,
  file_understanding_max_size_mb: 8, file_understanding_max_chars: 24000,
  file_understanding_timeout_seconds: 15, file_understanding_prompt: '',
  style_prompt: '', prompt_style_mode: 'free', content_cleanup_enabled: true, content_cleanup_mode: 'words',
  content_cleanup_scope: 'trailing', content_cleanup_rule: '[\\n]', content_cleanup_words: '["\\n"]',
  content_replacement_enabled: false, content_replacements: '[]', framework_error_guard_enabled: true,
  enable_forbidden_word_recall: false, recall_forbidden_words: '[]', recall_forbidden_scope: 'bot_and_group',
  recall_forbidden_word_case_sensitive: false, forward_message_adaptation_enabled: true,
  component_order: '[]', reply_quote_enabled: false,
  reply_quote_group_enabled: true, reply_quote_first_part_only: true, reply_quote_skip_chars: 0,
  reply_mention_speaker_enabled: false, reply_diagnostic_limit: 80,
  voice_trigger_mode: 'always', voice_generation_mode: 'plain', voice_segment_mode: 'each',
  voice_conversion_scope: 'full',
  voice_delivery_mode: 'voice_and_text', voice_position: 'after_media', voice_language: 'zh',
  voice_foreign_text_mode: 'original', voice_probability: 1, voice_cooldown_seconds: 0,
  voice_max_speech_chars: 1800, voice_timeout_seconds: 30, voice_trigger_keywords: '',
  voice_primary_mention_keywords: '', voice_primary_mention_probability: 0.8,
  voice_primary_mention_prompt: '',
  voice_provider_id: '', voice_zh_provider_id: '', voice_ja_provider_id: '',
  voice_en_provider_id: '', voice_extra_prompt: '', style_prompt: '',
  voice_private_zh_provider_id: '', voice_private_ja_provider_id: '',
  voice_private_en_provider_id: '', voice_group_zh_provider_id: '',
  voice_group_ja_provider_id: '', voice_group_en_provider_id: '',
  enable_humanized_expression: false, humanized_expression_prompt: '',
};
const LIMITS = {
  threshold: [1, 65536], min_segment_chars: [1, 65536], max_segments: [1, 64],
  part_gap_ms: [0, 60000], part_gap_min_ms: [100, 60000], part_gap_max_ms: [100, 60000],
  part_gap_log_base: [1.1, 5], voice_cooldown_seconds: [0, 86400], voice_max_speech_chars: [1, 10000],
  voice_timeout_seconds: [1, 120], group_wakeup_question_threshold: [0, 100],
  group_wakeup_cold_group_threshold: [0, 100], group_wakeup_cold_group_idle_minutes: [3, 720],
  group_wakeup_cooldown_seconds: [0, 3600], group_wakeup_fatigue_limit: [1, 20],
  group_wakeup_fatigue_decay_minutes: [5, 720], group_wakeup_generated_keyword_limit: [4, 80],
  group_wakeup_topic_interest_max_boost: [0, 150], group_wakeup_debounce_pending_penalty: [0, 100],
  group_wakeup_short_text_wait_ms: [0, 30000], group_wakeup_log_limit: [10, 300],
  group_air_guard_window_seconds: [30, 1800], group_air_guard_max_bot_replies: [1, 20],
  group_air_guard_cooldown_seconds: [0, 1800],
  group_conversation_followup_seconds: [15, 1800], group_conversation_followup_max_turns: [1, 12],
  group_air_guard_polite_loop_limit: [1, 10], context_image_caption_max_items: [0, 8],
  private_closing_guard_window_seconds: [60, 7200],
  private_debounce_min_ms: [10, 120000], private_debounce_max_ms: [10, 120000],
  private_debounce_total_ms: [10, 120000],
  private_image_vision_cache_max_items: [0, 2000], private_image_vision_max_chars: [120, 4000],
  private_image_vision_wait_seconds: [1, 30], private_image_provider_failure_cooldown_seconds: [0, 3600],
  video_understanding_max_chars: [200, 6000], video_understanding_wait_seconds: [1, 60],
  file_understanding_max_size_mb: [1, 16], file_understanding_max_chars: [1000, 48000],
  file_understanding_timeout_seconds: [1, 30],
  reply_diagnostic_limit: [10, 300],
};
const GROUP_SCOPE_FIELDS = new Set(['group_wakeup_primary_member_ids', 'group_wakeup_owner_direct_words']);
const FIELDS = Object.fromEntries(Object.entries(DEFAULTS).map(([name, value]) => {
  const kind = name === 'part_gap_log_base' ? 'weight'
    : ['voice_probability', 'voice_primary_mention_probability',
       'group_wakeup_interest_probability', 'group_wakeup_image_weight'].includes(name)
    ? 'probability'
    : typeof value === 'boolean' ? 'boolean' : typeof value === 'string' ? 'text' : 'integer';
  const roleShared = new Set([
    'image_understanding_private_enabled', 'image_understanding_group_enabled',
    'video_understanding_private_enabled', 'video_understanding_group_enabled',
    'file_understanding_private_enabled', 'file_understanding_group_enabled',
    'prompt_style_mode',
    'private_closing_guard_enabled', 'private_closing_guard_window_seconds',
    'private_closing_guard_prompt_enabled', 'recall_silence_enabled',
    'private_debounce_enabled', 'private_debounce_min_ms', 'private_debounce_max_ms',
    'private_debounce_total_ms',
    ...Object.keys(DEFAULTS).filter(name => name.startsWith('group_wakeup_') && !GROUP_SCOPE_FIELDS.has(name)),
  ]);
  const entry = {kind, default: value, apply_mode: 'immediate',
    editable_layers: GROUP_SCOPE_FIELDS.has(name) ? ['window']
      : roleShared.has(name) ? ['role', 'window'] : ['window']};
  entry.write_layer = 'window';
  if (LIMITS[name]) [entry.minimum, entry.maximum] = LIMITS[name];
  if (kind === 'probability') Object.assign(entry, {minimum: 0, maximum: 1, decimal_places: 2});
  if (kind === 'weight') entry.decimal_places = 1;
  return [name, entry];
}));

/* This section answers flat (no `sections` wrapper) because it lives on the
   reply route family rather than the config route. */
function status(changes = {}, token = TOKEN) {
  const settings = {...DEFAULTS, ...changes};
  return {
    settings, edit_token: token, revision: 1, configuration_token: 'configuration',
    apply_mode: 'next_sealed_reply', group_id: 'conversation.presentation', scope: 'window',
    layers: ['installation', 'role', 'window'],
    provenance: Object.fromEntries(Object.keys(settings).map(name =>
      [name, {layer: 'window', explicit: true, revision: 1}])),
    fields: FIELDS, recent: [], participation_recent: [], participation_limit: 80,
    tts: {state: 'ready', providers: [{id: 'tts-a', model: 's2.1-pro-free', voice: '高木さん'}, {id: 'tts-b'}]},
  };
}

const PREVIEW = {mode: 'offline_preview', parts: ['example'], count: 1, characters: 7,
  minimum_spacing_ms: 0, planned_spacing_ms: 0, part_spacing_ms: [], image_position: 'after_text', instruction: '',
  components: [{speech: '', success_order: ['text'], fallback_order: ['text']}],
  voice: {enabled: false, instruction: ''}};

function harness() {
  const requests = [], store = new Map(), listeners = {}, inner = {}, slots = {};
  const previewInputs = {
    example: {value: '', dataset: {replyPartInput: 'example'}},
    'image-count': {value: '0', valueAsNumber: 0, dataset: {replyPartInput: 'image-count'}},
  };
  const exampleButtons = ['simple', 'complex'].map(replyExample => {
    const classes = new Set(), attributes = {};
    return {dataset: {replyExample}, classes, attributes,
      classList: {toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); }},
      setAttribute(name, value) { attributes[name] = value; }};
  });
  const sectionIds = ['segment', 'style', 'cleanup', 'wakeup_direct', 'wakeup_interest', 'wakeup_members',
    'wakeup_cold_group', 'wakeup_fatigue', 'group_continuation', 'private_debounce',
    'silence_policy', 'group_air_guard',
    'private_closing', 'group_content_safety',
    'context_image', 'image_narration', 'video_narration', 'file_understanding', 'forwards', 'voice',
    'voice_channels', 'components', 'checks'];
  const sectionTabs = sectionIds.map(id => {
    const tab = {dataset: {wbSection: id}, focus() {}, click() {
      listeners['settings:click']?.({target: {closest: selector => selector === '[data-wb-section]' ? tab : null}});
    }};
    return tab;
  });
  const make = name => {
    const node = {
      dataset: {}, isConnected: true, textContent: '',
      classList: {add() {}, remove() {}, toggle() {}, contains: () => false},
      querySelectorAll: selector => selector === '[data-wb-section]' ? sectionTabs : [], querySelector: () => null,
      scrollIntoView() {},
      addEventListener: (type, handler) => {listeners[name + ':' + type] = handler;},
      removeEventListener: (type, handler) => {
        if (listeners[name + ':' + type] === handler) delete listeners[name + ':' + type];
      },
    };
    Object.defineProperty(node, 'innerHTML', {
      get: () => inner[name] || '',
      set: value => { inner[name] = value; syncSlots(value); },
    });
    return node;
  };
/* Holders are created by the adapter's innerHTML, and the preview section
   resolves through root.querySelector('[data-reply-part="..."]'). */
  const syncSlots = () => {
    for (const part of ['scope', 'settings', 'preview', 'diagnostics']) {
      if (!slots[part]) slots[part] = make(part);
    }
    slots.preview.querySelector = selector => {
      if (selector === '#reply-preview-result') return slots['preview-result'] || (slots['preview-result'] = make('preview-result'));
      const inputMatch = /^\[data-reply-part-input="(.+)"\]$/.exec(selector);
      if (inputMatch) return previewInputs[inputMatch[1]] || null;
      return null;
    };
    slots.preview.querySelectorAll = selector => selector === '[data-reply-example]' ? exampleButtons : [];
  };
  const root = make('root');
  root.querySelectorAll = selector => selector === '[data-wb-section]' ? sectionTabs
    : selector === '[data-reply-example]' ? exampleButtons : [];
  root.querySelector = selector => {
    const match = /^\[data-reply-part="(.+)"\]$/.exec(selector);
    if (match) return slots[match[1]] || null;
    const inputMatch = /^\[data-reply-part-input="(.+)"\]$/.exec(selector);
    if (inputMatch) return previewInputs[inputMatch[1]] || null;
    if (selector === '#reply-preview-result') return slots['preview-result'] || (slots['preview-result'] = make('preview-result'));
    if (selector === '#reply-preview-status') return slots['preview-status'] || (slots['preview-status'] = make('preview-status'));
    return null;
  };
  const sessionStorage = {getItem: key => store.get(key) || null,
    setItem: (key, value) => store.set(key, value), removeItem: key => store.delete(key)};
  const window = {
    addEventListener() {}, removeEventListener() {},
    CompanionProduct: {
      hosted: () => true, role: () => ({reply_api: PREFIX + 'group/reply'}),
      identity: () => ({installation_id: 'inst', operator_id: 'admin'}),
      /* 页面取桥先问 bridgeNow()，没有再 await bridgeFor()。 */
      bridgeNow: () => window.AstrBotPluginPage,
      bridgeFor: () => ({then: resolve => resolve(window.AstrBotPluginPage)}),
    },
    AstrBotPluginPage: {
      apiGet: (route, body) => new Promise((resolve, reject) =>
        requests.push({method: 'GET', route, body, resolve, reject})),
      apiPost: (route, body) => new Promise((resolve, reject) =>
        requests.push({method: 'POST', route, body, resolve, reject})),
    },
  };
  const sandbox = {window, sessionStorage, crypto: {randomUUID: () => 'save-key'},
    setTimeout, clearTimeout, URLSearchParams, Math, Number, Object, JSON, Array, String};
  vm.runInNewContext(workbench, sandbox);
  vm.runInNewContext(adapter, sandbox);
  return {page: window.CompanionReplyPage, workbench: window.CompanionWorkbench,
    root, slots, requests, store, listeners, previewInputs, exampleButtons,
    get previewHtml() { return inner.preview || ''; }};
}

async function loaded(harnessed, view = 'expression') {
  harnessed.page.mount(harnessed.root, {view});
  await drains();
  assert.equal(harnessed.requests.length, 1, 'mounting reads the settings exactly once');
  harnessed.requests[0].resolve(status());
  await drains();
  return harnessed;
}

const live = harnessed => harnessed.page.state.instance;

test('the settings section reads and saves through the reply route family', async () => {
  const harnessed = harness();
  await loaded(harnessed);
  assert.equal(harnessed.requests[0].route, 'group/reply');
  assert.match(harnessed.root.innerHTML, /日常聊天的表达设置/);
  assert.match(harnessed.root.innerHTML, /语音朗读与消息组件/);
  assert.doesNotMatch(harnessed.root.innerHTML, /普通聊天已使用 AstrBot 核心回复/);
  const instance = live(harnessed);
  instance.state.draft.max_segments = 6;
  const saving = instance.save(false);
  await drains();
  assert.equal(harnessed.requests[1].route, 'group/reply/configure');
  assert.deepEqual(plain(harnessed.requests[1].body), {
    operation_key: 'save-key', edit_token: TOKEN, changes: {max_segments: 6}, reset_to_inherit: [],
  });
  harnessed.requests[1].resolve({result: {operation_id: 'op', replayed: false},
    status: status({max_segments: 6}, 'b'.repeat(64))});
  await saving;
  await drains();
  assert.equal(instance.state.base.max_segments, 6);
  assert.equal(harnessed.store.size, 0);
});

test('legacy all cleanup scope migrates to all three positions before validation', async () => {
  const harnessed = harness();
  harnessed.page.mount(harnessed.root, {view: 'expression'});
  await drains();
  harnessed.requests[0].resolve(status({content_cleanup_scope: 'all'}));
  await drains();
  const instance = live(harnessed);
  const field = instance.state.container.fields.content_cleanup_scope;
  assert.equal(field.input, 'chips');
  assert.equal(field.chipOptions.mode, 'multi');
  assert.deepEqual(plain(field.chipOptions.options), ['leading', 'middle', 'trailing']);
  assert.deepEqual(plain(field.chipOptions.labels), {leading: '句首', middle: '句中', trailing: '句尾'});
  assert.equal(instance.state.base.content_cleanup_scope, 'all');
  assert.equal(instance.state.draft.content_cleanup_scope, 'leading、middle、trailing');
  assert.equal(harnessed.workbench.valid(field, instance.state.draft.content_cleanup_scope), true);
  assert.equal(instance.dirty(), true, 'the legacy value remains as a pending migration');

  const saving = instance.save(false);
  await drains();
  assert.deepEqual(plain(harnessed.requests[1].body.changes), {content_cleanup_scope: 'leading、middle、trailing'});
  harnessed.requests[1].resolve({result: {operation_id: 'cleanup-scope', replayed: false},
    status: status({content_cleanup_scope: 'leading、middle、trailing'}, 'b'.repeat(64))});
  await saving;
  await drains();
  assert.equal(instance.state.base.content_cleanup_scope, 'leading、middle、trailing');
  assert.equal(instance.state.draft.content_cleanup_scope, 'leading、middle、trailing');
  harnessed.page.unmount();
});

test('prompt style offers free, guided, and constraint modes and saves the role preference', async () => {
  const harnessed = await loaded(harness());
  const instance = live(harnessed);
  const field = instance.state.container.fields.prompt_style_mode;
  assert.equal(instance.state.draft.prompt_style_mode, 'free');
  assert.deepEqual(plain(field.choices), {constraint: '约束', free: '自由', guided: '引导'});
  assert.deepEqual(plain(field.editable), ['role', 'window']);

  instance.state.draft.prompt_style_mode = 'guided';
  const saving = instance.save(false);
  await drains();
  assert.deepEqual(plain(harnessed.requests[1].body.changes), {prompt_style_mode: 'guided'});
  harnessed.requests[1].resolve({result: {operation_id: 'style-save', replayed: false},
    status: status({prompt_style_mode: 'guided'}, 'c'.repeat(64))});
  await saving;
  await drains();
  assert.equal(instance.state.base.prompt_style_mode, 'guided');
  harnessed.page.unmount();
});

test('group wakeup controls keep the selected group override', async () => {
  const harnessed = await loaded(harness(), 'decision');
  assert.match(harnessed.root.innerHTML, /群唤醒设置跟随角色/);
  const instance = live(harnessed);
  assert.deepEqual(instance.state.container.fields.group_wakeup_enabled.editable, ['role', 'window']);
  assert.equal(instance.state.container.fields.group_wakeup_enabled.writeLayer, 'window');
  assert.match(harnessed.slots.settings.innerHTML,
    /data-rail="group_wakeup_enabled"[\s\S]*data-layer="window"/);
});

test('a window override can be cleared through the role inheritance control', async () => {
  const inherited = status({style_prompt: '角色默认表达'});
  inherited.provenance.style_prompt = {layer: 'role', explicit: false, revision: 1};
  const harnessed = harness();
  harnessed.page.mount(harnessed.root, {view: 'expression'});
  await drains();
  harnessed.requests[0].resolve(inherited);
  await drains();
  assert.match(harnessed.slots.settings.innerHTML, /data-wb-reset="style_prompt"/);
  const reset = {dataset: {wbReset: 'style_prompt'}};
  harnessed.listeners['settings:click']({target: {closest: selector =>
    selector === '[data-wb-reset]' ? reset : null}});
  const saving = live(harnessed).save(false);
  await drains();
  assert.equal(harnessed.requests[1].route, 'group/reply/configure');
  assert.deepEqual(plain(harnessed.requests[1].body), {
    operation_key: 'save-key', edit_token: TOKEN, changes: {}, reset_to_inherit: ['style_prompt'],
  });
  harnessed.requests[1].resolve({result: {operation_id: 'reset-op', replayed: false},
    status: status({style_prompt: '角色默认表达'}, 'b'.repeat(64))});
  await saving;
});

test('the card is split into labelled groups but keeps one save', async () => {
  const harnessed = harness();
  await loaded(harnessed);
  const instance = live(harnessed);
  /* vm 上下文里的数组与宿主 realm 不同，比较前先过 plain()。 */
  const ids = plain(instance.state.container.groups.map(group => group.id));
  assert.deepEqual(ids, ['segment', 'style', 'cleanup', 'voice', 'voice_channels', 'components', 'checks']);
  assert.ok(plain(instance.state.container.groups.find(group => group.id === 'voice').fields)
    .includes('voice_provider_id'));
  assert.ok(!ids.some(id => ['wakeup_direct', 'wakeup_interest', 'wakeup_members', 'wakeup_cold_group',
    'wakeup_fatigue', 'silence_policy', 'group_air_guard', 'group_content_safety'].includes(id)));
  assert.deepEqual(Object.keys(plain(instance.state.container.fields.rule_mode.choices)),
    ['words', 'regex', 'sentences', 'paragraphs']);
  assert.equal(instance.state.draft.rule_mode, 'words');
  for (const name of ['style_prompt', 'content_cleanup_enabled', 'content_replacements',
    'component_order', 'reply_quote_enabled', 'reply_mention_speaker_enabled',
    'framework_error_guard_enabled', 'enable_forbidden_word_recall', 'part_gap_ms']) {
    assert.ok(instance.state.container.fields[name], `${name} is available in the editor`);
  }
  assert.equal(instance.state.container.fields.forward_message_adaptation_enabled, undefined);
  /* 段落只是阅读顺序：仍然只有一个 edit_token、一次保存。 */
  assert.equal(instance.state.container.fields.enabled.editable.includes('window'), true);
  assert.equal(instance.state.container.fields.group_tool_profile, undefined);
  assert.ok(plain(instance.state.container.groups.find(group => group.id === 'segment').fields)
    .includes('part_gap_mode'), 'message spacing is part of the segmentation workspace');
  assert.equal(harnessed.page.selectSection('timing'), true, 'the old timing jump resolves to segmentation');
  assert.equal(instance.state.activeGroupId, 'segment');
});

test('segmentation keeps rule choices visible while the legacy rules switch is absent', async () => {
  const harnessed = await loaded(harness());
  const instance = live(harnessed);
  assert.equal(instance.state.draft.rule_mode, 'words');
  assert.equal(instance.state.draft.forward_message_adaptation_enabled, true);
  assert.equal(instance.state.draft.llm_controlled, false);
  assert.equal(instance.state.draft.rules_enabled, true);
  let html = harnessed.slots.settings.innerHTML;
  assert.doesNotMatch(html, /data-wb-row="rules_enabled"|启用规则补充分段/);
  assert.match(html, /value="words" checked><span>词列表<\/span>/);
  assert.match(html, /data-wb-word-list="split_words"/);
  assert.doesNotMatch(html, /data-wb-line-list="split_words"/);
  assert.doesNotMatch(html, /data-wb-row="split_words" hidden/);
  assert.doesNotMatch(html, /data-wb-row="match_width_variants" hidden/);
  assert.match(html, /data-wb-row="rule_regex" hidden/);
  assert.doesNotMatch(html, /data-wb-row="threshold" hidden/);
  assert.doesNotMatch(html, /data-wb-row="min_segment_chars" hidden/);

  const regex = {dataset: {wbField: 'rule_mode'}, tagName: 'INPUT', type: 'radio', value: 'regex',
    closest: () => null};
  harnessed.listeners['settings:input']({type: 'input', target: regex});
  assert.equal(instance.state.draft.rule_mode, 'words', 'radio input does not repaint before change');
  harnessed.listeners['settings:change']({type: 'change', target: regex});
  html = harnessed.slots.settings.innerHTML;
  assert.match(html, /data-wb-row="split_words" hidden/);
  assert.match(html, /data-wb-row="match_width_variants" hidden/);
  assert.doesNotMatch(html, /data-wb-row="rule_regex" hidden/);
});

test('stream delivery mode is editable when model streaming is enabled', async () => {
  const harnessed = await loaded(harness());
  const instance = live(harnessed);
  assert.deepEqual(plain(instance.state.container.fields.streaming_mode.choices),
    {buffered: '收齐后分段发送', incremental: '边生成边分段发送'});
  assert.match(harnessed.slots.settings.innerHTML, /data-wb-row="streaming_mode" hidden/);
  harnessed.listeners['settings:change']({type: 'change', target: {
    dataset: {wbField: 'streaming'}, type: 'checkbox', checked: true, closest: () => null}});
  assert.doesNotMatch(harnessed.slots.settings.innerHTML, /data-wb-row="streaming_mode" hidden/);
  harnessed.listeners['settings:change']({type: 'change', target: {
    dataset: {wbField: 'streaming_mode'}, tagName: 'INPUT', type: 'radio', value: 'incremental', closest: () => null}});
  assert.equal(instance.state.draft.streaming_mode, 'incremental');
});

test('cleanup mode only shows the control used by the selected mode', async () => {
  const harnessed = await loaded(harness());
  const instance = live(harnessed);
  let html = harnessed.slots.settings.innerHTML;
  assert.equal(instance.state.draft.content_cleanup_mode, 'words');
  assert.match(html, /句首/);
  assert.match(html, /句中/);
  assert.match(html, /句尾/);
  assert.doesNotMatch(html, /整段回复/);
  assert.match(html, /data-wb-row="content_cleanup_rule" hidden/);
  assert.doesNotMatch(html, /data-wb-row="content_cleanup_words" hidden/);

  const mode = {dataset: {wbField: 'content_cleanup_mode'}, tagName: 'SELECT', value: 'regex',
    closest: () => null};
  harnessed.listeners['settings:change']({type: 'change', target: mode});
  html = harnessed.slots.settings.innerHTML;
  assert.match(html, /data-wb-row="content_cleanup_rule"/);
  assert.doesNotMatch(html, /data-wb-row="content_cleanup_rule" hidden/);
  assert.match(html, /data-wb-row="content_cleanup_words" hidden/);
});

test('regex and word-list modes both expose editable controls and save their own values', async () => {
  const h = await loaded(harness());
  const instance = live(h);
  const choose = value => h.listeners['settings:change']({type: 'change', target: {
    dataset: {wbField: 'rule_mode'}, tagName: 'INPUT', type: 'radio', value, closest: () => null,
  }});
  choose('regex');
  assert.match(h.slots.settings.innerHTML, /data-wb-row="rule_regex"/);
  assert.doesNotMatch(h.slots.settings.innerHTML, /data-wb-row="rule_regex" hidden/);
  assert.match(h.slots.settings.innerHTML, /data-wb-row="split_words" hidden/);
  assert.match(h.slots.settings.innerHTML, /data-wb-row="match_width_variants" hidden/);
  const regex = {dataset: {wbField: 'rule_regex'}, tagName: 'TEXTAREA', value: '。|！',
    closest: () => null, setAttribute() {}};
  h.listeners['settings:input']({target: regex});
  assert.equal(instance.state.draft.rule_regex, '。|！');

  const regexSave = instance.save(false);
  await drains();
  assert.equal(h.requests[1].body.changes.rule_mode, 'regex');
  assert.equal(h.requests[1].body.changes.rule_regex, '。|！');
  h.requests[1].resolve({result: {operation_id: 'regex-op', replayed: false},
    status: status({rule_mode: 'regex', rule_regex: '。|！'}, 'e'.repeat(64))});
  await regexSave;
  await drains();

  choose('words');
  assert.match(h.slots.settings.innerHTML, /data-wb-word-list="split_words"/);
  assert.doesNotMatch(h.slots.settings.innerHTML, /data-wb-row="split_words" hidden/);
  assert.doesNotMatch(h.slots.settings.innerHTML, /data-wb-row="match_width_variants" hidden/);
  assert.match(h.slots.settings.innerHTML, /data-wb-row="rule_regex" hidden/);
  assert.match(h.slots.settings.innerHTML, /data-wb-word-value="。"/);
  for (const value of ['sentences', 'paragraphs']) {
    choose(value);
    assert.match(h.slots.settings.innerHTML, /data-wb-row="split_words" hidden/);
    assert.match(h.slots.settings.innerHTML, /data-wb-row="match_width_variants" hidden/);
    assert.match(h.slots.settings.innerHTML, /data-wb-row="rule_regex" hidden/);
  }
  h.page.unmount();
});

test('segmentation words use editable chips and save the existing JSON list', async () => {
  const harnessed = await loaded(harness());
  const instance = live(harnessed);
  assert.equal(instance.state.container.fields.split_words.input, 'word-list');
  assert.match(instance.state.container.fields.split_words.help, /回车添加.*换行和空格/);
  assert.match(harnessed.slots.settings.innerHTML, /data-wb-word-list="split_words"/);
  const initialHtml = harnessed.slots.settings.innerHTML;
  const quickLabelIndex = initialHtml.indexOf('class="wb-word-tools-label">常用分段词');
  const quickNewlineIndex = initialHtml.indexOf('data-wb-word-special="newline"');
  assert.ok(quickLabelIndex >= 0 && quickNewlineIndex > quickLabelIndex,
    'newline is grouped under common split words');
  assert.ok(initialHtml.indexOf('data-wb-word-special="space"') > quickLabelIndex,
    'space is grouped under common split words');
  assert.match(harnessed.slots.settings.innerHTML, /data-wb-word-value="。"/);
  const removeNewline = {dataset: {wbWordRemove: 'split_words', wbWordIndex: '6'},
    closest: selector => selector === '[data-wb-word-remove]' ? removeNewline : null};
  harnessed.listeners['settings:click']({target: removeNewline});
  const clickSpecial = special => {
    const button = {dataset: {wbWordSpecial: special, wbWordField: 'split_words'}};
    const target = {closest: selector => selector === '[data-wb-word-special]' ? button : null};
    harnessed.listeners['settings:click']({target});
  };
  clickSpecial('newline');
  clickSpecial('space');
  const input = {value: '，', dataset: {wbWordInput: 'split_words'}, focus() {}};
  const addButton = {dataset: {wbWordAdd: 'split_words'},
    closest: selector => selector === '.wb-field' ? {querySelector: () => input} : null};
  const addTarget = {closest: selector => selector === '[data-wb-word-add]' ? addButton : null};
  harnessed.listeners['settings:click']({target: addTarget});
  assert.deepEqual(plain(JSON.parse(instance.state.draft.split_words)), ['。', '？', '！', '~', '…', '“', '\n', ' ', '，']);

  const saving = instance.save(false);
  await drains();
  assert.equal(harnessed.requests[1].body.changes.split_words, JSON.stringify(['。', '？', '！', '~', '…', '“', '\n', ' ', '，']));
  harnessed.requests[1].resolve({result: {operation_id: 'op', replayed: false},
    status: status({split_words: JSON.stringify(['。', '？', '！', '~', '…', '“', '\n', ' ', '，'])}, 'd'.repeat(64))});
  await saving;
  await drains();
  assert.match(harnessed.slots.settings.innerHTML, /<span>换行<\/span>/);
  assert.match(harnessed.slots.settings.innerHTML, /<span>空格<\/span>/);
});

test('expression modules render only their own navigation and panels after every switch', async () => {
  const h = await loaded(harness());
  const instance = live(h);
  h.page.setExpressionModule('expression-text');
  assert.match(h.slots.scope.innerHTML, /文字表达设置/);
  let html = h.slots.settings.innerHTML;
  assert.match(html, /data-wb-section="segment"/);
  assert.match(html, /data-wb-section="style"/);
  assert.match(html, /data-wb-section="cleanup"/);
  assert.doesNotMatch(html, /data-wb-section="voice"|data-wb-section="components"|data-wb-section="checks"/);
  assert.doesNotMatch(html, /data-wb-section-panel="voice"|data-wb-section-panel="components"|data-wb-section-panel="checks"/);
  assert.equal(h.page.selectSection('voice'), false, 'hidden modules cannot be selected by a stale jump');

  h.page.setExpressionModule('expression-platform');
  assert.match(h.slots.scope.innerHTML, /消息组件与发送检查/);
  html = h.slots.settings.innerHTML;
  assert.match(html, /data-wb-section="components"/);
  assert.match(html, /data-wb-section="checks"/);
  assert.doesNotMatch(html, /data-wb-section="segment"|data-wb-section="voice"/);
  assert.equal(instance.state.activeGroupId, 'components');
  assert.equal(h.page.selectSection('checks'), true);
  assert.equal(instance.state.activeGroupId, 'checks');

  h.page.setExpressionModule('expression-voice');
  assert.match(h.slots.scope.innerHTML, /语音朗读设置/);
  assert.match(h.slots.scope.innerHTML, /音色与 TTS 合成后端在「模型与连接」中配置/);
  assert.doesNotMatch(h.slots.scope.innerHTML, /核心回复|历史兼容|群唤醒/);
  html = h.slots.settings.innerHTML;
  assert.match(html, /data-wb-section="voice"/);
  assert.match(html, /data-wb-section="voice_channels"/);
  assert.match(html, /data-wb-section-panel="voice_channels"/);
  assert.equal(instance.state.activeGroupId, 'voice');
  assert.match(html, /data-wb-row="voice_enabled"/);
  assert.doesNotMatch(html, /data-wb-section="segment"|data-wb-section="components"|data-wb-section="checks"/);
  assert.doesNotMatch(html, /data-wb-row="reply_quote_enabled"|data-wb-row="enabled"/);
  h.page.unmount();
});

test('voice channel overrides are reachable and save from the voice module', async () => {
  const h = await loaded(harness());
  h.page.setExpressionModule('expression-voice');
  assert.equal(h.page.selectSection('voice_channels'), true);
  const instance = live(h);
  assert.equal(instance.state.activeGroupId, 'voice_channels');
  const overrides = ['voice_private_zh_provider_id', 'voice_private_ja_provider_id',
    'voice_private_en_provider_id', 'voice_group_zh_provider_id',
    'voice_group_ja_provider_id', 'voice_group_en_provider_id'];
  for (const name of overrides) {
    assert.ok(instance.state.container.fields[name], `${name} is editable`);
    assert.match(h.slots.settings.innerHTML, new RegExp(`data-wb-field="${name}"`));
    instance.state.draft[name] = 'tts-a';
  }
  const saving = instance.save(false);
  await drains();
  assert.deepEqual(plain(h.requests[1].body.changes),
    Object.fromEntries(overrides.map(name => [name, 'tts-a'])));
  h.requests[1].resolve({result: {operation_id: 'voice-channels', replayed: false},
    status: status(Object.fromEntries(overrides.map(name => [name, 'tts-a'])), 'f'.repeat(64))});
  await saving;
  await drains();
  assert.equal(instance.state.base.voice_private_ja_provider_id, 'tts-a');
  h.page.unmount();
});

test('expression voice hides segmentation preview and cancels its pending refresh', async () => {
  const h = await loaded(harness());
  h.page.setExpressionModule('expression-voice');
  assert.equal(h.slots.preview.hidden, true);
  await wait(300);
  assert.equal(h.requests.length, 1, 'the voice tab does not request a segmentation preview');

  h.page.setExpressionModule('expression-text');
  assert.equal(h.slots.preview.hidden, false);
  assert.match(h.slots.preview.innerHTML, /分段效果预览/);
  h.page.unmount();
});

test('word-list chips add and remove entries while saving the existing wire format', async () => {
  const harnessed = await loaded(harness());
  const instance = live(harnessed);
  for (const name of ['content_cleanup_words', 'recall_forbidden_words']) {
    assert.equal(instance.state.container.fields[name].input, 'word-list', name + ' uses the list editor');
    assert.doesNotMatch(instance.state.container.fields[name].help, /JSON/i);
  }
  const decision = await loaded(harness(), 'decision');
  for (const name of ['group_wakeup_direct_words', 'group_wakeup_context_words',
    'group_wakeup_interest_keywords', 'group_wakeup_primary_member_ids', 'group_wakeup_owner_direct_words']) {
    assert.equal(live(decision).state.container.fields[name].input, 'word-list', name + ' uses the list editor');
  }
  const original = JSON.parse(instance.state.draft.content_cleanup_words);
  const input = {value: '♪', dataset: {wbWordInput: 'content_cleanup_words'}, focus() {}};
  const addButton = {dataset: {wbWordAdd: 'content_cleanup_words'},
    closest: selector => selector === '.wb-field' ? {querySelector: () => input} : null};
  const addTarget = {closest: selector => selector === '[data-wb-word-add]' ? addButton : null};
  harnessed.listeners['settings:click']({target: addTarget});
  assert.deepEqual(plain(JSON.parse(instance.state.draft.content_cleanup_words)), [...original, '♪']);

  const saving = instance.save(false);
  await drains();
  assert.equal(harnessed.requests[1].body.changes.content_cleanup_words, JSON.stringify([...original, '♪']));
  harnessed.requests[1].resolve({result: {operation_id: 'op', replayed: false},
    status: status({content_cleanup_words: JSON.stringify([...original, '♪'])}, 'd'.repeat(64))});
  await saving;
  await drains();

  const removeButton = {dataset: {wbWordRemove: 'content_cleanup_words', wbWordIndex: String(original.length)}};
  const removeTarget = {closest: selector => selector === '[data-wb-word-remove]' ? removeButton : null};
  harnessed.listeners['settings:click']({target: removeTarget});
  assert.deepEqual(plain(JSON.parse(instance.state.draft.content_cleanup_words)), original);
});

test('replacement pairs and component order are edited without JSON controls', async () => {
  const harnessed = await loaded(harness());
  const instance = live(harnessed);
  const settings = harnessed.slots.settings;
  const from = {value: '旧称', focus() {}};
  const to = {value: '新称', focus() {}};
  const form = {querySelector: selector => selector === '[data-wb-replacement-from]' ? from
    : selector === '[data-wb-replacement-to]' ? to : null};
  settings.querySelector = selector => selector === '[data-wb-replacement-form="content_replacements"]'
    ? form : null;

  assert.equal(instance.state.container.fields.content_replacements.input, 'replacement-list');
  assert.equal(instance.state.container.fields.component_order.input, 'order-list');
  assert.match(settings.innerHTML, /data-wb-replacement-form="content_replacements"/);
  assert.match(settings.innerHTML, /data-wb-order-start="component_order"/);
  assert.doesNotMatch(settings.innerHTML, /<textarea data-wb-field="content_replacements"/);
  assert.match(instance.state.container.fields.content_replacements.help, /逐项填写原词和替换文本/);

  const addPair = {dataset: {wbReplacementAdd: 'content_replacements'}};
  harnessed.listeners['settings:click']({target: {closest: selector =>
    selector === '[data-wb-replacement-add]' ? addPair : null}});
  assert.deepEqual(plain(JSON.parse(instance.state.draft.content_replacements)), [{from: '旧称', to: '新称'}]);
  assert.match(settings.innerHTML, /旧称/);
  assert.match(settings.innerHTML, /新称/);

  const startOrder = {dataset: {wbOrderStart: 'component_order'}};
  harnessed.listeners['settings:click']({target: {closest: selector =>
    selector === '[data-wb-order-start]' ? startOrder : null}});
  const originalOrder = ['voice', 'at', 'text', 'face', 'image'];
  assert.deepEqual(plain(JSON.parse(instance.state.draft.component_order)), originalOrder);
  const moveUp = {dataset: {wbOrderMove: 'up', wbOrderIndex: '2'}};
  harnessed.listeners['settings:click']({target: {closest: selector =>
    selector === '[data-wb-order-move]' ? moveUp : null}});
  assert.deepEqual(plain(JSON.parse(instance.state.draft.component_order)),
    ['voice', 'text', 'at', 'face', 'image']);
  const natural = {dataset: {wbOrderNatural: 'component_order'}};
  harnessed.listeners['settings:click']({target: {closest: selector =>
    selector === '[data-wb-order-natural]' ? natural : null}});
  assert.deepEqual(plain(JSON.parse(instance.state.draft.component_order)), []);
});

test('reply expression, perception, and decision settings partition every field exactly once', async () => {
  const expressionHarness = await loaded(harness());
  const expression = plain(live(expressionHarness).state.container.groups.flatMap(group => group.fields));
  const decisionHarness = await loaded(harness(), 'decision');
  const decisionState = live(decisionHarness).state.container;
  const decision = plain(decisionState.groups.flatMap(group => group.fields));
  assert.ok(decision.includes('group_tool_profile'));
  assert.deepEqual(plain(decisionState.fields.group_tool_profile.choices),
    {full: '完整工具', standard: '常用工具', lean: '精简工具'});
  assert.deepEqual(plain(decisionState.groups.map(group => group.id)),
    ['wakeup_direct', 'wakeup_interest', 'wakeup_members', 'wakeup_cold_group', 'wakeup_fatigue',
      'group_continuation', 'private_debounce', 'silence_policy', 'group_air_guard', 'private_closing', 'group_content_safety']);
  const perceptionHarness = await loaded(harness(), 'perception');
  const perceptionState = live(perceptionHarness).state.container;
  const perception = plain(perceptionState.groups.flatMap(group => group.fields));
  const exposed = [...expression, ...perception, ...decision];
  assert.equal(exposed.length, new Set(exposed).size, 'a setting must not be duplicated across modules');
  assert.deepEqual([...exposed].sort(), Object.keys(DEFAULTS)
    .filter(name => name !== 'rules_enabled' && name !== 'file_understanding_formats').sort());
  assert.deepEqual(plain(perceptionState.groups.map(group => group.id)),
    ['media_permissions', 'context_image', 'image_narration', 'video_narration', 'file_understanding', 'forwards']);
  assert.deepEqual(perception, ['image_understanding_private_enabled', 'image_understanding_group_enabled',
    'video_understanding_private_enabled', 'video_understanding_group_enabled',
    'file_understanding_private_enabled', 'file_understanding_group_enabled',
    'enable_context_image_captioning', 'context_image_caption_max_items', 'enable_private_image_self_recognition',
    'enable_private_image_vision_cache',
    'private_image_vision_cache_max_items', 'private_image_vision_custom_prompt',
    'private_image_vision_max_chars', 'private_image_vision_wait_seconds',
    'private_image_provider_failure_cooldown_seconds',
    'video_understanding_enabled', 'video_understanding_max_chars',
    'video_understanding_wait_seconds', 'video_understanding_prompt',
    'file_understanding_enabled',
    'file_understanding_format_groups', 'file_understanding_max_size_mb',
    'file_understanding_max_chars', 'file_understanding_timeout_seconds', 'file_understanding_prompt',
    'forward_message_adaptation_enabled']);
  assert.equal(perceptionHarness.page.selectSection('segment'), false);
  assert.equal(perceptionHarness.page.selectSection('context_image'), true);
  assert.equal(perceptionHarness.page.selectSection('forwards'), true);
  perceptionHarness.page.unmount();
});

test('decision view edits the same reply route and omits expression-only tools', async () => {
  const harnessed = await loaded(harness(), 'decision');
  assert.equal(harnessed.requests[0].route, 'group/reply');
  assert.match(harnessed.root.innerHTML, /data-reply-part="settings"/);
  assert.doesNotMatch(harnessed.root.innerHTML, /data-reply-part="preview"/);
  assert.match(harnessed.root.innerHTML, /data-reply-part="diagnostics"/);
  assert.equal(harnessed.page.selectSection('voice'), false);
  const instance = live(harnessed);
  instance.state.draft.group_wakeup_enabled = false;
  const saving = instance.save(false);
  await drains();
  assert.equal(harnessed.requests[1].route, 'group/reply/configure');
  assert.deepEqual(plain(harnessed.requests[1].body.changes), {group_wakeup_enabled: false});
  harnessed.requests[1].resolve({result: {operation_id: 'decision-save', replayed: false},
    status: status({group_wakeup_enabled: false}, 'e'.repeat(64))});
  await saving;
  await drains();
  harnessed.page.unmount();
});

test('decision tempo exposes and saves the follow-up fuse settings', async () => {
  const h = await loaded(harness(), 'decision');
  assert.equal(h.page.selectSection('group_continuation'), true);
  assert.match(h.slots.settings.innerHTML, /连续对话与熔断/);
  assert.match(h.slots.settings.innerHTML, /每次唤醒最多自动续接轮数/);
  const instance = live(h);
  instance.state.draft.group_conversation_followup_max_turns = 2;
  const saving = instance.save(false);
  await drains();
  assert.deepEqual(plain(h.requests[1].body.changes), {group_conversation_followup_max_turns: 2});
  h.requests[1].resolve({result: {operation_id: 'followup-save', replayed: false},
    status: status({group_conversation_followup_max_turns: 2}, 'e'.repeat(64))});
  await saving;
  h.page.unmount();
});

test('decision safety exposes silence policy masters and the private closing detail', async () => {
  const h = await loaded(harness(), 'decision');
  assert.equal(h.page.selectSection('silence_policy'), true);
  assert.match(h.slots.settings.innerHTML, /沉默策略/);
  assert.match(h.slots.settings.innerHTML, /启用私聊收尾沉默/);
  assert.match(h.slots.settings.innerHTML, /启用撤回沉默/);
  const instance = live(h);
  for (const name of ['recall_silence_enabled', 'private_closing_guard_enabled',
    'group_air_guard_enabled']) {
    assert.ok(instance.state.container.fields[name], `${name} is available in the decision editor`);
  }
  assert.deepEqual(plain(instance.state.container.fields.private_closing_guard_enabled.editable),
    ['role', 'window']);
  assert.equal(h.page.selectSection('private_closing'), true);
  assert.match(h.slots.settings.innerHTML, /私聊收尾 · 细则/);
  assert.equal(instance.state.container.fields.private_closing_guard_window_seconds.minimum, 60);
  assert.equal(instance.state.container.fields.private_closing_guard_window_seconds.maximum, 7200);
  instance.state.draft.private_closing_guard_window_seconds = 600;
  const saving = instance.save(false);
  await drains();
  assert.deepEqual(plain(h.requests[1].body.changes), {private_closing_guard_window_seconds: 600});
  h.requests[1].resolve({result: {operation_id: 'closing-save', replayed: false},
    status: status({private_closing_guard_window_seconds: 600}, 'f'.repeat(64))});
  await saving;
  h.page.unmount();
});

test('decision tempo exposes private debounce as a role preference', async () => {
  const h = await loaded(harness(), 'decision');
  assert.equal(h.page.selectSection('private_debounce'), true);
  assert.match(h.slots.settings.innerHTML, /私聊节奏 · 连续输入/);
  assert.match(h.slots.settings.innerHTML, /启用私聊连续输入防抖/);
  const instance = live(h);
  for (const name of ['private_debounce_enabled', 'private_debounce_min_ms',
    'private_debounce_max_ms', 'private_debounce_total_ms']) {
    assert.ok(instance.state.container.fields[name], `${name} is available in the decision editor`);
  }
  assert.deepEqual(plain(instance.state.container.fields.private_debounce_enabled.editable),
    ['role', 'window']);
  assert.equal(instance.state.container.fields.private_debounce_min_ms.minimum, 10);
  assert.equal(instance.state.container.fields.private_debounce_max_ms.maximum, 120000);
  assert.equal(instance.state.container.fields.private_debounce_total_ms.maximum, 120000);
  instance.state.draft.private_debounce_min_ms = 2000;
  const saving = instance.save(false);
  await drains();
  assert.deepEqual(plain(h.requests[1].body.changes), {private_debounce_min_ms: 2000});
  h.requests[1].resolve({result: {operation_id: 'debounce-save', replayed: false},
    status: status({private_debounce_min_ms: 2000}, 'e'.repeat(64))});
  await saving;
  h.page.unmount();
});

test('the expression page leaves image and forward understanding to the understanding page', async () => {
  const harnessed = await loaded(harness());
  assert.equal(harnessed.page.selectSection('voice'), true);
  assert.equal(live(harnessed).state.activeGroupId, 'voice');
  assert.equal(harnessed.page.selectSection('context_image'), false);
  assert.equal(harnessed.page.selectSection('image_narration'), false);
  assert.equal(harnessed.page.selectSection('forwards'), false);
  assert.equal(harnessed.page.selectSection('components'), true);
  assert.equal(live(harnessed).state.activeGroupId, 'components');
  assert.equal(harnessed.page.selectSection('unknown'), false);
});

test('a voice tab can select its TTS section before settings finish loading', async () => {
  const h = harness();
  const diversitySwitch = h.page.searchEntries().find(item => item.field === 'enable_humanized_expression');
  const diversityPrompt = h.page.searchEntries().find(item => item.field === 'humanized_expression_prompt');
  assert.equal(diversitySwitch.label, '追加拟人表达提示');
  assert.match(diversitySwitch.help, /与所选风格叠加/);
  assert.match(diversitySwitch.help, /自由模式下关闭即可移除/);
  assert.equal(diversityPrompt.label, '额外表达提示正文');
  assert.match(diversityPrompt.help, /约束模式使用此正文/);
  assert.match(diversityPrompt.help, /自由或引导模式也会额外注入/);
  h.page.mount(h.root, {view: 'expression'});
  await drains();
  assert.equal(h.page.selectSection('voice'), true);
  h.requests[0].resolve(status());
  await drains();
  assert.equal(live(h).state.activeGroupId, 'voice');
  h.page.selectSection('components');
  live(h).attach();
  assert.equal(live(h).state.activeGroupId, 'components', 'a later redraw keeps the user selection');
  h.page.unmount();
});

test('the perception view edits image, native video and file understanding without reply preview or diagnostics', async () => {
  const harnessed = await loaded(harness(), 'perception');
  assert.equal(harnessed.requests[0].route, 'group/reply');
  assert.match(harnessed.root.innerHTML, /data-reply-part="settings"/);
  assert.match(harnessed.root.innerHTML, /普通聊天的媒体理解在 AstrBot 中配置/);
  assert.doesNotMatch(harnessed.root.innerHTML, /data-reply-part="preview"|data-reply-part="diagnostics"/);
  const instance = live(harnessed);
  assert.deepEqual(plain(instance.state.container.order),
    ['image_understanding_private_enabled', 'image_understanding_group_enabled',
      'video_understanding_private_enabled', 'video_understanding_group_enabled',
      'file_understanding_private_enabled', 'file_understanding_group_enabled',
      'enable_context_image_captioning', 'context_image_caption_max_items',
      'enable_private_image_self_recognition', 'enable_private_image_vision_cache',
      'private_image_vision_cache_max_items', 'private_image_vision_custom_prompt',
      'private_image_vision_max_chars', 'private_image_vision_wait_seconds',
      'private_image_provider_failure_cooldown_seconds',
      'video_understanding_enabled', 'video_understanding_max_chars',
      'video_understanding_wait_seconds', 'video_understanding_prompt',
      'file_understanding_enabled',
      'file_understanding_format_groups', 'file_understanding_max_size_mb',
      'file_understanding_max_chars', 'file_understanding_timeout_seconds', 'file_understanding_prompt',
      'forward_message_adaptation_enabled']);
  assert.equal(harnessed.page.selectSection('image_narration'), true);
  assert.equal(harnessed.page.selectSection('video_narration'), true);
  assert.equal(harnessed.page.selectSection('file_understanding'), true);
  assert.equal(live(harnessed).state.container.fields.file_understanding_format_groups.listPolicy.minItems, 1);
  assert.equal(live(harnessed).state.container.fields.file_understanding_format_groups.listPolicy.quickAddLabel, '支持格式');
  assert.match(harnessed.slots.settings.innerHTML, /支持格式/);
  assert.equal(harnessed.page.selectSection('forwards'), true);
  harnessed.page.unmount();
});

test('expression D1-D6 links reach current settings owners', () => {
  for (const id of ['expression-text', 'expression-voice', 'expression-reaction',
    'expression-platform', 'expression-timing', 'expression-group']) assert.ok(appPage.includes(id), `missing ${id}`);
  for (const id of ['voice', 'components', 'checks', 'timing']) {
    assert.ok(appPage.includes(`data-reply-section="${id}"`) || appPage.includes(`'${id}'`), `missing reply route ${id}`);
  }
  assert.ok(appPage.includes('data-expression-life-tab="${tab}"'));
  assert.ok(appPage.includes("navigate('life',el.dataset.expressionLifeTab)"));
  assert.ok(appPage.includes('settingsJump('));
});

test('expression voice tab hosts the shared reply editor with only TTS settings visible', () => {
  const expressionCss = fs.readFileSync(path.resolve(__dirname, PAGE + 'expression-config.css'), 'utf8');
  const narrowExpressionLayouts = expressionCss.match(/@container expression-modules \(max-width:760px\)\s*\{([\s\S]*?)\n\}/)?.[1] || '';
  assert.ok(appPage.includes('id="expressionVoiceReplyHost"'));
  assert.ok(appPage.includes('function syncExpressionReplyWorkspace'));
  assert.ok(appPage.includes('voiceHost.append(reply)'));
  assert.ok(appPage.includes('syncExpressionReplyWorkspace(expressionModule)'));
  assert.ok(appPage.includes("voice:'expression-voice'"));
  assert.ok(appPage.includes("text:'expression-text'"));
  assert.ok(appPage.includes('activateExpressionModule(module)'));
  assert.ok(adapter.includes('setVisibleGroups'));
  assert.ok(expressionCss.includes('expression-platform-focus .wb-section-sidebar{display:block}'));
  assert.ok(expressionCss.includes('#replyWorkspace.expression-voice-focus>[data-reply-part="preview"]{display:none}'));
  assert.match(expressionCss, /container-name:expression-modules;container-type:inline-size/);
  assert.match(narrowExpressionLayouts, /\.expression-module-nav\{display:grid;grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(narrowExpressionLayouts, /\.expression-platform-workspace\{grid-template-columns:minmax\(0,1fr\)\}/);
  assert.match(narrowExpressionLayouts, /\.semantic-providers\{grid-template-columns:minmax\(0,1fr\)\}/);
  for (const field of ['voice_trigger_mode', 'voice_provider_id', 'voice_delivery_mode',
    'voice_private_ja_provider_id', 'voice_group_ja_provider_id']) {
    assert.ok(adapter.includes(field), `missing TTS setting ${field}`);
  }
  assert.match(adapter, /私聊 \/ 群聊语音覆盖/);
});

test('the TTS provider catalog comes from the read result, not a page-local list', async () => {
  const harnessed = harness();
  await loaded(harnessed);
  const item = live(harnessed).state.container.fields.voice_provider_id;
  const entries = item.catalog();
  assert.equal(entries[''], '跟随 AstrBot');
  assert.deepEqual(Object.keys(entries).filter(Boolean).sort(), ['tts-a', 'tts-b']);
  assert.equal(entries['tts-a'], 'tts-a · s2.1-pro-free · 高木さん');
  const zh = live(harnessed).state.container.fields.voice_zh_provider_id.catalog();
  assert.equal(zh[''], '使用通用扩展');
});

test('offline preview submits the current draft and renders the result', async () => {
  const harnessed = harness();
  await loaded(harnessed);
  const instance = live(harnessed);
  instance.state.draft.max_segments = 3;
  harnessed.listeners['root:click']({target: {closest: () => ({dataset: {replyAction: 'preview'}})}});
  await drains();
  const request = harnessed.requests[1];
  /* 预览按 app 相对路径发（桥接也接受），设置卡则用完整地址。 */
  assert.equal(request.route, 'group/reply/preview');
  assert.equal(request.body.settings.max_segments, 3, 'preview must use the draft, not the saved values');
  assert.equal(request.body.image_count, 0);
  request.resolve(PREVIEW);
  await drains();
  assert.match(harnessed.slots['preview-result'].innerHTML, /预览结果/);
  assert.match(harnessed.slots['preview-status'].textContent, /使用当前窗口的设置草稿/);
});

test('preview identifies components that this private session will not send', async () => {
  const harnessed = await loaded(harness());
  harnessed.listeners['root:click']({target: {closest: () => ({dataset: {replyAction: 'preview'}})}});
  await drains();
  harnessed.requests[1].resolve({...PREVIEW,
    quote: {enabled: true, group_enabled: true, first_part_only: true, skip_chars: 0,
      mention_speaker: true},
    voice: {enabled: false, provider_id: '', instruction: ''}});
  await drains();
  const html = harnessed.slots['preview-result'].innerHTML;
  assert.match(html, /预览会话：私聊/);
  assert.match(html, /引用：私聊不发送/);
  assert.match(html, /@：私聊不发送/);
  assert.match(html, /语音位置：未启用 · 按请求插入/);
  const sequence = html.match(/aria-label="发送顺序">([\s\S]*?)<\/div>/)[1];
  assert.match(sequence, /引用 · 私聊不发送/);
  assert.match(sequence, /@ · 私聊不发送/);
  assert.match(sequence, /语音 · 未启用 · 按请求插入/);
  assert.doesNotMatch(sequence, /引用 · 固定第一段/);
  harnessed.page.unmount();
});

test('offline preview shows each segment component plan and its attachment rules', async () => {
  const harnessed = await loaded(harness());
  harnessed.listeners['root:click']({target: {closest: selector =>
    selector === '[data-reply-preview-session]' ? {dataset: {replyPreviewSession: 'group'}} : null}});
  harnessed.listeners['root:click']({target: {closest: () => ({dataset: {replyAction: 'preview'}})}});
  await drains();
  harnessed.requests[1].resolve({...PREVIEW, parts: ['看这条 https://example.com/notes', '带图的末段'], count: 2,
    image_count: 2, quote: {enabled: true, group_enabled: true, first_part_only: true,
      skip_chars: 8, mention_speaker: true}, component_order: ['face'], image_position: 'after_text',
    components: [
      {speech: '第一段朗读', success_order: ['voice'], fallback_order: ['text'], text_hidden: true},
      {speech: '末段朗读', success_order: ['voice', 'image'], fallback_order: ['text', 'image'], text_hidden: true},
    ], voice: {enabled: true, provider_id: 'tts-a', instruction: ''}});
  await drains();
  const html = harnessed.slots['preview-result'].innerHTML;
  assert.match(html, /朗读成功/);
  assert.match(html, /未触发或合成失败/);
  assert.match(html, /文字由语音替代/);
  assert.match(html, /图片/);
  assert.match(html, /引用 · 固定第一段/);
  assert.match(html, /@ · 跟随第一段/);
  assert.match(html, /平台表情 · 跟随第一段/);
  assert.match(html, /2 张图片 · 文字后/);
  assert.match(html, /语音 · 按请求插入/);
  assert.match(html, /网址保护 · 链接整段保留/);
  assert.match(html, /组件挂载 · 预览模拟/);
  assert.match(html, /预览会话：群聊/);
  assert.match(html, /reply-preview-position-arrow/);
  const sequences = [...html.matchAll(/aria-label="发送顺序">([\s\S]*?)<\/div>/g)]
    .map(match => match[1]);
  assert.equal(sequences.length, 2);
  const firstSequence = sequences[0];
  for (const [before, after] of [['引用 · 固定第一段', '平台表情 · 跟随第一段'],
    ['平台表情 · 跟随第一段', '语音 · 按请求插入'],
    ['语音 · 按请求插入', '@ · 跟随第一段']]) {
    assert.ok(firstSequence.indexOf(before) < firstSequence.indexOf(after),
      `${before} should precede ${after}: ${firstSequence}`);
  }
  assert.ok(sequences[1].indexOf('语音 · 按请求插入') < sequences[1].indexOf('2 张图片 · 文字后'),
    'the successful voice part should place its image after the voice');

  const positionInput = (key, value) => harnessed.listeners['root:input']({target: {
    dataset: {replyPreviewOption: key}, value,
  }});
  positionInput('image', 'first');
  positionInput('mention', 'each');
  positionInput('reaction', 'last');
  positionInput('voice', 'before');
  positionInput('media', 'last');
  const moved = harnessed.previewHtml;
  assert.match(moved, /2 张图片 · 文字后/);
  assert.equal((moved.match(/@ · 每一段/g) || []).length, 2);
  assert.match(moved, /平台表情 · 跟随末段/);
  assert.match(moved, /语音 · 文字前/);
  assert.match(moved, /表情包\/附件：跟随末段/);
  assert.match(moved, /表情包\/附件 · 跟随末段/);
  assert.doesNotMatch(moved, /分段回复后单独发送/);
  assert.equal(harnessed.requests.length, 2, 'preview placement controls only re-render local results');
  positionInput('media', 'single');
  const independent = harnessed.previewHtml;
  assert.match(independent, /表情包\/附件/);
  assert.match(independent, /分段回复后单独发送 · 预览模拟/);
  harnessed.page.unmount();
});

test('the first preview runs automatically with the simple example', async () => {
  const harnessed = await loaded(harness());
  assert.match(harnessed.previewHtml, /data-reply-example="simple" aria-pressed="true"/);
  assert.match(harnessed.previewHtml, /data-reply-example="complex" aria-pressed="false"/);
  assert.match(harnessed.previewHtml, /data-reply-example="marker" aria-pressed="false"/);
  await wait(300);
  assert.equal(harnessed.requests.length, 2);
  assert.equal(harnessed.requests[1].route, 'group/reply/preview');
  assert.match(harnessed.requests[1].body.text, /玻璃上都是细细的水线/);
  assert.equal(harnessed.requests[1].body.settings.enabled, false);
  harnessed.requests[1].resolve(PREVIEW);
  await drains();
  assert.match(harnessed.slots['preview-result'].innerHTML, /预览结果/);
  harnessed.page.unmount();
});

test('editing a draft field schedules an offline preview with the updated value', async () => {
  const harnessed = await loaded(harness());
  const control = {dataset: {wbField: 'max_segments'}, tagName: 'INPUT', valueAsNumber: 3,
    closest: () => null, setAttribute() {}};
  harnessed.listeners['settings:input']({target: control});
  harnessed.listeners['root:input']({target: control});
  await wait(300);
  assert.equal(harnessed.requests.length, 2);
  assert.equal(harnessed.requests[1].body.settings.max_segments, 3);
  harnessed.requests[1].resolve(PREVIEW);
  await drains();
  harnessed.page.unmount();
});

test('the complex example button previews through the V2 offline endpoint', async () => {
  const harnessed = await loaded(harness());
  const button = harnessed.exampleButtons[1];
  harnessed.listeners['root:click']({target: {closest: selector =>
    selector === '[data-reply-example]' ? button : null}});
  assert.match(harnessed.previewInputs.example.value, /https:\/\/example\.com\/notes/);
  assert.equal(button.attributes['aria-pressed'], 'true');
  assert.ok(button.classes.has('is-selected'));
  assert.equal(harnessed.exampleButtons[0].attributes['aria-pressed'], 'false');
  assert.equal(harnessed.exampleButtons[0].classes.has('is-selected'), false);
  harnessed.listeners['root:click']({target: {closest: selector => selector === '[data-reply-action]'
    ? {dataset: {replyAction: 'preview'}} : null}});
  await drains();
  assert.equal(harnessed.requests.length, 2);
  assert.match(harnessed.requests[1].body.text, /https:\/\/example\.com\/notes/);
  harnessed.requests[1].resolve(PREVIEW);
  await drains();
  harnessed.page.unmount();
});

test('typing in the example clears the selected preset state', async () => {
  const harnessed = await loaded(harness());
  const input = harnessed.previewInputs.example;
  input.value = '手动编辑的正文';
  harnessed.listeners['root:input']({target: input});
  assert.equal(harnessed.exampleButtons[0].attributes['aria-pressed'], 'false');
  assert.equal(harnessed.exampleButtons[1].attributes['aria-pressed'], 'false');
  assert.equal(harnessed.exampleButtons[0].classes.has('is-selected'), false);
  assert.equal(harnessed.exampleButtons[1].classes.has('is-selected'), false);
  harnessed.page.unmount();
});

/* 页面桥只转发信封的 message，机器码到前端时没有 code 字段；这类只带码的
   失败必须翻成中文，不能把 staging_not_initialized 这种裸码摆到界面上。 */
test('a machine code delivered only in message is translated on load', async () => {
  const harnessed = harness();
  harnessed.page.mount(harnessed.root);
  await drains();
  harnessed.requests[0].reject(new Error('staging_not_initialized'));
  await drains();
  const html = harnessed.slots.settings.innerHTML;
  assert.match(html, /角色数据尚未加载/);
  assert.doesNotMatch(html, /staging_not_initialized/);
});

test('a machine code delivered only in message is translated on preview failure', async () => {
  const harnessed = harness();
  await loaded(harnessed);
  harnessed.listeners['root:click']({target: {closest: () => ({dataset: {replyAction: 'preview'}})}});
  await drains();
  harnessed.requests[1].reject(new Error('reply_preview_invalid'));
  await drains();
  const message = harnessed.slots['preview-status'].textContent;
  assert.equal(message, '请填写 1–65536 字的有效正文。');
  assert.doesNotMatch(message, /reply_preview_invalid/);
  assert.match(harnessed.slots['preview-result'].innerHTML, /预览失败/);
});

test('an unconfirmed save stays pending and the retry reuses the operation', async () => {
  const harnessed = harness();
  await loaded(harnessed);
  const instance = live(harnessed);
  instance.state.draft.max_segments = 6;
  const saving = instance.save(false);
  await drains();
  const original = plain(harnessed.requests[1].body);
  harnessed.requests[1].reject(Object.assign(new Error('down'), {code: 'bridge_timeout'}));
  await saving;
  await drains();
  assert.equal(harnessed.store.size, 1);
  const retrying = instance.save(true);
  await drains();
  assert.deepEqual(plain(harnessed.requests[2].body), original);
  harnessed.requests[2].resolve({result: {operation_id: 'op', replayed: true},
    status: status({max_segments: 6}, 'c'.repeat(64))});
  await retrying;
  await drains();
  assert.equal(harnessed.store.size, 0);
  assert.match(instance.state.savedState, /is-replayed/);
});

test('an over-long prompt and an out-of-range value never dispatch', async () => {
  const harnessed = harness();
  await loaded(harnessed);
  const instance = live(harnessed);
  /* 提示词长度不在注册表范围里，由后端 validate 判；这里只验单字段越界。 */
  instance.state.draft.max_segments = 65;
  await instance.save(false);
  assert.equal(harnessed.requests.length, 1, 'the invalid draft must not reach the network');
  assert.equal(instance.state.bad, true);
  /* 概率字段在界面按百分比编辑，101% 要拦下。 */
  instance.state.draft.max_segments = 4;
  instance.state.draft.voice_probability = 1.5;
  await instance.save(false);
  assert.equal(harnessed.requests.length, 1);
  assert.equal(instance.state.bad, true);
  instance.state.draft.voice_probability = 1;
  instance.state.draft.voice_primary_mention_probability = 1.5;
  await instance.save(false);
  assert.equal(harnessed.requests.length, 1);
  assert.equal(instance.state.bad, true);
});

test('reply diagnostics show bounded metadata without batch IDs or prompt text', async () => {
  const harnessed = harness();
  harnessed.page.mount(harnessed.root);
  await drains();
  harnessed.requests[0].resolve({...status(),
    recent: [{batch_id: 'batch-internal', created_at: '2026-09-27 10:00', state: 'complete', reason: 'confirmed_delivery',
      captured: {settings: {custom_prompt: 'NEVER-RENDER-PRIVATE-PROMPT'}},
      parts: [{sequence: 1, chars: 8, state: 'unmapped_part_state'}],
      response_type: 'text_or_mixed', group_history_items: 3,
      injected_materials: [{owner: 'conversation.group_context', purpose: 'group_conversation_history', state: 'ready'}],
      offered_tools: ['slang.search'], rounds: [{round_no: 0, state: 'tools', tool_actions: ['slang.search']}],
      tool_calls: [{action: 'slang.search', state: 'succeeded', effect: 'none'}],
      turn_type: 'conversation.reply', current_inputs: 1,
      calls: [{position: 0, state: 'generated', failure_code: 'native_response_parse_failed', actual_tokens: null,
        latency_ms: null, response_diagnostics: {completion_present: true, parse_state: 'native_rejected', finish_reason: 'stop',
          raw: {chars: 2, shape: 'json_array', sha256: 'a'.repeat(64), has_internal_markers: true}, parsed: null, visible: null}}]}]});
  await drains();
  const rendered = harnessed.slots.diagnostics.innerHTML;
  assert.match(rendered, /回复诊断/);
  assert.match(rendered, /group_conversation_history/);
  assert.match(rendered, /slang\.search/);
  assert.match(rendered, /native_response_parse_failed/);
  assert.match(rendered, /conversation\.reply/);
  assert.match(rendered, /有效输入 1 条/);
  assert.match(rendered, /宿主解析失败/);
  assert.match(rendered, /原始 2 字 · JSON 数组/);
  assert.match(rendered, /含标签样式标记/);
  assert.match(rendered, /a{64}/);
  assert.doesNotMatch(rendered, /batch-internal|confirmed_delivery|NEVER-RENDER-PRIVATE-PROMPT/);
  assert.match(rendered, /不含提示词正文与消息原文/);
});

test('diagnostics put the failing model line, fallback and wait time in the summary', async () => {
  const harnessed = harness();
  harnessed.page.mount(harnessed.root);
  await drains();
  const failed = {batch_id: 'turn-failed', created_at: '2026-10-24T05:40:36.918433+00:00',
    execution_path: 'astrbot_core', state: 'completed', response_type: 'no_reply', send_returns: 0,
    delivery_state: 'not_started', turn_type: 'astrbot.core.reply', current_inputs: 1, rounds: [],
    injected_materials: [], offered_tools: [], filtered_tools: [], tool_calls: [],
    calls: [{position: 1, state: 'unknown', provider: 'deepseek/deepseek-v4.1-flash-expires-on-0910',
      failure_code: 'UnprocessableEntityError', response_diagnostics: null}]};
  const recovered = {...failed, batch_id: 'turn-recovered', state: 'complete', response_type: 'native_reply',
    send_returns: 2, delivery_state: 'returned', created_at: '2026-10-24T12:13:32.489373+00:00',
    calls: [{...failed.calls[0], state: 'returned'}, {position: 2, state: 'returned', provider: 'deepseek/deepseek-v4-flash',
      failure_code: null, response_diagnostics: null}]};
  const waiting = {...failed, batch_id: 'turn-waiting', state: 'generating', response_type: 'no_reply',
    created_at: new Date(Date.now() - 3 * 3600 * 1000).toISOString(), calls: []};
  const silent = {...failed, batch_id: 'turn-silent', state: 'completed', send_returns: 0, calls: []};
  harnessed.requests[0].resolve({...status(), recent: [failed, recovered, waiting, silent]});
  await drains();
  const rendered = harnessed.slots.diagnostics.innerHTML;
  /* 摘要行必须自己说明原因，而不是把失败码留在展开后的细节里。 */
  assert.match(rendered, /生成完成<\/span><span>未发送<\/span><span class="reply-record-note is-warn">模型线路拒绝请求 422/);
  assert.match(rendered, /首次请求失败 422 · 已回退/);
  assert.match(rendered, /本轮未发言/);
  assert.match(rendered, /已等待 3 小时/);
  /* 时间按本地时区渲染，不再把 UTC 原样当成当地时间。 */
  assert.match(rendered, /<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}<\/time>/);
  assert.doesNotMatch(rendered, /\+\d{2}:\d{2}<\/time>|T\d{2}:\d{2}<\/time>/);
  /* 失败详情保留具体线路，便于对照配置。 */
  assert.match(rendered, /deepseek\/deepseek-v4\.1-flash-expires-on-0910/);
  assert.match(rendered, /发送已返回/);
});

test('a route outside the plugin prefix is refused instead of half-mounting', async () => {
  const requests = [], listeners = {};
  const node = {dataset: {}, isConnected: true, textContent: '', innerHTML: '',
    classList: {add() {}, remove() {}, contains: () => false},
    querySelectorAll: () => [], querySelector: () => null,
    addEventListener: (type, handler) => {listeners[type] = handler;}, removeEventListener() {}};
  const window = {
    CompanionProduct: {hosted: () => true, role: () => ({reply_api: '/elsewhere/reply'}),
      identity: () => ({installation_id: 'inst', operator_id: 'admin'})},
    AstrBotPluginPage: {
      apiGet: route => new Promise(resolve => requests.push({method: 'GET', route, resolve})),
      apiPost: route => new Promise(resolve => requests.push({method: 'POST', route, resolve})),
    },
  };
  const sandbox = {window, sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    crypto: {randomUUID: () => 'k'}, setTimeout, clearTimeout, URLSearchParams,
    Math, Number, Object, JSON, Array, String};
  vm.runInNewContext(workbench, sandbox);
  vm.runInNewContext(adapter, sandbox);
  window.CompanionReplyPage.mount(node);
  await drains();
  assert.equal(requests.length, 0);
  assert.match(node.innerHTML, /没有可用的回复设置工作区/);
});
