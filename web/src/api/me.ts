/** `/api/me`（ログイン中の利用者と所属ワークスペース）。VITE_MOCK=1 のときは固定値を返す。 */

import { apiFetch } from './client'
import type { Me, MyProfile } from '../types/me'

const USE_MOCK = import.meta.env.VITE_MOCK === '1'

export async function getMe(): Promise<Me> {
  if (USE_MOCK) {
    return {
      uid: 'mock-user',
      email: null,
      email_verified: false,
      display_name: null,
      workspaces: [{ slug: 'demo', name: 'デモ', role: 'member' }],
    }
  }
  return apiFetch<Me>('/api/me')
}

/** 本人の表示名を変える（null で消す）。 */
export async function updateMyProfile(displayName: string | null): Promise<MyProfile> {
  if (USE_MOCK) return { uid: 'mock-user', display_name: displayName, email: null, email_verified: false }
  return apiFetch<MyProfile>('/api/me/profile', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_name: displayName }),
  })
}
