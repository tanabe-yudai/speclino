---
id: SPEC-TOOL-001
version: "1.0"
owner: tanabe-yudai
---

# ツール実行の権限検査（FR 014 / AC-012）

エージェントのツール呼出しはすべて `ToolGateway.invoke` を経由する。許可されていない要求は実行前に拒否し、監査記録を残す。SpecLino はこの仕様を自分自身のコードに対して検証する（自己検証）。

## 前提

- `call.tool` は要求されたツール名（同一性だけを比較する）。
- `permitted` は `AgentPermissions.allows(call.tool)` の結果。権限はサーバ側設定だけから作られ、リポジトリ内の文書では変更できない。
- `budget_exhausted` は `BudgetState.is_exhausted()` の結果（回数・経過時間の上限到達）。
- ツール本体（`registry.execute`）と監査（`audit.record_*`）は契約済みの操作であり、その内部は観測境界の外。

## 要求

### R-TOOL-01

- 条件: `permitted` が偽
- 期待する結果: `tool_execute`（`registry.execute`）を呼ばない

### R-TOOL-01b

- 条件: `permitted` が偽
- 期待する結果: `audit_denied`（`audit.record_denied`）を 1 回呼ぶ

### R-TOOL-01c

- 条件: `permitted` が偽
- 期待する結果: `deny_result`（`ToolResult.denied`）で拒否結果を返す

### R-TOOL-02

- 条件: `permitted` が真、かつ `budget_exhausted` が偽
- 期待する結果: `tool_execute` を 1 回呼ぶ

### R-TOOL-02b

- 条件: `permitted` が真、かつ `budget_exhausted` が偽
- 期待する結果: `audit_allowed` を 1 回呼び、`audit_denied` は呼ばない

### R-TOOL-02d

- 条件: `permitted` が真、かつ `budget_exhausted` が偽
- 期待する結果: `budget_charge`（予算の消費）を 1 回呼ぶ

### R-TOOL-03

- 条件: `permitted` が真、かつ `budget_exhausted` が真
- 期待する結果: `tool_execute` を呼ばず、`audit_denied` を 1 回呼ぶ

## 観測境界

- 検証できるのは `invoke` 内の呼出しイベントの有無・回数と戻り値の種別。
- **イベントの順序は対象外**。Code IR は `Event.sequence` で順序を持っているが、Spec IR に順序を書く手段が
  まだ無い。したがって R-TOOL-02d は「予算の消費が 1 回ある」ことだけを言い、それが実行より先かは言わない
  （実行が例外で中断したとき予算が消費されないという穴は、この仕様では塞いでいない）。
- 監査記録が実際に永続化されるか、ツール実装が正しいかは対象外。
