"""LLM に送る前の秘密情報・個人情報の検出と、モデルの引用の伏せ字（#114）。

本物に見えるトークンをリポジトリに置かないよう、検査に使う値は実行時に組み立てる。
"""

from __future__ import annotations

import time

import pytest

from speclino.services.secret_scan import MASK, find_secrets, mask_secrets, secret_kinds

GITHUB = "gh" + "p_" + "A1b2C3d4E5" * 4
AWS = "AK" + "IA" + "ABCDEFGHIJ234567"
GCP = "AI" + "za" + "Sy" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"
SLACK = "xo" + "xb-" + "1234567890-abcdefghij"
OPENAI = "s" + "k-" + "proj-" + "a1B2c3D4e5F6g7H8i9J0k1L2"
JWT = (
    "ey"
    + "JhbGciOiJIUzI1NiJ9"
    + ".ey"
    + "JzdWIiOiIxMjM0NTY3ODkwIn0"
    + ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (f'TOKEN = "{GITHUB}"', "GitHub のトークン"),
        (f"key = '{AWS}'", "AWS のアクセスキー"),
        (f'k = "{GCP}"', "GCP の API キー"),
        (f'slack = "{SLACK}"', "Slack のトークン"),
        (f'openai = "{OPENAI}"', "API キー（sk-）"),
        (f'auth = "Bearer {JWT}"', "JWT"),
        (f'pem = """{PEM}\nMIIE"""', "秘密鍵"),
        ('url = "postgres://admin:hunter2@db.internal:5432/app"', "認証情報つきの URL"),
        ('owner = "taro.yamada@acme-corp.co.jp"', "メールアドレス"),
        ('{"type": "service_account", "private_key_id": "abc"}', "GCP のサービスアカウント鍵"),
    ],
)
def test_detects_secrets_and_pii(text: str, kind: str) -> None:
    assert kind in secret_kinds(text)


def test_example_domains_and_plain_code_are_not_flagged() -> None:
    code = (
        "def notify(order_id: str) -> None:\n"
        '    send("noreply@example.com", f"注文 {order_id} を受け付けた")\n'
        '    log("user@service.test")\n'
        '    url = "https://api.example.com/v1/orders"\n'
    )
    assert secret_kinds(code, python=True) == []


def test_high_entropy_string_literal_is_flagged_but_identifiers_are_not() -> None:
    secret = "q8Zr2LmX9vB4nT7kP1sD6fH3jW5yC0aE"  # 32 文字・英数字混在
    assert "高いエントロピーの文字列" in secret_kinds(f'API_KEY = "{secret}"', python=True)
    # 長い識別子（文字列リテラルではない）は対象外
    ident = "def handle_order_cancellation_request_with_retry_policy_v2(): pass"
    assert secret_kinds(ident, python=True) == []
    # 繰り返しの多い（エントロピーの低い）文字列も対象外
    assert secret_kinds('PAD = "' + "0a" * 32 + '"', python=True) == []


def test_mask_replaces_only_the_detected_ranges() -> None:
    quote = f'raise Error("token {GITHUB} は無効")'
    masked = mask_secrets(quote)
    assert GITHUB not in masked and MASK in masked
    assert masked.startswith('raise Error("token ') and masked.endswith(' は無効")')
    assert mask_secrets("注文が無い") == "注文が無い"


@pytest.mark.parametrize(
    "adversarial",
    [
        "a@" * 3000,
        "-" * 6000,
        "a." * 3000 + "@",
        "x://" + ":" * 6000,
        "eyJ" + "a" * 3000 + ".eyJ" + "b" * 2990,
        "A" * 6000,
    ],
)
def test_scanning_is_fast_on_adversarial_input(adversarial: str) -> None:
    started = time.perf_counter()
    find_secrets(adversarial)
    mask_secrets(adversarial)
    assert time.perf_counter() - started < 2.0
