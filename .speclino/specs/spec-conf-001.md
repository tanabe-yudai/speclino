# SPEC-CONF-001 検査器の判定規則

SPEC-SYS-001 の区画のうち、意味検証のコア（仕様の変換の性質、旧形式の IR の語彙、コードの解析、仕様の義務の照合、
Finding の契約、判定の規則）を定める。全体の要求 R-SYS-07、R-SYS-09、R-SYS-11 を、検査器の側から詳しくする。
`src/speclino/spec/uml/grammar.md`（parser の正本）が「図とコードの対応」「判定の規則」として参照する先はこの仕様。

## 1. 目的と範囲

- 目的: 仕様に書かれた要求と、コードから決定的に取り出した振る舞いを、共通の語彙と型付きの条件式で照合し、
  「どの要求が、どの条件で、どう違反されるか」「何を確かめられ、何を確かめられなかったか」を出す
- 対象: 仕様の変換（`spec/uml/`）の性質、旧形式の IR（`domain/expr.py`・`domain/spec_ir.py`・`domain/code_ir.py`）、
  経路の解析（`analysis/`）、照合（`conformance/`）、Finding（`domain/finding.py`）、CLI `speclino check` の終了コード
- 範囲外: ゲートの決定と run の流れ（SPEC-GATE-002、SPEC-SYS-001 の図 gate-decision）、Mermaid の構文そのもの（grammar.md）、
  規約の検査器の個別の中身（SPEC-GATE-002 の 2 章）
- 目標ではないもの: 汎用プログラムの完全な正しさの証明、コードレビュー・テストの廃止、LLM の自由文による承認、図をきれいに描くこと

## 2. 仕様の変換の性質

仕様の機械可読な部分は、UML（Mermaid の厳格な部分集合）と旧形式（Spec IR v0.1 の JSON）の 2 つの入口を持ち、どちらも同じ IR
（`CompiledSpec`）に変換する。文章（prose）は判定に使わない。

| 性質 | 内容 |
| --- | --- |
| 決定的 | 同じ本文・同じ parser の版からは同じ IR とハッシュが出る。LLM・ネットワーク・時刻に依存しない |
| 全か無か | すべてのトークンが、IR の要素・明示的に無視する要素・エラーのどれかに分類される。消費されないトークンが 1 つでもあればエラー |
| 往復 | `render` は IR から正規形の Mermaid を作り、`compile(render(compile(s))) == compile(s)`（性質テストで保証） |
| 明示的な ID | 図の全要素に明示的な ID を必須にし、表示ラベルを ID に使わない |
| 描画と意味の分離 | Mermaid として描画できるかと、SpecLino が意味を読めるかは別の診断にする |
| 意味の確認は人 | エディタは原文と正規形を並べ、承認者は両方を見て承認する |
| 依存の制限 | 変換（`spec/uml/`）は LLM と adapters に依存しない。IR と manifest に parser の版を残す |

## 3. 旧形式の IR の語彙

### 3.1 式（Expr）

| 構成 | 形 |
| --- | --- |
| 参照 | `{op: "ref", path: "before.order.status"}`。名前空間 `before`（入口の状態）・`after`（終了時の状態）・`outcome`（モデル化した終了結果）が必須 |
| 定数 | `{op: "const", type, value}`。type は `boolean`・`integer`・列挙型名・opaque 型名 |
| 否定 | `{op: "not", arg}` |
| 2 項 | `eq`・`ne`・`lt`・`le`・`gt`・`ge`・`add`・`sub` |
| 多項 | `and`・`or`（args は 1 件以上。空配列は拒否） |

- 型は boolean・integer・列挙型・同一性だけを比べる opaque ID の 4 種。`lt`・`le`・`gt`・`ge` は integer だけ。opaque な値の集合を受け渡す場合、
  その中身を検査できたとは扱わない
- `outcome` の組込み変数は `status`（integer）と `kind`（列挙 `http_response`・`return`・`exception`・`unresolved`）だけ
- 参照の型は振る舞いの型環境から決める。比較・演算の型が合わなければ入力不正。式を Python の `eval` で実行しない

### 3.2 要求（Rule）と主張（Assertion）

