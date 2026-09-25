"""Cloud Run sandboxes 経由でテストを実行するクライアント（S38）。`Sandbox` ポートの本番実装。

sandbox ロールのサービスは権限ゼロの SA で動く（GCS を読めない）ため、head と宣言済みテストは
worker がその場で tar にまとめ HTTP で持ち込む。認証は OIDC の ID トークン
（audience はサンドボックスサービスの URL）で、`google.auth` の ADC 経由で取得する
（worker SA にのみ `run.invoker` を付与）。

平坦化・junit のファイル名の戻し方は `docker_local.py` と揃える（`copy_declared_tests` /
`rewrite_junit_test_ids` を共有する）。リクエスト・レスポンスの形は `sandbox_runner.runner` の
pydantic モデルをそのまま使い、両側の契約がずれないようにする。
"""

from __future__ import annotations

import base64
import io
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token as google_id_token

from speclino.adapters.sandbox.docker_local import copy_declared_tests, rewrite_junit_test_ids
from speclino.ports.sandbox import SandboxResult
from speclino.sandbox_runner.runner import SandboxRunRequest, SandboxRunResponse

RUN_PATH = "/internal/sandbox/run"
PYTEST_INI = "[pytest]\naddopts = -p no:cacheprovider\n"
# HTTP 側のタイムアウトは、サーバ側が timeout_s ぶん待ってから応答するのを待てるだけの余裕を持つ
REQUEST_TIMEOUT_MARGIN_S = 60.0


@dataclass
class CloudRunSandbox:
    sandbox_url: str
    http: httpx.Client = field(default_factory=httpx.Client)

    def _id_token(self) -> str:
        """audience = sandbox サービスの URL の OIDC ID トークンを ADC から取得する。"""
        audience = self.sandbox_url.rstrip("/")
        token: str = google_id_token.fetch_id_token(  # type: ignore[no-untyped-call]
            GoogleAuthRequest(), audience
        )
        return token

    def _build_tar_b64(
        self, head_root: Path, base_tests_root: Path, test_ids: list[str]
    ) -> tuple[str, dict[str, str]] | None:
        """head をそのまま、宣言済みテストは平坦化して 1 つの tar にまとめ base64 化する。

        戻り値は (tar の base64, 元 ID → 平坦化した ID の対応)。宣言済みテストが 1 つも
        base に無ければ None（呼び出し側で「実行できない」を返す）。
        """
        with tempfile.TemporaryDirectory(prefix="speclino-sandbox-client-") as tmp:
            tests_dst = Path(tmp) / "tests"
            mapping = copy_declared_tests(base_tests_root, tests_dst, test_ids)
            if not mapping:
                return None
            (tests_dst / "pytest.ini").write_text(PYTEST_INI)  # 設定は SpecLino 側で固定

            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                tar.add(head_root, arcname="head")
                tar.add(tests_dst, arcname="tests")
            return base64.b64encode(buf.getvalue()).decode("ascii"), mapping

    def run_tests(
        self, head_root: Path, base_tests_root: Path, test_ids: list[str], timeout_s: int = 120
    ) -> SandboxResult:
        if not test_ids:
            return SandboxResult(2, None, "テスト ID が無い", False, "cloud_run_sandbox")

        built = self._build_tar_b64(head_root, base_tests_root, test_ids)
        if built is None:
            return SandboxResult(
                2, None, "宣言済みテストが base に無い", False, "cloud_run_sandbox"
            )
        tar_b64, mapping = built

        body = SandboxRunRequest(
            tar_b64=tar_b64, test_ids=list(mapping.values()), timeout_s=timeout_s
        )
        url = self.sandbox_url.rstrip("/") + RUN_PATH
        try:
            response = self.http.post(
                url,
                json=body.model_dump(),
                headers={"Authorization": f"Bearer {self._id_token()}"},
                timeout=float(timeout_s) + REQUEST_TIMEOUT_MARGIN_S,
            )
            response.raise_for_status()
        except httpx.TimeoutException:
            return SandboxResult(
                -1, None, "sandbox の呼び出しがタイムアウトした", True, "cloud_run_sandbox"
            )
        except httpx.HTTPError as e:
            return SandboxResult(
                -1, None, f"sandbox の呼び出しに失敗: {e}", False, "cloud_run_sandbox"
            )

        result = SandboxRunResponse.model_validate(response.json())
        junit = result.junit_xml
        if junit is not None:
            junit = rewrite_junit_test_ids(junit, mapping)
        return SandboxResult(
            result.exit_code, junit, result.stdout_tail, result.timed_out, "cloud_run_sandbox"
        )


__all__ = ["CloudRunSandbox"]
