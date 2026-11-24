"""固定薪酬分布、相邻技能级差与倒挂位置。

输出分三层，方便各方审议：

1. **紧分布**：只含截至日已确定归属（determined）的人员，按级别汇总
   标准工时口径固定薪酬的 min/max/均值/人数；
2. **争议展宽**：跨级估计（ambiguous）人员若分别放入候选级别，会把各级
   别的 min/max 再推开多少（widening），并显式声明他们没有被重复计入；
3. **覆盖展宽**：只覆盖部分观测月份的记录，其取值区间与完整覆盖相比
   额外变宽的幅度。

相邻技能级差按“较高档最低标准工时工资 − 较低档最高标准工时工资”比较
（即带口径 ``lower(高) - upper(低)``），负值即为倒挂；同时给出实测人群中
的同口径倒挂位置。
"""

from dataclasses import dataclass, field
from decimal import Decimal

from .calibration import coverage_ratio, observed_values
from .grading import GradeView


@dataclass(frozen=True)
class GradeStat:
    grade: str
    workers: tuple[str, ...]
    count: int
    minimum: Decimal | None
    maximum: Decimal | None
    mean: Decimal | None
    values: tuple[Decimal, ...]


@dataclass(frozen=True)
class Widening:
    """某一级别因争议/部分覆盖产生的展宽。"""
    grade: str
    tight_minimum: Decimal | None
    tight_maximum: Decimal | None
    widest_minimum: Decimal | None
    widest_maximum: Decimal | None
    downward: Decimal          # 最低值被向下推宽的幅度（正数）
    upward: Decimal            # 最高值被向上推宽的幅度（正数）
    added_workers: tuple[str, ...]
    partial_coverage_workers: tuple[str, ...]


@dataclass(frozen=True)
class GapCheck:
    higher: str
    lower: str
    higher_minimum: Decimal
    lower_maximum: Decimal
    gap: Decimal
    required: Decimal
    shortfall: Decimal          # required - gap；正数表示不达标
    inverted: bool             # gap < 0 即倒挂


@dataclass(frozen=True)
class ObservedInversion:
    higher_grade: str
    lower_grade: str
    higher_worker: str
    lower_worker: str
    higher_value: Decimal
    lower_value: Decimal
    gap: Decimal                # 负值


@dataclass
class DistributionReport:
    caliber: str
    as_of: str
    order: tuple[str, ...]
    tight: dict = field(default_factory=dict)
    ambiguous_workers: tuple = ()
    widening: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    band_gaps: tuple = ()
    observed_inversions: tuple = ()
    notes: tuple = ()


def _stats(grade, worker_values):
    values = tuple(worker_values.values())
    flat = tuple(value for seq in values for value in seq)
    if not flat:
        return GradeStat(grade, tuple(worker_values), 0, None, None, None, ())
    return GradeStat(
        grade=grade,
        workers=tuple(worker_values),
        count=len(worker_values),
        minimum=min(flat), maximum=max(flat),
        mean=(sum(flat) / Decimal(len(flat))).quantize(Decimal("0.01")),
        values=tuple(sorted(flat)),
    )


