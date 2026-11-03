# NAI 生图后端接入

## 登记与调用入口

- `astrbot_host/native_extensions/image_native/output_routing.py` 按文生图、改图各自保存的队列顺序选择后端。NAI 使用已经加载的 NAI 插件公开接口，不复制密钥或地址。
- 图片能力仍由内置 `image.native` 登记、授权和保存产物。NAI 的付费调用在 `companion_v2/model_gateway/images.py` 的 `IMAGE_PROVIDERS` 中登记为 `image.nai`，支持文生图及接口实际允许的参考图改图，不登记为文字模型。
- 可用操作优先读取 NAI 联动 API 的 `operations` 声明；旧版接口未声明时，根据 `reference_supported` 为 OpenAI 和 NovelAI 两种接入保留参考图改图。`call_mode` 保留真实接入方式，不把 NovelAI 伪装成 OpenAI。传输协议和改图入口可用均不代表具体模型支持精准参考；型号限制仍由 NAI 插件在提交前校验。
- 原生聊天工具与陪伴回复中的图片工具均携带原始子任务的运行凭证。`NativeExtensionRegistry` 经 `LocalModelGateway.image_session` 建立一次性调用边界，由图片适配器传给 NAI 后端；运行凭证和回调不进入 JSON、提示词、持久化回执或第三方请求。
- 网关为原始子任务建立调用计划，并经过现有 `BudgetLedger.reserve/dispatch/settle`。调用记录、身份账号索引、耗时、固定失败分类和响应诊断使用同一套网关台账。重启或结果未知后不能重开同一调用。

## 优先级与回退

显式配置的文生图、改图队列各自从上到下尝试，NAI 不自动插队。原有自动模式仍按在线模型、ComfyUI、SD WebUI 的顺序工作，不自动启用 NAI；必须明确选择 NAI 或把它加入队列。

NAI 未安装、已停用、缺少联动接口或不支持当前操作时，跳过该后端。插件明确返回联动已关闭、并保证未执行请求时，也可尝试下一个后端。

请求已经提交后的超时、连接中断、空图片结果、结果文件丢失和取消都不能证明上游没生成或没收费，记录结果未知，不切换后端重画。旧版 NAI 接口只有错误说明、没有结构化的执行回执时，不根据错误文字猜测是否可以重试，也不把说明中的密钥、网址或提示词写入诊断。

## 额度与费用

- 继续使用现有每日图片额度，按请求者、角色、场景及角色日历时区统计。原生聊天和陪伴回复共用额度；一次确认成功的图片操作计一次，失败和结果未知不占成功次数，重复读取同一结果不重复计数。
- 图片授权、角色绑定和原任务权限继续在提交前检查；达到图片额度时，不调用 NAI 插件。
- 当前 NAI 联动接口没有真实 Token 用量、费用回执或单次费用上限。成功与结果未知的网关记录保留实际费用为空，不能显示成免费，也不能按图片数量猜测文字 Token 或美元金额。
- 未开启金额硬限制时，网关仍登记、记录和结算调用状态；零预留仅表示没有可用估价，不是实际费用为零。启用现有金额硬限制时，因无法证明单次费用上限，在提交前拒绝 NAI 请求，诊断为 `image_usage_unavailable`，不偷偷绕过限制或自动改走其他收费后端。
- 只有已经证明未执行的请求，才可记录零消费。当前接入不查询余额，不增加收费探测、提示词预检、语言检测或模型改写。

## 固定失败分类

| 图片诊断 | 网关分类 | 结果与重试 |
| --- | --- | --- |
| `nai_plugin_unavailable` | `provider_recheck_failed` | 提交前不可用或明确未执行时可换后端 |
| `unsupported_image_parameter`、`invalid_image_parameter` | `request_preparation_failed` | 本地参数错误，不提交 |
| `image_usage_unavailable` | `request_preparation_failed` | 金额硬限制下缺少费用上限，停止 |
| `nai_generation_timed_out` | `provider_wait_timed_out` | 提交后未知，不重复生成 |
| `nai_generation_failed` | `provider_request_failed` | 提交后未知，不重复生成 |
| `nai_generation_unconfirmed`、`nai_output_unavailable` | `native_response_parse_failed` | 无可确认产物，不重复生成 |
| 取消请求 | `provider_wait_interrupted` | 提交后未知，不重复生成 |
| 权限撤销、模块停止 | `request_recheck_failed` | 依据是否跨过提交边界记录，禁止继续调用 |

固定诊断同时进入现有图片调用记录，前端由页面 PR 翻译为中文。成功图片的采用、归属和发送仍由原有图片能力负责。

## 验证范围

`tests/test_nai_gateway.py` 验证网关登记、账号归属、真实费用保持未知、金额限制、固定失败分类、取消和禁止重放；`tests/test_nai_backend.py` 验证插件发现、临时参考图、确定未执行时回退、未知结果禁止回退及诊断不泄露第三方说明；`tests/test_native_image_flow.py` 验证原生聊天与陪伴回复两条真实调用链的台账、额度和图片采用。
