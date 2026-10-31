"""Persistent, V2-local progress for the one-time first-run guide."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4


SCHEMA_VERSION = 3
# 与旧版配置引导一致的八步：窗口、模型、人格与世界知识、关系教学、主动功能与强度、
# 日程与观察、群聊配置，最后是主动消息测试（测试完成才算 ready）。
SETUP_STEPS = ("model", "knowledge", "relationships", "proactive", "life", "group")


class SetupStepsIncomplete(ValueError):
    """A guided section is still open, so the one-time test cannot start yet.

    Stays a ``ValueError``: every rejection in this store is one, and existing
    callers keep working unchanged.  The distinct type exists only so a host
    route can tell "the guide is not finished" apart from "this attempt already
    ran and cannot be replayed" — two conditions with nothing in common, which
    used to reach the operator as the same, misleading, replay-flavoured code.
    """


def empty_state() -> dict:
    return {"schema_version": SCHEMA_VERSION, "completed": [],
            "skipped": [], "proactive_test": None, "ready": False}


class SetupStateStore:
    def __init__(self, data_root):
        self.root = Path(data_root).resolve() / "setup"
        self.path = self.root / "progress.json"

    def read(self) -> dict:
        if not self.path.exists():
            return empty_state()
        raw = self.path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError("setup progress must be UTF-8 without BOM")
        document = json.loads(raw.decode("utf-8"))
        if type(document) is not dict:
            raise ValueError("setup progress has an unsupported shape")
        if document.get("schema_version") == 1:
            old_fields = {"schema_version", "mode", "completed", "proactive_test", "ready"}
            if set(document) != old_fields or type(document.get("completed")) is not list:
                raise ValueError("setup progress has an unsupported shape")
            old_test = document["proactive_test"]
            if old_test is not None:
                if type(old_test) is not dict:
                    raise ValueError("setup test receipt is invalid")
                tested_at = old_test.get("tested_at")
                if old_test.get("state") != "delivered" or type(tested_at) is not str:
                    raise ValueError("setup test receipt is invalid")
                old_test = {"state": "delivered", "attempt_id": "previous-setup",
                            "started_at": tested_at, "finished_at": tested_at}
            document = {**empty_state(), "completed": document["completed"],
                        "proactive_test": old_test, "ready": document["ready"]}
        elif document.get("schema_version") == 2:
            old_fields = {"schema_version", "mode", "completed", "skipped", "proactive_test", "ready"}
            if set(document) != old_fields:
                raise ValueError("setup progress has an unsupported shape")
            document = {"schema_version": SCHEMA_VERSION, "completed": document["completed"],
                        "skipped": document["skipped"], "proactive_test": document["proactive_test"],
                        "ready": document["ready"]}
        if (document.get("schema_version") != SCHEMA_VERSION
                or set(document) != {"schema_version", "completed", "skipped", "proactive_test", "ready"}
                or type(document.get("completed")) is not list
                or type(document.get("skipped")) is not list
                or any(item not in SETUP_STEPS for item in document["completed"] + document["skipped"])
                or len(set(document["completed"])) != len(document["completed"])
                or len(set(document["skipped"])) != len(document["skipped"])
                or set(document["completed"]) & set(document["skipped"])
                or type(document.get("ready")) is not bool):
            raise ValueError("setup progress has an unsupported shape")
        test = document["proactive_test"]
        if test is not None:
            if (type(test) is not dict or test.get("state") not in ("started", "failed", "unknown", "delivered", "received")
                    or type(test.get("attempt_id")) is not str
                    or type(test.get("started_at")) is not str):
                raise ValueError("setup test receipt is invalid")
            if ("contact_enabled_before" in test
                    and type(test["contact_enabled_before"]) is not bool):
                raise ValueError("setup contact permission receipt is invalid")
            if ("contact_permission_restored" in test
                    and type(test["contact_permission_restored"]) is not bool):
                raise ValueError("setup contact permission receipt is invalid")
            if "route" in test and (type(test["route"]) is not dict
                    or set(test["route"]) != {"id", "label", "variant"}
                    or any(type(value) is not str for value in test["route"].values())):
                raise ValueError("setup test route receipt is invalid")
            if "stages" in test and (type(test["stages"]) is not list or len(test["stages"]) > 16
                    or any(type(item) is not dict or not {"stage", "state"} <= set(item)
                           or set(item) - {"stage", "state", "detail"}
                           or type(item["stage"]) is not str or type(item["state"]) is not str
                           or ("detail" in item and type(item["detail"]) is not str)
                           for item in test["stages"])):
                raise ValueError("setup test stages are invalid")
            for key in ("task_id", "delivery_state", "receipt_ref"):
                if key in test and (type(test[key]) is not str or len(test[key]) > 256):
                    raise ValueError("setup test delivery receipt is invalid")
            if test["state"] in ("failed", "unknown", "delivered") and type(test.get("finished_at")) is not str:
                raise ValueError("setup test receipt is invalid")
            if ("error_code" in test and (type(test["error_code"]) is not str
                    or len(test["error_code"]) > 64
                    or any(not (char.islower() or char.isdigit() or char == "_") for char in test["error_code"]))):
                raise ValueError("setup test receipt is invalid")
            if test["state"] == "received" and (
                    type(test.get("confirmed_at")) is not str
                    or type(test.get("confirmed_by")) is not str or not test["confirmed_by"].strip()):
                raise ValueError("setup test recipient confirmation is invalid")
        if document["ready"] != bool(test and test.get("state") in ("delivered", "received")):
            raise ValueError("setup completion does not match the proactive test receipt")
        return document

    def write(self, document: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name("." + self.path.name + "." + uuid4().hex + ".tmp")
        payload = json.dumps(document, ensure_ascii=False, allow_nan=False,
                             sort_keys=True, indent=2) + "\n"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def complete(self, step: str, *, skipped: bool = False) -> dict:
        if step not in SETUP_STEPS:
            raise ValueError("unknown setup step")
        state = self.read()
        destination = "skipped" if skipped else "completed"
        other = "completed" if skipped else "skipped"
        if step not in state[destination]:
            state[destination].append(step)
        if step in state[other]:
            state[other].remove(step)
        self.write(state)
        return state

    def begin_proactive_test(self, *, attempt_id: str, started_at: str) -> dict:
        state = self.read()
        if state["ready"]:
            return state
        if state["proactive_test"] is not None and state["proactive_test"]["state"] != "failed":
            raise ValueError("the proactive test has already started and cannot be replayed")
        if any(step not in state["completed"] + state["skipped"] for step in SETUP_STEPS):
            raise SetupStepsIncomplete("complete the guided setup steps first")
        state["proactive_test"] = {"state": "started", "attempt_id": attempt_id,
                                    "started_at": started_at,
                                    "stages": [{"stage": "authorization", "state": "passed"},
                                               {"stage": "binding", "state": "passed"},
                                               {"stage": "runtime", "state": "passed"},
                                               {"stage": "settings", "state": "running"}]}
        self.write(state)
        return state

    def record_proactive_test_progress(self, *, attempt_id: str, stages: list[dict], route: dict | None = None,
                                       task_id: str = "", delivery_state: str = "", receipt_ref: str = "") -> dict:
        if (not isinstance(stages, list) or len(stages) > 16
                or any(type(item) is not dict or not {"stage", "state"} <= set(item)
                       or set(item) - {"stage", "state", "detail"}
                       or type(item["stage"]) is not str or type(item["state"]) is not str
                       or ("detail" in item and (type(item["detail"]) is not str or len(item["detail"]) > 256))
                       for item in stages)):
            raise ValueError("proactive test stages are invalid")
        if route is not None and (type(route) is not dict or set(route) != {"id", "label", "variant"}
                or any(type(value) is not str for value in route.values())):
            raise ValueError("proactive test route is invalid")
        if any(type(value) is not str or len(value) > 256 for value in (task_id, delivery_state, receipt_ref)):
            raise ValueError("proactive test receipt details are invalid")
        state = self.read()
        receipt = state["proactive_test"]
        if not receipt or receipt["attempt_id"] != attempt_id or receipt["state"] != "started":
            raise ValueError("the proactive test attempt is no longer active")
        receipt["stages"] = stages
        if route is not None:
            receipt["route"] = route
        if task_id:
            receipt["task_id"] = task_id
        if delivery_state:
            receipt["delivery_state"] = delivery_state
        if receipt_ref:
            receipt["receipt_ref"] = receipt_ref
        self.write(state)
        return state

    def record_test_contact_permission(self, *, attempt_id: str, enabled_before: bool) -> dict:
        if type(enabled_before) is not bool:
            raise ValueError("the previous contact permission must be boolean")
        state = self.read()
        receipt = state["proactive_test"]
        if not receipt or receipt["attempt_id"] != attempt_id or receipt["state"] != "started":
            raise ValueError("the proactive test attempt is no longer active")
        receipt["contact_enabled_before"] = enabled_before
        receipt["contact_permission_restored"] = False
        self.write(state)
        return state

    def mark_test_contact_permission_restored(self, *, attempt_id: str) -> dict:
        state = self.read()
        receipt = state["proactive_test"]
        if not receipt or receipt["attempt_id"] != attempt_id:
            raise ValueError("the proactive test attempt does not match")
        receipt["contact_permission_restored"] = True
        self.write(state)
        return state

    def finish_proactive_test(self, *, delivered: bool | None = None, outcome: str | None = None,
                              finished_at: str, error_code: str = "", report: dict | None = None) -> dict:
        state = self.read()
        receipt = state["proactive_test"]
        if not receipt or receipt["state"] != "started":
            raise ValueError("there is no in-flight proactive test")
        result = outcome or ("delivered" if delivered else "unknown")
        if result not in ("failed", "unknown", "delivered"):
            raise ValueError("proactive test outcome is invalid")
        receipt["state"] = result
        receipt["finished_at"] = finished_at
        if error_code:
            receipt["error_code"] = error_code
        if report:
            allowed = {"route", "stages", "task_id", "delivery_state", "receipt_ref"}
            if set(report) - allowed:
                raise ValueError("proactive test report contains unsupported fields")
            receipt.update(report)
        state["ready"] = result == "delivered"
        self.write(state)
        return state

    def confirm_proactive_test(self, *, attempt_id: str, confirmed_by: str, confirmed_at: str) -> dict:
        state = self.read()
        receipt = state["proactive_test"]
        if (not receipt or receipt["attempt_id"] != attempt_id
                or not confirmed_by.strip()):
            raise ValueError("confirm the existing proactive test attempt")
        if state["ready"]:
            return state
        if receipt["state"] not in ("started", "unknown"):
            raise ValueError("there is no unconfirmed proactive test")
        receipt.update(state="received", confirmed_by=confirmed_by, confirmed_at=confirmed_at)
        state["ready"] = True
        self.write(state)
        return state