def analyze_distribution(dataset, book, view: GradeView):
    order = tuple(sorted({row.grade for row in dataset.terms},
                         key=lambda g: _grade_rank(dataset, g)))
    report = DistributionReport(
        caliber=book.caliber, as_of=book.as_of, order=order,
        ambiguous_workers=tuple(sorted(view.ambiguous())),
    )
    notes = []

    # 1) 紧分布：确定归属者，每人取其有固定薪酬月份的全部观测值
    tight_values = {grade: {} for grade in order}
    for worker, resolution in view.determined().items():
        values = observed_values(book, worker)
        if values:
            tight_values[resolution.determined_grade][worker] = values
    report.tight = {grade: _stats(grade, workers)
                    for grade, workers in tight_values.items()}

    # 2) 争议展宽：跨级者若放入每个候选级，各级 min/max 被推开的幅度
    widest = {grade: dict(workers) for grade, workers in tight_values.items()}
    for worker, resolution in view.ambiguous().items():
        values = observed_values(book, worker)
        if not values:
            notes.append(f"{worker} 在观测窗口无固定薪酬记录，"
                         f"争议展宽不纳入其数值（缺月不能以奖金补齐）")
            continue
        for grade in resolution.grades:
            widest.setdefault(grade, {})[worker] = values

    for grade in order:
        tight_stat = report.tight.get(grade) or _stats(grade, {})
        wide_stat = _stats(grade, widest.get(grade, {}))
        added = tuple(sorted(
            set(wide_stat.workers) - set(tight_stat.workers)))
        downward = (_safe(tight_stat.minimum) - _safe(wide_stat.minimum)
                    if tight_stat.minimum is not None and wide_stat.minimum is not None
                    else Decimal(0))
        upward = (_safe(wide_stat.maximum) - _safe(tight_stat.maximum)
                  if tight_stat.maximum is not None and wide_stat.maximum is not None
                  else Decimal(0))
        partial = tuple(sorted(
            w for w in wide_stat.workers
            if coverage_ratio(book, w) < 1))
        report.widening[grade] = Widening(
            grade=grade,
            tight_minimum=tight_stat.minimum, tight_maximum=tight_stat.maximum,
            widest_minimum=wide_stat.minimum, widest_maximum=wide_stat.maximum,
            downward=max(downward, Decimal(0)), upward=max(upward, Decimal(0)),
            added_workers=added, partial_coverage_workers=partial,
        )

    # 3) 覆盖情况（部分月份记录让比较变宽多少，按人员披露）
    coverage = {}
    for employee in dataset.employees:
        worker = employee.worker
        series = book.series(worker)
        if not series:
            continue
        ratio = coverage_ratio(book, worker)
        present = tuple(m.standard_hour_fixed for m in series if not m.missing)
        coverage[worker] = {
            "ratio": ratio.quantize(Decimal("0.0001")),
            "months_observed": len(series),
            "months_covered": book.coverage[worker],
            "missing": book.missing_months[worker],
            "bonus_only_months": book.voided_bonus_months[worker],
            "observed_min": min(present) if present else None,
            "observed_max": max(present) if present else None,
        }
    report.coverage = coverage

    # 4) 带口径级差：lower(高) - upper(低)，对照 min_gap
    term_index = {(row.version, row.grade): row for row in dataset.terms}
    versions = tuple(sorted({row.version for row in dataset.terms}))
    version = versions[-1]
    gaps = []
    for higher, lower in zip(order[1:], order[:-1]):
        hi = term_index[(version, higher)]
        lo = term_index[(version, lower)]
        gap = hi.lower - lo.upper
        gaps.append(GapCheck(
            higher=higher, lower=lower,
            higher_minimum=hi.lower, lower_maximum=lo.upper,
            gap=gap, required=hi.min_gap,
            shortfall=max(hi.min_gap - gap, Decimal(0)),
            inverted=gap < 0,
        ))
    report.band_gaps = tuple(gaps)

    # 5) 实测倒挂：确定归属者相邻级别间，高档最低实测 < 低档最高实测
    inversions = []
    grade_workers = {grade: dict(workers)
                     for grade, workers in tight_values.items()}
    for higher, lower in zip(order[1:], order[:-1]):
        hi_vals = grade_workers.get(higher, {})
        lo_vals = grade_workers.get(lower, {})
        if not hi_vals or not lo_vals:
            continue
        hi_min_worker = min(hi_vals, key=lambda w: min(hi_vals[w]))
        lo_max_worker = max(lo_vals, key=lambda w: max(lo_vals[w]))
        hi_min = min(hi_vals[hi_min_worker])
        lo_max = max(lo_vals[lo_max_worker])
        gap = hi_min - lo_max
        if gap < 0:
            inversions.append(ObservedInversion(
                higher_grade=higher, lower_grade=lower,
                higher_worker=hi_min_worker, lower_worker=lo_max_worker,
                higher_value=hi_min, lower_value=lo_max, gap=gap,
            ))
    report.observed_inversions = tuple(inversions)
    report.notes = tuple(notes)
    return report


def _grade_rank(dataset, grade):
    lowers = [row.lower for row in dataset.terms if row.grade == grade]
    return min(lowers) if lowers else Decimal("Infinity")


def _safe(value):
    return value if value is not None else Decimal(0)
