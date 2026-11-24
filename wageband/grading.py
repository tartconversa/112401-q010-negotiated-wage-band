"""职级归属：在指定“截至日”的证据视图下判定每人的技能带级别。

规则（对应讨论口径）：

1. 只有 ``recorded <= as_of`` 的职级证明在当时可见；后到的证明即便
   ``effective`` 更早，也不能在其收到前参与判断（收到后追溯有效日）。
2. 每条证明通过本厂 ``grade_map`` 映射为一个候选级别集合；集合单值表示
   已确定归属，多值表示只能跨级估计。
3. 同一人多条证明的候选集合取交集——交集为单值即确定；为多值即有争议，
   只能做区间估计且不得重复计入；交集为空是证据冲突，必须人工裁决。
4. 对照在证据有效日仍处于有效期（from/to），过期对照不得使用。
"""

from dataclasses import dataclass, field

from .periods import parse_date


@dataclass(frozen=True)
class GradeResolution:
    worker: str
    firm: str
    grades: tuple[str, ...]          # 候选级别（已按级别序排序）
    status: str                      # determined / ambiguous / conflict / none
    used_proofs: tuple[str, ...]     # 参与判断的证明
    pending_proofs: tuple[str, ...]  # 截至日之后才收到、当时不可见的证明
    note: str = ""

    @property
    def determined_grade(self):
        return self.grades[0] if self.status == "determined" else None


@dataclass
class GradeView:
    as_of: str
    resolutions: dict = field(default_factory=dict)

    def get(self, worker):
        return self.resolutions[worker]

    def determined(self):
        return {w: r for w, r in self.resolutions.items()
                if r.status == "determined"}

    def ambiguous(self):
        return {w: r for w, r in self.resolutions.items()
                if r.status == "ambiguous"}

    def conflicts(self):
        return {w: r for w, r in self.resolutions.items()
                if r.status == "conflict"}


def _mapping_active(mapping, effective):
    eff = parse_date(effective)
    start = parse_date(mapping.valid_from)
    if eff < start:
        return False
    if mapping.valid_to is not None and eff > parse_date(mapping.valid_to):
        return False
    return True


def grade_order(dataset):
    """从 terms 取级别名称的统一顺序（按 lower 升序）。"""
    versions = {row.version for row in dataset.terms}
    if len(versions) != 1:
        # 多版本时调用方应自行指定；这里仅用于排序，取任一版本的级别并集
        pass
    ordered = sorted(
        {(row.grade, row.lower) for row in dataset.terms},
        key=lambda item: item[1],
    )
    seen = []
    for grade, _ in ordered:
        if grade not in seen:
            seen.append(grade)
    return seen


def resolve_grades(dataset, as_of):
    """计算 ``as_of`` 当天所有人的职级归属视图。

    ``as_of`` 接受 ``YYYY-MM-DD`` 或日期。
    """
    cutoff = parse_date(as_of) if isinstance(as_of, str) else as_of
    order = grade_order(dataset)
    order_index = {grade: i for i, grade in enumerate(order)}
    map_index = {(m.firm, m.code): m for m in dataset.grade_map}
    view = GradeView(as_of=cutoff.isoformat())

    for employee in dataset.employees:
        proofs = sorted(
            (p for p in dataset.proofs if p.worker == employee.worker),
            key=lambda p: p.recorded,
        )
        visible = [p for p in proofs if parse_date(p.recorded) <= cutoff]
        pending = tuple(p.proof for p in proofs
                        if parse_date(p.recorded) > cutoff)

        if not visible:
            view.resolutions[employee.worker] = GradeResolution(
                worker=employee.worker, firm=employee.firm,
                grades=(), status="none", used_proofs=(),
                pending_proofs=pending, note="截至日没有任何已收到的职级证明",
            )
            continue

        candidate_sets = []
        used = []
        stale = []
        for proof in visible:
            mapping = map_index.get((employee.firm, proof.code))
            if mapping is None or not _mapping_active(mapping, proof.effective):
                stale.append(proof.proof)
                continue
            candidate_sets.append(set(mapping.options))
            used.append(proof.proof)

        if not candidate_sets:
            view.resolutions[employee.worker] = GradeResolution(
                worker=employee.worker, firm=employee.firm,
                grades=(), status="none", used_proofs=(),
                pending_proofs=pending,
                note=f"证明 {','.join(stale)} 在其有效日找不到有效职级对照",
            )
            continue

        intersection = set.intersection(*candidate_sets)
        grades = tuple(sorted(intersection, key=lambda g: order_index.get(g, 999)))
        note = ""
        note_parts = []
        if stale:
            note_parts.append(f"证明 {','.join(stale)} 的对照已过期，未采用")

        if not intersection:
            status, note = "conflict", "多条证明的候选级别互不相交，需人工裁决"
        elif len(intersection) == 1:
            status = "determined"
        else:
            status = "ambiguous"
            note = "候选级别跨级，只能做区间估计，不得在各级重复计入"
        if note_parts:
            note = "；".join([*note_parts, note] if note else note_parts)

        view.resolutions[employee.worker] = GradeResolution(
            worker=employee.worker, firm=employee.firm,
            grades=grades, status=status, used_proofs=tuple(used),
            pending_proofs=pending, note=note,
        )

    return view


def grade_span(view):
    """返回 ``{worker: (最低候选级, 最高候选级)}``，确定者两端相同。"""
    return {
        worker: (res.grades[0], res.grades[-1])
        for worker, res in view.resolutions.items() if res.grades
    }
