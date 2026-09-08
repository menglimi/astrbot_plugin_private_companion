# Companion Injection Protocol

> 导航：[设计总纲](./FRAMEWORK_DESIGN.md) / [主题目录](./FRAMEWORK_DESIGN_INDEX.md)。定位：公共协议草案；维护对外字段、能力与结果，平台身份依赖可移植设计。

状态：草案 v0.2（2026-09-06 设计审查修订，尚未作为 SDK 新版本发布）

公共身份与首条记忆切片已有[机器契约包 0.2.0 review](./contracts/v1/README.md)，公共执行已有[独立契约包 0.1.0 review](./contracts/execution/v1/README.md)，用于精确格式和兼容样例验证。2026-09-08 补充的能力描述与控制面字段仍是待转 Schema 的设计，拟议 profile 为 `companion.control@1`，不表示已发布第三个契约包。现有 Python PROTOCOL_VERSION=0.1 保持原语义。RuntimeScope 的新字段不得直接交给会忽略未知字段的旧 from_dict 构造器。

本文定义目标协议，现有 `companion/injection.py` 仅实现其中的兼容子集。新增字段、枚举和作用域语义须通过能力版本协商后实施，不能仅凭本文修改就视为当前运行时支持。架构所有权、投递与迁移语义分别见 [架构基线](./ARCHITECTURE_RESET.md) 3.2.2、3.12.4 和 4.1。

平台、宿主和打包模式的可替换边界见[平台可移植与一站式打包设计 v0](./PLATFORM_PORTABILITY_AND_BUNDLING_V0.md)。本文定义能力和 DTO，不能把 AstrBot、OneBot 或某个平台的原生对象当作跨平台协议。

本文定义外部扩展向陪伴运行时注入能力的平台中立协议，适用于宿主插件、独立服务和嵌入式应用。AstrBot 是首个宿主适配器。协议描述的是“能力”，一个扩展可以注册多个能力，一个能力也可以由不同扩展提供不同实现。

记忆、创作、生图、现实、屏幕、一起、游戏、直播和问题治理等原生联动插件从首期参与共同设计，实际适配按[总纲](./FRAMEWORK_DESIGN.md)的切片验证。原生插件提供生产方、消费方和故障样本，第三方扩展复用相同边界；原生身份不赋予额外权限，也不要求用户全部安装。覆盖范围与联动场景见 [架构基线](./ARCHITECTURE_RESET.md) 3.1.1、5.2。

## 1. 设计原则

1. **能力与实现分离**：调用方只依赖能力 ID、版本和 DTO，不依赖提供方的 Python 类。
2. **读、提议、执行分离**：读取事实不会自动获得写入或设备控制权限。
3. **作用域显式**：所有请求、事件、记忆和动作都必须带 Scope。
4. **证据可追溯**：模型上下文中的事实必须标明来源、时间、新鲜度和可信度。
5. **失败可表达**：缺失、未授权、降级和执行失败必须返回结构化状态。
6. **协议可演进**：DTO 只增加可选字段；破坏性变化通过新的主版本号发布。
7. **不传递宿主对象**：跨插件领域 DTO 不得出现 AstrBot event、插件实例、请求对象或数据库连接；6.3 的宿主入口负责把原生对象转换为协议数据。

## 2. 控制面与数据面

主陪伴插件拥有扩展控制面，外部插件拥有领域数据面。两者通过本协议交换轻量元数据和 DTO：

| 归属 | 负责内容 |
| --- | --- |
| 主陪伴控制面 | 发现、注册、版本协商、启停、能力状态、依赖、权限、作用域绑定、任务监督、资源预算、统一诊断和工作区展示 |
| 外部插件数据面 | 领域算法、外部连接、凭证、领域数据库、缓存、媒体处理和具体动作执行 |

就扩展控制面而言，主陪伴保存 ExtensionManifest、ExtensionStatus、能力描述和审计摘要，不保存扩展的账号密码、原始设备数据或领域数据库；陪伴自身状态仍由其领域服务保存。扩展卸载时，控制面记录变为 stopped/unavailable，不能继续持有失效实例。

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

提供方通过 manifest 声明能力，不直接暴露对象。下例保留为早期概念说明；新控制 profile 的字段以 §4.1 为准，不能将本例直接当作注册输入：

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

manifest 的 confirmation 不能降低 Kernel 的授权要求。已授予且覆盖目标、操作、参数边界与有效期的授权可以复用，只有授权缺失或变化超出范围时才请求确认。确认记录绑定参数摘要、作用域、revision 和过期时间；副作用开始前再校验，等待确认期间变更参数不能沿用旧批准。

同一能力存在多个提供方时按显式绑定及兼容版本选择，不以最后注册者覆盖。只读失败可在兼容语义下回退；已经提交且结果不确定的副作用不得换提供方重试。依赖循环或缺失只使相关能力不可用，不阻塞整个插件或宿主。

### 4.1 CapabilityDescriptor 字段设计

本节维护提供方的静态声明；控制面另行签发注册和绑定投影。拟议 `schema_version=companion.capability.v1`，尚无对应机器 Schema。除明确允许 null 的字段外，下面的字段均必填；空数组表示没有该项，不能将缺失字段解释成无限额度、默认授权或自动恢复。

