"""表示名の検証と uid の表示（DB を使わない部分）。"""

from __future__ import annotations

import pytest

from speclino.services.profiles import (
    InvalidDisplayName,
    normalize_display_name,
    profile_label,
    short_uid,
)


def test_display_name_is_stripped_and_kept_up_to_50_characters() -> None:
    assert normalize_display_name("  田辺 ") == "田辺"
    assert normalize_display_name("あ" * 50) == "あ" * 50


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "あ" * 51,
        "boss@example.com",
        "a@b",
        "a\nb",
        "a\tb",
        "a\x00b",
        "evil\u202etxt",
        "a\u2028b",
    ],
)
def test_invalid_display_names_are_rejected(bad: str) -> None:
    with pytest.raises(InvalidDisplayName):
        normalize_display_name(bad)


def test_label_prefers_display_name_then_email_then_short_uid() -> None:
    uid = "Xy12abcdEFGHijklmnop"
    assert profile_label(uid, "Tanabe", "t@example.com", True) == "Tanabe（t@example.com）"
    assert profile_label(uid, "Tanabe", "t@example.com", False) == "Tanabe（t@example.com・未確認）"
    assert profile_label(uid, "Tanabe", None, False) == "Tanabe（Xy12abcd…）"
    assert profile_label(uid, None, "t@example.com", True) == "t@example.com"
    assert profile_label(uid, None, "t@example.com", False) == "t@example.com（未確認）"
    assert profile_label(uid, None, None, False) == "Xy12abcd…"


def test_two_people_with_the_same_display_name_are_distinguishable() -> None:
    a = profile_label("uid-aaaaaaaaaa", "Tanabe", "real@example.com", True)
    b = profile_label("uid-bbbbbbbbbb", "Tanabe", None, False)
    c = profile_label("uid-cccccccccc", "Tanabe", "real@example.com", False)
    assert len({a, b, c}) == 3
    assert short_uid("dev") == "dev"


@pytest.mark.parametrize(
    "raw",
    [
        "bob＠example.com",
        "Alice（alice@example.com）",
        "Alice (x)",
        "Alice【admin】",
        "a﹫b",
        "⑴ x",
        "a〔b〕",
    ],
)
def test_display_name_rejects_email_and_suffix_lookalikes(raw: str) -> None:
    """全角の ＠ や括弧で、表示の添え書き「表示名（メール）」を偽装できない。"""
    with pytest.raises(InvalidDisplayName):
        normalize_display_name(raw)
