# Companion Injection Protocol

状态：草案 v0.1

本文定义外部 AstrBot 插件向陪伴运行时注入能力的统一协议。协议描述的是“能力”，不是插件之间的私有调用方式。一个插件可以注册多个能力，一个能力也可以由不同插件提供不同实现。

## 1. 设计原则

1. **能力与实现分离**：调用方只依赖能力 ID、版本和 DTO，不依赖提供方的 Python 类。
2. **读、提议、执行分离**：读取事实不会自动获得写入或设备控制权限。
3. **作用域显式**：所有请求、事件、记忆和动作都必须带 Scope。
4. **证据可追溯**：模型上下文中的事实必须标明来源、时间、新鲜度和可信度。
5. **失败可表达**：缺失、未授权、降级和执行失败必须返回结构化状态。
6. **协议可演进**：DTO 只增加可选字段；破坏性变化通过新的主版本号发布。
7. **不传递宿主对象**：协议中不得出现 AstrBot event、插件实例、请求对象或数据库连接。

## 2. 控制面与数据面

主陪伴插件拥有扩展控制面，外部插件拥有领域数据面。两者通过本协议交换轻量元数据和 DTO：

| 归属 | 负责内容 |
| --- | --- |
| 主陪伴控制面 | 发现、注册、版本协商、启停、能力状态、依赖、权限、作用域绑定、任务监督、资源预算、统一诊断和工作区展示 |
| 外部插件数据面 | 领域算法、外部连接、凭证、领域数据库、缓存、媒体处理和具体动作执行 |

主陪伴保存的是 ExtensionManifest、ExtensionStatus、能力描述和审计摘要，不保存扩展的账号密码、原始设备数据或领域数据库。扩展卸载时，控制面记录应变为 stopped/unavailable，不能继续持有失效实例。

SDK 中的 ExtensionManifest 描述扩展版本、SDK 版本、能力、依赖、权限、页面和资源预算；ExtensionStatus 描述当前生命周期、缺失依赖、各能力状态、任务数和最近错误。这些对象用于主插件的扩展管理页，不代替扩展自己的领域配置。

小型事实和结果通过 DTO 传递；图片、音频、视频、录屏和大批量日志只传递 ContentRef、SessionRef 或分页游标，不把二进制内容复制到 Kernel 内存。

## 3. 能力类型

协议的核心原语只有六类：

| kind | 含义 | 典型例子 |
| --- | --- | --- |
| observe | 读取外部事实或状态 | 健康数据、位置、天气、屏幕、设备状态 |
| enrich | 将事实转换为可理解的语义 | “刚运动完”“在公司附近”“正在写代码” |
| event | 发布状态变化或外部发生的事件 | 到家、睡眠不足、设备被关闭、直播观众加入 |
| remember | 读取、写入或整理有权限的记忆 | 运动习惯、常去地点、阅读进度、游戏经历 |
| propose | 提交主动候选或动作建议 | 关心睡眠、提醒补水、建议开灯 |
| execute | 执行具有副作用的动作 | 开灯、创建房间、发送提醒、生成图片 |

以下属于平台扩展接口，不改变上述六个原语：

| 扩展接口 | 用途 |
| --- | --- |
| temporal | 外部日历、课程表、排班和提醒 |
| interaction | 通话、观影、阅读、游戏和移动端会话 |
| ui | 陪伴工作区中的页面、卡片和设置项 |
| diagnostics | 健康状态、错误、资源使用和审计 |

例如智能家居插件可以同时提供 observe device.light、event device.light.changed 和 execute device.light.set，但这三个能力拥有独立版本和权限。

## 4. 能力描述

提供方通过 manifest 声明能力，不直接暴露对象：

~~~json
{
  "id": "device.light.set",
  "kind": "execute",
  "version": "1.0",
  "provider": "example.smart_home",
  "scopes": ["user", "conversation"],
  "permissions": ["device.light.write"],
  "input_schema": "urn:companion:schema:device-light-set:1",
  "output_schema": "urn:companion:schema:action-result:1",
  "requires": ["device.light.read@1"],
  "side_effect": "external_device",
  "confirmation": "policy_decides",
  "resource_budget": {
    "max_concurrency": 2,
    "timeout_ms": 8000,
    "rate_limit": "30/minute"
  },
  "lifecycle": "on_demand"
}
~~~

