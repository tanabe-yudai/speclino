# S45 プロジェクト画面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 最新の完了した検査の成果物から、プロジェクトの状態・要求・適用規約・検査履歴・「次にやること」を 1 ページで見せる（Issue #55）。

**Architecture:** 組み立ては純粋関数（`services/project_overview.py` の `build_*`）で行い、単体テストする。DB と blob の読み出しは同じモジュールの非同期関数（`list_projects` / `load_overview`）に分け、統合テストする。ルート（`api/routes/projects.py`）は呼ぶだけ。画面側は `web/src/services/`（`nextActions`・`requirements`・`health`・`requestCache`）に規則を置いて vitest で試し、部品は `web/src/components/project/` に置く。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy async / pydantic、React 19 / Vite 8 / Tailwind v4 / shadcn/ui、pytest（integration は実 PostgreSQL）、vitest。

**Spec:** `design/2026-09-30-ui-redesign.md`（5 章、4 章のルート、8 章のテスト）。Issue #55 の完了条件も満たす。

## Global Constraints

- 判定は検査器だけ。画面と API は findings を並べ直すだけで、新しい判定を作らない。4 値（適合 / 違反 / 未検証 / 要確認）をそのまま出し、判定の無い要求は「未解析」と出す（推測で埋めない）
- 「次にやること」は検査結果から決まった規則で並べ、LLM で作らない。最大 5 件
- 検査がまだ無ければ API は `status: "not_checked"`、画面は「まだ検査されていない」とだけ出す
- `/api/workspaces/{slug}/...` はメンバーだけ。それ以外（未所属・他のワークスペースの project）は存在しない場合と同じ 404（`workspace_not_found`）
- 型は `src/speclino/domain/`（ロジックなし）と `web/src/types/`、ロジックは `services/`、API と画面には置かない
- `conformance/`・`analysis/` は変えない。`make check`・`make eval` の結果を変えない
- 画面に「デモ」という語を出さない。個人の環境の値を画面・文書・fixture に書かない（例は `example-org/...`）
- スマホ幅（390px）で横スクロールを出さない。ライト / ダークの両方で読める

## Review Focus

1. `/w/<所属していない slug>` を開く → 別のワークスペースの内容に化けず「見つからない」と出る（`useCurrentWorkspace` は所属の先頭に落ちるので、ホームでは URL の slug を直接照合する）
2. 最新の完了した run の成果物が blob に無い・一部だけ欠けている・壊れている → 500 にせず、欠けたまま `ok` にもせず `status: "unavailable"` と検査の詳細へのリンク
6. 同じワークスペースでプロジェクトを切り替える → 前のプロジェクトの状態や反例が新しいプロジェクトの名前の下に一瞬も出ない
3. ワークスペースを切り替えた直後 → 前のワークスペースのプロジェクト一覧が一瞬も出ない（M-6）
4. 要求の ID・題・仕様のパスが長い / 仕様のパスが無い（例の Spec IR は `source_refs[].path` が null）→ 390px ではみ出さず、GitHub へのリンクは出さない
5. 規約がチーム規約もプロジェクト固有も無い run → 規約の欄は「適用された規約はありません」、チーム規約だけ結果が無い → 「この検査では結果なし」（空欄や適合にしない）

---

## 設計上の判断（計画の時点で決めたこと）

- **承認切れの定義を実データに合わせる**: 仕様書 5.2 は「承認の内容ハッシュ不一致の finding がある要求」としているが、そういう finding は無い。仕様本文のハッシュ不一致（`E_SPEC_HASH_MISMATCH`）は検証エラーで run 自体が失敗する。IR のハッシュ不一致（`W_IR_HASH_MISMATCH`）は警告だけで、検査器は `approval_ref` の有無だけで承認を判断する（`conformance/engine.py` の `_approved`）。実際には PR で内容が変わった要求は承認を外され（`services/obligation_diff.py`）、`REVIEW: stale_approval` として検査される。そこで
  - 承認済み = `approval_ref` がその Spec IR の `approvals[].id` にある（`_approved` と同じ条件。画面の承認の状態を検査器の判定と食い違わせない）。IR ハッシュでは照合しない: PR の run では base 由来の承認記録（`base-trusted`）が仮の IR ハッシュ `sha256:base` を持つため、照合すると変更の無い要求まで承認切れになる
  - 承認切れ = 承認済みでなく、`semantic-diff.json` の `rules[]` でその要求が `changed`（base では有った要求の内容が変わった）。または head から消えた要求で `stale_approval` の finding がある（承認なしの削除）
  - 未承認 = それ以外の承認済みでない要求
  - Task 7 で仕様書 5.2 をこの定義に直す
- **要求の判定** = その要求の findings（規約の findings を除く）の最も重いもの。重さは 違反 > 要確認 > 未検証 > 適合（`web/src/services/labels.ts` の `VERDICT_ORDER` と同じ）。finding の無い要求は `null`（未解析）
- **規約の finding の見分け方**: `behavior_id` が `policy:` で始まる。規約のルール ID は `requirement_id` の最初の `:` より前（`PolicyRule.id` はコロンを含めない）
- **規約の必須 / 助言と検査器の有無**: チーム規約は manifest の `policy_team` / `policy_revision` から改訂を引いて `PolicyRule` の `level` と `verifier` を使う。プロジェクト固有の規約（`.speclino/policy/`）は run の成果物に定義が残らないので、`gate.json` の `blocking_ids`（必須）/ `advisory_ids`（助言）に載った finding からだけ分かる。分からなければ `null`（画面は「—」）
- **未解析を分母から落とさない**: Spec IR の `unextracted`（抽出しきれなかった要求）と、finding の無い要求を「未解析」として数え、健康状態の分母に入れる（`conformance/engine.py` の `requirements_total` と同じ考え方）。ホームのカードは findings（要求ごとの判定の件数）と `findings.json` の `coverage.requirements_total` から未解析を出す（Spec IR は読まない）
- **成果物の欠落は `unavailable`**: manifest の `analysis[].behavior_id` にある behavior の Spec IR が 1 つでも読めなければ、欠けたまま `ok` にしない（複数 behavior の run でも先頭の behavior の `spec-ir.json` が残るため、見つかったファイルだけで組み立てると要求が静かに消える）
- **M-1**: ワークスペースのホームを `/w/:slug` にする。`/app` は所属の先頭の `/w/:slug` へ転送（所属 0 件ならその場で uid を出す）。ワークスペースのピルの行き先は `/w/:slug`
- **M-6**: プロジェクト一覧は新しい `GET /api/workspaces/{slug}/projects` を slug ごとにキャッシュして、ホーム・ピル・⌘K で共有する。登録が成功したら無効化する
- **反例の表示**: overview には finding の本文を入れない。要求の行を開いたときに既存の `GET /api/review-runs/{run_id}` を 1 回だけ読み、その要求の findings を既存の `FindingCard` で出す
- **⌘K の要求 ID** は今回入れない（プロジェクトとワークスペースのホームだけ）。要求 ID は画面の絞り込みで探せるため。必要なら段 4 のあとに足す

---

### Task 1: overview の型と組み立て（純粋関数）

**Files:**
- Create: `src/speclino/domain/project_overview.py`
- Create: `src/speclino/services/project_overview.py`
- Test: `tests/unit/services/test_project_overview.py`

**Interfaces:**
- Produces（domain）: `ApprovalState = Literal["approved", "unapproved", "stale"]`、`PolicyOrigin = Literal["team", "project"]`、`OverviewStatus = Literal["ok", "not_checked", "unavailable"]`、`RunView`、`RequirementView`、`PolicyView`、`ProjectSummary`、`ProjectOverview`（フィールドは下のコード）
- Produces（services）: `is_policy_finding(f: Finding) -> bool`、`worst_verdict(vs: Iterable[Verdict]) -> Verdict | None`、`requirement_verdicts(findings: Iterable[Finding]) -> dict[str, Verdict]`、`count_verdicts(vs: Iterable[Verdict]) -> VerdictCounts`、`build_requirements(spec_irs: list[SpecIR], findings: list[Finding], diff: SemanticDiff | None) -> list[RequirementView]`、`build_policies(findings: list[Finding], gate: dict[str, Any] | None, team: PolicyPackage | None) -> list[PolicyView]`

- [ ] **Step 1: domain の型を書く**（ロジックなし。テストより先に置くのは型だけ）

```python
"""プロジェクト画面（S45）の応答の型。組み立ては `services/project_overview.py`。

design/2026-09-30-ui-redesign.md 5.2 節。判定は検査器の findings をそのまま使い、ここでは作らない。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from speclino.domain.common import IRModel
from speclino.domain.finding import Verdict, VerdictCounts
from speclino.domain.policy import PolicyLevel
from speclino.domain.run import ExecState, GateStatus

ApprovalState = Literal["approved", "unapproved", "stale"]
PolicyOrigin = Literal["team", "project"]
OverviewStatus = Literal["ok", "not_checked", "unavailable"]


class RunView(IRModel):
    id: str
    pr_number: int | None
    head_sha: str
    exec_state: ExecState
    gate_status: GateStatus | None
    created_at: datetime | None


class RequirementView(IRModel):
    id: str
    behavior_id: str
    title: str | None
    spec_path: str | None
    line_start: int | None
    line_end: int | None
    approval: ApprovalState
    tests_declared: int = Field(ge=0)
    verdict: Verdict | None = Field(description="findings の最も重い判定。None は未解析")
    finding_ids: list[str] = Field(default_factory=list)
    removed: bool = Field(default=False, description="head の Spec IR から消えた要求")


class PolicyView(IRModel):
    rule_id: str
    origin: PolicyOrigin
    title: str | None
    level: PolicyLevel | None = Field(
        description="分からなければ None（プロジェクト固有で成立したもの）"
    )
    has_verifier: bool | None
    status: Verdict | None = Field(description="None はこの検査で結果が無い")
    counts: VerdictCounts


class ProjectSummary(IRModel):
    id: str
    repo_full_name: str
    default_branch: str
    latest_run: RunView | None = Field(
        description="最新の完了した検査（completed / awaiting_answer）"
    )
    counts: VerdictCounts | None = Field(description="latest_run の要求ごとの判定の件数")
    unanalyzed: int | None = Field(
        default=None,
        description="判定の無い要求の数（findings.json の coverage から）。読めなければ None",
    )


class ProjectOverview(IRModel):
    status: OverviewStatus
    project: ProjectSummary
    run: RunView | None = None
    counts: VerdictCounts | None = None
    unanalyzed: int = Field(
        default=0, description="finding の無い要求と、抽出しきれなかった要求の数"
    )
    unextracted: list[str] = Field(
        default_factory=list, description="Spec IR の unextracted（抽出しきれなかった要求 ID）"
    )
    unapproved: int = Field(default=0, description="承認済みでない要求（未承認と承認切れ）")
    policy_team: str | None = None
    policy_revision: int | None = None
    requirements: list[RequirementView] = Field(default_factory=list)
    policies: list[PolicyView] = Field(default_factory=list)
    runs: list[RunView] = Field(
        default_factory=list, description="その project の run（新しい順、上限 20）"
    )
```

