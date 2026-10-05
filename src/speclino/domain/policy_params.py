"""規約の検査器ごとのパラメーター（`PolicyVerifierRef.params` の型）。

型定義のみ。検査器は `params` をこのモデルで検証し、不正なら `ValueError`（pydantic の
`ValidationError` は `ValueError` の派生）にする。未定義のキーは拒否する（綴り間違いを黙って
無視しない）。
"""

from __future__ import annotations

from typing import Annotated, Literal

import regex
from pydantic import AfterValidator, Field, model_validator

from speclino.domain.common import IRModel

MAX_PATTERN_LENGTH = 256


def _validate_pattern(value: str) -> str:
    """利用者入力の正規表現: 長さ上限とコンパイル可否（ReDoS 対策の一部）。"""
    if len(value) > MAX_PATTERN_LENGTH:
        raise ValueError(f"正規表現は {MAX_PATTERN_LENGTH} 文字以下にしてください")
    try:
        regex.compile(value)
    except regex.error as e:
        raise ValueError(f"正規表現として不正です: {e}") from e
    return value


RegexPattern = Annotated[str, AfterValidator(_validate_pattern)]


class ApiErrorFormatParams(IRModel):
    """`api.error_format`。"""

    openapi_path: str = "openapi.json"
    error_schema: str = "ErrorResponse"
    api_glob: str = "src/speclino/api/**/*.py"
    helper_symbol: str = "api_error"
    success_envelope_schema: str | None = Field(
        default=None,
        description="指定すると 2xx の JSON 応答がこのスキーマで包まれること（allOf の先頭でも可）",
    )


class ApiConventionsParams(IRModel):
    """`api.conventions`。`openapi_path` と `api_glob` の少なくとも一方を指定する。"""

    openapi_path: str | None = None
    api_glob: str | None = None
    path_case: Literal["kebab", "snake"] | None = None
    require_plural_collections: bool = False
    version_prefix: str | None = None
    allowed_methods: list[str] | None = None
    require_response_model: bool = False

    @model_validator(mode="after")
    def _needs_an_input(self) -> ApiConventionsParams:
        if self.openapi_path is None and self.api_glob is None:
            raise ValueError("openapi_path と api_glob の少なくとも一方が必要です")
        return self


class NamingIdentifiersParams(IRModel):
    """`naming.identifiers`。各正規表現は fullmatch で評価する。"""

    function: RegexPattern | None = None
    class_: RegexPattern | None = Field(default=None, alias="class")
    constant: RegexPattern | None = None
    module: RegexPattern | None = None
    forbidden_words: list[str] = Field(default_factory=list)


class ErrorMessageFormatParams(IRModel):
    """`error_message.format`。"""

    call_names: list[str] = Field(default_factory=lambda: ["api_error", "HTTPException"])
    message_arg: str = "message"
    pattern: RegexPattern | None = None
    require_code: bool = False
    forbid_trailing_period: bool = False
    language: Literal["ja", "en"] | None = None


class LintPresetParams(IRModel):
    """`lint.preset`。"""

    tool: Literal["ruff", "eslint"]
    config_path: str
    required_rules: list[str] = Field(default_factory=list)
    ci_glob: str | None = None
    ci_command_pattern: RegexPattern | None = None

    @model_validator(mode="after")
    def _ci_pattern_needs_glob(self) -> LintPresetParams:
        if self.ci_command_pattern is not None and self.ci_glob is None:
            raise ValueError("ci_command_pattern には ci_glob が必要です")
        return self
