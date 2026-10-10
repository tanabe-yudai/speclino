# エージェントによる知識の取り込み（2026-10-07、S75）

知識を承認するたびに人が `speclino sync` を流すのをやめ、MCP に登録済みのコーディングエージェントが
自分で知識を取り込み、作業の PR に lock を含める。`2026-10-05-team-knowledge-registry.md` の 7 章（配信）と
8 章（鮮度）の延長で、ゲートの判定（KNOWLEDGE-LOCK-001）は変えない。

## 1. 目的

- エージェントが作業を始めるときに lock の鮮度を確かめ、古ければ MCP から知識を取り込んで手元に書く
- 人は承認するだけでよく、sync の操作をしない
- エージェントが書き間違えても、ゲートの照合で UNKNOWN になる（偽の VERIFIED にならない）

## 2. 決定事項（codex のレビューを反映）

| # | 決定 | 理由 |
| --- | --- | --- |
| A1 | MCP に `knowledge_pull(repo_full_name, lock_json?)` を足す。返すのは「書き出しの計画（SyncPlan）」で、エージェントは**同梱の実行スクリプトに計画を渡して走らせる**。個々のファイルをエージェント自身が書くことはしない | 自然言語の手順では、symlink の拒否・全件の事前確認・途中失敗の巻き戻しを保証できない |
| A2 | 書き出しの実行は、標準ライブラリだけの Python 1 ファイル `speclino_apply.py` に集約する（`src/speclino/sync_apply.py` を正本にし、プラグインの `plugins/speclino/scripts/speclino_apply.py` に同じ内容を置く。一致はテストで固定）。CLI の `speclino sync` も同じ `apply_plan` を使う。今の `write_bundle` の中の削除の再計算はやめる | MCP と CLI の結果を食い違わせない。Python 3.10 以上があれば、speclino を入れていない環境でも動く |
| A3 | 計画（SyncPlan）はサーバが作り、署名する: `{bundle_generation, github_repo_id, ops: [...], lock, signature}`。`op` は `write`（ファイル全体、`content` と `sha256`）・`set_managed_block`（AGENTS.md / CLAUDE.md の区画の中だけ、`inner` と `generation`、`expected_inner_sha256?`、`if_exists?`（S77 の橋、6 章））・`remove_managed_block`（区画だけを取り除く、`expected_inner_sha256`）・`delete`（`expected_sha256`）の 4 つ。AGENTS.md / CLAUDE.md に `delete` は出さない | 書き方をサーバが決め、エージェントに判断させない |
| A4 | 実行スクリプトは、すべての操作の前提（パスが許可リスト・パスの途中に symlink が無い・ディレクトリの取り違えが無い・`expected_*` の一致・区画の印が 1 組だけ）を**先に全部確かめ**、1 つでも崩れたら何も書かずに止まる（終了コードと理由）。通れば一時ディレクトリに書いてから置き換え、失敗したら元に戻す。lock は最後に書く | 部分的に適用された状態で新しい lock を書かない |
| A5 | 削除（`delete` と `remove_managed_block`）の計画は、サーバが**署名・ワークスペース・リポジトリ ID を確かめた前回の lock**からだけ作る。`lock_json` が無い・無効なら削除は計画しない（既存のファイルと衝突する `write` は、内容が違えば止まる）。lock を渡さないと外れた知識のファイルが残りうるので、スキルと MCP の説明で必ず渡すよう指示する | 偽の lock を根拠に利用者のファイルを消させない |
| A6 | 新しい lock に、署名の対象として `absent: [path]`（前回あって今回は無いファイル・区画）を持たせる。ゲート（KNOWLEDGE-LOCK-001）は `absent` のファイルが存在しない・区画が無いことも照合し、残っていれば UNKNOWN | 削除の失敗・し忘れを、古い知識が残ったまま VERIFIED にしない（ただし `absent` は前回の lock を渡した取り込みでだけ埋まる。渡さなければ外れた知識は照合の対象にならない） |
| A7 | 鮮度の判定は今の実装（S68）を正とする: 世代が古くても、このプロジェクトに当たるメンバーと規約が同じなら VERIFIED_IN_SCOPE、違えば REVIEW。`2026-10-05-team-knowledge-registry.md` 8 章の「古ければ REVIEW」はこの意味に読み替える（ここに明記する） | 関係の無い承認のたびに REVIEW にしない |
| A8 | `sync_status(repo_full_name, lock_json: str \| None, local_hashes?: {path: sha256}, managed_inner?: {path: sha256})` が `pull_needed: bool` と理由（lock が無い・期限切れ・別のリポジトリ・メンバーの変化・規約の変化・ファイルの改変）を返す。これはエージェントの判断の材料で、最終的な判定はゲートの照合 | エージェントの判断を単純にする |
| A9 | 認可は既存のワークスペース単位の MCP トークン（同じワークスペースのどのリポジトリの文脈も既に読めるので、見えるものは増えない）と sync 専用のトークン（自分のリポジトリだけ）。リポジトリは必ず `_workspace_project` で引き、lock のリポジトリ ID は要求の値ではなく引いた Project から入れる | テナントの境界を既存どおりに保つ |
| A10 | 分割はしない。計画は MCP の応答で運ばない: `knowledge_pull` は計画を作ってサーバに置き、使い捨ての取得 URL（`GET /api/sync/plans/{token}`、1 回だけ・10 分で失効・ワークスペースとリポジトリに結び付く、トークンはハッシュだけ保存）を返す。実行スクリプトが `--plan-url` で取得する（https だけ、開発用に localhost の http を `--allow-http`）。計画の上限は CLI と同じ 8MB（fix round 1 のコントローラ裁定 40） | エージェントに大きな JSON を書き写させない。ページの混在を作らない |
| A11 | 取得は `knowledge_fetches` に記録する。手順はプラグインの新しいスキル `speclino-knowledge-sync` と MCP の説明文に書く: 作業を始める前に `sync_status`、`pull_needed` なら `knowledge_pull` → `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/speclino_apply.py --plan-url <plan_url>` → 結果を確かめて作業の変更と一緒にコミット | エージェントの読み込みの仕組みに乗る |

