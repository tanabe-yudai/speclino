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

- ブロック構造（フェンス・字下げのコード・リスト・引用・HTML・表）は CommonMark 準拠のパーサー `markdown-it-py`
  （preset `commonmark` ＋ GFM の表。インラインは解析しない）で読む。読み手に見える構造と SpecLino が読む構造を一致させるため
- 図として読むのは、言語名の最初の語が大文字小文字を無視して `mermaid` のフェンスのうち、**正規形**だけ:
  トップレベル（引用・リストの外）、開きが ` ちょうど 3 つ、言語名が厳密に `mermaid`、開きの行が字下げなしで ```` ```mermaid ````。
  それ以外（`~~~mermaid`、```` ```mermaid title ````、字下げ、```` ````mermaid ````、引用・入れ子のリストの中、```` ```Mermaid ````）は
  `E_UNSUPPORTED_SYNTAX`。字下げのコードブロックの中の ```` ```mermaid ```` の行も同じ
- 閉じは CommonMark どおり（開きと同じ文字が同数以上で、言語名なし。0〜3 字下げ可。```` ``` foo ```` では閉じない）。
  mermaid ブロック内の字下げ ```` ``` ```` もブロックを閉じる（読み手の見え方どおり）
- 閉じていない mermaid のフェンスは `E_UNCLOSED_BLOCK`。mermaid 以外の閉じていないフェンス（文書・コンテナの終わりまで伸びる）が
  ```` ```mermaid ```` の行や要求の表の見出しを飲み込んでいれば `E_UNCLOSED_BLOCK`。閉じたコードの中の ```` ```mermaid ```` は
  図として読まず警告 `W_MERMAID_IN_CODE`（説明の例として書くことがあるため）
- HTML（ブロック・段落の中のコメント）の中に `mermaid` があれば `E_UNSUPPORTED_SYNTAX`、閉じていない `<!--` も
  `E_UNSUPPORTED_SYNTAX`（後ろの内容が表示から隠れる）、要求の表の見出しがあれば `E_REQ_TABLE`
- `mermaid` 以外の言語のブロックは中身ごと本文として扱い読まない
- **事後照合**（不変条件）: markdown-it は既定の入れ子の上限（maxNesting）を超えた中身を黙って捨てる。そこで解析の後、
  行頭の空白・`>`・リストの記号・Unicode の Zs と Cf の文字（NBSP・U+3000・BOM・ゼロ幅文字・U+00AD・方向制御など）を除いた後が
  「` か ~ が 3 個以上 → 空白・タブ 0 個以上 → 大文字小文字を無視して `mermaid`」で始まる行は、フェンス（受理・エラー・閉じたコードの中）・字下げのコード・HTML のどれかの中でなければならない。
  要求の表の見出しに見える行は、どれかの葉のブロック（表・段落を含む）の中でなければならない。説明できない行は
  `E_UNSUPPORTED_SYNTAX`（「Markdown の構造を解釈できない」）。NBSP・ゼロ幅文字・2 つ目の BOM の後ろの ```` ```mermaid ````
  （段落として表示される）もこれで知らせる。照合は差分配列で O(行数 + トークン数)、行頭の除去は 1 回の走査。
  報告は 20 件までで、残りは最後の診断に「ほか N 件」と付ける
- 本文の行頭のインラインコード（```` ```mermaid``` ````）もこの照合でエラーになる（安全側に倒す。行頭以外に書けばよい）
- **文字参照・バックスラッシュのエスケープ**: インラインを解析しないので markdown-it は info やセルの文字参照を解かないが、
  読み手は解いて表示する。そこで (1) フェンスの info は `unescapeAll` で解いてから mermaid かどうかを決め、解いた結果が
  mermaid で原文と違えば（```` ```mer&#109;aid ````、```` ```&#77;ermaid ````）開きの行の `E_UNSUPPORTED_SYNTAX`
  （`\m` はエスケープではないので ```` ```\mermaid ```` は図にならない）。(2) 事後照合の「図の開きに見える行」も、
  info の部分を解いた結果で判定する。(3) 要求の表の候補は、見出しのセルの強調記号を除いた後に解いて判定する。候補の表の
  行（見出し・本文）に、解くと変わる文字参照（`&amp;`・`&#73;` 等）やエスケープ（`\*` 等）があれば `E_REQ_TABLE`
  （「要求の表に文字参照・エスケープは使えない」）。`&&` のように参照の形でない `&` は使える。解く処理は 1 行 1 回で線形。
  (4) 行ごとに出る診断（事前検査・段落の中の HTML コメント・HTML ブロック・フェンス・表）も 20 件までで、残りは最後の診断に「ほか N 件」と付ける
