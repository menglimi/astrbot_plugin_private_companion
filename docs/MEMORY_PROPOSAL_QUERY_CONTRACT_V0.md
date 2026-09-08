# 记忆提议、查询与外部注入契约 v0

> 导航：[设计总纲](./FRAMEWORK_DESIGN.md) / [主题目录](./FRAMEWORK_DESIGN_INDEX.md)。定位：记忆外部规范草案；维护标准字段与扩展协商，依赖公共注入协议。

> 状态：2026-09-08 修订的设计草案，尚未冻结或发布 SDK。`memory.*.v1` 是拟议 schema 标识，示例不表示当前 Python API 已支持。

现有[公共身份与记忆契约包](./contracts/v1/README.md)已提供 Draft 2020-12 Schema、文件指纹和完整请求/结果样例，包修订为 0.2.0、成熟度 review，新增订阅控制、批次和 checkpoint 格式。精确字段与互斥条件由该包维护，本文维护语义；格式校验不等于授权或运行验收。

设计目标是让新的记忆来源、调用方、类型和检索策略通过公开能力接入，同时保持调用成本、数据归属和用户纠正体验可预测。旧框架只提供兼容映射和验证依据。

本文作为记忆外部接口的字段规范，复用 [Companion Injection Protocol](./COMPANION_INJECTION_PROTOCOL.md) 的能力描述、RuntimeScope、权限、版本与结果语义。[记忆契约](./MEMORY_CONTRACT_V0.md) 维护领域事实与生命周期；[状态机](./MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md) 和 [评估集](./MEMORY_COUNTEREXAMPLE_EVAL_V0.md) 验证组合行为。更新契约时同步更新这些消费者。

插件级注册与卸载见[接入生命周期规范](./COMPANION_EXTENSION_LIFECYCLE_V0.md)，现有记忆入口的适配边界见[Memory 参考适配器](../../astrbot_plugin_remember_you/docs/MEMORY_ADAPTER_DESIGN_V0.md)。三者分别约束接口字段、实例生命周期和领域实现映射，不互相替代。

## 1. 外部注入的参与方式

| 参与方 | 标准接入 | 所有权和权限 |
| --- | --- | --- |
| 记忆来源插件 | 提交 `MemoryProposal`，例如共读经历、创作进度、设备观测摘要 | 保有来源事实；提交提议不等于批准记忆写入 |
| 记忆消费者 | 提交 `MemoryQuery`，消费 `AnswerEvidence` | 按用途获得有限结果，不获得库连接或任意原文访问 |
| 抽取、检索、重排、策略提供方 | 声明可选能力，由 Memory 按显式配置调用 | 返回提议、候选引用或策略建议，不能直接覆盖事实和 ACL |
| Memory Service 提供方 | 实现写入、查询、状态查询和变更事件 | 每个稳定归属只有一个被选中的权威 Writer |
| 存储或索引适配器 | 由 Memory 内部绑定；可随实现更换 | 事实事务由 Writer 管理，索引为可重建投影 |

原生和第三方插件使用同一协议。注册、安装、启用、授权、协商成功和实际可用分别记录；目录存在不能代表能力可用。管理页展示提供方绑定、版本、降级原因和有效预算。

这里的外部注入指公开的能力接口。首期复用宿主注册/调度入口，不另开一套 Web 服务。未来进程间适配复用相同 DTO，必须绑定经过认证的调用方身份；同进程插件协议本身不是任意 Python 代码的安全沙箱。

## 2. 能力声明与绑定

目标能力家族为 `memory.api`。最小能力集如下，API 方法名由后续 SDK 实现映射，业务方只依赖能力 ID 和 schema：

| capability ID | kind | 输入 payload | 输出 | 权限 / side_effect |
| --- | --- | --- | --- | --- |
| `memory.proposal.submit` | remember | `MemoryProposal` | `OperationResult<ProposalReceipt>` | `memory.propose`；correct/retract 另验 `memory.correct`/`memory.retract` / local |
| `memory.query` | remember | `MemoryQuery` | `OperationResult<RecallResult>` | `memory.read` / none |
| `memory.operation.get` | remember | `OperationLookup` | 当前操作的 `OperationResult` | `memory.operation.read`，仅原调用方或获授权管理者 / none |
| `memory.changed` | event | owner 发布 `MemoryChangeEvent` | 授权事件流，订阅由公共控制面管理 | `memory.subscribe` / none |

请求 schema 依次为 `memory.proposal.v1`、`memory.query.v1`、`memory.operation-query.v1`，三种调用结果均用 `memory.result.v1`。事件用 `memory.changed.v1`，不作为同步函数请求；消费确认和重放游标遵循公共事件生命周期。0.2.0 另登记可协商控制能力 `memory.changed.subscribe`、`memory.changed.ack`、`memory.changed.resync`，使用独立控制结果，见 §8.1；不改旧最小能力集或 memory.result.v1，不表示已有可调用入口。

提供方复用公共 manifest 的 `id/kind/version/provider/scopes/permissions/input_schema/output_schema/requires/side_effect/confirmation/resource_budget/lifecycle`。`requires` 声明能力而非插件目录名。以下为一次声明示例，数字是示例预算，实际值由安装配置和 Runtime 取交集：