| 字段 | 类型与取值 | 责任与约束 |
| --- | --- | --- |
| schema_version | 固定 `companion.capability.v1` | 控制 profile 内的描述符格式，不是业务能力版本 |
| id / kind / version | namespaced ID / 六类 kind / `major.minor` | 主次版本按整数比较，不用浮点；kind 不用于推断权限或副作用 |
| provider_id | 公共 token | 来自可信宿主/服务身份的关联；统一使用 provider_id，旧示例的 provider 只在显式兼容 adapter 内转换 |
| scopes / permissions | 去重的作用域枚举数组 / 权限 ID 数组 | 仅声明可接受粒度与所需权限；不含具体 owner 或授权票据 |
| input_schema / output_schema | 已登记的完整 DTO Schema URN | 锁定本地 schema 指纹；业务请求与控制请求分开 |
| payload_schema / output_payload_schema | 已登记 Schema URN 或 null | 公共执行封套必须绑定领域 payload；自身已完整描述 payload 的 memory.*.v1 等可用 null |
| features | 去重的版本化 feature ID 数组 | 精确声明支持的语义；required_features 必须是其子集，不能通过忽略未知字段兼容 |
| requires | DependencyRequirement 数组 | 必需及可选依赖均按能力声明，不写插件包名或任意下载地址 |
| side_effect | none / local / external_device / external_network / message_delivery | 描述单个操作的主要提交边界；复合流程拆步骤，各步独立授权和记账 |
| confirmation / lifecycle | 沿用本节已有枚举 | 声明不能降低当前策略的确认要求；always_on 不授予无主后台循环 |
| completion | inline / deferred / either | deferred/either 必须可查询原操作；pending 必须有持久定位依据 |
| idempotency | `{mode, retention_ms}` | mode 为 none / owner_ledger / remote_guaranteed；none 时 retention_ms=0，其余为正整数 |
| controls | `{operation_lookup, cancel, session_resume}` | lookup 为 none / by_id / by_id_or_key；cancel 为 none / before_commit / cooperative；resume 为 none / checkpoint |
| limits | 本节的有限资源上限对象 | 单位、零值和协商规则明确，不沿用字符串 rate_limit |
| extensions | 公共命名空间扩展对象 | 非必需扩展不能覆盖核心字段；必需扩展语义必须在 features 中协商 |

`DependencyRequirement` 使用 `{capability_id, major, min_minor, required_features, optional}`：major 为正整数，min_minor 为非负整数，required_features 为去重数组，optional 为布尔值。不提供自由文本版本表达式。调用方、提供方和已登记 schema 必须有精确兼容交集，bind 返回一个确切的版本；主版本相同不代表任何次版本都可消费。当前 execution 0.1.0 Schema 的 capability_version 只允许 1.0，不能仅因描述符声明 1.1 就把 1.1 派发给该格式。必需依赖循环使相关能力 unavailable；可选边不触发自动递归加载，缺失可选能力时不得继续声明依赖它的有效 feature。

`limits` 的首批字段均为整数：`max_input_bytes`、`max_output_bytes`、`max_inflight_bytes`、`max_concurrency`、`max_duration_ms` 为正数，`max_queue_items`、`max_queue_bytes` 为非负数；队列两项同时为零表示拒绝排队。`rate_limit` 是 `{max_calls, window_ms}`，两项为正整数，首版按绑定 caller + owner + 能力主版本的滚动窗口计量，换句柄或重试不清零；provider/runtime 另有聚合准入上限。数值是可配置的部署边界，本文不提供未经目标环境测量的生产默认值。

max_input_bytes/max_output_bytes 按 UTF-8 wire 字节计；max_inflight_bytes 覆盖已解码对象、并发页、序列化缓冲及暂存引用内容的保守内存预留，不能仅统计最后输出大小。`limits` 表示提供方接受的最大值，实际生效值还受 runtime、安装、owner、caller、父任务剩余额度约束；绑定获得上限不等于提前领取一份可复制预算。描述符缺少字段或无法形成有限有效值时不能进入 ready。

幂等命名空间固定包含稳定 caller、owner 和能力主版本，键不包含 request_id、provider_generation、binding_revision。owner_ledger 只保证本领域账本去重，不能证明远端非幂等发送不会重复；remote_guaranteed 还需原远端目标、相同参数摘要和有效远端保留窗的证据。超过保留窗后不把原未知操作当新操作。所有 side_effect 非 none 的能力至少支持 owner_ledger 与 by_id_or_key，先持久登记幂等键和操作，再开始效果；外部平台不支持查单时，本地 lookup 可以一直报告 unknown，不能伪造远端保证。

controls 声明可挂接的标准控制 handler 及其保证，不是额外的业务授权。注册时核对 handler 映射与声明一致，none 表示对应能力不支持该操作。Memory 现有 `memory.operation.get` 可由经过协商的 adapter 映射到 operation.lookup，保留原协议及授权；控制面不自行读取 Memory 数据库，也不把它作为所有领域的查账服务。查询/取消 handler 不递归要求自己再声明一组查账/取消能力。

### 4.2 声明、就绪与绑定投影

以下是控制面签发的字段，不接受提供方在 CapabilityDescriptor 中自报：

| 投影 | 签发内容 | 有效边界 |
| --- | --- | --- |
| RegistrationHandle | registration_ref、provider_id、provider_generation、manifest_digest、registration_revision、expires_at | 绑定可信安装/宿主身份；有界租约，重复续租不创建新代 |
| CapabilityAvailability | capability_id/version、provider_generation、descriptor_revision、state、admission、effective_features、reason_code、observed_at | 动态快照；声明的 feature 与当前可用 feature 分开，probe 不替代 invoke 时检查 |
| BindingHandle | binding_ref、caller_ref、owner_ref、精确能力/提供方/版本、provider_generation、descriptor_revision、binding_revision、authorization_revision、scope_ref、scope_fingerprint、purpose、features、schema_set_digest、effective_limits、expires_at | Runtime 签发的不透明句柄及获授权投影；真正比较值在控制面，调用方复制字段不能制造权限 |

caller_ref 是稳定调用主体，runtime 连接/实例另行鉴别；owner_ref 是该能力业务归属，由可信解析器选定，不能从 provider_id 或一个聊天 session 推算。scope_ref 指向当前已解析的 RuntimeScope 或安装/来源控制 scope，scope_fingerprint 覆盖解析结果与适用授权边界；不能为注册或安装级能力虚构用户和人格。schema_set_digest 覆盖完整输入、输出和领域 payload 的已登记 Schema。

describe/discover 只返回获授权的投影，不保证可 bind；bind 也不保证将来永远可 invoke。handler、授权或 descriptor 变化由 revision 和准入状态阻止旧调用继续派发。控制面签发的字段不塞入已有封闭 Action/Memory Schema；公共执行请求的 binding_ref 由 Runtime 查到这些值并核验已有 scope/provider 字段。

