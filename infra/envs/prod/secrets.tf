# Secret のコンテナだけを作る。値は Terraform state に載せたくないため
# ここでは作らず、apply 後に手動で投入する:
#
#   echo -n "<値>" | gcloud secrets versions add github-app-private-key --data-file=- --project=<project_id>
#   echo -n "<値>" | gcloud secrets versions add github-webhook-secret  --data-file=- --project=<project_id>
#   echo -n "<値>" | gcloud secrets versions add session-secret         --data-file=- --project=<project_id>
#   openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add bundle-signing-key --data-file=- --project=<project_id>
#
# bundle-signing-key は知識の bundle・lock の署名鍵（S62・S68）。値を投入してから
# tfvars の mount_bundle_signing_key = true にする（バージョンの無い secret を注入すると
# リビジョンが起動できない。手順は docs/runbooks/knowledge-registry-cutover.md）。
#
# mcp-token は S54 で env への注入をやめた（MCP の認証はワークスペース単位のトークンに
# 切り替え、UI（/workspaces/<slug>/mcp-tokens）で owner が発行する。DB の mcp_tokens テーブル
# が正本）。secret のコンテナとアクセス権はロールバック期間が過ぎるまで残す（消すと
# secret 自体が削除対象になり戻せなくなるため）。値をもう投入する必要は無い。
#
# アクセス権は secret 単位で付与する（web: webhook-secret, session-secret, （legacy）mcp-token /
# worker: github-app-private-key）。design のアーキテクチャ表に従う。

locals {
  secret_accessors = {
    "github-app-private-key" = ["worker"]
    "github-webhook-secret"  = ["web"]
    # S54: 静的トークンでの MCP 認証は廃止し、ワークスペース単位のトークン（DB の
    # mcp_tokens、UI から owner が発行）に切り替えた。web はもうこの secret を env に
    # マウントしない（下の secret_env.web 参照）。ただし secret 自体とアクセス権は
    # ロールバック期間が過ぎるまで残す（切り替え直後に戻せるように。design の切り替え順序）。
    "mcp-token"      = ["web"]
    "session-secret" = ["web"]
    # S55: GitHub App のユーザー認可（リポジトリ登録）の client secret。web だけが使う
    "github-app-client-secret" = ["web"]
    # Cloud SQL の接続 URL（パスワードを含むため tfvars ではなくここに置く）。
    # web も run の一覧・詳細を DB から読むので両方に許可する（iam.tf の cloudsql.client も両方）。
    "database-url" = ["web", "worker"]
    # 知識の bundle・lock の署名鍵（S62・S68）。web は承認で bundle を作って署名し、
    # worker は run で bundle と PR の lock.json の署名を検証する
    "bundle-signing-key" = ["web", "worker"]
  }

  # 署名鍵は値の投入を待ってから注入する（github-app-client-secret と同じ理由）
  bundle_signing_env = var.mount_bundle_signing_key ? {
    SPECLINO_BUNDLE_SIGNING_KEY = "bundle-signing-key"
  } : {}

  # Cloud Run の環境変数として参照する秘密（役割 → 環境変数名 → secret_id）。
  # 値は state に入らない（参照だけを宣言する）。mount_secrets = false のあいだは
  # 何も参照しないので、まだ値を入れていない秘密があっても起動できる。
  secret_env = {
    web = merge(
      {
        SPECLINO_GITHUB_WEBHOOK_SECRET = "github-webhook-secret"
        SPECLINO_DATABASE_URL          = "database-url"
      },
      # 値（バージョン）の無い secret を注入すると新しいリビジョンが起動できない。client secret は
      # ユーザーが値を投入して github_client_id を tfvars に書いてから注入する（2 回目の apply）
      var.github_client_id == "" ? {} : {
        SPECLINO_GITHUB_CLIENT_SECRET = "github-app-client-secret"
      },
      local.bundle_signing_env,
    )
    worker = merge(
      {
        SPECLINO_GITHUB_PRIVATE_KEY = "github-app-private-key"
        SPECLINO_DATABASE_URL       = "database-url"
      },
      local.bundle_signing_env,
    )
    sandbox = {}
  }

  secret_accessor_bindings = {
    for pair in flatten([
      for secret_id, sa_keys in local.secret_accessors : [
        for sa_key in sa_keys : {
          key       = "${secret_id}-${sa_key}"
          secret_id = secret_id
          sa_key    = sa_key
        }
      ]
    ]) : pair.key => pair
  }
}

resource "google_secret_manager_secret" "this" {
  for_each = local.secret_accessors

  project   = var.project_id
  secret_id = each.key

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "accessors" {
  for_each = local.secret_accessor_bindings

  project   = var.project_id
  secret_id = google_secret_manager_secret.this[each.value.secret_id].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.this[each.value.sa_key].email}"
}
