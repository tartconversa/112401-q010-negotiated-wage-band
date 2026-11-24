"""不可变领域记录与数据装载。

CSV 是各方交付的原始资料，本模块只做解析和结构校验，不做任何口径判断。
所有金额转 Decimal；竖线多值字段转元组；空日期转 None。
"""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .periods import parse_date, parse_period, period_of_date

MONEY = Decimal  # 显性别名，便于阅读


def _money(text):
    value = Decimal(text)
    if value < 0:
        raise ValueError(f"金额不得为负: {text}")
    return value


def _multi(text):
    return tuple(part for part in text.split("|") if part)


def _yesno(text):
    if text == "yes":
        return True
    if text == "no":
        return False
    raise ValueError(f"布尔列只接受 yes/no，收到: {text!r}")


@dataclass(frozen=True)
class Employee:
    worker: str
    firm: str
    family: str
    site: str
    workload: Decimal          # 实际用工比例，标准全额工时为 1
    start: str                  # 入职日期
    end: str | None             # 离职日期，空表示在职
    protected_monthly: Decimal  # 人事当前受保护月固定工资（实际比例口径）


@dataclass(frozen=True)
class GradeMapping:
    firm: str
    code: str
    options: tuple[str, ...]    # 单值=确定归属；多值=只能跨级估计
    valid_from: str
    valid_to: str | None


@dataclass(frozen=True)
class GradeProof:
    proof: str
    worker: str
    code: str
    effective: str              # 职级有效日
    recorded: str               # 证据收到日（可晚于有效日）


@dataclass(frozen=True)
class PayLine:
    line: str
    worker: str
    period: str                 # 工资期 YYYY-MM
    kind: str                   # fixed / allowance / bonus
    amount: Decimal
    unconditional: bool


@dataclass(frozen=True)
class BandTerms:
    version: str
    grade: str
    lower: Decimal              # 标准全额工时口径下界
    upper: Decimal
    min_gap: Decimal            # 与紧邻较低等级的最低级差
    active: str                 # 生效工资期


@dataclass(frozen=True)
class Budget:
    firm: str
    year: int
    limit: Decimal              # 年度固定薪酬增量上限
    months: tuple[str, ...]     # 当年尚可拟议的新带生效期范围


@dataclass(frozen=True)
class Commitment:
    commitment: str
    worker: str
    terms: str
    start: str                  # 承诺适用工资期
    end: str
    agreed: Decimal             # 标准化之前应支付的月固定金额（实际比例口径）
    state: str                  # signed / proposed


@dataclass(frozen=True)
class Correction:
    correction: str
    lines: tuple[str, ...]
    actual_unconditional: bool
    received: str               # 纠正凭证可用日期
    reason: str


@dataclass(frozen=True)
class Dataset:
    employees: tuple[Employee, ...]
    grade_map: tuple[GradeMapping, ...]
    proofs: tuple[GradeProof, ...]
    pay: tuple[PayLine, ...]
    terms: tuple[BandTerms, ...]
    budgets: tuple[Budget, ...]
    commitments: tuple[Commitment, ...]
    corrections: tuple[Correction, ...]

    def employee(self, worker):
        for row in self.employees:
            if row.worker == worker:
                return row
        raise KeyError(f"未知人员: {worker}")

    def budget_of(self, firm, year):
        for row in self.budgets:
            if row.firm == firm and row.year == year:
                return row
        return None

    def terms_version(self, version):
        rows = tuple(row for row in self.terms if row.version == version)
        if not rows:
            raise KeyError(f"未知口径版本: {version}")
        return rows


def _rows(csv_path):
    import csv

    with csv_path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        rows = list(reader)
    if not rows:
        raise ValueError(f"空资料表: {csv_path.name}")
    return rows


