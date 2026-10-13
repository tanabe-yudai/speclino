"""チームの知識レジストリの型（S62–S68）。"""

from __future__ import annotations

import posixpath
import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from speclino.domain.common import IRModel
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.knowledge_chat import NoteSource
from speclino.domain.policy import PolicyRevision
from speclino.domain.spec_ir import SpecIR
from speclino.domain.uml import CompiledSpec, SpecDiagnostic

ArtifactKind = Literal["skill", "instructions", "spec"]
ArtifactStatus = Literal["draft", "active", "superseded"]
InstructionsTarget = Literal["agents_md", "claude_md"]
# スキルの書き出し先のルート（S76・S77）。lock の `skill_roots` と配置の型
SkillsRoot = Literal[".agents/skills", ".claude/skills"]

MANAGED_BEGIN = "<!-- speclino:begin"
MANAGED_END = "<!-- speclino:end -->"
BODY_MAX_BYTES = 256 * 1024
# 知識の名前の規則（作成の API と、会話の名前の提案の検証で同じものを使う）
ARTIFACT_NAME_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
ARTIFACT_NAME_MAX_CHARS = 64
SKILL_FILE_MAX_BYTES = 256 * 1024
SKILL_TOTAL_MAX_BYTES = 1024 * 1024

_MARKER_PREFIXES = ("<!--speclino:begin", "<!--speclino:end")
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


def _reject_markers(text: str) -> str:
    squeezed = re.sub(r"\s+", "", text).lower()
    if any(m in squeezed for m in _MARKER_PREFIXES):
        raise ValueError("本文に SpecLino の管理区画のマーカーを含められない")
    return text


def _limit_body(text: str) -> str:
    if len(text.encode()) > BODY_MAX_BYTES:
        raise ValueError("本文が大きすぎる")
    return text


def _check_skill_path(path: str) -> None:
    norm = posixpath.normpath(path) if path else ""
    if (
        not path
        or "\x00" in path
        or "\\" in path
        or path.startswith("/")
        or _DRIVE_LETTER.match(path)
        or norm != path
        or norm.split("/")[0] == ".."
        or any(seg in (".", "..") for seg in path.split("/"))
        or path.lower() == "skill.md"
    ):
        raise ValueError(f"付属ファイルのパスが不正: {path!r}")


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
    files: dict[str, str] = Field(
        default_factory=dict, description="相対パス → 本文（テキストのみ）"
    )
    scope: ArtifactScope

    @field_validator("files")
    @classmethod
    def _safe_files(cls, v: dict[str, str]) -> dict[str, str]:
        seen: set[str] = set()
        for path, body in v.items():
            _check_skill_path(path)
            if path.lower() in seen:
                raise ValueError(f"付属ファイルのパスが大文字小文字違いで重複: {path!r}")
            seen.add(path.lower())
            if len(body.encode()) > SKILL_FILE_MAX_BYTES:
                raise ValueError(f"付属ファイルが大きすぎる: {path}")
            _reject_markers(body)
        return v

    @field_validator("skill_md")
    @classmethod
    def _md(cls, v: str) -> str:
        return _reject_markers(_limit_body(v))

    @model_validator(mode="after")
    def _total_size(self) -> SkillContent:
        total = len(self.skill_md.encode()) + sum(len(b.encode()) for b in self.files.values())
        if total > SKILL_TOTAL_MAX_BYTES:
            raise ValueError("skill の合計が大きすぎる")
        return self


class InstructionsContent(IRModel):
    kind: Literal["instructions"]
    # 旧データの互換のためだけに受け付ける。書き出し先は取り込むエージェントが選ぶ（S76）
    target: InstructionsTarget | None = None
    body: str = Field(min_length=1)
    scope: ArtifactScope

    @field_validator("body")
    @classmethod
    def _body(cls, v: str) -> str:
        return _reject_markers(_limit_body(v))


class SpecContent(IRModel):
    kind: Literal["spec"]
    markdown: str = Field(min_length=1)
    domain: DomainSpec | None = Field(
        default=None, description="入口の仮定・不透明 ID・観測の境界など（旧 domain.yaml）"
    )
    legacy_ir: list[SpecIR] = Field(
        default_factory=list, description="旧形式の入口（承認済み JSON をそのまま）"
    )
    scope: ArtifactScope
    compiled: CompiledSpec | None = Field(default=None, description="承認時に固定した変換結果")

    @field_validator("markdown")
    @classmethod
    def _markdown(cls, v: str) -> str:
        return _limit_body(v)


ArtifactContent = Annotated[
    SkillContent | InstructionsContent | SpecContent, Field(discriminator="kind")
]


class ArtifactRevision(IRModel):
    artifact_id: str = Field(min_length=1)
    workspace: str = Field(min_length=1)
    kind: ArtifactKind
    name: str = Field(
        min_length=1, max_length=ARTIFACT_NAME_MAX_CHARS, pattern=ARTIFACT_NAME_PATTERN
    )
    revision: int = Field(ge=1)
    status: ArtifactStatus
    content: ArtifactContent
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    author: str = Field(min_length=1)
    approver: str | None = None
    self_approved: bool = False
    note: str = ""
    # 改訂の説明の由来（S73）。本人の申告で、サーバは由来を証明しない。note が空なら None
    note_source: NoteSource | None = None
    created_at: datetime
    approved_at: datetime | None = None

    @model_validator(mode="after")
    def _kind_matches_content(self) -> ArtifactRevision:
        if self.kind != self.content.kind:
            raise ValueError("kind と content.kind が一致しない")
        return self


