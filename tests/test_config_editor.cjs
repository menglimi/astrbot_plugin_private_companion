const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PAGE = '../pages/陪伴小屋/';
const workbench = fs.readFileSync(path.resolve(__dirname, PAGE + 'workbench-page.js'), 'utf8');
const adapter = fs.readFileSync(path.resolve(__dirname, PAGE + 'config-editor.js'), 'utf8');
const selfStyle = fs.readFileSync(path.resolve(__dirname, PAGE + 'self-page.css'), 'utf8');
const app = fs.readFileSync(path.resolve(__dirname, PAGE + 'app.js'), 'utf8');
const birthdayHelpers = (() => {
  const start = workbench.indexOf('  const CHINESE_DIGITS');
  const end = workbench.indexOf('  const fireInput', start);
  return vm.runInNewContext(`() => { ${workbench.slice(start, end)}
    return {parseBirthday, birthdayText, birthdayPartsError, lunarDateIndex};
  }` )();
})();
const flush = () => new Promise(resolve => setImmediate(resolve));
/* 每次 await 只推进一层微任务；refresh 里有多层 await，用 drains 让它走完。 */
const drains = async (times = 6) => { for (let index = 0; index < times; index += 1) await flush(); };
const TOKEN = a => a.repeat(64);
const plain = value => JSON.parse(JSON.stringify(value));
const PREFIX = '/astrbot_plugin_private_companion/';

const quota = {
  private_owner_max_daily: -1, private_friend_max_daily: -1, group_max_daily: -1, proactive_max_daily: -1,
};
const review = {
  enable_group_nsfw_private_fallback: false, group_nsfw_image_review_mode: 'single',
  group_nsfw_image_review_sensitivity: 'balanced', group_nsfw_image_review_min_confidence: 0.7,
  group_nsfw_image_review_timeout_seconds: 8, group_nsfw_image_review_max_dimension: 1280,
  group_nsfw_image_review_max_images: 1, group_nsfw_image_review_failure_action: 'private',
  group_nsfw_image_review_custom_prompt: '', review_provider: '', review_dual_provider: '',
};

/* The backend also publishes the inheritance chain, per-field provenance and the
   registered field contract, so the workbench renders controls from the server's
   bounds instead of a duplicated copy that can drift from the validator. */
const QUOTA_FIELDS = Object.fromEntries(Object.keys(quota).map(name =>
  [name, {kind: 'integer', default: -1, minimum: -1, maximum: 100,
    apply_mode: 'immediate', editable_layers: ['installation', 'role']}]));
/* Enum-valued fields are TEXT in the registry, so the fixture's contract says
   `text` and the options come from the page; the two tests below check that the
   adapter actually hands those options to the kernel.
   Kinds must mirror the registry rather than the fixture value's JS type: a
   confidence is registered as PROBABILITY (a 0–1 float), not as an integer. */
const REVIEW_KINDS = {
  enable_group_nsfw_private_fallback: 'boolean', group_nsfw_image_review_mode: 'text',
  group_nsfw_image_review_sensitivity: 'text', group_nsfw_image_review_min_confidence: 'probability',
  group_nsfw_image_review_timeout_seconds: 'integer', group_nsfw_image_review_max_dimension: 'integer',
  group_nsfw_image_review_max_images: 'integer', group_nsfw_image_review_failure_action: 'text',
  group_nsfw_image_review_custom_prompt: 'text', review_provider: 'text', review_dual_provider: 'text',
};
const REVIEW_FIELDS = Object.fromEntries(Object.keys(review).map(name => {
  const kind = REVIEW_KINDS[name];
  const entry = {kind, default: review[name], apply_mode: 'immediate',
    editable_layers: ['installation', 'role', 'window']};
  if (kind === 'probability') Object.assign(entry, {minimum: 0, maximum: 1, decimal_places: 2});
  return [name, entry];
}));

function section(kind, changes = {}, token = TOKEN('a')) {
  const settings = kind === 'quota' ? {...quota, ...changes} : {...review, ...changes};
  return {
    settings, edit_token: token, revision: 1, configuration_token: 'configuration',
    group_id: kind === 'quota' ? 'conversation.media_quota' : 'conversation.image_review',
    scope: kind === 'quota' ? 'role' : 'window',
    apply_mode: kind === 'quota' ? 'next_task' : 'immediate',
    layers: kind === 'quota' ? ['installation', 'role'] : ['installation', 'role', 'window'],
    provenance: Object.fromEntries(Object.keys(settings).map(name =>
      [name, {layer: kind === 'quota' ? 'role' : 'window', explicit: true, revision: 1}])),
    fields: kind === 'quota' ? QUOTA_FIELDS : REVIEW_FIELDS,
    section: kind,
  };
}
/* 2026-09-28 新接入的四个分区。默认值、类型与边界都照真实注册表抄，
   否则夹具给出与后端不一致的合同，就测不出「页面按后端下发的范围校验」这件事。 */
const NEW_KINDS = {
  'expression.learning': {
    group: 'expression.learning', scope: 'role', layers: ['installation', 'role'],
    editableLayers: ['installation', 'role', 'window'],
    settings: {scan_interval_minutes: 1440, group_scan_interval_minutes: 180, group_daily_batch_limit: 6},
    fields: {scan_interval_minutes: ['integer', 5, 10080],
      group_scan_interval_minutes: ['integer', 5, 10080], group_daily_batch_limit: ['integer', 1, 50]},
  },
  reaction: {
    group: 'expression.reaction', scope: 'installation', layers: ['installation'],
    settings: {low_latency_enabled: true, candidate_limit: 6, embedding_weight: 1.0,
      embedding_score_threshold: 0.42, embedding_candidate_limit: 1200,
      embedding_backfill_enabled: true, embedding_backfill_batch_size: 24,
      embedding_backfill_interval_seconds: 300, vision_verify_mode: 'embedding'},
    fields: {low_latency_enabled: ['boolean'], candidate_limit: ['integer', 1, 24],
      embedding_weight: ['weight', 0.05, 3], embedding_score_threshold: ['probability', 0, 1],
      embedding_candidate_limit: ['integer', 20, 5000], embedding_backfill_enabled: ['boolean'],
      embedding_backfill_batch_size: ['integer', 1, 100],
      embedding_backfill_interval_seconds: ['integer', 0, 86400], vision_verify_mode: ['text']},
  },
  recall: {
    group: 'conversation.recall',
    settings: {transcribe_enabled: false, transcribe_max_items: 5, transcribe_text_chars: 500,
      transcribe_window_minutes: 10},
    fields: {transcribe_enabled: ['boolean'], transcribe_max_items: ['integer', 1, 20],
      transcribe_text_chars: ['integer', 80, 2000], transcribe_window_minutes: ['integer', 1, 1440]},
  },
  screen: {
    group: 'screen',
    settings: {interpretation_enabled: false, journal_material_enabled: false,
      journal_material_max_chars: 700},
    fields: {interpretation_enabled: ['boolean'], journal_material_enabled: ['boolean'],
      journal_material_max_chars: ['integer', 200, 1600]},
  },
  person: {
    group: 'person',
    settings: {acquaintance_enabled: true, acquaintance_card_chars: 1024},
    fields: {acquaintance_enabled: ['boolean'], acquaintance_card_chars: ['integer', 256, 4096]},
  },
  'role.growth.skills': {
    group: 'role.growth.skills',
    scope: 'role',
    layers: ['installation', 'role'],
    editableLayers: ['role'],
    settings: {enabled: true, custom_skills: '[]', schedule_influence_strength: 0},
    fields: {enabled: ['boolean'], custom_skills: ['text'], schedule_influence_strength: ['integer', 0, 3]},
  },
  /* 自我页的子集夹具：只放吃到专用控件的字段，其余字段按 order 过滤后不渲染。
     生日/性别等在注册表里都是 TEXT——专用控件不改变值形状。 */
  role_profile: {
    group: 'role_profile',
    scope: 'role', layers: ['installation', 'role'],
    settings: {name: '澄汐', birthday: '4月17日', gender: '女', species: '人类',
      first_person: '我', voice_examples: '今天的风有点像海边的。\n你先说，我听着。',
      signature_phrases: '慢慢来、我在呢', hobbies: '植物随笔、听雨',
      avatar_data_url: '',
      main_companion_subject: '',
      expression_private_source_mode: 'owner', expression_private_source_subject_ids: '[]',
      expression_private_application_mode: 'all', expression_private_application_subject_ids: '[]'},
    fields: {name: ['text'], birthday: ['text'], gender: ['text'], species: ['text'],
      first_person: ['text'], voice_examples: ['text'], signature_phrases: ['text'],
      avatar_data_url: ['text'],
      hobbies: ['text'], main_companion_subject: ['text'],
      expression_private_source_mode: ['text'], expression_private_source_subject_ids: ['text'],
      expression_private_application_mode: ['text'], expression_private_application_subject_ids: ['text']},
  },
  knowledge: {
    group: 'knowledge',
    settings: {import_enabled: false, import_paths: '', source_ids: '', entry_limit: 500,
      entry_char_limit: 1200, source_limit: 16, candidate_char_budget: 60000, inject_enabled: true,
      inject_max_entries: 8, inject_max_chars: 6000, member_recognition_enabled: false,
      member_match_aliases: true, member_inject_limit: 6, member_clue_chars: 1200},
    fields: {import_enabled: ['boolean'], import_paths: ['text'], source_ids: ['text'],
      entry_limit: ['integer', 1, 5000], entry_char_limit: ['integer', 64, 4000],
      source_limit: ['integer', 1, 64], candidate_char_budget: ['integer', 2000, 200000],
      inject_enabled: ['boolean'], inject_max_entries: ['integer', 1, 24],
      inject_max_chars: ['integer', 500, 24000], member_recognition_enabled: ['boolean'],
      member_match_aliases: ['boolean'], member_inject_limit: ['integer', 1, 12],
      member_clue_chars: ['integer', 200, 4000]},
  },
  life: {
    group: 'role.life',
    scope: 'role',
    layers: ['installation', 'role'],
    editable: Object.fromEntries(['enabled', 'diary_enabled', 'diary_time', 'timezone',
      'sleep_enabled', 'sleep_time', 'sleep_timezone', 'sleep_variation_minutes', 'sleep_window_minutes', 'sleep_minutes',
      'reading_enabled', 'derive_enabled', 'diary_style_prompt', 'dream_theme_candidates',
      'dream_mixed_themes_enabled', 'dream_intimate_theme_enabled', 'dream_afterglow_mode',
      'enable_rest_reply_simulation', 'rest_reply_mode', 'rest_reply_probability',
      'rest_reply_llm_threshold', 'rest_reply_active_windows', 'rest_reply_awake_grace_minutes',
      'enable_rest_backlog_reply', 'rest_backlog_max_messages'].map(name => [name,
      ['sleep_enabled', 'sleep_time', 'sleep_timezone', 'sleep_variation_minutes', 'sleep_window_minutes', 'sleep_minutes'].includes(name)
        ? ['role', 'window'] : ['window']])),
    settings: {enabled: false, diary_enabled: true, diary_time: '23:50', timezone: 'Asia/Shanghai',
      sleep_enabled: false, sleep_time: '00:30', sleep_timezone: 'Asia/Shanghai', sleep_variation_minutes: 20, sleep_window_minutes: 120, sleep_minutes: 480,
      reading_enabled: false, derive_enabled: true,
      diary_style_prompt: '', dream_theme_candidates: '', dream_mixed_themes_enabled: false,
      dream_intimate_theme_enabled: false, dream_afterglow_mode: 'auto',
      enable_rest_reply_simulation: false, rest_reply_mode: 'probability', rest_reply_probability: 18,
      rest_reply_llm_threshold: 65, rest_reply_active_windows: '23:00-08:30,12:20-13:40',
      rest_reply_awake_grace_minutes: 30, enable_rest_backlog_reply: true,
      rest_backlog_max_messages: 4},
    fields: {enabled: ['boolean'], diary_enabled: ['boolean'], diary_time: ['text'],
      timezone: ['text'], sleep_enabled: ['boolean'], sleep_time: ['text'],
      sleep_timezone: ['text'], sleep_variation_minutes: ['integer', 0, 120], sleep_window_minutes: ['integer', 0, 360], sleep_minutes: ['integer', 1, 1440], reading_enabled: ['boolean'],
      derive_enabled: ['boolean'],
      diary_style_prompt: ['text'], dream_theme_candidates: ['text'],
      dream_mixed_themes_enabled: ['boolean'], dream_intimate_theme_enabled: ['boolean'],
      dream_afterglow_mode: ['text'], enable_rest_reply_simulation: ['boolean'],
      rest_reply_mode: ['text'], rest_reply_probability: ['integer', 0, 100],
      rest_reply_llm_threshold: ['integer', 0, 100], rest_reply_active_windows: ['text'],
      rest_reply_awake_grace_minutes: ['integer', 0, 240],
      enable_rest_backlog_reply: ['boolean'], rest_backlog_max_messages: ['integer', 1, 32]},
  },
  library: {
    group: 'library',
    scope: 'role',
    layers: ['installation', 'role'],
    settings: {enabled: false, reviews_enabled: true, reading_excerpt_chars: 1200,
      manual_entries_enabled: true, max_entries_per_page: 64, rating_enabled: false,
      preference_influence_enabled: false, preference_min_ratings: 5, preference_max_terms: 8,
      min_interval_hours: 18, share_probability: 0.18, ask_probability: 0.16},
    fields: {enabled: ['boolean'], reviews_enabled: ['boolean'],
      reading_excerpt_chars: ['integer', 200, 8000], manual_entries_enabled: ['boolean'],
      max_entries_per_page: ['integer', 1, 200], rating_enabled: ['boolean'],
      preference_influence_enabled: ['boolean'], preference_min_ratings: ['integer', 1, 64],
      preference_max_terms: ['integer', 1, 32], min_interval_hours: ['integer', 1, 720],
      share_probability: ['fraction'], ask_probability: ['fraction']},
  },
  wardrobe: {
    group: 'wardrobe',
    scope: 'role',
    layers: ['installation', 'role'],
    settings: {enabled: false, tendency: '', prompt_enabled: false, prompt_max_items: 12,
      image_max_count: 3, image_prompt: ''},
    fields: {enabled: ['boolean'], tendency: ['text'], prompt_enabled: ['boolean'],
      prompt_max_items: ['integer', 1, 24], image_max_count: ['integer', 1, 8],
      image_prompt: ['text']},
  },
  'photo.reference': {
    group: 'photo.reference',
    scope: 'role',
    layers: ['installation', 'role'],
    settings: {enabled: false, group_enabled: false, daily_outfit_enabled: false, daily_outfit_rotation_days: 14},
    fields: {enabled: ['boolean'], group_enabled: ['boolean'], daily_outfit_enabled: ['boolean'],
      daily_outfit_rotation_days: ['integer', 1, 365]},
  },
  group_participation: {
    group: 'conversation.group_participation',
    settings: {enable_group_social_context: false, enable_group_topic_threads: true, group_topic_thread_max: 12,
      enable_group_mood_detection: false,
      enable_group_roleplay_strength: false, enable_group_moments: false,
      enable_group_moment_portrait: false, enable_group_joke_guard: false,
      enable_group_persona_denoise: true, enable_group_interjection: false,
      group_interject_min_interval_minutes: 180, group_interject_max_daily: 2,
      enable_group_interjection_feedback: true, group_interject_feedback_window_seconds: 600,
      enable_group_repeat_follow: true, group_repeat_count_distinct_users_only: false,
      group_repeat_trigger_threshold: 4, group_repeat_follow_probability: 0.18,
      group_repeat_interrupt_probability: 0.1, group_repeat_interrupt_probability_step: 0.12,
      group_repeat_interrupt_text: '禁止复读', group_repeat_interrupt_image_path: '', group_repeat_window_seconds: 120,
      group_context_max_age_hours: 12,
      enable_group_behavior_learning:true, enable_group_behavior_imitation:true,
      enable_group_behavior_feedback:true, group_behavior_guidance:''},
    /* 概率字段注册成 probability（0–1 的小数），与 role.growth 的整数档位不同。 */
    fields: {enable_group_social_context: ['boolean'], enable_group_topic_threads: ['boolean'],
      group_topic_thread_max: ['integer', 3, 40], enable_group_mood_detection: ['boolean'],
      enable_group_roleplay_strength: ['boolean'], enable_group_moments: ['boolean'],
      enable_group_moment_portrait: ['boolean'], enable_group_joke_guard: ['boolean'],
      enable_group_persona_denoise: ['boolean'], enable_group_interjection: ['boolean'],
      group_interject_min_interval_minutes: ['integer', 0, 1440],
      group_interject_max_daily: ['integer', 0, 20],
      enable_group_interjection_feedback: ['boolean'],
      group_interject_feedback_window_seconds: ['integer', 60, 86400],
      enable_group_repeat_follow: ['boolean'], group_repeat_count_distinct_users_only: ['boolean'],
      group_repeat_trigger_threshold: ['integer', 3, 20],
      group_repeat_follow_probability: ['probability', 0, 1],
      group_repeat_interrupt_probability: ['probability', 0, 1],
      group_repeat_interrupt_probability_step: ['probability', 0, 1],
      group_repeat_interrupt_text: ['text'], group_repeat_interrupt_image_path: ['text'],
      group_repeat_window_seconds: ['integer', 10, 600], group_context_max_age_hours: ['integer', 1, 168],
      enable_group_behavior_learning:['boolean'],
      enable_group_behavior_imitation:['boolean'], enable_group_behavior_feedback:['boolean'],group_behavior_guidance:['text']},
  },
};

