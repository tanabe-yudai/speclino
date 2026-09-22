"""replay（AC-013）: 凍結した成果物から同じ判定が再現し、改ざんは検出される。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from speclino.cli import app
from speclino.services.check_pipeline import run_check, write_result
from speclino.services.replay_service import replay
from tests.fixtures import cancel_order as co
from tests.unit.services.test_check_pipeline import orders_request


def test_replay_reproduces_saved_findings(tmp_path: Path) -> None:
    override = tmp_path / "code-ir.json"
    override.write_text(co.RESTORE_ADMIN_ONLY.model_dump_json())
    out = tmp_path / "out"
    write_result(run_check(orders_request(code_ir_override=override, run_id="r1")), out)
    assert (out / "domain.json").exists()
    res = replay(out)
    assert res.identical and res.compared == 9 and res.differences == []


def test_replay_detects_tampered_findings(tmp_path: Path) -> None:
    override = tmp_path / "code-ir.json"
    override.write_text(co.RESTORE_ADMIN_ONLY.model_dump_json())
    out = tmp_path / "out"
    write_result(run_check(orders_request(code_ir_override=override, run_id="r1")), out)
    data = json.loads((out / "findings.json").read_text())
    for f in data["findings"]:
        if f["requirement_id"] == "R-CANCEL-04":
            f["verdict"], f["category"] = "VERIFIED_IN_SCOPE", "conformant"
    (out / "findings.json").write_text(json.dumps(data))
    res = replay(out)
    assert not res.identical and any("R-CANCEL-04" in d for d in res.differences)
    cli = CliRunner().invoke(app, ["replay", str(out)])
    assert cli.exit_code == 1 and "不一致" in cli.output
