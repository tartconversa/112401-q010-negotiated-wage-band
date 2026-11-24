"""工资期（YYYY-MM）与日期的最小工具集。

工资期是本项目的时间原子：预算、承诺、工资单都按月对齐。
日期（职级生效日、凭证收到日、纠正可用日）仅用于判定某个工资期
当时已知哪些证据，比较时统一换算成序数月序号。
"""

from datetime import date

PERIOD_FMT = "%Y-%m"
DATE_FMT = "%Y-%m-%d"


def parse_period(text):
    """把 ``YYYY-MM`` 解析为 ``(年, 月)``。"""
    year_text, month_text = text.split("-")
    year, month = int(year_text), int(month_text)
    if not 1 <= month <= 12:
        raise ValueError(f"非法工资期: {text!r}")
    return year, month


def format_period(year, month):
    return f"{year:04d}-{month:02d}"


def period_index(text):
    """可比较、可做差的月序号。"""
    year, month = parse_period(text)
    return year * 12 + (month - 1)


def diff_months(later, earlier):
    """两个工资期之间相差的月数（later - earlier）。"""
    return period_index(later) - period_index(earlier)


def month_after(text):
    """返回下一工资期。"""
    year, month = parse_period(text)
    total = year * 12 + month
    return format_period(total // 12, total % 12 + 1)


def add_months(text, delta):
    year, month = parse_period(text)
    total = year * 12 + (month - 1) + delta
    return format_period(total // 12, total % 12 + 1)


def iter_periods(start, end):
    """闭区间遍历 start..end 的全部工资期。"""
    if period_index(end) < period_index(start):
        raise ValueError(f"工资期区间倒置: {start} > {end}")
    current = start
    while period_index(current) <= period_index(end):
        yield current
        current = month_after(current)


def periods_intersect(start_a, end_a, start_b, end_b):
    """两个闭区间工资期集合的交集（按期返回）；无交集返回空列表。"""
    lo = start_a if period_index(start_a) >= period_index(start_b) else start_b
    hi = end_a if period_index(end_a) <= period_index(end_b) else end_b
    if period_index(hi) < period_index(lo):
        return []
    return list(iter_periods(lo, hi))


def parse_date(text):
    """解析 ``YYYY-MM-DD``。空字符串返回 None。"""
    if not text:
        return None
    return date.fromisoformat(text)


def period_of_date(value):
    """日期落在哪一个工资期。"""
    if isinstance(value, str):
        value = parse_date(value)
    return format_period(value.year, value.month)
