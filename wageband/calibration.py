"""共同口径下的固定薪酬标准化。

两类共同口径（由各方在测算时显式选定）：

* ``fixed_only``：只计原报表中的 fixed 拆项；
* ``with_unconditional_allowance``：fixed + 发工资当时证据显示
  ``unconditional=yes`` 的津贴。

奖金（bonus）在任何口径下都不是固定薪酬，因此**不能**用一次性奖金
补齐因停产缺失的月份。

纠正凭证（corrections）在其 ``received`` 日之后才可用：可用后，被纠正的
津贴行按 ``actual_unconditional`` 重新归类；可用之前维持原报表判断。
所有金额按本人实际用工比例 ``workload`` 折算为标准全额工时（160h/月）
口径，部分用工人员由此与全职人员可比。
"""

from dataclasses import dataclass, field
from decimal import Decimal

from .model import PayLine
from .periods import parse_date

CALIBERS = ("fixed_only", "with_unconditional_allowance")


@dataclass(frozen=True)
class MonthlyPay:
    worker: str
    period: str
    actual_fixed: Decimal          # 实际比例口径的月固定薪酬
    standard_hour_fixed: Decimal   # 标准全额工时口径
    workload: Decimal
    included_lines: tuple[str, ...]
    excluded_lines: tuple[str, ...]  # 被口径排除的行（奖金/非无条件津贴）
    missing: bool                  # 该月没有任何固定薪酬记录
    corrected_lines: tuple[str, ...]


@dataclass
class CaliberBook:
    """某一口径、某一截至日下的标准化工资账。"""
    caliber: str
    as_of: str
    months: tuple[str, ...]
    by_worker: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)       # worker -> 有固定薪酬的月数
    missing_months: dict = field(default_factory=dict)  # worker -> 缺月列表
    applied_corrections: tuple = ()
    voided_bonus_months: dict = field(default_factory=dict)

    def series(self, worker):
        return self.by_worker.get(worker, ())


def _correction_index(dataset, as_of):
    """返回截至日可用的纠正：{工资单行: 是否无条件}。"""
    cutoff = parse_date(as_of) if isinstance(as_of, str) else as_of
    index = {}
    applied = []
    for correction in dataset.corrections:
        if parse_date(correction.received) > cutoff:
            continue
        for line in correction.lines:
            index[line] = correction.actual_unconditional
        applied.append(correction.correction)
    return index, tuple(applied)


def _line_is_fixed(line: PayLine, correction_index):
    """按发工资当时（经可用纠正修正后）的证据判断该行是否计入固定薪酬。"""
    if line.kind == "bonus":
        return False
    if line.kind == "fixed":
        return True
    if line.kind == "allowance":
        unconditional = correction_index.get(line.line, line.unconditional)
        return unconditional
    raise ValueError(f"未知拆项类型: {line.kind}")


def build_book(dataset, caliber, as_of):
    """构造标准化工资账。

    观测窗口取工资单中出现过的全部工资期；在窗口内没有任何固定薪酬行的
    人员-月份标记为 missing，即使当月存在奖金也不补齐。
    """
    if caliber not in CALIBERS:
        raise ValueError(f"未知口径 {caliber!r}，可选: {CALIBERS}")
    cutoff = parse_date(as_of) if isinstance(as_of, str) else as_of
    correction_index, applied = _correction_index(dataset, cutoff)

    all_periods = tuple(sorted({line.period for line in dataset.pay}))
    book = CaliberBook(caliber=caliber, as_of=cutoff.isoformat(),
                       months=all_periods, applied_corrections=applied)

    for employee in dataset.employees:
        worker = employee.worker
        rows = tuple(line for line in dataset.pay if line.worker == worker)
        # 仅统计入职后、且在观测窗口内的月份
        employed_periods = tuple(
            period for period in all_periods
            if _period_employed(period, employee.start, employee.end)
        )
        monthly = []
        covered = 0
        missing = []
        bonus_only = []
        for period in employed_periods:
            period_rows = tuple(line for line in rows if line.period == period)
            included, excluded, corrected = [], [], []
            for line in period_rows:
                if line.line in correction_index:
                    corrected.append(line.line)
                if _line_is_fixed(line, correction_index):
                    if caliber == "fixed_only" and line.kind == "allowance":
                        excluded.append(line.line)
                    else:
                        included.append(line.line)
                else:
                    excluded.append(line.line)
            actual_fixed = sum(
                (line.amount for line in period_rows
                 if line.line in included), Decimal(0))
            has_fixed_record = any(
                line.kind in ("fixed", "allowance") for line in period_rows)
            is_missing = not included
            if is_missing:
                missing.append(period)
                if period_rows and all(line.kind == "bonus"
                                       for line in period_rows):
                    bonus_only.append(period)
            else:
                covered += 1
            standard = (actual_fixed / employee.workload
                        if employee.workload > 0 else Decimal(0))
            monthly.append(MonthlyPay(
                worker=worker, period=period, actual_fixed=actual_fixed,
                standard_hour_fixed=standard.quantize(Decimal("0.01")),
                workload=employee.workload,
                included_lines=tuple(included),
                excluded_lines=tuple(excluded), missing=is_missing,
                corrected_lines=tuple(corrected),
            ))
        book.by_worker[worker] = tuple(monthly)
        book.coverage[worker] = covered
        book.missing_months[worker] = tuple(missing)
        book.voided_bonus_months[worker] = tuple(bonus_only)

    return book


def _period_employed(period, start, end):
    from .periods import parse_period
    py, pm = parse_period(period)
    sy, sm = parse_period(start[:7])
    if (py, pm) < (sy, sm):
        return False
    if end:
        ey, em = parse_period(end[:7])
        if (py, pm) > (ey, em):
            return False
    return True


def observed_values(book, worker):
    """该人员有固定薪酬月份的标准工时口径序列（缺月不参与分布）。"""
    return tuple(round(m.standard_hour_fixed, 2)
                 for m in book.series(worker) if not m.missing)


def coverage_ratio(book, worker):
    series = book.series(worker)
    if not series:
        return Decimal(0)
    return Decimal(book.coverage[worker]) / Decimal(len(series))
