"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const PAGE_DIR = path.join(__dirname, "..", "pages", "陪伴小屋");
const STORAGE_JS = fs.readFileSync(path.join(PAGE_DIR, "storage-page.js"), "utf8");
const TUNING_JS = fs.readFileSync(path.join(PAGE_DIR, "tuning-page.js"), "utf8");
const SEARCH_JS = fs.readFileSync(path.join(PAGE_DIR, "search-engine.js"), "utf8");

function fakeButton(attrs = {}) {
  const map = new Map(Object.entries(attrs));
  return {
    hasAttribute: name => map.has(name),
    getAttribute: name => (map.has(name) ? map.get(name) : null),
    setAttribute(name, value) { map.set(name, String(value)); },
    closest(selector) { return selector === "button" ? this : null; },
  };
}

function mountStorage({canFullVacuum = true, compactError = null, healthError = null} = {}) {
  const calls = [];
  let healthState = {
    status: "compact_recommended",
    telemetry_ttl_days: 14,
    total_sqlite_bytes: 882 * 1024 * 1024,
    total_reclaimable_bytes: 120 * 1024 * 1024,
    max_fragmentation_ratio: 0.24,
    disk_free_bytes: canFullVacuum ? 10 * 1024 ** 3 : 500 * 1024 ** 2,
    vacuum_required_bytes: 2 * 1024 ** 3,
    can_full_vacuum: canFullVacuum,
    databases: {
      main: {exists: true, db_bytes: 860 * 1024 * 1024, wal_bytes: 22 * 1024 * 1024, total_bytes: 882 * 1024 * 1024},
      semantic_catalog: {exists: true, db_bytes: 8 * 1024 * 1024, wal_bytes: 0, total_bytes: 8 * 1024 * 1024},
    },
    opportunities: {
      un_deduped_native_turns: 420,
      un_deduped_native_observations: 1200,
      un_deduped_core_observations: 50,
      cold_core_observations: 80,
      cold_laya_payloads: 12,
      inline_conversation_images: 5,
      inline_reaction_blobs: 3,
      inline_native_image_blobs: 0,
    },
    recommendations: [
      {code: "dedup_native_storage", label: "存在重复存储的人格提示词或会话上下文，可无损去重缩减数据库体积"},
    ],
  };

  const window = {
    AstrBotPluginPage: {
      async apiGet(route) {
        calls.push(["GET", route]);
        if (route === "storage/health") {
          if (healthError instanceof Error) throw healthError;
          if (healthError) return healthError;
          return {status: "ok", data: healthState};
        }
        return {
          status: "ok",
          data: {
            active_root: "D:\\Data",
            selected_path: "",
            desired_root: "D:\\Data",
            selected_backup_path: "",
            backup_root: "D:\\Data\\backups",
            restart_required: false,
            policy: {retention_count: 10, retention_days: 90},
            inventory: {
              total_bytes: healthState.total_sqlite_bytes,
              free_bytes: healthState.disk_free_bytes,
              backup_free_bytes: healthState.disk_free_bytes,
              categories: {
                database: {bytes: healthState.total_sqlite_bytes, files: 3},
                backups: {bytes: 0, files: 0},
                other: {bytes: 0, files: 0},
              },
            },
            cleanup_preview: {snapshot_count: 0, remove_count: 0, remove_bytes: 0},
          },
        };
      },
      async apiPost(route, body) {
        calls.push(["POST", route, body]);
        if (route === "storage/compact") {
          if (compactError instanceof Error) throw compactError;
          if (compactError) return compactError;
          const after = {
            ...healthState,
            status: "healthy",
            total_sqlite_bytes: 210 * 1024 * 1024,
            total_reclaimable_bytes: 0,
            max_fragmentation_ratio: 0,
            opportunities: {
              un_deduped_native_turns: 0, un_deduped_native_observations: 0, un_deduped_core_observations: 0,
              cold_core_observations: 0, cold_laya_payloads: 0,
              inline_conversation_images: 0, inline_reaction_blobs: 0, inline_native_image_blobs: 0,
            },
            recommendations: [],
          };
          const before = healthState;
          healthState = after;
          return {
            status: "ok",
            data: {
              before,
              after,
              reclaimed_sqlite_bytes: before.total_sqlite_bytes - after.total_sqlite_bytes,
              elapsed_ms: 1400,
              steps: {
                dedup: {turn_contexts_migrated: 420, obs_contexts_migrated: 1200, role_blobs_migrated: 30, core_contexts_migrated: 50},
                archive: {core_observations_archived: 80, laya_payloads_archived: 12},
                media_offload: {conversation_images: {migrated: 5}},
                vacuum: {main: {vacuumed: Boolean(body.vacuum), freed_pages: body.vacuum ? 0 : 128}},
              },
            },
          };
        }
        return {status: "ok", data: {}};
      },
    },
  };
  vm.runInNewContext(STORAGE_JS, {window, document: {}, setTimeout, clearTimeout, Promise, Number, String, Object, Array, Error, Math});
  const handlers = {};
  const root = {isConnected: true, innerHTML: "", addEventListener(name, fn) { handlers[name] = fn; }};
  window.CompanionStoragePage.mount(root);
  return {window, root, handlers, calls};
}

