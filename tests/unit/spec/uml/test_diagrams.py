"""図ごとの正常系と拒否（grammar.md 4–7 章、設計 4.1）。"""

from __future__ import annotations

import pytest

from speclino.domain.expr import Binary, Not, const, eq, ref
from speclino.domain.uml import CallLabel, CompiledSpec, EpsilonLabel, ParseResult
from speclino.spec.uml.compile import compile_spec
from tests.unit.spec.uml.samples import ACTIVITY, CLASSES, FULL, SEQUENCE, STATES, block


def _ok(md: str) -> CompiledSpec:
    res = compile_spec(md, domain=None, legacy_ir=[])
    assert res.compiled is not None, [d.message for d in res.diagnostics]
    return res.compiled


def _codes(md: str) -> list[str]:
    res = compile_spec(md, domain=None, legacy_ir=[])
    assert res.compiled is None
    return [d.code for d in res.diagnostics]


def _seq(*body: str) -> str:
    return CLASSES + block(
        "%%@ id: s",
        "%%@ entry: OrderService.checkout",
        "sequenceDiagram",
        "    participant S as OrderService",
        "    participant I as InventoryService",
        *body,
    )


# ---------------------------------------------------------------- クラス図


def test_class_diagram() -> None:
    c = _ok(CLASSES)
    names = [k.name for k in c.classes]
    assert names == ["OrderService", "InventoryService", "PaymentGateway", "Order", "OrderStatus"]
    order = c.classes[3]
    assert [(a.name, a.type, a.enum) for a in order.attributes] == [
        ("qty", "int", None),
        ("paid", "bool", None),
        ("order_id", "opaque_id", None),
        ("status", "enum", "OrderStatus"),
    ]
    assert c.classes[4].stereotype == "enumeration"
    assert c.classes[4].enum_values == ["PENDING", "PAID", "CANCELLED"]
    assert [s.id for s in c.structure] == [
        "vocab#OrderService",
        "vocab#InventoryService",
        "vocab#PaymentGateway",
    ]
    assert c.structure[0].operations[0].params == ["order", "payment"]
    assert c.classes[0].span.line == 6


@pytest.mark.parametrize(
    ("line", "code"),
    [
        ("    Order <|-- Special", "E_UNSUPPORTED_SYNTAX"),
        ("    Order : +int qty", "E_UNSUPPORTED_SYNTAX"),
        ("    class Order", "E_DUPLICATE_ID"),
    ],
)
def test_class_diagram_rejects(line: str, code: str) -> None:
    md = CLASSES.replace("```\n", line + "\n```\n", 1).replace("```mermaid" + line, "```mermaid")
    assert code in _codes(md)


def test_class_unknown_attribute_type() -> None:
    md = block("%%@ id: v", "classDiagram", "    class A {", "        Money price", "    }")
    assert _codes(md) == ["E_UNKNOWN_TYPE"]


# ---------------------------------------------------------------- シーケンス図


def test_sequence_diagram_automaton() -> None:
    c = _ok(CLASSES + SEQUENCE)
    (t,) = c.traces
    assert t.id == "checkout#OrderService.checkout"
    assert t.mode == "contains_subsequence" and t.diagram_kind == "sequence"
    a = t.automaton
    rows = [
        (x.id, x.source, x.target, x.label.operation if isinstance(x.label, CallLabel) else "ε")
        for x in a.transitions
    ]
    assert rows == [
        ("m1", "s0", "s1", "InventoryService.reserve"),
        ("g1", "s1", "s2", "ε"),
        ("m2", "s2", "s3", "PaymentGateway.charge"),
        ("g2", "s1", "s4", "ε"),
        ("m3", "s4", "s5", "InventoryService.release"),
        ("g1.join", "s3", "s6", "ε"),
        ("g2.join", "s5", "s6", "ε"),
    ]
    assert a.initial == "s0" and a.accepting == ["s6"]
    g1 = Binary(op="gt", left=ref("before.order.qty"), right=const("integer", 0))
    assert a.transitions[1].guard == g1
    assert a.transitions[3].guard == Not(op="not", arg=g1)


