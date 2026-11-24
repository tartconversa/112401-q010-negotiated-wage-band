"""职级归属的时点视图与跨级处理。"""
from support import DatasetCase

from wageband.grading import resolve_grades

class GradingTests(DatasetCase):
    def test_before_late_proof_w2_is_ambiguous_and_w7_stays_ambiguous(self):
        view = resolve_grades(self.dataset, "2026-07-15")
        self.assertEqual(view.get("W1").grades, ("L1",))
        self.assertEqual(view.get("W1").status, "determined")
        self.assertEqual(view.get("W2").grades, ("L2", "L3"))
        self.assertEqual(view.get("W2").status, "ambiguous")
        self.assertEqual(view.get("W2").pending_proofs, ("P8",))
        self.assertEqual(view.get("W7").grades, ("L1", "L2"))
        self.assertEqual(view.get("W7").status, "ambiguous")

    def test_late_proof_resolves_w2_to_l3_with_retroactive_effect(self):
        view = resolve_grades(self.dataset, "2026-08-15")
        w2 = view.get("W2")
        self.assertEqual(w2.status, "determined")
        self.assertEqual(w2.grades, ("L3",))
        self.assertIn("P8", w2.used_proofs)
        # P8 的有效日早于收到日，但 8 月前不可见
        early = resolve_grades(self.dataset, "2026-08-09")
        self.assertEqual(early.get("W2").status, "ambiguous")

    def test_intersection_logic_does_not_double_count_ambiguous_workers(self):
        view = resolve_grades(self.dataset, "2026-07-15")
        # 每个跨级者只出现一次，候选级为集合而非两条独立归属
        w7 = view.get("W7")
        self.assertEqual(len(view.resolutions["W7"].grades),
                         len(set(w7.grades)))
        self.assertEqual(set(view.ambiguous()), {"W2", "W7"})
        self.assertEqual(set(view.determined()),
                         {"W1", "W3", "W4", "W5", "W6"})
from support import DatasetCase
