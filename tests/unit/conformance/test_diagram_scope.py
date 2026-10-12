"""ワークスペース全体の仕様の、図ごとの対象・対象外（conformance/diagram_scope.py）。"""

from __future__ import annotations

from speclino.conformance.diagram_scope import (
    diagram_elements,
    narrow,
    obligation_diagram,
    out_of_scope_diagrams,
)
from speclino.domain.uml import CompiledSpec
from speclino.spec.uml import compile_spec


def block(*lines: str) -> str:
    return "```mermaid\n" + "\n".join(lines) + "\n```\n"


MD = "# x\n\n" + "\n".join(
    [
        block(
            "%%@ id: cls",
            "classDiagram",
            "    class Svc {",
            "        +place(order)",
            "    }",
            "    class Repo {",
            "        +save(order)",
            "    }",
            "    class tools {",
            "        <<module>>",
            "        +log(order)",
            "    }",
            "    class Order {",
            "        Phase phase",
            "    }",
            "    class Phase {",
            "        <<enumeration>>",
            "        A",
            "        B",
            "    }",
        ),
        block(
            "%%@ id: seq",
            "%%@ entry: Svc.place",
            "sequenceDiagram",
            "    participant S as Svc",
            "    participant R as Repo",
            "    S->>R: [m1] save(order)",
        ),
        block(
            "%%@ id: st",
            "%%@ attribute: order.phase",
            "stateDiagram-v2",
            "    [*] --> A",
            "    A --> B: [t1] go",
        ),
    ]
)


def compiled() -> CompiledSpec:
    result = compile_spec(MD, domain=None, legacy_ir=[])
    assert result.compiled is not None, result.diagnostics
    return result.compiled


def test_elements_per_diagram() -> None:
    assert diagram_elements(compiled()) == {
        "cls": [("class", "Svc"), ("class", "Repo"), ("module", "tools")],
        "seq": [("entry", "Svc.place"), ("class", "Svc"), ("class", "Repo")],
        "st": [("attribute", "order.phase")],
    }


def test_diagram_is_out_of_scope_only_when_no_element_resolves() -> None:
    c = compiled()
    assert out_of_scope_diagrams(c, lambda kind, name: False) == ["cls", "seq", "st"]
    # 1 つでも解決できれば対象（クラス図は tools だけ、シーケンス図は Repo だけ）
    known = {("module", "tools"), ("class", "Repo")}
    assert out_of_scope_diagrams(c, lambda kind, name: (kind, name) in known) == ["st"]


def test_narrow_drops_only_the_obligations_of_skipped_diagrams() -> None:
    c = compiled()
    n = narrow(c, ["cls", "st"])
    assert [s.id for s in n.structure] == []
    assert [t.id for t in n.traces] == ["seq#Svc.place"]
    assert n.state_machines == []
    assert n.requirements == c.requirements and n.classes == c.classes
    assert narrow(c, []) is c
    assert obligation_diagram("seq#Svc.place") == "seq" and obligation_diagram("st") == "st"
