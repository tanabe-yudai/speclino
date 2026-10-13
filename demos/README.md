# 旧デモの題材（`examples/orders`、旧形式の Spec IR）

> **今の本番のデモ PR はこれではない。** 提出のデモは、このリポジトリ自身の UML の仕様（俯瞰の仕様 7 本）に対する
> 3 本の PR で、中身は各 PR の説明が正本:
> [#47](https://github.com/tanabe-yudai/spec-lino/pull/47) デモ1（テストは緑でもゲートの図に違反・反例）、
> [#45](https://github.com/tanabe-yudai/spec-lino/pull/45) デモ3（仕様の写しの改変を lock の照合で止める）、
> [#46](https://github.com/tanabe-yudai/spec-lino/pull/46) デモ4（解析できないループで UNKNOWN → ゲート失敗）。
> 見せ方は `docs/submission/form.md` と `docs/submission/video-script.md`。

ここに残すのは、旧形式の題材 `examples/orders`（注文キャンセル、要求 9 件）に変異を入れて、**期待どおりの判定に
なることを固定する**ためのソース。CLI の旧形式の流れ（リポジトリの `specs/ir`）の検出能力のテストとして、
`tests/unit/test_demos.py` などが CI で毎回検査する。

| デモ | 変異 | 期待する結果 |
| --- | --- | --- |
| ① 意味差分 | [`demo1_admin_only.py`](cancel_order/demo1_admin_only.py): 在庫復元を管理者経路だけに移す | `R-CANCEL-04` が **VIOLATED**（missing_effect）。反例は「非管理者・所有者・PENDING」。base と比べると **新規違反 1** |
| ② エージェント調査 | [`demo2_deep_helpers.py`](cancel_order/demo2_deep_helpers.py): 在庫復元をヘルパー 3 段の奥へ移し、管理者条件を付ける | 既定の深さ上限 2 では **UNKNOWN**（`limit_depth`）。エージェントが `analyze_focus(depth=4)` を選ぶと **VIOLATED** に確定する |
| ③ 自己検証 | `ToolGateway.invoke` で `registry.execute` を権限判定の前へ移す（`tests/unit/test_self_verification.py`） | `R-TOOL-01` が **VIOLATED**（forbidden_effect） |
| ④ ガバナンス | Spec IR から `R-CANCEL-04` を削除して通そうとする（`tests/integration/services/test_api_flow.py`） | **REVIEW**（stale_approval）でゲート失敗。`/speclino approve-spec removed:<hash>` の後に通過 |
| ⑤ フェイルクローズ | [`demo5_loop.py`](cancel_order/demo5_loop.py): 在庫復元を `for` ループで呼ぶ | `R-CANCEL-04` が **UNKNOWN**、ゲート **失敗**。構造解析により他の 8 要求は VERIFIED のまま（巻き込まない） |

以前はこれらを `examples/orders/app/service.py` に適用したブランチを本番のデモ PR にしていたが、
知識レジストリ（仕様の正本を SpecLino の bundle に置く）への切り替えに合わせて、上の 3 本に置き換えた。
