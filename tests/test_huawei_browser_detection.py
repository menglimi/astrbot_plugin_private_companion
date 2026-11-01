"""Browser discovery works on Linux containers and PATH-only installs."""

import unittest
from unittest import mock

from astrbot_host.huawei_health.vendor import hw_auth


class BrowserDetectionTests(unittest.TestCase):
    def test_path_lookup_finds_chromium_when_fixed_paths_are_absent(self):
        def which(name):
            return "/usr/bin/chromium" if name == "chromium" else None

        with mock.patch.object(hw_auth.os.path, "exists", return_value=False), \
                mock.patch.object(hw_auth.shutil, "which", side_effect=which):
            self.assertEqual(hw_auth.find_browser(), "/usr/bin/chromium")

    def test_explicit_missing_path_falls_back_to_detection(self):
        with mock.patch.object(hw_auth.os.path, "exists", return_value=False), \
                mock.patch.object(hw_auth.shutil, "which", return_value=None):
            self.assertEqual(hw_auth.find_browser("/no/such/browser"), "")


if __name__ == "__main__":
    unittest.main()
