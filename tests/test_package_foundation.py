import importlib
import importlib.metadata
import importlib.resources
import unittest
from pathlib import Path

import aiteqno


EXPECTED_LAYERS = (
    "aiteqno.domain",
    "aiteqno.ports",
    "aiteqno.application",
    "aiteqno.adapters",
    "aiteqno.cli",
)


class PackageFoundationTest(unittest.TestCase):
    def test_public_version_matches_distribution_metadata(self):
        installed_version = importlib.metadata.version("aiteqno")

        self.assertEqual(aiteqno.__version__, installed_version)
        self.assertEqual(installed_version, "0.5.0.dev0")

    def test_architecture_layers_are_importable(self):
        for module_name in EXPECTED_LAYERS:
            with self.subTest(module=module_name):
                self.assertIsNotNone(importlib.import_module(module_name))

    def test_package_uses_src_layout(self):
        repository_root = Path(__file__).resolve().parents[1]

        self.assertTrue((repository_root / "src" / "aiteqno" / "__init__.py").is_file())
        self.assertFalse((repository_root / "aiteqno").exists())

    def test_typing_marker_is_packaged(self):
        marker = importlib.resources.files("aiteqno").joinpath("py.typed")

        self.assertTrue(marker.is_file())


if __name__ == "__main__":
    unittest.main()
