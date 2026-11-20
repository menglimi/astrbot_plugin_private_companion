import * as THREE from "../../vendor/three.module.min.js";
import { IRoomComponent } from "./room-engine-contract.js";

/**
 * StageEngine: 负责小屋基础结构（地板、墙体）的渲染与构建
 */
export class StageEngine extends IRoomComponent {
    constructor(id = "stage") {
        super(id);
        this.group = new THREE.Group();
        this.layout = null;
    }

    /**
     * @param {RoomContext} context
     */
    async initialize(context) {
        this.scene = context.scene;
    }

    /**
     * 根据配置文件构建场景
     * @param {Object} layoutConfig 
     */
    build(layoutConfig) {
        this.layout = layoutConfig;
        this.clear();
        
        // 示例：构建地板
        if (this.layout.floor) {
            this.buildFloor(this.layout.floor);
        }
        
        // 示例：构建墙体
        if (this.layout.walls) {
            this.layout.walls.forEach(wall => this.buildWall(wall));
        }

        this.scene.add(this.group);
    }

    buildFloor(config) {
        // 使用 BufferGeometry 进行高性能构建
        const geometry = new THREE.BoxGeometry(config.width, config.height, config.depth);
        const material = new THREE.MeshStandardMaterial({ color: config.color });
        const floor = new THREE.Mesh(geometry, material);
        floor.position.set(config.x, config.y, config.z);
        this.group.add(floor);
    }

    buildWall(config) {
        const geometry = new THREE.BoxGeometry(config.width, config.height, config.depth);
        const material = new THREE.MeshStandardMaterial({ color: config.color });
        const wall = new THREE.Mesh(geometry, material);
        wall.position.set(config.x, config.y, config.z);
        this.group.add(wall);
    }

    clear() {
        while(this.group.children.length > 0) {
            this.group.remove(this.group.children[0]);
        }
    }

    mount() {
        this.group.visible = true;
    }

    dispose() {
        this.group.clear();
        this.scene.remove(this.group);
    }
}
