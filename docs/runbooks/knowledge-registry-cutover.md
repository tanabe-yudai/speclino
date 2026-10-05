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

- **統合ブランチ（`s72-integration`）はまだ main にマージしない**。main へのマージで
  `.github/workflows/deploy.yml` が自動でマイグレーションとデプロイを行い、新しいコードは env=prod で
  署名鍵が無いと起動しない。署名鍵の secret の作成・値の投入・注入（手順 1〜3）は、マージより前に
  統合ブランチの `.tf` で行う
- 統合ブランチの CI（lint / typecheck / test / self-check / web）が緑
- `speclino-migrate` と `speclino-admin` の Cloud Run Job がある（`infra/envs/prod/run.tf`）
- 手元に `gcloud` と `terraform`。統合ブランチを checkout し、`infra/envs/prod` で `terraform init` 済み

## 1. 署名鍵の secret を作る（統合ブランチの .tf で apply 1 回目、ユーザー、マージ前）

```sh
git switch s72-integration
cd infra/envs/prod
terraform plan -out=tfplan   # 増えるのは bundle-signing-key / bundle-signing-keys-old の secret と IAM、
                             # admin Job の SPECLINO_ENV=prod。それ以外の差分が出たら止めて確かめる
terraform apply tfplan
```

この時点では `mount_bundle_signing_key = false`（既定）なので、サービスには何も注入されない。admin Job の
`SPECLINO_ENV=prod` は、今の（main の）コードでも動作を変えない。

## 2. 鍵の値を入れる（ユーザー、マージ前）

```sh
openssl rand -hex 32 | tr -d '\n' | \
  gcloud secrets versions add bundle-signing-key --data-file=- --project=$P
```

値は手元に残さない（シェルの履歴にも出ない形で入れる）。

## 3. 鍵を注入する（統合ブランチの .tf で apply 2 回目、ユーザー、マージ前）

```sh
echo 'mount_bundle_signing_key = true' >> terraform.tfvars
terraform plan -out=tfplan   # web / worker / admin Job の env に SPECLINO_BUNDLE_SIGNING_KEY が増える
terraform apply tfplan
```

今の（main の）コードは `SPECLINO_BUNDLE_SIGNING_KEY` を読まないので、この apply で動作は変わらない。
確かめる: `gcloud run services describe speclino-web --project=$P --region=$REGION
--format='yaml(spec.template.spec.containers[0].env)'` に `SPECLINO_BUNDLE_SIGNING_KEY` が secret 参照で
出ていること。

## 4. main にマージする（マイグレーション 0010 / 0011 とデプロイ）

手順 1〜3 が済んでから統合ブランチを main にマージする。deploy.yml が次を順に行う:
migrate Job（0010 / 0011）→ admin Job の `backfill-repo-ids` → web / worker の更新。
デプロイを止めていて手で流す場合:

```sh
IMAGE=$REGION-docker.pkg.dev/$P/speclino/speclino:<新しいイメージのタグ>
gcloud run jobs update speclino-migrate --project=$P --region=$REGION --image=$IMAGE
gcloud run jobs execute speclino-migrate --project=$P --region=$REGION --wait
gcloud run jobs executions list --job=speclino-migrate --project=$P --region=$REGION --limit=1
```

続けて web / worker の更新は `guides/deploy-runbook.md` の 5 章。0010 / 0011 は**追加だけ**（新しい表と
NULL 可の列 `review_runs.pr_author_id`・`bundles.key_id`）なので、サービスの更新より先にスキーマが進んでも
旧コードは動き続ける。

## 5. デプロイを確かめる

```sh
gcloud run services describe speclino-web --project=$P --region=$REGION \
  --format='value(status.latestReadyRevisionName)'
gcloud run jobs execute speclino-admin --project=$P --region=$REGION --wait --args=signing-key-id
gcloud run jobs execute speclino-admin --project=$P --region=$REGION --wait --args=cutover-status
gcloud run jobs logs read speclino-admin --project=$P --region=$REGION --limit=50
```

`signing-key-id` が 16 桁の 16 進を出せば鍵が届いている。`cutover-status` はこの時点で全プロジェクトが
`TODO`（仕様・トークン・lock がまだ無い）。新しいリビジョンが起動しない（鍵が無い）ときは手順 3 を
確かめる。旧リビジョンへの戻し方は 8 章。

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

   project ID は `cutover-status` の各行の `project=<ID>` で確かめる。仕様の無い
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
3. **3 と 4 は間を空けずに続けて行う。** 新しい鍵の値を入れる:
   `openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add bundle-signing-key --data-file=- --project=$P`。
   secret は `latest` で参照しているので、この後に起動したインスタンスは新しい鍵を読む。手順 4 が終わる
   までは、前の鍵を鍵束に持たないインスタンスが混ざり、前の鍵の bundle を使う検査が失敗しうる
4. 続けて `mount_bundle_signing_keys_old = true` を tfvars に書いて `terraform apply`（ユーザー）。新しい
   リビジョンが新しい鍵（`latest`）と前の鍵の両方を読む。古いリビジョンへのトラフィックが無くなった
   ことを確かめる（`gcloud run services describe ... --format='value(status.traffic)'`）
5. 全ワークスペースの bundle を新しい鍵で作り直す:

   ```sh
   gcloud run jobs execute speclino-admin --project=$P --region=$REGION --wait --args=rebundle-all
   ```

   既に新しい鍵で署名済みのワークスペースは飛ばす。競合などで作れなかったワークスペースは `failed` と
   理由が出て、終了コードが 1 になる（他のワークスペースの作り直しは残る）。作り直しで世代は進むが、
   メンバーと規約は同じなので、各リポジトリの前の鍵の lock は**前の鍵が鍵束にある間は VERIFIED のまま**
   （lock の鮮度はメンバーと規約で比べ、世代の差だけでは古い扱いにしない）。手順 6 で前の鍵を外すと
   UNKNOWN になるので、それまでに各リポジトリで `speclino sync` をやり直してコミットする
6. 全リポジトリの lock を新しい鍵で取り直した（または lock の期限の 30 日が過ぎた）ら、
   `mount_bundle_signing_keys_old = false` にして apply し、`bundle-signing-keys-old` の値を無効化する
   （`gcloud secrets versions disable`）。前の鍵の lock と bundle はその時点で検証できなくなる

鍵が漏れた疑いのときは、手順 2 と 6 を飛ばして（前の鍵を残さず）すぐに 3〜5 を行う。前の鍵で
署名された bundle と lock は直ちに検証できなくなり、検査は sync をやり直すまで失敗する（安全側）。
