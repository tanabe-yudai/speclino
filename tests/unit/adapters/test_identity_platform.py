"""Identity Platform の ID トークン検証（S13）: 発行者と aud を確認する。"""

from __future__ import annotations

import pytest

from speclino.adapters.auth.identity_platform import ISSUER_PREFIX, _check_claims, verify_bearer
from speclino.settings import Settings

PROJECT = "example-project"


def claims(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "iss": f"{ISSUER_PREFIX}{PROJECT}",
        "aud": PROJECT,
        "email": "judge@example.com",
        "user_id": "uid-1",
    }
    base.update(overrides)
    return base


def test_valid_claims_return_the_email() -> None:
    assert _check_claims(claims(), PROJECT) == "judge@example.com"


def test_falls_back_to_uid_when_no_email() -> None:
    assert _check_claims(claims(email=None), PROJECT) == "uid-1"
    assert _check_claims(claims(email=None, user_id=None, sub="sub-1"), PROJECT) == "sub-1"


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


def test_unidentifiable_token_is_rejected() -> None:
    with pytest.raises(ValueError, match="識別"):
        _check_claims(claims(email=None, user_id=None), PROJECT)


async def test_missing_project_id_is_an_error() -> None:
    with pytest.raises(ValueError, match="firebase_project_id"):
        await verify_bearer("t", Settings(env="prod", auth_kind="identity_platform"))
