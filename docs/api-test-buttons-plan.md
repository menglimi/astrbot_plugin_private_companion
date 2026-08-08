# WebUI 外部 API 测试按钮完整方案

## 目标

在插件 WebUI 中为以下能力提供独立的测试请求按钮：

- 天气 API
- 余额接口
- 主动搜索接口

主动搜索和群黑话联网参考必须复用同一套搜索实现，不新增第二套搜索链路。

## 一、统一后端测试入口

继续复用现有接口：

```text
POST /astrbot_plugin_private_companion/page/troubleshooting/test
```

新增三种测试类型：

```text
weather_api
balance_api
web_search
```

三个类型统一沿用现有排障诊断能力：

- `request_id`
- `test_id`
- `steps`
- `test_status`
- `error_code`
- 错误分类和重试建议
- 敏感信息脱敏
- 测试历史持久化
- WebUI 测试诊断弹窗

建议在 `run_troubleshooting_test()` 中增加分派分支，并为三种类型增加标题映射。

## 二、天气 API 测试

### 真实请求入口

测试直接调用：

```python
plugin._fetch_own_weather_prompt()
```

该入口会根据 `weather_source` 分派到：

- 和风天气
- Open-Meteo
- 高德天气
- OpenWeatherMap

### 副作用约束

测试不能调用 `_ensure_weather_context()`，因为该方法会更新 `daily_weather` 缓存并触发持久化。

测试请求应使用临时配置副本，不修改正式插件配置。和风天气地点解析产生的临时缓存也应隔离或恢复。

### 成功结果

```json
{
  "ok": true,
  "type": "weather_api",
  "title": "天气 API 请求测试",
  "provider": "qweather",
  "location_label": "北京",
  "detail": "当前天气 晴，约 26°C。",
  "steps": []
}
```

### 失败判定

天气底层方法可能把 HTTP、网络和解析异常转换为空结果，因此以下情况都应明确判定为失败：

- 返回值不是对象
- `prompt` 为空
- `source` 为空
- 地点配置缺失
- 请求超时

错误信息必须经过页面诊断脱敏，不能泄露天气 Token、OpenWeather Key 或完整请求 URL。

## 三、余额接口测试

### 真实请求入口

测试直接调用只读方法：

```python
plugin._fetch_balance_snapshot()
```

支持两种来源：

- 用户填写的自定义余额 URL
- 自动探测 AstrBot Provider 的余额端点

### 副作用约束

测试不能调用 `_maybe_refresh_balance_awareness()`，避免修改余额状态、失败冷却时间、下次检查时间或主动消息候选。

### 成功结果

```json
{
  "ok": true,
  "type": "balance_api",
  "title": "余额接口请求测试",
  "query_mode": "manual",
  "source_id": "custom",
  "endpoint_path": "/balance",
  "amount": 12.5,
  "total": 100,
  "used": 87.5,
  "remaining_percent": 12.5,
  "currency_label": "元"
}
```

### 安全要求

- 使用 `_balance_safe_error()` 处理底层异常。
- URL 只保留安全的主机和路径摘要。
- 不返回 API Key、Authorization 或自定义请求头。
- JSON 解析失败、字段缺失、HTTP 4xx/5xx 和超时都要生成可操作诊断。

## 四、主动搜索与群黑话联网参考

### 统一请求入口

测试只调用：

```python
plugin._run_astrbot_web_search(
    query,
    umo=umo,
    topic=topic,
    usage="web_exploration",
)
```

建议请求体：

```json
{
  "type": "web_search",
  "query": "北京今天有什么新闻",
  "topic": "general",
  "umo": "",
  "usage": "web_exploration"
}
```

### 复用关系

- 配置了自定义搜索接口时，测试实际验证自定义接口。
- 未配置自定义接口时，测试走 AstrBot 搜索 Provider。
- 主动搜索运行时和群黑话联网参考继续调用同一个 `_run_astrbot_web_search()`。
- 不调用 `_collect_group_slang_web_evidence()`，避免写入群黑话缓存。

