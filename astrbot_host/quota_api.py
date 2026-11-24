"""Dashboard HTTP API handlers for the multi-station quota workbench."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from astrbot.api.web import request

from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.model_gateway.quota import QuotaSettingsError

from .quota_service import get_quota_watch_service
from .service_balance import configured_balance_port, propagate_balance_port

MAX_BODY_BYTES = 65_536


def _parse_json_body(raw: bytes, *, limit: int = MAX_BODY_BYTES) -> dict[str, Any]:
    if len(raw) > limit:
        raise ValueError("request body too large")

    def _unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate JSON key")
            out[key] = value
        return out

    def _reject_nonfinite(_val):
        raise ValueError("nonfinite JSON value")

    text = raw.decode("utf-8") if raw else "{}"
    parsed = json.loads(text, object_pairs_hook=_unique, parse_constant=_reject_nonfinite)
    if type(parsed) is not dict:
        raise ValueError("JSON object required")
    return parsed


class QuotaWebEndpoints:
    """Delegate mounted onto ModelWebAPI for `/astrbot_plugin_private_companion/model/quota/*`."""

    def __init__(self, model_api: Any):
        self.model_api = model_api

    @property
    def controller(self) -> Any:
        return self.model_api.controller

    def routes(self, prefix: str) -> list[tuple[str, Any, list[str], str]]:
        return [
            (prefix + "/quota", self.dashboard, ["GET"], "Read multi-station quota dashboard, snapshots, FX summary, and awareness policy"),
            (prefix + "/quota/adapters", self.adapters, ["GET"], "Read registered quota protocol adapters and dynamic field schemas"),
            (prefix + "/quota/detect", self.detect, ["POST"], "Auto-detect station type and required fields from URL or AstrBot provider"),
            (prefix + "/quota/test", self.test_draft, ["POST"], "Test a draft station configuration without persisting"),
            (prefix + "/quota/exchange-token", self.exchange_token, ["POST"], "One-time login to exchange NewAPI credentials for an access token and UID"),
            (prefix + "/quota/stations", self.save_station, ["POST"], "Create or update one monitored quota station"),
            (prefix + "/quota/stations/delete", self.delete_station, ["POST"], "Delete one monitored quota station"),
            (prefix + "/quota/refresh", self.refresh, ["POST"], "Refresh all or one monitored quota station"),
            (prefix + "/quota/awareness", self.save_awareness, ["POST"], "Save multi-station balance awareness aggregation policy"),
            (prefix + "/quota/icon", self.icon, ["GET"], "Read cached same-origin station logo data URL for one station"),
            (prefix + "/quota/upstream-logs", self.upstream_logs, ["GET"], "Read-only upstream NewAPI consumption logs for reconciliation"),
        ]

    @staticmethod
    def _response(outcome: Outcome):
        from .web_api import outcome_response

        return outcome_response(outcome)

    async def _authorize(self, *, allow_query_keys: tuple[str, ...] = ()):
        return await self.model_api._run_authorized_read(allow_query_keys=allow_query_keys)

    def _service(self, session: Any):
        runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
        return get_quota_watch_service(self.controller, Path(runtime.data_root)), runtime

    def _sync_live_balance_port(self, runtime: Any) -> None:
        base = getattr(self.controller, "controller", self.controller)
        try:
            port = configured_balance_port(
                getattr(base, "config", None),
                context=getattr(base, "context", None),
                runtime_root=Path(runtime.data_root),
                controller=base,
            )
        except Exception:
            port = None
        propagate_balance_port(base, port)

    async def dashboard(self):
        try:
            session, _op, _mgr, _status = await self._authorize()
            service, runtime = self._service(session)
            payload = await asyncio.to_thread(service.get_dashboard_payload, runtime=runtime, session=session)
            service.maybe_schedule_background_refresh(session=session)
            return self._response(Outcome.success(payload))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except QuotaSettingsError as error:
            return self._response(Outcome.failed(error.code, error.reason))
        except (ValueError, TypeError, KeyError):
            return self._response(Outcome.failed("quota_request_invalid", "无法读取额度看板数据"))

    async def adapters(self):
        try:
            session, _op, _mgr, _status = await self._authorize()
            service, _runtime = self._service(session)
            return self._response(Outcome.success({"adapters": service.registry.list_descriptors()}))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError):
            return self._response(Outcome.failed("quota_request_invalid", "无法读取站点协议清单"))

    async def detect(self):
        try:
            session, _op, _mgr, _status = await self._authorize()
            body = _parse_json_body(await request.body(), limit=16_384)
            allowed = {"base_url", "astrbot_provider_id", "allow_http", "proxy_url"}
            if set(body) - allowed:
                raise QuotaSettingsError("quota_request_invalid", "站点探测请求包含不支持的字段")
            service, _runtime = self._service(session)
            result = await service.detect(
                base_url=str(body.get("base_url") or ""),
                astrbot_provider_id=str(body.get("astrbot_provider_id") or ""),
                allow_http=bool(body.get("allow_http", False)),
                proxy_url=str(body.get("proxy_url") or ""),
                session=session,
            )
            return self._response(Outcome.success(result))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except QuotaSettingsError as error:
            return self._response(Outcome.failed(error.code, error.reason))
        except (ValueError, TypeError, json.JSONDecodeError):
            return self._response(Outcome.failed("quota_request_invalid", "请检查站点识别参数"))

    async def test_draft(self):
        try:
            session, _op, _mgr, _status = await self._authorize()
            body = _parse_json_body(await request.body(), limit=MAX_BODY_BYTES)
            station_payload = body.get("station") if "station" in body and type(body.get("station")) is dict else body
            service, _runtime = self._service(session)
            result = await service.test_draft(station_payload, session=session)
            return self._response(Outcome.success(result))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except QuotaSettingsError as error:
            return self._response(Outcome.failed(error.code, error.reason))
        except (ValueError, TypeError, json.JSONDecodeError):
            return self._response(Outcome.failed("quota_request_invalid", "请检查站点测试配置"))

    async def exchange_token(self):
        try:
            session, _op, _mgr, _status = await self._authorize()
            body = _parse_json_body(await request.body(), limit=16_384)
            allowed = {
                "base_url",
                "astrbot_provider_id",
                "username",
                "password",
                "user_id_header",
                "allow_http",
                "proxy_url",
            }
            if set(body) - allowed:
                raise QuotaSettingsError("quota_request_invalid", "登录换取令牌请求包含不支持的字段")
            service, _runtime = self._service(session)
            result = await service.exchange_token(
                base_url=str(body.get("base_url") or ""),
                astrbot_provider_id=str(body.get("astrbot_provider_id") or ""),
                username=str(body.get("username") or ""),
                password=str(body.get("password") or ""),
                user_id_header=str(body.get("user_id_header") or "New-Api-User"),
                allow_http=bool(body.get("allow_http", False)),
                proxy_url=str(body.get("proxy_url") or ""),
                session=session,
            )
            return self._response(Outcome.success(result))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except QuotaSettingsError as error:
            return self._response(Outcome.failed(error.code, error.reason))
        except (ValueError, TypeError, json.JSONDecodeError):
            return self._response(Outcome.failed("quota_request_invalid", "请检查登录换取令牌参数"))

    async def save_station(self):
        try:
            session, _op, _mgr, _status = await self._authorize()
            body = _parse_json_body(await request.body(), limit=MAX_BODY_BYTES)
            if set(body) - {"edit_token", "station", "probe_after_save"} or "edit_token" not in body or "station" not in body:
                raise QuotaSettingsError("quota_request_invalid", "保存站点请求缺少 edit_token 或 station 字段")
            service, runtime = self._service(session)
            spec, _stations, _awareness, _token = await asyncio.to_thread(
                service.upsert_station,
                body["edit_token"],
                body["station"],
            )
            service.invalidate_upstream_logs(spec.id)
            self._sync_live_balance_port(runtime)
            if body.get("probe_after_save", True) and spec.enabled:
                try:
                    await asyncio.wait_for(service.refresh_station(spec.id, session=session), timeout=12.0)
                except Exception:
                    pass
            payload = await asyncio.to_thread(service.get_dashboard_payload, runtime=runtime, session=session)
            return self._response(Outcome.success({"saved": True, "station_id": spec.id, **payload}))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except QuotaSettingsError as error:
            return self._response(Outcome.failed(error.code, error.reason))
        except (ValueError, TypeError, json.JSONDecodeError):
            return self._response(Outcome.failed("quota_request_invalid", "站点配置未通过校验"))

    async def delete_station(self):
        try:
            session, _op, _mgr, _status = await self._authorize()
            body = _parse_json_body(await request.body(), limit=4096)
            if set(body) != {"edit_token", "station_id"}:
                raise QuotaSettingsError("quota_request_invalid", "删除站点请求参数无效")
            service, runtime = self._service(session)
            sid = str(body.get("station_id") or "").strip()
            if not service.store.exists() and any(st.id == sid for st in service.legacy_seed_stations()):
                raise QuotaSettingsError(
                    "quota_legacy_readonly",
                    "旧版单站投影请前往「感知 → 余额感知」移除，或先编辑保存迁移后再管理",
                )
            await asyncio.to_thread(service.store.delete_station, body["edit_token"], sid)
            service.snapshot_cache.delete(sid)
            service.logo_cache.forget(sid)
            service.invalidate_upstream_logs(sid)
            self._sync_live_balance_port(runtime)
            payload = await asyncio.to_thread(service.get_dashboard_payload, runtime=runtime, session=session)
            return self._response(Outcome.success({"deleted": True, "station_id": sid, **payload}))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except QuotaSettingsError as error:
            return self._response(Outcome.failed(error.code, error.reason))
        except (ValueError, TypeError, json.JSONDecodeError):
            return self._response(Outcome.failed("quota_request_invalid", "删除站点请求无效"))

    async def refresh(self):
        try:
            session, _op, _mgr, _status = await self._authorize()
            body = _parse_json_body(await request.body(), limit=4096)
            if set(body) - {"station_id", "force"}:
                raise QuotaSettingsError("quota_request_invalid", "刷新请求包含不支持的字段")
            service, runtime = self._service(session)
            sid = str(body.get("station_id") or "").strip()
            force = bool(body.get("force", True))
            if sid:
                await service.refresh_station(sid, session=session)
            else:
                await service.refresh_all(force=force, session=session)
            payload = await asyncio.to_thread(service.get_dashboard_payload, runtime=runtime, session=session)
            return self._response(Outcome.success({"refreshed": True, "station_id": sid, **payload}))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except QuotaSettingsError as error:
            return self._response(Outcome.failed(error.code, error.reason))
        except (ValueError, TypeError, json.JSONDecodeError):
            return self._response(Outcome.failed("quota_request_invalid", "刷新站点失败"))

    async def save_awareness(self):
        try:
            session, _op, _mgr, _status = await self._authorize()
            body = _parse_json_body(await request.body(), limit=8192)
            if set(body) != {"edit_token", "awareness"} or type(body.get("awareness")) is not dict:
                raise QuotaSettingsError("quota_request_invalid", "余额感知策略请求参数无效")
            service, runtime = self._service(session)
            await asyncio.to_thread(service.update_awareness, body["edit_token"], body["awareness"])
            self._sync_live_balance_port(runtime)
            payload = await asyncio.to_thread(service.get_dashboard_payload, runtime=runtime, session=session)
            return self._response(Outcome.success({"saved": True, **payload}))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except QuotaSettingsError as error:
            return self._response(Outcome.failed(error.code, error.reason))
        except (ValueError, TypeError, json.JSONDecodeError):
            return self._response(Outcome.failed("quota_request_invalid", "保存余额感知策略失败"))

    async def icon(self):
        try:
            session, _op, _mgr, _status = await self._authorize(allow_query_keys=("id", "v"))
            station_id = str(request.query.get("id") or "").strip()
            service, _runtime = self._service(session)
            data = service.get_icon_payload(station_id)
            return self._response(Outcome.success(data))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except QuotaSettingsError as error:
            return self._response(Outcome.failed(error.code, error.reason))
        except (ValueError, TypeError):
            return self._response(Outcome.failed("quota_request_invalid", "无法读取站点头像"))

    async def upstream_logs(self):
        try:
            session, _op, _mgr, _status = await self._authorize(
                allow_query_keys=("station_id", "days", "token_name", "force")
            )
            station_id = str(request.query.get("station_id") or "").strip()
            raw_days = str(request.query.get("days") or "7").strip()
            try:
                days = int(raw_days) if raw_days else 7
            except ValueError as exc:
                raise QuotaSettingsError("quota_request_invalid", "天数参数必须是整数") from exc
            token_name = str(request.query.get("token_name") or "").strip()
            force = str(request.query.get("force") or "").strip().lower() in ("1", "true", "yes")
            service, _runtime = self._service(session)
            data = await service.get_upstream_logs(
                station_id=station_id,
                days=days,
                token_name=token_name,
                force=force,
                session=session,
            )
            return self._response(Outcome.success(data))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except QuotaSettingsError as error:
            return self._response(Outcome.failed(error.code, error.reason))
        except (ValueError, TypeError):
            return self._response(Outcome.failed("quota_request_invalid", "无法读取上游消费日志对照"))
