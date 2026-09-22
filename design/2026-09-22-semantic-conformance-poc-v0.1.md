# 仕様–実装の意味差分レビュー基盤

## PoC v0.1 実装依頼書（提案）

> 位置づけ: SpecLino の意味検証コア（Spec IR / Code IR / 検査器）の規範文書。ユーザー提供の原文を保存したもの。ハッカソン向けの取捨選択は `2026-09-22-hackathon-plan.md` を参照。

文書の位置づけ：これまでの議論を、初めて参加する実装者に引き渡せる粒度へ具体化した草案。目的と既存機構は議論に基づく。Python、CLI、対応構文、データ構造、判定区分、各種上限は、この文書で追加した初期実装案であり、現場の確定済み要件ではない。

最初に読むべき要約：仕様書に書かれた要求と、ソースコードから抽出した振る舞いを、共通の語彙と条件式で照合する。出力するのは巨大なコード解説ではなく、「どの要求が、どの条件で、どう違反されるか」「何を確認でき、何を確認できなかったか」である。最初の成果物はローカル／CIで実行するCLIとJSON・Markdownレポートとする。

---

## 1. 背景と目的

AI生成コードの変更量が増えたときに、すべてのコード差分を人間が逐次読む方式ではレビュー負荷が大きい、という問題意識が出発点である。

現場には、コードからクラス図とテストの呼び出しベースのシーケンス図を生成し、仕様書にある図と照合する仕組みがある。今回のシステムは、その置き換えではなく、条件・更新値・副作用・結果についての比較を追加する。

既存シーケンス図の生成元が「実際のテスト実行トレース」なのか「テストコードを解析した呼び出し関係」なのかは、導入時に確認する。後者を実行済みの証拠として扱ってはならない。また、シーケンス図がガード条件や引数まで保持している場合、その情報は再利用する。「シーケンス図では処理内容を一切表現できない」という前提は置かない。

### 目標

> レビュー対象を Code Diff だけにせず、仕様と実装の意味差分へ移す。

### 目標ではないもの

汎用プログラムの完全な正しさの証明、コードレビュー・テストの全面廃止、LLMの「問題なさそう」という自由文による承認、グラフをきれいに表示すること自体は、今回の目標ではない。

---

## 2. 実装を依頼するもの

次の処理を一連のCLIとして実装する。

```text
仕様Markdown → 要求抽出 → 人間が承認したSpec IR ─┐
                                                ├→ 適合性検査 → JSON / Markdown
コード      → 静的解析 → Code IR ───────────────┘
テスト記録  → トレース取り込み ─────────────────→ 観測証拠を追加
```

ソースコードと仕様書の文章を直接比較するのではない。中間表現で比較する。

ただし「共通の中間表現」とは、両側を同じフラットな一覧へ潰すことではない。

- Spec IR：満たすべき要求。条件、必須／禁止事項、許容結果を保持する。
- Code IR：解析した実装の振る舞い。経路条件、状態更新、実行された呼び出し、終了結果を保持する。

両者は同じ業務語彙・型・式表現・参照方式を使うが、要求と観測／解析結果という役割を分ける。

### 利用者の操作

初回は、対象処理と仕様の対応付け、業務語彙の対応付け、仕様の解釈を承認する。以後はPRのbase/headを入力し、差分レポートを得る。仕様の該当箇所が変更されたときは、その解釈を再承認する。

---

## 3. PoCの範囲

### 3.1 採用する初期案

| 項目 | v0.1での扱い |
|------|-----------------------------------|
| 対象言語 | Python。最初はPython 3.12構文に固定して開発・試験する |
| 対象処理 | 同期的なWeb APIのサービス関数。対象関数は設定で明示する |
| 入力仕様 | ローカルMarkdown。要求IDまたは安定した見出しを付ける |
| 出力 | CLI、機械可読JSON、人間向けMarkdown |
| 静的解析 | AST、簡易CFG、経路条件、状態更新、呼び出しと引数 |
| 条件検査 | 型付きの式とSMTソルバー。有限ドメインでの検算も用意する |
| データ保存 | JSONファイル。グラフDBは必須にしない |
| テスト統合 | トレースJSONの取り込み。テスト実行自体は別ジョブとする |
| LLM | 仕様抽出・用語対応の候補生成のみ。比較の最終判定はさせない |
| CIでの扱い | 初期は警告運用。ツール実行失敗は別の失敗として扱う |

### 3.2 最初に対応するPythonの部分集合

if / elif / else、早期return、認識済み例外のraise、定数、ローカル変数、登録済みの単純な属性参照・代入、and / or / not、等値・不等値・大小比較、整数の加減算、静的に解決可能な直接呼び出しを対象にする。

単純な属性とは、プロパティや任意の`__getattr__`等を実行しないと、型・値の対応を確定できない属性を除いたもの。対象データ型とそのフィールドは設定で宣言する。