```json
{
  "id": "memory.proposal.submit",
  "kind": "remember",
  "version": "1.0",
  "provider": "example.memory",
  "scopes": ["persona", "user", "group", "session"],
  "permissions": ["memory.propose"],
  "input_schema": "memory.proposal.v1",
  "output_schema": "memory.result.v1",
  "requires": [],
  "side_effect": "local",
  "confirmation": "policy_decides",
  "resource_budget": {"max_concurrency": 2, "timeout_ms": 3000},
  "lifecycle": "on_demand"
}
```

记忆专用协商信息放入独立的 `MemoryExtensionDescriptor`，由公共注册机制关联到上述 capability，避免修改其他领域 manifest 的必填字段：

| 字段 | 含义 |
| --- | --- |
| `capability_id`、`capability_version` | 关联的标准能力及精确版本 |
| `features` | 支持的可选语义及主版本，例如 `memory.temporal@1`、`example.reading.chapter@1` |
| `type_schemas` | 命名空间类型、schema 引用、版本、声明者和可选/必需属性 |
| `limits` | 单请求字节数、嵌套深度、条目数、候选数、并发数、总时限和模型预算 |
| `idempotency_retention_seconds` | 写入账本可查询和去重的最短保证窗口 |
| `event_retention_seconds` | 可恢复变更事件的游标保留窗口 |

注册时校验 schema 与命名空间归属；重复声明且结构冲突则拒绝绑定，不能由后注册者覆盖。schema 从已登记的契约包加载，请求中的引用不能触发任意 URL 下载或代码执行。描述符具有独立版本和 revision，用于缓存失效。

调用顺序：发现兼容能力 -> 解析 RuntimeScope 与授权 -> 选择显式提供方绑定和 features -> 分配预算 -> 提交请求 -> 校验结果 -> 按需订阅/查询。绑定结果包含 `provider_id`、`provider_generation`、选定版本、有效 limits 和降级原因。写入提供方切换需要交接/对账，不按注册顺序双写；只读回退也必须指向同一权威数据或可验证的新鲜投影。

## 3. 标准请求封套

以下 JSON 是完整的写入请求示例；schema 和能力版本均属于本草案，payload 不再复制 scope、trace 或 provider 字段。

```json
{
  "schema_version": "memory.proposal.v1",
  "capability_id": "memory.proposal.submit",
  "capability_version": "1.0",
  "provider_id": "example.memory",
  "provider_generation": "generation-7",
  "request_id": "req-reading-001",
  "trace_id": "trace-reading-001",
  "deadline_at": "2026-09-07T01:00:03Z",
  "scope": {
    "ecosystem_id": "ecosystem-001",
    "installation_id": "installation-001",
    "runtime_instance_id": "runtime-001",
    "host_kind": "astrbot",
    "bot_id": "bot-main",
    "platform": "aiocqhttp",
    "account_id": "qq-bot-001",
    "conversation_ref": "conversation-private-user-123",
    "platform_conversation_id": "private-user-123",
    "conversation_id": "private-user-123",
    "session_id": "reading-session-001",
    "user_id": "user-123",
    "group_id": null,
    "persona_id": "persona-main",
    "persona_binding_revision": 12
  },
  "required_features": [],
  "budget": {"max_input_bytes": 16384, "max_model_calls": 0},
  "idempotency_key": "reading-event-001:proposal-1",
  "payload": {
    "proposal_id": "proposal-reading-001",
    "namespace": "example.reading",
    "visibility": "private",
    "purpose": "example.reading.continuity",
    "operation": "add",
    "target_atom_id": null,
    "expected_revision": null,
    "subject": {"kind": "companion.person", "ref": "person-user-123"},
    "predicate": "共同读到的章节",
    "object": {"book_ref": "book-001", "chapter_ref": "chapter-03"},
    "qualifiers": {"book_ref": "book-001", "session_ref": "reading-session-001"},
    "polarity": "affirmed",
    "attribution": "user",
    "representation": "这次一起读到了第三章。",
    "evidence_refs": [{"id": "evidence-reading-001", "source_id": "reading-event-001", "source_type": "example.reading.session", "evidence_kind": "action_receipt", "observed_at": "2026-09-07T01:00:00Z", "expires_at": null}],
    "confidence": 0.9,
    "valid_from": "2026-09-07T01:00:00Z",
    "valid_to": null,
    "retention": "durable",
    "defer_until_boundary": false,
    "types": ["example.reading.chapter"],
    "extensions": {"example.reading.chapter": {"schema_version": "1.0", "payload": {"progress": 0.25}}}
  }
}
```

### 3.1 身份与稳定归属

`scope` 采用公共协议 5.1 的 RuntimeScope，由宿主解析。外部调用方通过已认证 SDK 上下文绑定身份；JSON 中的 scope、provider 和源引用仍需重验，不能由插件自报获得授权。抽取模型仅生成语义 payload，不决定调用者身份、provider_generation 或预算。旧调用只有 installation_id/conversation_id 时必须经过兼容映射；portable 入口使用 ecosystem_id、runtime_instance_id、host_kind、conversation_ref 和 platform_conversation_id。

