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

  it('免除中の規約は、要確認の後ろに期限の近い順で入る', () => {
    const policies = [
      { rule_id: 'P-LATE', origin: 'team' as const, title: '遅い', level: null, has_verifier: true, status: 'VIOLATED' as const,
        counts: { VERIFIED_IN_SCOPE: 0, VIOLATED: 1, UNKNOWN: 0, REVIEW: 0 }, waived: 1, exempted: true,
        waivers: [{ exception_id: 'E2', expires_on: '2026-12-31', reason: 'r', alternative: '直し方2' }] },
      { rule_id: 'P-SOON', origin: 'team' as const, title: '近い', level: null, has_verifier: true, status: 'VIOLATED' as const,
        counts: { VERIFIED_IN_SCOPE: 0, VIOLATED: 1, UNKNOWN: 0, REVIEW: 0 }, waived: 1, exempted: true,
        waivers: [{ exception_id: 'E1', expires_on: '2026-11-01', reason: 'r', alternative: '直し方1' }] },
    ]
    const reqs = [req('Q', { verdict: 'REVIEW' }), req('K', { verdict: 'UNKNOWN' })]
    expect(nextActions(reqs, policies).map((a) => [a.kind, a.requirementId])).toEqual([
      ['review', 'Q'], ['exempted', 'P-SOON'], ['exempted', 'P-LATE'], ['unknown', 'K'],
    ])
    expect(nextActions([], policies)[0].hint).toBe('免除の期限 2026-11-01 までに直す: 直し方1')
  })
})

