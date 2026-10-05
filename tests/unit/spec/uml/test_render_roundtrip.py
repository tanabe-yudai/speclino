"""render と往復の性質（設計 4.3: `parse(render(parse(s))) == parse(s)`）。

本文は grammar.md の文法から hypothesis で生成する。見た目だけの揺れ（別名・送り手・応答・コメント・
可視性・空白・ノードの形・判断のラベル・終了の遷移・余分な括弧）も混ぜ、正規形で落ちても意味が
変わらないことを確かめる。等価は `semantic_dump`
（source_hash・ir_hash・span の行と列を除く。grammar.md 11 章）。
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from speclino.domain.expr import BOOL_TYPE, INT_TYPE, Binary, Const, Expr, Nary, Not, Ref
from speclino.spec.uml.compile import compile_spec, semantic_dump
from speclino.spec.uml.guard import render_expr
from speclino.spec.uml.render import render
from tests.unit.spec.uml.samples import FULL

ENUMS = {"E0": ["A", "B", "C"], "E1": ["X", "Y"]}
CLASSES = ["C0", "C1", "C2"]
METHODS = ["m0", "m1", "m2"]


# ---------------------------------------------------------------- ガード


def _atoms() -> st.SearchStrategy[Expr]:
    refs = st.sampled_from(
        ["before.order.v", "after.order.v", "outcome.status", "before.order.qty"]
    ).map(lambda p: Ref(op="ref", path=p))
    ints = st.integers(-50, 50).map(lambda v: Const(op="const", type=INT_TYPE, value=v))
    return st.one_of(refs, ints)


def _bool_atoms() -> st.SearchStrategy[Expr]:
    flags = st.sampled_from(["before.order.flag", "after.order.ok"]).map(
        lambda p: Ref(op="ref", path=p)
    )
    bools = st.booleans().map(lambda v: Const(op="const", type=BOOL_TYPE, value=v))
    enum_cmp = st.builds(
        lambda t, i, op: Binary(
            op=op,
            left=Ref(op="ref", path="before.order.status"),
            right=Const(op="const", type=t, value=ENUMS[t][i % len(ENUMS[t])]),
        ),
        st.just("E0"),  # before.order.status の列挙
        st.integers(0, 5),
        st.sampled_from(["eq", "ne"]),
    )
    arith = st.recursive(
        _atoms(),
        lambda inner: st.builds(
            lambda op, a, b: Binary(op=op, left=a, right=b),
            st.sampled_from(["add", "sub"]),
            inner,
            inner,
        ),
        max_leaves=3,
    )
    cmp = st.builds(
        lambda op, a, b: Binary(op=op, left=a, right=b),
        st.sampled_from(["eq", "ne", "lt", "le", "gt", "ge"]),
        arith,
        arith,
    )
    return st.one_of(flags, bools, enum_cmp, cmp)


GUARDS: st.SearchStrategy[Expr] = st.recursive(
    _bool_atoms(),
    lambda inner: st.one_of(
        inner.map(lambda e: Not(op="not", arg=e)),
        st.builds(
            lambda op, args: Nary(op=op, args=args),
            st.sampled_from(["and", "or"]),
            st.lists(inner, min_size=2, max_size=3),
        ),
    ),
    max_leaves=5,
)


@st.composite
def guard_text(draw: st.DrawFn) -> str:
    text = render_expr(draw(GUARDS))
    return f"({text})" if draw(st.booleans()) else text


# ---------------------------------------------------------------- 図


class Ids:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}{self.n}"


def _noise(draw: st.DrawFn) -> list[str]:
    return draw(st.sampled_from([[], [""], ["    %% コメント"], ["", "%% x"]]))


@st.composite
def class_block(draw: st.DrawFn) -> tuple[str, list[str]]:
    lines = ["%%@ id: vocab", "classDiagram"]
    if draw(st.booleans()):
        lines.append("    direction LR")
    for name, values in ENUMS.items():
        lines += [
            f"    class {name} {{",
            "        <<enumeration>>",
            *(f"        {v}" for v in values),
            "    }",
        ]
    for c in CLASSES:
        lines.append(f"    class {c} {{")
        vis = draw(st.sampled_from(["", "+", "-", "#", "~"]))
        for m in METHODS:
            params = draw(st.lists(st.sampled_from(["a", "b", "c"]), unique=True, max_size=3))
            lines.append(f"        {vis}{m}({', '.join(params)})")
        for i, t in enumerate(
            draw(st.lists(st.sampled_from(["int", "bool", "str", "opaque_id", "E0"]), max_size=3))
        ):
            lines.append(f"        {t} f{i}")
        lines += _noise(draw)
        lines.append("    }")
    lines += ["    class Order {", "        int qty", "        int v", "        bool flag"]
    lines += ["        bool ok", "        E0 status", "    }"]
    lines.append("    class Empty")
    return "```mermaid\n" + "\n".join(lines) + "\n```\n", [
        "vocab#" + c for c in [*CLASSES, "Empty"]
    ]


@st.composite
def sequence_block(draw: st.DrawFn, ids: Ids, diagram_id: str) -> tuple[str, list[str]]:
    aliases = {c: draw(st.sampled_from([c, f"P{c}"])) for c in CLASSES}
    refs: list[str] = []
    lines = [f"%%@ id: {diagram_id}", "%%@ entry: C0.m0", "sequenceDiagram"]
    for c, a in aliases.items():
        lines.append(f"    participant {a} as {c}" if a != c else f"    participant {c}")

    def items(depth: int, indent: int) -> list[str]:
        pad = "    " * indent
        out: list[str] = []
        for _ in range(draw(st.integers(1 if depth else 0, 3))):
            kind = draw(
                st.sampled_from(
                    ["call", "call", "reply"] + (["alt", "opt", "loop"] if depth < 3 else [])
                )
            )
            if kind in ("call", "reply"):
                src = aliases[draw(st.sampled_from(CLASSES))]
                dst = aliases[draw(st.sampled_from(CLASSES))]
                mid = ids("m")
                if kind == "reply":
                    out.append(f"{pad}{dst}-->>{src}: [{mid}] 結果 ok")
                    continue
                args = draw(st.sampled_from(["", "(order)", "()", "(a, b)"]))
                out.append(f"{pad}{src}->>{dst}: [{mid}] {draw(st.sampled_from(METHODS))}{args}")
                refs.append(f"{diagram_id}#{mid}")
            elif kind == "alt":
                gid = ids("g")
                refs.append(f"{diagram_id}#{gid}")
                out.append(f"{pad}alt [{gid}] {draw(guard_text())}")
                out += items(depth + 1, indent + 1)
                n_else = draw(st.integers(0, 2))
                for k in range(n_else):
                    eid = ids("g")
                    bare = k == n_else - 1 and draw(st.booleans())
                    out.append(f"{pad}else [{eid}]" + ("" if bare else f" {draw(guard_text())}"))
                    out += items(depth + 1, indent + 1)
                out.append(f"{pad}end")
            elif kind == "opt":
                oid = ids("o")
                guard = f" {draw(guard_text())}" if draw(st.booleans()) else ""
                out.append(f"{pad}opt [{oid}]{guard}")
                out += items(depth + 1, indent + 1)
                out.append(f"{pad}end")
            else:
                lid = ids("l")
                out.append(f"{pad}loop [{lid}]")
                out += items(depth + 1, indent + 1)
                if not any("->>" in x and "-->>" not in x for x in out[-1:]):
                    out.append(
                        f"{pad}    C0->>C1: [{ids('m')}] m1".replace("C0", aliases["C0"]).replace(
                            "C1", aliases["C1"]
                        )
                    )
                out.append(f"{pad}end")
            out += _noise(draw)
        return out

    lines += items(0, 1)
    lines.append(f"    {aliases['C0']}->>{aliases['C1']}: [{ids('m')}] m2")  # 呼出しを 1 つは持つ
    return "```mermaid\n" + "\n".join(lines) + "\n```\n", refs


@st.composite
def state_block(draw: st.DrawFn, ids: Ids, diagram_id: str) -> tuple[str, list[str]]:
    states = ENUMS["E0"][: draw(st.integers(1, 3))]  # 状態は order.status の列挙値
    lines = [f"%%@ id: {diagram_id}", "%%@ attribute: order.status", "stateDiagram-v2"]
    initial = draw(st.sampled_from(states))
    body = [f"    [*] --> {initial}"]
    refs = [f"{diagram_id}#{initial}"]
    for _ in range(draw(st.integers(1, 5))):
        tid = ids("t")
        label = f"[{tid}]"
        if draw(st.booleans()):
            label += f" ev{draw(st.integers(0, 3))}"
        if draw(st.booleans()):
            label += f" [{draw(guard_text())}]"
        body.append(
            f"    {draw(st.sampled_from(states))} --> {draw(st.sampled_from(states))}: {label}"
        )
        refs.append(f"{diagram_id}#{tid}")
    if draw(st.booleans()):
        body.append(f"    {body[0].split()[-1]} --> [*]")
    body = draw(st.permutations(body))
    if draw(st.booleans()):
        lines.append("    direction LR")
    return "```mermaid\n" + "\n".join(lines + list(body)) + "\n```\n", refs


@st.composite
def activity_block(draw: st.DrawFn, ids: Ids, diagram_id: str) -> tuple[str, list[str]]:
    n = draw(st.integers(1, 4))
    middle = [f"n{i}" for i in range(n)]
    kinds = {m: draw(st.sampled_from(["process", "decision"])) for m in middle}
    kinds["n0"] = "process"  # 処理ノードを 1 つは持つ
    nodes = ["s0", *middle, "z0"]
    decls = ["    s0((start))"]
    for m in middle:
        if kinds[m] == "process":
            op = f"{draw(st.sampled_from(CLASSES))}.{draw(st.sampled_from(METHODS))}"
            shape = draw(st.sampled_from(["[{}]", "({})", "([{}])"]))
            decls.append(f"    {m}{shape.format(op)}")
        else:
            decls.append(f"    {m}{{{draw(st.sampled_from(['判断', 'ok?', '']))}}}")
    decls.append("    z0((end))")
    edges: list[str] = []
    refs = [f"{diagram_id}#{m}" for m in middle]

    def edge(a: str, b: str) -> None:
        eid = ids("e")
        guard = f" {draw(guard_text())}" if draw(st.booleans()) else ""
        edges.append(f"    {a} -->|[{eid}]{guard}| {b}")
        refs.append(f"{diagram_id}#{eid}")

    for a, b in zip(nodes, nodes[1:], strict=False):
        edge(a, b)
    for _ in range(draw(st.integers(0, 3))):
        edge(draw(st.sampled_from(nodes[:-1])), draw(st.sampled_from(nodes[1:])))
    header = [f"%%@ id: {diagram_id}", "%%@ entry: C0.m1"]
    if draw(st.booleans()):
        header.append(f"%%@ closed: {draw(st.sampled_from(['true', 'false']))}")
    return "```mermaid\n" + "\n".join(header + ["flowchart LR"] + decls + edges) + "\n```\n", refs


@st.composite
def documents(draw: st.DrawFn) -> str:
    ids = Ids()
    blocks: list[str] = []
    refs: list[str] = []
    cls, crefs = draw(class_block())
    blocks.append(cls)
    refs += crefs
    for i in range(draw(st.integers(0, 2))):
        b, r = draw(sequence_block(ids, f"seq-{i}"))
        blocks.append(b)
        refs += r
    for i in range(draw(st.integers(0, 2))):
        b, r = draw(state_block(ids, f"st-{i}"))
        blocks.append(b)
        refs += r
    for i in range(draw(st.integers(0, 2))):
        b, r = draw(activity_block(ids, f"act-{i}"))
        blocks.append(b)
        refs += r
    blocks = list(draw(st.permutations(blocks)))
    rows = []
    for i in range(draw(st.integers(0, 4))):
        chosen = draw(st.lists(st.sampled_from(refs), max_size=2, unique=True)) if refs else []
        cell = ", ".join(chosen) if chosen else draw(st.sampled_from(["", "-"]))
        level = draw(st.sampled_from(["mandatory", "advisory"]))
        rows.append(f"| R{i} | {level} | 要求 {i} を満たす | {cell} |")
    table = ""
    if rows:
        table = (
            "| ID | level | 要求 | 形式化 |\n| --- | --- | --- | --- |\n" + "\n".join(rows) + "\n"
        )
    return "# 仕様\n\n本文。\n\n" + table + "\n" + "\n".join(blocks)


# ---------------------------------------------------------------- 性質


def _compile_ok(md: str):  # type: ignore[no-untyped-def]
    res = compile_spec(md, domain=None, legacy_ir=[])
    assert res.compiled is not None, (md, [(d.code, d.message, d.span) for d in res.diagnostics])
    return res.compiled


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(documents())
def test_roundtrip_property(md: str) -> None:
    first = _compile_ok(md)
    canonical = render(first)
    second = _compile_ok(canonical)
    assert semantic_dump(second) == semantic_dump(first)
    assert second.ir_hash == first.ir_hash
    assert render(second) == canonical  # 正規形は冪等


def test_render_full_sample() -> None:
    first = _compile_ok(FULL)
    canonical = render(first)
    assert "%%@ id: checkout" in canonical and "participant OrderService" in canonical
    assert "OrderService->>InventoryService: [m1] reserve" in canonical
    assert "    else [g2]\n" in canonical  # 省いたガードは省いたまま
    assert "%%@ closed: true" in canonical
    # アクティビティ図の辺のラベルは引用符で囲む（Mermaid は `|[` で始まるラベルを描けない）
    assert '    d -->|"[e2] before.order.paid"| r' in canonical
    assert semantic_dump(_compile_ok(canonical)) == semantic_dump(first)