function newSection(kind, changes = {}, token = TOKEN('a')) {
  const spec = NEW_KINDS[kind];
  const settings = {...spec.settings, ...changes};
  /* Groups with an explicit role scope must not inherit the window fallback. */
  const layers = spec.layers || ['installation', 'role', 'window'];
  const editableLayers = spec.editableLayers || layers;
  const scope = spec.scope || 'window';
  const fields = Object.fromEntries(Object.entries(spec.fields).map(([name, [type, low, high]]) => {
    const editable = (spec.editable || {})[name] || editableLayers;
    const entry = {kind: type, default: spec.settings[name], apply_mode: 'immediate',
      editable_layers: editable};
    if (low !== undefined) Object.assign(entry, {minimum: low, maximum: high});
    return [name, entry];
  }));
  return {
    settings, edit_token: token, revision: 1, configuration_token: 'configuration',
    group_id: spec.group, scope, apply_mode: 'immediate',
    layers,
    provenance: Object.fromEntries(Object.keys(settings).map(name =>
      [name, {layer: layers[0], explicit: true, revision: 1}])),
    fields, section: kind,
  };
}

function groupInterjectionStatus(changes = {}, token = TOKEN('a')) {
  const value = newSection('group_participation', changes, token);
  const names = ['enable_group_interjection', 'group_interject_min_interval_minutes',
    'group_interject_max_daily', 'enable_group_interjection_feedback',
    'group_interject_feedback_window_seconds'];
  return {...value,
    settings: Object.fromEntries(names.map(name => [name, value.settings[name]])),
    fields: Object.fromEntries(names.map(name => [name, value.fields[name]])),
    provenance: Object.fromEntries(names.map(name => [name, value.provenance[name]])),
  };
}

/* The route answers with every requested section wrapped in `sections`. */
const payload = (kind, changes, token) => {
  const value = Object.hasOwn(NEW_KINDS, kind)
    ? newSection(kind, changes, token) : section(kind, changes, token);
  if (kind === 'knowledge') {
    value.knowledge_sources_available = true;
    value.knowledge_sources = [{id: 'kb:kb-1', type: 'knowledge_base', kb_id: 'kb-1',
      name: '星河设定', emoji: '📚', doc_count: 1, chunk_count: 2,
      documents: [{id: 'doc:kb-1:doc-1', doc_id: 'doc-1', name: '角色资料.md',
        file_type: 'md', chunk_count: 2}]}];
  }
  return {sections: {[kind]: value}};
};

function harness(kinds = ['quota', 'image_review'], views = {}, endpointOverride = '', options = {}) {
  const requests = [], store = options.store || new Map(), holders = {}, painted = {}, windowListeners = new Map();
  const root = {
    isConnected: true, dataset: {}, textContent: '', classList: {add() {}},
    appendChild(node) {
      holders[node.dataset.configHost] = node;
      Object.defineProperty(node, 'innerHTML', {
        get: () => node._html || '',
        set: value => { node._html = value; painted[node.dataset.configHost] = value; },
      });
    },
    querySelector: selector => {
      const match = /^\[data-config-host="(.+)"\]$/.exec(selector);
      return match ? holders[match[1]] || null : null;
    },
  };
  const sessionStorage = {
    getItem: key => store.get(key) || null,
    setItem: (key, value) => store.set(key, value), removeItem: key => store.delete(key),
  };
  const window = {
    addEventListener(name, listener) {
      const listeners = windowListeners.get(name) || new Set();
      listeners.add(listener); windowListeners.set(name, listeners);
    },
    removeEventListener(name, listener) { windowListeners.get(name)?.delete(listener); },
    dispatchEvent(event) { for (const listener of windowListeners.get(event.type) || []) listener(event); },
    CompanionProduct: {
      hosted: () => true, role: () => ({role_id: 'role-a', reply_api: PREFIX + 'reply',
        config_api: options.configApi}),
      roleTarget: () => ({bot_id: 'bot-a'}),
      acquaintanceDirectory: options.people ? async () => ({persons: options.people}) : undefined,
      identity: () => ({installation_id: 'inst', operator_id: 'admin'}),
      scopeKey: () => options.scopeKey || 'scope-a',
      ...(options.batchRead ? {request: async (method, path, body) => {
        const response = await new Promise((resolve, reject) =>
          requests.push({method, route: 'config', body, resolve, reject}));
        return response?.status === 'ok' ? response.data : response;
      }} : {}),
    },
    AstrBotPluginPage: {
      apiGet: (route, body) => new Promise((resolve, reject) =>
        requests.push({method: 'GET', route, body, resolve, reject})),
      apiPost: (route, body) => new Promise((resolve, reject) =>
        requests.push({method: 'POST', route, body, resolve, reject})),
    },
  };
  /* 适配层用 document.createElement 造分区挂载点；这里只要一个带 dataset 的壳。
     document 级 querySelector(All) 返回空：档案汇报（onStatus）在 VM 里
     查不到页面元素是常态，不能让它把 refresh 拖进 catch。 */
  const document = {
    createElement: () => ({
      dataset: {}, classList: {add() {}}, textContent: '',
      innerHTML: '', appendChild() {}, querySelector: () => null, querySelectorAll: () => [],
      listeners: {},
      addEventListener(type, callback) { this.listeners[type] = callback; },
      removeEventListener(type) { delete this.listeners[type]; },
    }),
    querySelector: () => null, querySelectorAll: () => [],
    hidden: false,
  };
  const sandbox = {window, sessionStorage, document, Event: class Event {constructor(type) {this.type = type;}}, crypto: {randomUUID: () => 'config-key'},
    setTimeout, clearTimeout, URLSearchParams, Math, Number, Object, JSON, Array, String};
  vm.runInNewContext(workbench, sandbox);
  vm.runInNewContext(adapter, sandbox);
  const editors = window.CompanionConfigEditors.create(kinds, () => root, views, endpointOverride);
  return {editors, root, holders, requests, store, window, painted, workbench: window.CompanionWorkbench};
}

/* Mounts the sections and answers each one's own read, in the same order app.js
   drives it: restore → paint → read. The workbench ignores a read that arrives
   while one is already in flight, so a section still issues exactly one request. */
async function loaded(harnessed, kinds = ['quota', 'image_review'], requestOverrides = {}) {
  harnessed.editors.restore();
  harnessed.editors.paint();
  for (const kind of Object.keys(harnessed.painted)) harnessed.painted[kind] = '';
  harnessed.editors.read();
  await drains();
  kinds.forEach((kind, index) => {
    const request = harnessed.requests[index];
    const expected = requestOverrides[kind] || {};
    assert.equal(request.method, 'GET');
    assert.equal(request.route, expected.route || 'config');
    assert.deepEqual(plain(request.body), Object.hasOwn(expected, 'body') ? expected.body : {sections: kind});
    request.resolve(Object.hasOwn(expected, 'response') ? expected.response : payload(kind));
  });
  await drains();
  return harnessed.editors.state.instances;
}

test('a multi-section page reads all configuration groups in one batch', async () => {
  const h = harness(['quota', 'image_review'], {}, '', {batchRead: true});
  h.editors.restore();
  h.editors.paint();
  const read = h.editors.read();
  await drains();
  assert.equal(h.requests.length, 1, 'multiple sections should share one read request');
  assert.equal(h.requests[0].method, 'GET');
  assert.deepEqual(plain(h.requests[0].body), {sections: 'quota,image_review'});
  h.requests[0].resolve({status: 'ok', data: {sections: {
    quota: payload('quota').sections.quota,
    image_review: payload('image_review').sections.image_review,
  }}});
  await read;
  await drains();
  assert.ok(h.editors.state.instances.every(instance => instance.state.status));
  assert.equal(h.requests.length, 1, 'prefetched sections must not be fetched again on mount');
});

test('dirty editor exposes changed field names and discard clears the source before unmount', async () => {
  const h = harness(['quota']);
  const [instance] = await loaded(h, ['quota']);
  instance.state.draft.private_owner_max_daily = 6;
  const [pending] = h.workbench.dirtyEditors();
  assert.equal(pending.fields.length, 1);
  assert.equal(pending.fields[0], h.window.CompanionConfigEditors.spec.quota.labels.private_owner_max_daily);
  assert.equal(pending.discard(), true);
  assert.equal(h.workbench.dirtyEditors().length, 0);
  instance.state.draft.private_owner_max_daily = 7;
  h.editors.unmount();
  assert.equal(h.workbench.dirtyEditors().length, 0);
  assert.equal(instance.state.draft, null);
});

test('detached config editors cannot keep prompting on subsequent navigation', async () => {
  const h = harness(['quota']);
  const [instance] = await loaded(h, ['quota']);
  instance.state.draft.private_owner_max_daily = 6;
  h.holders.quota.isConnected = false;
  assert.equal(h.workbench.dirtyEditors().length, 0);
  assert.equal(instance.state.draft, null);
});

test('save from navigation uses the existing receipt and preserves invalid drafts', async () => {
  const h = harness(['quota']);
  const [instance] = await loaded(h, ['quota']);
  instance.state.draft.private_owner_max_daily = 999;
  const invalid = await h.workbench.dirtyEditors()[0].save();
  assert.ok(invalid.error);
  assert.equal(h.requests.length, 1);
  assert.equal(h.workbench.dirtyEditors().length, 1);
  instance.state.draft.private_owner_max_daily = 6;
  const save = h.workbench.dirtyEditors()[0].save();
  await drains();
  assert.equal(h.requests[1].method, 'POST');
  h.requests[1].resolve({result:{operation_id:'navigation-save',replayed:false},
    status:{sections:{quota:section('quota',{private_owner_max_daily:6},TOKEN('b'))}}});
  assert.equal(await save, true);
  assert.equal(h.workbench.dirtyEditors().length, 0);
  h.editors.unmount();
});

test('the role dossier exposes the V1 character and world detail fields in one grouped contract', () => {
  const {window} = harness(['role_profile']);
  const profile = window.CompanionConfigEditors.spec.role_profile;
  const grouped = plain(profile.sections.flatMap(section => section.fields));
  assert.equal(profile.sections.length, 8);
  assert.equal(new Set(grouped).size, grouped.length, 'each field belongs to one dossier group');
  assert.deepEqual(grouped.slice().sort(), plain(profile.order).slice().sort());
  assert.deepEqual(grouped.slice().sort(), Object.keys(profile.labels).sort());
  for (const field of ['name', 'species', 'birthday', 'age', 'gender', 'appearance_points', 'hair', 'eyes',
    'clothing', 'occupation', 'personality', 'first_person', 'reaction_patterns', 'voice_examples',
    'emotional_style', 'social_distance', 'signature_phrases', 'desire', 'hobbies', 'taboos', 'key_lore',
    'world_name', 'era', 'tone', 'rules', 'scenes', 'network', 'world_extra', 'world']) {
    assert.ok(grouped.includes(field), `${field} is part of the editable role dossier`);
  }
  for (const legacy of ['identity', 'appearance', 'personality', 'motivations', 'boundaries', 'world']) {
    assert.ok(grouped.includes(legacy), `${legacy} retains its earlier saved content`);
  }
  // 平台称呼与世界观适配已下线：既不在可编辑字段里，也不再作为分组出现。
  for (const removed of ['platform_names', 'platform_group_chat', 'world_adaptation_mode', 'world_adaptation']) {
    assert.ok(!grouped.includes(removed), `${removed} must be gone from the dossier`);
  }
});

test('the settings search catalogue exposes field metadata without reading configuration values', () => {
  const {window} = harness(['proactive.contact', 'knowledge']);
  const entries = plain(window.CompanionConfigEditors.searchEntries());
  const contact = entries.filter(item => item.group === 'proactive.contact');
  assert.equal(contact.length, 173);
  assert.equal(new Set(contact.map(item => item.field)).size, 173);
  assert.ok(contact.some(item => item.field === 'morning_chain_enabled'
    && item.sectionTitle === '起床与晨间确认'));
  assert.ok(contact.some(item => item.field === 'balance_awareness_enabled'
    && item.label === '允许因余额变化联系' && item.sectionTitle === '余额感知'));
  assert.ok(entries.some(item => item.group === 'knowledge' && item.section === 'members'
    && item.field === 'member_clue_chars'));
  assert.ok(entries.every(item => !Object.hasOwn(item, 'value') && !Object.hasOwn(item, 'settings')));
});

test('weather cooldown belongs to the weather section with the other V1 weather settings', () => {
  const {window} = harness(['proactive.contact']);
  const sections = window.CompanionConfigEditors.spec['proactive.contact'].sections;
  const weather = sections.find(item => item.id === 'weather');
  const dedup = sections.find(item => item.id === 'dedup');
  const freshness = sections.find(item => item.id === 'freshness');
  const rhythm = sections.find(item => item.id === 'rhythm');
  assert.ok(weather.fields.includes('dedup_weather_window_minutes'));
  assert.ok(weather.fields.includes('weather_sample_minutes'));
  assert.ok(weather.fields.includes('official_alert_poll_minutes'));
  assert.ok(!dedup.fields.includes('dedup_weather_window_minutes'));
  assert.equal(freshness.fields.join(','), 'event_max_age_seconds');
  assert.ok(!rhythm.fields.includes('event_max_age_seconds'));
  assert.match(freshness.title, /素材与关系线索时效/);
  assert.match(freshness.help, /角色活动、日记、近期私聊素材和旧式关系线索/);
  assert.match(freshness.help, /主动跟进与未完话头不受这项时效限制/);
  const grouped = sections.flatMap(item => item.fields);
  assert.equal(grouped.length, new Set(grouped).size, 'each contact field belongs to exactly one section');
});

test('each section reads its own route and keeps its own edit token', async () => {
  const harnessed = harness();
  const [quotaInstance, reviewInstance] = await loaded(harnessed);
  assert.equal(quotaInstance.state.status.scope, 'role');
  assert.equal(reviewInstance.state.status.scope, 'window');
  assert.equal(quotaInstance.state.status.group_id, 'conversation.media_quota');
  assert.equal(reviewInstance.state.status.group_id, 'conversation.image_review');
  assert.deepEqual(plain(quotaInstance.state.draft), quota);
  assert.deepEqual(plain(reviewInstance.state.draft), review);
});

