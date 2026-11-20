import * as THREE from "../../vendor/three.module.min.js";
import { IRoomComponent } from "./room-engine-contract.js";

/**
 * AssetRegistry: 资产管理与高性能实体渲染引擎
 * 负责家具与角色的加载、实例化与生命周期管理。
 */
export class AssetRegistry extends IRoomComponent {
    constructor() {
        super("asset-registry");
        this.entities = new Map(); // EntityID -> { mesh, definition }
        this.scene = null;
        // 批处理容器，用于高性能渲染（如体素堆砌）
        this.instances = new Map(); // DefinitionID -> InstancedMesh
    }

    async initialize(context) {
        this.scene = context.scene;
    }

    /**
     * 注册并生成一个实体
     * @param {Object} entityDef 实体定义 (JSON)
     * @param {THREE.Object3D} [parent=null] 父级容器
     */
    async spawn(entityDef, parent = null) {
        const target = parent || this.scene;
        if (Array.isArray(entityDef)) {
            for (const item of entityDef) {
                await this.spawnComponent(item, target);
            }
        } else {
            await this.spawnComponent(entityDef, target);
        }
    }

    async spawnComponent(component, parent) {
        if (component.type === "group") {
            const group = new THREE.Group();
            if (component.position) {
                group.position.set(component.position.x || 0, component.position.y || 0, component.position.z || 0);
            }
            parent.add(group);
            if (component.children) {
                for (const child of component.children) {
                    await this.spawnComponent(child, group);
                }
            }
            this.entities.set(component.id || Math.random().toString(), { mesh: group });
        } else if (component.type === "block") {
            this.spawnBlock(component.params, parent);
        } else if (component.type === "block-array") {
            this.spawnBlockArray(component.params, parent);
        }
    }

    spawnBlock(params, parent) {
        const geometry = new THREE.BoxGeometry(params.w, params.h, params.d);
        const material = new THREE.MeshStandardMaterial({ color: params.tint || 0x888888 });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.set(params.x || 0, params.y || 0, params.z || 0);
        parent.add(mesh);
        this.entities.set(params.id || Math.random().toString(), { mesh });
    }

    spawnBlockArray(params, parent) {
        const { base, items, dims, tint } = params;
        items.forEach(item => {
            const x = item.x !== undefined ? item.x : (base.x || 0);
            const y = item.y !== undefined ? item.y : (base.y || 0);
            const z = item.z !== undefined ? item.z : (base.z || 0);
            this.spawnBlock({
                x, y, z,
                w: dims.w, h: dims.h, d: dims.d,
                tint: tint
            }, parent);
        });
    }

    mount() {
        this.entities.forEach(entity => entity.mesh.visible = true);
    }

    dispose() {
        this.entities.forEach(entity => {
            this.scene.remove(entity.mesh);
            entity.mesh.geometry.dispose();
            entity.mesh.material.dispose();
        });
        this.entities.clear();
    }
}
