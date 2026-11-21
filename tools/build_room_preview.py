"""Build a self-contained, offline preview of the voxel cottage.

The preview embeds the real `assets/voxel-room.js` controller and exposes every
public room capability: light, weather, camera stations, resident, tour, door,
entrance/construction progress, and the furniture editor. It does not need the
AstrBot host or any mock API.

Usage: python tools/build_room_preview.py [output]
Default output: build/room-preview.html
"""
from __future__ import annotations

import base64
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = ROOT / "pages" / "陪伴小屋"
DEFAULT_OUT = ROOT / "build" / "room-preview.html"


def read(path: pathlib.Path) -> str:
    if not path.exists():
        raise SystemExit(f"missing file: {path}")
    return path.read_text(encoding="utf-8")


def inline_safe(script: str) -> str:
    # HTML ends a script block at a literal </script, even inside JS strings.
    return script.replace("</", "<\\/")


def font_face() -> str:
    out = []
    for name, weight in (("MiSans-Regular.woff2", 400), ("MiSans-Demibold.woff2", 600)):
        path = PAGE / "assets" / name
        if not path.exists():
            continue
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        out.append(
            "@font-face{font-family:MiSans;src:url(data:font/woff2;base64,"
            + data
            + f") format('woff2');font-weight:{weight};font-display:swap}}"
        )
    return "".join(out)