- [ ] **Step 2: 失敗するテストを書く**

```python
"""`services/project_overview.py` の組み立て（S45）。DB も blob も使わない。"""

from __future__ import annotations

import json
from pathlib import Path

from speclino.domain.finding import Finding, Verdict
from speclino.domain.policy import PolicyApplicability, PolicyPackage, PolicyRule
from speclino.domain.semantic_diff import RuleDelta, SemanticDiff
from speclino.domain.spec_ir import SpecIR
from speclino.services.project_overview import (
    build_policies,
    build_requirements,
    count_verdicts,
    requirement_verdicts,
    worst_verdict,
)

ROOT = Path(__file__).resolve().parents[3]


def _spec() -> SpecIR:
    path = ROOT / "examples/orders/specs/ir/cancel_order.spec-ir.json"
    return SpecIR.model_validate(json.loads(path.read_text()))


def _finding(
    req: str,
    verdict: Verdict,
    *,
    behavior: str = "cancel_order",
    category: str = "conformant",
    fid: str | None = None,
) -> Finding:
    return Finding.model_validate(
        {
            "id": fid or f"{req}:{verdict}:{category}",
            "requirement_id": req,
            "behavior_id": behavior,
            "verdict": verdict,
            "category": category,
            "severity": "high",
            "message": "m",
            "claim_scope": "persisted_state",
        }
    )


def test_worst_verdict_orders_violated_review_unknown_verified() -> None:
    assert worst_verdict(["VERIFIED_IN_SCOPE", "UNKNOWN", "REVIEW"]) == "REVIEW"
    assert worst_verdict(["UNKNOWN", "VIOLATED"]) == "VIOLATED"
    assert worst_verdict([]) is None


def test_requirement_verdicts_ignore_policy_findings() -> None:
    findings = [
        _finding("R-1", "VERIFIED_IN_SCOPE"),
        _finding("R-1", "UNKNOWN", category="unsupported_analysis"),
        _finding("MAKE-001:Makefile", "VIOLATED", behavior="policy:make_targets"),
    ]
    assert requirement_verdicts(findings) == {"R-1": "UNKNOWN"}


def test_count_verdicts_fills_all_four() -> None:
    c = count_verdicts(["VIOLATED", "VIOLATED", "REVIEW"])
    assert (c.VIOLATED, c.REVIEW, c.UNKNOWN, c.VERIFIED_IN_SCOPE) == (2, 1, 0, 0)


def test_requirements_follow_spec_ir_and_findings() -> None:
    spec = _spec()
    rows = build_requirements(
        [spec], [_finding("R-CANCEL-04", "VIOLATED", category="missing_effect")], None
    )
    by_id = {r.id: r for r in rows}
    assert [r.id for r in rows] == [r.id for r in spec.rules]
    assert by_id["R-CANCEL-04"].verdict == "VIOLATED"
    assert by_id["R-CANCEL-04"].approval == "approved"
    assert by_id["R-CANCEL-04"].tests_declared == 2
    assert by_id["R-CANCEL-04"].finding_ids == ["R-CANCEL-04:VIOLATED:missing_effect"]
    assert by_id["R-CANCEL-01"].verdict is None  # finding が無い = 未解析


def test_unapproved_and_stale_are_told_apart_by_semantic_diff() -> None:
    spec = _spec()
    rules = [
        r.model_copy(update={"approval_ref": None}) if r.id in {"R-CANCEL-01", "R-CANCEL-02"} else r
        for r in spec.rules
    ]
    spec = spec.model_copy(update={"rules": rules})
    diff = SemanticDiff(
        head_commit="h",
        base_available=True,
        rules=[RuleDelta(rule_id="R-CANCEL-02", behavior_id=spec.behavior_id, status="changed")],
    )
    by_id = {r.id: r for r in build_requirements([spec], [], diff)}
    assert by_id["R-CANCEL-01"].approval == "unapproved"
    assert by_id["R-CANCEL-02"].approval == "stale"
    assert by_id["R-CANCEL-03"].approval == "approved"


def test_approval_ref_missing_from_records_is_not_approved() -> None:
    spec = _spec()
    rules = [
        r.model_copy(update={"approval_ref": "nowhere"}) if r.id == "R-CANCEL-01" else r
        for r in spec.rules
    ]
    rows = build_requirements([spec.model_copy(update={"rules": rules})], [], None)
    assert {r.id: r.approval for r in rows}["R-CANCEL-01"] == "unapproved"


def test_removed_requirement_with_stale_approval_is_listed() -> None:
    spec = _spec()
    spec = spec.model_copy(update={"rules": [r for r in spec.rules if r.id != "R-CANCEL-04"]})
    rows = build_requirements(
        [spec], [_finding("R-CANCEL-04", "REVIEW", category="stale_approval")], None
    )
    removed = [r for r in rows if r.removed]
    assert [(r.id, r.approval, r.verdict) for r in removed] == [("R-CANCEL-04", "stale", "REVIEW")]


def _team() -> PolicyPackage:
    def rule(rid: str, level: str, verifier: bool) -> PolicyRule:
        return PolicyRule.model_validate(
            {
                "id": rid,
                "version": "1",
                "level": level,
                "title": f"{rid} の題",
                "text": "t",
                "applies_to": PolicyApplicability(paths=["specs/*.md"]).model_dump(),
                "verifier": {"id": "spec_format", "version": "1"} if verifier else None,
                "on_missing_input": "unknown",
            }
        )

    return PolicyPackage(
        id="team",
        version="1",
        status="active",
        owner="o",
        rules=[rule("SPEC-001", "mandatory", True), rule("DOC-001", "advisory", False)],
    )


def test_policies_combine_team_rules_and_project_findings() -> None:
    findings = [
        _finding(
            "SPEC-001:specs/a.md", "VERIFIED_IN_SCOPE", behavior="policy:spec_format", fid="p1"
        ),
        _finding("SPEC-001:specs/b.md", "VIOLATED", behavior="policy:spec_format", fid="p2"),
        _finding("MAKE-001:Makefile", "VIOLATED", behavior="policy:make_targets", fid="p3"),
        _finding("LOCAL-9:x", "VERIFIED_IN_SCOPE", behavior="policy:x", fid="p4"),
        _finding("R-1", "VIOLATED"),
    ]
    gate = {"status": "fail", "blocking_ids": ["p2", "p3"], "advisory_ids": []}
    rows = {p.rule_id: p for p in build_policies(findings, gate, _team())}
    assert list(rows) == ["SPEC-001", "DOC-001", "LOCAL-9", "MAKE-001"]
    assert (rows["SPEC-001"].origin, rows["SPEC-001"].level, rows["SPEC-001"].has_verifier) == (
        "team",
        "mandatory",
        True,
    )
    assert rows["SPEC-001"].status == "VIOLATED" and rows["SPEC-001"].counts.VERIFIED_IN_SCOPE == 1
    assert rows["DOC-001"].status is None and rows["DOC-001"].has_verifier is False
    assert (rows["MAKE-001"].origin, rows["MAKE-001"].level, rows["MAKE-001"].title) == (
        "project",
        "mandatory",
        None,
    )
    assert rows["LOCAL-9"].level is None and rows["LOCAL-9"].has_verifier is None


def test_policies_without_team_or_gate() -> None:
    assert build_policies([_finding("R-1", "VIOLATED")], None, None) == []
```

- [ ] **Step 3: 失敗を確かめる**

Run: `UV_NO_SYNC=1 uv run pytest tests/unit/services/test_project_overview.py -q`
Expected: FAIL（`ModuleNotFoundError: speclino.services.project_overview`）

- [ ] **Step 4: 組み立てを書く**

```python
"""プロジェクト画面（S45）の組み立て。design/2026-09-30-ui-redesign.md 5.2 節。

最新の完了した検査の成果物（Spec IR・findings・gate・semantic diff）から要求と規約の一覧を作る。
判定はしない（検査器の findings を要求・規約ごとに並べ直すだけ）。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from speclino.domain.finding import Finding, Verdict, VerdictCounts
from speclino.domain.policy import PolicyPackage
from speclino.domain.project_overview import ApprovalState, PolicyView, RequirementView
from speclino.domain.semantic_diff import SemanticDiff
from speclino.domain.spec_ir import Rule, SpecIR

# 重い順（web/src/services/labels.ts の VERDICT_ORDER と同じ）
VERDICT_SEVERITY: tuple[Verdict, ...] = ("VIOLATED", "REVIEW", "UNKNOWN", "VERIFIED_IN_SCOPE")


def is_policy_finding(f: Finding) -> bool:
    return f.behavior_id.startswith("policy:")


def policy_rule_id(f: Finding) -> str:
    """規約 finding の requirement_id は `<rule>:<path>`（PolicyRule.id はコロンを含まない）。"""
    return f.requirement_id.split(":", 1)[0]


def worst_verdict(verdicts: Iterable[Verdict]) -> Verdict | None:
    present = set(verdicts)
    return next((v for v in VERDICT_SEVERITY if v in present), None)


def requirement_verdicts(findings: Iterable[Finding]) -> dict[str, Verdict]:
    grouped: dict[str, list[Verdict]] = {}
    for f in findings:
        if not is_policy_finding(f):
            grouped.setdefault(f.requirement_id, []).append(f.verdict)
    return {rid: v for rid, vs in grouped.items() if (v := worst_verdict(vs)) is not None}


def count_verdicts(verdicts: Iterable[Verdict]) -> VerdictCounts:
    c = Counter(verdicts)
    return VerdictCounts(**{v: c.get(v, 0) for v in VERDICT_SEVERITY})


def _approval(rule: Rule, spec: SpecIR, changed: set[tuple[str, str]]) -> ApprovalState:
    # conformance/engine.py の _approved と同じ条件
    if rule.approval_ref is not None and any(a.id == rule.approval_ref for a in spec.approvals):
        return "approved"
    return "stale" if (spec.behavior_id, rule.id) in changed else "unapproved"


def build_requirements(
    spec_irs: list[SpecIR], findings: list[Finding], diff: SemanticDiff | None
) -> list[RequirementView]:
    verdicts = requirement_verdicts(findings)
    ids: dict[str, list[str]] = {}
    for f in findings:
        if not is_policy_finding(f):
            ids.setdefault(f.requirement_id, []).append(f.id)
    changed = {
        (d.behavior_id, d.rule_id) for d in (diff.rules if diff else []) if d.status == "changed"
    }

    rows: list[RequirementView] = []
    known: set[str] = set()
    for spec in spec_irs:
        for rule in spec.rules:
            known.add(rule.id)
            ref = next((r for r in rule.source_refs if r.path), None)
            rows.append(
                RequirementView(
                    id=rule.id,
                    behavior_id=spec.behavior_id,
                    title=rule.title,
                    spec_path=ref.path if ref else None,
                    line_start=ref.line_start if ref else None,
                    line_end=ref.line_end if ref else None,
                    approval=_approval(rule, spec, changed),
                    tests_declared=len(rule.tests),
                    verdict=verdicts.get(rule.id),
                    finding_ids=sorted(ids.get(rule.id, [])),
                )
            )
    # head の Spec IR から消えた要求（承認なしの削除は stale_approval の finding が残る）
    for f in findings:
        if is_policy_finding(f) or f.requirement_id in known:
            continue
        known.add(f.requirement_id)
        same = [
            g for g in findings if g.requirement_id == f.requirement_id and not is_policy_finding(g)
        ]
        ref = next((r for g in same for r in g.spec_refs if r.path), None)
        rows.append(
            RequirementView(
                id=f.requirement_id,
                behavior_id=f.behavior_id,
                title=None,
                spec_path=ref.path if ref else None,
                line_start=ref.line_start if ref else None,
                line_end=ref.line_end if ref else None,
                approval="stale"
                if any(g.category == "stale_approval" for g in same)
                else "approved",
                tests_declared=0,
                verdict=verdicts.get(f.requirement_id),
                finding_ids=sorted(g.id for g in same),
                removed=True,
            )
        )
    return rows


def build_policies(
    findings: list[Finding], gate: dict[str, Any] | None, team: PolicyPackage | None
) -> list[PolicyView]:
    blocking = set(gate.get("blocking_ids", [])) if gate else set()
    advisory = set(gate.get("advisory_ids", [])) if gate else set()
    grouped: dict[str, list[Finding]] = {}
    for f in findings:
        if is_policy_finding(f):
            grouped.setdefault(policy_rule_id(f), []).append(f)
    team_rules = {r.id: r for r in team.rules} if team else {}

    rows: list[PolicyView] = []
    for rid in [*team_rules, *sorted(k for k in grouped if k not in team_rules)]:
        fs = grouped.get(rid, [])
        rule = team_rules.get(rid)
        if rule is not None:
            rows.append(
                PolicyView(
                    rule_id=rid,
                    origin="team",
                    title=rule.title,
                    level=rule.level,
                    has_verifier=rule.verifier is not None,
                    status=worst_verdict(f.verdict for f in fs),
                    counts=count_verdicts(f.verdict for f in fs),
                )
            )
            continue
        fids = {f.id for f in fs}
        level = "mandatory" if fids & blocking else "advisory" if fids & advisory else None
        rows.append(
            PolicyView(
                rule_id=rid,
                origin="project",
                title=None,
                level=level,
                has_verifier=None,
                status=worst_verdict(f.verdict for f in fs),
                counts=count_verdicts(f.verdict for f in fs),
            )
        )
    return rows
```

