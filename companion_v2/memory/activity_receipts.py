"""Project real role receipts without turning writing or elapsed time into facts."""

from dataclasses import dataclass
from datetime import datetime

from companion_v2.execution.models import TaskError, digest, require, stamp
from companion_v2.role_work.codec import activity_from_json
from companion_v2.role_work.journal import codec as journal_codec
from companion_v2.role_work.models import RoleActivityState
from companion_v2.sources.privacy import redact_text
from companion_v2.storage.models import OperationState


TABLES = {"activity": "role_life_operations", "journal": "role_journal_operations"}


@dataclass(frozen=True)
class ReceiptExperience:
    title: str
    statements: tuple[str, ...]
    entities: tuple[str, ...]
    occurred_at: datetime
    sources: tuple

    @property
    def text(self):
        return "\n".join(self.statements)


def excerpt(value, maximum=1200):
    value = redact_text(value)
    return value if len(value) <= maximum else value[:maximum] + "（原记录较长，可沿原活动继续读取）"


class RoleReceiptReader:
    def __init__(self, life_context):
        self.life = life_context
        self.runtime = life_context.runtime

    def read(self, context, stream, operation_id, at):
        if stream not in TABLES or type(operation_id) is not str or not 1 <= len(operation_id) <= 512:
            raise TaskError("memory_activity_receipt_invalid", "select an original life or journal receipt")
        owner = self.life.owner(context)
        row = self.runtime.read_one(
            f"SELECT result_json FROM {TABLES[stream]} WHERE operation_id = ? AND owner_key = ?",
            (operation_id, digest(owner)))
        if row is None:
            raise TaskError("memory_activity_receipt_unavailable", "original role receipt is unavailable")
        operation = require(self.life.life.operations.get(operation_id))
        expected_owner = "role.life" if stream == "activity" else "role.journal"
        if (operation.state is not OperationState.COMPLETED or operation.operation.owner != expected_owner
                or operation.operation.scope != context.scope):
            raise TaskError("memory_activity_receipt_unconfirmed", "receipt lacks its original completed operation")
        if stream == "activity":
            return self._activity(context, operation_id, row["result_json"], at)
        return self._journal(context, operation_id, row["result_json"], at)

    def _activity(self, context, operation_id, raw, at):
        activity = activity_from_json(raw)
        # A start/pause/cancel receipt and a plan whose time passed are not a
        # completed experience. Completion must be present in the original owner.
        if activity.state is not RoleActivityState.COMPLETED:
            return None
        try:
            original = self.life.life.activity_revision(activity.activity_id, activity.revision,
                owner=self.life.owner(context))
        except KeyError:
            raise TaskError("memory_activity_receipt_unavailable", "original activity version is unavailable") from None
        history = self.runtime.read_one("""SELECT operation_id FROM role_activity_revisions
            WHERE owner_key = ? AND activity_id = ? AND revision = ?""",
            (digest(activity.owner), activity.activity_id, activity.revision))
        if original != activity or history["operation_id"] != operation_id:
            raise TaskError("memory_activity_receipt_changed", "receipt differs from the recorded activity version")
        self.life._activity(activity, context, at)
        sources = []
        if activity.work is not None and activity.work.source is not None:
            sources.append(activity.work.source)
        label = excerpt(activity.description or "生活活动", 220)
        statements = [f"角色活动执行端在 {stamp(activity.updated_at)} 记录了完成回执：{label}。"]
        if activity.checkpoint:
            statements.append("执行记录的进度说明：" + excerpt(activity.checkpoint))
        reading = activity.reading
        label_id = activity.activity_id if len(activity.activity_id) <= 509 else digest(activity.activity_id)
        entities = ["角色活动", "活动:" + label_id]
        if reading is not None:
            sources.append(reading.source)
            coverage_note = "本回执覆盖了这份原文。" if reading.covers_source else "只确认该片段，不代表读完全文。"
            if not reading.covers_source:
                progress = self.life.life.book_progress_for_sources(
                    activity.owner, activity.scope, (reading.source.source_id,), observed_at=at
                ).get(reading.source.source_id)
                if progress and progress.get("completed_book") and progress.get("total", 0) > 1:
                    coverage_note = f"全书共 {progress['total']} 个片段已全部读完，本回执完成了该书的片段。"
            statements.append(f"已读原文的 Unicode 字符区间为 [{reading.start_offset}, {reading.end_offset})，"
                f"原文共 {reading.text_length} 个字符；{coverage_note}")
            if reading.reflection:
                statements.append("阅读后的主观感想（不作为用户事实）：" + excerpt(reading.reflection))
            entities.append("阅读")
        elif activity.work is not None:
            entities.append("活动类型:" + activity.work.kind)
        return ReceiptExperience("角色活动完成：" + label, tuple(statements), tuple(entities),
                                 activity.updated_at, tuple(dict.fromkeys(sources)))

    def _journal(self, context, operation_id, raw, at):
        receipt = journal_codec.receipt_from_json(raw)
        if receipt.operation_id != operation_id:
            raise TaskError("memory_activity_receipt_changed", "journal receipt identity changed")
        # LegacyMigrationWebAPI uses this operation prefix for archived V1
        # journals. Importing an archive is not a new writing experience.
        if receipt.action not in ("save", "restore") or operation_id.startswith("legacy-journal-"):
            return None
        head = receipt.head
        if head.key.owner != self.life.owner(context):
            raise TaskError("memory_activity_receipt_changed", "journal receipt belongs to another role")
        entry = self.life._entry(head.key, context, at)
        if entry.reference.revision != head.revision or entry.updated_at != head.updated_at:
            raise TaskError("journal_dependency_changed", "journal receipt has been replaced or withdrawn")
        # Journal bodies stay in their owner. Their narrative, dreams and
        # summaries must never be republished as independent factual evidence.
        label = "私人日记" if head.key.kind.value == "diary" else "梦境记录"
        statement = (f"角色于 {stamp(head.updated_at)} 保存了 {head.key.local_date} 的{label}（第 {head.revision} 版）。"
            "这只确认保存操作；正文是主观书写，不证明其中事件已经发生。需要内容时回到原日记读取。")
        sources = list(entry.draft.sources)
        for activity in entry.activity_evidence:
            if activity.work is not None and activity.work.source is not None:
                sources.append(activity.work.source)
            if activity.reading is not None:
                sources.append(activity.reading.source)
        return ReceiptExperience(label + "：" + head.key.local_date, (statement,),
            ("私人书写", label, "日期:" + head.key.local_date), head.updated_at, tuple(dict.fromkeys(sources)))
