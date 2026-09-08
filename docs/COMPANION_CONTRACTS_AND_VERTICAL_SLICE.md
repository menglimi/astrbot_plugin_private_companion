# 陪伴公共契约与首条纵向闭环

> 导航：[设计总纲](./FRAMEWORK_DESIGN.md) / [主题目录](./FRAMEWORK_DESIGN_INDEX.md)。定位：公共契约草案；维护 DTO 组合与切片用例，字段依赖注入协议。

## 目标

设计上把所有拓展放在同一张地图中，避免记忆、现实触及、实时共处各自长出一套人格、权限、主动和会话协议；落地上只先验证一条最小纵向链，避免一开始陷入全量存储、设备和页面工程。

本稿是公共契约草案，不是最终数据库 schema。任务、会话、操作和回执已有[公共执行契约包 0.1.0](./contracts/execution/v1/README.md)及跨插件设计夹具，成熟度 review；领域差异放在独立 payload Schema 和能力协商中。运行验证与完整领域契约仍待完成，不得把旧框架的固定中文类别、词表和动作枚举搬进公共层。

公共字段的语义以上游[状态、事件与反馈闭环设计](./COMPANION_STATE_EVENT_FEEDBACK_DESIGN.md)及[第一优先级领域状态机](./DOMAIN_STATE_MACHINES_V0.md)为准；本稿只负责把它们收敛为可验证的跨插件 DTO。

## 一、双轨推进

### 设计轨道：全域共同建模

所有领域共同使用以下概念：

```text
RuntimeScope
ContextContribution
Observation
EvidenceRef
IntentGraph
MotivePlan
ActionRequest / OperationResult
DeliveryReceipt
MemoryProposal / MemoryQuery
SessionRecord
TaskEnvelope
CapabilityDescriptor
```

共同定义：

- 会话、用户、群组、设备和公开目标的作用域；
- `sharing_mode=session|user|global` 与由 Kernel 签发的 `sharing_anchor`；
- `visibility`、`redaction` 和跨插件导出规则；
- 观察、决策、执行、投递、记忆写入之间的因果关系；
- 取消、重载、任务代际、预算、超时、重试和降级；
- AI 可自由扩展的语义字段，以及代码必须校验的安全不变量。

### 实现轨道：纵向切片

