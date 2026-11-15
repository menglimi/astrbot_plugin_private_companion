# 服务余额：NewAPI 与高级脚本

入口为「陪伴小屋 → 此刻，对话能读到什么 → 余额感知」。配置数据源后，还需在「主动联系 → 外部触发源」中开启余额感知，并按需要开启后台自动联系。手动查询或草稿测试不会自行开启主动联系。

## 两种模式互斥

默认使用原有 NewAPI 表单。开启其下方的「启用高级设置」后，上方表单全部禁用，只使用高级区的独立地址、API Key、脚本和代理；配置错误不会回退旧来源。关闭高级模式则恢复旧 NewAPI 行为，包括用户 ID、额度除数和已知域名兼容处理。

切换后点击「保存数据源」才会应用到后台。两套已保存配置独立保留；未保存草稿可先「测试连接」。高级模式关闭时不会执行或校验闲置的高级脚本。

## 从 AstrBot 供应商读取连接配置

高级区提供「从 AstrBot 供应商读取余额（地址、API Key 和代理）」开关，默认关闭以兼容原有手动配置。

- 开启：下拉框默认「跟随 AstrBot 默认模型所属供应商」，也可明确选择其他聊天模型供应商。列表按供应商来源去重，不按同一供应商下的各个模型重复列出；兼容旧版直接把连接信息放在模型上的配置。
- 后端每次查询读取该供应商的 `api_base`、`key` 和 `proxy`，不将这些值复制进插件配置，也不向页面回传 Key 或代理。多 Key 列表使用首个非空、可解析的 Key；支持 `$ENV_NAME` 和 `${ENV_NAME}` 环境变量引用，不进行余额接口的 Key 轮询。
- 「跟随默认」基于 AstrBot 默认配置中的模型归属，不跟随当前聊天窗口的临时模型切换。明确选择后固定该供应商，不受默认模型变更影响。
- 关闭：恢复手动基础地址、API Key 和专用代理，原先保存的手动配置及当前页面草稿会保留。开关或选择变化均需保存才作用于后台；测试连接只测试草稿。
- 无代理表示直连；有代理就沿用，代理无效或不可达不会退回直连。供应商被删除、禁用、缺少地址或 Key 时查询失败，不回退手动连接或其他供应商。仍遵循现有 HTTPS 地址与 HTTP CONNECT/SOCKS5 代理校验。
- 查询脚本仍由管理员提供；脚本中的 `{{baseUrl}}`、`{{apiKey}}` 使用所选来源值。API 基础地址常含 `/v1`，余额接口路径未必相同，请按供应商实际接口调整脚本。
- NewAPI 的 `{{accessToken}}` 与 `{{userId}}` 继续独立配置；模型 API Key 通常不能替代带 `profile:read` 权限的 NewAPI 管理令牌。绑定不会自动发现余额接口，也不会自动转换管理令牌。

## 脚本格式

高级区默认填入通用模板（`GET {{baseUrl}}/user/balance`，读取 `response.balance`，单位 USD）。通用模板用 `response.is_active !== false` 保留明确的账户无效状态，避免 `|| true` 将 false 覆盖。

内置预设下拉框提供「通用 / newapi / sub2api」，选定后点击「载入」才会替换脚本；「恢复默认」固定恢复通用模板。两种操作都只更新草稿脚本，保留地址、凭据和代理，不自动保存。所有模板的 User-Agent 使用插件标识 `astrbot_plugin_private_companion`。

参考 CC Switch 的同步 `request + extractor(response)` 对象表达式，整体保留括号：

```javascript
({
  request: {
    url: "{{baseUrl}}/v1/user/balance",
    method: "GET",
    headers: { "Authorization": "Bearer {{apiKey}}" }
  },
  extractor: function(response) {
    return {
      isValid: response.success !== false,
      remaining: response.data.display.remaining,
      total: response.data.display.total,
      used: response.data.display.used,
      unit: response.data.display.unit
    };
  }
})
```

例如响应 `{"success":true,"data":{"display":{"remaining":12.3,"used":4.5,"total":16.8,"unit":"CNY"}}}` 将得到余额 `12.30 CNY`。

