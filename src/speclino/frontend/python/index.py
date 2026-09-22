"""python_frontend: ソースを読み、AST・import・シンボルを索引化する（PoC 9.1 の 1〜3）。

対象リポジトリのモジュールを import しない。ファイルを読んで `ast.parse` するだけ。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path


class SymbolNotFound(LookupError):
    pass


@dataclass(frozen=True)
class FunctionInfo:
    qualname: str  # 完全修飾（pkg.mod.func または pkg.mod.Cls.method）
    module: str
    node: ast.FunctionDef
    path: Path

    @property
    def line_start(self) -> int:
        return self.node.lineno

    @property
    def line_end(self) -> int:
        return self.node.end_lineno or self.node.lineno


@dataclass
class ModuleInfo:
    name: str
    path: Path
    tree: ast.Module
    imports: dict[str, str] = field(default_factory=dict)  # ローカル名 → 完全修飾名
    functions: dict[str, ast.FunctionDef] = field(default_factory=dict)
    classes: dict[str, ast.ClassDef] = field(default_factory=dict)
    enums: dict[str, list[str]] = field(default_factory=dict)  # Enum クラス名 → 値名

    def resolve_name(self, name: str) -> str:
        """モジュール内のローカル名（import 名・定義名）を完全修飾名にする。"""
        if name in self.imports:
            return self.imports[name]
        if name in self.functions or name in self.classes:
            return f"{self.name}.{name}"
        return name


def _collect(mod: ModuleInfo) -> None:
    for node in mod.tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                local = a.asname or a.name.split(".")[0]
                mod.imports[local] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                mod.imports[a.asname or a.name] = f"{node.module}.{a.name}"
        elif isinstance(node, ast.FunctionDef):
            mod.functions[node.name] = node
        elif isinstance(node, ast.ClassDef):
            mod.classes[node.name] = node
            if any(_is_enum_base(b) for b in node.bases):
                values = [
                    t.id
                    for s in node.body
                    if isinstance(s, ast.Assign)
                    for t in s.targets
                    if isinstance(t, ast.Name)
                ]
                mod.enums[node.name] = values


def _is_enum_base(b: ast.expr) -> bool:
    return (isinstance(b, ast.Name) and b.id in ("Enum", "StrEnum", "IntEnum")) or (
        isinstance(b, ast.Attribute) and b.attr in ("Enum", "StrEnum", "IntEnum")
    )


class RepoIndex:
    """リポジトリ内の Python モジュールを名前で引く。`source_roots` はモジュール名の起点。"""

    def __init__(self, root: Path, source_roots: tuple[str, ...] = (".", "src")) -> None:
        self.root = root
        self.source_roots = source_roots
        self._modules: dict[str, ModuleInfo] = {}

    def module_path(self, module: str) -> Path | None:
        rel = Path(*module.split("."))
        for sr in self.source_roots:
            base = self.root / sr
            for cand in (base / rel.with_suffix(".py"), base / rel / "__init__.py"):
                if cand.is_file():
                    return cand
        return None

    def module(self, name: str) -> ModuleInfo | None:
        if name in self._modules:
            return self._modules[name]
        path = self.module_path(name)
        if path is None:
            return None
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        mod = ModuleInfo(name=name, path=path, tree=tree)
        _collect(mod)
        self._modules[name] = mod
        return mod

    def split_symbol(self, qualname: str) -> tuple[ModuleInfo, list[str]] | None:
        """`pkg.mod.attr.sub` を (モジュール, [attr, sub]) に分ける（最長一致）。"""
        parts = qualname.split(".")
        for i in range(len(parts), 0, -1):
            mod = self.module(".".join(parts[:i]))
            if mod is not None:
                return mod, parts[i:]
        return None

    def function(self, qualname: str) -> FunctionInfo:
        found = self.split_symbol(qualname)
        if found is None:
            raise SymbolNotFound(f"モジュールが見つからない: {qualname}")
        mod, rest = found
        if len(rest) == 1 and rest[0] in mod.functions:
            return FunctionInfo(qualname, mod.name, mod.functions[rest[0]], mod.path)
        if len(rest) == 2 and rest[0] in mod.classes:
            for s in mod.classes[rest[0]].body:
                if isinstance(s, ast.FunctionDef) and s.name == rest[1]:
                    return FunctionInfo(qualname, mod.name, s, mod.path)
        raise SymbolNotFound(f"関数が見つからない: {qualname}")

    def enum_values(self, qualname: str) -> list[str] | None:
        found = self.split_symbol(qualname)
        if found is None:
            return None
        mod, rest = found
        if len(rest) == 1:
            return mod.enums.get(rest[0])
        return None

    def is_function(self, qualname: str) -> bool:
        try:
            self.function(qualname)
            return True
        except SymbolNotFound:
            return False
