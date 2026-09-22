"""注文キャンセル（PoC 4 章）の手書き IR。検査器の単体試験と Golden の元。"""

from __future__ import annotations

from datetime import UTC, datetime

from speclino.domain import (
    AnalysisLimits,
    ApprovalRecord,
    CodeIR,
    DomainSpec,
    Event,
    Outcome,
    Path,
    Rule,
    SnapshotRef,
    SourceRef,
    SpecIR,
    StateUpdate,
)
from speclino.domain.expr import Expr, and_, const, eq, ne, not_, or_, ref

# ---------------------------------------------------------------- domain
DOMAIN = DomainSpec(
    enums={"OrderStatus": ["PENDING", "SHIPPED", "CANCELLED"], "AuditAction": ["CANCEL_ORDER"]},
    opaque_types=["UserId", "OrderId", "ItemSet"],
    variables={
        "actor.is_admin": "boolean",
        "actor.id": "UserId",
        "order.owner_id": "UserId",
        "order.status": "OrderStatus",
        "order.items": "ItemSet",
    },
    observation_scope=["サービス層の状態", "コマンド呼出し", "モデル化した応答"],
    out_of_scope=["DB コミット", "並行実行", "外部サービス内部"],
)

IS_ADMIN = ref("before.actor.is_admin")
IS_OWNER = eq(ref("before.actor.id"), ref("before.order.owner_id"))
AUTHORIZED = or_(IS_ADMIN, IS_OWNER)
PENDING = eq(ref("before.order.status"), const("OrderStatus", "PENDING"))
NOT_PENDING = ne(ref("before.order.status"), const("OrderStatus", "PENDING"))
SUCCESS = and_(AUTHORIZED, PENDING)
REJECTED = or_(not_(AUTHORIZED), and_(AUTHORIZED, NOT_PENDING))
CANCELLED = const("OrderStatus", "CANCELLED")
CANCEL_ACTION = const("AuditAction", "CANCEL_ORDER")


def status_is(n: int) -> Expr:
    return eq(ref("outcome.status"), const("integer", n))


def _src(req: str) -> list[SourceRef]:
    return [SourceRef(snapshot_id="demo-spec", requirement_id=req)]


def _rule(
    id_: str, req: str, applicability: Expr, assertion: dict[str, object], scope: str
) -> Rule:
    return Rule.model_validate(
        {
            "id": id_,
            "behavior_id": "cancel_order",
            "source_refs": [s.model_dump() for s in _src(req)],
            "approval_ref": "demo-approval",
            "modality": "must",
            "claim_scope": scope,
            "applicability": applicability.model_dump(),
            "assertion": assertion,
        }
    )


def _calls(op: str, count: int, args: dict[str, Expr] | None = None) -> dict[str, object]:
    return {
        "kind": "event_requirement",
        "event_kind": "call",
        "operation_or_target": op,
        "phase": "invoked",
        "count": {"operator": "eq", "value": count},
        "arguments_equal": {k: v.model_dump() for k, v in (args or {}).items()},
    }


def _writes(target: str, count: int) -> dict[str, object]:
    return {
        "kind": "event_requirement",
        "event_kind": "write",
        "operation_or_target": target,
        "phase": "applied",
        "count": {"operator": "eq", "value": count},
    }


def _expr(e: Expr) -> dict[str, object]:
    return {"kind": "expression", "expr": e.model_dump()}


APPROVAL = ApprovalRecord(
    id="demo-approval",
    approver="tanabe-yudai",
    approved_at=datetime(2026, 9, 22, tzinfo=UTC),
    source="manual",
    spec_content_hash="sha256:demo",
    ir_content_hash="sha256:demo",
)

RULES = [
    _rule("R-CANCEL-01", "R-CANCEL-01", not_(AUTHORIZED), _expr(status_is(403)), "service_outcome"),
    _rule(
        "R-CANCEL-02",
        "R-CANCEL-02",
        and_(AUTHORIZED, NOT_PENDING),
        _expr(status_is(409)),
        "service_outcome",
    ),
    _rule(
        "R-CANCEL-03",
        "R-CANCEL-03",
        SUCCESS,
        _expr(eq(ref("after.order.status"), CANCELLED)),
        "memory_state",
    ),
    _rule(
        "R-CANCEL-04",
        "R-CANCEL-04",
        SUCCESS,
        _calls("inventory_restore_command", 1, {"items": ref("before.order.items")}),
        "command_invocation",
    ),
    _rule(
        "R-CANCEL-05",
        "R-CANCEL-05",
        SUCCESS,
        _calls(
            "audit_record_command",
            1,
            {"actor_id": ref("before.actor.id"), "action": CANCEL_ACTION},
        ),
        "command_invocation",
    ),
    _rule("R-CANCEL-06", "R-CANCEL-06", SUCCESS, _expr(status_is(204)), "http_response"),
    _rule("R-CANCEL-07a", "R-CANCEL-07", REJECTED, _writes("order.status", 0), "memory_state"),
    _rule(
        "R-CANCEL-07b",
        "R-CANCEL-07",
        REJECTED,
        _calls("inventory_restore_command", 0),
        "command_invocation",
    ),
    _rule(
        "R-CANCEL-07c",
        "R-CANCEL-07",
        REJECTED,
        _calls("audit_record_command", 0),
        "command_invocation",
    ),
]

