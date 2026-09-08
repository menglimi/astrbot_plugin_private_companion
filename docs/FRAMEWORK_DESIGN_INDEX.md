# 新框架设计主题目录

> 更新：2026-09-08。本页是详细目录，不是第二个总纲；日常阅读从[文档入口](./README.md)开始。总纲负责整体结论与全局路线，本页负责文档职责、依赖、状态和来源；详细字段由对应专题维护。

## 1. 阅读与维护规则

不再要求顺序通读全部设计稿。了解整体只读总纲；推进一个主题时，再读该主题的规范、领域补充和验收依据。

| 文档层级 | 决定什么 | 使用边界 |
| --- | --- | --- |
| 总纲 | 总体目标、职责分层、共同结论、当前进度和全局顺序 | 唯一连续阅读入口；不重复完整字段表 |
| 专题规范草案 | 公共字段、能力接口、生命周期和平台边界 | 是对应主题的详细设计依据；仍待 schema、兼容与运行验证 |
| 领域设计草案 | 领域事实、算法责任、状态和用例 | 依赖公共契约，不另建身份、授权或投递协议 |
| 验收设计 | 反例、场景、预期和报告要求 | 是待执行要求，不代表已测试通过 |
| 审计 / 调研 / 实现提案 | 代码事实、外部经验、历史方案和迁移建议 | 提供证据，不覆盖现行契约或全局路线 |

遇到冲突按主题归属处理：总体方向和排期回到总纲；公共字段回到注入协议；记忆外部字段回到记忆接口；状态转移回到专门状态机；宿主与逻辑身份回到平台设计。不得仅按文件日期选择一套行为。尚未解决的字段问题标为待定，在契约定型时同步修改生产方、消费方与验收案例。

总纲 §10 的 A--D 是当前设计讨论路线，阶段 1--7 是未来建设与运行验收顺序。架构基线的阶段 0--5、A--I 和其他稿件中的局部“下一步”保留为迁移工作包与历史依据，不构成额外的开工前置条件。文档 v0、拟议 schema v1 与现有代码协议 0.1 是不同版本维度。

## 2. 架构与公共协议

本组规范由陪伴核心维护。依赖关系为：总体范围与架构 -> 公共状态语义 -> DTO / 能力协议 -> 生命周期；平台边界横贯这些主题。

