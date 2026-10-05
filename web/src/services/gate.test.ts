import { describe, expect, it } from 'vitest'
import { describeGateReason } from './gate'
import { countByVerdict, splitCleared } from './verdict'
import type { Finding } from '@/types/finding'

const HASH = 'sha256:' + 'a'.repeat(64)

function finding(over: Partial<Finding>): Finding {
  return {
    id: 'f',
    requirement_id: 'R',
    behavior_id: 'b',
    verdict: 'REVIEW',
    category: 'potential_violation',
    severity: 'high',
    message: 'm',
    expected: null,
    actual: null,
    counterexample: null,
    evidence_refs: ['fingerprint:R:i:abc', `content_hash:${HASH}`],
    assumptions: [],
    claim_scope: 'persisted_state',
    analysis_scope: null,
    code_refs: [],
    spec_refs: [],
    base_head_status: null,
    test_observation: null,
    next_steps: [],
    assessment_outcome: 'issue',
    ...over,
  }
}

describe('件数と失敗の理由は cleared を除いた同じ集合から作る', () => {
  const findings = [
    finding({ id: 'v', verdict: 'VIOLATED', assessment_outcome: null }),
    finding({ id: 'issue' }),
    finding({ id: 'n1', assessment_outcome: 'no_issue' }),
    finding({ id: 'n2', assessment_outcome: 'no_issue' }),
    finding({ id: 'u', verdict: 'UNKNOWN', assessment_outcome: null }),
  ]

  it('バックエンドの cleared_ids だけを除く（画面で推測しない）', () => {
    const { active, cleared } = splitCleared(findings, ['n1', 'n2'])
    expect(cleared.map((f) => f.id)).toEqual(['n1', 'n2'])
    expect(countByVerdict(active)).toEqual({ VIOLATED: 1, REVIEW: 1, UNKNOWN: 1, VERIFIED_IN_SCOPE: 0 })
    expect(describeGateReason('fail', null, active)).toBe('違反 1件 / 未検証 1件 / 要確認 1件 が残っているため失敗しました')
  })

  it('確定が効いた指摘も cleared に入っていれば数えない', () => {
    const { active } = splitCleared(findings, ['issue', 'n1', 'n2'])
    expect(describeGateReason('fail', null, active)).toBe('違反 1件 / 未検証 1件 が残っているため失敗しました')
  })

  it('cleared_ids の無い古い run は全部を数える。REVIEW 以外の ID は cleared にしない', () => {
    expect(splitCleared(findings, undefined).cleared).toEqual([])
    const { active, cleared } = splitCleared(findings, ['v', 'u'])
    expect(cleared).toEqual([])
    expect(active).toHaveLength(5)
  })
})
