# 新的她工作区

这里的 `workspace.html`、`app.js`、`style.css` 是可编辑源文件。运行 `python -X utf8 tools/sync_framework_page.py`，生成陪伴小屋内的 `designer` 工作区和按需加载包；不要手改生成副本。

入口位于「自我 → 人格再造」，与原「人格问卷」并列。配色通过既有工作区主题投影注入，布局继续使用小屋的主题、字号、密度与圆角设置。

- iframe 内负责展示、编辑和共创。模型返回内容始终作为纯文本显示。
- 小屋父页面的 `persona-designer-memory.js` 只维护当前页面内存和请求回执，内部切页或切换问卷后可恢复；离开扩展页时清空。此功能不使用浏览器存储自动保存。
- 保存按钮才会将当前模式的完整文档发往独立的 `persona_designer/drafts.sqlite3`，按 Dashboard 账号隔离并追加版本。读取版本会恢复模型选择、输入、对话、阶段与编辑稿。
- `/persona-designer` 接口在主体路由注册成功后单独挂载。模型请求有独立的并发、停止与超时控制，存储操作在线程中执行，不占用主体控制锁。
- AstrBot 人格与模型配置只读，成品由用户复制填写。旧问卷保持原有流程。

领域与宿主合同测试分别是 `tests.test_persona_designer`、`tests.test_persona_designer_api`；父页面桥接测试位于 `tests/test_product_workspaces.cjs`。安装可选的 Playwright 和 Chromium 后，可运行 `tests.test_persona_designer_browser`，用模拟宿主验证真实小屋入口、切页、失败恢复和移动端布局。所有测试均使用合成数据，不调用真实模型或机器人账号。
