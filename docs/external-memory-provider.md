# 外部记忆 Provider 接口

接口版本为 `companion.external-memory.v1`，由陪伴插件实例的 `extension_api` 提供。第三方插件注册自己的回调即可，不需要使用指定的插件名、继承内部类或实现 MemoryCompanion 的旧桥。注册仅在当前进程和当前宿主生命周期内有效。

## 支持范围与归属设置

7.0.7 的兼容方式如下；“可注册”表示接口可用，不代表第三方已经实现接入。

| 记忆来源 | 接入方式 |
| --- | --- |
| 陪伴插件内置记忆 | 默认可用；外部模型侧记忆提供方活动时按归属规则让位。 |
| 我会牢牢记住你（MemoryCompanion） | 继续自动发现已有兼容桥；也可采用本接口。 |
| Hindsight 等其他记忆插件 | 作者可注册本接口；没有内置专用适配器，也不会仅凭安装名称自动连通。 |
| LivingMemory 旧工具名配置 | 7.x 不迁移旧 `livingmemory_tool_name`，需第三方改用公开接口；改名不能替代接入。 |

当前没有 Dashboard 的 `self / external / auto` 手动归属开关。内置记忆、旧桥和活动注册按文末规则实时判定。新 Provider 可用 `is_active` 或注销临时退出协作，不必停用整个第三方插件；旧桥是否支持单独关闭协作，需查阅对应插件设置。仅在第三方已自行负责模型侧记忆时使用 `handles_model_memory=True`。

## 注册和注销

在 AstrBot 已加载陪伴插件后，从 `context.get_all_stars()` 找到 `root_dir_name` 或 `name` 为 `astrbot_plugin_private_companion` 的启用项，读取它的 `star_cls.extension_api`。推荐在第三方插件的 `initialize()` 或 AstrBot 完成加载的生命周期钩子中注册。若陪伴插件随后重载，需重新取得新实例的 API 并注册。

```python
async def connect_memory(self):
    for star in self.context.get_all_stars():
        if (getattr(star, "activated", False)
                and "astrbot_plugin_private_companion" in (
                    getattr(star, "root_dir_name", ""), getattr(star, "name", ""))):
            api = getattr(getattr(star, "star_cls", None), "extension_api", None)
            if getattr(api, "memory_provider_api_version", None) != "companion.external-memory.v1":
                continue
            self._companion_api = api
            self._memory_registration = api.register_memory_provider(
                "my_plugin.memory",
                plugin=self,
                display_name="我的记忆插件",
                read=self.read_memory,
                write=self.write_memory,
                summary=self.memory_summary,
                is_active=lambda: self.memory_enabled,
                priority=0,
                timeout_seconds=5.0,
            )
            return True
    return False

async def terminate(self):
    api = getattr(self, "_companion_api", None)
    registration = getattr(self, "_memory_registration", None)
    if api is not None and registration is not None:
        api.unregister_memory_provider(registration)
```

`read`、`write`、`summary` 都可省略，但至少提供一个；回调接收一个普通字典，可以是同步函数或异步函数，建议使用异步函数。`is_active` 是可选的同步函数，返回 `True` 表示当前可服务。插件被停用、卸载或替换后，即使忘记注销，旧实例也不会再被调用。

`register_memory_provider()` 返回不透明的注销凭据。同一实例重复注册相同 `provider_id` 会替换旧注册，旧凭据不能注销新注册；仍在加载的其他插件不能占用同一 ID。`unregister_memory_provider()` 返回是否实际移除了注册。`list_memory_providers()` 返回 ID、展示名、能力、优先级和活动状态，不返回回调、插件实例或注销凭据。

每种能力分别选择活动 provider：优先级数值越高越优先，同优先级按 `provider_id` 排序。一条请求只调用一个 provider，不广播；选中后发生错误或超时，不改投其他 provider。没有已注册的对应能力时，原有 MemoryCompanion 桥继续可用。

## 读取

`read(request)` 在已授权私聊的 AstrBot 回复准备阶段调用，使用现有记忆开关、自动注入开关和上下文条数/字符预算。群聊不会调用这个私聊读取接口。返回的资料与内置记忆一起作为参考注入，不会成为新的指令。

