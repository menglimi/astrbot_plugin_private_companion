"""Cross-platform unit tests for companion_v2.storage.local_folder."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from companion_v2.storage import local_folder


def _make_win32_mocks(
    *,
    com_hr: int = local_folder.S_OK,
    shell_execute_rc: int = 42,
    is_iconic: bool = False,
    attach_ok: bool = True,
    set_fg_ok: bool = True,
    fg_hwnd: int = 9001,
    fg_tid: int = 101,
    cur_tid: int = 202,
    target_tid: int = 303,
):
    ole32 = MagicMock()
    ole32.CoInitializeEx.return_value = com_hr

    shell32 = MagicMock()
    shell32.ShellExecuteW.return_value = shell_execute_rc

    user32 = MagicMock()
    user32.IsWindow.return_value = True
    user32.IsIconic.return_value = is_iconic
    user32.GetForegroundWindow.return_value = fg_hwnd
    user32.GetWindowThreadProcessId.side_effect = lambda hwnd, _pid=None: (
        fg_tid if hwnd == fg_hwnd else target_tid
    )
    user32.AttachThreadInput.return_value = attach_ok
    user32.SetForegroundWindow.return_value = set_fg_ok

    kernel32 = MagicMock()
    kernel32.GetCurrentThreadId.return_value = cur_tid

    class FakeFlashInfo:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    return ole32, shell32, user32, kernel32, FakeFlashInfo, object()


class LocalFolderOpenerTests(unittest.TestCase):
    def test_clean_windows_path_strips_extended_and_unc_prefixes(self):
        cleaned = local_folder.clean_windows_path(r"\\?\C:\Users\demo\album")
        self.assertFalse(cleaned.startswith("\\\\?\\"))
        self.assertTrue(cleaned.endswith("album"))

        unc = local_folder.clean_windows_path(r"\\?\UNC\fileserver\share\album")
        self.assertTrue(unc.startswith("\\\\fileserver\\share\\album"))

        with self.assertRaises(ValueError):
            local_folder.clean_windows_path("   ")

    def test_open_local_folder_rejects_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            with self.assertRaises(FileNotFoundError):
                local_folder.open_local_folder(missing)

    def test_windows_reuses_already_open_explorer_window_and_restores_to_foreground(self):
        ole32, shell32, user32, kernel32, flash_cls, enum_type = _make_win32_mocks(is_iconic=True)
        with tempfile.TemporaryDirectory() as tmp:
            raw_target = f"\\\\?\\{tmp}" if os.name == "nt" else tmp
            with patch.object(local_folder.os, "name", "nt"), \
                 patch.object(local_folder.os.path, "isdir", return_value=True), \
                 patch.object(local_folder, "_build_win32_api", return_value=(ole32, shell32, user32, kernel32, flash_cls, enum_type)), \
                 patch.object(local_folder, "_find_explorer_window", return_value=7777):
                opened = local_folder.open_local_folder(raw_target)

        self.assertFalse(opened.startswith("\\\\?\\"))
        ole32.CoInitializeEx.assert_called_once_with(None, local_folder.COINIT_APARTMENTTHREADED)
        ole32.CoUninitialize.assert_called_once()
        user32.AllowSetForegroundWindow.assert_called_once_with(local_folder.ASFW_ANY)
        shell32.ShellExecuteW.assert_not_called()
        user32.ShowWindow.assert_called_once_with(7777, local_folder.SW_RESTORE)
        user32.BringWindowToTop.assert_called_once_with(7777)
        user32.SetForegroundWindow.assert_called_once_with(7777)
        # AttachThreadInput paired attach & detach for both fg and target threads.
        self.assertEqual(user32.AttachThreadInput.call_count, 4)
        self.assertEqual(user32.SetWindowPos.call_count, 2)
        user32.FlashWindowEx.assert_not_called()

    def test_windows_shellexecute_launches_and_lifts_new_explorer_window(self):
        ole32, shell32, user32, kernel32, flash_cls, enum_type = _make_win32_mocks(
            com_hr=local_folder.RPC_E_CHANGED_MODE,
            shell_execute_rc=42,
            is_iconic=False,
        )
        find_results = [None, 8888]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(local_folder.os, "name", "nt"), \
                 patch.object(local_folder.os.path, "isdir", return_value=True), \
                 patch.object(local_folder, "_build_win32_api", return_value=(ole32, shell32, user32, kernel32, flash_cls, enum_type)), \
                 patch.object(local_folder, "_list_explorer_windows", return_value=[(1111, "Other", None)]), \
                 patch.object(local_folder, "_find_explorer_window", side_effect=find_results):
                opened = local_folder.open_local_folder(tmp)

        self.assertEqual(opened, local_folder.clean_windows_path(tmp))
        # RPC_E_CHANGED_MODE must NOT call CoUninitialize.
        ole32.CoUninitialize.assert_not_called()
        shell32.ShellExecuteW.assert_called_once_with(
            None, "open", opened, None, None, local_folder.SW_SHOWNORMAL,
        )
        user32.ShowWindow.assert_called_once_with(8888, local_folder.SW_SHOW)
        user32.SetForegroundWindow.assert_called_once_with(8888)

    def test_windows_shellexecute_failure_falls_back_to_explorer_and_surfaces_double_failure(self):
        ole32, shell32, user32, kernel32, flash_cls, enum_type = _make_win32_mocks(shell_execute_rc=2)
        with tempfile.TemporaryDirectory() as tmp:
            # 1. ShellExecuteW <= 32 triggers explorer.exe fallback with SW_SHOWNORMAL.
            fake_proc = SimpleNamespace(poll=lambda: None, returncode=None)
            with patch.object(local_folder.os, "name", "nt"), \
                 patch.object(local_folder.os.path, "isdir", return_value=True), \
                 patch.object(local_folder, "_build_win32_api", return_value=(ole32, shell32, user32, kernel32, flash_cls, enum_type)), \
                 patch.object(local_folder, "_list_explorer_windows", return_value=[]), \
                 patch.object(local_folder, "_find_explorer_window", side_effect=[None, 9999]), \
                 patch.object(local_folder.subprocess, "Popen", return_value=fake_proc) as mock_popen, \
                 patch.object(local_folder.subprocess, "STARTUPINFO", create=True, return_value=SimpleNamespace(dwFlags=0, wShowWindow=0)), \
                 patch.object(local_folder.subprocess, "STARTF_USESHOWWINDOW", 1, create=True):
                local_folder.open_local_folder(tmp)

            mock_popen.assert_called_once()
            popen_args, popen_kwargs = mock_popen.call_args
            self.assertEqual(popen_args[0][0], "explorer.exe")
            self.assertEqual(popen_kwargs["startupinfo"].wShowWindow, local_folder.SW_SHOWNORMAL)
            self.assertTrue(popen_kwargs["startupinfo"].dwFlags & 1)
            user32.SetForegroundWindow.assert_called_once_with(9999)

            # 2. Both ShellExecuteW <= 32 and explorer.exe OSError raise OSError (no fake success).
            ole32_2, shell32_2, user32_2, kernel32_2, flash_cls_2, enum_type_2 = _make_win32_mocks(shell_execute_rc=5)
            with patch.object(local_folder.os, "name", "nt"), \
                 patch.object(local_folder.os.path, "isdir", return_value=True), \
                 patch.object(local_folder, "_build_win32_api", return_value=(ole32_2, shell32_2, user32_2, kernel32_2, flash_cls_2, enum_type_2)), \
                 patch.object(local_folder, "_list_explorer_windows", return_value=[]), \
                 patch.object(local_folder, "_find_explorer_window", return_value=None), \
                 patch.object(local_folder.subprocess, "Popen", side_effect=OSError("explorer missing")), \
                 patch.object(local_folder.subprocess, "STARTUPINFO", create=True, return_value=SimpleNamespace(dwFlags=0, wShowWindow=0)), \
                 patch.object(local_folder.subprocess, "STARTF_USESHOWWINDOW", 1, create=True):
                with self.assertRaises(OSError):
                    local_folder.open_local_folder(tmp)
            ole32_2.CoUninitialize.assert_called_once()

    def test_windows_uipi_denial_falls_back_to_flash_window_cleanly(self):
        ole32, shell32, user32, kernel32, _flash_cls, enum_type = _make_win32_mocks(
            attach_ok=False, set_fg_ok=False,
        )
        # Use a real ctypes Structure so ctypes.sizeof / ctypes.byref succeed on all platforms.
        import ctypes
        class RealFlashInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("hwnd", ctypes.c_void_p),
                ("dwFlags", ctypes.c_uint),
                ("uCount", ctypes.c_uint),
                ("dwTimeout", ctypes.c_uint),
            ]
        activated = local_folder._bring_window_to_foreground(user32, kernel32, RealFlashInfo, 5555)
        self.assertFalse(activated)
        user32.FlashWindowEx.assert_called_once()

    def test_posix_launch_and_immediate_exit_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok_proc = SimpleNamespace(poll=lambda: None)
            with patch.object(local_folder.os, "name", "posix"), \
                 patch.object(local_folder.sys, "platform", "darwin"), \
                 patch.object(local_folder.subprocess, "Popen", return_value=ok_proc) as mock_popen:
                self.assertEqual(local_folder.open_local_folder(tmp), tmp)
            self.assertEqual(mock_popen.call_args[0][0], ["open", tmp])
            self.assertTrue(mock_popen.call_args[1]["start_new_session"])

            bad_proc = SimpleNamespace(poll=lambda: 1)
            with patch.object(local_folder.os, "name", "posix"), \
                 patch.object(local_folder.sys, "platform", "linux"), \
                 patch.object(local_folder.subprocess, "Popen", return_value=bad_proc) as mock_popen:
                with self.assertRaises(OSError):
                    local_folder.open_local_folder(tmp)
            self.assertEqual(mock_popen.call_args[0][0], ["xdg-open", tmp])

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
