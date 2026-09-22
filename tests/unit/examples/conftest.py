"""`examples` パッケージ（リポジトリ直下）を import 可能にする。

`examples/` は `src/speclino` とは別の src レイアウト外パッケージなので、editable install
（`src/` だけが sys.path に載る）だけでは `from examples.orders...` が解決できない。このテスト
ディレクトリにだけ repo root を追加する。

このディレクトリ自体には `__init__.py` を置かない: `examples/__init__.py`（本物のパッケージ）
と名前が衝突しないようにするため（pytest はここを非パッケージとして扱い、ディレクトリ自体を
sys.path へ積む）。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
