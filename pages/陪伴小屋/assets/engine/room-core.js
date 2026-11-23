import { StageEngine } from "./stage-engine.js";
import { StateCoordinator } from "./state-coordinator.js";
import { AssetRegistry } from "./asset-registry.js";
import { InteractionRouter } from "./interaction-router.js";
import { AtmosphereEngine } from "./atmosphere-engine.js";

/**
 * RoomCore: 小屋核心调度引擎
 * 负责各引擎模块的初始化、协调与生命周期管理。
 */
export class RoomCore {
    constructor(context) {
        this.context = context;
        this.engines = {
            stage: new StageEngine(),
            state: new StateCoordinator(),
            assets: new AssetRegistry(),
            router: new InteractionRouter(),
            atmosphere: new AtmosphereEngine()
        };
    }

    async init() {
        // 初始化所有引擎
        const initPromises = Object.values(this.engines).map(engine => 
            engine.initialize(this.context)
        );
        await Promise.all(initPromises);
        console.log("RoomCore: All engines initialized.");
    }

    mount() {
        Object.values(this.engines).forEach(engine => engine.mount());
    }

    dispose() {
        Object.values(this.engines).forEach(engine => engine.dispose());
    }

    // 快捷访问器
    get stage() { return this.engines.stage; }
    get state() { return this.engines.state; }
    get assets() { return this.engines.assets; }
    get router() { return this.engines.router; }
    get atmosphere() { return this.engines.atmosphere; }

    /**
     * 应用设计方案
     * @param {Object} layoutConfig 
     * @param {Object} atmosphereConfig 
     */
    applyDesign(layoutConfig, atmosphereConfig) {
        if (layoutConfig) {
            this.engines.stage.build(layoutConfig.stage);
            this.engines.assets.spawn(layoutConfig.entities);
        }
        if (atmosphereConfig) {
            this.engines.atmosphere.apply(atmosphereConfig);
        }
        console.log("RoomCore: Design applied.");
    }
}
