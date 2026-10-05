"""LLM に送る前の秘密情報・個人情報の検出と、モデルの引用の伏せ字（#114）。

本物に見えるトークンをリポジトリに置かないよう、検査に使う値は実行時に組み立てる。
"""

from __future__ import annotations

import time

import pytest

from speclino.services.secret_scan import (
    MASK,
    NAME_KIND,
    VALUE_KIND,
    find_secrets,
    mask_secrets,
    secret_kinds,
)

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


def test_value_like_string_literal_is_flagged_but_identifiers_are_not() -> None:
    """値による検出: 20 文字以上・空白なし・英字と数字が混在する Base64 / 16 進 / URL セーフ。"""
    for value in (
        "q8Zr2LmX9vB4nT7kP1sD",  # 20 文字の英数字
        "3f9a1c0e7b2d4a6f8e1c3b5d",  # 16 進
        "dGhpcyBpcyBhIHNlY3JldDEy==",  # Base64
        "a1-b2_c3-d4_e5-f6_g7-h8",  # URL セーフ
    ):
        assert VALUE_KIND in secret_kinds(f'X = "{value}"', python=True), value
        assert VALUE_KIND in secret_kinds(f"send({value!r})", python=True), value
    # 長い識別子（文字列リテラルではない）・空白を含む文・繰り返しの多い文字列・短い値は対象外
    ident = "def handle_order_cancellation_request_with_retry_policy_v2(): pass"
    assert secret_kinds(ident, python=True) == []
    assert (
        secret_kinds('m = "注文 12345 が見つからない。もう一度お試しください"', python=True) == []
    )
    assert secret_kinds('PAD = "' + "0a" * 32 + '"', python=True) == []
    assert secret_kinds('code = "ORD-2024"', python=True) == []


@pytest.mark.parametrize(
    "code",
    [
        'password = "hunter2"',
        'self.api_key = "abc"',
        'cfg["client_secret"] = "x"',
        'connect(host="db", passwd="x")',
        'headers = {"Authorization": "Bearer abc"}',
        'def login(user, pwd="admin"): pass',
        'def f(*, session_cookie="c"): pass',
        'if token == "letmein": pass',
        'SIGNING_KEY: str = "k"',
        'AccessKey = b"raw"',
        'token = os.getenv("API_TOKEN", "fallback")',
    ],
)
def test_sensitive_names_bound_to_literals_are_flagged(code: str) -> None:
    """名前による検出: 値の形に関係なく、秘密らしい名前に結びつくリテラルは送らない。"""
    assert NAME_KIND in secret_kinds(code, python=True)


@pytest.mark.parametrize(
    "code",
    [
        'token = os.environ["API_TOKEN"]',
        'password = os.getenv("DB_PASSWORD")',
        'api_key = os.environ.get("OPENAI_API_KEY")',
        "client = Client(api_key=settings.api_key)",
        "session = make_session(engine)",
        'token = ""',
    ],
)
def test_values_read_from_env_or_settings_are_sent(code: str) -> None:
    """環境変数・設定から読む式（リテラルを含まない）は送ってよい。変数名のリテラルも同じ。"""
    assert secret_kinds(code, python=True) == []


def test_env_default_literal_is_checked_by_value() -> None:
    value = "q8Zr2LmX9vB4nT7kP1sD6fH3"
    assert VALUE_KIND in secret_kinds(f'url = os.getenv("DB_URL", "{value}")', python=True)
    assert secret_kinds('region = os.getenv("REGION", "asia-northeast1")', python=True) == []


def test_ordinary_code_is_sent() -> None:
    code = (
        "@router.post('/orders/{order_id}/cancel')\n"
        "async def cancel_order(order_id: str, reason: str | None = None) -> dict[str, str]:\n"
        '    """注文を取り消す。"""\n'
        "    order = await repo.get(order_id)\n"
        "    if order is None:\n"
        "        raise OrderError(f'注文 {order_id} が無い。番号を確かめてください。')\n"
        "    await inventory.restore(order.items)\n"
        "    return {'status': 'cancelled', 'order_id': order_id}\n"
    )
    assert secret_kinds(code, python=True) == []


