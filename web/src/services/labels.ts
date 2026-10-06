/** 判定・状態を日本語ラベルへ変換する定数群。コンポーネントには埋め込まない。 */

import type {
  AssessmentOutcome,
  BaseHeadStatus,
  Category,
  Severity,
  TestObservation,
  Verdict,
} from '../types/finding'
import type { OnMissingInput, PolicyRevision } from '../types/policy'
import type { ApprovalState, NextActionKind, PolicyLevel, RequirementFilter } from '../types/project'
import type { ExecState, GateStatus } from '../types/run'
import type { VerdictTone } from '../types/components'
import type { WorkspaceRole } from '../types/me'

export const VERDICT_LABELS: Record<Verdict, string> = {
  VERIFIED_IN_SCOPE: '適合',
  VIOLATED: '違反',
  UNKNOWN: '未検証',
  REVIEW: '要確認',
}

/** ゲートを止めない REVIEW（人が問題なしと確定した指摘）の区分。verdict は REVIEW のまま */
export const CLEARED_LABEL = '確定済み・情報'

/** VIOLATED → REVIEW → UNKNOWN → VERIFIED_IN_SCOPE の表示順。 */
export const VERDICT_ORDER: Record<Verdict, number> = {
  VIOLATED: 0,
  REVIEW: 1,
  UNKNOWN: 2,
  VERIFIED_IN_SCOPE: 3,
}

export const CATEGORY_LABELS: Record<Category, string> = {
  missing_effect: '効果の欠落',
  forbidden_effect: '禁止された効果',
  constraint_mismatch: '制約の不一致',
  state_mismatch: '状態の不一致',
  outcome_mismatch: '結果の不一致',
  argument_mismatch: '引数の不一致',
  cardinality_mismatch: '回数の不一致',
  undocumented_behavior: '未記載の挙動',
  unsupported_analysis: '解析対象外',
  ambiguous_spec: '仕様の曖昧さ',
  stale_approval: '承認の失効',
  vacuous_applicability: '適用条件の充足不能',
  potential_violation: '違反の疑い',
  solver_limit: '検査上限到達',
  conformant: '適合',
}

export const SEVERITY_LABELS: Record<Severity, string> = {
  high: '高',
  medium: '中',
  low: '低',
  info: '情報',
}

export const TEST_OBSERVATION_LABELS: Record<TestObservation, string> = {
  OBSERVED: '観測済み',
  NOT_OBSERVED: '未観測',
  CONTRADICTED: '矛盾',
}

export const EXEC_STATE_LABELS: Record<ExecState, string> = {
  queued: '待機中',
  running: '実行中',
  awaiting_answer: '回答待ち',
  completed: '完了',
  failed: '実行失敗',
  cancelled: 'キャンセル',
  superseded: '無効化（再実行あり）',
}

export const GATE_STATUS_LABELS: Record<GateStatus, string> = {
  pass: '通過',
  pass_with_exceptions: '通過（免除中あり）',
  fail: '失敗',
}

interface EventMeta {
  label: string
  /** Timeline のインジケータの色分け用カテゴリ。 */
  group: 'state' | 'agent' | 'tool' | 'audit' | 'other'
}

const EVENT_TYPE_META: Record<string, EventMeta> = {
  state: { label: '状態遷移', group: 'state' },
  'agent.start': { label: 'エージェント開始', group: 'agent' },
  'agent.end': { label: 'エージェント終了', group: 'agent' },
  plan: { label: '計画', group: 'agent' },
  'tool.policy_check': { label: 'ツール実行: 権限確認', group: 'tool' },
  'tool.analyze_focus': { label: 'ツール実行: 重点再解析', group: 'tool' },
  'tool.repo_search': { label: 'ツール実行: リポジトリ検索', group: 'tool' },
  audit: { label: '監査記録', group: 'audit' },
  spec_bot: { label: '仕様 PR bot', group: 'other' },
}

export function eventMeta(type: string): EventMeta {
  return EVENT_TYPE_META[type] ?? { label: type, group: 'other' }
}

export const BASE_HEAD_STATUS_LABELS: Record<BaseHeadStatus, string> = {
  new: '新規',
  existing: '既存',
  resolved: '解消',
  unchanged: '変化なし',
}