| 文档 | 职责与权威范围 | 当前状态 | 下一步 |
| --- | --- | --- | --- |
| [设计总纲](./FRAMEWORK_DESIGN.md) | 总体方向、分层、统一路线 | 本轮整理后的主入口 | 随契约评审更新已定结论 |
| [生态范围](./COMPANION_ECOSYSTEM_SCOPE.md) | 领域地图、owner、建设批次 | 范围设计草案 | 随切片确认生产方、消费方和缺失行为 |
| [架构基线](./ARCHITECTURE_RESET.md) | 分层、稳定归属、日历调控、持续剧本、连续生活、情绪及迁移约束 | 综合设计基线；已补 HDSI 持续剧本对日历的适配边界 | 按专题定位细节；旧改造工作包按总纲择取 |
| [状态、事件与反馈](./COMPANION_STATE_EVENT_FEEDBACK_DESIGN.md) | 共同闭环与各类状态的责任 | 语义设计草案 | 与公共 DTO、反例互相校验 |
| [公共契约与纵向闭环](./COMPANION_CONTRACTS_AND_VERTICAL_SLICE.md) | DTO 组合、任务/会话/动作边界；创作分享与共处恢复走查 | 执行语义和 0.1.0 review Schema 已形成 | 用控制方法串联跨领域记录，补控制夹具 |
| [公共执行契约包](./contracts/execution/v1/README.md) | 6 个公共 Schema、110 个格式案例、17 份 DTO、跨领域静态关联 | 包 0.1.0，review；两条 workflow 未运行 | 作为后续控制夹具的记录输入，不改用记录充当命令 |
| [注入协议](./COMPANION_INJECTION_PROTOCOL.md) | RuntimeScope、能力 ID/版本、公共结果/回执；§4 描述符精确字段与控制封套 | 协议 v0.2 草案；control profile 0.1.0 已有 Schema 和设计夹具 | 补运行状态页、兼容负例和运行验证 |
| [公共控制契约包](./contracts/control/v1/README.md) | register/discover/bind/release/cancel/operation.lookup/session.resume 的封闭字段与设计夹具 | 包 0.1.0，review；5 个 Schema、12 个格式案例、4 个设计夹具；生命周期隔离回放 11 项通过 | 接入更多控制负例和资源故障，再评估薄 SDK 隔离实现 |
| [共享模式契约包](./contracts/sharing/v1/README.md) | session/user/global 归属；Memory v1 命名空间扩展、上下文/状态提议及受众边界 | `companion.sharing@1`，包 0.1.0 review；5 个 Schema、59 个格式案例、12 个示例、5 个静态关联与 SHR-01--12 | 世界切片复用；可信 anchor、ACL、订阅与资源运行验收 not_run |
| [扩展生命周期](./COMPANION_EXTENSION_LIFECYCLE_V0.md) | 注册、发现、绑定、取消/查账、恢复及签发责任；撤权、排空和资源释放 | 方法语义已细化；LC-01--LC-22 均 not_run | 将控制字段转 Schema，补响应丢失、旧代、并发恢复和资源夹具 |
| [平台迁移与打包](./PLATFORM_PORTABILITY_AND_BUNDLING_V0.md) | 宿主/平台分离、身份映射、快照与部署模式 | 可移植设计草案 | 在首份 fixture 中验证身份与降级 |
| [Canonical Fixture 与 SDK](./PORTABLE_CANONICAL_FIXTURE_AND_SDK_V0.md) | 录制、回放、最小端口和跨宿主验收 | 验收与执行层设计；工具待实现 | 复用公共 DTO，先做只读隔离回放 |

## 3. 记忆与首条参考切片

本组依赖公共注入协议和生命周期。阅读路径：领域对象 -> 外部接口 -> 状态机 -> 参考适配器 -> 反例。精度审查和旧提议通道说明用于理解来源，不是两套平行契约。

| 文档 | 职责与权威范围 | 当前状态 | 下一步 |
| --- | --- | --- | --- |
| [记忆领域契约](./MEMORY_CONTRACT_V0.md) | 事实、证据、查询与治理对象 | 领域契约草案 | 与外部字段和状态机保持一致 |
| [记忆提议、查询与外部注入](./MEMORY_PROPOSAL_QUERY_CONTRACT_V0.md) | 标准能力、请求/结果、类型与算法注入、版本兼容；§8.1 订阅控制 wire 与恢复轨迹 | 外部接口草案；控制 Schema 已有 review 版 | 细化服务端关联校验、持久化与授权变更 |
| [公共身份与记忆契约包](./contracts/v1/README.md) | 13 个 Schema、170 个格式案例、指纹、完整样例及订阅恢复夹具 | 包 0.2.0，review；运行语义未验证 | 作为隔离验证输入，补授权、租约、快照与资源证据 |
| [记忆与主动切片状态机](./MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md) | 独立状态转移；§6 细化事务、纠正/撤回、outbox、订阅/ACK、快照、增量重同步、持久化记录与资源预算 | 记忆设计 review 基线已形成；MW/SUB 运行验收 not_run | 进入隔离验证，取得事务、租约、恢复和资源证据 |
| [记忆与主动反例评估](./MEMORY_COUNTEREXAMPLE_EVAL_V0.md) | 语义反例、EXT、MW-01--24、SUB-01--16 和一致性报告；关联版本化格式案例与恢复夹具 | 验收设计；MW/SUB 全部 not_run | 为运行断言绑定隔离接口和原始证据 |
| [记忆参考适配器](../../astrbot_plugin_remember_you/docs/MEMORY_ADAPTER_DESIGN_V0.md) | 旧入口映射、单一 Writer、能力就绪与事务缺口 | 领域接入设计草案 | 评审作用域、提交边界和 fence；先读后写 |
| [记忆精度审查](../../astrbot_plugin_remember_you/docs/MEMORY_PRECISION_REVIEW_20260906.md) | 完整答案证据、检索权限、事实更新的依据 | 审查与设计来源 | 将有效结论回写契约和评估集 |
| [记忆提议通道升级](../../astrbot_plugin_remember_you/docs/MEMORY_PROPOSAL_UPGRADE_20260907.md) | 现有 MemoryProposal 通道及兼容方式 | 旧实现演进提案 | 作为适配器映射输入，不替代标准接口 |

