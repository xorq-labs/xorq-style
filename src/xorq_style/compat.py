from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    from enum import StrEnum  # xorq-style: disable=strenum-compat
else:
    import enum

    class StrEnum(str, enum.Enum):  # xorq-style: disable=enum-placement
        pass


__all__ = ["StrEnum"]
