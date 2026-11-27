"""Cross-platform unit tests for companion_v2.storage.local_folder."""

from __future__ import annotations

import ast
import ctypes
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from companion_v2.storage import local_folder


class _FakeRect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _FakeMonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("rcMonitor", _FakeRect),
        ("rcWork", _FakeRect),
        ("dwFlags", ctypes.c_uint),
    ]


class _FakeFlashInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hwnd", ctypes.c_void_p),
        ("dwFlags", ctypes.c_uint),
        ("uCount", ctypes.c_uint),
        ("dwTimeout", ctypes.c_uint),
    ]


def _make_win32_api_mock(
    *,
    com_hr: int = local_folder.S_OK,
    shell_execute_rc: int = 42,
    is_iconic: bool = False,
    attach_ok: bool = True,
    set_fg_ok: bool = True,
    switch_activates: bool = False,
    fg_hwnd: int = 9001,
    fg_tid: int = 101,
    cur_tid: int = 202,
    target_tid: int = 303,
    window_rect: tuple[int, int, int, int] = (100, 100, 900, 700),
    work_rect: tuple[int, int, int, int] = (0, 0, 1600, 952),
):
    ole32 = MagicMock()
    ole32.CoInitializeEx.return_value = com_hr
    oleaut32 = MagicMock()

    shell32 = MagicMock()
    shell32.ShellExecuteW.return_value = shell_execute_rc

    current_fg = [fg_hwnd]
    user32 = MagicMock()
    user32.IsWindow.return_value = True
    user32.IsIconic.return_value = is_iconic
    user32.GetForegroundWindow.side_effect = lambda: current_fg[0]
    user32.GetWindowThreadProcessId.side_effect = lambda hwnd, _pid=None: (
        fg_tid if hwnd == fg_hwnd else target_tid
    )
    user32.AttachThreadInput.return_value = attach_ok

    def fake_set_fg(hwnd):
        if set_fg_ok:
            current_fg[0] = hwnd
        return set_fg_ok

    def fake_switch(hwnd, _alt_tab):
        if switch_activates:
            current_fg[0] = hwnd

    def fake_get_rect(_hwnd, rect_ptr):
        rect = ctypes.cast(rect_ptr, ctypes.POINTER(_FakeRect)).contents
        rect.left, rect.top, rect.right, rect.bottom = window_rect
        return True

    def fake_get_mon_info(_hmon, info_ptr):
        info = ctypes.cast(info_ptr, ctypes.POINTER(_FakeMonitorInfo)).contents
        info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom = work_rect
        info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom = work_rect
        return True

    user32.SetForegroundWindow.side_effect = fake_set_fg
    user32.SwitchToThisWindow.side_effect = fake_switch
    user32.GetWindowRect.side_effect = fake_get_rect
    user32.MonitorFromWindow.return_value = 12345
    user32.GetMonitorInfoW.side_effect = fake_get_mon_info

    kernel32 = MagicMock()
    kernel32.GetCurrentThreadId.return_value = cur_tid

    api = {
        "ole32": ole32,
        "oleaut32": oleaut32,
        "shell32": shell32,
        "user32": user32,
        "kernel32": kernel32,
        "RECT": _FakeRect,
        "MONITORINFO": _FakeMonitorInfo,
        "FLASHWINFO": _FakeFlashInfo,
        "enum_proc_type": object(),
    }
    return api