本稿描述完整闭环的组合关系；实际顺序与完成条件统一见[总纲第 10 节](./FRAMEWORK_DESIGN.md#10-当前进度与统一路线)：契约定型 -> 只读查询与回放 -> 可靠记忆写入 -> 低风险主动 -> 现实与 Together -> 其余领域。不要把下方完整流程理解为一次性交付要求。

QQ 空间和问题治理不进入当前主干。

## 二、公共契约最小核心

### `RuntimeScope`

```text
ecosystem_id / installation_id / bot_id
runtime_instance_id / host_kind / host_id（宿主实例信息按能力声明）
platform / account_id
conversation_ref / platform_conversation_id
conversation_id（旧兼容字段）/ session_id
user_id / group_id
persona_id / persona_binding_revision
```

字段沿用[注入协议 5.1](./COMPANION_INJECTION_PROTOCOL.md)，由运行时解析，不同身份与平台字段不可合并为裸用户 ID。主体、业务 namespace、visibility 和 purpose 由领域 payload 声明并校验；它们表示申请范围，不能自行扩大权限。早期 scope_id/actor_id/subject_id 是概念速记，不作为另一套外部 schema。

### `SharingMode` 与跨会话归属

需要保存或投影的状态具有以下共享语义；语义字段不表示所有 DTO 都增加同名顶层属性：

```text
sharing_mode: session | user | global
sharing_anchor: 由 Kernel 解析的不透明稳定归属引用
sharing_policy_revision: 当前共享策略版本
```

`session` 绑定真实 `session_id`，只在当前实际会话内可见；`user` 绑定同一平台和账号下的 `user_id`，允许该用户跨私聊和群聊共享用户级状态；`global` 绑定显式的 installation/bot/persona 公共分区，适合公共人格设定和世界资源。`sharing_mode` 不替代 RuntimeScope、visibility、purpose 或 retention，也不独立授予读取权限。

精确 wire 由[共享契约包 0.1.0 review](./contracts/sharing/v1/README.md)维护：Memory 请求使用 `payload.extensions["companion.sharing"].payload`，召回项使用各自的 `extensions["companion.sharing"].payload`；本包新定义的 ContextContribution/StateTransitionProposal 使用顶层三字段。必需特性通过 `companion.sharing@1` 协商，不修改已有封闭 Memory v1 Schema；统一事件、控制描述符和订阅的概念字段不能直接当作可发送字段。

读取上下文最多按 `session -> user -> global` 合并，每层都要重新经过授权、用途、版本、TTL、脱敏和预算检查。写入不得由模型把 session 状态自动扩大到 user/global；扩大共享范围必须产生带证据的 StateTransitionProposal 或 MemoryProposal，并由 owner 依据策略和授权确认。群主题、成员关系和第三方发言不因存在 user_id 就进入用户跨会话档案。

首条共享模式切片至少覆盖：同一平台用户从私聊进入群聊后的用户级偏好召回；新建 session 后旧会话临时状态不可见；global 公共世界状态更新后多个会话可读取但不包含用户私密事实。三类场景均需验证跨平台/跨人格映射被拒绝、缺失 anchor 返回 `scope_unresolved`、撤回后投影失效和缓存不越权。

### `EvidenceRef` 与 `Observation`

```text
EvidenceRef:
  id, source_type, source_id, evidence_kind, observed_at, expires_at

Observation:
  kind, payload, evidence_refs, observed_at, expires_at
```

观察只说明“看到了什么”，不直接说明“这意味着什么”。例如屏幕识别到“用户在编辑文档”，不能自动变成“用户希望我帮忙”。

EvidenceRef 指向的主体、权限、revision 和保留状态由授权来源解析；自报 confidence 不构成授权或真实性证明。

### `IntentGraph` 与 `MotivePlan`

```text
IntentGraph:
  intents, targets, conditions, negations, quoted_content, uncertainty

MotivePlan:
  stance, motive, candidate_actions, rationale, confidence, expires_at
```

`stance` 可以是等待、观察、回应、靠近、分享、执行或放弃等开放语义。代码不得扫描 `rationale` 反推动作，动作必须出现在结构化字段中。

### `ActionRequest`、`OperationResult`、`DeliveryReceipt`

```text
ActionRequest:
  capability, scope, payload, idempotency_key, budget, preconditions

OperationResult:
  status, reason_code, output, degraded, retryable, warnings

DeliveryReceipt:
  status, platform_message_ref, delivered_at, verification, attempts
```

结果沿用[注入协议 6.6](./COMPANION_INJECTION_PROTOCOL.md)：OperationResult.status 为 succeeded、pending、partial、rejected、permission_denied、unavailable、timeout、uncertain、failed 或 cancelled；DeliveryReceipt.status 为 pending、submitted、accepted、delivered、partial、uncertain、failed 或 cancelled。queued 是内部任务状态，早期 sent/probable_sent/unknown 通过回执证据适配，不能成为一组并行外部状态。超时但可能已送达时先查询或保留 uncertain，不能盲目重试。

### `MemoryProposal`

```text
MemoryProposal:
  operation, target_atom_id, expected_revision, namespace, visibility, purpose,
  subject, predicate, object, qualifiers, polarity, attribution, evidence_refs,
  confidence, valid_from, valid_to, retention, types, extensions

共享语义位于 extensions["companion.sharing"].payload:
  sharing_mode, sharing_anchor, sharing_policy_revision
```

情绪变化、临时状态和角色表达不自动进入长期记忆。Memory Writer 只接受带证据的提案，并负责冲突、撤回、过期和作用域校验。

外部调用统一使用[记忆提议、查询与外部注入契约](./MEMORY_PROPOSAL_QUERY_CONTRACT_V0.md)的能力 ID、标准封套和结果。operation 为 add/correct/retract/no_op；RuntimeScope、幂等键和 generation 放在封套。扩展属性支持命名空间和版本协商，不能复写安全字段。

### `TaskEnvelope` 与 `SessionRecord`

```text
TaskEnvelope:
  schema_version, task_id, task_revision, kind, mode, scope, owner_ref,
  supervisor_generation, binding,
  parent_task_id, source_event_ref, session_id, operation_id,
  idempotency_key, side_effect, state, attempt_id, attempt_no, occurrence,
  next_run_at, deadline_at, budget_ref, wait, retry,
  checkpoint_ref, result_ref, cancellation_reason, last_error

SessionRecord:
  schema_version, session_id, kind, scope, owner_ref, controller_generation,
  persona_binding_revision, phase, session_revision, participants_ref,
  capability_grants_ref, context_ref, context_revision, checkpoint_ref,
  started_at, last_activity_at, expires_at, ended_at,
  termination_reason, summary_ref
```

任务和会话必须能在重启、重载、断线和能力缺失后恢复或明确终止。不能让旧代任务在新运行时写回状态。`task_id` 标识一个可恢复工作单元，`attempt_no` 标识本次执行尝试；重试不创建新的逻辑操作，除非语义 payload 或授权用途改变。`checkpoint_ref` 只指向任务 owner 的持久状态，不能把内存对象、宿主 event 或数据库连接塞入 DTO。

这些字段已登记为公共执行 0.1.0 review Schema，不追加到现有 memory.*.v1 请求或结果。已有能力通过 Runtime 本地 TaskContext/派发记录关联任务；只有实际协商 companion.execution@1 的能力才使用新 Schema。短同步查询可以只使用有界 TaskContext，不强制每次查询写任务账本、创建后台轮询或启动独立进程。

owner_ref 由对应领域解析，不默认采用 Memory 的 owner 结构。首次调用尚无 owner 签发的 operation_id 时显式为 null，按原稳定幂等键定位；调用方不能自造一个 operation_id 当作已受理证明。parent_task_id、source_event_ref、session_id、checkpoint_ref、result_ref 在不适用或尚未建立时为 null。只读任务不强制幂等键；有副作用的操作必须使用能力规定的稳定键。budget_ref 指向共享额度账本，重试和恢复不能复制一份初始额度。

supervisor_generation 标识调度控制者，与 binding.provider_generation 分开；mode=orchestration 的父任务 binding=null、side_effect=none，领域动作作为子步骤绑定。mode=capability 尚未绑定时可为 null，admitted/running 等派发阶段必须具有完整 capability/provider/version/generation。kind 使用声明者命名空间，领域可以增加任务种类，不能借 kind 自行增加权限。周期计划的 occurrence 同时记录 schedule_id、稳定 occurrence_id 和 scheduled_for；同一 occurrence 的重试复用幂等键，下一次合法触发用新键，不能拿整天或整个 session 作为去重单位。

#### 公共执行边界

任务状态属于调度层，动作结果属于领域/平台调用层，会话阶段属于连续交互层，三者不互相替代：

| 对象 | 首版状态 | 转移约束 | 持久化责任 |
| --- | --- | --- | --- |
| `TaskEnvelope.state` | `created`、`admitted`、`running`、`waiting`、`retry_scheduled`、`cancelling`、`succeeded`、`partial`、`failed`、`uncertain`、`cancelled`、`expired`、`dead_letter` | 准入需身份/代际/额度；状态按 task_revision 和 attempt fence 更新；结果未知进入 uncertain，不能直接重新执行 | TaskSupervisor 保存状态、尝试号、deadline、预算、取消原因和对账引用 |
| `OperationResult.status` | `succeeded`、`pending`、`partial`、`rejected`、`permission_denied`、`unavailable`、`timeout`、`uncertain`、`failed`、`cancelled` | 表示本次能力调用，不表示消息已送达；`uncertain` 不能自动换 provider 重做 | 能力 owner 保存逻辑 operation、幂等键和原始提交证据 |
| `DeliveryReceipt.status` | `pending`、`submitted`、`accepted`、`delivered`、`partial`、`uncertain`、`failed`、`cancelled` | `accepted` 不等于 `delivered`；重试沿用 part_id/逻辑操作，不能重复已确认部分 | delivery owner 保存平台引用、尝试、查询回执和历史归属 |
| `SessionRecord.phase` | `requested`、`invited`、`joined`、`active`、`paused`、`reconnecting`、`ending`、`ended`、`declined`、`expired`、`failed` | 沿用领域状态机；不需要邀请的能力可省略 invited/joined；每次变更校验 session_revision、controller_generation 和人格绑定 | Session owner 保存上下文引用、能力票据、结束原因和可选摘要引用 |

只有明确支持独立 part 的任务才能聚合为 partial，原子事实写入仍不能返回部分提交。所有 part 均未提交且已停止时才能整体 cancelled；有已成功部分则保留 partial，有未知部分则保留 uncertain，并分别记录剩余 part 的取消结果。dead_letter 表示调度停止自动恢复，result_ref 中的未知效果不被改成“从未执行”。uncertain 可由当前获授权的对账任务收敛，旧 worker 不能用迟到结果直接覆盖新 task_revision。

waiting 必须有 owner 可恢复的等待条件和到期时间；retry_scheduled 只适用于已证明可重试或提供可靠远端幂等保障的操作。不存在操作、查不到记录或 worker 租约过期均不构成“没有提交”的证明。对于不能在外部服务上验证 fence 的非幂等动作，切换本地 generation 只能阻止新的派发；已发出的请求仍先查远端回执，不能据此并发启动第二次发送。

#### 逻辑操作、尝试和因果关联

一个外部动作至少关联以下标识：

```text
operation_id       owner 签发的逻辑能力操作 ID；签发前为 null，签发后跨尝试保留
idempotency_key    稳定调用方/owner/能力主版本下的去重键，不能含 request_id 或 generation
task_id            调度工作单元；可因恢复接管而继续
attempt_id         单次运行尝试；超时、重载或重试产生新值
part_id            可拆分动作的独立部分；已成功部分不重做
trace_id           诊断关联，不作为去重或权限凭据
```

`caused_by` 只引用已经授权的事件、观察、候选、会话或操作记录，不把完整原文和宿主对象传入下游；因果链有最大深度、总字节和保留期限。`source_event_ref`、`session_id`、`operation_id` 和 `task_id` 用于对账，不自动授予读取历史或发送权限。平台 callback 迟到时先按 operation/part 查询当前回执，不能按 callback 到达顺序逆转已持久化状态。

#### 取消、未知和恢复

取消按 barrier 分三类记录：准入前拒绝、外部提交前已停止、提交后效果未知。只有前两类在 owner 明确证明未提交时可对外返回 `cancelled`；提交后未获可靠结果必须是 `uncertain`，由原能力的对账入口处理。重启恢复先读取原 task/operation 账本和 checkpoint，再重新授权、检查 generation fence、剩余 deadline 与预算；不能用新 task_id 把未知动作发送第二次。会话断线只进入 `reconnecting`，不等价于用户重新同意，也不自动恢复已过期 capability grant。

执行 deadline 到期后停止新增效果；只读回执对账和资源清理可使用事先预留、独立有界的恢复额度，不借对账名义再次生成或发送。控制面按当前授权输出结果，已撤销权限的原文不随任务恢复返还。Session 的 paused/reconnecting 停止实时动作，ended 不能被旧回调重新打开；必要的结束后摘要通过显式、短期、只读证据授权建立独立任务，结束会话不隐含授予长期读取或记忆写入权。

#### 两条跨领域设计走查

以下使用已有领域契约核对公共对象，均为设计预期，run_status=not_run，不表示已经执行流程：

| 流程 | 领域步骤与关联 | 公共边界应产生的结果 |
| --- | --- | --- |
| 创作产物分享 | Content 在 project_revision 上生成章节；Image 独立生成封面并返回 MediaAssetRef；Content 提交 offer-share 候选；编排器按当前授权交给 delivery owner | 每个有副作用步骤有独立幂等键/operation，任务通过有界 parent/caused_by 关联；作品保存成功不表示分享成功。封面晚到不覆盖新作品；消息 uncertain 时只对账发送，不重新生成章节或付费图片 |
| 共处会话恢复 | Session active 时更新媒体进度；断线后进入 reconnecting；新控制者重验参与者、票据、session_revision 和上下文 checkpoint | 进度是领域 checkpoint，媒体帧不持久化到通用任务队列；恢复同一逻辑 session 并产生新 controller_generation，旧流量/回调失效。会话结束释放本会话资源，可选摘要另建任务 |

创作流程依据[创作联动契约](../../astrbot_plugin_content_companion/docs/COMPANION_STORY_CONTRACT.md)，共处阶段依据[领域状态机](./DOMAIN_STATE_MACHINES_V0.md#3-实时共处状态机)。各领域事务独立提交；流程编排没有跨 Content/Image/平台的原子事务，也不默认用“补偿”删除已经产出的作品或撤销已发生的外部动作。

任务、参与者、上下文和因果链均按引用分页恢复；子任务同时受父级预算、runtime 总并发/在途字节、最大深度和队列存储额度约束。同步链路没有未决工作时不生成常驻任务；旧 task/inbox/媒体进度的保留与回收按 owner 负责。会话断开不能无界缓冲媒体，每帧内容和连接凭证留在领域 Session/Host Adapter。

#### 公共执行 0.1.0 wire 决策

精确字段由[公共执行契约包](./contracts/execution/v1/README.md)维护，使用独立 manifest 和 profile，复用已锁定的公共词汇/RuntimeScope。六个公共 Schema 与一个夹具领域 Schema 只定义格式和离线验证，不包含调度器、数据库或平台实现。

| 对象 | 本版精确选择 | 后续运行校验 |
| --- | --- | --- |
| ActionRequest | companion.action.v1；标准请求封套加 binding_ref、execution、可空 idempotency_key、payload_schema、payload、preconditions；required_features 含 companion.execution@1 | binding 与真实 caller/provider/scope 匹配；副作用类别来自 descriptor，不由 caller 自报；有副作用时 key 必须非空 |
| execution | task_id 可空支持短同步调用；attempt_id、budget_ref 必填；part_id/operation_id 可空；caused_by 为带 kind/ref/revision 的引用数组 | task、part、owner 及原操作关联；因果深度/字节和共享额度；不能用 caller 自造 operation_id 作为受理证明 |
| TaskEnvelope | companion.task.v1 是 owner 输出记录；binding 独立对象，mode 区分 capability/orchestration；attempt_no=0 时 attempt_id=null；waiting 需有界 wait，retry_scheduled 需 next_run_at 与 retry 依据；终止/未知状态需 result_ref | 创建/取消/查账命令另行设计；retry.proof_ref 的真实性、task_revision/attempt fence、原逻辑键和实际持久化 |
| SessionRecord | companion.session.v1 是授权快照；participants_ref、capability_grants_ref、context_ref、checkpoint_ref 不复制集合；reconnecting 必须有 checkpoint；终止状态带 ended_at/termination_reason | scope.session_id 与逻辑 session 的对应、当前人格/票据、控制者 fence；ended 不能被旧快照反向恢复 |
| OperationResult | companion.operation-result.v1 复用公共状态；execution 对应当前调用，output_schema/output 同时为空或同时有值；新增 effect_state 和 receipt_ref/parts_ref | effect_state 是能力声明成功条件下的证据摘要，不是调用者自证；返回前重验权限，原子能力不接受 partial |
| DeliveryReceipt | companion.delivery-receipt.v1；receipt_kind=part 时 part_id 必填且不允许 partial；aggregate 时 parts_ref 必填；receipt_revision 和 verification 描述证据更新 | parts_ref 对应完整部分集合及计划版本；确认/失败/未知的聚合、真实平台回执、历史 owner 和迟到版本 |

effect_state 区分 not_applicable、not_applied、applied、partial、unknown。succeeded 可用于只读、已证明执行或明确 no_op，须返回领域结果；applied 必须带 owner operation_id 与回执引用。pending 必须可定位原 operation/receipt，不能只表示进了内存队列。partial 必须给出可分页的 parts_ref 并标记 degraded；它不授予失败部分自动重试权。uncertain 固定 unknown、retryable=false，不输出猜测的领域成功体。timeout/cancelled/failed 只使用 not_applicable/not_applied；当前准入因权限、绑定或能力不可用被拒绝时可保留 unknown，不能从拒绝推断原操作从未提交。

同一 send 能力的成功条件可以是“平台已受理提交”；其 applied 不表示用户已收到。DeliveryReceipt.delivered 另需 delivered_at、delivery_confirmed 证据，单 part 还需平台回执引用；accepted 的 delivered_at 必为 null。aggregate 不内嵌无限 parts 数组，相关完整性证明仍需 owner 校验。一个部分未知时不能只因另一个部分受理就报告整体送达。

payload_schema/output_schema 必须属于协商的本地登记资源，公共封套通过之后继续校验领域 payload。夹具使用 fixture-only 的创作、发送、会话 payload Schema；其中 example.* capability ID 是测试声明，不是已经安装的接口。本版没有将 Task/Session 快照直接当作修改命令；注册、取消、查账及恢复控制的字段语义由[注入协议 §4](./COMPANION_INJECTION_PROTOCOL.md#41-capabilitydescriptor-字段设计)和[生命周期 §4](./COMPANION_EXTENSION_LIFECYCLE_V0.md#4-控制面操作语义)维护，控制 Schema 另行协商。

[110 个格式案例](./contracts/execution/v1/cases.json)与[跨插件夹具](./contracts/execution/v1/fixtures/cross-plugin.fixture.json)映射 LC-09--LC-13；17 份 DTO 给出两条设计轨迹，3 对请求/回执做静态关联检查。旧代、伪造证明、作用域错配等 shape-only 案例保留运行拒绝要求。静态案例与指纹通过不证明动作执行、故障恢复或峰值内存受控，LC 场景仍为 not_run。

## 三、首条纵向闭环：记忆核心 + 低风险主动

### 流程

```text
用户消息
  ↓
建立 RuntimeScope 与 EvidenceRef
  ↓
AI 生成 IntentGraph
  ↓
AI 判断是否形成 MemoryProposal
  ↓
Memory Writer 校验作用域、证据、冲突和保留策略
  ↓
生成低风险主动候选
  ↓
AI 形成 MotivePlan，决定等待、回应或靠近
  ↓
ActionRequest → 平台执行 → DeliveryReceipt
  ↓
仅依据真实结果写入反馈、情绪或后续 MemoryProposal
```

### 首批只验证的能力

- 时间化记忆原子：新增、修正、撤回、过期；
- 私聊与群聊作用域隔离；
- 记忆写入可恢复、可去重、可进入死信；
- 一个低风险主动动作，例如沉默后延续一个已经讨论过的话题；
- 发送失败、发送未知、插件重载、权限不足和新消息接管；
- 用户没有反馈时，不推断“用户喜欢/不喜欢”。

### 示例

用户说：“我最近晚上不太想被连续追问，但明早八点提醒我交材料。”

AI 应拆成两个意图：

1. 一个短期互动边界：晚上减少追问；
2. 一个有时间条件的提醒承诺。

前者形成带 TTL 的 `BoundaryIntent`，后者形成带时间和来源的 `MemoryProposal` 或 `TaskEnvelope`。系统不能因为出现“不要”就把整条消息当成拒绝，也不能因为出现“提醒”就立即创建未确认任务。

## 四、后续两个切片

### 现实触及切片

先用模拟设备或模拟观察，不急于接入真实摄像头、位置和健康设备：

```text
Observation → 权限/TTL/来源校验 → AI Decision
→ ActionRequest → OperationResult → Memory/Affect 更新
```

阶段门槛是：没有授权不读取、过期观察不驱动行动、动作失败不写成成功事实。

### Together 切片

先实现通用会话生命周期，不先做完整通话、观影或游戏：

- 建立、恢复、暂停、取消、结束；
- 临时上下文和会话摘要；
- 断线重连与旧代作废；
- 结束后按轮次写入记忆提案；
- 实时能力缺失时回退到普通文字互动。

## 五、阶段门槛

每个切片都必须通过跨域场景，而不是以“插件代码完成”为门槛：

1. 作用域不泄漏；
2. 事实可追溯到证据；
3. 动作不会因超时或重启重复执行；
4. 旧 generation 不会写回新状态；
5. AI 可以表达未枚举的新意图；
6. 权限、预算、协议和资源安全仍由代码硬校验；
7. 同义表达、否定、引用、反话、多意图和无关键词输入不会触发错误硬拦截。

此外，每个切片必须提交 [功能覆盖与对等性清单](./FUNCTIONAL_COVERAGE_AND_PARITY.md) 中的 `CapabilityCoverage`：明确旧入口映射、保留/重新设计/暂缓/删除状态，以及缺失依赖、权限拒绝、超时、未知、取消、重载和恢复结果。没有完成能力清单时，只能进行影子运行，不能关闭旧兼容入口。

## 六、首批评估场景

- 用户纠正一条旧记忆，旧事实被保留为历史但不再作为当前事实；
- 用户在群聊中说了只属于私聊的内容，群聊上下文不得读取；
- 用户说“先别问了，明早提醒我”，不能把拒绝和提醒混成一个动作；
- 主动消息发送超时但平台可能已接收，DeliveryReceipt 为 `uncertain`，等待对账且不重复发送；
- 插件重载期间旧任务醒来，必须被 generation 作废；
- 用户没有任何后续反馈，系统不得生成“用户觉得这次主动很好”的记忆；
- 输入不含任何旧词表关键词，但语义明确表达了同一意图，AI 仍应正确处理；
- 输入含有关键词但处在引用、否定或反话中，不得直接触发旧式硬规则。

## 七、下一步

本稿对应的三份配套设计已经单独落档，作为首条切片的评审材料：

1. [记忆提议、查询与外部注入契约 v0](./MEMORY_PROPOSAL_QUERY_CONTRACT_V0.md)：能力声明、标准字段与回执、作用域、类型和策略扩展；
2. [记忆与低风险主动纵向切片状态机 v0](./MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md)：状态转移、失败、幂等和恢复；
3. [记忆与主动反例评估集 v0](./MEMORY_COUNTEREXAMPLE_EVAL_V0.md)：语义验收和 EXT-01 至 EXT-14 外部来源、查询调用方与检索提供方场景。

接入层已补充[外部插件生命周期](./COMPANION_EXTENSION_LIFECYCLE_V0.md)、[记忆参考适配器](../../astrbot_plugin_remember_you/docs/MEMORY_ADAPTER_DESIGN_V0.md)和评估集中的第三方验收包规范，均为待验证设计。

公共执行与 control profile 0.1.0 review 的 Schema 和设计夹具已形成，LC-09--LC-22 运行验收仍为 not_run。共享模式另有 `companion.sharing@1` 契约包，保留原 Memory 0.2.0 的独立协商。本轮用[世界模拟纵向切片](./DOMAIN_STATE_MACHINES_V0.md#7-世界模拟纵向切片学习被打断与跨会话恢复)检查跨 session 连续性、用户偏好、公共世界和失效传播；后续设计顺序以总纲为准。薄 SDK、真实授权和持久化运行验证留在建设路线中，当前不启动旧插件数据库和页面迁移。