フレームワークの全挙動を推測しない。HTTP応答や例外の変換は、明示したアダプターが理解する構文だけに限定する。サービス関数の返り値だけを見た場合は「サービス層の結果」であり、実際のHTTP応答を確認したとは表示しない。

### 3.3 対象外とするもの

ループ、再帰、動的ディスパッチ、任意のデコレーター、非同期並行処理、分散トランザクション、ORM全体の解析、一般的な別名参照、DB永続化の成否、配送保証、性能、UI適合性は初期対象外とする。

これらに遭遇した場合は、処理を黙って落とすのではなく、影響する要求をUNKNOWNにする。対象外領域でも、登録済みアダプターに明示された契約を利用することはできるが、その仮定と確認範囲をレポートに残す。

---

## 4. 実装者に渡す具体例

### 4.1 業務例：注文のキャンセル

デモ用の仕様を次のように固定する。これは実際の現場仕様ではなく、検査器を開発するための例である。

| 要求ID | 要求 |
|-----------|------------------------------------------------------|
| R-CANCEL-01 | 管理者または注文所有者でなければ403を返す。認可を状態判定より優先する |
| R-CANCEL-02 | 認可された利用者でも、注文がPENDING以外なら409を返す |
| R-CANCEL-03 | 認可された利用者がPENDINGの注文をキャンセルした場合、関数終了時の注文状態をCANCELLEDにする |
| R-CANCEL-04 | 同じ成功条件で、在庫復元コマンドを対象注文の商品を引数にして1回呼ぶ |
| R-CANCEL-05 | 同じ成功条件で、監査記録コマンドを実行者IDとキャンセル操作を引数にして1回呼ぶ |
| R-CANCEL-06 | 同じ成功条件で、応答を204にする |
| R-CANCEL-07 | 403または409で拒否する経路では、注文状態への書き込みと、在庫復元・監査記録コマンドの呼び出しを行わない |

例の状態ドメインはPENDING / SHIPPED / CANCELLEDとする。注文取得、入力型の検証、認証済み利用者の取得は処理入口より前に完了していると仮定し、明記する。

「未発送」は必ずしも「PENDING」と同義ではないため、自然言語から勝手に置き換えない。このデモでは仕様にPENDINGを明記する。

### 4.2 観測境界

この例でv0.1が判定するのは、サービス関数内の状態値、コマンド呼び出し、引数、回数、アダプターがモデル化した応答である。

「在庫復元コマンドが呼ばれた」と「DB上の在庫が実際に復元された」は別である。業務仕様が後者を要求する場合、コマンド呼び出しの検査は子項目として表示し、元の業務要求全体を適合扱いにしない。永続化の観測または検証済み契約が不足していれば、元の要求はUNKNOWNにする。

同様に、メモリー上のorder.statusへの代入はDBコミットの証拠ではない。

### 4.3 不具合例

同じクラスとメソッドを同じ順序で呼んでいても、次を違反として提示したい。

- 成功時の状態更新：CANCELLEDではなくSHIPPED
- 認可条件：管理者 OR 所有者ではなく管理者だけ
- 在庫復元：呼び出し自体はあるが、管理者の経路にしかない
- 監査記録：実行者IDではなく注文所有者IDを渡す
- 応答：409ではなく400

---

## 5. 入力と出力の契約

### 5.1 必須入力

| 入力 | 内容 |
|---------------|-------------------------------|
| ソーススナップショット | 対象リポジトリの解決済みコミットSHA |
| 仕様スナップショット | Markdown本文、要求ID、本文ハッシュ |
| 承認済みSpec IR | 抽出した要求と、本文／IRハッシュに対応する承認記録 |
| bindings | 対象関数、フィールド、API応答、業務操作と実装シンボルの対応 |
| domain | 状態の列挙値、各変数の型、入口の仮定 |
| analysis policy | 対応範囲、タイムアウト、上限、重大度・ゲート方針 |

最初の段階ではSpec IRを人手で作成してよい。自然言語抽出器の完成を比較器実装の前提にしない。

### 5.2 任意入力

baseコミット、base側の仕様と承認、テストトレース、既存のクラス／呼び出し関係データ。画像の図を再解析する方式は初期必須にせず、既存生成器のJSON等をアダプターで取り込む。

### 5.3 出力ファイル

```text
output/
  run-manifest.json       # 入力ハッシュ、解析器・設定・依存のバージョン
  spec-ir.json            # 検査で実際に使った承認済み要求
  code-ir.json            # 経路条件、更新、呼び出し、結果
  findings.json           # 判定、理由、反例、証拠
  semantic-diff.json      # base/headの意味差分。base未指定時は省略
  review.md               # 人間向けの要約と詳細
```

実行エラー時も可能な範囲でrun-manifest.jsonと診断情報を出す。ソース、仕様、承認記録をツールが勝手に書き換えない。

---

## 6. Semantic IRのデータモデル

### 6.1 共通の型

