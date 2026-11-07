"""Installation-local display remarks for exact group routes."""

import json

from companion_v2.execution.models import TaskError


PREFIX = "host.group_remark.v1:"
ROUTE_FIELDS = ("platform_id", "bot_id", "account_id", "group_id")


def _route(row):
    return tuple(row.get(field) for field in ROUTE_FIELDS)


def _key(route):
    return PREFIX + json.dumps(route, ensure_ascii=False, separators=(",", ":"))


class GroupRemarks:
    def __init__(self, runtime):
        self.runtime = runtime

    def apply(self, rows):
        if not any(row.get("group_id") and row.get("group_id") != "*" for row in rows):
            return [dict(row) for row in rows]
        stored = {}
        for row in self.runtime.read_all(
                "SELECT key, value FROM runtime_metadata WHERE key >= ? AND key < ?",
                (PREFIX, PREFIX + "~")):
            try:
                document = json.loads(row["value"])
            except (TypeError, ValueError):
                continue
            if (isinstance(document, dict) and isinstance(document.get("remark"), str)
                    and type(document.get("revision")) is int):
                stored[row["key"]] = document
        result = []
        for source in rows:
            row = dict(source)
            if row.get("group_id") and row.get("group_id") != "*":
                document = stored.get(_key(_route(row)), {})
                row["remark"] = document.get("remark", "")
                row["remark_revision"] = document.get("revision", 0)
            result.append(row)
        return result

    def write(self, route, remark, expected_revision):
        values = _route(route)
        if (any(type(value) is not str or not value.strip() or len(value) > 512 for value in values)
                or values[-1] == "*" or type(remark) is not str
                or type(expected_revision) is not int or expected_revision < 0):
            raise TaskError("group_remark_command_invalid", "provide one exact group route and remark")
        remark = " ".join(remark.split())
        if len(remark) > 64:
            raise TaskError("group_remark_command_invalid", "the group remark must be at most 64 characters")
        key = _key(values)
        with self.runtime.transaction() as connection:
            row = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (key,)).fetchone()
            current = json.loads(row["value"]) if row else {"revision": 0, "remark": ""}
            if current["revision"] != expected_revision:
                raise TaskError("group_remark_revision_conflict", "the group remark changed; refresh before saving")
            revision = expected_revision + 1
            connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
                               "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                               (key, json.dumps({"remark": remark, "revision": revision}, ensure_ascii=False)))
        return {**dict(zip(ROUTE_FIELDS, values)), "remark": remark, "remark_revision": revision}