function mountTuning({
  clamped = false,
  initialProfile = "auto",
  getError = null,
  postError = null,
  delayGetMs = 0,
  querySelectorImpl = null,
  useAbortController = true,
} = {}) {
  const calls = [];
  const autoBaseline = {
    cache_size_mb: 64, sidecar_cache_size_mb: 16, mmap_size_mb: 256, sidecar_mmap_size_mb: 128,
    reader_pool_size: 4, journal_size_limit_mb: 64, synchronous: "NORMAL",
    write_batch_window_ms: 50, background_slots: 3, tts_max_concurrency: 3, telemetry_ttl_days: 14,
  };
  const baselines = {
    auto: autoBaseline,
    eco: {
      cache_size_mb: 8, sidecar_cache_size_mb: 4, mmap_size_mb: 0, sidecar_mmap_size_mb: 0,
      reader_pool_size: 2, journal_size_limit_mb: 16, synchronous: "NORMAL",
      write_batch_window_ms: 80, background_slots: 2, tts_max_concurrency: 1, telemetry_ttl_days: 7,
    },
    balanced: {
      cache_size_mb: 32, sidecar_cache_size_mb: 8, mmap_size_mb: 128, sidecar_mmap_size_mb: 32,
      reader_pool_size: 4, journal_size_limit_mb: 32, synchronous: "NORMAL",
      write_batch_window_ms: 50, background_slots: 3, tts_max_concurrency: 3, telemetry_ttl_days: 14,
    },
    turbo: {
      cache_size_mb: 128, sidecar_cache_size_mb: 32, mmap_size_mb: 512, sidecar_mmap_size_mb: 256,
      reader_pool_size: 6, journal_size_limit_mb: 64, synchronous: "NORMAL",
      write_batch_window_ms: 30, background_slots: 5, tts_max_concurrency: 4, telemetry_ttl_days: 30,
    },
    compat: {
      cache_size_mb: 16, sidecar_cache_size_mb: 8, mmap_size_mb: 0, sidecar_mmap_size_mb: 0,
      reader_pool_size: 2, journal_size_limit_mb: 16, synchronous: "FULL",
      write_batch_window_ms: 100, background_slots: 2, tts_max_concurrency: 2, telemetry_ttl_days: 14,
    },
  };
  const activeBase = baselines[initialProfile] || autoBaseline;
  let serverPayload = {
    profile: initialProfile,
    profile_label: initialProfile === "compat" ? "兼容稳妥" : "自动推荐",
    resolved_profile: initialProfile === "auto" ? "balanced" : initialProfile,
    resolved_profile_label: initialProfile === "compat" ? "兼容稳妥" : "平衡推荐",
    overrides: {},
    override_count: 0,
    baseline: {...activeBase},
    baselines,
    values: {...activeBase},
    clamped_fields: clamped ? [{field: "mmap_size_mb", label: "数据库内存映射", requested: 512, effective: 256, reason: "按可用内存 25% 安全限幅至 256 MB。"}] : [],
    hardware: {
      effective_memory_bytes: 8 * 1024 ** 3,
      effective_memory_gib: 8.0,
      cgroup_limited: false,
      cpu_count: 8,
      is_32bit: false,
      recommended_profile: "balanced",
      recommended_profile_label: "平衡推荐",
      recommendation_reason: "识别到可用内存约 8.0 GB、8 核，推荐「平衡推荐」兼顾速度与内存占用。",
    },
    profiles: [
      {id: "auto", label: "自动推荐", badge: "默认", summary: "自动匹配", recommended_for: "大多数用户", preview_cache: "自适应", preview_concurrency: "自适应", preview_disk: "自动"},
      {id: "eco", label: "省内存轻量化", badge: "低配友好", summary: "低内存", recommended_for: "1–2 GB", preview_cache: "8 MB", preview_concurrency: "2", preview_disk: "常规"},
      {id: "balanced", label: "平衡推荐", badge: "日常主力", summary: "折中", recommended_for: "4–8 GB", preview_cache: "32 MB", preview_concurrency: "4", preview_disk: "常规"},
      {id: "turbo", label: "高性能流畅", badge: "极速响应", summary: "大内存", recommended_for: "16 GB+", preview_cache: "128 MB", preview_concurrency: "6–8", preview_disk: "常规"},
      {id: "compat", label: "兼容稳妥", badge: "护盘防卡", summary: "机械硬盘", recommended_for: "HDD/NAS", preview_cache: "16 MB", preview_concurrency: "2", preview_disk: "FULL"},
    ],
    groups: [
      {id: "database", title: "数据库读写", description: "控制缓存与映射"},
      {id: "concurrency", title: "任务与并发", description: "控制并发"},
      {id: "maintenance", title: "归档与清理", description: "控制保留天数"},
    ],
    fields: [
      {key: "cache_size_mb", group: "database", label: "主库内存缓存", kind: "int", unit: "MB", minimum: 4, maximum: 512, step: 4, hot_reload: true, apply_mode: "立即热生效", effect_hint: "调大更快"},
      {key: "synchronous", group: "database", label: "写盘安全策略", kind: "enum", options: [{value: "NORMAL", label: "常规"}, {value: "FULL", label: "最稳"}], hot_reload: true, apply_mode: "立即热生效", effect_hint: "最稳防断电"},
      {key: "telemetry_ttl_days", group: "maintenance", label: "运行明细保留天数", kind: "int", unit: "天", minimum: 3, maximum: 365, step: 1, hot_reload: true, apply_mode: "立即热生效", effect_hint: "冷归档"},
    ],
    lite_page: {saved_enabled: false, effective_enabled: false, reload_required: false},
    runtime: {reader_pool_size: 4},
  };

  const window = {
    AstrBotPluginPage: {
      async apiGet(route) {
        calls.push(["GET", route]);
        if (delayGetMs > 0) await new Promise(resolve => setTimeout(resolve, delayGetMs));
        if (getError instanceof Error) throw getError;
        if (getError) return getError;
        return {status: "ok", data: serverPayload};
      },
      async apiPost(route, body) {
        calls.push(["POST", route, body]);
        if (postError instanceof Error) throw postError;
        if (postError) return postError;
        if (body.action === "lite_page") {
          serverPayload = {
            ...serverPayload,
            lite_page: {saved_enabled: body.lite_page_enabled, effective_enabled: false, reload_required: Boolean(body.lite_page_enabled)},
          };
          return {status: "ok", data: serverPayload};
        }
        const nextProfile = body.profile || serverPayload.profile;
        serverPayload = {
          ...serverPayload,
          profile: nextProfile,
          baseline: {...(baselines[nextProfile] || autoBaseline)},
          overrides: body.overrides || {},
          override_count: Object.keys(body.overrides || {}).length,
        };
        return {status: "ok", data: serverPayload};
      },
    },
  };
  const sandbox = {
    window, document: {}, setTimeout, clearTimeout, Promise, Number, String, Object, Array, Error, Math,
  };
  if (useAbortController && typeof AbortController === "function") {
    sandbox.AbortController = AbortController;
  }
  vm.runInNewContext(TUNING_JS, sandbox);

  const listeners = new Map();
  let html = "";
  let renderWrites = 0;
  const root = {
    isConnected: true,
    get innerHTML() { return html; },
    set innerHTML(val) { html = val; renderWrites += 1; },
    get renderWrites() { return renderWrites; },
    resetRenderWrites() { renderWrites = 0; },
    querySelector: selector => (querySelectorImpl ? querySelectorImpl(selector, html) : null),
    addEventListener(name, fn, opts) {
      if (!listeners.has(name)) listeners.set(name, new Set());
      const bucket = listeners.get(name);
      bucket.add(fn);
      if (opts && opts.signal && typeof opts.signal.addEventListener === "function") {
        opts.signal.addEventListener("abort", () => bucket.delete(fn), {once: true});
      }
    },
    removeEventListener(name, fn) {
      listeners.get(name)?.delete(fn);
    },
    listenerCount(name) {
      return listeners.get(name)?.size || 0;
    },
    dispatch(name, event) {
      for (const fn of [...(listeners.get(name) || [])]) fn(event);
    },
  };
  const handlers = {
    input: event => root.dispatch("input", event),
    change: event => root.dispatch("change", event),
    click: event => root.dispatch("click", event),
  };
  window.CompanionTuningPage.mount(root);
  return {window, root, handlers, calls};
}

