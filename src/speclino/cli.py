"""SpecLino CLI エントリポイント。"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

import typer
from pydantic import ValidationError

from speclino import __version__
from speclino.adapters.localgit import GitError, export_snapshot, resolve_sha
from speclino.analysis.limits import DEFAULT_LIMITS, clamp
from speclino.conformance.engine import EngineInputError
from speclino.domain.code_ir import AnalysisLimits
from speclino.domain.finding import Verdict
from speclino.services import CheckInputError, CheckRequest, run_check, write_result

app = typer.Typer(name="speclino", help="仕様–実装の意味差分レビュー基盤 CLI")
DEFAULT_SCHEMAS_DIR = Path("schemas")

_VERDICTS: tuple[Verdict, ...] = ("VERIFIED_IN_SCOPE", "VIOLATED", "UNKNOWN", "REVIEW")


def _parse_fail_on(values: list[str]) -> tuple[Verdict, ...]:
    invalid = [v for v in values if v not in _VERDICTS]
    if invalid:
        raise typer.BadParameter(f"--fail-on に不正な値: {invalid}（許容値: {list(_VERDICTS)}）")
    # 上で許容値（_VERDICTS）だけであることを確認済み。
    return tuple(values)  # type: ignore[arg-type]


@app.command()
def check(
    repo: str = typer.Option(".", "--repo", help="対象リポジトリのパス"),
    base: str | None = typer.Option(None, "--base", help="比較元の参照（コミット/ブランチ）"),
    head: str = typer.Option(
        "HEAD", "--head", help="比較先の参照（コミット/ブランチ。WORKTREE で作業ツリーを使う）"
    ),
    project: str | None = typer.Option(
        None, "--project", help=".speclino/ と specs/ の在処（既定: --repo 直下）"
    ),
    code_ir: str | None = typer.Option(
        None, "--code-ir", help="手書き Code IR を直接入力する（解析器の代わりに使う）"
    ),
    out: str = typer.Option("build/review", "--out", help="成果物の出力先"),
    fail_on: list[str] | None = typer.Option(  # noqa: B008
        None, "--fail-on", help="ゲート失敗にする verdict（複数可。既定は advisory 運用）"
    ),
    blocking: bool = typer.Option(
        False, "--blocking", help="ゲート失敗を終了コード1にする（既定: advisory）"
    ),
    depth: int | None = typer.Option(None, "--depth", help="呼出し展開の深さ上限"),
    max_paths: int | None = typer.Option(None, "--max-paths", help="経路数の上限"),
    solver_timeout_ms: int | None = typer.Option(
        None, "--solver-timeout-ms", help="ソルバー呼出し1回あたりのタイムアウト(ms)"
    ),
) -> None:
    """仕様とコードの意味差分を検査する（PoC 15章）。

    終了コード: 0=完了しゲート条件に該当しない / 1=ゲート条件に該当（--blocking時）/
    2=入力不正・実行不能。
    """
    fail_on_verdicts = _parse_fail_on(fail_on or [])
    limits = clamp(
        AnalysisLimits(
            depth=depth if depth is not None else DEFAULT_LIMITS.depth,
            max_paths=max_paths if max_paths is not None else DEFAULT_LIMITS.max_paths,
            solver_timeout_ms=(
                solver_timeout_ms
                if solver_timeout_ms is not None
                else DEFAULT_LIMITS.solver_timeout_ms
            ),
        )
    )

    repo_root = Path(repo).resolve()
    project_dir = Path(project).resolve() if project is not None else repo_root

    try:
        head_sha = resolve_sha(repo_root, head)
        base_sha = resolve_sha(repo_root, base) if base is not None else None
    except GitError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2) from e

    with TemporaryDirectory(prefix="speclino-check-") as tmp:
        try:
            analysis_root = export_snapshot(repo_root, head_sha, Path(tmp))
        except GitError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2) from e

        if analysis_root == repo_root:
            analysis_project_dir = project_dir
        else:
            try:
                rel_project = project_dir.relative_to(repo_root)
            except ValueError as e:
                typer.echo(f"--project は --repo の配下にする必要がある: {e}", err=True)
                raise typer.Exit(code=2) from e
            analysis_project_dir = analysis_root / rel_project

        req = CheckRequest(
            repo_root=analysis_root,
            head_sha=head_sha,
            base_sha=base_sha,
            project_dir=analysis_project_dir,
            limits=limits,
            fail_on=fail_on_verdicts,
            mode="blocking" if blocking else "advisory",
            code_ir_override=Path(code_ir).resolve() if code_ir is not None else None,
        )

        try:
            result = run_check(req)
        except (CheckInputError, EngineInputError, ValidationError, ValueError, OSError) as e:
            # 入力不正・実行不能（JSON 破損、スキーマ不一致、ファイル欠落を含む）は終了コード 2
            typer.echo(f"入力不正または実行不能: {e}", err=True)
            raise typer.Exit(code=2) from e

        artifacts = write_result(result, Path(out))

    for report in result.validation:
        for diag in report.warnings:
            typer.echo(f"warning[{diag.code}] {diag.rule_id or '-'}: {diag.message}", err=True)

    counts = result.findings.counts
    for v in _VERDICTS:
        typer.echo(f"{v}: {getattr(counts, v)}")
    typer.echo(f"gate: {result.gate.status} ({result.gate.reason})")
    for name in sorted(artifacts):
        typer.echo(str(artifacts[name]))

    if result.gate.status == "fail" and req.mode == "blocking":
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


@app.command()
def version() -> None:
    """バージョンを表示する。"""
    typer.echo(__version__)


@app.command()
def schemas(
    out: Annotated[Path, typer.Option("--out", help="JSON Schema の出力先")] = DEFAULT_SCHEMAS_DIR,
) -> None:
    """domain/ の pydantic モデルから JSON Schema を生成する。"""
    from speclino.schemas import export_schemas

    for path in export_schemas(out):
        typer.echo(str(path))


def main() -> None:
    app()


if __name__ == "__main__":
    main()


@app.command()
def replay(
    run_dir: Annotated[Path, typer.Argument(help="成果物ディレクトリ（run-manifest.json 等）")],
) -> None:
    """凍結した IR から判定を再評価し、保存された結果と一致するか確認する（AC-013）。"""
    from speclino.services.replay_service import replay as _replay

    try:
        res = _replay(run_dir)
    except (OSError, ValueError) as e:
        typer.echo(f"入力不正: {e}", err=True)
        raise typer.Exit(code=2) from e
    typer.echo(f"run_id: {res.run_id}  behavior: {res.behavior_id}  compared: {res.compared}")
    for d in res.differences:
        typer.echo(f"  差異: {d}")
    typer.echo("一致" if res.identical else "不一致")
    raise typer.Exit(code=0 if res.identical else 1)
