"""仕様 PR bot（S46）: 変更された仕様 Markdown の候補 IR・診断・既存 IR との差分を PR に出す。

LLM は ScriptedJsonLlm（台本）で置き換え、Gemini を呼ばない。
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from speclino.adapters.llm.scripted import ScriptedJsonLlm
from speclino.ports.text_llm import JsonGeneration
from speclino.services.cost_guard import CostGuard, InMemoryLedger
from speclino.services.spec_pr_bot import (
    MAX_SPEC_CHARS,
    SPEC_BOT_MARKER,
    collect_spec_changes,
    diff_candidates,
    generate_candidates,
    render_spec_bot_comment,
    worst_case_cost,
)
from speclino.spec import load_spec_ir

ORDERS = Path(__file__).resolve().parents[3] / "examples" / "orders"
EXISTING = load_spec_ir(ORDERS / "specs" / "ir" / "cancel_order.spec-ir.json")
TODAY = date(2026, 10, 4)


def _rule_json(rule_id: str) -> dict[str, Any]:
    """既存 IR の Rule を、LLM が返す候補（RuleProposal）の形にする。"""
    rule = next(r for r in EXISTING.rules if r.id == rule_id)
    data = rule.model_dump(mode="json", exclude_none=True)
    return {
        "id": data["id"],
        "requirement_id": rule.source_refs[0].requirement_id,
        "modality": data["modality"],
        "level": data["level"],
        "claim_scope": data["claim_scope"],
        "applicability": data["applicability"],
        "assertion": data["assertion"],
    }


def _proposal() -> str:
    """R-CANCEL-01 は既存と同じ、R-CANCEL-02 は応答を 400 に変えた、R-CANCEL-08 は新規。"""
    changed = _rule_json("R-CANCEL-02")
    changed["assertion"]["expr"]["right"]["value"] = 400
    added = {**_rule_json("R-CANCEL-01"), "id": "R-CANCEL-08", "requirement_id": "R-CANCEL-08"}
    return json.dumps(
        {
            "rules": [_rule_json("R-CANCEL-01"), changed, added],
            "unextracted": [{"requirement_id": "R-CANCEL-06", "reason": "通知先を語彙で表せない"}],
            "ambiguities": [
                {
                    "requirement_id": "R-CANCEL-05",
                    "phrase": "速やかに記録する",
                    "question": "同期呼出しか、後続のジョブでもよいか",
                }
            ],
        },
        ensure_ascii=False,
    )


def _make_roots(tmp_path: Path, edit: bool = True) -> tuple[Path, Path]:
    roots = []
    for name in ("base", "head"):
        root = tmp_path / name
        shutil.copytree(ORDERS / ".speclino", root / ".speclino")
        shutil.copytree(ORDERS / "specs", root / "specs")
        (root / "specs" / "_template.md").write_text("### R-XXX-01\n", encoding="utf-8")
        roots.append(root)
    base, head = roots
    if edit:
        md = head / "specs" / "orders-cancel.md"
        md.write_text(md.read_text(encoding="utf-8") + "\n### R-CANCEL-08\n\n追記\n", "utf-8")
        (head / "specs" / "_template.md").write_text("### R-XXX-02\n", encoding="utf-8")
    return base, head


def test_only_changed_markdown_is_collected_and_mapped_to_its_behavior(tmp_path: Path) -> None:
    base, head = _make_roots(tmp_path)
    inp = collect_spec_changes(base, head)
    assert inp.error is None
    [target] = inp.targets  # テンプレート（_ 始まり）は対象外
    assert target.path == "specs/orders-cancel.md"
    assert target.behavior_id == "cancel_order"
    assert target.existing_path == "specs/ir/cancel_order.spec-ir.json"
    assert {r.id for r in target.existing_rules} >= {"R-CANCEL-01", "R-CANCEL-07a"}


def test_nothing_is_collected_when_specs_are_unchanged(tmp_path: Path) -> None:
    base, head = _make_roots(tmp_path, edit=False)
    assert collect_spec_changes(base, head).targets == []


def test_input_hash_changes_with_the_markdown(tmp_path: Path) -> None:
    base, head = _make_roots(tmp_path)
    first = collect_spec_changes(base, head).input_hash
    md = head / "specs" / "orders-cancel.md"
    md.write_text(md.read_text(encoding="utf-8") + "\n追記 2\n", "utf-8")
    assert collect_spec_changes(base, head).input_hash != first


def test_diff_compares_interpretation_not_provenance() -> None:
    """候補は出典（snapshot_id=candidate）や承認が既存と違うが、解釈が同じなら「変更なし」。"""
    import asyncio

    from speclino.adapters.llm.scripted import ScriptedJsonLlm as Llm
    from speclino.spec import load_bindings, load_domain
    from speclino.spec.spec_extractor import extract_spec

    md = (ORDERS / "specs" / "orders-cancel.md").read_text(encoding="utf-8")
    result = asyncio.run(
        extract_spec(
            md,
            "cancel_order",
            load_domain(ORDERS / ".speclino" / "domain.yaml"),
            load_bindings(ORDERS / ".speclino" / "bindings.yaml"),
            Llm(_proposal()),
        )
    )
    changes = {c.rule_id: c.status for c in diff_candidates(EXISTING.rules, result.spec.rules)}
    assert changes["R-CANCEL-01"] == "unchanged"
    assert changes["R-CANCEL-02"] == "changed"
    assert changes["R-CANCEL-08"] == "added"
    assert changes["R-CANCEL-03"] == "removed"


async def test_candidates_diagnostics_and_cost_are_recorded(tmp_path: Path) -> None:
    base, head = _make_roots(tmp_path)
    llm = ScriptedJsonLlm(_proposal(), usage=(2000, 500))
    ledger = InMemoryLedger([])
    guard = CostGuard(ledger, Decimal("3"))
    [outcome] = await generate_candidates(collect_spec_changes(base, head), llm, guard, "r1", TODAY)
    assert outcome.error is None and outcome.result is not None
    assert len(outcome.result.spec.rules) == 3
    assert {d.code for d in outcome.result.warnings} >= {"W_AMBIGUOUS", "W_UNEXTRACTED"}
    assert len(llm.prompts) == 1 and len(ledger.rows) == 1, "Gemini の呼出しは費用台帳に残す"


async def test_cost_guard_refusal_skips_the_llm_and_says_why(tmp_path: Path) -> None:
    base, head = _make_roots(tmp_path)
    llm = ScriptedJsonLlm(_proposal())
    # 枠を使い切った日（台本の LLM は費用 0 なので、残り枠が負のときだけ止まる）
    guard = CostGuard(InMemoryLedger([(TODAY, Decimal("3.01"))]), Decimal("3"))
    [outcome] = await generate_candidates(collect_spec_changes(base, head), llm, guard, "r1", TODAY)
    assert llm.prompts == []
    assert outcome.result is None and outcome.error and "費用" in outcome.error


class _BrokenLlm:
    model = "gemini-3.8-flash"

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> JsonGeneration:
        raise RuntimeError("429 RESOURCE_EXHAUSTED")


async def test_llm_failure_becomes_a_message_not_an_exception(tmp_path: Path) -> None:
    base, head = _make_roots(tmp_path)
    [outcome] = await generate_candidates(
        collect_spec_changes(base, head), _BrokenLlm(), None, "r1", TODAY
    )
    assert outcome.result is None and outcome.error and "429" in outcome.error
    body = render_spec_bot_comment([outcome], "abcdef1234567890")
    assert body.startswith(SPEC_BOT_MARKER)
    assert "候補を生成できなかった" in body and "429" in body


async def test_comment_shows_candidates_diff_and_diagnostics_but_never_approves(
    tmp_path: Path,
) -> None:
    base, head = _make_roots(tmp_path)
    # 語彙に無い変数を参照する候補（E_REF_UNDEFINED）を足す
    data = json.loads(_proposal())
    bad = _rule_json("R-CANCEL-01")
    bad["id"] = "R-CANCEL-09"
    bad["requirement_id"] = "R-CANCEL-09"
    bad["applicability"] = {"op": "ref", "path": "before.order.no_such_field"}
    data["rules"].append(bad)
    llm = ScriptedJsonLlm(json.dumps(data, ensure_ascii=False))
    outcomes = await generate_candidates(collect_spec_changes(base, head), llm, None, "r1", TODAY)
    body = render_spec_bot_comment(outcomes, "abcdef1234567890")
    assert body.startswith(SPEC_BOT_MARKER)
    assert "specs/orders-cancel.md" in body and "cancel_order" in body and "abcdef1" in body
    for code in ("W_AMBIGUOUS", "W_UNEXTRACTED", "E_REF_UNDEFINED"):
        assert code in body
    assert "| R-CANCEL-02 | 変更 |" in body
    assert "| R-CANCEL-08 | 追加 |" in body
    assert "| R-CANCEL-03 | 削除 |" in body
    assert "承認しません" in body
    assert "W_UNAPPROVED" not in body, "候補は必ず未承認なので並べない"
    assert "/speclino approve-spec sha256:" not in body.replace(
        "/speclino approve-spec sha256:...", ""
    ), "候補のハッシュで承認コマンドを提示しない"
    assert '"approval_ref"' not in body or '"approval_ref": null' in body


async def test_oversized_spec_is_skipped_without_calling_the_llm(tmp_path: Path) -> None:
    """入力の大きさに上限を置き、1 回の呼出しの費用を抑える（超えたら理由を出して止める）。"""
    base, head = _make_roots(tmp_path)
    md = head / "specs" / "orders-cancel.md"
    md.write_text(md.read_text(encoding="utf-8") + "あ" * (MAX_SPEC_CHARS + 1), "utf-8")
    llm = ScriptedJsonLlm(_proposal())
    [outcome] = await generate_candidates(collect_spec_changes(base, head), llm, None, "r1", TODAY)
    assert llm.prompts == []
    assert outcome.result is None and outcome.error and "大きすぎる" in outcome.error
    assert f"{MAX_SPEC_CHARS}" in outcome.error


async def test_worst_case_cost_must_fit_in_the_remaining_daily_budget(tmp_path: Path) -> None:
    """固定の余裕（per_run_limit）ではなく、上限入力＋最大出力の最悪費用で判断する。"""
    base, head = _make_roots(tmp_path)
    llm = ScriptedJsonLlm(_proposal())
    expensive = {"scripted": (Decimal("100"), Decimal("100"))}  # 100 万トークンあたり $100
    # 使用済み $2.4 / 枠 $3: 固定の余裕 $0.50 なら通るが、最悪費用は残り $0.60 を超える
    guard = CostGuard(InMemoryLedger([(TODAY, Decimal("2.4"))]), Decimal("3"), pricing=expensive)
    [outcome] = await generate_candidates(collect_spec_changes(base, head), llm, guard, "r1", TODAY)
    assert llm.prompts == []
    assert outcome.result is None and outcome.error and "最悪" in outcome.error

    # 枠が十分なら呼ぶ
    roomy = CostGuard(InMemoryLedger([]), Decimal("100"), pricing=expensive)
    [ok] = await generate_candidates(collect_spec_changes(base, head), llm, roomy, "r1", TODAY)
    assert ok.result is not None and len(llm.prompts) == 1


def test_worst_case_estimate_uses_capped_input_and_max_output() -> None:
    guard = CostGuard(InMemoryLedger([]), Decimal("3"))
    llm = ScriptedJsonLlm(_proposal())
    usd = worst_case_cost(guard, llm, "x" * 1000)
    assert usd == guard.estimate(llm.model, 1000, llm.max_output_tokens)
