"""Role-owned outfit photos, generated through the host's Image capability."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
import hashlib
from io import BytesIO
import json
import logging
import re
from zoneinfo import ZoneInfo

from PIL import Image

from companion_v2.agenda.planning import KIND as PLANNING_KIND
from companion_v2.agenda.planning_config import (
    SCHEDULE_MODE_ORCHESTRATED, configured_weather_location, configured_weather_max_age,
)
from companion_v2.agenda.weather_material import planning_weather_material
from companion_v2.execution.models import (
    Lane, ProviderBinding, SideEffect, TaskError, TaskRegistration, TaskDefinition,
    TaskSpec, TaskResult, TERMINAL, digest, encode, encode_prompt, require, stamp,
)
from companion_v2.execution.runner import ExactTaskRunner
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import ScopeKind
from companion_v2.relationships.private_access import REVOCABLE_ABSENCE_CODES
from companion_v2.storage.generated_album import album_filename, hidden_album_ids
from companion_v2.storage.image_payloads import MARKER_PREFIX, image_root, is_marker, load_image, store_image

from .models import MAX_ASSET_BYTES, ReferenceRole


KIND = "role.daily_outfit"
PREFIX = "daily-outfit-photo.v1:"
DAILY_OUTFIT_ACTION = "wardrobe.outfit"
DAILY_OUTFIT_IMAGE_PREFIX = "wardrobe-outfit:"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_LOCAL_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MARKER_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:png|jpg|webp|gif|bin)$")
_DAILY_OUTFIT_ID_RE = re.compile(r"^wardrobe-outfit:([0-9a-f]{8}):(\d{4}-\d{2}-\d{2}):([0-9a-f]{8})$")
_SAFE_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
TIMEOUT = 360
logger = logging.getLogger(__name__)


def daily_outfit_image_id(owner: str, local_date: str, content_hash: str) -> str:
    """Build a deterministic, role-scoped album identifier for one daily outfit photo."""
    return f"{DAILY_OUTFIT_IMAGE_PREFIX}{owner[:8].lower()}:{local_date}:{content_hash[:8].lower()}"


def parse_daily_outfit_image_id(image_id: str) -> tuple[str, str, str] | None:
    """Parse ``wardrobe-outfit:<owner8>:<YYYY-MM-DD>:<hash8>`` or return None."""
    if type(image_id) is not str:
        return None
    match = _DAILY_OUTFIT_ID_RE.fullmatch(image_id)
    if match is None:
        return None
    return match.group(1), match.group(2), match.group(3)


def _sniff_mime_type(content: bytes, declared: str = "") -> str:
    normalized = (declared or "").strip().lower()
    if normalized in _SAFE_IMAGE_MIMES:
        return normalized
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    return "application/octet-stream"


def _image_dimensions(content: bytes, photo: dict) -> tuple[int, int]:
    width = photo.get("width")
    height = photo.get("height")
    if type(width) is int and type(height) is int and 1 <= width <= 16384 and 1 <= height <= 16384:
        return width, height
    try:
        with Image.open(BytesIO(content)) as opened:
            w, h = opened.size
            if type(w) is int and type(h) is int and w > 0 and h > 0:
                return w, h
    except Exception:
        pass
    return 0, 0


def _marker_file_available(data_root, marker_str: str) -> bool:
    if data_root is None:
        return False
    try:
        raw = marker_str.encode("ascii")
    except UnicodeEncodeError:
        return False
    if not is_marker(raw):
        return bool(raw)
    name = raw[len(MARKER_PREFIX):].decode("ascii", errors="ignore")
    if _MARKER_NAME_RE.fullmatch(name) is None:
        return False
    try:
        return (image_root(data_root) / name).is_file()
    except OSError:
        return False


def list_daily_outfit_records(runtime, owner: str, *, hidden_ids=()) -> list[dict]:
    """List valid role-owned daily outfit metadata records without decoding image bytes."""
    read_all = getattr(runtime, "read_all", None)
    if not callable(read_all) or not isinstance(owner, str) or len(owner) < 8:
        return []
    prefix = f"{PREFIX}{owner}:"
    prefix_end = f"{PREFIX}{owner};"
    rows = read_all(
        "SELECT key, value FROM runtime_metadata WHERE key >= ? AND key < ?",
        (prefix, prefix_end),
    )
    hidden = set(hidden_ids or ())
    data_root = getattr(runtime, "data_root", None)
    records = []
    for row in rows:
        key = row["key"]
        local_date = key[len(prefix):] if isinstance(key, str) else ""
        if _LOCAL_DATE_RE.fullmatch(local_date) is None:
            continue
        try:
            document = json.loads(row["value"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        photo = document.get("photo") if isinstance(document, dict) else None
        if not isinstance(photo, dict):
            continue
        marker = photo.get("marker")
        content_hash = photo.get("content_hash")
        created_at = photo.get("created_at")
        if (type(marker) is not str or not marker
                or type(content_hash) is not str or _HEX64_RE.fullmatch(content_hash.lower()) is None
                or type(created_at) is not str or not created_at.strip()):
            continue
        normalized_hash = content_hash.lower()
        image_id = daily_outfit_image_id(owner, local_date, normalized_hash)
        if image_id in hidden:
            continue
        if not _marker_file_available(data_root, marker):
            continue
        records.append({
            "image_id": image_id,
            "local_date": local_date,
            "marker": marker,
            "content_hash": normalized_hash,
            "created_at": created_at,
            "action": DAILY_OUTFIT_ACTION,
            "mime_type": photo.get("mime_type") if type(photo.get("mime_type")) is str else "",
            "width": photo.get("width") if type(photo.get("width")) is int else None,
            "height": photo.get("height") if type(photo.get("height")) is int else None,
            "weather": photo.get("weather") if isinstance(photo.get("weather"), dict) else None,
            "activities": list(photo.get("activities")) if isinstance(photo.get("activities"), list) else [],
        })
    records.sort(key=lambda item: (item["created_at"], item["image_id"]), reverse=True)
    return records


def load_daily_outfit_record(runtime, owner: str, image_id: str) -> dict | None:
    """Resolve and verify one role-owned daily outfit image payload by its album image_id."""
    parsed = parse_daily_outfit_image_id(image_id)
    if parsed is None or not isinstance(owner, str) or len(owner) < 8:
        return None
    owner_prefix, local_date, hash_prefix = parsed
    if owner_prefix != owner[:8].lower():
        return None
    read_one = getattr(runtime, "read_one", None)
    data_root = getattr(runtime, "data_root", None)
    if not callable(read_one) or data_root is None:
        return None
    row = read_one("SELECT value FROM runtime_metadata WHERE key = ?", (f"{PREFIX}{owner}:{local_date}",))
    if row is None:
        return None
    try:
        document = json.loads(row["value"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    photo = document.get("photo") if isinstance(document, dict) else None
    if not isinstance(photo, dict):
        return None
    marker = photo.get("marker")
    content_hash = photo.get("content_hash")
    created_at = photo.get("created_at")
    if (type(marker) is not str or not marker
            or type(content_hash) is not str or _HEX64_RE.fullmatch(content_hash.lower()) is None
            or type(created_at) is not str or not created_at.strip()):
        return None
    normalized_hash = content_hash.lower()
    if not normalized_hash.startswith(hash_prefix):
        return None
    try:
        content = load_image(data_root, marker.encode("ascii"))
    except (TaskError, OSError, ValueError, UnicodeEncodeError):
        return None
    if not isinstance(content, bytes) or not content or len(content) > MAX_ASSET_BYTES:
        return None
    if hashlib.sha256(content).hexdigest() != normalized_hash:
        return None
    mime_type = _sniff_mime_type(content, photo.get("mime_type") if type(photo.get("mime_type")) is str else "")
    width, height = _image_dimensions(content, photo)
    return {
        "image_id": daily_outfit_image_id(owner, local_date, normalized_hash),
        "local_date": local_date,
        "created_at": created_at,
        "action": DAILY_OUTFIT_ACTION,
        "content": content,
        "content_hash": normalized_hash,
        "mime_type": mime_type,
        "width": width,
        "height": height,
        "size_bytes": len(content),
        "weather": photo.get("weather") if isinstance(photo.get("weather"), dict) else None,
        "activities": list(photo.get("activities")) if isinstance(photo.get("activities"), list) else [],
    }

PROMPT = """Create one image of this character wearing a newly designed outfit for today.
Treat the material below as background. Use the character's persona and visual reference for identity, and prefer the explicit character profile when it supplies a detail. Preserve recognizable facial features, hair, build and visual style. Use the clothing preferences and any suggested garment as inspiration.
Read the day's agenda as an itinerary. Dress for its main occasion and any meaningful transitions between activities. Let the observed weather guide layers, fabrics, footwear and accessories where relevant. Use an adaptable outfit or a simple setting when the agenda or weather is missing.
Show one cohesive outfit on the character in a natural three-quarter or full-length portrait with a visible face. Use a setting compatible with the plans. Keep the image free of text, labels, logos and collage panels."""
PROFILE_FIELDS = ("name", "species", "gender", "age", "occupation", "appearance_points",
                  "hair", "eyes", "appearance", "clothing", "hobbies", "world_name", "era")


class DailyOutfitPhotos:
    def __init__(self, runtime, weather_port):
        self.runtime, self.weather_port = runtime, weather_port
        self.port = None
        self.workers = {}
        self.next_check = None
        self.closed = False
        self.registrations = (TaskRegistration(TaskDefinition(
            KIND, KIND, "extension.tool.invoke", Lane.BACKGROUND, side_effect=SideEffect.EXTERNAL_NETWORK,
            binding=ProviderBinding(KIND, "host.image", "1", "daily-outfit-v1"),
            timeout_seconds=TIMEOUT, max_attempts=1, concurrency=1), self.validate),)

    def attach(self, app):
        self.app = app
        self.runner = ExactTaskRunner(app.tasks, frozenset({KIND}), self.generate, waiting=app.background_waiting)

    def _day(self, context, at):
        settings, _ = self.app.role_state.settings(context)
        zone = ZoneInfo(settings["planning_timezone"])
        return at.astimezone(zone).date(), zone, settings

    def _key(self, context, day):
        return PREFIX + self.app.references.owner(context) + ":" + day.isoformat()

    def _read(self, key):
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key=?", (key,))
        return json.loads(row["value"]) if row else {}

    @staticmethod
    def _write(connection, key, value):
        connection.execute("""INSERT INTO runtime_metadata(key,value) VALUES (?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, encode(value)))

    def _task(self, identifier):
        if not identifier:
            return None
        row = self.runtime.read_one("SELECT * FROM execution_tasks WHERE task_id=? AND kind=?", (identifier, KIND))
        return self.app.tasks._record(row) if row else None

    def _allowed(self, context, at):
        self.app.require_running()
        if self.closed or context.scope.kind is not ScopeKind.PRIVATE:
            raise TaskError("daily_outfit_unavailable", "daily outfit photos require an active private role")
        if not self.app.reference_settings.read(context, at)["daily_outfit_enabled"]:
            raise TaskError("daily_outfit_disabled", "enable daily outfit photos for this role")

    def share_photo(self, context, at):
        self._allowed(context, at)
        day, _, _ = self._day(context, at)
        photo = self._read(self._key(context, day)).get("photo")
        if (not isinstance(photo, dict) or not isinstance(photo.get("content_hash"), str)
                or not _HEX64_RE.fullmatch(photo["content_hash"])):
            return None
        owner = self.app.references.owner(context)
        image_id = daily_outfit_image_id(owner, day.isoformat(), photo["content_hash"])
        if image_id in hidden_album_ids(self.runtime, context):
            return None
        return {**photo, "image_id": image_id, "local_date": day.isoformat()}

    @outcome
    def validate(self, spec, at):
        context = spec.context
        self._allowed(context, at)
        day, _, _ = self._day(context, spec.not_before)
        if self._read(self._key(context, day)).get("task_id") != spec.task_id:
            raise TaskError("daily_outfit_superseded", "the original daily outfit request is no longer current")
        return True

    def request(self, context, *, operation_key=None):
        at = self.app.now()
        self._allowed(context, at)
        if self.port is None or not self.port.outfit_image_route(context, self._identity(context, at)):
            raise TaskError("daily_outfit_image_unavailable", "the role has no active Image route")
        day, _, _ = self._day(context, at)
        key = self._key(context, day)
        task_id = "outfit-" + digest([key, operation_key or "automatic"])
        with self.runtime.transaction() as connection:
            document = self._read(key)
            active = self._task(document.get("task_id"))
            existing = self._task(task_id)
            if active and active.state not in TERMINAL:
                task = active
            elif existing or (operation_key is None and document):
                return self.status(context, at=at)
            else:
                spec = TaskSpec(task_id, context, KIND, task_id, task_id, at, at + timedelta(minutes=15))
                self._write(connection, key, {"task_id": task_id, "photo": document.get("photo")})
                task = require(self.app.tasks.submit(spec))
        self._launch(key, task)
        return self.status(context, at=at)

    def _launch(self, key, task):
        if task.state not in ("created", "retry_scheduled") or key in self.workers:
            return
        worker = asyncio.create_task(self._run(key, task.spec), name=KIND)
        self.workers[key] = worker

    async def _run(self, key, spec):
        try:
            result = await self.runner.run(spec.context, spec.task_id)
            code = result.failure.code if result.failure else result.unwrap().stop_code
            if code:
                with self.runtime.transaction() as connection:
                    document = self._read(key)
                    if document.get("task_id") == spec.task_id:
                        self._write(connection, key, {**document, "code": code})
        except (TaskError, OSError) as error:
            logger.warning("Daily outfit task closed: %s", getattr(error, "code", type(error).__name__))
        finally:
            self.workers.pop(key, None)
            self.app.notify()

    def advance(self):
        if self.closed or self.port is None or self.workers:
            return
        at = self.app.now()
        if self.next_check and at < self.next_check:
            return
        self.next_check = at + timedelta(seconds=60)
        for context, _, _ in tuple(self.app.state_maintenance.bindings.values()):
            try:
                self._allowed(context, at)
                day, zone, settings = self._day(context, at)
                key = self._key(context, day)
                document = self._read(key)
                if document:
                    task = self._task(document.get("task_id"))
                    if task:
                        self._launch(key, task)
                    if self.workers:
                        return
                    continue
                if (settings["calendar_enabled"] and settings["planning_enabled"]
                        and settings["schedule_simulation_mode"] == SCHEDULE_MODE_ORCHESTRATED
                        and PLANNING_KIND in self.app.gateway.router.profiles):
                    planning = self.app.calendar_planning.status(context)
                    daily = planning["daily_result"]
                    last = planning["last_result"] or {}
                    manual_today = (last.get("mode") == "daily" and last.get("state") == "succeeded"
                                    and datetime.fromisoformat(last["observed_at"]).astimezone(zone).date() == day)
                    if not manual_today and (daily is None or daily["state"] not in TERMINAL
                                             or planning["next_review_at"]):
                        continue
                if self.app.state_maintenance.foreground_busy(context):
                    continue
                self.request(context)
                if self.workers:
                    return
            except TaskError:
                continue

    def _identity(self, context, at):
        if not self.app.reference_settings.read(context, at)["enabled"]:
            return None
        references = self.app.references
        assets = references.list_assets(context, role=ReferenceRole.IDENTITY)
        owner = references.owner(context)
        for asset in assets:
            if references.assets.usage_of(owner, asset.asset_id)["base_identity"]:
                return asset.asset_id
        return assets[0].asset_id if assets else None

    async def _materials(self, context, at, day, zone, settings):
        snapshot = self.app.messages.role_profile_context.capture(context, at) or {}
        fields = snapshot.get("fields", {})
        role = {key: fields[key][:600] for key in PROFILE_FIELDS if fields.get(key)}
        binding = self.app.state_maintenance.bindings.get(context.key)
        persona = binding[1] if binding is not None and binding[0] == context else await self.port.outfit_persona(context)
        if persona:
            role["persona"] = persona[:3200]
        agenda = []
        if settings["calendar_enabled"]:
            start = datetime.combine(day, time.min, zone)
            try:
                page = self.app.calendar.occurrences(context, start=start,
                    end=datetime.combine(day + timedelta(days=1), time.min, zone), kinds=("role_plan",))
                for item in page["items"]:
                    if item["state"] != "accepted" or item["sources"]:
                        continue
                    agenda.append({"time": datetime.fromisoformat(item["start"]).astimezone(zone).strftime("%H:%M"),
                                   "activity": item["title"][:120], "detail": item["detail"][:140]})
                    if len(agenda) == 8:
                        break
            except TaskError:
                pass
        weather = None
        if settings["planning_weather_enabled"] and self.weather_port is not None:
            location = configured_weather_location(settings["planning_weather_location"])
            if location is not None:
                try:
                    observed = await asyncio.wait_for(self.weather_port.fetch(location), timeout=6)
                    current = planning_weather_material(observed, location, self.app.now(),
                                                        max_age=configured_weather_max_age(settings))
                    if current:
                        weather = {key: current[key] for key in ("role_location", "condition", "temperature_c", "wind_kph")}
                except (TimeoutError, OSError, ValueError, TaskError):
                    pass
                except Exception as error:
                    logger.debug("Daily outfit weather unavailable: %s", type(error).__name__)
        wardrobe = {}
        references = self.app.references
        try:
            wardrobe_options = references.wardrobe_options(context)
        except TaskError as error:
            if error.code not in REVOCABLE_ABSENCE_CODES:
                raise
            wardrobe_options = {}
        if wardrobe_options.get("enabled") and wardrobe_options.get("prompt_enabled"):
            wardrobe = {"preferences": wardrobe_options["tendency"][:500]}
            selected = references.assets.daily_outfit_item(references.owner(context), day.isoformat())
            if selected:
                wardrobe["suggested_garment"] = {"name": selected.name, "details": selected.description[:400]}
        return {"date": day.isoformat(), "character": role, "agenda": agenda, "weather": weather,
                "wardrobe": wardrobe}

    async def generate(self, run):
        spec = run.task.spec
        context, at = spec.context, self.app.now()
        day, zone, settings = self._day(context, spec.not_before)
        key = self._key(context, day)
        self.app.messages.image_quota.admit_proactive(context, at)
        material = await self._materials(context, at, day, zone, settings)
        prompt = PROMPT + "\n" + encode_prompt(material)
        result = await self.port.generate_outfit_image(run, prompt, self._identity(context, self.app.now()))
        if result.state != "succeeded" or not result.images:
            if result.effect == "not_committed" and result.receipt_ref:
                return TaskResult("failed", effect="not_committed", receipt_ref=result.receipt_ref)
            return TaskResult("uncertain", effect="unknown", receipt_ref=result.receipt_ref)
        image = result.images[0]
        marker = await asyncio.to_thread(store_image, self.runtime.data_root, image.content,
                                         mime_type=image.descriptor.mime_type)
        with self.runtime.transaction() as connection:
            run.validate()
            run.record_operation(result.operation_id)
            self._write(connection, key, {"task_id": spec.task_id, "photo": {
                "marker": marker.decode("ascii"), "content_hash": image.descriptor.content_hash,
                "created_at": stamp(self.app.now()), "weather": material["weather"],
                "activities": [item["activity"] for item in material["agenda"]],
                "mime_type": image.descriptor.mime_type,
                "width": image.descriptor.width, "height": image.descriptor.height,
                "size_bytes": image.descriptor.byte_count,
            }})
        return TaskResult("succeeded", effect="committed", result_ref=spec.task_id,
                          operation_id=result.operation_id, receipt_ref=result.receipt_ref)

    def status(self, context, *, at, preview=None):
        day, _, _ = self._day(context, at)
        local_date = day.isoformat()
        owner = self.app.references.owner(context)
        document = self._read(self._key(context, day))
        task = self._task(document.get("task_id"))
        state = task.state if task else "empty"
        photo = document.get("photo")
        value = {"local_date": local_date, "state": state, "code": document.get("code", "")}
        if photo:
            photo_hash = photo.get("content_hash")
            image_id = (daily_outfit_image_id(owner, local_date, photo_hash)
                        if type(photo_hash) is str and _HEX64_RE.fullmatch(photo_hash.lower()) else "")
            entry = {key: photo[key] for key in ("created_at", "weather", "activities")}
            entry["local_date"] = local_date
            if image_id:
                entry["image_id"] = image_id
            value["photo"] = entry
            if preview:
                try:
                    content = load_image(self.runtime.data_root, photo["marker"].encode("ascii"))
                    if hashlib.sha256(content).hexdigest() == photo["content_hash"]:
                        entry["thumbnail"] = preview(content)
                        mime_type = _sniff_mime_type(content, photo.get("mime_type") or "")
                        width, height = _image_dimensions(content, photo)
                        entry["mime_type"] = mime_type
                        entry["width"] = width
                        entry["height"] = height
                        entry["size_bytes"] = len(content)
                        if image_id:
                            entry["filename"] = album_filename(
                                image_id, entry["created_at"], DAILY_OUTFIT_ACTION,
                                content, mime_type=mime_type)
                except (TaskError, OSError, ValueError):
                    pass
        return value

    @property
    def active_handles(self):
        return self.runner.active_handles

    def cancel_running(self):
        self.closed = True
        for worker in tuple(self.workers.values()):
            worker.cancel()

    async def close(self):
        workers = tuple(self.workers.values())
        self.cancel_running()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        await self.runner.close()
