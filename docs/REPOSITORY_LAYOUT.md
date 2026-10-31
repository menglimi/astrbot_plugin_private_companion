# 仓库布局与依赖

本仓库交付单个 AstrBot 插件 `astrbot_plugin_private_companion`。根目录只放 AstrBot 入口 `main.py`、元数据、配置 schema、依赖和用户导航文档，不放数据、日志、临时脚本或交付包。

| 路径 | 职责 |
| --- | --- |
| `main.py` | 唯一 Star 注册、启动、事件转发与退出。 |
| `astrbot_host/` | AstrBot SDK、Dashboard、OneBot 和七项内置能力的宿主适配。 |
| `companion_v2/` | 不反向依赖宿主包的领域逻辑、配置、迁移与存储；目录名是内部实现名称。 |
| `pages/陪伴小屋/` | 唯一公开 Dashboard 页面；其他工作区以 `workspace.html` 为源并嵌入小屋。 |
| `tests/`、`fixtures/` | 自动测试与合成数据，不保存用户资料。 |
| `tools/` | 开发、页面同步、交付包构建和验证工具。 |
| `docs/` | 当前工程合同、配置映射和开发者排障说明。历史设计与验收资料在发布准备归档中。 |

依赖方向是 `main.py → astrbot_host → companion_v2`。核心包不得导入 `astrbot_host` 或 AstrBot SDK。跨领域操作须经所属领域服务及权限边界，不能直接读写别的领域的持久表。

七项能力 Content、Image、Screen、Reality、Together、Game、Live 随同一本体交付，内部按各领域实现和启停，不额外注册 Star。插件身份、目录名、`metadata.name`、`PLUGIN_NAME` 和注册装饰器必须一致；领域产品 ID 仍为 `private_companion.v2`。页面规范源只在 `pages/陪伴小屋/`，同步使用 `tools/sync_framework_page.py`。

源码统一在该仓库维护。发布与安装都从同一份已验证源码生成；内嵌工作区和页面清单由源文件生成，不手工修改生成副本。设计稿、历史发布暂存、测试输出及备份放在仓库和 AstrBot 一级插件扫描目录之外，不作为后续修改的来源。

运行数据与旧版来源分开。迁移仅读取旧版配置和资料，选择明确目标后写入新数据根；不得修改旧版用户数据。测试、可加载副本和备份放在 AstrBot 一级插件扫描目录之外。宿主测试使用 AstrBot 自带 Python、`backend/app` 路径及独立临时 `ASTRBOT_ROOT`。

发布顺序：修改领域功能及实际配置 → 同步陪伴小屋 → 从当前源码构建交付包 → 对该包验证清单、迁移、生命周期、重载和路由注销。旧包的通过记录不能代替新包测试。公开升级与回退步骤见根目录 `README.md`。

## 运行目录同步

源码工作树是唯一可编辑副本；AstrBot 插件安装目录是部署副本，不要在那里直接修改源码。`tools/sync_runtime.py` 提供检查、计划和应用三步：

```powershell
Set-Location <源码仓库>
python -X utf8 tools/sync_runtime.py --check
python -X utf8 tools/sync_runtime.py --plan
python -X utf8 tools/sync_runtime.py --apply --plan-path <plan.json>
```

`--check` 在存在差异时返回非零状态；`--plan` 会生成计划并在仓库外备份将覆盖的运行文件。运行数据、缓存、Git 元数据和生成清单不属于源码同步范围。先构建并完成定向测试，再应用同步计划；应用后按发布流程重载并核对。

## 工作树、备份与归档边界

| 内容 | 处理方式 |
| --- | --- |
| 当前源码工作树 | 唯一维护来源；修改前确认所在分支或 worktree。 |
| 已验证交付包 | 只读发布产物；新版本从当前源码重新构建。 |
| 源码快照与同步备份 | 放在源码仓库及插件扫描目录之外，并作为恢复材料只读保存。 |
| 历史归档与旧版运行目录 | 仅在明确迁移或审计任务中读取；不作为日常开发副本。 |
| 用户运行数据 | 迁移时按迁移流程读取；测试中使用隔离数据根，不能连接或改写生产数据。 |

不要在 AstrBot 插件扫描目录内创建开发副本。并行开发可使用分支或 Git worktree；若仓库使用链接 worktree，共享 Git 对象库是仓库的一部分，不要单独删除或移动。需要迁移工作树时，按 Git worktree 命令修复链接并确认状态后再继续。
