"""project ごとの設定（`projects.settings`）とオンボーディングの段階の型（S47）。

design/2026-09-26-team-policies-and-dx.md 4 章の「設定」「オンボーディング」。読み方・導出は
`services/project_settings.py`。
"""

from __future__ import annotations

import re
import uuid
from typing import Final, Literal

from pydantic import Field, field_validator

from speclino.domain.common import IRModel

# ゲートを失敗にする判定。フェイルクローズ（CLAUDE.md）なので 3 つとも外せない
GateFailVerdict = Literal["VIOLATED", "UNKNOWN", "REVIEW"]
REQUIRED_FAIL_ON: Final[tuple[GateFailVerdict, ...]] = ("VIOLATED", "UNKNOWN", "REVIEW")

# GitHub のログイン名（英数字とハイフン、先頭は英数字、39 文字まで）。bot（`[bot]`）は承認者にしない
_GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")


class ProjectSettings(IRModel):
    """`projects.settings` の型。API はこの型で検証してから保存する。

    - `fail_on`: ゲートを失敗にする判定。違反・未検証・要確認のどれも外せない（弱められない）
    - `agent_enabled` / `test_run_enabled`: 証拠を集める機能の project ごとの有効・無効。
      サーバ全体の設定（環境変数）で無効なら、ここで有効にしても動かない（AND で効く）
    - `approvers`: PR コメントの `/speclino approve-spec` を受け付ける GitHub ログイン名
    """

    fail_on: list[GateFailVerdict] = Field(default_factory=lambda: list(REQUIRED_FAIL_ON))
    agent_enabled: bool = True
    test_run_enabled: bool = True
    approvers: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("fail_on")
    @classmethod
    def _fail_closed(cls, v: list[GateFailVerdict]) -> list[GateFailVerdict]:
        if len(v) != len(set(v)) or set(v) != set(REQUIRED_FAIL_ON):
            raise ValueError("違反・未検証・要確認はゲートの失敗から外せない（フェイルクローズ）")
        return v

    @field_validator("approvers")
    @classmethod
    def _github_logins(cls, v: list[str]) -> list[str]:
        bad = [login for login in v if not _GITHUB_LOGIN.fullmatch(login)]
        if bad:
            raise ValueError(f"GitHub のログイン名ではない: {', '.join(bad)}")
        if len(v) != len(set(v)):
            raise ValueError("承認者が重複している")
        return v


class StoredProjectSettings(IRModel):
    """保存値を読んだ結果。`invalid` なら保存値が型に合わず、安全側の値に置き換えている。"""

    settings: ProjectSettings
    invalid: bool = False


class ProjectSettingsView(IRModel):
    """設定画面の 1 project 分。

    `can_edit` は owner / approver のとき True。
    `can_edit_approvers`（PR の承認者の変更）は owner だけ。
    """

    project_id: uuid.UUID
    repo_full_name: str
    settings: ProjectSettings
    stored_invalid: bool
    can_edit: bool
    can_edit_approvers: bool


OnboardingStepId = Literal["app", "speclino", "first_run"]
# done: 到達済み / todo: まだ（やることがある） / unknown: 手元のデータでは確かめられない
OnboardingState = Literal["done", "todo", "unknown"]


class OnboardingStep(IRModel):
    id: OnboardingStepId
    state: OnboardingState
    detail: str


class ProjectOnboarding(IRModel):
    project_id: uuid.UUID
    repo_full_name: str
    steps: list[OnboardingStep]


class WorkspaceOnboarding(IRModel):
    """ワークスペースの各 project の段階。`can_edit_settings` は設定を変更できる role か。"""

    can_edit_settings: bool
    projects: list[ProjectOnboarding]


__all__ = [
    "REQUIRED_FAIL_ON",
    "GateFailVerdict",
    "OnboardingState",
    "OnboardingStep",
    "OnboardingStepId",
    "ProjectOnboarding",
    "ProjectSettings",
    "ProjectSettingsView",
    "StoredProjectSettings",
    "WorkspaceOnboarding",
]
