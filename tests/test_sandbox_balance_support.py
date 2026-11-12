"""Controller, lifecycle and packaged entrypoint tests against the installed game."""
from pathlib import Path
import importlib.util
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('build_sandbox_balance_support',
                                            ROOT / 'tools/build_sandbox_balance_support.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class SandboxBalanceSupportTests(unittest.TestCase):
    def test_controller_and_lifecycle_against_installed_dashboard(self):
        if not builder.GAME.is_file():
            self.skipTest('Subway Builder 1.7.2 is required for the production dashboard oracle')
        result = builder.run_tests(builder.inspect_game(builder.GAME))
        self.assertGreaterEqual(result['passed'], 20)
        self.assertGreaterEqual(result['dashboard_cases'], 60)


if __name__ == '__main__':
    unittest.main()