| 欄 | 内容 |
| --- | --- |
| modality | `must`（主張が真であること）か `must_not`（主張全体が偽であること） |
| level | `mandatory`（既定）か `advisory` |
| applicability | この要求が当たる入口の条件（Expr） |
| assertion | `expression`（boolean の式の真偽）か `event_requirement`（下の表） |
| claim_scope | `memory_state`・`command_invocation`・`service_outcome`・`http_response`・`persisted_state` |
| approval_ref・source_refs | 承認の記録と、元の仕様の該当箇所 |

`event_requirement` は `event_kind`（`call`・`write`・`return`・`raise`）・`operation_or_target`・`phase`（`invoked`・`applied`・
`returned`・`raised`）・`count`（演算子 `eq`・`ge`・`le` と 0 以上の整数）・`arguments_equal`（引数名 → 式）を持つ。

- イベントの総数は、種別・操作・phase で選び、**引数で絞り込む前に**数える（正しい引数で 1 回・誤った引数でもう 1 回呼ぶ実装は「1 回」を満たさない）。
  `arguments_equal` の項目は、選んだすべてのイベントで一致しなければならない
- 禁止（「通知を呼ばない」）は、否定の読み違いを避けるため `must` と回数 0 の主張に正規化して書く
- 曖昧な「できる」は必須の成功に変換せず、人の確認に回す。抽出できなかった要求は `unextracted` に残し、分母から外さない

### 3.3 経路（Code IR の Path）

各経路は、入口の条件（guard）・状態の更新・順序つきのイベント（書き込み・呼出し・戻り・例外、`certainty` は must か may）・終了結果・
精度（`precision`）・未解決の要因（`unknown_causes`）・ソースの位置を持つ。精度は `exact_in_model`・`overapproximate`・`incomplete` の 3 つで、
`incomplete` の経路は未解決の要因を必ず持つ。未解決の要因の種類は `unsupported_syntax`・`unresolved_call`・`missing_contract`・
`unresolved_attribute`・`limit_depth`・`limit_paths`・`limit_time`・`unmodeled_outcome`・`other`。

## 4. 解析の不変条件

### 4.1 Python の部分集合

対象は Python 3.12 の構文で、次の部分集合だけを経路として解析する。

- `if` / `elif` / `else`、早期の `return`、認識済みの例外の `raise`、定数、ローカル変数、登録済みの単純な属性の参照と代入（プロパティや
  `__getattr__` を実行しないと型・値が決まらない属性は除く）、`and` / `or` / `not`、等値・不等値・大小の比較、整数の加減算、静的に解決できる直接の呼出し
- 拡張: `assert t, m` は「t が偽なら `AssertionError` で抜ける」分岐（`python -O` の実行はモデルの外）。分解代入は対象が名前だけのとき
  （右辺が同じ長さのタプルなら要素ごと、呼出しなら呼出しを解析し各名前は値の分からない結果）。`return None` は `return`
- 副作用の無い標準の関数（`len`・`isinstance`・`str`・`int`・`bool`・`repr`・`abs`・`json.dumps`・`json.loads`。組込みの名前はモジュールで
  再束縛され得ないとき）は呼出しを未解決にしない（結果の値は追わない）
- `await f(...)` は f の呼出しとして扱う（await の間に他のタスクが入口の値を書き換えることはモデルの外）。`async def` を await せずに呼ぶ、
  同期関数の結果を await する、呼出し以外を await する、はどれも未解決。`async with` / `async for` は `with` / `for` と同じく不透明な領域
- フレームワークの応答は明示したアダプター（FastAPI の `HTTPException(status_code=定数)` と `Response(status_code=定数)`）だけを理解する。
  サービス関数の返り値だけを見た結果は「サービス層の結果」で、HTTP の応答を確かめたとは表示しない
- 対象外（ループ、再帰、動的ディスパッチ、任意のデコレーター、非同期の並行、ORM の全体、一般の別名、永続化の成否など）に当たったら、
  黙って落とさず、影響する要求を UNKNOWN にする

### 4.2 守る意味

