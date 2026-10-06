import type { Verdict } from './finding'

export type PolicyLevel = 'mandatory' | 'advisory'
export type OnMissingInput = 'violation' | 'unknown' | 'skipped'

export interface PolicyRule {
  id: string
  version: string
  level: PolicyLevel
  title: string
  text: string
  applies_to: { paths: string[]; languages?: string[] }
  source?: { doc: string; anchor?: string | null } | null
  verifier: { id: string; version: string; params?: Record<string, unknown> } | null
  on_missing_input: OnMissingInput
  exceptions_allowed: boolean
}

export interface PolicyPackage {
  schema_version?: string
  id: string
  version: string
  status: 'active' | 'draft'
  owner: string
  rules: PolicyRule[]
}

export interface PolicyRevision {
  team: string
  revision: number
  status: 'draft' | 'active' | 'superseded'
  package: PolicyPackage
  author: string
  approver: string | null
  note: string
}

export interface RuleResult {
  status: Verdict | null
  projects: number
  failing_projects: number
}

export interface PoliciesView {
  active: PolicyRevision | null
  draft: PolicyRevision | null
  draft_hash: string | null
  can_approve: boolean
  verifiers: Record<string, string>
  results: Record<string, RuleResult>
  /** 下書きの LLM 支援の規約の送り先の範囲の警告（#114。承認は拒否しない） */
  draft_warnings?: string[]
}

export interface RevisionSummary {
  revision: number
  status: PolicyRevision['status']
  author: string
  approver: string | null
  note: string
  rule_count: number
  created_at: string
  approved_at: string | null
}

export interface AuditEntry {
  ts: string
  actor: string
  action: string
  target: string
  decision: string
  detail: Record<string, unknown>
}

/** 有効な改訂から下書きへの変化（1 ルール 1 件）。fields は変わった項目名。 */
export interface RuleChange {
  ruleId: string
  kind: 'added' | 'removed' | 'changed'
  fields: string[]
}
