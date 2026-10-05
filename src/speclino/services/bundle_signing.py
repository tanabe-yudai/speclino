"""知識の bundle・lock の署名（S62、design/2026-10-05-team-knowledge-registry.md 3.2・7.1）。

HMAC-SHA256。鍵は `SPECLINO_BUNDLE_SIGNING_KEY`（本番は Secret Manager）。未設定のとき、
env=local（テスト・手元）は固定の開発鍵を使い、env=prod は `RuntimeError`（`build_state` が
起動時に呼ぶので、鍵の無い本番は立ち上がらない）。鍵は起動時に注入した設定の値を使う
（`configure`）。開発鍵の署名は本番の鍵では検証できない。
"""

from __future__ import annotations

import hashlib
import hmac

from speclino.settings import Settings

DEV_SIGNING_KEY = b"speclino-dev-bundle-signing-key"
_PREFIX = "hmac-sha256:"


_key: bytes | None = None


def signing_key(settings: Settings) -> bytes:
    """settings の署名鍵。本番（env=prod）で未設定なら RuntimeError。"""
    if settings.bundle_signing_key:
        return settings.bundle_signing_key.encode()
    if settings.env == "prod":
        raise RuntimeError("bundle の署名鍵（SPECLINO_BUNDLE_SIGNING_KEY）が無い")
    return DEV_SIGNING_KEY


def configure(settings: Settings | None) -> None:
    """起動時（`build_state`）に注入された設定の鍵を使う。None で未設定に戻す（テスト用）。"""
    global _key
    _key = signing_key(settings) if settings is not None else None


def _current_key() -> bytes:
    # 注入されていない（テストの AppState 直組み・CLI）ときは、環境変数から一度だけ読む
    if _key is None:
        configure(Settings())
    assert _key is not None
    return _key


def sign(payload: bytes) -> str:
    return _PREFIX + hmac.new(_current_key(), payload, hashlib.sha256).hexdigest()


def verify(payload: bytes, signature: str) -> bool:
    """署名が合うか。非 ASCII を含む署名も例外にせず False（bytes で比較する）。"""
    given = signature.encode("utf-8", "surrogatepass")
    return hmac.compare_digest(sign(payload).encode(), given)


__all__ = ["DEV_SIGNING_KEY", "configure", "sign", "signing_key", "verify"]
