import { describe, expect, it } from 'vitest'
import { describeGateReason } from './gate'
import type { Finding } from '@/types/finding'
import type { RunManifest } from '@/types/run'

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

describe('describeGateReason', () => {
  it('LLM 支援の「指摘なし」は失敗の理由の件数に数えない', () => {
    const findings = [
      finding({ id: 'v', verdict: 'VIOLATED', assessment_outcome: null }),
      finding({ id: 'n1', assessment_outcome: 'no_issue' }),
      finding({ id: 'n2', assessment_outcome: 'no_issue' }),
    ]
    expect(describeGateReason('fail', null, findings)).toBe('違反 1件 が残っているため失敗しました')
  })

  it('この run で問題なしと確定した指摘は数えず、阻止したままの記録は数える', () => {
    const attestation = {
      fingerprint: 'R:i:abc',
      content_hash: HASH,
      disposition: 'attested' as const,
      actor: 'judge',
      workspace: 'w',
      created_at: '2026-10-05T00:00:00Z',
      self_attested: false,
    }
    const manifest = { attestations: [attestation] } as unknown as RunManifest
    const findings = [finding({}), finding({ id: 'u', verdict: 'UNKNOWN', assessment_outcome: null })]
    expect(describeGateReason('fail', manifest, findings)).toBe('未検証 1件 が残っているため失敗しました')
    const kept = { attestations: [{ ...attestation, disposition: 'acknowledged_violation' }] } as unknown as RunManifest
    expect(describeGateReason('fail', kept, findings)).toBe('未検証 1件 / 要確認 1件 が残っているため失敗しました')
  })
})
