/**
 * 人の確定（S70）の判断。バックエンドの services/attestations.py の attestation_target と同じ規則:
 * 確定できるのは REVIEW で、指紋と内容の hash を持つもの（LLM 支援の issue / split と
 * KNOWLEDGE-BINDINGS-001）だけ。確定ボタンを出せるのは owner / approver だけ。
 */

import type { Attestation, AttestationTarget } from '../types/attestation'
import type { Finding } from '../types/finding'
import type { WorkspaceRole } from '../types/me'

const FINGERPRINT_REF = 'fingerprint:'
const CONTENT_HASH_REF = 'content_hash:'
const CONFIG_CHANGE_REQUIREMENT_ID = 'KNOWLEDGE-BINDINGS-001'

function refValue(refs: string[], prefix: string): string | null {
  const found = refs.find((r) => r.startsWith(prefix))
  return found ? found.slice(prefix.length) : null
}

export function attestationTarget(finding: Finding): AttestationTarget | null {
  if (finding.verdict !== 'REVIEW') return null
  const outcome = finding.assessment_outcome ?? null
  const llmIssue = outcome === 'issue' || outcome === 'split'
  const configChange = outcome === null && finding.requirement_id === CONFIG_CHANGE_REQUIREMENT_ID
  if (!llmIssue && !configChange) return null
  const fingerprint = refValue(finding.evidence_refs, FINGERPRINT_REF)
  const contentHash = refValue(finding.evidence_refs, CONTENT_HASH_REF)
  if (!fingerprint || !contentHash) return null
  return { fingerprint, contentHash }
}

/** この run のゲートで効いた確定のうち、対象に一致するもの。 */
export function recordedAttestation(
  target: AttestationTarget,
  attestations: Attestation[] | undefined,
): Attestation | null {
  return (
    (attestations ?? []).find(
      (a) => a.fingerprint === target.fingerprint && a.content_hash === target.contentHash,
    ) ?? null
  )
}

/** 確定の表示の文言。自己確定（PR の作成者による確定）は必ず明示する。 */
export function describeAttestation(a: Pick<Attestation, 'disposition' | 'self_attested'>): string {
  const base = a.disposition === 'attested' ? '問題なしとして確定済み' : '問題として記録済み（阻止したまま）'
  return a.self_attested ? `${base}・自己確定` : base
}

export function canAttest(role: WorkspaceRole | null | undefined): boolean {
  return role === 'owner' || role === 'approver'
}
