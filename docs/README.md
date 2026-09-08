# 陪伴新框架设计文档入口

这是唯一的文档导航入口。先读[设计总纲](./FRAMEWORK_DESIGN.md)了解结论和路线，再按任务进入专题。不要把每个专题末尾的“下一步”当作全局排期，全局顺序只看总纲 §10。完整职责、依赖和状态见[设计主题目录](./FRAMEWORK_DESIGN_INDEX.md)。

## 当前结论

新框架以陪伴核心和标准能力端口为中心，记忆、世界模拟、现实触及、实时共处、创作和其他插件可以独立安装，也可以打包成一站式应用。三种共享模式统一为 `session`（实际会话隔离）、`user`（同平台账号用户跨私聊/群聊共享获授权状态）和 `global`（显式公共安装/Bot/人格分区）；它们不代替 RuntimeScope、权限、受众、用途或保留策略。

HDSI 的持续剧本、overlay、alter、自动推进和共用世界已作为拟真参考纳入；外部参考统一放在[外部参考汇总](./EXTERNAL_REFERENCE_REVIEWS.md)，不再为每份参考建立一个设计入口。旧框架只提供能力清单、行为样本和迁移约束，当前不启动旧数据库或生产路径改造。

## 按任务阅读

| 你要确认的内容 | 先读 | 再读 |
| --- | --- | --- |
| 总体边界和当前下一项 | [设计总纲](./FRAMEWORK_DESIGN.md) | [主题目录](./FRAMEWORK_DESIGN_INDEX.md) |
| 三种共享模式和外部注入 | [注入协议 §5](./COMPANION_INJECTION_PROTOCOL.md#5-作用域证据和隐私) | [共享契约包](./contracts/sharing/v1/README.md) |
| 记忆写入、查询、纠正和恢复 | [记忆接口](./MEMORY_PROPOSAL_QUERY_CONTRACT_V0.md) | [记忆状态机](./MEMORY_VERTICAL_SLICE_STATE_MACHINE_V0.md)、[记忆契约包](./contracts/v1/README.md) |
| 世界模拟、日程和拟真生活 | [架构基线 §3.7](./ARCHITECTURE_RESET.md#37-日历和日程) | [日历契约包](./contracts/calendar/v1/README.md)、[世界契约包](./contracts/world/v1/README.md)、[世界状态机 §7--§11](./DOMAIN_STATE_MACHINES_V0.md#7-世界模拟纵向切片学习被打断与跨会话恢复)、[外部参考汇总](./EXTERNAL_REFERENCE_REVIEWS.md) |
| 角色回复、情绪、关系和主动 | [角色/主动蓝图](./ROLEPLAY_PROACTIVE_REBUILD.md) | [角色决策契约包](./contracts/decision/v1/README.md)、[状态事件闭环](./COMPANION_STATE_EVENT_FEEDBACK_DESIGN.md)、[Prompt 设计](./PROMPT_SURFACE_REVIEW.md) |
| 插件注册、绑定、取消和恢复 | [生命周期](./COMPANION_EXTENSION_LIFECYCLE_V0.md) | [控制契约包](./contracts/control/v1/README.md)、[执行契约包](./contracts/execution/v1/README.md) |
| 平台迁移和一站式打包 | [平台可移植设计](./PLATFORM_PORTABILITY_AND_BUNDLING_V0.md) | [Canonical Fixture/SDK](./PORTABLE_CANONICAL_FIXTURE_AND_SDK_V0.md) |
| 现有插件能力和迁移风险 | [现有插件审计](./EXISTING_PLUGIN_AUDIT.md) | [设计与代码对照](./DESIGN_VS_EXISTING_PLUGIN_AUDIT_20260907.md) |

## 文档权威层级

| 层级 | 包含内容 | 冲突时谁优先 |
| --- | --- | --- |
| 总纲 | 目标、全局结论、统一设计/建设路线 | 总纲 §10 |
| 专题设计 | 某领域的字段、状态、owner 和边界 | 该专题契约/状态机；总体方向回到总纲 |
| 机器契约 | JSON Schema、样例、指纹和静态夹具 | 对应 `contracts/*/README.md` 与 manifest |
| 验收设计 | 反例、运行场景、通过条件 | 只说明待验收要求，不表示已运行 |
| 审计与外部参考 | 旧代码事实、调研来源、历史提案 | 提供依据，不覆盖现行契约 |

## 当前状态

设计仍处于 review，不等于 SDK 或生产实现已完成。记忆、公共执行、控制和共享模式已有机器契约与离线格式验证；世界模拟已有学习中断/跨会话恢复、三种共享归属、来源边界、资源上限和 WS-01--12 设计场景，并形成 `companion.world@1` 最小机器契约及 WMS-01--08 回放夹具；日历已补齐硬承诺、角色软活动、StoryProjection 与 suggest/reserve/commit 的适配边界，并形成 `companion.calendar@1` 最小机器契约及 CAL-01--04 回放夹具；角色决策已形成 `companion.decision@1` 契约、平台无关的 `NormalizedInteractionEvent`、观察模式主动预览、ShadowRun 和 ShadowAdapterBinding；角色跨域回放通过 9 项检查，AstrBot normalize/record 隔离回放通过 8 项检查，跨宿主投影回放通过 9 项检查，能力降级与预算回放通过 11 项检查，多窗口连续性回放通过 13 项检查。真实授权、真实策略影子接入、Memory Writer、订阅恢复、跨平台身份、资源峰值和平台回放均保持 `not_run`。

当前设计顺序只有四步：公共契约与共享预设 -> 世界模拟最小机器契约 -> 角色决策闭环 -> 原生能力组合与产品场景。前三步的最小契约已形成，当前已完成平台事件归一化、跨宿主/能力降级和控制生命周期的隔离回放，随后才进入低风险主动预览和原生能力组合。实现顺序另见总纲阶段 1--7；两者不混用。

## 维护规则

新增内容先判断它属于现有专题、机器契约、验收案例还是历史参考。属于已有责任时直接修改对应文件并更新总纲/索引，不创建同义稿；外部参考默认写入[外部参考汇总](./EXTERNAL_REFERENCE_REVIEWS.md)。只有出现新的独立 owner、协议或领域责任时才新增文件，并在主题目录登记职责、依赖、状态和下一项。

所有文本使用 UTF-8。`docs/docs.zip` 是旧快照，不能作为当前设计依据；最新跨仓库设计包由仓库根目录的 `README_DESIGN_PACK.md` 说明。
