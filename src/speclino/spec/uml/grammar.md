# SpecLino が受け付ける Mermaid の部分集合（parser `uml-0.1`）

この文書が parser（`src/speclino/spec/uml/`）の正本。ここに無い構文はすべて**エラー**にする。
「明示的に無視する要素」（7 章）に載っているものだけが IR に入らずに通る。
設計: `design/2026-10-05-team-knowledge-registry.md` 4 章。

表記: `{ x }` は 0 回以上、`[ x ]` は省略可、`"x"` は字句そのもの、`NL` は行末。
Mermaid の 1 文は 1 行。行をまたぐ文は無い（ブロックの `alt … end` 等は行の並びで表す）。

## 1. 文書（Markdown）

```ebnf
document      = { prose | mermaid_block | other_fence | req_table } ;
mermaid_block = "```mermaid" NL { header_line } { comment_line | blank } diagram "```" NL ;
other_fence   = "```" lang NL { any_line } "```" NL ;      (* 中身は読まない *)
req_table     = req_header NL req_sep NL { req_row NL } ;
```

- コードフェンスは CommonMark どおりに認識する: ``` か `~~~` を 3 つ以上。字下げ・引用（`>`）・リストの中の開きも見る。
  閉じは開きと同じ文字が同数以上で、後ろに言語名を持たない行（```` ``` foo ```` では閉じない）
- 言語名の最初の語が大文字小文字を無視して `mermaid` のフェンスは、**正規形（行頭の ```` ```mermaid ````、字下げ・引用・リストなし、
  追加の語なし、` ちょうど 3 つ）だけ**を受け付ける。それ以外（`~~~mermaid`、```` ```mermaid title ````、字下げ、```` ````mermaid ````、
  引用、```` ```Mermaid ````）は `E_UNSUPPORTED_SYNTAX`。描画はされるのに検査から漏れる図を作らないため
- HTML コメント（`<!--` 〜 `-->`）の中の mermaid フェンスは `E_UNSUPPORTED_SYNTAX`、要求の表は `E_REQ_TABLE`
- `mermaid` 以外の言語のブロックは中身ごと本文として扱い読まない
- 閉じない `mermaid` ブロックはエラー（`E_UNCLOSED_BLOCK`）
- 本文（prose）は IR に入らない。トークンの網羅の対象は `mermaid` ブロックと要求の表

### 1.1 宣言の行（ヘッダー）

```ebnf
header_line = "%%@" key ":" value NL ;
key         = "id" | "entry" | "closed" | "attribute" ;
diagram_id  = letter { letter | digit | "_" | "-" } ;
operation   = NAME "." NAME ;                            (* <クラス名>.<メソッド名> *)
```

| key | 値 | 必須 | 使う図 |
| --- | --- | --- | --- |
| `id` | `diagram_id`。文書の中で一意 | 全図で必須 | 全図 |
| `entry` | `operation` | 必須 | シーケンス図・アクティビティ図 |
| `closed` | `true` / `false`（既定 `false`） | 任意 | アクティビティ図だけ |
| `attribute` | 名前空間の無い変数名（例 `order.status`） | 必須 | 状態遷移図 |

- `%%@` で始まる行は Mermaid ではコメントなので描画に影響しない。図の種類の行より前に置く
- 未知の key、同じ key の重複、図の種類に合わない key はエラー（`E_HEADER`）
- `%%{ ... }%%`（Mermaid の directive）はエラー（`E_UNSUPPORTED_SYNTAX`）

## 2. 字句

```ebnf
NAME   = ( letter | "_" ) { letter | digit | "_" } { "." ( letter | "_" ) { letter | digit | "_" } } ;
INT    = digit { digit } ;
ARROW  = "-->>" | "->>" | "-->" | "--x" | "--o" | "-.->" | "==>" | "---" | "-x" | "-)" | "--)" | "->" | "--" | "<|--" | "..>" ;
PUNCT  = "[" | "]" | "(" | ")" | "{" | "}" | "|" | ":" | "," | "*" | "<<" | ">>"
       | "==" | "!=" | "<=" | ">=" | "<" | ">" | "+" | "-" | "#" | "~" ;
TEXT   = 非 ASCII 文字の連続 ;
OTHER  = 上のどれにも当たらない 1 文字 ;
```

- `letter` は ASCII 英字だけ。要素 ID は `NAME` のうちドットを含まないもの
- 字句解析は全域で失敗しない（どんな文字列もトークン列にする）。意味を決めるのは各図の parser
- 記号は最長一致。`ARROW` のうち受け付けるのは図ごとに決めたものだけで、ほかは `E_UNKNOWN_ARROW`

## 3. ガード（`Expr`）

