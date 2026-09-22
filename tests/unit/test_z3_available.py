"""z3-solver がインストールされ、動作することを確認する。"""

from __future__ import annotations

import z3


def test_z3_solves_simple_constraint() -> None:
    s = z3.Solver()
    x = z3.Int("x")
    s.add(x > 0)
    assert s.check() == z3.sat