记忆仓库目录为 `astrbot_plugin_remember_you`，插件 ID 为 `astrbot_plugin_memory_companion`。事实、证据、索引和治理仍归 Memory owner；核心保存有期限的投影。

## 4. 角色、主动与 Prompt

本组依赖共同状态模型与公共执行/投递契约。角色和主动的现行设计由蓝图维护；问卷和词表审查提供用例与反例，不能直接转成新的硬编码引擎。

| 文档 | 职责与权威范围 | 当前状态 | 下一步 |
| --- | --- | --- | --- |
| [扮演与主动蓝图](./ROLEPLAY_PROACTIVE_REBUILD.md) | 角色、持续剧本、动机、候选、占用态、预算、投递与反馈 | 已形成 `RoleDecisionSnapshot` 决策闭环、统一准入和反馈回写；领域设计仍为 review | 运行跨域回放与低风险主动预览，验证版本、占用、预算和降级 |
| [角色决策契约包](./contracts/decision/v1/README.md) | NormalizedInteractionEvent、AffectEvent、RelationshipEvent、RoleDecisionSnapshot、ProactivePreview、ShadowRun、ShadowAdapterBinding 与跨域回放 | `companion.decision@1`，包 0.1.0 review；7 个 Schema、7 个示例和 9 个归一化/跨宿主/降级/多窗口案例；角色回放 9 项、normalize/record 8 项、跨宿主 9 项、降级预算 11 项、多窗口连续性 13 项隔离检查通过 | 扩展平台原生录制样本，再接入真实旧/新策略影子计算，仍不执行真实投递 |
| [Prompt 重建设计](./PROMPT_SURFACE_REVIEW.md) | 语义理解、上下文贡献、计划和展示的分层 | 审查与设计草案 | 将来源转成类型化贡献和语义反例 |
| [旧词表与 AI-first 设计](./LEXICON_POLICY_REBUILD.md) | 词面判断的证据化、提示词约束和协议迁移 | 审计与重建设计来源 | 按切片替换，不新增统一词库工程 |
| [主动偏好问卷对照](./PROACTIVE_PREFERENCE_SURVEY_REVIEW_20260907.md) | 接触偏好、形式、时机、成本和授权的来源 | 调研对照 | 结论归入蓝图，案例归入评估集 |

日历、具身生活和情绪的详细对象仍在[架构基线](./ARCHITECTURE_RESET.md)的 3.7、3.10；本轮不拆出新的同义文档。

## 5. 其他领域与功能覆盖

