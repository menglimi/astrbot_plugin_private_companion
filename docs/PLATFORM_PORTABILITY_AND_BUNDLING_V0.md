# 平台可移植与一站式打包设计 v0

> 导航：[设计总纲](./FRAMEWORK_DESIGN.md) / [主题目录](./FRAMEWORK_DESIGN_INDEX.md)。定位：平台规范草案；维护宿主、逻辑身份、部署与迁移边界。

> 状态：2026-09-07 设计草案。平台迁移和整合部署是新框架的一级目标，本文定义边界和验收条件，不表示当前插件已经可以脱离 AstrBot 运行。

## 1. 目标

陪伴体系应能在保留领域数据、人格、权限和可解释回执的前提下：

1. 把整套插件迁移到另一种 Bot/消息平台，只替换宿主、平台、认证和投递适配器；
2. 把多个插件打包进一个一站式应用，进程内、同机多进程或远端服务使用同一能力 DTO；
3. 在平台能力不对等时局部降级，例如富媒体变成文本/附件、缺少线程变成会话；只有受理证据时保持 `accepted`，明确送达时才是 `delivered`，用户已读或回应另行记录；
4. 保留跨平台迁移所需的稳定身份、事实 revision、证据引用、授权、事件账本和任务对账；
5. 让平台迁移可以先做录制回放和离线验收，不依赖真实平台持续在线。

迁移不是把某个平台的 event 对象、Cookie、页面路由和数据库路径搬到新平台。它是将逻辑实例、领域数据和能力绑定映射到新运行时，并明确哪些外部身份/回执无法等价迁移。

## 2. 分层与依赖方向

```text
Host / Packaging Shell
  ├─ AstrBot adapter, standalone app, embedded app, worker supervisor
  └─ lifecycle, config/secrets, health, process and transport
        ↓
Platform Adapters
  ├─ inbound events, identity, conversation, media, delivery, auth
  └─ OneBot / Telegram / Discord / Web / custom Bot
        ↓
Companion Kernel + companion_sdk (portable)
  ├─ RuntimeScope, clock, event, capability, task, policy, delivery boundary
  └─ no AstrBot event, platform SDK, Quart request, cookie or file path
        ↓
Feature Services
  ├─ memory, content, image, reality, screen, together, game, live
  └─ own facts and adapters; versioned DTO through Kernel
        ↓
Provider / Device / Storage Adapters
```

可移植层允许使用 Python 标准库和明确声明的跨平台依赖；AstrBot、Quart/Hypercorn、OneBot、平台 SDK 和桌面 UI 只出现在宿主/平台/管理入口包。Feature Service 的核心用例不能 import 这些模块，也不能接收其对象。平台适配器可以在边界将原生对象转换为规范 DTO，转换后原对象不得进入领域任务或被后台保存。

端口和适配器由能力决定，不由平台名决定：`InboundAdapter`、`IdentityAdapter`、`ConversationAdapter`、`DeliveryAdapter`、`MediaAdapter`、`ModelAdapter`、`Clock`、`Store`、`TaskSupervisor`、`WorkspaceGateway` 和 `SecretProvider`。一个平台可以提供多个版本/能力，一个能力可以由不同平台或自建实现提供；Feature 不判断 `aiocqhttp`、Telegram 或 Discord。

## 3. 宿主、平台和数据身份

现有 `RuntimeScope` 的 `installation_id` 当前描述 AstrBot 部署/数据目录，这会把基础设施迁移误当作领域归属。新协议区分逻辑身份与运行时身份：

| 字段 | 含义 | 迁移时 |
| --- | --- | --- |
| `ecosystem_id` | 陪伴生态/数据集合的稳定逻辑身份 | 保留；整套插件迁移仍指向同一生态 |
| `installation_id` | 逻辑应用安装和租户边界，不含具体宿主目录 | 可由迁移工具显式保留或映射；不按文件夹自动推断 |
| `runtime_instance_id` | 当前进程/容器/宿主代实例 | 改变；用于 generation、任务 fencing 和诊断，不作为事实 owner 主键 |
| `host_kind` / `host_id` | 宿主实现类型（例如 `astrbot`）及其宿主实例 ID | 用于调用归属和控制面/诊断，不进入稳定事实 owner |
| `bot_id` | 逻辑 Bot/代理身份 | 只有明确新 Bot 才改变；平台账号变更使用身份绑定 |
| `persona_id` | 角色身份 | 保留；人格绑定 revision 重新校验 |
| `platform` / `account_id` | 平台适配器与外部登录身份 | 可变外部映射；不作为逻辑用户/作品主键 |
| `conversation_ref` | 规范化会话引用 | 可作为连续性关联；不能伪称为不同平台上的同一实时线程 |
| `platform_conversation_id` | 平台原生会话 ID | 由适配器拥有和映射，可变且不可跨平台直接复用 |

