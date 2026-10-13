"""Markdown（UML）と旧形式の Spec IR → `CompiledSpec`。

決定的（LLM・ネットワーク・時刻に依存しない）で、全か無か
（分類されないトークンが 1 つでもあればエラー）。
"""

from __future__ import annotations

import json
from typing import Any

from speclino.conformance.typing import OUTCOME_VARIABLES, IRTypeError, TypeEnv, require_boolean
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.expr import BOOL_TYPE, INT_TYPE, Binary, Const, Expr, Nary, Not, Ref, refs_in
from speclino.domain.spec_ir import RuleLevel, SpecIR
from speclino.domain.uml import (
    ClassAttribute,
    ClassDef,
    CompiledSpec,
    DiagramKind,
    ParseResult,
    RelationObligation,
    RequirementRow,
    SourceSpan,
    SpecDiagnostic,
    StateMachine,
    StructureObligation,
    TraceObligation,
)
from speclino.spec.loader import content_hash
from speclino.spec.spec_validator import validate_spec_ir
from speclino.spec.uml._parse import DIRECTIONS, Ctx
from speclino.spec.uml.activity import parse_activity
from speclino.spec.uml.class_diagram import parse_class_diagram
from speclino.spec.uml.lexer import tokenize_block_line, tokenize_line
from speclino.spec.uml.limits import (
    MAX_AUTOMATON_SIZE,
    MAX_BODY_BYTES,
    MAX_DIAGRAMS,
    MAX_LINE_CHARS,
    MAX_STATEMENTS_PER_DIAGRAM,
    MAX_TOKENS,
)
from speclino.spec.uml.markdown import split_markdown
from speclino.spec.uml.sequence import parse_sequence
from speclino.spec.uml.state import parse_state
from speclino.spec.uml.types import (
    DOC_ID,
    Coverage,
    DiagramResult,
    MermaidBlock,
    RequirementTable,
    Token,
)

PARSER_VERSION = "uml-0.1"
_PLACEHOLDER_HASH = "sha256:" + "0" * 64
_BUILTIN_ATTR_TYPES = frozenset({"int", "bool", "str", "opaque_id"})
_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
_HEADER_KEYS: dict[DiagramKind, frozenset[str]] = {
    "class": frozenset({"id"}),
    "sequence": frozenset({"id", "entry"}),
    "state": frozenset({"id", "attribute"}),
    "activity": frozenset({"id", "entry", "closed"}),
}
_REQUIRED_KEYS: dict[DiagramKind, frozenset[str]] = {
    "class": frozenset({"id"}),
    "sequence": frozenset({"id", "entry"}),
    "state": frozenset({"id", "attribute"}),
    "activity": frozenset({"id", "entry"}),
}


def _doc_error(code: str, message: str, line: int | None = None, column: int = 1) -> SpecDiagnostic:
    span = None if line is None else SourceSpan(diagram_id=DOC_ID, line=line, column=column)
    return SpecDiagnostic(code=code, severity="error", message=message, span=span)


# ---------------------------------------------------------------- ハッシュと正規形


def semantic_dump(compiled: CompiledSpec) -> dict[str, Any]:
    """意味の正規形（grammar.md 11 章）。`source_hash`・`ir_hash`・span の行と列を除く。"""
    data = compiled.model_dump(mode="json")
    data.pop("source_hash", None)
    data.pop("ir_hash", None)

    def strip(obj: Any) -> Any:
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
            for k, v in obj.items():
                if k == "span" and isinstance(v, dict):
                    out[k] = {"diagram_id": v["diagram_id"]}
                elif k == "spans" and isinstance(v, list):
                    out[k] = [{"diagram_id": s["diagram_id"]} for s in v]
                else:
                    out[k] = strip(v)
            return out
        if isinstance(obj, list):
            return [strip(x) for x in obj]
        return obj

    return strip(data)  # type: ignore[no-any-return]


