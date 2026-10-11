"""bundle の run の UML の義務: `<<module>>`、名前の一致の既定（bindings に無い操作・入口）、
宣言型による属性経由の呼出し、async / await、図の変数名の対応。"""

from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

from speclino.services.check_pipeline import run_check
from tests.unit.services.test_check_pipeline_bundle import (
    block,
    by_req,
    knowledge,
    request,
    spec_revision,
)

CLASSES = block(
    "%%@ id: vocab",
    "classDiagram",
    "    class registry {",
    "        <<module>>",
    "        +decide(t, store)",
    "        +finish(t)",
    "    }",
    "    class Store {",
    "        +save(t)",
    "        +deny(t)",
    "    }",
    "    class Ticket {",
    "        bool ok",
    "    }",
)

SEQUENCE = block(
    "%%@ id: flow",
    "%%@ entry: registry.decide",
    "sequenceDiagram",
    "    participant R as registry",
    "    participant S as Store",
    "    alt [a1] before.ticket.ok",
    "        R->>S: [m1] save(t)",
    "        R->>R: [m2] finish(t)",
    "    else [a2]",
    "        R->>S: [m3] deny(t)",
    "    end",
)

SPEC = "# 判定\n\n" + CLASSES + "\n" + SEQUENCE

STORE = """
class Store:
    def __init__(self, session: object) -> None:
        self._session = session

    async def save(self, t) -> None:
        pass

    async def deny(self, t) -> None:
        pass
"""

REGISTRY = """
from app.store import Store


async def decide(t, store: Store) -> None:
    if not t.ok:
        await store.deny(t)
        return None
    await store.save(t)
    await finish(t)


async def finish(t) -> None:
    pass
"""


def make_repo(root: Path, registry: str = REGISTRY, variables: bool = True) -> Path:
    app = root / "app"
    app.mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "store.py").write_text(textwrap.dedent(STORE))
    (app / "registry.py").write_text(textwrap.dedent(registry))
    (root / ".speclino").mkdir()
    bindings = {
        "schema_version": "0.1",
        "behaviors": {
            "decide": {
                "entrypoint": "app.registry.decide",
                "variables": {"ticket.ok": "t.ok"} if variables else {},
            }
        },
    }
    (root / ".speclino" / "bindings.yaml").write_text(yaml.safe_dump(bindings))
    return root


def run(root: Path) -> dict[str, object]:
    res = run_check(request(root, knowledge(spec_revision("s", SPEC, domain=None))))
    return by_req(res.findings.findings)


def test_module_structure_and_implicit_names_verified(tmp_path: Path) -> None:
    found = run(make_repo(tmp_path / "r"))
    for rid in ("vocab#registry", "vocab#Store", "flow#registry.decide"):
        f = found[rid]
        assert f.verdict == "VERIFIED_IN_SCOPE", (rid, f.message)  # type: ignore[attr-defined]


def test_missing_awaited_call_is_violated(tmp_path: Path) -> None:
    broken = REGISTRY.replace("    await store.save(t)\n", "")
    f = run(make_repo(tmp_path / "r", broken))["flow#registry.decide"]
    assert f.verdict == "VIOLATED", f.message  # type: ignore[attr-defined]


def test_unbound_guard_variable_is_unknown(tmp_path: Path) -> None:
    f = run(make_repo(tmp_path / "r", variables=False))["flow#registry.decide"]
    assert f.verdict == "UNKNOWN"  # type: ignore[attr-defined]
    assert "ticket.ok" in f.message  # type: ignore[attr-defined]


def test_missing_module_function_is_violated(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r", REGISTRY.replace("async def finish", "async def _finish"))
    found = run(root)
    assert found["vocab#registry"].verdict == "VIOLATED"  # type: ignore[attr-defined]
    # 図の操作 registry.finish がコードに無いので順序の義務は対応なし（UNKNOWN）
    assert found["flow#registry.decide"].verdict == "UNKNOWN"  # type: ignore[attr-defined]


def test_implicit_operation_body_is_expanded(tmp_path: Path) -> None:
    # 名前の一致で対応づけた操作は効果を「無い」と仮定しない
    # （本体に解決できない呼出しがあれば UNKNOWN）
    root = make_repo(tmp_path / "r")
    store = root / "app" / "store.py"
    store.write_text(
        store.read_text().replace(
            "    async def save(self, t) -> None:\n        pass",
            "    async def save(self, t) -> None:\n        await self._session.add(t)",
        )
    )
    f = run(root)["flow#registry.decide"]
    assert f.verdict == "UNKNOWN", f.message  # type: ignore[attr-defined]


STATES = block(
    "%%@ id: st",
    "%%@ attribute: ticket.phase",
    "stateDiagram-v2",
    "    [*] --> open",
    "    open --> closed: [t1] close",
)


def test_state_writer_without_binding_is_unknown(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path / "r",
        REGISTRY + "\n\nasync def reopen(t) -> None:\n    t.phase = 'open'\n",
    )
    data = yaml.safe_load((root / ".speclino" / "bindings.yaml").read_text())
    data["behaviors"]["reopen"] = {"entrypoint": "app.registry.reopen"}
    (root / ".speclino" / "bindings.yaml").write_text(yaml.safe_dump(data))
    classes = CLASSES.replace(
        "        bool ok\n", "        bool ok\n        Phase phase\n"
    ) + block(
        "%%@ id: ph",
        "classDiagram",
        "    class Phase {",
        "        <<enumeration>>",
        "        open",
        "        closed",
        "    }",
    )
    md = "# 状態\n\n" + classes + "\n" + STATES
    res = run_check(request(root, knowledge(spec_revision("s", md, domain=None))))
    found = [f for f in res.findings.findings if f.requirement_id == "st"]
    assert any(
        f.behavior_id == "reopen" and f.verdict == "UNKNOWN" and "代入" in f.message for f in found
    ), [(f.behavior_id, f.message) for f in found]
