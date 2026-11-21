# 配置体验改进方案（2026-11-17）

对应五条反馈：功能解释、目标导向查找、简易模式、过程可解释、文档与界面同步。

## 一、现状盘点

| 反馈 | 现在有什么 | 缺什么 |
| --- | --- | --- |
| 1. 功能—作用—关联—影响 | `config-editor.js` 每个分组有 `labels` / `helps`，部分 help 已写“只影响…不影响…” | 没有结构化的“修改后影响 / 需同步调整 / 不修改的默认表现”；高影响项和普通项没有区分 |
| 2. 按目标查找 | `app.js` 已有全局配置搜索：`searchEntries()` 汇总各编辑器，支持标签、帮助、别名，结果可跳到页面并聚焦字段 | 没有“我想实现什么”的配方层；用户仍要先知道功能名 |
| 3. 简易模式 | 无 | 所有分组全量展示；没有常用项/预设模板 |
| 4. 过程可解释 | 保存失败显示 `errors[code]` 的中文文案 | 没有“为什么失败、下一步去哪、相关配置跳转”；依赖/冲突不预检 |
| 5. 文档与界面同步 | `docs/` 是 V1/V2 审计 CSV；README 是总览 | 没有面向用户的配置手册；没有从界面 spec 生成文档、也没有一致性校验 |

结论：第 2 条的地基已经有了，缺的是“目标层”；第 1 条有素材，缺结构；第 3、5 条基本从零开始。

## 二、分三相落地

### Phase 1：结构化解释 + 目标配方 + 可行动错误（不改配置语义）

**1.1 给高影响字段加 `impacts`**

在 `config-editor.js` 的 spec 里，为 15–20 个高影响字段补：

```js
impacts: {
  "automatic_enabled": {
    after: "关闭后后台不再自动发起联系；手动「消息测试」和回复不受影响。",
    sync: ["proactive.contact.enabled", "proactive.contact.quiet_enabled"],
    default: "默认开启；关闭后需要重新打开才会恢复自动联系。",
  },
}
```

界面在字段 help 下渲染三行（可折叠）：`调整后` / `需同步` / `默认表现`。只覆盖高频项，不追求全量。

**1.2 目标配方（recipes）**

在 `config-editor.js` 导出 `recipes`，每条：

```js
{
  id: "quiet-down",
  title: "让她少主动找我",
  keywords: ["少主动", "别打扰", "安静", "不要找我"],
  steps: [
    {group: "proactive.contact", section: "master", field: "automatic_enabled", note: "先关自动联系"},
    {group: "proactive.contact", section: "quiet", field: "quiet_enabled", note: "再设免打扰时段"},
  ],
}
```

首批 12 条：少主动、多主动、只回复不主动、群聊少插话、换语音、改日记时间、记忆整理频率、空间少发、陌生人处理、换模型、回复分段、备份/迁移。

全局搜索把 recipe 作为 `kind: "goal"` 的结果排在字段结果之前；点一条跳到第一步所在页面/分组并聚焦字段，步骤以清单形式展示。

**1.3 错误可行动**

给 `errors` 增加 `next`：

```js
configuration_revision_conflict: {
  text: "设置已被别处修改。",
  next: "重新读取后再保存；你填的值会保留为草稿。",
  jump: null,
},
invalid_contact_preference: {
  text: "联系偏好有一项不合法。",
  next: "检查免打扰时段、时区与暂停时间。",
  jump: {group: "proactive.contact", section: "quiet"},
},
```

保存失败时在错误文案下渲染“下一步”和“去修改”按钮。

### Phase 2：简易模式与预设

- 每个分组加 `essential: ["enabled", "automatic_enabled", ...]`。
- 配置页头部加「常用 / 全部」切换，状态存 `localStorage`；常用模式只渲染 `essential`，底部固定“显示全部设置”。
- 预设模板：`安静陪伴`、`主动一点`、`群聊克制`、`只回复`。应用前弹出 diff 预览（字段旧值 → 新值），确认后一次写入；不覆盖用户没勾选的字段。
- 默认策略建议：老用户保持“全部”，新安装默认“常用”；避免升级后设置突然消失。

### Phase 3：文档与界面同源

- 新增 `tools/build_config_reference.py`：从 `config-editor.js` 的 spec（labels/helps/impacts/sections）生成 `docs/CONFIG_REFERENCE.md`，每条带 `group/section/field` 锚点和界面路径。
- `--check` 模式比较生成结果与仓库文件，纳入发布前检查；spec 改动未更新文档时失败。
- 应用内每组标题旁加「查看说明」，链到生成文档的对应锚点；文档每条反向标注“在哪个页面/分组”。

## 三、验收标准

- 任选一个目标（如“让她少主动找我”），从搜索到改完不超过 3 次点击，且不需要知道字段名。
- 高影响字段页面上能直接看到“改完会怎样、还要动哪里、默认是什么”。
- 保存失败时，用户能看到原因、下一步和相关设置入口，不需要读代码或猜。
- `docs/CONFIG_REFERENCE.md` 与 spec 的字段集合、标签、分组路径完全一致（`--check` 通过）。

## 四、风险与前提

- 配置项数量大，`impacts` 与 `essential` 必须人工审；建议先覆盖使用频率最高的 20%，其余按反馈补。
- “简易模式默认开/关”是产品决策：默认开会让老用户以为设置丢了，默认关则新用户仍面对全量。建议新安装默认开、老用户保持原状。
- 线上运行库是 v70、当前代码到 v68；文档与界面映射要以实际加载的构建为准。