def test_sequence_opt_loop_and_implicit_none() -> None:
    c = _ok(
        _seq(
            "    opt [o1] before.order.qty > 1",
            "        S->>I: [m1] reserve",
            "    end",
            "    loop [l1]",
            "        S->>I: [m2] reserve",
            "    end",
            "    alt [g1] before.order.paid",
            "        S->>I: [m3] release",
            "    end",
        )
    )
    ids = [(x.id, x.source, x.target) for x in c.traces[0].automaton.transitions]
    assert ids == [
        ("o1", "s0", "s1"),
        ("m1", "s1", "s2"),
        ("o1.join", "s2", "s3"),
        ("o1.skip", "s0", "s3"),
        ("l1", "s3", "s4"),
        ("m2", "s4", "s5"),
        ("l1.back", "s5", "s4"),
        ("l1.exit", "s4", "s6"),
        ("g1", "s6", "s7"),
        ("m3", "s7", "s8"),
        ("g1.join", "s8", "s9"),
        ("g1.none", "s6", "s9"),
    ]
    none = c.traces[0].automaton.transitions[-1]
    assert isinstance(none.label, EpsilonLabel)
    assert none.guard == Not(op="not", arg=ref("before.order.paid"))


def test_rejects_par() -> None:
    md = _seq("    par [p1] 並行", "        S->>I: [m1] reserve", "    and", "    end")
    assert "E_UNSUPPORTED_SYNTAX" in _codes(md)


@pytest.mark.parametrize("kw", ["critical", "rect", "break", "Note", "autonumber", "actor"])
def test_rejects_unsupported_sequence_syntax(kw: str) -> None:
    assert "E_UNSUPPORTED_SYNTAX" in _codes(_seq(f"    {kw} x"))


def test_rejects_natural_language_guard() -> None:
    md = _seq("    alt [g1] 在庫が足りない", "        S->>I: [m1] reserve", "    end")
    assert "E_GUARD_SYNTAX" in _codes(md)
    md = STATES.replace("[before.order.status == OrderStatus.PENDING]", "[未払いのとき]")
    assert "E_GUARD_SYNTAX" in _codes(CLASSES + md)
    md = ACTIVITY.replace("[e2] before.order.paid", "[e2] paid")
    assert "E_GUARD_SYNTAX" in _codes(CLASSES + md)


def test_rejects_duplicate_ids() -> None:
    assert "E_DUPLICATE_ID" in _codes(_seq("    S->>I: [m1] reserve", "    S->>I: [m1] release"))
    # ラベルだけ違う同じ ID（アクティビティ図のノード）
    md = ACTIVITY.replace("    z((end))", "    z((end))\n    r[PaymentGateway.charge]")
    assert "E_DUPLICATE_ID" in _codes(CLASSES + md)
    # 図 ID の重複
    assert "E_DUPLICATE_ID" in _codes(CLASSES + SEQUENCE + SEQUENCE)
    # 状態名と遷移 ID
    md = STATES.replace("[t2] cancel", "[PAID] cancel")
    assert "E_DUPLICATE_ID" in _codes(CLASSES + md)


def test_rejects_unknown_arrow() -> None:
    assert "E_UNKNOWN_ARROW" in _codes(_seq("    S-xI: [m1] reserve"))
    assert "E_UNKNOWN_ARROW" in _codes(_seq("    S-)I: [m1] reserve"))
    assert "E_UNKNOWN_ARROW" in _codes(CLASSES + ACTIVITY.replace("r -->|[e4]| z", "r --x z"))
    assert "E_UNKNOWN_ARROW" in _codes(CLASSES + ACTIVITY.replace("r -->|[e4]| z", "r -.-> z"))


def test_sequence_rejects_missing_id_undeclared_and_unclosed() -> None:
    assert "E_MISSING_ID" in _codes(_seq("    S->>I: reserve(order)"))
    assert "E_UNDECLARED_PARTICIPANT" in _codes(_seq("    S->>X: [m1] reserve"))
    assert "E_UNCLOSED_BLOCK" in _codes(_seq("    loop [l1]", "        S->>I: [m1] reserve"))
    assert "E_EMPTY_BLOCK" in _codes(_seq("    loop [l1]", "    end"))
    assert "E_GUARD_SYNTAX" in _codes(
        _seq("    loop [l1] 毎回", "        S->>I: [m1] a", "    end")
    )
    md = _seq(
        "    alt [g1] before.order.paid",
        "    else [g2]",
        "    else [g3] before.order.qty > 1",
        "    end",
    )
    assert "E_GUARD_SYNTAX" in _codes(md)


