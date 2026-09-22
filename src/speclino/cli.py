"""SpecLino CLI エントリポイント。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from speclino import __version__

app = typer.Typer(name="speclino", help="仕様–実装の意味差分レビュー基盤 CLI")
DEFAULT_SCHEMAS_DIR = Path("schemas")


@app.command()
def check(
    repo: str = typer.Option(..., "--repo", help="対象リポジトリのパスまたは URL"),
    base: str = typer.Option(..., "--base", help="比較元の参照（コミット/ブランチ）"),
    head: str = typer.Option(..., "--head", help="比較先の参照（コミット/ブランチ）"),
    config: str | None = typer.Option(None, "--config", help="設定ファイルのパス"),
    code_ir: str | None = typer.Option(None, "--code-ir", help="コード IR の出力先/入力"),
    out: str | None = typer.Option(None, "--out", help="レポートの出力先"),
) -> None:
    """仕様とコードの意味差分を検査する（未実装）。"""
    typer.echo("not implemented", err=True)
    raise typer.Exit(code=2)


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
