"""SpecLino の Agent Card（`/.well-known/agent-card.json`。公開、認証不要）。"""

from __future__ import annotations

import json

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)

import speclino

A2A_PATH = "/a2a"
_BOUNDARY = "判定は検査器だけが行い、承認は PR で人が行う。"


def _examples(meta: dict[str, str], body: str) -> list[str]:
    """DataPart 版（JSON の DataPart ＋本文の TextPart）と front matter 版の書き方。"""
    front = "".join(f"{k}: {v}\n" for k, v in meta.items())
    return [
        f"DataPart: {json.dumps(meta, ensure_ascii=False)} / TextPart: {body}",
        f"---\n{front}---\n{body}",
    ]


def _skills() -> list[AgentSkill]:
    return [
        AgentSkill(
            id="spec_review",
            name="仕様のレビュー",
            description=(
                "仕様の Markdown の下書きを検証し、Spec IR の候補・診断・修正案（差分）を返す。"
                "あいまいな点があれば質問して入力待ちにする。リポジトリには書き込まない。"
                + _BOUNDARY
            ),
            tags=["spec", "review"],
            examples=_examples(
                {"skill": "spec_review", "repo": "owner/name", "behavior": "orders.cancel"},
                "### R-1\n注文は発送前ならキャンセルできる",
            ),
        ),
        AgentSkill(
            id="requirement_consult",
            name="要求の相談",
            description=(
                "要求の意味や判定の理由を、仕様・Spec IR・判定を根拠に説明する。"
                "コミットを指定すると検査を起動し、検査器の判定と反例をそのまま返す。" + _BOUNDARY
            ),
            tags=["spec", "requirement"],
            examples=_examples(
                {"skill": "requirement_consult", "repo": "owner/name", "requirement_id": "R-1"},
                "この要求は発送済みの注文にも効く？",
            ),
        ),
        AgentSkill(
            id="run_clarification",
            name="検査の後の聞き取り",
            description=(
                "検査の「要確認」の点を質問として返し、答えを補足として記録する。"
                "補足は正式な回答ではなく、判定にもゲートにも効かない（正式な回答は人が行う）。"
                + _BOUNDARY
            ),
            tags=["run", "clarification"],
            examples=_examples(
                {
                    "skill": "run_clarification",
                    "run_id": "00000000-0000-0000-0000-000000000000",
                },
                "要確認の点を聞かせて",
            ),
        ),
        AgentSkill(
            id="spec_suggest",
            name="仕様の下書きの相談",
            description=(
                "仕様の Markdown と Mermaid の図の下書きに、書き換えの提案"
                "（本文の位置と置換・理由）を返す。提案ごとに SpecLino の決定的な parser で"
                "検証し直した結果（前後のエラー数）を添える。IR は返さず、本文から IR を作るのは"
                "parser だけ。採否・保存は人が行う。" + _BOUNDARY
            ),
            tags=["spec", "suggest"],
            examples=_examples(
                {"skill": "spec_suggest"},
                "```mermaid\n%%@ id: order-status\n...\n```",
            ),
        ),
    ]


def build_agent_card(public_url: str) -> AgentCard:
    return AgentCard(
        name="SpecLino",
        description=(
            "仕様とコードの適合を検査する SpecLino の議論役。仕様のレビュー・要求の相談・"
            "検査の後の聞き取り・仕様の下書きの相談を行う。" + _BOUNDARY
        ),
        version=speclino.__version__,
        supported_interfaces=[
            AgentInterface(
                url=f"{public_url}{A2A_PATH}", protocol_binding="JSONRPC", protocol_version="1.0"
            )
        ],
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "text/markdown", "application/json"],
        security_schemes={
            "bearer": SecurityScheme(
                http_auth_security_scheme=HTTPAuthSecurityScheme(
                    scheme="bearer", description="ワークスペースの MCP トークン（slmcp_）"
                )
            )
        },
        security_requirements=[SecurityRequirement(schemes={"bearer": StringList()})],
        skills=_skills(),
    )
