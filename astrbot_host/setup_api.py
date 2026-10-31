"""Authenticated, persistent first-run setup routes for the Companion V2 host."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import logging
from uuid import uuid4

from astrbot.api.web import request

from companion_v2.execution.models import TERMINAL, TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind
from companion_v2.proactive.configuration import PURPOSES
from companion_v2.proactive.diagnostic_models import DiagnosticSourceError
from companion_v2.proactive.management import ContactManagement
from companion_v2.proactive.scheduling import DIAGNOSTIC_ENVIRONMENT_VARIANTS
from companion_v2.sources.privacy import redact_text

from .binding_store import PendingBinding, binding_path, read_binding, write_binding
from .bootstrap_provider import (resolve_bootstrap_provider, supported_text_provider,
                                 text_provider_catalog, text_provider_predicate)
from .identity_derivation import derive_bot_id, derive_role_id, normalize_persona_id
from .management_identity import dashboard_operator
from .persona_catalog import available_personas
from .settings import HostControlError
from .setup_state import SETUP_STEPS, SetupStateStore, SetupStepsIncomplete
from .display_names import display_names


PREFIX = "/astrbot_plugin_private_companion/setup"
# Profile editors submit the complete edited field set in one request. The
# per-field limits are larger than the small command-route body cap.
PROFILE_BODY_LIMIT = 262144
logger = logging.getLogger(__name__)

PROACTIVE_TEST_SUGGESTIONS = {
    "dashboard_identity_required": "当前请求没有可用于真实投递的 Dashboard 登录身份。请从已登录的管理界面重新打开；插件 API Key 不能代替 Dashboard 登录会话。",
    "unbound_identity": "当前 Dashboard 身份还没有完成本体绑定。请从已登录的管理界面重新打开一次，让系统建立当前安装的身份绑定。",
    "binding_revoked": "当前 Dashboard 身份与本体的绑定已撤销。请由仍有核心权限的管理员重新确认该身份，不要反复运行真实投递测试。",
    "binding_expired": "当前 Dashboard 身份与本体的绑定已过期。请重新登录并由仍有核心权限的管理员确认当前身份。",
    "binding_not_active": "当前 Dashboard 身份的本体绑定尚未生效。请稍后重新读取状态，确认绑定生效后再测。",
    "binding_rebound": "当前 Dashboard 身份已被重新绑定到其他主体。请刷新管理界面，确认当前账号与本体身份对应后再测。",
    "scope_installation_mismatch": "当前 Dashboard 身份属于其他 AstrBot 安装，不能管理这份插件数据。请从当前安装的管理界面重新打开。",
    "scope_not_granted": "当前 Dashboard 身份没有这个配置范围的授权。请由仍有核心权限的管理员补发对应授权。",
    "core_access_required": "当前 Dashboard 身份没有本体核心管理权限。请使用已获核心管理权限的账号，或由仍有权限的管理员恢复该授权。",
    "authorization_denied": "当前 Dashboard 身份没有管理此配置的权限。请由仍有核心权限的管理员检查授权状态。",
    "authorization_revoked": "当前 Dashboard 身份的核心管理授权已撤销。请由另一位仍有权限的管理员重新授权；反复运行测试不会恢复授权。",
    "authorization_expired": "当前 Dashboard 身份的管理授权已过期。请由仍有核心权限的管理员重新授权。",
    "authorization_not_active": "当前 Dashboard 身份的管理授权尚未生效。请稍后刷新状态，或由仍有核心权限的管理员重新授权。",
    "contact_permission_required": "在核心管理员私聊的主动联系设置中启用主动联系授权。",
    "model_task_unregistered": "为核心管理员角色配置并启用 proactive.compose 模型任务。",
    "contact_attempt_budget": "主动模型调用次数上限错误；请更新插件，并查看每日实际联系额度。",
    "contact_delivery_budget": "到「主动联系 → 每日额度」查看实际联系上限；已确认发送计联系次数，待回执发送暂时预占，明确未发送后释放。模型跳过不计联系。",
    "contact_purpose_paused": "检查所选目的的暂停状态和恢复时间。",
    "contact_quiet_hours": "当前处于静默时段；结束后再运行测试。",
    "delivery_result_unknown": "投递结果未知；先核对核心管理员私聊，不要重放这次尝试。",
    "delivery_failed": "检查 OneBot 私聊连接、机器人在线状态和平台返回错误。",
    "contact_diagnostic_source_required": "展开技术详情查看本次没有测试机会的原因。关系表达类使用已完成的私聊窗口和用途设置；事件类需要当前有效事件来源。没有候选时不会调用模型。",
    "contact_weather_unavailable": "检查角色地点与天气观测设置，并确认 Open-Meteo 或已配置的和风凭据可访问；刷新后先在状态页确认出现当前观测。",
    "contact_news_unavailable": "检查当前资讯源的网络抓取结果与条目发布时间；真实测试不会用合成新闻替代。",
    "screen_peek_unavailable": "检查当前私聊已配置的截图设备、识屏开关与窥屏授权。",
    "screen_diagnostic_pending": "本次截图或视觉分析任务仍未结束；先核对该任务和设备记录，结果确认前不要重新运行。",
    "screen_peek_unknown": "设备截图结果未确认；核对该截图任务与设备记录，不要重放。",
    "reality_location_unavailable": "确认手机陪伴终端已连接并上报未过期的前台地点；测试只读取授权快照，不会伪造坐标。",
    "reality_location_not_authorized": "在当前角色的现实触及授权中允许读取手机摘要，并确认手机终端与核心管理员私聊已配对。",
    "proactive_diagnostic_image_unavailable": "确认图片能力和私聊图片发送开关已启用、图库中有可用且授权的原图，并确认本人没有要求停用图库表情。一次性测试忽略日常频率和日限设置。",
    "contact_weather_change_unavailable": "确认角色地点和天气变化开关已设置；该来源需要前后两次新鲜观测，等第二次观测完成后再测。",
    "contact_official_alert_unavailable": "确认角色地点包含预警区域，并在宿主环境配置 QWEATHER_ALERT_API_HOST 与 QWEATHER_ALERT_TOKEN；没有当前覆盖该地点的官方预警时不能完成此分支验收。",
    "contact_balance_unavailable": "在宿主环境配置 COMPANION_V2_BALANCE_URL 和所需认证变量，检查接口可达及余额 JSON 路径；缺少当前有效余额时不能完成此分支验收。",
    "contact_weather_source_changed": "天气观测在测试期间已更新或过期；刷新角色天气状态，再为当前来源启动一次新测试。",
    "contact_weather_change_source_changed": "两次天气观测已变化或过期；等待生产器重新确认当前变化后再测。",
    "contact_official_alert_changed": "预警已更新、撤销或不再覆盖配置地点；刷新官方预警状态后再测。",
    "contact_diagnostic_variant_invalid": "环境与状态变化可选择天气观测、天气变化、官方预警、服务余额、外部资讯或手机终端地点；请重新选择来源。",
    "model_provider_unavailable": "打开「模型路由」，为当前角色的 proactive.compose 选择一个已加载且支持文本生成的 Provider。",
    "model_generation_failed": "查看 proactive.compose 任务的 Provider 返回与用量限制；修复连接、额度或模型错误后，用新的来源验收。",
    "model_budget_exhausted": "检查本地模型每日硬额度；额度恢复或调整策略后再测。管理员测试仍会真实调用模型，因此遵守本地用量上限。",
    "model_budget_soft_deferred": "本次 proactive.compose 被本地每日软限额暂缓；检查软限额设置，额度恢复后再测。",
    "persona_review_denied": "检查 proactive.judge 的 Provider、角色设定和拒绝理由；调整后重新验收，不能绕过人格复核。",
    "delivery_result_unknown": "投递结果未知；核对核心管理员私聊与平台消息记录。确认是否收到前不要重试本次测试。",
    "delivery_failed": "检查 OneBot 私聊连接、机器人在线状态和平台返回错误；确认没有收到后，再用新的验收尝试重测。",
    "setup_core_window_ambiguous": "检查已配置私聊目标的核心用户身份；主动测试只会发送到唯一且当前有效的核心管理员私聊窗口。",
    "host_persona_binding_changed": "绑定已按当前窗口自动修复；若反复出现，请在「开始使用」重新确认管理员私聊窗口。",
}

PROACTIVE_TEST_FAILURE_LABELS = {
    "dashboard_identity_required": ("authorization", "Dashboard 登录身份不可用"),
    "unbound_identity": ("authorization", "Dashboard 身份尚未完成绑定"),
    "binding_revoked": ("authorization", "Dashboard 身份绑定已撤销"),
    "binding_expired": ("authorization", "Dashboard 身份绑定已过期"),
    "binding_not_active": ("authorization", "Dashboard 身份绑定尚未生效"),
    "binding_rebound": ("authorization", "Dashboard 身份已重新绑定"),
    "scope_installation_mismatch": ("authorization", "Dashboard 身份不属于当前安装"),
    "scope_not_granted": ("authorization", "当前 Dashboard 身份未获该配置范围授权"),
    "core_access_required": ("authorization", "当前 Dashboard 身份没有核心管理权限"),
    "authorization_denied": ("authorization", "当前 Dashboard 身份没有配置管理权限"),
    "authorization_revoked": ("authorization", "当前 Dashboard 身份的管理授权已撤销"),
    "authorization_expired": ("authorization", "当前 Dashboard 身份的管理授权已过期"),
    "authorization_not_active": ("authorization", "当前 Dashboard 身份的管理授权尚未生效"),
    "host_persona_binding_changed": ("binding", "管理员私聊绑定已变化"),
    "contact_attempt_budget": ("attempt_budget", "主动模型调用次数上限已触发"),
    "contact_delivery_budget": ("delivery_budget", "本私聊今日实际联系额度已用完"),
    "contact_permission_required": ("authorization", "主动联系授权未通过"),
    "contact_purpose_paused": ("purpose", "所选联系目的当前已暂停"),
    "contact_quiet_hours": ("settings", "当前处于免打扰时段"),
    "model_task_unregistered": ("model_route", "主动联系模型任务未配置"),
    "contact_diagnostic_source_required": ("trusted_candidate", "当前用途没有可核验的测试机会"),
    "contact_diagnostic_variant_invalid": ("trusted_candidate", "测试来源选择无效"),
    "contact_weather_unavailable": ("trusted_candidate", "天气观测来源不可用"),
    "contact_weather_change_unavailable": ("trusted_candidate", "天气变化来源不可用"),
    "contact_official_alert_unavailable": ("trusted_candidate", "官方预警来源不可用"),
    "contact_balance_unavailable": ("trusted_candidate", "服务余额来源不可用"),
    "reality_location_unavailable": ("mobile_location_fetch", "手机终端没有可用的真实地点快照"),
    "reality_location_not_authorized": ("mobile_location_fetch", "手机终端地点读取未获授权"),
    "reality_location_label_unavailable": ("mobile_location_fetch", "手机快照没有可分享的地点标签"),
    "reality_location_changed": ("mobile_location_fetch", "手机地点在读取过程中发生变化"),
    "proactive_diagnostic_image_unavailable": ("image_attachment", "没有可用的授权主动图片素材"),
    "contact_weather_source_changed": ("trusted_candidate", "天气观测来源已变化"),
    "contact_weather_change_source_changed": ("trusted_candidate", "天气变化来源已变化"),
    "contact_official_alert_changed": ("trusted_candidate", "官方预警来源已变化"),
    "model_provider_unavailable": ("model_route", "主动联系模型 Provider 不可用"),
    "model_generation_failed": ("model_generation", "主动联系模型生成失败"),
    "model_budget_exhausted": ("model_generation", "主动消息模型每日硬额度已用完"),
    "model_budget_soft_deferred": ("model_generation", "主动消息模型生成被每日软限额暂缓"),
    "persona_review_denied": ("persona_review", "人格复核未放行"),
    "delivery_result_unknown": ("delivery", "平台投递结果未知"),
    "delivery_failed": ("delivery", "平台投递失败"),
}

PROACTIVE_TEST_STAGE_SUGGESTIONS = {
    "authorization": "先看具体原因：登录身份缺失或使用 API Key，与本体核心权限不足、已撤销是不同问题；请勿重复启动真实投递测试。",
    "binding": "确认首次绑定，或确保共享配置中只有一个精确私聊目标对应当前核心管理员身份。",
    "runtime": "在宿主管理页检查 V2 运行实例、角色载入和路由注册状态。",
    "settings": "检查主动联系总开关、目的开关及角色/关系/窗口各层的有效配置。",
    "purpose": "检查该用途是否启用、暂停时间是否结束，以及当前权限是否覆盖该私聊。",
    "trusted_candidate": "检查所选用途的测试条件；事件类等待当前有效来源，关系表达类确认私聊窗口和用途开关。不要手工补造事件。",
    "news_fetch": "检查当前配置的真实资讯源是否可访问、是否返回带日期的有效条目。",
    "weather_fetch": "检查已配置的角色所在地与真实天气接口；测试会即时获取观测。",
    "screen_capture": "检查已授权的截图设备及本次任务记录；未知结果不能重拍。",
    "screen_interpretation": "检查 screen.interpretation 的视觉模型、实际截图原件及分析任务记录。",
    "mobile_location_fetch": "检查现实触及模块、手机端配对、位置读取授权和本次地点快照时效。",
    "image_attachment": "检查私聊表情边界、已授权图库素材、图片检索和 QQ 图片投递能力。",
    "model_route": "在模型路由中为 proactive.compose 选择可用文本 Provider。",
    "attempt_budget": "该上限已不再单独限制；现在只按窗口每日实际联系额度限制发送。",
    "delivery_budget": "查看「主动联系」中的每日实际联系上限与待回执发送。",
    "model_generation": "检查 proactive.compose Provider、任务参数、服务可达性与模型用量。",
    "persona_review": "检查 proactive.judge 路由、角色设定和复核理由；不要跳过复核。",
    "delivery": "检查 OneBot 连接和平台回执；未知结果先核对收件窗口，禁止重发。",
    "receipt": "检查同一 attempt 的平台投递回执和核心管理员私聊是否实际收到。",
}

PROACTIVE_TEST_STAGE_ORDER = (
    "authorization", "binding", "runtime", "settings", "purpose", "news_fetch", "weather_fetch",
    "screen_capture", "screen_interpretation", "mobile_location_fetch", "image_attachment", "trusted_candidate",
    "attempt_budget", "delivery_budget",
    "model_route", "model_generation", "persona_review", "delivery", "receipt",
)

PROACTIVE_TEST_STAGE_BY_CODE = {
    "dashboard_identity_required": "authorization",
    "unbound_identity": "authorization",
    "binding_revoked": "authorization",
    "binding_expired": "authorization",
    "binding_not_active": "authorization",
    "binding_rebound": "authorization",
    "scope_installation_mismatch": "authorization",
    "scope_not_granted": "authorization",
    "core_access_required": "authorization",
    "authorization_denied": "authorization",
    "authorization_revoked": "authorization",
    "authorization_expired": "authorization",
    "authorization_not_active": "authorization",
    "setup_binding_confirmation_required": "binding",
    "setup_core_window_not_configured": "binding",
    "setup_core_window_ambiguous": "binding",
    "setup_private_window_required": "binding",
    "host_persona_binding_changed": "binding",
    "staging_not_initialized": "runtime",
    "proactive_diagnostic_unavailable": "runtime",
    "contact_permission_required": "settings",
    "contact_purpose_paused": "purpose",
    "contact_quiet_hours": "purpose",
    "contact_diagnostic_source_required": "trusted_candidate",
    "contact_diagnostic_variant_invalid": "trusted_candidate",
    "reality_location_unavailable": "mobile_location_fetch",
    "reality_location_not_authorized": "mobile_location_fetch",
    "reality_location_label_unavailable": "mobile_location_fetch",
    "reality_location_changed": "mobile_location_fetch",
    "model_task_unregistered": "model_route",
    "model_provider_unavailable": "model_route",
    "model_budget_exhausted": "model_generation",
    "model_budget_soft_deferred": "model_generation",
    "delivery_result_unknown": "delivery",
    "delivery_failed": "delivery",
}


class SetupWebAPI:
    def __init__(self, controller):
        self.controller = controller
        self.progress = SetupStateStore(controller.data_root)
        self._test_lock = asyncio.Lock()
        self._test_task = None

    def routes(self):
        return [
            (PREFIX + "/status", self.status, ["GET"], "Read persistent Companion V2 setup progress"),
            (PREFIX + "/upgrade", self.prepare_upgrade, ["POST"], "Confirm existing legacy account and persona mappings"),
            (PREFIX + "/targets", self.api_targets, ["GET"], "Read connected OneBot accounts and native personas"),
            (PREFIX + "/binding/confirm", self.confirm_binding, ["POST"], "Confirm the Companion V2 core administrator window"),
            (PREFIX + "/model", self.model, ["POST"], "Choose the initial Companion V2 text provider"),
            (PREFIX + "/model-tasks", self.model_tasks, ["GET"], "Read per-task model routes for the setup role"),
            (PREFIX + "/model-tasks/configure", self.configure_model_task, ["POST"], "Configure one setup-role model task"),
            (PREFIX + "/profile", self.profile, ["GET"], "Read setup-role profile and relationship cards"),
            (PREFIX + "/profile-draft", self.profile_draft, ["POST"], "Draft role canon or relationship card from the current persona"),
            (PREFIX + "/profile/save", self.save_profile, ["POST"], "Save reviewed setup profile fields"),
            (PREFIX + "/host/start", self.reconnect_host, ["POST"], "Reconnect a completed Companion V2 setup"),
            (PREFIX + "/prepare", self.prepare, ["POST"], "Prepare the confirmed Companion V2 profile"),
            (PREFIX + "/complete", self.complete, ["POST"], "Record a completed Companion V2 setup section"),
            (PREFIX + "/proactive-test", self.proactive_test, ["POST"], "Run a one-time proactive contact through the V2 contact and delivery pipeline"),
            (PREFIX + "/proactive-test/status", self.proactive_test_status, ["GET"], "Read the existing proactive test receipt without host discovery"),
            (PREFIX + "/proactive-diagnostic", self.proactive_diagnostic, ["POST"], "Run a confirmed purpose-specific proactive diagnostic to the core administrator"),
            (PREFIX + "/proactive-test/confirm", self.confirm_proactive_test, ["POST"], "Record administrator confirmation of the existing test message"),
        ]

    @staticmethod
    def _admin():
        username = request.username
        if type(username) is not str or not username.strip() or username.startswith("api_key:"):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard as an administrator")
        if request.query:
            raise TaskError("setup_request_invalid", "setup routes do not accept query overrides")
        return username

    @staticmethod
    async def _body(expected: set[str], *, optional: set[str] | frozenset[str] = frozenset(), limit: int = 4096) -> dict:
        raw = await request.body()
        if len(raw) > limit:
            raise ValueError("request is too large")

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate field")
                result[key] = value
            return result

        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite value")))
        if (type(value) is not dict or not expected <= set(value)
                or not set(value) <= expected | set(optional)):
            raise ValueError("unexpected fields")
        return value

    @staticmethod
    def _response(result: Outcome):
        from .web_api import outcome_response
        return outcome_response(result)

    @staticmethod
    def _error(code: str, reason: str):
        return SetupWebAPI._response(Outcome.failed(code, reason))

    def _binding(self) -> dict:
        getter = getattr(self.controller, "binding_state", None)
        if not callable(getter):
            return {"bound": False, "confirmed": False, "pending": False}
        return getter()

    def _model_providers(self) -> list[dict]:
        context = getattr(self.controller, "context", None)
        expected_type = text_provider_predicate(getattr(self.controller, "native_types", None))
        return text_provider_catalog(context, expected_type) if context is not None else []

    def _configured_provider_id(self) -> str:
        config = getattr(self.controller, "config", {})
        section = config.get("model", {}) if isinstance(config, dict) else {}
        return str(section.get("provider_id", "")) if isinstance(section, dict) else ""

    def _model_selection(self):
        context = getattr(self.controller, "context", None)
        expected_type = text_provider_predicate(getattr(self.controller, "native_types", None))
        return (resolve_bootstrap_provider(context, expected_type, self._configured_provider_id())
                if context is not None else None)

    def _model_catalog(self):
        selection = self._model_selection()
        providers = self._model_providers()
        if selection is not None and selection.provider_id and not any(
                item["id"] == selection.provider_id for item in providers):
            providers.append({"id": selection.provider_id, "name": selection.name or selection.provider_id})
        return providers

    def _configured_provider_id(self) -> str:
        config = getattr(self.controller, "config", {})
        section = config.get("model", {}) if isinstance(config, dict) else {}
        return str(section.get("provider_id", "")) if isinstance(section, dict) else ""

    def _status_document(self) -> dict:
        state = self.progress.read()
        binding = self._binding()
        status = self.controller.status()
        confirmed = bool(binding.get("bound") and not binding.get("pending"))
        prepared = bool(getattr(self.controller, "runtime", None)
                        and (getattr(self.controller, "session", None)
                             or getattr(self.controller, "last_session", None)))
        completed = list(state["completed"])
        skipped = list(state["skipped"])
        selection = self._model_selection()
        selection_id = (selection.provider_id
                        if selection is not None and selection.source in ("auto", "configured")
                        else "")
        if not confirmed:
            step = "binding"
        elif state["ready"]:
            step = "complete"
        elif "model" not in completed and "model" not in skipped:
            step = "model"
        elif not selection_id:
            step = "model"
        else:
            step = next((name for name in SETUP_STEPS if name not in completed and name not in skipped),
                        "proactive_test")
        return {"step": step,
                "binding": binding, "binding_confirmed": confirmed, "prepared": prepared,
                "completed": completed, "skipped": skipped,
                "model": {"provider_id": selection_id or self._configured_provider_id(),
                          "provider_source": (selection.source if selection_id and selection is not None
                                              else "unavailable"),
                          "providers": self._model_catalog()},
                "proactive_test": state["proactive_test"], "ready": state["ready"],
                "settings_error": getattr(getattr(self.controller, "settings_error", None), "code", None),
                "host": status}

    async def _test_progress(self, username: str) -> dict:
        saved = self.progress.read()
        receipt = saved["proactive_test"]
        if (receipt and receipt["state"] == "started"
                and (self._test_task is None or self._test_task.done())):
            async with self._test_lock:
                saved = self.progress.read()
                receipt = saved["proactive_test"]
                if (receipt and receipt["state"] == "started"
                        and (self._test_task is None or self._test_task.done())):
                    await self._restore_test_contact_permission(username, receipt["attempt_id"])
                    saved = self.progress.finish_proactive_test(outcome="unknown",
                        finished_at=datetime.now(timezone.utc).isoformat(), error_code="setup_test_interrupted")
        return saved

    async def proactive_test_status(self):
        try:
            saved = await self._test_progress(self._admin())
            return self._response(Outcome.success({"proactive_test": saved["proactive_test"],
                                                   "ready": saved["ready"]}))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_progress_unavailable", "the existing test receipt could not be read")

    async def status(self):
        try:
            await self._test_progress(self._admin())
            document = self._status_document()
            from .upgrade_setup import UpgradeSetup
            try:
                document["upgrade"], _ = await UpgradeSetup(self.controller).plan()
            except (TaskError, ValueError, OSError, UnicodeError) as error:
                # 升级计划读不出来不该把整页设置进度一起弄没：页面还能显示绑定、
                # 模型和步骤，只把这一块标成不可用，并把原因留在日志里。
                logger.warning("[CompanionV2] 升级计划读取失败 error_type=%s code=%s",
                               type(error).__name__, getattr(error, "code", ""))
                document["upgrade"] = {"present": False, "configured": False, "can_prepare": False,
                                       "unavailable": True,
                                       "reason": getattr(error, "code", None) or type(error).__name__}
            names = display_names(self.controller)
            if document["binding"].get("participant_id"):
                document["binding"] = (await names.targets([document["binding"]]))[0]
            self._admin()
            return self._response(Outcome.success(document))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, OSError, UnicodeError) as error:
            logger.warning("[CompanionV2] 设置进度读取失败 error_type=%s detail=%s",
                           type(error).__name__, str(error)[:200])
            return self._error("setup_progress_unavailable", "the saved setup progress cannot be read")

    async def prepare_upgrade(self):
        try:
            self._admin()
            body = await self._body({"confirmed", "plan_token"})
            if body["confirmed"] is not True or type(body["plan_token"]) is not str:
                raise ValueError("review confirmation is required")
            from .upgrade_setup import UpgradeSetup
            return self._response(Outcome.success(await UpgradeSetup(self.controller).prepare(body["plan_token"])))
        except (TaskError, HostControlError) as error:
            return self._error(error.code, str(error))
        except (ValueError, OSError, UnicodeError):
            return self._error("upgrade_prepare_failed", "旧版连接准备未完成，请保留来源并重新读取状态。")

    async def api_targets(self):
        try:
            self._admin()
            context = self.controller.context
            accounts = self.controller.session_topology.accounts()
            persona_names = await available_personas(context)
            from .multi_settings import MultiHostSettings
            try:
                # The runtime may already share one persona role across Bot
                # accounts; use the same normalized mapping in the setup picker.
                mapping_rows = [{"bot_id": item.bot_id, "persona_id": item.persona_id,
                                 "role_id": item.role_id}
                                for item in MultiHostSettings.parse(self.controller.config).personas]
            except (HostControlError, ValueError):
                mapping_rows = self.controller.config.get("multi_host", {}).get("personas", [])
            choices = []
            for account in accounts:
                names = [*persona_names, "[%None]"]
                for persona_id in names:
                    mapping = next((item for item in mapping_rows
                                    if item.get("bot_id") == account["bot_id"] and item.get("persona_id") == persona_id), None)
                    choices.append({"platform_id": account["platform_id"], "account_id": account["account_id"],
                                    "bot_id": account["bot_id"], "persona_id": persona_id,
                                    "role_id": mapping["role_id"] if mapping else derive_role_id(account["account_id"], persona_id),
                                    "label": persona_id, "mapped": mapping is not None})
            # When the shared host is assembled, use its normalized settings so
            # a persona that is shared by several Bot accounts reports the same
            # canonical role id that the role editor and runtime use.
            inventory_settings = getattr(self.controller, "settings", None)
            if inventory_settings is not None and not hasattr(inventory_settings, "targets"):
                # Single-target HostSettings has no shared account inventory.
                inventory_settings = None
            directory = await self.controller.session_topology.inventory_report(settings=inventory_settings)
            targets = directory["targets"]
            # The setup page runs before the first runtime profile exists, so
            # page/targets cannot supply migration routes yet. The setup catalog
            # must still describe which native roles can receive an old install.
            from .legacy_api import PREFIX as LEGACY_PREFIX
            from .role_paths import role_legacy_paths
            inventory_roles = {}
            inventory = getattr(getattr(self.controller, "web_api", None), "target_inventory", None)
            if callable(inventory):
                try:
                    inventory_roles = {
                        (target.get("target_id"), role.get("role_id")): role
                        for target in inventory() for role in target.get("roles", [])
                        if target.get("target_id") and role.get("role_id")
                    }
                except Exception:
                    inventory_roles = {}
            for target in targets:
                scope_kind = target.get("scope_kind")
                for role in target.get("roles", []):
                    target_id, role_id = target.get("target_id"), role.get("role_id")
                    matched = inventory_roles.get((target_id, role_id))
                    if matched:
                        for key in ("role_name", "legacy_api", "legacy_group_data_api"):
                            if not role.get(key) and matched.get(key):
                                role[key] = matched[key]
                        role["persona_ids"] = list(dict.fromkeys(
                            [*(role.get("persona_ids") or []), *(matched.get("persona_ids") or [])]))
                    if target_id and role_id:
                        if getattr(self.controller, "_multi_host", False):
                            paths = role_legacy_paths(target_id, role_id, scope_kind)
                            if not role.get("legacy_api"):
                                role["legacy_api"] = paths["legacy_api"]
                            if not role.get("legacy_group_data_api"):
                                role["legacy_group_data_api"] = paths["legacy_group_data_api"]
                        elif scope_kind != "group" and not role.get("legacy_api"):
                            # The single-target host mounts migration at the root.
                            role["legacy_api"] = LEGACY_PREFIX
            names = display_names(self.controller)
            accounts = await names.targets(accounts)
            targets = await names.targets(targets)
            self._admin()
            return self._response(Outcome.success({"accounts": accounts, "personas": choices,
                                                    "targets": targets,
                                                    "directory": {"complete": directory["complete"],
                                                                  "issues": directory["issues"]}}))
        except TaskError as error:
            return self._error(error.code, str(error))
        except Exception as error:
            logger.exception("AstrBot session topology inventory failed")
            detail = " ".join(str(error).split())
            reason = "connected account and persona inventory is unavailable"
            if detail:
                reason += f" ({type(error).__name__}: {detail[:512]})"
            else:
                reason += f" ({type(error).__name__})"
            return self._error("setup_targets_unavailable", reason)

    async def confirm_binding(self):
        try:
            self._admin()
            await self._body(set())
            confirm = getattr(self.controller, "confirm_binding", None)
            if not callable(confirm):
                raise TaskError("setup_binding_unavailable", "the active host cannot confirm a private window")
            result = confirm()
            if result.get("confirmed") is not True:
                raise TaskError(result.get("reason", "setup_binding_missing"),
                                result.get("detail", "send 我会永远陪着你 from the core administrator private window first"))
            return self._response(Outcome.success({"binding": self._binding(), "confirmed": True}))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError):
            return self._error("setup_binding_confirmation_failed", "the pending private window could not be confirmed")

    async def model(self):
        try:
            self._admin()
            raw = await request.body()
            if len(raw) > 4096:
                raise ValueError("request is too large")
            data = json.loads(raw.decode("utf-8"))
            if type(data) is not dict or set(data) != {"provider_id"}:
                raise ValueError("invalid model fields")
            provider_id = data["provider_id"]
            if type(provider_id) is not str or not provider_id.strip() or len(provider_id) > 512:
                raise ValueError("invalid provider")
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_model_invalid", "choose a loaded AstrBot text provider and retry")

        stage = "provider_validation"
        try:
            status = self._status_document()
            if status["step"] != "model":
                raise TaskError("setup_step_order_invalid", "confirm the core administrator window before choosing a model")
            available = {item["id"] for item in status["model"]["providers"]}
            provider = self.controller.context.get_provider_by_id(provider_id)
            expected_type = text_provider_predicate(getattr(self.controller, "native_types", None))
            if (provider_id not in available or expected_type is None
                    or not supported_text_provider(provider, expected_type)):
                raise TaskError("host_provider_unavailable", "choose a loaded AstrBot text provider supported by V2")
            config = self.controller.config
            if not isinstance(config, dict):
                raise TaskError("setup_config_unavailable", "the plugin configuration cannot be edited here")
            save = getattr(self.controller, "save_plugin_config", None)
            if not callable(save):
                raise TaskError("setup_config_save_unavailable", "restart the plugin with Dashboard configuration support")
            candidate = deepcopy(dict(config))
            model_settings = candidate.setdefault("model", {})
            if type(model_settings) is not dict:
                raise TaskError("setup_config_invalid", "the plugin model settings have an invalid shape")
            model_settings["provider_id"] = provider_id
            profiles = model_settings.get("additional_providers")
            if type(profiles) is list:
                model_settings["additional_providers"] = [
                    profile for profile in profiles
                    if not isinstance(profile, dict) or profile.get("provider_id") != provider_id
                ]
            if model_settings.get("fallback_provider_id") == provider_id:
                model_settings["fallback_provider_id"] = ""
            stage = "config_persistence"
            save(candidate)
            stage = "settings_refresh"
            refresh = getattr(self.controller, "refresh_settings", None)
            if callable(refresh):
                refresh()
            settings_error = getattr(self.controller, "settings_error", None)
            if settings_error is not None:
                raise TaskError(settings_error.code, str(settings_error))
            stage = "profile_initialization"
            initialized = await self.controller.initialize_profile()
            if initialized.failure is not None:
                if initialized.failure.code != "host_must_stop":
                    raise TaskError(initialized.failure.code, initialized.failure.reason)
                # The setup guide can be reopened while the shared host is
                # already running. The provider is saved and the current host
                # keeps serving; it will use the new selection after reload.
                return self._response(Outcome.success({"provider_id": provider_id,
                    "prepared": True, "restart_required": True,
                    "status": self._status_document()}))
            return self._response(Outcome.success({"provider_id": provider_id,
                "prepared": True, "status": self._status_document()}))
        except TaskError as error:
            return self._error(error.code, str(error))
        except Exception as error:
            logger.warning("[CompanionV2] Initial model setup failed stage=%s error_type=%s",
                           stage, type(error).__name__)
            return self._error("setup_model_apply_failed",
                               "the selected provider was accepted, but setup could not save or initialize it")

    async def reconnect_host(self):
        try:
            self._admin()
            await self._body(set())
            state = self.progress.read()
            if not state["ready"]:
                raise TaskError("setup_completion_required", "complete the first-run setup before reconnecting")
            binding = self._binding()
            if not binding.get("bound") or binding.get("pending"):
                raise TaskError("setup_binding_confirmation_required", "confirm the core administrator private window first")
            refresh = getattr(self.controller, "refresh_settings", None)
            if callable(refresh):
                refresh()
            settings_error = getattr(self.controller, "settings_error", None)
            if settings_error is not None:
                raise TaskError(settings_error.code, str(settings_error))
            result = await self.controller.start()
            if result.failure is not None:
                raise TaskError(result.failure.code, result.failure.reason)
            return self._response(Outcome.success({"started": True, "status": self._status_document()}))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_reconnect_failed", "the completed V2 host could not reconnect")

    async def prepare(self):
        try:
            self._admin()
            await self._body(set())
            refresh = getattr(self.controller, "refresh_settings", None)
            if callable(refresh):
                refresh()
            binding = self._binding()
            if not binding.get("bound") or binding.get("pending"):
                raise TaskError("setup_binding_confirmation_required", "confirm the core administrator private window first")
            if getattr(self.controller, "settings_error", None) is not None:
                error = self.controller.settings_error
                raise TaskError(error.code, str(error))
            result = await self.controller.initialize_profile()
            if result.failure is not None:
                raise TaskError(result.failure.code, result.failure.reason)
            return self._response(Outcome.success({"prepared": True, "profile": result.unwrap(),
                                                  "status": self._status_document()}))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError):
            return self._error("setup_prepare_failed", "the confirmed V2 profile could not be prepared")

    async def complete(self):
        try:
            self._admin()
            data = await self._body({"step", "skipped"})
            step, skipped = data["step"], data["skipped"]
            if step not in SETUP_STEPS or type(skipped) is not bool:
                raise ValueError("invalid setup step")
            if step == "model" and skipped:
                raise TaskError("setup_model_required", "choose a V2 text provider before continuing")
            state = self.progress.read()
            binding = self._binding()
            if not binding.get("bound") or binding.get("pending"):
                raise TaskError("setup_binding_confirmation_required", "confirm the core administrator private window first")
            prepared = bool(getattr(self.controller, "runtime", None)
                            and (getattr(self.controller, "session", None)
                                 or getattr(self.controller, "last_session", None)))
            position = SETUP_STEPS.index(step)
            if any(name not in state["completed"] + state["skipped"] for name in SETUP_STEPS[:position]):
                raise TaskError("setup_step_order_invalid", "finish the previous setup section first")
            if step != "model" and not prepared:
                refresh = getattr(self.controller, "refresh_settings", None)
                if callable(refresh):
                    refresh()
                settings_error = getattr(self.controller, "settings_error", None)
                if settings_error is not None:
                    raise TaskError(settings_error.code, str(settings_error))
                result = await self.controller.initialize_profile()
                if result.failure is not None:
                    raise TaskError(result.failure.code, result.failure.reason)
                prepared = True
            if step == "model":
                refresh = getattr(self.controller, "refresh_settings", None)
                if callable(refresh):
                    refresh()
                settings_error = getattr(self.controller, "settings_error", None)
                if settings_error is not None:
                    raise TaskError(settings_error.code, str(settings_error))
                settings = getattr(self.controller, "settings", None)
                model_settings = getattr(settings, "common", settings)
                if not getattr(model_settings, "provider_id", ""):
                    raise TaskError("setup_model_required", "choose a V2 text provider in Model and connections first")
                if not prepared:
                    result = await self.controller.initialize_profile()
                    if result.failure is not None:
                        raise TaskError(result.failure.code, result.failure.reason)
            state = self.progress.complete(step, skipped=skipped)
            return self._response(Outcome.success({"completed": state["completed"],
                "skipped": state["skipped"], "step": step, "skipped_this_step": skipped,
                "prepared": bool(getattr(self.controller, "runtime", None)
                                  and (getattr(self.controller, "session", None)
                                       or getattr(self.controller, "last_session", None)))}))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_request_invalid", "finish or skip the current setup section")

    async def _test_context(self):
        session, context, instruction = await self._setup_context()
        if context.scope.kind is not ScopeKind.PRIVATE:
            raise TaskError("setup_private_window_required", "the active test requires the confirmed private window")
        return session, context, instruction

    def _canonical_role(self, role_id):
        settings = getattr(self.controller, "settings", None)
        canonical = getattr(settings, "canonical_role_id", None)
        return canonical(role_id) if callable(canonical) else str(role_id or "")

    @staticmethod
    def _context_role(context):
        role = getattr(getattr(context, "role_binding", None), "role", None)
        return getattr(role, "subject_id", None)

    def _role_matches_binding(self, binding_role, context):
        """角色比对容错：任一侧缺信息就不校验，别名链视为同一角色。"""
        actual = self._context_role(context)
        if not binding_role or not actual:
            return True
        return self._canonical_role(binding_role) == self._canonical_role(actual)

    def _repair_parked_binding(self, target, context):
        """窗口目标没变时，按当前角色重写过期的人格映射，而不是中断流程。"""
        actual = self._context_role(context)
        root = getattr(self.controller, "binding_root", None)
        if not actual or root is None:
            return False
        parked = read_binding(root)
        if parked is None:
            return False
        settings = getattr(self.controller, "settings", None)
        wanted = self._canonical_role(actual)
        matches = [item for item in getattr(settings, "personas", ())
                   if item.bot_id == target.bot_id and self._canonical_role(item.role_id) == wanted]
        if len(matches) != 1:
            return False
        persona_id = matches[0].persona_id
        if normalize_persona_id(persona_id) == normalize_persona_id(parked.persona_id):
            return False
        try:
            write_binding(root, PendingBinding(parked.platform_id, parked.account_id,
                parked.participant_id, persona_id, parked.confirmed))
        except (OSError, ValueError):
            return False
        logger.info("[CompanionV2] 管理员私聊绑定已按当前窗口人格自动修复 target=%s", target.target_id)
        return True

    async def _setup_context_with_target(self):
        binding = self._binding()
        if not binding.get("bound") or binding.get("pending"):
            code = binding.get("error") if type(binding.get("error")) is str else None
            if code == "setup_core_window_ambiguous":
                raise TaskError(code, "more than one configured private target belongs to a core administrator")
            raise TaskError("setup_binding_confirmation_required", "confirm the core administrator private window first")
        session = self.controller.session or self.controller.last_session
        if session is None:
            raise TaskError("staging_not_initialized", "initialize the selected V2 role first")
        if getattr(self.controller, "_multi_host", False):
            parked = self.controller._parked_binding()
            if parked is not None:
                target = self.controller._core_target(parked)
            elif binding.get("recovered"):
                resolver = getattr(self.controller, "proactive_diagnostic_target", None)
                target = await resolver() if callable(resolver) else None
            else:
                target = None
            if target is None:
                raise TaskError("setup_core_window_not_configured",
                                "add the confirmed administrator private window to the shared host first")
            if (target.platform_id, target.account_id, target.participant_id) != (
                    binding.get("platform_id"), binding.get("account_id"), binding.get("participant_id")):
                raise TaskError("setup_binding_confirmation_required", "the confirmed administrator private window changed")
            context, instruction = await session.synchronize(target)
            current = self._binding()
            if (not current.get("bound") or current.get("pending")
                    or (target.platform_id, target.account_id, target.participant_id) != (
                        current.get("platform_id"), current.get("account_id"), current.get("participant_id"))):
                raise TaskError("setup_binding_confirmation_required", "the administrator private window changed during setup")
            if not self._role_matches_binding(current.get("role_id"), context):
                # 窗口目标没变时，角色漂移只修绑定、不阻断测试。
                if not self._repair_parked_binding(target, context):
                    logger.warning("[CompanionV2] 管理员私聊窗口角色与绑定不一致，按当前窗口继续 code=host_persona_binding_changed")
            return session, context, instruction, target
        else:
            context = session.profile.target
            instruction = await session.bridge.verify_role(context.scope, context.role_binding)
            return session, context, instruction, None

    async def _setup_context(self):
        session, context, instruction, _target = await self._setup_context_with_target()
        return session, context, instruction

    async def _proactive_diagnostic_target(self):
        binding = self._binding()
        if binding.get("bound") and not binding.get("pending"):
            return None
        if binding.get("pending"):
            raise TaskError("setup_binding_confirmation_required",
                            "finish confirming the pending core administrator window first")
        resolver = getattr(self.controller, "proactive_diagnostic_target", None)
        if not getattr(self.controller, "_multi_host", False) or not callable(resolver):
            raise TaskError("setup_binding_confirmation_required",
                            "confirm the core administrator private window first")
        return await resolver()

    async def _proactive_diagnostic_context(self, fallback_target=None):
        if fallback_target is None:
            return await self._setup_context()
        session = self.controller.session or self.controller.last_session
        if session is None:
            raise TaskError("staging_not_initialized", "initialize the selected V2 role first")
        context, instruction = await session.synchronize(fallback_target)
        binding = self._binding()
        if (not binding.get("bound") or binding.get("pending")
                or (binding.get("platform_id"), binding.get("account_id"), binding.get("participant_id")) !=
                (fallback_target.platform_id, fallback_target.account_id, fallback_target.participant_id)):
            raise TaskError("setup_binding_confirmation_required",
                            "the core administrator binding changed during the diagnostic")
        if not self._role_matches_binding(binding.get("role_id"), context):
            if not self._repair_parked_binding(fallback_target, context):
                logger.warning("[CompanionV2] 诊断期间管理员角色与绑定不一致，按当前窗口继续 code=host_persona_binding_changed")
        resolver = getattr(self.controller, "proactive_diagnostic_target", None)
        if not callable(resolver) or await resolver() != fallback_target:
            raise TaskError("setup_binding_confirmation_required",
                            "the core administrator private target changed during the diagnostic")
        if context.scope.kind is not ScopeKind.PRIVATE:
            raise TaskError("setup_private_window_required", "the core administrator target must be private")
        return session, context, instruction

    async def _role_view(self):
        if not getattr(self.controller, "_multi_host", False):
            return self.controller
        _session, context, _instruction, target = await self._setup_context_with_target()
        role_id = self._context_role(context) or self._binding().get("role_id") or ""
        if not role_id:
            raise TaskError("setup_binding_confirmation_required", "confirm the core administrator private window first")
        from .multi_api import TargetControllerView
        return TargetControllerView(self.controller, target.target_id, role_id)

    @staticmethod
    def _operator(controller, username):
        return dashboard_operator(controller.runtime, username, at=controller.now())

    async def model_tasks(self):
        try:
            username = self._admin()
            _session, context, _instruction = await self._setup_context()
            from companion_v2.model_gateway.management import ModelManagement
            manager = ModelManagement(_session.application, context)
            result = require(manager.status(self._operator(self.controller, username), layer="role"))
            names = {item["id"]: item["name"] for item in self._model_providers()}
            for provider in result["providers"]:
                provider["name"] = names.get(provider["id"], provider["id"])
            return self._response(Outcome.success(result))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_model_routes_unavailable", "the setup role's model routes could not be read")

    async def configure_model_task(self):
        try:
            username = self._admin()
            data = await self._body({"kind", "operation_key", "edit_token", "changes", "reset"}, optional={"section"})
            _session, context, _instruction = await self._setup_context()
            from companion_v2.model_gateway.management import ModelManagement
            manager = ModelManagement(_session.application, context)
            saved = require(manager.configure(self._operator(self.controller, username), {**data, "layer": "role"}))
            return self._response(Outcome.success(saved))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_model_routes_invalid", "check the selected task, providers and edit token")

    async def profile(self):
        try:
            username = self._admin()
            session, context, _instruction = await self._setup_context()
            from companion_v2.configuration.management import RoleProfileManagement, RelationshipProfileManagement
            operator = self._operator(self.controller, username)
            return self._response(Outcome.success({
                "role_profile": require(RoleProfileManagement(session.application, context).status(operator)),
                "relationship_profile": require(RelationshipProfileManagement(session.application, context).status(operator)),
            }))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_profile_unavailable", "the selected role and private relationship profiles could not be read")

    async def profile_draft(self):
        try:
            self._admin()
            view = await self._role_view()
            from .persona_workshop_api import PersonaWorkshopWebAPI
            return await PersonaWorkshopWebAPI(view).profile_draft()
        except TaskError as error:
            return self._error(error.code, str(error))

    async def save_profile(self):
        try:
            username = self._admin()
            data = await self._body({"target", "operation_key", "edit_token", "changes"},
                                    limit=PROFILE_BODY_LIMIT)
            target, changes = data["target"], data["changes"]
            if (target not in ("role_profile", "relationship_profile")
                    or type(data["operation_key"]) is not str or not data["operation_key"].strip()
                    or len(data["operation_key"]) > 128 or type(data["edit_token"]) is not str
                    or len(data["edit_token"]) != 64 or type(changes) is not dict or not changes):
                raise ValueError("invalid profile patch")
            session, context, _instruction = await self._setup_context()
            from companion_v2.configuration.management import RoleProfileManagement, RelationshipProfileManagement
            editor_type = RoleProfileManagement if target == "role_profile" else RelationshipProfileManagement
            saved = require(editor_type(session.application, context).configure(
                self._operator(self.controller, username), {key: data[key] for key in ("operation_key", "edit_token", "changes")}))
            return self._response(Outcome.success(saved))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_profile_invalid", "review the draft fields and save with the current profile version")

    async def confirm_proactive_test(self):
        async with self._test_lock:
            try:
                username = self._admin()
                data = await self._body({"attempt_id"})
                if type(data["attempt_id"]) is not str or not data["attempt_id"]:
                    raise ValueError("invalid test attempt")
                binding = self._binding()
                if not binding.get("bound") or binding.get("pending"):
                    raise TaskError("setup_binding_confirmation_required", "confirm the core administrator private window first")
                await self._restore_test_contact_permission(username, data["attempt_id"])
                state = self.progress.confirm_proactive_test(attempt_id=data["attempt_id"],
                    confirmed_by=username, confirmed_at=datetime.now(timezone.utc).isoformat())
                return self._response(Outcome.success({"state": state["proactive_test"]["state"],
                    "proactive_test": state["proactive_test"],
                    "ready": state["ready"], "replay_allowed": False}))
            except TaskError as error:
                return self._error(error.code, str(error))
            except (ValueError, TypeError, KeyError, OSError, UnicodeError):
                return self._error("setup_test_confirmation_invalid", "confirm receipt of the existing test message")

    async def proactive_test(self):
        async with self._test_lock:
            try:
                username = self._admin()
                data = await self._body({"enable_proactive"})
                if type(data["enable_proactive"]) is not bool or not data["enable_proactive"]:
                    raise TaskError("setup_proactive_opt_in_required",
                                    "enable private proactive contact before starting its one-time test")
                saved = self.progress.read()
                if saved["ready"]:
                    return self._response(Outcome.success({"state": saved["proactive_test"]["state"], "ready": True,
                                                           "replay_allowed": False}))
                receipt = saved["proactive_test"]
                if receipt and receipt["state"] == "started" and self._test_task is not None and not self._test_task.done():
                    return self._response(Outcome.success({"state": "started", "ready": False,
                                                           "replay_allowed": False}))
                if receipt is not None and receipt["state"] not in ("failed",):
                    raise TaskError("setup_test_non_replayable",
                                    "the proactive test already started; its result cannot be replayed")
                active = await self.controller.start()
                if active.failure is not None:
                    raise TaskError(active.failure.code, active.failure.reason)
                started = datetime.now(timezone.utc).isoformat()
                attempt_id = uuid4().hex
                try:
                    self.progress.begin_proactive_test(attempt_id=attempt_id, started_at=started)
                except SetupStepsIncomplete as error:
                    # "The guide is not finished" and "this attempt already ran and
                    # cannot be replayed" have nothing in common. Collapsing both
                    # into setup_test_non_replayable told the operator to hunt a
                    # replay problem while proactive_test was still null, i.e. while
                    # no attempt had ever existed.
                    raise TaskError("setup_steps_incomplete", str(error)) from error
                except ValueError as error:
                    raise TaskError("setup_test_non_replayable", str(error)) from error
                self._test_task = asyncio.create_task(self._execute_proactive_test(username, attempt_id),
                                                       name="companion-v2-setup-proactive-test")
                return self._response(Outcome.success({"state": "started", "attempt_id": attempt_id,
                                                       "ready": False, "replay_allowed": False}))
            except TaskError as error:
                return self._error(error.code, str(error))
            except (ValueError, TypeError, KeyError, OSError, UnicodeError):
                return self._error("setup_test_failed", "the active message test could not be started")

    async def proactive_diagnostic(self):
        async with self._test_lock:
            try:
                username = self._admin()
                data = await self._body({"purpose", "confirm_delivery"},
                    optional={"variant", "synthetic_event", "diagnostic_image"})
                purpose = data["purpose"]
                variant = data.get("variant")
                synthetic_event = data.get("synthetic_event", False)
                diagnostic_image = data.get("diagnostic_image", False)
                if type(purpose) is not str or purpose not in PURPOSES or data["confirm_delivery"] is not True:
                    raise TaskError("proactive_test_confirmation_required",
                                    "choose one proactive purpose and confirm delivery to the core administrator")
                if type(synthetic_event) is not bool:
                    raise TaskError("proactive_test_confirmation_required", "synthetic_event must be an explicit boolean")
                if type(diagnostic_image) is not bool or (diagnostic_image and
                        (purpose != "chat" or synthetic_event or variant is not None)):
                    raise TaskError("proactive_test_confirmation_required",
                                    "an image attachment test requires a source-backed chat diagnostic")
                if variant is not None and (type(variant) is not str or purpose != "environment"
                        or (variant not in DIAGNOSTIC_ENVIRONMENT_VARIANTS and variant != "mobile_location")):
                    raise TaskError("contact_diagnostic_variant_invalid",
                                    "choose a supported environment source for this proactive purpose")
                fallback_target = await self._proactive_diagnostic_target()
                started = datetime.now(timezone.utc).isoformat()
                attempt_id = uuid4().hex
                existing = self.controller.session or self.controller.last_session
                if existing is None or existing.application.state != "running":
                    active = await self.controller.start()
                    if active.failure is not None and active.failure.code != "host_already_started":
                        raise TaskError(active.failure.code, active.failure.reason)
                session, context, instruction = await self._proactive_diagnostic_context(fallback_target)
                if context.scope.kind is not ScopeKind.PRIVATE:
                    raise TaskError("setup_private_window_required", "the core administrator target must be private")

                operator = dashboard_operator(self.controller.runtime, username, at=self.controller.now())
                manager = ContactManagement(session.application, context)
                status = require(manager.status(operator))
                purpose_status = status["purposes"].get(purpose)
                test_policy_override = bool(purpose_status and purpose_status.get("reason") in
                    ("proactive_disabled", "proactive_paused", "proactive_quiet_hours")
                    and status.get("settings", {}).get(purpose + ".enabled") is True)
                if not purpose_status or (not purpose_status["allowed"] and not test_policy_override):
                    code = purpose_status["reason"] if purpose_status else "contact_purpose_unavailable"
                    raise TaskError(code, "the selected proactive purpose is not currently authorized")

                now = session.application.now()
                schedule = getattr(session.application, "contact_schedule", None)
                producer = getattr(schedule, "diagnostic_candidate", None)
                if not synthetic_event and not callable(producer):
                    raise TaskError("proactive_diagnostic_unavailable",
                                    "the active host has no source-backed proactive producer registry")
                route_hint = None
                if variant is not None:
                    if variant == "mobile_location":
                        route_hint = {"id": "reality_location_event", "label": "手机陪伴终端地点来源",
                                      "variant": variant}
                    else:
                        _producer_name, route_id, route_label = DIAGNOSTIC_ENVIRONMENT_VARIANTS[variant]
                        route_hint = {"id": route_id, "label": route_label, "variant": variant}
                try:
                    if synthetic_event:
                        synthetic_producer = getattr(schedule, "synthetic_diagnostic_candidate", None)
                        if not callable(synthetic_producer):
                            raise TaskError("proactive_diagnostic_unavailable",
                                            "the active host has no synthetic diagnostic event registry")
                        candidate = await synthetic_producer(context, purpose, at=now,
                            test_id=attempt_id, variant=variant, role_instruction=instruction)
                    elif variant == "mobile_location":
                        native_extensions = getattr(self.controller, "native_extensions", None)
                        reality = (native_extensions.active_service("reality")
                                   if native_extensions is not None else None)
                        if reality is None:
                            raise TaskError("reality_location_unavailable",
                                            "the Reality mobile location service is not active")
                        _row, _batch, _event, receipt = schedule.diagnostic_receipt(
                            context, purpose, "reality_location_event", now)
                        candidate = await reality.v2_api().mobile_location_diagnostic_candidate(
                            context.scope.native_scope_id, test_id=attempt_id, diagnostic_origin=receipt)
                        if not candidate.get("accepted"):
                            code = candidate.get("reason") or "contact_diagnostic_source_required"
                            raise TaskError(code, "the current authorized mobile location is unavailable")
                    else:
                        candidate = (await producer(context, purpose, at=now, test_id=attempt_id, variant=variant)
                                     if variant is not None else await producer(context, purpose, at=now, test_id=attempt_id))
                except DiagnosticSourceError as error:
                    stages = [{"stage": name, "state": "passed"} for name in
                        ("authorization", "binding", "runtime", "settings", "purpose")]
                    stages.extend(error.stages)
                    failure = self._diagnostic_failure(purpose, route_hint, error.stage, error.code, str(error),
                        stages=stages, route=route_hint, state=error.state, task_id=error.task_id)
                    failure.update(attempt_id=attempt_id, event_mode="source_backed")
                    return self._response(Outcome.success(failure))
                except TaskError as error:
                    failure_stage = PROACTIVE_TEST_FAILURE_LABELS.get(
                        error.code, ("trusted_candidate", "当前来源生产器未能生成有效候选"))[0]
                    stages = [{"stage": name, "state": "passed"} for name in
                        ("authorization", "binding", "runtime", "settings", "purpose")]
                    stages.append({"stage": failure_stage, "state": "failed"})
                    return self._response(Outcome.success(self._diagnostic_failure(
                        purpose, route_hint, failure_stage, error.code, str(error),
                        stages=stages, route=route_hint)))
                opportunity, intents = candidate["opportunity"], candidate["intents"]
                route = candidate["route"]
                label = PURPOSES[purpose]
                result = await session.application.proactive.contact(context, opportunity, intents,
                    role_instruction=candidate["role_instruction"], selection_seed=attempt_id,
                    administrator_test=True, diagnostic_source_backed=not synthetic_event,
                    diagnostic_origin=candidate["diagnostic_origin"],
                    diagnostic_image_intent=("适合轻松、友好、自然日常互动的表情图片；不适合严肃、悲伤、冲突或边界语境。"
                                             if diagnostic_image else None))
                if result.failure is not None:
                    failure = result.failure
                    failure_stage = PROACTIVE_TEST_FAILURE_LABELS.get(
                        failure.code, ("contact_admission", "主动联系准入检查失败"))[0]
                    stages = [{"stage": name, "state": "passed"} for name in
                        ("authorization", "binding", "runtime", "settings", "purpose")]
                    stages.extend(candidate.get("stages", []))
                    stages.append({"stage": "trusted_candidate", "state": "passed"})
                    stages.append({"stage": failure_stage, "state": "failed"})
                    budget_usage = self._diagnostic_attempt_usage(
                        session, context, failure.code)
                    return self._response(Outcome.success(self._diagnostic_failure(
                        purpose, route, failure_stage, failure.code, failure.reason,
                        stages=stages, budget_usage=budget_usage, route=route)))
                task = result.unwrap().task
                if task is None:
                    return self._response(Outcome.success(self._diagnostic_failure(
                        purpose, route, "model_generation", "contact_task_not_started",
                        "the proactive service did not create an execution task", route=route)))

                stages = [{"stage": name, "state": "passed"} for name in
                    ("authorization", "binding", "runtime", "settings", "purpose")]
                stages.extend(candidate.get("stages", []))
                stages.append({"stage": "trusted_candidate", "state": "passed"})
                stages[-1]["detail"] = f"{route.get('label', route.get('id', ''))} · {route.get('variant', '')}".strip(" ·")
                if diagnostic_image:
                    stages.append({"stage": "image_attachment", "state": "pending"})
                stages.extend(({"stage": "model_generation", "state": "running"},
                               {"stage": "persona_review", "state": "pending"},
                               {"stage": "delivery", "state": "pending"},
                               {"stage": "receipt", "state": "pending"}))
                deadline = asyncio.get_running_loop().time() + 150
                observation = None
                while asyncio.get_running_loop().time() < deadline:
                    observed = session.application.proactive.observe(context, task.task_id)
                    if observed.failure is not None:
                        return self._response(Outcome.success(self._diagnostic_failure(
                            purpose, route, "delivery", observed.failure.code, observed.failure.reason,
                            stages=stages, task_id=task.task_id, route=route)))
                    observation = observed.unwrap()
                    current_task = require(session.application.tasks.read(context, task.task_id))
                    if observation["skipped"] or observation["delivery"] in ("delivered", "failed", "unknown", "accepted"):
                        break
                    if current_task.state in ("failed", "cancelled", "expired", "uncertain"):
                        break
                    await asyncio.sleep(0.2)

                current_task = require(session.application.tasks.read(context, task.task_id))
                if observation is None:
                    observation = require(session.application.proactive.observe(context, task.task_id))
                skipped = observation["skipped"]
                delivery = observation["delivery"]
                judge = observation["judge"]
                stage_by_name = {item["stage"]: item for item in stages}
                stage_by_name["model_generation"]["state"] = "passed" if current_task.state == "succeeded" else "failed"
                if diagnostic_image:
                    image_result = (observation.get("stage_metrics") or {}).get("image_attachment", {})
                    image_attached = image_result.get("state") == "passed"
                    stage_by_name["image_attachment"]["state"] = "passed" if image_attached else "failed"
                stage_by_name["persona_review"]["state"] = ("skipped" if skipped else "passed" if observation["judge_allowed"] is True
                                       else "failed" if observation["judge_allowed"] is False
                                       else "not_required" if judge is None else "pending")
                stage_by_name["delivery"]["state"] = ("skipped" if skipped else "passed" if delivery == "delivered"
                                       else "failed" if delivery in ("failed", "unknown")
                                       else "pending")
                stage_by_name["receipt"]["state"] = ("skipped" if skipped else "passed" if delivery == "delivered"
                                      and observation.get("receipt_ref") else
                                      "failed" if delivery in ("failed", "unknown") else "pending")
                state = ("failed" if diagnostic_image and not image_attached else
                         "skipped" if skipped else "passed" if delivery == "delivered"
                         else "unknown" if delivery in ("unknown", "accepted")
                         else "pending" if delivery in (None, "pending", "dispatched")
                         else "failed")
                code = ("proactive_diagnostic_image_unavailable" if diagnostic_image and not image_attached else
                        None if state in ("passed", "skipped") else current_task.reason or (
                    "delivery_result_unknown" if state == "unknown" else "delivery_failed" if state == "failed"
                    else "proactive_test_timeout"))
                return self._response(Outcome.success({"state": state, "purpose": purpose,
                    "purpose_label": label, "variant": route,
                    "route": route,
                    "event_mode": "synthetic_event" if synthetic_event else "source_backed",
                    "recipient": "core_administrator", "attempt_id": attempt_id, "task_id": task.task_id,
                    "task_state": current_task.state, "delivery_state": delivery,
                    "judge_state": judge, "judge_reason": (redact_text(observation["judge_reason"])
                        if observation["judge_reason"] else None),
                    "skip_reason": observation.get("skip_reason"),
                    "model_skip_reason": observation.get("model_skip_reason"),
                    "dedup_reason": observation.get("dedup_reason"),
                    "presentation_reason": observation.get("presentation_reason"),
                    "decision_confidence": observation.get("decision_confidence"),
                    "silence_threshold": observation.get("silence_threshold"),
                    "stage_metrics": observation.get("stage_metrics"),
                    "usage_summary": self._diagnostic_usage_summary(session, context),
                    "receipt_ref": observation["receipt_ref"], "stage": None if state in ("passed", "skipped") else
                        "delivery" if delivery else "model_generation", "code": code,
                    "error": None if code is None else redact_text(current_task.reason or code),
                    "suggestion": (PROACTIVE_TEST_SUGGESTIONS.get(code)
                        or PROACTIVE_TEST_STAGE_SUGGESTIONS.get(stage_by_name.get("delivery", {}).get("stage", "delivery")))
                        if code else None,
                    "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
                    "stages": stages, "replay_allowed": False}))
            except TaskError as error:
                failure_stage = PROACTIVE_TEST_STAGE_BY_CODE.get(error.code, "execution")
                return self._response(Outcome.success(self._diagnostic_failure(
                    data.get("purpose") if "data" in locals() and isinstance(data, dict) else None,
                    None, failure_stage, error.code, str(error))))
            except (ValueError, TypeError, KeyError, OSError, UnicodeError) as error:
                return self._response(Outcome.success(self._diagnostic_failure(
                    None, None, "request", "proactive_test_failed", f"{type(error).__name__}: {error}")))
            except Exception as error:
                return self._response(Outcome.success(self._diagnostic_failure(
                    data.get("purpose") if "data" in locals() and isinstance(data, dict) else None,
                    None, "execution", "proactive_test_failed", f"{type(error).__name__}: {error}")))

    @staticmethod
    def _diagnostic_attempt_usage(session, context, code):
        label = PROACTIVE_TEST_FAILURE_LABELS.get(code)
        if label is None or not label[0].endswith("attempt_budget"):
            return None
        proactive = getattr(session.application, "proactive", None)
        preferences_store = getattr(proactive, "preferences", None)
        budget = getattr(proactive, "budget", None)
        if preferences_store is None or budget is None:
            return None
        try:
            now = session.application.now()
            result = preferences_store.read(context, observed_at=now)
            if result.failure is not None:
                return None
            usage = budget.attempt_usage(context, result.unwrap(), now)
            return usage["session"]
        except Exception:
            # A supplemental counter must not hide the original admission failure.
            return None

    @staticmethod
    def _diagnostic_usage_summary(session, context):
        proactive = getattr(session.application, "proactive", None)
        preferences_store = getattr(proactive, "preferences", None)
        budget = getattr(proactive, "budget", None)
        if preferences_store is None or budget is None:
            return None
        try:
            now = session.application.now()
            result = preferences_store.read(context, observed_at=now)
            if result.failure is not None:
                return None
            return budget.usage_summary(context, result.unwrap(), now)
        except Exception:
            # Reporting is supplemental and must not change the test outcome.
            return None

    @staticmethod
    def _diagnostic_failure(purpose, variant, stage, code, error, *, stages=None, task_id=None,
                            budget_usage=None, route=None, state="failed"):
        label = PROACTIVE_TEST_FAILURE_LABELS.get(code)
        if label:
            title = label[1]
            explanation = title + "。请根据下方阶段记录核对本次实际完成的步骤。"
        else:
            explanation = None
        if stages is None:
            try:
                failed_index = PROACTIVE_TEST_STAGE_ORDER.index(stage)
            except ValueError:
                failed_index = 0
            stages = ([{"stage": name, "state": "passed"}
                       for name in PROACTIVE_TEST_STAGE_ORDER[:failed_index]]
                      + [{"stage": stage, "state": "failed"}])
        return {"state": state, "purpose": purpose,
            "purpose_label": PURPOSES.get(purpose) if purpose else None,
            "variant": (variant if isinstance(variant, dict) else
                        {"id": variant[0], "label": variant[1]} if variant else None),
            "route": route or (variant if isinstance(variant, dict) else None),
            "recipient": "core_administrator", "task_id": task_id, "stage": stage, "code": code,
            "failure_title": title if label else None, "explanation": explanation,
            "budget_usage": budget_usage,
            "error": redact_text(error), "suggestion": PROACTIVE_TEST_SUGGESTIONS.get(code)
                or PROACTIVE_TEST_STAGE_SUGGESTIONS.get(stage, "查看该阶段的宿主日志和当前配置，修复后使用新尝试验收。"),
            "replay_allowed": False, "stages": stages}

    async def _execute_proactive_test(self, username: str, attempt_id: str) -> None:
        attempt = None
        app = context = None
        route = None
        task_id = ""
        stages = [{"stage": name, "state": "passed"} for name in
            ("authorization", "binding", "runtime", "settings", "purpose")]

        def stage_state(name, value):
            row = next((item for item in stages if item["stage"] == name), None)
            if row is not None:
                row["state"] = value

        def report(delivery_state="", receipt_ref=""):
            value = {"stages": stages}
            if route is not None:
                value["route"] = route
            if task_id:
                value["task_id"] = task_id
            if delivery_state:
                value["delivery_state"] = delivery_state
            if receipt_ref:
                value["receipt_ref"] = receipt_ref
            return value
        try:
            session, context, _instruction = await self._test_context()
            app = session.application
            operator = dashboard_operator(self.controller.runtime, username, at=self.controller.now())
            manager = ContactManagement(app, context)
            current = require(manager.status(operator))
            enabled_before = current["settings"]["enabled"]
            self.progress.record_test_contact_permission(attempt_id=attempt_id,
                                                         enabled_before=enabled_before)
            if not enabled_before:
                require(manager.configure(operator, {
                    "operation_key": "setup-test-" + attempt_id,
                    "edit_token": current["edit_token"],
                    "changes": {"enabled": True},
                }))
            self.progress.record_proactive_test_progress(attempt_id=attempt_id, stages=stages)
            now = self.controller.now()
            schedule = getattr(app, "contact_schedule", None)
            producer = getattr(schedule, "diagnostic_candidate", None)
            if not callable(producer):
                raise TaskError("proactive_diagnostic_unavailable",
                                "the active host has no source-backed proactive producer registry")
            try:
                candidate = await producer(context, "chat", at=now)
            except TaskError:
                stages.append({"stage": "trusted_candidate", "state": "failed"})
                self.progress.record_proactive_test_progress(attempt_id=attempt_id, stages=stages)
                raise
            opportunity, intents = candidate["opportunity"], candidate["intents"]
            route = candidate["route"]
            stages.append({"stage": "trusted_candidate", "state": "passed",
                           "detail": f"{route.get('label', route.get('id', ''))} · {route.get('variant', '')}".strip(" ·")})
            stages.extend(({"stage": "model_generation", "state": "running"},
                           {"stage": "persona_review", "state": "pending"},
                           {"stage": "delivery", "state": "pending"},
                           {"stage": "receipt", "state": "pending"}))
            task_id = app.proactive.task_id(context, opportunity, administrator_test=True)
            self.progress.record_proactive_test_progress(attempt_id=attempt_id, route=route,
                stages=stages, task_id=task_id)
            attempt = (app, context, task_id)
            result = await app.proactive.contact(context, opportunity, intents,
                role_instruction=candidate["role_instruction"], selection_seed=attempt_id,
                administrator_test=True, diagnostic_source_backed=True,
                diagnostic_origin=candidate["diagnostic_origin"])
            if result.failure is not None:
                failed_stage = PROACTIVE_TEST_FAILURE_LABELS.get(
                    result.failure.code, ("model_route", "主动联系准入检查失败"))[0]
                stages.append({"stage": failed_stage, "state": "failed"})
                self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                self.progress.finish_proactive_test(outcome="failed",
                    finished_at=datetime.now(timezone.utc).isoformat(), error_code=result.failure.code,
                    report=report())
                return
            task = result.unwrap().task
            if task is None:
                stage_state("model_generation", "failed")
                self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                self.progress.finish_proactive_test(outcome="failed",
                    finished_at=datetime.now(timezone.utc).isoformat(), error_code="setup_contact_not_started",
                    report=report())
                return
            deadline = asyncio.get_running_loop().time() + 150
            last_progress = None
            while asyncio.get_running_loop().time() < deadline:
                receipt = self.progress.read()["proactive_test"]
                if not receipt or receipt["attempt_id"] != attempt_id or receipt["state"] != "started":
                    return
                observed = require(app.proactive.observe(context, task.task_id))
                delivery = observed["delivery"]
                generation = observed.get("generation")
                stage_state("model_generation", "passed" if generation == "succeeded" else "running")
                judge = observed.get("judge")
                stage_state("persona_review", "passed" if observed.get("judge_allowed") is True else
                            "not_required" if judge is None else
                            "running" if judge == "pending" else "pending")
                if delivery in ("pending", "partial", "dispatched"):
                    stage_state("delivery", "running")
                if delivery is None and generation in TERMINAL - {"succeeded"}:
                    current_task = require(app.tasks.read(context, task.task_id))
                    stage_state("model_generation", "failed")
                    for item in stages:
                        if item["stage"] == "model_generation" and current_task.reason:
                            item["detail"] = redact_text(str(current_task.reason))[:256]
                    stage_state("persona_review", "skipped")
                    stage_state("delivery", "skipped")
                    stage_state("receipt", "skipped")
                    self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                    self.progress.finish_proactive_test(
                        outcome="unknown" if generation == "uncertain" else "failed",
                        finished_at=datetime.now(timezone.utc).isoformat(),
                        error_code="setup_contact_generation_failed", report=report())
                    return
                progress = (tuple((item["stage"], item["state"], item.get("detail", ""))
                                  for item in stages), delivery, observed.get("receipt_ref"))
                if progress != last_progress:
                    self.progress.record_proactive_test_progress(
                        attempt_id=attempt_id, stages=stages, route=route,
                        task_id=task.task_id, delivery_state=delivery or "",
                        receipt_ref=observed.get("receipt_ref") or "")
                    last_progress = progress
                if delivery == "delivered":
                    stage_state("delivery", "passed")
                    stage_state("receipt", "passed" if observed.get("receipt_ref") else "failed")
                    self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                    self.progress.finish_proactive_test(outcome="delivered",
                        finished_at=datetime.now(timezone.utc).isoformat(),
                        report=report(delivery, observed.get("receipt_ref") or ""))
                    return
                if delivery == "failed" or observed["skipped"] or observed["judge"] == "denied":
                    stage_state("persona_review", "failed" if observed["judge"] == "denied" else
                                "skipped" if observed["skipped"] else stages[-3]["state"])
                    stage_state("delivery", "failed" if delivery == "failed" else "skipped")
                    stage_state("receipt", "failed" if delivery == "failed" else "skipped")
                    self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                    self.progress.finish_proactive_test(outcome="failed",
                        finished_at=datetime.now(timezone.utc).isoformat(),
                        error_code="setup_contact_not_delivered", report=report(delivery or "skipped"))
                    return
                if delivery == "unknown" or delivery == "accepted":
                    stage_state("delivery", "failed")
                    stage_state("receipt", "failed")
                    self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                    self.progress.finish_proactive_test(outcome="unknown",
                        finished_at=datetime.now(timezone.utc).isoformat(), error_code="delivery_result_unknown",
                        report=report(delivery, observed.get("receipt_ref") or ""))
                    return
                await asyncio.sleep(0.2)
            self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
            stage_state("delivery", "failed")
            stage_state("receipt", "failed")
            self.progress.finish_proactive_test(outcome="unknown",
                finished_at=datetime.now(timezone.utc).isoformat(), error_code="setup_contact_timeout",
                report=report("unknown"))
        except asyncio.CancelledError:
            try:
                if app is not None and context is not None:
                    self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                self.progress.finish_proactive_test(outcome="unknown",
                    finished_at=datetime.now(timezone.utc).isoformat(), error_code="setup_test_interrupted")
            except (TaskError, ValueError, OSError):
                pass
            raise
        except Exception as error:
            code = error.code if isinstance(error, TaskError) else "setup_test_failed"
            outcome = "unknown" if attempt is not None else "failed"
            if app is not None and context is not None:
                try:
                    self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                except Exception:
                    code = "setup_contact_permission_restore_failed"
                    outcome = "unknown"
            try:
                for item in stages:
                    if item["state"] == "running":
                        item["state"] = "failed"
                self.progress.finish_proactive_test(outcome=outcome,
                    finished_at=datetime.now(timezone.utc).isoformat(), error_code=code, report=report())
            except ValueError:
                pass

    async def _restore_test_contact_permission(self, username: str, attempt_id: str) -> None:
        receipt = self.progress.read()["proactive_test"]
        if (not receipt or receipt["attempt_id"] != attempt_id
                or "contact_enabled_before" not in receipt
                or receipt.get("contact_permission_restored")):
            return
        if receipt["contact_enabled_before"]:
            self.progress.mark_test_contact_permission_restored(attempt_id=attempt_id)
            return
        if self.controller.session is None and self.controller.last_session is None:
            loaded = await self.controller.load_existing()
            if loaded.failure is not None:
                raise TaskError(loaded.failure.code, loaded.failure.reason)
        session, context, _instruction = await self._test_context()
        self._restore_test_contact_permission_with_context(
            username, attempt_id, session.application, context)

    def _restore_test_contact_permission_with_context(self, username: str, attempt_id: str,
                                                      app, context) -> None:
        receipt = self.progress.read()["proactive_test"]
        if (not receipt or receipt["attempt_id"] != attempt_id
                or "contact_enabled_before" not in receipt
                or receipt.get("contact_permission_restored")):
            return
        enabled_before = receipt["contact_enabled_before"]
        operator = dashboard_operator(self.controller.runtime, username, at=self.controller.now())
        manager = ContactManagement(app, context)
        current = require(manager.status(operator))
        # Restore the public switch independently from the administrator's
        # temporary grant, so the test can be repeated without hidden reauthorization.
        if not enabled_before:
            data = {
                "operation_key": "setup-test-restore-" + attempt_id,
                "edit_token": current["edit_token"],
                "changes": {"enabled": False},
            }
            if app.state == "running":
                require(manager.configure(operator, data))
            else:
                require(manager.restore_temporary_disable(operator, data))
        self.progress.mark_test_contact_permission_restored(attempt_id=attempt_id)
