# 世界模拟契约包

> `companion.world@1`，修订 `0.1.0`，状态 `review`。这是设计期机器契约，不是生产 SDK，也不创建第二个记忆库。

本包固定世界模拟的最小边界：`WorldEntity` 保存人格世界中的可查询对象；`WorldEvent` 只表示 World owner 已提交的变化或可幂等重放的边界事件；`WorldRequest/Result` 覆盖模型查询、状态变更、活动推进和事件重发。活动过程本身由[日历与持续生活契约包](../../calendar/v1/README.md)的 `ActivityProcess/checkpoint` 负责。

每个实体和事件都带 `owner_ref`、完整 `RuntimeScope`、来源证据、`reality_mode`、可见性和 revision。`global_safe` 是明确的公共投影，不是把私聊或现实用户数据放进全球世界；`simulated` 不得被表达成现实观测。`world_revision` 是世界水位，`process_revision` 只保护单个长活动，不能用一个版本锁住无关活动。

## 运行边界

- `world.model.query` 只返回按用途、受众和字节预算裁剪的快照，不隐式提交状态。
- `world.state.transition` 通过 owner 校验的提议提交实体或关系变化。
- `world.activity.advance` 只在互动、承诺边界、外部观测、主动评估或 checkpoint 到达时推进；默认 `max_model_calls=0`。
- `world.event.publish` 只能发布/重发 owner 已提交事件，重发保持原 `event_id`，不能伪造新提交。
- 成功回执必须带 `committed=true`、新 revision 和有界 `changed_refs`；响应丢失按幂等键查账。

`world.fixture.json` 的 WMS-01--08 覆盖来源、revision、幂等、模拟/现实边界、三种共享、响应丢失、修订失效和资源上限，当前均为 `not_run`。
