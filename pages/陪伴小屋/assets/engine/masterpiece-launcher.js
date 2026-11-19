import * as THREE from "../../vendor/three.module.min.js";
import { RoomCore } from "./room-core.js";

/**
 * Masterpiece Launcher: 全新渲染入口
 */
async function launchMasterpiece() {
    console.log("Launching Masterpiece Cottage...");

    const context = {
        scene: new THREE.Scene(),
        camera: new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 1000),
        bridge: {}
    };
    
    // 设置相机位置以鸟瞰整个小屋
    context.camera.position.set(10, 10, 10);
    context.camera.lookAt(0, 0, 0);

    const core = new RoomCore(context);
    await core.init();

    // 加载大师作配置文件
    const [layout, atmosphere] = await Promise.all([
        fetch('config/masterpiece-layout.json').then(r => r.json()),
        fetch('config/warm-atmosphere.json').then(r => r.json())
    ]);
    
    // 应用新架构设计
    core.applyDesign(layout, atmosphere);
    core.mount();
    
    console.log("Masterpiece Cottage launched successfully.");
    
    // 这里可以添加简单的渲染循环以验证
    function render() {
        requestAnimationFrame(render);
        // context.renderer.render(context.scene, context.camera); // 假设 renderer 已在 context 中
    }
    // render();
}

launchMasterpiece().catch(console.error);
