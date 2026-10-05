"""base/head の Rule 差分と承認の適用（FR 017 / AC-010、PoC 12.1）。

必須の検証集合は信頼済みの base（デフォルトブランチにマージ済み）から確定する。head で
追加・変更された Rule は、承認記録（`/speclino approve-spec <hash>` または base 由来）が無ければ
未承認として扱い、削除された Rule は「承認なしの削除」として REVIEW: stale_approval を残す。
head の仕様だけを見て検証集合を決めない（削除で検証を消せないようにする）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from speclino.conformance.reasons import finding_id
from speclino.domain.finding import Finding
from speclino.domain.spec_ir import ApprovalRecord, Rule, SpecIR
from speclino.spec.spec_validator import rule_content_hash

ChangeStatus = Literal["added", "changed", "removed", "unchanged"]
BASE_APPROVAL_ID = "base-trusted"
BASE_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# サーバ側の承認の記録に本文のハッシュが無いときの値（review_run_service が入れる）
UNKNOWN_SPEC_HASH = "sha256:unknown"


@dataclass(frozen=True)
class ObligationChange:
    rule_id: str
    status: ChangeStatus
    base_hash: str | None
    head_hash: str | None
    approved: bool


def diff_rules(base: list[Rule], head: list[Rule]) -> list[ObligationChange]:
    base_h = {r.id: rule_content_hash(r) for r in base}
    head_h = {r.id: rule_content_hash(r) for r in head}
    out: list[ObligationChange] = []
    for rid in sorted(set(base_h) | set(head_h)):
        b, h = base_h.get(rid), head_h.get(rid)
        if b is None:
            status: ChangeStatus = "added"
        elif h is None:
            status = "removed"
        elif b == h:
            status = "unchanged"
        else:
            status = "changed"
        out.append(ObligationChange(rid, status, b, h, approved=status == "unchanged"))
    return out


def approved_hashes(approvals: list[ApprovalRecord]) -> set[str]:
    return {a.ir_content_hash for a in approvals}


def effective_spec(
    base: SpecIR | None,
    head: SpecIR,
    approvals: list[ApprovalRecord],
) -> tuple[SpecIR, list[ObligationChange], list[Finding]]:
    """検査に使う Spec IR、差分、削除に対する Finding を返す。

    - base が無い（初回）: head ファイル内の承認記録も使う（ブートストラップ）。
    - unchanged: base 由来として承認済み（approval_ref を base-trusted に付け替える）。
    - added / changed: **サーバ側の承認記録**（`/speclino approve-spec`）に head Rule のハッシュが
      あれば承認済み、無ければ approval_ref=None（engine が REVIEW: stale_approval にする）。
      head ファイル内の承認記録は PR で改ざんできるので使わない。
    - removed: `removed:<base_hash>` の承認が無ければ REVIEW: stale_approval。
    """
    # サーバ側の承認（コメント）は本文のハッシュを持たない。承認の対象は要求の解釈（IR の
    # ハッシュ）なので、head の本文のハッシュで補う。補わないと検証器が E_SPEC_HASH_MISMATCH にする
    approvals = [
        a.model_copy(update={"spec_content_hash": head.spec_content_hash})
        if a.spec_content_hash == UNKNOWN_SPEC_HASH and head.spec_content_hash
        else a
        for a in approvals
    ]
    hashes = approved_hashes(approvals)
    if base is None:
        hashes |= approved_hashes(head.approvals)
    base_rules = base.rules if base else []
    changes = diff_rules(base_rules, head.rules)
    base_by_id = {r.id: r for r in base_rules}
    base_approval = ApprovalRecord(
        id=BASE_APPROVAL_ID,
        approver="default-branch",
        approved_at=BASE_EPOCH,  # 合成記録。実際の承認は「デフォルトブランチへのマージ」
        source="merged_default_branch",
        spec_content_hash=(
            base.spec_content_hash if base and base.spec_content_hash else "sha256:base"
        ),
        ir_content_hash="sha256:base",
    )
    new_rules: list[Rule] = []
    extra: list[Finding] = []
    resolved: list[ObligationChange] = []
    head_by_id = {r.id: r for r in head.rules}
    for ch in changes:
        if ch.status == "removed":
            removed = base_by_id[ch.rule_id]
            approved = ch.base_hash is not None and f"removed:{ch.base_hash}" in hashes
            resolved.append(ObligationChange(ch.rule_id, ch.status, ch.base_hash, None, approved))
            if not approved:
                extra.append(
                    Finding(
                        id=finding_id(removed.id, removed.behavior_id, None, "stale_approval"),
                        requirement_id=removed.id,
                        behavior_id=removed.behavior_id,
                        verdict="REVIEW",
                        category="stale_approval",
                        severity="high" if removed.level == "mandatory" else "low",
                        message="base に存在する要求が head で削除されているが、削除の承認が無い",
                        claim_scope=removed.claim_scope,
                        spec_refs=removed.source_refs,
                        base_head_status="existing",
                        next_steps=[
                            f"削除を承認するには `/speclino approve-spec removed:{ch.base_hash}`",
                            "または要求を specs/ir に戻す",
                        ],
                    )
                )
            continue
        rule = head_by_id[ch.rule_id]
        if ch.status == "unchanged" and base is not None:
            new_rules.append(rule.model_copy(update={"approval_ref": BASE_APPROVAL_ID}))
            resolved.append(ch)
            continue
        # added / changed（または base 無し）: 明示承認が要る
        approved = ch.head_hash in hashes or (
            base is None
            and rule.approval_ref is not None
            and any(a.id == rule.approval_ref for a in head.approvals)
        )
        resolved.append(
            ObligationChange(ch.rule_id, ch.status, ch.base_hash, ch.head_hash, approved)
        )
        if not approved:
            new_rules.append(rule.model_copy(update={"approval_ref": None}))
        elif ch.head_hash in hashes:
            ref = next(
                (a.id for a in approvals if a.ir_content_hash == ch.head_hash), rule.approval_ref
            )
            new_rules.append(rule.model_copy(update={"approval_ref": ref}))
        else:
            new_rules.append(rule)
    all_approvals = [*approvals, *(head.approvals if base is None else [])]
    if base is not None:
        all_approvals.append(base_approval)
    spec = head.model_copy(update={"rules": new_rules, "approvals": all_approvals})
    return spec, resolved, extra
