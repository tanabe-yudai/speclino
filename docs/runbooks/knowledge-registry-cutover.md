# 知識レジストリへの切り替え（本番）

対象: マイグレーション 0010（知識レジストリ）・0011（LLM 支援の記録・人の確定・bundle の鍵の ID）と、
それを使うコードを本番に出し、**登録済みの全プロジェクトを一度に**切り替える（ユーザー判断 2026-10-05）。

切り替えの後、ワークスペースに登録されたプロジェクトの検査は bundle（承認済みの知識）で行う。
`specs/` の旧形式の仕様は読まれなくなり（取り込むまで `KNOWLEDGE-SPEC-001` が UNKNOWN）、
`.speclino/lock.json` が無い PR は `KNOWLEDGE-LOCK-001` が UNKNOWN になる。**どちらも必須なので、
デプロイから各プロジェクトの sync までの間、全プロジェクトのゲートは失敗する**。作業の時間帯を決めて
関係者に知らせてから始める。

以下、`P` は GCP プロジェクト、`REGION` は `asia-northeast1`。`terraform apply` と Secret Manager への
値の投入は**ユーザーが行う**（CLAUDE.md の確認が必要な操作）。

## 0. 前提

- 統合ブランチが main にマージ済みで、CI（lint / typecheck / test / self-check / web）が緑
- `speclino-migrate` と `speclino-admin` の Cloud Run Job がある（`infra/envs/prod/run.tf`）
- 手元に `gcloud` と `terraform`（`infra/envs/prod` で `terraform init` 済み）

## 1. 署名鍵の secret を作る（terraform apply 1 回目、ユーザー）

```sh
cd infra/envs/prod
terraform plan -out=tfplan   # bundle-signing-key / bundle-signing-keys-old の secret と IAM が増えるだけ
terraform apply tfplan
```

この時点では `mount_bundle_signing_key = false`（既定）なので、サービスには何も注入されない。

## 2. 鍵の値を入れる（ユーザー）

```sh
openssl rand -hex 32 | tr -d '\n' | \
  gcloud secrets versions add bundle-signing-key --data-file=- --project=$P
```

値は手元に残さない（シェルの履歴にも出ない形で入れる）。

## 3. 鍵を注入する（terraform apply 2 回目、ユーザー）

```sh
echo 'mount_bundle_signing_key = true' >> terraform.tfvars
terraform plan -out=tfplan   # web / worker の env と admin Job（SPECLINO_ENV=prod も）が変わる
terraform apply tfplan
```

旧コードは `SPECLINO_BUNDLE_SIGNING_KEY` を読まないので、この apply で動作は変わらない。新しいコードは
env=prod で鍵が無いと起動しないので、**必ずデプロイより先に行う**。

## 4. マイグレーション 0010 / 0011（migrate Job）

main へのマージで `.github/workflows/deploy.yml` が、サービスを更新する前に `speclino-migrate` を
流す。手で流す場合（デプロイを止めているときなど）:

```sh
IMAGE=$REGION-docker.pkg.dev/$P/speclino/speclino:<新しいイメージのタグ>
gcloud run jobs update speclino-migrate --project=$P --region=$REGION --image=$IMAGE
gcloud run jobs execute speclino-migrate --project=$P --region=$REGION --wait
gcloud run jobs executions list --job=speclino-migrate --project=$P --region=$REGION --limit=1
```

0010 / 0011 は**追加だけ**（新しい表と NULL 可の列 `review_runs.pr_author_id`・`bundles.key_id`）なので、
旧コードのサービスがこのスキーマで動き続けても問題ない。

## 5. デプロイ

deploy.yml がそのまま続けて行う（migrate → `backfill-repo-ids` → web / worker の更新）。手で行うなら
`guides/deploy-runbook.md` の 5 章。確かめる:

```sh
gcloud run services describe speclino-web --project=$P --region=$REGION \
  --format='value(status.latestReadyRevisionName)'
gcloud run jobs execute speclino-admin --project=$P --region=$REGION --wait --args=signing-key-id
gcloud run jobs execute speclino-admin --project=$P --region=$REGION --wait --args=cutover-status
gcloud run jobs logs read speclino-admin --project=$P --region=$REGION --limit=50
```

`signing-key-id` が 16 桁の 16 進を出せば鍵が届いている。`cutover-status` はこの時点で全プロジェクトが
`TODO`（仕様・トークン・lock がまだ無い）。

## 6. プロジェクトごとの切り替え（登録済みの全プロジェクト）

`cutover-status` の各行について、次を順に行う。

1. **仕様を取り込む**（旧形式 → spec の下書き）。リポジトリの checkout が要るので手元で流す。
   下書きを作るだけで署名はしないので、署名鍵は要らない（DB だけ。`guides/deploy-runbook.md` 7 章の
   Cloud SQL Auth Proxy）:

   ```sh
   cloud-sql-proxy $P:$REGION:speclino-db &
   SPECLINO_DATABASE_URL="postgresql+asyncpg://speclino:<pw>@localhost:5432/speclino" \
     uv run speclino import-specs --workspace <slug> --project-id <project ID> \
       --author <自分の uid> --repo <checkout のパス>
   ```

   project ID は `cutover-status` の出力（または `speclino workspace list`）で確かめる。仕様の無い
   リポジトリは飛ばしてよい（規約・スキル・指示だけで使う）。
2. **画面で確かめて承認する**: 「知識」→「仕様」→ 取り込んだ仕様。診断が 0 件であること、図・正規形・
   要求の表を見てから「承認して有効にする」。承認のたびに新しい bundle（署名つき）ができる。
