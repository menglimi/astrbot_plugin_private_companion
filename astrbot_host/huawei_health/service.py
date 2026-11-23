"""Huawei Health account binding and read-only health summary service."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .vendor.hw_auth import (
    HuaweiAuth, HuaweiAuthError, MIN_COOKIE_SPECS, compute_expire_at,
)
from .vendor.hw_client import (
    AuthExpired, HuaweiHealthClient, HuaweiHealthError, format_distance_m,
    format_duration_s,
)
from .vendor.hw_credentials import HuaweiCredentialsStore, harden_dir_permissions
from .vendor.hw_login import HuaweiLoginManager, LoginFlowError
from .vendor.hw_store import HealthStore


DEFAULT_CLIENT_ID = "106533743"
DEFAULT_VERSION = "HealthKitRunningGroupPortal_6.26.4.200"
DEFAULT_TIMEZONE = "+0800"
DEFAULT_CONFIG = {
    "cookie_json": "",
    "cookie_CASTGC": "",
    "cookie_sid": "",
    "cookie_hwid_cas_sid": "",
    "cookie_HuaweiID_CAS_ISCASLOGIN": "",
    "cookie_CASLOGINSITE": "",
    "cookie_LOGINACCSITE": "",
    "browser_path": "",
    "headless": True,
    "page_timeout": 90,
    "token_ttl": 3000,
    "data_ttl": 600,
    "warmup": False,
    "login_daily_limit": 3,
    "timezone": DEFAULT_TIMEZONE,
    "client_id": DEFAULT_CLIENT_ID,
    "api_version": DEFAULT_VERSION,
    "bound_subject_id": "",
    "bound_role_id": "",
}


def _int_cfg(value, default: int, lo: int | None = None, hi: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if lo is not None:
        result = max(lo, result)
    if hi is not None:
        result = min(hi, result)
    return result


class HealthConfig(dict):
    """Small persistent JSON config with the ``get`` contract used by vendor code."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            saved = {}
        super().__init__({**DEFAULT_CONFIG, **(saved if isinstance(saved, dict) else {})})

    def save_config(self) -> bool:
        payload = json.dumps(self, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        temporary = self.path.with_name(".config-" + os.urandom(8).hex() + ".tmp")
        try:
            temporary.write_text(payload, encoding="utf-8", newline="\n")
            temporary.replace(self.path)
            return True
        finally:
            temporary.unlink(missing_ok=True)


class HuaweiHealthService:
    """Owns one user-authorized Huawei Health cloud account per installation."""

    def __init__(self, data_dir, *, logger):
        self.data_dir = str(Path(data_dir))
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        self.logger = logger
        self.config = HealthConfig(Path(self.data_dir) / "config.json")
        self.cookie_file = os.path.join(self.data_dir, "hw_cookies.json")
        self.db_path = os.path.join(self.data_dir, "health.db")
        self.latest_path = Path(self.data_dir) / "latest_health.json"
        self.store = HealthStore(self.db_path)
        self.credentials = HuaweiCredentialsStore(self.data_dir)
        self.login_manager = HuaweiLoginManager(self)
        self._lock = asyncio.Lock()
        self._cache_inflight: dict[str, asyncio.Task] = {}
        self._cache_generation: dict[str, int] = {}
        try:
            saved = json.loads(self.latest_path.read_text(encoding="utf-8"))
            self._latest = saved if isinstance(saved, dict) else None
        except (FileNotFoundError, OSError, ValueError, TypeError):
            self._latest = None
        self._refresh_task: asyncio.Task | None = None
        self._initialized = False
        self._closed = False

    @property
    def auth(self) -> HuaweiAuth:
        return HuaweiAuth(
            cookie_file=self.cookie_file,
            profile_dir=os.path.join(self.data_dir, "browser_profile"),
            browser_path=(self.config.get("browser_path") or "").strip(),
            headless=bool(self.config.get("headless", True)),
            page_timeout=_int_cfg(self.config.get("page_timeout"), 90, 1, 600),
            logger=self.logger,
        )

    async def initialize(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        try:
            await asyncio.to_thread(harden_dir_permissions, self.data_dir)
        except Exception:
            pass
        raw = str(self.config.get("cookie_json") or "").strip()
        fields = {name: self.config.get("cookie_" + name, "")
                  for name, _, _ in MIN_COOKIE_SPECS}
        if raw or any(str(value or "").strip() for value in fields.values()):
            try:
                await asyncio.to_thread(self.auth.import_cookie_sources, raw, fields, True)
            except Exception as error:
                self.logger.warning("[HuaweiHealth] 配置 cookie 导入失败 error_type=%s",
                                    type(error).__name__)

    def _err(self, exc: Exception) -> str:
        if isinstance(exc, HuaweiHealthError):
            return str(exc)
        return "取数失败：%s" % type(exc).__name__

    async def _password_token(self) -> str:
        manager = self.login_manager
        try:
            await asyncio.to_thread(lambda: manager.start("auto", manual=False))
        except LoginFlowError as error:
            labels = {
                "credentials_missing": "尚未保存账号密码，请先在感知页的华为健康卡保存凭据。",
                "credentials_corrupt": "账号密码凭据已损坏，请重新保存。",
                "credentials_locked": "账号密码凭据被占用或无权读取，请稍后重试。",
                "credentials_unavailable": "当前系统不支持安全凭据登录，请使用 Cookie 方式。",
                "daily_limit": "今日自动登录失败次数已达上限，请稍后再试。",
                "cooldown": "自动登录失败冷却中，请稍后再试。",
            }
            raise HuaweiAuthError(labels.get(error.code, "自动登录无法启动。")) from error
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            status = manager.status()
            state = status.get("state") or "IDLE"
            if state == "DONE":
                cached = self.store.get("token") or {}
                if cached.get("token"):
                    return cached["token"]
                raise HuaweiAuthError("自动登录完成但未拿到有效 token。")
            if state == "FAILED":
                raise HuaweiAuthError(status.get("message") or "自动登录失败。")
            if state in ("NEED_SMS", "NEED_CRED", "NEED_TRUST"):
                raise HuaweiAuthError("自动登录需要你在感知页的华为健康卡继续完成验证。")
            await asyncio.sleep(0.5)
        try:
            await asyncio.to_thread(manager.cancel)
        except Exception:
            pass
        raise HuaweiAuthError("自动登录等待超时，已取消本次会话。")

    async def _token(self, force: bool = False, stale_token: str = "") -> str:
        if not force:
            cached = self.store.get("token") or {}
            if cached.get("token") and cached.get("expire_at", 0) > time.time():
                return cached["token"]
        async with self._lock:
            cached = self.store.get("token") or {}
            if cached.get("token") and cached.get("expire_at", 0) > time.time():
                if not force or (stale_token and cached.get("token") != stale_token):
                    return cached["token"]
            mode = getattr(self.login_manager, "login_mode", "cookie")
            auth = self.auth
            if mode == "password":
                try:
                    token = await self._password_token()
                except HuaweiAuthError:
                    if not os.path.exists(self.cookie_file):
                        raise
                    token = await asyncio.to_thread(auth.fetch_token)
            else:
                token = await asyncio.to_thread(auth.fetch_token)
            ttl = _int_cfg(self.config.get("token_ttl"), 3000, 0, 86400)
            stored = self.store.get("token") or {}
            if mode == "password" and stored.get("token") == token and stored.get("expire_at"):
                expire_at = float(stored["expire_at"])
            else:
                expire_at = compute_expire_at(getattr(auth, "token_expire_at", 0.0), ttl, time.time())
            self.store.set("token", {
                "token": token, "expire_at": expire_at,
                "obtained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            return token

    async def _client(self, force: bool = False, stale_token: str = "") -> HuaweiHealthClient:
        token = await self._token(force=force, stale_token=stale_token)
        return HuaweiHealthClient(
            token,
            client_id=str(self.config.get("client_id") or DEFAULT_CLIENT_ID),
            version=str(self.config.get("api_version") or DEFAULT_VERSION),
            timezone=str(self.config.get("timezone") or DEFAULT_TIMEZONE),
        )

    async def _cached(self, key: str, producer, ttl: int | None = None, force: bool = False):
        if not force:
            hit = self.store.get(key)
            if hit is not None or self.store.has(key):
                return hit
        ttl = _int_cfg(self.config.get("data_ttl"), 600, 0, 86400) if ttl is None else ttl
        if _int_cfg(self.config.get("data_ttl"), 600, 0, 86400) <= 0:
            ttl = 0
        task = self._cache_inflight.get(key)
        if task is None or task.done():
            generation = self._cache_generation.get(key, 0)

            async def produce():
                used_token = ""
                async def run(force_auth: bool, stale_token: str = ""):
                    nonlocal used_token
                    client = await self._client(force=force_auth, stale_token=stale_token)
                    used_token = client.token
                    return await asyncio.to_thread(producer, client)
                try:
                    try:
                        result = await run(False)
                    except AuthExpired:
                        result = await run(True, stale_token=used_token)
                    if self._cache_generation.get(key, 0) == generation and ttl > 0:
                        self.store.set(key, result, ttl=ttl)
                    return result
                finally:
                    if self._cache_inflight.get(key) is asyncio.current_task():
                        self._cache_inflight.pop(key, None)

            task = asyncio.create_task(produce())
            self._cache_inflight[key] = task
        return await asyncio.shield(task)

    def clear_health_cache(self) -> None:
        """Synchronous cache reset for the login worker thread; keeps the token row."""
        self._latest = None
        for key in list(self._cache_generation):
            self._cache_generation[key] = self._cache_generation.get(key, 0) + 1
        try:
            self.store.clear_except("token")
        except Exception:
            pass

    async def invalidate_auth_cache(self) -> None:
        for task in list(self._cache_inflight.values()):
            task.cancel()
        self._cache_inflight.clear()
        for key in list(self._cache_generation):
            self._cache_generation[key] = self._cache_generation.get(key, 0) + 1
        try:
            self.store.clear_except("token")
        except Exception:
            pass
        try:
            self.store.delete("token")
        except Exception:
            pass
        self._latest = None

    async def import_cookie(self, text: str = "", fields: dict | None = None) -> int:
        count = await asyncio.to_thread(self.auth.import_cookie_sources, text or "", fields or {})
        self.login_manager.set_login_mode("cookie")
        await self.invalidate_auth_cache()
        return count

    async def clear_cookie(self) -> None:
        await asyncio.to_thread(self.auth.clear_cookies)
        await self.invalidate_auth_cache()

    async def save_credentials(self, account: str, password: str) -> dict:
        account, password = str(account or "").strip(), str(password or "")
        if not account or not password:
            raise ValueError("账号和密码都不能为空")
        result = await asyncio.to_thread(self.credentials.save, account, password)
        self.login_manager.set_login_mode("password")
        return result

    async def clear_credentials(self) -> bool:
        result = await asyncio.to_thread(self.credentials.clear)
        self.login_manager.set_login_mode("cookie")
        return int(result or 0) > 0

    async def start_login(self, *, auto_trust: bool = True, prefer_auto: bool = False,
                          visible_fallback: bool = False) -> dict:
        return await asyncio.to_thread(self.login_manager.start, "auto" if prefer_auto else "manual",
            auto_trust, visible_fallback, True, prefer_auto)

    async def login_status(self) -> dict:
        return self.login_manager.status()

    async def submit_sms(self, code: str) -> dict:
        return await asyncio.to_thread(self.login_manager.submit_sms, str(code or "").strip())

    async def submit_trust(self, choice: str) -> dict:
        return await asyncio.to_thread(self.login_manager.submit_trust, str(choice or "later"))

    async def cancel_login(self) -> dict:
        return await asyncio.to_thread(self.login_manager.cancel)

    def _stat_line(self, stats: dict, count_label: str = "条") -> str:
        if not stats:
            return "无数据"
        parts = []
        for key, label in (("avg", "均"), ("min", "最低"), ("max", "最高")):
            if stats.get(key) is not None:
                parts.append("%s%s" % (label, stats[key]))
        if stats.get("count") is not None:
            parts.append("%s %s" % (stats["count"], count_label))
        return " / ".join(parts) if parts else "无数据"

    def _metric_text(self, parsed: dict, unit: str, days: int, name: str) -> str:
        overall = parsed.get("overall") or {}
        daily = parsed.get("daily") or []
        if not overall and not daily:
            return "最近 %d 天没有 %s 数据。" % (days, name)
        lines = []
        for field, stats in overall.items():
            lines.append("%s：%s" % (field, self._stat_line(stats, "天")))
        if daily:
            lines.append("按天：" + "；".join(
                "%s %s" % (item.get("day"), item.get("avg", item.get("max", "-")))
                for item in daily[-7:] if isinstance(item, dict)))
        return "\n".join(lines)

    def _stress_text(self, stats: dict, days: int) -> str:
        overall = (stats or {}).get("overall") if isinstance(stats, dict) else None
        daily = (stats or {}).get("daily") if isinstance(stats, dict) else None
        values = overall.get("stressScore") if isinstance(overall, dict) else None
        if not isinstance(values, dict):
            return "最近 %d 天没有压力记录。" % days
        line = "均%s / 最低%s / 最高%s（%s 次采样）" % (
            values.get("avg", "-"), values.get("min", "-"), values.get("max", "-"),
            values.get("count", "-"))
        if isinstance(daily, list) and daily:
            line += "\n按天：" + "；".join(
                "%s %s" % (item.get("day"), (item.get("stressScore") or {}).get("avg", "-"))
                for item in daily[-7:] if isinstance(item, dict))
        return line

    def _performance_text(self, data: dict, days: int = 7) -> str:
        if not isinstance(data, dict) or not data:
            return ""
        bits = []
        for key, label in (("runningAbility", "跑力"), ("condition", "状态"),
                           ("fitness", "体能"), ("fatigue", "疲劳")):
            if data.get(key) is not None:
                bits.append("%s %s" % (label, round(float(data[key]), 1)))
        times = data.get("predictedTimes") or {}
        labels = {"km1": "1km", "km3": "3km", "km5": "5km", "km10": "10km",
                  "halfMarathon": "半马", "marathon": "全马"}
        predicted = []
        for key, label in labels.items():
            value = times.get(key) if isinstance(times, dict) else None
            if isinstance(value, (int, float)) and value > 0:
                predicted.append("%s %s" % (label, format_duration_s(value)))
        if predicted:
            bits.append("预测 " + " / ".join(predicted))
        return "；".join(bits)

    def _personal_bests_text(self, data: dict) -> str:
        reports = data.get("sportReports") if isinstance(data, dict) else None
        if not isinstance(reports, list) or not reports:
            return ""
        lines = []
        for report in reports:
            if not isinstance(report, dict):
                continue
            labels = {"bestRunDistance": "最远跑步", "bestRunPartTime5KM": "5km 最佳",
                      "bestRunPartTime3KM": "3km 最佳", "bestRunPace": "最佳配速",
                      "accumulatedRunDistance": "累计跑步"}
            for item in report.get("personalBest") or []:
                if not isinstance(item, dict) or item.get("value") is None:
                    continue
                name = labels.get(item.get("name"), item.get("name"))
                value = float(item["value"])
                if "Distance" in str(item.get("name")):
                    text = format_distance_m(value)
                elif "Pace" in str(item.get("name")):
                    text = "%s / km" % format_duration_s(value)
                elif "Time" in str(item.get("name")):
                    text = format_duration_s(value)
                else:
                    text = str(item["value"])
                lines.append("%s：%s" % (name, text))
            for item in report.get("accumulate") or []:
                if isinstance(item, dict) and item.get("name") == "accumulatedRunDistance" and item.get("value") is not None:
                    lines.append("累计跑步：%s" % format_distance_m(float(item["value"])))
        return "；".join(dict.fromkeys(lines))

    def _sleep_text(self, records: list, days: int) -> str:
        if not records:
            return "最近 %d 天没有睡眠记录。" % days
        lines = []
        for record in records[-7:]:
            values = {}
            for item in record.get("value") or []:
                if not isinstance(item, dict):
                    continue
                for key in ("floatValue", "integerValue", "longValue", "doubleValue"):
                    if item.get(key) is not None:
                        values[item.get("fieldName")] = item[key]
                        break
            start = record.get("startTime")
            try:
                day = datetime.datetime.fromtimestamp(int(start) / 1e9).strftime("%m-%d")
            except Exception:
                day = "?"
            bits = []
            for keys, label, unit in (
                (("all_sleep_time", "allSleepTime"), "总睡", "分"),
                (("sleep_score", "sleepScore"), "得分", ""),
                (("deep_sleep_time", "deepSleepTime"), "深睡", "分"),
                (("dream_time", "dreamTime"), "REM", "分"),
                (("awake_time", "awakeTime"), "清醒", "分"),
                (("wakeup_count", "wakeupCount"), "夜醒", "次"),
            ):
                value = next((values.get(key) for key in keys if values.get(key) is not None), None)
                if value is not None:
                    bits.append("%s %s%s" % (label, value, unit))
            lines.append("%s %s" % (day, "，".join(bits) or "有记录"))
        return "最近 30 天睡眠（最近 7 条）：\n" + "\n".join(lines)

    @staticmethod
    def _activity_metrics(summary: dict) -> dict:
        """Flatten Huawei's dataSummary blocks into the few numbers worth showing."""
        result = {}
        for block in (summary or {}).get("dataSummary") or []:
            if not isinstance(block, dict):
                continue
            kind = str(block.get("dataTypeName") or "")
            values = {}
            for item in block.get("value") or []:
                if not isinstance(item, dict):
                    continue
                for key in ("floatValue", "integerValue", "longValue", "doubleValue"):
                    if item.get(key) is not None:
                        values[item.get("fieldName")] = item[key]
                        break
            if kind.endswith("continuous.distance.total"):
                result["distance"] = values.get("distance")
            elif kind.endswith("continuous.calories.burnt.total"):
                result["calories"] = values.get("calories_total")
            elif kind.endswith("continuous.heart_rate.statistics"):
                if values.get("avg") is not None:
                    result["heart_rate"] = "%s/%s" % (values.get("avg"), values.get("max", "-"))
            elif kind.endswith("continuous.steps.total"):
                result["steps"] = values.get("steps")
        return result

    def _activities_text(self, activities: list, days: int, limit: int = 5) -> str:
        if not activities:
            return "最近 %d 天没有运动记录。" % days
        lines = []
        for item in activities[:limit]:
            when = item.get("startTime") or item.get("endTime") or ""
            try:
                when = time.strftime("%m-%d %H:%M", time.localtime(int(when) / 1000))
            except Exception:
                when = str(when)
            summary = item.get("activitySummary") if isinstance(item.get("activitySummary"), dict) else {}
            metrics = self._activity_metrics(summary)
            start, end = item.get("startTime"), item.get("endTime")
            if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
                metrics.setdefault("duration", (end - start) / 1000)
            distance, duration = metrics.get("distance"), metrics.get("duration")
            calories, heart = metrics.get("calories"), metrics.get("heart_rate")
            details = []
            if distance:
                details.append(format_distance_m(distance))
            if duration:
                details.append(format_duration_s(duration))
            if calories:
                details.append("%s 千卡" % round(float(calories)))
            if heart:
                details.append("心率 %s" % heart)
            lines.append("%s 运动类型%s %s" % (
                when, item.get("activityType", "-"), " · ".join(details) or "无摘要"))
        return "最近运动：\n" + "\n".join(lines)

    @staticmethod
    def _epoch_iso(value, *, nanoseconds=False):
        try:
            number = float(value)
            seconds = number / 1e9 if nanoseconds else (number / 1000 if number > 1e11 else number)
            return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            return ""

    @staticmethod
    def _daily_values(parsed):
        values = []
        for item in (parsed or {}).get("daily") or []:
            if not isinstance(item, dict):
                continue
            for field, stats in item.items():
                if field == "day" or not isinstance(stats, dict):
                    continue
                value = stats.get("avg", stats.get("max"))
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
                if value == value:
                    values.append(value)
                    break
        return values

    def _trend_event(self, resting_hr, hrv, now):
        hr_values = self._daily_values(resting_hr)
        hrv_values = self._daily_values(hrv)
        if len(hr_values) < 4 or len(hrv_values) < 4:
            return None
        recent_hr = sum(hr_values[-3:]) / 3
        baseline_hr = sum(hr_values[-6:-3]) / max(1, len(hr_values[-6:-3]))
        recent_hrv = sum(hrv_values[-3:]) / 3
        baseline_hrv = sum(hrv_values[-6:-3]) / max(1, len(hrv_values[-6:-3]))
        hr_change = ((recent_hr - baseline_hr) / baseline_hr * 100) if baseline_hr else 0
        hrv_change = ((recent_hrv - baseline_hrv) / baseline_hrv * 100) if baseline_hrv else 0
        if hr_change < 7 and hrv_change > -15:
            return None
        event_id = "recovery-" + hashlib.sha256(
            f"{now.date().isoformat()}:{round(hr_change, 1)}:{round(hrv_change, 1)}".encode()).hexdigest()[:20]
        return {
            "event_id": event_id,
            "kind": "recovery_trend",
            "occurred_at": now.isoformat(),
            "summary": "最近几天的恢复状态有些变化，可以作为关心或承接话题的背景，不要求主动提起。",
            "facts": {"resting_hr_change_percent": round(hr_change, 1),
                      "hrv_change_percent": round(hrv_change, 1)},
        }

    def _weekly_event(self, sleep_records, activities, now):
        sleep_count = len(sleep_records) if isinstance(sleep_records, list) else 0
        activity_count = len(activities) if isinstance(activities, list) else 0
        if sleep_count < 2 and activity_count < 1:
            return None
        event_id = "weekly-" + now.strftime("%G-W%V")
        return {
            "event_id": event_id,
            "kind": "weekly_review",
            "occurred_at": now.isoformat(),
            "summary": "最近一周有一些健康记录，可以作为一次轻松的共同回顾，也可以只当语气背景。",
            "facts": {"sleep_records": sleep_count, "workout_records": activity_count},
        }

    def _health_events(self, sleep_records, activities, resting_hr=None, hrv=None):
        sleep_records = sleep_records if isinstance(sleep_records, list) else []
        activities = activities if isinstance(activities, list) else []
        events = []
        for record in sleep_records[-7:]:
            if not isinstance(record, dict):
                continue
            start = self._epoch_iso(record.get("startTime"), nanoseconds=True)
            end = self._epoch_iso(record.get("endTime"), nanoseconds=True)
            if not end:
                continue
            values = {}
            for item in record.get("value") or []:
                if not isinstance(item, dict):
                    continue
                for key in ("floatValue", "integerValue", "longValue", "doubleValue"):
                    if item.get(key) is not None:
                        values[item.get("fieldName")] = item[key]
                        break
            total = next((values.get(key) for key in ("all_sleep_time", "allSleepTime")
                          if values.get(key) is not None), None)
            event_id = "sleep-" + hashlib.sha256(f"{start}:{end}:{total}".encode()).hexdigest()[:20]
            facts = {"started_at": start, "ended_at": end}
            if total is not None:
                facts["duration_minutes"] = round(float(total), 1)
            events.append({
                "event_id": event_id,
                "kind": "sleep_completed",
                "occurred_at": end,
                "summary": "昨晚的睡眠记录已经完成，适合作为一句自然问候或关心时的背景，不要求主动提起。",
                "facts": facts,
            })
        for item in activities[-10:]:
            if not isinstance(item, dict):
                continue
            started = self._epoch_iso(item.get("startTime"))
            ended = self._epoch_iso(item.get("endTime")) or started
            if not ended:
                continue
            activity_type = str(item.get("activityType") or "运动")
            distance = item.get("distance")
            duration = item.get("duration")
            facts = {"activity_type": activity_type}
            if isinstance(distance, (int, float)):
                facts["distance_km"] = round(float(distance) / 1000, 2)
            if isinstance(duration, (int, float)):
                facts["duration_minutes"] = round(float(duration) / 60000, 1)
            event_id = "workout-" + hashlib.sha256(
                f"{started}:{ended}:{activity_type}:{distance}".encode()).hexdigest()[:20]
            summary = "最近完成了一次运动，可以像熟人一样顺口问一句或分享自己的感受，不要求主动提起。"
            events.append({
                "event_id": event_id,
                "kind": "workout_completed",
                "occurred_at": ended,
                "summary": summary,
                "facts": facts,
            })
        now = datetime.datetime.now(datetime.timezone.utc)
        trend = self._trend_event(resting_hr, hrv, now)
        if trend is not None:
            events.append(trend)
        weekly = self._weekly_event(sleep_records, activities, now)
        if weekly is not None:
            events.append(weekly)
        return events

    async def fetch_today(self, *, force: bool = False) -> tuple[str, list[dict]]:
        parts, sleep_records, activities = [], [], []
        resting_hr = hrv_stats = None
        try:
            resting_hr = await self._cached("hr:7", lambda c: c.resting_heart_rate(7), force=force)
            parts.append("【静息心率】\n" + self._metric_text(resting_hr, "bpm", 7, "静息心率"))
        except Exception as error:
            parts.append("【静息心率】" + self._err(error))
        try:
            sleep_records = await self._cached("sleep:30", lambda c: c.sleep_records(30), force=force)
            parts.append(self._sleep_text(sleep_records, 30))
        except Exception as error:
            parts.append("【睡眠】" + self._err(error))
        try:
            activities = await self._cached("act:30:5", lambda c: c.activities(30, 5), force=force)
            parts.append(self._activities_text(activities, 30))
        except Exception as error:
            parts.append("【运动】" + self._err(error))
        try:
            hrv_stats = await self._cached("hrv:7", lambda c: c.hrv_stats(7), force=force)
            parts.append("【睡眠 HRV】\n" + self._metric_text(hrv_stats, "", 7, "HRV"))
        except Exception as error:
            parts.append("【睡眠 HRV】" + self._err(error))
        try:
            stress = await self._cached("stress:7", lambda c: c.stress_stats(7), force=force)
            parts.append("【压力】\n" + self._stress_text(stress, 7))
        except Exception as error:
            parts.append("【压力】" + self._err(error))
        try:
            performance = await self._cached("perf:latest", lambda c: c.athletic_performance(), force=force)
            text = self._performance_text(performance)
            if text:
                parts.append("【运动能力】\n" + text)
        except Exception as error:
            parts.append("【运动能力】" + self._err(error))
        try:
            bests = await self._cached("pb:running", lambda c: c.personal_bests("running"), force=force)
            text = self._personal_bests_text(bests)
            if text:
                parts.append("【个人纪录】\n" + text)
        except Exception as error:
            parts.append("【个人纪录】" + self._err(error))
        return "\n\n".join(parts)[:5000], self._health_events(
            sleep_records, activities, resting_hr, hrv_stats)

    async def refresh_summary(self, *, force: bool = True) -> dict:
        text, events = await self.fetch_today(force=force)
        observed_at = datetime.datetime.now(datetime.timezone.utc)
        self._latest = {
            "basis": "huawei_health_cloud",
            "privacy": "private_only",
            "source": "huawei_health_web",
            "observed_at": observed_at.isoformat(),
            "summary": text,
            "events": events,
        }
        try:
            self.latest_path.write_text(
                json.dumps(self._latest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8", newline="\n")
        except OSError:
            pass
        return dict(self._latest)

    def bind_scope(self, subject_id: str, role_id: str) -> None:
        self.config["bound_subject_id"] = str(subject_id or "")
        self.config["bound_role_id"] = str(role_id or "")
        self.config.save_config()

    def allowed_for(self, subject_id: str, role_id: str) -> bool:
        """Installation-wide accounts stay usable; optional legacy scope still binds.

        The Huawei account belongs to the AstrBot installation's owner, not to a
        particular private chat.  Per-window exposure is controlled separately by
        ``health_enabled``.  If an older build stored an explicit bound scope, keep
        honouring it until the account is rebound.
        """
        bound_subject = self.config.get("bound_subject_id") or ""
        bound_role = self.config.get("bound_role_id") or ""
        if not bound_subject and not bound_role:
            return True
        return bool(subject_id and role_id and bound_subject == subject_id and bound_role == role_id)

    def is_bound(self) -> bool:
        if os.path.exists(self.cookie_file) or bool((self.store.get("token") or {}).get("token")):
            return True
        try:
            return self.credentials.status().get("state") == "ok"
        except Exception:
            return False

    def schedule_refresh(self) -> None:
        if self._closed or (self._refresh_task is not None and not self._refresh_task.done()):
            return
        try:
            task = asyncio.create_task(
                self.refresh_summary(force=True), name="companion-v2:huawei-health-refresh")
        except RuntimeError:
            self._refresh_task = None
            return
        self._refresh_task = task

        def _done(finished: asyncio.Task) -> None:
            if self._refresh_task is finished:
                self._refresh_task = None
            if finished.cancelled():
                return
            try:
                exc = finished.exception()
            except BaseException:
                return
            if exc is not None:
                try:
                    self.logger.warning("[HuaweiHealth] 后台刷新失败 error_type=%s", type(exc).__name__)
                except Exception:
                    pass

        task.add_done_callback(_done)

    def _stop_login_sync(self) -> None:
        manager = getattr(self, "login_manager", None)
        if manager is None:
            return
        session = None
        try:
            session = manager.current_session()
        except Exception:
            session = None
        try:
            stop = getattr(manager, "stop", None)
            if callable(stop):
                stop()
            else:
                cancel = getattr(manager, "cancel", None)
                if callable(cancel):
                    cancel()
        except Exception as error:
            try:
                self.logger.warning("[HuaweiHealth] 停机关闭登录会话失败 error_type=%s", type(error).__name__)
            except Exception:
                pass
        if session is not None:
            close_browser = getattr(session, "_close_browser", None)
            if callable(close_browser) and (
                getattr(session, "_proc", None) is not None
                or getattr(session, "_ws", None) is not None
            ):
                try:
                    close_browser(sweep_profile=True)
                except Exception:
                    pass

    async def aclose(self) -> None:
        """Cancel background refresh/cache tasks and shut down any login browser."""
        self._closed = True
        refresh_task = self._refresh_task
        self._refresh_task = None
        inflight = list(self._cache_inflight.values())
        self._cache_inflight.clear()
        tasks = [task for task in (refresh_task, *inflight) if task is not None and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await asyncio.to_thread(self._stop_login_sync)
        except Exception as error:
            try:
                self.logger.warning("[HuaweiHealth] 登录线程关闭失败 error_type=%s", type(error).__name__)
            except Exception:
                pass
        if _SERVICES.get(self.data_dir) is self:
            _SERVICES.pop(self.data_dir, None)

    def cached_context(self, *, max_age_seconds: int = 3600) -> dict | None:
        latest = self._latest
        if not latest:
            return None
        try:
            observed = datetime.datetime.fromisoformat(latest["observed_at"])
            age = (datetime.datetime.now(datetime.timezone.utc) - observed).total_seconds()
        except (KeyError, TypeError, ValueError):
            return None
        if age > max(60, min(86400, max_age_seconds)):
            return None
        return {**{key: value for key, value in latest.items() if key != "events"},
                "age_seconds": round(max(0, age))}

    def cached_events(self, *, max_age_seconds: int = 129600) -> list[dict]:
        """Return recent completed health events without rotating the summary.

        The event producer owns windows and deduplication.  This method only
        filters by freshness so a stale cloud record never becomes "now".
        """
        latest = self._latest
        if not isinstance(latest, dict) or not isinstance(latest.get("events"), list):
            return []
        now = datetime.datetime.now(datetime.timezone.utc)
        result = []
        for event in latest["events"][:16]:
            if not isinstance(event, dict):
                continue
            try:
                occurred = datetime.datetime.fromisoformat(event["occurred_at"])
            except (KeyError, TypeError, ValueError):
                continue
            age = (now - occurred).total_seconds()
            if 0 <= age <= max(300, min(7 * 86400, max_age_seconds)):
                result.append({**event, "age_seconds": round(age)})
        return result

    async def status(self) -> dict:
        """Return a partial status even when one optional provider is unavailable.

        The dashboard binds each sub-status independently.  A broken token store or
        login-state reader must not turn the whole account card into "unreadable".
        """
        cookie = {"exists": os.path.exists(self.cookie_file), "count": 0, "mtime": ""}
        if cookie["exists"]:
            try:
                cookies = await asyncio.to_thread(self.auth.load_cookies)
                cookie["count"] = len(cookies)
                cookie["mtime"] = time.strftime("%Y-%m-%d %H:%M",
                    time.localtime(os.path.getmtime(self.cookie_file)))
            except Exception:
                pass
        try:
            token = self.store.get("token") or {}
        except Exception:
            token = {}
        try:
            left = int(token.get("expire_at", 0) - time.time()) if token.get("expire_at") else 0
        except (TypeError, ValueError, OverflowError):
            left = 0
        try:
            credentials = self.credentials.status()
        except Exception:
            credentials = {"state": "unavailable"}
        try:
            login = self.login_manager.status()
        except Exception as error:
            login = {"state": "ERROR", "mode": "manual", "auto_trust": True,
                     "message": f"登录状态暂不可读（{type(error).__name__}）",
                     "deadline_left": 0, "need": {"account": False, "sms": False, "trust": False},
                     "updated_at": "-", "prefer_auto": True, "daily_used": 0,
                     "daily_limit": 3, "cooldown_left": 0, "login_mode": "cookie"}
        try:
            browser = self._browser()
        except Exception as error:
            browser = {"path": "", "found": False, "error": type(error).__name__}
        return {
            "bound": bool(cookie["count"] or token.get("token") or credentials.get("state") == "ok"),
            "cookie": cookie,
            "token": {"cached": bool(token.get("token")), "left": max(0, left),
                      "obtained_at": token.get("obtained_at") or ""},
            "credentials": credentials,
            "login": login,
            "browser": browser,
            "latest": self._latest,
            "data_dir": self.data_dir,
        }

    @staticmethod
    def _browser() -> dict:
        try:
            from .vendor.hw_auth import find_browser
            path = find_browser("")
        except Exception:
            path = ""
        return {"path": path, "found": bool(path)}


_SERVICES: dict[str, HuaweiHealthService] = {}


def get_huawei_health_service_for_root(root, *, logger) -> HuaweiHealthService:
    if root is None:
        raise RuntimeError("Huawei Health requires a V2 data root")
    path = str(Path(root) / "huawei_health")
    service = _SERVICES.get(path)
    if service is None:
        service = HuaweiHealthService(path, logger=logger)
        _SERVICES[path] = service
    return service


def get_huawei_health_service(controller, *, logger) -> HuaweiHealthService:
    runtime = getattr(controller, "runtime", None)
    root = getattr(runtime, "data_root", None) or getattr(controller, "data_root", None)
    return get_huawei_health_service_for_root(root, logger=logger)


async def ensure_huawei_health_service(controller, *, logger) -> HuaweiHealthService:
    service = get_huawei_health_service(controller, logger=logger)
    await service.initialize()
    return service


async def close_all_huawei_health_services() -> None:
    """Shut down all cached Huawei Health services and clear the process registry."""
    services = list(_SERVICES.values())
    _SERVICES.clear()
    for service in services:
        try:
            await service.aclose()
        except Exception as error:
            try:
                service.logger.warning("[HuaweiHealth] 服务停机清理失败 error_type=%s", type(error).__name__)
            except Exception:
                pass


__all__ = ["DEFAULT_CONFIG", "HuaweiHealthService", "close_all_huawei_health_services",
           "ensure_huawei_health_service", "get_huawei_health_service",
           "get_huawei_health_service_for_root"]
