"""技能工资带的可比测算、分阶段可行性与签认台账。

只使用 Python 3.11 标准库。所有金额使用 :class:`decimal.Decimal`，
工资期使用 ``YYYY-MM`` 字符串，比较口径由调用方显式选定。
"""

from .periods import (
    diff_months,
    format_period,
    iter_periods,
    month_after,
    parse_date,
    parse_period,
    period_of_date,
    periods_intersect,
)

__all__ = [
    "diff_months",
    "format_period",
    "iter_periods",
    "month_after",
    "parse_date",
    "parse_period",
    "period_of_date",
    "periods_intersect",
]
