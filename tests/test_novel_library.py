from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from companion_v2.together.novel_library import NovelLibrary


class NovelLibraryTests(unittest.TestCase):
    def test_role_reading_chapters_preserve_the_imported_chapter_text(self):
        with TemporaryDirectory(prefix="novel-library-") as directory:
            library = NovelLibrary(Path(directory))
            book = library.import_bytes(
                "story.txt",
                "第一章\n河边的风很轻。\n她停下脚步。\n第二章\n天已经亮了。".encode("utf-8"),
            )

            chapters = library.reading_chapters(book["id"])

        self.assertEqual(
            chapters,
            [
                {"chapter_index": 0, "title": "第一章", "content": "河边的风很轻。\n她停下脚步。"},
                {"chapter_index": 1, "title": "第二章", "content": "天已经亮了。"},
            ],
        )

    def test_role_reading_chapters_enforce_the_character_limit(self):
        with TemporaryDirectory(prefix="novel-library-") as directory:
            library = NovelLibrary(Path(directory))
            book = library.import_bytes(
                "story.txt",
                "第一章\n河边的风很轻。\n她停下脚步。".encode("utf-8"),
            )

            chapters = library.reading_chapters(book["id"])
            exact_limit = len(chapters[0]["content"])
            within_limit = library.reading_chapters(book["id"], max_chars=exact_limit)
            with self.assertRaisesRegex(ValueError, "最多加入"):
                library.reading_chapters(book["id"], max_chars=exact_limit - 1)

        self.assertEqual(within_limit, chapters)
