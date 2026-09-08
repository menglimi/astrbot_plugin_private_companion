# 可移植 Canonical Fixture 与 SDK 边界 v0

> 状态：2026-09-07 验收与执行层设计草案。整体入口见[设计总纲](./FRAMEWORK_DESIGN.md)，职责与依赖见[主题目录](./FRAMEWORK_DESIGN_INDEX.md)。本文定义录制回放载体，不表示 SDK 或回放工具已经实现。

本文承接[平台可移植设计](./PLATFORM_PORTABILITY_AND_BUNDLING_V0.md)，复用[注入协议](./COMPANION_INJECTION_PROTOCOL.md)、[记忆接口](./MEMORY_PROPOSAL_QUERY_CONTRACT_V0.md)和[扩展生命周期](./COMPANION_EXTENSION_LIFECYCLE_V0.md)的 DTO、能力句柄、授权与结果语义。

公共身份与记忆已有[机器契约包 0.2.0](./contracts/v1/README.md)，新增版本化订阅恢复夹具，包含引用投影初始状态、控制 DTO、预期及 SUB 场景映射，可供本稿后续录制记录引用。该包只验证格式、指纹、引用和旧命名空间示例，尚无平台原生录制或双宿主运行结果，不是完整跨平台回放包。

公共执行另有[契约包 0.1.0 review](./contracts/execution/v1/README.md)，提供 Task/Session/Action/结果/投递回执的 6 个公共 Schema、110 个格式案例及创作分享、共处恢复两条设计夹具；只检查静态关联，不执行 workflow.steps。公共控制方法的字段设计见[生命周期 §4](./COMPANION_EXTENSION_LIFECYCLE_V0.md#4-控制面操作语义)，拟议 control profile 尚无 Schema 或夹具，不能把 execution 示例中的 SessionRecord 当作 resume 命令回放。

## 1. 目标与边界

验证分为两层：

1. 各平台的脱敏原生输入，经各自 adapter 转换，比较规范消息、身份、会话和证据。这样可以发现适配器误归因，不能只给所有宿主注入同一个已转换对象就声称平台适配完成。
2. 同一规范 DTO 经 AstrBot、独立服务和嵌入式 shell 调用领域服务，比较 owner、权限、事实 revision、证据、幂等和结果。领域代码不导入 AstrBot、Quart 或平台 SDK。

固定时钟、策略、初始数据、能力配置和录制的模型/外部结果后，才能验证确定性；真实模型效果使用独立的语义评估。能力不同的 shell 分别匹配声明的预期降级分支。

Fixture 是经过授权和脱敏的验收载体，不是生产事件日志、另一套公共请求协议或调用权限来源。生产日志、凭据和事实由各自 owner 持有。

## 2. 录制记录与公共 DTO

一条录制记录只增加回放关联信息，`dto` 内复用对应公共 schema。原先的 Canonical Envelope 统一解释为 fixture 记录，不要求所有生产调用改用它；事件、请求、查询结果和投递回执仍是不同类型。

下面展示一条查询记录的结构。示例版本是设计目标，完整 schema 仍待定型：

```json
{
  "record_version": "fixture.record.v0.1",
  "record_id": "record-001",
  "trace_id": "trace-001",
  "sequence": 1,
  "kind": "capability.request",
  "occurred_at": "2026-09-07T12:00:00Z",
  "dto_schema": "memory.query.v1",
  "dto": {
    "schema_version": "memory.query.v1",
    "capability_id": "memory.query",
    "capability_version": "1.0",
    "provider_id": "example.memory",
    "provider_generation": "generation-7",
    "request_id": "req-001",
    "trace_id": "trace-001",
    "deadline_at": "2026-09-07T12:00:03Z",
    "scope": {
      "ecosystem_id": "ecosystem-001",
      "installation_id": "installation-001",
      "runtime_instance_id": "runtime-recorded",
      "host_kind": "astrbot",
      "bot_id": "bot-main",
      "platform": "aiocqhttp",
      "account_id": "qq-bot-001",
      "conversation_ref": "conversation-001",
      "platform_conversation_id": "private-user-123",
      "conversation_id": "private-user-123",
      "session_id": null,
      "user_id": "user-123",
      "group_id": null,
      "persona_id": "persona-main",
      "persona_binding_revision": 12
    },
    "required_features": [],
    "budget": {"max_input_bytes": 16384, "max_model_calls": 0},
    "payload": {
      "namespace": "example.reading",
      "visibility": "private",
      "purpose": "reply",
      "query_intent": "我们上次一起读到哪里？",
      "include_pending": false,
      "conflict_policy": "current_only",
      "evidence_budget": {"max_items": 8, "max_chars": 2400, "max_tokens": 800},
      "extensions": {}
    }
  },
  "adapter": {
    "adapter_id": "astrbot-recording",
    "external_event_ref": "source-001",
    "adapter_generation": "adapter-gen-1"
  }
}
```

`scope` 位于 DTO 内，由隔离运行时按预置身份和授权解析，不能从来源 payload 或录制值自行获得权限。回放可以显式映射 runtime/host/外部账号，但必须记录映射并保持稳定 owner 与权限语义。写请求仍保留逻辑幂等键并走标准提交边界。

`adapter` 元数据只用于诊断、去重和回放关联，不进入事实 owner。时间使用带时区的 ISO 8601；未知核心字段按对应 schema 拒绝，非必需扩展放入声明的 extensions，不能静默改写安全语义。

输出记录以 kind 区分 domain.event、operation.result、evidence.projection 和 delivery.receipt，dto_schema 指向对应契约。原生录制样本单独隔离，仅供 adapter 测试；Cookie、token、宿主绝对路径和未授权正文不进入 fixture。决策输入的最小录制样例见 `docs/contracts/decision/v1/recordings/astrbot-message-recording.json`，只保留平台类型、外部引用、内容哈希和字节数，并明确禁止 `raw_text`、宿主 event、凭据、消息句柄和数据库连接；`normalized_ref` 指向同包的 `NormalizedInteractionEvent`。

## 3. Fixture 包格式

包内至少包含版本清单、输入、初始数据、绑定/授权、能力配置、录制响应和期望投影：

```json
{
  "fixture_id": "memory-portability-001",
  "fixture_version": "0.1.0",
  "schema_versions": ["fixture.record.v0.1", "memory.query.v1", "memory.result.v1"],
  "redaction_policy": "fixture-safe-v1",
  "clock": {"mode": "fixed", "now": "2026-09-07T12:00:00Z"},
  "initial_state": "state/initial.json",
  "bindings_and_grants": "state/bindings.json",
  "policy_and_capabilities": "state/policy.json",
  "recorded_responses": "responses/recorded.json",
  "inputs": ["inputs/0001.json"],
  "expected_projection": "expected/semantic.json",
  "required_capabilities": ["memory.query"],
  "forbidden_dependencies": ["astrbot", "quart", "platform_sdk"],
  "integrity": {"algorithm": "sha256", "manifest_hash": "pending"}
}
```

这些文件名表示目标包布局，不是当前已经生成的文件。打包时清单列出各文件 hash；manifest_hash 对排除该字段后的规范化清单计算。pending 仅是设计占位，验收包必须包含真实 hash。forbidden_dependencies 只检查便携 SDK/领域层，宿主适配器使用声明依赖。

只读切片的期望投影示例：

```json
{
  "operations": [
    {
      "request_id": "req-001",
      "status": "succeeded",
      "items": [{"atom_id": "atom-001", "revision": 7, "owner_ref": "owner-001"}]
    }
  ],
  "required_evidence_refs": ["evidence-001"],
  "forbidden_effects": ["fact_write", "message_delivery"],
  "capabilities": {"memory.query": "ready"}
}
```

这是比较用投影，不是另一种 OperationResult。实际输出仍检查完整 AnswerEvidence、权限和预算。后续写入切片分别检查 OperationResult.status=succeeded、output.proposal_state=persisted、事实 revision 和 memory.changed 事件；no_op 为 skipped。不能用操作的 state=persisted 或 proposal.accepted 事件替代提交证明。

回放应支持重复与乱序、取消、超时、generation 变化和能力缺失。写入/outbox 故障在可靠记忆阶段加入，只读阶段不宣称已通过这些要求。

## 4. 最小 SDK 端口

SDK 只提供协议 DTO、端口和错误/状态枚举；实现由宿主或服务注入。SDK 不启动循环、读取环境变量、发现插件或持有数据库连接。

| 责任 | 复用接口及边界 |
| --- | --- |
| 能力发现、绑定、调用、订阅、取消 | 复用注入协议和生命周期的标准 facade、ScopeContext、能力句柄与预算 |
| 平台输入转换 | 原生对象只在平台 adapter 内可见，转换为公共消息/观察 DTO；精确输入 schema 随契约定型 |
| 投递 | 标准 ActionRequest 和 DeliveryReceipt；回放时注入录制实现 |
| 领域调用 | MemoryProposal/MemoryQuery 与标准结果封套；不传 event、request、ORM session 或 data directory |
| 控制命令与原操作对账 | register/discover/bind/release/cancel/operation.lookup/session.resume 使用独立控制语义；可信 caller 由 shell 注入，控制成功与原业务效果分开 |
| fixture 读取、记录与比较 | 属于验收工具包；可包装多种 DTO，不进入生产 SDK 的强制运行依赖 |

不新增与能力 facade 平行的 CanonicalRuntime.dispatch。Runner 解包记录后，通过已经协商的标准接口调用；记录器旁路收集结果，不拥有事实或授权。

### 4.1 角色决策影子适配器

角色决策影子适配器复用[角色决策契约包](./contracts/decision/v1/README.md)的 `ShadowAdapterBinding`、`RoleDecisionSnapshot` 和 `ShadowRun`。它的输入端只接收经过平台 adapter 归一化的规范事件和已授权上下文，输出端只产生候选比较与资源指标；`delivery_permission=none` 是绑定级不变量。

适配器生命周期沿用注册、探测、绑定、调用、撤权和卸载，不另建调度器：

```text
native event
  -> PlatformAdapter.normalize
  -> Scope/ACL/evidence validation
  -> NormalizedInteractionEvent (read-only, redacted)
  -> RoleDecisionSnapshot
  -> ShadowAdapter.invoke
  -> ShadowRun
  -> diagnostic ledger
```

适配器不得读取或保存平台私有对象、凭据、消息句柄、完整聊天正文或数据库连接。`normalize` 必须先产生 `NormalizedInteractionEvent`，由 Kernel 再校验 scope、ACL、证据、可见性、TTL 和共享模式；缺少稳定事件 ID 或用户映射时拒绝输入。`invoke` 使用父请求的 deadline、取消信号和预算；同一 `decision_id` 重放返回同一比较结果，不创建投递或领域写入。宿主重载或 generation 变化时，旧绑定立即失效，未完成影子任务只允许收束和释放资源，不能回写新代状态。

影子阶段的最小指标是：归一化失败率、旧/新候选排序变化、硬阻塞漏检数、同主题吸收率、无关候选增加量、首句延迟、模型调用、快照字节和峰值在途预览。只有这些指标在固定回放和脱敏 live 观察中满足门槛，才进入低风险行为灰度；灰度仍通过正常 `DeliveryGateway`，影子适配器本身永远不能发送。

跨 shell 的夹具重建 registration/binding/provider_generation/controller_generation，不能将录制中的运行句柄当作有效授权。task/operation/session/receipt 和稳定 owner 按显式 lineage 映射保留；新 shell 按原 owner/幂等键查账，旧平台投递不在新平台重发。LC-14--LC-22 后续需录制控制请求、控制回执与目标状态三者，区分 cancel 受理/实际停止、resume 受理/会话 active，并观察句柄、分页缓存和恢复队列的峰值；这些目前仍为 not_run。

## 5. 语义比较与降级

| 比较层 | 必须一致的语义 | 可解释的差异 |
| --- | --- | --- |
| 身份与授权 | 稳定 owner、可见范围、身份绑定的授权依据 | runtime、宿主与外部 ID 的显式映射 |
| 领域状态 | 事实 revision、墓碑、证据、幂等、纠正与撤回语义 | 仅限输入/能力差异导致的已声明预期分支 |
| 计划与执行 | 能力协商、权限、预算、取消、未知提交处理规则 | 能力不同可生成不同可执行计划与失败结果 |
| 投递 | accepted、delivered、uncertain 等状态的证据含义 | 分段、附件、线程、回执支持程度不同 |

同一能力配置下比较相同语义；能力不对等时匹配各自预期，不要求所有 action 或回执值相同。缺少线程时保留会话映射和 threading_unsupported 降级，不能隐式换 owner。只有平台受理证据时保持 accepted；提交结果未知时保持 uncertain；两者都不投影为 delivered。

决策层的首个跨宿主比较夹具见 `docs/contracts/decision/v1/cases/cross-host-comparison.json`。它固定同一 `user_shared` 事件在 AstrBot、独立服务和嵌入式 shell 中的稳定 owner、证据和内容哈希，同时要求各自保留独立 session；`scripts/replay_cross_host_comparison.py` 只比较投影，不调用平台 SDK 或发送消息。

能力降级和资源预算夹具见 `docs/contracts/decision/v1/cases/capability-degradation-budget.json`。它要求 shell 显式声明缺失 feature 和降级结果，并在共同的最大模型调用、候选数、快照字节、在途预览和总时长内运行；`scripts/replay_capability_degradation.py` 检查降级后的工作量单调不增加，所有副作用仍为零。

## 6. Conformance Runner

每个 shell 使用隔离 runtime_instance_id 和初始状态；运行期间记录映射、能力协商、输出及资源账本：

| 检查 | 失败含义 |
| --- | --- |
| schema / 枚举 | DTO 不可解析或未知状态被吞掉 |
| import boundary | 便携层依赖宿主模块 |
| adapter normalization | 原生输入的身份、证据或消息含义转换错误 |
| semantic projection | owner、权限、revision、证据或状态偏离预期 |
| replay determinism | 固定依赖结果后仍有无法解释的语义差异 |
| fence / cancel | 写入阶段的旧代或撤权任务仍能提交 |
| resource ledger | 任务、连接或句柄未收束 |
| redaction | fixture 或报告泄漏凭据、原始私密 payload 或绝对路径 |

报告保存 runner 版本、fixture hash、shell/adapter 版本、能力协商结果和失败 record_id，复用[一致性验收报告](./MEMORY_COUNTEREXAMPLE_EVAL_V0.md)的已执行、跳过和未覆盖分类。使用录制或替身时标记 recorded / isolated；真实平台验证才标记 live。

## 7. 实施顺序

本节细化[总纲](./FRAMEWORK_DESIGN.md)的契约、只读验证和可靠记忆阶段：

1. 定型 RuntimeScope、公共平台输入、记忆请求/结果的 JSON Schema，再生成引用这些 schema 的 fixture。
2. 在现有 AstrBot 边界验证只读 normalize/record adapter，并用同一规范事件在独立 facade 与嵌入式 shell 中比较身份、可见性和资源投影。
3. 先通过 schema、身份、权限、查询和降级检查；再加入操作账本、提交 fence、outbox 和热重载故障。
4. 对应写入闭环通过后评估按 owner 迁移和 standalone/embedded 装配；SDK 独立发行与生产数据切换分别决策。

没有通过对应闭环就不切换生产 owner；只读回放成功或两个平台都能启动不代表整套平台迁移完成。
