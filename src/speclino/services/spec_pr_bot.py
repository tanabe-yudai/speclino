"""仕様 PR bot（S46）: 仕様 Markdown を変える PR に、Spec IR の候補と診断をコメントする。

worker が run のスナップショット（base / head）から変更された `specs/**/*.md` を見つけ、
`spec_extractor.extract_spec`（`speclino spec-extract` と同じ処理）で候補を作る。PR には
候補・診断（W_AMBIGUOUS / W_UNEXTRACTED / E_REF_UNDEFINED 等）・既存 IR との差分を、目印
（`SPEC_BOT_MARKER`）つきの 1 件のコメントとして出し、push のたびに積み上げず更新する。

この bot は **提案だけ** をする。IR をリポジトリに書かない・承認しない・ゲート判定と
Check Run に影響しない。承認は従来どおり、人が候補を `specs/ir/` に反映してコミットし、
承認者が `/speclino approve-spec sha256:...` する（またはマージする）ことで行う。

Gemini の呼出しは費用ガード（`CostGuard`）の対象。枠が無い・Gemini が使えないときは
黙って失敗せず、候補を作れなかった理由をコメントに書く。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from speclino.domain.bindings import Bindings
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.spec_ir import Rule, SpecIR
from speclino.ports.text_llm import TextLlm
from speclino.services.cost_guard import CostGuard
from speclino.spec import content_hash, load_bindings, load_domain, load_spec_ir
from speclino.spec.spec_extractor import ExtractionResult, build_prompt, extract_spec

SPEC_BOT_MARKER = "<!-- speclino:spec-bot -->"
# run_events の type。同じ入力で Gemini を呼び直さないための記録にも使う
SPEC_BOT_EVENT = "spec_bot"
_COMMENT_LIMIT = 60000  # GitHub のコメント上限（65536）に対する余裕
# 1 つの仕様 Markdown の上限（文字数）。1 回の呼出しの入力費用を有限にする
MAX_SPEC_CHARS = 30000
_REQUIREMENT_HEADING = re.compile(r"^###\s+(\S+)", re.MULTILINE)
# 「解釈」を表すフィールド。出典（source_refs）・承認・タイトル・テストは候補と既存で
# 必ず違うので、差分には使わない
_INTERPRETATION_FIELDS = ("modality", "level", "claim_scope", "applicability", "assertion")

CandidateStatus = Literal["added", "changed", "removed", "unchanged"]
_STATUS_LABELS: dict[CandidateStatus, str] = {
    "added": "追加",
    "changed": "変更",
    "removed": "削除",
    "unchanged": "変更なし",
}


@dataclass(frozen=True)
class SpecTarget:
    """候補を作る 1 つの仕様 Markdown。"""

    path: str  # リポジトリ相対（例 specs/orders-cancel.md）
    markdown: str
    behavior_id: str | None
    existing_path: str | None  # 対応する head の Spec IR（無ければ None）
    existing_rules: list[Rule] = field(default_factory=list)
    skip_reason: str | None = None  # behavior を決められない等。LLM を呼ばない


@dataclass(frozen=True)
class SpecBotInput:
    targets: list[SpecTarget]
    domain: DomainSpec | None
    bindings: Bindings | None
    input_hash: str  # 同じ入力で Gemini を呼び直さないための鍵
    error: str | None = None  # domain / bindings を読めない等


@dataclass(frozen=True)
class CandidateChange:
    rule_id: str
    status: CandidateStatus


@dataclass(frozen=True)
class TargetOutcome:
    target: SpecTarget
    result: ExtractionResult | None = None
    error: str | None = None  # 候補を作れなかった理由（費用ガード・Gemini の失敗・入力不正）


# ------------------------------------------------------------ 収集（純粋、ファイルのみ）
def collect_spec_changes(base_root: Path | None, head_root: Path) -> SpecBotInput:
    """base と head のスナップショットを比べ、変更・追加された仕様 Markdown を集める。

    `specs/` 以下の `*.md` が対象（`_` で始まるテンプレートと `specs/ir/` は除く）。
    base が無ければ全ての仕様を対象にする。behavior は head の `specs/ir/*.spec-ir.json`
    のうち、出典（source_refs）の path またはこの Markdown の `### 要求 ID` を持つものから
    決める。決まらず bindings の behavior が 1 つだけならそれを使う。
    """
    changed: list[tuple[str, str]] = []
    for md in _spec_markdowns(head_root):
        rel = md.relative_to(head_root).as_posix()
        text = md.read_text(encoding="utf-8")
        before = base_root / rel if base_root is not None else None
        if before is not None and before.is_file() and before.read_text("utf-8") == text:
            continue
        changed.append((rel, text))
    if not changed:
        return SpecBotInput([], None, None, content_hash("[]"))

    speclino_dir = head_root / ".speclino"
    try:
        domain = load_domain(speclino_dir / "domain.yaml")
        bindings = load_bindings(speclino_dir / "bindings.yaml")
        specs = _load_head_specs(head_root)
    except Exception as e:  # noqa: BLE001 - 読めない理由をコメントに出す
        targets = [SpecTarget(rel, text, None, None) for rel, text in changed]
        error = f".speclino/ または specs/ir を読めない: {type(e).__name__}: {e}"[:500]
        return SpecBotInput(targets, None, None, _input_hash(targets, error), error)

    targets = [_target(rel, text, specs, bindings) for rel, text in changed]
    raw = [
        (speclino_dir / "domain.yaml").read_text("utf-8"),
        (speclino_dir / "bindings.yaml").read_text("utf-8"),
    ]
    return SpecBotInput(targets, domain, bindings, _input_hash(targets, *raw))


def _spec_markdowns(root: Path) -> list[Path]:
    specs_dir = root / "specs"
    if not specs_dir.is_dir():
        return []
    return [
        p
        for p in sorted(specs_dir.rglob("*.md"))
        if not p.name.startswith("_") and "ir" not in p.relative_to(specs_dir).parts[:-1]
    ]


def _load_head_specs(head_root: Path) -> list[tuple[str, SpecIR]]:
    ir_dir = head_root / "specs" / "ir"
    if not ir_dir.is_dir():
        return []
    return [
        (p.relative_to(head_root).as_posix(), load_spec_ir(p))
        for p in sorted(ir_dir.glob("*.spec-ir.json"))
    ]


def _target(rel: str, text: str, specs: list[tuple[str, SpecIR]], bindings: Bindings) -> SpecTarget:
    headings = set(_REQUIREMENT_HEADING.findall(text))
    for ir_path, spec in specs:
        rules = [r for r in spec.rules if _belongs(r, rel, headings)]
        if rules:
            return SpecTarget(rel, text, spec.behavior_id, ir_path, rules)
    if len(bindings.behaviors) == 1:
        [behavior_id] = bindings.behaviors
        match = next(((p, s) for p, s in specs if s.behavior_id == behavior_id), None)
        if match is not None:
            return SpecTarget(rel, text, behavior_id, match[0], list(match[1].rules))
        return SpecTarget(rel, text, behavior_id, None)
    return SpecTarget(
        rel,
        text,
        None,
        None,
        skip_reason=(
            "behavior を決められない（この Markdown の要求 ID を持つ Spec IR が specs/ir に無く、"
            ".speclino/bindings.yaml の behavior が複数ある）。"
            "`speclino spec-extract --behavior <id>` を手元で実行してください"
        ),
    )


def _belongs(rule: Rule, rel: str, headings: set[str]) -> bool:
    return any(ref.path == rel or ref.requirement_id in headings for ref in rule.source_refs)


def _input_hash(targets: list[SpecTarget], *extra: str) -> str:
    items = [
        {
            "path": t.path,
            "markdown": content_hash(t.markdown),
            "behavior": t.behavior_id,
            "existing": sorted(interpretation_hash(r) for r in t.existing_rules),
        }
        for t in targets
    ]
    return content_hash(json.dumps([items, list(extra)], ensure_ascii=False, sort_keys=True))


# ------------------------------------------------------------ 差分
def interpretation_hash(rule: Rule) -> str:
    """Rule の解釈（modality / level / claim_scope / applicability / assertion）のハッシュ。"""
    data = rule.model_dump(mode="json", exclude_defaults=True, include=set(_INTERPRETATION_FIELDS))
    data["id"] = rule.id
    return content_hash(json.dumps(data, ensure_ascii=False, sort_keys=True))


def diff_candidates(existing: list[Rule], candidates: list[Rule]) -> list[CandidateChange]:
    """既存 IR と候補を Rule ID で突き合わせる（追加 / 変更 / 削除 / 変更なし）。

    `obligation_diff.diff_rules` と同じく ID で対応を取るが、比べるのは解釈だけ。
    候補は出典（snapshot_id=candidate）と承認を持たないので、承認ハッシュで比べると
    全てが「変更」になってしまう。
    """
    before = {r.id: interpretation_hash(r) for r in existing}
    after = {r.id: interpretation_hash(r) for r in candidates}
    out: list[CandidateChange] = []
    for rid in sorted(set(before) | set(after)):
        b, a = before.get(rid), after.get(rid)
        if b is None:
            status: CandidateStatus = "added"
        elif a is None:
            status = "removed"
        else:
            status = "unchanged" if a == b else "changed"
        out.append(CandidateChange(rid, status))
    order = list(_STATUS_LABELS)
    return sorted(out, key=lambda c: (order.index(c.status), c.rule_id))


# ------------------------------------------------------------ 生成（LLM）
async def generate_candidates(
    inp: SpecBotInput,
    llm: TextLlm,
    cost_guard: CostGuard | None,
    run_id: str | None,
    today: date,
) -> list[TargetOutcome]:
    """対象ごとに候補を作る。失敗は例外にせず `TargetOutcome.error` に理由を残す。"""
    outcomes: list[TargetOutcome] = []
    for target in inp.targets:
        if inp.error is not None or inp.domain is None or inp.bindings is None:
            outcomes.append(TargetOutcome(target, error=inp.error or "語彙を読めない"))
            continue
        if target.skip_reason is not None or target.behavior_id is None:
            outcomes.append(TargetOutcome(target, error=target.skip_reason))
            continue
        if len(target.markdown) > MAX_SPEC_CHARS:
            outcomes.append(
                TargetOutcome(
                    target,
                    error=(
                        f"仕様が大きすぎる（{len(target.markdown)} 文字、"
                        f"上限 {MAX_SPEC_CHARS} 文字）。仕様を分割するか、"
                        "手元で `speclino spec-extract` を実行してください"
                    ),
                )
            )
            continue
        if cost_guard is not None:
            prompt = build_prompt(target.markdown, target.behavior_id, inp.domain, inp.bindings)
            ok, reason = await cost_guard.can_afford(
                today, worst_case_cost(cost_guard, llm, prompt)
            )
            if not ok:
                outcomes.append(TargetOutcome(target, error=f"費用ガードにより停止: {reason}"))
                continue
        try:
            result = await extract_spec(
                target.markdown,
                target.behavior_id,
                inp.domain,
                inp.bindings,
                llm,
                spec_path=target.path,
            )
        except Exception as e:  # noqa: BLE001 - Gemini の失敗は理由としてコメントに出す
            outcomes.append(
                TargetOutcome(target, error=f"Gemini の呼出しに失敗: {type(e).__name__}: {e}"[:500])
            )
            continue
        if cost_guard is not None:
            await cost_guard.record(
                today, run_id, llm.model, result.input_tokens, result.output_tokens
            )
        outcomes.append(TargetOutcome(target, result=result))
    return outcomes


def worst_case_cost(cost_guard: CostGuard, llm: TextLlm, prompt: str) -> Decimal:
    """1 回の呼出しの最悪費用。入力トークンは文字数で上から見積もる（Gemini のトークナイザは
    日本語でも概ね 1 文字 1 トークン以下）。出力は上限（思考トークンを含む）まで出たとみなす。
    """
    return cost_guard.estimate(llm.model, len(prompt), llm.max_output_tokens)


# ------------------------------------------------------------ 表示
def render_spec_bot_comment(outcomes: list[TargetOutcome], head_sha: str) -> str:
    parts = [
        SPEC_BOT_MARKER,
        "## SpecLino / 仕様 PR bot: Spec IR の候補",
        "",
        f"head `{head_sha[:7]}` で変更された仕様 Markdown から、Spec IR の**候補**を抽出しました。",
        "",
        "> この bot は提案だけを行い、**承認しません**。IR をリポジトリに書き込まず、"
        "ゲート判定にも影響しません。候補を確認して `specs/ir/` に反映・コミットし、"
        "承認者が `/speclino approve-spec sha256:...` で承認してください。",
        "",
    ]
    for outcome in outcomes:
        parts += _render_target(outcome)
    body = "\n".join(parts)
    if len(body) > _COMMENT_LIMIT:
        body = body[:_COMMENT_LIMIT] + "\n\n…（省略。手元で `speclino spec-extract` を実行）\n"
    return body


def render_no_changes_comment(head_sha: str) -> str:
    """以前は仕様の変更があったが、今の head では無くなったとき（古い候補を残さない）。"""
    return "\n".join(
        [
            SPEC_BOT_MARKER,
            "## SpecLino / 仕様 PR bot: Spec IR の候補",
            "",
            f"head `{head_sha[:7]}` では、base からの仕様 Markdown（`specs/**/*.md`）の変更は"
            "ありません。候補はありません。",
        ]
    )


def _render_target(outcome: TargetOutcome) -> list[str]:
    t = outcome.target
    behavior = f"behavior `{t.behavior_id}`" if t.behavior_id else "behavior 不明"
    lines = [f"### `{t.path}`（{behavior}）", ""]
    result = outcome.result
    if result is None:
        lines += [f"候補を生成できなかった: {outcome.error or '理由不明'}", ""]
        return lines
    spec = result.spec
    # 候補は定義上すべて未承認なので W_UNAPPROVED は出さない（全要求に並んで本題が埋もれる）
    warnings = [d for d in result.warnings if d.code != "W_UNAPPROVED"]
    lines += [
        f"候補 {len(spec.rules)} 件 / 未抽出 {len(spec.unextracted)} 件 / "
        f"誤り {len(result.errors)} 件 / 警告 {len(warnings)} 件",
        "",
    ]
    source = f"`{t.existing_path}`" if t.existing_path else "既存 IR なし"
    lines += [f"#### 既存 IR との差分（{source}）", "", "| 要求 | 差分 |", "| --- | --- |"]
    changes = diff_candidates(t.existing_rules, spec.rules)
    lines += [f"| {c.rule_id} | {_STATUS_LABELS[c.status]} |" for c in changes]
    if not changes:
        lines.append("| - | 候補も既存の要求も無い |")
    lines.append("")
    diags = [*result.errors, *warnings]
    if diags:
        lines += ["#### 診断", ""]
        lines += [
            f"- {d.severity} `{d.code}` {d.rule_id or '-'}: {d.message.replace(chr(10), ' ')}"
            for d in diags
        ]
        lines.append("")
    payload = json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, indent=2)
    lines += [
        "<details><summary>候補 Spec IR（JSON、未承認）</summary>",
        "",
        "```json",
        payload,
        "```",
        "",
        "</details>",
        "",
    ]
    return lines


__all__ = [
    "MAX_SPEC_CHARS",
    "SPEC_BOT_EVENT",
    "SPEC_BOT_MARKER",
    "CandidateChange",
    "SpecBotInput",
    "SpecTarget",
    "TargetOutcome",
    "collect_spec_changes",
    "diff_candidates",
    "generate_candidates",
    "interpretation_hash",
    "worst_case_cost",
    "render_no_changes_comment",
    "render_spec_bot_comment",
]