CSS = r"""
:root{
  color-scheme: light;
  --paper:#f5f2e8; --panel:#fffdf7; --ink:#2f3a33; --muted:#6d786f;
  --line:#d9d4c4; --accent:#3f6b56; --accent-soft:#e6eee7;
  --shadow:0 18px 50px rgba(53,64,52,.14);
  --sans:MiSans,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%}
body{background:radial-gradient(1200px 700px at 20% 0%,#fffdf4,#ece8da 60%,#e3dfd0);
  color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.55}
.page{max-width:1480px;margin:0 auto;padding:28px 24px 50px}
.hero{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;margin-bottom:18px}
.eyebrow{margin:0 0 4px;color:var(--accent);font-size:11px;letter-spacing:.18em;text-transform:uppercase}
h1{margin:0;font-size:28px;letter-spacing:.02em}
.hero p{margin:6px 0 0;color:var(--muted);max-width:64ch}
.badge{align-self:flex-start;border:1px solid var(--line);border-radius:999px;padding:6px 11px;
  background:rgba(255,253,247,.72);color:var(--muted);font-size:12px;white-space:nowrap}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 370px;gap:18px;align-items:start}
.stage-card{position:relative;background:var(--panel);border:1px solid var(--line);
  border-radius:18px;box-shadow:var(--shadow);overflow:hidden;min-height:620px}
@media(min-width:1081px){.stage-card{position:sticky;top:20px;height:calc(100vh - 130px);min-height:560px;max-height:900px}}
.stage{position:absolute;inset:0;background:linear-gradient(160deg,#dfe9df,#cdd9ce)}
.stage canvas{display:block;width:100%;height:100%}
.stage-hint{position:absolute;left:16px;bottom:16px;z-index:3;max-width:70%;
  padding:8px 12px;border:1px solid rgba(255,255,255,.65);border-radius:999px;
  background:rgba(255,253,247,.82);backdrop-filter:blur(10px);color:var(--muted);font-size:12px}
.stage-fallback{display:grid;place-items:center;height:100%;padding:30px;text-align:center;color:var(--muted)}
.panel{display:grid;gap:12px}
.card{background:rgba(255,253,247,.92);border:1px solid var(--line);border-radius:14px;
  padding:14px;box-shadow:0 8px 22px rgba(57,67,55,.07)}
.card h2{margin:0 0 8px;font-size:13px;letter-spacing:.04em;color:var(--accent)}
.row{display:flex;flex-wrap:wrap;gap:6px}
button{font:inherit;color:inherit;cursor:pointer;border:1px solid var(--line);
  border-radius:9px;background:#fffdf7;padding:6px 10px;transition:.16s ease}
button:hover{border-color:var(--accent);background:var(--accent-soft)}
button[aria-pressed="true"]{border-color:var(--accent);background:var(--accent);color:#fff}
button:focus-visible{outline:3px solid rgba(63,107,86,.28);outline-offset:2px}
select,input{font:inherit;color:inherit;border:1px solid var(--line);border-radius:8px;
  background:#fffdf7;padding:6px 8px;min-width:0}
label{display:grid;gap:3px;color:var(--muted);font-size:11px}
.field-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.full{grid-column:1/-1}
.status{margin:8px 0 0;min-height:18px;color:var(--muted);font-size:11.5px}
.slider-row{display:grid;grid-template-columns:64px minmax(0,1fr) 42px;gap:8px;align-items:center;margin-top:8px}
input[type=range]{padding:0;accent-color:var(--accent)}
.telemetry{margin:0;max-height:170px;overflow:auto;padding:10px;border-radius:10px;
  background:#f1efe5;color:#59645a;font:11px/1.55 ui-monospace,Consolas,monospace;white-space:pre-wrap}
@media(max-width:1080px){.layout{grid-template-columns:1fr}.stage-card{min-height:560px}}
@media(max-width:620px){.page{padding:18px 12px 36px}.hero{display:block}.badge{display:inline-block;margin-top:10px}.stage-card{min-height:440px}}
"""


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>体素小屋 · 全功能预览</title>
<style>__FONTS__ __CSS__</style>
</head>
<body>
<div class="page">
  <header class="hero">
    <div>
      <p class="eyebrow">Companion V2 · Room Preview</p>
      <h1>体素小屋 · 全功能预览</h1>
      <p>真实体素控制器离屏运行：环境、天气、镜头、角色、巡游、房门、入场、施工进度与家具布置都直接调用生产接口。</p>
    </div>
    <span class="badge">自包含预览 · 不连接宿主</span>
  </header>

  <div class="layout">
    <section class="stage-card" aria-label="体素小屋场景">
      <div class="stage" id="stage"></div>
      <div class="stage-hint" id="hint">拖动旋转 · 滚轮缩放 · 点家具查看</div>
    </section>

    <aside class="panel">
      <section class="card">
        <h2>光照 / 天气</h2>
        <div class="row" id="lights"></div>
        <div class="row" id="weathers" style="margin-top:6px"></div>
        <div class="row" style="margin-top:6px">
          <button id="tour">开始巡游</button>
          <button id="door">开 / 关门</button>
          <button id="overview">回到全景</button>
        </div>
      </section>

      <section class="card">
        <h2>镜头与家具</h2>
        <div class="row" id="stations"></div>
      </section>

      <section class="card">
        <h2>角色</h2>
        <div class="row">
          <button id="resident-show">显示角色</button>
          <button id="resident-focus">走近角色</button>
          <button id="resident-work">在书桌阅读</button>
          <button id="resident-hide">收起角色</button>
        </div>
        <p class="status" id="resident-status"></p>
      </section>

      <section class="card">
        <h2>入场 / 施工</h2>
        <div class="slider-row"><span>入场</span><input id="entrance" type="range" min="0" max="1" step="0.01" value="1"><output id="entrance-out">1.00</output></div>
        <div class="slider-row"><span>施工</span><input id="construction" type="range" min="0" max="1" step="0.01" value="1"><output id="construction-out">1.00</output></div>
      </section>

      <section class="card">
        <h2>布置房间</h2>
        <div class="row">
          <button id="edit-toggle">进入布置</button>
          <button id="edit-undo">撤销</button>
          <button id="edit-redo">重做</button>
          <button id="edit-defaults">默认布局</button>
        </div>
        <div class="field-grid" style="margin-top:8px">
          <label class="full">房屋样式<select id="house-style"></select></label>
          <label class="full">家具<select id="furniture"></select></label>
          <label class="full">样式<select id="style"></select></label>
          <label class="full">款式 / 表面<select id="model"></select></label>
          <label>X<input id="pos-x" type="number" step="0.25"></label>
          <label>Z<input id="pos-z" type="number" step="0.25"></label>
          <label>朝向<input id="rotation" type="number" step="45"></label>
          <label>状态<select id="stored"><option value="false">摆在外面</option><option value="true">收纳</option></select></label>
        </div>
        <div class="row" style="margin-top:8px">
          <button id="edit-save">应用数值</button>
          <button id="edit-store">应用收纳</button>
        </div>
        <p class="status" id="edit-status"></p>
      </section>

      <section class="card">
        <h2>生活痕迹</h2>
        <div class="row">
          <button id="trace-photo">放一张照片</button>
          <button id="trace-work">放一页手账</button>
          <button id="trace-clear">收起痕迹</button>
        </div>
        <p class="status" id="trace-status">照片会落在相框，手账会落在书桌。</p>
      </section>

      <section class="card">
        <h2>遥测</h2>
        <pre class="telemetry" id="telemetry">等待场景…</pre>
      </section>
    </aside>
  </div>
