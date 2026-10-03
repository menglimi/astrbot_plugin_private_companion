# -*- coding: utf-8 -*-
"""REQ-041 权威私聊记忆的持久化完整性回归测试。

背景：`_req041_private_memory` 分区的每条记录都带有覆盖其 `content` 的
`content_hash`，而 `AuthoritativePrivateMemoryStore.read()` 会在哈希不匹配时
抛出 `private_memory_record_invalid`，导致记忆层读写双双停摆。

持久化前的控制标签清洗（`_sanitize_store_control_tags_inplace`）会就地改写
该分区内的字符串（例如把「瞎折腾。 ……洗完了」里的空格按
`\\s+([，,。！？!?；;：:、~～…])` 规则吃掉），却不会重算 `content_hash`，
于是写入成功后每次读取都失败。

这些测试锁定：清洗不得触碰 `_req041_private_memory`。
"""
from __future__ import annotations

import hashlib
import json
import unittest

from astrbot_plugin_private_companion.authoritative_private_memory import (
    AuthoritativePrivateMemoryStore,
)
from astrbot_plugin_private_companion.core_store import CoreStoreMixin


class _SanitizerHarness(CoreStoreMixin):
    def __init__(self, enabled: bool = True) -> None:
        self.enable_store_control_tag_sanitization = enabled


def _digest(value) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Req041PrivateMemoryPersistenceTests(unittest.TestCase):
    """清洗器必须跳过带 content_hash 校验的 REQ-041 私聊记忆分区。"""

    # 触发 \s+([，,。！？!?；;：:、~～…]) 规则的最小样本：
    # 句号后的空格会被吃掉，字符串内容随之改变。
    _SPACE_BEFORE_PUNCT = "……洗完了就赶紧去吃点东西，别空着肚子瞎折腾。 ……洗完了"

    def _snapshot_with_memory(self, text: str) -> dict:
        """构造一个内容与 content_hash 自洽的 REQ-041 快照。"""
        content = {"recent_reply_topics": [{"text": text}]}
        return {
            "_req041_private_memory": {
                "schema": "req041.person_private_memory.v1",
                "records": {
                    "person_under_test": {
                        "schema": "req041.person_private_memory.v1",
                        "revision": 1,
                        "content": content,
                        "content_hash": _digest(content),
                        "updated_at": 1.0,
                        "last_operation_hash": "op",
                        "last_request_hash": "req",
                    }
                },
            }
        }

    def test_req041_private_memory_is_exempt_from_persistence_cleanup(self) -> None:
        """该分区内的字符串不应被清洗改写（豁免判定）。"""
        path = (
            "_req041_private_memory", "records", "person_under_test",
            "content", "recent_reply_topics", 0, "text",
        )

        self.assertTrue(
            CoreStoreMixin._store_path_is_raw_user_text(path),
            "REQ-041 私聊记忆携带 content_hash 校验，持久化清洗必须跳过它",
        )

    def test_sanitizer_preserves_req041_content_and_hash(self) -> None:
        """跑完整清洗后，记忆内容与哈希都必须保持自洽。"""
        snapshot = self._snapshot_with_memory(self._SPACE_BEFORE_PUNCT)
        record = snapshot["_req041_private_memory"]["records"]["person_under_test"]
        before_text = record["content"]["recent_reply_topics"][0]["text"]
        before_hash = record["content_hash"]

        _SanitizerHarness(True)._sanitize_store_control_tags_inplace(snapshot)

        after_record = snapshot["_req041_private_memory"]["records"]["person_under_test"]
        after_text = after_record["content"]["recent_reply_topics"][0]["text"]

        self.assertEqual(
            before_text, after_text,
            "清洗改写了 REQ-041 记忆内容，但不会重算 content_hash",
        )
        self.assertEqual(before_hash, after_record["content_hash"])

        # 端到端：清洗之后必须仍然可读。
        result = AuthoritativePrivateMemoryStore(snapshot).read("person_under_test")
        self.assertTrue(result.get("ok"), f"read() 失败: {result}")

    def test_read_rejects_tampered_content_without_hash_update(self) -> None:
        """反向锁定：内容被改而哈希未更新时，read() 必须报错。

        这正是线上故障的形态——若此断言失效，说明校验被削弱。
        注意错误码在不同版本间有演进（旧版 `private_memory_record_invalid`，
        新版细分为 `private_memory_hash_mismatch`），故匹配共同前缀。
        """
        snapshot = self._snapshot_with_memory(self._SPACE_BEFORE_PUNCT)
        record = snapshot["_req041_private_memory"]["records"]["person_under_test"]
        # 模拟「清洗吃掉空格、哈希未重算」后的落盘状态。
        record["content"]["recent_reply_topics"][0]["text"] = (
            record["content"]["recent_reply_topics"][0]["text"].replace("。 ……", "。……")
        )

        with self.assertRaises(Exception) as ctx:
            AuthoritativePrivateMemoryStore(snapshot).read("person_under_test")

        message = str(ctx.exception)
        self.assertTrue(
            "private_memory_hash_mismatch" in message
            or "private_memory_record_invalid" in message,
            f"期望哈希失配类错误，实际为: {message}",
        )

    def test_other_sections_still_get_cleaned(self) -> None:
        """豁免必须精确：其它分区仍应正常清理「非标准」伪控制标签。

        使用模型误生成形态的伪标签作为样本；标准标签不在此清洗器的职责内。
        """
        leaked = "残留 <pc_history_media_records=\"1\" /> 内容"
        snapshot = {
            "memory": {"text": leaked},
            "users": {"10001": {"nickname": leaked}},
        }

        _SanitizerHarness(True)._sanitize_store_control_tags_inplace(snapshot)

        self.assertNotIn("pc_history_media_records", snapshot["memory"]["text"])
        self.assertNotIn("pc_history_media_records", snapshot["users"]["10001"]["nickname"])

    def test_existing_exemptions_still_hold(self) -> None:
        """原有的三类豁免不得被破坏。"""
        cases = [
            ("groups", "1", "members", "2", "recent_phrases", 0),
            ("memo_notes", 0, "content"),
            ("memo_notes", 0, "title"),
            ("groups", "1", "recent_messages", "3", "text"),
        ]
        for path in cases:
            with self.subTest(path=path):
                self.assertTrue(CoreStoreMixin._store_path_is_raw_user_text(path))


if __name__ == "__main__":
    unittest.main()