```ebnf
guard    = or_expr ;
or_expr  = and_expr { "or" and_expr } ;
and_expr = not_expr { "and" not_expr } ;
not_expr = "not" not_expr | cmp ;
cmp      = arith [ ( "==" | "!=" | "<" | "<=" | ">" | ">=" ) arith ] ;
arith    = atom { ( "+" | "-" ) atom } ;
atom     = ref | int | "true" | "false" | enum_lit | "(" or_expr ")" ;
ref      = ( "before" | "after" | "outcome" ) "." NAME ;   (* 1 つの NAME トークン *)
int      = [ "-" ] INT ;
enum_lit = EnumType "." VALUE ;                             (* 1 つの NAME トークン、ドットは 1 つ *)
```

- 参照は `before.` / `after.` / `outcome.` の名前空間が必須（入口の引数は `before.`）。名前空間の無い名前・日本語・
  文字列はエラー（`E_GUARD_SYNTAX`）。自然言語のガードはここで落ちる
- 列挙値は `型名.値`（例 `OrderStatus.PENDING` → `Const(type="OrderStatus", value="PENDING")`）。型はクラス図の
  `<<enumeration>>` か `domain.enums` に宣言されていて、値はその列挙の要素でなければならない（`E_UNKNOWN_ENUM`）
- 比較の連鎖（`a < b < c`）はエラー。`and` / `or` の連続は 1 つの `Nary` に平らにする。括弧はノードを作らない
- 最上位が算術式・整数・列挙値のガードはエラー（`E_GUARD_TYPE`）。`domain` が渡されたときは
  `conformance.typing.require_boolean` で型検査する（参照は `domain.variables` に必要）
- 正規形の表記（`render`）: 2 項演算の左右と `Nary` の要素は、参照・定数以外を括弧で囲む。`not` は `not (x)`

## 4. クラス図

```ebnf
class_diagram = "classDiagram" NL { class_stmt } ;
class_stmt    = "direction" DIR NL
              | "class" NAME NL
              | "class" NAME "{" NL { member } "}" NL ;
member        = "<<" "enumeration" ">>" NL
              | [ visibility ] NAME NL                         (* 列挙値（enumeration のとき） *)
              | [ visibility ] attr_type NAME NL               (* 属性 *)
              | [ visibility ] NAME "(" [ NAME { "," NAME } ] ")" NL ;   (* 操作 *)
attr_type     = "int" | "bool" | "str" | "opaque_id" | NAME ;  (* NAME は列挙型 *)
visibility    = "+" | "-" | "#" | "~" ;
DIR           = "TB" | "TD" | "BT" | "LR" | "RL" ;
```

- **要素 ID はクラス名**（クラス名は識別子として厳格で、表示ラベルではない）。クラス名は文書全体で一意（`E_DUPLICATE_ID`）
- `<<enumeration>>` のクラスは列挙値だけを持つ（属性・操作はエラー）。値は 1 つ以上で重複なし。
  `domain.enums` にも同名があれば値の集合が一致しなければならない（`E_ENUM_MISMATCH`）
- 属性の型 `NAME` は列挙型として解決する（文書のクラス図か `domain.enums`）。解決できなければ `E_UNKNOWN_TYPE`
- 操作の引数は名前だけ（型・既定値・戻り値の型はエラー）
- 操作を 1 つ以上持つクラスごとに `StructureObligation`（id `<図 ID>#<クラス名>`）を作る
- 関係線（`<|--`、`-->`、`..>` など）、`Class : member` の書式、注記、`class X["label"]`、ジェネリクスはエラー

## 5. シーケンス図

```ebnf
sequence = "sequenceDiagram" NL { seq_stmt } ;
seq_stmt = "participant" NAME [ "as" NAME ] NL
         | NAME "->>" NAME ":" "[" ID "]" NAME [ "(" [ NAME { "," NAME } ] ")" ] NL   (* 呼出し *)
         | NAME "-->>" NAME ":" "[" ID "]" { any_token } NL                         (* 応答 *)
         | "alt" "[" ID "]" guard NL { seq_stmt }
           { "else" "[" ID "]" [ guard ] NL { seq_stmt } } "end" NL
         | "opt" "[" ID "]" [ guard ] NL { seq_stmt } "end" NL
         | "loop" "[" ID "]" NL seq_stmt { seq_stmt } "end" NL ;
```

- `participant X as Y` の `X` はメッセージで使う別名、`Y` が**クラス名**。`participant X` はクラス名 `X`。
  メッセージの参加者は宣言済みでなければならない（`E_UNDECLARED_PARTICIPANT`）
