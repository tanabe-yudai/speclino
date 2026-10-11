"""構造の義務（クラス図）の検査（AST のみ、import しない）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from speclino.analysis.structure_check import (
    check_structure,
    locate_class,
    locate_module,
    module_qualname,
)
from speclino.domain.finding import Finding
from speclino.domain.uml import ClassOperation, StructureObligation

SERVICE = """
class Base:
    def audit(self, actor):
        ...

class OrderService(Base):
    def place(self, order, qty):
        ...

    @staticmethod
    def quote(order):
        ...
"""


def _ob(*ops: tuple[str, list[str]], cls: str = "OrderService") -> StructureObligation:
    return StructureObligation(
        id=f"cls-orders#{cls}",
        class_name=cls,
        operations=[ClassOperation(name=n, params=p) for n, p in ops],
        level="mandatory",
    )


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    return tmp_path


def _one(ob: StructureObligation, root: Path) -> Finding:
    fs = check_structure(ob, root, ("src",))
    assert len(fs) == 1 and fs[0].requirement_id == ob.id
    return fs[0]


def test_matching_class_and_methods_verified(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/app/orders.py": SERVICE})
    ob = _ob(("place", ["order", "qty: int"]), ("quote", ["order"]), ("audit", ["actor"]))
    f = _one(ob, root)
    assert f.verdict == "VERIFIED_IN_SCOPE", f.message
    assert f.behavior_id == "cls-orders"
    assert f.code_refs[0].path == "src/app/orders.py"


def test_missing_class_violated(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/app/orders.py": SERVICE})
    f = _one(_ob(cls="PaymentService"), root)
    assert (f.verdict, f.category) == ("VIOLATED", "missing_effect")


def test_missing_method_violated(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/app/orders.py": SERVICE})
    f = _one(_ob(("cancel", ["order"])), root)
    assert (f.verdict, f.category) == ("VIOLATED", "missing_effect")
    assert "OrderService.cancel" in f.message


def test_parameter_mismatch_violated(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/app/orders.py": SERVICE})
    f = _one(_ob(("place", ["order"])), root)
    assert (f.verdict, f.category) == ("VIOLATED", "argument_mismatch")
    f = _one(_ob(("place", ["qty", "order"])), root)
    assert f.verdict == "VIOLATED"


def test_parse_error_makes_missing_class_unknown(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/app/orders.py": SERVICE, "src/app/broken.py": "def (:\n"})
    f = _one(_ob(cls="PaymentService"), root)
    assert f.verdict == "UNKNOWN"
    assert "broken.py" in f.message


def test_dynamic_definitions_are_unknown(tmp_path: Path) -> None:
    dyn = SERVICE + "\nPaymentService = type('PaymentService', (), {})\n"
    root = _repo(tmp_path, {"src/app/orders.py": dyn})
    assert _one(_ob(cls="PaymentService"), root).verdict == "UNKNOWN"
    getattr_cls = "class Proxy:\n    def __getattr__(self, name):\n        ...\n"
    root = _repo(tmp_path, {"src/app/proxy.py": getattr_cls})
    assert _one(_ob(("place", ["order"]), cls="Proxy"), root).verdict == "UNKNOWN"
    external_base = "from lib import Remote\n\nclass Client(Remote):\n    pass\n"
    root = _repo(tmp_path, {"src/app/client.py": external_base})
    assert _one(_ob(("call", []), cls="Client"), root).verdict == "UNKNOWN"


def test_varargs_mismatch_is_unknown(tmp_path: Path) -> None:
    root = _repo(
        tmp_path, {"src/app/v.py": "class V:\n    def run(self, *args, **kw):\n        ...\n"}
    )
    assert _one(_ob(("run", ["order"]), cls="V"), root).verdict == "UNKNOWN"


def test_duplicate_class_is_unknown_and_tests_ignored(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {
            "src/app/orders.py": SERVICE,
            "src/tests/test_orders.py": "class OrderService:\n    pass\n",
        },
    )
    assert _one(_ob(("place", ["order", "qty"])), root).verdict == "VERIFIED_IN_SCOPE"
    root = _repo(tmp_path, {"src/legacy/orders.py": "class OrderService:\n    pass\n"})
    assert _one(_ob(("place", ["order", "qty"])), root).verdict == "UNKNOWN"


def test_does_not_import_target_code(tmp_path: Path) -> None:
    boom = "raise SystemExit('imported!')\n\nclass Boom:\n    def go(self):\n        ...\n"
    root = _repo(tmp_path, {"src/app/boom.py": boom})
    assert _one(_ob(("go", []), cls="Boom"), root).verdict == "VERIFIED_IN_SCOPE"


# ---------------------------------------------------------------- レビュー fix round 1
def test_compound_statement_in_class_body_makes_missing_method_unknown(tmp_path: Path) -> None:
    body = (
        "import sys\n\nclass Svc:\n    if sys.version_info >= (3, 12):\n"
        "        def run(self, order):\n            ...\n"
        "    def stop(self):\n        ...\n"
    )
    root = _repo(tmp_path, {"src/app/svc.py": body})
    assert _one(_ob(("run", ["order"]), cls="Svc"), root).verdict == "UNKNOWN"
    # 直下に見つかるメソッドは今までどおり判定する
    assert _one(_ob(("stop", ["x"]), cls="Svc"), root).verdict == "VIOLATED"


def test_setattr_and_exec_make_missing_method_unknown(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {
            "src/app/a.py": "class A:\n    pass\n",
            "src/app/patch.py": "from app.a import A\n\nsetattr(A, 'run', lambda self: 1)\n",
            "src/app/b.py": "class B:\n    pass\n\nexec('B.run = lambda self: 1')\n",
        },
    )
    assert _one(_ob(("run", []), cls="A"), root).verdict == "UNKNOWN"
    assert _one(_ob(("run", []), cls="B"), root).verdict == "UNKNOWN"


def test_class_defined_only_inside_function_is_unknown(tmp_path: Path) -> None:
    body = (
        "def make():\n    class Local:\n        def run(self):\n            ...\n    return Local\n"
    )
    root = _repo(tmp_path, {"src/app/f.py": body})
    f = _one(_ob(("run", []), cls="Local"), root)
    assert f.verdict == "UNKNOWN"
    assert "直下でない" in f.message


# ---------------------------------------------------------------- レビュー fix round 2
def test_conditional_definition_elsewhere_makes_unknown(tmp_path: Path) -> None:
    # プローブ S5: 直下の A があっても、別の場所に条件付きの A があれば一意に決まらない
    root = _repo(
        tmp_path,
        {
            "src/app/a.py": "class A:\n    pass\n",
            "src/app/b.py": (
                "import sys\nif sys.version_info > (3,):\n"
                "    class A:\n        def run(self): ...\n"
            ),
        },
    )
    f = _one(_ob(("run", []), cls="A"), root)
    assert f.verdict == "UNKNOWN"
    assert "一意に決まらない" in f.message


def test_nested_class_with_same_name_makes_unknown(tmp_path: Path) -> None:
    # プローブ S2: 直下の A と入れ子の Holder.A
    body = "class A:\n    pass\n\nclass Holder:\n    class A:\n        def run(self): ...\n"
    root = _repo(tmp_path, {"src/app/a.py": body})
    assert _one(_ob(("run", []), cls="A"), root).verdict == "UNKNOWN"


def test_attribute_monkeypatch_makes_missing_method_unknown(tmp_path: Path) -> None:
    # プローブ S1: A.run = f
    root = _repo(
        tmp_path,
        {
            "src/app/a.py": "class A:\n    pass\n",
            "src/app/p.py": "from app.a import A\n\ndef f(self):\n    return 1\n\nA.run = f\n",
        },
    )
    f = _one(_ob(("run", []), cls="A"), root)
    assert f.verdict == "UNKNOWN"
    assert "A.run への代入" in f.message


def test_base_with_conditional_definition_is_unknown(tmp_path: Path) -> None:
    body = (
        "import sys\n\nclass Base:\n    pass\n\nif sys.version_info > (3,):\n"
        "    class Base:\n        def run(self): ...\n\nclass C(Base):\n    pass\n"
    )
    root = _repo(tmp_path, {"src/app/c.py": body})
    assert _one(_ob(("run", []), cls="C"), root).verdict == "UNKNOWN"


# ---------------------------------------------------------------- <<module>>

REGISTRY = """
def approve(revision_id, actor):
    ...