- [ ] **Step 5: 通ることを確かめる**

Run: `UV_NO_SYNC=1 uv run pytest tests/unit/services/test_project_overview.py -q && make lint typecheck`
Expected: PASS（9 件）、ruff と mypy strict が通る。`PolicyRule` の必須項目がテストの dict と合わなければ、テストの dict を `domain/policy.py` に合わせる（組み立ての側は変えない）

- [ ] **Step 6: コミット**

```bash
git add src/speclino/domain/project_overview.py src/speclino/services/project_overview.py tests/unit/services/test_project_overview.py
git commit -m "feat(api): プロジェクト画面の要求と規約の組み立て (#55)"
```

---

### Task 2: 読み出しと API（projects・overview）

**Files:**
- Modify: `src/speclino/db/repositories/review_runs.py`（`latest_completed`・`list_for_project` を追加）
- Modify: `src/speclino/services/project_overview.py`（`list_projects`・`load_overview` を追加）
- Create: `src/speclino/api/routes/projects.py`
- Modify: `src/speclino/api/app.py`（`include_router(projects.router)` を `repositories.router` の次に）
- Test: `tests/integration/services/test_projects_api.py`

**Interfaces:**
- Consumes: Task 1 の `build_requirements`・`build_policies`・`count_verdicts`・`requirement_verdicts` と domain の型
- Produces: `ReviewRunRepository.latest_completed(project_id: uuid.UUID) -> ReviewRun | None`、`ReviewRunRepository.list_for_project(project_id: uuid.UUID, limit: int) -> list[ReviewRun]`、`list_projects(session, blobs: BlobStore, slug: str, uid: str) -> list[ProjectSummary]`、`load_spec_irs(blobs, run_id, expected: list[str]) -> list[SpecIR]`（欠けていれば `ArtifactsIncomplete`）、`load_overview(session, blobs: BlobStore, slug: str, uid: str, project_id: uuid.UUID) -> ProjectOverview`、HTTP `GET /api/workspaces/{slug}/projects`（`ProjectSummary[]`）と `GET /api/workspaces/{slug}/projects/{project_id}/overview`（`ProjectOverview`）。どちらも `model_dump(mode="json")`

- [ ] **Step 1: 失敗する統合テストを書く**

```python
"""`api/routes/projects.py` の統合試験（S45、design/2026-09-30-ui-redesign.md 5.2・8 章）。

実 PostgreSQL が必要（`SPECLINO_DATABASE_URL` が未設定なら skip）。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.adapters.localgit.local_code_host import LocalCodeHost
from speclino.adapters.queue.inprocess import InProcessQueue
from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.api.app import create_app
from speclino.api.deps import AppState
from speclino.services.review_run_service import ReviewRunConfig, ReviewRunService
from speclino.settings import Settings
from tests.integration.services.conftest import project_id_of, register_repo, repo_id_of
from tests.integration.services.test_review_run_service import make_demo_repo

_DATABASE_URL = os.environ.get("SPECLINO_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_DATABASE_URL is None, reason="SPECLINO_DATABASE_URL が未設定"),
]

Ctx = tuple[httpx.AsyncClient, str, str, str, str, str, uuid.UUID, Path]
DEV = {"X-Dev-User": "dev"}


@pytest_asyncio.fixture
async def ctx(engine: AsyncEngine, tmp_path: Path) -> AsyncIterator[Ctx]:
    slug = f"e2e-proj-{uuid.uuid4().hex[:8]}"
    repo = f"e2e/{slug}"
    await register_repo(engine, repo, {"dev": "member"}, team=slug)
    base, head, head2 = make_demo_repo(tmp_path / "repo")
    host = LocalCodeHost(repos={repo: tmp_path / "repo"}, repo_ids={repo: repo_id_of(repo)})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    holder: dict[str, ReviewRunService] = {}
    queue = InProcessQueue(lambda rid: holder["svc"].execute(rid), immediate=True)
    blobs = FileSystemBlobStore(tmp_path / "blobs")
    settings = Settings(env="local", role="all")
    service = ReviewRunService(factory, host, queue, blobs, ReviewRunConfig(agent_enabled=False))
    holder["svc"] = service
    app = create_app(settings, AppState(settings, engine, factory, host, queue, blobs, service))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield (
            c,
            slug,
            repo,
            base,
            head,
            head2,
            await project_id_of(engine, repo),
            tmp_path / "blobs",
        )


async def _run(c: httpx.AsyncClient, repo: str, head: str, base: str) -> str:
    res = await c.post(
        "/api/review-runs",
        json={"repo_full_name": repo, "head_sha": head, "base_sha": base},
        headers=DEV,
    )
    assert res.status_code == 202, res.text
    return str(res.json()["run_id"])


async def test_outsiders_get_404(ctx: Ctx, engine: AsyncEngine) -> None:
    c, slug, _, _, _, _, pid, _ = ctx
    stranger = {"X-Dev-User": "stranger"}
    for path in (
        f"/api/workspaces/{slug}/projects",
        f"/api/workspaces/{slug}/projects/{pid}/overview",
    ):
        res = await c.get(path, headers=stranger)
        assert res.status_code == 404 and res.json()["error"]["code"] == "workspace_not_found"
    # 自分のワークスペースの slug で、他のワークスペースの project を指しても 404
    other = f"e2e-other-{uuid.uuid4().hex[:8]}"
    await register_repo(engine, f"e2e/{other}", {"dev": "member"}, team=other)
    res = await c.get(f"/api/workspaces/{other}/projects/{pid}/overview", headers=DEV)
    assert res.status_code == 404


async def test_not_checked_before_any_run(ctx: Ctx) -> None:
    c, slug, repo, _, _, _, pid, _ = ctx
    [summary] = (await c.get(f"/api/workspaces/{slug}/projects", headers=DEV)).json()
    assert (
        summary["repo_full_name"] == repo
        and summary["latest_run"] is None
        and summary["counts"] is None
    )
    body = (await c.get(f"/api/workspaces/{slug}/projects/{pid}/overview", headers=DEV)).json()
    assert body["status"] == "not_checked" and body["requirements"] == [] and body["runs"] == []


async def test_overview_after_a_violating_run(ctx: Ctx) -> None:
    c, slug, repo, base, head, _, pid, _ = ctx
    run_id = await _run(c, repo, head, base)
    body = (await c.get(f"/api/workspaces/{slug}/projects/{pid}/overview", headers=DEV)).json()
    assert body["status"] == "ok" and body["run"]["id"] == run_id
    req = {r["id"]: r for r in body["requirements"]}
    assert (
        req["R-CANCEL-04"]["verdict"] == "VIOLATED" and req["R-CANCEL-04"]["approval"] == "approved"
    )
    assert body["counts"]["VIOLATED"] >= 1
    assert [r["id"] for r in body["runs"]] == [run_id]
    assert all(p["rule_id"] and p["counts"] for p in body["policies"])
    [summary] = (await c.get(f"/api/workspaces/{slug}/projects", headers=DEV)).json()
    assert summary["latest_run"]["id"] == run_id and summary["counts"]["VIOLATED"] >= 1


async def test_removed_requirement_is_stale(ctx: Ctx) -> None:
    c, slug, repo, base, _, head2, pid, _ = ctx
    await _run(c, repo, head2, base)  # head2: R-CANCEL-04 を IR から消したコミット
    body = (await c.get(f"/api/workspaces/{slug}/projects/{pid}/overview", headers=DEV)).json()
    removed = [r for r in body["requirements"] if r["removed"]]
    assert [(r["id"], r["approval"]) for r in removed] == [("R-CANCEL-04", "stale")]


async def test_missing_artifacts_are_unavailable_not_500(ctx: Ctx) -> None:
    c, slug, repo, base, head, _, pid, blobs = ctx
    run_id = await _run(c, repo, head, base)
    for p in (blobs / "runs" / run_id).glob("spec-ir*.json"):
        p.unlink()
    body = (await c.get(f"/api/workspaces/{slug}/projects/{pid}/overview", headers=DEV)).json()
    assert (
        body["status"] == "unavailable"
        and body["run"]["id"] == run_id
        and body["requirements"] == []
    )
```

単体テスト（`tests/unit/services/test_project_overview.py` に足す。blob はファイルの実装を使う）:

