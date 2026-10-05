# チームの知識レジストリ Implementation Plan（S62–S72、全体計画）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> この文書は**全体計画**。Task 0（契約）は TDD の手順まで書く。S62–S72 は、ゴール・持ち主のファイル・インターフェース・受け入れテストまでを固定し、
> 細かい手順は各ストーリーの着手時に `design/plans/2026-10-0x-sNN-<topic>.md` として書く（writing-plans の書式、この文書の契約を変えない）。
> 契約（Task 0 の型と、各ストーリーの「Produces」）を変える必要が出たら、実装を止めてメインセッションに戻す。

**Goal:** 規約・スキル・指示書・仕様を SpecLino で改訂・承認して bundle として配り、UML（Mermaid）と旧形式の仕様を決定的に IR に変換して検査し、守られていることをゲートで保証・観測する。

**Architecture:** 追加だけのテーブル（`artifacts` / `artifact_revisions` / `bundles` ほか）に skill・instructions・spec を載せ、規約は既存の `policy_revisions` を bundle から参照する。仕様は `spec/uml/`（Mermaid の厳格な部分集合 → `CompiledSpec`）と旧形式の入口から同じ IR に入り、`conformance/` のオートマトン照合と既存の Z3 エンジンで判定する。配信は `speclino sync`（署名つき lock）と MCP。

**Tech Stack:** Python 3.12、uv、pydantic v2、FastAPI、SQLAlchemy 2 + Alembic（PostgreSQL）、z3-solver、hypothesis（性質テスト）、React + TypeScript（web）、mermaid（web のプレビュー）、Gemini（AI 提案・LLM 支援）。

**Spec:** `design/2026-10-05-team-knowledge-registry.md`

## Global Constraints

- 判定は 4 値（`VERIFIED_IN_SCOPE` / `VIOLATED` / `UNKNOWN` / `REVIEW`）。第五の verdict を作らない。LLM 支援は常に `REVIEW`、区分は `assessment_outcome` で表す
- **間に合わない・解析できない部分は UNKNOWN。偽の VERIFIED_IN_SCOPE を出さない**。VERIFIED / VIOLATED は `precision=exact_in_model` の経路だけ
- open-world: 図に無い処理は REVIEW。VIOLATED にしてよいのは `closed: true` のアクティビティ図だけ
- `conformance/`・`analysis/`・`spec/uml/` は LLM・`adapters`・`google.*`・`agent` を import しない（import 制約テストに `spec/uml` を足す）
- `domain/` は型だけ。ビジネスロジックは `services/`・`conformance/`・`analysis/`・`spec/`。`api/`・`web/` に置かない。TS の型は `web/src/types/`
- 対象リポジトリのモジュールを import しない。IR の式を eval しない
- `policy_revisions` は変更・移行しない。新しいテーブルは追加だけ
- run は開始時に bundle を 1 つ固定し、run の中で再解決しない
- 他のワークスペースのものは 404（存在を漏らさない）
- `schemas/*.schema.json` は `domain/` から生成する（`make schemas` 相当の既存手順。手で編集しない）
- `make test` と `make selfcheck` を混ぜない
- UI に「デモ」という語を使わない。個人の環境の値をコード・文書・fixture に書かない
- Conventional Commits、末尾に `(#<issue>)`。コミットは各ストーリーの承認範囲で。push・デプロイ・マイグレーションの本番適用は別途ユーザー承認
- マイグレーション番号: S62 = `0010_knowledge_registry`、S70 = `0011_llm_assessments`。S61 縮小版（`s61b-approver-ids`）が `0009` を使うかをマージ時に確かめ、`down_revision` を合わせる

## Review Focus

1. **未対応・紛らわしい Mermaid**（`par`、自然言語のガード、ID の重複、ラベルだけ違う同じ ID、未知の矢印 `-x`）→ 必ずエラーになり、IR に入らない。S63 の `test_rejects_*` で固定する
2. **承認の競合と run の途中の承認** → 2 人が同時に承認したら片方は `DraftConflict`。run の途中で bundle が進んでも、その run の判定は固定した bundle のまま。S62 の `test_concurrent_approve_one_wins`、S65 の `test_run_pins_bundle`
3. **sync の書き出し先の攻撃**（`../`、絶対パス、既存の symlink、本文に `speclino:end`）→ 拒否し、手元の状態と lock は前のまま。S68 の `test_sync_rejects_*`
4. **解析しきれないコード**（`while`、動的呼出し、経路の上限超過）に順序の義務 → UNKNOWN で、VERIFIED にならない。S64 の `test_inexact_path_is_unknown`
5. **mandatory のルールの Finding が欠ける**（検査器の例外、LLM の失敗、図の要素の対応なし）→ UNKNOWN が合成され、ゲートは fail。S64 の `test_missing_expected_is_unknown`、S70 の `test_llm_failure_is_unknown`

---

## 並列の列とマージの順序

