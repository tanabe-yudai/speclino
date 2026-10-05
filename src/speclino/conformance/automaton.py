"""図のオートマトンと経路の積（設計 5.1）。ガードは経路条件と合わせて Z3 で充足可能性を確かめる。

構成は (状態, 通ったガードつき辺の集合)。ガードの集合は有限なので、ε 閉包は
有限集合の不動点として停止し、
`loop` の戻り辺があっても呼出しを消費する遷移は経路の長さで抑えられる。

図の規模の制約: 構成の数はガードつきの枝（opt / alt）の組合せで指数的に増え得る（独立したガードつき
opt が十数個並ぶと上限 MAX_CONFIGS に達する）。上限を超えたら判定せず UNKNOWN にする。
LLM・adapters に依存しない。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import z3

from speclino.conformance.encode import Z3Context
from speclino.domain.expr import Expr
from speclino.domain.uml import Automaton, CallLabel

# 構成の数の上限。超えたら判定せず UNKNOWN にする（ガードの組合せ爆発への備え）
MAX_CONFIGS = 4096

Guards = frozenset[str]


class SolverLimit(Exception):
    """ソルバーが unknown / timeout を返した。違反なしに読み替えず UNKNOWN にする。"""


class ConfigLimit(Exception):
    """構成の数が上限を超えた。"""


@dataclass(frozen=True)
class Edge:
    id: str
    source: str
    target: str
    op: str | None  # None は ε
    guard: Expr | None


@dataclass(frozen=True)
class Config:
    state: str
    guards: Guards


@dataclass
class NormalizedAutomaton:
    initial: str
    accepting: frozenset[str]
    edges: list[Edge]
    by_source: dict[str, list[Edge]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for e in self.edges:
            self.by_source.setdefault(e.source, []).append(e)

    @property
    def operations(self) -> set[str]:
        return {e.op for e in self.edges if e.op is not None}

    @property
    def guards(self) -> dict[str, Expr]:
        return {e.id: e.guard for e in self.edges if e.guard is not None}


def normalize(
    automaton: Automaton,
    resolve_operation: Callable[[str], str | None],
    entry_operation: str,
    known_operations: frozenset[str] | None = None,
) -> tuple[NormalizedAutomaton, list[str]]:
    """呼出しラベルを Code IR の操作 ID に写す。戻り値の 2 つ目は対応が見つからない操作。

    初期状態を出る入口の操作そのものへの呼出し辺は経路の開始なので ε として扱う（図の途中の
    再帰呼出しは要求として残す）。known_operations を渡すと、写した先がその集合に無い操作も
    対応なしとみなす（名前の取り違えで「呼んでいない」を VIOLATED にしない）。
    """
    edges: list[Edge] = []
    unbound: list[str] = []
    for t in automaton.transitions:
        op: str | None = None
        is_entry = (
            isinstance(t.label, CallLabel)
            and t.label.operation == entry_operation
            and t.source == automaton.initial
        )
        if isinstance(t.label, CallLabel) and not is_entry:
            op = resolve_operation(t.label.operation)
            if op is None or (known_operations is not None and op not in known_operations):
                unbound.append(t.label.operation)
        edges.append(Edge(t.id, t.source, t.target, op, t.guard))
    return (
        NormalizedAutomaton(automaton.initial, frozenset(automaton.accepting), edges),
        sorted(set(unbound)),
    )


class GuardOracle:
    """ガード集合の充足可能性（経路条件と合わせて）。結果はキャッシュする。"""

    def __init__(
        self, ctx: Z3Context, base: list[Any], guards: dict[str, Expr], timeout_ms: int
    ) -> None:
        self.ctx = ctx
        self.base = base
        self.timeout_ms = timeout_ms
        # 型が合わない・未定義の変数は呼出し側で UNKNOWN にする（IRTypeError / ValueError）
        self.encoded = {gid: ctx.encode_bool(g) for gid, g in guards.items()}
        self._cache: dict[Guards, bool] = {}

    def formula(self, gs: Guards) -> Any:
        return z3.And(self.ctx.true(), *(self.encoded[g] for g in sorted(gs)))

    def feasible(self, gs: Guards) -> bool:
        if gs in self._cache:
            return self._cache[gs]
        s = self.ctx.solver(self.timeout_ms)
        s.add(*self.base, self.formula(gs))
        r = s.check()
        if r == z3.unknown:
            raise SolverLimit("ガードの充足可能性")
        self._cache[gs] = r == z3.sat
        return self._cache[gs]


def _extend(c: Config, e: Edge, oracle: GuardOracle) -> Config | None:
    gs = c.guards | {e.id} if e.guard is not None else c.guards
    if gs != c.guards and not oracle.feasible(gs):
        return None
    return Config(e.target, gs)


def _check_size(configs: set[Config]) -> None:
    if len(configs) > MAX_CONFIGS:
        raise ConfigLimit(f"構成の数が上限 {MAX_CONFIGS} を超えた")


def closure(
    configs: Iterable[Config], a: NormalizedAutomaton, oracle: GuardOracle
) -> frozenset[Config]:
    """ε 辺で到達できる構成を足す（ガードが両立しない枝は捨てる）。"""
    seen = set(configs)
    stack = list(seen)
    while stack:
        c = stack.pop()
        for e in a.by_source.get(c.state, []):
            if e.op is not None:
                continue
            n = _extend(c, e, oracle)
            if n is not None and n not in seen:
                seen.add(n)
                stack.append(n)
                _check_size(seen)
    return frozenset(seen)


def initial_configs(a: NormalizedAutomaton, oracle: GuardOracle) -> frozenset[Config]:
    return closure([Config(a.initial, frozenset())], a, oracle)


def step(
    configs: frozenset[Config],
    op: str,
    a: NormalizedAutomaton,
    oracle: GuardOracle,
    *,
    skip: bool,
) -> frozenset[Config]:
    """呼出し op を 1 つ読む。skip=True なら読み飛ばし（部分列の判定、open-world）も残す。"""
    moved: set[Config] = set(configs) if skip else set()
    for c in configs:
        for e in a.by_source.get(c.state, []):
            if e.op != op:
                continue
            n = _extend(c, e, oracle)
            if n is not None:
                moved.add(n)
    _check_size(moved)
    return closure(moved, a, oracle)


def run_word(
    word: list[str], a: NormalizedAutomaton, oracle: GuardOracle, *, skip: bool
) -> tuple[set[Guards], int | None]:
    """呼出し列を読み、受理に達したガード集合と、構成が尽きた位置（尽きなければ None）を返す。"""
    configs = initial_configs(a, oracle)
    for i, op in enumerate(word):
        configs = step(configs, op, a, oracle, skip=skip)
        if not configs:
            return set(), i
    return {c.guards for c in configs if c.state in a.accepting}, None


def accepting_guard_sets(a: NormalizedAutomaton, oracle: GuardOracle) -> set[Guards]:
    """呼出しを自由に読んで受理に達するガード集合（図が何かを要求する入力の範囲）。"""
    seen = set(initial_configs(a, oracle))
    stack = list(seen)
    while stack:
        c = stack.pop()
        for e in a.by_source.get(c.state, []):
            n = _extend(c, e, oracle)
            if n is not None and n not in seen:
                seen.add(n)
                stack.append(n)
                _check_size(seen)
    return {c.guards for c in seen if c.state in a.accepting}


def disjunction(oracle: GuardOracle, sets: Iterable[Guards]) -> Any:
    parts = [oracle.formula(gs) for gs in sets]
    return z3.Or(*parts) if parts else z3.BoolVal(False, oracle.ctx.z3ctx)


def explain_subsequence(
    word: list[str], a: NormalizedAutomaton, true_guards: set[str]
) -> tuple[str | None, list[str]]:
    """反例の説明: モデルで成り立つガードの辺だけで部分列を読み、最も進んだ構成で最後に一致した
    呼出しと、その先に図が期待する呼出しを返す。"""
    Node = tuple[str, str | None, int]  # (状態, 最後に一致した呼出し, 一致した数)

    def allowed(e: Edge) -> bool:
        return e.guard is None or e.id in true_guards

    def eps(nodes: set[Node]) -> set[Node]:
        seen = set(nodes)
        stack = list(seen)
        while stack:
            s, last, n = stack.pop()
            for e in a.by_source.get(s, []):
                if e.op is None and allowed(e) and (e.target, last, n) not in seen:
                    seen.add((e.target, last, n))
                    stack.append((e.target, last, n))
        return seen

    cur = eps({(a.initial, None, 0)})
    for op in word:
        nxt = set(cur)
        for s, _, n in cur:
            for e in a.by_source.get(s, []):
                if e.op == op and allowed(e):
                    nxt.add((e.target, op, n + 1))
        cur = eps(nxt)
    best = max(n for _, _, n in cur)
    front = [(s, last) for s, last, n in cur if n == best]
    last = min((x for _, x in front if x is not None), default=None)
    expected = sorted(
        {e.op for s, _ in front for e in a.by_source.get(s, []) if e.op is not None and allowed(e)}
    )
    return last, expected
