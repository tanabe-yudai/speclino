/** `/api/review-runs` 系のエンドポイント関数。VITE_MOCK=1 のときは fixture を返す。 */

import { apiFetch } from './client'
import { ApiError } from './errors'
import { MOCK_EVENTS, MOCK_REVIEW_MARKDOWN, MOCK_RUNS, MOCK_RUN_DETAILS } from './mock/fixtures'
import type { RunEvent } from '../types/events'
import type { AgentSupplement, SupplementsResponse } from '../types/supplement'
import type { RerunResponse, ReviewRunDetail, ReviewRunSummary } from '../types/run'

const USE_MOCK = import.meta.env.VITE_MOCK === '1'

/** workspace を渡すと、そのワークスペースの run だけを API 側で絞る（他のワークスペースに押し出されない）。 */
export async function listRuns(limit = 50, workspace?: string): Promise<ReviewRunSummary[]> {
  if (USE_MOCK) return MOCK_RUNS.slice(0, limit)
  const query = new URLSearchParams({ limit: String(limit) })
  if (workspace) query.set('workspace', workspace)
  return apiFetch<ReviewRunSummary[]>(`/api/review-runs?${query.toString()}`)
}

export async function getRun(id: string): Promise<ReviewRunDetail> {
  if (USE_MOCK) {
    const run = MOCK_RUN_DETAILS[id]
    if (!run) throw new ApiError(404, 'run が無い')
    return run
  }
  return apiFetch<ReviewRunDetail>(`/api/review-runs/${id}`)
}

export async function getRunEvents(id: string): Promise<RunEvent[]> {
  if (USE_MOCK) return MOCK_EVENTS[id] ?? []
  return apiFetch<RunEvent[]>(`/api/review-runs/${id}/events`)
}

/** エージェントからの補足（S59）。正式な回答ではない。モックでは補足なし。 */
export async function getSupplements(id: string): Promise<AgentSupplement[]> {
  if (USE_MOCK) return []
  const res = await apiFetch<SupplementsResponse>(`/api/review-runs/${id}/supplements`)
  return res.supplements
}

export async function getReviewMarkdown(id: string): Promise<string> {
  if (USE_MOCK) {
    const markdown = MOCK_REVIEW_MARKDOWN[id]
    if (!markdown) throw new ApiError(404, '成果物が無い')
    return markdown
  }
  return apiFetch<string>(`/api/review-runs/${id}/artifacts/review.md`)
}

export async function rerunRun(id: string): Promise<RerunResponse> {
  if (USE_MOCK) {
    return { run_id: id, used_today: 1, limit: 20, remaining: 19, cost_ok: true, cost_note: 'mock' }
  }
  return apiFetch<RerunResponse>(`/api/review-runs/${id}/rerun`, { method: 'POST' })
}
