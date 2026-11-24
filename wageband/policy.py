"""分阶段安排的可行性测算与签认范围判定。

四项硬约束，任何一项不满足都不得通过调低保护条件来“凑过”：

1. **固定收入保护**：承诺的月固定金额不得低于本人现行受保护固定工资；
2. **带界**：承诺金额按用工比例折算标准工时口径后，须落在当时适用级别
   的 lower/upper 之内；
3. **相邻级差**：同一企业、同一适用工资期内相邻两档都有人时，按
   ``较高档 lower − 较低档 upper`` 与较高档 min_gap 比较——不按员工
   逐个算补差；
4. **预算**：增量以人事受保护固定工资为基准，按适用工资期逐月计算，
   已签承诺只扣一次；企业报的是年度总额，拟议期次落在预算 months 内。

预算不足或级差冲突时，引擎返回冲突本身和**可独立签认的最大范围**，
供各方改议生效范围，而不是改写工资保护。
"""

from dataclasses import dataclass, field
from decimal import Decimal
from itertools import combinations

from .periods import (
    parse_date,
    parse_period,
    periods_intersect,
)


@dataclass(frozen=True)
class PeriodGrade:
    worker: str
    period: str
    grades: tuple[str, ...]
    status: str  # determined / ambiguous / conflict / none


@dataclass(frozen=True)
class Violation:
    kind: str            # protection / band / gap / budget / timing / grade
    commitment: str
    message: str
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CommitmentCost:
    commitment: str
    worker: str
    firm: str
    applicable_months: tuple[str, ...]
    monthly_increment: Decimal
    workload: Decimal
    total_increment: Decimal
    annual_fixed_outlay: Decimal       # 该承诺覆盖期的固定支出总额
    baseline_outlay: Decimal           # 同期按受保护工资的支出


@dataclass(frozen=True)
class FirmResult:
    firm: str
    budget_limit: Decimal | None
    signed_spend: Decimal
    proposed_spend: Decimal
    remaining_after_signed: Decimal | None
    costs: tuple[CommitmentCost, ...]
    violations: tuple[Violation, ...]
    feasible: bool
    independently_signable: tuple       # 可独立签认的最大承诺编号组合
    conflict_summary: str


@dataclass(frozen=True)
class FeasibilityReport:
    as_of: str
    version: str
    firms: tuple[FirmResult, ...]
    package_commitments: tuple[str, ...]
    feasible: bool
    total_annual_fixed_outlay: Decimal
    total_baseline_outlay: Decimal
    total_increment: Decimal

    def violations(self):
        return tuple(v for firm in self.firms for v in firm.violations)


# ---------------------------------------------------------------- 职级的期次视图

def _month_end(period):
    import calendar
    year, month = parse_period(period)
    return parse_date(f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}")


def grades_for_worker_period(dataset, worker, period, as_of):
    """某人在某工资期适用的级别（只认该期之前已收到且已生效的证明）。"""
    cutoff = parse_date(as_of) if isinstance(as_of, str) else as_of
    employee = dataset.employee(worker)
    end_of_period = _month_end(period)
    map_index = {(m.firm, m.code): m for m in dataset.grade_map}

    sets = []
    used = []
    for proof in sorted(dataset.proofs, key=lambda p: p.recorded):
        if proof.worker != worker:
            continue
        if parse_date(proof.recorded) > cutoff:
            continue
        if parse_date(proof.effective) > end_of_period:
            continue  # 年中才生效的级别，在生效期之前不适用
        mapping = map_index.get((employee.firm, proof.code))
        if mapping is None:
            continue
        eff = parse_date(proof.effective)
        if eff < parse_date(mapping.valid_from):
            continue
        if mapping.valid_to and eff > parse_date(mapping.valid_to):
            continue
        sets.append(set(mapping.options))
        used.append(proof.proof)

    if not sets:
        return PeriodGrade(worker, period, (), "none")
    intersection = set.intersection(*sets)
    order = sorted({row.grade for row in dataset.terms},
                   key=lambda g: min(r.lower for r in dataset.terms
                                     if r.grade == g))
    grades = tuple(sorted(intersection, key=lambda g: order.index(g)))
    if not grades:
        return PeriodGrade(worker, period, (), "conflict")
    status = "determined" if len(grades) == 1 else "ambiguous"
    return PeriodGrade(worker, period, grades, status)


# ---------------------------------------------------------------- 单项校验

def _term_lookup(dataset, version):
    return {row.grade: row for row in dataset.terms_version(version)}