def test_sequence_requires_entry() -> None:
    md = CLASSES + SEQUENCE.replace("%%@ entry: OrderService.checkout\n", "")
    assert "E_HEADER" in _codes(md)


# ---------------------------------------------------------------- 状態遷移図


def test_state_diagram() -> None:
    c = _ok(CLASSES + STATES)
    (m,) = c.state_machines
    assert m.id == "order-status" and m.attribute == "order.status"
    assert m.states == ["PENDING", "PAID", "CANCELLED"] and m.initial == "PENDING"
    t1, t2 = m.transitions
    assert (t1.id, t1.event, t1.guard) == (
        "t1",
        "pay",
        eq(ref("before.order.status"), const("OrderStatus", "PENDING")),
    )
    assert (t2.id, t2.event, t2.guard) == ("t2", "cancel", None)


def test_state_rejects() -> None:
    assert "E_STATE_INITIAL" in _codes(CLASSES + STATES.replace("    [*] --> PENDING\n", ""))
    assert "E_MISSING_ID" in _codes(CLASSES + STATES.replace(": [t2] cancel", ""))
    assert "E_UNSUPPORTED_SYNTAX" in _codes(
        CLASSES + STATES.replace("    PAID --> [*]", "    state PAID {")
    )
    assert "E_UNKNOWN_ENUM" in _codes(
        CLASSES + STATES.replace("OrderStatus.PENDING", "OrderStatus.X")
    )


# ---------------------------------------------------------------- アクティビティ図


def test_activity_diagram() -> None:
    c = _ok(CLASSES + ACTIVITY)
    (t,) = c.traces
    assert t.mode == "included_in" and t.closed is True and t.diagram_kind == "activity"
    a = t.automaton
    assert a.states == ["a", "d", "r", "z"] and a.initial == "a" and a.accepting == ["z"]
    labels = [
        (x.id, x.source, x.target, x.label.operation if isinstance(x.label, CallLabel) else "ε")
        for x in a.transitions
    ]
    assert labels == [
        ("e1", "a", "d", "ε"),
        ("e2", "d", "r", "PaymentGateway.refund"),
        ("e3", "d", "z", "ε"),
        ("e4", "r", "z", "ε"),
    ]
    assert a.transitions[1].guard == ref("before.order.paid")


def test_activity_quoted_edge_labels_are_the_same_ir() -> None:
    """`|"[ID] guard"|` は Mermaid が描ける形。引用符の有無で IR は変わらない（S72）。"""
    quoted = ACTIVITY
    for old, new in (
        ("|[e1]|", '|"[e1]"|'),
        ("|[e2] before.order.paid|", '|"[e2] before.order.paid"|'),
        ("|[e3] not before.order.paid|", '|"[e3] not before.order.paid"|'),
        ("|[e4]|", '|"[e4]"|'),
    ):
        assert old in quoted
        quoted = quoted.replace(old, new)
    plain, q = _ok(CLASSES + ACTIVITY), _ok(CLASSES + quoted)
    assert q.ir_hash == plain.ir_hash
    assert "E_UNEXPECTED_TOKEN" in _codes(CLASSES + quoted.replace('|"[e4]"|', '|"[e4]|'))
    # 開きの無い引用符はガードの誤り（文字列はガードに書けない）
    assert "E_GUARD_SYNTAX" in _codes(CLASSES + quoted.replace('|"[e4]"|', '|[e4]"|'))


