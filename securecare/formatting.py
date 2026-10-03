"""Small, dependency-free formatting helpers."""
from __future__ import annotations


def format_inr(amount: float) -> str:
    """Format a number with Indian digit grouping, e.g. 127500 -> '₹1,27,500'."""
    n = int(round(amount))
    sign = "-" if n < 0 else ""
    digits = str(abs(n))
    if len(digits) <= 3:
        return f"{sign}₹{digits}"
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return f"{sign}₹{','.join(parts + [tail])}"
