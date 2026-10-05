"""図のオートマトンの模擬（ε 閉包・ガードの枝刈り・部分列）の単体試験。"""

from __future__ import annotations

from speclino.conformance.automaton import (
    GuardOracle,
    accepting_guard_sets,
    explain_subsequence,
    initial_configs,
    normalize,
    run_word,
)
from speclino.conformance.encode import Z3Context
from speclino.conformance.typing import TypeEnv
from speclino.domain import DomainSpec
from speclino.domain.expr import Expr, const, eq, ref
from speclino.domain.uml import Automaton, AutomatonTransition, CallLabel, EpsilonLabel

DOMAIN = DomainSpec(variables={"order.express": "boolean"})
EXPRESS = eq(ref("before.order.express"), const("boolean", True))
NORMAL = eq(ref("before.order.express"), const("boolean", False))


def _t(
    id_: str, s: str, t: str, op: str | None = None, guard: Expr | None = None
) -> AutomatonTransition:
    label = CallLabel(operation=op) if op else EpsilonLabel()
    return AutomatonTransition(id=id_, source=s, target=t, label=label, guard=guard)


# s0 -reserve-> s1 -[express] ε-> s2 -fast-> s4
#                s1 -[normal]  ε-> s3 -normal-> s4 ; s4 -(loop) ε-> s0
AUTO = Automaton(
    states=["s0", "s1", "s2", "s3", "s4"],
    initial="s0",
    accepting=["s4"],
    transitions=[
        _t("m0", "s0", "s1", "Inv.reserve"),
        _t("g1", "s1", "s2", guard=EXPRESS),
        _t("g2", "s1", "s3", guard=NORMAL),
        _t("m1", "s2", "s4", "Ship.fast"),
        _t("m2", "s3", "s4", "Ship.normal"),
        _t("back", "s4", "s0"),
    ],
)


def _oracle(base_guard: Expr | None = None) -> tuple[GuardOracle, Z3Context]:
    ctx = Z3Context(TypeEnv(DOMAIN))
    a, _ = normalize(AUTO, lambda op: op, "Svc.entry")
    base = [ctx.encode_bool(base_guard)] if base_guard is not None else []
    return GuardOracle(ctx, base, a.guards, 500), ctx


def test_subsequence_run_collects_branch_guards() -> None:
    a, unbound = normalize(AUTO, lambda op: op, "Svc.entry")
    assert unbound == []
    oracle, _ = _oracle()
    ok, dead = run_word(["Inv.reserve", "Log.write", "Ship.fast"], a, oracle, skip=True)
    assert dead is None
    assert ok == {frozenset({"g1"})}


def test_guard_pruning_with_path_condition() -> None:
    a, _ = normalize(AUTO, lambda op: op, "Svc.entry")
    oracle, _ = _oracle(NORMAL)
    # 経路条件 normal の下では express の枝に入れない
    assert accepting_guard_sets(a, oracle) == {frozenset({"g2"})}


def test_exact_mode_dies_on_unknown_call() -> None:
    a, _ = normalize(AUTO, lambda op: op, "Svc.entry")
    oracle, _ = _oracle()
    ok, dead = run_word(["Inv.reserve", "Log.write"], a, oracle, skip=False)
    assert (ok, dead) == (set(), 1)


def test_loop_terminates_and_accepts_repetition() -> None:
    a, _ = normalize(AUTO, lambda op: op, "Svc.entry")
    oracle, _ = _oracle(EXPRESS)
    word = ["Inv.reserve", "Ship.fast"] * 3
    ok, _ = run_word(word, a, oracle, skip=False)
    assert ok == {frozenset({"g1"})}


def test_unbound_and_entry_labels() -> None:
    auto = Automaton(
        states=["a", "b", "c"],
        initial="a",
        accepting=["c"],
        transitions=[_t("e", "a", "b", "Svc.entry"), _t("x", "b", "c", "Nope.op")],
    )
    a, unbound = normalize(auto, lambda op: None if op.startswith("Nope") else op, "Svc.entry")
    assert unbound == ["Nope.op"]
    assert a.edges[0].op is None  # 入口そのものは ε


def test_explain_reports_last_match_and_expected_call() -> None:
    a, _ = normalize(AUTO, lambda op: op, "Svc.entry")
    last, expected = explain_subsequence(["Inv.reserve"], a, {"g2"})
    assert last == "Inv.reserve"
    assert expected == ["Ship.normal"]


# S63 の parser は中身が opt だけの loop から ε だけで一周する閉路を作る（o1.skip → l1.back）。
# head -[o1.enter, express] ε-> body -Ship.fast-> join ; head -[o1.skip] ε-> join ;
# join -[l1.back] ε-> head ; join -[l1.again, express] ε-> head ; head -[l1.exit] ε-> done
EPS_LOOP = Automaton(
    states=["head", "body", "join", "done"],
    initial="head",
    accepting=["done"],
    transitions=[
        _t("o1.enter", "head", "body", guard=EXPRESS),
        _t("m1", "body", "join", "Ship.fast"),
        _t("o1.skip", "head", "join"),
        _t("l1.back", "join", "head"),
        _t("l1.again", "join", "head", guard=EXPRESS),
        _t("l1.exit", "head", "done"),
    ],
)


def test_epsilon_cycle_closure_terminates_with_bounded_guards() -> None:
    a, _ = normalize(EPS_LOOP, lambda op: op, "Svc.entry")
    ctx = Z3Context(TypeEnv(DOMAIN))
    oracle = GuardOracle(ctx, [], a.guards, 500)
    configs = initial_configs(a, oracle)
    # ガードつき ε の閉路を何周しても条件集合は増え続けない（同じ状態・同じ集合で打ち切る）
    assert {c.guards for c in configs} <= {
        frozenset(),
        frozenset({"o1.enter"}),
        frozenset({"l1.again"}),
        frozenset({"o1.enter", "l1.again"}),
    }
    assert len(configs) <= 4 * 4
    ok, dead = run_word(["Ship.fast"] * 4, a, oracle, skip=False)
    assert dead is None and ok
    assert all("o1.enter" in g for g in ok)
    assert accepting_guard_sets(a, oracle)  # ε だけで受理に達する（0 回）
