# メール/パスワード認証のみ有効化する（IAP は Google アカウント必須でハッカソン
# 要項に抵触するため不採用、Identity Platform のメール/パスワードを採用）。
resource "google_identity_platform_config" "default" {
  project = var.project_id

  sign_in {
    email {
      enabled           = true
      password_required = true
    }
  }

  # Cloud Run web サービスの URL は apply 後に確定するため、authorized_domains は
  # 初回 apply 後に手動追加するか、次回 apply で以下のように差し込む（S13 で対応）。
  # authorized_domains = [
  #   trimprefix(google_cloud_run_v2_service.services["web"].uri, "https://"),
  # ]
}
