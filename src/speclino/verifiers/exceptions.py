"""規約の例外（PolicyException）の読み込みと有効性判定（AC-009）。

registry での適用は S30 で実装する。ここでは「有効な例外だけを残す」純関数と、
壊れた例外エントリ（必須項目欠落）を読み込み時点で除外するローダーだけを提供する。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import ValidationError

from speclino.domain.common import SCHEMA_VERSION
from speclino.domain.policy import PolicyException, PolicyExceptions


def load_policy_exceptions(raw: dict[str, Any]) -> PolicyExceptions:
    """`.speclino/exceptions.yaml` を読み込んだ辞書から PolicyExceptions を作る。

    個々の例外エントリが必須項目を欠いている場合、そのエントリだけを無効として除外する
    （ファイル全体の読み込みを失敗させない）。
    """
    valid: list[PolicyException] = []
    for item in raw.get("exceptions", []):
        try:
            valid.append(PolicyException.model_validate(item))
        except ValidationError:
            continue
    schema_version = raw.get("schema_version", SCHEMA_VERSION)
    return PolicyExceptions(schema_version=schema_version, exceptions=valid)


def effective_exceptions(exceptions: PolicyExceptions, today: date) -> list[PolicyException]:
    """期限切れでない例外だけを返す（純関数）。必須項目欠落は読み込み時点で既に除外済み。"""
    return [exc for exc in exceptions.exceptions if exc.expires_on >= today]