字段约定：

- id 使用小写点号命名，例如 health.activity.read、map.place.resolve。
- version 是能力契约版本，不是插件版本。
- requires 只声明能力依赖，不声明插件包名。
- side_effect 取 none、local、external_device、external_network 或 message_delivery。
- confirmation 取 never、policy_decides 或 user_required。
- lifecycle 取 always_on、on_demand 或 session_bound。
- scopes 表示能力允许被调用的作用域粒度，可取 installation、bot、account、persona、conversation、session、group 或 user；它不授予调用权限，实际授权仍由 Kernel 决定。

## 5. 作用域、证据和隐私

### 5.1 Scope

~~~json
{
  "installation_id": "astrbot-local-001",
  "bot_id": "bot-main",
  "platform": "aiocqhttp",
  "account_id": "bot-001",
  "conversation_id": "group-456",
  "session_id": "thread-20260905-01",
  "user_id": "user-123",
  "group_id": "group-456",
  "persona_id": "persona-main",
  "persona_binding_revision": 12
}
~~~

`Scope` 对象本身必须存在。字段是否为空由平台场景决定，但 `installation_id`、`bot_id`、`platform`、`account_id`、`persona_id` 和 `persona_binding_revision` 在进入 Kernel 后必须有确定值；没有会话、群或用户的系统事件使用显式 `null`，不能借用其它字段代替。

字段语义不能互相替代：`installation_id` 区分不同 AstrBot 部署，`bot_id` 区分同一部署中的逻辑 Bot，`account_id` 区分平台登录身份，`persona_id` 区分角色身份，`conversation_id` 区分聊天空间，`session_id` 区分线程、房间或临时交互。简单部署可以让部分值相同，但 DTO 和存储主键仍保留完整字段。

外部插件发出的事件、记忆、候选、上下文和动作必须携带 Kernel 已解析的完整 Scope；只有查询或绑定请求可以提交部分 `scope_selector`，由 Kernel 解析后再执行。任何只带 `user_id`、`conversation_id` 或插件自定义“当前 Bot”的调用都必须返回 `scope_required`。

人格绑定由主陪伴控制面维护 `PersonaBinding`：

~~~json
{
  "binding_id": "binding-001",
  "selector": { "bot_id": "bot-main", "conversation_id": "group-456" },
  "persona_id": "persona-main",
  "precedence": 60,
  "effective_from": "2026-09-05T00:00:00+08:00",
  "effective_until": null,
  "revision": 12,
  "source": "admin",
  "authority": "explicit"
}
~~~

默认匹配顺序为 `session` > `conversation + user` > `conversation` > `group + user` > `user` > `group` > `bot` > `installation`。同一优先级冲突时返回诊断错误，不静默选择。绑定版本变化必须递增 `persona_binding_revision`，使旧人格下尚未投递的候选、生成任务和动作令牌失效。

人格绑定只决定“Bot 是谁”，不改变目标用户的关系和权限。群聊的一次回复只能使用一个已解析人格；不同用户的关系、情绪和动机可以作为该人格对不同目标的局部投影，但不能在同一轮隐式切换人格。跨 Bot、跨人格或跨平台共享记忆必须通过显式授权的投影请求完成，不能按昵称或平台 ID 自动合并。

### 5.2 Evidence

所有进入模型上下文的外部内容都应带：

~~~json
{
  "source": "health_plugin",
  "evidence_kind": "device_observed",
  "observed_at": "2026-09-05T08:00:00+08:00",
  "expires_at": "2026-09-05T12:00:00+08:00",
  "confidence": 0.98,
  "sensitivity": "private"
}
~~~

evidence_kind 至少包括 user_stated、device_observed、external_published、derived、model_inferred 和 action_receipt。精确坐标、原始生理数据和原始媒体默认不得直接注入普通角色扮演上下文，应先通过 enrich 生成最小必要语义。

## 6. 六类 DTO

下列 JSON 仅展示各 DTO 的业务字段；示例中的 `scope` 为缩略写法，实际跨插件传输必须使用 5.1 节定义的完整已解析 Scope。

