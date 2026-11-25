"""Authenticated, persistent first-run setup routes for the Companion V2 host."""

from __future__ import annotations

import asyncio
from copy import copy, deepcopy
from datetime import datetime, timedelta, timezone
import json
import logging
import time as _time
from uuid import uuid4

from astrbot.api.web import request

from companion_v2.adapters.astrbot.memory_owner import read_memory_companion_status
from companion_v2.adapters.astrbot.provider import (
    PROBE_OUTPUT_TOKENS,
    looks_like_text_provider,
)
from companion_v2.adapters.astrbot.provider_transport import extract_error_status_code
from companion_v2.execution.models import TERMINAL, TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind
from companion_v2.proactive.configuration import PURPOSES
from companion_v2.proactive.diagnostic_models import DiagnosticSourceError
from companion_v2.proactive.management import ContactManagement
from companion_v2.proactive.scheduling import DIAGNOSTIC_ENVIRONMENT_VARIANTS
from companion_v2.sources.privacy import redact_text

from .binding_store import PendingBinding, binding_path, read_binding, write_binding
from .bootstrap_provider import (active_chat_provider_info, provider_capabilities,
                                 provider_model_name, resolve_bootstrap_provider,
                                 supported_text_provider, text_provider_catalog,
                                 text_provider_predicate)
from .identity_derivation import derive_bot_id, derive_role_id, normalize_persona_id
from .management_identity import dashboard_operator
from .page_api import read_user_hint_preferences
from .persona_catalog import available_personas
from .settings import HostControlError
from .setup_state import SETUP_STEPS, SetupStateStore, SetupStepsIncomplete
from .display_names import display_names


PREFIX = "/astrbot_plugin_private_companion/setup"
# Profile editors submit the complete edited field set in one request. The
# per-field limits are larger than the small command-route body cap.
PROFILE_BODY_LIMIT = 262144
MODEL_PROBE_TIMEOUT_SECONDS = 15.0
VISION_SETUP_TASKS = (
    "conversation.image_narration",
    "conversation.context_image_caption",
    "screen.interpretation",
    "conversation.image_review",
)
logger = logging.getLogger(__name__)

ROUTE_REJECTION_LABELS = {
    "contact_delivery_budget": "该账号的主动联系额度已用尽",
    "proactive_disabled": "该账号或联系目的未启用主动联系",
    "proactive_paused": "该账号或联系目的处于暂停期",
    "proactive_quiet_hours": "该账号处于免打扰时段",
    "account_link_transport_unavailable": "该账号的平台连接不可用",
    "account_link_route_changed": "关联关系、角色或路由策略已变化",
    "account_link_context_unavailable": "无法解析该账号的有效会话身份",
    "account_link_weight_zero": "该账号的随机权重为 0",
    "delivery_not_sent": "平台明确未发送",
}


def _diagnostic_route_failure(observation):
    rejections = observation.get("route_rejections") or []
    if not rejections:
        return None
    details = []
    for item in rejections:
        member = item.get("member") or {}
        code = item.get("code") or "account_link_context_unavailable"
        target = " · ".join(str(member.get(key) or "") for key in
                            ("platform", "account_id", "native_account_id"))
        details.append(f"{target}：{ROUTE_REJECTION_LABELS.get(code, '路由检查拒绝')}（{code}）")
    return "关联账号路由不可用；" + "；".join(dict.fromkeys(details))


PROACTIVE_TEST_SUGGESTIONS = {
    "account_link_all_unavailable": "按诊断步骤中的逐账号拒绝原因检查权限、暂停、免打扰、权重或连接；管理员主动测试已豁免联系额度，生产额度保持不变。",
    "proactive_recipient_required": "请在消息测试顶部选择核心管理员并保存；只有一个可用管理员时自动使用。",
    "proactive_recipient_changed": "测试收件人或管理员身份已变化，请重新读取顶部收件人并保存后再试。",
    "proactive_recipient_invalid": "请重新读取当前具有核心管理员权限的具体私聊目标。",
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
    "contact_diagnostic_source_required": "展开技术详情查看本次没有测试机会的原因。关系表达类使用已完成的私聊窗口和用途设置；事件类需要当前有效事件来源。没有候选时不会调用模型。若是首次使用：请用核心管理员账号私聊 bot，完成一次问答后回到这里重试。",
    "contact_weather_unavailable": "检查角色地点与天气观测设置，并确认 Open-Meteo 或已配置的和风凭据可访问；刷新后先在状态页确认出现当前观测。",
    "contact_news_unavailable": "检查当前资讯源的网络抓取结果与条目发布时间；真实测试不会用合成新闻替代。",
    "screen_peek_unavailable": "检查当前私聊已配置的截图设备、识屏开关与窥屏授权。",
    "screen_diagnostic_pending": "本次截图或视觉分析任务仍未结束；先核对该任务和设备记录，结果确认前不要重新运行。",
    "screen_peek_unknown": "设备截图结果未确认；核对该截图任务与设备记录，不要重放。",
    "reality_location_unavailable": "确认手机陪伴终端已连接并上报未过期的前台地点；测试只读取授权快照，不会伪造坐标。",
    "reality_location_not_authorized": "在当前角色的现实触及授权中允许读取手机摘要，并确认手机终端与核心管理员私聊已配对。",
    "proactive_diagnostic_image_unavailable": "检查私聊表情包开关、用户的表情包偏好和授权图库中的可用素材。",
    "proactive_image_route_unavailable": "检查角色的 Image 扩展、文生图模型和会话授权。",
    "proactive_image_generation_failed": "检查文生图模型配置和生图服务回执。",
    "proactive_image_result_unknown": "先核对生图服务回执和实际收件人，再处理本次测试。",
    "image_quota_closed": "检查角色的主动生图每日上限和生图每日额度。",
    "image_quota_exhausted": "今日主动生图额度已用完。",
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
    "host_account_unavailable": "在 AstrBot 的账号设置里确认该 Bot 已登录并在线，然后重新测试；错误详情里带着具体的平台与账号。",
    "host_platform_unavailable": "确认 AstrBot 里仍存在这条平台实例；改名或重建平台后，回「账号与会话」重新核对映射。",
    "host_persona_window_unbound": "打开「账号与会话」，在角色映射里给这个私聊窗口补上它现在使用的人格；或在 AstrBot 里把该会话的人格改回已映射的那个，保存后重新测试。",
    "host_persona_unmapped": "这台 Bot 还没有任何人格映射；先在「账号与会话」把角色人格绑定到当前 Bot，再重新测试。",
    "task_lane_full": "本次测试没有抢到后台任务通道，还没开始生成。稍等几十秒再测；同一通道也被自动主动消息、人格复核、记忆反思共用，bot 较忙时可把「运行环境 → 后台任务槽」调到 2 以上。",
    "task_concurrency_limited": "同一私聊已有主动消息任务在排队或执行，本次测试没有开始。等它结束后再测，不要连续点击测试。",
    "proactive_lane_busy": "后台任务通道持续被占用（通常是自动主动消息正在生成），本次测试没有开始，也没有发出任何消息。等它结束后再测，或把「运行环境 → 后台任务槽」调到 2 以上。",
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
    "contact_diagnostic_source_changed": ("trusted_candidate", "诊断来源凭据已变化"),
    "contact_diagnostic_variant_invalid": ("trusted_candidate", "测试来源选择无效"),
    "contact_conversation_changed": ("trusted_candidate", "诊断来源会话在测试期间发生变化"),
    "contact_input_changed": ("trusted_candidate", "诊断来源原文已变化"),
    "contact_input_unavailable": ("trusted_candidate", "诊断来源原文不可用"),
    "contact_context_mismatch": ("trusted_candidate", "诊断来源与原窗口不一致"),
    "contact_weather_unavailable": ("trusted_candidate", "天气观测来源不可用"),
    "contact_weather_change_unavailable": ("trusted_candidate", "天气变化来源不可用"),
    "contact_official_alert_unavailable": ("trusted_candidate", "官方预警来源不可用"),
    "contact_balance_unavailable": ("trusted_candidate", "服务余额来源不可用"),
    "reality_location_unavailable": ("mobile_location_fetch", "手机终端没有可用的真实地点快照"),
    "reality_location_not_authorized": ("mobile_location_fetch", "手机终端地点读取未获授权"),
    "reality_location_label_unavailable": ("mobile_location_fetch", "手机快照没有可分享的地点标签"),
    "reality_location_changed": ("mobile_location_fetch", "手机地点在读取过程中发生变化"),
    "proactive_diagnostic_image_unavailable": ("image_attachment", "没有可用的授权表情包"),
    "proactive_image_route_unavailable": ("image_generation", "角色的文生图路线不可用"),
    "proactive_image_generation_failed": ("image_generation", "图片生成失败"),
    "proactive_image_result_unknown": ("image_generation", "图片生成结果未确认"),
    "image_quota_closed": ("image_generation", "主动生图额度已关闭"),
    "image_quota_exhausted": ("image_generation", "今日主动生图额度已用完"),
    "contact_weather_source_changed": ("trusted_candidate", "天气观测来源已变化"),
    "contact_weather_change_source_changed": ("trusted_candidate", "天气变化来源已变化"),
    "contact_official_alert_changed": ("trusted_candidate", "官方预警来源已变化"),
    "model_provider_unavailable": ("model_route", "主动联系模型 Provider 不可用"),
    "model_generation_failed": ("model_generation", "主动联系模型生成失败"),
    "model_budget_exhausted": ("model_generation", "主动消息模型每日硬额度已用完"),
    "model_budget_soft_deferred": ("model_generation", "主动消息模型生成被每日软限额暂缓"),
    "task_lane_full": ("runtime", "本机后台任务通道已满，本次测试未开始"),
    "task_concurrency_limited": ("runtime", "同类主动任务已达并发上限，本次测试未开始"),
    "proactive_lane_busy": ("runtime", "后台任务通道持续被占用，本次测试未开始"),
    "host_account_unavailable": ("runtime", "配置的 Bot 账号当前未连接 AstrBot"),
    "host_platform_unavailable": ("runtime", "配置的平台实例不在当前 AstrBot 中"),
    "host_persona_window_unbound": ("binding", "当前窗口的人格没有绑定到这台 Bot"),
    "host_persona_unmapped": ("binding", "这台 Bot 还没有人格映射"),
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
    "contact_admission": "检查诊断来源会话是否在测试期间被新消息取代；若刚发过消息，等待该轮完成后再测。",
    "news_fetch": "检查当前配置的真实资讯源是否可访问、是否返回带日期的有效条目。",
    "weather_fetch": "检查已配置的角色所在地与真实天气接口；测试会即时获取观测。",
    "screen_capture": "检查已授权的截图设备及本次任务记录；未知结果不能重拍。",
    "screen_interpretation": "检查 screen.interpretation 的视觉模型、实际截图原件及分析任务记录。",
    "mobile_location_fetch": "检查现实触及模块、手机端配对、位置读取授权和本次地点快照时效。",
    "image_attachment": "检查私聊表情包偏好、授权图库和投递回执。",
    "image_generation": "检查角色的文生图路线、授权和生图额度。",
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
    "screen_capture", "screen_interpretation", "mobile_location_fetch", "image_attachment", "image_generation", "trusted_candidate",
    "contact_admission", "attempt_budget", "delivery_budget",
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
    "proactive_recipient_required": "binding",
    "proactive_recipient_changed": "binding",
    "proactive_recipient_invalid": "binding",
    "setup_core_window_ambiguous": "binding",
    "setup_private_window_required": "binding",
    "host_persona_binding_changed": "binding",
    "staging_not_initialized": "runtime",
    "proactive_diagnostic_unavailable": "runtime",
    "contact_permission_required": "settings",
    "contact_purpose_paused": "purpose",
    "contact_quiet_hours": "purpose",
    "contact_diagnostic_source_required": "trusted_candidate",
    "contact_diagnostic_source_changed": "trusted_candidate",
    "contact_diagnostic_variant_invalid": "trusted_candidate",
    "contact_conversation_changed": "trusted_candidate",
    "contact_input_changed": "trusted_candidate",
    "contact_input_unavailable": "trusted_candidate",
    "contact_context_mismatch": "trusted_candidate",
    "reality_location_unavailable": "mobile_location_fetch",
    "reality_location_not_authorized": "mobile_location_fetch",
    "reality_location_label_unavailable": "mobile_location_fetch",
    "reality_location_changed": "mobile_location_fetch",
    "model_task_unregistered": "model_route",
    "model_provider_unavailable": "model_route",
    "model_budget_exhausted": "model_generation",
    "model_budget_soft_deferred": "model_generation",
    "task_lane_full": "runtime",
    "task_concurrency_limited": "runtime",
    "proactive_lane_busy": "runtime",
    "host_account_unavailable": "runtime",
    "host_platform_unavailable": "runtime",
    "host_persona_window_unbound": "binding",
    "host_persona_unmapped": "binding",
    "delivery_result_unknown": "delivery",
    "delivery_failed": "delivery",
}


