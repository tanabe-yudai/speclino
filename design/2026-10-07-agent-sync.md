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
| A3 | 計画（SyncPlan）はサーバが作り、署名する: `{bundle_generation, github_repo_id, ops: [...], lock, signature}`。`op` は `write`（ファイル全体、`content` と `sha256`）・`set_managed_block`（AGENTS.md / CLAUDE.md の区画の中だけ、`inner` と `generation`、`expected_inner_sha256?`）・`remove_managed_block`（区画だけを取り除く、`expected_inner_sha256`）・`delete`（`expected_sha256`）の 4 つ。AGENTS.md / CLAUDE.md に `delete` は出さない | 書き方をサーバが決め、エージェントに判断させない |
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

## 6. 非目標

- CI での自動 sync（エージェントが行うので当面は不要）
- エージェントがコミット・PR を作ること自体の自動化（エージェント側の運用）