稳定 owner_key 沿用架构基线：`installation + bot + persona + subject_ref + visibility_namespace`；`namespace` 为该归属下的业务分区。Memory 解析并返回不透明 `owner_ref`。群空间、平台身份和跨账号映射包含于已授权命名空间解析，不按昵称自动合并。session、绑定 revision 和 generation 参与校验，不进入长期实体键；`session` 保留级别另限制临时可见范围。

Memory 请求通过已协商的 `companion.sharing@1` 携带共享语义：`session` 绑定实际会话，`user` 绑定同一平台账号下的用户并可跨私聊/群聊复用，`global` 绑定显式公共分区。它与 `scope`、`visibility`、`purpose`、`retention` 分开校验，不能仅凭请求字段扩大可见范围。群聊中的第三方内容、群专属主题和未授权观察不能借 `user` 模式写入用户档案；`global` 不承载用户私密事实。上下文编排可以分别查询三层并有界组合，每次 Memory 请求指定一个 anchor，各子查询共用总预算，证据保留原始归属和授权来源。

兼容 wire 位置固定为 `payload.extensions["companion.sharing"].payload`，其中包含 `sharing_mode`、`sharing_anchor`、`sharing_policy_revision`；扩展的 `schema_version` 为 `1.0`，封套的 `required_features` 必须包含 `companion.sharing@1`。召回项使用 `output.items[i].extensions["companion.sharing"].payload`。这些字段不进入原 Memory payload 或封套顶层，不改已有 v1 Schema 指纹；旧实现能够解析扩展不等于具备共享语义，未协商该必需能力应拒绝。完整样例、静态反例和待运行场景见[共享契约包](./contracts/sharing/v1/README.md)。

记忆主体与当前发言者分开：第三方转述可以保留明确归因，不自动写入当前用户档案。`visibility`、`namespace`、`purpose` 是申请范围，不授予读取或发布权；跨人格/平台共享通过另行授权的投影。任意扩展字段不能替代这三者。

### 3.2 封套规则

- 所有调用必须带 schema、能力版本、请求/追踪标识、已解析 scope、目标 generation 和有限 deadline。运行时同时验证调用方注册状态。
- 写入必须带 `idempotency_key`；只读查询/状态查询不要求写入幂等键。`request_id` 标识一次尝试，重试可以换 request_id，逻辑写入复用幂等键。
- 缺少时间范围的可选字段用 `null`，空数组表示未指定该过滤条件；类型错误、未知核心字段和同名冲突返回 `rejected/schema_invalid`。不能把 schema 中的未知安全字段静默忽略。
- 请求 limits 只能收紧授权上限；未配置可用模型预算时不额外调用模型。传输只接收 JSON 值和已授权资源引用，不含宿主对象、文件路径、凭证、二进制媒体或可执行函数。

## 4. MemoryProposal 字段与操作

| payload 字段 | 约定 |
| --- | --- |
| `proposal_id`、`namespace`、`visibility`、`purpose` | 必填；区分来源提议、业务分区、申请可见范围和用途 |
| `operation` | `add`、`correct`、`retract`、`no_op`；未知操作必须协商新协议，不从文本猜测 |
| `target_atom_id`、`expected_revision` | correct/retract 必填；add/no_op 为 null；先校验可见性再比较版本 |
| `subject`、`predicate`、`object` | add/correct 必填；主体 kind 可扩展，predicate 为开放语义，object 为有界 JSON 值 |
| `qualifiers`、`polarity`、`attribution` | qualifiers 为可扩展限定属性；polarity 为 affirmed/negated/unknown，attribution 为 user/third_party/bot/unknown；缺失归因保持 unknown |
| `representation`、`confidence` | 简洁表达与语义置信度；confidence 为 null 或 0..1 的有限数；不授予权限，也不自动决定真实性 |
| `evidence_refs` | 有事实副作用的操作必填；通过授权来源解析 ID、主体、revision、时间、可见性和撤回状态 |
| `valid_from`、`valid_to` | 带时区时间或 null；有效区间为 `[valid_from, valid_to)`，未知时间不补造 |
| `retention` | session/short/durable/protected 保留建议；实际期限由已授权策略计算并在回执返回 |
| `defer_until_boundary` | 可选，默认 false；会话暂存不等于长期 active |
| `types`、`extensions` | 可选；按第 7 节声明领域类型和扩展属性 |

`no_op` 返回 `succeeded` 与 `proposal_state=skipped`，不创建事实；幂等账本仅保留必要元数据。`retract` 使目标失效并传播撤回，物理擦除另由记忆治理能力按保留策略处理，回执不得宣称所有副本已删除。到期由 owner 执行，外部调整有效时间使用带 revision 的 correct；不提供能任意批量过期的通用入口。

correct 产生完整的新断言 revision，并在同一事务关闭被替代版本。只有声明为单值的稳定事实键才互斥，键包含主体、具体属性和适用限定条件；家庭地址与公司地址、不同作品或不同阶段的经历不能互相覆盖。未知类型默认不自动合并、替换已有事实，也不自动调用专用策略；可保存通用断言并注明未解释的扩展。

