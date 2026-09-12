# 后台文本模型请求尝试次数

统一文本调用通道 `_llm_call`、`_llm_tool_call` 和 `_llm_generate_streaming` 按以下顺序选择最大尝试次数，包含首次请求：

1. 模型卡片的 `model_request_max_attempts_overrides`。
2. 插件的 `background_llm_request_max_attempts`。
3. AstrBot 的 `provider_settings.request_max_retries`。
4. 全局值缺失或无效时使用 AstrBot Provider 默认值。

插件值 `0` 和卡片留空均表示继承。快速模式使用当前快速模型卡片，精准模式使用任务卡片；切换模式不删除另一模式的配置。长创作、日程和每日巡视建议配置为 1 或 2。

该值通过 `request_max_retries` 交给支持此参数的 Provider。旧 Provider 不接收未知参数，调用记录会标记不支持。专用识图、TTS 转换及外部插件自行调用的模型不属于上述统一文本通道，面板不为这些卡片显示此项覆盖。

当长输出请求（`max_tokens >= 512` 或未指定）出现读取超时、Provider 超时或插件等待超时，插件不立即改用非流式或备用模型。同一人格、任务、Provider 和提示词的请求至少退避 60 秒，记录 `retry_after`；日程及每日巡视继续沿用各自更长的调度退避。该请求级退避保存在内存中，重新加载插件会清除。

Token 最近调用记录显示生效上限、配置来源、Provider 参数支持状态及退避时间。AstrBot 当前没有返回实际 Provider attempts，因此该计数为 `null`，页面显示“实际未知”。这里的上限不等于整个业务任务的 HTTP 请求总数：SDK 重试可能与 Provider 尝试次数相乘，密钥轮换、备用模型和任务分段也可能产生额外请求。实际计费次数应以服务商记录为准。
