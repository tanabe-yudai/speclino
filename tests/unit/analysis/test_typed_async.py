"""宣言型による呼出し先の解決（static_types）と async / await の経路解析。"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from speclino.analysis.path_analyzer import analyze_repo
from speclino.analysis.symbolic_state import AmbiguousBinding
from speclino.domain import Bindings, CodeIR, DomainSpec

DOMAIN = DomainSpec.model_validate({"variables": {"req.ok": "boolean"}})

LIB = """
class Audit:
    def __init__(self, session: object) -> None:
        self._session = session

    def record(self, what: str) -> None:
        print(what)

    async def arecord(self, what: str) -> None:
        print(what)

    def count(self) -> int:
        return 0


class Noisy:
    def __init__(self, session: object) -> None:
        print(session)

    def record(self, what: str) -> None:
        print(what)


class Registry:
    def execute(self, x: int) -> int:
        return x


class Gateway:
    def __init__(self, registry: Registry, audit: Audit) -> None:
        self.registry = registry
        self.audit = audit

    def invoke(self, req) -> None:
        if req.ok:
            self.registry.execute(1)
        self.audit.record("done")


def make_audit(session: object) -> Audit:
    return Audit(session)
"""


def repo(tmp_path: Path, body: str) -> Path:
    pkg = tmp_path / "app"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "lib.py").write_text(textwrap.dedent(LIB))
    (pkg / "svc.py").write_text(textwrap.dedent(body))
    return tmp_path


def bindings(entry: str, ops: dict[str, str], variables: dict[str, str] | None = None) -> Bindings:
    return Bindings.model_validate(
        {
            "behaviors": {"b": {"entrypoint": entry, "variables": variables or {}}},
            "operations": {
                k: {"symbol": v, "claim_scope": "command_invocation"} for k, v in ops.items()
            },
        }
    )


def ops_of(code: CodeIR) -> list[list[str]]:
    return [[e.operation or "" for e in p.events if e.kind == "call"] for p in code.paths]


def causes(code: CodeIR) -> list[str]:
    return [u.detail for p in code.paths for u in p.unknown_causes] + [
        u.detail for u in code.unknown_causes
    ]


def test_self_attribute_typed_by_init_annotation(tmp_path: Path) -> None:
    root = repo(tmp_path, "")
    b = bindings(
        "app.lib.Gateway.invoke",
        {"exec": "app.lib.Registry.execute", "rec": "app.lib.Audit.record"},
        {"req.ok": "req.ok"},
    )
    code = analyze_repo(root, "b", DOMAIN, b)
    assert sorted(ops_of(code)) == [["exec", "rec"], ["rec"]]
    assert all(p.precision == "exact_in_model" for p in code.paths)


def test_chain_symbol_still_matches(tmp_path: Path) -> None:
    root = repo(tmp_path, "")
    b = bindings(
        "app.lib.Gateway.invoke",
        {"exec": "self.registry.execute", "rec": "self.audit.record"},
        {"req.ok": "req.ok"},
    )
    code = analyze_repo(root, "b", DOMAIN, b)
    assert sorted(ops_of(code)) == [["exec", "rec"], ["rec"]]


def test_overlapping_bindings_are_unknown(tmp_path: Path) -> None:
    root = repo(tmp_path, "")
    b = bindings(
        "app.lib.Gateway.invoke",
        {"a": "self.audit.record", "b2": "app.lib.Audit.record", "exec": "self.registry.execute"},
        {"req.ok": "req.ok"},
    )
    code = analyze_repo(root, "b", DOMAIN, b)
    assert all(p.precision == "incomplete" for p in code.paths)
    assert any("複数の操作" in c for c in causes(code))


def test_trivial_constructor_and_local_type(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        """
        from app.lib import Audit, make_audit

        def run(session):
            audit = Audit(session)
            audit.record("x")
            other = make_audit(session)
            other.record("y")
        """,
    )
    code = analyze_repo(root, "b", DOMAIN, bindings("app.svc.run", {"rec": "app.lib.Audit.record"}))
    assert ops_of(code) == [["rec", "rec"]]
    assert code.paths[0].precision == "exact_in_model"


def test_constructor_with_effects_is_unresolved(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        """
        from app.lib import Noisy

        def run(session):
            n = Noisy(session)
            n.record("x")
        """,
    )
    code = analyze_repo(root, "b", DOMAIN, bindings("app.svc.run", {"rec": "app.lib.Noisy.record"}))
    assert code.paths[0].precision == "incomplete"
    assert ops_of(code) == [["rec"]]  # 型は分かるので呼出しは記録する


def test_reassignment_drops_type(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        """
        from app.lib import Audit

        def run(session, other):
            audit = Audit(session)
            audit = other
            audit.record("x")
        """,
    )
    code = analyze_repo(root, "b", DOMAIN, bindings("app.svc.run", {"rec": "app.lib.Audit.record"}))
    assert ops_of(code) == [[]]
    assert code.paths[0].precision == "incomplete"


def test_async_entry_and_await(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        """
        from app.lib import Audit

        async def helper(audit: Audit) -> None:
            await audit.arecord("h")

        async def run(session, req):
            audit = Audit(session)
            if req.ok:
                await audit.arecord("x")
            await helper(audit)
            return await audit.arecord("z")
        """,
    )
    b = bindings("app.svc.run", {"rec": "app.lib.Audit.arecord"}, {"req.ok": "req.ok"})
    code = analyze_repo(root, "b", DOMAIN, b)
    assert sorted(ops_of(code)) == [["rec", "rec"], ["rec", "rec", "rec"]]
    assert all(p.precision == "exact_in_model" for p in code.paths)


def test_async_without_await_is_unresolved(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        """
        from app.lib import Audit

        async def helper() -> None:
            pass

        async def run(session):
            audit = Audit(session)
            audit.arecord("x")
            helper()
        """,
    )
    b = bindings("app.svc.run", {"rec": "app.lib.Audit.arecord"})
    code = analyze_repo(root, "b", DOMAIN, b)
    assert ops_of(code) == [[]]
    assert sum("await" in c for c in causes(code)) == 2


def test_await_of_sync_function_and_non_call(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        """
        def sync():
            pass

        async def run(task):
            await sync()
            await task
        """,
    )
    code = analyze_repo(root, "b", DOMAIN, bindings("app.svc.run", {}))
    assert code.paths[0].precision == "incomplete"
    assert any("await が対応しない" in c for c in causes(code))
    assert any("呼出し以外の await" in c for c in causes(code))


def test_keyword_only_entry_parameters(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        """
        from app.lib import Audit

        def run(session, *, req):
            if req.ok:
                Audit(session).record("x")
        """,
    )
    b = bindings("app.svc.run", {}, {"req.ok": "req.ok"})
    code = analyze_repo(root, "b", DOMAIN, b)
    assert len(code.paths) == 2  # キーワード専用の引数も入口の値として分岐できる


def test_bindings_outside_domain_are_ignored(tmp_path: Path) -> None:
    root = repo(tmp_path, "def run(req):\n    if req.ok:\n        pass\n")
    b = bindings("app.svc.run", {}, {"req.ok": "req.ok", "other.flag": "req.flag"})
    code = analyze_repo(root, "b", DOMAIN, b)
    assert len(code.paths) == 2


def test_two_domain_variables_on_one_implementation_fail(tmp_path: Path) -> None:
    root = repo(tmp_path, "def run(req):\n    pass\n")
    domain = DomainSpec.model_validate({"variables": {"req.ok": "boolean", "x.ok": "boolean"}})
    b = bindings("app.svc.run", {}, {"req.ok": "req.ok", "x.ok": "req.ok"})
    with pytest.raises(AmbiguousBinding):
        analyze_repo(root, "b", domain, b)


def test_opaque_region_with_awaited_operation(tmp_path: Path) -> None:
    # try の中の await した呼出しは、その呼出しのチャネルだけに触れる領域として読み飛ばせる
    root = repo(
        tmp_path,
        """
        from app.lib import Audit

        async def run(session):
            audit = Audit(session)
            try:
                await audit.arecord("x")
            except ValueError:
                pass
            audit.record("y")
        """,
    )
    b = bindings("app.svc.run", {"arec": "app.lib.Audit.arecord", "rec": "app.lib.Audit.record"})
    code = analyze_repo(root, "b", DOMAIN, b)
    assert ops_of(code) == [["rec"]]
    [cause] = code.paths[0].unknown_causes
    assert cause.may_affect == ["call:arec"]


def test_typed_method_is_not_inlined(tmp_path: Path) -> None:
    # 宣言型で解決したメソッドは self の束縛が無いので展開しない（引数がずれて偽の判定になる）
    root = repo(
        tmp_path,
        """
        from app.lib import Audit

        class Helper:
            def gate(self, a: bool, b: bool) -> None:
                if a:
                    Audit(None).record("x")

        def run(req, helper: Helper):
            helper.gate(req.ok, False)
        """,
    )
    b = bindings("app.svc.run", {"rec": "app.lib.Audit.record"}, {"req.ok": "req.ok"})
    code = analyze_repo(root, "b", DOMAIN, b)
    assert ops_of(code) == [[]]
    assert code.paths[0].precision == "incomplete"


def test_construction_with_call_in_arguments_is_not_trivial(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        """
        from app.lib import Audit

        def run(session, audit: Audit):
            return Audit(audit.record("x"))
        """,
    )
    code = analyze_repo(root, "b", DOMAIN, bindings("app.svc.run", {"rec": "app.lib.Audit.record"}))
    assert code.paths[0].precision == "incomplete"


def test_region_does_not_scan_typed_method_body(tmp_path: Path) -> None:
    # 宣言型で解決したメソッドを self なしで走査すると引数がずれるので、領域の効果を確定できない
    root = repo(
        tmp_path,
        """
        from app.lib import Audit

        class Helper:
            def gate(self, a: bool) -> None:
                pass

        def run(req, helper: Helper, audit: Audit):
            try:
                helper.gate(req.ok)
            except ValueError:
                pass
            audit.record("after")
        """,
    )
    b = bindings("app.svc.run", {"rec": "app.lib.Audit.record"}, {"req.ok": "req.ok"})
    code = analyze_repo(root, "b", DOMAIN, b)
    assert ops_of(code) == [[]]  # 領域で打ち切る（後ろの呼出しまで進まない）
    assert code.paths[0].outcome.kind == "unresolved"


def test_attribute_type_only_from_init(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        """
        from app.lib import Audit, Registry

        class Box:
            def __init__(self, audit: Audit) -> None:
                self.audit = audit

            def swap(self, r: Registry) -> None:
                self.audit = r

            def run(self) -> None:
                self.audit.record("x")
        """,
    )
    b = bindings("app.svc.Box.run", {"rec": "app.lib.Audit.record"})
    code = analyze_repo(root, "b", DOMAIN, b)
    assert ops_of(code) == [[]]  # 他のメソッドで書き換え得るので型なし
    assert code.paths[0].precision == "incomplete"


def test_construction_with_subscript_argument_is_not_trivial(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        """
        from app.lib import Audit

        def run(session):
            Audit(session[0])
        """,
    )
    code = analyze_repo(root, "b", DOMAIN, bindings("app.svc.run", {}))
    assert code.paths[0].precision == "incomplete"


def test_assert_unpacking_and_pure_functions(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        """
        import json
        from app.lib import Audit

        def pair(x):
            return x, x

        def run(session, req):
            assert req.ok, "must be ok"
            a, b = req.ok, req.ok
            c, d = pair(session)
            text = json.dumps(session, default=str)
            if len(text) > 0:
                pass
            Audit(session).record("x")
        """,
    )
    b = bindings("app.svc.run", {"rec": "app.lib.Audit.record"}, {"req.ok": "req.ok"})
    code = analyze_repo(root, "b", DOMAIN, b)
    kinds = sorted((p.outcome.kind, p.precision) for p in code.paths)
    # assert の偽の側は AssertionError で抜ける（完全な経路）。真の側は len(...) の比較で打ち切り
    assert ("exception", "exact_in_model") in kinds
    assert all(k != ("return", "exact_in_model") for k in kinds)


def test_pure_function_with_call_argument_is_unresolved(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        """
        import json
        from app.lib import Audit

        def run(session, audit: Audit):
            json.dumps(audit.record("x"))
        """,
    )
    code = analyze_repo(root, "b", DOMAIN, bindings("app.svc.run", {"rec": "app.lib.Audit.record"}))
    assert code.paths[0].precision == "incomplete"


def test_operation_inside_expression_is_recorded(tmp_path: Path) -> None:
    # 式の中の操作の呼出し（`cond and op()`、`x = op() + 1`、タプルの要素）は順序を追えない
    for body in (
        "    req.ok and audit.record('x')\n",
        "    n = audit.count() + 1\n",
        "    a, b = audit.count(), 1\n",
    ):
        root = repo(
            tmp_path / str(abs(hash(body))),
            "from app.lib import Audit\n\ndef run(req, audit: Audit):\n" + body,
        )
        b = bindings(
            "app.svc.run",
            {"rec": "app.lib.Audit.record", "cnt": "app.lib.Audit.count"},
            {"req.ok": "req.ok"},
        )
        code = analyze_repo(root, "b", DOMAIN, b)
        causes_ = [u for p in code.paths for u in p.unknown_causes]
        assert any(u.may_affect and u.may_affect[0].startswith("call:") for u in causes_), body


def test_unresolved_call_inside_expression_is_recorded(tmp_path: Path) -> None:
    root = repo(tmp_path, "def run(req, other):\n    x = other.thing() + 1\n")
    code = analyze_repo(root, "b", DOMAIN, bindings("app.svc.run", {}))
    assert code.paths[0].precision == "incomplete"


def test_json_keyword_and_rebinding_rules(tmp_path: Path) -> None:
    for body, pure in (
        ("import json\n\ndef run(x):\n    json.dumps(x, indent=2)\n", True),
        ("import json\n\ndef run(x):\n    json.dumps(x, default=str)\n", False),
        ("import json\njson = None\n\ndef run(x):\n    json.dumps(x)\n", False),
        ("import simplejson as json\n\ndef run(x):\n    json.dumps(x)\n", False),
    ):
        root = repo(tmp_path / str(abs(hash(body))), body)
        code = analyze_repo(root, "b", DOMAIN, bindings("app.svc.run", {}))
        assert (code.paths[0].precision == "exact_in_model") is pure, body


def test_starred_arguments_are_not_inlined(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        "def helper(a, b):\n    pass\n\ndef run(xs, kw):\n    helper(*xs)\n    helper(**kw)\n",
    )
    code = analyze_repo(root, "b", DOMAIN, bindings("app.svc.run", {}))
    assert sum("展開（* / **）" in c for c in causes(code)) == 2


def test_region_receiver_not_rooted_is_opaque(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        """
        from app.lib import Audit

        def run(session):
            audit = Audit(session)
            try:
                audit.record("x")
            except ValueError:
                pass
            audit.record("y")
        """,
    )
    code = analyze_repo(
        root,
        "b",
        DOMAIN,
        bindings("app.svc.run", {"rec": "app.lib.Audit.record"}),
        expand_operations=frozenset({"rec"}),
    )
    assert code.paths[0].outcome.kind == "unresolved"  # 受け手が入口の引数を根に持たない


def test_region_with_assert_touches_outcome(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        """
        def run(req):
            try:
                assert req.ok
            except ValueError:
                pass
        """,
    )
    code = analyze_repo(root, "b", DOMAIN, bindings("app.svc.run", {}, {"req.ok": "req.ok"}))
    assert code.paths[0].outcome.kind == "unresolved"  # 結果に触れる領域は読み飛ばさない
