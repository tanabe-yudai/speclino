"""junit XML を決定的にパースする（test.run の証拠）。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Literal

Status = Literal["passed", "failed", "error", "skipped"]


@dataclass(frozen=True)
class TestOutcome:
    test_id: str  # "path/to/test_x.py::test_name"
    status: Status
    message: str = ""


def parse_junit(xml_text: str) -> list[TestOutcome]:
    root = ET.fromstring(xml_text)  # noqa: S314 - 自前サンドボックスが出力した XML
    out: list[TestOutcome] = []
    for tc in root.iter("testcase"):
        classname = tc.get("classname", "")
        name = tc.get("name", "")
        file_ = tc.get("file")
        if file_:
            test_id = f"{file_}::{name}"
        else:
            # pytest の classname は "pkg.module" または "pkg.module.Class"
            module = classname.replace(".", "/")
            test_id = f"{module}.py::{name}" if module else name
        status: Status = "passed"
        message = ""
        for child in tc:
            if child.tag == "failure":
                status, message = "failed", (child.get("message") or child.text or "")[:500]
            elif child.tag == "error":
                status, message = "error", (child.get("message") or child.text or "")[:500]
            elif child.tag == "skipped":
                status, message = "skipped", (child.get("message") or "")[:500]
        out.append(TestOutcome(test_id, status, message))
    return sorted(out, key=lambda t: t.test_id)
