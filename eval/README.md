# 判定品質の評価

エンジンが「どれだけ正しく判定できるか」を測る題材。`make eval` が回す。

## golden/ — 答えを先に決めてある 27 ケース

`golden/cancel_order/<番号>_<slug>/` に、変異させた実装（`app/service.py`）と
**期待する判定**（`case.yaml` の `expected`）が入っている。

```yaml
# 例: 07_restore_admin_only/case.yaml
expected:
  R-CANCEL-04:
    verdict: VIOLATED
    category: missing_effect
  R-CANCEL-03:
    verdict: VERIFIED_IN_SCOPE   # 巻き添えで未検証にしない
    category: conformant
```

要求 ID ごとに verdict と category を固定するので、**違反を見つけられたか**だけでなく
**関係ない要求まで巻き込んでいないか**も測れる。`counterexample_must_include` を書けば
反例の中身（どの条件で破れるか）まで確かめる。

### 2 通りの入力で回す

| モード | 入力 | 何を検証するか |
| --- | --- | --- |
| `handwritten` | 人が書いた `expected-code-ir.json` | **検査器**（Z3）だけを切り離して |
| `source` | `app/service.py` を解析器に通した結果 | **解析器 + 検査器**を通しで |

両方で同じ判定になることが、片方の誤りをもう片方が打ち消していない証拠になる。

### 見る指標

- **誤った VERIFIED = 0**（CI で強制）。解析できなかったものを「適合」と言うのが最悪の失敗
- 既知違反の検出率 / UNKNOWN 率 / 要確認率 / 反例の再現率
- Z3 と有限ドメイン総当たりの照合（`conformance/finite_check.py`）
- 「要求数 / 承認済み / 対応範囲 / 判定済み / 未解析」の併記（一致率だけを見せない）

## holdout/ — 調整に使わない 3 ケース

golden に合わせ込んで過学習していないかを確かめるために取り分けてある。
`make eval` の対象外で、必要なときに明示的に回す:

```sh
uv run python -m speclino.eval.runner eval/holdout/cancel_order --mode source
```

## 追加するには

1. `golden/cancel_order/<番号>_<slug>/app/service.py` に変異させた実装を置く
2. 解析器の出力を `expected-code-ir.json` に保存する
3. 実際の判定を見て `case.yaml` の `expected` を書く（**先に期待を書いてから**実装を直す方が望ましい）
4. `uv run python -m speclino.eval.runner eval/golden/cancel_order --mode source` で確認
