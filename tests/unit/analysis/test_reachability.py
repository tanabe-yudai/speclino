"""analysis.reachability: 到達関数群のソースハッシュ（S41c）の単体試験。

一時ディレクトリに小さな Python パッケージを書き、静的な直接呼出しの推移閉包が正しく
たどれること、コメント・空白の差でハッシュが変わらないこと、関数本体の変更や解決できない
呼出し式の変更でハッシュが変わることを確認する。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from speclino.analysis.reachability import reachable_source_hash
from speclino.domain.bindings import BehaviorBinding, Bindings


def _bindings(entrypoint: str) -> Bindings:
    return Bindings(behaviors={"b1": BehaviorBinding(entrypoint=entrypoint)})


def _write(root: Path, relpath: str, source: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")


def test_reachable_set_is_transitive_closure_of_direct_calls(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/mod.py",
        """
        def entry(x: int) -> int:
            return helper1(x)


        def helper1(x: int) -> int:
            return helper2(x) + 1


        def helper2(x: int) -> int:
            return x * 2
        """,
    )
    digest = reachable_source_hash(tmp_path, "b1", _bindings("pkg.mod.entry"), source_roots=(".",))
    assert digest.functions == ["pkg.mod.entry", "pkg.mod.helper1", "pkg.mod.helper2"]
    assert digest.unresolved_calls == []
    assert digest.source_hash.startswith("sha256:")


def test_hash_unchanged_by_comments_and_whitespace(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _write(
        a,
        "pkg/mod.py",
        """
        def entry(x: int) -> int:
            return helper1(x)


        def helper1(x: int) -> int:
            return x + 1
        """,
    )
    _write(
        b,
        "pkg/mod.py",
        """
        # このモジュールについてのコメント（docstring ではない。docstring は AST に残るので
        # ハッシュに影響してよく、ここでは対象外にする）


        def entry(x: int) -> int:
            # ここでヘルパーを呼ぶ
            return helper1(x)



        def helper1(x: int) -> int:

            return x + 1  # 1 を足す
        """,
    )
    bindings = _bindings("pkg.mod.entry")
    digest_a = reachable_source_hash(a, "b1", bindings, source_roots=(".",))
    digest_b = reachable_source_hash(b, "b1", bindings, source_roots=(".",))
    assert digest_a.source_hash == digest_b.source_hash
    assert digest_a.functions == digest_b.functions


def test_hash_changes_when_function_body_changes(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _write(
        a,
        "pkg/mod.py",
        """
        def entry(x: int) -> int:
            return helper1(x)


        def helper1(x: int) -> int:
            return x * 2
        """,
    )
    _write(
        b,
        "pkg/mod.py",
        """
        def entry(x: int) -> int:
            return helper1(x)


        def helper1(x: int) -> int:
            return x * 3
        """,
    )
    bindings = _bindings("pkg.mod.entry")
    digest_a = reachable_source_hash(a, "b1", bindings, source_roots=(".",))
    digest_b = reachable_source_hash(b, "b1", bindings, source_roots=(".",))
    assert digest_a.source_hash != digest_b.source_hash
    assert digest_a.functions == digest_b.functions  # 到達集合自体は同じ


def test_hash_changes_when_only_an_unrelated_import_changes(tmp_path: Path) -> None:
    """関数本体の文字列は同一でも、その関数外の環境（import 束縛）が変わればハッシュも変わる。

    S41c の最初の実装は関数本体だけをハッシュにしており、この差を見落とした（実例:
    `from fastapi import Response` を `from builtins import print as Response` に変えても
    `Response(status_code=204)` という呼出し文自体のテキストは1文字も変わらない。呼出し先は
    どちらも repo 内で解決できない外部シンボルなので、旧実装では unresolved_calls の文字列
    「Response(x)」も両者で同一になり、ハッシュが一致してしまっていた）。
    モジュール全体（import 行を含む）をハッシュに含めることで防ぐ。
    """
    a, b = tmp_path / "a", tmp_path / "b"
    _write(
        a,
        "pkg/mod.py",
        """
        from pkg.external_a import Response


        def entry(x: int) -> int:
            return Response(x)
        """,
    )
    _write(
        b,
        "pkg/mod.py",
        """
        from pkg.external_b import Response


        def entry(x: int) -> int:
            return Response(x)
        """,
    )
    bindings = _bindings("pkg.mod.entry")
    digest_a = reachable_source_hash(a, "b1", bindings, source_roots=(".",))
    digest_b = reachable_source_hash(b, "b1", bindings, source_roots=(".",))
    # entry 自体の呼出し文は同一（呼出し先の解決だけが import 経由で変わる）ことを確認する
    assert digest_a.unresolved_calls == digest_b.unresolved_calls == ["Response(x)"]
    assert digest_a.functions == digest_b.functions == ["pkg.mod.entry"]
    assert digest_a.source_hash != digest_b.source_hash


def test_hash_changes_when_an_imported_but_not_called_module_changes(tmp_path: Path) -> None:
    """呼出しグラフには現れない（属性参照・型としてだけ使う）import 先が変わってもハッシュが
    変わる。

    `examples/orders/app/models.py` の `OrderStatus.PENDING` を `WAITING` に改名する実例
    （呼び出す関数は1つも変わらないのに `RepoIndex.enum_values()` 経由の属性参照が解決
    できなくなる）と同種の穴の最小再現。
    """
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        _write(
            root,
            "pkg/mod.py",
            """
            from pkg.values import LIMIT


            def entry(x: int) -> int:
                return x + LIMIT
            """,
        )
    _write(a, "pkg/values.py", "LIMIT = 1\n")
    _write(b, "pkg/values.py", "LIMIT = 2\n")

    bindings = _bindings("pkg.mod.entry")
    digest_a = reachable_source_hash(a, "b1", bindings, source_roots=(".",))
    digest_b = reachable_source_hash(b, "b1", bindings, source_roots=(".",))
    # entry のソース・到達関数集合は同一（LIMIT の値だけが違う）
    assert digest_a.functions == digest_b.functions == ["pkg.mod.entry"]
    assert "pkg.values" in digest_a.modules
    assert digest_a.source_hash != digest_b.source_hash


def test_relative_import_and_init_reexport_are_followed(tmp_path: Path) -> None:
    """相対 import（`from .sub import X`）と `__init__.py` 経由の再エクスポートも推移的に
    たどり、直接 import されていない末端モジュールの変更もハッシュに反映する。"""
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        _write(root, "pkg/__init__.py", "")
        _write(
            root,
            "pkg/mod.py",
            """
            from .sub import THING  # 相対 import。sub/__init__.py 経由の再エクスポート


            def entry(x: int) -> int:
                return x
            """,
        )
        _write(root, "pkg/sub/__init__.py", "from .inner import THING\n")
    _write(a, "pkg/sub/inner.py", "THING = 1\n")
    _write(b, "pkg/sub/inner.py", "THING = 2\n")  # mod.py も sub/__init__.py も変えていない

    bindings = _bindings("pkg.mod.entry")
    digest_a = reachable_source_hash(a, "b1", bindings, source_roots=(".",))
    digest_b = reachable_source_hash(b, "b1", bindings, source_roots=(".",))
    assert {"pkg.sub", "pkg.sub.inner"} <= set(digest_a.modules)
    assert digest_a.source_hash != digest_b.source_hash


def test_unresolved_dynamic_call_text_is_recorded_and_changes_hash(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _write(
        a,
        "pkg/mod.py",
        """
        class Entry:
            def run(self, x: int) -> int:
                return self.registry.execute(x)
        """,
    )
    _write(
        b,
        "pkg/mod.py",
        """
        class Entry:
            def run(self, x: int) -> int:
                return self.registry.execute(x + 1)
        """,
    )
    bindings = _bindings("pkg.mod.Entry.run")
    digest_a = reachable_source_hash(a, "b1", bindings, source_roots=(".",))
    digest_b = reachable_source_hash(b, "b1", bindings, source_roots=(".",))
    assert digest_a.unresolved_calls == ["self.registry.execute(x)"]
    assert digest_b.unresolved_calls == ["self.registry.execute(x + 1)"]
    assert digest_a.source_hash != digest_b.source_hash


def test_mutual_recursion_does_not_loop_forever(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/mod.py",
        """
        def entry(x: int) -> int:
            return a(x)


        def a(x: int) -> int:
            if x <= 0:
                return 0
            return b(x - 1)


        def b(x: int) -> int:
            return a(x)
        """,
    )
    digest = reachable_source_hash(tmp_path, "b1", _bindings("pkg.mod.entry"), source_roots=(".",))
    assert digest.functions == ["pkg.mod.a", "pkg.mod.b", "pkg.mod.entry"]


def test_unknown_entrypoint_is_reported_not_raised(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/mod.py", "def other() -> None:\n    pass\n")
    digest = reachable_source_hash(
        tmp_path, "b1", _bindings("pkg.mod.missing"), source_roots=(".",)
    )
    assert digest.functions == []
    assert digest.unresolved_calls == ["<entrypoint を解決できない: pkg.mod.missing>"]


def test_attribute_writes_finds_dynamic_writes(tmp_path: Path) -> None:
    from speclino.analysis.reachability import attribute_writes

    src = tmp_path / "src" / "app"
    src.mkdir(parents=True)
    (src / "a.py").write_text("def f(r):\n    r.status = 'x'\n")
    (src / "b.py").write_text("def g(r):\n    object.__setattr__(r, 'status', 1)\n")
    (src / "c.py").write_text("def h(r):\n    r.__dict__['status'] = 1\n")
    (src / "d.py").write_text("def k(r, n):\n    setattr(r, n, 1)\n")
    (src / "e.py").write_text("def m(r):\n    r.other = 1\n")
    found = attribute_writes(tmp_path, "status", ("src",))
    assert [w.split(":")[0] for w in found] == [
        "src/app/a.py",
        "src/app/b.py",
        "src/app/c.py",
        "src/app/d.py",
    ]
    assert "名前が定数でない" in found[3]