- 改行は CommonMark と同じく `\n` にそろえ、先頭の BOM（U+FEFF）を 1 つ除いてから読む（行番号はそろえた後の行。`source_hash` は原文）
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
enum_lit = EnumType "." VALUE ;                             (* 1 つの NAME トークン、ドットは 1 つ、EnumType は大文字で始まる *)
```

- 参照は `before.` / `after.` / `outcome.` の名前空間が必須。入口の引数を参照するときも、**引数をクラス図の属性として宣言してから**
  `before.<変数>.<属性>` で参照する（13 章。宣言の無い参照は `E_UNKNOWN_REFERENCE`）。名前空間の無い名前・日本語・
  文字列はエラー（`E_GUARD_SYNTAX`）。自然言語のガードはここで落ちる。小文字で始まるドット付きの名前（`order.qty`）は
  名前空間の付け忘れとして「before./after./outcome. で始める」と案内する
- 列挙値は `型名.値`（例 `OrderStatus.PENDING` → `Const(type="OrderStatus", value="PENDING")`）。型はクラス図の
  `<<enumeration>>` か `domain.enums` に宣言されていて、値はその列挙の要素でなければならない（`E_UNKNOWN_ENUM`）
- 比較の連鎖（`a < b < c`）はエラー。`and` / `or` の連続は 1 つの `Nary` に平らにする。括弧はノードを作らない
- 最上位が算術式・整数・列挙値のガードはエラー（`E_GUARD_TYPE`）。参照の解決と型検査は 13 章
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
          | node "-->" "|" label "|" node NL ;                   (* 辺。1 行に 1 本 *)
label     = '"' "[" ID "]" [ guard ] '"' | "[" ID "]" [ guard ] ;
node      = ID [ shape ] ;
shape     = "[" operation "]" | "(" operation ")" | "(" "[" operation "]" ")"   (* 処理 *)
          | "{" { any_token } "}"                                               (* 判断 *)
          | "(" "(" "start" ")" ")" | "(" "(" "end" ")" ")" ;                    (* 開始・終了 *)
```

- 辺のラベルは引用符で囲んだ形 `A -->|"[e1] guard"| B` で書く。Mermaid は `|[` で始まるラベルを描けない
  （構文エラー）ため。引用符は見た目だけで IR に入らない。囲まない形 `A -->|[e1] guard| B` も同じ IR として
  受け付ける（エディタのプレビューは「描画できない」と出す）。正規形（`render`）は引用符で囲む
- 形（`shape`）は ID ごとに 1 回だけ宣言する。2 回目の宣言は同じ内容でも `E_DUPLICATE_ID`（ラベルだけ違う同じ ID を拒否）。
  形の無いノードの参照は宣言済みでなければならない（`E_UNDECLARED_NODE`）
- 開始はちょうど 1 つで入る辺が無い。終了は 1 つ以上で出る辺が無い。開始以外のノードは入る辺を、終了以外は出る辺を持つ
- 辺の矢印は `-->` だけ。`---`、`-.->`、`==>`、`--x`、`--o`、`A -- text --> B`、`A --> B --> C`、`&` はエラー
- `subgraph`、`click`、`style`、`classDef`、`class`、`linkStyle`、`graph` はエラー
- オートマトン: 状態 = ノード ID（最初に現れた順）、初期 = 開始、受理 = 終了。辺 `A -->|"[e] g"| B` は
  `A → B` に `e: call(B の操作) [g]`（B が処理ノードのとき）か `e: ε [g]`（判断・終了のとき）
- `TraceObligation(mode="included_in", closed=%%@ closed)`

## 8. 要求の表

```ebnf
req_header = "|" "ID" "|" "level" "|" "要求" "|" "形式化" "|" ;
req_sep    = "|" { "-" | ":" | " " | "|" } ;
req_row    = "|" REQ_ID "|" ( "mandatory" | "advisory" ) "|" TEXT "|" [ ref { "," ref } | "-" ] "|" ;
ref        = diagram_id "#" ID | "ir:" RULE_ID ;
```

- 表は GFM の表として読む。見出しに `形式化` を含む表、または最初の列が大文字小文字を無視して `ID` の表は要求の表の候補とし、
  トップレベルで行頭の `|` から始まり見出しが上と完全に一致するものだけを受け付ける。引用・リストの中の候補や見出しの違いは `E_REQ_TABLE`
- GFM が表と認めない「表らしい行の並び」（段落の中の `|` を 2 つ以上含む行）が候補の見出しを持てば `E_REQ_TABLE`
- 本文の行は GFM の行として読む（先頭の `|` の無い行も行になる）。列の数（`|` で区切った生の数）が 4 でない行は `E_REQ_TABLE`
- 要求の表の行（見出し・本文の行全体）に `\|` は書けない（`E_REQ_TABLE`。GFM は `\|` を区切りにしないので列の数え方が読み手とずれる）
- 候補の判定では見出しのセルの強調記号（`*` `_` `` ` ``）と前後の空白を除いて比べる（`**ID**` も候補になり、完全一致しないので `E_REQ_TABLE`）
- 要求 ID は一意。本文の列に `|` は書けない
- `図ID#要素ID` は 9 章の参照できる要素、`ir:要求ID` は旧形式の IR の `Rule.id` を指す。無ければ `E_REF_UNKNOWN`
- `形式化` が空か `-` の行は**未形式化**として残す（`formalized_by=[]`）
- 義務の level: その義務を構成する要素が**すべて**、`advisory` の行だけから参照されているときだけ `advisory`。
  参照の無い要素や `mandatory` の行から参照される要素が 1 つでもあれば `mandatory`
  （義務の要素: 構造 = そのクラス、順序・包含・遷移 = その図の参照できる要素すべて。9 章）

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
| アクティビティ図の辺のラベルの引用符 `"…"` | Mermaid で描くための見た目だけ |

