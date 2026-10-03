"""S47: `projects.settings` の型と、保存値の読み方・オンボーディングの段階の導出。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from speclino.domain.project_settings import ProjectSettings
from speclino.services.project_settings import derive_onboarding, parse_stored


def test_defaults_keep_current_behavior() -> None:
    s = ProjectSettings()
    assert s.fail_on == ["VIOLATED", "UNKNOWN", "REVIEW"]
    assert s.agent_enabled is True and s.test_run_enabled is True
    assert s.approvers == []


@pytest.mark.parametrize(
    "fail_on",
    [["VIOLATED"], ["VIOLATED", "REVIEW"], [], ["VIOLATED", "UNKNOWN", "REVIEW", "REVIEW"]],
)
def test_fail_on_cannot_be_weakened(fail_on: list[str]) -> None:
    """フェイルクローズ: 違反・未検証・要確認のどれもゲートの失敗から外せない。"""
    with pytest.raises(ValidationError):
        ProjectSettings.model_validate({"fail_on": fail_on})


def test_fail_on_rejects_verified() -> None:
    with pytest.raises(ValidationError):
        ProjectSettings.model_validate(
            {"fail_on": ["VIOLATED", "UNKNOWN", "REVIEW", "VERIFIED_IN_SCOPE"]}
        )


def test_unknown_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ProjectSettings.model_validate({"agent_enabled": True, "approve_all": True})


@pytest.mark.parametrize("login", ["", "-bad", "a" * 40, "has space", "dependabot[bot]", "a/b"])
def test_approver_must_be_github_login(login: str) -> None:
    with pytest.raises(ValidationError):
        ProjectSettings.model_validate({"approvers": [login]})


def test_approvers_must_be_unique() -> None:
    with pytest.raises(ValidationError):
        ProjectSettings.model_validate({"approvers": ["octo", "octo"]})


def test_valid_approvers() -> None:
    s = ProjectSettings.model_validate({"approvers": ["octo-cat", "A1"]})
    assert s.approvers == ["octo-cat", "A1"]


def test_parse_stored_reads_partial_settings() -> None:
    """管理 CLI が approvers だけを書いた行も読める（他は既定値）。"""
    parsed = parse_stored({"approvers": ["octo"]})
    assert parsed.settings.approvers == ["octo"]
    assert parsed.settings.agent_enabled is True and parsed.invalid is False


def test_parse_stored_invalid_falls_back_to_locked_down() -> None:
    """壊れた保存値は既定値に化けさせない。証拠を増やす機能だけを止めた安全側にする。"""
    parsed = parse_stored({"fail_on": ["VIOLATED"], "agent_enabled": True})
    assert parsed.invalid is True
    assert parsed.settings.fail_on == ["VIOLATED", "UNKNOWN", "REVIEW"]
    assert parsed.settings.agent_enabled is False and parsed.settings.test_run_enabled is False
    assert parsed.settings.approvers == []


def test_parse_stored_unknown_key_is_invalid() -> None:
    assert parse_stored({"mystery": 1}).invalid is True


def _states(steps: list) -> dict[str, str]:  # type: ignore[type-arg]
    return {s.id: s.state for s in steps}


def test_onboarding_nothing_yet() -> None:
    steps = derive_onboarding(
        installation_id=None, has_completed_run=False, has_any_run=False, latest_failure=None
    )
    assert [s.id for s in steps] == ["app", "speclino", "first_run"]
    assert _states(steps) == {"app": "unknown", "speclino": "unknown", "first_run": "todo"}


def test_onboarding_installed_but_speclino_missing() -> None:
    steps = derive_onboarding(
        installation_id=42,
        has_completed_run=False,
        has_any_run=True,
        latest_failure="入力不正: domain.yaml が無い: /tmp/x/head/.speclino/domain.yaml",
    )
    assert _states(steps) == {"app": "done", "speclino": "todo", "first_run": "todo"}
    speclino = next(s for s in steps if s.id == "speclino")
    # 作業ディレクトリの一時パスは出さない
    assert "/tmp" not in speclino.detail and ".speclino/domain.yaml" in speclino.detail


def test_onboarding_missing_spec_ir_is_also_a_setup_step() -> None:
    steps = derive_onboarding(
        installation_id=42,
        has_completed_run=False,
        has_any_run=True,
        latest_failure="入力不正: Spec IR が無い: /tmp/x/specs/ir/*.spec-ir.json",
    )
    assert _states(steps)["speclino"] == "todo"


def test_onboarding_other_failure_does_not_claim_missing_speclino() -> None:
    steps = derive_onboarding(
        installation_id=42,
        has_completed_run=False,
        has_any_run=True,
        latest_failure="実行障害: RuntimeError: boom",
    )
    assert _states(steps) == {"app": "done", "speclino": "unknown", "first_run": "todo"}


def test_onboarding_done_after_completed_run() -> None:
    steps = derive_onboarding(
        installation_id=42, has_completed_run=True, has_any_run=True, latest_failure=None
    )
    assert _states(steps) == {"app": "done", "speclino": "done", "first_run": "done"}