| 型 | 必須内容 |
|-----------|---------------------------------------------|
| SnapshotRef | repository、commit、path、content_hash、必要なら行範囲 |
| SourceRef | snapshot_id、要求IDまたはsymbol、line_start、line_end |
| Expr | 型付きの式木。文字列のPythonコードは実行しない |
| Behavior | 安定したid、入口、ドメイン、仮定、観測境界 |
| Evidence | 種別、入力スナップショット、解析範囲、根拠位置 |
| Diagnostic | code、対象要求・経路、理由、重大度 |

Exprの初期語彙はref / const / eq / ne / lt / le / gt / ge / and / or / not / add / subとする。変数参照にはbefore / after / outcomeの名前空間を明示する。前二者は入口と終了時の状態、outcomeはモデル化した終了結果を表す。

型はboolean、integer、列挙型、同一性だけ比較するopaque IDとする。opaqueな商品の集合を受け渡す場合は、その内容を検査できたと扱わない。

JSON Schemaで必須項目・型・列挙値・未定義フィールドの禁止を検証する。形式検証後に、参照解決、型整合性、要求の矛盾、承認ハッシュの一致を別途確認する。[S2]

### 6.2 Spec IR

各Ruleを独立した判定単位とする。

| フィールド | 意味 |
|-------------|----------------------------------------------------------------------------------------|
| id | 安定した要求／判定単位ID |
| source_refs | 元の仕様の該当箇所 |
| applicability | この要求が適用される入口条件 |
| modality | v0.1は`must`または`must_not` |
| assertion | 成立すべき結果、更新、イベント回数、引数など |
| claim_scope | `memory_state / command_invocation / service_outcome / http_response / persisted_state`等 |
| approval_ref | この仕様解釈を承認した記録 |

mayを将来導入しても、許可を必須と取り違えない。初期は曖昧な「できる」をそのまま必須成功へ変換せず、人間確認の対象にする。

### 6.3 Code IR

各経路は以下を保持する。

| フィールド | 意味 |
|--------------|-----------------------------------------------|
| path_id | 経路ID |
| guard | この経路を通る入口条件 |
| state_updates | 更新対象、更新前後の式、対応ソース位置 |
| events | 書き込み・呼び出し・戻り等の順序付きイベント |
| outcome | 戻り値、認識した応答、例外、未解決終了 |
| precision | `exact_in_model / overapproximate / incomplete` |
| unknown_causes | 未解決呼び出し、型、上限等 |
| source_refs | 経路を構成するソース位置 |

経路全体を要約しても、イベントの条件・引数・回数を落とさない。仕様が順序を規定していなければ、イベント順の違いだけで違反にしない。

### 6.4 グラフとしての保存

Behavior、Rule、Path、Predicate、StateUpdate、Effect、Outcome、SourceRefをノードとし、applies_when / follows / writes / invokes / yields / backed_by等の型付きエッジで接続できるよう、安定したID参照を持たせる。

v0.1はJSONのノード・参照で十分とする。グラフDBやグラフ描画UIは必須にしない。CPGはコード側の解析基盤候補であり、要求と実装を比較する業務意味モデルそのものではない。JoernのCPGも複数表現や抽象化レイヤーを扱うが、初期実装で必須採用とはしない。[S5]

### 6.5 式とAssertionの構文契約

Exprは次のタグ付きオブジェクトとする。

```text
Ref       = {op: "ref", path: "before.order.status"}
Const     = {op: "const", type: "OrderStatus", value: "PENDING"}
Unary     = {op: "not", arg: Expr}
Binary    = {op: "eq" | "ne" | "lt" | "le" | "gt" | "ge" | "add" | "sub",
             left: Expr, right: Expr}
Nary      = {op: "and" | "or", args: [Expr, ...]}
```

Refの型はBehaviorのsymbol tableから決める。and/orのargsは1件以上とし、空配列は拒否する。比較・演算の型が合わない場合は入力不正とする。

Assertionのv0.1の種類は次の2つとする。

| kind | フィールドと意味 |
|-----------------|-------------------------------------------------------------------------------------------------------------|
| expression | `expr`にboolean Exprを持ち、その式の真偽を検査する。終了状態や応答の比較に用いる |
| event_requirement | `event_kind / operation_or_target / phase / count / arguments_equal`を持つ。種別・操作・phaseで選んだイベントの総数と、各イベントの引数を検査する |

countはeq / ge / leの比較演算子と非負整数のvalueを持つ。arguments_equalは引数名からExprへの辞書である。イベント総数は引数による絞り込みの前に数える。したがって、正しい引数で1回、間違った引数でもう1回呼んだ実装は「1回」の要求を満たさない。引数の辞書に指定した項目は、選択された全イベントで一致することを要求する。

modality: mustはAssertionが真であることを要求し、must_notはAssertion全体が偽であることを要求する。初期の仕様抽出では禁止事項をmust＋イベント数0へ正規化し、複雑な否定の読み違いを避ける。たとえば「通知を呼ばない」は、通知呼び出し数が0というAssertionにする。

