"""S43: ワークスペースの各 project の規約の結果を規約ごとに集計する。"""

from __future__ import annotations

from speclino.domain.finding import VerdictCounts
from speclino.domain.project_overview import PolicyView
from speclino.services.team_policies import aggregate_results


def _p(rule: str, status: str | None, origin: str = "team") -> PolicyView:
    return PolicyView(
        rule_id=rule,
        origin=origin,  # type: ignore[arg-type]
        title=None,
        level=None,
        has_verifier=None,
        status=status,  # type: ignore[arg-type]
        counts=VerdictCounts(),
    )


def test_worst_status_and_project_counts() -> None:
    results = aggregate_results(
        [[_p("A", "VERIFIED_IN_SCOPE"), _p("B", None)], [_p("A", "VIOLATED")]]
    )
    assert results["A"].status == "VIOLATED"
    assert (results["A"].projects, results["A"].failing_projects) == (2, 1)
    assert results["B"].status is None and results["B"].projects == 0


def test_no_projects_means_no_results() -> None:
    assert aggregate_results([]) == {}


def test_project_specific_rules_do_not_count_as_team_rules() -> None:
    """リポジトリ固有の規約に同じ ID があっても、チーム規約の成否には入れない。"""
    results = aggregate_results([[_p("A", "VIOLATED", origin="project")]])
    assert results == {}
