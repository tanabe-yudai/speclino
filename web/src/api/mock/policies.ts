/** チーム規約（S43）のモック。架空の example-org だけを使う。 */

import type { AuditEntry, PoliciesView, PolicyPackage, PolicyRule, RevisionSummary } from '../../types/policy'

const rule = (over: Partial<PolicyRule> & Pick<PolicyRule, 'id' | 'title'>): PolicyRule => ({
  version: '1.0.0',
  level: 'advisory',
  text: '本文',
  applies_to: { paths: ['specs/**'] },
  verifier: null,
  on_missing_input: 'unknown',
  exceptions_allowed: true,
  ...over,
})

const ACTIVE: PolicyPackage = {
  id: 'team',
  version: '1',
  status: 'active',
  owner: 'example-org',
  rules: [
    rule({ id: 'SPEC-001', title: '仕様に必須の節がある', level: 'mandatory', verifier: { id: 'spec.required_sections', version: '1.0.0' } }),
    rule({ id: 'TEST-001', title: '要求にテストを宣言する', verifier: { id: 'spec_tests', version: '1.0.0' } }),
    rule({ id: 'DOC-001', title: '設計判断を記録する', applies_to: { paths: ['docs/**'] } }),
  ],
}

const DRAFT: PolicyPackage = {
  ...ACTIVE,
  rules: ACTIVE.rules.map((r) => (r.id === 'TEST-001' ? { ...r, level: 'mandatory' } : r)),
}

export const MOCK_POLICIES: PoliciesView = {
  active: { team: 'example-org', revision: 3, status: 'active', package: ACTIVE, author: 'owner-uid', approver: 'owner-uid', note: '初期化' },
  draft: { team: 'example-org', revision: 4, status: 'draft', package: DRAFT, author: 'member-uid', approver: null, note: 'TEST-001 を変更' },
  draft_hash: 'sha256:mock',
  can_approve: true,
  verifiers: {
    'makefile.required_targets': '1.0.0',
    'spec.required_sections': '1.0.0',
    spec_tests: '1.0.0',
    'ci.required_conditions': '1.0.0',
    'api.error_format': '1.0.0',
  },
  results: {
    'SPEC-001': { status: 'VERIFIED_IN_SCOPE', projects: 2, failing_projects: 0 },
    'TEST-001': { status: 'VIOLATED', projects: 2, failing_projects: 1 },
  },
}

export const MOCK_REVISIONS: RevisionSummary[] = [
  { revision: 4, status: 'draft', author: 'member-uid', approver: null, note: 'TEST-001 を変更', rule_count: 3, created_at: '2026-10-01T03:00:00Z', approved_at: null },
  { revision: 3, status: 'active', author: 'owner-uid', approver: 'owner-uid', note: '初期化', rule_count: 3, created_at: '2026-09-30T03:00:00Z', approved_at: '2026-09-30T03:05:00Z' },
]

export const MOCK_POLICY_AUDIT: AuditEntry[] = [
  { ts: '2026-10-01T03:00:00Z', actor: 'member-uid', action: 'policy.draft', target: 'example-org@4', decision: 'created', detail: {} },
  { ts: '2026-09-30T03:06:00Z', actor: 'member-uid', action: 'policy.approve', target: 'example-org@3', decision: 'denied', detail: { reason: 'not_an_approver' } },
  { ts: '2026-09-30T03:05:00Z', actor: 'owner-uid', action: 'policy.approve', target: 'example-org@3', decision: 'approved', detail: { self_approved: true } },
]
