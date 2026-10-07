/**
 * 開発モード（Firebase 未設定時）の認証。
 * `SPECLINO_AUTH_KIND=dev` のバックエンドは `X-Dev-User` ヘッダだけで通す。
 * ログイン画面は「開発用ログイン」ボタンをクリックしたことをセッション内で覚えておく。
 */

export const DEV_USER_HEADER_NAME = 'X-Dev-User'
export const DEV_USER_HEADER_VALUE = 'judge'

const SESSION_KEY = 'speclino.devAuthenticated'

export function isDevSessionActive(): boolean {
  try {
    return sessionStorage.getItem(SESSION_KEY) === '1'
  } catch {
    // プライベートブラウジング等で sessionStorage が使えない場合は未ログイン扱い。
    return false
  }
}

export function markDevSessionActive(): void {
  try {
    sessionStorage.setItem(SESSION_KEY, '1')
  } catch {
    // 保存できなくても致命的ではない（このタブ内の状態はメモリ側の AuthProvider が持つ）。
  }
}

export function clearDevSession(): void {
  try {
    sessionStorage.removeItem(SESSION_KEY)
  } catch {
    // 無視してよい。
  }
}