标准 RuntimeScope 应增加 `ecosystem_id`、`runtime_instance_id`、`host_kind` 和规范化 `conversation_ref`，保留已有平台/account 字段的外部映射。`installation_id + bot_id + persona_id + subject_ref + visibility_namespace` 作为稳定领域 owner 的兼容形式时，installation_id 指逻辑边界；旧数据若只存宿主目录身份，迁移前创建明确的 lineage/owner 映射，不静默合并。

外部身份使用可审计的 `IdentityBinding`：

```json
{
  "binding_id": "identity-binding-001",
  "ecosystem_id": "ecosystem-001",
  "canonical_subject_ref": "subject-123",
  "platform": "telegram",
  "account_id": "bot-account-9",
  "external_subject_id": "telegram-user-77",
  "kind": "user",
  "authority": "user_confirmed",
  "valid_from": "2026-09-07T00:00:00Z",
  "valid_to": null,
  "revision": 2
}
```

昵称、头像、平台数字 ID 和同一字符串不能自动合并身份。多个平台身份只有在明确授权、证据和 revision 下投影到同一 canonical subject；不确定时保留为不同主体。群、私聊、频道和网页会话各自有 visibility namespace；迁移不扩大可见范围。

平台丢失线程、群成员或已读回执时，适配器返回能力缺失/未知并保存原平台映射；连续性可以通过历史摘要或新 conversation_ref 继续，不能把平台不支持的事实伪造为同一实时会话。

## 4. 规范平台边界

`host_kind` 与部署模式分开：例如 AstrBot 宿主使用 `host_kind=astrbot`，装配配置选择 `plugin_hosted`；独立服务、嵌入应用与拆分服务也由装配配置声明。纯内部能力调用无需虚构外部账号，对外投递再解析真实平台目标。

平台适配器只转换以下规范接口：

| 接口 | 输入 | 输出/能力状态 |
| --- | --- | --- |
| `InboundAdapter` | 原生更新、连接游标 | `InboundEnvelope`、来源证据、ack/重复更新状态 |
| `IdentityAdapter` | 外部账号、参与者、绑定请求 | canonical subject 候选、IdentityBinding 或 `identity_unresolved` |
| `ConversationAdapter` | 外部 chat/thread/room | `conversation_ref`、参与者、线程能力、可见性和生命周期 |
| `DeliveryAdapter` | 规范 ActionRequest、媒体引用、幂等键 | OperationResult + DeliveryReceipt；明确 accepted/delivered/uncertain |
| `MediaAdapter` | 授权 ContentRef、用途、过期时间 | 平台可用附件或 `unsupported/degraded`，不复制原媒体到 Kernel |
| `Auth/SecretProvider` | 逻辑能力请求 | 有效期授权/平台票据；密钥不进入 DTO、快照或日志 |

`InboundEnvelope` 至少包含 envelope_id、platform、account_ref、conversation_ref、external_event_ref、received_at、source_scope、payload 摘要、evidence_refs、dedupe_key 和 adapter_generation。payload 原文由适配器按隐私策略保留；Kernel 只接受结构化文本/媒体引用和最小必要证据。

`DeliveryAdapter` 必须声明 `supports_idempotency`、`supports_status_query`、`receipt_levels`、`max_payload`、`rich_media`、`threading` 和资源/限流能力。平台受理不等于送达；不支持查询的超时动作保持 uncertain，不能因为换宿主就自动重发。平台 adapter 不拥有记忆、主动候选或 history owner。

## 5. 应用内聚合与传输

所有部署模式调用同一 `companion_sdk` DTO 和能力协商：

| 模式 | 组件关系 | 适用场景 |
| --- | --- | --- |
| `plugin_hosted` | Kernel/Feature 在宿主插件中，平台事件由宿主适配 | 当前 AstrBot 兼容运行 |
| `standalone_service` | Kernel、Feature、Workspace 在独立进程，平台 adapter 通过连接接入 | 独立 Bot 服务或后台任务 |
| `embedded_app` | Kernel/Feature 作为一站式应用内模块，应用提供宿主端口 | 桌面、移动端或自有 Web 应用 |
| `split_services` | 控制面、领域服务、平台 gateway 分进程/容器 | 高并发或独立扩缩容 |

模式只改变 transport 和 lifecycle shell，不改变能力 ID、schema、owner、回执和状态机。首期可以是 in-process facade；同机 IPC、HTTP/gRPC 等远程实现使用同一 envelope，必须具备认证、大小/超时/重放保护、版本协商和 cancellation 语义。Feature 不知道请求走内存、IPC 还是网络。

一站式应用的 `PackageManifest` 至少声明 package_id/version、SDK/schema 版本、组件、能力/平台需求、迁移脚本版本、健康检查、资源预算、静态资源入口、数据目录策略、备份/恢复版本和可选模块。密钥、外部账号、设备票据和平台 webhook 在 `SecretProvider`/部署配置中注入；包内 manifest 不携带秘密。

管理 UI 使用 `WorkspaceGateway` 资源处理器和结构化错误/分页/能力状态；页面不直接调用某个插件的 Quart 路由或读取其 data_dir。CLI、桌面管理页和宿主 dashboard 可以是不同渲染器，使用同一 DTO 与权限。