模型的归因、长期价值、歧义和冲突解释优先通过提示词与已有语义结果处理；低置信度可进入 pending，由带版本的策略决定后续处理，不把 `0.55` 等阈值冻结为协议规则。模型输出非法评分时可由提议适配层按声明策略纠正并记录，标准接口收到不合法数字、时间或结构则拒绝。quarantined 仅是提议审查状态，不是可召回事实状态。

证据引用的 expires_at 表示该观察作为当前依据的时效，不直接删除有历史用途的证据。来源正文过期、证据缺失、用户撤回分开表达；可长期保留经授权的必要片段，不能由引用本身伪造证据。记忆变更及其 outbox 在 owner 本地事务提交，索引和下游通过 revision 收敛。

## 5. MemoryQuery 与 AnswerEvidence

查询使用 `schema_version=memory.query.v1`、`capability_id=memory.query` 和同一封套，下面仅展示 payload。首期单个查询指定一个 namespace；多域查询由 Memory 在同一权限和总预算下组合。

```json
{
  "namespace": "example.reading",
  "visibility": "private",
  "purpose": "reply",
  "query_intent": "我们上次一起读到哪里？",
  "subjects": [{"kind": "companion.person", "ref": "person-user-123"}],
  "types": ["example.reading.chapter"],
  "time_range": null,
  "at_time": "2026-09-07T02:00:00Z",
  "allowed_retention": ["short", "durable"],
  "include_pending": false,
  "conflict_policy": "current_only",
  "confidence_floor": null,
  "evidence_budget": {"max_items": 8, "max_chars": 2400, "max_tokens": 800},
  "cursor": null,
  "extensions": {}
}
```

`namespace`、`visibility`、`query_intent` 和 `purpose` 必填，purpose 可声明命名空间用途并协商策略。其他过滤项可省略，默认不限 subjects/types，include_pending=false，conflict_policy=current_only，at_time 使用运行时时钟，confidence_floor 为 null 或 0..1 的有限数。有效预算取调用预算与策略上限的交集。`time_range` 非空时含带时区的 start/end，端点可为空；历史查询显式选择 `conflict_policy=include_history`，不会使已撤回/越权内容重新可见。

检索顺序：授权候选空间 -> 时间与生命周期过滤 -> 选定检索策略 -> 去重/重排 -> 解析 owner 的事实和证据 -> 再验权限/revision -> 形成有预算的 AnswerEvidence。无权记录不能挤占合法候选；实际选中并注入的片段与仅检索命中的 ID 分开记录。

`RecallResult` 包含 `items[]`、`next_cursor`、`memory_revision`、`policy_revision`、`coverage` 和预算消耗。item_kind=fact 的 AnswerEvidence 包含 atom_id、revision、fact_status、owner_ref、完整断言、必要证据片段、有效期、冲突/缺失说明和可见范围。允许返回 pending 时，使用 item_kind=proposal、proposal_id、proposal_state=pending 和当前 session_id，不伪造 atom_id 或事实 revision；include_pending=true 的查询必须有 session_id。

`coverage` 至少列出实际执行的 strategy_id/version、各自状态、数据 revision 和省略原因；`usage` 返回实际候选数、模型调用数与消耗 token。coverage 描述本次声明路径的完成度，不承诺搜遍所有历史。撤回/无权对象的 ID 和正文不能出现在省略原因中。

游标绑定已认证调用方、查询/作用域指纹、权限和数据 revision、截止时间及剩余总预算；下一页重新授权。过期或无法保证一致性的游标返回 `rejected/cursor_invalid`，不能继续旧快照泄漏撤回内容。缓存键至少包含 scope、purpose、权限 revision、memory revision、policy revision、描述符 revision、摘要边界和预算；隐私撤回后未更新索引的结果必须经 owner 拦下。

## 6. 统一结果与错误

`memory.result.v1` 复用公共 OperationResult 的状态集合。以下写入结果中的 `succeeded` 只证明指定事实事务提交；投递使用另一条 DeliveryReceipt。

```json
{
  "schema_version": "memory.result.v1",
  "capability_id": "memory.proposal.submit",
  "capability_version": "1.0",
  "provider_id": "example.memory",
  "provider_generation": "generation-7",
  "request_id": "req-reading-001",
  "trace_id": "trace-reading-001",
  "status": "succeeded",
  "reason_code": null,
  "retryable": false,
  "degraded": false,
  "warnings": [],
  "output": {
    "operation_id": "memory-op-001",
    "proposal_id": "proposal-reading-001",
    "proposal_state": "persisted",
    "atom_id": "atom-001",
    "revision": 1,
    "owner_ref": "owner-001",
    "memory_revision": 104,
    "effective_retention": "durable",
    "retention_until": null,
    "valid_from": "2026-09-07T01:00:00Z",
    "valid_to": null,
    "policy_revision": "policy-3",
    "commit_state": "committed"
  }
}
```

结果封套的身份/版本/请求字段必须与派发记录一致；不匹配或结构非法时不能消费 output。`reason_code` 为可扩展、可机器读的原因；未知 reason_code 按已知顶层 status 处理，未知 status 视为协议不兼容，不能当成功。错误详情只含获授权字段位置和简短原因，不泄漏隐藏记录是否存在或堆栈正文。