- 呼出し `A->>B: [m1] reserve(order)` は `CallLabel(operation="<B のクラス名>.reserve")`。送り手 `A` と引数名は IR に入らない
- 応答 `-->>` は IR に入らない（ID は必須で、ID の重複検査の対象。要求の表から参照するとエラー）
- 入口の操作（`%%@ entry`）ごとに `TraceObligation(mode="contains_subsequence")`
- `else` のガードを省けるのは最後の `else` だけ。その意味は「それより前のガードのどれも成り立たない」
- `loop` は 0 回以上の繰り返し（ガードを書かない。本体は空にできない）
- 対応しない構文（`par`、`and`、`critical`、`option`、`break`、`rect`、`Note`、`actor`、`activate`、
  `autonumber`、`box`、`create`、`destroy`、`title` など）はエラー（`E_UNSUPPORTED_SYNTAX`）

### 5.1 オートマトンへの変換（決定的）

状態は `s0, s1, …` を作った順に振る。初期状態 `s0`、受理状態は最後の状態 1 つ。

| 構文 | 遷移（id: 種類 [ガード]） |
| --- | --- |
| 呼出し `[m]` | `cur → new` に `m: call` |
| `alt [g1] e1 … else [g2] e2 … end` | 各分岐 i: `cur → bi` に `gi: ε [ei]`、分岐の終わり `→ J` に `gi.join: ε`。省いたガードは前のガードの否定。最後が無条件の `else` でなければ `cur → J` に `g1.none: ε [どのガードも成り立たない]` |
| `opt [o] e … end` | `cur → b` に `o: ε [e]`、終わり `→ J` に `o.join: ε`、`cur → J` に `o.skip: ε [not e]`（ガードが無ければ無条件） |
| `loop [l] … end` | `cur → h` に `l: ε`、本体の終わり `→ h` に `l.back: ε`、`h → J` に `l.exit: ε` |

利用者の ID はドットを含まないので、`.join` などの派生 ID と衝突しない。状態を作る順は
「分岐の頭 → 分岐の本体 → 合流」の順（`alt` は全分岐の後で合流 `J` を作る）。

## 6. 状態遷移図

```ebnf
state_diagram = ( "stateDiagram-v2" | "stateDiagram" ) NL { state_stmt } ;
state_stmt    = "direction" DIR NL
              | "[*]" "-->" NAME NL                                      (* 初期状態。ちょうど 1 つ *)
              | NAME "-->" "[*]" NL                                      (* 終了。無視する *)
              | NAME "-->" NAME ":" "[" ID "]" [ NAME ] [ "[" guard "]" ] NL ;   (* 遷移: ID イベント ガード *)
```

- `StateMachine.id` は図 ID、`attribute` は `%%@ attribute`。状態の並びは「初期状態 → 遷移に現れた順」
- 終了の遷移（`X --> [*]`）は遷移の関係の検査に使わないので IR に入れない（7 章）。`X` は他の行で現れる状態でなければならない
- 状態名と遷移 ID は同じ名前空間で一意。`state`、複合状態、`<<choice>>`、注記はエラー

## 7. アクティビティ図（flowchart）

```ebnf
activity  = "flowchart" DIR NL { act_stmt } ;
act_stmt  = node NL
          | node "-->" "|" "[" ID "]" [ guard ] "|" node NL ;   (* 辺。1 行に 1 本 *)
node      = ID [ shape ] ;
shape     = "[" operation "]" | "(" operation ")" | "(" "[" operation "]" ")"   (* 処理 *)
          | "{" { any_token } "}"                                               (* 判断 *)
          | "(" "(" "start" ")" ")" | "(" "(" "end" ")" ")" ;                    (* 開始・終了 *)
```

- 形（`shape`）は ID ごとに 1 回だけ宣言する。2 回目の宣言は同じ内容でも `E_DUPLICATE_ID`（ラベルだけ違う同じ ID を拒否）。
  形の無いノードの参照は宣言済みでなければならない（`E_UNDECLARED_NODE`）
- 開始はちょうど 1 つで入る辺が無い。終了は 1 つ以上で出る辺が無い。開始以外のノードは入る辺を、終了以外は出る辺を持つ
- 辺の矢印は `-->` だけ。`---`、`-.->`、`==>`、`--x`、`--o`、`A -- text --> B`、`A --> B --> C`、`&` はエラー
- `subgraph`、`click`、`style`、`classDef`、`class`、`linkStyle`、`graph` はエラー
- オートマトン: 状態 = ノード ID（最初に現れた順）、初期 = 開始、受理 = 終了。辺 `A -->|[e] g| B` は
  `A → B` に `e: call(B の操作) [g]`（B が処理ノードのとき）か `e: ε [g]`（判断・終了のとき）
