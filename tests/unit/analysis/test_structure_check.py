"""構造の義務（クラス図）の検査（AST のみ、import しない）。"""

from __future__ import annotations

from pathlib import Path

from speclino.analysis.structure_check import check_structure
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
