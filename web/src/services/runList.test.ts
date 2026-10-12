import { describe, expect, it } from 'vitest'
import type { ReviewRunSummary } from '@/types/run'
import { failingPullRequests, filterRuns, parseRunFilter } from './runList'

function run(id: string, repo: string, pr: number | null, gate: ReviewRunSummary['gate_status'], at: string): ReviewRunSummary {
  return {
    id,
    repo_full_name: repo,
    pr_number: pr,
    head_sha: 'abc',
    base_sha: null,
    trigger: 'rerun',
    exec_state: gate ? 'completed' : 'running',
    gate_status: gate,
    check_run_id: null,
    artifacts_uri: null,
    manifest: null,
    budget: null,
    created_at: at,
    finished_at: null,
  }
}

const runs = [
  run('r3', 'org/shop', 1, 'pass', '2026-10-03'),
  run('r2', 'org/shop', 1, 'fail', '2026-10-02'),
  run('r1', 'org/api', 7, 'fail', '2026-10-01'),
  run('r0', 'org/api', 8, null, '2026-10-04'),
]

describe('parseRunFilter / filterRuns', () => {
  it('ゲートとプロジェクトで絞る（running は判定の無いもの）', () => {
    expect(parseRunFilter(new URLSearchParams('gate=fail&project=org/api'))).toEqual({ gate: 'fail', project: 'org/api' })
    expect(parseRunFilter(new URLSearchParams('gate=x'))).toEqual({ gate: null, project: null })
    expect(filterRuns(runs, { gate: 'fail', project: null }).map((r) => r.id)).toEqual(['r2', 'r1'])
    expect(filterRuns(runs, { gate: 'running', project: null }).map((r) => r.id)).toEqual(['r0'])
    expect(filterRuns(runs, { gate: null, project: 'org/shop' }).map((r) => r.id)).toEqual(['r3', 'r2'])
  })
})

describe('failingPullRequests', () => {
  it('PR ごとの最新の検査が失敗のものだけ（後で通った PR は出さない）', () => {
    expect(failingPullRequests(runs).map((r) => r.id)).toEqual(['r1'])
  })
})
