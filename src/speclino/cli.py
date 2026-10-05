"""SpecLino CLI エントリポイント。"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Annotated, get_args

import httpx
import typer
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.helpers import get_artifact_text, get_message_text, new_data_part, new_text_part
from a2a.types import Message, Role, SendMessageRequest, Task, TaskState
from google.protobuf.json_format import MessageToDict
from pydantic import ValidationError

from speclino import __version__, cli_sync
from speclino.adapters.localgit import GitError, export_snapshot, resolve_sha
from speclino.analysis.limits import DEFAULT_LIMITS, clamp
from speclino.conformance.engine import EngineInputError
from speclino.domain.code_ir import AnalysisLimits
from speclino.domain.finding import Verdict
from speclino.domain.workspace import WorkspaceRole
from speclino.services import CheckInputError, CheckRequest, run_check, write_result

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

app = typer.Typer(name="speclino", help="仕様–実装の意味差分レビュー基盤 CLI")
policy_app = typer.Typer(name="policy", help="チーム規約（改訂・承認・監査）の管理（S42）")
app.add_typer(policy_app, name="policy")
workspace_app = typer.Typer(name="workspace", help="ワークスペース（テナント境界）の管理（S53）")
app.add_typer(workspace_app, name="workspace")
app.command("sync")(cli_sync.sync)
DEFAULT_SCHEMAS_DIR = Path("schemas")
DEFAULT_POLICY_SEED_SOURCE = Path(".speclino/policy/speclino-core.yaml")

_VERDICTS: tuple[Verdict, ...] = ("VERIFIED_IN_SCOPE", "VIOLATED", "UNKNOWN", "REVIEW")
_WORKSPACE_ROLES: tuple[WorkspaceRole, ...] = get_args(WorkspaceRole)


def _in_transaction[T](work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """`SPECLINO_DATABASE_URL` の DB に 1 トランザクションで work を実行する（DB を使う CLI 用）。

    `ValueError` は拒否として終了コード 2 とメッセージにする。
    """

    from speclino.db.session import make_engine, make_session_factory
    from speclino.settings import Settings

    async def _run() -> T:
        engine = make_engine(Settings())
        try:
            async with make_session_factory(engine)() as session, session.begin():
                return await work(session)
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_run())
    except ValueError as e:
        typer.echo(f"拒否した: {e}", err=True)
        raise typer.Exit(code=2) from e


def _parse_role(value: str) -> WorkspaceRole:
    if value not in _WORKSPACE_ROLES:
        raise typer.BadParameter(f"--role に不正な値: {value}（許容値: {list(_WORKSPACE_ROLES)}）")
    # 上で許容値（_WORKSPACE_ROLES）だけであることを確認済み。
    return value


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


_A2A_STATE_NAMES = {
    "TASK_STATE_SUBMITTED": "SUBMITTED",
    "TASK_STATE_WORKING": "WORKING",
    "TASK_STATE_INPUT_REQUIRED": "INPUT_REQUIRED",
    "TASK_STATE_COMPLETED": "COMPLETED",
    "TASK_STATE_FAILED": "FAILED",
    "TASK_STATE_CANCELED": "CANCELED",
    "TASK_STATE_REJECTED": "REJECTED",
    "TASK_STATE_AUTH_REQUIRED": "AUTH_REQUIRED",
}


def _client(
    url: str, token: str, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """A2A サーバへの HTTP クライアント（Bearer 付き）。transport はテストで差し替える。"""
    return httpx.AsyncClient(
        base_url=url.rstrip("/"),
        headers={"Authorization": f"Bearer {token}"},
        transport=transport,
        timeout=360.0,  # 検査待ち（サーバ側で最大 180 秒）＋ 議論役（最大 120 秒）を待てるように
    )


def _a2a_message(
    text: str, skill: str | None, fields: dict[str, str | None], task_id: str | None
) -> Message:
    """送るメッセージ。skill があれば DataPart で指定し、返信（task_id あり）は本文だけ。"""
    parts = []
    if skill is not None and task_id is None:
        meta = {"skill": skill, **{k: v for k, v in fields.items() if v is not None}}
        parts.append(new_data_part(meta))
    if text:
        parts.append(new_text_part(text))
    return Message(role=Role.ROLE_USER, message_id=uuid.uuid4().hex, parts=parts)


def _a2a_render(task: Task) -> list[str]:
    """タスクの状態・最後のエージェントの発言・成果物を表示用の行にする。"""
    state = TaskState.Name(task.status.state)
    lines = [
        f"state: {_A2A_STATE_NAMES.get(state, state)}",
        f"task_id: {task.id}",
        f"context_id: {task.context_id}",
    ]
    if task.status.HasField("message"):
        lines.append(f"message: {get_message_text(task.status.message)}")
    for artifact in task.artifacts:
        lines.append(f"artifact: {artifact.name}")
        text = get_artifact_text(artifact)
        if text:
            lines.append(text)
        lines.extend(
            json.dumps(d, ensure_ascii=False, indent=2)
            for part in artifact.parts
            if part.HasField("data")
            for d in [MessageToDict(part.data)]
        )
    return lines


async def _a2a_send(http: httpx.AsyncClient, message: Message) -> Task:
    card = await A2ACardResolver(http, str(http.base_url)).get_agent_card()
    client = ClientFactory(ClientConfig(streaming=False, httpx_client=http)).create(card)
    last = None
    async for event in client.send_message(SendMessageRequest(message=message)):
        last = event
    if last is None or not last.HasField("task"):
        raise RuntimeError("サーバからタスクが返らなかった")
    return last.task


@app.command("a2a-send")
def a2a_send(
    text: Annotated[str, typer.Argument(help="本文（質問・返信・仕様の Markdown）")] = "",
    url: Annotated[str, typer.Option("--url", help="SpecLino のベース URL")] = "",
    skill: Annotated[
        str | None,
        typer.Option(help="spec_review / requirement_consult / run_clarification"),
    ] = None,
    repo: Annotated[str | None, typer.Option(help="owner/name")] = None,
    behavior: Annotated[str | None, typer.Option(help="spec_review の対象の振る舞い")] = None,
    path: Annotated[str | None, typer.Option(help="spec_review の書き込み先の候補パス")] = None,
    requirement_id: Annotated[
        str | None, typer.Option(help="requirement_consult の要求 ID")
    ] = None,
    commit: Annotated[
        str | None, typer.Option(help="requirement_consult の検査対象のコミット")
    ] = None,
    run_id: Annotated[str | None, typer.Option(help="run_clarification の検査 ID")] = None,
    task_id: Annotated[str | None, typer.Option(help="入力待ちへの返信先のタスク")] = None,
    context_id: Annotated[str | None, typer.Option(help="入力待ちへの返信先の文脈")] = None,
    file: Annotated[Path | None, typer.Option(help="本文を読むファイル（front matter 可）")] = None,
) -> None:
    """A2A のエージェントにメッセージを送る。トークンは環境変数 SPECLINO_TOKEN から読む。"""
    token = os.environ.get("SPECLINO_TOKEN")
    if not token:
        typer.echo(
            "環境変数 SPECLINO_TOKEN にワークスペースの MCP トークン（slmcp_...）を"
            "設定してください。"
            "トークンは引数では受け付けない（シェルの履歴に残るため）",
            err=True,
        )
        raise typer.Exit(code=2)
    if not url:
        typer.echo("--url に SpecLino のベース URL を指定してください", err=True)
        raise typer.Exit(code=2)
    try:
        body = file.read_text(encoding="utf-8") if file is not None else text
    except OSError as e:
        typer.echo(f"--file を読めません: {file}: {e.strerror or type(e).__name__}", err=True)
        raise typer.Exit(code=2) from e
    fields = {
        "repo": repo,
        "behavior": behavior,
        "path": path,
        "requirement_id": requirement_id,
        "commit": commit,
        "run_id": run_id,
    }
    message = _a2a_message(body, skill, fields, task_id)
    if task_id:
        message.task_id = task_id
    if context_id:
        message.context_id = context_id

    async def _run() -> Task:
        async with _client(url, token) as http:
            return await _a2a_send(http, message)

    try:
        task = asyncio.run(_run())
    except Exception as e:  # 接続・認証・プロトコルの失敗。理由の型だけ見せ、トークンは出さない
        typer.echo(f"送信に失敗した: {type(e).__name__}: {str(e).replace(token, '***')}", err=True)
        raise typer.Exit(code=1) from e
    for line in _a2a_render(task):
        typer.echo(line)


def main() -> None:
    app()


if __name__ == "__main__":
    main()


@app.command()
def replay(
    run_dir: Annotated[Path, typer.Argument(help="成果物ディレクトリ（run-manifest.json 等）")],
    no_policy_check: Annotated[
        bool,
        typer.Option(
            "--no-policy-check",
            help=(
                "チーム規約の改訂検査を明示的に飛ばす（CLI は DB 接続を持たないため、"
                "run-manifest がチーム規約の改訂を記録している場合は既定で失敗する）"
            ),
        ),
    ] = False,
    project_id: Annotated[
        str | None,
        typer.Option("--project-id", help="bundle を固定した run のプロジェクト ID（DB を使う）"),
    ] = None,
    repo: Annotated[
        Path | None, typer.Option("--repo", help="bundle の run: head のスナップショット")
    ] = None,
    base: Annotated[
        Path | None, typer.Option("--base", help="bundle の PR の run: base のスナップショット")
    ] = None,
    trusted: Annotated[
        Path | None,
        typer.Option("--trusted", help="bundle の run: 信頼側（trusted_commit）のスナップショット"),
    ] = None,
) -> None:
    """凍結した IR から判定を再評価し、保存された結果と一致するか確認する（AC-013）。

    bundle を固定した run（S65）は `--project-id` と `--repo`（PR なら `--base` も）を渡すと、
    `SPECLINO_DATABASE_URL` の DB から記録の bundle を解決し、規約・lock を含む決定的な検査を
    再実行して比べる。

    run-manifest がチーム規約の改訂（`policy_revision`）を記録している run は、CLI 単体では
    DB に接続できず改訂を解決できないため既定で失敗する（規約が変わっても過去の判定が
    再現できることを検証できないまま「一致」を名乗らせないため）。`--no-policy-check` を
    付けるとその検査だけを明示的に飛ばして再評価する。
    """
    from speclino.services.replay_service import replay as _replay

    if project_id is not None:
        from speclino.services.replay_service import BundleReplayResult, replay_bundle

        if repo is None:
            typer.echo("入力不正: bundle の run の replay には --repo が要る", err=True)
            raise typer.Exit(code=2)
        head_root = repo

        async def work(s: AsyncSession) -> BundleReplayResult:
            return await replay_bundle(
                s,
                run_dir,
                project_id=project_id,
                repo_root=head_root,
                base_root=base,
                trusted_root=trusted,
            )

        try:
            bres = _in_transaction(work)
        except (OSError, LookupError) as e:
            typer.echo(f"入力不正: {e}", err=True)
            raise typer.Exit(code=2) from e
        typer.echo(
            f"run_id: {bres.run_id}  bundle: {bres.bundle_generation}  compared: {bres.compared}"
        )
        for d in bres.differences:
            typer.echo(f"  差異: {d}")
        if bres.llm_findings:
            typer.echo(f"  LLM 支援（記録で再評価、比較対象外）: {len(bres.llm_findings)} 件")
        typer.echo("一致" if bres.identical else "不一致")
        raise typer.Exit(code=0 if bres.identical else 1)

    try:
        res = _replay(run_dir, require_policy_check=not no_policy_check)
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
    import yaml

    from speclino.domain.policy import PolicyPackage, PolicyRevision
    from speclino.services.knowledge_registry import BundleConflict
    from speclino.services.policy_governance import seed_from_package

    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        package = PolicyPackage.model_validate(raw)
    except (OSError, ValidationError, ValueError) as e:
        typer.echo(f"入力不正: {e}", err=True)
        raise typer.Exit(code=2) from e

    async def seed(s: AsyncSession) -> PolicyRevision:
        try:
            return await seed_from_package(s, team=team, package=package)
        except BundleConflict as e:
            # 規約の有効化は知識の bundle を作り直す（S62）。作れなければ規約も有効にしない
            raise ValueError(f"規約を有効にすると知識の bundle を作れない: {e}") from e

    # 既に改訂があるチームへの seed は ValueError で拒否される（seed_from_package）
    revision = _in_transaction(seed)
    typer.echo(
        f"{team}@{revision.revision} を {revision.status} として取り込んだ"
        f"（rules={len(revision.package.rules)}）"
    )


@app.command("import-specs")
def import_specs(
    workspace: Annotated[str, typer.Option("--workspace", help="取り込み先のワークスペース")],
    project_id: Annotated[
        str, typer.Option("--project-id", help="仕様を当てるプロジェクトの ID（scope）")
    ],
    author: Annotated[str, typer.Option("--author", help="下書きの作者として記録する uid")],
    repo: Annotated[
        Path, typer.Option("--repo", help="specs/ と .speclino/ のあるディレクトリ")
    ] = Path("."),
) -> None:
    """リポジトリの旧形式の仕様（specs/*.md・specs/ir・domain.yaml）を spec の下書きにする。

    承認は画面か API で行う（承認時にサーバが IR を固定する）。図への書き換えは行わない。
    DB 接続は `SPECLINO_DATABASE_URL`。同じ名前の仕様が既にあれば拒否する（終了コード 2）。
    """
    from speclino.domain.knowledge import ArtifactRevision
    from speclino.services.spec_import import import_specs as _import

    async def work(s: AsyncSession) -> list[ArtifactRevision]:
        return await _import(
            s, workspace=workspace, project_id=project_id, repo_root=repo, author=author
        )

    try:
        created = _in_transaction(work)
    except (OSError, LookupError) as e:
        typer.echo(f"入力不正: {e}", err=True)
        raise typer.Exit(code=2) from e
    for rev in created:
        typer.echo(f"{rev.name}: 下書き {rev.revision} を作成した（{rev.content_hash}）")


@workspace_app.command("create")
def workspace_create(
    slug: Annotated[
        str, typer.Argument(help="ワークスペースの識別子（不変。run-manifest に残る）")
    ],
    owner_uid: Annotated[str, typer.Option("--owner-uid", help="owner として登録する利用者の uid")],
    name: Annotated[str | None, typer.Option("--name", help="表示名")] = None,
) -> None:
    """ワークスペース（`teams` 行）を作り、owner を登録する。

    既に同じ slug のワークスペースがあれば拒否する（作り直しは対象外）。
    """
    from speclino.services import workspace_admin

    _in_transaction(
        lambda s: workspace_admin.create_workspace(s, slug=slug, name=name, owner_uid=owner_uid)
    )
    typer.echo(f"ワークスペース {slug} を作成し、{owner_uid} を owner として登録した")


@workspace_app.command("add-member")
def workspace_add_member(
    slug: Annotated[str, typer.Argument(help="ワークスペースの識別子")],
    uid: Annotated[str, typer.Argument(help="登録する利用者の uid")],
    role: Annotated[str, typer.Option("--role", help="owner|approver|member")],
) -> None:
    """uid をワークスペースのメンバーにする（既にいれば role を置き換える）。"""
    from speclino.services import workspace_admin

    parsed_role = _parse_role(role)
    _in_transaction(lambda s: workspace_admin.add_member(s, slug=slug, uid=uid, role=parsed_role))
    typer.echo(f"{uid} を {slug} の {parsed_role} として登録した")


@workspace_app.command("add-project")
def workspace_add_project(
    slug: Annotated[str, typer.Argument(help="ワークスペースの識別子")],
    repo: Annotated[str, typer.Argument(help="登録するリポジトリ（owner/repo）")],
) -> None:
    """既存の project をワークスペースに登録する（project 自体はここで作らない）。"""
    from speclino.services import workspace_admin

    _in_transaction(lambda s: workspace_admin.add_project(s, slug=slug, repo_full_name=repo))
    typer.echo(f"{repo} を {slug} に登録した")


@workspace_app.command("backfill-repo-ids")
def workspace_backfill_repo_ids() -> None:
    """`github_repo_id` が未確定の project 全件にコードホストから引いた ID を埋める。

    埋められない行（コードホストが解決できない、または ID が既に他の行に使われている）は
    埋めずに team を外して隔離する（design/2026-09-27-workspaces.md の codex レビュー #3）。
    """
    from speclino.api.deps import build_code_host
    from speclino.services import workspace_admin
    from speclino.settings import Settings

    try:
        code_host = build_code_host(Settings())
    except RuntimeError as e:
        typer.echo(f"実行不能: {e}", err=True)
        raise typer.Exit(code=2) from e
    results = _in_transaction(lambda s: workspace_admin.backfill_repo_ids(s, code_host))
    for r in results:
        suffix = f"（{r.reason}）" if r.reason else ""
        typer.echo(f"{r.repo}: {r.action}{suffix}")
    isolated = sum(1 for r in results if r.action == "isolated")
    typer.echo(f"埋めた: {len(results) - isolated} 件 / 隔離: {isolated} 件")


@workspace_app.command("set-pr-approvers")
def workspace_set_pr_approvers(
    repo: Annotated[str, typer.Argument(help="対象リポジトリ（owner/repo）")],
    logins: Annotated[
        list[str] | None, typer.Argument(help="承認者の GitHub ログイン名（省略で全員外す）")
    ] = None,
) -> None:
    """PR コメントの `/speclino approve-spec` を受け付ける GitHub ログイン名を置き換える。"""
    from speclino.services import workspace_admin

    approvers = logins or []
    _in_transaction(
        lambda s: workspace_admin.set_pr_approvers(s, repo_full_name=repo, logins=approvers)
    )
    typer.echo(f"{repo} の PR 承認者を {approvers} にした")


@workspace_app.command("list")
def workspace_list() -> None:
    """全ワークスペースの slug・表示名・メンバー・登録 project を一覧する。"""
    from speclino.services import workspace_admin

    summaries = _in_transaction(workspace_admin.list_workspaces)
    if not summaries:
        typer.echo("ワークスペースが無い")
        return
    for w in summaries:
        typer.echo(f"{w.slug} ({w.name or '-'})")
        for m in w.members:
            typer.echo(f"  member: {m.uid} [{m.role}]")
        for p in w.projects:
            typer.echo(f"  project: {p}")
