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
    ["", "   ", "あ" * 51, "a\nb", "a\tb", "a\x00b", "evil\u202etxt", "a\u2028b"],
)
def test_invalid_display_names_are_rejected(bad: str) -> None:
    with pytest.raises(InvalidDisplayName):
        normalize_display_name(bad)


def test_label_prefers_display_name_then_email_then_short_uid() -> None:
    uid = "Xy12abcdEFGHijklmnop"
    assert profile_label(uid, "Tanabe", "t@example.com") == "Tanabe"
    assert profile_label(uid, None, "t@example.com") == "t@example.com"
    assert profile_label(uid, None, None) == "Xy12abcd…"
    assert short_uid("dev") == "dev"
