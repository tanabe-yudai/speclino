"""services: CLI・S20 worker が共有するオーケストレータ（PoC 14章「モジュール分割」の cli 層と
conformance_engine の間）。GitHub・DB・LLM に依存しない。

パッケージ本体では何も re-export しない（各モジュールから直接 import する）。ここで
check_pipeline を読むと、run_freshness のような純粋関数だけを使う側まで z3 を引き込み、
依存を絞ったサンドボックス用イメージでテストを収集できなくなる（S60）。
"""
