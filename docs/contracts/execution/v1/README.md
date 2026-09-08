# 公共执行契约包

> 2026-09-08，包修订 `0.1.0`，profile `companion.execution@1`，成熟度 `review`。返回[设计总纲](../../../FRAMEWORK_DESIGN.md) / [主题目录](../../../FRAMEWORK_DESIGN_INDEX.md)。这是接口设计与离线验收资产，不是已发布 SDK。

语义由[公共契约](../../../COMPANION_CONTRACTS_AND_VERTICAL_SLICE.md#公共执行-010-wire-决策)、[注入协议](../../../COMPANION_INJECTION_PROTOCOL.md#66-actionrequest--actionresult)和[生命周期](../../../COMPANION_EXTENSION_LIFECYCLE_V0.md)维护。这里定位精确字段、依赖与夹具，不新增一份平行业务设计。

## 1. 文件与协商

| wire 标识 / 对象 | Schema | 用途 |
| --- | --- | --- |
| 执行公共词汇 | [execution-common](./schemas/execution-common.schema.json) | 引用、关联、绑定、等待、重试依据、效果与投递证据 |
| `companion.action.v1` | [action-request](./schemas/action-request.schema.json) | 经绑定的能力调用；公共封套与独立领域 payload |
| `companion.operation-result.v1` | [operation-result](./schemas/operation-result.schema.json) | 当前调用观察、效果状态和领域结果；不表示消息送达 |
| `companion.task.v1` | [task-envelope](./schemas/task-envelope.schema.json) | owner 输出的持久任务记录；不是任务创建或修改命令 |
| `companion.session.v1` | [session-record](./schemas/session-record.schema.json) | owner 输出的会话快照；不是加入、恢复或授权命令 |
| `companion.delivery-receipt.v1` | [delivery-receipt](./schemas/delivery-receipt.schema.json) | 单个 part 或整体的投递证据；独立于任务成功 |

采用 JSON Schema Draft 2020-12，根对象和协议对象封闭。Action 的 required_features 必含 `companion.execution@1`，绑定仍须实际协商 provider、能力版本及输入/输出 schema；填写字段不使能力就绪。领域可以增加 namespaced kind、引用种类和 capability ID，未知必需语义按协商拒绝。

引用两份现有共享 Schema：[common](../../v1/schemas/common.schema.json) 和 [runtime-scope](../../v1/schemas/runtime-scope.schema.json)。它们位于较早的记忆契约目录，内容是公共词汇与身份，依赖由[manifest](./manifest.json)固定路径、URN 和 SHA-256；不依赖 Memory Service、Writer 或记忆 DTO。后续 SDK 装配应一并携带这两份共享资产，不复制定义或依靠联网加载。

公共执行包独立为 0.1.0 review，原记忆包保持 0.2.0 review。已有封闭 memory.*.v1 请求继续通过本地 TaskContext 关联，不能直接改用本包封套。公共控制的字段语义已补入[注入协议 §4](../../../COMPANION_INJECTION_PROTOCOL.md#41-capabilitydescriptor-字段设计)与[生命周期 §4](../../../COMPANION_EXTENSION_LIFECYCLE_V0.md#4-控制面操作语义)；独立的 [control profile 0.1.0](../../control/v1/README.md) 已形成 5 个 Schema、12 个格式案例和 3 个设计夹具。本包仍不包含控制命令，也不声称已实现 register、cancel、resume 等方法。

## 2. 格式范围

公共字段的空值、互斥、状态约束见 Schema，具体解释见公共契约。Action 用 execution 对象关联 task、attempt、part、已知 operation、预算及因果引用；operation_id 在 owner 签发前为 null。请求 idempotency_key 可以为 null 以支持只读调用，运行时必须结合可信 descriptor 阻止有副作用操作省略幂等键。

payload_schema/output_schema 是已登记领域 Schema 的定位值。公共 Schema 只检查封套及 object 形状，提供方必须继续校验领域 payload、当前 ACL、目标版本和预算。校验器仅从本地登记资源解析夹具 payload，不下载请求自报的 URL。

Task 将 provider 字段归入可空 binding，将等待条件与重试依据独立建模；Session 的参与者、授权集合、上下文和进度按引用保存。OperationResult 使用 effect_state 区分 not_applicable/not_applied/applied/partial/unknown；applied 必须有 operation_id 和回执引用，pending 必须可定位原操作。具体副作用由能力成功条件解释，不能把 applied 当作用户收到消息。

DeliveryReceipt 区分 part/aggregate：单 part 不可返回 partial；aggregate 使用 parts_ref 指向完整且可分页的部分回执集合。delivered 必须有送达时间及 delivery_confirmed 证据，accepted 不能填写 delivered_at。Schema 无法证明平台证据真实、parts 完整或迟到回执没有回退，这些仍属运行边界。

资源字段使用有限调用 budget 与共享 budget_ref，不在记录中复制完整额度或媒体。数组和字符串的协商字节上限、对象分配前准入、父子额度、引用页大小和回收都需运行时计量；Schema 格式通过不表示峰值内存受控。

## 3. 跨插件夹具

[cases.json](./cases.json)包含 110 个公共格式案例，其中 9 个继续校验领域 payload，另有 5 个严格 JSON 案例。shape-only 案例中的旧 generation、伪造 operation/proof、空写入幂等键和身份错配可以满足结构；它们的运行时拒绝由 LC 场景单独要求。

[cross-plugin.fixture.json](./fixtures/cross-plugin.fixture.json)引用 17 份完整 DTO 和 3 对请求/结果，包含两条设计轨迹：

- 创作产物分享：Content 提交章节版本，使用已有授权封面引用分享；文字受理、封面结果未知时保留逐 part 回执，不重新生成或再次付费。生图自身的请求和完整业务流程不在本夹具中验证。
- 共处恢复：读取旧 session checkpoint，新控制者重验授权后恢复同一 session，结束时保留真实版本并只释放本会话资源。

[领域样例 Schema](./fixtures/schemas/domain.schema.json)只约束本夹具的输入/输出，`example.delivery.send`、`example.together.resume` 等是夹具声明，不表示这些能力已安装或已定型。LC-09--LC-13 的静态案例与运行断言已关联，全部运行结果为 not_run；两个 host profile 尚无原生录制或实际回放。

## 4. 离线验证

沿用[验证依赖](../../requirements-validation.txt)，在陪伴核心仓库运行：

```powershell
python scripts/validate_execution_contracts.py
```

验证器只复用既有离线 JSON/日期/指纹辅助函数，不导入 AstrBot、Quart 或插件运行代码。检查 6 个公共 Schema、2 个锁定共享依赖、1 个夹具领域 Schema、案例、文件指纹、本地引用及静态请求/结果关联；不执行 workflow.steps。

运行前、提交前和返回前授权，descriptor 与幂等键约束，task/session revision，取消 barrier，远端效果、投递完整性及内存峰值均保持 not_run。静态 fixture 对齐不等于真实派发关联校验已实现。

`python scripts/validate_execution_contracts.py --print-manifest` 只输出清单供评审；普通验证不会覆盖指纹。新增字段、依赖或样例要评审后登记 manifest，不能让修改自动变成通过。
