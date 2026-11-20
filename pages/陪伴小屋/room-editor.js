'use strict';

// The room stays visible while its right-side status and agenda panel becomes the editor.
let roomEditorState = null;
let roomEditorSignature = '';
let roomEditorClosing = false;

function roomEditorExitDuration(editor) {
  if (!editor) return 0;
  const style = window.getComputedStyle?.(editor);
  if (!style || style.animationName === 'none') return 0;
  return style.animationDuration.split(',').reduce((longest, value) => {
    const duration = value.trim();
    const number = Number.parseFloat(duration) || 0;
    return Math.max(longest, duration.endsWith('ms') ? number : number * 1000);
  }, 0);
}

function editorOptions(items, selected) {
  return Object.entries(items).map(([id, value]) => `<option value="${escapeHTML(id)}" ${id === selected ? 'selected' : ''}>${escapeHTML(typeof value === 'string' ? value : value.label)}</option>`).join('');
}

function roomOptionCards(items, selected, attribute, details = {}) {
  return Object.entries(items).map(([id, value]) => {
    const label = typeof value === 'string' ? value : value.label;
    const description = typeof value === 'object' ? value.materials : details[id]?.note;
    const active = id === selected;
    const swatch = (attribute === 'room-house' ? 'house-' : 'finish-') + id;
    return `<button type="button" class="room-choice${active ? ' active' : ''}" data-${attribute}="${escapeHTML(id)}" aria-pressed="${active}" title="${escapeHTML(label)}">
      <span class="room-choice-swatch" data-swatch="${escapeHTML(swatch)}" aria-hidden="true"></span>
      <span class="room-choice-copy"><strong>${escapeHTML(label)}</strong>${description ? `<small>${escapeHTML(description)}</small>` : ''}</span>
    </button>`;
  }).join('');
}

const ROOM_THEME_TO_HOUSE = {
  classic: 'classic', forest: 'cabin', coastal: 'coastal',
  french: 'french', loft: 'loft', washitsu: 'washitsu',
};
const HOUSE_TO_ROOM_THEME = Object.fromEntries(Object.entries(ROOM_THEME_TO_HOUSE).map(([theme, house]) => [house, theme]));
function roomThemeKey(houseStyle) { return HOUSE_TO_ROOM_THEME[houseStyle] || 'classic'; }
const ROOM_HOUSE_STYLES = new Set(Object.values(ROOM_THEME_TO_HOUSE));
/* 场景主题立即写入全局偏好：关掉拓展页时不会因为布置草稿没保存而退回庭院。
   家具仍是草稿，取消布置会把这一个主题一起还原。 */