def test_activity_closed_defaults_false() -> None:
    c = _ok(CLASSES + ACTIVITY.replace("%%@ closed: true\n", ""))
    assert c.traces[0].closed is False


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        ("r -->|[e4]| z", "r --> z", "E_MISSING_ID"),
        ("r -->|[e4]| z", "r -->|[e4]| q", "E_UNDECLARED_NODE"),
        ("r -->|[e4]| z", "r -->|[e4]| z -->|[e5]| a", "E_UNEXPECTED_TOKEN"),
        ("    r -->|[e4]| z", "    subgraph x", "E_UNSUPPORTED_SYNTAX"),
        ("r[PaymentGateway.refund]", "r[返金する]", "E_ACTIVITY_LABEL"),
        ("    r -->|[e4]| z", "    r -->|[e4]| a", "E_ACTIVITY_START"),
        ("    r -->|[e4]| z", "", "E_DEAD_END"),
        ("    a -->|[e1]| d", "", "E_UNREACHABLE"),
    ],
)
def test_activity_rejects(old: str, new: str, code: str) -> None:
    assert code in _codes(CLASSES + ACTIVITY.replace(old, new))


def test_full_document() -> None:
    res: ParseResult = compile_spec(FULL, domain=None, legacy_ir=[])
    assert res.compiled is not None, res.diagnostics
    assert len(res.compiled.traces) == 2 and len(res.compiled.state_machines) == 1


# ---------------------------------------------------------------- クラス図の語彙との突き合わせ


def test_rejects_behavior_diagram_without_class_diagram() -> None:
    for md in (SEQUENCE, STATES, ACTIVITY):
        assert _codes(md) == ["E_NO_CLASS_DIAGRAM"]


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        ("[m2] charge(order)", "[m2] capture(order)", "E_UNKNOWN_OPERATION"),
        ("participant P as PaymentGateway", "participant P as Stripe", "E_UNKNOWN_CLASS"),
        ("%%@ entry: OrderService.checkout", "%%@ entry: OrderService.pay", "E_UNKNOWN_OPERATION"),
        ("%%@ entry: OrderService.checkout", "%%@ entry: Checkout.run", "E_UNKNOWN_CLASS"),
        ("before.order.qty > 0", "before.order.amount > 0", "E_UNKNOWN_REFERENCE"),
        ("before.order.qty > 0", "before.cart.qty > 0", "E_UNKNOWN_REFERENCE"),
        ("before.order.qty > 0", "outcome.body > 0", "E_UNKNOWN_REFERENCE"),
    ],
)
def test_rejects_sequence_vocabulary_mismatch(old: str, new: str, code: str) -> None:
    assert code in _codes(CLASSES + SEQUENCE.replace(old, new))


def test_rejects_activity_operation_not_in_class_diagram() -> None:
    md = ACTIVITY.replace("r[PaymentGateway.refund]", "r[PaymentGateway.void]")
    assert "E_UNKNOWN_OPERATION" in _codes(CLASSES + md)
    md = ACTIVITY.replace("%%@ entry: OrderService.cancel", "%%@ entry: OrderService.abort")
    assert "E_UNKNOWN_OPERATION" in _codes(CLASSES + md)


def test_rejects_state_attribute_mismatch() -> None:
    assert "E_UNKNOWN_REFERENCE" in _codes(
        CLASSES + STATES.replace("attribute: order.status", "attribute: order.phase")
    )
    assert "E_STATE_ATTRIBUTE_TYPE" in _codes(
        CLASSES + STATES.replace("attribute: order.status", "attribute: order.qty")
    )
    assert "E_UNKNOWN_STATE" in _codes(CLASSES + STATES.replace("CANCELLED", "SHIPPED"))


def test_rejects_enum_value_of_other_enum() -> None:
    classes = CLASSES.replace(
        "    class OrderStatus {",
        "    class Color {\n        <<enumeration>>\n        RED\n    }\n    class OrderStatus {",
    )
    md = STATES.replace("OrderStatus.PENDING", "Color.RED")
    assert "E_UNKNOWN_ENUM" in _codes(classes + md)


def test_state_attribute_resolves_by_lowercased_class_name() -> None:
    c = _ok(CLASSES + STATES)
    assert c.state_machines[0].attribute == "order.status"


# ---------------------------------------------------------------- 中身の無い図・付け忘れ・予約語


@pytest.mark.parametrize(
    "md",
    [
        block("%%@ id: e", "classDiagram"),
        block(
            "%%@ id: e",
            "%%@ entry: OrderService.checkout",
            "sequenceDiagram",
            "    participant S as OrderService",
        ),
        block("%%@ id: e", "%%@ attribute: order.status", "stateDiagram-v2", "    [*] --> PENDING"),
        block(
            "%%@ id: e",
            "%%@ entry: OrderService.cancel",
            "flowchart TD",
            "    a((start))",
            "    z((end))",
            "    a -->|[e1]| z",
        ),
    ],
)
def test_rejects_empty_diagram(md: str) -> None:
    assert "E_EMPTY_DIAGRAM" in _codes(CLASSES + md)


