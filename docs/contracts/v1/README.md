# 公共身份与记忆契约包

> 2026-09-08，包修订 `0.2.0`，成熟度 `review`。这是总纲“契约定型”阶段的机器可校验设计产物，不是生产 SDK。返回[设计总纲](../../FRAMEWORK_DESIGN.md) / [主题目录](../../FRAMEWORK_DESIGN_INDEX.md)。

本包落实现有[记忆接口](../../MEMORY_PROPOSAL_QUERY_CONTRACT_V0.md)的字段、空值、互斥条件与结果形状。字段以本包同一修订的 Schema 为准；授权、生命周期与提交语义仍由所属专题维护。没有新增运行时入口，也不修改旧插件 DTO 或数据库。

## 1. 文件与版本

| 对象 / wire 标识 | Schema | 责任 |
| --- | --- | --- |
| 公共词汇 | [common](./schemas/common.schema.json) | 请求基础、标识、时间、预算、证据与扩展 |
| RuntimeScope，嵌套对象 | [runtime-scope](./schemas/runtime-scope.schema.json) | 已解析的调用位置和人格绑定，不授予权限 |
| Memory owner，服务内对象 | [memory-owner](./schemas/memory-owner.schema.json) | 稳定 owner 元组，不接受调用方自行设置 |
| `memory.proposal.v1` | [memory-proposal](./schemas/memory-proposal.schema.json) | add / correct / retract / no_op |
| `memory.query.v1` | [memory-query](./schemas/memory-query.schema.json) | 授权范围内的查询申请 |
| `memory.operation-query.v1` | [memory-operation-query](./schemas/memory-operation-query.schema.json) | operation_id 或原能力与幂等键二选一 |
| `memory.result.v1` | [memory-result](./schemas/memory-result.schema.json) | 按能力与状态选择不同结果形状 |
| `memory.changed.v1` | [memory-changed](./schemas/memory-changed.schema.json) | owner 变更事件向获授权订阅者的投影 |
| 订阅共用对象 | [memory-stream](./schemas/memory-stream.schema.json) | binding、limits、lease、snapshot 描述、引用/墓碑及完成证明句柄 |
| `memory.subscribe.v1` / `memory.ack.v1` / `memory.resync.v1` | [memory-subscription-request](./schemas/memory-subscription-request.schema.json) | 三种控制请求的封套、能力配对、操作互斥条件 |
| `memory.subscription-result.v1` | [memory-subscription-result](./schemas/memory-subscription-result.schema.json) | 绑定、确认、快照页或受限错误；不表示事实提交 |
| `memory.change-batch.v1` | [memory-change-batch](./schemas/memory-change-batch.schema.json) | Runtime 投递控制与不变的 memory.changed.v1 事件数组 |
| `memory.checkpoint.v1` | [memory-checkpoint](./schemas/memory-checkpoint.schema.json) | 分页暂存、尾部暂存、active 投影的持久化恢复依据 |

使用 JSON Schema Draft 2020-12。`$id` 是本地注册的 URN；wire 中的 schema_version 是协议标识，通过本表映射。引用只从[manifest](./manifest.json)登记的本地资源加载，不能联网获取调用者自报的 schema。

文档 v0、包修订 0.2.0、目标能力版本 1.0 和现有 Python 协议 0.1 分属不同维度。当前包尚在评审，修改字段同步变更样例与指纹；发布后的破坏性变更需要新的能力和 schema 主版本。0.2.0 新增五个 Schema，原八个 Schema 及原案例保持不变；旧客户端通过原能力继续使用 0.1.0 字段，不向它投递未协商的新批次。

manifest 登记全部 Schema、样例和案例集。`sha256-utf8-lf` 对 UTF-8 文件字节仅将 CRLF 规范为 LF 后计算 SHA-256，不忽略其他空白。它用于发现契约漂移，不是身份认证或签名。Markdown 说明与验证器不参与该协议资产指纹。