def check_commitment(dataset, commitment, as_of, term_index):
    """对单条承诺逐工资期做保护、带界、生效期校验。"""
    employee = dataset.employee(commitment.worker)
    violations = []
    active_start = min(row.active for row in dataset.terms
                       if row.version == commitment.terms)

    periods = list(_commitment_periods(dataset, commitment))
    if not periods:
        violations.append(Violation(
            "timing", commitment.commitment,
            f"{commitment.commitment}: 承诺期与企业预算可用期/在职期没有交集",
        ))
    for period in periods:
        if period < active_start:
            violations.append(Violation(
                "timing", commitment.commitment,
                f"{commitment.commitment}: 工资期 {period} 早于口径 "
                f"{commitment.terms} 生效期 {active_start}",
                {"period": period, "active": active_start},
            ))
        # 1) 固定收入保护（实际比例口径直接比较）
        if commitment.agreed < employee.protected_monthly:
            violations.append(Violation(
                "protection", commitment.commitment,
                f"{commitment.commitment}/{period}: 承诺月固定 {commitment.agreed} "
                f"低于受保护固定工资 {employee.protected_monthly}",
                {"period": period,
                 "agreed": str(commitment.agreed),
                 "protected": str(employee.protected_monthly)},
            ))
        # 2) 带界（折算标准工时口径）
        standard = (commitment.agreed / employee.workload
                    if employee.workload > 0 else Decimal(0))
        grade_view = grades_for_worker_period(
            dataset, employee.worker, period, as_of)
        if grade_view.status in ("none", "conflict"):
            violations.append(Violation(
                "grade", commitment.commitment,
                f"{commitment.commitment}/{period}: 级别状态为 "
                f"{grade_view.status}，无法判定带界",
                {"period": period, "status": grade_view.status},
            ))
            continue
        for grade in grade_view.grades:
            band = term_index.get(grade)
            if band is None:
                violations.append(Violation(
                    "grade", commitment.commitment,
                    f"{commitment.commitment}/{period}: 口径内不存在级别 {grade}",
                ))
                continue
            if not (band.lower <= standard <= band.upper):
                violations.append(Violation(
                    "band", commitment.commitment,
                    f"{commitment.commitment}/{period}: 标准工时口径 "
                    f"{standard.quantize(Decimal('0.01'))} 超出 {grade} 带界 "
                    f"[{band.lower}, {band.upper}]"
                    + ("（跨级估计，两个候选级均须满足）"
                       if grade_view.status == "ambiguous" else ""),
                    {"period": period, "grade": grade,
                     "standard_rate": str(standard),
                     "lower": str(band.lower), "upper": str(band.upper)},
                ))
    return violations, periods


def _commitment_periods(dataset, commitment):
    """承诺期与企业预算 months（显式列表）、在职期的交集。"""
    employee = dataset.employee(commitment.worker)
    budget = dataset.budget_of(employee.firm,
                               parse_period(commitment.start)[0])
    if budget is None:
        return []
    overlapped = periods_intersect(
        commitment.start, commitment.end,
        budget.months[0], budget.months[-1])
    overlapped = [p for p in overlapped if p in budget.months]
    if employee.end:
        overlapped = [p for p in overlapped if p <= employee.end[:7]]
    return overlapped


def _cost(dataset, commitment, periods):
    employee = dataset.employee(commitment.worker)
    monthly_increment = commitment.agreed - employee.protected_monthly
    return CommitmentCost(
        commitment=commitment.commitment, worker=commitment.worker,
        firm=employee.firm, applicable_months=tuple(periods),
        monthly_increment=monthly_increment, workload=employee.workload,
        total_increment=monthly_increment * len(periods),
        annual_fixed_outlay=commitment.agreed * len(periods),
        baseline_outlay=employee.protected_monthly * len(periods),
    )


# ---------------------------------------------------------------- 相邻级差（期次级）

