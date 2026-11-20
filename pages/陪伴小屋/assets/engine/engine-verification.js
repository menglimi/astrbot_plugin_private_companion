import * as THREE from "../../vendor/three.module.min.js";
import { RoomCore } from "./room-core.js";

/**
 * 验证脚本：模拟初始化 RoomCore 并加载配置
 */
async function verifyEngine() {
    console.log("Starting engine verification...");

    // 模拟上下文
    const context = {
        scene: new THREE.Scene(),
        camera: new THREE.PerspectiveCamera(),
        bridge: {}
    };

    const core = new RoomCore(context);
    await core.init();

    // 加载设计配置
    const [layout, atmosphere] = await Promise.all([
        fetch('config/living-flow-layout.json').then(r => r.json()),
        fetch('config/warm-atmosphere.json').then(r => r.json())
    ]);
    
    // 应用新设计方案
    core.applyDesign(layout, atmosphere);
    console.log("Living Flow design applied.");

    // 测试交互路由
    core.router.registerHandler("desk-top", () => console.log("Desk clicked!"));
    core.router.route("desk-top");

    console.log("Verification completed successfully.");
}

verifyEngine().catch(console.error);