```python
import asyncio
import uuid as _uuid

import pytest

from speclino.adapters.storage.fs import FileSystemBlobStore
from speclino.services.project_overview import ArtifactsIncomplete, load_spec_irs


def _put(store: FileSystemBlobStore, run_id: _uuid.UUID, name: str, spec: SpecIR) -> None:
    asyncio.run(store.put(f"runs/{run_id}/{name}", spec.model_dump_json().encode()))


def test_missing_behavior_spec_ir_is_incomplete(tmp_path: Path) -> None:
    store, run_id, spec = FileSystemBlobStore(tmp_path), _uuid.uuid4(), _spec()
    other = spec.model_copy(update={"behavior_id": "refund"})
    _put(store, run_id, "spec-ir.json", spec)  # 複数 behavior でも先頭の spec-ir.json は残る
    _put(store, run_id, f"spec-ir.{spec.behavior_id}.json", spec)
    with pytest.raises(FileNotFoundError):  # refund のファイルが無い
        asyncio.run(load_spec_irs(store, run_id, [spec.behavior_id, "refund"]))
    _put(store, run_id, "spec-ir.refund.json", other)
    assert [
        s.behavior_id
        for s in asyncio.run(load_spec_irs(store, run_id, [spec.behavior_id, "refund"]))
    ] == [spec.behavior_id, "refund"]


def test_single_behavior_must_match_manifest(tmp_path: Path) -> None:
    store, run_id, spec = FileSystemBlobStore(tmp_path), _uuid.uuid4(), _spec()
    _put(store, run_id, "spec-ir.json", spec)
    with pytest.raises(ArtifactsIncomplete):
        asyncio.run(load_spec_irs(store, run_id, ["refund"]))
```


- [ ] **Step 2: 失敗を確かめる**

Run: `make db-up && export SPECLINO_DATABASE_URL=postgresql+asyncpg://speclino:speclino@localhost:5432/speclino && make db-migrate && UV_NO_SYNC=1 uv run pytest tests/integration/services/test_projects_api.py -q`（URL は CI の `ci.yml` と同じ。以降の統合テストも同じ環境変数で走らせる）
Expected: FAIL（404 Not Found。ルートが無い）

- [ ] **Step 3: run の問い合わせを足す**（`db/repositories/review_runs.py` の `ReviewRunRepository` に）

```python
async def latest_completed(self, project_id: uuid.UUID) -> ReviewRun | None:
    """最新の完了した run（S45。awaiting_answer は判定と成果物が出そろっている）。"""
    stmt = (
        select(ReviewRun)
        .where(
            ReviewRun.project_id == project_id,
            ReviewRun.exec_state.in_(("completed", "awaiting_answer")),
        )
        .order_by(ReviewRun.created_at.desc())
        .limit(1)
    )
    return (await self._session.execute(stmt)).scalar_one_or_none()


async def list_for_project(self, project_id: uuid.UUID, limit: int) -> list[ReviewRun]:
    stmt = (
        select(ReviewRun)
        .where(ReviewRun.project_id == project_id)
        .order_by(ReviewRun.created_at.desc())
        .limit(limit)
    )
    return list((await self._session.execute(stmt)).scalars())
```

（`self._session` の名前は同ファイルの既存メソッドに合わせる）

- [ ] **Step 4: 読み出しを書く**（`services/project_overview.py` の末尾に。import は先頭へまとめる）

```python
import json
import uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import Project, ReviewRun
from speclino.db.repositories.findings import FindingRepository
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.db.repositories.projects import ProjectRepository
from speclino.db.repositories.review_runs import ReviewRunRepository
from speclino.domain.project_overview import ProjectOverview, ProjectSummary, RunView
from speclino.ports.blob_store import BlobStore
from speclino.services.workspaces import WorkspaceNotFound, require_member

HISTORY_LIMIT = 20


def run_view(run: ReviewRun) -> RunView:
    return RunView(
        id=str(run.id),
        pr_number=run.pr_number,
        head_sha=run.head_sha,
        exec_state=run.exec_state,
        gate_status=run.gate_status,
        created_at=run.created_at,
    )


async def _findings(session: AsyncSession, run_id: uuid.UUID) -> list[Finding]:
    return [
        Finding.model_validate(r.payload)
        for r in await FindingRepository(session).list_for_run(run_id)
    ]


async def _summary(session: AsyncSession, blobs: BlobStore, project: Project) -> ProjectSummary:
    latest = await ReviewRunRepository(session).latest_completed(project.id)
    counts: VerdictCounts | None = None
    unanalyzed: int | None = None
    if latest is not None:
        verdicts = requirement_verdicts(await _findings(session, latest.id))
        counts = count_verdicts(verdicts.values())
        raw = await _optional_json(blobs, latest.id, "findings.json")
        total = raw.get("coverage", {}).get("requirements_total") if isinstance(raw, dict) else None
        if isinstance(total, int):
            unanalyzed = max(0, total - len(verdicts))
    return ProjectSummary(
        id=str(project.id),
        repo_full_name=project.repo_full_name,
        default_branch=project.default_branch,
        latest_run=run_view(latest) if latest else None,
        counts=counts,
        unanalyzed=unanalyzed,
    )


async def list_projects(
    session: AsyncSession, blobs: BlobStore, slug: str, uid: str
) -> list[ProjectSummary]:
    await require_member(session, slug, uid)
    stmt = select(Project).where(Project.team == slug).order_by(Project.repo_full_name)
    return [await _summary(session, blobs, p) for p in (await session.execute(stmt)).scalars()]


async def _json(blobs: BlobStore, run_id: uuid.UUID, name: str) -> Any:
    return json.loads(await blobs.get(f"runs/{run_id}/{name}"))


class ArtifactsIncomplete(LookupError):
    """manifest にある behavior の Spec IR が揃っていない。"""


async def load_spec_irs(blobs: BlobStore, run_id: uuid.UUID, expected: list[str]) -> list[SpecIR]:
    """run の Spec IR を behavior ごとに読む（services/check_pipeline.py の write_result）。

    behavior が複数なら `spec-ir.<behavior>.json`、1 つなら `spec-ir.json`（複数のときも先頭の
    behavior の `spec-ir.json` が残るので、見つかったファイルからは数を決めない）。`expected` は
    manifest の `analysis[].behavior_id`。空（S41c より前の run）なら見つかったファイルに従う。
    """
    if not expected:
        names = [k.rsplit("/", 1)[-1] for k in await blobs.list(f"runs/{run_id}/")]
        per = sorted(
            n
            for n in names
            if n.startswith("spec-ir.") and n.endswith(".json") and n != "spec-ir.json"
        )
        targets = per or ["spec-ir.json"]
    elif len(expected) == 1:
        targets = ["spec-ir.json"]
    else:
        targets = [f"spec-ir.{b}.json" for b in expected]
    specs = [SpecIR.model_validate(await _json(blobs, run_id, n)) for n in targets]
    if expected and sorted(s.behavior_id for s in specs) != sorted(expected):
        raise ArtifactsIncomplete(str(run_id))
    return specs


async def _optional_json(blobs: BlobStore, run_id: uuid.UUID, name: str) -> Any | None:
    try:
        return await _json(blobs, run_id, name)
    except (FileNotFoundError, OSError):
        return None


async def load_overview(
    session: AsyncSession, blobs: BlobStore, slug: str, uid: str, project_id: uuid.UUID
) -> ProjectOverview:
    await require_member(session, slug, uid)
    project = await ProjectRepository(session).get(project_id)
    if project is None or project.team != slug:
        raise WorkspaceNotFound(slug)
    runs = ReviewRunRepository(session)
    summary = await _summary(session, blobs, project)
    history = [run_view(r) for r in await runs.list_for_project(project.id, HISTORY_LIMIT)]
    latest = await runs.latest_completed(project.id)
    if latest is None:
        return ProjectOverview(status="not_checked", project=summary, runs=history)
    manifest = latest.manifest or {}
    expected = [
        a["behavior_id"]
        for a in manifest.get("analysis", [])
        if isinstance(a, dict) and "behavior_id" in a
    ]
    try:
        spec_irs = await load_spec_irs(blobs, latest.id, expected)
        raw_diff = await _optional_json(blobs, latest.id, "semantic-diff.json")
        diff = SemanticDiff.model_validate(raw_diff) if raw_diff is not None else None
        gate = await _optional_json(blobs, latest.id, "gate.json")
    except (FileNotFoundError, OSError, ValueError, ValidationError, ArtifactsIncomplete):
        return ProjectOverview(
            status="unavailable", project=summary, run=run_view(latest), runs=history
        )

    team_name, revision = manifest.get("policy_team"), manifest.get("policy_revision")
    team = None
    if isinstance(team_name, str) and isinstance(revision, int):
        found = await PolicyRevisionRepository(session).revision_at(team_name, revision)
        team = found.package if found is not None else None

    findings = await _findings(session, latest.id)
    requirements = build_requirements(spec_irs, findings, diff)
    present = [r for r in requirements if not r.removed]
    return ProjectOverview(
        status="ok",
        project=summary,
        run=run_view(latest),
        counts=count_verdicts(r.verdict for r in present if r.verdict is not None),
        unanalyzed=sum(1 for r in present if r.verdict is None)
        + sum(len(s.unextracted) for s in spec_irs),
        unextracted=[u for s in spec_irs for u in s.unextracted],
        unapproved=sum(1 for r in requirements if r.approval != "approved"),
        policy_team=team_name if isinstance(team_name, str) else None,
        policy_revision=revision if isinstance(revision, int) else None,
        requirements=requirements,
        policies=build_policies(findings, gate, team),
        runs=history,
    )
```

`_spec_irs` の `spec-ir.json` が無い（`FileNotFoundError`）ときも `unavailable` になる。`ValueError` は `json.loads` の失敗を含む。

- [ ] **Step 5: ルートを書く**（`api/routes/projects.py`）

```python
"""プロジェクト画面（S45）の API。組み立ては `services/project_overview.py`。

メンバーだけが見られる。それ以外（未所属・他のワークスペースの project）は存在しない場合と同じ 404。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.domain.workspace import AuthUser
from speclino.services import project_overview
from speclino.services.workspaces import WorkspaceNotFound

router = APIRouter(
    prefix="/api/workspaces/{slug}/projects", tags=["projects"], responses=error_responses(401)
)


def _not_found(e: WorkspaceNotFound) -> Exception:
    return api_error(404, "workspace_not_found", "ワークスペースが無い")


@router.get("", responses=error_responses(404))
async def list_projects(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> list[dict[str, Any]]:
    try:
        async with state.session_factory() as s:
            rows = await project_overview.list_projects(s, state.blobs, slug, user.uid)
    except WorkspaceNotFound as e:
        raise _not_found(e) from e
    return [r.model_dump(mode="json") for r in rows]


@router.get("/{project_id}/overview", responses=error_responses(404))
async def get_overview(
    slug: str,
    project_id: uuid.UUID,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s:
            overview = await project_overview.load_overview(
                s, state.blobs, slug, user.uid, project_id
            )
    except WorkspaceNotFound as e:
        raise _not_found(e) from e
    return overview.model_dump(mode="json")
```

`api/app.py` で `from speclino.api.routes import projects` を足し、`app.include_router(repositories.router)` の次に `app.include_router(projects.router)`（同じ役割の分岐の中）。

- [ ] **Step 6: 通ることを確かめる**