- 早期の `return` より後のコードを実行済みにしない。実行され得ない分岐の呼出しを必須の処理の証拠にしない
- 副作用の有無だけでなく「どの条件で・どの引数で・何回」を保つ
- 前の状態（before）と後の状態（after）を混同しない。状態を変えた後に評価した条件は変えた後の値で計算する
- コメント・docstring・関数名をコードの振る舞いの根拠にしない（対応づけの候補のヒントには使える）
- 未知の外部関数を副作用なしと仮定しない。呼出しは深さの上限までローカルに要約し、外部は承認済みの契約、未知は未解決の要因にする
- 解析は対象のソースを読むだけで、対象のモジュールを import・実行しない。解析には時間・大きさの上限を置く
- 上限は既定で深さ 2・経路 64・ソルバー 500ms、選択的に引き上げて最大で深さ 4・経路 256・1000ms（`analysis/limits.py`）。
  上限を超えたら、実行され得る経路を捨てて VERIFIED_IN_SCOPE を出さず、影響の範囲を UNKNOWN にする

## 5. 図とコードの対応

図の名前とコードの対応は次の順で決める。どの段も決定的で、決まらないもの・一意でないものは写さず UNKNOWN にする
（偽の VERIFIED_IN_SCOPE / VIOLATED を出さない）。

| 対象 | 規則 |
| --- | --- |
| モジュール（`<<module>>`） | `bindings.yaml` の `modules`（名前 → 完全修飾のモジュール名）、無ければ `<名前>.py` / `<名前>/__init__.py` が source_roots に 1 つだけのとき。操作 = モジュール直下の `def` / `async def`。候補が複数、または動的な定義（`__getattr__`・`exec`・`globals()`・同名の代入や import・条件付きの定義）があれば UNKNOWN |
| 図の操作 `<C>.<m>` | `bindings.yaml` の operations（キーの一致 → シンボルの末尾の一致）。無ければ名前の一致: `<C>` をクラス図のステレオタイプに従って一意なクラス（モジュール）として探し、`<m>` が定義されていればその完全修飾名を操作にする（操作 ID は図の名前）。末尾の一致が複数なら名前の一致に進まず UNKNOWN |
| 入口 `%%@ entry` | `bindings.yaml` の behaviors（キー → entrypoint の末尾の一致）、無ければ同じ名前の一致で入口を作る。名前の一致で作った入口は変数の対応を持たない |
| 呼出し | 候補は「別名を展開した属性チェーン」（`self.registry.execute`）と「宣言型で解決した完全修飾名」（`pkg.mod.ToolRegistry.execute`）。bindings のシンボルはどちらで書いてもよい。候補が別々の操作に当たれば未解決（その操作に触れる義務は UNKNOWN） |
| ガードの変数 | 型環境は仕様の domain にクラス図の列挙と属性の変数（`triage.has_findings`）を足したもの。値の対応は behavior の `variables` に図の変数名で書く（旧形式の名前と並べてよい）。ガードが対応の無い変数を参照すれば UNKNOWN（経路条件と無関係な自由変数になり偽の判定になるため）。1 つの実装に domain の変数が 2 つ当たれば解析しない |
| 遷移 | 状態の属性を `variables` に持つ behavior を検査する。持たない behavior が、到達し得るモジュール（import を推移的にたどった範囲）で同じ名前の属性に代入する（`x.<名前> = …`・`setattr`）なら、その behavior について UNKNOWN（図に無い遷移を見逃さない） |
| 名前の一致で写した操作の効果 | bindings で宣言した操作は効果を仮定しない（契約が無ければ呼出しのイベントだけ）。名前の一致で写した操作は、イベントを出したうえで本体も展開する（メソッドなら `self` に受け手を束縛）。展開できなければ効果を確定できない未解決（UNKNOWN） |

### 5.1 クラス図の関係（`analysis/relation_check.py`）

- 両端の解決はモジュール・構造の検査と同じ（解決できなければ UNKNOWN、確定的に無ければ VIOLATED）。コードの名前は、そのモジュールの import と
  直下の定義で完全修飾名にし、再エクスポート（`pkg/__init__.py` の `from .mod import X`）はたどる