### 6.1 Observation

~~~json
{
  "type": "health.activity",
  "scope": { "user_id": "user-123", "persona_id": "default" },
  "value": { "steps": 8240, "active_minutes": 42 },
  "evidence": { "evidence_kind": "device_observed" },
  "revision": "health-20260905-001"
}
~~~

### 6.2 Event

~~~json
{
  "id": "evt-01J...",
  "type": "user.arrived.home",
  "scope": { "user_id": "user-123" },
  "occurred_at": "2026-09-05T18:20:00+08:00",
  "payload": { "place_label": "家附近" },
  "dedupe_key": "location:user-123:home:2026-09-05T18:20"
}
~~~

事件必须可去重；重复投递不得导致重复提醒或重复动作。

### 6.3 ContextContribution

observe 和 enrich 的结果只有经过 Kernel 投影后，才进入模型上下文：

~~~json
{
  "lane": "scene",
  "key": "user_activity",
  "content": "用户今天运动了约 42 分钟，可能刚结束运动。",
  "evidence": "device_observed",
  "priority": 40,
  "max_age_seconds": 1800,
  "visibility": "private"
}
~~~

lane 可取 identity、relationship、scene、memory、affect、affordance 或 safety。扩展提交事实和约束，不能直接覆盖人格核心设定。

主陪伴提供一个轻量的请求级入口：

~~~python
api.register_extension(manifest)
api.set_extension_status(status)
scope = api.get_runtime_scope(event)
api.add_context_contribution(req, contribution, event=event, source_id="my_extension")
~~~

`get_runtime_scope` 在宿主边界解析完整的安装、Bot、平台账号、会话和人格身份；扩展不应自行从 `unified_msg_origin` 拼接主键。`add_context_contribution` 只把一条带证据的内容暂存到当前请求的 typed prompt plan，最终的权限、合并、投递和审计仍由主陪伴负责。请求之外的贡献必须携带完整 `RuntimeScope`，只有完整身份的旧 `Scope` 才允许由兼容层升级；缺少作用域、作用域不匹配或人格绑定版本过期时返回失败并记录诊断。

能力状态和扩展元数据可通过主陪伴的控制面查看：`GET /astrbot_plugin_private_companion/page/extensions/status`。控制面只保存 manifest、状态、能力描述和审计摘要；凭证、原始观测和业务数据库仍归提供方所有。

### 6.4 MemoryMutation

记忆写入必须包含 namespace、scope、source、retention 和幂等键。普通对话、设备观测和模型推测不能使用同一种记忆等级。

### 6.5 ProactiveCandidate

~~~json
{
  "id": "candidate-health-check-001",
  "trigger": "health.sleep_insufficient",
  "scope": { "user_id": "user-123" },
  "intent": "关心用户今天的精力状态",
  "context_keys": ["health.sleep", "calendar.next_window"],
  "expires_at": "2026-09-05T10:00:00+08:00",
  "cooldown_key": "health-check:user-123",
  "requires_confirmation": false
}
~~~

候选只是建议。是否发言、何时发言、用什么语气，由主陪伴的主动策略决定。

### 6.6 ActionRequest / ActionResult

~~~json
{
  "action": "device.light.set",
  "scope": { "user_id": "user-123" },
  "arguments": { "device_alias": "卧室灯", "power": "on" },
  "idempotency_key": "light:user-123:bedroom:on:20260905",
  "requested_by": "llm",
  "trace_id": "trace-001"
}
~~~

结果必须说明真实状态：

~~~json
{
  "status": "succeeded",
  "action": "device.light.set",
  "receipt": { "device_alias": "卧室灯", "power": "on" },
  "completed_at": "2026-09-05T21:30:02+08:00"
}
~~~

状态统一为 succeeded、rejected、permission_denied、unavailable、timeout、failed 和 cancelled。

## 7. 注册和生命周期

SDK 提供方接口建议保持很小：

~~~python
class CompanionExtension(Protocol):
    def manifest(self) -> ExtensionManifest: ...
    async def setup(self, ctx: ExtensionContext) -> None: ...
    async def start(self) -> None: ...
    async def stop(self, reason: str) -> None: ...

