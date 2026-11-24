"""命令行入口：把资料变成可审议、可签认、可对账的过程。

子命令：
  grades       截至日的职级归属（确定/跨级争议/待收证明）
  distribution 共同口径下的固定薪酬分布、展宽与倒挂
  feasibility  分阶段安排的四项硬约束、预算与可独立签认范围
  sign         记录一次签认（追加式台账）
  recompute    后到证明/纠正凭证下的政策重算与对账
"""

import argparse
import json
import sys
from decimal import Decimal

from .calibration import CALIBERS, build_book
from .distribution import analyze_distribution
from .grading import resolve_grades
from .ledger import Ledger, recompute_policy
from .model import DataError, load_dataset
from .policy import evaluate_package


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return {k: _jsonable(getattr(value, k))
                for k in value.__dataclass_fields__}
    return value


def _print_json(payload):
    print(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2))


def cmd_grades(args):
    dataset = load_dataset(args.data)
    view = resolve_grades(dataset, args.as_of)
    if args.json:
        _print_json({"as_of": view.as_of,
                     "resolutions": list(view.resolutions.values())})
        return 0
    print(f"职级归属视图（截至 {view.as_of}）")
    for worker in sorted(view.resolutions):
        r = view.resolutions[worker]
        label = {"determined": "已确定", "ambiguous": "跨级争议",
                 "conflict": "证据冲突", "none": "无可用证明"}[r.status]
        grades = "/".join(r.grades) if r.grades else "—"
        print(f"  {worker} [{r.firm}] {label:<6} 级别={grades}")
        if r.used_proofs:
            print(f"      采用证明: {', '.join(r.used_proofs)}")
        if r.pending_proofs:
            print(f"      截至日后才收到: {', '.join(r.pending_proofs)}")
        if r.note:
            print(f"      说明: {r.note}")
    return 0


def cmd_distribution(args):
    dataset = load_dataset(args.data)
    view = resolve_grades(dataset, args.as_of)
    book = build_book(dataset, args.caliber, args.as_of)
    report = analyze_distribution(dataset, book, view)
    if args.json:
        _print_json(report)
        return 0
    print(f"固定薪酬分布（口径={args.caliber}，截至 {args.as_of}）")
    print("  紧分布（仅确定归属者，标准工时口径）：")
    for grade in report.order:
        stat = report.tight.get(grade)
        if stat is None or stat.count == 0:
            print(f"    {grade}: 无确定归属观测")
            continue
        print(f"    {grade}: 人数={stat.count} 最低={stat.minimum} "
              f"最高={stat.maximum} 均值={stat.mean} 人员={','.join(stat.workers)}")
    if report.ambiguous_workers:
        print("  争议展宽（跨级估计，未重复计入）：")
        for grade, w in report.widening.items():
            if w.added_workers:
                print(f"    {grade}: 纳入 {','.join(w.added_workers)} 后 "
                      f"区间 [{w.widest_minimum}, {w.widest_maximum}]，"
                      f"向下展宽 {w.downward}、向上展宽 {w.upward}")
    print("  部分月份覆盖（缺月不以奖金补齐）：")
    for worker, info in report.coverage.items():
        if info["months_covered"] < info["months_observed"]:
            bonus = (f"，其中 {','.join(info['bonus_only_months'])} "
                     f"仅有奖金" if info["bonus_only_months"] else "")
            print(f"    {worker}: 覆盖 {info['months_covered']}/"
                  f"{info['months_observed']} 月，缺 {','.join(info['missing'])}"
                  f"{bonus}，观测区间 "
                  f"[{info['observed_min']}, {info['observed_max']}]")
    print("  带口径相邻级差（较高档 lower − 较低档 upper）：")
    for gap in report.band_gaps:
        flag = "倒挂!" if gap.inverted else (
            f"低于最低级差，短缺 {gap.shortfall}" if gap.shortfall else "达标")
        print(f"    {gap.lower}→{gap.higher}: 级差={gap.gap} "
              f"(要求≥{gap.required}) {flag}")
    if report.observed_inversions:
        print("  实测倒挂位置：")
        for inv in report.observed_inversions:
            print(f"    {inv.lower_grade} 的 {inv.lower_worker}="
                  f"{inv.lower_value} 高于 {inv.higher_grade} 的 "
                  f"{inv.higher_worker}={inv.higher_value}，差额 {inv.gap}")
    for note in report.notes:
        print(f"  注: {note}")
    return 0


