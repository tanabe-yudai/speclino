/** 判定・状態を日本語ラベルへ変換する定数群。コンポーネントには埋め込まない。 */

import type { Category, Severity, TestObservation, Verdict } from '../types/finding'
import type { ExecState, GateStatus } from '../types/run'
import type { BadgeTone } from '../types/components'

export const VERDICT_LABELS: Record<Verdict, string> = {
  VERIFIED_IN_SCOPE: '適合（確認範囲内）',
  VIOLATED: '違反',
  UNKNOWN: '不明',
  REVIEW: '要確認',
}

export const VERDICT_TONE: Record<Verdict, BadgeTone> = {
  VERIFIED_IN_SCOPE: 'success',
  VIOLATED: 'danger',
  REVIEW: 'warning',
  UNKNOWN: 'neutral',
}

export const GATE_TONE: Record<GateStatus, BadgeTone> = {
  pass: 'success',
  pass_with_exceptions: 'warning',
  fail: 'danger',
}

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
  pass_with_exceptions: '例外付き通過',
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
}

export function eventMeta(type: string): EventMeta {
  return EVENT_TYPE_META[type] ?? { label: type, group: 'other' }
}
