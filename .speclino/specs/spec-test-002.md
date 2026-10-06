---
id: SPEC-TEST-002
version: "1.0"
owner: tanabe-yudai
---

# 宣言済みテストの観測は判定を上書きしない（エスカレーション③ / 動的証拠の信頼境界）

`test_run`（ハッカソン計画のエスカレーション③）が宣言済みテストを隔離実行した結果は、失敗だけが
判定に反映される。成功・error・skipped・timeout は observed 証拠を別軸に記録するだけで、適合の
判定を VIOLATED へ書き換えない（head の実装が同じプロセスで動くテストの「成功」は偽造しうるため、
信じない）。この判定は `agent.tools.test_run.decide_observation` が決める。SpecLino はこの仕様を
自分自身のコードに対して検証する（自己検証）。

## 前提

- `test.has_failed` は宣言済みテストのうち failed が1件以上あるか（`classify_statuses` が
  `TestStatuses.has_failed` に仕分けた結果）。
- `test.inconclusive` は宣言済みテストのうち missing / error / skipped が1件以上あるか
  （`classify_statuses` が `TestStatuses.inconclusive` に仕分けた結果）。
- `test.timed_out` はテスト実行全体がタイムアウトしたか（`SandboxResult.timed_out`）。
- 出口は `test_violated`（`_violated`）、`test_inconclusive`（`_inconclusive`）、
  `test_observed`（`_observed`）の3種で、いずれも `Decision` を作る契約済みの操作。

## 要求

### R-TEST-01

- 条件: `test.timed_out` と `test.inconclusive` が偽、かつ `test.has_failed` が真
- 期待する結果: `test_violated` を 1 回呼ぶ（宣言済みテストの失敗を VIOLATED として反映する）

### R-TEST-02

- 条件: `test.timed_out` と `test.inconclusive` と `test.has_failed` がすべて偽
  （宣言済みテストは全て成功した）
- 期待する結果: `test_observed` を 1 回呼ぶ（判定は変えず、観測を別軸に記録するだけ）

### R-TEST-03

- 条件: `test.timed_out` または `test.inconclusive` が真
- 期待する結果: `test_inconclusive` を 1 回呼ぶ（`test.has_failed` の値に関わらず。実行できな
  かった証拠は判定に使わない）

## 観測境界

- 検証できるのは `decide_observation` 内の出口（操作）の呼出し有無と回数。`test_violated` 以外
  の出口（`test_observed` / `test_inconclusive`）を呼ぶことは「判定を変えない」ことを意味する。
- 各テストの実行結果を `has_failed` / `inconclusive` に仕分ける処理（`classify_statuses`。
  ループを使うので対応構文の対象外）と、テストの隔離実行そのもの（サンドボックス）は対象外。
- 呼出し結果が Finding へどう反映されるか（message・category・counterexample など）の正しさは
  対象外。