### 6.6 Spec IRの具体例（1要求の抜粋）

以下はデモ用データであり、実際の承認記録ではない。

```yaml
schema_version: "0.1"
behavior_id: cancel_order
rule:
  id: R-CANCEL-04
  source_refs:
    - snapshot_id: demo-spec
      requirement_id: R-CANCEL-04
  approval_ref: demo-approval
  modality: must
  claim_scope: command_invocation
  applicability:
    op: and
    args:
      - op: eq
        left: {op: ref, path: before.order.status}
        right: {op: const, type: OrderStatus, value: PENDING}
      - op: or
        args:
          - {op: ref, path: before.actor.is_admin}
          - op: eq
            left: {op: ref, path: before.actor.id}
            right: {op: ref, path: before.order.owner_id}
  assertion:
    kind: event_requirement
    event_kind: call
    operation_or_target: inventory_restore_command
    phase: invoked
    count: {operator: eq, value: 1}
    arguments_equal:
      items: {op: ref, path: before.order.items}
```

### 6.7 Code IRの具体例（経路の抜粋）

次は所有者だけの利用者が通る経路で、在庫復元呼び出しが欠けているデモ用Code IRである。snapshotと承認情報は前掲例と同じデモ用定義を参照する。ソース位置も説明用であり、実際のコードを解析した結果ではない。

```yaml
behavior_id: cancel_order
path_id: owner_pending_path
precision: exact_in_model
guard:
  op: and
  args:
    - op: eq
      left: {op: ref, path: before.order.status}
      right: {op: const, type: OrderStatus, value: PENDING}
    - op: not
      arg: {op: ref, path: before.actor.is_admin}
    - op: eq
      left: {op: ref, path: before.actor.id}
      right: {op: ref, path: before.order.owner_id}
state_updates:
  - target: order.status
    value: {op: const, type: OrderStatus, value: CANCELLED}
events:
  - sequence: 1
    kind: write
    target: order.status
    phase: applied
    value: {op: const, type: OrderStatus, value: CANCELLED}
  - sequence: 2
    kind: call
    operation: audit_record_command
    phase: invoked
    arguments:
      actor_id: {op: ref, path: before.actor.id}
      action: {op: const, type: AuditAction, value: CANCEL_ORDER}
outcome:
  kind: http_response
  status: 204
unknown_causes: []
source_refs:
  - snapshot_id: demo-code
    symbol: app.orders.service.cancel_order
    line_start: 10
    line_end: 20
```

比較器はRuleの適用条件とこの経路条件が両立することを確認したうえで、対象コマンドの呼び出し数0が要求の1と異なると判定する。イベント一覧に対象の呼び出しがないことは、この経路のイベント抽出が完了した場合に限り「呼び出しなし」の証拠になる。未解析のため一覧に現れない場合は、precisionをincompleteにする。

---

## 7. 仕様からSpec IRを作る処理

1. Markdownを要求ID・見出し単位で分割し、原文位置を保持する。
2. LLMまたは構造化ルールで要求候補を抽出する。
3. 条件、要求、禁止事項、結果、用語の不確実性を出力する。
4. JSON Schema検証、型検査、ドメイン内での矛盾検査を行う。
5. 人間が「原文とその解釈」を確認し、本文とIRのハッシュを承認する。
6. 比較時は承認済みIRを再利用する。元の仕様本文が変われば再承認する。

### 禁止事項

コードの実装内容を見せて、それに合うよう仕様を解釈させない。コードに存在する挙動で仕様の空欄を埋めない。曖昧な語を黙って確定しない。LLMの自己申告confidenceで承認を代替しない。

例：「未発送」としか書かれていない場合、未発送を構成する状態集合が未定義なら、PENDINGへ勝手に縮約しない。

抽出しきれない要求も出力に残す。対応できる要求だけを分母にして、仕様全体を確認したように表示しない。

承認UIは初期必須ではない。生成された候補ファイルをGitのレビューで確認し、承認ハッシュを保存する方式でよい。承認者情報をLLMが生成してはならない。

---

## 8. 業務語彙とコードの対応付け

仕様の「在庫復元」とコードのstock_service.releaseが同じ操作かは、名称の類似度だけで確定しない。

bindingsには、完全修飾シンボル、業務操作ID、引数対応、観測境界、利用する契約を保存する。

```yaml
schema_version: "0.1"
behaviors:
  cancel_order:
    entrypoint: app.orders.service.cancel_order
    variables:
      actor.is_admin: user.is_admin
      actor.id: user.id
      order.owner_id: order.owner_id
      order.status: order.status
      order.items: order.items

operations:
  inventory_restore_command:
    symbol: app.inventory.restore
    claim_scope: command_invocation
    argument_bindings:
      items: positional:0
    summary_ref: inventory_restore_boundary_v1
```

この例は設定契約の例であり、既存ツールの設定ファイルではない。

summary_ref先には、正常終了するという仮定、戻り値、変更し得る状態、例外、内部呼び出しをどこまでモデル化するかを明示する。未知の外部関数を副作用なしと仮定しない。

