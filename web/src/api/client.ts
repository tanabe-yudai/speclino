/**
 * 同一オリジン `/api/...` への fetch ラッパ。認証ヘッダの付与とエラーの正規化だけを担う。
 * 集計・整形ロジックは持たない（services/ 側の責務）。
 */

import { getAuthHeaders } from '../auth/authHeaders'
import { ApiError } from './errors'

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const body: unknown = await response.clone().json()
    if (body && typeof body === 'object' && 'detail' in body) {
      const detail = (body as { detail?: unknown }).detail
      if (typeof detail === 'string') return detail
    }
  } catch {
    // JSON でなければ本文をそのまま使う。
  }
  const text = await response.text().catch(() => '')
  return text || response.statusText || `HTTP ${response.status}`
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const authHeaders = await getAuthHeaders()
  const response = await fetch(path, {
    ...init,
    headers: { ...authHeaders, ...init?.headers },
  })
  if (!response.ok) {
    throw new ApiError(response.status, await readErrorMessage(response))
  }
  const contentType = response.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) {
    return (await response.json()) as T
  }
  return (await response.text()) as unknown as T
}
