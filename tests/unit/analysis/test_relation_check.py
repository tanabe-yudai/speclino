"""関係の義務（クラス図の関係線）の検査（AST のみ、import しない）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from speclino.analysis.relation_check import RelationChecker
from speclino.domain.finding import Finding
from speclino.domain.uml import RelationKind, RelationObligation, SourceSpan

DOMAIN = """
from dataclasses import dataclass
from enum import Enum


class Base:
    pass


class Kind(Enum):
    A = "a"


@dataclass
class Part:
    name: str
"""

SERVICE = """
from collections.abc import Callable, Sequence

from app.domain import Base, Part
from app import helpers


def make_part() -> Part:
    return Part("x")


def untyped():
    return None


class Holder(Base):
    def __init__(self, part: Part, parts: Sequence[Part] | None = None) -> None:
        self.part = part
        self.parts = parts


class Builder:
    def __init__(self) -> None:
        self.part = Part("p")


class Factory:
    def __init__(self) -> None:
        self.part = make_part()


class Optional_:
    maybe: "Part | None" = None


class Callback:
    def __init__(self, cb: Callable[[Part], None]) -> None:
        self.cb = cb


class Plain:
    def __init__(self) -> None:
        self.items = []


class Untyped:
    def __init__(self, x) -> None:
        self.x = x


class FromUntyped:
    def __init__(self) -> None:
        self.x = untyped()


class Dynamic:
    def __getattr__(self, name: str) -> object:
        return None


class Child(Holder):
    pass
"""

HELPERS = """
def help() -> None:
    pass