| 列 | ストーリーの順 | worktree / ブランチ |
| --- | --- | --- |
| 契約 | Task 0 | `s62-team-knowledge-registry-design`（この計画と同じ） |
| A レジストリ | S62 → S68 → S71（バックエンド） | `s62-registry`、`s68-sync`、`s71-observability` |
| B UML | S63 → S64 | `s63-uml-parser`、`s64-trace-check` |
| C 流れ | S65（S62・S63 の後）→ S70 | `s65-pipeline-bundle`、`s70-llm-policies` |
| D 検査器 | S69 | `s69-verifiers` |
| E 画面 | S66 → S67 → S71（画面） | `s66-spec-editor`、`s67-ai-suggest` |
| 仕上げ | S72 | `s72-demo` |

- Task 0 を main にマージしてから、A・B・D を同時に始める（10/5 中）。C は S62・S63 のマージ後、E は S62 の API が固まった後
- ファイルの持ち主は各ストーリーの **Files** に書いたものだけ。他のストーリーの持ち物を変える必要が出たら、その持ち主の列に依頼する
- 共有ファイル（`domain/finding.py`、`domain/run.py`、`services/check_pipeline.py`、`mcp_server/server.py`）は、持ち主を 1 つに決めてある（下表）

| 共有ファイル | 持ち主 |
| --- | --- |
| `domain/finding.py`、`domain/run.py`（追加の欄） | Task 0 |
| `services/check_pipeline.py` | S65（S64・S69・S70 は関数を提供し、S65 が配線する） |
| `mcp_server/server.py` | S68（S67 は `spec_suggest` を S68 のあとに足す） |
| `web/src/types/knowledge.ts` | S66 |

---

### Task 0: 契約（全ストーリーが使う型）

**Files:**
- Create: `src/speclino/domain/knowledge.py`
- Create: `src/speclino/domain/uml.py`
- Create: `src/speclino/domain/assessment.py`
- Modify: `src/speclino/domain/finding.py`（`assessment_outcome`、カテゴリ `missing_finding`）
- Modify: `src/speclino/domain/run.py`（`RunManifest` に bundle の欄）
- Modify: `src/speclino/domain/__init__.py`（エクスポート）
- Test: `tests/domain/test_knowledge.py`、`tests/domain/test_uml.py`、`tests/domain/test_assessment.py`
- 生成: `schemas/` の該当ファイル

**Interfaces:**
- Consumes: `domain.common.IRModel`、`domain.expr.Expr`、`domain.spec_ir.SpecIR` / `RuleLevel`、`domain.domain_spec.DomainSpec`
- Produces: 下のコードの型すべて（名前・欄を変えない）

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/domain/test_knowledge.py
import pytest
from pydantic import ValidationError

from speclino.domain.knowledge import (
    ArtifactScope, Bundle, BundleMember, InstructionsContent, KnowledgeLock, SkillContent, SpecContent,
)


def test_scope_requires_exactly_one_form() -> None:
    assert ArtifactScope(workspace=True).workspace
    assert ArtifactScope(projects=["p1"]).projects == ["p1"]
    with pytest.raises(ValidationError):
        ArtifactScope()
    with pytest.raises(ValidationError):
        ArtifactScope(workspace=True, projects=["p1"])


def test_spec_scope_must_name_projects() -> None:
    with pytest.raises(ValidationError):
        SpecContent(kind="spec", markdown="# x", scope=ArtifactScope(workspace=True))


def test_skill_file_paths_are_relative_and_inside() -> None:
    ok = SkillContent(kind="skill", skill_md="---\nname: a\ndescription: b\n---\n", files={"ref/x.md": "y"},
                      scope=ArtifactScope(workspace=True))
    assert "ref/x.md" in ok.files
    for bad in ["../x", "/etc/passwd", "a/../../b", "a\\b", ""]:
        with pytest.raises(ValidationError):
            SkillContent(kind="skill", skill_md="---\nname: a\ndescription: b\n---\n", files={bad: "y"},
                         scope=ArtifactScope(workspace=True))


def test_managed_marker_is_rejected_in_bodies() -> None:
    with pytest.raises(ValidationError):
        InstructionsContent(kind="instructions", target="agents_md", body="x <!-- speclino:end --> y",
                            scope=ArtifactScope(workspace=True))


def test_bundle_and_lock_roundtrip() -> None:
    m = BundleMember(artifact_id="a1", kind="skill", name="deploy", revision=2, content_hash="sha256:" + "0" * 64)
    b = Bundle(workspace="w", generation=3, policy_revision=5, policy_hash=None, members=[m],
               scope_hash="sha256:" + "1" * 64, signature="sig")
    lock = KnowledgeLock(workspace="w", github_repo_id=1, bundle_generation=3, members=[m],
                         files={"AGENTS.md": "sha256:" + "2" * 64}, issued_at=b.created_at,
                         expires_at=b.created_at, signature="sig")
    assert KnowledgeLock.model_validate_json(lock.model_dump_json()) == lock
```

```python
# tests/domain/test_uml.py
import pytest
from pydantic import ValidationError

from speclino.domain.uml import Automaton, AutomatonTransition, CallLabel, TraceObligation


def _auto() -> Automaton:
    return Automaton(states=["s0", "s1"], initial="s0", accepting=["s1"],
                     transitions=[AutomatonTransition(id="m1", source="s0", target="s1",
                                                      label=CallLabel(operation="Inventory.reserve"))])


