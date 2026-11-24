"""分阶段可行性：四项硬约束、预算与可独立签认范围。"""
from decimal import Decimal

from support import DatasetCase

from wageband.policy import (
    _commitment_periods,
    evaluate_package,
    grades_for_worker_period,
)

class FeasibilityTests(DatasetCase):
    def test_mid_year_grade_effectivity(self):
        # W2 的 L3 证明 6/1 生效、8/10 收到：7 月视图看不到，8 月视图看得到
        july = grades_for_worker_period(
            self.dataset, "W2", "2026-07", "2026-07-15")
        self.assertEqual(july.grades, ("L2", "L3"))
        august = grades_for_worker_period(
            self.dataset, "W2", "2026-07", "2026-08-15")
        self.assertEqual(august.grades, ("L3",))

    def test_commitment_periods_respect_budget_window_and_workload(self):
        s6 = next(c for c in self.dataset.commitments
                  if c.commitment == "S6")
        # F3 预算窗口 9 月起，承诺 10 月起 → 10..12 共 3 期
        self.assertEqual(_commitment_periods(self.dataset, s6),
                         ["2026-10", "2026-11", "2026-12"])

    def test_full_package_fails_with_conflicts_and_never_weakens_protection(self):
        report = evaluate_package(self.dataset, "2026-07-15")
        self.assertFalse(report.feasible)
        kinds = {v.kind for v in report.violations()}
        # 带界、级差、预算三类冲突都应出现；没有任何一条建议降低保护
        self.assertIn("band", kinds)
        self.assertIn("gap", kinds)
        self.assertIn("budget", kinds)
        for violation in report.violations():
            if violation.kind == "protection":
                self.fail("样例承诺全部不低于受保护工资，不应有保护冲突")

    def test_f2_budget_overrun_and_independently_signable_subsets(self):
        report = evaluate_package(self.dataset, "2026-07-15")
        f2 = next(f for f in report.firms if f.firm == "F2")
        self.assertEqual(f2.signed_spend + f2.proposed_spend,
                         Decimal("2400"))
        self.assertEqual(f2.budget_limit, Decimal("2200"))
        # 超 200；S4、S5 单条都在额度内，各自可独立签认
        self.assertEqual(tuple(f2.independently_signable),
                         (("S4",), ("S5",)))
        budget_violation = next(v for v in f2.violations
                                if v.kind == "budget")
        self.assertEqual(budget_violation.detail["over"], "200")

    def test_band_violation_for_ambiguous_worker_requires_all_candidate_grades(self):
        report = evaluate_package(
            self.dataset, "2026-07-15", commitment_ids=["S6"])
        s6_violations = [v for v in report.violations()
                         if v.commitment == "S6" and v.kind == "band"]
        # 5900 满足 L1 [5500,6200] 但不满足 L2 [6100,6800]
        self.assertTrue(s6_violations)
        grades_flagged = {v.detail["grade"] for v in s6_violations}
        self.assertEqual(grades_flagged, {"L2"})

    def test_signed_only_package_costs_match_hand_calculation(self):
        report = evaluate_package(
            self.dataset, "2026-07-15", include_proposed=False)
        f1 = next(f for f in report.firms if f.firm == "F1")
        # S1 250*6 + S2 400*6 + S3 100*6 = 4500
        self.assertEqual(f1.signed_spend, Decimal("4500"))
        self.assertEqual(f1.remaining_after_signed, Decimal("2700"))
        self.assertEqual(report.total_increment, Decimal("4500"))
        # 覆盖期固定支出：S1 5650*6 + S2 6400*6 + S3 2900*6
        self.assertEqual(report.total_annual_fixed_outlay,
                         Decimal("5650") * 6 + Decimal("6400") * 6
                         + Decimal("2900") * 6)

    def test_protection_violation_is_flagged_not_silently_accepted(self):
        from wageband.model import Commitment
        from decimal import Decimal as D
        # 构造一条低于受保护工资的承诺，引擎必须报保护冲突
        hacked = Commitment(
            commitment="SX", worker="W1", terms="T1",
            start="2026-07", end="2026-12", agreed=D("5000"),
            state="proposed")
        original = self.dataset.commitments
        object.__setattr__(self.dataset, "commitments",
                           original + (hacked,))
        try:
            report = evaluate_package(
                self.dataset, "2026-07-15", commitment_ids=["SX"])
            self.assertIn("protection",
                          {v.kind for v in report.violations()})
        finally:
            object.__setattr__(self.dataset, "commitments", original)
from support import DatasetCase
