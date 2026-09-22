"""Golden ケースを実行し、期待 verdict / category と反例の一致を測る（PoC 17.1 / 17.3）。

mode:
  handwritten: expected-code-ir.json（手書き Code IR）を検査器に入力する（PoC M1）
  source:      app/service.py を解析器に入力し、生成 Code IR で検査する（PoC M2）
一致率だけを出さず、要求数・承認済み数・対応範囲数・判定済み数・未解析数を併記する。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from speclino.analysis.limits import DEFAULT_LIMITS
from speclino.analysis.path_analyzer import analyze_repo
from speclino.conformance.engine import evaluate
from speclino.domain.code_ir import CodeIR
from speclino.domain.finding import Findings
from speclino.spec import load_bindings, load_domain, load_spec_ir

Mode = Literal["handwritten", "source"]
VERDICTS = ("VERIFIED_IN_SCOPE", "VIOLATED", "UNKNOWN", "REVIEW")


class CaseResult(BaseModel):
    id: str
    title: str
    expected: dict[str, dict[str, str]]
    actual: dict[str, dict[str, str]]
    mismatches: list[str] = Field(default_factory=list)
    counterexample_ok: bool | None = None
    counterexample_detail: str = ""

    @property
    def ok(self) -> bool:
        return not self.mismatches and self.counterexample_ok is not False


class EvalReport(BaseModel):
    mode: Mode
    cases: list[CaseResult]
    confusion: dict[str, dict[str, int]]  # expected verdict → actual verdict → count
    false_verified: int
    counterexample_matches: int
    counterexample_total: int
    requirements_total: int
    requirements_approved: int
    requirements_in_scope: int
    requirements_decided: int
    requirements_unanalyzed: int

    @property
    def all_match(self) -> bool:
        return all(c.ok for c in self.cases)


def _source_repo(golden_root: Path, case_dir: Path, tmp: Path) -> Path:
    """examples/orders の app を写し、ケースの service.py で差し替えた一時リポジトリ。"""
    repo_root = (
        golden_root.parents[2] if golden_root.name == "cancel_order" else golden_root.parents[1]
    )
    src_app = repo_root / "examples" / "orders" / "app"
    dst_app = tmp / "examples" / "orders" / "app"
    dst_app.mkdir(parents=True)
    (tmp / "examples" / "__init__.py").write_text("")
    (tmp / "examples" / "orders" / "__init__.py").write_text("")
    for name in ("__init__.py", "models.py", "inventory.py", "audit.py"):
        shutil.copy(src_app / name, dst_app / name)
    shutil.copy(case_dir / "app" / "service.py", dst_app / "service.py")
    return tmp


def run_golden(golden_dir: Path, mode: Mode = "handwritten") -> EvalReport:
    golden_dir = golden_dir.resolve()
    spec = load_spec_ir(golden_dir / "spec-ir.json")
    domain = load_domain(golden_dir / "domain.yaml")
    bindings = (
        load_bindings(golden_dir / "bindings.yaml")
        if (golden_dir / "bindings.yaml").exists()
        else None
    )
    cases: list[CaseResult] = []
    confusion: dict[str, dict[str, int]] = {v: dict.fromkeys(VERDICTS, 0) for v in VERDICTS}
    false_verified = 0
    cex_ok = cex_total = 0
    cov = dict.fromkeys(
        (
            "requirements_total",
            "requirements_approved",
            "requirements_in_scope",
            "requirements_decided",
            "requirements_unanalyzed",
        ),
        0,
    )
    for case_dir in sorted(p for p in golden_dir.iterdir() if p.is_dir()):
        meta = yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))
        if mode == "handwritten":
            code = CodeIR.model_validate(
                json.loads((case_dir / "expected-code-ir.json").read_text())
            )
        else:
            if bindings is None:
                raise SystemExit("source モードには bindings.yaml が必要")
            with tempfile.TemporaryDirectory(prefix="speclino-golden-") as tmp:
                root = _source_repo(golden_dir, case_dir, Path(tmp))
                code = analyze_repo(root, spec.behavior_id, domain, bindings, limits=DEFAULT_LIMITS)
        findings = evaluate(spec, code, domain)
        result = _compare(meta, findings, strict_path_id=(mode == "handwritten"))
        cases.append(result)
        for rid, exp in result.expected.items():
            act = result.actual.get(rid, {"verdict": "UNKNOWN"})
            confusion[exp["verdict"]][act["verdict"]] += 1
            if exp["verdict"] != "VERIFIED_IN_SCOPE" and act["verdict"] == "VERIFIED_IN_SCOPE":
                false_verified += 1
        if result.counterexample_ok is not None:
            cex_total += 1
            cex_ok += int(result.counterexample_ok)
        for k in cov:
            cov[k] += getattr(findings.coverage, k)
    return EvalReport(
        mode=mode,
        cases=cases,
        confusion=confusion,
        false_verified=false_verified,
        counterexample_matches=cex_ok,
        counterexample_total=cex_total,
        **cov,
    )


def _compare(meta: dict[str, Any], findings: Findings, strict_path_id: bool = True) -> CaseResult:
    expected = {
        k: {"verdict": v["verdict"], "category": v["category"]} for k, v in meta["expected"].items()
    }
    actual = {
        f.requirement_id: {"verdict": f.verdict, "category": f.category} for f in findings.findings
    }
    mismatches = []
    for rid, exp in expected.items():
        act = actual.get(rid)
        if act is None:
            mismatches.append(f"{rid}: Finding が無い")
        elif act != exp:
            mismatches.append(
                f"{rid}: 期待 {exp['verdict']}/{exp['category']}"
                f" 実際 {act['verdict']}/{act['category']}"
            )
    cex_ok: bool | None = None
    detail = ""
    must = meta.get("counterexample_must_include") or {}
    for rid, req in must.items():
        f = next((f for f in findings.findings if f.requirement_id == rid), None)
        cex = f.counterexample if f else None
        if cex is None:
            cex_ok, detail = False, f"{rid}: 反例が無い"
            break
        ok = True
        if strict_path_id and req.get("path_id") and cex.path_id != req["path_id"]:
            ok, detail = False, f"{rid}: path_id 期待 {req['path_id']} 実際 {cex.path_id}"
        for var, val in (req.get("assignments") or {}).items():
            if cex.assignments.get(var) != val:
                ok, detail = False, f"{rid}: {var} 期待 {val!r} 実際 {cex.assignments.get(var)!r}"
        cex_ok = ok if cex_ok is None else (cex_ok and ok)
    return CaseResult(
        id=meta["id"],
        title=meta.get("title", meta["id"]),
        expected=expected,
        actual={k: actual[k] for k in expected if k in actual},
        mismatches=mismatches,
        counterexample_ok=cex_ok,
        counterexample_detail=detail,
    )


def render_markdown(report: EvalReport) -> str:
    lines = [f"# Golden 評価（mode={report.mode}）", ""]
    lines.append(f"- ケース: {sum(c.ok for c in report.cases)}/{len(report.cases)} 一致")
    lines.append(f"- 誤った VERIFIED: **{report.false_verified}**（0 でなければ CI 失敗）")
    lines.append(f"- 反例の一致: {report.counterexample_matches}/{report.counterexample_total}")
    lines.append("")
    lines.append("## 併記（PoC 17.3）")
    lines.append("")
    lines.append("| 要求数 | 承認済み | 対応範囲 | 判定済み | 未解析 |")
    lines.append("| --- | --- | --- | --- | --- |")
    lines.append(
        f"| {report.requirements_total} | {report.requirements_approved}"
        f" | {report.requirements_in_scope} "
        f"| {report.requirements_decided} | {report.requirements_unanalyzed} |"
    )
    lines.append("")
    lines.append("## 混同行列（期待 → 実際）")
    lines.append("")
    lines.append("| 期待 \\ 実際 | " + " | ".join(VERDICTS) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in VERDICTS) + " |")
    for exp in VERDICTS:
        lines.append(
            f"| {exp} | " + " | ".join(str(report.confusion[exp][a]) for a in VERDICTS) + " |"
        )
    lines.append("")
    lines.append("## ケース")
    lines.append("")
    lines.append("| ID | タイトル | 結果 | 不一致 |")
    lines.append("| --- | --- | --- | --- |")
    for c in report.cases:
        note = "; ".join(c.mismatches) or c.counterexample_detail
        lines.append(f"| {c.id} | {c.title} | {'OK' if c.ok else 'NG'} | {note} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Golden fixtures を評価する")
    parser.add_argument("golden", type=Path, help="eval/golden/<behavior> ディレクトリ")
    parser.add_argument("--mode", choices=("handwritten", "source"), default="handwritten")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    report = run_golden(args.golden, args.mode)
    md = render_markdown(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
    sys.stdout.write(md)
    if report.false_verified > 0 or not report.all_match:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
