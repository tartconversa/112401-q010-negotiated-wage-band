"""CLI 端到端：隔离模式启动器的退出码与台账文件。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "run_wageband.py"
SAMPLES = ROOT / "samples"

class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-I", str(LAUNCHER), *args],
            cwd=ROOT, capture_output=True, text=True, check=False)

    def test_grades_json_is_machine_readable(self):
        proc = self.run_cli(
            "grades", "--data", str(SAMPLES),
            "--as-of", "2026-08-15", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        w2 = next(r for r in payload["resolutions"] if r["worker"] == "W2")
        self.assertEqual(w2["grades"], ["L3"])

    def test_infeasible_package_exits_2_but_signable_range_is_printed(self):
        proc = self.run_cli(
            "feasibility", "--data", str(SAMPLES),
            "--as-of", "2026-07-15")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("可独立签认的最大范围: (S4)；(S5)", proc.stdout)
        self.assertIn("保护条件未被压低", proc.stdout)

    def test_sign_refuses_conflict_without_force_and_appends_with_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            refused = self.run_cli(
                "sign", "--data", str(SAMPLES),
                "--as-of", "2026-07-15", "--ledger", str(ledger),
                "--commitments", "S4", "S5")
            self.assertEqual(refused.returncode, 2)
            self.assertFalse(ledger.exists())

            forced = self.run_cli(
                "sign", "--data", str(SAMPLES),
                "--as-of", "2026-07-15", "--ledger", str(ledger),
                "--commitments", "S1", "--force")
            self.assertEqual(forced.returncode, 0, forced.stderr)
            events = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(events[0]["scope"], ["S1"])

    def test_recompute_appends_event_without_touching_prior_signoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            self.run_cli(
                "sign", "--data", str(SAMPLES),
                "--as-of", "2026-07-15", "--ledger", str(ledger),
                "--commitments", "S1", "S2", "S3", "--force")
            proc = self.run_cli(
                "recompute", "--data", str(SAMPLES),
                "--as-of", "2026-08-15", "--ledger", str(ledger))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            events = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual([e["type"] for e in events],
                             ["signoff", "recompute"])
            self.assertIn("历史签认未改动", proc.stdout)

if __name__ == "__main__":
    unittest.main()
