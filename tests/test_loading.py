"""资料装载与结构校验。"""
import unittest
from decimal import Decimal

from support import SAMPLES, DatasetCase

from wageband.model import DataError, load_dataset
from wageband.periods import (
    add_months,
    diff_months,
    iter_periods,
    month_after,
    periods_intersect,
)

class LoadingTests(DatasetCase):
    def test_amounts_are_decimal_and_workloads_kept(self):
        self.assertEqual(self.employees["W3"].workload, Decimal("0.5"))
        self.assertEqual(self.employees["W3"].protected_monthly, Decimal("2800"))
        w2_allowance = next(
            line for line in self.dataset.pay
            if line.line == "A3")
        self.assertEqual(w2_allowance.amount, Decimal("600"))
        self.assertTrue(w2_allowance.unconditional)

    def test_multi_value_fields_become_tuples(self):
        mapping = next(m for m in self.dataset.grade_map
                       if m.firm == "F1" and m.code == "b")
        self.assertEqual(mapping.options, ("L2", "L3"))
        correction = self.dataset.corrections[0]
        self.assertEqual(correction.lines, ("A3", "A12"))
        self.assertFalse(correction.actual_unconditional)

    def test_budget_months_validated_against_year(self):
        f1 = next(b for b in self.dataset.budgets if b.firm == "F1")
        self.assertEqual(f1.months[0], "2026-07")
        self.assertEqual(f1.months[-1], "2026-12")
        self.assertEqual(len(f1.months), 6)

    def test_missing_file_and_bad_reference_reported_together(self):
        import csv
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = __import__("pathlib").Path(tmp)
            # 只复制 employees，其余表缺失；且写一个未知企业的预算
            with (SAMPLES / "employees.csv").open(encoding="utf-8") as src:
                (tmp_path / "employees.csv").write_text(src.read(),
                                                        encoding="utf-8")
            for name in ("grade_map", "grades", "pay", "terms",
                         "budgets", "signoff", "corrections"):
                with (SAMPLES / f"{name}.csv").open(encoding="utf-8") as src:
                    text = src.read()
                if name == "budgets":
                    rows = list(csv.DictReader(text.splitlines()))
                    rows[0]["firm"] = "FX"
                    text = "firm,year,limit,months\n" + "\n".join(
                        f"{r['firm']},{r['year']},{r['limit']},{r['months']}"
                        for r in rows)
                (tmp_path / f"{name}.csv").write_text(text, encoding="utf-8")
            with self.assertRaises(DataError) as ctx:
                load_dataset(tmp_path)
            self.assertTrue(any("FX" in p for p in ctx.exception.problems))

class PeriodTests(unittest.TestCase):
    def test_iteration_and_diffs(self):
        self.assertEqual(list(iter_periods("2026-11", "2027-02")),
                         ["2026-11", "2026-12", "2027-01", "2027-02"])
        self.assertEqual(diff_months("2027-01", "2026-07"), 6)
        self.assertEqual(month_after("2026-12"), "2027-01")
        self.assertEqual(add_months("2026-01", -1), "2025-12")

    def test_intersection(self):
        self.assertEqual(
            periods_intersect("2026-07", "2026-12", "2026-09", "2027-03"),
            ["2026-09", "2026-10", "2026-11", "2026-12"])
        self.assertEqual(
            periods_intersect("2026-01", "2026-03", "2026-09", "2026-12"), [])
from support import SAMPLES, DatasetCase
