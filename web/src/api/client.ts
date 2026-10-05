/**
 * 同一オリジン `/api/...` への fetch ラッパ。認証ヘッダの付与とエラーの正規化だけを担う。
 * 集計・整形ロジックは持たない（services/ 側の責務）。
 */

import { getAuthHeaders } from '../auth/authHeaders'
import { ApiError } from './errors'

async function readError(response: Response): Promise<{ message: string; code: string | null }> {
  try {
    const body: unknown = await response.clone().json()
    // API-ERR-001: エラー本文は {"error": {"code", "message"}} 形式
    if (body && typeof body === 'object' && 'error' in body) {
      const error = (body as { error?: { message?: unknown; code?: unknown } }).error
      if (error && typeof error.message === 'string') {
        return { message: error.message, code: typeof error.code === 'string' ? error.code : null }
      }
    }
  } catch {
    // JSON でなければ本文をそのまま使う。
  }
  const text = await response.text().catch(() => '')
  return { message: text || response.statusText || `HTTP ${response.status}`, code: null }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const authHeaders = await getAuthHeaders()
  const response = await fetch(path, {
    ...init,
    headers: { ...authHeaders, ...init?.headers },
  })
  if (!response.ok) {
    const { message, code } = await readError(response)
    throw new ApiError(response.status, message, code)
  }
  const contentType = response.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) {
    return (await response.json()) as T
  }
  return (await response.text()) as unknown as T
}
