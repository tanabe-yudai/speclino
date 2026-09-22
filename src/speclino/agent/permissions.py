"""エージェントのツール権限（FR 014）。

権限はサーバ側設定（projects.settings と環境変数）だけから作る。対象リポジトリ内の文書や
モデルへ提示した文書から権限を変更できない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

MUST_TOOLS: frozenset[str] = frozenset(
    {"analyze_focus", "repo_search", "policy_check", "plan_record"}
)
SHOULD_TOOLS: frozenset[str] = frozenset(
    {"test_run", "contract_lookup", "docs_lookup", "human_ask"}
)
# 宣言だけして常に拒否するハニーポット。呼ばれたら injection_suspected として監査する（AC-012）
HONEYPOT_TOOLS: frozenset[str] = frozenset({"repo_write", "deploy_run"})


DEFAULT_TOOLS: frozenset[str] = MUST_TOOLS | frozenset({"contract_lookup", "human_ask", "test_run"})


@dataclass(frozen=True)
class AgentPermissions:
    allowed_tools: frozenset[str] = DEFAULT_TOOLS
    max_tool_calls: int = 20
    max_seconds: int = 600
    max_llm_calls: int = 40
    test_run_enabled: bool = False
    honeypots: frozenset[str] = HONEYPOT_TOOLS
    allowed_paths: tuple[str, ...] = field(default=("**",))

    def allows(self, tool: str) -> bool:
        """純粋関数。ツール名が許可集合にあり、ハニーポットでないときだけ True。"""
        if tool in self.honeypots:
            return False
        if tool == "test_run" and not self.test_run_enabled:
            return False
        return tool in self.allowed_tools

    def is_honeypot(self, tool: str) -> bool:
        return tool in self.honeypots
