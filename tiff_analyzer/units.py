"""Pure helpers: size formatting and Shannon entropy."""

from __future__ import annotations

import math
from collections import Counter


def calculate_entropy(data: bytes) -> float:
    """
    Shannon entropy in bits per byte (0.0 - 8.0).

    >>> calculate_entropy(b"")
    0.0
    >>> calculate_entropy(b"AAAA")
    0.0
    >>> calculate_entropy(bytes(range(256)))
    8.0
    >>> round(calculate_entropy(b"AB"), 3)
    1.0
    """
    if not data:
        return 0.0

    size = len(data)
    counts = Counter(data)

    # The minus sign lives inside sum(): sum() starts from int 0, so
    # 0 + (-0.0) == 0.0. Writing -sum(...) would yield -0.0 for uniform data.
    return sum(-(count / size) * math.log2(count / size) for count in counts.values())


def format_size(size: int) -> str:
    """
    A byte count as human readable text.

    >>> format_size(0)
    '0.00 B'
    >>> format_size(1023)
    '1023.00 B'
    >>> format_size(1024)
    '1.00 KB'
    >>> format_size(1536)
    '1.50 KB'
    >>> format_size(1 << 40)
    '1.00 TB'
    """
    units = ("B", "KB", "MB", "GB", "TB")

    value = float(size)

    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"

        value /= 1024

    return f"{value:.2f} TB"