コマンドを呼ぶことと業務的な効果が完了することは別の契約である。releaseが実際には予約解放なら「在庫数の加算」と同一操作にしてはならない。

コードから生成したヘルパー要約は、関数本文と依存先のハッシュが変われば再生成する。人間承認した外部契約は、変更時に再承認を要求する。

---

## 9. コードからCode IRを作る処理

### 9.1 解析の順序

1. 対象コミットのソースを読み、ASTを構築する。
2. 関数・import・単純なシンボル参照を索引化する。
3. bindingsの入口を解決する。
4. 対応構文から制御フローを構築する。
5. 経路を分岐させながら記号的な状態を更新する。
6. 呼び出しをローカル解析または承認済み契約で要約する。
7. 終了経路をCode IRへ保存する。
8. 未対応箇所と、その影響を受ける要求を記録する。

Pythonの`ast.parse()`と`NodeVisitor`は構文木の生成・走査に利用できる。ただし、CFG、呼び出し解決、経路条件、業務意味への変換は別途実装する。[S1]

### 9.2 必ず守る意味

早期returnより後のコードを実行済みにしない。実行不可能な分岐の呼び出しを必須処理の証拠にしない。副作用の存在だけでなく、「どの条件で、どの引数で、何回実行されるか」を保持する。

前状態と後状態を混同しない。状態変更後に評価した条件は、変更後の値に基づいて計算する。

コメント、docstring、関数名はコードの挙動の根拠にしない。対応付け候補のヒントとしての利用は可とする。

### 9.3 最初の上限案

解析する直接呼び出しの深さは4、入口当たりの経路数は256、要求×経路のソルバー呼び出しは1回当たり1,000msを既定の設定案とする。これは性能実績ではなく初期値であり、設定可能にする。

上限超過はその影響範囲をUNKNOWNにする。実行され得る経路を捨ててVERIFIED_IN_SCOPEを出してはならない。

v0.1では差分ファイルだけの解析に限定せず、設定した入口から到達する対応範囲をbase/headでそれぞれ解析する。増分解析の最適化は後でよい。

---

## 10. 適合性検査の具体的なアルゴリズム

### 10.1 単純なグラフ類似度にしない

ノード数、名前、形が似ているかではなく、要求に違反する入力と経路が存在するかを見る。

例：

仕様：PENDINGかつ(管理者または所有者)なら、在庫復元コマンドを1回呼ぶ。
実装：PENDINGかつ管理者なら呼ぶが、所有者だけの利用者では呼ばない。

期待する反例は次になる。

```text
order.status = PENDING
actor.is_admin = false
actor.id = order.owner_id
inventory_restore_command の呼び出し回数 = 0
```

### 10.2 検査式

各要求と実装経路について、次を組み立てる。

```text
承認済みの入口仮定
AND 型・状態ドメイン
AND 要求の適用条件
AND 実装経路の条件
AND 要求の保証が成立しない
```

これが充足可能なら、モデル上の反例が存在する。

Z3は制約の充足性をSAT / UNSAT / UNKNOWNで返し、SAT時にはモデルを取得できる。ソルバーのUNKNOWNとタイムアウトを、違反なしに読み替えてはならない。[S3]

### 10.3 判定規則

| 判定 | 必要条件 |
|-----------------|-------------------------------------------------------------|
| VERIFIED_IN_SCOPE | 要求が承認済み。適用可能な入力が存在し、関連経路を検査対象のモデル内で網羅し、違反式がすべてUNSAT。未解決の影響がない |
| VIOLATED | 対応範囲で正確に構成された経路について反例がある、または検証可能な実行記録で違反を観測した |
| UNKNOWN | 未対応構文、未解決呼び出し、仮定不足、タイムアウト、観測境界不足などで決定できない |
| REVIEW | 仕様解釈、語彙対応、仕様にない追加挙動、過大近似の反例など、人間判断が必要 |

VIOLATEDでも、symbolic_counterexampleとobserved_counterexampleを区別する。前者を実際に再現したバグと表現しない。すべての判定に仮定と解析境界を添える。

近似されたCode IRに対するSATは、実プログラムでは実行不可能な反例かもしれないため、REVIEW: potential_violationにする。十分な根拠がないのに確定違反へ昇格しない。

前提自体が矛盾する、または適用条件に該当する入力が存在しない場合、空虚な成功としてVERIFIEDを出さない。仕様／ドメイン診断として表示する。前条件・後条件を区別する考え方は既存の契約検証にもあり、たとえばDafnyはrequires / ensuresを用いる。[S6]

### 10.4 「成功条件」を失わない

「管理者または所有者だけがキャンセルできる」という制約だけでは、全員を拒否する実装まで許してしまう可能性がある。

そのため、必要な認可条件に加えて、「認可され、PENDINGで、承認済み前提を満たす場合は成功する」という要求も独立に持つ。拒否条件と成功条件を両方向から検査する。