def load_dataset(directory):
    """从资料目录读取全部 CSV 并做关联完整性校验。

    校验失败抛 :class:`DataError`，把所有问题一次报清，避免用半截数据测算。
    """
    base = Path(directory)
    problems = []

    def rows(name):
        try:
            return _rows(base / f"{name}.csv")
        except FileNotFoundError:
            problems.append(f"缺少资料表 {name}.csv")
            return []

    employees = []
    for row in rows("employees"):
        try:
            employees.append(Employee(
                worker=row["worker"], firm=row["firm"], family=row["family"],
                site=row["site"], workload=Decimal(row["workload"]),
                start=row["start"], end=row["end"] or None,
                protected_monthly=_money(row["protected_monthly"]),
            ))
        except (ValueError, KeyError) as exc:
            problems.append(f"employees/{row.get('worker', '?')}: {exc}")

    grade_map = []
    for row in rows("grade_map"):
        try:
            options = _multi(row["options"])
            if not options:
                raise ValueError("options 不能为空")
            grade_map.append(GradeMapping(
                firm=row["firm"], code=row["code"], options=options,
                valid_from=row["from"], valid_to=row["to"] or None,
            ))
        except (ValueError, KeyError) as exc:
            problems.append(f"grade_map/{row.get('code', '?')}: {exc}")

    proofs = []
    for row in rows("grades"):
        try:
            proofs.append(GradeProof(
                proof=row["proof"], worker=row["worker"], code=row["code"],
                effective=row["effective"], recorded=row["recorded"],
            ))
            parse_date(row["effective"]); parse_date(row["recorded"])
        except (ValueError, KeyError) as exc:
            problems.append(f"grades/{row.get('proof', '?')}: {exc}")

    pay = []
    for row in rows("pay"):
        try:
            pay.append(PayLine(
                line=row["line"], worker=row["worker"], period=row["period"],
                kind=row["kind"], amount=_money(row["amount"]),
                unconditional=_yesno(row["unconditional"]),
            ))
            parse_period(row["period"])
        except (ValueError, KeyError) as exc:
            problems.append(f"pay/{row.get('line', '?')}: {exc}")

    terms = []
    for row in rows("terms"):
        try:
            terms.append(BandTerms(
                version=row["version"], grade=row["grade"],
                lower=_money(row["lower"]), upper=_money(row["upper"]),
                min_gap=_money(row["min_gap"]), active=row["active"][:7],
            ))
            parse_period(row["active"][:7])
            if Decimal(row["upper"]) < Decimal(row["lower"]):
                raise ValueError("upper 不得低于 lower")
        except (ValueError, KeyError) as exc:
            problems.append(f"terms/{row.get('grade', '?')}: {exc}")

    budgets = []
    for row in rows("budgets"):
        try:
            months = _multi(row["months"])
            months = tuple(period_of_date(f"{m}-01") for m in months)
            budgets.append(Budget(
                firm=row["firm"], year=int(row["year"]),
                limit=_money(row["limit"]), months=months,
            ))
        except (ValueError, KeyError) as exc:
            problems.append(f"budgets/{row.get('firm', '?')}: {exc}")

    commitments = []
    for row in rows("signoff"):
        try:
            state = row["state"]
            if state not in ("signed", "proposed"):
                raise ValueError(f"未知承诺状态 {state!r}")
            commitments.append(Commitment(
                commitment=row["commitment"], worker=row["worker"],
                terms=row["terms"], start=row["start"], end=row["end"],
                agreed=_money(row["agreed"]), state=state,
            ))
            parse_period(row["start"]); parse_period(row["end"])
        except (ValueError, KeyError) as exc:
            problems.append(f"signoff/{row.get('commitment', '?')}: {exc}")

    corrections = []
    for row in rows("corrections"):
        try:
            corrections.append(Correction(
                correction=row["correction"], lines=_multi(row["lines"]),
                actual_unconditional=_yesno(row["actual_unconditional"]),
                received=row["received"], reason=row["reason"],
            ))
            parse_date(row["received"])
        except (ValueError, KeyError) as exc:
            problems.append(f"corrections/{row.get('correction', '?')}: {exc}")

    if problems:
        raise DataError(problems)

    _check_references(employees, grade_map, proofs, pay, terms,
                      budgets, commitments, corrections, problems)
    if problems:
        raise DataError(problems)

    return Dataset(
        employees=tuple(employees), grade_map=tuple(grade_map),
        proofs=tuple(proofs), pay=tuple(pay), terms=tuple(terms),
        budgets=tuple(budgets), commitments=tuple(commitments),
        corrections=tuple(corrections),
    )


def _check_references(employees, grade_map, proofs, pay, terms,
                      budgets, commitments, corrections, problems):
    worker_ids = {row.worker for row in employees}
    firms = {row.firm for row in employees}
    map_keys = {(row.firm, row.code): row for row in grade_map}
    term_versions = {row.version for row in terms}
    line_ids = {row.line for row in pay}

    if len(worker_ids) != len(employees):
        problems.append("employees 中 worker 出现重复")
    for row in grade_map:
        if row.firm not in firms:
            problems.append(f"grade_map/{row.code}: 未知企业 {row.firm}")
    for row in proofs:
        if row.worker not in worker_ids:
            problems.append(f"grades/{row.proof}: 未知人员 {row.worker}")
            continue
        firm = next(e.firm for e in employees if e.worker == row.worker)
        if (firm, row.code) not in map_keys:
            problems.append(
                f"grades/{row.proof}: {row.worker} 所在企业 {firm} "
                f"没有工种代码 {row.code} 的职级对照")
    for row in pay:
        if row.worker not in worker_ids:
            problems.append(f"pay/{row.line}: 未知人员 {row.worker}")
        if row.kind not in ("fixed", "allowance", "bonus"):
            problems.append(f"pay/{row.line}: 未知拆项类型 {row.kind}")
    if len(line_ids) != len(pay):
        problems.append("pay 中 line 出现重复")
    for row in budgets:
        if row.firm not in firms:
            problems.append(f"budgets: 未知企业 {row.firm}")
        if not row.months:
            problems.append(f"budgets/{row.firm}: months 不能为空")
        if any(not m.startswith(str(row.year)) for m in row.months):
            problems.append(f"budgets/{row.firm}: months 与年度 {row.year} 不一致")
    for row in commitments:
        if row.worker not in worker_ids:
            problems.append(f"signoff/{row.commitment}: 未知人员 {row.worker}")
        if row.terms not in term_versions:
            problems.append(f"signoff/{row.commitment}: 未知口径版本 {row.terms}")
        if parse_period(row.end) < parse_period(row.start):
            problems.append(f"signoff/{row.commitment}: 期次倒置")
    for row in corrections:
        missing = [line for line in row.lines if line not in line_ids]
        if missing:
            problems.append(
                f"corrections/{row.correction}: 未知工资单行 {','.join(missing)}")


class DataError(ValueError):
    """资料解析或关联校验失败，附带全部问题。"""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))
