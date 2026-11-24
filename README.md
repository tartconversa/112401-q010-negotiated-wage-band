# 技能工资带的可比测算、分阶段可行性与签认台账

把产业协会的工资带讨论变成**可审议、可签认、可对账**的过程。输入企业匿名薪酬记录、
岗位工时、带生效期的职级对照和企业年度预算，在各方选定的共同口径下比较标准工时的
固定薪酬分布与相邻技能级差，检验分阶段安排能否同时兑现收入保护、相邻级差与预算，
并以追加式台账保存签认与后续重算，已执行工资期的依据和实发值不可回改。

仅使用 Python 3.11 标准库，金额全部为 `Decimal`，工资期为 `YYYY-MM`。

## 资料约定

CSV 使用 UTF-8，首行为列名。多值字段以竖线分隔。空单元格表示来源未填。

- employees.csv。人员与原招聘工种、标准工资组和现行期薪保护。end 为空表示仍在职。
  字段为 `worker`、`firm`、`family`、`site`、`workload`、`start`、`end`、`protected_monthly`。
- grade_map.csv。各厂原工种级别到技能带级别的可用归属，options 非单值时不能把同一人员重复计入。
  字段为 `firm`、`code`、`options`、`from`、`to`。
- grades.csv。各人的有效职级证明。recorded 为收到证据的日期。后到记录可有早于收到时点的有效日。
  字段为 `proof`、`worker`、`code`、`effective`、`recorded`。
- pay.csv。月工资单拆项。fixed/allowance/bonus 分类按发工资时的原报表。unconditional 为该津贴
  是否无条件支付的证据，void 替代由 corrections 给出。
  字段为 `line`、`worker`、`period`、`kind`、`amount`、`unconditional`。
- terms.csv。标准全额工时为160小时/月。上、下界及 min_gap 均为标准全额工时口径。min_gap 指
  本级与紧邻较低等级的最低差。
  字段为 `version`、`grade`、`lower`、`upper`、`min_gap`、`active`。
- budgets.csv。企业年度固定薪酬增量上限。months 为当年尚可拟议的新带生效期范围。按人事当前
  受保护固定工资为支出比较基准。每个适用月份以实际用工比例计算，一个已签承诺形成的支出只扣一次。
  字段为 `firm`、`year`、`limit`、`months`。
- signoff.csv。业务侧的已签与待议承诺，agreed 是标准化之前应给人员支付的月固定金额。
  字段为 `commitment`、`worker`、`terms`、`start`、`end`、`agreed`、`state`。
- corrections.csv。已收纠正凭证。actual_unconditional 指相应津贴的正确归类，received 是可用日期。
  字段为 `correction`、`lines`、`actual_unconditional`、`received`、`reason`。

## 口径与判定规则（代码即规则）

1. **共同口径二选一，显式传入**：`fixed_only`（只计 fixed）或
   `with_unconditional_allowance`（fixed + 发工资当时证据为无条件的津贴）。
   奖金在任何口径下都不是固定薪酬，**因停产缺失的月份不能用一次性奖金补齐**。
2. **纠正凭证按收到日生效**：`received` 之前维持原报表归类，之后按
   `actual_unconditional` 重分类；历史工资期的依据在签认台账中冻结。
3. **标准工时换算**：实际月固定薪酬 ÷ 本人 `workload`，部分用工人员与全职同口径可比。
4. **职级归属按截至日的证据视图**：只认 `recorded <= 截至日` 的证明；多条证明的候选级
   取交集。单值=已确定，多值=跨级争议（只做区间估计、**不得在各级重复计入**），
   交集为空=证据冲突需人工裁决。年中生效的级别在生效工资期之前不适用。
5. **相邻级差**按较高档 `lower` − 较低档 `upper` 与较高档 `min_gap` 比较，
   不按员工逐个算补差；只约束同一企业同一适用工资期内**相邻两档都有人**的情况，
   空档不构成企业冲突（带本身的结构倒挂由分布报告披露）。
6. **四项硬约束**：固定收入保护（承诺 ≥ 受保护固定工资）、带界（标准工时口径落在
   lower/upper 内，跨级者每个候选级都要满足）、相邻级差、分阶段预算。任何冲突都返回
   冲突限制和**可独立签认的最大范围**，不会调低保护条件本身。