def test_missing_namespace_message() -> None:
    res = compile_spec(
        CLASSES + SEQUENCE.replace("before.order.qty", "order.qty"), domain=None, legacy_ir=[]
    )
    (d,) = [d for d in res.diagnostics if d.code == "E_GUARD_SYNTAX"]
    assert "before./after./outcome. で始める" in d.message and "before.order.qty" in d.message


def test_reserved_word_is_warning() -> None:
    md = SEQUENCE.replace(
        "participant I as InventoryService", "participant end as InventoryService"
    )
    md = md.replace("S->>I", "S->>end").replace("I-->>S", "end-->>S")
    res = compile_spec(CLASSES + md, domain=None, legacy_ir=[])
    assert res.compiled is not None
    assert [d.code for d in res.diagnostics] == ["W_RESERVED_WORD"]
    act = ACTIVITY.replace("z((end))", "end((end))").replace("| z", "| end")
    res = compile_spec(CLASSES + act, domain=None, legacy_ir=[])
    assert res.compiled is not None
    assert {d.code for d in res.diagnostics} == {"W_RESERVED_WORD"}


# ---------------------------------------------------------------- <<module>>


def _module_classes(*members: str, extra: str = "") -> str:
    return block(
        "%%@ id: mods",
        "classDiagram",
        "    class knowledge_registry {",
        "        <<module>>",
        *(f"        {m}" for m in members),
        "    }",
        *([extra] if extra else []),
    )


def test_module_class_accepted() -> None:
    c = _ok(_module_classes("approve(revision_id, actor)", "+list_bundles()"))
    mod = c.classes[0]
    assert mod.stereotype == "module"
    assert [(o.name, o.params) for o in mod.operations] == [
        ("approve", ["revision_id", "actor"]),
        ("list_bundles", []),
    ]
    assert [(s.id, s.class_name) for s in c.structure] == [
        ("mods#knowledge_registry", "knowledge_registry")
    ]


def test_module_without_operations_has_no_obligation() -> None:
    c = _ok(_module_classes())
    assert c.classes[0].stereotype == "module" and c.structure == []


def test_module_rejects_attribute_and_enum_value() -> None:
    assert "E_MODULE_MEMBER" in _codes(_module_classes("int qty"))
    assert "E_MODULE_MEMBER" in _codes(_module_classes("PENDING"))


def test_module_rejects_second_stereotype() -> None:
    # 同じステレオタイプの重ね書きは従来（<<enumeration>> の 2 回）どおり受け付ける
    assert _ok(_module_classes("<<module>>")).classes[0].stereotype == "module"
    assert "E_UNSUPPORTED_SYNTAX" in _codes(_module_classes("<<enumeration>>"))
    assert "E_UNSUPPORTED_SYNTAX" in _codes(_module_classes("<<interface>>"))


def test_module_cannot_be_attribute_type() -> None:
    md = block(
        "%%@ id: mods",
        "classDiagram",
        "    class knowledge_registry {",
        "        <<module>>",
        "        approve(a)",
        "    }",
        "    class Order {",
        "        knowledge_registry reg",
        "    }",
    )
    assert "E_UNKNOWN_TYPE" in _codes(md)


def test_module_can_own_sequence_calls() -> None:
    md = block(
        "%%@ id: mods",
        "classDiagram",
        "    class OrderService {",
        "        checkout(order)",
        "    }",
        "    class knowledge_registry {",
        "        <<module>>",
        "        approve(order)",
        "    }",
    ) + block(
        "%%@ id: s",
        "%%@ entry: OrderService.checkout",
        "sequenceDiagram",
        "    participant S as OrderService",
        "    participant K as knowledge_registry",
        "    S->>K: [m1] approve(order)",
    )
    c = _ok(md)
    assert any(
        isinstance(t.label, CallLabel) and t.label.operation == "knowledge_registry.approve"
        for tr in c.traces
        for t in tr.automaton.transitions
    )
