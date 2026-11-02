# 开发文档

按读者与任务选择入口：

## 插件贡献者

- [贡献指南](../CONTRIBUTING.md)：环境、目录边界、测试、提交和 PR 要求。
- [仓库布局与依赖](REPOSITORY_LAYOUT.md)：模块职责、依赖方向、源文件和生成文件边界。
- [陪伴小屋前端说明](../pages/陪伴小屋/README.md)：Dashboard 页面源文件与界面开发约定。
- [开发者日志规范](DEVELOPER_LOGGING.md)：日志字段和调试信息边界。

## 第三方开发者

- [扩展开发规范](extension-development.md)：稳定接口范围、扩展生命周期、身份边界和兼容建议。
- [外部记忆 Provider 接口](external-memory-provider.md)：注册代码、读写/统计负载、归属和超时语义。

## 架构与排障

- [Agent 首次配置与数据迁移手册](AGENT_MIGRATION_AND_SETUP.md)：向导、只读预览、目标核对、导入回执与重载验证。
- [核心回复路径设计](V2_CORE_REPLY_PATH_DESIGN.md)：宿主回复链路与领域核心协作方式。
- [注册统一说明](REGISTRATION_UNIFICATION_20260925.md)：页面与插件注册边界。
- 其他 `docs/` 下的带日期文件多为阶段设计、审计或迁移记录；引用时请同时核对当前源码与 README 的当前流程。
