# 共享模式契约包

> 包修订 `0.1.0`，profile `companion.sharing@1`，成熟度 `review`。本包固定共享归属的格式与设计夹具；可信身份解析、授权、撤回传播和资源峰值尚未运行验收。

共享语义由[注入协议 §5.1.1](../../../COMPANION_INJECTION_PROTOCOL.md#511-sharingmode会话用户与全局)维护，领域使用见[世界模拟纵向切片](../../../DOMAIN_STATE_MACHINES_V0.md#7-世界模拟纵向切片学习被打断与跨会话恢复)。共享模式与 RuntimeScope、权限、visibility、purpose 和 retention 分开校验。

## 1. 三种归属

| 模式 | Kernel 解析的归属 | 使用边界 |
| --- | --- | --- |
| `session` | 实际 conversation/session，连同安装、Bot、人格和平台账号边界 | 新 session 不继承旧 session 临时状态；同一聊天窗口也可能有不同 session |
| `user` | 同一平台、账号、用户及安装/Bot/人格 | 可跨私聊和群聊共享用户级状态；当前群受众仍需授权 |
| `global` | 显式配置的 installation/bot/persona 公共分区，在 ecosystem 内解析 | 多个授权入口可读取公共世界状态；不合并用户身份、私聊事实或投递目标 |

`sharing_anchor` 是 Kernel 解析并签发的不透明引用，JSON 字符串本身不是凭证。`sharing_policy_revision` 用于核对共享策略版本，不代替 memory/world revision。预设选择由策略决定各领域的分区；改预设不自动搬运历史数据，也不扩大已存记录权限。读取可以有界组合三层，写入一次只针对一个确定归属。

`global` 不会把私聊和群聊变成同一个会话，也不会自动统一入口流程。要避免同一人格在不同入口表现成不同 Bot，宿主必须让所有入口先进入同一规范事件流水线，再分别叠加 global 公共连续性、user 关系投影、session 场景和 audience 表达策略。群聊的唤醒/读空气只能决定继续、吸收或沉默，不能跳过统一人格、关系和决策层；私聊正文、专属关系和群成员观察也不能因 global 存在而互相泄漏。

## 2. 语义字段与 wire 位置

已有 Memory v1 的封闭对象及指纹保持不变。本包通过原有命名空间扩展叠加校验，不把共享字段直接塞进 Memory 封套或 payload 顶层。

| 对象 | 三个共享字段所在位置 | 协商方式 |
| --- | --- | --- |
| `memory.proposal.v1` / `memory.query.v1` | `payload.extensions["companion.sharing"].payload` | 请求封套 `required_features` 必须含 `companion.sharing@1` |
| `memory.result.v1` 的召回项，包括 pending 提议 | `output.items[i].extensions["companion.sharing"].payload` | 与发起查询的已协商 profile 关联；逐条保留归属 |
| 本包 `ContextContribution` / `StateTransitionProposal` | DTO 顶层 `sharing_mode`、`sharing_anchor`、`sharing_policy_revision` | DTO 的 `required_features` 必须含 `companion.sharing@1` |
| `sharing-binding` | 对象本身的三个字段 | 作为已解析归属或上述扩展 payload 使用 |
| 控制描述符、订阅和其他事件 | 本包未新增其 wire 字段 | 通过已有协商/绑定关联语义；后续专题定型，不向封闭 Schema 加字段 |

以下是 Memory 请求的局部片段，其他封套和 payload 字段仍按原 Memory Schema 填写：

```json
{
  "required_features": ["companion.sharing@1"],
  "payload": {
    "extensions": {
      "companion.sharing": {
        "schema_version": "1.0",
        "payload": {
          "sharing_mode": "user",
          "sharing_anchor": "opaque-anchor",
          "sharing_policy_revision": 3
        }
      }
    }
  }
}
```

扩展影响隔离与读取正确性，不能作为可忽略元数据发送。提供方不支持 `companion.sharing@1` 时，协商应拒绝该调用；不能删掉 required feature 后重试并宣称保持共享语义。旧 Memory Schema 能接受此形状，不证明旧实现会执行共享策略。能力描述仍使用已定义的协商字段，不新增顶层 `sharing_mode`。

Memory 单次查询指定一个 anchor；上下文编排分别发起有界查询后组合获授权结果。所有子查询共用本轮预算，不将三种模式各自分配一份完整额度。召回到 ContextContribution 的映射复制已验证的归属，不由模型重写；实际 audience、owner、policy revision、证据有效性及跨对象关联需要运行时重验。

## 3. 包内容与验证

本包包含 5 个公共 Schema、1 个世界 patch 夹具 Schema、12 个完整示例、59 个格式案例（30 个负例）、严格 JSON 案例、5 个静态关联断言及 SHR-01--SHR-12 设计场景。依赖的 5 个 Memory/公共 Schema 和本包 JSON 指纹登记在 [manifest](./manifest.json)。Markdown 和验证脚本不计入协议指纹。

```powershell
python scripts/validate_sharing_contracts.py
```

在私有陪伴仓库根目录执行；依赖见 [requirements-validation.txt](../../requirements-validation.txt)。校验器检查本地引用、格式、指纹、已注册领域 patch、基础 Memory 形状兼容和夹具关联。`patch` 不能仅通过公共封套就视为合法，必须用已注册的 `patch_schema` 再校验；夹具里的 world-patch 只演示暂停/进度，不是完整世界能力 Schema。

[sharing.fixture.json](./fixtures/sharing.fixture.json) 中 SHR-01--SHR-12 均为 `run_status=not_run`、`actual=null`。形状合法但 anchor 冒用、策略过期或受众越权的例子刻意保留，运行时必须拒绝。未验证项包括可信 anchor 解析、真实 ACL、身份映射、Writer、策略切换、订阅隔离、撤回传播、资源峰值及平台回放。
