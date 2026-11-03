"""Dashboard-only editors for the configuration groups the cottage edits itself.

Groups that already had owners and consumers but no read-write route get one
here. Each section keeps its own edit token because the scopes differ: reaction
retrieval is installation-wide, the daily allowance is an installation/role
ceiling, and role behavior settings prefer role defaults with object pages
providing explicit window overrides. Personal permissions and private material
remain on their user, device or relationship routes.
"""

import json
import logging

from astrbot.api.web import request
from companion_v2.configuration.management import (AcquaintanceManagement, BusyGateManagement,
                                                   ContentAuthoringManagement,
                                                   DiaryAndRestManagement, GroupParticipationManagement,
                                                   ImageQuotaManagement,
                                                   ImageOutputManagement,
                                                   ImageReviewManagement, KnowledgeManagement,
                                                   LibraryManagement,
                                                   ModelBudgetManagement, ProactiveContactManagement,
                                                   PrivateAccessManagement, StrangerPrivateAccessManagement,
                                                   ReactionRetrievalManagement,
                                                   RecallManagement, ReferenceManagement,
                                                   WebUnderstandingManagement,
                                                   RoleGrowthSkillsManagement,
                                                   ExpressionLearningManagement,
                                                   RoleProfileManagement,
                                                   RelationshipProfileManagement,
                                                   QzoneManagement,
                                                   RelayManagement,
                                                   ScreenManagement, WardrobeManagement)
from companion_v2.configuration.models import ConfigurationError
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.knowledge.configuration import KnowledgeSettings

from .knowledge_sources import AstrBotKnowledgeSources

from .management_identity import dashboard_operator


PREFIX = "/astrbot_plugin_private_companion/config"
logger = logging.getLogger(__name__)
SECTIONS = {"quota": ImageQuotaManagement, "image_review": ImageReviewManagement,
            "image.output": ImageOutputManagement,
            "busy_gate": BusyGateManagement,
            "content.authoring": ContentAuthoringManagement,
            "reaction": ReactionRetrievalManagement, "budget": ModelBudgetManagement,
            "recall": RecallManagement, "screen": ScreenManagement,
            "web_understanding": WebUnderstandingManagement,
            "relay": RelayManagement,
            # QQ 空间拓展的角色级开关与节流；安装与逐会话同意不在这里，归能力安装路由。
            "qzone": QzoneManagement,
            "person": AcquaintanceManagement,
            # Known users use role defaults; unbound accounts use installation settings.
            "private_access": PrivateAccessManagement,
            "private_access_installation": StrangerPrivateAccessManagement,
            "role.growth.skills": RoleGrowthSkillsManagement,
            "expression.learning": ExpressionLearningManagement,
            "knowledge": KnowledgeManagement, "life": DiaryAndRestManagement,
            "role_profile": RoleProfileManagement,
            "relationship_profile": RelationshipProfileManagement,
            "library": LibraryManagement,
            "wardrobe": WardrobeManagement, "photo.reference": ReferenceManagement,
            "group_participation": GroupParticipationManagement,
            # 公共联系设置全局保存；个人偏好和授权在私聊用户页单独维护。
            "proactive.contact": ProactiveContactManagement}


def _document(raw, *, expected):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite value")))
    if type(data) is not dict or set(data) != expected:
        raise ValueError("unexpected or missing configuration fields")
    return data


