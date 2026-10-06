/**
 * チームの知識レジストリ（S62–S68）の型。src/speclino/domain/knowledge.py・knowledge_view.py・
 * uml.py・spec_preview.py に対応させる。S67・S71 もここを使う（持ち主は S66）。
 */

export type ArtifactKind = 'skill' | 'instructions' | 'spec'
export type ArtifactStatus = 'draft' | 'active' | 'superseded'
export type InstructionsTarget = 'agents_md' | 'claude_md'
export type RuleLevel = 'mandatory' | 'advisory'

/** `workspace: true` か `projects`（project ID の正規形）のどちらか一方。spec は projects だけ。 */
export interface ArtifactScope {
  workspace: boolean
  projects: string[]
}

export interface SkillContent {
  kind: 'skill'
  skill_md: string
  /** 相対パス → 本文（テキストのみ） */
  files: Record<string, string>
  scope: ArtifactScope
}

export interface InstructionsContent {
  kind: 'instructions'
  target: InstructionsTarget
  body: string
  scope: ArtifactScope
}

/** 旧形式の入口の JSON（domain.yaml 相当・承認済みの Spec IR）。画面では中身を解釈しない。 */
export type DomainSpecJson = Record<string, unknown>
export type SpecIrJson = Record<string, unknown>

export interface SpecContent {
  kind: 'spec'
  markdown: string
  domain: DomainSpecJson | null
  legacy_ir: SpecIrJson[]
  scope: ArtifactScope
  /** 承認時にサーバが固定する。下書きでは常に null（送ると 422） */
  compiled: CompiledSpec | null
}

export type ArtifactContent = SkillContent | InstructionsContent | SpecContent

export interface ArtifactRevision {
  artifact_id: string
  workspace: string
  kind: ArtifactKind
  name: string
  revision: number
  status: ArtifactStatus
  content: ArtifactContent
  content_hash: string
  author: string
  approver: string | null
  self_approved: boolean
  note: string
  created_at: string
  approved_at: string | null
}

export interface ArtifactSummary {
  artifact_id: string
  workspace: string
  kind: ArtifactKind
  name: string
  active_revision: number | null
  draft_revision: number | null
  created_at: string
}

export interface BundleMember {
  artifact_id: string
  kind: ArtifactKind
  name: string
  revision: number
  content_hash: string
}

export interface Bundle {
  workspace: string
  generation: number
  policy_revision: number | null
  policy_hash: string | null
  members: BundleMember[]
  scope_hash: string
  /** 署名した鍵の ID（#114、鍵の更新）。古い bundle には無い */
  key_id?: string | null
  signature: string
  created_at: string
}

export interface ApproveResult {
  revision: ArtifactRevision
  bundle: Bundle
}

// ---------------------------------------------------------------- 仕様の変換（S63）

export interface SourceSpan {
  diagram_id: string
  line: number
  column: number
}

export interface SpecDiagnostic {
  code: string
  severity: 'error' | 'warning'
  message: string
  span: SourceSpan | null
}

export interface RequirementRow {
  id: string
  level: RuleLevel
  text: string
  /** `図ID#要素ID` か `ir:要求ID`。空なら未形式化 */
  formalized_by: string[]
}

/** 画面が使う欄だけ（ほかの欄は IR の差分の表示で JSON のまま扱う）。 */
export interface CompiledSpec {
  parser_version: string
  source_hash: string
  ir_hash: string
  requirements: RequirementRow[]
  [key: string]: unknown
}

export interface ParseResult {
  compiled: CompiledSpec | null
  diagnostics: SpecDiagnostic[]
}

export interface SpecPreview {
  parse_result: ParseResult
  canonical: string | null
}

export interface SpecPreviewRequest {
  markdown: string
  domain?: DomainSpecJson | null
  legacy_ir?: SpecIrJson[]
}

// ---------------------------------------------------------------- 画面の区分

/** URL の種類の区切り（/w/:slug/knowledge/:kind）。 */
export type KnowledgeSection = 'specs' | 'skills' | 'instructions'

/** 承認のボタンを出す・押せるかの判断（services/knowledge.ts の approvalState）。 */
export interface ApprovalState {
  visible: boolean
  enabled: boolean
  /** 押せない理由（enabled=false のとき） */
  reason: string | null
  selfApproval: boolean
}
