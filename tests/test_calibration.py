"""口径标准化、纠正凭证时点与缺月处理。"""
from decimal import Decimal

from support import DatasetCase

from wageband.calibration import build_book, coverage_ratio, observed_values

class CalibrationTests(DatasetCase):
    def test_fixed_only_excludes_allowance_and_bonus(self):
        book = build_book(self.dataset, "fixed_only", "2026-07-15")
        w2_july = next(m for m in book.series("W2") if m.period == "2026-07")
        self.assertEqual(w2_july.actual_fixed, Decimal("6200"))
        self.assertIn("A12", w2_july.excluded_lines)
        # W5 的奖金 A7 永远不计固定
        w5_june = next(m for m in book.series("W5") if m.period == "2026-06")
        self.assertEqual(w5_june.actual_fixed, Decimal("6100"))
        self.assertIn("A7", w5_june.excluded_lines)

    def test_allowance_caliber_includes_unconditional_before_correction(self):
        book = build_book(
            self.dataset, "with_unconditional_allowance", "2026-07-15")
        w2_june = next(m for m in book.series("W2") if m.period == "2026-06")
        self.assertEqual(w2_june.actual_fixed, Decimal("6600"))
        self.assertEqual(book.applied_corrections, ())

    def test_correction_removes_night_allowance_after_received_date(self):
        # 收到日 2026-08-10 之前，纠正不可用
        before = build_book(
            self.dataset, "with_unconditional_allowance", "2026-08-09")
        self.assertEqual(before.applied_corrections, ())
        after = build_book(
            self.dataset, "with_unconditional_allowance", "2026-08-15")
        self.assertEqual(after.applied_corrections, ("D1",))
        w2_june = next(m for m in after.series("W2") if m.period == "2026-06")
        self.assertEqual(w2_june.actual_fixed, Decimal("6000"))
        self.assertIn("A3", w2_june.corrected_lines)
        self.assertIn("A3", w2_june.excluded_lines)

    def test_standard_hour_scaling_for_part_time_workers(self):
        book = build_book(self.dataset, "fixed_only", "2026-07-15")
        w3 = next(m for m in book.series("W3") if m.period == "2026-07")
        # 2900 实际 / 0.5 用工 = 5800 标准工时口径
        self.assertEqual(w3.standard_hour_fixed, Decimal("5800.00"))
        w6 = next(m for m in book.series("W6") if m.period == "2026-07")
        self.assertEqual(w6.standard_hour_fixed,
                         (Decimal("4300") / Decimal("0.75")
                          ).quantize(Decimal("0.01")))

    def test_missing_month_not_filled_with_bonus(self):
        book = build_book(self.dataset, "fixed_only", "2026-07-15")
        w7_july = next(m for m in book.series("W7") if m.period == "2026-07")
        self.assertTrue(w7_july.missing)
        self.assertEqual(w7_july.actual_fixed, Decimal(0))
        self.assertEqual(book.missing_months["W7"], ("2026-07",))
        self.assertEqual(book.voided_bonus_months["W7"], ("2026-07",))
        # 缺月不进入观测序列
        self.assertEqual(observed_values(book, "W7"), (Decimal("5300.00"),))
        self.assertEqual(coverage_ratio(book, "W7"), Decimal("0.5"))

    def test_unknown_caliber_rejected(self):
        with self.assertRaises(ValueError):
            build_book(self.dataset, "gross_plus_bonus", "2026-07-15")
from support import DatasetCase
