# 贡献指南

感谢参与「我会永远陪着你」开发。本指南面向提交代码、文档、测试和界面改进的贡献者；第三方插件作者请先看[扩展开发规范](docs/extension-development.md)。

## 开始之前

- 先阅读 [仓库布局与依赖](docs/REPOSITORY_LAYOUT.md)，确认改动属于宿主适配层、领域核心、页面源文件还是测试。
- 修复缺陷时附上可复现步骤；新增行为时说明触发条件、用户可见结果和兼容影响。涉及较大改动时，先通过 Issue 或讨论确认方向。
- 每个 PR 聚焦一个目的。重构与行为变更尽量拆开，便于审阅和回滚。

## 本地环境

项目要求 Python 3.10 或更新版本。插件运行与宿主测试依赖 AstrBot SDK；在 AstrBot 安装环境中，请使用该安装自带的 Python，并将其 `backend/app` 加入模块搜索路径。命令中的 `<AstrBot>` 代表本机 AstrBot 安装目录，不要把本机绝对路径提交到仓库。

```powershell
$AstrBotPython = '<AstrBot>/backend/python/python.exe'
$env:PYTHONPATH = '<AstrBot>/backend/app'
& $AstrBotPython -X utf8 -c "import tests.astrbot_data_root; import unittest; unittest.main(module=None, argv=['unittest', 'tests.test_external_memory_providers', '-q'])"
```

无 SDK 依赖的纯领域测试可使用项目环境中的 Python。若测试需要运行宿主 SDK，请遵循测试自身的隔离设置，不要连接真实用户数据库或生产机器人账号。

## 修改边界

- 依赖方向保持为 `main.py → astrbot_host → companion_v2`；领域核心不导入 AstrBot SDK 或 `astrbot_host`。
- 页面按各目录的说明修改源文件；关系、账号、生活等嵌入页面的 `workspace.html` 和配套脚本位于对应目录。修改后运行 `python -X utf8 tools/sync_framework_page.py`，并用 `--check` 确认生成页面与清单同步。
- 不手工维护生成副本、交付包或发布清单；用仓库工具从当前源码构建并验证。
- 测试夹具只能使用合成、非敏感数据。不要提交生产数据库、聊天记录、图片原件、设备凭据、令牌、个人路径或运行日志。
- Python 源码、JSON、YAML、Markdown 与前端文件统一使用 UTF-8 编码；遵循 `.editorconfig` 的 LF 换行和末尾换行要求。
- 不为整理格式而改动无关代码，也不要在一个 PR 中顺手迁移无关数据或配置。

## 测试与验证

先运行覆盖改动行为的最小测试集，并在 PR 中列出实际执行的命令。改变共享生命周期、权限、数据格式或发布合同的改动，应补充相应回归测试。

提交前至少检查：

```powershell
git diff --check
```

页面、发布或配置迁移改动应按影响范围追加验证。仓库 CI 的当前发布合同包括：

```powershell
python -X utf8 tools/sync_framework_page.py --check
python -X utf8 -m unittest -q tests.test_release_secret_detection tests.test_native_release_version_guard tests.test_configuration_migration
node --test tests/test_page_route.cjs tests/test_models_page.cjs
python -X utf8 tools/build_v2_release.py --output <仓库和插件安装目录之外的交付目录>
python -X utf8 tools/verify_v2_release.py --release <交付目录> --extract-to <新的验收目录>
```

宿主验包需要 AstrBot 环境时，使用 AstrBot 自带 Python，并按 README 的发布验证流程传入 `--host-check`。无需为了文档或低风险局部改动运行完整宿主验收；若跳过某项 CI 检查，请在 PR 中说明原因。

## 提交与 PR

- 提交标题使用简短、可检索的描述，推荐格式：`类型: 动词 + 范围`，例如 `docs: 补充第三方记忆接入指南`、`fix: 修复重载后的回调清理`。类型可用 `feat`、`fix`、`docs`、`test`、`refactor`、`build`。
- PR 标题直接说明用户可见的变化，不使用“更新”“修复问题”等无法定位内容的泛称。
- 使用仓库的 PR 模板填写背景、行为变化、影响面、验证命令和结果。关联对应 Issue；不适用的项目标记为“不适用”并简述原因。
- 说明兼容性、配置或数据格式变化，以及页面生成物是否同步。若仅修改文档，也请标明文档读者与入口。
- 提交前检查差异中没有凭据、用户内容、临时文件、缓存、构建目录或与主题无关的格式化改动。

维护者可能会要求补充测试、收窄改动范围或调整公开契约；收到反馈后请在原 PR 里跟进，便于保留完整审阅记录。