class ExtensionContext(Protocol):
    observations: ObservationRegistry
    events: EventBus
    prompts: ContextRegistry
    memories: MemoryGateway
    actions: ActionRegistry
    proactive: ProactiveRegistry
    calendar: CalendarGateway
    sessions: InteractionGateway
    tasks: TaskSupervisor
    diagnostics: DiagnosticsGateway
~~~

生命周期为：

~~~text
discovered -> validated -> bound -> ready
                              |       |
                         degraded   stopped
~~~

setup 阶段不得启动永久任务或访问未授权数据。长连接、轮询和媒体任务必须通过 ctx.tasks 创建，并在能力撤销、插件重载或作用域结束时取消。

## 8. 主动行为接入规则

主动扩展只能注册三类内容：

1. EventSource：报告事件；
2. CandidateProvider：根据事件生成候选；
3. ActionProvider：执行候选最终选择的动作。

统一流程为：

~~~text
事件/时间窗口
  -> 候选生成
  -> 作用域、权限、免打扰和额度检查
  -> 模型选择发言、沉默或动作
  -> 幂等执行
  -> 回执和审计
  -> 冷却与后续事件
~~~

扩展不得直接调用发送接口绕过 Kernel，也不得自行维护面向用户的永久主动循环。

### 8.1 主动来源和 Signal

来源只负责报告事实、状态变化或用户明确指令，不能直接发送消息。来源可以是 calendar、life_state、user_interaction、external_observation、memory_relationship、goal_project、extension_content、persona_internal、system_lifecycle 或 user_directive。

来源声明为 push、pull 或 derived：

- push：事件发生时上报；
- pull：Kernel 按需或按时间窗口读取；
- derived：Kernel 从既有信号计算，不得冒充原始观测。

Signal 至少包含：

~~~text
signal_id、signal_type、source_id、scope
occurred_at、observed_at、valid_from、valid_until
payload/resource_ref、evidence、confidence、sensitivity
dedupe_key、revision、trace_id
~~~

occurred_at 是事情发生时间，observed_at 是系统发现时间；valid_until 到期后只能用于诊断，不能继续生成主动机会。信号进入 Kernel 后必须经过格式、作用域、权限、去重和时效校验。

SignalSourceDescriptor 需要声明信号类型、模式、最低证据、新鲜度、权限、可靠性、最大频率、资源成本和失败策略。来源不可用、数据过期或权限撤销时，信号状态为 unknown/unavailable。

### 8.2 主动类型描述

主动类型通过 ProactiveTypeDescriptor 注册策略，而不是注册一条独立消息链。描述至少包含：

~~~text
id
trigger_kind
evidence_requirements
urgency
expires_after
interrupt_policy
merge_group
cooldown_policy
generation_policy
fallback_policy
resource_budget
~~~

调度器提供 alert、normal 和 ambient 三条优先级车道。类型策略可以决定候选如何产生、何时失效和如何表达，但不能绕过 Kernel 的作用域、权限、日历、额度、去重、投递和审计。

候选只能先生成 IntentPlan，再生成正文或动作。IntentPlan 至少包含 purpose、emotional_stance、conversation_load、continuity_anchor、evidence_refs、forbidden_claims 和 preferred_modality。模型不能改变候选优先级、证据等级或投递状态。

### 8.3 合并、抢占和确认

多个候选应先按 merge_group、scope、时间窗口和 continuity_anchor 合并。高优先级候选可以在授权范围内抢占低优先级候选；低价值候选必须允许沉默、延后或过期。用户新消息、日历进入 quiet 时间段、候选失效或扩展卸载时，未投递候选应取消。

涉及设备、第三方服务、付费调用、现实承诺或隐私数据的主动类型必须声明 confirmation 策略。模型只能提交动作提议，实际执行和成功描述以 ActionResult 为准。

### 8.4 InteractionOpportunity

主动候选进入 Kernel 后统一转化为 InteractionOpportunity。机会可以产生消息、媒体、动作、持续会话，也可以正常结束为沉默、合并、延后、抑制或过期。

机会至少携带：

