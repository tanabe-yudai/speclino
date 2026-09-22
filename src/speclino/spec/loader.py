"""`.speclino/domain.yaml` `.speclino/bindings.yaml` `.speclino/contracts/*` と Spec IR
（json/yaml）の読み込み。未定義フィールドの拒否は各 IRModel（`extra="forbid"`）が行うため、
ここでは構文解析と型検証（`model_validate`）だけを担う。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from speclino.domain.bindings import Bindings, OperationContract
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.spec_ir import SpecIR

_YAML_SUFFIXES = (".yaml", ".yml")


def _read_structured(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix in _YAML_SUFFIXES:
        return yaml.safe_load(text)
    return json.loads(text)


def load_domain(path: Path | str) -> DomainSpec:
    return DomainSpec.model_validate(_read_structured(Path(path)))


def load_bindings(path: Path | str) -> Bindings:
    return Bindings.model_validate(_read_structured(Path(path)))


def load_spec_ir(path: Path | str) -> SpecIR:
    return SpecIR.model_validate(_read_structured(Path(path)))


def load_contracts(directory: Path | str) -> dict[str, OperationContract]:
    """ディレクトリ内の `*.yaml` / `*.yml` / `*.json` を契約として読み込む。

    キーはファイル名ではなく契約自身の `id`（`bindings.operations[*].summary_ref` が参照する値）。
    """
    contracts: dict[str, OperationContract] = {}
    for file in sorted(Path(directory).iterdir()):
        if file.suffix not in (*_YAML_SUFFIXES, ".json"):
            continue
        contract = OperationContract.model_validate(_read_structured(file))
        contracts[contract.id] = contract
    return contracts


def content_hash(data: str | bytes) -> str:
    """`sha256:` を前置いた16進ダイジェスト。承認ハッシュ（spec_content_hash / ir_content_hash）
    と同じ正規形式にする。
    """
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return "sha256:" + hashlib.sha256(raw).hexdigest()


__all__ = [
    "content_hash",
    "load_bindings",
    "load_contracts",
    "load_domain",
    "load_spec_ir",
]
