#!/usr/bin/env bash
# 本番デプロイの動作確認（S26 / AC: docs/guides/deploy-runbook.md の手順 8）。
#
#   scripts/smoke_prod.sh [<web URL>]
#
# web URL を省略すると gcloud から取得する。テストアカウントでログインして run を 1 件
# 取得するところまで確認したい場合は SPECLINO_SMOKE_EMAIL / SPECLINO_SMOKE_PASSWORD と
# SPECLINO_FIREBASE_API_KEY を設定する（未設定なら認証なしで確認できる範囲だけ見る）。
set -euo pipefail

PROJECT="${SPECLINO_PROJECT:?SPECLINO_PROJECT（GCP プロジェクト ID）が必要}"
REGION="${SPECLINO_REGION:-asia-northeast1}"
WEB_URL="${1:-}"

if [[ -z "$WEB_URL" ]]; then
  WEB_URL=$(gcloud run services describe speclino-web \
    --project="$PROJECT" --region="$REGION" --format='value(status.url)')
fi
echo "web: $WEB_URL"

fail() { echo "NG: $*" >&2; exit 1; }
ok() { echo "OK: $*"; }

# 1) ヘルスチェック
body=$(curl -sf --max-time 30 "$WEB_URL/health") || fail "/health に到達できない"
[[ "$body" == *'"status":"ok"'* ]] || fail "/health が ok を返さない: $body"
[[ "$body" == *'"role":"web"'* ]] || fail "role が web ではない: $body"
ok "/health ($body)"

# 2) SPA とディープリンク
[[ "$(curl -so /dev/null -w '%{http_code}' "$WEB_URL/")" == "200" ]] || fail "SPA が返らない"
[[ "$(curl -so /dev/null -w '%{http_code}' "$WEB_URL/runs/x")" == "200" ]] ||
  fail "ディープリンクが index.html にフォールバックしない"
ok "SPA とディープリンク"

# 2b) フロントエンドのビルドに Firebase の authDomain が埋め込まれているか
#     （2026-09-28: Actions の変数が空のままビルドされ、ログインできなくなった）。
#     値そのものは固定せず、<名前>.firebaseapp.com / .web.app の形だけ見る
html=$(curl -sf --max-time 30 "$WEB_URL/") || fail "index.html を取得できない"
bundles=$(grep -oE '/assets/[^"]+\.js' <<<"$html" | sort -u) || true
[[ -n "$bundles" ]] || fail "index.html から JS バンドルを見つけられない"
auth_domain=""
while read -r path; do
  js=$(curl -sf --max-time 60 "$WEB_URL$path") || fail "バンドル $path を取得できない"
  found=$(grep -oE "authDomain:[\`\"'][A-Za-z0-9.-]+\.(firebaseapp\.com|web\.app)[\`\"']" <<<"$js" | head -n1) || true
  if [[ -n "$found" ]]; then auth_domain="$found"; break; fi
done <<<"$bundles"
[[ -n "$auth_domain" ]] ||
  fail "バンドルに authDomain（*.firebaseapp.com / *.web.app）が無い。Actions の変数 VITE_FIREBASE_AUTH_DOMAIN を確認してビルドし直す"
ok "フロントエンドに authDomain が埋め込まれている（${auth_domain}）"

# 3) 認証が要るエンドポイントは 401（ErrorResponse 形式）
code=$(curl -so /tmp/smoke_api.json -w '%{http_code}' "$WEB_URL/api/review-runs")
case "$code" in
  401) ;;
  200) fail "/api/review-runs が未認証で 200。SPECLINO_ENV=prod と SPECLINO_AUTH_KIND=identity_platform を確認する" ;;
  500) fail "/api/review-runs が 500。prod で dev 認証のままになっている（SPECLINO_AUTH_KIND）" ;;
  *) fail "/api/review-runs が未認証で ${code}（401 であるべき）" ;;
esac
grep -q '"error"' /tmp/smoke_api.json || fail "エラー応答が ErrorResponse 形式でない"
ok "未認証は 401（$(cat /tmp/smoke_api.json)）"

# 4) webhook は署名なしを拒否する
code=$(curl -so /dev/null -w '%{http_code}' -X POST "$WEB_URL/webhooks/github" \
  -H 'Content-Type: application/json' -d '{}')
[[ "$code" == "401" ]] || fail "webhook が署名なしで ${code}（401 であるべき）"
ok "webhook は署名なしを拒否"

# 5) MCP は web ロールで常にマウントされる（S54: ワークスペース単位のトークン、DB 照合）。
#    Bearer なし・不正なトークンはどちらも 401（無い・失効済みを区別しない）
code=$(curl -so /dev/null -w '%{http_code}' -X POST "$WEB_URL/mcp" -d '{}')
[[ "$code" == "401" ]] || fail "MCP が Bearer なしで ${code}（401 であるべき）"
# 形は正しいが発行されていない値（gitleaks が本物の秘密と取り違えないよう変数から渡す）
unknown_token="slmcp_$(openssl rand -hex 8)"
code=$(curl -so /dev/null -w '%{http_code}' -X POST "$WEB_URL/mcp" \
  -H "Authorization: Bearer ${unknown_token}" -d '{}')
[[ "$code" == "401" ]] || fail "MCP が不正なトークンで ${code}（401 であるべき）"
ok "MCP は Bearer を要求し、不正なトークンも拒否する"

# 6) ログインして run を取得する（任意）
if [[ -n "${SPECLINO_SMOKE_EMAIL:-}" && -n "${SPECLINO_SMOKE_PASSWORD:-}" &&
      -n "${SPECLINO_FIREBASE_API_KEY:-}" ]]; then
  token=$(curl -sf -X POST \
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${SPECLINO_FIREBASE_API_KEY}" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"${SPECLINO_SMOKE_EMAIL}\",\"password\":\"${SPECLINO_SMOKE_PASSWORD}\",\"returnSecureToken\":true}" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["idToken"])') ||
    fail "テストアカウントでログインできない"
  runs=$(curl -sf "$WEB_URL/api/review-runs" -H "Authorization: Bearer $token") ||
    fail "ID トークンで /api/review-runs を取得できない"
  count=$(python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' <<<"$runs")
  ok "テストアカウントで run 一覧を取得（$count 件）"
  [[ "$count" -gt 0 ]] || echo "WARN: run が 0 件（デモ PR がまだ）"
else
  echo "SKIP: ログイン確認（SPECLINO_SMOKE_EMAIL / PASSWORD / SPECLINO_FIREBASE_API_KEY 未設定）"
fi

echo "smoke: 全て通過"
