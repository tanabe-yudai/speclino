import { describe, expect, it } from 'vitest'
import { explainGate, summarizePolicies } from './gateExplain'
import type { PolicyView } from '@/types/project'

const counts = { VERIFIED_IN_SCOPE: 0, VIOLATED: 0, UNKNOWN: 0, REVIEW: 0 }
const p = (over: Partial<PolicyView>): PolicyView => ({
  rule_id: 'X', origin: 'team', title: null, level: null, has_verifier: true, status: null, counts,
  waived: 0, exempted: false, waivers: [], ...over,
})
const w = (id: string, expires: string) => ({ exception_id: id, expires_on: expires, reason: 'r', alternative: 'a' })

describe('summarizePolicies', () => {
  it('違反（免除されていない）と免除中を分け、免除の最短の期限を出す', () => {
    const s = summarizePolicies([
      p({ rule_id: 'A', status: 'VIOLATED', waived: 2, exempted: true, waivers: [w('E1', '2026-12-01')] }),
      p({ rule_id: 'B', status: 'VIOLATED' }),
      p({ rule_id: 'C', status: 'VIOLATED', exempted: true, waivers: [w('E2', '2026-11-15')] }),
      p({ rule_id: 'D', status: 'VERIFIED_IN_SCOPE' }),
    ])
    expect(s).toEqual({ violated: 1, exempted: 2, earliestExpiry: '2026-11-15' })
  })
})

describe('explainGate', () => {
  it('例外付き通過は、免除中の規約と最短の期限を書く', () => {
    expect(explainGate('pass_with_exceptions', { violated: 0, exempted: 1, earliestExpiry: '2026-12-01' })).toBe(
      '規約 1 件の違反を、期限付きの例外で免除中です（最短の期限 2026-12-01 までに直す）',
    )
  })
  it('失敗・通過は説明を足さない（件数で分かる）', () => {
    expect(explainGate('fail', { violated: 1, exempted: 0, earliestExpiry: null })).toBeNull()
    expect(explainGate('pass', { violated: 0, exempted: 0, earliestExpiry: null })).toBeNull()
    expect(explainGate(null, { violated: 0, exempted: 0, earliestExpiry: null })).toBeNull()
  })
})
