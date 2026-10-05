import { describe, expect, it } from 'vitest'
import { attestationTarget, canAttest, describeAttestation, recordedAttestation } from './attestation'
import type { Finding } from '@/types/finding'

const HASH = 'sha256:' + 'a'.repeat(64)

function finding(over: Partial<Finding>): Finding {
  return {
    id: 'f',
    requirement_id: 'R:app.py',
    behavior_id: 'policy:llm.checklist',
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

describe('attestationTarget', () => {
  it('LLM 支援の issue / split は指紋と hash を返す', () => {
    expect(attestationTarget(finding({}))).toEqual({ fingerprint: 'R:i:abc', contentHash: HASH })
    expect(attestationTarget(finding({ assessment_outcome: 'split' }))).not.toBeNull()
  })
  it('指摘なし・REVIEW 以外・指紋の無いものは確定できない', () => {
    expect(attestationTarget(finding({ assessment_outcome: 'no_issue' }))).toBeNull()
    expect(attestationTarget(finding({ verdict: 'UNKNOWN' }))).toBeNull()
    expect(attestationTarget(finding({ evidence_refs: [] }))).toBeNull()
    expect(attestationTarget(finding({ assessment_outcome: null }))).toBeNull()
  })
  it('対応・規約の変更の通知（KNOWLEDGE-BINDINGS-001）も確定できる', () => {
    const notice = finding({ requirement_id: 'KNOWLEDGE-BINDINGS-001', assessment_outcome: null })
    expect(attestationTarget(notice)?.contentHash).toBe(HASH)
  })
})

describe('recordedAttestation / describeAttestation', () => {
  const att = {
    fingerprint: 'R:i:abc',
    content_hash: HASH,
    disposition: 'attested' as const,
    actor: 'u',
    workspace: 'w',
    created_at: '2026-10-05T00:00:00Z',
    self_attested: true,
  }
  it('指紋と hash が一致する確定を返す', () => {
    expect(recordedAttestation({ fingerprint: 'R:i:abc', contentHash: HASH }, [att])).toBe(att)
    expect(recordedAttestation({ fingerprint: 'R:i:abc', contentHash: 'x' }, [att])).toBeNull()
  })
  it('自己確定を明示する', () => {
    expect(describeAttestation(att)).toContain('自己確定')
    expect(describeAttestation({ ...att, self_attested: false })).not.toContain('自己確定')
  })
})

describe('canAttest', () => {
  it('owner と approver だけ', () => {
    expect(canAttest('owner')).toBe(true)
    expect(canAttest('approver')).toBe(true)
    expect(canAttest('member')).toBe(false)
    expect(canAttest(null)).toBe(false)
  })
})