def check_gap(dataset, firm, period, occupied, term_index, grade_order):
    """occupied: {级别: [worker...]}，同一企业同一工资期有人的级别。

    只在**相邻两档都有人**时按带口径比较级差
    （较高档 lower − 较低档 upper，对照较高档 min_gap）；
    空档不构成企业承诺冲突，带本身的结构倒挂由 distribution 报告披露。
    跨级者可能落入的级别产生“可能违规”，级别落定后需复核。
    返回 Violation 列表。
    """
    violations = []
    present = sorted(occupied, key=lambda g: grade_order.index(g))
    for higher, lower in zip(present[1:], present[:-1]):
        if grade_order.index(higher) - grade_order.index(lower) != 1:
            continue  # 相邻档有空档，不构成“相邻有人的工资档”
        hi_band, lo_band = term_index[higher], term_index[lower]
        gap = hi_band.lower - lo_band.upper
        if gap >= hi_band.min_gap:
            continue
        ambiguous_here = any(
            "AMB" in str(w) for w in occupied[higher] + occupied[lower])
        violations.append(Violation(
            "gap", "",
            f"{firm}/{period}: {higher} 下限 {hi_band.lower} 与 {lower} 上限 "
            f"{lo_band.upper} 的级差为 {gap}，低于最低级差 "
            f"{hi_band.min_gap}（短缺 {hi_band.min_gap - gap}）"
            + ("；其中含跨级估计人员，级别落定后需复核" if ambiguous_here else ""),
            {"firm": firm, "period": period, "higher": higher,
             "lower": lower, "gap": str(gap),
             "required": str(hi_band.min_gap)},
        ))
    return violations


def _occupied_by_period(dataset, selected, as_of):
    """{firm: {period: {grade: [worker(+AMB 标记)]}}}。"""
    occupied = {}
    for commitment in selected:
        employee = dataset.employee(commitment.worker)
        for period in _commitment_periods(dataset, commitment):
            view = grades_for_worker_period(
                dataset, commitment.worker, period, as_of)
            if not view.grades:
                continue
            bucket = occupied.setdefault(employee.firm, {}).setdefault(period, {})
            for grade in view.grades:
                tag = commitment.worker + ("AMB" if view.status == "ambiguous" else "")
                bucket.setdefault(grade, []).append(tag)
    return occupied


# ---------------------------------------------------------------- 整包可行性

def evaluate_package(dataset, as_of, include_proposed=True,
                     commitment_ids=None, version=None):
    """评估一组承诺（默认全部 signed + proposed）能否同时兑现。"""
    if version is None:
        version = sorted({row.version for row in dataset.terms})[-1]
    term_index = _term_lookup(dataset, version)
    grade_order = sorted(term_index, key=lambda g: term_index[g].lower)

    selected = [c for c in dataset.commitments
                if c.state == "signed" or include_proposed]
    if commitment_ids is not None:
        wanted = set(commitment_ids)
        selected = [c for c in selected if c.commitment in wanted]
    selected_ids = {c.commitment for c in selected}

    all_violations = []
    costs_by_firm = {}
    for commitment in selected:
        violations, periods = check_commitment(
            dataset, commitment, as_of, term_index)
        all_violations.extend(violations)
        if periods:
            costs_by_firm.setdefault(
                dataset.employee(commitment.worker).firm, []).append(
                _cost(dataset, commitment, periods))

    occupied = _occupied_by_period(dataset, selected, as_of)
    for firm, period_map in occupied.items():
        for period, bucket in period_map.items():
            all_violations.extend(
                check_gap(dataset, firm, period, bucket,
                          term_index, grade_order))

    firms = []
    for firm in sorted({e.firm for e in dataset.employees}):
        firm_commitments = [c for c in selected
                            if dataset.employee(c.worker).firm == firm]
        firm_costs = tuple(costs_by_firm.get(firm, ()))
        signed = [c for c in firm_commitments if c.state == "signed"]
        proposed = [c for c in firm_commitments if c.state == "proposed"]
        signed_ids = {c.commitment for c in signed}
        signed_spend = sum(
            (cost.total_increment for cost in firm_costs
             if cost.commitment in signed_ids), Decimal(0))
        proposed_spend = sum(
            (cost.total_increment for cost in firm_costs
             if cost.commitment not in signed_ids), Decimal(0))
        budget = next((b for b in dataset.budgets if b.firm == firm), None)
        remaining = (budget.limit - signed_spend) if budget else None

        firm_violations = [v for v in all_violations
                           if _violation_belongs(dataset, v, firm)]
        conflict_kinds = {v.kind for v in firm_violations}

        # 预算约束
        budget_violation = None
        if budget is not None and signed_spend + proposed_spend > budget.limit:
            over = signed_spend + proposed_spend - budget.limit
            budget_violation = Violation(
                "budget", "",
                f"{firm}: 拟议增量合计 {signed_spend + proposed_spend}，"
                f"年度上限 {budget.limit}，超支 {over}（已签占 {signed_spend}，"
                f"剩余额度 {remaining}）",
                {"firm": firm, "limit": str(budget.limit),
                 "signed_spend": str(signed_spend),
                 "proposed_spend": str(proposed_spend),
                 "over": str(over)},
            )
            firm_violations.append(budget_violation)
            conflict_kinds.add("budget")

        # 可独立签认的最大拟议范围（已签承诺不在枚举内，它们独立成块）
        signable = _max_signable_subsets(
            dataset, firm, signed, proposed, as_of, term_index,
            grade_order, signed_spend, budget)
        feasible = not firm_violations
        summary = _conflict_summary(
            conflict_kinds, firm, budget, signed_spend, proposed_spend,
            remaining, signable)
        firms.append(FirmResult(
            firm=firm,
            budget_limit=budget.limit if budget else None,
            signed_spend=signed_spend, proposed_spend=proposed_spend,
            remaining_after_signed=remaining, costs=tupled(firm_costs),
            violations=tuple(firm_violations), feasible=feasible,
            independently_signable=tuple(signable),
            conflict_summary=summary,
        ))

    total_outlay = sum((c.annual_fixed_outlay for f in firms
                        for c in f.costs), Decimal(0))
    total_baseline = sum((c.baseline_outlay for f in firms
                          for c in f.costs), Decimal(0))
    return FeasibilityReport(
        as_of=as_of if isinstance(as_of, str) else as_of.isoformat(),
        version=version, firms=tuple(firms),
        package_commitments=tuple(sorted(selected_ids)),
        feasible=all(f.feasible for f in firms),
        total_annual_fixed_outlay=total_outlay,
        total_baseline_outlay=total_baseline,
        total_increment=total_outlay - total_baseline,
    )


