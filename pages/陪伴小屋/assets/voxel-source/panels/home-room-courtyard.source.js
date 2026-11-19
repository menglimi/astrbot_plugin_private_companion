import * as THREE from "../vendor/three.module.min.js";

// Deterministic world scenery per houseStyle:
// - classic:  林间花园庭院 (layered meadow, stepped stone path, pergola arbor, flowerbeds, glowing lanterns, fireflies)
// - cabin:    幽林秘境森林 (dense spruce & birch pines, mossy boulders, woodland brook with wooden footbridge, mushroom rings, floating forest motes)
// - coastal:  临海白屋岬角 (terraced cliffside deck, shimmering turquoise ocean waves, sand & tidepool rocks, white-red lighthouse, sailboats, gulls)
// - french:   山巅云海观景台 (alpine stone terrace, layered distant snow-capped peaks, drifting cloud sea, cypress & mountain pines, observatory balustrade)
// - loft:     城市天台暮景 (rooftop parapet & water tower, multi-depth illuminated city skyline, warm amber/cyan windows, neon billboard glow, distant highway lights)
// - washitsu: 竹林枯山水庭院 (raked zen gravel waves, stepping stones, bamboo grove, red torii/wood bridge over koi pond, stone tsukubai lantern)
export function createCourtyard(scene) {
  const root = new THREE.Group();
  root.name = "preview-courtyard";
  scene.add(root);

  const unitBox = new THREE.BoxGeometry(1, 1, 1);
  const groundY = -0.735;
  const noise = (x, z, salt = 0) => {
    const n = Math.sin(x * 127.1 + z * 311.7 + salt * 73.9) * 43758.5453;
    return n - Math.floor(n);
  };
  const inHouse = (x, z, pad = 0) => x > -6.12 - pad && x < 6.12 + pad && z > -4.72 - pad && z < 5.92 + pad;
  const path = [[7.25, 1.25], [8.6, 1.25], [9.15, 3.6], [8.6, 6.4], [9.8, 9.2], [13.2, 12.6], [18.5, 15.2]];
  function pathDistance(x, z) {
    let distance = Infinity;
    for (let i = 1; i < path.length; i++) {
      const [ax, az] = path[i - 1], [bx, bz] = path[i];
      const dx = bx - ax, dz = bz - az;
      const t = Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / (dx * dx + dz * dz)));
      distance = Math.min(distance, Math.hypot(x - ax - dx * t, z - az - dz * t));
    }
    return distance;
  }

  // Procedural ground textures so every world has an organic, rich floor surface.
  function makeCanvasTexture(size, painterFn, repeat = 3) {
    const c = document.createElement("canvas");
    c.width = c.height = size;
    painterFn(c.getContext("2d"), size);
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
    tex.repeat.set(repeat, repeat);
    return tex;
  }

  const textures = {
    meadow: makeCanvasTexture(512, (ctx, S) => {
      const c = new THREE.Color(), a = new THREE.Color("#637c51"), b = new THREE.Color("#93a978");
      for (let px = 0; px < S; px += 4) for (let pz = 0; pz < S; pz += 4) {
        const x = px / S * 54, z = pz / S * 54;
        const v = 0.45 + Math.sin(x * 0.22 + z * 0.16) * 0.2 + Math.cos(z * 0.29 - x * 0.12) * 0.14 + noise(x, z) * 0.08;
        c.copy(a).lerp(b, THREE.MathUtils.clamp(v, 0, 1));
        ctx.fillStyle = "#" + c.getHexString();
        ctx.fillRect(px, pz, 4, 4);
      }
    }, 3),
    forest: makeCanvasTexture(512, (ctx, S) => {
      const c = new THREE.Color(), a = new THREE.Color("#3b5239"), b = new THREE.Color("#677d50");
      for (let px = 0; px < S; px += 4) for (let pz = 0; pz < S; pz += 4) {
        const x = px / S * 64, z = pz / S * 64;
        const v = 0.42 + Math.sin(x * 0.31 + z * 0.25) * 0.22 + Math.cos(x * 0.18 - z * 0.27) * 0.16 + noise(x, z, 2) * 0.14;
        c.copy(a).lerp(b, THREE.MathUtils.clamp(v, 0, 1));
        ctx.fillStyle = "#" + c.getHexString();
        ctx.fillRect(px, pz, 4, 4);
      }
    }, 3),
    ocean: makeCanvasTexture(512, (ctx, S) => {
      const c = new THREE.Color(), deep = new THREE.Color("#2b6e84"), shallow = new THREE.Color("#63b2bc"), foam = new THREE.Color("#cce8ea");
      for (let px = 0; px < S; px += 4) for (let pz = 0; pz < S; pz += 4) {
        const x = px / S * 42, z = pz / S * 42;
        const wave = Math.sin(x * 0.45 + Math.sin(z * 0.25) * 1.4) * 0.5 + 0.5;
        const ripple = noise(px, pz, 3) * 0.15;
        c.copy(deep).lerp(shallow, THREE.MathUtils.clamp(wave * 0.78 + ripple, 0, 1));
        if (wave > 0.92) c.lerp(foam, (wave - 0.92) * 5.5);
        ctx.fillStyle = "#" + c.getHexString();
        ctx.fillRect(px, pz, 4, 4);
      }
    }, 4),
    alpine: makeCanvasTexture(512, (ctx, S) => {
      const c = new THREE.Color(), a = new THREE.Color("#b4c4d0"), b = new THREE.Color("#e8eff4");
      for (let px = 0; px < S; px += 4) for (let pz = 0; pz < S; pz += 4) {
        const x = px / S * 30, z = pz / S * 30;
        const swirl = 0.5 + Math.sin(x * 0.28 + z * 0.19) * 0.28 + Math.cos(z * 0.31 - x * 0.14) * 0.18;
        c.copy(a).lerp(b, THREE.MathUtils.clamp(swirl + noise(x, z, 7) * 0.08, 0, 1));
        ctx.fillStyle = "#" + c.getHexString();
        ctx.fillRect(px, pz, 4, 4);
      }
    }, 2),
    city: makeCanvasTexture(256, (ctx, S) => {
      const c = new THREE.Color(), a = new THREE.Color("#3b414a"), b = new THREE.Color("#525a66");
      for (let px = 0; px < S; px += 4) for (let pz = 0; pz < S; pz += 4) {
        const grid = (px % 32 === 0 || pz % 32 === 0) ? 0.18 : 0;
        c.copy(a).lerp(b, THREE.MathUtils.clamp(0.35 + noise(px, pz, 5) * 0.35 + grid, 0, 1));
        ctx.fillStyle = "#" + c.getHexString();
        ctx.fillRect(px, pz, 4, 4);
      }
    }, 6),
    zen: makeCanvasTexture(512, (ctx, S) => {
      const c = new THREE.Color(), a = new THREE.Color("#cfc9b7"), b = new THREE.Color("#e3decb");
      for (let px = 0; px < S; px += 2) for (let pz = 0; pz < S; pz += 4) {
        const rake = Math.sin((pz / S) * Math.PI * 36 + Math.sin((px / S) * Math.PI * 4) * 1.4) * 0.5 + 0.5;
        c.copy(a).lerp(b, THREE.MathUtils.clamp(rake * 0.72 + noise(px, pz, 9) * 0.22, 0, 1));
        ctx.fillStyle = "#" + c.getHexString();
        ctx.fillRect(px, pz, 2, 4);
      }
    }, 4),
    snow: makeCanvasTexture(256, (ctx, S) => {
      const c = new THREE.Color();
      for (let x = 0; x < S; x += 4) for (let z = 0; z < S; z += 4) {
        c.set("#d8e3e8").lerp(new THREE.Color("#f6f9f7"), 0.4 + noise(x, z) * 0.6);
        ctx.fillStyle = "#" + c.getHexString();
        ctx.fillRect(x, z, 4, 4);
      }
    }, 3),
  };

  const lawnGeometry = new THREE.PlaneGeometry(210, 210);
  const lawnMaterial = new THREE.MeshStandardMaterial({ map: textures.meadow, roughness: 0.96 });
  const lawn = new THREE.Mesh(lawnGeometry, lawnMaterial);
  lawn.rotation.x = -Math.PI / 2;
  lawn.position.y = groundY;
  lawn.receiveShadow = true;
  root.add(lawn);

  const disposableGeos = [unitBox, lawnGeometry];
  const disposableMats = [lawnMaterial];
  const disposableMeshes = [];
  const themeGroups = {};

  // Helper builder that compiles a themed voxel world into batched InstancedMeshes.
  function createWorldBuilder(id) {
    const g = new THREE.Group();
    g.name = `world-${id}`;
    g.visible = false;
    root.add(g);
    const solid = [];
    const glow = [];
    const animated = []; // { mesh, update(t, light) }
    const box = (x, y, z, w, h, d, tint, angle = 0, snowCap = false) => solid.push({ x, y, z, w, h, d, tint, angle, snowCap });
    const glowBox = (x, y, z, w, h, d, tint = "#ffe0a1") => glow.push({ x, y, z, w, h, d, tint });

    // Shared architectural motifs used across worlds
    function foundationBorder(stoneA, stoneB) {
      for (let x = -6.3; x <= 6.35; x += 0.5) for (const z of [-4.88, 6.06])
        box(x, groundY + 0.028, z, 0.48, 0.095, 0.44, noise(x, z) > 0.5 ? stoneA : stoneB);
      for (let z = -4.4; z <= 5.8; z += 0.5) for (const x of [-6.32, 6.32]) {
        if (x > 0 && z > -0.3 && z < 2.75) continue;
        box(x, groundY + 0.028, z, 0.44, 0.095, 0.48, stoneA);
      }
      box(7.32, -0.632, 1.25, 0.64, 0.19, 2.6, stoneB, 0, true);
    }

    function windowBox(x, z, boxWood = "#8f7252", darkWood = "#6f563d", leafA = "#779267", leafB = "#8aa376", flowerA = "#ddb2b0", flowerB = "#e7c7a0") {
      box(x, 1.42, z, 2.46, 0.21, 0.36, boxWood, 0, true);
      box(x, 1.54, z, 2.38, 0.04, 0.29, darkWood, 0, true);
      for (const dx of [-0.88, 0.88]) box(x + dx, 1.2, z + 0.02, 0.11, 0.44, 0.11, darkWood, 0, true);
      for (let i = 0; i < 9; i++) {
        const px = x - 1.04 + i * 0.26, pz = z + (noise(i, 71) - 0.5) * 0.18;
        const h = 0.22 + noise(i, 72) * 0.24;
        box(px, 1.58 + h / 2, pz, 0.055, h, 0.055, i % 2 ? leafA : leafB, 0, true);
        box(px, 1.62 + h, pz, 0.16, 0.13, 0.16, i % 3 ? flowerA : flowerB, 0, true);
        box(px - 0.07, 1.56 + h * 0.45, pz + 0.02, 0.1, 0.075, 0.085, i % 2 ? leafB : leafA, 0, true);
      }
      box(x + 0.56, 1.56, z + 0.21, 0.24, 0.04, 0.22, darkWood, 0, true);
      box(x + 0.56, 1.65, z + 0.28, 0.13, 0.15, 0.13, darkWood, 0, true);
      glowBox(x + 0.56, 1.76, z + 0.28, 0.1, 0.13, 0.1, "#ffdfa0");
      box(x + 0.56, 1.84, z + 0.28, 0.19, 0.048, 0.19, darkWood, 0, true);
    }

    function stoneLantern(x, z, s = 1, stone = "#9d9a84", cap = "#5f4a35", glowColor = "#ffe0a1") {
      const y = groundY;
      box(x, y + 0.12 * s, z, 0.44 * s, 0.24 * s, 0.44 * s, stone, 0, true);
      box(x, y + 0.44 * s, z, 0.17 * s, 0.44 * s, 0.17 * s, cap, 0, true);
      box(x, y + 0.61 * s, z, 0.36 * s, 0.24 * s, 0.36 * s, stone, 0, true);
      glowBox(x, y + 0.67 * s, z, 0.22 * s, 0.21 * s, 0.22 * s, glowColor);
      box(x, y + 0.79 * s, z, 0.48 * s, 0.065 * s, 0.48 * s, cap, 0, true);
      box(x, y + 0.86 * s, z, 0.24 * s, 0.1 * s, 0.24 * s, stone, 0, true);
    }

    function broadleafTree(x, z, s, seed, palette = ["#567b53", "#6d8c59", "#7e9a69", "#4e704d", "#94a777"], trunk = "#8a7860") {
      box(x, groundY + 1.38 * s, z, 0.28 * s, 2.76 * s, 0.28 * s, trunk);
      box(x - 0.1 * s, groundY + 0.22 * s, z + 0.1 * s, 0.42 * s, 0.44 * s, 0.42 * s, trunk);
      box(x + 0.32 * s, groundY + 1.94 * s, z, 0.7 * s, 0.18 * s, 0.18 * s, trunk, -0.28);
      for (let ix = -3; ix <= 3; ix++) for (let iz = -3; iz <= 3; iz++) for (let iy = 0; iy < 5; iy++) {
        if (ix * ix + iz * iz + (iy - 2) * (iy - 2) * 1.32 > 11.4 || noise(ix + seed, iz, iy) < 0.08) continue;
        const unit = 0.37 * s;
        box(x + ix * unit, groundY + (2.15 + iy * 0.33) * s, z + iz * unit, unit * 1.03, 0.36 * s, unit * 1.03,
          palette[Math.floor(noise(ix, iz + seed, iy) * palette.length)], 0, true);
      }
    }

    function pineTree(x, z, s, seed, palette = ["#2d4e3e", "#386049", "#477255", "#244033", "#568160"], trunk = "#5d4736") {
      box(x, groundY + 1.5 * s, z, 0.26 * s, 3.0 * s, 0.26 * s, trunk);
      const tiers = 6;
      for (let tier = 0; tier < tiers; tier++) {
        const radius = Math.max(0.35, (2.15 - tier * 0.32)) * s;
        const y = groundY + (1.45 + tier * 0.66) * s;
        const step = 0.42 * s;
        for (let dx = -radius; dx <= radius; dx += step) for (let dz = -radius; dz <= radius; dz += step) {
          const dist = Math.hypot(dx, dz);
          if (dist > radius * (0.86 + noise(dx + seed, dz, tier) * 0.24)) continue;
          const tint = palette[Math.floor(noise(dx * 3 + seed, dz * 3, tier) * palette.length)];
          box(x + dx, y, z + dz, step * 1.04, 0.48 * s, step * 1.04, tint, 0, true);
        }
      }
      box(x, groundY + (1.45 + tiers * 0.66) * s, z, 0.32 * s, 0.56 * s, 0.32 * s, palette[2], 0, true);
    }

    function flowerCluster(x, z, s = 1, seed = 0, petals = ["#e7c7a0", "#ddb2b0", "#d4ad76"]) {
      for (let i = 0; i < 3; i++) {
        const px = x + (noise(i, seed, 2) - 0.5) * 0.32 * s, pz = z + (noise(i, 3, seed) - 0.5) * 0.26 * s;
        const h = (0.1 + noise(i, seed, 4) * 0.14) * s;
        box(px, groundY + h / 2, pz, 0.044 * s, h, 0.044 * s, i % 2 ? "#739064" : "#88a474", 0, true);
        if (i !== 2) box(px, groundY + h + 0.032 * s, pz, 0.088 * s, 0.075 * s, 0.088 * s, petals[(i + Math.floor(seed)) % petals.length], 0, true);
      }
    }

    function finalize(meta) {
      const mat = new THREE.MeshStandardMaterial({ roughness: meta.roughness ?? 0.96, metalness: meta.metalness ?? 0.02 });
      disposableMats.push(mat);
      const mesh = new THREE.InstancedMesh(unitBox, mat, Math.max(1, solid.length));
      const matrix = new THREE.Matrix4(), p = new THREE.Vector3(), q = new THREE.Quaternion(), sc = new THREE.Vector3(), tint = new THREE.Color();
      solid.forEach((b, i) => {
        p.set(b.x, b.y, b.z);
        sc.set(b.w, b.h, b.d);
        q.setFromAxisAngle(THREE.Object3D.DEFAULT_UP, b.angle);
        mesh.setMatrixAt(i, matrix.compose(p, q, sc));
        mesh.setColorAt(i, tint.set(b.tint));
      });
      mesh.count = solid.length;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.computeBoundingSphere();
      g.add(mesh);
      disposableMeshes.push(mesh);

      let glowMesh = null, glowMat = null;
      if (glow.length) {
        glowMat = new THREE.MeshBasicMaterial({ color: "#ffffff", toneMapped: false });
        disposableMats.push(glowMat);
        glowMesh = new THREE.InstancedMesh(unitBox, glowMat, glow.length);
        glow.forEach((b, i) => {
          p.set(b.x, b.y, b.z);
          sc.set(b.w, b.h, b.d);
          q.identity();
          glowMesh.setMatrixAt(i, matrix.compose(p, q, sc));
          glowMesh.setColorAt(i, tint.set(b.tint));
        });
        glowMesh.computeBoundingSphere();
        g.add(glowMesh);
        disposableMeshes.push(glowMesh);
      }

      // Per-world snow caps on exposed upward faces
      const exposed = new Map();
      solid.filter(b => b.snowCap).forEach(b => {
        const key = `${b.x.toFixed(1)}:${b.z.toFixed(1)}`;
        if (!exposed.has(key) || exposed.get(key).y < b.y) exposed.set(key, b);
      });
      const snowMat = new THREE.MeshStandardMaterial({ color: "#edf4f6", roughness: 0.96 });
      disposableMats.push(snowMat);
      const snowCaps = new THREE.InstancedMesh(unitBox, snowMat, Math.max(1, exposed.size));
      [...exposed.values()].forEach((b, i) => {
        p.set(b.x, b.y + b.h / 2 + 0.026, b.z);
        sc.set(b.w * 1.01, 0.064, b.d * 1.01);
        q.identity();
        snowCaps.setMatrixAt(i, matrix.compose(p, q, sc));
      });
      snowCaps.count = exposed.size;
      snowCaps.receiveShadow = true;
      snowCaps.computeBoundingSphere();
      snowCaps.visible = false;
      g.add(snowCaps);
      disposableMeshes.push(snowCaps);

      themeGroups[id] = {
        group: g,
        mesh,
        material: mat,
        glowMesh,
        glowMat,
        snowCaps,
        animated,
        textureKey: meta.textureKey,
        roughness: meta.roughness ?? 0.96,
        lawnTint: meta.lawnTint || "#ffffff",
        ambientParticleColor: meta.ambientParticleColor || "#ffe4a2",
        ambientAlwaysOn: Boolean(meta.ambientAlwaysOn),
      };
    }

    return { group: g, box, glowBox, animated, foundationBorder, windowBox, stoneLantern, broadleafTree, pineTree, flowerCluster, finalize };
  }

  // =========================================================================
  // 1. CLASSIC — 林间暖阳花园庭院 (Refined lush garden courtyard with arbor)
  // =========================================================================
  {
    const w = createWorldBuilder("classic");
    w.foundationBorder("#c4c2a8", "#b5b69f");
    for (let x = 7.2; x < 19.5; x += 0.46) for (let z = -0.2; z < 17; z += 0.46) {
      if (pathDistance(x, z) > 1.02 || inHouse(x, z, 0.1)) continue;
      const colors = ["#cbc6ac", "#d6d1ba", "#c3c0a7", "#dfd8bf"];
      w.box(x, groundY + 0.024, z, 0.44, 0.07, 0.44, colors[Math.floor(noise(x, z, 1) * colors.length)], 0, noise(x, z) > 0.45);
    }
    // Timber pergola archway framing the stone path entrance
    for (const dx of [-1.25, 1.25]) {
      w.box(9.2 + dx, groundY + 1.35, 3.3, 0.18, 2.7, 0.18, "#7e5e40", 0, true);
      w.box(9.2 + dx, groundY + 1.35, 4.4, 0.18, 2.7, 0.18, "#7e5e40", 0, true);
      w.box(9.2 + dx, groundY + 2.72, 3.85, 0.18, 0.18, 1.5, "#8f6c49", 0, true);
    }
    for (let i = 0; i < 5; i++) w.box(9.2, groundY + 2.86, 3.15 + i * 0.35, 3.1, 0.12, 0.14, "#9d7954", 0, true);
    for (let i = 0; i < 16; i++) {
      const t = i / 15;
      w.box(7.9 + t * 2.6, groundY + 2.96 + (i % 2) * 0.07, 3.25 + (i % 3) * 0.45, 0.26, 0.16, 0.24, i % 3 ? "#6c8c5c" : "#89a570", 0, true);
      if (i % 3 === 1) w.box(7.95 + t * 2.5, groundY + 3.04, 3.45 + (i % 2) * 0.5, 0.14, 0.12, 0.14, "#e7b9b5", 0, true);
    }
    // Hanging warm lantern under the pergola
    w.box(9.2, groundY + 2.54, 3.85, 0.05, 0.32, 0.05, "#4b3b2d");
    w.glowBox(9.2, groundY + 2.34, 3.85, 0.18, 0.22, 0.18, "#ffe2a2");
    w.box(9.2, groundY + 2.46, 3.85, 0.24, 0.05, 0.24, "#5d4834");

    // Grass tufts, shrubs & flower borders
    for (let i = 0; i < 180; i++) {
      const x = noise(i, 1) * 34 - 17, z = noise(i, 2) * 32 - 15;
      if (inHouse(x, z, 0.65) || pathDistance(x, z) < 1.2) continue;
      const s = 0.65 + noise(i, 3) * 0.7;
      for (let k = 0; k < 3; k++) {
        const px = x + (k - 1) * 0.095 * s, h = (0.17 + noise(x, z, k) * 0.15) * s;
        w.box(px, groundY + h / 2, z + (k % 2) * 0.08, 0.055 * s, h, 0.05 * s, noise(x, z) > 0.5 ? "#6f8c66" : "#8ca276", (k - 1) * 0.25);
      }
      if (i % 5 === 0) w.flowerCluster(x, z, 0.72, i);
    }
    for (let i = 0; i < 36; i++) {
      const x = -5.9 + noise(i, 30) * 11.8;
      const z = i % 2 ? -5.22 - noise(i, 31) * 0.22 : 6.38 + noise(i, 32) * 0.22;
      if (pathDistance(x, z) < 1.1) continue;
      w.flowerCluster(x, z, 0.7 + noise(i, 33) * 0.25, i);
    }
    // Trees framing the vista & behind the window
    for (const [x, z, s, seed] of [
      [-8.8, -4.7, 1.22, 1], [-5.3, -9.2, 1.28, 2], [-7.1, -8.7, 0.98, 11], [4.1, -9.4, 1.38, 3],
      [9.4, -5.4, 1.1, 4], [-13.5, 2.5, 1.18, 5], [-15, -13, 1.85, 6], [0, -18, 1.85, 7],
      [12.8, -15.5, 1.75, 8], [-20, -2, 1.55, 9], [21, -7, 1.82, 10],
    ]) w.broadleafTree(x, z, s, seed);

    // Garden bench & stone lanterns
    const bx = -7.65, bz = 6.85;
    w.box(bx, groundY + 0.025, bz, 2.3, 0.055, 1.42, "#bcb9a0", -0.18, true);
    for (let i = 0; i < 3; i++) w.box(bx + (i - 1) * 0.33, groundY + 0.5, bz, 0.3, 0.085, 1.12, i % 2 ? "#a9855e" : "#b89469", -0.18, true);
    for (const dx of [-0.93, 0.93]) {
      w.box(bx + dx, groundY + 0.25, bz - 0.39, 0.12, 0.5, 0.12, "#72583e", -0.18, true);
      w.box(bx + dx, groundY + 0.25, bz + 0.39, 0.12, 0.5, 0.12, "#72583e", -0.18, true);
      w.box(bx + dx, groundY + 0.8, bz - 0.42, 0.1, 0.62, 0.1, "#72583e", -0.18, true);
    }
    for (let i = 0; i < 4; i++) w.box(bx, groundY + 0.84 + i * 0.14, bz - 0.42, 1.94, 0.1, 0.1, i % 2 ? "#a9855e" : "#b89469", -0.18, true);

    w.stoneLantern(-10.55, 4.05, 1.0);
    w.stoneLantern(8.55, 2.1, 0.92);
    w.stoneLantern(11.95, 11.45, 0.88);
    w.stoneLantern(-4.55, -5.35, 0.82);
    w.windowBox(-3.6, -4.78);

    // Climbing ivy around the exterior window
    for (const side of [-1, 1]) {
      const x = side < 0 ? -5.1 : -2.05;
      for (let i = 0; i < 8; i++) {
        const y = 1.75 + i * 0.3, z = -4.65 + (noise(i, side + 80) - 0.5) * 0.05;
        w.box(x + side * 0.03, y, z, 0.15, 0.11, 0.07, i % 2 ? "#6f8b60" : "#7e9a69", 0, true);
        if (i % 3 === 1) w.box(x + side * 0.02, y + 0.06, z + 0.02, 0.11, 0.08, 0.06, "#d9b47e", 0, true);
      }
    }
    // Low white-timber picket fence in background
    for (let i = 0; i < 13; i++) {
      const x = -10.5 + i * 1.48;
      w.box(x, groundY + 0.54, -7.1, 0.11, 1.08, 0.11, "#c2bfa6");
      if (i < 12) for (const h of [0.36, 0.77]) w.box(x + 0.74, groundY + h, -7.1, 1.47, 0.095, 0.085, "#d2ceb5", 0, h > 0.5);
    }
    w.finalize({ textureKey: "meadow", roughness: 0.96, ambientParticleColor: "#ffe4a2" });
  }

  // =========================================================================
  // 2. CABIN — 幽林秘境森林 (Deep Pine Forest, Woodland Brook, Moss & Lanterns)
  // =========================================================================
  {
    const w = createWorldBuilder("cabin");
    // Mossy log foundation rim
    w.foundationBorder("#73795e", "#5b624b");
    // Winding woodland stream flowing past the right/front side of the cabin
    const streamPoints = [[-16, -9.5], [-11, -8.2], [-6.5, -8.8], [3, -8.5], [8.8, -5.8], [11.6, -1.2], [11.9, 4.5], [9.8, 9.6], [6.2, 13.5], [2.5, 17.5]];
    const streamDist = (x, z) => {
      let d = Infinity;
      for (let i = 1; i < streamPoints.length; i++) {
        const [ax, az] = streamPoints[i - 1], [bx, bz] = streamPoints[i];
        const dx = bx - ax, dz = bz - az;
        const t = Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / (dx * dx + dz * dz)));
        d = Math.min(d, Math.hypot(x - ax - dx * t, z - az - dz * t));
      }
      return d;
    };
    for (let x = -15; x <= 15; x += 0.55) for (let z = -11; z <= 16; z += 0.55) {
      if (inHouse(x, z, 0.8)) continue;
      const d = streamDist(x, z);
      if (d < 1.15) {
        const waterTint = d < 0.65 ? (noise(x, z, 5) > 0.5 ? "#4a848c" : "#56959c") : "#6ba8ad";
        w.box(x, groundY + 0.012, z, 0.54, 0.04, 0.54, waterTint);
      } else if (d < 1.65 && noise(x, z, 8) > 0.35) {
        const h = 0.09 + noise(x, z, 9) * 0.14;
        w.box(x, groundY + h / 2, z, 0.42, h, 0.42, noise(x, z, 4) > 0.5 ? "#7a8072" : "#67725c", 0, true);
      }
    }
    // Arched timber footbridge over the brook from the front door
    for (let i = 0; i < 11; i++) {
      const bx = 7.4 + i * 0.55;
      const arch = Math.sin((i / 10) * Math.PI) * 0.46;
      w.box(bx, groundY + 0.16 + arch, 1.25, 0.46, 0.1, 2.2, i % 2 ? "#7a583b" : "#896444", 0, true);
      if (i % 2 === 0) for (const side of [-1.02, 1.02]) {
        w.box(bx, groundY + 0.62 + arch, 1.25 + side, 0.12, 0.86, 0.12, "#5c422c", 0, true);
      }
    }
    for (const side of [-1.02, 1.02]) w.box(10.15, groundY + 1.15, 1.25 + side, 5.6, 0.1, 0.12, "#6c4e35", 0, true);

    // Dense tiered alpine pines + silver birch trunks surrounding the cabin
    for (const [x, z, s, seed] of [
      [-9.5, -5.2, 1.35, 1], [-6.2, -9.6, 1.5, 2], [-3.2, -10.8, 1.3, 3], [0.8, -9.8, 1.45, 4],
      [5.2, -9.2, 1.55, 5], [9.8, -8.5, 1.4, 6], [-12.5, -1.8, 1.45, 7], [-11.2, 3.8, 1.25, 8],
      [-14.8, 8.2, 1.35, 9], [14.8, -3.5, 1.45, 10], [16.2, 2.8, 1.55, 11], [14.5, 8.5, 1.35, 12],
      [-16.5, -11.5, 2.0, 13], [-8.5, -16.5, 2.1, 14], [2.5, -17.5, 2.2, 15], [13.5, -14.5, 2.0, 16],
      [-19.5, 1.5, 1.85, 17], [19.8, -7.2, 1.9, 18],
    ]) w.pineTree(x, z, s, seed);
    // Warm autumn/birch accents near window vista
    w.broadleafTree(-7.6, -7.8, 1.05, 21, ["#658852", "#88a45c", "#c8a45a", "#527345", "#a99953"], "#d7d0be");
    w.broadleafTree(7.8, -6.2, 0.95, 22, ["#587d4b", "#779759", "#bd9852", "#47693f"], "#d7d0be");

    // Mossy woodland boulders, felled log, and glowing forest mushrooms
    for (const [x, z, s] of [[-8.2, 1.8, 1.2], [-8.6, 5.8, 1.4], [-3.8, 7.6, 1.1], [3.5, 7.9, 1.25], [8.5, 5.4, 1.15], [-2.0, -6.6, 1.3]]) {
      w.box(x, groundY + 0.32 * s, z, 0.95 * s, 0.64 * s, 0.82 * s, "#6b7068", noise(x, z) * 0.5, true);
      w.box(x + 0.1 * s, groundY + 0.66 * s, z - 0.05 * s, 0.72 * s, 0.12 * s, 0.64 * s, "#5a7c4b", 0, true);
    }
    // Red-cap & glowing forest mushrooms
    for (const [x, z] of [[-7.2, 3.2], [-6.8, 3.6], [-4.4, 6.8], [2.4, 7.1], [7.8, 4.4], [-7.4, -3.2]]) {
      w.box(x, groundY + 0.12, z, 0.08, 0.24, 0.08, "#e6dec8");
      w.box(x, groundY + 0.27, z, 0.24, 0.12, 0.24, "#b85549", 0, true);
      w.box(x + 0.06, groundY + 0.34, z + 0.05, 0.05, 0.03, 0.05, "#f5ecd9");
    }
    // Woodland lanterns & camp woodpile
    w.stoneLantern(-7.4, 6.2, 0.95, "#7d8274", "#4f4030", "#ffd98a");
    w.stoneLantern(7.7, -0.4, 0.9, "#7d8274", "#4f4030", "#ffd98a");
    w.stoneLantern(12.6, 2.7, 0.9, "#7d8274", "#4f4030", "#ffd98a");
    w.stoneLantern(-4.4, -5.6, 0.85, "#7d8274", "#4f4030", "#ffd98a");
    w.windowBox(-3.6, -4.78, "#6f5036", "#4c3624", "#4c7548", "#6f925b", "#e8c488", "#d78b72");

    // Forest ferns & undergrowth
    for (let i = 0; i < 160; i++) {
      const x = noise(i, 41) * 30 - 15, z = noise(i, 42) * 28 - 13;
      if (inHouse(x, z, 0.7) || streamDist(x, z) < 1.3) continue;
      const h = 0.2 + noise(i, 43) * 0.28;
      w.box(x, groundY + h / 2, z, 0.22, h, 0.22, i % 3 ? "#4b6f47" : "#648653", noise(i, 44), true);
      if (i % 9 === 0) w.flowerCluster(x, z, 0.65, i, ["#f0dca8", "#e5c19c", "#d8e5b8"]);
    }
    w.finalize({ textureKey: "forest", roughness: 0.98, ambientParticleColor: "#c8f09c", ambientAlwaysOn: true });
  }

  // =========================================================================
  // 3. COASTAL — 临海白屋岬角 (Cliffside Terrace, Turquoise Ocean, Lighthouse & Sailboats)
  // =========================================================================
  {
    const w = createWorldBuilder("coastal");
    // Pale limestone coastal headland island under the white house
    for (let x = -8.6; x <= 9.6; x += 0.95) for (let z = -7.4; z <= 8.6; z += 0.95) {
      const rx = (x - 0.4) / 9.2, rz = (z - 0.5) / 8.2;
      const edge = Math.hypot(rx, rz) + (noise(x, z, 15) - 0.5) * 0.16;
      if (edge > 1.05) continue;
      const isSand = edge > 0.82;
      const h = isSand ? 0.22 : 0.42;
      w.box(x, groundY + h / 2, z, 0.96, h, 0.96, isSand ? (noise(x, z) > 0.5 ? "#e5d9bc" : "#d9cca9") : (noise(x, z) > 0.5 ? "#e6e3d3" : "#d6d4c3"), 0, true);
    }
    w.foundationBorder("#dcdad0", "#c9cfc7");

    // Wooden pier extending from the front door out over the sea
    for (let i = 0; i < 14; i++) {
      const px = 7.1 + i * 0.62;
      w.box(px, groundY + 0.44, 1.25, 0.52, 0.1, 2.35, i % 2 ? "#cbb28c" : "#bfa37a", 0, true);
      if (i % 3 === 0) for (const dz of [-1.05, 1.05]) {
        w.box(px, groundY + 0.46, 1.25 + dz, 0.16, 1.12, 0.16, "#856a4c", 0, true);
        w.box(px, groundY + 1.04, 1.25 + dz, 0.2, 0.08, 0.2, "#f4f2e8", 0, true);
      }
    }
    // Rope railings along pier
    for (const dz of [-1.05, 1.05]) w.box(11.1, groundY + 0.86, 1.25 + dz, 8.2, 0.05, 0.05, "#dfd3b8");

    // White & blue Mediterranean low terrace balustrade on the cliff edge
    for (let x = -7.4; x <= 7.8; x += 1.45) {
      w.box(x, groundY + 0.78, -6.2, 0.24, 0.76, 0.24, "#f4f3ea", 0, true);
      if (x < 7.0) w.box(x + 0.72, groundY + 1.12, -6.2, 1.46, 0.12, 0.22, "#4e869c", 0, true);
    }

    // Iconic red-and-white voxel lighthouse on a rocky sea stack behind the window
    const lx = -11.5, lz = -13.5;
    for (let ix = -2; ix <= 2; ix++) for (let iz = -2; iz <= 2; iz++) {
      if (Math.hypot(ix, iz) > 2.4) continue;
      const rh = 0.7 + (2.5 - Math.hypot(ix, iz)) * 0.55;
      w.box(lx + ix * 0.95, groundY + rh / 2, lz + iz * 0.95, 0.98, rh, 0.98, (ix + iz) % 2 ? "#7f8689" : "#6c757a", 0, true);
    }
    for (let tier = 0; tier < 9; tier++) {
      const s = 1.85 - tier * 0.1;
      w.box(lx, groundY + 1.7 + tier * 0.78, lz, s, 0.78, s, tier % 2 ? "#c95b52" : "#f4f1e6", 0, tier === 8);
    }
    w.box(lx, groundY + 8.85, lz, 1.55, 0.18, 1.55, "#3e5f70", 0, true);
    w.glowBox(lx, groundY + 9.45, lz, 0.95, 0.95, 0.95, "#fff0b0");
    w.box(lx, groundY + 10.1, lz, 1.35, 0.35, 1.35, "#c95b52", 0, true);
    w.box(lx, groundY + 10.5, lz, 0.65, 0.45, 0.65, "#c95b52", 0, true);

    // Coastal sea stacks & rocky outcrops
    for (const [rx, rz, rs] of [[11.5, -11.8, 1.4], [-14.2, -3.5, 1.1], [15.5, 9.8, 1.2], [-10.5, 10.5, 1.0]]) {
      w.box(rx, groundY + 0.65 * rs, rz, 2.2 * rs, 1.3 * rs, 1.8 * rs, "#818a8e", 0.3, true);
      w.box(rx + 0.4 * rs, groundY + 1.2 * rs, rz - 0.3 * rs, 1.2 * rs, 0.9 * rs, 1.2 * rs, "#949ca0", 0.2, true);
    }

    // Coastal pine / olive trees & bougainvillea shrubs on the terrace
    for (const [x, z, s, seed] of [[-7.2, -4.6, 0.95, 31], [-7.5, 4.8, 0.9, 32], [6.6, -5.2, 1.0, 33]]) {
      w.broadleafTree(x, z, s, seed, ["#58856d", "#6e9a81", "#85ab92", "#476e5c"], "#9c8d74");
    }
    for (let i = 0; i < 22; i++) {
      const a = (i / 22) * Math.PI * 2;
      const px = Math.cos(a) * 7.2, pz = Math.sin(a) * 6.5;
      if (inHouse(px, pz, 0.5) || (px > 5.5 && Math.abs(pz - 1.25) < 2.2)) continue;
      w.flowerCluster(px, pz, 0.85, i + 70, ["#e37c8b", "#f4ede0", "#7fb5c7"]);
    }
    // Sun deck lounger & beach umbrella
    w.box(-7.1, groundY + 0.55, 6.2, 1.9, 0.12, 0.9, "#f3eee2", -0.22, true);
    w.box(-7.8, groundY + 0.78, 6.05, 0.5, 0.42, 0.88, "#568fa4", -0.22, true);
    w.box(-6.6, groundY + 1.45, 7.1, 0.1, 2.3, 0.1, "#c7ab82");
    for (let i = -3; i <= 3; i++) for (let j = -3; j <= 3; j++) {
      if (Math.hypot(i, j) > 3.3) continue;
      const drop = Math.hypot(i, j) * 0.08;
      w.box(-6.6 + i * 0.34, groundY + 2.65 - drop, 7.1 + j * 0.34, 0.34, 0.1, 0.34, (Math.abs(i) + Math.abs(j)) % 2 ? "#f6f2e8" : "#4b8ea4", 0, true);
    }

    w.stoneLantern(7.8, -0.35, 0.9, "#ebe8dc", "#4a7f94", "#ffe7a8");
    w.stoneLantern(14.5, 2.4, 0.85, "#ebe8dc", "#4a7f94", "#ffe7a8");
    w.windowBox(-3.6, -4.78, "#5e8d9b", "#3d6f84", "#5c8c72", "#7aa68d", "#e37c8b", "#f6f2e8");

    // Animated bobbing sailboats on the sea (separate small groups)
    function addSailboat(x, z, scale, phase, hullColor = "#e7e2d5", sailColor = "#ffffff") {
      const boat = new THREE.Group();
      boat.position.set(x, groundY + 0.12, z);
      const bMat = new THREE.MeshStandardMaterial({ roughness: 0.7 });
      disposableMats.push(bMat);
      const parts = [
        [0, 0.12, 0, 1.8, 0.32, 0.76, hullColor],
        [0, 0.32, 0, 1.45, 0.12, 0.6, "#4c8398"],
        [0.05, 1.25, 0, 0.08, 1.9, 0.08, "#8b6d4c"],
        [0.45, 1.3, 0, 0.75, 1.25, 0.06, sailColor],
        [-0.32, 1.1, 0, 0.55, 0.9, 0.06, "#f2ebd9"],
      ];
      const m = new THREE.InstancedMesh(unitBox, bMat, parts.length);
      const mat4 = new THREE.Matrix4(), p = new THREE.Vector3(), q = new THREE.Quaternion(), sc = new THREE.Vector3(), c = new THREE.Color();
      parts.forEach((pt, idx) => {
        p.set(pt[0] * scale, pt[1] * scale, pt[2] * scale);
        sc.set(pt[3] * scale, pt[4] * scale, pt[5] * scale);
        m.setMatrixAt(idx, mat4.compose(p, q, sc));
        m.setColorAt(idx, c.set(pt[6]));
      });
      m.castShadow = true;
      boat.add(m);
      w.group.add(boat);
      disposableMeshes.push(m);
      w.animated.push({
        update(t) {
          boat.position.y = groundY + 0.14 + Math.sin(t * 1.4 + phase) * 0.1;
          boat.rotation.z = Math.sin(t * 1.1 + phase) * 0.06;
          boat.rotation.y = Math.sin(t * 0.25 + phase) * 0.15 + 0.4;
        },
      });
    }
    addSailboat(-3.5, -13.2, 1.15, 0.4);
    addSailboat(16.8, -5.2, 1.0, 2.2, "#c86c5a", "#faf5ea");
    addSailboat(9.5, 13.8, 0.92, 4.1);

    // Shimmering foam crest strips that gently oscillate
    const foamMat = new THREE.MeshBasicMaterial({ color: "#eaf7f8", transparent: true, opacity: 0.46, depthWrite: false });
    disposableMats.push(foamMat);
    const foamCount = 44;
    const foamMesh = new THREE.InstancedMesh(unitBox, foamMat, foamCount);
    const foamSeeds = Array.from({ length: foamCount }, (_, i) => {
      const ang = (i / foamCount) * Math.PI * 2;
      const dist = 10.2 + noise(i, 91) * 14;
      return { x: Math.cos(ang) * dist, z: Math.sin(ang) * dist, w: 1.1 + noise(i, 92) * 1.8, phase: noise(i, 93) * 6.28 };
    });
    w.group.add(foamMesh);
    disposableMeshes.push(foamMesh);
    const fM = new THREE.Matrix4(), fP = new THREE.Vector3(), fQ = new THREE.Quaternion(), fS = new THREE.Vector3();
    w.animated.push({
      update(t) {
        foamSeeds.forEach((s, i) => {
          const drift = Math.sin(t * 0.9 + s.phase) * 0.55;
          fP.set(s.x + drift, groundY + 0.03, s.z + Math.cos(t * 0.6 + s.phase) * 0.25);
          fS.set(s.w * (0.75 + 0.25 * Math.sin(t * 1.3 + s.phase)), 0.03, 0.16);
          foamMesh.setMatrixAt(i, fM.compose(fP, fQ, fS));
        });
        foamMesh.instanceMatrix.needsUpdate = true;
      },
    });

    w.finalize({ textureKey: "ocean", roughness: 0.72, ambientParticleColor: "#e8fbff", ambientAlwaysOn: true });
  }

  // =========================================================================
  // 4. FRENCH — 山巅云海观景台 (Alpine Summit Terrace, Snow Peaks, Sea of Clouds)
  // =========================================================================
  {
    const w = createWorldBuilder("french");
    // Stepped stone summit cliff plateau beneath the house
    for (let x = -8.2; x <= 9.2; x += 0.92) for (let z = -7.0; z <= 8.2; z += 0.92) {
      const edge = Math.hypot((x - 0.4) / 8.8, (z - 0.5) / 7.8) + (noise(x, z, 25) - 0.5) * 0.14;
      if (edge > 1.03) continue;
      const h = edge > 0.84 ? 0.28 : 0.48;
      const tint = edge > 0.88 ? "#8e96a1" : (noise(x, z) > 0.5 ? "#d8d1c2" : "#cbc4b4");
      w.box(x, groundY + h / 2, z, 0.94, h, 0.94, tint, 0, true);
    }
    w.foundationBorder("#d9d2c3", "#c8c0b0");

    // Classical alpine stone balustrade & brass telescope on the terrace
    for (let x = -7.2; x <= 7.4; x += 1.2) {
      w.box(x, groundY + 0.82, -5.9, 0.22, 0.84, 0.22, "#eee7d8", 0, true);
      if (x < 7.0) w.box(x + 0.6, groundY + 1.24, -5.9, 1.22, 0.12, 0.26, "#f4ede0", 0, true);
    }
    for (let z = -5.4; z <= 6.2; z += 1.25) {
      w.box(-7.2, groundY + 0.82, z, 0.22, 0.84, 0.22, "#eee7d8", 0, true);
      if (z < 5.6) w.box(-7.2, groundY + 1.24, z + 0.62, 0.26, 0.12, 1.26, "#f4ede0", 0, true);
    }
    // Summit viewing deck leading from front door
    for (let i = 0; i < 6; i++) w.box(7.2 + i * 0.55, groundY + 0.46, 1.25, 0.5, 0.14, 2.5, i % 2 ? "#ded7c7" : "#d0c8b8", 0, true);
    // Brass observatory telescope on the front-right overlook
    w.box(9.6, groundY + 0.92, 0.45, 0.12, 0.95, 0.12, "#5c4b3c");
    w.box(9.6, groundY + 1.46, 0.45, 0.18, 0.18, 0.95, "#cfa75f", -0.45);
    w.box(9.6, groundY + 1.48, 0.9, 0.24, 0.24, 0.2, "#a98142", -0.45);

    // Majestic terraced voxel mountain range & snow-capped peaks around the horizon
    function mountainPeak(cx, cz, baseR, height, seed) {
      const step = 1.65;
      const levels = Math.floor(height / 1.15);
      for (let lv = 0; lv < levels; lv++) {
        const ratio = 1 - lv / levels;
        const r = baseR * ratio;
        const y = groundY + 0.55 + lv * 1.12;
        const snowLine = lv > levels * 0.58;
        for (let dx = -r; dx <= r; dx += step) for (let dz = -r; dz <= r; dz += step) {
          const dist = Math.hypot(dx, dz);
          if (dist > r * (0.85 + noise(dx + seed, dz, lv) * 0.28)) continue;
          const tint = snowLine
            ? (noise(dx, dz, lv) > 0.35 ? "#edf3f7" : "#dbe6ee")
            : lv > levels * 0.32
              ? (noise(dx, dz, lv) > 0.5 ? "#7b8796" : "#6a7788")
              : (noise(dx, dz, lv) > 0.5 ? "#5b7167" : "#4d635b");
          w.box(cx + dx, y, cz + dz, step * 1.04, 1.18, step * 1.04, tint, 0, snowLine);
        }
      }
    }
    mountainPeak(-16.5, -18.5, 8.2, 13.5, 1);
    mountainPeak(-3.2, -23.5, 9.8, 16.5, 2);
    mountainPeak(12.8, -19.5, 8.8, 14.5, 3);
    mountainPeak(-22.5, -4.5, 7.2, 10.8, 4);
    mountainPeak(22.5, -7.2, 7.6, 11.4, 5);

    // Alpine pines & cypress on the cliff corners
    for (const [x, z, s, seed] of [[-7.2, -4.8, 1.05, 51], [-7.5, 4.6, 0.95, 52], [7.2, -5.2, 1.1, 53], [-5.2, -8.2, 0.85, 54]]) {
      w.pineTree(x, z, s, seed, ["#3a5a52", "#4c6d63", "#608174", "#2f4a44"], "#675748");
    }
    w.stoneLantern(-6.8, 6.2, 0.95, "#e6dfd0", "#9c876a", "#ffe6b0");
    w.stoneLantern(8.6, 2.4, 0.9, "#e6dfd0", "#9c876a", "#ffe6b0");
    w.stoneLantern(-4.2, -5.6, 0.85, "#e6dfd0", "#9c876a", "#ffe6b0");
    w.windowBox(-3.6, -4.78, "#d9c7a9", "#b59b77", "#678572", "#84a08b", "#f3e8d8", "#d7a6ab");

    // Drifting sea-of-clouds layer below & around the summit plateau
    const cloudMat = new THREE.MeshStandardMaterial({ color: "#f5f8fb", roughness: 1, transparent: true, opacity: 0.78, depthWrite: false });
    disposableMats.push(cloudMat);
    const cloudSeeds = [];
    for (let i = 0; i < 42; i++) {
      const ang = (i / 42) * Math.PI * 2;
      const dist = 10.8 + noise(i, 101) * 14;
      cloudSeeds.push({
        x: Math.cos(ang) * dist,
        y: groundY + 0.2 + (noise(i, 102) - 0.3) * 2.2,
        z: Math.sin(ang) * dist,
        sx: 3.4 + noise(i, 103) * 3.8,
        sy: 0.7 + noise(i, 104) * 0.8,
        sz: 2.2 + noise(i, 105) * 2.6,
        speed: 0.2 + noise(i, 106) * 0.35,
        phase: noise(i, 107) * 6.28,
      });
    }
    const seaClouds = new THREE.InstancedMesh(unitBox, cloudMat, cloudSeeds.length);
    w.group.add(seaClouds);
    disposableMeshes.push(seaClouds);
    const cM = new THREE.Matrix4(), cP = new THREE.Vector3(), cQ = new THREE.Quaternion(), cS = new THREE.Vector3();
    w.animated.push({
      update(t, light) {
        cloudMat.color.set(light === "night" ? "#5a7290" : light === "dusk" ? "#f4c8b6" : light === "dawn" ? "#f5e3d6" : "#f5f8fb");
        cloudSeeds.forEach((s, i) => {
          cP.set(s.x + Math.sin(t * s.speed + s.phase) * 1.4, s.y + Math.cos(t * 0.3 + s.phase) * 0.18, s.z);
          cS.set(s.sx, s.sy, s.sz);
          seaClouds.setMatrixAt(i, cM.compose(cP, cQ, cS));
        });
        seaClouds.instanceMatrix.needsUpdate = true;
      },
    });

    w.finalize({ textureKey: "alpine", roughness: 0.92, ambientParticleColor: "#fff2d2", ambientAlwaysOn: true });
  }

  // =========================================================================
  // 5. LOFT — 城市天台暮景 (Rooftop Terrace, Illuminated City Skyline, Neon & Water Tower)
  // =========================================================================
  {
    const w = createWorldBuilder("loft");
    // Concrete rooftop terrace slab & steel perimeter parapet
    for (let x = -8.4; x <= 9.4; x += 1.2) for (let z = -6.8; z <= 8.2; z += 1.2) {
      w.box(x, groundY + 0.18, z, 1.18, 0.36, 1.18, (Math.round(x + z) % 2) ? "#6d747c" : "#626972");
    }
    w.foundationBorder("#596068", "#4d545c");
    // Parapet wall & steel railings
    for (let x = -8.4; x <= 9.4; x += 1.2) {
      w.box(x, groundY + 0.64, -6.9, 1.18, 0.56, 0.34, "#7a7068", 0, true);
      w.box(x, groundY + 0.96, -6.9, 1.22, 0.1, 0.42, "#979ba0", 0, true);
    }
    for (let z = -6.4; z <= 8.0; z += 1.2) {
      w.box(-8.5, groundY + 0.64, z, 0.34, 0.56, 1.18, "#7a7068", 0, true);
      w.box(-8.5, groundY + 0.96, z, 0.42, 0.1, 1.22, "#979ba0", 0, true);
    }
    // Rooftop string-light poles & warm festoon bulbs
    const poles = [[-7.6, 7.2], [7.8, 7.2], [8.2, -5.8], [-7.6, -5.8]];
    poles.forEach(([px, pz]) => {
      w.box(px, groundY + 1.9, pz, 0.1, 3.2, 0.1, "#333b42");
      w.glowBox(px, groundY + 3.45, pz, 0.16, 0.16, 0.16, "#ffd28a");
    });
    for (let i = 1; i < 8; i++) {
      const t = i / 8;
      const sag = Math.sin(t * Math.PI) * 0.42;
      w.glowBox(-7.6 + t * 15.4, groundY + 3.4 - sag, 7.2, 0.12, 0.12, 0.12, i % 2 ? "#ffcf82" : "#ffe6b3");
      w.glowBox(8.2, groundY + 3.4 - sag, -5.8 + t * 13.0, 0.12, 0.12, 0.12, i % 2 ? "#ffe6b3" : "#ffcf82");
    }

    // Classic timber rooftop water tower on steel legs
    const wx = -9.6, wz = -8.6;
    for (const dx of [-0.65, 0.65]) for (const dz of [-0.65, 0.65]) w.box(wx + dx, groundY + 1.4, wz + dz, 0.16, 2.8, 0.16, "#394248");
    w.box(wx, groundY + 2.85, wz, 2.1, 0.16, 2.1, "#444d54");
    w.box(wx, groundY + 4.05, wz, 1.85, 2.2, 1.85, "#8a684e");
    for (const y of [3.4, 4.05, 4.7]) w.box(wx, groundY + y, wz, 1.92, 0.09, 1.92, "#394248");
    w.box(wx, groundY + 5.3, wz, 2.1, 0.25, 2.1, "#4b555c", 0, true);
    w.box(wx, groundY + 5.6, wz, 1.3, 0.35, 1.3, "#4b555c", 0, true);

    // Multi-layered voxel city skyline with glowing apartment windows & neon accents
    const towers = [
      [-15.5, -15.2, 4.0, 12.8, 3.8, "#47515e", "#ffcf87"],
      [-10.2, -17.5, 4.6, 16.5, 4.2, "#3b4654", "#8ce4f2"],
      [-4.2, -16.8, 4.4, 14.2, 3.8, "#525a66", "#ffd694"],
      [2.2, -18.8, 5.2, 18.4, 4.6, "#394452", "#ffca80"],
      [8.8, -16.2, 4.2, 13.6, 4.0, "#4c5460", "#9ae3eb"],
      [15.2, -14.8, 4.8, 15.8, 4.2, "#414a57", "#ffcf87"],
      [-18.5, -6.5, 4.0, 11.4, 4.4, "#4b5461", "#ffd694"],
      [-19.2, 1.8, 4.4, 10.2, 4.2, "#3f4956", "#8ce4f2"],
      [18.8, -5.5, 4.2, 12.6, 4.4, "#454f5d", "#ffcf87"],
      [19.6, 3.8, 4.6, 10.6, 4.0, "#3b4654", "#ffd694"],
    ];
    towers.forEach(([tx, tz, tw, th, td, bodyTint, winTint], ti) => {
      w.box(tx, groundY + th / 2, tz, tw, th, td, bodyTint, 0, true);
      w.box(tx, groundY + th + 0.45, tz, tw * 0.62, 0.9, td * 0.62, "#313942", 0, true);
      // Aviation beacon on tall skyscrapers
      if (th > 14) {
        w.box(tx, groundY + th + 1.6, tz, 0.14, 1.8, 0.14, "#6f7782");
        w.glowBox(tx, groundY + th + 2.55, tz, 0.24, 0.24, 0.24, "#ff6b6b");
      }
      // Illuminated voxel windows on inner-facing facades
      const cols = Math.floor(tw / 0.85), rows = Math.floor((th - 1.5) / 1.15);
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        if (noise(c + ti * 7, r, 19) < 0.42) continue;
        const wxp = tx - tw / 2 + 0.55 + c * 0.82;
        const wyp = groundY + 1.4 + r * 1.12;
        const col = noise(c, r + ti, 23) > 0.78 ? "#9be4f0" : winTint;
        if (tz < -8) w.glowBox(wxp, wyp, tz + td / 2 + 0.04, 0.42, 0.58, 0.06, col);
        if (tx < -12) w.glowBox(tx + tw / 2 + 0.04, wyp, tz - td / 2 + 0.55 + (c % 4) * 0.9, 0.06, 0.58, 0.42, col);
        if (tx > 12) w.glowBox(tx - tw / 2 - 0.04, wyp, tz - td / 2 + 0.55 + (c % 4) * 0.9, 0.06, 0.58, 0.42, col);
      }
      // Subtle rooftop neon sign on select buildings
      if (ti === 1 || ti === 3) {
        w.glowBox(tx, groundY + th + 0.6, tz + td / 2 + 0.08, tw * 0.55, 0.42, 0.08, ti === 1 ? "#ff8b7b" : "#7ad8e8");
      }
    });

    // Rooftop planter boxes & AC unit for industrial loft authenticity
    for (const [px, pz] of [[-7.4, 2.2], [-7.4, 5.2], [7.4, 4.4], [2.4, 7.1]]) {
      w.box(px, groundY + 0.58, pz, 1.35, 0.52, 0.72, "#7e5d4b", 0, true);
      w.box(px, groundY + 0.98, pz, 1.15, 0.44, 0.54, "#587954", 0, true);
    }
    w.windowBox(-3.6, -4.78, "#4b555c", "#343c42", "#597b58", "#74926c", "#e49d6a", "#eed29d");
    w.finalize({ textureKey: "city", roughness: 0.88, metalness: 0.06, ambientParticleColor: "#ffd892", ambientAlwaysOn: true });
  }

  // =========================================================================
  // 6. WASHITSU — 竹林枯山水庭院 (Zen Raked Gravel, Bamboo Grove, Koi Pond & Red Bridge)
  // =========================================================================
  {
    const w = createWorldBuilder("washitsu");
    w.foundationBorder("#9c9884", "#8c8875");
    // Stepping stones from the entrance door
    for (let i = 0; i < 11; i++) {
      const sx = 7.4 + i * 0.72, sz = 1.25 + Math.sin(i * 0.55) * 0.9;
      w.box(sx, groundY + 0.04, sz, 0.58, 0.09, 0.48, i % 2 ? "#8b8d83" : "#7c7f76", i * 0.2, true);
    }
    // Tranquil koi pond on the front-right courtyard with arched red-lacquer bridge
    for (let x = 3.5; x <= 12.2; x += 0.55) for (let z = 6.2; z <= 12.4; z += 0.55) {
      const d = Math.hypot((x - 7.8) / 4.2, (z - 9.2) / 3.0);
      if (d > 1.0) continue;
      if (d > 0.84) {
        const rh = 0.12 + noise(x, z, 31) * 0.22;
        w.box(x, groundY + rh / 2, z, 0.54, rh, 0.54, noise(x, z) > 0.5 ? "#7a7d73" : "#666b63", 0, true);
      } else {
        w.box(x, groundY + 0.02, z, 0.55, 0.04, 0.55, d < 0.45 ? "#3d7474" : "#4d8784");
        // Floating lily pads & koi accents
        if (noise(x, z, 33) > 0.86) w.box(x, groundY + 0.055, z, 0.34, 0.03, 0.34, "#5d8754");
        if (noise(x, z, 34) > 0.94) w.box(x, groundY + 0.045, z, 0.22, 0.03, 0.09, "#e67c4c", 0.5);
      }
    }
    // Tall bamboo grove framing the back window & left wall
    function bamboo(x, z, h, seed) {
      const segs = Math.floor(h / 0.65);
      for (let i = 0; i < segs; i++) {
        w.box(x, groundY + 0.32 + i * 0.65, z, 0.14, 0.61, 0.14, i % 2 ? "#698a52" : "#77995e");
        w.box(x, groundY + 0.62 + i * 0.65, z, 0.17, 0.05, 0.17, "#93ad76");
        if (i > segs - 4) {
          for (let k = 0; k < 3; k++) {
            const a = seed + k * 2.1 + i;
            w.box(x + Math.cos(a) * 0.36, groundY + 0.5 + i * 0.65, z + Math.sin(a) * 0.36, 0.42, 0.08, 0.24, "#587d47", a, true);
          }
        }
      }
    }
    for (let i = 0; i < 42; i++) {
      const side = i % 3;
      let bx, bz;
      if (side === 0) { bx = -10.5 + noise(i, 61) * 20; bz = -7.2 - noise(i, 62) * 5.2; }
      else if (side === 1) { bx = -8.2 - noise(i, 63) * 4.6; bz = -6.0 + noise(i, 64) * 13.5; }
      else { bx = 8.4 + noise(i, 65) * 4.5; bz = -6.5 + noise(i, 66) * 5.2; }
      bamboo(bx, bz, 3.8 + noise(i, 67) * 3.4, i);
    }
    // Red-maple (Momiji) accent tree visible beyond the shoji window
    w.broadleafTree(-6.5, -8.5, 1.15, 77, ["#b84f3f", "#cc664b", "#d98256", "#9a3f34", "#e3a26b"], "#5c4738");
    w.broadleafTree(8.2, -6.5, 1.0, 78, ["#b84f3f", "#cc664b", "#d98256", "#9a3f34"], "#5c4738");

    // Zen mossy rocks & stone lanterns
    for (const [x, z, s] of [[-4.2, 7.8, 1.2], [-1.2, 8.6, 0.9], [-8.2, 7.2, 1.15]]) {
      w.box(x, groundY + 0.36 * s, z, 1.1 * s, 0.72 * s, 0.85 * s, "#777b74", 0.3, true);
      w.box(x, groundY + 0.74 * s, z, 0.8 * s, 0.1 * s, 0.62 * s, "#658454", 0.3, true);
    }
    w.stoneLantern(-7.4, 5.4, 1.0, "#a8a596", "#7a796e", "#ffdfa2");
    w.stoneLantern(8.5, 4.6, 0.95, "#a8a596", "#7a796e", "#ffdfa2");
    w.stoneLantern(-4.5, -5.6, 0.88, "#a8a596", "#7a796e", "#ffdfa2");
    w.windowBox(-3.6, -4.78, "#997b4c", "#635b3e", "#5d7d4b", "#789862", "#d96b52", "#f0e2c6");
    w.finalize({ textureKey: "zen", roughness: 0.97, ambientParticleColor: "#ffdca0" });
  }

  // =========================================================================
  // Shared Atmospheric Layers (Fireflies / Motes, Puddles, Far Clouds, Weather Particles)
  // =========================================================================
  const flyCanvas = document.createElement("canvas");
  flyCanvas.width = flyCanvas.height = 32;
  const flyPainter = flyCanvas.getContext("2d");
  const flyGlow = flyPainter.createRadialGradient(16, 16, 1, 16, 16, 15);
  flyGlow.addColorStop(0, "rgba(255,255,230,1)");
  flyGlow.addColorStop(0.35, "rgba(255,232,165,0.85)");
  flyGlow.addColorStop(1, "rgba(255,210,120,0)");
  flyPainter.fillStyle = flyGlow;
  flyPainter.fillRect(0, 0, 32, 32);
  const flyTexture = new THREE.CanvasTexture(flyCanvas);

  const flyCount = 42, flyBase = [], flyPhase = [], flyPositions = new Float32Array(flyCount * 3);
  for (let i = 0; i < flyCount; i++) {
    let x, z, guard = 0;
    do {
      if (i < 18) { x = -7 + noise(i, 61) * 16; z = 6.8 + noise(i, 62) * 6.5; }
      else if (i < 30) { x = -14 + noise(i, 63) * 8; z = -5 + noise(i, 64) * 14; }
      else { x = 6.8 + noise(i, 65) * 8; z = -4 + noise(i, 66) * 14; }
    } while (inHouse(x, z, 0.5) && ++guard < 12);
    const y = groundY + 0.55 + noise(i, 67) * 2.1;
    flyBase.push(x, y, z);
    flyPhase.push(noise(i, 68) * Math.PI * 2);
    flyPositions[i * 3] = x;
    flyPositions[i * 3 + 1] = y;
    flyPositions[i * 3 + 2] = z;
  }
  const flyGeometry = new THREE.BufferGeometry();
  flyGeometry.setAttribute("position", new THREE.BufferAttribute(flyPositions, 3).setUsage(THREE.DynamicDrawUsage));
  const flyMaterial = new THREE.PointsMaterial({
    map: flyTexture, color: "#ffe4a2", size: 4.8, sizeAttenuation: false,
    transparent: true, opacity: 0.85, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  const fireflies = new THREE.Points(flyGeometry, flyMaterial);
  fireflies.frustumCulled = false;
  fireflies.visible = false;
  root.add(fireflies);
  disposableGeos.push(flyGeometry);
  disposableMats.push(flyMaterial);

  function poseFireflies(time) {
    const pos = flyGeometry.attributes.position;
    for (let i = 0; i < flyCount; i++) {
      const ph = flyPhase[i];
      pos.array[i * 3] = flyBase[i * 3] + Math.sin(time * 0.45 + ph) * 0.42;
      pos.array[i * 3 + 1] = flyBase[i * 3 + 1] + Math.sin(time * 0.62 + ph * 1.7) * 0.24;
      pos.array[i * 3 + 2] = flyBase[i * 3 + 2] + Math.cos(time * 0.38 + ph * 2.1) * 0.36;
    }
    pos.needsUpdate = true;
    flyMaterial.opacity = 0.55 + 0.3 * Math.sin(time * 0.9);
  }

  // Wet puddles for rainy weather
  const wetMaterial = new THREE.MeshStandardMaterial({ color: "#9fbcc3", roughness: 0.18, metalness: 0.12, transparent: true, opacity: 0.58 });
  disposableMats.push(wetMaterial);
  const wetPlaces = [[8.7, 6.5, 0.8], [10.9, 10.6, 1.1], [7.7, 3.2, 0.7], [-8.2, 6.9, 1.2], [3.8, 8.8, 0.7], [-4.5, 9.8, 0.9], [11.8, -0.7, 1.1], [-8.7, -0.9, 0.8], [13, 7.6, 0.9], [5.2, -6.2, 0.8]];
  const puddles = new THREE.InstancedMesh(unitBox, wetMaterial, wetPlaces.length * 3);
  const tmpM = new THREE.Matrix4(), tmpP = new THREE.Vector3(), tmpQ = new THREE.Quaternion(), tmpS = new THREE.Vector3();
  wetPlaces.forEach(([x, z, s], i) => {
    for (let j = 0; j < 3; j++) {
      tmpP.set(x + (j - 1) * 0.23 * s, groundY + (pathDistance(x, z) < 1.1 ? 0.064 : 0.015), z + (j % 2) * 0.17);
      tmpS.set((j === 1 ? 1.25 : 0.75) * s, 0.01, 0.42 * s);
      puddles.setMatrixAt(i * 3 + j, tmpM.compose(tmpP, tmpQ, tmpS));
    }
  });
  puddles.computeBoundingSphere();
  root.add(puddles);
  disposableMeshes.push(puddles);

  // Far high-altitude cloud banks
  const cloudMaterial = new THREE.MeshStandardMaterial({ color: "#edf3ef", roughness: 1, transparent: true, opacity: 0.66, depthWrite: false });
  disposableMats.push(cloudMaterial);
  const cloudBanks = new THREE.InstancedMesh(unitBox, cloudMaterial, 15);
  [[-19, 9.5, -27], [-3, 12.5, -32], [19, 11.2, -25]].forEach(([x, y, z], i) => {
    for (let j = 0; j < 5; j++) {
      tmpP.set(x + (j - 2) * 1.7, y + (j % 2) * 0.55, z + (j % 3) * 0.35);
      tmpS.set(3.0, 0.58 + noise(i, j) * 0.55, 1.6);
      cloudBanks.setMatrixAt(i * 5 + j, tmpM.compose(tmpP, tmpQ, tmpS));
    }
  });
  cloudBanks.computeBoundingSphere();
  root.add(cloudBanks);
  disposableMeshes.push(cloudBanks);

  // Weather precipitation particles (rain / storm / snow)
  const particleMaterial = new THREE.MeshBasicMaterial({ color: "#d6e7f1", transparent: true, opacity: 0.6, depthWrite: false });
  disposableMats.push(particleMaterial);
  const particles = new THREE.InstancedMesh(unitBox, particleMaterial, 640);
  particles.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  particles.frustumCulled = false;
  root.add(particles);
  disposableMeshes.push(particles);

  const rainAxis = new THREE.Vector3(0, 0, 1);
  const seeds = Array.from({ length: 640 }, (_, i) => {
    let x, z, attempt = 0;
    do { x = noise(i, 11, attempt) * 40 - 19; z = noise(i, 12, attempt++) * 40 - 17; } while (inHouse(x, z, 0.8));
    return { x, z, phase: noise(i, 13), size: 0.72 + noise(i, 14) * 0.55 };
  });

  let condition = "clear", light = "day", worldStyle = "classic", elapsed = 0, particleCount = 0;

  function poseParticles(time) {
    const snowing = condition === "snow", speed = snowing ? 0.072 : condition === "storm" ? 0.94 : 0.66;
    for (let i = 0; i < particleCount; i++) {
      const seed = seeds[i], fall = (seed.phase + time * speed) % 1;
      tmpP.set(seed.x + (snowing ? Math.sin(time * 0.8 + i) * 0.18 : 0), groundY + 0.06 + (1 - fall) * 10.5, seed.z);
      if (snowing) {
        tmpS.setScalar(0.066 * seed.size);
        tmpQ.setFromAxisAngle(THREE.Object3D.DEFAULT_UP, time * 0.22 + i);
      } else {
        tmpS.set(0.017, (condition === "storm" ? 0.52 : 0.35) * seed.size, 0.017);
        tmpQ.setFromAxisAngle(rainAxis, -0.12);
      }
      particles.setMatrixAt(i, tmpM.compose(tmpP, tmpQ, tmpS));
    }
    particles.instanceMatrix.needsUpdate = true;
  }

  function applyWorldVisuals() {
    const activeTheme = themeGroups[worldStyle] || themeGroups.classic;
    for (const [id, item] of Object.entries(themeGroups)) {
      item.group.visible = id === worldStyle;
    }
    const snowing = condition === "snow", wet = ["rain", "storm"].includes(condition), night = light === "night";
    const cloudy = ["cloudy", "rain", "storm", "snow", "mist"].includes(condition);
    const isOcean = worldStyle === "coastal";

    lawnMaterial.map = snowing && !isOcean ? textures.snow : textures[activeTheme.textureKey];
    lawnMaterial.needsUpdate = true;
    const baseLawnTint = snowing && !isOcean ? "#ffffff"
      : wet ? (isOcean ? "#6e9aa8" : "#a2b9bc")
      : condition === "mist" ? "#d7e2db"
      : night && isOcean ? "#436d82"
      : night && worldStyle === "french" ? "#6c7f99"
      : night && worldStyle === "loft" ? "#4e5663"
      : light === "dusk" && isOcean ? "#d8b4a6"
      : activeTheme.lawnTint;
    lawnMaterial.color.set(baseLawnTint);
    lawnMaterial.roughness = isOcean ? 0.28 : wet ? 0.62 : 0.96;
    lawnMaterial.metalness = isOcean ? 0.16 : 0;

    activeTheme.material.color.set(wet ? "#b5c5ca" : "#ffffff");
    activeTheme.material.roughness = wet ? Math.max(0.55, activeTheme.roughness - 0.25) : activeTheme.roughness;
    activeTheme.snowCaps.visible = snowing;
    if (activeTheme.glowMesh) {
      activeTheme.glowMesh.visible = true;
      activeTheme.glowMat.color.set(night ? "#ffffff" : light === "dusk" ? "#fff2d8" : "#d8d0be");
    }

    puddles.visible = wet && !isOcean;
    cloudBanks.visible = cloudy || worldStyle === "french";
    cloudMaterial.color.set(night ? "#748799" : condition === "storm" ? "#8c9da7" : condition === "rain" ? "#becdd2" : light === "dusk" ? "#f2cfc0" : "#e9f0f0");
    cloudMaterial.opacity = condition === "mist" ? 0.35 : 0.68;

    particleCount = snowing ? 240 : condition === "storm" ? 640 : wet ? 430 : 0;
    particles.visible = particleCount > 0;
    particles.count = particleCount;
    particleMaterial.color.set(snowing ? "#ffffff" : night ? "#b1c5df" : "#d6e7f1");
    particleMaterial.opacity = snowing ? 0.9 : condition === "storm" ? 0.63 : 0.5;

    flyMaterial.color.set(activeTheme.ambientParticleColor);
    const calm = !["rain", "storm", "snow"].includes(condition);
    fireflies.visible = calm && (light === "night" || light === "dusk" || activeTheme.ambientAlwaysOn);
    activeTheme.animated.forEach(a => a.update(elapsed, light));
    poseParticles(elapsed);
  }

  function setWorld(nextStyle) {
    const resolved = Object.hasOwn(themeGroups, nextStyle) ? nextStyle : "classic";
    if (resolved === worldStyle && themeGroups[resolved].group.visible) return;
    worldStyle = resolved;
    applyWorldVisuals();
  }

  function setWeather(nextCondition, nextLight) {
    condition = nextCondition;
    light = nextLight;
    applyWorldVisuals();
  }

  setWorld("classic");
  setWeather("clear", "day");

  return {
    setWorld,
    setWeather,
    tick(time) {
      const activeTheme = themeGroups[worldStyle] || themeGroups.classic;
      const hasAnimated = activeTheme.animated.length > 0;
      if (!particleCount && !fireflies.visible && !hasAnimated) return false;
      elapsed = time;
      if (particleCount) poseParticles(time);
      if (fireflies.visible) poseFireflies(time);
      if (hasAnimated) activeTheme.animated.forEach(a => a.update(time, light));
      return true;
    },
    getState() {
      const activeTheme = themeGroups[worldStyle] || themeGroups.classic;
      return {
        worldStyle,
        condition,
        light,
        particles: particleCount,
        elapsed,
        snowCover: activeTheme.snowCaps.visible,
        wetGround: puddles.visible,
        clouds: cloudBanks.visible,
        fireflies: fireflies.visible,
      };
    },
    dispose() {
      root.removeFromParent();
      disposableMeshes.forEach(m => m.dispose());
      disposableGeos.forEach(g => g.dispose());
      disposableMats.forEach(m => m.dispose());
      Object.values(textures).forEach(t => t.dispose());
      flyTexture.dispose();
    },
  };
}
