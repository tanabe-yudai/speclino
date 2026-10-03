import { describe, expect, it } from 'vitest'
import { explainGate, summarizePolicies } from './gateExplain'
import type { PolicyView } from '@/types/project'

const counts = { VERIFIED_IN_SCOPE: 0, VIOLATED: 0, UNKNOWN: 0, REVIEW: 0 }
const p = (over: Partial<PolicyView>): PolicyView => ({
  rule_id: 'X', origin: 'team', title: null, level: null, has_verifier: true, status: null, counts, waived: 0, ...over,
})

describe('summarizePolicies', () => {
  it('違反した規約の数と、そのうち例外で免除された数', () => {
    const s = summarizePolicies([
      p({ rule_id: 'A', status: 'VIOLATED', waived: 2 }),
      p({ rule_id: 'B', status: 'VIOLATED' }),
      p({ rule_id: 'C', status: 'VERIFIED_IN_SCOPE' }),
    ])
    expect(s).toEqual({ violated: 2, waived: 1 })
  })
})

describe('explainGate', () => {
  it('例外付き通過は、要求の違反が 0 でも「例外で免除した規約の違反がある」と書く', () => {
    expect(explainGate('pass_with_exceptions', { violated: 1, waived: 1 })).toBe(
      '規約の違反 1 件を、登録済みの例外で免除して通過しました',
    )
  })
  it('失敗・通過は説明を足さない（件数で分かる）', () => {
    expect(explainGate('fail', { violated: 1, waived: 0 })).toBeNull()
    expect(explainGate('pass', { violated: 0, waived: 0 })).toBeNull()
    expect(explainGate(null, { violated: 0, waived: 0 })).toBeNull()
  })
})
