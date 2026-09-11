// 角色衣柜面板：整体服饰倾向 + 具体衣物，支持上传图片由识图模型描述后入库。
window.PrivateCompanionWardrobe = (() => {
  const MAX_ITEMS = 40;
  const MAX_NAME = 40;
  const MAX_DESCRIPTION = 500;
  const MAX_TAGS = 8;
  const UPLOAD_MIMES = ["image/png", "image/jpeg", "image/webp"];
  const UPLOAD_MAX_BYTES = 12 * 1024 * 1024;

  let items = [];
  let bound = false;
  let busy = false;

  function cleanText(value, limit) {
    const text = String(value ?? "").replace(/\s+/g, " ").trim();
    return limit > 0 && text.length > limit ? text.slice(0, limit).trim() : text;
  }

  function cleanTags(value) {
    const raw = Array.isArray(value) ? value : String(value ?? "").split(/[,，、/|;；\s]+/);
    const result = [];
    for (const entry of raw) {
      const tag = cleanText(entry, 16);
      if (tag && !result.includes(tag)) result.push(tag);
      if (result.length >= MAX_TAGS) break;
    }
    return result;
  }

  function nameKey(value) {
    return cleanText(value, MAX_NAME).toLowerCase().replace(/\s+/g, "");
  }

  function randomId() {
    const buffer = new Uint8Array(6);
    if (window.crypto?.getRandomValues) window.crypto.getRandomValues(buffer);
    else for (let i = 0; i < buffer.length; i += 1) buffer[i] = Math.floor(Math.random() * 256);
    return `wardrobe_${Array.from(buffer, (b) => b.toString(16).padStart(2, "0")).join("")}`;
  }

  function normalizeItem(raw) {
    if (!raw || typeof raw !== "object") return null;
    const name = cleanText(raw.name ?? raw.title, MAX_NAME);
    const description = cleanText(raw.description ?? raw.note, MAX_DESCRIPTION);
    if (!name && !description) return null;
    const source = cleanText(raw.source ?? raw.path, 1200);
    return {
      id: cleanText(raw.id, 80) || randomId(),
      name: name || description.slice(0, 12) || "未命名衣物",
      description,
      tags: cleanTags(raw.tags),
      source,
      source_kind: cleanText(raw.source_kind, 20) || (source ? "image" : "manual"),
    };
  }

  function normalizeItems(value) {
    const list = Array.isArray(value) ? value : [];
    const result = [];
    const seenIds = new Set();
    const seenNames = new Set();
    for (const raw of list) {
      const item = normalizeItem(raw);
      if (!item || seenIds.has(item.id)) continue;
      const key = nameKey(item.name);
      if (key && seenNames.has(key)) continue;
      seenIds.add(item.id);
      if (key) seenNames.add(key);
      result.push(item);
      if (result.length >= MAX_ITEMS) break;
    }
    return result;
  }

  function parseStoredItems(raw) {
    if (Array.isArray(raw)) return normalizeItems(raw);
    const text = String(raw ?? "").trim();
    if (!text) return [];
    if (text.startsWith("[") && text.endsWith("]")) {
      try {
        return normalizeItems(JSON.parse(text));
      } catch (_error) {
        return [];
      }
    }
    return [];
  }

  function contextDocument(context) {
    return context?.document || window.document || null;
  }

  function setStatus(context, message, tone = "") {
    const node = contextDocument(context)?.querySelector("[data-wardrobe-status]");
    if (!node) return;
    node.textContent = message || "";
    node.dataset.tone = tone;
  }

  function currentLimit(context) {
    const document = contextDocument(context);
    const input = document?.querySelector('[name="wardrobe_image_max_count"]');
    const value = Number(input?.value || 3);
    if (!Number.isFinite(value)) return 3;
    return Math.max(1, Math.min(8, Math.round(value)));
  }

  function renderList(context) {
    const document = contextDocument(context);
    const list = document?.querySelector("[data-wardrobe-list]");
    if (!list) return;
    list.textContent = "";
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "wardrobe-empty";
      empty.textContent = "衣柜还是空的。可以用文字添加，也可以上传一张图片让识图模型描述后加入。";
      list.appendChild(empty);
    }
    items.forEach((item, index) => {
      const row = document.createElement("div");
      row.className = "wardrobe-item";
      row.dataset.wardrobeItemId = item.id;

      const body = document.createElement("div");
      body.className = "wardrobe-item-body";

      const title = document.createElement("strong");
      title.textContent = `${index + 1}. ${item.name}`;
      body.appendChild(title);

      if (item.description) {
        const detail = document.createElement("p");
        detail.textContent = item.description;
        body.appendChild(detail);
      }

      const meta = [];
      if (item.tags.length) meta.push(item.tags.join(" / "));
      if (item.source_kind === "image") meta.push("来自图片");
      if (meta.length) {
        const metaNode = document.createElement("small");
        metaNode.textContent = meta.join(" · ");
        body.appendChild(metaNode);
      }
      row.appendChild(body);

      const actions = document.createElement("div");
      actions.className = "wardrobe-item-actions";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "删除";
      remove.dataset.wardrobeRemove = item.id;
      actions.appendChild(remove);
      row.appendChild(actions);

      list.appendChild(row);
    });
    const counter = document.querySelector?.("[data-wardrobe-count]")
      || contextDocument(context)?.querySelector("[data-wardrobe-count]");
    if (counter) counter.textContent = `${items.length} / ${MAX_ITEMS}`;
  }

  function syncHiddenInput(context) {
    const input = contextDocument(context)?.querySelector("[data-wardrobe-items-input]");
    if (input) input.value = JSON.stringify(items);
  }

  function commit(context) {
    syncHiddenInput(context);
    renderList(context);
  }

  function readDraft(context) {
    const document = contextDocument(context);
    const nameInput = document?.querySelector("[data-wardrobe-name]");
    const descriptionInput = document?.querySelector("[data-wardrobe-description]");
    return {
      name: cleanText(nameInput?.value, MAX_NAME),
      description: cleanText(descriptionInput?.value, MAX_DESCRIPTION),
    };
  }

  function clearDraft(context) {
    const document = contextDocument(context);
    const nameInput = document?.querySelector("[data-wardrobe-name]");
    const descriptionInput = document?.querySelector("[data-wardrobe-description]");
    if (nameInput) nameInput.value = "";
    if (descriptionInput) descriptionInput.value = "";
  }

  function upsertItem(context, draft, extra = {}) {
    const name = cleanText(draft.name, MAX_NAME) || cleanText(draft.description, 12);
    if (!name) return { ok: false, message: "请先填写衣物名称或描述。" };
    const key = nameKey(name);
    const existingIndex = items.findIndex((item) => nameKey(item.name) === key);
    const payload = {
      id: existingIndex >= 0 ? items[existingIndex].id : randomId(),
      name,
      description: cleanText(draft.description, MAX_DESCRIPTION),
      tags: cleanTags(extra.tags ?? (existingIndex >= 0 ? items[existingIndex].tags : [])),
      source: cleanText(extra.source ?? (existingIndex >= 0 ? items[existingIndex].source : ""), 1200),
      source_kind: cleanText(extra.source_kind, 20)
        || (extra.source ? "image" : (existingIndex >= 0 ? items[existingIndex].source_kind : "manual")),
    };
    if (existingIndex >= 0) items.splice(existingIndex, 1, payload);
    else {
      if (items.length >= MAX_ITEMS) return { ok: false, message: `衣柜最多 ${MAX_ITEMS} 件，请先删除不用的衣物。` };
      items.push(payload);
    }
    return { ok: true, replaced: existingIndex >= 0, name };
  }

  function readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(new Error("读取图片失败"));
      reader.readAsDataURL(file);
    });
  }

  async function addFromFile(context, file) {
    const { postJson } = context;
    if (!file) return;
    if (!UPLOAD_MIMES.includes(String(file.type || "").toLowerCase())) {
      setStatus(context, "只支持 PNG、JPEG 或 WebP 图片。", "error");
      return;
    }
    if (Number(file.size || 0) > UPLOAD_MAX_BYTES) {
      setStatus(context, "图片请控制在 12 MB 以内。", "error");
      return;
    }
    if (items.length >= MAX_ITEMS) {
      setStatus(context, `衣柜最多 ${MAX_ITEMS} 件，请先删除不用的衣物。`, "error");
      return;
    }
    busy = true;
    setStatus(context, "正在上传图片…");
    try {
      const dataUrl = await readFileAsDataUrl(file);
      const uploaded = await postJson("/photo_reference/upload", {
        data_url: dataUrl,
        filename: file.name || "wardrobe.png",
      });
      const source = uploaded?.source || uploaded?.data?.source || "";
      if (!source) throw new Error(uploaded?.message || uploaded?.error || "图片上传失败");
      setStatus(context, "正在让识图模型描述这件衣服…");
      const described = await postJson("/wardrobe/describe", {
        source,
        note: cleanText(readDraft(context).name, MAX_NAME),
      });
      const payload = described?.data && typeof described.data === "object" ? described.data : described;
      const description = cleanText(payload?.description, MAX_DESCRIPTION);
      if (!description) throw new Error(payload?.message || "识图模型没有返回可用的衣物描述");
      const draft = {
        name: cleanText(payload?.name, MAX_NAME) || cleanText(description, 12),
        description,
      };
      const result = upsertItem(context, draft, {
        tags: payload?.tags,
        source,
        source_kind: "image",
      });
      if (!result.ok) {
        setStatus(context, result.message, "error");
        return;
      }
      commit(context);
      clearDraft(context);
      setStatus(context, result.replaced ? `已更新「${result.name}」。记得点保存。` : `已加入「${result.name}」。记得点保存。`, "ok");
    } catch (error) {
      setStatus(context, error?.message || "识图添加失败，请稍后再试。", "error");
    } finally {
      busy = false;
    }
  }

  function bindActions(context) {
    const document = contextDocument(context);
    if (!document || bound) return;
    const manager = document.querySelector("[data-wardrobe-manager]");
    if (!manager) return;
    bound = true;

    manager.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.dataset.wardrobeRemove) {
        const id = target.dataset.wardrobeRemove;
        items = items.filter((item) => item.id !== id);
        commit(context);
        setStatus(context, "已移除，记得点保存。", "ok");
        return;
      }
      if (target.hasAttribute("data-wardrobe-add-text")) {
        const draft = readDraft(context);
        const result = upsertItem(context, draft);
        if (!result.ok) {
          setStatus(context, result.message, "error");
          return;
        }
        commit(context);
        clearDraft(context);
        setStatus(context, result.replaced ? `已更新「${result.name}」。记得点保存。` : `已加入「${result.name}」。记得点保存。`, "ok");
        return;
      }
      if (target.hasAttribute("data-wardrobe-add-image")) {
        if (busy) return;
        manager.querySelector("[data-wardrobe-file]")?.click();
      }
    });

    manager.addEventListener("change", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLInputElement) || !target.hasAttribute("data-wardrobe-file")) return;
      const file = target.files?.[0];
      target.value = "";
      if (file) void addFromFile(context, file);
    });
  }

  function hydrateWardrobePanel(context) {
    const document = contextDocument(context);
    if (!document) return;
    const settings = context?.state?.overview?.settings || {};
    const input = document.querySelector("[data-wardrobe-items-input]");
    // 服务端设置是权威来源；只有在设置里根本没有这个键时才回退到表单草稿，
    // 否则 fillForm 写下的占位串会把已有衣物清空。
    if (Object.prototype.hasOwnProperty.call(settings, "wardrobe_items")) {
      items = parseStoredItems(settings.wardrobe_items);
    } else {
      items = parseStoredItems(input?.value);
    }
    const limit = document.querySelector("[data-wardrobe-image-limit]");
    if (limit) limit.textContent = String(currentLimit(context));
    commit(context);
    bindActions(context);
  }

  return {
    hydrateWardrobePanel,
    syncWardrobeFromSettings: hydrateWardrobePanel,
    wardrobeItemsForTest: () => items.slice(),
  };
})();