## 3. 実行スクリプトの約束

- 入力: 計画の取得 URL（`--plan-url`、MCP）か計画の JSON（ファイルか標準入力、CLI）と、リポジトリのルート（既定は現在のディレクトリ）
- 署名の検証はしない（鍵を持たない）。かわりに `github_repo_id` と、リポジトリの `.git` の remote から推定できる場合はその一致を確かめる（推定できなければ確かめない）
- 出力: 書いた・消した・変えなかったパスの一覧と、止まった場合はその理由（JSON）。終了コード 0 / 2（前提が崩れて何もしなかった）/ 1（予期しない失敗、元に戻した）
- 大きさの上限は計画の作成時と同じ値を再確認する

## 4. ゲートとの関係

- KNOWLEDGE-LOCK-001 に `absent` の照合を足す（A6）。それ以外は変えない
- 計画どおりに実行すれば VERIFIED_IN_SCOPE、書き間違い・書き忘れ・削除のし忘れは UNKNOWN、プロジェクトに関係する変化があって古ければ REVIEW

## 5. 安全性

- パスはサーバが許可リストから決め、実行スクリプトがもう一度確かめる。symlink はパスの途中も含めて拒否
- lock と計画の署名はサーバの鍵で、エージェントは作れない
- 応答に秘密は含まれない（知識の本文だけ）。トークンのワークスペースの外は返さない
- 本文に管理区画の印が含まれる知識は、承認の時点で拒否されている（S62）
- AGENTS.md / CLAUDE.md の区画の外は、実行スクリプトが触らない（ゲートでは照合しない。区画の外は利用者のもの）

## 6. 書き出し先の配置（2026-10-08 S76、2026-10-10 S77 で改訂）

