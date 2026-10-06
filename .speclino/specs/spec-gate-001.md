---
id: SPEC-GATE-001
version: "1.0"
owner: tanabe-yudai
---

# ゲートのフェイルクローズ（判定モデル / AC-003）

ゲート判定（`conformance.gate.decide_gate`）は、検証集合が空でも必須ルールが無くても通過を返さない。必須ルールの非成立が残れば失敗、残らなければ通過（免除があれば例外付き通過）。SpecLino はこの仕様を自分自身のコードに対して検証する（自己検証）。

## 前提

- `has_findings` は仕分け結果 `Triage.has_findings`（Finding が 1 件以上ある）。
- `has_mandatory` は `Triage.has_mandatory`（必須ルールの Finding が 1 件以上ある。levels に無い要求は必須とみなす）。
- `has_blocking` は `Triage.has_blocking`（fail_on に該当し免除されていない必須ルールの Finding がある）。
- `has_waived` は `Triage.has_waived`（有効な例外で免除された VIOLATED がある）。
- 出口は `gate_fail`（`_fail`）、`gate_pass`（`_pass`）、`gate_pass_waived`（`_pass_with_exceptions`）の 3 種で、いずれも `GateResult` を作る契約済みの操作。

## 要求

### R-GATE-01

- 条件: `has_findings` が偽、または `has_mandatory` が偽
- 期待する結果: `gate_fail` を 1 回呼ぶ（空の検証集合を通過として扱わない）

### R-GATE-01b

- 条件: `has_findings` が偽、または `has_mandatory` が偽
- 期待する結果: `gate_pass` を呼ばない

### R-GATE-01c

- 条件: `has_findings` が偽、または `has_mandatory` が偽
- 期待する結果: `gate_pass_waived` を呼ばない

### R-GATE-02

- 条件: `has_findings` と `has_mandatory` が真、かつ `has_blocking` が真
- 期待する結果: `gate_fail` を 1 回呼ぶ

### R-GATE-02b

- 条件: `has_findings` と `has_mandatory` が真、かつ `has_blocking` が真
- 期待する結果: `gate_pass` を呼ばない

### R-GATE-02c

- 条件: `has_findings` と `has_mandatory` が真、かつ `has_blocking` が真
- 期待する結果: `gate_pass_waived` を呼ばない

### R-GATE-03

- 条件: `has_findings` と `has_mandatory` が真、かつ `has_blocking` が偽
- 期待する結果: `gate_fail` を呼ばない

### R-GATE-03b

- 条件: `has_findings` と `has_mandatory` が真、`has_blocking` が偽、かつ `has_waived` が偽
- 期待する結果: `gate_pass` を 1 回呼ぶ

### R-GATE-03c

- 条件: `has_findings` と `has_mandatory` が真、`has_blocking` が偽、かつ `has_waived` が真
- 期待する結果: `gate_pass_waived` を 1 回呼ぶ

## 観測境界

- 検証できるのは `decide_gate` 内の出口（操作）の呼出し有無と回数。
- 仕分け（`triage`）が fail_on・level・免除を正しく判定するかは対象外（単体テストで確認する）。