Run: `UV_NO_SYNC=1 uv run pytest tests/integration/services/test_projects_api.py tests/unit/services/test_project_overview.py -q && make check`
Expected: PASS（統合 5 件・単体 11 件）。`make check`（lint・typecheck・test・selfcheck）が通る。`conformance/`・`analysis/` の import 禁止テストも通る

- [ ] **Step 7: コミット**

```bash
git add src/speclino/db/repositories/review_runs.py src/speclino/services/project_overview.py src/speclino/api/routes/projects.py src/speclino/api/app.py tests/integration/services/test_projects_api.py
git commit -m "feat(api): プロジェクトの一覧と overview の API (#55)"
```

---

### Task 3: 画面側の型・API・一覧のキャッシュ（M-6）

**Files:**
- Create: `web/src/types/project.ts`
- Create: `web/src/services/requestCache.ts`、Test: `web/src/services/requestCache.test.ts`
- Create: `web/src/api/projects.ts`、`web/src/api/mock/projects.ts`
- Create: `web/src/hooks/useWorkspaceProjects.ts`
- Modify: `web/src/pages/GithubCallback.tsx`（登録が成功したら `invalidateProjects(slug)`）

**Interfaces:**
- Produces: 型 `ApprovalState`、`PolicyOrigin`、`PolicyLevel`、`VerdictCounts`、`ProjectRun`、`ProjectSummary`、`RequirementView`、`PolicyView`、`ProjectOverview`、`RequirementFilter`、`NextActionKind`、`NextAction`。`createRequestCache<T>(fetcher: (key: string) => Promise<T>): { get(key: string): Promise<T>; invalidate(key?: string): void }`。`listProjects(slug): Promise<ProjectSummary[]>`（キャッシュ）、`invalidateProjects(slug?: string): void`、`getProjectOverview(slug, projectId): Promise<ProjectOverview>`。`useWorkspaceProjects(slug: string | undefined): { projects: ProjectSummary[] | null; error: string | null; retry: () => void }`

- [ ] **Step 1: 型を書く**（`web/src/types/project.ts`。API の JSON と同じ形）

```ts
/** プロジェクト画面（S45）の型。src/speclino/domain/project_overview.py に対応させる。 */

import type { Verdict } from './finding'
import type { ExecState, GateStatus } from './run'

export type ApprovalState = 'approved' | 'unapproved' | 'stale'
export type PolicyOrigin = 'team' | 'project'
export type PolicyLevel = 'mandatory' | 'advisory'
export type VerdictCounts = Record<Verdict, number>

export interface ProjectRun {
  id: string
  pr_number: number | null
  head_sha: string
  exec_state: ExecState
  gate_status: GateStatus | null
  created_at: string | null
}

export interface ProjectSummary {
  id: string
  repo_full_name: string
  default_branch: string
  latest_run: ProjectRun | null
  counts: VerdictCounts | null
  unanalyzed: number | null
}

export interface RequirementView {
  id: string
  behavior_id: string
  title: string | null
  spec_path: string | null
  line_start: number | null
  line_end: number | null
  approval: ApprovalState
  tests_declared: number
  verdict: Verdict | null
  finding_ids: string[]
  removed: boolean
}

export interface PolicyView {
  rule_id: string
  origin: PolicyOrigin
  title: string | null
  level: PolicyLevel | null
  has_verifier: boolean | null
  status: Verdict | null
  counts: VerdictCounts
}

export interface ProjectOverview {
  status: 'ok' | 'not_checked' | 'unavailable'
  project: ProjectSummary
  run: ProjectRun | null
  counts: VerdictCounts | null
  unanalyzed: number
  unextracted: string[]
  unapproved: number
  policy_team: string | null
  policy_revision: number | null
  requirements: RequirementView[]
  policies: PolicyView[]
  runs: ProjectRun[]
}

export type RequirementFilter = 'all' | 'violated' | 'unknown' | 'review' | 'unapproved'

export type NextActionKind = 'violated' | 'stale' | 'review' | 'unknown' | 'unapproved'

export interface NextAction {
  kind: NextActionKind
  requirementId: string
  title: string | null
  hint: string
}
```

- [ ] **Step 2: キャッシュの失敗するテストを書く**（`requestCache.test.ts`）

```ts
import { describe, expect, it, vi } from 'vitest'
import { createRequestCache } from './requestCache'

describe('createRequestCache', () => {
  it('同じキーは 1 回だけ取りに行く', async () => {
    const fetcher = vi.fn(async (k: string) => `v:${k}`)
    const cache = createRequestCache(fetcher)
    const [a, b] = await Promise.all([cache.get('x'), cache.get('x')])
    expect([a, b]).toEqual(['v:x', 'v:x'])
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('失敗したら覚えず、次は取り直す', async () => {
    let n = 0
    const cache = createRequestCache(async () => {
      n += 1
      if (n === 1) throw new Error('boom')
      return 'ok'
    })
    await expect(cache.get('x')).rejects.toThrow('boom')
    await expect(cache.get('x')).resolves.toBe('ok')
  })

  it('invalidate で取り直す（キー指定と全部）', async () => {
    const fetcher = vi.fn(async (k: string) => k)
    const cache = createRequestCache(fetcher)
    await cache.get('a')
    await cache.get('b')
    cache.invalidate('a')
    await cache.get('a')
    await cache.get('b')
    expect(fetcher).toHaveBeenCalledTimes(3)
    cache.invalidate()
    await cache.get('b')
    expect(fetcher).toHaveBeenCalledTimes(4)
  })
})
```

- [ ] **Step 3: 失敗を確かめる**

Run: `cd web && npx vitest run src/services/requestCache.test.ts`
Expected: FAIL（`./requestCache` が無い）

- [ ] **Step 4: キャッシュを書く**

```ts
/** キーごとに取得の Promise を覚える（同じ一覧を画面の複数箇所で二重に取らない）。失敗は覚えない。 */
export function createRequestCache<T>(fetcher: (key: string) => Promise<T>) {
  const entries = new Map<string, Promise<T>>()
  return {
    get(key: string): Promise<T> {
      const hit = entries.get(key)
      if (hit) return hit
      const p = fetcher(key)
      entries.set(key, p)
      p.catch(() => {
        if (entries.get(key) === p) entries.delete(key)
      })
      return p
    },
    invalidate(key?: string): void {
      if (key === undefined) entries.clear()
      else entries.delete(key)
    },
  }
}
```

- [ ] **Step 5: API とモックを書く**

`web/src/api/mock/projects.ts`（`example-org/...` だけを使う。要求 4 件: 違反 1・未検証 1・未承認 1・適合 1、規約 2 件、run 2 件）:

```ts
import type { ProjectOverview, ProjectSummary } from '../../types/project'

// 既存のモック（fixtures.ts）の失敗した run の ID。要求の行を開くとその findings（反例つき）が出る
const RUN = {
  id: '9871e2ff-4602-4382-b512-0bacd3006ae2', pr_number: 12, head_sha: 'a1b2c3d4e5f6', exec_state: 'completed' as const,
  gate_status: 'fail' as const, created_at: '2026-09-30T01:00:00Z',
}

export const MOCK_PROJECTS: ProjectSummary[] = [
  {
    id: '00000000-0000-4000-8000-000000000001', repo_full_name: 'example-org/orders', default_branch: 'main',
    latest_run: RUN, counts: { VERIFIED_IN_SCOPE: 1, VIOLATED: 1, UNKNOWN: 1, REVIEW: 1 }, unanalyzed: 0,
  },
  {
    id: '00000000-0000-4000-8000-000000000002', repo_full_name: 'example-org/a-repository-with-a-very-long-name-for-layout',
    default_branch: 'main', latest_run: null, counts: null, unanalyzed: null,
  },
]

export const MOCK_OVERVIEW: ProjectOverview = {
  status: 'ok',
  project: MOCK_PROJECTS[0],
  run: RUN,
  counts: { VERIFIED_IN_SCOPE: 1, VIOLATED: 1, UNKNOWN: 1, REVIEW: 1 },
  unanalyzed: 0,
  unextracted: [],
  unapproved: 1,
  policy_team: 'example-team',
  policy_revision: 3,
  requirements: [
    { id: 'R-CANCEL-01', behavior_id: 'cancel_order', title: '未発送ならキャンセルできる', spec_path: 'specs/cancel_order.md', line_start: 5, line_end: 8, approval: 'approved', tests_declared: 1, verdict: 'VERIFIED_IN_SCOPE', finding_ids: ['f1'], removed: false },
    { id: 'R-CANCEL-04', behavior_id: 'cancel_order', title: '発送済みは在庫を変えない', spec_path: 'specs/cancel_order.md', line_start: 20, line_end: 24, approval: 'approved', tests_declared: 2, verdict: 'VIOLATED', finding_ids: ['f4'], removed: false },
    { id: 'R-CANCEL-05', behavior_id: 'cancel_order', title: '返金は 1 回だけ', spec_path: 'specs/cancel_order.md', line_start: 26, line_end: 29, approval: 'approved', tests_declared: 0, verdict: 'UNKNOWN', finding_ids: ['f5'], removed: false },
    { id: 'R-CANCEL-06', behavior_id: 'cancel_order', title: '監査ログを残す', spec_path: null, line_start: null, line_end: null, approval: 'unapproved', tests_declared: 1, verdict: 'REVIEW', finding_ids: ['f6'], removed: false },
  ],
  policies: [
    { rule_id: 'SPEC-001', origin: 'team', title: '仕様に要求 ID を付ける', level: 'mandatory', has_verifier: true, status: 'VERIFIED_IN_SCOPE', counts: { VERIFIED_IN_SCOPE: 3, VIOLATED: 0, UNKNOWN: 0, REVIEW: 0 } },
    { rule_id: 'DOC-001', origin: 'team', title: '設計判断を記録する', level: 'advisory', has_verifier: false, status: null, counts: { VERIFIED_IN_SCOPE: 0, VIOLATED: 0, UNKNOWN: 0, REVIEW: 0 } },
  ],
  runs: [RUN, { ...RUN, id: 'b2c3d4e5-6789-4abc-9def-0123456789ab', pr_number: 11, gate_status: 'pass', created_at: '2026-09-29T01:00:00Z' }],
}
```

`web/src/api/projects.ts`:

```ts
/** プロジェクト（S45）のエンドポイント関数。一覧は slug ごとにキャッシュする。VITE_MOCK=1 のときは固定値。 */

import { apiFetch } from './client'
import { MOCK_OVERVIEW, MOCK_PROJECTS } from './mock/projects'
import { createRequestCache } from '../services/requestCache'
import type { ProjectOverview, ProjectSummary } from '../types/project'

const USE_MOCK = import.meta.env.VITE_MOCK === '1'

const projectsCache = createRequestCache<ProjectSummary[]>((slug) =>
  USE_MOCK
    ? Promise.resolve(MOCK_PROJECTS)
    : apiFetch<ProjectSummary[]>(`/api/workspaces/${encodeURIComponent(slug)}/projects`),
)

export function listProjects(slug: string): Promise<ProjectSummary[]> {
  return projectsCache.get(slug)
}

export function invalidateProjects(slug?: string): void {
  projectsCache.invalidate(slug)
}

export async function getProjectOverview(slug: string, projectId: string): Promise<ProjectOverview> {
  if (USE_MOCK) return { ...MOCK_OVERVIEW, project: MOCK_PROJECTS.find((p) => p.id === projectId) ?? MOCK_OVERVIEW.project }
  return apiFetch<ProjectOverview>(
    `/api/workspaces/${encodeURIComponent(slug)}/projects/${encodeURIComponent(projectId)}/overview`,
  )
}
```