### 10.5 「未記載」と「禁止」を分ける

仕様に通知処理が書かれていなくても、通知が禁止とは限らない。初期の既定値はopen-worldとする。

- 必須の処理がない：根拠が十分ならVIOLATED。
- 明示的に禁止された処理がある：根拠が十分ならVIOLATED。
- 仕様に記載されない追加処理がある：REVIEW。

閉じた操作一覧を要求したい場合は、仕様にその閉包性を明示し、承認してから検査する。

---

## 11. テスト・既存図との統合

### 11.1 動的観測は別軸で保持する

静的解析の判定と、テストで観測した事実を一つの緑色チェックへまとめない。

```text
static_verdict: VERIFIED_IN_SCOPE
test_observation: NOT_OBSERVED
```

これは「モデル内で静的に確認したが、対応する実行はテストで観測していない」という意味であり、テスト失敗でも、テストで確認済みでもない。

### 11.2 トレースの最小契約

run_id / test_id / code_commit / behavior_id / before / events / after / outcome / mocked / observation_scope / completenessを保持する。イベントには連番とソースまたはアダプターの識別子を付ける。

completenessは、トレースの欠落有無、計測したイベント種別、正常に終了記録を取得したかを表す。記録がないことを、呼び出しがなかった証拠として使う場合に必要になる。

pytestにはテストフェーズの結果を取得するフックがある。必要ならトレース収集プラグインの起点として使うが、引数・状態・業務イベントの計測は本システム側のアダプターとして追加する。[S4]

モック相手の呼び出しは、モック相手の呼び出しとして表示する。実DBの更新や実サービスの成功を観測した扱いにしない。

### 11.3 既存機構との接続

既存クラス図の比較結果とシーケンス図の比較結果は別のレーンとしてレポートに付加する。Behavioral Conformanceが一致しても、構造検査が失敗しているなら結果を隠さない。

旧システムから取り込むデータにはorigin: static_analysis / static_test_call_analysis / runtime_traceを必ず付ける。対応するエクスポート形式が未確定なら、初期PoCでは正規化JSONの入力インターフェースとサンプルデータまでを作る。

---

## 12. PRの意味差分

### 12.1 比較するスナップショット

```text
base仕様 + baseコード → base時点の適合性
head仕様 + headコード → head時点の適合性
base Code IR と head Code IR → 実装の意味差分
```

headコードをbase仕様だけに照合し、正式な仕様変更を誤って実装違反にしない。逆にhead仕様の解釈が未承認なら、その状態を表示し、仕様も実装も同じPRで変わっただけで自動承認しない。

### 12.2 結果分類

新規違反、解消された違反、既存違反、追加・変更・削除された要求、追加・変更・削除された実装の振る舞い、判定不能化を表示する。

変更行に要求IDを紐付けるだけでは不十分である。変更されたヘルパーに依存する入口の要求も再評価する。v0.1では対象入口を全解析して正しさを優先する。

### 12.3 再現性

入力コミット、仕様本文ハッシュ、Spec IR、bindings、アダプター契約、解析器、ソルバー、設定を固定して結果を再現可能にする。LLM抽出結果は承認後に保存し、比較のたびに自由生成し直さない。

解析時刻やrun_idを除き、同じ入力の結果・順序・finding識別子が安定することを試験する。

---

## 13. レポートの仕様

### 13.1 一覧

以下は表示例であり、実測値ではない。

```text
対象：cancel_order
比較：base_sha → head_sha
要求：7件

適合（確認範囲内）：4件
違反：1件
不明：1件
要確認：1件

確認範囲：サービス層の状態、コマンド呼び出し、モデル化した応答
対象外：DBコミット、並行実行、外部サービス内部
```

### 13.2 詳細

```text
R-CANCEL-04：在庫復元コマンドを1回呼ぶ
判定：VIOLATED
根拠種別：symbolic_counterexample

条件：PENDING、管理者ではない、注文所有者である
期待：inventory_restore_command を1回呼ぶ
実装：0回

仕様：要求IDと行範囲
コード：対象関数、条件分岐、呼び出しの有無を判断した経路
解析範囲：全関連経路を対応モデル内で検査済み
仮定：認証・注文取得が完了、境界サービスは宣言した契約に従う
テスト：この反例は未実行
```

「存在しない処理」の根拠を架空の1行に結び付けない。対象経路と解析完了範囲を根拠にする。

### 13.3 Findingの機械可読契約

id / requirement_id / behavior_id / verdict / category / severity / message / expected / actual / counterexample / evidence_refs / assumptions / claim_scope / code_refs / spec_refs / base_head_statusを出力する。

categoryの初期値はmissing_effect / forbidden_effect / constraint_mismatch / state_mismatch / outcome_mismatch / argument_mismatch / cardinality_mismatch / undocumented_behavior / unsupported_analysis / ambiguous_spec / stale_approvalとする。

