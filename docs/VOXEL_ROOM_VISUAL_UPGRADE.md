# 插件内体素小屋 · 美化说明

> 对象：`pages/陪伴小屋/assets/voxel-room.js` 及其 `assets/voxel-source/` 源码。
> 这是在**原有小屋**上做视觉升级，不换布局、不换房型、不换家具站台。

## 1. 改动范围

| 文件 | 改动 |
| --- | --- |
| `assets/voxel-source/panels/home-room-3d.source.js` | 光影、接触阴影、墙脚 AO、窗帘、墙钟、挂墙搁板、矮墙花盆、窗台陈设、家具细节 |
| `assets/voxel-source/panels/home-room-courtyard.source.js` | 平台花境、木凳、灯笼、夜间萤火、前景花丛 |
| `assets/voxel-room.js` | 用 `build.mjs` 从上述源码重新构建的 IIFE 产物 |
| `assets/voxel-source/build.mjs` | esbuild 构建脚本，支持 `--check` |
| `assets/voxel-source/package.json` | 固定 `esbuild@0.25.0` 的开发依赖 |
| `pages/陪伴小屋/index.html` | 体素包缓存版本号更新为 `home-v2-visual-20261119` |
| `pages/陪伴小屋/page-build.json` | 重新生成的页面哈希台账（含新构建文件） |

房间的 10 个站台（书桌、书架、衣柜、床、电台、花架、游戏、生图、在一起、相框）、8 种家具风格、6 种房型、默认布局、放置/碰撞/收纳/撤销逻辑都未改动。

## 2. 视觉改动

### 2.1 光影与材质

- 曝光从 1.18 降到 1.05，避免高光把浅色木材洗白。
- 白天主光改为更低的暖色侧光（`#ffe0b0`，强度 5.0），半球光换成冷天光 + 暖地面反射（1.95），补光降低到 0.55，并增加一盏冷色轮廓光。
- 阴影从已废弃的 `PCFSoftShadowMap` 改为 `PCFShadowMap` + `shadow.radius`，仍只有一盏投影灯、2048 阴影图。
- 地板靠墙 1.05 单位有程序生成的柔和 AO；家具下方有共享贴图的接触阴影（跟随家具组，不参与拾取）。
- 黄昏/夜晚分别调整主光、半球光、补光、室内点光与窗色；夜晚的窗、台灯、壁灯、灯笼和庭院萤火形成层次。

### 2.2 建筑与陈设

- 奶油色踢脚线 + 深色压边 + 顶部收口 + 转角压条，让两片剖面墙落地。
- 后墙圆挂钟、两片窄搁板（书、陶盆、蜡烛），填补床头上方的空白。
- 窗内侧三层布幔窗帘（帘杆、帘环、系带、下摆），保留窗景与采光。
- 矮墙顶一排陶盆植物；窗台增加书叠、蜡烛和杯子。
- 床尾折毯/书/靠枕、书桌杯与书叠、座椅坐垫、阅读椅披毯/书、衣柜顶木箱与垂吊植物。
- 全部家具细节都加在原有 `localBounds` 内；碰撞、收纳与编辑器 measured bounds 不变。

### 2.3 庭院

- 平台四周低矮花境；近景两侧成片小花，不铺满中央草坪。
- 左侧木凳、路径旁木框灯笼（夜间暖光），保留原有石路、栅栏、树木、雪盖、水洼与粒子。
- 灌木随机点缀花朵；夜间 28 个加法混合萤火点精灵，天气为雨/暴雨/雪时隐藏。

## 3. 构建

```powershell
cd pages/陪伴小屋/assets/voxel-source
npm install --no-save esbuild@0.25.0
node build.mjs
node build.mjs --check

cd ../../..
python -X utf8 tools/sync_framework_page.py          # 更新 page-build.json
python tools/build_room_preview.py build/room-preview.html
```

`build.mjs` 用 esbuild 把 `panels/home-room-3d.source.js`（含 Three.js / OrbitControls / 庭院 / 角色模块）打成经典 IIFE，写入 `../voxel-room.js`；页面仍然只用 `<script defer>`，无 CDN、无运行时 node 依赖。

## 4. 验证

- `node --test tests/test_entrance.cjs tests/test_shell.cjs tests/test_room_editor.cjs tests/test_voxel_mount_callbacks.cjs tests/test_performance_gate.cjs`：**71/71 通过**。
- 重新构建 `voxel-room.js` 与源码 `--check` 哈希一致。
- 预览页（`build/room-preview.html`）在 1500×1000 下加载：无 console/page error；日/晨/黄昏/夜、晴/雨/雪/雾、6 种房型、编辑/收纳/角色/巡游均可操作。
- `build/room-preview.png` / `build/room-preview-interaction.png` 更新；新增 `build/room-visual-before-after.png` 对照图。

## 5. 已知边界

- 窗帘、灯笼是体素块近似，近看有台阶感；这是本项目一贯的体素语言。
- 接触阴影是共享径向纹理的平面，不是 SSAO；没有引入后处理。
- 挂墙搁板/时钟是纯装饰，点中不会派发 station（与墙、结构件一致）。
- 插件页面的体素包 URL 已换版本号；若宿主或反向代理另有强缓存，仍需一次硬刷新。
- 与本轮无关的既有问题：`assets/engine/config/default-layout.json` 使用 `0xff0000` 非法 JSON 字面量，会阻断 `tools/build_v2_release.py`；`tests/test_home_styles.cjs` 的 class 扫描对一个模板字符串误报。两者都没有在本轮改动范围内。
