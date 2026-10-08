"""S75: 書き出しの実行スクリプト（`sync_apply.py`）。計画は手で組み立てる（サーバに依存しない）。"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from speclino import sync_apply as sa

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "speclino" / "sync_apply.py"
PLUGIN_COPY = ROOT / "plugins" / "speclino" / "scripts" / "speclino_apply.py"


def _h(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.replace("\r\n", "\n").encode()).hexdigest()


def write(path: str, content: str, expected: str | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {"op": "write", "path": path, "content": content, "sha256": _h(content)}
    if expected is not None:
        op["expected_sha256"] = expected
    return op


def set_block(path: str, inner: str, gen: int = 1, expected: str | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {
        "op": "set_managed_block",
        "path": path,
        "inner": inner,
        "generation": gen,
    }
    if expected is not None:
        op["expected_inner_sha256"] = expected
    return op


def remove_block(path: str, expected: str) -> dict[str, Any]:
    return {"op": "remove_managed_block", "path": path, "expected_inner_sha256": expected}


def delete(path: str, expected: str) -> dict[str, Any]:
    return {"op": "delete", "path": path, "expected_sha256": expected}


def plan(*ops: dict[str, Any], gen: int = 1, repo: str = "acme/app") -> dict[str, Any]:
    files: dict[str, str] = {}
    absent: list[str] = []
    # 区画の世代は計画の世代にそろえる（そろわない計画は前提の確認で止まる）
    ops = tuple({**op, "generation": gen} if "generation" in op else op for op in ops)
    for op in ops:
        if op["op"] == "write":
            files[op["path"]] = op["sha256"]
        elif op["op"] == "set_managed_block":
            files[op["path"]] = _h(op["inner"])
        else:
            absent.append(op["path"])
    lock = {
        "schema_version": "0.1",
        "workspace": "acme",
        "github_repo_id": 42,
        "bundle_generation": gen,
        "members": [],
        "files": files,
        "absent": sorted(absent),
        "issued_at": "2026-10-07T00:00:00Z",
        "expires_at": "2026-11-06T00:00:00Z",
        "signature": "hmac-sha256:x",
    }
    return {
        "schema_version": "0.1",
        "workspace": "acme",
        "repo_full_name": repo,
        "github_repo_id": 42,
        "bundle_generation": gen,
        "ops": list(ops),
        "lock": lock,
        "signature": "hmac-sha256:y",
    }


def tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


SKILL = ".claude/skills/review/SKILL.md"
SPEC = ".speclino/specs/orders.md"
BASE = (write(SKILL, "手順\n"), set_block("AGENTS.md", "共通\n"), write(SPEC, "# orders\n"))


def stopped(result: sa.ApplyResult, word: str) -> None:
    assert result.exit_code == sa.EXIT_PRECONDITION, result
    assert result.reason is not None and word in result.reason, result.reason
    assert not result.written and not result.deleted


# ---------------------------------------------------------------------------
# 書き出し
# ---------------------------------------------------------------------------


def test_apply_writes_files_block_and_lock(tmp_path: Path) -> None:
    p = plan(*BASE)
    result = sa.apply_plan(tmp_path, p)
    assert result.exit_code == 0 and result.ok, result
    assert (tmp_path / SKILL).read_text() == "手順\n"
    assert (tmp_path / SPEC).read_text() == "# orders\n"
    assert (tmp_path / "AGENTS.md").read_text() == (
        "<!-- speclino:begin bundle=1 -->\n共通\n<!-- speclino:end -->\n"
    )
    assert json.loads((tmp_path / sa.LOCK_PATH).read_text()) == p["lock"]
    assert sorted(result.written) == sorted([SKILL, SPEC, "AGENTS.md", sa.LOCK_PATH])
    assert not [x for x in (tmp_path / ".speclino").iterdir() if x.name.startswith(".tmp-")]


def test_apply_is_idempotent(tmp_path: Path) -> None:
    p = plan(*BASE)
    sa.apply_plan(tmp_path, p)
    before = tree(tmp_path)
    again = sa.apply_plan(tmp_path, p)
    assert again.exit_code == 0
    assert again.written == []
    assert sorted(again.unchanged) == sorted([SKILL, SPEC, "AGENTS.md", sa.LOCK_PATH])
    assert tree(tmp_path) == before


def test_managed_block_keeps_outside_text(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# 手書き\n\nメモ\n")
    sa.apply_plan(tmp_path, plan(set_block("AGENTS.md", "共通\n")))
    agents = tmp_path / "AGENTS.md"
    agents.write_text(agents.read_text() + "\n## 後ろ\n")
    result = sa.apply_plan(
        tmp_path, plan(set_block("AGENTS.md", "新\n", gen=2, expected=_h("共通\n")), gen=2)
    )
    assert result.exit_code == 0, result
    assert agents.read_text() == (
        "# 手書き\n\nメモ\n\n<!-- speclino:begin bundle=2 -->\n新\n"
        "<!-- speclino:end -->\n\n## 後ろ\n"
    )


def test_remove_managed_block_keeps_outside_text(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# 手書き\n")
    sa.apply_plan(tmp_path, plan(set_block("AGENTS.md", "共通\n")))
    result = sa.apply_plan(tmp_path, plan(remove_block("AGENTS.md", _h("共通\n")), gen=2))
    assert result.exit_code == 0, result
    assert result.deleted == ["AGENTS.md"]
    assert (tmp_path / "AGENTS.md").read_text() == "# 手書き\n\n"
    lock = json.loads((tmp_path / sa.LOCK_PATH).read_text())
    assert lock["absent"] == ["AGENTS.md"] and lock["files"] == {}
    # もう一度当てても区画は無いまま（変えない）
    assert sa.apply_plan(tmp_path, plan(remove_block("AGENTS.md", _h("共通\n")), gen=2)).ok


def test_crlf_managed_block_and_files(tmp_path: Path) -> None:
    sa.apply_plan(tmp_path, plan(*BASE))
    agents = tmp_path / "AGENTS.md"
    agents.write_bytes(agents.read_bytes().replace(b"\n", b"\r\n"))
    spec = tmp_path / SPEC
    spec.write_bytes(spec.read_bytes().replace(b"\n", b"\r\n"))
    # CRLF の checkout でも同じ内容なら変えない
    same = sa.apply_plan(tmp_path, plan(*BASE))
    assert same.exit_code == 0 and SPEC in same.unchanged and "AGENTS.md" in same.unchanged
    newer = plan(
        write(SKILL, "手順\n"),
        set_block("AGENTS.md", "新\n", gen=2, expected=_h("共通\n")),
        write(SPEC, "# orders\n"),
        gen=2,
    )
    result = sa.apply_plan(tmp_path, newer)
    assert result.exit_code == 0, result
    assert b"<!-- speclino:begin bundle=2 -->\n" in agents.read_bytes()
    assert agents.read_bytes().count(b"speclino:begin") == 1


def test_same_inner_with_new_generation_rewrites_header(tmp_path: Path) -> None:
    sa.apply_plan(tmp_path, plan(set_block("AGENTS.md", "共通\n")))
    result = sa.apply_plan(tmp_path, plan(set_block("AGENTS.md", "共通\n", gen=3), gen=3))
    assert result.exit_code == 0 and result.written == ["AGENTS.md", sa.LOCK_PATH]
    assert "bundle=3" in (tmp_path / "AGENTS.md").read_text()


def test_delete_removes_file_and_empty_skill_dir(tmp_path: Path) -> None:
    old = ".claude/skills/old/SKILL.md"
    sa.apply_plan(tmp_path, plan(write(old, "古い\n"), *BASE))
    (tmp_path / ".agents/skills/old").mkdir(parents=True)
    result = sa.apply_plan(tmp_path, plan(*BASE, delete(old, _h("古い\n")), gen=2))
    assert result.exit_code == 0, result
    assert result.deleted == [old]
    assert not (tmp_path / ".claude/skills/old").exists()
    assert (tmp_path / ".claude/skills").is_dir()  # 置き場の根は残す
    # 消えたものをもう一度消す計画は「変えなかった」
    again = sa.apply_plan(tmp_path, plan(*BASE, delete(old, _h("古い\n")), gen=2))
    assert again.exit_code == 0 and old in again.unchanged


def test_delete_keeps_user_files_in_the_same_dir(tmp_path: Path) -> None:
    old = ".agents/skills/old/SKILL.md"
    sa.apply_plan(tmp_path, plan(write(old, "古い\n")))
    (tmp_path / ".agents/skills/old/notes.md").write_text("user")
    assert sa.apply_plan(tmp_path, plan(delete(old, _h("古い\n")), gen=2)).ok
    assert (tmp_path / ".agents/skills/old/notes.md").read_text() == "user"
    assert not (tmp_path / old).exists()


def test_write_overwrites_when_previous_hash_matches(tmp_path: Path) -> None:
    sa.apply_plan(tmp_path, plan(write(SPEC, "v1\n")))
    result = sa.apply_plan(tmp_path, plan(write(SPEC, "v2\n", expected=_h("v1\n")), gen=2))
    assert result.exit_code == 0 and (tmp_path / SPEC).read_text() == "v2\n"


# ---------------------------------------------------------------------------
# 前提（どれも何も書かずに終了コード 2）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "../outside.md",
        "/etc/passwd",
        ".claude/skills/x/../../../escape",
        "src/main.py",
        ".git/hooks/pre-commit",
        "C:/x",
        ".claude/skills/x\\y",
        ".claude/skills/Bad/SKILL.md",
        ".speclino/lock.json",
        "",
    ],
)
def test_rejects_paths_outside_allowlist(tmp_path: Path, bad: str) -> None:
    result = sa.apply_plan(tmp_path, plan(*BASE, write(bad, "x")))
    stopped(result, "パス")
    assert tree(tmp_path) == {}


def test_rejects_op_kind_for_path(tmp_path: Path) -> None:
    stopped(sa.apply_plan(tmp_path, plan(write("AGENTS.md", "x"))), "AGENTS.md")
    stopped(sa.apply_plan(tmp_path, plan(delete("CLAUDE.md", _h("x")))), "CLAUDE.md")
    stopped(sa.apply_plan(tmp_path, plan(set_block(SPEC, "x"))), SPEC)


def test_rejects_symlink_in_the_middle(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".claude").symlink_to(outside, target_is_directory=True)
    stopped(sa.apply_plan(repo, plan(*BASE)), "symlink")
    assert list(outside.iterdir()) == []
    assert not (repo / "AGENTS.md").exists() and not (repo / sa.LOCK_PATH).exists()


def test_rejects_symlink_file_and_lock(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("keep")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").symlink_to(secret)
    stopped(sa.apply_plan(repo, plan(*BASE)), "symlink")
    assert secret.read_text() == "keep"
    (repo / "AGENTS.md").unlink()
    (repo / ".speclino").mkdir()
    (repo / sa.LOCK_PATH).symlink_to(secret)
    stopped(sa.apply_plan(repo, plan(*BASE)), "symlink")
    assert secret.read_text() == "keep"


def test_rejects_directory_where_file_expected(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").mkdir()
    stopped(sa.apply_plan(tmp_path, plan(*BASE)), "ディレクトリ")
    (tmp_path / "AGENTS.md").rmdir()
    (tmp_path / ".claude").write_text("file")
    stopped(sa.apply_plan(tmp_path, plan(*BASE)), "ファイル")


def test_rejects_write_conflicting_with_existing_file(tmp_path: Path) -> None:
    (tmp_path / ".speclino/specs").mkdir(parents=True)
    (tmp_path / SPEC).write_text("利用者の仕様\n")
    stopped(sa.apply_plan(tmp_path, plan(*BASE)), SPEC)
    assert (tmp_path / SPEC).read_text() == "利用者の仕様\n"
    # 前回の記録と違う（手で直した）ファイルも上書きしない
    stopped(sa.apply_plan(tmp_path, plan(write(SPEC, "v2\n", expected=_h("v1\n")))), SPEC)


def test_rejects_delete_of_edited_file(tmp_path: Path) -> None:
    old = ".claude/skills/old/SKILL.md"
    sa.apply_plan(tmp_path, plan(write(old, "古い\n")))
    (tmp_path / old).write_text("手で直した")
    stopped(sa.apply_plan(tmp_path, plan(delete(old, _h("古い\n")), gen=2)), old)
    assert (tmp_path / old).read_text() == "手で直した"


def test_rejects_edited_managed_block(tmp_path: Path) -> None:
    sa.apply_plan(tmp_path, plan(set_block("AGENTS.md", "共通\n")))
    agents = tmp_path / "AGENTS.md"
    agents.write_text(agents.read_text().replace("共通", "勝手な指示"))
    before = agents.read_text()
    stopped(
        sa.apply_plan(tmp_path, plan(remove_block("AGENTS.md", _h("共通\n")), gen=2)), "AGENTS.md"
    )
    stopped(
        sa.apply_plan(tmp_path, plan(set_block("AGENTS.md", "新\n", 2, _h("共通\n")), gen=2)),
        "AGENTS.md",
    )
    # 前回の記録が無い（検証済みの lock が無い）ときに、違う中身の区画があれば止まる
    stopped(sa.apply_plan(tmp_path, plan(set_block("AGENTS.md", "新\n", 2), gen=2)), "AGENTS.md")
    assert agents.read_text() == before


@pytest.mark.parametrize(
    "agents",
    [
        "<!-- speclino:begin bundle=1 -->\nA\n<!-- speclino:end -->\n<!-- speclino:end -->\n",
        "<!-- speclino:begin bundle=1 -->\nA\n",
        "<!-- speclino:end -->\n<!-- speclino:begin bundle=1 -->\n",
        "<!--speclino:begin bundle=x-->\nA\n<!-- speclino:end -->\n",
        "<!-- speclino:begin bundle=1 -->\nA\n<!-- speclino:end -->\n"
        "<!-- speclino:begin bundle=1 -->\nB\n<!-- speclino:end -->\n",
        "doc:\n```\n<!-- speclino:end -->\n```\n",
    ],
)
def test_rejects_broken_markers(tmp_path: Path, agents: str) -> None:
    (tmp_path / "AGENTS.md").write_text(agents)
    stopped(sa.apply_plan(tmp_path, plan(*BASE)), "管理区画")
    assert (tmp_path / "AGENTS.md").read_text() == agents
    assert not (tmp_path / ".claude").exists()


def test_rejects_marker_in_inner(tmp_path: Path) -> None:
    stopped(
        sa.apply_plan(tmp_path, plan(set_block("AGENTS.md", "x\n<!-- speclino:end -->\n"))),
        "管理区画",
    )


def test_rejects_hash_mismatch_and_lock_disagreement(tmp_path: Path) -> None:
    bad = write(SPEC, "a")
    bad["sha256"] = _h("b")
    stopped(sa.apply_plan(tmp_path, plan(bad)), "ハッシュ")
    p = plan(*BASE)
    p["lock"]["files"][SPEC] = _h("別")
    stopped(sa.apply_plan(tmp_path, p), "lock")
    p = plan(*BASE)
    p["lock"]["absent"] = [".claude/skills/x/SKILL.md"]
    stopped(sa.apply_plan(tmp_path, p), "lock")
    p = plan(*BASE)
    p["lock"]["github_repo_id"] = 7
    stopped(sa.apply_plan(tmp_path, p), "lock")
    assert tree(tmp_path) == {}


def test_rejects_malformed_plan(tmp_path: Path) -> None:
    for broken in ({}, {"ops": "x"}, {**plan(*BASE), "ops": [{"op": "chmod", "path": SPEC}]}):
        stopped(sa.apply_plan(tmp_path, broken), "計画")
    dup = plan(write(SPEC, "a"), write(SPEC, "a"))
    stopped(sa.apply_plan(tmp_path, dup), "重複")
    case = plan(write(".claude/skills/x/A.md", "a"), write(".claude/skills/x/a.md", "a"))
    stopped(sa.apply_plan(tmp_path, case), "重複")


@pytest.mark.parametrize(
    ("limit", "value"),
    [("MAX_FILES", 2), ("MAX_FILE_BYTES", 4), ("MAX_MANAGED_BYTES", 4), ("MAX_TOTAL_BYTES", 20)],
)
def test_rejects_over_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str, value: int
) -> None:
    monkeypatch.setattr(sa, limit, value)
    stopped(sa.apply_plan(tmp_path, plan(*BASE)), "大きすぎる" if "BYTES" in limit else "多すぎる")
    assert tree(tmp_path) == {}


def test_rejects_file_and_dir_mixup_between_ops(tmp_path: Path) -> None:
    p = plan(write(".claude/skills/x/ref", "a"), write(".claude/skills/x/ref/b.md", "b"))
    stopped(sa.apply_plan(tmp_path, p), "ref")


# ---------------------------------------------------------------------------
# remote との照合
# ---------------------------------------------------------------------------


def _git_config(root: Path, url: str) -> None:
    (root / ".git").mkdir()
    (root / ".git/config").write_text(f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {url}\n')


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:acme/app.git",
        "https://github.com/Acme/App",
        "ssh://git@github.com/acme/app.git",
    ],
)
def test_remote_matching_repo_is_accepted(tmp_path: Path, url: str) -> None:
    _git_config(tmp_path, url)
    assert sa.apply_plan(tmp_path, plan(*BASE)).ok


def test_remote_of_other_repo_is_rejected(tmp_path: Path) -> None:
    _git_config(tmp_path, "git@github.com:acme/other.git")
    stopped(sa.apply_plan(tmp_path, plan(*BASE)), "acme/other")


def test_remote_not_derivable_is_skipped(tmp_path: Path) -> None:
    _git_config(tmp_path, "/srv/git/app.git")
    assert sa.apply_plan(tmp_path, plan(*BASE)).ok


def test_remote_in_worktree_gitdir(tmp_path: Path) -> None:
    common = tmp_path / "main/.git"
    (common / "worktrees/wt").mkdir(parents=True)
    (common / "config").write_text('[remote "origin"]\n\turl = git@github.com:acme/other.git\n')
    (common / "worktrees/wt/commondir").write_text("../..\n")
    repo = tmp_path / "wt"
    repo.mkdir()
    (repo / ".git").write_text(f"gitdir: {common / 'worktrees/wt'}\n")
    stopped(sa.apply_plan(repo, plan(*BASE)), "acme/other")


# ---------------------------------------------------------------------------
# 失敗と巻き戻し（終了コード 1）
# ---------------------------------------------------------------------------


def _second_plan() -> dict[str, Any]:
    return plan(
        write(SKILL, "改訂\n", expected=_h("手順\n")),
        set_block("AGENTS.md", "新\n", gen=2, expected=_h("共通\n")),
        write(".speclino/specs/billing.md", "# billing\n"),
        delete(SPEC, _h("# orders\n")),
        gen=2,
    )


def test_staging_failure_keeps_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sa.apply_plan(tmp_path, plan(*BASE))
    (tmp_path / "AGENTS.md").write_text("# 手書き\n" + (tmp_path / "AGENTS.md").read_text())
    before = tree(tmp_path)
    real = Path.write_bytes
    calls = {"n": 0}

    def flaky(self: Path, data: bytes) -> int:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky)
    result = sa.apply_plan(tmp_path, _second_plan())
    monkeypatch.undo()
    assert result.exit_code == sa.EXIT_FAILED and "disk full" in (result.reason or "")
    assert tree(tmp_path) == before


@pytest.mark.parametrize("fail_at", [1, 3, 5, 6])
def test_commit_failure_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_at: int
) -> None:
    sa.apply_plan(tmp_path, plan(*BASE))
    before = tree(tmp_path)
    real = os.replace
    calls = {"n": 0}

    def flaky(src: Any, dst: Any) -> None:
        calls["n"] += 1
        if calls["n"] == fail_at:
            raise OSError("boom")
        real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    result = sa.apply_plan(tmp_path, _second_plan())
    monkeypatch.undo()
    assert result.exit_code == sa.EXIT_FAILED and "boom" in (result.reason or "")
    assert tree(tmp_path) == before
    assert not (tmp_path / ".speclino/specs/billing.md").exists()
    assert not [x for x in (tmp_path / ".speclino").iterdir() if x.name.startswith(".tmp-")]


def test_rollback_reports_files_it_could_not_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sa.apply_plan(tmp_path, plan(*BASE))
    real = os.replace
    calls = {"n": 0, "restore": 0}

    def flaky(src: Any, dst: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 6:
            raise OSError("boom")
        if calls["n"] > 6:
            calls["restore"] += 1
            if calls["restore"] == 1:
                raise OSError("restore failed")
        real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    result = sa.apply_plan(tmp_path, _second_plan())
    monkeypatch.undo()
    assert result.exit_code == sa.EXIT_FAILED
    assert calls["restore"] >= 2  # 1 つ目の復元に失敗しても残りを続けた
    assert "元に戻せなかった" in (result.reason or "")


def test_symlink_swapped_after_staging_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    real_stage = sa._stage

    def swap_then_stage(*args: Any, **kwargs: Any) -> Any:
        staged = real_stage(*args, **kwargs)
        (repo / ".claude").symlink_to(outside, target_is_directory=True)
        return staged

    monkeypatch.setattr(sa, "_stage", swap_then_stage)
    stopped(sa.apply_plan(repo, plan(*BASE)), "symlink")
    assert list(outside.iterdir()) == []
    assert not (repo / sa.LOCK_PATH).exists()


# ---------------------------------------------------------------------------
# CLI と配布
# ---------------------------------------------------------------------------


def test_main_reads_file_and_prints_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan(*BASE)))
    repo = tmp_path / "repo"
    repo.mkdir()
    assert sa.main([str(plan_file), "--repo", str(repo)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["exit_code"] == 0 and out["bundle_generation"] == 1
    assert SKILL in out["written"]
    (repo / ".speclino/specs/orders.md").write_text("手で直した")
    assert sa.main([str(plan_file), "--repo", str(repo)]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and SPEC in out["reason"]


def test_main_reads_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(plan(*BASE)).encode()))
    )
    assert sa.main(["-", "--repo", str(tmp_path)]) == 0
    assert (tmp_path / SKILL).exists()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"{not json")))
    assert sa.main(["-", "--repo", str(tmp_path)]) == 2
    assert "JSON" in json.loads(capsys.readouterr().out.splitlines()[-1])["reason"]


def test_script_runs_standalone_without_speclino(tmp_path: Path) -> None:
    """プラグインの写しは speclino を入れていない環境でも動く（`-I` で site-packages を見ない）。"""
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan(*BASE)))
    repo = tmp_path / "repo"
    repo.mkdir()
    done = subprocess.run(
        [sys.executable, "-I", "-S", str(PLUGIN_COPY), str(plan_file), "--repo", str(repo)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["ok"] is True
    assert (repo / SKILL).read_text() == "手順\n"


def test_plugin_copy_is_identical() -> None:
    assert PLUGIN_COPY.read_bytes() == SOURCE.read_bytes()


def test_source_uses_only_the_standard_library() -> None:
    tree_ = ast.parse(SOURCE.read_text())
    names: set[str] = set()
    for node in ast.walk(tree_):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "相対 import は使わない"
            names.add((node.module or "").split(".")[0])
    assert names <= set(sys.stdlib_module_names) | {"__future__"}, names


def test_constants_match_the_server() -> None:
    from speclino.domain.knowledge import BODY_MAX_BYTES, MANAGED_BEGIN, MANAGED_END
    from speclino.services import knowledge_sync as ks
    from speclino.spec.loader import content_hash

    assert sa.MAX_FILE_BYTES == BODY_MAX_BYTES
    assert (sa.MANAGED_BEGIN, sa.MANAGED_END) == (MANAGED_BEGIN, MANAGED_END)
    assert tuple(ks.MANAGED_FILES) == sa.MANAGED_FILES
    assert sa.file_hash("a\r\nb") == content_hash("a\nb") == ks.file_hash("a\nb")


# ---------------------------------------------------------------------------
# fix round 1
# ---------------------------------------------------------------------------


def test_incomplete_rollback_keeps_backup_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """置き換えの 3 回目と復元の 1 回目を失敗させる: 退避を消さず、その場所を理由に書く。"""
    sa.apply_plan(tmp_path, plan(*BASE))
    real = os.replace
    calls = {"n": 0, "restore": 0}

    def flaky(src: Any, dst: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("boom")
        if calls["n"] > 3:
            calls["restore"] += 1
            if calls["restore"] == 1:
                raise OSError("restore failed")
        real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    result = sa.apply_plan(tmp_path, _second_plan())
    monkeypatch.undo()
    assert result.exit_code == sa.EXIT_FAILED
    kept = [x for x in (tmp_path / ".speclino").iterdir() if x.name.startswith(".tmp-")]
    assert len(kept) == 1 and str(kept[0]) in (result.reason or "")
    assert "元に戻せなかった" in (result.reason or "")
    # 退避した前の中身（SKILL の手順）が残っている
    assert any(p.read_bytes() == "手順\n".encode() for p in kept[0].iterdir())


def test_prune_failure_is_only_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = ".claude/skills/old/SKILL.md"
    sa.apply_plan(tmp_path, plan(write(old, "古い\n")))

    def deny(self: Path) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "rmdir", deny)
    result = sa.apply_plan(tmp_path, plan(delete(old, _h("古い\n")), gen=2))
    monkeypatch.undo()
    assert result.exit_code == 0 and result.deleted == [old]
    assert sa.LOCK_PATH in result.written
    assert result.warnings and "空ディレクトリ" in result.warnings[0]
    assert not (tmp_path / old).exists()


def test_rejects_block_generation_different_from_plan(tmp_path: Path) -> None:
    p = plan(*BASE)
    p["ops"][1] = {**p["ops"][1], "generation": 7}
    stopped(sa.apply_plan(tmp_path, p), "世代")


def test_file_changed_after_preflight_rolls_back_with_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sa.apply_plan(tmp_path, plan(*BASE))
    real_stage = sa._stage

    def edit_then_stage(*args: Any, **kwargs: Any) -> Any:
        staged = real_stage(*args, **kwargs)
        (tmp_path / SPEC).unlink()
        (tmp_path / SPEC).write_text("途中で直した\n")
        return staged

    monkeypatch.setattr(sa, "_stage", edit_then_stage)
    p = _second_plan()
    p["ops"][3] = delete(SPEC, _h("# orders\n"))  # SKILL・区画・billing を先に置き換えてから
    result = sa.apply_plan(tmp_path, p)
    monkeypatch.undo()
    stopped(result, "途中で変更")
    assert (tmp_path / SPEC).read_text() == "途中で直した\n"
    assert (tmp_path / SKILL).read_text() == "手順\n"  # 置き換え済みのものも戻した
    assert not (tmp_path / ".speclino/specs/billing.md").exists()


class _PlanServer:
    """計画を 1 回だけ返す HTTP サーバ（URL 方式の実行スクリプトの試験）。"""

    def __init__(self, body: bytes, status: int = 200, location: str | None = None) -> None:
        import http.server
        import threading

        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(outer.status)
                if outer.location:
                    self.send_header("Location", outer.location)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *args: Any) -> None:
                pass

        self.body, self.status, self.location = body, status, location
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/sync/plans/slplan_x"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def plan_server() -> Any:
    servers: list[_PlanServer] = []

    def start(body: bytes, status: int = 200, location: str | None = None) -> _PlanServer:
        s = _PlanServer(body, status, location)
        servers.append(s)
        return s

    yield start
    for s in servers:
        s.close()


def _main_json(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    code = sa.main(list(argv))
    return code, json.loads(capsys.readouterr().out.splitlines()[-1])


def test_main_fetches_plan_url(
    tmp_path: Path, plan_server: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    srv = plan_server(json.dumps(plan(*BASE)).encode())
    code, out = _main_json(capsys, "--plan-url", srv.url, "--repo", str(tmp_path))
    assert code == 2 and "https" in out["reason"]  # http は既定で拒否
    assert not (tmp_path / SKILL).exists()
    code, out = _main_json(
        capsys,
        "--plan-url",
        srv.url,
        "--allow-http",
        "--repo",
        str(tmp_path),
        "--expect-generation",
        "2",
    )
    assert code == 2 and "世代" in out["reason"]
    code, out = _main_json(
        capsys,
        "--plan-url",
        srv.url,
        "--allow-http",
        "--repo",
        str(tmp_path),
        "--expect-generation",
        "1",
    )
    assert code == 0 and out["ok"] is True
    assert (tmp_path / SKILL).read_text() == "手順\n"


def test_allow_http_is_only_for_localhost(tmp_path: Path) -> None:
    with pytest.raises(sa.PreconditionFailed, match="https"):
        sa.fetch_plan("http://speclino.example.com/api/sync/plans/x", allow_http=True)
    with pytest.raises(sa.PreconditionFailed, match="https"):
        sa.fetch_plan("file:///etc/passwd", allow_http=True)


@pytest.mark.parametrize("case", ["404", "redirect", "large", "garbage"])
def test_plan_url_failures_write_nothing(
    tmp_path: Path,
    plan_server: Any,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    body = json.dumps(plan(*BASE)).encode()
    if case == "404":
        srv = plan_server(b"{}", status=404)
    elif case == "redirect":
        srv = plan_server(b"", status=302, location="http://127.0.0.1:1/evil")
    elif case == "large":
        monkeypatch.setattr(sa, "MAX_PLAN_BYTES", 100)
        srv = plan_server(body)
    else:
        srv = plan_server(b"<html>")
    code, out = _main_json(capsys, "--plan-url", srv.url, "--allow-http", "--repo", str(tmp_path))
    assert code == 2 and out["ok"] is False, out
    assert tree(tmp_path) == {}


# ---------------------------------------------------------------------------
# git に無視されるパスの警告
# ---------------------------------------------------------------------------


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)


@pytest.mark.skipif(not shutil.which("git"), reason="git が無い")
def test_warns_when_written_files_are_ignored_by_git(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text(".claude/\n")
    result = sa.apply_plan(tmp_path, plan(*BASE))
    assert result.ok, result
    assert len(result.warnings) == 1 and SKILL in result.warnings[0], result.warnings
    assert ".gitignore" in result.warnings[0]


@pytest.mark.skipif(not shutil.which("git"), reason="git が無い")
def test_warns_about_ignored_files_even_when_nothing_changes(tmp_path: Path) -> None:
    _git_init(tmp_path)
    p = plan(*BASE)
    assert sa.apply_plan(tmp_path, p).warnings == []
    (tmp_path / ".gitignore").write_text(".speclino/\n")
    again = sa.apply_plan(tmp_path, p)
    assert again.ok and again.written == []
    assert any(sa.LOCK_PATH in w for w in again.warnings), again.warnings


def test_no_warning_outside_a_git_repository(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".claude/\n")
    assert sa.apply_plan(tmp_path, plan(*BASE)).warnings == []
