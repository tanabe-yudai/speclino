"""A2A の最初のメッセージからスキルの指定を読む（DataPart か front matter）。"""

from __future__ import annotations

import pytest
from a2a.helpers import new_data_part, new_text_part
from a2a.types import Message, Part, Role

from speclino.agent.consultant.request import SkillRequestError, parse_skill_request


def _msg(*parts: Part) -> Message:
    return Message(role=Role.ROLE_USER, message_id="m", parts=list(parts))


def test_data_part_selects_skill_and_text_part_is_body() -> None:
    req = parse_skill_request(
        _msg(new_data_part({"skill": "spec_review", "repo": "o/r"}), new_text_part("### R-1\n本文"))
    )
    assert req.skill == "spec_review" and req.repo == "o/r" and req.text == "### R-1\n本文"


def test_front_matter_selects_skill_for_text_only_clients() -> None:
    req = parse_skill_request(
        _msg(
            new_text_part(
                "---\nskill: requirement_consult\nrepo: o/r\nrequirement_id: R-1\n---\n"
                "この要求の意味は？"
            )
        )
    )
    assert (req.skill, req.requirement_id, req.text) == (
        "requirement_consult",
        "R-1",
        "この要求の意味は？",
    )


@pytest.mark.parametrize("text", ["本文だけ", "---\nskill: approve\n---\nx", "---\n: [\n---\nx"])
def test_missing_or_unknown_skill_is_explained(text: str) -> None:
    with pytest.raises(SkillRequestError, match="spec_review"):
        parse_skill_request(_msg(new_text_part(text)))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1234567", "1234567"), ("0123456", "0123456"), ("1e10", "1e10"), ("12.50", "12.50")],
)
def test_front_matter_numbers_are_read_as_written_text(raw: str, expected: str) -> None:
    """数字だけのコミット SHA を数として読み、型の誤りで分かりにくく断らない。"""
    req = parse_skill_request(
        _msg(new_text_part(f"---\nskill: requirement_consult\nrepo: o/r\ncommit: {raw}\n---\nQ"))
    )
    assert req.commit == expected
