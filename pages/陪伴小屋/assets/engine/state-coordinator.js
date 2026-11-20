/**
 * StateCoordinator: 状态协调中心
 * 负责全局状态管理与组件间的事件同步。
 */
export class StateCoordinator {
    constructor() {
        this.state = {
            atmosphere: 'day',
            furnitureLayout: [],
            avatar: null
        };
        this.subscribers = new Map();
    }

    /**
     * 更新状态并通知订阅者
     * @param {Object} newState 
     */
    setState(newState) {
        this.state = { ...this.state, ...newState };
        this.notify(newState);
    }

    /**
     * 订阅状态变更
     * @param {string} key 
     * @param {Function} callback 
     */
    subscribe(key, callback) {
        if (!this.subscribers.has(key)) {
            this.subscribers.set(key, []);
        }
        this.subscribers.get(key).push(callback);
    }

    notify(changedState) {
        Object.keys(changedState).forEach(key => {
            if (this.subscribers.has(key)) {
                this.subscribers.get(key).forEach(callback => callback(this.state[key]));
            }
        });
    }

    getState() {
        return this.state;
    }
}
