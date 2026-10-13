"""CI-001: GitHub Actions ワークフローの必須条件を検証する（S36）。

ワークフロー YAML は構文的に読むだけで、実行しない（`gh` や `act` を呼ばない）。判定する条件は
すべて params で調整できる。

1. ``require_pull_request_trigger``: ``on:`` に ``pull_request`` トリガーが含まれること
2. ``required_steps``: 指定した各コマンド（例 ``"make lint"``）が、いずれかのジョブ・ステップの
   ``run:`` の **1 行として完全一致** で現れること（``echo make lint`` のような部分一致は
   認めない）。さらにそのステップと所属ジョブに ``if:`` が無く（実行条件は静的に判定できない
   ため、条件付きしか無ければ UNKNOWN）、``continue-on-error: true`` でない（失敗が伝播しない
   ため VIOLATED）こと
3. ``forbid_events``: ``on:`` に指定イベント（例 ``"pull_request_target"``）が含まれないこと
4. ``require_read_permissions``: トップレベル ``permissions`` が宣言され、読み取り専用である
   こと。``allowed_write_permissions`` に列挙したスコープ（例 ``"id-token"``、deploy 用）は
   書込みでも例外にできる

``jobs.<id>.uses``（再利用ワークフロー呼出し）はファイルの外を追えないため UNKNOWN
（unsupported_analysis）。

AC-005: 「本番承認（environment の protection rules）」のような外部状態はこのツールから
取得できない。params ``require_environment_protection: true`` が指定され、かつ他に決定的な
VIOLATED が無いときは UNKNOWN を返し、message に「外部設定を取得できないため確認不能」と書く
（他の判定が VIOLATED ならそちらを優先し、確定できる違反を UNKNOWN で覆い隠さない）。

YAML の ``on:`` キーは PyYAML の YAML 1.1 resolver によって bool の ``True`` にパースされる
（GitHub Actions のよく知られた落とし穴）。文字列 ``"on"`` と bool ``True`` の両方を見る。

ワークフローファイルごとに Finding は1件。requirement_id は ``<rule.id>:<path>``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import yaml

from speclino.domain.common import SourceRef
from speclino.domain.finding import Category, Finding, Severity, Verdict
from speclino.domain.policy import PolicyRule
from speclino.verifiers.base import Snapshot

VERIFIER_ID = "ci.required_conditions"
VERIFIER_VERSION = "1.0.0"

_EXTERNAL_STATE_MESSAGE = "外部設定を取得できないため確認不能"
_VERDICT_RANK: dict[Verdict, int] = {"VIOLATED": 2, "UNKNOWN": 1, "VERIFIED_IN_SCOPE": 0}


@dataclass
class _Issue:
    verdict: Verdict
    category: Category
    detail: str


def _trigger_events(doc: dict[Any, Any]) -> set[str] | None:
    """``on:`` の値からトリガーイベント名の集合を返す。``on:`` が無ければ None。"""
    on_value = doc.get("on", doc.get(True))
    if on_value is None:
        return None
    if isinstance(on_value, str):
        return {on_value}
    if isinstance(on_value, list):
        return {str(v) for v in on_value}
    if isinstance(on_value, dict):
        return {str(k) for k in on_value}
    return set()


@dataclass
class _RunStep:
    lines: list[str]  # run の各行（前後の空白を除き、空行は除く）
    conditional: bool  # step または job に if: がある
    tolerates_failure: bool  # step または job が continue-on-error: true


def _iter_run_steps(doc: dict[Any, Any]) -> list[_RunStep]:
    """全ジョブの ``run:`` ステップを、実行条件と失敗伝播の情報つきで集める。"""
    runs: list[_RunStep] = []
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return runs
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get("run"), str):
                continue
            runs.append(
                _RunStep(
                    lines=[ln.strip() for ln in step["run"].splitlines() if ln.strip()],
                    conditional="if" in job or "if" in step,
                    tolerates_failure=(
                        job.get("continue-on-error") is True
                        or step.get("continue-on-error") is True
                    ),
                )
            )
    return runs


StepStatus = Literal["ok", "missing", "conditional", "tolerates_failure"]


def _required_step_status(command: str, steps: list[_RunStep]) -> StepStatus:
    """コマンドが無条件かつ失敗伝播つきで実行されるステップを探す。"""
    matches = [st for st in steps if command in st.lines]
    if not matches:
        return "missing"
    if any(not st.conditional and not st.tolerates_failure for st in matches):
        return "ok"
    if all(st.tolerates_failure for st in matches):
        return "tolerates_failure"
    return "conditional"


def _has_reusable_workflow_call(doc: dict[Any, Any]) -> bool:
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return False
    return any(isinstance(job, dict) and "uses" in job for job in jobs.values())


def _permissions_value(doc: dict[Any, Any]) -> dict[str, str] | str | None:
    """トップレベル ``permissions``。無ければ None。文字列（read-all/write-all）かマップ。"""
    if "permissions" not in doc:
        return None
    value = doc["permissions"]
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


def _trigger_issues(doc: dict[Any, Any], params: dict[str, Any]) -> list[_Issue]:
    """条件 1: ``pull_request`` トリガーがあること。"""
    if not params.get("require_pull_request_trigger", True):
        return []
    events = _trigger_events(doc)
    if events is not None and "pull_request" in events:
        return []
    return [_Issue("VIOLATED", "missing_effect", "pull_request トリガーがありません")]


def _required_step_issues(
    doc: dict[Any, Any], params: dict[str, Any], *, has_reusable: bool
) -> tuple[list[_Issue], list[str]]:
    """条件 2: 必須コマンドが無条件かつ失敗伝播つきで実行されること。

    見つからなかったコマンドも返す（再利用ワークフローの説明文に使う）。
    """
    required_steps: list[str] = list(params.get("required_steps", []))
    if not required_steps:
        return [], []

    steps = _iter_run_steps(doc)
    status = {cmd: _required_step_status(cmd, steps) for cmd in required_steps}
    missing = [cmd for cmd, st in status.items() if st == "missing"]
    tolerated = [cmd for cmd, st in status.items() if st == "tolerates_failure"]
    conditional = [cmd for cmd, st in status.items() if st == "conditional"]

    issues: list[_Issue] = []
    # 再利用ワークフロー（uses:）呼出しがあると、そのジョブの中身は追えないため、
    # このファイル内に見つからないだけでは missing_effect と断定しない
    # （`_reusable_workflow_issue` で unsupported_analysis にまとめる）。
    if missing and not has_reusable:
        issues.append(
            _Issue(
                "VIOLATED",
                "missing_effect",
                f"必須ステップがありません（run の 1 行として完全一致が必要）: "
                f"{', '.join(missing)}",
            )
        )
    if tolerated:
        issues.append(
            _Issue(
                "VIOLATED",
                "constraint_mismatch",
                "必須ステップが continue-on-error のため失敗が伝播しません: "
                f"{', '.join(tolerated)}",
            )
        )
    if conditional:
        issues.append(
            _Issue(
                "UNKNOWN",
                "unsupported_analysis",
                "必須ステップに if: が付いており実行されるか静的に判定できません: "
                f"{', '.join(conditional)}",
            )
        )
    return issues, missing


def _forbidden_event_issues(doc: dict[Any, Any], params: dict[str, Any]) -> list[_Issue]:
    """条件 3: ``forbid_events`` に挙げたイベントが使われていないこと。"""
    forbid_events: list[str] = list(params.get("forbid_events", []))
    if not forbid_events:
        return []
    events = _trigger_events(doc) or set()
    present = sorted(e for e in forbid_events if e in events)
    if not present:
        return []
    return [
        _Issue(
            "VIOLATED", "forbidden_effect", f"禁止イベントが使われています: {', '.join(present)}"
        )
    ]


def _permission_issues(doc: dict[Any, Any], params: dict[str, Any]) -> list[_Issue]:
    """条件 4: トップレベル ``permissions`` が宣言され、読み取り専用であること。"""
    if not params.get("require_read_permissions", True):
        return []
    permissions = _permissions_value(doc)
    if permissions is None:
        detail = "トップレベルの permissions が宣言されていません"
    elif isinstance(permissions, str):
        if permissions == "read-all":
            return []
        detail = f"permissions が読み取り専用ではありません: {permissions}"
    else:
        allowed_write = set(params.get("allowed_write_permissions", []))
        write_scopes = sorted(
            k for k, v in permissions.items() if v == "write" and k not in allowed_write
        )
        if not write_scopes:
            return []
        detail = f"書込み権限があります: {', '.join(write_scopes)}"
    return [_Issue("VIOLATED", "constraint_mismatch", detail)]


def _reusable_workflow_issue(missing_steps: list[str]) -> _Issue:
    """``jobs.<id>.uses`` はファイルの外を追えないため UNKNOWN。"""
    if missing_steps:
        detail = (
            f"必須ステップ（{', '.join(missing_steps)}）がこのファイル内に見つからず、"
            "再利用ワークフロー（jobs.<id>.uses）の中身を追えないため確認できません"
        )
    else:
        detail = "再利用ワークフロー（jobs.<id>.uses）は中身を追えません"
    return _Issue("UNKNOWN", "unsupported_analysis", detail)


def _collect_issues(doc: dict[Any, Any], params: dict[str, Any]) -> list[_Issue]:
    has_reusable = _has_reusable_workflow_call(doc)
    step_issues, missing_steps = _required_step_issues(doc, params, has_reusable=has_reusable)
    issues = [
        *_trigger_issues(doc, params),
        *step_issues,
        *_forbidden_event_issues(doc, params),
        *_permission_issues(doc, params),
    ]
    if has_reusable:
        issues.append(_reusable_workflow_issue(missing_steps))
    # AC-005: 確定できる違反があるときは、外部状態の確認不能で覆い隠さない
    if params.get("require_environment_protection", False) and not any(
        i.verdict == "VIOLATED" for i in issues
    ):
        issues.append(_Issue("UNKNOWN", "unsupported_analysis", _EXTERNAL_STATE_MESSAGE))
    return issues


def _overall(issues: list[_Issue]) -> tuple[Verdict, Category]:
    if not issues:
        return "VERIFIED_IN_SCOPE", "conformant"
    worst_verdict = max((i.verdict for i in issues), key=lambda v: _VERDICT_RANK[v])
    chosen = next(i for i in issues if i.verdict == worst_verdict)
    return chosen.verdict, chosen.category


class CiRequiredVerifier:
    id = VERIFIER_ID
    version = VERIFIER_VERSION

    def run(self, rule: PolicyRule, snapshot: Snapshot) -> list[Finding]:
        params = rule.verifier_ref.params
        paths = sorted({p for pattern in rule.applies_to.paths for p in snapshot.glob(pattern)})
        return [self._check_file(rule, snapshot, path, params) for path in paths]

    def _check_file(
        self, rule: PolicyRule, snapshot: Snapshot, path: str, params: dict[str, Any]
    ) -> Finding:
        requirement_id = f"{rule.id}:{path}"
        text = snapshot.read_text(path) or ""
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as e:
            return self._unsupported(rule, path, f"YAML を解析できません: {e}")
        if not isinstance(doc, dict):
            detail = "ワークフローの形式が不正です（トップレベルがマップでない）"
            return self._unsupported(rule, path, detail)

        issues = _collect_issues(doc, params)
        verdict, category = _overall(issues)

        if not issues:
            message = f"{path}: CI-001 の必須条件をすべて満たしています"
            actual = "違反なし"
        else:
            message = f"{path}: " + "; ".join(i.detail for i in issues)
            actual = "\n".join(f"- {i.detail}" for i in issues)

        return Finding(
            id=f"policy:{requirement_id}",
            requirement_id=requirement_id,
            behavior_id=f"policy:{rule.verifier_ref.id}",
            verdict=verdict,
            category=category,
            severity=self._severity(rule),
            message=message,
            expected=(
                "pull_request トリガー、必須ステップ、禁止イベント無し、"
                "読み取り専用の permissions を満たすこと"
            ),
            actual=actual,
            claim_scope="persisted_state",
            code_refs=[SourceRef(snapshot_id=snapshot.snapshot_id, path=path)],
        )

    def _severity(self, rule: PolicyRule) -> Severity:
        return "high" if rule.level == "mandatory" else "medium"

    def _unsupported(self, rule: PolicyRule, path: str, detail: str) -> Finding:
        requirement_id = f"{rule.id}:{path}"
        return Finding(
            id=f"policy:{requirement_id}",
            requirement_id=requirement_id,
            behavior_id=f"policy:{rule.verifier_ref.id}",
            verdict="UNKNOWN",
            category="unsupported_analysis",
            severity=self._severity(rule),
            message=f"{path}: {detail}",
            actual=detail,
            claim_scope="persisted_state",
        )
