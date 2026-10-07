import { describe, expect, it } from 'vitest'
import {
  attestationTarget,
  canAttest,
  configChangeWarning,
  describeAttestation,
  prCommentCommand,
  requiresPrComment,
  recordedAttestation,
} from './attestation'
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
  it('LLM 支援の issue / split / no_issue は指紋と hash を返す（指摘なしも人の確定まで止める）', () => {
    expect(attestationTarget(finding({}))).toEqual({ fingerprint: 'R:i:abc', contentHash: HASH })
    expect(attestationTarget(finding({ assessment_outcome: 'split' }))).not.toBeNull()
    expect(attestationTarget(finding({ assessment_outcome: 'no_issue' }))).toEqual({ fingerprint: 'R:i:abc', contentHash: HASH })
  })
  it('REVIEW 以外・指紋の無いもの・LLM 支援でも対応の変更でもないものは確定できない', () => {
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
  it('自己確定・作成者以外・照合不可を区別して明示する', () => {
    expect(describeAttestation(att)).toContain('自己確定')
    expect(describeAttestation({ ...att, self_attested: false })).toContain('作成者以外の確定')
    expect(describeAttestation({ ...att, self_attested: null })).toContain('PR の作成者かどうかは確認できません')
  })
  it('KNOWLEDGE-BINDINGS-001 の自己確定・照合不可は警告する', () => {
    const cfg = { ...att, fingerprint: `KNOWLEDGE-BINDINGS-001:${HASH}` }
    expect(configChangeWarning(cfg)).toContain('PR の作成者自身')
    expect(configChangeWarning({ ...cfg, self_attested: null })).toContain('照合できない')
    expect(configChangeWarning({ ...cfg, self_attested: false })).toBeNull()
    expect(configChangeWarning(att)).toBeNull()
  })
})

describe('requiresPrComment', () => {
  it('KNOWLEDGE-BINDINGS-001 は PR コメントのコマンドを出す', () => {
    const target = { fingerprint: `KNOWLEDGE-BINDINGS-001:${HASH}`, contentHash: HASH }
    expect(requiresPrComment(target)).toBe(true)
    expect(prCommentCommand(target)).toBe(`/speclino attest KNOWLEDGE-BINDINGS-001:${HASH} ${HASH}`)
    expect(requiresPrComment({ fingerprint: 'R:i:abc', contentHash: HASH })).toBe(false)
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
