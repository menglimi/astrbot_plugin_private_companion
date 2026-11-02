"""陌生首触：给"不属于任何目标"的私聊账号回一句固定文案。

与"已绑定但被收回权限"的礼拒（``private_notice.py``）刻意分开写，因为前提不同：
那条路有已解析的身份与窗口；这条路**什么都没有**——只有一个原生账号。
也正因为什么都没有，这里的约束必须写死在代码里，而不是靠调用方自觉：

* **只对本插件拥有的账号发**：``self_id`` 必须是某个已配置账号，平台实例与客户端
  必须与当前事件一致。否则我们可能在替别人的号说话。
* **只在边界校验通过后发**：旧 ``preview`` 值只为兼容读取，正式投递固定走真实 OneBot 私聊通道。
* **只发配置里那一行**：不建身份、不写 ``conversation_*``、不签发 grant、不读记忆。
* **频率兜底**：同一账号冷却期内只回一次，另有每日总量上限（``0`` = 完全不回）。
* **回执有界**：账号、时间、当天第几条、命中哪条配置——够回答"为什么回了这个人"，
  也不构成第二份通讯录。被拒的账号另记一条**有界**的联系记录（平台、收信号、账号、
  次数、最后一次结果），供管理页显示"近期私聊被拒"；它不建身份、不写会话、
  不签发任何 grant。

属于本功能的一次产品选择：真发了这一句之后会关闭默认模型回复，把这一轮从 AstrBot
自己的回复管线里拿走。既然插件已经作答，就不该让陌生人收到两条回复。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from companion_v2.adapters.astrbot.native import account_number
from companion_v2.execution.models import encode, stamp
from companion_v2.foundation.refs import ScopeKind

from .private_notice import private_notice_allowed, send_private_notice


UPSERT = """INSERT INTO memory_views VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
    ON CONFLICT(view_id) DO UPDATE SET revision = excluded.revision,
        document_json = excluded.document_json, updated_at = excluded.updated_at"""

COOLDOWN_KIND = "relationship.stranger_notice.v1"
DAILY_KIND = "relationship.stranger_notice_day.v1"
CONTACT_KIND = "relationship.stranger_contact.v1"
OWNER = "relationship.stranger_notice"


@dataclass(frozen=True)
class StrangerRoute:
    platform_id: str
    login: str
    peer: str
    client: object


def stranger_route(controller, event):
    """认证"这是本插件某个账号收到的一条私聊"；不是就返回 ``None``。"""
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    meta = getattr(event, "platform_meta", None)
    if not isinstance(raw, Mapping) or meta is None:
        return None
    if raw.get("post_type") != "message" or raw.get("message_type") != "private":
        return None
    if raw.get("group_id") is not None:
        return None
    login, peer = account_number(raw.get("self_id")), account_number(raw.get("user_id"))
    if not login or not peer:
        return None
    platform = controller.context.get_platform_inst(meta.id)
    if platform is None or platform.get_client() is not event.bot:
        return None
    if (platform.meta().name != "aiocqhttp" or meta.name != "aiocqhttp"
            or not getattr(controller, "native_types", None)
            or not isinstance(event, controller.native_types.event)):
        return None
    settings = controller.settings
    if hasattr(settings, "accounts"):
        owned = {(account.platform_id, account.self_id) for account in settings.accounts}
        configured = {(target.platform_id, target.account_id, target.participant_id)
                      for target in settings.targets}
    else:
        owned = {(settings.platform_id, settings.account_id)}
        configured = {(settings.platform_id, settings.account_id, settings.participant_id)}
    if (meta.id, login) not in owned:
        return None
    if ((meta.id, login, peer) in configured
            or (meta.id, login, "*") in configured):
        # 已配置的人**不是**陌生号：他们有自己的窗口与权限判定（包括"没有对话权限"
        # 时的礼拒）。挡住这里，是为了这条兜底通道永远不会抢走一个已配置对象的入站。
        return None
    client = platform.get_client()
    if not client._wsr_api_clients.get(login):
        return None
    return StrangerRoute(meta.id, login, peer, client)


class StrangerNotice:
    """按账号冷却、按天限额地回一句；任何一步不合规就什么都不做。"""

    def __init__(self, controller):
        self.controller = controller

    # -- 频率记账 ------------------------------------------------------------

    def _read(self, view_id, kind):
        row = self.controller.runtime.read_one(
            "SELECT document_json FROM memory_views WHERE view_id = ? AND kind = ?", (view_id, kind))
        if row is None:
            return {}
        import json
        try:
            document = json.loads(row["document_json"])
        except (TypeError, ValueError):
            return {}
        return document if isinstance(document, dict) else {}

    def _write(self, view_id, kind, document, at):
        import json
        with self.controller.runtime.transaction() as connection:
            row = connection.execute("SELECT revision FROM memory_views WHERE view_id = ? AND kind = ?",
                                     (view_id, kind)).fetchone()
            connection.execute(UPSERT, (view_id, view_id, kind, (row["revision"] + 1) if row else 1,
                                        encode(document), encode({"owner": OWNER}), stamp(at)))

    def _touch_contact(self, route, at, outcome):
        """Remember that this account tried to open a private chat.

        A bounded contact ledger only: no subject, no binding, no grant and no
        conversation row. It exists so the administrator can see who was turned
        away even when nothing was sent (disabled, cooldown, daily cap).
        """
        view_id = "stranger-contact:" + route.platform_id + ":" + route.login + ":" + route.peer
        document = self._read(view_id, CONTACT_KIND)
        attempts = int(document.get("attempts") or 0) + 1
        self._write(view_id, CONTACT_KIND, {
            "version": 1, "platform_id": route.platform_id, "login": route.login, "peer": route.peer,
            "first_seen_at": document.get("first_seen_at") or stamp(at),
            "last_seen_at": stamp(at), "attempts": attempts, "last_outcome": outcome}, at)

    def _cooldown_ok(self, route, at, cooldown):
        if cooldown <= 0:
            return True
        document = self._read("stranger-notice:" + route.login + ":" + route.peer, COOLDOWN_KIND)
        previous = document.get("last_notice_at")
        if not isinstance(previous, str) or not previous:
            return True
        try:
            moment = datetime.fromisoformat(previous.replace("Z", "+00:00"))
        except ValueError:
            return True
        return (at - moment).total_seconds() >= cooldown

    def _daily_room(self, at, cap):
        if cap <= 0:
            return None
        view_id = "stranger-notice-day:" + at.date().isoformat()
        document = self._read(view_id, DAILY_KIND)
        count = int(document.get("count") or 0)
        return None if count >= cap else (view_id, count)

    # -- 主流程 --------------------------------------------------------------

    def defaults(self):
        application = self.controller.session.application if self.controller.session else None
        settings = getattr(application, "private_access_settings", None)
        if settings is None:
            return None
        return settings.defaults_installation(self.controller.runtime.installation_id)

    async def handle(self, event, *, on_outcome=None):
        """Return the original send receipt and report every recognized silent gate."""
        controller = self.controller
        route = stranger_route(controller, event)
        if route is None:
            return None
        reported = False
        outcome = ["stranger_notice_processing_failed"]

        def report(state, reason_code):
            nonlocal reported
            outcome[0] = reason_code
            if reported:
                return
            reported = True
            if callable(on_outcome):
                on_outcome({"state": state, "reason_code": reason_code})

        def no_reply(reason_code):
            report("no_reply", reason_code)
            return None

        session = controller.session
        replied = False
        try:
            if session is None or controller.runtime is None or session.application.state != "running":
                return no_reply("stranger_notice_session_unavailable")
            defaults = self.defaults()
            if defaults is None:
                return no_reply("stranger_notice_settings_unavailable")
            if not defaults["stranger_notice"]:
                return no_reply("stranger_notice_disabled")
            text = defaults["denied_reply"]
            if not text or not private_notice_allowed(controller.settings):
                return no_reply("stranger_notice_delivery_unavailable")
            at = controller.now()
            if not self._cooldown_ok(route, at, defaults["notice_cooldown_seconds"]):
                return no_reply("stranger_notice_cooldown")
            room = self._daily_room(at, defaults["stranger_daily_cap"])
            if room is None:
                return no_reply("stranger_notice_daily_cap")
            view_id, count = room
            receipt = await send_private_notice(controller, event, text=text, route=route)
            if receipt.get("state") != "delivered":
                return no_reply("stranger_notice_send_failed")
            replied = True
            report("replied", "stranger_notice_sent")
            # 只有真的送出去了才记账：失败不占用冷却，也不占当天的额度。
            self._write("stranger-notice:" + route.login + ":" + route.peer, COOLDOWN_KIND,
                        {"version": 1, "platform_id": route.platform_id, "login": route.login,
                         "peer": route.peer, "text_sha": receipt.get("receipt"), "last_notice_at": stamp(at)}, at)
            self._write(view_id, DAILY_KIND,
                        {"version": 1, "date": at.date().isoformat(), "count": count + 1}, at)
            return {"platform_id": route.platform_id, "login": route.login, "peer": route.peer,
                    "count_today": count + 1, "sent_at": stamp(at), "receipt": receipt.get("receipt")}
        except Exception:
            if not reported:
                report("no_reply", "stranger_notice_processing_failed")
            return None
        finally:
            # Visibility outlives every gate: a stranger is listed even when the
            # notice was disabled, rate-limited or failed to send.
            try:
                self._touch_contact(route, controller.now(), outcome[0])
            except Exception:  # noqa: BLE001 - the ledger must never block message handling
                pass


async def refuse_stranger(controller, event, *, on_outcome=None):
    """给 ``main.py`` 用：不再抛，只回报"到底发没发"。"""
    try:
        return await StrangerNotice(controller).handle(event, on_outcome=on_outcome)
    except Exception:  # noqa: BLE001 - 婉拒陌生人永远不该影响正常消息处理
        return None
