# 運用する

可観測性、CI での運用、自己検証、評価、既知の制約。

## 可観測性

「観測」の画面（`/w/<slug>/observability`）。表示はサーバの集計 1 つから作り、画面ごとに数え直さない。
数えるものが無い指標は 0 ではなく「未計測」と出す。

| 指標 | 見るもの |
| --- | --- |
| 形式化率 | プロジェクトに当たる仕様の要求の表のうち、`形式化` が書かれた行の割合 |
| 対応率 | 最新の検査の仕様の義務のうち、「対応が見つからない」UNKNOWN が無いものの割合（推定） |
| UNKNOWN の内訳 | 対応なし / 不完全な経路 / 解析の上限 / 判定の欠落 / lock / その他 |
| 取得 | メンバー・トークンごとに、最後に取得した bundle の世代と今の世代との遅れ（失効したトークンは「失効済み」） |
| 規約の推移 | 直近 10 回の完了した検査での、規約ごとの 違反 / 要確認 / 未検証 / 適合 の件数 |
| LLM 支援の安定度 | 指摘あり・割れた・指摘なしの件数、記録の再利用率、確定の件数（うち自己確定） |

## CI での運用（警告から始める）

最初は警告運用（advisory）から始め、対象を絞ってから必須にするのが安全:

```yaml
# 警告のみ（ゲート失敗でも終了コード 0。入力不正・実行不能のときだけ 2）
- run: uv run speclino check --repo . --head WORKTREE --specs-dir .speclino/specs --out build/review

# 必須（必須ルールに未検証が残れば失敗）
- run: >-
    uv run speclino check --repo . --head WORKTREE --specs-dir .speclino/specs --out build/self-check
    --fail-on VIOLATED --fail-on UNKNOWN --fail-on REVIEW --blocking
```

GitHub App のゲートでも同じ考え方で、規約を `level: advisory`（画面では「助言」）にすればゲートを落とさず
報告だけできる。仕様の要求も要求の表の `level` を `advisory` にできる。期限つきの免除は
`.speclino/exceptions.yaml`（全項目必須、期限切れは無効）で、免除された違反は適合に書き換えず「通過（免除中あり）」として別記する。

このリポジトリ自身が [.github/workflows/ci.yml](../.github/workflows/ci.yml) で必須運用している（**自己検証**）。
CLI 側を残しているのは、**GCP を畳んだ後でも / clone した人の手元でも自己検証が回る**ことと、
**LLM を一切使わずに違反を検出できる**ことを示すため。GitHub App を作って実 PR に接続する手順は
[guides/github-app-setup.md](guides/github-app-setup.md)、GCP への初回デプロイは [guides/deploy-runbook.md](guides/deploy-runbook.md)。

## 自己検証

SpecLino は自分自身のコードを自分の仕様で検査する。CI が落ちれば自分の規約違反を自分で見つけた
ということ。

仕様の正本は SpecLino（ワークスペース）にある。このリポジトリの仕様は、UML で書いた**俯瞰の仕様 7 本**
（`spec-sys-001` はワークスペース全体、`spec-ai-001`・`spec-gate-002`・`spec-know-001`・`spec-obs-001`・
`spec-sync-001`・`spec-ws-001` は spec-lino に当たる）。CI の self-check ジョブ（`make selfcheck`）は、承認済みの
仕様を `speclino sync`（または MCP の `knowledge_pull`）で書き出した `.speclino/specs/*.md` を手元の
parser で変換し、bundle の run と同じ UML の義務の検査に通す（`speclino check --specs-dir .speclino/specs`。
LLM・DB・GitHub App を使わない）。UML の要求を持たない仕様（旧形式の本文だけのもの）は、IR が手元に
無いので UNKNOWN（advisory）として「判定していない」ことを残す。

検出のデモ（`make test`）は、凍結した旧形式の Spec IR（`tests/fixtures/selfcheck/`）で、
`ToolGateway.invoke`・`conformance.gate.decide_gate`・`services.run_freshness.should_publish`・
`agent.tools.test_run.decide_observation` をわざと壊すと違反が出ることを確かめる。

規約は MAKE-001（`make setup/test/lint` が存在し空でない）、SPEC-FMT-001（仕様の書式）、
CI-001（pull_request トリガー・必須ステップ・読取り専用権限。`echo make test` や
`continue-on-error` は通さない）、API-ERR-001（エラー応答形式）。

## 評価（判定品質）

**Golden**（`eval/golden/cancel_order/`）は、**答えを先に決めてある 27 個の題材**。
「この変異を入れたら、この要求が・この分類で・この反例とともに違反になるはず」を
`case.yaml` に固定しておき、エンジンがそのとおり判定するかを毎回確かめる
（正しい実装 / 状態誤り / 409→400 / 所有者拒否 / 在庫復元の削除・管理者経路のみ・2 回 /
別注文の商品 / 早期 return 後の呼出し / 到達不能分岐 / 変数名変更 / 同値書換え /
ヘルパー分割 / 未解決の getattr / ループ / 上限到達 / 充足不能な適用条件 / 承認切れ …）。

