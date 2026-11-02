"""Dashboard bridge for the bundled Huawei Health account binding."""

from __future__ import annotations

import json
import logging

from astrbot.api.web import request

from companion_v2.execution.models import TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.identity_policy.object_access import ObjectAccessPolicy

from ..management_identity import dashboard_operator
from .service import ensure_huawei_health_service
from .vendor.hw_auth import HuaweiAuthError


PREFIX = "/astrbot_plugin_private_companion/huawei-health"
logger = logging.getLogger(__name__)


class HuaweiHealthWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        return [
            (PREFIX + "/status", self.status, ["GET"], "Huawei Health binding status"),
            (PREFIX + "/cookie/import", self.import_cookie, ["POST"], "Import Huawei Health cookies"),
            (PREFIX + "/cookie/clear", self.clear_cookie, ["POST"], "Clear Huawei Health cookies"),
            (PREFIX + "/credentials", self.save_credentials, ["POST"], "Save Huawei account credentials"),
            (PREFIX + "/credentials/clear", self.clear_credentials, ["POST"], "Clear Huawei credentials"),
            (PREFIX + "/login/start", self.start_login, ["POST"], "Start Huawei web login"),
            (PREFIX + "/login/status", self.login_status, ["GET"], "Read Huawei login state"),
            (PREFIX + "/login/sms", self.submit_sms, ["POST"], "Submit Huawei SMS code"),
            (PREFIX + "/login/trust", self.submit_trust, ["POST"], "Submit Huawei trust choice"),
            (PREFIX + "/login/cancel", self.cancel_login, ["POST"], "Cancel Huawei login"),
            (PREFIX + "/refresh", self.refresh, ["POST"], "Refresh Huawei health summary"),
        ]

    def _authorize(self):
        controller = self.controller
        if controller._terminated:
            raise TaskError("staging_host_terminated", "plugin is stopped")
        username = request.username
        if type(username) is not str or not username.strip() or username.startswith("api_key:"):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        if controller.runtime is None:
            raise TaskError("staging_not_initialized", "load the V2 profile first")
        operator = dashboard_operator(controller.runtime, username, at=controller.now())
        require(ObjectAccessPolicy(controller.runtime).feature_access(
            operator,
            scope=ScopeRef(controller.runtime.installation_id, "companion", ScopeKind.SYSTEM,
                           "page.management"),
            action="memory.manage", observed_at=controller.now(),
        ))
        return operator

    async def _service(self):
        self._authorize()
        return await ensure_huawei_health_service(self.controller, logger=logger)

    @staticmethod
    def _selected_scope(body):
        target_id = body.pop("target_id", None)
        role_id = body.pop("role_id", None)
        if target_id is not None and (type(target_id) is not str or not target_id.strip()):
            raise ValueError("target_id is invalid")
        if role_id is not None and (type(role_id) is not str or not role_id.strip()):
            raise ValueError("role_id is invalid")
        return (target_id.strip() if isinstance(target_id, str) else "",
                role_id.strip() if isinstance(role_id, str) else "")

    @staticmethod
    async def _json_body(limit=64 * 1024):
        raw = await request.body()
        if request.query or len(raw) > limit:
            raise ValueError("bounded JSON body required")

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON field")
                result[key] = value
            return result

        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite value")))
        if type(value) is not dict:
            raise ValueError("JSON object required")
        return value

    @staticmethod
    def _ok(data):
        return Outcome.success(data)

    @staticmethod
    def _failure(error):
        from ..web_api import outcome_response
        normalized = error if isinstance(error, TaskError) else TaskError(
            "huawei_health_request_invalid", str(error) or "Huawei Health request failed")
        return outcome_response(Outcome.failed(normalized.code, str(normalized)))

    async def status(self):
        from ..web_api import outcome_response
        try:
            service = await self._service()
            return outcome_response(self._ok(await service.status()))
        except (TaskError, ValueError, RuntimeError) as error:
            return self._failure(error)
        except Exception as error:
            logger.exception("[HuaweiHealth] 状态读取失败 error_type=%s error=%s", type(error).__name__, error)
            return self._failure(TaskError("huawei_health_status_failed", "运动健康状态暂不可读"))

    async def import_cookie(self):
        from ..web_api import outcome_response
        try:
            body = await self._json_body()
            target_id, role_id = self._selected_scope(body)
            if set(body) - {"json", "fields"}:
                raise ValueError("invalid cookie import request")
            text = body.get("json") if isinstance(body.get("json"), str) else ""
            fields = body.get("fields") if isinstance(body.get("fields"), dict) else {}
            if not text.strip() and not any(str(value or "").strip() for value in fields.values()):
                raise ValueError("cookie 内容为空")
            service = await self._service()
            count = await service.import_cookie(text, fields)
            return outcome_response(self._ok({"imported": count, "status": await service.status()}))
        except HuaweiAuthError as error:
            # Keep the specific reason (missing CASTGC/sid, mixed formats, bad
            # JSON) instead of collapsing every import failure into one generic
            # "check the format" message.
            return self._failure(TaskError("huawei_health_cookie_invalid", str(error)))
        except (TaskError, ValueError, RuntimeError, OSError) as error:
            return self._failure(error)
        except Exception:
            return self._failure(TaskError("huawei_health_cookie_failed", "Cookie 导入失败，请核对格式"))

    async def clear_cookie(self):
        from ..web_api import outcome_response
        try:
            service = await self._service()
            await service.clear_cookie()
            return outcome_response(self._ok({"cleared": True, "status": await service.status()}))
        except (TaskError, ValueError, RuntimeError, OSError) as error:
            return self._failure(error)
        except Exception:
            return self._failure(TaskError("huawei_health_cookie_failed", "Cookie 清除失败"))

    async def save_credentials(self):
        from ..web_api import outcome_response
        try:
            body = await self._json_body()
            target_id, role_id = self._selected_scope(body)
            if set(body) != {"account", "password"}:
                raise ValueError("invalid credential request")
            service = await self._service()
            saved = await service.save_credentials(body.get("account"), body.get("password"))
            return outcome_response(self._ok({"saved": saved, "status": await service.status()}))
        except (TaskError, ValueError, RuntimeError, OSError) as error:
            return self._failure(error)
        except Exception as error:
            logger.warning("[HuaweiHealth] 账号密码保存失败 error_type=%s", type(error).__name__)
            return self._failure(TaskError("huawei_health_credentials_failed", "账号密码保存失败"))

    async def clear_credentials(self):
        from ..web_api import outcome_response
        try:
            service = await self._service()
            cleared = await service.clear_credentials()
            return outcome_response(self._ok({"cleared": cleared, "status": await service.status()}))
        except (TaskError, ValueError, RuntimeError, OSError) as error:
            return self._failure(error)
        except Exception:
            return self._failure(TaskError("huawei_health_credentials_failed", "账号密码清除失败"))

    async def start_login(self):
        from ..web_api import outcome_response
        try:
            body = await self._json_body()
            target_id, role_id = self._selected_scope(body)
            if set(body) - {"auto_trust", "prefer_auto", "visible_fallback"}:
                raise ValueError("invalid login request")
            service = await self._service()
            result = await service.start_login(
                auto_trust=body.get("auto_trust", True) is True,
                prefer_auto=body.get("prefer_auto", False) is True,
                visible_fallback=body.get("visible_fallback", False) is True,
            )
            return outcome_response(self._ok(result))
        except (TaskError, ValueError, RuntimeError, OSError) as error:
            return self._failure(error)
        except Exception as error:
            logger.warning("[HuaweiHealth] 登录启动失败 error_type=%s", type(error).__name__)
            return self._failure(TaskError("huawei_health_login_failed", "华为账号登录无法启动"))

    async def login_status(self):
        from ..web_api import outcome_response
        try:
            service = await self._service()
            return outcome_response(self._ok(await service.login_status()))
        except (TaskError, ValueError, RuntimeError) as error:
            return self._failure(error)
        except Exception:
            return self._failure(TaskError("huawei_health_login_failed", "登录状态暂不可读"))

    async def submit_sms(self):
        from ..web_api import outcome_response
        try:
            body = await self._json_body()
            if set(body) != {"code"} or type(body.get("code")) is not str:
                raise ValueError("invalid SMS code request")
            service = await self._service()
            return outcome_response(self._ok(await service.submit_sms(body["code"])))
        except (TaskError, ValueError, RuntimeError, OSError) as error:
            return self._failure(error)
        except Exception:
            return self._failure(TaskError("huawei_health_login_failed", "验证码提交失败"))

    async def submit_trust(self):
        from ..web_api import outcome_response
        try:
            body = await self._json_body()
            if set(body) != {"choice"} or body.get("choice") not in ("true", "false", "later"):
                raise ValueError("invalid trust choice")
            service = await self._service()
            return outcome_response(self._ok(await service.submit_trust(body["choice"])))
        except (TaskError, ValueError, RuntimeError, OSError) as error:
            return self._failure(error)
        except Exception:
            return self._failure(TaskError("huawei_health_login_failed", "信任选择提交失败"))

    async def cancel_login(self):
        from ..web_api import outcome_response
        try:
            service = await self._service()
            return outcome_response(self._ok(await service.cancel_login()))
        except (TaskError, ValueError, RuntimeError, OSError) as error:
            return self._failure(error)
        except Exception:
            return self._failure(TaskError("huawei_health_login_failed", "取消登录失败"))

    async def refresh(self):
        from ..web_api import outcome_response
        try:
            service = await self._service()
            latest = await service.refresh_summary(force=True)
            return outcome_response(self._ok({"latest": latest, "status": await service.status()}))
        except (TaskError, ValueError, RuntimeError, OSError) as error:
            return self._failure(error)
        except Exception:
            return self._failure(TaskError("huawei_health_fetch_failed", "华为健康取数失败"))


__all__ = ["HuaweiHealthWebAPI", "PREFIX"]