"""


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    return tmp_path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return _repo(
        tmp_path,
        {
            "src/app/__init__.py": "",
            "src/app/domain/__init__.py": "from .model import Base, Kind, Part\n",
            "src/app/domain/model.py": DOMAIN,
            "src/app/service.py": SERVICE,
            "src/app/helpers.py": HELPERS,
            "src/app/registry.py": "from app.domain import Part\n\ndefault: Part | None = None\n",
            "src/app/lonely.py": "VALUE = 1\n",
        },
    )


def _check(
    root: Path,
    kind: RelationKind,
    source: str,
    target: str,
    modules: frozenset[str] = frozenset({"service", "registry", "helpers", "lonely"}),
) -> Finding:
    ob = RelationObligation(
        id=f"v#{source}-{kind}-{target}",
        kind=kind,
        source=source,
        target=target,
        level="mandatory",
        span=SourceSpan(diagram_id="v", line=1, column=1),
    )
    stereotypes = {n: ("module" if n in modules else "class") for n in (source, target)}
    fs = RelationChecker(root, ("src",)).check(ob, stereotypes)
    assert len(fs) == 1 and fs[0].requirement_id == ob.id and fs[0].behavior_id == "v"
    return fs[0]


@pytest.mark.parametrize(
    ("kind", "source", "target"),
    [
        ("inheritance", "Holder", "Base"),  # 再エクスポートをたどって model.Base
        ("composition", "Holder", "Part"),  # 注釈つき引数の代入
        ("aggregation", "Builder", "Part"),  # 構築の代入
        ("association", "Factory", "Part"),  # 戻り値の注釈つき関数の代入
        ("association", "Optional_", "Part"),  # クラス本体の文字列の注釈 Part | None
        ("association", "Child", "Part"),  # 基底の属性
        ("association", "registry", "Part"),  # モジュール直下の変数の注釈
        ("dependency", "service", "helpers"),  # モジュールの import
        ("dependency", "Holder", "Part"),  # クラスの定義のあるモジュールの import
    ],
)
def test_verified(root: Path, kind: RelationKind, source: str, target: str) -> None:
    f = _check(root, kind, source, target)
    assert f.verdict == "VERIFIED_IN_SCOPE", f.message
    assert f.code_refs and f.code_refs[0].path is not None


@pytest.mark.parametrize(
    ("kind", "source", "target", "words"),
    [
        ("inheritance", "Builder", "Base", "基底"),
        ("composition", "Callback", "Part", "型の属性が無い"),  # Callable の中は所有でない
        ("association", "Plain", "Part", "型の属性が無い"),  # 注釈の無い空のリスト
        ("dependency", "lonely", "Part", "import も名前の参照"),
        ("association", "registry", "Kind", "型の変数が無い"),
    ],
)
def test_violated(root: Path, kind: RelationKind, source: str, target: str, words: str) -> None:
    f = _check(root, kind, source, target)
    assert (f.verdict, f.category) == ("VIOLATED", "missing_effect"), f.message
    assert words in f.message


@pytest.mark.parametrize(
    ("source", "words"),
    [
        ("Untyped", "注釈の無い引数 x"),
        ("FromUntyped", "戻り値の注釈の無い"),
        ("Dynamic", "__getattr__"),
    ],
)
def test_unknown_when_absence_is_not_certain(root: Path, source: str, words: str) -> None:
    f = _check(root, "association", source, "Part")
    assert f.verdict == "UNKNOWN", f.message
    assert words in f.message


def test_missing_end_is_violated_and_ambiguous_end_is_unknown(root: Path, tmp_path: Path) -> None:
    f = _check(root, "dependency", "service", "Nowhere")
    assert (f.verdict, f.category) == ("VIOLATED", "missing_effect")
    _repo(tmp_path, {"src/other/domain.py": "class Part:\n    pass\n"})
    f = _check(root, "association", "Holder", "Part")
    assert f.verdict == "UNKNOWN" and "複数" in f.message


def test_star_import_makes_dependency_unknown(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {
            "src/app/domain.py": "class Part:\n    pass\n",
            "src/app/consumer.py": "from app.other import *\n\nx = 1\n",
            "src/app/other.py": "",
        },
    )
    f = _check(root, "dependency", "consumer", "Part", frozenset({"consumer"}))
    assert f.verdict == "UNKNOWN" and "import *" in f.message


def test_does_not_import_target(root: Path) -> None:
    before = set(sys.modules)
    _check(root, "inheritance", "Holder", "Base")
    assert not {m for m in set(sys.modules) - before if m.startswith("app")}


def _repo_with(tmp_path: Path, service: str, extra: dict[str, str] | None = None) -> Path:
    return _repo(
        tmp_path,
        {
            "src/app/__init__.py": "",
            "src/app/model.py": "class Base:\n    pass\n\n\nclass Part:\n    pass\n",
            "src/app/service.py": service,
            **(extra or {}),
        },
    )


@pytest.mark.parametrize(
    "rebind",
    ["Base = object", "del Base", "def f():\n    global Base\n    Base = object"],
)
def test_rebound_name_is_not_resolved(tmp_path: Path, rebind: str) -> None:
    root = _repo_with(
        tmp_path, f"from app.model import Base\n\n{rebind}\n\n\nclass Child(Base):\n    pass\n"
    )
    f = _check(root, "inheritance", "Child", "Base")
    assert f.verdict == "UNKNOWN", f.message


def test_duplicate_import_of_the_same_name_still_resolves(tmp_path: Path) -> None:
    root = _repo_with(
        tmp_path,
        "from app.model import Base\n\n\ndef f() -> None:\n    from app.model import Base\n\n\n"
        "class Child(Base):\n    pass\n",
    )
    assert _check(root, "inheritance", "Child", "Base").verdict == "VERIFIED_IN_SCOPE"


@pytest.mark.parametrize(
    "body",
    [
        "        if False:\n            self.part = Part()",
        "        while 0:\n            self.part = Part()",
        "        return\n        self.part = Part()",
        "        if True:\n            pass\n        else:\n            self.part = Part()",
    ],
)
def test_unreachable_assignment_is_not_evidence(tmp_path: Path, body: str) -> None:
    root = _repo_with(
        tmp_path,
        f"from app.model import Part\n\n\nclass Holder:\n    def __init__(self) -> None:\n{body}\n",
    )
    f = _check(root, "association", "Holder", "Part")
    assert f.verdict == "VIOLATED", f.message


def test_assignment_under_a_runtime_condition_is_evidence(tmp_path: Path) -> None:
    root = _repo_with(
        tmp_path,
        "from app.model import Part\n\n\nclass Holder:\n    def __init__(self, on: bool) -> None:\n"
        "        if on:\n            self.part = Part()\n",
    )
    assert _check(root, "association", "Holder", "Part").verdict == "VERIFIED_IN_SCOPE"


@pytest.mark.parametrize(
    ("decorator", "verdict"),
    [
        ("@builtins.staticmethod", "VIOLATED"),
        ("@staticmethod", "VIOLATED"),
        ("@classmethod", "VIOLATED"),
        ("@property", "VERIFIED_IN_SCOPE"),
        ("@custom", "UNKNOWN"),
    ],
)
def test_decorators_are_resolved_by_qualified_name(
    tmp_path: Path, decorator: str, verdict: str
) -> None:
    root = _repo_with(
        tmp_path,
        "import builtins\n\nfrom app.model import Part\nfrom app.deco import custom\n\n\n"
        f"class Holder:\n    {decorator}\n    def make(obj, part: Part) -> None:\n"
        "        obj.part = part\n",
        {"src/app/deco.py": "def custom(f):\n    return f\n"},
    )
    f = _check(root, "association", "Holder", "Part")
    assert f.verdict == verdict, f.message


def test_base_is_followed_by_qualified_name(tmp_path: Path) -> None:
    root = _repo_with(
        tmp_path,
        "from app.base import RealBase as Alias\n\n\nclass Child(Alias):\n    pass\n",
        {
            "src/app/base.py": "class RealBase:\n    pass\n",
            "src/app/unrelated.py": (
                "from app.model import Part\n\n\nclass Alias:\n    part: Part\n"
            ),
        },
    )
    f = _check(root, "association", "Child", "Part")
    assert f.verdict == "VIOLATED", f.message
