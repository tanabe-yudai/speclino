"""spec_extractor（S33 / PoC 7 章）: 仕様 Markdown から Spec IR の **候補** を生成する。

LLM は候補を出すだけで承認はしない（approval_ref=None、approvals=[]）。曖昧な表現は
確定させず、`ambiguities` として返させて Diagnostic（W_AMBIGUOUS）に残す。語彙は
domain / bindings に宣言されたものだけを使わせ、外れたものは spec_validator が
E_REF_UNDEFINED 等で弾く（候補が検証を通らなければ承認の対象にならない）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from speclino.domain.bindings import Bindings
from speclino.domain.common import ClaimScope, SourceRef
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.expr import Expr
from speclino.domain.spec_ir import Assertion, Modality, Rule, RuleLevel, SpecIR
from speclino.ports.text_llm import TextLlm
from speclino.spec.diagnostics import Diagnostic, Severity, ValidationReport
from speclino.spec.loader import content_hash
from speclino.spec.spec_validator import validate_spec_ir

CANDIDATE_SNAPSHOT_ID = "candidate"
_RULES = """\
あなたは仕様書から検査可能な要求（Spec IR の Rule）を抽出する。判断や承認はしない。
規則:
- 変数参照は before.<変数> / after.<変数> / outcome.status だけ。語彙に無い名前を作らない
- 列挙の値は語彙に宣言されたものだけ。数値は integer
- 呼出し回数の要求は event_requirement
  （event_kind=call, phase=invoked, operation_or_target=操作 ID, count={operator, value}）
- 状態や応答の要求は expression（after.x == 値、outcome.status == 数値）
- 1 つの ### 要求 ID から複数の Rule を作るときは id に接尾辞を付ける（例 R-X-01, R-X-01b）
- 「適切に」「速やかに」「必要に応じて」のような曖昧語は確定させず ambiguities に入れる
- 語彙で表せない要求は unextracted に理由つきで入れる（無理に近似しない）
"""


class RuleProposal(BaseModel):
    """LLM が返す 1 要求分の候補。Rule から承認・出典・テストを除いたもの。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    requirement_id: str = Field(min_length=1, description="Markdown 上の要求 ID（### 見出し）")
    title: str | None = None
    modality: Modality
    level: RuleLevel = "mandatory"
    claim_scope: ClaimScope
    applicability: Expr
    assertion: Assertion
    rationale: str | None = Field(default=None, description="本文のどの記述から抽出したか")


class Ambiguity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str | None = None
    phrase: str = Field(min_length=1, description="曖昧な語句（本文の引用）")
    question: str = Field(min_length=1, description="確定させるために人に聞くこと")


class Unextracted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[RuleProposal] = Field(default_factory=list)
    unextracted: list[Unextracted] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)


@dataclass
class ExtractionResult:
    spec: SpecIR
    report: ValidationReport
    diagnostics: list[Diagnostic] = field(default_factory=list)  # 検証以外（曖昧・不正な候補）
    raw: str = ""

    @property
    def errors(self) -> list[Diagnostic]:
        return [*self.report.errors, *[d for d in self.diagnostics if d.severity == "error"]]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [*self.report.warnings, *[d for d in self.diagnostics if d.severity == "warning"]]


def build_prompt(markdown: str, behavior_id: str, domain: DomainSpec, bindings: Bindings) -> str:
    behavior = bindings.behaviors.get(behavior_id)
    vocab = {
        "behavior_id": behavior_id,
        "entrypoint": behavior.entrypoint if behavior else None,
        "variables": domain.variables,
        "enums": domain.enums,
        "opaque_types": domain.opaque_types,
        "operations": {
            op_id: {"claim_scope": ob.claim_scope, "arguments": sorted(ob.argument_bindings)}
            for op_id, ob in bindings.operations.items()
        },
        "outcome": {"outcome.status": "integer (HTTP status)", "outcome.kind": "OutcomeKind"},
    }
    return "\n".join(
        [
            _RULES,
            "語彙（JSON）:",
            json.dumps(vocab, ensure_ascii=False, indent=2),
            "",
            "仕様 Markdown:",
            "<<<",
            markdown,
            ">>>",
        ]
    )


def parse_proposal(
    text: str,
    behavior_id: str,
    markdown: str,
    domain: DomainSpec,
    bindings: Bindings,
    spec_path: str | None = None,
) -> ExtractionResult:
    """LLM の出力（JSON 文字列）を Spec IR 候補にし、検証して Diagnostic を付ける。"""
    extra: list[Diagnostic] = []
    try:
        proposal = Proposal.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as e:
        extra.append(_diag("E_CANDIDATE_INVALID", "error", None, "proposal", str(e)[:500]))
        empty = SpecIR(behavior_id=behavior_id, spec_content_hash=content_hash(markdown))
        return ExtractionResult(
            empty, validate_spec_ir(empty, domain, bindings, markdown), extra, text
        )

    rules: list[Rule] = []
    for rp in proposal.rules:
        try:
            rules.append(
                Rule(
                    id=rp.id,
                    behavior_id=behavior_id,
                    title=rp.title,
                    source_refs=[
                        SourceRef(
                            snapshot_id=CANDIDATE_SNAPSHOT_ID,
                            requirement_id=rp.requirement_id,
                            path=spec_path,
                        )
                    ],
                    approval_ref=None,
                    modality=rp.modality,
                    level=rp.level,
                    claim_scope=rp.claim_scope,
                    applicability=rp.applicability,
                    assertion=rp.assertion,
                )
            )
        except ValidationError as e:
            extra.append(_diag("E_CANDIDATE_INVALID", "error", rp.id, "rules", str(e)[:500]))
    for a in proposal.ambiguities:
        extra.append(
            _diag(
                "W_AMBIGUOUS",
                "warning",
                a.requirement_id,
                "markdown",
                f"「{a.phrase}」は確定できない: {a.question}",
            )
        )
    for u in proposal.unextracted:
        extra.append(_diag("W_UNEXTRACTED", "warning", u.requirement_id, "markdown", u.reason))
    spec = SpecIR(
        behavior_id=behavior_id,
        spec_snapshot_id=CANDIDATE_SNAPSHOT_ID,
        spec_content_hash=content_hash(markdown),
        rules=rules,
        approvals=[],
        unextracted=[u.requirement_id for u in proposal.unextracted],
    )
    report = validate_spec_ir(spec, domain, bindings, markdown)
    return ExtractionResult(spec, report, extra, text)


async def extract_spec(
    markdown: str,
    behavior_id: str,
    domain: DomainSpec,
    bindings: Bindings,
    llm: TextLlm,
    spec_path: str | None = None,
) -> ExtractionResult:
    prompt = build_prompt(markdown, behavior_id, domain, bindings)
    text = await llm.generate_json(prompt, proposal_schema())
    return parse_proposal(text, behavior_id, markdown, domain, bindings, spec_path)


def proposal_schema() -> dict[str, Any]:
    return Proposal.model_json_schema()


def _diag(
    code: str, severity: Severity, rule_id: str | None, path: str, message: str
) -> Diagnostic:
    return Diagnostic(code=code, severity=severity, rule_id=rule_id, path=path, message=message)


__all__ = [
    "CANDIDATE_SNAPSHOT_ID",
    "ExtractionResult",
    "Proposal",
    "RuleProposal",
    "build_prompt",
    "extract_spec",
    "parse_proposal",
    "proposal_schema",
]
