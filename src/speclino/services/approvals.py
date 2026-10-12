"""PR コメントのコマンド（`/speclino approve-spec <hash>` / answer / attest / rerun）。

承認者はサーバ側設定（projects.settings の approvers / approver_ids）だけで検証する（全体共通の
承認者は持たない）。照合は GitHub のユーザー ID で行う（`services/approvers.py`、S61）。
`author_association` は所属であって承認権限ではないので使わない。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from speclino.domain.spec_ir import ApprovalRecord
from speclino.services.approvers import is_approver

CommandKind = Literal["approve-spec", "answer", "attest", "rerun"]
_CMD_RE = re.compile(
    r"^\s*/speclino\s+(approve-spec|answer|attest|rerun)(?:\s+(.*?))?\s*$", re.MULTILINE
)
_HASH_RE = re.compile(r"^(removed:)?sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class Command:
    kind: CommandKind
    args: tuple[str, ...]


@dataclass(frozen=True)
class CommandDecision:
    accepted: bool
    reason: str
    command: Command | None = None


def parse_commands(body: str) -> list[Command]:
    out: list[Command] = []
    for m in _CMD_RE.finditer(body or ""):
        kind = m.group(1)
        args = tuple((m.group(2) or "").split())
        out.append(Command(kind, args))  # type: ignore[arg-type]
    return out


def authorize_command(
    cmd: Command,
    author_login: str,
    *,
    author_id: int | None,
    settings: Mapping[str, object],
) -> CommandDecision:
    """`settings` は project の設定の保存値。承認者は `is_approver` で GitHub のユーザー ID だけで
    照合する（ID の無い設定では誰も承認者にならない）。"""
    if cmd.kind == "rerun":
        return CommandDecision(True, "再実行はコメント権限があれば誰でも可", cmd)
    if not is_approver(settings, user_id=author_id, login=author_login):
        return CommandDecision(False, f"{author_login} は承認者ではない（監査に記録）", cmd)
    if cmd.kind == "approve-spec" and (len(cmd.args) != 1 or not _HASH_RE.match(cmd.args[0])):
        return CommandDecision(
            False, "approve-spec の引数は sha256:<hex> または removed:sha256:<hex>", cmd
        )
    if cmd.kind == "answer" and len(cmd.args) != 2:
        return CommandDecision(False, "answer の引数は <質問ID> <選択肢>", cmd)
    if cmd.kind == "attest" and not _attest_args_ok(cmd.args):
        return CommandDecision(
            False, "attest の引数は無し、または <指紋> <sha256:内容の hash> [ack]", cmd
        )
    return CommandDecision(True, "承認者のコマンド", cmd)


_CONTENT_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _attest_args_ok(args: tuple[str, ...]) -> bool:
    """`/speclino attest <指紋> <sha256:hash> [ack]`（ack は acknowledged_violation）か、引数なしの
    `/speclino attest`（その PR の最新の検査の要確認をすべて確定する）。"""
    if not args:
        return True
    if len(args) not in (2, 3) or not _CONTENT_HASH_RE.match(args[1]):
        return False
    return len(args) == 2 or args[2] == "ack"


def attest_disposition(args: tuple[str, ...]) -> Literal["attested", "acknowledged_violation"]:
    return "acknowledged_violation" if len(args) == 3 else "attested"


def approval_from_command(
    cmd: Command,
    approver: str,
    comment_url: str | None,
    spec_content_hash: str,
    now: datetime | None = None,
) -> ApprovalRecord:
    assert cmd.kind == "approve-spec" and cmd.args
    ir_hash = cmd.args[0]
    return ApprovalRecord(
        id=f"pr-comment:{ir_hash[-12:]}",
        approver=approver,
        approved_at=now or datetime.now(UTC),
        source="pr_comment",
        spec_content_hash=spec_content_hash,
        ir_content_hash=ir_hash,
        evidence_url=comment_url,
    )
