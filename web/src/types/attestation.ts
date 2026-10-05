/** 人の確定（S70）。src/speclino/domain/assessment.py の Attestation と api/routes/attestations.py。 */

export type Disposition = 'attested' | 'acknowledged_violation'

export interface Attestation {
  fingerprint: string
  content_hash: string
  disposition: Disposition
  actor: string
  workspace: string
  created_at: string
  /**
   * PR の作成者が自分で確定したか（GitHub のユーザー ID で照合。拒否はせず印を残す）。
   * null は作成者との照合不可（確定者が GitHub 未連携、または PR の作成者の ID が無い run）
   */
  self_attested?: boolean | null
}

/** Finding から取り出した確定の対象（指紋と内容の hash）。 */
export interface AttestationTarget {
  fingerprint: string
  contentHash: string
}

export interface AttestRequest {
  fingerprint: string
  content_hash: string
  disposition: Disposition
  run_id?: string
}

export interface AttestResponse {
  attestation: Attestation
  recheck_run_id: string | null
}
