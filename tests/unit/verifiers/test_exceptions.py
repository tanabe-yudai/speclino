"""PolicyException の読み込みと有効性判定（AC-009: 期限切れ・必須項目欠落は無効）。"""

from __future__ import annotations

from datetime import date

from speclino.verifiers.exceptions import effective_exceptions, load_policy_exceptions

VALID_ITEM = {
    "id": "EXC-001",
    "rule": "MAKE-001",
    "target": {"paths": ["Makefile"]},
    "reason": "移行期間中の一時的な例外",
    "approver": "bob",
    "expires_on": "2099-01-01",
    "alternative": "移行完了後に対応する",
}


def test_load_skips_entries_missing_required_fields() -> None:
    missing_alternative = {k: v for k, v in VALID_ITEM.items() if k != "alternative"}
    raw = {"schema_version": "0.1", "exceptions": [VALID_ITEM, missing_alternative]}

    loaded = load_policy_exceptions(raw)

    assert len(loaded.exceptions) == 1
    assert loaded.exceptions[0].id == "EXC-001"


def test_effective_exceptions_excludes_expired() -> None:
    expired = {**VALID_ITEM, "id": "EXC-EXPIRED", "expires_on": "2020-01-01"}
    raw = {"schema_version": "0.1", "exceptions": [VALID_ITEM, expired]}
    loaded = load_policy_exceptions(raw)

    result = effective_exceptions(loaded, today=date(2026, 9, 22))

    assert [exc.id for exc in result] == ["EXC-001"]


def test_effective_exceptions_keeps_exact_expiry_date() -> None:
    raw = {"schema_version": "0.1", "exceptions": [VALID_ITEM]}
    loaded = load_policy_exceptions(raw)

    result = effective_exceptions(loaded, today=date(2099, 1, 1))

    assert len(result) == 1
