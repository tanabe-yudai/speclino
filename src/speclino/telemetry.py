"""可観測性: OpenTelemetry を ADK のフックで設定し、Cloud Trace へ送る（本番のみ）。

ローカルでは何もしない（環境変数 SPECLINO_TRACING=cloud のときだけ有効）。ADK の
`invoke_agent` / `generate_content` / `execute_tool` span に加え、パイプラインの span を
`speclino.pipeline` トレーサで出す。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)
_configured = False


def configure_tracing(mode: str | None = None) -> bool:
    """Cloud Trace エクスポータを一度だけ設定する。設定できたら True。"""
    global _configured
    mode = mode or os.environ.get("SPECLINO_TRACING", "off")
    if _configured or mode != "cloud":
        return _configured
    try:
        from google.adk.telemetry.google_cloud import get_gcp_exporters
        from google.adk.telemetry.setup import maybe_set_otel_providers

        hooks = get_gcp_exporters(enable_cloud_tracing=True, enable_cloud_logging=False)
        maybe_set_otel_providers([hooks])
        _configured = True
    except Exception as e:  # noqa: BLE001 - 可観測性の失敗で検証を止めない
        logger.warning("Cloud Trace の設定に失敗: %s", e)
        return False
    return True


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    """パイプラインの区間。OTel が無効でもそのまま実行する。"""
    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover
        yield
        return
    tracer = trace.get_tracer("speclino.pipeline")
    with tracer.start_as_current_span(name) as s:
        for k, v in attributes.items():
            s.set_attribute(k, v)
        yield
