# 本地与远端差距审计

> 导航：[设计总纲](./FRAMEWORK_DESIGN.md) / [主题目录](./FRAMEWORK_DESIGN_INDEX.md)。定位：仓库状态快照；反映审计时点，不代表本次整理后的文件状态。

审计时间：2026-09-07。已对各仓库执行 `git fetch --all --prune`，以下“同步”均指当前检出的分支与其 `origin` 跟踪分支的提交图一致。

## 1. 仓库状态

| 插件 | 当前分支 | 跟踪分支 | 提交差异 | 工作树 |
| --- | --- | --- | --- | --- |
| private_companion | `codex/release-6.6.0` | `origin/codex/release-6.6.0` | 0 / 0 | 5 个已跟踪文件修改，20 项未跟踪路径 |
| remember_you | `main` | `origin/main` | 0 / 0 | 1 个已跟踪文件修改，5 项未跟踪路径 |
| content_companion | `main` | `origin/main` | 0 / 0 | 1 个已跟踪文件修改，5 项未跟踪路径 |
| image_companion | `main` | `origin/main` | 0 / 0 | `dist/` 未跟踪 |
| reality_companion | `main` | `origin/main` | 0 / 0 | 5 个已跟踪文件修改 |
| bug_companion | `main` | `origin/main` | 0 / 0 | 干净 |

`together_companion` 和 `live_stream_companion` 当前目录没有 `.git` 元数据，因此无法从本地快照计算提交差异；其 metadata 中记录的远端分别为 `astrbot_plugin_together_companion` 和 `astrbot_plugin_live_stream_companion`，远端 `main` 当前可见 HEAD 分别为 `4777326c`（0.8.3）和 `8ccf0dec`（1.8.1）。这两个目录应视为“无版本锚点的本地部署副本”，本次已进一步做文件级对照，但没有把本地内容伪装成某个提交，也没有覆盖本地文件。

private companion 的发布分支同时包含 `origin/main` 的全部历史，并比 `origin/main` 多 19 个提交；这 19 个提交已经存在于远端的 `origin/codex/release-6.6.0`，不是本地独有内容。image companion 的 `pr-head` 远端分支比当前 `main` 落后 31 个提交，不应作为当前基线。

## 2. 本地独有内容

private companion 的未跟踪内容主要是本轮设计和审计稿，包括架构重建、状态闭环、记忆契约、主动偏好问卷深度分析和设计与现有插件对照；根目录 `README_DESIGN_PACK.md` 是设计包入口；`proactive.py` 的修改是主动提示层的自适应接触指导。它们尚未进入任何远端分支。

remember_you 的未跟踪内容是记忆精度审查脚本、分析数据和报告；content_companion 的未跟踪内容是问卷/腾讯文档抓取和分析文件；image_companion 的 `dist/` 是本地构建产物。这些内容不会随远端代码更新自动同步，也不应直接视为插件运行时的一部分。

reality_companion 有 5 个已跟踪文件的本地修改（移动端遥测扩展及测试），当前不在 `origin/main`。它们与本次主动偏好设计无直接关系，合并前应单独确认。

按工作树相对跟踪分支的文本差异计，private companion 为约 `+201/-52` 行，reality companion 为约 `+116/-5` 行；remember_you 的 README 为 `+8/-4`，content_companion 的 README 为 `+4`，image_companion 和 bug_companion 没有已跟踪文件差异。行数只反映当前工作树改动规模，不等于功能完成度，也不包含未跟踪文件。

## 3. 设计与运行代码的差距

设计稿已经明显超前于远端发布代码：`ProactivePreference` 多轴偏好、接触成本、形式路由、对话占用态、分级授权和解释回执主要仍是目标设计；当前运行代码已有主动路线、未回应减速、候选去重、占用态和诊断入口，但尚未把这些设计统一成独立的偏好投影和跨插件协议。

因此当前安全策略是：先提交/保存设计与审查产物，再按垂直切片实现；不要把未跟踪设计文件误当成远端已发布功能，也不要在同步远端时覆盖本地审查稿和 reality companion 的修改。

## 4. 同步建议

1. private companion 以 `origin/codex/release-6.6.0` 作为当前运行基线；设计稿和 `proactive.py` 修改另开提交或分支。
2. remember_you、content_companion 和 image_companion 先把分析/构建产物与源码分开管理，避免把临时数据推入主分支。
3. reality companion 先对 5 个本地修改做功能测试和提交归属确认，再决定是否推送。
4. 后续对照远端时同时检查跟踪分支和发布分支，不能只比较 `origin/main`。

## 5. 无 Git 副本的文件级对照

为避免“没有提交差异”被误读成“没有代码差异”，本次以本地目录和浅克隆的远端 `main` 做了 UTF-8 文本归一化后的路径/内容比较，并排除 `.git`、缓存、测试缓存、运行数据和构建依赖目录。结果只用于审计，不会自动同步。

### together_companion

