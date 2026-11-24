"""分布、展宽与倒挂的数字断言。"""
from decimal import Decimal

from support import DatasetCase

from wageband.calibration import build_book
from wageband.distribution import analyze_distribution
from wageband.grading import resolve_grades

class DistributionTests(DatasetCase):
    def analyze(self, caliber, as_of):
        book = build_book(self.dataset, caliber, as_of)
        view = resolve_grades(self.dataset, as_of)
        return analyze_distribution(self.dataset, book, view)

    def test_tight_distribution_uses_determined_workers_only(self):
        report = self.analyze("fixed_only", "2026-07-15")
        l1 = report.tight["L1"]
        self.assertEqual(l1.workers, ("W1", "W3", "W4"))
        self.assertEqual(l1.minimum, Decimal("5400.00"))
        self.assertEqual(l1.maximum, Decimal("5800.00"))
        l2 = report.tight["L2"]
        self.assertEqual(set(l2.workers), {"W5", "W6"})
        self.assertEqual(l2.minimum, Decimal("5733.33"))
        # L3 没有确定归属者
        self.assertEqual(report.tight["L3"].count, 0)

    def test_ambiguous_widening_quantified_and_not_double_counted(self):
        report = self.analyze("fixed_only", "2026-07-15")
        l2_wide = report.widening["L2"]
        self.assertEqual(set(l2_wide.added_workers), {"W2", "W7"})
        # L2 紧分布最低 5733.33，纳入 W7(5300) 后向下展宽 433.33
        self.assertEqual(l2_wide.downward, Decimal("433.33"))
        self.assertEqual(l2_wide.widest_minimum, Decimal("5300.00"))
        l1_wide = report.widening["L1"]
        self.assertEqual(l1_wide.added_workers, ("W7",))
        self.assertEqual(l1_wide.downward, Decimal("100.00"))

    def test_partial_coverage_disclosed_per_worker(self):
        report = self.analyze("fixed_only", "2026-07-15")
        w7 = report.coverage["W7"]
        self.assertEqual(w7["months_observed"], 2)
        self.assertEqual(w7["months_covered"], 1)
        self.assertEqual(w7["missing"], ("2026-07",))
        self.assertEqual(w7["bonus_only_months"], ("2026-07",))

    def test_band_gap_uses_lower_of_higher_minus_upper_of_lower(self):
        report = self.analyze("fixed_only", "2026-07-15")
        gap_12, gap_23 = report.band_gaps
        self.assertEqual(gap_12.gap, Decimal("6100") - Decimal("6200"))
        self.assertTrue(gap_12.inverted)
        self.assertEqual(gap_12.shortfall, Decimal("400"))
        self.assertEqual(gap_23.gap, Decimal("6650") - Decimal("6800"))
        self.assertEqual(gap_23.shortfall, Decimal("500"))

    def test_observed_inversion_between_populated_grades(self):
        report = self.analyze("fixed_only", "2026-07-15")
        self.assertEqual(len(report.observed_inversions), 1)
        inv = report.observed_inversions[0]
        self.assertEqual((inv.lower_worker, inv.higher_worker),
                         ("W3", "W6"))
        self.assertEqual(inv.gap, Decimal("5733.33") - Decimal("5800.00"))

    def test_correction_changes_distribution_and_new_inversion_appears(self):
        report = self.analyze(
            "with_unconditional_allowance", "2026-08-15")
        # W2 落定 L3，其固定薪酬因津贴重分类而只剩 fixed
        self.assertEqual(report.tight["L3"].workers, ("W2",))
        self.assertEqual(report.tight["L3"].minimum, Decimal("6000.00"))
        inversions = {(i.lower_worker, i.higher_worker)
                      for i in report.observed_inversions}
        self.assertIn(("W3", "W6"), inversions)
        self.assertIn(("W5", "W2"), inversions)
from support import DatasetCase