def test_automaton_rejects_unknown_states() -> None:
    with pytest.raises(ValidationError):
        Automaton(states=["s0"], initial="s9", accepting=[], transitions=[])


def test_closed_only_for_activity() -> None:
    with pytest.raises(ValidationError):
        TraceObligation(id="seq1#placeOrder", diagram_kind="sequence", entry_operation="Order.place",
                        mode="contains_subsequence", closed=True, automaton=_auto(), level="mandatory", spans=[])
```

```python
# tests/domain/test_assessment.py
from speclino.domain.assessment import AssessmentKey
from speclino.domain.finding import Finding


def test_key_is_stable_string() -> None:
    k = AssessmentKey(policy_revision=3, rule_id="ERRMSG-002", unit_hash="sha256:" + "a" * 64,
                      model="m", prompt_version="1")
    assert k.as_str() == AssessmentKey.model_validate(k.model_dump()).as_str()


def test_finding_accepts_assessment_outcome() -> None:
    assert "assessment_outcome" in Finding.model_fields
```

- [ ] **Step 2: 失敗を確かめる**

Run: `uv run pytest tests/domain/test_knowledge.py tests/domain/test_uml.py tests/domain/test_assessment.py -q`
Expected: FAIL（`ModuleNotFoundError: speclino.domain.knowledge` など）

- [ ] **Step 3: 型を書く**

```python
# src/speclino/domain/knowledge.py
"""チームの知識レジストリの型（S62–S68、design/2026-10-05-team-knowledge-registry.md 3 章）。"""

from __future__ import annotations

import posixpath
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from speclino.domain.common import IRModel
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.policy import PolicyRevision
from speclino.domain.spec_ir import SpecIR
from speclino.domain.uml import CompiledSpec

ArtifactKind = Literal["skill", "instructions", "spec"]
ArtifactStatus = Literal["draft", "active", "superseded"]
InstructionsTarget = Literal["agents_md", "claude_md"]

MANAGED_BEGIN = "<!-- speclino:begin"
MANAGED_END = "<!-- speclino:end -->"
SKILL_FILE_MAX_BYTES = 256 * 1024
SKILL_TOTAL_MAX_BYTES = 1024 * 1024


def _reject_markers(text: str) -> str:
    if MANAGED_BEGIN in text or MANAGED_END in text:
        raise ValueError("本文に SpecLino の管理区画のマーカーを含められない")
    return text


class ArtifactScope(IRModel):
    """どのリポジトリに当たるか。`workspace=True` か `projects`（project ID）のどちらか一方。"""

    workspace: bool = False
    projects: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_form(self) -> ArtifactScope:
        if self.workspace == bool(self.projects):
            raise ValueError("scope は workspace=true か projects のどちらか一方")
        if len(set(self.projects)) != len(self.projects):
            raise ValueError("projects が重複している")
        return self


class SkillContent(IRModel):
    kind: Literal["skill"]
    skill_md: str = Field(min_length=1)
    files: dict[str, str] = Field(default_factory=dict, description="相対パス → 本文（テキストのみ）")
    scope: ArtifactScope

    @field_validator("files")
    @classmethod
    def _safe_files(cls, v: dict[str, str]) -> dict[str, str]:
        total = 0
        for path, body in v.items():
            norm = posixpath.normpath(path) if path else ""
            if (not path or "\\" in path or path.startswith("/") or norm.startswith("..")
                    or norm != path or path == "SKILL.md"):
                raise ValueError(f"付属ファイルのパスが不正: {path!r}")
            size = len(body.encode())
            if size > SKILL_FILE_MAX_BYTES:
                raise ValueError(f"付属ファイルが大きすぎる: {path}")
            total += size
            _reject_markers(body)
        if total > SKILL_TOTAL_MAX_BYTES:
            raise ValueError("付属ファイルの合計が大きすぎる")
        return v

    @field_validator("skill_md")
    @classmethod
    def _md(cls, v: str) -> str:
        return _reject_markers(v)


class InstructionsContent(IRModel):
    kind: Literal["instructions"]
    target: InstructionsTarget
    body: str = Field(min_length=1)
    scope: ArtifactScope

    @field_validator("body")
    @classmethod
    def _body(cls, v: str) -> str:
        return _reject_markers(v)


class SpecContent(IRModel):
    kind: Literal["spec"]
    markdown: str = Field(min_length=1)
    domain: DomainSpec | None = Field(default=None, description="入口の仮定・不透明 ID・観測の境界など（旧 domain.yaml）")
    legacy_ir: list[SpecIR] = Field(default_factory=list, description="旧形式の入口（承認済み JSON をそのまま）")
    scope: ArtifactScope
    compiled: CompiledSpec | None = Field(default=None, description="承認時に固定した変換結果")

    @model_validator(mode="after")
    def _projects_only(self) -> SpecContent:
        if self.scope.workspace:
            raise ValueError("spec の scope はプロジェクトを列挙する（仕様と実装の組）")
        return self


ArtifactContent = Annotated[SkillContent | InstructionsContent | SpecContent, Field(discriminator="kind")]


