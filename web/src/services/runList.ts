/** 検査の一覧（`/w/:slug/runs?gate=&project=`）の絞り込みと、ホームの「ゲートが失敗のままの PR」。 */

import type { RunFilter, RunGateFilter } from '@/types/navigation'
import type { ReviewRunSummary } from '@/types/run'

export const RUN_GATE_FILTERS: RunGateFilter[] = ['fail', 'pass_with_exceptions', 'pass', 'running']

/** URL のクエリを絞り込みに。知らないゲートは絞らない。 */
export function parseRunFilter(params: URLSearchParams): RunFilter {
  const gate = params.get('gate')
  return {
    gate: RUN_GATE_FILTERS.find((g) => g === gate) ?? null,
    project: params.get('project') || null,
  }
}

/** ゲート（running はまだ判定が無いもの）とプロジェクト（owner/repo）で絞る。並びはそのまま。 */
export function filterRuns(runs: ReviewRunSummary[], filter: RunFilter): ReviewRunSummary[] {
  return runs.filter(
    (r) =>
      (!filter.project || r.repo_full_name === filter.project) &&
      (!filter.gate || (filter.gate === 'running' ? r.gate_status === null : r.gate_status === filter.gate)),
  )
}

/** PR ごとに最新の検査を見て、ゲートが失敗のもの（違反か要確認が止めている）。新しい順。 */
export function failingPullRequests(runs: ReviewRunSummary[]): ReviewRunSummary[] {
  const latest = new Map<string, ReviewRunSummary>()
  for (const r of runs) {
    if (r.pr_number === null || !r.repo_full_name) continue
    const key = `${r.repo_full_name}#${r.pr_number}`
    const seen = latest.get(key)
    if (!seen || (r.created_at ?? '') > (seen.created_at ?? '')) latest.set(key, r)
  }
  return [...latest.values()]
    .filter((r) => r.gate_status === 'fail')
    .sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))
}