订阅 binding 的 contract_fingerprint 对所协商 Schema 集合排序后计算：按 `$id` 的 Unicode 码点顺序，每行是 `$id`、一个 TAB、该文件的 sha256、一个 LF，连接后取 UTF-8 SHA-256。当前夹具锁定本包十三个 Schema，样例/manifest 自身不参与这一摘要，避免自引用；资产完整性仍由 manifest 单独锁定。view_fingerprint 由服务端对解析后的视图语义签发，样例值只是夹具标识，不作为真实授权证明。

## 2. RuntimeScope 的字段决策

| 字段 | 本包约定 |
| --- | --- |
| ecosystem_id / installation_id / bot_id / persona_id | 必填稳定逻辑标识；由可信装配及身份服务解析 |
| runtime_instance_id / host_kind | 必填运行身份；host_kind 是宿主实现，不接受部署模式名 |
| host_id | 可省略或为 null；独立宿主实例信息，不进入 owner |
| persona_binding_revision | 必填非负整数；0 也是需核验的版本，不能解释为跳过校验 |
| platform / account_id | 必须显式出现；内部调用两者为 null，平台调用两者均有值 |
| conversation_ref | 显式字符串或 null；内部任务可以无会话，平台会话存在时必须有规范引用 |
| platform_conversation_id | 显式字符串或 null；只能由适配器解析 |
| user_id / group_id | 外部平台调用中的发言者/群映射，不是 canonical owner；内部调用均为 null |
| session_id | 显式字符串或 null；表示临时线程、房间或会话边界，不进入长期 owner |
| conversation_id | 可省略的旧兼容别名；如有值必须由适配器证明与 platform_conversation_id 一致 |

外部平台字段使用 null 表示“不适用”，不使用空字符串。字段之间的值相等约束、已注册映射和当前 revision 需要运行时复核，JSON Schema 不会查询绑定服务。原始 event、request、插件对象和路径不得进入 DTO。

内部调用可使用规范 conversation_ref 保留应用连续性，同时保持外部平台字段为 null。对外发送时需另行绑定真实目标；内部 scope 合法不代表投递地址已确定。

## 3. owner、事实主体与兼容边界

稳定 owner 为 `(ecosystem_id, installation_id, bot_id, persona_id, subject_ref, visibility_namespace)`。前五项是逻辑身份，第六项是已经授权解析的可见空间；业务 namespace 是该 owner 下的领域分区。Memory 返回不透明 owner_ref，外部请求不提交 owner 元组覆盖归属。

owner.subject_ref 表示该记忆空间的归属主体，payload.subject 表示断言谈论的主体，scope.user_id 表示平台发言者映射。第三方转述、群成员和代办调用时三者可以不同，不能从其中一个直接推出另外两个。查询 subjects 只过滤断言，不改变获授权 owner。

`RuntimeScope -> NamespaceContext` 不是单纯字段改名。旧上下文缺少 ecosystem、installation 和 bot，且正式记忆访问要求有效 assurance、profile_status、policy_version 和 migration_epoch。这些字段不能由调用者或模型补填。

