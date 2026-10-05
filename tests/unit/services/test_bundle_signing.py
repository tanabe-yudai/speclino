"""bundle の署名（S62、services/bundle_signing.py）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from speclino.services import bundle_signing
from speclino.settings import Settings


@pytest.fixture(autouse=True)
def _reset_key() -> Iterator[None]:
    yield
    bundle_signing.configure(None)


def test_sign_and_verify_round_trip() -> None:
    bundle_signing.configure(Settings(bundle_signing_key="k1"))
    sig = bundle_signing.sign(b'{"a":1}')
    assert sig.startswith("hmac-sha256:")
    assert bundle_signing.verify(b'{"a":1}', sig)
    assert not bundle_signing.verify(b'{"a":2}', sig)
    assert not bundle_signing.verify(b'{"a":1}', "hmac-sha256:00")
    assert not bundle_signing.verify(b'{"a":1}', "garbage")


def test_non_ascii_signature_is_false_not_an_error() -> None:
    sig = bundle_signing.sign(b"x")
    assert not bundle_signing.verify(b"x", sig[:-1] + "é")
    assert not bundle_signing.verify(b"x", "\udcff")


def test_a_different_key_does_not_verify() -> None:
    bundle_signing.configure(Settings(bundle_signing_key="k1"))
    sig = bundle_signing.sign(b"x")
    bundle_signing.configure(Settings(bundle_signing_key="k2"))
    assert not bundle_signing.verify(b"x", sig)


def test_the_key_is_read_once_not_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """鍵は起動時に注入された設定の値（署名のたびに環境変数を読み直さない）。"""
    bundle_signing.configure(Settings(bundle_signing_key="k1"))
    sig = bundle_signing.sign(b"x")
    monkeypatch.setenv("SPECLINO_BUNDLE_SIGNING_KEY", "k2")
    assert bundle_signing.verify(b"x", sig)


def test_local_without_key_uses_the_dev_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPECLINO_BUNDLE_SIGNING_KEY", raising=False)
    assert bundle_signing.signing_key(Settings(env="local")) == bundle_signing.DEV_SIGNING_KEY
    assert bundle_signing.verify(b"x", bundle_signing.sign(b"x"))


def test_prod_without_key_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPECLINO_BUNDLE_SIGNING_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SPECLINO_BUNDLE_SIGNING_KEY"):
        bundle_signing.signing_key(Settings(env="prod"))
    assert bundle_signing.signing_key(Settings(env="prod", bundle_signing_key="s")) == b"s"


def test_prod_web_does_not_start_without_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from speclino.api.deps import build_state

    monkeypatch.delenv("SPECLINO_BUNDLE_SIGNING_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SPECLINO_BUNDLE_SIGNING_KEY"):
        build_state(Settings(env="prod", role="web", code_host_kind="github"))


def test_build_state_injects_the_key() -> None:
    from speclino.api.deps import build_state

    build_state(Settings(env="prod", role="web", code_host_kind="github", bundle_signing_key="z"))
    sig = bundle_signing.sign(b"x")
    bundle_signing.configure(Settings(bundle_signing_key="z"))
    assert bundle_signing.verify(b"x", sig)
    bundle_signing.configure(Settings(bundle_signing_key="other"))
    assert not bundle_signing.verify(b"x", sig)


# ---------------------------------------------------------------------------
# 鍵の更新（#114）
# ---------------------------------------------------------------------------


def _kid(secret: str) -> str:
    return bundle_signing.key_id_of(secret.encode())


def test_key_id_is_derived_from_the_key_and_does_not_contain_it() -> None:
    assert _kid("k1") == _kid("k1") != _kid("k2")
    assert len(_kid("k1")) == 16 and "k1" not in _kid("k1")
    bundle_signing.configure(Settings(bundle_signing_key="k1"))
    assert bundle_signing.current_key_id() == _kid("k1")


def test_old_key_verifies_with_its_kid_only_while_in_the_keyring() -> None:
    bundle_signing.configure(Settings(bundle_signing_key="k1"))
    sig = bundle_signing.sign(b"x")
    bundle_signing.configure(
        Settings(bundle_signing_key="k2", bundle_signing_keys_old=f"{_kid('k1')}:k1")
    )
    assert bundle_signing.verify(b"x", sig, _kid("k1"))
    # 違う kid では検証しない（鍵の取り違えを通さない）
    assert not bundle_signing.verify(b"x", sig, _kid("k2"))
    # 新しい署名は今の鍵
    assert bundle_signing.verify(b"x", bundle_signing.sign(b"x"), _kid("k2"))
    # 前の鍵を外すと検証できない
    bundle_signing.configure(Settings(bundle_signing_key="k2"))
    assert not bundle_signing.verify(b"x", sig, _kid("k1"))


def test_unknown_kid_fails_closed() -> None:
    bundle_signing.configure(Settings(bundle_signing_key="k1"))
    sig = bundle_signing.sign(b"x")
    assert not bundle_signing.verify(b"x", sig, "0" * 16)


def test_signature_without_kid_is_checked_against_the_keyring() -> None:
    """鍵の ID を持たない古い署名は、鍵束のどれかで合えば通す（どれも信頼する鍵）。"""
    bundle_signing.configure(Settings(bundle_signing_key="k1"))
    sig = bundle_signing.sign(b"x")
    bundle_signing.configure(
        Settings(bundle_signing_key="k2", bundle_signing_keys_old=f"{_kid('k1')}:k1")
    )
    assert bundle_signing.verify(b"x", sig)
    bundle_signing.configure(Settings(bundle_signing_key="k3"))
    assert not bundle_signing.verify(b"x", sig)


@pytest.mark.parametrize("value", ["k1", ":k1", "abc:", "0000000000000000:k1"])
def test_malformed_or_mismatched_old_keys_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="SPECLINO_BUNDLE_SIGNING_KEYS_OLD"):
        bundle_signing.configure(Settings(bundle_signing_key="k2", bundle_signing_keys_old=value))


def test_old_keys_accept_several_entries_and_ignore_blanks() -> None:
    value = f" {_kid('a')}:a , ,{_kid('b')}:b"
    ring = bundle_signing.keyring(Settings(bundle_signing_key="c", bundle_signing_keys_old=value))
    assert set(ring.old) == {_kid("a"), _kid("b")}
