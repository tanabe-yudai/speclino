/** 人の確定（S70）のエンドポイント関数。判断は持たない。 */

import { apiFetch } from './client'
import type { AttestRequest, AttestResponse } from '../types/attestation'

export function createAttestation(slug: string, body: AttestRequest): Promise<AttestResponse> {
  return apiFetch<AttestResponse>(`/api/workspaces/${encodeURIComponent(slug)}/attestations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}
