"""reporter.json_out（PoC 5.3「出力ファイル」）。成果物一式を決定的に書き出す。

同じ入力なら同じバイト列になる（JSON は indent=2, ensure_ascii=False, sort_keys=True で固定）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from speclino.conformance.exceptions import Waiver
from speclino.conformance.gate import GateResult
from speclino.domain.code_ir import CodeIR
from speclino.domain.finding import Findings
from speclino.domain.run import RunManifest
from speclino.domain.semantic_diff import SemanticDiff
from speclino.domain.spec_ir import SpecIR
from speclino.reporter.review_md import render_review

_JSON_KWARGS: dict[str, Any] = {"indent": 2, "ensure_ascii": False, "sort_keys": True}


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, **_JSON_KWARGS) + "\n", encoding="utf-8")


def _gate_dict(gate: GateResult, waivers: list[Waiver] | None = None) -> dict[str, object]:
    # ゲートで実際に免除された finding の分だけ（どの例外で・いつまで・どう直すか）を残す
    waived = {f.id for f in gate.waived}
    kept = sorted((w for w in waivers or [] if w.finding_id in waived), key=lambda w: w.finding_id)
    out: dict[str, object] = {
        "status": gate.status,
        "reason": gate.reason,
        "blocking_ids": sorted(f.id for f in gate.blocking),
        "advisory_ids": sorted(f.id for f in gate.advisory),
        "waived_ids": sorted(f.id for f in gate.waived),
        "waivers": [
            {
                "finding_id": w.finding_id,
                "requirement_id": w.requirement_id,
                "exception_id": w.exception_id,
                "approver": w.approver,
                "expires_on": w.expires_on.isoformat(),
                "reason": w.reason,
                "alternative": w.alternative,
            }
            for w in kept
        ],
    }
    if gate.cleared:
        # 阻止しない REVIEW（S70: LLM 支援の指摘なし・人の確定）。無い run の gate.json は変えない
        out["cleared_ids"] = sorted(f.id for f in gate.cleared)
    if gate.self_attested_ids:
        out["self_attested_ids"] = sorted(gate.self_attested_ids)
    return out


def write_artifacts(
    out_dir: Path,
    manifest: RunManifest,
    spec: SpecIR | None,
    code: CodeIR | None,
    findings: Findings,
    gate: GateResult | None,
    semantic_diff: SemanticDiff | None = None,
    waivers: list[Waiver] | None = None,
) -> dict[str, Path]:
    """PoC 5.3 の出力ファイル一式を `out_dir` に書き、書いたパスの辞書を返す。

    `code` / `semantic_diff` が None のときはそのファイルを書かない。`gate` が None
    （ゲート計算前の異常終了時など）のときも gate.json は省略するが、review.md は
    「ゲート: 未計算」として最後まで出す（PoC 5.3「実行エラー時も可能な範囲で
    run-manifest.json と診断情報を出す」）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    def emit(name: str, data: object) -> None:
        path = out_dir / name
        _write_json(path, data)
        written[name] = path

    emit("run-manifest.json", manifest.model_dump(mode="json"))
    # bundle の run（S65）で旧形式の仕様が無いときは spec-ir.json を書かない
    if spec is not None:
        emit("spec-ir.json", spec.model_dump(mode="json"))
    if code is not None:
        emit("code-ir.json", code.model_dump(mode="json"))
    emit("findings.json", findings.model_dump(mode="json"))
    if gate is not None:
        emit("gate.json", _gate_dict(gate, waivers))
    if semantic_diff is not None:
        emit("semantic-diff.json", semantic_diff.model_dump(mode="json"))

    review_path = out_dir / "review.md"
    review_path.write_text(
        render_review(
            manifest,
            findings,
            gate,
            spec.behavior_id if spec is not None else "(bundle)",
            semantic_diff,
        ),
        encoding="utf-8",
    )
    written["review.md"] = review_path
    return written


__all__ = ["write_artifacts"]