def cmd_feasibility(args):
    dataset = load_dataset(args.data)
    ids = args.commitments or None
    report = evaluate_package(
        dataset, args.as_of,
        include_proposed=not args.signed_only,
        commitment_ids=ids)
    if args.json:
        _print_json(report)
        return 0
    print(f"分阶段可行性（截至 {args.as_of}，口径版本 {report.version}）")
    for firm in report.firms:
        print(f"  {firm.firm}: {'可行' if firm.feasible else '不可行'}")
        limit = firm.budget_limit if firm.budget_limit is not None else "无预算"
        print(f"    预算上限={limit} 已签增量={firm.signed_spend} "
              f"拟议增量={firm.proposed_spend} "
              f"签后剩余={firm.remaining_after_signed}")
        for cost in firm.costs:
            print(f"    {cost.commitment}({cost.worker}): "
                        f"适用 {len(cost.applicable_months)} 期 "
                        f"{cost.applicable_months[0]}..{cost.applicable_months[-1]}，"
                        f"月增 {cost.monthly_increment}，"
                        f"增量合计 {cost.total_increment}，"
                        f"覆盖期固定支出 {cost.annual_fixed_outlay}")
        if firm.violations:
            print("    冲突限制：")
            for v in firm.violations:
                print(f"      - [{v.kind}] {v.message}")
        print(f"    结论: {firm.conflict_summary}")
    print(f"  全年固定支出合计={report.total_annual_fixed_outlay}，"
          f"基准支出={report.total_baseline_outlay}，"
          f"增量合计={report.total_increment}")
    return 0 if report.feasible else 2


def cmd_sign(args):
    dataset = load_dataset(args.data)
    ledger = Ledger.open(args.ledger)
    ids = args.commitments or [c.commitment for c in dataset.commitments
                               if c.state == "signed"]
    report = evaluate_package(dataset, args.as_of, commitment_ids=ids)
    if not report.feasible and not args.force:
        print("拒绝签认：存在未解决冲突，可先看 feasibility 的可独立签认范围",
              file=sys.stderr)
        for firm in report.firms:
            for v in firm.violations:
                print(f"  - [{v.kind}] {v.message}", file=sys.stderr)
        return 2
    periods = sorted({p for firm in report.firms for cost in firm.costs
                      for p in cost.applicable_months})
    event = ledger.record_signoff(
        scope=ids, periods=periods, caliber=args.caliber,
        basis={"as_of": args.as_of, "version": report.version,
               "data_dir": str(args.data)},
        package_report=report, note=args.note or "")
    print(f"已追加签认事件 {event['event_id']}，范围={sorted(ids)}，"
          f"期次={periods[0]}..{periods[-1]}")
    return 0


