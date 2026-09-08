# WaifuBot 记忆框架借鉴评审

> 这是一份外部经验评审，不是新框架契约。WaifuBot 的主程序为打包应用，以下结论依据其角色配置、记忆文件和运行参数整理。

## 1. 值得吸收的经验

WaifuBot 将记忆按用途拆开：

| WaifuBot 数据 | 新框架中的对应物 | 处理方式 |
| --- | --- | --- |
| `short_term_memory.json` | `SceneState` / `SessionRecord` / `ContextContribution` | 会话级滚动上下文，按预算和 TTL 淘汰，不作为长期事实 |
| `memories.json.long_term` | Memory 的派生摘要投影 | 保留来源事件、摘要边界和版本，可重建、纠正和失效 |
| `important_events.json` | `MemoryProposal` / `MemoryAtom` | 只把承诺、偏好、关系变化等可延续事件提交为事实 |
| `role.yaml`、世界观配置 | Persona / World owner | 与记忆库分离，不由聊天摘要覆盖 |
| `tags_index` | 可替换的召回索引 | 只做候选召回，不作为事实语义或权限依据 |

它还提供了几个实用的产品经验：按用户/群作用域隔离、批量整理短期消息、限制长期召回数量、支持清空记忆，以及让摘要和角色设定共同参与回复上下文。

## 2. 不直接照搬的部分

- 自动生成的标签包含大量泛词，不能作为唯一检索算法。
- 长期摘要不能脱离来源证据成为权威事实。
- 定时提醒不应伪装成普通聊天历史，应由 Task/Calendar 持有。
- 群聊中的第三方发言不能自动归入当前用户档案。
- 多个 JSON 文件适合可读性和备份，不足以提供可靠写入、幂等、撤回和并发恢复。

## 3. 对当前框架的补充

当前设计已经有 `MemoryContextPolicy`、`ContextContribution`、提议/事实分离和摘要任务元数据。需要在实现时落实以下最小约束：

1. 短期上下文与长期事实使用不同的 owner 和生命周期。
2. 摘要任务保存 `last_summarized_event_id`、`summary_boundary`、`summary_version`、`source_event_ids` 和 `batch_id`。
3. 重要事件使用标准 Memory 提议写入，不建立第二套事件数据库。
4. 检索预算至少同时限制条数、字符/token 总量和作用域；标签索引只能帮助召回。
5. 用户能够查看、纠正、撤回和清理记忆，清理必须传播到摘要、投影和缓存。

## 4. 对 Content 的落地

Content 只吸收“作品连续性”部分：

- `story_bible` 作为当前作品摘要和主线投影；
- `creative_memory_pool` 作为作品内的有界片段记忆；
- 人工大纲、角色表和修订永远优先于外部摘要；
- 续写召回同时考虑关键词、重要性和新鲜度；
- 跨作品或跨会话的长期事实通过 Memory 提议接口写回，并携带 `project_id`、`project_revision` 和来源证据。

这样保留了 WaifuBot 的连续感，同时避免把聊天记录、角色设定和作品记忆混成一张不可治理的历史表。