### 4.3 控制请求与结果边界

拟议控制 profile `companion.control@1` 与 `companion.execution@1` 独立协商；未协商时返回 unsupported_feature，不能落到旧 metadata facade 后返回新语义的成功。控制 DTO 只包含 JSON 数据，本地 handler/认证上下文通过宿主端口注入，远程连接经可信身份映射到同一主体。

控制请求字段收敛为 `schema_version, request_id, trace_id, method, deadline_at, budget, idempotency_key, params`，拟议版本 companion.control-request.v1。method 只选择已登记操作，params 按方法封闭；不使用任意属性路径修改 Task/Session。budget 复用有限公共调用预算，控制面的输入/输出/并发上限另由已协商 profile 和安装额度限制。身份从可信调用上下文取得，不接受 JSON 内自报 caller 代替认证。

结果字段收敛为 `schema_version, request_id, trace_id, method, status, reason_code, output, observed_at`，拟议版本 companion.control-result.v1。status 复用公共结果枚举，output 使用对应方法的封闭结构；失败时仅返回获授权的最小状态。它表示这次控制请求的结果：cancel 的 succeeded 只表示取消意图已登记，operation.lookup 的 succeeded 可以查询到原操作 uncertain。原业务结果保留为独立记录/引用，不把控制成功改写成业务成功或消息送达。控制调用超时也不能推断 cancel/resume 没被受理。