</div>

<script>__VOXEL__</script>
<script>
(() => {
  "use strict";
  const stage = document.getElementById("stage");
  const hint = document.getElementById("hint");
  const telemetry = document.getElementById("telemetry");
  const $ = id => document.getElementById(id);
  const lights = [["dawn","清晨"],["day","日间"],["dusk","黄昏"],["night","夜晚"]];
  const weathers = [["clear","晴"],["cloudy","多云"],["rain","雨"],["storm","暴雨"],["snow","雪"],["mist","雾"]];
  const stations = [["overview","全景"],["desk","书桌"],["shelf","书架"],["wardrobe","衣柜"],["bed","床"],["radio","电台"],["garden","花架"],["game","游戏"],["image","生图"],["together","在一起"],["art","相框"]];
  let controller = null, catalog = null, editing = false, selectedId = "", residentOn = false, tourOn = false, doorOpen = false;
  let editorState = null;

  function buttonRow(host, items, active, onPick) {
    host.replaceChildren(...items.map(([id, label]) => {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = label; b.dataset.id = id;
      b.setAttribute("aria-pressed", String(id === active));
      b.addEventListener("click", () => onPick(id, b));
      return b;
    }));
  }
  function markRow(host, active) {
    host.querySelectorAll("button[data-id]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.id === active)));
  }
  function setHint(text) { hint.textContent = text; }
  function telemetryText() {
    if (!controller) return "场景尚未挂载";
    const ambient = controller.getAmbientState?.() || {};
    const resident = controller.getResidentState?.() || {};
    const state = controller.getState?.() || {};
    return JSON.stringify({ ambient, resident, room: { houseStyle: state.houseStyle, doorOpen: state.doorOpen, tour: state.tourRunning, moving: state.moving, construction: state.constructionProgress, camera: state.camera } }, null, 2);
  }
  function refreshTelemetry() {
    telemetry.textContent = telemetryText();
  }
  function fillEditor() {
    if (!catalog || !controller) return;
    const layout = controller.getLayout?.();
    selectedId = editorState?.id || selectedId || Object.keys(catalog.furniture)[0];
    const houseSelect = $("house-style");
    houseSelect.replaceChildren(...Object.entries(catalog.houseStyles || {}).map(([id, entry]) => {
      const o = document.createElement("option"); o.value = id; o.textContent = entry.label || id; return o;
    }));
    houseSelect.value = editorState?.houseStyle || layout?.houseStyle || "classic";
    const furnitureSelect = $("furniture");
    furnitureSelect.replaceChildren(...Object.entries(catalog.furniture).map(([id, label]) => {
      const o = document.createElement("option"); o.value = id; o.textContent = label; return o;
    }));
    furnitureSelect.value = selectedId;
    const item = layout?.items?.[selectedId] || {};
    const styleSelect = $("style");
    styleSelect.replaceChildren(...Object.entries(catalog.styles).map(([id, label]) => {
      const o = document.createElement("option"); o.value = id; o.textContent = label; return o;
    }));
    const style = editorState?.item?.style || item.style || "oak";
    styleSelect.value = style;
    const modelSelect = $("model");
    const models = catalog.objectModels?.[selectedId] || {};
    const modelEntries = Object.entries(models).map(([id, entry]) => [id, entry.label || id]);
    if (!modelEntries.length) modelEntries.push(["default", "默认款式"]);
    modelSelect.replaceChildren(...modelEntries.map(([id, label]) => {
      const o = document.createElement("option"); o.value = id; o.textContent = label; return o;
    }));
    modelSelect.value = editorState?.item?.model || item.model || modelEntries[0][0];
    $("pos-x").value = editorState?.item?.x ?? item.x ?? 0;
    $("pos-z").value = editorState?.item?.z ?? item.z ?? 0;
    $("rotation").value = editorState?.item?.rotation ?? item.rotation ?? 0;
    $("stored").value = String(Boolean(editorState?.item?.stored ?? item.stored));
    $("edit-status").textContent = editorState?.hint || (editing ? (editorState?.blocked ? "当前位置不可放" : "拖动家具或修改数值") : "进入布置后可编辑");
    $("edit-toggle").textContent = editing ? "退出布置" : "进入布置";
    $("edit-toggle").setAttribute("aria-pressed", String(editing));
    $("edit-undo").disabled = !editing || !editorState?.canUndo;
    $("edit-redo").disabled = !editing || !editorState?.canRedo;
  }
  function applyEditorItem(patch) {
    if (!controller || !editing) return;
    controller.selectFurniture(selectedId);
    controller.updateFurniture(patch);
    fillEditor();
  }
  function mountRoom() {
    if (!window.PrivateCompanionHomeRoom3D) {
      stage.innerHTML = '<div class="stage-fallback">体素控制器未内联</div>';
      return;
    }
    try {
      controller = window.PrivateCompanionHomeRoom3D.mount(stage, {
        light: "day",
        reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
        onSelect: id => { markRow(stationsHost, id); setHint("已聚焦：" + id); },
        onResidentSelect: () => { setHint("点到了角色"); },
        onTourChange: on => { tourOn = on; $("tour").setAttribute("aria-pressed", String(on)); $("tour").textContent = on ? "停止巡游" : "开始巡游"; refreshTelemetry(); },
        onShotChange: label => setHint("巡游镜头：" + label),
        onFocusChange: id => markRow(stationsHost, id),
        onEditorChange: state => { editorState = state; editing = Boolean(state?.editing); fillEditor(); refreshTelemetry(); },
        onAtmosphereChange: () => refreshTelemetry(),
      });
      catalog = controller.getCatalog?.() || { furniture: {}, styles: {}, objectModels: {}, houseStyles: {} };
      controller.setAtmosphere({ condition: "clear" });
      controller.setActive(true);
      setHint("拖动旋转 · 滚轮缩放 · 点家具查看");
      fillEditor();
      refreshTelemetry();
    } catch (error) {
      stage.innerHTML = '<div class="stage-fallback">场景未能启动<br><small>' + String(error?.message || error) + "</small></div>";
    }
  }

  const stationsHost = $("stations");
  buttonRow($("lights"), lights, "day", (id, b) => { controller?.setLight(id); markRow($("lights"), id); refreshTelemetry(); });
  buttonRow($("weathers"), weathers, "clear", (id, b) => { controller?.setAtmosphere({ condition: id }); markRow($("weathers"), id); refreshTelemetry(); });
  buttonRow(stationsHost, stations, "overview", (id, b) => {
    controller?.focus?.(id); markRow(stationsHost, id); setHint("镜头：" + (b.textContent || id)); refreshTelemetry();
  });
  $("tour").addEventListener("click", () => { controller?.toggleTour(); refreshTelemetry(); });
  $("door").addEventListener("click", () => { doorOpen = Boolean(controller?.toggleDoor?.()); $("door").setAttribute("aria-pressed", String(doorOpen)); setHint(doorOpen ? "门已打开" : "门已关上"); refreshTelemetry(); });
  $("overview").addEventListener("click", () => { controller?.focus?.("overview"); markRow(stationsHost, "overview"); refreshTelemetry(); });

  $("resident-show").addEventListener("click", () => {
    controller?.setResident?.({
      appearance: { skin: "#f0c8ab", hair: "#4a3730", top: "#6d8f82", bottom: "#6b5b4a", hair_style: "long", accessory: "none", height: 1 },
      presentation: { mode: "voxel" }, work: null,
    });
    residentOn = true; $("resident-status").textContent = "角色已显示";
    refreshTelemetry();
  });
  $("resident-focus").addEventListener("click", () => { controller?.focusResident?.(); setHint("已走近角色"); refreshTelemetry(); });
  $("resident-work").addEventListener("click", () => {
    controller?.setResident?.({
      appearance: { skin: "#f0c8ab", hair: "#4a3730", top: "#6d8f82", bottom: "#6b5b4a", hair_style: "long", accessory: "none", height: 1 },
      presentation: { mode: "voxel" }, work: { title: "今天的手账", ref: { id: "preview" } },
    });
    residentOn = true; $("resident-status").textContent = "角色去书桌阅读";
    refreshTelemetry();
  });
  $("resident-hide").addEventListener("click", () => { controller?.setResident?.(null); residentOn = false; $("resident-status").textContent = "角色已收起"; refreshTelemetry(); });

  function previewPhoto() {
    const canvas = document.createElement("canvas"); canvas.width = 360; canvas.height = 480;
    const ctx = canvas.getContext("2d");
    const sky = ctx.createLinearGradient(0, 0, 0, 480); sky.addColorStop(0, "#b8d5d3"); sky.addColorStop(1, "#e9d9b8");
    ctx.fillStyle = sky; ctx.fillRect(0, 0, 360, 480);
    ctx.fillStyle = "#6f8b72"; ctx.fillRect(0, 290, 360, 190);
    ctx.fillStyle = "#f5e8c9"; ctx.beginPath(); ctx.arc(268, 118, 42, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#4f6858";
    for (let i = 0; i < 7; i++) { const x = 30 + i * 48; ctx.fillRect(x, 250 - (i % 3) * 22, 12, 130); }
    ctx.fillStyle = "rgba(255,253,245,.9)"; ctx.font = "600 20px MiSans,sans-serif"; ctx.fillText("小屋窗外的下午", 24, 440);
    return canvas.toDataURL("image/png");
  }
  $("trace-photo").addEventListener("click", () => {
    controller?.setRoomTraces?.({ photo: { image: previewPhoto(), ref: { id: "preview-photo" }, title: "小屋窗外的下午" } });
    $("trace-status").textContent = "照片已放到相框"; refreshTelemetry();
  });
  $("trace-work").addEventListener("click", () => {
    controller?.setRoomTraces?.({ work: { title: "今天的手账", summary: "风把窗帘吹起来的时候，刚好写到这一页。", ref: { id: "preview-work" } } });
    $("trace-status").textContent = "手账已放到书桌"; refreshTelemetry();
  });
  $("trace-clear").addEventListener("click", () => {
    controller?.setRoomTraces?.({ photo: null, work: null });
    $("trace-status").textContent = "生活痕迹已收起"; refreshTelemetry();
  });

  $("entrance").addEventListener("input", event => {
    const value = Number(event.target.value); $("entrance-out").textContent = value.toFixed(2);
    controller?.setEntranceProgress?.(value); refreshTelemetry();
  });
  $("construction").addEventListener("input", event => {
    const value = Number(event.target.value); $("construction-out").textContent = value.toFixed(2);
    controller?.setConstructionProgress?.(value); refreshTelemetry();
  });

  $("edit-toggle").addEventListener("click", () => {
    if (!controller) return;
    editing = !editing; controller.setEditing(editing); setHint(editing ? "布置模式：拖动家具，方向键微调" : "已退出布置");
    fillEditor(); refreshTelemetry();
  });
  $("furniture").addEventListener("change", event => { selectedId = event.target.value; controller?.selectFurniture?.(selectedId); editorState = null; fillEditor(); });
  $("house-style").addEventListener("change", event => { controller?.updateHouseStyle?.(event.target.value); fillEditor(); refreshTelemetry(); });
  $("style").addEventListener("change", event => applyEditorItem({ style: event.target.value }));
  $("model").addEventListener("change", event => applyEditorItem({ model: event.target.value }));
  $("edit-save").addEventListener("click", () => applyEditorItem({
    x: Number($("pos-x").value) || 0, z: Number($("pos-z").value) || 0,
    rotation: Number($("rotation").value) || 0,
  }));
  $("edit-store").addEventListener("click", () => { controller?.setFurnitureStored?.($("stored").value === "true"); fillEditor(); refreshTelemetry(); });
  $("edit-undo").addEventListener("click", () => { controller?.undoEdit?.(false); fillEditor(); refreshTelemetry(); });
  $("edit-redo").addEventListener("click", () => { controller?.undoEdit?.(true); fillEditor(); refreshTelemetry(); });
  $("edit-defaults").addEventListener("click", () => { controller?.restoreDefaults?.(); fillEditor(); refreshTelemetry(); });

  mountRoom();
  requestAnimationFrame(() => { controller?.resize?.(); refreshTelemetry(); });
  setInterval(refreshTelemetry, 1200);
})();
</script>
</body>
</html>
"""


def main() -> int:
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    voxel = read(PAGE / "assets" / "voxel-room.js")
    html = (HTML.replace("__FONTS__", font_face())
                .replace("__CSS__", CSS)
                .replace("__VOXEL__", inline_safe(voxel)))
    if "window.PrivateCompanionHomeRoom3D" not in html:
        raise SystemExit("controller was not embedded")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")
    print(f"generated {out}")
    print(f"  size {out.stat().st_size / 1024:.0f} KB")
    print("  embedded: voxel controller, environment, stations, resident, tour, door, entrance, construction, editor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