async def publish(bundle, *, force):
    ...

def helper(a):
    ...
"""


def _mod_check(
    root: Path,
    *ops: tuple[str, list[str]],
    name: str = "registry",
    modules: dict[str, str] | None = None,
) -> Finding:
    ob = _ob(*ops, cls=name)
    fs = check_structure(ob, root, ("src",), module=True, modules=modules)
    assert len(fs) == 1 and fs[0].requirement_id == ob.id
    return fs[0]


def test_module_matching_functions_verified(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/app/registry.py": REGISTRY})
    f = _mod_check(root, ("approve", ["revision_id", "actor"]), ("publish", ["bundle", "force"]))
    assert f.verdict == "VERIFIED_IN_SCOPE", f.message
    assert "モジュール registry と 2 個の関数" in f.message
    assert f.code_refs[0].path == "src/app/registry.py"


def test_module_argument_mismatch_violated(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/app/registry.py": REGISTRY})
    f = _mod_check(root, ("approve", ["revision_id"]))
    assert f.verdict == "VIOLATED" and f.category == "argument_mismatch"


def test_module_missing_function_violated(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/app/registry.py": REGISTRY})
    f = _mod_check(root, ("revoke", ["x"]))
    assert f.verdict == "VIOLATED" and f.category == "missing_effect"
    assert "registry.revoke が定義されていない" in f.message


def test_module_missing_module_violated(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/app/other.py": REGISTRY})
    f = _mod_check(root, ("approve", ["a"]))
    assert f.verdict == "VIOLATED" and "モジュール registry が source_roots に無い" in f.message


def test_module_varargs_unknown(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/app/registry.py": "def approve(*args):\n    ...\n"})
    assert _mod_check(root, ("approve", ["a"])).verdict == "UNKNOWN"


def test_module_ambiguous_name_unknown_and_bindings_resolve(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {"src/a/registry.py": "def approve(x):\n    ...\n", "src/b/registry.py": REGISTRY},
    )
    f = _mod_check(root, ("approve", ["x"]))
    assert f.verdict == "UNKNOWN"
    assert "src/a/registry.py" in f.message and "modules" in f.message
    ok = _mod_check(root, ("approve", ["x"]), modules={"registry": "a.registry"})
    assert ok.verdict == "VERIFIED_IN_SCOPE", ok.message


def test_module_bindings_missing_module_violated(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/app/registry.py": REGISTRY})
    f = _mod_check(root, ("approve", ["a"]), modules={"registry": "app.nowhere"})
    assert f.verdict == "VIOLATED" and "app.nowhere" in f.message


def test_module_bindings_package_init(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/app/server/__init__.py": "def serve(port):\n    ...\n"})
    f = _mod_check(root, ("serve", ["port"]), name="server", modules={"server": "app.server"})
    assert f.verdict == "VERIFIED_IN_SCOPE", f.message
    assert f.code_refs[0].path == "src/app/server/__init__.py"
    # 名前だけでもパッケージの __init__.py に解決する
    assert _mod_check(root, ("serve", ["port"]), name="server").verdict == "VERIFIED_IN_SCOPE"


def test_module_syntax_error_unknown(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/app/registry.py": "def approve(:\n"})
    assert _mod_check(root, ("approve", ["a"])).verdict == "UNKNOWN"


DYNAMIC = {
    "getattr": "def __getattr__(name):\n    ...\n",
    "import": "from other import approve\n",
    "import_as": "import other as approve\n",
    "assign": "approve = make()\n",
    "if": "import os\nif os.name:\n    def approve(a):\n        ...\n",
    "exec": "exec('def approve(a): pass')\n",
    "globals": "globals()['approve'] = 1\n",
    "global": "def f():\n    global approve\n    approve = 1\n",
}


@pytest.mark.parametrize("body", DYNAMIC.values(), ids=DYNAMIC.keys())
def test_module_dynamic_definition_unknown(tmp_path: Path, body: str) -> None:
    root = _repo(tmp_path, {"src/app/registry.py": body})
    f = _mod_check(root, ("approve", ["a"]))
    assert f.verdict == "UNKNOWN", f.message


def test_module_later_definition_wins(tmp_path: Path) -> None:
    root = _repo(
        tmp_path, {"src/app/registry.py": "def approve(a):\n    ...\n\ndef approve(b):\n    ...\n"}
    )
    assert _mod_check(root, ("approve", ["b"])).verdict == "VERIFIED_IN_SCOPE"


def test_module_qualname() -> None:
    roots = ("src", "src/speclino")
    assert module_qualname("src/speclino/services/x.py", ("src",)) == "speclino.services.x"
    assert module_qualname("src/speclino/services/x.py", roots) == "services.x"
    assert module_qualname("src/speclino/mcp/__init__.py", ("src",)) == "speclino.mcp"
    assert module_qualname("tools/x.py", ("src",)) is None
    assert module_qualname("x.py", (".",)) == "x"
    assert module_qualname("src/__init__.py", ("src",)) is None


def test_locate_module_and_class(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {
            "src/app/orders.py": SERVICE,
            "src/app/registry.py": REGISTRY,
            "src/app/dup1.py": "class Dup:\n    ...\n",
            "src/app/dup2.py": "class Dup:\n    ...\n",
            "src/app/hidden.py": "def f():\n    class Inner:\n        ...\n",
        },
    )
    m = locate_module("registry", root, ("src",))
    assert (m.qualname, m.path, m.problem, m.missing) == (
        "app.registry",
        "src/app/registry.py",
        None,
        False,
    )
    gone = locate_module("nothing", root, ("src",))
    assert gone.missing and gone.problem is not None and gone.qualname is None
    bound = locate_module("anything", root, ("src",), {"anything": "app.orders"})
    assert bound.qualname == "app.orders" and bound.path == "src/app/orders.py"
    c = locate_class("OrderService", root, ("src",))
    assert (c.qualname, c.path, c.missing) == (
        "app.orders.OrderService",
        "src/app/orders.py",
        False,
    )
    assert locate_class("Nope", root, ("src",)).missing
    dup = locate_class("Dup", root, ("src",))
    assert not dup.missing and dup.problem is not None and dup.qualname is None
    inner = locate_class("Inner", root, ("src",))
    assert not inner.missing and inner.problem is not None


def test_bindings_modules_validation() -> None:
    from pydantic import ValidationError

    from speclino.domain.bindings import Bindings

    assert Bindings().modules == {}
    assert Bindings(modules={"server": "speclino.mcp_server.server"}).modules["server"]
    for bad in ({"a.b": "x.y"}, {"1a": "x"}, {"a": "x..y"}, {"a": "x-y"}):
        with pytest.raises(ValidationError):
            Bindings(modules=bad)


def test_module_function_rebound_or_decorated_is_unknown(tmp_path: Path) -> None:
    from speclino.domain.uml import ClassOperation, StructureObligation

    pkg = tmp_path / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "reg.py").write_text(
        "import functools\n\n"
        "def approve(a):\n    pass\n\napprove = functools.partial(approve, 1)\n\n"
        "@functools.cache\ndef build(a):\n    pass\n"
    )
    for op, word in (("approve", "代入"), ("build", "デコレーター")):
        ob = StructureObligation(
            id=f"v#reg.{op}",
            class_name="reg",
            operations=[ClassOperation(name=op, params=["a"])],
            level="mandatory",
        )
        [f] = check_structure(ob, tmp_path, ("src",), module=True)
        assert f.verdict == "UNKNOWN", f.message
        assert word in f.message
