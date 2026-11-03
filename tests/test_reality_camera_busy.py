"""A camera already in use must not be re-opened or queued behind another capture."""

import asyncio
import unittest

from astrbot_host.native_extensions.reality.reality_touch_camera import RealityTouchCameraMixin


class CameraBusyTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_observation_reports_busy_instead_of_waiting(self):
        class CameraHost(RealityTouchCameraMixin):
            enable_experimental_bluetooth_wakeup = True
            enable_reality_touch_camera = True

        host = CameraHost()
        lock = asyncio.Lock()
        await lock.acquire()
        host._reality_touch_camera_operation_lock = lock
        try:
            result = await host._reality_touch_camera_snapshot_for_user("user-1", "看看摄像头")
        finally:
            lock.release()
        self.assertEqual(result["status"], "busy")
        self.assertIn("不抢占", result["message"])


if __name__ == "__main__":
    unittest.main()