打包必须保留模块所有权、数据库迁移、任务 supervisor、静态资源许可证、可选依赖和回滚边界。一个应用包可以不安装某个 Feature，控制面显示 unavailable/degraded；不能把缺失插件替换成一套隐式平行存储。

## 6. 数据迁移与整体搬迁

迁移分为逻辑生态迁移、运行时重打包和平台身份迁移，三者单独记录：

```text
source runtime fenced
  -> export manifest + scoped snapshot + identity map + event/tombstone ledger
  -> validate hashes, schema, owner and destination capabilities
  -> prepare target in isolated mode
  -> commit owner/lineage handoff
  -> activate target generation
  -> reconcile new events and close source
```

`PortableSnapshot` 至少包含 ecosystem/installation lineage、组件和 schema 版本、按 namespace 的事实/结构化领域事件/回执、可选的 StorySegment 投影引用、证据引用状态、revision、撤回墓碑、IdentityBinding、任务账本、策略/授权版本、资源引用和 integrity hash。故事正文默认可重建，不作为迁移成功的唯一依据；若保留经过裁剪的叙事缓存，必须带 source_event_refs、输入 revision、受众和 `reality_mode`。默认不包含平台 Cookie、token、原始设备/私聊全文或宿主绝对路径；需迁移的平台凭据由新平台重新授权。

快照必须支持 dry-run、分 namespace 导出、限时/权限裁剪、重复导入检测、source generation fencing、hash 冲突停止、已提交标记 replay 和目标回退。不同 schema 的转换器明确列出字段保留/丢失；不能表示的外部回执、线程、媒体或平台身份保留 `unknown/degraded`，不能伪造成功。

导入后先验证 owner、证据和授权，再发布能力绑定；来源仍可只读对账，不能与目标无条件双写。目标新事件使用新的 runtime_instance/provider generation，但沿用逻辑 owner 和事实 revision。源已提交的消息/设备动作不因快照回滚撤销，外部补偿另记 ActionRequest。

迁移平台时，平台映射表把 canonical subject/conversation 与新 external ID 关联；未确认的身份保持隔离。新平台不存在群、线程、文件、已读或互动按钮时，能力状态和用户可见降级由 adapter 回执说明；历史中可保留原平台引用，不向新平台发送不可表达的动作。

## 7. 依赖与打包验收

跨平台验收使用同一份 canonical 录制输入，分别送入两个平台 adapter 和三种 packaging shell。比较领域事件、MemoryProposal/Query、AnswerEvidence、主动候选、ActionRequest、事实 revision 和结果状态；允许外部 ID、格式和平台降级不同，禁止 owner、权限、幂等和事实语义不同。

| 检查 | 通过条件 |
| --- | --- |
| 核心可导入 | 便携层 import 扫描不依赖 AstrBot、平台 SDK、Quart request 或宿主 event |
| 录制回放 | 相同 canonical input 产生相同语义事件和状态 revision；平台特有字段只在 adapter 层出现 |
| 账户/会话迁移 | 明确映射或保持独立；不以昵称/平台 ID 自动合并，不泄露私聊/群空间 |
| 能力不对等 | 缺失线程、富媒体、已读或查询时正确 degraded/uncertain，不伪造 delivered |
| 打包启动/停止 | plugin/standalone/embedded 的 start、health、fence、drain、backup、restore 一致 |
| 快照搬迁 | dry-run、hash、revision、墓碑、任务账本和 owner 对账通过；失败可停在隔离态 |
| 资源与性能 | 适配层转换、序列化、IPC、连接和模型预算计入同一总预算，不新增隐式后台循环 |
| 第三方适配器 | 只实现标准端口即可通过 schema、权限、生命周期、回执和 EXT/LC 场景；不读取私有字段 |

首批录制平台可使用当前 AstrBot/aiocqhttp 和一个能力模型不同的平台替身，后续增加真实平台。没有真实平台或密钥验证的项目标为 recorded/isolated，不标记 live。便携性检查不是把平台代码搬走，而是证明领域代码只依赖端口和标准 DTO。

## 8. 当前架构修订与实施顺序

先冻结 RuntimeScope 的逻辑/运行时身份分离、Canonical identity/conversation、Platform/Host Adapter 能力声明和 PackageManifest；再把记忆外部契约的 `installation_id` 兼容语义写入映射说明。第一实现切片只做录制回放、schema 适配和只读查询，具体封套、fixture 和 SDK 端口见[可移植 Canonical Fixture 与 SDK 边界 v0](./PORTABLE_CANONICAL_FIXTURE_AND_SDK_V0.md)，随后验证写入 fence/receipt，最后才切换生产 owner。

实现时保留 AstrBot 入口作为一个 adapter；不在 Feature 代码里批量替换平台字符串。建立 import boundary、canonical fixture、迁移快照和 capability conformance runner 后，再评估独立 `companion_sdk` 包的发行形态。这样一站式应用是同一框架的新宿主，不是另一套业务实现。
