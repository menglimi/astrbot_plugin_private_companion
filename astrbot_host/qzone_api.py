"""Dashboard entry point for the QQ Zone extension.

The page and the model share one service: the route resolves the authenticated
operator and the pinned private target, then calls the same install, scan, like,
comment and publish calls the character uses later. Nothing here reaches into the
provider directly, and no route accepts a target override.
"""

from __future__ import annotations

import asyncio
import inspect
import json

from astrbot.api.web import request

from companion_v2.capabilities.qzone_access import QzoneAccess
from companion_v2.execution.models import TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.qzone.comment_scan import candidate_for
from companion_v2.qzone.images import (MAX_PUBLISH_BODY, PublishImage, album_images, candidate_image,
                                       image_batch)
from companion_v2.storage.runtime import StorageOpenError

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
        self._share_planner_runtime = None

    def routes(self):
        return (
            (PREFIX + "/status", self.status, ["GET"], "Read the QQ Zone extension state"),
            (PREFIX + "/configure", self.configure, ["POST"], "Install or revoke QQ Zone for this target"),
            (PREFIX + "/feed", self.feed, ["GET"], "Scan the QQ Zone timeline"),
            (PREFIX + "/like", self.like, ["POST"], "Like a scanned post"),
            (PREFIX + "/comment", self.comment, ["POST"], "Reply to a scanned post"),
            (PREFIX + "/publish", self.publish, ["POST"], "Publish a post to the role's Zone"),
            (PREFIX + "/album", self.album, ["GET"], "Select a governed album image for a Zone post"),
            (PREFIX + "/refresh-credentials", self.refresh, ["POST"], "Refresh the QQ Zone session"),
            (PREFIX + "/scan-comments", self.scan_comments, ["GET"],
             "Read which comments this round may answer"),
            (PREFIX + "/reply-comment", self.reply_comment, ["POST"],
             "Answer one scanned comment and record it"),
            (PREFIX + "/agenda-suggestions/resolve", self.resolve_suggestion, ["POST"],
             "Close one pending agenda suggestion with its calendar receipt"),
        )

    async def status(self):
        return await self._run_read(
            lambda session, runtime, access, service, at, username, _cursor: access.status(
                self._operator_for(runtime, username, at), at))

    async def configure(self):
        command, failed = await self._parse_command()
        if failed is not None:
            return failed
        return await self._run(
            lambda access, service, at, operator: asyncio.to_thread(access.configure, operator, command, at),
            action="capability.consent.set",
            pass_operator=True,
        )

    async def feed(self):
        try:
            query = request.query if hasattr(request, "query") else {}
            scope = str(query.get("scope") or "friends")
            raw_limit = query.get("limit")
            detail = str(query.get("detail") or "").lower() in ("1", "true", "yes")
        except RuntimeError:
            return await self._failed("dashboard_identity_required", "sign in to the AstrBot Dashboard")

        async def read_feed(access, service, at):
            if scope not in ("friends", "own"):
                raise TaskError("qzone_request_invalid", "刷空间只支持 friends 或 own")
            try:
                limit = int(raw_limit or 0) or None
            except (TypeError, ValueError):
                raise TaskError("qzone_request_invalid", "invalid scan limit") from None
            return await service.scan(access.context, scope=scope, limit=limit, detail=detail)
        return await self._run(read_feed, action="capability.view")

    async def like(self):
        command, failed = await self._parse_command()
        if failed is not None:
            return failed
        return await self._run(lambda access, service, at: service.like(
            access.context, post_ref=str(command.get("post_ref") or ""),
            operation_key=_operation_key(command)))

    async def comment(self):
        command, failed = await self._parse_command()
        if failed is not None:
            return failed
        return await self._run(lambda access, service, at: service.comment(
            access.context, post_ref=str(command.get("post_ref") or ""),
            text=str(command.get("text") or ""), operation_key=_operation_key(command)))

    async def publish(self):
        command, failed = await self._parse_command(max_bytes=MAX_PUBLISH_BODY, error_message="发布参数无效")
        if failed is not None:
            return failed

        async def publish(access, service, at):
            context = access.context if access is not None else self._context()
            # An authenticated operator is the human in the loop: choosing the topic
            # and scene on the page *is* the confirmation. A model call cannot set
            # this flag (see the tool port), so auto mode stays the only model path.
            refs = [str(item) for item in (command.get("source_refs") or ())]
            candidate_id = str(command.get("share_candidate_id") or "").strip()
            image_id = str(command.get("share_image_id") or "").strip()
            image_ids = command.get("image_ids", [])
            if not isinstance(image_ids, list) or (image_id and image_ids):
                raise TaskError("qzone_request_invalid", "相册选图参数无效")
            if candidate_id and (image_id or image_ids):
                raise TaskError("qzone_request_invalid", "引用相册图只能用 share_image_id 或 share_candidate_id 之一")
            uploads = command.get("images", [])
            if not isinstance(uploads, list) or len(uploads) > 9:
                raise TaskError("qzone_image_invalid", "每条说说最多选择 9 张图片")
            images = []
            app = (
                access.application
                if access is not None
                else (self.controller.session or self.controller.last_session).application
            )
            planner_runtime = access.runtime if access is not None else getattr(app, "runtime", None)
            planner = self._planner(planner_runtime) if planner_runtime is not None else self._planner()
            for upload in uploads:
                if not isinstance(upload, dict) or set(upload) - {"data_url", "name", "image_id"}:
                    raise TaskError("qzone_image_invalid", "图片参数无效")
                if upload.get("image_id"):
                    if upload.get("data_url"):
                        raise TaskError("qzone_image_invalid", "单张图片不能同时指定相册与本地文件")
                    selected, _ = album_images(app, context, at, [upload["image_id"]], planner=planner)
                    images.extend(selected)
                else:
                    images.append(PublishImage.from_data_url(upload.get("data_url")))
            if candidate_id:
                images.append(candidate_image(app, context, at, candidate_id, planner))
            elif image_id or image_ids:
                selected, candidates = album_images(
                    app, context, at, [image_id] if image_id else image_ids, planner=planner
                )
                images.extend(selected)
                candidate_id = candidates[0] if len(candidates) == 1 else ""
            images = image_batch(images)
            refs.extend(image.source_id for image in images if image.source_id)
            receipt = await service.publish(
                context, text=str(command.get("text") or ""),
                topic=str(command.get("topic") or ""), scene=str(command.get("scene") or ""),
                confidence=command.get("confidence", 1.0), topic_confirmed=True,
                source_refs=tuple(refs),
                images=images,
                agenda_proposal=command.get("agenda_proposal"),
                operation_key=_operation_key(command))
            return {"candidate_id": candidate_id, "source_refs": tuple(refs), "receipt": receipt}
        return await self._run(publish)

    async def album(self):
        def read_album(session, runtime, access, service, at, username, cursor):
            from companion_v2.references.management import ReferenceMaterialManagement
            return ReferenceMaterialManagement(session.application).album(
                self._operator_for(runtime, username, at), session.profile.target, at=at, cursor=cursor)
        return await self._run_read(read_album, include_cursor=True)

    async def refresh(self):
        async def refresh_credentials(access, service, at, operator):
            # Authorization already succeeded inside ``_run`` before any credential refresh.
            await service.transport.credential(refresh=True)
            return await asyncio.to_thread(access.status, operator, at)
        return await self._run(refresh_credentials, pass_operator=True)

    async def resolve_suggestion(self):
        """Close one pending agenda suggestion.

        Marking it ``applied`` requires the calendar operation that actually
        changed the agenda: the write itself belongs to the calendar domain, so
        the page (or the model, through the calendar tool) performs it there and
        reports the receipt here. ``dismissed`` needs no receipt.
        """
        command, failed = await self._parse_command()
        if failed is not None:
            return failed

        async def resolve(access, service, at):
            state = str(command.get("state") or "").strip()
            suggestion_id = str(command.get("suggestion_id") or "").strip()
            if not suggestion_id:
                raise TaskError("qzone_request_invalid", "必须先指定要处理的日程建议")
            return await service.resolve_agenda_suggestion(
                access.context, suggestion_id=suggestion_id, state=state,
                calendar_operation_id=str(command.get("calendar_operation_id") or ""),
                result_refs=command.get("result_refs") or (), operation_key=_operation_key(command))
        return await self._run(resolve)

    async def scan_comments(self):
        """评论收件箱这一轮该回哪几条——只读，不发送、不调模型。"""
        async def scan(access, service, at):
            context = access.context
            scanner, owner = await self._scanner(service, context, runtime=access.runtime)
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
        return await self._run(scan, action="capability.view")

    async def reply_comment(self):
        """回一条扫到的评论，并把它记进账本。

        候选**在宿主这边按请求字段重新折叠**（``candidate_for``），所以回复用的键永远由
        评论身份决定，页面伪造不了一个能绕过"同键拒重放"的键。
        """
        command, failed = await self._parse_command()
        if failed is not None:
            return failed

        async def reply(access, service, at):
            context = access.context
            scanner, owner = await self._scanner(service, context, runtime=access.runtime)
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

    @staticmethod
    def _request_username():
        try:
            return request.username
        except RuntimeError:
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard") from None

    @staticmethod
    def _operator_for(runtime, username, at):
        if type(username) is not str or not username.strip() or username.startswith("api_key:"):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        return dashboard_operator(runtime, username, at=at)

    def _authorize_operator(self, runtime, access, username, at, *, action="memory.manage"):
        operator = self._operator_for(runtime, username, at)
        require(ObjectAccessPolicy(runtime).feature_access(
            operator,
            scope=ScopeRef(runtime.installation_id, "companion", ScopeKind.SYSTEM, "page.management"),
            action="memory.manage",
            observed_at=at,
        ))
        access._access(operator, at, action=action)
        return operator

    def _operator(self, at):
        return self._operator_for(self.controller.runtime, self._request_username(), at)

    def _context(self):
        session = self.controller.session or self.controller.last_session
        return session.profile.target

    async def _scanner(self, service, context, *, runtime=None):
        """扫描器要三样东西：缓存好的凭据（拿空间账号）、运行库、以及角色的账本归属。

        账号来自凭据本身（``uin``），不猜、也不允许页面指定——账本按账号分档，
        认错账号就会把别人的回复算到这个号头上。
        """
        from companion_v2.qzone.comment_scan import CommentScanner
        from companion_v2.qzone.inbox import CommentInbox
        from companion_v2.qzone.service import owner_key

        credential = await service.transport.credential()
        target_runtime = runtime if runtime is not None else self.controller.runtime
        scanner = CommentScanner(service=service, inbox=CommentInbox(target_runtime, now=self.controller.now),
                                 runtime=target_runtime, platform=context.scope.platform,
                                 login=str(credential.uin), now=self.controller.now)
        return scanner, owner_key(context)

    def _planner(self, runtime=None):
        """一个控制器一份候选目录：候选是"稍后发布"的凭据，必须跨请求还在。

        与空间动态引用同一套做法（进程内、进程重启即失效）；候选本身不是授权，
        发布前还会再复检一次源与素材。
        """
        target_runtime = runtime if runtime is not None else self.controller.runtime
        if self._share_planner is None or self._share_planner_runtime is not target_runtime:
            from companion_v2.sources.repositories import AssetRepository, SourceRepository
            from companion_v2.sharing.planner import ShareCatalog, SharePlanner

            self._share_planner = SharePlanner(sources=SourceRepository(target_runtime),
                                               assets=AssetRepository(target_runtime),
                                               catalog=ShareCatalog())
            self._share_planner_runtime = target_runtime
        return self._share_planner

    @staticmethod
    def _role_owner(context):
        from companion_v2.foundation.refs import RoleOwnerRef

        return RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                            context.role_binding.role.subject_id)

    async def _command(self, *, max_bytes=MAX_BODY_BYTES):
        raw = await request.body()
        if len(raw) > max_bytes:
            raise TaskError("qzone_request_invalid", "QQ Zone request is too large")
        command = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                             parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite value")))
        if type(command) is not dict:
            raise ValueError("command must be an object")
        return command

    async def _parse_command(self, *, max_bytes=MAX_BODY_BYTES, error_message="invalid QQ Zone request"):
        try:
            return await self._command(max_bytes=max_bytes), None
        except TaskError as error:
            return None, await self._failed(error.code, str(error))
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            return None, await self._failed("qzone_request_invalid", error_message)

    async def _failed(self, code, reason):
        from .web_api import outcome_response
        return outcome_response(Outcome.failed(code, reason))

    async def _run_read(self, call, *, include_cursor=False):
        from .web_api import outcome_response
        controller = self.controller
        try:
            username = self._request_username()
            cursor = request.query.get("cursor") if include_cursor else None
            async with controller._control_lock:
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "the V2 host has stopped")
                session = controller.session or controller.last_session
                runtime = controller.runtime
                if session is None or runtime is None:
                    raise TaskError("staging_not_initialized", "load the explicit V2 profile first")
                service = getattr(session.application, "qzone", None)
                if service is None:
                    raise TaskError("qzone_unavailable", "QQ 空间扩展没有可用的宿主通道")
                at = controller.now()
                access = QzoneAccess(session.application, session.profile.target, service=service)
            result = await asyncio.to_thread(call, session, runtime, access, service, at, username, cursor)
            return outcome_response(Outcome.success(result))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except StorageOpenError:
            return outcome_response(Outcome.failed("qzone_storage_unavailable", "the QQ Zone store is not available"))
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            return outcome_response(Outcome.failed("qzone_request_invalid", "invalid QQ Zone request"))

    async def _run(self, call, *, action="memory.manage", pass_operator=False):
        from .web_api import outcome_response
        controller = self.controller
        try:
            username = self._request_username()
            async with controller._control_lock:
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "the V2 host has stopped")
                session = controller.session or controller.last_session
                runtime = controller.runtime
                if session is None or runtime is None:
                    raise TaskError("staging_not_initialized", "load the explicit V2 profile first")
                service = getattr(session.application, "qzone", None)
                if service is None:
                    raise TaskError("qzone_unavailable", "QQ 空间扩展没有可用的宿主通道")
                at = controller.now()
                # The credential conclusion travels with ``QzoneService.status`` so
                # it is read when the status is built; capturing a snapshot here
                # would make "refresh then report" answer with the old state.
                access = QzoneAccess(session.application, session.profile.target, service=service)
            operator = await asyncio.to_thread(
                self._authorize_operator, runtime, access, username, at, action=action
            )
            result = call(access, service, at, operator) if pass_operator else call(access, service, at)
            if inspect.isawaitable(result):
                result = await result
            return outcome_response(Outcome.success(result))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except StorageOpenError:
            return outcome_response(Outcome.failed("qzone_storage_unavailable", "the QQ Zone store is not available"))
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            return outcome_response(Outcome.failed("qzone_request_invalid", "invalid QQ Zone request"))


__all__ = ["PREFIX", "QzoneWebAPI"]
