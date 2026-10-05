/** Finding の集計・並び替え。表示順は VIOLATED → REVIEW → UNKNOWN → VERIFIED_IN_SCOPE。 */

import type { Finding, Verdict } from '../types/finding'
import { VERDICT_ORDER } from './labels'

export function emptyVerdictCounts(): Record<Verdict, number> {
  return { VERIFIED_IN_SCOPE: 0, VIOLATED: 0, UNKNOWN: 0, REVIEW: 0 }
}

export function countByVerdict(findings: Finding[]): Record<Verdict, number> {
  const counts = emptyVerdictCounts()
  for (const finding of findings) counts[finding.verdict] += 1
  return counts
}

export function sortFindingsForDisplay(findings: Finding[]): Finding[] {
  return [...findings].sort((a, b) => {
    const byVerdict = VERDICT_ORDER[a.verdict] - VERDICT_ORDER[b.verdict]
    if (byVerdict !== 0) return byVerdict
    return a.requirement_id.localeCompare(b.requirement_id)
  })
}

export function groupFindingsByVerdict(findings: Finding[]): Record<Verdict, Finding[]> {
  const grouped: Record<Verdict, Finding[]> = {
    VIOLATED: [],
    REVIEW: [],
    UNKNOWN: [],
    VERIFIED_IN_SCOPE: [],
  }
  for (const finding of sortFindingsForDisplay(findings)) grouped[finding.verdict].push(finding)
  return grouped
}

/** 上部要約向け: analysis_scope と assumptions の重複を除いた集合（出現順を保つ）。 */
export function collectScopeAndAssumptions(findings: Finding[]): {
  scopes: string[]
  assumptions: string[]
} {
  const scopes = [...new Set(findings.map((f) => f.analysis_scope).filter((s): s is string => !!s))]
  const assumptions = [...new Set(findings.flatMap((f) => f.assumptions))]
  return { scopes, assumptions }
}

/** 未確認範囲: UNKNOWN / REVIEW の要求を「要求ID: メッセージ」の形でまとめる。 */
export function collectUnresolvedItems(findings: Finding[]): string[] {
  return sortFindingsForDisplay(findings)
    .filter((f) => f.verdict === 'UNKNOWN' || f.verdict === 'REVIEW')
    .map((f) => `${f.requirement_id}: ${f.message}`)
}

/**
 * ゲートの cleared（阻止しない REVIEW）とそれ以外に分ける。判定ごとの件数・失敗の理由・一覧は
 * すべてこの分け方から作る（バックエンドの gate が決めた集合だけを使い、画面で推測しない）。
 */
export function splitCleared(findings: Finding[], clearedIds: readonly string[] | undefined): { active: Finding[]; cleared: Finding[] } {
  const ids = new Set(clearedIds ?? [])
  const active: Finding[] = []
  const cleared: Finding[] = []
  for (const f of findings) (ids.has(f.id) && f.verdict === 'REVIEW' ? cleared : active).push(f)
  return { active, cleared }
}
