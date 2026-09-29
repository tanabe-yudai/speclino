/** リポジトリ登録（S55）のエンドポイント関数。VITE_MOCK=1 のときは固定値を返す。 */

import { apiFetch } from './client'
import type { AuthorizeResponse, RegisteredRepository, RegistrationResult } from '../types/repository'

const USE_MOCK = import.meta.env.VITE_MOCK === '1'

export async function listRepositories(slug: string): Promise<RegisteredRepository[]> {
  if (USE_MOCK) {
    return [{ repo_full_name: 'tanabe-yudai/spec-lino', github_repo_id: 1, default_branch: 'main' }]
  }
  return apiFetch<RegisteredRepository[]>(`/api/workspaces/${slug}/repositories`)
}

export async function startRegistration(slug: string, repoFullName: string): Promise<AuthorizeResponse> {
  if (USE_MOCK) return { authorize_url: '/github/callback?code=mock&state=mock' }
  return apiFetch<AuthorizeResponse>(`/api/workspaces/${slug}/repositories/authorize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo_full_name: repoFullName }),
  })
}

export async function completeRegistration(code: string, state: string): Promise<RegistrationResult> {
  if (USE_MOCK) return { slug: 'demo', repo_full_name: 'tanabe-yudai/spec-lino', status: 'registered' }
  return apiFetch<RegistrationResult>('/api/github/callback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, state }),
  })
}