`make eval` はこれを **2 通りの入力**（人が書いた Code IR / Python ソースを解析器に通したもの）
で評価する。両方で同じ結果になることが、解析器と検査器を別々に検証したことになる。

| 指標 | 結果 |
| --- | --- |
| ケース一致（手書き IR 入力 / Python ソース入力） | 27 / 27 |
| **誤った VERIFIED** | **0** |
| 反例の再現 | 15 / 15 |
| Z3 と有限ドメイン総当たりの照合 | 213 一致 / 0 不一致 |

**holdout**（`eval/holdout/`）は**調整に使わない別題材 3 ケース**。Golden に合わせ込んで
過学習していないかを確かめるために取り分けてある（Golden の調整中は見ない）。

混同行列と「要求数 / 承認済み / 対応範囲 / 判定済み / 未解析」を併記し、一致率だけを見せない。
**誤った VERIFIED**（解析できなかったものを適合と言う）は最悪の失敗なので、
上の表の値は [tests/unit/eval/test_golden.py](../tests/unit/eval/test_golden.py) が assert しており、
崩れると CI の `test (backend)` が落ちる。`make eval` はレポート本文を読みたいときに手元で回す。

## 既知の制約

- **対応する Python は部分集合**（if/elif/else、早期 return、認識済み raise、単純な代入と属性
  参照、and/or/not、比較、整数加減算、静的な直接呼出し）。それ以外は UNKNOWN として報告する
- ループは**要素数が静的に分かる `for`**（タプル／リストのリテラル、`range(定数)`、合計 8 回まで、
  `break`/`continue` なし）だけを展開して解析する。要素は Python と同じくループ前に評価し、
  `range` はモジュールで再定義され得るときは組込みと確定できないので展開しない。それ以外の
  `for`/`while` は UNKNOWN（S29 の構造解析で、触れないチャネルは巻き込まない）
- base を指定した検査では、到達する関数群を含むモジュール全体と仕様側入力（Spec IR・domain・
  bindings・contracts）のハッシュが base と一致する behavior の経路解析を省き、base の Code IR を
  再利用する（判定は必ず再実行する）。**再利用したことは run-manifest の `analysis` に残す**。
  この規模のリポジトリでは時間短縮は測定誤差の範囲で、効果が出るのは呼出しグラフが深い実コード
- フレームワークの応答は明示アダプター経由（FastAPI の `HTTPException(status_code=定数)` と
  `Response(status_code=定数)`）
- 「DB に永続化された」「並行実行で壊れない」は観測範囲外。コマンドの呼出しまでを検査する
- 動的証拠（`test_run`）は実行した入力の範囲に限る。静的な判定を上書きせず別軸で表示する。
  **テストの成功は判定を変えない**（テストは head の実装と同じプロセスで動くため、成功という
  観測そのものを未信頼側が偽造できる）。失敗だけを違反の証拠として受け取る
- 隔離実行の資源制限は環境で差がある。ローカル（Docker）はメモリ 512MB・CPU 1・pids 256 を
  コンテナに直接与える。本番（Cloud Run sandboxes）は**サンドボックス単位の上限を指定できず
  親サービスと資源を共有する**ため、親の側で締めて影響範囲を限っている（メモリ 512Mi・CPU 1・
  **同時実行 1**・タイムアウト 300s）。同時実行を 1 にしてあるので、未信頼コードが資源を
  食い潰しても同じコンテナの別の run を巻き込まない。ネットワーク遮断はどちらも効く
  （本番では DNS 解決の段階で失敗する）
- トレース取り込み（`trace_importer`）と増分解析は正規化 JSON の契約までで、実装は Stretch
- **UML の検査は Python の静的な経路との照合**。実行トレースとの照合、`loop` の回数の義務、非同期メッセージ、`par` は
  対象外。構造・順序の検査は Python だけ（OpenAPI による規約の検査は言語に依存しない）
- クラス図の関係は多重度とラベルの文を見ない。所有・依存は静的に確定できるもの（注釈・import・名前の参照）だけで、
  再束縛・動的な定義・解決できないデコレーターは UNKNOWN
- 状態遷移図は bindings の全 behavior の経路で検査する。属性への代入が対応に 1 つも無ければ UNKNOWN
- 可観測性の UNKNOWN の内訳と対応率は、検査器の決まった言い回しで分類した推定。合わないものは「その他」に入る。
  LLM 支援の記録の再利用率は、並行した run の分を外れに数えるので低めに出る
- `bindings.yaml` 等を変える PR は、承認者が確定するまで必ず REVIEW で止まる（マージ後の run で効く）
- 旧形式の仕様から図への自動変換はしない（人が移す。エディタで両方を書ける）
