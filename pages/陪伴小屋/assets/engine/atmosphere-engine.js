import * as THREE from "../../vendor/three.module.min.js";
import { IRoomComponent } from "./room-engine-contract.js";

/**
 * AtmosphereEngine: 环境氛围渲染引擎
 * 负责场景光影、雾气与氛围参数的统一管理与平滑过渡。
 */
export class AtmosphereEngine extends IRoomComponent {
    constructor() {
        super("atmosphere");
        this.scene = null;
        this.currentSettings = null;
        this.lights = {
            ambient: new THREE.AmbientLight(),
            sun: new THREE.DirectionalLight(),
            point: new THREE.PointLight()
        };
    }

    async initialize(context) {
        this.scene = context.scene;
        this.scene.add(this.lights.ambient);
        this.scene.add(this.lights.sun);
        this.scene.add(this.lights.point);
    }

    /**
     * 应用氛围配置
     * @param {Object} settings 
     */
    apply(settings) {
        this.currentSettings = settings;
        
        // 更新光照
        this.lights.ambient.intensity = settings.ambient.intensity;
        this.lights.ambient.color.set(settings.ambient.color);
        
        this.lights.sun.intensity = settings.sun.intensity;
        this.lights.sun.color.set(settings.sun.color);
        this.lights.sun.position.set(settings.sun.position.x, settings.sun.position.y, settings.sun.position.z);
        
        // 更新点光源（示例）
        if (settings.pointLights && settings.pointLights.length > 0) {
            const p = settings.pointLights[0];
            this.lights.point.intensity = p.intensity;
            this.lights.point.color.set(p.color);
            this.lights.point.position.set(p.position.x, p.position.y, p.position.z);
        }

        // 更新雾气
        if (settings.fog) {
            this.scene.fog = new THREE.Fog(settings.fog.color, settings.fog.near, settings.fog.far);
            this.scene.background = new THREE.Color(settings.fog.color);
        }
    }

    mount() {
        this.lights.ambient.visible = true;
        this.lights.sun.visible = true;
    }

    dispose() {
        this.scene.remove(this.lights.ambient);
        this.scene.remove(this.lights.sun);
        this.scene.remove(this.lights.point);
    }
}
