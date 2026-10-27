"""Cost admission and outbound gate checks with no network calls."""
import importlib.util
import shutil
import unittest
import uuid
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("agent_model_provider", Path(__file__).with_name("agent_model_provider.py"))
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class ProviderGateTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).resolve().parents[3] / "artifacts" / ("model-provider-test-" + uuid.uuid4().hex)
        self.path.mkdir()

    def tearDown(self):
        shutil.rmtree(self.path)

    def test_cost_stop_and_long_context(self):
        ledger = MOD.CostLedger(self.path)
        self.assertEqual(ledger.input_cap(128, 1800), 24050)
        self.assertAlmostEqual(ledger.projection(24050, 1800) * 128, 0.50)
        repair = MOD.CostLedger(self.path / "repair", limit_usd=0.45)
        expanded = MOD.CostLedger(self.path / "expanded", limit_usd=0.80)
        self.assertAlmostEqual(expanded.limit_usd, 0.80)
        self.assertEqual(expanded.configured_input_cap(160, 4096, 22000), 22000)
        with self.assertRaises(ValueError):
            expanded.configured_input_cap(160, 4096, 24000)
        with self.assertRaises(ValueError):
            MOD.CostLedger(self.path / "too-large", limit_usd=40.01)
        self.assertEqual(MOD.CostLedger(self.path / "panel", limit_usd=40.00).limit_usd, 40.00)
        self.assertEqual(repair.input_cap(96, 4096), 21116)
        self.assertLessEqual(repair.projection(21116, 4096) * 96, 0.45)
        self.assertGreater(ledger.admit(16384, 1800), 0)
        reopened = MOD.CostLedger(self.path)
        self.assertEqual(reopened.reserved, ledger.reserved)
        with self.assertRaisesRegex(RuntimeError, "estimated-charge-stop"):
            reopened.check_remaining(16384, 1800, 200)
        with self.assertRaisesRegex(RuntimeError, "price-tier-unqualified"):
            ledger.admit(272001, 1800)
        with self.assertRaisesRegex(RuntimeError, "estimated-charge-stop"):
            ledger.admit(272000, 1000000)

    def test_corpus_outbound_gate_precedes_key_or_sdk(self):
        with self.assertRaisesRegex(RuntimeError, "outbound-data-review-required"):
            MOD.OpenAIResponses(self.path)

    def test_local_endpoint_gate(self):
        with self.assertRaisesRegex(ValueError, "local-endpoint-required"):
            MOD.LocalResponses("qwen", "digest", self.path, endpoint="https://ollama.com")


if __name__ == "__main__":
    unittest.main()
