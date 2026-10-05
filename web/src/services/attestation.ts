/**
 * 人の確定（S70）の判断。バックエンドの services/attestations.py の attestation_target と同じ規則:
 * 確定できるのは REVIEW で、指紋と内容の hash を持つもの（LLM 支援の issue / split と
 * KNOWLEDGE-BINDINGS-001）だけ。確定ボタンを出せるのは owner / approver だけ。
 */

import type { AttestationTarget } from '../types/attestation'
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

export function canAttest(role: WorkspaceRole | null | undefined): boolean {
  return role === 'owner' || role === 'approver'
}