def tupled(items):
    return tuple(sorted(items, key=lambda c: c.commitment))


def _violation_belongs(dataset, violation, firm):
    if violation.kind in ("gap", "budget"):
        return violation.detail.get("firm") == firm
    if not violation.commitment:
        return False
    return dataset.employee(
        next(c.worker for c in dataset.commitments
             if c.commitment == violation.commitment)).firm == firm


def _max_signable_subsets(dataset, firm, signed, proposed, as_of,
                          term_index, grade_order, signed_spend, budget):
    """枚举拟议承诺中可独立签认的最大子集。

    级差基准只包含**已签承诺**形成的在岗级别；拟议承诺之间再做组合枚举。
    任何子集都不得触发硬约束或超预算，保护条件本身不被改写。
    """
    if not proposed:
        return []

    def add_commitment(target, commitment):
        for period in _commitment_periods(dataset, commitment):
            view = grades_for_worker_period(
                dataset, commitment.worker, period, as_of)
            bucket = target.setdefault(period, {})
            for grade in view.grades:
                tag = commitment.worker + (
                    "AMB" if view.status == "ambiguous" else "")
                bucket.setdefault(grade, []).append(tag)

    base_occupied = {}
    for commitment in signed:
        add_commitment(base_occupied, commitment)

    def subset_ok(group):
        spend = signed_spend + sum(
            (c.agreed - dataset.employee(c.worker).protected_monthly)
            * len(_commitment_periods(dataset, c)) for c in group)
        if budget is not None and spend > budget.limit:
            return False
        for commitment in group:
            violations, _ = check_commitment(
                dataset, commitment, as_of, term_index)
            if violations:
                return False
        trial_occupied = {
            period: {grade: list(workers)
                     for grade, workers in bucket.items()}
            for period, bucket in base_occupied.items()}
        for commitment in group:
            add_commitment(trial_occupied, commitment)
        for period, bucket in trial_occupied.items():
            if check_gap(dataset, firm, period, bucket,
                         term_index, grade_order):
                return False
        return True

    ids = [c.commitment for c in proposed]
    by_id = {c.commitment: c for c in proposed}
    for size in range(len(ids), 0, -1):
        winners = []
        for combo in combinations(ids, size):
            if subset_ok([by_id[i] for i in combo]):
                winners.append(combo)
        if winners:
            # 去掉被更大可行集包含的较小集（本层已是最大层，直接返回）
            return sorted(winners)
    return []


def _conflict_summary(kinds, firm, budget, signed_spend, proposed_spend,
                      remaining, signable):
    if not kinds:
        return f"{firm}: 全部约束满足，可整体签认"
    labels = {"protection": "固定收入保护", "band": "工资带上下界",
              "gap": "相邻技能级差", "budget": "企业年度预算",
              "timing": "生效期次", "grade": "职级归属未定"}
    parts = [labels[k] for k in sorted(kinds)]
    text = f"{firm}: 触发{'、'.join(parts)}冲突，保护条件未被压低；"
    if signable:
        text += "可独立签认的最大范围: " + "；".join(
            "(" + "、".join(combo) + ")" for combo in signable)
    else:
        text += "当前拟议无一可独立签认，需改议生效范围或期次"
    return text
