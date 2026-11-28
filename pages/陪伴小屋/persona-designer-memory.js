/* Page-lifetime memory + per-mode sandbox-safe localStorage draft backup for the designer. */
"use strict";
(() => {
  let state = null, active = true;
  const results = new Map();
  const LOCAL_PREFIX = "companion.persona-designer.v1.";
  const clone = value => value == null ? value : JSON.parse(JSON.stringify(value));
  function readLocal(mode) {
    if (!["optimize", "create"].includes(mode)) return null;
    try {
      const raw = typeof localStorage !== "undefined" && localStorage
        ? localStorage.getItem(LOCAL_PREFIX + mode) : null;
      if (typeof raw !== "string" || !raw) return null;
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
    } catch { return null; }
  }
  function writeLocal(mode, doc) {
    if (!["optimize", "create"].includes(mode)) return false;
    try {
      if (typeof localStorage === "undefined" || !localStorage) return false;
      if (doc === null) { localStorage.removeItem(LOCAL_PREFIX + mode); return true; }
      const encoded = JSON.stringify(doc);
      if (encoded.length > 500000) return false;
      localStorage.setItem(LOCAL_PREFIX + mode, encoded);
      return true;
    } catch { return false; }
  }
  function handle(method, path, body) {
    if (!active) throw new Error("persona_designer_stopped");
    if (path === "designer/client-state") {
      if (method === "GET") return {state: clone(state)};
      const encoded = JSON.stringify(body);
      if (!body || body.version !== 1 || !["optimize", "create"].includes(body.activeMode)
          || encoded.length > 5000000) throw new Error("persona_designer_invalid");
      state = JSON.parse(encoded);
      return {};
    }
    if (path === "designer/local-draft") {
      const mode = body?.mode;
      if (!["optimize", "create"].includes(mode)) throw new Error("persona_designer_invalid");
      if (method === "GET") return {document: readLocal(mode)};
      if (method === "POST") {
        const doc = body?.document ?? null;
        if (doc !== null && (typeof doc !== "object" || Array.isArray(doc)))
          throw new Error("persona_designer_invalid");
        return {saved: writeLocal(mode, doc)};
      }
    }
    const id = body?.id;
    if (typeof id !== "string" || !/^(turn|save):[a-f0-9]{32}$/.test(id))
      throw new Error("persona_designer_invalid");
    if (method === "GET" && path === "designer/client-result") {
      const result = results.get(id);
      return result ? clone(result.value) : {status: "missing"};
    }
    if (method === "POST" && path === "designer/client-ack") {
      if (results.get(id)?.value.status !== "pending") results.delete(id);
      return {};
    }
    throw new Error("persona_designer_invalid");
  }
  function run(id, execute, cancel) {
    if (!active) return Promise.reject(new Error("persona_designer_stopped"));
    if (results.has(id)) return results.get(id).promise;
    // Retain only a few completed receipts. Pending work is never evicted.
    for (const [key, value] of results) {
      if (results.size < 8) break;
      if (value.value.status !== "pending") results.delete(key);
    }
    const record = {value: {status: "pending"}, cancel};
    results.set(id, record);
    record.promise = Promise.resolve().then(execute).then(data => {
      if (active) record.value = {status: "done", data};
      return data;
    }, error => {
      if (active) record.value = {status: "failed", code: error?.code || error?.message || "",
        reason: error?.reason || ""};
      throw error;
    });
    return record.promise;
  }
  window.addEventListener("pagehide", () => {
    active = false;
    state = null;
    for (const record of results.values()) {
      if (record.value.status === "pending") {
        try { Promise.resolve(record.cancel?.()).catch(() => {}); } catch {}
      }
    }
    results.clear();
  });
  window.addEventListener("pageshow", () => { active = true; });
  window.CompanionPersonaDesignerMemory = {handle, run};
})();
