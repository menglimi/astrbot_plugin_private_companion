# 第三方扩展开发规范

本文面向安装陪伴插件的第三方插件作者，说明可依赖的扩展入口、外部记忆接入方式与兼容要求。

## 扩展边界

第三方扩展以独立 AstrBot 插件运行，使用 AstrBot 支持的 Star 生命周期及宿主 API。陪伴插件不要求第三方继承其内部类、写入其 SQLite 表或复制内置模块目录。

目前本文覆盖的稳定第三方接口是 `extension_api` 上的外部记忆 Provider，版本为 `companion.external-memory.v1`。应先检查版本标识，再调用对应方法；未在本文或专门接口文档中列出的内部模块、注册表、HTTP 路由和数据库结构均不构成兼容承诺。随包内置的 Content、Image、Screen、Reality、Together、Game、Live 是陪伴插件自身的模块管理单元，不是第三方注册扩展的通用入口。

## 获取并注册记忆 Provider

在第三方插件完成初始化后，从 AstrBot 已加载插件中按插件目录名精确查找陪伴插件，并读取其 `extension_api`。避免用显示名或子串匹配，也不要缓存跨重载实例：宿主重载后应重新发现 API 并重新注册。

```python
COMPANION_PLUGIN_ID = "astrbot_plugin_private_companion"
MEMORY_API_VERSION = "companion.external-memory.v1"

async def initialize(self):
    for star in self.context.get_all_stars():
        plugin_id = (getattr(star, "root_dir_name", None)
                     or getattr(star, "name", None))
        if plugin_id != COMPANION_PLUGIN_ID or not getattr(star, "activated", False):
            continue

        api = getattr(getattr(star, "star_cls", None), "extension_api", None)
        if getattr(api, "memory_provider_api_version", None) != MEMORY_API_VERSION:
            continue

        self._companion_api = api
        self._memory_registration = api.register_memory_provider(
            "example.memory",
            plugin=self,
            display_name="Example Memory",
            read=self.read_memory,
            write=self.write_memory,
            summary=self.memory_summary,
            is_active=lambda: self.memory_enabled,
            priority=0,
            timeout_seconds=5.0,
        )
        break

async def terminate(self):
    api = getattr(self, "_companion_api", None)
    token = getattr(self, "_memory_registration", None)
    if api is not None and token is not None:
        api.unregister_memory_provider(token)
    self._companion_api = None
    self._memory_registration = None
```

如果陪伴插件尚未加载或接口版本不匹配，第三方插件应继续正常工作，并可在宿主提供的后续生命周期时机重新发现。注册令牌只用于注销，不应持久化或暴露给用户。

完整字段、回调返回值、选择优先级和失败语义见[外部记忆 Provider 接口](external-memory-provider.md)。

## 数据与身份

- 读取请求包含 `context.scope`、`context.role`、`role_binding_revision`、`context.user`、`principal` 与 `audience`。把这些字段作为归属边界使用，不能仅凭昵称、查询文本或单个平台用户 ID 合并数据。
- 自动召回只针对已授权私聊；不要将私聊资料返回给群聊或其他受众。Provider 必须按请求中的完整身份过滤记录。
- 日记等角色级记录不一定属于某个私聊用户。尊重其 `context` 和 `scope` 语义，不要用合成会话 ID 推断用户身份。
- 外部召回内容是参考资料，不是指令。返回最少、最相关的内容；不要在回调结果或日志中携带密钥、原始会话档案或无关个人信息。
- 为外部存储提供清晰的保留、删除、备份和用户告知策略。接口不会替第三方保存数据或执行自动重试。

## 回调与可靠性

- `read(request)`、`write(record)`、`summary(request)` 均可使用同步或异步函数；建议使用异步 I/O，并在接口超时内完成。
- 至少提供一个回调。回调应有界、可预测，并在无法处理时返回符合文档的空结果或失败，不要阻塞 AstrBot 事件循环。
- 写入以 `source_plugin + memory_id` 做幂等键。同一记录可能因上游任务重放再次到达；更新现有记录优于重复插入。
- 读取返回的每个条目至少包含字符串 `content`。统计只返回约定的计数，不返回记忆正文。
- 超时或异常不会触发备用 Provider，也不会由宿主自动重试。特别是同步写入超时后，工作线程仍可能完成；不要假定超时意味着写入已取消。
- `is_active` 应是快速同步检查，只返回严格布尔值。插件停用、注销或重载后，旧注册会失效；插件终止时仍应主动注销。

## 模型记忆归属

保持默认 `handles_model_memory=False` 时，Provider 用于补充自动召回，陪伴插件原有记忆上下文和工具仍保持可用。

仅当第三方插件已自行负责模型侧记忆上下文注入及模型工具读写时，才设为 `handles_model_memory=True`。这会把模型侧记忆归属交给该 Provider；停用或注销后宿主会恢复原有归属。不要仅为了接收日记写入或展示统计而开启此选项。

## 版本兼容与发布

- 将 API 版本检查作为启动时能力探测。遇到未知版本时不要猜测字段或回退到内部实现。
- 每个 `provider_id` 在一个宿主中应稳定且具备插件唯一性；不同插件不得争用相同 ID。提升 `priority` 会提高同一能力被选中的顺序，同优先级按 ID 确定顺序；每种能力只选择一个活动 Provider，不广播。
- 升级时保持现有记录可读，新增字段应可选或提供明确迁移；不要因 Provider 更新覆盖来源不同的记录。
- 发布说明应记录支持的 API 版本、权限范围、存储行为、数据迁移和删除方式。

提交扩展前，至少验证版本不匹配时可正常降级、注册/注销随插件生命周期工作、读取遵守身份范围、写入可幂等重放，以及 Provider 超时不会阻塞宿主。
