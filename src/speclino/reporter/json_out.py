"""reporter.json_out（PoC 5.3「出力ファイル」）。成果物一式を決定的に書き出す。

同じ入力なら同じバイト列になる（JSON は indent=2, ensure_ascii=False, sort_keys=True で固定）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from speclino.conformance.gate import GateResult
from speclino.domain.code_ir import CodeIR
from speclino.domain.finding import Findings
from speclino.domain.run import RunManifest
from speclino.domain.spec_ir import SpecIR
from speclino.reporter.review_md import render_review

_JSON_KWARGS: dict[str, Any] = {"indent": 2, "ensure_ascii": False, "sort_keys": True}


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, **_JSON_KWARGS) + "\n", encoding="utf-8")


def _gate_dict(gate: GateResult) -> dict[str, object]:
    return {
        "status": gate.status,
        "reason": gate.reason,
        "blocking_ids": sorted(f.id for f in gate.blocking),
        "advisory_ids": sorted(f.id for f in gate.advisory),
        "waived_ids": sorted(f.id for f in gate.waived),
    }


def write_artifacts(
    out_dir: Path,
    manifest: RunManifest,
    spec: SpecIR,
    code: CodeIR | None,
    findings: Findings,
    gate: GateResult | None,
    semantic_diff: dict[str, Any] | None = None,
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
    emit("spec-ir.json", spec.model_dump(mode="json"))
    if code is not None:
        emit("code-ir.json", code.model_dump(mode="json"))
    emit("findings.json", findings.model_dump(mode="json"))
    if gate is not None:
        emit("gate.json", _gate_dict(gate))
    if semantic_diff is not None:
        emit("semantic-diff.json", semantic_diff)

    review_path = out_dir / "review.md"
    review_path.write_text(
        render_review(manifest, findings, gate, spec.behavior_id), encoding="utf-8"
    )
    written["review.md"] = review_path
    return written


__all__ = ["write_artifacts"]
