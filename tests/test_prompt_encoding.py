import hashlib
import unittest

from companion_v2.execution.models import digest, encode, encode_prompt


class PromptEncodingTests(unittest.TestCase):
    def test_prompt_material_keeps_cjk_literal_while_records_stay_ascii(self):
        value = {"text": "晚上一起吃饭吗？", "n": 1}
        record = encode(value)
        prompt = encode_prompt(value)
        self.assertIn("\\u", record)
        self.assertNotIn("\\u", prompt)
        self.assertIn("晚上一起吃饭吗？", prompt)
        self.assertLess(len(prompt), len(record))

    def test_digest_stays_bound_to_the_ascii_record_encoding(self):
        value = {"text": "晚上一起吃饭吗？"}
        self.assertEqual(digest(value), hashlib.sha256(encode(value).encode("utf-8")).hexdigest())


if __name__ == "__main__":
    unittest.main()