/** base との比較の色（変化なしは出さない）。 */
export const BASE_HEAD_STATUS_TONE: Record<Exclude<BaseHeadStatus, 'unchanged'>, VerdictTone> = {
  new: 'bad',
  existing: 'rev',
  resolved: 'ok',
}

export const WORKSPACE_ROLE_LABELS: Record<WorkspaceRole, string> = {
  owner: 'オーナー',
  approver: '承認者',
  member: 'メンバー',
}

/** ゲートの結果を判定の色に対応させる（通過=適合の色、通過（免除中あり）=免除中の色、失敗=違反の色）。 */
export function gateTone(status: GateStatus): VerdictTone {
  return status === 'pass' ? 'ok' : status === 'pass_with_exceptions' ? 'exempt' : 'bad'
}

export const APPROVAL_LABELS: Record<ApprovalState, string> = {
  approved: '承認済み',
  unapproved: '未承認',
  stale: '承認切れ',
}

export const POLICY_LEVEL_LABELS: Record<PolicyLevel, string> = { mandatory: '必須', advisory: '助言' }

export const REQUIREMENT_FILTER_LABELS: Record<RequirementFilter, string> = {
  all: 'すべて',
  violated: '違反',
  unknown: '未検証',
  review: '要確認',
  unapproved: '未承認',
}

/** 「次にやること」の種類の名前と色（プロジェクト画面とランディングページの見本で共通）。 */
export const NEXT_ACTION_KIND: Record<NextActionKind, { label: string; tone: VerdictTone }> = {
  violated: { label: '違反', tone: 'bad' },
  stale: { label: '承認切れ', tone: 'rev' },
  review: { label: '要確認', tone: 'rev' },
  exempted: { label: '免除中', tone: 'rev' },
  unknown: { label: '未検証', tone: 'unk' },
  unapproved: { label: '未承認', tone: 'rev' },
}

export const ON_MISSING_INPUT_LABELS: Record<OnMissingInput, string> = {
  violation: '対象が無ければ違反',
  unknown: '対象が無ければ未検証',
  skipped: '対象が無ければ評価しない',
}

export const REVISION_STATUS_LABELS: Record<PolicyRevision['status'], string> = {
  active: '有効',
  draft: '下書き',
  superseded: '置き換え済み',
}

/** 規約の差分で変わった項目の名前。 */
export const RULE_FIELD_LABELS: Record<string, string> = {
  level: 'レベル',
  title: '題',
  text: '本文',
  applies_to: '適用範囲',
  verifier: '検査器',
  on_missing_input: '対象が無いとき',
  exceptions_allowed: '例外の可否',
  version: '版',
}

/** 監査の「何を」（action と decision の組）。 */
export function auditLabel(action: string, decision: string): string {
  if (action === 'policy.draft') return decision === 'updated' ? '下書きを更新' : '下書きを作成'
  if (action === 'policy.approve') return decision === 'denied' ? '承認を拒否（承認者ではない）' : '承認して有効にした'
  return `${action} ${decision}`
}

/**
 * LLM 支援の規約の区分（S70）。指摘なしは機械検証ではないことを明記する独自の区分。
 * 指摘なしも人の確定まではゲートを止める（#114）ので「人の確定が必要」と出す。
 */
export const ASSESSMENT_OUTCOME_LABELS: Record<AssessmentOutcome, string> = {
  issue: 'LLM の指摘あり（人の確定待ち）',
  split: 'LLM の評価が割れた（人の確定待ち）',
  no_issue: 'LLM で確認、指摘なし（機械検証ではない）— 人の確定が必要',
}

/** 確定済み（ゲートの cleared）の LLM 支援の区分。確定待ちの文言を外す。 */
export const ASSESSMENT_OUTCOME_CLEARED_LABELS: Record<AssessmentOutcome, string> = {
  issue: 'LLM の指摘あり（人が問題なしと確定）',
  split: 'LLM の評価が割れた（人が問題なしと確定）',
  no_issue: 'LLM で確認、指摘なし（機械検証ではない）— 人が確定',
}

export function assessmentOutcomeLabel(outcome: AssessmentOutcome, cleared: boolean): string {
  return (cleared ? ASSESSMENT_OUTCOME_CLEARED_LABELS : ASSESSMENT_OUTCOME_LABELS)[outcome]
}

export const ASSESSMENT_OUTCOME_TONE: Record<AssessmentOutcome, VerdictTone> = {
  issue: 'rev',
  split: 'rev',
  no_issue: 'rev',
}