| 文档 | 职责与权威范围 | 当前状态 | 下一步 |
| --- | --- | --- | --- |
| [优先领域状态机](./DOMAIN_STATE_MACHINES_V0.md) | 现实触及、共处及世界模拟生命周期；§7--§11 维护学习中断/恢复、共享归属和资源预算 | 世界首条详细切片已形成，WS-01--12 均 not_run | 日历、世界和角色决策边界已形成；运行授权、跨域回放和资源验证按切片执行 |
| [日历与持续生活契约包](./contracts/calendar/v1/README.md) | ActivityIntent、CalendarBoundary、ActivityProcess/checkpoint、StoryProjection、CalendarRequest/Result | `companion.calendar@1`，包 0.1.0 review；CAL-01--04 设计回放 not_run | 做局部重算、授权、幂等恢复和资源峰值隔离验证 |
| [世界模拟契约包](./contracts/world/v1/README.md) | WorldEntity、WorldEvent、WorldRequest/Result；世界/过程 revision 与事件重放边界 | `companion.world@1`，包 0.1.0 review；WMS-01--08 设计回放 not_run | 做共享裁剪、修订失效、幂等查账和资源隔离验证，随后转角色决策闭环 |
| [创作联动契约](../../astrbot_plugin_content_companion/docs/COMPANION_STORY_CONTRACT.md) | 作品、章节、版本、作品内记忆、封面和分享候选 | Content 领域契约草案 | 验证公共协议不只适配记忆；细化投递关联 |
| [功能覆盖与对等性](./FUNCTIONAL_COVERAGE_AND_PARITY.md) | 导入、修正、搜索、治理、重试、恢复和诊断 | 功能覆盖与验收设计 | 每个切片映射既有用户能力 |

其他领域的边界已经参与共同设计，专属契约尚待细化：

| 领域 / owner 目录 | 现有依据 | 进入切片前补充 |
| --- | --- | --- |
| 世界模拟 / `PersonaWorldModel` 能力提供方 | 架构基线 3.7、优先领域状态机 §6--§11、记忆状态机 §6.10、[世界契约包](./contracts/world/v1/README.md) | ActivityProcess/checkpoint、WorldEntity/WorldEvent、操作回执和 WS/WMS 场景已有机器契约；来源与资源边界已有文字设计，运行授权与峰值仍待验证 |
| 现实触及 / `astrbot_plugin_reality_companion` | 生态范围、优先领域状态机、平台协议 | 设备绑定、授权、观察 TTL、动作回执与离线 |
| 实时共处 / `astrbot_plugin_together_companion` | 公共契约、优先领域状态机 | 房间、媒体进度、恢复、结束与可选记忆 |
| 生图 / `astrbot_plugin_image_companion` | 能力协议、Prompt 设计、创作契约 | ImageTask、参考图归属、费用与资源回执 |
| 屏幕观察 / `astrbot_plugin_screen_companion` | 观察与隐私边界 | 采样、脱敏、留存与活动摘要 |
| 游戏共处 / `astrbot_plugin_game_companion` | 能力协议、Session 模型 | 房间、席位、规则引擎和生命周期 |
| 直播演出 / `astrbot_plugin_live_stream_companion` | 事件反馈、实时流与普通投递边界 | 演出 Session、平台连接与动作回执 |
| QQ 空间 / 现有核心模块 | 功能覆盖与社交发布边界 | 暂缓迁移；保留发布能力清单 |
| 问题治理 / `astrbot_plugin_bug_companion` | 生态范围、治理平面 | 暂缓独立治理流程；核心只读诊断可先做 |

## 6. 审计与参考来源

这些材料保存结论的来历和当时的证据，不直接定义当前 SDK，不用历史命中数或旧运行结果证明新框架完成。

| 文档 | 职责 | 当前状态 / 后续用途 |
| --- | --- | --- |
| [现有插件审计](./EXISTING_PLUGIN_AUDIT.md) | 带文件位置的旧代码与资源盘点 | 日期化静态基线；实现前复核相关入口 |
| [2026-09-06 设计审查](./DESIGN_REVIEW_20260906.md) | 架构、协议和审计的一致性修订记录 | 历史评审；保留待验证问题 |
| [设计与现有代码对照](./DESIGN_VS_EXISTING_PLUGIN_AUDIT_20260907.md) | 成熟能力、运行缺口和补写依据 | 日期化差距审查；辅助功能对等 |
| [本地与远端差距](./LOCAL_REMOTE_GAP_AUDIT_20260907.md) | 设计稿、工作树、远端分支与分析产物的区别 | 当时的仓库快照；需重新检查才代表当前状态 |
| [daily_life / daily_share 借鉴](./DAILY_LIFE_SHARE_INSPIRATION_REVIEW.md) | 连续活动与分享体验的参考 | 外部借鉴评审；结论回写角色/主动设计 |
| [爱语记忆借鉴](./AIYU_MEMORY_REVIEW.md) | 记忆体验、纠正和闭环的参考 | 外部借鉴评审；不继承词库和隐式绑定 |
| [WaifuBot 记忆借鉴](./WAIFUBOT_MEMORY_REVIEW.md) | 短期上下文、长期摘要、重要事件和召回预算的参考 | 外部借鉴评审；映射回记忆契约与 Content 领域 |
| [外部参考汇总](./EXTERNAL_REFERENCE_REVIEWS.md) | HDSI 的持续写作式生活/overlay/alter/共用剧本，以及小黑盒分享链接的投影、解析和降级机制 | HDSI 代码与实测未独立核验；小黑盒帖子正文未取得；均作为设计参考 |