- 継承 `A <|-- B`: B の class 文の基底（`Base[T]` は `Base`）に A がある
- 所有（`*--`・`o--`・`-->`）: A の属性の型に B がある。根拠はクラス本体の注釈（dataclass・pydantic のフィールド）、メソッドの中の `self.x: T`・
  `self.x = B(...)`・`self.x = <注釈つき引数>`・`self.x = <戻り値の注釈つきの関数>(...)`、一意な基底の同じ根拠。A が `<<module>>` なら直下の変数の注釈・代入。
  注釈の中の `B | None`・`list[B]`・`Sequence[B]` も所有とみなし、`Callable[...]` の中は見ない。3 種の違い（多重度・寿命）は照合しない
- 依存 `A ..> B`: A のモジュール（クラスなら定義のあるモジュール）が B（の中の名前）を import するか名前で参照する。見つからないとき、`__getattr__`・
  `setattr`・`exec`・`import *`・動的な import・型の分からない属性の値（注釈の無い引数、戻り値の注釈の無い関数の結果など）・解決できない同名の参照・
  一意でない基底があれば UNKNOWN、無ければ VIOLATED（見たモジュールを Finding に書く）。注釈の無いリテラル・コンテナ（`[]`・`{}`）とリポジトリ外の
  関数・クラスの呼出しは B とみなさない
- 名前の解決: モジュールで import 以外にも束縛される名前（代入・`del`・`global`・同名の定義や引数。同じ先の import の重複は除く）は解決せず、同名なら
  UNKNOWN の要因。基底は解決した完全修飾名のクラスをたどる
- 到達性: 所有は has-a の主張なので定数以外の条件の下の代入も根拠にするが、静的に到達しない代入（定数が偽の `if` / `while` の本体、定数が真の `if` の
  `else`、`return`・`raise`・`continue`・`break` の後）は根拠にしない
- デコレーター: 完全修飾名で解決する。`staticmethod`・`classmethod` のメソッドは根拠にしない。`property`・`functools.cached_property`・
  `abc.abstractmethod`・`typing.override`・同じクラスの `x.setter` 以外の（解決できない）デコレーターのメソッドは根拠にせず UNKNOWN の要因にする

### 5.2 宣言型と前提

- 宣言型（`analysis/static_types.py`）の根拠は、引数の注釈・メソッドの `self`・`__init__` の中の `self.a = <注釈つき引数>` / `T(...)` / `self.a: T = ...`
  （すべて同じ型のときだけ。ほかのメソッドやクラス本体で書かれる属性は型なし）・戻り値の注釈だけ。宣言型で解決したメソッドは操作の照合にだけ使い、
  名前の一致で写した操作を除いて本体を展開・走査しない
- 自明な構築: 基底・デコレーター（メソッドのものを含む）が無く、`__init__` が `self.x = <引数か定数>` だけのクラスの構築は副作用なしとし、
  イベントも未解決の要因も作らない
- 前提（Finding の観測境界にも同じ前提が入る）: 注釈は実行時の値と整合する（mypy strict で保証されたリポジトリを前提にする）。実行時のサブクラスの
  上書きは区別しない。属性の読み出しは純粋（プロパティ・`__getattr__` の効果は見ない）。自明な構築・純粋な標準関数の引数も、名前・定数・属性の
  読み出しだけのときに限る（呼出し・await・添字・演算子があれば未解決）

## 6. 仕様の義務の照合

図（シーケンス・状態遷移・アクティビティ）は有限オートマトン（`TraceObligation`）に変換し、経路ごとのイベント列（`sequence` 順）との積を取る。
ガードは経路条件と合わせて Z3 で充足可能性を確かめる。VIOLATED と VERIFIED_IN_SCOPE を出してよいのは `precision=exact_in_model` の経路だけで、
不完全な経路・不透明な領域・一般の `while`・展開できない `for`・経路数の上限の超過・対応の見つからない要素は UNKNOWN にする。

| 義務 | 検査 | open-world との関係 |
| --- | --- | --- |
| 構造（クラス図） | クラスとメソッドがあり、引数の数と名前が合うか（AST。import しない） | 図に無いクラスやメソッドがあっても問題にしない |
| 関係（クラス図の関係線） | 継承・所有・依存の根拠がコードにあるか（5.1 節） | 図に無い関係があっても問題にしない |
| 順序（シーケンス図） | ガードが成り立つ経路のイベント列が、図の呼出し列を部分列として含むか。`loop` は 0 回以上 | 図に無い呼出しが間にあっても違反にしない |
| 遷移（状態遷移図） | 状態の属性への代入ごとに、経路上の前の状態から許された遷移か | 宣言した属性だけを見る |
| 包含（アクティビティ図） | 経路のイベント列が図のどれかの道筋に沿うか | `closed: true` の図だけ図に無い処理を VIOLATED、それ以外は REVIEW |