| 情况 | status / reason_code | output 与恢复 |
| --- | --- | --- |
| 写入提交 / 主动放弃写入 | succeeded / null | proposal_state=persisted / skipped；回执区分两者 |
| 已持久化等待处理的提议 | pending / awaiting_boundary 或 review_required | operation_id；仅在 operation.get 能恢复查询时返回 pending |
| 合法查询无匹配 / 证据不足 | succeeded / empty 或 insufficient_evidence | items=[]；不能据此推断隐藏记录存在 |
| scope 缺失 / 字段非法 / 必需扩展不支持 | rejected / scope_required、schema_invalid、unsupported_feature | 无事实写入；修正请求后使用新逻辑操作 |
| 无授权 / 目标不可见或不存在 | permission_denied / forbidden；或 rejected / target_unavailable | 不返回隐藏目标信息 |
| 版本冲突 / 同键不同 payload | rejected / revision_conflict、idempotency_conflict | 不覆盖当前事实 |
| 未绑定兼容提供方 | unavailable / capability_unavailable | 能力局部降级 |
| 查询部分策略成功 | partial / provider_partial | 有界有效 items、coverage 和失败分项；全部不可用为 failed |
| 已证明尚未提交时超时 | timeout / deadline_exceeded | commit_state=not_committed；是否可重试由有界策略决定 |
| 无法确定是否提交 | uncertain / commit_unknown | 不换写入者重试；通过 operation_id 或原幂等键对账 |
| 提交前取消 / 旧代结果 | cancelled / caller_cancelled 或 generation_stale | 仅在可证明未提交时标记取消，否则对账为 uncertain |

`degraded` 是结果属性；`empty`、`forbidden`、`quarantined` 是原因或领域状态，均不新增顶层 status。`retention_until=null` 只说明未定固定到期时间，仍服从撤回和保留策略。

commit_state 专指事实提交：no_op/skipped 可以有持久化操作账本，同时保持 not_committed 且不带 atom_id。待处理提议的落盘也不等于事实已经提交。查不到操作的结果 output=null，不能从缺失回执生成 not_committed 证明。精确组合见契约包的 memory-result Schema。

`OperationLookup` 必填 namespace、visibility、purpose，定位方式为 operation_id 或原 capability_id + idempotency_key 二选一，按原调用方和 owner 授权查询。状态查询回执封套对应 memory.operation.get，本体 output 保留 original_capability_id、原提交 generation 和事实回执；查到失败或 pending 时沿用原操作状态。不可见和不存在均返回 rejected/target_unavailable；调用方不能将查不到结果当作未执行证据。

幂等账本键为调用方稳定身份 + owner + namespace + capability 主版本 + idempotency_key，排除 request_id、临时 session 和 provider_generation。相同键校验规范化语义 payload 摘要；同键同内容返回原提交证据，外层封套使用当前查询/尝试标识，保留原提交 generation。写入回执与事实在同一事务落地。保证窗口内重试不重复执行；调用方保存首次尝试时间和协商窗口，超过窗口禁止自动重提。服务端可从最小过期标记识别旧键时返回 uncertain/idempotency_window_expired；没有可验证记录时不承诺无限期去重，需先恢复账本或对账。