class ArtifactRevision(IRModel):
    artifact_id: str = Field(min_length=1)
    workspace: str = Field(min_length=1)
    kind: ArtifactKind
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    revision: int = Field(ge=1)
    status: ArtifactStatus
    content: ArtifactContent
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    author: str = Field(min_length=1)
    approver: str | None = None
    self_approved: bool = False
    note: str = ""
    created_at: datetime
    approved_at: datetime | None = None


class BundleMember(IRModel):
    artifact_id: str = Field(min_length=1)
    kind: ArtifactKind
    name: str = Field(min_length=1)
    revision: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class Bundle(IRModel):
    """ある時点の active な改訂の集合（ワークスペースごとの連番）。承認・規約の有効化のたびに作る。"""

    workspace: str = Field(min_length=1)
    generation: int = Field(ge=1)
    policy_revision: int | None = None
    policy_hash: str | None = None
    members: list[BundleMember] = Field(default_factory=list)
    scope_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    signature: str = Field(min_length=1, description="サーバ鍵による HMAC。対象は signature 以外の正規 JSON")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class KnowledgeLock(IRModel):
    """`.speclino/lock.json`。この checkout が宣言している bundle（利用の証明ではない）。"""

    schema_version: Literal["0.1"] = "0.1"
    workspace: str = Field(min_length=1)
    github_repo_id: int
    bundle_generation: int = Field(ge=1)
    members: list[BundleMember]
    files: dict[str, str] = Field(description="書き出したファイルの相対パス → sha256")
    issued_at: datetime
    expires_at: datetime
    signature: str = Field(min_length=1)


class ResolvedKnowledge(IRModel):
    """bundle をあるプロジェクトに当てた結果（`services.knowledge_registry.resolve_for_project`）。"""

    bundle: Bundle
    skills: list[ArtifactRevision] = Field(default_factory=list)
    instructions: list[ArtifactRevision] = Field(default_factory=list)
    specs: list[ArtifactRevision] = Field(default_factory=list)
    policy: PolicyRevision | None = None


class Suggestion(IRModel):
    """AI の書き換え提案（S67）。本文の置換だけで、IR は持たない。"""

    start: int = Field(ge=0, description="本文の文字オフセット")
    end: int = Field(ge=0)
    replacement: str
    reason: str = Field(min_length=1)