severityとverdictは別にする。重大な要求でも、解析できなければUNKNOWNであり、重大度の高さを理由に違反とは判定しない。

---

## 14. モジュール分割

| モジュール | 入力 → 出力 | 責務 |
|------------------|----------------------------|------------------|
| spec_reader | Markdown → 要求候補 | 原文位置を保持する |
| spec_extractor | 要求候補 → Spec IR候補 | LLMの候補生成、曖昧さの抽出 |
| spec_validator | IR候補・承認 → 検査用Spec IR | 型、矛盾、ハッシュを検証する |
| python_frontend | ソース → AST・シンボル索引 | 対応構文の判定 |
| path_analyzer | 入口・AST・契約 → Code IR | 経路、更新、呼び出し、未知箇所を抽出 |
| conformance_engine | Spec IR・Code IR → Findings | 決定的な制約検査 |
| trace_importer | トレース → Evidence | 観測範囲を保存する |
| semantic_diff | base/head IR・Findings → 差分 | 新規・既存・解消を分類 |
| reporter | Findings・Evidence → Markdown | 人間向けに圧縮する |
| cli | 設定・各入力 → 成果物 | 実行制御、終了コード、再現性 |

conformance_engineはLLMに依存させない。手作業で作ったSpec IR／Code IRだけで単体試験できる構造にする。

---

## 15. CLIと運用契約

以下は新規実装するCLIのインターフェース案であり、既存製品のコマンドではない。

```sh
semreview spec-extract \
  --spec docs/orders.md \
  --domain review/domain.yml \
  --out review/proposed-spec-ir.json

semreview check \
  --repo . \
  --base main \
  --head HEAD \
  --config review/config.yml \
  --out build/semantic-review
```

承認は、承認済みファイルとハッシュがconfigから参照できる形で管理する。CLIが実在しない承認者や承認記録を生成してはならない。

main / HEADは実行開始時にSHAへ固定する。PoCのCI入力はクリーンなGitスナップショットに限定する。

終了コード案：

| コード | 意味 |
|---|-------------------------------------------------|
| 0 | 検査完了。fail-onで指定されたゲート条件に該当しない。UNKNOWNがゼロという意味ではない |
| 1 | 検査完了。指定されたゲート条件に該当 |
| 2 | 入力不正・矛盾した仕様・実行基盤の失敗などで、要求した検査を完了できなかった |

初期の既定値はadvisoryとし、VIOLATEDでもレポート生成自体は成功させる。blockingモードは別設定で明示する。レポートには必ずverdictごとの件数を残す。

仕様未承認や対応外構文のような部分的判定不能はFindingsへ残す。JSON破損や入力スナップショット取得失敗のような実行不能とは区別する。

---

## 16. セキュリティと信頼境界

静的解析で対象リポジトリのモジュールをimportしない。IRの式をPythonのeval()で実行しない。ソース解析にも時間・メモリー・入力サイズの上限を設ける。

テスト実行は任意コード実行であるため、比較エンジンとは別の隔離ジョブにする。本番資格情報、ホストのDockerソケット、書き込み可能な共有領域を渡さず、ネットワークは既定で遮断する方針とする。

LLMへ渡すコード・仕様の範囲は設定で制限し、社内規程に従う。コメントや仕様に書かれたモデルへの命令を、承認・設定変更・ツール実行の指示として扱わない。

トレースに機密値を無制限に保存しない。要求検査に必要な値だけを取得し、伏せたために比較できない値はUNKNOWNの理由として残す。

---

## 17. 受け入れ試験

### 17.1 Golden fixtures

既知の仕様、コード、正解IR、期待Findingをセットで固定する。変異した実装だけでなく、等価な書き換え、対象外の構文、仕様の曖昧さも含める。

| ケース | 期待 |
|-------------------|---------------------------|
| 正しい実装 | 対応範囲の要求はVERIFIED_IN_SCOPE |
| 成功時の状態を誤る | state_mismatch |
| 409を400に変える | outcome_mismatch |
| 所有者を拒否し管理者だけ許す | constraint_mismatch |
| 無権限者のキャンセルを許す | constraint_mismatch |
| 在庫復元を削除する | missing_effect |
| 在庫復元を管理者経路だけで呼ぶ | 所有者のみの反例を伴うmissing_effect |
| 在庫復元を2回呼ぶ | cardinality_mismatch |
| 別の注文の商品を渡す | argument_mismatch |
| 監査に実行者ではなく所有者のIDを渡す | argument_mismatch |
| 早期return後に必須呼び出しがある | missing_effect |
| 到達不能分岐にだけ必須呼び出しがある | missing_effect |
| コメントには実施と書くが呼び出しがない | missing_effect |
| ローカル変数名だけを変える | 誤った意味差分を出さない |
| 同等の条件式へ書き換える | 誤った違反を出さない |
| 対応する直接ヘルパーへ処理を分割する | 正常に追跡する |
| 未記載の通知を追加する | REVIEW。禁止と明記されていればVIOLATED |
| 未解決のgetattrで呼び出す | 関連要求はUNKNOWN。黙って適合扱いにしない |
| 関連経路に対応外ループがある | UNKNOWN |
| 解析上限・ソルバー制限に達する | UNKNOWN |
| 管理者のテストだけを観測した | 所有者経路を観測済みにしない |
| モックへの呼び出しだけがある | 永続化要求全体はVERIFIEDにしない |
| 適用条件が充足不能 | 空虚な成功にせず仕様／ドメイン診断 |
| 仕様本文が承認後に変わる | stale_approval。黙って旧IRで成功しない |
| 呼び出し先だけを変更する | 依存する入口の要求を再評価する |
| 同じ凍結入力で再実行する | 時刻等を除いた結果が一致する |

