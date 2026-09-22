import os
import sys
import tempfile
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
