"""Keep the QQ Zone session alive without a human, and say so when it is not.

页面上「刷新凭据」是一次**手动**重试：它绕过退避，也不管上一次失败了几次。这一层
补的是另一半——**没人点的时候**：凭据坏了就按 ``companion_v2/qzone/credential.py``
的梯子（1 分钟 → 24 小时）自己重取，恢复正常后回到 5 分钟一次的体检；连续失败
跨过一个新的档位时，私聊管理员一句话，恢复时补一句"已恢复"。

三条边界，和 ``private_notice`` 保持一致：

* **只读凭据，不碰业务**：这里只调 ``transport.credential(refresh=True)``，不刷
  空间、不发说说、不写账本；真正的动作仍走原有链路与授权。
* **不打印任何凭据**：日志只写次数、下次间隔与布尔位；发给管理员的那句话只带
  掩码账号，不带 cookie / skey / 请求体。
* **不重放**：发消息失败就是没发出去，记一条 warning，不补发、不循环重试。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.qzone.credential import CredentialWatch, disconnect_message, recovered_message

from .private_notice import send_text_notice
from .recalls import NativeNoticeRoute


logger = logging.getLogger(__name__)

# 凭据新鲜时多久体检一次。与 QzoneHttpClient 的 credential_ttl_seconds 同档：
# 缓存过期后再问一次宿主，正好覆盖"OneBot 悄悄换了会话"这种情况。
SUCCESS_CHECK_SECONDS = 300.0
# 扩展没装、宿主没跑起来、或刚启动还没有 session 时的空转间隔。
IDLE_SECONDS = 60.0
REASON_LIMIT = 160

# 失败原因会被写进给管理员的私聊：任何疑似 cookie 的键值对先抹掉再说。
_COOKIE_PAIR = re.compile(r"(?i)\b(p_skey|pskey|skey2|skey|p_uin|uin|qzonetoken|pt4_token"
                          r"|pt_key|clientkey|superkey)\s*=\s*[^\s;,]+")


def _short_reason(error) -> str:
    """One bounded line: the error code plus a redacted message."""
    code = str(getattr(error, "code", "") or "").strip()
    text = _COOKIE_PAIR.sub(r"\1=***", str(error or "").strip())
    text = re.sub(r"\s+", " ", text)[:REASON_LIMIT]
    if code and text.startswith(code):
        text = text[len(code):].strip(" :：")
    return f"{code}: {text}" if code and text else (text or code)


class QzoneCredentialWatch:
    """One background loop per plugin instance; safe to start before a session exists."""

    def __init__(self, controller, *, sleep=asyncio.sleep, clock=time.monotonic):
        self.controller = controller
        self._sleep = sleep
        self._clock = clock
        self._watch = CredentialWatch()
        self._stopped = False
        self._settings_warned = False

    # -- lifecycle -------------------------------------------------------------------

    def stop(self) -> None:
        """Ask the loop to finish after the current cycle."""
        self._stopped = True

    @property
    def state(self) -> CredentialWatch:
        return self._watch

    async def run(self) -> None:
        while not self._stopped:
            try:
                delay = await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - 一轮出错不能带走整个任务
                logger.warning("V2 qzone credential.watch: status=error error_type=%s",
                               type(error).__name__)
                delay = IDLE_SECONDS
            if self._stopped:
                return
            await self._sleep(max(1.0, float(delay)))

    # -- one cycle -------------------------------------------------------------------

    async def _cycle(self) -> float:
        session = getattr(self.controller, "session", None)
        application = getattr(session, "application", None)
        service = getattr(application, "qzone", None)
        if service is None or getattr(application, "state", "") != "running":
            # 没装扩展、或运行档还没起来：这不是"凭据坏了"，不该发通知。
            return IDLE_SECONDS
        transport = getattr(service, "transport", None)
        if transport is None or not hasattr(transport, "credential"):
            return IDLE_SECONDS
        try:
            # refresh=True：明确要求重取，绕开运输层自己那圈短期退避。
            await transport.credential(refresh=True)
        except TaskError as error:
            return await self._failed(transport, error)
        except Exception as error:  # noqa: BLE001 - 未知异常也按一次失败处理
            return await self._failed(transport, error)
        return await self._recovered()

    async def _failed(self, transport, error) -> float:
        self._watch = self._watch.failed()
        delay = self._watch.next_delay_seconds()
        step = self._watch.pending_step()
        notified = False
        if step is not None:
            notified = await self._notify(transport, error, delay)
            if notified:
                self._watch = self._watch.noticed(step)
        logger.info("V2 qzone credential.watch: status=failed failures=%s next_in=%s notified=%s",
                    self._watch.failures, int(delay), notified)
        return float(delay)

    async def _recovered(self) -> float:
        failures = self._watch.failures
        self._watch = CredentialWatch()
        if failures > 0:
            delivered = await self._send(recovered_message(failures=failures))
            logger.info("V2 qzone credential.watch: status=recovered failures=%s notified=%s",
                        failures, delivered)
        return SUCCESS_CHECK_SECONDS

    # -- telling the administrator ---------------------------------------------------

    async def _notify(self, transport, error, delay) -> bool:
        if not self._notice_enabled():
            return False
        state = getattr(transport, "last_credential_state", None) or {}
        masked = str(state.get("uin_masked") or "") if isinstance(state, dict) else ""
        text = disconnect_message(watch=self._watch, reason=_short_reason(error),
                                  uin_masked=masked, delay_seconds=delay)
        return await self._send(text)

    def _notice_enabled(self) -> bool:
        """The role's switch; unreadable settings keep the documented default (on).

        多目标模式的 session 没有单一 ``profile``，读不到角色设置时按默认开启处理，
        并且只提醒一次——每 5 分钟刷一条同样的 INFO 会把日志淹掉。
        """
        try:
            settings = self._settings()
        except Exception as error:  # noqa: BLE001 - 读不到设置不该让循环停下来
            if not self._settings_warned:
                self._settings_warned = True
                logger.info("V2 qzone credential.watch: status=settings_unreadable error_type=%s detail=%s",
                            type(error).__name__, _short_reason(error))
            return True
        self._settings_warned = False
        return settings.get("credential_admin_notice", True) is not False

    def _settings(self) -> dict:
        session = getattr(self.controller, "session", None)
        reader = getattr(getattr(session, "application", None), "qzone_settings", None)
        if reader is None:
            return {}
        values = reader.read(session.profile.target, self.controller.now())
        return values if isinstance(values, dict) else {}

    @staticmethod
    def _is_private(scope) -> bool:
        """Compare by value: a reloaded enum instance must not fail an ``is`` check."""
        return str(getattr(scope, "kind", "") or "").lower().endswith("private")

    def _private_scope(self):
        """The private window to notify, or ``None``.

        活动目标可能是群（同一个宿主同时服务私聊与群聊），这时不能拿它当收件人；
        从已配置的私聊目标里挑第一个重新组一个 ``ScopeRef``——字段顺序与
        ``companion_v2/foundation/refs.py`` 的 ``ScopeRef`` 一致，且四个路由字段
        必须同时给，否则构造时会被判非法。
        """
        session = getattr(self.controller, "session", None)
        scope = getattr(getattr(getattr(session, "profile", None), "target", None), "scope", None)
        if self._is_private(scope):
            return scope
        bridge = getattr(session, "bridge", None)
        targets = tuple(getattr(getattr(bridge, "settings", None), "targets", ()) or ())
        # 设置里可能留着占位行（participant_id 是掩码 ``*``）：那不是能收消息的号，
        # 挑第一个"纯数字的 QQ 号"当收件人，一个都没有就不发。
        usable = [item for item in targets
                  if str(getattr(item, "participant_id", "") or "").isdigit()]
        if not usable:
            return None
        base = scope
        # 单目标模式下安装 id 在 scope 上；多目标模式下 session 没有 profile，
        # 但 bridge 自己记着这一个安装的 id。
        installation = (str(getattr(base, "installation_id", "") or "")
                        or str(getattr(bridge, "installation_id", "") or ""))
        if not installation:
            return None
        item = usable[0]
        peer = str(item.participant_id)
        return ScopeRef(installation_id=installation, platform=str(item.platform_id),
                        kind=ScopeKind.PRIVATE, native_scope_id=peer,
                        bot_id=str(item.bot_id), account_id=str(item.account_id),
                        conversation_id=peer, session_id=peer)

    def _route(self):
        """The pinned private window as a native route, or ``None`` when unusable."""
        session = getattr(self.controller, "session", None)
        bridge = getattr(session, "bridge", None)
        if bridge is None or not callable(getattr(bridge, "client", None)):
            logger.info("V2 qzone credential.watch: status=route_unavailable reason=no_bridge")
            return None
        scope = self._private_scope()
        if scope is None:
            logger.info("V2 qzone credential.watch: status=route_unavailable reason=no_private_target")
            return None
        try:
            client = bridge.client(scope)
        except TaskError as error:
            logger.info("V2 qzone credential.watch: status=route_unavailable code=%s",
                        getattr(error, "code", "unknown"))
            return None
        except Exception as error:  # noqa: BLE001 - 路由判定失败不该带走循环
            logger.info("V2 qzone credential.watch: status=route_unavailable error_type=%s",
                        type(error).__name__)
            return None
        peer = str(scope.native_scope_id)
        return NativeNoticeRoute(kind=ScopeKind.PRIVATE, platform_id=str(scope.platform),
                                 bot_id=str(getattr(scope, "bot_id", "") or ""),
                                 login=str(getattr(scope, "account_id", "") or ""),
                                 sender=peer, peer=peer, session_id=peer, client=client)

    async def _send(self, text) -> bool:
        route = self._route()
        if route is None:
            return False
        try:
            receipt = await send_text_notice(self.controller, route, text=text)
        except Exception as error:  # noqa: BLE001 - 发失败不重放，只记一条
            # 只写形状，不写号码：peer 是几位、是不是纯数字、走哪个平台。
            logger.warning(
                "V2 qzone credential.watch: status=notice_failed error_type=%s detail=%s "
                "peer_len=%s peer_digits=%s login_len=%s platform=%s",
                type(error).__name__, _short_reason(error),
                len(str(route.peer or "")), str(route.peer or "").isdigit(),
                len(str(route.login or "")), route.platform_id)
            return False
        delivered = isinstance(receipt, dict) and receipt.get("state") == "delivered"
        if not delivered:
            logger.info("V2 qzone credential.watch: status=notice_skipped reason=%s",
                        (receipt or {}).get("reason", "unknown") if isinstance(receipt, dict) else "unknown")
        return delivered


__all__ = ["IDLE_SECONDS", "QzoneCredentialWatch", "SUCCESS_CHECK_SECONDS"]