### 17.2 完了条件

上記の固定ケースで期待する分類・反例・根拠を出せること。対応範囲外のケースを誤って適合扱いしないこと。すべてのFindingから元の仕様とコードに戻れること。base/headで新規・既存・解消を区別できること。CLI実行手順と制約がREADMEに記載されること。

この合格はPoCの固定ケースに対するものであり、一般の全プログラムに対する精度保証ではない。

### 17.3 評価指標

検出精度、既知違反の検出率、UNKNOWN率、要確認率、要求の構造化率、反例の再現率、解析時間、レビュー所要時間を記録する。

「一致率」だけを出さない。仕様全体の要求数、承認済み数、対応範囲数、判定済み数、未解析数を併記する。対応対象を絞ることで見かけの精度を上げない。

---

## 18. 実装の順序

| 段階 | 作るもの | 完了を確認する方法 |
|--|-------------------------------|----------------------------|
| M1 | IR型・Schema・手書きIR用の比較器・レポート | 正常IRと不具合IRから期待どおりの判定と反例が出る |
| M2 | Python ASTからCode IRを作る解析器 | 同じケースをPythonソースから入力してM1と一致する |
| M3 | MarkdownからSpec IR候補を作る抽出器と承認管理 | 曖昧な仕様を勝手に確定せず、承認済み入力で再現する |
| M4 | base/head比較・トレース取り込み・既存機構のアダプター | PR相当の入力からレビュー用Markdownを生成する |

最初に全文をLLMに読ませて比較コメントを生成する方式から始めない。比較器の意味と誤判定を先に固定し、その前段に抽出器を接続する。

グラフ可視化UI、Notion等からの取得、リポジトリへの自動コメント、グラフDB、増分解析、複数言語対応は、その後の拡張とする。

---

## 19. 納品物

実装コード、CLI、Schema、設定サンプル、デモ用仕様・コード、Golden fixtures、単体／統合試験、サンプルJSON・Markdownレポート、README、既知の制約一覧、PoC評価結果を納品する。

READMEには少なくとも、セットアップ、1件の検査の実行、仕様解釈の承認、対象関数の追加、bindingsの追加、トレース取り込み、UNKNOWNの読み方、CIでのadvisory運用を含める。

既存クラス図／シーケンス図生成器の実際の形式・言語が今回の仮案と異なる場合でも、Spec IRと比較器を作り直さず、コード側フロントエンドとインポーターを交換できる境界にする。

---

## 20. 実装者への依頼の要約

> Markdownの仕様とPythonのサービス関数から、条件・状態更新・コマンド呼び出し・結果を比較するPoCを実装してください。
>
> 共通の語彙で表したSpec IRとCode IRを用意し、要求に違反する経路条件と反例を検査器で求め、仕様とコードの根拠位置を付けてJSON・Markdownへ出力してください。
>
> 最初は注文キャンセルを題材に、認可、状態遷移、呼び出し条件・引数・回数、応答の違いを検出してください。
>
> 解析不能、仕様の曖昧さ、未記載の追加処理、確認境界の不足は、確定した違反や適合と区別してください。LLMは候補抽出に限定し、比較器は凍結したIRから再現可能に動作させてください。
>
> 完成の基準はグラフ表示の美しさではなく、既知の不具合を説明可能に検出し、確認できない部分を正しく残すことです。

---

## 参考資料

以下は利用候補技術の一次資料であり、本書で提案したシステムそのものが実現済み、または精度検証済みであることを示す資料ではない。

- [S1] Python公式：ast — Abstract syntax trees。https://docs.python.org/3/library/ast.html
- [S2] JSON Schema公式：Object / required / additionalProperties。https://json-schema.org/understanding-json-schema/reference/object
- [S3] Microsoft Z3 Guide：Z3 Python Introduction。https://microsoft.github.io/z3guide/programming/Z3%20Python/Introduction/
- [S4] pytest公式：API Reference / pytest_runtest_makereport。https://docs.pytest.org/en/stable/reference/reference.html
- [S5] Joern公式：Code Property Graph。https://docs.joern.io/code-property-graph/
- [S6] Dafny公式：Getting Started with Dafny: A Guide。https://dafny.org/dafny/OnlineTutorial/guide
