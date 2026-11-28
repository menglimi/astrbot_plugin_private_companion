"""QQ 空间凭据的自动重取节奏，以及"未连接"该不该打扰管理员。

凭据本身由宿主侧 ``astrbot_host.qzone_client.QzoneHttpClient`` 拿（手动
``QZONE_COOKIE`` / ``qzone_cookie.txt``，或当前 OneBot 会话）；这一层只回答两个
纯问题，好让节奏与去重能被单测钉住，而不是散在一个 ``while True`` 里：

* **连续第 n 次失败之后隔多久再试**——从 1 分钟起逐级放大，24 小时封顶；
* **这一次失败该不该发消息**——每个新档位只提醒一次，恢复时补一句"已恢复"。

"连续失败"按**一次成功清零**：掉线恢复后又从 1 分钟重新开始，不会因为前一天
掉过线就一上来等一天。档位只增不减的副作用是：真的连续失败满 11 次以后，
后续每次重试都按 24 小时走，不再无限拉长。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# 1 分钟、5 分钟、10 分钟、20 分钟、30 分钟、1 小时、2 小时、3 小时、6 小时、
# 12 小时、24 小时。改这里就等于改"凭据坏了以后多久问一次"。
RETRY_STEPS_SECONDS = (60, 300, 600, 1200, 1800, 3600, 7200, 10800, 21600, 43200, 86400)
RETRY_CAP_SECONDS = RETRY_STEPS_SECONDS[-1]
# 普通操作（页面刷空间、主动联系、点赞回复）撞上退避时最多等这么久。长档位是
# 给后台自动重取用的：一个"刷新凭据"按钮不该因为连续失败过几次就等一天。
PASSIVE_RETRY_CAP_SECONDS = 300


def retry_step_index(failures: int) -> int:
    """Zero-based ladder rung for one consecutive-failure count.

    ``failures`` 小于 1 时按第 1 档处理（这一层不表达"没失败过"，那种情况由
    ``CredentialWatch.step`` 自己给 ``-1``）。
    """
    return min(max(int(failures or 0), 1), len(RETRY_STEPS_SECONDS)) - 1


def retry_delay_seconds(failures: int, *, cap: int | None = None) -> int:
    """How long to wait after the given consecutive failure, optionally capped."""
    delay = RETRY_STEPS_SECONDS[retry_step_index(failures)]
    return delay if cap is None else min(delay, int(cap))


def passive_retry_delay_seconds(failures: int) -> int:
    """Delay for callers that are not the background watch (never more than 5 min)."""
    return retry_delay_seconds(failures, cap=PASSIVE_RETRY_CAP_SECONDS)


def format_delay(seconds) -> str:
    """A short Chinese duration for messages and the page: 1 分钟 / 2 小时 / 24 小时."""
    total = max(0, int(seconds or 0))
    if total >= 3600:
        hours, rest = divmod(total, 3600)
        if not rest:
            return f"{hours} 小时"
        return f"{hours} 小时 {rest // 60} 分钟"
    if total >= 60:
        return f"{total // 60} 分钟"
    return f"{total} 秒"


@dataclass(frozen=True)
class CredentialWatch:
    """One watch's rung and what the administrator has already been told.

    ``notified_step`` 记的是"哪一档已经报过"。同一档里的多次重试只报第一条，
    所以连续失败一整天的消息量是 11 条，不是 1440 条。
    """

    failures: int = 0
    notified_step: int = -1

    @property
    def step(self) -> int:
        """Current rung, or ``-1`` when nothing has failed yet."""
        return retry_step_index(self.failures) if self.failures > 0 else -1

    def failed(self) -> "CredentialWatch":
        """Count one more consecutive failure (does not mark it as announced)."""
        return replace(self, failures=self.failures + 1)

    def succeeded(self) -> "CredentialWatch":
        """A fresh acquisition clears the ladder back to the first rung."""
        return CredentialWatch()

    def next_delay_seconds(self) -> int:
        """Wait this long before the next automatic attempt."""
        return retry_delay_seconds(self.failures)

    def pending_step(self) -> int | None:
        """The rung that still needs an announcement, or ``None``."""
        if self.failures <= 0:
            return None
        step = self.step
        return None if step == self.notified_step else step

    def noticed(self, step: int) -> "CredentialWatch":
        """Remember that this rung has been announced."""
        return replace(self, notified_step=int(step))


def disconnect_message(*, watch: CredentialWatch, reason: str = "", uin_masked: str = "",
                       delay_seconds: int | None = None) -> str:
    """The one line sent to the administrator when Zone cannot log in.

    只写状态、次数、下次重试与掩码账号：**不写任何 cookie、skey 或请求体**。
    """
    delay = watch.next_delay_seconds() if delay_seconds is None else int(delay_seconds)
    lines = ["【陪伴小屋】QQ 空间凭据未连接"]
    if uin_masked:
        lines.append(f"账号：{uin_masked}")
    lines.append(f"连续失败：{max(1, watch.failures)} 次")
    if reason:
        lines.append(f"原因：{reason}")
    lines.append(f"下次自动重试：{format_delay(delay)}后")
    lines.append("可在「陪伴小屋 → 行动 → QQ 空间」点「刷新凭据」手动重试。")
    return "\n".join(lines)


def recovered_message(*, failures: int) -> str:
    """The one line sent after a failed streak finally acquires a credential."""
    if failures <= 0:
        return "【陪伴小屋】QQ 空间凭据已恢复。"
    return f"【陪伴小屋】QQ 空间凭据已恢复（连续失败 {int(failures)} 次后自动重取成功）。"


__all__ = ["CredentialWatch", "PASSIVE_RETRY_CAP_SECONDS", "RETRY_CAP_SECONDS",
           "RETRY_STEPS_SECONDS", "disconnect_message", "format_delay",
           "passive_retry_delay_seconds", "recovered_message", "retry_delay_seconds",
           "retry_step_index"]
