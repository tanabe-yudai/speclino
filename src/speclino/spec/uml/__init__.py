"""UML（Mermaid の厳格な部分集合）と旧形式の Spec IR を CompiledSpec へ決定的に変換する。

文法の正本は同じディレクトリの `grammar.md`。LLM・adapters に依存しない。
"""

from speclino.spec.uml.compile import PARSER_VERSION, compile_spec, ir_hash_of, semantic_dump
from speclino.spec.uml.render import render

__all__ = ["PARSER_VERSION", "compile_spec", "ir_hash_of", "render", "semantic_dump"]