S76 は「取り込むエージェントが配置を 1 つ選び、反対側を片付ける」だった。1 つのリポジトリを
Claude Code と Codex 等が交互に取り込むと配置が往復し、旧い配置（スキルが両ルート）を引数なしで
取り込むと `.claude/skills` が黙って消えた。S77 で次の規則に改める（codex のレビュー 3 回を反映）。

事実（2026-10 時点）: Claude Code はスキルを `.claude/skills` だけから読み、指示書は CLAUDE.md
（`.claude/CLAUDE.md`・`CLAUDE.local.md`・上位ディレクトリを含む）が無いときだけ AGENTS.md を読む。
CLAUDE.md の `@AGENTS.md` の行は import され、HTML コメント（SpecLino の印）は除去される。
Codex・Cursor・Gemini CLI・Copilot は AGENTS.md と `.agents/skills` を読む。

| # | 規則 | 理由 |
| --- | --- | --- |
| L1 | 指示書の本文は常に AGENTS.md の管理区画。`instructions_file="CLAUDE.md"` を明示したときだけ本文を CLAUDE.md に書き AGENTS.md を片付ける（S76 の排他。明示はその取り込み限りで、省略すると AGENTS.md に戻る） | 指示書の置き場所をエージェントで往復させない |
| L2 | 本文が AGENTS.md のとき、計画に CLAUDE.md への橋 `set_managed_block{path:"CLAUDE.md", inner:"@AGENTS.md\n", if_exists:true, expected_inner_sha256?}` を足す。実行スクリプトは CLAUDE.md が**無ければ何もしない（作らない）**、あれば区画を置き換え・追記（区画の外は触らない。中身が既に橋なら世代が違っても書き換えない）。橋は lock の `files` に載せない（`_check_lock` は `if_exists` の op を除く） | CLAUDE.md を新しく作ると、Claude Code が配下の AGENTS.md まで読まなくなる。有無は手元でしか分からないので適用時に判定する |
| L3 | 指示書が 0 件なら、AGENTS.md・CLAUDE.md の管理区画を片付ける（期待ハッシュ無しの `remove_managed_block`。前回の lock が `absent` にしたものは除く）。本文が CLAUDE.md なら AGENTS.md を片付ける | 載せていない指示書の区画は古い写し |
| L4 | 旧配置からの移行（前回の lock の `files` に CLAUDE.md の本文）: CLAUDE.md を `absent` に入れず、削除・片付けの op も出さず、橋の op に前回のハッシュを `expected_inner_sha256` として付ける。実行スクリプトは区画の中身がそれ（か既に橋）のときだけ置き換え、違えば終了コード 2。CLAUDE.md が消えていれば何もしない。同じパスに 2 つの op を出さない | 手で直した区画を黙って上書きしない |
| L5 | CLAUDE.md に本文を書くとき（L1 の明示）は、前回の記録が無くても区画の中身が橋なら置き換える。前回の lock が CLAUDE.md の本文を載せていなければ（lock なしを含む）、橋は印が本物の SpecLino の区画を中身を問わず置き換える（S76 の片付けと同じ扱い） | 橋は SpecLino が決めた中身なので所有が明らか |
| L6 | スキルの置き場所の集合 R を lock の署名対象の任意欄 `skill_roots`（昇順。空なら欄を出さず旧い署名と互換）に残す。前回の R0 は検証済みの前回の lock の `skill_roots`、欄の無い旧い lock は `files` のパスから推定（推定できなければ空） | 和集合を lock の外の推定に頼ると、スキルの無い期間に忘れる |
| L7 | 名乗りのルート r: 名前に `claude` を含む（大小無視）なら `.claude/skills`、それ以外・不明は `.agents/skills`。R = R0 ∪ {r}（名乗りで減らさない）。`skills_dir` の明示は排他（R = {skills_dir}。外れたルートのスキルは前回の lock から delete・`absent`）。スキルが 0 件でも R を lock に残す | 交互に取り込んでも消えない。名乗りは自己申告だが、変わるのは書き出し先だけ |
| L8 | 名乗りは MCP の clientInfo（2026-07-28 以降のプロトコルは要求ごとの envelope に載り `ctx.session.client_params` で取れる。任意）を優先し、無ければ引数 `agent`（64 文字・英数字と `._ -`）。REST `POST /api/sync/bundle` と `speclino sync` は `agent` / `--agent`。`knowledge_pull` の応答の `layout` は `{instructions_file, skill_roots, agent}` | 手順書を読まないエージェントでも名乗れる範囲を広げる |
| L9 | `sync_status` の `layout_change`: 取り込み直したときの R が R0 と違う（R0 が空なら比べない）か、lock の本文の置き場所が求める置き場所（L1）と違う | 仕様だけの lock で永久に `layout_change` を出さない |
| L10 | ゲート（KNOWLEDGE-LOCK-001）: lock のスキルと同名のディレクトリが、そのスキルを lock が載せていないルート（R の外）にあれば古い写し（REVIEW、既存の文言）。本文を載せていない指示書ファイルに区画があれば古い写し。本文が AGENTS.md だけの lock では、CLAUDE.md の区画は中身が橋なら正当、それ以外は古い写し。**CLAUDE.md があるのに橋が無ければ REVIEW「Claude Code が指示書を読めない: CLAUDE.md に @AGENTS.md の橋が無い。取り込み直す」**。読めない・印が壊れていれば残っている扱い | 橋を消した・後から CLAUDE.md を作った状態を VERIFIED にしない |
| L11 | `sync_status` はエージェントの申告で同じ導き方をする。`managed_inner` のキーはファイルがあること、値は区画の中身のハッシュ（区画が無ければ空文字）。CLAUDE.md のキーがあり空なら `bridge_missing`、橋以外のハッシュなら `stale_copy`（どちらも `pull_needed`、判定は REVIEW） | ゲートと同じ結論を作業の前に出す |
| L12 | 上限（2,000 op・8MiB）は据え置き。R が 2 つで超えたら「`skills_dir` で片側を指定する」旨のエラー | スキルを 2 か所に書くと容量が実質半分になることを仕様として受け入れ、逃げ道を示す |
| L13 | 実行スクリプトは、ルートに CLAUDE.md が無く `.claude/CLAUDE.md` か `CLAUDE.local.md` があるとき、Claude Code が AGENTS.md を読まない可能性を `warnings` で知らせる（自分では書かない） | 申告に頼らず手元で分かることは知らせる |

