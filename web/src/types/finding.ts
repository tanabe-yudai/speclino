/**
 * Finding（判定結果）の型。バックエンドの src/speclino/domain/finding.py と対応させる。
 * additionalProperties: false の IR 契約に合わせ、API が返すフィールドのみを持つ。
 */

export type Verdict = 'VERIFIED_IN_SCOPE' | 'VIOLATED' | 'UNKNOWN' | 'REVIEW'

export type Category =
  | 'missing_effect'
  | 'forbidden_effect'
  | 'constraint_mismatch'
  | 'state_mismatch'
  | 'outcome_mismatch'
  | 'argument_mismatch'
  | 'cardinality_mismatch'
  | 'undocumented_behavior'
  | 'unsupported_analysis'
  | 'ambiguous_spec'
  | 'stale_approval'
  | 'vacuous_applicability'
  | 'potential_violation'
  | 'solver_limit'
  | 'conformant'

export type Severity = 'high' | 'medium' | 'low' | 'info'

export type ClaimScope =
  | 'memory_state'
  | 'command_invocation'
  | 'service_outcome'
  | 'http_response'
  | 'persisted_state'

export type BaseHeadStatus = 'new' | 'existing' | 'resolved' | 'unchanged'

export type TestObservation = 'OBSERVED' | 'NOT_OBSERVED' | 'CONTRADICTED'

/** LLM 支援の規約の区分（S70）。verdict は常に REVIEW。 */
export type AssessmentOutcome = 'issue' | 'no_issue' | 'split'

/** 仕様またはコード上の位置。requirement_id か symbol のどちらかを持つ。 */
export interface SourceRef {
  snapshot_id: string
  requirement_id: string | null
  symbol: string | null
  path: string | null
  line_start: number | null
  line_end: number | null
}

export interface Counterexample {
  kind: 'symbolic' | 'observed'
  path_id: string | null
  assignments: Record<string, boolean | number | string>
  description: string
}

export interface Finding {
  id: string
  requirement_id: string
  behavior_id: string
  verdict: Verdict
  category: Category
  severity: Severity
  message: string
  expected: string | null
  actual: string | null
  counterexample: Counterexample | null
  evidence_refs: string[]
  assumptions: string[]
  claim_scope: ClaimScope
  analysis_scope: string | null
  code_refs: SourceRef[]
  spec_refs: SourceRef[]
  base_head_status: BaseHeadStatus | null
  test_observation: TestObservation | null
  next_steps: string[]
  /** LLM 支援の規約だけ（S70）。それ以外は null か欠落 */
  assessment_outcome?: AssessmentOutcome | null
}