旧形式の要求は、要求と経路の組ごとに次の式の充足性を Z3 で調べる。充足可能ならモデルの上の反例がある。

```text
承認済みの入口の仮定 AND 型・状態のドメイン AND 要求の適用条件 AND 経路の条件 AND NOT 要求の保証
```

ノードの数・名前・形の類似ではなく、要求に違反する入力と経路があるかを見る。

## 7. 判定の規則

| verdict | 必要な条件 |
| --- | --- |
| VERIFIED_IN_SCOPE | 要求が承認済みで、適用できる入力があり、関係する経路をモデルの中で網羅し、違反の式がすべて UNSAT で、未解決の影響が無い |
| VIOLATED | 解析が厳密（`exact_in_model`）な経路に反例がある、または検証できる実行の記録で違反を観測した |
| UNKNOWN | 対応外の構文・未解決の呼出し・仮定や契約の不足・ソルバーの unknown やタイムアウト・上限・観測境界の不足で決められない |
| REVIEW | 仕様の解釈、語彙の対応、仕様に無い追加の処理、過大近似の経路の反例など、人の判断が要る |

- ソルバーの unknown とタイムアウトを違反なしに読み替えない（UNKNOWN、分類 `solver_limit`）
- 過大近似（`overapproximate`）の経路での SAT は、実行され得ない反例かもしれないので REVIEW（分類 `potential_violation`）にし、確定した違反に上げない。
  may のイベントのために回数が決まらないときも同じ
- 適用条件が充足不能（適用できる入力が無い）なら、空虚な成功として VERIFIED_IN_SCOPE を出さず REVIEW（分類 `vacuous_applicability`）にする
- 適用条件を満たすのにどの経路にも入らない入力があるとき、関係する経路が 1 本も無いときは UNKNOWN（分類 `unsupported_analysis`）
- 呼出しが無いことは、その経路のイベントの抽出が完了したときだけ「呼出しなし」の証拠になる。不完全な経路では不足を根拠にしない（UNKNOWN）。
  何かが存在することを根拠にする違反（`forbidden_effect`）は不完全な経路でも VIOLATED にできる
- 反例は記号的（`symbolic`）か観測した（`observed`）かを区別し、記号的な反例を再現したバグと表現しない。すべての判定に仮定と解析の範囲を添える
- 成功条件を失わない: 拒否の条件（「管理者か所有者だけ」）だけでは全員を拒否する実装を許すので、「認可され・前提を満たせば成功する」要求も独立に持ち、両方向から検査する
- 未記載と禁止を分ける（open-world が既定）: 必須の処理が無い・明示的に禁止した処理があるは、根拠が十分なら VIOLATED。仕様に書かれていない追加の
  呼出しは REVIEW（分類 `undocumented_behavior`）。閉じた一覧を求めるなら、仕様にその閉包性を明示して承認する
- 未承認・承認の後に本文が変わった要求は、古い IR で成功させず REVIEW（分類 `stale_approval`）
- severity と verdict は分ける。重大な要求でも解析できなければ UNKNOWN で、重大度を理由に違反としない
- 1 つの要求に複数の Finding があるときの集約の優先順位は VIOLATED > REVIEW > UNKNOWN > VERIFIED_IN_SCOPE
- 業務の効果と呼出しを分ける: 「在庫の復元のコマンドを呼んだ」と「DB の在庫が復元された」は別の主張で、メモリ上の状態への代入はコミットの証拠ではない。
  永続化の観測か検証済みの契約が足りなければ、業務の要求の全体は UNKNOWN にする

## 8. 静的な判定とテストでの観測

静的な判定とテストで観測した事実を 1 つの緑のチェックにまとめない。Finding は verdict と別の軸に `test_observation`
（`OBSERVED`・`NOT_OBSERVED`・`CONTRADICTED`）を持つ。

