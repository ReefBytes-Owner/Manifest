"""Fixture package B: consumes pkg_a.get_value() with an incompatible type.

pyright's own cross-file inference is what makes this a genuine cross-package
error -- pkg_b never redeclares get_value's return type, it just imports it.
"""

from tests.fixtures.types.pkg_a import get_value


def double(value: str) -> str:
    return value * 2


def use() -> str:
    return double(get_value())