class ConfigurationWebAPI:
    """One route family for the groups the cottage edits outside their own page."""

    def __init__(self, controller, *, allowed_sections=None):
        self.controller = controller
        if allowed_sections is None:
            self.sections = SECTIONS
        else:
            names = tuple(allowed_sections)
            if not names or set(names) - set(SECTIONS) or len(set(names)) != len(names):
                raise ValueError("invalid configuration section allowlist")
            self.sections = {name: SECTIONS[name] for name in names}

    def routes(self):
        routes = [(PREFIX, self.status, ["GET"], "Read allowed configuration sections"),
                (PREFIX + "/configure", self.configure, ["POST"],
                 "Save one allowed section with its own edit token")]
        if "group_participation" in self.sections:
            routes.extend([(PREFIX + "/behavior", self.behavior_status, ["GET"], "Read role-owned group habits"),
                           (PREFIX + "/behavior/command", self.behavior_command, ["POST"], "Review one learned group habit")])
        return routes

    async def _run_behavior(self, *, command=False):
        from companion_v2.conversation.behavior.management import BehaviorManagement
        from .web_api import outcome_response
        try:
            username = request.username
            if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            if request.query:
                raise TaskError("group_behavior_command_invalid", "unexpected behavior query")
            data = None
            if command:
                raw = await request.body()
                if len(raw) > 4096:
                    raise ValueError("behavior command is too large")
                data = _document(raw, expected={"group_key", "habit_id", "enabled", "revision"})
            async with self.controller._control_lock:
                controller = self.controller
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                session = controller.session or controller.last_session
                if session is None or controller.runtime is None:
                    raise TaskError("staging_not_initialized", "load the V2 profile first")
                operator = dashboard_operator(controller.runtime, username, at=controller.now())
                manager = BehaviorManagement(session.application, session.profile.target)
                payload = manager.command(operator, data) if command else manager.status(operator)
                return outcome_response(Outcome.success(payload))
        except (TaskError, ConfigurationError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError):
            return outcome_response(Outcome.failed("group_behavior_command_invalid", "invalid behavior request"))

    async def behavior_status(self):
        return await self._run_behavior()

    async def behavior_command(self):
        return await self._run_behavior(command=True)

    async def _run(self, action):
        from .web_api import outcome_response
        try:
            username = request.username
            if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            query = request.query
            if set(query.keys()) - {"sections"} or any(len(query.getlist(key)) != 1 for key in query.keys()):
                raise TaskError("configuration_request_invalid", "unexpected configuration query")
            data = None
            if action == "configure":
                raw = await request.body()
                if len(raw) > 32768:
                    raise ValueError("configuration request is too large")
                data = _document(raw, expected={"section", "operation_key", "edit_token", "changes"})
                if data["section"] not in self.sections:
                    raise TaskError("configuration_request_invalid", "unknown configuration section")
                if (type(data["operation_key"]) is not str or not data["operation_key"].strip()
                        or len(data["operation_key"]) > 128 or type(data["edit_token"]) is not str
                        or len(data["edit_token"]) != 64 or type(data["changes"]) is not dict or not data["changes"]):
                    raise ValueError("provide a stable operation key, edit token and explicit changes")
            async with self.controller._control_lock:
                controller = self.controller
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                session = controller.session or controller.last_session
                if session is None or controller.runtime is None:
                    raise TaskError("staging_not_initialized", "load the V2 profile first")
                operator = dashboard_operator(controller.runtime, username, at=controller.now())
                context = session.profile.target
                if action == "status":
                    result = Outcome.success(await self._status(session.application, context, operator, query,
                                                                 host_context=controller.context))
                else:
                    editor = SECTIONS[data["section"]](session.application, context)
                    result = editor.configure(operator, {key: data[key]
                                                         for key in ("operation_key", "edit_token", "changes")})
                    if result.is_success:
                        # The operation receipt confirms the write. Pages refresh status
                        # separately so a slow projection cannot turn a committed write
                        # into an unconfirmed POST.
                        result = Outcome.success({"result": result.unwrap()})
                return outcome_response(result)
        except (TaskError, ConfigurationError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("configuration_request_invalid",
                                                   "invalid UTF-8 configuration request"))

    def _requested(self, query):
        raw = query.get("sections", "") or ""
        wanted = tuple(self.sections) if not raw else tuple(part.strip() for part in raw.split(",") if part.strip())
        if not wanted or set(wanted) - set(self.sections) or len(set(wanted)) != len(wanted):
            raise TaskError("configuration_request_invalid", "unknown configuration section")
        return wanted

    async def _status(self, app, context, operator, query, *, host_context=None, section_names=None):
        sections, section_errors = {}, {}
        requested = tuple(section_names) if section_names is not None else self._requested(query)
        for name in requested:
            try:
                result = self.sections[name](app, context).status(operator)
                if result.failure is not None:
                    if len(requested) == 1:
                        raise TaskError(result.failure.code, result.failure.reason)
                    section_errors[name] = {"code": result.failure.code}
                    continue
                sections[name] = {**result.unwrap(), "section": name}
                if name == "knowledge":
                    section = sections[name]
                    try:
                        selected = KnowledgeSettings.from_values(section["settings"]).knowledge_source_ids()
                    except Exception:
                        selected = ()
                    try:
                        sources = await AstrBotKnowledgeSources(host_context).list_sources()
                        sources_available = bool(getattr(host_context, "kb_manager", None))
                    except Exception:
                        sources = []
                        sources_available = False
                    available = {item["id"] for item in sources}
                    available.update(doc["id"] for item in sources for doc in item.get("documents", ()))
                    for identifier in selected:
                        if identifier not in available:
                            sources.append({"id": identifier, "type": "missing", "kb_id": "",
                                            "name": "不可用来源 · " + identifier,
                                            "documents": []})
                    section["knowledge_sources"] = sources
                    section["knowledge_sources_available"] = sources_available
            except (TaskError, ConfigurationError) as error:
                sections.pop(name, None)
                if len(requested) == 1:
                    raise
                section_errors[name] = {"code": error.code}
            except Exception:
                sections.pop(name, None)
                logger.exception("[CompanionV2] 配置分区读取失败 section=%s", name)
                if len(requested) == 1:
                    raise TaskError("configuration_section_read_failed",
                                    "the configuration section could not be read") from None
                section_errors[name] = {"code": "configuration_section_read_failed"}
        return {"sections": sections, "section_errors": section_errors}

    async def status(self):
        return await self._run("status")

    async def configure(self):
        return await self._run("configure")