# A model choice is saved even when the shared installation cannot be created
# yet: these codes mean "this Bot is not connected right now", which the host's
# own start loop keeps retrying, so the setup step reports a deferral instead of
# a failure. Every other code is a configuration problem the operator must fix.
INIT_DEFERRED_CODES = frozenset(("host_account_unavailable", "host_platform_unavailable"))


def deferred_initialization(failure):
    """Describe a saved model whose shared installation waits for its Bot.

    Choosing a provider needs no live account; creating the installation reads
    the real windows and does. ``None`` means "not a deferral", so the caller
    keeps its normal failure path. The returned payload tells the page that the
    provider is saved and only the account connection is missing.
    """
    code = getattr(failure, "code", None)
    if code not in INIT_DEFERRED_CODES:
        return None
    return {"prepared": False, "init_deferred": True, "init_code": code,
            "init_reason": getattr(failure, "reason", "") or ""}


def _fail_stage(stages, stage):
    """Mark one stage failed without listing the same stage twice.

    A capacity refusal belongs to a stage the flow already recorded as passed
    (``runtime``). Appending it again would show "运行环境 通过" and
    "运行环境 失败" on the same card, so the existing row is upgraded instead.
    """
    for item in stages:
        if item["stage"] == stage:
            item["state"] = "failed"
            return item
    item = {"stage": stage, "state": "failed"}
    stages.append(item)
    return item


def _settle_failed_stages(stages, stage):
    """Mark one stage failed and close every later running/pending placeholder.

    The setup runner pre-creates the generation/review/delivery/receipt rows with
    ``running``/``pending`` before calling the producer.  If admission fails before
    generation starts, leaving those rows untouched makes the page look stuck on
    "模型生成中" even though the terminal error is already known.  A failed test
    has no future work in this attempt, so every unfinished placeholder becomes
    ``skipped``; the failed stage is kept exactly once.
    """
    existing = next((item for item in stages if item["stage"] == stage), None)
    # ``unknown`` is a real terminal result for a side-effecting step (for
    # example a screenshot was captured but interpretation is uncertain).  Do
    # not turn it into a hard failure merely because the overall test failed.
    failed = existing if existing is not None and existing.get("state") == "unknown" else _fail_stage(stages, stage)
    for item in stages:
        if item is failed:
            continue
        if item["state"] in ("running", "pending"):
            item["state"] = "skipped"
    return failed


