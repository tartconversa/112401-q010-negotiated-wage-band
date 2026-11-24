"""追加式台账与新证据重算：已执行期冻结、保护与待改议分流。"""
import json
import tempfile
from decimal import Decimal
from pathlib import Path

from support import DatasetCase

from wageband.ledger import Ledger, recompute_policy
from wageband.policy import evaluate_package

class LedgerTests(DatasetCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.tmp.name) / "ledger.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_signoff_is_append_only_and_history_immutable(self):
        ledger = Ledger.open(self.ledger_path)
        report = evaluate_package(
            self.dataset, "2026-07-15",
            commitment_ids=["S1", "S2", "S3"])
        ledger.record_signoff(
            scope=["S1", "S2", "S3"],
            periods=["2026-07", "2026-08", "2026-09", "2026-10",
                     "2026-11", "2026-12"],
            caliber="with_unconditional_allowance",
            basis={"as_of": "2026-07-15"},
            package_report=report, note="七月签认")
        recompute_policy(self.dataset, "2026-08-15", ledger=ledger)

        raw = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertEqual([e["type"] for e in raw],
                         ["signoff", "recompute"])
        # 重算后重开台账，旧签认原样保留
        reopened = Ledger.open(self.ledger_path)
        signed = reopened.signoff_events()[0]
        self.assertEqual(signed["scope"], ["S1", "S2", "S3"])
        self.assertEqual(signed["note"], "七月签认")
        self.assertEqual(len(reopened.recompute_events()), 1)

    def test_recompute_keeps_signed_protection_and_freezes_executed_periods(self):
        report = recompute_policy(
            self.dataset, "2026-08-15",
            caliber="with_unconditional_allowance")
        by_id = {item["commitment"]: item
                 for item in report["commitments"]}
        # 已签承诺的保护继续有效；S2 在新级别下带界不支持，但已执行期冻结
        self.assertIn("S1", report["protected_signed"])
        self.assertIn("S2", report["protected_signed"])
        self.assertIn("S3", report["protected_signed"])
        self.assertEqual(by_id["S2"]["status"], "executed_frozen")
        self.assertEqual(by_id["S2"]["supportable_grades"], [])

        executed = {row["worker"] + row["period"]: row
                    for row in report["executed_reconciliation"]}
        # W2 七月应发 6400、实发（津贴被重分类后）6200，差额 200
        w2 = executed["W22026-07"]
        self.assertEqual(w2["variance"], "200")
        self.assertTrue(w2["consumes_remaining_budget"])
        # W1 无差额，不占用额度
        w1 = executed["W12026-07"]
        self.assertEqual(w1["variance"], "0")
        self.assertFalse(w1["consumes_remaining_budget"])

    def test_recompute_identifies_supportable_grade_and_renegotiation_queue(self):
        report = recompute_policy(
            self.dataset, "2026-08-15",
            caliber="with_unconditional_allowance")
        by_id = {item["commitment"]: item
                 for item in report["commitments"]}
        # W7 的 5900 只落在 L1 带内，L2 不支持 → 待改议
        self.assertEqual(by_id["S6"]["supportable_grades"], ["L1"])
        self.assertIn("S6", report["renegotiation_queue"])
        # S2 在旧级别 L2 下本可支持——重算明确给出这一信息
        s2_findings = " ".join(by_id["S2"]["findings"])
        self.assertIn("L3", s2_findings)

    def test_late_proof_change_is_retroactive_and_correction_is_applied(self):
        report = recompute_policy(
            self.dataset, "2026-08-15",
            caliber="with_unconditional_allowance")
        change = next(c for c in report["grade_changes"]
                      if c["proof"] == "P8")
        self.assertEqual(change["grades_before"], ["L2", "L3"])
        self.assertEqual(change["grades_after"], ["L3"])
        self.assertTrue(change["retroactive"])
        corrections = {c["line"]: c for c in report["correction_effects"]}
        self.assertFalse(corrections["A3"]["now_counted_as_fixed"])
        self.assertEqual(corrections["A3"]["actual_unconditional"], "no")
        self.assertEqual(report["applied_corrections"], ["D1"])

    def test_recompute_before_evidence_arrives_does_not_apply_it(self):
        report = recompute_policy(
            self.dataset, "2026-08-09",
            caliber="with_unconditional_allowance")
        self.assertEqual(report["applied_corrections"], [])
        self.assertEqual(report["grade_changes"], [])
        by_id = {item["commitment"]: item
                 for item in report["commitments"]}
        # 纠正未生效时 W2 七月实发含津贴 6200+600=6800，反而超发 400
        executed = {row["worker"] + row["period"]: row
                    for row in report["executed_reconciliation"]}
        self.assertEqual(executed["W22026-07"]["actual_fixed"], "6800")
        self.assertEqual(executed["W22026-07"]["variance"], "-400")
        self.assertFalse(
            executed["W22026-07"]["consumes_remaining_budget"])
from support import DatasetCase
