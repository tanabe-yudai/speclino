import type { VerdictCounts } from '@/types/project'

/** 適合の割合（0〜1）。未解析も分母に入れる。要求が 1 件も無ければ null（0% と言わない）。 */
export function healthRatio(counts: VerdictCounts | null, unanalyzed = 0): number | null {
  if (!counts) return null
  const total = counts.VERIFIED_IN_SCOPE + counts.VIOLATED + counts.UNKNOWN + counts.REVIEW + unanalyzed
  return total === 0 ? null : counts.VERIFIED_IN_SCOPE / total
}

/** 表示用の百分率。切り捨てる（違反が 1 件でもあれば 100 と出さない）。 */
export function healthPercent(ratio: number): number {
  return Math.floor(ratio * 100)
}

export type RingKind = 'ok' | 'rev' | 'unk' | 'bad' | 'none'

export interface RingSegment {
  kind: RingKind
  /** 円の中の開始位置と終了位置（%） */
  from: number
  to: number
}

/** 円グラフの区切り。適合 → 要確認 → 未検証 → 違反 → 未解析（none）の順で、0 件は出さない。 */
export function ringSegments(counts: VerdictCounts | null, unanalyzed: number): RingSegment[] {
  if (!counts) return []
  const parts: [RingKind, number][] = [
    ['ok', counts.VERIFIED_IN_SCOPE],
    ['rev', counts.REVIEW],
    ['unk', counts.UNKNOWN],
    ['bad', counts.VIOLATED],
    ['none', unanalyzed],
  ]
  const total = parts.reduce((n, [, v]) => n + v, 0)
  if (total === 0) return []
  const out: RingSegment[] = []
  let at = 0
  for (const [kind, v] of parts) {
    if (v === 0) continue
    const to = at + (v / total) * 100
    out.push({ kind, from: Math.round(at * 100) / 100, to: Math.round(to * 100) / 100 })
    at = to
  }
  return out
}

