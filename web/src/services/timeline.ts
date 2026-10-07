/** 検査のタイムラインのイベントを、人が読める要約と「項目名: 値」に直す（API の応答をそのまま見せない）。 */

import { EXEC_STATE_LABELS, GATE_STATUS_LABELS, TRIGGER_LABELS } from './labels'
import type { ExecState, GateStatus } from '@/types/run'

export interface EventField {
  label: string
  value: string
}

export interface EventDescription {
  /** 1 行の要約。作れないイベントは null（項目だけを出す） */
  summary: string | null
  fields: EventField[]
}

const KEY_LABELS: Record<string, string> = {
  exec_state: '状態',
  trigger: 'きっかけ',
  superseded: '置き換えた検査',
  gate: 'ゲート',
  error: 'エラー',
  publish: 'GitHub への反映',
  reason: '理由',
  rule_id: '要求',
  behavior_id: '振る舞い',
  unresolved: '未解決の要求',
  query: '検索語',
  hits: '見つかった数',
  runner: '実行環境',
  exit_code: '終了コード',
  timed_out: '時間切れ',
  statuses: 'テストの結果',
  symbol: 'シンボル',
  contracts: '契約',
  paths: '経路の数',
  results: '判定',
  clamped: '上限で打ち切り',
  steps: '手順',
  n: '回数',
}

const PUBLISH_LABELS: Record<string, string> = {
  publish: '反映した',
  skip: '反映しない',
  superseded: '反映しない（新しい検査がある）',
  not_latest: '反映しない（最新の検査ではない）',
}

function formatValue(key: string, value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null
  if (key === 'exec_state') return EXEC_STATE_LABELS[value as ExecState] ?? String(value)
  if (key === 'gate') return GATE_STATUS_LABELS[value as GateStatus] ?? String(value)
  if (key === 'publish') return PUBLISH_LABELS[String(value)] ?? String(value)
  if (key === 'trigger') return TRIGGER_LABELS[String(value)] ?? 'その他'
  if (key === 'superseded' && value === 0) return null
  if (typeof value === 'boolean') return value ? 'はい' : 'いいえ'
  if (Array.isArray(value)) {
    if (value.length === 0) return null
    return value.map((v) => (Array.isArray(v) ? v.join(': ') : typeof v === 'object' ? JSON.stringify(v) : String(v))).join(', ')
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
    if (entries.length === 0) return null
    return entries.map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`).join(' / ')
  }
  return String(value)
}

function stateSummary(payload: Record<string, unknown>): string | null {
  const state = payload.exec_state
  if (typeof state !== 'string') return null
  const head = EXEC_STATE_LABELS[state as ExecState] ?? state
  const trigger = formatValue('trigger', payload.trigger)
  if (trigger) return `${head}（きっかけ: ${trigger}）`
  const parts = [head]
  const gate = formatValue('gate', payload.gate)
  if (gate) parts.push(`ゲート: ${gate}`)
  if (payload.publish === 'publish') parts.push('GitHub に反映')
  else if (payload.publish) parts.push('GitHub に反映しない')
  return parts.join(' · ')
}

export function describeEvent(type: string, payload: Record<string, unknown>): EventDescription {
  const fields: EventField[] = []
  for (const [key, value] of Object.entries(payload)) {
    const formatted = formatValue(key, value)
    if (formatted !== null) fields.push({ label: KEY_LABELS[key] ?? key, value: formatted })
  }
  return { summary: type === 'state' ? stateSummary(payload) : null, fields }
}
