# S64: 仕様の検査（構造・順序・遷移・包含） 実装計画

> 設計: `design/2026-10-05-team-knowledge-registry.md` 1 章の不変条件、5.1、5.2。PoC 10.5（open-world）。
> 契約: `src/speclino/domain/uml.py`（Task 0）。型は変えない。

**Goal:** `CompiledSpec` の義務（順序・包含・遷移・構造）を Code IR / ソースの AST と突き合わせ、4 値の Finding を出す。
欠けた Finding は UNKNOWN（`missing_finding`）で補う。S65 が配線する関数だけを提供する。

**Architecture:** オートマトンの模擬は `conformance/automaton.py`（ガードの Z3 判定と ε 閉包つきの集合模擬）。
`trace_check.py`（順序・包含）と `state_check.py`（遷移）は既存の `ConformanceEngine` の Z3 文脈・入口仮定・終了状態の
符号化を再利用する。構造は `analysis/structure_check.py`（AST のみ、import しない）。`expected.py` は期待 ID と欠落の補完。

## 判定の規則（不変条件）

- VIOLATED / VERIFIED_IN_SCOPE は `precision == "exact_in_model"` の経路で、関係する操作のイベントが `must` のときだけ。
  経路の `unknown_causes`・Code IR 全体の `unknown_causes` が関係する観測（`call:<op>` / `write:<attr>`、`may_affect=None` は全部）
  に触れる、関係する操作に `may` イベントがある → その経路は UNKNOWN
- 経路が無い、どの経路のガードも図のガードと両立しない、図のガードを満たす入力の一部がどの経路にも入らない（経路上限の超過を含む）→ UNKNOWN
- ソルバーの unknown / timeout → UNKNOWN（`solver_limit`）。型を解決できないガード（domain に無い変数）→ UNKNOWN
- 集約は既存のエンジンと同じ（VIOLATED > REVIEW > UNKNOWN > VERIFIED）。義務 1 つにつき Finding 1 つ

### 順序（シーケンス図、`contains_subsequence`）

経路 P の呼出し列 w と、図のオートマトンの積を集合で模擬する。構成は (状態, 通ったガードの集合)。
各イベントで「読み飛ばす（図に無い呼出し、open-world）」と「一致する呼出し辺で進む」の両方を残し、各段で ε 閉包を取る。
ガードつきの辺を通るたびにガード集合を足し、`入口仮定 ∧ 経路条件 ∧ 終了状態 ∧ ガード集合` が UNSAT の構成は捨てる。

- `Cov` = 呼出しを自由に読める模擬で受理に達するガード集合の論理和（図が何かを要求する入力）
- `OK` = w を模擬して受理に達したガード集合の論理和
- `Cov` が経路条件と両立しない → その経路は対象外（スキップ）
- `経路条件 ∧ Cov ∧ ¬OK` が SAT → VIOLATED。反例はそのモデル（`before.*`）と「Y の後に X を期待したが呼ばない」
- UNSAT → 成立。`loop` の戻り辺は、呼出しを消費する遷移が経路長で抑えられ、ε 閉包は有限集合の不動点なので停止する

### 包含（アクティビティ図、`included_in`）

読み飛ばしを許さない模擬（経路の全呼出しが図のどれかの道筋で消費され、受理に達する）。外れたら
`closed: true` → VIOLATED（反例つき）、それ以外 → REVIEW（図に無い・図と違う処理。open-world）。

### 遷移（状態遷移図）

経路上の `write`（宣言した属性への代入、無ければ `state_updates`）を順に見る。前の値は 1 つ目が `before.<attr>`、
以降は直前の代入値。`経路条件 ∧ ¬(∨ 許された遷移 (前 == s ∧ 新 == t ∧ ガード))` が SAT → VIOLATED（反例つき）。
同じ値の再代入は遷移ではないので許す。属性の型が列挙でない・状態名が列挙に無い → UNKNOWN。

### 構造（クラス図）

`source_roots` の `.py` を AST で読み、クラス名が一意に見つかるか、各操作がメソッドとして存在し引数名（self / cls を除く）と
数が順に一致するか。構文エラーのファイルがある、クラスが複数ある、基底がソースに無い、`__getattr__`・クラス属性への代入・
クラスデコレータで動的に定義され得る、`*args` / `**kwargs` → 「無い／合わない」を確定できないので UNKNOWN。

## 判断の記録

- **型環境**: ガードの型には `DomainSpec` が要る。`check_trace` / `check_state_machine` に keyword 引数 `domain` を足す
  （既定は空の domain → 変数を参照するガードは UNKNOWN。偽の判定は出ない）。S65 は既存エンジンと同じ domain を渡す
- **操作 ID の対応**: keyword 引数 `resolve_operation`（図の `<Class>.<method>` → Code IR の操作 ID、None は未対応 → UNKNOWN）。既定は恒等
- **入口の呼出し**: `CallLabel.operation == entry_operation` の辺は経路の開始そのものなので ε として扱う
- **behavior_id**: 構造の Finding は Code IR を持たないので keyword 引数 `behavior_id`（既定は義務 ID の図 ID 部分）
- `obligation_levels(compiled)` を `expected.py` に置く（S65 のゲートが level を使う）

## Tasks

1. **automaton.py**: 正規化した辺、ガード判定（キャッシュ・timeout は例外）、ε 閉包、読み飛ばしあり／なしの 1 歩、`Cov` の列挙。単体試験
2. **trace_check.py（順序）**: 受け入れ `test_subsequence_satisfied_is_verified` / `test_missing_call_is_violated_with_counterexample` /
   `test_extra_call_is_not_violation` / `test_guard_unsat_path_skipped` / `test_inexact_path_is_unknown` / `test_unbound_element_is_unknown`
3. **trace_check.py（包含）**: `test_activity_closed_extra_call_violated` / `test_activity_open_extra_call_review`
4. **state_check.py**: `test_illegal_state_transition_violated` ほか
5. **structure_check.py**: 存在・引数・構文エラー・動的定義
6. **expected.py**: `expected_ids` / `fill_missing` / `obligation_levels`。`test_missing_expected_is_unknown`
7. `make lint` / `make typecheck` / `make test`

テストは `tests/unit/conformance/` と `tests/unit/analysis/`（既存の配置に合わせる）。
