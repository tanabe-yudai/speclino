"""会話（S73）の LLM の設定。台本の LLM は本番で使えない。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from speclino.adapters.llm.chat_scripted import ScriptedChatLlm
from speclino.services.knowledge_chat_prompt import SUMMARY_SCHEMA
from speclino.services.knowledge_chat_proposal import response_schema
from speclino.settings import Settings


def test_scripted_chat_llm_is_rejected_in_prod() -> None:
    with pytest.raises(ValidationError, match="chat_llm=scripted"):
        Settings(env="prod", chat_llm="scripted", bundle_signing_key="k")
    assert Settings(env="local", chat_llm="scripted").chat_llm == "scripted"
    assert Settings().chat_llm == "gemini"


async def test_scripted_chat_llm_answers_each_kind() -> None:
    llm = ScriptedChatLlm()
    for kind, field in (
        ("spec", "markdown"),
        ("skill", "skill_md"),
        ("instructions", "body"),
        ("policy", "rule"),
    ):
        gen = await llm.generate_json("", response_schema(kind))  # type: ignore[arg-type]
        assert field in json.loads(gen.text)["proposal"]
    summary = json.loads((await llm.generate_json("", SUMMARY_SCHEMA)).text)
    assert set(summary) == {"what", "why", "dropped"}