test("storage page renders DB health card, runs lossless compaction, and confirms full VACUUM", async () => {
  const {root, handlers, calls} = mountStorage();
  await new Promise(resolve => setTimeout(resolve, 10));

  assert.deepEqual(calls[0], ["GET", "storage"]);
  assert.deepEqual(calls[1], ["GET", "storage/health"]);
  assert.match(root.innerHTML, /数据库体检与一键瘦身/);
  assert.match(root.innerHTML, /建议瘦身/);
  assert.match(root.innerHTML, /1670 条可优化/);
  assert.match(root.innerHTML, /href="#manage\/data\/tuning"/);

  // One-click lossless compaction
  handlers.click({target: fakeButton({"data-db-compact-normal": ""})});
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(calls.some(c => c[0] === "POST" && c[1] === "storage/compact" && c[2].vacuum === false), true);
  assert.match(root.innerHTML, /常规无损瘦身完成/);
  assert.match(root.innerHTML, /882\.0 MB → 瘦身后 210\.0 MB/);

  // Full VACUUM requires explicit confirmation
  const postsBefore = calls.filter(c => c[0] === "POST").length;
  handlers.click({target: fakeButton({"data-db-compact-vacuum-review": ""})});
  assert.match(root.innerHTML, /确认执行深度整理压缩（全量 VACUUM）/);
  assert.equal(calls.filter(c => c[0] === "POST").length, postsBefore);

  handlers.click({target: fakeButton({"data-db-compact-vacuum-confirm": ""})});
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(calls.some(c => c[0] === "POST" && c[1] === "storage/compact" && c[2].vacuum === true), true);
});