- `TraceObligation(mode="included_in", closed=%%@ closed)`

## 8. 要求の表

```ebnf
req_header = "|" "ID" "|" "level" "|" "要求" "|" "形式化" "|" ;
req_sep    = "|" { "-" | ":" | " " | "|" } ;
req_row    = "|" REQ_ID "|" ( "mandatory" | "advisory" ) "|" TEXT "|" [ ref { "," ref } | "-" ] "|" ;
ref        = diagram_id "#" ID | "ir:" RULE_ID ;
```

- 見出しに `形式化` を含む表、または最初の列が大文字小文字を無視して `ID` の表（先頭の `|` が無い表・引用の中の表を含む）は
  要求の表の候補とし、行頭の `|` から始まり見出しが上と完全に一致し区切り行が続くものだけを受け付ける。ほかは `E_REQ_TABLE`
- 要求 ID は一意。本文の列に `|` は書けない
- `図ID#要素ID` は 9 章の参照できる要素、`ir:要求ID` は旧形式の IR の `Rule.id` を指す。無ければ `E_REF_UNKNOWN`
- `形式化` が空か `-` の行は**未形式化**として残す（`formalized_by=[]`）
- 義務の level: その義務の要素を参照する行が 1 つ以上あり、すべて `advisory` なら `advisory`。それ以外は `mandatory`
  （義務の要素: 構造 = そのクラス、順序・包含・遷移 = その図の要素すべて）

## 9. 参照できる要素

| 図 | 要素 ID |
| --- | --- |
| クラス図 | クラス名 |
| シーケンス図 | 呼出しの ID、`alt`/`else`/`opt`/`loop` の ID（応答は不可） |
| 状態遷移図 | 状態名、遷移の ID |
| アクティビティ図 | ノード ID、辺の ID |

## 10. 明示的に無視する要素（IR に入らないが、エラーにもしない）

| 要素 | 理由 |
| --- | --- |
| 空行、`%%` で始まる行（`%%@`・`%%{` を除く） | コメント |
| `direction DIR`、`flowchart` の向き | 見た目だけ |
| クラス図の可視性（`+ - # ~`） | 構造の義務は可視性を見ない |
| シーケンス図の送り手と呼出しの引数名 | 順序の義務は受け手の操作だけを見る。引数の数と名前はクラス図で検査する |
| シーケンス図の応答 `-->>` の全体 | 応答はコード上のイベントにならない |
| 状態遷移図の終了 `X --> [*]` | 遷移の関係の検査は終了状態を使わない |
| アクティビティ図の判断ノードのラベル、処理ノードの括弧の形 | 判断の意味は辺のガードが持つ。形は見た目だけ |

## 11. 等価とハッシュ

- `source_hash` = Markdown 本文の `content_hash`（`sha256:<hex>`）
- `ir_hash` = `CompiledSpec` の正規 JSON（キー順、区切りなし）から `source_hash`・`ir_hash`・span の `line` と `column` を除いたものの
  `content_hash`。見た目の違い（空白・コメント・別名・送り手）では変わらず、意味が変われば変わる
- 往復の等価（`compile(render(compile(s))) == compile(s)`）はこの正規形で比べる。除くものは上と同じ
  （span の行と列は正規形の Markdown では位置が変わるため、`source_hash` は本文そのものが変わるため）

## 12. 入力の上限（`limits.py`）

仕様の本文は利用者入力（エディタのプレビュー API）なので、どの処理も入力に対して線形で、再帰の深さを有界にする。
超えたら場所つきのエラー診断で打ち切る（例外にしない）。正規表現は使わず、字句解析と切り出しは手書きの走査で行う。

| 上限 | 値 | 診断 |
| --- | --- | --- |
| 本文の大きさ | 256KB（`domain.knowledge.BODY_MAX_BYTES`）。超えたら parse しない | `E_TOO_LARGE` |
| 1 行の文字数 | 4096 | `E_LINE_TOO_LONG` |
| 文書全体のトークン数 | 100,000 | `E_TOO_LARGE` |
| 図の数 | 64 | `E_TOO_LARGE` |
| 図ごとの文（行）の数（要素・メッセージ・辺の数はこれ以下） | 2,000 | `E_TOO_LARGE` |
| オートマトンの状態数・遷移数 | それぞれ 10,000（構成は Thompson 型で要素数に線形。分岐の直積展開はしない） | `E_TOO_LARGE` |
| `alt` / `opt` / `loop` の入れ子 | 32 | `E_NESTING_TOO_DEEP` |
| ガードの式の深さ（括弧・`not`・比較・加減の連なり） | 64 | `E_NESTING_TOO_DEEP` |