## 11. 等価とハッシュ

- `source_hash` = Markdown 本文の `content_hash`（`sha256:<hex>`）
- `ir_hash` = `CompiledSpec` の正規 JSON（キー順、区切りなし）から `source_hash`・`ir_hash`・span の `line` と `column` を除いたものの
  `content_hash`。見た目の違い（空白・コメント・別名・送り手）では変わらず、意味が変われば変わる
- 往復の等価（`compile(render(compile(s))) == compile(s)`）はこの正規形で比べる。除くものは上と同じ
  （span の行と列は正規形の Markdown では位置が変わるため、`source_hash` は本文そのものが変わるため）

## 12. 入力の上限（`limits.py`）

仕様の本文は利用者入力（エディタのプレビュー API）なので、どの処理も入力に対して線形で、再帰の深さを有界にする。
超えたら場所つきのエラー診断で打ち切る（例外にしない）。図の字句解析は正規表現を使わない手書きの走査、Markdown は
markdown-it-py のブロック解析だけ（インラインは二次の時間がかかるので使わない）。

| 上限 | 値 | 診断 |
| --- | --- | --- |
| 本文の大きさ | 256KB（`domain.knowledge.BODY_MAX_BYTES`）。超えたら parse しない | `E_TOO_LARGE` |
| 1 行の文字数 | 4096 | `E_LINE_TOO_LONG` |
| 引用・リストの入れ子の見積り（DoS 対策。見積りがずれても事後照合が図と表の消失を検出する） | 16 | `E_NESTING_TOO_DEEP` |
| 1 行の `|` の数（表の列の数） | 64 | `E_TOO_LARGE` |
| `|` を含む行の数（表の行の数） | 5,000 | `E_TOO_LARGE` |
| 文書全体の `|` の数（表のセルの総数） | 20,000 | `E_TOO_LARGE` |
| 文書全体のトークン数 | 100,000 | `E_TOO_LARGE` |
| 図の数 | 64 | `E_TOO_LARGE` |
| 図ごとの文（行）の数（要素・メッセージ・辺の数はこれ以下） | 2,000 | `E_TOO_LARGE` |
| オートマトンの状態数・遷移数 | それぞれ 10,000（構成は Thompson 型で要素数に線形。分岐の直積展開はしない） | `E_TOO_LARGE` |
| `alt` / `opt` / `loop` の入れ子 | 32 | `E_NESTING_TOO_DEEP` |
| ガードの式の深さ（括弧・`not`・比較・加減の連なり） | 64 | `E_NESTING_TOO_DEEP` |

## 13. クラス図の語彙との突き合わせ

振る舞いの図（シーケンス・状態遷移・アクティビティ）は、同じ仕様のクラス図を語彙とする。旧形式の IR だけの仕様は対象外。

- 振る舞いの図があるのにクラス図が 1 つも無ければ `E_NO_CLASS_DIAGRAM`
- シーケンス図の参加者のクラスはクラス図に必要（`E_UNKNOWN_CLASS`）。呼出しの `<受け手のクラス>.<メソッド>`、`%%@ entry`、
  アクティビティ図の処理ノードの操作は、そのクラスの操作として宣言されていなければならない（`E_UNKNOWN_CLASS` / `E_UNKNOWN_OPERATION`）
- **変数名の規則**: クラス `C` の属性 `a` は変数 `c.a` で参照する。`c` はクラス名の**先頭の 1 文字だけを小文字にしたもの**
  （`Order` → `order`、`InventoryService` → `inventoryService`、先頭が小文字のクラス名はそのまま）。`domain` が渡されたら
  `domain.variables` の変数でも補う（クラス図が優先）
- 状態遷移図の `%%@ attribute` はこの規則で属性に解決できなければ `E_UNKNOWN_REFERENCE`。型が列挙でなければ
  `E_STATE_ATTRIBUTE_TYPE`、状態の集合が列挙値の部分集合でなければ `E_UNKNOWN_STATE`
- ガードの参照は `before.` / `after.` の後をこの規則で、`outcome.` の後を組込み変数（`status`、`kind`）で解決する。
  できなければ `E_UNKNOWN_REFERENCE`。`参照 ==/!= 列挙値` は、列挙値がその属性の列挙に属さなければ `E_UNKNOWN_ENUM`。
  そのうえで `conformance.typing.require_boolean` で型検査する（`E_GUARD_TYPE`）
- 中身の無い図はエラー（`E_EMPTY_DIAGRAM`）: クラスの無いクラス図、呼出し（`->>`）の無いシーケンス図、遷移の無い状態遷移図、
  処理ノードの無いアクティビティ図
- Mermaid の予約語（`end` など。`_parse.RESERVED_SEQUENCE` / `RESERVED_FLOWCHART`）を参加者・ノード ID に使うと
  警告 `W_RESERVED_WORD`（IR には影響しない。描画できるかの判断はエディタ側）
