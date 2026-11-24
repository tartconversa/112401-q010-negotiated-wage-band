"""检查演练资料的读取契约和关联锚点。"""
import csv
from pathlib import Path
import unittest

INPUTS = Path(__file__).resolve().parent.parent / "samples"

HEADERS = {'employees': ('worker', 'firm', 'family', 'site', 'workload', 'start', 'end', 'protected_monthly'), 'grade_map': ('firm', 'code', 'options', 'from', 'to'), 'grades': ('proof', 'worker', 'code', 'effective', 'recorded'), 'pay': ('line', 'worker', 'period', 'kind', 'amount', 'unconditional'), 'terms': ('version', 'grade', 'lower', 'upper', 'min_gap', 'active'), 'budgets': ('firm', 'year', 'limit', 'months'), 'signoff': ('commitment', 'worker', 'terms', 'start', 'end', 'agreed', 'state'), 'corrections': ('correction', 'lines', 'actual_unconditional', 'received', 'reason')}
REFERENCES = [('pay', 'worker', 'employees', 'worker'), ('signoff', 'terms', 'terms', 'version'), ('corrections', 'lines', 'pay', 'line'), ('grades', 'worker', 'employees', 'worker')]
EXAMPLES = [['grades', 'worker', 'W2', 2], ['pay', 'worker', 'W7', 2], ['signoff', 'state', 'signed', 3]]


class SampleInputContract(unittest.TestCase):
    def read(self, table):
        with (INPUTS / f"{table}.csv").open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source, strict=True)
            self.assertEqual(tuple(reader.fieldnames), HEADERS[table])
            rows = list(reader)
        self.assertTrue(rows, table)
        for row in rows:
            self.assertNotIn(None, row, table)
            self.assertNotIn(None, row.values(), table)
        return rows

    def test_csv_readable_with_documented_columns(self):
        for table in HEADERS:
            with self.subTest(table=table):
                self.read(table)

    def test_cross_source_example_references(self):
        for source, source_field, target, target_field in REFERENCES:
            with self.subTest(source=source, field=source_field, target=target):
                available = {row[target_field] for row in self.read(target)}
                actual = {value for row in self.read(source)
                          for value in row[source_field].split("|") if value}
                self.assertTrue(actual)
                self.assertEqual(actual - available, set())

    def test_example_revisions_and_overlaps_are_present(self):
        for table, field, example, expected in EXAMPLES:
            with self.subTest(table=table, field=field, example=example):
                found = sum((row[field] == example if "|" in example else example in row[field].split("|")) for row in self.read(table))
                self.assertEqual(found, expected)


if __name__ == "__main__":
    unittest.main()
