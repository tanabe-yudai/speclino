import { describe, expect, it } from 'vitest'
import { describeGateReason } from './gate'
import { assessmentOutcomeLabel } from './labels'
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

describe('LLM 支援の「指摘なし」も人の確定までは要確認として数える（#114）', () => {
  const findings = [
    finding({ id: 'n1', assessment_outcome: 'no_issue' }),
    finding({ id: 'n2', assessment_outcome: 'no_issue' }),
  ]

  it('cleared_ids に無い指摘なしは要確認に残り、失敗の理由に入る', () => {
    const { active, cleared } = splitCleared(findings, [])
    expect(cleared).toEqual([])
    expect(describeGateReason('fail', null, active)).toBe('要確認 2件 が残っているため失敗しました')
  })

  it('確定待ちと確定済みで区分の文言を分ける', () => {
    expect(assessmentOutcomeLabel('no_issue', false)).toBe('AI で確認、指摘なし（機械検証ではない）— 人の確定が必要')
    expect(assessmentOutcomeLabel('no_issue', true)).not.toContain('確定が必要')
  })
})
