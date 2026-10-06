"""利用者入力に対する上限（grammar.md 12 章）。計算量と再帰の深さを有界にするため 1 か所にまとめる。

仕様の本文はエディタのプレビュー API から来る利用者入力なので、どの処理も入力の大きさに対して
線形で、再帰の深さはここの定数で抑える。超えたら場所つきのエラー診断にする（例外にしない）。
"""

from __future__ import annotations

from speclino.domain.knowledge import BODY_MAX_BYTES

MAX_BODY_BYTES = BODY_MAX_BYTES
"""本文の上限（256KB）。超えたら parse しない（E_TOO_LARGE）。"""
MAX_LINE_CHARS = 4096
"""1 行の文字数の上限（E_LINE_TOO_LONG）。"""
MAX_TOKENS = 100_000
"""文書全体のトークン数の上限（E_TOO_LARGE）。"""
MAX_DIAGRAMS = 64
"""図（mermaid ブロック）の数の上限（E_TOO_LARGE）。"""
MAX_STATEMENTS_PER_DIAGRAM = 2000
"""図ごとの文（行）の数の上限。要素・メッセージ・辺の数はこれ以下になる（E_TOO_LARGE）。"""
MAX_AUTOMATON_SIZE = 10_000
"""オートマトンの状態数・遷移数それぞれの上限（E_TOO_LARGE）。構成は Thompson 型で要素数に線形。"""
MAX_CONTAINER_DEPTH = 16
"""Markdown の引用・リストの入れ子の上限（E_NESTING_TOO_DEEP）。

CommonMark のパーサーは深すぎる入れ子の
内容を黙って捨てるので、先に数えて止める。"""
MAX_PIPES_PER_LINE = 64
"""1 行の `|` の数の上限（表の列の数を抑える。E_TOO_LARGE）。"""
MAX_PIPE_LINES = 5000
"""`|` を含む行の数の上限（表の行の数を抑える。表の解析の時間を有界にする。E_TOO_LARGE）。"""
MAX_TOTAL_PIPES = 20_000
"""文書全体の `|` の数の上限（表のセルの総数を抑える。E_TOO_LARGE）。"""
MAX_NESTING = 32
"""alt / opt / loop の入れ子の上限（E_NESTING_TOO_DEEP）。"""
MAX_EXPR_DEPTH = 64
"""ガードの式の木の深さの上限（括弧・not・演算子の連なりを含む。E_NESTING_TOO_DEEP）。"""

__all__ = [
    "MAX_AUTOMATON_SIZE",
    "MAX_BODY_BYTES",
    "MAX_CONTAINER_DEPTH",
    "MAX_DIAGRAMS",
    "MAX_EXPR_DEPTH",
    "MAX_LINE_CHARS",
    "MAX_NESTING",
    "MAX_PIPES_PER_LINE",
    "MAX_PIPE_LINES",
    "MAX_TOTAL_PIPES",
    "MAX_STATEMENTS_PER_DIAGRAM",
    "MAX_TOKENS",
]