test("storage page blocks full VACUUM confirmation when disk space is insufficient", async () => {
  const {root, handlers} = mountStorage({canFullVacuum: false});
  await new Promise(resolve => setTimeout(resolve, 10));
  handlers.click({target: fakeButton({"data-db-compact-vacuum-review": ""})});
  assert.match(root.innerHTML, /低于安全要求/);
  assert.match(root.innerHTML, /data-db-compact-vacuum-confirm disabled/);
});

test("tuning page renders 5 profiles, sparse overrides, switch prompt, clamp notice, and lite toggle", async () => {
  const {window, root, handlers, calls} = mountTuning({clamped: true});
  await new Promise(resolve => setTimeout(resolve, 10));

  assert.deepEqual(calls[0], ["GET", "performance/tuning"]);
  assert.match(root.innerHTML, /本机硬件识别与性能策略/);
  assert.match(root.innerHTML, /防爆内存护栏已生效/);
  assert.match(root.innerHTML, /兼容稳妥/);
  assert.match(root.innerHTML, /展开高级性能微调/);

  // Open advanced drawer via focusField
  window.CompanionTuningPage.focusField("cache_size_mb");
  assert.match(root.innerHTML, /tuning-advanced-panel is-open/);

  // Override synchronous to FULL -> active badge shows 1 override
  handlers.click({target: fakeButton({"data-tuning-enum-key": "synchronous", "data-tuning-enum-val": "FULL"})});
  assert.match(root.innerHTML, /已微调 1 项/);

  // Switching profile while overrides exist presents the keep/clear prompt
  handlers.click({target: fakeButton({"data-tuning-profile": "compat"})});
  assert.match(root.innerHTML, /你当前保留了 1 项手动微调/);
  handlers.click({target: fakeButton({"data-tuning-switch-clear": ""})});
  assert.match(root.innerHTML, /当前生效：兼容稳妥/);

  // Save posts clean payload
  handlers.click({target: fakeButton({"data-tuning-save": ""})});
  await new Promise(resolve => setTimeout(resolve, 10));
  const saveCall = calls.find(c => c[0] === "POST" && c[2].action === "save");
  assert.ok(saveCall);
  assert.equal(saveCall[2].profile, "compat");
  assert.equal(JSON.stringify(saveCall[2].overrides), "{}");

  // Lite page toggle saves cleanly
  handlers.click({target: fakeButton({"data-tuning-lite-toggle": ""})});
  await new Promise(resolve => setTimeout(resolve, 10));
  const liteCall = calls.find(c => c[0] === "POST" && c[2].action === "lite_page");
  assert.ok(liteCall);
  assert.equal(liteCall[2].lite_page_enabled, true);
  assert.match(root.innerHTML, /请重载插件后生效/);
});