`web/src/hooks/useWorkspaceProjects.ts`（前の slug の結果は返さない = M-6）:

```ts
import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '@/api/errors'
import { invalidateProjects, listProjects } from '@/api/projects'
import type { ProjectSummary } from '@/types/project'

interface Loaded {
  slug: string
  projects: ProjectSummary[] | null
  error: string | null
}

/** slug のワークスペースのプロジェクト一覧。slug が変わった直後は前の一覧を返さず null。 */
export function useWorkspaceProjects(slug: string | undefined) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!slug) return
    let alive = true
    listProjects(slug)
      .then((projects) => alive && setLoaded({ slug, projects, error: null }))
      .catch((err: unknown) => {
        if (alive) setLoaded({ slug, projects: null, error: err instanceof ApiError ? err.message : 'プロジェクトの一覧を取得できませんでした' })
      })
    return () => {
      alive = false
    }
  }, [slug, attempt])

  const retry = useCallback(() => {
    if (slug) invalidateProjects(slug)
    setLoaded(null)
    setAttempt((n) => n + 1)
  }, [slug])

  const current = loaded && loaded.slug === slug ? loaded : null
  return { projects: current?.projects ?? null, error: current?.error ?? null, retry }
}
```

`GithubCallback.tsx`: 登録結果（`RegistrationResult` の `slug`）を受け取った直後に `invalidateProjects(result.slug)` を呼ぶ（成功時だけ）。

- [ ] **Step 6: 通ることを確かめる**

Run: `cd web && npm test && npx tsc -b && npm run lint`
Expected: PASS（requestCache 3 件を含む全件）、型・lint が通る

- [ ] **Step 7: コミット**

```bash
git add web/src/types/project.ts web/src/services/requestCache.ts web/src/services/requestCache.test.ts web/src/api/projects.ts web/src/api/mock/projects.ts web/src/hooks/useWorkspaceProjects.ts web/src/pages/GithubCallback.tsx
git commit -m "feat(web): プロジェクト一覧の API とキャッシュ (#55)"
```

---

### Task 4: 画面の規則（次にやること・要求の絞り込み・健康状態）

**Files:**
- Create: `web/src/services/nextActions.ts`、Test: `web/src/services/nextActions.test.ts`
- Create: `web/src/services/requirements.ts`、Test: `web/src/services/requirements.test.ts`
- Create: `web/src/services/health.ts`、Test: `web/src/services/health.test.ts`

**Interfaces:**
- Consumes: Task 3 の `RequirementView`・`RequirementFilter`・`NextAction`・`VerdictCounts`
- Produces: `nextActions(reqs: RequirementView[], limit?: number): NextAction[]`、`matchesFilter(r: RequirementView, f: RequirementFilter): boolean`、`filterCounts(reqs: RequirementView[]): Record<RequirementFilter, number>`、`groupBySpec(reqs: RequirementView[]): RequirementGroup[]`（`interface RequirementGroup { key: string; specPath: string | null; behaviorId: string; items: RequirementView[] }` を `types/project.ts` に足す）、`healthRatio(counts: VerdictCounts | null, unanalyzed?: number): number | null`

- [ ] **Step 1: 失敗するテストを書く**

`nextActions.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { nextActions } from './nextActions'
import type { RequirementView } from '@/types/project'

function req(id: string, over: Partial<RequirementView>): RequirementView {
  return {
    id, behavior_id: 'b', title: null, spec_path: null, line_start: null, line_end: null,
    approval: 'approved', tests_declared: 0, verdict: 'VERIFIED_IN_SCOPE', finding_ids: [], removed: false, ...over,
  }
}

describe('nextActions', () => {
  it('違反 → 承認切れ → 要確認 → 未検証 → 未承認 の順', () => {
    const reqs = [
      req('U', { approval: 'unapproved', verdict: 'REVIEW' }),
      req('K', { verdict: 'UNKNOWN' }),
      req('Q', { verdict: 'REVIEW' }),
      req('S', { approval: 'stale', verdict: 'REVIEW' }),
      req('V', { verdict: 'VIOLATED' }),
      req('OK', {}),
    ]
    expect(nextActions(reqs).map((a) => [a.kind, a.requirementId])).toEqual([
      ['violated', 'V'], ['stale', 'S'], ['review', 'Q'], ['unknown', 'K'], ['unapproved', 'U'],
    ])
  })

  it('未承認の要確認は「要確認」ではなく「未承認」に入る', () => {
    expect(nextActions([req('U', { approval: 'unapproved', verdict: 'REVIEW' })]).map((a) => a.kind)).toEqual(['unapproved'])
  })

  it('最大 5 件、同じ要求は 1 回だけ', () => {
    const reqs = Array.from({ length: 8 }, (_, i) => req(`V${i}`, { verdict: 'VIOLATED' }))
    const out = nextActions(reqs)
    expect(out).toHaveLength(5)
    expect(new Set(out.map((a) => a.requirementId)).size).toBe(5)
  })

  it('何も無ければ空', () => {
    expect(nextActions([req('OK', {})])).toEqual([])
  })
})
```

`requirements.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { filterCounts, groupBySpec, matchesFilter } from './requirements'
import type { RequirementView } from '@/types/project'

function req(id: string, over: Partial<RequirementView>): RequirementView {
  return {
    id, behavior_id: 'b', title: null, spec_path: null, line_start: null, line_end: null,
    approval: 'approved', tests_declared: 0, verdict: 'VERIFIED_IN_SCOPE', finding_ids: [], removed: false, ...over,
  }
}

describe('requirements', () => {
  const reqs = [
    req('V', { verdict: 'VIOLATED', spec_path: 'specs/a.md' }),
    req('K', { verdict: 'UNKNOWN', spec_path: 'specs/a.md' }),
    req('N', { verdict: null, behavior_id: 'other' }),
    req('Q', { verdict: 'REVIEW', approval: 'stale', spec_path: 'specs/b.md' }),
  ]

  it('未検証の絞り込みは未解析（判定なし）も含む', () => {
    expect(reqs.filter((r) => matchesFilter(r, 'unknown')).map((r) => r.id)).toEqual(['K', 'N'])
  })

  it('未承認の絞り込みは承認切れも含む', () => {
    expect(reqs.filter((r) => matchesFilter(r, 'unapproved')).map((r) => r.id)).toEqual(['Q'])
  })

  it('件数', () => {
    expect(filterCounts(reqs)).toEqual({ all: 4, violated: 1, unknown: 2, review: 1, unapproved: 1 })
  })

  it('仕様ファイルごと（無ければ behavior ごと）に、出てきた順でまとめる', () => {
    expect(groupBySpec(reqs).map((g) => [g.key, g.items.map((r) => r.id)])).toEqual([
      ['specs/a.md', ['V', 'K']], ['other', ['N']], ['specs/b.md', ['Q']],
    ])
  })
})
```

`health.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { healthRatio } from './health'

describe('healthRatio', () => {
  it('適合の割合', () => {
    expect(healthRatio({ VERIFIED_IN_SCOPE: 3, VIOLATED: 1, UNKNOWN: 0, REVIEW: 0 })).toBe(0.75)
  })
  it('未解析も分母に入れる（抽出できなかった要求を落とさない）', () => {
    expect(healthRatio({ VERIFIED_IN_SCOPE: 1, VIOLATED: 0, UNKNOWN: 0, REVIEW: 0 }, 1)).toBe(0.5)
  })
  it('判定が無ければ null（0% と言わない）', () => {
    expect(healthRatio(null)).toBeNull()
    expect(healthRatio({ VERIFIED_IN_SCOPE: 0, VIOLATED: 0, UNKNOWN: 0, REVIEW: 0 })).toBeNull()
  })
})
```

- [ ] **Step 2: 失敗を確かめる**

Run: `cd web && npx vitest run src/services/nextActions.test.ts src/services/requirements.test.ts src/services/health.test.ts`
Expected: FAIL（3 ファイルとも import できない）

- [ ] **Step 3: 実装する**

`nextActions.ts`:

```ts
/** 「次にやること」（design 5.3）。検査結果から決まった規則で並べ、LLM は使わない。 */

import type { NextAction, NextActionKind, RequirementView } from '@/types/project'

const RULES: { kind: NextActionKind; match: (r: RequirementView) => boolean; hint: string }[] = [
  { kind: 'violated', match: (r) => r.verdict === 'VIOLATED', hint: '反例を見る' },
  { kind: 'stale', match: (r) => r.approval === 'stale', hint: '仕様の PR で再承認する' },
  { kind: 'review', match: (r) => r.verdict === 'REVIEW' && r.approval === 'approved', hint: '人の確認が要る質問に答える' },
  { kind: 'unknown', match: (r) => r.verdict === 'UNKNOWN', hint: 'エージェントの調査の結果を見る' },
  { kind: 'unapproved', match: (r) => r.approval === 'unapproved', hint: '仕様の PR で承認する' },
]

export function nextActions(reqs: RequirementView[], limit = 5): NextAction[] {
  const out: NextAction[] = []
  const used = new Set<string>()
  for (const rule of RULES) {
    for (const r of reqs) {
      if (out.length >= limit) return out
      if (used.has(r.id) || !rule.match(r)) continue
      used.add(r.id)
      out.push({ kind: rule.kind, requirementId: r.id, title: r.title, hint: rule.hint })
    }
  }
  return out
}
```

`requirements.ts`:

```ts
/** 要求の一覧の絞り込みとまとめ方（design 5.1）。 */

import type { RequirementFilter, RequirementGroup, RequirementView } from '@/types/project'

export const REQUIREMENT_FILTERS: RequirementFilter[] = ['all', 'violated', 'unknown', 'review', 'unapproved']

export function matchesFilter(r: RequirementView, f: RequirementFilter): boolean {
  switch (f) {
    case 'all':
      return true
    case 'violated':
      return r.verdict === 'VIOLATED'
    case 'unknown':
      return r.verdict === 'UNKNOWN' || r.verdict === null
    case 'review':
      return r.verdict === 'REVIEW'
    case 'unapproved':
      return r.approval !== 'approved'
  }
}

export function filterCounts(reqs: RequirementView[]): Record<RequirementFilter, number> {
  return Object.fromEntries(
    REQUIREMENT_FILTERS.map((f) => [f, reqs.filter((r) => matchesFilter(r, f)).length]),
  ) as Record<RequirementFilter, number>
}

export function groupBySpec(reqs: RequirementView[]): RequirementGroup[] {
  const groups = new Map<string, RequirementGroup>()
  for (const r of reqs) {
    const key = r.spec_path ?? r.behavior_id
    let g = groups.get(key)
    if (!g) {
      g = { key, specPath: r.spec_path, behaviorId: r.behavior_id, items: [] }
      groups.set(key, g)
    }
    g.items.push(r)
  }
  return [...groups.values()]
}
```

