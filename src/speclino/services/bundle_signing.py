"""知識の bundle・lock の署名（S62、design/2026-10-05-team-knowledge-registry.md 3.2・7.1）。

HMAC-SHA256。鍵は `SPECLINO_BUNDLE_SIGNING_KEY`（本番は Secret Manager）。未設定のとき、
env=local（テスト・手元）は固定の開発鍵を使い、env=prod は `RuntimeError`（`build_state` が
起動時に呼ぶので、鍵の無い本番は立ち上がらない）。鍵は起動時に注入した設定の値を使う
（`configure`）。開発鍵の署名は本番の鍵では検証できない。

鍵の更新（#114）: 署名には鍵の ID（`key_id`、鍵から決まる 16 桁の 16 進）を添える。署名は今の鍵で
行い、検証は `key_id` の一致する鍵で行う。更新の間は前の鍵を `SPECLINO_BUNDLE_SIGNING_KEYS_OLD`
（`kid:secret` をカンマ区切り）に残すと、前の鍵の bundle・lock も検証できる。知らない `key_id` は
検証しない（False。呼び出し側で UNKNOWN / 拒否になる）。`key_id` の無い古い署名は、鍵束のどれかで
合えば受け付ける（どれも信頼する鍵）。手順は docs/runbooks/knowledge-registry-cutover.md。
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field

from speclino.settings import Settings

DEV_SIGNING_KEY = b"speclino-dev-bundle-signing-key"
_PREFIX = "hmac-sha256:"


def key_id_of(secret: bytes) -> str:
    """鍵の ID（鍵から決まる。鍵そのものは推測できない）。"""
    return hashlib.sha256(b"speclino-bundle-kid:" + secret).hexdigest()[:16]


@dataclass(frozen=True)
class Keyring:
    """署名に使う今の鍵と、検証だけに使う前の鍵（key_id → 鍵）。"""

    current: bytes
    old: dict[str, bytes] = field(default_factory=dict)

    @property
    def current_id(self) -> str:
        return key_id_of(self.current)

    def by_id(self, kid: str) -> bytes | None:
        if kid == self.current_id:
            return self.current
        return self.old.get(kid)

    def all_keys(self) -> list[bytes]:
        return [self.current, *self.old.values()]


def signing_key(settings: Settings) -> bytes:
    """settings の署名鍵。本番（env=prod）で未設定なら RuntimeError。"""
    if settings.bundle_signing_key:
        return settings.bundle_signing_key.encode()
    if settings.env == "prod":
        raise RuntimeError("bundle の署名鍵（SPECLINO_BUNDLE_SIGNING_KEY）が無い")
    return DEV_SIGNING_KEY


def parse_old_keys(value: str | None) -> dict[str, bytes]:
    """`kid:secret,kid:secret`。kid が secret から決まる ID と違えば ValueError（取り違え）。"""
    old: dict[str, bytes] = {}
    for raw in (value or "").split(","):
        entry = raw.strip()
        if not entry:
            continue
        kid, sep, secret = entry.partition(":")
        if not sep or not kid or not secret:
            raise ValueError("SPECLINO_BUNDLE_SIGNING_KEYS_OLD は kid:secret をカンマ区切りで書く")
        if key_id_of(secret.encode()) != kid:
            raise ValueError(
                f"SPECLINO_BUNDLE_SIGNING_KEYS_OLD の kid {kid} が鍵と合わない"
                "（`speclino signing-key-id` で前の鍵の ID を確かめる）"
            )
        old[kid] = secret.encode()
    return old


def keyring(settings: Settings) -> Keyring:
    current = signing_key(settings)
    old = parse_old_keys(settings.bundle_signing_keys_old)
    old.pop(key_id_of(current), None)
    return Keyring(current=current, old=old)


_ring: Keyring | None = None


def configure(settings: Settings | None) -> None:
    """起動時（`build_state`）に注入された設定の鍵を使う。None で未設定に戻す（テスト用）。"""
    global _ring
    _ring = keyring(settings) if settings is not None else None


def _current_ring() -> Keyring:
    # 注入されていない（テストの AppState 直組み・CLI）ときは、環境変数から一度だけ読む
    if _ring is None:
        configure(Settings())
    assert _ring is not None
    return _ring


def current_key_id() -> str:
    """今の署名鍵の ID（bundle・lock の `key_id` に入れる）。"""
    return _current_ring().current_id


def _mac(key: bytes, payload: bytes) -> str:
    return _PREFIX + hmac.new(key, payload, hashlib.sha256).hexdigest()


def sign(payload: bytes) -> str:
    return _mac(_current_ring().current, payload)


def verify(payload: bytes, signature: str, key_id: str | None = None) -> bool:
    """署名が合うか（定数時間の比較）。非 ASCII を含む署名も例外にせず False。

    `key_id` があればその鍵だけで確かめ、知らない ID なら False（フェイルクローズ）。無ければ
    （鍵の ID を持たない古い署名）鍵束のどれかで合えば True。
    """
    given = signature.encode("utf-8", "surrogatepass")
    ring = _current_ring()
    if key_id is not None:
        key = ring.by_id(key_id)
        return key is not None and hmac.compare_digest(_mac(key, payload).encode(), given)
    ok = False
    for key in ring.all_keys():
        ok |= hmac.compare_digest(_mac(key, payload).encode(), given)
    return ok


__all__ = [
    "DEV_SIGNING_KEY",
    "Keyring",
    "configure",
    "current_key_id",
    "key_id_of",
    "keyring",
    "parse_old_keys",
    "sign",
    "signing_key",
    "verify",
]