SPEC = SpecIR(behavior_id="cancel_order", rules=RULES, approvals=[APPROVAL])

# ---------------------------------------------------------------- code IR
LIMITS = AnalysisLimits(depth=2, max_paths=64, solver_timeout_ms=500)
SNAP = SnapshotRef(snapshot_id="demo-code", commit="deadbeef")
CODE_SRC = SourceRef(snapshot_id="demo-code", symbol="examples.orders.app.service.cancel_order")


def write_status(seq: int) -> Event:
    return Event(
        sequence=seq, kind="write", phase="applied", target="order.status", value=CANCELLED
    )


def call_restore(seq: int, items: Expr | None = None) -> Event:
    return Event(
        sequence=seq,
        kind="call",
        phase="invoked",
        operation="inventory_restore_command",
        arguments={"items": items or ref("before.order.items")},
    )


def call_audit(seq: int, actor: Expr | None = None) -> Event:
    return Event(
        sequence=seq,
        kind="call",
        phase="invoked",
        operation="audit_record_command",
        arguments={"actor_id": actor or ref("before.actor.id"), "action": CANCEL_ACTION},
    )


def path(
    pid: str,
    guard: Expr,
    status: int,
    events: list[Event] | None = None,
    updates: list[StateUpdate] | None = None,
    precision: str = "exact_in_model",
) -> Path:
    return Path(
        path_id=pid,
        guard=guard,
        state_updates=updates or [],
        events=events or [],
        outcome=Outcome(kind="http_response", status=status),
        precision=precision,  # type: ignore[arg-type]
        source_refs=[CODE_SRC],
    )


def code_ir(paths: list[Path]) -> CodeIR:
    return CodeIR(
        behavior_id="cancel_order",
        entrypoint="examples.orders.app.service.cancel_order",
        snapshot=SNAP,
        analyzer_version="handwritten",
        limits=LIMITS,
        paths=paths,
        origin="handwritten",
    )


P_FORBIDDEN = path("p1_forbidden", not_(AUTHORIZED), 403)
P_CONFLICT = path("p2_conflict", and_(AUTHORIZED, NOT_PENDING), 409)
SUCCESS_EVENTS = [write_status(1), call_restore(2), call_audit(3)]
SUCCESS_UPDATES = [StateUpdate(target="order.status", value=CANCELLED)]
P_SUCCESS = path("p3_success", SUCCESS, 204, SUCCESS_EVENTS, SUCCESS_UPDATES)

CORRECT = code_ir([P_FORBIDDEN, P_CONFLICT, P_SUCCESS])

# 在庫復元を管理者経路だけで呼ぶ（PoC 4.3）
RESTORE_ADMIN_ONLY = code_ir(
    [
        P_FORBIDDEN,
        P_CONFLICT,
        path(
            "p3_admin",
            and_(IS_ADMIN, PENDING),
            204,
            SUCCESS_EVENTS,
            SUCCESS_UPDATES,
        ),
        path(
            "p4_owner",
            and_(not_(IS_ADMIN), IS_OWNER, PENDING),
            204,
            [write_status(1), call_audit(2)],
            SUCCESS_UPDATES,
        ),
    ]
)

# 409 を 400 に変える
CONFLICT_400 = code_ir(
    [P_FORBIDDEN, path("p2_conflict", and_(AUTHORIZED, NOT_PENDING), 400), P_SUCCESS]
)

# 所有者を拒否し管理者だけ許す
ADMIN_ONLY_AUTH = code_ir(
    [
        path("p1_forbidden", not_(IS_ADMIN), 403),
        path("p2_conflict", and_(IS_ADMIN, NOT_PENDING), 409),
        path("p3_success", and_(IS_ADMIN, PENDING), 204, SUCCESS_EVENTS, SUCCESS_UPDATES),
    ]
)

# 監査に実行者ではなく所有者の ID を渡す
AUDIT_OWNER_ID = code_ir(
    [
        P_FORBIDDEN,
        P_CONFLICT,
        path(
            "p3_success",
            SUCCESS,
            204,
            [write_status(1), call_restore(2), call_audit(3, ref("before.order.owner_id"))],
            SUCCESS_UPDATES,
        ),
    ]
)

# 成功時の状態を SHIPPED にする
WRONG_STATE = code_ir(
    [
        P_FORBIDDEN,
        P_CONFLICT,
        path(
            "p3_success",
            SUCCESS,
            204,
            [
                Event(
                    sequence=1,
                    kind="write",
                    phase="applied",
                    target="order.status",
                    value=const("OrderStatus", "SHIPPED"),
                ),
                call_restore(2),
                call_audit(3),
            ],
            [StateUpdate(target="order.status", value=const("OrderStatus", "SHIPPED"))],
        ),
    ]
)

# 在庫復元を 2 回呼ぶ
RESTORE_TWICE = code_ir(
    [
        P_FORBIDDEN,
        P_CONFLICT,
        path(
            "p3_success",
            SUCCESS,
            204,
            [write_status(1), call_restore(2), call_restore(3), call_audit(4)],
            SUCCESS_UPDATES,
        ),
    ]
)
