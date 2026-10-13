"""会話の提案の許可リストと診断（S73）。

- 提案できる欄は種類ごとの許可リストだけ。scope・target・domain・legacy_ir・compiled・
  承認の情報は応答の型（`response_schema`）にも、返す提案（`raw_proposal`）にも入れない。
  余分な欄は捨て、捨てた件数だけを診断に出す（欄の名前は出さない）
- 名前（`name`）は新規作成の会話（まだ知識に結び付かない。規約を除く）だけの許可リストの欄で、
  名前の規則に合うときだけ返す。画面は名前の欄が空のときだけ入れる。作成時の検査は別に行う
- 提案は返す前に診断する: 仕様は `compile_spec`（スレッドで）、スキル・指示書は
  `domain/knowledge.py` の型、規約は `PolicyRule` と検査器の一覧（`validate_package`）。
  型に合わない提案も `raw_proposal` と診断で返す（`ArtifactContent` にはしない）
- この診断は案内にすぎない。保存・承認の検証は既存の経路が独立に行う
- 返答には任意で、次に決めることの質問（`questions`、選択肢つき）を添えられる。
  サーバで個数・長さを検証し、空や上限を超えるものは捨てる（`extract_questions`）
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from pydantic import ValidationError

from speclino.domain.domain_spec import DomainSpec
from speclino.domain.knowledge import (
    ARTIFACT_NAME_MAX_CHARS,
    ARTIFACT_NAME_PATTERN,
    InstructionsContent,
    SkillContent,
)
from speclino.domain.knowledge_chat import (
    CHAT_OPTION_MAX_CHARS,
    CHAT_OPTIONS_MAX,
    CHAT_QUESTION_MAX_CHARS,
    CHAT_QUESTIONS_MAX,
    ChatProposal,
    ChatQuestion,
    KnowledgeChatKind,
    ProposalDiagnostic,
)
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
_NAME = re.compile(ARTIFACT_NAME_PATTERN)


def draft_text_field(kind: KnowledgeChatKind) -> str | None:
    """途中経過として本文を見せる提案の欄（`proposal.<欄>`）。規約の提案には無い。"""
    fields = _TEXT_FIELDS.get(kind)
    return fields[0] if fields else None


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


def _proposal_schema(kind: KnowledgeChatKind, *, propose_name: bool) -> dict[str, Any]:
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
    if propose_name and kind != "policy":
        props["name"] = {
            "type": "string",
            "description": "新規作成時の名前の案（英小文字・数字・ハイフン、64 文字以内）",
        }
    # 提案を出すなら本文の欄は必ず書かせる（名前だけの提案を出させない）
    body = _TEXT_FIELDS.get(kind, ("rule",))[0]
    return {"type": "object", "properties": props, "required": [body]}


def response_schema(kind: KnowledgeChatKind, *, propose_name: bool = False) -> dict[str, Any]:
    """AI の返答の JSON Schema（説明の文と、任意の質問と提案）。

    キーの順（explanation → questions → proposal）が生成の順になる。ストリームで説明を先に
    見せるため崩さない。`propose_name` は新規作成の会話のとき（提案に名前の案を足す）。
    """
    return {
        "type": "object",
        "properties": {
            "explanation": {"type": "string", "description": "利用者への説明（日本語）"},
            "questions": _QUESTIONS_SCHEMA,
            "proposal": _proposal_schema(kind, propose_name=propose_name),
        },
        "required": ["explanation"],
    }


_QUESTIONS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "maxItems": CHAT_QUESTIONS_MAX,
    "description": "利用者に決めてほしいことの質問（1〜3 問）。無ければ省く",
    "items": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "質問の文（短く、1 文）"},
            "options": {
                "type": "array",
                "maxItems": CHAT_OPTIONS_MAX,
                "items": {"type": "string"},
                "description": "答えの選択肢 2〜4 個（それぞれ短い文。そのまま利用者の発言になる）",
            },
        },
        "required": ["question", "options"],
    },
}


def _short(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text if 0 < len(text) <= limit else None


def extract_questions(data: Any) -> list[ChatQuestion]:
    """返答の `questions` を検証して取り出す。空・長すぎる質問と選択肢は捨て、重複した選択肢は
    1 つにする。質問は 3 問まで、選択肢は 4 個まで。選択肢が 2 個に満たなければ選択肢なしにする。
    """
    if not isinstance(data, list):
        return []
    out: list[ChatQuestion] = []
    for item in data:
        if len(out) >= CHAT_QUESTIONS_MAX:
            break
        if not isinstance(item, dict):
            continue
        question = _short(item.get("question"), CHAT_QUESTION_MAX_CHARS)
        if question is None:
            continue
        raw_options = item.get("options")
        options: list[str] = []
        for o in raw_options if isinstance(raw_options, list) else []:
            option = _short(o, CHAT_OPTION_MAX_CHARS)
            if option is not None and option not in options:
                options.append(option)
        options = options[:CHAT_OPTIONS_MAX]
        out.append(ChatQuestion(question=question, options=options if len(options) >= 2 else []))
    return out


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


def extract_proposal(
    kind: KnowledgeChatKind, data: Any, *, propose_name: bool = False
) -> tuple[dict[str, Any] | None, int]:
    """許可リストの欄だけを取り出す。(提案、捨てた欄の数)。提案が無ければ None。

    名前は `propose_name` のとき（新規作成）だけ、規則に合えば本文の提案に添えて返す。
    名前だけの提案は返さない。
    """
    if not isinstance(data, dict):
        return None, 0
    if kind == "policy":
        rule, dropped = _extract_rule(data.get("rule"))
        dropped += sum(1 for k in data if k != "rule")
        return ({"rule": rule} if rule is not None else None), dropped
    fields = _TEXT_FIELDS[kind] + (("files",) if kind == "skill" else ())
    out: dict[str, Any] = {k: data[k] for k in _TEXT_FIELDS[kind] if isinstance(data.get(k), str)}
    dropped = sum(1 for k in data if k not in fields and k != "name")
    name = data.get("name")
    if name is not None:
        if propose_name and _valid_name(name):
            out["name"] = name
        else:
            dropped += 1
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
    if not any(k in out for k in _TEXT_FIELDS[kind]):
        return None, dropped
    return out, dropped


def _valid_name(name: Any) -> bool:
    return (
        isinstance(name, str)
        and len(name) <= ARTIFACT_NAME_MAX_CHARS
        and _NAME.fullmatch(name) is not None  # match は `$` が末尾の改行を許す
    )


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
    body = {k: v for k, v in raw.items() if k != "name"}
    try:
        if kind == "skill":
            SkillContent.model_validate({"kind": "skill", **body, "scope": scope})
        elif kind == "instructions":
            InstructionsContent.model_validate({"kind": "instructions", **body, "scope": scope})
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
    propose_name: bool = False,
) -> ChatProposal | None:
    """LLM の `proposal` から返す提案を作る。提案が無ければ None。"""
    raw, dropped = extract_proposal(kind, data, propose_name=propose_name)
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


__all__ = [
    "build_proposal",
    "draft_text_field",
    "extract_proposal",
    "extract_questions",
    "response_schema",
    "text_hash",
]