`health.ts`:

```ts
import type { VerdictCounts } from '@/types/project'

/** 適合の割合（0〜1）。未解析も分母に入れる。要求が 1 件も無ければ null（0% と言わない）。 */
export function healthRatio(counts: VerdictCounts | null, unanalyzed = 0): number | null {
  if (!counts) return null
  const total = counts.VERIFIED_IN_SCOPE + counts.VIOLATED + counts.UNKNOWN + counts.REVIEW + unanalyzed
  return total === 0 ? null : counts.VERIFIED_IN_SCOPE / total
}
```

`types/project.ts` に足す:

```ts
export interface RequirementGroup {
  key: string
  specPath: string | null
  behaviorId: string
  items: RequirementView[]
}
```

- [ ] **Step 4: 通ることを確かめる**

Run: `cd web && npm test && npx tsc -b && npm run lint`
Expected: PASS（新しい 10 件を含む全件）

- [ ] **Step 5: コミット**

```bash
git add web/src/services/nextActions.ts web/src/services/nextActions.test.ts web/src/services/requirements.ts web/src/services/requirements.test.ts web/src/services/health.ts web/src/services/health.test.ts web/src/types/project.ts
git commit -m "feat(web): 次にやること・要求の絞り込み・健康状態の規則 (#55)"
```

---

### Task 5: ワークスペースのホーム（M-1）と、ピル・⌘K の一覧の共有（M-6）

**Files:**
- Modify: `web/src/services/paths.ts`（`workspace(slug)` を追加）、Test: `web/src/services/paths.test.ts`
- Modify: `web/src/services/commands.ts`（`repositories` → `projects: Record<string, ProjectSummary[]>`、ワークスペースのホームを足す）、Test: `web/src/services/commands.test.ts`
- Create: `web/src/components/project/HealthRing.tsx`
- Modify: `web/src/App.tsx`（`/w/:slug` → `Home`、`/app` → `HomeRedirect`）
- Create: `web/src/pages/HomeRedirect.tsx`
- Modify: `web/src/pages/Home.tsx`、`web/src/components/shell/WorkspacePill.tsx`、`web/src/components/shell/ProjectPill.tsx`、`web/src/components/shell/CommandPalette.tsx`

**Interfaces:**
- Consumes: Task 3 の `useWorkspaceProjects`・`listProjects`・`ProjectSummary`、Task 4 の `healthRatio`
- Produces: `paths.workspace(slug: string): string`（`/w/<slug>`）、`HealthRing({ ratio, size }: { ratio: number | null; size?: 'sm' | 'lg' })`、`buildCommands({ me, projects })`

- [ ] **Step 1: 失敗するテストを書く**

`paths.test.ts` に:

```ts
  it('ワークスペースのホーム', () => {
    expect(paths.workspace('acme team')).toBe('/w/acme%20team')
  })
```

`commands.test.ts` の既存の `repositories: {...}` を `projects: {...}`（`ProjectSummary` の形。`id`・`default_branch`・`latest_run: null`・`counts: null`・`unanalyzed: null` を埋める）に書き換え、次を足す:

```ts
  it('ワークスペースごとにホームへの候補がある', () => {
    const me = { uid: 'u', email: null, email_verified: false, workspaces: [{ slug: 'ws', name: 'Team', role: 'member' as const }] }
    const cmds = buildCommands({ me, projects: {} })
    expect(cmds.find((c) => c.id === 'workspace:ws')).toMatchObject({ label: 'Team のホーム', path: '/w/ws', group: 'ワークスペース' })
  })
```

- [ ] **Step 2: 失敗を確かめる**

Run: `cd web && npx vitest run src/services/paths.test.ts src/services/commands.test.ts`
Expected: FAIL（`paths.workspace` が無い、`projects` を受けない）

- [ ] **Step 3: paths と commands を直す**

`paths.ts` に `workspace: (slug: string) => \`/w/${e(slug)}\`,` を `home` の次に足す。`commands.ts` は入力を `{ me: Me | null; projects: Record<string, ProjectSummary[]> }` にし、各ワークスペースで最初に

```ts
    commands.push({ id: `workspace:${ws.slug}`, label: `${label} のホーム`, group: 'ワークスペース', path: paths.workspace(ws.slug) })
```

を足し、プロジェクトの候補は `input.projects[ws.slug]` の `repo_full_name` から作る（今の `repositories` の処理と同じ）。

- [ ] **Step 4: テストが通ることを確かめる**

Run: `cd web && npx vitest run src/services/paths.test.ts src/services/commands.test.ts`
Expected: PASS

- [ ] **Step 5: 画面を直す**

`HealthRing.tsx`（色はトークン。判定が無いときは「—」）:

```tsx
import { cn } from '@/lib/utils'

/** 健康状態のリング（適合の割合）。ratio が null なら判定なし。 */
export function HealthRing({ ratio, size = 'sm' }: { ratio: number | null; size?: 'sm' | 'lg' }) {
  const pct = ratio === null ? 0 : Math.round(ratio * 100)
  const outer = size === 'lg' ? 'size-24' : 'size-12'
  const inner = size === 'lg' ? 'size-18 text-xl' : 'size-9 text-xs'
  return (
    <div
      className={cn('grid shrink-0 place-items-center rounded-full', outer)}
      style={{ background: ratio === null ? 'var(--muted)' : `conic-gradient(var(--ok) 0 ${pct}%, var(--muted) ${pct}% 100%)` }}
      role="img"
      aria-label={ratio === null ? '判定なし' : `適合 ${pct}%`}
    >
      <span className={cn('grid place-items-center rounded-full bg-card font-bold', inner)}>
        {ratio === null ? '—' : pct}
      </span>
    </div>
  )
}
```

`HomeRedirect.tsx`:

```tsx
import { Navigate } from 'react-router-dom'
import { LoadingState } from '@/components/LoadingState'
import { useMe } from '@/hooks/useMe'
import { paths } from '@/services/paths'
import { Home } from './Home'

/** `/app`: 所属の先頭のワークスペースのホームへ。所属が無ければその場で uid を出す（Home が出す）。 */
export function HomeRedirect() {
  const { me } = useMe()
  if (!me) return <LoadingState />
  const first = me.workspaces[0]
  return first ? <Navigate to={paths.workspace(first.slug)} replace /> : <Home />
}
```

`App.tsx`: `<Route path="/app" element={<HomeRedirect />} />` と `<Route path="/w/:slug" element={<Home />} />` を置く（`/w/:slug/p/...` と `/w/:slug/settings` より前でも後でもよい。React Router は具体的なものを優先する）。

`Home.tsx`:
- `useParams<{ slug?: string }>()` の slug があって `me.workspaces` に無ければ `<Panel>` で「このワークスペースは見つかりません」と `paths.home()` へのリンクを出す（Review Focus 1）。`useCurrentWorkspace` の先頭への落とし込みに頼らない
- リポジトリの一覧は `listRepositories` をやめて `useWorkspaceProjects(slug)` を使う。取得中は `LoadingState`、失敗は `ErrorState onRetry={retry}`、0 件は今の文言
- カードは `HealthRing ratio={healthRatio(p.counts, p.unanalyzed ?? 0)}`、名前・`repo_full_name`（`truncate`）、件数（違反・未検証・要確認の数。`counts` が null なら「まだ検査されていない」）、`latest_run` があれば `GateBadge`。カードの `Link` は `min-w-0`、グリッドは `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3`
- 「最近の検査」は今のワークスペースのプロジェクトに絞る（`runs.filter((r) => names.has(r.repo_full_name ?? ''))`、`names` は `projects` の `repo_full_name` の集合。`projects` が null の間は絞らずに読み込み中）

`WorkspacePill.tsx`: 切り替え先を `paths.settings(slug)` から `paths.workspace(slug)` に。

`ProjectPill.tsx`: `listRepositories` と自前の state をやめて `const { projects } = useWorkspaceProjects(slug)` を使い、`projects ?? []` を並べる（`repo_full_name` で）。

`CommandPalette.tsx`: `listRepositories` を `listProjects` に替え、`buildCommands({ me, projects })` に渡す。

- [ ] **Step 6: 通ることを確かめる**

Run: `cd web && npm test && npx tsc -b && npm run lint && npm run build`
Expected: PASS。`grep -rn "listRepositories" web/src` は `api/repositories.ts` と `components/settings/RepositoriesTab.tsx` だけ

- [ ] **Step 7: コミット**

```bash
git add web/src
git commit -m "feat(web): ワークスペースのホームとプロジェクト一覧の共有 (#55)"
```

---

### Task 6: プロジェクト画面

**Files:**
- Create: `web/src/components/project/StatusPanel.tsx`、`RequirementsPanel.tsx`、`RequirementRow.tsx`、`PoliciesPanel.tsx`、`RunHistory.tsx`、`NextActionsCard.tsx`
- Create: `web/src/pages/Project.tsx`
- Delete: `web/src/pages/ProjectPlaceholder.tsx`
- Modify: `web/src/App.tsx`（`/w/:slug/p/:owner/:repo` → `Project`）
- Modify: `web/src/services/labels.ts`（`APPROVAL_LABELS`・`POLICY_LEVEL_LABELS`・`REQUIREMENT_FILTER_LABELS` を追加）

**Interfaces:**
- Consumes: Task 3 の `getProjectOverview`・`useWorkspaceProjects`・型、Task 4 の `nextActions`・`groupBySpec`・`matchesFilter`・`filterCounts`・`REQUIREMENT_FILTERS`・`healthRatio`、Task 5 の `HealthRing`、既存の `getRun`（`api/reviewRuns.ts`）・`FindingCard`・`VerdictBadge`・`GateBadge`・`ToneBadge`・`Panel`・`githubBlobUrl`・`githubPrUrl`・`paths`
- Produces: 画面のみ（後続なし）

- [ ] **Step 1: 文言を足す**（`labels.ts`。表示の対応表なので services に置く）

```ts
export const APPROVAL_LABELS: Record<ApprovalState, string> = {
  approved: '承認済み',
  unapproved: '未承認',
  stale: '承認切れ',
}

export const POLICY_LEVEL_LABELS: Record<PolicyLevel, string> = { mandatory: '必須', advisory: '助言' }

export const REQUIREMENT_FILTER_LABELS: Record<RequirementFilter, string> = {
  all: 'すべて',
  violated: '違反',
  unknown: '未検証',
  review: '要確認',
  unapproved: '未承認',
}
```

- [ ] **Step 2: 部品を書く**

各部品の中身（ロジックは Task 4 の関数を呼ぶだけ。条件分岐は表示だけ）:

