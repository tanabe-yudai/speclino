"""解析上限。既定は控えめ、エージェントの analyze_focus で最大値まで引き上げる。"""

from __future__ import annotations

from speclino.domain.code_ir import AnalysisLimits

DEFAULT_LIMITS = AnalysisLimits(depth=2, max_paths=64, solver_timeout_ms=500)
MAX_LIMITS = AnalysisLimits(depth=4, max_paths=256, solver_timeout_ms=1000)


def clamp(limits: AnalysisLimits) -> AnalysisLimits:
    return AnalysisLimits(
        depth=min(limits.depth, MAX_LIMITS.depth),
        max_paths=min(limits.max_paths, MAX_LIMITS.max_paths),
        solver_timeout_ms=min(limits.solver_timeout_ms, MAX_LIMITS.solver_timeout_ms),
    )