7. **预算**：月增量 = 承诺月固定 − 受保护月固定，按承诺期与预算 months 的交集逐月累计；
   已签承诺的支出只扣一次。企业报年度总额，承诺从不同工资期开始、岗位分期进入新带
   都按期折算。
8. **签认台账只追加**：签认与每次重算各是一条事件，旧事件原样保留。已执行工资期冻结
   当时依据与实发值，重算只产生核对差额并标注是否占用剩余年度额度，不能靠重跑分布表
   改掉已签安排。

## 命令行流程

所有命令在隔离模式下运行（`-I` 不加载当前目录/PYTHONPATH/用户 site，数据目录中即使有
同名模块也不会被导入），数据目录以参数传入：

```bash
# 1) 截至日的职级归属：7 月时 W2 还是 L2/L3 跨级争议
python3 -I run_wageband.py grades --data samples --as-of 2026-07-15

# 2) 共同口径下的固定薪酬分布、争议/缺月展宽、带级差与实测倒挂
python3 -I run_wageband.py distribution --data samples \
    --as-of 2026-07-15 --caliber fixed_only

# 3) 分阶段可行性：保护、带界、相邻级差、预算；冲突时给出可独立签认范围
#    退出码 0=可行，2=有冲突（冲突详情在 stdout）
python3 -I run_wageband.py feasibility --data samples --as-of 2026-07-15

# 4) 签认：存在未解决冲突时默认拒绝；台账为追加式 JSON
python3 -I run_wageband.py sign --data samples --as-of 2026-07-15 \
    --ledger build/ledger.json --commitments S1 S2 S3

# 5) 新证据（后到晋级证明 P8、夜班津贴纠正 D1）到达后重算并对账
python3 -I run_wageband.py recompute --data samples \
    --as-of 2026-08-15 --ledger build/ledger.json
```

各命令均支持 `--json` 输出机器可读结果。

### 样例资料的演练结论

- 7/15 视图：W2 持 b 证为 **L2/L3 跨级**，W7 为 **L1/L2 跨级**，均只出现一次、不重复计入；
  W7 七月只有奖金，缺月不以奖金补齐，覆盖率 1/2。
- T1 带本身两处结构倒挂：L2 下限 6100 − L1 上限 6200 = −100（要求级差 300）；
  L3 下限 6650 − L2 上限 6800 = −150（要求 350）。
- 实测倒挂：L1 的 W3（标准化 5800）高于 L2 的 W6（5733.33）。
- 预算：F1 已签增量 4500/上限 7200；F2 两条拟议合计 2400 > 2200（超 200），
  S4、S5 各自可独立签认；F3 的 S6 标准工时 5900 只满足 L1 带界。
- 8/15 重算：P8 追溯把 W2 落定 **L3**，其 6400 承诺在 L3 带 [6650,7600] 内**不再可支持**，
  旧签认保护保留、未生效部分进待改议；D1 把 W2 的夜班津贴剔除出固定收入，
  七月实发 6200 对承诺 6400 欠付 200，补发占用 F1 剩余年度额度。

## 包结构

| 模块 | 职责 |
| --- | --- |
| `wageband/periods.py` | 工资期/日期工具，月序差、区间交集 |
| `wageband/model.py` | 不可变记录、CSV 装载与一次性关联校验 |
| `wageband/grading.py` | 截至日证据视图、候选级交集、争议/冲突判定 |
| `wageband/calibration.py` | 共同口径、纠正凭证时点、标准工时换算、缺月识别 |
| `wageband/distribution.py` | 紧分布、争议/覆盖展宽、带级差与实测倒挂 |
| `wageband/policy.py` | 四项硬约束、分阶段预算、可独立签认最大子集 |
| `wageband/ledger.py` | 追加式签认台账、政策重算、已执行期对账 |
| `wageband/cli.py` | 五个子命令的文本/JSON 入口 |

## 运行测试

```bash
python3 -I tests/run_tests.py -v
```

资料契约的原始检查仍可通过 `python3 -m unittest discover -s tests -v` 运行
（该方式不使用隔离模式；隔离模式请用上面的启动器）。
