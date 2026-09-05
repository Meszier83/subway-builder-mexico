"""
tests.test_wsl_bridge
=====================
Pruebas unitarias para el puente WSL y el endpoint /api/system-check.
"""

import unittest
import os
from sb_mexico.cartography import to_wsl_path, is_wsl_available


class TestWSLBridge(unittest.TestCase):
    def test_to_wsl_path(self):
        win_path = r"C:\subway-builder-mexico\cities\test.yaml"
        wsl_p = to_wsl_path(win_path)
        self.assertTrue(wsl_p.startswith("/mnt/c/"))
        self.assertNotIn("\\", wsl_p)
        self.assertIn("cities/test.yaml", wsl_p)

    def test_is_wsl_available(self):
        ready, distro, tools = is_wsl_available()
        self.assertTrue(ready)
        self.assertIn(distro, ["Ubuntu", "native"])
        self.assertTrue(tools.get("tippecanoe", False))
        self.assertTrue(tools.get("depot", False))


if __name__ == "__main__":
    unittest.main()
