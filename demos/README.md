# デモ PR の題材（S27）

本番の GitHub でデモ PR を作る前に、**同じ変異をローカルで検査して期待どおりの判定になることを
固定する**ためのソース。`tests/unit/test_demos.py` が CI で毎回検査するので、実装を変えて
デモが壊れれば CI で気付ける。

| デモ | 変異 | 期待する結果 |
| --- | --- | --- |
| ① 意味差分 | [`demo1_admin_only.py`](cancel_order/demo1_admin_only.py): 在庫復元を管理者経路だけに移す | `R-CANCEL-04` が **VIOLATED**（missing_effect）。反例は「非管理者・所有者・PENDING」。base と比べると **新規違反 1** |
| ② エージェント調査 | [`demo2_deep_helpers.py`](cancel_order/demo2_deep_helpers.py): 在庫復元をヘルパー 3 段の奥へ移し、管理者条件を付ける | 既定の深さ上限 2 では **UNKNOWN**（`limit_depth`）。エージェントが `analyze_focus(depth=4)` を選ぶと **VIOLATED** に確定する |
| ③ 自己検証 | `ToolGateway.invoke` で `registry.execute` を権限判定の前へ移す（`tests/unit/test_self_verification.py`） | `R-TOOL-01` が **VIOLATED**（forbidden_effect） |
| ④ ガバナンス | Spec IR から `R-CANCEL-04` を削除して通そうとする（`tests/integration/services/test_api_flow.py`） | **REVIEW**（stale_approval）でゲート失敗。`/speclino approve-spec removed:<hash>` の後に通過 |
| ⑤ フェイルクローズ | [`demo5_loop.py`](cancel_order/demo5_loop.py): 在庫復元を `for` ループで呼ぶ | `R-CANCEL-04` が **UNKNOWN**、ゲート **失敗**。構造解析により他の 8 要求は VERIFIED のまま（巻き込まない） |

本番のデモ PR は、これらを `examples/orders/app/service.py` に適用したブランチとして作り、
12/1 まで open のまま維持する。
