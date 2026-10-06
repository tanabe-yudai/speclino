/** AI の書き換え提案（S67）のエンドポイント関数。判断は持たない。 */

import { apiFetch } from './client'
import type { SpecPreviewRequest } from '../types/knowledge'
import type { SpecSuggestions } from '../types/suggestion'

export function suggestSpec(slug: string, body: SpecPreviewRequest, signal?: AbortSignal): Promise<SpecSuggestions> {
  return apiFetch<SpecSuggestions>(`/api/workspaces/${encodeURIComponent(slug)}/specs/suggest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
}