- `StatusPanel({ overview })`: 左に `HealthRing size="lg" ratio={healthRatio(overview.counts, overview.unanalyzed)}`。右に 4 値の件数のタイル（`VerdictBadge` と数。順は 違反・要確認・未検証・適合）、「未解析 N」「未承認 N」（`unapproved`）。下に最新の検査: `PR #n`（`githubPrUrl`）・`head_sha.slice(0, 7)`・時刻（`services/format.ts` の既存の日時の整形）・`GateBadge`・「検査の詳細」（`paths.run(run.id)`）。`flex-wrap` で 390px でも折り返す
- `NextActionsCard({ actions, run, repo })`: 見出し「次にやること」。`actions` が空なら「すべて確認済み」。各行は `ToneBadge`（kind ごとの色: violated=bad、stale=rev、review=rev、unknown=unk、unapproved=rev）・要求 ID（`font-mono`）・題（`truncate`）・`hint`。行のリンクは violated / review / unknown なら `paths.run(run.id)`、stale / unapproved は同じく検査の詳細（仕様の PR はまだ分からないため）。カードの下に無効のボタン「この件について質問する」（`disabled`、`title="準備中"`）
- `RequirementsPanel({ requirements, run, repo })`: `REQUIREMENT_FILTERS` のボタン列（`filterCounts` の数つき、横にはみ出すときは `overflow-x-auto` の中で並べる）。`groupBySpec(requirements.filter((r) => matchesFilter(r, filter)))` を仕様ごとの見出し（`specPath ?? behaviorId`、`font-mono truncate`）で並べ、行は `RequirementRow`。絞り込みの結果が 0 件なら「該当する要求はありません」。`unextracted` があれば一覧の下に「抽出できなかった要求」として ID を並べる（未解析として数えていることを書く）
- `RequirementRow({ req, run, repo, findings, onOpen })`: 1 行目に ID（`font-mono`）・題（`truncate`、無ければ「（題なし）」）・`APPROVAL_LABELS`（承認済み以外は `ToneBadge tone="rev"`）・「テスト N」・判定（`VerdictBadge`、null なら「未解析」）・`removed` なら「削除」。違反・未検証・要確認の行だけ開ける（`button`、`aria-expanded`）。開いたら `onOpen()` を呼び、親から渡された `findings`（その要求の finding。`finding_ids` で絞る）を `FindingCard repoFullName={repo} headSha={run.head_sha}` で出す。`spec_path` があれば「仕様の該当行」（`githubBlobUrl(repo, run.head_sha, spec_path, line_start, line_end)`）、常に「検査の詳細」（`paths.run(run.id)`）
- `PoliciesPanel({ overview })`: 見出しの下に「チーム規約 {policy_team} 改訂 {policy_revision}」（無ければ「チーム規約は適用されていません」）。`policies` が空なら「適用された規約はありません」。各行: `rule_id`（`font-mono`）・題（無ければ「—」）・「チーム」/「プロジェクト」・`POLICY_LEVEL_LABELS[level]`（null は「—」）・検査器（true「検査器あり」/ false「検査器なし」/ null「—」）・`status` の `VerdictBadge`（null は「この検査では結果なし」）
- `RunHistory({ runs, repo })`: 新しい順に 1 行ずつ: `PR #n` または「手動」・`head_sha.slice(0, 7)`・時刻・`GateBadge`（`gate_status` が null なら `EXEC_STATE_LABELS[exec_state]`）。行全体が `paths.run(id)` へのリンク。0 件は「まだ検査がありません」

- [ ] **Step 3: ページを書く**（`pages/Project.tsx`）

```tsx
import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { ApiError } from '@/api/errors'
import { getProjectOverview } from '@/api/projects'
import { getRun } from '@/api/reviewRuns'
import { Panel, PanelTitle } from '@/components/app/Panel'
import { ErrorState } from '@/components/ErrorState'
import { LoadingState } from '@/components/LoadingState'
import { NextActionsCard } from '@/components/project/NextActionsCard'
import { PoliciesPanel } from '@/components/project/PoliciesPanel'
import { RequirementsPanel } from '@/components/project/RequirementsPanel'
import { RunHistory } from '@/components/project/RunHistory'
import { StatusPanel } from '@/components/project/StatusPanel'
import { useWorkspaceProjects } from '@/hooks/useWorkspaceProjects'
import { nextActions } from '@/services/nextActions'
import { paths } from '@/services/paths'
import type { Finding } from '@/types/finding'
import type { ProjectOverview, ProjectSummary } from '@/types/project'

/** プロジェクト画面（S45、design 5 章）。URL のリポジトリを一覧から引き、中身は project ごとに作り直す。 */
export function Project() {
  const { slug, owner, repo } = useParams<{ slug: string; owner: string; repo: string }>()
  const fullName = `${owner}/${repo}`
  const { projects, error: listError, retry: retryList } = useWorkspaceProjects(slug)
  const project = projects?.find((p) => p.repo_full_name === fullName) ?? null

  if (listError) return <ErrorState message={listError} onRetry={retryList} />
  if (projects === null) return <LoadingState />
  if (!project || !slug) {
    return (
      <Panel>
        <PanelTitle>このワークスペースに {fullName} はありません</PanelTitle>
      </Panel>
    )
  }
  // 同じルートのままプロジェクトを切り替えても前の状態・反例を持ち越さない（key で作り直す）
  return <ProjectBody key={`${slug}/${project.id}`} slug={slug} project={project} repo={repo ?? ''} fullName={fullName} />
}

function ProjectBody({ slug, project, repo, fullName }: { slug: string; project: ProjectSummary; repo: string; fullName: string }) {
  const [overview, setOverview] = useState<ProjectOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [findings, setFindings] = useState<Finding[] | null>(null)

  useEffect(() => {
    let alive = true
    getProjectOverview(slug, project.id)
      .then((o) => alive && setOverview(o))
      .catch((err: unknown) => {
        if (alive) setError(err instanceof ApiError ? err.message : 'プロジェクトの状態を取得できませんでした')
      })
    return () => {
      alive = false
    }
  }, [slug, project.id, attempt])

  const runId = overview?.run?.id
  const loadFindings = useCallback(() => {
    if (!runId || findings !== null) return
    getRun(runId)
      .then((r) => setFindings(r.findings))
      .catch(() => setFindings([]))
  }, [runId, findings])

  function retry() {
    setError(null)
    setOverview(null)
    setAttempt((n) => n + 1)
  }

  if (error) return <ErrorState message={error} onRetry={retry} />
  if (!overview) return <LoadingState message="プロジェクトの状態を取得しています…" />

  const header = (
    <header className="min-w-0">
      <p className="truncate font-mono text-xs text-ink-3">{fullName}</p>
      <h1 className="mt-1 truncate text-2xl font-bold tracking-tight">{repo}</h1>
    </header>
  )

  if (overview.status !== 'ok' || !overview.run) {
    return (
      <div className="space-y-6">
        {header}
        <Panel>
          <PanelTitle>
            {overview.status === 'not_checked' ? 'まだ検査されていません' : '最新の検査の成果物を読めませんでした'}
          </PanelTitle>
          {overview.run && (
            <a className="mt-2 inline-block text-sm text-primary hover:underline" href={paths.run(overview.run.id)}>
              検査の詳細
            </a>
          )}
        </Panel>
        <RunHistory runs={overview.runs} repo={fullName} />
      </div>
    )
  }

  const run = overview.run
  return (
    <div className="space-y-6">
      {header}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="min-w-0 lg:col-start-1"><StatusPanel overview={overview} /></div>
        <aside className="min-w-0 self-start lg:sticky lg:top-20 lg:col-start-2 lg:row-span-4 lg:row-start-1">
          <NextActionsCard actions={nextActions(overview.requirements)} run={run} repo={fullName} />
        </aside>
        <div className="min-w-0 lg:col-start-1">
          <RequirementsPanel requirements={overview.requirements} run={run} repo={fullName} findings={findings} onOpen={loadFindings} />
        </div>
        <div className="min-w-0 lg:col-start-1"><PoliciesPanel overview={overview} /></div>
        <div className="min-w-0 lg:col-start-1"><RunHistory runs={overview.runs} repo={fullName} /></div>
      </div>
    </div>
  )
}
```

（`RequirementsPanel` は `findings` と `onOpen` を `RequirementRow` に渡す。「検査の詳細」のリンクは `a` ではなく `react-router-dom` の `Link` で書く）

`App.tsx` の `ProjectPlaceholder` を `Project` に替え、`ProjectPlaceholder.tsx` を消す。

- [ ] **Step 4: 通ることを確かめる**

Run: `cd web && npm test && npx tsc -b && npm run lint && npm run build`
Expected: PASS

- [ ] **Step 5: モックで画面を確かめる**

Run: `cd web && VITE_MOCK=1 npx vite --host 127.0.0.1 --port 5173 --strictPort`（背景で起動）
Expected: `/w/demo/p/example-org/orders` がライト / ダーク・PC / 390px で崩れない（違反の行を開くと反例のカード、要求の絞り込み、次にやることが 3 件: 違反 R-CANCEL-04・未検証 R-CANCEL-05・未承認 R-CANCEL-06）。`/w/demo/p/example-org/nothing` は「ありません」。ブラウザのツールが使えなければ、確認は Task 7 のデプロイ後にユーザーへ依頼する

- [ ] **Step 6: コミット**

```bash
git add web/src
git commit -m "feat(web): プロジェクト画面（状態・要求・規約・履歴・次にやること） (#55)"
```

---

### Task 7: 仕様書の直しと仕上げ

**Files:**
- Modify: `design/2026-09-30-ui-redesign.md`（5.2 の承認切れの定義、ホームのルート `/w/:slug`）

- [ ] **Step 1: 仕様書を直す**

5.2 の「承認切れ = 承認の内容ハッシュ不一致の finding がある要求」を次に置き換える:

```markdown
- 承認の状態: 承認済み = `approval_ref` がその Spec IR の承認記録にある。承認切れ = 承認済みでなく、
  semantic diff で内容が変わった（`changed`）要求、または head から消えて `stale_approval` の finding が残る要求。
  未承認 = それ以外。承認済みかどうかは検査器と同じく `approval_ref` で決める（仕様本文のハッシュ不一致は検証エラーで
  run が失敗する。IR ハッシュの不一致は警告で、検査器は承認として扱う）
- 未解析 = finding の無い要求と Spec IR の `unextracted`。健康状態の分母に入れる
- 成果物（manifest の behavior ごとの Spec IR）が欠けていれば `{"status": "unavailable"}`
```

4 章の表に `/w/:slug`（ワークスペースのホーム。`/app` は所属の先頭へ転送）を足す。

- [ ] **Step 2: 全体の確認**

Run: `make check && make eval && cd web && npm test && npx tsc -b && npm run lint && npm run build`
Expected: すべて通る。`make eval` の数値が main と同じ

- [ ] **Step 3: コミット**

```bash
git add design/2026-09-30-ui-redesign.md
git commit -m "docs: S45 の承認切れの定義とワークスペースのホームを仕様に反映 (#55)"
```