def cmd_recompute(args):
    dataset = load_dataset(args.data)
    ledger = Ledger.open(args.ledger) if args.ledger else None
    report = recompute_policy(dataset, args.as_of,
                              caliber=args.caliber, ledger=ledger)
    if args.json:
        _print_json(report)
        return 0
    print(f"政策重算（截至 {args.as_of}，口径={args.caliber}）")
    if report["grade_changes"]:
        print("  后到晋级证明引起的级别变化：")
        for change in report["grade_changes"]:
            tag = "（追溯生效）" if change["retroactive"] else ""
            print(f"    {change['proof']} {change['worker']}: "
                  f"{'/'.join(change['grades_before']) or '—'} → "
                  f"{'/'.join(change['grades_after'])}{tag}，"
                  f"有效日 {change['effective']}，收到日 {change['recorded']}")
    if report["correction_effects"]:
        print("  纠正凭证的重分类：")
        for effect in report["correction_effects"]:
            print(f"    {effect['line']} {effect['worker']}/{effect['period']} "
                  f"{effect['amount']}: 原报无条件={effect['reported_unconditional']} "
                  f"→ 实际={effect['actual_unconditional']}，"
                  f"现计固定={effect['now_counted_as_fixed']}")
    print("  承诺逐条结论：")
    for item in report["commitments"]:
        label = {"supported": "仍可支持", "renegotiate": "待改议",
                 "executed_frozen": "已执行冻结"}[item["status"]]
        print(f"    {item['commitment']}({item['worker']}, {item['state']}): "
              f"{label}，可支持级别={'/'.join(item['supportable_grades']) or '无'}")
        for finding in item["findings"]:
            print(f"      - {finding}")
    if report["package_conflicts"]:
        print("  整包层面冲突（单条合规但组合不达标，保护条件未被压低）：")
        for conflict in report["package_conflicts"]:
            print(f"    [{conflict['kind']}] {conflict['message']}")
    if report["executed_reconciliation"]:
        print("  已执行工资期对账（依据与实发值冻结）：")
        for row in report["executed_reconciliation"]:
            print(f"    {row['worker']} {row['period']}: 应发 {row['agreed']} "
                  f"实发 {row['actual_fixed']} 差额 {row['variance']}，"
                  f"补发占用剩余额度={row['consumes_remaining_budget']}")
    for note in report["budget_note"]:
        print(f"  额度: {note['firm']} 待补发 {note['pending_makeup']} "
              f"/ 年度上限 {note['budget_limit']}")
    print(f"  继续受保护的已签承诺: {', '.join(report['protected_signed']) or '无'}")
    print(f"  待改议队列: {', '.join(report['renegotiation_queue']) or '无'}")
    if ledger is not None:
        print(f"  重算结论已追加到台账 {ledger.path}（历史签认未改动）")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="wageband",
        description="技能工资带可比测算、分阶段可行性与签认台账")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--data", default="samples", help="CSV 资料目录")
        p.add_argument("--as-of", required=True, help="判定截至日 YYYY-MM-DD")
        p.add_argument("--json", action="store_true", help="输出 JSON")

    p = sub.add_parser("grades", help="截至日职级归属")
    add_common(p)
    p.set_defaults(func=cmd_grades)

    p = sub.add_parser("distribution", help="分布、展宽与倒挂")
    add_common(p)
    p.add_argument("--caliber", choices=CALIBERS,
                   default="with_unconditional_allowance")
    p.set_defaults(func=cmd_distribution)

    p = sub.add_parser("feasibility", help="分阶段可行性")
    add_common(p)
    p.add_argument("--signed-only", action="store_true",
                   help="只评估已签承诺")
    p.add_argument("--commitments", nargs="*",
                   help="限定承诺编号，默认全部")
    p.set_defaults(func=cmd_feasibility)

    p = sub.add_parser("sign", help="追加签认事件")
    add_common(p)
    p.add_argument("--ledger", required=True, help="台账 JSON 路径")
    p.add_argument("--caliber", choices=CALIBERS,
                   default="with_unconditional_allowance")
    p.add_argument("--commitments", nargs="*",
                   help="签认范围；默认仅已签承诺")
    p.add_argument("--note", default="")
    p.add_argument("--force", action="store_true",
                   help="存在冲突仍记录（正常流程不允许）")
    p.set_defaults(func=cmd_sign)

    p = sub.add_parser("recompute", help="新证据下重算与对账")
    add_common(p)
    p.add_argument("--ledger", help="提供则把重算事件追加到台账")
    p.add_argument("--caliber", choices=CALIBERS,
                   default="with_unconditional_allowance")
    p.set_defaults(func=cmd_recompute)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DataError as exc:
        print(f"资料校验失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