function rememberRoomTheme() {
  if (!voxelController || typeof state === 'undefined') return;
  const layout = voxelController.getLayout?.();
  if (!layout || !ROOM_HOUSE_STYLES.has(layout.houseStyle)) return;
  state.roomHouseStyle = layout.houseStyle;
  if (arrangementDraft?.role && state.roomLayouts?.[arrangementDraft.role]) {
    state.roomLayouts[arrangementDraft.role].houseStyle = layout.houseStyle;
  }
  if (typeof persist === 'function') persist();
}
function restoreRoomTheme(theme) {
  if (!theme || !ROOM_HOUSE_STYLES.has(theme) || typeof state === 'undefined') return;
  state.roomHouseStyle = theme;
  if (arrangementDraft?.role && state.roomLayouts?.[arrangementDraft.role]) {
    state.roomLayouts[arrangementDraft.role].houseStyle = theme;
  }
  if (typeof persist === 'function') persist();
}
function syncRoomThemeButtons(theme) {
  const root = $('#roomEditor');
  if (!root?.querySelectorAll) return;
  root.querySelectorAll('[data-room-theme]').forEach(button => {
    const active = button.dataset.roomTheme === theme;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

let roomEngineLoad = null;
function loadRoomEditorEngine() {
  if (window.PrivateCompanionHomeRoom3D) return Promise.resolve();
  if (roomEngineLoad) return roomEngineLoad;
  // Use the published URL, including the host's asset token and cache version.
  const source = document.getElementById('companion-full-assets')?.content.querySelector('script[data-voxel-engine]')?.src;
  if (!source) return Promise.reject(new Error('房间布置资源尚未发布'));
  roomEngineLoad = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = source;
    script.onload = () => window.PrivateCompanionHomeRoom3D ? resolve() : reject(new Error('体素引擎未就绪'));
    script.onerror = () => { script.remove(); reject(new Error('房间布置资源加载失败')); };
    document.head.appendChild(script);
  }).finally(() => { roomEngineLoad = null; });
  return roomEngineLoad;
}

async function openLiteRoomEditor() {
  if (window.CompanionShell?.section && !await backHome()) return;
  const ticket = navigationIntent, key = layoutKey();
  const stillCurrent = () => ticket === navigationIntent && key === layoutKey() && !document.hidden;
  try {
    if (!window.PrivateCompanionHomeRoom3D && !roomEngineLoad) toast('正在加载房间布置工具…');
    await loadRoomEditorEngine();
    if (!stillCurrent() || arrangementDraft || roomEditorClosing) return;
    const mount = $('[data-voxel-room]');
    if (!mount || !$('#shellSide')) return;
    // The compact stage expands before the engine measures the canvas.
    document.body.classList.add('room-editing');
    mountVoxelScene(mount);
    enterRoomEditor();
    armHomeAgendaClock();
  } catch (error) {
    if (!stillCurrent()) return;
    if (arrangementDraft) finishRoomEditor(false);
    else releaseLiteRoomScene();
    toast('无法打开房间布置，请再次点击「布置」重试。');
    console.warn('房间布置未能启动', error);
  }
}
function releaseLiteRoomScene() {
  voxelController?.dispose();
  voxelController = null;
  document.body.classList.remove('room-editing');
}

function openRoomEditor() {
  if (arrangementDraft || roomEditorClosing) return;
  if (litePerformanceMode) return openLiteRoomEditor();
  enterRoomEditor();
}
function enterRoomEditor() {
  if (!voxelController || $('.voxel-scene-failed')) {
    toast('请先重新加载体素小屋，再进入布置');
    return;
  }
  closeDetail();
  if (window.CompanionShell?.section) backHome();
  const side = $('#shellSide');
  if (!side) return;
  arrangementDraft = {
    role: layoutKey(), scene: state.scene,
    layout: voxelController.getLayout(),
    returnFocus: document.activeElement, returnFurniture: document.activeElement?.dataset?.locateFurniture,
  };
  const catalog = voxelController.getCatalog();
  side.innerHTML = `<section class="room-editor" id="roomEditor" aria-labelledby="roomEditorTitle">
    <header class="room-editor-header">
      <div class="room-editor-title"><small>${roleName()}的小屋</small><h1 id="roomEditorTitle">布置房间</h1></div>
      <div class="room-editor-actions"><button type="button" class="button" data-room-cancel>取消</button><button type="button" class="button primary" data-room-save>${I('check')}保存布置</button></div>
    </header>
    <p class="room-editor-hint" data-room-hint role="status" aria-live="polite"></p>
    <aside class="room-editor-controls" aria-label="布置工具">
      <section class="room-editor-group room-editor-ambience" aria-labelledby="roomAmbienceTitle">
        <div class="room-editor-group-head"><h2 id="roomAmbienceTitle">场景主题</h2></div>
        <div class="room-choice-grid room-theme-choices" role="group" aria-label="场景主题">
          <button type="button" class="room-choice" data-room-theme="classic" aria-label="经典庭院" aria-pressed="false"><span class="room-choice-swatch" style="--room-swatch-default:linear-gradient(135deg,#d9d2b8 0 48%,#4a7c50 48% 100%)" aria-hidden="true"></span><span>庭院</span></button>
          <button type="button" class="room-choice" data-room-theme="forest" aria-label="森林木屋" aria-pressed="false"><span class="room-choice-swatch" style="--room-swatch-default:linear-gradient(135deg,#8a6a48 0 48%,#1f3b2a 48% 100%)" aria-hidden="true"></span><span>森林</span></button>
          <button type="button" class="room-choice" data-room-theme="coastal" aria-label="海岸白屋" aria-pressed="false"><span class="room-choice-swatch" style="--room-swatch-default:linear-gradient(135deg,#f0f0df 0 48%,#44899c 48% 100%)" aria-hidden="true"></span><span>海岸</span></button>
          <button type="button" class="room-choice" data-room-theme="french" aria-label="山巅云屋" aria-pressed="false"><span class="room-choice-swatch" style="--room-swatch-default:linear-gradient(135deg,#f2e9da 0 48%,#7c93b5 48% 100%)" aria-hidden="true"></span><span>山巅</span></button>
          <button type="button" class="room-choice" data-room-theme="loft" aria-label="城市天台" aria-pressed="false"><span class="room-choice-swatch" style="--room-swatch-default:linear-gradient(135deg,#38474b 0 48%,#b87961 48% 100%)" aria-hidden="true"></span><span>天台</span></button>
          <button type="button" class="room-choice" data-room-theme="washitsu" aria-label="竹林和室" aria-pressed="false"><span class="room-choice-swatch" style="--room-swatch-default:linear-gradient(135deg,#e4dfc9 0 48%,#788369 48% 100%)" aria-hidden="true"></span><span>和室</span></button>
        </div>
        <div class="room-editor-group-head"><h2 id="roomStyleTitle">房屋样式</h2><span>${Object.keys(catalog.houseStyles).length} 种可选</span></div>
        <div class="room-choice-grid room-house-choices" role="group" aria-label="房屋样式">
          ${roomOptionCards(catalog.houseStyles, voxelController.getState().houseStyle, 'room-house')}
        </div>
        <div class="room-editor-fields">
          <div class="room-editor-field"><label for="roomPreviewTime">预览时段</label><select id="roomPreviewTime" data-room-time>${sceneTimes.map(([id, label]) => `<option value="${id}" ${id === state.scene ? 'selected' : ''}>${label}</option>`).join('')}</select></div>
        </div>
        <small class="room-editor-note" data-room-materials></small>
      </section>

      <section class="room-editor-group room-editor-furniture" aria-labelledby="roomFurnitureTitle">
        <div class="room-editor-group-head"><h2 id="roomFurnitureTitle">选择家具</h2><span data-room-placement></span></div>
        <div class="room-editor-fields room-editor-furniture-fields">
          <div class="room-editor-field room-editor-furniture-picker"><label for="roomFurniture">家具</label><select id="roomFurniture" data-room-furniture></select></div>
          <div class="room-editor-field" data-room-model-field hidden><label for="roomFurnitureModel">款式</label><select id="roomFurnitureModel" data-room-model></select></div>
          <div class="room-editor-field" data-room-surface-field hidden><label for="roomFurnitureSurface">摆放表面</label><select id="roomFurnitureSurface" data-room-surface></select></div>
        </div>
        <div class="room-choice-grid room-finish-choices" role="group" aria-label="家具样式">
          ${roomOptionCards(catalog.styles, '', 'room-style', catalog.styleDetails)}
        </div>
        <small class="room-editor-note" data-room-style-note></small>
        <div class="room-editor-storage"><span>已摆放 · 收纳会保留外观与位置</span><button type="button" class="button room-editor-store" data-room-store></button></div>
      </section>

      <section class="room-editor-group room-editor-placement-group" aria-labelledby="roomPlacementTitle">
        <div class="room-editor-group-head"><h2 id="roomPlacementTitle">摆放调整</h2><span>坐标与朝向</span></div>
        <div class="room-coordinate-grid">
          <label class="room-editor-field"><span>左右 X</span><input type="number" step="0.25" inputmode="decimal" data-room-coordinate="x" aria-label="左右位置 X"></label>
          <label class="room-editor-field"><span>前后 Z</span><input type="number" step="0.25" inputmode="decimal" data-room-coordinate="z" aria-label="前后位置 Z"></label>
          <label class="room-editor-field"><span>朝向 °</span><input type="number" step="45" inputmode="numeric" data-room-coordinate="rotation" aria-label="家具朝向角度"></label>
        </div>
        <div class="room-editor-placement">
          <div class="room-editor-position"><span class="room-editor-field-label">微调位置</span><div class="room-editor-nudge" role="group" aria-label="微调家具位置"><button type="button" data-room-nudge="up" title="向前或向上移动" aria-label="向前或向上移动家具">${I('arrow-up')}</button><button type="button" data-room-nudge="left" title="向左移动" aria-label="向左移动家具">${I('arrow-left')}</button><span aria-hidden="true">${I('move')}</span><button type="button" data-room-nudge="right" title="向右移动" aria-label="向右移动家具">${I('arrow-right')}</button><button type="button" data-room-nudge="down" title="向后或向下移动" aria-label="向后或向下移动家具">${I('arrow-down')}</button></div></div>
          <div class="room-editor-rotation" data-room-rotation><span class="room-editor-field-label">旋转</span><div><button type="button" class="button small" data-room-rotate="-45" title="向左旋转 45 度" aria-label="向左旋转 45 度">${I('rotate-ccw')}</button><button type="button" class="button small" data-room-rotate="45" title="向右旋转 45 度" aria-label="向右旋转 45 度">${I('rotate-cw')}</button></div><output data-room-angle aria-live="polite"></output></div>
        </div>
      </section>

      <div class="room-editor-history" role="group" aria-label="编辑历史"><button type="button" class="button small" data-room-undo title="撤销（Ctrl 或 Command + Z）" aria-label="撤销" disabled>${I('undo-2')}</button><button type="button" class="button small" data-room-redo title="重做（Ctrl 或 Command + Shift + Z）" aria-label="重做" disabled>${I('redo-2')}</button><button type="button" class="button small room-editor-defaults" data-room-defaults title="恢复默认布局">默认布局</button></div>
    </aside>
  </section>`;
  side.setAttribute('aria-label', '布置房间工作台');
  document.body.classList.remove('room-editing-exit');
  document.body.classList.add('room-editing');
  const canvas = $('.room-stage canvas');
  canvas.tabIndex = 0;
  canvas.setAttribute('aria-label', '布置房间。点选或拖动家具，方向键微调，R 旋转，Ctrl 或 Command Z 撤销。');
  icons();
    
    // Theme switching. Keep this test-double friendly: the real editor exposes
    // querySelectorAll, while the DOM harness only implements the selectors it
    // asserts on.
    const themeRoot = $('#roomEditor');
    const themeButtons = themeRoot?.querySelectorAll
      ? [...themeRoot.querySelectorAll('[data-room-theme]')] : [];
    themeButtons.forEach(button => {
      button.addEventListener('click', () => {
        voxelController.setTheme(button.dataset.roomTheme);
        syncRoomThemeButtons(button.dataset.roomTheme);
        rememberRoomTheme();
      });
    });
    syncRoomThemeButtons(roomThemeKey(voxelController.getState?.().houseStyle));

  voxelController.setEditing(true);
  voxelController.resize();
  canvas.focus({preventScroll: true});
}

function updateRoomEditor(editor) {
  if (!arrangementDraft || !$('#roomEditor') || !editor.editing) return;
  syncRoomThemeButtons(roomThemeKey(editor.houseStyle));
  roomEditorState = editor;
  const signature = JSON.stringify([editor.id, editor.houseStyle, editor.item.style, editor.item.model, editor.item.surface, editor.item.x, editor.item.y, editor.item.z, editor.item.rotation, editor.item.stored, editor.canUndo, editor.canRedo, editor.hint, editor.blocked]);
  if (signature === roomEditorSignature) return;
  roomEditorSignature = signature;
  const catalog = voxelController.getCatalog();
  const stored = new Set(voxelController.getState().storedFurniture);
  const choices = Object.fromEntries(Object.entries(catalog.furniture).filter(([id]) => {
    const entry = directory.find(item => item.station === id);
    return !entry?.cap || state.modules[entry.cap] !== 'removed';
  }).map(([id, label]) => [id, label.replace(/ · .+$/, '') + (stored.has(id) ? ' · 已收纳' : '')]));
  const picker = $('[data-room-furniture]');
  const choiceHTML = editorOptions(choices, editor.id);
  if (picker.innerHTML !== choiceHTML) picker.innerHTML = choiceHTML;
  picker.value = editor.id;
  $('#roomEditor').querySelectorAll('[data-room-house]').forEach(button => {
    const active = button.dataset.roomHouse === editor.houseStyle;
    button.setAttribute('aria-pressed', String(active));
    button.classList.toggle('active', active);
  });
  $('[data-room-materials]').textContent = catalog.houseStyles[editor.houseStyle]?.materials || '';
  $('#roomEditor').querySelectorAll('[data-room-style]').forEach(button => {
    const active = button.dataset.roomStyle === editor.item.style;
    button.setAttribute('aria-pressed', String(active));
    button.classList.toggle('active', active);
  });
  $('[data-room-style-note]').textContent = catalog.styleDetails[editor.item.style]?.note || '';
  const models = catalog.objectModels[editor.id] || {};
  $('[data-room-model-field]').hidden = Object.keys(models).length < 2;
  $('[data-room-model]').innerHTML = editorOptions(models, editor.item.model);
  const surfaces = models[editor.item.model]?.surfaces || ['floor'];
  $('[data-room-surface-field]').hidden = surfaces.length < 2;
  $('[data-room-surface]').innerHTML = editorOptions(Object.fromEntries(surfaces.map(id => [id, catalog.surfaces[id]])), editor.item.surface);
  $('[data-room-placement]').textContent = stored.has(editor.id) ? '已收纳' : '已摆放';
  $('[data-room-store]').innerHTML = I(stored.has(editor.id) ? 'package-open' : 'archive') + (stored.has(editor.id) ? '摆回房间' : '收纳这件家具');
  $('[data-room-rotation]').hidden = editor.item.surface !== 'floor';
  $('[data-room-angle]').textContent = `${Math.round(editor.item.rotation)}°`;
  $('[data-room-coordinate="x"]').value = Number(editor.item.x).toFixed(2);
  $('[data-room-coordinate="z"]').value = Number(editor.item.z).toFixed(2);
  $('[data-room-coordinate="rotation"]').value = String(Math.round(editor.item.rotation));
  $('[data-room-undo]').disabled = !editor.canUndo;
  $('[data-room-redo]').disabled = !editor.canRedo;
  $('[data-room-hint]').textContent = editor.hint;
  $('[data-room-hint]').classList.toggle('is-blocked', editor.blocked);
  icons();
}

function nudgeRoomFurniture(direction) {
  if (!roomEditorState || !voxelController) return;
  const item = roomEditorState.item;
  const step = .25;
  const patch = {};
  if (direction === 'left' || direction === 'right') {
    const axis = item.surface === 'left-wall' ? 'z' : 'x';
    patch[axis] = item[axis] + (direction === 'left' ? -step : step) * (axis === 'z' ? -1 : 1);
  } else {
    const axis = item.surface === 'floor' ? 'z' : 'y';
    patch[axis] = item[axis] + (direction === 'up' ? step : -step) * (axis === 'z' ? -1 : 1);
  }
  voxelController.updateFurniture(patch);
}

function finishRoomEditor(save) {
  if (!arrangementDraft || roomEditorClosing) return;
  roomEditorClosing = true;
  const draft = arrangementDraft;
  const editor = $('#roomEditor');
  voxelController?.setEditing(false);
  if (save && voxelController) {
    const layout = voxelController.getLayout();
    // Capability removal is distinct from the user's choice to store furniture.
    for (const entry of directory.filter(item => item.cap && state.modules[item.cap] === 'removed' && item.station)) {
      if (!layout.items[entry.station]) continue;
      if (state.roomLayouts[draft.role]?.items?.[entry.station]?.stored) layout.items[entry.station].stored = true;
      else delete layout.items[entry.station].stored;
    }
    state.roomLayouts[draft.role] = layout;
    state.roomHouseStyle = layout.houseStyle;
  } else {
    restoreRoomTheme(draft.layout?.houseStyle);
    if (!litePerformanceMode) voxelController?.setLayout(draft.layout);
  }
  // Lite discards the temporary scene instead of repainting it at the compact size.
  if (litePerformanceMode) releaseLiteRoomScene();
  editor?.classList.add('is-leaving');
  if (editor) document.body.classList.add('room-editing-exit');
  document.body.classList.remove('room-editing');
  roomEditorState = null;
  roomEditorSignature = '';
  const canvas = $('.room-stage canvas');
  if (canvas) {
    canvas.removeAttribute('tabindex');
    canvas.setAttribute('aria-label', '体素小屋。可拖动旋转、滚轮缩放，也可使用下方家具按钮浏览内容。');
  }
  syncEnvironment();
  voxelController?.resize();
  updateFurnitureDirectory();
  if (draft.returnFocus?.isConnected) draft.returnFocus.focus({preventScroll: true});
  else if (draft.returnFurniture) $(`[data-locate-furniture="${draft.returnFurniture}"]`)?.focus({preventScroll: true});
  if (save) saved('房间布置已保存');

  const restoreSide = () => {
    if (editor && $('#roomEditor') !== editor) return;
    editor?.remove();
    document.body.classList.remove('room-editing-exit');
    arrangementDraft = null;
    roomEditorClosing = false;
    if (state.sceneMode === 'auto') {
      const scene = window.CompanionHomeEnvironment?.sceneForTime(Date.now(), product.data.state, product.data.life);
      if (scene && scene !== state.scene) state.scene = scene;
      syncEnvironment();
    }
    renderShellSide();
    if (litePerformanceMode) armHomeAgendaClock();
    else voxelController?.resize();
  };
  const duration = litePerformanceMode ? 0 : roomEditorExitDuration(editor);
  if (duration > 0) window.setTimeout(restoreSide, duration);
  else restoreSide();
}

function handleRoomEditorClick(el) {
  if (!arrangementDraft || roomEditorClosing) return false;
  if (el.hasAttribute('data-room-save')) finishRoomEditor(true);
  else if (el.hasAttribute('data-room-cancel')) finishRoomEditor(false);
  else if (el.dataset.roomHouse) { voxelController.updateHouseStyle(el.dataset.roomHouse); rememberRoomTheme(); }
  else if (el.dataset.roomStyle) voxelController.updateFurniture({style: el.dataset.roomStyle});
  else if (el.hasAttribute('data-room-undo')) voxelController.undoEdit();
  else if (el.hasAttribute('data-room-redo')) voxelController.undoEdit(true);
  else if (el.hasAttribute('data-room-defaults')) voxelController.restoreDefaults();
  else if (el.hasAttribute('data-room-store')) voxelController.setFurnitureStored(!voxelController.getState().storedFurniture.includes(roomEditorState.id));
  else if (el.dataset.roomNudge) nudgeRoomFurniture(el.dataset.roomNudge);
  else if (el.dataset.roomRotate) voxelController.updateFurniture({rotation: roomEditorState.item.rotation + Number(el.dataset.roomRotate)});
  else return false;
  return true;
}

function handleRoomEditorChange(el) {
  if (!arrangementDraft || roomEditorClosing) return false;
  if (el.hasAttribute('data-room-coordinate')) {
    const key = el.dataset.roomCoordinate, value = Number(el.value);
    if (['x', 'z', 'rotation'].includes(key) && Number.isFinite(value)) voxelController.updateFurniture({[key]: value});
  }
  else if (el.hasAttribute('data-room-furniture')) voxelController.selectFurniture(el.value);
  else if (el.hasAttribute('data-room-model')) voxelController.updateFurniture({model: el.value});
  else if (el.hasAttribute('data-room-surface')) voxelController.updateFurniture({surface: el.value});
  else if (el.hasAttribute('data-room-time')) {
    arrangementDraft.scene = el.value;
    syncEnvironment({...state, scene: el.value});
  } else return false;
  return true;
}

function handleRoomEditorKey(event) {
  if (!arrangementDraft || roomEditorClosing) return false;
  if (event.key === 'Escape') {
    event.preventDefault();
    finishRoomEditor(false);
    return true;
  }
  if (event.target.matches('input,textarea,select,[contenteditable="true"]')) return true;
  const key = event.key.toLowerCase();
  if ((event.ctrlKey || event.metaKey) && key === 'z') {
    event.preventDefault();voxelController.undoEdit(event.shiftKey);
  } else if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) {
    event.preventDefault();nudgeRoomFurniture(event.key.slice(5).toLowerCase());
  } else if (key === 'r' && roomEditorState?.item.surface === 'floor') {
    event.preventDefault();voxelController.updateFurniture({rotation: roomEditorState.item.rotation + 90});
  }
  return true;
}
