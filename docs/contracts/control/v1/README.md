# 公共控制契约包

> 包修订 `0.1.0`，profile `companion.control@1`，成熟度 `review`。这是注册、发现、绑定、释放、取消、查账和会话恢复的字段契约与离线夹具，不是已发布 SDK，也不证明运行时具备原子性或真实回执。

控制语义由[生命周期设计](../../../COMPANION_EXTENSION_LIFECYCLE_V0.md)和[注入协议](../../../COMPANION_INJECTION_PROTOCOL.md)维护。本包只固定精确字段、封闭对象、依赖和设计状态；业务结果仍由对应领域 owner 的契约负责。

## 包内容

| 对象 | 用途 |
| --- | --- |
| `extension-manifest` | 注册时提交的扩展元数据、能力描述和资源上限 |
| `capability-descriptor` | 单项能力的版本、owner、权限、Schema 和资源边界 |
| `control-request` | 七种控制方法的统一请求封套与方法参数 |
| `control-result` | 控制受理、拒绝、挂起和失败的统一结果封套 |
| `extension-state-page` | 能力状态页、目录 revision 和准入状态 |

控制句柄、provider generation、descriptor revision、binding revision 和 cursor 都是有期限的运行引用。Schema 不把句柄变成授权，也不允许从备份恢复成可用句柄。`operation.lookup` 的 `not_found`/`expired` 不证明外部动作没有发生，`cancel` 的受理也不证明业务效果已经撤销。

## 离线验证

```powershell
python scripts/validate_control_contracts.py
```

验证只检查本地 JSON、Schema、方法分支、示例和夹具引用。真实授权、CAS、generation fence、响应丢失重试、资源释放、远端效果和峰值内存保持 `not_run`。

## 设计夹具

[fixture.json](./fixture.json) 覆盖三条必须保留的轨迹：bind 响应丢失后的同键重试、旧 binding release 后的拒绝、cancel 已受理但业务效果未知时转查账。所有轨迹仍是 `not_run`，不能被静态通过结果解释为运行成功。

`lifecycle-replay.json` 与 `scripts/replay_control_lifecycle.py` 是新增的纯内存隔离回放，补充新 generation、撤权排空和资源预算。它通过 11 项设计检查，但不改变上述真实运行轨迹的 `not_run` 状态。
