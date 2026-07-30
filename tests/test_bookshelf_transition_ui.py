import unittest
from pathlib import Path


class BookshelfTransitionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.html = (root / "pages" / "陪伴面板" / "index.html").read_text(encoding="utf-8")
        cls.script = (root / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        cls.css = (root / "pages" / "陪伴面板" / "app.css").read_text(encoding="utf-8")

    def test_bookshelf_views_have_stable_transition_markers(self):
        self.assertIn('class="bookcase-home bookshelf-view" data-bookshelf-view="shelf"', self.html)
        self.assertIn('class="book-detail-panel bookshelf-view" data-bookshelf-view="detail"', self.html)

    def test_internal_navigation_uses_one_direction_aware_controller(self):
        self.assertIn("function transitionBookshelfPage(nextPage, options = {})", self.script)
        self.assertIn('transitionBookshelfPage("detail"', self.script)
        self.assertIn('transitionBookshelfPage("reader"', self.script)
        self.assertIn('transitionBookshelfPage("shelf"', self.script)
        self.assertIn("previousTransition?.skipTransition?.()", self.script)
        self.assertIn('matchMedia?.("(prefers-reduced-motion: reduce)")', self.script)
        self.assertIn("cancelBookshelfTransition();", self.script)
        self.assertIn("if (activeTabTransition)", self.script)
        self.assertIn("if (!committed)", self.script)

    def test_internal_snapshot_does_not_reuse_global_panel_snapshot(self):
        self.assertIn('html[data-bookshelf-transition="active"] #panel-bookshelf.panel.is-active', self.css)
        self.assertIn("view-transition-name: bookshelf-view", self.css)
        self.assertIn("::view-transition-old(bookshelf-view)", self.css)
        self.assertIn("bookshelf-view-enter-backward", self.css)
        self.assertIn("::view-transition-group(bookshelf-view)", self.css)


if __name__ == "__main__":
    unittest.main()
