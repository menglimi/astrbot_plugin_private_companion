"""Single-participant inference for the default expression learning source."""

import unittest
from types import SimpleNamespace as NS

from companion_v2.expression.configuration import _sole_private_participant


class FakeRuntime:
    def __init__(self, rows, *, fail=False):
        self.rows, self.fail = rows, fail
        self.queries = []

    def read_all(self, query, parameters):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("table unavailable")
        return self.rows


def context():
    scope = NS(bot_id="bot-a", installation_id="installation")
    role = NS(subject_id="role-a")
    return NS(scope=scope, role_binding=NS(role=role))


class SolePrivateParticipantTests(unittest.TestCase):
    def test_one_participant_becomes_the_implicit_main_companion(self):
        runtime = FakeRuntime([{"subject": "onebot-user:1"}])
        self.assertEqual(_sole_private_participant(runtime, context()), "onebot-user:1")

    def test_several_participants_stay_ambiguous(self):
        runtime = FakeRuntime([{"subject": "onebot-user:1"}, {"subject": "onebot-user:2"}])
        self.assertIsNone(_sole_private_participant(runtime, context()))

    def test_missing_tables_do_not_break_the_default(self):
        runtime = FakeRuntime([{"subject": "onebot-user:1"}], fail=True)
        self.assertIsNone(_sole_private_participant(runtime, context()))


if __name__ == "__main__":
    unittest.main()
