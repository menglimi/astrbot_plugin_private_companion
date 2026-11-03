/* Page-lifetime state for the optional designer. Never writes browser storage. */
"use strict";
(() => {
  let state = null, active = true;
  const results = new Map();
  const clone = value => value == null ? value : JSON.parse(JSON.stringify(value));
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
