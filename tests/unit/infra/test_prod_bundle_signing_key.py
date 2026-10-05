"""本番の Terraform が bundle の署名鍵を web・worker・admin Job に渡すこと（#114）。

terraform を動かさずに、宣言の文字列で確かめる（plan / apply はしない）。値はコードに書かない。
"""

from __future__ import annotations

import re
from pathlib import Path

PROD = Path(__file__).resolve().parents[3] / "infra" / "envs" / "prod"


def _read(name: str) -> str:
    return (PROD / name).read_text(encoding="utf-8")


def test_secret_container_and_accessors_for_web_and_worker() -> None:
    secrets = _read("secrets.tf")
    assert re.search(r'"bundle-signing-key"\s*=\s*\["web",\s*"worker"\]', secrets)
    # 値（secret_version）は Terraform で作らない
    assert "google_secret_manager_secret_version" not in secrets


def test_env_injected_into_web_and_worker_behind_flag() -> None:
    secrets = _read("secrets.tf")
    assert re.search(
        r"var\.mount_bundle_signing_key\s*\?\s*\{\s*"
        r'SPECLINO_BUNDLE_SIGNING_KEY\s*=\s*"bundle-signing-key"',
        secrets,
    )
    web = secrets.split("web = merge(", 1)[1].split("worker = merge(", 1)[0]
    worker = secrets.split("worker = merge(", 1)[1].split("sandbox", 1)[0]
    assert "local.bundle_signing_env" in web
    assert "local.bundle_signing_env" in worker


def test_admin_job_runs_as_prod_with_signing_key() -> None:
    run = _read("run.tf")
    admin = run.split('resource "google_cloud_run_v2_job" "admin"', 1)[1]
    assert re.search(r'name\s*=\s*"SPECLINO_ENV"\s*\n\s*value\s*=\s*"prod"', admin)
    assert "for_each = local.bundle_signing_env" in admin


def test_flag_is_declared_and_defaults_off() -> None:
    variables = _read("variables.tf")
    block = variables.split('variable "mount_bundle_signing_key"', 1)[1]
    assert re.search(r"default\s*=\s*false", block)


def test_old_keys_secret_is_optional_and_off_by_default() -> None:
    """#114: 鍵の更新の間だけ前の鍵を渡す。既定では注入しない。"""
    secrets = _read("secrets.tf")
    assert re.search(r'"bundle-signing-keys-old"\s*=\s*\["web",\s*"worker"\]', secrets)
    assert re.search(
        r"var\.mount_bundle_signing_keys_old\s*\?\s*\{\s*"
        r'SPECLINO_BUNDLE_SIGNING_KEYS_OLD\s*=\s*"bundle-signing-keys-old"',
        secrets,
    )
    block = _read("variables.tf").split('variable "mount_bundle_signing_keys_old"', 1)[1]
    assert re.search(r"default\s*=\s*false", block)