3. **sync トークンを作る**: 「設定」→「sync トークン」でこのプロジェクトのトークンを発行する
   （owner / approver）。値は発行直後にしか見えない。CI で sync するならリポジトリの secret に入れる。
4. **sync する**（リポジトリの checkout で）:

   ```sh
   export SPECLINO_SYNC_TOKEN=slsync_...
   uv run speclino sync --url https://<本番の URL> --repo . --repo-id <GitHub のリポジトリ ID>
   ```

5. **lock と書き出したファイルをコミットする**: `.speclino/lock.json`、`.speclino/specs/`、
   `.claude/skills/`・`.agents/skills/`、`AGENTS.md` / `CLAUDE.md` の管理区画。main に入れる
   （PR のレビューで中身を確かめる）。
6. **検査し直す**: 開いている PR は main を取り込んで push するか、PR に `/speclino rerun` とコメントする。

### LLM 支援の規約を使うプロジェクト

LLM 支援の規約（`tier: llm_assisted`）は、`params.paths` に一致する Python の単位を外部のモデル
（Vertex AI の Gemini）に送る。秘密情報・個人情報らしい値を含む単位は送らないが、**検出は完全ではない**。
規約を承認する前に、`params.paths` が送ってよいディレクトリだけを指していることを確かめる（無指定や
`**/*` のような広いパターンは、承認の画面に警告が出る）。止めるときは `SPECLINO_LLM_POLICIES_ENABLED=false`。

## 7. 確かめる

```sh
gcloud run jobs execute speclino-admin --project=$P --region=$REGION --wait --args=cutover-status
gcloud run jobs logs read speclino-admin --project=$P --region=$REGION --limit=50
```

全行が `ready`（承認済みの仕様がある・有効な sync トークンがある・最新の完了した検査の
`KNOWLEDGE-LOCK-001` が VERIFIED_IN_SCOPE）で、最後の行が `N/N プロジェクトが切り替え済み` になれば完了。
仕様を持たないリポジトリは `specs=-` のまま `TODO` に残る（仕様が無いことを確かめたうえで完了とする）。
加えて、画面の「可観測性」で各リポジトリの bundle の遅れが 0 であること、run の詳細で
`KNOWLEDGE-LOCK-001` が適合になっていることを見る。

## 8. 戻すとき

- **コードは前のイメージに戻してよい**（`gcloud run services update speclino-{web,worker} --image=<前のタグ>`）。
  0010 / 0011 は追加だけなので、前のコードは新しい表と列を無視して動く。前のコードはリポジトリの `specs/` を
  読むので、**切り替えが落ち着くまで各リポジトリの `specs/` を消さない**
- **マイグレーションは本番で downgrade しない**。downgrade は bundle・改訂・確定・評価の記録を消す
  （戻せない）。もう一度進めるときは、残ったデータをそのまま使う
- `mount_bundle_signing_key = true` は前のコードでも害が無いので戻さなくてよい
- 一部のプロジェクトだけ遅れる場合、そのプロジェクトのゲートは手順 6 が終わるまで失敗し続ける
  （UNKNOWN を通す設定は無い）。急ぐなら手順 6 の 1〜2 を飛ばして 3〜6（sync と lock）だけ先に行う
  （仕様が無ければ `KNOWLEDGE-SPEC-001` だけが残る）

## 9. 署名鍵の更新（ローテーション）

bundle と lock は署名した鍵の ID（`key_id`）を持つ。サーバは今の鍵で署名し、`key_id` の一致する鍵で
検証する。知らない `key_id` は検証しない（lock は UNKNOWN、bundle を使う検査は失敗する）。

1. 今の鍵の ID を控える:
   `gcloud run jobs execute speclino-admin --args=signing-key-id --wait ...`（ログに出る）
2. 前の鍵を `kid:secret` の形で `bundle-signing-keys-old` に入れる（ユーザー。複数ならカンマ区切り）:

   ```sh
   OLD=$(gcloud secrets versions access latest --secret=bundle-signing-key --project=$P)
   printf '%s:%s' "<控えた kid>" "$OLD" | \
     gcloud secrets versions add bundle-signing-keys-old --data-file=- --project=$P
   unset OLD
   ```

   kid と鍵が合わないとサービスは起動しない（取り違えを起動時に止める）。
3. 新しい鍵を入れる: `openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add bundle-signing-key ...`
4. `mount_bundle_signing_keys_old = true` を tfvars に書いて `terraform apply`（ユーザー）。新しい
   リビジョンが新しい鍵（`latest`）と前の鍵を読む
5. 全ワークスペースの bundle を新しい鍵で作り直す:

   ```sh
   gcloud run jobs execute speclino-admin --project=$P --region=$REGION --wait --args=rebundle-all
   ```

   既に新しい鍵で署名済みのワークスペースは飛ばす。競合などで作れなかったワークスペースは `failed` と
   理由が出て、終了コードが 1 になる（他のワークスペースの作り直しは残る）。世代が進むので、各リポジトリの
   lock は古い扱い（REVIEW）になる。各リポジトリで `speclino sync` をやり直してコミットする
6. lock の期限（30 日）が過ぎたら、`mount_bundle_signing_keys_old = false` にして apply し、
   `bundle-signing-keys-old` の値を無効化する（`gcloud secrets versions disable`）。前の鍵の lock は
   その時点で UNKNOWN になる

鍵が漏れた疑いのときは、手順 2 と 6 を飛ばして（前の鍵を残さず）すぐに 3〜5 を行う。前の鍵で
署名された bundle と lock は直ちに検証できなくなり、検査は sync をやり直すまで失敗する（安全側）。
