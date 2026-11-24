# 技能工资带的可比测算与调薪方案签认

技能工资带演练用月度工资与入档记录。金额为元。标准工时、带内上下界和级差按月讨论，年度增量为各适用工资期之和。

## 资料约定

CSV 使用 UTF-8，首行为列名。多值字段以竖线分隔。空单元格表示来源未填。

- employees.csv。人员与原招聘工种、标准工资组和现行期薪保护。end 为空表示仍在职。
  字段为 `worker`、`firm`、`family`、`site`、`workload`、`start`、`end`、`protected_monthly`。
- grade_map.csv。各厂原工种级别到技能带级别的可用归属，options 非单值时不能把同一人员重复计入。
  字段为 `firm`、`code`、`options`、`from`、`to`。
- grades.csv。各人的有效职级证明。recorded 为收到证据的日期。后到记录可有早于收到时点的有效日。
  字段为 `proof`、`worker`、`code`、`effective`、`recorded`。
- pay.csv。月工资单拆项。fixed/allowance/bonus 分类按发工资时的原报表。unconditional 为该津贴是否无条件支付的证据，void 替代由 corrections 给出。
  字段为 `line`、`worker`、`period`、`kind`、`amount`、`unconditional`。
- terms.csv。标准全额工时为160小时/月。上、下界及 min_gap 均为标准全额工时口径。min_gap 指本级与紧邻较低等级的最低差。
  字段为 `version`、`grade`、`lower`、`upper`、`min_gap`、`active`。
- budgets.csv。企业年度固定薪酬增量上限。months 为当年尚可拟议的新带生效期范围。 按人事当前受保护固定工资为支出比较基准。每个适用月份以实际用工比例计算，一个已签承诺形成的支出只扣一次。
  字段为 `firm`、`year`、`limit`、`months`。
- signoff.csv。业务侧的已签与待议承诺，agreed 是标准化之前应给人员支付的月固定金额。
  字段为 `commitment`、`worker`、`terms`、`start`、`end`、`agreed`、`state`。
- corrections.csv。已收纠正凭证。actual_unconditional 指相应津贴的正确归类，received 是可用日期。
  字段为 `correction`、`lines`、`actual_unconditional`、`received`、`reason`。

## 检查演练资料

使用 Python 3.11.2 标准库，无需安装第三方依赖。在项目根目录运行下列命令可检查 CSV 读取、资料关联及样例记录完整性。

```bash
python3 -m unittest discover -s tests -v
```