def ir_hash_of(compiled: CompiledSpec) -> str:
    canonical = json.dumps(
        semantic_dump(compiled), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return content_hash(canonical)


# ---------------------------------------------------------------- 図のブロック


def _parse_headers(
    tokens: list[Token], coverage: Coverage
) -> tuple[dict[str, tuple[str, Token]], list[SpecDiagnostic]]:
    headers: dict[str, tuple[str, Token]] = {}
    diags: list[SpecDiagnostic] = []
    for tok in tokens:
        body = tok.text[3:]
        key, sep, value = body.partition(":")
        key, value = key.strip(), value.strip()
        if not sep or not key or not value:
            coverage.mark([tok], "error")
            diags.append(_doc_error("E_HEADER", "宣言は `%%@ key: value`", tok.line, tok.column))
            continue
        if key in headers:
            coverage.mark([tok], "error")
            diags.append(_doc_error("E_HEADER", f"宣言 {key} が重複", tok.line, tok.column))
            continue
        coverage.mark([tok], "ir")
        headers[key] = (value, tok)
    return headers, diags


def _valid_diagram_id(v: str) -> bool:
    return bool(v) and v[0].isascii() and v[0].isalpha() and all(c in _ID_CHARS for c in v)


def _single_name(value: str) -> str | None:
    toks = tokenize_line(value, 1)
    if len(toks) == 1 and toks[0].kind == "NAME":
        return toks[0].text
    return None


def _diagram_kind(cur_tokens: list[Token]) -> tuple[DiagramKind | None, str]:
    texts = [t.text for t in cur_tokens]
    if texts == ["classDiagram"]:
        return "class", ""
    if texts == ["sequenceDiagram"]:
        return "sequence", ""
    if texts in (["stateDiagram"], ["stateDiagram", "-", "v2"]):
        return "state", ""
    if len(texts) == 2 and texts[0] == "flowchart" and texts[1] in DIRECTIONS:
        return "activity", ""
    head = texts[0] if texts else ""
    return None, f"対応しない図の種類: {' '.join(texts)!r}" if head else "図の種類が無い"


def _compile_block(
    block: MermaidBlock, coverage: Coverage
) -> tuple[DiagramResult | None, list[SpecDiagnostic]]:
    diags: list[SpecDiagnostic] = []
    header_toks: list[Token] = []
    type_line: list[Token] | None = None
    body: list[list[Token]] = []
    for line_no, text in block.lines:
        toks = tokenize_block_line(text, line_no)
        coverage.add(toks)
        if not toks:
            continue
        tok_kind = toks[0].kind
        if tok_kind == "COMMENT":
            coverage.mark(toks, "ignored")
        elif tok_kind == "DIRECTIVE":
            coverage.mark(toks, "error")
            diags.append(
                _doc_error(
                    "E_UNSUPPORTED_SYNTAX",
                    "Mermaid の directive は書けない",
                    line_no,
                    toks[0].column,
                )
            )
        elif tok_kind == "HEADER":
            if type_line is not None:
                coverage.mark(toks, "error")
                diags.append(
                    _doc_error(
                        "E_HEADER", "`%%@` の宣言は図の種類の行より前", line_no, toks[0].column
                    )
                )
            else:
                header_toks.extend(toks)
        elif type_line is None:
            type_line = toks
        else:
            body.append(toks)
    headers, hdiags = _parse_headers(header_toks, coverage)
    diags.extend(hdiags)
    if type_line is None:
        diags.append(_doc_error("E_DIAGRAM_KIND", "図の種類の行が無い", block.fence_line))
        return None, diags
    kind, why = _diagram_kind(type_line)
    if kind is None:
        coverage.mark(type_line, "error")
        for toks in body:
            coverage.mark(toks, "error")
        diags.append(
            _doc_error("E_UNSUPPORTED_SYNTAX", why, type_line[0].line, type_line[0].column)
        )
        return None, diags
    coverage.mark(type_line, "ir")
    if kind == "activity":
        coverage.mark(type_line[1:], "ignored")  # 向きは見た目だけ
    for key, (_, tok) in headers.items():
        if key not in _HEADER_KEYS[kind]:
            coverage.mark([tok], "error")
            diags.append(
                _doc_error("E_HEADER", f"{kind} の図に宣言 {key} は書けない", tok.line, tok.column)
            )
    for key in sorted(_REQUIRED_KEYS[kind] - headers.keys()):
        diags.append(_doc_error("E_HEADER", f"宣言 `%%@ {key}: …` が必要", block.fence_line))
    diagram_id = DOC_ID
    if "id" in headers:
        value, tok = headers["id"]
        if _valid_diagram_id(value):
            diagram_id = value
        else:
            coverage.mark([tok], "error")
            diags.append(_doc_error("E_HEADER", f"図 ID が不正: {value!r}", tok.line, tok.column))
    ctx = Ctx(diagram_id, coverage)
    result = DiagramResult(diagram_id=diagram_id, kind=kind, span=ctx.at(type_line[0]))
    if "entry" in headers:
        value, tok = headers["entry"]
        name = _single_name(value)
        if name is None or name.count(".") != 1:
            coverage.mark([tok], "error")
            diags.append(
                _doc_error("E_HEADER", f"entry は <クラス名>.<メソッド名>: {value!r}", tok.line)
            )
        else:
            result.entry = name
            result.entry_span = SourceSpan(diagram_id=diagram_id, line=tok.line, column=tok.column)
    if "closed" in headers:
        value, tok = headers["closed"]
        if value not in ("true", "false"):
            coverage.mark([tok], "error")
            diags.append(_doc_error("E_HEADER", "closed は true か false", tok.line, tok.column))
        result.closed = value == "true"
    if "attribute" in headers:
        value, tok = headers["attribute"]
        name = _single_name(value)
        if name is None or name.split(".", 1)[0] in ("before", "after", "outcome"):
            coverage.mark([tok], "error")
            diags.append(
                _doc_error(
                    "E_HEADER",
                    f"attribute は名前空間の無い変数名（例 order.status）: {value!r}",
                    tok.line,
                )
            )
        else:
            result.attribute = name
            result.attribute_span = SourceSpan(
                diagram_id=diagram_id, line=tok.line, column=tok.column
            )
    parser = {
        "class": parse_class_diagram,
        "sequence": parse_sequence,
        "state": parse_state,
        "activity": parse_activity,
    }[kind]
    if len(body) > MAX_STATEMENTS_PER_DIAGRAM:
        for toks in body:
            coverage.mark(toks, "error")
        msg = f"図の文は {MAX_STATEMENTS_PER_DIAGRAM} 行まで（{len(body)} 行ある）"
        diags.append(_doc_error("E_TOO_LARGE", msg, body[MAX_STATEMENTS_PER_DIAGRAM][0].line))
        return result, diags
    parser(ctx, body, result)
    a = result.automaton
    if a is not None and max(len(a.states), len(a.transitions)) > MAX_AUTOMATON_SIZE:
        result.automaton = None
        msg = f"オートマトンの状態・遷移は {MAX_AUTOMATON_SIZE} まで"
        ctx.error("E_TOO_LARGE", msg, type_line[0])
    diags.extend(ctx.diagnostics)
    return result, diags


# ---------------------------------------------------------------- 要求の表


def _parse_requirements(
    tables: list[RequirementTable],
    coverage: Coverage,
    legacy_rule_ids: set[str],
) -> tuple[list[RequirementRow], list[tuple[str, str, Token]], list[SpecDiagnostic]]:
    """行と、解決すべき `図ID#要素ID` の参照（要求 ID, 参照, セル）を返す。"""
    rows: list[RequirementRow] = []
    pending: list[tuple[str, str, Token]] = []
    diags: list[SpecDiagnostic] = []
    seen: set[str] = set()
    for table in tables:
        coverage.mark(table.header, "ignored")
        for cells in table.rows:
            row_diags: list[SpecDiagnostic] = []

            def bad(msg: str, tok: Token, code: str = "E_REQ_TABLE") -> None:
                row_diags.append(_doc_error(code, msg, tok.line, tok.column))  # noqa: B023

            if len(cells) != 4:
                bad(f"要求の表の行は 4 列（{len(cells)} 列ある）", cells[0])
                coverage.mark(cells, "error")
                diags.extend(row_diags)
                continue
            id_c, level_c, text_c, ref_c = cells
            if not id_c.text:
                bad("要求 ID が空", id_c)
            elif id_c.text in seen:
                bad(f"要求 ID {id_c.text} が重複", id_c, "E_DUPLICATE_ID")
            if level_c.text not in ("mandatory", "advisory"):
                bad(f"level は mandatory か advisory: {level_c.text!r}", level_c)
            if not text_c.text:
                bad("要求の本文が空", text_c)
            refs: list[str] = []
            if ref_c.text not in ("", "-"):
                for raw in ref_c.text.split(","):
                    r = raw.strip()
                    if r.startswith("ir:"):
                        if r[3:] not in legacy_rule_ids:
                            bad(f"旧形式の IR に要求 {r[3:]} が無い", ref_c, "E_REF_UNKNOWN")
                    elif r.count("#") == 1 and all(r.split("#")):
                        pending.append((id_c.text, r, ref_c))
                    else:
                        bad(f"形式化は `図ID#要素ID` か `ir:要求ID`: {r!r}", ref_c)
                    refs.append(r)
            if row_diags:
                coverage.mark(cells, "error")
                diags.extend(row_diags)
                continue
            coverage.mark(cells, "ir")
            seen.add(id_c.text)
            level: RuleLevel = "mandatory" if level_c.text == "mandatory" else "advisory"
            rows.append(
                RequirementRow(id=id_c.text, level=level, text=text_c.text, formalized_by=refs)
            )
    return rows, pending, diags


# ---------------------------------------------------------------- 図をまたぐ解決


def _enum_consts(e: Expr) -> list[Const]:
    if isinstance(e, Const):
        return [] if e.type in (BOOL_TYPE, INT_TYPE) else [e]
    if isinstance(e, Not):
        return _enum_consts(e.arg)
    if isinstance(e, Binary):
        return _enum_consts(e.left) + _enum_consts(e.right)
    if isinstance(e, Nary):
        return [c for a in e.args for c in _enum_consts(a)]
    return []


def _err(code: str, message: str, span: SourceSpan | None) -> SpecDiagnostic:
    return SpecDiagnostic(code=code, severity="error", message=message, span=span)


def _level_index(rows: list[RequirementRow]) -> dict[str, set[RuleLevel]]:
    """参照（`図ID#要素ID`）→ それを参照する行の level の集合。義務ごとの全探索を避ける。"""
    index: dict[str, set[RuleLevel]] = {}
    for r in rows:
        for ref in r.formalized_by:
            index.setdefault(ref, set()).add(r.level)
    return index


def _level(element_ids: set[str], diagram_id: str, index: dict[str, set[RuleLevel]]) -> RuleLevel:
    """義務を構成する要素がすべて、advisory の行だけから参照されているときだけ advisory。

    参照の無い要素が 1 つでもあれば mandatory（grammar.md 8 章。一部の要素だけを advisory の行で
    参照して、義務全体を黙って advisory に落とさないため）。
    """
    if not element_ids:
        return "mandatory"
    for e in element_ids:
        if index.get(f"{diagram_id}#{e}", set()) != {"advisory"}:
            return "mandatory"
    return "advisory"


_ATTR_EXPR_TYPES = {"int": INT_TYPE, "bool": BOOL_TYPE, "str": "str", "opaque_id": "opaque_id"}


def class_variable(class_name: str) -> str:
    """クラス図の属性を参照するときの変数名の頭（クラス名の先頭の 1 文字を小文字にしたもの）。"""
    return class_name[:1].lower() + class_name[1:]


def _variables(classes: list[ClassDef], domain: DomainSpec | None) -> dict[str, str]:
    """参照できる変数（名前空間なし）→ 型名。クラス図の属性を先に、domain の変数で補う。"""
    out: dict[str, str] = {}
    for c in classes:
        if c.stereotype == "enumeration":
            continue
        head = class_variable(c.name)
        for a in c.attributes:
            ty = a.enum if a.type == "enum" and a.enum else _ATTR_EXPR_TYPES[a.type]
            out.setdefault(f"{head}.{a.name}", ty)
    if domain is not None:
        for var, ty in domain.variables.items():
            out.setdefault(var, ty)
    return out


def check_domain(compiled: CompiledSpec, domain: DomainSpec | None) -> DomainSpec:
    """検査に使う型環境: 仕様の domain に、クラス図の列挙と属性の変数を足したもの。

    ガードは 13 章の規則でクラス図の属性（`triage.has_findings`）を参照するので、検査器にも
    同じ変数を宣言する（同名はクラス図が優先。compile の解決と同じ）。`str` の属性は検査器の
    型に無いので入れない（ガードで使えば型の検査で UNKNOWN になる）。
    """
    base = domain or DomainSpec()
    enums = dict(base.enums)
    for c in compiled.classes:
        if c.stereotype == "enumeration":
            enums.setdefault(c.name, list(c.enum_values))
    opaque = list(base.opaque_types)
    variables: dict[str, str] = {}
    for c in compiled.classes:
        if c.stereotype != "class":
            continue
        head = class_variable(c.name)
        for a in c.attributes:
            if a.type == "str":
                continue
            ty = a.enum if a.type == "enum" and a.enum else _ATTR_EXPR_TYPES[a.type]
            if ty == "opaque_id" and ty not in opaque:
                opaque.append(ty)
            variables.setdefault(f"{head}.{a.name}", ty)
    for var, ty in base.variables.items():
        variables.setdefault(var, ty)
    return base.model_copy(update={"enums": enums, "opaque_types": opaque, "variables": variables})


def _enum_pairs(e: Expr) -> list[tuple[Ref, Const]]:
    """`参照 ==/!= 列挙値` の組（列挙値がその属性の列挙に属するかを確かめる）。"""
    if isinstance(e, Not):
        return _enum_pairs(e.arg)
    if isinstance(e, Nary):
        return [p for a in e.args for p in _enum_pairs(a)]
    if isinstance(e, Binary):
        pairs = _enum_pairs(e.left) + _enum_pairs(e.right)
        for x, y in ((e.left, e.right), (e.right, e.left)):
            if isinstance(x, Ref) and isinstance(y, Const) and y.type not in (BOOL_TYPE, INT_TYPE):
                pairs.append((x, y))
        return pairs
    return []


def _check_vocabulary(
    results: list[DiagramResult],
    classes: list[ClassDef],
    enums: dict[str, list[str]],
    domain: DomainSpec | None,
) -> list[SpecDiagnostic]:
    """振る舞いの図をクラス図の語彙と突き合わせる（grammar.md 13 章）。"""
    diags: list[SpecDiagnostic] = []
    behavior = [r for r in results if r.kind != "class"]
    if not behavior:
        return diags
    if not any(r.kind == "class" for r in results):
        return [
            _err(
                "E_NO_CLASS_DIAGRAM",
                "シーケンス図・状態遷移図・アクティビティ図を書くときは、語彙のクラス図が必要",
                behavior[0].span,
            )
        ]
    by_name = {c.name: c for c in classes}
    variables = _variables(classes, domain)

    def check_operation(op: str, span: SourceSpan | None) -> None:
        cls, method = op.split(".", 1)
        target = by_name.get(cls)
        if target is None or target.stereotype == "enumeration":
            diags.append(_err("E_UNKNOWN_CLASS", f"クラス {cls} がクラス図に無い", span))
        elif method not in {o.name for o in target.operations}:
            diags.append(_err("E_UNKNOWN_OPERATION", f"操作 {op} がクラス図に無い", span))

    for r in behavior:
        for cls, span in r.class_refs:
            if cls not in by_name or by_name[cls].stereotype == "enumeration":
                diags.append(_err("E_UNKNOWN_CLASS", f"クラス {cls} がクラス図に無い", span))
        if r.entry is not None:
            check_operation(r.entry, r.entry_span)
        for op, span in r.operations:
            check_operation(op, span)
        if r.kind == "state" and r.attribute is not None:
            ty = variables.get(r.attribute)
            if ty is None:
                msg = f"状態の属性 {r.attribute} をクラス図の属性に解決できない"
                diags.append(_err("E_UNKNOWN_REFERENCE", msg, r.attribute_span))
            elif ty not in enums:
                msg = f"状態の属性 {r.attribute} の型 {ty} は列挙でない"
                diags.append(_err("E_STATE_ATTRIBUTE_TYPE", msg, r.attribute_span))
            else:
                extra = [st for st in r.states if st not in enums[ty]]
                if extra:
                    msg = f"状態 {extra} は {r.attribute} の列挙 {ty} の値に無い"
                    diags.append(_err("E_UNKNOWN_STATE", msg, r.attribute_span))

    try:
        env = TypeEnv(
            DomainSpec(
                enums=enums,
                opaque_types=sorted({*(domain.opaque_types if domain else []), "opaque_id"}),
            ),
            extra_variables=variables,
        )
    except ValueError:
        return diags  # 列挙の定義の誤りは別の診断で報告済み
    for r in behavior:
        for guard, span in r.guards:
            bad = False
            for rf in refs_in(guard):
                known = (
                    rf.variable in OUTCOME_VARIABLES
                    if rf.namespace == "outcome"
                    else rf.variable in variables
                )
                if not known:
                    msg = f"ガードの参照 {rf.path} を属性・domain の変数に解決できない"
                    diags.append(_err("E_UNKNOWN_REFERENCE", msg, span))
                    bad = True
            for lit in _enum_consts(guard):
                if lit.type not in enums or lit.value not in enums[lit.type]:
                    msg = f"列挙値 {lit.type}.{lit.value} が未定義"
                    diags.append(_err("E_UNKNOWN_ENUM", msg, span))
                    bad = True
            if bad:
                continue
            for rf, lit in _enum_pairs(guard):
                ty = env.type_of_ref(rf)
                if ty != lit.type:
                    msg = f"列挙値 {lit.type}.{lit.value} は {rf.path}（型 {ty}）の列挙に無い"
                    diags.append(_err("E_UNKNOWN_ENUM", msg, span))
                    bad = True
            if bad:
                continue
            try:
                require_boolean(env, guard, "guard")
            except IRTypeError as e:
                diags.append(_err("E_GUARD_TYPE", str(e), span))
    return diags


def compile_with_coverage(
    markdown: str, *, domain: DomainSpec | None, legacy_ir: list[SpecIR]
) -> tuple[ParseResult, Coverage]:
    coverage = Coverage()
    if len(markdown.encode("utf-8")) > MAX_BODY_BYTES:
        msg = f"仕様の本文が上限 {MAX_BODY_BYTES} バイトを超える"
        return ParseResult(compiled=None, diagnostics=[_doc_error("E_TOO_LARGE", msg)]), coverage
    # 改行を CommonMark と同じく \n にそろえる（行番号を markdown-it と合わせる）
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]  # 先頭の BOM を 1 つ除く（行番号は変わらない）
    long_lines = [i + 1 for i, line in enumerate(text.split("\n")) if len(line) > MAX_LINE_CHARS]
    if long_lines:
        too_long = [
            _doc_error("E_LINE_TOO_LONG", f"1 行は {MAX_LINE_CHARS} 文字まで", n)
            for n in long_lines[:20]
        ]
        return ParseResult(compiled=None, diagnostics=too_long), coverage

    diags: list[SpecDiagnostic] = []
    parts = split_markdown(text)
    diags.extend(_doc_error(e.code, e.message, e.line, e.column) for e in parts.errors)
    diags.extend(
        SpecDiagnostic(
            code=w.code,
            severity="warning",
            message=w.message,
            span=SourceSpan(diagram_id=DOC_ID, line=w.line, column=w.column),
        )
        for w in parts.warnings
    )
    coverage.add(parts.table_tokens)

    if len(parts.blocks) > MAX_DIAGRAMS:
        block = parts.blocks[MAX_DIAGRAMS]
        msg = f"図は {MAX_DIAGRAMS} 個まで（{len(parts.blocks)} 個ある）"
        return ParseResult(
            compiled=None, diagnostics=[_doc_error("E_TOO_LARGE", msg, block.fence_line)]
        ), coverage
    results: list[DiagramResult] = []
    for block in parts.blocks:
        result, bdiags = _compile_block(block, coverage)
        diags.extend(bdiags)
        if result is not None:
            results.append(result)
        if len(coverage.tokens) > MAX_TOKENS:
            msg = f"トークンは {MAX_TOKENS} 個まで"
            diags.append(_doc_error("E_TOO_LARGE", msg, block.fence_line))
            return ParseResult(compiled=None, diagnostics=diags), coverage

    by_id: dict[str, DiagramResult] = {}
    for r in results:
        if r.diagram_id == DOC_ID:
            continue
        if r.diagram_id in by_id:
            diags.append(_err("E_DUPLICATE_ID", f"図 ID {r.diagram_id} が重複", r.span))
        by_id[r.diagram_id] = r

    legacy_rule_ids = {rule.id for ir in legacy_ir for rule in ir.rules}
    rows, pending, rdiags = _parse_requirements(parts.tables, coverage, legacy_rule_ids)
    level_index = _level_index(rows)
    diags.extend(rdiags)
    for req_id, ref, cell in pending:
        diagram_id, element_id = ref.split("#")
        target = by_id.get(diagram_id)
        if target is None or element_id not in target.referenceable:
            coverage.mark([cell], "error")
            what = "図" if target is None else "参照できる要素"
            diags.append(
                _doc_error(
                    "E_REF_UNKNOWN", f"要求 {req_id} の {ref}: {what}が無い", cell.line, cell.column
                )
            )

    # 列挙（クラス図の <<enumeration>> と domain.enums）
    enums: dict[str, list[str]] = dict(domain.enums) if domain else {}
    classes: list[ClassDef] = []
    class_seen: dict[str, SourceSpan] = {}
    raw_classes = [(r, c) for r in results for c in r.classes]
    for _r, c in raw_classes:
        if c.name in class_seen:
            diags.append(_err("E_DUPLICATE_ID", f"クラス {c.name} が複数の図にある", c.span))
        class_seen[c.name] = c.span
        if c.stereotype == "enumeration":
            if (
                domain
                and c.name in domain.enums
                and set(domain.enums[c.name]) != set(c.enum_values)
            ):
                diags.append(
                    _err("E_ENUM_MISMATCH", f"列挙 {c.name} の値が domain と一致しない", c.span)
                )
            enums.setdefault(c.name, c.enum_values)
    structure: list[StructureObligation] = []
    for r, c in raw_classes:
        attrs: list[ClassAttribute] = []
        for a in c.attributes:
            if a.type_name in _BUILTIN_ATTR_TYPES:
                attrs.append(ClassAttribute(name=a.name, type=a.type_name))
            elif a.type_name in enums:
                attrs.append(ClassAttribute(name=a.name, type="enum", enum=a.type_name))
            else:
                diags.append(_err("E_UNKNOWN_TYPE", f"型 {a.type_name} が未定義", a.span))
        classes.append(
            ClassDef(
                id=c.name,
                name=c.name,
                stereotype=c.stereotype,
                attributes=attrs,
                operations=c.operations,
                enum_values=c.enum_values,
                span=c.span,
            )
        )
        if c.operations:
            structure.append(
                StructureObligation(
                    id=f"{r.diagram_id}#{c.name}",
                    class_name=c.name,
                    operations=c.operations,
                    level=_level({c.name}, r.diagram_id, level_index),
                )
            )

    diags.extend(_check_vocabulary(results, classes, enums, domain))

    # 旧形式
    if domain is not None:
        for ir in legacy_ir:
            for d in validate_spec_ir(ir, domain).diagnostics:
                code = f"{d.code[:2]}LEGACY_{d.code[2:]}"
                where = f"{ir.behavior_id}/{d.rule_id or '-'}"
                diags.append(
                    SpecDiagnostic(code=code, severity=d.severity, message=f"{where}: {d.message}")
                )

    for tok in coverage.unclassified():
        diags.append(
            _doc_error(
                "E_UNCONSUMED_TOKEN", f"分類されないトークン {tok.text!r}", tok.line, tok.column
            )
        )

    if any(d.severity == "error" for d in diags):
        return ParseResult(compiled=None, diagnostics=diags), coverage

    relations = [
        RelationObligation(
            id=f"{r.diagram_id}#{rel.element_id or f'{rel.source}-{rel.kind}-{rel.target}'}",
            element_id=rel.element_id,
            kind=rel.kind,
            source=rel.source,
            target=rel.target,
            level=(
                _level({rel.element_id}, r.diagram_id, level_index)
                if rel.element_id is not None
                else "mandatory"
            ),
            span=rel.span,
        )
        for r in results
        for rel in r.relations
    ]
    traces: list[TraceObligation] = []
    machines: list[StateMachine] = []
    for r in results:
        level = _level(r.referenceable, r.diagram_id, level_index)
        if r.kind in ("sequence", "activity"):
            assert r.entry is not None and r.automaton is not None
            traces.append(
                TraceObligation(
                    id=f"{r.diagram_id}#{r.entry}",
                    diagram_kind=r.kind,
                    entry_operation=r.entry,
                    mode="contains_subsequence" if r.kind == "sequence" else "included_in",
                    closed=r.closed,
                    automaton=r.automaton,
                    level=level,
                    spans=[r.span],
                )
            )
        elif r.kind == "state":
            assert r.attribute is not None and r.initial is not None
            machines.append(
                StateMachine(
                    id=r.diagram_id,
                    attribute=r.attribute,
                    states=r.states,
                    initial=r.initial,
                    transitions=r.state_transitions,
                    level=level,
                )
            )
    draft = CompiledSpec(
        parser_version=PARSER_VERSION,
        source_hash=content_hash(markdown),
        ir_hash=_PLACEHOLDER_HASH,
        classes=classes,
        structure=structure,
        relations=relations,
        traces=traces,
        state_machines=machines,
        legacy=list(legacy_ir),
        requirements=rows,
    )
    compiled = draft.model_copy(update={"ir_hash": ir_hash_of(draft)})
    return ParseResult(compiled=compiled, diagnostics=diags), coverage


def compile_spec(
    markdown: str, *, domain: DomainSpec | None, legacy_ir: list[SpecIR]
) -> ParseResult:
    """仕様の本文と旧形式の IR を `CompiledSpec` にする。エラーがあれば compiled は None。"""
    return compile_with_coverage(markdown, domain=domain, legacy_ir=legacy_ir)[0]


__all__ = [
    "PARSER_VERSION",
    "compile_spec",
    "compile_with_coverage",
    "ir_hash_of",
    "semantic_dump",
]
