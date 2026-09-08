# 陪伴插件体系设计文档入口

整理日期：2026-09-08。

首先阅读[新框架文档入口](./docs/README.md)，再读[新框架设计总纲](./docs/FRAMEWORK_DESIGN.md)了解整体架构、当前进度和统一推进顺序；需要字段、状态机或来源时，再通过[设计主题目录](./docs/FRAMEWORK_DESIGN_INDEX.md)查阅。

详细文档分为公共规范、领域设计、验收设计和审计参考。记忆与创作的配套稿保留在各自仓库；单独传阅时需包含主题目录中引用的配套材料。

当前 Markdown 是整理后的设计依据，`docs/docs.zip` 是旧快照。`new_framework_design_20260908_control.zip` 已更新为包含公共控制、`companion.sharing@1` 共享契约、日历与持续生活契约包、世界模拟契约包和世界模拟首条详细切片的最新整理包；保留文件名供已有入口使用。此前的 `new_framework_design_20260908.zip` 保留为上一份压缩快照。

最新包保留三个仓库的相对目录，确保记忆与创作配套设计链接可定位；从包内 `astrbot_plugin_private_companion/docs/README.md` 开始阅读。包含私有陪伴全部当前 docs（排除旧 ZIP）、四个离线契约验证脚本，以及 remember_you/content_companion 的配套 docs；不含插件运行代码、数据库、凭据或临时抓取文件。中文文件名和文本均使用 UTF-8。审计稿中指向旧源码、个人工作记录和在线设计平台的历史引用仍需原工作区或外部来源，不属于包内可离线运行材料。HDSI 与小黑盒参考统一收录在 `docs/EXTERNAL_REFERENCE_REVIEWS.md`，不再为每份参考建立单独设计文件。

共享包已有 5 个 Schema、59 个格式案例、12 个示例和 SHR-01--12 设计场景；[日历包](./docs/contracts/calendar/v1/README.md)已有 6 个 Schema、6 个示例和 CAL-01--04 设计回放；[世界包](./docs/contracts/world/v1/README.md)已有 4 个 Schema、4 个示例和 WMS-01--08 设计回放；[角色决策包](./docs/contracts/decision/v1/README.md)已有 7 个 Schema、7 个示例、9 个归一化/跨宿主/降级/多窗口案例和跨域回放，角色回放通过 9 项、normalize/record 通过 8 项、跨宿主投影通过 9 项、能力降级预算通过 11 项、多窗口连续性通过 13 项隔离检查；[角色/主动蓝图](./docs/ROLEPLAY_PROACTIVE_REBUILD.md)已形成 `GlobalActorRuntime`、`RoleDecisionSnapshot` 决策闭环、低风险主动预览、ShadowRun 和影子适配器边界。当前先扩展平台录制样本，再接入低风险主动影子预览，仍不执行投递。设计稿不表示 SDK 已发布或生产迁移已完成。

在 `astrbot_plugin_private_companion` 目录按 `docs/contracts/requirements-validation.txt` 安装离线验证依赖后，可分别运行 `scripts/validate_framework_contracts.py`、`scripts/validate_execution_contracts.py`、`scripts/validate_control_contracts.py`、`scripts/validate_sharing_contracts.py`、`scripts/validate_calendar_contracts.py`、`scripts/validate_world_contracts.py`、`scripts/validate_decision_contracts.py`、`scripts/replay_decision_fixture.py`、`scripts/replay_normalized_input.py`、`scripts/replay_cross_host_comparison.py`、`scripts/replay_capability_degradation.py`、`scripts/replay_control_lifecycle.py` 和 `scripts/replay_global_multi_window.py`。这些脚本只验证格式、指纹和隔离语义，不启动 AstrBot，也不证明真实授权、恢复或内存峰值已通过。
