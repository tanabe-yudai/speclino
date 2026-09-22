# examples/orders

design/2026-09-22-semantic-conformance-poc-v0.1.md 4.1 の「注文キャンセル」をそのまま実装した
デモ題材。SpecLino の意味検証コア（Spec IR / bindings / 検査器）の Golden fixture として使う。

- `app/`: `cancel_order` の正しい実装（PoC 3.2 の対応構文のみで書いたサービス層）
- `specs/orders-cancel.md`: 要求 R-CANCEL-01〜07、前提、状態ドメイン、観測境界
- `specs/ir/cancel_order.spec-ir.json`: 上記仕様を承認済み Spec IR（`SpecIR`）へ変換したもの
- `.speclino/domain.yaml`: 状態の列挙値・変数の型（`DomainSpec`）
- `.speclino/bindings.yaml`: 業務語彙とコードシンボルの対応（`Bindings`）
- `.speclino/contracts/`: 在庫復元・監査記録コマンドの外部契約（`OperationContract`）

承認記録（`demo-approval`）とハッシュはデモ用であり、実際の承認プロセスを経たものではない。
