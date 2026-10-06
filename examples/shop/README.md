# examples/shop

チームの知識レジストリ（仕様・skill・指示・LLM 支援の規約）を通しで見せるための題材。
仕様は Mermaid の UML（`src/speclino/spec/uml/grammar.md` の部分集合）で書く。手順は
`docs/demo-knowledge-registry.md`。

- `spec.md`: 要求の表、クラス図（語彙）、シーケンス図（注文の確定）、状態遷移図（`order.status`）、
  閉じたアクティビティ図（注文の取消）
- `app/`: 仕様に適合する実装（`OrderService` が注入された `Inventory` / `Payment` / `Mailer` を呼ぶ）
- `.speclino/bindings.yaml`: 図の名前（`<クラス>.<メソッド>`）とコードの対応。`domain.yaml`: 型
- `mutations/*.patch`: main をわざと壊す差分。1 つの変異が 1 つの判定だけを変える

| 変異 | 変えること | 期待する判定 |
| --- | --- | --- |
| `01-missing-charge` | 決済を呼ばない | `place#…` が VIOLATED（反例つき） |
| `02-ship-from-pending` | 未決済でも出荷する | `status`（ship_order）が VIOLATED（PENDING → SHIPPED） |
| `03-refund-on-cancel` | 取消で返金も呼ぶ | `cancel#…` が VIOLATED（閉じた図に無い呼出し） |
| `04-notify-between` | 確保と決済の間に通知を挟む | 変化なし（シーケンス図は open-world） |
| `05-dynamic-charge` | `getattr` で決済を呼ぶ | `place#…` と `status`（place_order）が UNKNOWN |
| `06-rebind-charge` | PR で bindings の決済を通知に向ける | `KNOWLEDGE-BINDINGS-001` が REVIEW（判定は main の bindings） |
| `07-terse-error` | 例外のメッセージを短くする | LLM 支援の規約 `SHOP-ERR-001` が REVIEW（issue）。確定は人 |

- `materialize.py`: 検査対象のリポジトリの形（根に `.speclino/`、コードは `examples/shop/app/`）に書き出す
- `seed.py`: ローカルの DB に、ワークスペース・プロジェクト・承認済みの知識・規約・検査の結果を作る
  （`scripts/seed_demo.py` から使う。LLM は呼ばない）

テスト: `tests/unit/examples/test_shop.py`（DB なし）、`tests/integration/services/test_shop_bundle.py`（DB あり）。