__all__ = [
    "MANAGED_BEGIN", "MANAGED_END", "ArtifactContent", "ResolvedKnowledge", "Suggestion", "ArtifactKind", "ArtifactRevision", "ArtifactScope",
    "ArtifactStatus", "Bundle", "BundleMember", "InstructionsContent", "InstructionsTarget", "KnowledgeLock",
    "SkillContent", "SpecContent",
]
```

```python
# src/speclino/domain/uml.py
"""UML（Mermaid の厳格な部分集合）から変換した IR（S63–S64、設計 4–5 章）。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from speclino.domain.common import IRModel
from speclino.domain.expr import Expr
from speclino.domain.spec_ir import RuleLevel, SpecIR

DiagramKind = Literal["class", "sequence", "state", "activity"]
AttrType = Literal["int", "bool", "str", "enum", "opaque_id"]


class SourceSpan(IRModel):
    diagram_id: str = Field(min_length=1)
    line: int = Field(ge=1, description="Markdown 全体での行")
    column: int = Field(ge=1)


class SpecDiagnostic(IRModel):
    code: str = Field(pattern=r"^[EW]_[A-Z_]+$")
    severity: Literal["error", "warning"]
    message: str = Field(min_length=1)
    span: SourceSpan | None = None


class ClassAttribute(IRModel):
    name: str = Field(min_length=1)
    type: AttrType
    enum: str | None = Field(default=None, description="type=enum のときの列挙クラス名")


class ClassOperation(IRModel):
    name: str = Field(min_length=1)
    params: list[str] = Field(default_factory=list)


class ClassDef(IRModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    stereotype: Literal["class", "enumeration"] = "class"
    attributes: list[ClassAttribute] = Field(default_factory=list)
    operations: list[ClassOperation] = Field(default_factory=list)
    enum_values: list[str] = Field(default_factory=list)
    span: SourceSpan


class CallLabel(IRModel):
    kind: Literal["call"] = "call"
    operation: str = Field(min_length=1, description="<参加者のクラス名>.<メソッド名>")


class EpsilonLabel(IRModel):
    kind: Literal["epsilon"] = "epsilon"


TransitionLabel = Annotated[CallLabel | EpsilonLabel, Field(discriminator="kind")]


class AutomatonTransition(IRModel):
    id: str = Field(min_length=1, description="図の要素 ID（メッセージ・辺）")
    source: str
    target: str
    label: TransitionLabel
    guard: Expr | None = None
    span: SourceSpan | None = None


class Automaton(IRModel):
    states: list[str] = Field(min_length=1)
    initial: str
    accepting: list[str]
    transitions: list[AutomatonTransition]

    @model_validator(mode="after")
    def _closed_states(self) -> Automaton:
        known = set(self.states)
        refs = {self.initial, *self.accepting} | {t.source for t in self.transitions} | {t.target for t in self.transitions}
        if not refs <= known:
            raise ValueError(f"未宣言の状態: {sorted(refs - known)}")
        return self


class TraceObligation(IRModel):
    id: str = Field(min_length=1, description="<図 ID>#<入口の操作>")
    diagram_kind: Literal["sequence", "activity"]
    entry_operation: str = Field(min_length=1)
    mode: Literal["contains_subsequence", "included_in"]
    closed: bool = False
    automaton: Automaton
    level: RuleLevel
    spans: list[SourceSpan] = Field(default_factory=list)

    @model_validator(mode="after")
    def _closed_activity_only(self) -> TraceObligation:
        if self.closed and self.diagram_kind != "activity":
            raise ValueError("closed はアクティビティ図だけ")
        expected = "contains_subsequence" if self.diagram_kind == "sequence" else "included_in"
        if self.mode != expected:
            raise ValueError(f"{self.diagram_kind} の mode は {expected}")
        return self


class StateTransition(IRModel):
    id: str = Field(min_length=1)
    source: str
    target: str
    event: str | None = None
    guard: Expr | None = None
    span: SourceSpan | None = None


class StateMachine(IRModel):
    id: str = Field(min_length=1)
    attribute: str = Field(min_length=1, description="状態を持つ属性（例 order.status）")
    states: list[str] = Field(min_length=1)
    initial: str
    transitions: list[StateTransition]
    level: RuleLevel


class StructureObligation(IRModel):
    id: str = Field(min_length=1, description="<図 ID>#<クラス ID>")
    class_name: str = Field(min_length=1)
    operations: list[ClassOperation] = Field(default_factory=list)
    level: RuleLevel


class RequirementRow(IRModel):
    id: str = Field(min_length=1)
    level: RuleLevel
    text: str = Field(min_length=1)
    formalized_by: list[str] = Field(default_factory=list, description="`図ID#要素ID` か `ir:要求ID`")


class CompiledSpec(IRModel):
    parser_version: str = Field(min_length=1)
    source_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ir_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    classes: list[ClassDef] = Field(default_factory=list)
    structure: list[StructureObligation] = Field(default_factory=list)
    traces: list[TraceObligation] = Field(default_factory=list)
    state_machines: list[StateMachine] = Field(default_factory=list)
    legacy: list[SpecIR] = Field(default_factory=list)
    requirements: list[RequirementRow] = Field(default_factory=list)


class ParseResult(IRModel):
    compiled: CompiledSpec | None
    diagnostics: list[SpecDiagnostic] = Field(default_factory=list)

    @model_validator(mode="after")
    def _all_or_nothing(self) -> ParseResult:
        has_error = any(d.severity == "error" for d in self.diagnostics)
        if has_error == (self.compiled is not None):
            raise ValueError("エラーがあれば compiled は None、無ければ必須（全か無か）")
        return self


__all__ = [
    "AttrType", "Automaton", "AutomatonTransition", "CallLabel", "ClassAttribute", "ClassDef", "ClassOperation",
    "CompiledSpec", "DiagramKind", "EpsilonLabel", "ParseResult", "RequirementRow", "SourceSpan", "SpecDiagnostic",
    "StateMachine", "StateTransition", "StructureObligation", "TraceObligation", "TransitionLabel",
]
```

```python
# src/speclino/domain/assessment.py
"""LLM 支援の規約の評価記録と人の確定（S70、設計 6.2–6.3）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from speclino.domain.common import IRModel

AssessmentOutcome = Literal["issue", "no_issue", "split"]
Disposition = Literal["attested", "acknowledged_violation"]


class AssessmentKey(IRModel):
    policy_revision: int = Field(ge=1)
    rule_id: str = Field(min_length=1)
    unit_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)

    def as_str(self) -> str:
        return f"{self.policy_revision}|{self.rule_id}|{self.unit_hash}|{self.model}|{self.prompt_version}"


class ItemVerdict(IRModel):
    item_id: str = Field(min_length=1)
    outcome: AssessmentOutcome
    votes: list[bool] = Field(min_length=1, description="試行ごとの「該当する」")
    quote: str | None = None


class AssessmentRecord(IRModel):
    key: AssessmentKey
    unit_symbol: str = Field(min_length=1)
    items: list[ItemVerdict]
    created_at: datetime


class AssessmentUnit(IRModel):
    """LLM 支援で評価する単位（関数・文字列リテラル・エンドポイント）。"""

    symbol: str = Field(min_length=1, description="例 src/app/api.py::create_order または ...::L42:'msg'")
    kind: Literal["function", "string_literal", "endpoint"]
    text: str = Field(min_length=1, description="正規化前の本文（根拠の引用に使う）")
    unit_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$", description="空白とコメントを除いた本文の hash")


class Attestation(IRModel):
    fingerprint: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    disposition: Disposition
    actor: str = Field(min_length=1)
    workspace: str = Field(min_length=1)
    created_at: datetime


__all__ = ["AssessmentKey", "AssessmentOutcome", "AssessmentUnit", "AssessmentRecord", "Attestation", "Disposition", "ItemVerdict"]
```

`domain/finding.py` の `Finding` に次を足し、`Category` の Literal に `"missing_finding"` を足す:

```python
    assessment_outcome: Literal["issue", "no_issue", "split"] | None = Field(
        default=None, description="LLM 支援の規約だけ。verdict は常に REVIEW"
    )
