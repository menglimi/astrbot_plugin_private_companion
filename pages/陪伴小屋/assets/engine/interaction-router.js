import { IRoomComponent } from "./room-engine-contract.js";

/**
 * InteractionRouter: 交互路由引擎
 * 负责捕获场景交互事件并将其路由至已注册的扩展行为处理函数，实现交互逻辑与渲染逻辑的解耦。
 */
export class InteractionRouter extends IRoomComponent {
    constructor() {
        super("interaction-router");
        this.handlers = new Map(); // EntityID -> Handler Function
    }

    async initialize(context) {
        this.scene = context.scene;
        // 示例：监听场景点击事件
        // 在实际应用中，这里需要配合 Raycaster 进行射线检测
    }

    /**
     * 注册家具交互处理程序
     * @param {string} entityId 
     * @param {Function} handler 
     */
    registerHandler(entityId, handler) {
        this.handlers.set(entityId, handler);
    }

    /**
     * 路由交互事件
     * @param {string} entityId 
     */
    route(entityId) {
        const handler = this.handlers.get(entityId);
        if (handler) {
            handler();
        } else {
            console.warn(`InteractionRouter: No handler registered for entity ${entityId}`);
        }
    }

    mount() {
        // 绑定必要的交互监听器
    }

    dispose() {
        this.handlers.clear();
    }
}