基础地址映射到 `{{baseUrl}}`，高级 API Key 映射到 `{{apiKey}}`，独立的 NewAPI 访问令牌、用户 ID 分别映射到 `{{accessToken}}`、`{{userId}}`。变量在脚本字符串字面量中以 Unicode 转义展开，保留数据而不允许引号、反斜杠或模板插值变成代码；随后可正常执行 `.replace()` 等字符串运算。基础地址可留空并在脚本中填写完整 HTTPS URL。请不要在脚本里硬编码密钥；脚本内容本身可由管理员重新读取，独立的 API Key 与代理字段不会回显。

`request` 支持 `url`、`method`、`headers`、可选字符串 `body`。方法可为 GET、POST、PUT、PATCH、DELETE、HEAD、OPTIONS，默认 GET。发送 JSON 时显式设置 `Content-Type` 并构造字符串 body；需要变量的 JSON 请求体可用 `JSON.stringify({key: "{{apiKey}}"})` 构造，以正确编码引号等字符。请求 URL 仅允许 HTTPS，不允许内嵌账号密码、片段和自动重定向。

`extractor` 接收响应 JSON，返回单个同步对象：

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| `isValid` | boolean | false 表示账户无效，不产生余额事件 |
| `invalidMessage` | string | 管理页面显示的无效原因 |
| `remaining` | number | 最终剩余金额，优先使用 |
| `unit` | string | 页面与余额联系使用的单位，最多 24 字符 |
| `total` | number | 总额，也用于原有百分比阈值 |
| `used` | number | 已用量；缺少 remaining 时与 total 相减，最低为 0 |
| `planName` | string | 仅供管理页面显示的套餐名 |
| `extra` | string | 仅供管理页面显示的补充信息 |

以上字段可省略或为 null，但余额链路需要有限、非负数值的 `remaining`，或同时提供 `total`、`used`。数字字符串请在脚本里显式 `Number(...)` 转换；无金额不会当作零余额。高级金额不再套用旧 NewAPI 除数。若查询 NewAPI，载入 newapi 预设并填写高级区独立的访问令牌、用户 ID；预设查询 `/api/user/self`，对 quota、used_quota 除以 500000，同时返回套餐及总额。sub2api 预设先移除基础地址末尾斜杠及 `/v1`，再请求 `/v1/usage`；优先使用 remaining，缺失时由 quota.limit 与 quota.used 计算，单位优先 response.unit、再 quota.unit、最后 USD。

返回的金额转换为原有 `BalanceObservation`，继续使用原来的查询间隔、阈值、冷却、发送前复核和联系管路。套餐名与 extra 不自动进入联系消息。未返回 unit 时，页面沿用“额度”，联系沿用原有角色单位设置。

## 余额专用代理与凭据

高级区可填写 `http://host:port` 或 `socks5://host:port`，也可使用 `scheme://user:password@host:port` 认证；特殊字符需 URL 编码。SOCKS5 使用代理端 DNS。代理仅影响余额请求，失败不会改为直连，不修改进程或其他模型的代理。

已保存的高级 API Key、NewAPI 访问令牌、代理地址留空表示保留，输入新值表示更换；勾选对应的清除项表示删除。清除代理后改为直连。移除整个页面数据源会恢复既有环境变量来源选择。配置文件中的凭据仍为原有配置存储方式，这不代表加密存储。

## 运行边界

安装依赖包括 `quickjs-ng` 和 `aiohttp-socks`，不需要系统 Node。只有开启高级模式才加载 JS 运行时；缺少依赖时应安装插件依赖后重试，不会回退旧数据源。

支持 ES2020 语法；不提供 Node、浏览器 fetch、文件、进程、Python 回调或模块导入。请求由宿主执行，脚本不能自行联网。首版不支持 Promise、异步提取和多账户数组。对象表达式在构造请求、提取响应时分别求值，请保持确定性，不依赖跨阶段状态。

脚本最多 16 KiB，每阶段最多 500 毫秒、16 MiB 内存与 256 KiB 栈；请求体最多 16 KiB，响应最多 64 KiB，请求超时 6 秒。语法错误、超限、无效账户、缺失金额或网络错误显示为不可用，不会合成低余额读数。