- テストで違反を観測したら、観測した反例として VIOLATED にし `CONTRADICTED` を記録する
- テストがすべて成功しても verdict は変えず `OBSERVED` を記録するだけ（「モデルの中で静的に確かめた」と「テストで観測した」は別）
- テストの error・skipped・欠落・タイムアウトは観測にならず、verdict を変えない（もとの UNKNOWN・REVIEW のまま残り、次の手を `next_steps` に足す）
- 1 つの経路（管理者）のテストだけを観測しても、別の経路（所有者）を観測済みにしない。モックへの呼出しはモックへの呼出しとして扱い、
  実 DB の更新や実サービスの成功を観測した扱いにしない

## 9. Finding の契約

| 欄 | 内容 |
| --- | --- |
| 識別 | `id`・`requirement_id`・`behavior_id` |
| 判定 | `verdict`（4 値）・`category`・`severity`（`high`・`medium`・`low`・`info`）・`assessment_outcome`（LLM 支援だけ） |
| 説明 | `message`・`expected`・`actual`・`counterexample`（`kind` は `symbolic` か `observed`）・`next_steps` |
| 根拠 | `evidence_refs`・`assumptions`・`claim_scope`・`analysis_scope`・`code_refs`・`spec_refs` |
| 差分と観測 | `base_head_status`（`new`・`existing`・`resolved`・`unchanged`）・`test_observation` |

category は `missing_effect`・`forbidden_effect`・`constraint_mismatch`・`state_mismatch`・`outcome_mismatch`・`argument_mismatch`・
`cardinality_mismatch`・`undocumented_behavior`・`unsupported_analysis`・`ambiguous_spec`・`stale_approval`・`vacuous_applicability`・
`potential_violation`・`solver_limit`・`conformant`・`missing_finding`・`scope_changed` の 17 個。

- severity は verdict と level から決める: VERIFIED_IN_SCOPE は `info`、VIOLATED は mandatory で `high`・advisory で `medium`、それ以外は mandatory で `medium`・advisory で `low`
- 「存在しない処理」の根拠を架空の 1 行に結び付けない。対象の経路と解析が完了した範囲を根拠にする
- すべての Finding から元の仕様とコードに戻れる。同じ凍結した入力では、時刻と run の ID を除き、結果・順序・Finding の ID が同じになる

## 10. CLI の終了コード（`speclino check`）

| コード | 意味 |
| --- | --- |
| 0 | 検査が完了し、ゲートの条件に当たらない（advisory のときはゲートが fail でも 0）。UNKNOWN が 0 件という意味ではない |
| 1 | 検査が完了し、`--blocking` を指定していてゲートが fail |
| 2 | 入力の不正・Git の失敗・実行基盤の失敗などで、求めた検査を完了できなかった |

- 既定は advisory（`--blocking` を付けたときだけ 1 を返す）。`--fail-on` の既定は VIOLATED・UNKNOWN・REVIEW
- 仕様の未承認や対応外の構文のような部分的な判定不能は Finding に残し、JSON の破損や入力のスナップショットの取得の失敗のような実行不能（2）と区別する。
  レポートには verdict ごとの件数を必ず残す
- CLI はソース・仕様・承認の記録を書き換えず、実在しない承認者や承認の記録を作らない

## 11. 要求

