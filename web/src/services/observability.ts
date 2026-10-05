/** 可観測性の画面の表示用の整形（数え直さない。集計はサーバの WorkspaceHealth だけ）。 */

import type { VerdictTone } from '@/types/components'
import type { FetchHealth, Ratio, RuleTier, UnknownBreakdown, UnknownReason } from '@/types/observability'

export const UNMEASURED = '未計測'

export const UNKNOWN_REASON_LABELS: Record<UnknownReason, string> = {
  unbound: '対応なし',
  inexact_path: '不完全な経路',
  analysis_limit: '解析の上限',
  missing_finding: '判定の欠落',
  lock: 'lock',
  other: 'その他',
}

export const RULE_TIER_LABELS: Record<RuleTier, string> = {
  deterministic: '決定的',
  llm_assisted: 'LLM 支援',
  unverified: '検査器なし',
}

export const CHANNEL_LABELS: Record<string, string> = { sync: 'sync', mcp: 'MCP' }

export function formatRatio(ratio: Ratio | null): string {
  if (!ratio) return UNMEASURED
  return `${Math.round(ratio.rate * 100)}%（${ratio.numerator}/${ratio.denominator}）`
}

export function lagLabel(lag: number | null): string {
  if (lag === null) return UNMEASURED
  return lag === 0 ? '最新' : `${lag} 世代遅れ`
}

export function lagTone(lag: number | null): VerdictTone {
  if (lag === null) return 'unk'
  return lag === 0 ? 'ok' : 'rev'
}

export function unknownEntries(b: UnknownBreakdown): { reason: UnknownReason; label: string; count: number }[] {
  return (Object.keys(UNKNOWN_REASON_LABELS) as UnknownReason[])
    .map((reason) => ({ reason, label: UNKNOWN_REASON_LABELS[reason], count: b[reason] }))
    .filter((e) => e.count > 0)
    .sort((a, b) => b.count - a.count)
}

/** 取得の遅れの表示。失効済みのトークンは遅れではなく「失効済み」。 */
export function fetchLag(f: FetchHealth): { label: string; tone: VerdictTone } {
  if (f.revoked) return { label: '失効済み', tone: 'exempt' }
  return { label: lagLabel(f.behind_by), tone: lagTone(f.behind_by) }
}

export function fetchLabel(f: FetchHealth): string {
  return f.token_name ?? f.member_email ?? f.uid ?? '-'
}

export function percent(rate: number | null): string {
  return rate === null ? UNMEASURED : `${Math.round(rate * 100)}%`
}
