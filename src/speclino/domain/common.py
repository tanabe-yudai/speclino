"""IR 全体で共有する基底型と参照型（PoC 6.1）。"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

SchemaVersion = Literal["0.1"]
SCHEMA_VERSION: Final[SchemaVersion] = "0.1"


class IRModel(BaseModel):
    """IR の全モデルの基底。未定義フィールドを拒否する（additionalProperties: false）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SnapshotRef(IRModel):
    """入力スナップショット（解決済みコミット、パス、内容ハッシュ）。"""

    snapshot_id: str = Field(min_length=1)
    repository: str | None = None
    commit: str | None = Field(default=None, description="解決済みコミット SHA")
    path: str | None = None
    content_hash: str | None = Field(default=None, description="sha256:... 形式")


class SourceRef(IRModel):
    """仕様またはコード上の位置。requirement_id か symbol のどちらかを持つ。"""

    snapshot_id: str = Field(min_length=1)
    requirement_id: str | None = None
    symbol: str | None = Field(default=None, description="完全修飾シンボル")
    path: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)


Precision = Literal["exact_in_model", "overapproximate", "incomplete"]
ClaimScope = Literal[
    "memory_state",
    "command_invocation",
    "service_outcome",
    "http_response",
    "persisted_state",
]
