/**
 * API 呼び出しに付ける認証ヘッダを組み立てる。React に依存しないプレーン関数にして、
 * api/client.ts（コンポーネント外）から使えるようにする。
 */

import { DEV_USER_HEADER_NAME, DEV_USER_HEADER_VALUE, isDevSessionActive } from './devAuth'
import { getCurrentIdToken, isFirebaseConfigured } from './firebaseClient'

export async function getAuthHeaders(): Promise<Record<string, string>> {
  if (isFirebaseConfigured) {
    const token = await getCurrentIdToken()
    return token ? { Authorization: `Bearer ${token}` } : {}
  }
  if (isDevSessionActive()) {
    return { [DEV_USER_HEADER_NAME]: DEV_USER_HEADER_VALUE }
  }
  return {}
}