def test_text_that_is_not_python_uses_the_textual_name_check() -> None:
    assert NAME_KIND in secret_kinds("host=db user=app password=hunter2")
    assert NAME_KIND in secret_kinds('"api_key": "abc"')
    assert secret_kinds("注文が無い。番号を確かめてください。") == []


def test_mask_replaces_only_the_detected_ranges() -> None:
    quote = f'raise Error("token {GITHUB} は無効")'
    masked = mask_secrets(quote)
    assert GITHUB not in masked and MASK in masked
    assert masked.startswith('raise Error("token ') and masked.endswith(' は無効")')
    assert mask_secrets("注文が無い") == "注文が無い"
    # 名前による検出も引用に効く（名前は残し、値だけを伏せる）
    assert mask_secrets('password = "hunter2"') == f'password = "{MASK}"'
    assert "hunter2" not in mask_secrets("db_password=hunter2 host=db")


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


# ---------------------------------------------------------------------------
# 検出の対象と送る文字列のずれ（#114 detector differential）
# ---------------------------------------------------------------------------

# sk- の後ろ。数字を含まないので、それだけでは値による検出に当たらない（組み立ての検出を確かめる）
TAIL = "AbCdEfGhIjKlMnOpQrStUvWx"

EVASIONS = [
    f'KEY = "sk-" "{TAIL}"',  # 暗黙の連結
    'KEY = "\\x73\\x6b-' + TAIL + '"',  # エスケープ
    f'KEY = f"sk-{{prefix}}{TAIL}"',  # f-string の定数部分
    f'KEY = b"sk-{TAIL}"',  # bytes
    f'KEY = "sk-" + "{TAIL}"',  # + の連結
    f'msg = "%s-%s" % ("sk", "{TAIL}")',  # %-format の定数
    f'msg = "{{}}-{{}}".format("sk", "{TAIL}")',  # .format の定数
    'cfg = {"db": {"settings": {"password": "x"}}}',  # ネストした dict のキーの名前
]


@pytest.mark.parametrize("source", EVASIONS)
def test_evasions_are_detected_on_the_normalized_text(source: str) -> None:
    """単位の本文は ast.unparse の正規形（LLM に送るそのもの）。それと AST の定数の値で検出する。"""
    import ast

    normalized = ast.unparse(ast.parse(source))
    assert secret_kinds(normalized, python=True), normalized


def test_tail_alone_is_not_detected() -> None:
    assert secret_kinds(f'X = "{TAIL}"', python=True) == []


def test_prompt_gate_catches_secrets_outside_the_unit_body() -> None:
    from speclino.services.secret_scan import prompt_secret_kinds

    assert prompt_secret_kinds(f"評価の単位（function）: app.py::sk-{TAIL}\n```\npass\n```")
    assert prompt_secret_kinds('```\npassword = "hunter2"\n```')
    ordinary = "規約: エラーは次の行動を示す\n```\ndef f(order_id):\n    token = make_token()\n```"
    assert prompt_secret_kinds(ordinary) == []


# ---------------------------------------------------------------------------
# fix round 2: 組み立ての上限（資源の使い切り）と、除外のすり抜け
# ---------------------------------------------------------------------------

UNANALYZABLE_PROBES = [
    'x = "%99999999s" % ("a",)',
    'x = "%2000000000s" % ("a",)',
    'x = "%.99999999s" % ("a",)',
    'x = "%*s" % (99999999, "a")',
    'x = "{0:>99999999}".format("a")',
    'x = "{0:{1}}".format("a", "99999999")',
    'x = f"{y:>99999999}"',
    'x = "ab" * 10**9',
    'x = 10**9 * "ab"',
    'x = "ab" * 1000000',
    'x = ("ab" * 40000) + ("cd" * 40000)',
    'x = "-".join(["' + "a" * 1000 + '"] * 1)' + " + " + '"x" * 70000',
]


