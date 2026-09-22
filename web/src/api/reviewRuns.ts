/** `/api/review-runs` 系のエンドポイント関数。VITE_MOCK=1 のときは fixture を返す。 */

import { apiFetch } from './client'
import { ApiError } from './errors'
import { MOCK_EVENTS, MOCK_REVIEW_MARKDOWN, MOCK_RUNS, MOCK_RUN_DETAILS } from './mock/fixtures'
import type { RunEvent } from '../types/events'
import type { ReviewRunDetail, ReviewRunSummary } from '../types/run'

const USE_MOCK = import.meta.env.VITE_MOCK === '1'

export async function listRuns(limit = 50): Promise<ReviewRunSummary[]> {
  if (USE_MOCK) return MOCK_RUNS.slice(0, limit)
  return apiFetch<ReviewRunSummary[]>(`/api/review-runs?limit=${limit}`)
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

export async function getReviewMarkdown(id: string): Promise<string> {
  if (USE_MOCK) {
    const markdown = MOCK_REVIEW_MARKDOWN[id]
    if (!markdown) throw new ApiError(404, '成果物が無い')
    return markdown
  }
  return apiFetch<string>(`/api/review-runs/${id}/artifacts/review.md`)
}
