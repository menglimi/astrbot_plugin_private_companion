// 角色衣柜面板：整体服饰倾向 + 具体衣物，支持上传图片由识图模型描述后入库。
window.PrivateCompanionWardrobe = (() => {
  const MAX_ITEMS = 40;
  const MAX_NAME = 40;
  const MAX_DESCRIPTION = 500;
  const MAX_TAGS = 8;
  const UPLOAD_MIMES = ["image/png", "image/jpeg", "image/webp"];
  const UPLOAD_MAX_BYTES = 12 * 1024 * 1024;

  const MAX_OUTFITS = 30;
  const MAX_OUTFIT_NAME = 40;
  const MAX_OUTFIT_STYLE = 300;
  const OUTFIT_KINDS = ["style", "bundle"];
  const OUTFIT_KIND_LABELS = { style: "风格", bundle: "组合" };
  const OWNERSHIPS = ["owned", "reference"];
  const OWNERSHIP_LABELS = { owned: "自有", reference: "参考" };
  // 与后端 WARDROBE_IMAGE_KINDS 对应；kind 为空表示这条素材还没识图。
  const DRAFT_KIND_LABELS = { item: "散件", outfit: "整套", reference: "参考整套", none: "无法辨认" };
  const SLOT_OPTIONS = [
    ["", "未分类"],
    ["upper", "上身"],
    ["lower", "下身"],
    ["whole", "整身"],
    ["feet", "足部"],
    ["extra", "配件"],
  ];

  let items = [];
  let outfits = [];
  let drafts = [];
  let bound = false;
  let outfitsBound = false;
  let draftsBound = false;
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
    const slot = cleanText(raw.slot ?? raw.category ?? raw.part, 20);
    const precision = cleanText(raw.precision, 12) || "exact";
    const row = {
      id: cleanText(raw.id, 80) || randomId(),
      name: name || description.slice(0, 12) || "未命名衣物",
      description,
      tags: cleanTags(raw.tags),
      source,
      source_kind: cleanText(raw.source_kind, 20) || (source ? "image" : "manual"),
      slot,
      intimate: raw.intimate === true || raw.intimate === "true" || raw.intimate === 1,
      precision: ["exact", "loose"].includes(precision) ? precision : "exact",
    };
    // 面板不编辑的字段原样带回去：hydrate 会立刻重写隐藏域，此后任何一次保存都会把
    // items 整份覆盖回服务端 —— 不保留就会洗掉图片关联与归属（整套那条路同理，
    // 见 normalizeOutfit）。
    const ownership = cleanText(raw.ownership, 16).toLowerCase();
    if (OWNERSHIPS.includes(ownership)) row.ownership = ownership;
    for (const key of ["asset_ids", "created_at", "updated_at", "version"]) {
      if (raw[key] !== undefined && raw[key] !== null) row[key] = raw[key];
    }
    return row;
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

  function randomOutfitId() {
    const buffer = new Uint8Array(6);
    if (window.crypto?.getRandomValues) window.crypto.getRandomValues(buffer);
    else for (let i = 0; i < buffer.length; i += 1) buffer[i] = Math.floor(Math.random() * 256);
    return "outfit_" + Array.from(buffer, (b) => b.toString(16).padStart(2, "0")).join("");
  }

  function cleanOutfitItems(value) {
    const raw = Array.isArray(value) ? value : [];
    const result = [];
    for (const entry of raw) {
      const text = cleanText(entry, 80);
      if (text && !result.includes(text)) result.push(text);
      if (result.length >= 8) break;
    }
    return result;
  }

  function normalizeOutfit(raw) {
    if (!raw || typeof raw !== "object") return null;
    const name = cleanText(raw.name ?? raw.title, MAX_OUTFIT_NAME);
    const style = cleanText(raw.style ?? raw.description ?? raw.note, MAX_OUTFIT_STYLE);
    const linkedItems = cleanOutfitItems(raw.items);
    if (!name && !style && !linkedItems.length) return null;
    const kindText = cleanText(raw.kind, 16).toLowerCase();
    let kind = OUTFIT_KINDS.includes(kindText) ? kindText : (linkedItems.length ? "bundle" : "style");
    // 声称是组合却没有件，等同于空组合 —— 与后端一样降级成风格。
    if (kind === "bundle" && !linkedItems.length) kind = "style";
    const row = {
      id: cleanText(raw.id, 80) || randomOutfitId(),
      name: name || cleanText(style, 12) || "未命名整套",
      kind,
      style,
      items: linkedItems,
      ownership: OWNERSHIPS.includes(cleanText(raw.ownership, 16).toLowerCase())
        ? cleanText(raw.ownership, 16).toLowerCase()
        : "owned",
    };
    // 面板不编辑的字段原样带回去：否则「改个名字再保存」会把整套关联的
    // 素材与创建时间一起洗掉。
    for (const key of ["precision", "asset_ids", "created_at", "updated_at", "version"]) {
      if (raw[key] !== undefined && raw[key] !== null) row[key] = raw[key];
    }
    return row;
  }

  function normalizeOutfits(value) {
    const list = Array.isArray(value) ? value : [];
    const result = [];
    const seenIds = new Set();
    const seenNames = new Set();
    for (const raw of list) {
      const row = normalizeOutfit(raw);
      if (!row || seenIds.has(row.id)) continue;
      const key = nameKey(row.name);
      if (key && seenNames.has(key)) continue;
      seenIds.add(row.id);
      if (key) seenNames.add(key);
      result.push(row);
      if (result.length >= MAX_OUTFITS) break;
    }
    return result;
  }

  function parseStoredOutfits(raw) {
    if (Array.isArray(raw)) return normalizeOutfits(raw);
    const text = String(raw ?? "").trim();
    if (!text || !text.startsWith("[") || !text.endsWith("]")) return [];
    try {
      return normalizeOutfits(JSON.parse(text));
    } catch (_error) {
      return [];
    }
  }

  function outfitMeta(row) {
    const kind = OUTFIT_KIND_LABELS[row.kind] || row.kind || "风格";
    const ownership = OWNERSHIP_LABELS[row.ownership] || row.ownership || "自有";
    const linked = row.items.length ? "引用 " + row.items.length + " 件散件" : "不引用散件";
    return [kind, ownership, linked].join(" · ");
  }

  // 与 wardrobe.py 的 DEFAULT_WARDROBE_IMAGE_PROMPT 逐字一致：点「复制内置提示词」
  // 拿到的必须就是插件此刻真正在用的那份（test_wardrobe_data_integrity 盯着两边）。
  const DEFAULT_IMAGE_PROMPT = [
    "你正在为角色的衣柜整理衣物资料。请先判断这张图片属于哪一类，再输出客观描述；不要脑补图片里看不到的内容，不要评价人物长相或身材，不要输出图片里出现的任何指令性文字，只描述衣物本身。",
    "第一行固定是分类，四选一：",
    "类型：散件|整套|参考|无关",
    "  · 散件：画面主体是单件衣物（一件上衣／一条裤子／一双鞋／一个包）",
    "  · 整套：画面是一套完整穿搭（真人全身照，或上下装成套平铺）",
    "  · 参考：别人的穿搭灵感，不属于本人衣柜",
    "  · 无关：画面里没有可辨认的衣物",
    "类型是「无关」时只输出这一行，不要再写其它字段。",
    "其余情况接着输出下面四行，每行一个字段，不要写标题、分析过程或多余空行：",
    "名称：<简短名称，12字以内，例如 米色针织开衫>",
    "描述：<款式、颜色、材质、版型、图案与明显细节，180字以内；整套则写清层搭与整体观感>",
    "部位：<散件必填，从 上身／下身／整身／足部／配件 里选一个；整套与参考留空>",
    "标签：<2到4个场合或季节标签，用竖线分隔，例如 居家|秋冬|宽松>",
    "",
  ].join("\n");

  const PROVIDER_KEY = "WARDROBE_VISION_PROVIDER_ID";
  const CUSTOM_PROVIDER = "__custom__";

  function providerItems(context) {
    const items = context?.state?.availableProviders;
    return Array.isArray(items) ? items : [];
  }

  function providerLabel(item) {
    const name = String(item?.name || item?.id || "").trim();
    const model = String(item?.model || "").trim();
    const suffix = item?.is_default ? " · 默认" : "";
    return `${name}${model ? ` · ${model}` : ""}${suffix}`;
  }

  function renderProviderControl(context) {
    const document = contextDocument(context);
    const host = document?.querySelector("[data-wardrobe-provider-control]");
    if (!host) return;
    const settings = context?.state?.overview?.settings || {};
    const current = cleanText(settings[PROVIDER_KEY], 160);
    const items = providerItems(context);
    const known = items.some((item) => String(item?.id || "") === current);
    const isCustom = Boolean(current) && !known;

    host.textContent = "";
    const select = document.createElement("select");
    select.dataset.wardrobeProviderSelect = "1";
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "留空则跟随陪伴通用视觉模型";
    select.appendChild(blank);
    items.forEach((item) => {
      const id = String(item?.id || "");
      if (!id) return;
      const option = document.createElement("option");
      option.value = id;
      option.textContent = providerLabel(item);
      if (id === current) option.selected = true;
      select.appendChild(option);
    });
    const custom = document.createElement("option");
    custom.value = CUSTOM_PROVIDER;
    custom.textContent = "手动输入 Provider ID";
    if (isCustom) custom.selected = true;
    select.appendChild(custom);
    // name 只挂在真正生效的那个控件上，避免表单收集到两个同名值。
    if (!isCustom) select.name = PROVIDER_KEY;
    host.appendChild(select);

    const manual = document.createElement("input");
    manual.type = "text";
    manual.maxLength = 160;
    manual.placeholder = "自定义 Provider ID";
    manual.value = isCustom ? current : "";
    manual.dataset.wardrobeProviderManual = "1";
    if (isCustom) manual.name = PROVIDER_KEY;
    else manual.hidden = true;
    host.appendChild(manual);

    const hint = document.querySelector("[data-wardrobe-provider-hint]");
    if (hint) {
      hint.textContent = items.length
        ? `找到 ${items.length} 个可用模型；选不到时可以手动填写 Provider ID。`
        : "暂时读不到模型列表；可以直接手动填写 Provider ID，或留空跟随通用视觉模型。";
    }
  }

  function syncProviderControl(context) {
    const document = contextDocument(context);
    const select = document.querySelector("[data-wardrobe-provider-select]");
    const manual = document.querySelector("[data-wardrobe-provider-manual]");
    if (!select || !manual) return;
    if (select.value === CUSTOM_PROVIDER) {
      delete select.name;
      manual.hidden = false;
      manual.name = PROVIDER_KEY;
      manual.focus();
      return;
    }
    manual.hidden = true;
    delete manual.name;
    select.name = PROVIDER_KEY;
  }

  function currentProviderValue(context) {
    const document = contextDocument(context);
    const select = document.querySelector("[data-wardrobe-provider-select]");
    const manual = document.querySelector("[data-wardrobe-provider-manual]");
    if (select?.value === CUSTOM_PROVIDER) return cleanText(manual?.value, 160);
    if (select) return cleanText(select.value, 160);
    return cleanText(context?.state?.overview?.settings?.[PROVIDER_KEY], 160);
  }

  function renderPromptEditor(context) {
    const document = contextDocument(context);
    const editor = document.querySelector("[data-wardrobe-image-prompt]");
    if (!editor) return;
    const settings = context?.state?.overview?.settings || {};
    const stored = String(settings.wardrobe_image_prompt || "");
    if (document.activeElement !== editor) editor.value = stored;
    editor.placeholder = "留空使用内置提示词。";
  }

  async function testProvider(context) {
    const { postJson } = context;
    const document = contextDocument(context);
    const status = document.querySelector("[data-wardrobe-provider-status]");
    const providerId = currentProviderValue(context);
    if (!providerId) {
      if (status) {
        status.textContent = "先选一个识图模型再测试。";
        status.dataset.tone = "error";
      }
      return;
    }
    if (status) {
      status.textContent = "正在测试…";
      status.dataset.tone = "";
    }
    try {
      const result = await postJson("/provider/test", { key: PROVIDER_KEY, provider_id: providerId });
      const ok = Boolean(result?.ok);
      if (status) {
        status.textContent = ok ? "识图模型可用。" : `测试失败：${result?.error || "未返回有效结果"}`;
        status.dataset.tone = ok ? "ok" : "error";
      }
    } catch (error) {
      if (status) {
        status.textContent = `测试失败：${error?.message || "请求异常"}`;
        status.dataset.tone = "error";
      }
    }
  }

  function bindVisionSettings(context) {
    const document = contextDocument(context);
    const host = document.querySelector("[data-wardrobe-vision-settings]");
    if (!host || host.dataset.wardrobeVisionBound === "1") return;
    host.dataset.wardrobeVisionBound = "1";
    host.addEventListener("change", (event) => {
      const target = event.target;
      if (target instanceof HTMLSelectElement && target.dataset.wardrobeProviderSelect) {
        syncProviderControl(context);
      }
    });
    host.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.hasAttribute("data-wardrobe-test-provider")) {
        void testProvider(context);
        return;
      }
      const editor = document.querySelector("[data-wardrobe-image-prompt]");
      const status = document.querySelector("[data-wardrobe-prompt-status]");
      if (target.hasAttribute("data-wardrobe-prompt-reset")) {
        if (editor) editor.value = "";
        if (status) {
          status.textContent = "已清空，保存后恢复内置提示词。";
          status.dataset.tone = "ok";
        }
        return;
      }
      if (target.hasAttribute("data-wardrobe-prompt-copy")) {
        if (editor) editor.value = DEFAULT_IMAGE_PROMPT;
        if (status) {
          status.textContent = "已复制内置提示词，可在此基础上修改后保存。";
          status.dataset.tone = "ok";
        }
      }
    });
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

  function renderOutfitList(context) {
    const document = contextDocument(context);
    const list = document?.querySelector("[data-wardrobe-outfit-list]");
    if (!list) return;
    list.textContent = "";
    if (!outfits.length) {
      const empty = document.createElement("p");
      empty.className = "wardrobe-empty";
      empty.textContent = "还没有整套。识图判定为整套或参考的图片会进这里，也可以手动添加。";
      list.appendChild(empty);
    }
    outfits.forEach((row, index) => {
      const element = document.createElement("div");
      element.className = "wardrobe-outfit";
      element.dataset.wardrobeOutfitId = row.id;

      const body = document.createElement("div");
      body.className = "wardrobe-outfit-body";
      const title = document.createElement("strong");
      title.textContent = (index + 1) + ". " + row.name;
      body.appendChild(title);
      if (row.style) {
        const detail = document.createElement("p");
        detail.textContent = row.style;
        body.appendChild(detail);
      }
      const meta = document.createElement("small");
      meta.textContent = outfitMeta(row);
      body.appendChild(meta);
      element.appendChild(body);

      const actions = document.createElement("div");
      actions.className = "wardrobe-outfit-actions";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "删除";
      remove.dataset.wardrobeOutfitRemove = row.id;
      actions.appendChild(remove);
      element.appendChild(actions);

      list.appendChild(element);
    });
    const counter = document.querySelector("[data-wardrobe-outfit-count]");
    if (counter) counter.textContent = outfits.length + " / " + MAX_OUTFITS;
  }

  function syncOutfitHiddenInput(context) {
    const input = contextDocument(context)?.querySelector("[data-wardrobe-outfits-input]");
    if (input) input.value = JSON.stringify(outfits);
  }

  function syncHiddenInput(context) {
    const input = contextDocument(context)?.querySelector("[data-wardrobe-items-input]");
    if (input) input.value = JSON.stringify(items);
  }

  function commit(context) {
    syncHiddenInput(context);
    renderList(context);
    syncOutfitHiddenInput(context);
    renderOutfitList(context);
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
      slot: cleanText(extra.slot ?? (existingIndex >= 0 ? items[existingIndex].slot : ""), 20),
      intimate: extra.intimate ?? (existingIndex >= 0 ? items[existingIndex].intimate : false),
      precision: ["exact", "loose"].includes(extra.precision ?? (existingIndex >= 0 ? items[existingIndex].precision : "exact"))
        ? (extra.precision ?? (existingIndex >= 0 ? items[existingIndex].precision : "exact"))
        : "exact",
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
        provider_id: currentProviderValue(context),
      });
      const payload = described?.data && typeof described.data === "object" ? described.data : described;
      const description = cleanText(payload?.description, MAX_DESCRIPTION);
      if (!description) throw new Error(payload?.message || "识图模型没有返回可用的衣物描述");
      const kind = String(payload?.kind || "item").toLowerCase();
      if (kind === "outfit" || kind === "reference") {
        // 整套不该塞进散件列表：面板的整套区还没接线，这里先明确告知，
        // 并指向已经支持整套入库的命令路径。
        setStatus(
          context,
          kind === "reference"
            ? "识图判定这是一套参考穿搭：请用「陪伴 衣柜 添加图片」入库；面板整套编辑还没接线。"
            : "识图判定这是一整套：请用「陪伴 衣柜 添加图片」入库；面板整套编辑还没接线。",
          "error",
        );
        return;
      }
      const draft = {
        name: cleanText(payload?.name, MAX_NAME) || cleanText(description, 12),
        description,
      };
      const result = upsertItem(context, draft, {
        tags: payload?.tags,
        slot: cleanText(payload?.slot, 20),
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
    // 整套走同一套规则：设置是权威来源，表单草稿只在键缺失时兜底。
    const outfitsInput = document.querySelector("[data-wardrobe-outfits-input]");
    if (Object.prototype.hasOwnProperty.call(settings, "wardrobe_outfits")) {
      outfits = parseStoredOutfits(settings.wardrobe_outfits);
    } else {
      outfits = parseStoredOutfits(outfitsInput?.value);
    }
    const limit = document.querySelector("[data-wardrobe-image-limit]");
    if (limit) limit.textContent = String(currentLimit(context));
    renderProviderControl(context);
    renderPromptEditor(context);
    bindVisionSettings(context);
    bindOutfitPreview(context);
    bindOutfitActions(context);
    bindDraftActions(context);
    bindIntentActions(context);
    commit(context);
    bindActions(context);
    // 草稿队列与穿衣意图都是异步的：读不到只影响各自那一块，不该挡住衣柜本身。
    void refreshDrafts(context, { silent: true });
    // 穿衣意图只在展开时拉取；上次打开过（浏览器记住了状态）就顺手补一次。
    if (contextDocument(context)?.querySelector("[data-wardrobe-intent]")?.open === true) {
      void refreshIntent(context, { silent: true });
    }
  }

  // ------------------------------------------------------------------
  // 整套：新增 / 删除
  // ------------------------------------------------------------------

  function readOutfitDraft(context) {
    const document = contextDocument(context);
    return {
      name: cleanText(document?.querySelector("[data-wardrobe-outfit-name]")?.value, MAX_OUTFIT_NAME),
      style: cleanText(document?.querySelector("[data-wardrobe-outfit-style]")?.value, MAX_OUTFIT_STYLE),
      ownership: cleanText(document?.querySelector("[data-wardrobe-outfit-ownership]")?.value, 16).toLowerCase(),
    };
  }

  function clearOutfitDraft(context) {
    const document = contextDocument(context);
    const name = document?.querySelector("[data-wardrobe-outfit-name]");
    const style = document?.querySelector("[data-wardrobe-outfit-style]");
    if (name) name.value = "";
    if (style) style.value = "";
  }

  function upsertOutfit(context, draft) {
    const name = cleanText(draft.name, MAX_OUTFIT_NAME) || cleanText(draft.style, 12);
    if (!name) return { ok: false, message: "请先填写整套名称或风格描述。" };
    const ownership = OWNERSHIPS.includes(cleanText(draft.ownership, 16).toLowerCase())
      ? cleanText(draft.ownership, 16).toLowerCase()
      : "owned";
    const existingIndex = outfits.findIndex((row) => nameKey(row.name) === nameKey(name));
    // 面板只加「风格整套」：组合整套要引用散件，那是识图与命令路径的产物；
    // 手填一个没有件的组合会被后端降级成风格，不如这里就不提供。
    const payload = {
      id: existingIndex >= 0 ? outfits[existingIndex].id : randomOutfitId(),
      name,
      kind: "style",
      style: cleanText(draft.style, MAX_OUTFIT_STYLE),
      items: existingIndex >= 0 ? outfits[existingIndex].items : [],
      ownership,
    };
    if (existingIndex >= 0) outfits.splice(existingIndex, 1, { ...outfits[existingIndex], ...payload });
    else {
      if (outfits.length >= MAX_OUTFITS) return { ok: false, message: "最多 " + MAX_OUTFITS + " 套，请先删除不用的整套。" };
      outfits.push(payload);
    }
    return { ok: true, replaced: existingIndex >= 0, name };
  }

  function upsertLocalOutfit(row) {
    const normalized = normalizeOutfit(row);
    if (!normalized) return;
    const key = nameKey(normalized.name);
    const index = outfits.findIndex((entry) => entry.id === normalized.id || (key && nameKey(entry.name) === key));
    if (index >= 0) outfits.splice(index, 1, normalized);
    else outfits.push(normalized);
  }

  function adoptItem(raw) {
    // normalizeItem 已经原样保留服务端维护的字段（素材引用、归属、时间戳），
    // 这里只是一个语义化别名：读起来是「采纳后端刚确认落库的那一行」。
    return normalizeItem(raw);
  }

  function upsertLocalItem(row) {
    const normalized = adoptItem(row);
    if (!normalized) return;
    const key = nameKey(normalized.name);
    const index = items.findIndex((entry) => entry.id === normalized.id || (key && nameKey(entry.name) === key));
    if (index >= 0) items.splice(index, 1, normalized);
    else items.push(normalized);
  }

  function setBlockStatus(context, selector, message, tone = "") {
    const node = contextDocument(context)?.querySelector(selector);
    if (!node) return;
    node.textContent = message || "";
    node.dataset.tone = tone;
  }

  function bindOutfitActions(context) {
    const document = contextDocument(context);
    const manager = document?.querySelector("[data-wardrobe-outfit-manager]");
    if (!manager || outfitsBound) return;
    outfitsBound = true;
    manager.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.dataset.wardrobeOutfitRemove) {
        outfits = outfits.filter((row) => row.id !== target.dataset.wardrobeOutfitRemove);
        commit(context);
        setBlockStatus(context, "[data-wardrobe-outfit-status]", "已移除，记得点保存。", "ok");
        return;
      }
      if (!target.hasAttribute("data-wardrobe-outfit-add")) return;
      const result = upsertOutfit(context, readOutfitDraft(context));
      if (!result.ok) {
        setBlockStatus(context, "[data-wardrobe-outfit-status]", result.message, "error");
        return;
      }
      commit(context);
      clearOutfitDraft(context);
      setBlockStatus(
        context,
        "[data-wardrobe-outfit-status]",
        result.replaced ? "已更新「" + result.name + "」。记得点保存。" : "已加入「" + result.name + "」。记得点保存。",
        "ok",
      );
    });
  }

  // ------------------------------------------------------------------
  // 草稿队列（素材 → 散件/整套 的人工确认）
  //
  // 面板不自己判断「这张图该进哪个库」：确认请求交给后端，落库结果
  // （payload.row）再合并回本地列表，所以隐藏字段始终跟着服务端走，
  // 不会出现「刚确认完，一保存又把它洗掉」。
  // ------------------------------------------------------------------

  const DRAFTS_STATUS = "[data-wardrobe-drafts-status]";

  function apiPayload(result) {
    return result?.data && typeof result.data === "object" ? result.data : result;
  }

  function normalizeDraft(raw) {
    if (!raw || typeof raw !== "object") return null;
    const assetId = cleanText(raw.asset_id, 80);
    if (!assetId) return null;
    const kind = cleanText(raw.kind, 32);
    const slot = cleanText(raw.slot, 20);
    return {
      asset_id: assetId,
      kind,
      kind_label: cleanText(raw.kind_label, 20) || DRAFT_KIND_LABELS[kind] || kind || "待识图",
      name: cleanText(raw.name, MAX_NAME),
      description: cleanText(raw.description, MAX_DESCRIPTION),
      slot,
      origin_label: cleanText(raw.origin_label, 32) || cleanText(raw.origin, 32) || "未知来源",
      has_draft: raw.has_draft === true,
      has_image: raw.has_image === true,
    };
  }

  function labeledField(document, labelText, control) {
    const label = document.createElement("label");
    label.textContent = labelText;
    label.appendChild(control);
    return label;
  }

  function draftSlotSelect(document, row) {
    const select = document.createElement("select");
    select.dataset.wardrobeDraftSlot = row.asset_id;
    SLOT_OPTIONS.forEach(([value, text]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = text;
      if (value === row.slot) option.selected = true;
      select.appendChild(option);
    });
    return select;
  }

  function draftsPanelOpen(context) {
    return contextDocument(context)?.querySelector("[data-wardrobe-drafts]")?.open === true;
  }

  async function loadDraftThumb(context, image, placeholder, assetId) {
    try {
      const postJson = context?.postJson;
      if (!postJson) throw new Error("panel has no postJson");
      const payload = apiPayload(await postJson("/wardrobe/asset-image", { asset_id: assetId }));
      const url = String(payload?.data_url || "");
      if (!url) throw new Error("empty data url");
      image.src = url;
      image.hidden = false;
      if (placeholder) placeholder.hidden = true;
    } catch (_error) {
      // 缩略图只是锦上添花：读不到就留着占位符，确认与丢弃照常可用。
      if (placeholder) placeholder.textContent = "无预览";
    }
  }

  function draftRow(context, document, row, withThumb) {
    const element = document.createElement("div");
    element.className = "wardrobe-draft";
    element.dataset.wardrobeDraftId = row.asset_id;
    if (!row.has_draft) element.dataset.wardrobeDraftPending = "1";

    const thumb = document.createElement("div");
    thumb.className = "wardrobe-draft-thumb";
    const placeholder = document.createElement("span");
    if (withThumb && row.has_image) {
      const image = document.createElement("img");
      image.alt = "";
      image.hidden = true;
      image.dataset.wardrobeDraftThumb = row.asset_id;
      placeholder.textContent = "…";
      thumb.append(image, placeholder);
      void loadDraftThumb(context, image, placeholder, row.asset_id);
    } else {
      // 折叠时只拉清单、不拉图片：几百条素材也不该在打开面板时炸出一串请求。
      placeholder.textContent = row.has_image ? "展开预览" : "无图";
      thumb.appendChild(placeholder);
    }
    element.appendChild(thumb);

    const body = document.createElement("div");
    body.className = "wardrobe-draft-body";
    const meta = document.createElement("small");
    meta.className = "wardrobe-draft-meta";
    meta.textContent = [row.asset_id, row.kind_label, "来自" + row.origin_label].join(" · ");
    body.appendChild(meta);

    const fields = document.createElement("div");
    fields.className = "wardrobe-draft-fields";
    const name = document.createElement("input");
    name.type = "text";
    name.maxLength = MAX_NAME;
    name.value = row.name;
    name.placeholder = "确认后写进衣柜的名称";
    name.dataset.wardrobeDraftName = row.asset_id;
    fields.appendChild(labeledField(document, "名称", name));
    fields.appendChild(labeledField(document, "部位", draftSlotSelect(document, row)));
    const description = document.createElement("textarea");
    description.rows = 2;
    description.maxLength = MAX_DESCRIPTION;
    description.value = row.description;
    description.placeholder = "款式、颜色、材质、版型与明显细节";
    description.dataset.wardrobeDraftDescription = row.asset_id;
    fields.appendChild(
      labeledField(document, row.kind && row.kind !== "item" ? "风格描述" : "描述", description),
    );
    body.appendChild(fields);

    const actions = document.createElement("div");
    actions.className = "wardrobe-draft-actions";
    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "secondary-button";
    apply.textContent = "确认";
    apply.dataset.wardrobeDraftApply = row.asset_id;
    const reject = document.createElement("button");
    reject.type = "button";
    reject.className = "secondary-button";
    reject.textContent = "丢弃";
    reject.dataset.wardrobeDraftReject = row.asset_id;
    if (!row.has_draft || row.kind === "none") {
      apply.disabled = true;
      const hint = document.createElement("small");
      hint.className = "wardrobe-draft-hint";
      hint.textContent = row.kind === "none" ? "识图没认出衣物，建议丢弃。" : "等识图出草稿后才能确认。";
      actions.appendChild(hint);
    }
    actions.append(apply, reject);
    body.appendChild(actions);
    element.appendChild(body);
    return element;
  }

  function renderDraftList(context) {
    const document = contextDocument(context);
    const list = document?.querySelector("[data-wardrobe-draft-list]");
    const counter = document?.querySelector("[data-wardrobe-drafts-count]");
    if (counter) counter.textContent = String(drafts.length);
    if (!list) return;
    list.textContent = "";
    if (!drafts.length) {
      const empty = document.createElement("p");
      empty.className = "wardrobe-empty";
      empty.textContent = "队列是空的：要么还没导入素材，要么都已确认或丢弃。";
      list.appendChild(empty);
      return;
    }
    const withThumb = draftsPanelOpen(context);
    drafts.forEach((row) => list.appendChild(draftRow(context, document, row, withThumb)));
  }

  function draftOverrides(context, assetId) {
    const document = contextDocument(context);
    let host = null;
    (document?.querySelectorAll?.("[data-wardrobe-draft-id]") || []).forEach((node) => {
      if (node?.dataset?.wardrobeDraftId === assetId) host = node;
    });
    if (!host) return {};
    return {
      name: cleanText(host.querySelector("[data-wardrobe-draft-name]")?.value, MAX_NAME),
      description: cleanText(host.querySelector("[data-wardrobe-draft-description]")?.value, MAX_DESCRIPTION),
      slot: cleanText(host.querySelector("[data-wardrobe-draft-slot]")?.value, 20),
    };
  }

  function applyConfirmedRow(context, payload) {
    const row = payload?.row;
    if (!payload?.ok || !row || typeof row !== "object") return;
    const kind = String(payload.kind || "");
    if (kind === "outfit" || kind === "reference") upsertLocalOutfit(row);
    else upsertLocalItem(row);
    commit(context);
  }

  async function refreshDrafts(context, options = {}) {
    const document = contextDocument(context);
    const postJson = context?.postJson;
    if (!document || !postJson) return;
    const silent = options.silent === true;
    if (!silent) setBlockStatus(context, DRAFTS_STATUS, "正在读取草稿队列…");
    try {
      const payload = apiPayload(await postJson("/wardrobe/drafts", {})) || {};
      const rows = Array.isArray(payload.drafts) ? payload.drafts : [];
      drafts = rows.map(normalizeDraft).filter(Boolean);
      renderDraftList(context);
      if (!silent) {
        const ready = drafts.filter((row) => row.has_draft).length;
        setBlockStatus(
          context,
          DRAFTS_STATUS,
          drafts.length ? "待确认 " + drafts.length + " 项（可确认 " + ready + " 项）。" : "队列是空的。",
          "ok",
        );
      }
    } catch (error) {
      if (!silent) setBlockStatus(context, DRAFTS_STATUS, error?.message || "读取草稿队列失败，请稍后再试。", "error");
    }
  }

  async function confirmDraft(context, assetId) {
    const postJson = context?.postJson;
    if (!postJson) return;
    setBlockStatus(context, DRAFTS_STATUS, "正在确认…");
    try {
      const payload = apiPayload(
        await postJson("/wardrobe/draft-apply", { asset_id: assetId, overrides: draftOverrides(context, assetId) }),
      );
      applyConfirmedRow(context, payload);
      await refreshDrafts(context, { silent: true });
      setBlockStatus(context, DRAFTS_STATUS, "已确认「" + (payload?.name || assetId) + "」。", "ok");
    } catch (error) {
      setBlockStatus(context, DRAFTS_STATUS, error?.message || "确认失败，请稍后再试。", "error");
    }
  }

  async function rejectDraft(context, assetId) {
    const postJson = context?.postJson;
    if (!postJson) return;
    setBlockStatus(context, DRAFTS_STATUS, "正在丢弃…");
    try {
      await postJson("/wardrobe/draft-reject", { asset_id: assetId });
      await refreshDrafts(context, { silent: true });
      setBlockStatus(context, DRAFTS_STATUS, "已丢弃这条草稿，素材不会进衣柜。", "ok");
    } catch (error) {
      setBlockStatus(context, DRAFTS_STATUS, error?.message || "丢弃失败，请稍后再试。", "error");
    }
  }

  async function applyAllDrafts(context) {
    const postJson = context?.postJson;
    if (!postJson) return;
    const targets = drafts.filter((row) => row.has_draft && row.kind !== "none");
    if (!targets.length) {
      setBlockStatus(context, DRAFTS_STATUS, "没有可确认的草稿（等识图的项要先跑识图）。", "error");
      return;
    }
    setBlockStatus(context, DRAFTS_STATUS, "正在确认 " + targets.length + " 项…");
    let applied = 0;
    const failures = [];
    // 串行：衣柜落盘是「读-改-写」，并发确认会互相覆盖，最后只剩一条。
    for (const row of targets) {
      try {
        const payload = apiPayload(
          await postJson("/wardrobe/draft-apply", {
            asset_id: row.asset_id,
            overrides: draftOverrides(context, row.asset_id),
          }),
        );
        applyConfirmedRow(context, payload);
        applied += 1;
      } catch (error) {
        failures.push((row.name || row.asset_id) + "：" + (error?.message || "确认失败"));
      }
    }
    await refreshDrafts(context, { silent: true });
    const suffix = failures.length ? "；" + failures.length + " 项失败：" + failures.slice(0, 3).join("；") : "";
    setBlockStatus(context, DRAFTS_STATUS, "已确认 " + applied + " 项" + suffix, failures.length ? "error" : "ok");
  }

  function bindDraftActions(context) {
    const document = contextDocument(context);
    const root = document?.querySelector("[data-wardrobe-drafts]");
    if (!root || draftsBound) return;
    draftsBound = true;
    root.addEventListener("toggle", () => {
      // 展开时才拉缩略图：没打开这一块的人不必为此付一串请求。
      if (root.open) void refreshDrafts(context);
    });
    root.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.hasAttribute("data-wardrobe-drafts-refresh")) {
        void refreshDrafts(context);
        return;
      }
      if (target.hasAttribute("data-wardrobe-drafts-apply-all")) {
        void applyAllDrafts(context);
        return;
      }
      if (target.dataset.wardrobeDraftApply) {
        void confirmDraft(context, target.dataset.wardrobeDraftApply);
        return;
      }
      if (target.dataset.wardrobeDraftReject) {
        void rejectDraft(context, target.dataset.wardrobeDraftReject);
      }
    });
  }

  // ------------------------------------------------------------------
  // 今天的穿衣意图：只读 + 清除
  //
  // 意图就是作者那套 dialogue_outfit_override（模型工具或用户对话写的），
  // 它优先于当天轮换；面板只看和清，写入归模型工具，所以这里没有保存按钮。
  // ------------------------------------------------------------------

  // 两个按钮各有一个 aria-live 状态区：读取与清除的消息互不覆盖。
  const INTENT_STATUS = "[data-wardrobe-intent-status]";
  const INTENT_CLEAR_STATUS = "[data-wardrobe-intent-clear-status]";
  const INTENT_SOURCE_LABELS = { model_tool: "模型记录", user_dialogue: "用户对话" };

  // undefined 表示还没读过：badge 显示「未读取」，而不是谎报「无」；null 专指确实没有。
  let intent;
  let intentBound = false;

  function intentSourceLabel(source) {
    const key = cleanText(source, 40);
    return INTENT_SOURCE_LABELS[key] || key || "未知来源";
  }

  function intentSlotLabel(slot) {
    const key = cleanText(slot, 20);
    const entry = SLOT_OPTIONS.find(([value]) => value === key);
    return entry ? entry[1] : key || "未分类";
  }

  // 后端给的是秒级时间戳；面板只显示本地时间的时分，够用来判断还剩多久。
  function intentClock(value) {
    const stamp = Number(value);
    if (!Number.isFinite(stamp) || stamp <= 0) return "—";
    const date = new Date(stamp * 1000);
    if (Number.isNaN(date.getTime())) return "—";
    const pad = (part) => String(part).padStart(2, "0");
    return pad(date.getHours()) + ":" + pad(date.getMinutes());
  }

  function normalizeIntent(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const items = (Array.isArray(raw.items) ? raw.items : [])
      .map((entry) => {
        if (!entry || typeof entry !== "object") return null;
        const id = cleanText(entry.id, 80);
        const name = cleanText(entry.name, MAX_NAME);
        if (!id && !name) return null;
        return { id, name: name || id, slot: cleanText(entry.slot, 20) };
      })
      .filter(Boolean);
    const row = {
      instruction: cleanText(raw.instruction, 180),
      source: cleanText(raw.source, 40),
      date: cleanText(raw.date, 16),
      expires_at: Number(raw.expires_at) || 0,
      outfit_name: cleanText(raw.outfit_name, MAX_OUTFIT_NAME),
      items,
    };
    // 没有意图时后端回 {}：一个字段都没读到就当没有，别显示一个空壳。
    if (!row.instruction && !row.outfit_name && !row.items.length) return null;
    return row;
  }

  function intentBadgeText() {
    if (intent === undefined) return "未读取";
    if (!intent) return "无";
    return "有 · " + intentSourceLabel(intent.source);
  }

  function renderIntent(context) {
    const document = contextDocument(context);
    const counter = document?.querySelector("[data-wardrobe-intent-count]");
    if (counter) counter.textContent = intentBadgeText();
    const clear = document?.querySelector("[data-wardrobe-intent-clear]");
    if (clear) clear.disabled = !intent;
    const host = document?.querySelector("[data-wardrobe-intent-detail]");
    if (!host) return;
    host.textContent = "";
    if (!intent) {
      const empty = document.createElement("p");
      empty.className = "wardrobe-empty";
      empty.textContent = "当前没有额外指定，按当天轮换着装。";
      host.appendChild(empty);
      return;
    }

    const note = document.createElement("p");
    note.className = "wardrobe-intent-note";
    note.textContent = "本会话指定的着装优先于当天轮换：下一次注入按它来，不再用当天裁决出的那一套。";
    host.appendChild(note);

    const meta = document.createElement("dl");
    meta.className = "wardrobe-intent-meta";
    const rows = [
      ["指令原文", intent.instruction || "（没有留下原文）"],
      ["来源", intentSourceLabel(intent.source)],
      ["到期", intentClock(intent.expires_at) + (intent.date ? " · " + intent.date : "")],
    ];
    if (intent.outfit_name) rows.push(["整套", intent.outfit_name]);
    rows.forEach(([label, value]) => {
      const cell = document.createElement("div");
      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      detail.textContent = value;
      cell.append(term, detail);
      meta.appendChild(cell);
    });
    host.appendChild(meta);

    const picked = document.createElement("div");
    picked.className = "wardrobe-intent-items";
    if (!intent.items.length) {
      const none = document.createElement("p");
      none.className = "wardrobe-empty";
      none.textContent = "这条意图没有点名具体衣物，模型会照这句话自己挑。";
      picked.appendChild(none);
    } else {
      intent.items.forEach((item) => {
        const line = document.createElement("span");
        line.className = "wardrobe-intent-item";
        line.textContent = item.name + " · " + intentSlotLabel(item.slot);
        picked.appendChild(line);
      });
    }
    host.appendChild(picked);
  }

  async function refreshIntent(context, options = {}) {
    const document = contextDocument(context);
    const postJson = context?.postJson;
    if (!document || !postJson) return;
    const silent = options.silent === true;
    if (!silent) setBlockStatus(context, INTENT_STATUS, "正在读取穿衣意图…");
    try {
      const payload = apiPayload(await postJson("/wardrobe/intent", {})) || {};
      intent = normalizeIntent(payload.intent);
      renderIntent(context);
      if (!silent) {
        setBlockStatus(
          context,
          INTENT_STATUS,
          intent ? "已读取本会话的穿衣意图。" : "当前没有额外指定，按当天轮换着装。",
          "ok",
        );
      }
    } catch (error) {
      if (!silent) setBlockStatus(context, INTENT_STATUS, error?.message || "读取穿衣意图失败，请稍后再试。", "error");
    }
  }

  async function clearIntent(context) {
    const postJson = context?.postJson;
    if (!postJson) return;
    setBlockStatus(context, INTENT_CLEAR_STATUS, "正在清除穿衣意图…");
    try {
      const payload = apiPayload(await postJson("/wardrobe/intent-clear", {})) || {};
      // 清完必须重读一次：badge、衣物列表和清除按钮的可用状态都跟着快照走。
      await refreshIntent(context, { silent: true });
      // 快照已经变了，读取区的旧文案会跟 badge 打架，先清掉。
      setBlockStatus(context, INTENT_STATUS, "");
      setBlockStatus(
        context,
        INTENT_CLEAR_STATUS,
        payload.cleared === true ? "已清除，之后按当天轮换着装。" : "本来就没有穿衣意图。",
        "ok",
      );
    } catch (error) {
      setBlockStatus(context, INTENT_CLEAR_STATUS, error?.message || "清除失败，请稍后再试。", "error");
    }
  }

  function bindIntentActions(context) {
    const document = contextDocument(context);
    const root = document?.querySelector("[data-wardrobe-intent]");
    if (!root || intentBound) return;
    intentBound = true;
    root.addEventListener("toggle", () => {
      // 与草稿队列同一思路：没展开这一块的人不必为此付一次请求。
      if (root.open) void refreshIntent(context);
    });
    root.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.hasAttribute("data-wardrobe-intent-refresh")) {
        void refreshIntent(context);
        return;
      }
      if (target.hasAttribute("data-wardrobe-intent-clear")) {
        void clearIntent(context);
      }
    });
  }

  // ------------------------------------------------------------------
  // 搭配测试：只读预览
  //
  // 直接问后端"这一刻会注入什么"，不写配置、不调模型，所以可以随便点。
  // 面板展示的就是真正会发出去的请求原文，不是近似值。
  // ------------------------------------------------------------------

  const PREVIEW_SOURCE_LABELS = {
    bundle: "整套（准确）",
    style: "整套（模糊）",
    rule: "散件兜底",
    generate: "模型生成",
  };

  const PREVIEW_SLOT_LABELS = {
    upper: "上身",
    lower: "下身",
    whole: "整身",
    feet: "足部",
    extra: "配件",
  };

  function previewPickLine(row) {
    const slot = PREVIEW_SLOT_LABELS[row?.slot] || "未分类";
    const intimate = row?.intimate ? "·贴身" : "";
    return `[${slot}${intimate}] ${row?.name || ""}`;
  }

  function renderPreviewMeta(context, data) {
    const document = contextDocument(context);
    const host = document?.querySelector("[data-wardrobe-preview-meta]");
    if (!host) return;
    host.textContent = "";
    const rows = [
      ["注入模式", data.mode === "select" ? "按天裁决" : "整份清单"],
      [
        "生成器",
        data.generator_enabled
          ? data.generator_ready
            ? "已开启 · 已有生成结果"
            : "已开启 · 尚未生成（后台进行中）"
          : "关闭",
      ],
      ["场合", data.scene || "（未判定）"],
      ["天气", data.weather || "—"],
      ["裁决来源", PREVIEW_SOURCE_LABELS[data.rule?.source] || data.rule?.source || "—"],
      ["命中的整套", data.rule?.outfit_name || "—"],
      ["衣柜", `${data.item_count} 件 · ${data.outfit_count} 套`],
      [
        "未分类衣物",
        Number(data.unclassified_count) > 0
          ? `${data.unclassified_count} 件（没有部位，不参与自动搭配，建议补全）`
          : "0 件",
      ],
      ["注入长度", `${data.injected_chars} / ${data.injected_limit} 字`],
    ];
    for (const [key, value] of rows) {
      const wrap = document.createElement("div");
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = String(value);
      wrap.append(dt, dd);
      host.append(wrap);
    }
  }

  function setPreviewText(context, selector, text) {
    const node = contextDocument(context)?.querySelector(selector);
    if (node) node.textContent = text || "";
  }

  function renderPreviewPicked(context, data) {
    const document = contextDocument(context);
    const host = document?.querySelector("[data-wardrobe-preview-picked]");
    if (!host) return;
    host.textContent = "";
    const picked = Array.isArray(data.rule?.picked) ? data.rule.picked : [];
    if (!picked.length) {
      const empty = document.createElement("p");
      empty.className = "wardrobe-empty";
      empty.textContent = "衣柜里还没有可搭配的衣物。";
      host.append(empty);
      return;
    }
    for (const row of picked) {
      const line = document.createElement("div");
      line.className = "wardrobe-preview-pick";
      line.textContent = previewPickLine(row);
      host.append(line);
    }
  }

  function renderPreviewProfile(context, data) {
    const profile = data.rule?.profile || {};
    const keys = Object.keys(profile);
    const body = keys.length
      ? keys.map((key) => `${key}: ${profile[key]}`).join("\n")
      : "（本次没有生图投影字段）";
    setPreviewText(context, "[data-wardrobe-preview-profile]", body);
  }

  async function runOutfitPreview(context) {
    const document = contextDocument(context);
    const { postJson } = context || {};
    if (!document) return;
    const status = document.querySelector("[data-wardrobe-preview-status]");
    if (!postJson) {
      if (status) status.textContent = "当前面板不支持预览请求。";
      return;
    }
    const scene = document.querySelector("[data-wardrobe-preview-scene]")?.value || "";
    const weather = document.querySelector("[data-wardrobe-preview-weather]")?.value || "";
    if (status) {
      status.textContent = "正在生成预览…";
      status.dataset.tone = "";
    }
    try {
      const result = await postJson("/wardrobe/outfit-preview", { scene, weather });
      const payload =
        result?.data && typeof result.data === "object" ? result.data : result;
      if (!payload || result?.status === "error") {
        if (status) {
          status.textContent = result?.error || result?.message || "预览失败。";
          status.dataset.tone = "error";
        }
        return;
      }
      const output = document.querySelector("[data-wardrobe-preview-output]");
      if (output) output.hidden = false;
      renderPreviewMeta(context, payload);
      renderPreviewPicked(context, payload);
      renderPreviewProfile(context, payload);
      setPreviewText(context, "[data-wardrobe-preview-outfit]", payload.rule?.prompt_text || "（无）");
      setPreviewText(context, "[data-wardrobe-preview-injected]", payload.injected || "（无）");
      setPreviewText(context, "[data-wardrobe-preview-request]", payload.request || "");
      if (status) {
        status.textContent = "预览已更新。";
        status.dataset.tone = "ok";
      }
    } catch (error) {
      if (status) {
        status.textContent = `预览失败：${error?.message || "请求异常"}`;
        status.dataset.tone = "error";
      }
    }
  }

  function bindOutfitPreview(context) {
    const document = contextDocument(context);
    const root = document?.querySelector("[data-wardrobe-outfit-preview]");
    if (!root || root.dataset.bound === "1") return;
    root.dataset.bound = "1";
    root.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const preset = target.getAttribute("data-wardrobe-preview-scene-set");
      if (preset !== null) {
        const input = root.querySelector("[data-wardrobe-preview-scene]");
        if (input) input.value = preset;
        void runOutfitPreview(context);
        return;
      }
      if (target.hasAttribute("data-wardrobe-preview-run")) {
        void runOutfitPreview(context);
      }
    });
  }

  return {
    hydrateWardrobePanel,
    syncWardrobeFromSettings: hydrateWardrobePanel,
    wardrobeItemsForTest: () => items.slice(),
    wardrobeOutfitsForTest: () => outfits.map((row) => ({ ...row })),
    wardrobeDraftsForTest: () => drafts.map((row) => ({ ...row })),
    refreshWardrobeDrafts: (context) => refreshDrafts(context),
    confirmWardrobeDraft: (context, assetId) => confirmDraft(context, assetId),
    rejectWardrobeDraft: (context, assetId) => rejectDraft(context, assetId),
    applyAllWardrobeDrafts: (context) => applyAllDrafts(context),
    wardrobeIntentForTest: () => (intent ? { ...intent, items: intent.items.map((row) => ({ ...row })) } : intent),
    refreshWardrobeIntent: (context) => refreshIntent(context),
    clearWardrobeIntent: (context) => clearIntent(context),
  };
})();
