# 判定を読む

UNKNOWN の読み方、知識の Finding、LLM 支援の規約と確定、LLM に送るもの。

## UNKNOWN の読み方

UNKNOWN は「壊れている」でも「大丈夫」でもなく、**この道具では確認できなかった**という報告。
Finding の `category` と `next_steps` が対処を示す。

| 原因 | よくある形 | 対処 |
| --- | --- | --- |
| 対応が見つからない | 図の操作・入口に、名前の一致でも `bindings.yaml` でも対応するシンボルが無い | 名前をそろえるか、`bindings.yaml` に上書きを足す |
| 不完全な経路 | 対応外の構文（一般の `while`、展開できない `for`、`getattr`）、不透明な領域 | その部分を単純化する、または要求に関係する効果だけを関数に切り出す |
| 解析の上限 | 経路 64 / 深さ / ソルバーの時間 | エージェントが `analyze_focus` で対象の要求だけ上限を上げて再解析する。残るなら構造を単純化する |
| 判定の欠落（`missing_finding`） | 出るべき要求・規約の結果が出なかった（検査器の例外、LLM の失敗、対応する behavior が無い） | `next_steps` の原因を直す。欠落は通過にならない |
| 契約の無い外部呼出し | 新しい SDK を呼んだ | `.speclino/contracts/` に要約を登録する |
| lock | lock が無い・署名が合わない・ファイルが改変された・期限切れ | `speclino sync` し直して lock をコミットする |

VERIFIED / VIOLATED を出すのは、モデルの中で正確な経路（`precision=exact_in_model`）だけ。段階的な
エスカレーション: 決定的解析 → エージェントの再解析 → 宣言済みテストの隔離実行（`Rule.tests` の ID だけ、
ネットワーク遮断）→ 承認者への構造化質問 → ゲート失敗。

## 知識の Finding（`KNOWLEDGE-*`）

| ID | 判定 | 意味と対処 |
| --- | --- | --- |
| `KNOWLEDGE-LOCK-001` | 適合 / 要確認 / 未検証 | lock が今の bundle と一致すれば適合。世代が古くメンバーが違えば要確認（sync し直す）。lock が無い・署名不一致・改変・期限切れ・別リポジトリなら未検証 |
| `KNOWLEDGE-SPEC-001` | 未検証 / 要確認 | リポジトリに取り込んでいない仕様（`specs/ir`）がある。`import-specs` で取り込んで承認する |
| `KNOWLEDGE-BINDINGS-001` | 要確認 | PR が `bindings.yaml`・`contracts/`・`exceptions.yaml`・プロジェクト規約を変えた（判定は変更前の内容）。承認者が確定すれば解除、マージ後の run で効く |

## LLM 支援の規約と確定

決定的に書けない規約（例: エラーメッセージが利用者に次の行動を示しているか）は、規約のルールを
`tier: llm_assisted`・検査器 `llm.checklist` にして、チェック項目の一覧を書く。

- **verdict は常に REVIEW**。区分を別に持つ: 指摘あり（`issue`）／割れた（`split`）／「LLM で確認、指摘なし
  （機械検証ではない）」（`no_issue`）。指摘には根拠の引用・場所・チェック項目が付く
- **必須（mandatory）の LLM 支援の規約は、区分にかかわらず人の確定（`attested`）までゲートを止める**。
  `no_issue` も機械検証ではないので自動では通さない（画面では「— 人の確定が必要」と出す）。
  助言（advisory）の規約はどの区分でもゲートを止めない
- 揺らぎを抑える: 関数・文字列リテラル・エンドポイントの単位で評価し、キー（規約の改訂・ルール・単位の内容の
  ハッシュ・モデル・プロンプトの版）が同じなら LLM を呼ばず記録を返す。新しいキーは 3 回評価して多数決、
  `no_issue` は 3 回とも一致したときだけ。replay は記録で再評価し、LLM を呼ばない。LLM の失敗は UNKNOWN
- **人の確定は verdict を書き換えず、ゲートの処置として記録する**（`attestations`）。指紋と内容のハッシュに結びつき、
  コードが変われば効かない。確定はプロジェクトに属し、そのプロジェクトの run にだけ効く。画面（REST）からは、
  開いている run の確定できる指摘だけを確定できる（`run_id` が必須。他の run・他のワークスペースの指摘は 404）。
  owner / approver が画面（run の詳細の「問題なしとして確定」/「問題として記録」）か、PR コメントで行う:

```text
/speclino attest <指紋> <sha256:内容のハッシュ>        # 問題なしとして確定（ゲートの阻止から外す）
/speclino attest <指紋> <sha256:内容のハッシュ> ack    # 問題として記録（阻止したまま）
```

