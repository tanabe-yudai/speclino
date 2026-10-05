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

/** 自己確定の区分の文言。照合できないときは黙って「作成者以外」にしない。 */
export function selfAttestLabel(selfAttested: boolean | null | undefined): string {
  if (selfAttested === true) return '自己確定'
  if (selfAttested === false) return '作成者以外の確定'
  return '作成者との照合不可'
}

/** 確定の表示の文言。自己確定・照合不可は必ず明示する。 */
export function describeAttestation(a: Pick<Attestation, 'disposition' | 'self_attested'>): string {
  const base = a.disposition === 'attested' ? '問題なしとして確定済み' : '問題として記録済み（阻止したまま）'
  return `${base}・${selfAttestLabel(a.self_attested)}`
}

/**
 * 対応・契約・免除・規約の変更（KNOWLEDGE-BINDINGS-001）を、作成者自身か照合できない人が確定した
 * ときの警告（拒否はしない）。それ以外は null。
 */
export function configChangeWarning(a: Pick<Attestation, 'fingerprint' | 'self_attested'>): string | null {
  if (!a.fingerprint.startsWith(`${CONFIG_CHANGE_REQUIREMENT_ID}:`) || a.self_attested === false) return null
  const who = a.self_attested === true ? 'PR の作成者自身' : 'PR の作成者と照合できない人'
  return `${who}が判定の前提（対応・契約・免除・規約）の変更を確定しています。別の人によるレビューを推奨します`
}

export function canAttest(role: WorkspaceRole | null | undefined): boolean {
  return role === 'owner' || role === 'approver'
}