@pytest.mark.parametrize("source", UNANALYZABLE_PROBES)
def test_oversized_assembly_is_unanalyzable_quickly(source: str) -> None:
    import tracemalloc

    from speclino.services.secret_scan import UNANALYZABLE_KIND

    tracemalloc.start()
    started = time.perf_counter()
    kinds = secret_kinds(source, python=True)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert UNANALYZABLE_KIND in kinds, source
    assert elapsed < 1.0 and peak < 8 * 1024 * 1024, (elapsed, peak)


def test_deeply_nested_source_is_unanalyzable() -> None:
    """パーサが深さで失敗する本文（RecursionError / MemoryError）は送らない。"""
    from speclino.services.secret_scan import UNANALYZABLE_KIND

    for deep in ("x = " + "a." * 100000 + "b", "x = " + "-" * 100000 + "1"):
        assert UNANALYZABLE_KIND in secret_kinds(deep, python=True)


def test_deep_concatenation_is_scanned_without_recursion() -> None:
    chain = f'x = "sk-" + "{TAIL}" + ' + "+".join(['" "'] * 3000)
    assert secret_kinds(chain, python=True)


def test_long_concatenation_chain_is_linear() -> None:
    chain = "x = " + " + ".join(['"ab"'] * 900)
    started = time.perf_counter()
    secret_kinds(chain, python=True)
    assert time.perf_counter() - started < 1.0


def test_small_formats_are_still_assembled() -> None:
    assert VALUE_KIND in secret_kinds(f'x = "%5s%s" % ("sk-", "{TAIL}1")', python=True)
    assert secret_kinds(f'x = "-".join(["sk", "{TAIL}"])', python=True)
    assert secret_kinds('x = "%-10s|" % ("注文",)', python=True) == []


SECRET = "q8Zr2LmX9vB4nT7kP1sD"


@pytest.mark.parametrize(
    "value",
    [
        f"/tmp/{SECRET}",
        f"https://x/{SECRET}",
        f"https://api.example.com/v1/{SECRET}",
        "q8Zr2LmX-9vB4-nT7k-P1sD-6fH3jW5yC0aE",  # UUID の形に埋め込む
        f"sha256:{SECRET}",
        f"{SECRET}@example.com",  # 例示用ドメインのローカル部に埋め込む
        "aB3_xY7_pQ9_zZ1_kK2_mN4",  # 識別子の形に似せる
        "ab3_cd7_ef9_gh1_ij2_kl4",
    ],
)
def test_lookalikes_of_allowed_forms_are_not_sent(value: str) -> None:
    assert secret_kinds(f'x = "{value}"', python=True), value
    assert secret_kinds(value), value


def test_identifier_exclusion_applies_only_to_code_identifiers() -> None:
    assert (
        secret_kinds(
            "def handle_order_cancellation_request_with_retry_policy_v2(): pass", python=True
        )
        == []
    )
    # 同じ形でも文字列の値は除外しない
    assert secret_kinds('x = "handle_order_v2_cancel_retry_v3"', python=True)


def test_name_detection_wins_over_allowed_forms() -> None:
    for value in ("550e8400-e29b-41d4-a716-446655440000", "noreply@example.com", "/tmp/x"):
        assert NAME_KIND in secret_kinds(f'password = "{value}"', python=True), value
    # 環境変数の名前は完全一致の形のときだけ除外する
    assert NAME_KIND in secret_kinds('token = os.environ["sk-not-an-env-name"]', python=True)
    assert secret_kinds('token = os.environ["API_TOKEN"]', python=True) == []


def test_example_email_exclusion_is_exact() -> None:
    assert secret_kinds('x = "noreply@example.com"', python=True) == []
    assert secret_kinds('x = "user@service.test"', python=True) == []
    assert "メールアドレス" in secret_kinds('x = "taro@mail.example.com.evil.jp"')
    assert "メールアドレス" in secret_kinds('x = "Taro.Yamada@corp.example.co.jp"')


@pytest.mark.parametrize(
    "source",
    [
        'setattr(obj, "password", "hunter2")',
        'setattr(cfg, "api_key", "abc")',
        f'x = "".join(["sk-", "{TAIL}"])',
        f'x = "".join(("sk", "-", "{TAIL}"))',
    ],
)
def test_setattr_and_join_are_detected(source: str) -> None:
    assert secret_kinds(source, python=True), source
