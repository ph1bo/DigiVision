import ctypes
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.services import camera


class MvsRuntimeTests(unittest.TestCase):
    def test_windows_runtime_is_registered_before_python_wrapper_import(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime_dir = Path(directory) / "Win64_x64"
            runtime_dir.mkdir()
            (runtime_dir / "MvCameraControl.dll").touch()
            dll_handle = object()

            def import_wrapper(name):
                self.assertEqual(name, "MvCameraControl_class")
                self.assertEqual(os.environ["PATH"].split(os.pathsep)[0], str(runtime_dir))
                return object()

            runtime = camera.MvsRuntime()
            with (
                patch.object(sys, "platform", "win32"),
                patch.object(runtime, "_candidate_paths", return_value=[]),
                patch.object(
                    runtime,
                    "_candidate_runtime_paths",
                    return_value=[str(runtime_dir)],
                    create=True,
                ),
                patch.object(os, "add_dll_directory", return_value=dll_handle, create=True),
                patch.object(camera.importlib, "import_module", side_effect=import_wrapper),
                patch.dict(os.environ, {"PATH": "existing-path"}),
            ):
                self.assertTrue(runtime.available, runtime.error)

            self.assertEqual(runtime._dll_directory_handles, [dll_handle])


class _FakeFrameInfo:
    nWidth = 4
    nHeight = 2
    enPixelType = camera.PIXEL_TYPE_MONO8
    nFrameLen = 8
    nPaddingSize = 0


class _FakeCam:
    """只实现 grab() 用到的最小接口；GetImageBuffer 返回码序列可编排。"""

    def __init__(self, results: list[int]) -> None:
        self._results = list(results)
        self.timeouts: list[int] = []

    def MV_CC_GetImageBuffer(self, st_frame, timeout_ms):
        self.timeouts.append(int(timeout_ms))
        return self._results.pop(0) if self._results else 0

    def MV_CC_FreeImageBuffer(self, st_frame):
        return 0


def _make_runtime(results: list[int]) -> tuple[camera.MvsRuntime, _FakeCam]:
    """搭一个能走通 grab() 全流程的最小假环境（4x2 Mono8，像素值 100）。"""
    buffer = (ctypes.c_ubyte * 8).from_buffer_copy(bytes([100] * 8))

    class _FakeFrame:
        def __init__(self) -> None:
            self.stFrameInfo = _FakeFrameInfo()
            self.pBufAddr = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))

    runtime = camera.MvsRuntime()
    cam = _FakeCam(results)
    runtime._module = SimpleNamespace(MV_FRAME_OUT=_FakeFrame)
    runtime._handles["S1"] = cam
    return runtime, cam


class GrabTimeoutFloorTests(unittest.TestCase):
    """采集超时保护性下限：历史前端默认给过 500μs（换算后 1ms），物理上不可能出图。"""

    def test_grab_floors_absurdly_small_timeout(self):
        runtime, cam = _make_runtime([0])
        try:
            frame = runtime.grab("S1", timeout_ms=1, params=None)
        finally:
            runtime._handles.clear()
        self.assertEqual(frame.shape, (2, 4, 3))
        self.assertEqual(cam.timeouts, [500])

    def test_grab_keeps_larger_timeout(self):
        runtime, cam = _make_runtime([0])
        try:
            runtime.grab("S1", timeout_ms=1500, params=None)
        finally:
            runtime._handles.clear()
        self.assertEqual(cam.timeouts, [1500])

    def test_grab_retries_nodata_within_floored_budget(self):
        """首次 NODATA 后在钳制后的预算内重试并成功。"""
        runtime, cam = _make_runtime([camera.MV_E_NODATA, 0])
        try:
            frame = runtime.grab("S1", timeout_ms=1, params=None)
        finally:
            runtime._handles.clear()
        self.assertEqual(frame.shape, (2, 4, 3))
        self.assertEqual(len(cam.timeouts), 2)
        self.assertTrue(all(t >= 500 for t in cam.timeouts))


if __name__ == "__main__":
    unittest.main()
