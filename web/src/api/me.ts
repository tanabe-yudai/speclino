/** `/api/me`（ログイン中の利用者と所属ワークスペース）。VITE_MOCK=1 のときは固定値を返す。 */

import { apiFetch } from './client'
import type { Me } from '../types/me'

const USE_MOCK = import.meta.env.VITE_MOCK === '1'

export async function getMe(): Promise<Me> {
  if (USE_MOCK) {
    return {
      uid: 'mock-user',
      email: null,
      email_verified: false,
      workspaces: [{ slug: 'demo', name: 'デモ', role: 'member' }],
    }
  }
  return apiFetch<Me>('/api/me')
}
