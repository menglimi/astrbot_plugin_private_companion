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

function mountStorage({canFullVacuum = true, compactError = null} = {}) {
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
        if (route === "storage/health") return {status: "ok", data: healthState};
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

function mountTuning({clamped = false} = {}) {
  const calls = [];
  let serverPayload = {
    profile: "auto",
    profile_label: "自动推荐",
    resolved_profile: "balanced",
    resolved_profile_label: "平衡推荐",
    overrides: {},
    override_count: 0,
    baseline: {
      cache_size_mb: 32, sidecar_cache_size_mb: 8, mmap_size_mb: 128, sidecar_mmap_size_mb: 32,
      reader_pool_size: 4, journal_size_limit_mb: 32, synchronous: "NORMAL",
      write_batch_window_ms: 50, background_slots: 3, tts_max_concurrency: 3, telemetry_ttl_days: 14,
    },
    values: {
      cache_size_mb: 32, sidecar_cache_size_mb: 8, mmap_size_mb: 128, sidecar_mmap_size_mb: 32,
      reader_pool_size: 4, journal_size_limit_mb: 32, synchronous: "NORMAL",
      write_batch_window_ms: 50, background_slots: 3, tts_max_concurrency: 3, telemetry_ttl_days: 14,
    },
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
        return {status: "ok", data: serverPayload};
      },
      async apiPost(route, body) {
        calls.push(["POST", route, body]);
        if (body.action === "lite_page") {
          serverPayload = {
            ...serverPayload,
            lite_page: {saved_enabled: body.lite_page_enabled, effective_enabled: false, reload_required: Boolean(body.lite_page_enabled)},
          };
          return {status: "ok", data: serverPayload};
        }
        serverPayload = {
          ...serverPayload,
          profile: body.profile || serverPayload.profile,
          overrides: body.overrides || {},
          override_count: Object.keys(body.overrides || {}).length,
        };
        return {status: "ok", data: serverPayload};
      },
    },
  };
  vm.runInNewContext(TUNING_JS, {window, document: {}, setTimeout, clearTimeout, Promise, Number, String, Object, Array, Error, Math});
  const handlers = {};
  const root = {isConnected: true, innerHTML: "", querySelector: () => null, addEventListener(name, fn) { handlers[name] = fn; }};
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
