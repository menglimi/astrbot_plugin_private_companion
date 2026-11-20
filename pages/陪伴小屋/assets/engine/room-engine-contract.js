/**
 * @fileoverview 小屋核心引擎接口契约
 * 定义所有小屋组件（场景、家具、角色、能力扩展）必须遵循的标准生命周期与行为规范。
 */

/**
 * @typedef {Object} RoomContext
 * @property {THREE.Scene} scene - 主场景
 * @property {THREE.Camera} camera - 主相机
 * @property {Object} bridge - 插件通讯桥接
 */

/**
 * @typedef {Object} GlobalState
 * @property {string} atmosphere - 当前氛围
 * @property {Array} furnitureLayout - 当前家具布局
 * @property {Object} avatar - 当前角色状态
 */

/**
 * @interface IRoomComponent
 * @description 所有小屋扩展模块必须实现的生命周期接口
 */
export class IRoomComponent {
  constructor(id) {
    this.id = id;
  }

  /**
   * 初始化组件（用于加载资源、注册事件）
   * @param {RoomContext} context 
   */
  async initialize(context) { throw new Error("Not implemented"); }

  /**
   * 组件挂载到场景（用于添加到渲染树）
   */
  mount() { throw new Error("Not implemented"); }

  /**
   * 状态更新响应
   * @param {GlobalState} state 
   */
  update(state) { /* Optional */ }

  /**
   * 销毁组件（释放内存）
   */
  dispose() { throw new Error("Not implemented"); }
}
