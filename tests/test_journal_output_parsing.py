import unittest


class JournalOutputParsingTests(unittest.TestCase):
    def test_object_output_accepts_a_markdown_fence(self):
        from companion_v2.role_work.journal.writing_output import object_output

        value = object_output('```json\n{"body": "今天很安静"}\n```')
        self.assertEqual(value, {"body": "今天很安静"})

    def test_object_output_still_rejects_duplicate_keys(self):
        from companion_v2.role_work.journal.writing_output import object_output

        with self.assertRaises(ValueError):
            object_output('{"body": "甲", "body": "乙"}')


if __name__ == "__main__":
    unittest.main()
