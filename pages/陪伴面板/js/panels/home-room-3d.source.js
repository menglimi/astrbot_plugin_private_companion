import * as THREE from "../vendor/three.module.min.js";
import { OrbitControls } from "../vendor/OrbitControls.js";

// Individually coloured blocks are instanced per furniture group. Geometry is
// deterministic; no external models, textures or character meshes are loaded.
window.PrivateCompanionHomeRoom3D = (() => {
  const controllers = new WeakMap();
  const STATIONS = {
    calendar: { label: "生活日历", target: [-5.2, 2.4, -2.85], position: [3.1, 5.4, 5.1], fov: 38 },
    desk: { label: "手账与便签", target: [-3.7, 1.35, -3.25], position: [2.3, 6.4, 4.5], fov: 36 },
    shelf: { label: "故事书架", target: [-4.8, 1.9, -.3], position: [5.2, 4.8, 5.8], fov: 37 },
    wardrobe: { label: "今日衣柜", target: [-.25, 1.9, -3.2], position: [5.9, 4.6, 6.3], fov: 35 },
    bed: { label: "睡眠与梦境", target: [3.8, .9, -2.1], position: [9.4, 6.1, 6.3], fov: 36 },
    radio: { label: "见闻电台", target: [-4.8, 1, 3], position: [3.5, 3.7, 9.1], fov: 36 },
    garden: { label: "慢慢成长", target: [4.35, .9, 2.95], position: [9.9, 4.3, 10.5], fov: 34 },
    game: { label: "游戏伴侣" }, image: { label: "生图工作室" }, together: { label: "在一起" },
  };
  const CATALOG = {
    desk: "窗边书桌", shelf: "故事书架", wardrobe: "衣柜", bed: "床与床头柜",
    radio: "电台边柜", garden: "阶梯花架", lounge: "阅读扶手椅", readingLamp: "落地灯", stool: "茶几", rug: "编织地毯",
    game: "游戏伴侣摆件", image: "生图摆件", together: "在一起电话", calendar: "生活挂历", art: "植物装饰画",
  };
  const SURFACES = { floor: "地面", "back-wall": "后墙", "left-wall": "左墙" };
  const WALLS = ["back-wall", "left-wall"];
  // Appearance belongs to the room; changing it never changes the extension identity.
  const OBJECT_MODELS = {
    game: { chess: { label: "双人棋桌", surfaces: ["floor"] }, arcade: { label: "复古街机", surfaces: ["floor"] }, console: { label: "游戏挂屏", surfaces: WALLS } },
    image: { camera: { label: "三脚架相机", surfaces: ["floor"] }, instant: { label: "拍立得展台", surfaces: ["floor"] }, gallery: { label: "照片挂架", surfaces: WALLS } },
    together: { telephone: { label: "转盘电话柜", surfaces: ["floor"] }, wallPhone: { label: "壁挂电话", surfaces: WALLS } },
    calendar: { calendar: { label: "便签挂历", surfaces: WALLS } },
    art: { botanical: { label: "植物装饰画", surfaces: WALLS } },
  };
  // A 1.2-unit front extension leaves the original walls/window and furniture at scale.
  const ROOM = { minX: -5.62, maxX: 5.7, minZ: -4.2, maxZ: 5.46, wallTop: 4.65, lowWallTop: 1.38, lowWallStart: 1.62 };
  const STYLES = { oak: "原木日常", cream: "奶油轻居", ink: "墨蓝复古", rattan: "日式藤编", walnut: "胡桃中古", loft: "工业拼搭", blossom: "樱色软装", nordic: "北欧几何" };
  const NEW_STYLES = ["rattan", "walnut", "loft", "blossom", "nordic"];
  // Keep stable style IDs so existing saved rooms continue to load unchanged.
  const STYLE_DETAILS = {
    oak: { note: "温润木色，保留熟悉的生活细节。", models: ["窗边手账桌", "开放书格", "竖纹双门柜", "木栅床头", "格栅边柜", "阶梯花架", "木扶手阅读椅", "层叠布罩灯", "编织矮茶几", "椭圆织毯"] },
    cream: { note: "浅色饰面和饱满轮廓，让角落更轻柔。", models: ["浅木围板桌", "错层书格", "素面分格柜", "三片软包床", "浅色包边柜", "方柱花架", "宽背软椅", "方筒布罩灯", "奶油方茶几", "奶油条纹毯"] },
    ink: { note: "墨蓝与暖金搭配，点缀复古细节。", models: ["金属脚书桌", "宽檐书架", "金线双门柜", "高低栅格床", "金边收音柜", "细脚花架", "高背阅读椅", "锥形层叠灯", "金脚矮茶几", "墨蓝织纹毯"] },
    rattan: { note: "藤编格纹、细木框与竹节，带一点庭院气息。", models: ["藤编抽屉桌", "竹格书架", "藤芯双门柜", "藤编屏风床", "藤编唱片柜", "竹梯花架", "藤背休闲椅", "竹编宫灯", "藤篮茶几", "流苏草编毯"] },
    walnut: { note: "深胡桃木、收分轮廓与黄铜细节。", models: ["中古抽屉桌", "胡桃分区书柜", "雕线胡桃柜", "翼形木床头", "中古唱片柜", "胡桃陈列架", "翼背扶手椅", "琥珀蘑菇灯", "椭圆双层茶几", "复古菱格毯"] },
    loft: { note: "深色钢架、铆钉和交叉撑杆，结构清晰利落。", models: ["钢架工作台", "桁架书架", "铆钉储物柜", "钢管框架床", "铆钉器材柜", "钢架植物台", "悬臂框架椅", "折臂工作灯", "框架工作茶几", "拼色方格毯"] },
    blossom: { note: "樱粉软包和花瓣轮廓，配细腻的浅色木脚。", models: ["樱色梳写桌", "花瓣顶书架", "花窗双门柜", "花瓣软包床", "樱色小边柜", "花瓣托盘架", "花瓣靠背椅", "花苞落地灯", "花瓣矮茶几", "花朵簇绒毯"] },
    nordic: { note: "白蜡木、鼠尾草绿与几何留白，轻巧通透。", models: ["A 字支架桌", "几何分格架", "拼色平板柜", "三角拼板床", "拼色几何柜", "A 字花架", "曲折木框椅", "三脚折纸灯", "三脚圆茶几", "山形几何毯"] },
  };
  const FINISHES = {
    rattan: { wood: "#c6a16a", edge: "#876740", accent: "#91a17a", dark: "#596d4d", light: "#d6c59c", fabric: "#cebd92", rose: "#bb8765" },
    walnut: { wood: "#76503a", edge: "#48392f", accent: "#986d46", dark: "#543f34", light: "#bc996e", fabric: "#ad845f", rose: "#ba754f" },
    loft: { wood: "#b49a79", edge: "#38474b", accent: "#72888a", dark: "#3d4e53", light: "#b9c4bf", fabric: "#879b98", rose: "#c68a50" },
    blossom: { wood: "#e1c6ad", edge: "#a48a7c", accent: "#d99fac", dark: "#9c6c80", light: "#f0c6ca", fabric: "#e2b1bc", rose: "#b66f89" },
    nordic: { wood: "#d0b589", edge: "#786e58", accent: "#91a899", dark: "#506b63", light: "#e4eadb", fabric: "#adc2b5", rose: "#c29569" },
  };
  const HOUSE_STYLES = {
    classic: { label: "原木小屋", materials: "木地板 · 绿墙裙 · 布帘窗", note: "熟悉的木色与窗边光线，保留原来的小屋。" },
    washitsu: { label: "日式和室", materials: "榻榻米 · 木格窗 · 纸面门", note: "草席编纹、细木格与柔和纸窗，留一间安静的和室。" },
    loft: { label: "红砖阁楼", materials: "红砖墙 · 水泥地 · 钢窗", note: "错缝砖墙、混凝土地面和黑色钢架，带一点旧工坊的质感。" },
    french: { label: "法式奶油", materials: "线板墙 · 拼花地板 · 拱窗", note: "奶油白线板与拱形窗饰，配一扇带雕线的浅色房门。" },
    coastal: { label: "海边白屋", materials: "白灰墙 · 蓝白砖 · 百叶窗", note: "白灰墙、蓝色窗套与门板，地面围一圈蓝白花砖。" },
    cabin: { label: "林间木屋", materials: "横木墙 · 宽木板 · 外露木梁", note: "横向木墙、宽板地面和粗木窗梁，像林间的小木屋。" },
  };
  const HOUSE_FINISHES = {
    washitsu: { wall: "#e4dfc9", lower: "#d2c9aa", wood: "#a88855", edge: "#635b3e", accent: "#788369", floor: ["#b7b185", "#c9c39a", "#c1bb90"] },
    loft: { wall: "#b87961", lower: "#a96e57", wood: "#796955", edge: "#3e4b4d", accent: "#617a7b", floor: ["#a7aaa1", "#b5b7ad", "#aeb2a7"] },
    french: { wall: "#f2e9da", lower: "#e4d8c2", wood: "#d9c7a9", edge: "#b59b77", accent: "#b8b89e", floor: ["#bf9870", "#d1b18b", "#c8a67f"] },
    coastal: { wall: "#f0f0df", lower: "#e3e9dd", wood: "#5e8d9b", edge: "#3d6f84", accent: "#74a0ad", floor: ["#e5dfc7", "#f0e9d6", "#dcd5bd"] },
    cabin: { wall: "#b28b60", lower: "#ac8056", wood: "#7e5c3f", edge: "#543f30", accent: "#799079", floor: ["#b88d5f", "#c6a173", "#ac8257"] },
  };
  const SHOTS = [
    { label: "01 / 越过屋檐 · 俯冲入室", points: [[0, 18, 2], [8, 12, 10], [6, 5, 9], [3, 2.3, 6]], target: [0, 1, 0], fovs: [46, 50, 58, 64], bank: -.075, seconds: 8 },
    { label: "02 / 桌沿掠影 · 推拉变焦", anchor: "desk", points: [[2.5, 2.1, 4.1], [1.2, 1.8, 2.8], [-.4, 2.7, 2.9], [-1, 4.6, 4.8]], target: [0, 1.4, .1], fovs: [50, 67, 54, 36], bank: .045, seconds: 8 },
    { label: "03 / 书脊长廊 · 横移掠过", anchor: "shelf", points: [[-2, 2.5, 4.3], [0, 1.85, 3.6], [2.3, 2.7, 4]], target: [0, 1.8, .1], fovs: [55, 62, 49], bank: -.035, seconds: 7 },
    { label: "04 / 床边低语 · 半环绕升起", anchor: "bed", points: [[-.7, 1.8, 4.5], [-1.8, 2.4, 4.1], [-2.8, 3.5, 2.8], [-1.6, 6, 1.4]], target: [0, .9, 0], fovs: [59, 57, 50, 43], bank: .075, seconds: 8 },
    { label: "05 / 叶间光线 · 低位环绕", anchor: "garden", points: [[2.4, 1.7, 3], [0, 2.1, 3.1], [-2.1, 1.7, 2.2], [-2.4, 3.8, .6]], target: [0, 1, 0], fovs: [55, 63, 58, 46], bank: -.06, seconds: 7 },
    { label: "06 / 一间生活 · 螺旋拉远", points: [[0, 8, 7], [-1, 16, 9], [12.9, 12.2, 15.1]], target: [0, 1.15, 0], fovs: [48, 44, 38], bank: .06, seconds: 7 },
  ];
  const C = {
    wood: "#a9764f", edge: "#77553f", oak: "#d0a574", cream: "#f5ecd9", paper: "#fff6df",
    teal: "#507e7a", tealLight: "#7bada1", tealDark: "#365f5a", rose: "#c47465", mustard: "#d6ad55",
    navy: "#435369", green: "#657c45", leaf: "#91a768", dark: "#303e3d", brass: "#d7b66c",
  };
  function createController(container, options = {}) {
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(38, 1, .1, 110);
    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.65));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.shadowMap.autoUpdate = false;
    renderer.shadowMap.needsUpdate = true;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.18;
    container.replaceChildren(renderer.domElement);
    renderer.domElement.setAttribute("aria-label", "体素小屋。可拖动旋转、滚轮缩放，也可使用下方家具按钮浏览内容。");
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true; controls.dampingFactor = .085; controls.enablePan = false;
    controls.minDistance = 1.5; controls.maxDistance = 36;
    controls.minPolarAngle = .06; controls.maxPolarAngle = Math.PI * .49;
    // The two open sides of this dollhouse are the useful viewing hemisphere.
    controls.minAzimuthAngle = -.17; controls.maxAzimuthAngle = Math.PI * .59;
    const geometry = new THREE.BoxGeometry(1, 1, 1);
    const material = new THREE.MeshStandardMaterial({ roughness: .94, metalness: .02 });
    const batches = new Map(), allMeshes = [], groups = {}, doors = [], styleGroups = {}, baseBodies = {}, houseGroups = {}, modelGroups = {};
    const matrix = new THREE.Matrix4(), quaternion = new THREE.Quaternion();
    const position = new THREE.Vector3(), scale = new THREE.Vector3(), color = new THREE.Color();
    let voxelCount = 0;
    function group(id, x = 0, y = 0, z = 0, angle = 0, parent = scene) {
      const g = new THREE.Group(); g.position.set(x, y, z); g.rotation.y = angle;
      g.userData.station = id; parent.add(g); return g;
    }
    function furnitureBody(root) {
      const id = root.userData.station, body = group(id, 0, 0, 0, 0, root);
      (baseBodies[id] ||= []).push(body); return body;
    }
    const structure = group(""), floorG = group(""), wallBack = group(""), wallLeft = group("");
    function block(g, x, y, z, w, h, d, tint = C.oak, ry = 0, rz = 0) {
      if (!batches.has(g)) batches.set(g, []);
      batches.get(g).push({ x, y, z, w, h, d, tint, ry, rz }); voxelCount++;
    }
    function cube(g, x, y, z, size, tint) { block(g, x, y, z, size, size, size, tint); }
    function plant(g, x, y, z, s = 1) {
      for (let i = 0; i < 4; i++) block(g, x, y + (.08 + i * .13) * s, z, (.36 + i * .055) * s, .13 * s, (.36 + i * .055) * s, i === 3 ? C.cream : C.rose);
      block(g, x, y + .48 * s, z, .37 * s, .055 * s, .37 * s, C.edge);
      for (let b = 0; b < 7; b++) {
        const a = b * 2.4, height = .72 + (b % 3) * .22;
        for (let t = 0; t < 5; t++) {
          const v = t / 4;
          cube(g, x + Math.cos(a) * v * .34 * s, y + (.48 + height * v * .64) * s, z + Math.sin(a) * v * .34 * s, .105 * s, C.tealDark);
        }
        for (let u = -1; u <= 1; u++) for (let v = 0; v < 3; v++) cube(g, x + (Math.cos(a) * .34 + u * .1) * s, y + (.48 + height * .64 + v * .085) * s, z + (Math.sin(a) * .34 + v * .025) * s, .13 * s, (b + v) % 2 ? C.green : C.leaf);
      }
    }
    function book(g, x, y, z, tint, h = .64, w = .16) {
      block(g, x, y + h / 2, z, w, h, .43, tint);
      block(g, x, y + h / 2, z + .221, w * .62, .024, .012, C.brass);
      block(g, x, y + h - .09, z + .221, w * .72, .022, .012, C.paper);
      block(g, x, y + h - .015, z, w * .7, .018, .38, C.paper);
    }
    function lamp(g, x, y, z, s = 1) {
      block(g, x, y + .06 * s, z, .48 * s, .12 * s, .48 * s, C.brass);
      block(g, x, y + .4 * s, z, .075 * s, .65 * s, .075 * s, C.edge);
      for (let i = 0; i < 5; i++) {
        const w = (.68 - i * .075) * s;
        block(g, x, y + (.6 + i * .085) * s, z, w, .087 * s, w, i % 2 ? "#e8ca87" : "#f9dc9f");
      }
    }
    // Raised plinth, staggered parquet and fine grain, built from blocks.
    block(structure, 0, -.43, .6, 12.05, .54, 10.45, C.tealDark);
    block(structure, 0, -.14, .6, 12.14, .13, 10.52, C.edge);
    for (let row = 0; row < 34; row++) for (let col = 0; col < 9; col++) {
      const x = -5.98 + col * 1.51 + (row % 2 ? -.74 : 0);
      const left = Math.max(-5.98, x), right = Math.min(5.98, x + 1.49);
      if (right <= left) continue;
      const z = -4.43 + row * .3;
      const tint = ["#cda374", "#d6af82", "#c49b6d", "#dfbb90", "#d1a97a"][(row * 3 + col) % 5];
      block(floorG, (left + right) / 2, -.043, z, right - left, .065, .286, tint);
      for (let grain = 0; grain < 3; grain++) block(floorG, (left + right) / 2 + (grain - 1) * .07, -.008, z + (grain - 1) * .063, (right - left) * .68, .003, .008, row % 2 ? "#bb9365" : "#c59e72");
    }
    // Stacked wall courses leave a genuine window opening.
    for (let x = -5.7; x < 5.9; x += .3) for (let y = .16; y < 4.8; y += .3) {
      if (x > -5.2 && x < -1.95 && y > 1.6 && y < 4.25) continue;
      block(wallBack, x, y, -4.47, .294, .294, .22, y < 1.45 ? "#7d9b90" : ["#e6dfca", "#eae3d0", "#e2dbc7"][Math.abs(Math.round(x * 10 + y * 10)) % 3]);
    }
    for (let z = -4.4; z < 5.7; z += .3) for (let y = .15; y < (z > 1.7 ? 1.48 : 4.8); y += .3) block(wallLeft, -5.9, y, z, .22, .293, .294, y < 1.5 ? "#76948c" : "#e5dfcd");
    for (const y of [.18, 1.48, 4.8]) block(wallBack, 0, y, -4.29, 12, .1, .13, y === 4.8 ? C.cream : C.tealDark);
    for (const y of [.18, 1.48]) block(wallLeft, -5.72, y, .6, .13, .1, 10.2, C.tealDark);
    block(wallLeft, -5.88, 4.8, -1.35, .33, .12, 6.1, C.cream);
    for (let x = -5.5; x < 5.8; x += .55) block(wallBack, x, .83, -4.27, .05, 1.16, .08, "#adc0aa");
    for (let z = -4.2; z < 5.7; z += .55) block(wallLeft, -5.74, .83, z, .08, 1.16, .05, "#adc0aa");
    const windowG = group("", -3.6, 2.94, -4.45);
    block(windowG, 0, 0, -.16, 3.25, 2.72, .04, "#9ecacc");
    for (let i = 0; i < 28; i++) {
      const h = .23 + (Math.sin(i * .41) + 1) * .32;
      block(windowG, -1.55 + i * .115, -1.22 + h / 2, -.1, .116, h, .05, "#709c8e");
      if (i % 2) block(windowG, -1.55 + i * .115, -1.25 + h / 4, -.06, .117, h / 2, .04, "#547f73");
    }
    const windowFrame = group("", 0, 0, 0, 0, windowG);
    for (const x of [-1.65, 0, 1.65]) block(windowFrame, x, 0, .06, .11, 2.83, .23, C.cream);
    for (const y of [-1.39, 0, 1.39]) block(windowFrame, 0, y, .07, 3.43, .11, .24, C.cream);
    block(windowFrame, 0, -1.48, .29, 3.7, .14, .68, C.oak);
    for (const side of [-1, 1]) for (let i = 0; i < 6; i++) block(windowFrame, side * (1.46 + i * .085), -.02, .3 + (i % 2) * .065, .095, 3.1 - i * .025, .12, i % 2 ? "#ecd8b8" : "#fff0d0");
    plant(windowG, -1.05, -1.4, .33, .48);

    // Window desk, wall bookcase, clear wardrobe opening, and a rear bed.
    // The centre/right aisle stays open from the cutaway entry to all stations.
    const desk = groups.desk = group("desk", -3.7, 0, -3.45);
    const deskFrame = furnitureBody(desk);
    block(desk, 0, 1.22, 0, 3.3, .18, 1.3, C.oak);
    for (let i = 0; i < 11; i++) block(desk, -1.49 + i * .298, 1.318, 0, .285, .017, 1.27, i % 3 ? "#d8b68b" : "#c9a779");
    for (const x of [-1.42, 1.42]) for (const z of [-.46, .46]) block(deskFrame, x, .58, z, .14, 1.16, .14, C.edge);
    for (let i = 0; i < 3; i++) {
      block(deskFrame, 1.03, .43 + i * .25, .02, .68, .235, 1, C.teal);
      block(deskFrame, 1.03, .43 + i * .25, .55, .19, .05, .06, C.brass);
    }
    for (const side of [-1, 1]) {
      block(desk, side * .3 - .12, 1.365, .07, .59, .055, .76, C.rose, 0, side * .045);
      for (let page = 0; page < 4; page++) block(desk, side * .29 - .12, 1.397 + page * .009, .07, .54, .008, .70, C.paper, 0, side * .035);
      for (let line = 0; line < 6; line++) block(desk, side * .29 - .12, 1.442, -.17 + line * .073, .38, .007, .012, "#acb3a0");
    }
    block(desk, .5, 1.37, .28, .04, .045, .57, C.navy, -.26);
    lamp(desk, -1.13, 1.32, -.24, .76);
    block(desk, .77, 1.49, -.36, .26, .34, .26, C.cream);
    for (let i = 0; i < 4; i++) block(desk, .69 + i * .05, 1.76, -.37, .027, .41, .03, i % 2 ? C.rose : C.navy, 0, (i - 1) * .1);
    for (let i = 0; i < 3; i++) block(desk, .34 + i * .02, 1.35 + i * .028, -.42, .3, .023, .23, [C.mustard, C.rose, C.tealLight][i]);
    const chair = group("desk", -.4, 0, 1.27, -.18, desk);
    block(chair, 0, .7, 0, .87, .17, .82, C.teal);
    for (const x of [-.34, .34]) for (const z of [-.29, .29]) block(chair, x, .34, z, .1, .68, .1, C.edge);
    for (let i = 0; i < 5; i++) block(chair, -.35 + i * .176, 1.18, .33, .12, .86, .12, C.teal);
    block(chair, 0, 1.59, .33, .9, .13, .16, C.tealDark);
    const calendar = groups.calendar = group("calendar", -5.69, 2.84, -2.98, Math.PI / 2);
    block(calendar, 0, 0, 0, 1.9, 1.72, .09, C.edge);
    block(calendar, 0, 0, .06, 1.72, 1.54, .08, "#b59265");
    block(calendar, -.25, .11, .12, 1.0, 1.23, .07, C.paper);
    block(calendar, -.25, .63, .17, 1.01, .23, .05, C.rose);
    for (let col = 0; col < 7; col++) for (let row = 0; row < 4; row++) block(calendar, -.65 + col * .128, .34 - row * .18, .17, .065, .064, .012, row === 1 && col === 3 ? C.rose : "#b6b5a2");
    for (let i = 0; i < 3; i++) {
      block(calendar, .58, .46 - i * .43, .16, .4, .32, .03, [C.mustard, C.tealLight, C.cream][i], 0, (i - 1) * .08);
      cube(calendar, .58, .56 - i * .43, .19, .043, C.rose);
    }
    const shelf = groups.shelf = group("shelf", -5.19, 0, -.1, Math.PI / 2);
    const shelfFrame = furnitureBody(shelf);
    block(shelfFrame, 0, 1.74, -.32, 2.68, 3.45, .09, C.edge);
    for (const x of [-1.37, 1.37]) block(shelfFrame, x, 1.77, 0, .16, 3.54, .78, C.oak);
    for (let row = 0; row < 5; row++) {
      block(shelf, 0, .18 + row * .84, 0, 2.9, .12, .85, C.oak);
      if (row === 4) continue;
      for (let b = 0; b < (row === 2 ? 6 : 11); b++) book(shelf, -1.17 + b * .228, .245 + row * .84, .035, [C.teal, C.rose, C.navy, C.mustard, C.cream][(b + row * 2) % 5], .49 + ((b * 7 + row) % 4) * .065, .15 + (b % 2) * .025);
      if (row === 2) {
        for (let i = 0; i < 3; i++) block(shelf, .7, 1.99 + i * .12, .1, .65, .10, .42, i % 2 ? C.tealLight : C.cream);
        block(shelf, .7, 2.42, .1, .35, .13, .3, C.brass);
      }
    }
    plant(shelf, .87, 3.6, 0, .68);
    for (let i = 0; i < 3; i++) block(shelf, -.67, 3.67 + i * .12, 0, .66, .1, .47, [C.teal, C.paper, C.rose][i]);
    const wardrobe = groups.wardrobe = group("wardrobe", -.25, 0, -3.63);
    block(wardrobe, 0, 1.68, -.39, 2.55, 3.25, .1, C.tealDark);
    for (const x of [-1.32, 1.32]) block(wardrobe, x, 1.7, 0, .16, 3.4, 1.05, C.teal);
    for (const y of [.2, 3.4]) block(wardrobe, 0, y, 0, 2.85, .18, 1.15, C.teal);
    block(wardrobe, 0, 2.83, 0, 2.53, .06, .06, C.brass);
    for (let i = 0; i < 6; i++) {
      const x = -.91 + i * .36;
      block(wardrobe, x, 2.67, 0, .04, .28, .04, C.brass);
      for (let r = 0; r < 9; r++) block(wardrobe, x, 2.48 - r * .125, .04, .21 + (r < 2 ? .1 : r * .009), .122, .51, [C.rose, C.cream, C.navy, C.mustard, C.tealLight, C.paper][i]);
    }
    for (const x of [-.67, .67]) {
      block(wardrobe, x, .51, 0, 1.15, .5, .89, C.oak); block(wardrobe, x, .54, .48, .26, .06, .06, C.brass);
    }
    for (const side of [-1, 1]) {
      const door = group("wardrobe", side * 1.31, 0, .56, 0, wardrobe); doors.push({ group: door, side });
      const doorBody = furnitureBody(door);
      block(doorBody, -side * .647, 1.84, 0, 1.28, 2.92, .095, C.teal);
      for (let i = 0; i < 7; i++) block(doorBody, -side * (.1 + i * .175), 1.84, .052, .025, 2.68, .025, "#6b9890");
      for (const y of [.51, 3.16]) block(doorBody, -side * .65, y, .075, 1.12, .055, .035, C.tealLight);
      block(door, -side * 1.15, 1.74, .11, .054, .23, .07, C.brass);
    }
    for (let i = 0; i < 2; i++) block(wardrobe, -.48 + i * 1.1, 3.7, -.05, .85, .43, .73, i ? C.mustard : C.cream);
    const bed = groups.bed = group("bed", 3.9, 0, -2.2);
    for (const x of [-1.18, 1.18]) for (const z of [-1.64, 1.64]) block(bed, x, .21, z, .17, .43, .17, C.edge);
    block(bed, 0, .43, 0, 2.81, .24, 3.78, C.oak);
    const headboard = furnitureBody(bed);
    for (let i = 0; i < 13; i++) block(headboard, -1.27 + i * .212, 1.05, -1.86, .18, 1.44 - Math.abs(i - 6) * .023, .15, C.teal);
    for (let layer = 0; layer < 4; layer++) block(bed, 0, .57 + layer * .07, 0, 2.65 - (layer === 3 ? .12 : 0), .072, 3.59, layer % 2 ? C.paper : "#e1d5b9");
    for (let ix = 0; ix < 22; ix++) for (let iz = 0; iz < 24; iz++) {
      const x = -1.32 + ix * .126, z = -.87 + iz * .115;
      const y = .9 - Math.max(0, Math.abs(x) - 1.12) * 1.5 + Math.sin(ix * .68) * .024;
      cube(bed, x, y, z, .126, iz % 8 === 0 || ix % 8 === 0 ? "#a9c3ac" : (ix + iz) % 3 ? "#6e9990" : "#82a89a");
    }
    for (const px of [-.68, .67]) for (let x = 0; x < 9; x++) for (let z = 0; z < 5; z++) cube(bed, px - .44 + x * .11, .96 + (x > 0 && x < 8 && z > 0 && z < 4 ? .07 : 0), -1.51 + z * .11, .118, px < 0 ? C.paper : "#d6bd99");
    for (let x = 0; x < 24; x++) block(bed, -1.39 + x * .12, .94, 1.39, .115, .055, .48, x % 4 < 2 ? C.rose : "#dc9b79");
    block(bed, -1.98, .5, -1.09, .75, .94, .85, C.oak);
    block(bed, -1.98, .52, -.637, .57, .65, .07, C.cream); cube(bed, -1.98, .59, -.576, .075, C.brass);
    lamp(bed, -1.98, 1.01, -1.09, .79);
    const rug = furnitureBody(groups.rug = group("rug", -1.65, 0, 1.62));
    for (let x = -22; x <= 22; x++) for (let z = -16; z <= 16; z++) {
      if ((x / 23) ** 2 + (z / 17) ** 2 > 1) continue;
      const edge = (x / 23) ** 2 + (z / 17) ** 2 > .78;
      block(rug, x * .115, .025, z * .115, .112, .036, .112, edge ? ((x + z) % 3 ? C.rose : C.mustard) : (x + z) % 4 ? "#e5d8b8" : "#dbcaab");
    }
    const radio = groups.radio = group("radio", -5.12, 0, 3.0, Math.PI / 2);
    const radioBody = furnitureBody(radio);
    for (const x of [-.94, .94]) for (const z of [-.34, .34]) block(radioBody, x, .17, z, .12, .34, .12, C.edge);
    block(radioBody, 0, .64, 0, 2.34, .78, .95, C.oak);
    for (let i = 0; i < 16; i++) block(radioBody, -.97 + i * .129, .65, .495, .06, .58, .07, C.edge);
    block(radio, -.35, 1.37, -.02, 1.3, .64, .5, C.tealDark);
    block(radio, -.55, 1.38, .25, .69, .44, .035, C.cream);
    for (let x = 0; x < 7; x++) for (let y = 0; y < 4; y++) cube(radio, -.81 + x * .088, 1.24 + y * .091, .278, .027, C.edge);
    block(radio, .055, 1.45, .253, .24, .15, .04, C.mustard);
    for (const x of [-.01, .13]) cube(radio, x, 1.23, .29, .09, C.brass);
    block(radio, -.86, 1.99, -.09, .027, .72, .027, C.brass, 0, -.22);
    for (let i = 0; i < 4; i++) block(radio, .73, 1.09 + i * .075, .04, .49, .057, .51, i % 2 ? C.paper : C.rose);
    plant(radio, .71, 1.35, -.03, .47);
    const garden = groups.garden = group("garden", 4.4, 0, 3.4);
    const gardenFrame = furnitureBody(garden);
    for (let i = 0; i < 3; i++) {
      const y = .18 + i * .26, x = -.62 + i * .55;
      block(gardenFrame, x, y, -.13, .62, .09, .8, C.oak);
      for (const z of [-.45, .17]) block(gardenFrame, x, y / 2, z, .07, y, .07, C.edge);
      plant(garden, x, y + .05, -.11, [.73, .92, 1.15][i]);
    }
    // Reading chair faces its side table; neither occupies the main aisle.
    const lounge = furnitureBody(groups.lounge = group("lounge", -2.85, 0, 1.3, Math.PI / 2));
    for (const x of [-.57, .57]) for (const z of [-.5, .5]) block(lounge, x, .17, z, .13, .34, .13, C.edge);
    block(lounge, 0, .46, 0, 1.44, .25, 1.5, C.oak);
    for (let x = 0; x < 10; x++) for (let z = 0; z < 9; z++) cube(lounge, -.58 + x * .13, .69 + (x > 0 && x < 9 && z > 0 && z < 8 ? .04 : 0), -.48 + z * .13, .132, (x + z) % 4 ? "#c89669" : "#d4a87a");
    for (let i = 0; i < 11; i++) block(lounge, -.65 + i * .13, 1.16, -.64, .125, 1.03, .2, i % 3 ? "#bd895d" : "#c89669");
    for (const x of [-.76, .76]) {
      block(lounge, x, .8, 0, .2, .75, 1.45, C.oak);
      block(lounge, x, 1.2, .02, .24, .11, 1.51, C.edge);
    }
    for (let x = 0; x < 5; x++) for (let y = 0; y < 5; y++) cube(lounge, -.25 + x * .115, .92 + y * .115, -.4, .118, (x + y) % 3 ? C.cream : C.mustard);
    const readingLamp = furnitureBody(groups.readingLamp = group("readingLamp", -4.1, 0, .16));
    lamp(readingLamp, 0, 1.13, 0, 1.08);
    block(readingLamp, 0, .61, 0, .07, 1.25, .07, C.edge);
    block(readingLamp, 0, .065, 0, .5, .13, .5, C.brass);
    const stool = furnitureBody(groups.stool = group("stool", -.95, 0, 1.6));
    for (let i = 0; i < 5; i++) block(stool, 0, .16 + i * .095, 0, .95 - Math.abs(i - 2) * .09, .095, .83, i % 2 ? "#c89963" : "#d9b486");
    block(stool, 0, .67, 0, 1.25, .1, 1.0, C.edge); block(stool, -.14, .75, .03, .74, .045, .56, C.oak);
    for (const x of [-.32, .02]) {
      block(groups.stool, x, .83, .02, .19, .13, .19, C.paper); block(groups.stool, x, .90, .02, .13, .015, .13, C.edge);
    }
    const art = groups.art = group("art", 3.92, 2.99, -4.25);
    block(art, 0, 0, 0, .58, .82, .08, C.edge); block(art, 0, 0, .05, .46, .68, .04, C.paper);
    for (let i = 0; i < 6; i++) cube(art, -.12 + (i % 3) * .09, -.2 + i * .075, .087, .09, i % 2 ? C.green : C.mustard);

    // A real opening on the short right return wall. The leaf swings inward
    // from the rear jamb, leaving the bed and central walking aisle clear.
    const entrance = groups.entrance = group("entrance", 5.89, 0, 1.25, -Math.PI / 2);
    const entranceFrame = group("entrance", 0, 0, 0, 0, entrance);
    for (const x of [-1.25, 1.25]) {
      for (let y = .15; y < 3.6; y += .3) block(entranceFrame, x, y, 0, .3, .294, .24, y < 1.5 ? "#7d9b90" : "#eae3d0");
      block(entranceFrame, x * .87, 1.65, .02, .13, 3.35, .33, C.cream);
    }
    block(entranceFrame, 0, 3.48, 0, 2.8, .3, .28, C.cream);
    block(entranceFrame, 0, 3.29, .02, 2.28, .13, .33, C.oak);
    block(entranceFrame, 0, .025, -.16, 2.2, .05, .58, C.edge);
    for (let i = 0; i < 3; i++) block(entranceFrame, 0, -.13 - i * .17, -.54 - i * .36, 2.48, .17, .4, "#c6cab9");
    const entranceLeaf = group("entrance", -1.04, 0, .03, 0, entrance);
    const entranceDoorBody = group("entrance", 0, 0, 0, 0, entranceLeaf);
    block(entranceDoorBody, 1.025, 1.63, 0, 2.03, 3.2, .13, C.tealDark);
    for (const x of [.17, 1.88]) block(entranceDoorBody, x, 1.63, .08, .095, 2.94, .045, C.oak);
    for (const y of [.25, 1.33, 3.02]) block(entranceDoorBody, 1.025, y, .08, 1.8, .08, .04, C.oak);
    for (let i = 0; i < 8; i++) block(entranceDoorBody, .32 + i * .2, 2.18, .075, .14, 1.47, .025, "#537e73");
    block(entranceLeaf, 1.74, 1.54, .15, .08, .28, .07, C.brass);
    block(entranceLeaf, 1.63, 1.61, .23, .3, .055, .07, C.brass);
    block(entrance, 0, .06, .63, 1.76, .045, .66, C.rose);
    for (let i = 0; i < 10; i++) block(entrance, -.76 + i * .169, .086, .63, .02, .006, .54, C.cream);

    // Each style changes both the finish and the furniture silhouette/details.
    for (const [id, root] of Object.entries(groups)) {
      if (!CATALOG[id]) continue;
      styleGroups[id] = {};
      if (OBJECT_MODELS[id]) continue;
      for (const style of ["cream", "ink"]) {
        const g = group(id, 0, 0, 0, 0, root); g.visible = false; styleGroups[id][style] = g;
        const soft = style === "cream", tint = soft ? C.cream : C.navy;
        if (id === "bed") {
          for (let i = 0; i < (soft ? 3 : 9); i++) block(g, soft ? -.84 + i * .84 : -1.2 + i * .3, 1.18, -1.73, soft ? .8 : .2, soft ? 1.45 : 1.65 - Math.abs(i - 4) * .1, .23, tint);
          block(g, 0, .26, 0, 2.64, .24, 3.58, soft ? C.paper : C.navy);
        } else if (id === "lounge") {
          block(g, 0, 1.17, -.49, 1.23, soft ? 1.12 : 1.48, .25, tint);
          for (const x of [-.76, .76]) block(g, x, .93, .05, .25, soft ? .62 : .34, 1.48, tint);
        } else if (id === "desk") {
          block(g, 0, .95, -.47, 2.64, .36, .12, tint);
          if (!soft) for (const x of [-1.43, 1.43]) block(g, x, .32, 0, .16, .07, 1.14, C.brass);
          else block(g, 1.04, 1.17, .02, .74, .13, 1.07, C.cream);
        } else if (id === "shelf") {
          for (let row = 0; row < 4; row++) block(g, (row % 2 ? 1 : -1) * .27, .59 + row * .84, .05, .075, .72, .72, tint);
          block(g, 0, 3.64, 0, soft ? 2.9 : 3.12, .13, soft ? .87 : 1, tint);
        } else if (id === "wardrobe") {
          block(g, 0, 3.51, 0, soft ? 2.86 : 3.06, soft ? .1 : .18, 1.2, tint);
          for (const door of doors) {
            const panel = group(id, 0, 0, 0, 0, door.group); panel.visible = false;
            (g.userData.doorPanels ||= []).push(panel);
            for (let i = 0; i < 3; i++) block(panel, -door.side * .65, .98 + i * .87, .077, 1.08, soft ? .71 : .025, .035, tint);
          }
        } else if (id === "stool") {
          block(g, 0, .7, 0, soft ? 1.38 : 1.27, .12, soft ? 1.1 : 1.04, tint);
          for (const x of [-.5, .5]) block(g, x, .28, 0, .09, .53, .78, soft ? C.cream : C.brass);
        } else if (id === "radio") {
          block(g, 0, 1.04, 0, 2.5, .12, 1.06, tint);
          for (const x of [-1.06, 1.06]) block(g, x, .63, .55, .07, .7, .1, soft ? C.cream : C.brass);
        } else if (id === "readingLamp") {
          for (let i = 0; i < 3; i++) block(g, 0, 1.95 + i * .1, 0, soft ? .62 : .8 - i * .18, .1, soft ? .62 : .8 - i * .18, tint);
        } else if (id === "garden") {
          for (let i = 0; i < 3; i++) block(g, -.62 + i * .55, .1 + i * .13, -.13, soft ? .42 : .08, .18 + i * .26, .56, tint);
        } else if (id === "rug") {
          for (let i = -15; i <= 15; i++) block(g, i * .14, .05, 0, .06, .014, 2.4 * Math.sqrt(1 - (i / 18) ** 2), soft ? C.paper : (i % 3 ? C.navy : C.brass));
        }
      }
    }
    function beam(g, from, to, thickness, tint) {
      const a = new THREE.Vector3(...from), b = new THREE.Vector3(...to), length = a.distanceTo(b), steps = Math.ceil(length / (thickness * .7));
      for (let i = 0; i <= steps; i++) { const p = a.clone().lerp(b, i / steps); cube(g, p.x, p.y, p.z, thickness, tint); }
    }
    function lattice(g, x, y, z, w, h, tint) {
      for (let i = 0; i <= Math.floor(w / .13); i++) block(g, x - w / 2 + i * .13, y, z, .035, h, .038, tint);
      for (let i = 0; i <= Math.floor(h / .13); i++) block(g, x, y - h / 2 + i * .13, z + .021, w, .035, .025, tint);
    }
    function roundSlab(g, x, y, z, rx, rz, h, tint, petals = 0) {
      for (let u = -rx; u <= rx; u += .11) for (let v = -rz; v <= rz; v += .11) {
        const radius = Math.hypot(u / rx, v / rz), edge = petals ? .88 + .12 * Math.cos(Math.atan2(v / rz, u / rx) * petals) : 1;
        if (radius <= edge) block(g, x + u, y, z + v, .113, h, .113, tint);
      }
    }
    function makeNewStyle(id, style) {
      const root = groups[id], f = FINISHES[style];
      const g = group(id, 0, 0, 0, 0, root); g.visible = false; g.userData.variant = style; styleGroups[id][style] = g;
      const rattan = style === "rattan", walnut = style === "walnut", loft = style === "loft", blossom = style === "blossom";
      if (id === "desk") {
        for (const x of [-1.39, 1.39]) {
          if (loft) {
            for (const z of [-.46, .46]) block(g, x, .58, z, .1, 1.16, .1, f.edge);
            block(g, x, .08, 0, .11, .1, 1.02, f.edge); beam(g, [x, .14, -.4], [x, 1.13, .4], .07, f.edge);
          } else if (style === "nordic") {
            for (const z of [-.48, .48]) beam(g, [x, .08, z], [x, 1.17, 0], .11, f.wood);
            block(g, x, .43, 0, .09, .09, .69, f.edge);
          } else for (const z of [-.43, .43]) beam(g, [x, .07, z], [x * .95, 1.17, z * .85], blossom ? .14 : .11, f.edge);
        }
        block(g, walnut || blossom ? 0 : .99, 1, .03, walnut || blossom ? 2.55 : .81, .33, .98, f.wood);
        if (rattan) lattice(g, .99, 1, .54, .69, .25, f.light);
        else for (let i = 0; i < (walnut || blossom ? 3 : 1); i++) {
          const x = walnut || blossom ? -.85 + i * .85 : .99;
          block(g, x, 1, .535, walnut || blossom ? .79 : .68, .27, .05, f.accent);
          block(g, x, 1, .58, blossom ? .09 : .2, .045, .05, blossom ? C.cream : C.brass);
        }
        if (loft) beam(g, [-1.28, .18, -.47], [1.28, 1.12, -.47], .065, f.edge);
      } else if (id === "shelf") {
        for (const x of [-1.36, 1.36]) for (const z of [-.31, .3]) block(g, x, 1.77, z, loft ? .085 : .12, 3.54, .12, f.edge);
        if (rattan) lattice(g, 0, 1.81, -.35, 2.59, 3.25, f.wood);
        else if (loft) {
          beam(g, [-1.27, .26, -.35], [1.27, 3.5, -.35], .06, f.edge);
          beam(g, [1.27, .26, -.35], [-1.27, 3.5, -.35], .06, f.edge);
        } else {
          block(g, 0, 1.78, -.36, 2.63, 3.38, .075, walnut ? f.dark : f.light);
          for (let i = 0; i < 4; i++) block(g, (i % 2 ? 1 : -1) * .52, .6 + i * .84, .04, .09, .73, .68, f.wood);
        }
        if (blossom) for (let i = -6; i <= 6; i++) block(g, i * .21, 3.5 + .22 * Math.sqrt(1 - (i / 7) ** 2), 0, .205, .18, .79, f.accent);
        if (walnut) { block(g, 0, 3.52, 0, 2.85, .15, .9, f.wood); block(g, 0, .06, 0, 2.7, .12, .8, f.dark); }
      } else if (id === "wardrobe") {
        for (const door of doors) {
          const panel = group(id, 0, 0, 0, 0, door.group), x = -door.side * .647;
          panel.visible = false; panel.userData.variant = style; (g.userData.doorPanels ||= []).push(panel);
          block(panel, x, 1.84, -.015, 1.28, 2.92, .08, rattan ? f.dark : f.accent);
          for (const dx of [-.59, .59]) block(panel, x + dx, 1.84, .045, .075, 2.92, .07, f.wood);
          for (const y of [.43, 3.25]) block(panel, x, y, .045, 1.22, .08, .07, f.wood);
          if (rattan) lattice(panel, x, 1.84, .052, 1.1, 2.66, f.light);
          else if (walnut) for (let i = 0; i < 8; i++) block(panel, x - .49 + i * .14, 1.84, .06, .06, 2.6, .04, i % 2 ? f.wood : f.dark);
          else if (loft) {
            for (const dx of [-.53, .53]) for (const y of [.62, 1.4, 2.22, 3.06]) cube(panel, x + dx, y, .087, .06, C.brass);
            for (let i = 0; i < 6; i++) block(panel, x, 2.36 + i * .11, .042, .72, .035, .026, f.dark);
          } else if (blossom) {
            for (let i = -4; i <= 4; i++) block(panel, x + i * .11, 2.01 + .12 * Math.sqrt(1 - (i / 5) ** 2), .05, .105, 1.84 + .24 * Math.sqrt(1 - (i / 5) ** 2), .04, f.light);
            for (let i = 0; i < 4; i++) cube(panel, x + Math.cos(i * Math.PI / 2) * .1, 2.66 + Math.sin(i * Math.PI / 2) * .1, .09, .12, C.cream);
          } else { block(panel, x - door.side * .23, 1.84, .045, .53, 2.74, .04, f.light); block(panel, x, .97, .075, 1.12, .06, .03, f.wood); }
        }
        if (walnut || blossom) block(g, 0, 3.49, 0, 2.92, .12, 1.16, f.wood);
      } else if (id === "bed") {
        if (rattan) {
          block(g, 0, 1.22, -1.87, 2.65, 1.68, .08, f.dark); lattice(g, 0, 1.26, -1.8, 2.42, 1.38, f.light);
          for (const x of [-1.3, 1.3]) block(g, x, 1.14, -1.83, .11, 1.94, .15, f.wood);
          for (const y of [.53, 2.03]) block(g, 0, y, -1.8, 2.7, .1, .13, f.wood);
        } else if (loft) {
          for (const x of [-1.27, 1.27]) block(g, x, 1.05, -1.86, .1, 1.91, .1, f.edge);
          for (const y of [.8, 1.98]) block(g, 0, y, -1.86, 2.58, .08, .1, f.edge);
          for (let i = -5; i <= 5; i++) block(g, i * .22, 1.4, -1.86, .055, 1.12, .055, f.edge);
        } else for (let i = -7; i <= 7; i++) {
          const h = blossom ? 1.22 + .6 * Math.sqrt(1 - (i / 8) ** 2) : walnut ? 1.25 + .35 * Math.abs(i / 7) : 1.12 + .5 * (1 - Math.abs(i / 7));
          block(g, i * .175, .43 + h / 2, -1.84, .17, h, blossom ? .26 : .16, blossom ? (i % 3 ? f.accent : f.light) : walnut ? f.wood : i < 0 ? f.accent : f.light);
        }
        if (walnut) for (const x of [-1.35, 1.35]) block(g, x, 1.18, -1.68, .12, 1.5, .4, f.wood);
      } else if (id === "radio") {
        block(g, 0, .98, 0, 2.36, .12, .98, f.wood);
        block(g, 0, .37, 0, 2.2, .1, .88, f.wood);
        for (const x of [-1.06, 1.06]) { block(g, x, .68, 0, .11, .56, .9, f.wood); for (const z of [-.32, .32]) beam(g, [x, .03, z], [x * .94, .38, z * .9], .095, f.edge); }
        for (const x of [-.54, .54]) {
          if (rattan) { block(g, x, .67, .39, .98, .47, .045, f.dark); lattice(g, x, .67, .43, .93, .43, f.light); }
          else {
            block(g, x, .67, .43, .97, .47, .05, walnut ? f.wood : x < 0 ? f.accent : f.light);
            block(g, x + .32, .7, .49, .09, .055, .04, C.brass);
            if (loft) for (const dx of [-.4, .4]) for (const y of [.5, .84]) cube(g, x + dx, y, .48, .05, f.edge);
            if (walnut) for (let i = 0; i < 7; i++) block(g, x - .41 + i * .137, .67, .47, .025, .42, .025, f.light);
          }
        }
      } else if (id === "garden") {
        for (let i = 0; i < 3; i++) {
          const x = -.62 + i * .55, y = .18 + i * .26;
          if (blossom) roundSlab(g, x, y, -.13, .4, .43, .08, i % 2 ? f.light : f.accent, 5);
          else block(g, x, y, -.13, .66, .08, .79, f.wood);
          for (const z of [-.43, .16]) {
            if (style === "nordic") beam(g, [x - .24, .04, z], [x, y, z], .065, f.edge);
            block(g, x + (style === "nordic" ? .24 : 0), y / 2, z, loft ? .06 : .09, y, .07, f.edge);
          }
        }
        if (rattan || loft) { beam(g, [-.88, .1, -.48], [.78, .94, -.48], .065, f.edge); beam(g, [-.88, .08, -.48], [-.88, .6, -.48], .07, f.edge); }
      } else if (id === "lounge") {
        for (const x of [-.55, .55]) for (const z of [-.49, .49]) beam(g, [x, .05, z], [x * .9, .63, z * .83], loft ? .075 : .1, f.edge);
        block(g, 0, .56, 0, 1.36, .18, 1.35, f.wood);
        block(g, 0, .71, .02, 1.25, .19, 1.19, f.fabric);
        if (rattan) { lattice(g, 0, 1.27, -.59, 1.25, 1.02, f.light); for (const x of [-.66, .66]) block(g, x, 1.26, -.6, .09, 1.2, .11, f.wood); block(g, 0, 1.85, -.6, 1.42, .08, .13, f.wood); }
        else if (blossom || walnut) {
          for (let i = -4; i <= 4; i++) { const h = .83 + .45 * Math.sqrt(1 - (i / 5) ** 2); block(g, i * .15, .65 + h / 2, -.55, .145, h, .22, blossom && i % 2 ? f.light : f.accent); }
          for (const x of [-.7, .7]) block(g, x, 1.32, -.38, .16, .88, .48, f.accent);
        } else { block(g, 0, 1.24, -.58, 1.23, .98, .16, f.accent); for (const x of [-.68, .68]) beam(g, [x, .12, .6], [x, 1.8, -.6], loft ? .08 : .12, f.edge); }
        for (const x of [-.75, .75]) { block(g, x, .98, .1, .13, .47, .12, f.edge); block(g, x, 1.19, 0, .2, .1, 1.43, walnut ? f.wood : f.light); }
        block(g, .12, 1.01, -.31, .57, .49, .18, blossom ? C.cream : f.light, 0, -.13);
      } else if (id === "readingLamp") {
        if (style === "nordic") for (let i = 0; i < 3; i++) { const a = i * Math.PI * 2 / 3; beam(g, [Math.cos(a) * .36, .06, Math.sin(a) * .36], [0, 1.78, 0], .07, f.wood); }
        else { roundSlab(g, 0, .07, 0, .3, .3, .12, f.edge); block(g, 0, .91, 0, .07, 1.76, .07, f.edge); }
        if (rattan) {
          for (const y of [1.7, 2.31]) block(g, 0, y, 0, .64, .08, .64, f.wood);
          for (const x of [-.29, .29]) for (let z = -.27; z < .3; z += .09) block(g, x, 2, z, .045, .59, .045, f.wood);
          for (const z of [-.29, .29]) for (let x = -.27; x < .3; x += .09) block(g, x, 2, z, .045, .59, .045, f.wood);
          block(g, 0, 2, 0, .42, .49, .42, "#ffdfa0");
        } else if (loft) {
          beam(g, [0, 1.7, 0], [.35, 2.16, 0], .08, f.edge); cube(g, 0, 1.7, 0, .15, C.brass);
          for (let i = 0; i < 4; i++) block(g, .29, 1.98 + i * .09, 0, .56 - i * .1, .095, .47 - i * .075, f.accent);
        } else for (let i = 0; i < 7; i++) {
          const radius = walnut ? .44 * Math.sqrt(1 - (i / 8) ** 2) : blossom ? .16 + Math.sin(i / 6 * Math.PI) * .19 : .43 - i * .039;
          roundSlab(g, 0, 1.78 + i * .077, 0, radius, radius, .079, i === 0 ? "#ffe2a6" : blossom ? (i % 2 ? f.light : f.accent) : walnut ? "#c58d49" : f.light, blossom ? 5 : 0);
        }
      } else if (id === "stool") {
        if (rattan) {
          for (const z of [-.39, .39]) lattice(g, 0, .35, z, .91, .48, f.light);
          for (const x of [-.47, .47]) block(g, x, .34, 0, .07, .55, .8, f.wood);
          block(g, 0, .69, 0, 1.25, .12, 1.02, f.wood);
        } else {
          if (loft) { block(g, 0, .69, 0, 1.29, .12, 1.02, f.wood); for (const x of [-.52, .52]) { for (const z of [-.39, .39]) block(g, x, .33, z, .07, .59, .07, f.edge); block(g, x, .08, 0, .075, .075, .86, f.edge); } }
          else { roundSlab(g, 0, .69, 0, walnut ? .76 : .7, .56, .11, blossom ? f.light : f.wood, blossom ? 5 : 0); for (let i = 0; i < 3; i++) { const a = i * Math.PI * 2 / 3; beam(g, [Math.cos(a) * .49, .04, Math.sin(a) * .4], [Math.cos(a) * .31, .66, Math.sin(a) * .3], .095, f.edge); } }
          if (walnut) roundSlab(g, 0, .23, 0, .61, .43, .07, f.wood);
        }
      } else if (id === "rug") {
        for (let x = -22; x <= 22; x++) for (let z = -16; z <= 16; z++) {
          const nx = x / 22, nz = z / 16, radius = Math.hypot(nx, nz);
          const inside = blossom ? radius <= .87 + .13 * Math.cos(Math.atan2(nz, nx) * 6) : loft ? true : radius <= 1;
          if (!inside) continue;
          const tint = rattan ? (z % 3 ? f.light : f.wood) : walnut ? ((Math.abs(x) + Math.abs(z)) % 9 < 3 ? f.accent : f.light) : loft ? (Math.floor((x + 22) / 6) + Math.floor((z + 16) / 6)) % 2 ? f.dark : f.fabric : blossom ? radius < .24 ? C.mustard : radius > .68 ? f.accent : f.light : (z + 16 < Math.abs(x) * .8 ? f.light : z % 9 < 3 ? f.dark : f.fabric);
          block(g, x * .113, .027, z * .113, .111, .04, .111, tint);
        }
        if (rattan) for (let i = -11; i <= 11; i++) for (const side of [-1, 1]) block(g, i * .14, .023, side * (1.76 - Math.abs(i) * .038), .045, .028, .24, f.light);
      }
    }
    function makeHouseStyle(id) {
      const f = HOUSE_FINISHES[id], house = group(""); house.visible = false; houseGroups[id] = house;
      const win = group("", 0, 0, 0, 0, windowG), door = group("entrance", 0, 0, 0, 0, entranceLeaf);
      win.visible = false; door.visible = false; house.userData.parts = [win, door];
      const japanese = id === "washitsu", loft = id === "loft", french = id === "french", coastal = id === "coastal", cabin = id === "cabin";
      // All shells share their floor boundary and real window/door openings.
      // Their geometry, trim and floor patterns change without moving furniture.
      const course = cabin ? .28 : loft ? .23 : .3, brickWidth = loft ? .59 : cabin ? 1.47 : .6;
      for (let row = 0, y = course / 2; y < 4.8; row++, y += course) {
        for (let x = -5.98 + (loft && row % 2 ? -brickWidth / 2 : 0); x < 5.99; x += brickWidth) {
          const a = Math.max(-5.98, x), b = Math.min(5.99, x + brickWidth), bottom = y - course / 2, top = y + course / 2;
          const pieces = top > 1.6 && bottom < 4.29 ? [[a, Math.min(b, -5.25)], [Math.max(a, -1.93), b]] : [[a, b]];
          for (const [left, right] of pieces) if (right - left > .018) {
            const tint = loft ? [f.wall, f.lower, "#c08b70", "#aa725e"][Math.abs(row + Math.round(x * 10 + 60)) % 4] : cabin ? [f.wall, f.lower, "#bc956e"][(row + 60 + Math.round(x)) % 3] : y < 1.48 ? f.lower : f.wall;
            block(house, (left + right) / 2, y, -4.47, right - left - .012, course - .012, cabin ? .27 : .22, tint);
          }
        }
        for (let z = -4.5 + (loft && row % 2 ? -brickWidth / 2 : 0); z < 5.7; z += brickWidth) {
          const a = Math.max(-4.5, z), b = Math.min(5.7, z + brickWidth);
          if (y > 1.48 && (a + b) / 2 > 1.7) continue;
          const tint = loft ? [f.wall, f.lower, "#c08b70"][(row + Math.round(z * 10 + 60)) % 3] : y < 1.48 ? f.lower : f.wall;
          block(house, -5.9, y, (a + b) / 2, cabin ? .27 : .22, course - .012, b - a - .012, tint);
        }
      }
      for (const y of [.13, 4.8]) block(house, 0, y, -4.29, 12, cabin ? .2 : .09, cabin ? .3 : .14, f.edge);
      block(house, -5.72, .13, .6, .14, .09, 10.2, f.edge);
      block(house, -5.86, 4.8, -1.35, cabin ? .35 : .2, cabin ? .23 : .1, 6.1, f.edge);
      if (japanese || cabin || loft) {
        for (const x of [-5.77, -1.73, 5.79]) block(house, x, 2.43, -4.25, cabin ? .22 : .13, 4.8, cabin ? .28 : .14, f.edge);
        for (const z of [-4.36, 1.62]) block(house, -5.7, 2.41, z, .16, 4.7, cabin ? .22 : .14, f.edge);
      }
      if (loft) {
        // Riveted lintels and slim exterior braces stay clear of the room aisle.
        for (const x of [-5.78, -1.73, 5.78]) for (let y = .35; y < 4.65; y += .52) cube(house, x, y, -4.16, .065, "#a1a69c");
        for (const y of [4.44, 4.65]) block(house, 0, y, -4.29, 11.84, .055, .18, f.edge);
      }
      if (french) {
        for (const y of [.25, 1.48, 4.54, 4.7]) { block(house, 0, y, -4.28, 11.85, .07, .08, "#fff4df"); block(house, -5.71, Math.min(y, 1.48), .6, .08, .07, 10.06, "#fff4df"); }
        for (let x = -5.23; x < 5.6; x += 1.32) {
          for (const dx of [-.5, .5]) block(house, x + dx, .86, -4.23, .032, .97, .026, "#fff3db");
          for (const y of [.37, 1.34]) block(house, x, y, -4.23, 1.03, .03, .026, "#fff3db");
        }
        for (const x of [2.35, 4.68]) {
          for (const dx of [-.96, .96]) block(house, x + dx, 3.07, -4.26, .04, 2.17, .05, "#fff5e3");
          for (const y of [1.97, 4.17]) block(house, x, y, -4.26, 1.96, .04, .05, "#fff5e3");
        }
      }
      if (coastal) {
        block(house, 0, 1.5, -4.26, 11.85, .075, .12, f.wood);
        block(house, -5.7, 1.5, .6, .12, .075, 10.09, f.wood);
        for (let x = -5.7; x < 5.9; x += .36) block(house, x, .78, -4.27, .026, 1.3, .03, "#bccdc2");
      }
      if (japanese) {
        for (let row = 0; row < 7; row++) for (let col = 0; col < 4; col++) {
          const x = -4.47 + col * 2.98, z = -3.75 + row * 1.455;
          block(house, x, -.038, z, 2.96, .065, 1.435, f.edge);
          block(house, x, -.006, z, 2.81, .012, 1.285, f.floor[(row + col) % 3]);
          for (let grain = 0; grain < 11; grain++) block(house, x, .001, z - .55 + grain * .11, 2.76, .004, .018, "#a9a578");
        }
      } else if (loft || coastal) {
        const size = loft ? 1.49 : .596;
        for (let row = 0; row < (loft ? 7 : 17); row++) for (let col = 0; col < (loft ? 8 : 20); col++) {
          const x = -5.96 + size / 2 + col * size, z = -4.47 + size / 2 + row * size;
          const border = coastal && (row === 0 || row === 16 || col === 0 || col === 19);
          const depth = Math.min(size, 5.68 - (z - size / 2));
          block(house, x, -.032, z - (size - depth) / 2, size - .017, .05, depth - .017, border ? f.edge : f.floor[(row * 2 + col) % 3]);
          if (border) block(house, x, -.004, z, .2, .006, .2, "#eee9d5", Math.PI / 4);
        }
      } else {
        for (let row = 0; row < 23; row++) for (let col = 0; col < 8; col++) {
          const x = -5.96 + col * 1.49, z = -4.46 + row * .442;
          if (french) {
            const parquet = (row + col) % 2 === 0;
            for (let strip = 0; strip < 3; strip++) block(house, x + .745 + (parquet ? (strip - 1) * .49 : 0), -.032, z + .223 + (parquet ? 0 : (strip - 1) * .145), parquet ? .48 : 1.47, .05, parquet ? .432 : .139, f.floor[strip]);
          } else {
            block(house, x + .745, -.032, z + .223, 1.474, .05, .432, f.floor[(row + col) % 3]);
            for (const offset of [-.12, .12]) block(house, x + .745, -.005, z + .223 + offset, 1.2, .004, .009, f.wood);
          }
        }
      }
      // Window frames use the same opening as the original window scenery.
      for (const x of [-1.65, 1.65]) block(win, x, 0, .1, cabin ? .2 : .12, 2.9, .28, f.wood);
      for (const y of [-1.4, 1.4]) block(win, 0, y, .1, 3.5, cabin ? .18 : .12, .28, f.wood);
      block(win, 0, -1.48, .24, 3.7, .14, .68, f.wood);
      if (japanese) {
        block(win, 0, 0, .014, 3.19, 2.67, .03, "#eee9cb"); lattice(win, 0, 0, .11, 3.15, 2.62, f.wood);
      } else if (french) {
        for (const x of [-.52, .52]) block(win, x, -.22, .15, .055, 2.25, .08, "#fff5df");
        for (let i = -14; i <= 14; i++) {
          const u = i / 14, arc = .62 + .72 * Math.sqrt(Math.max(0, 1 - u * u));
          block(win, u * 1.53, arc, .17, .117, .12, .12, "#fff6e2");
          if (arc < 1.28) block(win, u * 1.53, (arc + 1.36) / 2, .06, .114, 1.36 - arc, .1, f.wall);
        }
        block(win, 0, -.24, .15, 3.16, .055, .08, "#fff5df");
      } else {
        for (let i = 1; i < (loft ? 5 : 3); i++) block(win, -1.65 + i * 3.3 / (loft ? 5 : 3), 0, .14, loft ? .05 : .07, 2.7, .09, f.edge);
        for (const y of (loft ? [-.68, 0, .68] : [0])) block(win, 0, y, .14, 3.24, .06, .09, f.edge);
      }
      if (coastal) for (const side of [-1, 1]) {
        block(win, side * 1.85, 0, .25, .38, 2.88, .11, f.edge);
        for (let i = 0; i < 19; i++) block(win, side * 1.85, -1.29 + i * .143, .33, .34, .065, .1, f.accent);
      }
      if (cabin) {
        block(win, 0, 1.52, .05, 3.96, .21, .45, f.edge);
        for (const x of [-1.74, 1.74]) for (const y of [-1.32, 1.32]) cube(win, x, y, .27, .09, "#343f37");
      }
      // Only the leaf skin changes. Handle, hinge, animation and extension event stay shared.
      block(door, 1.025, 1.63, 0, 2.03, 3.2, .13, japanese ? "#e2ddbe" : loft ? f.edge : french ? f.wall : f.wood);
      for (const x of [.08, 1.97]) block(door, x, 1.63, .08, .09, 3.12, .06, f.edge);
      for (const y of [.11, 3.16]) block(door, 1.025, y, .08, 1.95, .09, .06, f.edge);
      if (japanese) lattice(door, 1.025, 1.67, .087, 1.77, 2.83, f.wood);
      else if (french) for (const [cy, h] of [[.76, .96], [2.19, 1.36]]) {
        for (const x of [.27, 1.77]) block(door, x, cy, .09, .045, h, .04, f.wood);
        for (const y of [cy - h / 2, cy + h / 2]) block(door, 1.02, y, .09, 1.55, .045, .04, f.wood);
      } else if (loft) {
        for (const x of [.21, 1.84]) for (let y = .32; y < 3.1; y += .43) cube(door, x, y, .09, .075, "#a9b3a7");
        for (let i = 0; i < 5; i++) block(door, 1.025, 2.28 + i * .13, .075, 1.24, .038, .025, f.accent);
      } else {
        for (let i = 0; i < 8; i++) block(door, .24 + i * .223, 1.63, .079, .026, 2.94, .028, f.edge);
        if (cabin) { for (const y of [.58, 2.73]) block(door, 1.025, y, .12, 1.8, .13, .08, f.edge); beam(door, [.27, .68, .12], [1.8, 2.63, .12], .1, f.edge); }
      }
      flushBatches();
    }
    function makeObjectModel(id, model) {
      const root = groups[id], g = group(id, 0, 0, 0, 0, root);
      (modelGroups[id] ||= {})[model] = g;
      const plinth = (w = 1.15) => {
        block(g, 0, .76, 0, w, .12, .8, C.oak);
        for (const x of [-1, 1]) for (const z of [-1, 1]) block(g, x * (w / 2 - .12), .37, z * .28, .12, .74, .12, C.edge);
      };
      const screen = (y, z, w = 1.45, h = .9) => {
        block(g, 0, y, z, w + .16, h + .16, .16, C.dark);
        block(g, 0, y, z + .09, w, h, .035, C.tealDark);
        for (let i = 0; i < 8; i++) cube(g, -.52 + i * .14, y - .25 + (i % 3) * .15, z + .115, .1, i % 2 ? C.mustard : C.tealLight);
      };
      const phone = (y, wall) => {
        block(g, 0, y, 0, .8, wall ? .94 : .19, wall ? .22 : .57, C.teal);
        for (let i = 0; i < 10; i++) {
          const a = i * Math.PI / 5;
          cube(g, Math.cos(a) * .16, wall ? y - .07 + Math.sin(a) * .16 : y + .13, wall ? .14 : Math.sin(a) * .16, .065, C.brass);
        }
        block(g, 0, y + (wall ? .3 : .23), wall ? .23 : -.1, .84, .13, .15, C.rose);
        for (const x of [-.34, .34]) block(g, x, y + (wall ? .25 : .19), wall ? .25 : -.1, .2, .23, .24, C.rose);
        for (let i = 0; i < 16; i++) cube(g, .44 + (i % 2) * .06, y - .04 - i * .035, .1, .06, C.dark);
      };
      if (model === "chess") {
        plinth(1.55); block(g, 0, .87, 0, 1.5, .12, 1.3, C.edge);
        for (let x = 0; x < 8; x++) for (let z = 0; z < 8; z++) block(g, -.56 + x * .16, .94, -.56 + z * .16, .155, .025, .155, (x + z) % 2 ? C.cream : C.tealDark);
        for (let x = 0; x < 8; x++) for (const z of [-.56, .56]) {
          block(g, -.56 + x * .16, 1.01, z, .09, .12, .09, z < 0 ? C.cream : C.rose);
          cube(g, -.56 + x * .16, 1.1 + (x % 3) * .015, z, .08, z < 0 ? C.cream : C.rose);
        }
      } else if (model === "arcade") {
        block(g, 0, .64, 0, 1.14, 1.28, .95, C.teal); block(g, 0, 1.43, -.26, 1.14, 1.62, .44, C.teal);
        screen(1.62, .005, .88, .66); block(g, 0, 2.25, -.06, 1.2, .27, .8, C.rose);
        block(g, 0, 1.22, .31, 1.2, .13, .62, C.dark);
        block(g, -.3, 1.38, .37, .055, .22, .055, C.edge); cube(g, -.3, 1.5, .37, .13, C.rose);
        for (let i = 0; i < 3; i++) cube(g, .13 + i * .17, 1.31, .39, .1, i % 2 ? C.mustard : C.tealLight);
        for (let i = 0; i < 6; i++) cube(g, -.4 + i * .16, 2.25, .35, .08, C.cream);
      } else if (model === "console") {
        screen(.1, .03); block(g, 0, -.63, .16, 1.62, .1, .45, C.oak);
        block(g, 0, -.51, .19, .7, .15, .28, C.cream);
        for (const x of [-.22, .22]) cube(g, x, -.42, .3, .1, C.rose);
      } else if (model === "camera" || model === "instant") {
        const y = model === "camera" ? 1.65 : 1.08;
        if (model === "instant") plinth();
        else {
          block(g, 0, .95, 0, .07, 1.18, .07, C.edge);
          for (const [x, z] of [[-.5, .32], [.5, .32], [0, -.5]]) beam(g, [0, 1.25, 0], [x, .07, z], .065, C.edge);
        }
        block(g, 0, y, 0, .88, .57, .37, model === "instant" ? C.cream : C.teal);
        block(g, -.06, y, .26, .37, .37, .23, C.dark); block(g, -.06, y, .39, .23, .23, .045, C.tealLight);
        block(g, -.21, y + .33, -.02, .28, .11, .2, C.dark); cube(g, .28, y + .3, 0, .12, C.rose);
        if (model === "instant") { block(g, 0, y - .36, .23, .6, .025, .45, C.paper); block(g, 0, y - .34, .23, .45, .012, .29, C.tealLight); }
      } else if (model === "gallery") {
        block(g, 0, .48, 0, 1.75, .065, .1, C.edge);
        for (let i = -1; i <= 1; i++) {
          block(g, i * .57, .07 - (i % 2) * .1, .04, .49, .67, .07, C.cream);
          block(g, i * .57, .12 - (i % 2) * .1, .085, .37, .43, .02, i === 0 ? C.tealLight : C.rose);
          cube(g, i * .57, .44, .08, .09, C.brass);
        }
      } else if (model === "telephone") { plinth(); phone(1.02, false); }
      else if (model === "wallPhone") { phone(0, true); block(g, 0, 0, -.15, 1.08, 1.44, .1, C.oak); }
      flushBatches();
    }
    for (const [id, x, z] of [["game", .6, 3.5], ["image", 2.9, 4.65], ["together", -2.2, 4.5]]) {
      groups[id] = group(id, x, 0, z); styleGroups[id] = {};
      makeObjectModel(id, Object.keys(OBJECT_MODELS[id])[0]);
    }
    function flushBatches() {
      for (const [g, blocks] of batches) {
      const mesh = new THREE.InstancedMesh(geometry, material, blocks.length);
      blocks.forEach((b, i) => {
        position.set(b.x, b.y, b.z); scale.set(b.w, b.h, b.d);
        quaternion.setFromEuler(new THREE.Euler(0, b.ry, b.rz)); matrix.compose(position, quaternion, scale);
        mesh.setMatrixAt(i, matrix); mesh.setColorAt(i, color.set(b.tint));
      });
      mesh.castShadow = true; mesh.receiveShadow = true; mesh.userData.station = g.userData.station;
      mesh.userData.baseColors = blocks.map(b => b.tint);
      mesh.userData.variant = g.userData.variant || "";
      g.add(mesh); allMeshes.push(mesh);
      }
      batches.clear(); container.dataset.voxelCount = String(voxelCount);
    }
    flushBatches();
    const hemi = new THREE.HemisphereLight("#e7f2e9", "#716448", 2.5);
    const sun = new THREE.DirectionalLight("#fff0ce", 4.1); sun.position.set(-3, 9, 6);
    sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048);
    Object.assign(sun.shadow.camera, { left: -10, right: 10, top: 10, bottom: -10, near: .5, far: 35 });
    sun.shadow.normalBias = .024; sun.shadow.bias = -.00015;
    const fill = new THREE.DirectionalLight("#b7e2dd", 1.1); fill.position.set(7, 5, -1);
    const bedsideLight = new THREE.PointLight("#ffd094", 10, 7, 2); bedsideLight.position.set(-1.98, 1.7, -1.09); bed.add(bedsideLight);
    const deskLight = new THREE.PointLight("#ffcd83", 7, 5, 2); deskLight.position.set(-1.13, 2, -.24); desk.add(deskLight);
    scene.add(hemi, sun, fill);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(180, 180), new THREE.MeshStandardMaterial({ color: "#d8dfd2", roughness: 1 }));
    ground.rotation.x = -Math.PI / 2; ground.position.y = -.74; ground.receiveShadow = true; scene.add(ground);
    const selection = new THREE.Box3Helper(new THREE.Box3(), "#dfb55d");
    selection.material.depthTest = true; selection.material.transparent = true; selection.material.opacity = .32;
    selection.visible = false; selection.renderOrder = 8; scene.add(selection);
    const hoverTitle = document.createElement("div");
    hoverTitle.className = "home-room-hover-title"; hoverTitle.hidden = true;
    hoverTitle.setAttribute("role", "tooltip"); container.appendChild(hoverTitle);
    const grid = new THREE.GridHelper(12, 48, "#547d71", "#8ba698");
    grid.position.set(0, .04, .6); grid.scale.z = .85; grid.visible = false;
    grid.material.transparent = true; grid.material.opacity = .3; scene.add(grid);
    let disposed = false, active = false, frameId = 0, selected = "overview", hovered = "", renderDirty = true;
    let tourRunning = false, shotIndex = -1, tween = null, lastTime = 0, scripted = false;
    let width = 1, height = 1, wardrobeOpen = 0, entranceOpen = false, entranceAmount = 0;
    let editing = false, editId = "lounge", history = [], future = [], drag = null;
    let houseStyle = "classic";
    const styles = {}, models = {}, surfaces = {}, defaults = {}, localBounds = {}, styleBounds = {};
    let placementMessage = "", placementBlocked = false, layoutRepairs = [];
    let reducedMotion = Boolean(options.reducedMotion);
    const media = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const raycaster = new THREE.Raycaster(), framingRay = new THREE.Raycaster(), pointer = new THREE.Vector2();
    const hoverPosition = { x: 0, y: 0 };
    let down = null;
    const floorPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    for (const id of Object.keys(CATALOG)) {
      const root = groups[id]; root.updateWorldMatrix(true, true);
      const inverse = root.matrixWorld.clone().invert(), box = new THREE.Box3();
      root.traverse(obj => {
        if (!obj.isInstancedMesh) return;
        obj.computeBoundingBox(); box.union(obj.boundingBox.clone().applyMatrix4(inverse.clone().multiply(obj.matrixWorld)));
      });
      localBounds[id] = box;
      styleBounds[id] = { legacy: box.clone() };
      models[id] = OBJECT_MODELS[id] ? Object.keys(OBJECT_MODELS[id])[0] : "default";
      surfaces[id] = id === "calendar" ? "left-wall" : id === "art" ? "back-wall" : "floor";
      defaults[id] = { x: root.position.x, y: root.position.y, z: root.position.z, rotation: root.rotation.y * 180 / Math.PI, style: "oak", model: models[id], surface: surfaces[id] };
      styles[id] = "oak";
    }
    // Decorative meshes are deliberately outside the furniture/picking batches.
    // They follow their parent furniture but never change its collision box.
    const ambientMaterials = [];
    function ambientBox(parent, xyz, size, tint, opacity = 1) {
      const mat = new THREE.MeshBasicMaterial({ color: tint, transparent: opacity < 1, opacity, depthWrite: opacity === 1 });
      ambientMaterials.push(mat);
      const mesh = new THREE.Mesh(geometry, mat);
      mesh.position.set(...xyz); mesh.scale.set(...size); parent.add(mesh); return mesh;
    }
    const sky = ambientBox(windowG, [0, 0, -.131], [3.24, 2.71, .014], "#9ecacc");
    const skyOrb = ambientBox(windowG, [.93, .82, -.10], [.30, .30, .025], "#fff0bf");
    const clouds = group("", 0, 0, -.05, 0, windowG);
    for (let i = 0; i < 3; i++) {
      ambientBox(clouds, [-1.03 + i * .81, .76 - (i % 2) * .44, 0], [.55, .13, .025], "#e4edeb");
      ambientBox(clouds, [-1.02 + i * .81, .86 - (i % 2) * .44, 0], [.27, .12, .025], "#e4edeb");
    }
    const precipitation = group("", 0, 0, -.012, 0, windowG);
    const weatherParticles = Array.from({ length: 36 }, (_, i) => ambientBox(precipitation, [((i * .618) % 1) * 2.9 - 1.45, ((i * .37) % 1) * 2.46 - 1.23, 0], [.025, .13, .022], "#d2e7eb", .78));
    const steam = group("", -.32, .94, .02, 0, groups.stool);
    const steamParticles = Array.from({ length: 7 }, (_, i) => ambientBox(steam, [0, i * .1, 0], [.08, .07, .07], "#f9eee0", .35));
    const butterfly = group("", 0, 1.8, 0, 0, groups.garden);
    ambientBox(butterfly, [0, 0, 0], [.07, .20, .07], "#6b533c");
    const wings = [-1, 1].map(side => {
      const wing = group("", side * .04, 0, 0, 0, butterfly);
      ambientBox(wing, [side * .11, .045, 0], [.22, .21, .045], "#e3b15e");
      ambientBox(wing, [side * .08, -.09, 0], [.15, .11, .045], "#e6d5a3");
      ambientBox(wing, [side * .16, .08, .025], [.06, .07, .025], "#825f4e");
      return wing;
    });
    const bird = group("", .86, -1.23, .41, 0, windowG);
    ambientBox(bird, [0, 0, 0], [.30, .25, .23], "#8c9e9a");
    ambientBox(bird, [.14, .13, 0], [.19, .20, .20], "#cabca0");
    ambientBox(bird, [.24, .11, 0], [.12, .065, .10], "#cb9a4c");
    ambientBox(bird, [.19, .17, .105], [.035, .035, .02], "#344a47");
    ambientBox(bird, [-.18, -.01, 0], [.18, .08, .14], "#607d78");
    for (const x of [-.07, .07]) ambientBox(bird, [x, -.15, .01], [.045, .10, .05], "#b08b52");
    const fireflies = group("", 0, 1.4, 0, 0, groups.garden);
    const fireflyParticles = Array.from({ length: 8 }, (_, i) => ambientBox(fireflies, [Math.sin(i * 2.4) * .6, (i % 3) * .24, Math.cos(i * 2.4) * .6], [.06, .06, .06], "#e6f4ae"));
    const eventGroups = { steam, butterfly, bird, fireflies };
    Object.values(eventGroups).forEach(g => { g.visible = false; });
    const momentCaptions = {
      steam: "杯口升起一缕热气，慢慢散在小屋里。",
      butterfly: "一只小蝴蝶绕着绿植，轻轻停了一会儿。",
      bird: "窗沿落下一只小鸟，歪着脑袋看了看。",
      fireflies: "叶片间亮起几粒萤火，像藏起来的小星星。",
    };
    let lightMode = "day", weatherCondition = "unknown", ambientMotion = true, ambientEvents = true;
    let ambientElapsed = 0, ambientStep = 0, eventElapsed = 0, eventTimeLeft = 0, moment = "", previousMoment = "";
    let nextMomentIn = 40 + Math.random() * 35;
    function ambientPaused() { return !active || editing || tourRunning; }
    function getAmbientState() {
      return { condition: weatherCondition, light: lightMode, motion: ambientMotion && !reducedMotion, events: ambientEvents, paused: ambientPaused(), event: moment || null, eventElapsed, nextIn: nextMomentIn, elapsed: ambientElapsed, precipitation: precipitation.visible };
    }
    function endMoment() {
      Object.values(eventGroups).forEach(g => { g.visible = false; });
      moment = ""; eventElapsed = 0; eventTimeLeft = 0;
      nextMomentIn = 90 + Math.random() * 150; renderDirty = true;
      options.onMoment?.(null);
    }
    function encounter(automatic = false) {
      if (disposed || ambientPaused() || moment || (automatic && !ambientEvents)) return false;
      const calm = !["rain", "snow", "storm", "mist"].includes(weatherCondition);
      const candidates = ["steam"];
      if (lightMode === "night" && calm) candidates.push("fireflies");
      if (lightMode !== "night" && calm) candidates.push("bird");
      if (lightMode !== "night" && ["clear", "cloudy"].includes(weatherCondition)) candidates.push("butterfly");
      const pool = candidates.filter(id => id !== previousMoment);
      moment = (pool.length ? pool : candidates)[Math.floor(Math.random() * (pool.length || candidates.length))];
      previousMoment = moment; eventElapsed = 0; eventTimeLeft = 12;
      eventGroups[moment].visible = true;
      poseMoment(0); renderDirty = true; draw();
      options.onMoment?.({ id: moment, caption: momentCaptions[moment] });
      return true;
    }
    function poseMoment(t) {
      if (moment === "steam") steamParticles.forEach((p, i) => {
        const phase = (i / steamParticles.length + t * .2) % 1;
        p.position.set(Math.sin(phase * 7 + i) * .08, phase * .72, Math.cos(phase * 6) * .025);
        p.material.opacity = Math.sin(phase * Math.PI) * .46;
      });
      if (moment === "butterfly") {
        butterfly.position.set(Math.sin(t * .7) * .52, 1.85 + Math.sin(t * .9) * .18, Math.cos(t * .7) * .42);
        butterfly.rotation.y = -t * .7;
        wings.forEach((wing, i) => { wing.rotation.y = (i ? 1 : -1) * (.4 + Math.sin(t * 9) * .55); });
      }
      if (moment === "bird") bird.rotation.y = Math.sin(t * .6) * .25;
      if (moment === "fireflies") fireflyParticles.forEach((p, i) => {
        p.position.set(Math.sin(i * 2.4 + t * .3) * .65, (i % 3) * .24 + Math.sin(t * .6 + i) * .10, Math.cos(i * 2.4 + t * .3) * .6);
      });
    }
    function updateAmbient(dt) {
      const paused = ambientPaused();
      // Hide visits while editing/touring, keeping their remaining active time.
      for (const [id, g] of Object.entries(eventGroups)) {
        const visible = id === moment && !paused;
        if (g.visible !== visible) { g.visible = visible; renderDirty = true; }
      }
      if (paused) return;
      const step = Math.min(dt, .1);
      if (moment) {
        eventTimeLeft -= step;
        if (eventTimeLeft <= 0) endMoment();
      } else if (ambientEvents && !down && !tween) {
        nextMomentIn -= step;
        if (nextMomentIn <= 0) encounter(true);
      }
      if (!ambientMotion || reducedMotion) return;
      ambientElapsed += step; ambientStep += step;
      if (moment) eventElapsed += step;
      // Ambient-only renders are limited to 24 fps. Camera/door motion keeps
      // its existing cadence; no shadows are recalculated for these effects.
      if (ambientStep < 1 / 24) return;
      ambientStep = 0;
      if (precipitation.visible) {
        const snow = weatherCondition === "snow";
        weatherParticles.forEach((p, i) => {
          p.position.y = 1.23 - ((i * .37 + ambientElapsed * (snow ? .17 : .9)) % 1) * 2.46;
          p.position.x = ((i * .618) % 1) * 2.7 - 1.35 + (snow ? Math.sin(ambientElapsed + i) * .09 : 0);
        });
        renderDirty = true;
      }
      if (moment) { poseMoment(eventElapsed); renderDirty = true; }
    }
    function setAtmosphere(value = {}) {
      const condition = ["clear", "cloudy", "rain", "snow", "storm", "mist"].includes(value.condition) ? value.condition : "unknown";
      const motionChanged = ambientMotion !== (value.motion !== false), eventsChanged = ambientEvents !== (value.events !== false);
      ambientMotion = value.motion !== false; ambientEvents = value.events !== false;
      if (condition !== weatherCondition) { weatherCondition = condition; if (moment) endMoment(); setLight(lightMode, true); }
      if (eventsChanged && !ambientEvents && moment) endMoment();
      if (motionChanged) { if (!ambientMotion) poseMoment(0); renderDirty = true; draw(); }
      options.onAtmosphereChange?.();
    }
    function resetAtmosphere() {
      endMoment(); previousMoment = ""; ambientElapsed = 0; eventElapsed = 0; nextMomentIn = 40 + Math.random() * 35;
      weatherCondition = "unknown"; setLight(lightMode, true);
    }
    const rounded = n => Math.round(n * 100) / 100;
    function getLayout() {
      return { version: 1, houseStyle, items: Object.fromEntries(Object.keys(CATALOG).map(id => [id, {
        x: rounded(groups[id].position.x), y: rounded(groups[id].position.y), z: rounded(groups[id].position.z), rotation: rounded(THREE.MathUtils.radToDeg(groups[id].rotation.y)), style: styles[id], model: models[id], surface: surfaces[id],
      }])) };
    }
    function footprint(id) {
      groups[id].updateWorldMatrix(true, true);
      return localBounds[id].clone().applyMatrix4(groups[id].matrixWorld);
    }
    function placementHint() {
      if (placementMessage) return placementMessage;
      if (surfaces[editId] !== "floor") return "沿墙拖动；方向键左右移动、上下调高低。挂件会避开窗户和家具。";
      return editId === "rug" ? "地毯可铺在家具下。拖动摆放，空白处旋转视角。" : "拖动摆放，重叠时保留上一个位置。方向键微调，R 旋转，Ctrl / ⌘ Z 撤销。";
    }
    function emitEditor() {
      container.dataset.placementBlocked = String(placementBlocked);
      options.onEditorChange?.({ editing, houseStyle, id: editId, label: CATALOG[editId], item: getLayout().items[editId], canUndo: history.length > 0, canRedo: future.length > 0, hint: placementHint(), blocked: placementBlocked });
    }
    function finishChange(before) {
      if (JSON.stringify(before) !== JSON.stringify(getLayout())) { history.push(before); if (history.length > 60) history.shift(); future = []; }
      emitEditor();
    }
    function applyHouseStyle(value) {
      const next = typeof value === "string" && Object.hasOwn(HOUSE_STYLES, value) ? value : "classic";
      container.dataset.houseStyle = next;
      if (next === houseStyle) return;
      if (next !== "classic" && !houseGroups[next]) makeHouseStyle(next);
      houseStyle = next;
      for (const original of [floorG, wallBack, wallLeft, windowFrame, entranceDoorBody]) original.visible = next === "classic";
      for (const [id, house] of Object.entries(houseGroups)) {
        house.visible = id === next; house.userData.parts.forEach(part => { part.visible = house.visible; });
      }
      const f = HOUSE_FINISHES[next];
      const palette = f ? { [C.tealDark]: f.edge, [C.edge]: f.edge, [C.oak]: f.wood, [C.cream]: f.wall, "#7d9b90": f.lower, "#eae3d0": f.wall, "#c6cab9": f.floor[1] } : {};
      for (const root of [structure, entranceFrame]) root.traverse(obj => {
        if (!obj.isInstancedMesh) return;
        obj.userData.baseColors.forEach((base, i) => { obj.setColorAt(i, color.set(palette[base] || base)); });
        obj.instanceColor.needsUpdate = true;
      });
      scene.updateMatrixWorld(true); hovered = ""; renderDirty = true; renderer.shadowMap.needsUpdate = true;
    }
    function updateHouseStyle(value) {
      if (!editing) return;
      const before = getLayout(); applyHouseStyle(value); finishChange(before); draw();
    }
    function applyStyle(id, style) {
      if (styles[id] === style) return;
      const custom = !OBJECT_MODELS[id] && NEW_STYLES.includes(style), finish = FINISHES[style];
      if (custom && !styleGroups[id][style]) { makeNewStyle(id, style); flushBatches(); }
      styles[id] = style;
      const maps = style === "cream" ? {
        [C.oak]: "#e6ddc6", [C.wood]: "#c8b696", [C.edge]: "#ab987d", [C.teal]: "#d5cdb5", [C.tealDark]: "#879287", [C.tealLight]: "#e8dcc5", [C.rose]: "#cfa796",
      } : style === "ink" ? {
        [C.oak]: "#78654f", [C.wood]: "#675541", [C.edge]: "#3f464b", [C.teal]: "#3f546c", [C.tealDark]: "#293b50", [C.tealLight]: "#74899b", [C.rose]: "#bc794f",
      } : finish ? {
        [C.oak]: finish.wood, [C.wood]: finish.wood, [C.edge]: finish.edge, [C.teal]: finish.accent,
        [C.tealDark]: finish.dark, [C.tealLight]: finish.light, [C.rose]: finish.rose,
        "#d8b68b": finish.wood, "#c9a779": finish.light, "#dc9b79": finish.rose,
      } : {};
      const fabric = ["#6e9990", "#82a89a", "#a9c3ac", "#c89669", "#d4a87a", "#bd895d", "#e5d8b8", "#dbcaab", "#6b9890"];
      groups[id].traverse(obj => {
        if (!obj.isInstancedMesh || obj.userData.variant) return;
        obj.userData.baseColors.forEach((base, i) => {
          const alternate = style !== "oak" && fabric.includes(base) ? (finish?.fabric || (style === "cream" ? "#e1d7c4" : "#546c85")) : base;
          obj.setColorAt(i, color.set(maps[base] || alternate));
        }); obj.instanceColor.needsUpdate = true;
      });
      for (const [key, g] of Object.entries(styleGroups[id])) {
        g.visible = key === style; for (const panel of g.userData.doorPanels || []) panel.visible = g.visible;
      }
      for (const body of baseBodies[id] || []) body.visible = !custom;
    }
    function measureFurniture(id) {
      const key = `${models[id]}:${styles[id]}`;
      if (!styleBounds[id][key]) {
        // Visible geometry only; a hidden model or an open wardrobe door must
        // never change the saved footprint. A small seam allows touching edges.
        const angles = doors.map(door => door.group.rotation.y);
        for (const door of doors) door.group.rotation.y = 0;
        const root = groups[id]; root.updateWorldMatrix(true, true);
        const inverse = root.matrixWorld.clone().invert(), box = new THREE.Box3();
        root.traverse(obj => {
          if (!obj.isInstancedMesh) return;
          for (let ancestor = obj; ancestor !== root; ancestor = ancestor.parent) if (!ancestor.visible) return;
          if (!obj.boundingBox) obj.computeBoundingBox();
          box.union(obj.boundingBox.clone().applyMatrix4(inverse.clone().multiply(obj.matrixWorld)));
        });
        styleBounds[id][key] = box;
        doors.forEach((door, i) => { door.group.rotation.y = angles[i]; });
      }
      localBounds[id] = styleBounds[id][key].clone();
    }
    function allowedSurfaces(id) { return OBJECT_MODELS[id]?.[models[id]]?.surfaces || ["floor"]; }
    function poseFurniture(id, item = {}) {
      const root = groups[id], initial = defaults[id];
      const number = (value, fallback) => typeof value === "number" && Number.isFinite(value) ? value : fallback;
      const model = OBJECT_MODELS[id] && typeof item.model === "string" && Object.hasOwn(OBJECT_MODELS[id], item.model) ? item.model : initial.model;
      if (model !== models[id]) {
        if (["game", "image", "together"].includes(id) && !modelGroups[id]?.[model]) makeObjectModel(id, model);
        for (const [key, g] of Object.entries(modelGroups[id] || {})) g.visible = key === model;
        models[id] = model; styles[id] = ""; // Reapply the finish to newly built geometry.
      }
      applyStyle(id, typeof item.style === "string" && Object.hasOwn(STYLES, item.style) ? item.style : "oak");
      measureFurniture(id);
      const allowed = allowedSurfaces(id);
      surfaces[id] = allowed.includes(item.surface) ? item.surface : allowed.includes(initial.surface) ? initial.surface : allowed[0];
      const surface = surfaces[id], wall = surface !== "floor";
      root.rotation.y = wall ? (surface === "left-wall" ? Math.PI / 2 : 0) : THREE.MathUtils.degToRad(((number(item.rotation, initial.rotation) % 360) + 360) % 360);
      const bounds = localBounds[id].clone().applyMatrix4(new THREE.Matrix4().makeRotationY(root.rotation.y));
      root.position.x = rounded(THREE.MathUtils.clamp(number(item.x, initial.x), ROOM.minX - bounds.min.x, ROOM.maxX - bounds.max.x));
      root.position.z = rounded(THREE.MathUtils.clamp(number(item.z, initial.z), ROOM.minZ - bounds.min.z, ROOM.maxZ - bounds.max.z));
      if (surface === "back-wall") root.position.z = ROOM.minZ - bounds.min.z;
      if (surface === "left-wall") root.position.x = ROOM.minX - bounds.min.x;
      const top = surface === "left-wall" && root.position.z + bounds.max.z > ROOM.lowWallStart ? ROOM.lowWallTop : ROOM.wallTop;
      root.position.y = wall ? rounded(THREE.MathUtils.clamp(number(item.y, initial.y || 2.8), .22 - bounds.min.y, top - bounds.max.y)) : 0;
      root.updateWorldMatrix(true, true); renderDirty = true; renderer.shadowMap.needsUpdate = true;
    }
    function shape(id) {
      const root = groups[id], box = localBounds[id], c = box.getCenter(new THREE.Vector3()), half = box.getSize(new THREE.Vector3()).multiplyScalar(.5);
      const center = root.localToWorld(c), a = root.rotation.y;
      return { center, half, axes: [new THREE.Vector3(Math.cos(a), 0, -Math.sin(a)), new THREE.Vector3(Math.sin(a), 0, Math.cos(a))] };
    }
    function shapesOverlap(a, b) {
      const delta = b.center.clone().sub(a.center);
      if (Math.abs(delta.y) >= a.half.y + b.half.y - .025) return false;
      // Separating-axis test for upright oriented boxes, including height.
      return [...a.axes, ...b.axes].every(axis => {
        const radius = s => s.half.x * Math.abs(s.axes[0].dot(axis)) + s.half.z * Math.abs(s.axes[1].dot(axis));
        return Math.abs(delta.dot(axis)) < radius(a) + radius(b) - .025;
      });
    }
    function placementIssue(id, peers = Object.keys(CATALOG)) {
      const b = footprint(id), surface = surfaces[id];
      if (b.min.x < ROOM.minX - .015 || b.max.x > ROOM.maxX + .015 || b.min.z < ROOM.minZ - .015 || b.max.z > ROOM.maxZ + .015 || b.min.y < -.1 || b.max.y > ROOM.wallTop + .015) return "超出房间边界";
      if (surface === "left-wall" && b.max.z > ROOM.lowWallStart && b.max.y > ROOM.lowWallTop + .015) return "此处是矮墙，挂件需要降低或移向里侧";
      // Keep the window/curtains and the complete inward door sweep clear.
      if (surface === "back-wall" && b.max.x > -5.55 && b.min.x < -1.55 && b.max.y > 1.38 && b.min.y < 4.55) return "这里是窗户，请移到窗边的实墙";
      if (id !== "rug" && b.max.x > 3.62 && b.max.z > .08 && b.min.z < 2.65 && b.min.y < 3.48) return "请给房门留出开合空间";
      if (id === "rug") return "";
      const current = shape(id);
      const other = peers.find(key => key !== id && key !== "rug" && shapesOverlap(current, shape(key)));
      return other ? `与${CATALOG[other]}重叠` : "";
    }
    function findSpace(id, item, peers) {
      poseFurniture(id, item);
      if (!placementIssue(id, peers)) return true;
      const origin = groups[id].position.clone(), surface = surfaces[id], candidates = [];
      const wall = surface !== "floor";
      for (let u = -48; u <= 48; u++) for (let v = wall ? -19 : -40; v <= (wall ? 19 : 40); v++) candidates.push([u, v]);
      candidates.sort((a, b) => a[0] ** 2 + a[1] ** 2 - b[0] ** 2 - b[1] ** 2);
      for (const [u, v] of candidates) {
        const x = surface === "left-wall" ? origin.x : origin.x + u * .25;
        const z = surface === "back-wall" ? origin.z : origin.z + (wall ? -u : v) * .25;
        poseFurniture(id, { ...item, x, z, y: wall ? origin.y + v * .25 : 0 });
        if (!placementIssue(id, peers)) return true;
      }
      return false;
    }
    function putFurniture(id, item, relocate = false) {
      const previous = getLayout().items[id];
      poseFurniture(id, item); const issue = placementIssue(id);
      placementBlocked = false; placementMessage = "";
      if (issue && !(relocate && findSpace(id, item))) {
        poseFurniture(id, previous); placementBlocked = true; placementMessage = `${issue}，已保留原位。可先移动家具再切换外观。`; return false;
      }
      if (issue) placementMessage = "已放到附近的空位，可继续拖动微调。";
      return true;
    }
    function applyLayout(layout) {
      const items = layout?.version === 1 && layout.items && typeof layout.items === "object" ? layout.items : defaults;
      applyHouseStyle(layout?.version === 1 ? layout.houseStyle : "classic");
      placementMessage = ""; placementBlocked = false; layoutRepairs = [];
      const placed = [];
      for (const id of Object.keys(CATALOG)) {
        const item = items[id] && typeof items[id] === "object" ? items[id] : defaults[id];
        poseFurniture(id, item);
        if (placementIssue(id, placed)) {
          layoutRepairs.push(id);
          if (!findSpace(id, item, placed) && !findSpace(id, defaults[id], placed)) {
            // An impossible imported room must not retain intersecting geometry.
            // Restore the known fitting room; the stored source remains untouched.
            if (items !== defaults) { applyLayout(null); placementMessage = "原布局没有足够空位，已恢复默认布置；浏览器中的原记录仍保留。"; emitEditor(); return; }
          }
        }
        placed.push(id);
      }
      if (layoutRepairs.length) placementMessage = `已为${layoutRepairs.map(id => CATALOG[id]).join("、")}避让重叠或门窗；保存后记住新位置。`;
      emitEditor(); draw();
    }
    function updateFurniture(patch) {
      if (!editing) return;
      const before = getLayout(), item = { ...before.items[editId], ...patch };
      if (patch.model && patch.model !== before.items[editId].model) { delete item.surface; item.y = 2.8; }
      putFurniture(editId, item, Boolean(patch.model || patch.surface || patch.style)); finishChange(before); draw();
    }
    function selectFurniture(id) {
      if (!editing || !CATALOG[id]) return;
      editId = id; hovered = ""; placementMessage = ""; placementBlocked = false; emitEditor(); draw();
    }
    function setEditing(value) {
      cancelDrag(); editing = Boolean(value); grid.visible = editing; history = []; future = [];
      selected = "overview"; hovered = ""; focus("overview"); emitEditor();
      updateAmbient(0); options.onAtmosphereChange?.(); draw();
    }
    function undoEdit(redo = false) {
      if (!editing) return;
      const from = redo ? future : history, to = redo ? history : future;
      if (from.length) { to.push(getLayout()); applyLayout(from.pop()); }
    }
    function overview() { return { position: [13.5, 12.8, 16.2], target: [0, 1.15, .5], fov: 38 }; }
    function focusShot(id) {
      if (id === "overview") return editing ? { position: [7.8, 17, 12.8], target: [0, 0, 0], fov: 40 } : overview();
      const root = groups[id]; if (!root) return overview();
      const center = localBounds[id]?.getCenter(new THREE.Vector3()) || new THREE.Vector3(0, 1.6, 0);
      const target = root.localToWorld(center), offset = new THREE.Vector3(2.7, 3.4, 7.2).applyAxisAngle(new THREE.Vector3(0, 1, 0), root.rotation.y);
      return { position: safeCameraPoint(target.clone().add(offset)).toArray(), target: target.toArray(), fov: 40, focusId: id };
    }
    function visibleMeshes() {
      return allMeshes.filter(mesh => {
        for (let obj = mesh; obj; obj = obj.parent) if (!obj.visible) return false;
        return true;
      });
    }
    function destination(shot) {
      const p = new THREE.Vector3(...shot.position), t = new THREE.Vector3(...shot.target);
      if (camera.aspect < 1.25) p.sub(t).multiplyScalar(1 + (1.25 - camera.aspect) * .64).add(t);
      if (shot.focusId) {
        scene.updateMatrixWorld(true);
        const meshes = visibleMeshes(), offset = p.clone().sub(t);
        const candidates = [[0, 0], [-.5, 0], [.5, 0], [-.9, 1], [.9, 1], [0, 4]];
        const extent = footprint(shot.focusId).getSize(new THREE.Vector3()).multiplyScalar(.37);
        const samples = [t, t.clone().add(new THREE.Vector3(extent.x, 0, 0)), t.clone().add(new THREE.Vector3(-extent.x, 0, 0)),
          t.clone().add(new THREE.Vector3(0, 0, extent.z)), t.clone().add(new THREE.Vector3(0, 0, -extent.z)), t.clone().add(new THREE.Vector3(0, extent.y, 0))];
        let best = -1;
        for (const [angle, lift] of candidates) {
          const candidate = safeCameraPoint(offset.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), angle).add(t)); candidate.y += lift;
          let visible = 0;
          for (const sample of samples) {
            framingRay.set(candidate, sample.clone().sub(candidate).normalize()); framingRay.far = candidate.distanceTo(sample) + .1;
            const front = framingRay.intersectObjects(meshes, false)[0];
            if (!front || front.object.userData.station === shot.focusId) visible++;
          }
          if (visible > best) { best = visible; p.copy(candidate); }
          if (visible === samples.length) break;
        }
      }
      return { p, t, fov: shot.fov || 38 };
    }
    function neutralizeControls() {
      if (!scripted) { controls.enableDamping = false; controls.update(); controls.enableDamping = true; }
      camera.up.set(0, 1, 0);
    }
    function move(shot, seconds = 1.45) {
      const to = destination(shot);
      neutralizeControls(); scripted = true; hovered = "";
      // Repeated resets can have a zero-length path; avoid arc-length sampling
      // of a degenerate curve (and an unnecessary animation).
      if (reducedMotion || camera.position.distanceToSquared(to.p) < .00001) {
        camera.position.copy(to.p); controls.target.copy(to.t); camera.fov = to.fov; camera.updateProjectionMatrix(); tween = null;
      } else {
        const from = camera.position.clone(), start = controls.target.clone(), middle = from.clone().lerp(to.p, .5);
        const span = from.distanceTo(to.p); middle.y += Math.min(4, span * .26); middle.x += Math.min(2.8, span * .18);
        tween = { curve: new THREE.CatmullRomCurve3([from, middle, to.p]), start, end: to.t, fov: camera.fov, endFov: to.fov, duration: seconds, time: 0 };
      }
      draw();
    }
    function stopTour() { tourRunning = false; renderDirty = true; options.onTourChange?.(false); options.onShotChange?.("自由观察"); options.onAtmosphereChange?.(); }
    function focus(id) {
      stopTour(); hovered = ""; selected = STATIONS[id] ? id : "overview";
      options.onFocusChange?.(selected); move(focusShot(selected));
    }
    function select(id) {
      if (id === "entrance") { toggleDoor(); return; }
      if (editing) { selectFurniture(id); return; }
      if (STATIONS[id]) { focus(id); options.onSelect?.(id); }
    }
    function safeCameraPoint(point) {
      // Keep a moving close-up inside the two opaque walls and above furniture.
      point.x = Math.max(-5.25, point.x); point.z = Math.max(-3.85, point.z); point.y = Math.max(1.35, point.y);
      for (const id of Object.keys(CATALOG).filter(id => id !== "rug")) {
        const box = footprint(id).expandByScalar(.3);
        if (point.x > box.min.x && point.x < box.max.x && point.z > box.min.z && point.z < box.max.z) point.y = Math.max(point.y, box.max.y + .3);
      }
      return point;
    }
    function nextShot() {
      shotIndex = (shotIndex + 1) % SHOTS.length;
      const shot = SHOTS[shotIndex], root = groups[shot.anchor];
      const target = new THREE.Vector3(...shot.target); if (root) root.localToWorld(target);
      const points = shot.points.map(p => { const v = new THREE.Vector3(...p); if (root) root.localToWorld(v); return safeCameraPoint(v); });
      neutralizeControls(); scripted = true;
      const start = camera.position.clone(), join = start.clone().lerp(points[0], .5); join.y = Math.max(start.y, points[0].y) + 1;
      tween = { curve: new THREE.CatmullRomCurve3([start, join, ...points], false, "centripetal"), start: controls.target.clone(), end: target,
        fov: camera.fov, fovs: [camera.fov, camera.fov, ...shot.fovs], endFov: shot.fovs.at(-1), duration: shot.seconds, time: 0, bank: shot.bank, cinematic: true };
      options.onShotChange?.(shot.label);
      updateAmbient(0); options.onAtmosphereChange?.();
    }
    function toggleTour() {
      if (tourRunning) { stopTour(); tween = null; return false; }
      if (editing) return false;
      if (reducedMotion) { options.onShotChange?.("已开启减少动态效果 · 可手动选择家具"); return false; }
      tourRunning = true; hovered = ""; selected = "overview"; shotIndex = -1;
      options.onTourChange?.(true); options.onFocusChange?.("overview"); nextShot(); return true;
    }
    function updateHoverTitle() {
      const station = hovered === "entrance" ? { label: entranceOpen ? "关上房门" : "打开房门" } : editing && CATALOG[hovered] ? { label: CATALOG[hovered] } : STATIONS[hovered];
      hoverTitle.hidden = !station || tourRunning || Boolean(down) || Boolean(tween);
      if (hoverTitle.hidden) return;
      hoverTitle.textContent = station.label;
      hoverTitle.style.left = `${Math.max(10, Math.min(width - 138, hoverPosition.x + 14))}px`;
      hoverTitle.style.top = `${Math.max(10, Math.min(height - 38, hoverPosition.y - 36))}px`;
    }
    function draw(force = true) {
      if (disposed) return;
      const changed = !scripted && controls.enabled ? controls.update() : false;
      if (!force && !changed && !renderDirty) return;
      renderDirty = false;
      const highlightId = editing ? editId : hovered, highlight = groups[highlightId];
      selection.visible = Boolean(highlight) && !tourRunning && !tween && (editing || !down);
      selection.material.opacity = editing ? .85 : .32;
      selection.material.color.set(placementBlocked && editing ? "#ca6859" : "#dfb55d");
      if (highlight) selection.box.copy(localBounds[highlightId] ? footprint(highlightId) : new THREE.Box3().setFromObject(highlight)).expandByScalar(.035);
      if (scripted) camera.lookAt(controls.target);
      renderer.render(scene, camera); updateHoverTitle();
    }
    function animate(now) {
      if (!active || disposed) return;
      // Camera timing follows elapsed active time even on a slower GPU. Time
      // spent on another page is excluded by setActive's lastTime reset.
      const dt = Math.max(0, (now - lastTime) / 1000); lastTime = now;
      if (tween) {
        renderDirty = true;
        tween.time += dt; const t = Math.min(1, tween.time / tween.duration), ease = tween.cinematic ? t : t * t * t * (t * (t * 6 - 15) + 10);
        camera.position.copy(tween.curve.getPointAt(ease));
        if (tween.cinematic) safeCameraPoint(camera.position);
        controls.target.lerpVectors(tween.start, tween.end, tween.cinematic ? Math.min(1, t * 2.6) ** .7 : ease);
        if (tween.fovs) {
          const f = t * (tween.fovs.length - 1), index = Math.min(tween.fovs.length - 2, Math.floor(f));
          camera.fov = THREE.MathUtils.lerp(tween.fovs[index], tween.fovs[index + 1], f - index);
        } else camera.fov = THREE.MathUtils.lerp(tween.fov, tween.endFov, ease);
        camera.up.set(Math.sin(Math.PI * t) * (tween.bank || 0), 1, 0).normalize(); camera.updateProjectionMatrix();
        if (t >= 1) { tween = null; if (tourRunning) nextShot(); }
      }
      const oldOpen = wardrobeOpen;
      wardrobeOpen = reducedMotion ? (selected === "wardrobe" ? 1 : 0) : THREE.MathUtils.damp(wardrobeOpen, selected === "wardrobe" ? 1 : 0, 5, dt);
      if (Math.abs(oldOpen - wardrobeOpen) > .0001) { renderDirty = true; renderer.shadowMap.needsUpdate = true; }
      for (const door of doors) door.group.rotation.y = door.side * wardrobeOpen * 1.65;
      const oldEntrance = entranceAmount;
      entranceAmount = reducedMotion ? Number(entranceOpen) : THREE.MathUtils.damp(entranceAmount, Number(entranceOpen), 4.5, dt);
      entranceLeaf.rotation.y = entranceAmount === 0 ? 0 : -entranceAmount * Math.PI * .55;
      if (Math.abs(oldEntrance - entranceAmount) > .0001) { renderDirty = true; renderer.shadowMap.needsUpdate = true; }
      updateAmbient(dt);
      draw(false); frameId = requestAnimationFrame(animate);
    }
    function resize() {
      if (disposed) return;
      const bounds = container.getBoundingClientRect(), w = Math.max(1, Math.round(bounds.width)), h = Math.max(1, Math.round(bounds.height));
      if (w === width && h === height) return;
      width = w; height = h; camera.aspect = width / height; camera.updateProjectionMatrix(); renderer.setSize(width, height, false);
      if (!tourRunning) { const to = destination(focusShot(selected)); camera.position.copy(to.p); controls.target.copy(to.t); camera.up.set(0, 1, 0); camera.fov = to.fov; camera.updateProjectionMatrix(); scripted = true; tween = null; }
      draw();
    }
    function setActive(next) {
      const value = Boolean(next) && !document.hidden;
      if (disposed || value === active) return;
      active = value;
      if (active) { resize(); lastTime = performance.now(); frameId = requestAnimationFrame(animate); }
      else { cancelDrag(); cancelAnimationFrame(frameId); frameId = 0; }
      updateAmbient(0); options.onAtmosphereChange?.();
    }
    function setLight(mode, force = false) {
      const next = ["night", "dusk"].includes(mode) ? mode : "day";
      if (!force && next === lightMode && scene.background) return;
      if (next !== lightMode && moment) endMoment();
      lightMode = next;
      const night = mode === "night", dusk = mode === "dusk", overcast = ["rain", "storm", "snow", "mist", "cloudy"].includes(weatherCondition);
      const background = night ? "#253e43" : dusk ? "#e5d4bc" : overcast ? "#c8d8d6" : "#dce4d8";
      scene.background = new THREE.Color(background); scene.fog = new THREE.Fog(scene.background, 38, 85);
      ground.material.color.set(night ? "#2d474a" : dusk ? "#d5c5af" : overcast ? "#c5d3cf" : "#d8dfd2");
      hemi.intensity = night ? 1.0 : dusk ? 1.85 : 2.5;
      sun.intensity = night ? .65 : dusk ? 2.3 : overcast ? 2.0 : 4.1;
      sun.color.set(night ? "#a8c9ef" : dusk ? "#ffbb83" : overcast ? "#d4e6ef" : "#fff0ce");
      fill.intensity = night ? .6 : 1.1; bedsideLight.intensity = night ? 24 : dusk ? 14 : 6; deskLight.intensity = night ? 18 : dusk ? 10 : 4;
      sky.material.color.set(night ? "#243c60" : dusk ? "#e8b793" : weatherCondition === "snow" ? "#cddddd" : overcast ? "#8eacb7" : "#9ecacc");
      skyOrb.visible = !overcast; skyOrb.material.color.set(night ? "#eee6c6" : "#fff0bf");
      clouds.visible = overcast;
      clouds.children.forEach(mesh => mesh.material.color.set(night ? "#647787" : "#dbe6e5"));
      precipitation.visible = ["rain", "snow", "storm"].includes(weatherCondition);
      weatherParticles.forEach(p => { const snow = weatherCondition === "snow"; p.scale.set(snow ? .055 : .023, snow ? .055 : .15, .023); p.material.color.set(snow ? "#fcf7ef" : "#d2e7eb"); });
      renderer.shadowMap.needsUpdate = true; draw();
    }
    function hit(event) {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1); raycaster.setFromCamera(pointer, camera);
      // Structural meshes occlude furniture during picking too.
      scene.updateMatrixWorld(true);
      const id = raycaster.intersectObjects(visibleMeshes(), false)[0]?.object?.userData?.station || "";
      return STATIONS[id] || id === "entrance" || (editing && CATALOG[id]) ? id : "";
    }
    function placementPoint(event, id) {
      hit(event);
      const surface = surfaces[id], root = groups[id];
      const plane = surface === "back-wall" ? new THREE.Plane(new THREE.Vector3(0, 0, 1), -root.position.z) : surface === "left-wall" ? new THREE.Plane(new THREE.Vector3(1, 0, 0), -root.position.x) : floorPlane;
      return raycaster.ray.intersectPlane(plane, new THREE.Vector3());
    }
    function pointerDown(event) {
      if (event.button !== 0) return;
      if (editing) {
        const id = hit(event), point = CATALOG[id] ? placementPoint(event, id) : null;
        if (CATALOG[id] && point) {
          event.stopImmediatePropagation(); event.preventDefault();
          tween = null; selectFurniture(id); controls.enabled = false;
          drag = { id, before: getLayout(), offset: groups[id].position.clone().sub(point), pointerId: event.pointerId };
          renderer.domElement.setPointerCapture(event.pointerId);
        }
      }
      down = { x: event.clientX, y: event.clientY, id: event.pointerId, moved: false };
      hovered = ""; renderDirty = true; updateHoverTitle();
    }
    function pointerMove(event) {
      if (down && Math.hypot(event.clientX - down.x, event.clientY - down.y) > 7) down.moved = true;
      if (drag && event.pointerId === drag.pointerId) {
        const point = placementPoint(event, drag.id);
        if (point && down?.moved) { point.add(drag.offset); putFurniture(drag.id, { ...getLayout().items[drag.id], x: Math.round(point.x * 4) / 4, y: Math.round(point.y * 4) / 4, z: Math.round(point.z * 4) / 4 }); emitEditor(); draw(); }
        return;
      }
      if (!down && event.pointerType !== "touch") {
        const rect = renderer.domElement.getBoundingClientRect();
        hoverPosition.x = event.clientX - rect.left; hoverPosition.y = event.clientY - rect.top;
        const id = hit(event);
        if (hovered !== id) renderDirty = true;
        hovered = id; renderer.domElement.style.cursor = hovered ? "pointer" : "grab"; updateHoverTitle();
      }
    }
    function pointerUp(event) {
      if (drag && event.pointerId === drag.pointerId) {
        const before = drag.before; drag = null; down = null; controls.enabled = true;
        if (renderer.domElement.hasPointerCapture(event.pointerId)) renderer.domElement.releasePointerCapture(event.pointerId);
        finishChange(before); draw(); return;
      }
      if (down && down.id === event.pointerId && !down.moved) { const id = hit(event); if (id) select(id); } down = null;
    }
    function cancelDrag() {
      if (drag) {
        const { before, pointerId } = drag; drag = null; controls.enabled = true; applyLayout(before);
        if (renderer.domElement.hasPointerCapture(pointerId)) renderer.domElement.releasePointerCapture(pointerId);
      }
      down = null;
    }
    function cancelPointer(event) { if (drag && event.type === "pointerleave") return; cancelDrag(); hovered = ""; renderDirty = true; updateHoverTitle(); }
    function manualControl() { stopTour(); tween = null; scripted = false; camera.up.set(0, 1, 0); hovered = ""; updateHoverTitle(); }
    function toggleDoor(force, notify = true) {
      const next = typeof force === "boolean" ? force : !entranceOpen;
      if (next === entranceOpen) return entranceOpen;
      entranceOpen = next; container.dataset.doorOpen = String(next); renderDirty = true;
      if (reducedMotion) { entranceAmount = Number(next); entranceLeaf.rotation.y = next ? -Math.PI * .55 : 0; renderer.shadowMap.needsUpdate = true; draw(); }
      if (notify) options.onDoorChange?.(next);
      return next;
    }
    function motionChange(event) { reducedMotion = event.matches; if (reducedMotion) { stopTour(); tween = null; focus(selected); poseMoment(0); } renderDirty = true; draw(); options.onAtmosphereChange?.(); }
    function contextLost(event) { event.preventDefault(); setActive(false); options.onError?.("显卡连接中断，请重试加载小屋"); }
    const canvas = renderer.domElement;
    canvas.addEventListener("pointerdown", pointerDown, true); canvas.addEventListener("pointermove", pointerMove); canvas.addEventListener("pointerup", pointerUp);
    canvas.addEventListener("pointercancel", cancelPointer); canvas.addEventListener("pointerleave", cancelPointer); canvas.addEventListener("webglcontextlost", contextLost);
    controls.addEventListener("start", manualControl); media?.addEventListener?.("change", motionChange);
    const observer = new ResizeObserver(resize); observer.observe(container);
    const to = destination(overview()); camera.position.copy(to.p); controls.target.copy(to.t);
    setLight(options.light); applyLayout(options.layout); resize(); options.onTourChange?.(false); options.onShotChange?.("自由观察");
    container.dataset.doorOpen = "false";
    return {
      focus, resize, setActive, setLight, toggleTour,
      setAtmosphere, getAmbientState, resetAtmosphere, encounter,
      nextShot() {
        if (editing || reducedMotion) return false;
        tourRunning = true; selected = "overview"; hovered = ""; options.onTourChange?.(true); nextShot(); return true;
      },
      getLayout, setEditing, selectFurniture, updateFurniture, updateHouseStyle, undoEdit, toggleDoor,
      getCatalog() { return { furniture: { ...CATALOG }, styles: { ...STYLES }, surfaces: { ...SURFACES }, objectModels: structuredClone(OBJECT_MODELS), houseStyles: Object.fromEntries(Object.entries(HOUSE_STYLES).map(([id, style]) => [id, { ...style }])), styleDetails: Object.fromEntries(Object.entries(STYLE_DETAILS).map(([id, detail]) => [id, {
        note: detail.note, models: Object.fromEntries(Object.keys(CATALOG).map((key, i) => [key, detail.models[i]])),
      }])) }; },
      setLayout(layout) { cancelDrag(); history = []; future = []; applyLayout(layout); },
      restoreDefaults() { if (!editing) return; const before = getLayout(); applyLayout({ version: 1, items: defaults }); finishChange(before); },
      reset() { cancelDrag(); focus("overview"); },
      // Read-only state also lets extensions inspect the scene without touching Three objects.
      getState() { return { editing, editId, houseStyle, placementBlocked, placementMessage, layoutRepairs: [...layoutRepairs], placementIssues: Object.fromEntries(Object.keys(CATALOG).map(id => [id, placementIssue(id)]).filter(([, issue]) => issue)), doorOpen: entranceOpen, doorAngle: entranceLeaf.rotation.y, tourRunning, shotIndex, moving: Boolean(tween), camera: { position: camera.position.toArray(), target: controls.target.toArray(), fov: camera.fov, up: camera.up.toArray() } }; },
      dispose() {
        if (disposed) return;
        setActive(false); disposed = true; observer.disconnect(); media?.removeEventListener?.("change", motionChange);
        controls.removeEventListener("start", manualControl); controls.dispose();
        canvas.removeEventListener("pointerdown", pointerDown, true); canvas.removeEventListener("pointermove", pointerMove); canvas.removeEventListener("pointerup", pointerUp);
        canvas.removeEventListener("pointercancel", cancelPointer); canvas.removeEventListener("pointerleave", cancelPointer); canvas.removeEventListener("webglcontextlost", contextLost);
        allMeshes.forEach(mesh => mesh.dispose()); geometry.dispose(); material.dispose();
        ambientMaterials.forEach(mat => mat.dispose());
        ground.geometry.dispose(); ground.material.dispose(); selection.geometry.dispose(); selection.material.dispose(); grid.geometry.dispose(); grid.material.dispose();
        renderer.dispose(); container.replaceChildren(); controllers.delete(container);
      },
    };
  }
  return { mount(container, options = {}) {
    if (controllers.has(container)) return controllers.get(container);
    const controller = createController(container, options); controllers.set(container, controller); return controller;
  } };
})();
