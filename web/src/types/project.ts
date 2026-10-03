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

export type OverviewStatus = 'ok' | 'not_checked' | 'unavailable'

export interface ProjectSummary {
  id: string
  repo_full_name: string
  default_branch: string
  status: OverviewStatus
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
  /** 例外（exceptions.yaml）で免除された finding の数 */
  waived: number
  /** 違反がすべて例外で免除されている（免除中。違反でも適合でもない） */
  exempted: boolean
  waivers: WaiverView[]
}

/** 規約の違反を免除している例外（期限付き。期限までに直す前提）。 */
export interface WaiverView {
  exception_id: string
  expires_on: string
  reason: string
  /** 代替策（直し方） */
  alternative: string
}

export interface ProjectOverview {
  status: OverviewStatus
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

export type NextActionKind = 'violated' | 'stale' | 'review' | 'exempted' | 'unknown' | 'unapproved'

export interface NextAction {
  kind: NextActionKind
  requirementId: string
  title: string | null
  hint: string
}

export interface RequirementGroup {
  key: string
  specPath: string | null
  behaviorId: string
  items: RequirementView[]
}
