"""知識の bundle・lock の署名（S62、design/2026-10-05-team-knowledge-registry.md 3.2・7.1）。

HMAC-SHA256。鍵は `SPECLINO_BUNDLE_SIGNING_KEY`（本番は Secret Manager）。未設定のとき、
env=local（テスト・手元）は固定の開発鍵を使い、env=prod は `RuntimeError`（`build_state` が
起動時に呼ぶので、鍵の無い本番は立ち上がらない）。開発鍵の署名は本番の鍵では検証できない。
"""

from __future__ import annotations

import hashlib
import hmac

from speclino.settings import Settings

DEV_SIGNING_KEY = b"speclino-dev-bundle-signing-key"
_PREFIX = "hmac-sha256:"


def signing_key(settings: Settings | None = None) -> bytes:
    """署名鍵。settings を省くと環境変数から読む（署名のたびに読む: 鍵の差し替えに追従する）。"""
    s = settings if settings is not None else Settings()
    if s.bundle_signing_key:
        return s.bundle_signing_key.encode()
    if s.env == "prod":
        raise RuntimeError("bundle の署名鍵（SPECLINO_BUNDLE_SIGNING_KEY）が無い")
    return DEV_SIGNING_KEY


def sign(payload: bytes) -> str:
    return _PREFIX + hmac.new(signing_key(), payload, hashlib.sha256).hexdigest()


def verify(payload: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign(payload), signature)


__all__ = ["DEV_SIGNING_KEY", "sign", "signing_key", "verify"]
