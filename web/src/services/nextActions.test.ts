import { describe, expect, it } from 'vitest'
import { nextActions } from './nextActions'
import type { RequirementView } from '@/types/project'

function req(id: string, over: Partial<RequirementView>): RequirementView {
  return {
    id, behavior_id: 'b', title: null, spec_path: null, line_start: null, line_end: null,
    approval: 'approved', tests_declared: 0, verdict: 'VERIFIED_IN_SCOPE', finding_ids: [], removed: false, ...over,
  }
}

describe('nextActions', () => {
  it('違反 → 承認切れ → 要確認 → 未検証 → 未承認 の順', () => {
    const reqs = [
      req('U', { approval: 'unapproved', verdict: 'REVIEW' }),
      req('K', { verdict: 'UNKNOWN' }),
      req('Q', { verdict: 'REVIEW' }),
      req('S', { approval: 'stale', verdict: 'REVIEW' }),
      req('V', { verdict: 'VIOLATED' }),
      req('OK', {}),
    ]
    expect(nextActions(reqs).map((a) => [a.kind, a.requirementId])).toEqual([
      ['violated', 'V'], ['stale', 'S'], ['review', 'Q'], ['unknown', 'K'], ['unapproved', 'U'],
    ])
  })

  it('未承認の要確認は「要確認」ではなく「未承認」に入る', () => {
    expect(nextActions([req('U', { approval: 'unapproved', verdict: 'REVIEW' })]).map((a) => a.kind)).toEqual(['unapproved'])
  })

  it('最大 5 件、同じ要求は 1 回だけ', () => {
    const reqs = Array.from({ length: 8 }, (_, i) => req(`V${i}`, { verdict: 'VIOLATED' }))
    const out = nextActions(reqs)
    expect(out).toHaveLength(5)
    expect(new Set(out.map((a) => a.requirementId)).size).toBe(5)
  })

  it('何も無ければ空', () => {
    expect(nextActions([req('OK', {})])).toEqual([])
  })
})