### 结果展示

测试只返回有限结构化信息：

- 搜索 Provider
- 结果数量
- 首条或前几条标题
- 受限长度的摘要
- 失败原因和冷却提示

不直接把完整搜索响应、请求头或 API Key 返回到 WebUI。

## 五、未保存配置的处理

按钮点击时读取当前表单中的未保存值，并通过请求体提交临时配置：

```json
{
  "type": "weather_api",
  "settings": {
    "weather_source": "qweather",
    "weather_api_host": "https://tenant.example",
    "weather_token": "临时凭据",
    "weather_location": "北京"
  }
}
```

后端只在测试副本中应用允许的字段：

- 不修改插件正式属性。
- 不写配置文件。
- 不污染天气、余额和搜索运行时状态。
- 测试结束后自动恢复。

临时 API Key、Token 和自定义请求头只用于当前请求，所有异常文本都要二次脱敏。

## 六、诊断契约扩展

诊断公共类型增加：

```text
weather_api
balance_api
web_search
```

安全结构化字段建议增加：

```text
provider
source
location_label
query_mode
source_id
endpoint_path
amount
total
used
remaining_percent
result_count
result_preview
```

字段约束：

- URL 只保留协议、主机和路径。
- 查询词、标题和摘要限制长度。
- 不保存完整第三方响应体。
- 不保存 Token、API Key、Authorization 或自定义请求头。

## 七、WebUI 改动

以下两个目录必须保持完全一致：

```text
pages/companion-panel/
pages/陪伴面板/
```

按钮位置：

| 功能分组 | 按钮文本 | 数据标记 |
| --- | --- | --- |
| 天气上下文 | 测试天气 API | `data-external-api-test="weather_api"` |
| 余额与补给 | 测试余额接口 | `data-external-api-test="balance_api"` |
| 自定义搜索接口 | 测试搜索接口 | `data-external-api-test="web_search"` |

按钮交互流程：

1. 读取当前表单的未保存字段。
2. 调用 `/troubleshooting/test`。
3. 显示按钮忙碌状态，防止重复请求。
4. 成功或失败后显示 Toast。
5. 结果使用 `showTestDiagnosticDialog()` 展示详细诊断。
6. 搜索按钮说明其同时验证主动搜索和群黑话联网参考共用的搜索路径。

## 八、测试计划

新增：

```text
tests/test_external_api_troubleshooting.py
```

后端测试覆盖：

- 天气成功请求。
- 天气空结果失败。
- 天气测试不修改 `daily_weather`。
- 余额自定义接口成功。
- 余额自动探测成功。
- 余额鉴权、网络和解析错误脱敏。
- 搜索自定义接口成功。
- 搜索回退 AstrBot Provider。
- 搜索空结果和错误状态。
- 主动搜索和群黑话共用同一底层入口。
- 三种类型生成稳定诊断信封。

前端测试覆盖：

- 两份面板文件完全一致。
- 三个按钮数据标记存在。
- 按钮位于正确功能分组。
- 请求包含当前未保存字段。
- 结果使用统一诊断弹窗。

## 九、验收标准

- WebUI 可以分别测试天气、余额和搜索 API。
- 主动搜索和群黑话联网参考不产生两套搜索实现。
- 测试不会触发主动消息、天气缓存、余额提醒或黑话缓存写入。
- 错误信息可定位问题且不泄露凭据。
- 中英文两套面板保持字节级一致。
- 专项测试和完整测试通过。
- 改动提交到后续确定的目标分支。

## 十、建议验证命令

```powershell
pytest -q tests/test_external_api_troubleshooting.py
pytest -q tests/test_page_api_test_diagnostics.py tests/test_weather_config_ui_grouping.py tests/test_balance_page.py
pytest -q
```