## 7. 证据、产品记录与历史实现

保留原路径，避免破坏跨仓库引用。以下内容按需追溯，不加入连续阅读路径：

| 文件或记录 | 用途与边界 |
| --- | --- |
| [主模块依赖图](./dependency_graph_main_round4.dot)、[mixin 报告](./giant_mixin_dependency_report.csv)、[复杂度扫描](./module_complexity_scan.csv) | 静态审计证据；重新生成后才代表新代码 |
| `docs.zip` | 旧设计快照；本轮不更新，当前 Markdown 为准 |
| [Prompt 局部重构审计](./prompt-section-unification-audit.md)、[PR 集成审查](./PR_INTEGRATION_REVIEW_20260906.md) | 具体实现/集成记录；不覆盖新框架总边界 |
| [工作原则](./WORKING_PRINCIPLES.md)、[测试说明](./testing.md) | 当前实现与测试入口 |
| [参考图交接](./参考图功能交接.md)、[QQ 空间重构](./QQ空间-UIN身份与模块化重构.md)、[人格配置集成](./persona-config-integration.md)、[页面优化记录](./page-optimization-i18n-report.md) | 旧功能交接与局部设计；迁移时提取能力和兼容要求 |
| 创作目录的 `full.json`、`no_tab.json`、`qqdoc.html`、`sheetdata.json` | 临时抓取来源；不作为契约或生产数据 |
| [一起读 / 一起看工作记录](../../.workbuddy/memory/2026-08-29.md)、[Ardot 设计](https://ardot.tencent.com/file/719833898198972) | 进度、无剧透和互动体验的产品来源 |
| [记忆面板工作记录](../../astrbot_plugin_remember_you/.workbuddy/memory/2026-08-29.md)、[Ardot 设计](https://ardot.tencent.com/file/720042886141975) | 记忆治理与权限拓扑的产品来源 |
| [终端记录 09-01](../.workbuddy/memory/2026-09-01.md)、[09-02](../.workbuddy/memory/2026-09-02.md) | 手机摄像头、设置与运行状态的来源 |

外部设计文件和工作记录不代表统一工作区 API 已实现；进入对应领域时提取页面状态、授权和回执要求。

## 8. 当前推进与后续维护

记忆包保持 0.2.0 review，公共执行、控制、共享、日历和世界包均为 review；格式检查不改变各包运行夹具的 not_run 状态。本轮完成共享契约说明、wire 一致性、日历最小契约、世界对象/事件最小契约和设计包整理，并在现有领域状态机中补齐世界学习活动的详细切片及 WS-01--12。当前下一项是角色决策闭环；控制负例、状态页关联、Memory 持久化、WMS/CAL 运行授权和资源峰值验证保留在后续建设要求中。此阶段不启动旧插件接入或生产迁移；设计与建设两类顺序及完成条件只维护在[总纲第 10 节](./FRAMEWORK_DESIGN.md#10-当前进度与统一路线)。

维护一个主题时修改其所属详细稿、总纲中的相关结论以及受影响验收案例。只有出现独立的新领域或责任才增加文档；新稿登记职责、层级、依赖、状态和下一步。审计与参考保留日期，不在原报告中改写历史运行结果。