ふだんは承認者が PR に引数なしの `/speclino attest` とコメントする（または GitHub で PR を **Approve** する）と、
その PR の最新の検査の要確認をまとめて確定する（ハッシュを打つ必要はない。最新のコミットの検査が完了しているとき
だけ。設定は [guides/github-app-setup.md](guides/github-app-setup.md)）。
PR コメント・Approve の承認者は GitHub のユーザー ID で照合する（ログイン名の付け替えでは通らない）。
`KNOWLEDGE-BINDINGS-001` の確定は ID で照合できる PR コメントか PR の Approve からだけ受け付ける。PR の作成者自身の確定は
自己確定として gate.json・manifest・画面に残る。

### LLM に送るもの（データの扱い）

- **送るのは、`tier: llm_assisted` のルールを定義したときだけ**（opt-in）。そのルールの `applies_to` /
  `params.paths` に一致する Python ファイルから取り出した単位と、ルールの題・本文・チェック項目。
  リポジトリ全体や差分、PR の本文・コメントは送らない
- **値は構造で送らない**（主の防御）:
  - 関数・エンドポイントの単位（`unit_kind: function` / `endpoint`）は、正規形（`ast.unparse`。コメントは
    落ちる）の中のすべての str / bytes の定数（docstring・f-string の定数部分を含む）を `<str len=N>` /
    `<bytes len=N>` に、6 桁を超える数値を `<num>` に置き換えてから送る。LLM は名前・構造・呼出しだけで
    判定する。単位の hash（記録のキー）も置き換えた後の正規形から作る
  - 文言を見るチェック項目（エラーメッセージの書き方など）は `unit_kind: string_literal` で評価する。
    `params.call_names`（例 `OrderError`・`HTTPException`・`api_error`）が必須で、その呼出しに渡された
    リテラルだけを送る。`call_names` の無い string_literal の規約は承認できず、既存のものは UNKNOWN になる
- 送り先は worker が呼ぶ Gemini（本番は Vertex AI。`GOOGLE_CLOUD_PROJECT` のプロジェクト、
  `SPECLINO_LLM_POLICIES_MODEL`、未指定なら `SPECLINO_AGENT_MODEL`）。費用は `SPECLINO_DAILY_COST_LIMIT_USD`
  の枠を共有する
- **秘密情報・個人情報らしい値を含む単位は送らない**（補助の防御。送る前の単位と、組み立てたプロンプト
  全体の両方にかける）。検出は 3 段:
  - 形: 秘密鍵のヘッダー、AWS / GCP / GitHub / Slack / `sk-` 形式のトークン、JWT、認証情報つきの URL、
    メールアドレス（`example.com` などの例示用ドメインを除く）
  - 名前: 代入先・キーワード引数・引数の既定値・dict のキー・属性名・比較の相手が秘密らしい名前
    （password / passwd / pwd / secret / token / api_key / access_key / private_key / credential / auth /
    cookie / session / signing_key / client_secret など。大文字小文字・区切りを無視した部分一致）なら、
    結びつくリテラルは形に関係なく秘密とみなす。環境変数・設定から読む式は送ってよい
  - 値: 文字列リテラルの中の、長さ 20 以上・空白なし・英字と数字が混在する Base64 / 16 進 / URL セーフの
    連なり（`os.environ` / `getenv` の変数名は除き、既定値は対象）

  その単位は UNKNOWN（「秘密情報らしい値を含むため LLM に送らない」と検出の種類）になり、必須の規約なら
  ゲートを止める。モデルが返した引用も同じ検出器で伏せてから保存・表示する
- 定数の式（暗黙の連結・`+`・`*`・f-string・`%`・`.format`・`.join`）は組み立ててから検出する。組み立てると
  64KB を超える・書式の幅や精度が大きすぎる・入れ子の書式・入れ子が深すぎる単位は「解析できない書式」として
  送らない（UNKNOWN）。実行時の計算（`chr()` で組み立てる値、`bytes([...])` の整数の並び、外部から読む値など）は
  検出できない（関数の単位では値そのものを送らないので、これが効くのは文字列の単位だけ）
- 1MB を超えるファイルは読まずに UNKNOWN（「ファイルが大きすぎる」）。元のソースが単位の上限の 4 倍を超える関数・
  入れ子や条件つきの関数は、正規形を作らずに UNKNOWN
- **検出は完全ではない**。LLM 支援の規約は、送ってよいと確かめたパスだけを `params.paths`（無ければ
  `applies_to.paths`）に指定する。`params.paths` が無い・リポジトリ全体に当たる（`**/*` など）規約は、
  承認の画面と監査に警告が出る（拒否はしない）
- 評価の結果（区分・票・伏せた引用）は `llm_assessments` にワークスペースごとに残し、同じ内容の単位は
  呼び直さない
- 全体で止めるには `SPECLINO_LLM_POLICIES_ENABLED=false`（LLM 支援のルールはすべて UNKNOWN になる）