test('initial successful reads do not show a redundant status banner', async () => {
  const harnessed = harness(['quota']);
  const [instance] = await loaded(harnessed, ['quota']);
  assert.equal(instance.state.notice, '');
  assert.doesNotMatch(harnessed.painted.quota, /class="wb-notice/);
});

test('the server publishes the inheritance chain and per-field provenance', async () => {
  const harnessed = harness(['quota']);
  const [instance] = await loaded(harnessed, ['quota']);
  assert.deepEqual(plain(instance.state.container.layers), ['installation', 'role']);
  assert.equal(instance.state.container.writeLayer, 'role');
  assert.equal(instance.state.status.provenance.group_max_daily.layer, 'role');
  assert.equal(instance.state.status.provenance.group_max_daily.explicit, true);
  /* Bounds come from the contract, not from a page-local copy. */
  assert.equal(instance.state.container.fields.group_max_daily.maximum, 100);
});

test('role life defaults keep window-only fields disabled in the role editor', async () => {
  const harnessed = harness(['life']);
  const [instance] = await loaded(harnessed, ['life']);
  assert.equal(instance.state.container.writeLayer, 'role');
  assert.deepEqual(plain(instance.state.container.fields.sleep_enabled.editable), ['role', 'window']);
  assert.deepEqual(plain(instance.state.container.fields.diary_enabled.editable), ['window']);
  assert.match(harnessed.painted.life, /data-wb-field="diary_enabled"[^>]* disabled/);
  assert.match(harnessed.painted.life, /data-wb-field="sleep_enabled"[^>]*>/);
});

test('a section the route never answered is refused instead of half-applied', async () => {
  const harnessed = harness(['quota']);
  harnessed.editors.paint();
  await drains();
  /* The group id does not match the requested section: the token was never accepted. */
  harnessed.requests[0].resolve({sections: {image_review: section('image_review')}});
  await drains();
  const [instance] = harnessed.editors.state.instances;
  assert.equal(instance.state.denied, true);
  assert.equal(instance.state.draft, null);
});

/* 页面桥只转发信封的 message，机器码到前端时没有 code 字段——工作台内核必须
   从 message 里认出码并翻成中文，同时保留本来就是人话的原因句。 */
test('a machine code delivered only in message is translated on read failure', async () => {
  const harnessed = harness(['quota']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].reject(new Error('staging_not_initialized'));
  await drains();
  const [instance] = harnessed.editors.state.instances;
  assert.equal(instance.state.denied, true);
  assert.match(instance.state.notice, /角色数据尚未加载/);
  assert.doesNotMatch(String(instance.state.notice), /staging_not_initialized/);
  assert.doesNotMatch(harnessed.painted.quota || '', /staging_not_initialized/);
});

test('a timed out read retries in place and clears its transient notice after recovery', async () => {
  const harnessed = harness(['quota']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  const [instance] = harnessed.editors.state.instances;
  harnessed.requests[0].reject(Object.assign(new Error('request timed out'), {code: 'bridge_timeout'}));
  await drains();
  assert.equal(instance.state.loading, true);
  assert.equal(instance.state.denied, false);
  assert.match(instance.state.notice, /自动重试读取（1\/2）/);
  await new Promise(resolve => setTimeout(resolve, 450));
  await drains();
  assert.equal(harnessed.requests.length, 2);
  assert.equal(harnessed.requests[1].method, 'GET');
  harnessed.requests[1].resolve(payload('quota'));
  await drains();
  assert.ok(instance.state.status);
  assert.equal(instance.state.notice, '');
});

test('a reason sentence that is not a machine code is kept verbatim', async () => {
  const harnessed = harness(['quota']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  /* 后端有时直接给人话（例如某项能力未装配），这种原因句要原样展示。 */
  harnessed.requests[0].reject(new Error('unsupported reaction delivery settings'));
  await drains();
  const [instance] = harnessed.editors.state.instances;
  assert.match(instance.state.notice, /unsupported reaction delivery settings/);
});

test('a failed reread removes the previous workbench configuration from the page', async () => {
  const harnessed = harness(['quota']);
  const [instance] = await loaded(harnessed, ['quota']);
  assert.ok(instance.state.status);
  assert.ok(instance.state.container);
  harnessed.editors.read();
  await drains();
  harnessed.requests[1].reject(new Error('staging_not_initialized'));
  await drains();
  assert.equal(instance.state.status, null);
  assert.equal(instance.state.container, null);
  assert.equal(instance.state.draft, null);
  assert.match(harnessed.painted.quota, /角色数据尚未加载/);
  assert.doesNotMatch(harnessed.painted.quota, /group_max_daily/);
});

test('an out-of-range draft is refused before any write is dispatched', async () => {
  const harnessed = harness(['quota']);
  const [instance] = await loaded(harnessed, ['quota']);
  instance.state.draft.proactive_max_daily = 101;
  await instance.save(false);
  assert.equal(harnessed.requests.length, 1, 'the invalid draft must not reach the network');
  assert.equal(instance.state.bad, true);
});

test('saving sends only the changed fields with the section edit token', async () => {
  const harnessed = harness(['quota']);
  const [instance] = await loaded(harnessed, ['quota']);
  instance.state.draft.group_max_daily = 5;
  const saving = instance.save(false);
  await drains();
  assert.equal(harnessed.requests.length, 2,
    JSON.stringify(harnessed.requests.map(item => [item.method, item.route])));
  assert.equal(harnessed.requests[1].route, 'config/configure');
  assert.deepEqual(plain(harnessed.requests[1].body), {
    section: 'quota', operation_key: 'config-key', edit_token: TOKEN('a'), changes: {group_max_daily: 5},
  });
  harnessed.requests[1].resolve({result: {operation_id: 'op-1', replayed: false}});
  await saving;
  await drains();
  assert.equal(harnessed.requests[2].method, 'GET', 'refresh is independent from the committed write');
  harnessed.requests[2].resolve(payload('quota', {group_max_daily: 5}, TOKEN('b')));
  await drains();
  assert.equal(instance.state.status.settings.group_max_daily, 5);
  assert.equal(instance.state.pending, null);
});

test('explicit field saves compare with the stored baseline and can retry a rejected draft', async () => {
  const harnessed = harness(['quota']);
  const [instance] = await loaded(harnessed, ['quota']);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    assert.equal((await harnessed.editors.saveField('quota', 'group_max_daily', 101)).state, 'rejected');
    assert.equal(harnessed.requests.length, 1);
  }
  instance.state.draft.group_max_daily = 5;
  const saving = harnessed.editors.saveField('quota', 'group_max_daily', 5);
  await drains();
  assert.equal(harnessed.requests.length, 2, 'an unsaved matching draft still reaches the backend');
  harnessed.requests[1].resolve({result: {operation_id: 'field-save', replayed: false},
    status: payload('quota', {group_max_daily: 5}, TOKEN('b'))});
  assert.equal((await saving).state, 'saved');
  assert.equal((await harnessed.editors.saveField('quota', 'group_max_daily', 5)).state, 'unchanged');
  assert.equal(harnessed.requests.length, 2);
});

test('explicit field saves retain conflicts and reconcile unknown writes through the original operation', async () => {
  const harnessed = harness(['quota']);
  const [instance] = await loaded(harnessed, ['quota']);
  let saving = instance.saveField('group_max_daily', 5);
  await drains();
  harnessed.requests[1].resolve({status: 'error', data: {code: 'configuration_revision_conflict'}});
  assert.equal((await saving).state, 'conflict');
  assert.equal((await instance.saveField('group_max_daily', 5)).state, 'conflict');
  assert.equal(harnessed.requests.length, 2);
  const reading = instance.refresh();
  await drains();
  harnessed.requests[2].resolve(payload('quota', {}, TOKEN('b')));
  await reading;
  assert.equal(instance.state.stale, false, 'a successfully reread version can be edited immediately');
  saving = instance.saveField('group_max_daily', 5);
  await drains();
  const original = plain(harnessed.requests[3].body);
  harnessed.requests[3].reject(Object.assign(new Error('bridge down'), {code: 'bridge_timeout'}));
  assert.equal((await saving).state, 'unknown');
  assert.equal((await instance.saveField('group_max_daily', 5)).state, 'unknown');
  assert.equal(harnessed.requests.length, 4, 'the field editor must not repeat an unconfirmed write');
  const reconciling = instance.save(true);
  await drains();
  assert.deepEqual(plain(harnessed.requests[4].body), original);
  harnessed.requests[4].resolve({result: {operation_id: 'field-save', replayed: true},
    status: payload('quota', {group_max_daily: 5}, TOKEN('b'))});
  await reconciling;
  assert.equal((await instance.saveField('group_max_daily', 5)).state, 'unchanged');
});

test('explicit field saves preserve unrelated configuration drafts', async () => {
  const harnessed = harness(['quota']);
  const [instance] = await loaded(harnessed, ['quota']);
  instance.state.draft.proactive_max_daily = 3;
  assert.equal((await instance.saveField('group_max_daily', 5)).state, 'dirty');
  assert.equal(instance.state.draft.group_max_daily, -1);
  assert.equal(harnessed.requests.length, 1);
});

test('核对新版本保留本地改动，并采用其他页面已保存的无冲突字段', async () => {
  const h = harness(['quota']);
  const [instance] = await loaded(h, ['quota']);
  instance.state.draft.group_max_daily = 5;
  const saving = instance.save();
  await drains();
  h.requests[1].reject(Object.assign(new Error('configuration_revision_conflict'), {code: 'configuration_revision_conflict'}));
  await saving;
  assert.match(h.painted.quota, /data-wb-action="reconcile"/);
  assert.match(h.painted.quota, /改动已保留，待核对当前配置/);
  const reading = instance.refresh();
  await drains();
  h.requests[2].resolve(payload('quota', {private_friend_max_daily: 9}, TOKEN('b')));
  await reading;
  assert.equal(instance.state.draft.group_max_daily, 5);
  assert.equal(instance.state.draft.private_friend_max_daily, 9);
  assert.deepEqual(plain(instance.state.conflicts), {});
  const retry = instance.save();
  await drains();
  assert.deepEqual(plain(h.requests[3].body.changes), {group_max_daily: 5});
  assert.equal(h.requests[3].body.edit_token, TOKEN('b'));
  h.requests[3].resolve({result: {operation_id: 'merged', replayed: false},
    status: payload('quota', {group_max_daily: 5, private_friend_max_daily: 9}, TOKEN('c'))});
  await retry;
  assert.equal(instance.dirty(), false);
});

test('同一字段同时修改需逐项确认，刷新两次也不会静默覆盖', async () => {
  const h = harness(['quota']);
  const [instance] = await loaded(h, ['quota']);
  instance.state.draft.group_max_daily = 5;
  for (let index = 1; index <= 2; index += 1) {
    const reading = instance.refresh();
    await drains();
    h.requests[index].resolve(payload('quota', {group_max_daily: 7}, TOKEN('b')));
    await reading;
    assert.equal(instance.state.draft.group_max_daily, 5);
    assert.ok(instance.state.conflicts.group_max_daily);
  }
  await instance.save();
  assert.equal(h.requests.length, 3);
  assert.match(h.painted.quota, /当前已保存：7/);
  assert.match(h.painted.quota, /我的修改：5/);
  h.holders.quota.listeners.click({target: {closest: selector => selector === '[data-wb-conflict]'
    ? {dataset: {wbConflict: 'group_max_daily', wbChoice: 'draft'}} : null}});
  assert.deepEqual(plain(instance.state.conflicts), {});
  const saving = instance.save();
  await drains();
  assert.equal(h.requests[3].body.changes.group_max_daily, 5);
  h.requests[3].resolve({result: {operation_id: 'chosen-draft', replayed: false},
    status: payload('quota', {group_max_daily: 5}, TOKEN('c'))});
  await saving;
  assert.equal(instance.dirty(), false);
});

test('选择当前值消除冲突，只提交剩余本地改动', async () => {
  const h = harness(['quota']);
  const [instance] = await loaded(h, ['quota']);
  Object.assign(instance.state.draft, {group_max_daily: 5, proactive_max_daily: 3});
  const reading = instance.refresh();
  await drains();
  h.requests[1].resolve(payload('quota', {group_max_daily: 7}, TOKEN('b')));
  await reading;
  h.holders.quota.listeners.click({target: {closest: selector => selector === '[data-wb-conflict]'
    ? {dataset: {wbConflict: 'group_max_daily', wbChoice: 'current'}} : null}});
  assert.equal(instance.state.draft.group_max_daily, 7);
  const saving = instance.save();
  await drains();
  assert.deepEqual(plain(h.requests[2].body.changes), {proactive_max_daily: 3});
  h.requests[2].resolve({result: {operation_id: 'chosen-current', replayed: false},
    status: payload('quota', {group_max_daily: 7, proactive_max_daily: 3}, TOKEN('c'))});
  await saving;
});

test('冲突后使用已保存配置会重新读取，恢复后可以继续编辑保存', async () => {
  const h = harness(['quota']);
  const [instance] = await loaded(h, ['quota']);
  instance.state.draft.group_max_daily = 5;
  const saving = instance.save();
  await drains();
  h.requests[1].reject(Object.assign(new Error('configuration_revision_conflict'), {code: 'configuration_revision_conflict'}));
  await saving;
  h.holders.quota.listeners.click({target: {closest: selector => selector === '[data-wb-action]'
    ? {dataset: {wbAction: 'reset'}} : null}});
  await drains();
  h.requests[2].resolve(payload('quota', {group_max_daily: 7}, TOKEN('b')));
  await drains();
  assert.equal(instance.state.stale, false);
  assert.equal(instance.state.draft.group_max_daily, 7);
  assert.equal(instance.dirty(), false);
  assert.equal(h.requests.length, 3, '恢复配置只读取，不重复执行被拒绝的保存');
});

test('未确认保存的工作台重新挂载后仍能读取状态并核对原请求', async () => {
  const h = harness(['quota']);
  const [instance] = await loaded(h, ['quota']);
  instance.state.draft.group_max_daily = 5;
  const saving = instance.save();
  await drains();
  const original = plain(h.requests[1].body);
  h.requests[1].reject(Object.assign(new Error('bridge down'), {code: 'bridge_timeout'}));
  await saving;
  instance.unmount();
  instance.mount();
  await drains();
  assert.equal(h.requests[2].method, 'GET');
  h.requests[2].resolve(payload('quota', {group_max_daily: 5}, TOKEN('b')));
  await drains();
  assert.ok(instance.state.pending);
  const reconciling = instance.save(true);
  await drains();
  assert.deepEqual(plain(h.requests[3].body), original);
  h.requests[3].resolve({result: {operation_id: 'recovered', replayed: true},
    status: payload('quota', {group_max_daily: 5}, TOKEN('b'))});
  await reconciling;
  assert.equal(instance.state.pending, null);
  assert.equal(instance.dirty(), false);
  instance.unmount();
});

test('an enum-valued text field is edited as a select, not a free-text box', async () => {
  const harnessed = harness(['image_review']);
  const [instance] = await loaded(harnessed, ['image_review']);
  const item = instance.state.container.fields.group_nsfw_image_review_mode;
  assert.equal(item.kind, 'text');
  assert.deepEqual(plain(item.choices), {single: '单模型', dual: '双模型复核'});
  const markup = harnessed.painted.image_review;
  assert.match(markup, /<select data-wb-field="group_nsfw_image_review_mode">/);
  assert.doesNotMatch(markup, /<textarea data-wb-field="group_nsfw_image_review_mode"/);
  /* 未声明选项的文本字段仍是自由输入。 */
  assert.equal(instance.state.container.fields.group_nsfw_image_review_custom_prompt.choices, null);
});

test('the group image review switch is distinct from inconclusive-result handling', async () => {
  const harnessed = harness(['image_review']);
  const [instance] = await loaded(harnessed, ['image_review']);
  const spec = harnessed.window.CompanionConfigEditors.spec.image_review;
  assert.equal(spec.labels.enable_group_nsfw_private_fallback, '启用群聊成图审核');
  assert.match(spec.helps.enable_group_nsfw_private_fallback, /关闭时跳过审核/);
  assert.equal(spec.labels.group_nsfw_image_review_failure_action, '审核未得到有效结论时');
  assert.equal(spec.choices.group_nsfw_image_review_sensitivity.relaxed, '宽松');
  assert.equal(spec.choices.group_nsfw_image_review_sensitivity.lenient, undefined);
  assert.match(spec.helps.group_nsfw_image_review_failure_action, /请求者私聊已授权时尝试投递原图/);
  assert.deepEqual(plain(instance.state.container.fields.group_nsfw_image_review_failure_action.choices), {
    private: '尝试私聊投递原图', block: '群聊仅发送文字',
  });
  assert.match(harnessed.painted.image_review, /启用群聊成图审核/);
  assert.match(harnessed.painted.image_review, /审核未得到有效结论时/);
  assert.doesNotMatch(harnessed.painted.image_review, /审核不通过时降级私聊投递|改走私聊投递|整条阻止/);
});

test('a value outside the declared options is refused before any write', async () => {
  const harnessed = harness(['image_review']);
  const [instance] = await loaded(harnessed, ['image_review']);
  instance.state.draft.group_nsfw_image_review_mode = 'triple';
  await instance.save(false);
  assert.equal(harnessed.requests.length, 1, 'the invalid draft must not reach the network');
  assert.equal(instance.state.bad, true);
  /* 合法选项仍然放行。 */
  instance.state.draft.group_nsfw_image_review_mode = 'dual';
  const saving = instance.save(false);
  await drains();
  assert.equal(harnessed.requests.length, 2);
  harnessed.requests[1].resolve({result: {operation_id: 'op-3', replayed: false},
    status: payload('image_review', {group_nsfw_image_review_mode: 'dual'}, TOKEN('b'))});
  await saving;
  await drains();
});

test('a save the server answered but refused stops being pending', async () => {
  const harnessed = harness(['image_review']);
  const [instance] = await loaded(harnessed, ['image_review']);
  instance.state.draft.group_nsfw_image_review_mode = 'dual';
  const saving = instance.save(false);
  await drains();
  /* 服务端明确回绝：这不是「未确认」，继续挂着 pending 反而会让人重复提交。 */
  harnessed.requests[1].resolve({status: 'error', data: {code: 'configuration_revision_conflict'}});
  await saving;
  await drains();
  assert.equal(instance.state.pending, null);
  assert.equal(instance.state.bad, true);
  assert.match(instance.state.notice, /配置已经更新/);
  assert.equal(harnessed.store.size, 0, 'the draft receipt must not survive a refusal');
});

test('a coded revision conflict from the bridge is a confirmed refusal', async () => {
  const harnessed = harness(['image_review']);
  const [instance] = await loaded(harnessed, ['image_review']);
  instance.state.draft.group_nsfw_image_review_mode = 'dual';
  const saving = instance.save(false);
  await drains();
  harnessed.requests[1].reject(Object.assign(new Error('configuration_revision_conflict'), {
    code: 'configuration_revision_conflict',
  }));
  await saving;
  await drains();
  assert.equal(instance.state.pending, null);
  assert.equal(instance.state.stale, true);
  assert.equal(harnessed.store.size, 0);
  assert.match(instance.state.notice, /配置已经更新/);
});

test('an unconfirmed save is automatically replayed once with the original request', async () => {
  const harnessed = harness(['image_review']);
  const [instance] = await loaded(harnessed, ['image_review']);
  instance.state.draft.group_nsfw_image_review_mode = 'dual';
  const saving = instance.save(false);
  await drains();
  const original = plain(harnessed.requests[1].body);
  /* 结果没有回来（连接层失败）：写入可能已落库，所以保留 pending 待核对。 */
  harnessed.requests[1].reject(Object.assign(new Error('bridge down'), {code: 'bridge_timeout'}));
  await saving;
  await drains();
  assert.equal(instance.state.pending.operation_key, 'config-key');
  assert.equal(instance.state.notice, '', 'the actionable pending block replaces the duplicate top warning');
  assert.match(harnessed.painted.image_review, /保存结果尚未确认（连接超时，请核对连接后重试。）。可能已经写入/);
  assert.equal((harnessed.painted.image_review.match(/保存结果尚未确认/g) || []).length, 1,
    'an unconfirmed save is explained once');
  const persisted = JSON.parse([...harnessed.store.values()][0]);
  assert.equal(persisted.pending_reason, '连接超时，请核对连接后重试。',
    'the reason survives a page reload with the pending operation');
  await new Promise(resolve => setTimeout(resolve, 450));
  assert.deepEqual(plain(harnessed.requests[2].body), original,
    'automatic recovery must reuse the original request instead of minting a second operation');
  harnessed.requests[2].resolve({result: {operation_id: 'op-2', replayed: true},
    status: payload('image_review', {group_nsfw_image_review_mode: 'dual'}, TOKEN('b'))});
  await drains();
  await saving;
  assert.equal(instance.state.pending, null);
  assert.match(instance.state.savedState, /is-replayed/);
  assert.match(instance.state.notice, /没有重复写入/);
});

test('a restored pending config request is automatically replayed with its original section and key', async () => {
  const store = new Map([[`companion-workbench.v1:${JSON.stringify(['inst', 'admin', 'scope-a', 'screen'])}`,
    JSON.stringify({operation_key: 'legacy-screen-save', edit_token: TOKEN('a'),
      changes: {interpretation_enabled: true}, reset_to_inherit: []})]]);
  const harnessed = harness(['screen'], {}, '', {store});
  const [instance] = await loaded(harnessed, ['screen']);
  assert.equal(instance.state.pending.operation_key, 'legacy-screen-save');
  await new Promise(resolve => setTimeout(resolve, 450));
  const retrying = harnessed.requests[1];
  assert.equal(retrying.method, 'POST');
  assert.equal(retrying.route, 'config/configure');
  assert.deepEqual(plain(harnessed.requests[1].body), {
    section: 'screen', operation_key: 'legacy-screen-save', edit_token: TOKEN('a'),
    changes: {interpretation_enabled: true},
  });
  harnessed.requests[1].resolve({result: {operation_id: 'legacy-screen-save', replayed: true},
    status: payload('screen', {interpretation_enabled: true}, TOKEN('b'))});
  await drains();
  assert.equal(instance.state.pending, null);
});

test('a nested host error envelope is a confirmed refusal, not an unknown save', async () => {
  const harnessed = harness(['screen']);
  const [instance] = await loaded(harnessed, ['screen']);
  instance.state.draft.interpretation_enabled = true;
  const saving = instance.save(false);
  await drains();
  harnessed.requests[1].resolve({data: {status: 'error', message: 'invalid_response',
    reason: 'request was rejected'}});
  await saving;
  assert.equal(instance.state.pending, null);
  assert.match(instance.state.notice, /返回数据不完整/);
});

test('a pending save is isolated to the selected object and role', async () => {
  const store = new Map();
  const first = harness(['image_review'], {}, '', {store, scopeKey: 'target-a:role-a'});
  const [firstInstance] = await loaded(first, ['image_review']);
  firstInstance.state.draft.group_nsfw_image_review_mode = 'dual';
  const saving = firstInstance.save(false);
  await drains();
  first.requests[1].reject(Object.assign(new Error('bridge down'), {code: 'bridge_timeout'}));
  await saving;
  await drains();
  assert.equal(store.size, 1);

  const second = harness(['image_review'], {}, '', {store, scopeKey: 'target-b:role-b'});
  const [secondInstance] = await loaded(second, ['image_review']);
  assert.equal(secondInstance.state.pending, null,
    'a pending receipt from another target must not appear in this editor');
  firstInstance.unmount();
});

test('the endpoint falls back to the reply route family when the manifest has no config_api', () => {
  const {window} = harness();
  assert.equal(window.CompanionConfigEditors.endpointFor(window.CompanionProduct), PREFIX + 'config');
  window.CompanionProduct.role = () => ({reply_api: '/other', config_api: PREFIX + 'x/config'});
  assert.equal(window.CompanionConfigEditors.endpointFor(window.CompanionProduct), PREFIX + 'x/config');
  assert.equal(window.CompanionConfigEditors.endpointFor(window.CompanionProduct,
    PREFIX + 'targets/profile-b/roles/role-b/config'), PREFIX + 'targets/profile-b/roles/role-b/config');
  assert.equal(window.CompanionConfigEditors.endpointFor(window.CompanionProduct,
    PREFIX + 'targets/group-a/roles/role-a/reply/participation'),
    PREFIX + 'targets/group-a/roles/role-a/reply/participation');
  assert.equal(window.CompanionConfigEditors.endpointFor(window.CompanionProduct, '/other/config'), null);
});

test('the group interjection editor uses its target route without a section query or discriminator', async () => {
  const endpoint = PREFIX + 'targets/group-a/roles/role-a/reply/participation';
  const harnessed = harness(['group_participation'], {group_participation:{
    sectionIds:['interjection'], title:'本群主动插话', scopeNote:'当前群聊',
    query:null, requestSection:null, resetKey:'reset_to_inherit',
  }}, endpoint);
  const [instance] = await loaded(harnessed, ['group_participation'], {
    group_participation:{route:'targets/group-a/roles/role-a/reply/participation', body:null,
      response:groupInterjectionStatus()},
  });
  assert.equal(harnessed.requests[0].route, 'targets/group-a/roles/role-a/reply/participation');
  assert.equal(harnessed.requests[0].body, null);
  assert.equal(instance.state.container.resettable, true);
  assert.deepEqual(plain(instance.state.container.groups.map(group => group.id)), ['interjection']);
  assert.deepEqual(plain(instance.state.container.groups[0].fields), [
    'enable_group_interjection', 'group_interject_min_interval_minutes', 'group_interject_max_daily',
    'enable_group_interjection_feedback', 'group_interject_feedback_window_seconds']);

  const saving = instance.saveField('enable_group_interjection', true);
  await drains();
  assert.equal(harnessed.requests[1].route,
    'targets/group-a/roles/role-a/reply/participation/configure');
  assert.equal(harnessed.requests[1].body.section, undefined);
  assert.deepEqual(plain(harnessed.requests[1].body.changes), {enable_group_interjection: true});
  const refreshed = newSection('group_participation', {enable_group_interjection: true}, TOKEN('b'));
  refreshed.settings = Object.fromEntries(Object.entries(refreshed.settings).filter(([name]) =>
    ['enable_group_interjection', 'group_interject_min_interval_minutes', 'group_interject_max_daily',
      'enable_group_interjection_feedback', 'group_interject_feedback_window_seconds'].includes(name)));
  refreshed.fields = Object.fromEntries(Object.entries(refreshed.fields).filter(([name]) => name in refreshed.settings));
  refreshed.provenance = Object.fromEntries(Object.entries(refreshed.provenance).filter(([name]) => name in refreshed.settings));
  harnessed.requests[1].resolve({result:{operation_id:'group-interjection-save',replayed:false},status:refreshed});
  assert.equal((await saving).state, 'saved');
});

test('installation-wide contact preferences never use the selected target config route', async () => {
  const targetEndpoint = PREFIX + 'targets/window-a/roles/role-a/config';
  const harnessed = harness(['proactive.contact'], {}, '', {configApi: targetEndpoint});
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();

  assert.equal(harnessed.requests[0].method, 'GET');
  assert.equal(harnessed.requests[0].route, 'config');
  assert.deepEqual(plain(harnessed.requests[0].body), {sections: 'proactive.contact'});
  harnessed.requests[0].resolve(payload('proactive.contact'));
  await drains();
  const [instance] = harnessed.editors.state.instances;
  assert.equal(instance.state.status.scope, 'installation');
  assert.deepEqual(plain(instance.state.status.layers), ['installation']);

  instance.state.draft.screen_glance_enabled = true;
  const saving = instance.save(false);
  await drains();
  assert.equal(harnessed.requests[1].method, 'POST');
  assert.equal(harnessed.requests[1].route, 'config/configure');
  assert.deepEqual(plain(harnessed.requests[1].body), {
    section: 'proactive.contact', operation_key: 'config-key', edit_token: TOKEN('a'),
    changes: {screen_glance_enabled: true},
  });
  harnessed.requests[1].resolve({result: {operation_id: 'contact-save', replayed: false},
    status: newSection('proactive.contact', {screen_glance_enabled: true}, TOKEN('b'))});
  await saving;
  await drains();
  harnessed.editors.stop();
});

test('learning policies are role-scoped and selectors only filter records', () => {
  const start = app.indexOf('function renderLearningPage(){');
  const end = app.indexOf('/* 2026-09-30 接回：', start);
  assert.ok(start >= 0 && end > start, 'the active learning page renderer must exist');
  const page = app.slice(start, end);
  assert.match(page, /\['strategy','角色策略'\]/);
  assert.match(page, /configEditors\('growthConfigWorkspace',\['role\.growth\.skills'\]/);
  assert.match(page, /configEditors\('expressionLearningConfigWorkspace',\['expression\.learning'\]/);
  assert.doesNotMatch(page, /strategyRole|learningRoleControls\('strategy'|roleConfigApiFor\(strategy/);
  assert.doesNotMatch(page, /role_growth/,
    'per-person growth permissions belong to the private member editor');
  assert.match(page, /learningTab==='strategy'/);
  assert.match(page, /const scopeControls=scopeKey\?learningScopeControls/,
    'object selection is rendered only by the record tabs');
  assert.match(page, /learningRoleControls\(scopeKey,'筛选记录',scopeControls\)/);
});

test('role learning policies use the selected role config route', async () => {
  const targetEndpoint = PREFIX + 'targets/window-a/roles/role-a/config';
  const harnessed = harness(['role.growth.skills'], {}, '', {configApi: targetEndpoint});
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  assert.equal(harnessed.requests.length, 1);
  assert.equal(harnessed.requests[0].route, targetEndpoint.slice(PREFIX.length));
  harnessed.requests[0].resolve(payload('role.growth.skills'));
  await drains();
});

/* ==== 2026-09-28 新接入的四个分区 ==== */

test('target-owned sections use their registered scope and role route', async () => {
  const installationKinds = new Set(['proactive.contact', 'reaction', 'budget']);
  const kinds = Object.keys(NEW_KINDS).filter(kind => !installationKinds.has(kind));
  const targetEndpoint = PREFIX + 'targets/window-a/roles/role-a/config';
  const harnessed = harness(kinds, {}, '', {configApi: targetEndpoint});
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  assert.equal(harnessed.requests.length, kinds.length);
  kinds.forEach((kind, index) => {
    assert.equal(harnessed.requests[index].method, 'GET');
    assert.equal(harnessed.requests[index].route, targetEndpoint.slice(PREFIX.length));
    assert.deepEqual(plain(harnessed.requests[index].body), {sections: kind});
    harnessed.requests[index].resolve(payload(kind));
  });
  await drains();
  const instances = harnessed.editors.state.instances;
  /* 每个分区各自持 edit token；container.group 是页面侧的 kind，
     后端真正的组名在 status.group_id 上。 */
  assert.deepEqual(plain(instances.map(item => item.state.status.group_id)),
    kinds.map(kind => NEW_KINDS[kind].group));
  assert.deepEqual(plain(instances.map(item => item.state.container.group)), kinds);
  /* 层由各分区自己的契约决定，不是一律 window。 */
  assert.deepEqual(plain(instances.map(item => item.state.status.scope)),
    kinds.map(kind => NEW_KINDS[kind].scope || 'window'));
});

test('reaction retrieval settings render as installation-wide and do not use window draft keys', async () => {
  const targetEndpoint = PREFIX + 'targets/window-a/roles/role-a/config';
  const harnessed = harness(['reaction'], {}, '', {configApi: targetEndpoint});
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  assert.equal(harnessed.requests[0].route, 'config');
  harnessed.requests[0].resolve(payload('reaction'));
  await drains();
  const painted = harnessed.painted.reaction;
  assert.match(painted, /插件安装全局/);
  assert.match(painted, /视觉复核/);
  const instance = harnessed.editors.state.instances[0];
  instance.state.draft.candidate_limit = 7;
  const saving = instance.save(false);
  await drains();
  assert.equal(harnessed.requests[1].method, 'POST');
  harnessed.requests[1].reject(new Error('temporary timeout'));
  await saving;
  await drains();
  const storedKeys = [...harnessed.store.keys()];
  assert.equal(storedKeys.length, 1);
  const keyParts = JSON.parse(storedKeys[0].slice('companion-workbench.v1:'.length));
  assert.equal(keyParts[2], 'installation');
  assert.equal(keyParts[3], 'reaction');
});

test('a new section saves only the changed fields with its own token', async () => {
  const harnessed = harness(['recall']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('recall'));
  await drains();
  const [instance] = harnessed.editors.state.instances;
  instance.state.draft.transcribe_enabled = true;
  const saving = instance.save(false);
  await drains();
  assert.deepEqual(plain(harnessed.requests[1].body), {
    section: 'recall', operation_key: 'config-key', edit_token: TOKEN('a'), changes: {transcribe_enabled: true},
  });
  harnessed.requests[1].resolve({result: {operation_id: 'op', replayed: false},
    status: newSection('recall', {transcribe_enabled: true}, TOKEN('b'))});
  await saving;
  await drains();
  assert.equal(instance.state.base.transcribe_enabled, true);
});

test('a value outside the published bounds never reaches the network', async () => {
  const harnessed = harness(['screen']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('screen'));
  await drains();
  const [instance] = harnessed.editors.state.instances;
  /* 注册表写的是 200–1600；越界必须就地拦下。 */
  instance.state.draft.journal_material_max_chars = 5000;
  await instance.save(false);
  assert.equal(harnessed.requests.length, 1, 'the out-of-range draft must not be dispatched');
  assert.equal(instance.state.bad, true);
});

test('the role skill list is edited as text because the parser is lenient', async () => {
  const harnessed = harness(['role.growth.skills']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('role.growth.skills'));
  await drains();
  const [instance] = harnessed.editors.state.instances;
  /* The domain parser accepts JSON arrays and comma-separated text. */
  instance.state.draft.custom_skills = '写作, 植物学';
  const saving = instance.save(false);
  await drains();
  assert.equal(harnessed.requests[1].body.changes.custom_skills, '写作, 植物学');
  harnessed.requests[1].resolve({result: {operation_id: 'op', replayed: false},
    status: newSection('role.growth.skills', {custom_skills: '写作, 植物学'}, TOKEN('b'))});
  await saving;
  await drains();
  assert.equal(instance.state.bad, false);
});

test('a scope refusal on a new section is translated instead of shown raw', async () => {
  const harnessed = harness(['person']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].reject(new Error('person_scope_invalid'));
  await drains();
  const [instance] = harnessed.editors.state.instances;
  assert.equal(instance.state.denied, true);
  assert.match(instance.state.notice, /熟人卡按对象生效/);
  assert.doesNotMatch(String(instance.state.notice), /person_scope_invalid/);
});

/* ==== 2026-09-28 第二批：knowledge 与 role.life ==== */

test('knowledge reads its own group and saves only the changed field', async () => {
  const harnessed = harness(['knowledge']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  assert.equal(harnessed.requests[0].route, 'config');
  assert.deepEqual(plain(harnessed.requests[0].body), {sections: 'knowledge'});
  harnessed.requests[0].resolve(payload('knowledge'));
  await drains();
  const [instance] = harnessed.editors.state.instances;
  assert.equal(instance.state.status.group_id, 'knowledge');
  assert.equal(instance.state.status.scope, 'window');
  instance.state.draft.source_ids = 'kb:kb-1';
  const saving = instance.save(false);
  await drains();
  assert.deepEqual(plain(harnessed.requests[1].body), {
    section: 'knowledge', operation_key: 'config-key', edit_token: TOKEN('a'), changes: {source_ids: 'kb:kb-1'},
  });
  harnessed.requests[1].resolve({result: {operation_id: 'op', replayed: false},
    status: newSection('knowledge', {source_ids: 'kb:kb-1'}, TOKEN('b'))});
  await saving;
  await drains();
  assert.equal(instance.state.base.source_ids, 'kb:kb-1');
});

test('knowledge sources are selected from the official AstrBot catalogue', async () => {
  const harnessed = harness(['knowledge']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('knowledge'));
  await drains();
  const [instance] = harnessed.editors.state.instances;
  const painted = harnessed.painted.knowledge;
  assert.match(painted, /星河设定/);
  assert.match(painted, /角色资料\.md/);
  assert.match(painted, /data-wb-source-base="kb-1"/);
  assert.match(painted, /data-wb-source-document="kb-1"/);
  assert.doesNotMatch(painted, /import_paths/);
  assert.equal(instance.state.container.fields.source_ids.kind, 'text');
  assert.equal(instance.state.container.fields.source_ids.input, 'source-checklist');
});

test('knowledge source checkmarks survive saving and rereading newline-separated ids', async () => {
  const harnessed = harness(['knowledge']);
  const [instance] = await loaded(harnessed, ['knowledge']);
  const selected = 'doc:kb-1:doc-1\nkb:missing';
  instance.state.draft.source_ids = selected;
  const saving = instance.save(false);
  await drains();
  const status = payload('knowledge', {source_ids: selected}, TOKEN('b'));
  status.sections.knowledge.knowledge_sources.push({id: 'kb:missing', type: 'missing', name: '已移除的资料'});
  harnessed.requests[1].resolve({result: {operation_id: 'op', replayed: false}, status});
  await saving;
  instance.attach();

  assert.match(harnessed.painted.knowledge, /data-wb-source-id="doc:kb-1:doc-1"[^>]*checked/);
  assert.match(harnessed.painted.knowledge, /data-wb-source-id="kb:missing"[^>]*checked/);
  assert.match(harnessed.painted.knowledge, /已选 2 \/ 16 项/);
  /* 逐篇令牌选满这个库时，父级按「已全选」显示勾选；写回值仍是逐篇令牌，
     不会被折叠成 kb:kb-1，否则以后新增的文档会被顺带纳入引用。 */
  assert.match(harnessed.painted.knowledge, /data-wb-source-base="kb-1"[^>]*checked/);
  const reading = instance.refresh(true);
  await drains();
  harnessed.requests[2].resolve(status);
  await reading;
  assert.match(harnessed.painted.knowledge, /data-wb-source-id="doc:kb-1:doc-1"[^>]*checked/);
});

/* ==== 世界知识来源：整库 / 逐篇 / 半选 ==== */

const kbDocuments = count => Array.from({length: count}, (_, index) => ({
  id: `doc:kb-1:doc-${index + 1}`, doc_id: `doc-${index + 1}`,
  name: `资料${index + 1}.md`, file_type: 'md', chunk_count: index + 1,
}));

/* 一个库有 3 篇文档时，父级与子项的组合才分得出「整库 / 选满 / 选一部分」。 */
const knowledgePayload = (documents, sourceIds) => {
  const response = payload('knowledge', {source_ids: sourceIds});
  response.sections.knowledge.knowledge_sources = [{
    id: 'kb:kb-1', type: 'knowledge_base', kb_id: 'kb-1', name: '星河设定', emoji: '📚',
    doc_count: documents.length,
    chunk_count: documents.reduce((sum, doc) => sum + doc.chunk_count, 0),
    documents,
  }];
  return response;
};

async function loadedSources(documents, sourceIds) {
  const harnessed = harness(['knowledge']);
  const [instance] = await loaded(harnessed, ['knowledge'],
    {knowledge: {response: knowledgePayload(documents, sourceIds)}});
  return {harnessed, instance, painted: harnessed.painted.knowledge};
}

/* 半选属性只能由 JS 设置，把这段实现单独取出来验证。 */
const sourceIndeterminate = (() => {
  const start = workbench.indexOf('  const syncSourceIndeterminate');
  const end = workbench.indexOf('  /* 把一次复选框点击换算成新的 source_ids', start);
  return vm.runInNewContext(`() => { ${workbench.slice(start, end)}
    return syncSourceIndeterminate; }`)();
})();

test('a whole knowledge base renders checked with documents disabled and a hint', async () => {
  const {painted} = await loadedSources(kbDocuments(3), 'kb:kb-1');
  assert.match(painted, /<section class="wb-source-group" data-wb-source-state="whole">/);
  assert.match(painted, /data-wb-source-base="kb-1"[^>]*checked/);
  assert.match(painted, /data-wb-source-id="doc:kb-1:doc-1"[^>]* disabled/);
  assert.match(painted, /data-wb-source-id="doc:kb-1:doc-2"[^>]* disabled/);
  assert.doesNotMatch(painted, /data-wb-source-id="doc:kb-1:doc-1"[^>]*checked/);
  assert.match(painted, /整库引用中：本库 3 篇文档整体参与引用。需要逐篇选择时，请先取消整库。/);
  assert.match(painted, /已选 1 \/ 16 项/);
});

test('a partially selected knowledge base renders a mixed parent without checked', async () => {
  const {painted} = await loadedSources(kbDocuments(3),
    'doc:kb-1:doc-1\ndoc:kb-1:doc-2');
  assert.match(painted, /<section class="wb-source-group" data-wb-source-state="partial">/);
  assert.match(painted, /data-wb-source-base="kb-1"[^>]*aria-checked="mixed"/);
  assert.match(painted, /data-wb-source-base="kb-1"[^>]*data-wb-source-state="partial"/);
  assert.doesNotMatch(painted, /data-wb-source-base="kb-1"[^>]* checked/);
  assert.match(painted, /逐篇引用 · 已选 2\/3 篇/);
  assert.doesNotMatch(painted, /整库引用中/);
});

test('a fully selected knowledge base renders the parent checked but keeps document tokens', async () => {
  const {painted} = await loadedSources(kbDocuments(3),
    'doc:kb-1:doc-1\ndoc:kb-1:doc-2\ndoc:kb-1:doc-3');
  assert.match(painted, /<section class="wb-source-group" data-wb-source-state="full">/);
  assert.match(painted, /data-wb-source-base="kb-1"[^>]*checked/);
  assert.match(painted, /data-wb-source-id="doc:kb-1:doc-1"[^>]*checked/);
  assert.match(painted, /data-wb-source-id="doc:kb-1:doc-2"[^>]*checked/);
  assert.match(painted, /data-wb-source-id="doc:kb-1:doc-3"[^>]*checked/);
  assert.match(painted, /逐篇引用 · 已选 3\/3 篇/);
  assert.doesNotMatch(painted, /aria-checked="mixed"/);
});

test('a legacy value mixing kb: and doc: renders as whole instead of checking both', async () => {
  const {painted} = await loadedSources(kbDocuments(3),
    'kb:kb-1\ndoc:kb-1:doc-1\ndoc:kb-1:doc-2');
  assert.match(painted, /<section class="wb-source-group" data-wb-source-state="whole">/);
  assert.match(painted, /data-wb-source-base="kb-1"[^>]*checked/);
  assert.match(painted, /data-wb-source-id="doc:kb-1:doc-1"[^>]* disabled/);
  assert.doesNotMatch(painted, /data-wb-source-id="doc:kb-1:doc-1"[^>]*checked/);
  assert.match(painted, /整库引用中/);
});

test('partial parent input is marked indeterminate after the list is rendered', () => {
  const inputs = [{indeterminate: false}, {indeterminate: false}];
  sourceIndeterminate({querySelectorAll: selector => {
    assert.equal(selector, 'input[data-wb-source-state="partial"]');
    return inputs;
  }});
  assert.ok(inputs.every(input => input.indeterminate === true));
});

test('resolveSourceToggle keeps whole and document references mutually exclusive', () => {
  const {workbench: kernel} = harness([]);
  const docs = ['doc-1', 'doc-2', 'doc-3'];
  const other = 'doc:kb-2:doc-9';

  /* 取消整库：同库逐篇一起清掉，别的库不动。 */
  const cancelled = kernel.resolveSourceToggle(
    `kb:kb-1\ndoc:kb-1:doc-1\n${other}`,
    {kbId: 'kb-1', checked: false, docIds: docs, limit: 16});
  assert.equal(cancelled.accepted, true);
  assert.equal(cancelled.value, other);

  /* 半选状态点父级 = 全选：逐篇换成一条整库令牌。 */
  const whole = kernel.resolveSourceToggle(
    'doc:kb-1:doc-1\ndoc:kb-1:doc-2',
    {kbId: 'kb-1', checked: true, docIds: docs, limit: 16});
  assert.equal(whole.accepted, true);
  assert.equal(whole.value, 'kb:kb-1');

  /* 逐篇选满后点父级 = 取消：结果为空，而不是留下三条 doc:。 */
  const cleared = kernel.resolveSourceToggle(
    'doc:kb-1:doc-1\ndoc:kb-1:doc-2\ndoc:kb-1:doc-3',
    {kbId: 'kb-1', checked: false, docIds: docs, limit: 16});
  assert.equal(cleared.accepted, true);
  assert.equal(cleared.value, '');

  /* 点子项：写出 doc: 令牌；历史值里的 kb: 兜底删掉。 */
  const picked = kernel.resolveSourceToggle(
    'kb:kb-1',
    {kbId: 'kb-1', docId: 'doc:kb-1:doc-2', checked: true, docIds: docs, limit: 16});
  assert.equal(picked.accepted, true);
  assert.equal(picked.value, 'doc:kb-1:doc-2');

  /* 选中最后一篇也不自动折叠成 kb:，覆盖率语义保持不变。 */
  const completed = kernel.resolveSourceToggle(
    'doc:kb-1:doc-1\ndoc:kb-1:doc-2',
    {kbId: 'kb-1', docId: 'doc-3', checked: true, docIds: docs, limit: 16});
  assert.equal(completed.accepted, true);
  assert.equal(completed.value, 'doc:kb-1:doc-1\ndoc:kb-1:doc-2\ndoc:kb-1:doc-3');

  /* 超过 source_limit：拒绝这一次勾选，值原样返回。 */
  const limited = kernel.resolveSourceToggle(
    'doc:kb-1:doc-1',
    {kbId: 'kb-1', docId: 'doc-2', checked: true, docIds: docs, limit: 1});
  assert.equal(limited.accepted, false);
  assert.equal(limited.value, 'doc:kb-1:doc-1');
  assert.match(limited.notice, /最多读取 1 个来源/);
});

test('the rest gate mode and dream afterglow are edited as selects, not free text', async () => {
  const harnessed = harness(['life']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('life'));
  await drains();
  const [instance] = harnessed.editors.state.instances;
  const painted = harnessed.painted.life;
  /* 注册表里这两项是 TEXT，取值合法性只有内核的 valid() 知道；
     漏传 choices 就会被当成任意文本放过，填错后静默回默认值。 */
  assert.match(painted, /<select[^>]*data-wb-field="rest_reply_mode"/);
  assert.match(painted, /<select[^>]*data-wb-field="dream_afterglow_mode"/);
  assert.match(painted, /value="probability"[^>]*>概率判定/);
  assert.match(painted, /value="llm"[^>]*>模型判定/);
  assert.match(painted, /value="auto"[^>]*>自动/);
  assert.match(painted, /rest_reply_active_windows[\s\S]{0,400}23:00-08:30/);
});

test('legacy rest-gate aliases normalize before validation and save canonically', async () => {
  const aliases = {model: 'llm', '模型': 'llm', llm_judge: 'llm', 'llm-judge': 'llm'};
  const {window} = harness([]);
  const normalizeDraft = window.CompanionConfigEditors.spec.life.normalizeDraft;
  for (const [legacy, canonical] of Object.entries(aliases)) {
    const draft = {rest_reply_mode: legacy};
    normalizeDraft(draft);
    assert.equal(draft.rest_reply_mode, canonical, `legacy mode ${legacy} should normalize`);
  }

  const harnessed = harness(['life']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('life', {rest_reply_mode: '模型'}));
  await drains();
  const [instance] = harnessed.editors.state.instances;
  assert.equal(instance.state.draft.rest_reply_mode, 'llm');
  assert.equal(instance.state.base.rest_reply_mode, '模型', 'the saved value stays visible as a migration diff');
  assert.match(fieldRow(harnessed.painted.life, 'rest_reply_mode'), /value="llm"[^>]*selected/);

  const saving = instance.save(false);
  await drains();
  assert.deepEqual(plain(harnessed.requests[1].body.changes), {rest_reply_mode: 'llm'});
  harnessed.requests[1].resolve({result: {operation_id: 'life-mode', replayed: false},
    status: newSection('life', {rest_reply_mode: 'llm'}, TOKEN('b'))});
  await saving;
  await drains();
});

test('a life field outside its registered bounds never reaches the network', async () => {
  const harnessed = harness(['life']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('life'));
  await drains();
  const [instance] = harnessed.editors.state.instances;
  instance.state.draft.rest_reply_probability = 180;
  await instance.save(false);
  assert.equal(harnessed.requests.length, 1, 'the out-of-range draft must not be dispatched');
  assert.equal(instance.state.bad, true);
});

test('the knowledge cross-field rule is reported in words, not as a bare code', async () => {
  const harnessed = harness(['knowledge']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('knowledge'));
  await drains();
  const [instance] = harnessed.editors.state.instances;
  instance.state.draft.entry_char_limit = 4000;
  const saving = instance.save(false);
  await drains();
  /* 后端 KnowledgeConfigurationOwner 拒绝「单轮引用上限 < 单条字数」这个组合，
     它跨字段、各自的范围都表达不了，所以只能在保存时被后端回绝。 */
  harnessed.requests[1].resolve({status: 'error', data: {code: 'knowledge_settings_invalid'}});
  await saving;
  await drains();
  assert.match(instance.state.notice, /不能小于单条素材的字数上限/);
  assert.doesNotMatch(String(instance.state.notice), /knowledge_settings_invalid/);
});

/* 回归：整数档位字段此前拿不到枚举，渲染成数字框、且下拉读回的字符串
   判不过 valid()，导致这类字段根本存不进去（schedule_influence_strength
   就是实例）。contract() 现在对非布尔字段都保留 choices，并按下拉值归一。 */
test('an integer field with named steps keeps its choices and accepts them', () => {
  const {workbench} = harness([]);
  const definition = {kind: 'integer', default: 0, minimum: 0, maximum: 3};
  const choices = {0: '不影响', 1: '轻微参考', 2: '留出练习时间', 3: '优先安排相关活动'};
  const item = workbench.contract('schedule_influence_strength', definition, choices);
  assert.equal(item.kind, 'int');
  assert.deepEqual(plain(item.choices), choices);
  assert.equal(workbench.valid(item, 0), true);
  assert.equal(workbench.valid(item, 3), true);
  /* 越界与非整数仍然被拒。 */
  assert.equal(workbench.valid(item, 4), false);
  assert.equal(workbench.valid(item, 1.5), false);
  /* 布尔字段不接受枚举：它的控件就是开关。 */
  assert.equal(workbench.contract('x', {kind: 'boolean', default: false}, choices).choices, null);
});

/* 有依赖关系的字段靠段落分组说明条件；漏传 sections 时内核会退回单段渲染，
   分组与依赖说明被静默丢掉——看起来正常，只是少了几段。 */
test('the knowledge section groups official sources, injection and member clues', async () => {
  const harnessed = harness(['knowledge']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('knowledge'));
  await drains();
  const painted = harnessed.painted.knowledge;
  assert.match(painted, /data-wb-group="import"/);
  assert.match(painted, /data-wb-group="inject"/);
  assert.match(painted, /data-wb-group="members"/);
  assert.match(painted, /AstrBot 官方知识库/);
  assert.match(painted, /在对话中引用所选资料/);
});

test('the life section separates routines, writing, the rest gate and the backlog', async () => {
  const harnessed = harness(['life']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('life'));
  await drains();
  const painted = harnessed.painted.life;
  assert.match(painted, /data-wb-group="routine"/);
  assert.match(painted, /data-wb-group="writing"/);
  assert.match(painted, /data-wb-group="rest"/);
  assert.match(painted, /data-wb-group="backlog"/);
  /* 例程总闸关着时，日记/睡眠/阅读的日程都不登记——这是新接入的依赖说明。 */
  assert.match(painted, /关闭总闸时，日记、睡眠与阅读的日程都不再登记/);
  assert.match(painted, /关闭总闸时，下面各项都不起作用/);
  /* 两个时刻字段用时间控件；注册表里它们是 TEXT，值仍是字符串。 */
  assert.match(painted, /<input type="time" data-wb-field="diary_time"/);
  assert.match(painted, /<input type="time" data-wb-field="sleep_time"/);
  /* 时区是单行文本，不是多行框——TEXT 且没声明 rows 时内核会画成 textarea。 */
  assert.match(painted, /<input type="text" data-wb-field="timezone"/);
  assert.match(painted, /<input type="text" data-wb-field="sleep_timezone"/);
  /* 分组不能把没提到的字段弄丢：全部 24 项都要在界面上。 */
  const container = harnessed.editors.state.instances[0].state.container;
  const rendered = plain(container.groups.flatMap(group => group.fields));
  assert.deepEqual(rendered.sort(), plain(Object.keys(NEW_KINDS.life.settings)).sort());
});

test('the life audit blocks a malformed clock or timezone before any request', async () => {
  /* 内核的 valid() 对 TEXT 只判「是不是字符串」，HH:MM 与 IANA 时区
     得靠分区的 audit 钩子兜住，否则值会走到后端才被拒。 */
  const harnessed = harness(['life']);
  const [instance] = await loaded(harnessed, ['life']);
  const posts = () => harnessed.requests.filter(request => request.method === 'POST').length;
  const before = posts();
  instance.state.draft.diary_time = '25:00';
  await instance.save(false);
  assert.equal(posts(), before, '25:00 不该发请求');
  assert.equal(instance.state.bad, true);
  assert.match(instance.state.notice, /二十四小时制/);
  instance.state.draft.diary_time = '23:50';
  instance.state.draft.timezone = 'Not/A-Zone';
  await instance.save(false);
  assert.equal(posts(), before, '无效时区不该发请求');
  assert.match(instance.state.notice, /IANA 时区/);
  instance.state.draft.timezone = 'Asia/Tokyo';
  instance.state.draft.sleep_variation_minutes = 121;
  await instance.save(false);
  assert.equal(posts(), before, '超过 120 分钟的浮动不该发请求');
  /* 提示要说清是哪一项、允许什么。此前这里断言的是那句通用文案
     「请核对数值类型与范围。」——它出现在卡片顶部，而按保存的人通常在底部，
     等于没提示；现在改成字段级提示并跳到该字段，文案里带上标签与区间。 */
  assert.match(instance.state.notice, /自然浮动上限/);
  assert.match(instance.state.notice, /0 到 120/);
  assert.match(instance.state.notice, /改动还在/);
  /* 合法的组合应当放行。 */
  instance.state.draft.sleep_variation_minutes = 120;
  await instance.save(false);
  assert.equal(posts(), before + 1);
});

test('the library section separates the switches from the bounds they govern', async () => {
  const harnessed = harness(['library']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('library'));
  await drains();
  const painted = harnessed.painted.library;
  assert.match(painted, /data-wb-group="switches"/);
  assert.match(painted, /data-wb-group="limits"/);
  assert.match(painted, /data-wb-group="preference"/);
  assert.match(painted, /data-wb-group="triggers"/);
  assert.match(painted, /关闭书架后，下面各项都不起作用/);
  /* 书架按角色保存，所以标签是「安装与角色」而不是「当前会话」。 */
  assert.match(painted, /安装与角色/);
  assert.doesNotMatch(painted, /当前会话/);
  /* 间隔先于概率这条顺序必须在页面上说清，否则用户会把概率设成 1 却等不到开口。 */
  assert.match(painted, /先看最短间隔，再看概率/);
  assert.match(painted, /重启不重置/);
  /* 偏好只是参考，不是本轮要求——这句不能丢。 */
  assert.match(painted, /偏好只是参考，不是本轮要求/);
  /* 分组不能把没提到的字段弄丢：全部 12 项都要在界面上。 */
  const container = harnessed.editors.state.instances[0].state.container;
  const rendered = plain(container.groups.flatMap(group => group.fields));
  assert.deepEqual(rendered.sort(), plain(Object.keys(NEW_KINDS.library.settings)).sort());
});

test('the wardrobe section separates its switches, ceilings and the operator words', async () => {
  const harnessed = harness(['wardrobe']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('wardrobe'));
  await drains();
  const painted = harnessed.painted.wardrobe;
  assert.match(painted, /data-wb-group="switches"/);
  assert.match(painted, /data-wb-group="material"/);
  assert.match(painted, /data-wb-group="words"/);
  assert.match(painted, /关闭后既不进提示词，也不接受新的识图入库/);
  assert.match(painted, /安装与角色/);
  assert.doesNotMatch(painted, /当前会话/);
  /* 条目本体在数据表里，页面只出现开关与上限：一个字段都不能丢。 */
  const container = harnessed.editors.state.instances[0].state.container;
  const rendered = plain(container.groups.flatMap(group => group.fields));
  assert.deepEqual(rendered.sort(), plain(Object.keys(NEW_KINDS.wardrobe.settings)).sort());
});

test('the reference section keeps the daily outfit switch independent', async () => {
  const harnessed = harness(['photo.reference']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('photo.reference'));
  await drains();
  const painted = harnessed.painted['photo.reference'];
  assert.match(painted, /data-wb-group="switches"/);
  assert.match(painted, /data-wb-group="rotation"/);
  /* 同意参考图不等于同意每天出穿搭图，文案必须说清这一点。 */
  assert.match(painted, /不等于同意每天都出一张穿搭图/);
  assert.match(painted, /允许群聊使用参考图/);
  assert.match(painted, /配置里不存文件路径/);
  assert.match(painted, /安装与角色/);
  const container = harnessed.editors.state.instances[0].state.container;
  const rendered = plain(container.groups.flatMap(group => group.fields));
  assert.deepEqual(rendered.sort(), plain(Object.keys(NEW_KINDS['photo.reference'].settings)).sort());
});

test('the skill group is role-shared and has no window override', async () => {
  const targetEndpoint = PREFIX + 'targets/window-a/roles/role-a/config';
  const harnessed = harness(['role.growth.skills'], {}, '', {configApi: targetEndpoint});
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  assert.equal(harnessed.requests[0].route, targetEndpoint.slice(PREFIX.length));
  harnessed.requests[0].resolve(payload('role.growth.skills'));
  await drains();
  const painted = harnessed.painted['role.growth.skills'];
  assert.ok(painted, 'the skills section must paint into its own host, keys: '
    + JSON.stringify(Object.keys(harnessed.painted)));
  /* This policy is shared by the selected role and writable at its role layer. */
  assert.match(painted, /安装与角色/, 'scope label was: '
    + (/<span class="wb-scope">([^<]*)</.exec(painted) || [])[1]);
  assert.match(painted, /<h3>技能领域<\/h3>/);
  assert.match(painted, /data-layer="role"/);
  assert.doesNotMatch(painted, /当前会话可覆盖|data-layer="window"/);
  assert.doesNotMatch(painted, /scope_key|binding_revision|operation_id/);
  /* 两个字段都不能在分组里弄丢。 */
  const container = harnessed.editors.state.instances[0].state.container;
  const rendered = plain(container.groups.flatMap(group => group.fields));
  assert.deepEqual(rendered.sort(), plain(Object.keys(NEW_KINDS['role.growth.skills'].settings)).sort());
});

test('expression learning exposes separate private and group cadence with a shared group budget', async () => {
  const targetEndpoint = PREFIX + 'targets/window-a/roles/role-a/config';
  const harnessed = harness(['expression.learning'], {}, '', {configApi: targetEndpoint});
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('expression.learning'));
  await drains();
  const painted = harnessed.painted['expression.learning'];
  assert.match(painted, /私聊学习扫描间隔/);
  assert.match(painted, /群聊学习扫描间隔/);
  assert.match(painted, /角色群聊每日学习批次/);
  assert.match(painted, /安装与角色/);
  const rendered = plain(harnessed.editors.state.instances[0].state.container.groups
    .flatMap(group => group.fields)).sort();
  assert.deepEqual(rendered, Object.keys(NEW_KINDS['expression.learning'].settings).sort());
});

test('window growth permissions are absent from the shared configuration editor', () => {
  assert.doesNotMatch(adapter, /role_growth\s*:/);
  assert.doesNotMatch(app, /mountConfigEditor\('growthConfigWorkspace',\s*\[[^\]]*role_growth/);
});

test('the wardrobe note states its bound because the registry cannot enforce one', async () => {
  const harnessed = harness(['wardrobe']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('wardrobe'));
  await drains();
  /* 文字长度由扩展在写入时拒绝；注册表只公布上限，不做长度校验，
     所以页面的职责是说清 800 字这条线，而不是假装能拦住。 */
  const painted = harnessed.painted.wardrobe;
  assert.match(painted, /最多 800 字/);
  assert.match(painted, /超长会被拒绝而不是截断/);
});

test('the group participation section splits the three independent chains', async () => {
  const harnessed = harness(['group_participation']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('group_participation'));
  await drains();
  const painted = harnessed.painted.group_participation;
  for (const id of ['topics', 'atmosphere', 'joke_guard', 'denoise', 'interjection', 'repeat'])
    assert.match(painted, new RegExp(`data-wb-group="${id}"`), id);
  assert.match(painted, /跟踪群聊话题线/);
  assert.match(painted, /不保存成员身份/);
  assert.match(painted, /玩笑护栏/);
  assert.doesNotMatch(painted, /关闭这一项时，下面五项都不起作用|关闭总闸时下面四项不起作用|关闭总闸时下面七项都不起作用/);
  /* 分组不能把没提到的字段弄丢，打断图也必须进入复读设置区。 */
  const container = harnessed.editors.state.instances[0].state.container;
  assert.ok(container.groups.find(group => group.id === 'repeat').fields.includes('group_repeat_interrupt_image_path'));
  assert.match(painted, /打断复读时发的图片/);
  assert.match(painted, /复读只会跟读原句或打断/);
  const rendered = plain(container.groups.flatMap(group => group.fields));
  assert.deepEqual(rendered.sort(), plain(Object.keys(NEW_KINDS.group_participation.settings)).sort());
});

test('group participation fields can be filtered by page without duplicating the shared contract', async () => {
  const decisionView = harness(['group_participation'], {group_participation:{
    sectionIds:['topics', 'atmosphere', 'joke_guard', 'repeat', 'history'], navigationLimit:0,
  }});
  const [decision] = await loaded(decisionView, ['group_participation']);
  const decisionFields = plain(decision.state.container.groups.flatMap(group => group.fields));
  assert.deepEqual(plain(decision.state.container.groups.map(group => group.id)),
    ['topics', 'atmosphere', 'joke_guard', 'repeat', 'history']);
  assert.ok(decisionFields.includes('enable_group_topic_threads'));
  assert.ok(decisionFields.includes('group_topic_thread_max'));
  assert.deepEqual(decisionFields.includes('enable_group_joke_guard'), true);
  assert.equal(decisionFields.includes('enable_group_persona_denoise'), false);
  assert.equal(decisionFields.includes('enable_group_interjection'), false);

  const interjectionView = harness(['group_participation'], {group_participation:{
    sectionIds:['interjection'], title:'本群主动插话', query:null, requestSection:null,
    resetKey:'reset_to_inherit',
  }}, PREFIX + 'targets/group-a/roles/role-a/reply/participation');
  const [interjection] = await loaded(interjectionView, ['group_participation'], {
    group_participation:{route:'targets/group-a/roles/role-a/reply/participation', body:null,
      response:groupInterjectionStatus()},
  });
  const interjectionFields = plain(interjection.state.container.groups.flatMap(group => group.fields));
  assert.deepEqual(plain(interjection.state.container.groups.map(group => group.id)), ['interjection']);

  const expressionView = harness(['group_participation'], {group_participation:{
    sectionIds:['denoise'], title:'群聊表达去噪', note:'只影响群聊回复的表达腔调。',
  }});
  const [expression] = await loaded(expressionView, ['group_participation']);
  const expressionFields = plain(expression.state.container.groups.flatMap(group => group.fields));
  assert.deepEqual(plain(expression.state.container.groups.map(group => group.id)), ['denoise']);
  assert.deepEqual(expressionFields, ['enable_group_persona_denoise']);
  assert.equal(expression.state.container.title, '群聊表达去噪');

  const behaviorView=harness(['group_participation'],{group_participation:{sectionIds:['behavior'],title:'行为模仿策略',note:''}});
  const [behavior]=await loaded(behaviorView,['group_participation']);
  const behaviorFields=plain(behavior.state.container.groups.flatMap(group=>group.fields));
  assert.deepEqual(plain(behavior.state.container.groups.map(group=>group.id)),['behavior']);
  assert.ok(behaviorFields.includes('enable_group_behavior_imitation'));
  const exposed = [...decisionFields, ...interjectionFields, ...expressionFields, ...behaviorFields];
  assert.equal(exposed.length, new Set(exposed).size);
  assert.deepEqual([...exposed].sort(),
    plain(Object.keys(NEW_KINDS.group_participation.settings)).sort());
});

test('world knowledge and member clues can live on separate page views of one contract', async () => {
  const worldView = harness(['knowledge'], {knowledge:{sectionIds:['import', 'inject']}});
  const [world] = await loaded(worldView, ['knowledge']);
  const worldFields = plain(world.state.container.groups.flatMap(group => group.fields));
  assert.deepEqual(plain(world.state.container.groups.map(group => group.id)), ['import', 'inject']);
  assert.equal(worldFields.includes('member_recognition_enabled'), false);

  const clueView = harness(['knowledge'], {knowledge:{sectionIds:['members'],
    title:'群聊成员线索', note:'只在群聊输入中提供已登记的成员线索。'}});
  const [clues] = await loaded(clueView, ['knowledge']);
  const clueFields = plain(clues.state.container.groups.flatMap(group => group.fields));
  assert.deepEqual(plain(clues.state.container.groups.map(group => group.id)), ['members']);
  assert.deepEqual(clueFields, ['member_recognition_enabled', 'member_match_aliases',
    'member_inject_limit', 'member_clue_chars']);

  const exposed = [...worldFields, ...clueFields];
  assert.equal(exposed.length, new Set(exposed).size);
  const visibleKnowledgeFields = Object.keys(NEW_KINDS.knowledge.settings)
    .filter(name => !['import_enabled', 'import_paths'].includes(name));
  assert.deepEqual([...exposed].sort(), plain(visibleKnowledgeFields).sort());
});

test('probability fields keep decimals because the registry declares a fraction', async () => {
  /* 这一组有四个 0–1 的概率字段。它们不是整数档位，也不是 TEXT，
     若被当成整数处理，0.18 会被 valid() 拒掉，跟读概率就永远存不进去。 */
  const harnessed = harness(['group_participation']);
  const [instance] = await loaded(harnessed, ['group_participation']);
  /* container.fields 是按短名索引的契约表；container.groups[].fields 存的是名字。 */
  const byName = instance.state.container.fields;
  for (const name of ['group_repeat_follow_probability', 'group_repeat_interrupt_probability',
                      'group_repeat_interrupt_probability_step']) {
    assert.equal(byName[name].kind, 'float', name);
    assert.equal(byName[name].minimum, 0, name);
    assert.equal(byName[name].maximum, 1, name);
  }
  /* 整数上限字段不受影响。 */
  assert.equal(byName.group_interject_max_daily.kind, 'int');
  assert.equal(byName.group_interject_max_daily.maximum, 20);
});

test('a group participation refusal is translated instead of shown raw', async () => {
  const harnessed = harness(['group_participation']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  /* 桥只转发信封的 message，所以机器码就是在这里出现的。 */
  harnessed.requests[0].reject({message: 'group_participation_configuration_invalid'});
  await drains();
  const markup = harnessed.painted.group_participation;
  assert.doesNotMatch(markup, /group_participation_configuration_invalid/);
  assert.match(markup, /打断语只能是一行/);
});

/* ==== 主动联系偏好（proactive.contact）：173 个页面可编辑项、四层、24 段 ====
 *
 * 这一组此前四个触点全缺（编辑器 / SECTIONS / spec / 挂载点），173 个可编辑项里 92 项
 * 在任何页面上都点不到。字段名与类型按注册表抄成独立的一份：页面与它逐项对齐
 * 才算「没有字段被丢掉」，而页面与**注册表**的对齐由
 * tools/audit_page_config_coverage.py 的 MODULES 表保证。
 *
 * 数量本身就是断言：47 boolean / 58 integer / 47 text / 17 weight / 4 probability。
 */
const CONTACT_KINDS = {
  boolean: [
    "affection.enabled", "allow_insomnia_night_message", "automatic_enabled",
    "balance_awareness_enabled", "balance_include_amount_in_message", "care.enabled", "chat.enabled",
    "creation.enabled", "date_checkin_enabled", "dedup_enabled", "dedup_last_message_enabled",
    "enable_daily_greetings", "enable_llm_proactive_persona_judge", "enable_proactive_burst", "enabled",
    "environment.enabled", "external_event_self_link_enabled", "followup.enabled", "greeting.enabled",
    "insomnia_require_sleep_state", "invitation.enabled", "life.enabled", "meal.enabled",
    "meal_checkin_enabled", "news.enabled", "news_daily_hot_read_enabled", "news_share_enabled",
    "morning_chain_enabled", "morning_nudge_enabled", "morning_role_greeting_enabled",
    "official_weather_alert_enabled", "play.enabled", "poke_action_enabled",
    "proactive_hour_activity_enabled", "quiet_enabled", "reminder.enabled", "repair.enabled",
    "routine_learning_enabled",
    "screen.enabled", "screen_glance_enabled", "screen_glance_followup_enabled",
    "screen_glance_goodnight_enabled", "voice_action_enabled", "weather_change_enabled",
    "wake_require_device_evidence", "weather_checkin_enabled", "web_exploration_enabled",
  ],
  integer: [
    "appointment_lag_minutes", "appointment_lead_minutes", "appointment_pre_silence_minutes",
    "balance_check_interval_minutes", "balance_critical_percent_threshold", "balance_critical_threshold",
    "balance_low_percent_threshold", "balance_low_threshold", "balance_message_cooldown_hours",
    "contact_followup_after_minutes", "contact_followup_cooldown_minutes",
    "dedup_last_message_window_minutes", "dedup_min_shared_tokens", "dedup_sent_window_minutes",
    "dedup_weather_window_minutes", "event_max_age_seconds", "external_event_self_link_cooldown_hours",
    "generation_history_limit", "greeting_idle_minutes", "history_max_chars", "idle_seconds",
    "important_date_lookahead_days", "insomnia_night_daily_limit", "interval_seconds",
    "max_contacts_24h", "meal_care_followup_minutes", "morning_nudge_delay_minutes",
    "morning_nudge_max_attempts", "news_check_interval_minutes",
    "news_hot_result_count", "official_alert_poll_minutes", "poke_action_cooldown_minutes",
    "poke_action_daily_limit", "proactive_burst_gap_max_seconds", "proactive_burst_gap_min_seconds",
    "proactive_burst_max_messages", "proactive_closing_contacts_threshold",
    "proactive_closing_grace_minutes", "proactive_intensity_body_chars",
    "proactive_persona_judge_cache_minutes",
    "proactive_persona_judge_threshold", "proactive_share_probability", "screen_glance_cooldown_minutes",
    "screen_glance_daily_limit", "screen_glance_followup_after_minutes",
    "screen_glance_followup_cooldown_minutes", "screen_glance_goodnight_delay_minutes",
    "routine_window_days", "unanswered_pause_after", "unanswered_slowdown_start",
    "wake_contact_max_daily", "wake_contact_min_confidence", "wake_contact_window_minutes",
    "voice_action_cooldown_minutes",
    "voice_action_max_chars", "weather_sample_minutes", "web_exploration_min_interval_hours",
    "web_exploration_result_count",
  ],
  text: [
    "affection.pause_until", "balance_currency_label", "care.pause_until", "chat.pause_until",
    "creation.pause_until", "date_checkin_time", "dedup_policies", "environment.pause_until",
    "followup.pause_until", "greeting.pause_until", "greeting_evening_window", "greeting_morning_window",
    "greeting_noon_window", "insomnia_night_end", "insomnia_night_start",
    "invitation.pause_until", "life.pause_until", "meal.pause_until", "meal_dinner_time",
    "meal_lunch_time", "news.pause_until", "news_hot_sources", "news_source_kind", "news_source_uid",
    "news_source_url", "note", "official_alert_min_severity", "pause_until", "play.pause_until",
    "proactive_hour_activity_curve", "proactive_intensity_preset",
    "proactive_persona_judge_exempt_purposes", "quiet_end", "quiet_start", "quiet_timezone",
    "reminder.pause_until", "repair.pause_until", "screen.pause_until", "style_prompt",
    "wake_morning_end", "wake_morning_start", "weather_checkin_time", "web_exploration_interests",
    "expression_prompt", "news_sharing_prompt", "length_prompt", "news_length_prompt",
  ],
  weight: [
    "affection.weight", "care.weight", "chat.weight", "creation.weight", "environment.weight",
    "external_event_self_link_weight", "followup.weight", "greeting.weight", "invitation.weight",
    "life.weight", "meal.weight", "news.weight", "play.weight", "reminder.weight", "repair.weight",
    "screen.weight", "unanswered_max_interval_multiplier",
  ],
  probability: ["dedup_min_overlap_ratio", "morning_wake_confidence_floor", "silence_feedback_penalty", "silence_min_confidence"],
};
/* 文本字段的默认值照注册表抄：页面审计按这些值判断时刻、窗口、曲线与清单的形状。 */
const CONTACT_DEFAULTS = {
  quiet_start: '23:00', quiet_end: '07:00', quiet_timezone: 'Asia/Shanghai',
  meal_lunch_time: '12:00', meal_dinner_time: '18:00', weather_checkin_time: '08:30',
  date_checkin_time: '09:00', insomnia_night_start: '23:00', insomnia_night_end: '06:00',
  greeting_morning_window: '07:45-10:20', greeting_noon_window: '12:05-13:35',
  greeting_evening_window: '20:10-21:20',
  morning_nudge_delay_minutes: 25, morning_nudge_max_attempts: 1, routine_window_days: 60,
  wake_contact_window_minutes: 90, wake_contact_min_confidence: 65, wake_contact_max_daily: 1,
  wake_morning_start: '05:00', wake_morning_end: '11:30', morning_wake_confidence_floor: 0.25,
  dedup_policies: 'semantic,content_fingerprint,life_event,event_revision',
  contact_followup_after_minutes: 45, contact_followup_cooldown_minutes: 180,
  news_source_kind: 'host', news_hot_sources: 'weibo,hackernews', official_alert_min_severity: '',
  proactive_intensity_preset: 'off', balance_currency_label: '元',
  proactive_hour_activity_curve: '0.22,0.16,0.12,0.10,0.10,0.14,0.28,0.50,0.66,0.72,0.78,0.92,'
    + '1.0,0.94,0.82,0.74,0.78,0.92,1.0,0.98,0.88,0.70,0.48,0.32',
};
const CONTACT_BOUNDS = {
  contact_followup_after_minutes: [10, 1440],
  contact_followup_cooldown_minutes: [30, 2880],
  morning_nudge_delay_minutes: [10, 90], morning_nudge_max_attempts: [0, 2],
  routine_window_days: [14, 180], wake_contact_window_minutes: [15, 240],
  wake_contact_min_confidence: [40, 100], wake_contact_max_daily: [0, 3],
  morning_wake_confidence_floor: [0, 1],
};
const CONTACT_SETTINGS = {};
const CONTACT_FIELDS = {};
for (const [kind, names] of Object.entries(CONTACT_KINDS)) {
  for (const name of names) {
    if (Object.hasOwn(CONTACT_DEFAULTS, name)) CONTACT_SETTINGS[name] = CONTACT_DEFAULTS[name];
    else if (kind === 'boolean') CONTACT_SETTINGS[name] = false;
    else if (kind === 'text') CONTACT_SETTINGS[name] = '';
    else if (kind === 'integer') CONTACT_SETTINGS[name] = 0;
    else if (kind === 'weight') CONTACT_SETTINGS[name] = 1;
    else CONTACT_SETTINGS[name] = 0.5;
    CONTACT_FIELDS[name] = [kind, ...(CONTACT_BOUNDS[name] || [])];
  }
}
const CONTACT_LAYERS = ['installation', 'role', 'relationship', 'window'];
const CONTACT_WINDOW_QUOTA = ['max_contacts_24h'];
NEW_KINDS['proactive.contact'] = {
  group: 'proactive.contact', layers: ['installation'], editableLayers: CONTACT_LAYERS, scope: 'installation',
  settings: CONTACT_SETTINGS, fields: CONTACT_FIELDS,
  editable: Object.fromEntries(CONTACT_WINDOW_QUOTA.map(name =>
    [name, CONTACT_LAYERS.filter(layer => layer !== 'relationship')])),
};
const CONTACT_SECTIONS = ['master', 'rhythm', 'freshness', 'intensity', 'quiet', 'dedup',
  'judgement', 'bond', 'daily', 'followup', 'share', 'greeting', 'morning', 'meal', 'date', 'weather', 'news', 'web',
  'selflink', 'balance', 'screen', 'action', 'appointment', 'context', 'prompts'];

async function contactLoaded(changes = {}, token = TOKEN('a')) {
  const harnessed = harness(['proactive.contact']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('proactive.contact', changes, token));
  await drains();
  return harnessed;
}

test('legacy proactive intensity aliases normalize before validation and save canonically', async () => {
  const aliases = {'': 'off', manual: 'off', none: 'off', default: 'off', standard: 'balanced',
    active: 'high_private', private: 'high_private', group: 'high_group',
    online: 'live', '直播': 'live', '高频': 'live'};
  const {window} = harness([]);
  const normalizeDraft = window.CompanionConfigEditors.spec['proactive.contact'].normalizeDraft;
  for (const [legacy, canonical] of Object.entries(aliases)) {
    const draft = {proactive_intensity_preset: legacy};
    normalizeDraft(draft);
    assert.equal(draft.proactive_intensity_preset, canonical, `legacy tier ${legacy} should normalize`);
  }

  const harnessed = await contactLoaded({proactive_intensity_preset: '高频'});
  const [instance] = harnessed.editors.state.instances;
  assert.equal(instance.state.draft.proactive_intensity_preset, 'live');
  assert.equal(instance.state.base.proactive_intensity_preset, '高频',
    'the saved alias stays visible as a migration diff');
  assert.match(fieldRow(harnessed.painted['proactive.contact'], 'proactive_intensity_preset'),
    /value="live"[^>]*selected/);

  const saving = instance.save(false);
  await drains();
  assert.deepEqual(plain(harnessed.requests[1].body.changes), {proactive_intensity_preset: 'live'});
  harnessed.requests[1].resolve({result: {operation_id: 'contact-tier', replayed: false},
    status: newSection('proactive.contact', {proactive_intensity_preset: 'live'}, TOKEN('b'))});
  await saving;
  await drains();
});

/* 画出来的某一行控件：从它的行标记切到下一条行标记。 */
function fieldRow(markup, name) {
  const start = markup.indexOf(`data-wb-row="${name}"`);
  assert.notEqual(start, -1, `字段 ${name} 没有渲染出来`);
  const end = markup.indexOf('data-wb-row="', start + 10);
  return markup.slice(start, end === -1 ? markup.length : end);
}

test('inherited contact values render once and show the old value only after a draft override', async () => {
  const harnessed = harness(['proactive.contact']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  const response = payload('proactive.contact', {
    screen_glance_daily_limit: 1, screen_glance_goodnight_enabled: false,
  }, TOKEN('a'));
  const section = response.sections['proactive.contact'];
  for (const name of ['screen_glance_daily_limit', 'screen_glance_goodnight_enabled']) {
    section.provenance[name] = {layer: 'installation', explicit: false, revision: 1};
  }
  harnessed.requests[0].resolve(response);
  await drains();

  const [instance] = harnessed.editors.state.instances;
  let row = fieldRow(harnessed.painted['proactive.contact'], 'screen_glance_daily_limit');
  assert.doesNotMatch(row, /wb-ghost|wb-live/);
  assert.match(row, /data-wb-field="screen_glance_daily_limit"[^>]*value="1"/);
  row = fieldRow(harnessed.painted['proactive.contact'], 'screen_glance_goodnight_enabled');
  assert.doesNotMatch(row, /wb-ghost|wb-live/);
  assert.match(row, /已关闭/);

  instance.state.draft.screen_glance_daily_limit = 2;
  instance.attach();
  row = fieldRow(harnessed.painted['proactive.contact'], 'screen_glance_daily_limit');
  assert.match(row, /class="wb-ghost"[^>]*>继承值 1/);
  assert.doesNotMatch(row, /wb-live/);
});

test('a legacy pending revision conflict restores its draft without replaying the rejected write', async () => {
  const key = `companion-workbench.v1:${JSON.stringify(['inst', 'admin', 'installation', 'proactive.contact'])}`;
  const store = new Map([[key, JSON.stringify({operation_key: 'old-conflict', edit_token: TOKEN('b'),
    changes: {screen_glance_daily_limit: 2}, reset_to_inherit: [],
    pending_reason: '配置已经更新。草稿已保留，读取新版本后再核对。'})]]);
  const harnessed = harness(['proactive.contact'], {}, '', {store});
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('proactive.contact'));
  await drains();

  const [instance] = harnessed.editors.state.instances;
  assert.equal(instance.state.pending, null);
  assert.equal(instance.state.draft.screen_glance_daily_limit, 2);
  assert.equal(store.size, 0, 'a known refusal is removed from pending storage');
  assert.match(instance.state.notice, /服务端没有写入；草稿已恢复/);
  await new Promise(resolve => setTimeout(resolve, 450));
  assert.equal(harnessed.requests.length, 1, 'a confirmed refusal must not be replayed');

  const saving = instance.save(false);
  await drains();
  assert.equal(harnessed.requests[1].body.operation_key, 'config-key', 'a manual save uses a new operation');
  assert.deepEqual(plain(harnessed.requests[1].body.changes), {screen_glance_daily_limit: 2});
  harnessed.requests[1].resolve({result: {operation_id: 'new-save', replayed: false},
    status: payload('proactive.contact', {screen_glance_daily_limit: 2}, TOKEN('c'))});
  await saving;
  await drains();
});

test('the contact section renders all 173 editable fields exactly once', async () => {
  const harnessed = await contactLoaded();
  const painted = harnessed.painted['proactive.contact'];
  assert.ok(painted, '主动联系偏好必须画进自己的挂载点');
  const container = harnessed.editors.state.instances[0].state.container;
  const rendered = plain(container.groups.flatMap(group => group.fields));
  const expected = plain(Object.keys(CONTACT_FIELDS));
  assert.equal(rendered.length, 173, '173 个可编辑字段一个都不能少');
  assert.equal(new Set(rendered).size, 173, '同一个字段不能出现在两段里');
  assert.deepEqual(rendered.slice().sort(), expected.slice().sort());
  assert.deepEqual(plain(container.order).slice().sort(), expected.slice().sort());
  /* 24 段全部画出来，段落 id 是测试能定位依赖说明的锚点。 */
  for (const id of CONTACT_SECTIONS) {
    assert.match(painted, new RegExp(`data-wb-group="${id}"`), id);
  }
  assert.equal(container.fields.contact_followup_after_minutes.minimum, 10);
  assert.equal(container.fields.contact_followup_after_minutes.maximum, 1440);
  assert.equal(container.fields.contact_followup_cooldown_minutes.minimum, 30);
  assert.equal(container.fields.contact_followup_cooldown_minutes.maximum, 2880);
  /* 公开编辑器只写安装级全局值；角色与关系的用户细调在各自页面维护。 */
  assert.deepEqual(plain(container.layers), ['installation']);
  assert.deepEqual(plain(harnessed.editors.state.instances[0].state.status.fields.interval_seconds.editable_layers),
    CONTACT_LAYERS);
  assert.ok(plain(harnessed.editors.state.instances[0].state.status.fields.max_contacts_24h.editable_layers).includes('window'));
  assert.equal(Object.hasOwn(container.fields, 'max_attempts_24h'), false);
  assert.equal(Object.hasOwn(container.fields, 'proactive_persona_judge_daily_limit'), false);
  assert.equal(Object.hasOwn(container.fields, 'max_relationship_contacts_24h'), false);
  assert.equal(Object.hasOwn(container.fields, 'max_relationship_attempts_24h'), false);
  assert.ok(!plain(harnessed.editors.state.instances[0].state.status.fields.max_contacts_24h.editable_layers)
    .includes('relationship'));
  assert.doesNotMatch(painted, /data-layer="relationship"|data-layer="window"/);
  assert.doesNotMatch(painted, /data-wb-row="important_dates"/,
    '生日与纪念日自动读取资料，不再渲染手动 JSON 清单');
});

test('the contact card clearly identifies global defaults and personal overrides', async () => {
  const harnessed = await contactLoaded();
  const painted = harnessed.painted['proactive.contact'];
  assert.match(painted, /<span class="wb-scope">全局默认设置/);
  assert.match(painted, /全局默认；个人偏好在私聊用户页调整/);
  assert.match(painted, /每个私聊窗口每日最多实际联系/);
  assert.doesNotMatch(painted, /max_attempts_24h|每日最多主动模型调用|proactive_persona_judge_daily_limit/);
  assert.doesNotMatch(painted, /关系级 24 小时上限|max_relationship_/);
  assert.doesNotMatch(painted, /默认写在窗口层|本会话可覆盖|当前生效值来自窗口/);
  /* 公共入口说明全局暂停，个人暂停仍由私聊用户设置控制。 */
  assert.match(painted, /全局暂停与个人暂停取较晚的结束时间/);
  assert.doesNotMatch(painted, /默认写在窗口层|同步管理当前窗口的主动联系授权/);
  /* 内部名词不能漏到界面上。 */
  assert.doesNotMatch(painted, /scope_key|binding_revision|operation_id|provider_id/);
});

test('the contact audit blocks malformed shapes before any request', async () => {
  const harnessed = await contactLoaded();
  const [instance] = harnessed.editors.state.instances;
  const posts = () => harnessed.requests.filter(request => request.method === 'POST').length;
  const before = posts();
  const cases = [
    ['quiet_start', '25:00', /二十四小时制/],
    ['greeting_morning_window', '20:10-07:45', /必须当天内结束/],
    ['quiet_timezone', 'Not/A-Zone', /IANA 时区/],
    ['pause_until', '2026-09-28T12:00:00', /要带时区/],
    ['proactive_hour_activity_curve', '1,1,1', /写满 24 个/],
    ['dedup_policies', 'semantic,nope', /只能填允许的名字/],
    ['news_hot_sources', 'weibo,weibo', /不能重复/],
    ['proactive_persona_judge_exempt_purposes', 'nope', /只能填允许的名字/],
    ['date_checkin_time', '25:00', /二十四小时制/],
    ['news_source_kind', 'bilibili', /十进制 UID/],
  ];
  for (const [name, value, expected] of cases) {
    instance.state.draft[name] = value;
    await instance.save(false);
    assert.equal(posts(), before, `${name}=${value} 不该发请求`);
    assert.equal(instance.state.bad, true, name);
    assert.match(instance.state.notice, expected, name);
    instance.state.draft[name] = instance.state.base[name];
  }
  /* 合法的组合应当放行：注册表默认值本身就必须通过审计。 */
  instance.state.draft.quiet_start = '22:30';
  await instance.save(false);
  assert.equal(posts(), before + 1);
});

test('window daily quotas are editable and relationship-level quota fields are absent', async () => {
  const harnessed = await contactLoaded();
  const [instance] = harnessed.editors.state.instances;
  assert.equal(instance.state.status.settings.max_relationship_contacts_24h, undefined);
  assert.equal(instance.state.status.settings.max_relationship_attempts_24h, undefined);
  assert.ok(instance.state.status.fields.max_contacts_24h.editable_layers.includes('window'));
  assert.equal(Object.hasOwn(instance.state.status.fields, 'max_attempts_24h'), false);
});

test('the role dossier maps profile fields onto the purpose-built controls', () => {
  const {window} = harness(['role_profile']);
  const profile = window.CompanionConfigEditors.spec.role_profile;
  /* 值仍是 TEXT；inputs/chipOptions 只声明控件形态，漏传时内核退回普通文本框。 */
  /* 一行放得下的事实字段声明为单行输入；没有声明的保持多行文本框。 */
  assert.deepEqual(plain(profile.inputs),
    {birthday: 'date', gender: 'chips', species: 'chips', first_person: 'chips',
      voice_examples: 'quotes', signature_phrases: 'tags', hobbies: 'tags',
      name: 'line', avatar_data_url: 'line', age: 'line', occupation: 'line', appearance_points: 'line',
      hair: 'line', eyes: 'line', clothing: 'line', image_identity_hint: 'line',
      world_name: 'line', era: 'line', tone: 'line',
      main_companion_subject: 'catalog-select',
      expression_private_source_subject_ids: 'identity-checklist',
      expression_private_application_subject_ids: 'identity-checklist'});
  assert.equal(profile.chipOptions.gender.mode, 'single');
  assert.deepEqual(plain(profile.chipOptions.gender.options), ['女', '男', '无性别']);
  assert.deepEqual(plain(profile.chipOptions.species.options),
    ['人类', '精灵', '仿生人', 'AI', '兽人']);
  assert.deepEqual(plain(profile.segmentedChoices), [
    'expression_private_source_mode', 'expression_private_application_mode']);
  assert.deepEqual(plain(profile.choices.expression_private_source_mode),
    {owner: '主要陪伴对象', selected: '指定对象', all: '所有获准私聊'});
  assert.equal(profile.sections.at(-1).id, 'expression_learning');
  const relationship = window.CompanionConfigEditors.spec.relationship_profile;
  assert.equal(relationship.inputs.user_birthday, 'date');
  assert.equal(relationship.inputs.user_gender, 'chips');
  assert.equal(relationship.inputs.default_nickname, 'line');
  assert.deepEqual(plain(relationship.chipOptions.user_gender.options), ['女', '男', '未说明']);
});

test('quote rows carry the marker used by removal and renumbering', () => {
  /* The rendered row and the node added at runtime must both carry
     data-wb-quote; removeQuote/renumberQuotes select exactly that marker. */
  assert.match(workbench, /class="wb-quote" data-wb-quote/);
  assert.match(workbench, /node\.dataset\.wbQuote = ""/);
  assert.match(workbench, /querySelectorAll\("\[data-wb-quote\]"\),?/);
});

test('role profile controls render as date, chips, quotes, tags and a segmented mode', async () => {
  const harnessed = harness(['role_profile']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  assert.equal(harnessed.requests.length, 1);
  harnessed.requests[0].resolve(payload('role_profile'));
  await drains();
  const html = harnessed.painted['role_profile'];
  assert.match(html, /data-wb-date-group="birthday"/);
  assert.match(html, /data-wb-date-text="birthday"/);
  assert.match(html, /data-wb-date-role="picker"[^>]*>/);
  /* “4月17日”没有年份：选择器落在占位年份上，「不确定年份」默认勾选。 */
  assert.match(html, /value="2000-04-17"/);
  assert.match(html, /data-wb-date-role="noyear"[^>]* checked/);
  assert.match(html, /data-wb-date-role="month"/);
  assert.match(html, /data-wb-date-role="leap"/);
  assert.match(html, /class="wb-chip-opt is-on" data-wb-chip="gender" data-wb-chip-value="女"/);
  assert.match(html, /class="wb-chip-opt" data-wb-chip="gender" data-wb-chip-value="男"/);
  assert.match(html, /data-wb-quotes="voice_examples"/);
  assert.match(html, /class="wb-quote" data-wb-quote(?:=""|>)/);
  assert.match(html, /今天的风有点像海边的。/);
  assert.match(html, /data-wb-tagset="signature_phrases"/);
  assert.match(html, /data-wb-tag-remove="signature_phrases" data-wb-tag-index="1"/);
  assert.match(html, /type="hidden" data-wb-field="hobbies"/);
  // 平台称呼与世界观适配已下线，页面不再渲染这两组控件。
  assert.doesNotMatch(html, /wb-world_adaptation_mode/);
  assert.doesNotMatch(html, /wb-platform_names/);
  assert.match(html, /data-wb-section-panel="expression_learning"/);
  assert.match(html, /data-wb-field="main_companion_subject"/);
  assert.match(html, /name="wb-expression_private_source_mode" value="owner"/);
  assert.match(html, /wb-identity-picker/);
});

test('lunar birthday values retain calendar, year and leap-month fields', async () => {
  for (const [birthday, expectedMonth, expectedDay, leap] of [
    ['2025年农历闰六月初五', '6', '5', true],
    ['2025年4月17日 · 农历', '4', '17', false],
  ]) {
    const harnessed = harness(['role_profile']);
    const [instance] = await loaded(harnessed, ['role_profile']);
    instance.state.draft.birthday = birthday;
    instance.state.status.settings.birthday = birthday;
    harnessed.editors.paint();
    const html = harnessed.painted.role_profile;
    assert.match(html, /data-wb-date-role="lunar" checked/);
    assert.match(html, /data-wb-date-role="year" value="2025"/);
    assert.match(html, new RegExp('<option value="' + expectedMonth + '" selected>'));
    assert.match(html, new RegExp('<option value="' + expectedDay + '" selected>'));
    assert.equal(/data-wb-date-role="leap" checked/.test(html), leap);
  }
});

test('lunar birthdays parse legacy values and validate real leap-month dates', () => {
  const parsed = birthdayHelpers.parseBirthday('生日是：农历二〇二五年闰六月初五');
  assert.deepEqual(plain(parsed), {year: '2025', month: '6', day: '5', lunar: true, leap: true});
  assert.equal(birthdayHelpers.birthdayText(parsed, true, false), '2025年农历闰六月初五');
  const legacy = birthdayHelpers.parseBirthday('2025年4月17日 · 农历');
  assert.equal(legacy.lunar, true);
  assert.equal(legacy.month, '4');
  assert.equal(legacy.day, '17');
  const commonLunar = birthdayHelpers.parseBirthday('阴历五月廿三');
  assert.deepEqual(plain(commonLunar), {year: '', month: '5', day: '23', lunar: true, leap: false});
  assert.deepEqual(plain(birthdayHelpers.parseBirthday('2025農曆閏六月初五號')),
    {year: '2025', month: '6', day: '5', lunar: true, leap: true});
  assert.deepEqual(plain(birthdayHelpers.parseBirthday('农历4/17/2025')),
    {year: '2025', month: '4', day: '17', lunar: true, leap: false});
  assert.deepEqual(plain(birthdayHelpers.parseBirthday('公历2024年2月29日')),
    {year: '2024', month: '2', day: '29', lunar: false, leap: false});
  assert.deepEqual(plain(birthdayHelpers.parseBirthday('五月初八（农历）')),
    {year: '', month: '5', day: '8', lunar: true, leap: false});
  assert.equal(birthdayHelpers.birthdayPartsError(birthdayHelpers.parseBirthday('公历2025年2月29日')),
    '这个公历日期不存在，请核对月份和日期。');
  assert.equal(birthdayHelpers.birthdayPartsError(birthdayHelpers.parseBirthday('农历十三月初八')),
    '请核对生日的月份和日期。');
  assert.match(birthdayHelpers.birthdayPartsError(
    birthdayHelpers.parseBirthday('2025年农历闰六月三十')), /对应的农历日期/);
  assert.equal(birthdayHelpers.parseBirthday('每年冬至'), null);
  assert.equal(birthdayHelpers.lunarDateIndex(2025).has('6:1:5'), true);
  assert.equal(birthdayHelpers.lunarDateIndex(2025).has('6:1:30'), false);
  assert.equal(birthdayHelpers.lunarDateIndex(2026).has('6:1:5'), false);
});

test('an invalid but recognizable stored birthday is surfaced on the editor', async () => {
  const harnessed = harness(['role_profile']);
  const [instance] = await loaded(harnessed, ['role_profile']);
  instance.state.draft.birthday = '公历2025年2月29日';
  instance.state.status.settings.birthday = instance.state.draft.birthday;
  harnessed.editors.paint();
  assert.match(harnessed.painted.role_profile, /data-wb-date-invalid="true"/);
  assert.match(harnessed.painted.role_profile, /这个公历日期不存在/);
});

test('role learning scope choices use only private identities bound to the active role', async () => {
  const harnessed = harness(['role_profile'], {}, '', {people: [
    {subject_id: 'owner-a', display_name: '主要对象', role_id: 'role-a', bot_id: 'bot-a', private_target: true},
    {subject_id: 'group-a', display_name: '群成员', role_id: 'role-a', bot_id: 'bot-a', private_target: false},
    {subject_id: 'other-role', display_name: '其他角色', role_id: 'role-b', bot_id: 'bot-a', private_target: true},
    {subject_id: 'other-bot', display_name: '其他机器人', role_id: 'role-a', bot_id: 'bot-b', private_target: true},
  ]});
  const [instance] = await loaded(harnessed, ['role_profile']);
  await drains();
  instance.state.draft.main_companion_subject = 'owner-a';
  instance.state.draft.expression_private_source_subject_ids = '["owner-a"]';
  harnessed.editors.paint();
  const html = harnessed.painted.role_profile;
  assert.match(html, /<option value="owner-a"[^>]*>主要对象/);
  assert.match(html, /data-wb-identity-id="owner-a" checked/);
  assert.doesNotMatch(html, /data-wb-identity-id="group-a"/);
  assert.doesNotMatch(html, /data-wb-identity-id="other-role"/);
  assert.doesNotMatch(html, /data-wb-identity-id="other-bot"/);
});

test('journal presentation swaps chapter panels without repainting or losing the draft', async () => {
  const harnessed = harness(['role_profile'], {role_profile: {
    presentation: 'journal', sectionTabPrefix: 'self-chapter-', initialSection: 'voice',
  }});
  const [instance] = await loaded(harnessed, ['role_profile']);
  const before = harnessed.painted.role_profile;
  assert.match(before, /class="wb-card wb-journal"/);
  assert.match(before, /id="self-chapter-panel-voice"/);
  assert.match(before, /aria-labelledby="self-chapter-tab-voice"[^>]*data-wb-section-panel="voice"/);
  assert.doesNotMatch(before, /class="wb-rail/);
  assert.doesNotMatch(before, /class="wb-help"/);
  instance.state.draft.name = '临时草稿';
  harnessed.editors.selectSection('identity');
  assert.equal(instance.state.activeGroupId, 'identity');
  assert.equal(instance.state.draft.name, '临时草稿');
  assert.equal(harnessed.painted.role_profile, before, 'changing chapters keeps the mounted controls intact');
});

test('a removed gender preset stays editable as a saved custom value', async () => {
  const harnessed = harness(['role_profile']);
  harnessed.editors.restore();
  harnessed.editors.paint();
  harnessed.editors.read();
  await drains();
  harnessed.requests[0].resolve(payload('role_profile', {gender: '非二元'}));
  await drains();
  const html = harnessed.painted.role_profile;
  assert.doesNotMatch(html, /data-wb-chip="gender" data-wb-chip-value="非二元"/);
  assert.match(html, /data-wb-field="gender" value="非二元"/);
});

test('the self journal reflows to its container width and wraps long controls', () => {
  assert.match(selfStyle, /\.self-page\{[^}]*margin-inline:auto/);
  assert.match(selfStyle, /container:self-journal \/ inline-size/);
  assert.match(selfStyle, /@container self-journal \(max-width:650px\)/);
  assert.match(selfStyle, /\.self-page \.wb-journal \.wb-fields\{display:grid;grid-template-columns:repeat\(2,minmax\(0,1fr\)/);
  assert.match(selfStyle, /@container self-journal \(max-width:650px\)\{[\s\S]*?\.self-page \.wb-journal \.wb-fields,[\s\S]*?grid-template-columns:minmax\(0,1fr\)/);
  assert.match(selfStyle, /overflow-wrap:anywhere/);
  assert.match(selfStyle, /\.section-chips\{[^}]*overflow-x:auto/s);
});

test('the live self profile uses journal panels and its chapter navigation stays connected', () => {
  assert.match(app, /roleProfileConfigMount=mountConfigEditor\('roleProfileConfigWorkspace',\['role_profile'\],\{\s*role_profile:\{presentation:'journal',sectionTabPrefix:'self-chapter-',initialSection:selfProfileSection,navigationLimit:9\}/);
  assert.match(app, /function selectSelfProfileSection\(section\)\{[\s\S]*?roleProfileConfigMount\?\.selectSection\?\.\(section\)/);
  assert.match(app, /id="self-chapter-tab-\$\{id\}" aria-controls="self-chapter-panel-\$\{id\}"/);
  assert.match(app, /<button type="button" class="section-chip" data-role-profile-organize-jump><span class="section-chip-no">✦<\/span>通过已有人格整理<\/button>/);
  assert.match(app, /<h3>通过已有人格整理<\/h3>/);
  assert.match(app, /data-role-profile-draft-open="role_profile"/);
  assert.match(app, /从当前人格整理角色档案/);
  assert.match(app, /data-profile-avatar-file/);
  assert.match(selfStyle, /data-wb-row="avatar_data_url"/);
  assert.match(workbench, /lunarDateIndex/);
  assert.match(workbench, /这个年份没有对应的农历日期/);
  assert.doesNotMatch(app, /selfwriteCanvas|data-selfwrite-source|角色自述/);
});
