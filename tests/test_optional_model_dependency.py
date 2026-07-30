# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.helpers import _missing_optional_model_dependency


class OptionalModelDependencyTests(unittest.TestCase):
    def test_direct_missing_torch_is_detected(self) -> None:
        error = ModuleNotFoundError("No module named 'torch'", name="torch")
        self.assertEqual("torch", _missing_optional_model_dependency(error))

    def test_nested_torch_module_is_normalized_to_dependency_root(self) -> None:
        error = ModuleNotFoundError("No module named 'torch.nn'", name="torch.nn")
        self.assertEqual("torch", _missing_optional_model_dependency(error))

    def test_wrapped_optional_dependency_is_detected(self) -> None:
        try:
            try:
                raise ModuleNotFoundError("No module named 'torchvision'", name="torchvision")
            except ModuleNotFoundError as missing:
                raise RuntimeError("视觉模型初始化失败") from missing
        except RuntimeError as wrapped:
            self.assertEqual("torchvision", _missing_optional_model_dependency(wrapped))

    def test_non_optional_dependency_is_not_suppressed(self) -> None:
        error = ModuleNotFoundError("No module named 'required_package'", name="required_package")
        self.assertEqual("", _missing_optional_model_dependency(error))


if __name__ == "__main__":
    unittest.main()