~~~text
opportunity_id
scope
value_class
purpose
evidence_refs
thread_id
parent_opportunity_id
merge_group
expires_at
dedupe_key
interrupt_cost
expected_response
~~~

Kernel 维护 InteractionLedger，记录最近自然互动、主动联系、用户回应、取消原因、接触负担和已被当前对话吸收的事件。用户新消息应优先合并或取消同主题机会；连续互动使用同一 thread，避免多个扩展分别向用户发出重复通知。

机会调度先执行硬门槛，再做软判断。硬门槛包括过期、权限、Scope、quiet 时间段、平台能力、预算和冲突；软判断包括相关性、新颖性、连续性价值、延迟损失、打断成本和近期接触负担。没有合适机会时，silence 是成功结果。

## 9. 角色扮演接入规则

扩展对角色扮演的贡献统一进入 ContextContribution，不得提交任意 system prompt。Kernel 按 lane、优先级、证据和预算合并上下文，并处理冲突和过期内容。

模型输出应分为：

~~~text
user_visible_text
intent_proposals[]
action_requests[]
~~~

这样健康插件可以提供“用户可能刚运动完”，智能家居插件可以提供“卧室灯当前关闭”，主陪伴再决定是否自然地关心用户或提出开灯动作。

### 9.1 时间上下文的来源分级

角色扮演上下文中的时间信息必须区分四种来源：

| 来源 | 语义 | 可否直接断言已经发生 |
| --- | --- | --- |
| CalendarCommitment | 用户或可信外部来源确认的现实承诺 | 可以断言安排存在，不能断言已完成 |
| Routine / Goal | 长期习惯、目标或生活阶段意图 | 只能表达为倾向或计划 |
| ActivityEpisode | 运行时实际活动及其回执 | 可以在证据有效期内表达当前/近期状态 |
| ScheduleProjection | 根据以上对象生成的短期预测 | 只能表达为“可能”“预计” |

扩展不应提交完整日程文本给模型，而应提交结构化事件、活动状态或 ContextContribution。主陪伴只向模型投影当前活动、近期经历、下一项高置信意图和可用时间，保留空闲与未知状态。

### 9.2 TemporalDecisionContext 和双向控制

Kernel 为每轮对话、主动候选和动作请求生成 TemporalDecisionContext。它只包含当前决策所需的时间段：

~~~text
current_segments[]
next_transitions[]
fixed_commitments[]
available_windows[]
conflicts[]
deadlines[]
interrupt_policy
evidence_refs[]
~~~

时间段允许重叠，类型包括 commitment、activity、availability、quiet、transition、deadline、buffer 和 unknown。每个时间段必须带起止时间、时区、状态、优先级、可打断性、来源和确定性。

日历向 Bot 的输入分为 context、preference、constraint 和 gate；Bot 向日历的输出分为 observe、suggest、reserve 和 commit。observe/suggest 不改变现实承诺，reserve 必须有过期时间，commit 必须经过明确授权或用户确认。

时间段变更只触发受影响子树的局部重算。已完成和已确认记录保持不变；自动调整需要携带原版本、新版本、原因、证据、授权来源和回滚信息。日历写入产生的事件必须带来源和幂等键，避免“回写 -> 事件 -> 再回写”的循环。

### 9.3 CalendarCommand 和 Bot 写入权限

Bot 修改日历必须提交结构化 CalendarCommand，不能直接写数据库或用自然语言表达“已经改好了”。命令至少包含：

~~~text
target_id、calendar_kind、operation、interval_patch、reason、evidence_refs
scope、expected_revision、authorization、idempotency_key
~~~

operation 取 create、move、resize、split、merge、postpone、cancel、complete、reserve、lock 和 unlock。Kernel 先校验作用域、权限、版本和冲突，再生成 CalendarChangeSet；自动提交或用户确认后才产生新 revision。提交成功后发布 calendar.changed，失败则返回冲突、未授权、过期或不可用状态。

persona_calendar 和 user_calendar 必须分开。Bot 可以在授权范围内自动调整自己的模拟生活和软活动；用户现实日历中的会议、课程、预约、交通、提醒和第三方同步默认需要确认。跨越安静时段、改变固定承诺、触发设备或付费动作时，不能使用普通自动调整权限。

