import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


MODEL_SCRIPT = Path("/Users/leo/Downloads/models/hdw-fasteners-detect/infer.py")


def load_module():
    spec = importlib.util.spec_from_file_location("hdw_fasteners_infer_test", MODEL_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class HdwModelRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_provider_priority_uses_actual_runtime_capability(self):
        self.assertEqual(
            self.module.OnnxYoloDetector._provider_candidates({
                "runtime": {"availableProviders": [
                    "CUDAExecutionProvider", "CPUExecutionProvider"
                ]}
            }),
            ["CUDAExecutionProvider", "CPUExecutionProvider"],
        )

    def test_session_retries_with_cpu_after_accelerator_failure(self):
        calls = []

        def create(path, providers):
            calls.append(providers)
            if providers != ["CPUExecutionProvider"]:
                raise RuntimeError("accelerator failed")
            return object()

        with patch.object(self.module.ort, "InferenceSession", side_effect=create):
            session = self.module.OnnxYoloDetector._create_session(
                "model.onnx", {"runtime": {"availableProviders": [
                    "CoreMLExecutionProvider", "CPUExecutionProvider"
                ]}}
            )
        self.assertIsNotNone(session)
        self.assertEqual(calls[-1], ["CPUExecutionProvider"])


if __name__ == "__main__":
    unittest.main()
