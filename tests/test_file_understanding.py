"""Bounded document extraction never treats attachment text as executable input."""

import base64
import hashlib
import json
import unittest

from astrbot_host.files.loader import load_native_file
from astrbot_host.files.parsing import extract_document
from companion_v2.adapters.astrbot.files import (
    NativeFileLocator,
    prepare_file_text,
)
from companion_v2.configuration.models import ConfigurationError
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.conversation.media.understanding import (
    DEFAULTS,
    DOCUMENT_SUFFIXES,
    SCOPE_PERMISSION_FIELDS,
    TEXT_SUFFIXES,
    intake_fields,
    settings,
)
from companion_v2.conversation.reply_settings import configuration_fields as reply_configuration_fields
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind


class FileUnderstandingTests(unittest.IsolatedAsyncioTestCase):
    def test_plain_text_extraction_keeps_hash_and_truncation(self):
        content = "Hello, 世界".encode("utf-8")
        result = extract_document(content, 4, "notes.txt", maximum_chars=5)

        self.assertEqual(result.position, 4)
        self.assertEqual(result.name, "notes.txt")
        self.assertEqual(result.content_hash, hashlib.sha256(content).hexdigest())
        self.assertEqual(result.byte_count, len(content))
        self.assertEqual(result.text, "Hello")
        self.assertTrue(result.truncated)

    def test_document_formats_and_preferences_are_bounded(self):
        with self.assertRaises(TaskError) as unsupported:
            extract_document(b"text", 0, "report.pdf", formats="text_only")
        self.assertEqual(unsupported.exception.code, "host_file_format_unsupported")

        with self.assertRaises(ConfigurationError):
            settings({**DEFAULTS, "file_understanding_max_size_mb": 17})
        with self.assertRaises(ConfigurationError):
            settings({**DEFAULTS, "file_understanding_formats": "all_files"})

    def test_format_groups_select_supported_types_and_keep_legacy_values(self):
        # 注册默认值是换行分隔的多行文本；后端按同一组分隔符回退，应覆盖
        # 15 个文本后缀 + 4 个文档后缀，共 19 个。
        registered = settings(DEFAULTS)
        self.assertEqual(len(TEXT_SUFFIXES), 15)
        self.assertEqual(len(DOCUMENT_SUFFIXES), 4)
        self.assertEqual(registered["file_understanding_allowed_suffixes"],
                         TEXT_SUFFIXES | DOCUMENT_SUFFIXES)
        self.assertEqual(len(registered["file_understanding_allowed_suffixes"]), 19)

        selected = settings({**DEFAULTS,
            "file_understanding_format_groups": json.dumps(["PDF", "Word（DOCX）"], ensure_ascii=False)})
        self.assertEqual(selected["file_understanding_allowed_suffixes"], {".pdf", ".docx"})
        with self.assertRaises(TaskError) as unsupported:
            extract_document(b"text", 0, "notes.txt",
                formats=selected["file_understanding_allowed_suffixes"])
        self.assertEqual(unsupported.exception.code, "host_file_format_unsupported")

        legacy_values = {key: value for key, value in DEFAULTS.items()
                         if key != "file_understanding_format_groups"}
        legacy = settings({**legacy_values, "file_understanding_formats": "text_only"})
        self.assertEqual(legacy["file_understanding_allowed_suffixes"], TEXT_SUFFIXES)
        with self.assertRaises(ConfigurationError):
            settings({**DEFAULTS, "file_understanding_format_groups": "PDF\nPDF"})
        with self.assertRaises(ConfigurationError):
            settings({**DEFAULTS, "file_understanding_format_groups": "[]"})

    def test_video_self_recognition_hint_is_bounded(self):
        with self.assertRaises(ConfigurationError):
            settings({**DEFAULTS, "video_self_recognition_hint": "x" * 1201})

    def test_intake_fields_match_the_registered_reply_group_contract(self):
        registered = {field.field_id: field for field in
                      reply_configuration_fields(SegmentationPolicy(), True, 0.0)}
        intake = intake_fields()

        self.assertEqual({field.field_id.rsplit(".", 1)[-1] for field in intake
                          if field.field_id.rsplit(".", 1)[-1] in SCOPE_PERMISSION_FIELDS},
                         set(SCOPE_PERMISSION_FIELDS))
        for field in intake:
            self.assertEqual(field, registered[field.field_id])
        FieldRegistry(version="media-understanding.test.v1",
                      fields=tuple(registered.values()))

    async def test_disabled_setting_does_not_call_the_attachment_loader(self):
        async def forbidden(*_args, **_kwargs):
            raise AssertionError("disabled file understanding must not read attachment bytes")

        result = await prepare_file_text(
            (NativeFileLocator(2, "notes.txt", "file-id"),),
            forbidden,
            settings(DEFAULTS),
            client=None,
            scope=None,
        )

        entry = json.loads(result[2].split("\n", 2)[2])
        self.assertEqual(entry["status"], "文件理解未启用，未读取正文")
        self.assertEqual(entry["component_position"], 2)

    async def test_disallowed_scope_does_not_call_the_attachment_loader(self):
        async def forbidden(*_args, **_kwargs):
            raise AssertionError("a disallowed scope must not read attachment bytes")

        preferences = settings({**DEFAULTS, "file_understanding_enabled": True,
                                "file_understanding_private_enabled": False})
        result = await prepare_file_text(
            (NativeFileLocator(1, "notes.txt", "file-id"),), forbidden, preferences,
            client=None, scope=ScopeKind.PRIVATE)

        entry = json.loads(result[1].split("\n", 2)[2])
        self.assertEqual(entry["status"], "当前会话范围未启用文件理解，未读取正文")

    async def test_host_loader_uses_disposable_worker_for_frozen_base64_file(self):
        content = "标题：测试\n正文：仅提取文字".encode("utf-8")
        locator = NativeFileLocator(7, "notes.txt", "base64://" + base64.b64encode(content).decode("ascii"))

        result = await load_native_file(locator, client=None, scope=None, maximum_bytes=1024,
                                        maximum_chars=1000, formats="text_only")

        self.assertEqual(result.position, 7)
        self.assertEqual(result.text, content.decode("utf-8"))
        self.assertEqual(result.content_hash, hashlib.sha256(content).hexdigest())

    async def test_arbitrary_local_paths_are_never_read(self):
        with self.assertRaises(TaskError) as blocked:
            await load_native_file(NativeFileLocator(0, "notes.txt", "C:/private/notes.txt"),
                client=None, scope=None, maximum_bytes=1024, maximum_chars=1000, formats="text_only")
        self.assertEqual(blocked.exception.code, "host_file_location_invalid")


if __name__ == "__main__":
    unittest.main()
