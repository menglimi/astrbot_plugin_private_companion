import * as THREE from "../vendor/three.module.min.js";

export class AssetLoader {
    constructor(scene, bridge) {
        this.scene = scene;
        this.bridge = bridge;
        this.manifest = [];
    }

    async load() {
        try {
            const resp = await fetch('assets/furniture_manifest.json');
            this.manifest = await resp.json();
            
            for (const item of this.manifest) {
                if (this.validate(item)) {
                    await this.spawnFurniture(item);
                }
            }
            console.log("AssetLoader: Furniture loaded.", this.manifest);
        } catch (e) {
            console.error("AssetLoader: Failed to load manifest", e);
        }
    }

    validate(item) {
        if (!item.model_path || !item.id) {
            console.warn(`AssetLoader: Skipping invalid asset ${item.id}`);
            return false;
        }
        return true;
    }

    async spawnFurniture(item) {
        // 暂时使用占位立方体，确保基本架构运作正常
        // 后续我们将在此处接入真实的模型加载逻辑
        const geometry = new THREE.BoxGeometry(1, 1, 1);
        const material = new THREE.MeshStandardMaterial({ 
            color: 0x888888,
            roughness: 0.5, 
            metalness: 0.2 
        });
        const mesh = new THREE.Mesh(geometry, material);
        
        // 放置逻辑
        mesh.position.set(item.position?.x || 0, item.position?.y || 0, item.position?.z || 0);
        
        this.scene.add(mesh);
        console.log(`AssetLoader: Spawned ${item.id} at`, mesh.position);
    }
}
