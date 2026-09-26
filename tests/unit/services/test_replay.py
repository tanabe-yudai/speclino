"""replay（AC-013）: 凍結した成果物から同じ判定が再現し、改ざんは検出される。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from speclino.cli import app
from speclino.domain.finding import Finding, Findings
from speclino.domain.policy import PolicyRevision
from speclino.services.check_pipeline import run_check, write_result
from speclino.services.replay_service import _check_policy_findings_against, replay
from tests.fixtures import cancel_order as co
from tests.unit.services.test_check_pipeline import (
    mandatory_rule,
    orders_request,
    team_revision,
)


def test_replay_reproduces_saved_findings(tmp_path: Path) -> None:
    override = tmp_path / "code-ir.json"
    override.write_text(co.RESTORE_ADMIN_ONLY.model_dump_json())
    out = tmp_path / "out"
    write_result(run_check(orders_request(code_ir_override=override, run_id="r1")), out)
    assert (out / "domain.json").exists()
    res = replay(out)
    assert res.identical and res.compared == 9 and res.differences == []


def test_replay_detects_tampered_findings(tmp_path: Path) -> None:
    override = tmp_path / "code-ir.json"
    override.write_text(co.RESTORE_ADMIN_ONLY.model_dump_json())
    out = tmp_path / "out"
    write_result(run_check(orders_request(code_ir_override=override, run_id="r1")), out)
    data = json.loads((out / "findings.json").read_text())
    for f in data["findings"]:
        if f["requirement_id"] == "R-CANCEL-04":
            f["verdict"], f["category"] = "VERIFIED_IN_SCOPE", "conformant"
    (out / "findings.json").write_text(json.dumps(data))
    res = replay(out)
    assert not res.identical and any("R-CANCEL-04" in d for d in res.differences)
    cli = CliRunner().invoke(app, ["replay", str(out)])
    assert cli.exit_code == 1 and "不一致" in cli.output


# ---------------------------------------------------------------- S42 チーム規約の改訂で再評価


def _write_run_with_team_policy(tmp_path: Path, revision: PolicyRevision) -> Path:
    override = tmp_path / "code-ir.json"
    override.write_text(co.RESTORE_ADMIN_ONLY.model_dump_json())
    out = tmp_path / "out"
    req = orders_request(code_ir_override=override, run_id="r1", team_policy=revision)
    write_result(run_check(req), out)
    return out


def test_replay_without_resolve_revision_ignores_recorded_policy(tmp_path: Path) -> None:
    """resolve_revision を渡さなければ、従来どおり規約を考慮せず再評価する。"""
    revision = team_revision([mandatory_rule("TEAM-001", ["team-only/*.never"])])
    out = _write_run_with_team_policy(tmp_path, revision)
    res = replay(out)
    assert res.identical and res.differences == []


def test_replay_resolves_recorded_revision_and_matches(tmp_path: Path) -> None:
    """resolve_revision が同じ改訂を返せば、再評価は保存結果と一致する。"""
    revision = team_revision([mandatory_rule("TEAM-001", ["team-only/*.never"])])
    out = _write_run_with_team_policy(tmp_path, revision)

    calls: list[tuple[str, int]] = []

    def resolve(team: str, rev: int) -> PolicyRevision | None:
        calls.append((team, rev))
        return revision

    res = replay(out, resolve_revision=resolve)
    assert res.identical and res.differences == []
    assert calls == [("acme", 1)]


def test_replay_fails_when_recorded_revision_cannot_be_resolved(tmp_path: Path) -> None:
    """黙って規約なしで再評価すると過去の判定と一致しない理由が分からなくなるため、失敗させる。"""
    revision = team_revision([mandatory_rule("TEAM-001", ["team-only/*.never"])])
    out = _write_run_with_team_policy(tmp_path, revision)

    with pytest.raises(ValueError, match="解決できない"):
        replay(out, resolve_revision=lambda team, rev: None)

    cli = CliRunner().invoke(app, ["replay", str(out)])
    assert cli.exit_code == 0  # CLI は resolve_revision を渡さないので従来どおり再評価する


# ------------------------------ S42: 改訂と保存済み規約 Finding の整合


def _policy_finding(rid: str, severity: str, behavior_id: str = "policy:make_targets") -> Finding:
    return Finding(
        id=f"policy:{rid}:x",
        requirement_id=rid,
        behavior_id=behavior_id,
        verdict="VIOLATED",
        category="missing_effect",
        severity=severity,  # type: ignore[arg-type]
        message="m",
        claim_scope="persisted_state",
    )


def test_saved_policy_findings_must_match_the_resolved_revision() -> None:
    """検証器は再実行できない（スナップショットが無い）が、改訂との整合は確かめられる。

    manifest が別の改訂を指していれば、保存された Finding の severity が改訂の level と
    食い違う。これを黙って通すと「規約を変えても過去の判定は変わらない」が空手形になる。
    """
    revision = team_revision(rules=[mandatory_rule("MAKE-001", ["Makefile"])])
    rule_id = revision.package.rules[0].id

    # 整合している（mandatory → high）
    _check_policy_findings_against(revision, Findings(findings=[_policy_finding(rule_id, "high")]))

    # 食い違う（mandatory なのに medium）
    with pytest.raises(ValueError, match="整合しない"):
        _check_policy_findings_against(
            revision, Findings(findings=[_policy_finding(rule_id, "medium")])
        )


def test_project_local_and_non_policy_findings_are_not_checked() -> None:
    """改訂に無い id はプロジェクト固有の追加規約なので照合しない。規約以外も無関係。"""
    revision = team_revision(rules=[mandatory_rule("MAKE-001", ["Makefile"])])
    _check_policy_findings_against(
        revision,
        Findings(
            findings=[
                _policy_finding("TEAM-999", "medium"),
                _policy_finding("R-CANCEL-04", "medium", behavior_id="cancel_order"),
            ]
        ),
    )