古いクライアント: 旧い実行スクリプトは橋の op を lock の照合で拒否し（「lock の files が計画の
書き出しと一致しない」、終了コード 2）、旧い `speclino sync` は lock の `skill_roots` と op の
`if_exists` を未知の欄として拒否する。手順書でプラグインの実行スクリプト（リポジトリにあれば
`plugins/speclino/scripts/speclino_apply.py`）を最新にするよう案内する。

残余（扱わないもの）:

- 上位ディレクトリ（リポジトリの外）の CLAUDE 系ファイルは見ない。そこに CLAUDE.md があると
  Claude Code は AGENTS.md を読まないが、ゲートでも実行スクリプトでも検出しない
- 手順書を読まず clientInfo も送らないエージェントは agents 扱い（`.agents/skills`）。Claude Code
  なのに名乗れなければ `.claude/skills` は足されない（人が `agent` を渡すか `speclino sync --agent`）
- CLAUDE.md が AGENTS.md への symlink のリポジトリでは、実行スクリプトは symlink を拒否する
  （S76 からの既存の振る舞い）
- `.claude/CLAUDE.md` / `CLAUDE.local.md` だけがあるときは警告だけで、ゲートは判定しない

## 7. 非目標

- CI での自動 sync（エージェントが行うので当面は不要）
- エージェントがコミット・PR を作ること自体の自動化（エージェント側の運用）