class LocalFolderOpenerTests(unittest.TestCase):
    def test_module_has_no_top_level_win32_or_ctypes_imports(self):
        source = Path(local_folder.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level_imports = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level_imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level_imports.add(node.module)
        self.assertNotIn("ctypes", top_level_imports)
        self.assertNotIn("ctypes.wintypes", top_level_imports)

    def test_clean_windows_path_and_file_url_conversion(self):
        cleaned = local_folder.clean_windows_path(r"\\?\C:\Users\demo\album")
        self.assertFalse(cleaned.startswith("\\\\?\\"))
        self.assertTrue(cleaned.endswith("album"))

        unc = local_folder.clean_windows_path(r"\\?\UNC\fileserver\share\album")
        self.assertTrue(unc.startswith("\\\\fileserver\\share\\album"))

        decoded = local_folder._file_url_to_windows_path("file:///C:/Users/demo/%E5%9B%BE%E5%BA%93/role-1")
        self.assertIsNotNone(decoded)
        self.assertIn("图库", decoded)
        self.assertTrue(decoded.startswith("C:"))

        unc_url = local_folder._file_url_to_windows_path("file://nas/share/album")
        self.assertEqual(unc_url, r"\\nas\share\album")
        self.assertIsNone(local_folder._file_url_to_windows_path("https://example.com/path"))

        with self.assertRaises(ValueError):
            local_folder.clean_windows_path("   ")

    def test_open_local_folder_rejects_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            with self.assertRaises(FileNotFoundError):
                local_folder.open_local_folder(missing)

    def test_windows_non_interactive_desktop_returns_honest_not_opened_result(self):
        api = _make_win32_api_mock()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(local_folder.os, "name", "nt"), \
                 patch.object(local_folder.os.path, "isdir", return_value=True), \
                 patch.object(local_folder, "_build_win32_api", return_value=api), \
                 patch.object(
                     local_folder,
                     "_windows_interactive_desktop_status",
                     return_value=(False, "winsta=WinSta0 thread_desktop=exebox-1 input_desktop=Default"),
                 ):
                result = local_folder.open_local_folder(tmp)

        self.assertEqual(result.opened, False)
        self.assertEqual(result.reason, "non_interactive_desktop")
        self.assertEqual(result.launcher, "none")
        api["shell32"].ShellExecuteW.assert_not_called()

    def test_windows_reuses_existing_window_restores_clamps_offscreen_and_raises_foreground(self):
        api = _make_win32_api_mock(
            is_iconic=True,
            window_rect=(-1800, 1200, -1000, 1800),
            work_rect=(0, 0, 1600, 952),
        )
        with tempfile.TemporaryDirectory() as tmp:
            raw_target = f"\\\\?\\{tmp}" if os.name == "nt" else tmp
            with patch.object(local_folder.os, "name", "nt"), \
                 patch.object(local_folder.os.path, "isdir", return_value=True), \
                 patch.object(local_folder, "_build_win32_api", return_value=api), \
                 patch.object(local_folder, "_windows_interactive_desktop_status", return_value=(True, "ok")), \
                 patch.object(local_folder, "_list_shell_windows", return_value=[(7777, local_folder.clean_windows_path(tmp))]):
                result = local_folder.open_local_folder(raw_target)

        self.assertTrue(result.opened)
        self.assertEqual(result.reason, "reused")
        self.assertEqual(result.launcher, "existing_explorer")
        self.assertFalse(result.path.startswith("\\\\?\\"))
        api["ole32"].CoInitializeEx.assert_called_once_with(None, local_folder.COINIT_APARTMENTTHREADED)
        api["ole32"].CoUninitialize.assert_called_once()
        api["user32"].AllowSetForegroundWindow.assert_called_once_with(local_folder.ASFW_ANY)
        api["shell32"].ShellExecuteW.assert_not_called()
        api["user32"].ShowWindow.assert_called_once_with(7777, local_folder.SW_RESTORE)
        api["user32"].BringWindowToTop.assert_called_once_with(7777)
        api["user32"].SetForegroundWindow.assert_called_once_with(7777)
        # 1 clamp call + 2 topmost/notopmost calls = 3 SetWindowPos calls
        self.assertEqual(api["user32"].SetWindowPos.call_count, 3)
        clamp_args = api["user32"].SetWindowPos.call_args_list[0][0]
        self.assertGreaterEqual(clamp_args[2], 0)
        self.assertGreaterEqual(clamp_args[3], 0)
        # Paired attach & detach for both fg and target threads
        self.assertEqual(api["user32"].AttachThreadInput.call_count, 4)
        api["user32"].FlashWindowEx.assert_not_called()

    def test_windows_shellexecute_launches_and_uses_switch_to_this_window_when_fg_locked(self):
        api = _make_win32_api_mock(
            com_hr=local_folder.RPC_E_CHANGED_MODE,
            shell_execute_rc=42,
            is_iconic=False,
            set_fg_ok=False,
            switch_activates=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(local_folder.os, "name", "nt"), \
                 patch.object(local_folder.os.path, "isdir", return_value=True), \
                 patch.object(local_folder, "_build_win32_api", return_value=api), \
                 patch.object(local_folder, "_windows_interactive_desktop_status", return_value=(True, "ok")), \
                 patch.object(local_folder, "_list_explorer_windows", return_value=[(1111, "Other", None)]), \
                 patch.object(local_folder, "_find_explorer_window", side_effect=[None, 8888]):
                result = local_folder.open_local_folder(tmp)

        self.assertTrue(result.opened)
        self.assertEqual(result.launcher, "shell_execute")
        # RPC_E_CHANGED_MODE must NOT call CoUninitialize.
        api["ole32"].CoUninitialize.assert_not_called()
        api["shell32"].ShellExecuteW.assert_called_once_with(
            None, "open", result.path, None, None, local_folder.SW_SHOWNORMAL,
        )
        api["user32"].SwitchToThisWindow.assert_called_once_with(8888, True)
        api["user32"].FlashWindowEx.assert_not_called()

    def test_windows_shellexecute_failure_falls_back_to_explorer_and_surfaces_double_failure(self):
        api = _make_win32_api_mock(shell_execute_rc=2)
        with tempfile.TemporaryDirectory() as tmp:
            fake_proc = SimpleNamespace(poll=lambda: None, returncode=None)
            with patch.object(local_folder.os, "name", "nt"), \
                 patch.object(local_folder.os.path, "isdir", return_value=True), \
                 patch.object(local_folder, "_build_win32_api", return_value=api), \
                 patch.object(local_folder, "_windows_interactive_desktop_status", return_value=(True, "ok")), \
                 patch.object(local_folder, "_list_explorer_windows", return_value=[]), \
                 patch.object(local_folder, "_find_explorer_window", side_effect=[None, 9999]), \
                 patch.object(local_folder.subprocess, "Popen", return_value=fake_proc) as mock_popen, \
                 patch.object(local_folder.subprocess, "STARTUPINFO", create=True, return_value=SimpleNamespace(dwFlags=0, wShowWindow=0)), \
                 patch.object(local_folder.subprocess, "STARTF_USESHOWWINDOW", 1, create=True):
                result = local_folder.open_local_folder(tmp)

            self.assertTrue(result.opened)
            self.assertEqual(result.launcher, "explorer")
            popen_args, popen_kwargs = mock_popen.call_args
            self.assertEqual(popen_args[0][0], "explorer.exe")
            self.assertEqual(popen_kwargs["startupinfo"].wShowWindow, local_folder.SW_SHOWNORMAL)
            self.assertTrue(popen_kwargs["startupinfo"].dwFlags & 1)

            api2 = _make_win32_api_mock(shell_execute_rc=5)
            with patch.object(local_folder.os, "name", "nt"), \
                 patch.object(local_folder.os.path, "isdir", return_value=True), \
                 patch.object(local_folder, "_build_win32_api", return_value=api2), \
                 patch.object(local_folder, "_windows_interactive_desktop_status", return_value=(True, "ok")), \
                 patch.object(local_folder, "_list_explorer_windows", return_value=[]), \
                 patch.object(local_folder, "_find_explorer_window", return_value=None), \
                 patch.object(local_folder.subprocess, "Popen", side_effect=OSError("explorer missing")), \
                 patch.object(local_folder.subprocess, "STARTUPINFO", create=True, return_value=SimpleNamespace(dwFlags=0, wShowWindow=0)), \
                 patch.object(local_folder.subprocess, "STARTF_USESHOWWINDOW", 1, create=True):
                with self.assertRaises(OSError):
                    local_folder.open_local_folder(tmp)
            api2["ole32"].CoUninitialize.assert_called_once()

    def test_windows_uipi_denial_falls_back_to_flash_window_cleanly(self):
        api = _make_win32_api_mock(attach_ok=False, set_fg_ok=False, switch_activates=False)
        activated = local_folder._bring_window_to_foreground(api, 5555)
        self.assertFalse(activated)
        api["user32"].FlashWindowEx.assert_called_once()

    def test_macos_desktop_open_and_missing_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok_proc = SimpleNamespace(poll=lambda: None)
            with patch.object(local_folder.os, "name", "posix"), \
                 patch.object(local_folder.sys, "platform", "darwin"), \
                 patch.object(local_folder, "_POSIX_IMMEDIATE_CHECK_SECONDS", 0), \
                 patch.object(local_folder.shutil, "which", return_value="/usr/bin/open"), \
                 patch.object(local_folder.subprocess, "Popen", return_value=ok_proc) as mock_popen:
                result = local_folder.open_local_folder(tmp)
            self.assertEqual(result, local_folder.FolderOpenResult(path=tmp, opened=True, reason="opened", launcher="open"))
            self.assertEqual(mock_popen.call_args[0][0], ["/usr/bin/open", tmp])
            self.assertTrue(mock_popen.call_args[1]["start_new_session"])

            with patch.object(local_folder.os, "name", "posix"), \
                 patch.object(local_folder.sys, "platform", "darwin"), \
                 patch.object(local_folder.shutil, "which", return_value=None):
                missing = local_folder.open_local_folder(tmp)
            self.assertEqual(missing.opened, False)
            self.assertEqual(missing.reason, "headless_environment")

    def test_linux_desktop_xdg_open_gio_fallback_and_immediate_exit_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok_proc = SimpleNamespace(poll=lambda: None)
            # 1. DISPLAY present -> prefers xdg-open
            with patch.object(local_folder.os, "name", "posix"), \
                 patch.object(local_folder.sys, "platform", "linux"), \
                 patch.dict(local_folder.os.environ, {"DISPLAY": ":0"}, clear=True), \
                 patch.object(local_folder, "_POSIX_IMMEDIATE_CHECK_SECONDS", 0), \
                 patch.object(local_folder.shutil, "which", side_effect=lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None), \
                 patch.object(local_folder.subprocess, "Popen", return_value=ok_proc) as mock_popen:
                res_xdg = local_folder.open_local_folder(tmp)
            self.assertTrue(res_xdg.opened)
            self.assertEqual(res_xdg.launcher, "xdg-open")
            self.assertEqual(mock_popen.call_args[0][0], ["/usr/bin/xdg-open", tmp])

            # 2. WAYLAND_DISPLAY present, xdg-open missing -> falls back to gio open
            with patch.object(local_folder.os, "name", "posix"), \
                 patch.object(local_folder.sys, "platform", "linux"), \
                 patch.dict(local_folder.os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True), \
                 patch.object(local_folder, "_POSIX_IMMEDIATE_CHECK_SECONDS", 0), \
                 patch.object(local_folder.shutil, "which", side_effect=lambda name: "/usr/bin/gio" if name == "gio" else None), \
                 patch.object(local_folder.subprocess, "Popen", return_value=ok_proc) as mock_popen:
                res_gio = local_folder.open_local_folder(tmp)
            self.assertTrue(res_gio.opened)
            self.assertEqual(res_gio.launcher, "gio")
            self.assertEqual(mock_popen.call_args[0][0], ["/usr/bin/gio", "open", tmp])

            # 3. Immediate non-zero exit raises OSError
            bad_proc = SimpleNamespace(poll=lambda: 3)
            with patch.object(local_folder.os, "name", "posix"), \
                 patch.object(local_folder.sys, "platform", "linux"), \
                 patch.dict(local_folder.os.environ, {"DISPLAY": ":0"}, clear=True), \
                 patch.object(local_folder, "_POSIX_IMMEDIATE_CHECK_SECONDS", 0), \
                 patch.object(local_folder.shutil, "which", return_value="/usr/bin/xdg-open"), \
                 patch.object(local_folder.subprocess, "Popen", return_value=bad_proc):
                with self.assertRaises(OSError):
                    local_folder.open_local_folder(tmp)

    def test_linux_headless_docker_returns_not_opened_without_spawning_or_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 1. Neither DISPLAY nor WAYLAND_DISPLAY set (Docker / SSH / server)
            with patch.object(local_folder.os, "name", "posix"), \
                 patch.object(local_folder.sys, "platform", "linux"), \
                 patch.dict(local_folder.os.environ, {}, clear=True), \
                 patch.object(local_folder.subprocess, "Popen", side_effect=AssertionError("must not spawn")):
                res = local_folder.open_local_folder(tmp)
            self.assertEqual(res, local_folder.FolderOpenResult(
                path=tmp, opened=False, reason="headless_environment", launcher="none",
            ))

            # 2. DISPLAY set, but neither xdg-open nor gio installed
            with patch.object(local_folder.os, "name", "posix"), \
                 patch.object(local_folder.sys, "platform", "linux"), \
                 patch.dict(local_folder.os.environ, {"DISPLAY": ":0"}, clear=True), \
                 patch.object(local_folder.shutil, "which", return_value=None), \
                 patch.object(local_folder.subprocess, "Popen", side_effect=AssertionError("must not spawn")):
                res_no_bin = local_folder.open_local_folder(tmp)
            self.assertEqual(res_no_bin.opened, False)
            self.assertEqual(res_no_bin.reason, "headless_environment")

    def test_extract_address_path_handles_localized_explorer_labels(self):
        self.assertEqual(
            local_folder._extract_address_path("地址: C:\\Users\\demo\\generated-album"),
            "C:\\Users\\demo\\generated-album",
        )
        self.assertEqual(
            local_folder._extract_address_path("Address: D:\\Album\\role-1"),
            "D:\\Album\\role-1",
        )
        self.assertIsNone(local_folder._extract_address_path("Navigation Buttons"))


if __name__ == "__main__":
    unittest.main()
