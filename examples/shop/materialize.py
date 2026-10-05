"""ショップ題材を、SpecLino が検査する 1 つのリポジトリの形に書き出す。

検査の対象のリポジトリは「根に `.speclino/`、コードは `examples/shop/app/`」の形にする
（bindings のシンボルが `examples.shop.app.*` なので）。`mutations/*.patch` はこの形の根
からの相対パスで書いてあり、`git apply` で当てる。テスト（tests/integration/services/
test_shop_bundle.py）と examples/shop/seed.py が使う。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SHOP = Path(__file__).resolve().parent
MUTATIONS = SHOP / "mutations"
SPEC = SHOP / "spec.md"
DOMAIN = SHOP / ".speclino" / "domain.yaml"


def mutation_names() -> list[str]:
    return sorted(p.stem for p in MUTATIONS.glob("*.patch"))


def materialize(root: Path, mutation: str | None = None) -> Path:
    """root にショップのリポジトリを作る。mutation（`01-missing-charge` 等）があれば当てる。"""
    app = root / "examples" / "shop" / "app"
    app.mkdir(parents=True)
    (root / "examples" / "__init__.py").write_text("")
    (root / "examples" / "shop" / "__init__.py").write_text("")
    for src in sorted((SHOP / "app").glob("*.py")):
        shutil.copy(src, app / src.name)
    shutil.copytree(SHOP / ".speclino", root / ".speclino")
    if mutation is not None:
        apply_mutation(root, mutation)
    return root


def apply_mutation(root: Path, mutation: str) -> None:
    patch = MUTATIONS / f"{mutation}.patch"
    if not patch.is_file():
        raise ValueError(f"未知の変異: {mutation}（{', '.join(mutation_names())}）")
    subprocess.run(
        ["git", "apply", "--whitespace=nowarn", str(patch)],
        cwd=root,
        check=True,
        capture_output=True,
    )
