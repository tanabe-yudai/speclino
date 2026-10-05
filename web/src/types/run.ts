/**
 * ReviewRun（1回のレビュー実行）の型。src/speclino/domain/run.py と
 * src/speclino/api/routes/review_runs.py の `_run_view` に対応させる。
 */

import type { Attestation } from './attestation'
import type { Finding } from './finding'

export type ExecState =
  | 'queued'
  | 'running'
  | 'awaiting_answer'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'superseded'

export type GateStatus = 'pass' | 'pass_with_exceptions' | 'fail'

export interface AnalysisLimits {
  depth: number
  max_paths: number
  solver_timeout_ms: number
}

export interface RunInputs {
  repository: string | null
  head_commit: string
  base_commit: string | null
  spec_content_hash: string | null
  spec_ir_hash: string | null
  bindings_hash: string | null
  domain_hash: string | null
  contracts_hash: string | null
  policy_hash: string | null
  code_ir_hash: string | null
}

export interface RunVersions {
  speclino: string
  analyzer: string
  engine: string
  solver: string
  settings_hash: string | null
}

/** 意味差分（PoC 12.2）の件数。src/speclino/domain/semantic_diff.py の SemanticDiffCounts。 */
export interface SemanticDiffCounts {
  new_violation: number
  existing_violation: number
  resolved_violation: number
  became_undecidable: number
  became_decidable: number
  added: number
  removed: number
  unchanged: number
  changed: number
}

/** review_run_service が manifest に同梱する意味差分の要約。 */
export interface SemanticDiffSummary {
  base_available: boolean
  base_error: string | null
  counts: SemanticDiffCounts
}

export interface RunManifest {
  schema_version: string
  run_id: string
  created_at: string
  inputs: RunInputs
  versions: RunVersions
  limits: AnalysisLimits
  fail_on: string[]
  mode: 'advisory' | 'blocking'
  semantic_diff?: SemanticDiffSummary | null
  /** 適用したチーム規約（S42/S43）。チーム規約の無い run は null */
  policy_team?: string | null
  policy_revision?: number | null
  /** このゲートで効いた人の確定（S70） */
  attestations?: Attestation[]
}

/** `GET /api/review-runs` の1件（findings を含まない）。 */
export interface ReviewRunSummary {
  id: string
  repo_full_name: string | null
  pr_number: number | null
  head_sha: string
  base_sha: string | null
  trigger: string
  exec_state: ExecState
  gate_status: GateStatus | null
  check_run_id: number | null
  artifacts_uri: string | null
  manifest: RunManifest | null
  budget: Record<string, unknown> | null
  created_at: string | null
  finished_at: string | null
}

/** `GET /api/review-runs/{id}` は findings を追加で持つ。 */
export interface ReviewRunDetail extends ReviewRunSummary {
  findings: Finding[]
  /** run のプロジェクトのワークスペース（S70: 確定の宛先）。未登録なら null */
  workspace?: string | null
  /** ゲートを止めない REVIEW の ID（gate の cleared: LLM 支援の指摘なし・人の確定）。件数と理由の正本 */
  cleared_ids?: string[]
}

/** `POST /api/review-runs/{id}/rerun`（デモ再実行、S39）の応答。 */
export interface RerunResponse {
  run_id: string
  used_today: number
  limit: number
  remaining: number
  cost_ok: boolean
  cost_note: string
}
