import type { ProjectOverview, ProjectSummary } from '../../types/project'

// 既存のモック（fixtures.ts）の失敗した run の ID。要求の行を開くとその findings（反例つき）が出る
const RUN = {
  id: '9871e2ff-4602-4382-b512-0bacd3006ae2', pr_number: 12, head_sha: 'a1b2c3d4e5f6', exec_state: 'completed' as const,
  gate_status: 'fail' as const, created_at: '2026-09-30T01:00:00Z',
}

export const MOCK_PROJECTS: ProjectSummary[] = [
  {
    id: '00000000-0000-4000-8000-000000000001', repo_full_name: 'example-org/orders', default_branch: 'main', status: 'ok',
    latest_run: RUN, counts: { VERIFIED_IN_SCOPE: 1, VIOLATED: 1, UNKNOWN: 1, REVIEW: 1 }, unanalyzed: 0,
  },
  {
    id: '00000000-0000-4000-8000-000000000002', repo_full_name: 'example-org/a-repository-with-a-very-long-name-for-layout',
    default_branch: 'main', status: 'not_checked', latest_run: null, counts: null, unanalyzed: null,
  },
]

export const MOCK_OVERVIEW: ProjectOverview = {
  status: 'ok',
  project: MOCK_PROJECTS[0],
  run: RUN,
  counts: { VERIFIED_IN_SCOPE: 1, VIOLATED: 1, UNKNOWN: 1, REVIEW: 1 },
  unanalyzed: 0,
  unextracted: [],
  unapproved: 1,
  policy_team: 'example-team',
  policy_revision: 3,
  requirements: [
    { id: 'R-CANCEL-01', behavior_id: 'cancel_order', title: '未発送ならキャンセルできる', spec_path: 'specs/cancel_order.md', line_start: 5, line_end: 8, approval: 'approved', tests_declared: 1, verdict: 'VERIFIED_IN_SCOPE', finding_ids: ['f1'], removed: false },
    { id: 'R-CANCEL-04', behavior_id: 'cancel_order', title: '発送済みは在庫を変えない', spec_path: 'specs/cancel_order.md', line_start: 20, line_end: 24, approval: 'approved', tests_declared: 2, verdict: 'VIOLATED', finding_ids: ['f4'], removed: false },
    { id: 'R-CANCEL-05', behavior_id: 'cancel_order', title: '返金は 1 回だけ', spec_path: 'specs/cancel_order.md', line_start: 26, line_end: 29, approval: 'approved', tests_declared: 0, verdict: 'UNKNOWN', finding_ids: ['f5'], removed: false },
    { id: 'R-CANCEL-06', behavior_id: 'cancel_order', title: '監査ログを残す', spec_path: null, line_start: null, line_end: null, approval: 'unapproved', tests_declared: 1, verdict: 'REVIEW', finding_ids: ['f6'], removed: false },
  ],
  policies: [
    { rule_id: 'SPEC-001', origin: 'team', title: '仕様に要求 ID を付ける', level: 'mandatory', has_verifier: true, status: 'VERIFIED_IN_SCOPE', counts: { VERIFIED_IN_SCOPE: 3, VIOLATED: 0, UNKNOWN: 0, REVIEW: 0 } },
    { rule_id: 'DOC-001', origin: 'team', title: '設計判断を記録する', level: 'advisory', has_verifier: false, status: null, counts: { VERIFIED_IN_SCOPE: 0, VIOLATED: 0, UNKNOWN: 0, REVIEW: 0 } },
  ],
  runs: [RUN, { ...RUN, id: 'b2c3d4e5-6789-4abc-9def-0123456789ab', pr_number: 11, gate_status: 'pass', created_at: '2026-09-29T01:00:00Z' }],
}