```

`domain/run.py` の `RunManifest` に次を足す:

```python
    bundle_generation: int | None = Field(default=None, description="run の開始時に固定した bundle")
    bundle_hash: str | None = None
    uml_parser_version: str | None = None
    expected_requirement_ids: list[str] = Field(default_factory=list, description="出るべき要求 ID（欠けたら UNKNOWN）")
    llm_assessment_keys: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: 通ることを確かめる**

Run: `uv run pytest tests/domain -q && make typecheck && make lint`
Expected: PASS。続けて既存の手順で `schemas/` を再生成し、`make test` が緑

- [ ] **Step 5: コミット**（ユーザー承認後）

```bash
git add src/speclino/domain/knowledge.py src/speclino/domain/uml.py src/speclino/domain/assessment.py \
  src/speclino/domain/finding.py src/speclino/domain/run.py src/speclino/domain/__init__.py tests/domain schemas
git commit -m "feat: 知識レジストリと UML の契約となる型 (#<S62>)"
```

---

### S62: 知識レジストリの土台

**Files:** Create `migrations/versions/0010_knowledge_registry.py`、`db/repositories/artifacts.py`、`db/repositories/bundles.py`、`services/knowledge_registry.py`、`services/bundle_signing.py`、`api/routes/knowledge.py`、`tests/services/test_knowledge_registry.py`、`tests/api/test_knowledge_routes.py`。Modify `db/models.py`（追加のモデルのみ）、`services/policy_governance.py`（規約の有効化の後に `build_bundle` を呼ぶ 1 か所だけ）。

**Interfaces:**
- Consumes: Task 0 の `ArtifactRevision` / `Bundle` / `ArtifactScope`、既存の `policy_revisions` リポジトリ、`audit`
- Produces:
  - `async def create_draft(session, *, workspace: str, kind: ArtifactKind, name: str, content: ArtifactContent, author: str) -> ArtifactRevision`
  - `async def update_draft(session, *, artifact_id: str, expected_revision: int, content: ArtifactContent, author: str) -> ArtifactRevision`（draft は可変）
  - `async def approve(session, *, artifact_id: str, expected_revision: int, expected_hash: str, approver: str) -> Bundle`（ワークスペース行のロック → 競合検査 → active 切替 → bundle 作成 → 監査を 1 トランザクション。`DraftConflict` / `PermissionError` / `BundleConflict`）
  - `async def build_bundle(session, workspace: str) -> Bundle`（規約の有効化からも呼ぶ）
  - `async def current_bundle(session, workspace: str) -> Bundle | None`、`async def get_bundle(session, workspace: str, generation: int) -> Bundle`
  - `async def resolve_for_project(session, bundle: Bundle, project_id: str) -> ResolvedKnowledge`（`ResolvedKnowledge` は Task 0）
  - `def sign(payload: bytes) -> str` / `def verify(payload: bytes, signature: str) -> bool`（鍵は Secret Manager の `SPECLINO_BUNDLE_SIGNING_KEY`、HMAC-SHA256）
  - REST: `GET/POST /api/w/{slug}/artifacts`、`GET/PUT /api/w/{slug}/artifacts/{id}/draft`、`POST /api/w/{slug}/artifacts/{id}/approve`、`GET /api/w/{slug}/artifacts/{id}/revisions`、`GET /api/w/{slug}/bundles/current`
- 受け入れテスト: `test_concurrent_approve_one_wins`、`test_self_approval_is_flagged`、`test_bundle_conflict_rejects_approval`（同スコープ同 target の instructions 2 つ）、`test_scope_project_must_belong_to_workspace`、`test_other_workspace_is_404`、`test_policy_activation_creates_bundle`、`test_spec_with_errors_cannot_be_approved`（S63 の parser が入るまでは `compiled is None` で拒否）

### S63: UML parser と旧形式の入口

**Files:** Create `spec/uml/__init__.py`、`spec/uml/grammar.md`（EBNF）、`spec/uml/lexer.py`、`spec/uml/markdown.py`（コードブロックと要求の表の抽出）、`spec/uml/class_diagram.py`、`spec/uml/sequence.py`、`spec/uml/state.py`、`spec/uml/activity.py`、`spec/uml/guard.py`（ガード → `Expr`）、`spec/uml/render.py`、`spec/uml/compile.py`、`tests/spec/uml/`（図ごと＋性質テスト）。Modify `tests/test_import_constraints.py`（`spec.uml` を LLM 非依存に追加）、`pyproject.toml`（dev に hypothesis が無ければ追加）。

**Interfaces:**
- Consumes: Task 0 の `domain.uml` 全部、`domain.expr`、`domain.spec_ir.SpecIR`
- Produces:
  - `PARSER_VERSION: str = "uml-0.1"`
  - `def compile_spec(markdown: str, *, domain: DomainSpec | None, legacy_ir: list[SpecIR]) -> ParseResult`
  - `def render(compiled: CompiledSpec) -> str`（正規形の Markdown。図は ID つき）
