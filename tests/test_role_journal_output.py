"""Synthetic output boundaries without logging or persisting private prose."""

import json
from types import SimpleNamespace as NS
import unittest

from companion_v2.foundation.refs import RoleOwnerRef, ScopeKind, ScopeRef
from companion_v2.role_work.journal.models import JournalContext, JournalKey, JournalKind
from companion_v2.role_work.journal.writing_output import JournalOutputError, draft_output


class DiaryOutputTests(unittest.TestCase):
    def setUp(self):
        scope = ScopeRef("journal-output-test", "onebot", ScopeKind.PRIVATE, "user-a",
                         "bot-a", "account-a", "conversation-a", "session-a")
        owner = RoleOwnerRef(scope.installation_id, scope.bot_id, "role-a")
        target = JournalContext(JournalKey(owner, JournalKind.DIARY, "2026-11-07"), scope, "Asia/Shanghai")
        self.job = NS(target=target, sources=(), activities=(), recent=(), materials=())

    def test_valid_diary_body_does_not_adopt_dream_only_fields(self):
        output = json.dumps({"body": "今天写完了这页。", "form": "随记",
                             "recall": {"unexpected": "dream"}, "afterglow": ["这段不能变成日记正文。"]},
                            ensure_ascii=False)
        draft = draft_output(output, self.job)
        self.assertEqual(draft.body, "今天写完了这页。")
        self.assertIsNone(draft.recall)
        self.assertEqual(draft.afterglow, "")

    def test_missing_body_is_classified_without_exposing_extra_text(self):
        output = json.dumps({"body": "", "form": "随记", "recall": None,
                             "afterglow": "私人梦境内容"}, ensure_ascii=False)
        with self.assertRaises(JournalOutputError) as error:
            draft_output(output, self.job)
        self.assertEqual(error.exception.code, "journal_diary_body_missing")
        self.assertNotIn("私人梦境内容", str(error.exception))


if __name__ == "__main__":
    unittest.main()