test("global search engine indexes DB compaction, 5-tier profiles, and tuning fields", () => {
  const win = {document: {}, location: {}, history: {}};
  vm.runInNewContext(SEARCH_JS, {
    window: win, document: win.document, globalThis: win,
    console, RegExp, Intl, Math, Date, JSON, Object, Array, Map, Set, Number, String, Boolean,
  });
  const entries = win.CompanionSearchRegistry.collectSettings();
  const engine = win.CompanionSearchEngine;
  const prepared = engine.prepare(entries);

  const queries = [
    ["数据库瘦身", "storage", "storage_database_health_compaction"],
    ["VACUUM", "storage", "storage_database_health_compaction"],
    ["性能调优", "tuning", "performance_tuning_profile"],
    ["机械硬盘", "tuning", "performance_tuning_profile"],
    ["主库内存缓存", "tuning", "cache_size_mb"],
    ["连库数", "tuning", "reader_pool_size"],
  ];

  for (const [q, expectedSubtab, expectedField] of queries) {
    const hits = engine.search(prepared, q, {limit: 5});
    assert.ok(hits.length > 0, `expected search results for ${q}`);
    assert.ok(
      hits.some(hit => hit.item.field === expectedField && hit.item.location?.subtab === expectedSubtab),
      `expected ${expectedField} (${expectedSubtab}) in top results for "${q}", got: ${hits.map(h => h.item.field).join(", ")}`
    );
  }
});

