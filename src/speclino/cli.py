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
policy_app = typer.Typer(name="policy", help="チーム規約（改訂・承認・監査）の管理（S42）")
app.add_typer(policy_app, name="policy")
DEFAULT_SCHEMAS_DIR = Path("schemas")
DEFAULT_POLICY_SEED_SOURCE = Path(".speclino/policy/speclino-core.yaml")

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

    try:
        rel_project = project_dir.relative_to(repo_root)
    except ValueError as e:
        typer.echo(f"--project は --repo の配下にする必要がある: {e}", err=True)
        raise typer.Exit(code=2) from e

    # head と base は別ディレクトリに展開する（同じ木に入れると解析器が互いを走査してしまう）
    with (
        TemporaryDirectory(prefix="speclino-check-") as tmp,
        TemporaryDirectory(prefix="speclino-base-") as tmp_base,
    ):
        try:
            analysis_root = export_snapshot(repo_root, head_sha, Path(tmp))
            base_root = (
                export_snapshot(repo_root, base_sha, Path(tmp_base))
                if base_sha is not None
                else None
            )
        except GitError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=2) from e

        req = CheckRequest(
            repo_root=analysis_root,
            head_sha=head_sha,
            base_sha=base_sha,
            project_dir=analysis_root / rel_project,
            limits=limits,
            fail_on=fail_on_verdicts,
            mode="blocking" if blocking else "advisory",
            code_ir_override=Path(code_ir).resolve() if code_ir is not None else None,
            base_root=base_root,
            base_project_dir=base_root / rel_project if base_root is not None else None,
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


@app.command("spec-extract")
def spec_extract(
    spec: Annotated[Path, typer.Option("--spec", help="仕様 Markdown")],
    behavior: Annotated[str, typer.Option("--behavior", help="bindings.behaviors のキー")],
    project: Annotated[Path, typer.Option("--project", help=".speclino/ の在処")] = Path("."),
    out: Annotated[
        Path | None, typer.Option("--out", help="候補 Spec IR の出力先（既定: 標準出力）")
    ] = None,
    model: Annotated[str, typer.Option("--model", help="Gemini モデル")] = "gemini-3.8-flash",
) -> None:
    """Markdown から Spec IR の候補を Gemini で抽出する（承認はしない。曖昧箇所は診断に残す）。

    終了コード: 0=候補が検証を通った（警告は表示）/ 2=候補に誤り（語彙外の参照など）または
    入力不正。候補は approval_ref 無しで出力され、承認は `/speclino approve-spec` で行う。
    """
    import asyncio
    import json

    from speclino.adapters.llm.gemini_json import GeminiJsonLlm
    from speclino.spec import load_bindings, load_domain
    from speclino.spec.spec_extractor import extract_spec

    try:
        domain = load_domain(project / ".speclino" / "domain.yaml")
        bindings = load_bindings(project / ".speclino" / "bindings.yaml")
        markdown = spec.read_text(encoding="utf-8")
    except (OSError, ValidationError, ValueError) as e:
        typer.echo(f"入力不正: {e}", err=True)
        raise typer.Exit(code=2) from e
    if behavior not in bindings.behaviors:
        typer.echo(f"bindings.behaviors に無い behavior: {behavior}", err=True)
        raise typer.Exit(code=2)
    try:
        rel = str(spec.resolve().relative_to(project.resolve()))
    except ValueError:
        rel = str(spec)
    try:
        result = asyncio.run(
            extract_spec(markdown, behavior, domain, bindings, GeminiJsonLlm(model), spec_path=rel)
        )
    except Exception as e:  # noqa: BLE001 - LLM 呼出しの失敗は入力不正と同じ扱い（終了コード 2）
        typer.echo(
            f"Gemini の呼出しに失敗: {type(e).__name__}: {e}\n"
            "GOOGLE_API_KEY を設定するか、GOOGLE_GENAI_USE_VERTEXAI=true と ADC を用意する",
            err=True,
        )
        raise typer.Exit(code=2) from e
    payload = json.dumps(result.spec.model_dump(mode="json"), ensure_ascii=False, indent=2)
    if out is not None:
        out.write_text(payload + "\n", encoding="utf-8")
        typer.echo(str(out))
    else:
        typer.echo(payload)
    for d in [*result.errors, *result.warnings]:
        typer.echo(
            f"{d.severity}[{d.code}] {d.rule_id or '-'} {d.path or ''}: {d.message}", err=True
        )
    typer.echo(
        f"候補 {len(result.spec.rules)} 件 / 未抽出 {len(result.spec.unextracted)} 件 / "
        f"誤り {len(result.errors)} 件 / 警告 {len(result.warnings)} 件",
        err=True,
    )
    raise typer.Exit(code=2 if result.errors else 0)


@policy_app.command("seed")
def policy_seed(
    team: Annotated[str, typer.Option("--team", help="チーム識別子（当面は GitHub の owner）")],
    source: Annotated[
        Path, typer.Option("--from", help="取り込む規約パッケージ YAML")
    ] = DEFAULT_POLICY_SEED_SOURCE,
) -> None:
    """既存の規約パッケージ（`.speclino/policy/*.yaml`）を team の最初の改訂として取り込む。

    author / approver は両方 "seed"（人の承認ではなく既存ファイルの移行のため）。
    下書き作成→承認と同じ経路を通すので、audit_events に policy.draft / policy.approve
    の両方が残る。DB 接続は `SPECLINO_DATABASE_URL`（`Settings.database_url`）を使う。
    """
    import asyncio

    import yaml

    from speclino.db.session import make_engine, make_session_factory
    from speclino.domain.policy import PolicyPackage, PolicyRevision
    from speclino.services.policy_governance import seed_from_package
    from speclino.settings import Settings

    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        package = PolicyPackage.model_validate(raw)
    except (OSError, ValidationError, ValueError) as e:
        typer.echo(f"入力不正: {e}", err=True)
        raise typer.Exit(code=2) from e

    async def _run() -> PolicyRevision:
        settings = Settings()
        engine = make_engine(settings)
        try:
            session_factory = make_session_factory(engine)
            async with session_factory() as session, session.begin():
                return await seed_from_package(session, team=team, package=package)
        finally:
            await engine.dispose()

    try:
        revision = asyncio.run(_run())
    except ValueError as e:
        # 既に改訂があるチームへの seed 拒否など（seed_from_package が投げる）。
        typer.echo(f"seed を拒否した: {e}", err=True)
        raise typer.Exit(code=2) from e
    typer.echo(
        f"{team}@{revision.revision} を {revision.status} として取り込んだ"
        f"（rules={len(revision.package.rules)}）"
    )
