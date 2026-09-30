/** プロジェクト画面（S45）の型。src/speclino/domain/project_overview.py に対応させる。 */

import type { Verdict } from './finding'
import type { ExecState, GateStatus } from './run'

export type ApprovalState = 'approved' | 'unapproved' | 'stale'
export type PolicyOrigin = 'team' | 'project'
export type PolicyLevel = 'mandatory' | 'advisory'
export type VerdictCounts = Record<Verdict, number>

export interface ProjectRun {
  id: string
  pr_number: number | null
  head_sha: string
  exec_state: ExecState
  gate_status: GateStatus | null
  created_at: string | null
}

export interface ProjectSummary {
  id: string
  repo_full_name: string
  default_branch: string
  latest_run: ProjectRun | null
  counts: VerdictCounts | null
  unanalyzed: number | null
}

export interface RequirementView {
  id: string
  behavior_id: string
  title: string | null
  spec_path: string | null
  line_start: number | null
  line_end: number | null
  approval: ApprovalState
  tests_declared: number
  verdict: Verdict | null
  finding_ids: string[]
  removed: boolean
}

export interface PolicyView {
  rule_id: string
  origin: PolicyOrigin
  title: string | null
  level: PolicyLevel | null
  has_verifier: boolean | null
  status: Verdict | null
  counts: VerdictCounts
}

export interface ProjectOverview {
  status: 'ok' | 'not_checked' | 'unavailable'
  project: ProjectSummary
  run: ProjectRun | null
  counts: VerdictCounts | null
  unanalyzed: number
  unextracted: string[]
  unapproved: number
  policy_team: string | null
  policy_revision: number | null
  requirements: RequirementView[]
  policies: PolicyView[]
  runs: ProjectRun[]
}

export type RequirementFilter = 'all' | 'violated' | 'unknown' | 'review' | 'unapproved'

export type NextActionKind = 'violated' | 'stale' | 'review' | 'unknown' | 'unapproved'

export interface NextAction {
  kind: NextActionKind
  requirementId: string
  title: string | null
  hint: string
}