- 本地约 48 个可比文件，远端约 43 个；本地独有 5 个有意义的路径，远端没有本地缺失的对应源码；17 个共同文件存在内容差异（其中一部分是换行差异，已在归一化比较中剔除）。
- 本地 metadata 为 0.9.0，远端 `main` 为 0.8.3；远端只看到 `main` 和 v0.8.3 标签，没有发现可直接对应本地 0.9.0 的公开分支或标签。
- 本地独有的主要内容是 `novel_library.py`、小说房间/实时连续性测试，以及与之配套的 `main.py`、`server.py`、`models.py`、`_conf_schema.json`、房间页面和文档改动。
- 这组本地改动不是简单的版本漂移：它加入了小说库导入与检索、章节边界/进度、无剧透提示、房间身份头、短期实时连续性同步和 `open_together_reading_room` 工具，并扩展了房间状态模型。它们已经构成一个新的功能切片，不能用远端 0.8.3 直接覆盖。
- 建议把当前目录先导出为带日期的源码快照或初始化独立分支，再决定是否以“小说共读”垂直切片提交到远端；小说正文、进度和运行数据仍应与源码分开保存。
- 已生成 `C:\Users\99505\Downloads\companion_gap_manifests_20260907\together_companion.json`，记录 48 个源码/配置/测试文件及远端基线提交，排除了运行数据和缓存。

### live_stream_companion

- 本地约 117 个可比文件，远端约 63 个；本地独有 63 个，远端独有 9 个，共同文件中 29 个存在内容差异。比较时排除了 `desktop_pet/node_modules`、构建输出、保护目录、缓存和运行数据。
- 本地与远端 metadata 都是 1.8.1，但本地没有提交时间线，不能据版本号推断两者相同。远端 `main` HEAD 为 2026-08-10 的 `8ccf0dec`，只发现 `main`，没有更高版本公开分支。
- 本地独有内容包含 `twitch_mixin.py`、扩展的 Soullink/场景/配饰映射、`desktop_pet` Electron 工程、配置迁移脚本和桌面宠物测试/截图；这些内容的规模已经超过一次小补丁，属于未锚定的本地产品树。
- 远端独有的根目录 `clients/*`、`models/*` 是与嵌套 `blivedm` 实现并存的供应商式副本；本地运行代码主要使用嵌套包，不能仅凭“远端多 9 个文件”判定本地缺功能，需要逐个做导入可达性检查。
- 本地 `main.py`、`page_api.py`、`soullink_mixin.py` 已包含 Twitch、VTS 恢复、连续情绪参数、场景编辑和外部主动能力等扩展。合并前应先建立源码快照，分别审查直播事件协议、主动能力注册和桌面宠物 IPC，避免把生成物或保护目录一并提交。
- 已生成 `C:\Users\99505\Downloads\companion_gap_manifests_20260907\live_stream_companion.json`，记录 116 个可发布源码/配置/测试文件；`desktop_pet` 的构建产物、依赖、保护模型和运行数据未写入清单。

## 6. 远端分支与发布基线

- private companion 当前检出 `codex/release-6.6.0`，与其跟踪远端 0/0；该分支比 `origin/main` 多 19 个提交，但这 19 个提交已经在远端发布分支上，不属于本地独有改动。
- remember_you 当前 `main` 与 `origin/main` 0/0；可见的 `origin/codex/core-memory-1.10.0` 比当前 main 少 48 个提交，不能作为“更新版”基线。当前记忆精度脚本、数据和报告仍是本地未跟踪内容。
- image companion 当前 `main` 与 `origin/main` 0/0；可见的 `origin/feat/unified-image-engine` 比当前 main 少 28 个提交，旧 `pr-head` 也落后，不应回退到这些分支。
- content、reality、bug 三个仓库的检出分支均与 `origin/main` 0/0；reality 的 5 个已跟踪移动端/小说房间改动是工作树修改，不代表远端已有。
- Together 和 Live 没有本地 Git 元数据，远端也没有发现能解释本地新增功能的公开分支/标签。它们必须先建立可追溯基线，才能进行提交级同步、回滚或发布。

## 7. 风险分级与下一步

**P0：先保留、再锚定。** 不要对 Together/Live 执行覆盖式拉取，也不要清理 reality 的工作树。两个无 Git 副本的源码快照清单和 SHA-256 manifest 已生成；运行数据、缓存、`dist`、`node_modules`、截图和日志仍单独归档。

**P1：按功能切片复核协议。** Together 先审“小说共读”与 private/memory/reality 的记忆、房间身份和进度契约；Live 先审 Twitch、Soullink、主动开播/下播和 desktop_pet IPC。确认协议后再创建分支和提交，避免把部署目录的临时状态当成公共 API。

**P2：整理可发布边界。** private、remember_you、content 的设计/调研稿可以继续保留在本地设计包，但应与运行源码分开提交；image 的 `dist` 和各仓库的抓取 JSON/HTML 不应进入插件发布包，除非明确标记为复现材料。

本次审计没有修改任何缓存、记忆库、房间数据或直播运行数据，也没有执行合并、推送或远端覆盖操作。下一轮工作应以“建立快照基线 + 选定一个垂直切片”开始，而不是继续扩大未锚定目录的差异。