class BundleMember(IRModel):
    artifact_id: str = Field(min_length=1)
    kind: ArtifactKind
    name: str = Field(min_length=1)
    revision: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class Bundle(IRModel):
    """ある時点の active な改訂の集合（ワークスペースごとの連番）。

    承認・規約の有効化のたびに作る。
    """

    workspace: str = Field(min_length=1)
    generation: int = Field(ge=1)
    policy_revision: int | None = None
    policy_hash: str | None = None
    members: list[BundleMember] = Field(default_factory=list)
    scope_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    # 署名した鍵の ID（#114、鍵の更新）。欄の無い古い bundle も読めるよう既定は None
    key_id: str | None = None
    signature: str = Field(
        min_length=1, description="サーバ鍵による HMAC。対象は signature 以外の正規 JSON"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class KnowledgeLock(IRModel):
    """`.speclino/lock.json`。この checkout が宣言している bundle（利用の証明ではない）。"""

    schema_version: Literal["0.1"] = "0.1"
    workspace: str = Field(min_length=1)
    github_repo_id: int
    bundle_generation: int = Field(ge=1)
    # bundle の規約（#114）。規約の有効化でも bundle の世代は進むので、鮮度の照合に使う。
    # 欄の無い古い lock も読めるよう既定は None（署名の対象からも None の欄は外す）
    policy_revision: int | None = None
    policy_hash: str | None = None
    members: list[BundleMember]
    files: dict[str, str] = Field(description="書き出したファイルの相対パス → sha256")
    # S75（A6）: 前回あって今回は無いファイル・管理区画。ゲートが残っていないことを照合する。
    # 欄の無い古い lock も読めるよう既定は空（空のときは署名の対象から外す）
    absent: list[str] = Field(
        default_factory=list, description="消したファイル・取り除いた管理区画の相対パス"
    )
    # S77: スキルを書き出すルートの集合（昇順）。取り込むエージェントの名乗りで足していき、次の
    # 取り込みで忘れないよう署名の対象にする。欄の無い古い lock は files のパスから推定する
    # （空のときは署名の対象から外す）
    skill_roots: list[SkillsRoot] = Field(
        default_factory=list, description="スキルを書き出すルート（昇順）"
    )
    issued_at: datetime
    expires_at: datetime
    # 署名した鍵の ID（#114、鍵の更新）。欄の無い古い lock も読めるよう既定は None
    key_id: str | None = None
    signature: str = Field(min_length=1)

    @model_validator(mode="after")
    def _expiry_after_issue(self) -> KnowledgeLock:
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at は issued_at より後")
        return self


class RebundleOutcome(IRModel):
    """鍵の更新の後の bundle の作り直し（`speclino rebundle-all`、#114）の 1 ワークスペース分。"""

    workspace: str
    previous_generation: int | None = None
    previous_key_id: str | None = None
    generation: int | None = Field(
        default=None, description="作った bundle の世代（作らなければ None）"
    )
    status: Literal["rebundled", "skipped", "failed"]
    detail: str = ""


class ResolvedKnowledge(IRModel):
    """bundle をあるプロジェクトに当てた結果。

    `services.knowledge_registry.resolve_for_project` が返す。
    """

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


class SuggestionOutcome(IRModel):
    """提案を写しの本文に当てて `compile_spec` を通し直した結果（S67）。IR は持たない。"""

    errors_before: int = Field(ge=0, description="提案を当てる前の本文のエラー数")
    errors_after: int = Field(ge=0, description="提案を当てた後の本文のエラー数")
    compiles: bool = Field(description="当てた後の本文がエラーなしで変換できるか")
    diagnostics_after: list[SpecDiagnostic] = Field(default_factory=list)


class SuggestionResult(Suggestion):
    """提案と、それを当てた後の検証の結果（S67、`services.spec_suggest.suggest` が返す）。"""

    after: SuggestionOutcome


class SpecSuggestions(IRModel):
    """REST の `POST /specs/suggest` の応答（S67）。提案と、今の本文のエラー数。"""

    errors_before: int = Field(ge=0)
    suggestions: list[SuggestionResult] = Field(default_factory=list)


__all__ = [
    "ARTIFACT_NAME_MAX_CHARS",
    "ARTIFACT_NAME_PATTERN",
    "BODY_MAX_BYTES",
    "MANAGED_BEGIN",
    "MANAGED_END",
    "ArtifactContent",
    "ResolvedKnowledge",
    "Suggestion",
    "SuggestionOutcome",
    "SuggestionResult",
    "ArtifactKind",
    "ArtifactRevision",
    "ArtifactScope",
    "ArtifactStatus",
    "Bundle",
    "BundleMember",
    "InstructionsContent",
    "InstructionsTarget",
    "KnowledgeLock",
    "RebundleOutcome",
    "SkillContent",
    "SkillsRoot",
    "SpecContent",
    "SpecSuggestions",
]
