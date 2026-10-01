/** チーム規約（S43）のエンドポイント関数。VITE_MOCK=1 のときは固定値。 */

import { apiFetch } from './client'
import { MOCK_POLICIES, MOCK_POLICY_AUDIT, MOCK_REVISIONS } from './mock/policies'
import type { AuditEntry, PoliciesView, PolicyPackage, PolicyRevision, RevisionSummary } from '../types/policy'

const USE_MOCK = import.meta.env.VITE_MOCK === '1'
const base = (slug: string) => `/api/workspaces/${encodeURIComponent(slug)}/policies`
const json = { 'Content-Type': 'application/json' }

export async function getPolicies(slug: string): Promise<PoliciesView> {
  if (USE_MOCK) return MOCK_POLICIES
  return apiFetch<PoliciesView>(base(slug))
}

/** expectedRevision / expectedHash は保存の前提にした下書き（無ければ null）。違えば 409。 */
export async function saveDraft(
  slug: string,
  pkg: PolicyPackage,
  note: string,
  expectedRevision: number | null,
  expectedHash: string | null,
): Promise<PolicyRevision> {
  if (USE_MOCK) return { ...(MOCK_POLICIES.draft as PolicyRevision), package: pkg, note }
  return apiFetch<PolicyRevision>(`${base(slug)}/draft`, { method: 'PUT', headers: json, body: JSON.stringify({ package: pkg, note, expected_revision: expectedRevision, expected_hash: expectedHash }) })
}

export async function approveDraft(
  slug: string,
  expectedRevision: number,
  expectedHash: string,
): Promise<PolicyRevision> {
  if (USE_MOCK) return { ...(MOCK_POLICIES.draft as PolicyRevision), status: 'active' }
  return apiFetch<PolicyRevision>(`${base(slug)}/draft/approve`, {
    method: 'POST', headers: json, body: JSON.stringify({ expected_revision: expectedRevision, expected_hash: expectedHash }),
  })
}

export async function listRevisions(slug: string): Promise<RevisionSummary[]> {
  if (USE_MOCK) return MOCK_REVISIONS
  return apiFetch<RevisionSummary[]>(`${base(slug)}/revisions`)
}

export async function listPolicyAudit(slug: string): Promise<AuditEntry[]> {
  if (USE_MOCK) return MOCK_POLICY_AUDIT
  return apiFetch<AuditEntry[]>(`${base(slug)}/audit`)
}
