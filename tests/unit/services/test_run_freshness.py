"""should_publish: 古い run の結果を公開しない（specs/SPEC-RUN-001.md, R-RUN-01〜03）。"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from speclino.domain.run import ExecState
from speclino.services.run_freshness import Freshness, should_publish

RUN_ID = uuid4()
OTHER_RUN_ID = uuid4()

# superseded だけが特別扱いされることを示すための代表値。
NON_SUPERSEDED_STATES: tuple[ExecState, ...] = ("queued", "completed", "failed")


def check(exec_state: ExecState, latest_run_id: UUID | None) -> Freshness:
    return should_publish(RUN_ID, exec_state, latest_run_id)


@pytest.mark.spec("R-RUN-01")
@pytest.mark.parametrize("latest_run_id", [RUN_ID, OTHER_RUN_ID, None])
def test_superseded_is_never_published(latest_run_id: UUID | None) -> None:
    """superseded は is_latest に関わらず公開しない（自分自身が最新でも superseded を優先する）。"""
    result = check("superseded", latest_run_id)
    assert result.decision == "superseded"
    assert result.publish is False


@pytest.mark.spec("R-RUN-02")
@pytest.mark.parametrize("exec_state", NON_SUPERSEDED_STATES)
def test_not_latest_run_is_not_published(exec_state: ExecState) -> None:
    """superseded 以外でも、同じ PR の最新 run でなければ公開しない（完了順の逆転）。"""
    result = check(exec_state, OTHER_RUN_ID)
    assert result.decision == "not_latest"
    assert result.publish is False
    assert str(OTHER_RUN_ID) in result.reason


@pytest.mark.spec("R-RUN-03")
@pytest.mark.parametrize("exec_state", NON_SUPERSEDED_STATES)
def test_latest_run_is_published(exec_state: ExecState) -> None:
    """superseded 以外で、latest_run_id が自分自身なら公開する。"""
    result = check(exec_state, RUN_ID)
    assert result.decision == "publish"
    assert result.publish is True


@pytest.mark.spec("R-RUN-03")
@pytest.mark.parametrize("exec_state", NON_SUPERSEDED_STATES)
def test_unknown_latest_run_is_treated_as_latest(exec_state: ExecState) -> None:
    """latest_run_id is None（最新が分からない）ときは _is_latest の docstring 通り最新とみなす。"""
    result = check(exec_state, None)
    assert result.decision == "publish"
    assert result.publish is True


def test_publish_property_is_true_only_for_publish_decision() -> None:
    """Freshness.publish は decision == "publish" のときだけ真になる。"""
    assert should_publish(RUN_ID, "superseded", RUN_ID).publish is False
    assert should_publish(RUN_ID, "completed", OTHER_RUN_ID).publish is False
    assert should_publish(RUN_ID, "completed", RUN_ID).publish is True


@pytest.mark.parametrize(
    ("exec_state", "latest_run_id"),
    [
        ("superseded", RUN_ID),
        ("completed", OTHER_RUN_ID),
        ("completed", RUN_ID),
        ("completed", None),
    ],
)
def test_reason_is_always_non_empty(exec_state: ExecState, latest_run_id: UUID | None) -> None:
    """reason は UI と Check Run に表示されるため、どの分岐でも空にならない。"""
    assert check(exec_state, latest_run_id).reason