test("storage and tuning pages unpack rejected Error(machineCode) from host bridge into Chinese copy", async () => {
  // 1. storage-page: reject(new Error("storage_insufficient_disk_for_vacuum")) must show friendly Chinese message, not bare machine code.
  const storage = mountStorage({
    compactError: new Error("storage_insufficient_disk_for_vacuum"),
    healthError: new Error("staging_not_initialized"),
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.match(storage.root.innerHTML, /插件运行档尚未加载/);

  // Re-mount with healthy status and failing full vacuum via rejected Error(machineCode).
  const storageVac = mountStorage({compactError: new Error("storage_insufficient_disk_for_vacuum")});
  await new Promise(resolve => setTimeout(resolve, 10));
  storageVac.handlers.click({target: fakeButton({"data-db-compact-vacuum-review": ""})});
  storageVac.handlers.click({target: fakeButton({"data-db-compact-vacuum-confirm": ""})});
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.match(storageVac.root.innerHTML, /磁盘剩余空间不足（需至少 2\.2 倍主库空间）/);
  assert.match(storageVac.root.innerHTML, /（storage_insufficient_disk_for_vacuum）/);
  assert.doesNotMatch(storageVac.root.innerHTML, />storage_insufficient_disk_for_vacuum</);

  // 2. tuning-page: reject(new Error("tuning_request_invalid")) on save maps to Chinese range hint.
  const tuning = mountTuning({postError: new Error("tuning_request_invalid")});
  await new Promise(resolve => setTimeout(resolve, 10));
  tuning.handlers.click({target: fakeButton({"data-tuning-profile": "eco"})});
  tuning.handlers.click({target: fakeButton({"data-tuning-save": ""})});
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.match(tuning.root.innerHTML, /调优参数超出安全范围/);
  assert.match(tuning.root.innerHTML, /（tuning_request_invalid）/);

  // 3. tuning-page: reject(new Error("dashboard_identity_required")) on load maps to login hint.
  const tuningDenied = mountTuning({getError: new Error("dashboard_identity_required")});
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.match(tuningDenied.root.innerHTML, /请从 AstrBot 管理界面登录后查看性能调优/);
});

test("tuning page patches range drag in-place, uses backend auto baseline, handles async focusField, and cleans up listeners", async () => {
  const numberEl = {value: "64"};
  const badgeEl = {textContent: ""};
  const statusEl = {textContent: ""};
  const saveBtnEl = {disabled: true};
  const fieldClasses = new Set();
  const barClasses = new Set();
  const fieldEl = {
    classList: {toggle(name, force) { force ? fieldClasses.add(name) : fieldClasses.delete(name); }},
    scrolled: 0,
    scrollIntoView() { this.scrolled += 1; },
  };
  const barEl = {
    classList: {toggle(name, force) { force ? barClasses.add(name) : barClasses.delete(name); }},
  };
  const queriedSelectors = [];
  const queryMap = {
    '[data-tuning-number="cache_size_mb"]': numberEl,
    '[data-tuning-field="cache_size_mb"]': fieldEl,
    "[data-tuning-active-badge]": badgeEl,
    "[data-tuning-actions-bar]": barEl,
    "[data-tuning-actions-status]": statusEl,
    "[data-tuning-save]": saveBtnEl,
  };

  // Start on compat with delayed GET to test async focusField race + switching back to auto.
  const {window, root, handlers} = mountTuning({
    initialProfile: "compat",
    delayGetMs: 15,
    querySelectorImpl(selector) {
      queriedSelectors.push(selector);
      return queryMap[selector] || null;
    },
  });

  // Malicious selector injection must be rejected before querySelector is ever called.
  window.CompanionTuningPage.focusField('cache_size_mb"], .evil[a="');
  assert.equal(queriedSelectors.some(s => s.includes(".evil")), false);

  // Valid focusField called while GET is still pending must take effect after load() finishes.
  window.CompanionTuningPage.focusField("cache_size_mb");
  await new Promise(resolve => setTimeout(resolve, 35));
  assert.match(root.innerHTML, /tuning-advanced-panel is-open/);
  assert.match(root.innerHTML, /is-highlighted" data-tuning-field="cache_size_mb"/);
  assert.equal(fieldEl.scrolled, 1);

  // Switch from compat back to auto -> must use backend baselines.auto (cache_size_mb=64, NOT balanced=32).
  handlers.click({target: fakeButton({"data-tuning-profile": "auto"})});
  assert.match(root.innerHTML, /档位默认 64 MB/);
  assert.match(root.innerHTML, /当前生效：自动推荐（平衡推荐）/);

  // Range drag (`input` event) must update paired number input & dirty indicators IN PLACE with ZERO innerHTML rebuilds.
  root.resetRenderWrites();
  handlers.input({
    target: {
      type: "range",
      value: "128",
      getAttribute: name => (name === "data-tuning-input" ? "cache_size_mb" : null),
    },
  });
  assert.equal(root.renderWrites, 0, "range input must not rebuild root.innerHTML during drag");
  assert.equal(numberEl.value, "128");
  assert.equal(fieldClasses.has("is-overridden"), true);
  assert.equal(barClasses.has("is-dirty"), true);
  assert.equal(saveBtnEl.disabled, false);
  assert.match(badgeEl.textContent, /已微调 1 项/);

  // Committing via `change` triggers a single full render.
  handlers.change({
    target: {
      type: "range",
      value: "128",
      getAttribute: name => (name === "data-tuning-input" ? "cache_size_mb" : null),
    },
  });
  assert.equal(root.renderWrites, 1);
  assert.match(root.innerHTML, /已微调 1 项/);

  // Remounting on the same root must abort prior listeners so listener count stays 1 per event type.
  assert.equal(root.listenerCount("click"), 1);
  window.CompanionTuningPage.mount(root);
  assert.equal(root.listenerCount("click"), 1);
  assert.equal(root.listenerCount("input"), 1);
  assert.equal(root.listenerCount("change"), 1);
  window.CompanionTuningPage.unmount();
  assert.equal(root.listenerCount("click"), 0);
});