改变状态的 register/bind/release/cancel/session.resume 必须带稳定控制幂等键，discover/operation.lookup 使用 null。同一主体、方法、逻辑目标和键绑定语义参数摘要；重试可以更换 request_id/trace_id/deadline，不能改目标、取消范围、原操作定位或恢复 checkpoint。新的有效认证/绑定引用可用于同一逻辑重试，但 Runtime 必须证明其仍解析到同一 caller/owner/目标，不能用旧授权回放敏感结果。重复控制请求先验当前权限，再读原回执；revision 冲突不产生隐含状态修改。每种方法的字段、签发责任与场景见[生命周期 §4](./COMPANION_EXTENSION_LIFECYCLE_V0.md#4-控制面操作语义)。

控制 profile 协商必须公布有限的控制重试窗和回执保留窗，后者覆盖前者及在途收束时间；它们与业务描述符的 idempotency.retention_ms 分开。只有窗口内承诺相同键返回原控制回执。窗口外调用方停止自动重放，重新读取目标状态/原 owner 账本，再决定是否建立新的逻辑请求；不能把已回收的控制记录当作原业务未执行证明。未决业务的最小持久对账记录仍由 owner 保留，不随控制缓存过期删除。

## 5. 作用域、证据和隐私

### 5.1 Scope

~~~json
{
  "ecosystem_id": "ecosystem-001",
  "installation_id": "installation-001",
  "runtime_instance_id": "runtime-001",
  "host_kind": "astrbot",
  "bot_id": "bot-main",
  "platform": "aiocqhttp",
  "account_id": "bot-001",
  "conversation_ref": "conversation-456",
  "platform_conversation_id": "group-456",
  "conversation_id": "group-456",
  "session_id": "thread-20260905-01",
  "user_id": "user-123",
  "group_id": "group-456",
  "persona_id": "persona-main",
  "persona_binding_revision": 12
}
~~~

面向对话/主动/动作的调用使用已解析 `RuntimeScope`，其中逻辑安装、Bot、人格及绑定版本必须确定；对外投递还需解析平台、账号和真实目标。纯内部调用不虚构平台账号，没有群或线程时保留明确的空值语义。现有 v0.1 Python DTO 使用空字符串，v0.2 传输草案允许 `null`，需在版本适配处显式转换，不能直接把新示例当作旧构造器输入。各能力所需字段及空值条件在 schema 阶段定型。

`host_kind` 表示宿主实现，例如 `astrbot`；`plugin_hosted`、`standalone_service`、`embedded_app`、`split_services` 表示部署模式，由装配配置声明，不充当宿主类型或领域 owner。

### 5.1.1 SharingMode：会话、用户与全局

所有需要保存或投影状态的能力都必须声明并协商共享语义 `sharing_mode`，取值为 `session`、`user` 或 `global`。这是共享范围，不是授权结果；具体调用仍以完整 `RuntimeScope` 和当前 ACL 为准。这里的“声明”不表示向已有封闭 CapabilityDescriptor 增加同名顶层字段。

```text
session -> installation + bot + platform/account + persona + conversation_ref + session_id
user    -> installation + bot + platform/account + user_id + persona
global  -> 显式的 installation/bot/persona 全局分区
```

`session` 按实际会话隔离；`user` 允许同一平台账号下的同一用户跨私聊和群聊共享用户级状态；`global` 只共享经过定义的公共状态。模式所需的平台、账号、用户或 session 标识不完整时不能猜测或扩大范围。`global` 不默认跨 Bot、人格或安装；同一公共分区可显式授权多个平台入口，逐入口校验访问与投递目标，不因此合并用户身份。用户的跨平台关联仍须经过 IdentityBinding/Projection。

能力描述中的 `scopes` 表示可以接受的调用粒度，`sharing_mode` 表示该能力产生或读取的状态归属；二者不能互相替代。Memory 的 `retention=session|short|durable|protected` 表示保留策略，也不能替代 `sharing_mode`。例如：一次会话摘要可以是 `sharing_mode=session + retention=session`，用户确认的口味可以是 `sharing_mode=user + retention=durable`，人格的公共房间资源可以是 `sharing_mode=global + retention=durable`。

上下文编排允许按用途组合 `session`、`user` 和授权的 `global` 投影，但必须分别记录来源、anchor、visibility、purpose、revision、TTL 和预算。模型不能把 session 事实自动升级为 user/global 事实；升级通过带证据的 `StateTransitionProposal` 或 `MemoryProposal` 完成，并由 owner 检查授权和共享策略。群聊的成员发言、群主题和第三方信息不能因为存在 `user_id` 就进入该用户的跨会话档案。

#### 5.1.2 Global 连续性与入口统一

`global` 只能解决公共人格/世界状态的归属，不能单独解决私聊和群聊的入口分叉。新框架要求所有入口先经过同一个 `PlatformAdapter.normalize -> IdentityResolver -> EventLedger`，再由同一个 `RoleDecisionGate` 组合四类投影：

```text
GlobalContinuityState   # 人格公共生活、世界水位、公开可见的长期变化
UserRelationshipState   # 当前用户的关系、偏好和获授权的跨会话事实
SessionSceneState       # 本窗口/群会话的上下文、占用、近期话题和临时状态
AudienceExpressionPolicy # 私聊、群聊和多人的可见性、距离、长度和泄漏约束
```

群聊的唤醒、读空气、防刷屏和连续对话判断只能输出 `SceneGate` 或事件信号，不能因为入口是群聊就提前结束人格、关系和决策流水线。没有回复资格时，统一决策链返回 `silence`；有回复资格时仍从同一个人格快照、同一份全局连续性和同一条目标关系投影生成 `ResponsePlan`。群聊只改变受众策略和会话场景，不改变人格 owner，也不复制私聊正文。

上下文采用有界三层组合：先取当前受众允许的 global 安全投影，再取已解析用户的 user 投影，最后取当前 session 的近期场景。每一层保留 `source/anchor/visibility/purpose/revision/TTL`，合并由 Kernel 去重和裁剪；群成员观察、群主题和第三方消息默认停留在 session 或群场景，只有带证据的提议被 owner 接受后才可转成 user/global 状态。global 事件可以影响角色公共情绪、活动和世界进度，但必须去除私聊主体、原文和用户专属证据。

平台原生历史是一个可替换的 `ContextContribution` 来源，不能被某个群聊分支静默清空或替换成第二套历史。每轮只选择一个 `history_owner`，将宿主历史、规范事件和角色连续性摘要按事件 ID/修订去重后编译；缺失、过期或冲突的来源降级为局部缺失，不重建一套平行对话。

人格选择以已解析的 installation/Bot/persona 绑定为准，不能以私聊或群聊的 UMO 直接决定另一套人格。不同入口可以有不同 `conversation_ref` 和 `session_id`，但在同一 global 分区下使用同一个 persona owner；目标用户关系仍由 user projection 单独解析，多人场景只投影当前受众获准看到的关系强度。

这组不变量分别解决入口流程分叉、私聊专属人格层、群聊过度降噪、两套记忆、原生历史替换和多人格漂移：入口差异只存在于 `SceneGate` 与 `AudienceExpressionPolicy`；人格、关系、情绪、记忆和决策均由同一 owner 计算；任何私聊专属内容若没有 user/global 授权就不会进入群聊；所有 global 写入都必须是公共安全投影而不是私聊事实。

共享字段的精确位置见[共享契约包](./contracts/sharing/v1/README.md)。`companion.sharing@1` 在已有 Memory 请求的 `payload.extensions["companion.sharing"].payload` 中携带 `sharing_mode`、`sharing_anchor`、`sharing_policy_revision`，请求封套通过 `required_features` 协商；召回项在自己的 extensions 中保留相同结构。本包新定义的 ContextContribution/StateTransitionProposal 使用顶层三字段。旧 Memory、控制描述符和订阅 Schema 不直接加字段；不支持必需 profile 时拒绝调用，不能静默降级为其他共享模式。

字段语义不能互相替代：`ecosystem_id`/`installation_id` 表示逻辑数据边界，`runtime_instance_id`/`host_kind` 表示当前宿主；`bot_id` 区分逻辑 Bot，`account_id` 区分平台登录身份，`persona_id` 区分角色，`conversation_ref` 表示规范化聊天空间，`platform_conversation_id` 和兼容 `conversation_id` 只由平台适配器拥有，`session_id` 区分线程或房间。DTO 保留调用上下文；持久数据使用领域稳定 `owner_key`，不将宿主实例、绑定 revision 或临时 session 一律放入主键。迁移映射和打包模式见平台可移植设计。

对话贡献、记忆、候选和动作必须携带 Kernel 已解析的 RuntimeScope；查询或绑定请求可提交 `scope_selector`，由 Kernel 解析。安装级注册、账号连接状态及尚未绑定接收人的设备观测使用声明的 control/source scope，不能伪造人格或用户。Kernel 按已授权订阅为各消费方解析 RuntimeScope；仅带裸用户 ID 且无法确定命名空间的调用返回 `scope_required` 或 `scope_unresolved`。

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

绑定先按安装/Bot 筛选，在平台/账号命名空间内匹配。私聊顺序为 `session` > `conversation + user` > `conversation` > `user` > `bot` > `installation`；群聊为 `session` > `conversation/group` > `bot` > `installation`，普通群消息不应用发言人的私聊绑定。`precedence` 仅解决同层显式优先级，完全同级冲突返回诊断错误。绑定版本变化使受影响绑定下尚未提交的动作失效；已提交动作保留对账，长期状态不重置。

人格绑定只决定“Bot 是谁”，不改变目标用户的关系和权限。群聊的一次回复只能使用一个已解析人格；不同用户的关系、情绪和动机可以作为该人格对不同目标的局部投影，但不能在同一轮隐式切换人格。跨 Bot、跨人格或跨平台共享记忆必须通过显式授权的投影请求完成，不能按昵称或平台 ID 自动合并。

#### 5.1.2.1 HDSI 窗口连续性与多人格

HDSI 的“持续性生活剧本”把聊天窗口视为角色手中的不同通信窗口，而不是不同的 Bot 实例。`global` 分区只建立一个连续的 `GlobalActorRuntime`：

```text
GlobalActorRuntime {
  actor_id / persona_id / global_revision
  continuity_state_ref       # 公共生活、当前活动、注意力和全局情绪投影
  active_window_ref          # 当前注意力，不是人格或状态分区
  pending_window_refs[]      # 有界待处理窗口引用
}

ConversationWindow {
  window_ref / conversation_ref / session_id
  audience / target_subject_ref
  window_revision / occupancy / last_event_ref
  context_checkpoint_ref     # 冷窗口仅保留有期限引用
}
```

因此，群聊与私聊共享同一个 `actor_id`、`persona_id`、人格核心、公共活动和全局 revision；差异只来自 `SessionSceneState`、目标用户的 `UserRelationshipState` 和 `AudienceExpressionPolicy`。私聊可以使用更完整的关系线索，群聊则降低私密细节、长度和主动追问，但这只是受众表达策略，不是人格降噪或人格克隆。群聊的唤醒、读空气和防刷屏只能产生 `SceneGate`（继续、吸收、延后或沉默），不能跳过统一的关系、情绪和决策链。

窗口切换只改变 `active_window_ref`。A 窗口的事件若改变公共活动或可共享情绪，B 窗口读取新的 `global_revision`；若只改变 A 的私聊关系或 session 话题，B 只能看到经过授权的脱敏投影。私聊正文、专属关系、健康/位置和群成员观察不会因 `global` 自动互相可见。全局状态按 `global_revision` 串行提交，窗口上下文按 `window_revision` 隔离；模型可并行生成只读草稿，但提交前必须校验人格绑定版本和全局 revision，过期草稿只能重编译或丢弃。

真正的不同人格必须是不同的 `actor_id + persona_id` 运行时，各自拥有 world、affect、relationship、memory owner 和事件账本。窗口不能通过 UMO、最近一次回复或群成员身份隐式切换人格；切换只能由显式 `PersonaBinding` 和授权触发。绑定变更后，旧人格尚未提交的生成任务失效，已提交的投递仍保留原回执并继续对账。一个窗口的一轮回复只能绑定一个人格；多人格共同出现时拆成带明确发言者的多个 `ResponsePlan`，不能把多套 system prompt 隐式拼接。

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

evidence_kind 至少包括 user_stated、device_observed、external_published、derived、model_inferred 和 action_receipt；角色模拟另带 `reality_mode=simulated` 与主体，不升级为现实观测。语义推断与原始证据分开引用，置信度不授予权限。精确坐标、原始生理数据和媒体先通过 enrich 生成最小必要语义；资源引用解析时重新校验 ACL、有效期和撤回状态。

## 6. 六类 DTO

下列 JSON 仅展示业务字段，不是可直接提交的完整请求。面向对话的请求须补齐 5.1 的 RuntimeScope；安装级控制和来源事件使用其声明粒度。v0.2 通用封套拟包含 `schema_version`、`capability_version`、`provider_id`、`provider_generation`、`trace_id`、`request_id` 与作用域；时效动作另带 deadline 和相关 revision。

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

可恢复事件与领域写入在所有者同一本地事务中写 outbox，消费方按事件 ID/消费者去重，并按实体 revision 处理乱序。修订和撤回带被替换引用；同一事实被召回或重放不能再次累加情绪或执行动作。跨库不承诺原子事务，使用幂等命令和对账；重放默认禁止副作用。

#### 6.2.1 NormalizedInteractionEvent

平台适配器把 AstrBot、独立服务或嵌入式应用的原生事件转换为 `companion.normalized-interaction-event.v1`。该 DTO 是只读决策输入，不是新的投递或领域写入协议。它必须包含稳定 `event_id`、`occurred_at`/`observed_at`、已解析 `RuntimeScope`、`scope_mode`（`session`、`user` 或 `global`）、subject/conversation/session 引用、证据引用、`visibility`、`reality_mode`、`source_adapter` 和 `adapter_generation`。

`payload` 只允许短摘要、内容引用或结构化小对象，并带 `redaction`、字节长度和可选内容哈希；完整聊天正文、平台对象、凭据、消息句柄和数据库连接留在 adapter 边界。Kernel 在生成 `RoleDecisionSnapshot` 前再次校验 ACL、共享模式、可见性、证据 TTL、事件去重和 adapter generation。用户未完成身份映射、群聊与私聊引用不一致、私密正文试图进入 `global`、旧代输入或过期事件均拒绝，不通过静默降级扩大范围。

### 6.3 ContextContribution

observe 和 enrich 的结果只有经过 Kernel 投影后，才进入模型上下文：

~~~json
{
  "lane": "scene",
  "key": "user_activity",
  "content": "设备记录显示用户今天累计运动约 42 分钟，无法据此判断刚结束运动。",
  "evidence": "device_observed",
  "priority": 40,
  "max_age_seconds": 1800,
  "visibility": "private"
}
~~~

lane 可取 identity、relationship、scene、memory、affect、affordance 或 safety。扩展提交事实和约束，不能直接覆盖人格核心设定。

主陪伴提供一个宿主侧请求入口。以下 event/req 仅用于与 AstrBot hook 对接，不属于跨插件领域 DTO，桥接层在返回时不保留可被后台复用的原生请求对象：

~~~python
api.register_extension(manifest)
api.set_extension_status(status)
scope = api.get_runtime_scope(event)
api.add_context_contribution(req, contribution, event=event, source_id="my_extension")
~~~

`get_runtime_scope` 在宿主边界解析完整的安装、Bot、平台账号、会话和人格身份；扩展不应自行从 `unified_msg_origin` 拼接主键。`add_context_contribution` 只把一条带证据的内容暂存到当前请求的 typed prompt plan，最终的权限、合并、投递和审计仍由主陪伴负责。请求之外的贡献必须携带完整 `RuntimeScope`，只有完整身份的旧 `Scope` 才允许由兼容层升级；缺少作用域、作用域不匹配或人格绑定版本过期时返回失败并记录诊断。

能力状态和扩展元数据可通过主陪伴的控制面查看：`GET /astrbot_plugin_private_companion/page/extensions/status`。控制面只保存 manifest、状态、能力描述和审计摘要；凭证、原始观测和业务数据库仍归提供方所有。

### 6.4 MemoryMutation

记忆领域的标准能力、请求/结果封套、扩展类型、策略注入及兼容映射见[记忆提议、查询与外部注入契约 v0](./MEMORY_PROPOSAL_QUERY_CONTRACT_V0.md)。`memory.proposal.submit`、`memory.query`、`memory.operation.get` 和 `memory.changed` 复用本协议的六类原语与控制面。MemoryMutation/RecallRequest 保留为变更/查询语义描述，对外统一映射为 MemoryProposal/MemoryQuery，不另建发现、权限或投递入口。注册类型和算法不授予权威写入权限。

记忆写入必须包含 namespace、scope、source、retention 和幂等键。普通对话、设备观测和模型推测不能使用同一种记忆等级。Memory Service 拥有记录及索引，Kernel 保存有期限的投影。修订/删除需要同步失效索引、缓存、余波和未执行候选；恢复备份时先应用删除记录，不能恢复已撤回的正文。

首期精度契约补充如下，属于 v0.2 待适配草案，不是现有 SDK 字段已实现的声明：

| 对象 | 必需语义 |
| --- | --- |
| MemoryAssertion | 稳定 assertion_id、主体、具体属性、值、polarity、qualifiers、valid_from/to、asserted_at、来源及 revision；未知项显式缺失 |
| MemoryMutation | 新增/纠正/更改有效时间/撤回语义；修改已有断言时指定 target_assertion_id、expected_revision 和证据；不同属性不按粗词类别互相替换 |
| EvidenceRef / EvidenceExcerpt | 来源 ID、获授权的必要片段、主体、时间、权限、保留状态；源日志过期后仍可解释，已经丢失则标记 source_missing |
| RecallRequest | RuntimeScope、查询、可选主体/属性/时间线索及预算；通过公开版本化入口传递，不依赖私有事件属性 |
| AnswerEvidence | 本轮选中的 assertion_id/revision、完整事实及证据片段、有效时间、置信依据、冲突/缺失和省略原因；外层沿用作用域与版本封套 |

断言与摘要复用现有 MemoryRecord 和纠正能力，旧记录先提供只读兼容投影，不从摘要批量补造证据。Memory Service 先限定合法候选空间，再进行有界语义召回；核心编排保留与问题有关的完整事实，记录实际注入的断言及片段，不能把 included_memory_ids 等同于答案证据已经完整进入模型。诊断记录按授权和保留策略处理，不将原始私聊正文复制到通用日志。

反馈单独区分曝光、实际使用、用户确认、纠正和独立来源佐证。重复召回、角色转述和用户沉默都不能记作新的事实确认。来源撤回时同时失效证据片段及派生断言；原始日志的保留期不等于长期断言证据的保留期。具体反例、提示词和验收口径见[记忆精度专项审查](../../astrbot_plugin_remember_you/docs/MEMORY_PRECISION_REVIEW_20260906.md)。

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
  "idempotency_key": "action-request-001:part-001",
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

现有状态包括 succeeded、rejected、permission_denied、unavailable、timeout、failed 和 cancelled。v0.2 拟增加 pending、uncertain、partial，并用 `reason_code` 表示 revision_conflict、scope_unresolved 等原因；新增枚举需版本协商，旧消费者不得默认当作 succeeded。

幂等键标识一次逻辑执行，重试复用，新请求创建新键，不能按“设备 + 操作 + 日期”去重整天的合法请求。请求先持久化、再执行、最后记回执；提交前超时可取消，提交后无法判定外部结果时为 uncertain，不能把 timeout 当作未执行。部分成功按 part_id 记录，不重放成功部分。

消息另用 DeliveryReceipt，区分 pending、submitted、accepted、delivered、partial、uncertain、failed、cancelled；平台受理不等于用户送达或已读。Adapter 声明幂等、查询和回执能力，未知结果只在有可靠查询或远端幂等保障时重试。发送历史由单一 history_owner 按已受理部分记录，长期记忆另行判断。详细结算规则见架构基线 3.12.4。

公共执行 profile 的关联字段在[公共契约](./COMPANION_CONTRACTS_AND_VERTICAL_SLICE.md#逻辑操作尝试和因果关联)统一设计，已有[execution 0.1.0 review Schema](./contracts/execution/v1/README.md)。task_id、attempt_id、可选 part_id、operation_id、budget_ref 和 caused_by 归入 execution，trace_id 与 idempotency_key 在请求封套。owner 签发前 operation_id 为 null，签发后跨尝试保留；不能要求调用方预造受理编号。Runtime 核对本次结果与派发记录，仅在实际协商 companion.execution@1 时采用新格式，不向现有封闭 memory.*.v1 请求/结果直接加字段，也不强制短同步查询写任务账本。

TaskSupervisor 的调度状态与领域结果分开，不能直接映射成平台已送达。所有 part 均未提交且已停止才整体 cancelled；存在已完成或未知部分时保持 partial/uncertain，并保存剩余部分的取消结果。状态更新校验 task_revision 和当前 attempt，uncertain 可经获授权对账收敛；dead_letter 只停止自动调度，不抹去未知回执。恢复使用原 task/operation/checkpoint，不以新幂等键重做未知动作；外部平台不支持 fence/幂等时，本地换代不能证明旧网络请求已停止。

SessionRecord 另以 `session_revision` 和 `context_revision` 控制连续上下文，断线进入 `reconnecting`，旧 revision 或旧 capability grant 不得直接恢复 `active`。会话结束只释放它拥有的上下文和票据，不关闭共享 provider；摘要、反馈和记忆提案需另走授权入口。以上是跨领域语义，设备、消息、媒体和记忆能力只负责各自结果与持久化证据。

新 OperationResult 用 effect_state 区分 not_applicable/not_applied/applied/partial/unknown，applied 需原 operation 与 receipt_ref，pending 需可查回执，uncertain 不返回猜测成功体。DeliveryReceipt 单独区分 part/aggregate，聚合回执以 parts_ref 引用完整部分集合，delivered 必须有送达时间和相应证据。字段组合以公共执行包为准；现有 ActionResult 和 Memory result 不自动获得这些字段。Task/Session Schema 是可读记录；取消、查账和恢复控制的字段语义见 §4.3 与生命周期，控制 Schema 和创建等其他命令仍待补齐。

## 7. 注册和生命周期

完整注册、探测、绑定、调用、订阅、撤权和卸载语义见[外部插件接入生命周期与适配器规范 v0](./COMPANION_EXTENSION_LIFECYCLE_V0.md)。它细化本节的状态和职责，复用当前控制面；旧 v0.1 元数据注册不等于已支持新句柄、提交 fence 或多提供方派发。

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

setup 阶段不得启动无主永久任务或访问未授权数据。ctx.tasks 可创建任务或托管已有句柄，连接允许在受监督任务组中循环。卸载先撤销准入及 generation，再取消和等待相关子任务，最后关闭资源；按资源实际所有者结束生命周期，不能因单个聊天会话结束而关闭共享账号连接。

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

已由 Kernel 接管的陪伴主动行为不得绕过协调层发送。独立命令及未接管的第三方插件仍走宿主；每轮声明唯一 delivery_owner 和 history_owner。实时房间、音频、字幕和外设数据走领域 Session/Adapter，管理面统一授权与监督，不让每帧经过主动候选和接触预算。

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

`interrupt_policy` 使用可解释值 `never_during_turn`、`defer_when_engaged` 或 `authorized_preempt`。旧布尔值只作为兼容输入：`false` 迁移为 `defer_when_engaged`；`true` 仍必须声明时效证据、授权和抢占理由，不能视为无条件插话许可。`ambient`、`continuity`、`invitation` 和 `share` 默认使用 `defer_when_engaged`。

调度器提供 alert、normal 和 ambient 三条优先级车道。类型策略可以决定候选如何产生、何时失效和如何表达，但不能绕过 Kernel 的作用域、权限、日历、额度、去重、投递和审计。

候选只能先生成 IntentPlan，再生成正文或动作。IntentPlan 至少包含 purpose、emotional_stance、conversation_load、continuity_anchor、evidence_refs、forbidden_claims 和 preferred_modality。模型不能改变候选优先级、证据等级或投递状态。

### 8.3 合并、抢占和确认

多个候选先按 merge_group、作用域、时间窗口和 continuity_anchor 合并。高优先级候选只在授权范围内抢占。用户新消息优先吸收同主题候选，不取消无关的明确提醒。用户明确 quiet、候选失效或扩展卸载时停止尚未提交的受影响动作；模型推测/角色模拟作息只用于时机评分。已提交部分保留回执并对账。

涉及设备、第三方服务、付费调用、现实承诺或隐私数据的主动类型必须声明 confirmation 策略。模型只能提交动作提议，实际执行和成功描述以 ActionResult 为准。

#### 8.3.1 对话占用态

Kernel 在每次候选生成、批准和提交前都计算按会话/目标隔离的 `ConversationOccupancy`，用来区分“可接受联系”与“可以插话”。最小 DTO 为：

~~~text
state = turn_open | engaged_thread | closing | idle | quiet | unknown
conversation_revision
active_thread_id
topic_fingerprint
last_user_turn_at / last_bot_turn_at
continuation_until
source_refs[]
~~~

`turn_open` 中普通候选只能合并到当前回复或暂存；`engaged_thread` 中同主题候选可以合并，无关的 `share`、`ambient`、邀请和角色生活更新必须延后，不得单独发送媒体；`closing` 使用短暂收尾宽限；`idle` 才按正常策略竞争；`quiet` 抑制普通候选但仍响应用户主动请求。缺少可靠状态时使用 `unknown`，不能猜测用户空闲。安全事件、用户明确请求和已授权的高时效承诺按自己的证据和权限策略处理。

`conversation_revision` 进入 `ResponsePlan`/`DeliveryPlan`。提交前 revision 变化时重新执行占用态、权限和主题检查；未提交计划进入 `deferred(conversation_busy)` 或 `superseded_by_thread`，已提交部分只保留真实回执。媒体生成必须在机会获准后启动，角色内部活动或图片生成完成本身不能触发投递。

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
conversation_revision
topic_fingerprint
defer_reason
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
| ActivityEpisode | 带主体及 observed/simulated 标记的活动和回执 | 可表达对应主体状态；角色模拟不能证明现实行为已发生 |
| ScheduleProjection | 根据以上对象生成的短期预测 | 只能表达为“可能”“预计” |

扩展不应提交完整日程文本给模型，而应提交结构化事件、活动状态或 ContextContribution。主陪伴只向模型投影当前活动、近期经历、下一项高置信意图和可用时间，保留空闲与未知状态。

`ScheduleProjection` 和每轮 `PromptSnapshot` 都是短期视图，不是角色的持久状态。跨插件需要改变角色连续生活时，应提交受校验的 `StateTransitionProposal`，而不是写入 prompt 或生成一页新日程：

~~~text
proposal_id / owner_key / target_state
patch / evidence_refs / expected_revision
valid_until / idempotency_key / trace_id
~~~

主陪伴按数据所有者、证据、权限、revision 和 TTL 接受或拒绝提议，并发布新的 `PersonaContinuityState` 投影。未确认的软计划、角色模拟和一次性分享不能直接成为持久事实；状态提交也不能获得消息、媒体或设备动作权限。

### 9.1.1 PersonaWorldModel 和具身状态

角色的生活世界不通过完整提示词传递。`PersonaWorldModel` 保存人格范围内的实体与关系，`EmbodimentState` 保存当前身体/注意力状态，`ActivityProcess` 保存可暂停和恢复的长活动，`HabitModel` 保存带上下文的倾向分布。技能或扩展若要改变这些状态，必须提交带 owner、证据、TTL、revision 和幂等键的 `StateTransitionProposal`；不能直接改 prompt 或写入长期事实。

面向单轮扮演只返回按需裁剪的 `AffordanceSnapshot`，例如当前可用的物品、地点、路线和剩余时间。没有证据的实体保持 unknown；模拟状态带 `reality_mode=simulated`，不能冒充现实观测。长活动只在交互、承诺边界、外部观测或检查点推进，不要求每分钟调用模型，也不因活动完成自动发送消息或媒体。

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
conversation_occupancy
evidence_refs[]
~~~

时间段允许重叠，类型包括 commitment、activity、availability、quiet、transition、deadline、buffer 和 unknown。持续区间使用 `[start, end)`，截止点独立表示；全天记录保存本地日期与时区，重复记录保留规则、例外和 occurrence ID，按有限窗口展开。明确夏令时缺失/重复时刻策略，不能把每天等同于固定 24 小时。各记录携带来源、状态、可打断性和确定性。

天气、空气质量、温度和预警属于外部观察，只能通过 `EnvironmentContext` 影响受影响窗口的活动可行性和候选排序，不能直接创建全天 `CalendarCommitment`、覆盖 `daily_plan` 或让模型生成整天大纲。天气变化触发局部重算；软活动可替代、缩短、延后或等待确认，现实承诺仍需明确授权才能修改。

同一边界适用于其他来源：`ScheduleProjection` 默认只返回当前段、下一转折和受影响窗口。用户要求查看全天时可以现场渲染视图，但未经确认的条目仍是意图/预测，不能转成 `ActivityEpisode`、主动触发器或媒体任务。兼容期间旧 `daily_plan` 仅作为旧页面/命令的读取快照，新路径不得从中逐项派生消息。

日历向 Bot 的输入分为 context、preference、constraint 和 gate；Bot 向日历的输出分为 observe、suggest、reserve 和 commit。observe/suggest 不改变现实承诺，reserve 必须有过期时间，commit 必须经过明确授权或用户确认。

时间段变更只触发受影响子树的局部重算。已完成和已确认记录保持不变；自动调整需要携带原版本、新版本、原因、证据、授权来源和回滚信息。日历写入产生的事件必须带来源和幂等键，避免“回写 -> 事件 -> 再回写”的循环。

### 9.3 CalendarCommand 和 Bot 写入权限

Bot 修改日历必须提交结构化 CalendarCommand，不能直接写数据库或用自然语言表达“已经改好了”。命令至少包含：

~~~text
target_id、calendar_kind、operation、interval_patch、reason、evidence_refs
scope、expected_revision、authorization、idempotency_key
~~~

operation 取 create、move、resize、split、merge、postpone、cancel、complete、reserve、lock 和 unlock。Kernel 先校验作用域、权限、版本和冲突，再生成 CalendarChangeSet；自动提交或用户确认后才产生新 revision。提交成功后发布 calendar.changed，失败则返回冲突、未授权、过期或不可用状态。

persona_calendar 和 user_calendar 必须分开。Bot 可以在授权范围内调整自己的模拟生活和软活动；用户现实日历及第三方同步需覆盖对应操作的授权。现有授权已覆盖时不重复确认，普通软活动授权不能扩展到设备或付费动作。确认绑定目标、参数摘要、revision 和有效期，版本变化返回冲突并重算。外部同步结果与本地提交分开记录，撤销外部效果需要补偿命令。

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

首期以 low/medium/high/unknown 及理由表示证据充分程度；未经标注样本校准不能声称是故障概率。现有数值 confidence 若保留，需说明评分版本和含义。反对证据可以为空，模型不能为凑字段编造证据或抬高等级。

证据的有效期、作用域和脱敏级别必须在聚合时再次校验。无法执行检查、数据过期或不同来源冲突时使用 unknown，并在答疑中明确说明未决项。

## 11. 版本和兼容性

- 能力版本使用 major.minor；同一主版本只允许向后兼容的可选字段增加。新增必填字段、改变键/空值语义或向无法识别的消费者返回新枚举时使用新主版本或显式协商，不能只提升文档版本。
- 提供方可以同时注册多个主版本。
- Kernel 启动时完成能力协商，不满足 requires 时标记 unavailable。
- 旧插件通过 Compatibility Adapter 转换到本协议；适配器不得暴露私有字段。
- DTO 必须可 JSON 序列化，时间使用带时区的 ISO 8601，枚举使用稳定字符串。
- 所有跨插件调用必须携带 trace_id、scope 和 capability_version。

## 12. 分批实现范围

以下是联合设计与适配的覆盖清单，不是现有 SDK 完成功能表。生产方、消费方及失败场景见架构基线 3.1.1、5.2；按[总纲](./FRAMEWORK_DESIGN.md)的当前切片选择实际验证范围，再据结果定型对应契约，不要求所有适配同时完成。

1. Scope、CapabilityDescriptor、Observation、Event、ContextContribution、ActionRequest、ActionResult；
2. 能力注册、版本协商、生命周期和 unavailable/degraded 状态；
3. TaskSupervisor、事件去重和动作幂等；
4. DiagnosticDescriptor、DiagnosticRequest、DiagnosticResult 和 Finding 的最小结构；
5. L0/L1 只读诊断检查和短期缓存；
6. 原生记忆的召回、写入/撤回、领域所有者和回执；原生日历的只读时间投影；
7. 原生创作/生图的作品版本、任务取消与资源引用，以及现实/屏幕的观测新鲜度、主体绑定和受控动作回执；
8. 一起、游戏、直播各自的 Session、参与者、人格快照、上下文、实时输出边界与结束摘要；
9. 问题治理读取跨插件诊断，以及所有原生插件的配置/工作区描述与能力降级状态；
10. `ActivityEpisode`、`PersonaContinuityState` 和 `StateTransitionProposal` 的事件/状态边界，验证短期 Prompt 视图过期后角色连续性仍可恢复；
11. 架构基线 N-01--N-05 的联动契约验证，覆盖精简安装、依赖缺失、混合版本、热重载和重复事件。

各插件首期可以使用现有业务与兼容适配器实现上述最小边界，适配记录须指明真实入口、输入输出、失败行为和验证编号。公共 DTO 根据原生联动结果调整后定型，不先冻结核心再要求所有领域迁就接口。外部模型、设备或平台可用录制数据/测试替身验证契约，未实测能力明确标记；仅注册 manifest 不算完成原生接入。