- 受け入れテスト: 図ごとの正常系、`test_rejects_par`、`test_rejects_natural_language_guard`、`test_rejects_duplicate_ids`、`test_rejects_unknown_arrow`、`test_every_token_is_classified`（トークンの網羅）、`test_roundtrip_property`（hypothesis: `compile(render(compile(s))) == compile(s)`）、`test_deterministic_hash`、`test_requirement_row_without_reference_is_unformalized`、`test_legacy_ir_passthrough`

### S64: 仕様の検査

**Files:** Create `conformance/automaton.py`（積とガードの Z3）、`conformance/trace_check.py`、`conformance/state_check.py`、`conformance/expected.py`、`analysis/structure_check.py`（AST でクラスとメソッド）、`tests/conformance/test_trace_check.py`、`tests/conformance/test_state_check.py`、`tests/conformance/test_expected.py`、`tests/analysis/test_structure_check.py`。

**Interfaces:**
- Consumes: `CompiledSpec`、`CodeIR` / `Path` / `Event`、`conformance.engine.EngineConfig`、Z3 のヘルパー
- Produces:
  - `def check_trace(ob: TraceObligation, code: CodeIR, config: EngineConfig) -> list[Finding]`
  - `def check_state_machine(sm: StateMachine, code: CodeIR, config: EngineConfig) -> list[Finding]`
  - `def check_structure(ob: StructureObligation, repo_root: Path, source_roots: tuple[str, ...]) -> list[Finding]`
  - `def expected_ids(compiled: CompiledSpec, policy_rule_ids: list[str]) -> list[str]`
  - `def fill_missing(findings: Findings, expected: list[str], behavior_id: str) -> Findings`（欠けた ID に `UNKNOWN` / `missing_finding`）
- 受け入れテスト: `test_subsequence_satisfied_is_verified`、`test_missing_call_is_violated_with_counterexample`、`test_extra_call_is_not_violation`、`test_guard_unsat_path_skipped`、`test_inexact_path_is_unknown`、`test_activity_closed_extra_call_violated`、`test_activity_open_extra_call_review`、`test_illegal_state_transition_violated`、`test_missing_expected_is_unknown`、`test_unbound_element_is_unknown`

### S65: チェックの流れを bundle に差し替える

**Files:** Modify `services/check_pipeline.py`、`services/review_run_service.py`（worker の配線）、`services/replay_service.py`、`cli.py`（`speclino import-specs`）。Create `services/spec_import.py`、`services/knowledge_inputs.py`（bundle → 検査器への入力）、`tests/services/test_check_pipeline_bundle.py`、`tests/services/test_replay_bundle.py`、`tests/services/test_spec_import.py`。

**Interfaces:**
- Consumes: S62 の `current_bundle` / `get_bundle` / `resolve_for_project`、S63 の `compile_spec`、S64 の関数、S69 の検査器（レジストリ経由）
- Produces:
  - `CheckRequest.knowledge: ResolvedKnowledge | None`（None なら従来の Git の入力。CLI のローカル実行と自己検証のため残す）
  - manifest の `bundle_generation` / `bundle_hash` / `uml_parser_version` / `expected_requirement_ids` を埋める
  - `async def import_specs(session, *, workspace: str, project_id: str, repo_root: Path, author: str) -> list[ArtifactRevision]`（旧形式を draft として登録）
  - replay: manifest の bundle を解決して決定的な検査を再実行する
- 受け入れテスト: `test_run_pins_bundle`、`test_bundle_specs_replace_git_specs`、`test_legacy_spec_same_findings_as_before`（`examples/orders` で移行前後の Findings が同じ）、`test_replay_reruns_policy_verifiers`、`test_selfcheck_still_reads_frozen_copy`

### S66: 仕様エディタと承認の画面

**Files:** Create `web/src/types/knowledge.ts`、`web/src/api/knowledge.ts`、`web/src/pages/knowledge/`（一覧・仕様エディタ・スキル／指示書エディタ・差分と承認）、`web/src/components/spec-editor/`（Markdown 入力、mermaid のプレビュー、正規形、診断、要求の追跡）、`api/routes/spec_preview.py`（`POST /api/w/{slug}/specs/preview` → `ParseResult` と `render`）。

**Interfaces:**
- Consumes: S62 の REST、S63 の `compile_spec` / `render`
- Produces: 画面。`knowledge.ts` の型（S67・S71 が使う）
- 受け入れテスト: preview API のテスト（エラー時 `compiled=null` と場所つき診断）、web の型検査とビルド、Playwright で「下書き → 診断が消える → 承認 → bundle の世代が進む」

### S67: AI の書き換え提案

**Files:** Create `services/spec_suggest.py`、`api/routes/spec_suggest.py`、`web/src/components/spec-editor/SuggestionPanel.tsx`。Modify `mcp_server/server.py`（`spec_suggest`）、`agent/consultant/`（A2A の相談に下書きを足す）。