完整映射步骤、按 kind 的分支和存储分区条件维护在[记忆适配器 §4.4](../../../../astrbot_plugin_remember_you/docs/MEMORY_ADAPTER_DESIGN_V0.md#44-公共身份到旧命名空间的映射决策)。本包的[兼容案例](./compatibility.json)只执行现有纯 NamespaceContext/AssurancePolicy，证明字段与旧策略的关系；不宣称完成真实身份绑定或生产授权。

## 4. 提议、查询与回执

提议规则：add/correct 必须有完整 subject、predicate、object 与 retention；correct/retract 必须指定 target_atom_id 和 expected_revision；add/no_op 的这两个字段为 null。所有写请求带幂等键，有事实副作用的提议还必须有证据。no_op 不进入延迟队列，不声称生成事实。

查询的可选过滤项省略时使用接口规定的默认值；Schema 的 default 只是说明，验证器不会修改请求。include_pending=true 必须携带 session_id。未提供 evidence_budget 时，由已绑定策略提供有限预算；不能解释为无限召回。

所有请求显式带 deadline_at 与调用预算；max_model_calls=0 允许关闭辅助模型调用。标识字段最长 256 字符是传输上限，输入字节数、嵌套深度、语义正文和总 token 等仍按协商预算执行。日期时间使用带秒和时区的 RFC 3339 子集，不接受闰秒；日历日期与偏移量须由日期解析器校验，不能只匹配字符串。

| 结果 | output 的含义 |
| --- | --- |
| 提议 succeeded + persisted | 有 atom_id、真实 revision、保留策略和 committed 事实回执 |
| 提议 succeeded + skipped | no_op；可以持久化操作账本，但 commit_state=not_committed 指没有事实事务，不带 atom_id |
| 提议 pending | 持久化的待处理提议及可查 operation_id；事实仍未提交 |
| 提议 uncertain | commit_state=uncertain，不允许自动重试或伪造事实回执 |
| 查询 succeeded / partial | RecallResult，包含 items、coverage、usage 和分页游标；partial 必须明确 degraded |
| 操作查账 | 外层对应当前 lookup 请求；成功/待处理 output 保留原能力和 generation，失败/不可见返回受限信息 |
| 查不到操作 | rejected / target_unavailable，output=null；不能据此证明 not_committed |

本包的只读查询同步返回 succeeded/partial 或拒绝、超时、取消等失败结果，不使用写入提交意义上的 pending/uncertain。事实提交保持原子性，Writer 不返回 partial。operation.get 当前查询的原能力限定为 memory.proposal.submit，不能用它探测没有写入账本的普通查询。

查询结果采用带判别字段的两类 item：

- item_kind=fact：atom_id、revision、fact_status 及完整 AnswerEvidence；fact_status 只有 active、superseded、expired，历史返回仍须满足用途、权限和保留条件。
- item_kind=proposal：proposal_id、proposal_state=pending、session_id 及明确的临时证据；不带 atom_id 或事实 revision。

证据片段 available 时包含真实 excerpt；source_missing 时 excerpt=null，并保留有权解释的来源引用。无权或撤回内容由 owner 阻止输出，不能借 source_missing 暴露隐藏对象。查询 schema 通过不证明断言准确、片段完整或已被模型实际使用。

memory.changed Schema 描述向某个已授权订阅者投递的 RuntimeScope 视图，源 outbox 仍按 owner 保存。事件不带事实正文；trace_id 的对应关系、发布者身份、revision 与实际事务必须由运行时核验，不能通过填对字段伪造变更。

## 5. 扩展与格式校验之外的要求

语义 predicate、object、qualifiers 与类型标识保持开放。附加元数据使用 namespaced extensions，每项含 schema_version 和 payload。影响正确性或过滤的扩展必须进入 required_features 并实际协商；未知非必需元数据可保留。Schema 只确认格式，不决定扩展可执行或已获授权。

以下检查是后续只读适配与可靠 Writer 的验收项，本次格式通过不能替代：

| 检查 | 执行责任 |
| --- | --- |
| 调用方身份、目标 provider 与句柄 generation、授权、namespace/purpose | Kernel 与 Memory owner；provider_id 表示目标提供方，不是调用者自证 |
| owner 分区和旧命名空间映射、P5 等来源证明 | 可信适配层与旧存储授权入口 |
| deadline、有效区间先后、证据时效、游标过期 | Runtime 与 owner 的受控时钟 |
| 请求与结果 ID/版本对应、旧结果撤权复核 | 能力 facade 的返回边界 |
| current_only、include_pending/session、subjects、类型与用途过滤 | Memory 查询和输出边界 |
| 证据来源、真实 revision、片段完整度、预算与 coverage 真实性 | Memory 与上下文编排 |
| 幂等、事实/回执/outbox 同提交、旧代 fence、未知提交对账 | 单一权威 Writer 的事务边界 |

## 6. 离线验证

依赖见[验证依赖](../requirements-validation.txt)。从陪伴核心仓库运行：

```powershell
python -m pip install -r docs/contracts/requirements-validation.txt
python scripts/validate_framework_contracts.py
```

验证器只使用 jsonschema、Python 标准库和核心/记忆两份无宿主依赖的命名空间模块，不导入 AstrBot/Quart，不启动插件，不打开生产数据库。普通系统 Python 可执行此验证。

[cases.json](./cases.json)引用 24 个完整样例，并用明确字段替换/删除表达反例；changes 是验收数据辅助格式，不是生产 JSON Patch 接口。覆盖四种提议、内部与平台 scope、查询/查账、暂存/事实判别、冲突、未知回执、版本、日期、预算与扩展。

验证器还检查严格 JSON（重复键、非有限数）、Schema 元规范、本地引用与文件指纹。日期校验显式启用，避免 jsonschema 的可选格式依赖缺失时静默跳过。兼容案例同时校验核心和 Memory 两份旧契约的指纹与策略。

2026-09-07 首轮：80 个 Schema 案例、6 个严格 JSON 案例、11 个兼容案例通过。格式验证和旧策略示例是当前覆盖范围；报告中 authorization_service、live_identity_mapping、writer_atomicity、outbox、fence 和 platform_replay 等保持 not_run。

需要重新登记已评审文件指纹时，运行 `python scripts/validate_framework_contracts.py --print-manifest` 获取清单，再随包修订更新 manifest；普通验证不会自动覆盖指纹或让变更自行通过。

## 7. 订阅控制与恢复夹具

完整语义维护在[外部契约 §8.1](../../MEMORY_PROPOSAL_QUERY_CONTRACT_V0.md#81-订阅控制-wire-决策与恢复轨迹)，状态与故障边界见[切片状态机](../../MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md#68-订阅确认与增量重同步)。这里只维护包内定位和验证范围。

首版快照为 references 投影，只包含当前 fact_ref 和可披露 tombstone；不传正文，不证明完整备份或向量重建。current_projection 强制四种 change_kind，初次从 snapshot 开始；notifications 可以只监听部分种类。所有控制调用 max_model_calls=0，实际批次、总在途内存和恢复磁盘占用仍须运行时按协商额度计量。

控制结果采用公共状态的子集：succeeded/rejected/permission_denied/unavailable/timeout/uncertain/failed/cancelled；不使用提议 pending 或部分事实提交意义上的 partial。succeeded + resync_required 只表示订阅绑定成功。尾部 ACK 回执保持 resyncing，snapshot 完成确认后才 live；失败 output=null，不把控制回执放进原 memory.result.v1。

[subscription-cases.json](./subscription-cases.json)新增 90 个正反例，与原 cases.json 一起验证。包括快照完成标记、零重试预算、未知字段、能力/版本配对、过滤遗漏撤回、非法 checkpoint 阶段，以及格式合法但语义待运行核验的 shape-only 案例。原有 80 个格式案例、6 个严格 JSON 案例和 11 个兼容案例继续保留。

[subscription-recovery.fixture.json](./subscription-recovery.fixture.json)为 fixture 0.1.0，引用 16 份完整 DTO，编排 W=104 的分页快照、E105 纠正、E106 撤回、C=106 的激活确认和 C 之后的新批次，并映射 SUB-01--16。17 份新增样例还包含独立失败回执。时间、游标、证明句柄和原平台映射是隔离执行素材；原生平台录制、租约签发和存储实现尚未加入。

离线验证检查十三个 Schema、170 个格式案例、fixture 引用/形状/指纹及既有 JSON/兼容案例。它不会执行 fixture.steps，也不会把 fixture 的 not_run 改成 pass。服务端关联校验、快照一致性、checkpoint 持久化、授权撤销、租约顺序和真实内存峰值在报告中保持 not_run；持久化记录与对账接口的设计已归入[状态机 §6.9](../../MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md#69-持久化记录对账与故障-barrier)，下一步是隔离验证。
