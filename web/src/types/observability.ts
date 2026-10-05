/** `/api/workspaces/:slug/observability`（S71）の型。src/speclino/domain/observability.py と対応させる。 */

import type { PolicyLevel } from './policy'
import type { GateStatus } from './run'

export type UnknownReason = 'unbound' | 'inexact_path' | 'analysis_limit' | 'missing_finding' | 'lock' | 'other'
export type RuleTier = 'deterministic' | 'llm_assisted' | 'unverified'

/** 分母が 0 のものは作られない（null = 未計測）。 */
export interface Ratio {
  numerator: number
  denominator: number
  rate: number
}

export type UnknownBreakdown = Record<UnknownReason, number>

export interface ObservedVerdictCounts {
  VERIFIED_IN_SCOPE: number
  VIOLATED: number
  UNKNOWN: number
  REVIEW: number
}

export interface RunSummary {
  run_id: string
  pr_number: number | null
  head_sha: string
  created_at: string
  gate_status: GateStatus | null
  counts: ObservedVerdictCounts
  bundle_generation: number | null
  generation_lag: number | null
}

export interface ProjectHealth {
  project_id: string
  repo_full_name: string
  formalization: Ratio | null
  binding: Ratio | null
  unknown_breakdown: UnknownBreakdown | null
  latest_run: RunSummary | null
}

export interface FetchHealth {
  channel: string
  token_id: string | null
  token_name: string | null
  /** 失効済みのトークン（遅れに数えない。behind_by は null） */
  revoked: boolean
  uid: string | null
  member_email: string | null
  project_id: string | null
  repo_full_name: string | null
  last_fetched_at: string | null
  last_generation: number | null
  behind_by: number | null
}

export interface TrendRun {
  run_id: string
  repo_full_name: string
  created_at: string
}

export interface RulePoint {
  violated: number
  review: number
  unknown: number
  verified: number
}

export interface RuleTrend {
  rule_id: string
  title: string | null
  level: PolicyLevel | null
  tier: RuleTier | null
  /** trend_runs と同じ順（古い順）。null はその run に結果が無い */
  points: (RulePoint | null)[]
}

export interface LlmStability {
  issue: number
  no_issue: number
  split: number
  cache_hit_rate: number | null
  attestations: number | null
}

export interface WorkspaceHealth {
  workspace: string
  generated_at: string
  current_generation: number | null
  fetch_window_days: number
  projects: ProjectHealth[]
  fetches: FetchHealth[]
  trend_runs: TrendRun[]
  rules: RuleTrend[]
  llm: LlmStability | null
}
