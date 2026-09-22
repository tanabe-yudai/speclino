from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from speclino.cli import app
from tests.fixtures import cancel_order as co

REPO = Path(__file__).resolve().parents[2]
runner = CliRunner()


def test_check_examples_orders_worktree_exit_0(tmp_path: Path) -> None:
    res = runner.invoke(
        app,
        [
            "check",
            "--repo",
            str(REPO),
            "--head",
            "WORKTREE",
            "--project",
            str(REPO / "examples" / "orders"),
            "--out",
            str(tmp_path),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "VERIFIED_IN_SCOPE: 9" in res.output and "gate: pass" in res.output
    assert (tmp_path / "review.md").exists()


def test_check_code_ir_violation_blocking_exit_1(tmp_path: Path) -> None:
    ir = tmp_path / "ir.json"
    ir.write_text(co.RESTORE_ADMIN_ONLY.model_dump_json())
    res = runner.invoke(
        app,
        [
            "check",
            "--repo",
            str(REPO),
            "--head",
            "WORKTREE",
            "--project",
            str(REPO / "examples" / "orders"),
            "--code-ir",
            str(ir),
            "--out",
            str(tmp_path / "out"),
            "--fail-on",
            "VIOLATED",
            "--blocking",
        ],
    )
    assert res.exit_code == 1, res.output
    assert "VIOLATED: 1" in res.output and "gate: fail" in res.output


def test_check_advisory_mode_exit_0_even_when_violated(tmp_path: Path) -> None:
    ir = tmp_path / "ir.json"
    ir.write_text(co.RESTORE_ADMIN_ONLY.model_dump_json())
    res = runner.invoke(
        app,
        [
            "check",
            "--repo",
            str(REPO),
            "--head",
            "WORKTREE",
            "--project",
            str(REPO / "examples" / "orders"),
            "--code-ir",
            str(ir),
            "--out",
            str(tmp_path / "out"),
        ],
    )
    assert res.exit_code == 0, res.output


def test_check_broken_input_exit_2(tmp_path: Path) -> None:
    ir = tmp_path / "ir.json"
    ir.write_text("{not json")
    res = runner.invoke(
        app,
        [
            "check",
            "--repo",
            str(REPO),
            "--head",
            "WORKTREE",
            "--project",
            str(REPO / "examples" / "orders"),
            "--code-ir",
            str(ir),
            "--out",
            str(tmp_path / "out"),
        ],
    )
    assert res.exit_code == 2, res.output


def test_check_head_commit_uses_git_archive(tmp_path: Path) -> None:
    # HEAD コミットの内容で自己検証（作業ツリーではなくコミット）。specs/ が HEAD に無ければ exit 2
    res = runner.invoke(
        app, ["check", "--repo", str(REPO), "--head", "HEAD", "--out", str(tmp_path)]
    )
    assert res.exit_code in (0, 2), res.output
