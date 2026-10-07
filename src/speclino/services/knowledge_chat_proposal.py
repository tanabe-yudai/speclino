"""会話の提案の許可リストと診断（S73、design/2026-10-06-knowledge-chat.md C4・C5）。

- 提案できる欄は種類ごとの許可リストだけ。名前・scope・target・domain・legacy_ir・compiled・
  承認の情報は応答の型（`response_schema`）にも、返す提案（`raw_proposal`）にも入れない。
  余分な欄は捨て、捨てた件数だけを診断に出す（欄の名前は出さない）
- 提案は返す前に診断する: 仕様は `compile_spec`（スレッドで）、スキル・指示書は
  `domain/knowledge.py` の型、規約は `PolicyRule` と検査器の一覧（`validate_package`）。
  型に合わない提案も `raw_proposal` と診断で返す（`ArtifactContent` にはしない）
- この診断は案内にすぎない。保存・承認の検証は既存の経路が独立に行う
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from pydantic import ValidationError

from speclino.domain.domain_spec import DomainSpec
from speclino.domain.knowledge import InstructionsContent, SkillContent
from speclino.domain.knowledge_chat import ChatProposal, KnowledgeChatKind, ProposalDiagnostic
from speclino.domain.policy import PolicyPackage, PolicyRule
from speclino.domain.spec_ir import SpecIR
from speclino.services.policy_governance import InvalidPackage, validate_package
from speclino.spec.uml import compile_spec

MAX_DIAGNOSTICS = 50
_TEXT_FIELDS: dict[KnowledgeChatKind, tuple[str, ...]] = {
    "spec": ("markdown",),
    "instructions": ("body",),
    "skill": ("skill_md",),
}
_RULE_TEXT = ("id", "title", "text")
_LEVELS = ("mandatory", "advisory")
_TIERS = ("deterministic", "llm_assisted")
# 規約の提案に無い必須欄（編集中のルールにも無いとき）。承認時の検証は既存どおり独立に行う
_RULE_DEFAULTS: dict[str, Any] = {
    "version": "1",
    "on_missing_input": "unknown",
    "applies_to": {"paths": [], "languages": []},
}


def text_hash(text: str) -> str:
    """編集欄の本文のハッシュ（提案を土台に結び付ける）。"""
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _string_list(schema_desc: str) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "description": schema_desc}


def _proposal_schema(kind: KnowledgeChatKind) -> dict[str, Any]:
    if kind == "spec":
        props: dict[str, Any] = {"markdown": {"type": "string", "description": "仕様の本文の全文"}}
    elif kind == "instructions":
        props = {"body": {"type": "string", "description": "指示書の本文の全文"}}
    elif kind == "skill":
        props = {
            "skill_md": {"type": "string", "description": "SKILL.md の全文（frontmatter を含む）"},
            "files": {
                "type": "array",
                "description": "付属ファイル（相対パスと本文）。全体を置き換える",
                "items": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "body": {"type": "string"}},
                    "required": ["path", "body"],
                },
            },
        }
    else:
        props = {
            "rule": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "level": {"type": "string", "enum": list(_LEVELS)},
                    "tier": {"type": "string", "enum": list(_TIERS)},
                    "verifier": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "version": {"type": "string"},
                            "params_json": {
                                "type": "string",
                                "description": "検査器の params の JSON（オブジェクト）",
                            },
                        },
                        "required": ["id", "version"],
                    },
                    "applies_to": {
                        "type": "object",
                        "properties": {
                            "paths": _string_list("glob パターン"),
                            "languages": _string_list("言語"),
                        },
                    },
                },
                "required": ["id", "title", "text", "level"],
            }
        }
    return {"type": "object", "properties": props}


def response_schema(kind: KnowledgeChatKind) -> dict[str, Any]:
    """AI の返答の JSON Schema（説明の文と、任意の提案）。

    キーの順（explanation → proposal）が生成の順になる。ストリームで説明を先に見せるため崩さない。
    """
    return {
        "type": "object",
        "properties": {
            "explanation": {"type": "string", "description": "利用者への説明（日本語）"},
            "proposal": _proposal_schema(kind),
        },
        "required": ["explanation"],
    }


def _strings(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return list(value)


def _extract_rule(rule: Any) -> tuple[dict[str, Any] | None, int]:
    if not isinstance(rule, dict):
        return None, 0
    allowed = {*_RULE_TEXT, "level", "tier", "verifier", "applies_to"}
    out: dict[str, Any] = {}
    dropped = sum(1 for k in rule if k not in allowed)
    for key in _RULE_TEXT:
        if isinstance(rule.get(key), str):
            out[key] = rule[key]
    if rule.get("level") in _LEVELS:
        out["level"] = rule["level"]
    if rule.get("tier") in _TIERS:
        out["tier"] = rule["tier"]
    verifier = rule.get("verifier")
    if isinstance(verifier, dict) and isinstance(verifier.get("id"), str):
        dropped += sum(1 for k in verifier if k not in ("id", "version", "params_json", "params"))
        params: Any = verifier.get("params", {})
        raw_params = verifier.get("params_json")
        if isinstance(raw_params, str):
            try:
                params = json.loads(raw_params) if raw_params.strip() else {}
            except json.JSONDecodeError:
                params = None
        out["verifier"] = {
            "id": verifier["id"],
            "version": verifier.get("version") if isinstance(verifier.get("version"), str) else "",
            "params": params if isinstance(params, dict) else {},
        }
    applies = rule.get("applies_to")
    if isinstance(applies, dict):
        dropped += sum(1 for k in applies if k not in ("paths", "languages"))
        out["applies_to"] = {
            "paths": _strings(applies.get("paths")) or [],
            "languages": _strings(applies.get("languages")) or [],
        }
    return (out or None), dropped


def extract_proposal(kind: KnowledgeChatKind, data: Any) -> tuple[dict[str, Any] | None, int]:
    """許可リストの欄だけを取り出す。(提案、捨てた欄の数)。提案が無ければ None。"""
    if not isinstance(data, dict):
        return None, 0
    if kind == "policy":
        rule, dropped = _extract_rule(data.get("rule"))
        dropped += sum(1 for k in data if k != "rule")
        return ({"rule": rule} if rule is not None else None), dropped
    fields = _TEXT_FIELDS[kind] + (("files",) if kind == "skill" else ())
    out: dict[str, Any] = {k: data[k] for k in _TEXT_FIELDS[kind] if isinstance(data.get(k), str)}
    dropped = sum(1 for k in data if k not in fields)
    if kind == "skill" and isinstance(data.get("files"), list):
        files: dict[str, str] = {}
        for item in data["files"]:
            if (
                isinstance(item, dict)
                and isinstance(item.get("path"), str)
                and isinstance(item.get("body"), str)
            ):
                files[item["path"]] = item["body"]
        out["files"] = files
    if kind == "skill" and "skill_md" not in out:
        return None, dropped
    return (out or None), dropped


def _from_validation(e: ValidationError) -> list[ProposalDiagnostic]:
    return [
        ProposalDiagnostic(
            severity="error",
            code="E_INVALID",
            message=f"{'.'.join(str(p) for p in err['loc']) or '(全体)'}: {err['msg']}",
        )
        for err in e.errors(include_input=False, include_url=False)
    ]


def _base_rule(editor_text: str) -> dict[str, Any]:
    try:
        data = json.loads(editor_text) if editor_text.strip() else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _diagnose_policy(raw: dict[str, Any], editor_text: str) -> list[ProposalDiagnostic]:
    merged = {**_RULE_DEFAULTS, **_base_rule(editor_text), **raw["rule"]}
    try:
        rule = PolicyRule.model_validate(merged)
        validate_package(
            PolicyPackage(id="chat", version="1", status="draft", owner="chat", rules=[rule])
        )
    except ValidationError as e:
        return _from_validation(e)
    except InvalidPackage as e:
        return [ProposalDiagnostic(severity="error", code="E_POLICY", message=str(e))]
    return []


async def _diagnose(
    kind: KnowledgeChatKind,
    raw: dict[str, Any],
    *,
    editor_text: str,
    spec_domain: DomainSpec | None,
    spec_legacy: list[SpecIR],
) -> list[ProposalDiagnostic]:
    if kind == "spec":
        # compile_spec は CPU を使う同期処理。イベントループを止めないようスレッドで動かす
        result = await asyncio.to_thread(
            compile_spec, raw["markdown"], domain=spec_domain, legacy_ir=spec_legacy
        )
        return [
            ProposalDiagnostic(
                severity=d.severity,
                code=d.code,
                message=(f"{d.span.line} 行 {d.span.column} 列: " if d.span else "") + d.message,
            )
            for d in result.diagnostics
        ]
    scope = {"workspace": True}
    try:
        if kind == "skill":
            SkillContent.model_validate({"kind": "skill", **raw, "scope": scope})
        elif kind == "instructions":
            InstructionsContent.model_validate(
                {"kind": "instructions", "target": "agents_md", **raw, "scope": scope}
            )
    except ValidationError as e:
        return _from_validation(e)
    if kind == "policy":
        return _diagnose_policy(raw, editor_text)
    return []


async def build_proposal(
    kind: KnowledgeChatKind,
    data: Any,
    *,
    editor_text: str,
    base_hash: str,
    spec_domain: DomainSpec | None = None,
    spec_legacy: list[SpecIR] | None = None,
) -> ChatProposal | None:
    """LLM の `proposal` から返す提案を作る。提案が無ければ None。"""
    raw, dropped = extract_proposal(kind, data)
    if raw is None:
        return None
    diagnostics = await _diagnose(
        kind, raw, editor_text=editor_text, spec_domain=spec_domain, spec_legacy=spec_legacy or []
    )
    shown = diagnostics[:MAX_DIAGNOSTICS]
    if len(diagnostics) > len(shown):
        shown.append(
            ProposalDiagnostic(
                severity="info",
                code="I_MORE",
                message=f"ほか {len(diagnostics) - len(shown)} 件",
            )
        )
    if dropped:
        shown.append(
            ProposalDiagnostic(
                severity="info",
                code="I_FIELDS_DROPPED",
                message=f"提案できない欄 {dropped} 件を捨てました（名前・スコープは人が決める）",
            )
        )
    return ChatProposal(
        base_hash=base_hash,
        raw_proposal=raw,
        diagnostics=shown,
        valid=not any(d.severity == "error" for d in diagnostics),
    )


__all__ = ["build_proposal", "extract_proposal", "response_schema", "text_hash"]
