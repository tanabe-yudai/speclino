"""Identity Platform の ID トークン検証（S13）: 発行者と aud を確認する。"""

from __future__ import annotations

import pytest

from speclino.adapters.auth.identity_platform import ISSUER_PREFIX, _check_claims, verify_bearer
from speclino.domain.workspace import AuthUser
from speclino.settings import Settings

PROJECT = "example-project"


def claims(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "iss": f"{ISSUER_PREFIX}{PROJECT}",
        "aud": PROJECT,
        "email": "judge@example.com",
        "email_verified": True,
        "user_id": "uid-1",
        "sub": "uid-1",
    }
    base.update(overrides)
    return base


def test_the_identifier_is_the_uid_and_the_email_is_only_for_display() -> None:
    assert _check_claims(claims(), PROJECT) == AuthUser(
        uid="uid-1", email="judge@example.com", email_verified=True
    )


def test_an_unverified_email_is_marked_as_such() -> None:
    user = _check_claims(claims(email_verified=False), PROJECT)
    assert user.uid == "uid-1"
    assert user.email_verified is False


@pytest.mark.parametrize(
    "bad",
    [
        {"iss": "https://accounts.google.com"},  # 別の Google 発行者
        {"iss": f"{ISSUER_PREFIX}other-project"},  # 別プロジェクト
        {"iss": None},
    ],
)
def test_wrong_issuer_is_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="発行者が不正"):
        _check_claims(claims(**bad), PROJECT)


def test_wrong_audience_is_rejected() -> None:
    with pytest.raises(ValueError, match="aud"):
        _check_claims(claims(aud="other"), PROJECT)


def test_a_token_without_sub_is_rejected_even_if_it_has_an_email() -> None:
    with pytest.raises(ValueError, match="sub"):
        _check_claims(claims(sub=None), PROJECT)


async def test_missing_project_id_is_an_error() -> None:
    with pytest.raises(ValueError, match="firebase_project_id"):
        await verify_bearer("t", Settings(env="prod", auth_kind="identity_platform"))
