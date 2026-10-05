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
    "スキルを指定してください"
    "（spec_review / requirement_consult / run_clarification / spec_suggest）。"
    "書き方は Agent Card の examples を見てください"
)


class _TextNumberLoader(yaml.SafeLoader):
    """数に見える値も書いたとおりの文字列で読む（数字だけのコミット SHA や先頭 0 を壊さない）。"""


_TextNumberLoader.yaml_implicit_resolvers = {
    first: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag not in ("tag:yaml.org,2002:int", "tag:yaml.org,2002:float")
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


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
            meta = yaml.load(m.group(1), Loader=_TextNumberLoader)  # SafeLoader の派生
        except yaml.YAMLError as e:
            raise SkillRequestError(_EXPLAIN) from e
        if not isinstance(meta, dict):
            raise SkillRequestError(_EXPLAIN)
        body = body[m.end() :]
    try:
        return SkillRequest.model_validate({**meta, "text": body})
    except ValidationError as e:
        raise SkillRequestError(_EXPLAIN) from e
