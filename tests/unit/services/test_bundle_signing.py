"""bundle の署名（S62、services/bundle_signing.py）。"""

from __future__ import annotations

import pytest

from speclino.services import bundle_signing
from speclino.settings import Settings


def test_sign_and_verify_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPECLINO_BUNDLE_SIGNING_KEY", "k1")
    sig = bundle_signing.sign(b'{"a":1}')
    assert sig.startswith("hmac-sha256:")
    assert bundle_signing.verify(b'{"a":1}', sig)
    assert not bundle_signing.verify(b'{"a":2}', sig)
    assert not bundle_signing.verify(b'{"a":1}', "hmac-sha256:00")
    assert not bundle_signing.verify(b'{"a":1}', "garbage")


def test_a_different_key_does_not_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPECLINO_BUNDLE_SIGNING_KEY", "k1")
    sig = bundle_signing.sign(b"x")
    monkeypatch.setenv("SPECLINO_BUNDLE_SIGNING_KEY", "k2")
    assert not bundle_signing.verify(b"x", sig)


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
