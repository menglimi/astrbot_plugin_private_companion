"""Dashboard entry point for the QQ Zone extension.

The page and the model share one service: the route resolves the authenticated
operator and the pinned private target, then calls the same install, scan, like,
comment and publish calls the character uses later. Nothing here reaches into the
provider directly, and no route accepts a target override.
"""

from __future__ import annotations

import json

from astrbot.api.web import request

from companion_v2.capabilities.qzone_access import QzoneAccess
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.qzone.comment_scan import candidate_for

from .management_identity import dashboard_operator


PREFIX = "/astrbot_plugin_private_companion/qzone-extension"
MAX_BODY_BYTES = 8192


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _operation_key(command, *, required=True):
    key = command.get("operation_key") if isinstance(command, dict) else None
    if key is None and not required:
        return ""
    if type(key) is not str or not key.strip() or len(key) > 160:
        raise TaskError("qzone_request_invalid", "an explicit operation key is required")
    return key.strip()


class QzoneWebAPI:
    def __init__(self, controller):
        self.controller = controller
        self._share_planner = None

    def routes(self):
        return (
            (PREFIX + "/status", self.status, ["GET"], "Read the QQ Zone extension state"),
            (PREFIX + "/configure", self.configure, ["POST"], "Install or revoke QQ Zone for this target"),
            (PREFIX + "/feed", self.feed, ["GET"], "Scan the QQ Zone timeline"),
            (PREFIX + "/like", self.like, ["POST"], "Like a scanned post"),
            (PREFIX + "/comment", self.comment, ["POST"], "Reply to a scanned post"),
            (PREFIX + "/publish", self.publish, ["POST"], "Publish a post to the role's Zone"),
            (PREFIX + "/refresh-credentials", self.refresh, ["POST"], "Refresh the QQ Zone session"),
            (PREFIX + "/scan-comments", self.scan_comments, ["GET"],
             "Read which comments this round may answer"),
            (PREFIX + "/reply-comment", self.reply_comment, ["POST"],
             "Answer one scanned comment and record it"),
            (PREFIX + "/agenda-suggestions/resolve", self.resolve_suggestion, ["POST"],
             "Close one pending agenda suggestion with its calendar receipt"),
        )

    async def status(self):
        return await self._run(lambda access, service, at: access.status(self._operator(at), at))

    async def configure(self):
        command = await self._command()
        return await self._run(lambda access, service, at: access.configure(self._operator(at), command, at))

    async def feed(self):
        query = request.query if hasattr(request, "query") else {}
        scope = str(query.get("scope") or "friends")
        if scope not in ("friends", "own"):
            return await self._failed("qzone_request_invalid", "刷空间只支持 friends 或 own")
        try:
            limit = int(query.get("limit") or 0) or None
        except (TypeError, ValueError):
            return await self._failed("qzone_request_invalid", "invalid scan limit")
        detail = str(query.get("detail") or "").lower() in ("1", "true", "yes")
        return await self._run(lambda access, service, at: service.scan(
            self._context(), scope=scope, limit=limit, detail=detail))

    async def like(self):
        command = await self._command()
        return await self._run(lambda access, service, at: service.like(
            self._context(), post_ref=str(command.get("post_ref") or ""),
            operation_key=_operation_key(command)))

    async def comment(self):
        command = await self._command()
        return await self._run(lambda access, service, at: service.comment(
            self._context(), post_ref=str(command.get("post_ref") or ""),
            text=str(command.get("text") or ""), operation_key=_operation_key(command)))

    async def publish(self):
        command = await self._command()

        async def publish(access, service, at):
            context = self._context()
            # An authenticated operator is the human in the loop: choosing the topic
            # and scene on the page *is* the confirmation. A model call cannot set
            # this flag (see the tool port), so auto mode stays the only model path.
            refs = [str(item) for item in (command.get("source_refs") or ())]
            candidate_id = str(command.get("share_candidate_id") or "").strip()
            image_id = str(command.get("share_image_id") or "").strip()
            if candidate_id and image_id:
                raise TaskError("qzone_request_invalid", "引用相册图只能用 share_image_id 或 share_candidate_id 之一")
            if candidate_id:
                # 备好候选、稍后发布：发布前**再复检一次**，撤销过的素材当场拒绝。
                refs.append(self._recheck_share(candidate_id, context, at))
            elif image_id:
                source_id, candidate_id = self._share_source(image_id, context, at)
                refs.append(source_id)
            receipt = await service.publish(
                context, text=str(command.get("text") or ""),
                topic=str(command.get("topic") or ""), scene=str(command.get("scene") or ""),
                confidence=command.get("confidence", 1.0), topic_confirmed=True,
                source_refs=tuple(refs),
                agenda_proposal=command.get("agenda_proposal"),
                operation_key=_operation_key(command))
            return {"candidate_id": candidate_id, "source_refs": tuple(refs), "receipt": receipt}
        return await self._run(publish)

    async def refresh(self):
        async def refresh_credentials(access, service, at):
            await service.transport.credential(refresh=True)
            return access.status(self._operator(at), at)
        return await self._run(refresh_credentials)

    async def resolve_suggestion(self):
        """Close one pending agenda suggestion.

        Marking it ``applied`` requires the calendar operation that actually
        changed the agenda: the write itself belongs to the calendar domain, so
        the page (or the model, through the calendar tool) performs it there and
        reports the receipt here. ``dismissed`` needs no receipt.
        """
        command = await self._command()
        state = str(command.get("state") or "").strip()
        suggestion_id = str(command.get("suggestion_id") or "").strip()
        if not suggestion_id:
            raise TaskError("qzone_request_invalid", "必须先指定要处理的日程建议")
        return await self._run(lambda access, service, at: service.resolve_agenda_suggestion(
            self._context(), suggestion_id=suggestion_id, state=state,
            calendar_operation_id=str(command.get("calendar_operation_id") or ""),
            result_refs=command.get("result_refs") or (), operation_key=_operation_key(command)))

    async def scan_comments(self):
        """评论收件箱这一轮该回哪几条——只读，不发送、不调模型。"""
        async def scan(access, service, at):
            context = self._context()
            scanner, owner = await self._scanner(service, context)
            plan = await scanner.scan(owner=owner, context=context, at=at)
            return {"due": plan.due, "reason": plan.reason,
                    "candidates": [{"post_ref": item.post_ref, "post_id": item.post_id,
                                    "author": item.author, "author_key": item.event.author,
                                    "text": item.text, "operation_key": item.operation_key}
                                   for item in plan.candidates],
                    "skipped": [{"reason": reason, "event_id": event_id}
                                for reason, event_id in plan.skipped],
                    "next_scan_at": plan.cursor.next_scan_at.isoformat() if plan.cursor.next_scan_at else None,
                    "consecutive_failures": plan.cursor.consecutive_failures}
        return await self._run(scan)

    async def reply_comment(self):
        """回一条扫到的评论，并把它记进账本。

        候选**在宿主这边按请求字段重新折叠**（``candidate_for``），所以回复用的键永远由
        评论身份决定，页面伪造不了一个能绕过"同键拒重放"的键。
        """
        command = await self._command()

        async def reply(access, service, at):
            context = self._context()
            scanner, owner = await self._scanner(service, context)
            candidate = candidate_for(
                platform=context.scope.platform, login=scanner.login,
                post_ref=str(command.get("post_ref") or ""), post_id=str(command.get("post_id") or ""),
                uin=command.get("author_uin"), name=str(command.get("author_name") or ""),
                content=str(command.get("comment_text") or ""), created=str(command.get("comment_created") or ""),
                author=str(command.get("author_name") or ""))
            receipt = await scanner.reply(owner=owner, context=context, candidate=candidate,
                                          text=str(command.get("text") or ""), at=at)
            return {"post_ref": candidate.post_ref, "operation_key": candidate.operation_key,
                    "answered": True, "receipt": receipt if isinstance(receipt, dict) else None}
        return await self._run(reply)

    # ---- plumbing ------------------------------------------------------

    def _operator(self, at):
        username = request.username
        if type(username) is not str or not username.strip() or username.startswith("api_key:"):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        return dashboard_operator(self.controller.runtime, username, at=at)

    def _context(self):
        session = self.controller.session or self.controller.last_session
        return session.profile.target

    async def _scanner(self, service, context):
        """扫描器要三样东西：缓存好的凭据（拿空间账号）、运行库、以及角色的账本归属。

        账号来自凭据本身（``uin``），不猜、也不允许页面指定——账本按账号分档，
        认错账号就会把别人的回复算到这个号头上。
        """
        from companion_v2.qzone.comment_scan import CommentScanner
        from companion_v2.qzone.inbox import CommentInbox
        from companion_v2.qzone.service import owner_key

        credential = await service.transport.credential()
        runtime = self.controller.runtime
        scanner = CommentScanner(service=service, inbox=CommentInbox(runtime, now=self.controller.now),
                                 runtime=runtime, platform=context.scope.platform,
                                 login=str(credential.uin), now=self.controller.now)
        return scanner, owner_key(context)

    def _planner(self):
        """一个控制器一份候选目录：候选是"稍后发布"的凭据，必须跨请求还在。

        与空间动态引用同一套做法（进程内、进程重启即失效）；候选本身不是授权，
        发布前还会再复检一次源与素材。
        """
        if self._share_planner is None:
            from companion_v2.sources.repositories import AssetRepository, SourceRepository
            from companion_v2.sharing.planner import ShareCatalog, SharePlanner

            self._share_planner = SharePlanner(sources=SourceRepository(self.controller.runtime),
                                               assets=AssetRepository(self.controller.runtime),
                                               catalog=ShareCatalog())
        return self._share_planner

    def _recheck_share(self, candidate_id, context, at):
        """发布前复检一个先前备好的候选：素材被撤销/过期就当场拒绝。

        候选是"稍后发布"的凭据，不是通行证：``follow_up_material`` 会重新读源仓库与
        素材仓库，所以页面停留期间被撤销的图不会因为"刚才备过案"而发出去。
        """
        from companion_v2.execution.models import require
        from companion_v2.qzone.capability import PUBLISH_ACTION

        session = self.controller.session or self.controller.last_session
        material = require(self._planner().follow_up_material(
            candidate_id, resolved=context.actor, capability=PUBLISH_ACTION, observed_at=at,
            authorizer=session.application.tasks.identity, role_owner=self._role_owner(context)))
        return material.source.source_id

    def _share_source(self, image_id, context, at):
        """把"发说说时要配的那张相册图"折成它的**受治理来源**（经 SharePlanner 复检）。

        **不做上传**：这里只是把来源标注带进发布参数——``publish`` 本来就收 ``source_refs``，
        而相册图在生成时就已经登记为受治理的 source。图片本体留在相册里；真上传是一次
        外部效果，要按"结果未知不重放"的纪律单独做。

        复检用的能力是**公开发布**那一项：要公开引用一张图，就得先持有公开说话的授权，
        这一点由 ``SharePlanner`` 去读源仓库时真实校验，不是这里自己声明的。
        """
        from companion_v2.execution.models import require
        from companion_v2.qzone.capability import PUBLISH_ACTION

        session = self.controller.session or self.controller.last_session
        application = session.application
        generated = application.messages.generated_images
        item = generated.album_image(context, at, image_id)
        candidate = require(generated.album_share_candidate(
            item, context, planner=self._planner(), capability=PUBLISH_ACTION, at=at,
            authorizer=application.tasks.identity))
        return candidate.material.source.source_id, candidate.candidate_id

    @staticmethod
    def _role_owner(context):
        from companion_v2.foundation.refs import RoleOwnerRef

        return RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                            context.role_binding.role.subject_id)

    async def _command(self):
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            raise TaskError("qzone_request_invalid", "QQ Zone request is too large")
        command = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                             parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite value")))
        if type(command) is not dict:
            raise ValueError("command must be an object")
        return command

    async def _failed(self, code, reason):
        from .web_api import outcome_response
        return outcome_response(Outcome.failed(code, reason))

    async def _run(self, call):
        from .web_api import outcome_response
        controller = self.controller
        try:
            async with controller._control_lock:
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "the V2 host has stopped")
                session = controller.session or controller.last_session
                if session is None or controller.runtime is None:
                    raise TaskError("staging_not_initialized", "load the explicit V2 profile first")
                service = getattr(session.application, "qzone", None)
                if service is None:
                    raise TaskError("qzone_unavailable", "QQ 空间扩展没有可用的宿主通道")
                at = controller.now()
                # The credential conclusion travels with ``QzoneService.status`` so
                # it is read when the status is built; capturing a snapshot here
                # would make "refresh then report" answer with the old state.
                access = QzoneAccess(session.application, session.profile.target, service=service)
                result = call(access, service, at)
                if hasattr(result, "__await__"):
                    result = await result
                return outcome_response(Outcome.success(result))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            return outcome_response(Outcome.failed("qzone_request_invalid", "invalid QQ Zone request"))


__all__ = ["PREFIX", "QzoneWebAPI"]
