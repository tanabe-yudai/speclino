"""A2A の最初のメッセージからスキルの指定（`SkillRequest`）を読む。

DataPart（JSON）があればそれを使い、無ければ本文の先頭の YAML front matter を読む
（テキストしか送れないクライアント向け）。
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from a2a.helpers import get_data_parts, get_text_parts
from a2a.types import Message
from pydantic import ValidationError

from speclino.domain.a2a import SkillRequest

_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_EXPLAIN = (
    "スキルを指定してください（spec_review / requirement_consult / run_clarification）。"
    "書き方は Agent Card の examples を見てください"
)


class SkillRequestError(ValueError):
    """スキルの指定が読めない。メッセージは相手にそのまま見せてよい文。"""


def parse_skill_request(message: Message) -> SkillRequest:
    body = "\n".join(get_text_parts(message.parts))
    data = next((d for d in get_data_parts(message.parts) if isinstance(d, dict)), None)
    meta: Any
    if data is not None:
        meta = data
    else:
        m = _FRONT_MATTER_RE.match(body)
        if m is None:
            raise SkillRequestError(_EXPLAIN)
        try:
            meta = yaml.safe_load(m.group(1))
        except yaml.YAMLError as e:
            raise SkillRequestError(_EXPLAIN) from e
        if not isinstance(meta, dict):
            raise SkillRequestError(_EXPLAIN)
        body = body[m.end() :]
    try:
        return SkillRequest.model_validate({**meta, "text": body})
    except ValidationError as e:
        raise SkillRequestError(_EXPLAIN) from e
