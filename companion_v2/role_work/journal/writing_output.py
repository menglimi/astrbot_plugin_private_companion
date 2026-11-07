"""Parse storage structure only; literary quality belongs in the instructions."""

import json

from .models import DreamRecall, JournalDerivatives, JournalDraft, JournalKind


class JournalOutputError(ValueError):
    def __init__(self, code, reason):
        super().__init__(reason)
        self.code = code


def object_output(text):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate writing field")
            value[key] = item
        return value
    raw = text.strip()
    # 模型常把整段合法 JSON 包在 ```json 围栏里；剥掉围栏再解析，
    # 否则日记/选材会被误判成 unhandled_exception。
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    value = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError("writing output must be an object")
    return value


def draft_output(text, job):
    value = object_output(text)
    if set(value) != {"body", "form", "recall", "afterglow"}:
        raise ValueError("writing result must contain only its body fields")
    if job.target.key.kind is JournalKind.DIARY:
        if not isinstance(value["body"], str) or not value["body"].strip():
            raise JournalOutputError("journal_diary_body_missing", "diary output needs a nonempty body")
        return JournalDraft(job.target, body=value["body"], form=value["form"], recall=None,
                            afterglow="", sources=job.sources, activities=job.activities,
                            journals=job.recent, materials=job.materials)
    recall = DreamRecall(value["recall"]) if value["recall"] is not None else None
    if job.target.key.kind is JournalKind.DREAM:
        if recall is DreamRecall.AFTERGLOW:
            if value["body"] != "" or not isinstance(value["afterglow"], str) or not value["afterglow"].strip():
                raise ValueError("afterglow-only recall cannot manufacture a dream body")
        elif not isinstance(value["body"], str) or not value["body"].strip():
            raise ValueError("remembered dream or fragments need their body")
    return JournalDraft(job.target, body=value["body"], form=value["form"], recall=recall,
                        afterglow=value["afterglow"], sources=job.sources, activities=job.activities,
                        journals=job.recent, materials=job.materials)


def derivatives_output(text, parent):
    value = object_output(text)
    if set(value) != {"summary", "dream_fragments", "continuity"}:
        raise ValueError("derived result must contain only its material fields")
    fragments = value["dream_fragments"]
    if not isinstance(fragments, list) or len(fragments) > 32:
        raise ValueError("derived fragments are not a bounded list")
    texts, origins = [], []
    known = {item.material_id for item in parent.draft.materials}
    for fragment in fragments:
        if not isinstance(fragment, dict) or set(fragment) != {"text", "material_ids"}:
            raise ValueError("fragment must retain its provenance")
        ids = fragment["material_ids"]
        if (not isinstance(ids, list) or any(not isinstance(item, str) or item not in known for item in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("fragment cannot invent or repeat material references")
        texts.append(fragment["text"])
        origins.append(tuple(ids))
    return JournalDerivatives(parent.reference, value["summary"], tuple(texts), value["continuity"], tuple(origins))