| ID | level | 要求 | 形式化 |
| --- | --- | --- | --- |
| R-CONF-01 | mandatory | 仕様の変換は 2 章の性質（決定的・全か無か・往復・明示的な ID）を持ち、LLM と adapters に依存せず、IR と manifest に parser の版を残す | - |
| R-CONF-02 | mandatory | 式は 3.1 節の 13 の演算と before・after・outcome の名前空間、boolean・integer・列挙・opaque ID の 4 つの型だけを持ち、型の合わない比較・演算と空の and・or は入力不正にし、式を eval しない | - |
| R-CONF-03 | mandatory | 要求の主張は expression と event_requirement の 2 種で、イベントの総数は引数で絞る前に数え、arguments_equal は選んだすべてのイベントで一致を求め、禁止は回数 0 の主張に正規化する | - |
| R-CONF-04 | mandatory | 経路の解析は 4.1 節の部分集合だけを解析し、対応外の構文・未解決の呼出し・上限の超過は経路を incomplete にして未解決の要因を残し、影響する要求を UNKNOWN にする | - |
| R-CONF-05 | mandatory | 経路の解析は早期の return の後を実行済みにせず、到達し得ない分岐の呼出しを証拠にせず、before と after を混同せず、コメント・docstring・関数名を振る舞いの根拠にしない | - |
| R-CONF-06 | mandatory | 解析は対象のモジュールを import・実行せず、上限は既定で深さ 2・64 経路・500ms、最大で深さ 4・256 経路・1000ms とし、上限を超えて捨てた経路があれば VERIFIED_IN_SCOPE を出さない | - |
| R-CONF-07 | mandatory | 図の名前とコードの対応は 5 章の順で決定的に決め、決まらない・一意でないものは写さず UNKNOWN にする | - |
| R-CONF-08 | mandatory | クラス図の関係は 5.1 節の根拠でだけ照合し、根拠を確定的に否定できたときだけ VIOLATED、動的な要素があれば UNKNOWN にする | - |
| R-CONF-09 | mandatory | 仕様の義務は 6 章の表で照合し、VIOLATED と VERIFIED_IN_SCOPE は exact_in_model の経路からだけ出し、図に無い処理は closed の図だけで VIOLATED、それ以外は REVIEW にする | - |
| R-CONF-10 | mandatory | 旧形式の要求は「仮定 ∧ ドメイン ∧ 適用条件 ∧ 経路の条件 ∧ ¬保証」の充足性で判定し、ソルバーの unknown とタイムアウトは UNKNOWN（solver_limit）にする | - |
| R-CONF-11 | mandatory | VERIFIED_IN_SCOPE は 7 章の条件をすべて満たすときだけ出し、適用条件が充足不能なら REVIEW（vacuous_applicability）にして空虚な成功を出さない | - |
| R-CONF-12 | mandatory | 過大近似の経路の SAT と may のイベントで回数が決まらないものは REVIEW（potential_violation）にし、確定した違反に上げない | - |
| R-CONF-13 | mandatory | 反例は symbolic と observed を区別し、すべての判定に仮定と解析の範囲を添える | - |
| R-CONF-14 | mandatory | 未記載の追加の呼出しは REVIEW（undocumented_behavior）、明示的に禁止した処理と必須の処理の欠落は根拠が十分なら VIOLATED とし、既定は open-world にする | - |
| R-CONF-15 | mandatory | 成功の条件は拒否の条件と独立の要求として持ち、両方向から検査する | - |
| R-CONF-16 | mandatory | severity と verdict は分け、重大度を理由に UNKNOWN を違反にしない | - |
| R-CONF-17 | mandatory | テストの観測は test_observation の別の軸に記録し、成功では verdict を変えず、観測した違反だけを observed の反例として VIOLATED にする | - |
| R-CONF-18 | mandatory | Finding は 9 章の欄と 17 の category を持ち、すべての Finding から元の仕様とコードに戻れ、同じ凍結した入力では時刻と run の ID を除き結果と Finding の ID が同じになる | - |
| R-CONF-19 | mandatory | CLI の終了コードは 10 章の 0・1・2 で、既定は advisory とし、部分的な判定不能は Finding に、実行不能は終了コード 2 にして区別する | - |
| R-CONF-20 | mandatory | 呼出しと業務の効果、メモリ上の代入と永続化を区別し、観測や検証済みの契約が足りない業務の要求は UNKNOWN にする | - |

## 観測境界

- この仕様は図を持たず、要求はすべて未形式化（`-`）。検査器そのものの正しさは、Golden fixtures（正しい実装、状態・応答・認可・回数・引数の誤り、
  早期 return の後や到達しない分岐にだけある呼出し、コメントだけの実施、等価な書き換え、未解決の getattr、対応外のループ、上限、充足不能な適用条件、
  承認後の本文の変更、同じ入力の再実行）と単体・性質テスト（`tests/`）で確かめる
- 現在の解析器が出す精度は `exact_in_model` と `incomplete` だけで、`overapproximate` は手書きの Code IR（`--code-ir`）でだけ現れる
- `test_observation` の `NOT_OBSERVED` は型とレポートの表示にあるが、今のコードで代入する経路は無い
- 実行トレースとの照合、`loop` の回数の義務、非同期のメッセージ・`par`、Python 以外の言語の構造・順序の検査はこの仕様の範囲外
