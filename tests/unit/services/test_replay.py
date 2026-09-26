"""replay（AC-013）: 凍結した成果物から同じ判定が再現し、改ざんは検出される。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from speclino.cli import app
from speclino.domain.policy import PolicyRevision
from speclino.services.check_pipeline import run_check, write_result
from speclino.services.replay_service import replay
from tests.fixtures import cancel_order as co
from tests.unit.services.test_check_pipeline import _team_revision, orders_request


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


# ---------------------------------------------------------------- S42 チーム規約の改訂照合


def _run_with_team_policy(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    revision = _team_revision(exceptions_allowed=True)
    write_result(run_check(orders_request(team_policy=revision, run_id="r1")), out)
    return out


def test_replay_fails_when_policy_revision_recorded_but_no_resolver(tmp_path: Path) -> None:
    """codex 実測: CLI は `replay(run_dir)` としか呼ばないため resolver が渡らず、改訂の検査が
    一切走らなかった穴。既定では resolver 無し・改訂ありは失敗させる。"""
    out = _run_with_team_policy(tmp_path)
    with pytest.raises(ValueError, match="resolve_revision"):
        replay(out)


def test_replay_no_policy_check_skips_team_policy_verification(tmp_path: Path) -> None:
    """`require_policy_check=False`（CLI の --no-policy-check）は明示的に飛ばせる。"""
    out = _run_with_team_policy(tmp_path)
    res = replay(out, require_policy_check=False)
    assert res.identical


def test_replay_fails_when_revision_cannot_be_resolved(tmp_path: Path) -> None:
    """manifest の policy_revision が存在しない改訂を指す（例: 999999 に書き換え）と失敗する。"""
    out = _run_with_team_policy(tmp_path)

    def resolve(_team: str, _revision: int) -> PolicyRevision | None:
        return None

    with pytest.raises(ValueError, match="解決できない"):
        replay(out, resolve_revision=resolve)


def test_replay_detects_revision_with_empty_rules_via_policy_hash(tmp_path: Path) -> None:
    """codex 実測: resolver を渡しても、ルールが空の別改訂に差し替えると旧チェック
    （ルール ID の突き合わせ）は全 Finding を「プロジェクト固有」扱いにして見逃していた。
    policy_hash の照合はルール集合の入れ替え（空の改訂も含む）を必ず検出する。"""
    out = _run_with_team_policy(tmp_path)
    original = _team_revision(exceptions_allowed=True)
    swapped = original.model_copy(
        update={"package": original.package.model_copy(update={"rules": []})}
    )

    def resolve(_team: str, _revision: int) -> PolicyRevision | None:
        return swapped

    with pytest.raises(ValueError, match="ハッシュが一致しない"):
        replay(out, resolve_revision=resolve)


def test_replay_succeeds_when_resolved_revision_matches(tmp_path: Path) -> None:
    """正しい改訂を解決できれば policy_hash が一致し、通常の再評価がそのまま進む。"""
    out = tmp_path / "out"
    revision = _team_revision(exceptions_allowed=True)
    write_result(run_check(orders_request(team_policy=revision, run_id="r1")), out)

    def resolve(team: str, rev: int) -> PolicyRevision | None:
        assert (team, rev) == ("acme", 1)
        return revision

    res = replay(out, resolve_revision=resolve)
    assert res.identical


def test_cli_replay_requires_no_policy_check_flag_for_team_policy_runs(tmp_path: Path) -> None:
    """CLI は DB に接続できないため resolver を渡せない。改訂ありの run は既定で失敗し、
    --no-policy-check を付けたときだけ明示的に飛ばして再評価する。"""
    out = _run_with_team_policy(tmp_path)
    cli = CliRunner().invoke(app, ["replay", str(out)])
    assert cli.exit_code == 2 and "resolve_revision" in cli.output

    cli2 = CliRunner().invoke(app, ["replay", str(out), "--no-policy-check"])
    assert cli2.exit_code == 0 and "一致" in cli2.output