```python
async def read_memory(self, request):
    identity = request["context"]
    rows = await self.store.search(
        identity=identity,
        query=request["query"],
        limit=request["limit"],
        max_chars=request["max_chars"],
    )
    return {"items": [
        {"id": row.id, "content": row.text,
         "occurred_at": row.occurred_at, "source": row.source}
        for row in rows
    ]}
```

请求包含：

| 字段 | 含义 |
| --- | --- |
| `schema` | `companion.external-memory.v1` |
| `source_plugin` | `astrbot_plugin_private_companion` |
| `purpose` | 自动召回为 `recall`，统计为 `summary` |
| `context.scope` | 完整会话身份：安装、平台、Bot、账号、原生会话、会话类型和窗口 |
| `context.role` / `role_binding_revision` | 当前角色身份与绑定修订 |
| `context.user` / `principal` / `audience` | 已认证用户、平台主体与原始可见受众 |
| `query` | 本轮查询文本；统计请求为空 |
| `limit` / `max_chars` | 本次资料预算；统计请求 `max_chars=0` |

返回 `{"items": []}` 表示本次没有找到资料。条目必须有字符串 `content`；`id`、`occurred_at`、`source` 为可选字符串，其余字段不进入模型提示。宿主只选取预算内的完整条目，超出预算的条目会略过。provider 应按请求身份过滤资料，不能只凭昵称、查询文本或相同平台用户 ID 跨角色、跨 Bot 合并记忆。宿主在等待结束后重新检查权限与注册状态，丢弃已失效的结果。

## 写入

`write(record)` 接收宿主已完成的日记、梦境和主动消息记录。当前接口不镜像内置记忆库的每次事实增删改，也不自动接管第三方的聊天记忆工具。

```python
async def write_memory(self, record):
    await self.store.upsert(
        source=record["source_plugin"],
        memory_id=record["memory_id"],
        identity=record["context"],
        kind=record["kind"],
        content=record["content"],
        metadata=record["metadata"],
    )
    return record["memory_id"]
```

记录包含 `schema`、`source_plugin`、`memory_id`、`kind`、`content`、`scope`、`session_id`、`platform`、`occurred_at`、`tags`、`metadata`、`confidence`、`importance` 和 `context`。

- `kind` 为 `persona_life` 或 `proactive_message`；日记/梦境的细分类型位于 `metadata`。
- `context` 与读取请求使用同一身份格式，宿主原生生产者会提供它。手动构造的旧记录可能为 `None`，provider 可拒收无法归属的记录。
- 日记是角色级资料，按 `context.scope.installation_id`、`context.scope.bot_id` 和 `context.role` 归属；其 `scope` 可为 `unknown`。不要把日记的合成 `session_id` 当作某位用户的私聊身份。
- `memory_id` 在同一记录重放时保持稳定。provider 应以 `source_plugin + memory_id` 幂等写入，保留时间、来源、归属和否定条件。通用接口不使用旧桥的 4000 字符截断。
- 成功返回非空记忆 ID，未保存返回 `None`。异常、超时或停用不会撤销已完成的宿主动作；宿主不缓存、不自动重试、不尝试第二个 writer。同步回调在线程中执行，超时不保证线程中的写入已停止。

## 统计与记忆归属

可选 `summary(request)` 接收 `purpose="summary"` 的同身份请求，返回 `{"counts": {"profile": 2, "preference": 3, "relationship": 1, "private_conversation": 4, "other": 0, "total": 10}}`。统计页面只接收这些计数，其余正文或调试字段会丢弃。读取失败显示不可用，不伪装成零条记录。

默认 `handles_model_memory=False`：宿主调用 `read` 注入补充资料，内置记忆及其工具继续工作。只有第三方已经通过 AstrBot 自行完成记忆上下文注入和模型工具读写时，才注册 `handles_model_memory=True`。这表示第三方负责模型侧记忆，宿主不再自动调用它的 `read`，并按现有归属机制让内置记忆注入和工具让位；`write` 和 `summary` 仍可使用。该 provider 停用或注销后，归属会在下次查询时恢复到旧桥或内置记忆。
