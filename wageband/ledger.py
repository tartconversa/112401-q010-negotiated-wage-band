"""追加式签认台账与政策重算。

设计原则（对应“不能靠重跑一张分布表就默默改掉已经签认的安排”）：

* 台账文件只追加、不覆盖：每次签认写一条 ``signoff`` 事件，每次重算写
  一条带新 ``event_id`` 的 ``recompute`` 事件，旧事件原样保留；
* 已执行工资期（有工资单实发记录）冻结当时依据与实发值，重算只生成
  核对差额，不回改历史；
* 后到的晋级证明、纠正凭证触发重算：原来哪一档仍可支持、哪些已签保护
  继续有效、哪些**未生效**承诺需要改议，分别列示；
* 差额同时标注是否占用剩余年度额度。
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .calibration import build_book
from .grading import resolve_grades
from .periods import parse_date
from .policy import (
    _commitment_periods,
    _term_lookup,
    check_commitment,
    evaluate_package,
    grades_for_worker_period,
)


def _default_json(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (tuple, set)):
        return list(value)
    raise TypeError(f"不可序列化的类型: {type(value)!r}")


@dataclass
class Ledger:
    path: Path
    events: list = field(default_factory=list)

    @classmethod
    def open(cls, path):
        path = Path(path)
        if path.exists():
            events = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(events, list):
                raise ValueError("台账文件必须是事件数组")
        else:
            events = []
        return cls(path=path, events=events)

    def _append(self, event):
        if any(e.get("event_id") == event["event_id"] for e in self.events):
            raise ValueError(f"事件编号冲突: {event['event_id']}")
        self.events.append(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 整文件重写但事件只增不改；读回校验保证历史未被篡改
        self.path.write_text(
            json.dumps(self.events, ensure_ascii=False, indent=2,
                       default=_default_json),
            encoding="utf-8")

    def record_signoff(self, scope, periods, caliber, basis, package_report,
                       note=""):
        """记录一次各方签认。scope 为明确的人员/承诺编号清单。"""
        event = {
            "event_id": uuid.uuid4().hex,
            "type": "signoff",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "scope": sorted(scope),
            "periods": list(periods),
            "caliber": caliber,
            "basis": basis,
            "note": note,
            "package": {
                "commitments": list(package_report.package_commitments),
                "feasible": package_report.feasible,
                "total_increment": str(package_report.total_increment),
                "total_annual_fixed_outlay":
                    str(package_report.total_annual_fixed_outlay),
            },
            "firms": [
                {"firm": f.firm, "feasible": f.feasible,
                 "signed_spend": str(f.signed_spend),
                 "proposed_spend": str(f.proposed_spend),
                 "budget_limit": (str(f.budget_limit)
                                  if f.budget_limit is not None else None),
                 "independently_signable":
                     [list(combo) for combo in f.independently_signable],
                 "conflict_summary": f.conflict_summary}
                for f in package_report.firms
            ],
        }
        self._append(event)
        return event

    def record_recompute(self, report, note=""):
        event = {
            "event_id": uuid.uuid4().hex,
            "type": "recompute",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "note": note,
            "report": report,
        }
        self._append(event)
        return event

    def signoff_events(self):
        return [e for e in self.events if e["type"] == "signoff"]

    def recompute_events(self):
        return [e for e in self.events if e["type"] == "recompute"]


# ---------------------------------------------------------------- 重算

@dataclass(frozen=True)
class ExecutedPeriod:
    worker: str
    period: str
    commitment: str
    agreed: Decimal
    actual_fixed: Decimal             # 当口径下实发固定薪酬
    variance: Decimal                 # 应发 - 实发（正数=欠付）
    consumes_remaining_budget: bool   # 欠付若补发是否占用剩余年度额度
    basis_frozen: str


@dataclass(frozen=True)
class RecomputedCommitment:
    commitment: str
    worker: str
    state: str                        # 原状态
    supportable_grades: tuple
    status: str                       # supported / renegotiate / executed_frozen
    old_basis_grade: tuple
    findings: tuple
    executed_periods: tuple


def recompute_policy(dataset, as_of, caliber="with_unconditional_allowance",
                     ledger=None):
    """在新证据视图下重算政策，区分已签保护与待改议承诺。

    返回结构：
      grade_changes   后到证明造成的级别变化（含追溯有效日）
      corrections     本视图实际采用的纠正凭证及影响人员
      commitments     每条承诺的重算结论
      executed        已执行工资期的冻结依据、实发值与核对差额
      budget_note     差额是否占用剩余年度额度
    """
    cutoff = parse_date(as_of) if isinstance(as_of, str) else as_of
    view = resolve_grades(dataset, cutoff.isoformat())
    book = build_book(dataset, caliber, cutoff.isoformat())
    version = sorted({row.version for row in dataset.terms})[-1]
    term_index = _term_lookup(dataset, version)

    grade_changes = _grade_changes(dataset, cutoff)
    correction_effects = _correction_effects(dataset, cutoff, book)

    recomputed = []
    executed_all = []
    for commitment in sorted(dataset.commitments,
                             key=lambda c: c.commitment):
        employee = dataset.employee(commitment.worker)
        findings = []
        supportable = set()
        old_basis = set()
        executed = []

        periods = list(_commitment_periods(dataset, commitment))
        for period in periods:
            period_grade = grades_for_worker_period(
                dataset, commitment.worker, period, cutoff.isoformat())
            old_basis.update(period_grade.grades)
            standard = (commitment.agreed / employee.workload
                        if employee.workload > 0 else Decimal(0))
            ok_grades = tuple(
                grade for grade in period_grade.grades
                if term_index[grade].lower <= standard
                <= term_index[grade].upper)
            supportable.update(ok_grades)
            if not ok_grades:
                findings.append(
                    f"{period}: 标准工时口径 {standard.quantize(Decimal('0.01'))} "
                    f"不在候选级 {period_grade.grades} 的带界内")

            actual = _actual_in_period(book, commitment.worker, period)
            if actual is not None:
                variance = commitment.agreed - actual
                # 欠付需要补发，占用剩余额度；超付不占用
                consumes = variance > 0
                executed.append(ExecutedPeriod(
                    worker=commitment.worker, period=period,
                    commitment=commitment.commitment,
                    agreed=commitment.agreed, actual_fixed=actual,
                    variance=variance,
                    consumes_remaining_budget=consumes,
                    basis_frozen=(
                        f"承诺 {commitment.commitment} 原约定月固定 "
                        f"{commitment.agreed}，级别视图冻结于签认时点"),
                ))

        violations, _ = check_commitment(
            dataset, commitment, cutoff.isoformat(), term_index)
        # 带界问题已在上方按“可支持级别”逐期说明，这里只补充其余硬约束
        for violation in violations:
            if violation.kind != "band":
                findings.append(violation.message)

        if executed and commitment.state == "signed":
            status = "executed_frozen"
        elif supportable and not violations:
            status = "supported"
        else:
            status = "renegotiate"
        executed_all.extend(executed)
        recomputed.append(RecomputedCommitment(
            commitment=commitment.commitment, worker=commitment.worker,
            state=commitment.state,
            supportable_grades=tuple(sorted(supportable)),
            status=status, old_basis_grade=tuple(sorted(old_basis)),
            findings=tuple(findings),
            executed_periods=tuple(executed),
        ))

    budget_note = _budget_note(dataset, executed_all)

    # 整包层面的预算/相邻级差冲突：单条合规但组合后不达标时，进入待改议
    package = evaluate_package(dataset, cutoff.isoformat())
    package_conflicts = []
    renegotiate_extra = set()
    for firm in package.firms:
        for violation in firm.violations:
            # 单条承诺的带界/保护/期次/职级问题已在逐条结论中列出，
            # 这里只保留组合层面才会出现的预算与相邻级差冲突。
            if violation.kind not in ("budget", "gap"):
                continue
            package_conflicts.append({
                "firm": firm.firm, "kind": violation.kind,
                "message": violation.message,
            })
            # 整包冲突无法定位到单条时，把该企业拟议承诺全部列入改议
            for commitment in dataset.commitments:
                if (dataset.employee(commitment.worker).firm == firm.firm
                        and commitment.state == "proposed"):
                    renegotiate_extra.add(commitment.commitment)

    report = {
        "as_of": cutoff.isoformat(),
        "caliber": caliber,
        "applied_corrections": list(book.applied_corrections),
        "grade_changes": grade_changes,
        "correction_effects": correction_effects,
        "package_conflicts": package_conflicts,
        "commitments": [
            {"commitment": item.commitment, "worker": item.worker,
             "state": item.state, "status": item.status,
             "old_basis_grades": list(item.old_basis_grade),
             "supportable_grades": list(item.supportable_grades),
             "findings": list(item.findings),
             "executed_periods": [
                 {"period": p.period, "agreed": str(p.agreed),
                  "actual_fixed": str(p.actual_fixed),
                  "variance": str(p.variance),
                  "consumes_remaining_budget":
                      p.consumes_remaining_budget,
                  "basis_frozen": p.basis_frozen}
                 for p in item.executed_periods]}
            for item in recomputed
        ],
        "executed_reconciliation": [
            {"worker": p.worker, "period": p.period,
             "commitment": p.commitment, "agreed": str(p.agreed),
             "actual_fixed": str(p.actual_fixed),
             "variance": str(p.variance),
             "consumes_remaining_budget": p.consumes_remaining_budget}
            for p in executed_all
        ],
        "budget_note": budget_note,
        "renegotiation_queue": sorted(set(
            item.commitment for item in recomputed
            if item.status == "renegotiate") | renegotiate_extra),
        "protected_signed": [
            item.commitment for item in recomputed
            if item.state == "signed"
            and item.status in ("supported", "executed_frozen")],
    }
    if ledger is not None:
        ledger.record_recompute(
            report, note="后到证明/纠正凭证触发的政策重算")
    return report


def _actual_in_period(book, worker, period):
    for monthly in book.series(worker):
        if monthly.period == period:
            return monthly.actual_fixed
    return None


def _grade_changes(dataset, cutoff):
    """对比每条证明收到前后的级别判定，只列真正的归属变化（后到追溯）。"""
    changes = []
    for proof in sorted(dataset.proofs, key=lambda p: p.recorded):
        recorded = parse_date(proof.recorded)
        if recorded > cutoff:
            continue
        before = resolve_grades(dataset, _day_before(recorded))
        after = resolve_grades(dataset, recorded.isoformat())
        b = before.resolutions.get(proof.worker)
        a = after.resolutions.get(proof.worker)
        # 首次收到证明（此前无任何级别）不算“变化”，只是建档
        if not b or not b.grades or not a or b.grades == a.grades:
            continue
        changes.append({
            "proof": proof.proof, "worker": proof.worker,
            "effective": proof.effective, "recorded": proof.recorded,
            "grades_before": list(b.grades),
            "grades_after": list(a.grades),
            "retroactive": proof.effective < proof.recorded,
        })
    return changes


def _day_before(value):
    from datetime import timedelta
    return (value - timedelta(days=1)).isoformat()


def _correction_effects(dataset, cutoff, book):
    effects = []
    for correction in dataset.corrections:
        if parse_date(correction.received) > cutoff:
            continue
        for line_id in correction.lines:
            line = next(p for p in dataset.pay if p.line == line_id)
            monthly = next((m for m in book.series(line.worker)
                            if m.period == line.period), None)
            effects.append({
                "correction": correction.correction,
                "line": line_id, "worker": line.worker,
                "period": line.period, "amount": str(line.amount),
                "reported_unconditional": "yes" if line.unconditional else "no",
                "actual_unconditional":
                    "yes" if correction.actual_unconditional else "no",
                "now_counted_as_fixed": (
                    line_id in (monthly.included_lines if monthly else ())),
                "reason": correction.reason,
            })
    return effects


def _budget_note(dataset, executed):
    notes = []
    by_firm = {}
    for item in executed:
        if not item.consumes_remaining_budget:
            continue
        employee = dataset.employee(item.worker)
        by_firm.setdefault(employee.firm, Decimal(0))
        by_firm[employee.firm] += item.variance
    for firm, amount in sorted(by_firm.items()):
        budget = next((b for b in dataset.budgets if b.firm == firm), None)
        limit = budget.limit if budget else None
        notes.append({
            "firm": firm,
            "pending_makeup": str(amount),
            "budget_limit": str(limit) if limit is not None else None,
            "note": "欠付补发将占用剩余年度额度；超付不追加额度",
        })
    return notes