**Interfaces:**
- Produces: `async def suggest(markdown: str, diagnostics: list[SpecDiagnostic], *, llm: TextLlm) -> list[Suggestion]`（`Suggestion` は Task 0）。提案を当てた本文は必ず `compile_spec` を通して診断を返す
- 受け入れテスト: `test_suggestion_never_writes_ir`、`test_suggestion_is_revalidated`、費用枠（既存の `cost_guard`）を超えたら断る

### S68: スキルと指示書の配信

**Files:** Create `services/knowledge_sync.py`、`services/sync_tokens.py`、`cli_sync.py`（`speclino sync`）、`api/routes/sync.py`、`tests/services/test_knowledge_sync.py`、`tests/test_cli_sync.py`。Modify `mcp_server/server.py`（`sync_status` / `spec_get` / `skill_get`）、`db/models.py`（`sync_tokens`、`knowledge_fetches` は S62 のマイグレーションに含める）。

**Interfaces:**
- Produces:
  - `GET /api/sync/bundle?repo_id=` → `{bundle, files: {path: body}, lock: KnowledgeLock}`（sync 用トークン。読み取り専用・リポジトリ限定）
  - `def write_bundle(repo_root: Path, files: dict[str, str], lock: KnowledgeLock) -> None`（一時ディレクトリ → 置換。管理区画の外は保つ。symlink・ルート外は拒否）
  - `def check_lock(repo_root: Path, lock_json: str | None, *, current: Bundle, verify: Callable[[bytes, str], bool]) -> Finding`（鮮度: 古い → REVIEW、無い・署名不一致・ファイル改変 → UNKNOWN、一致 → VERIFIED_IN_SCOPE）
- 受け入れテスト: `test_sync_rejects_parent_path`、`test_sync_rejects_symlink_target`、`test_sync_keeps_outside_managed_block`、`test_sync_failure_keeps_old_lock`、`test_lock_stale_is_review`、`test_lock_tampered_file_is_unknown`、`test_sync_token_cannot_start_verification`

### S69: 決定的な規約の検査器

**Files:** Create `verifiers/api_conventions.py`、`verifiers/naming.py`、`verifiers/error_message.py`、`verifiers/lint_preset.py`、各テスト。Modify `verifiers/api_error_format.py`（エンベロープ）、`verifiers/registry.py`（登録）。

**Interfaces:**
- Produces: `Verifier` プロトコル（既存）の実装。ID は `api.conventions` / `naming.identifiers` / `error_message.format` / `lint.preset`、版 `1.0.0`。パラメーターの型は各ファイルの pydantic モデル（`domain/policy_params.py` に置く。画面のフォームが使う）
- 受け入れテスト: 各検査器の VERIFIED / VIOLATED / UNKNOWN（入力なし・構文エラー）の 3 通り。`on_missing_input` の扱いは既存と同じ

### S70: LLM 支援の規約と人の確定

**Files:** Create `migrations/versions/0011_llm_assessments.py`、`services/llm_policies.py`、`services/attestations.py`、`db/repositories/assessments.py`、`api/routes/attestations.py`、テスト。Modify `api/routes/webhooks.py`（`attest` を `attestations` に記録。ID 照合は S61 縮小版の関数を使う）、`verifiers/registry.py`（`llm.checklist` は検査器ではなく S70 のサービスが Finding を作る旨の登録）。

**Interfaces:**
- Produces:
  - `async def assess(rule: PolicyRule, units: list[AssessmentUnit], *, llm: TextLlm, store: AssessmentStore, policy_revision: int) -> tuple[list[Finding], list[str]]`（Finding と使ったキー。verdict は常に REVIEW）
  - `async def attest(session, *, fingerprint: str, content_hash: str, disposition: Disposition, actor: str, workspace: str) -> Attestation`
  - ゲートへの適用: `def apply_attestations(findings: Findings, attestations: list[Attestation]) -> frozenset[str]`（阻止から外す finding ID。`waived_ids` と同じ経路で `triage` に渡す）
- 受け入れテスト: `test_same_unit_same_key_no_llm_call`、`test_majority_of_three`、`test_split_is_review_split`、`test_attestation_bound_to_content_hash`、`test_llm_failure_is_unknown`、`test_replay_uses_recorded_output`

### S71: 可観測性

**Files:** Create `services/observability.py`、`api/routes/observability.py`、`web/src/pages/observability/`、`domain/observability.py`（表示の型）。

**Interfaces:**
- Produces: `async def workspace_health(session, workspace: str) -> WorkspaceHealth`（リポジトリごとの形式化率・対応率・UNKNOWN の内訳、トークンごとの最終取得 bundle と遅れ、規約ごとの推移、LLM の安定度）。集計は 1 か所で作り、画面は数え直さない
- 受け入れテスト: 集計の単体テスト（固定の run・fetch・assessment から期待値）、他ワークスペースが混ざらない

### S72: デモ題材と提出物

- `examples/` に UML で書いた仕様（クラス・シーケンス・状態遷移・アクティビティ）と、違反を含む PR を用意する
- 提出物（S40）の文書・動画台本を新しい位置づけで書き直す
- 10/12–13: 全体の回帰、本番マイグレーションのリハーサル（ステージングの DB で 0010・0011）、sync のセキュリティの確認、codex による差分のレビュー
