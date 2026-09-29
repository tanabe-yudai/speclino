#!/usr/bin/env bash
# 審査員用のテストアカウントを Identity Platform に作る（S13 / 提出フォーム用）。
#
#   SPECLINO_PROJECT=<project_id> SPECLINO_FIREBASE_API_KEY=<apiKey> \
#     scripts/create_test_user.sh <email> [<password>]
#
# apiKey はコンソール → Identity Platform →「アプリケーションの設定の詳細」で取得する。
# 作った認証情報は提出フォームにだけ書き、リポジトリには置かない。
set -euo pipefail

: "${SPECLINO_FIREBASE_API_KEY:?SPECLINO_FIREBASE_API_KEY が必要}"
EMAIL="${1:?作成するアカウントのメールアドレスが必要}"
PASSWORD="${2:-$(openssl rand -base64 18)}"
PROJECT="${SPECLINO_PROJECT:?SPECLINO_PROJECT（GCP プロジェクト ID）が必要}"

response=$(curl -sf -X POST \
  "https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=${SPECLINO_FIREBASE_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\",\"returnSecureToken\":true}")

uid=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["localId"])' <<<"$response")
cat <<SUMMARY

テストアカウントを作成した（提出フォームにこの 2 行を記載する）:

  email:    ${EMAIL}
  password: ${PASSWORD}

uid: ${uid}

run を見られるのは所属ワークスペースのメンバーだけ（S53）。審査員として閲覧させるには member で追加する:

  gcloud run jobs execute speclino-admin --project=${PROJECT} --region=asia-northeast1 --wait \\
    --args=workspace,add-member,<slug>,${uid},--role,member
SUMMARY
