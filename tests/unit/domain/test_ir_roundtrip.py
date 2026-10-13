"""IR の例が IR 型でラウンドトリップできること。"""

from __future__ import annotations

import json

import yaml
from pydantic import TypeAdapter

from speclino.domain import CodeIR, Path, Rule
from speclino.domain.expr import Expr, refs_in

SPEC_RULE_YAML = """
id: R-CANCEL-04
behavior_id: cancel_order
source_refs:
  - snapshot_id: demo-spec
    requirement_id: R-CANCEL-04
approval_ref: demo-approval
modality: must
claim_scope: command_invocation
applicability:
  op: and
  args:
    - op: eq
      left: {op: ref, path: before.order.status}
      right: {op: const, type: OrderStatus, value: PENDING}
    - op: or
      args:
        - {op: ref, path: before.actor.is_admin}
        - op: eq
          left: {op: ref, path: before.actor.id}
          right: {op: ref, path: before.order.owner_id}
assertion:
  kind: event_requirement
  event_kind: call
  operation_or_target: inventory_restore_command
  phase: invoked
  count: {operator: eq, value: 1}
  arguments_equal:
    items: {op: ref, path: before.order.items}
"""

CODE_PATH_YAML = """
path_id: owner_pending_path
precision: exact_in_model
guard:
  op: and
  args:
    - op: eq
      left: {op: ref, path: before.order.status}
      right: {op: const, type: OrderStatus, value: PENDING}
    - op: not
      arg: {op: ref, path: before.actor.is_admin}
    - op: eq
      left: {op: ref, path: before.actor.id}
      right: {op: ref, path: before.order.owner_id}
state_updates:
  - target: order.status
    value: {op: const, type: OrderStatus, value: CANCELLED}
events:
  - sequence: 1
    kind: write
    target: order.status
    phase: applied
    value: {op: const, type: OrderStatus, value: CANCELLED}
  - sequence: 2
    kind: call
    operation: audit_record_command
    phase: invoked
    arguments:
      actor_id: {op: ref, path: before.actor.id}
      action: {op: const, type: AuditAction, value: CANCEL_ORDER}
outcome:
  kind: http_response
  status: 204
unknown_causes: []
source_refs:
  - snapshot_id: demo-code
    symbol: app.orders.service.cancel_order
    line_start: 10
    line_end: 20
"""


def test_spec_rule_roundtrip() -> None:
    rule = Rule.model_validate(yaml.safe_load(SPEC_RULE_YAML))
    assert rule.assertion.kind == "event_requirement"
    again = Rule.model_validate_json(rule.model_dump_json())
    assert again == rule
    assert [r.path for r in refs_in(rule.applicability)] == [
        "before.order.status",
        "before.actor.is_admin",
        "before.actor.id",
        "before.order.owner_id",
    ]


def test_code_path_roundtrip() -> None:
    path = Path.model_validate(yaml.safe_load(CODE_PATH_YAML))
    assert path.outcome.status == 204
    assert [e.operation_or_target for e in path.events] == ["order.status", "audit_record_command"]
    again = Path.model_validate(json.loads(path.model_dump_json()))
    assert again == path


def test_code_ir_complete_flag() -> None:
    path = Path.model_validate(yaml.safe_load(CODE_PATH_YAML))
    ir = CodeIR(
        behavior_id="cancel_order",
        entrypoint="app.orders.service.cancel_order",
        snapshot={"snapshot_id": "demo-code", "commit": "abc"},
        analyzer_version="0",
        limits={"depth": 2, "max_paths": 64, "solver_timeout_ms": 500},
        paths=[path],
    )
    assert ir.complete


def test_expr_adapter_parses_discriminated_union() -> None:
    expr = TypeAdapter(Expr).validate_python(
        {"op": "not", "arg": {"op": "ref", "path": "outcome.status"}}
    )
    assert expr.op == "not"
