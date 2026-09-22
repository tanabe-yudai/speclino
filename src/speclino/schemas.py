"""domain/ の pydantic モデルから JSON Schema を生成する（schemas/ は手で編集しない）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from speclino.domain import (
    Bindings,
    CodeIR,
    DomainSpec,
    Findings,
    OperationContract,
    RunManifest,
    SpecIR,
    TraceRecord,
)
from speclino.domain.expr import Nary

SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "spec-ir": SpecIR,
    "code-ir": CodeIR,
    "findings": Findings,
    "bindings": Bindings,
    "contract": OperationContract,
    "domain": DomainSpec,
    "trace": TraceRecord,
    "run-manifest": RunManifest,
}


def expr_schema() -> dict[str, Any]:
    """Expr 単体のスキーマ。Nary をルートにして $defs から Expr を取り出す。"""
    root = Nary.model_json_schema()
    defs = root.get("$defs", {})
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://speclino.dev/schemas/expr.schema.json",
        "title": "Expr",
        "oneOf": [{"$ref": f"#/$defs/{n}"} for n in ("Ref", "Const", "Not", "Binary", "Nary")],
        "$defs": {**defs, "Nary": {k: v for k, v in root.items() if k != "$defs"}},
    }
    return schema


def model_schema(name: str, model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://speclino.dev/schemas/{name}.schema.json",
        **schema,
    }
    return schema


def export_schemas(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    schemas: dict[str, dict[str, Any]] = {"expr": expr_schema()}
    schemas.update({name: model_schema(name, m) for name, m in SCHEMA_MODELS.items()})
    for name, schema in schemas.items():
        path = out_dir / f"{name}.schema.json"
        path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written
