"""途中までの JSON から、トップレベルの文字列キーの値を少しずつ取り出す（S74 ストリーム）。

LLM の応答は `{"explanation": "...", "proposal": {...}}` の形で、断片に分かれて届く。
説明の文字列だけを、閉じる前から文字単位で取り出して利用者に見せる。値の確定（検証）は
最後に全文を `json.loads` して行うので、ここで取り出した文字列は表示用にすぎない。
"""

from __future__ import annotations

from enum import Enum, auto

_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_KEY_MAX = 64
_REPLACEMENT = "�"
_HEX = frozenset("0123456789abcdefABCDEF")


class _Phase(Enum):
    SEEK = auto()  # 目的のキーを探している
    COLON = auto()
    VALUE = auto()
    IN_STRING = auto()
    DONE = auto()  # 値が閉じた、または形が想定と違う


class TopLevelStringStream:
    """`key` のトップレベルの文字列値を、feed した分だけ増えるテキストとして返す。"""

    def __init__(self, key: str) -> None:
        self._key = key
        self._phase = _Phase.SEEK
        self._depth = 0
        self._in_str = False
        self._esc = False
        self._cur: list[str] = []  # 深さ 1 の文字列（キー候補）
        self._expect_key = False
        self._uni: str | None = None  # \u の途中（集めた 16 進の桁）
        self._esc_pending = False  # 値の文字列の中で直前が \
        self._high: str | None = None  # 保留中の上位サロゲート

    @property
    def done(self) -> bool:
        return self._phase is _Phase.DONE

    def feed(self, chunk: str) -> str:
        """断片を読み、今回新しく取り出せた分のテキストを返す（キーが出る前は空）。"""
        out: list[str] = []
        for ch in chunk:
            if self._phase is _Phase.IN_STRING:
                self._value_char(ch, out)
            elif self._phase is _Phase.SEEK:
                self._seek_char(ch)
            elif self._phase is _Phase.COLON:
                if ch == ":":
                    self._phase = _Phase.VALUE
                elif not ch.isspace():
                    self._phase = _Phase.DONE
            elif self._phase is _Phase.VALUE:
                if ch == '"':
                    self._phase = _Phase.IN_STRING
                elif not ch.isspace():
                    self._phase = _Phase.DONE
            else:
                break
        return "".join(out)

    # --- キーの探索（文字列の中の `{` などに惑わされない）
    def _seek_char(self, ch: str) -> None:
        if self._in_str:
            if self._esc:
                self._esc = False
                self._cur.append(ch)
            elif ch == "\\":
                self._esc = True
            elif ch == '"':
                self._in_str = False
                is_key = self._depth == 1 and self._expect_key and "".join(self._cur) == self._key
                self._expect_key = False
                if is_key:
                    self._phase = _Phase.COLON
            elif self._depth == 1 and len(self._cur) < _KEY_MAX:
                self._cur.append(ch)
            return
        if ch == '"':
            self._in_str = True
            self._cur = []
        elif ch in "{[":
            self._depth += 1
            self._expect_key = ch == "{" and self._depth == 1
        elif ch in "}]":
            self._depth -= 1
        elif ch == ",":
            self._expect_key = self._depth == 1

    # --- 値の文字列のデコード
    def _value_char(self, ch: str, out: list[str]) -> None:
        if self._uni is not None:
            if ch not in _HEX:
                self._uni = None  # 壊れたエスケープ。ここで諦める
                self._phase = _Phase.DONE
                return
            self._uni += ch
            if len(self._uni) == 4:
                code = int(self._uni, 16)
                self._uni = None
                self._emit_code(code, out)
            return
        if self._esc_pending:
            self._esc_pending = False
            if ch == "u":
                self._uni = ""
            elif ch in _SIMPLE_ESCAPES:
                self._emit(_SIMPLE_ESCAPES[ch], out)
            else:
                self._phase = _Phase.DONE
            return
        if ch == "\\":
            self._esc_pending = True
        elif ch == '"':
            self._flush_high(out)
            self._phase = _Phase.DONE
        else:
            self._emit(ch, out)

    def _emit_code(self, code: int, out: list[str]) -> None:
        if 0xD800 <= code <= 0xDBFF:
            self._flush_high(out)
            self._high = chr(code)
        elif 0xDC00 <= code <= 0xDFFF:
            if self._high is not None:
                pair = (((ord(self._high) - 0xD800) << 10) | (code - 0xDC00)) + 0x10000
                self._high = None
                out.append(chr(pair))
            else:
                out.append(_REPLACEMENT)
        else:
            self._emit(chr(code), out)

    def _emit(self, text: str, out: list[str]) -> None:
        self._flush_high(out)
        out.append(text)

    def _flush_high(self, out: list[str]) -> None:
        if self._high is not None:  # 下位サロゲートの来ない上位サロゲート
            self._high = None
            out.append(_REPLACEMENT)


__all__ = ["TopLevelStringStream"]