class SetupWebAPI:
    def __init__(self, controller):
        self.controller = controller
        self.progress = SetupStateStore(controller.data_root)
        from .diagnostic_recipient import DiagnosticRecipientStore
        self.diagnostic_recipient = DiagnosticRecipientStore(controller.data_root)
        self._test_lock = asyncio.Lock()
        self._test_task = None

    def cancel_background_tasks(self) -> None:
        task = self._test_task
        if task is not None and not task.done():
            task.cancel()

    async def aclose(self) -> None:
        task = self._test_task
        self._test_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as error:
                logger.debug("[CompanionV2] 设置测试任务停机清理异常 error_type=%s",
                             type(error).__name__)

    def routes(self):
        return [
            (PREFIX + "/status", self.status, ["GET"], "Read persistent Companion V2 setup progress"),
            (PREFIX + "/health", self.health, ["GET"], "Run a zero-side-effect onboarding readiness health check"),
            (PREFIX + "/upgrade", self.prepare_upgrade, ["POST"], "Confirm existing legacy account and persona mappings"),
            (PREFIX + "/targets", self.api_targets, ["GET"], "Read connected OneBot accounts and native personas"),
            (PREFIX + "/binding/confirm", self.confirm_binding, ["POST"], "Confirm the Companion V2 core administrator window"),
            (PREFIX + "/binding/select", self.select_binding, ["POST"], "Directly select and confirm a discovered private window as the core administrator"),
            (PREFIX + "/binding/reset", self.reset_binding, ["POST"], "Release the confirmed core administrator window so it can be bound again"),
            (PREFIX + "/model", self.model, ["POST"], "Choose the initial Companion V2 text provider"),
            (PREFIX + "/model/probe", self.probe_model, ["POST"], "Run a lightweight live connectivity check on one AstrBot text provider"),
            (PREFIX + "/model-tasks", self.model_tasks, ["GET"], "Read per-task model routes for the setup role"),
            (PREFIX + "/model-tasks/configure", self.configure_model_task, ["POST"], "Configure one setup-role model task"),
            (PREFIX + "/profile", self.profile, ["GET"], "Read setup-role profile and relationship cards"),
            (PREFIX + "/profile-draft", self.profile_draft, ["POST"], "Draft role canon or relationship card from the current persona"),
            (PREFIX + "/profile/save", self.save_profile, ["POST"], "Save reviewed setup profile fields"),
            (PREFIX + "/host/start", self.reconnect_host, ["POST"], "Reconnect a completed Companion V2 setup"),
            (PREFIX + "/prepare", self.prepare, ["POST"], "Prepare the confirmed Companion V2 profile"),
            (PREFIX + "/complete", self.complete, ["POST"], "Record a completed Companion V2 setup section"),
            (PREFIX + "/finish", self.finish_setup, ["POST"], "Complete first-run onboarding after verifying core prerequisites"),
            (PREFIX + "/proactive-test", self.proactive_test, ["POST"], "Run a one-time proactive contact through the V2 contact and delivery pipeline"),
            (PREFIX + "/proactive-test/status", self.proactive_test_status, ["GET"], "Read the existing proactive test receipt without host discovery"),
            (PREFIX + "/proactive-recipients", self.proactive_recipients, ["GET"], "Read current core administrator test recipients"),
            (PREFIX + "/proactive-recipient", self.save_proactive_recipient, ["POST"], "Save the reviewed diagnostic recipient"),
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
        native_types = getattr(self.controller, "native_types", None)
        expected_type = text_provider_predicate(native_types)
        video_type = getattr(native_types, "video_provider_type", None)
        return text_provider_catalog(context, expected_type, video_type=video_type) if context is not None else []

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
            providers.append({"id": selection.provider_id, "name": selection.name or selection.provider_id,
                              "model": "", "capabilities": ["text"]})
        return providers

    def _effective_chat_model(self) -> dict | None:
        context = getattr(self.controller, "context", None)
        native_types = getattr(self.controller, "native_types", None)
        expected_type = text_provider_predicate(native_types)
        video_type = getattr(native_types, "video_provider_type", None)
        return active_chat_provider_info(context, expected_type, video_type=video_type)

    def _memory_companion_document(self, username: str = "") -> dict:
        session = getattr(self.controller, "session", None) or getattr(self.controller, "last_session", None)
        ownership = None
        if session is not None and getattr(session, "application", None) is not None:
            try:
                ownership = session.application.memory.context.ownership()
            except Exception:
                ownership = None
        summary = read_memory_companion_status(getattr(self.controller, "context", None), ownership=ownership)
        dismissed = False
        if username:
            hints = read_user_hint_preferences(getattr(self.controller, "data_root", None), username)
            dismissed = bool(hints.get("memoryCompanion", False))
        return {**summary, "hint_dismissed": dismissed}

    def _configured_fallback_provider_id(self) -> str:
        config = getattr(self.controller, "config", {})
        section = config.get("model", {}) if isinstance(config, dict) else {}
        return str(section.get("fallback_provider_id", "")) if isinstance(section, dict) else ""

    def _status_document(self, *, username: str = "") -> dict:
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
        catalog = self._model_catalog()
        active_provider_id = selection_id or self._configured_provider_id()
        selected_entry = next((item for item in catalog if item["id"] == active_provider_id), None)
        vision_providers = [item for item in catalog if "image" in (item.get("capabilities") or ())]
        primary_supports_vision = bool(selected_entry and "image" in (selected_entry.get("capabilities") or ()))
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
                "model": {"provider_id": active_provider_id,
                          "fallback_provider_id": self._configured_fallback_provider_id(),
                          "provider_source": (selection.source if selection_id and selection is not None
                                              else "unavailable"),
                          "providers": catalog,
                          "effective_chat_model": self._effective_chat_model(),
                          "primary_supports_vision": primary_supports_vision,
                          "vision_providers": vision_providers},
                "memory_companion": self._memory_companion_document(username=username),
                "proactive_test": state["proactive_test"], "ready": state["ready"],
                "finished_at": state.get("finished_at"),
                "finished_by": state.get("finished_by"),
                "settings_error": getattr(getattr(self.controller, "settings_error", None), "code", None),
                "host": status}

    @staticmethod
    def _call_progress_sync(fn, *args, **kwargs):
        # Retry transient Windows file-sharing errors when concurrent readers
        # inspect setup_state.json during atomic replacement.
        for attempt in range(6):
            try:
                return fn(*args, **kwargs)
            except PermissionError:
                if attempt == 5:
                    raise
                _time.sleep(0.01 * (attempt + 1))

    async def _test_progress(self, username: str) -> dict:
        saved = await asyncio.to_thread(self._call_progress_sync, self.progress.read)
        receipt = saved["proactive_test"]
        if (receipt and receipt["state"] == "started"
                and (self._test_task is None or self._test_task.done())):
            async with self._test_lock:
                saved = await asyncio.to_thread(self._call_progress_sync, self.progress.read)
                receipt = saved["proactive_test"]
                if (receipt and receipt["state"] == "started"
                        and (self._test_task is None or self._test_task.done())):
                    await self._restore_test_contact_permission(username, receipt["attempt_id"])
                    saved = await asyncio.to_thread(
                        self._call_progress_sync,
                        self.progress.finish_proactive_test,
                        outcome="unknown",
                        finished_at=datetime.now(timezone.utc).isoformat(),
                        error_code="setup_test_interrupted",
                    )
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
            username = self._admin()
            await self._test_progress(username)
            document = await asyncio.to_thread(self._status_document, username=username)
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
            accounts = await asyncio.to_thread(self.controller.session_topology.accounts)
            persona_names = await available_personas(context)
            from .multi_settings import MultiHostSettings

            def read_mapping_rows():
                try:
                    # The runtime may already share one persona role across Bot
                    # accounts; use the same normalized mapping in the setup picker.
                    parsed = getattr(self.controller, "settings", None)
                    if parsed is None or not hasattr(parsed, "personas"):
                        parsed = MultiHostSettings.parse(self.controller.config)
                    return [{"bot_id": item.bot_id, "persona_id": item.persona_id,
                             "role_id": item.role_id} for item in parsed.personas]
                except (HostControlError, ValueError):
                    return self.controller.config.get("multi_host", {}).get("personas", [])

            mapping_rows = await asyncio.to_thread(read_mapping_rows)
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
            directory = await self.controller.session_topology.inventory_report(
                settings=inventory_settings, include_unmanaged=True)
            targets = directory["targets"]
            # The setup page runs before the first runtime profile exists, so
            # page/targets cannot supply migration routes yet. The setup catalog
            # must still describe which native roles can receive an old install.
            from .legacy_api import PREFIX as LEGACY_PREFIX
            from .role_paths import role_legacy_paths
            inventory_roles = {}
            inventory = getattr(getattr(self.controller, "web_api", None), "target_inventory", None)
            if callable(inventory):
                def read_inventory_roles():
                    try:
                        return {
                            (target.get("target_id"), role.get("role_id")): role
                            for target in inventory() for role in target.get("roles", [])
                            if target.get("target_id") and role.get("role_id")
                        }
                    except Exception:
                        return {}
                inventory_roles = await asyncio.to_thread(read_inventory_roles)
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

            def confirm_sync():
                result = confirm()
                if result.get("confirmed") is not True:
                    raise TaskError(result.get("reason", "setup_binding_missing"),
                                    result.get("detail", "send 我会永远陪着你 from the core administrator private window first"))
                return self._binding()

            binding = await asyncio.to_thread(confirm_sync)
            return self._response(Outcome.success({"binding": binding, "confirmed": True}))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError):
            return self._error("setup_binding_confirmation_failed", "the pending private window could not be confirmed")

    async def select_binding(self):
        """Directly bind a discovered private window as the core administrator."""
        try:
            username = self._admin()
            data = await self._body({"platform_id", "account_id", "participant_id"},
                                    optional={"persona_id"})
            selector = getattr(self.controller, "select_core_binding", None)
            if not callable(selector):
                raise TaskError("setup_binding_unavailable", "the active host cannot select a private window directly")

            def select_sync():
                result = selector(
                    platform_id=data["platform_id"],
                    account_id=data["account_id"],
                    participant_id=data["participant_id"],
                    persona_id=data.get("persona_id", ""),
                )
                return result.get("binding") or self._binding(), self._status_document(username=username)

            binding, status = await asyncio.to_thread(select_sync)
            return self._response(Outcome.success({"binding": binding, "confirmed": True, "status": status}))
        except (TaskError, HostControlError) as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_binding_confirmation_failed", "the selected private window could not be bound")

    async def reset_binding(self):
        """Release the confirmed core administrator window so it can be bound again.

        ``receive_binding`` tells the operator to "reset the first-run setup" in
        this guide before switching windows, but until now no route implemented
        that: an already-confirmed window could only be replaced by editing the
        plugin data directory by hand. Worse, the refusal named a control that
        did not exist, so the command, the page and the docs each blamed another.

        Only the *confirmation* is withdrawn. The configured mapping, the
        storage installation and every stored memory stay exactly as they are;
        after this call ``binding_state`` reports ``pending`` again and the
        normal confirm flow re-pins the *currently* reviewed mapping. That is
        what makes a renamed persona or a moved window fixable from the page
        instead of by editing ``binding.json``.
        """
        try:
            self._admin()
            await self._body({"confirm"})
            clear = getattr(self.controller, "reset_binding", None)
            if not callable(clear):
                raise TaskError("setup_binding_unavailable",
                                "the active host cannot reset a private window")

            def reset_sync():
                result = clear()
                if result.get("reset") is not True:
                    raise TaskError(result.get("reason", "setup_binding_missing"),
                                    result.get("detail", "there is no confirmed core administrator window to reset"))
                return self._binding()

            binding = await asyncio.to_thread(reset_sync)
            return self._response(Outcome.success({"binding": binding, "reset": True}))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError):
            return self._error("setup_binding_reset_failed",
                               "the confirmed private window could not be reset")

    @staticmethod
    def _probe_error_hint(error: Exception) -> tuple[str, str]:
        if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
            return "timeout", "请求超时（15 秒内未返回）。请检查代理、接口地址或模型服务状态。"
        if isinstance(error, TaskError) and error.code == "host_provider_empty_reply":
            return "failed", "模型未返回可显示的文本。请检查模型类型和输出设置。"
        status_code = extract_error_status_code(error)
        if status_code in (401, 403):
            return "failed", f"鉴权失败（HTTP {status_code}）。请到 AstrBot「模型提供商」核对 API Key 与权限。"
        if status_code == 404:
            return "failed", "接口或模型名不存在（HTTP 404）。请核对 Base URL 与模型名称是否填写正确。"
        if status_code == 429:
            return "failed", "触发频率限制或额度不足（HTTP 429）。请检查账户余额或限流设置。"
        if isinstance(status_code, int) and status_code >= 500:
            return "failed", f"模型服务端异常（HTTP {status_code}）。可稍后重试或配置一个全局备用模型。"
        name = type(error).__name__
        detail = str(error).lower()
        if "connect" in detail or "connection" in name.lower() or "dns" in detail or "ssl" in detail:
            return "failed", "无法连接到模型服务地址。请检查网络、代理设置或接口 URL。"
        return "failed", f"调用未成功（{name}）。请核对 AstrBot 中该 Provider 的配置与日志。"

    @staticmethod
    def _probe_output_key(model: str, protocol: str) -> str:
        if "responses" in protocol:
            return "max_output_tokens"
        lowered = str(model or "").casefold()
        if lowered.startswith(("gpt-6", "gpt-5", "o1", "o3", "o4")):
            return "max_completion_tokens"
        return "max_tokens"

    async def _run_provider_probe(self, provider) -> str:
        native = copy(provider)
        config = getattr(provider, "provider_config", None)
        config_dict = deepcopy(config) if isinstance(config, dict) else {}
        native.provider_config = config_dict
        model = provider_model_name(provider)
        protocol = str(config_dict.get("type") or "").lower()
        client = getattr(provider, "client", None)
        if looks_like_text_provider(provider):
            with_options = getattr(client, "with_options", None)
            if callable(with_options):
                native.client = with_options(max_retries=0)
            if "responses" in protocol and callable(getattr(native, "_prepare_chat_payload", None)):
                payload, _ = await native._prepare_chat_payload(
                    prompt="Reply OK.", contexts=[], model=model)
                payload["max_output_tokens"] = PROBE_OUTPUT_TOKENS
                result = await native._query(payload, None, request_max_retries=1)
            else:
                output_key = self._probe_output_key(model, protocol)
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply OK."}],
                    output_key: PROBE_OUTPUT_TOKENS,
                }
                result = await native._query(payload, None, request_max_retries=1)
        elif callable(getattr(native, "text_chat", None)):
            try:
                result = await native.text_chat(prompt="Reply OK.", request_max_retries=1)
            except TypeError:
                result = await native.text_chat(prompt="Reply OK.")
        else:
            raise TaskError("host_provider_unavailable", "provider does not expose a supported text transport")
        text = getattr(result, "completion_text", None)
        if not isinstance(text, str) or not text.strip():
            raw = getattr(result, "raw_completion", None)
            choices = getattr(raw, "choices", None)
            if isinstance(choices, (list, tuple)) and choices:
                msg = getattr(choices[0], "message", None)
                text = getattr(msg, "content", None)
        if not isinstance(text, str) or not text.strip():
            raise TaskError("host_provider_empty_reply", "provider did not return text")
        return redact_text(text.strip())[:80]

    async def probe_model(self):
        """Lightweight live connectivity test for one selected AstrBot provider."""
        try:
            self._admin()
            data = await self._body({"provider_id"})
            provider_id = data["provider_id"]
            if type(provider_id) is not str or not provider_id.strip() or len(provider_id) > 512:
                raise ValueError("invalid provider")
            provider_id = provider_id.strip()
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_model_invalid", "choose a loaded AstrBot text provider to test")

        try:
            context = getattr(self.controller, "context", None)
            native_types = getattr(self.controller, "native_types", None)
            expected_type = text_provider_predicate(native_types)
            video_type = getattr(native_types, "video_provider_type", None)
            available = {item["id"] for item in self._model_providers()}
            provider = context.get_provider_by_id(provider_id) if context is not None else None
            if (provider is None or provider_id not in available or expected_type is None
                    or not supported_text_provider(provider, expected_type)):
                raise TaskError("host_provider_unavailable", "choose a loaded AstrBot text provider supported by V2")
            model = provider_model_name(provider)
            caps = provider_capabilities(provider, video_type=video_type)
            started = _time.monotonic()
            try:
                preview = await asyncio.wait_for(
                    self._run_provider_probe(provider),
                    timeout=MODEL_PROBE_TIMEOUT_SECONDS,
                )
                latency_ms = max(1, int((_time.monotonic() - started) * 1000))
                return self._response(Outcome.success({
                    "provider_id": provider_id,
                    "ok": True,
                    "status": "ok",
                    "latency_ms": latency_ms,
                    "model": model,
                    "capabilities": caps,
                    "reply_preview": preview,
                    "error": "",
                    "hint": "",
                }))
            except Exception as error:
                latency_ms = max(1, int((_time.monotonic() - started) * 1000))
                status, hint = self._probe_error_hint(error)
                return self._response(Outcome.success({
                    "provider_id": provider_id,
                    "ok": False,
                    "status": status,
                    "latency_ms": latency_ms,
                    "model": model,
                    "capabilities": caps,
                    "reply_preview": "",
                    "error": type(error).__name__,
                    "hint": hint,
                }))
        except TaskError as error:
            return self._error(error.code, str(error))

    async def _apply_vision_provider(self, username: str, vision_provider_id: str) -> list[str]:
        """Assign a chosen vision-capable provider to registered vision tasks on role layer."""
        if not vision_provider_id:
            return []
        try:
            session, context, _instruction = await self._setup_context()
        except TaskError:
            return []

        def apply_sync():
            from companion_v2.model_gateway.management import ModelManagement
            manager = ModelManagement(session.application, context)
            operator = self._operator(self.controller, username)
            status = require(manager.status(operator, layer="role"))
            providers_by_id = {item["id"]: item for item in status.get("providers", [])}
            chosen = providers_by_id.get(vision_provider_id)
            if chosen is None or "image" not in (chosen.get("capabilities") or ()):
                return []
            updated = []
            for task in status.get("tasks", []):
                kind = task.get("kind")
                if kind not in VISION_SETUP_TASKS:
                    continue
                required = task.get("capabilities") or ()
                if not all(cap in (chosen.get("capabilities") or ()) for cap in required):
                    continue
                current_provider = (task.get("fields") or {}).get("provider", {}).get("value", "")
                current_fallback = (task.get("fields") or {}).get("fallback", {}).get("value", "")
                if current_provider == vision_provider_id:
                    updated.append(kind)
                    continue
                fallback_info = providers_by_id.get(current_fallback) if current_fallback else None
                fallback_valid = (
                    bool(current_fallback)
                    and current_fallback != vision_provider_id
                    and fallback_info is not None
                    and all(cap in (fallback_info.get("capabilities") or ()) for cap in required)
                )
                fallback = current_fallback if fallback_valid else ""
                try:
                    require(manager.configure(operator, {
                        "kind": kind,
                        "layer": "role",
                        "operation_key": f"setup-vision-{kind}-{uuid4().hex[:12]}",
                        "edit_token": task["edit_token"],
                        "changes": {"provider": vision_provider_id, "fallback": fallback},
                        "reset": [],
                    }))
                    updated.append(kind)
                except TaskError as task_err:
                    logger.debug("[CompanionV2] 跳过不兼容的视觉任务路由 kind=%s code=%s",
                                 kind, task_err.code)
            return updated

        try:
            return await asyncio.to_thread(apply_sync)
        except Exception as error:
            logger.warning("[CompanionV2] 新手引导应用视觉模型任务路由失败 error_type=%s",
                           type(error).__name__)
            return []

    async def model(self):
        try:
            username = self._admin()
            raw = await request.body()
            if len(raw) > 4096:
                raise ValueError("request is too large")
            data = json.loads(raw.decode("utf-8"))
            has_fallback = isinstance(data, dict) and "fallback_provider_id" in data
            has_vision = isinstance(data, dict) and "vision_provider_id" in data
            complete_step = bool(data.get("complete_step", False)) if isinstance(data, dict) else False
            if (type(data) is not dict or "provider_id" not in data
                    or set(data) - {"provider_id", "fallback_provider_id", "vision_provider_id", "complete_step"}
                    or ("complete_step" in data and type(data["complete_step"]) is not bool)):
                raise ValueError("invalid model fields")
            provider_id = data["provider_id"]
            if type(provider_id) is not str or not provider_id.strip() or len(provider_id) > 512:
                raise ValueError("invalid provider")
            provider_id = provider_id.strip()
            fallback_provider_id = None
            if has_fallback:
                raw_fallback = data["fallback_provider_id"]
                if type(raw_fallback) is not str or len(raw_fallback.strip()) > 512:
                    raise ValueError("invalid fallback provider")
                fallback_provider_id = raw_fallback.strip()
            vision_provider_id = ""
            if has_vision:
                raw_vision = data["vision_provider_id"]
                if type(raw_vision) is not str or len(raw_vision.strip()) > 512:
                    raise ValueError("invalid vision provider")
                vision_provider_id = raw_vision.strip()
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_model_invalid", "choose a loaded AstrBot text provider and retry")

        stage_box = ["provider_validation"]
        try:
            def save_model_sync():
                status = self._status_document(username=username)
                if not status["binding_confirmed"]:
                    raise TaskError("setup_step_order_invalid", "confirm the core administrator window before choosing a model")
                if fallback_provider_id == provider_id:
                    raise TaskError("model_fallback_duplicate",
                                    "fallback model provider must differ from the primary provider")
                available = {item["id"] for item in status["model"]["providers"]}
                provider = self.controller.context.get_provider_by_id(provider_id)
                expected_type = text_provider_predicate(getattr(self.controller, "native_types", None))
                if (provider_id not in available or expected_type is None
                        or not supported_text_provider(provider, expected_type)):
                    raise TaskError("host_provider_unavailable", "choose a loaded AstrBot text provider supported by V2")
                if fallback_provider_id:
                    fallback_provider = self.controller.context.get_provider_by_id(fallback_provider_id)
                    if (fallback_provider_id not in available
                            or not supported_text_provider(fallback_provider, expected_type)):
                        raise TaskError("host_provider_unavailable",
                                        "choose a loaded AstrBot text provider for the fallback model")
                if vision_provider_id:
                    vision_provider = self.controller.context.get_provider_by_id(vision_provider_id)
                    if (vision_provider_id not in available
                            or not supported_text_provider(vision_provider, expected_type)):
                        raise TaskError("host_provider_unavailable",
                                        "choose a loaded AstrBot vision-capable provider")
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
                if fallback_provider_id is not None:
                    model_settings["fallback_provider_id"] = fallback_provider_id
                elif model_settings.get("fallback_provider_id") == provider_id:
                    model_settings["fallback_provider_id"] = ""
                stage_box[0] = "config_persistence"
                save(candidate)
                stage_box[0] = "settings_refresh"
                refresh = getattr(self.controller, "refresh_settings", None)
                if callable(refresh):
                    refresh()
                settings_error = getattr(self.controller, "settings_error", None)
                if settings_error is not None:
                    raise TaskError(settings_error.code, str(settings_error))

            await asyncio.to_thread(save_model_sync)
            stage_box[0] = "profile_initialization"
            initialized = await self.controller.initialize_profile()
            deferred = deferred_initialization(initialized.failure)
            if deferred is not None:
                # Choosing a model does not need a connected Bot; creating the
                # shared installation does. The provider is already saved, so
                # report the deferral instead of claiming the step failed, and
                # let the host create the installation once the account is back
                # (its own start loop retries, and this button can be pressed
                # again at any time).
                logger.info("[CompanionV2] 模型已保存，宿主初始化等待账号连接 code=%s",
                            deferred["init_code"])
                return self._response(Outcome.success({
                    "provider_id": provider_id, **deferred,
                    "status": await asyncio.to_thread(self._status_document, username=username)}))
            if initialized.failure is not None:
                if initialized.failure.code != "host_must_stop":
                    raise TaskError(initialized.failure.code, initialized.failure.reason)
                # The setup guide can be reopened while the shared host is
                # already running. The provider is saved and the current host
                # keeps serving; it will use the new selection after reload.
                vision_applied = await self._apply_vision_provider(username, vision_provider_id)
                if complete_step:
                    await asyncio.to_thread(self.progress.complete, "model", skipped=False)
                return self._response(Outcome.success({"provider_id": provider_id,
                    "prepared": True, "restart_required": True,
                    "vision_applied": vision_applied,
                    "status": await asyncio.to_thread(self._status_document, username=username)}))
            vision_applied = await self._apply_vision_provider(username, vision_provider_id)
            if complete_step:
                await asyncio.to_thread(self.progress.complete, "model", skipped=False)
            return self._response(Outcome.success({"provider_id": provider_id,
                "prepared": True, "restart_required": False,
                "vision_applied": vision_applied,
                "status": await asyncio.to_thread(self._status_document, username=username)}))
        except TaskError as error:
            return self._error(error.code, str(error))
        except Exception as error:
            logger.warning("[CompanionV2] Initial model setup failed stage=%s error_type=%s",
                           stage_box[0], type(error).__name__)
            return self._error("setup_model_apply_failed",
                               "the selected provider was accepted, but setup could not save or initialize it")

    async def reconnect_host(self):
        try:
            self._admin()
            await self._body(set())

            def check_reconnect_sync():
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

            await asyncio.to_thread(check_reconnect_sync)
            result = await self.controller.start()
            if result.failure is not None:
                raise TaskError(result.failure.code, result.failure.reason)
            return self._response(Outcome.success({"started": True,
                "status": await asyncio.to_thread(self._status_document)}))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_reconnect_failed", "the completed V2 host could not reconnect")

    async def prepare(self):
        try:
            self._admin()
            await self._body(set())

            def check_prepare_sync():
                refresh = getattr(self.controller, "refresh_settings", None)
                if callable(refresh):
                    refresh()
                binding = self._binding()
                if not binding.get("bound") or binding.get("pending"):
                    raise TaskError("setup_binding_confirmation_required", "confirm the core administrator private window first")
                if getattr(self.controller, "settings_error", None) is not None:
                    error = self.controller.settings_error
                    raise TaskError(error.code, str(error))

            await asyncio.to_thread(check_prepare_sync)
            result = await self.controller.initialize_profile()
            if result.failure is not None:
                raise TaskError(result.failure.code, result.failure.reason)
            return self._response(Outcome.success({"prepared": True, "profile": result.unwrap(),
                                                  "status": await asyncio.to_thread(self._status_document)}))
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

            def validate_complete_sync():
                state = self.progress.read()
                binding = self._binding()
                if not binding.get("bound") or binding.get("pending"):
                    raise TaskError("setup_binding_confirmation_required", "confirm the core administrator private window first")
                prepared = bool(getattr(self.controller, "runtime", None)
                                and (getattr(self.controller, "session", None)
                                     or getattr(self.controller, "last_session", None)))
                need_init = False
                if step != "model" and not prepared:
                    refresh = getattr(self.controller, "refresh_settings", None)
                    if callable(refresh):
                        refresh()
                    settings_error = getattr(self.controller, "settings_error", None)
                    if settings_error is not None:
                        raise TaskError(settings_error.code, str(settings_error))
                    need_init = True
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
                        need_init = True
                return need_init

            need_init = await asyncio.to_thread(validate_complete_sync)
            if need_init:
                result = await self.controller.initialize_profile()
                if result.failure is not None:
                    raise TaskError(result.failure.code, result.failure.reason)
            state = await asyncio.to_thread(self.progress.complete, step, skipped=skipped)
            return self._response(Outcome.success({"completed": state["completed"],
                "skipped": state["skipped"], "step": step, "skipped_this_step": skipped,
                "prepared": bool(getattr(self.controller, "runtime", None)
                                  and (getattr(self.controller, "session", None)
                                       or getattr(self.controller, "last_session", None)))}))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_request_invalid", "finish or skip the current setup section")

    def _readonly_health_context(self, session, binding: dict):
        """Resolve the confirmed private TaskContext strictly read-only (zero DB writes)."""
        if session is None:
            return None
        if not getattr(self.controller, "_multi_host", False):
            profile = getattr(session, "profile", None)
            return getattr(profile, "target", None)
        authority = getattr(self.controller, "authority", None)
        settings = getattr(self.controller, "settings", None)
        runtime = getattr(self.controller, "runtime", None)
        if authority is None or settings is None or runtime is None:
            return None
        try:
            from companion_v2.execution.models import TaskContext
            from companion_v2.foundation.refs import AudienceRef, ScopeRef

            parked = self.controller._parked_binding()
            target = self.controller._core_target(parked) if parked is not None else None
            if target is None or target.participant_id == "*":
                return None
            role_candidates = []
            bound_role = binding.get("role_id")
            if isinstance(bound_role, str) and bound_role.strip():
                role_candidates.append(settings.canonical_role_id(bound_role.strip()))
            for persona in getattr(settings, "personas", ()) or ():
                if persona.bot_id == target.bot_id:
                    canonical = settings.canonical_role_id(persona.role_id)
                    if canonical not in role_candidates:
                        role_candidates.append(canonical)
            stored = None
            with runtime.read_snapshot():
                for role_id in role_candidates:
                    candidate = authority.read(authority.context_key(target, role_id))
                    if isinstance(candidate, dict):
                        stored = candidate
                        break
                if stored is None:
                    active = authority.read("host.multi.active:" + target.target_id)
                    if isinstance(active, dict):
                        stored = active
                if stored is None:
                    prefix = "host.multi.context:"
                    for row in runtime.read_all(
                        "SELECT value FROM runtime_metadata WHERE substr(key, 1, ?) = ?",
                        (len(prefix), prefix),
                    ):
                        try:
                            candidate = json.loads(row["value"])
                            scope_probe = ScopeRef.restore(candidate)
                        except (TypeError, ValueError, KeyError):
                            continue
                        if (scope_probe.kind is ScopeKind.PRIVATE
                                and scope_probe.platform == target.platform_id
                                and scope_probe.account_id == target.account_id
                                and scope_probe.bot_id == target.bot_id
                                and scope_probe.native_scope_id == target.participant_id):
                            stored = candidate
                            break
                if not isinstance(stored, dict):
                    return None
                scope = ScopeRef.restore(stored)
                actor = authority.actor(target, scope.native_scope_id)
                resolution = authority.personas.resolve(scope, observed_at=self.controller.now())
                if resolution.failure is not None:
                    return None
                return TaskContext(actor, resolution.unwrap().binding, AudienceRef(scope, (actor.subject,)))
        except Exception:
            return None

    def _health_document(self, *, username: str = "") -> dict:
        status = self._status_document(username=username)
        binding = status.get("binding") or {}
        binding_confirmed = bool(status.get("binding_confirmed"))
        prepared = bool(status.get("prepared"))
        host = status.get("host") or {}
        accepting = bool(host.get("accepting"))
        model = status.get("model") or {}
        providers = model.get("providers") or []
        provider_id = model.get("provider_id") or ""
        provider_loaded = bool(provider_id and any(item.get("id") == provider_id for item in providers))
        primary_vision = bool(model.get("primary_supports_vision"))
        vision_providers = model.get("vision_providers") or []
        memory_companion = status.get("memory_companion") or {}

        session = getattr(self.controller, "session", None) or getattr(self.controller, "last_session", None)
        ctx = (self._readonly_health_context(session, binding)
               if session is not None and binding_confirmed and username else None)

        accounts = []
        topology = getattr(self.controller, "session_topology", None)
        if topology is not None and callable(getattr(topology, "accounts", None)):
            try:
                accounts = list(topology.accounts() or [])
            except Exception:
                accounts = []
        configured_personas = []
        settings = getattr(self.controller, "settings", None)
        if settings is not None and hasattr(settings, "personas"):
            configured_personas = list(getattr(settings, "personas", ()) or ())
        elif isinstance(getattr(self.controller, "config", None), dict):
            configured_personas = list(
                (self.controller.config.get("multi_host") or {}).get("personas") or []
            )

        if accounts and (configured_personas or binding_confirmed):
            account_check = {
                "id": "account", "label": "机器人与人格映射", "status": "ok",
                "summary": f"已连接 {len(accounts)} 个机器人账号"
                           + (f"，已映射 {len(configured_personas)} 个人格" if configured_personas else ""),
                "fix_step": "binding",
            }
        elif accounts:
            account_check = {
                "id": "account", "label": "机器人与人格映射", "status": "warn",
                "summary": f"已发现 {len(accounts)} 个机器人账号，尚未完成人格映射",
                "fix_step": "binding",
            }
        else:
            account_check = {
                "id": "account", "label": "机器人与人格映射", "status": "error",
                "summary": "暂未检测到在线的机器人账号，请先在 AstrBot 中连接消息平台",
                "fix_step": "binding",
            }

        binding_check = {
            "id": "binding", "label": "核心管理员私聊",
            "status": "ok" if binding_confirmed else "error",
            "summary": (f"已绑定私聊窗口（{binding.get('display_name') or binding.get('participant_id') or '已确认'}）"
                        if binding_confirmed else "尚未确认核心管理员私聊窗口"),
            "fix_step": "binding",
        }

        vision_assigned_id = ""
        if provider_loaded and not primary_vision and ctx is not None and session is not None and username:
            try:
                from companion_v2.model_gateway.management import ModelManagement
                operator = self._operator(self.controller, username)
                mstatus = require(ModelManagement(session.application, ctx).status(operator, layer="role"))
                cap_map = {item["id"]: set(item.get("capabilities") or ()) for item in mstatus.get("providers", [])}
                for task in mstatus.get("tasks", []):
                    if task.get("kind") not in VISION_SETUP_TASKS:
                        continue
                    pid = (task.get("fields") or {}).get("provider", {}).get("value", "")
                    if pid and "image" in cap_map.get(pid, set()):
                        vision_assigned_id = pid
                        break
            except Exception:
                vision_assigned_id = ""

        if provider_loaded and primary_vision:
            model_check = {
                "id": "model", "label": "插件通用与识图模型", "status": "ok",
                "summary": f"已配置 {provider_id}（支持文本与识图）",
                "fix_step": "model",
            }
        elif provider_loaded and vision_assigned_id:
            model_check = {
                "id": "model", "label": "插件通用与识图模型", "status": "ok",
                "summary": f"通用模型 {provider_id} 已就绪，图片理解由 {vision_assigned_id} 接管",
                "fix_step": "model",
            }
        elif provider_loaded:
            model_check = {
                "id": "model", "label": "插件通用与识图模型", "status": "warn",
                "summary": (f"通用模型 {provider_id} 已就绪，但当前不支持图片理解"
                            + (f"（可在第 2 步指定 {len(vision_providers)} 个可用视觉模型之一）"
                               if vision_providers else "（如暂不用看图/识屏可忽略）")),
                "fix_step": "model",
            }
        else:
            model_check = {
                "id": "model", "label": "插件通用与识图模型", "status": "error",
                "summary": "尚未选定可用的插件通用模型",
                "fix_step": "model",
            }

        if prepared and accepting:
            runtime_check = {
                "id": "runtime", "label": "陪伴运行实例", "status": "ok",
                "summary": "运行实例已启动并正在接收消息",
                "fix_step": "proactive_test",
            }
        elif prepared:
            runtime_check = {
                "id": "runtime", "label": "陪伴运行实例", "status": "warn",
                "summary": "运行实例已准备，完成引导时会自动启动接收消息",
                "fix_step": "proactive_test",
            }
        else:
            runtime_check = {
                "id": "runtime", "label": "陪伴运行实例", "status": "warn",
                "summary": "完成窗口绑定与模型选择后将自动准备运行实例",
                "fix_step": "model",
            }

        mc_state = memory_companion.get("state", "missing")
        if mc_state == "connected":
            memory_check = {
                "id": "memory", "label": "长期记忆搭档", "status": "ok",
                "summary": "已连接「我会牢牢记住你」，长期记忆与深度回忆完整就绪",
                "fix_step": "knowledge",
            }
        elif mc_state == "external_other":
            memory_check = {
                "id": "memory", "label": "长期记忆搭档", "status": "ok",
                "summary": f"已接入外部记忆插件〈{memory_companion.get('external_name') or '外部记忆插件'}〉",
                "fix_step": "knowledge",
            }
        elif mc_state == "disabled":
            memory_check = {
                "id": "memory", "label": "长期记忆搭档", "status": "warn",
                "summary": "已安装「我会牢牢记住你」但尚未启用，目前仅使用轻量内置记忆",
                "fix_step": "knowledge",
            }
        elif mc_state == "bridge_disabled":
            memory_check = {
                "id": "memory", "label": "长期记忆搭档", "status": "warn",
                "summary": "已启用「我会牢牢记住你」但协作桥接未打开，目前仅使用轻量内置记忆",
                "fix_step": "knowledge",
            }
        else:
            memory_check = {
                "id": "memory", "label": "长期记忆搭档", "status": "warn",
                "summary": "当前仅启用轻量内置记忆，长期记忆与深度回忆不完整，强烈建议安装「我会牢牢记住你」",
                "fix_step": "knowledge",
            }

        proactive_check = {
            "id": "proactive", "label": "主动联系与作息", "status": "ok",
            "summary": "可在引导第 5 步或陪伴小屋随时调整主动频率与免打扰时段",
            "fix_step": "proactive",
        }
        if ctx is not None and session is not None and username:
            try:
                operator = self._operator(self.controller, username)
                cs = require(ContactManagement(session.application, ctx).status(operator))
                enabled = bool((cs.get("settings") or {}).get("enabled", False))
                proactive_check["summary"] = (
                    "私聊主动联系已开启" if enabled else "私聊主动联系当前关闭（可随时开启）"
                )
            except Exception:
                pass

        checks = [account_check, binding_check, model_check, runtime_check, memory_check, proactive_check]
        blocking = [item["id"] for item in (binding_check, model_check) if item["status"] == "error"]
        return {
            "checks": checks,
            "can_finish": not blocking,
            "blocking": blocking,
            "memory_companion": memory_companion,
            "ready": bool(status.get("ready")),
            "accepting": accepting,
        }

    async def health(self):
        try:
            username = self._admin()
            document = await asyncio.to_thread(self._health_document, username=username)
            return self._response(Outcome.success(document))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_health_unavailable", "the onboarding health check could not be computed")

    async def finish_setup(self):
        """Complete first-run onboarding after verifying core prerequisites."""
        try:
            username = self._admin()
            data = await self._body(set(), optional={"start_host"})
            start_host = data.get("start_host", True)
            if type(start_host) is not bool:
                raise ValueError("start_host must be boolean")

            def verify_prerequisites():
                status = self._status_document(username=username)
                if not status["binding_confirmed"]:
                    raise TaskError("setup_binding_confirmation_required",
                                    "confirm the core administrator private window first")
                model = status.get("model") or {}
                provider_id = model.get("provider_id") or ""
                available = {item.get("id") for item in model.get("providers") or ()}
                if not provider_id or provider_id not in available:
                    raise TaskError("setup_model_required",
                                    "choose a loaded AstrBot text provider before finishing setup")
                return bool(status.get("prepared")), bool((status.get("host") or {}).get("accepting"))

            prepared, accepting = await asyncio.to_thread(verify_prerequisites)
            if not prepared:
                initialized = await self.controller.initialize_profile()
                deferred = deferred_initialization(initialized.failure)
                if deferred is not None:
                    return self._response(Outcome.success({
                        "ready": False,
                        "started": False,
                        **deferred,
                        "status": await asyncio.to_thread(self._status_document, username=username),
                        "health": await asyncio.to_thread(self._health_document, username=username),
                    }))
                if initialized.failure is not None and initialized.failure.code != "host_must_stop":
                    raise TaskError(initialized.failure.code, initialized.failure.reason)
            started = accepting
            start_error = ""
            if start_host and not accepting:
                active = await self.controller.start()
                if active.failure is None or active.failure.code == "host_already_started":
                    started = True
                else:
                    start_error = active.failure.code
            await asyncio.to_thread(
                self.progress.finish,
                finished_by=username,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            status = await asyncio.to_thread(self._status_document, username=username)
            health = await asyncio.to_thread(self._health_document, username=username)
            return self._response(Outcome.success({
                "ready": True,
                "started": bool(started or (status.get("host") or {}).get("accepting")),
                "start_error": start_error,
                "status": status,
                "health": health,
            }))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_finish_failed", "the onboarding guide could not be completed")

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
        binding = await asyncio.to_thread(self._binding)
        if not binding.get("bound") or binding.get("pending"):
            code = binding.get("error") if type(binding.get("error")) is str else None
            if code == "setup_core_window_ambiguous":
                raise TaskError(code, "more than one configured private target belongs to a core administrator")
            raise TaskError("setup_binding_confirmation_required", "confirm the core administrator private window first")
        session = self.controller.session or self.controller.last_session
        if session is None:
            raise TaskError("staging_not_initialized", "initialize the selected V2 role first")
        if getattr(self.controller, "_multi_host", False):
            parked = await asyncio.to_thread(self.controller._parked_binding)
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
            current = await asyncio.to_thread(self._binding)
            if (not current.get("bound") or current.get("pending")
                    or (target.platform_id, target.account_id, target.participant_id) != (
                        current.get("platform_id"), current.get("account_id"), current.get("participant_id"))):
                raise TaskError("setup_binding_confirmation_required", "the administrator private window changed during setup")
            if not self._role_matches_binding(current.get("role_id"), context):
                # 窗口目标没变时，角色漂移只修绑定、不阻断测试。
                if not await asyncio.to_thread(self._repair_parked_binding, target, context):
                    logger.warning("[CompanionV2] 管理员私聊窗口角色与绑定不一致，按当前窗口继续 code=host_persona_binding_changed")
            else:
                # The role can still match while the persona was renamed
                # upstream; rewrite the parked persona key once target
                # resolution has accepted the unique live mapping.
                await asyncio.to_thread(self._repair_parked_binding, target, context)
            return session, context, instruction, target
        else:
            context = session.profile.target
            instruction = await session.bridge.verify_role(context.scope, context.role_binding)
            return session, context, instruction, None

    async def _setup_context(self):
        session, context, instruction, _target = await self._setup_context_with_target()
        return session, context, instruction

    async def _recipient_document(self):
        resolver = getattr(self.controller, "proactive_diagnostic_targets", None)
        if callable(resolver):
            targets = list(await resolver())
        else:
            # Single-window hosts have no picker: the confirmed core binding is the
            # only candidate.  Keep the page working there instead of reporting
            # "unsupported" for a host that can answer.
            single = getattr(self.controller, "proactive_diagnostic_target", None)
            if not callable(single):
                raise TaskError("proactive_recipients_unavailable",
                                "the shared host does not support recipient selection")
            try:
                target = await single()
            except TaskError as error:
                if error.code != "setup_binding_confirmation_required":
                    raise
                targets = []
            else:
                targets = [target] if target is not None else []
        stored = await asyncio.to_thread(self.diagnostic_recipient.read)
        selected = next((t for t in targets if self.diagnostic_recipient.identity(t) == stored), None)
        # A unique live CORE recipient needs no manual configuration, even after
        # removal/revocation of a previously saved recipient.
        if len(targets) == 1:
            selected = targets[0]
        return targets, selected, {"candidates": [
            {**self.diagnostic_recipient.identity(t),
             "label": f"{t.participant_id} · {t.platform_id} / {t.account_id}"} for t in targets],
            "selected_target_id": selected.target_id if selected else "",
            "selection_required": len(targets) > 1 and selected is None,
            "stale_selection": stored is not None and not any(
                self.diagnostic_recipient.identity(t) == stored for t in targets)}

    async def proactive_recipients(self):
        try:
            self._admin()
            _targets, _selected, document = await self._recipient_document()
            return self._response(Outcome.success(document))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, OSError, UnicodeError):
            return self._error("proactive_recipient_unavailable", "the saved test recipient could not be read")

    async def save_proactive_recipient(self):
        try:
            self._admin()
            data = await self._body({"target_id"})
            if type(data["target_id"]) is not str or not data["target_id"]:
                raise ValueError("choose a current core administrator")
            if self._test_lock.locked():
                raise TaskError("proactive_test_busy", "wait for the running diagnostic before changing its recipient")
            async with self._test_lock:
                targets, _selected, _document = await self._recipient_document()
                target = next((t for t in targets if t.target_id == data["target_id"]), None)
                if target is None:
                    raise TaskError("proactive_recipient_invalid", "choose a currently authorized core administrator private target")
                await asyncio.to_thread(
                    self.diagnostic_recipient.write, self.diagnostic_recipient.identity(target)
                )
                _targets, _selected, document = await self._recipient_document()
                return self._response(Outcome.success(document))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, OSError, UnicodeError):
            return self._error("proactive_recipient_invalid", "the test recipient could not be saved")

    async def _selected_diagnostic_target(self, requested=None):
        _targets, selected, _document = await self._recipient_document()
        if selected is None:
            raise TaskError("proactive_recipient_required", "select and save a core administrator test recipient first")
        if requested is not None and (type(requested) is not str or requested != selected.target_id):
            raise TaskError("proactive_recipient_changed", "the saved test recipient changed; refresh before starting")
        return selected

    async def _proactive_diagnostic_target(self):
        if callable(getattr(self.controller, "proactive_diagnostic_targets", None)):
            return await self._selected_diagnostic_target()
        binding = await asyncio.to_thread(self._binding)
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
        if callable(getattr(self.controller, "proactive_diagnostic_targets", None)):
            if await self._selected_diagnostic_target(fallback_target.target_id) != fallback_target:
                raise TaskError("proactive_recipient_changed", "the recipient mapping changed before synchronization")
            context, instruction = await session.synchronize(fallback_target)
            if await self._selected_diagnostic_target(fallback_target.target_id) != fallback_target:
                raise TaskError("proactive_recipient_changed", "the recipient identity changed during synchronization")
            if context.scope.kind is not ScopeKind.PRIVATE:
                raise TaskError("setup_private_window_required", "the diagnostic recipient must be private")
            return session, context, instruction
        context, instruction = await session.synchronize(fallback_target)
        binding = await asyncio.to_thread(self._binding)
        if (not binding.get("bound") or binding.get("pending")
                or (binding.get("platform_id"), binding.get("account_id"), binding.get("participant_id")) !=
                (fallback_target.platform_id, fallback_target.account_id, fallback_target.participant_id)):
            raise TaskError("setup_binding_confirmation_required",
                            "the core administrator binding changed during the diagnostic")
        if not self._role_matches_binding(binding.get("role_id"), context):
            if not await asyncio.to_thread(self._repair_parked_binding, fallback_target, context):
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
        role_id = self._context_role(context) or (await asyncio.to_thread(self._binding)).get("role_id") or ""
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

            def read_model_tasks():
                from companion_v2.model_gateway.management import ModelManagement
                manager = ModelManagement(_session.application, context)
                result = require(manager.status(self._operator(self.controller, username), layer="role"))
                names = {item["id"]: item["name"] for item in self._model_providers()}
                for provider in result["providers"]:
                    provider["name"] = names.get(provider["id"], provider["id"])
                return result

            return self._response(Outcome.success(await asyncio.to_thread(read_model_tasks)))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_model_routes_unavailable", "the setup role's model routes could not be read")

    async def configure_model_task(self):
        try:
            username = self._admin()
            data = await self._body({"kind", "operation_key", "edit_token", "changes", "reset"}, optional={"section"})
            _session, context, _instruction = await self._setup_context()

            def save_model_task():
                from companion_v2.model_gateway.management import ModelManagement
                manager = ModelManagement(_session.application, context)
                return require(manager.configure(self._operator(self.controller, username), {**data, "layer": "role"}))

            saved = await asyncio.to_thread(save_model_task)
            return self._response(Outcome.success(saved))
        except TaskError as error:
            return self._error(error.code, str(error))
        except (ValueError, TypeError, KeyError, OSError, UnicodeError):
            return self._error("setup_model_routes_invalid", "check the selected task, providers and edit token")

    async def profile(self):
        try:
            username = self._admin()
            session, context, _instruction = await self._setup_context()

            def read_profile():
                from companion_v2.configuration.management import RoleProfileManagement, RelationshipProfileManagement
                operator = self._operator(self.controller, username)
                return {
                    "role_profile": require(RoleProfileManagement(session.application, context).status(operator)),
                    "relationship_profile": require(RelationshipProfileManagement(session.application, context).status(operator)),
                }

            return self._response(Outcome.success(await asyncio.to_thread(read_profile)))
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

            def write_profile():
                from companion_v2.configuration.management import RoleProfileManagement, RelationshipProfileManagement
                editor_type = RoleProfileManagement if target == "role_profile" else RelationshipProfileManagement
                return require(editor_type(session.application, context).configure(
                    self._operator(self.controller, username),
                    {key: data[key] for key in ("operation_key", "edit_token", "changes")}))

            saved = await asyncio.to_thread(write_profile)
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
                binding = await asyncio.to_thread(self._binding)
                if not binding.get("bound") or binding.get("pending"):
                    raise TaskError("setup_binding_confirmation_required", "confirm the core administrator private window first")
                await self._restore_test_contact_permission(username, data["attempt_id"])
                state = await asyncio.to_thread(
                    self.progress.confirm_proactive_test,
                    attempt_id=data["attempt_id"],
                    confirmed_by=username,
                    confirmed_at=datetime.now(timezone.utc).isoformat(),
                )
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
                saved = await asyncio.to_thread(self.progress.read)
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
                    await asyncio.to_thread(
                        self.progress.begin_proactive_test, attempt_id=attempt_id, started_at=started
                    )
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
                    optional={"variant", "synthetic_event", "diagnostic_image", "diagnostic_generated_image",
                              "target_id", "allow_linked_accounts"})
                purpose = data["purpose"]
                variant = data.get("variant")
                synthetic_event = data.get("synthetic_event", False)
                diagnostic_image = data.get("diagnostic_image", False)
                diagnostic_generated = data.get("diagnostic_generated_image", False)
                allow_linked_accounts = data.get("allow_linked_accounts", False)
                if type(allow_linked_accounts) is not bool:
                    raise TaskError("proactive_test_confirmation_required", "allow_linked_accounts must be an explicit boolean")
                if type(purpose) is not str or purpose not in PURPOSES or data["confirm_delivery"] is not True:
                    raise TaskError("proactive_test_confirmation_required",
                                    "choose one proactive purpose and confirm delivery to the core administrator")
                if type(synthetic_event) is not bool:
                    raise TaskError("proactive_test_confirmation_required", "synthetic_event must be an explicit boolean")
                if (type(diagnostic_image) is not bool or type(diagnostic_generated) is not bool
                        or diagnostic_image and diagnostic_generated
                        or (diagnostic_image or diagnostic_generated) and
                        (purpose != "chat" or synthetic_event or variant is not None)):
                    raise TaskError("proactive_test_confirmation_required",
                                    "an image attachment test requires a source-backed chat diagnostic")
                if variant is not None and (type(variant) is not str or purpose != "environment"
                        or (variant not in DIAGNOSTIC_ENVIRONMENT_VARIANTS and variant != "mobile_location")):
                    raise TaskError("contact_diagnostic_variant_invalid",
                                    "choose a supported environment source for this proactive purpose")
                fallback_target = await self._proactive_diagnostic_target()
                if "target_id" in data and (fallback_target is None or data["target_id"] != fallback_target.target_id):
                    raise TaskError("proactive_recipient_changed", "refresh the saved test recipient before starting")
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

                def check_contact_purpose_sync():
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

                await asyncio.to_thread(check_contact_purpose_sync)

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
                    _settle_failed_stages(stages, failure_stage)
                    return self._response(Outcome.success(self._diagnostic_failure(
                        purpose, route_hint, failure_stage, error.code, str(error),
                        stages=stages, route=route_hint)))
                if fallback_target is not None and callable(getattr(self.controller, "proactive_diagnostic_targets", None)):
                    if await self._selected_diagnostic_target(fallback_target.target_id) != fallback_target:
                        raise TaskError("proactive_recipient_changed", "the recipient changed while preparing diagnostic sources")
                opportunity, intents = candidate["opportunity"], candidate["intents"]
                route = candidate["route"]
                label = PURPOSES[purpose]
                result = await session.application.proactive.contact(context, opportunity, intents,
                    role_instruction=candidate["role_instruction"], selection_seed=attempt_id,
                    administrator_test=True, diagnostic_source_backed=not synthetic_event,
                    **({"diagnostic_allow_linked_accounts": True} if allow_linked_accounts else {}),
                    **({"diagnostic_generated_image": True} if diagnostic_generated else {}),
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
                    _settle_failed_stages(stages, failure_stage)
                    budget_usage = await asyncio.to_thread(
                        self._diagnostic_attempt_usage, session, context, failure.code
                    )
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
                stages.extend(({"stage": "model_generation", "state": "running"},
                               {"stage": "persona_review", "state": "pending"}))
                image_stage = ("image_generation" if diagnostic_generated else
                               "image_attachment" if diagnostic_image else None)
                if image_stage:
                    stages.append({"stage": image_stage, "state": "pending"})
                stages.extend(({"stage": "delivery", "state": "pending"},
                               {"stage": "receipt", "state": "pending"}))
                deadline = asyncio.get_running_loop().time() + 150
                observation = None
                current_task = None
                while asyncio.get_running_loop().time() < deadline:
                    observed = await asyncio.to_thread(
                        session.application.proactive.observe, context, task.task_id
                    )
                    if observed.failure is not None:
                        return self._response(Outcome.success(self._diagnostic_failure(
                            purpose, route, "delivery", observed.failure.code, observed.failure.reason,
                            stages=stages, task_id=task.task_id, route=route)))
                    observation = observed.unwrap()
                    current_task = require(await asyncio.to_thread(
                        session.application.tasks.read, context, task.task_id
                    ))
                    if observation["skipped"] or observation["delivery"] in ("delivered", "failed", "cancelled", "partial", "unknown", "accepted"):
                        break
                    if current_task.state in ("failed", "cancelled", "expired", "uncertain"):
                        break
                    await asyncio.sleep(0.2)

                def finalize_diagnostic_reads():
                    final_task = require(session.application.tasks.read(context, task.task_id))
                    final_obs = (observation if observation is not None
                                 else require(session.application.proactive.observe(context, task.task_id)))
                    targets = self._diagnostic_delivery_targets(session, context, task.task_id)
                    usage = self._diagnostic_usage_summary(session, context)
                    return final_task, final_obs, targets, usage

                current_task, observation, delivery_targets, usage_summary = await asyncio.to_thread(
                    finalize_diagnostic_reads
                )
                skipped = observation["skipped"]
                delivery = observation["delivery"]
                judge = observation["judge"]
                stage_by_name = {item["stage"]: item for item in stages}
                stage_by_name["model_generation"]["state"] = "passed" if current_task.state == "succeeded" else "failed"
                image_failure = None
                image_unknown = False
                if image_stage:
                    image_result = (observation.get("stage_metrics") or {}).get(image_stage, {})
                    image_state = image_result.get("state")
                    if diagnostic_generated:
                        image_unknown = image_state == "unknown" or current_task.state == "uncertain"
                        image_failure = image_result.get("code")
                        if current_task.state in ("failed", "expired", "cancelled", "uncertain"):
                            image_failure = current_task.reason or "proactive_image_generation_failed"
                        if delivery == "delivered" and image_state != "passed":
                            image_failure = "proactive_image_generation_failed"
                        stage_by_name[image_stage]["state"] = (
                            "unknown" if image_unknown else "failed" if image_failure else
                            "passed" if image_state == "passed" else "skipped" if skipped else "pending")
                    elif image_state != "passed":
                        image_failure = "proactive_diagnostic_image_unavailable"
                        stage_by_name[image_stage]["state"] = "failed"
                    else:
                        stage_by_name[image_stage]["state"] = "passed"
                stage_by_name["persona_review"]["state"] = ("passed" if observation["judge_allowed"] is True
                                       else "failed" if observation["judge_allowed"] is False
                                       else "skipped" if skipped
                                       else "not_required" if judge is None else "pending")
                stage_by_name["delivery"]["state"] = ("skipped" if skipped else "passed" if delivery == "delivered"
                                       else "failed" if delivery in ("failed", "cancelled", "partial", "unknown")
                                       else "pending")
                stage_by_name["receipt"]["state"] = ("skipped" if skipped else "passed" if delivery == "delivered"
                                      and observation.get("receipt_ref") else
                                      "failed" if delivery in ("failed", "cancelled", "partial", "unknown") else "pending")
                state = ("unknown" if image_unknown else "failed" if image_failure else
                         "skipped" if skipped else "passed" if delivery == "delivered"
                         else "unknown" if delivery in ("unknown", "accepted")
                         else "pending" if delivery in (None, "pending", "dispatched")
                         else "failed")
                route_failure = _diagnostic_route_failure(observation) if state == "failed" else None
                if route_failure:
                    stage_by_name["delivery"]["detail"] = redact_text(route_failure)
                code = (("proactive_image_result_unknown" if image_unknown else image_failure)
                        if image_failure or image_unknown else
                        None if state in ("passed", "skipped") else
                        "account_link_all_unavailable" if route_failure else
                        (current_task.reason if current_task.state != "succeeded" else None) or (
                    "delivery_result_unknown" if state == "unknown" else "delivery_failed" if state == "failed"
                    else "proactive_test_timeout"))
                return self._response(Outcome.success({"state": state, "purpose": purpose,
                    "purpose_label": label, "variant": route,
                    "route": route,
                    "event_mode": "synthetic_event" if synthetic_event else "source_backed",
                    "recipient": "core_administrator",
                    "recipient_target": self.diagnostic_recipient.identity(fallback_target)
                        if hasattr(self, "diagnostic_recipient") and hasattr(fallback_target, "target_id") else None,
                    "allow_linked_accounts": allow_linked_accounts,
                    "delivery_targets": delivery_targets,
                    "route_rejections": observation.get("route_rejections", []),
                    "attempt_id": attempt_id, "task_id": task.task_id,
                    "task_state": current_task.state, "delivery_state": delivery,
                    "judge_state": judge, "judge_reason": (redact_text(observation["judge_reason"])
                        if observation["judge_reason"] else None),
                    "judge_category": observation.get("judge_category"),
                    "judge_rewritten": observation.get("judge_rewritten", False),
                    "skip_reason": observation.get("skip_reason"),
                    "model_skip_reason": observation.get("model_skip_reason"),
                    "dedup_reason": observation.get("dedup_reason"),
                    "presentation_reason": observation.get("presentation_reason"),
                    "decision_confidence": observation.get("decision_confidence"),
                    "silence_threshold": observation.get("silence_threshold"),
                    "stage_metrics": observation.get("stage_metrics"),
                    "usage_summary": usage_summary,
                    "receipt_ref": observation["receipt_ref"], "stage": None if state in ("passed", "skipped") else
                        image_stage if image_failure or image_unknown else
                        "delivery" if delivery else "model_generation", "code": code,
                    "error": None if code is None else redact_text(route_failure or (current_task.reason if current_task.state != "succeeded" else None) or code),
                    "suggestion": (PROACTIVE_TEST_SUGGESTIONS.get(code)
                        or PROACTIVE_TEST_STAGE_SUGGESTIONS.get(image_stage if image_failure or image_unknown else "delivery"))
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
    def _diagnostic_delivery_targets(session, context, task_id):
        getter = getattr(session.application.proactive, "_delivery_rows", None)
        router = getattr(getattr(session.application, "outbound", None), "private_account_router", None)
        rows = getter(task_id) if callable(getter) else ()
        recipients = []
        seen = set()
        for row in rows:
            if row["state"] not in ("delivered", "accepted", "unknown", "dispatched"):
                continue
            actual = router.actual_context(row["delivery_id"]) if router is not None else None
            scope = (actual or context).scope
            key = (scope.platform, scope.account_id, scope.native_scope_id, row["state"])
            if key not in seen:
                seen.add(key)
                recipients.append({"platform_id": key[0], "account_id": key[1],
                                   "participant_id": key[2], "delivery_state": key[3]})
        return recipients

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
        else:
            _settle_failed_stages(stages, stage)
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

            def prepare_test_permission_sync():
                operator = dashboard_operator(self.controller.runtime, username, at=self.controller.now())
                manager = ContactManagement(app, context)
                current = require(manager.status(operator))
                enabled_before = current["settings"]["enabled"]
                self._call_progress_sync(
                    self.progress.record_test_contact_permission,
                    attempt_id=attempt_id, enabled_before=enabled_before,
                )
                if not enabled_before:
                    require(manager.configure(operator, {
                        "operation_key": "setup-test-" + attempt_id,
                        "edit_token": current["edit_token"],
                        "changes": {"enabled": True},
                    }))
                self._call_progress_sync(
                    self.progress.record_proactive_test_progress,
                    attempt_id=attempt_id, stages=stages,
                )

            await asyncio.to_thread(prepare_test_permission_sync)
            now = self.controller.now()
            schedule = getattr(app, "contact_schedule", None)
            producer = getattr(schedule, "diagnostic_candidate", None)
            if not callable(producer):
                raise TaskError("proactive_diagnostic_unavailable",
                                "the active host has no source-backed proactive producer registry")
            try:
                # The guide asks the administrator to send one private message
                # immediately before this test.  The new turn can still be
                # completing when the button is pressed; _origin then correctly
                # reports contact_conversation_changed.  That is a transient
                # race, not an unusable test opportunity: wait briefly for the
                # current turn to settle and retry the same real producer.
                candidate = None
                for attempt_no in range(30):
                    now = self.controller.now()
                    try:
                        candidate = await producer(context, "chat", at=now)
                        break
                    except TaskError as error:
                        if error.code != "contact_conversation_changed" or attempt_no == 29:
                            raise
                        if attempt_no % 5 == 0:
                            logger.info(
                                "[CompanionV2] 主动测试等待当前私聊轮次完成 attempt=%s/30",
                                attempt_no + 1)
                        await asyncio.sleep(1.0)
            except TaskError:
                stages.append({"stage": "trusted_candidate", "state": "failed"})
                await asyncio.to_thread(
                    self._call_progress_sync,
                    self.progress.record_proactive_test_progress,
                    attempt_id=attempt_id, stages=stages,
                )
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
            await asyncio.to_thread(
                self._call_progress_sync,
                self.progress.record_proactive_test_progress,
                attempt_id=attempt_id, route=route, stages=stages, task_id=task_id,
            )
            attempt = (app, context, task_id)
            result = await app.proactive.contact(context, opportunity, intents,
                role_instruction=candidate["role_instruction"], selection_seed=attempt_id,
                administrator_test=True, diagnostic_source_backed=True,
                diagnostic_origin=candidate["diagnostic_origin"])
            if result.failure is not None:
                failed_stage = PROACTIVE_TEST_FAILURE_LABELS.get(
                    result.failure.code, ("contact_admission", "主动联系准入检查失败"))[0]
                _settle_failed_stages(stages, failed_stage)

                def finish_admission_failed():
                    self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                    self._call_progress_sync(
                        self.progress.finish_proactive_test,
                        outcome="failed",
                        finished_at=datetime.now(timezone.utc).isoformat(),
                        error_code=result.failure.code,
                        report=report(),
                    )

                await asyncio.to_thread(finish_admission_failed)
                return
            task = result.unwrap().task
            if task is None:
                _settle_failed_stages(stages, "model_generation")

                def finish_not_started():
                    self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                    self._call_progress_sync(
                        self.progress.finish_proactive_test,
                        outcome="failed",
                        finished_at=datetime.now(timezone.utc).isoformat(),
                        error_code="setup_contact_not_started",
                        report=report(),
                    )

                await asyncio.to_thread(finish_not_started)
                return
            deadline = asyncio.get_running_loop().time() + 150
            last_progress = None
            while asyncio.get_running_loop().time() < deadline:
                def poll_step_sync():
                    receipt = self._call_progress_sync(self.progress.read)["proactive_test"]
                    if not receipt or receipt["attempt_id"] != attempt_id or receipt["state"] != "started":
                        return None, None
                    return receipt, require(app.proactive.observe(context, task.task_id))

                receipt, observed = await asyncio.to_thread(poll_step_sync)
                if receipt is None:
                    return
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
                    def finish_generation_failed():
                        current_task = require(app.tasks.read(context, task.task_id))
                        stage_state("model_generation", "failed")
                        for item in stages:
                            if item["stage"] == "model_generation" and current_task.reason:
                                item["detail"] = redact_text(str(current_task.reason))[:256]
                        stage_state("persona_review", "skipped")
                        stage_state("delivery", "skipped")
                        stage_state("receipt", "skipped")
                        self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                        self._call_progress_sync(
                            self.progress.finish_proactive_test,
                            outcome="unknown" if generation == "uncertain" else "failed",
                            finished_at=datetime.now(timezone.utc).isoformat(),
                            error_code="setup_contact_generation_failed", report=report(),
                        )

                    await asyncio.to_thread(finish_generation_failed)
                    return
                progress = (tuple((item["stage"], item["state"], item.get("detail", ""))
                                  for item in stages), delivery, observed.get("receipt_ref"))
                if progress != last_progress:
                    await asyncio.to_thread(
                        self._call_progress_sync,
                        self.progress.record_proactive_test_progress,
                        attempt_id=attempt_id, stages=stages, route=route,
                        task_id=task.task_id, delivery_state=delivery or "",
                        receipt_ref=observed.get("receipt_ref") or "",
                    )
                    last_progress = progress
                if delivery == "delivered":
                    stage_state("delivery", "passed")
                    stage_state("receipt", "passed" if observed.get("receipt_ref") else "failed")

                    def finish_delivered():
                        self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                        self._call_progress_sync(
                            self.progress.finish_proactive_test,
                            outcome="delivered",
                            finished_at=datetime.now(timezone.utc).isoformat(),
                            report=report(delivery, observed.get("receipt_ref") or ""),
                        )

                    await asyncio.to_thread(finish_delivered)
                    return
                if delivery == "failed" or observed["skipped"] or observed["judge"] == "denied":
                    stage_state("persona_review", "failed" if observed["judge"] == "denied" else
                                "skipped" if observed["skipped"] else stages[-3]["state"])
                    stage_state("delivery", "failed" if delivery == "failed" else "skipped")
                    stage_state("receipt", "failed" if delivery == "failed" else "skipped")

                    def finish_not_delivered():
                        self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                        self._call_progress_sync(
                            self.progress.finish_proactive_test,
                            outcome="failed",
                            finished_at=datetime.now(timezone.utc).isoformat(),
                            error_code="setup_contact_not_delivered", report=report(delivery or "skipped"),
                        )

                    await asyncio.to_thread(finish_not_delivered)
                    return
                if delivery == "unknown" or delivery == "accepted":
                    stage_state("delivery", "failed")
                    stage_state("receipt", "failed")

                    def finish_unknown():
                        self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                        self._call_progress_sync(
                            self.progress.finish_proactive_test,
                            outcome="unknown",
                            finished_at=datetime.now(timezone.utc).isoformat(),
                            error_code="delivery_result_unknown",
                            report=report(delivery, observed.get("receipt_ref") or ""),
                        )

                    await asyncio.to_thread(finish_unknown)
                    return
                await asyncio.sleep(0.2)
            stage_state("delivery", "failed")
            stage_state("receipt", "failed")

            def finish_timeout():
                self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                self._call_progress_sync(
                    self.progress.finish_proactive_test,
                    outcome="unknown",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    error_code="setup_contact_timeout",
                    report=report("unknown"),
                )

            await asyncio.to_thread(finish_timeout)
        except asyncio.CancelledError:
            try:
                def finish_cancelled():
                    if app is not None and context is not None:
                        self._restore_test_contact_permission_with_context(username, attempt_id, app, context)
                    self._call_progress_sync(
                        self.progress.finish_proactive_test,
                        outcome="unknown",
                        finished_at=datetime.now(timezone.utc).isoformat(),
                        error_code="setup_test_interrupted",
                    )
                await asyncio.to_thread(finish_cancelled)
            except (TaskError, ValueError, OSError):
                pass
            raise
        except Exception as error:
            code = error.code if isinstance(error, TaskError) else "setup_test_failed"
            outcome = "unknown" if attempt is not None else "failed"

            def finish_exception():
                nonlocal code, outcome
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
                    self._call_progress_sync(
                        self.progress.finish_proactive_test,
                        outcome=outcome,
                        finished_at=datetime.now(timezone.utc).isoformat(),
                        error_code=code, report=report(),
                    )
                except ValueError:
                    pass

            await asyncio.to_thread(finish_exception)

    async def _restore_test_contact_permission(self, username: str, attempt_id: str) -> None:
        receipt = (await asyncio.to_thread(self._call_progress_sync, self.progress.read))["proactive_test"]
        if (not receipt or receipt["attempt_id"] != attempt_id
                or "contact_enabled_before" not in receipt
                or receipt.get("contact_permission_restored")):
            return
        if receipt["contact_enabled_before"]:
            await asyncio.to_thread(
                self._call_progress_sync,
                self.progress.mark_test_contact_permission_restored, attempt_id=attempt_id,
            )
            return
        if self.controller.session is None and self.controller.last_session is None:
            loaded = await self.controller.load_existing()
            if loaded.failure is not None:
                raise TaskError(loaded.failure.code, loaded.failure.reason)
        session, context, _instruction = await self._test_context()
        await asyncio.to_thread(
            self._restore_test_contact_permission_with_context,
            username, attempt_id, session.application, context,
        )

    def _restore_test_contact_permission_with_context(self, username: str, attempt_id: str,
                                                      app, context) -> None:
        receipt = self._call_progress_sync(self.progress.read)["proactive_test"]
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
        self._call_progress_sync(self.progress.mark_test_contact_permission_restored, attempt_id=attempt_id)
