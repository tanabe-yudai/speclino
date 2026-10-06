---
id: SPEC-RUN-001
version: "1.0"
owner: tanabe-yudai
---

# 古い実行の結果を公開しない（AC-011 / FR 017）

完了した run の結果を PR の現在の結果として採用してよいかは `services.run_freshness.should_publish` が決める。push で置き換えられた run と、同じ PR の最新でない run は公開しない。SpecLino はこの仕様を自分自身のコードに対して検証する（自己検証）。

## 前提

- `exec_state` は run の実行状態（queued / running / awaiting_answer / completed / failed / cancelled / superseded）。
- `is_latest` は `_is_latest(run_id, latest_run_id)` の結果（最新 run が不明なら最新とみなす）。
- 出口は `publish`（`_publish`）、`mark_superseded`（`_mark_superseded`）、`mark_not_latest`（`_mark_not_latest`）の 3 種で、いずれも `Freshness` を作る契約済みの操作。

## 要求

### R-RUN-01

- 条件: `exec_state` が superseded
- 期待する結果: `publish` を呼ばない

### R-RUN-01b

- 条件: `exec_state` が superseded
- 期待する結果: `mark_superseded` を 1 回呼ぶ

### R-RUN-02

- 条件: `exec_state` が superseded でなく、`is_latest` が偽
- 期待する結果: `publish` を呼ばない

### R-RUN-02b

- 条件: `exec_state` が superseded でなく、`is_latest` が偽
- 期待する結果: `mark_not_latest` を 1 回呼ぶ

### R-RUN-03

- 条件: `exec_state` が superseded でなく、`is_latest` が真
- 期待する結果: `publish` を 1 回呼ぶ

## 観測境界

- 検証できるのは `should_publish` 内の出口（操作）の呼出し有無と回数。
- 最新 run の特定（DB 問合せ）と Check Run の更新そのものは対象外。