允许自动提交的范围应由策略明确列出，例如移动弹性活动、缩短低优先级任务、插入共同活动、释放过期预留和记录实际完成。每个自动提交都必须保留旧 revision、ChangeSet、授权来源和回滚入口。Bot 只能依据 ActionResult 或 CalendarChangeSet 的成功状态向用户描述结果。

## 10. 诊断注入

诊断是控制面能力，不应退化为说明书搜索。扩展可以注册只读 DiagnosticCheck，主陪伴负责调度、权限、超时、缓存、脱敏和报告聚合。

~~~python
class DiagnosticCheck(Protocol):
    def descriptor(self) -> DiagnosticDescriptor: ...
    async def run(self, request: DiagnosticRequest) -> DiagnosticResult: ...
~~~

DiagnosticDescriptor 至少声明：

~~~text
id、version、scope、required_permissions、cost、timeout、side_effect=none
~~~

DiagnosticResult 至少包含：

~~~text
status、summary、evidence_refs、started_at、finished_at、duration_ms、redacted_details
~~~

status 取 pass、warn、fail、unknown 或 not_applicable。检查结果必须标明检查版本和执行时间；没有实际执行结果时，模型不得声称“已经检查”。

答疑分为 explain、self_check、incident 和 repair_proposal 四种模式。repair_proposal 只能产生待确认的变更计划，不能在诊断链路中直接修改配置、记忆或设备。

### 10.1 分级检查和按需升级

检查按成本和证据强度分为四级：

| level | 数据来源 | 调度规则 |
| --- | --- | --- |
| L0 | manifest、Schema、静态依赖和能力状态 | 默认执行，目标是毫秒级 |
| L1 | 任务、缓存、最近错误、投递回执和本地状态 | 与 L0 并行，使用短期缓存 |
| L2 | 指定 Scope 和时间窗口的事件/审计时间线 | 仅在问题涉及具体事件时执行 |
| L3 | 网络、设备、Provider 或可复现探测 | 证据不足且已授权时执行，必须可取消 |

每个检查在 descriptor 中声明 level、cost、timeout、freshness、preconditions、observes 和可复现性。调度器先按问题类型选择最小检查集合；只有结果为 unknown、存在冲突或用户明确要求复现时才升级。

### 10.2 证据和结论

检查结果不得只返回自然语言摘要。Finding 至少包含：

~~~text
id、severity、statement、supporting_evidence_refs、contradicting_evidence_refs、confidence
~~~

confidence 由 Kernel 根据证据完整度、新鲜度、独立来源数量、冲突数量和复现结果计算。模型只能解释 Finding，不能修改其置信度或补造证据。

证据的有效期、作用域和脱敏级别必须在聚合时再次校验。无法执行检查、数据过期或不同来源冲突时使用 unknown，并在答疑中明确说明未决项。

## 11. 版本和兼容性

- 能力版本使用 major.minor；同一主版本只允许向后兼容的字段增加。
- 提供方可以同时注册多个主版本。
- Kernel 启动时完成能力协商，不满足 requires 时标记 unavailable。
- 旧插件通过 Compatibility Adapter 转换到本协议；适配器不得暴露私有字段。
- DTO 必须可 JSON 序列化，时间使用带时区的 ISO 8601，枚举使用稳定字符串。
- 所有跨插件调用必须携带 trace_id、scope 和 capability_version。

## 12. 首期实现范围

第一版 SDK 只实现：

1. Scope、CapabilityDescriptor、Observation、Event、ContextContribution、ActionRequest、ActionResult；
2. 能力注册、版本协商、生命周期和 unavailable/degraded 状态；
3. TaskSupervisor、事件去重和动作幂等；
4. DiagnosticDescriptor、DiagnosticRequest、DiagnosticResult 和 Finding 的最小结构；
5. L0/L1 只读诊断检查和短期缓存；
6. 只读的健康/位置观察样例；
7. 一个需要确认的设备动作样例；
8. 主动候选、角色扮演上下文和诊断证据的契约测试。

记忆、日历、页面和实时会话可以先使用兼容适配器接入，待核心 DTO 稳定后再迁移其内部实现。
