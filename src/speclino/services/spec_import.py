"""旧形式の仕様の取り込み（S65）。

リポジトリの `specs/ir/*.spec-ir.json` を、その要求 ID を含む `specs/*.md` ごとにまとめ、
spec の下書き（markdown・domain・legacy_ir）として登録する。`.speclino/domain.yaml` は spec の内容の
`domain` 節として取り込む。図への書き換えは行わない（人が移す）。承認は通常の承認の流れで行う
（承認時にサーバが `compile_spec` で IR を固定する）。

- IR は `check_pipeline` の Git の流れと同じ規則（要求 ID を含む最初の Markdown、名前順）で本文に
  対応づける。承認前の本文の照合（spec_content_hash）が取り込み前後で変わらないようにするため
- 対応する Markdown が無い IR は取り込まない（ValueError）。本文の無い仕様は人が読めない
- 図（```mermaid）を含む Markdown は、IR が無くても取り込む（UML の仕様）
- artifact 名は Markdown のファイル名から作る（小文字・英数字とハイフン）
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from speclino.domain.knowledge import ArtifactRevision, ArtifactScope, SpecContent
from speclino.domain.spec_ir import SpecIR
from speclino.spec import load_domain, load_spec_ir

_NAME_INVALID = re.compile(r"[^a-z0-9]+")


def artifact_name(md_path: Path) -> str:
    """`specs/SPEC-TOOL-001.md` → `spec-tool-001`。"""
    name = _NAME_INVALID.sub("-", md_path.stem.lower()).strip("-")[:64].rstrip("-")
    if not name:
        raise ValueError(f"仕様のファイル名から artifact 名を作れない: {md_path.name}")
    return name


def _requirement_ids(spec: SpecIR) -> set[str]:
    return {
        ref.requirement_id for rule in spec.rules for ref in rule.source_refs if ref.requirement_id
    }


def plan_import(repo_root: Path) -> list[tuple[Path, list[SpecIR]]]:
    """取り込む (Markdown, その Markdown に対応する IR) の一覧。DB に触れない。"""
    specs_dir = repo_root / "specs"
    md_files = sorted(specs_dir.glob("*.md")) if specs_dir.is_dir() else []
    texts = {p: p.read_text(encoding="utf-8") for p in md_files}
    ir_dir = specs_dir / "ir"
    ir_files = sorted(ir_dir.glob("*.spec-ir.json")) if ir_dir.is_dir() else []
    grouped: dict[Path, list[SpecIR]] = {}
    orphans: list[str] = []
    for path in ir_files:
        spec = load_spec_ir(path)
        ids = _requirement_ids(spec)
        md = next((p for p, text in texts.items() if any(r in text for r in ids)), None)
        if md is None:
            orphans.append(path.name)
            continue
        grouped.setdefault(md, []).append(spec)
    if orphans:
        raise ValueError(
            "要求 ID を含む specs/*.md が見つからない Spec IR は取り込めない: " + ", ".join(orphans)
        )
    for p, text in texts.items():
        if p not in grouped and "```mermaid" in text:
            grouped[p] = []
    plan = sorted(grouped.items(), key=lambda kv: kv[0].name)
    names = [artifact_name(p) for p, _ in plan]
    duplicated = sorted({n for n in names if names.count(n) > 1})
    if duplicated:
        raise ValueError(f"artifact 名が重なる仕様ファイルがある: {', '.join(duplicated)}")
    return plan


async def import_specs(
    session: AsyncSession, *, workspace: str, project_id: str, repo_root: Path, author: str
) -> list[ArtifactRevision]:
    """`repo_root` の旧形式の仕様を、project にスコープした spec の下書きとして登録する。

    同じ名前の artifact が既にあれば `ArtifactExists`、project が workspace に属さなければ
    `InvalidArtifact`（どちらも S62）。呼び出し側の 1 トランザクションで行う。
    """
    from speclino.services import knowledge_registry as kr

    domain_path = repo_root / ".speclino" / "domain.yaml"
    domain = load_domain(domain_path) if domain_path.is_file() else None
    plan = plan_import(repo_root)
    if not plan:
        raise ValueError(f"取り込む仕様が無い: {repo_root / 'specs'}")
    created: list[ArtifactRevision] = []
    for md_path, legacy in plan:
        content = SpecContent(
            kind="spec",
            markdown=md_path.read_text(encoding="utf-8"),
            domain=domain,
            legacy_ir=legacy,
            scope=ArtifactScope(projects=[project_id]),
        )
        created.append(
            await kr.create_draft(
                session,
                workspace=workspace,
                kind="spec",
                name=artifact_name(md_path),
                content=content,
                author=author,
            )
        )
    return created


__all__ = ["artifact_name", "import_specs", "plan_import"]