`expected_revision` 比较目标 atom 的当前修订；owner 的 `memory_revision` 用于查询新鲜度和变更水位，不增加必填的 `base_memory_revision` 整库锁。纠正/撤回、操作回执和 durable outbox 必须同事务提交；提交后事件派发失败不能改写 committed 结果。重复请求每次重新授权，原 pending 的后续合法状态转移由 owner 恢复任务处理，不因重复 submit 自动确认；改语义内容使用新逻辑提议。完整并发与故障边界见[状态机第 6 节](./MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md#6-写入纠正与回放的详细决策)。

## 7. 类型扩展与版本演进

类型/扩展使用声明者拥有的命名空间，例如 `example.reading.chapter`，不把所有未知类型归为固定 `other`。`predicate/object/qualifiers` 保留开放语义；操作、生命周期和权限字段保持有限且可协商的协议语义。

| 内容 | 兼容规则 |
| --- | --- |
| 未识别但非必需的 types/extension | 校验 JSON 大小/深度后保留为领域元数据，warnings 标注未解释；只按通用断言处理，不执行扩展指令 |
| 改变正确性或过滤语义的扩展 | 请求将其 feature 列入 required_features；提供方不支持时在写入/查询前拒绝，不能忽略过滤后扩大结果 |
| ACL、证据降级、保留豁免或自动动作 | 不能放在可忽略字段中；通过标准授权/策略能力协商，未知即拒绝 |
| 同主版本可选字段增加 | 更新 schema 修订并通过协商使用；不支持该修订的消费者由适配器按已知 schema 投影 |
| 改必填项、字段含义、空值或状态枚举 | 新能力主版本及对应 schema 主版本；可并行注册，不静默转换 |
| 扩展 schema 不兼容或缺失 | 必需扩展拒绝，纯展示元数据局部降级；旧投影不能丢掉重新导出所需的可选元数据 |

本草案的 `v0` 是文档成熟度，schema 中 `v1` 是拟议协议主版本；实现前将精确 JSON Schema、能力版本与契约指纹一起登记。本文的示例用于评审，不冒充可调用的 SDK。

## 8. 可插拔策略和资源边界

| 可选能力 | 输入 -> 输出 | 约束 |
| --- | --- | --- |
| `memory.extract`（enrich） | 已授权证据片段 -> MemoryProposal 列表 | 复用当前语义结果或按预算异步抽取，不直接写入 |
| `memory.recall.search`（remember） | 受限查询与候选访问范围 -> atom_id/revision 候选 | 支持向量、关键词、时间线或关系图实现；Memory 解析并复核引用 |
| `memory.recall.rerank`（enrich） | 有界已授权候选 -> 排序及分数 | 不新增未授权事实，不将内部评分直接注入 Prompt |
| `memory.policy.evaluate`（enrich） | 提议与有限上下文 -> 保留/冲突建议及 policy_revision | 建议经 Writer 与 Runtime 校验；不能改变 ACL 和保护授权 |

这些能力采用同一封套、能力版本和结果规则，输入/输出 schema 在提供方进入切片前登记，未协商时不调用。候选访问范围由 owner 的受限句柄或受控页面解析，不能把一段 caller 自报过滤条件当作权限；远端索引只处理单独获授权的投影。策略代码和索引凭证按其信任边界部署，注册函数不提供进程隔离。

一次查询只有一个总预算，策略分配并发、候选数、deadline、模型调用和费用子额度；新增提供方不能扩大默认 fan-out 或提高普通聊天模型调用次数。超时收束到已授权的可用结果，失败影响记录在 coverage；没有有效路径时明确失败。普通请求不等待索引重建或全库重排。

权威存储保持单一 Writer。更换 SQLite、向量或图索引应通过 owner 内部适配器完成，事务语义含 revision 校验、幂等回执、outbox 和撤回记录；不具备这些保证的后端只能作为投影。变更事件按订阅者授权过滤，以 ID/revision 为主，按事件 ID 去重；重放默认禁外部动作。游标超保留窗口需获授权的有界重同步，不能漏掉撤回后继续使用旧缓存。

`MemoryChangeEvent` payload 必含 event_id、namespace、owner_ref、atom_id、atom_revision、memory_revision、change_kind、occurred_at 和 trace_id；change_kind 为 added/corrected/retracted/expired。发布者为权威 Writer，源事件按 owner/source scope 保存，向订阅者投递时由 Runtime 解析其合法 scope。事件默认不带记忆原文，下游解析引用时重新校验；撤权时关闭订阅并发送不含隐藏事实的缓存失效通知。核心据此取消失效的候选，来源插件不能发布 memory.changed 来伪造事实提交。

授权过滤后的 `memory_revision` 不保证连续；它不等于订阅流游标。0.2.0 已形成订阅确认和快照重同步的首版传输 Schema，成熟度仍为 review；控制字段不进入现有封闭的 memory.changed.v1 payload 或事件封套。语义由[状态机 §6.8](./MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md#68-订阅确认与增量重同步)维护，本稿约定以下对外边界：

| 对象 / 操作 | 对外语义与冻结要求 |
| --- | --- |
| subscribe / 绑定 | 解析可信 subscriber、owner/namespace、purpose、权限/policy revision、generation、lineage、schema 和预算；控制操作复用公共生命周期，订阅权限独立授予 |
| stream cursor / 投递批次 | 服务端签发不透明起止边界；不能用 query.next_cursor、atom_revision 或 memory_revision 代替。空可见批次也可有已扫描边界，但不暴露隐藏对象 |
| checkpoint / ACK | checkpoint 是消费者已持久应用的投影代次、截止游标与去重/版本依据；ACK 是对此的确认。服务端另存接受的确认进度，不把已派发等同于已应用 |
| ACK 绑定和重复 | 重新校验身份、subscription、租约、generation 和已派发边界；当前有效绑定重复确认幂等，累计确认不越过未应用页，旧租约不覆盖新租约 |
| resync / snapshot | 固定授权、lineage、epoch 和一致性边界 W；分页持久化 staging，再应用 W 后到 C 的日志，以短事务切换投影及 inbox/checkpoint，之后确认 C 并从 C 续接 |
| 撤权 / 失效 | 旧权限快照不得续读或激活；失效通知只带不透明订阅引用，不带隐藏 atom ID；重同步状态不增加 OperationResult 顶层枚举 |
| 过滤 / 当前投影 | 维护 active 投影必须覆盖纠正、撤回、到期和授权失效；局部 change_kind 通知不能独自证明当前事实新鲜 |
| 预算 / 保留 | 批次、总在途字节、租约、重试、staging/日志存储和快照 pin 均有额度；分页/重连不重置总预算，超额慢消费者转入有界重同步 |

当前 query 的普通分页不提供恢复所需的 snapshot 完整性证明；控制 Schema 明确页/完成标记、后缀边界及错误形状，仍需通过注册协商才能调用。重同步按授权完整替换投影时不得通过墓碑泄漏无权对象；无法证明一致边界和日志连续性则不可用，不能从旧缓存宣称恢复成功。

消费者持久化应用、去重与 checkpoint 后再确认；迟到旧版本不能恢复已撤回的目标，也不能按 owner 最大水位丢掉其他 atom 尚未处理的合法事件。原生回放与跨平台投影重建默认没有外部执行权；迁移保留 canonical ref、lineage 和源 provenance，在新宿主重新授权绑定后恢复，不直接携带旧平台句柄继续访问。

预算覆盖准入、取数、解码、并发工作集和持久队列，输出后大小校验不能独立防止 provider 提前物化造成的内存峰值。分页与 provider 下推额度应先于对象复制，普通写入在 outbox 容量耗尽前背压，撤回治理保留处理能力。部署 profile 的额度及测量方法见[状态机 §6.7](./MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md#67-内存存储与延迟预算)。

### 8.1 订阅控制 wire 决策与恢复轨迹

首版 `projection_kind=references` 同步事实标识、版本和失效状态；快照页只包含 fact_ref/tombstone，不复制正文、原始证据或向量。正文仍通过 owner 当前授权查询。完整数据备份、历史正文快照和向量索引重建需要另行协商投影格式，不能把本版引用快照当作这些能力已经完成。

| 控制能力 / 对象 | wire schema | 首版约定 |
| --- | --- | --- |
| memory.changed.subscribe | memory.subscribe.v1 | namespace、visibility、purpose、subscriber_generation、view_kind、change_kinds、start、limits；不接受 caller 自报 owner |
| memory.changed.ack | memory.ack.v1 | subscription_id、subscriber_generation、lease_id、target 和完整 checkpoint；target 区分 batch 与 snapshot |
| memory.changed.resync | memory.resync.v1 | start 或 page；page 必带 snapshot_id、snapshot_epoch、page_cursor |
| 三种控制回执 | memory.subscription-result.v1 | 复用公共结果身份字段；按能力区分 bound、acknowledged 和 snapshotPage，错误 output=null |
| Runtime 投递批次 | memory.change-batch.v1 | binding、订阅与消费者代次、lease、batch_id、from/to_cursor、delivery_mode、snapshot_id 和原样 events[] |
| 可移植恢复记录 | memory.checkpoint.v1 | binding、projection_generation、state、snapshot、page_cursor、stream_cursor、last_event_id、memory_revision、inbox_ref、stored_at |

三个控制能力均为同步控制调用，side_effect=local，只改变订阅/进度，不提交 MemoryAtom；复用公共注册与 release，不启动新总线。subscribe/ack 需 memory.subscribe，resync 另需 memory.read 和合法 purpose；current_projection 在绑定时就确认完整恢复能力与权限可用。target provider 与 subscriber_generation 均须对照 Runtime 绑定，DTO 本身不认证身份。

**起点与绑定。** current_projection 必须声明四种 change_kind，start 只允许 snapshot 或携带 active checkpoint 的 resume；notifications 可选部分种类，只允许 live 或 resume，不承诺当前事实完整。首版不采用额外失效旁路来允许 current_projection 省略 retracted/expired。新 snapshot 订阅回执可以是 succeeded 且 subscription_state=resync_required，表示绑定已建立、尚无可用当前投影；live 状态必须有服务端 cursor。权限、policy、lineage、保留 epoch、view_fingerprint 和 contract_fingerprint 均属于恢复绑定。

**创建与重试。** subscribe 和 resync.start 在封套必带 idempotency_key，同稳定调用方/owner/能力主版本/键在协商窗口内只建立一个逻辑资源；同键改变语义请求拒绝。相同消费者代次的重复调用返回当前合法绑定或原 snapshot；换代需重新绑定，旧创建键不能使旧句柄复活。resync.page 以 snapshot/epoch/page_cursor 定位同一页，ACK 以绑定/租约/target/截止游标/投影代次重复确认；二者不额外接收幂等键。request_id、trace_id、调用 deadline 可以属于新尝试，不能重置恢复总预算或偷偷分配第二份 staging。结果丢失先重试原控制定位；memory.operation.get 仍仅查询 proposal 账本。

**分页与确认。** snapshotPage 在每页返回固定快照描述、page_id、当前 page_cursor、items、next_page_cursor。非末页的 completion/activation_lease 必为 null；末页 next_page_cursor=null，必须返回服务端 completion_token、tail_until_cursor C 和独立 activation_lease，空快照也使用这一终页形式。completion_token 关联完整页链、W、C、授权、lineage 和 epoch；这是待运行服务核验的证明句柄，不是消费者自报的计数器。

页级落盘后 checkpoint.state=snapshot_pages，page_cursor 表示下一页，stream_cursor 仍为 W；末页落盘后进入 snapshot_tail，page_cursor=null。Runtime 在已绑定的有界投递通道发送 W 后至 C 的 snapshot_tail 批次，消费者逐批持久化 staging/inbox/checkpoint 后 ACK；这类 ACK 返回 resyncing，只确认 staging 的持久进度，不激活投影，也不释放尚需的恢复证明。没有可见尾部事件时同样需要服务端 C 边界；不能靠等待超时断言已追平。

消费者应用到 C 后以短事务切换投影及 checkpoint.state=active，再用 target.kind=snapshot、snapshot_id、completion_token 和 activation_lease 确认。服务端重验快照完成证明、当前绑定与租约、checkpoint.snapshot、投影代次及已完成的连续尾部边界，才返回 live 并派发 C 后的批次。同一消费者的过期激活租约可通过重取终页受控续领，保持同一快照/完成边界并 fence 旧租约；快照本身过期则不能续领。切换后 ACK 丢失允许以当前有效租约确认同一持久结果，不能重新生成事实或盲目重建全部投影。

**恢复与迁移。** checkpoint 内的 inbox_ref 只定位消费者持久化的一代去重/版本记录，是逻辑引用而非路径或存储凭据。迁移应一并携带相符的投影和去重数据，再提交 checkpoint 供新宿主重新授权绑定；只复制 JSON 不能证明应用已持久化。active 记录可用于 resume；snapshot_pages/tail 仅在当前仍有效的恢复任务内续接，跨绑定先按授权重建。新服务无法验证旧 cursor/lineage 或完整性时返回 resync_required，不直接采信 checkpoint 的自报版本。

**失败与预算。** 非法字段为 rejected/schema_invalid；旧租约为 rejected/lease_stale，失效游标/快照为 rejected/cursor_invalid 或 snapshot_expired，旧代为 rejected/generation_stale，撤权为 permission_denied/forbidden，无法给出一致快照为 unavailable/snapshot_unavailable。控制结果不确定为 uncertain/control_unknown，已证明未推进时才返回 timeout/cancelled；不得用它证明旧订阅从未建立。错误无 owner/atom 详情，普通 resync_required 是订阅状态或原因，不增加顶层 status。

limits 协商批次 item/序列化字节、总在途字节/批次、租约时长、重试次数、快照累计传输字节、staging/保留后缀磁盘额度和 recovery_deadline_at。调用 budget.max_model_calls 固定为 0；缺省 max_output_bytes 仍受有效 max_batch_bytes 限制。服务端有效额度取部署总预算与请求交集，分页和重连不重置；JSON Schema 只验证类型和下界，不能证明实际取数、解码、WAL 和进程峰值受控。

完整字段见[机器契约包](./contracts/v1/README.md)，恢复轨迹见[版本化夹具](./contracts/v1/subscription-recovery.fixture.json)。字段相等、cursor 真伪、当前 ACL、W/C 顺序、租约 fence、checkpoint 落盘与内存占用均需后续隔离验证；格式合法的伪造值仍可能在运行时被拒绝。

## 9. 旧稿与兼容接口映射

| 旧名称/行为 | 本稿对外约定 |
| --- | --- |
| MemoryMutation / RecallRequest | 提交前的领域变更语义 / 查询语义；对外能力统一为 MemoryProposal / MemoryQuery，不另建平行入口 |
| `scope_id/actor_id/subject_id` 的精简 scope | 只作概念速记；标准调用用完整 RuntimeScope + payload.subject，缺失身份不补猜 |
| `update`、`invalidate`、`upsert`、`correction_of` | 在明确目标和 expected_revision 后适配为 correct/retract；add 用独立操作；无法明确目标时返回待处理或拒绝，不盲目 upsert |
| `expire` | 由生命周期 owner 执行；外部变更有效期使用 correct |
| `durability`、`requested_persistence` | 适配为 retention 建议、no_op；pinned 只可申请 protected，须有相应授权 |
| `dedupe_key`、`min_confidence`、`intent`、`include_history` | 封套 idempotency_key、confidence_floor、query_intent、conflict_policy=include_history |
| `sent/probable_sent/unknown` | 仅为旧投递标签；根据实际证据适配 submitted/accepted/delivered/uncertain，不按名称宣称送达 |

旧 durability 的 ephemeral/short/durable 分别建议 session/short/durable；normal 的有效期限由具名兼容策略决定并回传 revision，不编造固定天数；pinned 不授予不可删除特权。旧结构不会被本文自动重写，适配记录需声明版本、字段损失和未确定归属。正式新接口不同时接受同义字段，避免优先级歧义。

## 10. 评审与冻结门槛

先用一个外部来源、一个独立查询调用方和一个时间线检索提供方验证接口。新增“共读章节经历”时，仅增加类型描述、适配与策略，公共请求和结果封套应保持稳定；变更只影响选中的能力。

评审必须覆盖 [反例集](./MEMORY_COUNTEREXAMPLE_EVAL_V0.md) 的 EXT-01 至 EXT-14：新类型、混合版本、伪造身份、扩展字段、读取隔离、多个提供方、重复写入、撤回、预算和卸载。产物包括能力声明、请求/回执样例、schema 兼容检查与预算观测。外部网络、存储适配和真实热重载未实测前保留待验证状态。

可靠写入进一步使用该反例集第 8 节的 MW-01 至 MW-24，检查目标版本并发、事务故障、确认丢失、撤回乱序、游标过期及内存放大。本轮仅完成设计，相关结果均为 not_run，不把原型分支测试写成可靠 Writer 已完成。

订阅与重同步使用该反例集[第 9 节](./MEMORY_COUNTEREXAMPLE_EVAL_V0.md#9-memorychanged-订阅与重同步场景设计)的 SUB-01 至 SUB-16，覆盖重复/非法确认、授权过滤、快照中断与并发变更、慢消费者、矛盾事件和跨平台续接。控制格式、版本化夹具、服务端持久化记录、跨字段关联校验和故障 barrier 已在 review 设计中形成；运行场景全部保持 not_run，下一步进入隔离契约验证，不再增加平行记忆字段。
